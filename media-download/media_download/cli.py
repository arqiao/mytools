"""One stdout JSON receipt per command. Diagnostics never include raw exceptions."""
import argparse
import json
from . import core


def main(argv=None):
    parser = argparse.ArgumentParser(description="视频号单链接公共下载工具")
    parser.add_argument("--version", action="version", version=core.VERSION)
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("download", "status", "doctor"):
        sub = subs.add_parser(command)
        if command != "doctor":
            sub.add_argument("url")
            sub.add_argument("--output-dir")
            sub.add_argument("--audio", action="store_true", help="提取音轨；status 中要求已提取")
            sub.add_argument("--require-audio", action="store_true")
            sub.add_argument("--codec", choices=("auto", "h264", "h265"), default="auto")
            sub.add_argument("--verify-decode", action=argparse.BooleanOptionalAction, default=True)
        if command != "status":
            sub.add_argument("--browser-profile")
            sub.add_argument("--request-timeout", type=float, default=30, help="连接/空闲读取超时，非任务总时限")
            sub.add_argument("--retries", type=int, default=1, help="每个 HTTP API 的网络/5xx重试次数，0–2；不重试媒体下载")
        if command == "download":
            sub.add_argument("--route", choices=("auto", "finder", "feed"), default="auto")
            sub.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
            sub.add_argument("--lock-timeout", type=float, default=30)
            sub.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
            sub.add_argument("--browser-fallback", action=argparse.BooleanOptionalAction, default=True)
            sub.add_argument("--login-timeout", type=float, default=300)
        if command == "doctor":
            sub.add_argument("--check-auth", action="store_true", help="显式在线身份检查；不解析/下载、不启动登录流程")
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    url, output = args.pop("url", None), args.pop("output_dir", None)
    check_auth = args.pop("check_auth", False)
    if "audio" in args:
        args["extract_audio"] = args.pop("audio")
    options = {k: v for k, v in args.items() if v is not None}
    try:
        result = core.doctor(options, check_auth=check_auth) if command == "doctor" else getattr(core, command)(url, output, options)
    except Exception:
        result = {**core.receipt(), "status": "failed", "error_code": "internal_error", "error": "命令未完成"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "success" else (3 if result["status"] in {"missing", "incomplete"} else 1)
