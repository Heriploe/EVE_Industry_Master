"""
EVE Industry Simulation Engine
================================
真正的逐周期模拟，蓝图流程数实体化管理。

核心数据结构：
  BpcInventory: {bp_type_id: [runs, runs, ...]}  runs少的优先消耗
  BpoInventory: {bp_type_id}                      BPO无限使用

每周期流程：
  1. 查看当前库存 → 判断哪些任务可以启动
  2. 贪心分配槽位（单槽单订单）
  3. 周期结束 → 结算：产物入库、消耗出库
"""

import math
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("eve_sim")


# ---------------------------------------------------------------------------
# BPC库存管理
# ---------------------------------------------------------------------------

class BpcInventory:
    """
    BPC库存：{bp_type_id: sorted list of runs}
    runs从小到大排序，消耗时优先取runs最少的（避免浪费大流程BPC）
    """
    def __init__(self):
        self._inv: dict[int, list[int]] = defaultdict(list)

    def add(self, bp_type_id: int, runs: int):
        """加入一个BPC（runs个流程）"""
        lst = self._inv[bp_type_id]
        # 插入排序保持有序
        i = 0
        while i < len(lst) and lst[i] < runs:
            i += 1
        lst.insert(i, runs)

    def available_flows(self, bp_type_id: int) -> int:
        """该bp_type_id的总可用流程数"""
        return sum(self._inv.get(bp_type_id, []))

    def has(self, bp_type_id: int, needed: int = 1) -> bool:
        return self.available_flows(bp_type_id) >= needed

    def consume(self, bp_type_id: int, n: int = 1) -> int:
        """
        消耗n个流程，优先消耗runs最少的BPC。
        返回实际消耗数。
        """
        lst = self._inv.get(bp_type_id)
        if not lst:
            return 0
        consumed = 0
        while consumed < n and lst:
            r = lst[0]
            take = min(r, n - consumed)
            lst[0] -= take
            consumed += take
            if lst[0] == 0:
                lst.pop(0)
        return consumed

    def peek_first_runs(self, bp_type_id: int) -> int:
        """查看runs最少的BPC有多少runs（不消耗）"""
        lst = self._inv.get(bp_type_id, [])
        return lst[0] if lst else 0

    def snapshot(self) -> dict[int, list[int]]:
        return {k: list(v) for k, v in self._inv.items() if v}

    def copy(self) -> "BpcInventory":
        new = BpcInventory()
        for k, v in self._inv.items():
            new._inv[k] = list(v)
        return new


# ---------------------------------------------------------------------------
# 任务定义
# ---------------------------------------------------------------------------

@dataclass
class SimTask:
    """模拟任务"""
    task_id:      int
    name:         str           # 产物名
    display_name: str           # 不带后缀的名称
    activity:     str           # "manufacturing" / "copying" / "invention" / "reaction"
    bp_type_id:   int           # 蓝图typeID（BPO/BPC查询用）
    product_type_id: int        # 产物typeID
    job_time_s:   int           # 单run时长（秒，已应用TE）
    total_runs_needed: int      # 需要完成的总runs
    
    # 发明相关
    t1_bp_type_id: Optional[int] = None   # 发明用的T1蓝图ID（拷贝产出/库存中的T1 BPC ID）
    t2_bp_type_id: Optional[int] = None   # 发明产出的T2 BPC的typeID
    inv_prob:      float = 1.0
    inv_qty_per_success: int = 1          # 发明成功产出的T2 BPC runs数
    
    # 拷贝相关
    copy_runs_per_job: int = 1            # 每次拷贝产出的BPC runs数
    
    # 状态
    done_runs: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.total_runs_needed - self.done_runs)

    @property
    def is_done(self) -> bool:
        return self.done_runs >= self.total_runs_needed


# ---------------------------------------------------------------------------
# 模拟核心
# ---------------------------------------------------------------------------

