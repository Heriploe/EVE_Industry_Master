"""
expand_final_products.py
========================
将 final_products.csv 中的产物清单展开为原材料需求，结合库存扣减后输出：

  - missing_materials.csv      : 需要采购/补充的原材料（库存不足部分，向上取整）
  - expanded_blueprint_runs.csv: 子蓝图执行次数（depth >= 1 的中间产物蓝图）
  - root_blueprint_runs.csv    : 根蓝图执行次数（final_products 直接对应的蓝图）
  - remaining_materials.csv    : 展开并扣减需求后，库存中仍剩余的原材料

max_depth 语义：
  - max_depth=0 : 只展开根产物的直接材料（不递归展开子蓝图）
  - max_depth=1 : 根产物材料 + 子材料各展开一层
  - max_depth=N : 递归展开 N 层子蓝图（超出层数的材料成为叶节点）

depth 编号从 0 开始（根产物材料为 depth=0）。
_require_material 在 depth <= max_depth 时继续展开，否则成为叶节点。

兼容 Python 3.8+。
"""

import argparse
import configparser
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from Utilities.blueprint_utils import load_blueprints_from_file
from Utilities.config_utils import REPO_ROOT
from Utilities.industry_cost import invention_T2_runs
from Utilities.name_mapping import load_types_map, name_to_id

DEFAULT_FINAL_PRODUCTS = "Cache/Output/final_products.csv"
DEFAULT_INVENTORY_JSON = "Cache/Asset/Corp/final_non_blueprints.json"
DEFAULT_OUTPUT_DIR     = "Cache/Output/Expand_Final_Products"

ACTIVITY_PRIORITY = ["manufacturing", "reaction", "copying", "invention"]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else REPO_ROOT / p


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(REPO_ROOT / "config.ini", encoding="utf-8")
    return config


def parse_name_quantity(line: str) -> Tuple[str, float]:
    raw = line.strip()
    if not raw:
        return "", 0.0
    if "\t" in raw:
        name, qty = raw.rsplit("\t", 1)
    elif "," in raw:
        name, qty = raw.rsplit(",", 1)
    elif " " in raw:
        name, qty = raw.rsplit(" ", 1)
    else:
        return raw, 0.0
    try:
        q = float(qty.strip())
    except ValueError:
        q = 0.0
    return name.strip(), q


