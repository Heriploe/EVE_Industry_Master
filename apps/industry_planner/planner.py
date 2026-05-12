"""
EVE Industry Planner
====================
从 config.json 读取所有路径和参数，执行BOM分解、库存感知模拟调度、输出JSON结果。

使用方法:
    python planner.py                          # 使用同目录 config.json
    python planner.py --config path/to/config.json
    python planner.py --days 2 --me 8         # 命令行参数覆盖 config 值

所有路径均相对于 --root 指定的 eve/ 根目录（默认为 config.json 所在目录的上两级）。
"""

import json
import math
import random
import copy
import time
import argparse
import logging
from pathlib import Path

from sim_engine import BpcInventory, SimTask, simulate_production

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("EVEPlanner")


# ---------------------------------------------------------------------------
# 1. I/O — 共享工具从 utilities 导入
# ---------------------------------------------------------------------------
# sys.path 设置：使 utilities 包对所有 apps 可见
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utilities.io import load_json, save_json, load_yaml, load_csv_tsv

class EVEDataStore:
    """从 config 路径加载所有数据，提供查询接口。cfg 中的路径均相对于 eve_root。"""

    def __init__(self, cfg: dict, eve_root: Path):
        log.info("加载数据，根目录: %s", eve_root)
        d = cfg["data"]
        r = cfg["resources"]

        # ── 蓝图配方 ─────────────────────────────────────────
        log.info("  blueprints.yaml ...")
        self.blueprints_yaml: dict = load_yaml(eve_root / d["blueprints_yaml"])
        # blueprints_merged 已废弃，reaction 数据直接从 blueprints_yaml 读取

        # ── 类型名称 ─────────────────────────────────────────
        log.info("  types.json ...")
        raw_types: list = load_json(eve_root / d["types"])
        self.types: dict[int, dict] = {t["id"]: t for t in raw_types}

        # ── Corp 蓝图（bpo.json + bpc.json）─────────────────────
        def _load_or_empty(key: str, default: str) -> list:
            path = eve_root / r.get(key, default)
            if not path.exists():
                log.warning("  找不到 %s，使用空列表（请运行 fetch_assets.py）", path)
                return []
            return load_json(path)

        log.info("  corp/bpo.json + bpc.json ...")
        corp_bpos_raw: list = _load_or_empty("corp_bpo", "resources/corp/bpo.json")
        corp_bpcs_raw: list = _load_or_empty("corp_bpc", "resources/corp/bpc.json")
        self._corp_bps_raw = corp_bpos_raw + corp_bpcs_raw

        self.corp_bpos: dict[int, dict]       = {}
        self.corp_bpcs: dict[int, list[dict]] = {}

        for bp in corp_bpos_raw:
            bid = int(bp.get("type_id") or bp.get("id") or 0)
            if bid:
                self.corp_bpos[bid] = bp

        for bp in corp_bpcs_raw:
            bid = int(bp.get("type_id") or bp.get("id") or 0)
            if bid:
                self.corp_bpcs.setdefault(bid, []).append({
                    "id":                  bid,
                    "runs":                bp.get("runs", 1),
                    "material_efficiency": bp.get("material_efficiency", 0),
                    "time_efficiency":     bp.get("time_efficiency", 0),
                    "is_blueprint_copy":   True,
                    "location_flag":       bp.get("location_flag", ""),
                    "container_name":      bp.get("container_name", ""),
                })

        # ── Corp 材料库存 ─────────────────────────────────────
        log.info("  corp/materials.json ...")
        _mat_path = eve_root / r.get("corp_materials", "resources/corp/materials.json")
        corp_mats: list = load_json(_mat_path) if _mat_path.exists() else []
        self.corp_inventory: dict[int, int] = {
            (m.get("type_id") or m.get("id")): m.get("quantity", 0)
            for m in corp_mats
            if m.get("type_id") or m.get("id")
        }
        self.corp_inventory_raw: list = corp_mats

        # ── 最终产物目标 ─────────────────────────────────────
        log.info("  final_products.csv ...")
        prod_rows = load_csv_tsv(eve_root / cfg["targets"]["final_products"])
        name_to_id: dict[str, int] = {}
        for tid, t in self.types.items():
            if t.get("zh"):
                name_to_id[t["zh"]] = tid
            if t.get("en"):
                name_to_id[t["en"]] = tid
        self.final_products: list[dict] = []
        for row in prod_rows:
            tid = name_to_id.get(row["name"])
            self.final_products.append({"name": row["name"], "qty": row["qty"], "typeID": tid})
            if tid is None:
                log.warning("  产物未找到 typeID: %s", row["name"])

        # ── 进行中工业任务 ────────────────────────────────────
        log.info("  corp/industry_jobs.json ...")
        _jobs_path = eve_root / r.get("corp_industry_jobs", "resources/corp/industry_jobs.json")
        self.industry_jobs: list = load_json(_jobs_path) if _jobs_path.exists() else []

        # ── 配方索引 + T2映射 ─────────────────────────────────
        log.info("  building recipe index ...")
        self._build_recipe_index()

        sg = cfg.get("special_group_ids", {})
        self.DECRYPTOR_GROUPS: set[int] = set(sg.get("decryptors", [1304, 367776]))
        self.DATACORE_GROUPS:  set[int] = set(sg.get("datacores",  [333, 314, 356, 474, 1416, 1709]))

        t2_raw: list = load_json(eve_root / d["t2_map"])
        self.t2_bp_set:    set[int]       = {int(p[0]) for p in t2_raw if p}
        self.t2_to_t1_bp:  dict[int, int] = {int(p[0]): int(p[1]) for p in t2_raw if p}
        log.info("  T2蓝图: %d种", len(self.t2_bp_set))

        log.info("数据加载完毕. BPC种类=%d BPO=%d 库存种类=%d 产物=%d",
                 len(self.corp_bpcs), len(self.corp_bpos),
                 len(self.corp_inventory), len(self.final_products))

    # ── 配方索引 ──────────────────────────────────────────────
    def _build_recipe_index(self):
        """
        recipe_index: productTypeID -> {
            'manufacturing': {...} | None,
            'reaction':      {...} | None,
            'invention':     {...} | None,  # 发明：产物是T2 BPC
            'copying':       {...} | None,
        }
        每个 activity 字段: {
            blueprintTypeID, time, materials:[{typeID,quantity}],
            qty (per run), prob
        }
        """
        self.recipe_index: dict[int, dict] = {}

        def _add(product_id, act_name, bp_id, act_data, qty, prob=1.0):
            if product_id not in self.recipe_index:
                self.recipe_index[product_id] = {}
            self.recipe_index[product_id][act_name] = {
                "blueprintTypeID": int(bp_id),
                "time": act_data.get("time", 0),
                "materials": act_data.get("materials") or [],
                "qty": qty,
                "prob": prob,
                "skills": act_data.get("skills") or [],
            }

        for bp_id, bp_data in self.blueprints_yaml.items():
            acts = bp_data.get("activities") or {}
            for act_name in ("manufacturing", "invention", "copying"):
                act = acts.get(act_name)
                if not act:
                    continue
                for prod in (act.get("products") or []):
                    _add(prod["typeID"], act_name, bp_id, act,
                         prod["quantity"], prod.get("probability", 1.0))

        # reaction 来自 blueprints_yaml（与 manufacturing 同源）
        for bp_id, bp_data in self.blueprints_yaml.items():
            react = (bp_data.get("activities") or {}).get("reaction")
            if not react:
                continue
            for prod in (react.get("products") or []):
                _add(prod["typeID"], "reaction", int(bp_id), react,
                     prod["quantity"], 1.0)

    # ── 查询接口 ──────────────────────────────────────────────
    def type_name(self, type_id: int, lang="zh") -> str:
        t = self.types.get(type_id, {})
        return t.get(lang) or t.get("en") or f"ID:{type_id}"

    def get_recipe(self, type_id: int) -> dict | None:
        return self.recipe_index.get(type_id)

    def has_bpc(self, bp_type_id: int) -> bool:
        return bool(self.corp_bpcs.get(bp_type_id))

    def total_bpc_flows(self, bp_type_id: int) -> int:
        """该蓝图在库存中的总可用流程数（所有副本 runs 之和）"""
        return sum(bp.get("runs", 1) for bp in self.corp_bpcs.get(bp_type_id, []))

    def has_bpo(self, bp_type_id: int) -> bool:
        return bp_type_id in self.corp_bpos

    def has_blueprint(self, bp_type_id: int) -> bool:
        return self.has_bpc(bp_type_id) or self.has_bpo(bp_type_id)

    def is_t2_bp(self, bp_type_id: int) -> bool:
        """该蓝图ID是否为T2蓝图（需要通过发明获得BPC）"""
        return int(bp_type_id) in self.t2_bp_set

    def inventory(self, type_id: int) -> int:
        return self.corp_inventory.get(type_id, 0)

    def is_decryptor(self, type_id: int) -> bool:
        t = self.types.get(type_id, {})
        return t.get("groupID") in self.DECRYPTOR_GROUPS

    def is_datacore(self, type_id: int) -> bool:
        t = self.types.get(type_id, {})
        return t.get("groupID") in self.DATACORE_GROUPS


