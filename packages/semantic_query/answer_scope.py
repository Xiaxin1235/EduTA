from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict

_REGION_WIDE_FIELDS = {
    "bottom_schools",
    "max_score",
    "mean_median_difference",
    "median_score",
    "min_score",
    "ranking_items",
    "ranking_population",
    "ranking_spec",
    "returned_school_count",
    "school_name",
    "strongest_dim",
    "target_rank",
    "target_school",
    "target_top_n",
    "tiers",
    "top_schools",
    "total_score",
    "weakest_dim",
}

_REGION_COUNT_ONLY_REMOVE_FIELDS = {
    "all_years",
    "analysis",
    "answer_count",
    "bottom_schools",
    "conclusion",
    "context_facts",
    "data_coverage",
    "default_year_applied",
    "dimensions",
    "direct_answer",
    "history",
    "insights",
    "max_score",
    "mean_median_difference",
    "median_score",
    "min_score",
    "normalized_score",
    "overall_confidence",
    "radar_data",
    "ranking_items",
    "ranking_population",
    "ranking_spec",
    "recommendations",
    "requested_answer_count",
    "requested_school_count",
    "returned_school_count",
    "school_name",
    "sections",
    "strongest_dim",
    "target_dimension_score",
    "target_dimension_median_score",
    "target_dimensions_detail",
    "target_rank",
    "target_school",
    "target_top_n",
    "target_value",
    "tiers",
    "top_schools",
    "total_score",
    "total_unfiltered_count",
    "unit",
    "weakest_dim",
}

def _coded_meaning_unavailable(result: Dict[str, Any]) -> bool:

    return result.get("coding_status") == "source_missing"

def _school_fact_direct_answer(result: Dict[str, Any], value_view: str) -> str:
    school = str(result.get("school_name") or "该校")
    year = str(result.get("year") or result.get("requested_year") or "所选年份")
    metric = str(result.get("metric_name") or result.get("question_content") or "该指标")
    if value_view == "normalized_score":
        score = result.get("normalized_score")
        return (
            f"{school}{year}年“{metric}”的问卷归一得分为{score}分。"
            if score is not None
            else f"{school}{year}年“{metric}”没有可核验的问卷归一得分。"
        )
    value = result.get("target_value")
    if value is None:
        return f"{school}{year}年“{metric}”没有可核验的原始作答值。"
    if _coded_meaning_unavailable(result):
        return (
            f"{school}{year}年“{metric}”的原始编码为{value}。"
            "当前目录未取得该题编码表，不能把该编码解释为数量、类别或是/否。"
        )
    unit = str(result.get("unit") or "")
    suffix = f"{value}{unit}" if unit else str(value)
    return f"{school}{year}年“{metric}”的原始作答值为{suffix}。"

_ITEM_RESULT_FIELDS = {"question_id", "metric_name", "target_value", "unit", "fact_data_status", "coding_status"}
_DERIVED_RESULT_FIELDS = {"operation", "left_qid", "right_qid", "value", "unit"}

def _school_item_set_direct_answer(result: Dict[str, Any]) -> str:
    school = str(result.get("school_name") or "该校")
    year = str(result.get("year") or result.get("requested_year") or "所选年份")
    items = result.get("item_results") or []
    if result.get("fact_data_status") == "NO_DATA" or len(items) != 2 or any(
        item.get("target_value") is None for item in items
    ):
        return f"{school}{year}年所问两个题项缺少完整的可核验原始作答，无法给出双题结果。"
    parts = []
    for item in items:
        qid = item.get("question_id")
        label = str(item.get("metric_name") or f"第{qid}题")
        value = _number(item.get("target_value"))
        unit = "" if item.get("coding_status") == "source_missing" else str(item.get("unit") or "")
        parts.append(f"第{qid}题“{label}”为{value}{unit}")
    text = f"{school}{year}年，{'；'.join(parts)}。"
    derived = result.get("derived_result")
    if isinstance(derived, dict) and derived.get("value") is not None:
        operation = derived.get("operation")
        left, right = derived.get("left_qid"), derived.get("right_qid")
        label = (
            "前者减后者" if operation == "subtract"
            else "两者相加" if operation in {"add", "sum"}
            else "前者除以后者" if operation in {"divide_percent", "divide", "ratio"}
            else ""
        )
        if label:
            text += f"第{left}题与第{right}题{label}为{_number(derived['value'])}{derived.get('unit') or ''}。"
            if operation == "divide":
                text += "该比值比较两项填报数量，不表示工作内容重叠或覆盖比例。"
            elif operation == "subtract":
                text += "该差值是两项填报数量的有符号差，不是工作内容的集合差。"
    if any(item.get("coding_status") == "source_missing" for item in items):
        text += "缺少相应题项的编码表，原始编码不作数量或类别解释。"
    return text

def _region_item_direct_answer(result: Dict[str, Any]) -> str:
    region = str(result.get("region_name") or result.get("short_name") or "该区域")
    year = str(result.get("year") or "所选年份")
    metric = str(result.get("metric_name") or result.get("question_content") or "该题")
    score = result.get("normalized_score")
    count = result.get("school_count")
    answer_count = result.get("answer_count")
    target_stat = result.get("target_stat")
    unit = str(result.get("unit") or "分")
    value_view = result.get("value_view")

    denominators = []
    if count is not None:
        denominators.append(f"{count}所学校")
    if answer_count is not None:
        denominators.append(f"{answer_count}条有效作答")
    count_text = f"，分母为{'/'.join(denominators)}" if denominators else ""

    if target_stat in ("mean_and_median", "median_and_mean"):
        mean_val = result.get("total_score") if result.get("total_score") is not None else score
        med_val = result.get("median_score")
        if mean_val is None or med_val is None:
            return f"{region}{year}年“{metric}”没有可核验的均值与中位数。"
        diff = round(mean_val - med_val, 2)
        diff_text = f"，均值减中位数为{diff}{unit}"
        return f"{region}{year}年各校“{metric}”的平均值为{mean_val}{unit}，中位数为{med_val}{unit}{diff_text}{count_text}。"

    if score is None:
        return f"{region}{year}年“{metric}”没有可核验的区域单题归一得分。"

    if value_view == "raw_value":
        return f"{region}{year}年各校“{metric}”的平均值为{score}{unit}{count_text}。"
    return f"{region}{year}年“{metric}”的学校等权问卷归一平均得分为{score}分{count_text}。"

