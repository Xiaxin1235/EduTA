"""Make SCHEMA_DOC_v4_SUBDIM.md for the v4 baseline runs (Claude, 2026-10-28, author asked to run comparisons on v4).

Why: v4 contains sub-dimension questions. The system under test (fix1 candidate 36ca45ee) knows the 13 sub-dimensions, their
approved aliases and the application-defined sub-dimension score; the baselines must get the same information. The new doc keeps
every byte of doc_v3_geo/SCHEMA_DOC_v3_GEO.md and inserts one section before "## 83 题完整语义目录". Its content is generated
from the fix1 candidate's template (level2 item sets), semantics v2 'topics' (approved aliases) and the R11 uncollected-items list.
No question bank, reference or answer content is read. Writes doc_v4_subdim/SCHEMA_DOC_v4_SUBDIM.md; stops if it exists.
"""
import json
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "doc_v3_geo/SCHEMA_DOC_v3_GEO.md"
CAND = HERE.parents[1] / "project1_subdim_fix1_candidate_20261028/candidate"
OUT = HERE / "SCHEMA_DOC_v4_SUBDIM.md"
if OUT.exists():
    raise SystemExit(f"exists: {OUT}")
base = BASE.read_text("utf-8")
anchor = "## 83 题完整语义目录"
assert base.count(anchor) == 1
tmpl = yaml.safe_load((CAND / "evaluation_templates/school_digitization_v1.yaml").read_text("utf-8"))
topics = yaml.safe_load((CAND / "evaluation_templates/school_digitization_semantics_v2.yaml").read_text("utf-8"))["topics"]
items = {}
for d in tmpl["dimensions"]:
    for m in d["metrics"]:
        items.setdefault((d["name"], m["level2"]), []).append(int(m["id"]))
unc = json.loads((CAND / "research_rules/UNCOLLECTED_ITEMS_v1.json").read_text("utf-8"))["items_by_year"]
rows, none_years = [], []
for t in topics:
    ids = sorted(items[(t["dimension"], t["name"])])
    rows.append(f"|{t['dimension']}|{t['name']}|{','.join(map(str, ids))}|{'、'.join(t.get('aliases') or [])}|"
                + (f"按一级维度“{t['collapse_to_dimension']}”回答" if t.get("collapse_to_dimension") else "") + "|")
    for y in sorted(unc):
        if set(ids) <= set(map(int, unc[y])):
            none_years.append(f"{y} 年：{t['name']}")
section = f"""## 二级维度（应用自定义粒度）

问卷有三级：6 个一级维度、13 个二级维度（下表；目录字段 topic 即二级维度）、83 个题项。评估方案只定义题项、一级维度和总体得分，二级维度得分是应用自定义的分数，口径如下：

- 学校的二级维度得分：该校在该二级维度所含题项上有效 normalized_value×100 的平均（排除 R11 当年未采集题项；0 有效，NULL 排除）。
- 地区的二级维度得分：各有效学校得分的学校等权平均；有效学校指在这些题项上至少有一个有效作答的学校，回答时给出有效学校数。
- 回答二级维度得分时须注明：这是该二级维度所含题项得分的均值，评估方案未单独定义此分数。
- 映射：只问一个二级维度 → 该二级维度；部分列出同一维度的几个二级维度 → 分别给分，不合成一个分数；全部列出 → 一级维度；具体数量（台数、带宽、人数、比例）→ 题项原值；“教师数字素养”→ 一级维度数字素养。单个泛词（如“设备”“安全”）不构成二级维度。
- 问金额（花多少钱）仍属无数据：财力保障是得分，不是金额。
- 某二级维度的题项在某年全部未采集时，应说明当年未采集，不能回答 0。全部未采集的组合：{'；'.join(none_years)}。

|一级维度|二级维度|题号|别名|说明|
|---|---|---|---|---|
""" + "\n".join(rows) + "\n\n"
OUT.write_text(base.replace(anchor, section + anchor), "utf-8")
print("written", OUT, len(rows), "topics;", none_years)
