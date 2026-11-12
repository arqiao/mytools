# 参数调用示例:
#   python src/s3_subtitle.py <media-file> --whisper
#   python src/s3_subtitle.py <media-file> --whisper --backend mlx --model large-v3
#   python src/s3_subtitle.py <media-file> --whisper --backend faster-whisper --model medium
#   python src/s3_subtitle.py <media-file> --whisper --cpu
#   python src/s3_subtitle.py <media-file> --qwen3-asr
#   python src/s3_subtitle.py <media-file> --qwen3-asr --qwen3-language Chinese --qwen3-context "剪映 keyframe"
#   python src/s3_subtitle.py <media-file> --qwen3-asr --qwen3-diarize
#   python src/s3_subtitle.py output/demo.m4a --qwen3-asr --qwen3-language Chinese --qwen3-context "剪映 keyframe"
#   python src/s3_subtitle.py <media-file> --xunfei
#   python src/s3_subtitle.py <media-file> --aliyun
#   python src/s3_subtitle.py <media-file> --doubao
#   python src/s3_subtitle.py <media-file> --doubao-seed-asr
#   python src/s3_subtitle.py <media-file> --feishu
#   python src/s3_subtitle.py <media-file> --all
"""Step3: 媒体文件转字幕（fallback，当 Step1 未获取到字幕时使用）

本地识别模型优先使用缓存；距上次成功检查超过 14 天时比较远端版本，有变化才更新。

用法:
  # 默认使用 Whisper（Mac 自动选 MLX，其他系统选 faster-whisper）
  python src/s3_subtitle.py output/demo.m4a --whisper
  python src/s3_subtitle.py output/demo.mp4 --whisper

  # 指定 Whisper 后端和模型
  python src/s3_subtitle.py output/demo.mp3 --whisper --backend mlx --model large-v3-turbo
  python src/s3_subtitle.py output/demo.mp3 --whisper --backend faster-whisper --model medium
  python src/s3_subtitle.py output/demo.mp3 --whisper --cpu          # 强制 CPU 模式

  # Qwen3-ASR 本地识别（Apple Silicon Mac；优先使用本机缓存，缺少模型时自动下载）
  # 距上次成功检查超过 14 天时检查仓库版本，有变化才更新模型
  # 安装依赖: python -m pip install mlx-qwen3-asr==0.4.4
  python src/s3_subtitle.py output/demo.mp4 --qwen3-asr
  python src/s3_subtitle.py output/demo.mp4 --qwen3-asr --qwen3-language Chinese --qwen3-context "剪映 keyframe"
  # 说话人分离需先安装: python -m pip install "mlx-qwen3-asr[diarize]==0.4.4"
  # 并接受 pyannote/speaker-diarization-community-1 的模型条款，设置 PYANNOTE_AUTH_TOKEN
  python src/s3_subtitle.py output/demo.mp4 --qwen3-asr --qwen3-diarize

  # 使用云端 ASR 引擎
  python src/s3_subtitle.py output/demo.mp3 --xunfei                 # 讯飞
  python src/s3_subtitle.py output/demo.mp3 --aliyun                  # 阿里云
  python src/s3_subtitle.py output/demo.mp3 --doubao                  # 豆包（火山引擎）
  python src/s3_subtitle.py output/demo.mp3 --doubao-seed-asr         # 豆包 Seed-ASR 2.0
  python src/s3_subtitle.py output/demo.mp3 --feishu                  # 飞书

  # 同时使用多种引擎
  python src/s3_subtitle.py output/demo.mp3 --all

参数:
  audio_file        媒体文件路径（必填，支持音频和常见视频格式）
  --whisper         使用 Whisper 本地转写
  --qwen3-asr       使用 Qwen3-ASR 1.7B 本地转写并生成字幕（Mac MLX）
  --qwen3-language  指定 Qwen3-ASR 识别语言（默认自动检测，如 Chinese / English）
  --qwen3-context   Qwen3-ASR 专业词汇提示，词语之间用空格分隔
  --qwen3-diarize   Qwen3-ASR 说话人分离，在字幕中标注说话人
  --xunfei          使用讯飞语音转写
  --aliyun          使用阿里云语音转写
  --doubao          使用豆包（火山引擎）语音转写
  --doubao-seed-asr 使用 Agent Plan 豆包 Seed-ASR 2.0 流式识别
  --feishu          使用飞书语音转写
  --all             使用所有引擎转写
  --backend         Whisper 后端: auto / mlx / faster-whisper（默认: auto）
  --model           Whisper 模型: large-v3-turbo / large-v3 / medium / large / small / tiny
                    （默认: Mac 用 large-v3-turbo，其他系统用 medium）
  --cpu             强制使用 faster-whisper CPU 模式（显存不足或禁用 Mac MLX 时使用）
"""

import os
import sys

# CUDA DLL 路径：pip install nvidia-cublas-cu12 后 DLL 不在系统 PATH 中
_cuda_bin = os.path.join(
    sys.prefix, "Lib", "site-packages", "nvidia", "cublas", "bin"
)
if os.path.isdir(_cuda_bin) and _cuda_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _cuda_bin + os.pathsep + os.environ.get("PATH", "")

# Hugging Face 国内访问经常出现 SSL/连接中断。默认走镜像；如需官方源，可在命令前覆盖：
# HF_ENDPOINT=https://huggingface.co python src/s3_subtitle.py ...
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import argparse
import gc
import gzip
import hashlib
import hmac
import json
import logging
import platform
import re
import struct
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from base64 import b64encode
from difflib import SequenceMatcher
from pathlib import Path

import requests
import yaml

try:
    from websockets.sync.client import connect as websocket_connect
except ImportError:
    websocket_connect = None

from modules.ffmpeg_utils import mp3_to_wav, mp3_to_pcm, _get_duration
from modules.config_utils import load_config as _load_config
from modules.progress_utils import InlineProgress, clear_active_progress

# 繁体转简体转换器
try:
    import opencc
    _t2s = opencc.OpenCC("t2s")
except ImportError:
    class _NoopOpenCC:
        def convert(self, text: str) -> str:
            return text

    _t2s = _NoopOpenCC()

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
try:
    _input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
