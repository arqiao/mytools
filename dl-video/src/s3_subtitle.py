"""Step3: MP3 转字幕 — 支持 Whisper / 讯飞 / 飞书 / 阿里云 / 豆包"""

import os
import sys

# CUDA DLL 路径：pip install nvidia-cublas-cu12 后 DLL 不在系统 PATH 中
_cuda_bin = os.path.join(
    sys.prefix, "Lib", "site-packages", "nvidia", "cublas", "bin"
)
if os.path.isdir(_cuda_bin) and _cuda_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _cuda_bin + os.pathsep + os.environ.get("PATH", "")

import argparse
import gc
import hashlib
import hmac
import json
import logging
import re
import subprocess
import time
import uuid
from base64 import b64encode
from pathlib import Path

import opencc
import requests
import yaml
from faster_whisper import WhisperModel

from model_downloader import ensure_model, get_model_path

# 繁体转简体转换器
_t2s = opencc.OpenCC("t2s")

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
OUTPUT_DIR = PROJECT_DIR / "output"
LOG_DIR = PROJECT_DIR / "log-err"
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "s3_subtitle.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')

# API 地址常量
FEISHU_BASE = "https://open.feishu.cn/open-apis"
XUNFEI_UPLOAD_URL = "https://raasr.xfyun.cn/v2/api/upload"
XUNFEI_GET_RESULT_URL = "https://raasr.xfyun.cn/v2/api/getResult"
ALIYUN_FILE_TRANS_URL = "https://filetrans.cn-shanghai.aliyuncs.com"
ALIYUN_API_VERSION = "2018-08-17"
VOLC_SERVICE = "sami"
VOLC_HOST = "open.volcengineapi.com"
VOLC_REGION = "cn-north-1"


# ============================================================
# 配置加载 & 工具函数
# ============================================================

def load_config():
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(CFG_DIR / "credentials.yaml", encoding="utf-8") as f:
        creds = yaml.safe_load(f)
    return config, creds


def load_subtitle_config() -> dict:
    """加载字幕相关配置（从 config.yaml 读取）"""
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_engine_suffix(engine_name: str) -> str:
    cfg = load_subtitle_config()
    suffix_map = cfg.get("engine_suffix", {})
    return suffix_map.get(engine_name, f"_{engine_name}")


def safe_filename(title: str) -> str:
    return ILLEGAL_CHARS.sub("_", title).strip()


def format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# ============================================================
# 音频预处理
# ============================================================

def _prepare_audio_for_whisper(audio_path: Path) -> Path:
    """将音频预处理为 16kHz 单声道 WAV，减少 Whisper 内存占用"""
    wav_path = audio_path.with_suffix(".16k.wav")
    if wav_path.exists():
        log.info(f"预处理音频已存在，跳过: {wav_path}")
        return wav_path

    try:
        import av
        import wave

        log.info(f"预处理音频为 16kHz WAV（使用 PyAV）...")
        container = av.open(str(audio_path))
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            for frame in container.decode(audio=0):
                resampled = resampler.resample(frame)
                for r in resampled:
                    wf.writeframes(r.to_ndarray().tobytes())

        container.close()
        log.info(f"预处理完成: {wav_path} ({wav_path.stat().st_size / 1024 / 1024:.1f} MB)")
        return wav_path

    except ImportError:
        cmd = [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ar", "16000", "-ac", "1",
            str(wav_path),
        ]
        log.info(f"预处理音频为 16kHz WAV（使用 ffmpeg）...")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 转换失败: {result.stderr.decode(errors='replace')}")
        log.info(f"预处理完成: {wav_path} ({wav_path.stat().st_size / 1024 / 1024:.1f} MB)")
        return wav_path


