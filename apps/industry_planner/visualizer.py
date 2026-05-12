"""
EVE Industry Visualizer
=======================
读取 eve_industry_planner.py 输出的 JSON 文件，生成完整可视化报告。

使用方法:
    python eve_visualizer.py [--result eve_plan_result.json] [--out-dir charts/]

依赖:
    pip install matplotlib numpy
"""

import json
import argparse
import math
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as ticker
import numpy as np

# ── 字体初始化 ────────────────────────────────────────────────────────────
# 强制重建 matplotlib 字体缓存，确保系统 CJK 字体被识别
import matplotlib.font_manager as _fm
_fm._load_fontmanager(try_read_cache=False)

# 按优先级注册字体文件（直接用文件路径，绕过缓存名称匹配问题）
_CJK_FILE_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]
_CJK_FONT_PROP = None   # FontProperties with direct file path (最可靠)
_registered_names = []  # 成功注册的字体名列表

for _fp in _CJK_FILE_CANDIDATES:
    if Path(_fp).exists():
        try:
            _fm.fontManager.addfont(_fp)
            _prop = _fm.FontProperties(fname=_fp)
            _name = _prop.get_name()
            if _name not in _registered_names:
                _registered_names.append(_name)
            if _CJK_FONT_PROP is None:
                _CJK_FONT_PROP = _prop   # 保存第一个成功的用于直接渲染
        except Exception:
            continue

# rcParams 字体族：已注册名 + 系统中已知存在的名（去重）
_known_system = ["WenQuanYi Zen Hei", "Noto Sans CJK JP", "IPAGothic", "DejaVu Sans"]
_font_list = _registered_names + [f for f in _known_system if f not in _registered_names]

matplotlib.rcParams["font.family"]        = _font_list
matplotlib.rcParams["axes.unicode_minus"] = False

# ── 配色 ──────────────────────────────────────────────────────────────────
C = {
    "bg":      "#020810",
    "bg2":     "#0a1628",
    "panel":   "#0d1f3c",
    "border":  "#1a3a6a",
    "accent":  "#00d4ff",
    "gold":    "#ffc857",
    "red":     "#ff4444",
    "green":   "#00ff88",
    "purple":  "#a855f7",
    "orange":  "#ff8c00",
    "text":    "#c8d8e8",
    "dim":     "#5a7a9a",
}

ACT_COLOR = {
    "manufacturing": C["accent"],
    "reaction":      C["orange"],
    "invention":     C["purple"],
    "copying":       C["green"],
}

def style_ax(ax, title="", xlabel="", ylabel=""):
    ax.set_facecolor(C["bg2"])
    ax.spines["bottom"].set_color(C["border"])
    ax.spines["left"].set_color(C["border"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(colors=C["text"], labelsize=8)
    ax.xaxis.label.set_color(C["text"])
    ax.yaxis.label.set_color(C["text"])
    if title:
        ax.set_title(title, color=C["accent"], fontsize=10, pad=8, fontweight="bold")
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=8)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8)
    ax.grid(color=C["border"], linewidth=0.4, alpha=0.6)


def set_fig_dark(fig):
    fig.patch.set_facecolor(C["bg"])


# ── 1. BOM 物料缺口图 ─────────────────────────────────────────────────────

