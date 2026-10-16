import argparse
import csv
import json
import os
import sys
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from packages.core.frozen_paths import data_dir, output_dir, template_dir
from packages.indicator_engine.calibration import attach_calibration, calibrate

DATA_DIR = data_dir()
TEMPLATE_DIR = template_dir()
OUTPUT_DIR = output_dir()

def load_csv(name):
    path = os.path.join(DATA_DIR, name + ".csv")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

@lru_cache(maxsize=32)
def load_yaml(name):
    try:
        import yaml
        path = os.path.join(TEMPLATE_DIR, name + ".yaml")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        print("[warn] PyYAML 未安装，评估模板不可用", file=sys.stderr)
        return None

def get_teacher(teachers, teacher_id):
    for t in teachers:
        if t["teacher_id"] == teacher_id:
            return t
    return None

def filter_by_years(rows, school_years):
    pass
    return [r for r in rows if r.get("school_year") in set(school_years)]

def teacher_periods(periods, teacher_id, school_years):
    return [r for r in filter_by_years(periods, school_years)
            if r["teacher_id"] == teacher_id]

def safe_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None

def metric_lesson_plan_coverage(teacher, years, tables):
    pass
    plans = [r for r in tables["lesson_plan"] if r["teacher_id"] == teacher["teacher_id"]]
    plans = filter_by_years(plans, years)
    periods = teacher_periods(tables["course_period"], teacher["teacher_id"], years)
    expected = sum(safe_float(p.get("class_hours"), 0) or 0 for p in periods)

    submitted = sum(safe_float(p.get("submitted_hours"), 0) or 0 for p in plans)
    if expected <= 0:
        return {"value": None, "penalty": 0.4, "gap": "无授课记录，无法计算教案覆盖度",
                "evidence": []}
    cov = min(1.0, submitted / expected)

    period_str = ", ".join(years) if isinstance(years, (list, tuple)) else str(years)
    return {"value": cov * 100, "penalty": 0.0, "gap": None,
            "evidence": [{"metric": "lesson_plan_coverage", "label": "教案覆盖度", "raw": round(cov, 3),
                          "source": "lesson_plan+course_period",
                          "period": period_str, "formula": f"已交教案 {submitted:.0f} 课时 / 应授 {expected:.0f} 课时 (完成率 {cov*100:.1f}%)"}]}

def metric_observation_score(teacher, years, tables):
    obs = [r for r in tables["class_observation"] if r["teacher_id"] == teacher["teacher_id"]]
    obs = filter_by_years(obs, years)
    scores = [safe_float(r.get("score")) for r in obs]
    n = len(scores)
    period_str = ", ".join(years) if isinstance(years, (list, tuple)) else str(years)
    if n == 0:
        return {"value": None, "penalty": 0.5, "gap": "该时间段无听课记录",
                "evidence": []}
    avg = mean(scores)
    penalty = 0.3 if n < 3 else 0.0
    return {"value": avg, "penalty": penalty, "gap": None,
            "evidence": [{"metric": "observation_score", "label": "课堂听课评分", "raw": round(avg, 2),
                          "source": "class_observation", "period": period_str,
                          "formula": f"专家与督导听课平均分（共采集 {n} 次随堂评议）"}]}

def metric_pass_rate_improvement(teacher, years, tables):
    pass
    scores = [r for r in tables["exam_score"] if r["teacher_id"] == teacher["teacher_id"]]
    scores = filter_by_years(scores, years)
    all_scores = filter_by_years(tables["exam_score"], years)
    period_str = ", ".join(years) if isinstance(years, (list, tuple)) else str(years)
    if not scores:
        return {"value": None, "penalty": 0.5, "gap": "该时间段无成绩记录", "evidence": []}

    from collections import defaultdict
    base_pass = defaultdict(list)
    for r in all_scores:
        if r["teacher_id"] == teacher["teacher_id"]:
            continue
        key = (r["school_year"], r["semester"], r["course_id"])
        base_pass[key].append(1 if safe_float(r["score_val"], 0) >= 60 else 0)
    base_rate = {k: mean(v) for k, v in base_pass.items()}

    t_pass = defaultdict(list)
    for r in scores:
        key = (r["school_year"], r["semester"], r["course_id"])
        t_pass[key].append(1 if safe_float(r["score_val"], 0) >= 60 else 0)
    t_rate = {k: mean(v) for k, v in t_pass.items()}

    diffs = []
    for k, r in t_rate.items():
        if k in base_rate and base_rate[k] not in (None, 0):
            diffs.append(r - base_rate[k])
    if not diffs:
        return {"value": None, "penalty": 0.4, "gap": "无同口径基线可比", "evidence": []}

    delta = mean(diffs)
    scaled = min(100, max(0, 50 + delta / 0.15 * 50))
    return {"value": scaled, "penalty": 0.0, "gap": None,
            "evidence": [{"metric": "pass_rate_improvement", "label": "及格率增值", "raw": round(delta, 3),
                          "source": "exam_score", "period": period_str,
                          "formula": f"任教班级及格率相对年级基准线增值（增量 {delta:+.3f}）"}]}

