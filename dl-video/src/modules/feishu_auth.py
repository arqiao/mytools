"""项目授权兼容入口；实现统一维护在用户全局 feishu-access 中。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".local/share/arqiao-tools/feishu-access"))
from feishu_access.auth import load_config, save_tokens
from feishu_access.__main__ import main as _shared_main

_PROJECT_AUTH_KEY = 'auth_dl-video'


def load_credentials(path=None):
    return load_config(path=path)


def save_credentials(creds, path=None):
    save_tokens(_PROJECT_AUTH_KEY, creds.get(_PROJECT_AUTH_KEY, {}), path=path)


def main():
    return _shared_main(['authorize', '--profile', _PROJECT_AUTH_KEY])


if __name__ == '__main__':
    raise SystemExit(main())
