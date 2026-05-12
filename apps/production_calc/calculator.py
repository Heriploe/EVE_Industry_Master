"""
calculator.py  ─  双目标整数线性规划生产规划器
"""
import configparser, json, sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_APP_ROOT.parent.parent))
from utilities.data.app_config import load_app_config, load_meta, resolve

from pulp import (
    LpInteger,
    LpMaximize,
    LpProblem,
    LpVariable,
    lpSum,
    PULP_CBC_CMD,
)

from utilities.data.name_mapping import load_types_map, name_to_id
from utilities.industry.cost import get_T1_from_T2
from utilities.blueprint.blueprint_utils import (
    resolve_path,
    get_activity,
    load_blueprints_for_preset,
    load_ids_from_preset,
    load_blueprint_type_ids_from_preset,
    load_product_ids_from_blueprint_preset,
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
_app_cfg, _eve_root = load_app_config()
_meta = load_meta(_eve_root)
_calc = _app_cfg.get("calculator", {})


def _cfg(key, fallback, cast=str):
    v = _calc.get(key)
    return cast(v) if v is not None else fallback


def _rpath(key, fallback):
    """从 config.json resources/data 字段查路径，找不到用 fallback（相对 eve_root）"""
    v = ((_app_cfg.get("resources") or {}).get(key)
         or (_app_cfg.get("data") or {}).get(key))
    return _eve_root / v if v else _eve_root / fallback


def _rpath_paths(key, fallback):
    return _rpath(key, fallback)


# 数值参数
BUDGET            = _cfg("budget",                    200_000_000, int)
ME                = _cfg("me",                        0.125,       float)
MAX_PROD_FACTOR   = _cfg("max_prod_factor",           1.0,         float)
W_INVENTORY       = _cfg("w_inventory",               1.0,         float)
W_VALUE           = _cfg("w_value",                   1.0,         float)
ALPHA             = _cfg("alpha",                     1.0,         float)
FARE_JITA         = _cfg("fare_jita",                 500.0,       float)
ENABLE_FREIGHT    = bool(_calc.get("enable_freight",   True))
PURCHASE_INTEGER  = bool(_calc.get("purchase_integer", False))
SOLVER_TIME_LIMIT = _cfg("solver_time_limit_seconds", 180,         int)
SOLVER_GAP_REL    = _cfg("solver_gap_rel",            0.005,       float)

# 产物利润因子（兼容旧拼写 moudle_profit_factor）
SHIP_PROFIT_FACTOR   = _cfg("ship_profit_factor",   1.0, float)
MODULE_PROFIT_FACTOR = _cfg("module_profit_factor",
                       _cfg("moudle_profit_factor", 1.0, float), float)
RIG_PROFIT_FACTOR    = _cfg("rig_profit_factor",    1.0, float)
MATERIAL_COST_FACTOR = _cfg("material_cost_factor", 1.0, float)

# preset 名称（兼容旧拼写 moudles_preset）
BLUEPRINTS_PRESET = _cfg("blueprints_preset", "items_to_sell")
SHIPS_PRESET      = _cfg("ships_preset",      "ships_all")
MODULES_PRESET    = _cfg("modules_preset", _cfg("moudles_preset", "modules_all"))
RIGS_PRESET       = _cfg("rigs_preset",       "Rigs_all")
MATERIALS_PRESET  = _cfg("materials_preset",  "basic")
REACTIONS_PRESET  = _cfg("reactions_preset",  "reactions_all")
COMPONENTS_PRESET = _cfg("components_preset", "components_all")

# 文件路径
output_dir              = _rpath("output_dir",         "outputs/production_calc")
INVENTORY_JSON          = _rpath("inventory_json",     "resources/corp/materials.json")
PRICE_JSON              = _rpath("price_all",   "resources/market/price_all.json")
PRESET_SETTING_CONFIG   = _rpath("preset_setting_config", "apps/production_calc/preset_setting.config")
TYPES_JSON              = _rpath_paths("types_json",   "data/types.json")

# 修复：types_volume_json 独立配置，fallback 指向同一 types.json
# （旧代码 fallback 到 str(TYPES_JSON) 本身，虽能工作但语义不清）
TYPES_VOLUME_JSON       = _rpath("types_volume_json",  str(TYPES_JSON))

BLUEPRINTS_ALIAS_JSON   = _rpath_paths("blueprints_alias_json",  "data/Blueprints/alias.json")
BLUEPRINTS_PRESET_JSON  = _rpath_paths("blueprints_preset_json", "data/Blueprints/preset.json")
MATERIALS_ALIAS_JSON    = _rpath_paths("materials_alias_json",   "data/Materials/alias.json")
MATERIALS_PRESET_JSON   = _rpath_paths("materials_preset_json",  "data/Materials/preset.json")
T2_COSTS_JSON           = _rpath("t2_costs_json", "data/T2_blueprint_costs.json")
EXCLUDED_ITEM_CSV       = _rpath("excluded_item_csv", "excluded_item.csv")

output_dir.mkdir(parents=True, exist_ok=True)
PURCHASE_CSV             = output_dir / "purchase_list.csv"
EXECUTION_CSV            = output_dir / "execution_list.csv"
EXECUTION_FINAL_CSV      = output_dir / "execution_list_final_product.csv"
EXECUTION_REACTION_CSV   = output_dir / "execution_list_reaction.csv"
EXECUTION_COMPONENT_CSV  = output_dir / "execution_list_component.csv"
FINAL_PRODUCTS_CSV       = output_dir / "final_products.csv"
INITIAL_INVENTORY_JSON   = output_dir / "initial_inventory.json"
FINAL_INVENTORY_JSON     = output_dir / "final_inventory.json"

# ==========================================================================
# 加载数据
# ==========================================================================
print("正在加载数据...")

types_map = load_types_map(TYPES_JSON)
name_id_map = name_to_id(types_map)

with INVENTORY_JSON.open("r", encoding="utf-8") as f:
    inventory = parse_inventory(json.load(f))

_, selected_blueprints, blueprints = load_blueprints_for_preset(
    BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, BLUEPRINTS_PRESET, _eve_root
)


def _load_excluded_item_ids(path):
    result = set()
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            first_col = line.split("\t", 1)[0].strip()
            if not first_col:
                continue
            if first_col.isdigit():
                result.add(int(first_col))
                continue
            if first_col in name_id_map:
                result.add(int(name_id_map[first_col]))
    return result


def _blueprint_contains_excluded_item(bp, excluded_item_ids):
    act, _ = get_activity(bp)
    if not act:
        return False
    for section in ("materials", "products"):
        for item in act.get(section, []):
            tid = item.get("typeID")
            if tid is not None and int(tid) in excluded_item_ids:
                return True
    return False


excluded_item_ids = _load_excluded_item_ids(EXCLUDED_ITEM_CSV)
if excluded_item_ids:
    selected_blueprints = [
        bp for bp in selected_blueprints
        if not _blueprint_contains_excluded_item(bp, excluded_item_ids)
    ]
    blueprints = [
        bp for bp in blueprints
        if not _blueprint_contains_excluded_item(bp, excluded_item_ids)
    ]
    print("已排除物品数:      {}，移除相关蓝图后剩余: {}".format(len(excluded_item_ids), len(blueprints)))

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

with TYPES_VOLUME_JSON.open("r", encoding="utf-8") as f:
    types_volume_list = json.load(f)

ship_ids         = load_product_ids_from_blueprint_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, SHIPS_PRESET,   _eve_root)
module_ids       = load_product_ids_from_blueprint_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, MODULES_PRESET, _eve_root)
rig_ids          = load_product_ids_from_blueprint_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, RIGS_PRESET,    _eve_root)
reaction_bp_ids  = load_blueprint_type_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, REACTIONS_PRESET,  _eve_root)
component_bp_ids = load_blueprint_type_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, COMPONENTS_PRESET, _eve_root)
basic_mat_ids    = load_ids_from_preset(MATERIALS_ALIAS_JSON,  MATERIALS_PRESET_JSON,  MATERIALS_PRESET, _eve_root)