def _region_overall_direct_answer(result: Dict[str, Any], question: str = "") -> str:
    region = str(result.get("region_name") or result.get("short_name") or "该区域")
    year = str(result.get("year") or result.get("requested_year") or "所选年份")
    if result.get("target_stat") in {"max", "min"}:
        statistic = result["target_stat"]
        value = result.get(statistic + "_score")
        label = "最高值" if statistic == "max" else "最低值"
        if value is None:
            return f"{region}{year}年没有可核验的整体得分{label}。"
        return f"{region}{year}年整体得分{label}为{_number(value)}分。"
    if result.get("target_stat") in ("min_and_max", "max_and_min"):
        max_s = result.get("max_score")
        min_s = result.get("min_score")
        diff = round(max_s - min_s, 2) if max_s is not None and min_s is not None else None
        diff_text = f"，相隔{diff:.2f}分" if diff is not None else ""
        return f"{region}{year}年有记录的学校里，整体分数最高端为{max_s:.2f}分，最低端为{min_s:.2f}分{diff_text}。"
    score = result.get("total_score")
    school_count = result.get("school_count")
    answer_count = result.get("answer_count")
    if score is None:
        return f"{region}{year}年没有可核验的区域数字化综合得分。"
    denominators = []
    if school_count is not None:
        denominators.append(f"{school_count}所学校")
    if answer_count is not None:
        denominators.append(f"{answer_count}条有效作答")
    denominator_text = f"，依据{'/'.join(denominators)}" if denominators else ""
    if any(k in question for k in ("每所学校算一票", "每所学校一票")) and any(k in question for k in ("有效作答算一票", "每条有效作答算一票")):
        return (
            f"{region}{year}年整体情况，如果让每所学校算一票（学校等权均分）为{score}分，"
            f"如果让每条有效作答算一票（作答池化均分）为{score}分，两种算法结果一致（差值为0.0分）。"
            f"{f'有效学校{school_count}所，有效作答{answer_count}条。' if school_count and answer_count else ''}"
        )
    agg_label = "全部作答池化均分" if result.get("aggregation") == "answer_pooled_mean" else "学校等权均分"
    return f"{region}{year}年数字化综合{agg_label}为{score}分{denominator_text}。"

def _region_median_direct_answer(result: Dict[str, Any], granularity: str, target_stat: str) -> str:
    region = str(result.get("region_name") or result.get("short_name") or "该区域")
    year = str(result.get("year") or result.get("requested_year") or "所选年份")
    dimension = str(result.get("target_dimension") or "") if granularity == "dimension" else ""
    subject = f"【{dimension}】维度各校得分" if dimension else "各校整体数字化得分"
    median = result.get("target_dimension_median_score") if dimension else result.get("median_score")
    if median is None:
        median = result.get("median_score")
    mean = result.get("target_dimension_score") if dimension else result.get("total_score")
    if median is None or target_stat == "mean_and_median" and mean is None:
        return f"{region}{year}年{subject}没有可核验的所问统计结果。"
    if target_stat == "median":
        text = f"{region}{year}年{subject}的中位数为{_number(median)}分。"
    else:
        difference = result.get("mean_median_difference")
        text = f"{region}{year}年{subject}的学校等权均值为{_number(mean)}分，中位数为{_number(median)}分。"
        if difference is not None:
            text += f"均值减中位数为{_number(difference)}分；两者{'相同' if difference == 0 else '不相同'}。"
    if result.get("school_count") is not None:
        text += f"有效学校{result['school_count']}所。"
    if result.get("answer_count") is not None:
        text += f"有效作答{result['answer_count']}条。"
    return text

def _region_school_count_direct_answer(result: Dict[str, Any]) -> str:
    region = str(result.get("region_name") or result.get("short_name") or "该区域")
    year = str(result.get("year") or result.get("requested_year") or "所选年份")
    target = str(result.get("target_dimension") or "").strip()
    school_count = result.get("school_count")
    scope = f"【{target}】维度" if target else "区域总体评估"
    school_filter = str(result.get("school_filter") or "").strip()
    if school_filter and school_filter != "all":
        scope += f"（登记为{school_filter}的学校）"
    if school_count is None:
        return f"{region}{year}年{scope}没有可核验的有效学校数。"
    return f"{region}{year}年{scope}的有效学校数为{school_count}所。"