except yaml.YAMLError:
    _input_cfg = {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
LOG_DIR.mkdir(exist_ok=True)

class _ProgressAwareStreamHandler(logging.StreamHandler):
    def emit(self, record):
        clear_active_progress()
        super().emit(record)


class _ConsoleFormatter(logging.Formatter):
    def format(self, record):
        message = record.getMessage()
        if record.levelno >= logging.ERROR:
            return f"[ERROR] {message}"
        if record.levelno >= logging.WARNING:
            return f"[WARN] {message}"
        return message


_file_handler = logging.FileHandler(LOG_DIR / "subtitle.log", encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_console_handler = _ProgressAwareStreamHandler()
_console_handler.setFormatter(_ConsoleFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler], force=True)
log = logging.getLogger(__name__)

from modules.feishu_token import FEISHU_BASE, ensure_feishu_token

FASTER_WHISPER_DEFAULT_MODEL = "medium"
MLX_WHISPER_DEFAULT_MODEL = "large-v3-turbo"
QWEN3_ASR_MODEL = "moona3k/mlx-qwen3-asr-1.7b-8bit"
QWEN3_ALIGNER_MODEL = "mlx-community/Qwen3-ForcedAligner-0.6B-8bit"
QWEN3_UPDATE_CHECK_DIR = Path.home() / ".cache" / "dl-video" / "qwen3-model-checks"
MLX_WHISPER_MODELS = {
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}
MLX_WHISPER_DEFAULT_SPEED = {
    "large-v3-turbo": 4.0,
    "turbo": 4.0,
    "large-v3": 2.0,
    "large": 2.0,
    "medium": 3.0,
    "small": 6.0,
    "tiny": 10.0,
}
WHISPER_SPEED_CACHE = CFG_DIR / "whisper_speed.json"
AUDIO_EXTENSIONS = {
    ".aac", ".aiff", ".ape", ".flac", ".m4a", ".mp3", ".ogg", ".opus",
    ".pcm", ".wav", ".wma",
}
VIDEO_EXTENSIONS = {
    ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv",
}

# 讯飞语音转写 API 地址
XUNFEI_UPLOAD_URL = "https://raasr.xfyun.cn/v2/api/upload"
XUNFEI_GET_RESULT_URL = "https://raasr.xfyun.cn/v2/api/getResult"


def load_config():
    """加载凭证配置（兼容旧调用：仅返回 creds）"""
    _, creds = _load_config(include_input=False)
    return creds


def get_engine_suffix(engine_name: str) -> str:
    """获取引擎对应的文件名后缀（从 config.yaml 的 s3_engine_suffix 读取）"""
    cfg_path = CFG_DIR / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        suffix_map = cfg.get("s3_engine_suffix", {})
        return suffix_map.get(engine_name, f"_{engine_name}")
    return f"_{engine_name}"


def format_srt_time(seconds: float) -> str:
    """将秒数格式化为 SRT 时间格式 (HH:MM:SS,mmm)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_progress_time(seconds: float) -> str:
    """将秒数格式化为进度时间 HH:MM:SS"""
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def _is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def _describe_media(path: Path) -> str:
    if _is_video_file(path):
        return "视频"
    if _is_audio_file(path):
        return "音频"
    return "媒体"


def _should_prepare_for_whisper(media_path: Path) -> bool:
    return _is_video_file(media_path) or media_path.stat().st_size > 50 * 1024 * 1024


class TranscribeProgress:
    """长耗时转写心跳日志"""

    def __init__(self, label: str, total_sec: float = 0, interval_sec: int = 30,
                 estimated_audio_speed: float = 0):
        self.label = label
        self.total_sec = total_sec
        self.interval_sec = interval_sec
        self.estimated_audio_speed = estimated_audio_speed
        self.started_at = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._progress = InlineProgress(log, interval_sec=interval_sec)

    def __enter__(self):
        self.started_at = time.time()
        total = f"，音频时长 {format_progress_time(self.total_sec)}" if self.total_sec else ""
        self._progress.update(f"{self.label}: 开始{total}", force=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self._progress.done()
        elapsed = time.time() - self.started_at
        if exc_type is None:
            log.info(f"{self.label}: 完成，用时 {format_progress_time(elapsed)}")
        else:
            log.info(f"{self.label}: 中断，用时 {format_progress_time(elapsed)}")

    def _write_inline(self, message: str):
        self._progress.update(message, force=True)

    def _run(self):
        while not self._stop.wait(self.interval_sec):
            elapsed = time.time() - self.started_at
            if self.total_sec and self.estimated_audio_speed > 0:
                raw_estimated_audio = elapsed * self.estimated_audio_speed
                estimated_audio = min(raw_estimated_audio, self.total_sec)
                pct = min(int(estimated_audio / self.total_sec * 100), 99)
                if raw_estimated_audio >= self.total_sec:
                    self._write_inline(
                        f"{self.label}: 估算 99%，等待模型收尾，已用 {format_progress_time(elapsed)}"
                    )
                else:
                    remaining = max((self.total_sec - estimated_audio) / self.estimated_audio_speed, 0)
                    self._write_inline(
                        f"{self.label}: 估算 {pct}%，"
                        f"剩余约 {format_progress_time(remaining)}，"
                        f"已用 {format_progress_time(elapsed)}"
                    )
            else:
                total = f"，音频时长 {format_progress_time(self.total_sec)}" if self.total_sec else ""
                self._progress.update(
                    f"{self.label}: 转写中，用时 {format_progress_time(elapsed)}{total}",
                    force=True,
                )


def log_audio_progress(label: str, current_sec: float, total_sec: float):
    """记录按音频时间推进的进度"""
    if total_sec <= 0:
        return
    current_sec = min(max(current_sec, 0), total_sec)
    pct = min(int(current_sec / total_sec * 100), 100)
    progress = getattr(log_audio_progress, "_progress", None)
    if progress is None:
        progress = InlineProgress(log, interval_sec=5)
        setattr(log_audio_progress, "_progress", progress)
    progress.update(
        f"{label}: 总进度 {pct}% "
        f"({format_progress_time(current_sec)} / {format_progress_time(total_sec)})",
        force=pct >= 100,
    )
    if pct >= 100:
        progress.done()


def _load_whisper_speed_cache() -> dict:
    if not WHISPER_SPEED_CACHE.exists():
        return {}
    try:
        return json.loads(WHISPER_SPEED_CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"读取 Whisper 速度缓存失败: {e}")
        return {}


def _save_whisper_speed_cache(cache: dict):
    try:
        WHISPER_SPEED_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"写入 Whisper 速度缓存失败: {e}")


def _get_whisper_estimated_speed(backend: str, model_name: str) -> tuple[float, str]:
    """返回音频秒/运行秒的估算速度和来源说明"""
    key = f"{backend}:{model_name}"
    cache = _load_whisper_speed_cache()
    cached = cache.get(key, {})
    speed = float(cached.get("audio_sec_per_sec", 0) or 0)
    if speed > 0:
        return speed, "历史实测"
    if backend == "mlx":
        return MLX_WHISPER_DEFAULT_SPEED.get(model_name, 2.0), "默认估算"
    return 0, "未启用"


def _update_whisper_estimated_speed(backend: str, model_name: str, total_sec: float, elapsed_sec: float):
    """按本次完整转写耗时更新速度缓存，使用 EMA 避免单次波动过大"""
    if total_sec <= 0 or elapsed_sec <= 0:
        return
    key = f"{backend}:{model_name}"
    observed = total_sec / elapsed_sec
    cache = _load_whisper_speed_cache()
    previous = float(cache.get(key, {}).get("audio_sec_per_sec", 0) or 0)
    speed = previous * 0.7 + observed * 0.3 if previous > 0 else observed
    cache[key] = {
        "audio_sec_per_sec": round(speed, 3),
        "last_observed_audio_sec_per_sec": round(observed, 3),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_whisper_speed_cache(cache)


def _prepare_audio_for_whisper(audio_path: Path) -> Path:
    """将媒体预处理为本地 16kHz 单声道 WAV，减少 Whisper 内存占用。

    NAS/SMB 挂载盘在 ffmpeg 关闭大 WAV 文件或随后随机读取时，可能返回
    ``Bad file descriptor``。原始媒体可保留在挂载盘，但转写用的临时 WAV
    必须放在本机临时目录。
    """
    if _is_video_file(audio_path):
        log.info("  检测到视频文件，提取音频轨道用于转写")
    stat = audio_path.stat()
    fingerprint = hashlib.sha256(
        f"{audio_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    ).hexdigest()[:16]
    temp_dir = Path(tempfile.gettempdir()) / "dl-video-whisper"
    temp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = temp_dir / f"{audio_path.stem}.{fingerprint}.16k.wav"
    return mp3_to_wav(audio_path, wav_path)


def _prepare_media_for_audio_backend(media_path: Path) -> Path:
    """云端/PCM 后端收到视频时，先提取音频；音频文件保持原路径。"""
    if _is_video_file(media_path):
        return _prepare_audio_for_whisper(media_path)
    return media_path


def _cleanup_prepared_audio(original_path: Path, prepared_path: Path | None):
    if prepared_path and prepared_path != original_path:
        prepared_path.unlink(missing_ok=True)


def _parse_srt_time(ts: str) -> float:
    """解析 SRT 时间戳 'HH:MM:SS,mmm' 为秒数"""
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", ts.strip())
    if not m:
        return 0.0
    h, mi, s, ms = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    return h * 3600 + mi * 60 + s + ms / 1000.0


def _parse_srt_last_entry(srt_path: Path) -> tuple:
    """解析 srt 文件最后一条字幕的序号和结束时间，返回 (last_idx, last_end_sec)"""
    last_idx, last_end = 0, 0.0
    try:
        text = srt_path.read_text(encoding="utf-8").rstrip()
        if not text:
            return 0, 0.0
        # SRT 条目之间用空行分隔，取最后一个非空块
        blocks = re.split(r"\n\s*\n", text)
        for block in reversed(blocks):
            lines = block.strip().splitlines()
            if len(lines) >= 2 and lines[0].strip().isdigit():
                last_idx = int(lines[0].strip())
                ts_match = re.search(r"-->\s*(\S+)", lines[1])
                if ts_match:
                    last_end = _parse_srt_time(ts_match.group(1))
                break
    except Exception as e:
        log.warning(f"解析已有字幕文件失败: {e}")
    return last_idx, last_end


def _is_apple_silicon_mac() -> bool:
    """判断当前环境是否为 Apple Silicon Mac"""
    return sys.platform == "darwin" and platform.machine().lower() == "arm64"


def _normalize_model_name(model_name: str) -> str:
    """将模型名规范化为适合配置 key 和文件名的格式"""
    return model_name.strip().lower().replace("_", "-")


def _select_whisper_backend(backend: str, force_cpu: bool = False) -> str:
    """选择 Whisper 后端"""
    if backend != "auto":
        return backend
    if force_cpu:
        return "faster-whisper"
    if _is_apple_silicon_mac():
        return "mlx"
    return "faster-whisper"


def _select_whisper_model(backend: str, model_name: str | None) -> str:
    """根据后端选择默认模型"""
    if model_name:
        return _normalize_model_name(model_name)
    if backend == "mlx":
        return MLX_WHISPER_DEFAULT_MODEL
    return FASTER_WHISPER_DEFAULT_MODEL


def _resolve_mlx_model_ref(model_name: str) -> str:
    """解析 MLX Whisper 模型名为 Hugging Face repo 或本地路径"""
    normalized = _normalize_model_name(model_name)
    return MLX_WHISPER_MODELS.get(normalized, model_name)


def _get_mlx_model_download_cmd(model_name: str) -> str:
    normalized = _normalize_model_name(model_name)
    return f"python src/tools/model_downloader.py --backend mlx {normalized}"


def _write_segments_to_srt(segments, srt_path: Path) -> int:
    """将 start/end/text 结构的片段写成 SRT"""
    with open(srt_path, "w", encoding="utf-8") as f:
        return _append_segments_to_srt(f, segments)


def _append_segments_to_srt(srt_file, segments, start_idx=0, time_offset=0,
                            skip_before=0) -> int:
    """将一批相对时间字幕追加到 SRT，返回最新序号。"""
    idx = start_idx
    for segment in segments:
        if isinstance(segment, dict):
            start = float(segment.get("start", 0) or 0)
            end = float(segment.get("end", 0) or 0)
            text = str(segment.get("text", "")).strip()
        else:
            start = float(getattr(segment, "start", 0) or 0)
            end = float(getattr(segment, "end", 0) or 0)
            text = str(getattr(segment, "text", "")).strip()
        if not text or start < skip_before:
            continue
        idx += 1
        srt_file.write(f"{idx}\n")
        srt_file.write(
            f"{format_srt_time(time_offset + max(start, skip_before))} --> "
            f"{format_srt_time(time_offset + end)}\n"
        )
        srt_file.write(f"{_t2s.convert(text)}\n\n")
    return idx


def transcribe_mlx_whisper(audio_path: Path, model_name: str = MLX_WHISPER_DEFAULT_MODEL) -> Path | None:
    """使用 mlx-whisper 在 Apple Silicon Mac 上生成字幕"""
    try:
        normalized_model = _normalize_model_name(model_name)
        model_ref = _resolve_mlx_model_ref(normalized_model)
        suffix = get_engine_suffix(f"mlx_whisper_{normalized_model.replace('-', '_')}")
        srt_path = audio_path.parent / f"{audio_path.stem}{suffix}.srt"

        try:
            from tools.model_downloader import resolve_model_path
        except ImportError:
            log.error("未安装 huggingface_hub，请先运行: pip install huggingface_hub")
            return None

        local_path = resolve_model_path(normalized_model, "mlx")
        if not local_path:
            log.error(f"MLX Whisper 模型未下载: {model_ref}")
            log.error(f"请先运行: {_get_mlx_model_download_cmd(normalized_model)}")
            log.error("如镜像不可用，可使用官方源: "
                      f"HF_ENDPOINT=https://huggingface.co {_get_mlx_model_download_cmd(normalized_model)}")
            return None

        try:
            import mlx_whisper
        except ImportError:
            log.error("未安装 mlx-whisper，请先运行: pip install mlx-whisper")
            return None
        except Exception as e:
            log.error(f"mlx-whisper 不可用: {e}")
            return None

        total_audio_sec = _get_duration(audio_path)
        # 超过 30 分钟时分段处理，避免 MLX 单次长音频解码异常等待。
        segment_duration = 20 * 60
        overlap = 2
        use_segments = total_audio_sec > 30 * 60
        if use_segments or _should_prepare_for_whisper(audio_path):
            whisper_input = _prepare_audio_for_whisper(audio_path)
        else:
            whisper_input = audio_path

        log.info(f"  本地模型: {local_path}")
        log.info(f"  转写输入: {whisper_input}")
        estimated_speed, speed_source = _get_whisper_estimated_speed("mlx", normalized_model)
        if total_audio_sec and estimated_speed > 0:
            log.info(f"  估算速度: {estimated_speed:.2f}x 实时（{speed_source}）")
        transcribe_started_at = time.time()
        if use_segments:
            import numpy as np
            import wave

            with wave.open(str(whisper_input), "rb") as wf:
                n_frames = wf.getnframes()
                sample_rate = wf.getframerate()
            total_sec = n_frames / sample_rate
            step_duration = segment_duration - overlap
            n_segments = int((total_sec - overlap) / step_duration) + 1
            log.info(
                f"  分段: 音频 {format_progress_time(total_sec)}，"
                f"共 {n_segments} 段，每段 {segment_duration // 60} 分钟，重叠 {overlap} 秒"
            )

            srt_idx = 0
            resume_seg = 0
            if srt_path.exists() and srt_path.stat().st_size > 0:
                srt_idx, last_end = _parse_srt_last_entry(srt_path)
                if srt_idx:
                    resume_seg = min(int(last_end / step_duration), n_segments)
                    log.info(
                        f"检测到已有字幕 ({srt_idx} 条，至 {format_srt_time(last_end)})，"
                        f"从第 {resume_seg + 1}/{n_segments} 段续转"
                    )
                    if resume_seg >= n_segments:
                        return srt_path

            file_mode = "a" if resume_seg else "w"
            with open(srt_path, file_mode, encoding="utf-8") as srt_file:
                for seg_i in range(resume_seg, n_segments):
                    start_sec = seg_i * step_duration
                    start_frame = int(start_sec * sample_rate)
                    end_frame = min(
                        int((start_sec + segment_duration) * sample_rate), n_frames
                    )
                    chunk_frames = end_frame - start_frame
                    end_sec = start_sec + chunk_frames / sample_rate
                    segment_label = (
                        f"段 {seg_i + 1}/{n_segments} "
                        f"{format_progress_time(start_sec)} - {format_progress_time(end_sec)}"
                    )
                    with wave.open(str(whisper_input), "rb") as wf:
                        wf.setpos(start_frame)
                        raw = wf.readframes(chunk_frames)
                    chunk_audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    with TranscribeProgress(
                        segment_label,
                        chunk_frames / sample_rate,
                        interval_sec=10,
                        estimated_audio_speed=estimated_speed,
                    ):
                        result = mlx_whisper.transcribe(
                            chunk_audio,
                            path_or_hf_repo=local_path,
                            language="zh",
                        )
                    segments = result.get("segments", []) if isinstance(result, dict) else []
                    srt_idx = _append_segments_to_srt(
                        srt_file, segments, srt_idx, start_sec,
                        overlap if seg_i else 0,
                    )
                    srt_file.flush()
                    log_audio_progress(
                        "MLX Whisper", end_sec, total_sec
                    )
                    del chunk_audio, raw, result, segments
                    gc.collect()
            idx = srt_idx
        else:
            with TranscribeProgress("MLX Whisper", total_audio_sec, interval_sec=10,
                                    estimated_audio_speed=estimated_speed):
                result = mlx_whisper.transcribe(
                    str(whisper_input),
                    path_or_hf_repo=local_path,
                    language="zh",
                )
            segments = result.get("segments", []) if isinstance(result, dict) else []
            idx = _write_segments_to_srt(segments, srt_path)
        transcribe_elapsed = time.time() - transcribe_started_at
        if total_audio_sec:
            observed_speed = total_audio_sec / transcribe_elapsed if transcribe_elapsed > 0 else 0
            log.info(f"  实测速度: {observed_speed:.2f}x 实时")
            _update_whisper_estimated_speed("mlx", normalized_model, total_audio_sec, transcribe_elapsed)

        log.info(f"  字幕: {srt_path} ({idx} 条)")

        if whisper_input != audio_path and whisper_input.suffix == ".wav":
            whisper_input.unlink()
            log.info(f"  已清理临时文件: {whisper_input}")

        return srt_path

    except Exception as e:
        log.error(f"MLX Whisper 转写失败: {e}")
        if "Hub" in str(e) or "ConnectError" in str(e) or "snapshot" in str(e):
            log.error("模型下载/定位失败。可重试当前命令，或显式指定镜像: "
                      "HF_ENDPOINT=https://hf-mirror.com python src/tools/model_downloader.py --backend mlx ...")
        return None


def transcribe_faster_whisper(audio_path: Path, model_size: str = FASTER_WHISPER_DEFAULT_MODEL,
                              force_cpu: bool = False) -> Path | None:
    """使用 faster-whisper 生成字幕（长音频自动分段处理）"""
    try:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            log.error("未安装 faster-whisper，请先运行: pip install faster-whisper")
            return None

        try:
            from tools.model_downloader import resolve_model_path
        except ImportError:
            log.error("未安装 huggingface_hub，请先运行: pip install huggingface_hub")
            return None

        # 优先缓存，按两周间隔检查更新；直接加载检查后选定的本地版本。
        local_path = resolve_model_path(model_size)
        if not local_path:
            log.error(f"模型 {model_size} 不可用，请先运行: python src/tools/model_downloader.py {model_size}")
            return None

        total_audio_sec = _get_duration(audio_path)
        if total_audio_sec:
            log.info(f"  音频时长: {format_progress_time(total_audio_sec)}")

        # 预处理：视频或大文件先转为 16kHz WAV
        if _should_prepare_for_whisper(audio_path):
            whisper_input = _prepare_audio_for_whisper(audio_path)
        else:
            whisper_input = audio_path

        # 自动检测 CUDA（通过 CTranslate2，不依赖 PyTorch）
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
            log.info("  强制使用 CPU 模式")

        log.info(f"  运行模式: {device}/{compute_type}")
        model = WhisperModel(local_path, device=device, compute_type=compute_type)

        import numpy as np
        import wave

        # 读取 WAV 文件信息
        if str(whisper_input).endswith(".wav"):
            with wave.open(str(whisper_input), "rb") as wf:
                n_frames = wf.getnframes()
                sample_rate = wf.getframerate()
            total_sec = n_frames / sample_rate
        else:
            # 非 WAV 直接传路径
            total_sec = 0

        # 长音频（> 30 分钟）分段处理，每段 3 分钟，重叠 5 秒
        segment_duration = 180  # 3 分钟
        overlap = 5  # 5 秒重叠，避免切割点丢字

        suffix = get_engine_suffix(f"whisper_{model_size}")
        srt_path = audio_path.parent / f"{audio_path.stem}{suffix}.srt"
        srt_idx = 0

        if total_sec > 1800 and str(whisper_input).endswith(".wav"):
            n_segments = int((total_sec - overlap) / (segment_duration - overlap)) + 1
            log.info(
                f"  分段: 音频 {format_progress_time(total_sec)}，"
                f"共 {n_segments} 段，每段 {segment_duration // 60} 分钟，重叠 {overlap} 秒"
            )

            # 断点续转：检测已有字幕文件
            resume_seg = 0
            if srt_path.exists() and srt_path.stat().st_size > 0:
                last_idx, last_end = _parse_srt_last_entry(srt_path)
                if last_idx > 0:
                    srt_idx = last_idx
                    resume_seg = int(last_end / (segment_duration - overlap))
                    log.info(f"检测到已有字幕 ({last_idx} 条，至 {format_srt_time(last_end)})，"
                             f"从第 {resume_seg + 1}/{n_segments} 段续转")
                    if resume_seg >= n_segments:
                        log.info("所有段落已转写完成，跳过")
                        return srt_path

            file_mode = "a" if resume_seg > 0 else "w"
            with open(srt_path, file_mode, encoding="utf-8") as f:
                for seg_i in range(n_segments):
                    if seg_i < resume_seg:
                        continue

                    start_sec = seg_i * (segment_duration - overlap)
                    start_frame = int(start_sec * sample_rate)
                    end_frame = min(int((start_sec + segment_duration) * sample_rate), n_frames)
                    chunk_frames = end_frame - start_frame
                    end_sec = start_sec + chunk_frames / sample_rate
                    segment_label = (
                        f"段 {seg_i + 1}/{n_segments} "
                        f"{format_progress_time(start_sec)} - {format_progress_time(end_sec)}"
                    )

                    with wave.open(str(whisper_input), "rb") as wf:
                        wf.setpos(start_frame)
                        raw = wf.readframes(chunk_frames)

                    chunk_array = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                    with TranscribeProgress(segment_label, chunk_frames / sample_rate):
                        segments, info = model.transcribe(
                            chunk_array,
                            language="zh",
                            beam_size=1,
                            vad_filter=True,
                            vad_parameters=dict(min_silence_duration_ms=500),
                        )

                        for segment in segments:
                            # 跳过重叠区域的重复内容（非首段的前 overlap 秒）
                            if seg_i > 0 and segment.start < overlap:
                                continue
                            srt_idx += 1
                            abs_start = start_sec + segment.start
                            abs_end = start_sec + segment.end
                            f.write(f"{srt_idx}\n")
                            f.write(f"{format_srt_time(abs_start)} --> {format_srt_time(abs_end)}\n")
                            f.write(f"{_t2s.convert(segment.text.strip())}\n\n")
                    f.flush()
                    log_audio_progress("faster-whisper", end_sec, total_sec)

                    del chunk_array, raw, segments
                    gc.collect()
                    try:
                        import torch
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except ImportError:
                        pass
        else:
            # 短音频直接处理
            log.info(f"  转写输入: {whisper_input}")
            with TranscribeProgress("faster-whisper", total_audio_sec):
                segments, info = model.transcribe(
                    str(whisper_input),
                    language="zh",
                    beam_size=3,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500),
                )
                with open(srt_path, "w", encoding="utf-8") as f:
                    last_progress_sec = 0.0
                    for i, segment in enumerate(segments, 1):
                        f.write(f"{i}\n")
                        f.write(f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n")
                        f.write(f"{_t2s.convert(segment.text.strip())}\n\n")
                        srt_idx = i
                        if total_audio_sec and segment.end - last_progress_sec >= 60:
                            log_audio_progress("faster-whisper", segment.end, total_audio_sec)
                            last_progress_sec = segment.end
                    log_audio_progress("faster-whisper", total_audio_sec, total_audio_sec)

        log.info(f"  字幕: {srt_path} ({srt_idx} 条)")

        # 清理临时 16k WAV 文件
        if whisper_input != audio_path and whisper_input.suffix == ".wav":
            whisper_input.unlink()
            log.info(f"  已清理临时文件: {whisper_input}")

        return srt_path

    except Exception as e:
        log.error(f"Whisper 转写失败: {e}")
        return None


def transcribe_whisper(audio_path: Path, model_name: str | None = None,
                       backend: str = "auto", force_cpu: bool = False) -> Path | None:
    """按后端策略调用本地 Whisper 转写"""
    selected_backend = _select_whisper_backend(backend, force_cpu)
    selected_model = _select_whisper_model(selected_backend, model_name)

    log.info(f"  后端: {selected_backend}")
    log.info(f"  模型: {selected_model}")
    if selected_backend == "mlx":
        result = transcribe_mlx_whisper(audio_path, selected_model)
        if result or backend != "auto":
            return result

        fallback_model = FASTER_WHISPER_DEFAULT_MODEL
        log.warning(f"MLX 后端不可用，回退到 faster-whisper/{fallback_model}")
        return transcribe_faster_whisper(audio_path, fallback_model, force_cpu=True)
    if selected_backend == "faster-whisper":
        return transcribe_faster_whisper(audio_path, selected_model, force_cpu)

    log.error(f"不支持的 Whisper 后端: {selected_backend}")
    return None


def _resolve_qwen3_model(repo_id: str, weights_name: str) -> str:
    """Qwen 与 Whisper 共用缓存策略，沿用 Qwen 已有的检查记录。"""
    from modules.model_cache import resolve_cached_model

    required = ["config.json", "tokenizer_config.json", "vocab.json", "merges.txt", weights_name]
    return resolve_cached_model(
        repo_id, lambda path: all((path / name).is_file() for name in required),
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
        check_dir=QWEN3_UPDATE_CHECK_DIR,
    )


def _restore_qwen3_punctuation_fuzzy(words: list[dict], text: str) -> list[dict]:
    """字词与原文有少量差异时，按相同字符锚点找回标点。"""
    is_punct = lambda c: unicodedata.category(c).startswith("P")
    raw_chars = [c.casefold() for c in text if not c.isspace() and not is_punct(c)]
    aligned_chars = []
    aligned_word_indexes = []
    for word_index, word in enumerate(words):
        for char in word["text"]:
            if not char.isspace() and not is_punct(char):
                aligned_chars.append(char.casefold())
                aligned_word_indexes.append(word_index)
    if not raw_chars or not aligned_chars:
        return words

    matches = SequenceMatcher(None, raw_chars, aligned_chars, autojunk=False).get_matching_blocks()
    anchors = {
        match.a + offset: aligned_word_indexes[match.b + offset]
        for match in matches for offset in range(match.size)
    }
    if len(anchors) < len(raw_chars) * 0.7:
        return words

    def find_word(raw_index: int, opening: bool) -> int:
        if raw_index in anchors:
            return anchors[raw_index]
        previous = max((i for i in anchors if i < raw_index), default=None)
        following = min((i for i in anchors if i > raw_index), default=None)
        if opening and following is not None:
            index = anchors[following] - (following - raw_index)
        elif previous is not None:
            index = anchors[previous] + (raw_index - previous)
            if following is not None:
                index = min(index, anchors[following] - 1)
        else:
            index = 0
        return min(max(index, 0), len(words) - 1)

    prefixes = [""] * len(words)
    suffixes = [""] * len(words)
    raw_index = -1
    for char in text:
        if char.isspace():
            continue
        if not is_punct(char):
            raw_index += 1
            continue
        opening = unicodedata.category(char) in {"Ps", "Pi"}
        word_index = find_word(raw_index + 1 if opening else raw_index, opening)
        if opening:
            prefixes[word_index] += char
        else:
            suffixes[word_index] += char
    return [
        {**word, "text": prefixes[i] + word["text"] + suffixes[i]}
        for i, word in enumerate(words)
    ]


def _group_qwen3_subtitles(result) -> list[dict]:
    """按识别块恢复标点，再跨块合并完整句子。"""
    from mlx_qwen3_asr.writers import group_subtitle_segments, restore_punctuation

    words = result.segments
    chunks = getattr(result, "chunks", None)
    if chunks:
        restored = []
        offset = 0
        for chunk_index, chunk in enumerate(chunks, 1):
            end = float(chunk["end"])
            next_offset = offset
            while next_offset < len(words) and float(words[next_offset]["start"]) < end:
                next_offset += 1
            chunk_words = words[offset:next_offset]
            chunk_text = chunk.get("text", "")
            patched = restore_punctuation(chunk_words, chunk_text)
            raw_marks = sum(unicodedata.category(c).startswith("P") for c in chunk_text)
            kept_marks = sum(
                unicodedata.category(c).startswith("P")
                for word in patched for c in word["text"]
            )
            if kept_marks < raw_marks:
                recovered = _restore_qwen3_punctuation_fuzzy(chunk_words, chunk_text)
                recovered_marks = sum(
                    unicodedata.category(c).startswith("P")
                    for word in recovered for c in word["text"]
                )
                if recovered_marks > kept_marks:
                    log.info(
                        f"Qwen3-ASR 第 {chunk_index} 段使用近似对齐恢复标点: "
                        f"{recovered_marks}/{raw_marks}"
                    )
                    patched, kept_marks = recovered, recovered_marks
            if kept_marks < raw_marks:
                log.warning(
                    f"Qwen3-ASR 第 {chunk_index} 段"
                    f"标点回填不完整: {kept_marks}/{raw_marks}"
                )
            restored.extend(patched)
            offset = next_offset
        if offset < len(words):
            log.warning(f"Qwen3-ASR 有 {len(words) - offset} 个字词未匹配到识别块")
            restored.extend(words[offset:])
    else:
        restored = restore_punctuation(words, result.text)

    # 关闭库默认的字数、词数和时长切分；只按句末标点、说话人或明显长停顿分段。
    return group_subtitle_segments(
        restored, language=result.language, max_words=sys.maxsize,
        max_chars=sys.maxsize, max_duration_sec=float("inf"), max_gap_sec=5.0,
    )


def transcribe_qwen3_asr(audio_path: Path, language: str | None = None,
                         context: str = "", diarize: bool = False) -> Path | None:
    """使用 MLX Qwen3-ASR 识别，强制对齐后将字词合并为可读 SRT。"""
    if not _is_apple_silicon_mac():
        log.error("Qwen3-ASR 本地后端需要 Apple Silicon Mac（MLX）")
        return None

    prepared_audio = None
    try:
        from mlx_qwen3_asr import transcribe

        log.info(f"  模型: {QWEN3_ASR_MODEL}")
        log.info(f"  时间轴对齐模型: {QWEN3_ALIGNER_MODEL}")
        model_path = _resolve_qwen3_model(QWEN3_ASR_MODEL, "weights.safetensors")
        aligner_path = _resolve_qwen3_model(QWEN3_ALIGNER_MODEL, "model.safetensors")
        prepared_audio = _prepare_audio_for_whisper(audio_path)
        total_sec = _get_duration(prepared_audio)
        with TranscribeProgress("Qwen3-ASR", total_sec):
            result = transcribe(
                str(prepared_audio),
                model=model_path,
                language=language,
                context=context,
                diarize=diarize,
                return_timestamps=True,
                return_chunks=True,
                forced_aligner=aligner_path,
            )
            if result.truncated:
                raise RuntimeError("识别输出被截断，未生成完整字幕")
            if not result.segments:
                raise RuntimeError("未获得有效时间轴，无法生成字幕")
            segments = _group_qwen3_subtitles(result)
            if not segments:
                raise RuntimeError("未识别到可用的字幕片段")

        if diarize:
            speaker_at_start = {
                float(word["start"]): word.get("speaker") for word in result.segments
            }
            speaker_numbers = {}
            for segment in segments:
                speaker = speaker_at_start.get(float(segment["start"]))
                if not speaker:
                    raise RuntimeError("说话人分离未返回可用的说话人标签")
                number = speaker_numbers.setdefault(speaker, len(speaker_numbers) + 1)
                segment["text"] = f"说话人{number}：{segment['text']}"

        srt_path = audio_path.parent / f"{audio_path.stem}{get_engine_suffix('qwen3_asr')}.srt"
        count = _write_segments_to_srt(segments, srt_path)
        log.info(f"  字幕: {srt_path} ({count} 条)")
        return srt_path
    except ImportError as e:
        package = '"mlx-qwen3-asr[diarize]==0.4.4"' if "pyannote" in str(e) else "mlx-qwen3-asr==0.4.4"
        log.error(f"Qwen3-ASR 依赖不可用: {e}；安装: python -m pip install {package}")
        return None
    except Exception as e:
        log.error(f"Qwen3-ASR 转写失败: {e}")
        return None
    finally:
        _cleanup_prepared_audio(audio_path, prepared_audio)


def _xunfei_sign(app_id: str, api_key: str, ts: str) -> str:
    """生成讯飞语音转写 API 签名"""
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

    upload_path = None
    try:
        upload_path = _prepare_media_for_audio_backend(audio_path)
        ts = str(int(time.time()))
        signa = _xunfei_sign(app_id, api_key, ts)

        # 1. 上传音频文件
        file_size = upload_path.stat().st_size
        log.info(f"上传音频到讯飞: {upload_path.name} ({file_size / 1024 / 1024:.1f} MB)")

        upload_params = {
            "appId": app_id,
            "signa": signa,
            "ts": ts,
            "fileSize": str(file_size),
            "fileName": upload_path.name,
            "duration": "200",
        }
        with open(upload_path, "rb") as f:
            resp = requests.post(
                XUNFEI_UPLOAD_URL,
                params=upload_params,
                data=f,
                headers={"Content-Type": "application/octet-stream"},
                timeout=300,
            )
        result = resp.json()
        if result.get("code") != "000000":
            log.error(f"讯飞上传失败: {result}")
            return None

        order_id = result["content"]["orderId"]
        log.info(f"上传成功，任务 ID: {order_id}")

        # 2. 轮询结果
        return _xunfei_poll_result(audio_path, app_id, api_key, order_id)

    except Exception as e:
        log.error(f"讯飞转写失败: {e}")
        return None
    finally:
        _cleanup_prepared_audio(audio_path, upload_path)


def _xunfei_poll_result(audio_path, app_id, api_key, order_id):
    """轮询讯飞转写结果并生成 SRT"""
    for attempt in range(120):  # 最多等 20 分钟
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

        if status == 4:  # 转写完成
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
    """解析讯飞转写结果，生成 SRT 文件"""
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

                # 拼接该句的所有词
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



def _audio_to_pcm(audio_path: Path) -> Path:
    """将音频转为 PCM（16kHz, 16bit, 单声道）"""
    return mp3_to_pcm(audio_path)


def _get_app_token(creds: dict, session: requests.Session) -> str:
    """获取飞书 app_access_token（tenant token）"""
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
    work_audio_path = None
    pcm_path = None

    try:
        # 1. 获取 token
        token = _get_app_token(creds, session)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # 2. 转换为 PCM
        work_audio_path = _prepare_media_for_audio_backend(audio_path)
        pcm_path = _audio_to_pcm(work_audio_path)
        pcm_data = pcm_path.read_bytes()

        # PCM 参数：16kHz, 16bit, 单声道 → 每秒 32000 字节
        bytes_per_sec = 16000 * 2  # 16kHz * 16bit(2bytes)
        chunk_duration = 55  # 每段 55 秒（留余量，API 限制 60 秒）
        chunk_size = bytes_per_sec * chunk_duration
        total_chunks = (len(pcm_data) + chunk_size - 1) // chunk_size

        log.info(f"PCM 总时长: {len(pcm_data) / bytes_per_sec / 60:.1f} 分钟, 分 {total_chunks} 段识别")

        # 3. 逐段调用 file_recognize API
        url = f"{FEISHU_BASE}/speech_to_text/v1/speech/file_recognize"
        all_texts = []
        recognize_progress = InlineProgress(log, interval_sec=5)

        for i in range(total_chunks):
            offset = i * chunk_size
            chunk = pcm_data[offset:offset + chunk_size]
            speech_b64 = b64encode(chunk).decode()

            body = {
                "speech": {"speech": speech_b64},
                "config": {"engine_type": "16k_auto", "file_id": str(uuid.uuid4())},
            }

            start_sec = i * chunk_duration
            recognize_progress.update(
                f"飞书识别进度: {i + 1}/{total_chunks} 段 "
                f"({start_sec // 60}:{start_sec % 60:02d} 起)",
                force=True,
            )

            resp = session.post(url, json=body, headers=headers, timeout=60)
            result = resp.json()

            if result.get("code") != 0:
                log.warning(f"  第 {i + 1} 段识别失败: {result.get('msg', result)}")
                all_texts.append({"start": start_sec, "text": ""})
                continue

            text = result.get("data", {}).get("recognition_text", "")
            all_texts.append({"start": start_sec, "text": text.strip()})
            if text:
                log.debug(f"  → {text[:60]}...")
        recognize_progress.done()

        # 4. 生成 SRT 字幕
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
    finally:
        if work_audio_path and work_audio_path != audio_path:
            if pcm_path:
                pcm_path.unlink(missing_ok=True)
            _cleanup_prepared_audio(audio_path, work_audio_path)


# ---- 阿里云语音转写 API 常量 ----
ALIYUN_FILE_TRANS_URL = "https://filetrans.cn-shanghai.aliyuncs.com"
ALIYUN_API_VERSION = "2018-08-17"


def _aliyun_pop_sign(params: dict, access_key_secret: str, method: str = "GET") -> str:
    """阿里云 POP API 签名（SignatureVersion 1.0, HMAC-SHA1）"""
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
    """使用阿里云录音文件识别 API 生成字幕"""
    ali = creds.get("aliyun", {})
    ak_id = ali.get("access_key_id", "")
    ak_secret = ali.get("access_key_secret", "")
    app_key = ali.get("app_key", "")
    if not ak_id or not ak_secret or not app_key:
        log.error("阿里云凭证未配置（access_key_id / access_key_secret / app_key）")
        return None

    try:
        # 阿里云需要音频 URL，本地文件用 file_link 方式提交
        task = {
            "appkey": app_key,
            "file_link": "",  # 占位，下面用本地上传方式
            "version": "4.0",
            "enable_words": False,
        }
        return _aliyun_submit_local(audio_path, ak_id, ak_secret, app_key)

    except Exception as e:
        log.error(f"阿里云转写失败: {e}")
        return None


def _aliyun_submit_local(audio_path, ak_id, ak_secret, app_key):
    """阿里云录音文件识别 - 使用 RESTful API 提交本地文件"""
    import urllib.parse
    from datetime import datetime, timezone

    # 提交转写任务
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    task_body = json.dumps({
        "appkey": app_key,
        "file_link": "",
        "version": "4.0",
        "enable_words": False,
        "enable_timestamp": True,
    })

    common_params = {
        "Format": "JSON",
        "Version": ALIYUN_API_VERSION,
        "AccessKeyId": ak_id,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": timestamp,
        "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
    }

    # POST 提交任务
    submit_params = {**common_params, "Action": "SubmitTask", "Task": task_body}
    submit_params["Signature"] = _aliyun_pop_sign(submit_params, ak_secret, "POST")

    # 阿里云录音文件识别支持直接上传
    log.info(f"提交阿里云转写任务: {audio_path.name}")
    resp = requests.post(
        ALIYUN_FILE_TRANS_URL,
        data=submit_params,
        timeout=60,
    )
    result = resp.json()

    if result.get("StatusCode") != 21050000 and result.get("StatusText") != "SUCCESS":
        log.error(f"阿里云提交失败: {result}")
        return None

    task_id = result.get("TaskId")
    log.info(f"阿里云任务已提交，TaskId: {task_id}")

    # 轮询结果
    return _aliyun_poll_result(audio_path, ak_id, ak_secret, task_id)


def _aliyun_poll_result(audio_path, ak_id, ak_secret, task_id):
    """轮询阿里云转写结果"""
    import urllib.parse
    from datetime import datetime, timezone

    for attempt in range(120):
        time.sleep(10)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        query_params = {
            "Format": "JSON",
            "Version": ALIYUN_API_VERSION,
            "AccessKeyId": ak_id,
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": timestamp,
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "Action": "GetTaskResult",
            "TaskId": task_id,
        }
        query_params["Signature"] = _aliyun_pop_sign(query_params, ak_secret, "GET")

        resp = requests.get(ALIYUN_FILE_TRANS_URL, params=query_params, timeout=30)
        result = resp.json()
        status = result.get("StatusCode")

        if status == 21050000:  # 完成
            log.info("阿里云转写完成，解析结果...")
            return _aliyun_parse_result(audio_path, result)
        elif status == 21050001:  # 排队/处理中
            if attempt % 6 == 0:
                log.info(f"阿里云转写中... (StatusCode: {status})")
        else:
            log.error(f"阿里云转写失败: {result}")
            return None

    log.error("阿里云转写超时")
    return None


def _aliyun_parse_result(audio_path: Path, result: dict) -> Path | None:
    """解析阿里云转写结果，生成 SRT"""
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


# ---- 豆包（火山引擎）语音识别 ----
VOLC_SERVICE = "sami"
VOLC_HOST = "open.volcengineapi.com"
VOLC_REGION = "cn-north-1"


def _volc_sign(method, path, query, headers, body, ak_id, ak_secret):
    """火山引擎 API v4 签名（HMAC-SHA256）"""
    from datetime import datetime, timezone
    import urllib.parse

    now = datetime.now(timezone.utc)
    date_stamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")

    # 1. 规范请求
    signed_headers_list = sorted(headers.keys())
    signed_headers = ";".join(signed_headers_list)
    canonical_headers = "".join(
        f"{k}:{headers[k]}\n" for k in signed_headers_list
    )
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

    # 2. 待签名字符串
    credential_scope = f"{date_stamp}/{VOLC_REGION}/{VOLC_SERVICE}/request"
    string_to_sign = (
        f"HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
        + hashlib.sha256(canonical_request.encode()).hexdigest()
    )

    # 3. 计算签名
    def _hmac_sha256(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _hmac_sha256(ak_secret.encode(), date_stamp)
    k_region = _hmac_sha256(k_date, VOLC_REGION)
    k_service = _hmac_sha256(k_region, VOLC_SERVICE)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    # 4. Authorization header
    auth = (
        f"HMAC-SHA256 Credential={ak_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return auth, amz_date


def transcribe_doubao(audio_path: Path, creds: dict) -> Path | None:
    """使用豆包（火山引擎）录音文件识别 API 生成字幕"""
    db = creds.get("doubao", {})
    ak_id = db.get("access_key_id", "")
    ak_secret = db.get("access_key_secret", "")
    app_id = db.get("app_id", "")
    if not ak_id or not ak_secret or not app_id:
        log.error("豆包凭证未配置（access_key_id / access_key_secret / app_id）")
        return None

    upload_path = None
    try:
        # 提交转写任务（音频 base64 内联）
        upload_path = _prepare_media_for_audio_backend(audio_path)
        audio_data = upload_path.read_bytes()
        audio_b64 = b64encode(audio_data).decode()

        body_dict = {
            "app": {"appid": app_id, "cluster": "volc_auc_common"},
            "user": {"uid": "subtitle_generator"},
            "audio": {
                "format": upload_path.suffix.lstrip("."),
                "url": "",
                "data": audio_b64,
            },
            "additions": {"with_timestamp": "True"},
        }
        body = json.dumps(body_dict).encode()

        path = "/api/v1/auc/submit"
        headers_to_sign = {
            "content-type": "application/json",
            "host": VOLC_HOST,
        }
        auth, amz_date = _volc_sign("POST", path, {}, headers_to_sign, body, ak_id, ak_secret)

        req_headers = {
            "Content-Type": "application/json",
            "Host": VOLC_HOST,
            "X-Date": amz_date,
            "Authorization": auth,
        }

        log.info(f"提交豆包转写任务: {audio_path.name}")
        resp = requests.post(
            f"https://{VOLC_HOST}{path}",
            data=body,
            headers=req_headers,
            timeout=300,
        )
        result = resp.json()

        if result.get("code") != 0 and result.get("resp", {}).get("code") != 0:
            log.error(f"豆包提交失败: {result}")
            return None

        task_id = result.get("id") or result.get("resp", {}).get("id", "")
        log.info(f"豆包任务已提交，ID: {task_id}")

        # 轮询结果
        return _doubao_poll_result(audio_path, ak_id, ak_secret, app_id, task_id)

    except Exception as e:
        log.error(f"豆包转写失败: {e}")
        return None
    finally:
        _cleanup_prepared_audio(audio_path, upload_path)


def _doubao_poll_result(audio_path, ak_id, ak_secret, app_id, task_id):
    """轮询豆包转写结果"""
    for attempt in range(120):
        time.sleep(10)

        body_dict = {"appid": app_id, "id": task_id}
        body = json.dumps(body_dict).encode()
        path = "/api/v1/auc/query"

        headers_to_sign = {
            "content-type": "application/json",
            "host": VOLC_HOST,
        }
        auth, amz_date = _volc_sign("POST", path, {}, headers_to_sign, body, ak_id, ak_secret)

        req_headers = {
            "Content-Type": "application/json",
            "Host": VOLC_HOST,
            "X-Date": amz_date,
            "Authorization": auth,
        }

        resp = requests.post(
            f"https://{VOLC_HOST}{path}",
            data=body,
            headers=req_headers,
            timeout=30,
        )
        result = resp.json()
        code = result.get("code", result.get("resp", {}).get("code"))

        if code == 0:
            # 检查是否有转写结果
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
    """解析豆包转写结果，生成 SRT"""
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


# ---- 豆包 Seed-ASR 2.0（方舟 Agent Plan 流式语音识别）----
SEED_ASR_WS_URL = "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_nostream"
SEED_ASR_RESOURCE_ID = "volc.seedasr.sauc.duration"
SEED_ASR_SAMPLE_RATE = 16000
SEED_ASR_SEGMENT_MS = 200


def _seed_asr_api_key(creds: dict) -> str:
    """优先读取 Agent Plan 专属 Key，兼容此前单独的 Seed-ASR 配置。"""
    agent_plan = creds.get("volcengine-agentplan", {})
    legacy = creds.get("doubao-seed-asr", {})
    return agent_plan.get("api_key", "") or legacy.get("api_key", "")


def _build_seed_asr_headers(api_key: str, request_id: str) -> dict:
    """构造 Agent Plan Seed-ASR WebSocket 握手请求头。"""
    return {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": SEED_ASR_RESOURCE_ID,
        "X-Api-Request-Id": request_id,
        "X-Api-Connect-Id": request_id,
        "X-Api-Sequence": "-1",
    }


def _build_seed_asr_full_request(sequence: int) -> bytes:
    """构造官方二进制协议的 full client request。"""
    payload = {
        "user": {"uid": "subtitle_generator"},
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": SEED_ASR_SAMPLE_RATE,
            "bits": 16,
            "channel": 1,
            "language": "zh-CN",
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "enable_nonstream": False,
        },
    }
    compressed = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    header = bytes([0x11, 0x11, 0x11, 0x00])
    return header + struct.pack(">iI", sequence, len(compressed)) + compressed


def _build_seed_asr_audio_request(sequence: int, audio: bytes, is_last: bool) -> bytes:
    """构造 audio-only request；最后一包使用负序号和结束标记。"""
    flags = 0b0011 if is_last else 0b0001
    wire_sequence = -sequence if is_last else sequence
    compressed = gzip.compress(audio)
    header = bytes([0x11, (0b0010 << 4) | flags, 0x01, 0x00])
    return header + struct.pack(">iI", wire_sequence, len(compressed)) + compressed


def _parse_seed_asr_response(frame: bytes) -> dict:
    """解析 Agent Plan Seed-ASR 的 full response 或 error response。"""
    if not isinstance(frame, bytes) or len(frame) < 4:
        raise ValueError("Seed-ASR 返回了无效的 WebSocket 二进制包")

    header_size = (frame[0] & 0x0F) * 4
    message_type = frame[1] >> 4
    flags = frame[1] & 0x0F
    serialization = frame[2] >> 4
    compression = frame[2] & 0x0F
    offset = header_size
    sequence = None
    if flags & 0b0001:
        if len(frame) < offset + 4:
            raise ValueError("Seed-ASR 响应缺少序号")
        sequence = struct.unpack(">i", frame[offset:offset + 4])[0]
        offset += 4

    code = 0
    if message_type == 0b1001:
        if len(frame) < offset + 4:
            raise ValueError("Seed-ASR 响应缺少负载长度")
        payload_size = struct.unpack(">I", frame[offset:offset + 4])[0]
        offset += 4
    elif message_type == 0b1111:
        if len(frame) < offset + 8:
            raise ValueError("Seed-ASR 错误响应不完整")
        code, payload_size = struct.unpack(">II", frame[offset:offset + 8])
        offset += 8
    else:
        raise ValueError(f"Seed-ASR 返回未知消息类型: {message_type}")

    payload_bytes = frame[offset:offset + payload_size]
    if len(payload_bytes) != payload_size:
        raise ValueError("Seed-ASR 响应负载不完整")
    if compression == 0b0001 and payload_bytes:
        payload_bytes = gzip.decompress(payload_bytes)

    payload = {}
    if payload_bytes:
        text = payload_bytes.decode("utf-8", errors="replace")
        if serialization == 0b0001:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {"message": text}
        else:
            payload = {"message": text}
    return {
        "code": code,
        "sequence": sequence,
        "is_last": bool(flags & 0b0010),
        "payload": payload,
    }


def _write_seed_asr_srt(audio_path: Path, result: dict) -> Path | None:
    """把 Seed-ASR 逐句结果写入 SRT。"""
    utterances = result.get("result", {}).get("utterances", [])
    if not utterances:
        log.warning("Seed-ASR 未返回逐句时间轴，无法生成 SRT")
        return None

    suffix = get_engine_suffix("doubao_seed_asr")
    srt_path = audio_path.parent / f"{audio_path.stem}{suffix}.srt"
    idx = 0
    with open(srt_path, "w", encoding="utf-8") as f:
        for utterance in utterances:
            text = utterance.get("text", "").strip()
            if not text:
                continue
            idx += 1
            f.write(f"{idx}\n")
            f.write(f"{format_srt_time(utterance.get('start_time', 0) / 1000)} --> ")
            f.write(f"{format_srt_time(utterance.get('end_time', 0) / 1000)}\n")
            f.write(f"{_t2s.convert(text)}\n\n")

    if not idx:
        srt_path.unlink(missing_ok=True)
        log.warning("Seed-ASR 未返回有效字幕文本")
        return None
    log.info(f"Seed-ASR 字幕生成成功: {srt_path} ({idx} 条)")
    return srt_path


def transcribe_doubao_seed_asr(audio_path: Path, creds: dict) -> Path | None:
    """通过 Agent Plan WebSocket 流式识别媒体文件并生成 SRT。"""
    api_key = _seed_asr_api_key(creds)
    if not api_key:
        log.error("Seed-ASR 凭证未配置（gkeys.yaml 的 volcengine-agentplan.api_key）")
        return None
    if websocket_connect is None:
        log.error("缺少 websockets 依赖，请运行: pip install 'websockets>=15'")
        return None

    process = None
    try:
        request_id = str(uuid.uuid4())
        headers = _build_seed_asr_headers(api_key, request_id)
        duration = _get_duration(audio_path)
        chunk_size = SEED_ASR_SAMPLE_RATE * 2 * SEED_ASR_SEGMENT_MS // 1000
        command = [
            "ffmpeg", "-v", "error", "-i", str(audio_path), "-vn",
            "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SEED_ASR_SAMPLE_RATE),
            "-f", "s16le", "pipe:1",
        ]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("无法启动 ffmpeg 音频流")

        latest_result = None
        log.info(f"连接豆包 Seed-ASR 2.0 Agent Plan: {audio_path.name}")
        with websocket_connect(
            SEED_ASR_WS_URL,
            additional_headers=headers,
            open_timeout=30,
            close_timeout=10,
            max_size=None,
        ) as connection:
            trace_id = connection.response.headers.get("X-Tt-Logid", "")
            if trace_id:
                log.info(f"Seed-ASR 已连接，trace_id: {trace_id}")

            connection.send(_build_seed_asr_full_request(1))
            response = _parse_seed_asr_response(connection.recv(timeout=30))
            if response["code"]:
                message = response["payload"].get("message", "-")
                log.error(f"Seed-ASR 初始化失败: code={response['code']}, message={message}")
                return None

            sequence = 2
            sent_bytes = 0
            current = process.stdout.read(chunk_size)
            if not current:
                raise RuntimeError("媒体文件没有可识别的音频轨道")

            final_received = False
            while current:
                following = process.stdout.read(chunk_size)
                is_last = not following
                segment_started = time.monotonic()
                connection.send(_build_seed_asr_audio_request(sequence, current, is_last))
                response = _parse_seed_asr_response(connection.recv(timeout=120))
                if response["code"]:
                    message = response["payload"].get("message", "-")
                    log.error(f"Seed-ASR 识别失败: code={response['code']}, message={message}")
                    return None
                if response["payload"].get("result"):
                    latest_result = response["payload"]
                final_received = response["is_last"]
                sent_bytes += len(current)
                log_audio_progress("Seed-ASR", sent_bytes / (SEED_ASR_SAMPLE_RATE * 2), duration)

                if not is_last:
                    remaining = SEED_ASR_SEGMENT_MS / 1000 - (time.monotonic() - segment_started)
                    if remaining > 0:
                        time.sleep(remaining)
                current = following
                sequence += 1

            while not final_received:
                response = _parse_seed_asr_response(connection.recv(timeout=120))
                if response["code"]:
                    message = response["payload"].get("message", "-")
                    log.error(f"Seed-ASR 收尾失败: code={response['code']}, message={message}")
                    return None
                if response["payload"].get("result"):
                    latest_result = response["payload"]
                final_received = response["is_last"]

        return_code = process.wait(timeout=10)
        if return_code != 0:
            detail = process.stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg 音频解码失败: {detail or return_code}")
        if duration:
            log_audio_progress("Seed-ASR", duration, duration)
        if not latest_result:
            log.warning("Seed-ASR 未返回识别结果")
            return None
        return _write_seed_asr_srt(audio_path, latest_result)
    except Exception as e:
        log.error(f"Seed-ASR 转写失败: {e}")
        return None
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def main():
    parser = argparse.ArgumentParser(description="媒体转字幕工具")
    parser.add_argument("audio_file", help="音频/视频文件路径")
    parser.add_argument("--whisper", action="store_true", help="使用 Whisper 转写")
    parser.add_argument("--qwen3-asr", action="store_true",
                        help="使用 Qwen3-ASR 1.7B 本地转写（Apple Silicon Mac MLX）")
    parser.add_argument("--qwen3-language", default=None,
                        help="指定 Qwen3-ASR 识别语言（默认自动检测，如 Chinese / English）")
    parser.add_argument("--qwen3-context", default="",
                        help="Qwen3-ASR 专业词汇提示，词语之间用空格分隔")
    parser.add_argument("--qwen3-diarize", action="store_true",
                        help="Qwen3-ASR 说话人分离，并在字幕中标注说话人（需 pyannote）")
    parser.add_argument("--xunfei", action="store_true", help="使用讯飞转写")
    parser.add_argument("--aliyun", action="store_true", help="使用阿里云转写")
    parser.add_argument("--doubao", action="store_true", help="使用豆包（火山引擎）转写")
    parser.add_argument("--doubao-seed-asr", action="store_true",
                        help="使用 Agent Plan 豆包 Seed-ASR 2.0 流式识别")
    parser.add_argument("--feishu", action="store_true", help="使用飞书转写")
    parser.add_argument("--all", action="store_true", help="使用所有方式转写")
    parser.add_argument("--backend", default="auto", choices=["auto", "mlx", "faster-whisper"],
                        help="Whisper 后端: auto/mlx/faster-whisper (默认: auto)")
    parser.add_argument("--model", default=None,
                        help="Whisper 模型: large-v3-turbo/large-v3/medium/large/small/tiny "
                             "(默认: Mac 使用 large-v3-turbo，其他系统使用 medium)")
    parser.add_argument("--cpu", action="store_true",
                        help="强制使用 faster-whisper CPU 模式（显存不足或禁用 Mac MLX 时使用）")
    args = parser.parse_args()

    audio_path = Path(args.audio_file)

    # 如果传入的是 .ts 文件，优先使用同名 .mp3（体积小、省内存）
    if audio_path.suffix.lower() == ".ts":
        mp3_path = audio_path.with_suffix(".mp3")
        if mp3_path.exists():
            log.info(f"检测到同名 MP3，优先使用: {mp3_path}")
            audio_path = mp3_path
        else:
            log.warning(f"未找到同名 MP3，将使用 TS 文件（内存占用较高）")

    if not audio_path.exists():
        log.error(f"文件不存在: {audio_path}")
        sys.exit(1)

    log.info("媒体转字幕")
    log.info(f"输入: {_describe_media(audio_path)} {audio_path}")

    use_whisper = args.whisper or args.all
    use_qwen3_asr = args.qwen3_asr or args.all
    use_xunfei = args.xunfei or args.all
    use_aliyun = args.aliyun or args.all
    use_doubao = args.doubao or args.all
    use_doubao_seed_asr = args.doubao_seed_asr or args.all
    use_feishu = args.feishu or args.all

    if not any([use_whisper, use_qwen3_asr, use_xunfei, use_aliyun,
                use_doubao, use_doubao_seed_asr, use_feishu]):
        use_whisper = True  # 默认使用 whisper

    creds = None
    if any([use_xunfei, use_aliyun, use_doubao, use_doubao_seed_asr, use_feishu]):
        creds = load_config()

    results = []
    if use_whisper:
        log.info("")
        log.info("Whisper 转写")
        result = transcribe_whisper(audio_path, args.model, args.backend, args.cpu)
        if result:
            results.append(result)

    if use_qwen3_asr:
        log.info("")
        log.info("Qwen3-ASR 转写")
        result = transcribe_qwen3_asr(audio_path, language=args.qwen3_language,
                                      context=args.qwen3_context, diarize=args.qwen3_diarize)
        if result:
            results.append(result)

    if use_xunfei:
        log.info("")
        log.info("讯飞转写")
        result = transcribe_xunfei(audio_path, creds)
        if result:
            results.append(result)

    if use_aliyun:
        log.info("")
        log.info("阿里云转写")
        result = transcribe_aliyun(audio_path, creds)
        if result:
            results.append(result)

    if use_doubao:
        log.info("")
        log.info("豆包转写")
        result = transcribe_doubao(audio_path, creds)
        if result:
            results.append(result)

    if use_doubao_seed_asr:
        log.info("")
        log.info("豆包 Seed-ASR 2.0 转写")
        result = transcribe_doubao_seed_asr(audio_path, creds)
        if result:
            results.append(result)

    if use_feishu:
        log.info("")
        log.info("飞书转写")
        result = transcribe_feishu(audio_path, creds)
        if result:
            results.append(result)

    if not results:
        log.error("没有生成任何字幕文件")
        sys.exit(1)

    log.info("")
    log.info(f"完成: 生成 {len(results)} 个字幕文件")


if __name__ == "__main__":
    main()
