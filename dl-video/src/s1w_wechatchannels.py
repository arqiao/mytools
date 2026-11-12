#!/usr/bin/env python3
"""视频号独立 S1：元宝网页会话 → wx_export_id → findergetobjecturl → MP4/M4A。

python src/s1w_wechatchannels.py 'https://weixin.qq.com/sph/ALRGH1Htvi'
python src/s1w_wechatchannels.py --config cfg/input.yaml --headless

首次登录使用可见浏览器，仅访问元宝网页，不操作微信客户端。
成功后写入无媒体签名的 *.download.json，供流水线精确交接音频。
"""
import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
import yaml
from modules.config_utils import load_config
from modules.ffmpeg_utils import FFMPEG, extract_audio

PROJECT_DIR = Path(__file__).resolve().parent.parent
CFG_DIR = PROJECT_DIR / "cfg"
YUANBAO_URL = "https://yuanbao.tencent.com/"
AGENT_ID = "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1"
log = logging.getLogger(__name__)


class ChannelsError(RuntimeError):
    """只携带可公开记录的错误，不包含 Cookie、原始响应或签名媒体 URL。"""


class LoginRequired(ChannelsError):
    """可见浏览器允许用户完成登录；不用于重试网络或业务错误。"""


def extract_share_url(text: str) -> str:
    try:
        value = urlsplit((text or "").strip())
        valid = (value.scheme in {"http", "https"}
                 and value.hostname in {"weixin.qq.com", "www.weixin.qq.com"}
                 and not value.username and not value.password
                 and value.port in {None, 80, 443}
                 and re.fullmatch(r"/sph/[A-Za-z0-9_-]+/?", value.path))
    except ValueError:
        valid = False
    if not valid:
        raise ChannelsError("请提供 https://weixin.qq.com/sph/… 视频号分享链接")
    return "https://weixin.qq.com" + value.path.rstrip("/")


def artifact_paths(share_url: str, output_dir: Path) -> tuple[Path, Path, Path]:
    share_id = extract_share_url(share_url).rsplit("/", 1)[-1]
    base = output_dir.resolve() / f"wx_{share_id}"
    return base.with_suffix(".mp4"), base.with_suffix(".m4a"), base.with_suffix(".download.json")


def project_path(value) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_DIR / path).resolve()


def wechat_output_dir(config) -> Path:
    return project_path(config.get("path_wechat_dir") or "out-wechat")


def _find_chromium() -> str | None:
    """自动查找已安装的 Playwright Chromium 可执行文件（规避版本不匹配）。

    playwright 包的期望版本与缓存浏览器版本可能不一致，直接传 executable_path
    可跳过包的版本检查。优先环境变量 PLAYWRIGHT_CHROMIUM_PATH，其次扫描缓存目录
    中版本号最新的 chromium。
    """
    override = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip()
    if override and Path(override).exists():
        return override

    patterns = [
        "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chrome-win/chrome.exe",
        "chrome-win/Google Chrome for Testing.exe",
        "chrome-linux/chrome",
    ]
    candidates: list[tuple[int, str]] = []
    for cache in (Path.home() / "Library" / "Caches" / "ms-playwright",
                  Path.home() / ".cache" / "ms-playwright"):
        if not cache.is_dir():
            continue
        for d in cache.iterdir():
            if not d.is_dir() or not d.name.startswith("chromium-"):
                continue
            version_part = d.name.split("-")[-1]
            try:
                version = int(version_part)
            except ValueError:
                version = 0
            for pat in patterns:
                exe = d / pat
                if exe.is_file():
                    candidates.append((version, str(exe)))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


# 同一浏览器会话自动承接 Cookie 刷新；异常和日志不输出原始接口响应。
_REQUEST_JS = r"""async ({path, body, agentId, timeout}) => {
  const api = window.$webApi;
  const request = {url: path, headers: {
    ...(api?.getYbCommonHeaders?.() || {}),
    'Accept': 'application/json, text/plain, */*', 'X-Source': 'web'
  }};
  if (body !== null) request.headers['Content-Type'] = 'application/json';
  if (agentId) request.headers['X-AgentID'] = agentId;
  if (agentId && window.__dlVideoUserId) {
    request.headers['T-UserID'] = window.__dlVideoUserId;
    request.headers['X-ID'] = window.__dlVideoUserId;
  }
  if (api?.setContextualRequestHeaders) await api.setContextualRequestHeaders(request);
  const response = await fetch(path, {
    method: body === null ? 'GET' : 'POST', credentials: 'include',
    headers: request.headers, body: body === null ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeout)
  });
  let data;
  try { data = await response.json(); }
  catch (_) { return {status: response.status, invalidJson: true}; }
  if (path === '/api/getuserinfo') {
    const user = data?.data || data || {};
    window.__dlVideoUserId = String(user.userId || user.userid || user.user_id || user.id || '');
  }
  return {status: response.status, data};
}"""


