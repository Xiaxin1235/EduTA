"""Machine-readable task specs for the held-out v6 draft (same ops and layout as v5; new provinces, cities and schools).
Written 2026-10-30 after fix5 was delivered but before any fix5 answer was viewed. Writes inputs/TASK_SPECS.json. Claude."""
from common import *

P = lambda n: {"province": n}
C = lambda n: {"city": n}
S = lambda i, n: {"school_id": i, "school_name": n}
T = lambda n: ["topic", n]
Y = LATEST
specs = []


def add(qid, expect="answer", ops=(), default_year=False, note=""):
    specs.append({"id": qid, "expect": expect, "default_year": default_year, "ops": list(ops), "note": note})


SX, HL, JL, HE, SD, NM, LN, HI = "山西省", "黑龙江省", "吉林省", "河南省", "山东省", "内蒙古自治区", "辽宁省", "海南省"
# C1 open-ended province level
for q, sc, y, d in [("X01", SX, Y, True), ("X02", HL, "2022", False), ("X03", HE, Y, True), ("X04", JL, "2021", False),
                    ("X05", NM, Y, True), ("X06", LN, Y, False)]:
    add(q, ops=[{"op": "open", "scope": P(sc), "year": y}], default_year=d)
# C2 two provinces
for q, a, b, y, m, d in [("X07", SX, HE, Y, "overall", True), ("X08", JL, LN, "2022", "overall", False),
                         ("X09", HL, JL, Y, "overall", True), ("X10", SD, HE, "2021", "overall", False),
                         ("X11", HI, SD, Y, "教育治理", True), ("X12", NM, SX, "2022", "数字素养", False)]:
    add(q, ops=[{"op": "compare", "scopes": [P(a), P(b)], "year": y, "metric": m}], default_year=d)
# C3 dimensions
add("X13", ops=[{"op": "dims", "scope": P(HE), "year": Y, "focus": "min"}], default_year=True)
add("X14", ops=[{"op": "dims", "scope": P(SD), "year": "2022", "focus": "max"}])
add("X15", ops=[{"op": "stat", "scope": P(JL), "year": Y, "metric": m, "stats": ["mean", "count"]} for m in ["数字资源", "教育教学"]], default_year=True)
add("X16", ops=[{"op": "dims", "scope": P(LN), "year": Y, "focus": "spread"}])
# C4 city position
add("X17", ops=[{"op": "city_pos", "city": "长治市", "year": Y}], default_year=True)
add("X18", ops=[{"op": "city_pos", "city": "菏泽市", "year": Y}])
add("X19", ops=[{"op": "city_pos", "city": "聊城市", "year": Y}], default_year=True)
add("X20", ops=[{"op": "vs_nation", "scope": C("晋中市"), "year": Y}], default_year=True)
# C5 multi-object and national ranking
add("X21", ops=[{"op": "compare", "scopes": [P(SX), P(HE), P(SD)], "year": Y, "metric": "overall"}], default_year=True)
add("X22", ops=[{"op": "compare", "scopes": [P(HL), P(JL), P(LN)], "year": "2022", "metric": "overall"}])
add("X23", ops=[{"op": "compare", "scopes": [C("长治市"), C("菏泽市"), C("泰安市")], "year": Y, "metric": "overall"}])
add("X24", ops=[{"op": "rank_prov", "year": "2021", "side": "top", "n": 3}])
add("X25", ops=[{"op": "rank_prov", "year": Y, "side": "bottom", "n": 2}])
# C6 multi-year
add("X26", ops=[{"op": "series", "scope": P(HE), "years": YEARS, "metric": "overall"}], note="R5")
add("X27", ops=[{"op": "series", "scope": P(SD), "years": YEARS, "metric": "overall"}], note="R5")
add("X28", ops=[{"op": "series", "scope": C("聊城市"), "years": YEARS, "metric": "overall"}], note="R5")
add("X29", ops=[{"op": "series", "scope": P(JL), "years": ["2021", "2022", "2023"], "metric": "overall"}], note="R5")
# C7 colloquial
add("X30", ops=[{"op": "open", "scope": P(HI), "year": Y}], default_year=True)
for q, sc, m, y, d in [("X31", SX, "基础设施", "2022", False), ("X32", HE, "数字素养", Y, True), ("X33", SD, "保障机制", Y, False)]:
    add(q, ops=[{"op": "stat", "scope": P(sc), "year": y, "metric": m, "stats": ["mean", "count"]}], default_year=d)