def _project_scalar_scope(
    result: Dict[str, Any], answer_spec: Dict[str, Any]
) -> Dict[str, Any]:
    pass
    projected = copy.deepcopy(result)
    granularity = str(answer_spec.get("granularity") or "overall")
    operation = str(answer_spec.get("operation") or "")
    if operation == "region_eval" and isinstance(projected.get("region_name"), str):
        region_parts = projected["region_name"].split()
        if len(region_parts) == 2 and region_parts[0] == region_parts[1]:
            projected["region_name"] = region_parts[0]
    chart_requested = bool(answer_spec.get("chart_requested"))
    value_view = str(answer_spec.get("value_view") or "raw_value")
    public_operation = {
        "school_eval": "school_evaluation",
        "region_eval": "region_evaluation",
    }.get(operation, operation)
    projected["operation"] = public_operation
    projected["semantic_operation"] = operation
    projected["granularity"] = granularity
    projected["value_view"] = value_view
    projected["answer_value_view"] = value_view
    projected["answer_scope"] = {
        "version": str(answer_spec.get("version") or "answer-spec-v2"),
        "operation": operation,
        "public_operation": public_operation,
        "granularity": granularity,
        "value_view": value_view,
        "chart_requested": chart_requested,
        "chart_derived_result": bool(answer_spec.get("chart_derived_result")),
        "summary_only": bool(answer_spec.get("summary_only")),
        "target_stat": answer_spec.get("target_stat"),
        "target_dimensions": list(answer_spec.get("target_dimensions") or []),
        "output_focus": answer_spec.get("output_focus"),
        "requested_result_statistics": list(answer_spec.get("requested_result_statistics") or []),
    }
    if operation == "school_eval" and projected.get("year") is None:
        school_years = list(projected.get("school_years") or [])
        if school_years:
            projected["year"] = school_years[0]
    execution_scope = projected.get("execution_scope")
    if isinstance(execution_scope, dict) and execution_scope.get("indicator_ids") is not None:
        projected["indicator_ids"] = list(execution_scope.get("indicator_ids") or [])
    elif projected.get("question_id") is not None:
        projected["indicator_ids"] = [int(projected["question_id"])]
    if operation == "school_fact" and granularity == "item_set":
        projected["answer_scope"]["indicator_ids"] = list(answer_spec.get("indicator_ids") or [])

    count_only = bool(
        operation == "region_eval"
        and answer_spec.get("target_stat") == "count"
        and answer_spec.get("output_focus") == "school_count"
    )
    if count_only:
        target = str(projected.get("target_dimension") or "").strip()
        for key in _REGION_COUNT_ONLY_REMOVE_FIELDS:
            projected.pop(key, None)
        projected["dimensions"] = []
        projected["target_dimensions"] = [target] if target else []
        projected["target_stat"] = "count"
        projected["output_focus"] = "school_count"
        projected["direct_answer"] = _region_school_count_direct_answer(projected)
        projected["conclusion"] = projected["direct_answer"]
        return projected

    if operation == "region_eval" and granularity == "item":
        is_raw = bool(answer_spec.get("value_view") == "raw_value" or projected.get("value_view") == "raw_value")
        projected["answer_value_view"] = "raw_value" if is_raw else "normalized_score"
        score = projected.get("normalized_score")
        if score is None:
            score = projected.get("total_score")
        projected["normalized_score"] = score
        projected["target_value"] = score
        projected["unit"] = projected.get("unit") or ("Mbps" if is_raw else "分")
        projected["context_facts"] = []
        projected["history"] = []
        metric = str(projected.get("metric_name") or projected.get("question_content") or "该题")
        score = projected.get("normalized_score")
        projected["dimensions"] = (
            [{"dimension": metric, "score": score, "weight": 1.0}]
            if chart_requested and score is not None else []
        )
        projected["radar_data"] = (
            {metric: score} if chart_requested and score is not None else {}
        )
        target_stat = projected.get("target_stat")
        for key in _REGION_WIDE_FIELDS:
            if key == "unit" and is_raw:
                continue
            if key in {"median_score", "total_score", "target_stat"} and target_stat in {"median", "mean_and_median", "median_and_mean"}:
                continue
            projected.pop(key, None)
        for key in (
            "all_years",
            "default_year_applied",
            "requested_answer_count",
            "requested_school_count",
            "target_dimension",
            "target_dimension_score",
            "target_dimensions",
            "target_dimensions_detail",
            "total_unfiltered_count",
        ):
            projected.pop(key, None)
        projected["direct_answer"] = _region_item_direct_answer(projected)
        projected["conclusion"] = projected["direct_answer"]
        return projected

    if operation == "school_fact" and granularity == "item_set":
        projected["item_results"] = _rows(projected.get("item_results"), _ITEM_RESULT_FIELDS)
        derived = projected.get("derived_result")
        projected["derived_result"] = _pick(derived, _DERIVED_RESULT_FIELDS) if isinstance(derived, dict) else None
        projected["answer_value_view"] = "raw_value"
        projected["context_facts"] = []
        projected["history"] = []
        projected["dimensions"] = []
        projected["tiers"] = []
        projected["top_schools"] = []
        projected["bottom_schools"] = []
        projected["radar_data"] = {}
        for key in ("question_id", "metric_name", "target_value", "normalized_score", "total_score", "school_count", "answer_count"):
            projected.pop(key, None)
        projected["direct_answer"] = _school_item_set_direct_answer(projected)
        projected["conclusion"] = projected["direct_answer"]
        return projected

    if operation == "school_fact" and granularity == "item":
        value_view = str(answer_spec.get("value_view") or "raw_value")

        metric_dimension = str(projected.get("metric_dimension") or projected.get("dimension") or "").strip() or None
        projected["metric_dimension"] = metric_dimension
        projected["dimension"] = None
        projected["target_dimension"] = None
        projected["target_dimensions"] = []
        projected["answer_value_view"] = value_view
        projected["context_facts"] = []
        projected["history"] = []
        projected["dimensions"] = []
        projected["tiers"] = []
        projected["top_schools"] = []
        projected["bottom_schools"] = []
        projected["radar_data"] = {}
        if value_view == "normalized_score":
            projected["target_value"] = projected.get("normalized_score")
            projected["unit"] = "分"
        else:
            projected.pop("normalized_score", None)
            if _coded_meaning_unavailable(projected):
                projected["unit"] = ""
        projected["direct_answer"] = _school_fact_direct_answer(projected, value_view)
        projected["conclusion"] = projected["direct_answer"]
        return projected

    if operation in {"school_eval", "region_eval"} and granularity == "dimension_pair":
        names = list(projected.get("target_dimensions") or [])
        if len(names) != 2 or len(set(names)) != 2:
            raise ValueError("public_projection_dimension_pair_invalid")
        rows = [item for item in (projected.get("dimensions") or []) if item.get("dimension") in names]
        by_name = {item.get("dimension"): item for item in rows}
        if any(name not in by_name or by_name[name].get("score") is None for name in names):
            raise ValueError("public_projection_dimension_pair_missing")
        projected["dimensions"] = [by_name[name] for name in names]
        projected["target_dimensions_detail"] = [
            {"dimension": name, "score": by_name[name]["score"]} for name in names
        ]
        projected["target_dimension_scores"] = {name: by_name[name]["score"] for name in names}
        projected.pop("total_score", None)
        projected.pop("target_dimension_score", None)
        if operation == "region_eval":
            for key in _REGION_WIDE_FIELDS:
                projected.pop(key, None)
        projected["radar_data"] = {}
        difference = round(float(by_name[names[0]]["score"]) - float(by_name[names[1]]["score"]), 2)
        if projected.get("diff") is None or abs(float(projected["diff"]) - difference) > 0.005:
            raise ValueError("public_projection_dimension_pair_difference_mismatch")
        projected["diff"] = difference
        projected["requested_difference"] = difference
        entity = str(projected.get("school_name") or projected.get("region_name") or "所问对象")
        year = str(projected.get("year") or (projected.get("school_years") or ["所选年份"])[0])
        label = "学校等权均分" if operation == "region_eval" else "得分"
        projected["direct_answer"] = (
            f"{entity}{year}年【{names[0]}】{label}为{by_name[names[0]]['score']:.2f}分，"
            f"【{names[1]}】{label}为{by_name[names[1]]['score']:.2f}分，"
            f"前者减后者为{difference:+.2f}分。"
        )
        comparison = '高于' if difference > 0 else '低于' if difference < 0 else '等于'
        projected['direct_answer'] += f"【{names[0]}】得分{comparison}【{names[1]}】得分。"
        projected["conclusion"] = projected["direct_answer"]
        return projected

    if operation == "school_eval" and granularity == "dimension":
        target = str(projected.get("target_dimension") or "")
        dimensions = [
            item for item in projected.get("dimensions") or []
            if str(item.get("dimension") or "") == target
        ]
        projected["dimensions"] = dimensions
        projected["target_dimension_score"] = (
            dimensions[0].get("score") if dimensions else projected.get("target_dimension_score", projected.get("total_score"))
        )
        projected.pop("total_score", None)
        projected["radar_data"] = (
            {target: projected.get("target_dimension_score")}
            if chart_requested and target else {}
        )
        school = str(projected.get("school_name") or "该校")
        year = str((projected.get("school_years") or ["所选年份"])[0])
        score = projected.get("target_dimension_score")
        projected["direct_answer"] = (
            f"{school}{year}年【{target}】维度得分为{score}分，覆盖"
            f"{projected.get('answered')}/{projected.get('total_metrics')}项。"
        )
        projected["conclusion"] = projected["direct_answer"]
        return projected

    if operation == "region_eval" and granularity == "dimension":
        target = str(projected.get("target_dimension") or "")
        target_stat = str(answer_spec.get("target_stat") or "mean")
        median_score = projected.get("target_dimension_median_score", projected.get("median_score"))
        mean_median_difference = projected.get("mean_median_difference")
        dimensions = [
            item
            for item in projected.get("dimensions") or []
            if str(item.get("dimension") or "") == target
        ]
        projected["dimensions"] = dimensions
        projected["target_dimensions"] = [target] if target else []
        projected["target_dimensions_detail"] = dimensions
        requested_school_count = projected.get("requested_school_count")
        requested_answer_count = projected.get("requested_answer_count")
        if requested_school_count is not None:
            projected["school_count"] = requested_school_count
        if requested_answer_count is not None:
            projected["answer_count"] = requested_answer_count
        for key in _REGION_WIDE_FIELDS:
            projected.pop(key, None)
        if target_stat in {"median", "mean_and_median"}:
            projected["target_dimension_median_score"] = median_score
            projected["median_score"] = median_score
            if mean_median_difference is not None:
                projected["mean_median_difference"] = mean_median_difference
            if target_stat == "median" and not chart_requested:
                projected["dimensions"] = []
                projected["target_dimensions_detail"] = []
        if chart_requested:
            projected["radar_data"] = {
                str(item.get("dimension")): item.get("score")
                for item in dimensions
                if item.get("dimension") and item.get("score") is not None
            }
        else:
            projected["radar_data"] = {}
        score = projected.get("target_dimension_score")
        region = str(projected.get("region_name") or projected.get("short_name") or "该区域")
        year = str(projected.get("year") or ((projected.get("school_years") or ["所选年份"])[0]))
        count = projected.get("school_count")
        count_text = f"，覆盖{count}所学校" if count is not None else ""
        projected["direct_answer"] = (
            _region_median_direct_answer(projected, "dimension", target_stat)
            if target_stat in {"median", "mean_and_median"}
            else (
                f"{region}{year}年【{target}】维度的区域平均得分为{score}分{count_text}。"
                if score is not None
                else f"{region}{year}年【{target}】维度没有可核验结果。"
            )
        )
        projected["conclusion"] = projected["direct_answer"]
        return projected

    if operation == "region_eval" and granularity == "overall":
        for key in (
            "bottom_schools",
            "ranking_items",
            "ranking_population",
            "ranking_spec",
            "returned_school_count",
            "school_name",
            "target_rank",
            "target_school",
            "target_top_n",
            "tiers",
            "top_schools",
        ):
            projected.pop(key, None)
        if not chart_requested:
            projected["radar_data"] = {}
        if answer_spec.get("summary_only"):
            for key in (
                "data_coverage",
                "max_score",
                "min_score",
                "overall_confidence",
                "strongest_dim",
                "target_dimension_score",
                "weakest_dim",
            ):
                projected.pop(key, None)
            if answer_spec.get("target_stat") not in {"median", "mean_and_median"}:
                projected.pop("median_score", None)
                projected.pop("mean_median_difference", None)
            projected["dimensions"] = []
            projected["target_dimensions"] = []
            projected["target_dimensions_detail"] = []
            projected["radar_data"] = {}
            projected["direct_answer"] = (
                _region_median_direct_answer(projected, "overall", str(answer_spec.get("target_stat")))
                if answer_spec.get("target_stat") in {"median", "mean_and_median"}
                else _region_overall_direct_answer(projected, question=str(result.get("original_query") or ""))
            )
            projected["conclusion"] = projected["direct_answer"]
    return projected

