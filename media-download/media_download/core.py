"""视频号公共实现；不导入调用方项目，不读取业务配置。"""
import contextlib
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode

import requests
from urllib3.exceptions import HTTPError as HTTPStreamError

VERSION = "1.0.0"
SCHEMA_VERSION = 1
YUANBAO_URL = "https://yuanbao.tencent.com/"
AGENT_ID = "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1"
STATE_DIR = Path.home() / ".local/state/arqiao-tools/media-download"
DEFAULT_OUTPUT = Path.home() / "Downloads/media-download"
FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
log = logging.getLogger(__name__)


class ChannelsError(RuntimeError):
    """只传固定公开消息；禁止原始响应、异常文本和签名 URL。"""
    def __init__(self, message, code="operation_failed"):
        super().__init__(message)
        self.code = code


class LoginRequired(ChannelsError):
    def __init__(self, message="元宝认证已过期", code="auth_expired"):
        super().__init__(message, code)


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
        raise ChannelsError("请提供 https://weixin.qq.com/sph/… 视频号分享链接", "invalid_url")
    return "https://weixin.qq.com" + value.path.rstrip("/")


def artifact_paths(share_url: str, output_dir: Path) -> tuple[Path, Path, Path]:
    share_id = extract_share_url(share_url).rsplit("/", 1)[-1]
    base = output_dir.resolve() / f"wx_{share_id}"
    return base.with_suffix(".mp4"), base.with_suffix(".m4a"), base.with_suffix(".download.json")


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
    method: body === null ? 'GET' : 'POST', credentials: 'include', redirect: 'error',
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



def _response(status, data, success_statuses=(200,)):
    if status == 401:
        raise LoginRequired()
    if status == 403:
        raise ChannelsError("接口权限拒绝", "permission_denied")
    if status == 429:
        raise ChannelsError("接口限流", "rate_limited")
    if status >= 500:
        raise ChannelsError("接口服务暂不可用", "server_error")
    if status not in success_statuses:
        raise ChannelsError(f"接口 HTTP 状态异常（{status}）", "http_error")
    if not isinstance(data, dict):
        raise ChannelsError("接口字段结构发生变化", "schema_changed")
    # Known explicit auth/permission codes. Unknown codes stay business_error;
    # never infer credentials from an arbitrary server message.
    codes = [data.get(k) for k in ("code", "errCode", "errcode", "ret") if k in data]
    if isinstance(data.get("baseResp"), dict):
        codes.append(data["baseResp"].get("ret", 0))
    for code in codes:
        if str(code) in {"401", "-100", "-1001", "LOGIN_REQUIRED", "AUTH_EXPIRED"}:
            raise LoginRequired()
        if str(code) in {"403", "PERMISSION_DENIED"}:
            raise ChannelsError("接口权限拒绝", "permission_denied")
        if code not in (None, 0, "0"):
            raise ChannelsError("接口返回业务错误", "business_error")
    if data.get("error"):
        raise ChannelsError("接口返回业务错误", "business_error")
    return data


def _request(page, path, body=None, timeout=30):
    try:
        result = page.evaluate(_REQUEST_JS, {
            "path": path, "body": body, "timeout": int(timeout * 1000),
            "agentId": AGENT_ID if path.endswith("get_parse_result") else "",
        })
    except Exception:
        raise ChannelsError("元宝浏览器请求失败或超时", "network_error") from None
    if not isinstance(result, dict):
        raise ChannelsError("浏览器响应结构异常", "schema_changed")
    return _response(result.get("status", 0), None if result.get("invalidJson") else result.get("data"))


def _is_logged_in(data):
    user = data.get("data") if isinstance(data.get("data"), dict) else data
    if any(isinstance(obj.get("anonUser"), dict) and obj["anonUser"].get("isAnon")
           for obj in (data, user)):
        return False
    return any(isinstance(user.get(key), (str, int)) and not isinstance(user.get(key), bool)
               and user.get(key) for key in ("userId", "userid", "user_id", "id"))


