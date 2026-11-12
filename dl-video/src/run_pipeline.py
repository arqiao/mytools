#!/usr/bin/env python3
# 参数调用示例:
#   python src/run_pipeline.py                       # 按 cfg/input.yaml tasks 顺序逐条执行
#   python src/run_pipeline.py --wechat-url 'https://weixin.qq.com/sph/ALRGH1Htvi' --wechat-headless --no-notify
#   python src/run_pipeline.py --config cfg/input.wechat.example.yaml --subtitle-engine whisper --print-plan
#   python src/run_pipeline.py --live-url '<url_huifang>' --wiki-url '<source_wiki_url>' --target-wiki-url '<target_wiki_url>'
#   # URL 含 ? 或 & 时，必须给整个 URL 加引号，避免 zsh 将其当作通配符或命令分隔符。
#       [--full-copy | --exclude-before-heading <heading> --exclude-after-heading <heading>
#        | --include-start-heading <heading> --include-end-heading <heading>] [--no-attachments]
#   以下示例默认运行到 S5；传入中间产物时，会跳过它之前的步骤：
#   python src/run_pipeline.py --video-file out-yitang/demo.mp4  # 已有原始视频：从 S3 开始，不生成持久化音频
#   python src/run_pipeline.py --audio-file out-yitang/demo.mp3  # 从 S3 开始：默认 Qwen3-ASR → S4 校订 → S5
#   python src/run_pipeline.py --audio-file out-yitang/demo.mp3 --subtitle-engine qwen3-asr  # 从 S3 开始：同上，显式指定 Qwen3-ASR
#   python src/run_pipeline.py --audio-file out-yitang/demo.mp3 --subtitle-engine whisper  # 从 S3 开始：Whisper 识别 → S4 校订 → S5
#   python src/run_pipeline.py --subtitle out-yitang/demo.mlx_l3.srt  # 从 S4 开始：校订现有字幕 → S5，不执行 S3
#   python src/run_pipeline.py --fix-subtitle out-yitang/demo.mlx_l3_fix.srt  # 从 S5 开始：只执行 S5
#   python src/run_pipeline.py --audio-file output/demo.m4a --subtitle-engine whisper --print-plan  # 只查看计划，不执行
"""S1-S5 流水线脚本。

用法:
  # 完整流水线（从 cfg/input.yaml 读取 tasks，并按顺序逐条执行）
  python src/run_pipeline.py

  # 直接传入回放 URL、源 wiki URL、目标飞书 wiki URL
  python src/run_pipeline.py --live-url '<url_huifang>' --wiki-url '<source_wiki_url>' --target-wiki-url '<target_wiki_url>'
      [--full-copy | --exclude-before-heading <heading> --exclude-after-heading <heading>
       | --include-start-heading <heading> --include-end-heading <heading>] [--no-attachments]

  # 一堂文稿范围由任务级 full_copy / 四个标题字段控制；命令行参数优先于任务配置。

  # 从中间产物继续
  python src/run_pipeline.py --video-file out-yitang/demo.mp4  # S3 → S4 → S5；不生成持久化音频
  python src/run_pipeline.py --audio-file out-yitang/demo.mp3  # S3 → S4 → S5；默认 Qwen3-ASR
  python src/run_pipeline.py --audio-file out-yitang/demo.mp3 --subtitle-engine qwen3-asr  # S3 → S4 → S5；显式指定 Qwen3-ASR
  python src/run_pipeline.py --audio-file out-yitang/demo.mp3 --subtitle-engine whisper  # S3 → S4 → S5
  python src/run_pipeline.py --subtitle out-yitang/demo.mlx_l3.srt  # S4 → S5；不执行 S3，仍会校订
  python src/run_pipeline.py --fix-subtitle out-yitang/demo.mlx_l3_fix.srt  # 仅执行 S5

  # 兼容旧用法
  python src/run_pipeline.py -s2
  python src/run_pipeline.py out-yitang/demo.mp3
  python src/run_pipeline.py out-yitang/demo.mlx_l3.srt

流水线步骤:
  S1 视频下载 → S2 教学文档 → S3 字幕识别 → S4 字幕修订 → S5 生成Addon
"""

import argparse
import codecs
import io
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml
from modules.config_utils import TARGET_WIKI_URL_ERROR, is_valid_target_wiki_url
from s1w_wechatchannels import artifact_paths, extract_share_url, wechat_output_dir, ChannelsError

# Windows 控制台 UTF-8 模式
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"

# 飞书群通知配置
FEISHU_NOTIFY_CHAT_ID = "oc_42e15484900d10f7f30bcd18d72d1397"

STEP_ORDER = ["s1", "s2", "s3", "s4", "s5"]
STEP_NAMES = {
    "s1": "视频下载",
    "s2": "教学文档",
    "s3": "字幕识别",
    "s4": "字幕修订",
    "s5": "生成Addon",
}


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_pipeline_config(config_path: Path) -> dict:
    """读取 config.yaml，并用 input yaml 覆盖同名顶层配置。"""
    config = _read_yaml(CFG_DIR / "config.yaml")
    config.update(_read_yaml(config_path))
    return config


def is_yitang_live_url(url: str) -> bool:
    return "air.yitang.top/live/" in (url or "") or "yitang.top/live/" in (url or "")


