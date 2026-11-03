# -*- coding: utf-8 -*-
"""EducationalChartBuilder · 教育评估可视化代码专家生成器。

为各类评估场景生成结构优雅、带完整中文标注的 Matplotlib Python 脚本：
1. build_radar_code: 多维能力雷达图（支持单实体与双线对比）；
2. build_ranking_code: 区域梯队排位与基准线对照条形图；
3. build_equipment_fact_code: 办学规模与终端设备客观指标横纵对比图；
4. build_region_macro_code: 区域宏观大盘（多维能力雷达 + 顶尖标杆校排行榜）。
"""

import json
from typing import Dict, List, Optional, Tuple


class EducationalChartBuilder:
    """教育数字化评估图表 Python 脚本生成器。"""

    @staticmethod
    def build_task_values_code(nodes: List[Dict], kind: str, unit: str) -> str:
        """Plot executed task outputs in order, with their literal scopes and units."""
        if kind not in {'bar', 'line'} or not nodes:
            raise ValueError('invalid_task_chart')
        labels = [str(n['label']) for n in nodes]
        values = [float(n['value']) for n in nodes]
        draw = "ax.bar(positions, values, width=0.55, color='#357a91')" if kind == 'bar' else "ax.plot(positions, values, marker='o', color='#357a91')"
        return f'''import matplotlib.pyplot as plt
labels = {labels!r}
values = {values!r}
unit = {unit!r}
positions = list(range(len(values)))
fig, ax = plt.subplots(figsize=(10, 5.4), dpi=160)
{draw}
ax.set_xticks(positions, labels, rotation=15, ha='right')
ax.set_ylabel(unit)
ax.set_title(' / '.join(labels))
ax.axhline(0, color='#888888', linewidth=0.5)
ax.margins(y=0.2)
for x, value in zip(positions, values):
    ax.annotate(f"{{value:g}} {{unit}}", (x, value), xytext=(0, 5), textcoords='offset points', ha='center')
fig.tight_layout()
'''

    @staticmethod
    def build_school_item_set_code(title: str, items: List[Dict], derived: Optional[Dict] = None) -> str:
        """Build verified item bars, including a requested additive total."""
        labels = []
        for item in items:
            name = str(item["metric_name"])
            wrapped = "\n".join(name[i:i + 12] for i in range(0, len(name), 12))
            labels.append(f"Q{int(item['question_id'])}\n{wrapped}")
        values = [float(item["target_value"]) for item in items]
        unit = str(items[0].get("unit") or "")
        if derived is not None and derived.get("operation") == "add":
            labels.append("合计")
            values.append(float(derived["value"]))
        ratio_label = ""
        if derived is not None and derived.get("operation") == "divide":
            ratio_label = f"Q{int(derived['left_qid'])} ÷ Q{int(derived['right_qid'])} = {float(derived['value']):g}（计数比值，无量纲）"
        return f'''import matplotlib.pyplot as plt
labels = {json.dumps(labels, ensure_ascii=False)}
values = {json.dumps(values)}
unit = {json.dumps(unit, ensure_ascii=False)}
fig, ax = plt.subplots(figsize=(8, 4.8), dpi=160)
positions = list(range(len(values)))
bars = ax.bar(positions, values, color=['#357a91', '#d18442', '#629057'][:len(values)], width=0.55)
ax.set_xticks(positions, labels)
ax.set_ylabel(unit)
ax.set_title({json.dumps(title, ensure_ascii=False)})
ratio_label = {json.dumps(ratio_label, ensure_ascii=False)}
if ratio_label:
    ax.text(0.5, 0.97, ratio_label, transform=ax.transAxes, ha='center', va='top')
ax.set_ylim(0, max(values) * 1.2 if max(values) > 0 else 1)
for bar, value in zip(bars, values):
    ax.annotate(f"{{value:g}}{{unit}}", (bar.get_x() + bar.get_width()/2, bar.get_height()),
                xytext=(0, 4), textcoords='offset points', ha='center', va='bottom')
fig.tight_layout()
'''

    @staticmethod
    def build_radar_code(
        title: str,
        dimensions: List[str],
        values: List[float],
        comp_title: Optional[str] = None,
        comp_values: Optional[List[float]] = None,
        max_val: Optional[float] = None,
    ) -> str:
        """生成极坐标多维雷达图代码。"""
        dims_json = json.dumps(dimensions, ensure_ascii=False)
        vals_json = json.dumps([round(float(v), 2) for v in values])
        comp_vals_json = json.dumps([round(float(v), 2) for v in comp_values]) if comp_values else "None"
        comp_title_str = repr(comp_title) if comp_title else "None"
        title_literal = repr(title)

        # 动态计算雷达图上限刻度
        all_vals = list(values) + (list(comp_values) if comp_values else [])
        highest = max(all_vals) if all_vals else 20.0
        ceil_val = max_val or (round(highest * 1.25) if highest > 10 else 20.0)

        return f'''# 绘制多维数字化能力雷达图
import numpy as np
import matplotlib.pyplot as plt

dimensions = {dims_json}
values = {vals_json}
comp_values = {comp_vals_json}
comp_title = {comp_title_str}

N = len(dimensions)
angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
# 闭合曲线
angles_closed = angles + angles[:1]
vals_closed = values + values[:1]

fig, ax = plt.subplots(figsize=(6.5, 5.5), subplot_kw=dict(polar=True), dpi=160)
ax.set_theta_offset(np.pi / 2)
ax.set_theta_direction(-1)

# 设置刻度网格
ax.set_xticks(angles)
ax.set_xticklabels(dimensions, size=11, weight='bold', color='#201f1d')
ax.set_rlabel_position(0)
ax.set_ylim(0, {ceil_val})
ax.tick_params(axis='y', colors='#6f6c65', labelsize=9)

# 主体数据线
ax.plot(angles_closed, vals_closed, color='#c96442', linewidth=2.2, linestyle='solid', label='评估得分')
ax.fill(angles_closed, vals_closed, color='#c96442', alpha=0.25)
ax.scatter(angles, values, color='#c96442', s=45, zorder=5)

# 对比参考线（如全区均值）
if comp_values and len(comp_values) == N:
    comp_closed = comp_values + comp_values[:1]
    lbl = comp_title or '全区基准均值'
    ax.plot(angles_closed, comp_closed, color='#d97706', linewidth=1.8, linestyle='--', label=lbl)
    ax.fill(angles_closed, comp_closed, color='#d97706', alpha=0.12)
    ax.scatter(angles, comp_values, color='#d97706', s=30, zorder=4)

ax.grid(color='#e8e6df', linestyle='--', linewidth=0.8, alpha=0.8)
ax.spines['polar'].set_color('#dedbd2')
plt.title({title_literal}, size=14, weight='bold', pad=22, color='#201f1d')
plt.legend(loc='upper right', bbox_to_anchor=(1.22, 1.12), frameon=True, facecolor='#ffffff', edgecolor='#e8e6df', fontsize=10)
plt.tight_layout()
print(f"雷达图生成完成：涵盖 {{N}} 个维度")
'''

    @staticmethod
    def build_ranking_code(
        title: str,
        school_name: str,
        target_score: float,
        region_avg: float,
        peer_schools: List[Dict[str, any]],
        rank: int,
        total_schools: int,
    ) -> str:
        """生成区域学校排位梯队水平对比条形图。"""
        # 准备数据：提取临近梯队校名和得分
        names = []
        scores = []
        is_target_list = []

        for p in peer_schools:
            names.append(p.get("school_name", "未知学校"))
            scores.append(round(float(p.get("score", 0.0)), 2))
            is_target_list.append(bool(p.get("is_target", False)))

        # 若目标校不在梯队里，补充进去
        if not any(is_target_list):
            names.insert(0, school_name)
            scores.insert(0, round(float(target_score), 2))
            is_target_list.insert(0, True)

        names_json = json.dumps(names, ensure_ascii=False)
        scores_json = json.dumps(scores)
        is_target_repr = repr(is_target_list)

        return f'''# 绘制区域学校排位梯队横向对比图
import matplotlib.pyplot as plt
import numpy as np

names = {names_json}
scores = {scores_json}
is_targets = {is_target_repr}
region_avg = {round(float(region_avg), 2)}

# 倒序排列使高分排在上方
names = names[::-1]
scores = scores[::-1]
is_targets = is_targets[::-1]

y_pos = np.arange(len(names))
colors = ['#c96442' if t else '#dedbd2' for t in is_targets]

fig, ax = plt.subplots(figsize=(7.2, max(4.0, len(names) * 0.55)), dpi=160)
bars = ax.barh(y_pos, scores, color=colors, height=0.55, edgecolor='none')

# 标注全区均值参考线
ax.axvline(region_avg, color='#d97706', linestyle='--', linewidth=1.5, alpha=0.9,
           label=f'全区均值 ({{region_avg}} 分)')

# 柱顶数值标签
for bar, score, is_t in zip(bars, scores, is_targets):
    w = bar.get_width()
    txt = f" {{score}}分" + (" (当前学校)" if is_t else "")
    fcolor = '#c96442' if is_t else '#6f6c65'
    fweight = 'bold' if is_t else 'normal'
    ax.text(w + 0.15, bar.get_y() + bar.get_height()/2, txt,
            va='center', ha='left', color=fcolor, weight=fweight, fontsize=10)

ax.set_yticks(y_pos)
ax.set_yticklabels(names, fontsize=10.5, color='#201f1d')
ax.set_xlabel('数字化综合得分', fontsize=11, color='#6f6c65')
ax.set_title('{title}\\n(第 {rank} 名 / 全区共 {total_schools} 所)', fontsize=13.5, weight='bold', pad=14, color='#201f1d')

ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.spines['left'].set_color('#dedbd2')
ax.spines['bottom'].set_color('#dedbd2')
ax.grid(axis='x', color='#f0eee8', linestyle='--', alpha=0.8)

# 动态扩展 x 轴留出标签空间
max_s = max(scores + [region_avg])
ax.set_xlim(0, max_s * 1.25)
ax.legend(loc='lower right', frameon=True, facecolor='#ffffff', edgecolor='#e8e6df', fontsize=10)
plt.tight_layout()
print("排位梯队对比柱状图生成完毕")
'''

    @staticmethod
    def build_region_ranking_code(
        title: str,
        ranking_items: List[Dict],
        eligible_school_count: int,
    ) -> str:
        """Draw only the verified schools in a region ranking result."""
        if not ranking_items:
            return ""
        labels = [
            f"第{int(item['rank'])}名  {item['school_name']}"
            for item in ranking_items
        ]
        scores = [round(float(item["score"]), 2) for item in ranking_items]
        subtitle = f"有效候选学校{eligible_school_count}所" if eligible_school_count > 0 else ""
        display_title = title + ("\n" + subtitle if subtitle else "")
        return f'''import matplotlib.pyplot as plt
import numpy as np
labels = {json.dumps(labels, ensure_ascii=False)}[::-1]
scores = {json.dumps(scores)}[::-1]
fig, ax = plt.subplots(figsize=(8.8, max(4.2, len(labels) * 0.75 + 1.4)), dpi=160)
bars = ax.barh(np.arange(len(labels)), scores, color='#d97706', height=0.56)
ax.set_yticks(np.arange(len(labels)), labels)
ax.set_xlabel('数字化综合得分（分）')
ax.set_title({json.dumps(display_title, ensure_ascii=False)},
             fontsize=12.5, weight='bold', pad=16)
ax.set_xlim(0, max(scores) * 1.28 if max(scores) > 0 else 1)
for bar, score in zip(bars, scores):
    ax.text(bar.get_width() + max(scores) * 0.015, bar.get_y() + bar.get_height() / 2,
            f'{{score:g}}分', va='center', fontsize=10, weight='bold', color='#a74915')
ax.grid(axis='x', color='#f0eee8', linestyle='--', alpha=0.8)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.tight_layout()
'''

    @staticmethod
    def build_equipment_fact_code(
        school_name: str,
        metric_name: str,
        target_value: float,
        unit: str,
        context_facts: List[Dict[str, any]],
        history: Optional[List[Dict[str, any]]] = None,
    ) -> str:
        """生成具体指标事实与办学规模对比柱状图/折线图。"""
        # 整合要对比的几项核心指标
        labels = [metric_name]
        values = [float(target_value)]

        for c in context_facts[:4]:
            lbl = c.get("label", "")
            val_str = str(c.get("value", "0"))
            # 提取纯数字
            num = ""
            for ch in val_str:
                if ch.isdigit() or ch == '.':
                    num += ch
                elif num:
                    break
            if num:
                labels.append(lbl)
                values.append(float(num))

        labels_json = json.dumps(labels, ensure_ascii=False)
        values_json = json.dumps(values)

        # 检查是否有跨年度历史
        hist_years = []
        hist_vals = []
        if history and len(history) > 1:
            for h in history:
                y = h.get("year")
                v = h.get("value")
                if y and v is not None:
                    v_str = str(v)
                    num = ""
                    for ch in v_str:
                        if ch.isdigit() or ch == '.':
                            num += ch
                        elif num:
                            break
                    if num:
                        try:
                            val_f = float(num)
                            hist_years.append(str(y))
                            hist_vals.append(val_f)
                        except (ValueError, TypeError):
                            pass

        hist_years_json = json.dumps(hist_years)
        hist_vals_json = json.dumps(hist_vals)

        return f'''# 绘制学校设备配置指标与关联规模图
import matplotlib.pyplot as plt
import numpy as np

labels = {labels_json}
values = {values_json}
hist_years = {hist_years_json}
hist_vals = {hist_vals_json}

has_hist = len(hist_years) > 1

if has_hist:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.5), dpi=160)
else:
    fig, ax1 = plt.subplots(1, 1, figsize=(6.8, 4.5), dpi=160)
    ax2 = None

# 图1：核心指标与办学配置
x_pos = np.arange(len(labels))
bar_colors = ['#c96442'] + ['#e2ded5'] * (len(labels) - 1)
bars = ax1.bar(x_pos, values, color=bar_colors, width=0.55, edgecolor='none')

for bar, val in zip(bars, values):
    h = bar.get_height()
    ax1.text(bar.get_x() + bar.get_width()/2, h + (max(values)*0.02 or 0.5),
             f"{{int(val) if val.is_integer() else val}}",
             ha='center', va='bottom', fontsize=10.5, weight='bold', color='#201f1d')

ax1.set_xticks(x_pos)
ax1.set_xticklabels(labels, rotation=15, ha='right', fontsize=9.5, color='#201f1d')
ax1.set_title('{school_name}\\n指标核查与规模概况', fontsize=12.5, weight='bold', pad=12, color='#201f1d')
ax1.spines['top'].set_visible(False)
ax1.spines['right'].set_visible(False)
ax1.spines['left'].set_color('#dedbd2')
ax1.spines['bottom'].set_color('#dedbd2')
ax1.grid(axis='y', color='#f0eee8', linestyle='--', alpha=0.8)
ax1.set_ylim(0, max(values) * 1.18 if max(values) > 0 else 10)

# 图2（若有跨年）：历史演进走势
if ax2 and has_hist:
    ax2.plot(hist_years, hist_vals, color='#c96442', marker='o', linewidth=2.2, markersize=7, label='{metric_name}')
    for y, v in zip(hist_years, hist_vals):
        ax2.text(y, v + (max(hist_vals)*0.03 or 0.5), f"{{int(v) if v.is_integer() else v}}",
                 ha='center', va='bottom', fontsize=10, weight='bold', color='#c96442')
    ax2.set_title('历史跨年度演进监测', fontsize=12.5, weight='bold', pad=12, color='#201f1d')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.spines['left'].set_color('#dedbd2')
    ax2.spines['bottom'].set_color('#dedbd2')
    ax2.grid(color='#f0eee8', linestyle='--', alpha=0.8)
    ax2.set_ylim(0, max(hist_vals) * 1.25 if max(hist_vals) > 0 else 10)

plt.tight_layout()
print("指标事实与配置规模图表生成完成")
'''

    @staticmethod
    def build_school_dimension_bar_code(
        school_name: str,
        year: str,
        dimension: str,
        score: float,
    ) -> str:
        """One verified school dimension as one bar; item counts stay in prose."""
        school_repr = repr(str(school_name))
        year_repr = repr(str(year))
        dimension_repr = repr(str(dimension))
        score_value = round(float(score), 2)
        return f'''# 单校单维得分，不渲染六维雷达或其他维度
import matplotlib.pyplot as plt

school_name = {school_repr}
year = {year_repr}
dimension = {dimension_repr}
score = {score_value}
fig, ax = plt.subplots(figsize=(6.6, 4.5), dpi=160)
bars = ax.bar([dimension], [score], width=0.48, color='#c96442')
ax.set_ylim(0, max(100.0, score * 1.18))
ax.set_ylabel('维度得分（分）')
ax.set_title(f"{{school_name}} · {{year}}年 · {{dimension}}维度得分", pad=16)
ax.grid(axis='y', linestyle='--', color='#eeeae3', alpha=0.8)
ax.set_axisbelow(True)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.text(bars[0].get_x() + bars[0].get_width() / 2, score + 1.5,
        f"{{score:.2f}}分", ha='center', va='bottom', fontsize=12, weight='bold')
plt.tight_layout()
print('单校单维柱状图生成完成')
'''

    @staticmethod
    def build_region_dimension_bar_code(
        region_name: str,
        year: str,
        dimension: str,
        score: float,
        school_filter: str,
        school_count: int,
        answer_count: int,
    ) -> str:
        """区域单维学校等权均分的单柱图。"""
        return f'''# 区域单维均分；学校筛选、分母和图形保持同一范围
import matplotlib.pyplot as plt

region_name = {str(region_name)!r}
year = {str(year)!r}
dimension = {str(dimension)!r}
score = {round(float(score), 2)}
school_filter = {str(school_filter)!r}
school_count = {int(school_count)}
answer_count = {int(answer_count)}

fig, ax = plt.subplots(figsize=(7.4, 4.8), dpi=160)
bar = ax.bar([dimension], [score], width=0.46, color='#c96442')[0]
ax.set_ylim(0, max(100.0, score * 1.25))
ax.set_ylabel('学校等权均分（分）', fontsize=10.5, color='#6f6c65')
ax.set_title(f'{{region_name}} · {{year}}年 · {{school_filter}} · {{dimension}}维度学校等权均分',
             fontsize=12.5, weight='bold', pad=16, color='#201f1d')
ax.text(bar.get_x() + bar.get_width()/2, score + 1.5, f'{{score:.2f}}分',
        ha='center', va='bottom', fontsize=12, weight='bold')
ax.grid(axis='y', linestyle='--', color='#eeeae3', alpha=0.8)
ax.set_axisbelow(True)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.text(0.5, 0.035, f'有效学校 {{school_count}} 所 · 有效作答 {{answer_count}} 条',
         ha='center', va='bottom', fontsize=10, color='#6f6c65')
plt.tight_layout(rect=[0, 0.07, 1, 1])
print('区域单维学校等权均分图生成完成')
'''

    @staticmethod
    def build_region_item_code(
        region_name: str,
        year: str,
        metric_name: str,
        score: float,
        school_count: int,
        answer_count: int,
    ) -> str:
        """生成区域单题定向图，只呈现题名、归一均分与两个分母。"""
        region_repr = repr(str(region_name))
        year_repr = repr(str(year))
        metric_repr = repr(str(metric_name))
        score_value = round(float(score), 2)
        x_max = max(100.0, round(score_value * 1.25, 2), 10.0)

        return f'''# 绘制区域单题归一均分图；不包含维度雷达、学校排名或总体诊断
import matplotlib.pyplot as plt

region_name = {region_repr}
year = {year_repr}
metric_name = {metric_repr}
score = {score_value}
school_count = {int(school_count)}
answer_count = {int(answer_count)}

fig, ax = plt.subplots(figsize=(9.2, 3.6), dpi=160)
bars = ax.barh([metric_name], [score], color='#c96442', height=0.46)
ax.set_xlim(0, {x_max})
ax.set_xlabel('问卷归一得分', fontsize=10.5, color='#6f6c65')
ax.set_title(f"{{region_name}} · {{year}}年区域单题归一均分", fontsize=13, weight='bold', pad=14, color='#201f1d')
ax.grid(axis='x', color='#f0eee8', linestyle='--', alpha=0.8)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.spines['left'].set_color('#dedbd2')
ax.spines['bottom'].set_color('#dedbd2')
bar = bars[0]
ax.text(score + {x_max} * 0.015, bar.get_y() + bar.get_height() / 2,
        f"{{score:.2f}}分", va='center', ha='left', color='#c96442', weight='bold', fontsize=11)
fig.text(0.5, 0.03, f"分母：{{school_count}}所学校 / {{answer_count}}条有效作答",
         ha='center', va='bottom', fontsize=10, color='#6f6c65')
plt.tight_layout(rect=[0, 0.08, 1, 1])
print("区域单题归一均分图生成完成")
'''

    @staticmethod
    def build_region_macro_code(
        region_name: str,
        year: str,
        total_score: float,
        school_count: int,
        dimension_scores: Dict[str, float],
        top_schools: List[Dict[str, any]],
        target_school_name: Optional[str] = None,
        target_rank: Optional[int] = None,
    ) -> str:
        """生成区域宏观双联综合图：左侧五维能力雷达，右侧顶尖学校排行榜（支持高亮目标位次）。"""
        dims = list(dimension_scores.keys())
        dim_vals = [round(float(v), 2) for v in dimension_scores.values()]

        # 顶尖学校
        top_n = min(len(top_schools), 6)
        school_names = [s.get("school_name", "") for s in top_schools[:top_n]][::-1]
        def _get_school_score(item: dict) -> float:
            for k in ("score", "avg_score", "total_score"):
                v = item.get(k)
                if v is not None:
                    try:
                        return round(float(v), 2)
                    except (ValueError, TypeError):
                        pass
            return 0.0
        school_scores = [_get_school_score(s) for s in top_schools[:top_n]][::-1]

        dims_json = json.dumps(dims, ensure_ascii=False)
        dim_vals_json = json.dumps(dim_vals)
        school_names_json = json.dumps(school_names, ensure_ascii=False)
        school_scores_json = json.dumps(school_scores)
        target_name_str = repr(target_school_name) if target_school_name else "None"
        target_rank_str = repr(target_rank) if target_rank else "None"

        max_dim = max(dim_vals) if dim_vals else 10.0
        ceil_dim = round(max_dim * 1.25) if max_dim > 10 else 20.0

        return f'''# 绘制区域宏观双联综合图：五维能力雷达 + 顶尖领跑学校榜
import matplotlib.pyplot as plt
import numpy as np

dimensions = {dims_json}
dim_vals = {dim_vals_json}
school_names = {school_names_json}
school_scores = {school_scores_json}
target_name = {target_name_str}
target_rank = {target_rank_str}

fig = plt.figure(figsize=(11.5, 5.2), dpi=160)

# 子图1：五维能力雷达
ax1 = fig.add_subplot(1, 2, 1, projection='polar')
N = len(dimensions)
if N > 0:
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles_closed = angles + angles[:1]
    vals_closed = dim_vals + dim_vals[:1]

    ax1.set_theta_offset(np.pi / 2)
    ax1.set_theta_direction(-1)
    ax1.set_xticks(angles)
    ax1.set_xticklabels(dimensions, size=10.5, weight='bold', color='#201f1d')
    ax1.set_ylim(0, {ceil_dim})
    ax1.tick_params(axis='y', colors='#6f6c65', labelsize=8.5)

    ax1.plot(angles_closed, vals_closed, color='#c96442', linewidth=2.2, linestyle='solid')
    ax1.fill(angles_closed, vals_closed, color='#c96442', alpha=0.25)
    ax1.scatter(angles, dim_vals, color='#c96442', s=45, zorder=5)
    ax1.grid(color='#e8e6df', linestyle='--', linewidth=0.8, alpha=0.8)
    ax1.spines['polar'].set_color('#dedbd2')
ax1.set_title('{region_name} 数字化能力五维均值', fontsize=12.5, weight='bold', pad=18, color='#201f1d')

# 子图2：区域顶尖标杆校排行榜
ax2 = fig.add_subplot(1, 2, 2)
if school_names:
    y_pos = np.arange(len(school_names))
    bar_colors = ['#c96442' if (target_name and s == target_name) else '#d97706' for s in school_names]
    bars = ax2.barh(y_pos, school_scores, color=bar_colors, height=0.55, edgecolor='none', alpha=0.9)

    for bar, score, s_name in zip(bars, school_scores, school_names):
        w = bar.get_width()
        is_tgt = bool(target_name and s_name == target_name)
        txt = f" {{score}}分" + (f" (第{{target_rank}}名)" if is_tgt and target_rank else (" (目标校)" if is_tgt else ""))
        col = '#c96442' if is_tgt else '#d97706'
        ax2.text(w + 0.15, bar.get_y() + bar.get_height()/2, txt,
                 va='center', ha='left', color=col, weight='bold', fontsize=10)

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(school_names, fontsize=10, color='#201f1d')
    ax2.set_xlabel('数字化综合得分', fontsize=10.5, color='#6f6c65')
    ax2.set_title('区域数字化领跑标杆学校 TOP', fontsize=12.5, weight='bold', pad=14, color='#201f1d')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.spines['left'].set_color('#dedbd2')
    ax2.spines['bottom'].set_color('#dedbd2')
    ax2.grid(axis='x', color='#f0eee8', linestyle='--', alpha=0.8)
    if school_scores and max(school_scores) > 0:
        ax2.set_xlim(0, max(school_scores) * 1.25)
    else:
        ax2.set_xlim(0, 30)

plt.suptitle('{region_name} · {year}年区域数字化全景透视 (参评学校共 {school_count} 所，大盘均分 {total_score}分)',
             fontsize=13.5, weight='bold', y=0.98, color='#201f1d')
plt.tight_layout(rect=[0, 0, 1, 0.95])
print("区域宏观综合透视图生成完成")
'''

    @staticmethod
    def build_school_compare_code(
        school_a: str,
        score_a: float,
        school_b: str,
        score_b: float,
        dimensions: List[str],
        values_a: List[float],
        values_b: List[float],
        year_desc: str = "",
    ) -> str:
        """生成双校数字化多维对比双联图（左：双线能力雷达；右：六维得分逐项分组对比柱状图）。"""
        dims_json = json.dumps(dimensions, ensure_ascii=False)
        vals_a_json = json.dumps([round(float(v), 2) for v in values_a])
        vals_b_json = json.dumps([round(float(v), 2) for v in values_b])
        school_a_str = repr(school_a)
        school_b_str = repr(school_b)
        year_desc_str = repr(year_desc)

        all_vals = list(values_a) + list(values_b)
        highest = max(all_vals) if all_vals else 20.0
        ceil_val = round(highest * 1.25) if highest > 10 else 20.0
        diff = round(score_a - score_b, 2)
        diff_str = f"{diff:+0.2f}"

        return f'''# 绘制双校数字化多维对比双联图：能力雷达 + 六维得分对照
import matplotlib.pyplot as plt
import numpy as np

dimensions = {dims_json}
values_a = {vals_a_json}
values_b = {vals_b_json}
school_a = {school_a_str}
school_b = {school_b_str}
score_a = {score_a}
score_b = {score_b}
year_desc = {year_desc_str}

fig = plt.figure(figsize=(12.0, 5.2), dpi=160)

# 子图1：双校能力对比雷达图
ax1 = fig.add_subplot(1, 2, 1, projection='polar')
N = len(dimensions)
if N > 0:
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles_closed = angles + angles[:1]
    vals_a_closed = values_a + values_a[:1]
    vals_b_closed = values_b + values_b[:1]

    ax1.set_theta_offset(np.pi / 2)
    ax1.set_theta_direction(-1)
    ax1.set_xticks(angles)
    ax1.set_xticklabels(dimensions, size=10.5, weight='bold', color='#201f1d')
    ax1.set_ylim(0, {ceil_val})
    ax1.tick_params(axis='y', colors='#6f6c65', labelsize=8.5)

    # A校 (品牌赤土色)
    ax1.plot(angles_closed, vals_a_closed, color='#c96442', linewidth=2.2, linestyle='solid', label=f'{{school_a}} ({{score_a}}分)')
    ax1.fill(angles_closed, vals_a_closed, color='#c96442', alpha=0.22)
    ax1.scatter(angles, values_a, color='#c96442', s=36, zorder=5)

    # B校 (科技蓝)
    ax1.plot(angles_closed, vals_b_closed, color='#3b82f6', linewidth=2.2, linestyle='solid', label=f'{{school_b}} ({{score_b}}分)')
    ax1.fill(angles_closed, vals_b_closed, color='#3b82f6', alpha=0.20)
    ax1.scatter(angles, values_b, color='#3b82f6', s=36, zorder=5)

    ax1.grid(color='#e8e6df', linestyle='--', linewidth=0.8, alpha=0.8)
    ax1.spines['polar'].set_color('#dedbd2')
ax1.set_title('六大维度能力轮廓对照', fontsize=12.5, weight='bold', pad=18, color='#201f1d')
ax1.legend(loc='upper right', bbox_to_anchor=(1.32, 1.14), frameon=True, facecolor='#ffffff', edgecolor='#e8e6df', fontsize=9.0)

# 子图2：分维度得分分组柱状图
ax2 = fig.add_subplot(1, 2, 2)
if dimensions:
    y_pos = np.arange(len(dimensions))
    h = 0.35
    bars_a = ax2.barh(y_pos - h/2, values_a, height=h, color='#c96442', label=f'{{school_a}}', alpha=0.9)
    bars_b = ax2.barh(y_pos + h/2, values_b, height=h, color='#3b82f6', label=f'{{school_b}}', alpha=0.9)

    for bar, val in zip(bars_a, values_a):
        w = bar.get_width()
        ax2.text(w + 0.4, bar.get_y() + bar.get_height()/2, f'{{val}}分', va='center', ha='left', color='#c96442', fontsize=8.5, weight='bold')

    for bar, val in zip(bars_b, values_b):
        w = bar.get_width()
        ax2.text(w + 0.4, bar.get_y() + bar.get_height()/2, f'{{val}}分', va='center', ha='left', color='#3b82f6', fontsize=8.5, weight='bold')

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(dimensions, fontsize=10, color='#201f1d')
    ax2.set_xlabel('数字化维度得分 (0~100)', fontsize=10.5, color='#6f6c65')
    ax2.set_title('各维度量化得分详细比对', fontsize=12.5, weight='bold', pad=14, color='#201f1d')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.spines['left'].set_color('#dedbd2')
    ax2.spines['bottom'].set_color('#dedbd2')
    ax2.grid(axis='x', color='#f0eee8', linestyle='--', alpha=0.8)
    all_vals_combined = values_a + values_b
    max_h = max(all_vals_combined) if all_vals_combined else 30.0
    ax2.set_xlim(0, max_h * 1.32 if max_h > 0 else 30.0)
    ax2.legend(loc='lower right', frameon=True, facecolor='#ffffff', edgecolor='#e8e6df', fontsize=9.0)

plt.suptitle(f'{{school_a}} VS {{school_b}} · 数字化综合水平对比 (综合分差 {diff_str}分 · {{year_desc}})',
             fontsize=13.0, weight='bold', y=0.98, color='#201f1d')
plt.tight_layout(rect=[0, 0, 1, 0.95])
print("双校对比全景图表生成完成")
'''

    @staticmethod
    def build_region_dimension_compare_code(
        region_a: str, score_a: float, schools_a: Optional[int],
        region_b: str, score_b: float, schools_b: Optional[int],
        dimension: str, year: str, school_filter: str = "all",
        difference: Optional[float] = None,
    ) -> str:
        """One dimension, one pair of vertical bars in question order."""
        from packages.agent.tools.chart_scope import comparison_filter_label
        filter_label = comparison_filter_label(school_filter)
        if filter_label != "全部学校":
            filter_label = f"登记为{filter_label}的学校"
        title = f"{year}年 · {filter_label} · {dimension}学校等权均分"
        display_difference = float(difference) if difference is not None else float(score_a) - float(score_b)
        difference_label = f"{region_a}减{region_b}：{display_difference:+.2f}分"
        return f'''import matplotlib.pyplot as plt
regions = {[region_a, region_b]!r}
scores = {[float(score_a), float(score_b)]!r}
counts = {[int(schools_a) if schools_a is not None else None, int(schools_b) if schools_b is not None else None]!r}
fig, ax = plt.subplots(figsize=(8.8, 5.4), dpi=160)
for index, (region, score, count, color) in enumerate(zip(regions, scores, counts, ['#c96442', '#3b82f6'])):
    ax.bar(index, score, width=0.55, color=color, label=f'{{region}}（有效学校{{count}}所）' if count is not None else region)
    ax.text(index, score + max(scores) * 0.025, f'{{score:.2f}}分', ha='center', va='bottom', weight='bold')
ax.set_xticks([0, 1], regions)
ax.set_ylabel({(dimension + '学校等权均分（分）')!r})
ax.set_title({title!r}, pad=16, weight='bold')
ax.set_ylim(0, max(max(scores) * 1.28, 1))
ax.set_xlim(-0.6, 1.6)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.grid(axis='y', linestyle='--', alpha=0.5)
ax.set_axisbelow(True)
ax.legend(loc='upper left', frameon=False)
fig.text(0.5, 0.025, {difference_label!r}, ha='center', weight='bold')
fig.tight_layout(rect=[0, 0.07, 1, 1])
print('单维区域比较图生成完成')
'''

    @staticmethod
    def build_region_overall_compare_code(
        region_a: str, score_a: float, schools_a: Optional[int],
        region_b: str, score_b: float, schools_b: Optional[int], year: str,
        difference: Optional[float] = None,
    ) -> str:
        """Plot only the two verified overall means requested for a region pair."""
        title = f"{year}年 · 整体数字化学校等权均分"
        display_difference = float(difference) if difference is not None else float(score_a) - float(score_b)
        difference_label = f"{region_a}减{region_b}：{display_difference:+.2f}分"
        return f'''import matplotlib.pyplot as plt
regions = {[region_a, region_b]!r}
scores = {[float(score_a), float(score_b)]!r}
counts = {[int(schools_a) if schools_a is not None else None, int(schools_b) if schools_b is not None else None]!r}
fig, ax = plt.subplots(figsize=(8.8, 5.4), dpi=160)
for index, (region, score, count, color) in enumerate(zip(regions, scores, counts, ['#c96442', '#3b82f6'])):
    ax.bar(index, score, width=0.55, color=color, label=f'{{region}}（有效学校{{count}}所）' if count is not None else region)
    ax.text(index, score + max(scores) * 0.025, f'{{score:.2f}}分', ha='center', va='bottom', weight='bold')
ax.set_xticks([0, 1], regions)
ax.set_ylabel('整体学校等权均分（分）')
ax.set_title({title!r}, pad=16, weight='bold')
ax.set_ylim(0, max(max(scores) * 1.28, 1))
ax.set_xlim(-0.6, 1.6)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.grid(axis='y', linestyle='--', alpha=0.5)
ax.set_axisbelow(True)
ax.legend(loc='upper left', frameon=False)
fig.text(0.5, 0.025, {difference_label!r}, ha='center', weight='bold')
fig.tight_layout(rect=[0, 0.07, 1, 1])
print('整体双区域比较图生成完成')
'''

    @staticmethod
    def build_region_compare_code(
        region_a: str,
        score_a: float,
        schools_a: Optional[int],
        region_b: str,
        score_b: float,
        schools_b: Optional[int],
        dimensions: List[str],
        values_a: List[float],
        values_b: List[float],
        year_desc: str = "",
    ) -> str:
        """生成双区域数字化宏观横向对比双联图（左：双区能力雷达；右：六维均分分组柱状图）。"""
        dims_json = json.dumps(dimensions, ensure_ascii=False)
        vals_a_json = json.dumps([round(float(v), 2) for v in values_a])
        vals_b_json = json.dumps([round(float(v), 2) for v in values_b])
        region_a_str = repr(region_a)
        region_b_str = repr(region_b)
        year_desc_str = repr(year_desc)

        all_vals = list(values_a) + list(values_b)
        highest = max(all_vals) if all_vals else 20.0
        ceil_val = round(highest * 1.25) if highest > 10 else 20.0
        diff = round(score_a - score_b, 2)
        diff_str = f"{diff:+0.2f}"

        return f'''# 绘制双区域数字化宏观横向对比双联图：能力轮廓雷达 + 六维均分对照
import matplotlib.pyplot as plt
import numpy as np

dimensions = {dims_json}
values_a = {vals_a_json}
values_b = {vals_b_json}
region_a = {region_a_str}
region_b = {region_b_str}
score_a = {score_a}
score_b = {score_b}
schools_a = {schools_a!r}
schools_b = {schools_b!r}
year_desc = {year_desc_str}

fig = plt.figure(figsize=(12.0, 5.2), dpi=160)

# 子图1：双区域能力对比雷达图
ax1 = fig.add_subplot(1, 2, 1, projection='polar')
N = len(dimensions)
if N > 0:
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles_closed = angles + angles[:1]
    vals_a_closed = values_a + values_a[:1]
    vals_b_closed = values_b + values_b[:1]

    ax1.set_theta_offset(np.pi / 2)
    ax1.set_theta_direction(-1)
    ax1.set_xticks(angles)
    ax1.set_xticklabels(dimensions, size=10.5, weight='bold', color='#201f1d')
    ax1.set_ylim(0, {ceil_val})
    ax1.tick_params(axis='y', colors='#6f6c65', labelsize=8.5)

    # 区域A (品牌赤土色)
    ax1.plot(angles_closed, vals_a_closed, color='#c96442', linewidth=2.2, linestyle='solid', label=f'{{region_a}} ({{score_a}}分 · {{schools_a}}校)' if schools_a is not None else f'{{region_a}} ({{score_a}}分)')
    ax1.fill(angles_closed, vals_a_closed, color='#c96442', alpha=0.22)
    ax1.scatter(angles, values_a, color='#c96442', s=36, zorder=5)

    # 区域B (科技蓝)
    ax1.plot(angles_closed, vals_b_closed, color='#3b82f6', linewidth=2.2, linestyle='solid', label=f'{{region_b}} ({{score_b}}分 · {{schools_b}}校)' if schools_b is not None else f'{{region_b}} ({{score_b}}分)')
    ax1.fill(angles_closed, vals_b_closed, color='#3b82f6', alpha=0.20)
    ax1.scatter(angles, values_b, color='#3b82f6', s=36, zorder=5)

    ax1.grid(color='#e8e6df', linestyle='--', linewidth=0.8, alpha=0.8)
    ax1.spines['polar'].set_color('#dedbd2')
ax1.set_title('区域数字化能力雷达轮廓对照', fontsize=12.5, weight='bold', pad=18, color='#201f1d')
ax1.legend(loc='upper right', bbox_to_anchor=(1.35, 1.14), frameon=True, facecolor='#ffffff', edgecolor='#e8e6df', fontsize=8.8)

# 子图2：分维度大盘均分分组柱状图
ax2 = fig.add_subplot(1, 2, 2)
if dimensions:
    y_pos = np.arange(len(dimensions))
    h = 0.35
    bars_a = ax2.barh(y_pos - h/2, values_a, height=h, color='#c96442', label=f'{{region_a}} (均分)', alpha=0.9)
    bars_b = ax2.barh(y_pos + h/2, values_b, height=h, color='#3b82f6', label=f'{{region_b}} (均分)', alpha=0.9)

    for bar, val in zip(bars_a, values_a):
        w = bar.get_width()
        ax2.text(w + 0.3, bar.get_y() + bar.get_height()/2, f'{{val}}分', va='center', ha='left', color='#c96442', fontsize=8.5, weight='bold')

    for bar, val in zip(bars_b, values_b):
        w = bar.get_width()
        ax2.text(w + 0.3, bar.get_y() + bar.get_height()/2, f'{{val}}分', va='center', ha='left', color='#3b82f6', fontsize=8.5, weight='bold')

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(dimensions, fontsize=10, color='#201f1d')
    ax2.set_xlabel('区域维度大盘均分 (0~100)', fontsize=10.5, color='#6f6c65')
    ax2.set_title('六大维度均值量化详细比对', fontsize=12.5, weight='bold', pad=14, color='#201f1d')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.spines['left'].set_color('#dedbd2')
    ax2.spines['bottom'].set_color('#dedbd2')
    ax2.grid(axis='x', color='#f0eee8', linestyle='--', alpha=0.8)
    all_vals_combined = values_a + values_b
    max_h = max(all_vals_combined) if all_vals_combined else 30.0
    ax2.set_xlim(0, max_h * 1.32 if max_h > 0 else 30.0)
    ax2.legend(loc='lower right', frameon=True, facecolor='#ffffff', edgecolor='#e8e6df', fontsize=9.0)

plt.suptitle(f'{{region_a}} VS {{region_b}} · 区域数字化发展宏观比对 (综合均分差 {diff_str}分 · {{year_desc}})',
             fontsize=13.0, weight='bold', y=0.98, color='#201f1d')
plt.tight_layout(rect=[0, 0, 1, 0.95])
print("双区域宏观比对图表生成完成")
'''

    @staticmethod
    def build_region_longitudinal_code(
        region_name: str,
        year_a: str,
        score_a: float,
        schools_a: int,
        year_b: str,
        score_b: float,
        schools_b: int,
        dimensions: List[str],
        values_a: List[float],
        values_b: List[float],
        diff: float,
        trend: str = "提升",
    ) -> str:
        """旧版未调用构图入口；保留兼容，不用于重复截面契约输出。"""
        dims_json = json.dumps(dimensions, ensure_ascii=False)
        vals_a_json = json.dumps([round(float(v), 2) for v in values_a])
        vals_b_json = json.dumps([round(float(v), 2) for v in values_b])
        region_name_str = repr(region_name)
        year_a_str = repr(str(year_a))
        year_b_str = repr(str(year_b))
        diff_str = f"{diff:+0.2f}"

        all_vals = list(values_a) + list(values_b)
        highest = max(all_vals) if all_vals else 20.0
        ceil_val = round(highest * 1.25) if highest > 10 else 20.0

        return f'''# 旧版区域两期多维对照（非重复截面契约输出）：能力轮廓雷达 + 六维均分对照
import matplotlib.pyplot as plt
import numpy as np

dimensions = {dims_json}
values_a = {vals_a_json}
values_b = {vals_b_json}
region_name = {region_name_str}
year_a = {year_a_str}
year_b = {year_b_str}
score_a = {score_a}
score_b = {score_b}
schools_a = {schools_a}
schools_b = {schools_b}

fig = plt.figure(figsize=(12.0, 5.2), dpi=160)

# 子图1：两期能力轮廓对比雷达图
ax1 = fig.add_subplot(1, 2, 1, projection='polar')
N = len(dimensions)
if N > 0:
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles_closed = angles + angles[:1]
    vals_a_closed = values_a + values_a[:1]
    vals_b_closed = values_b + values_b[:1]

    ax1.set_theta_offset(np.pi / 2)
    ax1.set_theta_direction(-1)
    ax1.set_xticks(angles)
    ax1.set_xticklabels(dimensions, size=10.5, weight='bold', color='#201f1d')
    ax1.set_ylim(0, {ceil_val})
    ax1.tick_params(axis='y', colors='#6f6c65', labelsize=8.5)

    # 基准年（琥珀黄）
    ax1.plot(angles_closed, vals_a_closed, color='#d97706', linewidth=2.0, linestyle='--', label=f'{{year_a}}年 ({{score_a}}分/{{schools_a}}校)')
    ax1.fill(angles_closed, vals_a_closed, color='#d97706', alpha=0.15)
    ax1.scatter(angles, values_a, color='#d97706', s=32, zorder=5)

    # 目标年（品牌赤土色）
    ax1.plot(angles_closed, vals_b_closed, color='#c96442', linewidth=2.2, linestyle='solid', label=f'{{year_b}}年 ({{score_b}}分/{{schools_b}}校)')
    ax1.fill(angles_closed, vals_b_closed, color='#c96442', alpha=0.25)
    ax1.scatter(angles, values_b, color='#c96442', s=36, zorder=5)

    ax1.grid(color='#e8e6df', linestyle='--', linewidth=0.8, alpha=0.8)
    ax1.spines['polar'].set_color('#dedbd2')
ax1.set_title('六大维度能力轮廓演进', fontsize=12.5, weight='bold', pad=18, color='#201f1d')
ax1.legend(loc='upper right', bbox_to_anchor=(1.32, 1.14), frameon=True, facecolor='#ffffff', edgecolor='#e8e6df', fontsize=9.0)

# 子图2：六大维度得分跨期对照分组柱状图
ax2 = fig.add_subplot(1, 2, 2)
if dimensions:
    y_pos = np.arange(len(dimensions))
    h = 0.35
    bars_a = ax2.barh(y_pos - h/2, values_a, height=h, color='#d97706', label=f'{{year_a}}年', alpha=0.85)
    bars_b = ax2.barh(y_pos + h/2, values_b, height=h, color='#c96442', label=f'{{year_b}}年', alpha=0.9)

    for bar, val in zip(bars_a, values_a):
        w = bar.get_width()
        ax2.text(w + 0.3, bar.get_y() + bar.get_height()/2, f'{{val}}分', va='center', ha='left', color='#d97706', fontsize=8.5, weight='bold')

    for bar, val in zip(bars_b, values_b):
        w = bar.get_width()
        ax2.text(w + 0.3, bar.get_y() + bar.get_height()/2, f'{{val}}分', va='center', ha='left', color='#c96442', fontsize=8.5, weight='bold')

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(dimensions, fontsize=10, color='#201f1d')
    ax2.set_xlabel('区域维度大盘均分 (0~100)', fontsize=10.5, color='#6f6c65')
    ax2.set_title('六大维度均值跨期详细比对', fontsize=12.5, weight='bold', pad=14, color='#201f1d')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.spines['left'].set_color('#dedbd2')
    ax2.spines['bottom'].set_color('#dedbd2')
    ax2.grid(axis='x', color='#f0eee8', linestyle='--', alpha=0.8)
    all_vals_combined = values_a + values_b
    max_h = max(all_vals_combined) if all_vals_combined else 30.0
    ax2.set_xlim(0, max_h * 1.32 if max_h > 0 else 30.0)
    ax2.legend(loc='lower right', frameon=True, facecolor='#ffffff', edgecolor='#e8e6df', fontsize=9.0)

plt.suptitle(f'{{region_name}} · 年度截面多维对照（非同校追踪） ({{year_a}}年 {{score_a}}分 vs {{year_b}}年 {{score_b}}分 · 变动 {diff_str}分)',
             fontsize=13.0, weight='bold', y=0.98, color='#201f1d')
plt.tight_layout(rect=[0, 0, 1, 0.95])
print("旧版区域两期多维对照图表生成完成（非契约输出）")
'''

    @staticmethod
    def build_region_period_change_code(
        region_name: str,
        year_a: str,
        value_a: float,
        schools_a: Optional[int],
        answers_a: Optional[int],
        year_b: str,
        value_b: float,
        schools_b: Optional[int],
        answers_b: Optional[int],
        value_unit: str,
        diff: float,
        trend: str,
        school_filter: str = "全部学校",
        target_dimension: str = "",
    ) -> str:
        """生成两期重复截面主比较值图，不混入契约范围外的维度结论。"""
        from packages.agent.tools.chart_scope import comparison_filter_label
        filter_label = comparison_filter_label(school_filter)
        return f'''# 绘制单区域两期年度重复截面变化图
import matplotlib.pyplot as plt

region_name = {region_name!r}
years = [{str(year_a)!r}, {str(year_b)!r}]
values = [{round(float(value_a), 2)}, {round(float(value_b), 2)}]
schools = [{schools_a!r}, {schools_b!r}]
answers = [{answers_a!r}, {answers_b!r}]
unit = {value_unit!r}
diff = {round(float(diff), 2)}
trend = {trend!r}
school_filter = {filter_label!r}
target_dimension = {str(target_dimension)!r}

scope_label = f'{{school_filter}}学校' if school_filter != '全部学校' else school_filter
metric_label = f'【{{target_dimension}}】学校等权均分' if target_dimension else '主比较值'
filter_note = (f'{{years[0]}}年、{{years[1]}}年各按当年登记为{{school_filter}}的学校筛选'
               if school_filter != '全部学校' else '两个年度各按当年登记学校独立筛选')

fig, ax = plt.subplots(figsize=(8.8, 5.0), dpi=160)
bars = ax.bar(years, values, width=0.52, color=['#d97706', '#c96442'])
upper = max(values) if values else 0
padding = max(abs(upper) * 0.08, 0.8)
for bar, value in zip(bars, values):
    if value == 0:
        # 零值不抬成伪非零柱；在真实 y=0 位置画可见横帽，避免与坐标轴混淆。
        ax.plot([bar.get_x(), bar.get_x() + bar.get_width()], [0, 0],
                color=bar.get_facecolor(), linewidth=6, solid_capstyle='round',
                zorder=5, clip_on=False)
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + padding * 0.2,
            f'{{value:g}}{{unit}}', ha='center', va='bottom', fontsize=12, weight='bold')
tick_labels = []
for year, school_count, answer_count in zip(years, schools, answers):
    label = year
    if school_count is not None:
        label += f'\\n有效学校 {{school_count}} 所'
    if answer_count is not None:
        label += f'\\n有效作答 {{answer_count}} 条'
    tick_labels.append(label)
ax.set_xticks(range(len(years)), tick_labels)
ax.set_ylabel(f'主比较值（{{unit}}）', fontsize=10.5, color='#6f6c65')
ax.set_title(f'{{region_name}} · {{scope_label}} · {{metric_label}}\\n{{years[1]}}年减{{years[0]}}年 {{diff:+g}}{{unit}}（{{trend}}）',
             fontsize=13, weight='bold', color='#201f1d', pad=14)
fig.text(0.5, 0.025, f'注：{{filter_note}}；本图不表示追踪同一批学校。',
         ha='center', va='bottom', fontsize=9, color='#6f6c65')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.spines['left'].set_color('#dedbd2')
ax.spines['bottom'].set_color('#dedbd2')
ax.grid(axis='y', color='#f0eee8', linestyle='--', alpha=0.8)
ax.set_ylim(0, max(upper + padding * 2.2, padding * 2.2))
plt.tight_layout(rect=[0, 0.13, 1, 1])
print("区域两期年度重复截面变化图生成完成")
'''

    @staticmethod
    def build_national_ranking_code(
        title: str,
        year: str,
        target_dimension: Optional[str],
        provinces: List[str],
        scores: List[float],
        school_counts: List[int],
        tiers: List[str],
    ) -> str:
        """生成全国各省份教育数字化水平横向排行榜条形图代码。"""
        prov_json = json.dumps(provinces, ensure_ascii=False)
        scores_json = json.dumps([round(float(s), 2) for s in scores])
        counts_json = json.dumps([int(c) for c in school_counts])
        tiers_json = json.dumps(tiers, ensure_ascii=False)
        dim_label = repr(target_dimension) if target_dimension else "None"

        return f'''# 绘制全国各省份教育数字化水平横向排行榜柱状图
import matplotlib.pyplot as plt
import numpy as np

provinces = {prov_json}
scores = {scores_json}
counts = {counts_json}
tiers = {tiers_json}
target_dim = {dim_label}

# 反转使得排名第 1 的省份在水平条形图最上方
y_names = provinces[::-1]
y_scores = scores[::-1]
y_counts = counts[::-1]
y_tiers = tiers[::-1]

fig, ax = plt.subplots(figsize=(9.6, 6.2), dpi=160)
fig.patch.set_facecolor('#ffffff')
ax.set_facecolor('#faf9f6')

y_pos = np.arange(len(y_names))

# 颜色编码：规模化样本用品牌橙红，常态化用深蓝，小微样本用冷灰
colors = []
for tier in y_tiers:
    if tier == '规模化样本':
        colors.append('#c96442')
    elif tier == '常态化样本':
        colors.append('#2563eb')
    else:
        colors.append('#9ca3af')

bars = ax.barh(y_pos, y_scores, color=colors, height=0.62, edgecolor='none', zorder=3)

# 标注柱上数据与样本量
for bar, sc, cnt, tier in zip(bars, y_scores, y_counts, y_tiers):
    width = bar.get_width()
    tag = f"{{sc:.2f}}分 (样本: {{cnt}}所, {{tier}})"
    ax.text(width + 0.12, bar.get_y() + bar.get_height() / 2, tag,
            va='center', ha='left', fontsize=8.5, color='#374151', weight='bold')

ax.set_yticks(y_pos)
ax.set_yticklabels(y_names, fontsize=10, weight='bold', color='#1f2937')
max_val = max(scores) if scores else 15
ax.set_xlim(0, max_val * 1.35)

ax.grid(axis='x', linestyle='--', alpha=0.5, color='#d1d5db', zorder=0)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.spines['left'].set_color('#9ca3af')
ax.spines['bottom'].set_color('#9ca3af')

metric_str = f"【{{target_dim}}】维度" if target_dim else "数字化综合均分"
plt.title(f"{title}\\n(橙色: 规模化样本≥1000所 | 蓝色: 常态化100~999所 | 灰色: 小微样本<100所)", 
          fontsize=12.5, weight='bold', pad=12, color='#111827')
plt.xlabel(f"省域监测{{metric_str}}（分）", fontsize=10.5, color='#4b5563', labelpad=8)
plt.tight_layout()
print("全国省域排行榜柱状图生成完成")
'''
