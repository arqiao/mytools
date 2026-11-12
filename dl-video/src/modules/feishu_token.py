"""飞书 token 统一管理模块

集中管理 user_access_token 的检查、刷新、持久化，
以及 feishu_headers 和 wiki token 解析。
"""

import logging
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path.home() / ".local/share/arqiao-tools/feishu-access"))
from feishu_access.auth import ensure_user_token, save_tokens

log = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"
SECRETS_FEISHU_PATH = Path.home() / ".config" / "secrets" / "gfeishu.yaml"
SECRETS_AUTH_BLOCK = "auth_dl-video"


def _persist_auth_tokens(access_token: str, refresh_token: str, expire_time: int) -> None:
    """兼容入口：共享模块加锁并精确写回当前项目授权。"""
    save_tokens(SECRETS_AUTH_BLOCK, {
        "user_access_token": access_token,
        "user_refresh_token": refresh_token,
        "user_token_expire_time": expire_time,
    }, path=SECRETS_FEISHU_PATH)


def ensure_feishu_token(creds: dict, session: requests.Session) -> str:
    """保留项目接口；Token 检查、刷新和持久化全部委托公共模块。"""
    tokens = ensure_user_token(SECRETS_AUTH_BLOCK, session=session, path=SECRETS_FEISHU_PATH)
    creds.setdefault("feishu", {}).update(tokens)
    creds.setdefault(SECRETS_AUTH_BLOCK, {}).update(tokens)
    return tokens["user_access_token"]


def feishu_headers(creds: dict) -> dict:
    """返回带 user_access_token 的飞书 API 请求头。"""
    return {
        "Authorization": f"Bearer {creds['feishu']['user_access_token']}",
        "Content-Type": "application/json; charset=utf-8",
    }


def resolve_wiki_token(wiki_url: str, creds: dict, session: requests.Session) -> str:
    """从飞书 wiki URL 解析出 document_id。"""
    wiki_token = wiki_url.rstrip("/").split("/")[-1].split("?")[0]

    ensure_feishu_token(creds, session)
    resp = session.get(
        f"{FEISHU_BASE}/wiki/v2/spaces/get_node",
        params={"token": wiki_token},
        headers=feishu_headers(creds),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书 wiki 解析失败: {data}")
    return data["data"]["node"]["obj_token"]