def _http_json(session, url, *, body=None, headers=None, timeout=30, retries=1, stats=None, success_statuses=(200,)):
    for attempt in range(retries + 1):
        if stats is not None:
            stats["api_requests"] += 1
            if urlsplit(url).path.endswith("get_parse_result"):
                stats["parse_requests"] += 1
        try:
            with session.request("GET" if body is None else "POST", url, json=body,
                                 headers=headers, timeout=(min(timeout, 15), timeout),
                                 allow_redirects=False) as response:
                try:
                    data = response.json()
                except ValueError:
                    data = None
                return _response(response.status_code, data, success_statuses)
        except requests.RequestException:
            error = ChannelsError("HTTP 请求失败或超时", "network_error")
        except ChannelsError as exc:
            if exc.code not in {"server_error"}:
                raise
            error = exc
        if attempt == retries:
            raise error from None
        time.sleep(0.2)


def _media_url(url):
    if not isinstance(url, str):
        raise ChannelsError("媒体 URL 字段类型异常", "schema_changed")
    try:
        value = urlsplit(url)
        valid = (isinstance(url, str) and value.scheme == "https"
                 and value.hostname == "finder.video.qq.com"
                 and not value.username and not value.password and not value.fragment
                 and value.port in (None, 443))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ChannelsError("接口返回不受支持的媒体来源", "unsafe_media_url")
    return url


def _parsed_data(response):
    data = response.get("data")
    if data is None:
        raise ChannelsError("解析响应缺少 data", "schema_changed")
    if not isinstance(data, dict):
        raise ChannelsError("解析响应字段类型异常", "schema_changed")
    if any(data.get(k) is not None and not isinstance(data[k], str) for k in ("wx_export_id", "playable_url")):
        raise ChannelsError("解析标识字段类型异常", "schema_changed")
    if not data.get("wx_export_id") and not data.get("playable_url"):
        raise ChannelsError("空解析：没有 wx_export_id 或 playable_url", "parse_empty")
    return data


def _token_eid(parsed):
    playable = parsed.get("playable_url") or ""
    if not isinstance(playable, str):
        raise ChannelsError("playable_url 字段类型异常", "schema_changed")
    try:
        value = urlsplit(playable)
        if playable and (value.scheme != "https" or value.hostname != "channels.weixin.qq.com"
                         or value.username or value.password or value.port not in (None, 443)):
            raise ChannelsError("不受支持的 playable_url 来源", "schema_changed")
        query = parse_qs(value.query)
    except ValueError:
        raise ChannelsError("playable_url 字段异常", "schema_changed") from None
    token = (query.get("token") or [""])[0]
    eid = (query.get("eid") or query.get("exportId") or [""])[0] or parsed.get("wx_export_id") or ""
    return token, eid


def _url_codec(url):
    value = (parse_qs(urlsplit(url).query).get("fileFormat") or [""])[0].lower()
    return {"h264": "h264", "h265": "h265", "hevc": "h265"}.get(value)


def _stream_from_parse(parsed, source_url, request, feed_request, *, codec="auto", route="auto", transport="http"):
    export_id = parsed.get("wx_export_id")
    token, eid = _token_eid(parsed)
    metadata = {"title": str(parsed.get("desc") or ""), "source_url": source_url}
    first_error = None
    # Explicit codec selection uses the route which actually exposes separate streams.
    use_finder = route == "finder" or (route == "auto" and (codec == "auto" or not (token and eid)))
    if use_finder and isinstance(export_id, str) and export_id:
        try:
            media = request("/api/findergetobjecturl", {"exportId": export_id})
            nested = media.get("data") if isinstance(media.get("data"), dict) else {}
            url = media.get("videoUrl") or nested.get("videoUrl")
            if not isinstance(url, str) or not url:
                raise ChannelsError("取流响应缺少 videoUrl", "schema_changed")
            _media_url(url)
            if codec != "auto" and _url_codec(url) != codec:
                raise ChannelsError("原接口未返回指定编码", "codec_unavailable")
            return {**metadata, "url": url, "resolved_by": f"yuanbao-{transport}/findergetobjecturl"}
        except ChannelsError as exc:
            # Auth, permission, rate limits and unsafe URLs are terminal.
            if route != "auto" or exc.code not in {"schema_changed", "stream_empty", "codec_unavailable", "network_error", "server_error"}:
                raise
            first_error = exc
    elif route == "finder":
        raise ChannelsError("原接口需要 wx_export_id", "parse_missing_fields")
    if token and eid and route != "finder":
        feed = feed_request(token, eid)
        data = feed.get("data")
        info = data.get("feedInfo") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            raise ChannelsError("备用接口缺少 feedInfo", "schema_changed")
        choices = [codec] if codec != "auto" else ["h264", "base", "h265"]
        for choice in choices:
            section = info.get(choice + "VideoInfo")
            url = info.get("videoUrl") if choice == "base" else (section.get("videoUrl") if isinstance(section, dict) else None)
            if url:
                return {**metadata, "url": _media_url(url),
                        "resolved_by": f"yuanbao-{transport}/get_feed_info"}
        raise ChannelsError("备用接口没有所需视频流", "codec_unavailable" if codec != "auto" else "stream_empty")
    if first_error:
        raise first_error
    raise ChannelsError("解析结果缺少取流所需 token/eid 或 wx_export_id", "parse_missing_fields")


