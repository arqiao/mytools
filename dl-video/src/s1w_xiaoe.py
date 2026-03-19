"""Step1-xiaoe: 小鹅通视频下载 — AES-128 DRM 解密

使用方式:
  1. 浏览器打开视频页面，F12 → Network → 找到 .m3u8 请求
  2. 右键 Copy as cURL → 粘贴到 config.yaml 的 source_url 字段
     （只需 m3u8 URL，不需要完整 curl 命令）
  3. python src/s1_huifang.py

流程:
  m3u8 URL → 下载 m3u8 → 提取 key URL → 获取 AES key
  → 重写 m3u8（绝对路径 + 本地 key）→ ffmpeg 解密下载
"""

import logging
import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

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
        logging.FileHandler(LOG_DIR / "s1w_xiaoe.log", encoding="utf-8"),
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


def extract_m3u8_url(source_url: str) -> str:
    """从 source_url 提取 m3u8 URL（支持完整 curl 命令或纯 URL）"""
    # 如果是 curl 命令，提取 URL
    m = re.search(r'curl\s+["\']?(https?://[^\s"\']+)', source_url)
    if m:
        return m.group(1)
    # 纯 URL
    if source_url.startswith("http"):
        return source_url.strip().strip('"').strip("'")
    raise ValueError(f"无法解析小鹅通 URL: {source_url}")


def fetch_page_info(page_url: str, cookie_str: str) -> tuple[str, str, str]:
    """用 Playwright + cookie 同时获取标题、日期和 m3u8 URL，返回 (title, date_str, m3u8_url)"""
    from playwright.sync_api import sync_playwright
    import time as _time

    # 小鹅通常见域名后缀，cookie 需要设在这些域名上
    XIAOE_DOMAINS = [".xiaoecloud.com", ".xiaoeknow.com", ".xiaoe-tech.com",
                     ".xet.citv.cn"]

    def _make_cookies(cookie_str, domain=None):
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

    domain = urlparse(page_url).netloc
    m3u8_found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()

        # 提取 app_id（如 appsbkkqmgs9185），为所有小鹅通域名预设 cookie
        app_id_match = re.search(r'(app\w+)\.h5\.', domain)
        if app_id_match:
            app_id = app_id_match.group(1)
        else:
            app_id = "appsbkkqmgs9185"  # fallback
        all_domains = [domain]
        for suffix in XIAOE_DOMAINS:
            d = f"{app_id}.h5{suffix}"
            if d not in all_domains:
                all_domains.append(d)
        for d in all_domains:
            ctx.add_cookies(_make_cookies(cookie_str, d))

        page = ctx.new_page()
        page.on("request", lambda req: m3u8_found.append(req.url)
                if ".m3u8" in req.url else None)
        page.goto(page_url, timeout=30000, wait_until="domcontentloaded")

        # 检查是否跳转到登录页，若是则从 URL 提取 app_id 并补充 cookie
        if "/login/auth" in page.url:
            redirect_match = re.search(r'redirect_url=([^&]+)', page.url)
            if redirect_match:
                import urllib.parse
                redirect_url = urllib.parse.unquote(redirect_match.group(1))
                app_id_match = re.search(r'(app\w+)\.h5\.', redirect_url)
                if app_id_match:
                    real_app_id = app_id_match.group(1)
                    log.info(f"登录跳转，补充 {real_app_id} 的 cookie")
                    for suffix in XIAOE_DOMAINS:
                        ctx.add_cookies(_make_cookies(cookie_str,
                                                      f"{real_app_id}.h5{suffix}"))
                    # 直接导航到目标页面
                    page.goto(redirect_url, timeout=30000,
                              wait_until="domcontentloaded")

        # 等待 SPA 加载视频播放器
        _time.sleep(12)

        final_url = page.url
        log.info(f"页面 URL: {final_url}")
        try:
            content = page.content()
            log.info(f"页面内容长度: {len(content)}")
            if "登录" in content[:2000]:
                log.warning("检测到登录页面")
        except Exception:
            log.warning("页面仍在加载，跳过内容检查")

        title = page.title().strip()

        # 提取日期（尝试从页面内容中查找日期）
        date_str = ""
        try:
            content = page.content()
            # 尝试匹配常见日期格式：2024/03/14, 2024-03-14, 03/14等
            date_match = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', content)
            if date_match:
                year, month, day = date_match.groups()
                date_str = f"{year[2:]}{int(month):02d}{int(day):02d}"
        except:
            pass

        # 如果没有提取到日期，使用当前日期
        if not date_str:
            from datetime import datetime
            now = datetime.now()
            date_str = now.strftime("%y%m%d")

        browser.close()

    m3u8_url = m3u8_found[0] if m3u8_found else ""
    log.info(f"标题: {title}")
    log.info(f"日期: {date_str}")
    log.info(f"m3u8: {m3u8_url[:80]}...")
    return title, date_str, m3u8_url


