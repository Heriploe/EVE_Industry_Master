"""
restore_ore.py  ─  矿石最优组合 ILP 规划器
==========================================
通过整数线性规划选择最优矿石组合以满足矿物采购需求。

用法:
    python restore_ore.py
    python restore_ore.py --config /path/config.json
"""
import csv, json, sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_APP_ROOT.parent.parent))
from utilities.data.app_config import load_app_config, load_meta, resolve
from utilities.io.csv_reader import read_provider, read_purchase_list


# ── 配置 ──────────────────────────────────────────────────────────────────────
def load_settings() -> tuple[dict, dict, Path]:
    cfg, eve_root = load_app_config()
    meta = load_meta(eve_root)
    return cfg, meta, eve_root


# ── 数据加载 ──────────────────────────────────────────────────────────────────
# ── ILP 求解 ──────────────────────────────────────────────────────────────────
def solve(minerals_needed: dict, providers: dict,
          reprocessing: dict, eff: float,
          budget: float, batch_size: int) -> dict:
    """返回 {ore_name: batch_count}"""
    try:
        from pulp import (LpInteger, LpMaximize, LpProblem,
                          LpVariable, lpSum, PULP_CBC_CMD)
    except ImportError:
        print("[restore_ore] 需要安装 pulp: pip install pulp"); return {}

    ore_names = [o for o in providers if o in reprocessing]
    if not ore_names or not minerals_needed:
        return {}

    prob = LpProblem("ore_mix", LpMaximize)
    batches = {
        ore: LpVariable(f"b_{i}", lowBound=0,
                        upBound=providers[ore]["max_qty"] // max(batch_size, 1),
                        cat=LpInteger)
        for i, ore in enumerate(ore_names)
    }

    total_needed = sum(minerals_needed.values()) or 1
    weights = {m: q / total_needed for m, q in minerals_needed.items()}

    prob += lpSum(
        weights.get(m, 0) * qty * eff * bvar
        for ore, bvar in batches.items()
        for m, qty in reprocessing.get(ore, {}).items()
    )

    for mineral, need in minerals_needed.items():
        prob += lpSum(
            reprocessing[ore].get(mineral, 0) * eff * bvar
            for ore, bvar in batches.items()
        ) <= need

    prob += lpSum(
        providers[ore]["price"] * batch_size * bvar
        for ore, bvar in batches.items()
    ) <= budget

    prob.solve(PULP_CBC_CMD(msg=0))
    return {ore: int(bvar.varValue or 0)
            for ore, bvar in batches.items() if (bvar.varValue or 0) > 0}


# ── 输出 ──────────────────────────────────────────────────────────────────────
def write_result(result: dict, providers: dict,
                 batch_size: int, output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    rows, total = [], 0.0
    for ore, n_batches in sorted(result.items()):
        qty  = n_batches * batch_size
        cost = qty * providers[ore]["price"]
        total += cost
        rows.append([ore, qty, f"{providers[ore]['price']:.2f}", f"{cost:,.2f}"])
    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["矿石", "数量", "单价(ISK)", "总价(ISK)"])
        w.writerows(rows)
        w.writerow(["合计", "", "", f"{total:,.2f}"])
    print(f"[restore_ore] → {output_csv}  总成本: {total:,.0f} ISK")


# ── 主入口 ────────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="矿石最优组合 ILP")
    parser.add_argument("--config",   default=None)
    parser.add_argument("--provider", default=None, help="供应商 CSV")
    parser.add_argument("--purchase", default=None, help="采购清单 CSV")
    parser.add_argument("--output",   default=None, help="输出 CSV")
    args = parser.parse_args()

    cfg, meta, eve_root = load_settings()
    ore_cfg = cfg.get("restore_ore", {})
    inputs  = meta.get("inputs", {})

    provider_csv = Path(args.provider) if args.provider else         resolve(eve_root, inputs.get("provider",      "inputs/provider.csv"))
    purchase_csv = Path(args.purchase) if args.purchase else         resolve(eve_root, inputs.get("purchase_list", "inputs/purchase_list.csv"))
    output_csv   = Path(args.output)   if args.output   else         resolve(eve_root, cfg.get("output_dir", "outputs/restore_ore")) / "purchase_list_ore.csv"
    types_json   = resolve(eve_root, cfg["data"]["types"])

    eff        = float(ore_cfg.get("eff",        0.9063))
    budget     = float(ore_cfg.get("budget",     350_000_000))
    batch_size = int(ore_cfg.get("batch_size",   100))

    print(f"[restore_ore] 预算={budget:,.0f} ISK  效率={eff:.2%}  批次={batch_size}")

    providers       = load_provider(provider_csv)
    minerals_needed = load_purchase_list(purchase_csv)

    if not providers:
        print(f"[错误] 供应商文件为空: {provider_csv}"); return
    if not minerals_needed:
        print(f"[错误] 采购清单为空: {purchase_csv}"); return

    # reprocessing 数据（从 types.json 的 reprocessing 字段读取）
    types_data   = json.loads(types_json.read_text(encoding="utf-8"))
    reprocessing = {
        (t.get("zh") or t.get("en", "")): t["reprocessing"]
        for t in types_data
        if t.get("reprocessing") and (t.get("zh") or t.get("en"))
    }

    result = solve(minerals_needed, providers, reprocessing, eff, budget, batch_size)
    if not result:
        print("[restore_ore] 无可行解"); return

    write_result(result, providers, batch_size, output_csv)


if __name__ == "__main__":
    main()
