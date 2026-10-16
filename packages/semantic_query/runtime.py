from __future__ import annotations

import hashlib
import copy
import json
import re
from difflib import SequenceMatcher
from fnmatch import fnmatchcase
from typing import Any, Dict, Iterable, List, Optional, Tuple

from packages.core.national_scope import province_only_scope
from packages.experiment_protocol.mode import (
    mechanism_flags, requirement_preservation_enabled, requirement_preservation_scope,
)

COMMON_PATH_VERSION = "semantic-query-1.0"
PARSER_PROMPT_VERSION = "semantic-intent-c9-v5-public-scope-v1"
WRITER_PROMPT_VERSION = "semantic-answer-catalogue-coverage-v1.10"
SEMANTIC_CATALOGUE_VERSION = "school-digitization-semantics-v1"

DELIVERY_NOTICE_CATEGORIES = {
    "request_named_*": "对象或指标名称",
    "*unit*": "数值单位",
    "unsupported_numbers": "额外计算的数字",
    "arithmetic_*": "算式",
    "*direction*": "比较方向",
    "request_required_value_missing": "必答数值的写法",
    "*": "其他表述",
}

_LLM_MODES = {
    "product_semantic_constrained",
    "g1_llm_unconstrained",
    "g2_llm_constrained",
    "a1_no_execution_verification",
    "a2_no_output_verification_or_repair",
    "a3_no_answer_repair",
}
_INTENTS = {
    "clarify",
    "public_task",
    "region_eval",
    "school_eval",
    "school_fact",
    "region_compare",
    "school_compare",
    "region_longitudinal",
    "region_ranking",
    "national_ranking",
}
_DIMENSIONS = {
    "数字资源",
    "教育教学",
    "数字素养",
    "基础设施",
    "教育治理",
    "保障机制",
}
_DIMENSION_ALIASES = {
    "数字资源": (
        "数字资源", "数字化资源", "数字教育资源", "数字教学资源", "资源数字化",
        "数字内容", "教学数字内容",
    ),
    "教育教学": ("教育教学", "数字化教学", "信息化教学", "智慧教学", "教学数字化"),
    "数字素养": ("数字素养", "数字能力", "信息素养", "数字化素养"),
    "基础设施": (
        "基础设施",
        "数字基建",
        "信息化基础设施",
        "数字化基础设施",
        "信息化基础",
    ),
    "教育治理": ("教育治理", "数字治理", "信息化管理", "数字化管理", "治理数字化"),
    "保障机制": ("保障机制", "制度保障", "支撑保障", "数字化保障", "保障体系"),
}
_OVERALL_SCOPE_RE = re.compile(
    r"总体|整体|总览|全貌|大盘|综合(?:水平|情况|表现|发展|评估|概况)?",
    re.I,
)
_N2_SCOPE_CONFLICT_RE = re.compile(
    r"比较|对比|相比|差距|高于|低于|排名|排行|榜单|名次|排位|"
    r"变化|趋势|增长|下降|历年|跨年|两年|题项列表|问卷题项",
    re.I,
)
_SCOPE_TRANSITION_CONFLICT_RE = re.compile(
    r"比较|对比|相比|差距|高于|低于|排名|排行|榜单|名次|排位|"
    r"变化|趋势|增长|下降|历年|跨年|两年|题集|多题|题项列表",
    re.I,
)
_CONTEXTUAL_ENTITY_REFERENCE_RE = re.compile(
    r"^(?:其中|其|它|学校|区域|地区|该校|本校|这所学校|这个学校|上述学校|"
    r"该地区|本地区|该市|该区域|本区域|这个地区|这里|当地)(?:的)?$",
    re.I,
)
_STATISTICS = {None, "mean", "median", "mean_and_median", "count", "max", "min"}
_EXPLICIT_STATISTIC_RE = re.compile(
    r"均值|均分|平均(?:值|数|分)?|中位(?:数|值)?|数量|计数|最高|最低|"
    r"(?<![A-Za-z])(?:mean|average|median|max|min|count)(?![A-Za-z])",
    re.I,
)
_SCHOOL_FACT_EXPLICIT_STATISTIC_RE = re.compile(
    r"均值|均分|平均(?:值|数|分)?|中位(?:数|值)?|计数|最高|最低|"
    r"(?<![A-Za-z])(?:mean|average|median|max|min|count)(?![A-Za-z])",
    re.I,
)
_SCHOOL_FACT_EXPLICIT_COUNT_RE = re.compile(
    r"(?:归一(?:化)?(?:得分|分数|值)?|标准化(?:得分|分数)|原始(?:值|作答|编码)|作答|样本)"
    r"(?:的)?(?:数量|(?:(?:有|覆盖|依据|纳入|涉及|参与)(?:了)?)?(?:多少所|几所)学校|(?:有效)?学校数)"
    r"|计数|(?<![A-Za-z])count(?![A-Za-z])",
    re.I,
)
_AGGREGATIONS = {"school_equal_mean", "answer_pooled_mean"}
_OUTPUTS = {"direct_answer", "analysis", "table", "charts"}
_TASKDRAFT_FIELDS = {
    "intent",
    "regions",
    "schools",
    "periods",
    "dimension",
    "statistic",
    "aggregation",
    "school_filter",
    "question_ids",
    "value_view",
    "compound_key",
    "school_field",
    "target_rank",
    "target_top_n",
    "requested_outputs",
    "clarify_reason",
    "missing",
    "evidence",
    "confidence",
}
_EVIDENCE_FIELDS = {
    "region",
    "region_a",
    "region_b",
    "school",
    "school_a",
    "school_b",
    "period",
    "secondary_period",
    "dimension",
    "statistic",
    "aggregation",
    "school_filter",
    "metric",
    "target",
}

_REGION_LONGITUDINAL_UNSUPPORTED_INTENT_PATTERNS = (
    (
        "unsupported_region_longitudinal_significance",
        re.compile(r"显著(?:性|差异|变化)?|统计显著|统计检验|假设检验|p\s*值|置信区间", re.I),
        "需补充澄清：两期截面变化首批仅提供描述性差值，不支持显著性检验、P值或置信区间。",
    ),
    (
        "unsupported_region_longitudinal_causal_inference",
        re.compile(r"因果(?:效应|关系|影响|推断)?|归因于|是否(?:导致|造成)|(?:导致|造成)了", re.I),
        "需补充澄清：两期重复截面只能描述年度差值，不支持因果效应或归因判断。",
    ),
    (
        "unsupported_region_longitudinal_cohort",
        re.compile(
            r"同校队列|队列追踪|同一批学校|同批学校|相同一批学校|固定一批学校|"
            r"同一组学校|同样的学校|相同的学校|同一所学校|面板数据",
            re.I,
        ),
        "需补充澄清：当前能力是年度重复截面，不支持同一批学校或同一所学校的队列追踪。",
    ),
    (
        "unsupported_region_longitudinal_prediction",
        re.compile(r"预测|预估|外推|推算未来|估算未来|未来趋势|明年(?:会|将)", re.I),
        "需补充澄清：两期截面变化首批只描述已观测年份，不支持预测、外推或未来趋势判断。",
    ),
)
_REGION_RANKING_SCOPE_RE = re.compile(r"排名|排行|榜单|名次|排位", re.I)
_REGION_RANKING_TARGET_RE = re.compile(
    r"(?:Top\s*[+-]?\d+|前\s*[零一二三四五六七八九十百两\d]+"
    r"(?![零一二三四五六七八九十百两\d]|\s*(?:个\s*)?(?:年|月|日|天|周|期|季度|学期|题|种|类)|\s*个\s*(?:登记|数|值|项|题)|\s*项)\s*(?:名|所|个)?(?:学校)?|"
    r"第\s*[零一二三四五六七八九十百两\d]+\s*名|倒数第一|末位|最后一名|"
    r"排在最前面的\s*[零一二三四五六七八九十百两\d]+\s*所)",
    re.I,
)
_REGION_LONGITUDINAL_SCOPE_RE = re.compile(
    r"(?:20\d{2})\s*年?\s*(?:到|至|—|-|~|～|与|和)\s*(?:20\d{2})\s*年?"
    r"|趋势|变化|变动|跨年|历年|两期",
    re.I,
)
_REGION_LONGITUDINAL_OVERALL_RE = re.compile(
    r"(?:教育)?数字化(?:发展)?(?:综合)?(?:水平)?(?:的)?(?:趋势|变化|变动)"
    r"|(?:总体|整体|综合)(?:数字化)?(?:水平)?(?:的)?(?:趋势|变化|变动)",
    re.I,
)
_REGION_CAUSAL_INFERENCE_RE = re.compile(
    r"因果(?:效应|关系|影响|推断|分析)?|归因(?:于|分析|判断|推断|解释|关系|效应)?|"
    r"是否(?:导致|造成)|(?:导致|造成)了",
    re.I,
)
_REGION_RANKING_UNSUPPORTED_INTENT_PATTERNS = (
    (
        "unsupported_region_ranking_significance",
        re.compile(r"显著(?:性|差异|变化)?|统计显著|统计检验|假设检验|p\s*值|置信区间", re.I),
        "需补充澄清：区域排名首批只提供描述性名次与Top N，不支持显著性检验、P值或置信区间。",
    ),
    (
        "unsupported_region_ranking_causal_inference",
        _REGION_CAUSAL_INFERENCE_RE,
        "需补充澄清：区域排名首批只提供描述性名次与Top N，不支持因果分析或归因判断。",
    ),
)
_REGION_EVALUATION_UNSUPPORTED_INTENT_PATTERNS = (
    (
        "unsupported_region_causal_inference",
        _REGION_CAUSAL_INFERENCE_RE,
        "需补充澄清：单年度区域评估只提供描述性统计，不支持因果分析或归因判断。",
    ),
)
_TEACHER_OPERATION_SCOPE_RE = re.compile(
    r"(?:教师(?:查询|画像|档案|评估|趋势)|(?<![A-Za-z0-9])T\d{3,}(?![A-Za-z0-9]))",
    re.I,
)
_SCHOOL_NAME_FRAGMENT = r"[\u4e00-\u9fffA-Za-z0-9·（）()]{2,}(?:小学|中学|幼儿园|学校)"
_SCHOOL_RANKING_SCOPE_RE = re.compile(
    rf"(?:{_SCHOOL_NAME_FRAGMENT}\s*20\d{{2}}年?.{{0,24}}(?:排名|排行|名次|排位)|"
    rf"20\d{{2}}年?.{{0,24}}{_SCHOOL_NAME_FRAGMENT}.{{0,24}}(?:排名|排行|名次|排位))",
    re.I,
)

_CHART_NEGATION_RE = re.compile(
    r"(?:不需要|不需|不要|无需|不用|不画|别画|不生成|禁止)"
    r"(?:再)?(?:生成|输出|提供|绘制|画|展示)?(?:任何)?"
    r"(?:[一二两\d]{1,2}年)?(?:图表|图形|图|可视化|对比图|条形图)"
)
_CHART_REQUEST_RE = re.compile(
    r"(?:图表|图形|可视化|折线图|柱状图|单柱图|柱图|条形图|对比图|配图|用图比较|"
    r"用图(?:展示|表示|呈现|说明|比较|对照)|"
    r"(?:把|将).{0,20}(?:名字|名称|分数|得分|数量|学校数).{0,12}画出来|"
    r"(?:一|两|二)根柱子|雷达图|趋势图|画图|绘图|作图|生成图|画在一起|画在|画到)"
)
_SUMMARY_ONLY_RE = re.compile(
    r"简短(?:概括|回答|说明)|"
    r"(?:其他|其余|别的)(?:内容|维度|信息)?.{0,8}(?:先)?(?:不|别)(?:展开|看|说|展示|提供)|"
    r"(?:只|仅)(?:给|说|看|返回|展示).{0,20}(?:总体|整体|综合).{0,20}(?:分|结果|分母|学校)",
    re.I,
)
_REGION_SCORE_REQUEST_RE = re.compile(
    r"多少分|均分|平均(?:得分|分数|分|值|数)|学校等权(?:均分|均值)|"
    r"(?:总体|整体|综合|维度)(?:得分|分数|分)(?=[，,。；;？?]|是|为|在|于|相对|相比|多少|如何|怎样|变化)|"
    r"(?:得分|分数)(?:是|为|有)?多少|表现(?:如何|怎样|多少)",
    re.I,
)
_REGION_DENOMINATOR_REQUEST_RE = re.compile(
    r"覆盖(?:了)?多少所学校|依据(?:了)?多少所学校|"
    r"有效学校数|学校分母|(?:各自|两地|两侧|各地)?(?:有效)?样本(?:学校)?数|"
    r"多少所(?:有效|样本|参与|纳入)?学校(?:参与|纳入|用于|有数据)?|几所学校|"
    r"有效作答数|作答分母|多少条有效作答|按各年各自样本(?:说明|比较)",
    re.I,
)
_REGION_SCHOOL_COUNT_REQUEST_RE = re.compile(
    r"(?:有效|纳入|参与|有数据的)?学校(?:共有|有)?多少所|"
    r"多少所学校.{0,24}(?:参与计算|提供.{0,8}记录)|"
    r"共有多少所(?:有效|纳入|参与|有数据的)?学校|"
    r"(?:有效|样本|参与|纳入)学校数|几所(?:有效|样本|参与|纳入)?学校",
    re.I,
)
_REGION_VALID_SCORE_POPULATION_RE = re.compile(
    r"(?:有|具备)(?:有效)?(?:问卷|总体|整体|综合|数字化|维度)*(?:得分|分数|数据)的学校|"
    r"(?:纳入|参与)(?:本次|该次|数字化)?(?:统计|计算|评估|监测)的(?:有效)?学校|"
    r"有效(?:得分|分数|数据|样本)学校|"
    r"有效学校数|"
    r"学校提供.{0,8}(?:参与计算|用于计算).{0,8}记录",
    re.I,
)
_REGION_NON_MEAN_STATISTIC_RE = re.compile(
    r"中位(?:数|值)?|最高|最低|最大|最小|"
    r"(?<![A-Za-z])(?:median|max|min)(?![A-Za-z])",
    re.I,
)

_EXCLUDED_SCOPE_RE = re.compile(
    r"(?:不要|不需要|不必|不用|无需|不再|不查|不报|不列|不展开|不给|不画|不做|不比较|"
    r"不换算|不默认|不把|不能|不是|不补充|不补写|不补|(?<!分)别(?:用|列|把)|禁止|"
    r"取消(?:乡村|农村|城乡|学校|维度|基础设施|数字资源|题项)|"
    r"不单查|先不单查)[^，,。；;：:！？!?\n]*"
)
_DESCRIPTIVE_DIMENSIONS = {
    "数字资源": re.compile(r"数字教学内容建设[、，,\s]*(?:来源|获取)[、，,\s]*(?:和|与|及)?使用"),
    "教育教学": re.compile(
        r"课堂组织(?:和|与|、|及)考试评价|"
        r"(?:数字|信息)?技术.{0,6}(?:上课|课堂).{0,12}考试评价|"
        r"(?:把)?技术用于课堂(?:与|和|、)评价"
    ),
    "基础设施": re.compile(
        r"设备[、，,\s]*(?:和|与)?网络(?:和|与|、|及)(?:智能)?(?:学习|教学)(?:场所|空间)"
    ),
    "教育治理": re.compile(r"校务[、，,\s]*(?:和|与)?数据协同(?:和|与|、|及)校园安全(?:管理)?|用数字系统办校务[、，,\s]*管理数据(?:和|与|、|及)校园安全"),
    "保障机制": re.compile(
        r"组织[、，,\s]*(?:和|与)?人员(?:和|与|、|及)经费(?:保障|支持)?|"
        r"人员[、，,\s]*(?:安排|设置)?组织(?:机制|保障|安排)?(?:和|与|、|及)(?:资金|经费)(?:支持|保障|支撑)?"
    ),
    "数字素养": re.compile(
        r"教师使用技术的能力[、，,\s]*(?:相关)?培训(?:和|与|、|及)学生实践|"
        r"师生数字能力培养[、，,\s]*(?:和|与|、|及)?培训(?:和|与|、|及)实践"
    ),
}

def _scope_assertion_text(question: str, *, mask_explanations: bool = True) -> str:
    pass
    text = re.sub(
        r"(?:^|(?<=[，,。；;]))[^，,。；;！？!?\n]*(?:都不在本次范围|不在本次范围|先不看)[^，,。；;！？!?\n]*",
        lambda match: " " * len(match.group(0)), str(question or ""),
    )

    if mask_explanations:
        text = re.sub(
            r"(?:请)?(?:同时)?(?:说明|解释)[^，,。；;！？!?\n]*(?:口径|缺失|金额)[^，,。；;！？!?\n]*",
            lambda match: " " * len(match.group(0)), text,
        )
    return _EXCLUDED_SCOPE_RE.sub(lambda match: " " * len(match.group(0)), text)

def _scope_question_ids(question: str) -> List[int]:
    from packages.semantic_query.catalogue import extract_explicit_question_ids

    return extract_explicit_question_ids(_scope_assertion_text(question))

def _catalogue_alias_items(question: str) -> List[Tuple[int, str]]:
    pass
    text = _scope_assertion_text(question)
    if _scope_question_ids(text) or re.search(r"维度(?:得分|分数|均分)|六维", text):
        return []
    catalogue, _ = _catalogue()
    ratio_requested = bool(re.search(r"教师比例|百分比|占比|比例|百分之", text))
    matches = [
        (int(item["id"]), alias)
        for item in catalogue
        for alias in item.get("aliases") or []
        if len(alias) >= 4 and alias in text
        and (not ratio_requested or item.get("value_type") == "ratio")
    ]

    if re.search(r"学生类[、和与]教师类|教师类[、和与]学生类", text):
        for item in catalogue:
            for alias in item.get("aliases") or []:
                if alias.startswith(("学生类", "教师类")) and len(alias) >= 6:
                    suffix = alias[3:]
                    if suffix in text and ("学生类" in text and "教师类" in text):
                        matches.append((int(item["id"]), alias))

    paired_device = re.search(
        r"(?:教师|老师)(?:用)?[、和与](?:学生)(?:用)?(平板(?:电脑)?|台式机)"
        r"|学生(?:用)?[、和与](?:教师|老师)(?:用)?(平板(?:电脑)?|台式机)",
        text,
    )
    synthetic_positions: Dict[int, int] = {}
    if paired_device:
        device = "台式机" if "台式机" in paired_device.group(0) else "平板" if "平板" in paired_device.group(0) else None
        if device:
            for item in catalogue:
                aliases = item.get("aliases") or []
                if device == "台式机":
                    summary = str(item.get("summary") or "")
                    for actor in ("学生", "教师"):
                        if summary.endswith(f"{actor}用台式机数量"):
                            aliases = [*aliases, f"{actor}用台式机"]
                for alias in aliases:
                    if alias in {f"学生用{device}", f"教师用{device}", f"老师{device}"}:
                        qid = int(item["id"])
                        matches.append((qid, alias))
                        actor = "学生" if alias.startswith("学生") else "教师"
                        actor_match = re.search(r"学生" if actor == "学生" else r"教师|老师", paired_device.group(0))
                        if actor_match:
                            synthetic_positions[qid] = paired_device.start() + actor_match.start()
    best = {}
    for qid, alias in matches:
        if qid not in best or len(alias) > len(best[qid]):
            best[qid] = alias
    return sorted(
        best.items(),
        key=lambda pair: text.find(pair[1]) if pair[1] in text else synthetic_positions.get(
            pair[0], text.find("学生类") if pair[1].startswith("学生类") else text.find("教师类")
        ),
    )

def _unique_catalogue_alias_item(question: str) -> Optional[Tuple[int, str]]:
    matches = _catalogue_alias_items(question)
    return matches[0] if len(matches) == 1 else None

def _scope_years(question: str) -> List[str]:
    return list(dict.fromkeys(re.findall(r"(?<!\d)(20\d{2})(?!\d)", _scope_assertion_text(question))))

def _explicit_value_view(question: str) -> Optional[str]:
    text = _scope_assertion_text(question)
    changed_view = re.search(
        r"(?:原始(?:登记)?(?:值|数量|台数|编码)|标准化(?:得分|分数)|归一化(?:得分|分数))"
        r"(?:改为|换成|改成)(原始(?:登记)?(?:值|数量|台数|编码)|标准化(?:得分|分数)|归一化(?:得分|分数))",
        text,
    )
    if changed_view:
        return "raw_value" if changed_view.group(1).startswith("原始") else "normalized_score"
    raw = bool(re.search(r"原始(?:登记)?(?:值|作答|编码|数量|台数|种数)|raw\s*(?:value)?", text, re.I))
    normalized = bool(re.search(r"归一(?:化)?(?:得分|分数|值)?|标准化(?:得分|分数|均分|值)?|标准分", text))
    if raw and normalized:
        return "mixed_raw_and_normalized_score"
    return "raw_value" if raw else "normalized_score" if normalized else None

def _literal_school_filter(question: str) -> Optional[str]:
    text = _scope_assertion_text(question)
    for school in _literal_school_name_spans(text):
        text = text.replace(school, " " * len(school))

    pattern = r"乡村|农村|城镇或城区|城镇|城区|城市|镇区|公办|民办|小学|初中|高中|幼儿园"
    matches = re.findall(
        rf"(?:登记(?:在|为)|只(?:取|保留|纳入|看)|仅限).{{0,12}}?({pattern})"
        rf"|({pattern})(?=(?:学校|校|样本|学段|组)(?:中|里|的|数|各)?(?:[，,。；;\s]|$)?)",
        text,
    )
    filters = list(dict.fromkeys(left or right for left, right in matches))
    combined = re.search(r'(乡村|农村|城区|城镇|城市)\s*(小学|初中|高中|幼儿园)(?:学校|样本|学段|组|第|的|中|里|[，,。；;\s]|$)', text)
    if combined:
        return ' '.join(combined.groups())
    return filters[0] if len(filters) == 1 else None

def _literal_region_spans(question: str) -> List[str]:
    pass
    text = _scope_assertion_text(question)
    for school_span in _literal_school_name_spans(text):
        text = text.replace(school_span, " " * len(school_span))

    text = re.sub(r"(自治区|特别行政区|省)\s+(?=[\u4e00-\u9fff]{2,10}?(?:市|区|县|旗))", r"\1", text)
    text = re.sub(r"(市)\s+(?=[\u4e00-\u9fff]{2,10}?(?:区|县|旗))", r"\1", text)
    text = re.sub(
        r"^(?:(?:请问|请|比较|对比|评估|查看|查询|改查|改看|换成|换到|"
        r"再把地区换成|原本想给|把|在|按|想给|给出|给我|只看|只查|分析|统计))+", "", text,
    )
    spans: List[str] = []
    for clause in re.split(r"[，,、。；;！？!?：:\s\d]|(?<=[省市县区旗])(?:与|和|比|减)|以及|放左边|放右边", text):
        clause = re.sub(r"^年", "", clause)

        clause = re.sub(r"^(?:(?:并|且|再|请)?(?:算出|计算|给出|列出))+", "", clause)

        match = re.match(r"(?:[\u4e00-\u9fff]{2,8}?(?:省(?!级)|市(?!级)|县(?!级)|区(?!域|县|级)|旗)){1,3}", clause)
        if not match:
            continue
        span = re.sub(r"^(?:再把地区换成|地区换成|改查|改看|换成|换到|比较|对比|把|在|从|请|按)+", "", match.group(0))
        if span in {"这个地区", "该地区", "同地区", "同年同地区", "所有地区", "全部地区", "登记在城区", "登记在城镇或城区", "区县", "县区"}:
            continue
        if re.fullmatch(r"(?:题面|上述|这些|各个|各|两个|两|多个|多|前[一二两三四五六七八九十\d]+个)城市", span):
            continue
        if span.endswith(("地区", "区域", "城区", "镇区", "全市", "该市", "本市", "两市")):
            continue

        units = re.findall(r"[\u4e00-\u9fff]{2,8}?(?:省|市|县|区|旗)", span)
        if len({unit[-1] for unit in units}) < len(units):
            spans.extend(unit for unit in units if unit not in spans)
        elif span and span not in spans:
            spans.append(span)
    return spans

def _explicit_scoped_population_change(question: str) -> bool:
    pass
    text = _scope_assertion_text(question)
    return bool(
        _explicit_region_school_count_only(text)
        and len(_scope_years(text)) == 2
        and len(_literal_region_spans(text)) == 1
        and (_OVERALL_SCOPE_RE.search(text) or _explicit_dimension_alias(text)[0])
        and re.search(r"计算|评估|统计", text)
        and re.search(r"变化|差值|分差|多或少|增加|减少|减", text)
    )

def _explicit_chart_request(question: str) -> Optional[bool]:
    text = str(question or "")
    if _CHART_NEGATION_RE.search(text):
        return False

    format_text = _scope_assertion_text(text, mask_explanations=False)

    def requested_quote(match):

        prefix = format_text[:match.start()]
        if not re.search(r"(?:说明|解释|历史|原话|提到|提及)[^，,。；;！？!?\n]*$", prefix):
            return match.group(0)
        if re.search(
            r"(?:^|请|并|且|再|还|同时|需要|要求)(?:给我)?"
            r"(?:画|绘制|生成|制作|配|提供|输出|展示|用|执行|按|按照)"
            r"(?:[一两二\d]{1,2}张)?\s*$", prefix,
        ):
            return match.group(0)
        return " " * len(match.group(0))

    format_text = re.sub(r"“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|\"[^\"\n]*\"|'[^'\n]*'", requested_quote, format_text)
    if re.search(r'(?:画|绘制|生成|制作|展示)[^，,。；;！？!?\n]{0,24}(?:柱子|柱形|条形|双柱)',format_text):
        return True
    if re.search(r'(?:柱形|条形|双柱)[^，,。；;！？!?\n]{0,12}(?:展示|呈现|对比)',format_text):
        return True
    metric_text = _scope_assertion_text(text)
    if any(metric_text[match.start():match.end()].strip() for match in _CHART_REQUEST_RE.finditer(format_text)):
        return True

    for match in re.finditer(
        r"(?:并|且|还|再|同时|另外|以及)(?:请|需要|要)?(?:给我)?"
        r"(?:画|绘制|生成|制作|配|提供|输出|展示|用|以|按|按照)"
        r"[^，,。；;！？!?\n]*", format_text,
    ):
        if _CHART_REQUEST_RE.search(match.group(0)):
            return True
    return None

def _explicit_summary_only_request(question: str) -> bool:
    pass
    return bool(_SUMMARY_ONLY_RE.search(str(question or "")))

def _explicit_region_score_with_denominator(question: str) -> bool:
    pass
    text = _scope_assertion_text(question)
    return bool(
        _REGION_SCORE_REQUEST_RE.search(text)
        and _REGION_DENOMINATOR_REQUEST_RE.search(text)
        and not _REGION_NON_MEAN_STATISTIC_RE.search(text)
    )

def _explicit_region_school_count_only(question: str) -> bool:
    pass
    text = _scope_assertion_text(question)
    return bool(
        (
            _REGION_SCHOOL_COUNT_REQUEST_RE.search(text)
            or re.search(r"学校数", text)
        )
        and _REGION_VALID_SCORE_POPULATION_RE.search(text)
        and not _REGION_SCORE_REQUEST_RE.search(text)
        and not _REGION_NON_MEAN_STATISTIC_RE.search(text)
    )

def _explicit_answer_pooled_mean(question: str) -> bool:
    pass
    text = _scope_assertion_text(question)
    return bool(
        re.search(r"作答池化|池化平均|池化|(?<![A-Za-z])pool(?:ed|ing)?(?![A-Za-z])", text, re.I)
        or re.search(r"(?:所有|全部|每条)?有效题项作答.{0,8}(?:合并|汇总).{0,8}(?:平均|均值|均分)", text)
    )

def is_region_population_confirmation(question: str) -> bool:
    pass
    text = _scope_assertion_text(question).strip()
    affirmative = bool(
        re.search(r"^(?:是|对|对的|确认|是的|就是|指|按)", text)
    )
    return bool(
        affirmative
        and _REGION_VALID_SCORE_POPULATION_RE.search(text)
        and (
            _REGION_SCHOOL_COUNT_REQUEST_RE.search(text)
            or re.search(r"学校数", text)
        )
        and not _REGION_SCORE_REQUEST_RE.search(text)
        and not _REGION_NON_MEAN_STATISTIC_RE.search(text)
        and not _scope_years(text)
        and not _literal_region_spans(text)
        and not _scope_question_ids(text)
        and _single_literal_school_span(text) is None
        and not re.search(r"新任务|另起|换个|改查|改看|比较|对比", text)
    )

def _explicit_bare_region_school_population_count(question: str) -> bool:
    pass
    text = _scope_assertion_text(question)
    return bool(
        _REGION_SCHOOL_COUNT_REQUEST_RE.search(text)
        and not _REGION_VALID_SCORE_POPULATION_RE.search(text)
        and not _REGION_SCORE_REQUEST_RE.search(text)
        and not _REGION_NON_MEAN_STATISTIC_RE.search(text)
    )

def _explicit_single_region_identity(question: str) -> Optional[Tuple[str, str]]:
    pass
    text = _scope_assertion_text(question)
    spans = _literal_region_spans(text)
    if not text or len(spans) != 1 or _N2_SCOPE_CONFLICT_RE.search(text):
        return None
    try:
        from scripts.region_engine import detect_region

        resolved = detect_region(spans[0])
    except Exception:
        return None
    if not resolved or resolved.get("error"):
        return None

    canonical = str(resolved.get("name") or "").strip()
    literal_candidates: List[str] = []
    for key in ("name", "district", "city", "province"):
        value = str(resolved.get(key) or "").strip()
        if value and value in text and value not in literal_candidates:
            literal_candidates.append(value)
    if canonical and canonical in text and canonical not in literal_candidates:
        literal_candidates.append(canonical)
    if not literal_candidates:
        return None

    literal_span = max(literal_candidates, key=len)
    return canonical or literal_span, literal_span

def _explicit_dimension_aliases(question: str) -> List[Tuple[str, str]]:
    matches: List[Tuple[str, str]] = []
    text = _scope_assertion_text(question)
    for dimension, aliases in _DIMENSION_ALIASES.items():
        spans = [alias for alias in aliases if alias in text]
        if spans:
            matches.append((dimension, max(spans, key=len)))
        elif dimension in _DESCRIPTIVE_DIMENSIONS:
            match = _DESCRIPTIVE_DIMENSIONS[dimension].search(text)
            if match:
                matches.append((dimension, match.group(0)))
    from packages.semantic_query.catalogue import described_dimension_binding
    binding = described_dimension_binding(text)
    if binding['dimension'] and binding['dimension'] not in {name for name, _ in matches}:
        matches.append((binding['dimension'], binding['quote']))
    matches.sort(key=lambda m: text.find(m[1]) if m[1] in text else 9999)
    return matches

def _explicit_dimension_pair(question: str) -> List[str]:
    pass
    from packages.semantic_query.semantic_preflight import dimension_pair_requirements
    bound = dimension_pair_requirements(question)
    if bound['uncertain']:
        return []
    return bound['operands'] if bound['difference'] else bound['dimensions']