# ---------------------------------------------------------------------------
# 2. BOM DECOMPOSITION
# ---------------------------------------------------------------------------

def apply_me(qty: int, me_pct: int) -> int:
    """应用材料效率，最小为1"""
    return max(1, math.ceil(qty * (1 - me_pct / 100)))


def decompose(
    type_id: int,
    need_qty: int,
    ds: EVEDataStore,
    me_pct: int,
    depth: int = 0,
    raw: dict = None,
    intermediates: dict = None,
    visited: set = None,
) -> tuple[dict, dict]:
    """
    递归BOM展开。
    raw[typeID]           = 原材料总需求量（不可再分）
    intermediates[typeID] = {qty, runs, act_name, bp_type_id, depth}

    visited 在整个调用树里共享同一个 set，防止循环配方无限递归。
    """
    if raw is None:
        raw = {}
    if intermediates is None:
        intermediates = {}
    if visited is None:
        visited = set()

    if depth > 12 or type_id in visited:
        raw[type_id] = raw.get(type_id, 0) + need_qty
        return raw, intermediates

    recipe = ds.get_recipe(type_id)
    if recipe is None:
        raw[type_id] = raw.get(type_id, 0) + need_qty
        return raw, intermediates

    # 优先级: manufacturing > reaction > (invention 结果本身不分解)
    act = None
    act_name = None
    for pref in ("manufacturing", "reaction"):
        if pref in recipe:
            act = recipe[pref]
            act_name = pref
            break

    if act is None:
        raw[type_id] = raw.get(type_id, 0) + need_qty
        return raw, intermediates

    per_run = act["qty"]
    runs = math.ceil(need_qty / per_run)

    key = type_id
    if key in intermediates:
        intermediates[key]["qty"]  += need_qty
        intermediates[key]["runs"] += runs
    else:
        intermediates[key] = {
            "typeID":           type_id,
            "name":             ds.type_name(type_id),
            "qty":              need_qty,
            "runs":             runs,
            "act_name":         act_name,
            "bp_type_id":       act["blueprintTypeID"],
            "bp_time_per_run":  act["time"],
            "depth":            depth,
            "has_blueprint":    ds.has_blueprint(act["blueprintTypeID"]),
        }

    # 标记已访问，防止环路（visited 是共享引用，无需创建副本）
    visited.add(type_id)

    for mat in act["materials"]:
        mat_qty = apply_me(mat["quantity"] * runs, me_pct)
        decompose(mat["typeID"], mat_qty, ds, me_pct, depth + 1, raw, intermediates, visited)

    return raw, intermediates


