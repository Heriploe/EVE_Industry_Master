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

LOGIN_BASE = "https://login.eveonline.com"
DEFAULT_SCOPE = (
    "esi-assets.read_assets.v1 "
    "esi-assets.read_corporation_assets.v1 "
    "esi-industry.read_character_jobs.v1 "
    "esi-industry.read_corporation_jobs.v1 "
    "esi-characters.read_blueprints.v1 "
    "esi-corporations.read_blueprints.v1 "
    "esi-universe.read_structures.v1 "
    "esi-markets.structure_markets.v1"
)


def normalize_redirect_uri(raw_uri):
    uri = (raw_uri or "").strip()
    if not uri:
        return "http://localhost:5050/callback"
    uri = uri.replace("：", ":")
    return uri


def normalize_scope(raw_scope, default_scope=DEFAULT_SCOPE):
    scope = urllib.parse.unquote(raw_scope or "").replace("+", " ").strip()
    if scope:
        return " ".join(scope.split())
    return default_scope


def load_auth_settings(config_path, default_scope=DEFAULT_SCOPE):
    config = configparser.ConfigParser(interpolation=None)
    if not config.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(f"未找到配置文件: {config_path}")

    if "esi_auth" not in config:
        raise KeyError("config.ini 缺少 [esi_auth] 配置")

    cfg = config["esi_auth"]
    root = Path(config_path).resolve().parent
    return {
        "client_id": cfg.get("client_id", "").strip(),
        "client_secret": cfg.get("client_secret", "").strip(),
        "redirect_uri": normalize_redirect_uri(cfg.get("redirect_uri", "http://localhost:5050/callback")),
        "scope": normalize_scope(cfg.get("scope", ""), default_scope=default_scope),
        "cache_file": root / cfg.get("token_cache_file", "Cache/Asset/token_cache.json"),
        "output_dir": root / cfg.get("output_dir", "Cache/Asset"),
        "corp_id": cfg.getint("corp_id", fallback=98822194),
        "user_agent": cfg.get("user_agent", "AssetScript/1.0").strip(),
    }


def load_cached_tokens(cache_file):
    if not cache_file.exists():
        return {}
    with cache_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def refresh_access_token(client_id, client_secret, refresh_token, user_agent):
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": user_agent,
    }
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}

    r = requests.post(f"{LOGIN_BASE}/v2/oauth/token", headers=headers, data=data, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Refresh token failed: {r.status_code} {r.text}")

    token = r.json()
    return token["access_token"], token.get("refresh_token", refresh_token)


def exchange_code_for_token(client_id, client_secret, code, redirect_uri, user_agent):
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
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

    r = requests.post(f"{LOGIN_BASE}/v2/oauth/token", headers=headers, data=data, timeout=15)
    r.raise_for_status()
    return r.json()


def parse_code_from_callback_input(user_input):
    value = (user_input or "").strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(value).query)
        return query.get("code", [None])[0]
    return value


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

    query_parts = [
        "response_type=code",
        f"client_id={urllib.parse.quote(client_id, safe='')}",
        f"redirect_uri={redirect_uri}",
        f"scope={urllib.parse.quote(scope, safe='')}",
        f"state={urllib.parse.quote(state, safe='')}",
        "prompt=login",
    ]
    auth_url = f"{LOGIN_BASE}/v2/oauth/authorize?{'&'.join(query_parts)}"

    print("正在打开浏览器进行 EVE SSO 认证...")
    print(f"DEBUG auth_url: {auth_url}")
    if not webbrowser.open(auth_url, new=1, autoraise=True):
        print("自动打开浏览器失败，请手动访问以下链接：")
        print(auth_url)

    server_port = parsed.port if parsed.port is not None else 80
    if parsed.hostname:
        try:
            server = HTTPServer((parsed.hostname, server_port), OAuthCallbackHandler)
            server.timeout = 180
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
        except OSError as exc:
            print(f"自动监听回调失败({exc})，将回退手动输入 code。")

    user_input = input("callback url / code: ")
    code = parse_code_from_callback_input(user_input)
    if not code:
        raise RuntimeError("未解析到授权 code")
    return code


def get_character_id(access_token, user_agent):
    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": user_agent}
    r = requests.get(f"{LOGIN_BASE}/oauth/verify", headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data["CharacterID"], data["CharacterName"]


def get_valid_token(settings: dict) -> str:
    """
    从缓存获取有效的 access_token，过期则用 refresh_token 自动续期。
    首次运行（无缓存）则启动 OAuth 授权流程拿到 token 并缓存。

    settings 字典需包含：
        client_id, client_secret, redirect_uri, scope,
        cache_file (Path 或 str), user_agent
    """
    client_id     = settings["client_id"]
    client_secret = settings["client_secret"]
    redirect_uri  = settings["redirect_uri"]
    scope         = settings["scope"]
    cache_path    = Path(settings["cache_file"])
    user_agent    = settings.get("user_agent", "EVEIndustry/1.0")

    cache = load_cached_tokens(cache_path)

    # ── 1. 有缓存 refresh_token → 直接续期 ──────────────────────
    if cache.get("refresh_token"):
        try:
            access_token, new_refresh = refresh_access_token(
                client_id, client_secret,
                cache["refresh_token"], user_agent
            )
            cache["access_token"]  = access_token
            cache["refresh_token"] = new_refresh
            cache["updated_at"]    = int(time.time())
            save_json(cache_path, cache)
            print(f"[esi_auth] ✓ Token 已续期（角色: {cache.get('character_name', '?')}）")
            return access_token
        except Exception as e:
            print(f"[esi_auth] 续期失败，将重新授权: {e}")

    # ── 2. 无缓存 / 续期失败 → 重新 OAuth 授权 ─────────────────
    print("[esi_auth] 启动 EVE SSO 授权流程...")
    code = get_authorization_code(redirect_uri, client_id, scope)
    token_data = exchange_code_for_token(
        client_id, client_secret, code, redirect_uri, user_agent
    )
    access_token  = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")

    char_id, char_name = get_character_id(access_token, user_agent)
    cache = {
        "access_token":   access_token,
        "refresh_token":  refresh_token,
        "updated_at":     int(time.time()),
        "character_id":   char_id,
        "character_name": char_name,
    }
    save_json(cache_path, cache)
    print(f"[esi_auth] ✓ 授权成功（角色: {char_name}）")
    return access_token