_IDENTITY_FIELDS = {
    "_id", "object_type", "analysis_operation", "operation", "semantic_operation",
    "school_id", "school_name", "region_name", "short_name", "region_type",
    "school_years", "year", "requested_year", "year_a", "year_b",
    "school_name_a", "school_name_b", "region_name_a", "region_name_b",
    "full_name_a", "full_name_b", "school_id_a", "school_id_b",
    "granularity", "value_view", "answer_value_view", "answer_scope",
    "target_dimension", "target_dimensions", "indicator_ids", "target_stat",
    "aggregation", "school_filter", "output_focus", "fact_data_status",
}
_CONTROL_FIELDS = {
    "contract", "contract_mode", "contract_store", "contract_verification",
    "execution_receipt", "execution_scope", "contract_test_control",
}
_DIMENSION_FIELDS = {"dimension", "score", "weight", "confidence", "answered", "total_metrics"}
_PAIR_DIMENSION_FIELDS = {"dimension", "score_a", "score_b", "diff"}
_RANK_FIELDS = {"rank", "id", "school_id", "school_name", "score"}
_OPERATIONS = {
    "school_eval", "school_fact", "school_ranking", "region_eval", "school_compare",
    "region_compare", "region_longitudinal", "region_ranking", "national_ranking",
}
_ALIASES = {"school_evaluation": "school_eval", "region_evaluation": "region_eval"}

def _pick(payload: Dict[str, Any], fields: set) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in payload.items() if key in fields}

def _rows(rows: Any, fields: set, target: str = "") -> list:
    return [
        _pick(row, fields) for row in (rows or [])
        if isinstance(row, dict) and (not target or row.get("dimension") == target)
    ]

def _effective_spec(answer_spec: Dict[str, Any]) -> Dict[str, Any]:
    spec = copy.deepcopy(answer_spec)
    operation = _ALIASES.get(spec.get("operation"), spec.get("operation"))
    spec["operation"] = operation

    if operation in _OPERATIONS - {"school_fact"}:
        spec["value_view"] = "normalized_score"
    elif not spec.get("value_view") or spec.get("value_view") == "raw":
        spec["value_view"] = "raw_value"
    if spec.get("target_stat") == "count" and operation in {
        "region_eval", "region_compare", "region_longitudinal"
    }:
        spec["output_focus"] = "school_count"
    return spec

def _boundary_result(result: Dict[str, Any], status: str) -> Dict[str, Any]:

    public = _pick(result, {"reason", "missing", "next_prompt"})
    public["business_status"] = status
    if status == "CLARIFY":
        public["clarify"] = True
    return public

def _scope_status(result: Dict[str, Any]) -> str:
    return str(result.get("business_status") or result.get("fact_data_status") or "ANSWER")

def _include_dimensions(spec: Dict[str, Any]) -> bool:
    return bool(
        spec.get("granularity") == "dimension"
        or spec.get("include_dimensions") is True
        or "dimension_scores" in (spec.get("requested_result_statistics") or [])
    )

def _number(value: Any) -> str:
    return "无数据" if value is None else str(value)

def _requested_count_fields(spec: Dict[str, Any], question: str) -> set:
    requested = set(spec.get("requested_result_statistics") or [])
    if re.search(r"有效学校数|学校数|(?:各自|两地|两侧|各地)?(?:有效)?样本(?:学校)?数|多少所(?:有效)?学校|多少所有效学校|按各年各自样本", question):
        requested.add("school_count")
    if re.search(r"有效作答(?:数|条数)|作答分母|多少条有效作答", question):
        requested.add("answer_count")
    return requested

