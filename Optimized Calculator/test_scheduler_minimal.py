"""最小化scheduler测试 - 验证甘特图是否能正确显示research"""
import json
import csv
import os

# 确保目录存在
os.makedirs("Results", exist_ok=True)

# 模拟数据
class Task:
    def __init__(self, tid, ttype, slot_type):
        self.task_id = tid
        self.task_type = ttype
        self.slot_type = slot_type
        self.product_name = f"{ttype}产物{tid}"
        self.runs = 10
        self.duration = 10.0

# 创建测试任务（包含所有三种类型）
tasks = [
    Task(1, "copying", "research"),
    Task(2, "copying", "research"),
    Task(3, "invention", "research"),
    Task(4, "manufacturing", "manufacturing"),
    Task(5, "manufacturing", "manufacturing"),
    Task(6, "reaction", "reaction"),
]

print("=" * 70)
print("最小测试 - 创建包含research任务的测试数据")
print("=" * 70)

print("\n创建测试任务:")
for t in tasks:
    print(f"  {t.task_id}: {t.task_type} → {t.slot_type}")

# 导出CSV
csv_file = "Results/simulation_result.csv"
with open(csv_file, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([
        "任务ID", "类别", "槽位ID", "任务类型", "蓝图名称", 
        "产物", "数量", "开始时间(天)", "实际结束时间(天)", 
        "交付时间(天)", "总时间(秒)", "实际结束时间(秒)", "交付时间(秒)",
        "合并任务数", "价值(ISK)"
    ])
    
    time = 0
    for i, t in enumerate(tasks):
        start_day = time
        end_day = start_day + t.duration / 24
        delivery_day = int(end_day) + 1
        
        writer.writerow([
            t.task_id, 
            t.slot_type,  # 类别
            i % 3,  # 槽位ID
            t.task_type,  # 任务类型
            f"蓝图{t.task_id}", 
            t.product_name, 
            t.runs,
            start_day, 
            end_day, 
            delivery_day,
            t.duration * 3600,
            end_day * 24 * 3600,
            delivery_day * 24 * 3600,
            0, 
            1000000 if t.task_type in ["manufacturing", "reaction"] else 0
        ])
        time += t.duration / 24

print(f"\n✓ 创建 {csv_file}")

# 验证CSV
import pandas as pd
df = pd.read_csv(csv_file)
categories = df['类别'].unique()
print(f"\nCSV类别: {list(categories)}")
for cat in categories:
    count = len(df[df['类别'] == cat])
    print(f"  {cat}: {count} 个任务")

# 导出JSON
json_file = "Results/simulation_result.json"
data = {
    "summary": {
        "total_hours": 60,
        "total_days": 2.5,
        "completed_tasks": len(tasks),
        "total_value": 2000000,
        "average_daily_value": 800000
    },
    "configuration": {
        "research_slots": 9,
        "manufacturing_slots": 9,
        "reaction_slots": 9,
        "delivery_cycle_hours": 24
    },
    "tasks": [
        {
            "task_id": t.task_id,
            "task_type": t.task_type,
            "slot_type": t.slot_type,
            "slot_id": i % 3,
            "blueprint_name": f"蓝图{t.task_id}",
            "product_name": t.product_name,
            "quantity": t.runs,
            "duration_hours": t.duration,
            "start_time": i * 10,
            "end_time": (i + 1) * 10,
            "start_day": i * 10 / 24,
            "end_day": (i + 1) * 10 / 24,
            "value": 1000000 if t.task_type in ["manufacturing", "reaction"] else 0
        }
        for i, t in enumerate(tasks)
    ]
}

with open(json_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"✓ 创建 {json_file}")

# 验证JSON
slot_types = {}
for task in data['tasks']:
    st = task['slot_type']
    slot_types[st] = slot_types.get(st, 0) + 1

print(f"\nJSON槽位类型: {slot_types}")

print("\n" + "=" * 70)
print("测试数据已创建")
print("=" * 70)

print("\n现在运行:")
print("  python3 gantt_visualizer.py")

print("\n应该生成包含以下内容的甘特图:")
print("  - research子图 (包含copying和invention)")
print("  - manufacturing子图")
print("  - reaction子图")

print("\n如果甘特图正常显示research子图，说明gantt_visualizer工作正常")
print("问题可能在task_scheduler的规划或调度阶段")