def run_bom(ds: EVEDataStore, me_pct: int) -> dict:
    """对所有选定最终产物执行BOM分解"""
    raw_total: dict[int, int] = {}
    inter_total: dict[int, dict] = {}

    for prod in ds.final_products:
        if not prod.get("typeID"):
            continue
        qty = prod["qty"]
        raw, inter = decompose(prod["typeID"], qty, ds, me_pct)
        for tid, q in raw.items():
            raw_total[tid] = raw_total.get(tid, 0) + q
        for tid, info in inter.items():
            if tid in inter_total:
                inter_total[tid]["qty"]  += info["qty"]
                inter_total[tid]["runs"] += info["runs"]
            else:
                inter_total[tid] = copy.deepcopy(info)

    # ── 缺口分析 ──
    raw_list = []
    for tid, need in raw_total.items():
        have = ds.inventory(tid)
        raw_list.append({
            "typeID": tid,
            "name": ds.type_name(tid),
            "need": need,
            "have": have,
            "lack": max(0, need - have),
            "sufficient": have >= need,
        })
    raw_list.sort(key=lambda x: -x["lack"])

    # 中间产物蓝图缺口
    inter_list = sorted(inter_total.values(), key=lambda x: (x["depth"], x["act_name"]))

    return {
        "raw_materials": raw_list,
        "intermediates": inter_list,
        "raw_lacking_count": sum(1 for r in raw_list if not r["sufficient"]),
        "inter_lacking_bp_count": sum(1 for i in inter_list if not i["has_blueprint"]),
    }