def _feed_request(token, eid, options, stats):
    base = "https://channels.weixin.qq.com/finder-preview"
    query = urlencode({"_rid": f"{int(time.time()):x}-{uuid.uuid4().hex[:8]}", "_pageUrl": base + "/pages/feed"})
    referer = base + "/pages/feed?" + urlencode({"entry_card_type": 48, "comment_scene": 39,
        "appid": 0, "token": token, "entry_scene": 0, "eid": eid})
    # A separate session deliberately carries NO Yuanbao cookies to channels.
    with requests.Session() as session:
        return _http_json(session, base + "/api/feed/get_feed_info?" + query,
            body={"baseReq": {"generalToken": token}, "exportId": eid},
            headers={"Origin": "https://channels.weixin.qq.com", "Referer": referer,
                     "User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=options["request_timeout"], retries=options["retries"], stats=stats, success_statuses=(200, 201))


def _resolve_on_page(page, share_url, timeout):
    """Legacy internal helper, maintained only here for old imports/tests."""
    options = normalized_options({"request_timeout": timeout})
    parsed = _parsed_data(_request(page, "/api/weixin/get_parse_result",
        {"type": "video_channel_url", "url": share_url, "scene": 1}, timeout))
    return _stream_from_parse(parsed, share_url,
        lambda path, body: _request(page, path, body, timeout),
        lambda token, eid: _feed_request(token, eid, options, new_stats()), transport="browser")


def normalized_options(options=None):
    result = {"headless": True, "browser_profile": str(STATE_DIR / "browser"),
              "request_timeout": 30, "login_timeout": 300, "retries": 1,
              "browser_fallback": True, "verify_decode": True, "codec": "auto",
              "route": "auto", "max_bytes": 64 * 1024 * 1024, "lock_timeout": 30,
              "extract_audio": False, "require_audio": False}
    result.update(options or {})
    try:
        for key in ("request_timeout", "login_timeout", "lock_timeout"):
            result[key] = float(result[key])
            if not math.isfinite(result[key]) or result[key] <= 0:
                raise ValueError()
        for key in ("max_bytes", "retries"):
            result[key] = int(result[key])
        if result["max_bytes"] <= 0 or not 0 <= result["retries"] <= 2:
            raise ValueError()
        if result["codec"] not in {"auto", "h264", "h265"} or result["route"] not in {"auto", "finder", "feed"}:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ChannelsError("参数无效（正数限额；retries 为 0–2；合法 codec/route）", "invalid_options") from None
    result["browser_profile"] = str(Path(result["browser_profile"]).expanduser().resolve())
    return result


def new_stats():
    return {"api_requests": 0, "parse_requests": 0, "browser_fallbacks": 0,
            "download_attempts": 0, "received_bytes": 0, "written_bytes": 0}


@contextlib.contextmanager
def _auth_context(options):
    profile = Path(options["browser_profile"])
    if not profile.is_dir() and options["headless"]:
        raise LoginRequired("未找到既有浏览器认证容器", "auth_missing")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ChannelsError("缺少 Playwright 依赖", "dependency_missing") from None
    try:
        with sync_playwright() as p:
            kwargs = {"headless": options["headless"]}
            exe = _find_chromium()
            if exe:
                kwargs["executable_path"] = exe
            context = p.chromium.launch_persistent_context(str(profile), **kwargs)
            try:
                yield context
            finally:
                context.close()
    except ChannelsError:
        raise
    except Exception:
        raise ChannelsError("浏览器认证容器不可用（依赖、占用或访问失败）", "browser_unavailable") from None


def _http_user(session, context, options, stats):
    cookies = context.cookies([YUANBAO_URL])
    for cookie in cookies:
        domain = cookie.get("domain", "").lstrip(".")
        if domain in {"tencent.com", "yuanbao.tencent.com"}:
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie.get("path", "/"))
    if not session.cookies:
        raise LoginRequired("认证容器内没有适用 Cookie", "auth_missing")
    session.headers.update({"Accept": "application/json", "User-Agent": "Mozilla/5.0",
        "Origin": YUANBAO_URL.rstrip("/"), "Referer": YUANBAO_URL,
        "X-Source": "web", "X-Platform": "mac", "X-Requested-With": "XMLHttpRequest"})
    data = _http_json(session, YUANBAO_URL + "api/getuserinfo",
                      timeout=options["request_timeout"], retries=options["retries"], stats=stats)
    if not _is_logged_in(data):
        raise LoginRequired("元宝返回匿名身份，既有认证未通过", "auth_expired")
    user = data.get("data") if isinstance(data.get("data"), dict) else data
    user_id = next(str(user[k]) for k in ("userId", "userid", "user_id", "id") if user.get(k))
    return user_id


def _browser_user(context, options, stats):
    page = context.new_page()
    try:
        page.goto(YUANBAO_URL, wait_until="domcontentloaded", timeout=int(options["request_timeout"] * 1000))
        page.wait_for_function("() => !!window.$webApi?.getYbCommonHeaders", timeout=int(options["request_timeout"] * 1000))
        deadline = time.monotonic() + options["login_timeout"]
        while True:
            stats["api_requests"] += 1
            try:
                if _is_logged_in(_request(page, "/api/getuserinfo", timeout=options["request_timeout"])):
                    return page
            except LoginRequired:
                pass
            if options["headless"] or time.monotonic() >= deadline:
                raise LoginRequired("浏览器未登录或认证已过期")
            page.wait_for_timeout(2000)
    except ChannelsError:
        raise
    except Exception:
        raise ChannelsError("浏览器页面未能就绪", "browser_unavailable") from None


def _resolve_context(context, share_url, options, stats):
    body = {"type": "video_channel_url", "url": share_url, "scene": 1}
    timeout = options["request_timeout"]
    with requests.Session() as session:
        try:
            user_id = _http_user(session, context, options, stats)
            def request(path, body):
                headers = {"X-AgentID": AGENT_ID, "T-UserID": user_id, "X-ID": user_id} if path.endswith("get_parse_result") else {}
                return _http_json(session, YUANBAO_URL.rstrip("/") + path, body=body, headers=headers,
                                  timeout=timeout, retries=options["retries"], stats=stats)
            parsed = _parsed_data(request("/api/weixin/get_parse_result", body))
            transport = "http"
        except ChannelsError as exc:
            can_fallback = exc.code in {"network_error", "server_error", "schema_changed"}
            # Interactive login is opt-in and uses this same single context.
            can_login = not options["headless"] and exc.code in {"auth_missing", "auth_expired"}
            if not options["browser_fallback"] or not (can_fallback or can_login):
                raise
            stats["browser_fallbacks"] += 1
            page = _browser_user(context, options, stats)
            def request(path, body):
                stats["api_requests"] += 1
                if path.endswith("get_parse_result"):
                    stats["parse_requests"] += 1
                return _request(page, path, body, timeout)
            parsed = _parsed_data(request("/api/weixin/get_parse_result", body))
            transport = "browser"
        return _stream_from_parse(parsed, share_url, request,
            lambda token, eid: _feed_request(token, eid, options, stats),
            codec=options["codec"], route=options["route"], transport=transport)


def resolve_video(share_url, options, stats=None):
    options = normalized_options(options)
    stats = stats if stats is not None else new_stats()
    share_url = extract_share_url(share_url)
    with _auth_context(options) as context:
        return _resolve_context(context, share_url, options, stats)

def _mp4_structure(path):
    size = path.stat().st_size
    with path.open("rb") as handle:
        if handle.read(12)[4:8] != b"ftyp":
            raise ChannelsError("文件不是可识别 MP4/M4A", "invalid_media")
        pos = 0
        while pos < size:
            handle.seek(pos)
            header = handle.read(8)
            if len(header) != 8:
                raise ChannelsError("MP4 box 已截断", "truncated_media")
            length = int.from_bytes(header[:4], "big")
            minimum = 8
            if length == 1:
                extended = handle.read(8)
                if len(extended) != 8:
                    raise ChannelsError("MP4 扩展 box 已截断", "truncated_media")
                length = int.from_bytes(extended, "big")
                minimum = 16
            elif length == 0:
                length = size - pos
            if length < minimum or pos + length > size:
                raise ChannelsError("MP4 box 长度与文件不符", "truncated_media")
            pos += length


def inspect_media(path, *, video=True, full_decode=True, require_audio=False):
    path = Path(path)
    try:
        _mp4_structure(path)
        ffprobe = shutil.which("ffprobe") or str(Path(FFMPEG).with_name("ffprobe"))
        probe = subprocess.run([ffprobe, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,codec_name,duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        data = json.loads(probe.stdout)
        streams = data.get("streams", [])
        kinds = {s.get("codec_type") for s in streams}
        duration = float(data.get("format", {}).get("duration", 0))
        if probe.returncode or probe.stderr.strip() or not math.isfinite(duration) or duration <= 0:
            raise ChannelsError("媒体流或时长校验失败", "invalid_media")
        if video and "video" not in kinds:
            raise ChannelsError("媒体缺少视频流", "invalid_media")
        if (require_audio or not video) and "audio" not in kinds:
            raise ChannelsError("媒体缺少有效音轨", "audio_absent")
        if video and "audio" in kinds:
            audio_durations = [float(s["duration"]) for s in streams if s.get("codec_type") == "audio" and s.get("duration") not in (None, "N/A")]
            if audio_durations and abs(audio_durations[0] - duration) > max(2, duration * .01):
                raise ChannelsError("视频内音轨时长不一致", "duration_mismatch")
        if full_decode:
            maps = ["-map", "0:v:0", "-map", "0:a:0?"] if video else ["-map", "0:a:0"]
            decoded = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
                "-i", str(path), *maps, "-progress", "pipe:1", "-f", "null", "-"],
                capture_output=True, text=True, timeout=max(300, duration * 2))
            if decoded.returncode or decoded.stderr.strip():
                raise ChannelsError("媒体完整解码失败", "decode_failed")
            end_times = re.findall(r"out_time_us=(\d+)", decoded.stdout)
            if not end_times or float(end_times[-1]) / 1e6 < duration - max(2, duration * .01):
                raise ChannelsError("解码终点未达到声明时长", "truncated_media")
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        actual_codec = {"hevc": "h265", "h264": "h264"}.get(video_stream.get("codec_name"), video_stream.get("codec_name"))
        return {"duration": duration, "has_audio": "audio" in kinds, "codec": actual_codec}
    except ChannelsError:
        raise
    except Exception:
        raise ChannelsError("媒体校验无法完成（文件、依赖或校验超时）", "verification_failed") from None


def validate_media(path, *, video, full_decode=False):
    """Compatibility API: legacy caller always requires an audio stream."""
    return inspect_media(path, video=video, full_decode=full_decode, require_audio=True)["duration"]


def _publish(partial, output):
    try:
        os.link(partial, output)  # atomic create-if-absent, never replace media
    except FileExistsError:
        raise ChannelsError("目标文件已存在，拒绝覆盖", "file_conflict") from None
    partial.unlink()


def _download_mp4(url, output, verify_decode=True, *, options=None, stats=None):
    options = normalized_options({**(options or {}), "verify_decode": verify_decode})
    stats = stats if stats is not None else new_stats()
    output = Path(output)
    _media_url(url)
    if output.exists():
        return inspect_media(output, full_decode=verify_decode, require_audio=options["require_audio"])["duration"]
    partial = output.with_name(f"{output.stem}.{uuid.uuid4().hex}.part.mp4")
    try:
        stats["download_attempts"] += 1
        with requests.get(url, headers={"Referer": YUANBAO_URL, "Accept-Encoding": "identity"},
                          stream=True, timeout=(15, options["request_timeout"]), allow_redirects=False) as response:
            if response.status_code in {401, 403}:
                raise ChannelsError("媒体签名过期或访问被拒绝", "media_access_denied")
            if 300 <= response.status_code < 400:
                raise ChannelsError("拒绝媒体重定向", "media_redirect")
            if response.status_code != 200:
                raise ChannelsError("媒体 HTTP 响应异常", "media_http_error")
            if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise ChannelsError("拒绝编码压缩的媒体响应", "invalid_media")
            content_length = response.headers.get("Content-Length")
            try:
                expected = int(content_length) if content_length is not None else None
                if expected is not None and expected < 0:
                    raise ValueError()
            except ValueError:
                raise ChannelsError("无效 Content-Length", "length_mismatch") from None
            if expected is not None and expected > options["max_bytes"]:
                raise ChannelsError("Content-Length 超过单次下载上限", "byte_limit")
            downloaded = 0
            with partial.open("xb") as handle:
                while True:
                    remaining = options["max_bytes"] - downloaded
                    if remaining == 0:
                        if expected == downloaded:
                            break
                        # Without a length header, do not consume an extra byte to
                        # determine EOF. Exactly-at-cap unknown bodies fail closed.
                        raise ChannelsError("无长度响应已达到接收上限，无法确认完整", "byte_limit")
                    chunk = response.raw.read(min(64 * 1024, remaining), decode_content=False)
                    if not chunk:
                        break
                    stats["received_bytes"] += len(chunk)
                    downloaded += len(chunk)
                    if downloaded > options["max_bytes"]:
                        raise ChannelsError("媒体响应超过单次下载接收上限", "byte_limit")
                    handle.write(chunk)
                    stats["written_bytes"] += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if downloaded < 12 or (expected is not None and downloaded != expected):
                raise ChannelsError("Content-Length 与接收字节数不一致", "length_mismatch")
        info = inspect_media(partial, full_decode=verify_decode, require_audio=options["require_audio"])
        if options["codec"] != "auto" and info["codec"] != options["codec"]:
            raise ChannelsError("实际视频编码不符合请求", "codec_mismatch")
        _publish(partial, output)
        stats["content_length"] = expected
        return info["duration"]
    except ChannelsError:
        raise
    except (requests.RequestException, HTTPStreamError):
        raise ChannelsError("媒体接收中断；临时文件未发布", "download_interrupted") from None
    except Exception:
        raise ChannelsError("媒体写入失败；临时文件未发布", "io_error") from None


def extract_audio(video_path, audio_path, err_detect=False):
    """Local-only extraction with unique staging and no-overwrite publication."""
    video_path, audio_path = Path(video_path), Path(audio_path)
    video_info = inspect_media(video_path, full_decode=False, require_audio=True)
    if audio_path.exists():
        info = inspect_media(audio_path, video=False)
    else:
        partial = audio_path.with_name(f"{audio_path.stem}.{uuid.uuid4().hex}.part.m4a")
        for codec in ("copy", "aac"):
            result = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-xerror", "-i", str(video_path),
                "-map", "0:a:0", "-vn", "-c:a", codec, "-n", str(partial)],
                capture_output=True, timeout=max(300, video_info["duration"] * 2))
            if result.returncode == 0:
                break
            partial.unlink(missing_ok=True)
        if result.returncode:
            raise ChannelsError("音轨提取失败", "audio_extract_failed")
        info = inspect_media(partial, video=False)
        if abs(info["duration"] - video_info["duration"]) > max(2, video_info["duration"] * .01):
            raise ChannelsError("提取音轨时长不匹配", "duration_mismatch")
        _publish(partial, audio_path)
    if abs(info["duration"] - video_info["duration"]) > max(2, video_info["duration"] * .01):
        raise ChannelsError("音视频时长不一致", "duration_mismatch")
    return True


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key(share_url, output):
    return hashlib.sha256((str(Path(output).resolve()) + "\n" + share_url).encode()).hexdigest()