def plot_bom_gaps(result: dict, out_dir: Path):
    raw = result["bom"]["raw_materials"]
    lacking = [r for r in raw if not r["sufficient"]]
    if not lacking:
        print("  [BOM] 无缺口物料，跳过")
        return

    lacking = lacking[:30]  # 最多显示30种
    names  = [r["name"][:18] for r in lacking]
    need   = [r["need"]  for r in lacking]
    have   = [r["have"]  for r in lacking]
    lack   = [r["lack"]  for r in lacking]

    fig, axes = plt.subplots(1, 2, figsize=(16, max(5, len(lacking) * 0.38 + 2)))
    set_fig_dark(fig)
    fig.suptitle("原材料缺口分析", color=C["gold"], fontsize=14, fontweight="bold")

    # 左图：水平条形图
    ax = axes[0]
    style_ax(ax, title=f"缺口物料 TOP{len(lacking)} (红=缺口 蓝=库存)")
    y = np.arange(len(lacking))
    ax.barh(y, need, color=C["red"],    alpha=0.7, height=0.7, label="需求")
    ax.barh(y, have, color=C["accent"], alpha=0.8, height=0.7, label="库存")
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.legend(loc="lower right", facecolor=C["panel"], edgecolor=C["border"],
              labelcolor=C["text"], fontsize=8)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{int(x):,}"))

    # 右图：缺口率饼/百分比条
    ax2 = axes[1]
    style_ax(ax2, title="缺口率 %")
    pcts = [min(100, r["lack"] / r["need"] * 100) if r["need"] > 0 else 0 for r in lacking]
    bars = ax2.barh(y, pcts, color=[
        C["red"] if p >= 80 else C["orange"] if p >= 40 else C["gold"]
        for p in pcts
    ], alpha=0.85, height=0.7)
    ax2.set_yticks(y)
    ax2.set_yticklabels(names, fontsize=8)
    ax2.invert_yaxis()
    ax2.set_xlim(0, 105)
    ax2.axvline(100, color=C["dim"], linestyle="--", linewidth=0.8)
    for bar, pct in zip(bars, pcts):
        ax2.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                 f"{pct:.0f}%", va="center", fontsize=7, color=C["text"])

    plt.tight_layout()
    out = out_dir / "01_bom_gaps.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=C["bg"])
    plt.close()
    print(f"  [BOM] 保存: {out}")


# ── 2. 中间产物树深度分布 ─────────────────────────────────────────────────

def plot_intermediate_depth(result: dict, out_dir: Path):
    inters = result["bom"]["intermediates"]
    if not inters:
        return

    acts = [i["act_name"] for i in inters]
    depths = [i["depth"] for i in inters]
    runs = [i["runs"] for i in inters]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    set_fig_dark(fig)
    fig.suptitle("中间产物结构分析", color=C["gold"], fontsize=14, fontweight="bold")

    # 深度分布
    ax = axes[0]
    style_ax(ax, title="深度分布")
    max_d = max(depths) if depths else 0
    depth_counts = [depths.count(d) for d in range(max_d + 1)]
    ax.bar(range(max_d + 1), depth_counts, color=C["accent"], alpha=0.8, edgecolor=C["bg"])
    ax.set_xlabel("分解深度")
    ax.set_ylabel("产物数量")

    # 活动类型饼图
    ax2 = axes[1]
    style_ax(ax2, title="活动类型分布")
    act_counts = {}
    for a in acts:
        act_counts[a] = act_counts.get(a, 0) + 1
    labels = list(act_counts.keys())
    sizes  = list(act_counts.values())
    colors = [ACT_COLOR.get(l, C["dim"]) for l in labels]
    wedges, texts, autotexts = ax2.pie(
        sizes, labels=labels, colors=colors, autopct="%1.0f%%",
        textprops={"color": C["text"], "fontsize": 9},
        wedgeprops={"edgecolor": C["bg"], "linewidth": 1.5}
    )
    for at in autotexts:
        at.set_color(C["bg"])
        at.set_fontweight("bold")

    # TOP20 轮次需求
    ax3 = axes[2]
    style_ax(ax3, title="TOP20 轮次需求")
    top = sorted(inters, key=lambda x: -x["runs"])[:20]
    top_names = [i["name"][:14] for i in top]
    top_runs  = [i["runs"] for i in top]
    top_colors = [ACT_COLOR.get(i["act_name"], C["dim"]) for i in top]
    y = np.arange(len(top))
    ax3.barh(y, top_runs, color=top_colors, alpha=0.85)
    ax3.set_yticks(y)
    ax3.set_yticklabels(top_names, fontsize=7)
    ax3.invert_yaxis()
    ax3.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    # legend
    legend_patches = [mpatches.Patch(color=v, label=k) for k, v in ACT_COLOR.items()]
    ax3.legend(handles=legend_patches, facecolor=C["panel"], edgecolor=C["border"],
               labelcolor=C["text"], fontsize=7, loc="lower right")

    plt.tight_layout()
    out = out_dir / "02_intermediate_structure.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=C["bg"])
    plt.close()
    print(f"  [BOM] 保存: {out}")


# ── 3. 槽位甘特图 ─────────────────────────────────────────────────────────

