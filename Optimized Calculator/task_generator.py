"""
任务列表生成器

从calculator的结果中读取蓝图执行次数，生成详细的生产任务列表。
包括：拷贝、发明、制造、反应等任务及其时间计算。
"""

import json
import csv
import yaml
import configparser
from pathlib import Path
from math import ceil
from collections import defaultdict
import sys

REPO_ROOT = next((p for p in [Path(__file__).resolve().parent, *Path(__file__).resolve().parent.parents] if (p / "config.ini").exists()), Path(__file__).resolve().parent)
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Utilities.name_mapping import load_types_map, get_name


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


# ================== 配置参数 ==================
class TaskConfig:
    """任务生成配置"""

    # 文件路径
    EXECUTION_CSV = "Results/execution_list.csv"
    BLUEPRINTS_YAML = _resolve_shared_path("blueprints_yaml", "Data/blueprints.yaml")
    BLUEPRINTS_JSON = "Source/blueprints_merged.json"  # 用于获取蓝图名称映射
    T2_JSON = "Source/T2.json"
    TYPES_JSON = _resolve_shared_path("types_json", "Data/types.json")

    OUTPUT_CSV = "Results/task_list.csv"
    OUTPUT_SUMMARY_CSV = "Results/task_summary.csv"

    # 时间效率系数（越小越快，1.0表示无加成）
    COPY_TIME_MODIFIER = 0.8  # 拷贝时间效率（例如：0.8表示80%时间，即快20%）
    INVENTION_TIME_MODIFIER = 0.75  # 发明时间效率
    MANUFACTURING_TIME_MODIFIER = 0.9  # 制造时间效率（考虑ME研究）
    REACTION_TIME_MODIFIER = 1.0  # 反应时间效率

    # 发明产物默认属性
    INVENTION_OUTPUT_ME = 2  # 发明产物材料效率
    INVENTION_OUTPUT_TE = 4  # 发明产物时间效率
    INVENTION_OUTPUT_RUNS = 1  # 发明产物流程数（通常是1）

    # T2制造时间效率计算方式
    # TE4的蓝图，制造时间 = base_time * (1 - 0.04 * 4) = base_time * 0.84
    T2_MANUFACTURING_TE_MODIFIER = 1 - (0.04 * INVENTION_OUTPUT_TE)

    # 其他选项
    INCLUDE_SKILLS = False  # 是否在输出中包含技能要求


# ================== 辅助函数 ==================

def load_execution_list(csv_path):
    """
    从execution_list.csv读取蓝图执行次数
    返回: {blueprint_name: runs}
    """
    executions = {}
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            for row in reader:
                if len(row) >= 2:
                    bp_name = row[0]
                    runs = int(row[1])
                    executions[bp_name] = runs
    except FileNotFoundError:
        print(f"警告：未找到文件 {csv_path}")

    return executions


def load_blueprints_yaml(yaml_path):
    """
    加载blueprints.yaml
    返回: {blueprintTypeID: blueprint_data}
    """
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            blueprints = yaml.safe_load(f)
        return blueprints
    except FileNotFoundError:
        print(f"错误：未找到文件 {yaml_path}")
        return {}


def load_blueprints_json(json_path):
    """加载blueprints_merged.json以获取蓝图ID映射"""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            blueprints = json.load(f)
        return blueprints
    except FileNotFoundError:
        print(f"警告：未找到文件 {json_path}")
        return []


def load_t2_list(t2_json):
    """
    加载T2蓝图列表
    """
    try:
        with open(t2_json, "r", encoding="utf-8") as f:
            t2_data = json.load(f)
        return t2_data
    except FileNotFoundError:
        print(f"警告：未找到文件 {t2_json}，假设所有蓝图都不是T2")
        return []


