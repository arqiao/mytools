"""Step1: 飞书妙记下载 — 视频 + 音频 + 字幕/文字记录"""

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
        logging.FileHandler(LOG_DIR / "s1_huifang.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"
# Windows 非法文件名字符
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')


def load_config():
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(CFG_DIR / "credentials.yaml", encoding="utf-8") as f:
        creds = yaml.safe_load(f)
    return config, creds


def safe_filename(title: str) -> str:
    """替换 Windows 非法字符为下划线"""
    return ILLEGAL_CHARS.sub("_", title).strip()


def ensure_feishu_token(creds, session):
    """确保飞书 user_access_token 有效，过期则刷新"""
    import time
    expire = creds["feishu"].get("user_token_expire_time", 0)
    if time.time() < expire - 300:
        return creds["feishu"]["user_access_token"]

    # 先获取 app_access_token
    app_resp = session.post(
        f"{FEISHU_BASE}/auth/v3/app_access_token/internal",
        json={
            "app_id": creds["feishu"]["app_id"],
            "app_secret": creds["feishu"]["app_secret"],
        },
        timeout=15,
    )
    app_token = app_resp.json().get("app_access_token", "")

    # 刷新 user token
    resp = session.post(
        f"{FEISHU_BASE}/authen/v1/oidc/refresh_access_token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": creds["feishu"]["user_refresh_token"],
        },
        headers={"Authorization": f"Bearer {app_token}"},
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书 token 刷新失败: {data}")

    td = data["data"]
    creds["feishu"]["user_access_token"] = td["access_token"]
    creds["feishu"]["user_refresh_token"] = td["refresh_token"]
    creds["feishu"]["user_token_expire_time"] = int(time.time()) + td["expires_in"]

    with open(CFG_DIR / "credentials.yaml", "w", encoding="utf-8") as f:
        yaml.dump(creds, f, allow_unicode=True)
    log.info("飞书 token 已刷新")
    return td["access_token"]


def extract_minutes_token(url: str) -> str:
    """从妙记 URL 提取 object_token"""
    m = re.search(r"/minutes/([A-Za-z0-9]+)", url)
    if not m:
        raise ValueError(f"无法从 URL 提取妙记 token: {url}")
    return m.group(1)


def get_minutes_info(token, user_token, session):
    """Open API 获取妙记基本信息（标题、时长）"""
    url = f"{FEISHU_BASE}/minutes/v1/minutes/{token}"
    headers = {"Authorization": f"Bearer {user_token}"}
    resp = session.get(url, headers=headers, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取妙记信息失败: {data}")
    minute = data["data"]["minute"]
    title = minute.get("title", token)
    duration = minute.get("duration", "")
    log.info(f"妙记标题: {title}, 时长: {duration}")
    return title, duration


def get_minutes_media(token, cookie, session, source_url):
    """从妙记页面 HTML 解析标题、视频/音频 URL（SSR 数据）"""
    # 从 source_url 提取域名
    m = re.match(r"(https://[^/]+)", source_url)
    base_domain = m.group(1) if m else "https://waytoagi.feishu.cn"

    page_url = f"{base_domain}/minutes/{token}"
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    resp = session.get(page_url, headers=headers, timeout=30)
    if resp.status_code != 200:
        log.warning(f"妙记页面请求失败: status={resp.status_code}")
        return None, None, None

    # 用正则替换 \uXXXX 转义为实际字符，避免 unicode_escape 破坏中文
    def decode_unicode_escapes(text):
        return re.sub(
            r'\\u([0-9a-fA-F]{4})',
            lambda m: chr(int(m.group(1), 16)),
            text,
        )

    decoded = decode_unicode_escapes(resp.text)
    video_url = ""
    vtt_url = ""
    title = ""

    # 提取 video_url
    vm = re.search(r'"video_url":"([^"]+)"', decoded)
    if vm:
        video_url = vm.group(1)

    # 提取 web_vtt_url（字幕）
    wm = re.search(r'"web_vtt_url":"([^"]+)"', decoded)
    if wm:
        vtt_url = wm.group(1)

    # 提取标题
    title_match = re.search(r'"topic":"([^"]+)"', decoded)
    if title_match:
        title = title_match.group(1)
    if not title:
        title_match = re.search(r'<title>([^<]+)</title>', resp.text)
        if title_match:
            title = title_match.group(1).replace(" - 飞书妙记", "").strip()

    log.info(f"页面标题: {title}")
    log.info(f"video_url: {'有' if video_url else '无'}, "
             f"vtt_url: {'有' if vtt_url else '无'}")
    return title, video_url, vtt_url


def download_video(video_url, output_path, cookie):
    """下载视频（飞书内部流式 API，需要 cookie 认证）"""
    if not video_url:
        log.warning("无视频 URL，跳过视频下载")
        return False
    if output_path.exists():
        log.info(f"视频已存在，跳过: {output_path}")
        return True
    log.info(f"下载视频: {output_path.name}")
    headers = {
        "Cookie": cookie,
        "Referer": "https://waytoagi.feishu.cn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Range": "bytes=0-",
    }
    resp = requests.get(video_url, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0 and downloaded % (5 * 1024 * 1024) < 65536:
                log.info(f"  下载进度: {downloaded / 1024 / 1024:.1f} / "
                         f"{total / 1024 / 1024:.1f} MB")
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"视频下载完成: {output_path} ({size_mb:.1f} MB)")
    return True


def download_vtt_subtitle(vtt_url, output_dir, base_name, cookie):
    """下载 WebVTT 字幕并转换为 SRT 格式"""
    if not vtt_url:
        log.warning("无 VTT 字幕 URL，跳过字幕下载")
        return False
    srt_path = output_dir / f"{base_name}_ori.srt"
    if srt_path.exists():
        log.info(f"字幕已存在，跳过: {srt_path}")
        return True
    log.info(f"下载 VTT 字幕: {vtt_url[:80]}...")
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": vtt_url.split("/minutes/")[0] + "/",
    }
    resp = requests.get(vtt_url, headers=headers, timeout=30)
    resp.raise_for_status()
    vtt_text = resp.text
    # VTT → SRT 转换
    srt_text = vtt_to_srt(vtt_text)
    srt_path.write_text(srt_text, encoding="utf-8")
    log.info(f"字幕已保存: {srt_path}")
    return True


def vtt_to_srt(vtt_text):
    """将 WebVTT 格式转换为 SRT 格式"""
    lines = vtt_text.strip().splitlines()
    srt_lines = []
    idx = 0
    i = 0
    # 跳过 WEBVTT 头部
    while i < len(lines) and not re.match(r'\d{2}:\d{2}', lines[i]):
        i += 1
    while i < len(lines):
        line = lines[i].strip()
        # 匹配时间行: 00:00:00.000 --> 00:00:05.000
        m = re.match(r'(\d{2}:\d{2}:\d{2})\.(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2})\.(\d{3})', line)
        if not m:
            # 也匹配 MM:SS.mmm 格式（无小时）
            m = re.match(r'(\d{2}:\d{2})\.(\d{3})\s*-->\s*(\d{2}:\d{2})\.(\d{3})', line)
            if m:
                start_t = f"00:{m.group(1)},{m.group(2)}"
                end_t = f"00:{m.group(3)},{m.group(4)}"
            else:
                i += 1
                continue
        else:
            start_t = f"{m.group(1)},{m.group(2)}"
            end_t = f"{m.group(3)},{m.group(4)}"
        idx += 1
        # 收集字幕文本
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        srt_lines.append(str(idx))
        srt_lines.append(f"{start_t} --> {end_t}")
        srt_lines.extend(text_lines)
        srt_lines.append("")
        i += 1
    return "\n".join(srt_lines)


def extract_audio_from_video(video_path, audio_path):
    """用 ffmpeg 从视频中提取音频为 MP3"""
    if audio_path.exists():
        log.info(f"音频已存在，跳过: {audio_path}")
        return True
    if not video_path.exists():
        log.warning(f"视频文件不存在，无法提取音频: {video_path}")
        return False
    log.info(f"从视频提取音频: {audio_path.name}")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        str(audio_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    except FileNotFoundError:
        log.warning("ffmpeg 未安装或不在 PATH 中，跳过音频提取。"
                    "请安装 ffmpeg 后重新运行")
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg 提取音频失败: {result.stderr[:500]}")
        return False
    size_mb = audio_path.stat().st_size / 1024 / 1024
    log.info(f"音频提取完成: {audio_path} ({size_mb:.1f} MB)")
    return True


def get_transcript(token, user_token, session, output_dir, base_name):
    """Open API 获取文字记录，输出 _ori.md（不覆盖已有 _ori.srt）"""
    url = f"{FEISHU_BASE}/minutes/v1/minutes/{token}/transcript"
    headers = {"Authorization": f"Bearer {user_token}"}
    resp = session.get(url, headers=headers, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        log.warning(f"获取文字记录失败(可能需要权限): code={data.get('code')}, "
                    f"msg={data.get('msg', '')}")
        return False

    paragraphs = data.get("data", {}).get("paragraphs", [])
    if not paragraphs:
        log.warning("文字记录为空")
        return False

    # 生成 _ori.md（带说话人的文字记录）
    md_lines = []
    for p in paragraphs:
        speaker = p.get("speaker", {}).get("user_name", "")
        text = p.get("text", "")
        md_lines.append(f"**{speaker}**: {text}")

    md_path = output_dir / f"{base_name}_ori.md"
    md_path.write_text("\n\n".join(md_lines), encoding="utf-8")
    log.info(f"文字记录已保存: {md_path}")

    # 仅在没有 _ori.srt 时才从 transcript 生成 SRT
    srt_path = output_dir / f"{base_name}_ori.srt"
    if not srt_path.exists():
        srt_lines = []
        for i, p in enumerate(paragraphs, 1):
            text = p.get("text", "")
            start_ms = p.get("start_time", 0)
            end_ms = p.get("end_time", 0)
            srt_lines.append(str(i))
            srt_lines.append(f"{ms_to_srt(start_ms)} --> {ms_to_srt(end_ms)}")
            srt_lines.append(text)
            srt_lines.append("")
        srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
        log.info(f"原始字幕已保存: {srt_path}")
    else:
        log.info("_ori.srt 已存在（VTT 字幕），跳过 transcript SRT 生成")
    return True


def ms_to_srt(ms):
    """毫秒转 SRT 时间格式"""
    if isinstance(ms, str):
        ms = int(ms)
    s, ms_rem = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_rem:03d}"


def process_feishu_minutes(task, config, creds):
    """处理飞书妙记任务"""
    session = requests.Session()
    source_url = task["source_url"]
    token = extract_minutes_token(source_url)
    log.info(f"处理飞书妙记: token={token}")

    cookie = creds["feishu"].get("browser_cookie", "")

    # 从页面 HTML 获取标题和媒体 URL（不依赖 Open API）
    page_title, video_url, vtt_url = get_minutes_media(
        token, cookie, session, source_url
    )

    # 标题优先级：手动指定 > 页面解析 > Open API > token
    manual_title = task.get("title", "")
    if manual_title:
        title = manual_title
        log.info(f"使用手动指定标题: {title}")
    elif page_title:
        title = page_title
    else:
        # fallback: 尝试 Open API
        try:
            user_token = ensure_feishu_token(creds, session)
            title, duration = get_minutes_info(token, user_token, session)
        except Exception as e:
            log.warning(f"Open API 获取标题失败: {e}")
            title = token

    base_name = safe_filename(title)
    output_dir = PROJECT_DIR / config.get("output_dir", "output")
    output_dir.mkdir(exist_ok=True)

    # 下载视频
    video_path = output_dir / f"{base_name}.ts"
    download_video(video_url, video_path, cookie)

    # 从视频提取音频（飞书妙记无独立音频流）
    audio_path = output_dir / f"{base_name}.mp3"
    extract_audio_from_video(video_path, audio_path)

    # 下载 VTT 字幕并转为 SRT
    download_vtt_subtitle(vtt_url, output_dir, base_name, cookie)

    # 获取文字记录（Open API，可能失败）
    try:
        user_token = ensure_feishu_token(creds, session)
        get_transcript(token, user_token, session, output_dir, base_name)
    except Exception as e:
        log.warning(f"获取文字记录失败: {e}")

    log.info(f"飞书妙记处理完成: {base_name}")


def main():
    config, creds = load_config()
    tasks = config.get("tasks", [])
    if not tasks:
        log.warning("config.yaml 中无任务")
        return

    for i, task in enumerate(tasks):
        source_type = task.get("source_type", "")
        log.info(f"=== 任务 {i+1}/{len(tasks)}: {source_type} ===")

        if source_type == "feishu_minutes":
            process_feishu_minutes(task, config, creds)
        elif source_type == "panda":
            from s1_panda import process_panda
            process_panda(task, config, creds)
        else:
            log.warning(f"暂不支持的视频源: {source_type}")

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
