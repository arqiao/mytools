"""Step1-panda: 熊猫学院回放下载 — 视频 + 音频

API 流程:
  shortLink → getInviteMsg → inviteId
  inviteId  → getCourse    → 标题, thirdPartyId(VOD fileId), videoSource
  inviteId  → getLiveRoom  → liveRoomId
  liveRoomId→ getVideoSign → appId, fileId, psign (DRM token)
  psign     → 腾讯云 VOD m3u8 → ffmpeg 下载

已知限制:
  - 视频使用腾讯云 SimpleAES DRM (cipheredOverlayKey)
  - ffmpeg/yt-dlp 无法直接解密 overlay 加密的 HLS
  - 当前仅支持拿到 m3u8 地址，实际下载需要 DRM 方案突破或无 DRM 源
"""

import json
import logging
import re
import subprocess
from pathlib import Path

import requests
import yaml

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
        logging.FileHandler(LOG_DIR / "s1_panda.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

PANDA_API = "https://fclive.pandacollege.cn/api"
VOD_APPID = 1254019786  # 腾讯云 VOD appId（从 ts URL 路径确认）
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')


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


def build_m3u8_url(psign: str, file_id: str, app_id: int = VOD_APPID) -> str:
    """用 psign 构造腾讯云 VOD 的 getplayinfo 请求，获取 m3u8 地址

    TODO: 当 getVideoSign 可用时，从返回的 psign 构造播放地址。
    目前的 DRM m3u8 URL 格式（从浏览器抓包确认）:
      https://video.gzfeice.cn/{bucket}/{dir}{fileId}/
        voddrm.token.{jwt_header}~{jwt_payload}~{jwt_sig}
        .adp.{streamId}.m3u8?encdomain=cmv1&sign=xxx&t=xxx
    """
    # 尝试腾讯云 VOD v4 API
    url = f"https://playvideo.qcloud.com/getplayinfo/v4/{app_id}/{file_id}"
    params = {"psign": psign} if psign else {}
    r = requests.get(url, params=params, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        log.warning(f"VOD getplayinfo 失败: {d.get('message')}")
        return ""
    # 从返回的 streamingData 中提取 m3u8 URL
    # TODO: 解析实际返回格式
    return d.get("streamingData", {}).get("hlsUrl", "")


def download_m3u8_video(m3u8_url, output_path):
    """用 ffmpeg 下载 HLS 视频（选最高分辨率）"""
    if output_path.exists():
        log.info(f"视频已存在，跳过: {output_path}")
        return True
    if not m3u8_url:
        log.warning("无 m3u8 URL，跳过视频下载")
        return False
    log.info(f"下载视频: {output_path.name}")
    cmd = [
        "ffmpeg",
        "-headers", "Referer: https://fclive.pandacollege.cn/\r\n",
        "-i", m3u8_url,
        "-map", "0:p:2",  # 选第3个流（1080p，最高）
        "-c", "copy",
        str(output_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=3600)
    except FileNotFoundError:
        log.warning("ffmpeg 未安装或不在 PATH 中")
        return False
    except subprocess.TimeoutExpired:
        log.error("ffmpeg 下载超时（1小时）")
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg 下载失败: {result.stderr[-500:]}")
        return False
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"视频下载完成: {output_path} ({size_mb:.1f} MB)")
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
        "ffmpeg", "-i", str(video_path),
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
    if sign_data:
        psign = sign_data.get("sign", "")
        file_id = sign_data.get("fileId", course.get("thirdPartyId", ""))
        app_id = sign_data.get("appId", VOD_APPID)
        m3u8_url = build_m3u8_url(psign, file_id, app_id)
    else:
        log.warning("getVideoSign 失败，尝试用 thirdPartyId 直接构造")
        m3u8_url = ""

    # 5. 下载视频
    video_path = output_dir / f"{base_name}.ts"
    if m3u8_url:
        download_m3u8_video(m3u8_url, video_path)
    else:
        log.warning("无法获取 m3u8 地址，跳过视频下载。"
                    "可能原因: 视频已删除、DRM 限制、或 getVideoSign 参数不正确")

    # 6. 提取音频
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
