
import configparser
from pathlib import Path
import sys

REPO_ROOT = next((p for p in [Path(__file__).resolve().parent, *Path(__file__).resolve().parent.parents] if (p / "config.ini").exists()), Path(__file__).resolve().parent)
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Utilities.name_mapping import get_name as resolve_name, load_types_map


def _resolve_shared_path(config_key, default_rel_path):
    current_dir = Path(__file__).resolve().parent
    repo_root = next((p for p in [current_dir, *current_dir.parents] if (p / "config.ini").exists()), current_dir)

    config = configparser.ConfigParser()
    config.read(repo_root / "config.ini", encoding="utf-8")

    path_value = config.get("paths", config_key, fallback=default_rel_path)
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return str(candidate)

"""
产品过滤器 - Filter Products by Value

功能：
1. 读取 final_products.csv
2. 根据 jita 价格过滤总价值低于阈值的产品
3. 通过蓝图逆推，生成过滤后的执行清单
4. 输出到 execution_list_filtered.csv

使用方法：
python3 product_filter.py
"""

import json
import csv

# ================== 配置参数 ==================
MIN_PRODUCT_VALUE = 50_000_000  # 最小产品总价值（ISK）

# ================== 文件路径 ==================
data_Dir = "Source"
result_Dir = "Results"

FINAL_PRODUCTS_CSV = f"{result_Dir}/final_products.csv"
EXECUTION_CSV = f"{result_Dir}/execution_list.csv"
EXECUTION_FILTERED_CSV = f"{result_Dir}/execution_list_filtered.csv"

BLUEPRINTS_JSON = f"{data_Dir}/blueprints_merged.json"
JITA_PRICES_JSON = f"{data_Dir}/jita_prices.json"
TYPES_JSON = _resolve_shared_path("types_json", "Data/types.json")

# ------------------ 读取数据 ------------------
print("正在读取数据...")

# 读取 Jita 价格
with open(JITA_PRICES_JSON, "r", encoding="utf-8") as f:
    jita_prices_raw = json.load(f)

jita_prices = {}
for k, v in jita_prices_raw.items():
    jita_prices[int(k)] = {
        "buy": v["jita"].get("buy") if isinstance(v["jita"].get("buy"), (int, float)) else 0,
        "volume": v["jita"].get("volume", 0) if isinstance(v.get("volume", 0), (int, float)) else 0
    }

# 读取蓝图数据
with open(BLUEPRINTS_JSON, "r", encoding="utf-8") as f:
    blueprints = json.load(f)

# 读取物品名称
types_map = load_types_map(TYPES_JSON)


def get_jita_price(tid, field="buy"):
    item = jita_prices.get(int(tid), {})
    val = item.get(field)
    if isinstance(val, (int, float)):
        return val
    return 0


def get_activity(bp):
    if "manufacturing" in bp:
        return bp["manufacturing"], "manufacturing"
    if "reaction" in bp:
        return bp["reaction"], "reaction"
    return None, None


# ------------------ 读取 final_products.csv ------------------
print(f"正在读取 {FINAL_PRODUCTS_CSV}...")

final_products = {}
with open(FINAL_PRODUCTS_CSV, "r", encoding="utf-8") as f:
    reader = csv.reader(f, delimiter="\t")
    for row in reader:
        if len(row) >= 2:
            product_name_zh = row[0]
            quantity = int(row[1])
            
            # 通过名称查找 type_id
            product_id = None
            for tid, names in types_map.items():
                if names["zh"] == product_name_zh:
                    product_id = tid
                    break
            
            if product_id:
                final_products[product_id] = quantity

print(f"共读取 {len(final_products)} 个产品")

# ------------------ 过滤产品 ------------------
print(f"\n正在过滤总价值低于 {MIN_PRODUCT_VALUE:,.0f} ISK 的产品...")

filtered_products = {}
filtered_out_products = {}

