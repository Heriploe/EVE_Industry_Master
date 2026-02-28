import base64
import configparser
import json
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

ESI_BASE = "https://esi.evetech.net/latest"
LOGIN_BASE = "https://login.eveonline.com"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.ini"


def load_settings():
    config = configparser.ConfigParser()
    if not config.read(CONFIG_PATH, encoding="utf-8"):
        raise FileNotFoundError(f"未找到配置文件: {CONFIG_PATH}")

    section = "esi_auth"
    if section not in config:
        raise KeyError("config.ini 缺少 [esi_auth] 配置")

    cfg = config[section]
    return {
        "client_id": cfg.get("client_id", "").strip(),
        "client_secret": cfg.get("client_secret", "").strip(),
        "redirect_uri": cfg.get("redirect_uri", "http://127.0.0.1:8765/callback").strip(),
        "scope": cfg.get("scope", "esi-assets.read_assets.v1").strip(),
        "cache_file": Path(cfg.get("token_cache_file", "cache/Asset/token_cache.json")),
        "user_agent": cfg.get("user_agent", "AssetScript/1.0").strip(),
    }


def load_cached_tokens(cache_file):
    if not cache_file.exists():
        return {}
    with cache_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_cached_tokens(cache_file, tokens):
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with cache_file.open("w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


def refresh_access_token(client_id, client_secret, refresh_token, user_agent):
    auth = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("utf-8")

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": user_agent,
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }

    r = requests.post(
        "https://login.eveonline.com/v2/oauth/token",
        headers=headers,
        data=data,
        timeout=15
    )

    # 👇 关键调试信息
    if r.status_code != 200:
        print("Status:", r.status_code)
        print("Response:", r.text)
        raise RuntimeError("Refresh token failed")

    token = r.json()
    return token["access_token"], token.get("refresh_token", refresh_token)


def exchange_code_for_token(client_id, client_secret, code, redirect_uri, user_agent):
    auth = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("utf-8")

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": user_agent,
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    r = requests.post(
        f"{LOGIN_BASE}/v2/oauth/token",
        headers=headers,
        data=data,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_authorization_code(redirect_uri, client_id, scope):
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http":
        raise ValueError("redirect_uri 必须是本地 http 地址")

    state = secrets.token_urlsafe(16)
    callback_data = {"code": None, "state": None, "error": None}

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            callback_data["code"] = query.get("code", [None])[0]
            callback_data["state"] = query.get("state", [None])[0]
            callback_data["error"] = query.get("error", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h3>认证完成，可关闭页面返回终端。</h3>".encode("utf-8"))

        def log_message(self, format, *args):  # noqa: A003
            return

    server = HTTPServer((parsed.hostname, parsed.port), OAuthCallbackHandler)
    server.timeout = 180

    auth_params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": scope,
        "state": state,
    }
    auth_url = f"{LOGIN_BASE}/v2/oauth/authorize?{urllib.parse.urlencode(auth_params)}"

    print("正在打开浏览器进行 EVE SSO 认证...")
    if not webbrowser.open(auth_url, new=1, autoraise=True):
        print("自动打开浏览器失败，请手动访问以下链接：")
        print(auth_url)

    deadline = time.time() + 180
    while time.time() < deadline and callback_data["code"] is None and callback_data["error"] is None:
        server.handle_request()

    server.server_close()

    if callback_data["error"]:
        raise RuntimeError(f"认证失败: {callback_data['error']}")
    if callback_data["state"] != state:
        raise RuntimeError("state 校验失败")
    if not callback_data["code"]:
        raise TimeoutError("等待认证回调超时")

    return callback_data["code"]


def get_character_id(access_token, user_agent):
    """
    从 access_token 中获取角色 ID
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": user_agent,
    }

    r = requests.get(
        f"{LOGIN_BASE}/oauth/verify",
        headers=headers,
        timeout=10
    )
    r.raise_for_status()

    data = r.json()
    return data["CharacterID"], data["CharacterName"]


def get_all_character_assets(character_id, access_token, user_agent):
    """
    自动处理 X-Pages，获取全部资产
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": user_agent,
    }

    page = 1
    assets = []

    while True:
        r = requests.get(
            f"{ESI_BASE}/characters/{character_id}/assets/",
            headers=headers,
            params={"page": page},
            timeout=20
        )
        r.raise_for_status()

        assets.extend(r.json())

        pages = int(r.headers.get("X-Pages", 1))
        if page >= pages:
            break

        page += 1
        time.sleep(0.2)  # 防止 420

    return assets


def main():
    settings = load_settings()
    if not settings["client_id"] or not settings["client_secret"]:
        raise ValueError("请先在 config.ini 的 [esi_auth] 中配置 client_id 和 client_secret")

    cache_file = settings["cache_file"]
    tokens = load_cached_tokens(cache_file)

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if refresh_token:
        print("检测到缓存 refresh_token，优先尝试刷新 access_token...")
        try:
            access_token, refresh_token = refresh_access_token(
                settings["client_id"],
                settings["client_secret"],
                refresh_token,
                settings["user_agent"],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"刷新失败，将重新进行浏览器认证: {exc}")
            access_token = None

    if not access_token:
        code = get_authorization_code(
            settings["redirect_uri"],
            settings["client_id"],
            settings["scope"],
        )
        token_data = exchange_code_for_token(
            settings["client_id"],
            settings["client_secret"],
            code,
            settings["redirect_uri"],
            settings["user_agent"],
        )
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")

    save_cached_tokens(
        cache_file,
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "updated_at": int(time.time()),
        },
    )

    # 2. 获取角色信息
    char_id, char_name = get_character_id(access_token, settings["user_agent"])
    print(f"Character: {char_name} ({char_id})")

    # 3. 获取资产
    assets = get_all_character_assets(char_id, access_token, settings["user_agent"])
    print(f"Total assets: {len(assets)}")

    # 4. 打印部分示例
    for a in assets[:10]:
        print(a)

    print(f"Token 缓存已写入: {cache_file}")


if __name__ == "__main__":
    main()
