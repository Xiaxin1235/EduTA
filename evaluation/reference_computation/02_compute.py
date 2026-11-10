"""Step 2 (copied for v6 on 2026-10-30, only bank id and title changed; copied for v5 on 2026-10-30 without logic changes; v4, 2026-10-28; adapted from the v3 package, sub-dimension support added): compute the reference facts of the held-out 100 from evidence/school_scores.sqlite (+ raw item values read from the snapshot).

Independent of the product code. Definitions follow the reference-100 package (see 01_school_scores.py) plus the bank rules:
S1 sub-dimension (topic) score = mean of a school's valid item points in the topic (catalogue level2_name), R11 items excluded, regional
school-equal mean over schools with at least one valid item, answer must state it is application-defined; S3 a topic whose items are all
uncollected in a year yields a 'not collected' fact. R1 province ranking over the 31 provincial units (no 新疆生产建设兵团, no HK/Macao/Taiwan); R2 school-equal means, national mean over the
same 31 units; R3 fewer than 30 valid schools = small-sample flag; R4 新疆 = 新疆维吾尔自治区 only; R9 stage 小学 = 'A.' or '%小学%' or
grade span starting '1-6'/'1-5' (same rule as the reference-100 package); R10 城区 = school_area_type '城区' only.
Writes REFERENCE_HELDOUT100.json and REFERENCE_HELDOUT100_ZH.md (refuses to overwrite).
"""
import re, math, statistics, collections
from common import *

OUT = PACKAGE / "REFERENCE_HELDOUT100.json"
if OUT.exists():
    raise SystemExit("refuse to overwrite " + str(OUT))
SPECS = load(PACKAGE / "inputs/TASK_SPECS.json")
REG = load(PACKAGE / "inputs/geography_registry_v5.json")
EXCLUDED_UNITS = {"新疆生产建设兵团", "澳门特别行政区", "香港特别行政区", "台湾省"}
UNIT = {4: "%", 21: "种", 72: "种", 73: "种", 78: "种", 60: "Mbps", 62: "间", 61: "间", 38: "人", 39: "人", 41: "次", 63: "台", 64: "台", 67: "台", 80: "人", 76: "种", 10: "种", 40: "%", 49: "个学科", 51: "种",
        58: "人次", 31: "%", 13: "种", 54: "种", 29: "人次", 28: "种", 65: "台", 66: "台", 56: "%", 57: "%", 27: "种",
        23: "种", 70: "台", 59: "%", 50: "班", 68: "台", 52: "种", 30: "种", 43: "%", 3: "个", 44: "人", 77: "种", 71: "种"}
SMALL = 30

db = sqlite3.connect(SCORES.as_uri() + "?mode=ro", uri=True)
SCHOOL = {r[0]: dict(zip(["id", "year", "school_name", "province", "city", "district", "school_type", "school_area_type"], r))
          for r in db.execute("SELECT * FROM school")}
DSTAT = collections.defaultdict(dict)
for sid, dim, n, s, sc in db.execute("SELECT * FROM dim_stats"):
    DSTAT[sid][dim] = (n, s, sc)
# City -> province from the data itself (the frozen v5 registry covers only six benchmark cities). A city name that appears under
# more than one province is resolved to the province holding most of its school-years, and listed in CITY_AMBIGUOUS.
_cp = collections.defaultdict(collections.Counter)
for _r in SCHOOL.values():
    if _r["city"] and _r["province"]:
        _cp[_r["city"]][_r["province"]] += 1
CITY_PARENT = {c: cnt.most_common(1)[0][0] for c, cnt in _cp.items()}
TSTAT = collections.defaultdict(dict)
for _sid, _t, _n, _s in sqlite3.connect(TOPIC_SCORES.as_uri() + "?mode=ro", uri=True).execute("SELECT * FROM topic_stats"):
    TSTAT[_sid][_t] = (_n, _s)
TOPICS = collections.defaultdict(list)
for _i, _r in CAT.items():
    TOPICS[_r["level2_name"]].append(_i)
TOPIC_NOTE = "须注明：二级维度得分为所含题项得分均值，评估方案未单独定义此分数"


def topicpt(sid, name):
    v = TSTAT.get(sid, {}).get(name)
    return v[1] / v[0] if v and v[0] else None


def is_topic(m):
    return isinstance(m, list) and m[0] == "topic"


def uncollected(m, year):
    return is_topic(m) and set(TOPICS[m[1]]) <= UNCOLLECTED.get(year, set())
