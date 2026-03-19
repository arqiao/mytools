"""Step1-panda: 熊猫学院回放下载 — 视频 + 音频

API 流程:
  shortLink → getInviteMsg → inviteId
  inviteId  → getCourse    → 标题, thirdPartyId(VOD fileId), videoSource
  inviteId  → getLiveRoom  → liveRoomId
  liveRoomId→ getVideoSign → appId, fileId, psign
  psign     → getplayinfo/v4 (带自生成 overlay key) → drmToken
  drmToken  → m3u8 → license → AES-CBC 解密 → 真正的 key → 下载并解密 ts
"""

import base64
import json
import logging
import os
import re
import subprocess
from pathlib import Path

import requests
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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
        logging.FileHandler(LOG_DIR / "s1w_panda.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

PANDA_API = "https://fclive.pandacollege.cn/api"
VOD_APPID = 1254019786  # 腾讯云 VOD appId（从 ts URL 路径确认）
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')

# 腾讯云 tcplayer 硬编码的 RSA 公钥（用于 SimpleAES DRM overlay 加密）
RSA_PUB_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC3pDA7GTxOvNbXRGMi9QSIzQEI"
    "+EMD1HcUPJSQSFuRkZkWo4VQECuPRg/xVjqwX1yUrHUvGQJsBwTS/6LIcQiSwYsO"
    "qf+8TWxGQOJyW46gPPQVzTjNTiUoq435QB0v11lNxvKWBQIZLmacUZ2r1APta7i/M"
    "Y4Lx9XlZVMZNUdUywIDAQAB"
)
CDN_HEADERS = {
    "Origin": "https://fclive.pandacollege.cn",
    "Referer": "https://fclive.pandacollege.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _find_ffmpeg():
    """查找 ffmpeg 可执行文件路径"""
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    # Windows 常见安装位置
    for candidate in [r"D:\tools\ffmpeg\bin\ffmpeg.exe",
                      r"C:\tools\ffmpeg\bin\ffmpeg.exe"]:
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"  # fallback，让 FileNotFoundError 自然抛出


FFMPEG = _find_ffmpeg()


def safe_filename(title: str) -> str:
    return ILLEGAL_CHARS.sub("_", title).strip()


def load_config():
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(CFG_DIR / "credentials.yaml", encoding="utf-8") as f:
        creds = yaml.safe_load(f)
    return config, creds


def extract_short_link(url: str) -> str:
    """从回放 URL 提取 shortLink: ?param=xxx 或 /p/xxx 或 /playback/xxx"""
    m = re.search(r'[?&]param=([A-Za-z0-9]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'/p/([A-Za-z0-9]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'/playback/([A-Za-z0-9]+)', url)
    if m:
        return m.group(1)
    raise ValueError(f"无法从 URL 提取 shortLink: {url}")


def make_session(token: str) -> requests.Session:
    """创建带 Bearer token 认证的 session"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "source": "web",
    })
    return s


def get_invite_info(session, short_link: str) -> tuple[str, str]:
    """shortLink → inviteId, inviteUserId"""
    r = session.get(f"{PANDA_API}/live-student/getInviteMsg",
                    params={"shortLink": short_link}, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"getInviteMsg 失败: {d.get('msg')}")
    data = d["data"]
    log.info(f"inviteId={data['inviteId']}")
    return data["inviteId"], data["inviteUserId"]


def get_course_info(session, invite_id: str) -> dict:
    """inviteId → 课程信息（标题、videoId、thirdPartyId 等）"""
    r = session.post(f"{PANDA_API}/live-student/getCourse",
                     json={"inviteId": invite_id}, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"getCourse 失败: {d.get('msg')}")
    course = d["data"]
    log.info(f"课程: {course.get('name')}")
    log.info(f"  videoId={course.get('videoId')}, "
             f"thirdPartyId={course.get('thirdPartyId')}, "
             f"videoSource={course.get('videoSource')}, "
             f"transcodeStatus={course.get('transcodeStatus')}, "
             f"isAllowPlayBack={course.get('isAllowPlayBack')}")
    return course


def get_live_room(session, invite_id: str, invite_user_id: str) -> str:
    """inviteId → liveRoomId"""
    r = session.post(f"{PANDA_API}/live-student/getLiveRoom",
                     json={"inviteId": invite_id,
                           "inviteUserId": invite_user_id}, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"getLiveRoom 失败: {d.get('msg')}")
    room_id = str(d["data"]["liveRoomId"])
    log.info(f"liveRoomId={room_id}")
    return room_id


def get_video_sign(session, room_id: str) -> dict | None:
    """liveRoomId → {appId, fileId, sign(psign)} 或 None

    返回腾讯云 VOD 播放签名，用于构造 m3u8 地址。
    如果视频已删除或未转码完成，返回 None。
    """
    # 先检查视频是否可用
    r0 = session.get(f"{PANDA_API}/live-student/get-video-seek-sign",
                     params={"liveRoomId": room_id}, timeout=15)
    d0 = r0.json()
    if d0.get("code") != 0:
        msg = d0.get("msg", "")
        log.warning(f"视频不可用: {msg}")
        return None

    r = session.get(f"{PANDA_API}/live-student/getVideoSign",
                    params={"liveRoomId": room_id}, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        log.warning(f"getVideoSign 失败: {d.get('msg')} (code={d.get('code')})")
        return None
    log.info("获取到 VOD 播放签名")
    return d["data"]


def get_drm_playinfo(psign: str, file_id: str, app_id: int = VOD_APPID):
    """用 psign + 自生成 overlay key 调用 getplayinfo/v4，返回解密所需的全部信息

    Returns: (drm_url, drm_token, overlay_key, overlay_iv) 或 (None,...) 失败
    """
    # 1. 生成随机 overlay key/iv
    overlay_key = os.urandom(16).hex()
    overlay_iv = os.urandom(16).hex()

    # 2. RSA 加密
    pub_der = base64.b64decode(RSA_PUB_KEY_B64)
    pub_key = serialization.load_der_public_key(pub_der)
    enc_key = pub_key.encrypt(overlay_key.encode(), asym_padding.PKCS1v15()).hex()
    enc_iv = pub_key.encrypt(overlay_iv.encode(), asym_padding.PKCS1v15()).hex()

    # 3. 调用 getplayinfo/v4
    url = f"https://playvideo.qcloud.com/getplayinfo/v4/{app_id}/{file_id}"
    params = {
        "psign": psign,
        "cipheredOverlayKey": enc_key,
        "cipheredOverlayIv": enc_iv,
        "keyId": 1,
    }
    r = requests.get(url, params=params, headers=CDN_HEADERS, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        log.warning(f"getplayinfo 失败: code={d.get('code')}, {d.get('message')}")
        return None, None, None, None

    media = d.get("media", {})
    streaming = media.get("streamingInfo", {})
    drm_output = streaming.get("drmOutput", [])
    drm_token = streaming.get("drmToken", "")

    if not drm_output or not drm_token:
        log.warning("getplayinfo 返回无 drmOutput 或 drmToken")
        return None, None, None, None

    drm_url = drm_output[0].get("url", "")
    log.info(f"获取到 DRM 播放信息: type={drm_output[0].get('type')}")
    return drm_url, drm_token, overlay_key, overlay_iv


def get_real_aes_key(drm_url, drm_token, overlay_key, overlay_iv):
    """从 m3u8 获取 license → 解密得到真正的 AES key 和 IV

    Returns: (real_key_bytes, hls_iv_bytes, sub_m3u8_text, drm_url_base) 或 None
    """
    # 构造 master m3u8 URL
    parts = drm_url.split("/")
    parts[-1] = f"voddrm.token.{drm_token}.{parts[-1]}"
    master_url = "/".join(parts)

    r = requests.get(master_url, headers=CDN_HEADERS, timeout=15)
    if r.status_code != 200:
        log.warning(f"master m3u8 请求失败: {r.status_code}")
        return None

    # 选最高分辨率
    streams = re.findall(
        r"#EXT-X-STREAM-INF:.*?RESOLUTION=(\d+x\d+)\n(.+)", r.text
    )
    if not streams:
        log.warning("master m3u8 中无 STREAM-INF")
        return None
    best = streams[-1]
    log.info(f"可用分辨率: {[s[0] for s in streams]}，选择 {best[0]}")

    # 下载子 m3u8
    base_url = "/".join(master_url.split("/")[:-1])
    sub_url = f"{base_url}/{best[1].strip()}"
    r = requests.get(sub_url, headers=CDN_HEADERS, timeout=15)
    sub_text = r.text

    # 提取 license URL 和 IV
    key_m = re.search(r'#EXT-X-KEY:METHOD=AES-128,URI="([^"]+)"', sub_text)
    iv_m = re.search(r"IV=0x([0-9a-fA-F]+)", sub_text)
    if not key_m:
        log.warning("子 m3u8 中无 EXT-X-KEY")
        return None

    # 获取 base key（被 overlay 加密的）
    license_url = key_m.group(1)
    r = requests.get(license_url, headers=CDN_HEADERS, timeout=15)
    base_key = r.content
    if len(base_key) != 16:
        log.warning(f"license 返回异常: {len(base_key)} bytes")
        return None

    # AES-CBC 解密 base key → 真正的 key
    ok = bytes.fromhex(overlay_key)
    oiv = bytes.fromhex(overlay_iv)
    dec = Cipher(algorithms.AES(ok), modes.CBC(oiv)).decryptor()
    real_key = (dec.update(base_key) + dec.finalize())[:16]

    hls_iv_hex = iv_m.group(1) if iv_m else "00000000000000000000000000000000"
    hls_iv = bytes.fromhex(hls_iv_hex)

    log.info(f"AES key 解密成功, HLS IV={hls_iv_hex[:16]}...")

    # drm_url 的目录作为 ts 的 base URL
    drm_url_base = "/".join(drm_url.split("/")[:-1])
    return real_key, hls_iv, sub_text, drm_url_base


def download_drm_video(real_key, hls_iv, sub_m3u8_text, ts_base_url,
                       output_path):
    """下载并解密所有 ts 分片，用 ffmpeg 合并为最终视频。

    支持续传：中断后重新运行，已解密的分片会被跳过。
    临时目录仅在合并成功后清理。
    """
    if output_path.exists():
        log.info(f"视频已存在，跳过: {output_path}")
        return True

    # 提取所有 ts 分片 URL
    segments = re.findall(r"\n(\d+_\d+_\d+\.ts[^\n]*)", sub_m3u8_text)
    if not segments:
        log.warning("m3u8 中无 ts 分片")
        return False

    # 创建临时目录存放解密后的 ts
    tmp_dir = output_path.parent / f"_tmp_{output_path.stem}"
    tmp_dir.mkdir(exist_ok=True)

    # 统计已有分片（续传）
    existing = sum(1 for i in range(len(segments))
                   if (tmp_dir / f"{i:05d}.ts").exists())
    if existing > 0:
        log.info(f"续传: 已有 {existing}/{len(segments)} 个分片，"
                 f"继续下载剩余 {len(segments) - existing} 个...")
    else:
        log.info(f"共 {len(segments)} 个分片，开始下载解密...")

    # 下载并解密分片
    downloaded = 0
    for i, seg in enumerate(segments):
        ts_path = tmp_dir / f"{i:05d}.ts"
        if not ts_path.exists():
            ts_url = f"{ts_base_url}/{seg}"
            r = requests.get(ts_url, headers=CDN_HEADERS, timeout=60)
            r.raise_for_status()
            cipher = Cipher(algorithms.AES(real_key), modes.CBC(hls_iv))
            dec = cipher.decryptor()
            decrypted = dec.update(r.content) + dec.finalize()
            ts_path.write_bytes(decrypted)
            downloaded += 1

        if (i + 1) % 200 == 0:
            log.info(f"  进度: {i+1}/{len(segments)}")

    log.info(f"所有分片就绪（本次下载 {downloaded} 个），ffmpeg 合并中...")

    # 生成 concat 列表并合并
    concat_list = tmp_dir / "concat.txt"
    with open(concat_list, "w", encoding="utf-8") as flist:
        for i in range(len(segments)):
            ts_path = tmp_dir / f"{i:05d}.ts"
            flist.write(f"file '{ts_path.resolve().as_posix()}'\n")

    cmd = [
        FFMPEG, "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy", str(output_path), "-y",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    if result.returncode != 0:
        log.error(f"ffmpeg 合并失败: {result.stderr[-500:]}")
        return False

    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"视频下载完成: {output_path} ({size_mb:.1f} MB)")

    # 合并成功后才清理临时文件
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return True


def extract_audio(video_path, audio_path):
    """用 ffmpeg 从视频提取音频为 MP3"""
    if audio_path.exists():
        log.info(f"音频已存在，跳过: {audio_path}")
        return True
    if not video_path.exists():
        log.warning(f"视频不存在，无法提取音频: {video_path}")
        return False
    log.info(f"提取音频: {audio_path.name}")
    cmd = [
        FFMPEG, "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        str(audio_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    except FileNotFoundError:
        log.warning("ffmpeg 未安装或不在 PATH 中")
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg 提取音频失败: {result.stderr[-500:]}")
        return False
    size_mb = audio_path.stat().st_size / 1024 / 1024
    log.info(f"音频提取完成: {audio_path} ({size_mb:.1f} MB)")
    return True


def process_panda(task, config, creds):
    """处理熊猫学院回放任务"""
    source_url = task["source_url"]
    short_link = extract_short_link(source_url)
    log.info(f"处理熊猫学院回放: shortLink={short_link}")

    # 认证
    panda_token = creds.get("panda", {}).get("token", "")
    if not panda_token:
        log.error("credentials.yaml 中缺少 panda.token (JWT Bearer token)")
        return
    session = make_session(panda_token)

    # 1. 获取邀请信息
    invite_id, invite_user_id = get_invite_info(session, short_link)

    # 2. 获取课程信息
    course = get_course_info(session, invite_id)
    if not course.get("isAllowPlayBack"):
        log.warning("该课程不允许回放")
        return

    # 标题
    manual_title = task.get("title", "")
    title = manual_title or course.get("name", short_link)
    base_name = safe_filename(title)
    output_dir = PROJECT_DIR / config.get("output_dir", "output")
    output_dir.mkdir(exist_ok=True)
    log.info(f"输出基础名: {base_name}")

    # 3. 获取直播间 ID
    room_id = get_live_room(session, invite_id, invite_user_id)

    # 4. 获取 VOD 播放签名
    sign_data = get_video_sign(session, room_id)
    if not sign_data:
        log.warning("getVideoSign 失败，无法下载视频")
        return

    psign = sign_data.get("sign", "")
    file_id = sign_data.get("fileId", course.get("thirdPartyId", ""))
    app_id = sign_data.get("appId", VOD_APPID)

    # 5. 获取 DRM 播放信息（带自生成 overlay key）
    drm_url, drm_token, overlay_key, overlay_iv = get_drm_playinfo(
        psign, file_id, app_id
    )
    if not drm_url:
        log.warning("获取 DRM 播放信息失败")
        return

    # 6. 获取真正的 AES 解密 key
    key_info = get_real_aes_key(drm_url, drm_token, overlay_key, overlay_iv)
    if not key_info:
        log.warning("获取 AES 解密 key 失败")
        return
    real_key, hls_iv, sub_m3u8_text, ts_base_url = key_info

    # 7. 下载并解密视频
    video_path = output_dir / f"{base_name}.ts"
    download_drm_video(real_key, hls_iv, sub_m3u8_text, ts_base_url, video_path)

    # 8. 提取音频
    audio_path = output_dir / f"{base_name}.mp3"
    extract_audio(video_path, audio_path)

    log.info(f"熊猫学院处理完成: {base_name}")


def main():
    config, creds = load_config()
    tasks = config.get("tasks", [])
    panda_tasks = [t for t in tasks if t.get("source_type") == "panda"]
    if not panda_tasks:
        log.warning("config.yaml 中无 panda 类型任务")
        return

    for i, task in enumerate(panda_tasks):
        log.info(f"=== 熊猫学院任务 {i+1}/{len(panda_tasks)} ===")
        try:
            process_panda(task, config, creds)
        except Exception:
            log.exception(f"任务处理失败: {task.get('source_url')}")

    log.info("所有熊猫学院任务处理完成")


if __name__ == "__main__":
    main()