def _explicit_dimension_alias(question: str) -> Tuple[Optional[str], Optional[str]]:
    matches = _explicit_dimension_aliases(question)
    if len(matches) != 1:
        return None, None
    return matches[0]

def _anchored_school_item_qid(
    question: str, candidate: Dict[str, Any], dimension_span: Optional[str]
) -> Optional[int]:
    pass
    qids = _string_list(candidate.get("question_ids"))
    if str(candidate.get("intent") or "") != "school_fact" or len(qids) != 1:
        return None
    qid = _integer_or_none(qids[0])
    if qid not in _catalogue()[1] or str(candidate.get("value_view") or "") != "raw_value":
        return None
    metric_span = str((candidate.get("evidence") or {}).get("metric") or "").strip()
    if not metric_span or metric_span not in question or metric_span == dimension_span:
        return None
    if re.search(r"维度|(?:总体|整体|综合).{0,8}(?:得分|评分|分数)|(?:得分|评分|分数|均分)", question):
        return None
    return qid

def _management_work_count_pair(question: str, candidate: Dict[str, Any]) -> List[int]:
    pass
    qids = [_integer_or_none(q) for q in _string_list(candidate.get("question_ids"))]
    if (candidate.get("intent") != "school_fact" or candidate.get("value_view") != "raw_value"
            or len(qids) != 2 or len(set(qids)) != 2
            or "工作内容" not in question or not re.search(r"多少种|种类数|数量|计数", question)
            or re.search(r"维度|六维|得分|评分|分数|均分", question)):
        return []
    named = _management_work_count_items(question)
    return named if len(named) == 2 and set(named) == set(qids) else []

def _management_work_count_items(question: str) -> List[int]:
    pass
    if ("工作内容" not in question or not re.search(r"多少种|种类数|数量|计数", question)
            or re.search(r"维度|六维|得分|评分|分数|均分", question)):
        return []
    compact = re.sub(r"[\s、，,与]", "", question)
    positions = {}
    for qid, item in _catalogue()[1].items():
        label = re.search(r"实现(.+?)类教育数字化管理", str(item.get("question") or ""))
        if (not label or item.get("value_type") != "count" or item.get("scale") != "raw_count"
                or item.get("coding_status") != "not_applicable"):
            continue
        name = label.group(1).replace("与", "")
        at = compact.find(name)
        if at >= 0:
            positions[qid] = at
    if len(set(positions.values())) != len(positions):
        return []
    return sorted(positions, key=positions.__getitem__)

def _is_contextual_entity_reference(value: Any) -> bool:
    pass
    token = str(value or "").strip(" \t\r\n，。！？、：:；;“”‘’()（）")
    return bool(token and _CONTEXTUAL_ENTITY_REFERENCE_RE.fullmatch(token))

_RANK_NUMBER_MAP = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
}

def _explicit_ranking_target(question: str) -> Tuple[Optional[int], Optional[int]]:
    pass
    text = _scope_assertion_text(question)
    if re.search(r"最后一(?:名|所)|倒数第一(?:名)?|倒数第1名?|排名最后|排最后|末位|排名末位", text):
        return -1, None
    if re.search(r"榜首|第一名|排第一|排名第一|排在第一|最高分学校|分数最高的学校", text):
        return 1, None

    def parse_number(token: str) -> Optional[int]:
        value = str(token or "").strip()
        if value.isdigit():
            return int(value)
        return _RANK_NUMBER_MAP.get(value)

    top_match = re.search(
        r"(?:排名前|前|top\s*)\s*(\d+|[一二两三四五六七八九十]{1,3})"
        r"(?![零一二三四五六七八九十百两\d]|\s*(?:个\s*)?(?:年|月|日|天|周|期|季度|学期|题|种|类))"
        r"\s*(?:名|所|家|个)?(?:学校)?(?!题)",
        text,
        re.I,
    )
    if top_match:
        top_n = parse_number(top_match.group(1))
        if top_n is not None and 1 <= top_n <= 100:
            return None, top_n
    leading_match = re.search(
        r"排在最前面的\s*(\d+|[一二两三四五六七八九十]{1,3})\s*所",
        text,
    )
    if leading_match:
        top_n = parse_number(leading_match.group(1))
        if top_n is not None and 1 <= top_n <= 100:
            return None, top_n
    rank_match = re.search(
        r"(?:(?:排名?第|位列第|位居第|排第)\s*"
        r"(\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|位|名次)?"
        r"|第\s*(\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|位|名次))",
        text,
    )
    if rank_match:
        rank = parse_number(rank_match.group(1) or rank_match.group(2))
        if rank is not None and rank > 0:
            return rank, None
    colloquial_match = re.search(
        r"第\s*(\d+|[一二两三四五六七八九十]{1,3})\s*"
        r"(?:是|为)?\s*(?:哪|谁|什么)",
        text,
    )
    if colloquial_match:
        rank = parse_number(colloquial_match.group(1))
        if rank is not None and rank > 0:
            return rank, None
    return None, None