def _request(page, path: str, body=None, timeout=30) -> dict:
    try:
        result = page.evaluate(_REQUEST_JS, {
            "path": path, "body": body, "timeout": int(timeout * 1000),
            "agentId": AGENT_ID if path.endswith("get_parse_result") else "",
        })
    except Exception:
        raise ChannelsError("元宝请求失败或超时，请检查网络和网页登录状态") from None
    if result.get("status") == 401:
        raise LoginRequired("元宝登录失效；请用可见浏览器重新登录")
    if result.get("status") == 403:
        raise ChannelsError("元宝访问被拒绝（HTTP 403），停止请求")
    if result.get("status") != 200 or result.get("invalidJson"):
        raise ChannelsError(f"元宝接口响应异常（HTTP {result.get('status')}）")
    data = result.get("data")
    if not isinstance(data, dict):
        raise ChannelsError("元宝接口响应结构异常")
    if data.get("code") not in (None, 0, "0") or data.get("error"):
        raise ChannelsError("元宝接口返回业务错误，未取得可下载媒体")
    return data


def _is_logged_in(data: dict) -> bool:
    user = data.get("data") if isinstance(data.get("data"), dict) else data
    if any(isinstance(obj.get("anonUser"), dict) and obj["anonUser"].get("isAnon")
           for obj in (data, user)):
        return False
    return any(user.get(key) for key in ("userId", "userid", "user_id", "id"))


