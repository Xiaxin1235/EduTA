"""Common-item definition for comparisons across years (2026-11-09; Claude). Read-only, no model calls.
COMMON uses only items collected in all four years (no item of any year's R11 list), so the item composition is the same in
every year. Compared with R11 (year-specific exclusions): national overall mean per year, and the direction of change of the
overall mean for provinces and cities (>= 30 schools) between consecutive years. Writes R11_COMMON.json / R11_COMMON_ZH.md."""
import json, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import research, YEARS, UNCOLLECTED  # noqa: E402  (needs the confidential data, see common.py)
OUT = Path.cwd()
UNC = {y: sorted(map(int, v)) for y, v in UNCOLLECTED.items()}
EXCL = {"新疆生产建设兵团", "香港特别行政区", "澳门特别行政区", "台湾省"}
UNION = set().union(*(set(map(int, UNC[y])) for y in YEARS))
COMMON_N = 83 - len(UNION)
c = research()
mean = lambda v: sum(v) / len(v)
reg = {}
for y in YEARS:
    per = {}
    for sid, q, v, p, ci in c.execute("""SELECT a.school_id, a.question_id, a.normalized_value, s.province, s.query_city FROM facts.answers a
                        JOIN research_schools s ON s.id = a.school_id WHERE s.year = ? AND a.normalized_value IS NOT NULL""", (y,)):
        d = per.setdefault(sid, [p, ci, {}]); d[2][int(q)] = min(max(float(v), 0.0), 1.0) * 100
    for name, drop in (("R11", set(map(int, UNC[y]))), ("COMMON", UNION)):
        nat, prov, city = [], defaultdict(list), defaultdict(list)
        for sid, (p, ci, it) in per.items():
            vals = [v for q, v in it.items() if q not in drop]
            if not vals or not p or p in EXCL:
                continue
            m = mean(vals); nat.append(m); prov[p].append(m)
            if ci:
                city[(p, ci)].append(m)
        reg[(name, y)] = {"nation": mean(nat), "prov": {k: mean(v) for k, v in prov.items()},
                          "city": {k: mean(v) for k, v in city.items() if len(v) >= 30}}
    print(y, {n: round(reg[(n, y)]["nation"], 2) for n in ("R11", "COMMON")}, flush=True)
res = {"common_items": COMMON_N, "excluded_union": sorted(UNION), "nation": {y: {n: round(reg[(n, y)]["nation"], 2) for n in ("R11", "COMMON")} for y in YEARS}, "transitions": {}}
for a, b in zip(YEARS, YEARS[1:]):
    t = {}
    for lvl in ("prov", "city"):
        ks = set(reg[("R11", a)][lvl]) & set(reg[("R11", b)][lvl]) & set(reg[("COMMON", a)][lvl]) & set(reg[("COMMON", b)][lvl])
        sign = lambda n, k: reg[(n, b)][lvl][k] > reg[(n, a)][lvl][k]
        flips = [k for k in ks if sign("R11", k) != sign("COMMON", k)]
        up = {n: sum(sign(n, k) for k in ks) for n in ("R11", "COMMON")}
        t[lvl] = {"compared": len(ks), "direction_differs": len(flips), "rising_R11": up["R11"], "rising_COMMON": up["COMMON"],
                  "examples": [k if isinstance(k, str) else "/".join(k) for k in sorted(flips)[:8]]}
    t["nation_direction"] = {n: "up" if reg[(n, b)]["nation"] > reg[(n, a)]["nation"] else "down" for n in ("R11", "COMMON")}
    res["transitions"][f"{a}->{b}"] = t
(OUT / "R11_COMMON.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
L = [f"# 共同题项口径（四年都采集的 {COMMON_N} 题）与 R11 的跨年比较（只读，2026-11-09）", "",
     "| 年 | R11 全国均值 | 共同题项全国均值 |", "|---|---|---|"] + [f"| {y} | {v['R11']} | {v['COMMON']} |" for y, v in res["nation"].items()]
L += ["", "| 变化 | 全国方向 R11 / 共同 | 省：方向不同 / 比较数（上升省 R11→共同） | 市：方向不同 / 比较数 |", "|---|---|---|---|"]
for k, t in res["transitions"].items():
    L.append(f"| {k} | {t['nation_direction']['R11']} / {t['nation_direction']['COMMON']} | {t['prov']['direction_differs']} / {t['prov']['compared']}（{t['prov']['rising_R11']}→{t['prov']['rising_COMMON']}） | {t['city']['direction_differs']} / {t['city']['compared']} |")
(OUT / "R11_COMMON_ZH.md").write_text("\n".join(L), "utf-8"); print("\n".join(L))
