"""
build_t2_blueprint_costs.py
===========================
计算每个 T2 蓝图在最优解码器下的单次生产成本，写入 Data/T2_blueprint_costs.json。

修复：
  - 使用 config_utils.REPO_ROOT，移除重复的根目录查找逻辑
  - 使用 industry_cost 的缓存版 get_T1_from_T2

兼容 Python 3.8+。
"""

import json
import sys
from pathlib import Path

from Utilities.config_utils import REPO_ROOT, load_config, resolve_config_path
from Utilities.industry_cost import (
    get_T1_from_T2,
    invention_T2_runs,
    _load_blueprints,
    _load_price_adjusted_map,
    _load_types_map,
    _load_t2_t1_pairs,
)

if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))


def _resolve(path_value):
    p = Path(path_value)
    return p if p.is_absolute() else REPO_ROOT / p


def _load_config():
    return load_config()


def _load_jita_buy_map(path, region_key="jita"):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            buy = (v or {}).get("jita", {}).get("lowest", (v or {}).get("jita", {}).get("buy", 0))
            out[int(k)] = float(buy or 0)
    elif isinstance(data, list):
        for row in data:
            tid = row.get("id")
            if tid is None:
                continue
            buy = ((row.get(region_key) or {}).get("lowest", 0))
            out[int(tid)] = float(buy or 0)
    return out


def _load_decryptors(path):
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    return [r for r in rows if r.get("id") is not None]


def _build_costs():
    cfg = _load_config()
    jita_path            = _resolve(cfg.get("calculator", "jita_prices_json",    fallback="Cache/Market/price_materials_all.json"))
    blueprints_yaml_path = _resolve(cfg.get("paths",      "blueprints_yaml",     fallback="Data/blueprints.yaml"))
    t2_t1_path           = _resolve(cfg.get("paths",      "t2_t1_json",          fallback="Data/T2_T1.json"))
    decryptor_path       = REPO_ROOT / "Data/Materials/Basic/decryptor.json"
    price_adjusted_path  = _resolve(cfg.get("paths",      "price_adjusted_json", fallback="Data/price_adjusted.json"))
    types_json_path      = _resolve(cfg.get("paths",      "types_json",          fallback="Data/types.json"))
    output_path          = REPO_ROOT / "Data/T2_blueprint_costs.json"

    me_cfg               = cfg.getfloat("calculator", "me",                  fallback=0.125)
    material_cost_factor = cfg.getfloat("calculator", "material_cost_factor", fallback=1.0)
    region_key           = cfg.get("calculator", "price_region_key",          fallback="jita")
    base_price_source    = cfg.get("industry_cost", "base_price_source",      fallback="types_base").lower()

    t2_t1_pairs  = _load_t2_t1_pairs(t2_t1_path)  # 使用缓存版，仅读一次
    jita_buy     = _load_jita_buy_map(jita_path, region_key=region_key)
    decryptors   = _load_decryptors(decryptor_path)
    blueprints   = _load_blueprints(blueprints_yaml_path=blueprints_yaml_path)
    price_adjusted_map = _load_price_adjusted_map(price_adjusted_json_path=price_adjusted_path)
    types_map    = _load_types_map(types_json_path=types_json_path)

    def _base_price(type_id):
        type_id = int(type_id)
        if base_price_source == "adjusted_price":
            val = price_adjusted_map.get(type_id, {}).get("adjusted_price")
        elif base_price_source == "average_price":
            val = price_adjusted_map.get(type_id, {}).get("average_price")
        else:
            val = types_map.get(type_id, {}).get("basePrice")
        return float(val) if isinstance(val, (int, float)) else 0.0

    def _eiv(materials):
        return sum(float(m.get("quantity", 0)) * _base_price(m.get("typeID")) for m in materials)

    def _activity_cost_per_run(jcb, activity):
        sys_mod = cfg.getfloat("industry_cost", f"system_modifier_{activity}",    fallback=1.0)
        fac     = cfg.getfloat("industry_cost", f"facility_reduction_{activity}", fallback=0.0)
        rig     = cfg.getfloat("industry_cost", f"rig_reduction_{activity}",      fallback=0.0)
        return jcb * sys_mod * (1 - fac) * (1 - rig) + 0.04 * jcb

    result = {}
    for t2_bp_id, t1_bp_id in t2_t1_pairs:
        t2_bp = blueprints.get(int(t2_bp_id), {})
        manu  = (t2_bp.get("activities") or {}).get("manufacturing", {})
        manu_materials = manu.get("materials", [])
        if not manu_materials:
            continue

        t1_bp      = blueprints.get(int(t1_bp_id), {})
        invention  = (t1_bp.get("activities") or {}).get("invention", {})
        inv_mats   = invention.get("materials", [])
        inv_prods  = invention.get("products", [])

        product_row = next(
            (p for p in inv_prods if int(p.get("typeID", -1)) == int(t2_bp_id)),
            inv_prods[0] if inv_prods else None,
        )

        base_success_rate = float((product_row or {}).get("probability", 0.34))
        base_runs         = int((product_row or {}).get("quantity", 1))

        manu_eiv      = _eiv(manu_materials)
        manu_industry = _activity_cost_per_run(manu_eiv, "manufacturing")

        best = None
        for decryptor in decryptors:
            decryptor_id    = int(decryptor["id"])
            decryptor_price = jita_buy.get(decryptor_id, 0.0)

            req_inv_runs, decryptor_me, _ = invention_T2_runs(
                decryptor_id=decryptor_id,
                base_success_rate=base_success_rate,
                base_runs=base_runs,
                base_me=2,
                base_te=4,
            )

            decryptor_material_factor = material_cost_factor * (1 - me_cfg) * (1 - (decryptor_me / 100.0))
            manu_mat_cost = sum(
                float(m.get("quantity", 0)) * jita_buy.get(int(m.get("typeID", -1)), 0.0)
                for m in manu_materials
            ) * decryptor_material_factor

            inv_mat_cost_per_attempt = sum(
                float(m.get("quantity", 0)) * jita_buy.get(int(m.get("typeID", -1)), 0.0)
                for m in inv_mats
            )
            invention_jcb = 0.02 * manu_eiv
            inv_industry_total = req_inv_runs * _activity_cost_per_run(invention_jcb, "invention")
            inv_total_per_run  = req_inv_runs * (inv_mat_cost_per_attempt + decryptor_price) + inv_industry_total

            total_cost = manu_mat_cost + manu_industry + inv_total_per_run
            candidate  = {
                "blueprint_id": int(t2_bp_id),
                "t1_blueprint_id": int(get_T1_from_T2(t2_bp_id) or t1_bp_id),
                "decryptor_id": decryptor_id,
                "decryptor_zh": decryptor.get("zh", ""),
                "decryptor_en": decryptor.get("en", ""),
                "cost_per_run": total_cost,
                "manufacturing_material_cost": manu_mat_cost,
                "manufacturing_industry_cost": manu_industry,
                "invention_overhead_per_run": inv_total_per_run,
                "required_invention_runs_per_manu_run": req_inv_runs,
                "decryptor_material_factor": decryptor_material_factor,
            }
            if best is None or candidate["cost_per_run"] < best["cost_per_run"]:
                best = candidate

        if best:
            result[str(t2_bp_id)] = best

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"written: {output_path} ({len(result)} blueprints)")


if __name__ == "__main__":
    _build_costs()
