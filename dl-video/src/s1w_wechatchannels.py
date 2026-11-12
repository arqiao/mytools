#!/usr/bin/env python3
"""dl-video 视频号薄兼容层；唯一下载实现位于 arqiao-tools/media-download。"""
import argparse
import logging
import sys
from pathlib import Path
import yaml
from modules.config_utils import load_config

_TOOL = Path.home() / ".local/share/arqiao-tools/media-download"
if not (_TOOL / "media_download/core.py").is_file():
    raise RuntimeError("缺少公共 media-download 工具；请查看 doc/公共下载工具迁移交接.md")
sys.path.insert(0, str(_TOOL))
from media_download import core as _core
from media_download.core import (ChannelsError, LoginRequired, artifact_paths, extract_share_url,
    resolve_video, validate_media, extract_audio, FFMPEG, _request, _is_logged_in,
    _resolve_on_page, _download_mp4, _find_chromium)

PROJECT_DIR = Path(__file__).resolve().parent.parent
CFG_DIR = PROJECT_DIR / "cfg"
log = logging.getLogger(__name__)


def project_path(value) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_DIR / path).resolve()


def wechat_output_dir(config) -> Path:
    return project_path(config.get("path_wechat_dir") or "out-wechat")


def process_wechat_channels(task, config, creds=None):
    options = dict(config.get("wechat_channels") or {})
    options["browser_profile"] = str(project_path(options.get("browser_profile", ".browser_data_wxchannels")))
    options.setdefault("headless", False)
    # Legacy long lessons use a 16 GiB cap; public CLI defaults to 64 MiB.
    options.setdefault("max_bytes", 16 * 1024 * 1024 * 1024)
    options.update(extract_audio=True, require_audio=True)
    result = _core.download(task.get("source_huifang_url", ""), wechat_output_dir(config),
                            options, title=task.get("title") or "")
    if result["status"] != "success":
        raise ChannelsError(result.get("error", "视频号任务失败"), result["error_code"])
    log.info("视频号下载完成：%s / %s", Path(result["video_path"]).name, Path(result["audio_path"]).name)
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