def _reconcile_n2_scope_candidate(
    question: str, candidate: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    pass
    question = _scope_assertion_text(question)
    reconciled = dict(candidate)
    intent = str(reconciled.get("intent") or "clarify").strip()
    regions = _string_list(reconciled.get("regions"))
    periods = _string_list(reconciled.get("periods"))
    schools = _string_list(reconciled.get("schools"))

    rank, top_n = _explicit_ranking_target(question)
    alias_dimension, alias_span = _explicit_dimension_alias(question)
    count_only = _explicit_region_school_count_only(question)
    if (
        intent in {"clarify", "region_eval", "region_ranking"}
        and len(regions) == 1
        and len(periods) == 1
        and not schools
        and ((rank is None) != (top_n is None))
        and alias_dimension is None
        and not reconciled.get("question_ids")
        and not reconciled.get("compound_key")
        and not reconciled.get("school_field")
    ):
        reconciliation_applied = any((
            intent != "region_ranking",
            reconciled.get("dimension") is not None,
            str(reconciled.get("statistic") or "") != "mean",
            str(reconciled.get("aggregation") or "") != "school_equal_mean",
            str(reconciled.get("value_view") or "") != "normalized_score",
            reconciled.get("target_rank") != rank,
            reconciled.get("target_top_n") != top_n,
            bool(reconciled.get("clarify_reason")),
            bool(reconciled.get("missing")),
        ))
        reconciled["intent"] = "region_ranking"
        reconciled["dimension"] = None
        reconciled["statistic"] = "mean"
        reconciled["aggregation"] = "school_equal_mean"
        reconciled["value_view"] = "normalized_score"
        reconciled["target_rank"] = rank
        reconciled["target_top_n"] = top_n
        reconciled["clarify_reason"] = None
        reconciled["missing"] = []
        return reconciled, {
            "applied": reconciliation_applied,
            "reason": "explicit_region_ranking_target_with_single_region_period",
            "target_rank": rank,
            "target_top_n": top_n,
        }

    explicit_years: List[str] = []
    for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", str(question or "")):
        if year not in explicit_years:
            explicit_years.append(year)
    from packages.semantic_query.catalogue import extract_explicit_question_ids

    explicit_question_ids = _scope_question_ids(question)
    catalogue_alias_ids = [item[0] for item in _catalogue_alias_items(question)]
    catalogue_item_bound = bool(
        catalogue_alias_ids
        and [str(qid) for qid in catalogue_alias_ids] == _string_list(reconciled.get("question_ids"))
    )
    model_item_bound = bool(_anchored_school_item_qid(question, reconciled, alias_span) is not None
                            or _management_work_count_pair(question, reconciled))
    explicit_school = _explicit_school_identity(question)
    bare_school_population_count = _explicit_bare_region_school_population_count(
        question
    )
    explicit_region = (
        _explicit_single_region_identity(question)
        if bare_school_population_count
        else None
    )
    explicit_overall = bool(_OVERALL_SCOPE_RE.search(str(question or "")))
    explicit_raw = _explicit_value_view(question) == "raw_value"
    explicit_statistic_requested = bool(
        _EXPLICIT_STATISTIC_RE.search(str(question or ""))
    )
    if (
        explicit_school is not None
        and len(explicit_years) == 1
        and not explicit_question_ids
        and not catalogue_item_bound
        and not model_item_bound
        and not explicit_raw
        and not explicit_statistic_requested
        and not _SCOPE_TRANSITION_CONFLICT_RE.search(str(question or ""))
        and (explicit_overall != (alias_dimension is not None))
    ):

        school_dimension = alias_dimension if alias_dimension is not None else None
        reconciliation_applied = any((
            intent != "school_eval",
            schools != [explicit_school[0]],
            bool(regions),
            periods != explicit_years,
            reconciled.get("dimension") != school_dimension,
            reconciled.get("statistic") is not None,
            reconciled.get("aggregation") is not None,
            reconciled.get("school_filter") is not None,
            bool(reconciled.get("question_ids")),
            reconciled.get("compound_key") is not None,
            reconciled.get("school_field") is not None,
            reconciled.get("target_rank") is not None,
            reconciled.get("target_top_n") is not None,
            str(reconciled.get("value_view") or "") != "normalized_score",
            bool(reconciled.get("clarify_reason")),
            bool(reconciled.get("missing")),
        ))
        reconciled["intent"] = "school_eval"
        reconciled["schools"] = [explicit_school[0]]
        reconciled["regions"] = []
        reconciled["periods"] = explicit_years
        reconciled["dimension"] = school_dimension
        reconciled["statistic"] = None
        reconciled["aggregation"] = None
        reconciled["school_filter"] = None
        reconciled["question_ids"] = []
        reconciled["compound_key"] = None
        reconciled["school_field"] = None
        reconciled["target_rank"] = None
        reconciled["target_top_n"] = None
        reconciled["value_view"] = "normalized_score"
        reconciled["clarify_reason"] = None
        reconciled["missing"] = []
        evidence = dict(reconciled.get("evidence") or {})
        for key in (
            "region", "region_a", "region_b", "school_a", "school_b",
            "secondary_period", "metric",
        ):
            evidence.pop(key, None)
        evidence["school"] = explicit_school[1]
        evidence["period"] = explicit_years[0]
        if alias_span:
            evidence["dimension"] = alias_span
        else:
            evidence.pop("dimension", None)
        reconciled["evidence"] = evidence
        reason = (
            "explicit_school_dimension_scope"
            if school_dimension is not None
            else "explicit_school_overall_scope"
        )
        return reconciled, {
            "applied": reconciliation_applied,
            "reason": reason,
            "school": explicit_school[0],
            "period": explicit_years[0],
            "dimension": school_dimension,
        }
    candidate_dimension = str(reconciled.get("dimension") or "").strip() or None
    candidate_statistic = str(reconciled.get("statistic") or "").strip()
    explicit_statistic = bool(_EXPLICIT_STATISTIC_RE.search(str(question or "")))
    candidate_evidence = dict(reconciled.get("evidence") or {})
    candidate_dimension_span = str(candidate_evidence.get("dimension") or "").strip()
    if (
        intent in {"clarify", "region_eval", "region_longitudinal"}
        and len(regions) == 1
        and len(explicit_years) == 2
        and not schools
        and _REGION_LONGITUDINAL_SCOPE_RE.search(str(question or ""))
        and not _REGION_RANKING_SCOPE_RE.search(str(question or ""))
        and not reconciled.get("question_ids")
        and not reconciled.get("compound_key")
        and not reconciled.get("school_field")
        and (candidate_statistic in {"mean", ""} or not explicit_statistic or _explicit_scoped_population_change(question))
    ):
        if (
            alias_dimension is None
            and candidate_dimension in _DIMENSIONS
            and candidate_dimension_span
            and candidate_dimension_span in str(question or "")
        ):
            longitudinal_dimension = candidate_dimension
            reason = "model_dimension_with_explicit_longitudinal_range"
        elif alias_dimension is not None:
            longitudinal_dimension = alias_dimension
            reason = "explicit_dimension_with_longitudinal_range"
        elif (
            _REGION_LONGITUDINAL_OVERALL_RE.search(str(question or ""))
            or _OVERALL_SCOPE_RE.search(str(question or ""))
        ):
            longitudinal_dimension = None
            reason = "explicit_overall_longitudinal_range"
        else:
            longitudinal_dimension = None
            reason = ""
        if reason:
            reconciliation_applied = any((
                intent != "region_longitudinal",
                periods != explicit_years,
                candidate_dimension != longitudinal_dimension,
                candidate_statistic != "mean",
                str(reconciled.get("aggregation") or "") != "school_equal_mean",
                str(reconciled.get("value_view") or "") != "normalized_score",
                reconciled.get("target_rank") is not None,
                reconciled.get("target_top_n") is not None,
                bool(reconciled.get("clarify_reason")),
                bool(reconciled.get("missing")),
            ))
            reconciled["intent"] = "region_longitudinal"
            reconciled["periods"] = explicit_years
            reconciled["dimension"] = longitudinal_dimension
            reconciled["statistic"] = "count" if _explicit_scoped_population_change(question) else "mean"
            reconciled["aggregation"] = "school_equal_mean"
            reconciled["value_view"] = "normalized_score"
            reconciled["target_rank"] = None
            reconciled["target_top_n"] = None
            reconciled["clarify_reason"] = None
            reconciled["missing"] = []
            if alias_span:
                evidence = dict(reconciled.get("evidence") or {})
                evidence["dimension"] = alias_span
                reconciled["evidence"] = evidence
            return reconciled, {
                "applied": reconciliation_applied,
                "reason": reason,
                "periods": explicit_years,
                "dimension": longitudinal_dimension,
            }

    if (
        bare_school_population_count
        and explicit_region is not None
        and len(explicit_years) == 1
        and explicit_school is None
        and not explicit_question_ids
        and not _SCOPE_TRANSITION_CONFLICT_RE.search(str(question or ""))
    ):

        reconciled["intent"] = "clarify"
        reconciled["regions"] = [explicit_region[0]]
        reconciled["periods"] = explicit_years
        reconciled["schools"] = []
        reconciled["dimension"] = None
        reconciled["statistic"] = None
        reconciled["aggregation"] = None
        reconciled["school_filter"] = None
        reconciled["value_view"] = None
        reconciled["question_ids"] = []
        reconciled["compound_key"] = None
        reconciled["school_field"] = None
        reconciled["target_rank"] = None
        reconciled["target_top_n"] = None
        reconciled["clarify_reason"] = "school_population_scope_required"
        reconciled["missing"] = [
            "请确认是否指教育数字化评估中具有有效得分的学校数；当前不提供行政学校名录总数。"
        ]
        reconciled["evidence"] = {
            "region": explicit_region[1],
            "period": explicit_years[0],
        }
        return reconciled, {
            "applied": True,
            "reason": "bare_school_population_count_fail_closed",
            "reason_code": "school_population_scope_required",
            "scope_source": "original_utterance",
            "region": explicit_region[0],
            "period": explicit_years[0],
        }

    if (
        intent not in {"clarify", "region_eval"}
        or len(regions) != 1
        or len(periods) != 1
        or schools
        or reconciled.get("question_ids")
        or reconciled.get("compound_key")
        or reconciled.get("school_field")
        or _N2_SCOPE_CONFLICT_RE.search(str(question or ""))
    ):
        return reconciled, {"applied": False}

    candidate_dimension = str(reconciled.get("dimension") or "").strip() or None
    candidate_evidence = dict(reconciled.get("evidence") or {})
    candidate_dimension_span = str(candidate_evidence.get("dimension") or "").strip()
    if (
        alias_dimension is None
        and candidate_dimension in _DIMENSIONS
        and candidate_dimension_span
        and candidate_dimension_span in str(question or "")
    ):
        dimension = candidate_dimension
        reason = "model_dimension_with_explicit_single_region_period"
    elif alias_dimension is not None:
        dimension = alias_dimension
        reason = "explicit_dimension_alias"
    elif _OVERALL_SCOPE_RE.search(str(question or "")):
        dimension = None
        reason = "explicit_overall_scope"
    elif count_only:
        dimension = None
        reason = "explicit_valid_score_population"
    else:
        return reconciled, {"applied": False}

    score_with_denominator = _explicit_region_score_with_denominator(question)
    reconciled["intent"] = "region_eval"
    reconciled["dimension"] = dimension
    if count_only:
        reconciled["statistic"] = "count"
        reconciled["aggregation"] = "school_equal_mean"
        reconciled["value_view"] = "normalized_score"
    elif score_with_denominator and not explicit_raw and not re.search(r"池化|pool(?:ed|ing)?", question, re.I):

        reconciled["statistic"] = "mean"
        reconciled["aggregation"] = "school_equal_mean"
        reconciled["value_view"] = "normalized_score"
    reconciled["clarify_reason"] = None
    reconciled["missing"] = []
    if alias_span:
        evidence = dict(reconciled.get("evidence") or {})
        evidence["dimension"] = alias_span
        reconciled["evidence"] = evidence
    reconciliation_applied = any((
        intent != "region_eval",
        candidate_dimension != dimension,
        count_only and candidate_statistic != "count",
        count_only
        and str(candidate.get("aggregation") or "").strip() != "school_equal_mean",
        count_only
        and str(candidate.get("value_view") or "").strip() != "normalized_score",
        score_with_denominator and candidate_statistic != "mean",
        score_with_denominator
        and str(candidate.get("aggregation") or "").strip() != "school_equal_mean",
        score_with_denominator
        and str(candidate.get("value_view") or "").strip() != "normalized_score",
        bool(candidate.get("clarify_reason")),
        bool(candidate.get("missing")),
    ))
    return reconciled, {
        "applied": reconciliation_applied,
        "reason": (
            f"{reason}_school_count_only"
            if count_only else (
                f"{reason}_score_with_school_denominator"
                if score_with_denominator else reason
            )
        ),
        "dimension": dimension,
        "result_denominator": "coverage_count" if score_with_denominator else None,
        "output_focus": "school_count" if count_only else None,
    }

def _validate_explicit_statistic(question: str, statistic: Optional[str]) -> None:
    pass
    text = _scope_assertion_text(question)
    if not (
        any(token in text for token in ("中位数", "中位值", "中间位置", "正中间"))
        or re.search(r"(?<![A-Za-z])median(?![A-Za-z])", text, re.I)
    ):
        return
    mean_negated = re.search(r"(?:不需要|不要|无需|不用)(?:再)?(?:均值|均分|平均分|平均值)", text)
    also_mean = not mean_negated and any(
        token in text for token in ("均值", "均分", "平均分", "平均值")
    )
    expected = "mean_and_median" if also_mean else "median"
    if statistic != expected:
        raise ValueError(f"experiment_explicit_statistic_mismatch:{expected}")

def _school_dimension_extrema_request(question: str) -> bool:
    pass
    text = _scope_assertion_text(question)
    return bool(
        re.search(r"六维|六大维度|雷达图", text)
        and re.search(r"最高|最大", text)
        and re.search(r"最低|最小", text)
        and re.search(r"维度", text)
    )

def _explicit_item_statistic(question: str) -> Optional[str]:
    pass
    text = _scope_assertion_text(question)
    median_requested = bool(
        re.search(r"中位数|中位值|中间位置|正中间|(?<![A-Za-z])median(?![A-Za-z])", text, re.I)
    )
    mean_negated = bool(
        re.search(r"(?:不需要|不要|无需|不用)(?:再)?(?:均值|均分|平均分|平均值)", text)
    )
    mean_requested = not mean_negated and bool(
        re.search(
            r"均值|均分|平均(?:值|数|分)?|(?<![A-Za-z])(?:mean|average)(?![A-Za-z])",
            text,
            re.I,
        )
    )
    if median_requested:
        return "mean_and_median" if mean_requested else "median"

    max_requested = bool(re.search(r"最高|最大|(?<![A-Za-z])max(?![A-Za-z])", text, re.I))
    min_requested = bool(re.search(r"最低|最小|(?<![A-Za-z])min(?![A-Za-z])", text, re.I))
    if max_requested or min_requested:
        return "max" if max_requested else "min"
    if mean_requested:
        return "mean"
    if _SCHOOL_FACT_EXPLICIT_COUNT_RE.search(text):
        return "count"
    return None

def _json_object(text: Any) -> Dict[str, Any]:
    if isinstance(text, dict):
        return dict(text)
    if isinstance(text, list):
        text = "\n".join(
            str(item.get("text") or item.get("content") or "")
            if isinstance(item, dict) else str(item or "")
            for item in text
        )
    raw = str(text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    if not raw:
        raise ValueError("experiment_parser_missing_json_object")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("experiment_parser_json_object_required") from exc
    if not isinstance(parsed, dict):
        raise ValueError("experiment_parser_json_object_required")
    return parsed

def _parser_attempt_summaries(
    detail: Dict[str, Any], *, message_variant: Optional[str] = None
) -> List[Dict[str, Any]]:
    pass
    summaries: List[Dict[str, Any]] = []
    allowed_finish_reasons = {None, "stop", "length", "content_filter", "tool_calls"}
    allowed_sources = {"content", "reasoning_json_object"}
    allowed_variants = {"primary", "empty_content_retry", "parser_repair"}
    for item in detail.get("attempts") or []:
        if not isinstance(item, dict):
            continue
        finish_reason = item.get("finish_reason")
        if finish_reason not in allowed_finish_reasons:
            finish_reason = "other"
        content_source = item.get("content_source")
        if content_source not in allowed_sources:
            content_source = "content"
        item_variant = message_variant or item.get("message_variant")
        if item_variant not in allowed_variants:
            item_variant = "primary"

        def bounded_int(value: Any) -> Optional[int]:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return None

        summaries.append({
            "max_tokens": bounded_int(item.get("max_tokens")),
            "finish_reason": finish_reason,
            "content_chars": bounded_int(item.get("content_chars")),
            "reasoning_chars": bounded_int(item.get("reasoning_chars")),
            "content_source": content_source,
            "message_variant": item_variant,
        })
    return summaries

def _safe_parser_failure_code(value: Any) -> str:
    pass
    text = str(value or "").strip()
    match = re.match(r"^(experiment_[a-z0-9_]+)", text)
    return match.group(1) if match else "experiment_parser_candidate_rejected"

def _safe_gateway_failure_code(value: Any) -> str:
    pass
    text = str(value or "").lower()
    error_type = type(value).__name__.lower()
    cause = getattr(value, "cause", None)
    cause_type = type(cause).__name__.lower() if cause is not None else ""
    status = getattr(cause, "code", None) or getattr(cause, "status_code", None)
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = None
    if "unavailable" in error_type or "bound_profile" in text:
        return "experiment_parser_gateway_profile_unavailable"
    if (
        "connect" in error_type
        or "connect" in cause_type
        or "无法连接" in text
        or "connection refused" in text
    ):
        return "experiment_parser_gateway_connect_failed"
    if "timeout" in error_type or "timeout" in cause_type or "timed out" in text or "超时" in text:
        return "experiment_parser_gateway_timeout"
    if status == 429 or "rate_limit" in text or "限流" in text:
        return "experiment_parser_gateway_rate_limited"
    if status in {401, 403} or re.search(r"\b(?:401|403)\b", text):
        return "experiment_parser_gateway_auth_rejected"
    if status == 404 or re.search(r"\b404\b", text):
        return "experiment_parser_gateway_endpoint_not_found"
    if (status is not None and 500 <= status <= 599) or re.search(
        r"\b(?:500|502|503|504)\b|overloaded|temporarily unavailable|bad gateway",
        text,
    ):
        return "experiment_parser_gateway_upstream_unavailable"
    if "transient" in error_type or "connection" in text or "连接" in text:
        return "experiment_parser_gateway_connection_interrupted"
    return "experiment_parser_gateway_failed"

def _gateway_failure_attempt(*, max_tokens: int, message_variant: str) -> Dict[str, Any]:
    pass
    return {
        "max_tokens": max(1, int(max_tokens or 0)),
        "finish_reason": "other",
        "content_chars": 0,
        "reasoning_chars": 0,
        "content_source": "content",
        "message_variant": message_variant,
    }

_PARSER_REPAIRABLE_FAILURES = {
    "experiment_parser_candidate_rejected",
    "experiment_parser_evidence_invalid",
    "experiment_parser_integer_field_invalid",
    "experiment_parser_intent_not_supported",
    "experiment_parser_json_object_required",
    "experiment_parser_missing_json_object",
    "experiment_parser_period_invalid",
    "experiment_parser_requested_output_not_supported",
    "experiment_parser_requested_outputs_invalid",
    "experiment_parser_school_filter_invalid",
    "experiment_parser_unknown_evidence_fields",
    "experiment_parser_unknown_fields",
    "experiment_school_fact_requires_one_question_id",
    "experiment_compound_metric_not_in_catalogue",
    "experiment_school_field_not_in_catalogue",
    "experiment_dimension_not_in_catalogue",
    "experiment_statistic_not_supported",
    "experiment_aggregation_not_supported",
    "experiment_value_view_not_supported",
    "experiment_school_fact_scope_incomplete",
    "experiment_school_evaluation_scope_incomplete",
    "experiment_school_compare_scope_incomplete",
    "experiment_region_compare_scope_incomplete",
    "experiment_region_longitudinal_scope_incomplete",
    "experiment_region_ranking_scope_incomplete",
    "experiment_region_ranking_target_invalid",
    "experiment_region_scope_incomplete",
}

def _bind_literal_scope_candidate(
    question: str, candidate: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    pass
    bound = dict(candidate)
    actions: List[str] = []
    text = _scope_assertion_text(question)
    years = _scope_years(text)
    school_spans = _literal_school_name_spans(text)
    region_spans = _literal_region_spans(text)
    if str(bound.get("intent") or "") == "national_ranking" and re.search(r"全国|各省|省级地区", text):

        region_spans = []
    question_ids = _scope_question_ids(text)
    implicit_item = None
    implicit_items: List[Tuple[int, str]] = []
    if not question_ids and len(school_spans) == 1 and len(years) == 1 and not region_spans:
        implicit_items = _catalogue_alias_items(text)
        if len(implicit_items) in {1, 2}:
            question_ids = [item[0] for item in implicit_items]
            implicit_item = implicit_items[0] if len(implicit_items) == 1 else None
            actions.append("literal_scope:catalogue_alias_item")
    dimensions = _explicit_dimension_aliases(text)
    if not question_ids and len(school_spans) == 1 and len(years) == 1 and not region_spans:
        anchored_qid = _anchored_school_item_qid(
            question, bound, dimensions[0][1] if len(dimensions) == 1 else None
        )
        if anchored_qid is not None:
            question_ids = [anchored_qid]
            actions.append("literal_scope:anchored_model_item")
        elif pair := _management_work_count_pair(text, bound):
            question_ids = pair
            actions.append("literal_scope:catalogue_work_count_pair")
    overall = bool(_OVERALL_SCOPE_RE.search(text))
    view = _explicit_value_view(text)
    statistic = _explicit_item_statistic(text)
    if len(question_ids) == 1 and statistic == "mean":
        _, item_by_id = _catalogue()
        item_summary = str(item_by_id.get(question_ids[0], {}).get("summary") or "")
        if (
            "平均每周" in item_summary
            and re.search(r"(?:平均.{0,3}周|周.{0,3}平均)", text)
            and "课时" in text
        ):

            statistic = None
    if not question_ids and not dimensions and not overall and "常态化应用" in text and re.search(r"教师|教学", text):
        catalogue, _ = _catalogue()
        matches = [
            int(item["id"]) for item in catalogue
            if "常态化应用" in str(item.get("summary") or item.get("question") or "")
            and "教师" in str(item.get("summary") or item.get("question") or "")
        ]
        if len(matches) == 1:
            question_ids = matches
            actions.append("literal_scope:catalogue_application_metric")

    def bind(key: str, value: Any) -> None:
        if bound.get(key) != value:
            bound[key] = value
            actions.append(f"literal_scope:{key}")

    raw_evidence = bound.get("evidence")
    evidence = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
    if years:
        bind("periods", years)
        evidence["period"] = years[0]
        evidence.pop("secondary_period", None)
        if len(years) > 1:
            evidence["secondary_period"] = years[1]
    elif school_spans or region_spans:
        bind("periods", [])
        evidence.pop("period", None)
        evidence.pop("secondary_period", None)
    if school_spans:
        resolved = _explicit_school_identity(text) if len(school_spans) == 1 else None
        bind("schools", [resolved[0]] if resolved else school_spans)
        if not region_spans:
            bind("regions", [])
        for key in ("region", "region_a", "region_b", "school", "school_a", "school_b"):
            evidence.pop(key, None)
        for key, span in zip(("school",) if len(school_spans) == 1 else ("school_a", "school_b"), school_spans):
            evidence[key] = span
    if region_spans:
        bind("regions", region_spans)
        if not school_spans:
            bind("schools", [])
        for key in ("region", "region_a", "region_b"):
            evidence.pop(key, None)
        for key, span in zip(("region",) if len(region_spans) == 1 else ("region_a", "region_b"), region_spans):
            evidence[key] = span
        if not school_spans:
            school_filter = _literal_school_filter(text)
            bind("school_filter", school_filter)
            evidence.pop("school_filter", None)
            if school_filter:
                evidence["school_filter"] = school_filter
    if question_ids:
        bind("question_ids", question_ids)
        bind("dimension", None)
        bind("compound_key", None)
        bind("school_field", None)
        evidence.pop("dimension", None)
        if implicit_item is not None:
            evidence["metric"] = implicit_item[1]
    elif len(dimensions) == 1 or overall:
        bind("dimension", dimensions[0][0] if len(dimensions) == 1 else None)
        bind("question_ids", [])
        bind("compound_key", None)
        bind("school_field", None)
        if dimensions:
            evidence["dimension"] = dimensions[0][1]
        else:
            evidence.pop("dimension", None)
        evidence.pop("metric", None)

    if region_spans and not school_spans and (question_ids or dimensions or overall):
        bind("statistic", "count" if _explicit_region_school_count_only(text) else statistic or "mean")
        bind("aggregation", "answer_pooled_mean" if _explicit_answer_pooled_mean(text) else "school_equal_mean")
    if school_spans and not region_spans:
        bind("statistic", None if overall and not question_ids and _school_dimension_extrema_request(question) else statistic)
    if view in {"raw_value", "normalized_score"}:
        bind("value_view", view)
    elif question_ids and school_spans:
        bind("value_view", "raw_value")
    elif region_spans or dimensions or overall:
        bind("value_view", "normalized_score")

    rank, top_n = _explicit_ranking_target(text)
    ranking = bool(_REGION_RANKING_SCOPE_RE.search(text) or rank is not None or top_n is not None)
    complete_period = len(years) == 1
    target_scope = bool(question_ids or dimensions or overall or _explicit_region_school_count_only(text))
    new_intent = None
    if len(school_spans) == 1 and not region_spans and complete_period and target_scope:
        new_intent = "school_fact" if question_ids else "school_eval"
    elif len(school_spans) == 2 and not region_spans and complete_period and target_scope:
        new_intent = "school_compare"
    elif not school_spans and len(region_spans) == 2 and complete_period and target_scope:
        new_intent = "region_compare"
    elif not school_spans and len(region_spans) == 1 and len(years) == 2 and target_scope:
        new_intent = "region_longitudinal"
    elif not school_spans and len(region_spans) == 1 and complete_period and target_scope:
        new_intent = "region_ranking" if ranking else "region_eval"
    if new_intent and str(bound.get("intent") or "clarify") in _INTENTS:
        bind("intent", new_intent)
        bind("clarify_reason", None)
        bind("missing", [])
        bind("target_rank", rank if new_intent == "region_ranking" else None)
        bind("target_top_n", top_n if new_intent == "region_ranking" else None)
        if new_intent != "school_fact" and view != "raw_value":
            bind("value_view", "normalized_score")
        if new_intent == "region_ranking":
            bind("statistic", "mean")
    if (
        len(school_spans) == 1 and not region_spans and complete_period
        and not question_ids and _ambiguous_device_category(text)
    ):
        for key, value in {
            "intent": "clarify", "dimension": None, "question_ids": [],
            "compound_key": None, "school_field": None, "statistic": None,
            "aggregation": None, "school_filter": None, "value_view": "raw_value",
            "target_rank": None, "target_top_n": None,
            "clarify_reason": "device_category_required", "missing": ["device_category"],
        }.items():
            bind(key, value)
        for key in ("dimension", "metric", "statistic", "aggregation", "school_filter", "target"):
            evidence.pop(key, None)
    if _explicit_bare_region_school_population_count(text) and not question_ids and not (
        len(region_spans) == 1 and len(years) == 1 and not school_spans
    ):
        for key, value in {
            "intent": "clarify", "regions": region_spans, "schools": school_spans, "periods": years,
            "dimension": None, "question_ids": [], "compound_key": None, "school_field": None,
            "statistic": None, "aggregation": None, "school_filter": None,
            "target_rank": None, "target_top_n": None, "value_view": None,
            "clarify_reason": "scope_reference_not_unique",
            "missing": (["region"] if not region_spans and not school_spans else [])
            + (["period"] if not years else []) + (["scope"] if len(region_spans) > 1 or len(years) > 1 or school_spans else []),
        }.items():
            bind(key, value)
        evidence = {
            key: value for key, value in evidence.items()
            if key in {"region", "region_a", "region_b", "school", "period", "secondary_period"}
        }
    scope_evidence_fields = {
        "region", "region_a", "region_b", "school", "school_a", "school_b",
        "period", "secondary_period",
    }

    bound["evidence"] = {
        key: value for key, value in evidence.items()
        if key not in scope_evidence_fields or (
            isinstance(value, str) and bool(value.strip())
            and value in question and value in text
        )
    }
    return bound, actions

def _ambiguous_device_category(question: str) -> bool:
    text = _scope_assertion_text(question)
    return bool(
        re.search(r"学生|教师|老师", text)
        and re.search(r"配(?:备)?|统一配备", text)
        and re.search(r"电脑|终端|设备", text)
        and re.search(r"多少|几台|数量|台数", text)
        and not re.search(r"台式机|台式电脑|笔记本|平板|移动终端|合计|总量|生机比|师机比", text)
    )

def _pending_device_candidate(
    question: str, candidate: Dict[str, Any], context: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    pass
    text = _scope_assertion_text(question)
    if (
        context.get("pending_clarification_reason") != "device_category_required"
        or _scope_years(text) or _literal_region_spans(text)
        or _literal_school_name_spans(text) or _scope_question_ids(text)
        or re.search(r"新任务|另起|换个|比较|对比|改查|改看", text)
    ):
        return None
    school = str(context.get("last_school") or "").strip()
    year = str(context.get("last_year") or "").strip()
    actors = [name for name, pattern in (("学生", r"学生"), ("教师", r"教师|老师")) if re.search(pattern, text)]
    actor = actors[0] if len(actors) == 1 else None
    categories = [name for name in ("台式机", "移动终端", "笔记本", "平板") if name in text]
    if not school or not re.fullmatch(r"20\d{2}", year) or actor is None or len(categories) != 1:
        return None
    catalogue, _ = _catalogue()
    matches = [
        int(item["id"]) for item in catalogue
        if f"学校统一配备的{actor}用" in str(item.get("question") or "")
        and categories[0] in str(item.get("question") or "")
    ]
    if len(matches) != 1:
        return None
    patched = dict(candidate)
    patched.update({
        "intent": "school_fact", "schools": [school], "regions": [], "periods": [year],
        "dimension": None, "question_ids": matches, "compound_key": None, "school_field": None,
        "statistic": _explicit_item_statistic(text), "aggregation": "school_equal_mean",
        "school_filter": None, "value_view": _explicit_value_view(text) or "raw_value",
        "target_rank": None, "target_top_n": None, "clarify_reason": None, "missing": [],
        "evidence": {"metric": categories[0]},
    })
    return patched

def _comparison_difference_requested(question: str) -> bool:
    text = _scope_assertion_text(question)
    regions, schools = _literal_region_spans(text), _literal_school_name_spans(text)
    objects = regions if len(regions)==2 else schools if len(schools)==2 else []
    named = bool(objects and any(re.search(re.escape(a)+r'\s*(?:减去|减|[-−])\s*'+re.escape(b),text)
                                 for a,b in (objects,list(reversed(objects)))))
    return bool(
        (named or re.search(r"分差|差值|差额|之差|差多少|相差|(?:前|后)(?:者)?减(?:前|后)(?:者)?|(?:高|低|多|少|领先|落后)[+-]?\d+(?:\.\d+)?分", text))
        and (len(regions) == 2 or len(schools) == 2
             or len(_explicit_dimension_pair(text)) == 2)
    )

def _normalize_requested_outputs(question: str, value: Any) -> Tuple[Any, List[str]]:
    pass
    if value is None:
        return value, []
    actions: List[str] = []
    if isinstance(value, str):
        items = [value]
        actions.append("listify:requested_outputs")
    elif isinstance(value, list):
        items = value
    elif isinstance(value, dict) and all(type(enabled) is bool for enabled in value.values()):
        items = list(value)
        actions.append("flags:requested_outputs")
    else:
        raise ValueError("experiment_parser_requested_outputs_invalid")
    formats = {"chart": "charts", "tables": "table"}
    text = _scope_assertion_text(question)
    facts = {
        "valid_school_count": bool(re.search(r"有效学校数|学校分母|多少所学校|几所学校", text)),
        "school_count": bool(re.search(r"有效学校数|学校分母|多少所学校|几所学校", text)),
        "difference": _comparison_difference_requested(text),
        "score_difference": _comparison_difference_requested(text),
    }
    if not requirement_preservation_enabled():
        facts = dict.fromkeys(facts, True)
    outputs: List[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("experiment_parser_requested_outputs_invalid")
        token = item.strip().lower()
        if token in _OUTPUTS:
            output = token
        elif token in formats:
            output = formats[token]
        elif facts.get(token) is True:
            output = "direct_answer"
        else:
            raise ValueError("experiment_parser_requested_output_not_supported")
        if isinstance(value, dict) and value[item] is False:
            continue
        if output != item:
            actions.append(f"output:{item}->{output}")
        if output not in outputs:
            outputs.append(output)
    return outputs, actions

def _normalize_model_candidate(
    question: str, candidate: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    pass
    original_question = question
    question = _scope_assertion_text(question)
    preserve = requirement_preservation_enabled()
    normalized = dict(candidate)
    actions: List[str] = []
    aliases = {
        "region": "regions",
        "school": "schools",
        "year": "periods",
        "period": "periods",
        "question_id": "question_ids",
        "target_dimension": "dimension",
        "target_stat": "statistic",
    }
    for alias, canonical in aliases.items():
        if alias not in normalized:
            continue
        if canonical not in normalized or normalized.get(canonical) in (None, "", []):
            normalized[canonical] = normalized.get(alias)
            actions.append(f"alias:{alias}->{canonical}")
        normalized.pop(alias, None)

    if "requested_outputs" in normalized:
        outputs, output_actions = _normalize_requested_outputs(question, normalized["requested_outputs"])
        normalized["requested_outputs"] = outputs
        actions.extend(output_actions)

    school_filter = normalized.get("school_filter")
    if isinstance(school_filter, dict):
        filter_keys = {"登记类型", "城乡", "school_area_type", "area_type", "urban_rural"}
        literal = _literal_school_filter(question)
        filter_aliases = {"rural": "乡村", "农村": "乡村", "urban": "城镇或城区"}
        if len(school_filter) != 1 or not set(school_filter) <= filter_keys:
            raise ValueError("experiment_parser_school_filter_invalid")
        filter_value = next(iter(school_filter.values()))
        if not preserve and isinstance(filter_value, str):
            literal = filter_aliases.get(filter_value, filter_value)
        if not isinstance(filter_value, str) or not literal or (
            filter_aliases.get(filter_value, filter_value) != filter_aliases.get(literal, literal)
        ):
            raise ValueError("experiment_parser_school_filter_invalid")
        normalized["school_filter"] = literal
        actions.append("normalize:literal_school_filter_object")

    for key in ("regions", "schools", "periods", "question_ids", "missing"):
        value = normalized.get(key)
        if value is not None and not isinstance(value, list):
            normalized[key] = [value]
            actions.append(f"listify:{key}")

    null_tokens = {"", "null", "none", "无", "未指定", "不适用"}
    for key in ("dimension", "statistic", "school_filter", "compound_key", "school_field",
                "target_rank", "target_top_n", "clarify_reason"):
        value = normalized.get(key)
        if isinstance(value, str) and value.strip().lower() in null_tokens:
            normalized[key] = None
            actions.append(f"null:{key}")

    dimension = str(normalized.get("dimension") or "").strip()
    if dimension in {"总体", "整体", "综合", "全部", "全维度"} and (not preserve or _OVERALL_SCOPE_RE.search(question)):
        normalized["dimension"] = None
        actions.append("overall_dimension->null")
    statistic_aliases = {
        "均值": "mean", "平均": "mean", "平均值": "mean", "average": "mean",
        "中位数": "median", "数量": "count", "计数": "count",
    }
    statistic = str(normalized.get("statistic") or "").strip().lower()
    if statistic in statistic_aliases:
        normalized["statistic"] = statistic_aliases[statistic]
        actions.append("normalize:statistic")
    aggregation_aliases = {
        "学校等权": "school_equal_mean", "学校等权均值": "school_equal_mean",
        "按学校平均": "school_equal_mean", "作答池化": "answer_pooled_mean",
    }
    aggregation = str(normalized.get("aggregation") or "").strip()
    if aggregation in aggregation_aliases:
        normalized["aggregation"] = aggregation_aliases[aggregation]
        actions.append("normalize:aggregation")
    if normalized.get("value_view") == "normalized_value":
        normalized["value_view"] = "normalized_score"
        actions.append("normalize:value_view")

    if not preserve:
        from packages.semantic_query.semantic_preflight import constrain
        shared_catalogue, _ = _catalogue()
        _, shared_semantics = constrain(original_question, candidate, normalized, shared_catalogue)
        return normalized, {"applied": bool(actions), "actions": actions, "shared_semantics": shared_semantics}

    intent = str(normalized.get("intent") or "clarify").strip()
    item_statistic = _explicit_item_statistic(question) if intent == "school_fact" else None
    explicit_statistic = bool(
        item_statistic
        or (
            _SCHOOL_FACT_EXPLICIT_STATISTIC_RE
            if intent == "school_fact"
            else _EXPLICIT_STATISTIC_RE
        ).search(question)
    )
    if (
        intent in {"school_eval", "school_fact"}
        and not explicit_statistic
        and normalized.get("statistic") is not None
    ):
        normalized["statistic"] = None
        actions.append(f"{intent}_implicit_statistic->null")

    from packages.semantic_query.catalogue import extract_explicit_question_ids

    explicit_ids = _scope_question_ids(question)
    if explicit_ids:
        if normalized.get("question_ids") != explicit_ids:
            normalized["question_ids"] = explicit_ids
            actions.append("bind:explicit_question_ids")
        regions = _string_list(normalized.get("regions"))
        schools = _string_list(normalized.get("schools"))
        periods = _string_list(normalized.get("periods"))
        explicit_school = _explicit_school_identity(question)
        explicit_school_bound = False
        explicit_years: List[str] = []
        for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", str(question or "")):
            if year not in explicit_years:
                explicit_years.append(year)
        if (
            explicit_school is not None
            and len(explicit_ids) == 1
            and len(explicit_years) == 1
            and intent in {"clarify", "region_eval", "school_eval", "school_fact"}
            and not _N2_SCOPE_CONFLICT_RE.search(str(question or ""))
        ):

            if periods != explicit_years:
                normalized["periods"] = explicit_years
                actions.append("bind:explicit_school_item_period")
            periods = explicit_years
        if (
            explicit_school is not None
            and len(periods) == 1
            and intent in {"clarify", "region_eval", "school_eval", "school_fact"}
        ):
            normalized["schools"] = [explicit_school[0]]
            normalized["regions"] = []
            normalized["intent"] = "school_fact"
            actions.append("bind:explicit_school_entity")
            evidence = dict(normalized.get("evidence") or {})
            evidence["school"] = explicit_school[1]
            normalized["evidence"] = evidence
            schools = [explicit_school[0]]
            regions = []
            intent = "school_fact"
            explicit_school_bound = True
            item_statistic = _explicit_item_statistic(question)
            if item_statistic is None and normalized.get("statistic") is not None:
                normalized["statistic"] = None
                actions.append("school_fact_implicit_statistic->null")
        if not explicit_school_bound and len(regions) == 1 and not schools and len(periods) == 1 and intent in {"clarify", "region_eval"}:
            normalized["intent"] = "region_eval"
            actions.append("intent:explicit_region_item")
        elif not explicit_school_bound and len(schools) == 1 and not regions and len(periods) == 1 and intent in {"clarify", "region_eval", "school_eval", "school_fact"}:
            normalized["intent"] = "school_fact"
            actions.append("intent:explicit_school_item")
            item_statistic = _explicit_item_statistic(question)
            if item_statistic is None and normalized.get("statistic") is not None:
                normalized["statistic"] = None
                actions.append("school_fact_implicit_statistic->null")
        if _explicit_value_view(question) == "raw_value":
            normalized["value_view"] = "raw_value"
            actions.append("value_view:explicit_raw")
        elif _explicit_value_view(question) == "normalized_score":
            normalized["value_view"] = "normalized_score"
            actions.append("value_view:explicit_normalized")
        if item_statistic is not None and normalized.get("statistic") != item_statistic:
            normalized["statistic"] = item_statistic
            actions.append(f"statistic:explicit_item_{item_statistic}")
        if _explicit_answer_pooled_mean(question):
            normalized["aggregation"] = "answer_pooled_mean"
            actions.append("aggregation:explicit_item_pooled")

    if (
        str(normalized.get("intent") or "clarify").strip() == "school_eval"
        and normalized.get("value_view") != "normalized_score"
    ):

        normalized["value_view"] = "normalized_score"
        actions.append("school_eval_value_view->normalized_score")

    if (
        str(normalized.get("intent") or "clarify").strip() == "region_eval"
        and normalized.get("value_view") != "normalized_score"
        and _explicit_value_view(question) != "raw_value"
    ):

        normalized["value_view"] = "normalized_score"
        actions.append("region_eval_value_view->normalized_score")

    normalized, literal_actions = _bind_literal_scope_candidate(original_question, normalized)
    actions.extend(literal_actions)
    from packages.semantic_query.semantic_preflight import constrain
    shared_catalogue, _ = _catalogue()
    normalized, shared_semantics = constrain(original_question, candidate, normalized, shared_catalogue)
    actions.extend("shared_semantics:" + p['field'] for p in shared_semantics['patches'])
    return normalized, {"applied": bool(actions), "actions": actions, "shared_semantics": shared_semantics}

def _apply_scope_transition(
    question: str,
    candidate: Dict[str, Any],
    context: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    pass
    patched = dict(candidate)
    text = str(question or "")

    if (
        context.get("pending_clarification_reason")
        == "school_population_scope_required"
        and is_region_population_confirmation(text)
    ):
        entity = str(context.get("last_region") or "").strip()
        period = str(context.get("last_year") or "").strip()
        if entity and re.fullmatch(r"20\d{2}", period):
            requested_outputs = [
                str(item)
                for item in patched.get("requested_outputs") or []
                if str(item) in _OUTPUTS
            ] or ["direct_answer"]
            patched.update({
                "intent": "region_eval",
                "regions": [entity],
                "schools": [],
                "periods": [period],
                "dimension": None,
                "statistic": "count",
                "aggregation": "school_equal_mean",
                "school_filter": None,
                "question_ids": [],
                "value_view": "normalized_score",
                "compound_key": None,
                "school_field": None,
                "target_rank": None,
                "target_top_n": None,
                "requested_outputs": requested_outputs,
                "clarify_reason": None,
                "missing": [],
                "evidence": {},
            })
            return patched, {
                "applied": True,
                "reason": "pending_region_population_scope_confirmed",
                "inherited_fields": ["region", "period"],
                "output_focus": "school_count",
            }
        return patched, {
            "applied": False,
            "reason": "pending_region_population_scope_incomplete",
        }

    device_candidate = _pending_device_candidate(question, patched, context)
    if device_candidate is not None:
        return device_candidate, {
            "applied": True, "reason": "pending_device_category_resolved",
            "inherited_fields": ["school", "period"], "missing_field": "device_category",
        }

    source_operation = str(context.get("last_scope_operation") or "").strip()
    object_type = str(context.get("last_scope_object_type") or "").strip()
    if source_operation not in {"school_eval", "school_fact", "region_eval"}:
        return patched, {"applied": False, "reason": "incompatible_or_missing_focus"}
    if object_type not in {"school", "region"}:
        return patched, {"applied": False, "reason": "missing_focus_object_type"}

    text = _scope_assertion_text(question)
    years = _scope_years(text)
    regions = _literal_region_spans(text)
    schools = _literal_school_name_spans(text)
    if _SCOPE_TRANSITION_CONFLICT_RE.search(text) or len(years) > 1:
        return patched, {"applied": False, "reason": "explicit_scope_conflict"}
    if (
        object_type == "school" and re.search(r"(?:恢复|回到|返回)(?:区域|地区|区县)", text)
        or object_type == "region" and re.search(r"(?:恢复|回到|返回)(?:学校|单校)", text)
    ):
        return patched, {"applied": False, "reason": "focus_object_type_conflict"}

    keep_scope = bool(re.search(r"其他.{0,8}(?:不变|沿用)|仍是|不变|同一题|同年|这一年|上一次成功|上次成功|原始数量改为|换成.*记录|再回到", question))
    replacing_region = bool(object_type == "region" and len(regions) == 1 and not schools and keep_scope)
    if schools or (regions and not replacing_region):
        return patched, {"applied": False, "reason": "explicit_entity_present"}
    if years and not keep_scope:
        return patched, {"applied": False, "reason": "explicit_scope_conflict"}

    evidence = _validated_evidence(patched, text)
    candidate_entities = _string_list(patched.get("schools")) + _string_list(patched.get("regions"))
    if not replacing_region and any(
        value in text and not _is_contextual_entity_reference(value)
        and (_literal_school_name_spans(value) or _literal_region_spans(value))
        for value in candidate_entities
    ):
        return patched, {"applied": False, "reason": "explicit_entity_present"}

    explicit_ids = _scope_question_ids(text)
    alias_dimension, alias_span = _explicit_dimension_alias(text)
    overall_match = _OVERALL_SCOPE_RE.search(text)
    inherited_metric = context.get("last_school_fact_metric") or {}
    metric_key = str(context.get("last_scope_metric_key") or "")
    if len(explicit_ids) > 1:
        return patched, {"applied": False, "reason": "multi_item_outside_first_slice"}
    if explicit_ids:
        target_granularity = "item"
    elif alias_dimension:
        target_granularity = "dimension"
    elif overall_match:
        target_granularity = "overall"
    elif keep_scope or re.search(r"取消.*筛选|维度不变|同一题|上一次成功|上次成功", question):
        target_granularity = str(context.get("last_scope_granularity") or "")
        if target_granularity == "dimension":
            alias_dimension = context.get("last_dimension")
            if alias_dimension not in _DIMENSIONS:
                return patched, {"applied": False, "reason": "incomplete_focus_scope"}
        elif target_granularity == "item":
            qid = inherited_metric.get("qid")
            if qid is None and re.fullmatch(r"question:\d+", metric_key):
                qid = int(metric_key.split(":")[1])
            if qid is None:
                return patched, {"applied": False, "reason": "incomplete_focus_metric"}
            explicit_ids = [int(qid)]
        elif target_granularity != "overall":
            return patched, {"applied": False, "reason": "incomplete_focus_scope"}
    else:
        return patched, {"applied": False, "reason": "no_explicit_target_scope"}

    entity_key = "last_school" if object_type == "school" else "last_region"
    entity = regions[0] if replacing_region else str(context.get(entity_key) or "").strip()
    period = years[0] if years else str(context.get("last_year") or "").strip()
    if not entity or re.fullmatch(r"20\d{2}", period) is None:
        return patched, {"applied": False, "reason": "incomplete_focus_identity"}

    cleared_fields: List[str] = []
    changed_fields: List[str] = []

    def set_value(key: str, value: Any) -> None:
        if patched.get(key) != value:
            if patched.get(key) not in (None, "", []) and value in (None, "", []):
                cleared_fields.append(key)
            else:
                changed_fields.append(key)
            patched[key] = value

    set_value("regions", [entity] if object_type == "region" else [])
    set_value("schools", [entity] if object_type == "school" else [])
    set_value("periods", [period])
    for key in ("target_rank", "target_top_n", "compound_key", "school_field", "clarify_reason"):
        set_value(key, None)
    set_value("missing", [])
    clear_filter = bool(re.search(r"取消.{0,8}筛选|全部登记学校|全市样本", question))
    set_value("school_filter", None if clear_filter or object_type == "school" else _literal_school_filter(text) or context.get("last_school_filter"))
    explicit_view = _explicit_value_view(question)
    explicit_statistic = _explicit_item_statistic(question)
    aggregation = "answer_pooled_mean" if re.search(r"池化|pool(?:ed|ing)?", text, re.I) else "school_equal_mean"
    if target_granularity == "item":
        set_value("intent", "school_fact" if object_type == "school" else "region_eval")
        set_value("dimension", None)
        set_value("question_ids", explicit_ids)
        set_value("value_view", explicit_view or (
            "normalized_score" if object_type == "region" else
            context.get("last_value_view") if not _scope_question_ids(text) else "raw_value"
        ) or "raw_value")
        set_value("statistic", explicit_statistic or ("mean" if object_type == "region" else None))
        set_value("aggregation", aggregation)
    else:
        set_value("intent", "school_eval" if object_type == "school" else "region_eval")
        set_value("dimension", alias_dimension if target_granularity == "dimension" else None)
        set_value("question_ids", [])
        set_value("value_view", explicit_view or "normalized_score")
        target_statistic = explicit_statistic
        if object_type == "region":
            target_statistic = "count" if _explicit_region_school_count_only(text) else target_statistic
            if target_statistic is None:
                target_statistic = context.get("last_statistic") if keep_scope and not overall_match and not alias_span else "mean"
        set_value("statistic", target_statistic)
        set_value("aggregation", aggregation)

    patched_evidence = {}
    if replacing_region:
        patched_evidence["region"] = regions[0]
    if years:
        patched_evidence["period"] = years[0]
    if alias_span and target_granularity == "dimension":
        patched_evidence["dimension"] = alias_span
    if evidence.get("metric") and target_granularity == "item":
        patched_evidence["metric"] = evidence["metric"]
    patched["evidence"] = patched_evidence
    return patched, {
        "applied": True,
        "reason": "explicit_granularity_patch_on_compatible_focus",
        "object_type": object_type,
        "source_operation": source_operation,
        "source_granularity": context.get("last_scope_granularity"),
        "target_granularity": target_granularity,
        "inherited_fields": (["entity"] if not replacing_region else []) + (["period"] if not years else []),
        "cleared_fields": sorted(set(cleared_fields)),
        "changed_fields": sorted(set(changed_fields)),
    }

def _catalogue() -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    from packages.semantic_query.catalogue import load_semantic_catalogue

    catalogue = load_semantic_catalogue()
    public_rows = list(catalogue["items"])
    by_id = {int(item["id"]): item for item in public_rows}
    return public_rows, by_id

def _public_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    allowed = {
        "last_school",
        "last_school_compare_a",
        "last_school_compare_b",
        "last_school_fact_entity",
        "last_school_fact_metric",
        "last_region",
        "last_region_compare_a",
        "last_region_compare_b",
        "last_region_longitudinal_period_a",
        "last_region_longitudinal_period_b",
        "last_year",
        "last_dimension",
        "last_statistic",
        "last_aggregation",
        "last_school_filter",
        "last_value_view",
        "last_target_rank",
        "last_target_top_n",
        "last_scope_object_type",
        "last_scope_operation",
        "last_scope_granularity",
        "last_scope_metric_key",
        "pending_clarification_active",
        "pending_clarification_reason",
        "pending_original_question",
        "pending_school_fact",
    }
    source = context or {}
    return {key: source.get(key) for key in sorted(allowed) if source.get(key) is not None}

def _parser_prompt(question: str, context: Dict[str, Any], catalogue: List[Dict[str, Any]]) -> str:
    schema = {
        "intent": "clarify|region_eval|school_eval|school_fact|region_compare|school_compare|region_longitudinal|region_ranking|national_ranking",
        "regions": ["区域A", "区域B可选"],
        "schools": ["学校A", "学校B可选", "其余明确学校按原句顺序完整列出"],
        "periods": ["YYYY", "第二年份可选"],
        "dimension": "六维之一或null",
        "statistic": "mean|median|mean_and_median|count|max|min|null",
        "aggregation": "school_equal_mean|answer_pooled_mean",
        "school_filter": "学段或城乡筛选；无则null",
        "question_ids": [70],
        "value_view": "raw_value|normalized_score",
        "compound_key": "生机比|师机比|学生终端总量|教师终端总量|null",
        "school_field": "teacher_num|student_num|class_num|school_area_type|school_type|null",
        "target_rank": None,
        "target_top_n": None,
        "requested_outputs": ["direct_answer", "analysis", "table", "charts"],
        "clarify_reason": None,
        "missing": [],
        "evidence": {
            "region": "原问题中的逐字短片段或null",
            "region_a": "原问题中的逐字短片段或null",
            "region_b": "原问题中的逐字短片段或null",
            "school": "原问题中的逐字短片段或null",
            "school_a": "原问题中的逐字短片段或null",
            "school_b": "原问题中的逐字短片段或null",
            "period": "原问题中的逐字短片段或null",
            "secondary_period": "原问题中的逐字短片段或null",
            "dimension": "原问题中的逐字短片段或null",
            "statistic": "原问题中的逐字短片段或null",
            "aggregation": "原问题中的逐字短片段或null",
            "school_filter": "原问题中的逐字短片段或null",
            "metric": "原问题中的逐字短片段或null",
            "target": "原问题中的逐字短片段或null",
        },
        "confidence": 0.0,
    }
    return (
        "你是教育数字化分析系统的结构化意图解析器，只做映射，不计算、不回答问题。\n"
        "普通产品与G1/G2共用本提示、语义目录与输出结构。只可选择给定意图、六维和题号；禁止生成SQL。\n"
        "优先确定所问粒度。用户用‘那项、这一题项、某种人员覆盖比例’指一个登记题项时，即使题项主题涉及课堂或教师，也必须从目录选择唯一question_ids；学校对象使用school_fact，dimension=null。只有用户询问整维分数才使用school_eval及dimension，禁止用主题归属替代单项答案。归一值转百分制仍是该题normalized_score，不能上卷成整维。\n"
        "目录语义例：‘已经将技术变成日常教学的习惯’衡量常态应用人员覆盖；‘会不会操作技术、是否熟练’衡量技术能力，两者不是同一项。先以目录的测量对象和属性找题项，再填目录题号；否定分句只排除混淆项，不能把肯定分句映射为整维。\n"
        "只允许输出结构示例中列出的字段；额外字段会被拒绝。\n"
        "若对象、年份、比较双方、指标或用户指代无法唯一确定，intent必须为clarify并列出最小缺失信息。"
        "询问整块时，应合并原句全部肯定的测量线索核对共同目录：能力、培训、实践等多个属性若唯一归属同一维度，使用该维度，不因未写正式维度名而澄清；单项或多义描述不能上卷。\n"
        "evidence中的值必须是用户原问题里逐字出现的短片段，不得改写或补造。\n"
        "同校同年学校事实可选一题或两道不同问卷题；两题原始值或同口径标准分分别报告时按题意顺序填两个question_ids，不要拆成两次请求。复合指标或学校字段不要同时填question_ids；结合对象、值类型、时间窗口和混淆项区分近邻指标。\n"
        "学校事实默认value_view=raw_value；只有用户明确问得分或归一化分时才用normalized_score。\n"
        "单校总体、单维或题面明确两个维度的评估映射为school_eval、value_view=normalized_score，不得填question_ids，未明确另问统计量时statistic必须为null；同校两年描述性比较保留两个periods，不能因跨年拒绝；"
        "双维比较仍只填一个dimension字段（可为第一维或null），系统将从原题逐字范围绑定两个维度及题干先后次序；不得把双维改写成总体或六维，也不得把前者减后者颠倒。"
        "区域单题统计映射为region_eval、question_ids=[目录题号]。保留用户原值或标准分视图、mean/median/mean_and_median/max/min统计量、年份与筛选；数字0保留，NULL排除。并列极值、等权与池化均值、城乡两组计数会由公共执行器组合既有查询，保留所有对象与时期，不要自行丢掉一端。\n"
        "区域学校排名支持总体学校等权均值或一个题项的登记数值，一个年份、正整数精准名次、倒数第一(-1)或Top N。原值题项按所问登记数量排序，标准分按标准分排序；同数按学校编号小的在前。学校内某一维度在六维中的名次仍映射school_eval并保留目标dimension，不是区域学校排名。\n"
        "全国单年总体得分榜首映射为national_ranking；regions与schools留空，periods只填题面年份，"
        "不得把全国省域榜首映射为单个地区的学校排名。题面说省份或哪个省时只比较行政省；"
        "题面说省级地区或明确含直辖市时才包含直辖市。两者均映射为national_ranking，"
        "regions留空且school_filter留空，由执行层按原句限定范围；"
        "当前只回答总体榜首，不支持单维、学校筛选或图表。\n"
        "同年多校模板分比较完整保留schools，不能截成首尾两校；指定两校同年一个题项的原值或标准分比较使用school_compare并保留question_ids与value_view。双区域支持整体/单维的mean或count；一个区域或一所学校两个不同年份可作描述性比较。\n"
        "显著性检验、因果效果和未来预测仍不支持；单校跨年只描述两个年度登记结果，不能误当区域固定队列或因果效应。\n"
        "单年度区域评估不支持因果或归因；区域内学校排名映射为region_ranking，本校内维度名次映射为school_eval。\n"
        "单区域单年份问题中的“总体、整体、全貌、大盘、综合水平/情况”映射为region_eval且dimension=null；"
        "明确出现六维名称或下列别名时映射为region_eval及对应dimension；明确两维比较由原题顺序绑定两个维度，不得因口语化而要求补充已经明确的粒度。\n"
        "共同研究口径：学校指源数据中的年度登记ID；地区采用冻结字典的规范city与所属province同时精确匹配、按用户年度独立取样。所有显式学校类型、城乡、层级及否定限制必须保留。城区与小学等两个属性是AND组合，不能丢任何一个；校名中的地名不作为地区条件。不要用省级样本替代城市。无有效数据应说明，无值不能写0。\n"
        "目录消歧示例：询问人员使用技术的熟练程度与询问将技术作为日常习惯的人员覆盖比例，是不同登记题项；询问常态应用学科数量与常态应用教师比例也不是同一项。应依据目录的测量对象、属性和单位选择题项，不凭相似词上卷维度。\n"
        "描述性范围需结合多个线索判定：概括课堂组织和考试评价时使用教育教学；若原句询问一个登记题项（包括教师技术使用能力、常态化应用等比例题）的百分制标准分，必须按83题目录选择question_ids，不能上卷成整维，也不能把标准分解释成学生考试成绩或教师人数比例。设备、网络与学习场所属于基础设施；"
        "人员、组织机制与经费/资金支持属于保障机制。单个线索或否定/排除分句不得扩展目标维度。\n"
        "若会话上下文存在单校或单区域可信焦点，当前句只明确切换到总体、某一维度或一个题号时，"
        "把它视为范围补丁：仅继承相容的对象和年份，并清除旧维度、旧题项及不相容统计字段；不得继承比较、排名或纵向焦点。\n"
        f"六维别名：{json.dumps(_DIMENSION_ALIASES, ensure_ascii=False)}\n"
        "教师级操作仍须澄清；区域题项排名和本校六维名次按公共已实现任务执行，其他未实现排名须澄清。\n"
        "显式统计词必须映射为对应statistic，不能把中位数静默改成mean。"
        "区域总体或单维的均分/得分可以同时要求有效学校数或有效作答数；这些是结果分母字段，"
        "不是与mean竞争的第二个statistic，不得因此clarify，statistic仍为mean。\n"
        "单区域单年份明确询问‘有有效得分/数据或纳入统计的学校共有多少所’时，映射为"
        "region_eval、statistic=count、value_view=normalized_score；若只问行政区域共有多少所学校"
        "而未限定为评估有效样本，则必须clarify。count请求只回答目标范围的school_count，"
        "不得附带均分、有效作答数、六维、分布或榜单。\n"
        "若用户明确说不需要、不要、无需或不画图，requested_outputs中禁止包含charts。\n"
        "requested_outputs只表示展示形式，只能从direct_answer/analysis/table/charts选择；图表写charts，"
        "有效学校数与分差属于direct_answer内的事实，不得写成valid_school_count/difference等输出枚举。"
        "school_filter只能是字符串或null，不得输出对象；双区域顺序与题面首次出现顺序一致。\n"
        "严格只输出一个JSON对象，不要Markdown或解释。\n"
        f"输出结构示例：{json.dumps(schema, ensure_ascii=False)}\n"
        f"可用会话上下文：{json.dumps(context, ensure_ascii=False)}\n"
        f"六维目录：{json.dumps(sorted(_DIMENSIONS), ensure_ascii=False)}\n"
        f"83题目录：{json.dumps(catalogue, ensure_ascii=False, separators=(',', ':'))}\n"
        f"用户原问题：{question}"
    )

def _parser_empty_retry_prompt(
    question: str,
    context: Dict[str, Any],
    catalogue: List[Dict[str, Any]],
) -> str:
    pass
    compact_catalogue = [
        {
            "id": item.get("id"),
            "dimension": item.get("dimension"),
            "question": item.get("question"),
            "summary": item.get("summary"),
            "aliases": item.get("aliases") or [],
            "value_type": item.get("value_type"),
            "unit": item.get("unit"),
            "coding_status": item.get("coding_status"),
        }
        for item in catalogue
    ]
    skeleton = {
        "intent": "clarify",
        "regions": [],
        "schools": [],
        "periods": [],
        "dimension": None,
        "statistic": None,
        "aggregation": "school_equal_mean",
        "school_filter": None,
        "question_ids": [],
        "value_view": "raw_value",
        "compound_key": None,
        "school_field": None,
        "target_rank": None,
        "target_top_n": None,
        "requested_outputs": ["direct_answer"],
        "clarify_reason": None,
        "missing": [],
        "confidence": 0.0,
    }
    return (
        "上一响应没有可用正文。不要解释、不要思考过程、不要Markdown；只输出一个JSON对象。\n"
        "字段必须与骨架完全一致，不得添加evidence或任何额外字段。对象/年份/指标不唯一则intent=clarify。\n"
        "intent仅限clarify/region_eval/school_eval/school_fact/region_compare/"
        "school_compare/region_longitudinal/region_ranking/national_ranking；dimension仅限六维或null。\n"
        "单校总体/单维用school_eval；学校单题用school_fact；区域单题只支持唯一题号的"
        "normalized_score+mean+school_equal_mean。会话短追问可使用给定上下文。\n"
        "requested_outputs必须是字符串数组，元素只允许direct_answer/analysis/table/charts；"
        "画图用charts，有效学校数与分差属于direct_answer中的必答事实，不是独立输出枚举。\n"
        "school_filter必须是题面中学段/城乡筛选的字符串或null，不能写对象。"
        "双区域比较按题面城市顺序填写regions；指定单维时填写dimension、normalized_score、"
        "mean和school_equal_mean，有效学校数不把mean改成count。\n"
        f"JSON骨架：{json.dumps(skeleton, ensure_ascii=False, separators=(',', ':'))}\n"
        f"会话上下文：{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}\n"
        f"精简83题目录：{json.dumps(compact_catalogue, ensure_ascii=False, separators=(',', ':'))}\n"
        f"用户原问题：{question}"
    )

def _parser_repair_prompt(
    question: str,
    context: Dict[str, Any],
    catalogue: List[Dict[str, Any]],
    failure_code: str,
) -> str:
    pass
    compact = _parser_empty_retry_prompt(question, context, catalogue)
    return compact.replace(
        "上一响应没有可用正文。",
        f"上一响应未通过严格结构或范围校验（{failure_code}）。请重新映射。",
        1,
    )

def _string_list(value: Any, *, limit: Optional[int] = None) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value[:limit] if str(item).strip()]

def _pending_region_identity(token: str) -> Tuple[str, str]:
    try:
        from scripts.region_engine import detect_region

        resolved = detect_region(token)
        if resolved:
            return str(resolved.get("name") or token), str(resolved.get("full_name") or token)
    except Exception:
        pass
    return token, token

def _pending_school_identity(token: str) -> str:
    try:
        from scripts.school_engine import find_schools

        names = {
            str(row.get("school_name") or "").strip()
            for row in find_schools(token, limit=20)
            if str(row.get("school_name") or "").strip()
        }
        if len(names) == 1:
            return next(iter(names))
    except Exception:
        pass
    return token

def _literal_school_name_spans(question: str) -> List[str]:
    pass
    text = _scope_assertion_text(question)
    spans: List[str] = []
    for clause in re.split(r"20\d{2}年?|[、，,。；;！？!?：:\s]|(?<=[校学园])(?:和|与|跟|减(?:去)?)|以及", text):
        clause = re.sub(
            r"^(?:(?:请问|请|麻烦|烦请|帮我|帮忙|给我|我想|想要|想|要|"
            r"只比较|比较|对比|用图比较|用图对比|只查看|只查|只看|查看|查询|看看|看一下|看下|看|"
            r"评估|分析|了解|改查|改看|换成|恢复|回到|返回|把|在|另起一个任务|换个新任务|"
            r"(?:并|且|再|请)?(?:计算|算出|给出|列出)))+", "", clause,
        ).strip()
        match = re.match(
            r"[\u4e00-\u9fffA-Za-z·（）()]{2,}?(?:小学|中学|幼儿园|学校)"
            r"(?:[\u4e00-\u9fff]{0,12}?分校|校)?", clause,
        )
        if not match:
            continue
        span = match.group(0)
        if re.fullmatch(r'(?:乡村|农村|城区|城镇|城市|两地|各地|两组)(?:小学|中学|学校)', span) or re.search(r'整体|总体|最高|最低|等权|同数时|同分时', span):
            continue

        if span.endswith("学校") and re.match(
            r"(?:等权|分母|(?:总)?数(?=量|目|[，,。；;！？!?\s]|$|是|有|为|共|分|各|多|几|较|更|高|低|大|小|呢|吗|变|增|减|差))",
            clause[match.end():],
        ):
            continue
        if (
            _is_contextual_entity_reference(span)
            or re.search(r"的|多少|几所|每所|各校|所有|全部|有效|样本|登记为|纳入|参与|覆盖|依据|同数时|同分时|学校编号|所学校$|哪边学校$", span)
            or span.endswith("按学校")
            or span in {"乡村学校", "农村学校", "城镇学校", "城市学校", "城区学校", "公办学校", "民办学校", "学校等权"}
            or re.fullmatch(r"[\u4e00-\u9fff]{2,8}(?:省|市|县|区)学校", span)
        ):
            continue
        if span not in spans:
            spans.append(span)
    return spans

def _explicit_school_identity(question: str) -> Optional[Tuple[str, str]]:
    pass
    spans = _literal_school_name_spans(question)
    if len(spans) != 1:
        return None
    try:
        from scripts.school_engine import find_schools

        names = {
            str(row.get("school_name") or "").strip()
            for row in find_schools(spans[0], limit=20)
            if str(row.get("school_name") or "").strip()
        }
    except Exception:
        return None
    if len(names) != 1:
        return None
    return next(iter(names)), spans[0]

def _single_literal_school_span(question: str) -> Optional[str]:
    pass
    spans = _literal_school_name_spans(question)
    return spans[0] if len(spans) == 1 else None

def preflight_school_fact_statistic_boundary(
    question: str,
) -> Optional[Dict[str, Any]]:
    pass
    if not requirement_preservation_enabled():
        return None
    text = _scope_assertion_text(question)
    statistic = _explicit_item_statistic(text)
    if (
        statistic is None
        or _N2_SCOPE_CONFLICT_RE.search(text)
        or re.search(r"恢复|回滚|撤销|取消|(?<![A-Za-z])revision(?![A-Za-z])", text, re.I)
    ):
        return None

    years: List[str] = []
    for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text):
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None

    from packages.semantic_query.catalogue import extract_explicit_question_ids

    question_ids = _scope_question_ids(text)
    if len(question_ids) != 1:
        return None
    school_span = _single_literal_school_span(text)
    if school_span is None:
        return None

    guard = {
        "version": "school-fact-statistic-preflight-v1",
        "reason": "unsupported_school_fact_statistic",
        "matched": True,
        "model_parser_invoked": False,
        "fact_execution_started": False,
        "fact_tool_calls": 0,
        "contract_prepared": False,
        "pending_opened": False,
        "revision_committed": False,
    }
    return {
        "intent": "clarify",
        "reason": "unsupported_school_fact_statistic",
        "missing": [
            "单校单题事实当前只返回该校该年的直接值，不支持另加均值、中位数、计数或极值统计。"
        ],
        "original": str(question or ""),
        "school": school_span,
        "year": years[0],
        "question_ids": question_ids,
        "statistic": statistic,
        "pre_execution_guard": guard,
    }

def preflight_semantic_scope_boundary(
    question: str,
) -> Optional[Dict[str, Any]]:
    pass
    if not requirement_preservation_enabled():
        return None
    text = _scope_assertion_text(question)
    if re.search(r"恢复|回滚|撤销|取消|(?<![A-Za-z])revision(?![A-Za-z])", text, re.I):
        return None

    def boundary(
        reason: str,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        guard = {
            "version": "semantic-scope-boundary-preflight-v1",
            "reason": reason,
            "matched": True,
            "model_parser_invoked": False,
            "fact_execution_started": False,
            "fact_tool_calls": 0,
            "contract_prepared": False,
            "pending_opened": False,
            "revision_committed": False,
        }
        payload: Dict[str, Any] = {
            "intent": "clarify",
            "reason": reason,
            "missing": [message],
            "original": str(question or ""),
            "pre_execution_guard": guard,
        }
        if details:
            payload.update(details)
        return payload

    from packages.semantic_query.public_tasks import route_public_task
    import sys
    if route_public_task(question, {}, sys.modules[__name__]) is not None:
        return None
    question_ids = _scope_question_ids(text)
    school_spans = _literal_school_name_spans(text)
    region_spans = _literal_region_spans(text)
    years = _scope_years(text)
    if (len(school_spans) == 1 and not region_spans
            and re.search(r"(?:教师|老师)(?:用)?[、和与]学生(?:用)?台式机|学生(?:用)?[、和与](?:教师|老师)(?:用)?台式机", text)
            and re.search(r"比例|占比|百分比|比值", text) and "除以" not in text):
        return boundary(
            "school_device_ratio_order_required",
            "请明确哪一类台式机数量除以哪一类，以及需要倍数还是百分比。",
        )
    if len(region_spans) == 1 and len(years) == 1 and not school_spans and not question_ids:
        if re.search(r"最高|最大", text) and re.search(r"最低|最小", text):
            return boundary(
                "unsupported_region_statistic_set",
                "当前区域评估一次只支持一个极值统计量，不能把最高与最低两端压成其中一端。",
                details={"target_statistics": ["max", "min"], "target_operation": "region_eval"},
            )
        if re.search(r"(?:每所学校|每校|学校等权).{0,35}(?:每条|每条有效作答|作答池化)", text):
            return boundary(
                "unsupported_region_aggregation_set",
                "当前区域评估一次只支持一种均值口径，不能把学校等权与作答池化并列请求压成一种。",
                details={"target_aggregations": ["school_equal_mean", "answer_pooled_mean"], "target_statistic": "mean"},
            )
        if re.search(r"乡村|农村", text) and re.search(r"城镇|城区|非乡村|非农村", text) and re.search(r"各有多少所|各(?:有|是)多少所学校|两组.*学校数", text):
            return boundary(
                "unsupported_region_filter_group_comparison",
                "当前区域评估一次只支持一组学校筛选，不能把城乡两组样本数压成单组结果。",
                details={"target_school_filters": ["乡村", "城镇或城区"], "target_statistic": "count"},
            )
    if any(qid not in range(1, 84) for qid in question_ids):
        return boundary("question_id_not_in_catalogue", "题号必须位于当前可信语义目录的第1至83题范围内。")
    if school_spans and region_spans and _explicit_value_view(text) == "mixed_raw_and_normalized_score":
        return boundary("unsupported_mixed_value_view_comparison", "学校原始登记值与区域标准化均分口径不同，不能直接相减。")
    if len(school_spans) == 2 and question_ids:
        return boundary("unsupported_school_compare_fact_metric", "当前双校比较只支持学校模板，不支持题项事实值比较。")
    pair_scope = (
        len(question_ids) == 2 and len(set(question_ids)) == 2
        and len(school_spans) == 1 and not region_spans
        and len(_scope_years(text)) == 1
        and set(question_ids) != {20, 82}
    )
    if len(question_ids) > 1 and not pair_scope:
        message = "当前题项首片一次只支持一个题号，不能分别执行或合成题集。"
        if school_spans and question_ids == [20, 82]:
            message = (
                "第20题（基础设施：无线网络覆盖情况）和第82题（保障机制：是否制定数字化制度）"
                "属于不同维度；当前跨维学校题集暂不支持一次读取两个原始编码，"
                "请指定其中一题。未读取或合成任何编码。"
            )
        return boundary(
            "unsupported_school_item_set" if school_spans else "unsupported_region_item_set",
            message,
            details={"question_ids": question_ids},
        )
    dimensions = _explicit_dimension_aliases(text)
    is_pair_comp = bool(_explicit_dimension_pair(question)) and len(years) == 1 and (
        (len(school_spans) == 1 and not region_spans)
        or (len(region_spans) == 1 and not school_spans)

        or (not region_spans and not any(not _is_contextual_entity_reference(s) for s in school_spans)
            and re.search(r"该校|这所学校|那所学校|前者(?:学校)?|后者(?:学校)?", text))
    )
    if len(dimensions) > 1 and not is_pair_comp:
        return boundary(
            "unsupported_dimension_set",
            "当前一次评估只支持总体或一个六维范围；请一次指定一个维度。",
            details={"dimensions": [item[0] for item in dimensions]},
        )
    if dimensions and (_REGION_RANKING_SCOPE_RE.search(text) or _REGION_RANKING_TARGET_RE.search(text)) and not school_spans:
        return boundary("unsupported_region_ranking_dimension", "区域排名首片只支持总体分，不支持单维排名。")

    from packages.semantic_query.catalogue import extract_explicit_question_ids

    question_ids = _scope_question_ids(text)
    years = list(dict.fromkeys(re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)))
    item_statistic = _explicit_item_statistic(text)
    if (
        len(question_ids) == 1
        and len(years) == 1
        and item_statistic in {"median", "mean_and_median", "count", "max", "min"}
        and not school_spans
        and bool(region_spans)
    ):
        return boundary(
            "unsupported_region_item_statistic_or_aggregation",
            "区域单题首片仅支持该题有效学校的学校等权归一得分均值。",
            details={
                "question_ids": question_ids,
                "year": years[0],
                "statistic": item_statistic,
            },
        )

    sample_count = bool(
        (
            _REGION_SCHOOL_COUNT_REQUEST_RE.search(text)
            or re.search(r"学校数", text)
        )
        and _REGION_VALID_SCORE_POPULATION_RE.search(text)
        and not _REGION_SCORE_REQUEST_RE.search(text)
        and not _REGION_NON_MEAN_STATISTIC_RE.search(text)
    )
    ranking = bool(
        _REGION_RANKING_SCOPE_RE.search(text)
        or _REGION_RANKING_TARGET_RE.search(text)
    )
    comparison = bool(re.search(r"比较|对比|相比|差异|差距", text))
    if sample_count and not ranking and (comparison or len(years) >= 2) and not _explicit_scoped_population_change(text):
        if comparison and len(years) >= 2:
            reason = "unsupported_region_sample_count_cross_scope"
            message = "当前有效学校数一次只支持一个区域和一个年份，不支持跨区域且跨年份的组合查询。"
        elif comparison:
            reason = "unsupported_region_sample_count_comparison"
            message = "当前有效学校数一次只支持一个区域和一个年份，不支持多区域比较。"
        else:
            reason = "unsupported_region_sample_count_longitudinal"
            message = "当前有效学校数一次只支持一个区域和一个年份，不支持多年份并列或纵向查询。"
        return boundary(reason, message, details={"years": years})

    return None

def _integer_or_none(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("experiment_parser_integer_field_invalid")

def _validate_candidate_schema(
    candidate: Dict[str, Any], *, allow_evidence: bool = True, validate_outputs: bool = True
) -> None:
    pass
    allowed_fields = _TASKDRAFT_FIELDS if allow_evidence else (_TASKDRAFT_FIELDS - {"evidence"})
    if set(candidate) - allowed_fields:
        raise ValueError("experiment_parser_unknown_fields")
    evidence = candidate.get("evidence")
    if evidence is not None:
        if not isinstance(evidence, dict):
            raise ValueError("experiment_parser_evidence_invalid")
        if set(evidence) - _EVIDENCE_FIELDS:
            raise ValueError("experiment_parser_unknown_evidence_fields")
    requested_outputs = candidate.get("requested_outputs")
    if validate_outputs and requested_outputs is not None:
        if not isinstance(requested_outputs, list):
            raise ValueError("experiment_parser_requested_outputs_invalid")
        if any(str(item) not in _OUTPUTS for item in requested_outputs):
            raise ValueError("experiment_parser_requested_output_not_supported")

def _unsupported_scope_clarification(question: str, intent: str) -> Optional[Dict[str, Any]]:
    pass
    text = _scope_assertion_text(question)
    if _TEACHER_OPERATION_SCOPE_RE.search(text) or _SCHOOL_RANKING_SCOPE_RE.search(text):
        return {
            "intent": "clarify",
            "reason": "legacy_boundary",
            "missing": ["该请求不在P4共同实验首片支持的operation中，请使用默认兼容路径或改用已支持任务。"],
            "original": text,
        }
    if intent in {"region_ranking", "region_eval"} and _REGION_RANKING_SCOPE_RE.search(text):
        for reason, pattern, message in _REGION_RANKING_UNSUPPORTED_INTENT_PATTERNS:
            if pattern.search(text):
                return {
                    "intent": "clarify",
                    "reason": reason,
                    "missing": [message],
                    "original": text,
                }
    explicit_ranking = bool(_REGION_RANKING_SCOPE_RE.search(text) or _REGION_RANKING_TARGET_RE.search(text))
    if explicit_ranking and intent not in {"region_ranking", "national_ranking"}:
        return {
            "intent": "clarify",
            "reason": "experiment_intent_mismatch_region_ranking",
            "missing": ["原问题包含明确排名语义，但模型候选未映射为区域排名任务，请重新明确排名目标。"],
            "original": text,
        }
    calendar_years = set(re.findall(r"20\d{2}", text))
    longitudinal_scope = intent == "region_longitudinal" or (
        intent == "region_eval" and len(calendar_years) >= 2
    )
    if longitudinal_scope:
        for reason, pattern, message in _REGION_LONGITUDINAL_UNSUPPORTED_INTENT_PATTERNS:
            if pattern.search(text):
                return {
                    "intent": "clarify",
                    "reason": reason,
                    "missing": [message],
                    "original": text,
                }
    if intent == "region_eval":
        for reason, pattern, message in _REGION_EVALUATION_UNSUPPORTED_INTENT_PATTERNS:
            if pattern.search(text):
                return {
                    "intent": "clarify",
                    "reason": reason,
                    "missing": [message],
                    "original": text,
                }
    return None

def _validated_evidence(candidate: Dict[str, Any], question: str) -> Dict[str, str]:
    raw = candidate.get("evidence")
    if not isinstance(raw, dict):
        return {}
    evidence: Dict[str, str] = {}
    assertion = _scope_assertion_text(question)
    for key, value in raw.items():
        span = str(value or "").strip()
        if span and span in question and span in assertion:
            evidence[str(key)] = span
    return evidence

def _metric_from_candidate(candidate: Dict[str, Any], by_id: Dict[int, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    question_ids = candidate.get("question_ids") or []
    if question_ids:
        if not isinstance(question_ids, list) or len(question_ids) != 1:
            raise ValueError("experiment_school_fact_requires_one_question_id")
        qid = _integer_or_none(question_ids[0])
        if qid is None or qid not in by_id:
            raise ValueError("experiment_question_id_not_in_catalogue")
        item = by_id[qid]
        return {
            "type": "question",
            "qid": qid,
            "name": item["question"],
            "dimension": item.get("dimension") or "",
            "unit": item.get("unit") or "",
            "related": list(item.get("related") or []),
            "value_type": item.get("value_type") or "",
            "scale": item.get("scale") or "",
            "coding_status": item.get("coding_status") or "not_applicable",
            "coding_notes": item.get("notes") or "",
        }
    compound_key = str(candidate.get("compound_key") or "").strip()
    if compound_key:
        from packages.agent.indicator_catalog import COMPOUND_METRICS

        if compound_key not in COMPOUND_METRICS:
            raise ValueError("experiment_compound_metric_not_in_catalogue")
        return {"type": "compound", "compound_key": compound_key, **COMPOUND_METRICS[compound_key]}
    school_field = str(candidate.get("school_field") or "").strip()
    if school_field:
        fields = {
            "teacher_num": ("专任教师", "人"),
            "student_num": ("在校生", "人"),
            "class_num": ("班级数", "个"),
            "school_area_type": ("城乡类型", ""),
            "school_type": ("学段/办学类型", ""),
        }
        if school_field not in fields:
            raise ValueError("experiment_school_field_not_in_catalogue")
        name, unit = fields[school_field]
        return {"type": "school_field", "field": school_field, "name": name, "unit": unit}
    return None

def _question_pair_order_from_utterance(
    text: str, qids: List[int], by_id: Dict[int, Dict[str, Any]],
) -> Optional[List[int]]:
    pass
    work_pair = _management_work_count_pair(text, {
        "intent": "school_fact", "value_view": "raw_value", "question_ids": qids,
    })
    if work_pair:
        return work_pair
    compact = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)
    descriptions = {
        qid: [re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(value or ""))
              for value in (by_id[qid].get("question"), by_id[qid].get("summary"), *(by_id[qid].get("aliases") or []))]
        for qid in qids
    }
    positions = {}
    unresolved = []
    for qid in qids:
        other = qids[1] if qid == qids[0] else qids[0]
        candidates = []
        for phrase in descriptions[qid]:

            for size in range(len(phrase), 2, -1):
                for start in range(len(phrase) - size + 1):
                    fragment = phrase[start:start + size]
                    if any(fragment in other_phrase for other_phrase in descriptions[other]):
                        continue
                    at = compact.find(fragment)
                    if at >= 0:
                        candidates.append((-size, at))
                if candidates:
                    break
        if not candidates:
            unresolved.append(qid)
        else:
            positions[qid] = min(candidates)[1]
    if len(unresolved) == 1:

        missing = unresolved[0]
        bound = next(qid for qid in qids if qid != missing)
        orders = []
        for separator in re.finditer(r"和|与|及", compact):
            bound_left = positions[bound] < separator.start()
            other_side = compact[separator.end():] if bound_left else compact[:separator.start()]
            longest = max(
                (SequenceMatcher(None, other_side, phrase, autojunk=False).find_longest_match().size
                 for phrase in descriptions[missing] if phrase),
                default=0,
            )
            if longest >= 6:
                orders.append([bound, missing] if bound_left else [missing, bound])
        if orders and all(order == orders[0] for order in orders):
            return orders[0]
        return None
    if unresolved:
        return None
    if positions[qids[0]] == positions[qids[1]]:
        return None
    return sorted(qids, key=positions.__getitem__)

def _ordered_question_set_subtraction(
    assertion: str, qids: List[int], by_id: Dict[int, Dict[str, Any]],
) -> Optional[List[int]]:
    pass
    numbered = re.search(r"(?:第\s*(\d+)\s*题|Q\s*(\d+))\s*减\s*(?:第\s*(\d+)\s*题|Q\s*(\d+))", assertion, re.I)
    if numbered:
        ordered = [int(numbered.group(1) or numbered.group(2)), int(numbered.group(3) or numbered.group(4))]
        return ordered if set(ordered) == set(qids) else None
    indexed = re.search(
        r"(?:第一|前)(?:项|个|一个|者|一项|一题|题)?(?:登记)?(?:数(?:量|值)?|值|分(?:数)?)?"
        r"\s*减\s*(?:第二|后)(?:项|个|一个|者|一项|一题|题)?(?:登记)?(?:数(?:量|值)?|值|分(?:数)?)?",
        assertion,
    )
    if indexed:
        mentioned = _scope_question_ids(assertion)
        if len(mentioned) == 2 and set(mentioned) == set(qids):
            return mentioned
        ordered = _question_pair_order_from_utterance(assertion[:indexed.start()], qids, by_id)
        if ordered is None:
            raise ValueError("experiment_school_question_set_operand_order_unresolved")
        return ordered
    difference = re.search(r"差值|差多少|相差|(?:两项|二者|两者|前后两项).{0,8}之差", assertion)
    if difference:
        ordered = _question_pair_order_from_utterance(assertion[:difference.start()], qids, by_id)
        if ordered is None:
            raise ValueError("experiment_school_question_set_operand_order_unresolved")
        return ordered

    named = re.search(r"([^，,。；;？?]{1,30})减([^，,。；;？?]{1,30})", assertion)
    if not named:
        return None
    operands = [part.strip(" ‘’\"\t") for part in named.groups()]
    descriptions = [
        " ".join(str(by_id[qid].get(key) or "") for key in ("question", "summary"))
        + " " + " ".join(by_id[qid].get("aliases") or [])
        for qid in qids
    ]
    scores = [
        [SequenceMatcher(None, operand, description, autojunk=False).find_longest_match().size
         for description in descriptions]
        for operand in operands
    ]
    if scores[0][0] >= 3 and scores[1][1] >= 3 and scores[0][0] > scores[0][1] and scores[1][1] > scores[1][0]:
        return qids
    if scores[0][1] >= 3 and scores[1][0] >= 3 and scores[0][1] > scores[0][0] and scores[1][0] > scores[1][1]:
        return [qids[1], qids[0]]
    return None

def _school_question_set(
    candidate: Dict[str, Any], by_id: Dict[int, Dict[str, Any]], question: str,
    coverage_binding: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    raw_ids = candidate.get("question_ids") or []
    if not isinstance(raw_ids, list) or len(raw_ids) != 2:
        return None
    qids = [_integer_or_none(raw) for raw in raw_ids]
    if any(qid not in by_id for qid in qids) or qids[0] == qids[1]:
        raise ValueError("experiment_school_question_set_invalid")
    if coverage_binding is not None:
        if coverage_binding.get("source") != "original_record_coverage" or qids != coverage_binding.get("qids"):
            raise ValueError("original_record_coverage_mismatch")
        return {"type": "question_set", "name": "双题项", "qids": qids,
                "items": [_metric_from_candidate({"question_ids": [qid]}, by_id) for qid in qids],
                "derived": coverage_binding["derived"]}
    derived = None
    assertion = _scope_assertion_text(question) if requirement_preservation_enabled() else ""
    ordered = _ordered_question_set_subtraction(assertion, qids, by_id)
    if ordered is not None:
        qids = ordered
        derived = {"operation": "subtract", "left_qid": qids[0], "right_qid": qids[1]}
    elif addition := re.search(r"加起来|合计|相加|求和|总和|两项之和|两类之和", assertion):
        ordered = _question_pair_order_from_utterance(assertion[:addition.start()], qids, by_id)
        if ordered is None:
            raise ValueError("experiment_school_question_set_operand_order_unresolved")
        qids = ordered
        derived = {"operation": "add", "left_qid": qids[0], "right_qid": qids[1]}
    elif "除以" in assertion and ("百分" in assertion or "%" in assertion):
        qids = _ordered_percent_question_set(assertion, qids, by_id)
        derived = {"operation": "divide_percent", "left_qid": qids[0], "right_qid": qids[1]}
    elif "除以" in assertion:
        work_pair = _management_work_count_pair(assertion, candidate)
        qids = (_ordered_management_work_count_ratio(assertion, work_pair, by_id)
                if work_pair else _ordered_school_device_count_ratio(assertion, qids, by_id))
        derived = {"operation": "divide", "left_qid": qids[0], "right_qid": qids[1]}
    return {
        "type": "question_set", "name": "双题项", "qids": qids,
        "items": [_metric_from_candidate({"question_ids": [qid]}, by_id) for qid in qids],
        "derived": derived,
    }

def _ordered_management_work_count_ratio(
    assertion: str, qids: List[int], by_id: Dict[int, Dict[str, Any]],
) -> List[int]:
    pass
    if re.search(r"前者.{0,8}除以.{0,8}后者", assertion):
        return qids
    if re.search(r"后者.{0,8}除以.{0,8}前者", assertion):
        return qids[::-1]
    left_text, right_text = assertion.split("除以", 1)
    labels = {qid: re.search(r"实现(.+?)类教育数字化管理", by_id[qid]["question"]).group(1).replace("与", "") for qid in qids}
    def named(part: str):
        compact = part.replace("与", "")
        found = [qid for qid, label in labels.items() if label in compact]
        return found[0] if len(found) == 1 else None
    left, right = named(left_text[-18:]), named(right_text[:18])
    if left is None or right is None or left == right:
        raise ValueError("experiment_school_question_set_operand_order_unresolved")
    return [left, right]

def _ordered_percent_question_set(
    assertion: str, qids: List[int], by_id: Dict[int, Dict[str, Any]],
) -> List[int]:
    pass
    numbered = re.search(
        r"(?:第\s*(\d+)\s*题|Q\s*(\d+))\s*除以\s*"
        r"(?:第\s*(\d+)\s*题|Q\s*(\d+))", assertion, re.I,
    )
    if numbered:
        ordered = [int(numbered.group(1) or numbered.group(2)), int(numbered.group(3) or numbered.group(4))]
        if set(ordered) == set(qids):
            return ordered
        raise ValueError("experiment_school_question_set_operand_order_unresolved")
    relative = re.search(
        r"前(?:者|一个|一项|项).{0,6}除以.{0,6}后(?:者|一个|一项|项)", assertion,
    )
    if relative:
        ordered = _question_pair_order_from_utterance(assertion[:relative.start()], qids, by_id)
        if ordered is not None:
            return ordered
    raise ValueError("experiment_school_question_set_operand_order_unresolved")

def _ordered_school_device_count_ratio(
    assertion: str, qids: List[int], by_id: Dict[int, Dict[str, Any]],
) -> List[int]:
    pass
    categories = {
        "desktop": r"台式机|台式电脑",
        "laptop": r"笔记本",
        "tablet": r"平板",
        "mobile": r"移动终端",
    }
    requested = [kind for kind, pattern in categories.items() if re.search(pattern, assertion)]
    if len(requested) != 1:
        raise ValueError("experiment_school_device_ratio_category_unresolved")
    subjects = {}
    for qid in qids:
        item = by_id[qid]
        name = str(item.get("summary") or item.get("question") or "")
        matched = [kind for kind, pattern in categories.items() if re.search(pattern, name)]
        actor = item.get("subjects") or []
        if (
            item.get("value_type") != "count" or item.get("scale") != "raw_count"
            or item.get("unit") != "台" or item.get("topic") != "终端"
            or matched != requested or len(actor) != 1
            or actor[0] not in {"student", "teacher"} or actor[0] in subjects
        ):
            raise ValueError("experiment_school_device_ratio_scope_invalid")
        subjects[actor[0]] = qid
    if set(subjects) != {"student", "teacher"}:
        raise ValueError("experiment_school_device_ratio_scope_invalid")
    expression = re.search(
        r"([^，,。；;！？!?]{1,30})\s*除以\s*([^，,。；;！？!?]{1,30})", assertion,
    )
    if expression is None:
        raise ValueError("experiment_school_device_ratio_order_unresolved")
    def operand_actor(value: str) -> Optional[str]:
        actors = {"student" for _ in re.finditer(r"学生", value)}
        actors.update("teacher" for _ in re.finditer(r"教师|老师", value))
        if len(actors) != 1 or re.search(r"人数|生数|师数", value):
            return None
        if not re.search(r"台数|台式(?:机|电脑)(?:数量|数)?|电脑(?:数量|数)|终端(?:数量|数)|设备(?:数量|数)", value):
            return None
        return next(iter(actors))
    left, right = (operand_actor(part) for part in expression.groups())
    if left is None or right is None or left == right:
        raise ValueError("experiment_school_device_ratio_order_unresolved")
    return [subjects[left], subjects[right]]

def _route_from_candidate(
    question: str,
    candidate: Dict[str, Any],
    by_id: Dict[int, Dict[str, Any]],
    context: Dict[str, Any],
    *, coverage_binding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from packages.semantic_query.selective_clarification import draft_boundary
    _validate_candidate_schema(candidate)
    effective_question, effective_candidate, boundary = draft_boundary(question, candidate, context, by_id)
    if boundary is not None:
        return boundary
    routed = _route_from_candidate_impl(effective_question, effective_candidate, by_id, context, coverage_binding=coverage_binding)
    if effective_question != question and routed.get("intent") != "clarify":

        from packages.semantic_query.semantic_preflight import constrain
        _, original_boundary = constrain(question, candidate, candidate, list(by_id.values()))
        routed["atomic_requirements"] = original_boundary["requirements"]["atomic_requirements"]
        routed["original"] = question
        routed["context_resolved_question"] = effective_question
    from packages.semantic_query.selective_clarification import observations
    from packages.core.research_data import enabled as research_enabled
    if (observations() or research_enabled()) and candidate.get("intent") in {"region_eval", "region_longitudinal"}:
        routed["_selective_task_candidate"] = copy.deepcopy(candidate)
    return routed

def _route_from_candidate_impl(
    question: str,
    candidate: Dict[str, Any],
    by_id: Dict[int, Dict[str, Any]],
    context: Dict[str, Any],
    *, coverage_binding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _validate_candidate_schema(candidate)
    from packages.semantic_query.semantic_preflight import constrain
    preserve = requirement_preservation_enabled()
    _, shared_boundary = constrain(question, candidate, candidate, list(by_id.values()))
    requirements = shared_boundary['requirements']['atomic_requirements']
    if preserve and requirements['status'] == 'UNKNOWN':
        return {'intent': 'clarify', 'original': question, 'atomic_requirements': requirements,
                'reason': 'explicit_requirement_not_unique',
                'missing': ['请明确另一维度及唯一的差值操作数顺序。']}
    if preserve and shared_boundary['unsupported']:
        return {'intent':'clarify','original':question,'reason':'shared_item_selection_contract_not_supported',
                'missing':['当前共享执行支持单个极值及全部并列题项；该问题需要另外的题项选择合同。']}
    from packages.semantic_query.public_tasks import route_public_task
    import sys
    extended = route_public_task(question, candidate, sys.modules[__name__]) if preserve else None
    if extended is not None:
        extended['atomic_requirements'] = requirements
        return extended
    literal_boundary = preflight_semantic_scope_boundary(question)
    if literal_boundary is not None:

        literal_boundary.pop("pre_execution_guard", None)
        return literal_boundary
    intent = str(candidate.get("intent") or "clarify").strip()
    if intent not in _INTENTS:
        raise ValueError("experiment_parser_intent_not_supported")
    named_work_counts = _management_work_count_items(_scope_assertion_text(question)) if preserve else []
    if named_work_counts and len(_literal_school_name_spans(question)) == 1:
        candidate_items = [_integer_or_none(q) for q in _string_list(candidate.get("question_ids"))]
        if len(named_work_counts) > 2 or len(candidate_items) > 2:
            return {"intent": "clarify", "reason": "unsupported_scope_cardinality",
                    "missing": ["请限定为两个工作内容计数题项。"], "original": question}
        if len(named_work_counts) == 2 and (
            intent != "school_fact" or len(candidate_items) != 2
            or set(candidate_items) != set(named_work_counts)
        ):
            return {"intent": "clarify", "reason": "school_fact_item_scope_mismatch",
                    "missing": ["请核对两个工作内容计数题项。"], "original": question}
    unsupported_scope = _unsupported_scope_clarification(question, intent) if preserve else None
    if unsupported_scope is not None:
        unsupported_scope["original"] = question
        return unsupported_scope
    regions = [
        _pending_region_identity(token)[1]
        for token in _string_list(candidate.get("regions"))
    ]
    schools = _string_list(candidate.get("schools"))
    periods = _string_list(candidate.get("periods"))
    if len(schools) > 2 or len(regions) > 2 or len(periods) > 2 or (schools and regions):
        return {
            "intent": "clarify", "reason": "unsupported_scope_cardinality",
            "missing": ["请限定为当前支持的单对象、双对象同年或单区域两期范围。"],
            "original": question,
        }
    if preserve and intent != "clarify":
        literal_years = _scope_years(question)
        literal_schools = _literal_school_name_spans(question)
        literal_regions = _literal_region_spans(question)
        evidence = _validated_evidence(candidate, question)
        _, transition = _apply_scope_transition(question, candidate, context)

        same_item_reference = bool(
            intent == "school_fact"
            and context.get("last_scope_operation") == "school_fact"
            and context.get("last_scope_object_type") == "school"
            and context.get("last_scope_granularity") == "item"
            and re.search(r"(?:它|该校|这所学校)的(?:归一化值|原始值|原始数量)", question)
            and not re.search(r"别的|其他|另一|换|比较|对比|改成|改为", question)
            and not literal_years and not literal_schools and not literal_regions
            and schools == [str(context.get("last_school") or "")]
            and periods == [str(context.get("last_year") or "")]
            and candidate.get("question_ids") == [
                _integer_or_none(str(context.get("last_scope_metric_key") or "").removeprefix("question:"))
            ]
            and candidate.get("value_view") == _explicit_value_view(question)
            and not _SCOPE_TRANSITION_CONFLICT_RE.search(question)
        )
        compatible_focus = bool(transition.get("applied")) or same_item_reference
        typed_pending = (
            context.get("pending_clarification_reason") == "school_population_scope_required"
            and is_region_population_confirmation(question)
        ) or _pending_device_candidate(question, candidate, context) is not None
        inherited_period = bool(
            (compatible_focus or typed_pending) and periods == [str(context.get("last_year") or "")]
        )
        inherited_entity = bool(
            (compatible_focus or typed_pending) and (
                schools == [str(context.get("last_school") or "")]
                or regions == [_pending_region_identity(str(context.get("last_region") or ""))[1]]
            )
        )
        literal_entity = bool(
            (intent == "national_ranking" and re.search(r"全国|各省|省级地区", question))
            or literal_schools or literal_regions or any(
            evidence.get(key) and not _is_contextual_entity_reference(evidence[key])
            for key in ("school", "school_a", "school_b", "region", "region_a", "region_b")
        ))
        if not periods or (not literal_years and not inherited_period) or (not literal_entity and not inherited_entity):
            incomplete = {
                **candidate, "intent": "clarify", "clarify_reason": "scope_reference_not_unique",
                "missing": (["period"] if not periods or (not literal_years and not inherited_period) else [])
                + (["entity"] if not literal_entity and not inherited_entity else []),
            }
            if not literal_entity and not inherited_entity:
                incomplete.update({"schools": [], "regions": []})
            if not literal_years and not inherited_period:
                incomplete["periods"] = []
            return _route_from_candidate(question, incomplete, by_id, context)
    if any(re.fullmatch(r"20\d{2}", period) is None for period in periods):
        raise ValueError("experiment_parser_period_invalid")
    raw_question_ids = candidate.get("question_ids") or []
    invalid_question_ids = [
        raw_id
        for raw_id in raw_question_ids
        if _integer_or_none(raw_id) not in by_id
    ]
    if invalid_question_ids:
        return {
            "intent": "clarify",
            "reason": "question_id_not_in_catalogue",
            "missing": ["题号必须位于当前可信语义目录的第1至83题范围内。"],
            "original": question,
        }
    if (
        preserve and intent == "clarify" and len(raw_question_ids) == 2
        and len(schools) == 1 and len(periods) == 1 and not regions
        and _literal_school_name_spans(question)
        and _scope_years(question) == periods
        and str(candidate.get("clarify_reason") or "") in {"", "unsupported_school_item_set"}
    ):
        intent = "school_fact"
    if len(schools) == 1 and len(periods) >= 2:
        return {
            "intent": "clarify",
            "reason": "unsupported_school_longitudinal",
            "missing": ["需补充澄清：当前两期变化首片仅支持行政区域重复截面，不支持单校跨年变化或时间比较。"],
            "original": question,
        }
    if intent == "clarify":
        missing = candidate.get("missing") or []
        if not isinstance(missing, list):
            missing = [str(missing)]
        pending_context: Dict[str, Any] = {}
        pending_values: Dict[str, Any] = {}
        if len(regions) == 1 and not schools:
            region_name, region_id = _pending_region_identity(regions[0])
            pending_context["last_region"] = region_name
            pending_values["entity_id"] = region_id
        elif len(regions) == 2 and not schools:
            region_a_name, region_a_id = _pending_region_identity(regions[0])
            region_b_name, region_b_id = _pending_region_identity(regions[1])
            pending_context.update({
                "last_region_compare_a": region_a_name,
                "last_region_compare_b": region_b_name,
            })
            pending_values.update({
                "entity_id": region_a_id,
                "secondary_entity_id": region_b_id,
            })
        if len(schools) == 1 and not regions:
            school_name = _pending_school_identity(schools[0])
            pending_context["last_school"] = school_name
            pending_values["entity_id"] = school_name
        elif len(schools) == 2 and not regions:
            school_a_name = _pending_school_identity(schools[0])
            school_b_name = _pending_school_identity(schools[1])
            pending_context.update({
                "last_school_compare_a": school_a_name,
                "last_school_compare_b": school_b_name,
            })
            pending_values.update({
                "entity_id": school_a_name,
                "secondary_entity_id": school_b_name,
            })
        if periods:
            pending_context["last_year"] = periods[-1]
            pending_values["period"] = periods[0]
        if len(periods) == 2:
            pending_context.update({
                "last_region_longitudinal_period_a": periods[0],
                "last_region_longitudinal_period_b": periods[1],
            })
            pending_values["secondary_period"] = periods[1]
        dimension = str(candidate.get("dimension") or "").strip() or None
        statistic = str(candidate.get("statistic") or "").strip() or None
        aggregation = str(candidate.get("aggregation") or "").strip() or None
        school_filter = str(candidate.get("school_filter") or "").strip() or None
        if dimension in _DIMENSIONS:
            pending_context["last_dimension"] = dimension
            pending_values["dimension"] = dimension
        if statistic in _STATISTICS and statistic is not None:
            pending_context["last_statistic"] = statistic
            pending_values["statistic"] = statistic
        if aggregation in _AGGREGATIONS:
            pending_context["last_aggregation"] = aggregation
            pending_values["aggregation"] = aggregation
        if school_filter:
            pending_context["last_school_filter"] = school_filter
            pending_values["school_filter"] = school_filter
        metric = _metric_from_candidate(candidate, by_id)
        if metric is not None:
            pending_context["pending_school_fact"] = metric
        return {
            "intent": "clarify",
            "reason": str(candidate.get("clarify_reason") or "llm_requires_clarification"),
            "missing": [str(item) for item in missing if str(item).strip()] or ["请补充明确的对象、年份或指标。"],
            "original": question,
            "pending_context": pending_context,
            "pending_values": pending_values,
        }

    dimension = str(candidate.get("dimension") or "").strip() or None
    if dimension is not None and dimension not in _DIMENSIONS:
        raise ValueError("experiment_dimension_not_in_catalogue")
    statistic = str(candidate.get("statistic") or "").strip() or None
    if statistic not in _STATISTICS:
        raise ValueError("experiment_statistic_not_supported")
    if preserve:
        _validate_explicit_statistic(question, statistic)
    aggregation = str(candidate.get("aggregation") or "school_equal_mean").strip()
    if aggregation not in _AGGREGATIONS:
        raise ValueError("experiment_aggregation_not_supported")
    school_filter = str(candidate.get("school_filter") or "").strip() or None
    target_rank = _integer_or_none(candidate.get("target_rank"))
    target_top_n = _integer_or_none(candidate.get("target_top_n"))
    if intent == "region_ranking" and target_rank is None and target_top_n is None:
        return {
            "intent": "clarify",
            "reason": "missing_region_ranking_target",
            "missing": ["请指定第N名、倒数第一或Top N；当前首片不返回完整榜单。"],
            "original": question,
        }
    requested_outputs = [str(item) for item in candidate.get("requested_outputs") or []] or ["direct_answer"]
    explicit_chart_request = _explicit_chart_request(question) if preserve else None
    if explicit_chart_request is False:
        requested_outputs = [item for item in requested_outputs if item != "charts"]
    elif explicit_chart_request is True and "charts" not in requested_outputs:
        requested_outputs.append("charts")
    semantic_evidence = _validated_evidence(candidate, question)
    value_view = str(candidate.get("value_view") or "raw_value").strip()
    if value_view not in {"raw_value", "normalized_score"}:
        raise ValueError("experiment_value_view_not_supported")
    if preserve and _explicit_value_view(question) == "raw_value" and (
        intent in {"school_eval", "region_compare", "school_compare", "region_longitudinal", "region_ranking", "national_ranking"}
        or (intent == "region_eval" and not candidate.get("question_ids"))
    ):
        return {"intent": "clarify", "reason": "unsupported_evaluation_raw_value", "missing": ["当前评估与比较只支持标准化分数，不支持原始值聚合。"], "original": question}
    dimension_pair = list(requirements['execution_dimensions']) if preserve else []
    if dimension_pair and intent in {"school_eval", "region_eval"}:
        if dimension is not None and dimension not in dimension_pair:
            raise ValueError("experiment_dimension_pair_draft_mismatch")
        if candidate.get("question_ids") or target_rank is not None or target_top_n is not None:
            raise ValueError("experiment_dimension_pair_scope_mismatch")
        dimension = None
        if statistic not in {None, "mean"} or aggregation != "school_equal_mean":
            raise ValueError("experiment_dimension_pair_statistic_mismatch")
    common = {
        "atomic_requirements": requirements,
        "original": question,
        "target_dimension": dimension,
        "target_dimensions": dimension_pair or ([dimension] if dimension else []),
        "target_stat": statistic,
        "aggregation": aggregation,
        "school_filter": school_filter,
        "requested_outputs": requested_outputs,
        "value_view": value_view,
        "semantic_evidence": semantic_evidence,
        "metric_evidence_span": semantic_evidence.get("metric"),
        "semantic_catalogue_version": SEMANTIC_CATALOGUE_VERSION,
        "pending_provenance": context.get("pending_provenance") or {},
    }
    if intent == "national_ranking":
        text = _scope_assertion_text(question)
        national = bool(re.search(r"全国|各省|省级地区", text)) if preserve else True
        top_one = bool(re.search(r"最高|最好|榜首|第一名|排名第一", text)) if preserve else True
        if not national or not top_one or len(periods) != 1 or schools or regions not in ([], ["全国"]):
            return {
                "intent": "clarify", "reason": "national_ranking_scope_required",
                "missing": ["请明确一个年份，并询问全国省级地区总体得分最高的地区。"],
                "original": question,
            }
        if (
            dimension or candidate.get("question_ids") or school_filter
            or statistic not in {None, "mean", "max"}
            or target_rank not in {None, 1} or target_top_n not in {None, 1}
            or (_explicit_chart_request(question) if preserve else "charts" in requested_outputs)
        ):
            return {
                "intent": "clarify", "reason": "unsupported_national_ranking_scope",
                "missing": ["当前全国省域查询只支持单年总体得分榜首，不支持单维、题项、筛选或图表。"],
                "original": question,
            }
        province_only = province_only_scope(text) if preserve else False
        return {
            "intent": intent, "year": periods[0], "target_rank": 1,
            "target_top_n": None, "province_only": province_only,
            **{**common, "target_stat": "max", "value_view": "normalized_score"},
        }
    if intent == "school_fact":
        metric_set = _school_question_set(candidate, by_id, question, coverage_binding)
        if len(candidate.get("question_ids") or []) > 1 and metric_set is None:
            return {"intent": "clarify", "reason": "unsupported_school_item_set", "missing": ["当前学校题项一次只支持两道不同问卷题。"], "original": question}
        metric = None if metric_set else _metric_from_candidate(candidate, by_id)
        if len(schools) != 1 or len(periods) != 1 or (metric is None and metric_set is None):
            raise ValueError("experiment_school_fact_scope_incomplete")
        if metric_set is not None:
            if value_view not in {"raw_value", "normalized_score"} or statistic in {"median", "mean_and_median", "max", "min"}:
                return {
                    "intent": "clarify", "reason": "unsupported_school_question_set_scope",
                    "missing": ["双题项支持同校同年两项同口径原始值或标准分及明确指定的计算。"],
                    "original": question,
                }
            common.update({"target_dimension": None, "target_dimensions": [], "target_stat": None})
            return {
                "intent": intent, "school": schools[0], "year": periods[0],
                "metric_set": metric_set, **common,
            }
        if statistic is not None:
            return {
                "intent": "clarify",
                "reason": "unsupported_school_fact_statistic",
                "missing": ["单校单题事实当前只返回该校该年的直接值，不支持另加均值、中位数、计数或极值统计。"],
                "original": question,
            }
        common.update({"target_dimension": None, "target_dimensions": []})
        return {"intent": intent, "school": schools[0], "year": periods[0] if periods else None, "metric": metric, **common}
    if intent == "school_eval":
        metric = _metric_from_candidate(candidate, by_id)
        if len(schools) != 1 or len(periods) != 1 or metric is not None:
            raise ValueError("experiment_school_evaluation_scope_incomplete")
        if preserve and statistic in {"max", "min"} and not dimension and _school_dimension_extrema_request(question):
            statistic = None
        if statistic is not None or school_filter is not None:
            return {
                "intent": "clarify",
                "reason": "unsupported_school_evaluation_statistic_or_filter",
                "missing": ["单校评估首片仅支持一个年份的总体或单维模板得分，不支持另加统计量或学校筛选。"],
                "original": question,
            }
        return {
            "intent": intent,
            "school": schools[0],
            "year": periods[0],
            "years": [periods[0]],
            **common,
        }
    if intent == "school_compare":
        if candidate.get("question_ids") or candidate.get("compound_key") or candidate.get("school_field"):
            return {"intent": "clarify", "reason": "unsupported_school_compare_fact_metric", "missing": ["当前双校比较不支持题项事实值或复合指标比较。"], "original": question}
        if len(schools) != 2 or len(periods) != 1:
            raise ValueError("experiment_school_compare_scope_incomplete")
        return {"intent": intent, "school_a": schools[0], "school_b": schools[1], "year": periods[0], **common}
    if intent == "region_compare":
        if candidate.get("question_ids") or candidate.get("compound_key") or candidate.get("school_field"):
            return {"intent": "clarify", "reason": "unsupported_region_item_set", "missing": ["当前区域题项首片不支持跨区域比较。"], "original": question}
        if len(regions) != 2 or len(periods) != 1:
            raise ValueError("experiment_region_compare_scope_incomplete")
        if statistic not in {None, "mean", "count"}:
            return {
                "intent": "clarify",
                "reason": "unsupported_region_compare_statistic",
                "missing": ["需补充澄清：双区域首批契约仅支持均值或学校数量比较。"],
                "original": question,
            }
        return {"intent": intent, "region_a": regions[0], "region_b": regions[1], "year": periods[0], **common}
    if intent == "region_longitudinal":
        if candidate.get("question_ids") or candidate.get("compound_key") or candidate.get("school_field"):
            return {"intent": "clarify", "reason": "unsupported_region_item_set", "missing": ["当前区域题项首片不支持跨年份比较。"], "original": question}
        if len(regions) != 1 or len(periods) != 2 or periods[0] == periods[1]:
            raise ValueError("experiment_region_longitudinal_scope_incomplete")
        periods = sorted(periods)
        return {
            "intent": intent,
            "region": regions[0],
            "year": periods[0],
            "year_a": periods[0],
            "year_b": periods[1],
            "secondary_year": periods[1],
            "years": periods,
            **common,
        }
    if intent == "region_ranking":
        if dimension is not None:
            return {"intent": "clarify", "reason": "unsupported_region_ranking_dimension", "missing": ["区域排名首片只支持总体分。"], "original": question}
        if len(regions) != 1 or len(periods) != 1 or ((target_rank is None) == (target_top_n is None)):
            raise ValueError("experiment_region_ranking_scope_incomplete")
        if (
            (target_rank is not None and target_rank != -1 and target_rank <= 0)
            or (target_top_n is not None and not 1 <= target_top_n <= 100)
        ):
            raise ValueError("experiment_region_ranking_target_invalid")
        return {
            "intent": intent,
            "region": regions[0],
            "year": periods[0],
            "target_rank": target_rank,
            "target_top_n": target_top_n,
            **common,
        }
    if intent == "region_eval":
        if len(regions) != 1 or len(periods) != 1:
            raise ValueError("experiment_region_scope_incomplete")
        raw_question_ids = candidate.get("question_ids") or []
        if raw_question_ids and (
            not isinstance(raw_question_ids, list) or len(raw_question_ids) != 1
        ):
            return {
                "intent": "clarify",
                "reason": "unsupported_region_item_set",
                "missing": ["区域题集尚未进入当前首片；请一次只问一个题项的归一得分均值。"],
                "original": question,
            }
        if not raw_question_ids and statistic in {"median", "mean_and_median"} and aggregation != "school_equal_mean":
            return {
                "intent": "clarify",
                "reason": "unsupported_region_median_aggregation",
                "missing": ["区域中位数按每校归一得分计算，请使用学校等权口径。"],
                "original": question,
            }
        metric = _metric_from_candidate(candidate, by_id)
        if metric is not None:
            if preserve and re.search(
                r"复合指标|组合指标|指标组合|composite\s+(?:metric|indicator)",
                question,
                re.I,
            ):
                return {
                    "intent": "clarify",
                    "reason": "unsupported_region_item_metric_type",
                    "missing": ["区域单题首片只接受目录中的一个问卷题号，不支持复合指标或题项组合。"],
                    "original": question,
                }
            if metric.get("type") != "question":
                return {
                    "intent": "clarify",
                    "reason": "unsupported_region_item_metric_type",
                    "missing": ["区域单题首片只接受目录中的一个问卷题号，不支持复合指标或学校属性聚合。"],
                    "original": question,
                }
            if dimension is not None and dimension != metric.get("dimension"):
                raise ValueError("experiment_region_item_dimension_mismatch")
            if statistic not in {None, "mean"} or aggregation != "school_equal_mean":
                return {
                    "intent": "clarify",
                    "reason": "unsupported_region_item_statistic_or_aggregation",
                    "missing": ["区域单题首片仅支持该题有效学校的学校等权归一得分均值。"],
                    "original": question,
                }
            if value_view != "normalized_score":
                return {
                    "intent": "clarify",
                    "reason": "unsupported_region_item_raw_aggregation",
                    "missing": ["区域单题首片只支持问卷归一得分均值；原始作答聚合需先明确题型和单位口径。"],
                    "original": question,
                }
            item_common = dict(common)
            item_common.update({
                "target_dimension": None,
                "target_dimensions": [],
                "target_stat": "mean",
                "aggregation": "school_equal_mean",
                "metric": metric,
            })
            return {"intent": intent, "region": regions[0], "year": periods[0], **item_common}
        return {"intent": intent, "region": regions[0], "year": periods[0], **common}
    raise ValueError("experiment_parser_intent_not_supported")

def _with_requirement_preservation(function):
    from functools import wraps

    @wraps(function)
    def scoped(*args, **kwargs):
        enabled = (mechanism_flags(kwargs['mode'])['requirement_preservation']
                   if kwargs['mode'] == 'g1_llm_unconstrained' else True)
        with requirement_preservation_scope(enabled):
            return function(*args, **kwargs)
    return scoped

@_with_requirement_preservation
def parse_semantic_route(
    question: str,
    context: Optional[Dict[str, Any]],
    *,
    mode: str,
    expected_profile_id: Optional[str] = None,
    record_stage=None,
    allowed_intents: Optional[Iterable[str]] = None,
    semantic_constraint_mode: Optional[str] = None,
    semantic_check_callback=None,
    semantic_repair_callback=None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    mode = str(mode or "")
    if mode not in _LLM_MODES:
        raise ValueError("semantic_llm_mode_required")
    catalogue, by_id = _catalogue()
    import os
    preflight_mode = semantic_constraint_mode or os.environ.get('TE_SEMANTIC_PREFLIGHT', 'none')
    from packages.semantic_query.semantic_preflight import model_checker_research_enabled
    if preflight_mode == 'code_model' and not model_checker_research_enabled():
        preflight_mode = 'code'
    if mode != 'g2_llm_constrained': preflight_mode = 'none'
    preflight_trace = {}
    public_context = _public_context(context)
    prompt = _parser_prompt(question, public_context, catalogue)
    from packages.llm_gateway.gateway import GatewayError, ChatRequest, call_detailed, route
    from packages.experiment_protocol.trace import stage_event

    selected = route("chat", fallback=False)
    if not selected:
        raise RuntimeError("experiment_llm_profile_unavailable")
    parser_task = (
        "semantic_parser" if mode == "product_semantic_constrained" else "experiment_parser"
    )
    from time import perf_counter
    parser_started = perf_counter()
    stage_event("parser_call_started", model=selected.get("model"), task=parser_task)
    try:
        detail = call_detailed(ChatRequest(
            provider=selected["provider"],
            model=selected.get("model"),
            task=parser_task,
            messages=[
                {"role": "system", "content": "只输出严格JSON对象。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=3072,
            max_attempts=1, transport_attempts=1, single_endpoint=True,
            max_retry_tokens=4096,
            expected_profile_id=expected_profile_id,
            empty_retry_messages=[
                {"role": "system", "content": "只输出严格JSON对象；不要输出推理过程。"},
                {
                    "role": "user",
                    "content": _parser_empty_retry_prompt(
                        question, public_context, catalogue
                    ),
                },
            ],
        ))
    except GatewayError as gateway_exc:
        stage_event("parser_call_failed", failure=_safe_gateway_failure_code(gateway_exc),
                    usage_status="UNAVAILABLE", elapsed_ms=round((perf_counter() - parser_started) * 1000, 2))
        payload = {
            "stage": "primary",
            "final_failure": _safe_gateway_failure_code(gateway_exc),
            "attempts": [
                _gateway_failure_attempt(max_tokens=3072, message_variant="primary")
            ],
        }
        raise ValueError(
            "experiment_parser_gateway_failed:"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        ) from gateway_exc
    parser_content = detail.get("content") or ""
    stage_event("parser_call_completed", model=detail.get("model"), content=parser_content,
                usage=detail.get("usage") or {}, attempts=detail.get("attempts") or [],
                elapsed_ms=round((perf_counter() - parser_started) * 1000, 2))
    if not str(parser_content).strip():
        attempts = _parser_attempt_summaries(detail)
        raise ValueError(
            "experiment_parser_empty_content:"
            + json.dumps({"attempts": attempts}, ensure_ascii=False, separators=(",", ":"))
        )
    parser_repair = {"applied": False, "disabled_by_protocol": True}
    coverage_trace = {"enabled": mode in {"g2_llm_constrained", "a2_no_output_verification_or_repair", "a3_no_answer_repair"},
                      "applied": False, "signature": None, "scope": "catalogue-grounded explicit two raw records"}
    primary_attempts = _parser_attempt_summaries(detail)
    parser_calls = [{"stage": "primary", "model": detail.get("model"),
                     "usage": detail.get("usage") or {}, "attempts": primary_attempts}]

    def resolve_candidate(content: Any, *, repair_response: bool = False):
        nonlocal preflight_trace
        resolved_model_candidate = _json_object(content)
        if repair_response:

            _validate_candidate_schema(
                resolved_model_candidate, allow_evidence=False, validate_outputs=False
            )
        resolved_normalized, resolved_normalization = _normalize_model_candidate(
            question, resolved_model_candidate
        )
        if requirement_preservation_enabled():
            transition_candidate, resolved_transition = _apply_scope_transition(
                question, resolved_normalized, public_context
            )
            resolved_candidate, resolved_reconciliation = _reconcile_n2_scope_candidate(
                question, transition_candidate
            )
        else:
            resolved_candidate = resolved_normalized
            resolved_transition = {"applied": False, "gate": "OBSERVE_ONLY_ABLATION"}
            resolved_reconciliation = {"applied": False, "gate": "OBSERVE_ONLY_ABLATION"}
        coverage_binding = None
        if coverage_trace["enabled"]:
            from packages.semantic_query.original_task_binding import raw_pair_coverage_signature, repair_record_coverage
            signature = raw_pair_coverage_signature(question)
            corrected = repair_record_coverage(signature, resolved_candidate, resolved_model_candidate)
            coverage_trace.update(signature=signature, before=dict(resolved_candidate))
            if corrected is not None:
                coverage_binding = signature
                coverage_trace.update(applied=corrected != resolved_candidate, after=dict(corrected),
                                      basis="original text, catalogue names/types and unique school/year identity; no answer values")
                resolved_candidate = corrected
            stage_event("original_record_coverage", trace=coverage_trace)
        from packages.semantic_query.semantic_preflight import run_preflight
        def effective_summary(draft):
            return checker_effective_task_summary(question, draft, by_id, context or {},
                                                  coverage_binding=coverage_binding)
        resolved_candidate, preflight_trace = run_preflight(
            question, resolved_model_candidate, resolved_candidate, catalogue,
            mode=preflight_mode, checker=semantic_check_callback, repair=semantic_repair_callback,
            validate=_validate_candidate_schema, context=public_context,
            effective_summary=effective_summary)
        stage_event('semantic_preflight', trace=preflight_trace)
        for stage, key in [('semantic_check','model_check'), ('semantic_repair','model_repair')]:
            if record_stage is not None and preflight_trace.get(key, {}).get('calls'):
                record_stage(stage, 'shared_semantic_route')
        if preflight_trace.get('original_parse_followup', {}).get('unsupported_completion_blocked'):
            from packages.semantic_query.selective_clarification import draft_boundary
            _, _, bounded_clarification = draft_boundary(question, resolved_candidate, context or {}, by_id)
            if bounded_clarification is not None and bounded_clarification.get('selective_clarification'):
                return (resolved_model_candidate, resolved_normalization, resolved_transition, resolved_candidate,
                        resolved_reconciliation, bounded_clarification)
            stage_event('semantic_unresolved_school_binding', candidate=resolved_candidate,
                        reason='original_requirement_uncertain_and_parser_unsupported_completion')
            raise ValueError('semantic_unresolved_school_binding_no_unique_antecedent')
        resolved_route = _route_from_candidate(
            question, resolved_candidate, by_id, context or {}, coverage_binding=coverage_binding
        )
        return (
            resolved_model_candidate,
            resolved_normalization,
            resolved_transition,
            resolved_candidate,
            resolved_reconciliation,
            resolved_route,
        )

    try:
        (
            model_candidate,
            candidate_normalization,
            scope_transition,
            candidate,
            scope_reconciliation,
            routed,
        ) = resolve_candidate(parser_content)
    except ValueError as initial_exc:
        initial_failure = _safe_parser_failure_code(initial_exc)
        stage_event("parser_candidate_failed", failure=initial_failure, first_candidate=parser_content)
        stage_event("parser_repair_disabled", failure=initial_failure, protocol="g012-original100-first-attempt")
        raise
    allowed = {str(item) for item in (allowed_intents or ())}
    if allowed and routed.get("intent") not in allowed and routed.get("intent") != "clarify":
        routed = {
            "intent": "clarify",
            "reason": "semantic_product_scope_n4_firstslice",
            "missing": [
                "智能语义当前支持区域总体/单维/单题归一均值、单校总体/单维和学校单题事实，并支持同对象同年份的层级范围切换；请切换规则对照模式，或改问当前支持范围。"
            ],
            "original": question,
        }
    if record_stage is not None:
        record_stage("parser", "shared_semantic_route")
    from packages.core.research_data import bind_public_constraints, SelectionError
    try:
        routed = bind_public_constraints(question, routed)
        from packages.semantic_query.selective_clarification import observations
        if not observations():
            routed.pop("_selective_task_candidate", None)
    except SelectionError as exc:
        routed = {"intent":"clarify", "reason":str(exc), "missing":[str(exc)], "original":question,
                  "_selective_task_candidate": copy.deepcopy(routed.get("_selective_task_candidate"))}
    answer_spec = build_answer_spec(question, routed)
    if not requirement_preservation_enabled():
        observation = _observe_requirement_preservation(
            question, model_candidate, routed, answer_spec, by_id, context or {})
        routed['requirement_preservation_observation'] = observation
        answer_spec['requirement_preservation_observation'] = copy.deepcopy(observation)
        answer_spec['requirement_correspondence']['would_do'] = observation['would_do']
        stage_event('requirement_preservation_observation', trace=observation)

    routed["requested_outputs"] = list(answer_spec.get("required_outputs") or [])
    routed["requested_result_statistics"] = list(answer_spec.get("requested_result_statistics") or [])
    stage_event("parser_task_resolved", routed=routed, answer_spec=answer_spec)
    trace = {
        "common_path_version": COMMON_PATH_VERSION,
        "prompt_version": PARSER_PROMPT_VERSION,
        "catalogue_version": SEMANTIC_CATALOGUE_VERSION,
        "provider": detail.get("provider"),
        "model": detail.get("model"),
        "finish_reason": detail.get("finish_reason"),
        "usage": detail.get("usage") or {},
        "calls": parser_calls,
        "elapsed_ms": round((perf_counter() - parser_started) * 1000, 2),
        "attempts": primary_attempts + (
            list(parser_repair.get("attempts") or [])
            if parser_repair.get("applied") else []
        ),
        "parser_repair": parser_repair,
        "original_record_coverage": coverage_trace,
        "semantic_preflight": preflight_trace,
        "model_candidate": model_candidate,
        "candidate_normalization": candidate_normalization,
        "scope_transition": scope_transition,
        "candidate": candidate,
        "scope_reconciliation": scope_reconciliation,
        "task_draft": _evidence_value({
            key: value for key, value in routed.items() if key != "pending_provenance"
        }),
        "runtime_mode": mode,
        "profile_bound": bool(expected_profile_id),
    }
    return routed, answer_spec, trace

def checker_effective_task_summary(question, candidate, by_id, context, *, coverage_binding=None):
    pass
    try:
        routed = _route_from_candidate(question, candidate, by_id, context,
                                       coverage_binding=coverage_binding)
        spec = build_answer_spec(question, routed)
    except ValueError as exc:
        return {'compilation_available': False, 'compilation_error': str(exc),
                'user_ambiguity_inferred': False}
    keys = ('operation', 'granularity', 'target_dimensions', 'indicator_ids',
            'required_outputs', 'requested_result_statistics', 'value_view',
            'target_stat', 'subtraction_direction', 'school_filter')
    summary = {key: spec.get(key) for key in keys}
    summary['compilation_available'] = True
    summary['public_plan'] = routed.get('public_plan')
    summary['target_dimension'] = routed.get('target_dimension')
    for field in ('schools', 'regions', 'periods'):
        summary[field] = list(candidate.get(field) or [])
    summary['basis'] = 'same common compiler; original question, actual context and raw parser fields; not a correctness certificate'
    return summary

def _bind_native_subtraction_order(question: str, routed: Dict[str, Any]) -> None:
    pass
    intent = routed.get('intent')
    if intent not in {'region_compare', 'school_compare'} or routed.get('public_plan'):
        return
    if ((routed.get('native_subtraction_binding') or {}).get('question_sha256')
            != routed['atomic_requirements']['question_sha256']):
        routed.pop('native_subtraction_binding', None)
    fields = ('region_a', 'region_b') if intent == 'region_compare' else ('school_a', 'school_b')
    entities = [routed.get(k) for k in fields]
    if not all(isinstance(e, str) and e for e in entities):
        return
    from packages.semantic_query.typed_plan import explicit_subtraction_operands
    import sys
    plan = {'queries': [dict(id='v'+str(i+1), kind='region_value' if intent=='region_compare' else 'school_score',
                            entity=e, year=routed.get('year')) for i,e in enumerate(entities)]}
    try:
        wanted = explicit_subtraction_operands(plan, question, sys.modules[__name__])
    except ValueError:
        wanted = 'UNKNOWN'
    positional = re.search(r'(?:前者\s*减(?:去)?\s*后者|后者\s*减(?:去)?\s*前者)', _scope_assertion_text(question))
    if wanted is None and not positional:
        return
    literals = _literal_region_spans(question) if intent == 'region_compare' else _literal_school_name_spans(question)
    identity_key = lambda s: re.sub(r'\s+', '', s) if intent=='region_compare' else s
    keys = [identity_key(e) for e in entities]
    unique = len(set(keys))==2 and {identity_key(e) for e in literals}==set(keys)
    if wanted in (None, 'UNKNOWN') or not unique:
        routed.pop('native_subtraction_binding', None)
        routed.update(intent='clarify', reason='explicit_comparison_operands_not_unique',
                      missing=['请明确两个不同对象和唯一的减法顺序。'])
        routed['atomic_requirements']['status'] = 'UNKNOWN'
        return
    ordered = [entities[int(node[1:])-1] for node in wanted]
    if wanted==['v2','v1'] and isinstance(routed.get('semantic_evidence'), dict):
        evidence=dict(routed['semantic_evidence'])
        a,b=(evidence.get(k) for k in fields)
        for field,value in zip(fields,(b,a)):
            if value is None:evidence.pop(field,None)
            else:evidence[field]=value
        routed['semantic_evidence']=evidence
    prior = routed.get('native_subtraction_binding') or {}
    routed.update(zip(fields, ordered))
    requirements = routed['atomic_requirements']
    requirements['comparison_operands'] = list(ordered)
    routed['native_subtraction_binding'] = dict(
        question_sha256=requirements['question_sha256'],
        original_objects=prior.get('original_objects', entities), ordered_objects=ordered,
        operation='subtract', direction='primary_minus_secondary',
        requirement_ids=[a['id'] for a in requirements['atoms'] if a['kind']=='result' and a['value']=='difference'])

def _preservation_operand_order(routed, spec):
    plan = routed.get('public_plan') or spec.get('semantic_plan') or {}
    queries = plan.get('queries') or []
    if len(queries) == 2:
        fields = ('entity', 'year', 'dimension', 'question_id')
        nodes = {q['id']: tuple(q.get(k) for k in fields) for q in queries}
        subtraction = next((s for s in plan.get('steps', []) if s['operation'] == 'subtract'), None)
        return [nodes[node] for node in subtraction['args']] if subtraction else list(nodes.values())
    qids = (routed.get('metric_set') or {}).get('qids') or []
    if len(qids) == 2:
        return [(routed.get('school'), routed.get('year'), None, qid) for qid in qids]
    fields = ('region_a', 'region_b') if routed.get('intent') == 'region_compare' else ('school_a', 'school_b')
    return [(routed[k], routed.get('year'), routed.get('target_dimension'), None)
            for k in fields if routed.get(k)]

def _preservation_outcome(actual, actual_spec, expected, expected_spec):
    pass
    route_fields = (
        'intent', 'school', 'school_a', 'school_b', 'region', 'region_a', 'region_b',
        'year', 'year_a', 'year_b', 'years', 'target_dimension', 'target_dimensions',
        'target_stat', 'aggregation', 'school_filter', 'value_view', 'metric', 'metric_set',
        'public_plan', 'target_rank', 'target_top_n', 'research_constraints',
    )
    spec_fields = (
        'required_outputs', 'requested_result_statistics', 'summary_only',
        'include_dimensions', 'indicator_ids', 'chart_derived_result', 'chart_derived_ratio',
        'single_value_only', 'scoring_method_comparison_requested', 'causal_attribution_requested',
    )
    changed = ['route.' + k for k in route_fields if actual.get(k) != expected.get(k)]
    changed += ['spec.' + k for k in spec_fields if actual_spec.get(k) != expected_spec.get(k)]
    reordered = False
    for fields in (('school_a', 'school_b'), ('region_a', 'region_b')):
        before, after = ([route.get(k) for k in fields] for route in (actual, expected))
        reordered |= bool(all(before) and before != after and before == list(reversed(after)))
    for before, after in (
        (actual.get('target_dimensions') or [], expected.get('target_dimensions') or []),
        ((actual.get('metric_set') or {}).get('qids') or [], (expected.get('metric_set') or {}).get('qids') or []),
        (_preservation_operand_order(actual, actual_spec), _preservation_operand_order(expected, expected_spec)),
    ):
        reordered |= bool(len(before) == 2 and before != after and before == list(reversed(after)))
    action = ('clarify' if expected.get('intent') == 'clarify' and actual.get('intent') != 'clarify'
              else 'reorder' if reordered else 'restore' if changed else 'none')
    return {'gate': 'OBSERVE_ONLY_ABLATION', 'would_do': action,
            'changed_fields': changed, 'enabled_intent': expected.get('intent'),
            'enabled_reason': expected.get('reason')}

def _observe_answer_spec(question, routed, spec):
    expected = copy.deepcopy(routed)
    try:
        expected_spec = build_answer_spec(question, expected)
    except ValueError as exc:
        return {'gate': 'OBSERVE_ONLY_ABLATION', 'would_do': 'reject', 'enabled_error': str(exc)}
    return _preservation_outcome(routed, spec, expected, expected_spec)

def _observe_requirement_preservation(question, model, routed, spec, by_id, context):
    pass
    from packages.core.research_data import bind_public_constraints, SelectionError
    from packages.semantic_query.selective_clarification import capture_bindings, set_binding_context
    with requirement_preservation_scope(True), capture_bindings():
        set_binding_context(context, question)
        stage = 'route'
        try:
            expected = (preflight_semantic_scope_boundary(question)
                        or preflight_school_fact_statistic_boundary(question))
            if expected is None:
                normalized, _ = _normalize_model_candidate(question, copy.deepcopy(model))
                transitioned, _ = _apply_scope_transition(question, normalized, _public_context(context))
                candidate, _ = _reconcile_n2_scope_candidate(question, transitioned)
                expected = _route_from_candidate(question, candidate, by_id, copy.deepcopy(context))
                stage = 'research_constraints'
                expected = bind_public_constraints(question, expected)
            stage = 'answer_spec'
            expected_spec = build_answer_spec(question, expected)
        except ValueError as exc:
            action = 'clarify' if stage == 'research_constraints' and isinstance(exc, SelectionError) else 'reject'
            return {'gate': 'OBSERVE_ONLY_ABLATION', 'would_do': action, 'enabled_error': str(exc)}
    observation = _preservation_outcome(routed, spec, expected, expected_spec)
    observation['enabled_task'] = {
        key: expected.get(key) for key in ('intent', 'target_dimension', 'target_dimensions',
            'target_stat', 'value_view', 'school_filter', 'metric_set', 'public_plan')}
    observation['enabled_outputs'] = expected_spec.get('required_outputs')
    observation['enabled_result_statistics'] = expected_spec.get('requested_result_statistics')
    return observation

def build_answer_spec(question: str, routed: Dict[str, Any]) -> Dict[str, Any]:
    pass
    import hashlib
    from packages.semantic_query.semantic_preflight import atomic_requirements, requirement_correspondence, public_requirement_coverage_enabled
    preserve = requirement_preservation_enabled()
    requirements = routed.get('atomic_requirements')
    if requirements is None or not preserve:
        requirements = atomic_requirements(question)
        routed['atomic_requirements'] = requirements
    if preserve and requirements['question_sha256'] != hashlib.sha256(str(question).encode()).hexdigest():
        raise ValueError('original_requirement_question_mismatch')
    if preserve:
        _bind_native_subtraction_order(question, routed)
    spec = _build_answer_spec(question, routed)
    spec['atomic_requirements'] = copy.deepcopy(requirements)
    correspondence = requirement_correspondence(requirements, routed, spec)
    correspondence['gate'] = 'ENFORCED' if preserve and public_requirement_coverage_enabled() else 'OBSERVE_ONLY_ABLATION'
    spec['requirement_correspondence'] = correspondence
    if not preserve:
        spec['atomic_requirements']['gate'] = 'OBSERVE_ONLY_ABLATION'
        observation = routed.get('requirement_preservation_observation')
        if observation is None:
            with requirement_preservation_scope(True):
                observation = _observe_answer_spec(question, routed, spec)
        spec['requirement_preservation_observation'] = copy.deepcopy(observation)
        correspondence['would_do'] = observation['would_do']
    if preserve and public_requirement_coverage_enabled() and routed.get('intent') != 'clarify' and (requirements['status'] == 'UNKNOWN' or correspondence['missing_ids']):
        raise ValueError('explicit_requirement_not_preserved')
    if routed.get('intent') != 'clarify':
        from packages.semantic_query.typed_plan import bind_routed_plan
        catalogue, _ = _catalogue()

        plan_route = routed if preserve else {**routed, 'original': ''}
        semantic_plan = bind_routed_plan(plan_route, spec, {i['id']: i for i in catalogue})
        if semantic_plan is not None:
            spec['semantic_plan'] = semantic_plan
    return spec

def _build_answer_spec(question: str, routed: Dict[str, Any]) -> Dict[str, Any]:
    if routed.get("intent") == "public_task":
        from packages.semantic_query.public_tasks import answer_spec
        return answer_spec(routed)
    outputs = list(routed.get("requested_outputs") or ["direct_answer"])
    preserve = requirement_preservation_enabled()
    if not preserve:
        question = ""
    restoring = bool(routed.get("revision_restore")) or not preserve
    operation = routed.get("intent")
    raw_metric = routed.get("metric") or routed.get("metric_info") or routed.get("metric_set")
    is_qset = (
        bool(routed.get("metric_set"))
        or (isinstance(raw_metric, dict) and raw_metric.get("type") == "question_set")
    )
    granularity = (
        "item_set" if operation == "school_fact" and is_qset
        else "item" if operation == "school_fact" or (
            operation == "region_eval" and isinstance(routed.get("metric"), dict)
        )
        else "dimension_pair" if len(routed.get("target_dimensions") or []) == 2 and operation in {"school_eval", "region_eval"}
        else "dimension" if routed.get("target_dimension")
        else "overall"
    )
    if operation == "region_eval" and granularity == "item" and routed.get("value_view") in {"raw_value", "normalized_score"}:
        value_view = routed["value_view"]
    elif operation in {"school_eval", "region_eval", "school_compare", "region_compare", "region_longitudinal", "region_ranking", "national_ranking", "school_ranking"}:
        value_view = "normalized_score"
    else:
        value_view = routed.get("value_view") or "raw_value"
    target_stat = str(routed.get("target_stat") or routed.get("statistic") or "")
    count_only = bool(
        operation in {"region_eval", "region_compare", "region_longitudinal"}
        and granularity in {"overall", "dimension"}
        and target_stat == "count"
    )
    summary_only = bool(
        operation == "region_eval"
        and (
            count_only
            or (granularity == "overall" and _explicit_summary_only_request(question))
        )
    )
    analysis_tokens = ("分析", "研判", "原因", "建议", "怎么样", "如何")
    analysis_text = _scope_assertion_text(question, mask_explanations=False)
    analysis_text = re.sub(r"(?:不解释|不说明)[^，,。；;：:！？!?\n]*", " ", analysis_text)
    explicit_analysis = any(token in analysis_text for token in analysis_tokens)
    analysis_excluded = not explicit_analysis and any(token in question for token in analysis_tokens)

    wants_analysis = False if summary_only or analysis_excluded else (
        explicit_analysis
        if granularity in {"item", "item_set"}
        else "analysis" in outputs or explicit_analysis
    )
    if not preserve:
        wants_analysis = "analysis" in outputs
    explicit_chart_request = _explicit_chart_request(question)

    wants_charts = explicit_chart_request is True or restoring and "charts" in outputs
    assertion = _scope_assertion_text(question)
    chart_derived_result = bool(
        wants_charts and operation == "school_fact" and granularity == "item_set"
        and isinstance(raw_metric, dict)
        and (raw_metric.get("derived") or {}).get("operation") == "add"
        and re.search(r"合计|总量|加起来", assertion)
    )
    chart_derived_ratio = bool(
        wants_charts and operation == "school_fact" and granularity == "item_set"
        and isinstance(raw_metric, dict) and (raw_metric.get("derived") or {}).get("operation") == "divide"
        and re.search(r"(?:图|画).{0,20}(?:比值|比率)|(?:比值|比率).{0,20}(?:图|画)", assertion)
    )
    scoring_method_comparison_requested = bool(
        operation == "school_eval"
        and granularity == "overall"

        and re.search(r"(?:请|并|同时)(?:同时)?(?:说明|解释|比较).{0,80}(?:83题|全部题|所有题).{0,12}(?:直接求均值|直接均值|平均).{0,25}(?:口径|一样|相同|同一种|区别|不同)", question)
    )
    causal_attribution_requested = bool(
        operation == "region_longitudinal"
        and (
            re.search(
                r"(?:政策|措施|干预).{0,20}(?:造成|导致|引起|影响|归因)"
                r"|(?:造成|导致|引起|影响|归因).{0,20}(?:政策|措施|干预)",
                assertion,
            )

            or re.search(
                r"(?:是不是|是否|能否|能不能).{0,30}(?:政策|措施|干预).{0,20}(?:造成|导致|引起|影响)",
                question,
            )
        )
    )
    dimension_extrema_requested = (
        operation == "school_eval" and granularity == "overall"
        and _school_dimension_extrema_request(question)
    )
    include_dimensions = bool(
        granularity == "overall" and not count_only
        and (dimension_extrema_requested or restoring and "dimension_scores" in outputs or re.search(r"六维(?:分数|得分|明细)|(?:各(?:个)?|全部|所有)维度(?:的)?(?:分数|得分)|六维雷达|雷达图", assertion))
    )
    result_statistics = [target_stat] if target_stat else []
    if include_dimensions:
        result_statistics.append("dimension_scores")
    if dimension_extrema_requested:
        result_statistics.append("dimension_extrema")
    if count_only or _REGION_DENOMINATOR_REQUEST_RE.search(assertion):
        result_statistics.append("school_count")
        if operation == "region_compare":
            result_statistics.extend(("school_count_a", "school_count_b"))
    preserved_difference = preserve and any(a['kind'] == 'result' and a['value'] == 'difference'
                               for a in routed.get('atomic_requirements', {}).get('atoms', []))
    if (operation in {"region_compare", "school_compare"} or granularity == "dimension_pair") and (preserved_difference or _comparison_difference_requested(assertion)):
        result_statistics.append("difference")
    if not count_only and re.search(r"有效作答(?:数|条数)|作答分母|多少条有效作答", assertion):
        result_statistics.append("answer_count")
    if re.search(r"有效题(?:数)?|多少道有效题", assertion):
        result_statistics.append("valid_answer_count")
    catalogue_question_count = None
    indicator_ids = list(
        (routed.get("metric_set") or {}).get("qids")
        or (raw_metric.get("qids") if isinstance(raw_metric, dict) else [])
        or []
    )
    if not count_only and (restoring and "catalogue_question_count" in outputs or re.search(r"目录题数|目录.{0,8}(?:多少|几)(?:道)?题", assertion)):
        catalogue, _ = _catalogue()
        indicator_ids = [
            int(item["id"]) for item in catalogue
            if granularity == "overall" or item.get("dimension") == routed.get("target_dimension")
        ]
        if granularity == "item" and isinstance(routed.get("metric"), dict):
            indicator_ids = [int(routed["metric"]["qid"])]
        catalogue_question_count = len(indicator_ids)
        result_statistics.append("catalogue_question_count")
    prohibited = []
    if count_only:
        prohibited = [
            "unrequested_scores",
            "unrequested_answer_count",
            "unrequested_dimensions",
            "distribution_statistics",
            "rankings",
            "diagnostic_narrative",
            "generic_recommendations",
        ]
    elif granularity in {"item", "item_set"}:
        prohibited = ["unrequested_dimensions", "rankings", "generic_recommendations"]
    elif granularity in {"dimension", "dimension_pair"}:
        prohibited = ["unrequested_dimensions", "rankings"]
    elif summary_only:
        prohibited = [
            "unrequested_dimensions",
            "distribution_statistics",
            "rankings",
            "diagnostic_narrative",
            "generic_recommendations",
        ]
    return {
        "version": "answer-spec-v2",
        "answer_mode": "analysis" if wants_analysis else "direct",
        "granularity": granularity,
        "target_dimensions": list(routed.get("target_dimensions") or []) if granularity == "dimension_pair" else [],
        "summary_only": summary_only,
        "single_value_only": bool(operation == "region_eval" and granularity == "overall" and target_stat in {"max", "min"}
                                  and re.search(r"(?:只|仅)(?:给|报|回答)(?:出)?(?:最高值|最低值|最大值|最小值|数值)", question)
                                  and not wants_charts and not wants_analysis),
        "target_stat": target_stat or None,
        "output_focus": "school_count" if count_only else None,
        "include_dimensions": include_dimensions,
        "requested_result_statistics": list(dict.fromkeys(result_statistics)),
        "indicator_ids": indicator_ids,
        "catalogue_question_count": catalogue_question_count,
        "required_outputs": (["direct_answer"] + (["analysis"] if wants_analysis else []) + (["charts"] if wants_charts else [])
                             if preserve else list(dict.fromkeys(["direct_answer", *outputs]))),
        "chart_requested": wants_charts,
        "chart_derived_result": chart_derived_result,
        "chart_derived_ratio": chart_derived_ratio,
        "scoring_method_comparison_requested": scoring_method_comparison_requested,
        "causal_attribution_requested": causal_attribution_requested,
        "allowed_topics": [
            value for value in (
                *(routed.get("target_dimensions") or []),
                routed.get("target_dimension"),
                (routed.get("metric") or {}).get("name") if isinstance(routed.get("metric"), dict) else None,
                *([item.get("name") for item in (raw_metric.get("items") or [])]
                  if isinstance(raw_metric, dict) and raw_metric.get("items") else []),
            ) if value
        ],
        "value_view": value_view,
        "operation": operation,
        "prohibited_outputs": prohibited,
        "style": (
            "one_sentence"
            if count_only else (
                "one_or_two_sentences"
                if granularity == "item" and not wants_analysis else "compact"
            )
        ),
        "prohibit_unsupported_causal_claims": True,
        "bind_to_question": True,
    }

def bind_rule_output_scope(
    question: str, routed: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    pass
    bound = dict(routed)
    from packages.semantic_query.semantic_preflight import atomic_requirements
    requirements = atomic_requirements(question)
    bound['atomic_requirements'] = requirements
    if requirements['status'] == 'UNKNOWN':
        bound.update(intent='clarify', reason='explicit_requirement_not_unique',
                     missing=['请明确另一维度及唯一的差值操作数顺序。'])
    pair = requirements['execution_dimensions']
    if (pair and bound.get('intent') in {'school_eval', 'region_eval'}
            and len(_scope_years(question)) == 1
            and len(_literal_school_name_spans(question)) + len(_literal_region_spans(question)) == 1
            and not (bound.get('metric') or bound.get('metric_set') or bound.get('metric_info'))):

        bound.update(target_dimension=None, target_dimensions=list(pair))
    answer_spec = build_answer_spec(question, bound)
    if bound.get("intent") == "region_ranking" and not re.search(
        r"有效候选学校|候选学校数|有效学校数|学校数|多少所(?:有效)?学校|样本数|参与学校数",
        _scope_assertion_text(question),
    ):

        answer_spec["rule_hide_unrequested_ranking_population"] = True
    bound["requested_outputs"] = list(
        answer_spec.get("required_outputs") or ["direct_answer"]
    )
    bound["requested_result_statistics"] = list(answer_spec.get("requested_result_statistics") or [])
    return bound, answer_spec

_DROP_KEYS = {
    "base64",
    "image_base64",
    "file_path",
    "image_path",
    "path",
    "contract",
    "contract_store",
    "execution_receipt",
    "transaction_trace",
}

def _evidence_value(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return None
    if isinstance(value, dict):
        return {
            str(key): _evidence_value(item, depth + 1)
            for key, item in value.items()
            if str(key) not in _DROP_KEYS and not str(key).endswith("_path")
        }
    if isinstance(value, list):
        return [_evidence_value(item, depth + 1) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

def common_evidence_packet(result: Dict[str, Any]) -> Dict[str, Any]:
    keep = {
        "task_outputs",
        "task_analysis",
        "plan_fingerprint",
        "catalogue_coverage",
        "object_type",
        "analysis_operation",
        "direct_answer",
        "school_name",
        "school_name_a",
        "school_name_b",
        "short_name",
        "region_name",
        "region_name_a",
        "region_name_b",
        "year",
        "year_a",
        "year_b",
        "requested_year",
        "metric_name",
        "metric_key",
        "answer_value_view",
        "value_type",
        "scale",
        "coding_status",
        "coding_notes",
        "target_value",
        "normalized_score",
        "question_id",
        "question_content",
        "unit",
        "total_score",
        "max_score",
        "min_score",
        "median_score",
        "score_a",
        "score_b",
        "score_diff",
        "comparison_value_a",
        "comparison_value_b",
        "total_score_a",
        "total_score_b",
        "total_diff",
        "overall_score_a",
        "overall_score_b",
        "diff",
        "pct_change",
        "relative_change_status",
        "trend",
        "winner",
        "winner_name",
        "target_dimension",
        "target_dimensions",
        "target_dimensions_detail",
        "target_dimension_scores",
        "target_dimension_score",
        "target_dimension_score_a",
        "target_dimension_score_b",
        "target_stat",
        "aggregation",
        "school_filter",
        "school_count",
        "school_count_a",
        "school_count_b",
        "answer_count",
        "answer_count_a",
        "answer_count_b",
        "item_results",
        "derived_result",
        "requested_school_count",
        "requested_school_count_a",
        "requested_school_count_b",
        "requested_answer_count",
        "requested_answer_count_a",
        "requested_answer_count_b",
        "dimensions",
        "highest_dimensions",
        "lowest_dimensions",
        "dimension_comparison",
        "dimensions_comparison",
        "dimension_deltas",
        "period_a",
        "period_b",
        "ranking_items",
        "target_rank",
        "target_top_n",
        "returned_school_count",
        "ranking_population",
        "fact_data_status",
        "missing_reason",
        "conclusion",
    }
    return {
        key: _evidence_value(result.get(key))
        for key in sorted(keep)
        if result.get(key) is not None
    }

def _only_mean_and_school_count(question: str) -> bool:
    return bool(re.search(r"只(?:给|报|列).{0,18}(?:均分|得分).{0,18}(?:学校数|学校数量)", question))

def _writer_prompt(
    question: str,
    evidence: Dict[str, Any],
    answer_spec: Dict[str, Any],
    *,
    constrained: bool,
    repair: Optional[Dict[str, Any]] = None,
) -> str:
    treatment = (
        "启用所提约束：按AnswerSpec回答必要事实，允许有执行证据的正确补充和等价表达；数值及推导必须可追溯，不得新增无证据数字、对象、年份、因果、显著性或达标判断。"
        if constrained
        else "未启用所提任务契约与答复核验约束；请像普通可用的LLM工具助手一样依据同一证据包回答。"
    )
    repair_text = ""
    if repair:
        repair_text = (
            f"\n上一版答复：{repair.get('text', '')}\n"
            f"核验失败：{json.dumps(repair.get('failures') or [], ensure_ascii=False)}\n"
            "请重新生成完整答复，不要解释修订过程。"
        )
        if constrained:
            from packages.semantic_query.repair_delivery import obligation_check
            retained=obligation_check(repair.get('text') or '',question,evidence,answer_spec)
            repair_text += (
                '\n修复只改有证据的错误。保留首答已核实的对象、时期、各项数值、单位和所问已求值差；'
                '补高低方向不能删除差值，减法表达式不能代替已求值结果。候选生成后须重新核对全部必答项。'
                '\n首答必答项核对：'+json.dumps(retained,ensure_ascii=False)
            )
        if any(str(failure).startswith("unsupported_inference:") for failure in (repair.get("failures") or [])):
            repair_text += (
                "\n只按本轮AnswerSpec与共同工具证据给出当前对象、年份、维度的描述性数值。"
                "删除原因、政策效果、显著性和因果判断；不要用这些判断或限制性说明代替所问数值。"
            )
        if any(str(failure).startswith(("arithmetic_", "unconfirmed:arithmetic_", "evidence_conflict:arithmetic_")) for failure in (repair.get("failures") or [])):
            repair_text += (
                "\n逐式核对展示操作数、减法顺序、结果和单位；未注明舍入的精确等号必须符合展示算式；注明舍入时核对所声明的小数位。"
                "执行分差可能由未舍入均值相减后舍入，不能改成舍入显示均值之差。"
                "没有原始操作数精度证据时，不编造更多小数；可直接报告证据分差并注明计算口径。"
                "保留所问两侧数值、各自样本数、年份和高低方向；允许有证据的正确补充。"
            )
        if "unrequested_difference" in (repair.get("failures") or []):
            repair_text += "\n题面只要求两侧数值；删除未请求的差值和高低多少分，仅保留两地所问数值。"
        if any(str(failure).startswith(("comparison_assertion_", "dimension_compare_answer_difference_direction")) for failure in (repair.get("failures") or [])):
            repair_text += (
                "\n题面是在核对高低及差值。请根据证据先明确回答对或不对，"
                "再写出两侧得分和正确的高低方向、差值；不要复述题面中错误的数值。"
            )
        if "duplicate_text_chart_or_self_draw_request" in (repair.get("failures") or []):
            repair_text += (
                "\n删除文字中的ASCII柱图、代码围栏、示意图和请用户自行绘制的句子；"
                "保留所问数值与范围，由独立图表通道呈现图片。"
            )
        if "item_score_not_teacher_ratio_missing" in (repair.get("failures") or []):
            repair_text += (
                "\n请在最终答复中明确说明：本项是Q59常态化应用的学校等权标准化得分（单位为分），"
                "不是学科教师的实际比例或占比；题目名称含“比例”不等于结果单位是百分比。"
            )
        if "annual_filter_scope_missing" in (repair.get("failures") or []):
            repair_text += (
                "\n请明确说明两个年度各按各自当年登记为乡村的学校筛选，"
                "再给两年均分、有效学校数和后年减前年的分差；不要把两年视为固定同校队列。"
            )
        if any(str(failure).startswith("item_set_") for failure in (repair.get("failures") or [])):
            repair_text += (
                "\n请逐项写出证据包 item_results 中的两个数值，并明确写出 derived_result 的数值、"
                "运算关系和方向；不要把两项之间的差解释成某一项是另一项的子集。"
            )
        if any(str(failure).startswith(("device_count_ratio_", "ordered_ratio_")) for failure in (repair.get("failures") or [])):
            repair_text += (
                "\n请把证据包 item_results 的每项名称与原始值逐一绑定，并按 derived_result 的"
                "左右题号顺序写明除法方向和派生值；倍数保持倍数，百分数保持百分数，"
                "不得把设备台数解释为学生或教师人数，同一项不得同时写出互相矛盾的原始数值。"
            )
    scope_instruction = (
        "单题事实默认用一至两句直答，只写所问数值、单位、时期及必要的数据边界；不得展开六维、榜单或泛化建议。\n"
        if answer_spec.get("granularity") == "item"
        else "只覆盖AnswerSpec允许的粒度和主题，不展开未请求维度、榜单或模板化建议。\n"
    )
    if answer_spec.get("operation") == "region_compare" and answer_spec.get("granularity") == "dimension" and answer_spec.get("target_stat") != "count":
        required_statistics = set(answer_spec.get("requested_result_statistics") or [])
        scope_instruction += "单维区域比较逐一绑定两地均分，说明年份、维度与城乡筛选。"
        if required_statistics & {"school_count", "school_count_a", "school_count_b"}:
            scope_instruction += "逐一给出所问有效学校数。"
        if "difference" in required_statistics:
            scope_instruction += "按题面顺序给出前者减后者的有符号分差。"
            if re.search(r"(?:高|低|多|少)[+-]?\d+(?:\.\d+)?分.{0,5}(?:对吗|是否正确|是不是)", question):
                scope_instruction += "题面在核对高低差值，先明确判断对错，并用证据给出正确方向和差值；不要复述错误数值。"
        else:
            scope_instruction += "题面未要求分差，不附加有符号差值或高低多少分。"
        scope_instruction += (
            "不另画ASCII示意图。图表由工具交付，当前写作证据不含实际画布，"
            "因此正文只描述数据，不描述左右柱、雷达、图中标注等图形布局。\n"
        )
    if answer_spec.get("operation") == "region_longitudinal" and answer_spec.get("chart_requested"):
        scope_instruction += (
            "图表由工具通道处理。正文只描述两年数值、有效学校数、"
            "分差及逐年筛选边界；不得称图为示意图、要求用户自行绘制或另画ASCII柱图，"
            "也不得臆述图中左右柱、标注等当前写作证据未提供的画布细节。\n"
        )
    elif answer_spec.get("chart_requested"):
        scope_instruction += (
            "图表由工具通道处理；正文只描述已核实的数值、学校范围和必要分母。"
            "不要在文字中重复画图、输出代码围栏或ASCII图，不要称实际图为示意图，"
            "也不要要求用户自行绘制或猜测图中布局。\n"
        )
    if evidence.get("school_filter") not in (None, "", "all"):
        scope_instruction += f"正文写明本次学校筛选范围为{evidence['school_filter']}；跨年比较须说明两期各按当年登记筛选。\n"
    if evidence.get("catalogue_coverage"):
        scope_instruction += "用户要求解释维度题目类型：请概括catalogue_coverage中的题项名称和分类，逐组覆盖目录内容；这些是问卷测量内容，不能推断学生成绩、因果效果或未登记事实。\n"
    if _only_mean_and_school_count(question):
        scope_instruction += "题面只要均分和有效学校数：同一句给出两项，不附加有效作答数或维度表。\n"
    if "登记为0" in question or "登记0" in question:
        scope_instruction += "明确答复登记为0的题保留参与计算，NULL缺失值排除；不要把0当缺失。\n"
    if answer_spec.get("operation") == "region_eval" and answer_spec.get("granularity") == "item" and evidence.get("question_id") == 59:
        scope_instruction += (
            "这是Q59常态化应用题的标准化问卷得分。最终答复必须直接说明："
            "该分数不是学科教师的实际比例，也不是Q4技术使用能力分数；"
            "不能仅在证据包中保留这句区分。\n"
        )
    if answer_spec.get("operation") == "public_task":
        if any(q.get('kind') == 'region_item_rank' for q in (answer_spec.get('public_plan') or {}).get('queries', [])):
            scope_instruction += "单题学校排名逐校报告该指标的登记值或标准分及单位，不含跨校均值，不得称为校均或学校等权平均；正文不要抄写内部字段名或枚举值。\n"
        if any(q.get("statistic") == "template_weighted_score" for q in (answer_spec.get("public_plan") or {}).get("queries", [])):
            scope_instruction += "学校得分是模板加权分：维度内有效题项平均，总体按有效维度权重汇总；不是对多所学校等权平均。\n"
        scope_instruction += "逐项报告task_outputs中每个所问对象、年份、指标、统计量及单位；派生差值按标签中的左右操作数方向说明，排名同分按证据规则。不要遗漏中间学校或第二年度。\n"
    if answer_spec.get("operation") == "school_compare" and answer_spec.get("include_dimensions"):
        scope_instruction += "并列给出两校综合分、前校减后校分差，以及证据中每个维度的两校分数。\n"
    if answer_spec.get("granularity") == "dimension_pair":
        scope_instruction += (
            "只按用户题干的维度先后次序报告证据中两维分数及第一维减第二维的有符号分差；"
            "同时用两维名称明确说哪一维高于或低于另一维，同分则说明相等；"
            "高低必须与实际分数一致，不用公式替代文字方向。"
            "不得增列其余四维或整体综合分。仅作描述性比较，不推断一维造成另一维。\n"
        )
    if answer_spec.get("operation") == "school_compare" and answer_spec.get("granularity") == "dimension":
        scope_instruction += (
            f"本题只比较【{evidence.get('target_dimension') or '指定'}】维度。"
            "按题目顺序给出两校该维度分数与前校减后校分差；不要改写为综合分或展开其他维度。\n"
        )
    if answer_spec.get("operation") == "region_ranking" and evidence.get("ranking_items"):
        scope_instruction += "列出要求的每条记录及候选数，说明按证据中的指标和口径排名。\n"
    if "教师培训" in question and "教育教学" in str(evidence.get("target_dimension") or ""):
        scope_instruction += "说明教育教学衡量课堂组织与考试评价，教师培训属于不同维度，不能代替其得分。\n"
    return (
        "你是教育数字化分析助手。普通产品与G1/G2使用同一语义实现、证据投影和生成器。\n"
        f"{treatment}\n"
        f"{scope_instruction}"
        f"用户问题：{question}\n"
        f"AnswerSpec：{json.dumps(answer_spec, ensure_ascii=False)}\n"
        f"共同工具证据：{json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}"
        f"{repair_text}\n"
        "直接输出中文最终答复。"
    )

_MEASURED_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(-?\d+(?:\.\d+)?)\s*(人/台|台/人|Mbps|课时|年|分|台|所|人|个|项|名|位|%|％|次|间)"
)

def _normalized_number(value: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == 0:
        number = 0.0
    return format(number, ".15g")

def _allowed_numbers(question: str, evidence: Dict[str, Any]) -> set[str]:
    raw = json.dumps(evidence, ensure_ascii=False) + " " + question
    return {

        _normalized_number(match.group(0))
        for match in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", raw)
    }

def _supported_absolute_difference_measurement(
    answer: str, match: re.Match[str], evidence: Dict[str, Any]
) -> bool:
    pass
    difference = evidence.get("diff")
    if not isinstance(difference, (int, float)) or not difference:
        return False
    if _normalized_number(match.group(1)) != _normalized_number(str(abs(difference))):
        return False
    preceding = answer[max(0, match.start() - 16):match.start()]
    if re.search(r"(?:相差|差距)(?:约|为|是)?$", preceding):
        return True
    region_a = str(evidence.get("region_name_a") or "")
    region_b = str(evidence.get("region_name_b") or "")
    if region_a and region_b:
        context = answer[max(0, match.start() - 40):match.start()]
        lower, higher = (region_a, region_b) if difference < 0 else (region_b, region_a)
        return bool(
            re.search(rf"{re.escape(lower)}(?:比{re.escape(higher)})?(?:低|少)(?:了|约)?$", context)
            or re.search(rf"{re.escape(higher)}(?:比{re.escape(lower)})?(?:高|多)(?:了|约)?$", context)
        )
    direction = r"(?:低|少|下降|减少)(?:了|约)?$" if difference < 0 else r"(?:高|多|上升|增加)(?:了|约)?$"
    return bool(re.search(direction, preceding))

def _states_not_teacher_ratio(answer: str) -> bool:
    pass
    for denial in re.finditer(r"不是|并非|不等于|不代表|不能|不可|非", answer):
        clause = re.split(r"[，。；;!?！？]", answer[denial.end():denial.end() + 30], maxsplit=1)[0]
        ratio = re.search(r"教师.{0,8}(?:比例|占比)|(?:比例|占比).{0,8}教师", clause)
        if ratio and "Q4" not in clause[:ratio.start()] and "使用能力" not in clause[:ratio.start()]:
            return True
    return False

def _states_each_year_filter(answer: str) -> bool:
    return bool(re.search(
        r"各自当年|分别按.{0,24}当年|"
        r"(?:两年|两期|各年|每年|各期|分别|20\d{2}年(?:和|与|、|及)20\d{2}年).{0,24}(?:各自)?当年",
        answer,
    ))

def verify_answer_text(
    text: str,
    question: str,
    evidence: Dict[str, Any],
    answer_spec: Dict[str, Any],
    *, verify_new_constraints: bool = False,
) -> Dict[str, Any]:
    failures: List[str] = []
    from packages.semantic_query.unit_delivery import validation_input
    if verify_new_constraints:
        text, evidence, answer_spec, unit_delivery_errors, _ = validation_input(text, evidence, answer_spec)
        failures.extend(unit_delivery_errors)
    answer = str(text or "").strip().replace("−", "-").replace("－", "-").replace("减去", "减")
    if verify_new_constraints:
        from packages.semantic_query.fact_assertions import _SUBTRACTION_FORMULA

        answer = _SUBTRACTION_FORMULA.sub(lambda m: m[1]+(m[2] or '')+'减'+m[3]+(m[4] or '')+m[5]+m[6]+(m[7] or ''), answer)

    if answer_spec.get('granularity') == 'dimension_pair':
        for name, span in sorted(_explicit_dimension_aliases(answer), key=lambda p:len(p[1]), reverse=True):
            if name in (evidence.get('target_dimensions') or []) and span != name:
                answer = answer.replace(span, name)
    if not answer:
        failures.append("empty_answer")
    facts = []
    typed_values = None
    arithmetic_allowances = []
    numeric_role_proofs = []
    unit_evidence = []
    if verify_new_constraints:
        from packages.semantic_query.fact_assertions import constraint_inputs, arithmetic_assertions
        answer_spec,evidence=constraint_inputs(question,evidence,answer_spec)
        arithmetic_allowances, arithmetic_failures=arithmetic_assertions(answer,evidence,answer_spec)
        failures.extend(arithmetic_failures)
        from packages.semantic_query.fact_assertions import verify_explicit_scope
        failures.extend(verify_explicit_scope(answer,evidence,answer_spec))
        from packages.semantic_query.required_facts import required_facts, verify_single_value, verify_bound_facts
        facts = required_facts(evidence, answer_spec)
        from packages.semantic_query.fact_assertions import numeric_roles, explicit_listing_order, unit_evidence_review
        numeric_role_proofs, role_errors = numeric_roles(answer,evidence,answer_spec)
        unit_evidence, unit_errors = unit_evidence_review(evidence,answer_spec)
        failures.extend(role_errors + unit_errors + explicit_listing_order(answer,question,evidence,answer_spec))
        failures.extend(verify_single_value(answer, evidence, answer_spec))
        failures.extend(verify_bound_facts(answer, evidence, answer_spec))
        from packages.semantic_query.required_facts import verify_request_fact_slots
        failures.extend(verify_request_fact_slots(answer,evidence,answer_spec))
        from packages.semantic_query.required_facts import verify_public_facts
        failures.extend(verify_public_facts(answer, evidence, answer_spec))
        from packages.semantic_query.fact_assertions import measurements_by_unit
        typed_values = measurements_by_unit(evidence, answer_spec)
        from packages.semantic_query.repair_delivery import obligation_check
        request_obligations=obligation_check(answer,question,evidence,answer_spec)
        failures.extend(request_obligations['failures'])
    allowed = _allowed_numbers(question, evidence)
    typed_allowed = None if typed_values is None else {
        unit: {_normalized_number(token) for value in values for token in (str(value), f"{value:.2f}")}
        for unit, values in typed_values.items()}

    scope_years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", question))
    for key in ("year", "year_a", "year_b", "requested_year"):
        if evidence.get(key) is not None:
            scope_years.add(str(evidence[key]))
    def supported_measurement(match):
        if verify_new_constraints:
            from packages.semantic_query.fact_assertions import structural_count_supported
            structural = structural_count_supported(match, answer, evidence, answer_spec)
            if structural is not None:
                return structural
        if (verify_new_constraints and match.group(2)=='位' and match.group(1).isdigit()
                and re.search(r'(?:保留|精确到|舍入到)$',answer[:match.start()])
                and re.match(r'(?:小数)?[）)]?(?:[，,。；;\n]|$)',answer[match.end():])):
            return True
        if match.group(2) == "年" and match.group(1) in scope_years:
            return True
        if any(_normalized_number(match.group(1))==_normalized_number(p['token']) and match.group(2)==p['unit'] for p in arithmetic_allowances):
            return True
        if typed_allowed is not None:
            from packages.semantic_query.fact_assertions import unit_alias
            return _normalized_number(match.group(1)) in typed_allowed.get(unit_alias(match.group(2)), set())
        return (_normalized_number(match.group(1)) in allowed
                or _supported_absolute_difference_measurement(answer, match, evidence))
    measured_matches=list(_MEASURED_NUMBER_RE.finditer(answer))
    if verify_new_constraints:
        from packages.semantic_query.fact_assertions import MEASURE
        measured_matches=list(MEASURE.finditer(answer))+[m for m in measured_matches if m.group(2)=='年']
    unsupported_numbers = sorted({
        match.group(1)
        for match in measured_matches
        if not supported_measurement(match)
    })
    if unsupported_numbers:
        failures.append(("unconfirmed:unsupported_numbers:" if verify_new_constraints else "unsupported_numbers:") + ",".join(unsupported_numbers))
    if (
        answer_spec.get("operation") == "region_compare"
        and not verify_new_constraints
        and "difference" not in (answer_spec.get("requested_result_statistics") or [])
        and isinstance(evidence.get("diff"), (int, float))
    ):
        difference = abs(float(evidence["diff"]))
        side_values = {
            _normalized_number(str(value))
            for value in (evidence.get("total_score_a"), evidence.get("total_score_b"))
            if isinstance(value, (int, float))
        }
        if _normalized_number(str(difference)) not in side_values and any(
            match.group(2) == "分"
            and _normalized_number(str(abs(float(match.group(1))))) == _normalized_number(str(difference))
            for match in _MEASURED_NUMBER_RE.finditer(answer)
        ):
            failures.append("unrequested_difference")
    causal_tokens = ("导致", "造成", "证明", "因果", "显著提升", "显著降低", "已经达标")
    if answer_spec.get("prohibit_unsupported_causal_claims"):
        for token in causal_tokens:
            for claim in re.finditer(re.escape(token), answer):

                prefix = re.split(r"[。；;！!？?\n，,]|但是|然而|但", answer[:claim.start()])[-1]
                denied = re.search(r"(?:不能|不可|不应|不宜|不据此|无法|没有证据|未有证据|未证实|不代表|不表示|不支持|不证明|不推断|不用于推断|不用来推断|不作为推断|不解释为|不是|不作|不做|并非)(?:[^。；;！!？?\n]{0,22})$", prefix)
                suffix = re.split(r"[。；;！!？?\n，,]|但是|然而|但", answer[claim.end():])[0]
                denied = denied or (re.search(r"(?:不对|未对)[^。；;！!？?\n]*$", prefix)
                                     and re.search(r"(?:作|做|进行).{0,6}(?:推断|推测|归因|判断|分析)", suffix))

                denied = denied or (
                    re.search(r"(?:不对|未对)[^。；;！!？?\n]{0,22}(?:作|做|进行)\s*$", prefix)
                    and re.match(r"\s*(?:关系)?(?:推断|推测|归因|判断|分析)", suffix)
                )
                if not denied:
                    failures.append("unsupported_inference:" + token)
    direct = str(evidence.get("direct_answer") or "").strip()
    if "direct_answer" in (answer_spec.get("required_outputs") or []) and direct and len(answer) < 2:
        failures.append("required_direct_answer_missing")
    if "analysis" in (answer_spec.get("required_outputs") or []) and len(answer) < 30:
        failures.append("required_analysis_missing")
    if answer_spec.get("scoring_method_comparison_requested"):
        compact_answer = re.sub(r"\s+", "", answer)
        mentions_template_method = (
            any(token in compact_answer for token in ("模板", "六维", "维度"))
            and "加权" in compact_answer
        )
        mentions_direct_average = bool(re.search(
            r"(?:83|八十三)(?:道)?题.{0,18}(?:均值|平均)"
            r"|(?:均值|平均).{0,18}(?:83|八十三)(?:道)?题",
            compact_answer,
        ))
        states_difference = bool(re.search(
            r"不是|并非|不同|有别|不等同|不相同|不一致|不能等同|不等于|差异|区别",
            compact_answer,
        ))
        if not (mentions_template_method and mentions_direct_average and states_difference):
            failures.append("required_scoring_method_comparison_missing")
    compact_answer = re.sub(r"\s+", "", answer)
    if answer_spec.get("operation") == "school_eval" and "dimension_extrema" in (answer_spec.get("requested_result_statistics") or []):
        high = list(evidence.get("highest_dimensions") or [])
        low = list(evidence.get("lowest_dimensions") or [])
        total = evidence.get("total_score")
        if not high or not low or any(name not in compact_answer for name in high + low):
            failures.append("school_dimension_extrema_missing")
        if total is None or _normalized_number(str(total)) not in {
            _normalized_number(match.group(0)) for match in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", answer)
        }:
            failures.append("school_profile_total_score_missing")
        clauses = re.split(r"[，,。；;！？!?\n]", compact_answer)
        if not all(any("最高" in clause and name in clause for clause in clauses) for name in high) or not all(
            any("最低" in clause and name in clause for clause in clauses) for name in low
        ):
            failures.append("school_dimension_extrema_direction_missing")
    derived = evidence.get("derived_result")
    kind_item_failures = None
    if verify_new_constraints:
        from packages.semantic_query.fact_assertions import verify_kind_items
        kind_item_failures=verify_kind_items(answer,evidence,answer_spec)
        if kind_item_failures is not None:failures.extend(kind_item_failures)
    if (
        answer_spec.get("operation") == "school_fact"
        and kind_item_failures is None
        and answer_spec.get("granularity") == "item_set"
        and isinstance(derived, dict)
        and derived.get("operation") in {"add", "subtract"}
        and derived.get("value") is not None
    ):
        answer_numbers = {
            _normalized_number(match.group(0))
            for match in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", answer)
        }
        items = [item for item in (evidence.get("item_results") or []) if isinstance(item, dict)]
        for item in items:
            value = item.get("target_value")
            if value is not None and _normalized_number(str(value)) not in answer_numbers:
                failures.append("item_set_value_missing")
                break
        item_aliases = {
            61: ("班级教室",),
            62: ("交互设备", "多媒体设备", "电子白板", "一体机"),
            66: ("学生用平板", "学生平板"),
            67: ("台式机", "台式电脑"),
            69: ("笔记本", "笔记本电脑"),
            70: ("教师用平板", "教师平板", "老师平板"),
        }
        named_items = [item for item in items if item.get("question_id") in item_aliases]
        if len(named_items) == 2:
            clauses = re.split(r"[，,、。；;！？!?\n]", compact_answer)
            label_patterns = {}
            for item in named_items:
                qid = item["question_id"]
                aliases = list(item_aliases[qid]) + [f"第{qid}题", f"Q{qid}"]
                if qid == derived.get("left_qid"):
                    aliases += ["前者", "前一项", "前一个"]
                elif qid == derived.get("right_qid"):
                    aliases += ["后者", "后一项", "后一个"]
                label_patterns[qid] = re.compile("|".join(re.escape(alias) for alias in aliases), re.I)

            def binds_item(clause, item):
                qid = item["question_id"]
                other = next(row["question_id"] for row in named_items if row["question_id"] != qid)
                expected_value = _normalized_number(str(item["target_value"]))
                for label in label_patterns[qid].finditer(clause):
                    for number in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", clause):
                        if _normalized_number(number.group(0)) != expected_value:
                            continue
                        gap = (
                            clause[label.end():number.start()] if label.end() <= number.start()
                            else clause[number.end():label.start()] if number.end() <= label.start()
                            else None
                        )
                        if gap is not None and len(gap) <= 12 and not re.search(r"\d", gap) and not label_patterns[other].search(gap):
                            return True
                return False

            bound = all(any(binds_item(clause, item) for clause in clauses) for item in named_items)
            if not bound:
                for clause in clauses:
                    if "分别" not in clause:
                        continue
                    labels_part, values_part = clause.split("分别", 1)
                    label_order = [
                        (next((match.start() for match in label_patterns[item["question_id"]].finditer(labels_part)), None), item)
                        for item in named_items
                    ]
                    values = [
                        _normalized_number(match.group(0))
                        for match in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", values_part)
                    ]
                    if all(pos is not None for pos, _ in label_order) and len({pos for pos, _ in label_order}) == 2:
                        ordered = [item for _, item in sorted(label_order, key=lambda pair: pair[0])]
                        if values[:2] == [_normalized_number(str(item["target_value"])) for item in ordered]:
                            bound = True
                            break
            if not bound:
                failures.append("item_set_named_values_mismatch")
        expected = _normalized_number(str(derived["value"]))
        clauses = re.split(r"[。；;！？!?\n]", compact_answer)
        value_clauses = [
            clause for clause in clauses
            if expected in {
                _normalized_number(match.group(0))
                for match in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", clause)
            }
        ]
        if not value_clauses:
            failures.append("item_set_derived_value_missing")
        elif derived["operation"] == "add":
            if not any(re.search(r"合计|总计|总共|总和|相加|加起来|两项之和|两者之和|求和|[+＋]", clause) for clause in value_clauses):
                failures.append("item_set_derived_binding_missing")
        else:
            by_qid = {str(item.get("question_id")): item.get("target_value") for item in items}
            left = by_qid.get(str(derived.get("left_qid")))
            right = by_qid.get(str(derived.get("right_qid")))
            directional = [
                r"前(?:者|一个|项).{0,12}(?:减|－|−|-).{0,12}后(?:者|一个|项)",
                rf"(?:第|Q)?{derived.get('left_qid')}题?.{{0,12}}(?:减|－|−|-).{{0,12}}(?:第|Q)?{derived.get('right_qid')}题?",
            ]
            if {derived.get("left_qid"), derived.get("right_qid")} == {66, 70}:
                role = {
                    70: r"(?:教师|老师)(?:用|使用)?(?:平板(?:电脑)?)?(?:台数|数量|数)?",
                    66: r"学生(?:用|使用)?(?:平板(?:电脑)?)?(?:台数|数量|数)?",
                }
                directional.append(
                    role[derived["left_qid"]] + r".{0,8}(?:减|－|−|-)" + r".{0,8}" + role[derived["right_qid"]]
                )
            if left is not None and right is not None:
                directional.append(rf"{re.escape(str(left))}.{{0,12}}(?:减|－|−|-).{{0,12}}{re.escape(str(right))}")
            if not any(re.search(pattern, clause) for clause in value_clauses for pattern in directional):
                failures.append("item_set_subtraction_direction_missing")
    if (
        answer_spec.get("operation") == "school_fact"
        and answer_spec.get("granularity") == "item_set"
        and isinstance(derived, dict)
        and derived.get("operation") in {"divide", "divide_percent"}
        and derived.get("value") is not None
    ):
        failures.extend(_verify_ordered_quotient_answer(answer, evidence, derived))
    if answer_spec.get("granularity") == "dimension_pair":
        names = list(evidence.get("target_dimensions") or [])
        rows = {row.get("dimension"): row.get("score") for row in (evidence.get("dimensions") or [])}
        answer_numbers = {
            _normalized_number(match.group(0))
            for match in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", answer)
        }
        if len(names) != 2 or any(name not in rows or rows[name] is None for name in names):
            failures.append("dimension_pair_evidence_incomplete")
        else:
            for name in names:
                if name not in compact_answer or _normalized_number(str(rows[name])) not in answer_numbers:
                    failures.append("dimension_pair_answer_side_missing")
                    break
            if not verify_new_constraints and compact_answer.find(names[0]) > compact_answer.find(names[1]):
                failures.append("dimension_pair_answer_order_mismatch")
            expected_diff = evidence.get("diff")
            proved_relation=verify_new_constraints and not arithmetic_failures and any(
                p.get('directed') and expected_diff is not None and abs(abs(float(p['proof']['value']))-abs(float(expected_diff)))<.005000001
                for p in arithmetic_allowances)
            if expected_diff is None or (_normalized_number(str(expected_diff)) not in answer_numbers and not proved_relation):
                failures.append("dimension_pair_answer_difference_missing")

            for left, right in ((names[0], names[1]), (names[1], names[0])):
                relative = float(rows[left]) - float(rows[right])
                for claim in re.finditer(re.escape(left) + r'(?:的)?(?:维度)?(?:得分)?(?:为)?[【】]*'
                                         r'(高于|低于|等于)' + r'[【】]*' + re.escape(right), compact_answer):
                    direction = claim.group(1)
                    if ((direction == '高于' and relative <= 0) or
                        (direction == '低于' and relative >= 0) or
                        (direction == '等于' and relative != 0)):
                        failures.append('dimension_pair_answer_direction_mismatch')
            if any(name in compact_answer for name in (_DIMENSIONS - set(names))):
                failures.append("dimension_pair_answer_extra_dimension")
    if answer_spec.get("chart_requested") and re.search(r"```|ASCII|字符画|柱状图示意|请.{0,15}自行绘", answer, re.IGNORECASE):
        failures.append("duplicate_text_chart_or_self_draw_request")
    school_filter = str(evidence.get("school_filter") or "")
    if school_filter and school_filter != "all":
        filter_tokens = ("城镇", "城区") if "城镇" in school_filter or "城区" in school_filter else (school_filter,)
        if not any(token in compact_answer for token in filter_tokens):
            failures.append("school_filter_scope_missing")
        if answer_spec.get("operation") == "region_longitudinal" and "各自当年" in question and not _states_each_year_filter(compact_answer):
            failures.append("annual_filter_scope_missing")
    if ("登记为0" in question or "登记0" in question) and not (
        "0" in compact_answer and any(token in compact_answer for token in ("保留", "计入", "参与计算"))
    ):
        failures.append("zero_retention_explanation_missing")
    if answer_spec.get("operation") == "region_eval" and answer_spec.get("granularity") == "item" and evidence.get("question_id") == 59:
        if not any(token in compact_answer for token in ("标准化", "归一")) or not _states_not_teacher_ratio(compact_answer):
            failures.append("item_score_not_teacher_ratio_missing")
    if _only_mean_and_school_count(question) and evidence.get("answer_count") is not None:
        if _normalized_number(str(evidence["answer_count"])) in {_normalized_number(m.group(0)) for m in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", answer)} and "作答" in answer:
            failures.append("answer_count_outside_requested_scope")
    if "教师培训" in question and evidence.get("target_dimension") == "教育教学" and not ("教师培训" in answer and any(t in answer for t in ("不能代替", "不可替代", "不同维度", "不等同"))):
        failures.append("dimension_substitution_explanation_missing")
    if answer_spec.get("operation") == "region_ranking" and evidence.get("ranking_items") and "题均分" in question and "题均分" not in answer:
        failures.append("ranking_score_method_missing")
    if answer_spec.get("operation") == "school_compare" and answer_spec.get("include_dimensions") and evidence.get("dimensions_comparison"):
        answer_numbers = {_normalized_number(m.group(0)) for m in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", answer)}
        for row in evidence["dimensions_comparison"]:
            if not (str(row.get("dimension") or "") in answer
                    and _normalized_number(str(row.get("score_a"))) in answer_numbers
                    and _normalized_number(str(row.get("score_b"))) in answer_numbers):
                failures.append("school_comparison_dimensions_missing")
                break
    expected_item_value = evidence.get("normalized_score")
    if expected_item_value is None:
        expected_item_value = evidence.get("target_value")
    if (
        answer_spec.get("operation") == "region_eval"
        and answer_spec.get("granularity") == "item"
        and answer_spec.get("value_view") == "normalized_score"
        and expected_item_value is not None
    ):
        answer_numbers = {
            _normalized_number(match.group(0))
            for match in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", answer)
        }
        if _normalized_number(str(expected_item_value)) not in answer_numbers:
            failures.append("required_target_value_missing")
        if any(token in answer for token in ("没有可核验", "暂无可核验", "无法核验", "无可用数据")):
            failures.append("answer_denies_available_evidence")
    if (
        answer_spec.get("operation") == "region_eval"
        and answer_spec.get("target_stat") == "count"
        and answer_spec.get("output_focus") == "school_count"
    ):
        expected_school_count = evidence.get("school_count")
        answer_numbers = {
            _normalized_number(match.group(0))
            for match in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", answer)
        }
        if (
            expected_school_count is not None
            and _normalized_number(str(expected_school_count)) not in answer_numbers
        ):
            failures.append("required_school_count_missing")
        if any(token in answer for token in ("均分", "平均得分", "中位数", "最高分", "最低分")):
            failures.append("count_only_scope_widened")
    if answer_spec.get("operation") == "region_compare" and answer_spec.get("granularity") == "dimension" and answer_spec.get("target_stat") != "count" and evidence.get("fact_data_status") != "NO_DATA":
        comparison_failures=_verify_dimension_compare_answer(answer, evidence, answer_spec, question)
        if verify_new_constraints and arithmetic_allowances and not arithmetic_failures:
            comparison_failures=[f for f in comparison_failures if f!='dimension_compare_answer_difference_direction']
        if verify_new_constraints and 'unconfirmed:request_operand_precision_unavailable' in failures:

            comparison_failures=['unconfirmed:dimension_operand_precision_unavailable' if f=='dimension_compare_answer_side_value_or_count_mismatch' else f for f in comparison_failures]
        failures.extend(comparison_failures)
    if answer_spec.get("operation") == "school_compare" and answer_spec.get("granularity") == "dimension":
        dimension = str(evidence.get("target_dimension") or "")
        answer_numbers = {_normalized_number(m.group(0)) for m in re.finditer(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?", answer)}
        if not dimension or dimension not in answer:
            failures.append("school_comparison_dimension_missing")
        for side in ("a", "b"):
            value = evidence.get(f"comparison_value_{side}")
            if value is None or _normalized_number(str(value)) not in answer_numbers:
                failures.append(f"school_comparison_dimension_score_{side}_missing")
        if re.search(r"综合(?:得分|分数|均分|分差)|整体(?:得分|分数|均分|分差)", answer):
            failures.append("school_comparison_overall_scope_leak")
    failures=list(dict.fromkeys(failures))
    inconclusive=[f for f in failures if f.startswith('unconfirmed:')]
    violations=[f for f in failures if not f.startswith('unconfirmed:')]
    return {
        "status": "FAIL" if violations else "UNCONFIRMED" if inconclusive else "PASS",
        "violations": violations,
        "inconclusive": inconclusive,
        "request_obligations": request_obligations if verify_new_constraints else None,
        "arithmetic_proofs": arithmetic_allowances,
        "numeric_role_proofs": numeric_role_proofs,
        "unit_evidence": unit_evidence,
        "checks": [
            "non_empty",
            "evidence_number_scope",
            "inference_strength",
            "required_target_value",
            "scoring_method_comparison",
            "required_school_count",
            "count_only_scope",
            "dimension_comparison_order_and_facts",
            "school_fact_item_set_values",
            "ordered_ratio_values_and_direction",
        ],
        "failures": failures,
        "unsupported_numbers": unsupported_numbers,
        "required_facts": facts,
        "new_fact_constraints_enabled": verify_new_constraints,
        "constraint_rule_version": "executed-facts-1.0" if verify_new_constraints else None,
    }

def _verify_ordered_quotient_answer(
    answer: str, evidence: Dict[str, Any], derived: Dict[str, Any],
) -> List[str]:
    pass
    failures: List[str] = []
    items = [item for item in (evidence.get("item_results") or []) if isinstance(item, dict)]
    by_qid = {item.get("question_id"): item for item in items}
    left_qid, right_qid = derived.get("left_qid"), derived.get("right_qid")
    if len(items) != 2 or set(by_qid) != {left_qid, right_qid} or any(
        by_qid[qid].get("target_value") is None for qid in (left_qid, right_qid)
    ):
        return ["ordered_ratio_evidence_incomplete"]
    from packages.semantic_query.required_facts import _distinct_labels
    aliases = {qid:_distinct_labels(by_qid[qid],by_qid[right_qid if qid==left_qid else left_qid])
               for qid in (left_qid,right_qid)}

    vocabulary=(('学生',('学生',)),('教师',('教师','老师')),
                ('交互式多媒体设备',('交互式多媒体设备','多媒体设备','交互设备','电子白板','一体机')),
                ('班级教室总数',('班级教室',)))
    for qid in (left_qid,right_qid):
        other=right_qid if qid==left_qid else left_qid
        label=str(by_qid[qid].get('metric_name') or '')
        other_label=str(by_qid[other].get('metric_name') or '')
        for meaning,words in vocabulary:
            if meaning in label and meaning not in other_label:aliases[qid].update(words)
    patterns = {
        qid: re.compile(rf"第\s*{qid}\s*题|Q\s*{qid}|" + "|".join(re.escape(v) for v in sorted(aliases[qid],key=len,reverse=True)), re.I)
        for qid in (left_qid, right_qid)
    }
    number_pattern = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?")
    from packages.semantic_query.fact_assertions import _FORMULA_UNIT, unit_alias
    number=r'[+-]?\d+(?:\.\d+)?'
    division_pattern=re.compile(r'('+number+r')\s*('+_FORMULA_UNIT+r')?\s*[÷/]\s*('+number+r')\s*('+_FORMULA_UNIT+r')?')
    division_spans=[m.span() for m in division_pattern.finditer(answer)]
    physical_units='|'.join(re.escape(str(row.get('unit'))) for row in items if row.get('unit')) or r'(?!)'
    positions: Dict[int, int] = {}
    contradictory_labels = set()
    for segment in re.finditer(r"[^，,、。；;！？!?\n]+", answer):
        labels = sorted(
            (match.start(), match.end(), qid)
            for qid, pattern in patterns.items()
            for match in pattern.finditer(segment.group(0))
        )
        for index, (_, end, qid) in enumerate(labels):
            next_label = labels[index + 1][0] if index + 1 < len(labels) else len(segment.group(0))
            expected = _normalized_number(str(by_qid[qid]["target_value"]))
            tail = segment.group(0)[end:next_label]
            numbers = [m for m in number_pattern.finditer(tail)
                       if not any(a<=segment.start()+end+m.start()<b for a,b in division_spans)]

            claimed = [
                match for match in numbers
                if re.match(r"\s*(?:"+physical_units+r")(?!数比)", tail[match.end():])
            ]
            if not claimed:
                for match in numbers:
                    if re.match(r'\s*[÷/]\s*\d',tail[match.end():]):

                        continue
                    if re.match(r"\s*(?:倍|%|％)", tail[match.end():]):
                        continue
                    if re.search(r"除以|[÷/]|比值|之比|台数比", tail[:match.start()]):
                        continue
                    claimed = [match]
                    break
            if claimed:
                observed = {_normalized_number(match.group(0)) for match in claimed}
                if observed == {expected}:
                    positions.setdefault(qid, segment.start() + labels[index][0])
                else:
                    contradictory_labels.add(qid)
    if set(positions) != {left_qid, right_qid}:
        failures.append("ordered_ratio_named_values_mismatch")
    if contradictory_labels:
        failures.append("ordered_ratio_named_values_contradiction")

    expected_ratio = float(derived["value"])
    tolerance = 0.01 if derived["operation"] == "divide_percent" else 0.0001
    left_value = float(by_qid[left_qid]["target_value"])
    right_value = float(by_qid[right_qid]["target_value"])
    for formula in division_pattern.finditer(answer):
        operands=(float(formula[1]),float(formula[3]))
        if operands not in {(left_value,right_value),(right_value,left_value)}:
            failures.append('ordered_ratio_formula_operand_mismatch')
        for observed,qid in ((formula[2],left_qid),(formula[4],right_qid)):
            if observed and unit_alias(observed)!=unit_alias(by_qid[qid].get('unit')):
                failures.append('ordered_ratio_formula_unit_mismatch')
        stated=re.match(r'\s*=\s*('+number+r')\s*('+_FORMULA_UNIT+r'|倍)?',answer[formula.end():])
        if stated:
            if abs(float(stated[1])-expected_ratio)>tolerance:failures.append('ordered_ratio_formula_result_mismatch')
            if stated[2] and unit_alias(stated[2])!=unit_alias(derived.get('unit')):failures.append('ordered_ratio_formula_result_unit_mismatch')

    def edge_label(fragment: str, *, last: bool) -> Optional[int]:
        found = [
            (match.start(), qid)
            for qid, pattern in patterns.items()
            for match in pattern.finditer(fragment)
        ]
        if not found:
            return None
        return (max if last else min)(found, key=lambda pair: pair[0])[1]

    ratio_clauses = []
    for clause in re.finditer(r"[^。；;！？!?\n]+", answer):
        if any(abs(float(match.group(0)) - expected_ratio) <= tolerance for match in number_pattern.finditer(clause.group(0))):
            ratio_clauses.append(clause.group(0))
    if not ratio_clauses:
        failures.append("ordered_ratio_value_missing")
        return failures
    orientations = []
    for clause in ratio_clauses:
        if re.search(r"前(?:者|一个|一项|项).{0,12}(?:除以|是).{0,12}后(?:者|一个|一项|项)", clause):
            if set(positions) == {left_qid, right_qid}:
                orientations.append((left_qid, right_qid) if positions[left_qid] < positions[right_qid] else (right_qid, left_qid))
        for operator in re.finditer(r"除以|÷|/", clause):
            first = edge_label(clause[:operator.start()], last=True)
            second = edge_label(clause[operator.end():], last=False)
            if first is not None and second is not None:
                orientations.append((first, second))
        for operator in re.finditer(r"是", clause):
            first = edge_label(clause[:operator.start()], last=True)
            second = edge_label(clause[operator.end():], last=False)
            if first is not None and second is not None:
                orientations.append((first, second))
        for formula in division_pattern.finditer(clause):
            operands = (float(formula[1]), float(formula[3]))
            if operands == (left_value, right_value):
                orientations.append((left_qid, right_qid))
            elif operands == (right_value, left_value):
                orientations.append((right_qid, left_qid))
    if not orientations or any(pair != (left_qid, right_qid) for pair in orientations):
        failures.append("ordered_ratio_direction_mismatch")
    if derived["operation"] == "divide_percent":
        if not any(re.search(r"%|百分", clause) for clause in ratio_clauses):
            failures.append("ordered_ratio_percent_unit_missing")
    else:
        if re.search(r"百分之|\d+(?:\.\d+)?\s*%", answer):
            failures.append("ordered_ratio_as_percent")
        if not any(re.search(r"倍|台数比|比值|之比|除以|[÷/]", clause) for clause in ratio_clauses):
            failures.append("ordered_ratio_unit_or_relation_missing")
    return failures

def _verify_dimension_compare_answer(
    answer: str, evidence: Dict[str, Any], answer_spec: Dict[str, Any], question: str = ""
) -> List[str]:
    pass
    failures = []
    compact = re.sub(r"[\s*`]", "", answer).replace("−", "-").replace("负", "-")
    names = [str(evidence.get("region_name_a") or ""), str(evidence.get("region_name_b") or "")]

    aliases = [re.sub(r"(?:市|省)$", "", name) for name in names]
    requested_statistics = set(answer_spec.get("requested_result_statistics") or [])
    require_counts = bool(requested_statistics & {"school_count", "school_count_a", "school_count_b"})
    year = str(evidence.get("year") or "")
    dimension = str(evidence.get("target_dimension") or "")
    from packages.agent.tools.chart_scope import comparison_filter_label
    filter_label = comparison_filter_label(evidence.get("school_filter")) if evidence.get("school_filter") not in {None, "", "all"} else None
    if not year or year not in compact or not dimension or dimension not in compact or (filter_label and filter_label not in compact):
        failures.append("dimension_compare_answer_scope_missing")
    for index, suffix in enumerate(("a", "b")):
        score = evidence.get("total_score_" + suffix)
        count = evidence.get("school_count_" + suffix)
        if not aliases[index] or score is None or (require_counts and count is None):
            failures.append("dimension_compare_answer_side_missing")
            continue
        matched_at = -1
        for mention in re.finditer(re.escape(aliases[index]), compact):
            following = compact[mention.end():]
            stop = re.search("|".join(re.escape(alias) for alias in aliases if alias), following)
            clause = following[:stop.start()] if stop else following
            numbers = [_normalized_number(v) for v in re.findall(r"[-+]?\d+(?:\.\d+)?", clause)]
            if (
                _normalized_number(f"{float(score):.2f}") in numbers
                and (not require_counts or _normalized_number(str(count)) in numbers)
            ):
                matched_at = mention.start()
                break
        if matched_at < 0:
            failures.append("dimension_compare_answer_side_value_or_count_mismatch")
    difference = evidence.get("diff")
    if "difference" in requested_statistics and difference is None:
        failures.append("dimension_compare_answer_difference_missing")
    elif "difference" in requested_statistics:
        expected = f"{float(difference):.2f}"
        value_pattern = re.escape(expected).replace(r"\-", "-")
        if float(difference) >= 0:
            value_pattern = r"\+?" + value_pattern
        direction = rf"(?:{re.escape(aliases[0])}(?:市|省)?(?:减|−|-){re.escape(aliases[1])}(?:市|省)?|前者减后者|前减后)"
        difference_number = rf"(?<![\d.]){value_pattern}(?![\d.])分"
        magnitude = re.escape(f"{abs(float(difference)):.2f}")
        directed_word = "低|少" if float(difference) < 0 else "高|多"
        directed_difference = rf"{re.escape(names[0])}(?:比{re.escape(names[1])})?(?:{directed_word})(?:了|约)?{magnitude}分"
        reverse_word = "高|多" if float(difference) < 0 else "低|少"
        reverse_difference = rf"{re.escape(names[1])}(?:比{re.escape(names[0])})?(?:{reverse_word})(?:了|约)?{magnitude}分"
        if not (re.search(direction + rf"[^。；]{{0,30}}?{difference_number}", compact)
                or re.search(difference_number + rf"[^。；]{{0,30}}?{direction}", compact)
                or re.search(directed_difference, compact)
                or re.search(reverse_difference, compact)):
            failures.append("dimension_compare_answer_difference_direction")
        assertion = re.search(
            rf"{re.escape(names[0])}(?:比{re.escape(names[1])})?(高|低|多|少)([+-]?\d+(?:\.\d+)?)分.{{0,5}}(?:对吗|是否正确|是不是)",
            question,
        )
        if assertion:
            asserted = float(assertion.group(2)) * (1 if assertion.group(1) in {"高", "多"} else -1)
            if round(asserted, 2) != round(float(difference), 2):
                if not re.search(r"不对|不正确|错误|并非|实际|应为|正确(?:为|是)", compact):
                    failures.append("comparison_assertion_correction_missing")
                if round(abs(asserted), 2) != round(abs(float(difference)), 2):
                    for echoed in re.finditer(rf"(?<![\d.]){re.escape(assertion.group(2))}(?![\d.])分", compact):
                        before = compact[max(0, echoed.start() - 24):echoed.start()]
                        after = compact[echoed.end():echoed.end() + 5]
                        negated_reference = re.search(
                            rf"(?:不是|并非|而非|不等于)(?:(?:{re.escape(names[0])}(?:比{re.escape(names[1])})?)|(?:{re.escape(names[1])}(?:比{re.escape(names[0])})?))?(?:高|低|多|少)?$",
                            before,
                        )
                        if not (negated_reference
                                or re.match(r"(?:不对|错误|不正确)", after)):
                            failures.append("comparison_assertion_wrong_magnitude_echo")
                            break
    if re.search(r"左(?:侧)?柱|右(?:侧)?柱|柱(?:子)?(?:间|之间)|图中.*(?:标注|左|右)|雷达|ASCII|六大维度|综合(?:分|均分)", compact):
        failures.append("dimension_compare_unobserved_chart_or_extra_scope_claim")
    return list(dict.fromkeys(failures))

def direct_answer_with_filter_scope(text, evidence, answer_spec):
    pass
    answer=str(text or '').strip()
    school_filter=str(evidence.get('school_filter') or '')
    if school_filter and school_filter!='all' and school_filter not in answer:
        sentence=(f'两期各按当年登记为{school_filter}的学校筛选，不是固定同校队列。'
                  if answer_spec.get('operation')=='region_longitudinal'
                  else f'本次仅含登记为{school_filter}的学校。')
        answer=' '.join([answer,sentence]).strip()
    return answer

def generate_semantic_answer(
    question: str,
    result: Dict[str, Any],
    answer_spec: Dict[str, Any],
    *,
    mode: str,
    expected_profile_id: Optional[str] = None,
    record_stage=None,
    mechanisms: Optional[Dict[str, bool | str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    mode = str(mode or "")
    if mode not in _LLM_MODES:
        raise ValueError("semantic_llm_mode_required")
    constrained = mode != "g1_llm_unconstrained"

    if answer_spec.get('semantic_plan') is not None:
        from packages.semantic_query.typed_plan import bind_types
        catalogue, _ = _catalogue()
        bind_types(answer_spec['semantic_plan'], {i['id']: i for i in catalogue}, emit=False)
    output_verification = constrained if mechanisms is None else bool(mechanisms["output_verification"])
    answer_repair = constrained if mechanisms is None else bool(mechanisms["answer_repair"])
    delivery_policy = mechanisms.get("delivery_policy", "block") if mode == "g2_llm_constrained" and mechanisms is not None else "block"
    from packages.semantic_query.answer_scope import project_result_scope

    evidence_result = project_result_scope(result, answer_spec, question)
    evidence = common_evidence_packet(evidence_result)
    if constrained:
        from packages.semantic_query.fact_assertions import constraint_inputs, executed_population_scope, executed_arithmetic_precision
        population_scope=executed_population_scope(result)
        if population_scope is not None:
            evidence['executed_population_scope']=population_scope
        precision=executed_arithmetic_precision(result) if evidence_result.get("diff") is not None else None
        if precision is not None:
            evidence['executed_arithmetic_precision']=precision
            evidence['difference_basis']=precision['basis']
        answer_spec,evidence=constraint_inputs(question,evidence,answer_spec)
    from packages.semantic_query.required_facts import required_facts
    evidence["required_facts"] = required_facts(evidence, answer_spec)
    from packages.semantic_query.unit_delivery import policy, only_unit_unknown, project_text, delivery_allowed
    unit_policy = policy(evidence, answer_spec)
    if _only_mean_and_school_count(question) and answer_spec.get("operation") == "region_eval":

        score = evidence.get("target_dimension_score") if answer_spec.get("granularity") == "dimension" else evidence.get("total_score")
        if score is None:
            score = evidence.get("total_score")
        count = evidence.get("school_count")
        if score is not None and count is not None:
            region = evidence.get("region_name") or evidence.get("short_name") or "该区域"
            year = evidence.get("year") or evidence.get("requested_year") or "所选年份"
            dimension = f"【{evidence['target_dimension']}】维度" if evidence.get("target_dimension") else "总体"
            evidence["direct_answer"] = f"{region}{year}年{dimension}学校等权均分为{score}分，有效学校数为{count}所。"
            evidence.pop("answer_count", None)
            evidence.pop("requested_answer_count", None)
            evidence.pop("conclusion", None)
    direct_additions: List[str] = []
    evidence['direct_answer']=direct_answer_with_filter_scope(evidence.get('direct_answer'),evidence,answer_spec)
    if "登记为0" in question or "登记0" in question:
        direct_additions.append("登记为0的题保留参与计算，NULL缺失值排除。")
    if answer_spec.get("operation") == "region_eval" and answer_spec.get("granularity") == "item" and evidence.get("question_id") == 59:
        direct_additions.append("这是Q59常态化应用题的标准化得分，不是教师比例或Q4技术使用能力分数。")
    if "教师培训" in question and evidence.get("target_dimension") == "教育教学":
        direct_additions.append("教育教学衡量课堂组织与考试评价；教师培训属于不同维度，不能代替本维度得分。")
    if answer_spec.get("operation") == "region_ranking" and evidence.get("ranking_items") and "题均分" in question:
        direct_additions.append("排名分数采用整体数字化题均分，不是学校模板加权分。")
    if answer_spec.get("operation") == "school_compare" and answer_spec.get("include_dimensions"):
        for row in evidence.get("dimensions_comparison") or []:
            if all(row.get(key) is not None for key in ("dimension", "score_a", "score_b")):
                direct_additions.append(f"{row['dimension']}：前校{row['score_a']}分，后校{row['score_b']}分。")
    if direct_additions:
        evidence["direct_answer"] = " ".join([str(evidence.get("direct_answer") or "").strip(), *direct_additions]).strip()
    from packages.llm_gateway.gateway import ChatRequest, call_detailed, route
    from packages.experiment_protocol.trace import stage_event

    selected = route("chat", fallback=False)
    if not selected:
        raise RuntimeError("experiment_llm_profile_unavailable")

    calls: List[Dict[str, Any]] = []

    def invoke(prompt: str, task: str) -> Dict[str, Any]:
        from time import perf_counter
        started = perf_counter()
        stage_event("writer_call_started", task=task, model=selected.get("model"), prompt=prompt)
        try:
            detail = call_detailed(ChatRequest(
                provider=selected["provider"],
                model=selected.get("model"),
                task=task,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=4096,
                expected_profile_id=expected_profile_id,
                max_attempts=1, transport_attempts=1, single_endpoint=True,
            ))
        except Exception as exc:
            stage_event("writer_call_failed", task=task, failure_type=type(exc).__name__,
                        usage_status="UNAVAILABLE", elapsed_ms=round((perf_counter() - started) * 1000, 2))
            raise
        stage_event("writer_call_completed", task=task, model=detail.get("model"),
                    content=detail.get("content"), usage=detail.get("usage") or {},
                    attempts=detail.get("attempts") or [],
                    elapsed_ms=round((perf_counter() - started) * 1000, 2))
        calls.append({
            "stage": task,
            "provider": detail.get("provider"),
            "model": detail.get("model"),
            "finish_reason": detail.get("finish_reason"),
            "usage": detail.get("usage") or {},
            "attempts": detail.get("attempts") or [],
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        })
        return detail

    first = invoke(
        _writer_prompt(question, evidence, answer_spec, constrained=constrained),
        "semantic_writer" if mode == "product_semantic_constrained" else "experiment_writer",
    )
    first_text = str(first.get("content") or "").strip()
    if record_stage is not None:
        record_stage("writer", "shared_semantic_writer")
    verification = (
        verify_answer_text(first_text, question, evidence, answer_spec, verify_new_constraints=constrained)
        if output_verification
        else {"status": "NOT_CHECKED_ABLATION_A2" if constrained else "NOT_APPLICABLE_UNCONSTRAINED", "checks": [], "failures": []}
    )
    stage_event("first_output_verification", verification=verification)
    final_text = first_text
    repaired = False
    first_verification = dict(verification)
    repair_prompt = None
    repair_text = None
    repair_delivery = {"first_validation": first_verification, "supplier_candidate": None,
                       "supplier_candidate_validation": None, "selection": None}
    if (delivery_policy != "warn" and answer_repair and output_verification and verification.get("status") in {"FAIL","UNCONFIRMED"}
            and not (unit_policy and only_unit_unknown(verification))):
        repair_prompt = _writer_prompt(
            question, evidence, answer_spec, constrained=True,
            repair={"text": first_text, "failures": verification.get("failures")},
        )
        repair = invoke(
            repair_prompt,
            "semantic_repair" if mode == "product_semantic_constrained" else "experiment_repair",
        )
        repair_text = str(repair.get("content") or "").strip()
        final_text = repair_text
        supplier_validation=verify_answer_text(repair_text,question,evidence,answer_spec,verify_new_constraints=constrained)
        repair_delivery.update(supplier_candidate=repair_text,supplier_candidate_validation=supplier_validation)
        stage_event("repair_supplier_candidate_validation",verification=supplier_validation)
        if constrained and repair.get('finish_reason')!='length':
            from packages.semantic_query.repair_delivery import minimal_repair
            final_text,selection=minimal_repair(first_text,repair_text,question,evidence,answer_spec,first_verification)
            repair_delivery['selection']=selection
        if record_stage is not None:
            record_stage("repair", "shared_semantic_writer")
        verification = verify_answer_text(final_text, question, evidence, answer_spec, verify_new_constraints=constrained)
        stage_event("repaired_output_verification", verification=verification)
        repaired = True

    unit_projection = None
    if unit_policy and final_text and (not output_verification or only_unit_unknown(verification)):
        before_projection = final_text
        final_text = project_text(final_text, unit_policy)
        unit_projection = dict(status='UNCONFIRMED', basis=unit_policy['reason'],
                               before=before_projection, after=final_text,
                               deferred_nodes=unit_policy['deferred_nodes'])

    if output_verification:
        verification = verify_answer_text(final_text, question, evidence, answer_spec, verify_new_constraints=constrained)
        stage_event('final_body_verification', verification=verification)

    last_detail=repair if repaired else first
    content_status={
        'status':'CONTENT' if final_text else 'EMPTY_CONTENT',
        'finish_reason':last_detail.get('finish_reason'),
        'content_chars':len(final_text),
        'length_exhausted':last_detail.get('finish_reason')=='length',
        'accepted_reasoning_as_answer':False,
    }
    stage_event('writer_content_delivery', **content_status)
    limited = delivery_allowed(verification, final_text, unit_policy)
    delivery_status = "ANSWER" if final_text and not content_status['length_exhausted'] and (verification.get("status") in {"PASS", "NOT_APPLICABLE_UNCONSTRAINED", "NOT_CHECKED_ABLATION_A2"} or limited) else "BLOCKED"
    public_text = final_text if delivery_status == "ANSWER" else ""
    delivery_notice = {"applied": False}
    if (delivery_policy == "warn" and output_verification
            and first_verification.get("status") in {"FAIL", "UNCONFIRMED"}
            and first_text and not content_status['length_exhausted'] and not unit_projection):
        codes = list(dict.fromkeys(first_verification.get("violations", []) + first_verification.get("inconclusive", [])))
        matched = {
            next(category for pattern, category in DELIVERY_NOTICE_CATEGORIES.items()
                 if fnmatchcase(code.removeprefix("unconfirmed:"), pattern))
            for code in codes
        }
        categories = [category for category in DELIVERY_NOTICE_CATEGORIES.values() if category in matched]
        count = ("零", "一", "两", "三", "四", "五", "六", "七")[len(categories)]
        notice_text = (f"【自动核对提示】以下说明中有{count}类内容未能自动核对：{'、'.join(categories)}。"
                       "具体数值请以上方数据卡片为准。")
        public_text = notice_text + "\n\n" + first_text
        delivery_status = "ANSWER"
        delivery_notice = {"applied": True, "categories": categories,
                           "first_verification_status": first_verification["status"], "codes": codes}
    repair_delivery.update(selected_candidate=final_text,selected_candidate_validation=verification,
                           final_delivery_status=delivery_status,delivered_text=public_text)
    stage_event('answer_delivery_decision',delivery_status=delivery_status,verification=verification,
                candidate_chars=len(final_text),delivered_chars=len(public_text))
    response = {
        "provider": first.get("provider") or selected["provider"],
        "model": first.get("model") or selected.get("model"),
        "text": public_text,
        "model_invoked": True,
        "is_fallback": False,
        "query_mode": mode,
        "delivery_status": delivery_status,
        "delivery_notice": delivery_notice,
        "answer_verification": verification,
        "repaired": repaired,
        "content_delivery": content_status,
        "unit_delivery": unit_projection,
        "delivery_completeness": "UNIT_UNRESOLVED" if unit_projection else "FULL" if delivery_status == "ANSWER" else "BLOCKED",
    }
    trace = {
        "common_path_version": COMMON_PATH_VERSION,
        "prompt_version": WRITER_PROMPT_VERSION,
        "answer_spec": answer_spec,
        "evidence_packet": evidence,
        "first_answer": first_text,
        "first_answer_chars": len(first_text),
        "first_answer_sha256": hashlib.sha256(first_text.encode("utf-8")).hexdigest(),
        "final_answer": public_text,
        "final_answer_chars": len(public_text),
        "final_answer_sha256": hashlib.sha256(public_text.encode("utf-8")).hexdigest(),
        "final_candidate_chars": len(final_text),
        "final_candidate_sha256": hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
        "final_delivery_status": delivery_status,
        "delivery_notice": delivery_notice,
        "verification": verification,
        "first_verification": first_verification,
        "repair_prompt": repair_prompt,
        "repair_answer": repair_text,
        "final_candidate": final_text,
        "repair_delivery": repair_delivery,
        "mechanisms": mechanisms,
        "calls": calls,
        "content_delivery": content_status,
        "unit_delivery": unit_projection,
        "runtime_mode": mode,
        "profile_bound": bool(expected_profile_id),
    }
    if mode == "product_semantic_constrained":
        for private_field in ("first_answer", "repair_prompt", "repair_answer", "final_candidate"):
            trace.pop(private_field, None)
        trace['repair_delivery']={key:value for key,value in repair_delivery.items()
            if key not in {'supplier_candidate','selected_candidate'}}
    if mode != "product_semantic_constrained":
        response["experiment_mode"] = mode
    return response, trace
