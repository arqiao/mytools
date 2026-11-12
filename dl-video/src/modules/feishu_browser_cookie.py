"""飞书浏览器 Cookie 读取 — 从本机 Chrome / 飞书客户端解密 feishu.cn cookie

飞书网页端接口（妙记字幕 webvtt、跨租户 transcript、Drive 图片预览等）不接受
Open API 的 user_access_token，需要真实浏览器登录态的 Cookie。该 Cookie 会过期，
因此优先从本机浏览器实时读取（同 s2w_yitang_wiki 的图片下载兜底逻辑），
读取失败再回退到 secrets 里手写的 feishu.browser_cookie。

仅 macOS（darwin）支持本地解密；其他平台直接返回空，调用方应回退到配置里的
feishu.browser_cookie。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

log = logging.getLogger(__name__)

_cookie_cache: list[tuple[str, str, str]] | None = None


def local_cookie_sources() -> list[tuple[Path, str, str]]:
    """返回 (cookie_db, keychain_service, keychain_account) 列表。

    覆盖本机 Chrome 与飞书桌面客户端（LarkShell）内的 Chromium 配置。
    """
    home = Path.home()
    sources = [
        (
            home / "Library/Application Support/Google/Chrome/Default/Cookies",
            "Chrome Safe Storage",
            "Chrome",
        ),
    ]

    feishu_root = (
        home
        / "Library/Containers/com.bytedance.macos.feishu/Data/Library/Application Support/LarkShell"
    )
    if feishu_root.exists():
        for cookie_db in feishu_root.glob("**/Cookies"):
            if "Safe Browsing" in str(cookie_db):
                continue
            sources.append((
                cookie_db,
                "Suite App Safe Storage",
                "XY6NLV7YTS.com.bytedance.macos.feishu-SuiteApp",
            ))
    return sources


def _decrypt_chrome_cookie_value(host: str, encrypted: bytes, key: bytes, iv: bytes) -> str:
    if not encrypted:
        return ""
    data = encrypted[3:] if encrypted.startswith(b"v10") else encrypted
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()

    # Chrome 新版本会在明文前加 host_key 的 sha256 摘要。
    digest = hashlib.sha256(host.encode()).digest()
    if plain.startswith(digest):
        plain = plain[len(digest):]
    return plain.decode("utf-8", errors="replace")


def _read_chromium_cookie_db(cookie_db: Path, service: str, account: str) -> list[tuple[str, str, str]]:
    """读取一个 Chromium Cookie DB 中的 feishu.cn cookie。失败返回空列表。"""
    cookies: list[tuple[str, str, str]] = []
    try:
        password = subprocess.check_output(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        ).strip().encode()
        key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)
        iv = b" " * 16

        tmp_db = Path(tempfile.gettempdir()) / f"dl-video-cookies-{abs(hash(str(cookie_db)))}.sqlite"
        shutil.copy2(cookie_db, tmp_db)
        con = sqlite3.connect(tmp_db)
        cur = con.cursor()
        cur.execute(
            "select host_key, name, value, encrypted_value from cookies where host_key like ?",
            ("%feishu.cn%",),
        )
        rows = cur.fetchall()
        con.close()

        for host, name, value, encrypted in rows:
            cookie_value = value or _decrypt_chrome_cookie_value(host, encrypted, key, iv)
            if cookie_value:
                cookies.append((host, name, cookie_value))
    except subprocess.TimeoutExpired:
        log.info(f"读取本机 Cookie 超时，可能需要允许 Keychain 访问: {service}")
    except Exception as e:  # noqa: BLE001 - 读取失败应静默降级
        log.debug(f"读取 Cookie DB 失败 ({cookie_db}): {e}")

    return cookies


def load_local_feishu_cookies() -> list[tuple[str, str, str]]:
    """读取并解密本机浏览器/飞书客户端中 feishu.cn 相关 cookie。失败返回空列表。

    结果缓存到模块级，避免重复解密。
    """
    global _cookie_cache
    if _cookie_cache is not None:
        return _cookie_cache

    _cookie_cache = []
    if sys.platform != "darwin":
        return _cookie_cache

    cookies: list[tuple[str, str, str]] = []
    for cookie_db, service, account in local_cookie_sources():
        if not cookie_db.exists():
            continue
        cookies.extend(_read_chromium_cookie_db(cookie_db, service, account))

    _cookie_cache = cookies
    if cookies:
        log.info(f"已读取本机飞书 cookie: {len(cookies)} 个")
    return _cookie_cache


def build_cookie_header(url: str) -> str:
    """按 URL 所属域名组装 Cookie 头：优先精确主机，再回退到 .feishu.cn 通配。"""
    cookies = load_local_feishu_cookies()
    if not cookies:
        return ""

    request_host = urlparse(url).netloc
    wanted_hosts = [request_host, ".feishu.cn"]
    pairs: list[str] = []
    seen: set[str] = set()

    for preferred_host in wanted_hosts:
        for host, name, value in cookies:
            if not value or name in seen:
                continue
            if host == preferred_host or (
                host.startswith(".") and preferred_host.endswith(host.lstrip("."))
            ):
                pairs.append(f"{name}={value}")
                seen.add(name)

    return "; ".join(pairs)


def resolve_cookie(url: str, fallback: str = "") -> str:
    """返回可用于该 URL 的飞书 cookie：优先本机浏览器实时 cookie，回退到 fallback。"""
    local = build_cookie_header(url)
    if local:
        return local
    return fallback
