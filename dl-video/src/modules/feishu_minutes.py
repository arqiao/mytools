"""飞书妙记公共工具 — URL 解析 + 妙记信息查询"""
import logging
import re

import requests

from modules.feishu_token import FEISHU_BASE

log = logging.getLogger(__name__)


def extract_minutes_token(url: str) -> str:
    """从飞书妙记 URL 提取 minutes token"""
    m = re.search(r"/minutes/([A-Za-z0-9]+)", url)
    if not m:
        raise ValueError(f"无法从 URL 提取妙记 token: {url}")
    return m.group(1)


def get_minutes_info(token: str, user_token: str, session: requests.Session):
    """通过 Open API 获取妙记标题和时长，返回 (title, duration)"""
    url = f"{FEISHU_BASE}/minutes/v1/minutes/{token}"
    headers = {"Authorization": f"Bearer {user_token}"}
    resp = session.get(url, headers=headers, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取妙记信息失败: {data}")
    minute = data["data"]["minute"]
    title = minute.get("title", token)
    duration = minute.get("duration", "")
    log.info(f"妙记标题: {title}, 时长: {duration}")
    return title, duration
