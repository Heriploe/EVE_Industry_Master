import base64
import configparser
import json
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Utilities.name_mapping import load_types_map

ESI_BASE = "https://esi.evetech.net/latest"
LOGIN_BASE = "https://login.eveonline.com"
CONFIG_PATH = REPO_ROOT / "config.ini"


def normalize_scope(raw_scope):
    scope = urllib.parse.unquote(raw_scope or "").replace("+", " ").strip()
    if scope:
        return " ".join(scope.split())
    return (
        "esi-assets.read_assets.v1 "
        "esi-assets.read_corporation_assets.v1 "
        "esi-industry.read_character_jobs.v1 "
        "esi-industry.read_corporation_jobs.v1 "
        "esi-characters.read_blueprints.v1 "
        "esi-corporations.read_blueprints.v1 "
        "esi-universe.read_structures.v1"
    )


def load_settings():
    config = configparser.ConfigParser(interpolation=None)
    if not config.read(CONFIG_PATH, encoding="utf-8"):
        raise FileNotFoundError(f"未找到配置文件: {CONFIG_PATH}")

    if "esi_auth" not in config:
        raise KeyError("config.ini 缺少 [esi_auth] 配置")

    cfg = config["esi_auth"]
    types_file = config.get("paths", "types_json", fallback="Data/types.json")

    return {
        "client_id": cfg.get("client_id", "").strip(),
        "client_secret": cfg.get("client_secret", "").strip(),
        "redirect_uri": cfg.get("redirect_uri", "http://localhost/callback").strip(),
        "scope": normalize_scope(cfg.get("scope", "")),
        "cache_file": REPO_ROOT / cfg.get("token_cache_file", "Cache/Asset/token_cache.json"),
        "output_dir": REPO_ROOT / cfg.get("output_dir", "Cache/Asset"),
        "user_agent": cfg.get("user_agent", "AssetScript/1.0").strip(),
        "types_file": REPO_ROOT / types_file,
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

    auth_params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "state": state,
    }
    query_parts = [
        f"response_type={urllib.parse.quote(str(auth_params['response_type']), safe='')}",
        f"client_id={urllib.parse.quote(str(auth_params['client_id']), safe='')}",
        f"redirect_uri={auth_params['redirect_uri']}",
        f"scope={urllib.parse.quote(scope, safe='')}",
        f"state={urllib.parse.quote(str(auth_params['state']), safe='')}",
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

            if parsed.port is None:
                print(f"redirect_uri 未指定端口，自动尝试监听默认端口 {server_port}。")

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

    print("请在浏览器完成授权后，将回调地址（或 code）粘贴到这里。")
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


def get_all_pages(url, access_token, user_agent):
    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": user_agent}
    page = 1
    results = []

    while True:
        print(f"DEBUG request_url: {url}?page={page}")
        r = requests.get(url, headers=headers, params={"page": page}, timeout=20)
        if r.status_code == 403:
            raise PermissionError(f"403 Forbidden: {url}")
        r.raise_for_status()
        results.extend(r.json())

        pages = int(r.headers.get("X-Pages", 1))
        if page >= pages:
            break
        page += 1
        time.sleep(0.2)

    return results


def split_assets_with_blueprints(assets, blueprints, types_file):
    type_dict = load_types_map(str(types_file))
    blueprint_lookup = {bp["item_id"]: bp for bp in blueprints}

    non_blueprint_assets = []
    blueprint_assets = []

    for asset in assets:
        item_id = asset.get("item_id")
        type_id = asset.get("type_id")
        quantity = asset.get("quantity", 1)
        is_blueprint_copy = asset.get("is_blueprint_copy", False)
        names = type_dict.get(type_id, {"zh": "", "en": ""})

        if item_id in blueprint_lookup:
            bp = blueprint_lookup[item_id]
            blueprint_assets.append(
                {
                    "id": type_id,
                    "zh": names["zh"],
                    "en": names["en"],
                    "material_efficiency": bp.get("material_efficiency", 0),
                    "time_efficiency": bp.get("time_efficiency", 0),
                    "runs": bp.get("runs", -1),
                    "is_blueprint_copy": is_blueprint_copy,
                }
            )
        else:
            non_blueprint_assets.append(
                {
                    "id": type_id,
                    "zh": names["zh"],
                    "en": names["en"],
                    "quantity": quantity,
                }
            )

    return non_blueprint_assets, blueprint_assets


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
                settings["client_id"], settings["client_secret"], refresh_token, settings["user_agent"]
            )
        except Exception as exc:  # noqa: BLE001
            print(f"刷新失败，将重新进行浏览器认证: {exc}")
            access_token = None

    if not access_token:
        code = get_authorization_code(settings["redirect_uri"], settings["client_id"], settings["scope"])
        token_data = exchange_code_for_token(
            settings["client_id"],
            settings["client_secret"],
            code,
            settings["redirect_uri"],
            settings["user_agent"],
        )
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")

    save_json(
        cache_file,
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "updated_at": int(time.time()),
        },
    )

    char_id, char_name = get_character_id(access_token, settings["user_agent"])
    print(f"Character: {char_name} ({char_id})")

    assets = get_all_pages(f"{ESI_BASE}/characters/{char_id}/assets/", access_token, settings["user_agent"])
    print(f"Total assets: {len(assets)}")

    try:
        blueprints = get_all_pages(
            f"{ESI_BASE}/characters/{char_id}/blueprints/",
            access_token,
            settings["user_agent"],
        )
        print(f"Total blueprints: {len(blueprints)}")
    except PermissionError as exc:
        print(f"获取蓝图失败，按无蓝图处理: {exc}")
        blueprints = []

    non_blueprints, blueprint_assets = split_assets_with_blueprints(
        assets,
        blueprints,
        settings["types_file"],
    )

    out_dir = settings["output_dir"]
    save_json(out_dir / "character_assets_raw.json", assets)
    save_json(out_dir / "character_blueprints_raw.json", blueprints)
    save_json(out_dir / "final_non_blueprints.json", non_blueprints)
    save_json(out_dir / "final_blueprints.json", blueprint_assets)

    print(f"Token 缓存: {cache_file}")
    print(f"非蓝图资产 {len(non_blueprints)} 条 -> {out_dir / 'final_non_blueprints.json'}")
    print(f"蓝图资产 {len(blueprint_assets)} 条 -> {out_dir / 'final_blueprints.json'}")


if __name__ == "__main__":
    main()