@contextlib.contextmanager
def asset_lock(share_url, output, timeout=30, state_dir=None):
    root = Path(state_dir or STATE_DIR) / "locks"
    root.mkdir(parents=True, exist_ok=True)
    with (root / (_key(share_url, output) + ".lock")).open("a") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ChannelsError("同目录同资产正在处理", "asset_busy") from None
                time.sleep(.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def receipt(share_url=None):
    return {"schema_version": SCHEMA_VERSION, "tool_version": VERSION, "status": "incomplete",
        "asset_id": "wx_" + share_url.rsplit("/", 1)[-1] if share_url else None,
        "source_url": share_url, "resolved_by": None, "video_path": None, "audio_path": None,
        "audio_status": "unknown", "duration": None, "bytes": 0, "sha256": None,
        "verification": {}, "reused": False, "error_code": None}


def _existing_manifest(path, source_url, video, audio):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("status") != "success"
            or data.get("source_url") != source_url or data.get("video_path") != str(video)
            or data.get("audio_path") not in (None, str(audio))):
            raise ValueError()
        return data
    except (ValueError, OSError):
        raise ChannelsError("现有清单与本次资产不匹配", "manifest_conflict") from None


def _inspect_asset(share_url, output, options):
    video, audio, manifest = artifact_paths(share_url, Path(output))
    old = _existing_manifest(manifest, share_url, video, audio)
    if not video.is_file():
        if old or audio.exists():
            raise ChannelsError("现有资产缺少视频", "asset_incomplete")
        return None, old
    info = inspect_media(video, full_decode=options["verify_decode"], require_audio=options["require_audio"])
    if options["codec"] != "auto" and info["codec"] != options["codec"]:
        raise ChannelsError("既有文件编码不符合请求，拒绝覆盖", "codec_mismatch")
    digest = _sha256(video)
    if old.get("sha256") and old["sha256"] != digest:
        raise ChannelsError("视频摘要与成功清单不符", "file_conflict")
    audio_info = None
    if audio.exists():
        if not info["has_audio"]:
            raise ChannelsError("无音轨视频旁存在无法关联的音频", "file_conflict")
        audio_info = inspect_media(audio, video=False, full_decode=options["verify_decode"])
        if abs(audio_info["duration"] - info["duration"]) > max(2, info["duration"] * .01):
            raise ChannelsError("音视频时长不一致", "duration_mismatch")
        if old.get("audio_sha256") and old["audio_sha256"] != _sha256(audio):
            raise ChannelsError("音频摘要与成功清单不符", "file_conflict")
    result = {**receipt(share_url), "video_path": str(video), "audio_path": str(audio) if audio_info else None,
        "audio_status": "extracted" if audio_info else ("missing" if old.get("audio_path") else ("not_requested" if info["has_audio"] else "absent")),
        "duration": info["duration"], "duration_seconds": info["duration"],
        "bytes": video.stat().st_size, "sha256": digest, "codec": info["codec"],
        "audio_sha256": _sha256(audio) if audio_info else None,
        "verification": {"mp4_structure": True, "streams": True, "duration": True,
            "full_decode": options["verify_decode"], "audio_full_decode": bool(audio_info) and options["verify_decode"]},
        "resolved_by": old.get("resolved_by") or "existing-file", "reused": True}
    return result, old


