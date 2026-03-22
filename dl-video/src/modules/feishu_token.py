"""飞书 token 统一管理模块

集中管理 user_access_token 的检查、刷新、持久化，
以及 feishu_headers 和 wiki token 解析。
"""

import logging
import time
from pathlib import Path

import requests
import yaml

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CFG_DIR = PROJECT_DIR / "cfg"

log = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"


def ensure_feishu_token(creds: dict, session: requests.Session) -> str:
    """确保飞书 user_access_token 有效，过期则刷新并持久化。返回 token 字符串。"""
    expire = creds["feishu"].get("user_token_expire_time", 0)
    if time.time() < expire - 300:
        return creds["feishu"]["user_access_token"]

    # 1) 获取 app_access_token
    app_resp = session.post(
        f"{FEISHU_BASE}/auth/v3/app_access_token/internal",
        json={
            "app_id": creds["feishu"]["app_id"],
            "app_secret": creds["feishu"]["app_secret"],
        },
        timeout=15,
    )
    app_resp.raise_for_status()
    app_token = app_resp.json().get("app_access_token", "")

    # 2) 用 app_token 刷新 user_access_token
    resp = session.post(
        f"{FEISHU_BASE}/authen/v1/oidc/refresh_access_token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": creds["feishu"]["user_refresh_token"],
        },
        headers={"Authorization": f"Bearer {app_token}"},
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书 token 刷新失败: {data}")

    td = data["data"]
    creds["feishu"]["user_access_token"] = td["access_token"]
    creds["feishu"]["user_refresh_token"] = td["refresh_token"]
    creds["feishu"]["user_token_expire_time"] = int(time.time()) + td["expires_in"]

    with open(CFG_DIR / "credentials.yaml", "w", encoding="utf-8") as f:
        yaml.dump(creds, f, allow_unicode=True)
    log.info("飞书 token 已刷新")
    return td["access_token"]


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
