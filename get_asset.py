import configparser
import json
import time
from pathlib import Path

import requests

from Utilities.esi_auth import (
    exchange_code_for_token,
    get_authorization_code,
    get_character_id,
    load_auth_settings,
    load_cached_tokens,
    refresh_access_token,
    save_json,
)
from Utilities.name_mapping import load_types_map

ESI_BASE = "https://esi.evetech.net/latest"
REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config.ini"


def load_settings():
    settings = load_auth_settings(CONFIG_PATH)

    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_PATH, encoding="utf-8")
    types_file = config.get("paths", "types_json", fallback="Data/types.json")
    settings["types_file"] = REPO_ROOT / types_file
    return settings


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


def export_corp_blueprint_name_map(final_blueprints, industry_jobs, types_file, output_path):
    type_dict = load_types_map(str(types_file))
    blueprint_name_map = {}

    for blueprint in final_blueprints:
        if blueprint.get("is_blueprint_copy", False):
            continue

        blueprint_type_id = blueprint.get("id")
        if blueprint_type_id is None:
            continue

        name = blueprint.get("zh") or blueprint.get("en")
        if not name:
            names = type_dict.get(blueprint_type_id, {"zh": "", "en": ""})
            name = names["zh"] or names["en"] or str(blueprint_type_id)
        blueprint_name_map[blueprint_type_id] = name

    for job in industry_jobs:
        if job.get("activity_id") not in {3, 4}:
            continue

        blueprint_type_id = job.get("blueprint_type_id")
        if blueprint_type_id is None:
            continue

        names = type_dict.get(blueprint_type_id, {"zh": "", "en": ""})
        name = names["zh"] or names["en"] or str(blueprint_type_id)
        blueprint_name_map[blueprint_type_id] = name

    sorted_blueprint_name_map = {
        str(type_id): blueprint_name_map[type_id] for type_id in sorted(blueprint_name_map)
    }
    save_json(output_path, sorted_blueprint_name_map)


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
    _, corp_final_blueprints = split_assets_with_blueprints(
        corp_assets,
        corp_blueprints,
        settings["types_file"],
        location_name_map=corp_location_name_map,
    )
    export_corp_blueprint_name_map(
        corp_final_blueprints,
        corp_jobs,
        settings["types_file"],
        output_root / "Corp" / "blueprint_id_name_map.json",
    )

    print(f"Token 缓存: {settings['cache_file']}")
    print(f"Character 资产输出目录: {output_root / 'Character'}")
    print(f"Corporation 资产输出目录: {output_root / 'Corp'}")


if __name__ == "__main__":
    main()