CITY_AMBIGUOUS = {c: dict(cnt) for c, cnt in _cp.items() if len(cnt) > 1}
NATION = sorted({r["province"] for r in SCHOOL.values() if r["province"] and r["province"] not in EXCLUDED_UNITS})
assert len(NATION) == 31, NATION


def overall(sid):
    d = DSTAT.get(sid, {}); n = sum(v[0] for v in d.values())
    return sum(v[1] for v in d.values()) / n if n else None


def dimpt(sid, dim):
    v = DSTAT.get(sid, {}).get(dim)
    return v[1] / v[0] if v and v[0] else None


def template(sid):
    """R11: a dimension with no collected item that year is left out and the weights are renormalised over the rest."""
    d = DSTAT.get(sid, {})
    have = [k for k in DIMS if d.get(k) and d[k][0]]
    if not have:
        return None, None
    dims = {k: round(d[k][2] / d[k][0], 2) for k in have}
    return round(math.fsum(dims[k] * WEIGHTS[k] for k in have) / math.fsum(WEIGHTS[k] for k in have), 2), dims


def members(scope, year):
    out = []
    for r in SCHOOL.values():
        if r["year"] != year:
            continue
        if scope.get("nation") and r["province"] not in NATION:
            continue
        if "province" in scope and r["province"] != scope["province"]:
            continue
        if "city" in scope:
            if r["city"] != scope["city"] or r["province"] != CITY_PARENT[scope["city"]]:
                continue
        a = r["school_area_type"] or ""
        if scope.get("area") == "乡村" and not ("乡村" in a or "农村" in a):
            continue
        if scope.get("area") == "城区" and a != "城区":
            continue
        t = r["school_type"] or ""
        if scope.get("stage") == "小学" and not ("小学" in t or t.startswith(("1-6", "1-5", "A."))):
            continue
        if scope.get("stage") == "初中" and not ("初中" in t or "初级中学" in t or t.startswith(("7-9", "B.")) or t == "1-9"):
            continue
        out.append(r["id"])
    return out


_raw_cache = {}


def raw_values(ids, item):
    key = (tuple(ids), item)
    if key not in _raw_cache:
        c = research(); vals = collections.defaultdict(list)
        for j in range(0, len(ids), 500):
            chunk = ids[j:j + 500]
            for sid, v in c.execute(f"SELECT school_id, value FROM facts.answers WHERE question_id=? AND school_id IN ({','.join('?' * len(chunk))})", [item] + chunk):
                if v is not None and isinstance(v, (int, float)) and math.isfinite(v):
                    vals[sid].append(float(v))
        _raw_cache[key] = {sid: statistics.fmean(v) for sid, v in vals.items()}
    return _raw_cache[key]


def values(scope, year, metric):
    ids = members(scope, year)
    if is_topic(metric):
        return {s: v for s in ids if (v := topicpt(s, metric[1])) is not None}, len(ids)
    if isinstance(metric, list):
        return raw_values(ids, metric[1]), len(ids)
    f = overall if metric == "overall" else (lambda s: dimpt(s, metric))
    return {s: v for s in ids if (v := f(s)) is not None}, len(ids)


def stats(vals):
    v = list(vals.values())
    if not v:
        return {"count": 0}
    return {"mean": statistics.fmean(v), "median": statistics.median(v), "min": min(v), "max": max(v), "count": len(v)}


def label(scope):
    if scope.get("nation"):
        return "全国（31 个省级行政区）"
    s = scope.get("province") or scope.get("city")
    return s + ("·" + scope["area"] if scope.get("area") else "") + ("·" + scope["stage"] if scope.get("stage") else "")


def mname(metric):
    if is_topic(metric):
        return f"二级维度「{metric[1]}」"
    return "整体" if metric == "overall" else (f"Q{metric[1]} {CAT[metric[1]]['content']}" if isinstance(metric, list) else metric)


def munit(metric):
    return UNIT.get(metric[1], "") if isinstance(metric, list) and not is_topic(metric) else "分"


facts = []


def F(desc, value, unit="", **k):
    facts.append(dict({"fact_id": f"F{len(facts) + 1:02d}", "desc": desc, "value": value, "unit": unit}, **k))


def scope_stat(scope, year, metric, which=("mean", "count")):
    if uncollected(metric, year):
        F(f"{label(scope)} {year} {mname(metric)} 所含题项当年全部未采集（应说明未采集，不能给 0）", True, statement=True)
        return {"count": 0}
    if is_topic(metric):
        F(TOPIC_NOTE, True, statement=True)
    st = stats(values(scope, year, metric)[0])
    for w in which:
        F(f"{label(scope)} {year} {mname(metric)} {'学校等权均值' if w == 'mean' else {'count': '有效学校数', 'median': '中位数', 'min': '最小值', 'max': '最大值'}[w]}",
          st.get(w), "所" if w == "count" else munit(metric))
    if st["count"] < SMALL:
        F(f"{label(scope)} {year} 样本过少提示（有效学校 < {SMALL}）", True, small_sample=True)
    return st