def _slot_colors(n):
    cmap = plt.get_cmap("tab20")
    return [cmap(i % 20) for i in range(n)]

def plot_gantt_multi_cycle(mc_schedule: dict, out_dir: Path,
                           filename: str, title: str, bar_color: str):
    """
    多周期甘特图。
    X轴 = 真实累计时间（小时），每个周期用竖线分隔。
    Y轴 = 槽位。
    颜色按任务名称区分。
    """
    cycles     = mc_schedule.get("cycles", [])
    period_s   = mc_schedule.get("period_secs", 86400)
    total_days = mc_schedule.get("total_real_days", 0)

    if not cycles:
        return

    n_slots = max((len(cyc["slots"]) for cyc in cycles), default=0)
    if n_slots == 0:
        return

    # 颜色映射：按 display_name（产物名）分配颜色，拷贝/发明同产物同色
    display_names = []
    for cyc in cycles:
        for slot in cyc["slots"]:
            for j in slot.get("jobs", []):
                dn = j.get("display_name") or j.get("name", "?")
                if dn not in display_names:
                    display_names.append(dn)
    cmap = plt.get_cmap("tab20")
    name_colors = {dn: cmap(i % 20) for i, dn in enumerate(display_names)}

    total_hours = total_days * 24
    period_h    = period_s / 3600
    fig_h = max(4, n_slots * 0.65 + 2.5)
    fig, ax = plt.subplots(figsize=(16, fig_h))
    set_fig_dark(fig)
    # 标题加上周期数和总天数
    n_cyc = mc_schedule.get("total_cycles", len(cycles))
    avg_u = mc_schedule.get("avg_util", 0) * 100
    full_title = f"{title}  [{n_cyc}周期 / {total_days:.2f}天 / 均利用率{avg_u:.1f}%]"
    style_ax(ax, title=full_title)

    for cyc in cycles:
        cyc_id  = cyc["cycle_id"]           # 1-based
        x_base  = (cyc_id - 1) * period_h  # 本周期在X轴的起始小时

        for slot in cyc["slots"]:
            si = slot["slot_id"]
            for j in slot.get("jobs", []):
                start_h = x_base + j.get("start_s", 0) / 3600
                dur_h   = j.get("duration_s", 0) / 3600
                if dur_h <= 0:
                    continue
                nm    = j.get("name", "?")
                note  = j.get("note", "")
                act   = j.get("activity_type", "")
                dn    = j.get("display_name") or nm
                color = name_colors.get(dn, bar_color)

                # 跨周期任务用半透明+斜线填充区分
                # 拷贝任务用较淡颜色区分于发明任务
                hatch = "//" if "multi-cycle" in note else ("\\"                        if act == "copying" else None)
                alpha = 0.55 if "in-progress" in note else (0.55 if act == "copying" else 0.88)

                rect = mpatches.FancyBboxPatch(
                    (start_h, si - 0.38), dur_h, 0.76,
                    boxstyle="round,pad=0.01",
                    facecolor=color, edgecolor=C["bg"], linewidth=0.4,
                    alpha=alpha, hatch=hatch
                )
                ax.add_patch(rect)

                # 标注：两行，居中于周期宽度，透明背景
                raw_nm   = j.get("display_name") or nm
                line1    = raw_nm[:14]
                runs_val = j.get("runs", 0)
                act_type = j.get("activity_type", "")
                # 第二行：runs数 + 活动类型/状态注释
                if "跨周期进行中" in note or "in-progress" in note:
                    sub = "进行中..."
                elif "跨周期完成" in note or "finish" in note:
                    sub = f"×{runs_val} ✓"
                elif "跨周期开始" in note or "start" in note:
                    sub = "开始跨周期"
                elif act_type == "copying":
                    sub = f"拷贝 ×{runs_val}"
                elif act_type == "invention":
                    sub = f"发明 ×{runs_val}"
                elif act_type == "t2_mfg":
                    sub = f"制造(BPC) ×{runs_val}"
                else:
                    sub = f"×{runs_val}" if runs_val else ""
                label  = line1 + "\n" + sub if sub else line1

                label_x = x_base + period_h / 2
                label_y = si
                txt_kw  = dict(ha="center", va="center", fontsize=5.5,
                               color="white", clip_on=False, linespacing=1.3)
                if _CJK_FONT_PROP is not None:
                    txt_kw["fontproperties"] = _CJK_FONT_PROP
                ax.text(label_x, label_y, label, **txt_kw)

        # 周期分隔线
        x_end = cyc_id * period_h
        ax.axvline(x_end, color=C["border"], linewidth=0.6, alpha=0.7, linestyle=":")
        # 周期编号：居中标注在顶部，超出ylim用clip_on=False保证可见
        ax.text(x_base + period_h / 2, n_slots - 0.5,
                f"C{cyc_id}", ha="center", va="center", fontsize=7,
                color=C["accent"], fontweight="bold", clip_on=False,
                bbox=dict(boxstyle="round,pad=0.15", facecolor=C["bg2"],
                          edgecolor=C["border"], linewidth=0.5, alpha=0.8))

    ax.set_xlim(0, total_hours * 1.01)
    ax.set_ylim(-0.6, n_slots + 0.3)   # 顶部留空给周期标签
    ax.set_yticks(range(n_slots))
    ax.set_yticklabels([f"槽{i+1}" for i in range(n_slots)], fontsize=8)
    ax.set_xlabel("累计时间（小时）")
    # X轴主刻度 = 周期边界
    period_ticks = [i * period_h for i in range(mc_schedule.get("total_cycles", 0) + 1)]
    ax.set_xticks(period_ticks)
    ax.xaxis.set_major_formatter(
        ticker.FuncFormatter(lambda x, _: f"{x:.1f}h")
    )
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=7)

    plt.tight_layout()
    out = out_dir / filename
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=C["bg"])
    plt.close()
    print(f"  [Gantt] 保存: {out}")



