import csv
import json
from pulp import LpProblem, LpVariable, lpSum, LpMaximize, LpInteger, PULP_CBC_CMD

BATCH_SIZE = 100
BUDGET = 350_000_000  # 总预算
EFF = 0.9063

# -------------------------------
# 1. 读取 provider.csv
# -------------------------------
provider_dict = {}
with open("provider.csv", "r", encoding="utf-8") as f:
    for row in csv.reader(f, delimiter="\t"):
        if len(row) < 2:
            continue
        provider_dict[row[0].strip()] = int(row[1])

# -------------------------------
# 2. 读取 reprocessing.json
# -------------------------------
with open("reprocessing.json", "r", encoding="utf-8") as f:
    reprocessing_data = json.load(f)

ore_to_materials = {ore["id"]: ore.get("materials", []) for ore in reprocessing_data}
ore_id_to_name = {ore["id"]: ore["zh"] for ore in reprocessing_data}
for ore_id, mats in ore_to_materials.items():
    for mat in mats:
        mat['quantity'] = round(mat['quantity'] * EFF)
print(ore_to_materials)

# -------------------------------
# 3. 读取 minerals.json
# -------------------------------
with open("minerals.json", "r", encoding="utf-8") as f:
    minerals = json.load(f)
mineral_name_to_id = {m["zh"]: m["id"] for m in minerals}
mineral_id_to_name = {m["id"]: m["zh"] for m in minerals}
mineral_id_to_price = {m["id"]: 0 for m in minerals}

# -------------------------------
# 4. 读取 jita_prices.json
# -------------------------------
with open("jita_prices.json", "r", encoding="utf-8") as f:
    jita_prices_data = json.load(f)
for item in jita_prices_data:
    mineral_id_to_price[item["id"]] = item.get("buy", 0)

# -------------------------------
# 5. 读取 purchase_list.csv
# -------------------------------
purchase_list = {}
with open("purchase_list.csv", "r", encoding="utf-8") as f:
    for row in csv.reader(f, delimiter="\t"):
        if len(row) < 2:
            continue
        name = row[0].strip()
        qty = int(row[1])
        if name in mineral_name_to_id:
            purchase_list[mineral_name_to_id[name]] = qty

# -------------------------------
# 初始化剩余预算和需求
# -------------------------------
remaining_budget = BUDGET
remaining_demand = purchase_list.copy()
used_ores_total = {}

# -------------------------------
# 按目标矿物价值排序
# -------------------------------
sorted_targets = sorted(purchase_list.items(), key=lambda x: mineral_id_to_price[x[0]], reverse=True)

# -------------------------------
# 分步求解 ILP
# -------------------------------
for mid, demand in sorted_targets:
    if remaining_demand[mid] <= 0 or remaining_budget <= 0:
        continue

    # 当前目标矿物相关原矿
    relevant_ores = []
    for pname in provider_dict:
        ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
        if ore_id is None:
            continue
        mats = ore_to_materials[ore_id]
        if any(mat["materialTypeID"] == mid for mat in mats):
            relevant_ores.append(pname)
    if not relevant_ores:
        continue

    # 构建 ILP
    prob = LpProblem(f"Step_{mid}", LpMaximize)
    x_vars = {}
    for pname in relevant_ores:
        max_batches = provider_dict[pname] // BATCH_SIZE
        x_vars[pname] = LpVariable(pname, 0, max_batches, cat=LpInteger)

    # 约束1：当前目标矿物 ≤ 剩余需求
    coeffs = []
    vars_list = []
    for pname, var in x_vars.items():
        ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
        for mat in ore_to_materials[ore_id]:
            if mat["materialTypeID"] == mid:
                coeffs.append(mat["quantity"])
                vars_list.append(var)
    prob += lpSum([c * v for c, v in zip(coeffs, vars_list)]) <= remaining_demand[mid], f"DemandConstraint_{mid}"

    # 约束2：非目标矿物预算 ≤ 剩余预算
    extra_terms = []
    for pname, var in x_vars.items():
        ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
        for mat in ore_to_materials[ore_id]:
            if mat["materialTypeID"] != mid:
                extra_terms.append(mat["quantity"] * var * mineral_id_to_price.get(mat["materialTypeID"], 0))
    if extra_terms:
        prob += lpSum(extra_terms) <= remaining_budget, "BudgetConstraint"

    # 目标函数：当前目标矿物总量
    objective_terms = []
    for pname, var in x_vars.items():
        ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
        for mat in ore_to_materials[ore_id]:
            if mat["materialTypeID"] == mid:
                objective_terms.append(mat["quantity"] * var * mineral_id_to_price[mat["materialTypeID"]])
    prob += lpSum(objective_terms), f"MaxTarget_{mid}"

    # 求解
    solver = PULP_CBC_CMD(msg=0)
    prob.solve(solver)

    # 更新结果
    for pname, var in x_vars.items():
        val = int(var.varValue)
        if val > 0:
            used_ores_total[pname] = used_ores_total.get(pname, 0) + val * BATCH_SIZE
            ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
            for mat in ore_to_materials[ore_id]:
                mid_mat = mat["materialTypeID"]
                produced_qty = mat["quantity"] * val
                if mid_mat in remaining_demand:
                    remaining_demand[mid_mat] -= produced_qty
                    if remaining_demand[mid_mat] < 0:
                        remaining_demand[mid_mat] = 0
                else:
                    remaining_budget -= produced_qty * mineral_id_to_price.get(mid_mat, 0)
                    if remaining_budget < 0:
                        remaining_budget = 0

# -------------------------------
# 输出 purchase_list_ore.csv
# -------------------------------
with open("purchase_list_ore.csv", "w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f, delimiter="\t")
    for pname, qty in used_ores_total.items():
        writer.writerow([pname, qty])

# -------------------------------
# 输出统计表
# -------------------------------
print("\n===== 分步 ILP 统计表 =====")
print(f"剩余预算：{remaining_budget:.2f}\n")

for pname, qty in used_ores_total.items():
    print(f"- {pname}：{qty} units")
    ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
    for mat in ore_to_materials[ore_id]:
        mid_mat = mat["materialTypeID"]
        name = mineral_id_to_name.get(mid_mat, str(mid_mat))
        type_str = "目标矿物" if mid_mat in purchase_list else "非目标矿物"
        print(f"    {name} ({type_str}) ：{mat['quantity'] * (qty // BATCH_SIZE)}")

print("\n目标矿物剩余需求：")
for mid, demand_left in remaining_demand.items():
    name = mineral_id_to_name.get(mid, str(mid))
    total = purchase_list[mid]
    print(f"- {name}：{total - demand_left} / {total}（已满足比例：{(total - demand_left) / total * 100:.1f}%）")