def simulate_production(
    tasks_mfg:   list[SimTask],
    tasks_sci:   list[SimTask],   # copying + invention 混合
    tasks_react: list[SimTask],
    slots_mfg:   int,
    slots_sci:   int,
    slots_react: int,
    period_secs: float,
    bpc_inv:     BpcInventory,
    bpo_set:     set[int],
    max_cycles:  int = 300,
) -> dict:
    """
    主模拟循环。
    
    每个周期：
      Phase 1: 查看当前库存，判断各任务可启动runs数
      Phase 2: 贪心分配槽位（单槽单任务，单周期内最多完成 floor(period/job_time) runs）
      Phase 3: 周期结束结算：产物入库，蓝图flows扣除
    
    依赖约束（Phase 1检查）：
      manufacturing：需要bp_type_id对应的BPC(flows) 或 BPO
      copying：需要bp_type_id对应的BPO（T1 BPO）
      invention：需要t1_bp_type_id对应的BPC(1 flow per run)
      reaction：需要BPO或BPC
    
    产出（Phase 3）：
      manufacturing：产品（不追踪，只记录完成runs）
      copying：向库存加入 BPC(t1_bp_type_id, copy_runs_per_job)
      invention：向库存加入 BPC(t2_bp_type_id, inv_qty_per_success) × 成功次数
                 从库存消耗 BPC(t1_bp_type_id, 1) × 发明次数
      reaction：产品（不追踪）
    """
    # 所有任务索引
    all_tasks: dict[int, SimTask] = {}
    for t in tasks_mfg + tasks_sci + tasks_react:
        all_tasks[t.task_id] = t

    # 按产线分组
    mfg_ids   = [t.task_id for t in tasks_mfg]
    sci_ids   = [t.task_id for t in tasks_sci]
    react_ids = [t.task_id for t in tasks_react]

    cycles_out: list[dict] = []
    cycle_id = 0
    stall_count = 0

    def _avail_runs(task: SimTask) -> int:
        """周期开始前：该任务最多可启动多少runs（受库存和剩余需求限制）"""
        if task.is_done:
            return 0
        act = task.activity

        if act == "manufacturing":
            if task.bp_type_id in bpo_set:
                return task.remaining   # BPO无限
            else:
                # 需要BPC flows
                avail = bpc_inv.available_flows(task.bp_type_id)
                return min(task.remaining, avail)

        elif act == "copying":
            # 需要BPO
            if task.bp_type_id in bpo_set:
                return task.remaining
            return 0

        elif act == "invention":
            # 每次发明消耗1个T1 BPC flow
            if task.t1_bp_type_id is None:
                return 0
            avail = bpc_inv.available_flows(task.t1_bp_type_id)
            return min(task.remaining, avail)

        elif act == "reaction":
            if task.bp_type_id in bpo_set:
                return task.remaining
            avail = bpc_inv.available_flows(task.bp_type_id)
            return min(task.remaining, avail)

        return 0

    def _fits_in_cycle(task: SimTask, avail: int) -> int:
        """一个周期内单槽位最多能完成几runs（时间约束）"""
        if task.job_time_s <= 0:
            return avail
        fit = max(1, int(period_secs // task.job_time_s))
        return min(avail, fit)

    def _settle(slot_results: list[tuple[SimTask, int]]):
        """周期结束结算：产物入库，蓝图flows扣除"""
        for task, runs_done in slot_results:
            if runs_done <= 0:
                continue
            task.done_runs += runs_done
            act = task.activity

            if act == "copying":
                # 每run产出1个BPC（copy_runs_per_job个流程）
                for _ in range(runs_done):
                    bpc_inv.add(task.product_type_id, task.copy_runs_per_job)

            elif act == "invention":
                # 消耗T1 BPC flows（每次发明消耗1个flow）
                if task.t1_bp_type_id:
                    bpc_inv.consume(task.t1_bp_type_id, runs_done)
                # 产出T2 BPC：按期望值整数化
                # task已规划 total_runs_needed = ceil(needed/(prob*qty))
                # done到这里期望已产 done*prob*qty 个T2 BPC
                # 本次新增 runs_done * prob * qty，取floor
                prev_bpc = (task.done_runs - runs_done) * task.inv_prob * task.inv_qty_per_success
                curr_bpc = task.done_runs * task.inv_prob * task.inv_qty_per_success
                new_bpcs = int(curr_bpc) - int(prev_bpc)
                for _ in range(new_bpcs):
                    bpc_inv.add(task.t2_bp_type_id, task.inv_qty_per_success)

            elif act == "manufacturing":
                if task.bp_type_id not in bpo_set:
                    bpc_inv.consume(task.bp_type_id, runs_done)

            elif act == "reaction":
                if task.bp_type_id not in bpo_set:
                    bpc_inv.consume(task.bp_type_id, runs_done)

    def _schedule_line(task_ids: list[int], n_slots: int) -> list[tuple[SimTask, int]]:
        """
        为一条产线分配槽位。
        返回 [(task, runs_this_cycle), ...]
        单槽单任务，优先安排库存受限任务（copying优先于invention，invention优先于manufacturing）
        """
        # 计算每个任务可安排runs
        eligible: list[tuple[int, SimTask, int]] = []  # (priority, task, runs)
        for tid in task_ids:
            task = all_tasks[tid]
            avail = _avail_runs(task)
            if avail <= 0:
                continue
            runs = _fits_in_cycle(task, avail)
            if runs <= 0:
                continue
            # 优先级：copying=0, invention=1, manufacturing/reaction=2
            p = {"copying": 0, "invention": 1}.get(task.activity, 2)
            eligible.append((p, task, runs))

        # 按优先级排序，同优先级按剩余runs降序（量大优先）
        eligible.sort(key=lambda x: (x[0], -x[1].remaining))

        results: list[tuple[SimTask, int]] = []
        slots_used = 0
        for _, task, runs in eligible:
            if slots_used >= n_slots:
                break
            results.append((task, runs))
            slots_used += 1

        return results

    # ── 主循环 ─────────────────────────────────────────────────────────────
    while cycle_id < max_cycles:
        any_remaining = any(
            not all_tasks[tid].is_done
            for tid in mfg_ids + sci_ids + react_ids
        )
        if not any_remaining:
            break

        cycle_id += 1

        # Phase 1+2: 分配各产线槽位
        mfg_results   = _schedule_line(mfg_ids,   slots_mfg)
        sci_results   = _schedule_line(sci_ids,   slots_sci)
        react_results = _schedule_line(react_ids, slots_react)

        all_results = mfg_results + sci_results + react_results
        total_runs_this_cycle = sum(r for _, r in all_results)

        # Phase 3: 结算
        _settle(all_results)

        # 构建输出cycle记录
        def _make_slot_records(results: list[tuple[SimTask, int]], line: str) -> list[dict]:
            recs = []
            for i, (task, runs) in enumerate(results):
                dur = min(runs * task.job_time_s, period_secs)
                recs.append({
                    "slot_id":       i,
                    "task_id":       task.task_id,
                    "name":          task.name,
                    "display_name":  task.display_name,
                    "activity_type": task.activity,
                    "bp_type_id":    task.bp_type_id,
                    "runs":          runs,
                    "duration_s":    dur,
                    "start_s":       0.0,
                    "end_s":         dur,
                    "remaining_after": task.remaining,
                })
            return recs

        mfg_slots   = _make_slot_records(mfg_results, "mfg")
        sci_slots   = _make_slot_records(sci_results, "sci")
        react_slots = _make_slot_records(react_results, "react")

        # 利用率
        def _util(results, n_slots):
            if n_slots == 0: return 0.0
            used = sum(min(r * t.job_time_s, period_secs) for t, r in results)
            return used / (n_slots * period_secs)

        cycles_out.append({
            "cycle_id":   cycle_id,
            "mfg_slots":  mfg_slots,
            "sci_slots":  sci_slots,
            "react_slots": react_slots,
            "util_mfg":   round(_util(mfg_results,   slots_mfg),   4),
            "util_sci":   round(_util(sci_results,   slots_sci),   4),
            "util_react": round(_util(react_results, slots_react), 4),
            "bpc_snapshot": bpc_inv.snapshot(),  # 周期结束后库存快照
        })

        # 停滞检测
        if total_runs_this_cycle == 0:
            stall_count += 1
            if stall_count >= 3:
                log.warning("模拟停滞（3个周期无进展），提前退出。cycle=%d", cycle_id)
                break
        else:
            stall_count = 0

    # 完成率报告
    def _report(task_ids):
        rows = []
        for tid in task_ids:
            t = all_tasks[tid]
            rows.append({
                "task_id":   t.task_id,
                "name":      t.display_name,
                "activity":  t.activity,
                "done_runs": t.done_runs,
                "need_runs": t.total_runs_needed,
                "pct":       round(t.done_runs / t.total_runs_needed * 100, 1)
                             if t.total_runs_needed > 0 else 100.0,
            })
        return sorted(rows, key=lambda x: x["pct"])

    total_cycles = cycle_id

    return {
        "total_cycles":    total_cycles,
        "total_real_days": round(total_cycles * period_secs / 86400, 3),
        "period_secs":     period_secs,
        "cycles":          cycles_out,
        "completion_mfg":   _report(mfg_ids),
        "completion_sci":   _report(sci_ids),
        "completion_react": _report(react_ids),
        "bpc_final":        bpc_inv.snapshot(),
    }