def load_simple_lines(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8-sig") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def format_quantity(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def write_tsv(path: Path, rows: List[Tuple[str, float]], ceil_qty: bool = False) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        for name, qty in rows:
            out_qty = math.ceil(float(qty)) if ceil_qty else float(qty)
            f.write(f"{name}\t{format_quantity(out_qty)}\n")


# ---------------------------------------------------------------------------
# 蓝图 product_index
# ---------------------------------------------------------------------------

def build_product_index(blueprints: dict) -> Dict[int, dict]:
    """构建 {product_type_id: {blueprint_id, activity, product_quantity}} 索引。"""
    index: Dict[int, dict] = {}
    for activity in ACTIVITY_PRIORITY:
        for bp_id, bp_data in blueprints.items():
            act = bp_data.get("activities", {}).get(activity)
            if not act:
                continue
            for product in act.get("products", []):
                tid = int(product.get("typeID", -1))
                if tid < 0 or tid in index:
                    continue
                index[tid] = {
                    "blueprint_id": int(bp_id),
                    "activity": activity,
                    "product_quantity": float(product.get("quantity", 1) or 1),
                }
    return index


# ---------------------------------------------------------------------------
# 展开器
# ---------------------------------------------------------------------------

class Expander:
    """
    从最终产物清单展开原材料需求，结合库存扣减。

    depth 语义：
      - expand_root 调用 _expand_materials(depth=0)
      - _require_material 收到 depth=0 时：若 0 <= max_depth，则展开子蓝图
      - 子蓝图的材料以 depth=1 递归，以此类推
      - max_depth=0：根产物直接材料可展开，但其材料（depth=1）不再递归
      - max_depth=N：展开 N+1 层（根产物材料算 depth=0）
    """

    def __init__(
        self,
        blueprints: dict,
        product_index: Dict[int, dict],
        inventory: Dict[int, float],
        t2_to_t1: Dict[int, int],
        decryptor_id: Optional[int],
        max_depth: int,
    ):
        self.blueprints    = blueprints
        self.product_index = product_index
        self.inventory     = dict(inventory)   # 展开过程中逐步扣减
        self.t2_to_t1      = t2_to_t1
        self.decryptor_id  = decryptor_id
        self.max_depth     = max_depth

        self.root_execution:  Counter = Counter()  # {(bp_id, activity): runs}
        self.child_execution: Counter = Counter()  # {(bp_id, activity): runs}
        self.invention_exec:  Counter = Counter()  # {t1_bp_id: invention_runs}
        self.material_demand: Counter = Counter()  # {type_id: total_demand} 叶节点

    def expand_root(self, type_id: int, qty: float) -> None:
        """
        展开一个根产物。
        - 不消耗根产物库存（final_products 是生产计划，不是库存核销）
        - 直接找蓝图，记录根蓝图执行次数，以 depth=0 展开其材料
        """
        type_id = int(type_id)
        producer = self.product_index.get(type_id)
        if producer is None:
            self.material_demand[type_id] += qty
            return

        bp_id    = int(producer["blueprint_id"])
        activity = producer["activity"]
        prod_qty = float(producer.get("product_quantity", 1) or 1)
        runs     = qty / prod_qty if prod_qty > 0 else 0.0

        self.root_execution[(bp_id, activity)] += runs
        self._handle_invention(bp_id, runs)

        # depth=0：根产物的直接材料层
        self._expand_materials(bp_id, activity, runs, depth=0,
                               stack=frozenset({(bp_id, activity)}))

    def _expand_materials(self, bp_id: int, activity: str, runs: float,
                          depth: int, stack: frozenset) -> None:
        act_data = self.blueprints.get(bp_id, {}).get("activities", {}).get(activity, {})
        for mat in act_data.get("materials", []):
            mat_id  = int(mat.get("typeID"))
            mat_qty = float(mat.get("quantity", 0) or 0) * runs
            self._require_material(mat_id, mat_qty, depth, stack)

    def _require_material(self, type_id: int, qty: float,
                          depth: int, stack: frozenset) -> None:
        """
        处理一项材料需求。
        depth <= max_depth 且有蓝图且无循环 -> 继续展开（记入 child_execution）
        否则 -> 叶节点（记入 material_demand）
        """
        producer = self.product_index.get(type_id)

        if producer is not None and depth <= self.max_depth:
            bp_id     = int(producer["blueprint_id"])
            activity  = producer["activity"]
            cycle_key = (bp_id, activity)
            if cycle_key not in stack:
                prod_qty   = float(producer.get("product_quantity", 1) or 1)
                child_runs = qty / prod_qty if prod_qty > 0 else 0.0

                self.child_execution[cycle_key] += child_runs
                self._handle_invention(bp_id, child_runs)
                self._expand_materials(
                    bp_id, activity, child_runs,
                    depth + 1, stack | {cycle_key},
                )
                return

        # 叶节点
        self.material_demand[type_id] += qty

    def _handle_invention(self, bp_id: int, runs: float) -> None:
        t1_bp_id = self.t2_to_t1.get(bp_id)
        if t1_bp_id is None:
            return

        inv_runs_per_unit, _, _ = invention_T2_runs(decryptor_id=self.decryptor_id)
        required_inv_runs = runs * float(inv_runs_per_unit)
        self.invention_exec[t1_bp_id] += required_inv_runs

        if self.decryptor_id is not None:
            self.material_demand[int(self.decryptor_id)] += required_inv_runs

        t1_inv = self.blueprints.get(t1_bp_id, {}).get("activities", {}).get("invention", {})
        for mat in t1_inv.get("materials", []):
            mat_id  = int(mat.get("typeID"))
            mat_qty = float(mat.get("quantity", 0) or 0) * required_inv_runs
            self.material_demand[mat_id] += mat_qty

    def compute_missing(self) -> Counter:
        """将原材料总需求与库存比对，返回缺料 Counter，并更新库存。"""
        missing: Counter = Counter()
        for tid, demand in self.material_demand.items():
            have = float(self.inventory.get(tid, 0))
            used = min(have, demand)
            self.inventory[tid] = have - used
            shortage = demand - used
            if shortage > 1e-9:
                missing[tid] = shortage
        return missing

    def remaining_inventory(self) -> Dict[int, float]:
        """扣减后仍有余量的库存条目（仅含本次展开涉及的材料种类）。"""
        return {tid: qty for tid, qty in self.inventory.items() if qty > 1e-9}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    config = load_config()
    output_dir_from_config = config.get("calculator", "output_dir", fallback="Cache/Output")
    default_final = str(
        (resolve_path(output_dir_from_config) / "final_products.csv").relative_to(REPO_ROOT)
    )

    default_depth          = config.getint("expand_final_products", "max_depth",     fallback=0)
    default_decryptor_name = config.get("expand_final_products",   "decryptor_name", fallback="").strip()

    parser = argparse.ArgumentParser(
        description="展开 final_products，输出缺料、蓝图执行次数、剩余库存"
    )
    parser.add_argument("--final-products",  default=default_final or DEFAULT_FINAL_PRODUCTS)
    parser.add_argument("--inventory-json",  default=DEFAULT_INVENTORY_JSON)
    parser.add_argument("--output-dir",      default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-depth",       type=int, default=default_depth,
                        help="子蓝图递归展开层数（0=只展开根产物直接材料，不递归）")
    parser.add_argument("--decryptor-name",  default=default_decryptor_name)
    args = parser.parse_args()

    if args.max_depth < 0:
        raise ValueError("max-depth 不能小于 0")

    final_products_path = resolve_path(args.final_products)
    inventory_path      = resolve_path(args.inventory_json)
    output_dir          = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not final_products_path.exists():
        raise FileNotFoundError(f"未找到 final_products.csv: {final_products_path}")
    if not inventory_path.exists():
        raise FileNotFoundError(f"未找到库存文件: {inventory_path}")

    types_json      = resolve_path(config.get("paths", "types_json",     fallback="Data/types.json"))
    blueprints_yaml = resolve_path(config.get("paths", "blueprints_yaml", fallback="Data/blueprints.yaml"))
    t2_t1_path      = resolve_path(config.get("paths", "t2_t1_json",     fallback="Data/T2_T1.json"))

    type_map = load_types_map(str(types_json))
    name2id  = name_to_id(type_map)

    decryptor_id   = None
    decryptor_name = (args.decryptor_name or "").strip()
    if decryptor_name:
        decryptor_id = name2id.get(decryptor_name)
        if decryptor_id is None:
            raise ValueError(f"未找到解码器中文名对应 ID: {decryptor_name}")

    blueprints    = load_blueprints_from_file(blueprints_yaml)
    product_index = build_product_index(blueprints)

    with open(t2_t1_path, encoding="utf-8") as f:
        t2_t1_raw = json.load(f)
    t2_to_t1 = {int(p[0]): int(p[1]) for p in t2_t1_raw if isinstance(p, list) and len(p) >= 2}

    with open(inventory_path, encoding="utf-8") as f:
        inventory_raw = json.load(f)
    inventory_initial: Dict[int, float] = defaultdict(float)
    for row in inventory_raw:
        tid = row.get("id")
        if tid is not None:
            inventory_initial[int(tid)] += float(row.get("quantity", 0) or 0)

    expander = Expander(
        blueprints=blueprints,
        product_index=product_index,
        inventory=inventory_initial,
        t2_to_t1=t2_to_t1,
        decryptor_id=decryptor_id,
        max_depth=args.max_depth,
    )

    unresolved_names: List[str] = []
    for line in load_simple_lines(final_products_path):
        name, qty = parse_name_quantity(line)
        if not name or qty <= 0:
            continue
        type_id = name2id.get(name)
        if type_id is None:
            unresolved_names.append(name)
            continue
        expander.expand_root(int(type_id), float(qty))

    missing   = expander.compute_missing()
    remaining = expander.remaining_inventory()

    def _zh(tid: int) -> str:
        return (type_map.get(tid, {}) or {}).get("zh") or str(tid)

    missing_rows = sorted(
        [(_zh(tid), qty) for tid, qty in missing.items()],
        key=lambda x: x[0],
    )
    root_rows = sorted(
        [(_zh(bp_id), runs) for (bp_id, _act), runs in expander.root_execution.items()],
        key=lambda x: x[0],
    )
    child_rows = [
        (_zh(bp_id), runs) for (bp_id, _act), runs in expander.child_execution.items()
    ]
    for t1_bp_id, inv_runs in expander.invention_exec.items():
        child_rows.append((f"{_zh(t1_bp_id)}（发明）", inv_runs))
    child_rows.sort(key=lambda x: x[0])

    remaining_rows = sorted(
        [(_zh(tid), qty) for tid, qty in remaining.items()],
        key=lambda x: x[0],
    )

    missing_csv    = output_dir / "missing_materials.csv"
    root_exec_csv  = output_dir / "root_blueprint_runs.csv"
    child_exec_csv = output_dir / "expanded_blueprint_runs.csv"
    remaining_csv  = output_dir / "remaining_materials.csv"
    summary_json   = output_dir / "summary.json"

    write_tsv(missing_csv,    missing_rows,   ceil_qty=True)
    write_tsv(root_exec_csv,  root_rows,      ceil_qty=False)
    write_tsv(child_exec_csv, child_rows,     ceil_qty=False)
    write_tsv(remaining_csv,  remaining_rows, ceil_qty=False)

    summary = {
        "final_products":           str(final_products_path),
        "inventory_json":           str(inventory_path),
        "max_depth":                args.max_depth,
        "decryptor_name":           decryptor_name,
        "decryptor_id":             decryptor_id,
        "missing_material_types":   len(missing_rows),
        "root_blueprints":          len(expander.root_execution),
        "child_blueprints":         len(expander.child_execution),
        "invention_entries":        len(expander.invention_exec),
        "remaining_material_types": len(remaining_rows),
        "unresolved_product_names": unresolved_names,
        "outputs": {
            "missing_materials_csv":       str(missing_csv),
            "root_blueprint_runs_csv":     str(root_exec_csv),
            "expanded_blueprint_runs_csv": str(child_exec_csv),
            "remaining_materials_csv":     str(remaining_csv),
        },
    }
    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
