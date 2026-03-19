"""Step1-taobao: 淘宝直播（螳螂直播）回放下载 — 视频 + 音频

API 流程:
  URL → 提取 linkCode
  linkParam/getParamByCode → companyId, courseId, unionId
  selectCourseInfo → 标题, liveId, liveVendor, liveMode
  agora/live/video → m3u8 URL (3条CDN线路)
  ffmpeg → 下载 .ts + 提取 .mp3
"""

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
        logging.FileHandler(LOG_DIR / "s1w_taobao.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')


def _find_ffmpeg():
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in [r"D:\tools\ffmpeg\bin\ffmpeg.exe",
                      r"C:\tools\ffmpeg\bin\ffmpeg.exe"]:
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"


FFMPEG = _find_ffmpeg()


def safe_filename(title: str) -> str:
    return ILLEGAL_CHARS.sub("_", title).strip()


def load_config():
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(CFG_DIR / "credentials.yaml", encoding="utf-8") as f:
        creds = yaml.safe_load(f)
    return config, creds


def parse_taobao_url(source_url: str) -> tuple[str, str]:
    """从 URL 提取 companyId 和 linkCode（原始路径段，含可能的 c 前缀）"""
    m = re.match(r"https?://(\d+)\.tbkflow\.cn/pcLive/([0-9a-fA-F]+)", source_url)
    if not m:
        raise ValueError(f"无法解析淘宝直播 URL: {source_url}")
    return m.group(1), m.group(2)


def make_session(creds_taobao: dict) -> requests.Session:
    """创建带认证头的 session"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {creds_taobao['bearer_token']}",
        "X-AuthorizationAccess": f"Bearer {creds_taobao['access_token']}",
        "cid": creds_taobao["company_id"],
        "scene": "browser",
        "Origin": f"https://{creds_taobao['company_id']}.tbkflow.cn",
        "Referer": f"https://{creds_taobao['company_id']}.tbkflow.cn/",
    })
    return s


def get_link_params(session, api_base, link_code):
    """linkParam/getParamByCode → 解析压缩参数（自动尝试原始和去 c 前缀两种）"""
    url = f"{api_base}/scrm-course-api/pass/linkParam/getParamByCode"
    # 有些 URL 带 c 前缀，有些不带；API 端两种都可能有效
    candidates = [link_code]
    if link_code.startswith("c"):
        candidates.append(link_code[1:])
    else:
        candidates.append(f"c{link_code}")
    for code in candidates:
        resp = session.post(url, json={"linkCode": code}, timeout=15)
        data = resp.json()
        if data.get("code") == 200 and data.get("data"):
            params = data["data"].get("params", data["data"])
            if params and params.get("id"):
                log.info(f"linkParam 解析: companyId={params.get('companyId')}, "
                         f"id={params.get('id')}, liveId={params.get('liveId')}")
                return params
    raise RuntimeError(f"getParamByCode 失败 (tried {candidates}): {data}")


def get_course_info(session, api_base, company_id, union_id, course_id):
    """selectCourseInfo → 课程详情（标题, liveId, liveMode 等）"""
    url = f"{api_base}/scrm-course-api/pass/selectCourseInfo"
    body = {
        "companyId": company_id,
        "unionId": union_id,
        "id": course_id,
        "flag": "CAMP-COURSE",
    }
    resp = session.post(url, json=body, timeout=15)
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"selectCourseInfo 失败: {data}")
    info = data["data"]
    title = info.get("name", "")
    live_id = info.get("liveNum") or info.get("courseId") or info.get("id")
    camp_period_id = info.get("campPeriodId", "")
    live_vendor = info.get("liveVendor", "")
    live_mode = info.get("liveMode", "")
    live_status = info.get("liveStatus", "")
    log.info(f"课程: {title}, liveId={live_id}, vendor={live_vendor}, "
             f"mode={live_mode}, status={live_status}")
    return {
        "title": title, "live_id": live_id,
        "camp_period_id": camp_period_id,
        "live_vendor": live_vendor, "live_mode": live_mode,
    }


def get_replay_url(session, api_base, live_id):
    """agora/live/video → m3u8 URL（选 domain 线路）"""
    url = f"{api_base}/micor-live-guest/agora/live/video"
    resp = session.post(url, json={"liveId": live_id, "mediaType": "PC"}, timeout=15)
    data = resp.json()
    if data.get("code") != 200:
        resp = session.post(url, json={"liveId": live_id}, timeout=15)
        data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"agora/live/video 失败: {data}")
    videos = data["data"]
    if not videos:
        raise RuntimeError("agora/live/video 返回空列表")
    video = videos[0]
    m3u8 = video.get("domain") or video.get("volcanoUrl") or video.get("huaweiUrl", "")
    thumb = video.get("thumbUrl", "")
    if not m3u8:
        raise RuntimeError(f"未获取到 m3u8 URL: {video}")
    log.info(f"m3u8 URL: {m3u8[:100]}...")
    return m3u8, thumb


def download_video(m3u8_url, output_path):
    """ffmpeg 下载 HLS 视频（无 DRM，直接下载）"""
    if output_path.exists() and output_path.stat().st_size > 1024:
        log.info(f"视频已存在，跳过: {output_path}")
        return True
    log.info(f"下载视频: {output_path.name}")
    cmd = [
        FFMPEG, "-i", m3u8_url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        str(output_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=1800)
    except FileNotFoundError:
        log.error("ffmpeg 未安装或不在 PATH 中")
        return False
    except subprocess.TimeoutExpired:
        log.error("ffmpeg 下载超时（30分钟）")
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg 下载失败: {result.stderr[-500:]}")
        return False
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"视频下载完成: {output_path} ({size_mb:.1f} MB)")
    return True


def extract_audio(video_path, audio_path):
    """ffmpeg 从视频提取音频为 MP3"""
    if audio_path.exists():
        log.info(f"音频已存在，跳过: {audio_path}")
        return True
    if not video_path.exists():
        log.warning(f"视频不存在，无法提取音频: {video_path}")
        return False
    log.info(f"提取音频: {audio_path.name}")
    cmd = [
        FFMPEG, "-err_detect", "ignore_err",
        "-i", str(video_path),
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


def process_taobao(task, config, creds):
    """处理淘宝直播回放任务（入口）"""
    source_url = task["source_url"]
    company_id_from_url, link_code = parse_taobao_url(source_url)
    log.info(f"处理淘宝直播: companyId={company_id_from_url}, linkCode={link_code}")

    # 认证
    taobao_creds = creds.get("taobao", {})
    if not taobao_creds.get("bearer_token"):
        log.error("credentials.yaml 中缺少 taobao.bearer_token")
        return
    api_base = taobao_creds.get("api_base", "https://cg.infyrasys.cn")
    session = make_session(taobao_creds)

    # 1. 解析 linkCode → 参数
    params = get_link_params(session, api_base, link_code)
    company_id = params.get("companyId", company_id_from_url)
    course_id = params.get("id", "")
    union_id = params.get("unionId") or taobao_creds.get("union_id", "")
    live_id = params.get("liveId", "")

    # 2. 获取课程信息（标题等）
    course = get_course_info(session, api_base, company_id, union_id, course_id)
    title = task.get("title") or course["title"] or f"taobao_{link_code}"
    base_name = safe_filename(title)
    live_id = live_id or course["live_id"]
    log.info(f"标题: {title}, 文件名: {base_name}, liveId: {live_id}")
    m3u8_url, thumb_url = get_replay_url(session, api_base, live_id)

    # 4. 下载视频（mp4 格式，ts 格式音频流有兼容性问题）
    video_path = OUTPUT_DIR / f"{base_name}.mp4"
    if not download_video(m3u8_url, video_path):
        log.error("视频下载失败")
        return

    # 5. 提取音频
    audio_path = OUTPUT_DIR / f"{base_name}.mp3"
    extract_audio(video_path, audio_path)

    log.info(f"淘宝直播处理完成: {base_name}")
