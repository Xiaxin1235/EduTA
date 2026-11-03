"""学校具体指标与客观事实查询引擎 (Fact Engine)。

针对开放式提问中的微观指标（如平板电脑配置、网络带宽、生机比、教师人数等），
从 SQLite 底层 answers / schools / questions 提取高置信度事实数据与背景上下文。
"""

import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from packages.agent.indicator_catalog import match_indicator
from scripts.school_engine import connect, find_schools, get_school


def _is_missing_school_field(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _school_field_or_none(value: Any) -> Any:
    return None if _is_missing_school_field(value) else value


def query_school_fact(
    school_keyword_or_id: Union[str, int],
    metric_or_text: Union[str, Dict[str, Any]],
    year: Optional[str] = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """查询某所学校的具体指标事实。

    参数:
      school_keyword_or_id: 学校名关键词或学校 ID
      metric_or_text: match_indicator 返回的 dict，或者用户原始文本
      year: 指定年份（如 "2023"），若为 None 则默认最新年份
    """
    # 1. 规范化指标信息
    if isinstance(metric_or_text, dict):
        metric_info = metric_or_text
    else:
        metric_info = match_indicator(str(metric_or_text))

    if not metric_info:
        return {"error": f"未能从提问中识别出具体指标项（支持：平板电脑、台式机、带宽、三个课堂、培训、师机比等）"}

    # 2. 定位目标学校
    school = None
    if isinstance(school_keyword_or_id, int) or (
        isinstance(school_keyword_or_id, str)
        and school_keyword_or_id.isdigit()
    ):
        school = get_school(int(school_keyword_or_id), connection=connection)
    elif isinstance(school_keyword_or_id, str) and school_keyword_or_id.startswith("id:"):
        school = get_school(int(school_keyword_or_id.split(":", 1)[1]), connection=connection)
    else:
        hits = find_schools(
            str(school_keyword_or_id).strip(), year=year, limit=5, connection=connection
        )
        if not hits:
            return {"error": f"未检索到匹配的学校「{school_keyword_or_id}」" + (f"（年份 {year}）" if year else "")}
        school = get_school(hits[0]["id"], connection=connection)

    if not school:
        return {"error": "未找到有效学校档案"}

    school_id = school["id"]
    school_name = school["school_name"]
    active_year = school.get("year") or year
    if not active_year:
        return {"error": "学校档案缺少年份，不能在未绑定时期的情况下执行事实查询"}

    conn = connection or connect()
    owns_connection = connection is None

    # 3. 查寻同名学校全部建档历史，用于展示跨学年追踪
    hist_schools = conn.execute(
        "SELECT id, year, teacher_num, student_num, class_num FROM schools WHERE school_name=? ORDER BY year ASC",
        (school_name,),
    ).fetchall()
    hist_dict = {str(r["year"]): dict(r) for r in hist_schools}

    out: Dict[str, Any] = {
        "object_type": "school_fact",
        "school_id": school_id,
        "school_name": school_name,
        "school_years": [active_year],
        "year": active_year,
        "province": school.get("province"),
        "city": school.get("city"),
        "district": school.get("district"),
        "school_type": _school_field_or_none(school.get("school_type")),
        "school_area_type": _school_field_or_none(school.get("school_area_type")),
        "teacher_num": _school_field_or_none(school.get("teacher_num")),
        "student_num": _school_field_or_none(school.get("student_num")),
        "class_num": _school_field_or_none(school.get("class_num")),
        "fact_type": metric_info["type"],
        "metric_name": metric_info.get("name"),
        "unit": metric_info.get("unit", ""),
        "value_type": metric_info.get("value_type"),
        "scale": metric_info.get("scale"),
        "coding_status": metric_info.get("coding_status", "not_applicable"),
        "coding_notes": metric_info.get("coding_notes", ""),
        "context_facts": [],
        "history": [],
        "fact_data_status": "ANSWER",
    }

    # 4. 分支执行
    if metric_info["type"] == "question_set":
        qids = [int(qid) for qid in metric_info["qids"]]
        specs = list(metric_info["items"])
        marks = ",".join("?" for _ in qids)
        rows = conn.execute(
            f"SELECT question_id,value,normalized_value FROM answers WHERE school_id=? AND question_id IN ({marks})",
            (school_id, *qids),
        ).fetchall()
        row_map = {int(row["question_id"]): row for row in rows}
        is_norm = (
            metric_info.get("value_view") == "normalized_score"
            or out.get("value_view") == "normalized_score"
        )
        item_results = []
        for qid, spec in zip(qids, specs):
            r = row_map.get(qid)
            if r is None:
                value = None
                display = None
            elif is_norm:
                norm = r["normalized_value"]
                value = norm
                display = round(norm * 100, 1) if norm is not None else None
            else:
                value = r["value"]
                display = int(value) if value is not None and value == int(value) else value
            item_results.append({
                "question_id": qid,
                "metric_name": str(spec["name"]),
                "target_value": display,
                "unit": "分" if is_norm else str(spec.get("unit") or ""),
                "coding_status": str(spec.get("coding_status") or "not_applicable"),
                "fact_data_status": "ANSWER" if display is not None else "NO_DATA",
            })
        complete = all(item["fact_data_status"] == "ANSWER" for item in item_results)
        out["item_results"] = item_results
        out["derived_result"] = None
        derived = metric_info.get("derived")
        if complete and isinstance(derived, dict):
            left, right = (float(item["target_value"]) for item in item_results)
            op = derived["operation"]
            norm_op = "add" if op in {"sum", "add"} else ("divide" if op in {"ratio", "divide"} else op)
            if norm_op in {"divide_percent", "divide"} and right == 0:
                complete = False
            else:
                if norm_op == "subtract":
                    value = left - right
                    unit = item_results[0]["unit"]
                elif norm_op == "divide_percent":
                    value = left / right * 100
                    unit = "%"
                elif norm_op == "add":
                    value = left + right
                    unit = item_results[0]["unit"]
                elif norm_op == "divide":
                    value = left / right
                    unit = "倍"
                else:
                    value = left - right
                    unit = item_results[0]["unit"]

                out["derived_result"] = {
                    "operation": norm_op,
                    "left_qid": qids[0],
                    "right_qid": qids[1],
                    "value": round(value, 6),
                    "unit": unit,
                }
        out["fact_data_status"] = "ANSWER" if complete else "NO_DATA"
        if complete:
            values = "、".join(
                f"{item['metric_name']}为{item['target_value']}{item['unit']}"
                for item in item_results
            )
            extra = ""
            if out["derived_result"] is not None:
                d = out["derived_result"]
                v = d["value"]
                v_disp = int(v) if v == int(v) else round(v, 4)
                if d["operation"] == "subtract":
                    extra = f"；前者减后者为{v_disp}{d['unit']}"
                elif d["operation"] == "add":
                    extra = f"；两者合计为{v_disp}{d['unit']}"
                elif d["operation"] == "divide_percent":
                    extra = f"；前者除以后者为{round(v, 2)}{d['unit']}"
                elif d["operation"] == "divide":
                    extra = f"；前者是后者的{v_disp}{d['unit']}"
            out["direct_answer"] = f"{school_name}{active_year}年：{values}{extra}。"
        else:
            out["direct_answer"] = f"{school_name}{active_year}年两项所需原始作答不完整，无法给出完整结果。"

    elif metric_info["type"] == "school_field":
        field = metric_info["field"]
        raw_val = school.get(field)
        missing_value = _is_missing_school_field(raw_val)
        unit = metric_info.get("unit", "")
        out["target_value"] = None if missing_value else raw_val
        if missing_value:
            out["fact_data_status"] = "NO_DATA"
            out["direct_answer"] = f"{school_name}（{active_year}年）的【{metric_info['name']}】未登记（数据缺口）。"
        else:
            out["direct_answer"] = f"{school_name}（{active_year}年）的【{metric_info['name']}】为 **{raw_val} {unit}**。"
        # 历史趋势
        for y, h in hist_dict.items():
            history_value = h.get(field)
            history_display = "-" if _is_missing_school_field(history_value) else history_value
            out["history"].append({"year": y, "value": f"{history_display} {unit}"})

    elif metric_info["type"] == "question":
        qid = metric_info["qid"]
        q_row = conn.execute(
            "SELECT id, content, level1_name, level2_name FROM questions WHERE id=?", (qid,)
        ).fetchone()
        q_content = q_row["content"] if q_row else metric_info["name"]
        l1 = q_row["level1_name"] if q_row else "基础设施"
        l2 = q_row["level2_name"] if q_row else "终端"

        ans_row = conn.execute(
            "SELECT value, normalized_value FROM answers WHERE school_id=? AND question_id=?",
            (school_id, qid),
        ).fetchone()
        val = ans_row["value"] if ans_row else None
        norm = ans_row["normalized_value"] if ans_row else None

        coding_status = str(metric_info.get("coding_status") or "not_applicable")
        coded_meaning_unavailable = (
            coding_status == "source_missing"
            and (
                metric_info.get("scale") == "coded_category"
                or metric_info.get("value_type") in {"category", "boolean"}
            )
        )
        unit = str(metric_info.get("unit") or "")
        if not unit and not coded_meaning_unavailable:
            unit = "项"
        display_val = int(val) if val is not None and val == int(val) else val
        norm_score = round(norm * 100, 1) if norm is not None else None

        out["unit"] = unit
        out["question_id"] = qid
        out["question_content"] = q_content
        out["dimension"] = l1
        out["sub_dimension"] = l2
        out["target_value"] = display_val
        out["normalized_score"] = norm_score

        if display_val is not None and coded_meaning_unavailable:
            out["direct_answer"] = (
                f"{school_name} 在 {active_year} 年【{metric_info['name']}】的原始编码为 **{display_val}**。"
                "当前语义目录未取得该题编码表，不能把该编码解释为数量、类别或是/否。"
            )
        elif display_val is not None:
            out["direct_answer"] = (
                f"{school_name} 在 {active_year} 年官方登记配置的【{metric_info['name']}】具体为 **{display_val} {unit}**"
                + (f"（标准化得分 {norm_score} 分）" if norm_score is not None else "")
                + "。"
            )
        else:
            out["fact_data_status"] = "NO_DATA"
            out["direct_answer"] = f"{school_name} 在 {active_year} 年未作答此项指标（数据缺口）。"

        # 关联题号上下文提取（如查教师平板，一并查出台式机、笔记本、学生平板等）
        related_qids = metric_info.get("related", [])
        if related_qids:
            q_placeholders = ",".join("?" for _ in related_qids)
            rel_rows = conn.execute(
                f"SELECT q.id, q.content, a.value FROM questions q "
                f"LEFT JOIN answers a ON q.id=a.question_id AND a.school_id=? "
                f"WHERE q.id IN ({q_placeholders}) ORDER BY q.id",
                [school_id] + related_qids,
            ).fetchall()
            for r in rel_rows:
                v = r["value"]
                v_disp = int(v) if v is not None and v == int(v) else (v if v is not None else "未填")
                clean_name = r["content"].replace("学校统一配备的", "").replace("数量", "")
                r_unit = "台"
                if any(k in r["content"] for k in ("工作内容", "模式", "措施", "功能", "制度", "规章", "办法", "应用")):
                    r_unit = "项"
                elif any(k in r["content"] for k in ("人", "人数", "人次")):
                    r_unit = "人"
                elif any(k in r["content"] for k in ("间", "教室")):
                    r_unit = "间"
                elif any(k in r["content"] for k in ("Mbps", "带宽")):
                    r_unit = "Mbps"
                out["context_facts"].append({"qid": r["id"], "label": clean_name, "value": f"{v_disp} {r_unit}"})

        # 跨学年历史数据
        for y, h in hist_dict.items():
            sid_hist = h["id"]
            h_ans = conn.execute(
                "SELECT value FROM answers WHERE school_id=? AND question_id=?", (sid_hist, qid)
            ).fetchone()
            hv = h_ans["value"] if h_ans else None
            hv_disp = int(hv) if hv is not None and hv == int(hv) else (hv if hv is not None else "-")
            history_value = f"原始编码 {hv_disp}" if coded_meaning_unavailable else f"{hv_disp} {unit}"
            out["history"].append({"year": y, "value": history_value.strip()})

    elif metric_info["type"] == "compound":
        qids = metric_info["qids"]
        q_placeholders = ",".join("?" for _ in qids)
        rows = conn.execute(
            f"SELECT question_id, value FROM answers WHERE school_id=? AND question_id IN ({q_placeholders})",
            [school_id] + qids,
        ).fetchall()
        answer_rows = [r for r in rows if r["value"] is not None]
        val_map = {r["question_id"]: r["value"] for r in answer_rows}
        total_dev = sum(val_map.values())
        component_complete = set(val_map) == {int(qid) for qid in qids}
        total_dev_disp = int(total_dev) if total_dev == int(total_dev) else total_dev

        out["target_value"] = None
        out["component_total"] = total_dev_disp if answer_rows else None
        out["component_answer_count"] = len(answer_rows)
        out["component_complete"] = component_complete
        ckey = metric_info.get("compound_key")
        unit = metric_info.get("unit", "")

        if ckey == "生机比":
            student_value = school.get("student_num")
            s_num = 0.0 if _is_missing_school_field(student_value) else float(student_value)
            if component_complete and total_dev > 0 and s_num > 0:
                ratio = round(s_num / total_dev, 2)
                per_student = round(total_dev / s_num, 3)
                out["ratio"] = ratio
                out["target_value"] = ratio
                out["direct_answer"] = (
                    f"{school_name}（{active_year}年）在校学生 {int(s_num)} 人，各类学生终端配置共 **{total_dev_disp} 台**。"
                    f"折合生机比约为 **{ratio} 人/台**（平均每名在校生配置 {per_student} 台终端设备）。"
                )
            else:
                out["fact_data_status"] = "NO_DATA"
                out["direct_answer"] = (
                    f"{school_name} 暂无完整的生机比计算数据"
                    f"（有效终端题项 {len(answer_rows)}/{len(qids)}，在校生: {s_num}，学生终端合计: {total_dev_disp} 台）。"
                )

        elif ckey == "师机比":
            teacher_value = school.get("teacher_num")
            t_num = 0.0 if _is_missing_school_field(teacher_value) else float(teacher_value)
            if component_complete and t_num > 0:
                ratio = round(total_dev / t_num, 2)
                out["ratio"] = ratio
                out["target_value"] = ratio
                out["direct_answer"] = (
                    f"{school_name}（{active_year}年）专任教师 {int(t_num)} 人，各类教师教学终端共 **{total_dev_disp} 台**。"
                    f"折合师均终端配置比为 **{ratio} 台/人**。"
                )
            else:
                out["fact_data_status"] = "NO_DATA"
                out["direct_answer"] = (
                    f"{school_name} 暂无完整的师机比计算数据"
                    f"（有效终端题项 {len(answer_rows)}/{len(qids)}，专任教师: {t_num}，教师终端合计: {total_dev_disp} 台）。"
                )
        else:
            if component_complete:
                out["target_value"] = total_dev_disp
                out["direct_answer"] = f"{school_name}（{active_year}年）的【{metric_info['name']}】合计为 **{total_dev_disp} {unit}**。"
            else:
                out["fact_data_status"] = "NO_DATA"
                out["direct_answer"] = (
                    f"{school_name}（{active_year}年）的【{metric_info['name']}】题项数据不完整"
                    f"（有效题项 {len(answer_rows)}/{len(qids)}），不能计算完整合计。"
                )

    # 补充基础规模上下文
    teacher_display = "-" if _is_missing_school_field(school.get("teacher_num")) else school.get("teacher_num")
    student_display = "-" if _is_missing_school_field(school.get("student_num")) else school.get("student_num")
    class_display = "-" if _is_missing_school_field(school.get("class_num")) else school.get("class_num")
    out["context_facts"].insert(0, {"label": "专任教师数", "value": f"{teacher_display} 人"})
    out["context_facts"].insert(1, {"label": "在校学生数", "value": f"{student_display} 人"})
    out["context_facts"].insert(2, {"label": "教学班级数", "value": f"{class_display} 个"})

    metric_key = (
        f"question:{metric_info['qid']}" if metric_info["type"] == "question"
        else f"question_set:{','.join(str(qid) for qid in metric_info['qids'])}"
        if metric_info["type"] == "question_set"
        else f"compound:{metric_info.get('compound_key') or metric_info.get('name')}"
        if metric_info["type"] == "compound"
        else f"school_field:{metric_info['field']}"
    )
    out["execution_scope"] = {
        "query_template": "school_fact_set_v1" if metric_info["type"] == "question_set" else "school_fact_v1",
        "school_id": int(school_id),
        "school_name": school_name,
        "period": str(active_year),
        "metric_type": metric_info["type"],
        "metric_key": metric_key,
        "indicator_ids": [int(qid) for qid in (
            [metric_info["qid"]] if metric_info["type"] == "question"
            else metric_info.get("qids", [])
        )],
    }
    if metric_info["type"] == "question_set":
        out["execution_scope"]["derived"] = metric_info.get("derived")
    if owns_connection:
        conn.close()
    return out