def is_yitang_wiki_url(url: str) -> bool:
    return "yitang.top" in (url or "") or "yitanger.feishu.cn" in (url or "")


def is_wechat_task(task) -> bool:
    if task.get("source_type") == "wechat_channels":
        return True
    try:
        extract_share_url(task.get("source_huifang_url", ""))
        return True
    except ChannelsError:
        return False


def get_s3_suffixes(config):
    """返回 s3 字幕后缀，优先使用 config.yaml 配置。"""
    suffixes = list(config.get("s3_engine_suffix", {}).values())
    if suffixes:
        return suffixes
    return [
        ".wm", ".wl", ".ws", ".wt", ".mlx_turbo", ".mlx_l3", ".mlx_m", ".mlx_s",
        ".mlx_t", ".xunfei", ".aliyun", ".doubao", ".seed_asr", ".feishu", ".tencent",
        "_wm", "_wl", "_ws", "_wt", "_xunfei", "_aliyun", "_doubao", "_seed_asr", "_feishu",
        "_tencent",
    ]


def get_expected_s3_suffix(config, backend: str, model: str, engine: str = "whisper") -> str:
    suffix_map = config.get("s3_engine_suffix", {})
    if engine == "doubao-seed-asr":
        return suffix_map.get("doubao_seed_asr", "")
    if engine == "qwen3-asr":
        return suffix_map.get("qwen3_asr", ".qwen3_asr")
    normalized = (model or "").replace("-", "_")
    if backend == "mlx":
        return suffix_map.get(f"mlx_whisper_{normalized}", "")
    return suffix_map.get(f"whisper_{normalized}", "")


def newest_file(directory: Path, patterns, after_ts: float | None = None, allow_old=False):
    candidates = []
    if not directory.exists():
        return None
    for pattern in patterns:
        candidates.extend(directory.glob(pattern))
    files = [p for p in candidates if p.is_file()]
    if after_ts is not None:
        recent = [p for p in files if p.stat().st_mtime >= after_ts - 2]
        if recent:
            files = recent
        elif not allow_old:
            return None
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime).absolute()


def find_s3_audio(directory: Path, after_ts: float | None = None, allow_old=False):
    """为 S3 查找音频：优先 M4A，未找到时才使用 MP3。"""
    for pattern in ("*.m4a", "*.mp3"):
        path = newest_file(directory, [pattern], after_ts, allow_old)
        if path:
            return path
    return None


def strip_known_artifact_suffix(stem: str, suffixes, s4_suffix="_fix") -> str:
    """从字幕/修订字幕文件名中还原课程前缀。"""
    ordered_suffixes = sorted((s for s in suffixes if s), key=len, reverse=True)
    for suffix in ordered_suffixes:
        fix_suffix = f"{suffix}{s4_suffix}"
        if stem.endswith(fix_suffix):
            return stem[:-len(fix_suffix)]
    for suffix in ordered_suffixes:
        if stem.endswith(suffix):
            return stem[:-len(suffix)]
    if s4_suffix and stem.endswith(s4_suffix):
        return stem[:-len(s4_suffix)]
    return stem


def find_related_file(directory: Path, base_stem: str, patterns, after_ts: float | None = None):
    """只在同一前缀下查找关联文件，避免拿目录里其他任务的最新产物。"""
    if not directory.exists() or not base_stem:
        return None

    candidates = []
    for pattern in patterns:
        candidates.extend(directory.glob(pattern.format(base=base_stem)))

    files = [p for p in candidates if p.is_file() and p.stem.startswith(base_stem)]
    if after_ts is not None:
        recent = [p for p in files if p.stat().st_mtime >= after_ts - 2]
        if recent:
            files = recent
    if not files:
        return None

    exact_stem_matches = [p for p in files if p.stem == base_stem]
    if exact_stem_matches:
        files = exact_stem_matches
    return max(files, key=lambda p: p.stat().st_mtime).absolute()


def find_transcript_for_base(directory: Path, base_stem: str, after_ts: float | None = None):
    """按同一课程前缀查找教学文档；优先真实 wiki，再退到本轮生成的日志类 md。"""
    if not directory.exists() or not base_stem:
        return None

    for name in (f"{base_stem}_wiki.md", f"{base_stem}.md"):
        path = directory / name
        if path.is_file():
            return path.absolute()

    candidates = [
        p for p in directory.glob(f"{base_stem}*.md")
        if p.is_file()
        and not p.stem.endswith("_信息拓展")
        and not p.stem.endswith("_精华摘要")
    ]
    if after_ts is not None:
        recent = [p for p in candidates if p.stat().st_mtime >= after_ts - 2]
        if recent:
            candidates = recent
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime).absolute()


def find_srt_for_audio(mp3_path: Path, output_dir: Path, suffixes, preferred_suffix=""):
    """按优先级查找音频对应的字幕文件。"""
    if preferred_suffix:
        preferred = mp3_path.with_name(f"{mp3_path.stem}{preferred_suffix}.srt")
        if preferred.exists():
            return preferred.absolute()

    ori_srt = mp3_path.with_name(f"{mp3_path.stem}_ori.srt")
    if ori_srt.exists():
        return ori_srt.absolute()

    for suffix in suffixes:
        srt_path = mp3_path.with_name(f"{mp3_path.stem}{suffix}.srt")
        if srt_path.exists():
            return srt_path.absolute()

    return None


