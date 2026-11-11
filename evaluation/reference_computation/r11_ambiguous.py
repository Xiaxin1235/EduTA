"""Residual R11 uncertainty (Claude, 2026-11-10; read-only). Item-years whose all-zero status is NOT implausible under
'asked' (log10 p(all zero) > -10 in R11_PLAUSIBILITY.json, i.e. items that are almost always zero even when collected) are
counted as genuine zeros; all other R11 exclusions are kept. National school-equal overall mean per year, and direction of
province changes between consecutive years, compared with R11. Writes R11_AMBIGUOUS.json."""
import json, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import research, YEARS, UNCOLLECTED  # noqa: E402  (needs the confidential data, see common.py)
OUT = Path.cwd()
UNC = {y: sorted(map(int, v)) for y, v in UNCOLLECTED.items()}
PL = json.loads((OUT / "R11_PLAUSIBILITY.json").read_text("utf-8"))["rows"]
AMB = {(r["year"], r["item"]) for r in PL if r["log10_p_all_zero"] is not None and r["log10_p_all_zero"] > -10}
EXCL = {"新疆生产建设兵团", "香港特别行政区", "澳门特别行政区", "台湾省"}
c = research(); mean = lambda v: sum(v) / len(v)
reg = {}
for y in YEARS:
    per = {}
    for sid, q, v, p in c.execute("""SELECT a.school_id, a.question_id, a.normalized_value, s.province FROM facts.answers a
                                    JOIN research_schools s ON s.id = a.school_id WHERE s.year = ? AND a.normalized_value IS NOT NULL""", (y,)):
        d = per.setdefault(sid, [p, {}]); d[1][int(q)] = min(max(float(v), 0.0), 1.0) * 100
    r11 = set(map(int, UNC[y])); amb = r11 - {q for (yy, q) in AMB if yy == y}
    for name, drop in (("R11", r11), ("AMBIG_AS_ZERO", amb)):
        nat, prov = [], defaultdict(list)
        for sid, (p, it) in per.items():
            vals = [v for q, v in it.items() if q not in drop]
            if not vals or not p or p in EXCL: continue
            m = mean(vals); nat.append(m); prov[p].append(m)
        reg[(name, y)] = {"nation": mean(nat), "prov": {k: mean(v) for k, v in prov.items()}}
res = {"ambiguous_item_years": sorted(f"{y}:Q{q}" for y, q in AMB),
       "nation": {y: {n: round(reg[(n, y)]["nation"], 2) for n in ("R11", "AMBIG_AS_ZERO")} for y in YEARS}, "province_direction_differs": {}}
for a, b in zip(YEARS, YEARS[1:]):
    ks = set(reg[("R11", a)]["prov"]) & set(reg[("R11", b)]["prov"])
    s = lambda n, k: reg[(n, b)]["prov"][k] > reg[(n, a)]["prov"][k]
    res["province_direction_differs"][f"{a}->{b}"] = f"{sum(s('R11', k) != s('AMBIG_AS_ZERO', k) for k in ks)}/{len(ks)}"
(OUT / "R11_AMBIGUOUS.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8"); print(json.dumps(res, ensure_ascii=False, indent=1))
