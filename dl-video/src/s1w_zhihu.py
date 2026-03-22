"""Step1-zhihu: 知乎训练营视频下载

使用方式:
  1. 浏览器打开视频页面，F12 → Network → 找到 .m3u8 请求
  2. 右键 Copy as cURL → 粘贴到 config.yaml 的 source_url 字段
  3. python src/s1_huifang.py

流程:
  页面 URL → Playwright 捕获 m3u8 → ffmpeg 下载
"""

import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml

from modules.ffmpeg_utils import extract_audio, download_hls
from modules.config_utils import load_config, safe_filename

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
        logging.FileHandler(LOG_DIR / "s1w_zhihu.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def fetch_page_info(page_url: str, cookie_str: str) -> tuple[str, str, str]:
    """从知乎训练营页面 URL 提取视频标题、MP4 地址和发布日期

    Returns:
        (title, video_url, publish_date) - 日期格式 YYMMDD，如 "260312"
    """
    from playwright.sync_api import sync_playwright
    import time as _time
    import json
    from datetime import datetime

    # 从 URL 提取 course_id 和 video_id
    match = re.search(r'/training-video/(\d+)/(\d+)', page_url)
    if not match:
        raise ValueError(f"无法从 URL 提取视频 ID: {page_url}")

    course_id = match.group(1)
    video_id = match.group(2)
    log.info(f"提取到 course_id={course_id}, video_id={video_id}")

    # 解析 cookies
    cookies_dict = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            cookies_dict[name.strip()] = value.strip()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.zhihu.com/",
        "Origin": "https://www.zhihu.com",
    }

    # 尝试从 video_id 提取日期（雪花算法 ID）
    publish_date = ""

    # 优先从 catalog API 获取实际发布日期
    catalog_url = f"https://www.zhihu.com/api/education/training/{course_id}/video_page/catalog?limit=10&offset=0"
    try:
        resp = requests.get(catalog_url, cookies=cookies_dict, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            videos = data.get("data", {}).get("data", [])
            for v in videos:
                if v.get("id") == video_id and v.get("section_start_at"):
                    ts = int(v["section_start_at"])
                    dt = datetime.fromtimestamp(ts)
                    publish_date = dt.strftime("%y%m%d")
                    log.info(f"从 catalog API 获取日期: {publish_date} ({dt.strftime('%Y-%m-%d')})")
                    break
    except Exception as e:
        log.warning(f"catalog API 请求失败: {e}")

    # 如果 catalog API 失败，尝试从 video_id 提取（备选方案）
    if not publish_date:
        try:
            vid = int(video_id)
            timestamp_ms = (vid >> 22) + 1293170327719
            dt = datetime.fromtimestamp(timestamp_ms / 1000)
            publish_date = dt.strftime("%y%m%d")
            log.info(f"从 video_id 提取日期: {publish_date} ({dt.strftime('%Y-%m-%d')})")
        except Exception as e:
            log.warning(f"从 video_id 提取日期失败: {e}")

    # 1. 先获取课程详情，找到视频信息
    course_api_url = f"https://www.zhihu.com/api/education/training/course/{course_id}"
    try:
        resp = requests.get(course_api_url, cookies=cookies_dict, headers=headers, timeout=15)
        if resp.status_code == 200:
            course_data = resp.json()
            # 提取标题
            title = ""
            if "title" in course_data:
                title = course_data["title"]
            elif "course" in course_data and "title" in course_data["course"]:
                title = course_data["course"]["title"]
            log.info(f"从课程 API 获取标题: {title}")
    except Exception as e:
        log.warning(f"课程 API 请求失败: {e}")
        title = ""

    # 2. 尝试获取视频播放地址的 API
    # 知乎视频播放地址可能需要通过这个端点获取
    video_api_urls = [
        # 视频播放地址 API
        f"https://www.zhihu.com/api/education/training/course/{course_id}/video/{video_id}/play_info",
        f"https://www.zhihu.com/api/education/training/video/{video_id}/play_info",
        f"https://www.zhihu.com/api/education/video/{video_id}/play_info",
        # 视频详情 API
        f"https://www.zhihu.com/api/education/training/course/{course_id}/video_page/{video_id}",
    ]

    for api_url in video_api_urls:
        try:
            resp = requests.get(api_url, cookies=cookies_dict, headers=headers, timeout=15)
            log.info(f"API {api_url[:60]}... 响应: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                log.info(f"API 响应: {json.dumps(data, ensure_ascii=False)[:800]}")

                # 递归查找日期字段
                def find_timestamp(obj, depth=0):
                    if depth > 3 or not publish_date:
                        return None
                    if isinstance(obj, dict):
                        for key in ["created_time", "publish_time", "start_time", "created_at", "published_at",
                                    "create_time", "update_time", "updated_at", "time", "timestamp"]:
                            if key in obj and obj[key]:
                                try:
                                    val = obj[key]
                                    if isinstance(val, (int, float)):
                                        ts = int(val) if val < 10000000000 else int(val) // 1000
                                        if 1000000000 < ts < 2000000000:  # 合理的时间戳范围
                                            return ts
                                except:
                                    pass
                        for v in obj.values():
                            result = find_timestamp(v, depth + 1)
                            if result:
                                return result
                    elif isinstance(obj, list) and len(obj) > 0:
                        return find_timestamp(obj[0], depth + 1)
                    return None

                if not publish_date:
                    ts = find_timestamp(data)
                    if ts:
                        dt = datetime.fromtimestamp(ts)
                        publish_date = dt.strftime("%y%m%d")
                        log.info(f"从 API 提取日期: {publish_date}")

                # 遍历 JSON 查找视频 URL
                def find_video_url(obj, depth=0):
                    if depth > 5:
                        return None
                    if isinstance(obj, dict):
                        for key in ["play_url", "video_url", "url", "src", "mp4_url", "video", "play_info", "source"]:
                            if key in obj:
                                val = obj[key]
                                if isinstance(val, str) and (".mp4" in val or "vzuu.com" in val or "vdn" in val):
                                    return val
                                # 检查嵌套对象
                                result = find_video_url(val, depth + 1)
                                if result:
                                    return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = find_video_url(item, depth + 1)
                            if result:
                                return result
                    return None

                video_url = find_video_url(data)
                if video_url:
                    log.info(f"从 API 获取到视频 URL: {video_url[:80]}...")
                    return title, video_url, publish_date

        except Exception as e:
            log.warning(f"API 请求失败: {api_url[:60]}... - {e}")

    # 如果 API 方式都失败，使用 Playwright 捕获网络请求
    log.info("API 方式未获取到视频，尝试 Playwright 页面捕获...")

    domain = urlparse(page_url).netloc
    m3u8_found = []

    def _make_cookies(cookie_str, domain):
        """为指定域名生成 cookie 列表"""
        cookies = []
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                cookie = {"name": name.strip(), "value": value.strip(), "path": "/"}
                if domain:
                    cookie["domain"] = domain
                cookies.append(cookie)
        return cookies

    # 如果 API 方式失败，尝试 Playwright 页面方式
    log.info("启动 Playwright（headless 模式）...")
    with sync_playwright() as p:
        # 使用 headless 模式
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies(_make_cookies(cookie_str, domain))

        page = ctx.new_page()

        # 监听网络请求，捕获 m3u8 和视频相关请求
        def handle_request(req):
            url = req.url
            # 捕获 m3u8 和部分 ts 请求（知乎视频 URL 可能包含 video 关键字）
            if ".m3u8" in url or ("video" in url.lower() and "zhihu" in url.lower()):
                log.info(f"捕获请求: {url[:100]}...")
                m3u8_found.append(url)

        page.on("request", handle_request)

        page.goto(page_url, timeout=30000, wait_until="domcontentloaded")

        # 等待页面加载完成后，滚动页面触发懒加载
        _time.sleep(3)

        # 尝试点击"开始学习"或播放按钮
        try:
            # 查找可能的播放按钮并点击
            page.evaluate("""
                () => {
                    // 尝试多种选择器查找播放按钮
                    const selectors = [
                        'button[class*="play"]',
                        'button[class*="start"]',
                        '[class*="play-button"]',
                        '[class*="start-button"]',
                        '.play-icon',
                        'div[role="button"]'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null) {
                            el.click();
                            return 'clicked: ' + sel;
                        }
                    }
                    // 尝试直接查找包含"开始"的按钮
                    const buttons = document.querySelectorAll('button, div[role="button"]');
                    for (const btn of buttons) {
                        const text = btn.innerText || btn.textContent || '';
                        if (text.includes('开始') || text.includes('播放')) {
                            btn.click();
                            return 'clicked by text: ' + text.substring(0, 20);
                        }
                    }
                    return 'no button found';
                }
            """)
            log.info("已尝试点击播放按钮")
            _time.sleep(3)
        except Exception as e:
            log.warning(f"点击播放按钮失败: {e}")

        # 滚动页面触发视频懒加载
        try:
            page.evaluate("""
                () => {
                    window.scrollTo(0, 500);
                }
            """)
            _time.sleep(2)
            page.evaluate("""window.scrollTo(0, 0);""")
            _time.sleep(1)
        except Exception as e:
            log.warning(f"滚动页面失败: {e}")

        # 等待视频加载（知乎视频需要更长等待时间）
        _time.sleep(8)

        # 尝试从页面 DOM 中提取视频信息
        try:
            video_info = page.evaluate("""
                () => {
                    // 查找页面中的视频元素
                    const video = document.querySelector('video');
                    if (video && video.src) {
                        return { type: 'video_src', value: video.src };
                    }

                    // 查找 iframe 中的视频
                    const iframes = document.querySelectorAll('iframe');
                    for (const iframe of iframes) {
                        try {
                            const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
                            const iframeVideo = iframeDoc.querySelector('video');
                            if (iframeVideo && iframeVideo.src) {
                                return { type: 'iframe_video', value: iframeVideo.src };
                            }
                        } catch(e) {}
                    }

                    // 查找包含 video 或 vzuu 的脚本标签
                    const scripts = document.querySelectorAll('script');
                    for (const script of scripts) {
                        const text = script.innerHTML || '';
                        if (text.includes('vzuu.com') || text.includes('.mp4')) {
                            const match = text.match(/(https?:[^"']+\.mp4[^"']*)/);
                            if (match) {
                                return { type: 'script', value: match[1] };
                            }
                        }
                    }

                    // 查找 window.__INITIAL_STATE__ 或类似的全局变量
                    for (const key in window) {
                        if (key.includes('INITIAL') || key.includes('STATE') || key.includes('DATA')) {
                            try {
                                const data = JSON.stringify(window[key]);
                                if (data.includes('vzuu.com') || data.includes('.mp4')) {
                                    const match = data.match(/(https?:[^"']+\.mp4[^"']*)/);
                                    if (match) {
                                        return { type: 'global_var', value: match[1] };
                                    }
                                }
                            } catch(e) {}
                        }
                    }

                    return null;
                }
            """)
            if video_info:
                log.info(f"从 DOM 提取到视频信息: {video_info}")
                if video_info.get("value"):
                    m3u8_found.append(video_info["value"])
        except Exception as e:
            log.warning(f"从 DOM 提取视频信息失败: {e}")

        final_url = page.url
        log.info(f"页面 URL: {final_url}")

        # 获取标题（只有当之前没有从 API 获取到标题时才使用页面标题）
        if not title:
            try:
                title = page.title().strip()
                # 尝试从页面中提取更准确的标题
                # 知乎训练营视频标题可能在特定元素中
                title_match = page.evaluate("""
                    () => {
                        // 尝试多种选择器获取标题
                        const selectors = [
                            '.training-video-title',
                            '.video-title',
                            'h1',
                            '[class*="title"]'
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el) return el.innerText.trim();
                        }
                        return document.title;
                    }
                """)
                if title_match and title_match.strip():
                    title = title_match.strip()
            except Exception as e:
                log.warning(f"获取标题失败: {e}")
                if not title:
                    title = page.title().strip()

        browser.close()

    # 过滤出真正的视频 URL（m3u8 或 mp4）
    m3u8_url = ""
    for url in m3u8_found:
        if ".m3u8" in url:
            m3u8_url = url
            break
        # 如果没有 m3u8，尝试找 MP4 视频 URL
        if ".mp4" in url and ("vzuu.com" in url or "vdn" in url):
            m3u8_url = url
            break
        # 如果没有 m3u8，尝试找包含视频播放的 URL
        if "video" in url.lower() and "url" in url.lower():
            m3u8_url = url

    log.info(f"标题: {title}")
    if m3u8_url:
        log.info(f"m3u8: {m3u8_url[:80]}...")
    else:
        log.warning(f"未找到 m3u8，已捕获的 URL: {m3u8_found[:5]}")
    return title, m3u8_url, publish_date


def download_mp4_direct(mp4_url: str, referer: str, output_path: Path) -> bool:
    """直接下载 MP4 视频"""
    if output_path.exists() and output_path.stat().st_size > 1024:
        log.info(f"视频已存在，跳过: {output_path}")
        return True
    log.info(f"直接下载 MP4: {output_path.name}")

    headers = {
        "Referer": referer,
        "Origin": "https://www.zhihu.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Range": "bytes=0-",
    }

    try:
        # 先发送 HEAD 请求获取文件大小
        resp = requests.head(mp4_url, headers=headers, timeout=15, allow_redirects=True)
        content_length = resp.headers.get("Content-Length", "0")
        total_size = int(content_length) if content_length.isdigit() else 0
        log.info(f"视频大小: {total_size / 1024 / 1024:.1f} MB" if total_size else "大小未知")

        # 分片下载（知乎视频通常较大）
        chunk_size = 1024 * 1024  # 1MB
        with requests.get(mp4_url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(output_path, "wb") as f:
                downloaded = 0
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            pct = downloaded * 100 / total_size
                            if downloaded % (10 * 1024 * 1024) == 0 or downloaded == total_size:
                                log.info(f"  下载进度: {downloaded / 1024 / 1024:.1f} MB / {total_size / 1024 / 1024:.1f} MB ({pct:.1f}%)")

        size_mb = output_path.stat().st_size / 1024 / 1024
        log.info(f"MP4 下载完成: {output_path} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        log.error(f"MP4 下载失败: {e}")
        return False


def process_zhihu(task, config, creds):
    """处理知乎训练营视频任务（入口）"""
    source_url = task["source_huifang_url"]
    log.info(f"处理知乎训练营视频: {source_url}")

    # 直播间 URL 转换为录播 URL
    if "/training/live/room/" in source_url:
        source_url = source_url.replace("/training/live/room/", "/market/training/training-video/")
        log.info(f"直播间 URL 已转换为录播 URL: {source_url}")

    # 判断是直接的 MP4 URL 还是页面 URL
    is_mp4_url = ".mp4" in source_url and ("vzuu.com" in source_url or "vdn" in source_url)

    if is_mp4_url:
        # 直接使用 MP4 URL
        video_url = source_url
        referer = "https://www.zhihu.com/xen/market/training/training-video/2013216469425603157/2013217221053268327"
        title = "zhihu_video"
        publish_date = ""
    else:
        # 页面 URL，需要用 Playwright 获取视频 URL
        page_url = source_url
        parsed = urlparse(page_url)
        referer = f"{parsed.scheme}://{parsed.netloc}/"

        # 获取 cookie
        cookie_str = creds.get("zhihu", {}).get("browser_cookie", "")
        if not cookie_str:
            raise ValueError("credentials.yaml 中缺少 zhihu.browser_cookie")

        # 用 Playwright 获取标题 + 视频 URL + 日期
        title, video_url, publish_date = fetch_page_info(page_url, cookie_str)
        if not video_url:
            raise RuntimeError("未能从页面捕获视频 URL，请确认视频正常播放且已登录")

    # 确定文件名
    if not title:
        title = "zhihu_video"
        log.warning(f"标题为空，使用默认名: {title}")

    base_name = safe_filename(title)
    if publish_date:
        base_name = f"{publish_date}-{base_name}"
        log.info(f"添加日期前缀: {publish_date}")
    video_path = OUTPUT_DIR / f"{base_name}.mp4"

    # 下载视频
    if is_mp4_url or ".mp4" in video_url:
        # 直接 MP4 下载
        if not download_mp4_direct(video_url, referer, video_path):
            log.error("视频下载失败")
            return
    else:
        # HLS 流下载
        headers_str = f"Referer: {referer}\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
        if not download_hls(video_url, video_path, headers=headers_str):
            log.error("视频下载失败")
            return

    # 4. 提取音频
    extract_audio(video_path, OUTPUT_DIR / f"{base_name}.mp3")
    log.info(f"知乎训练营视频处理完成: {base_name}")


if __name__ == "__main__":
    config, creds = load_config()
    task = {
        "source_url": "https://www.zhihu.com/xen/market/training/training-video/2013216469425603157/2013217221053268327",
    }
    process_zhihu(task, config, creds)