def province_ranking(year, metric="overall"):
    r = []
    for p in NATION:
        st = stats(values({"province": p}, year, metric)[0])
        if st["count"]:
            r.append((st["mean"], p, st["count"]))
    r.sort(key=lambda x: (-x[0], x[1]))
    return r


def dims_of(scope, year):
    return {d: stats(values(scope, year, d)[0]) for d in DIMS}


def run(op):
    k = op["op"]
    if k == "stat":
        scope_stat(op["scope"], op["year"], op["metric"], op["stats"] + (["count"] if "count" not in op["stats"] else []))
    elif k in ("open", "vs_nation"):
        sc, y = op["scope"], op["year"]
        st = scope_stat(sc, y, "overall")
        nat = stats(values(N_SCOPE, y, "overall")[0])
        F(f"全国 {y} 整体学校等权均值", nat["mean"], "分"); F(f"全国 {y} 有效学校数", nat["count"], "所")
        F(f"{label(sc)} 减 全国（分）", st["mean"] - nat["mean"], "分"); F(f"{label(sc)} 高于全国平均", st["mean"] > nat["mean"])
        if k == "open":
            if "province" in sc:
                rk = province_ranking(y)
                pos = [p for _, p, _ in rk].index(sc["province"]) + 1
                F(f"{label(sc)} {y} 在 {len(rk)} 个省级行政区中的排名（1=最高）", pos)
            ds = dims_of(sc, y)
            means = {d: v["mean"] for d, v in ds.items()}
            for d in DIMS:
                F(f"{label(sc)} {y} {d} 学校等权均值", means[d], "分")
            F(f"{label(sc)} {y} 最强维度", max(means, key=means.get)); F(f"{label(sc)} {y} 最弱维度", min(means, key=means.get))
    elif k == "compare":
        res = []
        for sc in op["scopes"]:
            st = scope_stat(sc, op["year"], op["metric"])
            res.append((st["mean"], label(sc)))
        order = [l for _, l in sorted(res, key=lambda x: -x[0])]
        F(f"{op['year']} {mname(op['metric'])} 从高到低", order)
        if len(res) == 2:
            F(f"{res[0][1]} 减 {res[1][1]}", res[0][0] - res[1][0], munit(op["metric"]))
    elif k == "dims":
        ds = dims_of(op["scope"], op["year"]); means = {d: v["mean"] for d, v in ds.items()}
        for d in DIMS:
            F(f"{label(op['scope'])} {op['year']} {d} 学校等权均值", means[d], "分")
        F(f"{label(op['scope'])} {op['year']} 有效学校数（整体）", stats(values(op["scope"], op["year"], "overall")[0])["count"], "所")
        hi, lo = max(means, key=means.get), min(means, key=means.get)
        F("得分最高的维度", hi); F("得分最低的维度", lo); F("最高减最低（分）", means[hi] - means[lo], "分")
    elif k == "city_pos":
        city, y = op["city"], op["year"]; prov = CITY_PARENT[city]
        cst = scope_stat({"city": city}, y, "overall"); pst = scope_stat({"province": prov}, y, "overall")
        F(f"{city} 减 {prov}（分）", cst["mean"] - pst["mean"], "分"); F(f"{city} 高于 {prov} 全省", cst["mean"] > pst["mean"])
        cities = collections.defaultdict(list)
        for s in members({"province": prov}, y):
            if (v := overall(s)) is not None:
                cities[SCHOOL[s]["city"]].append(v)
        rk = sorted(((statistics.fmean(v), c, len(v)) for c, v in cities.items() if c), key=lambda x: (-x[0], x[1]))
        F(f"{city} 在 {prov} {len(rk)} 个城市中的排名（1=最高）", [c for _, c, _ in rk].index(city) + 1,
          ranking=[{"city": c, "mean": m, "count": n} for m, c, n in rk])
    elif k == "rank_prov":
        m = op.get("metric", "overall")
        rk = province_ranking(op["year"], m)
        pick = rk[:op["n"]] if op["side"] == "top" else rk[-op["n"]:][::-1]
        if is_topic(m):
            F(TOPIC_NOTE, True, statement=True)
        F(f"{op['year']} {mname(m)}得分{'最高' if op['side'] == 'top' else '最低'}的 {op['n']} 个省级行政区（31 个中）",
          [{"province": p, "mean": m, "count": n} for m, p, n in pick])
        for m, p, n in pick:
            if n < SMALL:
                F(f"{p} 样本过少提示", True, small_sample=True)
    elif k == "series":
        res = []
        for y in op["years"]:
            st = scope_stat(op["scope"], y, op["metric"])
            if st.get("count"):
                res.append((y, st["mean"]))
        F(f"{label(op['scope'])} {res[-1][0]} 减 {res[0][0]}（分）", res[-1][1] - res[0][1], "分")
        F("各年为独立年度截面，学校不是同一批（R5 必须说明）", True, statement=True)
    elif k in ("school_template", "school_compare"):
        sch = [op["school"]] if k == "school_template" else op["schools"]
        got = []
        for s in sch:
            r = SCHOOL[s["school_id"]]
            assert r["school_name"] == s["school_name"] and r["year"] == op["year"], (s, r)
            tot, dims = template(s["school_id"])
            got.append((s["school_name"], tot, dims))
        if k == "school_template":
            name, tot, dims = got[0]; focus = op.get("focus")
            if focus in DIMS:
                F(f"{name} {op['year']} {focus} 维度得分", dims[focus], "分")
            elif focus == "max":
                hi = max(dims, key=dims.get); F(f"{name} 得分最高的维度", hi); F(f"{name} {hi} 得分", dims[hi], "分")
            elif focus in ("maxmin", "min"):
                hi, lo = max(dims, key=dims.get), min(dims, key=dims.get)
                if focus == "maxmin":
                    F(f"{name} 得分最高的维度", hi); F(f"{name} {hi} 得分", dims[hi], "分")
                F(f"{name} 得分最低的维度", lo); F(f"{name} {lo} 得分", dims[lo], "分")
            else:
                F(f"{name} {op['year']} 学校整体得分（学校模板分）", tot, "分")
        else:
            m = op["metric"]; vals = [(n, t if m == "overall" else d[m]) for n, t, d in got]
            for n, v in vals:
                F(f"{n} {op['year']} {'学校整体得分（学校模板分）' if m == 'overall' else m + ' 维度得分'}", v, "分")
            F(f"{vals[0][0]} 减 {vals[1][0]}", vals[0][1] - vals[1][1], "分")
            F("得分更高的学校", vals[0][0] if vals[0][1] > vals[1][1] else vals[1][0])
    elif k == "school_item":
        s = op["school"]; r = SCHOOL[s["school_id"]]; assert r["school_name"] == s["school_name"], (s, r)
        tot = 0.0
        for q in op["items"]:
            v = raw_values([s["school_id"]], q).get(s["school_id"])
            F(f"{s['school_name']} {op['year']} Q{q} {CAT[q]['content']}", v, UNIT.get(q, ""))
            tot += v if v is not None else float("nan")
        if op.get("total"):
            F("两项合计", tot, UNIT.get(op["items"][0], ""))
    elif k == "item_top3":
        ids = members(op["scope"], op["year"]); vals = raw_values(ids, op["item"])
        rk = sorted(vals.items(), key=lambda x: (-x[1], x[0]))[:3]
        F(f"{label(op['scope'])} {op['year']} Q{op['item']} 前三名（同数按学校编号小者在前）",
          [{"school_id": s, "school_name": SCHOOL[s]["school_name"], "value": v} for s, v in rk], UNIT.get(op["item"], ""))
    elif k == "extremes":
        vals = values(op["scope"], op["year"], "overall")[0]
        hi = max(vals.items(), key=lambda x: (x[1], -x[0])); lo = min(vals.items(), key=lambda x: (x[1], x[0]))
        F("每校整体得分最高值", hi[1], "分", school=SCHOOL[hi[0]]["school_name"]); F("每校整体得分最低值", lo[1], "分", school=SCHOOL[lo[0]]["school_name"])
        F("最高减最低", hi[1] - lo[1], "分"); F("有效学校数", len(vals), "所")
    elif k == "rank_school":
        vals = values(op["scope"], op["year"], op["metric"])[0]
        rk = sorted(vals.items(), key=lambda x: (-x[1], x[0]))[:op["n"]]
        F(TOPIC_NOTE, True, statement=True)
        F(f"{label(op['scope'])} {op['year']} {mname(op['metric'])} 前 {op['n']} 名学校（同分按学校编号小者在前）",
          [{"school_id": s, "school_name": SCHOOL[s]["school_name"], "score": v} for s, v in rk])
        F(f"{label(op['scope'])} {op['year']} {mname(op['metric'])} 参与排名的学校数", len(vals), "所")
    elif k == "school_topic":
        F(TOPIC_NOTE, True, statement=True)
        for s in op["schools"]:
            r = SCHOOL[s["school_id"]]; assert r["school_name"] == s["school_name"] and r["year"] == op["year"], (s, r)
            F(f"{s['school_name']} {op['year']} {mname(op['metric'])} 得分", topicpt(s["school_id"], op["metric"][1]), "分")
    elif k == "year_absent":
        F(f"{label(op['scope'])} {op['year']} 的学校记录数（应为 0）", len(members(op["scope"], op["year"])), "所")
    elif k == "statement":
        F(op["text"], True, statement=True)
    elif k == "school_absent":
        hits = [r["school_name"] for r in SCHOOL.values() if op["name"] in (r["school_name"] or "") or (r["school_name"] or "") in op["name"] and len(r["school_name"] or "") > 6]
        F(f"库中校名包含“{op['name']}”的记录数（应为 0）", len(hits), evidence=hits[:5])
    elif k == "name_check":
        for n in op["names"]:
            key = "province" if n.endswith("省") else "city"
            F(f"{n} 在 2023 年的学校数", sum(1 for r in SCHOOL.values() if r["year"] == LATEST and r[key] == n), "所")
    elif k == "district_check":
        cities = sorted({(r["province"], r["city"]) for r in SCHOOL.values() if r["district"] == op["district"] and r["year"] == LATEST})
        F(f"2023 年有“{op['district']}”的城市", [f"{p}{c}" for p, c in cities])
    else:
        raise ValueError(k)