# ---------------------------------------------------------------------------
# 3. JOB QUEUE BUILDER
# ---------------------------------------------------------------------------

def build_job_queue(bom: dict, ds: EVEDataStore, te_pct: int, days: float) -> dict:
    """
    从BOM中间产物构建三类任务队列:
      mfg_jobs    制造
      react_jobs  反应
      inv_jobs    发明需求（BPC来源）
    """
    total_secs = days * 86400
    te_mult = 1 - te_pct / 100

    mfg_jobs   = []
    react_jobs = []
    inv_jobs   = []

    for item in bom["intermediates"]:
        bp_tid = item["bp_type_id"]
        base_time = item["bp_time_per_run"]
        job_time = max(1, int(base_time * te_mult))

        has_bpc    = ds.has_bpc(bp_tid)
        has_bpo    = ds.has_bpo(bp_tid)
        is_t2_item = ds.is_t2_bp(bp_tid)

        # T2产品：BPC由发明流程提供，视为"将有BPC"（虚拟BPC）
        # 非T2且无BPO/BPC：记录缺失BPO，添加虚拟BPO供调度
        virtual_bp = False
        if not has_bpc and not has_bpo:
            if is_t2_item:
                virtual_bp = True   # 发明提供BPC
            else:
                virtual_bp = True   # 需购买/已记录缺失BPO

        entry = {
            "typeID":      item["typeID"],
            "name":        item["name"],
            "bp_type_id":  bp_tid,
            "bp_name":     ds.type_name(bp_tid),
            "total_runs":  item["runs"],
            "qty_needed":  item["qty"],
            "job_time_s":  job_time,
            "per_run":     item["bp_time_per_run"],
            "has_bpc":     has_bpc,
            "has_bpo":     has_bpo,
            "has_any_bp":  has_bpc or has_bpo or virtual_bp,
            "virtual_bp":  virtual_bp,   # 虚拟蓝图（发明产出或需购BPO）
            "is_t2_item":  is_t2_item,
            "min_cycles":  math.ceil(job_time / total_secs) if total_secs > 0 else 1,
        }

        if item["act_name"] == "manufacturing":
            mfg_jobs.append(entry)
        elif item["act_name"] == "reaction":
            react_jobs.append(entry)

    # 科研需求：只对 T2蓝图 且 库存中无BPC/BPO 的制造任务生成发明+拷贝需求
    for job in mfg_jobs:
        # 触发发明：T2产品 且 没有BPO，且现有BPC flows < 总需求
        if not job["has_bpo"] and job.get("is_t2_item"):
            existing_flows = ds.total_bpc_flows(job["bp_type_id"])
            if existing_flows >= job["total_runs"]:
                continue  # 现有BPC flows已足够，无需发明
            t2_bp_id = job["bp_type_id"]
            t1_bp_id = ds.t2_to_t1_bp.get(t2_bp_id)   # T1蓝图ID

            # ── 从T1蓝图的invention活动获取参数 ──
            inv_mat    = []
            inv_prob   = 0.3
            inv_time   = 0
            qty_per_inv = 1     # 每次发明成功产出的T2 BPC数量
            if t1_bp_id:
                t1_rec = ds.get_recipe(t1_bp_id)        # 注：用T1蓝图ID查配方
                # T1蓝图的invention活动产物 = T2 BPC
                t1_bp_data = ds.blueprints_yaml.get(int(t1_bp_id), {})
                inv_act = (t1_bp_data.get("activities") or {}).get("invention") or {}
                for prod in (inv_act.get("products") or []):
                    if prod["typeID"] == t2_bp_id:
                        inv_prob    = prod.get("probability", 0.3)
                        qty_per_inv = prod.get("quantity", 1)
                for mat in (inv_act.get("materials") or []):
                    inv_mat.append({
                        "typeID":        mat["typeID"],
                        "name":          ds.type_name(mat["typeID"]),
                        "quantity":      mat["quantity"],
                        "is_datacore":   ds.is_datacore(mat["typeID"]),
                        "is_decryptor":  ds.is_decryptor(mat["typeID"]),
                    })
                inv_time = inv_act.get("time", 0)

            # ── 从T1蓝图的copying活动获取参数 ──
            copy_time = 0
            has_t1_bpo = ds.has_bpo(t1_bp_id) if t1_bp_id else False
            if t1_bp_id and has_t1_bpo:
                t1_bp_data = ds.blueprints_yaml.get(int(t1_bp_id), {})
                copy_act = (t1_bp_data.get("activities") or {}).get("copying") or {}
                copy_time = copy_act.get("time", 0)

            # ── 计算次数 ──
            existing_flows  = ds.total_bpc_flows(job["bp_type_id"])
            needed_t2_bpc   = max(0, job["total_runs"] - existing_flows)
            if needed_t2_bpc == 0:
                continue   # 现有BPC足够，跳过
            # 每次发明（消耗1个T1 BPC）期望产出 = inv_prob * qty_per_inv
            inv_attempts    = math.ceil(needed_t2_bpc / max(inv_prob * qty_per_inv, 0.01))
            copy_runs       = inv_attempts  # 每次发明需要1个T1 BPC

            inv_time_s  = max(1, int(inv_time  * (1 - te_pct / 100)))
            copy_time_s = max(1, int(copy_time * (1 - te_pct / 100))) if copy_time else 0

            # ── 拷贝任务（科研槽位，仅当有T1 BPO时）──
            if copy_runs > 0 and copy_time_s > 0 and has_t1_bpo:
                inv_jobs.append({
                    "activity_type": "copying",
                    "name":          f"{job['name']} [拷贝]",
                    "display_name":  job["name"],
                    "typeID":        t1_bp_id,         # T1蓝图typeID（产物）
                    "has_any_bp":    True,             # 有T1 BPO
                    "job_time_s":    copy_time_s,
                    "total_runs":    copy_runs,
                    "bp_type_id":    t1_bp_id,
                    # 发明专用
                    "product_name":  job["name"],
                    "product_typeID": job["typeID"],
                    "t2_bp_typeID":  t2_bp_id,
                    "t1_bp_typeID":  t1_bp_id,
                    "invention_materials": [],
                    "probability":   1.0,
                    "needed_copies": copy_runs,
                    "expected_attempts": copy_runs,
                })

            # ── 发明任务（科研槽位）──
            if inv_attempts > 0 and inv_time_s > 0:
                inv_jobs.append({
                    "activity_type": "invention",
                    "name":          f"{job['name']} [发明]",
                    "display_name":  job["name"],
                    "typeID":        job["typeID"],
                    "has_any_bp":    True,
                    "job_time_s":    inv_time_s,
                    "total_runs":    inv_attempts,
                    "bp_type_id":    t2_bp_id,
                    # 发明专用
                    "product_name":  job["name"],
                    "product_typeID": job["typeID"],
                    "t2_bp_typeID":  t2_bp_id,
                    "t1_bp_typeID":  t1_bp_id,
                    "invention_materials": inv_mat,
                    "probability":   inv_prob,
                    "qty_per_inv":   qty_per_inv,
                    "needed_copies": needed_t2_bpc,
                    "expected_attempts": inv_attempts,
                })

    mfg_jobs.sort(key=lambda x: (-x["total_runs"], not x["has_any_bp"]))
    react_jobs.sort(key=lambda x: (-x["total_runs"], not x["has_any_bp"]))

    return {
        "mfg_jobs":   mfg_jobs,
        "react_jobs": react_jobs,
        "inv_jobs":   inv_jobs,
    }


