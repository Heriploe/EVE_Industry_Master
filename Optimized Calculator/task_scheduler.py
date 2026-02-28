"""
Task Scheduler v7.0 - 任务拆分 + 槽位并行 + 库存优先

核心特性:
1. 从execution_list_filtered.csv读取目标
2. 递归分解产物，优先使用库存
3. 自动规划蓝图拷贝和发明
4. 任务拆分：长任务拆分为多个周期的子任务
5. 槽位并行：科研/制造/反应槽位充分利用
6. DAG管理依赖关系
"""

import json
import csv
import copy
import random
import math
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple
from collections import defaultdict, deque

# ==================配置参数==================
RESEARCH_SLOTS = 9
MANUFACTURING_SLOTS = 9
REACTION_SLOTS = 9

DELIVERY_CYCLE_HOURS = 24
MAX_TASK_DURATION_HOURS = 24  # 超过此时长的任务将被拆分

# 蓝图参数
COPYING_RUNS_PER_JOB = 10
COPYING_DURATION_PER_JOB = 1.0  # 小时
INVENTION_DURATION_PER_JOB = 2.0  # 小时
INVENTION_BASE_SUCCESS_RATE = 0.34
T2_BLUEPRINT_RUNS_PER_INVENTION = 1

# 文件路径
data_Dir = "Source"
result_Dir = "Results"
inventory_Dir = "Inventory"

EXECUTION_FILTERED_CSV = f"{result_Dir}/execution_list_filtered.csv"
INITIAL_INVENTORY_JSON = f"{inventory_Dir}/initial_inventory.json"
BLUEPRINTS_JSON = f"{inventory_Dir}/blueprints.json"
BLUEPRINTS_MERGED_JSON = f"{data_Dir}/blueprints_merged.json"
T2_JSON = f"{data_Dir}/T2.json"
JITA_PRICES_JSON = f"{data_Dir}/jita_prices.json"
TYPES_JSON = f"{data_Dir}/types.json"

SIMULATION_RESULT_JSON = f"{result_Dir}/simulation_result.json"
SIMULATION_RESULT_CSV = f"{result_Dir}/simulation_result.csv"

# ==================数据类==================
@dataclass
class TaskNode:
    """任务节点"""
    task_id: int
    task_type: str  # "copying", "invention", "manufacturing", "reaction"
    blueprint_id: int
    blueprint_name: str
    product_id: int
    product_name: str
    runs: int  # 原始runs数
    duration_per_run: float  # 每run耗时（小时）
    dependencies: Set[int] = field(default_factory=set)
    completed: bool = False
    completed_runs: int = 0  # 已完成的runs数
    depth: int = 0

    @property
    def remaining_runs(self):
        return self.runs - self.completed_runs
    
    @property
    def total_duration(self):
        return self.duration_per_run * self.runs

@dataclass
class SubTask:
    """子任务（拆分后的任务片段）"""
    parent_task_id: int
    runs: int
    duration: float
    slot_type: str
    
@dataclass
class ScheduledTask:
    """调度任务"""
    task_id: int
    task_node: TaskNode
    sub_task: SubTask
    slot_type: str
    slot_id: int
    start_time: float
    end_time: float
    value: float = 0

# ==================加载数据==================
print("=" * 70)
print("Task Scheduler v7.0 - 拆分+并行+库存优先")
print("=" * 70)
print("\n加载数据...")

with open(INITIAL_INVENTORY_JSON, "r", encoding="utf-8") as f:
    inventory_list = json.load(f)
inventory = {item["type_id"]: item["quantity"] for item in inventory_list}

with open(BLUEPRINTS_JSON, "r", encoding="utf-8") as f:
    blueprints_list = json.load(f)

with open(BLUEPRINTS_MERGED_JSON, "r", encoding="utf-8") as f:
    blueprints_db = json.load(f)

with open(T2_JSON, "r", encoding="utf-8") as f:
    t2_pairs = json.load(f)
t2_to_t1 = {int(pair[1]): int(pair[0]) for pair in t2_pairs if len(pair) == 2}