def load_credentials():
    try:
        from secrets_loader import load as _secrets_load
    except ImportError:
        sys.path.insert(0, "/Volumes/DATADRIVE/workspace/sys")
        from secrets_loader import load as _secrets_load
    return _secrets_load("gkeys", "gfeishu", "gtokens")


def notify_feishu(creds, msg: str):
    """发送文本消息到飞书群（使用 app token）。"""
    try:
        fs = creds.get("feishu", {})
        app_resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": fs.get("app_id", ""), "app_secret": fs.get("app_secret", "")},
            timeout=10,
        ).json()

        app_token = app_resp.get("app_access_token")
        if not app_token:
            print("  [飞书通知失败] 获取 app token 失败")
            return

        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={
                "Authorization": f"Bearer {app_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "receive_id": FEISHU_NOTIFY_CHAT_ID,
                "msg_type": "text",
                "content": json.dumps({"text": msg}, ensure_ascii=False),
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"  [飞书通知失败] {data.get('msg')}")
        else:
            print("  [飞书通知已发送]")
    except Exception as e:
        print(f"  [飞书通知异常] {e}")


def stream_process_output(proc, log_f):
    """转发子进程输出，合并 \r 进度，避免终端/日志刷屏。"""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    last_sep = "\n"
    progress_active = False
    last_progress_print_at = 0.0

    def emit_progress(text, final=False):
        nonlocal progress_active, last_progress_print_at
        if not text:
            return
        now = time.time()
        should_print = final or now - last_progress_print_at >= 5
        if not should_print:
            return
        if sys.stdout.isatty():
            print("\r" + text[:180], end="", flush=True)
        else:
            print(text[:180])
        log_f.write(text + "\n")
        log_f.flush()
        progress_active = True
        last_progress_print_at = now

    def emit_normal(text):
        nonlocal progress_active
        if progress_active and sys.stdout.isatty():
            print()
            progress_active = False
        print(text)
        log_f.write(text + "\n")
        log_f.flush()

    while True:
        chunk = proc.stdout.read(1024)
        if chunk == b"":
            break
        chunk = decoder.decode(chunk)
        for ch in chunk:
            if ch == "\r":
                if buf:
                    if last_sep == "\r":
                        emit_progress(buf)
                    else:
                        emit_normal(buf)
                    buf = ""
                last_sep = "\r"
                continue
            if ch == "\n":
                if buf:
                    if last_sep == "\r":
                        emit_progress(buf, final=True)
                        if sys.stdout.isatty():
                            print()
                    else:
                        emit_normal(buf)
                    buf = ""
                elif progress_active and sys.stdout.isatty():
                    print()
                progress_active = False
                last_sep = "\n"
                continue

            buf += ch

    if buf:
        if last_sep == "\r":
            emit_progress(buf, final=True)
            if sys.stdout.isatty():
                print()
        else:
            emit_normal(buf)
        log_f.flush()


def run_step(step_id, step_name, cmd, log_dir: Path, log_prefix=""):
    """运行单个 step，返回 (success, error_msg)。"""
    log_name = f"{log_prefix}_{step_id}_pipeline.log" if log_prefix else f"{step_id}_pipeline.log"
    log_file = log_dir / log_name

    print(f"\n{'=' * 60}")
    print(f"Step {step_id}: {step_name}")
    print(f"{'=' * 60}")
    print("$ " + shlex.join(cmd))

    start_time = time.time()
    try:
        env = {"PYTHONIOENCODING": "utf-8"}
        env.update(os.environ)

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

        with open(log_file, "w", encoding="utf-8") as log_f:
            stream_process_output(proc, log_f)

        proc.wait()
        elapsed = time.time() - start_time
        if proc.returncode == 0:
            print(f"\nStep {step_id} 完成 (耗时 {elapsed:.1f}s)")
            return True, None
        error_msg = f"Step {step_id} 失败，exit code {proc.returncode}"
        print(f"\n{error_msg}")
        return False, error_msg
    except Exception as e:
        error_msg = f"Step {step_id} 异常: {e}"
        print(f"\n{error_msg}")
        with open(log_file, "a", encoding="utf-8") as log_f:
            log_f.write(f"\n[EXCEPTION] {e}\n")
        return False, error_msg


