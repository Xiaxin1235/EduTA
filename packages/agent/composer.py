import json
from typing import Dict, List, Optional

def _fmt_evidence(e: dict) -> str:
    label = e.get("label") or e.get("metric", "指标依据")
    period = e.get("period")
    if isinstance(period, (list, tuple)):
        period_str = ", ".join(str(p) for p in period)
    else:
        period_str = str(period or "")
    formula = str(e.get("formula", "")).strip()
    if period_str and formula:
        return f"[证据: {label} | 学年: {period_str} | {formula}]"
    elif period_str:
        return f"[证据: {label} | 学年: {period_str}]"
    return f"[证据: {label} | {formula}]"

def _dimension_conclusion(d: dict) -> str:
    pass
    if d.get("is_redline"):
        if d.get("flagged"):
            return "已触发红线标记，本维度不参与评分，转独立人事流程。"
        return "未触发红线标记。"

    score = d.get("score")
    conf = d.get("confidence")
    if score is None:
        gaps = "；".join(d.get("gap", [])) or "数据缺失"
        return f"数据不足以支持结论，无数据支撑（缺口：{gaps}）。"

    if conf is not None and conf < 0.5:
        return f"仅作趋势性提示（置信度 {conf} 偏低，样本或覆盖不足），参考值约 {score} 分。"
    return f"得分 {score} 分（置信度 {conf}）。"

