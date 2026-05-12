"""
fetch_assets.py  ─  ESI 资产同步
=================================
输出文件（resources/corp/ 和 resources/character/）:
  bpo.json            蓝图原本（runs=-1）
  bpc.json            蓝图拷贝（runs>0）
  industry_jobs.json  已解析工业任务
  containers.json     容器内容
  structures.json     建筑名称（已解析）
  materials.json      非蓝图资产

  resources/auth/
  installer_names.json  installer_id → 角色名

用法:
  python fetch_assets.py [--config /path/config.json]
"""

# ── Imports ────────────────────────────────────────────────────────────────────
import argparse
import json
import sys
import time
from pathlib import Path

import requests

_APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_APP_ROOT.parent.parent))

from utilities.data.app_config import load_app_config, load_meta, resolve
from utilities.data.name_mapping import load_types_map
from utilities.esi.esi_auth import (
    exchange_code_for_token,
    get_authorization_code,
    get_character_id,
    load_cached_tokens,
    refresh_access_token,
    save_json,
)
from utilities.io.loaders import save_json as io_save_json

# ── Constants ──────────────────────────────────────────────────────────────────
ESI_BASE = "https://esi.evetech.net/latest"

# type_id 集合：视为"容器"的物品（可根据游戏版本扩充）
CONTAINER_TYPE_IDS: set[int] = {
    17366, 3297, 12383, 33003, 33005, 33007, 33009, 33011,
    1657, 17621, 17622, 17623, 17624, 17625, 17626,
}

# activity_id → 活动名称
ACTIVITY_NAMES: dict[int, str] = {
    1: "manufacturing", 3: "te_research", 4: "me_research",
    5: "copying", 8: "invention", 11: "reaction",
}


# ── Config ─────────────────────────────────────────────────────────────────────
def build_settings(cfg: dict, meta: dict, eve_root: Path) -> dict:
    esi     = meta.get("esi", {})
    out_cfg = cfg.get("output", {})
    return {
        "client_id":     esi.get("client_id", ""),
        "client_secret": esi.get("client_secret", ""),
        "redirect_uri":  esi.get("redirect_uri", "http://localhost:5050/callback"),
        "scope":         " ".join(esi.get("scopes", [])),
        "user_agent":    esi.get("user_agent", "eve-tools/1.0"),
        "cache_file":    resolve(eve_root, meta.get("token_cache", "resources/auth/token_cache.json")),
        "types_file":    resolve(eve_root, cfg["data"]["types"]),
        "corp_out":      resolve(eve_root, out_cfg.get("corp_dir",  "resources/corp")),
        "char_out":      resolve(eve_root, out_cfg.get("char_dir",  "resources/character")),
        "auth_out":      resolve(eve_root, "resources/auth"),
        "corp_id":       esi.get("corp_id") or None,
    }


# ── ESI helpers ────────────────────────────────────────────────────────────────
def esi_get(url: str, token: str, user_agent: str, params: dict = None) -> dict | list:
    headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent}
    r = requests.get(url, headers=headers, params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json()


def get_all_pages(url: str, token: str, user_agent: str) -> list:
    headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent}
    results, page = [], 1
    while True:
        r = requests.get(url, headers=headers, params={"page": page}, timeout=20)
        if r.status_code == 403:
            raise PermissionError(f"403 Forbidden: {url}")
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        results.extend(batch)
        if page >= int(r.headers.get("X-Pages", 1)):
            break
        page += 1
        time.sleep(0.2)
    return results


def get_corp_id(char_id: int, token: str, user_agent: str) -> int:
    data = esi_get(f"{ESI_BASE}/characters/{char_id}/", token, user_agent)
    corp_id = data["corporation_id"]
    print(f"  Corp ID（自动获取）: {corp_id}")
    return corp_id


