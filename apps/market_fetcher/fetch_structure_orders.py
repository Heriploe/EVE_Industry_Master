import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utilities.data.app_config import load_app_config, load_meta, resolve
from utilities.esi.esi_auth import (
    exchange_code_for_token,
    get_authorization_code,
    get_character_id,
    load_cached_tokens,
    refresh_access_token,
    save_json,
)

ESI_BASE = "https://esi.evetech.net/latest"


def get_all_pages(url, access_token, user_agent, params=None):
    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": user_agent}
    page = 1
    results = []

    while True:
        query = dict(params or {})
        query["page"] = page
        r = requests.get(url, headers=headers, params=query, timeout=20)
        if r.status_code == 403:
            raise PermissionError(f"403 Forbidden: {url}")
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            results.extend(data)
        else:
            return data

        pages = int(r.headers.get("X-Pages", 1))
        if page >= pages:
            break
        page += 1
        time.sleep(0.2)

    return results


def ensure_access_token(settings):
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
        access_token, refresh_token = refresh_access_token(
            settings["client_id"], settings["client_secret"], refresh_token, settings["user_agent"]
        )
        cache_tokens()

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
        cache_tokens()

    try:
        character_id, character_name = get_character_id(access_token, settings["user_agent"])
        cache_tokens(character_id, character_name)
        return access_token, character_id, character_name
    except Exception:
        if not refresh_token:
            raise
        access_token, refresh_token = refresh_access_token(
            settings["client_id"], settings["client_secret"], refresh_token, settings["user_agent"]
        )
        cache_tokens()
        character_id, character_name = get_character_id(access_token, settings["user_agent"])
        cache_tokens(character_id, character_name)
        return access_token, character_id, character_name


def fetch_structure_info(structure_id, access_token, user_agent):
    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": user_agent}
    r = requests.get(f"{ESI_BASE}/universe/structures/{structure_id}/", headers=headers, timeout=20)
    if r.status_code in {403, 404}:
        return {"structure_id": structure_id, "name": None, "note": "不可见或无权限"}
    if r.status_code == 503:
        return {"structure_id": structure_id, "name": None, "note": "ESI 暂时不可用(503)"}
    r.raise_for_status()
    data = r.json()
    return {"structure_id": structure_id, "name": data.get("name"), "solar_system_id": data.get("solar_system_id")}


def collect_asset_structures(access_token, user_agent, char_id):
    assets = get_all_pages(f"{ESI_BASE}/characters/{char_id}/assets/", access_token, user_agent)
    structure_ids = sorted(
        {
            int(asset.get("location_id"))
            for asset in assets
            if isinstance(asset.get("location_id"), int) and int(asset.get("location_id")) >= 1_000_000_000_000
        }
    )
    structures = [fetch_structure_info(structure_id, access_token, user_agent) for structure_id in structure_ids]
    return structures


def fetch_structure_orders(access_token, user_agent, structure_id, type_id=None):
    params = {}
    if type_id is not None:
        params["type_id"] = int(type_id)
    return get_all_pages(
        f"{ESI_BASE}/markets/structures/{int(structure_id)}/",
        access_token,
        user_agent,
        params=params,
    )


def main():
    parser = argparse.ArgumentParser(description="读取角色资产相关建筑，并按 type_id 拉取建筑市场订单")
    parser.add_argument("--type-id", type=int, help="物品 type_id，传入后会拉取对应结构订单")
    parser.add_argument("--structure-id", type=int, help="要拉取订单的 structure_id")
    parser.add_argument(
        "--output",
        default="Cache/Market/structure_orders.json",
        help="订单输出文件（默认 Cache/Market/structure_orders.json）",
    )
    args = parser.parse_args()

    cfg, eve_root = load_app_config()
    meta = load_meta(eve_root)
    esi = meta.get("esi", {})
    settings = {
        "client_id":    esi.get("client_id", ""),
        "client_secret":esi.get("client_secret", ""),
        "redirect_uri": esi.get("redirect_uri", "http://localhost:5050/callback"),
        "scope":        " ".join(esi.get("scopes", [])),
        "user_agent":   esi.get("user_agent", "eve-tools/1.0"),
        "cache_file":   resolve(eve_root, meta.get("token_cache", "resources/auth/token_cache.json")),
    }
    if not settings["client_id"] or not settings["client_secret"]:
        raise ValueError("请先在 eve/config_meta.json 的 \"esi\" 中配置 client_id 和 client_secret")

    access_token, char_id, char_name = ensure_access_token(settings)
    print(f"Character: {char_name} ({char_id})")

    structures = collect_asset_structures(access_token, settings["user_agent"], char_id)
    print("角色资产涉及的 structure：")
    if not structures:
        print("  (无可识别 structure_id)")
    for row in structures:
        name = row.get("name") or row.get("note") or "未知"
        print(f"  - {row['structure_id']}: {name}")

    if args.structure_id and args.type_id:
        orders = fetch_structure_orders(
            access_token=access_token,
            user_agent=settings["user_agent"],
            structure_id=args.structure_id,
            type_id=args.type_id,
        )
        output_path = resolve(eve_root, args.output) if args.output else resolve(eve_root, "resources/market/structure_cache.json")
        save_json(
            output_path,
            {
                "character_id": char_id,
                "character_name": char_name,
                "structure_id": args.structure_id,
                "type_id": args.type_id,
                "order_count": len(orders),
                "orders": orders,
            },
        )
        print(f"已输出 {len(orders)} 条订单到: {output_path}")
    elif args.structure_id or args.type_id:
        raise ValueError("拉取订单时需同时提供 --structure-id 和 --type-id")


if __name__ == "__main__":
    main()