def find_blueprint_by_name(bp_name, blueprints_json, types_map):
    """
    根据蓝图名称查找对应的blueprintTypeID
    """
    for bp in blueprints_json:
        bp_id = bp.get("blueprintTypeID")
        if bp_id:
            name = get_name(bp_id, types_map)["zh"]
            if name == bp_name:
                return bp_id
    return None


def format_time(seconds):
    """将秒数格式化为易读的时间"""
    if seconds < 60:
        return f"{seconds:.0f}秒"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}分钟"
    elif seconds < 86400:
        hours = seconds / 3600
        return f"{hours:.1f}小时"
    else:
        days = seconds / 86400
        return f"{days:.1f}天"


# ================== 任务生成器 ==================
class TaskGenerator:
    """生成生产任务列表"""

    def __init__(self, config):
        self.config = config
        self.types_map = load_types_map(config.TYPES_JSON)
        self.blueprints_yaml = load_blueprints_yaml(config.BLUEPRINTS_YAML)
        self.blueprints_json = load_blueprints_json(config.BLUEPRINTS_JSON)
        self.t2_set = load_t2_list(config.T2_JSON)
        self.executions = load_execution_list(config.EXECUTION_CSV)
        self.tasks = []

    def generate_tasks(self):
        """生成所有任务"""
        print(f"开始生成任务列表...")
        print(f"读取到 {len(self.executions)} 个蓝图执行记录")

        for bp_name, runs in self.executions.items():
            bp_id = find_blueprint_by_name(bp_name, self.blueprints_json, self.types_map)

            if bp_id is None:
                print(f"警告：未找到蓝图 '{bp_name}' 的ID")
                continue

            if bp_id not in self.blueprints_yaml:
                print(f"警告：blueprints.yaml中未找到蓝图ID {bp_id}")
                continue

            bp_data = self.blueprints_yaml[bp_id]
            activities = bp_data.get("activities", {})

            # 判断活动类型并生成任务
            if "manufacturing" in activities:
                self._generate_manufacturing_tasks(bp_id, bp_name, runs, activities, bp_data)
            elif "reaction" in activities:
                self._generate_reaction_tasks(bp_id, bp_name, runs, activities)

        print(f"生成了 {len(self.tasks)} 个任务")
        return self.tasks

    def _generate_manufacturing_tasks(self, bp_id, bp_name, runs, activities, bp_data):
        """生成制造相关任务"""
        is_t2 = False
        t1_bp_id = None
        t1_bp_name = None
        invention = None
        
        # 检查是否是T2蓝图
        for item in self.t2_set:
            if item[0] == bp_id:
                is_t2 = True
                t1_bp_id = item[1]  # T1蓝图ID
                t1_bp_name = get_name(t1_bp_id, self.types_map)["zh"]
                
                # 获取T1蓝图的发明活动
                if t1_bp_id in self.blueprints_yaml:
                    t1 = self.blueprints_yaml[t1_bp_id]
                    t1_activities = t1.get("activities", {})
                    invention = t1_activities.get("invention", {})
                break
        
        manufacturing = activities.get("manufacturing", {})
        copying = activities.get("copying", {})

        base_manufacturing_time = manufacturing.get("time", 0)
        copy_time = copying.get("time", 0)

        if is_t2:
            # T2蓝图：需要 拷贝T1 + 发明 + 制造T2
            
            if not invention:
                print(f"警告：T2蓝图 {bp_name} 的T1蓝图 {t1_bp_name} 没有发明活动")
                return

            # 1. 拷贝T1蓝图任务（用于发明）
            # 获取T1蓝图的拷贝时间
            if t1_bp_id in self.blueprints_yaml:
                t1_copying = self.blueprints_yaml[t1_bp_id].get("activities", {}).get("copying", {})
                t1_copy_time = t1_copying.get("time", 0)
                
                if t1_copy_time > 0:
                    # 需要多少个发明尝试
                    probability = self._get_invention_probability(invention)
                    invention_attempts = ceil(runs / probability) if probability > 0 else runs

                    # 拷贝T1蓝图任务数 = 发明尝试次数
                    copy_task_time = t1_copy_time * invention_attempts * self.config.COPY_TIME_MODIFIER

                    self.tasks.append({
                        "蓝图名称": t1_bp_name,  # ← 修复：拷贝T1蓝图而非T2
                        "任务类型": "拷贝",
                        "数量": invention_attempts,
                        "单次时间(秒)": t1_copy_time,
                        "时间效率": self.config.COPY_TIME_MODIFIER,
                        "总时间(秒)": copy_task_time,
                        "总时间(格式化)": format_time(copy_task_time),
                        "备注": f"拷贝T1蓝图用于发明T2蓝图{bp_name}"
                    })

                    # 2. 发明任务
                    invention_time = invention.get("time", 0)
                    if invention_time > 0:
                        invention_task_time = invention_time * invention_attempts * self.config.INVENTION_TIME_MODIFIER

                        self.tasks.append({
                            "蓝图名称": bp_name,  # T2蓝图名称
                            "任务类型": "发明",
                            "数量": invention_attempts,
                            "单次时间(秒)": invention_time,
                            "时间效率": self.config.INVENTION_TIME_MODIFIER,
                            "总时间(秒)": invention_task_time,
                            "总时间(格式化)": format_time(invention_task_time),
                            "备注": f"成功率{probability:.1%}, 预计产出{runs}流程蓝图"
                        })

            # 3. T2制造任务（使用发明产物的TE加成）
            manufacturing_time_modifier = self.config.T2_MANUFACTURING_TE_MODIFIER * self.config.MANUFACTURING_TIME_MODIFIER
            manufacturing_task_time = base_manufacturing_time * runs * manufacturing_time_modifier

            self.tasks.append({
                "蓝图名称": bp_name,
                "任务类型": "制造",
                "数量": runs,
                "单次时间(秒)": base_manufacturing_time,
                "时间效率": manufacturing_time_modifier,
                "总时间(秒)": manufacturing_task_time,
                "总时间(格式化)": format_time(manufacturing_task_time),
                "备注": f"T2制造，TE{self.config.INVENTION_OUTPUT_TE}"
            })

        else:
            # T1蓝图：需要 拷贝 + 制造

            # 1. 拷贝任务
            if copy_time > 0:
                copy_task_time = copy_time * runs * self.config.COPY_TIME_MODIFIER

                self.tasks.append({
                    "蓝图名称": bp_name,
                    "任务类型": "拷贝",
                    "数量": runs,
                    "单次时间(秒)": copy_time,
                    "时间效率": self.config.COPY_TIME_MODIFIER,
                    "总时间(秒)": copy_task_time,
                    "总时间(格式化)": format_time(copy_task_time),
                    "备注": "T1蓝图拷贝"
                })

            # 2. 制造任务
            manufacturing_task_time = base_manufacturing_time * runs * self.config.MANUFACTURING_TIME_MODIFIER

            self.tasks.append({
                "蓝图名称": bp_name,
                "任务类型": "制造",
                "数量": runs,
                "单次时间(秒)": base_manufacturing_time,
                "时间效率": self.config.MANUFACTURING_TIME_MODIFIER,
                "总时间(秒)": manufacturing_task_time,
                "总时间(格式化)": format_time(manufacturing_task_time),
                "备注": "T1制造"
            })

    def _generate_reaction_tasks(self, bp_id, bp_name, runs, activities):
        """生成反应任务"""
        reaction = activities.get("reaction", {})
        reaction_time = reaction.get("time", 0)

        if reaction_time > 0:
            reaction_task_time = reaction_time * runs * self.config.REACTION_TIME_MODIFIER

            self.tasks.append({
                "蓝图名称": bp_name,
                "任务类型": "反应",
                "数量": runs,
                "单次时间(秒)": reaction_time,
                "时间效率": self.config.REACTION_TIME_MODIFIER,
                "总时间(秒)": reaction_task_time,
                "总时间(格式化)": format_time(reaction_task_time),
                "备注": "反应配方"
            })

    def _get_invention_probability(self, invention):
        """获取发明成功率"""
        products = invention.get("products", [])
        if products:
            # 取第一个产物的概率
            return products[0].get("probability", 0.3)
        return 0.3  # 默认30%

    def save_tasks(self):
        """保存任务列表到CSV"""
        if not self.tasks:
            print("没有任务可保存")
            return

        # 保存详细任务列表
        with open(self.config.OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["蓝图名称", "任务类型", "数量", "单次时间(秒)",
                          "时间效率", "总时间(秒)", "总时间(格式化)", "备注"]
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            writer.writerows(self.tasks)

        print(f"任务列表已保存到: {self.config.OUTPUT_CSV}")

        # 生成汇总统计
        self._save_summary()

    def _save_summary(self):
        """生成并保存任务汇总"""
        summary = defaultdict(lambda: {"数量": 0, "总时间": 0})

        for task in self.tasks:
            task_type = task["任务类型"]
            summary[task_type]["数量"] += task["数量"]
            summary[task_type]["总时间"] += task["总时间(秒)"]

        # 保存汇总
        with open(self.config.OUTPUT_SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(["任务类型", "任务数量", "总时间(秒)", "总时间(格式化)", "占比"])

            total_time = sum(data["总时间"] for data in summary.values())

            for task_type, data in sorted(summary.items()):
                percentage = (data["总时间"] / total_time * 100) if total_time > 0 else 0
                writer.writerow([
                    task_type,
                    data["数量"],
                    f"{data['总时间']:.0f}",
                    format_time(data["总时间"]),
                    f"{percentage:.1f}%"
                ])

            # 总计
            writer.writerow([
                "总计",
                sum(data["数量"] for data in summary.values()),
                f"{total_time:.0f}",
                format_time(total_time),
                "100.0%"
            ])

        print(f"任务汇总已保存到: {self.config.OUTPUT_SUMMARY_CSV}")

        # 打印汇总
        print("\n" + "=" * 80)
        print("任务汇总")
        print("=" * 80)
        for task_type, data in sorted(summary.items()):
            percentage = (data["总时间"] / total_time * 100) if total_time > 0 else 0
            print(f"{task_type:10s} | 任务数: {data['数量']:6d} | "
                  f"时间: {format_time(data['总时间']):15s} | {percentage:5.1f}%")
        print("-" * 80)
        print(f"{'总计':10s} | 任务数: {sum(data['数量'] for data in summary.values()):6d} | "
              f"时间: {format_time(total_time):15s}")
        print("=" * 80)


# ================== 主程序 ==================
def main():
    """主函数"""
    print("=" * 80)
    print("EVE 生产任务列表生成器")
    print("=" * 80)
    print()

    # 初始化配置
    config = TaskConfig()

    print("配置参数:")
    print(f"  拷贝时间效率: {config.COPY_TIME_MODIFIER:.0%}")
    print(f"  发明时间效率: {config.INVENTION_TIME_MODIFIER:.0%}")
    print(f"  制造时间效率: {config.MANUFACTURING_TIME_MODIFIER:.0%}")
    print(f"  反应时间效率: {config.REACTION_TIME_MODIFIER:.0%}")
    print(
        f"  发明产物: ME{config.INVENTION_OUTPUT_ME}/TE{config.INVENTION_OUTPUT_TE}/{config.INVENTION_OUTPUT_RUNS}流程")
    print()

    # 生成任务
    generator = TaskGenerator(config)
    tasks = generator.generate_tasks()

    # 保存结果
    if tasks:
        generator.save_tasks()
        print()
        print("✅ 任务列表生成完成！")
    else:
        print()
        print("⚠️ 未生成任何任务，请检查输入文件")


if __name__ == "__main__":
    main()