# C12 sub-dimensions
st = lambda q, sc, y, m, stats, d=False, note="": add(q, ops=[{"op": "stat", "scope": sc, "year": y, "metric": m, "stats": stats}], default_year=d, note=note)
st("X34", P(SD), Y, T("网络安全"), ["mean", "count"], True, "S3 all items uncollected in 2023")
st("X35", P(JL), "2022", T("网络安全"), ["mean", "count"])
st("X36", P(HE), Y, T("教学评价"), ["mean", "count"], True)
st("X37", C("长治市"), "2021", T("资源规模"), ["mean", "count"])
add("X38", ops=[{"op": "stat", "scope": P(NM), "year": "2022", "metric": T(m), "stats": ["mean", "count"]} for m in ["终端", "网络"]], note="S2 partial list: separate scores")
add("X39", ops=[{"op": "compare", "scopes": [C("菏泽市"), C("泰安市")], "year": Y, "metric": T("教学方式")}])
add("X40", ops=[{"op": "series", "scope": P(SX), "years": YEARS, "metric": T("教学评价")}], note="S3 2020-2021 uncollected; R5")
add("X41", ops=[{"op": "rank_prov", "year": Y, "side": "top", "n": 3, "metric": T("网络")}])
st("X42", C("晋中市"), Y, T("资源应用"), ["mean", "count"], False, "S3 all items uncollected in 2023")
st("X43", C("聊城市"), Y, ["item", 62], ["mean", "count"], True, "item Q62 raw value, not the sub-dimension 教室")
# C8 beyond capability (R6)
add("X44", "decline", [{"op": "open", "scope": P(HE), "year": Y}], True, "R6 causal")
add("X45", "decline", [{"op": "series", "scope": P(SD), "years": YEARS, "metric": "overall"}], False, "R6 prediction")
add("X46", "decline", [{"op": "compare", "scopes": [P(JL), P(LN)], "year": Y, "metric": "overall"}], False, "R6 significance")
add("X47", "decline", [], False, "R6 causal")
add("X48", "decline", [], False, "R6 no person-level data")
# C9 not in data (R7)
add("X49", "decline", [{"op": "open", "scope": P(SX), "year": Y}], False, "R7 year 2025 not in data; 2023 offer optional")
for q, why in [("X50", "R7 no admission-rate data"), ("X51", "R7 no device usage-time data"), ("X54", "R7 no parent satisfaction data")]:
    add(q, "decline", [], False, why)
add("X52", "decline", [{"op": "statement", "text": "数据中没有经费金额；财力保障是得分不是金额，不能用得分冒充金额"}], False, "R7 no funding amounts")
add("X53", "decline", [{"op": "school_absent", "name": "太原市小店区星辰实验学校"}], False, "R7 school not in data")
# C10 clarification (R8)
add("X55", "clarify", [{"op": "district_check", "district": "城关区"}], False, "R8 district in two cities")
add("X56", "clarify", [{"op": "district_check", "district": "普陀区"}], False, "R8 district in two cities")
add("X57", "clarify", [], False, "R8 no context")
add("X58", "clarify", [], False, "R8 no region given")
# C11 traps
add("X59", ops=[{"op": "open", "scope": P("天津市"), "year": "2022"}], note="R3 small sample warning required (4 schools)")
add("X60", "decline", [{"op": "year_absent", "scope": C("濮阳市"), "year": "2021"}], False, "R7 no records for that year")

