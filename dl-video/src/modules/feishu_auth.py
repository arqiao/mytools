"""飞书 OAuth 授权工具 - 获取 user_access_token"""

import os
import time
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # src/ 的上级
CREDENTIALS_PATH = os.path.join(PROJECT_DIR, "cfg", "credentials.yaml")


class AuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            AuthCallbackHandler.auth_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>OK</h1><p>authorized</p>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def main():
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        creds = yaml.safe_load(f)

    fs = creds["feishu"]
    scopes = " ".join(fs.get("scopes", []))
    auth_url = (
        f"https://open.feishu.cn/open-apis/authen/v1/authorize?"
        f"app_id={fs['app_id']}&redirect_uri={fs['redirect_uri']}"
        f"&scope={scopes}&state=STATE"
    )

    print(f"Open: {auth_url}\n")
    server = HTTPServer(("localhost", 8080), AuthCallbackHandler)
    server.timeout = 1
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("Waiting for auth callback...")
    start = time.time()
    while AuthCallbackHandler.auth_code is None:
        if time.time() - start > 300:
            print("Timeout")
            return
        time.sleep(1)

    # get app_access_token
    app_resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
        json={"app_id": fs["app_id"], "app_secret": fs["app_secret"]},
    ).json()
    app_token = app_resp["app_access_token"]

    # exchange code for user token
    resp = requests.post(
        "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
        json={"grant_type": "authorization_code", "code": AuthCallbackHandler.auth_code},
        headers={"Authorization": f"Bearer {app_token}"},
    ).json()

    if resp.get("code") != 0:
        print(f"Failed: {resp}")
        return

    td = resp["data"]
    creds["feishu"]["user_access_token"] = td["access_token"]
    creds["feishu"]["user_refresh_token"] = td["refresh_token"]
    creds["feishu"]["user_token_expire_time"] = int(time.time()) + td["expires_in"]

    with open(CREDENTIALS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(creds, f, allow_unicode=True, default_flow_style=False)

    print(f"Done! token: {td['access_token'][:25]}... expires in {td['expires_in']}s")


if __name__ == "__main__":
    main()