# ---------------------------------------------------------------------------
# 6. MAIN PIPELINE
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 5. SIMULATION INPUT BUILDER
# ---------------------------------------------------------------------------

def build_sim_input(
    ds: EVEDataStore,
    jq: dict,
    te_pct: int,
) -> tuple[list[SimTask], list[SimTask], list[SimTask], BpcInventory, set[int], list[dict]]:
    """
    从 EVEDataStore 和任务队列构建模拟器所需的输入：
      - SimTask 列表（制造 / 科研 / 反应）
      - BpcInventory（从 corp 实体库存初始化）
      - bpo_set（corp BPO typeID 集合，含虚拟BPO）
      - missing_bpos（缺失BPO的报告条目）

    虚拟BPO：非T2产品但库存中没有BPO时，打印警告并加入 bpo_set，
    使调度可以进行（不影响BPC流程数计算）。
    """
    # 初始化蓝图库存
    log.info("=== 初始化蓝图库存 ===")
    bpc_inv = BpcInventory()
    bpo_set: set[int] = set()
    for bp in ds._corp_bps_raw:
        bid = int(bp.get("type_id") or bp.get("id") or 0)
        if not bid:
            continue
        if bp.get("is_blueprint_copy"):
            bpc_inv.add(bid, bp.get("runs", 1))
        else:
            bpo_set.add(bid)
    log.info("  BPC种类=%d  BPO种类=%d", len(bpc_inv.snapshot()), len(bpo_set))

    # 缺失BPO报告 + 添加虚拟BPO
    missing_bpos: list[dict] = []
    for j in jq["mfg_jobs"]:
        if j.get("virtual_bp") and not j.get("is_t2_item"):
            bp_id = j["bp_type_id"]
            missing_bpos.append({
                "name":       j["name"],
                "bp_type_id": bp_id,
                "bp_name":    j["bp_name"],
                "total_runs": j["total_runs"],
            })
            bpo_set.add(bp_id)

    if missing_bpos:
        print()
        print("=" * 60)
        print("⚠  缺少以下BPO（已添加虚拟BPO）")
        print("=" * 60)
        for m in missing_bpos:
            print(f"  蓝图: {m['bp_name']:<35s}  用于制造: {m['name']}")
            print(f"        所需轮次: {m['total_runs']}  蓝图ID: {m['bp_type_id']}")
        print("=" * 60)
        print()

    # 构建 SimTask 列表
    log.info("=== 构建模拟任务 ===")
    _counter = 0
    def _next_id() -> int:
        nonlocal _counter
        _counter += 1
        return _counter

    tasks_mfg:   list[SimTask] = []
    tasks_sci:   list[SimTask] = []
    tasks_react: list[SimTask] = []

    for j in jq["mfg_jobs"]:
        tasks_mfg.append(SimTask(
            task_id           = _next_id(),
            name              = j["name"],
            display_name      = j["name"],
            activity          = "manufacturing",
            bp_type_id        = j["bp_type_id"],
            product_type_id   = j["typeID"],
            job_time_s        = j["job_time_s"],
            total_runs_needed = j["total_runs"],
        ))

    for j in jq["inv_jobs"]:
        act      = j.get("activity_type", "invention")
        t1_bp_id = j.get("t1_bp_typeID") or j.get("bp_type_id")
        t2_bp_id = j.get("t2_bp_typeID") or j.get("bp_type_id")
        if act == "copying":
            t1_data  = ds.blueprints_yaml.get(int(t1_bp_id), {}) if t1_bp_id else {}
            max_runs = t1_data.get("maxProductionLimit", 1)
            tasks_sci.append(SimTask(
                task_id           = _next_id(),
                name              = j["name"],
                display_name      = j.get("display_name", j["name"]),
                activity          = "copying",
                bp_type_id        = t1_bp_id,
                product_type_id   = t1_bp_id,
                job_time_s        = j["job_time_s"],
                total_runs_needed = j["total_runs"],
                t1_bp_type_id     = t1_bp_id,
                t2_bp_type_id     = t2_bp_id,
                copy_runs_per_job = max_runs,
            ))
        else:
            tasks_sci.append(SimTask(
                task_id              = _next_id(),
                name                 = j["name"],
                display_name         = j.get("display_name", j["name"]),
                activity             = "invention",
                bp_type_id           = t2_bp_id,
                product_type_id      = t2_bp_id,
                job_time_s           = j["job_time_s"],
                total_runs_needed    = j["total_runs"],
                t1_bp_type_id        = t1_bp_id,
                t2_bp_type_id        = t2_bp_id,
                inv_prob             = j.get("probability", 0.34),
                inv_qty_per_success  = j.get("qty_per_inv", 1),
            ))

    for j in jq["react_jobs"]:
        tasks_react.append(SimTask(
            task_id           = _next_id(),
            name              = j["name"],
            display_name      = j["name"],
            activity          = "reaction",
            bp_type_id        = j["bp_type_id"],
            product_type_id   = j["typeID"],
            job_time_s        = j["job_time_s"],
            total_runs_needed = j["total_runs"],
        ))

    log.info("  制造=%d  科研=%d(拷贝+发明)  反应=%d",
             len(tasks_mfg), len(tasks_sci), len(tasks_react))
    return tasks_mfg, tasks_sci, tasks_react, bpc_inv, bpo_set, missing_bpos


