"""Step3: MP3 转字幕（fallback，当 Step1 未获取到字幕时使用）"""

import logging
import re
import sys
from pathlib import Path

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
        logging.FileHandler(LOG_DIR / "s3_subtitle.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')


def load_config():
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    with open(CFG_DIR / "credentials.yaml", encoding="utf-8") as f:
        creds = yaml.safe_load(f)
    return config, creds


def safe_filename(title: str) -> str:
    return ILLEGAL_CHARS.sub("_", title).strip()


def setup_yitang_path(config):
    yitang_dir = Path(PROJECT_DIR / config.get("yitang_dir", "../yitang"))
    yitang_src = yitang_dir / "src"
    if str(yitang_src) not in sys.path:
        sys.path.insert(0, str(yitang_src))
    return yitang_dir


def has_subtitle(output_dir, base_name):
    """检查是否已有字幕文件"""
    ori_srt = output_dir / f"{base_name}_ori.srt"
    if ori_srt.exists():
        log.info(f"已有原始字幕: {ori_srt}")
        return True
    return False


def generate_whisper_subtitle(audio_path, output_dir, base_name, config):
    """使用 Whisper 生成字幕"""
    from subtitle_from_mp3 import transcribe_whisper

    wm_srt = output_dir / f"{base_name}_wm.srt"
    if wm_srt.exists():
        log.info(f"Whisper 字幕已存在，跳过: {wm_srt}")
        return wm_srt

    if not audio_path.exists():
        log.error(f"音频文件不存在: {audio_path}")
        return None

    log.info(f"Whisper 转写: {audio_path.name}")
    whisper_cfg = config.get("whisper", {})
    model_size = whisper_cfg.get("model", "medium")
    force_cpu = whisper_cfg.get("force_cpu", False)

    result = transcribe_whisper(audio_path, model_size, force_cpu)
    if result is None:
        log.error("Whisper 转写失败")
        return None

    # transcribe_whisper 输出文件名带引擎后缀，重命名为 _wm.srt
    if result != wm_srt and result.exists():
        result.rename(wm_srt)
        log.info(f"字幕已重命名: {result.name} → {wm_srt.name}")

    return wm_srt


def get_task_title(task, config, creds):
    """获取任务标题"""
    manual_title = task.get("title", "")
    if manual_title:
        return manual_title

    source_type = task.get("source_type", "")
    if source_type == "feishu_minutes":
        from s1_huifang import extract_minutes_token, get_minutes_info
        from s1_huifang import ensure_feishu_token
        import requests
        session = requests.Session()
        token = extract_minutes_token(task["source_url"])
        user_token = ensure_feishu_token(creds, session)
        title, _ = get_minutes_info(token, user_token, session)
        return title

    return "untitled"


def main():
    config, creds = load_config()
    setup_yitang_path(config)

    tasks = config.get("tasks", [])
    if not tasks:
        log.warning("config.yaml 中无任务")
        return

    output_dir = PROJECT_DIR / config.get("output_dir", "output")
    output_dir.mkdir(exist_ok=True)

    for i, task in enumerate(tasks):
        log.info(f"=== 任务 {i+1}/{len(tasks)} ===")
        title = get_task_title(task, config, creds)
        base_name = safe_filename(title)

        # 检查是否已有字幕
        if has_subtitle(output_dir, base_name):
            log.info("已有原始字幕，无需 Whisper 转写")
            continue

        # 用 Whisper 生成字幕
        audio_path = output_dir / f"{base_name}.mp3"
        result = generate_whisper_subtitle(audio_path, output_dir, base_name, config)
        if result:
            log.info(f"Whisper 字幕生成完成: {result}")
        else:
            log.error(f"字幕生成失败: {base_name}")

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
