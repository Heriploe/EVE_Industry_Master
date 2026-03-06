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

from Utilities.name_mapping import load_types_map

ESI_BASE = "https://esi.evetech.net/latest"
LOGIN_BASE = "https://login.eveonline.com"
REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config.ini"


def normalize_redirect_uri(raw_uri):
    uri = (raw_uri or "").strip()
    if not uri:
        return "http://localhost:5050/callback"
    # 兼容中文输入法下的全角冒号，避免 urlparse NFKC 报错
    uri = uri.replace("：", ":")
    return uri


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
        "redirect_uri": normalize_redirect_uri(cfg.get("redirect_uri", "http://localhost:5050/callback")),
        "scope": normalize_scope(cfg.get("scope", "")),
        "cache_file": REPO_ROOT / cfg.get("token_cache_file", "Cache/Asset/token_cache.json"),
        "output_dir": REPO_ROOT / cfg.get("output_dir", "Cache/Asset"),
        "corp_id": cfg.getint("corp_id", fallback=98822194),
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


def get_asset_names(corp_id, item_ids, access_token, user_agent):
    if not item_ids:
        return []

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": user_agent,
        "Content-Type": "application/json",
    }
    url = f"{ESI_BASE}/corporations/{corp_id}/assets/names/"
    names_raw = []

    def fetch_batch(batch):
        r = requests.post(url, headers=headers, json=batch, timeout=20)
        if r.status_code == 200:
            return r.json()

        if r.status_code == 404:
            # ESI 在批量请求里只要混入一个无效 ID，整批都会 404。
            # 为避免丢掉同批中的有效 ID，这里降级为逐个查询。
            if len(batch) == 1:
                return []

            rows = []
            for item_id in batch:
                single = requests.post(url, headers=headers, json=[item_id], timeout=20)
                if single.status_code == 200:
                    rows.extend(single.json())
                elif single.status_code != 404:
                    raise RuntimeError(f"获取资产名称失败: {single.status_code} {single.text}")
                time.sleep(0.05)
            return rows

        raise RuntimeError(f"获取资产名称失败: {r.status_code} {r.text}")

    ids_list = list(item_ids)
    for idx in range(0, len(ids_list), 1000):
        chunk = ids_list[idx : idx + 1000]
        names_raw.extend(fetch_batch(chunk))
        time.sleep(0.05)

    return names_raw


def build_asset_name_map(names_raw):
    location_name_map = {}
    for row in names_raw or []:
        item_id = row.get("item_id")
        if item_id is not None:
            location_name_map[item_id] = row.get("name")
    return location_name_map