def resolve_character_names(char_ids: set, token: str, user_agent: str) -> dict[int, str]:
    """POST /characters/affiliation/ → {char_id: name}"""
    if not char_ids:
        return {}
    result: dict[int, str] = {}
    ids_list = list(char_ids)
    for i in range(0, len(ids_list), 1000):
        chunk = ids_list[i:i+1000]
        try:
            r = requests.post(
                f"{ESI_BASE}/characters/affiliation/",
                json=chunk,
                headers={"User-Agent": user_agent},
                timeout=20,
            )
            # affiliation 返回 corporation_id 等，不含 name
            # 改用 /characters/{id}/ 批量查
        except Exception:
            pass
    # ESI 无批量角色名接口，逐个查
    for cid in ids_list:
        try:
            data = esi_get(f"{ESI_BASE}/characters/{cid}/", token, user_agent)
            result[cid] = data.get("name", str(cid))
            time.sleep(0.05)
        except Exception:
            result[cid] = str(cid)
    return result


def resolve_structure_names(structure_ids: set, token: str, user_agent: str) -> dict[int, str]:
    """返回 {structure_id: name}"""
    result: dict[int, str] = {}
    for sid in structure_ids:
        try:
            data = esi_get(f"{ESI_BASE}/universe/structures/{sid}/", token, user_agent)
            result[sid] = data.get("name", str(sid))
        except Exception:
            result[sid] = str(sid)
        time.sleep(0.05)
    return result


def get_asset_names_batch(entity_id: int, item_ids: set,
                          token: str, user_agent: str,
                          entity_type: str = "corporations") -> dict[int, str]:
    """返回 {item_id: name}，支持 corporations / characters"""
    if not item_ids:
        return {}
    url     = f"{ESI_BASE}/{entity_type}/{entity_id}/assets/names/"
    headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent,
               "Content-Type": "application/json"}
    result: dict[int, str] = {}
    ids_list = list(item_ids)
    for i in range(0, len(ids_list), 1000):
        chunk = ids_list[i:i+1000]
        try:
            r = requests.post(url, headers=headers, json=chunk, timeout=20)
            if r.status_code == 200:
                for row in r.json():
                    result[row["item_id"]] = row.get("name", "")
        except Exception:
            pass
        time.sleep(0.05)
    return result


# ── Parsing ────────────────────────────────────────────────────────────────────
def split_blueprints(assets: list, blueprints: list,
                     types_map: dict, container_names: dict
                     ) -> tuple[list[dict], list[dict]]:
    """将 assets + blueprints 拆分为 (bpo_list, bpc_list)"""
    bp_lookup: dict[int, dict] = {bp["item_id"]: bp for bp in blueprints}
    bpos, bpcs = [], []

    for asset in assets:
        item_id = asset.get("item_id")
        if item_id not in bp_lookup:
            continue
        bp      = bp_lookup[item_id]
        type_id = asset.get("type_id")
        names   = types_map.get(type_id, {})
        runs    = bp.get("runs", -1)
        loc_id  = asset.get("location_id")

        rec = {
            "item_id":             item_id,
            "type_id":             type_id,
            "zh":                  names.get("zh", ""),
            "en":                  names.get("en", ""),
            "material_efficiency": bp.get("material_efficiency", 0),
            "time_efficiency":     bp.get("time_efficiency", 0),
            "runs":                runs,
            "location_id":         loc_id,
            "location_flag":       bp.get("location_flag", ""),
            "container_name":      container_names.get(loc_id, ""),
        }
        if runs == -1:
            bpos.append(rec)
        else:
            bpcs.append(rec)

    sort_key = lambda x: (x.get("zh") or x.get("en") or "")
    return sorted(bpos, key=sort_key), sorted(bpcs, key=sort_key)


def parse_materials(assets: list, blueprints: list, types_map: dict) -> list[dict]:
    bp_item_ids = {bp["item_id"] for bp in blueprints}
    result = []
    for asset in assets:
        if asset.get("item_id") in bp_item_ids:
            continue
        type_id = asset.get("type_id")
        names   = types_map.get(type_id, {})
        result.append({
            "id":       type_id,
            "zh":       names.get("zh", ""),
            "en":       names.get("en", ""),
            "quantity": asset.get("quantity", 1),
        })
    return result