def _audio_to_pcm(audio_path: Path) -> Path:
    """将音频转为 PCM（16kHz, 16bit, 单声道）"""
    pcm_path = audio_path.with_suffix(".pcm")
    if pcm_path.exists():
        log.info(f"PCM 文件已存在，跳过转换: {pcm_path}")
        return pcm_path

    try:
        import av

        log.info(f"转换音频为 PCM（使用 PyAV）: {audio_path}")
        container = av.open(str(audio_path))
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

        with open(pcm_path, "wb") as f:
            for frame in container.decode(audio=0):
                resampled = resampler.resample(frame)
                for r in resampled:
                    f.write(r.to_ndarray().tobytes())

        container.close()

    except ImportError:
        cmd = [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ar", "16000", "-ac", "1", "-f", "s16le",
            str(pcm_path),
        ]
        log.info(f"转换音频为 PCM（使用 ffmpeg）: {audio_path}")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 转换失败: {result.stderr.decode(errors='replace')}")

    log.info(f"PCM 转换完成: {pcm_path} ({pcm_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return pcm_path


# ============================================================
# Whisper 转写
# ============================================================

def transcribe_whisper(audio_path: Path, model_size: str = "small",
                       force_cpu: bool = False) -> Path | None:
    """使用 Whisper 生成字幕（长音频自动分段处理）"""
    try:
        if not ensure_model(model_size):
            log.error(f"模型 {model_size} 不可用，请先运行: python src/model_downloader.py {model_size}")
            return None

        # 预处理：大文件先转为 16kHz WAV
        if audio_path.stat().st_size > 50 * 1024 * 1024:
            whisper_input = _prepare_audio_for_whisper(audio_path)
        else:
            whisper_input = audio_path

        # 自动检测 CUDA
        device, compute_type = "cpu", "int8"
        if not force_cpu:
            try:
                import ctranslate2
                cuda_types = ctranslate2.get_supported_compute_types("cuda")
                if cuda_types:
                    device = "cuda"
                    compute_type = "int8" if "int8" in cuda_types else "float16"
                    log.info(f"检测到 CUDA 可用，使用 {device}/{compute_type}")
            except Exception:
                pass
        else:
            log.info("强制使用 CPU 模式")

        log.info(f"加载 Whisper {model_size} 模型 ({device}/{compute_type})...")
        local_path = get_model_path(model_size)
        model_ref = local_path or model_size
        model = WhisperModel(model_ref, device=device, compute_type=compute_type)

        import numpy as np
        import wave

        # 读取 WAV 文件信息
        if str(whisper_input).endswith(".wav"):
            with wave.open(str(whisper_input), "rb") as wf:
                n_frames = wf.getnframes()
                sample_rate = wf.getframerate()
            total_sec = n_frames / sample_rate
        else:
            total_sec = 0

        segment_duration = 180  # 3 分钟
        overlap = 5

        suffix = get_engine_suffix(f"whisper_{model_size}")
        srt_path = audio_path.parent / f"{audio_path.stem}{suffix}.srt"
        srt_idx = 0

        if total_sec > 1800 and str(whisper_input).endswith(".wav"):
            log.info(f"长音频 ({total_sec / 60:.0f} 分钟)，分段处理...")
            n_segments = int((total_sec - overlap) / (segment_duration - overlap)) + 1

            with open(srt_path, "w", encoding="utf-8") as f:
                for seg_i in range(n_segments):
                    start_sec = seg_i * (segment_duration - overlap)
                    start_frame = int(start_sec * sample_rate)
                    end_frame = min(int((start_sec + segment_duration) * sample_rate), n_frames)
                    chunk_frames = end_frame - start_frame

                    log.info(f"  转写第 {seg_i + 1}/{n_segments} 段 "
                             f"({int(start_sec) // 60}:{int(start_sec) % 60:02d} - "
                             f"{int(start_sec + chunk_frames / sample_rate) // 60}:"
                             f"{int(start_sec + chunk_frames / sample_rate) % 60:02d})...")

                    with wave.open(str(whisper_input), "rb") as wf:
                        wf.setpos(start_frame)
                        raw = wf.readframes(chunk_frames)

                    chunk_array = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                    segments, info = model.transcribe(
                        chunk_array, language="zh", beam_size=1,
                        vad_filter=True,
                        vad_parameters=dict(min_silence_duration_ms=500),
                    )

                    for segment in segments:
                        if seg_i > 0 and segment.start < overlap:
                            continue
                        srt_idx += 1
                        abs_start = start_sec + segment.start
                        abs_end = start_sec + segment.end
                        f.write(f"{srt_idx}\n")
                        f.write(f"{format_srt_time(abs_start)} --> {format_srt_time(abs_end)}\n")
                        f.write(f"{_t2s.convert(segment.text.strip())}\n\n")

                    del chunk_array, raw, segments
                    gc.collect()
        else:
            log.info(f"开始转写: {whisper_input}")
            segments, info = model.transcribe(
                str(whisper_input), language="zh", beam_size=3,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )
            with open(srt_path, "w", encoding="utf-8") as f:
                for i, segment in enumerate(segments, 1):
                    f.write(f"{i}\n")
                    f.write(f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n")
                    f.write(f"{_t2s.convert(segment.text.strip())}\n\n")
                    srt_idx = i

        log.info(f"字幕生成成功: {srt_path} ({srt_idx} 条)")

        # 清理临时 16k WAV 文件
        if whisper_input != audio_path and whisper_input.suffix == ".wav":
            whisper_input.unlink()
            log.info(f"已删除临时文件: {whisper_input}")

        return srt_path

    except Exception as e:
        log.error(f"Whisper 转写失败: {e}")
        return None


# ============================================================
# 讯飞转写
# ============================================================

def _xunfei_sign(app_id: str, api_key: str, ts: str) -> str:
    base_string = app_id + ts
    md5_hash = hashlib.md5(base_string.encode()).hexdigest()
    md5_bytes = md5_hash.encode()
    signa = hmac.new(api_key.encode(), md5_bytes, hashlib.sha1).digest()
    return b64encode(signa).decode()


def transcribe_xunfei(audio_path: Path, creds: dict) -> Path | None:
    """使用讯飞语音转写（非实时）API 生成字幕"""
    xf = creds.get("xunfei", {})
    app_id = xf.get("app_id", "")
    api_key = xf.get("api_key", "")
    if not app_id or not api_key:
        log.error("讯飞凭证未配置（app_id / api_key）")
        return None

    try:
        ts = str(int(time.time()))
        signa = _xunfei_sign(app_id, api_key, ts)

        file_size = audio_path.stat().st_size
        log.info(f"上传音频到讯飞: {audio_path.name} ({file_size / 1024 / 1024:.1f} MB)")

        upload_params = {
            "appId": app_id, "signa": signa, "ts": ts,
            "fileSize": str(file_size), "fileName": audio_path.name,
            "duration": "200",
        }
        with open(audio_path, "rb") as f:
            resp = requests.post(
                XUNFEI_UPLOAD_URL, params=upload_params, data=f,
                headers={"Content-Type": "application/octet-stream"}, timeout=300,
            )
        result = resp.json()
        if result.get("code") != "000000":
            log.error(f"讯飞上传失败: {result}")
            return None

        order_id = result["content"]["orderId"]
        log.info(f"上传成功，任务 ID: {order_id}")
        return _xunfei_poll_result(audio_path, app_id, api_key, order_id)

    except Exception as e:
        log.error(f"讯飞转写失败: {e}")
        return None


def _xunfei_poll_result(audio_path, app_id, api_key, order_id):
    for attempt in range(120):
        time.sleep(10)
        ts = str(int(time.time()))
        signa = _xunfei_sign(app_id, api_key, ts)

        resp = requests.post(
            XUNFEI_GET_RESULT_URL,
            params={"appId": app_id, "signa": signa, "ts": ts, "orderId": order_id},
            timeout=30,
        )
        result = resp.json()
        status = result.get("content", {}).get("orderInfo", {}).get("status")

        if status == 4:
            log.info("讯飞转写完成，解析结果...")
            return _xunfei_parse_result(audio_path, result)
        elif status == -1:
            log.error(f"讯飞转写失败: {result}")
            return None
        else:
            if attempt % 6 == 0:
                log.info(f"讯飞转写中... (状态: {status})")

    log.error("讯飞转写超时")
    return None


def _xunfei_parse_result(audio_path: Path, result: dict) -> Path | None:
    try:
        order_result = result["content"]["orderResult"]
        lattice_list = json.loads(order_result).get("lattice", [])

        suffix = get_engine_suffix("xunfei")
        srt_path = audio_path.parent / f"{audio_path.stem}{suffix}.srt"
        idx = 0

        with open(srt_path, "w", encoding="utf-8") as f:
            for item in lattice_list:
                json_1best = json.loads(item.get("json_1best", "{}"))
                st = json_1best.get("st", {})
                bg_ms = int(st.get("bg", "0"))
                ed_ms = int(st.get("ed", "0"))

                words = []
                for rt in st.get("rt", []):
                    for ws in rt.get("ws", []):
                        for cw in ws.get("cw", []):
                            words.append(cw.get("w", ""))
                text = "".join(words).strip()
                if not text:
                    continue

                idx += 1
                f.write(f"{idx}\n")
                f.write(f"{format_srt_time(bg_ms / 1000)} --> ")
                f.write(f"{format_srt_time(ed_ms / 1000)}\n")
                f.write(f"{_t2s.convert(text)}\n\n")

        log.info(f"讯飞字幕生成成功: {srt_path} ({idx} 条)")
        return srt_path

    except Exception as e:
        log.error(f"讯飞结果解析失败: {e}")
        return None


# ============================================================
# 飞书转写
# ============================================================

def ensure_feishu_token(creds: dict, session: requests.Session) -> str:
    expire = creds["feishu"].get("user_token_expire_time", 0)
    if time.time() > expire - 300:
        url = f"{FEISHU_BASE}/authen/v1/oidc/refresh_access_token"
        body = {
            "grant_type": "refresh_token",
            "refresh_token": creds["feishu"]["user_refresh_token"],
        }
        app_resp = session.post(
            f"{FEISHU_BASE}/auth/v3/app_access_token/internal",
            json={
                "app_id": creds["feishu"]["app_id"],
                "app_secret": creds["feishu"]["app_secret"],
            },
            timeout=15
        )
        app_data = app_resp.json()
        app_token = app_data.get("app_access_token", "")

        headers = {
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        resp = session.post(url, json=body, headers=headers, timeout=15)
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"飞书 token 刷新失败: {data}")

        token_data = data["data"]
        creds["feishu"]["user_access_token"] = token_data["access_token"]
        creds["feishu"]["user_refresh_token"] = token_data["refresh_token"]
        creds["feishu"]["user_token_expire_time"] = int(time.time()) + token_data["expires_in"]

        with open(CFG_DIR / "credentials.yaml", "w", encoding="utf-8") as f:
            yaml.dump(creds, f, allow_unicode=True)
        log.info("飞书 token 已刷新")

    return creds["feishu"]["user_access_token"]


def _get_app_token(creds: dict, session: requests.Session) -> str:
    resp = session.post(
        f"{FEISHU_BASE}/auth/v3/app_access_token/internal",
        json={
            "app_id": creds["feishu"]["app_id"],
            "app_secret": creds["feishu"]["app_secret"],
        },
        timeout=15,
    )
    data = resp.json()
    token = data.get("app_access_token", "")
    if not token:
        raise RuntimeError(f"获取 app_access_token 失败: {data}")
    return token


def transcribe_feishu(audio_path: Path, creds: dict) -> Path | None:
    """使用飞书语音识别 API 生成字幕（分段识别，每段 ≤ 60 秒）"""
    session = requests.Session()

    try:
        token = _get_app_token(creds, session)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        pcm_path = _audio_to_pcm(audio_path)
        pcm_data = pcm_path.read_bytes()

        bytes_per_sec = 16000 * 2
        chunk_duration = 55
        chunk_size = bytes_per_sec * chunk_duration
        total_chunks = (len(pcm_data) + chunk_size - 1) // chunk_size

        log.info(f"PCM 总时长: {len(pcm_data) / bytes_per_sec / 60:.1f} 分钟, 分 {total_chunks} 段识别")

        url = f"{FEISHU_BASE}/speech_to_text/v1/speech/file_recognize"
        all_texts = []

        for i in range(total_chunks):
            offset = i * chunk_size
            chunk = pcm_data[offset:offset + chunk_size]
            speech_b64 = b64encode(chunk).decode()

            body = {
                "speech": {"speech": speech_b64},
                "config": {"engine_type": "16k_auto", "file_id": str(uuid.uuid4())},
            }

            start_sec = i * chunk_duration
            log.info(f"  识别第 {i + 1}/{total_chunks} 段 ({start_sec // 60}:{start_sec % 60:02d} 起)...")

            resp = session.post(url, json=body, headers=headers, timeout=60)
            result = resp.json()

            if result.get("code") != 0:
                log.warning(f"  第 {i + 1} 段识别失败: {result.get('msg', result)}")
                all_texts.append({"start": start_sec, "text": ""})
                continue

            text = result.get("data", {}).get("recognition_text", "")
            all_texts.append({"start": start_sec, "text": text.strip()})
            if text:
                log.info(f"  → {text[:60]}...")

        suffix = get_engine_suffix("feishu")
        srt_path = audio_path.parent / f"{audio_path.stem}{suffix}.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            idx = 0
            for item in all_texts:
                if not item["text"]:
                    continue
                idx += 1
                start = format_srt_time(item["start"])
                end = format_srt_time(item["start"] + chunk_duration)
                f.write(f"{idx}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{_t2s.convert(item['text'])}\n\n")

        log.info(f"飞书字幕生成成功: {srt_path} ({idx} 段)")
        return srt_path

    except Exception as e:
        log.error(f"飞书转写失败: {e}")
        return None


# ============================================================
# 阿里云转写
# ============================================================

def _aliyun_pop_sign(params: dict, access_key_secret: str, method: str = "GET") -> str:
    import urllib.parse
    sorted_params = sorted(params.items())
    query = urllib.parse.urlencode(sorted_params, quote_via=urllib.parse.quote)
    string_to_sign = f"{method}&%2F&{urllib.parse.quote(query, safe='')}"
    sign_key = (access_key_secret + "&").encode()
    signature = b64encode(
        hmac.new(sign_key, string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    return signature


def transcribe_aliyun(audio_path: Path, creds: dict) -> Path | None:
    ali = creds.get("aliyun", {})
    ak_id = ali.get("access_key_id", "")
    ak_secret = ali.get("access_key_secret", "")
    app_key = ali.get("app_key", "")
    if not ak_id or not ak_secret or not app_key:
        log.error("阿里云凭证未配置（access_key_id / access_key_secret / app_key）")
        return None

    try:
        return _aliyun_submit_local(audio_path, ak_id, ak_secret, app_key)
    except Exception as e:
        log.error(f"阿里云转写失败: {e}")
        return None


def _aliyun_submit_local(audio_path, ak_id, ak_secret, app_key):
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    task_body = json.dumps({
        "appkey": app_key, "file_link": "", "version": "4.0",
        "enable_words": False, "enable_timestamp": True,
    })

    common_params = {
        "Format": "JSON", "Version": ALIYUN_API_VERSION,
        "AccessKeyId": ak_id, "SignatureMethod": "HMAC-SHA1",
        "Timestamp": timestamp, "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
    }

    submit_params = {**common_params, "Action": "SubmitTask", "Task": task_body}
    submit_params["Signature"] = _aliyun_pop_sign(submit_params, ak_secret, "POST")

    log.info(f"提交阿里云转写任务: {audio_path.name}")
    resp = requests.post(ALIYUN_FILE_TRANS_URL, data=submit_params, timeout=60)
    result = resp.json()

    if result.get("StatusCode") != 21050000 and result.get("StatusText") != "SUCCESS":
        log.error(f"阿里云提交失败: {result}")
        return None

    task_id = result.get("TaskId")
    log.info(f"阿里云任务已提交，TaskId: {task_id}")
    return _aliyun_poll_result(audio_path, ak_id, ak_secret, task_id)


def _aliyun_poll_result(audio_path, ak_id, ak_secret, task_id):
    from datetime import datetime, timezone

    for attempt in range(120):
        time.sleep(10)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        query_params = {
            "Format": "JSON", "Version": ALIYUN_API_VERSION,
            "AccessKeyId": ak_id, "SignatureMethod": "HMAC-SHA1",
            "Timestamp": timestamp, "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "Action": "GetTaskResult", "TaskId": task_id,
        }
        query_params["Signature"] = _aliyun_pop_sign(query_params, ak_secret, "GET")

        resp = requests.get(ALIYUN_FILE_TRANS_URL, params=query_params, timeout=30)
        result = resp.json()
        status = result.get("StatusCode")

        if status == 21050000:
            log.info("阿里云转写完成，解析结果...")
            return _aliyun_parse_result(audio_path, result)
        elif status == 21050001:
            if attempt % 6 == 0:
                log.info(f"阿里云转写中... (StatusCode: {status})")
        else:
            log.error(f"阿里云转写失败: {result}")
            return None

    log.error("阿里云转写超时")
    return None


def _aliyun_parse_result(audio_path: Path, result: dict) -> Path | None:
    try:
        sentences = result.get("Result", {}).get("Sentences", [])
        if not sentences:
            log.warning("阿里云返回结果为空")
            return None

        suffix = get_engine_suffix("aliyun")
        srt_path = audio_path.parent / f"{audio_path.stem}{suffix}.srt"

        with open(srt_path, "w", encoding="utf-8") as f:
            for i, sent in enumerate(sentences, 1):
                begin_ms = sent.get("BeginTime", 0)
                end_ms = sent.get("EndTime", 0)
                text = sent.get("Text", "").strip()
                if not text:
                    continue
                f.write(f"{i}\n")
                f.write(f"{format_srt_time(begin_ms / 1000)} --> ")
                f.write(f"{format_srt_time(end_ms / 1000)}\n")
                f.write(f"{_t2s.convert(text)}\n\n")

        log.info(f"阿里云字幕生成成功: {srt_path} ({len(sentences)} 条)")
        return srt_path

    except Exception as e:
        log.error(f"阿里云结果解析失败: {e}")
        return None


# ============================================================
# 豆包（火山引擎）转写
# ============================================================

def _volc_sign(method, path, query, headers, body, ak_id, ak_secret):
    from datetime import datetime, timezone
    import urllib.parse

    now = datetime.now(timezone.utc)
    date_stamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")

    signed_headers_list = sorted(headers.keys())
    signed_headers = ";".join(signed_headers_list)
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in signed_headers_list)
    if query:
        sorted_qs = sorted(query.items())
        canonical_qs = urllib.parse.urlencode(sorted_qs, quote_via=urllib.parse.quote)
    else:
        canonical_qs = ""

    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_request = (
        f"{method}\n{path}\n{canonical_qs}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    credential_scope = f"{date_stamp}/{VOLC_REGION}/{VOLC_SERVICE}/request"
    string_to_sign = (
        f"HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
        + hashlib.sha256(canonical_request.encode()).hexdigest()
    )

    def _hmac_sha256(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _hmac_sha256(ak_secret.encode(), date_stamp)
    k_region = _hmac_sha256(k_date, VOLC_REGION)
    k_service = _hmac_sha256(k_region, VOLC_SERVICE)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth = (
        f"HMAC-SHA256 Credential={ak_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return auth, amz_date


def transcribe_doubao(audio_path: Path, creds: dict) -> Path | None:
    db = creds.get("doubao", {})
    ak_id = db.get("access_key_id", "")
    ak_secret = db.get("access_key_secret", "")
    app_id = db.get("app_id", "")
    if not ak_id or not ak_secret or not app_id:
        log.error("豆包凭证未配置（access_key_id / access_key_secret / app_id）")
        return None

    try:
        audio_data = audio_path.read_bytes()
        audio_b64 = b64encode(audio_data).decode()

        body_dict = {
            "app": {"appid": app_id, "cluster": "volc_auc_common"},
            "user": {"uid": "subtitle_generator"},
            "audio": {
                "format": audio_path.suffix.lstrip("."),
                "url": "", "data": audio_b64,
            },
            "additions": {"with_timestamp": "True"},
        }
        body = json.dumps(body_dict).encode()

        path = "/api/v1/auc/submit"
        headers_to_sign = {"content-type": "application/json", "host": VOLC_HOST}
        auth, amz_date = _volc_sign("POST", path, {}, headers_to_sign, body, ak_id, ak_secret)

        req_headers = {
            "Content-Type": "application/json", "Host": VOLC_HOST,
            "X-Date": amz_date, "Authorization": auth,
        }

        log.info(f"提交豆包转写任务: {audio_path.name}")
        resp = requests.post(
            f"https://{VOLC_HOST}{path}", data=body,
            headers=req_headers, timeout=300,
        )
        result = resp.json()

        if result.get("code") != 0 and result.get("resp", {}).get("code") != 0:
            log.error(f"豆包提交失败: {result}")
            return None

        task_id = result.get("id") or result.get("resp", {}).get("id", "")
        log.info(f"豆包任务已提交，ID: {task_id}")
        return _doubao_poll_result(audio_path, ak_id, ak_secret, app_id, task_id)

    except Exception as e:
        log.error(f"豆包转写失败: {e}")
        return None


def _doubao_poll_result(audio_path, ak_id, ak_secret, app_id, task_id):
    for attempt in range(120):
        time.sleep(10)

        body_dict = {"appid": app_id, "id": task_id}
        body = json.dumps(body_dict).encode()
        path = "/api/v1/auc/query"

        headers_to_sign = {"content-type": "application/json", "host": VOLC_HOST}
        auth, amz_date = _volc_sign("POST", path, {}, headers_to_sign, body, ak_id, ak_secret)

        req_headers = {
            "Content-Type": "application/json", "Host": VOLC_HOST,
            "X-Date": amz_date, "Authorization": auth,
        }

        resp = requests.post(
            f"https://{VOLC_HOST}{path}", data=body,
            headers=req_headers, timeout=30,
        )
        result = resp.json()
        code = result.get("code", result.get("resp", {}).get("code"))

        if code == 0:
            utterances = result.get("utterances") or result.get("resp", {}).get("utterances")
            if utterances is not None:
                log.info("豆包转写完成，解析结果...")
                return _doubao_parse_result(audio_path, utterances)
        elif code and code < 0:
            log.error(f"豆包转写失败: {result}")
            return None

        if attempt % 6 == 0:
            log.info(f"豆包转写中... (attempt {attempt + 1})")

    log.error("豆包转写超时")
    return None


def _doubao_parse_result(audio_path: Path, utterances: list) -> Path | None:
    try:
        suffix = get_engine_suffix("doubao")
        srt_path = audio_path.parent / f"{audio_path.stem}{suffix}.srt"
        idx = 0

        with open(srt_path, "w", encoding="utf-8") as f:
            for utt in utterances:
                text = utt.get("text", "").strip()
                if not text:
                    continue
                start_ms = utt.get("start_time", 0)
                end_ms = utt.get("end_time", 0)
                idx += 1
                f.write(f"{idx}\n")
                f.write(f"{format_srt_time(start_ms / 1000)} --> ")
                f.write(f"{format_srt_time(end_ms / 1000)}\n")
                f.write(f"{_t2s.convert(text)}\n\n")

        log.info(f"豆包字幕生成成功: {srt_path} ({idx} 条)")
        return srt_path

    except Exception as e:
        log.error(f"豆包结果解析失败: {e}")
        return None


# ============================================================
# Pipeline 入口
# ============================================================

def has_subtitle(output_dir, base_name):
    """检查是否已有字幕文件"""
    ori_srt = output_dir / f"{base_name}_ori.srt"
    if ori_srt.exists():
        log.info(f"已有原始字幕: {ori_srt}")
        return True
    return False


def generate_whisper_subtitle(audio_path, output_dir, base_name, config):
    """使用 Whisper 生成字幕（pipeline 模式入口）"""
    wm_srt = output_dir / f"{base_name}_wm.srt"
    if wm_srt.exists():
        log.info(f"Whisper 字幕已存在，跳过: {wm_srt}")
        return wm_srt

    if not audio_path.exists():
        log.error(f"音频文件不存在: {audio_path}")
        return None

    log.info(f"Whisper 转写: {audio_path.name}")
    whisper_cfg = config.get("whisper", {})
    model_size = whisper_cfg.get("model", "medium")
    force_cpu = whisper_cfg.get("force_cpu", False)

    result = transcribe_whisper(audio_path, model_size, force_cpu)
    if result is None:
        log.error("Whisper 转写失败")
        return None

    if result != wm_srt and result.exists():
        result.rename(wm_srt)
        log.info(f"字幕已重命名: {result.name} → {wm_srt.name}")

    return wm_srt


# ============================================================
# main — pipeline 模式 + CLI 模式
# ============================================================

def main():
    # 先尝试解析 CLI 参数，判断走哪种模式
    parser = argparse.ArgumentParser(description="音频转字幕工具（pipeline / CLI 双模式）")
    parser.add_argument("audio_file", nargs="?", default=None, help="音频文件路径（CLI 模式）")
    parser.add_argument("--whisper", action="store_true", help="使用 Whisper 转写")
    parser.add_argument("--xunfei", action="store_true", help="使用讯飞转写")
    parser.add_argument("--aliyun", action="store_true", help="使用阿里云转写")
    parser.add_argument("--doubao", action="store_true", help="使用豆包（火山引擎）转写")
    parser.add_argument("--feishu", action="store_true", help="使用飞书转写")
    parser.add_argument("--all", action="store_true", help="使用所有方式转写")
    parser.add_argument("--model", default="medium",
                        help="Whisper 模型大小: tiny/small/medium/large (默认: medium)")
    parser.add_argument("--cpu", action="store_true", help="强制使用 CPU 模式")
    args = parser.parse_args()

    cli_mode = args.audio_file is not None

    if cli_mode:
        _main_cli(args)
    else:
        _main_pipeline()


def _main_cli(args):
    """CLI 模式：指定音频文件 + 引擎参数"""
    audio_path = Path(args.audio_file)

    if audio_path.suffix.lower() == ".ts":
        mp3_path = audio_path.with_suffix(".mp3")
        if mp3_path.exists():
            log.info(f"检测到同名 MP3，优先使用: {mp3_path}")
            audio_path = mp3_path
        else:
            log.warning(f"未找到同名 MP3，将使用 TS 文件（内存占用较高）")

    if not audio_path.exists():
        log.error(f"文件不存在: {audio_path}")
        return

    log.info(f"处理音频: {audio_path}")

    use_whisper = args.whisper or args.all
    use_xunfei = args.xunfei or args.all
    use_aliyun = args.aliyun or args.all
    use_doubao = args.doubao or args.all
    use_feishu = args.feishu or args.all

    if not any([use_whisper, use_xunfei, use_aliyun, use_doubao, use_feishu]):
        use_whisper = True

    creds = None
    if any([use_xunfei, use_aliyun, use_doubao, use_feishu]):
        _, creds = load_config()

    if use_whisper:
        log.info("=== Whisper 转写 ===")
        result = transcribe_whisper(audio_path, args.model, args.cpu)
        if result:
            log.info(f"Whisper 字幕: {result}")

    if use_xunfei:
        log.info("=== 讯飞转写 ===")
        result = transcribe_xunfei(audio_path, creds)
        if result:
            log.info(f"讯飞字幕: {result}")

    if use_aliyun:
        log.info("=== 阿里云转写 ===")
        result = transcribe_aliyun(audio_path, creds)
        if result:
            log.info(f"阿里云字幕: {result}")

    if use_doubao:
        log.info("=== 豆包转写 ===")
        result = transcribe_doubao(audio_path, creds)
        if result:
            log.info(f"豆包字幕: {result}")

    if use_feishu:
        log.info("=== 飞书转写 ===")
        result = transcribe_feishu(audio_path, creds)
        if result:
            log.info(f"飞书字幕: {result}")

    log.info("完成")


def _main_pipeline():
    """Pipeline 模式：扫描 output 目录，环境变量驱动"""
    config, creds = load_config()

    input_file = os.environ.get("DL_VIDEO_INPUT_FILE", "")
    input_type = os.environ.get("DL_VIDEO_INPUT_TYPE", "")
    mp3_input = input_file if input_type == "mp3" else ""

    if mp3_input:
        mp3_path = Path(mp3_input)
        output_dir = mp3_path.parent
        log.info(f"MP3 输入模式，输出目录: {output_dir}")
        mp3_files = [mp3_path]
    else:
        output_dir = PROJECT_DIR / config.get("output_dir", "output")
        output_dir.mkdir(exist_ok=True)
        mp3_files = sorted(output_dir.glob("*.mp3"))
        if not mp3_files:
            log.warning(f"未找到 MP3 文件: {output_dir}")
            return

    for i, mp3_path in enumerate(mp3_files):
        log.info(f"=== 任务 {i+1}/{len(mp3_files)} ===")
        base_name = mp3_path.stem

        if has_subtitle(output_dir, base_name):
            log.info("已有原始字幕，无需 Whisper 转写")
            continue

        result = generate_whisper_subtitle(mp3_path, output_dir, base_name, config)
        if result:
            log.info(f"Whisper 字幕生成完成: {result}")
        else:
            log.error(f"字幕生成失败: {base_name}")

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
