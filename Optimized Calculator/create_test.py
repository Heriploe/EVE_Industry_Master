"""
最小测试用例 - 验证科研任务调度
"""

import json
import csv

# 创建最小的测试文件
def create_test_files():
    """创建测试用的最小文件"""
    
    # 1. 创建execution_list_filtered.csv（包含需要拷贝的任务）
    with open("Results/execution_list_filtered.csv", "w", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        # 添加一个需要拷贝的蓝图
        writer.writerow(["小型护盾增效器蓝图", "10"])
    
    print("✓ 创建 Results/execution_list_filtered.csv")
    
    # 2. 修改blueprints.json，设置runs=0强制拷贝
    try:
        with open("Inventory/blueprints.json", "r", encoding="utf-8") as f:
            bps = json.load(f)
        
        # 将所有拷贝的runs设为0
        for bp in bps:
            if bp.get("is_blueprint_copy", False):
                bp["runs"] = 0
        
        with open("Inventory/blueprints.json", "w", encoding="utf-8") as f:
            json.dump(bps, f, ensure_ascii=False, indent=2)
        
        print("✓ 修改 Inventory/blueprints.json (所有拷贝runs=0)")
    except Exception as e:
        print(f"⚠️ 修改blueprints.json失败: {e}")
    
    print("\n现在运行:")
    print("  python3 task_scheduler.py")
    print("\n应该看到:")
    print("  📋 拷贝: ... (X次)")
    print("  ✓ copying: ... ×N")

if __name__ == "__main__":
    create_test_files()