def format_sim_output(
    sim_result: dict,
    period_secs: float,
) -> dict:
    """
    把 simulate_production() 的输出转换成可视化期望的 greedy_schedule 格式。
    每条产线：cycles 列表，每个 cycle 含 slots 列表（单 job）。
    """
    def _to_schedule(slot_key: str, completion_key: str) -> dict:
        cycles = []
        total_util = 0.0
        for cyc in sim_result["cycles"]:
            slots_raw = cyc.get(slot_key, [])
            slots, util_sum = [], 0.0
            for rec in slots_raw:
                dur = rec.get("duration_s", 0)
                util_sum += dur / period_secs if period_secs > 0 else 0
                slots.append({
                    "slot_id": rec["slot_id"],
                    "jobs": [{
                        "typeID":        rec.get("typeID"),
                        "name":          rec.get("name", "?"),
                        "display_name":  rec.get("display_name", rec.get("name", "?")),
                        "activity_type": rec.get("activity_type", ""),
                        "bp_type_id":    rec.get("bp_type_id"),
                        "runs":          rec.get("runs", 0),
                        "duration_s":    dur,
                        "start_s":       0.0,
                        "end_s":         dur,
                        "note":          rec.get("note", ""),
                    }],
                })
            n_slots = max(len(slots_raw), 1)
            avg_u = util_sum / n_slots
            total_util += avg_u
            cycles.append({"cycle_id": cyc["cycle_id"], "avg_util": round(avg_u, 4), "slots": slots})
        n_cyc = len(cycles)
        return {
            "period_secs":     period_secs,
            "total_cycles":    sim_result["total_cycles"],
            "total_real_days": sim_result["total_real_days"],
            "avg_util":        round(total_util / n_cyc, 4) if n_cyc else 0,
            "cycles":          cycles,
            "completion":      sim_result[completion_key],
        }

    return {
        "mfg":   _to_schedule("mfg_slots",   "completion_mfg"),
        "react": _to_schedule("react_slots",  "completion_react"),
        "inv":   _to_schedule("sci_slots",    "completion_sci"),
    }


