"""
calculator.py
=============
双目标整数规划计算器：

  目标 = W_INVENTORY * 库存利用得分
       + W_VALUE     * preset 最终产物价值得分

- 库存利用得分：仅 preset 中直接考察的产物按市场权重（价格 × 市场量）计分，
  反映"把仓库里的材料最大化变现"的意图。
- 最终产物价值得分：preset 最终产物的利润（扣材料成本 + 运费），
  反映"尽量产出高价值物品"的意图。
- 两个权重均可在 config.ini [calculator] 中用 w_inventory / w_value 调整。
- 可用 budget 购买所缺材料（设为 0 则禁止采购）。

兼容 Python 3.8+。
"""

import configparser
import json
import sys
from pathlib import Path

from pulp import (
    LpInteger,
    LpMaximize,
    LpProblem,
    LpVariable,
    lpSum,
    PULP_CBC_CMD,
)

# --------------------------------------------------------------------------
# 仓库根目录 & sys.path
# --------------------------------------------------------------------------
REPO_ROOT = next(
    (p for p in [Path(__file__).resolve().parent] + list(Path(__file__).resolve().parent.parents)
     if (p / "config.ini").exists()),
    Path(__file__).resolve().parent,
)
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Utilities.name_mapping import load_types_map
from Utilities.industry_cost import get_T1_from_T2
from Utilities.blueprint_utils import (
    resolve_path,
    get_activity,
    load_blueprints_for_preset,
    load_ids_from_preset,
    load_blueprint_type_ids_from_preset,
    parse_inventory,
    build_prices,
    get_price,
    get_volume,
    build_item_volumes,
    get_freight_cost,
    get_product_profit_factor,
    compute_flow,
    write_purchase_csv,
    write_execution_csv,
    write_execution_csv_filtered,
    write_final_products_csv,
    write_inventory_json,
)

# ==========================================================================
# 配置读取
# ==========================================================================
config = configparser.ConfigParser()
config.read(str(REPO_ROOT / "config.ini"), encoding="utf-8")
SEC = "calculator"


def _cfg(key, fallback, cast=str):
    if config.has_option(SEC, key):
        return cast(config.get(SEC, key))
    return fallback


def _rpath(key, fallback):
    return resolve_path(config, SEC, key, fallback, REPO_ROOT)


def _rpath_paths(key, fallback):
    return resolve_path(config, "paths", key, fallback, REPO_ROOT)


# 数值参数
BUDGET            = _cfg("budget",                    200_000_000, int)
ME                = _cfg("me",                        0.125,       float)
MAX_PROD_FACTOR   = _cfg("max_prod_factor",           1.0,         float)
W_INVENTORY       = _cfg("w_inventory",               1.0,         float)
W_VALUE           = _cfg("w_value",                   1.0,         float)
ALPHA             = _cfg("alpha",                     1.0,         float)
FARE_JITA         = _cfg("fare_jita",                 500.0,       float)
ENABLE_FREIGHT    = config.getboolean(SEC, "enable_freight",   fallback=True)
PURCHASE_INTEGER  = config.getboolean(SEC, "purchase_integer", fallback=False)
SOLVER_TIME_LIMIT = _cfg("solver_time_limit_seconds", 180,         int)
SOLVER_GAP_REL    = _cfg("solver_gap_rel",            0.005,       float)

# 产物利润因子
SHIP_PROFIT_FACTOR   = _cfg("ship_profit_factor",   1.0, float)
MODULE_PROFIT_FACTOR = _cfg("module_profit_factor",
                       _cfg("moudle_profit_factor", 1.0, float), float)
RIG_PROFIT_FACTOR    = _cfg("rig_profit_factor",    1.0, float)
MATERIAL_COST_FACTOR = _cfg("material_cost_factor", 1.0, float)

# preset 名称
BLUEPRINTS_PRESET = _cfg("blueprints_preset", "items_to_sell")
SHIPS_PRESET      = _cfg("ships_preset",      "ships_all")
MODULES_PRESET    = _cfg("modules_preset", _cfg("moudles_preset", "modules_all"))
RIGS_PRESET       = _cfg("rigs_preset",       "Rigs_all")
MATERIALS_PRESET  = _cfg("materials_preset",  "basic")
REACTIONS_PRESET  = _cfg("reactions_preset",  "reactions_all")
COMPONENTS_PRESET = _cfg("components_preset", "components_all")