def parse_industry_jobs(jobs: list, types_map: dict) -> list[dict]:
    """
    解析工业任务。
    ESI 字段说明：
      blueprint_type_id  蓝图类型 ID
      product_type_id    产物类型 ID（activity=1 时为制造产物，其他活动可能为空）
    """
    result = []
    for job in jobs:
        bp_type_id   = job.get("blueprint_type_id")
        prod_type_id = job.get("product_type_id")
        activity_id  = job.get("activity_id", 0)

        # 拷贝/发明等活动的产物 type_id 需要从蓝图推断，ESI 直接提供 product_type_id
        bp_names   = types_map.get(bp_type_id,   {})
        prod_names = types_map.get(prod_type_id, {}) if prod_type_id else {}

        result.append({
            "job_id":             job.get("job_id"),
            "activity_id":        activity_id,
            "activity_name":      ACTIVITY_NAMES.get(activity_id, f"activity_{activity_id}"),
            "blueprint_type_id":  bp_type_id,
            "blueprint_zh":       bp_names.get("zh", ""),
            "blueprint_en":       bp_names.get("en", ""),
            "product_type_id":    prod_type_id,
            "product_zh":         prod_names.get("zh", ""),
            "product_en":         prod_names.get("en", ""),
            "runs":               job.get("runs", 0),
            "licensed_runs":      job.get("licensed_runs", 0),
            "status":             job.get("status", ""),
            "start_date":         job.get("start_date", ""),
            "end_date":           job.get("end_date", ""),
            "installer_id":       job.get("installer_id"),
            "location_id":        job.get("location_id"),
            "output_location_id": job.get("output_location_id"),
            "cost":               job.get("cost", 0.0),
        })
    return result


def build_containers(assets: list, types_map: dict,
                     item_names: dict) -> list[dict]:
    """返回容器列表，每个包含 {item_id, name, type_id, type_zh, contents[]}"""
    container_item_ids = {
        a["item_id"] for a in assets if a.get("type_id") in CONTAINER_TYPE_IDS
    }
    children: dict[int, list] = {cid: [] for cid in container_item_ids}
    for asset in assets:
        loc = asset.get("location_id")
        if loc in children:
            type_id = asset.get("type_id")
            names   = types_map.get(type_id, {})
            children[loc].append({
                "type_id":  type_id,
                "zh":       names.get("zh", ""),
                "en":       names.get("en", ""),
                "quantity": asset.get("quantity", 1),
            })

    result = []
    for asset in assets:
        item_id = asset.get("item_id")
        if item_id not in container_item_ids:
            continue
        type_id = asset.get("type_id")
        names   = types_map.get(type_id, {})
        result.append({
            "item_id":  item_id,
            "name":     item_names.get(item_id, str(item_id)),
            "type_id":  type_id,
            "type_zh":  names.get("zh", ""),
            "type_en":  names.get("en", ""),
            "contents": children.get(item_id, []),
        })
    return result


def collect_structure_ids(assets: list) -> set[int]:
    """收集资产中的建筑 location_id（structure_id > 1e12）"""
    return {
        a["location_id"] for a in assets
        if isinstance(a.get("location_id"), int) and a["location_id"] > 1_000_000_000_000
    }


