"""Step1: 回放下载调度器 — 根据 source_type 分发到各平台下载模块"""

import logging
from pathlib import Path

import yaml

from modules.config_utils import load_config, safe_filename

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
_input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
LOG_DIR = PROJECT_DIR / _input_cfg.get("path_log_dir", "log-err")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "s1_huifang.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


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
        elif source_type == "yitang":
            from s1w_yitang_video import process_yitang_video
            process_yitang_video(task, config, creds)
        else:
            log.warning(f"暂不支持的视频源: {source_type}")

    log.info("所有任务处理完成")


if __name__ == "__main__":
    main()