def metric_survey_avg(teacher, years, tables):
    sv = [r for r in tables["student_survey"] if r["teacher_id"] == teacher["teacher_id"]]
    sv = filter_by_years(sv, years)
    scores = [safe_float(r.get("score")) for r in sv]
    n = len(scores)
    period_str = ", ".join(years) if isinstance(years, (list, tuple)) else str(years)
    if n == 0:
        return {"value": None, "penalty": 0.5, "gap": "该时间段无评教记录", "evidence": []}
    avg = mean(scores)
    penalty = 0.4 if n < 20 else 0.0

    return {"value": round(avg * 20, 2), "penalty": penalty, "gap": None,
            "evidence": [{"metric": "survey_avg", "label": "学生评教均分", "raw": round(avg, 1),
                          "source": "student_survey", "period": period_str,
                          "formula": f"有效问卷评教均分（共采集 {n} 份问卷，5分制换算百分制）"}]}

def metric_research_score(teacher, years, tables):
    recs = [r for r in tables["research_record"] if r["teacher_id"] == teacher["teacher_id"]]
    recs = filter_by_years(recs, years)
    dict_score = {"national": 100, "provincial": 80, "municipal": 60, "school": 40}
    total = 0
    for r in recs:
        total += dict_score.get(r.get("level"), 0) * safe_float(r.get("count"), 1)
    period_str = ", ".join(years) if isinstance(years, (list, tuple)) else str(years)
    if not recs:
        return {"value": 0, "penalty": 0.2, "gap": "该时间段无教研记录（按 0 计）", "evidence": []}

    value = min(100, total)
    return {"value": value, "penalty": 0.0, "gap": None,
            "evidence": [{"metric": "research_score", "label": "教科研成果积分", "raw": round(value, 1),
                          "source": "research_record", "period": period_str,
                          "formula": "国家/省/市/校各级教研成果加权累计得分"}]}

def metric_redline_check(teacher, years, tables):
    pass
    comp = [r for r in tables["complaint_record"] if r["teacher_id"] == teacher["teacher_id"]]
    comp = filter_by_years(comp, years)
    comp = [r for r in comp if r.get("verified") == "1"]
    disc = [r for r in tables["discipline_record"] if r["teacher_id"] == teacher["teacher_id"]]
    disc = filter_by_years(disc, years)
    disc = [r for r in disc if r.get("verified") == "1"]
    flagged = bool(comp) or bool(disc)
    period_str = ", ".join(years) if isinstance(years, (list, tuple)) else str(years)
    return {"value": flagged, "penalty": 0.0, "gap": None,
            "evidence": [{"metric": "redline", "label": "师德红线检查", "raw": flagged,
                          "source": "complaint_record+discipline_record",
                          "period": period_str, "formula": f"查实信访投诉 {len(comp)} 起，查实纪律处分 {len(disc)} 起"}]}

METRIC_FUNCS = {
    "lesson_plan_coverage": metric_lesson_plan_coverage,
    "observation_score": metric_observation_score,
    "pass_rate_improvement": metric_pass_rate_improvement,
    "survey_avg": metric_survey_avg,
    "research_score": metric_research_score,
    "redline_check": metric_redline_check,
}