for product_id, quantity in final_products.items():
    price = get_jita_price(product_id, "buy")
    total_value = price * quantity
    
    if total_value >= MIN_PRODUCT_VALUE:
        filtered_products[product_id] = quantity
    else:
        filtered_out_products[product_id] = {
            "quantity": quantity,
            "price": price,
            "value": total_value
        }

print(f"保留产品: {len(filtered_products)} 个")
print(f"过滤掉: {len(filtered_out_products)} 个")

# 显示被过滤的产品
if filtered_out_products:
    print("\n被过滤的产品（总价值低于阈值）:")
    for pid, info in sorted(filtered_out_products.items(), key=lambda x: x[1]["value"], reverse=True):
        print(f"  {resolve_name(pid, types_map)['zh']}: {info['quantity']} × {info['price']:,.0f} = {info['value']:,.0f} ISK")

# ------------------ 通过蓝图逆推 ------------------
print(f"\n正在逆推蓝图需求...")

# 建立产物到蓝图的映射
product_to_blueprints = {}

for i, bp in enumerate(blueprints):
    activity, _ = get_activity(bp)
    if not activity:
        continue
    
    products = activity.get("products", [])
    for p in products:
        product_id = p["typeID"]
        if product_id not in product_to_blueprints:
            product_to_blueprints[product_id] = []
        product_to_blueprints[product_id].append({
            "index": i,
            "blueprint": bp,
            "quantity_per_run": p.get("quantity", 1)
        })

# 读取原始执行清单
execution_data = {}
with open(EXECUTION_CSV, "r", encoding="utf-8") as f:
    reader = csv.reader(f, delimiter="\t")
    for row in reader:
        if len(row) >= 2:
            bp_name_zh = row[0]
            runs = int(row[1])
            execution_data[bp_name_zh] = runs

# 逆推需要的蓝图
filtered_execution = {}

for product_id in filtered_products.keys():
    if product_id in product_to_blueprints:
        # 找到可以生产这个产品的蓝图
        bp_options = product_to_blueprints[product_id]
        
        for bp_info in bp_options:
            bp = bp_info["blueprint"]
            bp_id = bp.get("blueprintTypeID")
            bp_name_zh = resolve_name(bp_id, types_map)["zh"] if bp_id in types_map else f"蓝图_{bp_id}"
            
            # 如果这个蓝图在原始执行清单中
            if bp_name_zh in execution_data:
                filtered_execution[bp_name_zh] = execution_data[bp_name_zh]

print(f"过滤后蓝图数量: {len(filtered_execution)}")

# ------------------ 输出过滤后的执行清单 ------------------
print(f"\n正在写入 {EXECUTION_FILTERED_CSV}...")

with open(EXECUTION_FILTERED_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f, delimiter="\t")
    
    # 按运行次数排序
    for bp_name, runs in sorted(filtered_execution.items(), key=lambda x: x[1], reverse=True):
        writer.writerow([bp_name, runs])

print(f"完成：过滤后执行清单 → {EXECUTION_FILTERED_CSV}")

# ------------------ 统计信息 ------------------
print("\n" + "=" * 70)
print("统计信息")
print("=" * 70)

total_products = len(final_products)
kept_products = len(filtered_products)
removed_products = len(filtered_out_products)

total_blueprints = len(execution_data)
kept_blueprints = len(filtered_execution)
removed_blueprints = total_blueprints - kept_blueprints

print(f"\n产品统计:")
print(f"  总产品数: {total_products}")
print(f"  保留: {kept_products} ({kept_products/total_products*100:.1f}%)")
print(f"  过滤: {removed_products} ({removed_products/total_products*100:.1f}%)")

print(f"\n蓝图统计:")
print(f"  总蓝图数: {total_blueprints}")
print(f"  保留: {kept_blueprints} ({kept_blueprints/total_blueprints*100:.1f}%)")
print(f"  过滤: {removed_blueprints} ({removed_blueprints/total_blueprints*100:.1f}%)")

print(f"\n阈值: {MIN_PRODUCT_VALUE:,.0f} ISK")
print("=" * 70)
