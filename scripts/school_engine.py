"""学校数字化评估引擎（真实数据）。

数据源：data/real/education_digitization.sqlite
模板：evaluation_templates/school_digitization_v1.yaml

口径：
  单题得分 = normalized_value × 100（0~100）
  维度得分 = 该维度已作答题的等权均值（题权当前全为 1）
  综合得分 = 已覆盖一级维度按模板权重加权
  覆盖率   = 已作答题数 / 模板总题数
  缺口     = 无答案的题，显式列出（最多 8 条）
"""

import os
import re
import sqlite3
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from packages.core.frozen_paths import resource_root  # noqa: E402


def _db_path():
    from packages.core.research_data import enabled, config
    if enabled():
        return config()['data']['source']['path']
    configured = str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    candidates = [
        os.path.join(resource_root(), "data", "real", "education_digitization.sqlite"),
        os.path.join(ROOT, "data", "real", "education_digitization.sqlite"),
        os.path.join(os.path.dirname(sys.executable), "data", "real", "education_digitization.sqlite")
        if getattr(sys, "frozen", False) else "",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return candidates[0]


def connect(data_path=None):
    """Open the selected data source read-only.

    ``data_path`` lets a contract request keep using the exact source selected
    when its service instance was created instead of resolving a mutable
    environment variable again between preparation and execution.
    """
    p = os.path.abspath(os.path.expanduser(str(data_path))) if data_path else _db_path()
    if not os.path.isfile(p):
        raise FileNotFoundError(
            f"未找到真实库 {p}。请先运行 python scripts/import_real_data.py")
    uri_path = quote(Path(p).resolve().as_posix(), safe="/:")
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    from packages.core.research_data import enabled, attach
    if enabled():
        attach(conn, p)
    return conn


def _research_identity():
    from packages.core.research_data import enabled, identity
    return identity() if enabled() else None


def data_source_status():
    p = _db_path()
    configured = bool(str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip())
    return {
        "status": "available" if os.path.isfile(p) else "unavailable",
        "active_path": p,
        "active_source": "environment_override" if configured else "packaged_or_source_default",
        "configured_by_environment": configured,
        "read_only": True,
        "research_dataset": _research_identity(),
    }


@lru_cache(maxsize=4)
def load_school_template():
    try:
        import yaml
    except ImportError:
        return None
    path = os.path.join(resource_root(), "evaluation_templates", "school_digitization_v1.yaml")
    if not os.path.isfile(path):
        path = os.path.join(ROOT, "evaluation_templates", "school_digitization_v1.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


_ALIAS_RULES = [
    ("华中师大一附中", "华中师范大学第一附属中学"),
    ("华师一附中", "华中师范大学第一附属中学"),
    ("华中师大", "华中师范大学"),
    ("华师大", "华中师范大学"),
    ("北师大", "北京师范大学"),
    ("华东师大", "华东师范大学"),
    ("东北师大", "东北师范大学"),
    ("陕师大", "陕西师范大学"),
    ("南师大", "南京师范大学"),
    ("湖南师大", "湖南师范大学"),
    ("华南师大", "华南师范大学"),
    ("北大附中", "北京大学附属中学"),
    ("清华附中", "清华大学附属中学"),
    ("人大附中", "中国人民大学附属中学"),
    ("一附中", "第一附属中学"),
    ("二附中", "第二附属中学"),
    ("三附中", "第三附属中学"),
    ("附中", "附属中学"),
    ("附小", "附属小学"),
    ("一中", "第一中学"),
    ("二中", "第二中学"),
    ("三中", "第三中学"),
    ("四中", "第四中学"),
    ("五中", "第五中学"),
    ("六中", "第六中学"),
    ("七中", "第七中学"),
    ("八中", "第八中学"),
    ("九中", "第九中学"),
    ("十中", "第十中学"),
]


def expand_school_alias(keyword: str) -> str:
    """将学校常见简称、高校简称与附属学校俗称规范化为主校全名。"""
    from packages.core.school_names import normalize_school_numbers
    kw = normalize_school_numbers(str(keyword).strip(),'chinese')
    curr = kw
    for src, dst in _ALIAS_RULES:
        if src in curr:
            curr = re.sub(re.escape(src)+(r'(?!学)' if src.endswith('中') else ''),lambda _:dst,curr)
    return curr


def find_schools(keyword, year=None, limit=20, connection=None):
    """根据关键词模糊检索学校，支持关键词出现在校名开头、中间或结尾，支持别名与区域联合消解。

    匹配与排序规则（非仅开头匹配）：
    1. 完全匹配用户原词 / 规范别名 / 城市补全名（最高优先级）
    2. 结尾匹配（如搜“朝阳学校”匹配“华中师范大学第一附属中学朝阳学校”，搜“王家河中学”匹配“武汉市黄陂区王家河街王家河中学”）
    3. 开头匹配（如搜“华中师范大学”匹配“华中师范大学第一附属中学”）
    4. 中间包含匹配（原词或别名出现在校名中间任意位置，如搜“第一附属中学”匹配“华中师范大学第一附属中学初中部”）
    5. 行政区划+校名联合匹配（如搜“武汉市王家河中学”自动联合匹配市域与校名）
    6. 校名字符长度升序（越接近检索词长度越优先） -> 最新年份降序
    """
    conn = connection or connect()
    owns_connection = connection is None
    kw = str(keyword).strip()
    expanded_kw = expand_school_alias(kw)
    from packages.core.school_names import normalize_school_numbers
    variants = [kw,expanded_kw,normalize_school_numbers(kw)]

    # 支持 2 字城市名简称自动补齐“市”（如 北京二中 -> 北京市第二中学，武汉二中 -> 武汉市第二中学）
    m_city = re.match(r"^(北京|天津|上海|重庆|武汉|长沙|广州|深圳|杭州|南京|成都|西安|沈阳|大连|青岛|厦门|济南|宁波|哈尔滨|长春|福州|南昌|郑州|合肥|南宁|海口|贵阳|昆明|兰州|西宁|银川|石家庄|太原)(.+)$", kw)
    if m_city:
        city_full = m_city.group(1) + "市"
        core = expand_school_alias(m_city.group(2))
        variants.append(city_full + core)
        variants.append(city_full + m_city.group(2))

    variants = list(dict.fromkeys(variants))

    where_parts = []
    params = []
    for v in variants:
        where_parts.append("school_name LIKE ?")
        params.append(f"%{v}%")

    # 1. 检查是否包含行政区划前缀（如 武汉市王家河中学、北京市朝阳学校）
    m_reg = re.match(r"^((?:[\u4e00-\u9fff]{2,6}?(?:省|市|区|县|旗))+)(.+)$", kw)
    if m_reg:
        reg_prefix, core_name = m_reg.group(1), m_reg.group(2).strip()
        where_parts.append(
            "((COALESCE(province, '') || COALESCE(city, '') || COALESCE(district, '') || school_name) LIKE ?"
            " AND (COALESCE(province, '') || COALESCE(city, '') || COALESCE(district, '') || school_name) LIKE ?)"
        )
        params.extend([f"%{reg_prefix}%", f"%{core_name}%"])

    # 2. 检查是否包含空格/逗号分隔的多词（如 武汉 华师一附中）
    tokens = [t for t in re.split(r"[\s,，]+", kw) if len(t) >= 2]
    if len(tokens) >= 2:
        parts = [
            "(COALESCE(province, '') || COALESCE(city, '') || COALESCE(district, '') || school_name) LIKE ?"
            for _ in tokens
        ]
        where_parts.append("(" + " AND ".join(parts) + ")")
        params.extend([f"%{t}%" for t in tokens])

    where_sql = " OR ".join(where_parts)
    year_clause = " AND year=? " if year else " "
    if year:
        params.append(str(year))

    case_whens = []
    order_params = []
    for i, v in enumerate(variants):
        case_whens.append("WHEN school_name = ? THEN " + str(i * 3))
        order_params.append(v)
        case_whens.append("WHEN school_name LIKE ? THEN " + str(i * 3 + 1))
        order_params.append(f"%{v}")
        case_whens.append("WHEN school_name LIKE ? THEN " + str(i * 3 + 2))
        order_params.append(f"{v}%")

    case_sql = "CASE " + " ".join(case_whens) + " ELSE 99 END, LENGTH(school_name) ASC, year DESC, id DESC"

    full_sql = f"""
    SELECT id, school_name, province, city, district, year, school_type, region
    FROM schools
    WHERE ({where_sql}){year_clause}
    ORDER BY {case_sql}
    LIMIT ?
    """
    full_params = params + order_params + [limit]
    rows = conn.execute(full_sql, full_params).fetchall()
    if owns_connection:
        conn.close()
    return [dict(r) for r in rows]


def get_school(school_id, connection=None):
    conn = connection or connect()
    owns_connection = connection is None
    row = conn.execute("SELECT * FROM schools WHERE id=?", (int(school_id),)).fetchone()
    if owns_connection:
        conn.close()
    return dict(row) if row else None


def evaluate_school(
    school_id,
    template=None,
    connection=None,
    target_dimension=None,
    target_dimensions=None,
    indicator_ids=None,
    topic=None,
):
    if topic is not None:
        from packages.semantic_query.topic_tasks import school_score
        return school_score(school_id,topic,connection)
    template = template or load_school_template()
    if template is None:
        return {"error": "学校数字化评估模板不可用"}
    conn = connection or connect()
    owns_connection = connection is None
    school = get_school(school_id, connection=conn)
    if not school:
        if owns_connection:
            conn.close()
        return {"error": f"学校 {school_id} 不存在"}

    answers = {r["question_id"]: r for r in conn.execute(
        "SELECT question_id, value, normalized_value FROM answers WHERE school_id=?",
        (int(school_id),)).fetchall()}
    if owns_connection:
        conn.close()

    selected_ids = {int(value) for value in (indicator_ids or ())}
    template_dimensions = list(template.get("dimensions", []))
    if target_dimensions and len(target_dimensions) == 2:
        by_name = {str(item.get("name") or ""): item for item in template_dimensions}
        if len(set(target_dimensions)) != 2 or any(name not in by_name for name in target_dimensions):
            return {"error": "学校评估双维范围无效"}
        template_dimensions = [by_name[name] for name in target_dimensions]
    if target_dimension:
        template_dimensions = [
            item for item in template_dimensions
            if str(item.get("name") or "") == str(target_dimension)
        ]
        if not template_dimensions:
            return {"error": f"学校评估模板不包含维度「{target_dimension}」"}

    dims_out = []
    total = 0.0
    covered_w = 0.0
    weight_sum = 0.0
    n_metrics = 0
    n_answered = 0

    for dim in template_dimensions:
        metrics = [
            metric for metric in (dim.get("metrics") or [])
            if not selected_ids or int(metric["id"]) in selected_ids
        ]
        if not metrics:
            continue
        n_metrics += len(metrics)
        scores = []
        evidence = []
        gaps = []
        for m in metrics:
            qid = int(m["id"])
            ans = answers.get(qid)
            if ans is None or ans["normalized_value"] is None:
                gaps.append(m.get("content") or f"题{qid}")
                continue
            n_answered += 1
            nv = float(ans["normalized_value"])
            sc = max(0.0, min(100.0, nv * 100.0))
            scores.append(sc)
            evidence.append({
                "metric": f"q{qid}",
                "label": m.get("content"),
                "raw": round(nv, 4),
                "source": "answers.normalized_value",
                "period": [school.get("year")],
                "formula": "normalized_value × 100",
            })
        dim_score = round(sum(scores) / len(scores), 2) if scores else None
        conf = round(len(scores) / len(metrics), 2) if metrics else 0.0
        w = float(dim.get("weight") or 0)
        weight_sum += w
        if dim_score is not None:
            total += dim_score * w
            covered_w += w
        dims_out.append({
            "dimension": dim.get("name"),
            "is_redline": False,
            "score": dim_score,
            "confidence": conf,
            "weight": w,
            "evidence": evidence,
            "gap": gaps[:8],
        })

    coverage = n_answered / n_metrics if n_metrics else 0.0
    total_score = round(total / covered_w, 2) if covered_w else None
    overall_conf = round(covered_w / weight_sum, 2) if weight_sum else 0.0

    if not dims_out:
        return {"error": "学校评估范围没有可执行的模板题项"}
    diff = None
    target_dim_scores = {}
    if target_dimensions and len(target_dimensions) >= 2:
        dim_map = {d["dimension"]: d["score"] for d in dims_out if d.get("score") is not None}
        d1 = target_dimensions[0]
        d2 = target_dimensions[1]
        s1 = dim_map.get(d1)
        s2 = dim_map.get(d2)
        if s1 is not None and s2 is not None:
            diff = round(s1 - s2, 2)
            target_dim_scores = {d1: s1, d2: s2}
            direct_answer = (
                f"{school.get('school_name')}在{school.get('year')}年【{d1}】维度得分为{s1:.2f}分，"
                f"【{d2}】维度得分为{s2:.2f}分，"
                f"前者减后者差值为{diff:+.2f}分（分差{diff:.2f}分）。"
            )
        else:
            return {"error": "学校评估双维分值不全"}
    elif target_dimension:
        direct_answer = (
            f"{school.get('school_name')}在{school.get('year')}年【{target_dimension}】维度得分为{total_score}分，"
            f"覆盖{n_answered}/{n_metrics}项。"
        )
    else:
        direct_answer = f"{school.get('school_name')}在{school.get('year')}年数字化总体得分为{total_score}分，覆盖{n_answered}/{n_metrics}项。"
    return {
        "object_type": "school",
        "analysis_operation": "school_evaluation",
        "teacher_id": str(school["id"]),          # composer 复用字段名
        "teacher_dept": f"{school.get('province')}/{school.get('city')}",
        "school_id": school["id"],
        "school_name": school.get("school_name"),
        "school_years": [school.get("year")],
        "template": (template.get("template") or {}).get("name"),
        "total_score": total_score,
        "overall_confidence": overall_conf,
        "data_coverage": round(coverage, 2),
        "redline_flagged": False,
        "dimensions": dims_out,
        "answered": n_answered,
        "total_metrics": n_metrics,
        "target_dimension": target_dimension,
        "target_dimensions": target_dimensions or ([target_dimension] if target_dimension else []),
        "target_dimension_scores": target_dim_scores,
        "target_dimension_score": total_score if target_dimension else None,
        "diff": diff,
        "requested_difference": diff,
        "direct_answer": direct_answer,
        "conclusion": direct_answer,
        "execution_scope": {
            "query_template": "school_evaluation_v1",
            "school_id": int(school["id"]),
            "school_name": school.get("school_name"),
            "period": str(school.get("year") or ""),
            "dimension": target_dimension,
            **({"dimensions": list(target_dimensions)} if target_dimensions and len(target_dimensions) == 2 else {}),
            "indicator_ids": [
                int(metric["id"])
                for dimension in template_dimensions
                for metric in (dimension.get("metrics") or [])
                if not selected_ids or int(metric["id"]) in selected_ids
            ],
            "metric_key": "school_digitization_v1",
            "statistic": "template_weighted_score",
            "aggregation": "template_weighted_dimension_mean",
        },
    }


def compare_schools(
    school_a_kw_or_id,
    school_b_kw_or_id,
    year=None,
    connection=None,
    require_complete_template=False,
):
    """双校数字化多维对比评估核心引擎。

    输入两所学校的关键词或 ID，可指定年份，返回维度级、指标级与总分的全面对比结果。
    """
    if connection is None:
        conn = connect()
        try:
            return compare_schools(
                school_a_kw_or_id,
                school_b_kw_or_id,
                year=year,
                connection=conn,
                require_complete_template=require_complete_template,
            )
        finally:
            conn.close()

    def _resolve_school(kw_or_id, target_year=None):
        if isinstance(kw_or_id, int) or (isinstance(kw_or_id, str) and kw_or_id.isdigit()):
            sch = get_school(int(kw_or_id), connection=connection)
            return [sch] if sch else []
        if isinstance(kw_or_id, str) and kw_or_id.startswith("id:"):
            sch = get_school(int(kw_or_id.split(":", 1)[1]), connection=connection)
            return [sch] if sch else []
        clean_kw = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", str(kw_or_id)).strip()
        return find_schools(clean_kw, year=target_year, limit=10, connection=connection)

    candidates_a = _resolve_school(school_a_kw_or_id, target_year=year)
    if not candidates_a:
        return {"error": f"未检索到对比学校 A「{school_a_kw_or_id}」" + (f"（年份 {year}）" if year else "")}

    candidates_b = _resolve_school(school_b_kw_or_id, target_year=year)
    if not candidates_b:
        return {"error": f"未检索到对比学校 B「{school_b_kw_or_id}」" + (f"（年份 {year}）" if year else "")}

    # 年份对齐：若未指定年份，优先选取两校共同存在的最新年份
    row_a = candidates_a[0]
    row_b = candidates_b[0]
    if year and str(row_a.get("year") or "") != str(year):
        return {"error": f"学校 A【{row_a.get('school_name')}】没有 {year} 年记录"}
    if year and str(row_b.get("year") or "") != str(year):
        return {"error": f"学校 B【{row_b.get('school_name')}】没有 {year} 年记录"}
    if int(row_a["id"]) == int(row_b["id"]):
        return {"error": "双校比较必须提供两所不同学校"}
    if not year:
        years_a = {str(r.get("year")) for r in candidates_a if r.get("year")}
        years_b = {str(r.get("year")) for r in candidates_b if r.get("year")}
        common_years = sorted(list(years_a & years_b), reverse=True)
        if common_years:
            align_yr = common_years[0]
            for r in candidates_a:
                if str(r.get("year")) == align_yr:
                    row_a = r
                    break
            for r in candidates_b:
                if str(r.get("year")) == align_yr:
                    row_b = r
                    break

    res_a = evaluate_school(row_a["id"], connection=connection)
    if "error" in res_a:
        return {"error": f"评估学校 A【{row_a.get('school_name')}】失败: {res_a['error']}"}

    res_b = evaluate_school(row_b["id"], connection=connection)
    if "error" in res_b:
        return {"error": f"评估学校 B【{row_b.get('school_name')}】失败: {res_b['error']}"}

    name_a = res_a.get("school_name") or "学校A"
    name_b = res_b.get("school_name") or "学校B"
    from packages.core.item_collection import collected_ids, enabled as r11_enabled
    r11_active = r11_enabled()
    if require_complete_template:
        for label, name, side in (("A", name_a, res_a), ("B", name_b, res_b)):
            answered = int(side.get("answered") or 0)
            total_metrics = int(side.get("total_metrics") or 0)
            expected_answered = len(collected_ids(side['school_years'][0], side['execution_scope']['indicator_ids'])) if r11_active else total_metrics
            if total_metrics <= 0 or expected_answered <= 0 or answered != expected_answered:
                return {
                    "error": (
                        f"学校 {label}【{name}】尚未录入有效问卷作答"
                        f"（固定模板完整度 {answered}/{total_metrics}）"
                    )
                }
    if res_a.get("total_score") is None:
        return {"error": f"学校 A【{name_a}】尚未录入有效问卷作答"}
    if res_b.get("total_score") is None:
        return {"error": f"学校 B【{name_b}】尚未录入有效问卷作答"}
    tot_a = float(res_a.get("total_score") or 0.0)
    tot_b = float(res_b.get("total_score") or 0.0)
    total_diff = round(tot_a - tot_b, 2)

    # 六大维度比对
    missing_score = None if r11_active else 0.0
    dims_a = {d["dimension"]: float(d['score']) if d.get('score') is not None else missing_score for d in res_a.get("dimensions", [])}
    dims_b = {d["dimension"]: float(d['score']) if d.get('score') is not None else missing_score for d in res_b.get("dimensions", [])}
    all_dims = [d["dimension"] for d in res_a.get("dimensions", [])]
    if not all_dims:
        all_dims = sorted(list(set(dims_a.keys()) | set(dims_b.keys())))

    dim_comp = []
    a_wins = 0
    b_wins = 0
    ties = 0

    for d_name in all_dims:
        sc_a = dims_a.get(d_name)
        sc_b = dims_b.get(d_name)
        diff = round(sc_a - sc_b, 2) if sc_a is not None and sc_b is not None else None
        if diff is None:
            lead_tag = 'NO_DATA'
            leader = '没有可比较的有效维度分'
        elif diff > 0.01:
            lead_tag = "A"
            leader = name_a
            a_wins += 1
        elif diff < -0.01:
            lead_tag = "B"
            leader = name_b
            b_wins += 1
        else:
            lead_tag = "TIE"
            leader = "持平"
            ties += 1

        dim_comp.append({
            "dimension": d_name,
            "score_a": sc_a,
            "score_b": sc_b,
            "diff": diff,
            "lead_tag": lead_tag,
            "leader": leader,
        })

    year_a = str(row_a.get("year") or "")
    year_b = str(row_b.get("year") or "")
    year_desc = f"{year_a} 年" if year_a == year_b else f"{name_a}({year_a}年) vs {name_b}({year_b}年)"

    # 胜出判定与结论构建
    if name_a == name_b and year_a != year_b:
        direction = "往下走（下降）" if tot_b < tot_a else ("往上走（上升）" if tot_b > tot_a else "持平")
        diff_abs = round(abs(tot_b - tot_a), 2)
        winner = "A" if tot_a > tot_b else ("B" if tot_b > tot_a else "TIE")
        winner_name = f"{name_a}（{year_a if tot_a > tot_b else year_b}年）" if winner != "TIE" else "两期持平"
        direct_answer = (
            f"【{name_a}】从 {year_a} 年到 {year_b} 年，数字化办学整体分数{direction}，"
            f"由 {tot_a:.2f} 分变为 {tot_b:.2f} 分，相差 {diff_abs:.2f} 分。"
            f"在 6 个核心监测维度中，{year_a} 年在 {a_wins} 个维度占优，"
            f"{year_b} 年在 {b_wins} 个维度占优"
            f"{f'，{ties} 个维度持平' if ties else ''}。"
        )
    else:
        if total_diff > 0:
            winner = "A"
            winner_name = name_a
            lead_desc = f"高于【{name_b}】（{tot_b} 分），综合分差为 +{abs(total_diff):0.2f} 分"
        elif total_diff < 0:
            winner = "B"
            winner_name = name_b
            lead_desc = f"高于【{name_a}】（{tot_a} 分），综合分差为 +{abs(total_diff):0.2f} 分"
        else:
            winner = "TIE"
            winner_name = "两校持平"
            lead_desc = f"与【{name_b}】综合得分持平（均为 {tot_a} 分）"

        direct_answer = (
            f"【{winner_name}】（{max(tot_a, tot_b):0.2f} 分）数字化水平{lead_desc}。"
            f"在 6 个核心监测维度中，【{name_a}】在 {a_wins} 个维度占优，"
            f"【{name_b}】在 {b_wins} 个维度占优"
            f"{f'，{ties} 个维度持平' if ties else ''}。"
        )

    return {
        "object_type": "school_compare",
        "school_a": res_a,
        "school_b": res_b,
        "school_name_a": name_a,
        "school_name_b": name_b,
        "school_name": f"{name_a} VS {name_b}",
        "year_a": year_a,
        "year_b": year_b,
        "year_desc": year_desc,
        "school_years": [year_a] if year_a == year_b else [year_a, year_b],
        "total_score_a": tot_a,
        "total_score_b": tot_b,
        "total_diff": total_diff,
        "score_diff": total_diff,
        "diff": total_diff,
        "winner": winner,
        "winner_name": winner_name,
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
        "direct_answer": direct_answer,
        "dimensions_comparison": dim_comp,
        "radar_data_a": dims_a,
        "radar_data_b": dims_b,
        "radar_data": dims_a,
        "radar_comp_data": dims_b,
        "radar_names": [name_a, name_b],
        "data_coverage": 1.0,
        "overall_confidence": 1.0,
        "execution_scope": {
            "query_template": "school_compare_v1",
            "school_id_a": int(row_a["id"]),
            "school_id_b": int(row_b["id"]),
            "school_name_a": name_a,
            "school_name_b": name_b,
            "period": year_a if year_a == year_b else None,
            "metric_key": "school_digitization_v1",
            "statistic": "template_weighted_score",
            "aggregation": "template_weighted_dimension_mean",
        },
        "conclusion": (
            f"数字化水平横向比对结论：\n"
            f"1. 综合水平：【{name_a}】综合得分 {tot_a} 分，【{name_b}】综合得分 {tot_b} 分，整体分差为 {total_diff:+0.2f} 分；\n"
            f"2. 维度格局：{name_a} 领先 {a_wins} 个维度，{name_b} 领先 {b_wins} 个维度"
            f"{f'，两校在 {ties} 个维度不分伯仲' if ties else ''}；\n"
            f"3. 优势建议：建议落后维度借鉴对标学校的数字化建设经验与典型举措。"
        ),
    }


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    hits = find_schools("长沙县成绩小学", year="2020", limit=3)
    print("hits:", hits)
    if hits:
        from packages.agent.composer import render_markdown
        res = evaluate_school(hits[0]["id"])
        print(render_markdown(res))
