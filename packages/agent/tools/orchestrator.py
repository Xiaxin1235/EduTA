# -*- coding: utf-8 -*-
"""AgentToolOrchestrator · Agent 工具调用编排执行器。

职责：
1. 分析评估结果对象类型（微观事实/排位对比/区域宏观/整校评估/教师评估）；
2. 调度 EducationalChartBuilder 自动编写 Matplotlib 绘图脚本；
3. 调度 ToolRegistry / PythonInterpreterTool 执行代码并捕获高清图表物料；
4. 组装标准化的 tool_calls 轨迹与 charts 产物，无缝注入 API 报告。
"""

from typing import Any, Dict, List, Optional
import json
import math
import re
from packages.agent.tools.chart_builder import EducationalChartBuilder
from packages.agent.tools.registry import default_registry


class AgentToolOrchestrator:
    """Agent 工具调用编排器。"""

    def __init__(self, registry=None):
        self.registry = registry or default_registry
        self.chart_builder = EducationalChartBuilder()

    def orchestrate_visualization(
        self, result: Dict[str, Any], question: Optional[str] = None,
        answer_spec: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """对评估结果进行工具调用编排，自动生成 Python 作图任务并执行。

        返回:
          {
            "tool_calls": List[dict],
            "charts": List[dict],
          }
        """
        tool_calls = []
        charts = []

        overview = result.get("overview") or result
        obj_type = overview.get("object_type") or "teacher"
        if overview.get("no_chart") or result.get("no_chart") or obj_type == "multi_school_compare":
            return {"tool_calls": tool_calls, "charts": charts}

        code = ""
        chart_title = ""

        # 场景 1：学校微观指标客观事实（如平板电脑台数）
        if obj_type == "structured_task":
            from packages.semantic_query.public_tasks import chart_code
            code = chart_code(result, answer_spec)
            chart_title = "所问对象与统计量"
            if not code:
                return {"tool_calls": [], "charts": []}
        elif obj_type == "school_fact":
            if (
                (overview.get("fact_type") or result.get("fact_type")) == "question_set"
                or overview.get("granularity") == "item_set"
            ):
                items = overview.get("item_results") or result.get("item_results") or []
                if (overview.get("fact_data_status") or result.get("fact_data_status")) != "ANSWER" or len(items) != 2:
                    return {"tool_calls": tool_calls, "charts": charts}
                if any(item.get("target_value") is None for item in items):
                    return {"tool_calls": tool_calls, "charts": charts}
                if any(item.get("coding_status") == "source_missing" for item in items):
                    return {"tool_calls": tool_calls, "charts": charts}
                if len({str(item.get("unit") or "") for item in items}) != 1:
                    return {"tool_calls": tool_calls, "charts": charts}
                derived = overview.get("derived_result") or result.get("derived_result") or {}
                include_total = bool((answer_spec or {}).get("chart_derived_result"))
                include_ratio = bool((answer_spec or {}).get("chart_derived_ratio"))
                if include_total and (
                    derived.get("operation") != "add"
                    or derived.get("value") is None
                    or str(derived.get("unit") or "") != str(items[0].get("unit") or "")
                    or abs(float(derived["value"]) - sum(float(item["target_value"]) for item in items)) > 1e-6
                ):
                    return {"tool_calls": tool_calls, "charts": charts}
                if include_ratio and (
                    derived.get("operation") != "divide" or derived.get("value") is None
                    or not all(math.isfinite(float(v)) for v in [derived.get("value"), items[0]["target_value"], items[1]["target_value"]])
                    or derived.get("left_qid") != items[0].get("question_id")
                    or derived.get("right_qid") != items[1].get("question_id")
                    or float(items[1]["target_value"]) == 0
                    or abs(float(derived["value"]) - float(items[0]["target_value"]) / float(items[1]["target_value"])) > 1e-6
                ):
                    return {"tool_calls": tool_calls, "charts": charts}
                chart_title = (
                    f"{overview.get('school_name') or result.get('school_name') or '目标学校'} · 分项与合计"
                    if include_total else
                    f"{overview.get('school_name') or result.get('school_name') or '目标学校'} · 两项数值"
                )
                code = self.chart_builder.build_school_item_set_code(
                    chart_title, items, derived if include_total or include_ratio else None
                )
            else:
                if overview.get("fact_data_status") == "NO_DATA" or overview.get("target_value") is None:
                    return {"tool_calls": tool_calls, "charts": charts}
                school_name = overview.get("school_name") or "目标学校"
                metric_name = overview.get("metric_name") or "核查指标"
                val = overview.get("target_value") or 0
                unit = overview.get("unit") or "台"
                context_facts = result.get("context_facts") or []
                history = result.get("history") or []

                chart_title = f"{school_name} · {metric_name} 配置对比"
                code = self.chart_builder.build_equipment_fact_code(
                    school_name=school_name,
                    metric_name=metric_name,
                    target_value=val,
                    unit=unit,
                    context_facts=context_facts,
                    history=history,
                )

        # 场景 2：区域学校相对排位与综合对比
        elif obj_type == "school_ranking":
            school_name = overview.get("school_name") or "目标学校"
            target_score = overview.get("total_score") or 0.0
            region_avg = overview.get("region_avg") or 0.0
            peers = result.get("peer_schools") or []
            rank = overview.get("rank") or 1
            total_schools = overview.get("total_schools") or 1

            chart_title = f"{school_name} · 区域排位与梯队对比"
            code = self.chart_builder.build_ranking_code(
                title=chart_title,
                school_name=school_name,
                target_score=target_score,
                region_avg=region_avg,
                peer_schools=peers,
                rank=rank,
                total_schools=total_schools,
            )

        # 场景 3：区域宏观数字化评估（如武汉市、新兴区大盘）
        elif obj_type == "region":
            is_region_ranking = overview.get("analysis_operation") == "region_ranking"
            contract = overview.get("contract") or result.get("contract") or {}
            execution_receipt = overview.get("execution_receipt") or result.get("execution_receipt") or {}
            is_region_item = (
                overview.get("metric_type") == "question"
                or result.get("metric_type") == "question"
                or contract.get("metric_type") == "question"
                or execution_receipt.get("query_template") == "region_item_mean_v1"
            )
            is_single_dimension_bar = (
                not is_region_item
                and overview.get("granularity") == "dimension"
                and overview.get("target_stat") == "mean"
                and bool(overview.get("target_dimension"))
                and bool(re.search(r"一根柱|单柱|柱状图|柱子", str(question or "")))
            )
            reg_name = overview.get("region_name") or "评估区域"
            years = overview.get("school_years") or result.get("school_years") or []
            year = str(overview.get("year") or (years[0] if years else "2021"))
            total_score = overview.get("total_score") or 0.0
            count = overview.get("school_count") or 0
            dims_dict = {}
            for d in result.get("dimensions", []):
                dims_dict[d.get("dimension", "")] = float(d.get("score", 0.0))

            if overview.get("target_stat") == "count" or overview.get("output_focus") == "school_count":
                from packages.semantic_query.public_tasks import bar_code
                chart_title = f"{reg_name} · {year}年有效学校数"
                code = bar_code([{'label':reg_name+'/'+year, 'value':overview.get('school_count'), 'unit':'所'}],chart_title)
            elif is_region_item:
                metric_name = (
                    overview.get("metric_name")
                    or result.get("metric_name")
                    or contract.get("metric_name")
                    or "目标题项"
                )
                target_stat = (
                    overview.get("target_stat")
                    or result.get("target_stat")
                    or contract.get("statistic")
                )
                unit = (
                    overview.get("unit")
                    or result.get("unit")
                    or (contract.get("metric_spec") or {}).get("unit")
                    or ("Mbps" if "Mbps" in str(overview) or "Mbps" in str(result) or "Mbps" in str(question or "") else "分")
                )
                if target_stat in ("mean_and_median", "median_and_mean"):
                    mean_val = float(overview.get("total_score") if overview.get("total_score") is not None else (result.get("total_score") if result.get("total_score") is not None else (overview.get("normalized_score") or 0.0)))
                    med_val = float(overview.get("median_score") if overview.get("median_score") is not None else (result.get("median_score") or 0.0))
                    labels = ["平均值", "中位数"]
                    values = [round(mean_val, 2), round(med_val, 2)]
                    chart_title = f"{reg_name} · {year}年{metric_name}平均值与中位数对比"
                    code = f'''import matplotlib.pyplot as plt
import json
labels = {json.dumps(labels, ensure_ascii=False)}
values = {json.dumps(values)}
unit = {json.dumps(unit, ensure_ascii=False)}
fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=160)
bars = ax.bar([0, 1], values, color=['#c96442', '#357a91'], width=0.45)
ax.set_xticks([0, 1], labels)
ax.set_ylabel(f"数值（{{unit}}）", fontsize=10.5)
ax.set_title({json.dumps(chart_title, ensure_ascii=False)}, fontsize=12, weight='bold')
ax.set_ylim(0, max(values) * 1.25 if max(values) > 0 else 20)
for bar, value in zip(bars, values):
    ax.annotate(f"{{value:g}}{{unit}}", (bar.get_x() + bar.get_width()/2, bar.get_height()),
                xytext=(0, 4), textcoords='offset points', ha='center', va='bottom', fontsize=11, weight='bold')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.tight_layout()
'''
                else:
                    item_score = (
                        overview.get("normalized_score")
                        if overview.get("normalized_score") is not None
                        else result.get("normalized_score")
                    )
                    if item_score is None:
                        item_score = overview.get("target_value") or result.get("target_value") or 0.0
                    answer_count = overview.get("answer_count") or result.get("answer_count") or 0
                    chart_title = f"{reg_name} · {metric_name} 归一均分"
                    code = self.chart_builder.build_region_item_code(
                        region_name=reg_name,
                        year=year,
                        metric_name=metric_name,
                        score=float(item_score),
                        school_count=int(count),
                        answer_count=int(answer_count),
                    )
            elif len(overview.get("target_dimensions") or result.get("target_dimensions") or []) >= 2:
                target_dims = overview.get("target_dimensions") or result.get("target_dimensions")
                d1, d2 = target_dims[0], target_dims[1]
                s1 = dims_dict.get(d1)
                s2 = dims_dict.get(d2)
                labels = [d1, d2]
                values = [round(float(s1), 2) if s1 is not None else 0.0, round(float(s2), 2) if s2 is not None else 0.0]
                chart_title = f"{reg_name} · {year}年【{d1}】与【{d2}】等权均分对比"
                code = f'''import matplotlib.pyplot as plt
import json
labels = {json.dumps(labels, ensure_ascii=False)}
values = {json.dumps(values)}
fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=160)
bars = ax.bar([0, 1], values, color=['#357a91', '#d18442'], width=0.45)
ax.set_xticks([0, 1], labels)
ax.set_ylabel("学校等权均分（分）", fontsize=10.5)
ax.set_title({json.dumps(chart_title, ensure_ascii=False)}, fontsize=12, weight='bold')
ax.set_ylim(0, max(values) * 1.25 if max(values) > 0 else 20)
for bar, value in zip(bars, values):
    ax.annotate(f"{{value:g}}分", (bar.get_x() + bar.get_width()/2, bar.get_height()),
                xytext=(0, 4), textcoords='offset points', ha='center', va='bottom', fontsize=11, weight='bold')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.tight_layout()
'''
            elif is_single_dimension_bar:
                dimension = str(overview["target_dimension"])
                score = overview.get("target_dimension_score")
                if score is None:
                    score = dims_dict.get(dimension)
                if score is not None:
                    from packages.agent.tools.chart_scope import comparison_filter_label
                    filter_label = comparison_filter_label(overview.get("school_filter"))
                    chart_title = f"{reg_name} · {year}年 · {filter_label} · {dimension}学校等权均分"
                    code = self.chart_builder.build_region_dimension_bar_code(
                        region_name=reg_name, year=year, dimension=dimension,
                        score=float(score), school_filter=filter_label,
                        school_count=int(count),
                        answer_count=int(overview.get("answer_count") or 0),
                    )
            elif (
                overview.get("target_stat") in ("min_and_max", "max_and_min")
                or contract.get("statistic") in ("min_and_max", "max_and_min")
                or result.get("target_stat") in ("min_and_max", "max_and_min")
            ):
                max_val = float(overview.get("max_score") or result.get("max_score") or 0.0)
                min_val = float(overview.get("min_score") or result.get("min_score") or 0.0)
                labels = ["高端 (最高分)", "低端 (最低分)"]
                values = [round(max_val, 2), round(min_val, 2)]
                chart_title = f"{reg_name} · {year}年学校数字化综合得分高端与低端对照"
                code = f'''import matplotlib.pyplot as plt
import json
labels = {json.dumps(labels, ensure_ascii=False)}
values = {json.dumps(values)}
fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=160)
bars = ax.bar([0, 1], values, color=['#c96442', '#357a91'], width=0.45)
ax.set_xticks([0, 1], labels)
ax.set_ylabel("得分（分）", fontsize=10.5)
ax.set_title({json.dumps(chart_title, ensure_ascii=False)}, fontsize=12, weight='bold')
ax.set_ylim(0, max(values) * 1.25 if max(values) > 0 else 20)
for bar, value in zip(bars, values):
    ax.annotate(f"{{value:g}}分", (bar.get_x() + bar.get_width()/2, bar.get_height()),
                xytext=(0, 4), textcoords='offset points', ha='center', va='bottom', fontsize=11, weight='bold')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.tight_layout()
'''
            elif is_region_ranking:
                ranking_items = result.get("ranking_items") or overview.get("ranking_items") or []
                top_n = overview.get("target_top_n") or result.get("target_top_n") or len(ranking_items)
                chart_title = f"{reg_name} · {year}年学校数字化综合得分前{top_n}名"
                code = self.chart_builder.build_region_ranking_code(
                    title=chart_title,
                    ranking_items=ranking_items,
                    eligible_school_count=int(count),
                )
            else:
                top_schools = (
                    result.get("top_schools") or []
                )
                target_school = result.get("target_school") or {}
                target_school_name = target_school.get("school_name") if overview.get("target_rank") else None
                target_rank = target_school.get("rank") if overview.get("target_rank") else None
                chart_title = (
                    f"{reg_name} · 区域宏观数字化综合透视"
                )
                code = self.chart_builder.build_region_macro_code(
                    region_name=reg_name,
                    year=year,
                    total_score=total_score,
                    school_count=count,
                    dimension_scores=dims_dict,
                    top_schools=top_schools,
                    target_school_name=target_school_name,
                    target_rank=target_rank,
                )

        # 场景 3.5：双校数字化多维对比评估
        elif obj_type == "school_compare":
            name_a = overview.get("school_name_a") or result.get("school_name_a") or "学校A"
            name_b = overview.get("school_name_b") or result.get("school_name_b") or "学校B"
            tot_a = float(overview.get("total_score_a") or result.get("total_score_a") or 0.0)
            tot_b = float(overview.get("total_score_b") or result.get("total_score_b") or 0.0)
            year_desc = str(overview.get("year_desc") or result.get("year_desc") or "")

            dims_comp = result.get("dimensions_comparison") or []
            dimensions = [dc.get("dimension", "") for dc in dims_comp]
            vals_a = [float(dc.get("score_a", 0.0)) for dc in dims_comp]
            vals_b = [float(dc.get("score_b", 0.0)) for dc in dims_comp]

            chart_title = f"{name_a} VS {name_b} · 数字化发展多维对比"
            year_a = str(overview.get("year_a") or result.get("year_a") or "")
            year_b = str(overview.get("year_b") or result.get("year_b") or "")
            q_str = str(question or "")
            target_dimension = str(result.get("target_dimension") or overview.get("target_dimension") or "").strip()
            if target_dimension:
                labels = [name_a, name_b]
                values = [round(tot_a, 2), round(tot_b, 2)]
                chart_title = f"{year_a}年 · {target_dimension}维度 · 双校得分比较"
                code = f'''import matplotlib.pyplot as plt
import json
labels = {json.dumps(labels, ensure_ascii=False)}
values = {json.dumps(values)}
fig, ax = plt.subplots(figsize=(9.0, 4.7), dpi=160)
bars = ax.bar([0, 1], values, color=['#357a91', '#d18442'], width=0.45)
ax.set_xticks([0, 1], labels)
ax.set_ylabel("维度得分（分）", fontsize=10.5)
ax.set_title({json.dumps(chart_title, ensure_ascii=False)}, fontsize=12, weight='bold')
ax.set_ylim(0, max(values) * 1.25 if max(values) > 0 else 20)
for bar, value in zip(bars, values):
    ax.annotate(f"{{value:g}}分", (bar.get_x() + bar.get_width()/2, bar.get_height()),
                xytext=(0, 4), textcoords='offset points', ha='center', va='bottom', fontsize=11, weight='bold')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.tight_layout()
'''
            elif name_a == name_b and any(k in q_str for k in ("柱", "两根柱子", "柱状图")):
                labels = [f"{year_a}年", f"{year_b}年"]
                values = [round(tot_a, 2), round(tot_b, 2)]
                code = f'''import matplotlib.pyplot as plt
import json
labels = {json.dumps(labels, ensure_ascii=False)}
values = {json.dumps(values)}
fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=160)
bars = ax.bar([0, 1], values, color=['#357a91', '#d18442'], width=0.45)
ax.set_xticks([0, 1], labels)
ax.set_ylabel("数字化办学得分（分）", fontsize=10.5)
ax.set_title({json.dumps(f"{name_a} {year_a}与{year_b}年度得分对比", ensure_ascii=False)}, fontsize=12, weight='bold')
ax.set_ylim(0, max(values) * 1.25 if max(values) > 0 else 20)
for bar, value in zip(bars, values):
    ax.annotate(f"{{value:g}}分", (bar.get_x() + bar.get_width()/2, bar.get_height()),
                xytext=(0, 4), textcoords='offset points', ha='center', va='bottom', fontsize=11, weight='bold')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.tight_layout()
'''
            elif dimensions:
                code = self.chart_builder.build_school_compare_code(
                    school_a=name_a if name_a != name_b else f"{name_a}({year_a}年)",
                    score_a=tot_a,
                    school_b=name_b if name_a != name_b else f"{name_b}({year_b}年)",
                    score_b=tot_b,
                    dimensions=dimensions,
                    values_a=vals_a,
                    values_b=vals_b,
                    year_desc=year_desc,
                )

        # 场景 3.55：双校具体指标事实对比
        elif obj_type == "school_item_compare":
            name_a = overview.get("school_name_a") or result.get("school_name_a") or "学校A"
            name_b = overview.get("school_name_b") or result.get("school_name_b") or "学校B"
            if overview.get("val_a") is None or overview.get("val_b") is None:
                return {"tool_calls": tool_calls, "charts": charts}
            val_a = float(overview["val_a"])
            val_b = float(overview["val_b"])
            metric_name = overview.get("metric_name") or "指标数值"
            unit = overview.get("unit") or ""
            year = str(overview.get("year") or "")
            labels = [name_a, name_b]
            values = [round(val_a, 2), round(val_b, 2)]
            chart_title = f"{name_a} VS {name_b} · {year}年{metric_name}对比"
            code = f'''import matplotlib.pyplot as plt
import json
labels = {json.dumps(labels, ensure_ascii=False)}
values = {json.dumps(values)}
unit = {json.dumps(unit, ensure_ascii=False)}
fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=160)
bars = ax.bar([0, 1], values, color=['#357a91', '#d18442'], width=0.45)
ax.set_xticks([0, 1], labels)
ax.set_ylabel(f"数值（{{unit}}）", fontsize=10.5)
ax.set_title({json.dumps(chart_title, ensure_ascii=False)}, fontsize=12, weight='bold')
ax.set_ylim(0, max(values) * 1.25 if max(values) > 0 else 20)
for bar, value in zip(bars, values):
    ax.annotate(f"{{value:g}}{{unit}}", (bar.get_x() + bar.get_width()/2, bar.get_height()),
                xytext=(0, 4), textcoords='offset points', ha='center', va='bottom', fontsize=11, weight='bold')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.tight_layout()
'''

        # 场景 3.6：双区域宏观数字化横向对比评估
        elif obj_type == "region_compare":
            name_a = overview.get("region_name_a") or result.get("region_name_a") or "区域A"
            name_b = overview.get("region_name_b") or result.get("region_name_b") or "区域B"
            # These are the contract-bound selected values.  A broader overall
            # score or absent sample count must never replace a dimension value.
            selected_a = overview.get("comparison_value_a")
            selected_b = overview.get("comparison_value_b")
            if answer_spec is None:
                # Legacy callers predate the selected-value public projection.
                selected_a = selected_a if selected_a is not None else overview.get("total_score_a")
                selected_b = selected_b if selected_b is not None else overview.get("total_score_b")
            if selected_a is None or selected_b is None:
                raise ValueError("region_compare_chart_values_missing")
            tot_a, tot_b = float(selected_a), float(selected_b)
            raw_count_a = overview.get("school_count_a")
            raw_count_b = overview.get("school_count_b")
            cnt_a = int(raw_count_a) if raw_count_a is not None else None
            cnt_b = int(raw_count_b) if raw_count_b is not None else None
            year_desc = str(overview.get("year_desc") or result.get("year_desc") or "")

            dims_comp = result.get("dimensions_comparison") or []
            dimensions = [dc.get("dimension", "") for dc in dims_comp]
            vals_a = [float(dc.get("score_a", 0.0)) for dc in dims_comp]
            vals_b = [float(dc.get("score_b", 0.0)) for dc in dims_comp]

            from packages.agent.tools.chart_scope import dimension_compare_scope, comparison_filter_label
            scope = dimension_compare_scope(overview)
            chart_title = f"{name_a} VS {name_b} · 区域数字化发展宏观比对"
            if overview.get("target_stat") == "count" or overview.get("output_focus") == "school_count":
                from packages.semantic_query.public_tasks import bar_code
                chart_title = f"{name_a}与{name_b} · {overview.get('year') or ''}年有效学校数"
                code = bar_code([{'label':name_a, 'value':tot_a, 'unit':'所'}, {'label':name_b, 'value':tot_b, 'unit':'所'}],chart_title)
            elif scope:
                if not scope["year"]:
                    raise ValueError("dimension_compare_year_required")
                filter_label = comparison_filter_label(scope["filter"])
                chart_title = f"{name_a} → {name_b} · {scope['year']}年 · {filter_label} · {scope['dimension']}均分"
                code = self.chart_builder.build_region_dimension_compare_code(
                    region_a=name_a, score_a=tot_a, schools_a=cnt_a,
                    region_b=name_b, score_b=tot_b, schools_b=cnt_b,
                    dimension=scope["dimension"], year=scope["year"], school_filter=scope["filter"],
                    difference=scope["diff"],
                )
            elif dimensions:
                code = self.chart_builder.build_region_compare_code(
                    region_a=name_a,
                    score_a=tot_a,
                    schools_a=cnt_a,
                    region_b=name_b,
                    score_b=tot_b,
                    schools_b=cnt_b,
                    dimensions=dimensions,
                    values_a=vals_a,
                    values_b=vals_b,
                    year_desc=year_desc,
                )
            else:
                chart_title = f"{name_a} → {name_b} · {overview.get('year') or ''}年 · 整体学校等权均分"
                code = self.chart_builder.build_region_overall_compare_code(
                    region_a=name_a, score_a=tot_a, schools_a=cnt_a,
                    region_b=name_b, score_b=tot_b, schools_b=cnt_b,
                    year=str(overview.get("year") or ""),
                    difference=overview.get("diff"),
                )

        # 场景 3.7：单区域两期年度重复截面变化
        elif obj_type == "region_longitudinal":
            reg_name = overview.get("region_name") or result.get("region_name") or "目标区域"
            y_a = str(overview.get("year_a") or result.get("year_a") or "基准年")
            y_b = str(overview.get("year_b") or result.get("year_b") or "目标年")
            tot_a = float(overview.get("comparison_value_a") if overview.get("comparison_value_a") is not None else (result.get("comparison_value_a") or 0.0))
            tot_b = float(overview.get("comparison_value_b") if overview.get("comparison_value_b") is not None else (result.get("comparison_value_b") or 0.0))
            cnt_a = int(overview.get("school_count_a") or result.get("school_count_a") or 0)
            cnt_b = int(overview.get("school_count_b") or result.get("school_count_b") or 0)
            answer_a = int(overview.get("answer_count_a") or result.get("answer_count_a") or 0)
            answer_b = int(overview.get("answer_count_b") or result.get("answer_count_b") or 0)
            if answer_spec is not None and question:
                requested_statistics = answer_spec.get("requested_result_statistics") or []
                if "school_count" not in requested_statistics:
                    cnt_a = cnt_b = None
                if "answer_count" not in requested_statistics:
                    answer_a = answer_b = None
            value_unit = str(overview.get("value_unit") or result.get("value_unit") or "分")
            diff_val = float(overview.get("diff") if overview.get("diff") is not None else (tot_b - tot_a))
            trend_str = str(overview.get("trend") or result.get("trend") or ("提升" if diff_val > 0 else "下降"))

            dim_deltas = result.get("dimension_deltas") or []
            dimensions = [dd.get("dimension", "") for dd in dim_deltas]
            vals_a = [float(dd.get("score_a", 0.0)) for dd in dim_deltas]
            vals_b = [float(dd.get("score_b", 0.0)) for dd in dim_deltas]

            from packages.agent.tools.chart_scope import comparison_filter_label
            school_filter = comparison_filter_label(overview.get("school_filter"))
            target_dimension = str(overview.get("target_dimension") or "")
            chart_title = f"{reg_name} · {y_a}至{y_b}年 · {school_filter} · {target_dimension or '主比较值'}"
            code = self.chart_builder.build_region_period_change_code(
                region_name=reg_name,
                year_a=y_a,
                value_a=tot_a,
                schools_a=cnt_a,
                answers_a=answer_a,
                year_b=y_b,
                value_b=tot_b,
                schools_b=cnt_b,
                answers_b=answer_b,
                value_unit=value_unit,
                diff=diff_val,
                trend=trend_str,
                school_filter=school_filter,
                target_dimension=target_dimension,
            )

        # 场景 3.8：全国各省份教育数字化水平横向排行榜
        elif obj_type == "national_ranking":
            year = str(overview.get("year") or result.get("year") or "2023")
            dim = overview.get("target_dimension") or result.get("target_dimension")
            charts_data = result.get("charts_data") or {}
            rankings = result.get("rankings") or []
            provinces = charts_data.get("provinces") or [it["province"] for it in rankings[:15]]
            scores = charts_data.get("scores") or [it["score"] for it in rankings[:15]]
            counts = charts_data.get("school_counts") or [it["school_count"] for it in rankings[:15]]
            tiers = charts_data.get("tiers") or [it.get("sample_tier", "") for it in rankings[:15]]

            chart_title = charts_data.get("title") or (
                f"{year} 年全国各省教育数字化水平排行榜 Top 15" if not dim else f"{year} 年全国各省【{dim}】得分排行榜 Top 15"
            )
            if provinces and scores:
                code = self.chart_builder.build_national_ranking_code(
                    title=chart_title,
                    year=year,
                    target_dimension=dim,
                    provinces=provinces,
                    scores=scores,
                    school_counts=counts,
                    tiers=tiers,
                )

        # 场景 4：整校数字化综合评估 或 教师多维评估
        else:
            name = overview.get("school_name") or overview.get("teacher_id") or "评估对象"
            tid = overview.get("teacher_id")
            if tid:
                teacher_alias = {"T001": "张老师", "T002": "李老师", "T003": "王老师", "T004": "赵老师"}.get(tid, "")
                if teacher_alias:
                    name = f"教师 {tid}（{teacher_alias}）"
            dims = []
            vals = []
            for d in result.get("dimensions", []):
                score = d.get("score")
                if score is not None and not d.get("is_redline"):
                    try:
                        vals.append(float(score))
                        dims.append(d.get("dimension", ""))
                    except (ValueError, TypeError):
                        pass

            explicit_single_bar = (
                not tid
                and len(dims) == 1
                and bool(re.search(r"一根柱|单柱|柱状图|柱子", str(question or "")))
            )
            target_dimensions = overview.get("target_dimensions") or result.get("target_dimensions") or []
            if len(target_dimensions) >= 2 and not tid:
                d1, d2 = target_dimensions[0], target_dimensions[1]
                s1 = next((float(d["score"]) for d in result.get("dimensions", []) if d.get("dimension") == d1), None)
                s2 = next((float(d["score"]) for d in result.get("dimensions", []) if d.get("dimension") == d2), None)
                if s1 is not None and s2 is not None:
                    labels = [d1, d2]
                    values = [round(s1, 2), round(s2, 2)]
                    years = overview.get("school_years") or result.get("school_years") or []
                    year = str(overview.get("year") or (years[0] if years else "2023"))
                    chart_title = f"{name} · {year}年【{d1}】与【{d2}】维度得分对比"
                    code = f'''import matplotlib.pyplot as plt
import json
labels = {json.dumps(labels, ensure_ascii=False)}
values = {json.dumps(values)}
fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=160)
bars = ax.bar([0, 1], values, color=['#357a91', '#d18442'], width=0.45)
ax.set_xticks([0, 1], labels)
ax.set_ylabel("维度得分（分）", fontsize=10.5)
ax.set_title({json.dumps(chart_title, ensure_ascii=False)}, fontsize=12, weight='bold')
ax.set_ylim(0, max(values) * 1.25 if max(values) > 0 else 20)
for bar, value in zip(bars, values):
    ax.annotate(f"{{value:g}}分", (bar.get_x() + bar.get_width()/2, bar.get_height()),
                xytext=(0, 4), textcoords='offset points', ha='center', va='bottom', fontsize=11, weight='bold')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
fig.tight_layout()
'''
            elif explicit_single_bar:
                years = overview.get("school_years") or result.get("school_years") or []
                year = str(overview.get("year") or (years[0] if years else "所选年份"))
                chart_title = f"{name} · {year}年 · {dims[0]}维度得分"
                code = self.chart_builder.build_school_dimension_bar_code(
                    school_name=name, year=year, dimension=dims[0], score=vals[0],
                )
            elif dims and vals:
                years = overview.get("school_years") or result.get("school_years") or []
                year = str(overview.get("year") or result.get("year") or (years[0] if years else "所选年份"))
                chart_title = (
                    f"{name} · {year}年教学业务与能力多维雷达图"
                    if tid else f"{name}\n{year}年学校数字化六维得分（分）雷达图"
                )
                code = self.chart_builder.build_radar_code(
                    title=chart_title,
                    dimensions=dims,
                    values=vals,
                )

        # 若生成了有效的作图代码，则调用本地 python_interpreter 工具执行
        if code:
            tool_res = self.registry.call(
                "python_interpreter",
                code=code,
                chart_title=chart_title,
            )
            tool_calls.append(tool_res.to_dict())
            if tool_res.artifacts:
                charts.extend(tool_res.artifacts)

        return {
            "tool_calls": tool_calls,
            "charts": charts,
        }


# 全局编排器单例
orchestrator = AgentToolOrchestrator()


def get_orchestrator() -> AgentToolOrchestrator:
    return orchestrator
