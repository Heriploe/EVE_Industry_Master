"""
甘特图可视化工具 - 基于matplotlib
读取simulation_result.csv生成甘特图
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import json
from chinese_font_config import setup_chinese_font

# 初始化中文字体
try:
    setup_chinese_font()
except:
    print("⚠️ 中文字体配置失败，使用默认字体")

class GanttVisualizer:
    def __init__(self, csv_file, json_file):
        self.csv_file = csv_file
        self.json_file = json_file
        
        # 加载数据
        self.df = pd.read_csv(csv_file, encoding='utf-8')
        with open(json_file, "r", encoding="utf-8") as f:
            self.json_data = json.load(f)
        
        print(f"✅ 加载了 {len(self.df)} 个任务")
    
    def generate_gantt_chart(self, output_png):
        """生成甘特图"""
        print("生成甘特图...")
        
        # 获取类别
        categories = sorted(self.df['类别'].unique())
        
        # 颜色映射
        color_map = {
            'research': '#9b59b6',
            'manufacturing': '#2ecc71',
            'reaction': '#e74c3c'
        }
        
        # 创建子图
        fig, axes = plt.subplots(len(categories), 1, figsize=(200, 6 * len(categories)))
        if len(categories) == 1:
            axes = [axes]
        
        for ax, category in zip(axes, categories):
            cat_df = self.df[self.df['类别'] == category].sort_values('槽位ID')
            max_slot = cat_df['槽位ID'].max() + 1
            
            for _, row in cat_df.iterrows():
                start_day = row['开始时间(天)']
                end_day = row['实际结束时间(天)']
                delivery_day = row['交付时间(天)']
                y_pos = row['槽位ID']
                color = color_map.get(row['类别'], '#95a5a6')
                
                # 工作时间（实心条）
                work_duration = end_day - start_day
                ax.barh(y_pos, work_duration, left=start_day, height=0.8,
                       color=color, alpha=0.8, edgecolor='black', linewidth=0.5)
                
                # 等待交付时间（浅色条带斜纹）
                wait_duration = delivery_day - end_day
                if wait_duration > 0.01:
                    ax.barh(y_pos, wait_duration, left=end_day, height=0.8,
                           color=color, alpha=0.3, edgecolor='gray', linewidth=0.5,
                           hatch='///')
                
                # 添加任务标签
                task_label = row['产物'][:20] if len(row['产物']) > 20 else row['产物']
                if work_duration > 0.1:
                    ax.text(start_day + work_duration / 2, y_pos, task_label,
                           ha='center', va='center', fontsize=8, color='white', weight='bold')
            
            # 交付周期线
            max_time = self.df['交付时间(天)'].max()
            delivery_cycle_days = self.json_data['configuration']['delivery_cycle_hours'] / 24
            
            cycle_time = 0
            while cycle_time <= max_time + delivery_cycle_days:
                ax.axvline(x=cycle_time, color='red', linestyle='--', linewidth=1.5, alpha=0.5, zorder=1)
                cycle_time += delivery_cycle_days
            
            ax.set_ylim(-0.5, max_slot - 0.5)
            ax.set_yticks(range(max_slot))
            ax.set_yticklabels([f'槽位{i}' for i in range(max_slot)])
            ax.invert_yaxis()
            ax.set_xlabel('时间 (天)', fontsize=11)
            ax.set_ylabel('槽位', fontsize=11)
            ax.set_title(f'{category} 任务甘特图', fontsize=13, weight='bold')
            ax.grid(True, alpha=0.3, axis='x')
        
        # 图例
        legend_elements = []
        for cat_type, color in color_map.items():
            legend_elements.append(
                mpatches.Patch(facecolor=color, edgecolor='black', label=cat_type, alpha=0.8)
            )
        legend_elements.append(
            mpatches.Patch(facecolor='gray', edgecolor='gray', label='等待交付', alpha=0.3, hatch='///')
        )
        legend_elements.append(
            plt.Line2D([0], [0], color='red', linestyle='--', linewidth=1.5, label='交付周期', alpha=0.5)
        )
        
        fig.legend(handles=legend_elements, loc='upper right', ncol=5, fontsize=10)
        
        # 总标题
        summary = self.json_data['summary']
        title = f"EVE Online 生产任务甘特图 | 总任务: {summary['completed_tasks']} | 总产值: {summary['total_value']:,.0f} ISK | 日产值: {summary['average_daily_value']:,.0f} ISK"
        fig.suptitle(title, fontsize=14, weight='bold', y=0.995)
        
        plt.tight_layout(rect=[0, 0, 1, 0.99])
        plt.savefig(output_png, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"✅ 甘特图已保存到: {output_png}")
    
    def generate_statistics_chart(self, output_png):
        """生成统计图表"""
        print("生成统计图表...")
        
        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        
        # 1. 各类别任务数量
        category_counts = self.df['类别'].value_counts()
        colors = ['#9b59b6', '#2ecc71', '#e74c3c']
        axes[0, 0].pie(category_counts.values, labels=category_counts.index,
                      autopct='%1.1f%%', colors=colors, startangle=90)
        axes[0, 0].set_title('各类别任务数量分布', fontsize=14, weight='bold')
        
        # 2. 各类别总产值
        category_value = self.df.groupby('类别')['价值(ISK)'].sum() / 1_000_000
        
        bars = axes[0, 1].bar(range(len(category_value)), category_value.values,
                             color=colors[:len(category_value)], alpha=0.8)
        axes[0, 1].set_xticks(range(len(category_value)))
        axes[0, 1].set_xticklabels(category_value.index)
        axes[0, 1].set_ylabel('产值 (M ISK)')
        axes[0, 1].set_title('各类别总产值', fontsize=14, weight='bold')
        axes[0, 1].grid(True, alpha=0.3, axis='y')
        
        for bar in bars:
            height = bar.get_height()
            axes[0, 1].text(bar.get_x() + bar.get_width()/2, height,
                          f'{height:.0f}M', ha='center', va='bottom', fontsize=9)
        
        # 3. 任务类型分布
        type_counts = self.df['任务类型'].value_counts()
        type_colors = ['#3498db', '#e74c3c']
        axes[1, 0].pie(type_counts.values, labels=type_counts.index,
                      autopct='%1.1f%%', colors=type_colors, startangle=90)
        axes[1, 0].set_title('任务类型分布', fontsize=14, weight='bold')
        
        # 4. 时间线图
        timeline_df = self.df.sort_values('开始时间(天)')
        cumulative_value = timeline_df['价值(ISK)'].cumsum() / 1_000_000
        axes[1, 1].plot(timeline_df['开始时间(天)'], cumulative_value,
                       color='#2ecc71', linewidth=2, marker='o', markersize=3)
        axes[1, 1].set_xlabel('时间 (天)')
        axes[1, 1].set_ylabel('累计产值 (M ISK)')
        axes[1, 1].set_title('累计产值时间线', fontsize=14, weight='bold')
        axes[1, 1].grid(True, alpha=0.3)
        
        summary = self.json_data['summary']
        plt.suptitle(f"EVE Online 生产统计 | 周期: {summary['total_days']:.1f}天 | 总产值: {summary['total_value']:,.0f} ISK",
                    fontsize=16, weight='bold')
        plt.tight_layout(rect=[0, 0, 1, 0.97])
        plt.savefig(output_png, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"✅ 统计图表已保存到: {output_png}")

def main():
    csv_file = "Results/simulation_result.csv"
    json_file = "Results/simulation_result.json"
    
    try:
        visualizer = GanttVisualizer(csv_file, json_file)
        visualizer.generate_gantt_chart("Results/gantt_chart.png")
        visualizer.generate_statistics_chart("Results/statistics_chart.png")
        print("\n✅ 图表生成完成！")
    except FileNotFoundError as e:
        print(f"❌ 错误: 找不到文件 {e.filename}")
        print("请先运行: python3 task_scheduler.py")
    except Exception as e:
        print(f"❌ 错误: {e}")

if __name__ == "__main__":
    main()