with open(JITA_PRICES_JSON, "r", encoding="utf-8") as f:
    jita_prices_raw = json.load(f)
jita_prices = {int(k): v["jita"].get("buy", 0) if isinstance(v["jita"].get("buy"), (int, float)) else 0 
               for k, v in jita_prices_raw.items()}

with open(TYPES_JSON, "r", encoding="utf-8") as f:
    types_list = json.load(f)
types_map = {int(item["id"]): {"zh": item.get("zh", ""), "en": item.get("en", "")} 
             for item in types_list if item.get("id")}

execution_targets = {}
try:
    with open(EXECUTION_FILTERED_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) >= 2:
                execution_targets[row[0]] = int(row[1])
except FileNotFoundError:
    print(f"❌ 未找到 {EXECUTION_FILTERED_CSV}")
    exit(1)

print(f"✓ 库存: {len(inventory)} 项")
print(f"✓ 蓝图: {len(blueprints_list)} 个")
print(f"✓ 数据库: {len(blueprints_db)} 条")
print(f"✓ T2映射: {len(t2_to_t1)} 对")
print(f"✓ 目标: {len(execution_targets)} 个\n")

# ==================工具函数==================
def get_name(tid):
    return types_map.get(int(tid), {"zh": f"未知_{tid}", "en": f"UNKNOWN_{tid}"})

def get_price(tid):
    return jita_prices.get(int(tid), 0)

def find_blueprint_by_id(bp_id):
    for bp in blueprints_db:
        if bp.get("blueprintTypeID") == bp_id:
            return bp
    return None

def find_blueprint_by_product(product_id):
    for bp in blueprints_db:
        if "manufacturing" in bp:
            for p in bp["manufacturing"].get("products", []):
                if p["typeID"] == product_id:
                    return bp
        elif "reaction" in bp:
            for p in bp["reaction"].get("products", []):
                if p["typeID"] == product_id:
                    return bp
    return None

def find_blueprint_by_name(bp_name):
    for tid, names in types_map.items():
        if names["zh"] == bp_name or names["en"] == bp_name:
            return find_blueprint_by_id(tid)
    return None

def get_activity(bp):
    if "manufacturing" in bp:
        return bp["manufacturing"], "manufacturing"
    if "reaction" in bp:
        return bp["reaction"], "reaction"
    return None, None

def estimate_duration(activity, te=0):
    base_time = activity.get("time", 3600)
    time_multiplier = max(0.2, 1 - (te * 0.01))
    return (base_time * time_multiplier) / 3600