def download(share_url, output_dir=None, options=None, *, title=""):
    """Stable Python API: returns v1 receipt; all task errors are safe receipts."""
    result = receipt()
    stats = new_stats()
    try:
        share_url = extract_share_url(share_url)
        result = receipt(share_url)
        options = normalized_options(options)
        output = Path(output_dir or DEFAULT_OUTPUT).expanduser().resolve()
        with asset_lock(share_url, output, options["lock_timeout"], options.get("state_dir")):
            output.mkdir(parents=True, exist_ok=True)
            video, audio, manifest = artifact_paths(share_url, output)
            existing, old = _inspect_asset(share_url, output, options)
            reused = existing is not None
            metadata = {}
            if not reused:
                metadata = resolve_video(share_url, options, stats)
                _download_mp4(metadata["url"], video, options["verify_decode"], options=options, stats=stats)
            info = inspect_media(video, full_decode=False, require_audio=options["require_audio"])
            if (options["extract_audio"] or old.get("audio_path")) and info["has_audio"] and not audio.exists():
                if not extract_audio(video, audio):
                    raise ChannelsError("音轨提取失败", "audio_extract_failed")
            result, _ = _inspect_asset(share_url, output, options)
            result.update(status="success", reused=reused, completed_at=time.time(),
                title=title or metadata.get("title") or old.get("title") or video.stem,
                resolved_by=metadata.get("resolved_by") or result["resolved_by"])
            result["verification"]["content_length"] = ("matched" if stats.get("content_length") is not None
                else old.get("verification", {}).get("content_length", "not_available"))
            result["activity"] = stats.copy()
            _write_json(manifest, result)
            # Scoped registry: no cross-directory lookup or title-based sharing.
            index = Path(options.get("state_dir") or STATE_DIR) / "assets" / (_key(share_url, output) + ".json")
            _write_json(index, result)
    except ChannelsError as exc:
        result.update(status="failed", error_code=exc.code, error=str(exc))
    except Exception:
        result.update(status="failed", error_code="internal_error", error="任务未完成；详情不得包含原始认证响应")
    result["activity"] = stats
    return result