def compose(result: dict, evidence: Optional[List[dict]] = None) -> dict:
    pass
    if result.get("object_type") == "structured_task":
        return {"title":"定向回答", "overview":result, "task_outputs":result['task_outputs'],
                "task_analysis":result.get('task_analysis',''), "direct_answer":result['direct_answer']}
    evidence = evidence or []
    dims = result.get("dimensions", [])
    ev_by_dim = {id(d): d.get("evidence", []) for d in dims}

    for e in evidence:
        pass

    sections = []
    for d in dims:
        conclusion = _dimension_conclusion(d)
        ev_list = ev_by_dim.get(id(d), d.get("evidence", []))
        refs = [_fmt_evidence(e) for e in ev_list]
        sec = {
            "dimension": d.get("dimension"),
            "is_redline": bool(d.get("is_redline")),
            "conclusion": conclusion,
            "score": d.get("score"),
            "confidence": d.get("confidence"),
            "evidence_refs": refs,
            "gaps": d.get("gap", []),
        }
        sections.append(sec)

    redline = bool(result.get("redline_flagged"))

    comparable = [s for s in sections
                  if not s["is_redline"] and s["score"] is not None
                  and (s["confidence"] or 0) >= 0.5]
    weakest = min(comparable, key=lambda s: s["score"]) if len(comparable) >= 2 else None

    years = result.get("school_years") or []
    obj_type = result.get("object_type") or "teacher"

    if obj_type == "region":
        is_region_ranking = result.get("analysis_operation") == "region_ranking"
        ranking_items = result.get("ranking_items", []) if is_region_ranking else result.get("top_schools", [])
        return {
            "title": (
                f"区域学校数字化排名报告 · {result.get('region_name')}"
                if is_region_ranking
                else f"区域数字化宏观评估报告 · {result.get('region_name')}"
            ),
            "overview": {
                "object_type": "region",
                "analysis_operation": result.get("analysis_operation", "region_evaluation"),
                "region_name": result.get("region_name"),
                "short_name": result.get("short_name"),
                "school_years": years,
                "school_count": result.get("school_count"),
                "answer_count": result.get("answer_count"),
                "total_unfiltered_count": result.get("total_unfiltered_count"),
                "school_filter": result.get("school_filter"),
                "total_score": result.get("total_score"),
                "max_score": result.get("max_score"),
                "min_score": result.get("min_score"),
                "median_score": result.get("median_score"),
                "strongest_dim": result.get("strongest_dim"),
                "weakest_dim": result.get("weakest_dim"),
                "conclusion": result.get("conclusion"),
                "direct_answer": result.get("direct_answer"),
                "target_dimension": result.get("target_dimension"),
                "target_dimensions": result.get("target_dimensions"),
                "target_dimensions_detail": result.get("target_dimensions_detail"),
                "target_dimension_score": result.get("target_dimension_score"),
                "target_stat": result.get("target_stat"),
                "target_rank": result.get("target_rank"),
                "target_top_n": result.get("target_top_n"),
                "target_school": result.get("target_school"),
                "ranking_spec": result.get("ranking_spec"),
                "ranking_population": result.get("ranking_population"),
                "returned_school_count": result.get("returned_school_count"),
                "school_name": result.get("school_name"),
                "data_coverage": 1.0,
                "overall_confidence": 1.0,
            },
            "direct_answer": result.get("direct_answer"),
            "target_dimension": result.get("target_dimension"),
            "target_dimension_score": result.get("target_dimension_score"),
            "target_stat": result.get("target_stat"),
            "target_rank": result.get("target_rank"),
            "target_top_n": result.get("target_top_n"),
            "target_school": result.get("target_school"),
            "radar_data": result.get("radar_data", {}),
            "dimensions": result.get("dimensions", []),
            "sections": result.get("dimensions", []),
            "tiers": result.get("tiers", []),
            "top_schools": ranking_items,
            "ranking_items": result.get("ranking_items", []),
            "ranking_spec": result.get("ranking_spec"),
            "ranking_population": result.get("ranking_population"),
            "returned_school_count": result.get("returned_school_count"),
            "bottom_schools": result.get("bottom_schools", []),
            "disclaimer": (
                "本排名仅覆盖所示区域、年份与结构化筛选后的有效作答学校；"
                "按未舍入综合均分排序，展示分数保留两位小数，仅供教育行政主管部门决策参考。"
                if is_region_ranking
                else "本报告由区域全量中小学数字化作答数据统计聚合生成，仅供教育行政主管部门决策参考。"
            ),
        }

    if obj_type == "region_compare":
        name_a = result.get("region_name_a") or "区域A"
        name_b = result.get("region_name_b") or "区域B"
        a_wins = result.get("a_wins") if result.get("a_wins") is not None else result.get("wins_a", 0)
        b_wins = result.get("b_wins") if result.get("b_wins") is not None else result.get("wins_b", 0)
        return {
            "title": f"区域数字化双区横向对比报告 · {name_a} VS {name_b}",
            "overview": {
                "object_type": "region_compare",
                "region_name_a": name_a,
                "region_name_b": name_b,
                "region_name": f"{name_a} VS {name_b}",
                "short_name_a": result.get("short_name_a"),
                "short_name_b": result.get("short_name_b"),
                "school_years": years or result.get("school_years", []),
                "year_a": result.get("year_a"),
                "year_b": result.get("year_b"),
                "year_desc": result.get("year_desc"),
                "school_count_a": result.get("school_count_a"),
                "school_count_b": result.get("school_count_b"),
                "answer_count_a": result.get("answer_count_a"),
                "answer_count_b": result.get("answer_count_b"),
                "target_dimension": result.get("target_dimension"),
                "target_stat": result.get("target_stat"),
                "aggregation": result.get("aggregation"),
                "school_filter": result.get("school_filter"),
                "total_score_a": result.get("total_score_a"),
                "total_score_b": result.get("total_score_b"),
                "total_score": f"{result.get('total_score_a')} vs {result.get('total_score_b')}",
                "total_diff": result.get("total_diff"),
                "winner": result.get("winner"),
                "winner_name": result.get("winner_name"),
                "a_wins": a_wins,
                "b_wins": b_wins,
                "ties": result.get("ties", 0),
                "direct_answer": result.get("direct_answer"),
                "data_coverage": 1.0,
                "overall_confidence": 1.0,
            },
            "direct_answer": result.get("direct_answer"),
            "dimensions_comparison": result.get("dimensions_comparison", []),
            "radar_data": result.get("radar_data") or result.get("radar_data_a", {}),
            "radar_comp_data": result.get("radar_comp_data") or result.get("radar_data_b", {}),
            "radar_names": result.get("radar_names", [name_a, name_b]),
            "region_a": result.get("region_a", {}),
            "region_b": result.get("region_b", {}),
            "conclusion": result.get("conclusion"),
            "sections": [],
            "disclaimer": "本横向对比报告基于两区域全量中小学在国家/省级教育数字化问卷填报的实际作答均分折算聚合生成，供宏观教育统筹与区域对标视导参考。",
        }

    if obj_type == "region_longitudinal":
        reg_name = result.get("region_name") or "区域大盘"
        y_a = result.get("year_a") or (years[0] if len(years) >= 1 else "基准年")
        y_b = result.get("year_b") or (years[1] if len(years) >= 2 else "目标年")
        return {
            "title": f"区域数字化两期年度截面变化报告 · {reg_name}",
            "overview": {
                "object_type": "region_longitudinal",
                "region_name": reg_name,
                "short_name": result.get("short_name"),
                "region_type": result.get("region_type"),
                "school_years": [str(y_a), str(y_b)],
                "year_a": str(y_a),
                "year_b": str(y_b),
                "temporal_mode": result.get("temporal_mode"),
                "period_order_policy": result.get("period_order_policy"),
                "comparison_direction": result.get("comparison_direction"),
                "comparison_value_type": result.get("comparison_value_type"),
                "comparison_value_a": result.get("comparison_value_a"),
                "comparison_value_b": result.get("comparison_value_b"),
                "value_unit": result.get("value_unit"),
                "target_stat": result.get("target_stat"),
                "target_dimension": result.get("target_dimension"),
                "aggregation": result.get("aggregation"),
                "school_filter": result.get("school_filter"),
                "total_score_a": result.get("total_score_a"),
                "total_score_b": result.get("total_score_b"),
                "diff": result.get("diff"),
                "pct_change": result.get("pct_change"),
                "trend": result.get("trend"),
                "school_count_a": result.get("school_count_a"),
                "school_count_b": result.get("school_count_b"),
                "answer_count_a": result.get("answer_count_a"),
                "answer_count_b": result.get("answer_count_b"),
                "total_unfiltered_count_a": result.get("total_unfiltered_count_a"),
                "total_unfiltered_count_b": result.get("total_unfiltered_count_b"),
                "relative_change_status": result.get("relative_change_status"),
                "top_gainer": result.get("top_gainer"),
                "top_decliner": result.get("top_decliner"),
                "direct_answer": result.get("direct_answer"),
                "conclusion": result.get("conclusion"),
                "data_coverage": 1.0,
                "overall_confidence": 1.0,
            },
            "direct_answer": result.get("direct_answer"),
            "conclusion": result.get("conclusion"),
            "dimension_deltas": result.get("dimension_deltas", []),
            "radar_data": result.get("radar_data", {}),
            "radar_comp_data": result.get("radar_comp_data", {}),
            "top_gainer": result.get("top_gainer"),
            "top_decliner": result.get("top_decliner"),
            "period_a": result.get("period_a", {}),
            "period_b": result.get("period_b", {}),
            "disclaimer": "本报告比较同一区域两个年度的独立学校截面；学校样本可变化，仅描述观测差异，不代表同一批学校追踪、因果效应或趋势外推。",
        }

    if obj_type == "national_ranking":
        year = result.get("year", "2023")
        dim = result.get("target_dimension")
        title_suffix = f"【{dim}】横向排行榜" if dim else "宏观发展大盘与排行榜"
        return {
            "title": f"全国教育数字化 {year} 年度{title_suffix}",
            "overview": {
                "object_type": "national_ranking",
                "year": year,
                "target_dimension": dim,
                "total_provinces": result.get("total_provinces", 0),
                "large_champion": result.get("large_champion", {}),
                "normal_champion": result.get("normal_champion", {}),
                "overall_champion": result.get("overall_champion", {}),
                "direct_answer": result.get("direct_answer"),
                "total_score": (result.get("large_champion") or {}).get("score"),
                "data_coverage": 1.0,
                "overall_confidence": 1.0,
            },
            "direct_answer": result.get("direct_answer"),
            "conclusion": result.get("conclusion"),
            "rankings": result.get("rankings", []),
            "top_provinces": result.get("top_provinces", []),
            "large_scale_rankings": result.get("large_scale_rankings", []),
            "normal_scale_rankings": result.get("normal_scale_rankings", []),
            "small_scale_rankings": result.get("small_scale_rankings", []),
            "large_champion": result.get("large_champion"),
            "normal_champion": result.get("normal_champion"),
            "overall_champion": result.get("overall_champion"),
            "charts_data": result.get("charts_data", {}),
            "sections": [],
            "disclaimer": result.get("disclaimer", "本榜单基于各省在册中小学在全国教育数字化监测平台的接入问卷作答折算均分，供宏观教育决策与视导参考。"),
        }

    if obj_type == "school_fact":
        return {
            "title": f"学校指标事实查询 · {result.get('school_name')}",
            "overview": {
                "object_type": "school_fact",
                "school_name": result.get("school_name"),
                "school_id": result.get("school_id"),
                "school_years": years,
                "province": result.get("province"),
                "city": result.get("city"),
                "district": result.get("district"),
                "school_type": result.get("school_type"),
                "metric_name": result.get("metric_name"),
                "answer_value_view": result.get("answer_value_view"),
                "value_type": result.get("value_type"),
                "scale": result.get("scale"),
                "coding_status": result.get("coding_status"),
                "coding_notes": result.get("coding_notes"),
                "target_value": result.get("target_value"),
                "fact_data_status": result.get("fact_data_status"),
                "component_total": result.get("component_total"),
                "component_answer_count": result.get("component_answer_count"),
                "component_complete": result.get("component_complete"),
                "ratio": result.get("ratio"),
                "unit": result.get("unit"),
                "normalized_score": result.get("normalized_score"),
                "question_id": result.get("question_id"),
                "question_content": result.get("question_content"),
                "dimension": result.get("dimension"),
                "direct_answer": result.get("direct_answer"),
                "total_score": result.get("normalized_score"),
                "data_coverage": 0.0 if result.get("fact_data_status") == "NO_DATA" else 1.0,
                "overall_confidence": 0.0 if result.get("fact_data_status") == "NO_DATA" else 1.0,
            },
            "direct_answer": result.get("direct_answer"),
            "context_facts": result.get("context_facts", []),
            "history": result.get("history", []),
            "sections": [],
            "disclaimer": "本数据源自学校在国家/省级教育数字化问卷的接入问卷作答库，供核查参考。",
        }

    if obj_type == "school_ranking":
        return {
            "title": f"学校区域数字化排位与对比 · {result.get('school_name')}",
            "overview": {
                "object_type": "school_ranking",
                "school_name": result.get("school_name"),
                "school_id": result.get("school_id"),
                "school_years": years,
                "region_name": result.get("region_name"),
                "short_name": result.get("short_name"),
                "region_type": result.get("region_type"),
                "rank": result.get("rank"),
                "total_schools": result.get("total_schools"),
                "top_pct": result.get("top_pct"),
                "beat_pct": result.get("beat_pct"),
                "percentile": result.get("percentile"),
                "total_score": result.get("total_score"),
                "region_avg": result.get("region_avg"),
                "diff": result.get("diff"),
                "direct_answer": result.get("direct_answer"),
                "data_coverage": 1.0,
                "overall_confidence": 1.0,
            },
            "direct_answer": result.get("direct_answer"),
            "dimensions_comparison": result.get("dimensions_comparison", []),
            "radar_data": result.get("radar_data", {}),
            "radar_region_data": result.get("radar_region_data", {}),
            "peer_schools": result.get("peer_schools", []),
            "sections": [],
            "disclaimer": "本排名基于区域全量中小学在国家/省数字化问卷接入问卷作答折算均分，供教育视导与学校定位参考。",
        }

    if obj_type == "school_compare":
        name_a = result.get("school_name_a") or "学校A"
        name_b = result.get("school_name_b") or "学校B"
        return {
            "title": f"学校数字化双校对比评估报告 · {name_a} VS {name_b}",
            "overview": {
                "object_type": "school_compare",
                "school_name_a": name_a,
                "school_name_b": name_b,
                "school_name": f"{name_a} VS {name_b}",
                "school_years": years or result.get("school_years", []),
                "year_a": result.get("year_a"),
                "year_b": result.get("year_b"),
                "year_desc": result.get("year_desc"),
                "total_score_a": result.get("total_score_a"),
                "total_score_b": result.get("total_score_b"),
                "total_score": f"{result.get('total_score_a')} vs {result.get('total_score_b')}",
                "total_diff": result.get("total_diff"),
                "winner": result.get("winner"),
                "winner_name": result.get("winner_name"),
                "a_wins": result.get("a_wins"),
                "b_wins": result.get("b_wins"),
                "ties": result.get("ties"),
                "direct_answer": result.get("direct_answer"),
                "data_coverage": 1.0,
                "overall_confidence": 1.0,
            },
            "direct_answer": result.get("direct_answer"),
            "dimensions_comparison": result.get("dimensions_comparison", []),
            "radar_data": result.get("radar_data", {}),
            "radar_comp_data": result.get("radar_comp_data", {}),
            "radar_names": result.get("radar_names", [name_a, name_b]),
            "school_a": result.get("school_a", {}),
            "school_b": result.get("school_b", {}),
            "conclusion": result.get("conclusion"),
            "sections": [],
            "disclaimer": "本横向对比报告基于两校在国家/省级教育数字化问卷填报作答的真实评估得分生成，供学校对标互鉴与教育视导参考。",
        }

    is_school = obj_type == "school"
    subject = "该校" if is_school else "该教师"
    report = {
        "title": (
            "学校数字化评估报告（辅助参考材料）"
            if is_school else "教师评估报告（辅助参考材料）"
        ),
        "overview": {
            "teacher_id": result.get("teacher_id"),
            "teacher_dept": result.get("teacher_dept"),
            "school_name": result.get("school_name"),
            "school_years": years,
            "template": result.get("template"),
            "total_score": result.get("total_score"),
            "overall_confidence": result.get("overall_confidence"),
            "data_coverage": result.get("data_coverage"),
            "redline_flagged": redline,
            "object_type": obj_type,
        },
        "trend_note": (
            f"趋势提示：本报告覆盖 {len(years)} 个学年（{'、'.join(str(y) for y in years)}）；"
            "跨学年趋势结论需逐周期单独评估后对比，本报告不做外推。" if len(years) >= 2 else None),
        "weakest_note": (
            f"相对最弱维度：{weakest['dimension']}（{weakest['score']} 分，"
            f"相对{subject}其他已覆盖维度而言，非全样本排名）。" if weakest else None),
        "sections": sections,
        "calibration": result.get("calibration"),
        "disclaimer": "本报告为辅助参考材料，供评审委员会人工复核使用；"
                      "不构成人事裁决的独立依据（考核性 R4）。",
    }
    return report