N_SCOPE = {"nation": True}
text = {}
for line in (PACKAGE / "inputs/HELDOUT100_QUESTIONS_ZH.md").read_text("utf-8").splitlines():
    m = re.match(r"\|\s*([XY]\d\d)\s*\|(.*)", line)
    if m:
        cells = [x.strip() for x in m.group(2).split("|")]
        text[m.group(1)] = cells[1] if m.group(1).startswith("Y") else cells[0]
refs = []
for s in SPECS:
    facts = []
    for op in s["ops"]:
        run(op)
    refs.append({"question_id": s["id"], "original_text": text[s["id"]], "expected_behavior": s["expect"],
                 "default_year_2023": s["default_year"], "rule_notes": s["note"], "facts": facts,
                 "facts_required": s["expect"] == "answer"})
dump(OUT, {"bank_id": "heldout100_v6", "topic_scores_sha256": sha(TOPIC_SCORES), "definition": "R11 uncollected items excluded", "computed_at": now(), "scores_db_sha256": sha(SCORES), "specs_sha256": sha(PACKAGE / "inputs/TASK_SPECS.json"),
           "rules": __doc__, "nation_units": NATION, "city_parent_source": "derived from data (majority province per city name)", "ambiguous_city_names_used": {c: CITY_AMBIGUOUS[c] for c in sorted({op.get("city") or sc.get("city") for s in SPECS for op in s["ops"] for sc in (op.get("scopes") or [op.get("scope") or {}]) if (op.get("city") or sc.get("city")) in CITY_AMBIGUOUS})}, "questions": refs})


