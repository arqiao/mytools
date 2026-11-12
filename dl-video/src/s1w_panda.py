# 参数调用示例:
#   python src/s1w_panda.py
"""Step1-panda: 熊猫学院回放下载 — 视频 + 音频

用法:
  # 通过 input.yaml 配置任务（source_type: "panda"）
  python src/s1_huifang.py

  # 直接运行（读取 input.yaml 中 panda 类型任务）
  python src/s1w_panda.py

配置:
  cfg/input.yaml  — tasks 中 source_type: "panda", source_huifang_url: 熊猫学院回放页面 URL
  cfg/credentials.yaml — panda.token

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
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from modules.ffmpeg_utils import extract_audio, concat_ts, download_hls
from modules.config_utils import load_config, safe_filename

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
try:
    _input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
except yaml.YAMLError:
    _input_cfg = {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
OUTPUT_DIR = PROJECT_DIR / _input_cfg.get("path_output_dir", "output")
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


def extract_invite_id(url: str) -> str:
    """从回放 URL 提取 inviteId。"""
    m = re.search(r'[?&]inviteId=(\d+)', url)
    return m.group(1) if m else ""


def extract_file_id_from_drm_url(url: str) -> str:
    """从 voddrm.token 直链提取腾讯云 VOD fileId。

    优先解析 JWT payload 中的 fileId 字段；失败则从路径目录名取后 19 位
    （目录名 = 8 位随机前缀 + 19 位 fileId）。
    """
    # 1. 从 JWT payload 解析
    m = re.search(r'voddrm\.token\.([^~]+~[^~]+~[^.]+)\.', url)
    if m:
        jwt = m.group(1)
        parts = jwt.split("~")
        if len(parts) >= 2:
            try:
                import base64 as _b64
                payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
                payload = json.loads(_b64.urlsafe_b64decode(payload_b64))
                fid = payload.get("fileId", "")
                if fid:
                    return str(fid)
            except Exception:
                pass
    # 2. 从目录名（倒数第 3 段）取后 19 位
    segs = [s for s in url.split("/") if s]
    if len(segs) >= 3:
        dir_name = segs[-3]
        fid = re.search(r"(\d{19})$", dir_name)
        if fid:
            return fid.group(1)
    # 3. 从 JWT 原始文本再试一次（宽松匹配 fileId 字段）
    m = re.search(r'"fileId":"(\d+)"', url)
    if m:
        return m.group(1)
    raise ValueError(f"无法从 DRM URL 提取 fileId: {url[:80]}...")


def get_psign_for_file_id(session, file_id: str,
                          invite_ids: list[str] | None = None) -> str:
    """为指定 fileId 获取 VOD 播放签名 psign。

    通过 get-video-seek-sign 接口，lessonId 用已知有效课程 inviteId。
    优先使用传入的 invite_ids，否则尝试已知历史课程 inviteId。
    """
    candidates = list(invite_ids or []) + ["825066007985913860",
                                           "746507605215543298"]
    tried = set()
    for inv in candidates:
        if inv in tried:
            continue
        tried.add(inv)
        try:
            d = get_playback_seek_sign(session, inv, file_id)
        except Exception:
            continue
        if d and d.get("sign"):
            return d["sign"]
    raise RuntimeError(f"无法为 fileId={file_id} 获取 psign（已尝试 {len(tried)} 个 inviteId）")


def download_drm_direct(source_url: str, output_dir: Path, base_name: str,
                        creds: dict, task: dict | None = None) -> bool:
    """下载熊猫学院 voddrm.token DRM 直链（SimpleAES）。

    流程: fileId → psign(get-video-seek-sign) → getplayinfo/v4(自生成 overlay key)
          → license 解密 → 下载解密 ts → ffmpeg 合并 → 提取音频
    """
    file_id = extract_file_id_from_drm_url(source_url)
    log.info(f"DRM 直链 fileId: {file_id}")

    panda_token = resolve_panda_token(creds)
    if not panda_token:
        log.error("缺少熊猫学院 JWT Bearer token: 请配置 panda.token")
        return False
    session = make_session(panda_token)

    # 可选：任务中指定 inviteId
    invite_ids = []
    if task:
        inv = (task.get("inviteId") or task.get("panda_invite_id") or "")
        if inv:
            invite_ids.append(str(inv))
    psign = get_psign_for_file_id(session, file_id, invite_ids)
    log.info(f"psign 获取成功: {psign[:40]}...")

    drm_url, drm_token, overlay_key, overlay_iv = get_drm_playinfo(
        psign, file_id, VOD_APPID
    )
    if not drm_url:
        log.error("getplayinfo 失败")
        return False

    key_info = get_real_aes_key(drm_url, drm_token, overlay_key, overlay_iv)
    if not key_info:
        log.error("license 解密失败")
        return False
    real_key, hls_iv, sub_text, ts_base = key_info
    log.info(f"真实 AES key: {real_key.hex()}")

    video_path = output_dir / f"{base_name}.mp4"
    if not download_drm_video(real_key, hls_iv, sub_text, ts_base, video_path):
        log.error("视频下载失败")
        return False
    extract_audio(video_path, output_dir / f"{base_name}.m4a")
    log.info(f"熊猫学院 DRM 直链处理完成: {base_name}")
    return True


def resolve_panda_url(url: str) -> str:
    """解析 /s/ 分享短链跳转，得到实际 playback URL。"""
    if "/s/" not in url:
        return url
    r = requests.get(url, headers=CDN_HEADERS, allow_redirects=False, timeout=15)
    location = r.headers.get("Location", "")
    if not location:
        return url
    resolved = urljoin(url, location)
    log.info(f"解析 Panda 分享短链: {resolved}")
    return resolved


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


def resolve_panda_token(creds: dict) -> str:
    """从 credentials.yaml 或环境变量读取熊猫学院 Bearer token"""
    return (creds.get("panda", {}).get("token") or os.environ.get("PANDA_TOKEN") or "").strip()


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


def get_playback_seek_sign(session, lesson_id: str, file_id: str) -> dict | None:
    """按网页回放页参数获取腾讯云 VOD 播放签名"""
    r = session.get(f"{PANDA_API}/live-student/get-video-seek-sign",
                    params={"lessonId": lesson_id, "platform": 1, "vid": file_id},
                    timeout=15)
    d = r.json()
    if d.get("code") != 0:
        log.warning(f"get-video-seek-sign 失败: {d.get('msg')} (code={d.get('code')})")
        return None
    log.info("按网页回放参数获取到 VOD 播放签名")
    return d["data"]


def get_plain_m3u8_from_playinfo(sign_data: dict) -> str:
    """用网页回放签名解析普通 HLS m3u8 地址"""
    app_id = sign_data.get("appId")
    file_id = sign_data.get("vid") or sign_data.get("fileId")
    psign = sign_data.get("sign", "")
    if not app_id or not file_id or not psign:
        return ""

    url = f"https://playvideo.qcloud.com/getplayinfo/v4/{app_id}/{file_id}"
    r = requests.get(url, params={"psign": psign}, headers=CDN_HEADERS, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        log.warning(f"getplayinfo 普通 HLS 解析失败: code={d.get('code')}, {d.get('message')}")
        return ""

    media = d.get("media") or {}
    original_url = (media.get("originalInfo") or {}).get("url", "")
    if original_url:
        log.info("从 getplayinfo 解析到 originalInfo m3u8")
        return original_url

    transcode_infos = (media.get("transcodeInfo") or {}).get("transcodeList") or []
    for item in reversed(transcode_infos):
        item_url = item.get("url", "")
        if item_url:
            log.info("从 getplayinfo 解析到 transcodeInfo m3u8")
            return item_url
    return ""


def download_plain_hls(m3u8_url: str, output_dir: Path, base_name: str) -> bool:
    """下载普通 HLS 并提取音频"""
    video_path = output_dir / f"{base_name}.mp4"
    headers = (
        "Referer: https://fclive.pandacollege.cn/\r\n"
        "Origin: https://fclive.pandacollege.cn\r\n"
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
    )
    if not download_hls(m3u8_url, video_path, headers=headers):
        log.error("视频下载失败")
        return False
    extract_audio(video_path, output_dir / f"{base_name}.m4a")
    log.info(f"熊猫学院处理完成: {base_name}")
    return True


def download_direct_hls(m3u8_url: str, output_dir: Path, base_name: str) -> bool:
    """下载熊猫学院 HLS 直链，支持 master m3u8 和 AES-128 key 本地改写。"""
    r = requests.get(m3u8_url, headers=CDN_HEADERS, timeout=30)
    r.raise_for_status()
    m3u8_text = r.text
    effective_url = m3u8_url

    streams = re.findall(r"#EXT-X-STREAM-INF:.*?\n([^#\n]+)", m3u8_text)
    if streams:
        effective_url = urljoin(m3u8_url, streams[-1].strip())
        log.info("检测到 master m3u8，选择最高码率子流")
        r = requests.get(effective_url, headers=CDN_HEADERS, timeout=30)
        r.raise_for_status()
        m3u8_text = r.text

    if "#EXT-X-KEY" not in m3u8_text:
        return download_plain_hls(effective_url, output_dir, base_name)

    video_path = output_dir / f"{base_name}.mp4"
    with tempfile.TemporaryDirectory(prefix="panda_hls_") as tmp:
        tmp_dir = Path(tmp)
        key_path = tmp_dir / "key.bin"
        local_m3u8 = tmp_dir / "playlist.m3u8"

        key_m = re.search(r'URI="([^"]+)"', m3u8_text)
        if not key_m:
            log.warning("m3u8 中有 EXT-X-KEY，但未找到 key URI")
            return False
        key_url = urljoin(effective_url, key_m.group(1))
        key_resp = requests.get(key_url, headers=CDN_HEADERS, timeout=30)
        key_resp.raise_for_status()
        key_path.write_bytes(key_resp.content)
        log.info(f"已保存 HLS key: {len(key_resp.content)} bytes")

        rewritten = []
        for line in m3u8_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#EXT-X-KEY"):
                rewritten.append(re.sub(r'URI="[^"]+"', f'URI="{key_path.as_posix()}"', line))
            elif stripped and not stripped.startswith("#"):
                rewritten.append(urljoin(effective_url, stripped))
            else:
                rewritten.append(line)
        local_m3u8.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

        ok = download_hls(
            local_m3u8.as_posix(), video_path,
            headers=(
                "Referer: https://fclive.pandacollege.cn/\r\n"
                "Origin: https://fclive.pandacollege.cn\r\n"
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
            ),
            allowed_extensions_all=True,
            protocol_whitelist="file,http,https,tcp,tls,crypto",
            extension_picky=False,
        )
    if not ok:
        log.error("视频下载失败")
        return False
    extract_audio(video_path, output_dir / f"{base_name}.m4a")
    log.info(f"熊猫学院处理完成: {base_name}")
    return True


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
            for attempt in range(5):
                try:
                    r = requests.get(ts_url, headers=CDN_HEADERS, timeout=60)
                    r.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 4:
                        raise
                    time.sleep(2 * (attempt + 1))
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

    if not concat_ts(concat_list, output_path):
        return False

    # 合并成功后才清理临时文件
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return True


def process_panda(task, config, creds):
    """处理熊猫学院回放任务"""
    source_url = resolve_panda_url(task["source_huifang_url"])
    output_dir = PROJECT_DIR / config.get("path_output_dir", "output").rstrip("/")
    output_dir.mkdir(exist_ok=True)

    if ".m3u8" in source_url:
        if "voddrm.token" in source_url:
            # SimpleAES DRM 直链：需完整 DRM 解密流程
            base_name = safe_filename(config.get("titleShougong", "") or task.get("title", "") or f"panda_{extract_file_id_from_drm_url(source_url)}")
            log.info(f"处理熊猫学院 DRM 直链: {base_name}")
            download_drm_direct(source_url, output_dir, base_name, creds, task)
            return
        file_id = source_url.split("?", 1)[0].rstrip("/").split("/")[-2]
        base_name = safe_filename(config.get("titleShougong", "") or task.get("title", "") or f"panda_{file_id}")
        log.info(f"处理熊猫学院 m3u8 直链: {base_name}")
        download_direct_hls(source_url, output_dir, base_name)
        return

    invite_id = extract_invite_id(source_url)
    if invite_id:
        log.info(f"处理熊猫学院回放: inviteId={invite_id}")

        panda_token = resolve_panda_token(creds)
        if not panda_token:
            log.error("缺少熊猫学院 JWT Bearer token: 请配置 credentials.yaml 的 panda.token，或设置环境变量 PANDA_TOKEN")
            return
        session = make_session(panda_token)

        course = get_course_info(session, invite_id)
        if not course.get("isAllowPlayBack"):
            log.warning("该课程不允许回放")
            return

        manual_title = config.get("titleShougong", "") or task.get("title", "")
        title = manual_title or course.get("name", invite_id)
        base_name = safe_filename(title)
        log.info(f"输出基础名: {base_name}")

        web_sign_data = get_playback_seek_sign(
            session, invite_id, course.get("thirdPartyId", "")
        )
        m3u8_url = get_plain_m3u8_from_playinfo(web_sign_data or {})
        if m3u8_url:
            download_plain_hls(m3u8_url, output_dir, base_name)
            return

        if web_sign_data:
            log.warning("网页回放无普通 m3u8，改用网页回放签名走 DRM 解密")
            psign = web_sign_data.get("sign", "")
            file_id = web_sign_data.get("vid") or web_sign_data.get("fileId") or course.get("thirdPartyId", "")
            app_id = web_sign_data.get("appId", VOD_APPID)
            drm_url, drm_token, overlay_key, overlay_iv = get_drm_playinfo(
                psign, file_id, app_id
            )
            if drm_url:
                key_info = get_real_aes_key(drm_url, drm_token, overlay_key, overlay_iv)
                if key_info:
                    real_key, hls_iv, sub_m3u8_text, ts_base_url = key_info
                    video_path = output_dir / f"{base_name}.ts"
                    if download_drm_video(real_key, hls_iv, sub_m3u8_text, ts_base_url, video_path):
                        extract_audio(video_path, output_dir / f"{base_name}.m4a")
                        log.info(f"熊猫学院处理完成: {base_name}")
                return

        log.warning("未能从 inviteId 自动解析 m3u8，无法下载视频")
        return

    short_link = extract_short_link(source_url)
    log.info(f"处理熊猫学院回放: shortLink={short_link}")

    # 认证
    panda_token = resolve_panda_token(creds)
    if not panda_token:
        log.error("缺少熊猫学院 JWT Bearer token: 请配置 credentials.yaml 的 panda.token，或设置环境变量 PANDA_TOKEN")
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
    manual_title = config.get("titleShougong", "") or task.get("title", "")
    title = manual_title or course.get("name", short_link)
    base_name = safe_filename(title)
    log.info(f"输出基础名: {base_name}")

    # 3. 获取直播间 ID
    room_id = get_live_room(session, invite_id, invite_user_id)

    # 4. 获取 VOD 播放签名
    sign_data = get_video_sign(session, room_id)
    if not sign_data:
        web_sign_data = get_playback_seek_sign(
            session, invite_id, course.get("thirdPartyId", "")
        )
        m3u8_url = get_plain_m3u8_from_playinfo(web_sign_data or {})
        if m3u8_url:
            log.warning("getVideoSign 失败，改用网页回放方式解析出的 m3u8 下载")
            download_plain_hls(m3u8_url, output_dir, base_name)
            return

        if web_sign_data:
            log.warning("getVideoSign 失败，改用网页回放签名走 DRM 解密")
            psign = web_sign_data.get("sign", "")
            file_id = web_sign_data.get("vid") or web_sign_data.get("fileId") or course.get("thirdPartyId", "")
            app_id = web_sign_data.get("appId", VOD_APPID)
            drm_url, drm_token, overlay_key, overlay_iv = get_drm_playinfo(
                psign, file_id, app_id
            )
            if drm_url:
                key_info = get_real_aes_key(drm_url, drm_token, overlay_key, overlay_iv)
                if key_info:
                    real_key, hls_iv, sub_m3u8_text, ts_base_url = key_info
                    video_path = output_dir / f"{base_name}.ts"
                    if download_drm_video(real_key, hls_iv, sub_m3u8_text, ts_base_url, video_path):
                        extract_audio(video_path, output_dir / f"{base_name}.m4a")
                        log.info(f"熊猫学院处理完成: {base_name}")
                    return

        if not m3u8_url:
            m3u8_url = task.get("source_m3u8_url", "")
        if not m3u8_url:
            log.warning("getVideoSign 失败，也未能自动解析 m3u8，无法下载视频")
            return
        log.warning("getVideoSign 失败，改用网页回放方式解析出的 m3u8 下载")
        download_plain_hls(m3u8_url, output_dir, base_name)
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
    audio_path = output_dir / f"{base_name}.m4a"
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
            log.exception(f"任务处理失败: {task.get('source_huifang_url')}")

    log.info("所有熊猫学院任务处理完成")


if __name__ == "__main__":
    main()