def render_json(result: dict, evidence: Optional[List[dict]] = None) -> str:
    return json.dumps(compose(result, evidence), ensure_ascii=False, indent=2)

def render_markdown(result: dict, evidence: Optional[List[dict]] = None) -> str:
    r = compose(result, evidence)
    o = r["overview"]
    obj_type = o.get("object_type")

    if obj_type == "region":
        is_region_ranking = o.get("analysis_operation") == "region_ranking"
        lines = [
            f"# {r['title']}",
            "",
            f"**监测地区**：{o['region_name']}　**监测学年**：{', '.join(o['school_years'])}　**监测校总数**：{o['school_count']} 所",
            "",
        ]
        if o.get("direct_answer"):
            lines.extend([
                "### 核心结论与精准回答",
                f"> **{o['direct_answer']}**",
                "",
            ])
        lines.extend([
            f"- 区域综合均分：**{o['total_score']} 分**（最高分 {o['max_score']} 分，最低分 {o['min_score']} 分，中位数 {o['median_score']} 分）",
            f"- 优势维度：**{o['strongest_dim']}**　薄弱短板：**{o['weakest_dim']}**",
            "",
            "## 核心结论",
            "",
            o.get("conclusion") or "",
            "",
            "## 分维度均分表现",
            "",
        ])
        for d in r.get("dimensions", []):
            lines.append(f"- **{d['dimension']}**：{d.get('score')} 分")
        lines.extend([
            "",
            (
                f"## 区域学校排名（返回 {o.get('returned_school_count') or len(r.get('top_schools', []))} 所）"
                if is_region_ranking
                else "## 标杆示范学校 (Top 5)"
            ),
            "",
        ])
        for s in r.get("top_schools", []):
            lines.append(f"{s['rank']}. **{s['school_name']}**（{s['school_type']}）· **{s['score']} 分**")
        lines.extend([
            "",
            "## 免责声明",
            "",
            r["disclaimer"],
            "",
        ])
        return "\n".join(lines)

    if obj_type == "region_compare":
        name_a = o.get("region_name_a") or "区域A"
        name_b = o.get("region_name_b") or "区域B"
        cnt_a = int(o.get("school_count_a") or 0)
        cnt_b = int(o.get("school_count_b") or 0)
        target_dimension = o.get("target_dimension")
        target_stat = o.get("target_stat")
        comparison_label = f"{target_dimension}维度均分" if target_dimension else "区域综合均分"
        core_comparison = (
            f"- 学校数量对比：**{name_a}** `{cnt_a} 所` vs **{name_b}** `{cnt_b} 所`（差值 {cnt_a - cnt_b:+d} 所）"
            if target_stat == "count"
            else f"- {comparison_label}对比：**{name_a}** `{o['total_score_a']} 分` vs **{name_b}** `{o['total_score_b']} 分`（分差 {o['total_diff']:+0.2f} 分）"
        )
        lines = [
            f"# {r['title']}",
            "",
            f"**对比区域**：{name_a}（{cnt_a} 所学校） VS {name_b}（{cnt_b} 所学校）　**监测周期**：{o.get('year_desc') or ', '.join(o.get('school_years', []))}",
            "",
            "### 核心比对结论",
            f"> **{o['direct_answer']}**",
            "",
            core_comparison,
            f"- 优势维度分布：**{name_a}** 领先 {o['a_wins']} 项 · **{name_b}** 领先 {o['b_wins']} 项" + (f" · 持平 {o['ties']} 项" if o.get('ties') else ""),
            "",
            "### 六大监测维度均分与领先对照",
            "",
            f"| 监测维度 | {name_a} | {name_b} | 分差 (A - B) | 领先方 |",
            f"| :--- | :--- | :--- | :--- | :--- |",
        ]
        for dc in r.get("dimensions_comparison", []):
            lead_icon = f"[领先] {name_a}" if dc.get("lead_tag") == "A" else (f"[领先] {name_b}" if dc.get("lead_tag") == "B" else "平手")
            diff_str = f"{dc['diff']:+0.2f} 分"
            lines.append(f"| **{dc['dimension']}** | {dc['score_a']} 分 | {dc['score_b']} 分 | {diff_str} | {lead_icon} |")

        if r.get("conclusion"):
            lines.extend([
                "",
                "### 宏观比对研判",
                "",
                r["conclusion"],
            ])
        lines.extend([
            "",
            "## 免责声明",
            "",
            r["disclaimer"],
            "",
        ])
        return "\n".join(lines)

    if obj_type == "region_longitudinal":
        name = o.get("region_name") or "区域大盘"
        y_a = o.get("year_a") or "基准年"
        y_b = o.get("year_b") or "目标年"
        diff = o.get("diff", 0)
        diff_str = f"+{diff}" if diff > 0 else f"{diff}"
        pct = o.get("pct_change")
        pct_str = (
            (f"+{pct}%" if pct > 0 else f"{pct}%")
            if pct is not None else "不适用（基期为0）"
        )
        unit = o.get("value_unit") or "分"
        value_a = o.get("comparison_value_a", o.get("total_score_a"))
        value_b = o.get("comparison_value_b", o.get("total_score_b"))
        trend = o.get("trend") or "变动"
        lines = [
            f"# {r['title']}",
            "",
            f"**监测地区**：{name}　**基期/末期**：{y_a} 年 → {y_b} 年　**观测变化**：{trend} {diff_str} {unit}（{pct_str}）",
            "",
            "> 两个年度为独立重复截面，学校样本可不同；这不是同一批学校的队列追踪。",
            "",
            "### 核心变动研判与结论",
            f"> **{o.get('direct_answer') or o.get('conclusion') or ''}**",
            "",
            f"- 基期（{y_a} 年）：**{value_a} {unit}**；原始学校 {o.get('total_unfiltered_count_a')} 所，有效学校 {o.get('school_count_a')} 所，有效作答 {o.get('answer_count_a')} 条",
            f"- 末期（{y_b} 年）：**{value_b} {unit}**；原始学校 {o.get('total_unfiltered_count_b')} 所，有效学校 {o.get('school_count_b')} 所，有效作答 {o.get('answer_count_b')} 条",
            f"- 后年减前年：**{diff_str} {unit}**；相对变化 {pct_str}",
        ]
        deltas = r.get("dimension_deltas", [])
        if deltas:
            lines.extend([
                "",
                "### 维度截面变化明细",
                "",
                f"| 监测维度 | {y_a} 年基准 | {y_b} 年末期 | 跨期分差 | 变动方向 |",
                "| :--- | :--- | :--- | :--- | :--- |",
            ])
        for dc in deltas:
            d_diff = dc.get("diff", 0)
            d_diff_str = f"+{d_diff}" if d_diff > 0 else f"{d_diff}"
            lines.append(f"| **{dc['dimension']}** | {dc['score_a']} 分 | {dc['score_b']} 分 | {d_diff_str} 分 | {dc['trend']} |")

        if r.get("conclusion"):
            lines.extend([
                "",
                "### 两期截面综合评价",
                "",
                r["conclusion"],
            ])
        lines.extend([
            "",
            "## 免责声明",
            "",
            r["disclaimer"],
            "",
        ])
        return "\n".join(lines)

    if obj_type == "school_fact":
        lines = [
            f"# {r['title']}",
            "",
            f"**学校**：{o['school_name']}（{o['province']}/{o['city']}/{o['district']}）　**学年**：{', '.join(o['school_years'])}",
            "",
            f"### 核心事实",
            f"> **{o['direct_answer']}**",
            "",
            "### 关联规模与配置背景",
            "",
        ]
        for c in r.get("context_facts", []):
            lines.append(f"- **{c['label']}**：{c['value']}")
        if r.get("history"):
            lines.extend([
                "",
                "### 历史配置记录",
                "",
            ])
            for h in r.get("history"):
                lines.append(f"- **{h['year']} 年**：{h['value']}")
        lines.extend([
            "",
            "## 免责声明",
            "",
            r["disclaimer"],
            "",
        ])

    if obj_type == "school_ranking":
        lines = [
            f"# {r['title']}",
            "",
            f"**学校**：{o['school_name']}　**对比区域**：{o['region_name']}　**学年**：{', '.join(o['school_years'])}",
            "",
            "### 核心排位结论",
            f"> **{o['direct_answer']}**",
            "",
            f"- 区域综合排位：**第 {o['rank']} 名** / 共 {o['total_schools']} 所学校（位居全区前 {o['top_pct']}%）",
            f"- 该校数字化均分：**{o['total_score']} 分**　全区均分：**{o['region_avg']} 分**（分差 {o['diff']:+0.2f} 分）",
            "",
            "### 六大维度与区域均值对照",
            "",
        ]
        for dc in r.get("dimensions_comparison", []):
            lead_tag = "领先" if dc.get("is_lead") else "落后"
            lines.append(f"- **{dc['dimension']}**：该校 {dc['school_score']} 分 vs 区域均分 {dc['region_avg']} 分（{lead_tag} {dc['diff']:+0.2f} 分）")
        if r.get("peer_schools"):
            lines.extend([
                "",
                "### 梯队临近学校参考",
                "",
            ])
            for ps in r.get("peer_schools"):
                star = " [本校]" if ps.get("is_target") else ""
                lines.append(f"{ps['rank']}. **{ps['school_name']}**（{ps['school_type']}）· {ps['score']} 分{star}")
        lines.extend([
            "",
            "## 免责声明",
            "",
            r["disclaimer"],
            "",
        ])
        return "\n".join(lines)

    if obj_type == "school_compare":
        name_a = o.get("school_name_a") or "学校A"
        name_b = o.get("school_name_b") or "学校B"
        lines = [
            f"# {r['title']}",
            "",
            f"**对比标的**：{name_a} VS {name_b}　**监测周期**：{o.get('year_desc') or ', '.join(o.get('school_years', []))}",
            "",
            "### 核心比对结论",
            f"> **{o['direct_answer']}**",
            "",
            f"- 综合得分对比：**{name_a}** `{o['total_score_a']} 分` vs **{name_b}** `{o['total_score_b']} 分`（综合分差 {o['total_diff']:+0.2f} 分）",
            f"- 优势维度分布：**{name_a}** 领先 {o['a_wins']} 项 · **{name_b}** 领先 {o['b_wins']} 项" + (f" · 持平 {o['ties']} 项" if o.get('ties') else ""),
            "",
            "### 六大监测维度得分与领先对照",
            "",
            f"| 监测维度 | {name_a} | {name_b} | 分差 (A - B) | 领先方 |",
            f"| :--- | :--- | :--- | :--- | :--- |",
        ]
        for dc in r.get("dimensions_comparison", []):
            lead_icon = f"[领先] {name_a}" if dc.get("lead_tag") == "A" else (f"[领先] {name_b}" if dc.get("lead_tag") == "B" else "平手")
            diff_str = f"{dc['diff']:+0.2f} 分"
            lines.append(f"| **{dc['dimension']}** | {dc['score_a']} 分 | {dc['score_b']} 分 | {diff_str} | {lead_icon} |")

        if r.get("conclusion"):
            lines.extend([
                "",
                "### 综合比对研判",
                "",
                r["conclusion"],
            ])
        lines.extend([
            "",
            "## 免责声明",
            "",
            r["disclaimer"],
            "",
        ])
        return "\n".join(lines)

    if obj_type == "national_ranking":
        year = o.get("year") or "2023"
        dim = o.get("target_dimension")
        dim_label = f"【{dim}】维度" if dim else "综合发展水平"
        lines = [
            f"# {r['title']}",
            "",
            f"**监测周期**：{year} 年　**评估维度**：{dim_label}　**在册省份**：{o.get('total_provinces', 0)} 个",
            "",
            "### 核心评估结论",
            f"> **{r['direct_answer']}**",
            "",
            "### 全国各省份教育数字化水平排名榜单",
            "",
            "| 排名 | 省份 | 监测样本量 | 样本分级 | 均分 | 基础设施 | 数字素养 | 教育教学 | 数字资源 | 教育治理 | 保障机制 |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        for item in r.get("rankings", [])[:20]:
            dims = item.get("dimensions", {})
            lines.append(
                f"| {item['rank']} | **{item['province']}** | {item['school_count']} 所 | {item['sample_tier']} | **{item['score']} 分** | "
                f"{dims.get('基础设施', '-')} | {dims.get('数字素养', '-')} | {dims.get('教育教学', '-')} | {dims.get('数字资源', '-')} | {dims.get('教育治理', '-')} | {dims.get('保障机制', '-')} |"
            )
        lines.extend([
            "",
            "## 免责声明",
            "",
            r["disclaimer"],
            "",
        ])
        return "\n".join(lines)

    if o.get('object_type') == 'school':
        header_target = f"学校：{o.get('school_name')}"
    else:
        tid = o.get('teacher_id') or "教师"
        alias = {"T001": "示例教师一", "T002": "示例教师二", "T003": "示例教师三", "T004": "示例教师四"}.get(tid, "")
        header_target = f"教师：{tid}（{alias} · {o.get('teacher_dept') or '任课组'}）" if alias else f"教师：{tid}（{o.get('teacher_dept') or ''}）"
    lines = [
        f"# {r['title']}",
        "",
        f"{header_target}　"
        f"评估学年：{', '.join(o['school_years'] or [])}　模板：{o.get('template') or ''}",
        "",
        f"- 综合得分：**{o['total_score']}**　数据覆盖率：{o['data_coverage']}　综合置信度：{o['overall_confidence']}",
    ]
    if o.get("redline_flagged"):
        lines += ["", "> **[红线预警]** **红线标记已触发**：本报告总分不参与评分，请转独立人事流程。"]

    if r.get("trend_note"):
        lines += ["", f"> {r['trend_note']}"]
    if r.get("weakest_note"):
        lines += ["", f"> {r['weakest_note']}"]

    lines += ["", "## 分维度结论", ""]
    for s in r["sections"]:
        lines.append(f"- **{s['dimension']}**：{s['conclusion']}")
        for ref in s.get("evidence_refs", []):
            lines.append(f"  - {ref}")
        if s.get("gaps"):
            lines.append(f"  - 缺口：{ '；'.join(s['gaps']) }")
        lines.append("")

    lines += ["", "## 免责声明", "", r["disclaimer"], ""]
    return "\n".join(lines)

if __name__ == "__main__":

    import os, sys
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from scripts.indicator_engine import evaluate_teacher, load_yaml
    from packages.indicator_engine.calibration import attach_calibration
    res = evaluate_teacher("T003", ["2023-2024", "2024-2025"], load_yaml("2025-2026_v1"))
    attach_calibration(res, group_col="dept_id")
    print(render_markdown(res))