def _resolve_on_page(page, share_url: str, timeout: int) -> dict:
    parsed = _request(page, "/api/weixin/get_parse_result", {
        "type": "video_channel_url", "url": share_url, "scene": 1,
    }, timeout)
    data = parsed.get("data") or {}
    export_id = data.get("wx_export_id") if isinstance(data, dict) else None
    if not isinstance(export_id, str) or not export_id:
        raise ChannelsError("元宝未返回 wx_export_id；空解析不能下载，请检查分享链接是否仍可用")
    media = _request(page, "/api/findergetobjecturl", {"exportId": export_id}, timeout)
    nested = media.get("data") if isinstance(media.get("data"), dict) else {}
    url = media.get("videoUrl") or nested.get("videoUrl")
    if not isinstance(url, str) or not url:
        raise ChannelsError("元宝未返回 videoUrl，无法下载")
    try:
        parsed_url = urlsplit(url)
        valid = (parsed_url.scheme == "https" and parsed_url.hostname == "finder.video.qq.com"
                 and not parsed_url.username and not parsed_url.password
                 and parsed_url.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise ChannelsError("元宝返回了不受支持的媒体来源")
    return {"url": url, "title": str(data.get("desc") or ""), "source_url": share_url}


def resolve_video(share_url: str, options: dict) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ChannelsError("缺少 Playwright；请安装 playwright 及其 Chromium 浏览器") from None
    headless = bool(options.get("headless", False))
    timeout = int(options.get("request_timeout", 30))
    login_timeout = int(options.get("login_timeout", 300))
    if timeout <= 0 or login_timeout <= 0:
        raise ChannelsError("request_timeout 和 login_timeout 必须大于 0")
    profile = project_path(options.get("browser_profile", ".browser_data_wxchannels"))
    try:
        with sync_playwright() as p:
            kwargs = {"headless": headless}
            exe = _find_chromium()
            if exe:
                kwargs["executable_path"] = exe
            browser = p.chromium.launch_persistent_context(str(profile), **kwargs)
            try:
                page = browser.new_page()
                page.goto(YUANBAO_URL, wait_until="domcontentloaded", timeout=60000)
                deadline = time.monotonic() + login_timeout
                log.info("检查元宝网页登录态（首次登录请在浏览器中完成）")
                while True:
                    try:
                        logged_in = _is_logged_in(_request(page, "/api/getuserinfo", timeout=timeout))
                    except LoginRequired:
                        if headless:
                            raise
                        logged_in = False
                    if logged_in:
                        break
                    if headless or time.monotonic() >= deadline:
                        raise ChannelsError("元宝未登录；请使用 --no-headless 后重新运行并登录")
                    page.wait_for_timeout(5000)
                return _resolve_on_page(page, share_url, timeout)
            finally:
                browser.close()
    except ChannelsError:
        raise
    except Exception:
        raise ChannelsError("元宝浏览器启动或访问失败；检查 Chromium、网络及 profile 是否被占用") from None


def validate_media(path: Path, *, video: bool, full_decode=False) -> float:
    ffprobe = shutil.which("ffprobe") or str(Path(FFMPEG).with_name(
        "ffprobe.exe" if os.name == "nt" else "ffprobe"))
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                                 "format=duration:stream=codec_type", "-of", "json", str(path)],
                                capture_output=True, text=True, timeout=60)
        data = json.loads(result.stdout)
        kinds = {s.get("codec_type") for s in data.get("streams", [])}
        duration = float(data.get("format", {}).get("duration", 0))
        if result.returncode or duration <= 0 or "audio" not in kinds or (video and "video" not in kinds):
            raise ValueError("invalid media")
        if full_decode:
            log.info("完整解码校验：%s", path.name)
            decoded = subprocess.run([FFMPEG, "-v", "error", "-xerror", "-err_detect", "explode",
                                      "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                                     capture_output=True, timeout=max(300, duration * 2))
            if decoded.returncode:
                raise ValueError("decode failed")
        return duration
    except Exception:
        raise ChannelsError(f"媒体校验失败：{path.name}（需要有效音轨、时长及 ffmpeg/ffprobe）") from None


def _download_mp4(url: str, output: Path, verify_decode: bool) -> float:
    if output.exists():
        return validate_media(output, video=True, full_decode=verify_decode)
    partial = output.with_name(f"{output.stem}.{uuid.uuid4().hex[:10]}.part.mp4")
    try:
        with requests.get(url, headers={"Referer": YUANBAO_URL, "Accept-Encoding": "identity"},
                          stream=True, timeout=(15, 60), allow_redirects=False) as response:
            if response.status_code != 200:
                raise ChannelsError(f"媒体下载失败（HTTP {response.status_code}），请重新解析获取地址")
            expected = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            last_report = time.monotonic()
            with partial.open("xb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if time.monotonic() - last_report >= 5:
                        log.info("下载 %.1f MiB / %s", downloaded / 1048576,
                                 f"{expected / 1048576:.1f} MiB" if expected else "未知大小")
                        last_report = time.monotonic()
            if downloaded < 12 or (expected and downloaded != expected):
                raise ChannelsError("媒体下载不完整，未生成正式 MP4")
            with partial.open("rb") as handle:
                if handle.read(12)[4:8] != b"ftyp":
                    raise ChannelsError("媒体响应不是可识别的 MP4，未生成正式文件")
        duration = validate_media(partial, video=True, full_decode=verify_decode)
        partial.replace(output)
        return duration
    except ChannelsError:
        raise
    except Exception:
        raise ChannelsError("媒体下载中断或写入失败；未完成的 .part.mp4 保留，正式文件未更新") from None


def process_wechat_channels(task, config, creds=None):
    share_url = extract_share_url(task.get("source_huifang_url", ""))
    output_dir = wechat_output_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    video, audio, manifest = artifact_paths(share_url, output_dir)
    options = config.get("wechat_channels") or {}
    verify_decode = bool(options.get("verify_decode", True))
    metadata = {"title": task.get("title") or "", "source_url": share_url}
    if video.exists():
        log.info("校验并复用视频：%s", video.name)
        duration = validate_media(video, video=True, full_decode=verify_decode)
    else:
        metadata = resolve_video(share_url, options)
        duration = _download_mp4(metadata["url"], video, verify_decode)
    if not audio.exists():
        if not extract_audio(video, audio, err_detect=False):
            raise ChannelsError("音频提取失败，未写入成功交接清单")
    audio_duration = validate_media(audio, video=False)
    if abs(audio_duration - duration) > max(2, duration * 0.01):
        raise ChannelsError("音视频时长不一致，未写入成功交接清单")
    result = {
        "schema_version": 1, "status": "success", "source_url": share_url,
        "title": task.get("title") or metadata.get("title") or video.stem,
        "video_path": str(video), "audio_path": str(audio), "duration_seconds": duration,
        "completed_at": time.time(),
    }
    temporary = manifest.with_name(f"{manifest.name}.{uuid.uuid4().hex[:10]}.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    log.info("视频号下载完成：%s / %s", video.name, audio.name)
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="视频号下载：元宝网页解析 → MP4 + M4A")
    parser.add_argument("urls", nargs="*", help="视频号 sph 分享链接；不传则读取配置 tasks")
    parser.add_argument("--config", default=str(CFG_DIR / "input.yaml"), help="任务及覆盖配置 YAML")
    parser.add_argument("--output-dir", help="覆盖 path_wechat_dir（默认 out-wechat/）")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=None,
                        help="复用登录态无窗口运行；首次登录使用 --no-headless")
    parser.add_argument("--browser-profile", help="元宝浏览器登录态目录")
    parser.add_argument("--login-timeout", type=int, help="可见浏览器登录等待秒数")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        config, creds = load_config(include_input=False)
        config_path = project_path(args.config)
        with config_path.open(encoding="utf-8") as handle:
            config.update(yaml.safe_load(handle) or {})
        options = dict(config.get("wechat_channels") or {})
        for key in ("headless", "browser_profile", "login_timeout"):
            if getattr(args, key) is not None:
                options[key] = getattr(args, key)
        config["wechat_channels"] = options
        if args.output_dir:
            config["path_wechat_dir"] = args.output_dir
        tasks = ([{"source_huifang_url": url} for url in args.urls] if args.urls else
                 [t for t in (config.get("tasks") or []) if t.get("source_type") == "wechat_channels"])
        if not tasks:
            raise ChannelsError("没有视频号任务；请传入分享链接或配置 source_type: wechat_channels")
        for task in tasks:
            process_wechat_channels(task, config, creds)
        return 0
    except ChannelsError as exc:
        log.error("%s", exc)
    except Exception as exc:
        log.error("视频号任务失败（%s）；未继续后续步骤", type(exc).__name__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
