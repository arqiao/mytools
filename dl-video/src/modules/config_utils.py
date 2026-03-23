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


def strip_date_from_title(title: str, date_prefix: str) -> str:
    """从标题中去除与 date_prefix（YYMMDD）对应的日期信息，避免文件名重复出现日期。

    支持的标题日期格式：
      - YYMMDD / YYYYMMDD（如 260312 / 20260312）
      - YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD
      - MM-DD / M-D / MM.DD / MM/DD
      - YYYY年MM月DD日 / YYYY年M月D日
      - MM月DD日 / M月D日
    去除后清理多余的分隔符。
    """
    if not date_prefix or len(date_prefix) != 6:
        return title
    yy, mm, dd = date_prefix[:2], date_prefix[2:4], date_prefix[4:6]
    yyyy = f"20{yy}"
    # 去掉前导零用于匹配 "3月12日" 这种
    m_short = str(int(mm))
    d_short = str(int(dd))

    patterns = [
        f"{yyyy}年{m_short}月{d_short}日",
        f"{yyyy}年{mm}月{dd}日",
        f"{m_short}月{d_short}日",
        f"{mm}月{dd}日",
        f"{yyyy}[-./]{mm}[-./]{dd}",
        f"{mm}[-./]{dd}",
        f"{m_short}[-./]{d_short}",
        f"{yyyy}{mm}{dd}",
        f"{yy}{mm}{dd}",
    ]

    result = title
    for pat in patterns:
        result = re.sub(pat, "", result)
        if result != title:
            break  # 只去除一次匹配

    # 清理残留的前后分隔符和空白
    result = re.sub(r'^[\s_\-—|/\\]+|[\s_\-—|/\\]+$', '', result)
    result = re.sub(r'[\s_\-—|]{2,}', '-', result)
    return result.strip()