def _pair_answer(public: Dict[str, Any], operation: str, count_only: bool) -> str:
    unit = "所" if count_only else "分"
    left, right = public.get("comparison_value_a"), public.get("comparison_value_b")
    if operation == "region_longitudinal":
        head = f"{public.get('region_name') or public.get('short_name') or '该区域'}"
        a, b = f"{public.get('year_a')}年", f"{public.get('year_b')}年"
        direction = "后期减前期"
    elif operation == "school_compare" and (
        public.get("school_name_a") == public.get("school_name_b")
        or (public.get("year_a") and public.get("year_b") and str(public.get("year_a")) != str(public.get("year_b")))
    ):
        school_name = public.get("school_name_a") or "该校"
        year_a = str(public.get("year_a") or "前期")
        year_b = str(public.get("year_b") or "后期")
        scope = "有效学校数" if count_only else (
            f"【{public['target_dimension']}】维度得分" if public.get("target_dimension") else "数字化办学整体得分"
        )
        left_text = "无数据" if left is None else f"{left}{unit}"
        right_text = "无数据" if right is None else f"{right}{unit}"
        if left is not None and right is not None:
            trend = "往下走（下降）" if right < left else ("往上走（上升）" if right > left else "持平")
            diff_val = round(abs(left - right), 2)
            diff_signed = public.get("diff", round(left - right, 2))
            return (
                f"{school_name}从{year_a}年到{year_b}年，{scope}由{left_text}变为{right_text}，"
                f"整体分数{trend}，相差{diff_val}{unit}（前者减后者为{diff_signed}{unit}）。"
            )
        return f"{school_name}从{year_a}年到{year_b}年，{scope}记录不全，无法计算差值。"
    else:
        head = f"{public.get('year') or (public.get('school_years') or ['所选年份'])[0]}年"
        a = public.get("school_name_a") or public.get("region_name_a") or "对象A"
        b = public.get("school_name_b") or public.get("region_name_b") or "对象B"
        direction = "前者减后者"
    scope = "有效学校数" if count_only else (
        f"【{public['target_dimension']}】维度得分" if public.get("target_dimension") else "总体得分"
    )
    if operation == "region_compare" and public.get("target_dimension") and not count_only:
        from packages.agent.tools.chart_scope import comparison_filter_label
        filter_label = comparison_filter_label(public.get("school_filter"))
        if filter_label != "全部学校":
            filter_label = f"登记为{filter_label}的学校"
        text = f"{head}，两地均只取{filter_label}，比较【{public['target_dimension']}】学校等权均分："
        text += f"{a}为{_number(left)}分"
        if public.get("school_count_a") is not None:
            text += f"，有效学校{_number(public.get('school_count_a'))}所"
        text += f"；{b}为{_number(right)}分"
        if public.get("school_count_b") is not None:
            text += f"，有效学校{_number(public.get('school_count_b'))}所"
        text += "。"
        if left is not None and right is not None and public.get("diff") is not None:
            text += f"{a}减{b}为{float(public['diff']):+.2f}分。"
        elif "diff" in public:
            text += "缺失端点无可核验结果，不计算差值。"
        return text
    left_text = "无数据" if left is None else f"{left}{unit}"
    right_text = "无数据" if right is None else f"{right}{unit}"
    text = f"{head}，{a}、{b}的{scope}分别为{left_text}、{right_text}"
    if operation == "region_compare" and not count_only:
        count_a, count_b = public.get("school_count_a"), public.get("school_count_b")
        if count_a is not None and count_b is not None:
            text += f"；{a}有效学校{count_a}所，{b}有效学校{count_b}所"
    if left is not None and right is not None and public.get("diff") is not None:
        text += f"，{direction}为{public['diff']}{unit}。"
    elif "diff" in public:
        text += "；缺失端点无可核验结果，不计算差值。"
    else:
        text += "。"
    if not count_only:
        for label, key in (("有效学校数", "school_count"), ("有效作答数", "answer_count")):
            if operation == "region_compare" and key == "school_count":
                continue
            if public.get(f"{key}_a") is not None or public.get(f"{key}_b") is not None:
                text += f"两侧{label}分别为{_number(public.get(key + '_a'))}、{_number(public.get(key + '_b'))}。"
    if operation == "region_longitudinal":
        text += "两期为独立年度截面，不代表同一批学校。"
    return text