def evaluate_teacher(teacher_id, years, template):
    teachers = load_csv("teacher")
    teacher = get_teacher(teachers, teacher_id)
    if teacher is None:
        return {"error": f"教师 {teacher_id} 不存在"}

    tables = {
        "course_period": load_csv("course_period"),
        "lesson_plan": load_csv("lesson_plan"),
        "class_observation": load_csv("class_observation"),
        "exam_score": load_csv("exam_score"),
        "student_survey": load_csv("student_survey"),
        "research_record": load_csv("research_record"),
        "complaint_record": load_csv("complaint_record"),
        "discipline_record": load_csv("discipline_record"),
    }

    dimension_results = []
    redline_flagged = False
    redline_evidence = None

    for dim in template["dimensions"]:
        if dim.get("is_redline"):

            m = dim["metrics"][0]
            r = METRIC_FUNCS[m["key"]](teacher, years, tables)
            redline_flagged = bool(r["value"])
            redline_evidence = r["evidence"]
            dimension_results.append({
                "dimension": dim["name"], "is_redline": True,
                "score": None, "confidence": 1.0,
                "flagged": redline_flagged, "evidence": redline_evidence,
                "gap": r["gap"]})
            continue

        dim_weighted = 0.0
        dim_confidence = 1.0
        dim_evidence = []
        dim_gaps = []
        covered = True
        for m in dim["metrics"]:
            r = METRIC_FUNCS[m["key"]](teacher, years, tables)
            if r["value"] is None:
                covered = False
                dim_gaps.append(r["gap"])
                dim_confidence -= r.get("penalty", 0.3)
                continue
            v = r["value"]

            dim_weighted += v * m.get("weight", 1.0)
            dim_confidence -= r.get("penalty", 0.0)
            dim_evidence.extend(r["evidence"])
            if r.get("gap"):
                dim_gaps.append(r["gap"])

        dimension_results.append({
            "dimension": dim["name"], "is_redline": False,
            "score": round(dim_weighted, 2) if covered else None,
            "confidence": max(0.0, round(dim_confidence, 2)),
            "evidence": dim_evidence, "gap": dim_gaps,
            "weight": dim.get("weight", 0)})

    total = 0.0
    weight_sum = 0.0
    covered_weight = 0.0
    conf_acc = 0.0
    for d in dimension_results:
        if d["is_redline"]:
            continue
        w = d["weight"]
        weight_sum += w
        if d["score"] is not None:
            total += d["score"] * w
            covered_weight += w
        conf_acc += d["confidence"] * w

    coverage = covered_weight / weight_sum if weight_sum else 0.0
    total_score = round(total / covered_weight, 2) if covered_weight else None
    overall_conf = round(conf_acc / weight_sum, 2) if weight_sum else 0.0

    res = {
        "teacher_id": teacher_id,
        "teacher_dept": teacher.get("dept_id"),
        "school_years": list(years),
        "template": (template.get("template") or {}).get("name"),
        "total_score": total_score,
        "overall_confidence": overall_conf,
        "data_coverage": round(coverage, 2),
        "redline_flagged": redline_flagged,
        "dimensions": dimension_results,
    }

    attach_calibration(res, group_col="dept_id")
    return res

def render_report(res):
    if "error" in res:
        print("评估失败：", res["error"])
        return
    print("=" * 64)
    print(f"教师评估报告（原型） 教师={res['teacher_id']} 部门={res['teacher_dept']}")
    print(f"时间范围：{', '.join(res['school_years'])}  模板：{res['template']}")
    print("=" * 64)
    if res["redline_flagged"]:
        print("\n[红线标记] 存在查实投诉/违纪记录 → 本期总评不参与评分，请按学校人事流程处理。\n")
    if res["data_coverage"] < 0.6:
        print("\n[提示] 数据覆盖不足（%s），以下结论仅供参考。\n" % res["data_coverage"])

    print("-- 维度得分 --")
    for d in res["dimensions"]:
        if d["is_redline"]:
            print(f"  {d['dimension']:<10} 红线标记={d['flagged']}  证据={len(d['evidence'])}条")
        elif d["score"] is None:
            print(f"  {d['dimension']:<10} 得分=无数据  置信={d['confidence']}  缺口={d['gap']}")
        else:
            gaps = ("；".join(d["gap"])) if d["gap"] else "无"
            print(f"  {d['dimension']:<10} 得分={d['score']:<6} 权重={d['weight']}  置信={d['confidence']}  缺口={gaps}")
    print(f"\n综合得分：{res['total_score']}  综合置信度：{res['overall_confidence']}  数据覆盖率：{res['data_coverage']}")
    print("-" * 64)
    print("证据引用列表（示例，正式版每条结论内联引用）：")
    seen = set()
    for d in res["dimensions"]:
        for e in d["evidence"]:
            k = (e["metric"], e.get("raw"))
            if k in seen:
                continue
            seen.add(k)
            print(f"  · [{e['metric']}] 值={e['raw']}  来源={e['source']}  周期={e['period']}  口径={e['formula']}")
    print("=" * 64)

def main():

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="指标引擎原型：宽泛提问 → 严谨评估")
    ap.add_argument("--teacher", default="T001", help="教师 ID（默认 T001）")
    ap.add_argument("--years", default="2023-2024,2024-2025", help="学年，逗号分隔")
    ap.add_argument("--json-out", action="store_true", help="同时输出 JSON 到 data/demo/output")
    args = ap.parse_args()

    years = [y.strip() for y in args.years.split(",")]
    template = load_yaml("2025-2026_v1")
    if template is None:
        sys.exit(1)

    res = evaluate_teacher(args.teacher, years, template)
    render_report(res)

    if args.json_out:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, f"report_{args.teacher}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 已写出：{out_path}")

if __name__ == "__main__":
    main()