# 文件路径
output_dir             = _rpath("output_dir",         "Cache/Output")
INVENTORY_JSON         = _rpath("inventory_json",     "Cache/Asset/Corp/final_non_blueprints.json")
PRICE_JSON             = _rpath("jita_prices_json",   "Cache/Market/price_materials_all.json")
PRESET_SETTING_CONFIG  = _rpath("preset_setting_config", "preset_setting.config")
TYPES_JSON             = _rpath_paths("types_json",   "Data/types.json")
TYPES_VOLUME_JSON      = _rpath("types_volume_json",  str(TYPES_JSON))
BLUEPRINTS_ALIAS_JSON  = _rpath_paths("blueprints_alias_json",  "Data/Blueprints/alias.json")
BLUEPRINTS_PRESET_JSON = _rpath_paths("blueprints_preset_json", "Data/Blueprints/preset.json")
MATERIALS_ALIAS_JSON   = _rpath_paths("materials_alias_json",   "Data/Materials/alias.json")
MATERIALS_PRESET_JSON  = _rpath_paths("materials_preset_json",  "Data/Materials/preset.json")
T2_COSTS_JSON          = _rpath("t2_costs_json", "Data/T2_blueprint_costs.json")

output_dir.mkdir(parents=True, exist_ok=True)
PURCHASE_CSV           = output_dir / "purchase_list.csv"
EXECUTION_CSV          = output_dir / "execution_list.csv"
EXECUTION_FINAL_CSV    = output_dir / "execution_list_final_product.csv"
EXECUTION_REACTION_CSV = output_dir / "execution_list_reaction.csv"
EXECUTION_COMPONENT_CSV = output_dir / "execution_list_component.csv"
FINAL_PRODUCTS_CSV     = output_dir / "final_products.csv"
INITIAL_INVENTORY_JSON = output_dir / "initial_inventory.json"
FINAL_INVENTORY_JSON   = output_dir / "final_inventory.json"

# ==========================================================================
# 加载数据
# ==========================================================================
print("正在加载数据...")

with INVENTORY_JSON.open("r", encoding="utf-8") as f:
    inventory = parse_inventory(json.load(f))

_, selected_blueprints, blueprints = load_blueprints_for_preset(
    BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, BLUEPRINTS_PRESET, REPO_ROOT
)

# preset 直接考察的产物 id 集合
final_product_ids = set()
for bp in selected_blueprints:
    act, _ = get_activity(bp)
    if not act:
        continue
    for p in act.get("products", []):
        if p.get("typeID") is not None:
            final_product_ids.add(int(p["typeID"]))

with PRICE_JSON.open("r", encoding="utf-8") as f:
    prices = build_prices(json.load(f))

types_map = load_types_map(TYPES_JSON)

with TYPES_VOLUME_JSON.open("r", encoding="utf-8") as f:
    types_volume_list = json.load(f)

ship_ids      = load_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, SHIPS_PRESET,   REPO_ROOT)
module_ids    = load_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, MODULES_PRESET, REPO_ROOT)
rig_ids       = load_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, RIGS_PRESET,    REPO_ROOT)
reaction_bp_ids = load_blueprint_type_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, REACTIONS_PRESET, REPO_ROOT)
component_bp_ids = load_blueprint_type_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, COMPONENTS_PRESET, REPO_ROOT)
basic_mat_ids = load_ids_from_preset(MATERIALS_ALIAS_JSON,  MATERIALS_PRESET_JSON,  MATERIALS_PRESET, REPO_ROOT)
t2_costs_map = {}
if T2_COSTS_JSON.exists():
    with T2_COSTS_JSON.open("r", encoding="utf-8") as f:
        raw_t2_costs = json.load(f)
    t2_costs_map = {int(k): v for k, v in raw_t2_costs.items()}

def _load_preset_price_settings(config_path):
    cfg = configparser.ConfigParser()
    cfg.read(str(config_path), encoding="utf-8")

    def _section(name):
        if not cfg.has_section(name):
            return {"region": "jita", "price_field": "buy", "volume_field": "volume"}
        region = cfg.get(name, "region", fallback="jita")
        price_field = cfg.get(name, "price_field", fallback="buy")
        volume_field = cfg.get(name, "volume_field", fallback="volume")
        return {"region": region, "price_field": price_field, "volume_field": volume_field}

    return {
        "blueprints_preset": _section("blueprints_preset"),
        "ships_preset": _section("ships_preset"),
        "modules_preset": _section("modules_preset"),
        "rigs_preset": _section("rigs_preset"),
        "materials_preset": _section("materials_preset"),
        "reactions_preset": _section("reactions_preset"),
        "components_preset": _section("components_preset"),
    }