# ==================生产规划器==================
class ProductionPlanner:
    """生产规划器 - 库存优先 + 递归分解"""
    
    def __init__(self, inventory, blueprints):
        self.working_inventory = copy.deepcopy(inventory)
        self.blueprint_copies = copy.deepcopy(blueprints)
        self.tasks = []
        self.task_counter = 0
        self.task_dependencies = defaultdict(set)
        
    def get_available_runs(self, bp_id):
        total = 0
        for bp in self.blueprint_copies:
            if bp["type_id"] == bp_id:
                if bp.get("runs", -1) == -1:
                    return float('inf')
                total += bp.get("runs", 0)
        return total
    
    def plan_execution_targets(self, execution_targets):
        print("=" * 70)
        print("生产规划（库存优先）")
        print("=" * 70)
        
        for bp_name, runs in execution_targets.items():
            bp = find_blueprint_by_name(bp_name)
            if not bp:
                print(f"⚠️ 找不到: {bp_name}")
                continue
            
            bp_id = bp.get("blueprintTypeID")
            activity, _ = get_activity(bp)
            if not activity:
                continue
            
            products = activity.get("products", [])
            if not products:
                continue
            
            product_id = products[0]["typeID"]
            product_name = get_name(product_id)["zh"]
            quantity = products[0].get("quantity", 1) * runs
            
            print(f"\n目标: {product_name} × {quantity}")
            self.plan_product(product_id, quantity, depth=0)
        
        print(f"\n生成 {len(self.tasks)} 个任务")
        self.print_task_stats()
        return self.tasks
    
    def plan_product(self, product_id, quantity, depth=0):
        indent = "  " * depth
        product_name = get_name(product_id)["zh"]
        
        # 检查库存
        available = self.working_inventory.get(product_id, 0)
        if available >= quantity:
            self.working_inventory[product_id] -= quantity
            print(f"{indent}✓ 库存: {product_name} ({quantity}/{available})")
            return []
        
        # 需要生产
        needed = quantity - available
        if available > 0:
            print(f"{indent}📦 部分库存: {product_name} ({available}/{quantity})")
            self.working_inventory[product_id] = 0
        else:
            print(f"{indent}📦 需要: {product_name} × {needed}")
        
        # 查找蓝图
        bp = find_blueprint_by_product(product_id)
        if not bp:
            print(f"{indent}  ⚠️ 无蓝图")
            return []
        
        bp_id = bp.get("blueprintTypeID")
        activity, act_type = get_activity(bp)
        if not activity:
            return []
        
        # 计算runs
        products = activity.get("products", [])
        qty_per_run = next((p["quantity"] for p in products if p["typeID"] == product_id), 1)
        runs_needed = math.ceil(needed / qty_per_run)
        
        print(f"{indent}  需要 {runs_needed} runs (每run={qty_per_run})")
        
        # 递归分解材料
        material_tasks = []
        materials = activity.get("materials", [])
        for mat in materials:
            mat_id = mat["typeID"]
            mat_qty = mat["quantity"] * runs_needed
            sub_tasks = self.plan_product(mat_id, mat_qty, depth + 1)
            material_tasks.extend(sub_tasks)
        
        # 规划蓝图（仅制造需要）
        blueprint_tasks = []
        if act_type == "manufacturing":
            blueprint_tasks = self.plan_blueprint_needs(bp_id, runs_needed, depth)
        
        # 创建生产任务
        duration_per_run = estimate_duration(activity, 0)
        production_task = TaskNode(
            task_id=self.task_counter,
            task_type=act_type,
            blueprint_id=bp_id,
            blueprint_name=get_name(bp_id)["zh"],
            product_id=product_id,
            product_name=product_name,
            runs=runs_needed,
            duration_per_run=duration_per_run,
            depth=depth
        )
        self.tasks.append(production_task)
        self.task_counter += 1
        
        # 建立依赖
        all_deps = material_tasks + blueprint_tasks
        if all_deps:
            for dep in all_deps:
                self.task_dependencies[production_task.task_id].add(dep.task_id)
        
        return all_deps + [production_task]
    
    def plan_blueprint_needs(self, bp_id, runs_needed, depth):
        tasks = []
        indent = "  " * (depth + 1)
        
        # 检查是否T2
        is_t2 = bp_id in t2_to_t1.values()
        
        if is_t2:
            tasks.extend(self.plan_t2_blueprint(bp_id, runs_needed, depth + 1))
        else:
            available = self.get_available_runs(bp_id)
            if available < runs_needed:
                shortage = runs_needed - available
                copy_jobs = math.ceil(shortage / COPYING_RUNS_PER_JOB)
                
                bp_name = get_name(bp_id)["zh"]
                print(f"{indent}📋 拷贝: {bp_name} ({copy_jobs}次)")
                
                task = TaskNode(
                    task_id=self.task_counter,
                    task_type="copying",
                    blueprint_id=bp_id,
                    blueprint_name=bp_name,
                    product_id=bp_id,
                    product_name=f"{bp_name}拷贝",
                    runs=copy_jobs,
                    duration_per_run=COPYING_DURATION_PER_JOB,
                    depth=depth + 1
                )
                self.tasks.append(task)
                tasks.append(task)
                self.task_counter += 1
        
        return tasks
    
    def plan_t2_blueprint(self, t2_bp_id, runs_needed, depth):
        tasks = []
        indent = "  " * depth
        
        available = self.get_available_runs(t2_bp_id)
        if available >= runs_needed:
            return tasks
        
        shortage = runs_needed - available
        invention_jobs = math.ceil(shortage / (T2_BLUEPRINT_RUNS_PER_INVENTION * INVENTION_BASE_SUCCESS_RATE))
        
        t2_name = get_name(t2_bp_id)["zh"]
        print(f"{indent}🔬 发明: {t2_name} ({invention_jobs}次)")
        
        # 获取T1蓝图
        t1_bp_id = t2_to_t1.get(t2_bp_id)
        if not t1_bp_id:
            return tasks
        
        # 规划T1拷贝
        t1_tasks = self.plan_blueprint_needs(t1_bp_id, invention_jobs, depth)
        tasks.extend(t1_tasks)
        
        # 创建发明任务
        invention_task = TaskNode(
            task_id=self.task_counter,
            task_type="invention",
            blueprint_id=t1_bp_id,
            blueprint_name=get_name(t1_bp_id)["zh"],
            product_id=t2_bp_id,
            product_name=t2_name,
            runs=invention_jobs,
            duration_per_run=INVENTION_DURATION_PER_JOB,
            depth=depth
        )
        self.tasks.append(invention_task)
        self.task_counter += 1
        
        # 建立依赖
        if t1_tasks:
            for t1_task in t1_tasks:
                self.task_dependencies[invention_task.task_id].add(t1_task.task_id)
        
        tasks.append(invention_task)
        return tasks
    
    def build_dag(self):
        for task in self.tasks:
            task.dependencies = self.task_dependencies[task.task_id]
        return self.tasks
    
    def print_task_stats(self):
        stats = defaultdict(int)
        for task in self.tasks:
            stats[task.task_type] += 1
        print("\n任务统计:")
        for task_type, count in sorted(stats.items()):
            print(f"  {task_type}: {count}")

