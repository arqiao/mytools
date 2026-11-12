# 参数调用示例:
#   python src/s1_huifang.py
#   python src/s1_huifang.py <url_huifang>
#   python src/s1_huifang.py <url_huifang_1> <url_huifang_2>
"""Step1: 回放下载调度器 — 根据 source_type 或 URL 自动识别分发到各平台下载模块

用法:
  # 从 cfg/input.yaml 的 tasks 中读取任务，自动调度下载
  python src/s1_huifang.py

  # 直接传入一个或多个回放 URL（自动识别平台类型）
  python src/s1_huifang.py <url_huifang>
  python src/s1_huifang.py <url_huifang_1> <url_huifang_2>

参数:
  url_huifang  回放 URL（可选，可传多个）。不传时读取 input.yaml 的 tasks

  input.yaml 中通过 source_type 指定平台:
    yitang          一堂（视频+文稿）
    feishu_minutes  飞书妙记
    tencent_meeting 腾讯会议回放
    zhihu           知乎训练营
    panda           熊猫学院
    xiaoe           小鹅通
    taobao          淘宝直播
    cda             CDA 网校
    wechat_channels 微信视频号

配置:
  cfg/input.yaml  — tasks 列表（source_type, source_huifang_url, source_wiki_url, target_wiki_url 等）
  凭据通过 load_config() 的集中加载器读取；视频号复用元宝浏览器 profile。
"""

import argparse
import logging
import re
import sys
from pathlib import Path

import yaml

from modules.config_utils import load_config, safe_filename

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"
try:
    _input_cfg = yaml.safe_load((CFG_DIR / "input.yaml").read_text(encoding="utf-8")) or {}
except yaml.YAMLError:
    _input_cfg = {}
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

# URL 域名 → source_type 映射
_URL_PATTERNS = [
    (r"feishu\.cn/minutes/", "feishu_minutes"),
    (r"meeting\.tencent\.com", "tencent_meeting"),
    (r"zhihu\.com", "zhihu"),
    (r"\.xet\.|xetslk\.com|xiaoecloud\.com|xiaoeknow\.com", "xiaoe"),
    (r"pandacollege\.cn|video\.gzfeice\.cn", "panda"),
    (r"tbkflow\.cn|taobao", "taobao"),
    (r"yitang\.top", "yitang"),
    (r"edu\.cda\.cn", "cda"),
    (r"weixin\.qq\.com/sph/", "wechat_channels"),
]


def _infer_source_type(url: str) -> str:
    """从 URL 自动推断平台类型"""
    if not url:
        return ""
    for pattern, stype in _URL_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return stype
    return ""


def main():
    parser = argparse.ArgumentParser(description="回放下载调度器")
    parser.add_argument("urls", nargs="*", help="回放 URL（可传多个）")
    args = parser.parse_args()

    config, creds = load_config(include_input=not bool(args.urls))
    config.setdefault("path_output_dir", "output/")
    config.setdefault("path_log_dir", "log-err/")
    config.setdefault("path_yitang_dir", "out-yitang/")
    if args.urls:
        tasks = [{"source_huifang_url": url} for url in args.urls]
    else:
        tasks = config.get("tasks", [])
    if not tasks:
        log.warning("input.yaml 中无任务")
        return

    failed = False
    for i, task in enumerate(tasks):
        source_type = task.get("source_type", "")
        url = task.get("source_huifang_url", "")

        # source_type 优先，没填则从 URL 自动推断
        if not source_type:
            source_type = _infer_source_type(url)
            if source_type:
                log.info(f"从 URL 自动识别平台: {source_type}")
            else:
                log.warning(f"任务 {i+1}: 无法识别平台类型 (url={url[:60]}...)")
                continue

        log.info(f"=== 任务 {i+1}/{len(tasks)}: {source_type} ===")

        try:
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
            elif source_type == "cda":
                from s1w_cda import process_cda
                process_cda(task, config, creds)
            elif source_type == "wechat_channels":
                from s1w_wechatchannels import process_wechat_channels
                process_wechat_channels(task, config, creds)
            else:
                log.warning(f"暂不支持的视频源: {source_type}")
        except Exception:
            failed = True
            log.exception(f"任务处理失败: {url}")

    log.info("任务处理结束%s", "（存在失败）" if failed else "")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