def _build_product_ids_for_blueprint_ids(bp_list, include_bp_ids):
    include_ids = {int(x) for x in include_bp_ids}
    result = set()
    for bp in bp_list:
        bp_id = bp.get("blueprintTypeID")
        if bp_id is None or int(bp_id) not in include_ids:
            continue
        act, _ = get_activity(bp)
        if not act:
            continue
        for p in act.get("products", []):
            if p.get("typeID") is not None:
                result.add(int(p["typeID"]))
    return result


preset_price_settings = _load_preset_price_settings(PRESET_SETTING_CONFIG)
reaction_product_ids = _build_product_ids_for_blueprint_ids(blueprints, reaction_bp_ids)
component_product_ids = _build_product_ids_for_blueprint_ids(blueprints, component_bp_ids)


def _get_item_price_rule(tid):
    tid = int(tid)
    if tid in ship_ids:
        return preset_price_settings["ships_preset"]
    if tid in module_ids:
        return preset_price_settings["modules_preset"]
    if tid in rig_ids:
        return preset_price_settings["rigs_preset"]
    if tid in basic_mat_ids:
        return preset_price_settings["materials_preset"]
    if tid in reaction_product_ids:
        return preset_price_settings["reactions_preset"]
    if tid in component_product_ids:
        return preset_price_settings["components_preset"]
    if tid in final_product_ids:
        return preset_price_settings["blueprints_preset"]
    return {"region": "jita", "price_field": "buy", "volume_field": "volume"}


def get_item_price(tid):
    rule = _get_item_price_rule(tid)
    return get_price(prices, tid, region_key=rule["region"], field=rule["price_field"], fallback_region="jita")


def get_item_volume(tid):
    rule = _get_item_price_rule(tid)
    return get_price(prices, tid, region_key=rule["region"], field=rule["volume_field"], fallback_region="jita")


jita_price_view = {
    tid: {
        "buy": get_price(prices, tid, region_key="jita", field="buy", fallback_region="jita"),
        "volume": get_volume(prices, tid, region_key="jita", fallback_region="jita"),
    }
    for tid in prices
}


item_volumes = build_item_volumes(types_volume_list, ship_ids)

print("蓝图总数（含依赖）: {}".format(len(blueprints)))
print("考察最终产物数:     {}".format(len(final_product_ids)))

# ==========================================================================
# 预计算
# ==========================================================================
all_items      = set()
material_items = set()

for bp in blueprints:
    act, _ = get_activity(bp)
    if not act:
        continue
    for m in act.get("materials", []):
        all_items.add(int(m["typeID"]))
        material_items.add(int(m["typeID"]))
    for p in act.get("products", []):
        all_items.add(int(p["typeID"]))

print("总物品数: {}".format(len(all_items)))

# 稀疏系数矩阵
mat_coef  = {tid: {} for tid in all_items}
prod_coef = {tid: {} for tid in all_items}
for i, bp in enumerate(blueprints):
    act, _ = get_activity(bp)
    if not act:
        continue
    for m in act.get("materials", []):
        mat_coef[int(m["typeID"])][i] = float(m.get("quantity", 0))
    for p in act.get("products", []):
        prod_coef[int(p["typeID"])][i] = float(p.get("quantity", 0))


def _max_runs(bp):
    act, _ = get_activity(bp)
    if not act or not act.get("products"):
        return 0
    p = act["products"][0]
    qty_per_run = p.get("quantity", 1) or 1
    vol = get_item_volume(p["typeID"])
    return max(int(vol * MAX_PROD_FACTOR / qty_per_run), 0)


bp_max_runs = [_max_runs(bp) for bp in blueprints]

# 市场权重总和（归一化用）
total_market_weight = sum(
    get_item_price(tid) * get_item_volume(tid)
    for tid in final_product_ids
)


