"""项目公共配置工具 — 配置加载、文件名安全化"""

import re
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent.parent   # src/
PROJECT_DIR = SCRIPT_DIR.parent                        # dl-video/
CFG_DIR = PROJECT_DIR / "cfg"

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|：]')


def load_config():
    """加载项目配置（config.yaml + input.yaml）和凭证（credentials.yaml）

    返回 (config, creds) 二元组。
    """
    with open(CFG_DIR / "config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    input_yaml = CFG_DIR / "input.yaml"
    if input_yaml.exists():
        with open(input_yaml, encoding="utf-8") as f:
            input_cfg = yaml.safe_load(f) or {}
            config.update(input_cfg)
    with open(CFG_DIR / "credentials.yaml", encoding="utf-8") as f:
        creds = yaml.safe_load(f)
    return config, creds


def safe_filename(title: str) -> str:
    """替换 Windows 非法字符为下划线"""
    return ILLEGAL_CHARS.sub("_", title).strip()