def split_assets_with_blueprints(assets, blueprints, types_file, location_name_map=None):
    type_dict = load_types_map(str(types_file))
    blueprint_lookup = {bp["item_id"]: bp for bp in blueprints}
    location_name_map = location_name_map or {}

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
            location_id = asset.get("location_id")
            blueprint_assets.append(
                {
                    "id": type_id,
                    "zh": names["zh"],
                    "en": names["en"],
                    "material_efficiency": bp.get("material_efficiency", 0),
                    "time_efficiency": bp.get("time_efficiency", 0),
                    "runs": bp.get("runs", -1),
                    "is_blueprint_copy": is_blueprint_copy,
                    "location_flag": bp.get("location_flag"),
                    "container_name": location_name_map.get(location_id),
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


def fetch_all_data(access_token, settings, char_id):
    char_assets = get_all_pages(f"{ESI_BASE}/characters/{char_id}/assets/", access_token, settings["user_agent"])
    char_blueprints = get_all_pages(
        f"{ESI_BASE}/characters/{char_id}/blueprints/",
        access_token,
        settings["user_agent"],
    )

    corp_id = settings["corp_id"]
    print(f"Corp ID: {corp_id}")
    corp_assets = get_all_pages(f"{ESI_BASE}/corporations/{corp_id}/assets/", access_token, settings["user_agent"])
    corp_blueprints = get_all_pages(
        f"{ESI_BASE}/corporations/{corp_id}/blueprints/",
        access_token,
        settings["user_agent"],
    )
    corp_jobs = get_all_pages(
        f"{ESI_BASE}/corporations/{corp_id}/industry/jobs/",
        access_token,
        settings["user_agent"],
    )

    corp_container_ids = {
        bp.get("location_id") for bp in corp_blueprints if bp.get("location_id") is not None
    }
    corp_names_raw = get_asset_names(corp_id, corp_container_ids, access_token, settings["user_agent"])

    return char_assets, char_blueprints, corp_assets, corp_blueprints, corp_jobs, corp_names_raw


def save_assets_bundle(base_dir, assets, blueprints, types_file, location_name_map=None):
    non_blueprints, blueprint_assets = split_assets_with_blueprints(
        assets, blueprints, types_file, location_name_map=location_name_map
    )
    save_json(base_dir / "assets_raw.json", assets)
    save_json(base_dir / "blueprints_raw.json", blueprints)
    save_json(base_dir / "final_non_blueprints.json", non_blueprints)
    save_json(base_dir / "final_blueprints.json", blueprint_assets)


def main():
    settings = load_settings()
    if not settings["client_id"] or not settings["client_secret"]:
        raise ValueError("请先在 config.ini 的 [esi_auth] 中配置 client_id 和 client_secret")

    settings["cache_file"].parent.mkdir(parents=True, exist_ok=True)
    tokens = load_cached_tokens(settings["cache_file"])
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    def cache_tokens(character_id=None, character_name=None):
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "updated_at": int(time.time()),
        }
        if character_id is not None:
            payload["character_id"] = character_id
        if character_name is not None:
            payload["character_name"] = character_name
        save_json(settings["cache_file"], payload)

    if not access_token and refresh_token:
        print("未找到缓存 access_token，尝试使用 refresh_token 获取...")
        try:
            access_token, refresh_token = refresh_access_token(
                settings["client_id"], settings["client_secret"], refresh_token, settings["user_agent"]
            )
            cache_tokens()
        except Exception as exc:  # noqa: BLE001
            print(f"refresh_token 刷新失败: {exc}")
            access_token = None

    if not access_token:
        print("缓存无可用 token，开始浏览器认证...")
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
        cache_tokens()

    try:
        char_id, char_name = get_character_id(access_token, settings["user_agent"])
        cache_tokens(char_id, char_name)
        print(f"Character: {char_name} ({char_id})")
        char_assets, char_blueprints, corp_assets, corp_blueprints, corp_jobs, corp_names_raw = fetch_all_data(
            access_token, settings, char_id
        )
    except Exception as first_exc:  # noqa: BLE001
        print(f"使用缓存 token 获取数据失败: {first_exc}")
        recovered = False

        if refresh_token:
            print("尝试使用 refresh_token 重新获取 access_token...")
            try:
                access_token, refresh_token = refresh_access_token(
                    settings["client_id"], settings["client_secret"], refresh_token, settings["user_agent"]
                )
                cache_tokens()
                char_id, char_name = get_character_id(access_token, settings["user_agent"])
                cache_tokens(char_id, char_name)
                print(f"Character: {char_name} ({char_id})")
                char_assets, char_blueprints, corp_assets, corp_blueprints, corp_jobs, corp_names_raw = fetch_all_data(
                    access_token, settings, char_id
                )
                recovered = True
            except Exception as refresh_exc:  # noqa: BLE001
                print(f"refresh_token 重试失败: {refresh_exc}")

        if not recovered:
            print("启动浏览器重新认证...")
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
            cache_tokens()
            char_id, char_name = get_character_id(access_token, settings["user_agent"])
            cache_tokens(char_id, char_name)
            print(f"Character: {char_name} ({char_id})")
            char_assets, char_blueprints, corp_assets, corp_blueprints, corp_jobs, corp_names_raw = fetch_all_data(
                access_token, settings, char_id
            )

    output_root = settings["output_dir"]
    save_assets_bundle(output_root / "Character", char_assets, char_blueprints, settings["types_file"])

    save_json(output_root / "Corp" / "names_raw.json", corp_names_raw)
    corp_location_name_map = build_asset_name_map(corp_names_raw)
    save_assets_bundle(
        output_root / "Corp",
        corp_assets,
        corp_blueprints,
        settings["types_file"],
        location_name_map=corp_location_name_map,
    )
    save_json(output_root / "Corp" / "industry_jobs_raw.json", corp_jobs)

    print(f"Token 缓存: {settings['cache_file']}")
    print(f"Character 资产输出目录: {output_root / 'Character'}")
    print(f"Corp 资产输出目录: {output_root / 'Corp'}")


if __name__ == "__main__":
    main()
