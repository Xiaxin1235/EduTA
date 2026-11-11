"""How plausible is R11? (Claude, 2026-11-10; read-only, no model calls.)
For every item-year treated as not collected (zero for every school in that year), compute: the number of schools with a
stored answer that year; the item's non-zero share among schools in the years where it was collected; and the probability
that all schools would report exactly zero if the item had been asked with that non-zero share (independence assumed,
log10 reported). Also lists which years each R11 item is uncollected in, to show whether exclusions form blocks.
Writes R11_PLAUSIBILITY.json / R11_PLAUSIBILITY_ZH.md."""
import json, math, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import research, YEARS, UNCOLLECTED  # noqa: E402  (needs the confidential data, see common.py)
OUT = Path.cwd()
UNC = {y: sorted(map(int, v)) for y, v in UNCOLLECTED.items()}
c = research()
n = defaultdict(int); nz = defaultdict(int)
for y, q, cnt, nonzero in c.execute("""SELECT s.year, a.question_id, COUNT(*), SUM(CASE WHEN a.normalized_value > 0 THEN 1 ELSE 0 END)
                                       FROM facts.answers a JOIN research_schools s ON s.id = a.school_id
                                       WHERE a.normalized_value IS NOT NULL GROUP BY s.year, a.question_id"""):
    n[(str(y), int(q))] = cnt; nz[(str(y), int(q))] = nonzero or 0
rows = []
for y in YEARS:
    for q in sorted(map(int, UNC[y])):
        other = [yy for yy in YEARS if q not in set(map(int, UNC[yy]))]
        N = sum(n[(yy, q)] for yy in other); NZ = sum(nz[(yy, q)] for yy in other)
        share = NZ / N if N else None
        m = n[(y, q)]
        log10p = (m * math.log10(1 - share)) if share and share < 1 else (float("-inf") if share == 1 else None)
        rows.append({"year": y, "item": q, "schools_that_year": m, "nonzero_that_year": nz[(y, q)], "collected_years": other,
                     "nonzero_share_when_collected": None if share is None else round(share, 4), "log10_p_all_zero": None if log10p is None else round(log10p, 1)})
blocks = defaultdict(list)
for q in sorted({r["item"] for r in rows}):
    blocks[tuple(y for y in YEARS if q in set(map(int, UNC[y])))].append(q)
shares = [r["nonzero_share_when_collected"] for r in rows if r["nonzero_share_when_collected"] is not None]
res = {"item_years": len(rows), "never_collected_items": sorted({r["item"] for r in rows if not r["collected_years"]}),
       "min_nonzero_share": min(shares) if shares else None,
       "max_log10_p": max((r["log10_p_all_zero"] for r in rows if r["log10_p_all_zero"] is not None), default=None),
       "uncollected_pattern": {"/".join(k) or "-": v for k, v in blocks.items()}, "rows": rows}
(OUT / "R11_PLAUSIBILITY.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
L = ["# R11 合理性检验（只读，2026-11-10）", "",
     f"R11 题项×年份共 {len(rows)} 个；从未在任何年份采集到非零的题项：{res['never_collected_items']}。",
     f"在采集年份里的最低非零比例：{res['min_nonzero_share']}；“若当年真的采集，全部学校恰好为 0”的概率上限：10^{res['max_log10_p']}（按独立假设）。", "",
     "## 未采集年份的分布（相同年份组合的题项）", ""]
L += [f"- 未采集于 {k}：{len(v)} 题（{', '.join('Q'+str(x) for x in v)}）" for k, v in res["uncollected_pattern"].items()]
L += ["", "## 非零比例最低的 10 个题项×年份", "", "| 年 | 题 | 当年学校数 | 采集年份非零比例 | log10 概率 |", "|---|---|---|---|---|"]
for r in sorted([r for r in rows if r["nonzero_share_when_collected"] is not None], key=lambda r: r["nonzero_share_when_collected"])[:10]:
    L.append(f"| {r['year']} | Q{r['item']} | {r['schools_that_year']} | {r['nonzero_share_when_collected']} | {r['log10_p_all_zero']} |")
(OUT / "R11_PLAUSIBILITY_ZH.md").write_text("\n".join(L), "utf-8"); print("\n".join(L))