def _bp_scores(i, bp):
    """
    返回 (inv_score, val_score)。
    inv_score：库存利用得分（按市场权重的 preset 产物价值，含流动性加成）
    val_score ：利润得分（preset 产物收入 - 材料成本×ME - 运费）
    """
    act, act_type = get_activity(bp)
    if not act:
        return 0.0, 0.0

    products = act.get("products", [])
    final_products = [p for p in products if int(p.get("typeID", -1)) in final_product_ids]

    # 库存利用得分
    final_value = sum(
        p.get("quantity", 0) * get_item_price(p["typeID"])
        for p in final_products
    )
    final_weight = sum(
        p.get("quantity", 0)
        * get_item_price(p["typeID"])
        * get_item_volume(p["typeID"])
        for p in final_products
    )
    norm_w = final_weight / total_market_weight if total_market_weight > 0 else 0
    inv_score = final_value * (1 + ALPHA * norm_w)

    bp_id = int(bp.get("blueprintTypeID", -1))
    is_t2 = bp_id in t2_costs_map and get_T1_from_T2(bp_id) is not None

    # 价值得分（利润）
    if is_t2:
        mat_cost = float(t2_costs_map[bp_id].get("cost_per_run", 0.0))
    else:
        mat_cost = sum(
            m.get("quantity", 0)
            * get_item_price(m["typeID"])
            * (MATERIAL_COST_FACTOR if int(m["typeID"]) in basic_mat_ids else 1.0)
            for m in act.get("materials", [])
        )
    revenue = sum(
        p.get("quantity", 0)
        * get_item_price(p["typeID"])
        * get_product_profit_factor(
            p["typeID"], ship_ids, module_ids, rig_ids,
            SHIP_PROFIT_FACTOR, MODULE_PROFIT_FACTOR, RIG_PROFIT_FACTOR,
        )
        for p in final_products
    )
    freight = sum(
        get_freight_cost(item_volumes, FARE_JITA, ENABLE_FREIGHT, p["typeID"], p.get("quantity", 0))
        for p in final_products
    )
    me_factor = 1.0 if is_t2 else ((1 - ME) if act_type == "manufacturing" else 1.0)
    val_score = revenue - mat_cost * me_factor - freight

    return inv_score, val_score


inv_scores = {}
val_scores = {}
for i, bp in enumerate(blueprints):
    inv_scores[i], val_scores[i] = _bp_scores(i, bp)

# ==========================================================================
# 建立 ILP 模型
# ==========================================================================
print("构建 ILP 模型...")
model = LpProblem("EVE_Dual_Objective", LpMaximize)

x = {
    i: LpVariable("bp_{}".format(i), lowBound=0, upBound=bp_max_runs[i], cat=LpInteger)
    for i in range(len(blueprints))
}

purchase_cat = LpInteger if PURCHASE_INTEGER else "Continuous"
purchase = {
    tid: LpVariable("buy_{}".format(tid), lowBound=0, cat=purchase_cat)
    for tid in material_items
}

# 双目标加权和
model += lpSum(
    (W_INVENTORY * inv_scores[i] + W_VALUE * val_scores[i]) * x[i]
    for i in range(len(blueprints))
)

# 物料平衡约束：库存 + 采购 + 自产 >= 消耗
for tid in all_items:
    model += (
        inventory.get(tid, 0)
        + purchase.get(tid, 0)
        + lpSum(x[i] * qty for i, qty in prod_coef[tid].items())
        >= lpSum(x[i] * qty for i, qty in mat_coef[tid].items())
    )

# 预算约束
model += lpSum(
    purchase[tid] * get_item_price(tid)
    for tid in material_items
) <= BUDGET

# ==========================================================================
# 求解
# ==========================================================================
print("开始求解（时限 {}s，相对 Gap {}）...".format(SOLVER_TIME_LIMIT, SOLVER_GAP_REL))
solver = PULP_CBC_CMD(msg=True, timeLimit=SOLVER_TIME_LIMIT, gapRel=SOLVER_GAP_REL)
model.solve(solver)
print("求解状态: {}".format(model.status))

if model.status != 1:
    print("求解失败或无可行解")
    sys.exit(1)

# ==========================================================================
# 输出结果
# ==========================================================================
x_vals        = {i: max(int(round(x[i].value() or 0)), 0) for i in range(len(blueprints))}
purchase_vals = {tid: max(int(round(var.value() or 0)), 0) for tid, var in purchase.items()}

flow = compute_flow(blueprints, x_vals, purchase_vals, prod_coef, mat_coef, inventory, all_items)