t2_costs_map: dict = {}
if T2_COSTS_JSON.exists():
    with T2_COSTS_JSON.open("r", encoding="utf-8") as f:
        raw_t2_costs = json.load(f)
    t2_costs_map = {int(k): v for k, v in raw_t2_costs.items()}


def _load_preset_price_settings(config_path):
    cfg = configparser.ConfigParser()
    cfg.read(str(config_path), encoding="utf-8")

    def _section(name):
        if not cfg.has_section(name):
            return {"region": "jita", "price_field": "buy", "volume_region": "jita"}
        return {
            "region":        cfg.get(name, "region",        fallback="jita"),
            "price_field":   cfg.get(name, "price_field",   fallback="buy"),
            "volume_region": cfg.get(name, "volume_region", fallback="jita"),
        }

    return {
        "blueprints_preset": _section("blueprints_preset"),
        "ships_preset":      _section("ships_preset"),
        "modules_preset":    _section("modules_preset"),
        "rigs_preset":       _section("rigs_preset"),
        "materials_preset":  _section("materials_preset"),
        "reactions_preset":  _section("reactions_preset"),
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
reaction_product_ids  = _build_product_ids_for_blueprint_ids(blueprints, reaction_bp_ids)
component_product_ids = _build_product_ids_for_blueprint_ids(blueprints, component_bp_ids)


def _get_item_price_rule(tid):
    tid = int(tid)
    if tid in ship_ids:             return preset_price_settings["ships_preset"]
    if tid in module_ids:           return preset_price_settings["modules_preset"]
    if tid in rig_ids:              return preset_price_settings["rigs_preset"]
    if tid in basic_mat_ids:        return preset_price_settings["materials_preset"]
    if tid in reaction_product_ids: return preset_price_settings["reactions_preset"]
    if tid in component_product_ids:return preset_price_settings["components_preset"]
    if tid in final_product_ids:    return preset_price_settings["blueprints_preset"]
    return {"region": "jita", "price_field": "buy", "volume_region": "jita"}


def get_item_price(tid):
    rule = _get_item_price_rule(tid)
    return get_price(prices, tid, region_key=rule["region"], field=rule["price_field"], fallback_region="jita")


def get_item_volume(tid):
    rule = _get_item_price_rule(tid)
    return get_volume(prices, tid, region_key=rule["volume_region"], fallback_region="jita")


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
    """
    根据市场成交量限制蓝图最大运行次数：
      max_runs = floor(日均成交量 * MAX_PROD_FACTOR / 每次产量)
    确保规划结果不超过市场实际消化能力。
    """
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

net_final_products = {
    tid: int(flow[tid]["final"])
    for tid in final_product_ids
    if tid in flow and flow[tid]["final"] > 0
}

final_inventory = {tid: int(f["final"]) for tid, f in flow.items() if f["final"] > 0}

# 库存利用率
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
    used = max(0.0, consumed - produced - purchased)
    used = min(used, inv_qty)
    used_inventory_value += used * get_item_price(tid)

utilization_rate = (used_inventory_value / total_inventory_value) if total_inventory_value > 0 else 0.0

# 汇总统计
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

# 修复：write_purchase_csv 现在接收 build_prices() 格式的 prices，而非旧 jita_price_view
total_purchase_cost = write_purchase_csv(PURCHASE_CSV, purchase_vals, prices, types_map)

# 修复：write_execution_csv 不再需要 bp_score 参数
write_execution_csv(EXECUTION_CSV, blueprints, x_vals, types_map)
write_execution_csv_filtered(EXECUTION_FINAL_CSV, blueprints, x_vals, types_map,
                             {int(bp.get("blueprintTypeID")) for bp in selected_blueprints if bp.get("blueprintTypeID") is not None})
write_execution_csv_filtered(EXECUTION_REACTION_CSV,  blueprints, x_vals, types_map, reaction_bp_ids)
write_execution_csv_filtered(EXECUTION_COMPONENT_CSV, blueprints, x_vals, types_map, component_bp_ids)
write_final_products_csv(FINAL_PRODUCTS_CSV, net_final_products, prices, types_map)

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