# Standard questions
sv = lambda q, sc, y, m, stats, **k: add(q, ops=[dict({"op": "stat", "scope": sc, "year": y, "metric": m, "stats": stats}, **k)])
si = lambda q, i, n, items, total=False: add(q, ops=[{"op": "school_item", "school": S(i, n), "year": "2023", "items": items, "total": total}])
sv("Y01", C("菏泽市"), "2023", "overall", ["mean", "count"])
add("Y02", ops=[{"op": "compare", "scopes": [C("长治市"), C("大同市")], "year": "2023", "metric": "overall"}])
add("Y03", ops=[{"op": "series", "scope": C("泰安市"), "years": ["2021", "2023"], "metric": "overall"}])
add("Y04", ops=[{"op": "school_template", "school": S(1740507, "长治市第九中学校"), "year": "2023"}])
sv("Y05", C("聊城市"), "2023", "overall", ["median"])
sv("Y06", C("临汾市"), "2023", "数字资源", ["mean"])
add("Y07", ops=[{"op": "compare", "scopes": [C("大同市"), C("临汾市")], "year": "2023", "metric": "教育治理"}])
sv("Y08", C("菏泽市"), "2022", "教育教学", ["mean", "count"])
add("Y09", ops=[{"op": "school_template", "school": S(1665003, "东昌府区滨河实验学校"), "year": "2023", "focus": "maxmin"}])
sv("Y10", dict(C("晋中市"), stage="小学"), "2023", "基础设施", ["mean", "count"])
sv("Y11", C("泰安市"), "2023", T("教学评价"), ["mean", "count"])
add("Y12", ops=[{"op": "compare", "scopes": [C("长治市"), C("菏泽市")], "year": "2023", "metric": T("学校治理")}])
sv("Y13", C("聊城市"), "2022", T("资源应用"), ["mean"])
add("Y14", ops=[{"op": "school_topic", "schools": [S(1674158, "泰安第二十中学")], "year": "2023", "metric": T("教学方式")}])
add("Y15", ops=[{"op": "rank_school", "scope": C("菏泽市"), "year": "2023", "metric": T("教学方式"), "n": 3}])
si("Y16", 1739183, "大同市煤矿第四中学校", [62])
si("Y17", 1664146, "单县徐寨镇宗庄小学", [65, 66])
sv("Y18", C("临汾市"), "2023", ["item", 70], ["median"])
add("Y19", ops=[{"op": "item_top3", "scope": C("聊城市"), "year": "2023", "item": 62}])
sv("Y20", C("菏泽市"), "2023", ["item", 52], ["mean"])
sv("Y21", C("泰安市"), "2023", "overall", ["mean", "count"])
add("Y22", ops=[{"op": "compare", "scopes": [C("临汾市"), C("大同市")], "year": "2023", "metric": "overall"}])
add("Y23", ops=[{"op": "extremes", "scope": C("晋中市"), "year": "2023"}])
add("Y24", ops=[{"op": "school_compare", "schools": [S(1743292, "榆次区振兴小学"), S(1738172, "和顺东关示范小学")], "year": "2023", "metric": "overall"}])
sv("Y25", dict(C("聊城市"), area="城区"), "2023", "overall", ["mean"], note="R10 城区 only")
sv("Y26", C("菏泽市"), "2023", "基础设施", ["mean"])
sv("Y27", C("长治市"), "2022", "数字素养", ["mean"])
sv("Y28", dict(C("泰安市"), stage="小学"), "2021", "数字资源", ["mean"], note="R9 stage by grade span; state it")
add("Y29", ops=[{"op": "compare", "scopes": [C("晋中市"), C("聊城市")], "year": "2023", "metric": "教育治理"}], note="full list of the dimension's sub-dimensions -> dimension; 网络安全 uncollected in 2023 (R11)")
add("Y30", ops=[{"op": "school_template", "school": S(1680590, "曹县青岗集镇实验小学"), "year": "2023", "focus": "max"}])
sv("Y31", C("临汾市"), "2023", T("网络"), ["mean"])
add("Y32", ops=[{"op": "school_topic", "schools": [S(1683382, "肥城市孙伯镇初级中学")], "year": "2023", "metric": T("组织保障")}])
add("Y33", ops=[{"op": "compare", "scopes": [C("菏泽市"), C("长治市")], "year": "2023", "metric": T("教学评价")}])
add("Y34", ops=[{"op": "series", "scope": C("聊城市"), "years": ["2022", "2023"], "metric": T("教室")}])
add("Y35", ops=[{"op": "stat", "scope": C("泰安市"), "year": "2023", "metric": T("资源应用"), "stats": ["mean", "count"]}], default_year=True, note="S3 all items uncollected in 2023")
si("Y36", 1663605, "单县人民路中学", [38])
si("Y37", 1665450, "东昌府区大张中心小学", [49])
sv("Y38", C("大同市"), "2023", ["item", 72], ["mean"])
si("Y39", 1745366, "太谷侯城学校", [65])
si("Y40", 1675242, "东昌府区梁水镇中心中学", [30])

assert len(specs) == 100 and len({s["id"] for s in specs}) == 100
from collections import Counter
assert Counter(s["expect"] for s in specs) == {"answer": 84, "decline": 12, "clarify": 4}, Counter(s["expect"] for s in specs)
dump(PACKAGE / "inputs/TASK_SPECS.json", specs)
print(len(specs), "specs written")