def show(v):
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    if isinstance(v, list):
        return "；".join(show(x) if not isinstance(x, dict) else "，".join(f"{kk}={show(vv)}" for kk, vv in x.items()) for x in v)
    return str(v)


md = ["# 未见测试集 v6 100 题参考答案（独立计算，口径 R11 + 二级维度 S1–S3）", "", f"计算时间：{now()}。口径见 `scripts/02_compute.py` 顶部说明。", ""]
for r in refs:
    md.append(f"## {r['question_id']}　{r['original_text']}")
    md.append(f"期望行为：{ {'answer': '作答', 'decline': '说明无数据或超出能力', 'clarify': '反问'}[r['expected_behavior']] }"
              + ("；未写年份，默认 2023" if r["default_year_2023"] else "") + (f"；{r['rule_notes']}" if r["rule_notes"] else ""))
    md.append("")
    for f in r["facts"]:
        md.append(f"- {f['fact_id']} {f['desc']}：{show(f['value'])}{(' ' + f['unit']) if f['unit'] and not isinstance(f['value'], (bool, list)) else ''}")
    md.append("")
(PACKAGE / "REFERENCE_HELDOUT100_ZH.md").write_text("\n".join(md), "utf-8")
print(len(refs), "references;", sum(len(r["facts"]) for r in refs), "facts")
