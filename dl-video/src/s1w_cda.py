# 参数调用示例:
#   python src/s1w_cda.py
#   python src/s1w_cda.py <url_cda>
#   python src/s1w_cda.py <url_cda_1> <url_cda_2>
"""Step1-CDA: CDA 网校课程视频下载

用法:
  # 通过 input.yaml 配置任务（source_type: "cda"）
  python src/s1_huifang.py

  # 直接运行（读取 input.yaml 中 cda 类型任务，无任务时使用内置默认 URL）
  python src/s1w_cda.py

  # 命令行直接传一个或多个 URL
  python src/s1w_cda.py https://edu.cda.cn/course/7494
  python src/s1w_cda.py URL1 URL2

参数:
  url_cda  CDA 课程目录或单课时 URL（可选，可传多个）

配置:
  cfg/input.yaml  — tasks 中 source_type: "cda", source_huifang_url: CDA 课程目录或单课时 URL
  cfg/credentials.yaml — cda.browser_cookie

流程：
  课程目录 URL -> 提取全部视频课时 URL -> 逐一下载
  单课时 URL -> 登录 Cookie 请求/浏览器捕获 -> 提取 m3u8 或 mp4 -> 下载视频 -> 提取 M4A
"""

import html
import json
import logging
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

from modules.config_utils import load_config, safe_filename
from modules.ffmpeg_utils import download_hls, extract_audio

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
_input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
OUTPUT_DIR = PROJECT_DIR / _input_cfg.get("path_output_dir", "output")
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "s1w_cda.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

DEFAULT_URL = "https://edu.cda.cn/course/7494/task/205524/show"
MEDIA_RE = re.compile(
    r"https?:\\?/\\?/[^\"'<>\\\s]+?(?:\.m3u8|\.mp4)(?:\?[^\"'<>\\\s]*)?",
    re.IGNORECASE,
)


def _cookie_dict(cookie_str: str) -> dict:
    cookies = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            cookies[name.strip()] = value.strip()
    return cookies


def _make_cookie_items(cookie_str: str, domains: list[str]) -> list[dict]:
    cookies = []
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        for domain in domains:
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
            })
    return cookies


def _normalize_media_url(raw_url: str, page_url: str) -> str:
    url = html.unescape(unquote(raw_url)).replace("\\/", "/")
    return urljoin(page_url, url)


def _unique_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _looks_like_media(url: str) -> bool:
    lower = url.lower()
    return ".m3u8" in lower or ".mp4" in lower


def _extract_course_id(url: str) -> str:
    parsed = urlparse(url)
    if "/task/" in parsed.path:
        return ""
    match = re.search(r"/(?:my/)?course/(\d+)(?:/)?$", parsed.path)
    return match.group(1) if match else ""


def _request_cda(url: str, cookie_str: str, referer: str = "https://edu.cda.cn/") -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
    }
    resp = requests.get(
        url,
        headers=headers,
        cookies=_cookie_dict(cookie_str),
        timeout=30,
    )
    resp.raise_for_status()
    return resp


def _extract_media_urls(text: str, page_url: str) -> list[str]:
    candidates = []
    variants = [
        text,
        html.unescape(text),
        html.unescape(text).replace("\\/", "/"),
    ]
    for variant in variants:
        for match in MEDIA_RE.finditer(variant):
            candidates.append(_normalize_media_url(match.group(0), page_url))

    # 部分播放器把 URL 放在 JSON 字段里，逐层解码能覆盖转义较重的情况。
    for json_blob in re.findall(r"({[^{}]*(?:url|media|source|play)[^{}]*})", text, re.I):
        try:
            data = json.loads(html.unescape(json_blob))
        except Exception:
            continue
        candidates.extend(_find_media_in_json(data, page_url))

    return _unique_keep_order(candidates)


def _find_media_in_json(obj, page_url: str, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    found = []
    if isinstance(obj, dict):
        for value in obj.values():
            found.extend(_find_media_in_json(value, page_url, depth + 1))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_find_media_in_json(value, page_url, depth + 1))
    elif isinstance(obj, str) and _looks_like_media(obj):
        found.append(_normalize_media_url(obj, page_url))
    return found


