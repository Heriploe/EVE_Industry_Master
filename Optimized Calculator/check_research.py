"""
科研任务测试脚本
检查scheduler是否生成科研任务，以及CSV/JSON是否包含research数据
"""

import json
import csv
import os

def check_simulation_results():
    """检查模拟结果"""
    print("=" * 70)
    print("科研任务检查")
    print("=" * 70)
    
    # 检查CSV
    csv_file = "Results/simulation_result.csv"
    json_file = "Results/simulation_result.json"
    
    if not os.path.exists(csv_file):
        print(f"❌ 找不到 {csv_file}")
        print("   请先运行: python3 task_scheduler.py")
        return False
    
    print(f"\n1. 检查CSV文件...")
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"   总任务数: {len(rows)}")
    
    # 统计类别
    categories = {}
    for row in rows:
        cat = row['类别']
        categories[cat] = categories.get(cat, 0) + 1
    
    print(f"\n   类别统计:")
    for cat, count in sorted(categories.items()):
        print(f"     {cat}: {count}")
    
    has_research = 'research' in categories
    
    if has_research:
        print(f"\n   ✅ CSV中包含research任务")
        # 显示几个research任务示例
        print(f"\n   Research任务示例:")
        research_tasks = [r for r in rows if r['类别'] == 'research']
        for task in research_tasks[:3]:
            print(f"     - {task['任务类型']}: {task['产物']} (槽位{task['槽位ID']})")
    else:
        print(f"\n   ❌ CSV中没有research任务")
    
    # 检查JSON
    print(f"\n2. 检查JSON文件...")
    if not os.path.exists(json_file):
        print(f"   ❌ 找不到 {json_file}")
        return False
    
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    tasks = data.get('tasks', [])
    print(f"   总任务数: {len(tasks)}")
    
    # 统计slot_type
    slot_types = {}
    for task in tasks:
        slot_type = task.get('slot_type', 'unknown')
        slot_types[slot_type] = slot_types.get(slot_type, 0) + 1
    
    print(f"\n   槽位类型统计:")
    for slot_type, count in sorted(slot_types.items()):
        print(f"     {slot_type}: {count}")
    
    has_research_json = 'research' in slot_types
    
    if has_research_json:
        print(f"\n   ✅ JSON中包含research任务")
        # 显示几个research任务示例
        print(f"\n   Research任务示例:")
        research_tasks = [t for t in tasks if t.get('slot_type') == 'research']
        for task in research_tasks[:3]:
            print(f"     - {task['task_type']}: {task['product_name']} (槽位{task['slot_id']})")
    else:
        print(f"\n   ❌ JSON中没有research任务")
    
    # 检查配置
    print(f"\n3. 检查配置...")
    config = data.get('configuration', {})
    print(f"   research_slots: {config.get('research_slots', 0)}")
    print(f"   manufacturing_slots: {config.get('manufacturing_slots', 0)}")
    print(f"   reaction_slots: {config.get('reaction_slots', 0)}")
    
    # 总结
    print(f"\n" + "=" * 70)
    print("诊断结果")
    print("=" * 70)
    
    if has_research and has_research_json:
        print("✅ CSV和JSON都包含research任务")
        print("   如果甘特图没有显示，可能是gantt_visualizer的问题")
        return True
    elif not has_research and not has_research_json:
        print("❌ CSV和JSON都没有research任务")
        print("   问题在scheduler - 没有生成科研任务")
        print("\n可能原因:")
        print("  1. execution_list中的产物不需要拷贝/发明")
        print("  2. 蓝图库存充足，不需要拷贝")
        print("  3. 规划阶段有bug")
        return False
    else:
        print("⚠️ CSV和JSON的数据不一致")
        return False

def suggest_solutions():
    """给出解决建议"""
    print("\n" + "=" * 70)
    print("解决方案")
    print("=" * 70)
    
    print("\n方案1: 检查是否有需要科研的任务")
    print("  python3 -c \"")
    print("  import csv")
    print("  with open('Results/execution_list_filtered.csv') as f:")
    print("      reader = csv.reader(f, delimiter='\\\\t')")
    print("      for row in reader:")
    print("          if 'II' in row[0] or '蓝图' in row[0]:")
    print("              print(row)")
    print("  \"")
    
    print("\n方案2: 手动添加测试任务到execution_list_filtered.csv")
    print("  echo '护卫舰蓝图\\t10' >> Results/execution_list_filtered.csv")
    print("  echo '小型护盾增效器 II\\t50' >> Results/execution_list_filtered.csv")
    
    print("\n方案3: 检查蓝图库存（如果runs充足则不会拷贝）")
    print("  python3 -c \"")
    print("  import json")
    print("  with open('Inventory/blueprints.json') as f:")
    print("      bps = json.load(f)")
    print("  print(f'总蓝图数: {len(bps)}')")
    print("  for bp in bps[:5]:")
    print("      print(f\\\"{bp.get('zh', '?')}: {bp.get('runs', -1)} runs\\\")")
    print("  \"")
    
    print("\n方案4: 清空蓝图库存，强制触发拷贝")
    print("  python3 -c \"")
    print("  import json")
    print("  with open('Inventory/blueprints.json') as f:")
    print("      bps = json.load(f)")
    print("  # 将所有拷贝的runs设为0")
    print("  for bp in bps:")
    print("      if bp.get('is_blueprint_copy', False):")
    print("          bp['runs'] = 0")
    print("  with open('Inventory/blueprints.json', 'w') as f:")
    print("      json.dump(bps, f, ensure_ascii=False, indent=2)")
    print("  \"")
    
    print("\n方案5: 重新运行完整流程")
    print("  python3 task_scheduler.py")
    print("  python3 gantt_visualizer.py")

if __name__ == "__main__":
    has_research = check_simulation_results()
    
    if not has_research:
        suggest_solutions()
    else:
        print("\n✅ 科研任务数据正常")
        print("如果甘特图仍然没有显示，请检查gantt_visualizer.py")