def download_m3u8(m3u8_url: str, referer: str = "") -> str:
    """下载 m3u8 内容（需要在签名过期前调用）"""
    if not referer:
        # 从 whref 参数推断 referer
        m = re.search(r'whref=[^&]*\*\.xiaoecloud\.com', m3u8_url)
        referer = "https://appsbkkqmgs9185.h5.xiaoecloud.com/"
    headers = {
        "Origin": referer.rstrip("/"),
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    resp = requests.get(m3u8_url, headers=headers, timeout=30)
    if "sign not match" in resp.text or resp.status_code != 200:
        raise RuntimeError(f"m3u8 签名已过期或无效: {resp.text[:200]}")
    if "#EXTM3U" not in resp.text:
        raise RuntimeError(f"非有效 m3u8 内容: {resp.text[:200]}")
    log.info(f"m3u8 下载成功，{resp.text.count('#EXTINF')} 个分片")
    return resp.text


def fetch_aes_key(key_url: str) -> bytes:
    """从小鹅通 key server 获取 AES-128 密钥"""
    headers = {
        "Origin": "https://appsbkkqmgs9185.h5.xiaoecloud.com",
        "Referer": "https://appsbkkqmgs9185.h5.xiaoecloud.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    resp = requests.get(key_url, headers=headers, timeout=15)
    if len(resp.content) != 16:
        raise RuntimeError(f"AES key 长度异常: {len(resp.content)} bytes")
    log.info(f"AES key 获取成功: {resp.content.hex()}")
    return resp.content


def rewrite_m3u8(m3u8_text: str, m3u8_url: str, local_key_path: str) -> str:
    """重写 m3u8: 相对 TS 路径 → 绝对 URL，key URI → 本地文件"""
    base_url = m3u8_url.rsplit("/", 1)[0] + "/"
    lines = m3u8_text.splitlines()
    out = []
    for line in lines:
        if line.startswith("#EXT-X-KEY:"):
            # 替换 key URI 为本地文件路径
            # re.escape 防止 Windows 路径中的 \w 等被当作正则转义
            safe_path = local_key_path.replace("\\", "/")
            line = re.sub(
                r'URI="[^"]*"',
                f'URI="{safe_path}"',
                line,
            )
        elif line and not line.startswith("#"):
            # TS 分片相对路径 → 绝对 URL
            line = urljoin(base_url, line)
        out.append(line)
    return "\n".join(out)


def parse_ts_urls(m3u8_text: str, m3u8_url: str) -> list[str]:
    """从 m3u8 提取所有 TS 分片的绝对 URL"""
    base_url = m3u8_url.rsplit("/", 1)[0] + "/"
    urls = []
    for line in m3u8_text.splitlines():
        if line and not line.startswith("#"):
            urls.append(urljoin(base_url, line))
    return urls


def download_and_decrypt_segments(ts_urls: list[str], aes_key: bytes,
                                  iv: bytes, referer: str,
                                  output_ts: Path) -> bool:
    """下载所有 TS 分片，AES-128 解密，合并为单个 TS 文件"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    headers = {
        "Referer": referer,
        "Origin": referer.rstrip("/"),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    total = len(ts_urls)
    log.info(f"开始下载 {total} 个 TS 分片...")

    with open(output_ts, "wb") as out:
        for i, url in enumerate(ts_urls):
            # 重试机制：最多 3 次
            for attempt in range(3):
                try:
                    resp = requests.get(url, headers=headers, timeout=30)
                    if resp.status_code != 200:
                        log.error(f"分片 {i}/{total} 下载失败: status={resp.status_code}")
                        return False
                    break
                except (requests.ConnectionError, requests.Timeout) as e:
                    if attempt < 2:
                        import time as _t
                        _t.sleep(2 * (attempt + 1))
                        log.warning(f"分片 {i} 重试 {attempt+1}/3: {e}")
                    else:
                        log.error(f"分片 {i} 下载失败（已重试3次）: {e}")
                        return False
            # AES-128-CBC 解密
            cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(resp.content) + decryptor.finalize()
            # PKCS7 去填充
            pad_len = decrypted[-1]
            if 1 <= pad_len <= 16:
                decrypted = decrypted[:-pad_len]
            out.write(decrypted)
            if (i + 1) % 50 == 0 or i == total - 1:
                log.info(f"  进度: {i+1}/{total}")
    size_mb = output_ts.stat().st_size / 1024 / 1024
    log.info(f"TS 合并完成: {output_ts} ({size_mb:.1f} MB)")
    return True


def ffmpeg_download_hls(m3u8_url: str, referer: str, output_path: Path) -> bool:
    """ffmpeg 直接下载无加密 HLS 流"""
    if output_path.exists() and output_path.stat().st_size > 1024:
        log.info(f"视频已存在，跳过: {output_path}")
        return True
    log.info(f"ffmpeg 下载 HLS（无加密）: {output_path.name}")
    cmd = [
        FFMPEG,
        "-headers", f"Referer: {referer}\r\nOrigin: {referer.rstrip('/')}\r\n",
        "-i", m3u8_url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        str(output_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=1800)
    except FileNotFoundError:
        log.error("ffmpeg 未安装")
        return False
    if result.returncode != 0:
        log.error(f"ffmpeg HLS 下载失败: {result.stderr[-500:]}")
        return False
    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"HLS 下载完成: {output_path} ({size_mb:.1f} MB)")
    return True


def remux_ts_to_mp4(ts_path: Path, mp4_path: Path) -> bool:
    """ffmpeg 将 TS 转封装为 MP4"""
    cmd = [
        FFMPEG, "-i", str(ts_path),
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        str(mp4_path), "-y",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=300)
    except FileNotFoundError:
        log.error("ffmpeg 未安装")
        return False
    if result.returncode != 0:
        log.error(f"remux 失败: {result.stderr[-500:]}")
        return False
    size_mb = mp4_path.stat().st_size / 1024 / 1024
    log.info(f"MP4 转封装完成: {mp4_path} ({size_mb:.1f} MB)")
    return True


def download_video(m3u8_text: str, m3u8_url: str, aes_key: bytes,
                   referer: str, output_path: Path) -> bool:
    """完整下载流程: 下载分片 → 解密 → 合并 → remux MP4"""
    if output_path.exists() and output_path.stat().st_size > 1024:
        log.info(f"视频已存在，跳过: {output_path}")
        return True

    # 解析 IV（默认全零）
    iv_match = re.search(r'IV=0x([0-9a-fA-F]+)', m3u8_text)
    iv = bytes.fromhex(iv_match.group(1)) if iv_match else b'\x00' * 16

    # 提取 TS URL 列表
    ts_urls = parse_ts_urls(m3u8_text, m3u8_url)
    if not ts_urls:
        log.error("未找到 TS 分片")
        return False

    # 下载 + 解密 + 合并
    ts_path = output_path.with_suffix(".ts")
    if not download_and_decrypt_segments(ts_urls, aes_key, iv, referer, ts_path):
        return False

    # TS → MP4
    if not remux_ts_to_mp4(ts_path, output_path):
        return False

    # 清理临时 TS
    ts_path.unlink(missing_ok=True)
    return True


def extract_audio(video_path: Path, audio_path: Path):
    """ffmpeg 从视频提取音频为 MP3"""
    if audio_path.exists():
        log.info(f"音频已存在，跳过: {audio_path}")
        return True
    if not video_path.exists():
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
        return False
    if result.returncode != 0:
        log.error(f"音频提取失败: {result.stderr[-500:]}")
        return False
    size_mb = audio_path.stat().st_size / 1024 / 1024
    log.info(f"音频提取完成: {audio_path} ({size_mb:.1f} MB)")
    return True


def process_xiaoe(task, config, creds):
    """处理小鹅通视频任务（入口）"""
    page_url = task["source_url"]
    parsed = urlparse(page_url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"
    log.info(f"处理小鹅通视频: {page_url}")

    # 1. 用 Playwright 获取标题 + 日期 + m3u8 URL
    cookie_str = creds.get("xiaoe", {}).get("browser_cookie", "")
    if not cookie_str:
        raise ValueError("credentials.yaml 中缺少 xiaoe.browser_cookie")
    title, date_prefix, m3u8_url = fetch_page_info(page_url, cookie_str)
    if not m3u8_url:
        raise RuntimeError("未能从页面捕获 m3u8 URL，请确认视频正常播放")

    # 2. 下载 m3u8
    m3u8_text = download_m3u8(m3u8_url, referer)

    # 3. 确定文件名（添加日期前缀）
    if not title:
        fid_match = re.search(r'fileId=(\d+)', m3u8_text)
        title = f"xiaoe_{fid_match.group(1)}" if fid_match else "xiaoe_video"
        log.warning(f"标题为空，使用默认名: {title}")
    base_name = f"{date_prefix}-{safe_filename(title)}"
    video_path = OUTPUT_DIR / f"{base_name}.mp4"

    # 4. 下载视频：有 AES 加密则手动解密，否则 ffmpeg 直接下载
    key_match = re.search(r'URI="([^"]+)"', m3u8_text)
    if key_match:
        aes_key = fetch_aes_key(key_match.group(1))
        if not download_video(m3u8_text, m3u8_url, aes_key, referer, video_path):
            log.error("视频下载失败")
            return
    else:
        log.info("无 AES 加密，使用 ffmpeg 直接下载")
        if not ffmpeg_download_hls(m3u8_url, referer, video_path):
            log.error("视频下载失败")
            return

    # 5. 提取音频
    extract_audio(video_path, OUTPUT_DIR / f"{base_name}.mp3")
    log.info(f"小鹅通视频处理完成: {base_name}")