def parse_args():
    parser = argparse.ArgumentParser(description="S1-S5 统一流水线")
    parser.add_argument("legacy_inputs", nargs="*", help="兼容旧用法：音视频/srt/fix.srt 文件")
    parser.add_argument("-s2", dest="legacy_s2", action="store_true",
                        help="兼容旧用法：从 S2 开始")
    parser.add_argument("--config", default=str(CFG_DIR / "input.yaml"),
                        help="输入配置 YAML，默认 cfg/input.yaml")
    parser.add_argument("--live-url", "--wechat-url", dest="live_url", default="",
                        help="回放或视频号 sph URL；视频号自动使用独立 S1 并跳过 S2")
    parser.add_argument("--wechat-headless", action=argparse.BooleanOptionalAction, default=None,
                        help="视频号复用元宝登录态无窗口运行；首次使用 --no-wechat-headless")
    parser.add_argument("--wechat-browser-profile", default=None, help="视频号元宝登录态目录")
    parser.add_argument("--wechat-login-timeout", type=int, default=None, help="元宝登录等待秒数")
    parser.add_argument("--wiki-url", default="", help="源 wiki/文稿 URL")
    parser.add_argument("--target-wiki-url", default="", help="目标飞书 wiki URL")
    parser.add_argument("--full-copy", action="store_true", help="S2 忽略标题边界，复制全文")
    parser.add_argument("--no-attachments", action="store_true", help="S2 不下载页面及 Sheet 内的附件")
    parser.add_argument("--exclude-before-heading", default=None, help="S2 排除该标题完整章节及之前内容")
    parser.add_argument("--exclude-after-heading", default=None, help="S2 排除该标题完整章节及之后内容")
    parser.add_argument("--include-start-heading", default=None, help="S2 从该标题开始复制")
    parser.add_argument("--include-end-heading", default=None, help="S2 复制该标题完整章节后停止")
    parser.add_argument("--from-step", choices=STEP_ORDER, default="", help="起始步骤")
    parser.add_argument("--to-step", choices=STEP_ORDER, default="s5", help="结束步骤")
    parser.add_argument(
        "--audio-file", "--video-file", dest="audio_file", default="", metavar="MEDIA_FILE",
        help=("从 S3 开始时传入已有音频或原始视频；使用 --video-file 时不生成同名的"
              "持久化音频，只在 S3 内部使用并清理临时音频"),
    )
    parser.add_argument("--subtitle", default="", help="从 S4 开始时传入 SRT 文件")
    parser.add_argument("--fix-subtitle", default="", help="从 S5 开始时传入修订后的 SRT 文件")
    parser.add_argument("--whisper-backend", default="mlx",
                        choices=["auto", "mlx", "faster-whisper"],
                        help="S3 Whisper 后端，默认 mlx")
    parser.add_argument("--whisper-model", default="large-v3",
                        help="S3 Whisper 模型，默认 large-v3")
    parser.add_argument("--subtitle-engine", "--s3-engine", dest="s3_engine",
                        choices=["qwen3-asr", "whisper", "doubao-seed-asr"], default="qwen3-asr",
                        help="S3 字幕引擎：qwen3-asr（默认）、whisper 或 doubao-seed-asr")
    parser.add_argument("--print-plan", action="store_true",
                        help="只打印执行计划，不运行子进程、不发送通知")
    parser.add_argument("--no-notify", action="store_true", help="不发送飞书通知")
    return parser.parse_args()


def apply_legacy_args(args):
    if args.legacy_s2 and not args.from_step:
        args.from_step = "s2"

    legacy_inputs = [item.strip() for item in args.legacy_inputs if item.strip()]
    for attr in ("audio_file", "subtitle", "fix_subtitle"):
        value = getattr(args, attr)
        if value and legacy_inputs and not Path(value).exists():
            repaired = " ".join([value] + legacy_inputs)
            if Path(repaired).exists():
                setattr(args, attr, repaired)
                legacy_inputs = []
                break

    args.legacy_inputs = legacy_inputs
    if not legacy_inputs:
        return

    legacy = " ".join(legacy_inputs)
    lower = legacy.lower()
    media_suffixes = (
        ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma",
        ".mp4", ".mov", ".mkv", ".webm", ".ts", ".m4v", ".avi", ".flv",
        ".m2ts", ".mts", ".mpeg", ".mpg", ".wmv", ".3gp",
    )
    if lower.endswith(media_suffixes) and not args.audio_file:
        args.audio_file = legacy
    elif "_fix.srt" in lower and not args.fix_subtitle:
        args.fix_subtitle = legacy
    elif lower.endswith(".srt") and not args.subtitle:
        args.subtitle = legacy
    else:
        print(f"[警告] 未识别的旧式位置参数: {legacy}")


def resolve_start_step(args):
    if args.from_step:
        return args.from_step
    if args.fix_subtitle:
        return "s5"
    if args.subtitle:
        return "s4"
    if args.audio_file:
        return "s3"
    return "s1"


def build_tasks(args, config):
    if args.audio_file or args.subtitle or args.fix_subtitle or args.legacy_inputs:
        task = {"source_huifang_url": args.live_url} if is_wechat_task({
            "source_huifang_url": args.live_url}) else {}
        return [task], "artifact"

    if args.live_url or args.wiki_url or args.target_wiki_url:
        return [{
            "source_huifang_url": args.live_url,
            "source_wiki_url": args.wiki_url,
            "target_wiki_url": args.target_wiki_url,
            "source_type": ("wechat_channels" if is_wechat_task({"source_huifang_url": args.live_url})
                            else "yitang" if is_yitang_live_url(args.live_url) else ""),
        }], "direct"

    tasks = config.get("tasks", [])
    if not tasks:
        return [], "config"
    return tasks, "config"