# ---------------------------------------------------------------------------
# 6. MAIN PIPELINE
# ---------------------------------------------------------------------------

def run_planner(cfg: dict, eve_root: Path) -> dict:
    """
    主规划流水线。所有参数从 cfg 读取，路径相对于 eve_root。
    """
    prod   = cfg["production"]
    days   = prod["days"]
    me_pct = prod["me_pct"]
    te_pct = prod["te_pct"]
    slots_mfg   = prod["slots_mfg"]
    slots_inv   = prod["slots_inv"]
    slots_react = prod["slots_react"]
    out_dir  = eve_root / cfg["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "plan_result.json"
    total_secs = days * 86400

    ds  = EVEDataStore(cfg, eve_root)
    bom = run_bom(ds, me_pct)
    log.info("=== BOM 分解 (ME=%d%%) ===  原材料=%d  中间产物=%d  缺BP=%d",
             me_pct, len(bom["raw_materials"]),
             len(bom["intermediates"]), bom["inter_lacking_bp_count"])

    jq = build_job_queue(bom, ds, te_pct, days)
    log.info("=== 任务队列 ===  制造=%d  反应=%d  科研=%d",
             len(jq["mfg_jobs"]), len(jq["react_jobs"]), len(jq["inv_jobs"]))

    tasks_mfg, tasks_sci, tasks_react, bpc_inv, bpo_set, missing_bpos = \
        build_sim_input(ds, jq, te_pct)

    log.info("=== 模拟生产 (周期=%.2f天=%.0fs) ===", days, total_secs)
    sim_result = simulate_production(
        tasks_mfg   = tasks_mfg,
        tasks_sci   = tasks_sci,
        tasks_react = tasks_react,
        slots_mfg   = slots_mfg,
        slots_sci   = slots_inv,
        slots_react = slots_react,
        period_secs = total_secs,
        bpc_inv     = bpc_inv,
        bpo_set     = bpo_set,
    )

    all_comp = (sim_result["completion_mfg"]
                + sim_result["completion_sci"]
                + sim_result["completion_react"])
    done_n = sum(1 for c in all_comp if c["pct"] >= 100)
    log.info("  总周期=%d (%.2f天)  任务完成=%d/%d",
             sim_result["total_cycles"], sim_result["total_real_days"],
             done_n, len(all_comp))
    for c in (x for x in all_comp if x["pct"] < 100):
        log.warning("  未完成: %s  %d/%d (%.1f%%)",
                    c["name"], c["done_runs"], c["need_runs"], c["pct"])

    result = {
        "config": {
            "eve_root":    str(eve_root),
            "days":        days,
            "total_secs":  total_secs,
            "me_pct":      me_pct,
            "te_pct":      te_pct,
            "slots_mfg":   slots_mfg,
            "slots_inv":   slots_inv,
            "slots_react": slots_react,
        },
        "final_products":  ds.final_products,
        "bom":             bom,
        "job_queue":       jq,
        "missing_bpos":    missing_bpos,
        "greedy_schedule": format_sim_output(sim_result, total_secs),
        "sim_summary": {
            "total_cycles":    sim_result["total_cycles"],
            "total_real_days": sim_result["total_real_days"],
            "bpc_final":       sim_result["bpc_final"],
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info("=== 结果已写入: %s ===", out_path)
    return result


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> tuple[dict, Path]:
    """
    加载 config.json，返回 (cfg, eve_root)。
    eve_root 默认为 config.json 所在目录的上两级（apps/app_name/ -> eve/）。
    """
    cfg = load_json(config_path)
    # 推断 eve_root：config 在 eve/apps/industry_planner/config.json
    # eve_root = config.parent.parent.parent = eve/
    eve_root = config_path.resolve().parent.parent.parent
    return cfg, eve_root


def main():
    parser = argparse.ArgumentParser(
        description="EVE Industry Planner — 配置驱动生产规划",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python planner.py                          # 使用同目录 config.json
  python planner.py --config /path/config.json
  python planner.py --root /path/to/eve      # 指定 eve/ 根目录
  python planner.py --days 2 --me 8          # 覆盖部分参数
        """
    )
    parser.add_argument("--config",     default=None,  help="config.json 路径（默认同目录）")
    parser.add_argument("--root",       default=None,  help="eve/ 根目录（覆盖 config 推断）")
    parser.add_argument("--days",       type=float,    help="规划天数（覆盖 config）")
    parser.add_argument("--me",         type=int,      help="材料效率 0-10（覆盖 config）")
    parser.add_argument("--te",         type=int,      help="时间效率 0-20（覆盖 config）")
    parser.add_argument("--slots-mfg",  type=int,      help="制造槽位数（覆盖 config）")
    parser.add_argument("--slots-inv",  type=int,      help="科研槽位数（覆盖 config）")
    parser.add_argument("--slots-react",type=int,      help="反应槽位数（覆盖 config）")
    args = parser.parse_args()

    # 定位 config.json
    if args.config:
        config_path = Path(args.config)
    else:
        config_path = Path(__file__).parent / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"config.json 不存在: {config_path}")

    cfg, eve_root = load_config(config_path)

    # --root 覆盖推断的 eve_root
    if args.root:
        eve_root = Path(args.root).resolve()

    # 命令行参数覆盖 config 中的 production 字段
    p = cfg["production"]
    if args.days        is not None: p["days"]        = args.days
    if args.me          is not None: p["me_pct"]      = args.me
    if args.te          is not None: p["te_pct"]      = args.te
    if args.slots_mfg   is not None: p["slots_mfg"]   = args.slots_mfg
    if args.slots_inv   is not None: p["slots_inv"]   = args.slots_inv
    if args.slots_react is not None: p["slots_react"] = args.slots_react

    log.info("config: %s", config_path)
    log.info("eve_root: %s", eve_root)
    run_planner(cfg, eve_root)


if __name__ == "__main__":
    main()