def status(share_url, output_dir=None, options=None):
    """Read-only: no auth access, network, extraction, registry or receipt writes."""
    result = receipt()
    try:
        share_url = extract_share_url(share_url)
        result = receipt(share_url)
        options = normalized_options(options)
        output = Path(output_dir or DEFAULT_OUTPUT).expanduser().resolve()
        found, old = _inspect_asset(share_url, output, options)
        if found:
            result = found
            complete = bool(old) and found["audio_status"] != "missing" and (not options["extract_audio"] or found["audio_status"] in {"extracted", "absent"})
            result["status"] = "success" if complete else "incomplete"
        else:
            partials = list(output.glob(result["asset_id"] + ".*.part.*")) if output.is_dir() else []
            result["status"] = "incomplete" if partials else "missing"
    except ChannelsError as exc:
        result.update(status="failed", error_code=exc.code, error=str(exc))
    except Exception:
        result.update(status="failed", error_code="io_error", error="本地产物状态检查失败")
    result["activity"] = new_stats()
    return result


def doctor(options=None, *, check_auth=False):
    result = receipt()
    try:
        options = normalized_options(options)
        import importlib.util
        result["environment"] = {"ffmpeg": Path(FFMPEG).is_file(), "ffprobe": bool(shutil.which("ffprobe")),
            "playwright": importlib.util.find_spec("playwright") is not None,
            "chromium": bool(_find_chromium()), "profile_exists": Path(options["browser_profile"]).is_dir()}
        result["auth_status"] = "unchecked"
        result["status"] = "success" if all(result["environment"][k] for k in ("ffmpeg", "ffprobe", "playwright", "chromium")) else "failed"
        if result["status"] == "failed":
            result["error_code"] = "dependency_missing"
        if check_auth:
            options["headless"] = True
            stats = new_stats()
            result["activity"] = stats
            with _auth_context(options) as context, requests.Session() as session:
                _http_user(session, context, options, stats)
            result["auth_status"] = "valid"
    except ChannelsError as exc:
        result.update(status="failed", error_code=exc.code, error=str(exc), auth_status=exc.code)
    return result