def resolve_cli_file(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    local = Path.cwd() / path
    if local.exists():
        return local.absolute()
    return (PROJECT_DIR / path).absolute()


def validate_explicit_input_files(args) -> bool:
    for attr, label in (
        ("audio_file", "音视频文件"),
        ("subtitle", "字幕文件"),
        ("fix_subtitle", "修订字幕文件"),
    ):
        value = getattr(args, attr)
        if not value:
            continue
        path = resolve_cli_file(value)
        if not path.exists():
            print(f"[ERROR] {label}不存在: {path}")
            print("[提示] 文件路径含空格、#、? 等字符时，请用引号包起来。")
            return False
        setattr(args, attr, str(path))
    return True


def output_dirs_for_task(task, config):
    if is_wechat_task(task):
        output = wechat_output_dir(config)
        return output, output
    live_url = task.get("source_huifang_url", "")
    wiki_url = task.get("source_wiki_url", "")
    default_output = PROJECT_DIR / config.get("path_output_dir", "output").rstrip("/")
    yitang_output = PROJECT_DIR / config.get("path_yitang_dir", "out-yitang").rstrip("/")
    s1_output = yitang_output if is_yitang_live_url(live_url) else default_output
    s2_output = yitang_output if is_yitang_wiki_url(wiki_url) else default_output
    return s1_output, s2_output


def command_for_s1(task, args=None, config=None):
    live_url = task.get("source_huifang_url", "")
    if not live_url:
        return None
    if is_wechat_task(task):
        cmd = [sys.executable, "src/s1w_wechatchannels.py", live_url]
        if args is not None:
            cmd.extend(["--config", str(args.config)])
            headless = getattr(args, "wechat_headless", None)
            if headless is not None:
                cmd.append("--headless" if headless else "--no-headless")
            for key in ("browser_profile", "login_timeout"):
                value = getattr(args, "wechat_" + key, None)
                if value is not None:
                    cmd.extend(["--" + key.replace("_", "-"), str(value)])
        if config is not None:
            cmd.extend(["--output-dir", str(output_dirs_for_task(task, config)[0])])
        return cmd
    if is_yitang_live_url(live_url):
        return [sys.executable, "src/s1w_yitang_video.py", live_url]
    return [sys.executable, "src/s1_huifang.py", live_url]


def command_for_s2(task, args=None):
    if is_wechat_task(task):
        return None
    wiki_url = task.get("source_wiki_url", "")
    target_url = task.get("target_wiki_url", "")
    if not wiki_url:
        return None
    if is_yitang_wiki_url(wiki_url):
        cmd = [sys.executable, "src/s2w_yitang_wiki.py", wiki_url, target_url]
        if (getattr(args, "full_copy", False) if args else False) or task.get("full_copy", False):
            cmd.append("--full-copy")
        if (getattr(args, "no_attachments", False) if args else False) or task.get("no_attachments", False):
            cmd.append("--no-attachments")
        for field in (
            "exclude_before_heading",
            "exclude_after_heading",
            "include_start_heading",
            "include_end_heading",
        ):
            value = getattr(args, field, None) if args else None
            if value is None:
                value = task.get(field, "")
            if value:
                cmd.extend(["--" + field.replace("_", "-"), value])
        return cmd
    cmd = [sys.executable, "src/s2_wiki.py", wiki_url]
    if target_url:
        cmd.append(target_url)
    if task.get("title"):
        cmd.extend(["--title", task["title"]])
    if (getattr(args, "no_attachments", False) if args else False) or task.get("no_attachments", False):
        cmd.append("--no-attachments")
    return cmd


def command_for_s3(audio_path: Path | str, args) -> list[str]:
    """按选择的字幕引擎构造 S3 命令。"""
    command = [sys.executable, "src/s3_subtitle.py", str(audio_path)]
    if args.s3_engine == "doubao-seed-asr":
        return command + ["--doubao-seed-asr"]
    if args.s3_engine == "qwen3-asr":
        return command + ["--qwen3-asr"]
    return command + [
        "--whisper", "--backend", args.whisper_backend, "--model", args.whisper_model,
    ]


def print_plan(plan, context):
    print("流水线执行计划")
    print("=" * 60)
    print(f"任务: {context['task_label']}")
    print(f"任务来源: {context['task_source']}")
    print(f"S1 输出目录: {context['s1_output_dir']}")
    print(f"S2 输出目录: {context['s2_output_dir']}")
    print(f"起止步骤: {context['start_step']} -> {context['to_step']}")
    print(f"S3 引擎: {context['s3_engine']}")
    if context["s3_engine"] == "whisper":
        print(f"Whisper: backend={context['whisper_backend']}, model={context['whisper_model']}")
    print("")
    for item in plan:
        status = "跳过" if item["skip"] else "执行"
        print(f"[{status}] {item['step_id']} {item['name']}")
        if item["reason"]:
            print(f"  reason: {item['reason']}")
        if item["cmd"]:
            print("  cmd: " + shlex.join(item["cmd"]))
    print("")
    print("中间产物发现:")
    if context.get("s3_input_path"):
        print(f"  S3 使用已指定媒体: {context['s3_input_path']}")
    elif context.get("wechat_task"):
        print("  S3 使用分享 ID 对应的 M4A；S1 成功清单核验失败即停止")
    else:
        print("  S3 将优先扫描 .m4a，未找到时扫描 .mp3")
    print("  S4 将优先使用 S3 生成的字幕后缀")
    if context.get("wechat_task"):
        print("  S5 使用校订字幕，以无 Wiki、无讨论区模式生成信息拓展和摘要")
    else:
        print("  S5 将优先使用 S4 生成的 _fix.srt，并附带 wiki md / 讨论区 xlsx（如找到）")


def build_plan(args, config, task, task_source, task_label):
    mp3_path = Path(args.audio_file).absolute() if args.audio_file else None
    srt_path = Path(args.subtitle).absolute() if args.subtitle else None
    fix_srt_path = Path(args.fix_subtitle).absolute() if args.fix_subtitle else None
    start_step = resolve_start_step(args)
    to_step = args.to_step

    s1_output_dir, s2_output_dir = output_dirs_for_task(task, config)
    if mp3_path:
        s1_output_dir = mp3_path.parent
        s2_output_dir = mp3_path.parent
    elif srt_path:
        s1_output_dir = srt_path.parent
    elif fix_srt_path:
        s1_output_dir = fix_srt_path.parent
    if srt_path:
        s2_output_dir = srt_path.parent
    elif fix_srt_path:
        s2_output_dir = fix_srt_path.parent
    if not args.print_plan:
        s1_output_dir.mkdir(parents=True, exist_ok=True)
        s2_output_dir.mkdir(parents=True, exist_ok=True)

    wechat_task = is_wechat_task(task)
    if wechat_task and not (mp3_path or srt_path or fix_srt_path):
        # 固定分享 ID 对应的音频，不扫描目录中的其他任务。
        _, mp3_path, _ = artifact_paths(task["source_huifang_url"], s1_output_dir)

    s3_suffixes = get_s3_suffixes(config)
    preferred_s3_suffix = get_expected_s3_suffix(
        config, args.whisper_backend, args.whisper_model, args.s3_engine
    )
    s4_suffix = config.get("output", {}).get("s4", {}).get("suffix", "_fix")
    base_stem = None
    if mp3_path:
        base_stem = mp3_path.stem
    elif srt_path:
        base_stem = strip_known_artifact_suffix(srt_path.stem, s3_suffixes, s4_suffix)
    elif fix_srt_path:
        base_stem = strip_known_artifact_suffix(fix_srt_path.stem, s3_suffixes, s4_suffix)
    strict_artifact_mode = bool(mp3_path or srt_path or fix_srt_path)
    transcript_path = None
    discussion_path = None
    pipeline_started_at = time.time()

    context = {
        "task_source": task_source,
        "task_label": task_label,
        "s1_output_dir": s1_output_dir,
        "s2_output_dir": s2_output_dir,
        "start_step": start_step,
        "to_step": to_step,
        "whisper_backend": args.whisper_backend,
        "whisper_model": args.whisper_model,
        "s3_engine": args.s3_engine,
        "wechat_task": wechat_task,
        "s3_input_path": mp3_path if args.audio_file else None,
    }

    def step_enabled(step_id):
        idx = STEP_ORDER.index(step_id)
        return STEP_ORDER.index(start_step) <= idx <= STEP_ORDER.index(to_step)

    plan = []
    for step_id in STEP_ORDER:
        enabled = step_enabled(step_id)
        cmd = None
        reason = ""
        if step_id == "s1":
            cmd = command_for_s1(task, args, config)
            if not cmd:
                reason = "没有回放 URL"
        elif step_id == "s2":
            cmd = command_for_s2(task, args)
            if not cmd:
                reason = "视频号无独立 Wiki，跳过 S2" if wechat_task else "没有源 wiki URL"
        elif step_id == "s3":
            audio_arg = str(mp3_path) if mp3_path else f"<m4a/mp3 discovered in {s1_output_dir}>"
            cmd = command_for_s3(audio_arg, args)
        elif step_id == "s4":
            subtitle_arg = str(srt_path) if srt_path else "<srt discovered after S3>"
            cmd = [sys.executable, "src/s4_srt_fix.py", "--subtitle", subtitle_arg]
            if wechat_task:
                cmd.append("--no-transcript")
        elif step_id == "s5":
            if fix_srt_path:
                fix_arg = str(fix_srt_path)
            elif srt_path:
                fix_arg = str(srt_path)
            else:
                fix_arg = "<fix srt discovered after S4>"
            cmd = [sys.executable, "src/s5_addon.py", "--subtitle", fix_arg]
            if wechat_task:
                cmd.extend(["--no-transcript", "--subtitle-only"])
        plan.append({
            "step_id": step_id,
            "name": STEP_NAMES[step_id],
            "cmd": cmd,
            "skip": not enabled or cmd is None,
            "reason": "起止步骤范围外" if not enabled else reason,
        })

    state = {
        "mp3_path": mp3_path,
        "srt_path": srt_path,
        "fix_srt_path": fix_srt_path,
        "s1_output_dir": s1_output_dir,
        "s2_output_dir": s2_output_dir,
        "s3_suffixes": s3_suffixes,
        "preferred_s3_suffix": preferred_s3_suffix,
        "s4_suffix": s4_suffix,
        "base_stem": base_stem,
        "strict_artifact_mode": strict_artifact_mode,
        "transcript_path": transcript_path,
        "discussion_path": discussion_path,
        "pipeline_started_at": pipeline_started_at,
        "step_enabled": step_enabled,
    }
    return plan, context, state


def run_one_task(args, config, task, task_source, task_label, log_dir, log_prefix=""):
    plan, context, state = build_plan(args, config, task, task_source, task_label)

    if args.print_plan:
        print_plan(plan, context)
        return True

    print("=" * 60)
    print(f"五步流水线开始: {task_label}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"任务来源: {task_source}")
    print(f"起止步骤: {context['start_step']} -> {context['to_step']}")
    print("=" * 60)

    if not args.no_notify:
        creds = load_credentials()
        notify_feishu(creds, f"[dl-video] 流水线开始\n{task_label}")

    failed_steps = []
    step_enabled = state["step_enabled"]
    mp3_path = state["mp3_path"]
    srt_path = state["srt_path"]
    fix_srt_path = state["fix_srt_path"]
    s1_output_dir = state["s1_output_dir"]
    s2_output_dir = state["s2_output_dir"]
    s3_suffixes = state["s3_suffixes"]
    preferred_s3_suffix = state["preferred_s3_suffix"]
    s4_suffix = state["s4_suffix"]
    base_stem = state["base_stem"]
    strict_artifact_mode = state["strict_artifact_mode"]
    transcript_path = state["transcript_path"]
    discussion_path = state["discussion_path"]
    pipeline_started_at = state["pipeline_started_at"]

    for step_id in STEP_ORDER:
        if not step_enabled(step_id):
            print(f"\n跳过 Step {step_id} ({STEP_NAMES[step_id]})")
            continue

        cmd = None
        if step_id == "s1":
            cmd = command_for_s1(task, args, config)
        elif step_id == "s2":
            cmd = command_for_s2(task, args)
        elif step_id == "s3":
            if mp3_path is None:
                mp3_path = find_s3_audio(
                    s1_output_dir, pipeline_started_at,
                    allow_old=not strict_artifact_mode,
                )
                if mp3_path:
                    print(f"[INFO] 扫描到音频: {mp3_path}")
            if not mp3_path:
                failed_steps.append((step_id, f"未找到 M4A 或 MP3: {s1_output_dir}"))
                break
            if not mp3_path.exists():
                failed_steps.append((step_id, f"音频文件不存在: {mp3_path}"))
                break
            base_stem = base_stem or mp3_path.stem
            cmd = command_for_s3(mp3_path, args)
        elif step_id == "s4":
            if srt_path is None and mp3_path:
                srt_path = find_srt_for_audio(
                    mp3_path, s1_output_dir, s3_suffixes, preferred_s3_suffix
                )
                if srt_path:
                    print(f"[INFO] S4 输入字幕: {srt_path}")
            if not srt_path:
                failed_steps.append((step_id, "未找到 SRT 字幕文件"))
                break
            if not srt_path.exists():
                failed_steps.append((step_id, f"SRT 字幕文件不存在: {srt_path}"))
                break
            base_stem = base_stem or strip_known_artifact_suffix(
                srt_path.stem, s3_suffixes, s4_suffix
            )
            cmd = [sys.executable, "src/s4_srt_fix.py", "--subtitle", str(srt_path)]
            if is_wechat_task(task):
                cmd.append("--no-transcript")
        elif step_id == "s5":
            if fix_srt_path is None and srt_path:
                candidate = srt_path.parent / f"{srt_path.stem}{s4_suffix}.srt"
                fix_srt_path = candidate.absolute() if candidate.exists() else srt_path
                print(f"[INFO] S5 输入字幕: {fix_srt_path}")
            if not fix_srt_path:
                failed_steps.append((step_id, "未找到 S5 输入字幕"))
                break
            if not fix_srt_path.exists():
                failed_steps.append((step_id, f"S5 输入字幕不存在: {fix_srt_path}"))
                break
            base_stem = base_stem or strip_known_artifact_suffix(
                fix_srt_path.stem, s3_suffixes, s4_suffix
            )
            if transcript_path is None and not is_wechat_task(task):
                transcript_path = find_transcript_for_base(
                    s2_output_dir, base_stem, pipeline_started_at
                )
            if discussion_path is None and not is_wechat_task(task):
                discussion_path = find_related_file(
                    s1_output_dir, base_stem, ["{base}.xlsx", "{base}*.xlsx"], pipeline_started_at
                )
            cmd = [sys.executable, "src/s5_addon.py", "--subtitle", str(fix_srt_path)]
            if is_wechat_task(task):
                cmd.extend(["--no-transcript", "--subtitle-only"])
            if transcript_path:
                cmd.extend(["--transcript", str(transcript_path)])
            if discussion_path:
                cmd.extend(["--discussion", str(discussion_path)])

        if not cmd:
            print(f"\n跳过 Step {step_id} ({STEP_NAMES[step_id]}): 缺少输入")
            continue

        success, error_msg = run_step(step_id, STEP_NAMES[step_id], cmd, log_dir, log_prefix)
        if not success:
            failed_steps.append((step_id, error_msg))
            if not args.no_notify:
                creds = load_credentials()
                failed_log_name = (
                    f"{log_prefix}_{step_id}_pipeline.log"
                    if log_prefix else f"{step_id}_pipeline.log"
                )
                notify_feishu(
                    creds,
                    f"[dl-video] {task_label} Step {step_id} ({STEP_NAMES[step_id]}) 失败\n"
                    f"错误: {error_msg}\n日志: {log_dir / failed_log_name}",
                )
            break

        if step_id == "s1":
            if is_wechat_task(task):
                video, expected_audio, manifest = artifact_paths(task["source_huifang_url"], s1_output_dir)
                try:
                    handoff = json.loads(manifest.read_text(encoding="utf-8"))
                    if (not isinstance(handoff, dict) or handoff.get("status") != "success"
                            or handoff.get("source_url") != extract_share_url(task["source_huifang_url"])
                            or handoff.get("completed_at", 0) < pipeline_started_at
                            or handoff.get("audio_path") != str(expected_audio)
                            or handoff.get("video_path") != str(video)
                            or not expected_audio.is_file() or not video.is_file()):
                        raise ValueError("invalid handoff")
                    mp3_path = expected_audio
                except (OSError, ValueError, TypeError):
                    failed_steps.append((step_id, "视频号 S1 未交付有效的本轮音视频清单，停止后续步骤"))
                    break
            else:
                mp3_path = find_s3_audio(
                    s1_output_dir, pipeline_started_at,
                    allow_old=not strict_artifact_mode,
                ) or mp3_path
            if mp3_path:
                base_stem = base_stem or mp3_path.stem
                if not is_wechat_task(task):
                    discussion_path = find_related_file(
                        s1_output_dir, base_stem, ["{base}.xlsx", "{base}*.xlsx"], pipeline_started_at
                    )
        elif step_id == "s2":
            if base_stem:
                transcript_path = find_transcript_for_base(
                    s2_output_dir, base_stem, pipeline_started_at
                )
            if transcript_path is None:
                transcript_path = newest_file(
                    s2_output_dir,
                    ["*wiki.md", "*.md"],
                    pipeline_started_at,
                    allow_old=not strict_artifact_mode,
                )
        elif step_id == "s3" and mp3_path:
            srt_path = find_srt_for_audio(
                mp3_path, s1_output_dir, s3_suffixes, preferred_s3_suffix
            ) or srt_path
            if srt_path is None:
                failed_steps.append((step_id, f"S3 未生成同前缀字幕: {mp3_path.stem}"))
                break
        elif step_id == "s4" and srt_path:
            candidate = srt_path.parent / f"{srt_path.stem}{s4_suffix}.srt"
            fix_srt_path = candidate.absolute() if candidate.exists() else srt_path

    print(f"\n{'=' * 60}")
    print(f"流水线执行结果: {task_label}")
    print(f"{'=' * 60}")
    if failed_steps:
        print(f"{len(failed_steps)} 个步骤失败:")
        for step_id, error in failed_steps:
            print(f"  - {step_id}: {error}")
    else:
        print("全部步骤执行成功")
        for label, path in [
            ("S3 输入媒体", mp3_path),
            ("教学文档", transcript_path),
            ("讨论区", discussion_path),
            ("字幕", srt_path),
            ("修订字幕", fix_srt_path if fix_srt_path != srt_path else None),
        ]:
            if path:
                print(f"  {label}: {path}")
        if not args.no_notify:
            creds = load_credentials()
            notify_feishu(creds, f"[dl-video] 流水线执行成功\n{task_label}")

    print(f"\n日志文件目录: {log_dir}")
    return not failed_steps


def main():
    args = parse_args()
    apply_legacy_args(args)
    if not is_valid_target_wiki_url(args.target_wiki_url):
        print(f"[ERROR] {TARGET_WIKI_URL_ERROR}")
        return False
    if not validate_explicit_input_files(args):
        return False

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_DIR / config_path
    config = load_pipeline_config(config_path)
    args.config = str(config_path.resolve())

    log_dir = PROJECT_DIR / config.get("path_log_dir", "log-err").rstrip("/")
    if not args.print_plan:
        log_dir.mkdir(parents=True, exist_ok=True)

    start_step = resolve_start_step(args)
    to_step = args.to_step
    if STEP_ORDER.index(start_step) > STEP_ORDER.index(to_step):
        print(f"[ERROR] --from-step ({start_step}) 不能晚于 --to-step ({to_step})")
        return False

    tasks, task_source = build_tasks(args, config)
    if not tasks:
        print("[ERROR] 没有可执行任务")
        return False
    if any(not is_valid_target_wiki_url(task.get("target_wiki_url", "")) for task in tasks):
        print(f"[ERROR] {TARGET_WIKI_URL_ERROR}")
        return False

    if args.print_plan and len(tasks) > 1:
        print(f"配置中共有 {len(tasks)} 个任务，将按顺序执行。")

    results = []
    for idx, task in enumerate(tasks, 1):
        task_label = f"任务 {idx}/{len(tasks)}"
        log_prefix = f"task{idx:02d}" if len(tasks) > 1 else ""
        ok = run_one_task(args, config, task, task_source, task_label, log_dir, log_prefix)
        results.append((task_label, ok))
        if not ok:
            break

    if len(results) > 1 and not args.print_plan:
        print(f"\n{'=' * 60}")
        print("多任务汇总")
        print(f"{'=' * 60}")
        for label, ok in results:
            print(f"{label}: {'成功' if ok else '失败'}")

    return all(ok for _, ok in results)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