# ------------------------------------------------------------------
# 最终产物：preset 中直接考察的产物（final_product_ids）
# 输出其最终库存量（初始库存 + 生产 - 消耗 + 采购），反映实际可售数量
# ------------------------------------------------------------------
net_final_products = {
    tid: int(flow[tid]["final"])
    for tid in final_product_ids
    if tid in flow and flow[tid]["final"] > 0
}

final_inventory = {tid: int(f["final"]) for tid, f in flow.items() if f["final"] > 0}

# ------------------------------------------------------------------
# 库存利用率：实际消耗的原库存物品价值 / 初始库存总价值
# 消耗量 = min(库存量, 总需求量 - 自产量 - 采购量)，下限为 0
# ------------------------------------------------------------------
total_inventory_value = sum(
    inventory.get(tid, 0) * get_item_price(tid)
    for tid in inventory
)
used_inventory_value = 0.0
for tid in all_items:
    inv_qty = inventory.get(tid, 0)
    if inv_qty <= 0:
        continue
    f = flow.get(tid, {})
    consumed  = f.get("consumed",  0)
    produced  = f.get("produced",  0)
    purchased = f.get("purchased", 0)
    # 实际从库存中取用的量：总消耗 - 自产补充 - 采购补充，不超过库存本身
    used = max(0.0, consumed - produced - purchased)
    used = min(used, inv_qty)
    used_inventory_value += used * get_item_price(tid)

utilization_rate = (used_inventory_value / total_inventory_value) if total_inventory_value > 0 else 0.0

# ------------------------------------------------------------------
# 汇总统计
# ------------------------------------------------------------------
total_inv_score    = sum(inv_scores[i] * x_vals[i] for i in range(len(blueprints)))
total_val_score    = sum(val_scores[i] * x_vals[i] for i in range(len(blueprints)))
final_preset_value = sum(
    flow[tid]["final"] * get_item_price(tid)
    for tid in final_product_ids if tid in flow
)

print("\n" + "=" * 60)
print("库存利用得分:    {:>20,.0f}  (W_INVENTORY={})".format(total_inv_score, W_INVENTORY))
print("最终产物价值得分:{:>20,.0f}  (W_VALUE={})".format(total_val_score, W_VALUE))
print("preset 产物总价: {:>20,.0f} ISK".format(final_preset_value))
print("初始库存总价值:  {:>20,.0f} ISK".format(total_inventory_value))
print("已用库存价值:    {:>20,.0f} ISK".format(used_inventory_value))
print("库存利用率:      {:>19.1f} %".format(utilization_rate * 100))
print("最终产物种类数:  {}".format(len(net_final_products)))
print("=" * 60)

total_purchase_cost = write_purchase_csv(PURCHASE_CSV, purchase_vals, jita_price_view, types_map)
write_execution_csv(EXECUTION_CSV, blueprints, x_vals, {}, types_map)
write_execution_csv_filtered(EXECUTION_FINAL_CSV, blueprints, x_vals, types_map, {int(bp.get("blueprintTypeID")) for bp in selected_blueprints if bp.get("blueprintTypeID") is not None})
write_execution_csv_filtered(EXECUTION_REACTION_CSV, blueprints, x_vals, types_map, reaction_bp_ids)
write_execution_csv_filtered(EXECUTION_COMPONENT_CSV, blueprints, x_vals, types_map, component_bp_ids)
write_final_products_csv(FINAL_PRODUCTS_CSV, net_final_products, jita_price_view, types_map)

merged = dict(inventory)
for tid, qty in purchase_vals.items():
    if qty > 0:
        merged[tid] = merged.get(tid, 0) + qty
write_inventory_json(INITIAL_INVENTORY_JSON, merged, types_map)
write_inventory_json(FINAL_INVENTORY_JSON, final_inventory, types_map)

print("\n完成：采购清单     -> {}  (总计 {:,.0f} ISK)".format(PURCHASE_CSV, total_purchase_cost))
print("完成：执行清单     -> {}".format(EXECUTION_CSV))
print("完成：最终产物执行 -> {}".format(EXECUTION_FINAL_CSV))
print("完成：反应执行清单 -> {}".format(EXECUTION_REACTION_CSV))
print("完成：组件执行清单 -> {}".format(EXECUTION_COMPONENT_CSV))
print("完成：最终产物总量 -> {}".format(FINAL_PRODUCTS_CSV))
print("完成：初始库存     -> {}".format(INITIAL_INVENTORY_JSON))
print("完成：最终库存     -> {}".format(FINAL_INVENTORY_JSON))