def _extract_title(page_text: str) -> str:
    soup = BeautifulSoup(page_text, "html.parser")
    dashboard_header = soup.select_one(".dashboard-header")
    if dashboard_header:
        title = dashboard_header.get_text(" ", strip=True)
        title = re.sub(r"^返回课程\s*", "", title)
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            return title

    selectors = [
        "meta[property='og:title']",
        "meta[name='title']",
        ".task-title",
        ".course-detail-heading .title",
        ".course-title",
        "h1",
        "h2",
        "title",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if not node:
            continue
        title = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
        title = re.sub(r"\s+", " ", title or "").strip()
        title = re.sub(r"\s*-\s*CDA.*$", "", title)
        if title and title not in {"提示信息", "请先登录"}:
            return title
    return ""


def _is_login_page(page_text: str) -> bool:
    soup = BeautifulSoup(page_text, "html.parser")
    meta_login = soup.select_one("meta[name='is-login']")
    if meta_login and meta_login.get("content") == "0":
        return True
    body_text = soup.get_text(" ", strip=True)[:2000]
    return "请先登录" in body_text or "/login?goto=" in page_text


def fetch_page_static(page_url: str, cookie_str: str) -> tuple[str, list[str]]:
    """请求课程页，返回 (title, media_urls)。"""
    resp = _request_cda(page_url, cookie_str)
    page_text = resp.text
    title = _extract_title(page_text)

    if _is_login_page(page_text):
        log.warning("CDA 页面提示未登录；如课程需要权限，请配置 cda.browser_cookie")
        return title, []

    media_urls = _extract_media_urls(page_text, page_url)
    log.info(f"静态页面提取到 {len(media_urls)} 个媒体 URL")
    return title, media_urls


def fetch_course_tasks(course_url: str, cookie_str: str) -> list[dict]:
    """从 CDA 课程目录页提取全部已发布视频课时。"""
    course_id = _extract_course_id(course_url)
    if not course_id:
        return []

    task_list_url = f"https://edu.cda.cn/course/{course_id}/task/list/render/default"
    referer = f"https://edu.cda.cn/my/course/{course_id}?type=tasks"
    log.info(f"请求 CDA 课程目录: {task_list_url}")
    resp = _request_cda(task_list_url, cookie_str, referer=referer)
    soup = BeautifulSoup(resp.text, "html.parser")
    data_node = soup.select_one(".js-hidden-cached-data")
    if not data_node:
        raise RuntimeError("CDA 课程目录中未找到课时缓存数据")

    raw_json = html.unescape(data_node.get_text("", strip=True))
    try:
        items = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise RuntimeError("CDA 课程目录课时 JSON 解析失败") from e

    tasks = []
    for item in items:
        if item.get("itemType") != "task":
            continue
        if item.get("status") != "published":
            continue
        if item.get("type") not in {"video", "replay", "live", "pseudolive"}:
            continue
        if item.get("lock"):
            log.warning(f"跳过未解锁课时: {item.get('title') or item.get('taskId')}")
            continue

        task_id = str(item.get("taskId") or "").strip()
        if not task_id:
            continue
        number = str(item.get("published_number") or item.get("number") or len(tasks) + 1)
        title = re.sub(r"\s+", " ", item.get("title") or f"task_{task_id}").strip()
        tasks.append({
            "number": number,
            "title": title,
            "url": f"https://edu.cda.cn/course/{course_id}/task/{task_id}/show",
            "task_id": task_id,
        })

    log.info(f"CDA 课程目录提取到 {len(tasks)} 个视频课时")
    return tasks


def _extract_activity_url(page_text: str, page_url: str) -> str:
    soup = BeautifulSoup(page_text, "html.parser")
    iframe = soup.select_one("#task-content-iframe")
    if not iframe:
        return ""
    activity_url = iframe.get("src") or iframe.get("data-url") or ""
    return urljoin(page_url, activity_url)


def _extract_qiqiuyun_playlist(activity_text: str, activity_url: str) -> str:
    soup = BeautifulSoup(activity_text, "html.parser")
    node = soup.select_one("#lesson-video-content")
    if not node:
        return ""

    res_no = (
        node.get("data-file-global-id")
        or node.get("data-global-id")
        or node.get("data-res-no")
        or ""
    )
    token = node.get("data-token") or ""
    if not res_no or not token:
        return ""

    params = {
        "resNo": res_no,
        "token": token,
        "ssl": 1,
        "sdkVersion": "1.0.14",
        "sdkType": "js",
        "lang": "zh-CN",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": activity_url,
        "Origin": "https://edu.cda.cn",
    }
    resp = requests.get(
        "https://play.qiqiuyun.net/sdk_api/play",
        params=params,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code"):
        raise RuntimeError(f"qiqiuyun 播放 API 错误: {data}")

    playlist = data.get("args", {}).get("playlist", "")
    if playlist:
        log.info(f"从 qiqiuyun 播放 API 获取 playlist: {playlist[:100]}...")
    return playlist


def fetch_activity_media(page_url: str, page_text: str, cookie_str: str) -> list[str]:
    """从 task iframe 的 activity_show 中提取云播放器 playlist。"""
    activity_url = _extract_activity_url(page_text, page_url)
    if not activity_url:
        return []

    log.info(f"请求 CDA activity iframe: {activity_url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": page_url,
    }
    resp = requests.get(
        activity_url,
        headers=headers,
        cookies=_cookie_dict(cookie_str),
        timeout=30,
    )
    resp.raise_for_status()

    media_urls = _extract_media_urls(resp.text, activity_url)
    playlist = _extract_qiqiuyun_playlist(resp.text, activity_url)
    if playlist:
        media_urls.insert(0, playlist)
    return _unique_keep_order(media_urls)


def fetch_page_browser(page_url: str, cookie_str: str) -> tuple[str, list[str]]:
    """用 Playwright 捕获 CDA 播放器发出的 m3u8/mp4 请求。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "未能从 HTML 直接提取视频地址，且当前环境未安装 playwright。"
            "请安装后重试，或在浏览器 Network 中复制 m3u8/mp4 直链作为 source_huifang_url。"
        ) from e

    parsed = urlparse(page_url)
    domains = [parsed.netloc, ".cda.cn", "edu.cda.cn"]
    found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            )
        )
        if cookie_str:
            ctx.add_cookies(_make_cookie_items(cookie_str, domains))

        page = ctx.new_page()

        def record(url: str):
            if _looks_like_media(url):
                log.info(f"捕获媒体请求: {url[:120]}...")
                found.append(url)

        page.on("request", lambda req: record(req.url))
        page.on("response", lambda resp: record(resp.url))
        page.goto(page_url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(15000)

        title = _extract_title(page.content()) or page.title().strip()
        found.extend(_extract_media_urls(page.content(), page_url))
        browser.close()

    return title, _unique_keep_order(found)


def choose_media_url(media_urls: list[str]) -> str:
    if not media_urls:
        return ""
    for url in media_urls:
        if ".m3u8" in url.lower():
            return url
    for url in media_urls:
        if ".mp4" in url.lower():
            return url
    return media_urls[0]


def resolve_qiqiuyun_variant(master_url: str) -> str:
    """qiqiuyun master playlist 需要先展开为具体清晰度的 hls_stream URL。"""
    if "play.qiqiuyun.net/sdk_api/video/hls_playlist.m3u8" not in master_url:
        return master_url

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(master_url, headers=headers, timeout=30)
    resp.raise_for_status()
    lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
    variants = [line for line in lines if not line.startswith("#") and ".m3u8" in line]
    if not variants:
        raise RuntimeError(f"qiqiuyun master playlist 中未找到清晰度流: {resp.text[:300]}")

    selected = variants[0]
    log.info(f"选择 qiqiuyun HLS 变体: {selected[:100]}...")
    return selected


def _parse_base36(value: str) -> int:
    return int(value, 36)


def _pick_bytes(data: bytes, order: str) -> bytes:
    return bytes(data[int(i)] for i in order.split("-"))


def _decode_qiqiuyun_clef(clef: bytes) -> bytes:
    """复刻 qiqiuyun HLS core 的 version=3 key 解混淆逻辑。"""
    if len(clef) == 16:
        return clef
    if len(clef) == 17:
        data = clef[1:]
        return _pick_bytes(data, "8-9-2-3-4-5-6-7-0-1-10-11-12-13-14-15")
    if len(clef) == 20:
        marker_idx = _parse_base36(chr(clef[0]).lower()) % 2
        algorithm = _parse_base36(chr(clef[marker_idx]) + chr(clef[marker_idx + 1])) % 3
        if algorithm == 0:
            return _pick_bytes(clef, "0-1-2-3-4-15-16-17-18-10-11-12-13-6-7-8")
        if algorithm == 1:
            return _pick_bytes(clef, "0-1-2-8-9-10-11-12-18-17-16-15-14-4-5-6")

        base = ord("a")
        b5 = clef[5] - base + 26 * (int(chr(clef[6])) + 1) - base
        b8 = clef[9] - base + 26 * (int(chr(clef[10])) + 1) - base
        b11 = clef[13] - base + 26 * (int(chr(clef[14])) + 1) - base
        b14 = clef[17] - base + 26 * (int(chr(clef[18])) + 2) - base
        return bytes([
            clef[0], clef[1], clef[2], clef[3], clef[4], b5,
            clef[7], clef[8], b8, clef[11], clef[12], b11,
            clef[15], clef[16], b14, clef[19],
        ])
    raise RuntimeError(f"qiqiuyun key 长度异常: {len(clef)} bytes")


def prepare_qiqiuyun_local_m3u8(variant_url: str) -> tuple[str, tempfile.TemporaryDirectory]:
    """下载 qiqiuyun 变体 m3u8，解混淆 key，并改写为本地 key 文件。"""
    tmpdir = tempfile.TemporaryDirectory(prefix="cda-qiqiuyun-")
    tmp_path = Path(tmpdir.name)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    resp = requests.get(variant_url, headers=headers, timeout=30)
    resp.raise_for_status()
    m3u8_text = resp.text

    key_urls = _unique_keep_order(re.findall(r'URI="([^"]+)"', m3u8_text))
    key_paths = {}
    for i, key_url in enumerate(key_urls):
        key_resp = requests.get(key_url, headers=headers, timeout=30)
        key_resp.raise_for_status()
        key = _decode_qiqiuyun_clef(key_resp.content)
        key_path = tmp_path / f"key_{i}.bin"
        key_path.write_bytes(key)
        key_paths[key_url] = key_path.as_posix()

    for key_url, key_path in key_paths.items():
        m3u8_text = m3u8_text.replace(f'URI="{key_url}"', f'URI="{key_path}"')

    local_m3u8 = tmp_path / "playlist.m3u8"
    local_m3u8.write_text(m3u8_text, encoding="utf-8")
    log.info(f"已生成 qiqiuyun 本地 m3u8: {local_m3u8}")
    return local_m3u8.as_posix(), tmpdir


def download_mp4_direct(mp4_url: str, output_path: Path, referer: str, cookie_str: str = "") -> bool:
    if output_path.exists() and output_path.stat().st_size > 1024:
        log.info(f"视频已存在，跳过: {output_path}")
        return True

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
    }
    if cookie_str:
        headers["Cookie"] = cookie_str

    tmp_path = output_path.with_suffix(".tmp.mp4")
    log.info(f"直接下载 MP4: {output_path.name}")
    with requests.get(mp4_url, headers=headers, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = min(int(downloaded * 100 / total), 99)
                    print(
                        f"\r  下载视频: {pct}% ({downloaded / 1024 / 1024:.1f} / "
                        f"{total / 1024 / 1024:.1f} MB)",
                        end="",
                        flush=True,
                    )
    if total:
        print(f"\r  下载视频: 100% ({total / 1024 / 1024:.1f} MB)")

    tmp_path.replace(output_path)
    log.info(f"MP4 下载完成: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return True


def process_cda(task, config, creds):
    source_url = task["source_huifang_url"]
    log.info(f"处理 CDA 视频: {source_url}")

    cookie_str = (
        task.get("browser_cookie")
        or creds.get("cda", {}).get("browser_cookie", "")
        or creds.get("cda", {}).get("cookie", "")
    )

    course_id = _extract_course_id(source_url)
    if course_id:
        course_tasks = fetch_course_tasks(source_url, cookie_str)
        if not course_tasks:
            raise RuntimeError("未能从 CDA 课程目录提取到视频课时")
        total = len(course_tasks)
        for i, course_task in enumerate(course_tasks, 1):
            log.info(f"=== CDA 课程课时 {i}/{total}: {course_task['title']} ===")
            child_task = dict(task)
            child_task["source_huifang_url"] = course_task["url"]
            child_task["title"] = course_task["title"]
            number = re.sub(r"\D+", "", course_task.get("number", "")) or str(i)
            if task.get("output_name"):
                child_task["output_name"] = safe_filename(
                    f"{task['output_name']}_{int(number):02d}_{course_task['title']}"
                )
            else:
                child_task["output_name"] = safe_filename(f"{int(number):02d}_{course_task['title']}")
            process_cda(child_task, config, creds)
        return

    if _looks_like_media(source_url):
        title = task.get("title") or "cda_video"
        media_url = source_url
    else:
        title, media_urls = fetch_page_static(source_url, cookie_str)
        media_url = choose_media_url(media_urls)
        if not media_url:
            resp = _request_cda(source_url, cookie_str)
            title = title or _extract_title(resp.text)
            media_url = choose_media_url(fetch_activity_media(source_url, resp.text, cookie_str))
        if not media_url:
            log.info("静态页面未找到媒体 URL，尝试浏览器网络捕获")
            title_browser, media_urls = fetch_page_browser(source_url, cookie_str)
            title = title or title_browser
            media_url = choose_media_url(media_urls)

    if not media_url:
        raise RuntimeError("未能提取 CDA 视频地址，请确认已登录且课程可播放")

    base_name = task.get("output_name") or safe_filename(task.get("title") or title or "cda_video")
    video_path = OUTPUT_DIR / f"{base_name}.mp4"
    referer = source_url if source_url.startswith("http") else "https://edu.cda.cn/"

    if ".m3u8" in media_url.lower():
        media_url = resolve_qiqiuyun_variant(media_url)
        headers = "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
        if "play.qiqiuyun.net/sdk_api/video/" not in media_url:
            headers += f"Referer: {referer}\r\nOrigin: https://edu.cda.cn\r\n"
        if cookie_str and "play.qiqiuyun.net/sdk_api/video/" not in media_url:
            headers += f"Cookie: {cookie_str}\r\n"
        probe_duration = "play.qiqiuyun.net/sdk_api/video/" not in media_url
        tmpdir = None
        try:
            allowed_extensions_all = False
            protocol_whitelist = None
            extension_picky = True
            if "play.qiqiuyun.net/sdk_api/video/hls_stream/" in media_url:
                media_url, tmpdir = prepare_qiqiuyun_local_m3u8(media_url)
                headers = None
                allowed_extensions_all = True
                protocol_whitelist = "file,http,https,tcp,tls,crypto,data"
                extension_picky = False
            if not download_hls(
                media_url,
                video_path,
                headers=headers,
                probe_duration=probe_duration,
                allowed_extensions_all=allowed_extensions_all,
                protocol_whitelist=protocol_whitelist,
                extension_picky=extension_picky,
            ):
                raise RuntimeError("CDA HLS 视频下载失败")
        finally:
            if tmpdir:
                tmpdir.cleanup()
    else:
        if not download_mp4_direct(media_url, video_path, referer, cookie_str):
            raise RuntimeError("CDA MP4 视频下载失败")

    extract_audio(video_path, OUTPUT_DIR / f"{base_name}.m4a")
    log.info(f"CDA 视频处理完成: {base_name}")


def main():
    config, creds = load_config()
    if len(sys.argv) > 1:
        tasks = [{"source_type": "cda", "source_huifang_url": arg} for arg in sys.argv[1:]]
    else:
        tasks = [t for t in config.get("tasks", []) if t.get("source_type") == "cda"]
        if not tasks:
            log.warning("input.yaml 中无 cda 类型任务；可直接运行: python src/s1w_cda.py URL")
            tasks = [{"source_type": "cda", "source_huifang_url": DEFAULT_URL}]

    for i, task in enumerate(tasks):
        log.info(f"=== CDA 任务 {i + 1}/{len(tasks)} ===")
        try:
            process_cda(task, config, creds)
        except Exception:
            log.exception(f"任务处理失败: {task.get('source_huifang_url')}")


if __name__ == "__main__":
    main()