def plot_all_gantts(result: dict, out_dir: Path):
    cfg        = result["config"]
    period_s   = cfg["total_secs"]
    days       = cfg["days"]
    gs         = result.get("greedy_schedule", {})

    # ── 贪心多周期甘特图 ──
    mfg_mc = gs.get("mfg", {})
    if mfg_mc.get("cycles"):
        plot_gantt_multi_cycle(mfg_mc, out_dir,
            "03_gantt_greedy_mfg.png",
            f"制造产线甘特图（贪心多周期 / {days}天/周期）", C["accent"])

    react_mc = gs.get("react", {})
    if react_mc.get("cycles"):
        plot_gantt_multi_cycle(react_mc, out_dir,
            "04_gantt_greedy_react.png",
            f"反应产线甘特图（贪心多周期 / {days}天/周期）", C["orange"])

    # ── 发明/拷贝甘特图 ──
    inv_mc = gs.get("inv", {})
    if inv_mc.get("cycles"):
        plot_gantt_multi_cycle(inv_mc, out_dir,
            "05_gantt_inv.png",
            f"科研产线甘特图（发明/拷贝 / {days}天/周期）", C["purple"])


# ── 4. 优化收敛曲线 ───────────────────────────────────────────────────────


def plot_completion(result: dict, out_dir: Path):
    gs = result.get("greedy_schedule", {})

    all_completions = []
    for key, label in [("mfg", "制造"), ("react", "反应"), ("inv", "科研")]:
        for c in gs.get(key, {}).get("completion", []):
            all_completions.append({**c, "line": label})

    if not all_completions:
        return

    fig, ax = plt.subplots(figsize=(14, max(5, len(all_completions) * 0.35 + 2)))
    set_fig_dark(fig)
    style_ax(ax, title="最优方案 — 各产物完成率")

    names  = [c["name"][:20] for c in all_completions]
    pcts   = [c["pct"] for c in all_completions]
    def _line_color(line):
        return C["accent"] if line == "制造" else C["orange"] if line == "反应" else C["purple"]
    colors = [_line_color(c["line"]) for c in all_completions]
    y = np.arange(len(all_completions))
    bars = ax.barh(y, pcts, color=colors, alpha=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlim(0, 110)
    ax.axvline(100, color=C["green"], linestyle="--", linewidth=0.8)

    for bar, c in zip(bars, all_completions):
        pct = bar.get_width()
        ax.text(min(pct + 1, 105), bar.get_y() + bar.get_height() / 2,
                f"{pct:.0f}%  ({c['done_runs']}/{c['need_runs']})",
                va="center", fontsize=6.5, color=C["text"])

    legend_p = [mpatches.Patch(color=C["accent"], label="制造"),
                mpatches.Patch(color=C["orange"], label="反应"),
                mpatches.Patch(color=C["purple"], label="科研")]
    ax.legend(handles=legend_p, facecolor=C["panel"], edgecolor=C["border"],
              labelcolor=C["text"], fontsize=8)

    plt.tight_layout()
    out = out_dir / "07_completion.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=C["bg"])
    plt.close()
    print(f"  [OPT] 保存: {out}")


# ── 6. 槽位利用率热力图 ───────────────────────────────────────────────────

def _greedy_cycle_utils(mc_schedule: dict) -> list[float]:
    """从多周期调度结果提取各槽位跨周期平均利用率（%）"""
    cycles    = mc_schedule.get("cycles", [])
    period_s  = mc_schedule.get("period_secs", 1)
    if not cycles:
        return []
    n_slots = max((len(c["slots"]) for c in cycles), default=0)
    slot_used = [0.0] * n_slots
    for cyc in cycles:
        for slot in cyc["slots"]:
            si = slot["slot_id"]
            used = sum(j.get("duration_s", 0) for j in slot.get("jobs", []))
            slot_used[si] += used
    n_cyc = len(cycles)
    return [u / (period_s * n_cyc) * 100 for u in slot_used]


def plot_slot_utilization(result: dict, out_dir: Path):
    gs = result.get("greedy_schedule", {})
    greedy_mfg_utils   = _greedy_cycle_utils(gs.get("mfg",   {}))
    greedy_react_utils = _greedy_cycle_utils(gs.get("react", {}))
    greedy_inv_utils   = _greedy_cycle_utils(gs.get("inv",   {}))

    if not greedy_mfg_utils and not greedy_react_utils:
        return

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    set_fig_dark(fig)
    fig.suptitle("各产线槽位平均利用率", color=C["gold"], fontsize=13, fontweight="bold")

    groups = [
        (axes[0], "制造",  greedy_mfg_utils,   C["accent"]),
        (axes[1], "反应",  greedy_react_utils, C["orange"]),
        (axes[2], "科研",  greedy_inv_utils,   C["purple"]),
    ]

    for ax, title, utils, color in groups:
        style_ax(ax, title=title)
        if not utils:
            ax.text(0.5, 0.5, "无数据", transform=ax.transAxes,
                    ha="center", color=C["dim"])
            continue
        x = np.arange(len(utils))
        bars = ax.bar(x, utils, color=color, alpha=0.8, edgecolor=C["bg"])
        ax.axhline(np.mean(utils), color=C["gold"], linestyle="--", linewidth=0.9,
                   label=f"均值 {np.mean(utils):.1f}%")
        ax.set_ylim(0, 110)
        ax.set_xlabel("槽位编号")
        ax.set_ylabel("利用率 %")
        ax.set_xticks(x)
        ax.set_xticklabels([f"S{i+1}" for i in x], fontsize=7)
        for bar, u in zip(bars, utils):
            if u > 5:
                ax.text(bar.get_x() + bar.get_width() / 2, u + 1,
                        f"{u:.0f}%", ha="center", fontsize=6.5, color=C["text"])
        ax.legend(facecolor=C["panel"], edgecolor=C["border"],
                  labelcolor=C["text"], fontsize=8)

    plt.tight_layout()
    out = out_dir / "08_slot_utilization.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=C["bg"])
    plt.close()
    print(f"  [UTIL] 保存: {out}")


# ── 7. 发明需求总览 ───────────────────────────────────────────────────────

def plot_invention_needs(result: dict, out_dir: Path):
    inv_jobs = result["job_queue"].get("inv_jobs", [])
    if not inv_jobs:
        print("  [INV] 无发明需求，跳过")
        return

    fig, axes = plt.subplots(1, 2, figsize=(15, max(4, len(inv_jobs) * 0.42 + 2)))
    set_fig_dark(fig)
    fig.suptitle(f"发明需求分析（共 {len(inv_jobs)} 种产物需要发明BPC）",
                 color=C["gold"], fontsize=13, fontweight="bold")

    names    = [j["product_name"][:20] for j in inv_jobs]
    attempts = [j["expected_attempts"] for j in inv_jobs]
    probs    = [j["probability"] * 100 for j in inv_jobs]
    y = np.arange(len(inv_jobs))

    ax = axes[0]
    style_ax(ax, title="预计发明尝试次数")
    ax.barh(y, attempts, color=C["purple"], alpha=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlabel("次数")

    ax2 = axes[1]
    style_ax(ax2, title="发明成功概率 %")
    colors2 = [C["green"] if p >= 40 else C["gold"] if p >= 20 else C["red"] for p in probs]
    ax2.barh(y, probs, color=colors2, alpha=0.85)
    ax2.set_yticks(y)
    ax2.set_yticklabels(names, fontsize=7.5)
    ax2.invert_yaxis()
    ax2.set_xlim(0, 105)
    ax2.axvline(30, color=C["dim"], linestyle="--", linewidth=0.8, label="30%线")
    for yi, p in zip(y, probs):
        ax2.text(p + 0.5, yi, f"{p:.1f}%", va="center", fontsize=7, color=C["text"])
    ax2.legend(facecolor=C["panel"], edgecolor=C["border"], labelcolor=C["text"], fontsize=8)

    plt.tight_layout()
    out = out_dir / "09_invention_needs.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=C["bg"])
    plt.close()
    print(f"  [INV] 保存: {out}")


# ── 8. 综合仪表板 ─────────────────────────────────────────────────────────

def plot_dashboard(result: dict, out_dir: Path):
    cfg  = result["config"]
    bom  = result["bom"]
    jq   = result["job_queue"]
    gs   = result.get("greedy_schedule", {})

    fig = plt.figure(figsize=(18, 10))
    set_fig_dark(fig)
    fig.suptitle("EVE 工业生产规划仪表板", color=C["gold"],
                 fontsize=16, fontweight="bold", y=0.98)

    gs_layout = fig.add_gridspec(3, 4, hspace=0.45, wspace=0.35)

    # ── 配置卡 ──
    ax_cfg = fig.add_subplot(gs_layout[0, 0])
    ax_cfg.set_facecolor(C["panel"])
    ax_cfg.axis("off")
    ax_cfg.set_title("配置", color=C["accent"], fontsize=10, fontweight="bold")
    cfg_text = (
        f"时间段: {cfg['days']} 天\n"
        f"ME: {cfg['me_pct']}%  TE: {cfg['te_pct']}%\n"
        f"制造槽: {cfg['slots_mfg']}\n"
        f"发明槽: {cfg['slots_inv']}\n"
        f"反应槽: {cfg['slots_react']}"
    )
    ax_cfg.text(0.05, 0.95, cfg_text, transform=ax_cfg.transAxes,
                color=C["text"], fontsize=9, va="top", linespacing=1.8)

    # ── BOM 统计卡 ──
    ax_bom_stat = fig.add_subplot(gs_layout[0, 1])
    ax_bom_stat.set_facecolor(C["panel"])
    ax_bom_stat.axis("off")
    ax_bom_stat.set_title("BOM 统计", color=C["accent"], fontsize=10, fontweight="bold")
    total_raw = len(bom["raw_materials"])
    lack_raw  = bom["raw_lacking_count"]
    total_int = len(bom["intermediates"])
    lack_bp   = bom["inter_lacking_bp_count"]
    bom_text = (
        f"最终产物: {len(result['final_products'])}\n"
        f"原材料种: {total_raw}\n"
        f"  缺口:   {lack_raw} ({lack_raw/max(total_raw,1)*100:.0f}%)\n"
        f"中间产物: {total_int}\n"
        f"  缺蓝图: {lack_bp} ({lack_bp/max(total_int,1)*100:.0f}%)\n"
        f"制造任务: {len(jq['mfg_jobs'])}\n"
        f"反应任务: {len(jq['react_jobs'])}\n"
        f"发明需求: {len(jq['inv_jobs'])}"
    )
    ax_bom_stat.text(0.05, 0.95, bom_text, transform=ax_bom_stat.transAxes,
                     color=C["text"], fontsize=9, va="top", linespacing=1.8)

    # ── 调度结果卡 ──
    ax_opt_stat = fig.add_subplot(gs_layout[0, 2])
    ax_opt_stat.set_facecolor(C["panel"])
    ax_opt_stat.axis("off")
    ax_opt_stat.set_title("调度结果", color=C["accent"], fontsize=10, fontweight="bold")
    mfg_fit  = 0
    react_fit = 0
    mfg_t    = 0
    react_t  = 0
    g_mfg_u  = gs.get("mfg",   {}).get("avg_util", 0) * 100
    g_react_u = gs.get("react", {}).get("avg_util", 0) * 100
    g_mfg_cyc    = gs.get("mfg",   {}).get("total_cycles", "-")
    g_react_cyc  = gs.get("react", {}).get("total_cycles", "-")
    g_inv_cyc    = gs.get("inv",   {}).get("total_cycles", "-")
    g_mfg_days   = gs.get("mfg",   {}).get("total_real_days", 0)
    g_react_days = gs.get("react", {}).get("total_real_days", 0)
    g_inv_days   = gs.get("inv",   {}).get("total_real_days", 0)
    opt_text = (
        f"制造利用率: {g_mfg_u:.1f}%\n"
        f"反应利用率: {g_react_u:.1f}%\n"
        f"制造周期数: {g_mfg_cyc} ({g_mfg_days:.2f}天)\n"
        f"反应周期数: {g_react_cyc} ({g_react_days:.2f}天)\n"
        f"科研周期数: {g_inv_cyc} ({g_inv_days:.2f}天)"
    )
    ax_opt_stat.text(0.05, 0.95, opt_text, transform=ax_opt_stat.transAxes,
                     color=C["text"], fontsize=9, va="top", linespacing=1.8)

    # ── 最终产物列表 ──
    ax_prods = fig.add_subplot(gs_layout[0, 3])
    ax_prods.set_facecolor(C["panel"])
    ax_prods.axis("off")
    ax_prods.set_title("最终产物目标", color=C["accent"], fontsize=10, fontweight="bold")
    prods = result["final_products"]
    prod_text = "\n".join(f"{p['name'][:18]}: {p['qty']}" for p in prods[:12])
    if len(prods) > 12:
        prod_text += f"\n...共{len(prods)}种"
    ax_prods.text(0.05, 0.95, prod_text, transform=ax_prods.transAxes,
                  color=C["text"], fontsize=8, va="top", linespacing=1.6)

    # ── 周期汇总 ──
    ax_conv = fig.add_subplot(gs_layout[1, :2])
    style_ax(ax_conv, title="各产线周期利用率")
    for key, color, label in [("mfg", C["accent"], "制造"), ("react", C["orange"], "反应"), ("inv", C["purple"], "科研")]:
        cyc_data = gs.get(key, {}).get("cycles", [])
        if cyc_data:
            utils = [c["avg_util"] * 100 for c in cyc_data]
            ax_conv.bar(range(len(utils)), utils, color=color, alpha=0.7, label=label, width=0.25,
                        align="edge" if key=="mfg" else ("center" if key=="react" else "edge"))
    ax_conv.set_xlabel("周期编号")
    ax_conv.set_ylabel("利用率 %")
    ax_conv.set_ylim(0, 110)
    ax_conv.legend(facecolor=C["panel"], edgecolor=C["border"],
                   labelcolor=C["text"], fontsize=8)

    # ── 原材料缺口 TOP10 ──
    ax_gap = fig.add_subplot(gs_layout[1, 2:])
    style_ax(ax_gap, title="缺口原材料 TOP10")
    lacking = [r for r in bom["raw_materials"] if not r["sufficient"]][:10]
    if lacking:
        names_g = [r["name"][:16] for r in lacking]
        lack_v  = [r["lack"] for r in lacking]
        y = np.arange(len(lacking))
        ax_gap.barh(y, lack_v, color=C["red"], alpha=0.75)
        ax_gap.set_yticks(y)
        ax_gap.set_yticklabels(names_g, fontsize=7)
        ax_gap.invert_yaxis()
        ax_gap.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    else:
        ax_gap.text(0.5, 0.5, "✓ 原材料充足", transform=ax_gap.transAxes,
                    ha="center", color=C["green"], fontsize=12)

    # ── 完成率 ──
    ax_comp = fig.add_subplot(gs_layout[2, :])
    style_ax(ax_comp, title="各产线任务完成率")
    all_c = []
    for key, lbl in [("mfg", "制造"), ("react", "反应"), ("inv", "科研")]:
        for c in gs.get(key, {}).get("completion", []):
            all_c.append({**c, "_line": lbl})
    if all_c:
        all_c = sorted(all_c, key=lambda x: x["pct"])
        names_c  = [c["name"][:20] for c in all_c]
        pcts_c   = [c["pct"] for c in all_c]
        def _dc(line): return C["accent"] if line=="制造" else C["orange"] if line=="反应" else C["purple"]
        colors_c = [_dc(c["_line"]) for c in all_c]
        y = np.arange(len(all_c))
        ax_comp.barh(y, pcts_c, color=colors_c, alpha=0.8)
        ax_comp.set_yticks(y)
        ax_comp.set_yticklabels(names_c, fontsize=7)
        ax_comp.set_xlim(0, 112)
        ax_comp.axvline(100, color=C["green"], linestyle="--", linewidth=0.8)
        for yi, c in zip(y, all_c):
            ax_comp.text(c["pct"] + 0.5, yi, f"{c['pct']:.0f}%",
                         va="center", fontsize=6, color=C["text"])
        legend_p = [mpatches.Patch(color=C["accent"], label="制造"),
                    mpatches.Patch(color=C["orange"], label="反应"),
                    mpatches.Patch(color=C["purple"], label="科研")]
        ax_comp.legend(handles=legend_p, facecolor=C["panel"], edgecolor=C["border"],
                       labelcolor=C["text"], fontsize=8, loc="lower right")
    else:
        ax_comp.text(0.5, 0.5, "暂无完成率数据", transform=ax_comp.transAxes,
                     ha="center", color=C["dim"])

    out = out_dir / "00_dashboard.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=C["bg"])
    plt.close()
    print(f"  [DASH] 保存: {out}")


# ── 主入口 ────────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> tuple[dict, Path]:
    """加载 config.json，推断 eve_root（config 所在目录向上三级：apps/app/config → eve/）"""
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    eve_root = config_path.resolve().parent.parent.parent
    return cfg, eve_root


def main():
    parser = argparse.ArgumentParser(
        description="EVE Industry Visualizer — 生成生产规划图表",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python visualizer.py                          # 使用同目录 config.json
  python visualizer.py --config /path/config.json
  python visualizer.py --result /path/result.json --out-dir /path/charts/
        """
    )
    parser.add_argument("--config",  default=None, help="config.json 路径（默认同目录）")
    parser.add_argument("--root",    default=None, help="eve/ 根目录（覆盖推断）")
    parser.add_argument("--result",  default=None, help="plan_result.json 路径（覆盖 config）")
    parser.add_argument("--out-dir", default=None, help="图表输出目录（覆盖 config）")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else Path(__file__).parent / "config.json"
    cfg, eve_root = load_config(config_path)
    if args.root:
        eve_root = Path(args.root).resolve()

    result_path = Path(args.result) if args.result else (
        eve_root / cfg["output_dir"] / "plan_result.json"
    )
    out_dir = Path(args.out_dir) if args.out_dir else (
        eve_root / cfg["output_dir"] / "charts"
    )

    if not result_path.exists():
        print(f"错误: 找不到结果文件 {result_path}")
        print("请先运行: python planner.py")
        return

    print(f"读取结果: {result_path}")
    with open(result_path, encoding="utf-8") as f:
        result = json.load(f)

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"输出目录: {out_dir.resolve()}")
    print("\n生成图表...")
    plot_dashboard(result, out_dir)
    plot_bom_gaps(result, out_dir)
    plot_intermediate_depth(result, out_dir)
    plot_all_gantts(result, out_dir)
    plot_completion(result, out_dir)
    plot_slot_utilization(result, out_dir)
    plot_invention_needs(result, out_dir)

    print(f"\n✓ 全部图表已保存至: {out_dir.resolve()}/")
    for f in sorted(out_dir.glob("*.png")):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