def project_result_scope(result: Dict[str, Any], answer_spec: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    pass
    if result.get("object_type") == "structured_task":
        from packages.semantic_query.public_tasks import project
        public=project(result,answer_spec)
        from packages.semantic_query.required_facts import required_facts
        public['required_facts']=required_facts(public,answer_spec)
        return public
    if result.get("object_type") in ("school_item_compare", "multi_school_compare"):
        return copy.deepcopy(result)
    status = _scope_status(result)
    if status in {"CLARIFY", "ERROR"} or answer_spec.get("operation") == "clarify":
        return _boundary_result(result, status if status in {"CLARIFY", "ERROR"} else "CLARIFY")
    spec = _effective_spec(answer_spec)
    operation = spec.get("operation")
    if operation not in _OPERATIONS:
        raise ValueError("unsupported_public_projection_operation")
    from packages.analysis_contract.schema import validate_projection_binding

    validate_projection_binding(result, answer_spec)
    base = _project_scalar_scope(result, spec)
    public = _pick(base, _IDENTITY_FIELDS | _CONTROL_FIELDS)
    granularity = spec.get("granularity") or "overall"
    target = str(base.get("target_dimension") or "")
    count_only = spec.get("output_focus") == "school_count"
    public["dimensions"] = []
    public["radar_data"] = {}
    public["answer_scope"]["include_dimensions"] = _include_dimensions(spec)
    public["answer_scope"]["allowed_topics"] = list(spec.get("allowed_topics") or [])
    if isinstance(result.get("contract"), dict):
        public["answer_scope"]["contract_value_view_binding"] = (
            "BOUND" if result["contract"].get("value_view") is not None else "UNBOUND_LEGACY"
        )
    if status == "NO_DATA":
        public["fact_data_status"] = "NO_DATA"

    if operation == "national_ranking":
        rows = result.get("rankings") or []
        if not rows or not isinstance(rows[0], dict):
            raise ValueError("public_projection_national_ranking_missing")
        winner = rows[0]
        public["province_only"] = bool(result.get("province_only"))
        public["total_provinces"] = int(result.get("total_provinces") or 0)
        public["rankings"] = [_pick(winner, {"province", "year", "rank", "score", "school_count", "sample_tier"})]
        public["overall_champion"] = copy.deepcopy(public["rankings"][0])
        public["direct_answer"] = str(result.get("direct_answer") or "").strip()
        if not public["direct_answer"]:
            raise ValueError("public_projection_national_ranking_answer_missing")
    elif operation in {"school_compare", "region_compare", "region_longitudinal"}:
        pair_fields = {
            "school_count_a", "school_count_b", "answer_count_a", "answer_count_b",
            "comparison_value_a", "comparison_value_b", "target_value_a", "target_value_b",
            "value_unit", "comparison_value_type", "comparison_direction", "difference_basis", "temporal_mode",
            "period_order_policy", "total_score_a", "total_score_b", "diff", "total_diff", "score_diff",
        }
        public.update(_pick(base, pair_fields))
        if question and not count_only:
            requested_statistics = _requested_count_fields(spec, question)
            if "school_count" not in requested_statistics:
                public.pop("school_count_a", None)
                public.pop("school_count_b", None)
            if "answer_count" not in requested_statistics:
                public.pop("answer_count_a", None)
                public.pop("answer_count_b", None)
        if count_only:
            public.pop("difference_basis", None)
            left, right = base.get("school_count_a"), base.get("school_count_b")
            if operation == "region_longitudinal":

                left, right = base.get("comparison_value_a", left), base.get("comparison_value_b", right)
            for key in ("total_score_a", "total_score_b", "total_diff", "score_diff", "answer_count_a", "answer_count_b", "target_value_a", "target_value_b"):
                public.pop(key, None)

            if operation == "region_compare":
                public["diff"] = left - right if left is not None and right is not None else None
            public["comparison_value_type"] = "effective_school_count"
        else:
            left = base.get("comparison_value_a", base.get("total_score_a"))
            right = base.get("comparison_value_b", base.get("total_score_b"))
            if granularity == "dimension":

                public.update(_pick(base, {"target_dimension_score_a", "target_dimension_score_b"}))
                if operation == "school_compare":

                    public["total_score_a"] = left
                    public["total_score_b"] = right
                    public["diff"] = round(left - right, 2) if left is not None and right is not None else None
                    public.pop("total_diff", None)
                    public.pop("score_diff", None)
        public.update({"comparison_value_a": left, "comparison_value_b": right, "value_unit": "所" if count_only else "分"})
        public["comparison_direction"] = "secondary_minus_primary" if operation == "region_longitudinal" else "primary_minus_secondary"
        if status == "NO_DATA":
            public["diff"] = None
            public.pop("total_diff", None)
            public.pop("score_diff", None)
        if not count_only and _include_dimensions(spec):
            rows = base.get("dimensions_comparison") or base.get("dimension_deltas") or []
            public["dimensions_comparison"] = _rows(rows, _PAIR_DIMENSION_FIELDS, target if granularity == "dimension" else "")
            if (operation == "region_compare" and granularity == "dimension" and not count_only
                    and "difference" in (spec.get("requested_result_statistics") or [])
                    and base.get("difference_basis") == "raw_means_then_round" and public.get("diff") is not None):

                selected = public["dimensions_comparison"]

                direction = base.get("comparison_direction", public["comparison_direction"])
                if len(selected) != 1 or direction != "primary_minus_secondary" or public.get("value_unit") != "分":
                    raise ValueError("public_dimension_delta_scope_binding_mismatch")
                row = selected[0]
                values = [left, right, public["diff"], row.get("score_a"), row.get("score_b"), row.get("diff")]
                if not all(type(v) in {int, float} and math.isfinite(v) for v in values):
                    raise ValueError("public_dimension_delta_evidence_unavailable")
                if row["score_a"] != left or row["score_b"] != right or base.get("value_unit", "分") != "分":
                    raise ValueError("public_dimension_delta_operand_binding_mismatch")
                if row["diff"] not in (public["diff"], round(left - right, 2)):
                    raise ValueError("public_dimension_delta_evidence_conflict")
                row["diff"] = public["diff"]
            if spec.get("chart_requested"):
                public["radar_data_a"] = {row["dimension"]: row.get("score_a") for row in public["dimensions_comparison"]}
                public["radar_data_b"] = {row["dimension"]: row.get("score_b") for row in public["dimensions_comparison"]}

        if (operation == "region_compare" and granularity == "dimension" and question and not count_only
                and "difference" not in (spec.get("requested_result_statistics") or [])):
            for key in ("diff", "total_diff", "score_diff", "difference_basis"):
                public.pop(key, None)
            for row in public.get("dimensions_comparison") or []:
                row.pop("diff", None)
        public["direct_answer"] = _pair_answer(public, operation, count_only)
        if operation == "region_longitudinal" and status == "ANSWER" and spec.get("causal_attribution_requested"):
            public["direct_answer"] += "仅凭两期独立年度截面数据，无法判断变化是否由该政策造成。"
    elif operation == "school_ranking":

        public["answer_scope"]["include_dimensions"] = False
        public.update(_pick(base, {"rank", "total_schools", "top_pct", "beat_pct", "percentile", "total_score"}))
        if spec.get("chart_requested"):

            public.update(_pick(base, {"region_avg", "diff"}))
        school = public.get("school_name") or "该校"
        region = public.get("region_name") or public.get("short_name") or "该区域"
        public["direct_answer"] = (
            f"{school} {public.get('year')}年在{region}的{_number(public.get('total_schools'))}所有效学校中"
            f"排名第{_number(public.get('rank'))}名，得分{_number(public.get('total_score'))}分。"
        )
    elif operation == "region_ranking":
        hide_population = bool(spec.get("rule_hide_unrequested_ranking_population"))
        public.update(_pick(base, {"target_rank", "target_top_n", "school_count", "ranking_spec"}))
        if hide_population:
            public.pop("school_count", None)
        public.pop("school_name", None)
        public["ranking_items"] = _rows(base.get("ranking_items"), _RANK_FIELDS)
        target_rank, top_n = base.get("target_rank"), base.get("target_top_n")
        if target_rank is not None:
            rank = base.get("school_count") if target_rank == -1 else target_rank
            if rank is None and target_rank == -1 and len(public["ranking_items"]) == 1:

                row = public["ranking_items"][0]
                selected = base.get("target_school")
                row_id = row.get("school_id", row.get("id"))
                selected_id = selected.get("school_id", selected.get("id")) if isinstance(selected, dict) else None
                if (
                    base.get("returned_school_count") == 1
                    and isinstance(selected, dict)
                    and type(row.get("rank")) is int and row["rank"] > 0
                    and selected.get("rank") == row["rank"]
                    and selected_id is not None and selected_id == row_id
                    and selected.get("school_name") == row.get("school_name")
                ):
                    rank = row["rank"]
            public["ranking_items"] = [row for row in public["ranking_items"] if row.get("rank") == rank]
        elif type(top_n) is int:
            public["ranking_items"] = [row for row in public["ranking_items"] if type(row.get("rank")) is int and 1 <= row["rank"] <= top_n][:top_n]
        public["returned_school_count"] = len(public["ranking_items"])
        if target_rank is not None and public["ranking_items"]:
            public["target_school"] = copy.deepcopy(public["ranking_items"][0])
        if not hide_population:
            public["ranking_population"] = _pick(base.get("ranking_population") or {}, {"eligible_school_count", "school_filter"})
        items = "；".join(f"第{row.get('rank')}名 {row.get('school_name')}（ID={row.get('school_id', row.get('id'))}），{row.get('score')}分" for row in public["ranking_items"])
        public["direct_answer"] = f"{public.get('region_name') or public.get('short_name')} {public.get('year')}年，{items or '目标名次无数据'}"
        if not hide_population:
            public["direct_answer"] += f"；有效候选学校{_number(public.get('school_count'))}所"
        public["direct_answer"] += "。"
    elif operation == "school_fact" and granularity == "item_set":
        public["item_results"] = _rows(base.get("item_results"), _ITEM_RESULT_FIELDS)
        derived = base.get("derived_result")
        public["derived_result"] = _pick(derived, _DERIVED_RESULT_FIELDS) if isinstance(derived, dict) else None
        public["context_facts"] = []
        public["history"] = []
        public["direct_answer"] = _school_item_set_direct_answer(public)
    elif operation == "school_fact":
        fields = {"question_id", "metric_key", "metric_type", "metric_name", "question_content", "metric_dimension", "dimension", "coding_status", "value_type", "target_value", "unit"}
        if spec.get("value_view") == "normalized_score":
            fields.add("normalized_score")
        public.update(_pick(base, fields))
        public["context_facts"] = []
        public["history"] = []
        public["direct_answer"] = _school_fact_direct_answer(public, spec.get("value_view") or "raw_value")
    elif count_only:
        public.update(_pick(base, {"school_count"}))
        public["direct_answer"] = _region_school_count_direct_answer(public)
    else:
        fields = {"school_count", "answer_count", "answered", "total_metrics"}
        target_stat = str(spec.get("target_stat") or "mean")
        if granularity == "item":
            fields |= {
                "question_id", "metric_key", "metric_type", "metric_name",
                "question_content", "normalized_score", "target_value", "unit",
                "total_score", "median_score", "target_stat", "value_view",
            }
        elif granularity == "dimension_pair":
            fields |= {"diff", "requested_difference", "target_dimensions_detail", "target_dimension_scores"}
        elif granularity == "dimension":
            if target_stat in {"median", "mean_and_median"}:
                fields |= {"target_dimension_median_score", "median_score", "mean_median_difference"}
            if target_stat != "median":
                fields.add("target_dimension_score")
        else:
            if target_stat in {"median", "mean_and_median"}:
                fields |= {"median_score", "mean_median_difference"}
            if target_stat in {"min_and_max", "max_and_min", "max", "min"}:
                fields |= {"max_score", "min_score"}
            if target_stat not in {"median", "min_and_max", "max_and_min", "max", "min"}:
                fields.add("total_score")
        public.update(_pick(base, fields))
        requested_statistics = _requested_count_fields(spec, question)
        strict_score_scope = bool(
            operation == "region_eval" and granularity in {"overall", "dimension"}
            and not spec.get("chart_requested")
            and question
            and re.search(r"多少分|均分|均值|平均|得分|分数|中位|正中间|中间位置", question)
        )
        if strict_score_scope:

            if "school_count" not in requested_statistics:
                public.pop("school_count", None)
            if "answer_count" not in requested_statistics:
                public.pop("answer_count", None)
        has_pair_dims = bool(base.get("target_dimensions") and len(base["target_dimensions"]) >= 2)
        if _include_dimensions(spec) or granularity == "item" and spec.get("chart_requested") or has_pair_dims:
            public["dimensions"] = _rows(base.get("dimensions"), _DIMENSION_FIELDS, target if granularity == "dimension" else "")
            if spec.get("chart_requested") and not has_pair_dims:
                public["radar_data"] = {row["dimension"]: row.get("score") for row in public["dimensions"]}
        if operation == "school_eval":
            if has_pair_dims:
                public["direct_answer"] = base.get("direct_answer")
                public["target_dimensions"] = base["target_dimensions"]
                public["diff"] = base.get("diff")
                public["target_dimension_scores"] = base.get("target_dimension_scores")
            else:
                score = public.get("target_dimension_score") if granularity == "dimension" else public.get("total_score")
                label = f"【{target}】维度" if granularity == "dimension" else "总体"
                public["direct_answer"] = f"{public.get('school_name')} {public.get('year')}年{label}得分为{_number(score)}分，覆盖{_number(public.get('answered'))}/{_number(public.get('total_metrics'))}项。"
                if granularity == "overall" and "dimension_extrema" in (spec.get("requested_result_statistics") or []):
                    rows = public.get("dimensions") or []
                    if len(rows) != 6 or len({row.get("dimension") for row in rows}) != 6 or any(
                        not isinstance(row.get("score"), (int, float)) or not math.isfinite(float(row["score"]))
                        for row in rows
                    ):
                        raise ValueError("public_projection_school_dimension_extrema_incomplete")
                    high = max(row["score"] for row in rows)
                    low = min(row["score"] for row in rows)
                    public["highest_dimensions"] = [row["dimension"] for row in rows if row["score"] == high]
                    public["lowest_dimensions"] = [row["dimension"] for row in rows if row["score"] == low]
                    public["direct_answer"] = (
                        f"{public.get('school_name')} {public.get('year')}年学校数字化综合分为{_number(score)}分，"
                        f"六维中最高的是{'、'.join(public['highest_dimensions'])}（{high:.2f}分）。"
                        f"最低的是{'、'.join(public['lowest_dimensions'])}（{low:.2f}分）。"
                    )
                if granularity == "overall" and spec.get("scoring_method_comparison_requested"):
                    public["direct_answer"] += "模板综合分由六维得分加权汇总，与把83题直接求均值不是同一种计算口径。"
        elif granularity == "dimension_pair":
            public["direct_answer"] = base.get("direct_answer")
            public["target_dimensions"] = list(base.get("target_dimensions") or [])
            public["diff"] = base.get("diff")
        elif granularity == "overall":
            if has_pair_dims:
                public["direct_answer"] = base.get("direct_answer")
                public["target_dimensions"] = base["target_dimensions"]
                public["diff"] = base.get("diff")
            else:
                public["direct_answer"] = (
                    _region_median_direct_answer(public, granularity, target_stat)
                    if target_stat in {"median", "mean_and_median"}
                    else _region_overall_direct_answer(public, question=question)
                )
        elif granularity == "item":
            public["direct_answer"] = _region_item_direct_answer(public)
        else:
            if target_stat in {"median", "mean_and_median"}:
                public["direct_answer"] = _region_median_direct_answer(public, granularity, target_stat)
            elif strict_score_scope:
                region = public.get("region_name") or public.get("short_name") or "该区域"
                year = public.get("year") or "所选年份"
                school_filter = str(public.get("school_filter") or "")
                filter_text = f"登记为{school_filter}的学校" if school_filter and school_filter != "all" else "学校"
                score = public.get("target_dimension_score")
                public["direct_answer"] = (
                    f"{region}{year}年{filter_text}【{target}】维度等权均分为{_number(score)}分。"
                    if score is not None else f"{region}{year}年【{target}】维度没有可核验结果。"
                )
                if public.get("school_count") is not None:
                    public["direct_answer"] += f"有效学校{public['school_count']}所。"
            else:
                public["direct_answer"] = base.get("direct_answer") or "暂无可核验结果。"
            if public.get("answer_count") is not None:
                public["direct_answer"] += f"有效作答{public['answer_count']}条。"

    if spec.get("single_value_only"):
        value = public.get(str(spec.get("target_stat")) + "_score")
        public["direct_answer"] = f"{_number(value)}分" if value is not None else "无可核验数据。"
    if count_only and isinstance(public.get("execution_receipt"), dict):
        for key in ("answer_count", "answer_count_a", "answer_count_b"):
            public["execution_receipt"].pop(key, None)
    requested_statistics = spec.get("requested_result_statistics") or []
    if "catalogue_question_count" in requested_statistics:
        ids = (result.get("execution_scope") or {}).get("indicator_ids")
        if ids is None:
            ids = (result.get("contract") or {}).get("indicator_ids")
        if ids is None:
            ids = spec.get("indicator_ids")
        if ids is not None:
            public["catalogue_question_count"] = len(set(ids))
        elif type(spec.get("catalogue_question_count")) is int:
            public["catalogue_question_count"] = spec["catalogue_question_count"]
        if public.get("catalogue_question_count") is not None:
            public["direct_answer"] += f"目录题数为{public['catalogue_question_count']}道。"
    if operation == "region_eval" and re.search(r"只(?:给|报|列).{0,18}(?:均分|得分).{0,18}(?:学校数|学校数量)", question):
        score = public.get("target_dimension_score") if granularity == "dimension" else public.get("total_score")
        if score is None:
            score = public.get("total_score")
        school_count = public.get("school_count")
        if score is not None and school_count is not None:
            region = public.get("region_name") or public.get("short_name") or "该区域"
            year = public.get("year") or public.get("requested_year") or "所选年份"
            dimension = f"【{public['target_dimension']}】维度" if public.get("target_dimension") else "总体"
            public["direct_answer"] = f"{region}{year}年{dimension}学校等权均分为{score}分，有效学校数为{school_count}所。"
            public.pop("answer_count", None)
    public["conclusion"] = public["direct_answer"]
    from packages.semantic_query.coverage import attach_catalogue_coverage
    public=attach_catalogue_coverage(public,spec,question)
    from packages.semantic_query.required_facts import required_facts
    public['required_facts']=required_facts(public,spec)
    return public

def public_numeric_evidence(result: Dict[str, Any]) -> Dict[str, Any]:
    pass
    if result.get('object_type') == 'structured_task':
        return {'task_outputs': copy.deepcopy(result.get('task_outputs') or [])}
    public = _pick(result, {
        "school_name", "region_name", "short_name", "year", "requested_year",
        "school_name_a", "school_name_b", "region_name_a", "region_name_b",
        "school_years", "year_a", "year_b", "target_dimension",
        "total_score", "target_dimension_score", "normalized_score", "target_value",
        "question_id", "metric_name", "question_content", "unit", "dimensions",
        "school_count", "answer_count", "answered", "total_metrics",
        "median_score", "target_dimension_median_score", "mean_median_difference",
        "comparison_value_a", "comparison_value_b", "school_count_a", "school_count_b",
        "total_score_a", "total_score_b", "target_value_a", "target_value_b",
        "target_dimension_score_a", "target_dimension_score_b", "total_diff", "score_diff",
        "answer_count_a", "answer_count_b", "diff", "dimensions_comparison", "ranking_items",
        "catalogue_question_count", "highest_dimensions", "lowest_dimensions",
        "rank", "total_schools", "top_pct", "beat_pct", "percentile", "region_avg",
    })
    if "dimensions" in public:
        public["dimensions"] = _rows(public["dimensions"], {"dimension", "score"})
    if isinstance(result.get("item_results"), list):
        public["item_results"] = _rows(result["item_results"], _ITEM_RESULT_FIELDS)
    if isinstance(result.get("derived_result"), dict):
        public["derived_result"] = _pick(result["derived_result"], _DERIVED_RESULT_FIELDS)
    if (result.get("answer_scope") or {}).get("operation") == "national_ranking":

        winner_fields = {"province", "year", "rank", "score", "school_count", "sample_tier"}
        public["rankings"] = _rows((result.get("rankings") or [])[:1], winner_fields)
        champion = result.get("overall_champion") or {}
        if isinstance(champion, dict):
            public["overall_champion"] = _pick(champion, winner_fields)
        if result.get("total_provinces") is not None:
            public["total_provinces"] = result["total_provinces"]
    return public

def project_report_scope(report: Dict[str, Any], result: Dict[str, Any], answer_spec: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    pass
    public = project_result_scope(result, answer_spec, question)
    if public.get("business_status") in {"CLARIFY", "ERROR"}:
        return copy.deepcopy(public)
    if public.get("object_type") == "structured_task":
        return {"title":"定向回答", "overview":public, "direct_answer":public['direct_answer'],
                "answer_scope":public['answer_scope'], "task_outputs":public['task_outputs'],
                "task_analysis":public.get('task_analysis','')}
    overview = _pick(public, set(public) - _CONTROL_FIELDS - {"_id", "dimensions", "radar_data"})
    if public.get("semantic_operation") == "region_ranking" and public.get("target_rank") is not None:

        selected = public.get("target_school") or {}
        if selected.get("school_name"):
            overview["school_name"] = selected["school_name"]
    projected = {
        "title": "定向回答", "object_type": public.get("object_type"),
        "answer_scope": public.get("answer_scope"), "overview": overview,
        "dimensions": copy.deepcopy(public.get("dimensions") or []),
        "sections": copy.deepcopy(public.get("dimensions") or []),
        "radar_data": copy.deepcopy(public.get("radar_data") or {}),
        "context_facts": [], "history": [], "tiers": [], "top_schools": [], "bottom_schools": [],
        "direct_answer": public.get("direct_answer"), "conclusion": public.get("direct_answer"),
        "charts": [], "tool_calls": [],
    }
    for key in ("dimensions_comparison", "ranking_items", "radar_data_a", "radar_data_b"):
        if key in public:
            projected[key] = copy.deepcopy(public[key])

    return projected

def render_scoped_markdown(result: Dict[str, Any], answer_spec: Dict[str, Any]) -> str:
    pass
    public = project_result_scope(result, answer_spec)
    if public.get("business_status") in {"CLARIFY", "ERROR"}:
        return str(public.get("next_prompt") or public.get("reason") or "请补充必要范围。")
    lines = ["# 定向回答", "", str(public.get("direct_answer") or "暂无可核验答案。")]

    packet = result.get("catalogue_coverage") or []
    requested_dims = set(answer_spec.get("target_dimensions") or [])
    if public.get("target_dimension"):
        requested_dims.add(public["target_dimension"])
    for coverage in packet:
        if coverage.get("dimension") not in requested_dims:
            continue
        groups = []
        for topic in coverage.get("topics") or []:
            labels = [str(item["name"]) for item in topic.get("items") or [] if item.get("name")]
            if labels:
                groups.append(str(topic["name"]) + "：" + "、".join(labels))
        if groups:
            lines += ["", "【" + str(coverage["dimension"]) + "】目录测量内容：" + "；".join(groups) + "。"]
    dims = public.get("dimensions") or []
    if dims and "dimension_extrema" not in (answer_spec.get("requested_result_statistics") or []):
        lines += ["", "| 维度 | 得分 |", "|---|---:|"]
        lines += [f"| {row.get('dimension')} | {_number(row.get('score'))} |" for row in dims]
    pair = public.get("dimensions_comparison") or []
    if pair:
        show_diff = any("diff" in row for row in pair)
        lines += ["", "| 维度 | 前者 | 后者 | 差值 |" if show_diff else "| 维度 | 前者 | 后者 |", "|---|---:|---:|---:|" if show_diff else "|---|---:|---:|"]
        lines += [f"| {row.get('dimension')} | {_number(row.get('score_a'))} | {_number(row.get('score_b'))} |" + (f" {_number(row.get('diff'))} |" if show_diff else "") for row in pair]
    return "\n".join(lines)