# ── Save helpers ───────────────────────────────────────────────────────────────
def save_character_assets(out_dir: Path, assets: list, blueprints: list,
                          types_map: dict, token: str, char_id: int,
                          user_agent: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 容器名称
    container_item_ids = {a["item_id"] for a in assets if a.get("type_id") in CONTAINER_TYPE_IDS}
    container_names    = get_asset_names_batch(char_id, container_item_ids, token, user_agent,
                                               entity_type="characters")

    bpos, bpcs = split_blueprints(assets, blueprints, types_map, container_names)
    save_json(out_dir / "bpo.json",       bpos)
    save_json(out_dir / "bpc.json",       bpcs)
    save_json(out_dir / "materials.json", parse_materials(assets, blueprints, types_map))

    # 容器
    item_names  = get_asset_names_batch(char_id, container_item_ids, token, user_agent,
                                        entity_type="characters")
    containers  = build_containers(assets, types_map, item_names)
    save_json(out_dir / "containers.json", containers)

    # 建筑名称
    structure_ids  = collect_structure_ids(assets)
    structure_names = resolve_structure_names(structure_ids, token, user_agent) if structure_ids else {}
    save_json(out_dir / "structures.json", structure_names)

    print(f"  角色 BPO={len(bpos)}  BPC={len(bpcs)}  "
          f"容器={len(containers)}  建筑={len(structure_names)}")


def save_corp_assets(out_dir: Path, assets: list, blueprints: list,
                     jobs: list, types_map: dict,
                     token: str, corp_id: int, user_agent: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 容器名称（用于 bpo/bpc 的 container_name 字段）
    bp_location_ids = {bp.get("location_id") for bp in blueprints if bp.get("location_id")}
    print(f"  获取 {len(bp_location_ids)} 个容器名称...")
    container_names = get_asset_names_batch(corp_id, bp_location_ids, token, user_agent)

    bpos, bpcs = split_blueprints(assets, blueprints, types_map, container_names)
    save_json(out_dir / "bpo.json", bpos)
    save_json(out_dir / "bpc.json", bpcs)
    print(f"  BPO={len(bpos)}  BPC={len(bpcs)}")

    # 工业任务
    parsed_jobs = parse_industry_jobs(jobs, types_map)
    save_json(out_dir / "industry_jobs.json", parsed_jobs)
    print(f"  industry_jobs={len(parsed_jobs)}")

    # 容器内容
    container_item_ids = {a["item_id"] for a in assets if a.get("type_id") in CONTAINER_TYPE_IDS}
    item_names  = get_asset_names_batch(corp_id, container_item_ids, token, user_agent)
    containers  = build_containers(assets, types_map, item_names)
    save_json(out_dir / "containers.json", containers)
    print(f"  containers={len(containers)}")

    # 建筑名称
    structure_ids   = collect_structure_ids(assets)
    print(f"  解析 {len(structure_ids)} 个建筑名称...")
    structure_names = resolve_structure_names(structure_ids, token, user_agent) if structure_ids else {}
    save_json(out_dir / "structures.json", structure_names)
    print(f"  structures={len(structure_names)}")

    # 材料
    materials = parse_materials(assets, blueprints, types_map)
    save_json(out_dir / "materials.json", materials)
    print(f"  materials={len(materials)}")


def save_installer_names(auth_out: Path, jobs: list,
                         token: str, user_agent: str) -> None:
    """解析所有 installer_id → 角色名，存入 resources/auth/installer_names.json"""
    installer_ids = {j["installer_id"] for j in jobs if j.get("installer_id")}
    if not installer_ids:
        return
    print(f"  解析 {len(installer_ids)} 个 installer 角色名...")
    names = resolve_character_names(installer_ids, token, user_agent)
    auth_out.mkdir(parents=True, exist_ok=True)
    save_json(auth_out / "installer_names.json", names)
    print(f"  installer_names={len(names)}")


# ── Auth flow ──────────────────────────────────────────────────────────────────
def ensure_token(settings: dict) -> tuple[str, str, int, str]:
    """返回 (access_token, refresh_token, char_id, char_name)"""
    cache_file    = settings["cache_file"]
    client_id     = settings["client_id"]
    client_secret = settings["client_secret"]
    redirect_uri  = settings["redirect_uri"]
    user_agent    = settings["user_agent"]
    scope         = settings["scope"]

    tokens        = load_cached_tokens(cache_file)
    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    def _persist(at: str, rt: str, cid: int = None,
                 cname: str = None, corp_id: int = None) -> None:
        payload = {"access_token": at, "refresh_token": rt,
                   "updated_at": int(time.time())}
        if cid:     payload["character_id"]   = cid
        if cname:   payload["character_name"] = cname
        if corp_id: payload["corp_id"]        = corp_id
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        save_json(cache_file, payload)

    def _validate(at: str, rt: str) -> tuple[str, str, int, str]:
        cid, cname = get_character_id(at, user_agent)
        return at, rt, cid, cname

    if access_token:
        try:
            return _validate(access_token, refresh_token)
        except Exception:
            pass

    if refresh_token:
        try:
            access_token, refresh_token = refresh_access_token(
                client_id, client_secret, refresh_token, user_agent)
            _persist(access_token, refresh_token)
            return _validate(access_token, refresh_token)
        except Exception as e:
            print(f"  refresh_token 失败: {e}")

    print("启动浏览器认证...")
    code = get_authorization_code(redirect_uri, client_id, scope)
    td   = exchange_code_for_token(client_id, client_secret, code, redirect_uri, user_agent)
    access_token  = td["access_token"]
    refresh_token = td.get("refresh_token", "")
    _persist(access_token, refresh_token)
    return _validate(access_token, refresh_token)


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="ESI 资产同步")
    parser.add_argument("--config", default=None, help="config.json 路径")
    args = parser.parse_args()

    cfg, eve_root = load_app_config(args.config)
    meta     = load_meta(eve_root)
    settings = build_settings(cfg, meta, eve_root)

    if not settings["client_id"] or not settings["client_secret"]:
        raise ValueError("请在 eve/config_meta.json 的 \"esi\" 中配置 client_id 和 client_secret")

    access_token, refresh_token, char_id, char_name = ensure_token(settings)

    corp_id = settings.get("corp_id") or get_corp_id(char_id, access_token, settings["user_agent"])
    settings["corp_id"] = corp_id

    # 缓存认证信息
    cache = load_cached_tokens(settings["cache_file"])
    cache.update({
        "access_token": access_token, "refresh_token": refresh_token,
        "character_id": char_id,      "character_name": char_name,
        "corp_id":      corp_id,      "updated_at": int(time.time()),
    })
    save_json(settings["cache_file"], cache)
    print(f"角色: {char_name} ({char_id})  公司: {corp_id}")

    types_map = load_types_map(str(settings["types_file"]))

    # 拉取数据
    print("\n拉取角色资产...")
    char_assets     = get_all_pages(f"{ESI_BASE}/characters/{char_id}/assets/",     access_token, settings["user_agent"])
    char_blueprints = get_all_pages(f"{ESI_BASE}/characters/{char_id}/blueprints/", access_token, settings["user_agent"])

    print("\n拉取军团资产...")
    corp_assets     = get_all_pages(f"{ESI_BASE}/corporations/{corp_id}/assets/",        access_token, settings["user_agent"])
    corp_blueprints = get_all_pages(f"{ESI_BASE}/corporations/{corp_id}/blueprints/",    access_token, settings["user_agent"])
    corp_jobs       = get_all_pages(f"{ESI_BASE}/corporations/{corp_id}/industry/jobs/", access_token, settings["user_agent"])

    # 保存
    print("\n保存角色资产...")
    save_character_assets(settings["char_out"], char_assets, char_blueprints,
                          types_map, access_token, char_id, settings["user_agent"])

    print("\n保存军团资产...")
    save_corp_assets(settings["corp_out"], corp_assets, corp_blueprints,
                     corp_jobs, types_map,
                     access_token, corp_id, settings["user_agent"])

    print("\n解析 installer 角色名...")
    parsed_jobs = parse_industry_jobs(corp_jobs, types_map)
    save_installer_names(settings["auth_out"], parsed_jobs,
                         access_token, settings["user_agent"])

    print(f"\n✓ 完成")
    print(f"  角色资产 → {settings['char_out']}")
    print(f"  军团资产 → {settings['corp_out']}")
    print(f"  认证缓存 → {settings['auth_out']}")


if __name__ == "__main__":
    main()
