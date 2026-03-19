"""Step1: 回放下载调度器 — 根据 source_type 分发到各平台下载模块"""

import logging
import re
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
        logging.FileHandler(LOG_DIR / "s1_huifang.log", encoding="utf-8"),
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
    """替换 Windows 非法字符为下划线"""
    return ILLEGAL_CHARS.sub("_", title).strip()


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
            from s1w_feishumiaoji import process_feishu_minutes
            process_feishu_minutes(task, config, creds)
        elif source_type == "tencent_meeting":
            from s1w_tencentmeeting import process_tencent_meeting
            process_tencent_meeting(task, config, creds)
        elif source_type == "zhihu":
            from s1w_zhihu import process_zhihu
            process_zhihu(task, config, creds)
        elif source_type == "xiaoe":
            from s1w_xiaoe import process_xiaoe
            process_xiaoe(task, config, creds)
        elif source_type == "panda":
            from s1w_panda import process_panda
            process_panda(task, config, creds)
        elif source_type == "taobao":
            from s1w_taobao import process_taobao
            process_taobao(task, config, creds)
        else:
            log.warning(f"暂不支持的视频源: {source_type}")

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