# ==================调度器==================
class SmartScheduler:
    """智能调度器 - 支持任务拆分和槽位并行"""
    
    def __init__(self, tasks):
        self.tasks = {task.task_id: task for task in tasks}
        self.slots = {
            "research": {i: None for i in range(RESEARCH_SLOTS)},
            "manufacturing": {i: None for i in range(MANUFACTURING_SLOTS)},
            "reaction": {i: None for i in range(REACTION_SLOTS)}
        }
        self.completed_tasks = []
        self.total_hours = 0
        self.total_value = 0
    
    def get_slot_type(self, task_type):
        """获取任务对应的槽位类型"""
        slot_map = {
            "copying": "research",
            "invention": "research",
            "manufacturing": "manufacturing",
            "reaction": "reaction"
        }
        return slot_map.get(task_type, "manufacturing")
    
    def get_ready_tasks(self):
        """获取就绪任务"""
        ready = []
        for task in self.tasks.values():
            if not task.completed and task.remaining_runs > 0:
                if all(self.tasks[dep].completed for dep in task.dependencies):
                    ready.append(task)
        return ready
    
    def split_task(self, task, max_duration):
        """拆分任务为多个子任务"""
        runs_per_subtask = max(1, int(max_duration / task.duration_per_run))
        subtasks = []
        
        remaining = task.remaining_runs
        while remaining > 0:
            chunk_runs = min(runs_per_subtask, remaining)
            subtask = SubTask(
                parent_task_id=task.task_id,
                runs=chunk_runs,
                duration=chunk_runs * task.duration_per_run,
                slot_type=self.get_slot_type(task.task_type)
            )
            subtasks.append(subtask)
            remaining -= chunk_runs
        
        return subtasks
    
    def get_free_slots(self, slot_type):
        """获取所有空闲槽位"""
        free = []
        for slot_id, data in self.slots[slot_type].items():
            if data is None or data[1] <= 0:
                free.append(slot_id)
        return free
    
    def schedule_subtask(self, task, subtask, slot_id):
        """调度一个子任务"""
        scheduled = ScheduledTask(
            task_id=task.task_id,
            task_node=task,
            sub_task=subtask,
            slot_type=subtask.slot_type,
            slot_id=slot_id,
            start_time=self.total_hours,
            end_time=self.total_hours + subtask.duration
        )
        
        self.slots[subtask.slot_type][slot_id] = (scheduled, subtask.duration)
        return scheduled
    
    def advance_time(self, hours):
        """推进时间"""
        self.total_hours += hours
        
        for slot_type in self.slots:
            for slot_id in self.slots[slot_type]:
                if self.slots[slot_type][slot_id]:
                    scheduled, remaining = self.slots[slot_type][slot_id]
                    remaining -= hours
                    
                    if remaining <= 0:
                        self.complete_subtask(scheduled)
                        self.slots[slot_type][slot_id] = None
                    else:
                        self.slots[slot_type][slot_id] = (scheduled, remaining)
    
    def complete_subtask(self, scheduled):
        """完成子任务"""
        self.completed_tasks.append(scheduled)
        task = scheduled.task_node
        task.completed_runs += scheduled.sub_task.runs
        
        # 检查任务是否完全完成
        if task.completed_runs >= task.runs:
            task.completed = True
            
            # 计算产值
            if task.task_type in ["manufacturing", "reaction"]:
                value = task.runs * get_price(task.product_id)
                scheduled.value = value
                self.total_value += value
                print(f"  ✓ {task.task_type}: {task.product_name} ×{task.runs} ({value:,.0f} ISK)")
            else:
                print(f"  ✓ {task.task_type}: {task.product_name} ×{task.runs}")
    
    def run_simulation(self):
        """运行模拟"""
        print("\n" + "=" * 70)
        print("开始调度")
        print("=" * 70)
        
        # 调试：打印所有任务
        print(f"\n总任务数: {len(self.tasks)}")
        task_types = defaultdict(int)
        for task in self.tasks.values():
            task_types[task.task_type] += 1
        for task_type, count in task_types.items():
            print(f"  {task_type}: {count}")
        
        cycle = 0
        while cycle < 10000:
            cycle += 1
            ready = self.get_ready_tasks()
            
            if not ready:
                if all(t.completed for t in self.tasks.values()):
                    break
                has_running = any(self.slots[st][sid] for st in self.slots for sid in self.slots[st])
                if not has_running:
                    print(f"⚠️ 周期{cycle}: 无就绪任务且无运行任务")
                    # 调试：显示未完成的任务
                    incomplete = [t for t in self.tasks.values() if not t.completed]
                    if incomplete:
                        print(f"  未完成任务: {len(incomplete)}")
                        for t in incomplete[:3]:
                            print(f"    - {t.task_type} {t.product_name}: {t.completed_runs}/{t.runs} runs")
                            print(f"      依赖: {[self.tasks[d].completed for d in t.dependencies]}")
                    break
            else:
                scheduled_count = 0
                
                # 调试：显示就绪任务类型
                ready_types = defaultdict(int)
                for task in ready:
                    ready_types[task.task_type] += 1
                
                # 为每个就绪任务尝试调度
                for task in ready:
                    slot_type = self.get_slot_type(task.task_type)
                    free_slots = self.get_free_slots(slot_type)
                    
                    if not free_slots:
                        continue
                    
                    # 拆分任务
                    subtasks = self.split_task(task, MAX_TASK_DURATION_HOURS)
                    
                    # 调度子任务到空闲槽位
                    for subtask in subtasks:
                        if not free_slots:
                            break
                        slot_id = free_slots.pop(0)
                        self.schedule_subtask(task, subtask, slot_id)
                        scheduled_count += 1
                
                if scheduled_count > 0:
                    ready_info = ", ".join([f"{k}:{v}" for k, v in ready_types.items()])
                    print(f"\n周期{cycle} (时间{self.total_hours:.0f}h, 就绪{len(ready)}[{ready_info}], 调度{scheduled_count})")
            
            self.advance_time(DELIVERY_CYCLE_HOURS)
            
            self.advance_time(DELIVERY_CYCLE_HOURS)
        
        print("\n" + "=" * 70)
        print("模拟完成")
        print("=" * 70)
        self.print_summary()
    
    def print_summary(self):
        days = self.total_hours / 24
        print(f"\n📊 总结:")
        print(f"  时间: {self.total_hours:.0f}h ({days:.1f}天)")
        print(f"  任务: {len(self.completed_tasks)} 个子任务")
        
        # 按槽位类型统计
        slot_stats = defaultdict(int)
        for t in self.completed_tasks:
            slot_stats[t.slot_type] += 1
        
        print(f"\n  槽位统计:")
        for slot_type in ["research", "manufacturing", "reaction"]:
            count = slot_stats.get(slot_type, 0)
            print(f"    {slot_type}: {count}")
        
        # 按任务类型统计
        type_stats = defaultdict(int)
        for t in self.completed_tasks:
            type_stats[t.task_node.task_type] += 1
        
        print(f"\n  任务类型:")
        for task_type in ["copying", "invention", "manufacturing", "reaction"]:
            count = type_stats.get(task_type, 0)
            if count > 0:
                print(f"    {task_type}: {count}")
        
        print(f"\n  产值: {self.total_value:,.0f} ISK")
        if days > 0:
            print(f"  日产值: {self.total_value / days:,.0f} ISK/天")
    
    def export_to_json(self, filename):
        days = self.total_hours / 24
        
        # 统计导出的任务类型
        export_stats = defaultdict(int)
        for t in self.completed_tasks:
            export_stats[t.slot_type] += 1
        
        print(f"\n导出统计:")
        for slot_type, count in export_stats.items():
            print(f"  {slot_type}: {count} 个子任务")
        
        data = {
            "summary": {
                "total_hours": self.total_hours,
                "total_days": days,
                "completed_tasks": len(self.completed_tasks),
                "total_value": self.total_value,
                "average_daily_value": self.total_value / days if days > 0 else 0
            },
            "configuration": {
                "research_slots": RESEARCH_SLOTS,
                "manufacturing_slots": MANUFACTURING_SLOTS,
                "reaction_slots": REACTION_SLOTS,
                "delivery_cycle_hours": DELIVERY_CYCLE_HOURS
            },
            "tasks": [{
                "task_id": t.task_id,
                "task_type": t.task_node.task_type,
                "slot_type": t.slot_type,
                "slot_id": t.slot_id,
                "blueprint_name": t.task_node.blueprint_name,
                "product_name": t.task_node.product_name,
                "quantity": t.sub_task.runs,
                "duration_hours": t.sub_task.duration,
                "start_time": t.start_time,
                "end_time": t.end_time,
                "start_day": t.start_time / 24,
                "end_day": t.end_time / 24,
                "value": t.value
            } for t in self.completed_tasks]
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def export_to_csv(self, filename):
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "任务ID", "类别", "槽位ID", "任务类型", "蓝图名称", 
                "产物", "数量", "开始时间(天)", "实际结束时间(天)", 
                "交付时间(天)", "总时间(秒)", "实际结束时间(秒)", "交付时间(秒)",
                "合并任务数", "价值(ISK)"
            ])
            
            for t in self.completed_tasks:
                delivery_cycle_days = DELIVERY_CYCLE_HOURS / 24
                delivery_day = ((t.end_time / 24) // delivery_cycle_days + 1) * delivery_cycle_days
                
                writer.writerow([
                    t.task_id, t.slot_type, t.slot_id, t.task_node.task_type,
                    t.task_node.blueprint_name, t.task_node.product_name, t.sub_task.runs,
                    t.start_time / 24, t.end_time / 24, delivery_day,
                    t.sub_task.duration * 3600, t.end_time * 3600, delivery_day * 24 * 3600,
                    0, t.value
                ])

# ==================主程序==================
if __name__ == "__main__":
    # 规划
    planner = ProductionPlanner(inventory, blueprints_list)
    tasks = planner.plan_execution_targets(execution_targets)
    planner.build_dag()
    
    # 调度
    scheduler = SmartScheduler(tasks)
    scheduler.run_simulation()
    
    # 导出
    print(f"\n导出...")
    scheduler.export_to_json(SIMULATION_RESULT_JSON)
    scheduler.export_to_csv(SIMULATION_RESULT_CSV)
    print(f"✓ 完成")
    print(f"\n提示: python3 gantt_visualizer.py")
