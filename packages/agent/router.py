import re
from typing import Any, Dict, List, Optional, Tuple

from packages.agent.indicator_catalog import match_indicator
from packages.core.national_scope import province_only_scope
from packages.semantic_query.catalogue import extract_explicit_question_ids, question_metric
from scripts.region_engine import detect_region

_TEACHER_RE = re.compile(r"\b(T\d{2,})\b")
_YEAR_RE = re.compile(r"20\d{2}-20\d{2}")
_CAL_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_SCHOOL_RE = re.compile(r"[\u4e00-\u9fff0-9A-Za-z]{2,40}(?:高级中学|初级中学|小学|中学|学校|幼儿园|学院|初中|高中|附中|附小|职中|职高|技校|分校|校区|[一二三四五六七八九十\d]+[中高小])")
_SCHOOL_ID_RE = re.compile(r"\b(?:S|学校|id:?)(\d{1,8})\b", re.I)

_SCHOOL_AGGREGATION_RE = re.compile(
    r"(?:按|对|以)(?:每所|各所|所有|全部)?学校|(?:每|各)(?:所)?(?:学校|校)|"
    r"(?:均分|平均|等权|汇总|加权|作答).*(?:学校|中小学)$"
)

_REGION_LONGITUDINAL_GENERIC_SCHOOL_SCOPE_RE = re.compile(
    r"(?:(?:有效|有数据|参与|纳入|覆盖|样本)(?:的)?)?学校(?:总)?"
    r"(?:数量|数目|数(?=$|[\s，,。？！?的量目为是有共分各多几较更高低大小呢吗变增减差]))"
    r"(?:的)?(?:变化|变动|增长|增加|下降|减少|差值|差异|对比|比较)?"
    r"|(?:同一批|同批|相同一批|固定一批|同样一批|同一组|同样的|相同的)学校"
    r"|同校(?:队列|追踪|样本)",
)

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

def _extract_years(text: str) -> List[str]:
    pass
    ranges = _YEAR_RE.findall(text)
    if ranges:
        return ranges

    affirmative = re.findall(r"(?:只接受|只看|只算|只选|仅看|只用|锁定|仅接受)\s*(20\d{2})年?", text)
    if affirmative:
        return [affirmative[0]]
    neg_years = set(re.findall(r"(?:不要用|不用|排除|不要|除开|去掉|剔除|除了|非)\s*(20\d{2})年?", text))
    cal = _CAL_YEAR_RE.findall(text)
    if cal:
        valid = [y for y in cal if y not in neg_years]
        if valid:
            return valid
        return cal

    if any(k in text for k in ("近三年", "近3年")):
        return ["2021", "2022", "2023"]
    if any(k in text for k in ("近两年", "近2年")):
        return ["2022", "2023"]
    if any(k in text for k in ("最近一年", "最新一年", "现有数据中最近一年", "最近可用年", "最新可用年", "现有数据中最新一年", "最近的一年", "最新的一年")):
        return ["latest"]
    return []

def _extract_aggregation(text: str) -> str:
    pass
    if any(k in text for k in (
        "作答放在一起平均", "合在一起平均", "所有作答合并平均", "全部作答放在一起平均",
        "全部题目的作答放在一起平均", "作答池化", "不要先按学校汇总", "不先按学校汇总",
        "不要先按学校", "不按学校汇总", "池化平均", "池化", "合在一起",
        "所有有效题项作答合并", "合并有效题项作答", "题项作答合并", "作答合并",
        "合并后计算平均值", "不先算每校均分", "合并全部有效作答",
    )) or re.search(r"(?<![A-Za-z])pool(?:ed|ing)?(?![A-Za-z])", text, re.I):
        return "answer_pooled_mean"
    return "school_equal_mean"

def _has_explicit_aggregation(text: str) -> bool:
    return any(k in text for k in (
        "作答放在一起平均", "合在一起平均", "所有作答合并平均", "全部作答放在一起平均",
        "全部题目的作答放在一起平均", "作答池化", "不要先按学校汇总", "不先按学校汇总",
        "不要先按学校", "不按学校汇总", "池化平均", "池化", "合在一起",
        "所有有效题项作答合并", "合并有效题项作答", "题项作答合并", "作答合并",
        "合并后计算平均值", "不先算每校均分", "合并全部有效作答",
        "学校等权", "每校等权", "按学校等权", "先按学校汇总", "各校均分再平均",
    )) or bool(re.search(r"(?<![A-Za-z])pool(?:ed|ing)?(?![A-Za-z])", text, re.I))

_AGGREGATION_SCHOOL_PHRASE_RE = re.compile(
    r"(?:(?:改回|改为|换成|换为|采用|使用|按照|按)\s*"
    r"(?:学校|每校|各校)\s*(?:等权|平均|汇总|聚合)"
    r"|(?:学校|每校|各校)\s*等权"
    r"|(?:每校|各校)\s*(?:平均|汇总|聚合))"
)

_RANKING_PATTERNS = [
    "排第几", "排名", "排位", "位次", "名次", "在区里", "在县里", "在市里",
    "全区平均", "全县平均", "全市平均", "平均水平相比", "对比全区", "对比全县",
]

_LEADERBOARD_PATTERNS = [
    "最好的学校", "标杆学校", "排名前", "前三", "前五", "前10", "前十",
    "排名靠前", "最薄弱", "最差的学校", "后五", "后三", "榜单",
]

_INVALID_SCHOOL_SUBSTRINGS = (
    "哪个学校", "哪所学校", "哪所小学", "哪所中学", "什么学校", "哪些学校",
    "哪家学校", "谁的学校", "哪所", "哪家", "哪个", "什么", "哪些",
    "好的学校", "差的学校", "标杆学校", "所有学校", "全部学校", "薄弱学校",
    "一所学校", "每个学校", "各学校", "该学校", "这所学校", "某学校",
    "的学校", "些学校", "类学校", "名学校", "位学校", "名是哪个", "名是哪所",
    "市学校", "省学校", "区学校", "县学校", "市的学校", "省的学校", "区的学校", "县的学校",
    "最高分学校", "最低分学校", "最高分学校是谁", "最低分学校是谁", "最高学校", "最低学校",
    "分数最高", "分数最低", "排名最高", "排名最后", "得分最高", "得分最低",
    "最后一名", "倒数第一", "排名最后的学校", "分数最低的学校", "分数最高的学校",
    "排第一的学校", "排最后的学校", "最后学校",
    "只看初中", "只看小学", "只看高中", "初中学校", "公办学校", "民办学校",
    "乡村学校", "城镇学校", "公办小学", "民办小学", "公办初中", "民办初中",
    "学校数量", "学校总数", "多少所学校", "学校数", "两所学校", "两校", "学校层面",
    "多少学校", "多少个学校", "多少间学校", "几所学校", "几个学校", "几间学校",
    "学校实现", "学校拥有", "学校利用", "学校开展", "学校能够", "学校管理", "学校建设", "学校推进",
    "学校主管", "学校是否", "学校统一", "学校数字化", "学校网络", "学校资产", "最近一年学校", "近一年学校",
    "平台的学校", "云平台的学校", "工作内容", "管理的工作内容",
    "哪三所学校", "哪两所学校", "哪四所学校", "哪五所学校", "哪十所学校", "哪几所学校",
    "同数时学校", "哪三所", "哪两所", "哪四所", "哪五所", "哪几所",
    "只回答学校", "回答学校", "有效学校", "参与学校", "覆盖学校",
)

_SCHOOL_FILTER_RE = re.compile(
    r"^(?:只纳入|纳入|仅包含|包含|只含|筛选|仅限|按|只看|只算|只统计|仅统计|统计)?(?:公办|民办|公立|私立|乡村|农村|镇区|城镇|城区|城市)?(?:小学|初中|高中|中学|幼儿园|中职|完全中学|九年一贯制|十二年一贯制|学校|中小学)$"
)

def _extract_school_filter(text: str) -> Tuple[Optional[str], str]:
    pass

    ranking_filter = re.search(
        r"(?:排名前|前|top\s*)\s*(?:\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|所|家|个)?\s*"
        r"(?P<filter_phrase>"
        r"(?:(?P<nature>公办|民办|公立|私立|乡村|农村|镇区|城镇|城区|城市)?\s*"
        r"(?P<stage>初中|初级中学|小学|高中|高级中学|中职|幼儿园|九年一贯制|十二年一贯制)(?:学校)?)"
        r"|(?P<nature_only>公办|民办|公立|私立|乡村|农村|镇区|城镇|城区|城市)(?:学校))",
        text,
        re.I,
    )
    if ranking_filter:
        nature = ranking_filter.group("nature") or ranking_filter.group("nature_only") or ""
        stage = ranking_filter.group("stage") or ""
        filter_name = re.sub(r"\s+", "", nature + stage)
        start, end = ranking_filter.span("filter_phrase")
        stripped_text = re.sub(r"\s+", " ", text[:start] + " " + text[end:]).strip()
        return filter_name, stripped_text

    m = re.search(
        r"(?:只纳入|纳入|仅包含|包含|只含|筛选|仅限|按|只看|只算|只统计|仅统计|统计)?\s*"
        r"(公办|民办|公立|私立|乡村|农村|镇区|城镇|城区|城市)?\s*"
        r"(?:学校)?\s*"
        r"(初中(?:学校)?|小学(?:学校)?|高中(?:学校)?|初级中学|高级中学|中职|幼儿园|九年一贯制|十二年一贯制|中小学)",
        text
    )
    if m:
        nature = m.group(1) or ""
        stage = m.group(2) or ""
        has_verb = bool(re.search(r"^(?:只纳入|纳入|仅包含|包含|只含|筛选|仅限|按|只看|只算|只统计|仅统计|统计)", m.group(0).strip()))
        if not nature and not has_verb:
            if re.search(r"(?:均分|平均分|得分|中位数|数量|水平|大盘|概况|是多少|分别多少)", text[m.end():]):
                stage = re.sub(r"学校$", "", stage)
                filter_name = (nature + stage).strip()
                stripped_text = text[:m.start()] + " " + text[m.end():]
                stripped_text = re.sub(r"\s+", " ", stripped_text).strip()
                return filter_name, stripped_text
        else:
            stage = re.sub(r"学校$", "", stage)
            filter_name = (nature + stage).strip()
            stripped_text = text[:m.start()] + " " + text[m.end():]
            stripped_text = re.sub(r"\s+", " ", stripped_text).strip()
            return filter_name, stripped_text

    m2 = re.search(
        r"(?:只纳入|纳入|仅包含|包含|只含|筛选|仅限|按|只看|只算|只统计|仅统计|统计)\s*"
        r"(公办|民办|公立|私立|乡村|农村|镇区|城镇|城区|城市|初中|初级中学|小学|高中|高级中学|中职|幼儿园|九年一贯制|十二年一贯制)(?:学校)?",
        text,
    )
    if m2:
        filter_name = m2.group(1)
        stripped_text = text[:m2.start()] + " " + text[m2.end():]
        stripped_text = re.sub(r"\s+", " ", stripped_text).strip()
        return filter_name, stripped_text

    m3 = re.search(r"(?:^|[，,、\s])(民办|公办|公立|私立|乡村|农村)(?:学校)(?:$|[，,、\s及和与])", text)
    if m3:
        filter_name = m3.group(1)
        stripped_text = text[:m3.start(1)] + " " + text[m3.end():]
        stripped_text = re.sub(r"\s+", " ", stripped_text).strip()
        return filter_name, stripped_text

    sample_filter = re.search(r"(乡村|农村|镇区|城镇|城区|城市)(?:样本|的学校)", text)
    if sample_filter:
        return sample_filter[1], text[:sample_filter.start()] + " " + text[sample_filter.end():]
    return None, text

STANDARD_MACRO_DIMENSIONS = [
    "数字资源",
    "教育教学",
    "数字素养",
    "基础设施",
    "教育治理",
    "保障机制",
]

_DIMENSION_ALIASES = {
    "数字资源": (
        "数字资源", "数字化资源", "数字教育资源", "数字教学资源", "资源数字化",
        "可供教学使用共享的数字内容建设及应用", "数字内容建设及应用", "数字教学资料",
        "供老师教学使用和共享的数字内容", "教学使用和共享的数字内容",
        "数字教学内容建设、来源和使用", "数字教学内容建设", "数字教学内容",
    ),
    "教育教学": (
        "教育教学", "数字化教学", "信息化教学", "智慧教学", "教学数字化",
        "把技术用于课堂与评价", "把数字技术用进课堂组织、学习活动和考试评价",
        "把技术用到上课和考试评价", "课堂组织、学习活动和考试评价",
        "课堂与评价", "上课和考试评价",
    ),
    "数字素养": (
        "数字素养", "数字能力", "信息素养", "数字化素养",
        "师生数字能力培养、培训与实践", "师生数字能力培养、老师培训和学生相关实践",
        "师生使用数字技术的能力培养，以及相关培训和实践参与",
        "师生数字能力培养", "师生数字能力", "数字能力培养",
    ),
    "基础设施": (
        "基础设施", "数字基建", "信息化基础设施", "数字化基础设施", "信息化基础",
        "设备、网络和智能学习场所", "设备、网络和智能学习空间",
        "设备、网络、智能学习场所", "设备网络和智能学习空间",
    ),
    "教育治理": (
        "教育治理", "数字治理", "信息化管理", "数字化管理", "治理数字化",
        "把校务、数据协同和校园安全放进数字系统来管理",
        "用数字系统处理校务、协同数据与校园安全",
        "校务、数据协同和校园安全", "校务、协同数据与校园安全",
    ),
    "保障机制": (
        "保障机制", "制度保障", "支撑保障", "数字化保障", "保障体系",
        "人员、组织和资金支撑", "为数字化工作配人员、安排组织机制和资金支持",
        "人员组织和资金支撑", "人员、组织和资金支持",
    ),
}

def _extract_dimensions_and_stat(text: str) -> Tuple[List[str], Optional[str]]:
    pass
    valid_dims = []
    for d in STANDARD_MACRO_DIMENSIONS:
        spans = [alias for alias in _DIMENSION_ALIASES.get(d, (d,)) if alias in text]
        if spans:
            span = max(spans, key=len)
            escaped_span = re.escape(span)
            negated = bool(
                re.search(rf"(?:先前说|先前|之前说|不是|不要|不用|排除|除开|去掉|剔除|除了|不展开|不包含|不纳入|先不看|不看)\s*{escaped_span}\s*(?:不对|错误|不要|不行)?", text)
                or re.search(rf"{escaped_span}\s*(?:不对|错误|不要|不行|先不看|不看|先不展开|不展开)", text)
            )
            if not negated:
                valid_dims.append((d, min(text.find(item) for item in spans)))

    valid_dims.sort(key=lambda item: item[1])
    valid_dims = [item[0] for item in valid_dims]
    alias_to_dimension = {
        alias: dimension
        for dimension, aliases in _DIMENSION_ALIASES.items()
        for alias in aliases
    }
    alias_pattern = "|".join(
        re.escape(alias) for alias in sorted(alias_to_dimension, key=len, reverse=True)
    )
    m_emphasis = re.search(
        rf"(?:这次要|改为|改成|换成|换为|只看|强调|重点看)\s*({alias_pattern})",
        text,
    )
    if m_emphasis:
        emp_dim = alias_to_dimension[m_emphasis.group(1)]
        if emp_dim in valid_dims:
            valid_dims.remove(emp_dim)
            valid_dims.insert(0, emp_dim)

    target_dims = valid_dims

    target_stat = None
    has_median = any(k in text for k in ("中位数", "中位值", "中间位置", "正中间", "中间的那所", "排在正中间", "排在中间")) or bool(
        re.search(r"(?<![A-Za-z])median(?![A-Za-z])", text, re.I)
    )
    has_mean = any(k in text for k in ("平均分", "均分", "平均得分", "平均值", "平均数", "均值", "整体是多少分", "多少分"))
    has_both_mean_median = (
        (has_median and has_mean)
        or any(k in text for k in (
            "均值/中位数", "均值与中位数", "均值和中位数", "均值及中位数", "均值中位数",
            "平均值与中位数", "平均值和中位数", "平均值及中位数",
            "均分与中位数", "均分和中位数", "均分及中位数",
            "平均数与中位数", "平均数和中位数",
            "中位数与均值", "中位数和均值", "中位数与均分", "中位数和均分",
            "中位数与平均值", "中位数和平均值",
        ))
    )
    has_count = any(k in text for k in (
        "学校数量", "学校总数", "多少所学校", "共有多少所", "共多少所",
        "有效学校数量", "有效学校数", "学校数量变化", "学校数变化",
        "各有多少所", "各有多少家", "各有几所", "分别有多少所", "各自有多少所",
        "各有多少学校", "各有多少", "有多少所", "有几所学校", "有多少学校", "多少所", "几所学校",
        "多少学校", "多少个学校", "多少间学校", "几个学校", "几间学校",
        "样本规模", "样本量", "参与计算的记录", "参与整体计算的学校", "哪组样本多", "哪边学校更多",
        "哪边实际参", "覆盖多少所学校", "有多少所有效学校", "有多少所学校参与计算", "依据了多少所学校",
    )) or bool(re.search(r"学校数(?=$|[\s，,。？！?]|[量目为是有共分各多几较更高低大小呢吗变增减差])", text))

    if has_both_mean_median:
        target_stat = "mean_and_median"
    elif ("最高" in text and "最低" in text) or ("最高分" in text and "最低分" in text) or ("高端" in text and "低端" in text):
        target_stat = "min_and_max"
    elif any(k in text for k in ("每所学校算一票", "每所学校一票")) and any(k in text for k in ("有效作答算一票", "每条有效作答算一票")):
        target_stat = "mean"
    elif any(k in text for k in ("合并有效题项作答", "题项作答合并", "作答合并", "作答池化", "合并后计算平均值", "不先算每校均分", "合并全部有效作答")):
        target_stat = "mean"
    elif has_mean:
        target_stat = "mean"
    elif has_median:
        target_stat = "median"
    elif any(k in text for k in ("最高分学校是谁", "最高分学校", "分数最高", "得分最高", "最高分", "最高得分", "第一名得分")):
        target_stat = "max"
    elif any(k in text for k in ("最低分学校是谁", "最低分学校", "分数最低", "得分最低", "排名最后", "倒数第一", "最后一名", "排名末位", "最低分", "最低得分", "末位得分")):
        target_stat = "min"
    elif has_count:
        target_stat = "count"

    return target_dims, target_stat

def _extract_dimension_and_stat(text: str) -> Tuple[Optional[str], Optional[str]]:
    pass
    dims, stat = _extract_dimensions_and_stat(text)
    return (dims[0] if dims else None), stat

_CN_NUM_MAP = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
}

def _parse_cn_or_digit(s: str) -> Optional[int]:
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s in _CN_NUM_MAP:
        return _CN_NUM_MAP[s]
    return None

_PREFIX_PAT = re.compile(
    r"^(?:那|那么|如果|若是|若|请问|请帮我|帮我|我想|我要|我想知道|我想了解|我想看|请评估|我评估|评估一下|评估|查询|查一下|查看|对比一下|对比|比较一下|比较|看看|分析一下|分析|测算|"
    r"不要用\s*20\d{2}\s*年?[，,]?|不用\s*20\d{2}\s*年?[，,]?|请给出|请列出|给出|列出|换到|换成|切换到|"
    r"沿用刚才的年份和维度[，,]?|沿用刚才的年份[，,]?|沿用刚才的维度[，,]?|沿用[，,]?|其他不变[，,]?|"
    r"只接受\s*20\d{2}\s*年?[，,]?|只接受|只看|只算|只统计|仅统计|统计|算算|算|请问下|求|求解|求取|求下)+",
    re.I
)
_CHART_INSTRUCTION_RE = re.compile(
    r"[，,]?\s*(?:用图展示|以图展示|以图表展示|通过图表展示|通过图展示|用图表展示|画图展示|画图对比|画图|生成图表|用图)[^，。？！]*"
)
_SUFFIX_PAT = re.compile(
    r"(?:教育)?(?:数字化发展水平|数字化水平如何|数字化水平怎样|数字化水平怎么样|数字化水平|数字化发展|数字化|"
    r"哪个数字化水平高|谁数字化水平高|哪个数字化水平更好|谁数字化水平更好|数字化水平高|数字化水平好|"
    r"水平如何|水平怎样|水平怎么样|哪个更好|哪个更高|哪个好|谁好|谁更高|谁更强|谁发展好|谁的数字化|怎么样|如何|好不好|"
    r"谁更好|哪家好|哪个强|比一比|比一下|对比|比较|比对|相比|对照|比|的得分|得分|成绩|排名|排位|位次|差异|差距|"
    r"的整体数字化均分|整体数字化均分|数字化均分|数字化综合分|大盘均分|均分是多少|均分|平均分|中位数是多少|中位数|"
    r"维度的平均得分是多少|维度的平均得分|平均得分是多少|平均得分)+[呢吧吗呀？\?。！\!]*$",
    re.I
)
_REGION_COMPARE_SCOPE_SUFFIX_RE = re.compile(
    r"(?:(?:数字资源|教育教学|数字素养|基础设施|教育治理|保障机制)(?:维度)?|"
    r"(?:区域)?(?:教育)?数字化(?:发展)?(?:综合)?(?:水平)?|综合|整体数字化|数字化监测|数字化整体计算|数字化)"
    r"(?:的)?(?:综合)?(?:学校等权均分|等权均分|学校等权|大盘均分|均分|平均分|平均得分|得分|水平|监测)?$",
    re.I,
)
_COMPARE_PATTERNS = (
    "对比", "比较", "相比较", "相比", "相较于", "相较", "比一比", "比一下", "比呢",
    "相对而言", "相对于", "相对", "相比于", "对照", "比之", "比上", "比于", "对比于",
    "谁更好", "哪个更高", "哪个好", "谁更高", "谁更强", "谁发展好", "谁的数字化", "哪所更好", "哪所学校好",
    "哪个数字化水平高", "谁数字化水平高", "哪个数字化水平更好", "谁数字化水平更好", "数字化水平高", "数字化水平好",
    "pk", "PK", "vs", "VS"
)
_DELIM_RE = re.compile(
    r"(?:相对而言|相较而言|相比较|相较于|相比于|相对于|对比于|比一比|比一下|谁更好|哪个更高|哪个好|谁更高|谁更强|哪个数字化水平高|谁数字化水平高|(?:只|仅|并且|并|且)?(?:比较|对比|相比|相较|相对|对照|比之|比上|比于)|\bvs\b|\bpk\b|与|和|跟|(?<![大协])同(?![济仁学步时行样])|、|,|，)",
    re.I
)
_SCHOOL_PAIR_CONNECTOR_RE = re.compile(
    r"(?:学校|小学|中学|高中|初中|附中)\s*(?:和|与|跟)\s*"
    r"[^，。？！?]{2,}(?:学校|小学|中学|高中|初中|附中)",
    re.I,
)

def _extract_rank_info(text: str) -> Tuple[Optional[int], Optional[int]]:
    pass

    if any(k in text for k in (
        "最后一名", "倒数第一", "倒数第1", "倒数第一名", "排名最后", "排最后", "末位",
        "最低分学校", "分数最低的学校", "排名末位", "最后一所", "最后一家", "最后的一所", "末位学校", "倒数第一所",
    )):
        return -1, None
    if any(k in text for k in ("榜首", "最好的学校", "最强学校", "领跑学校", "最高分学校", "分数最高的学校", "第一名", "排第一", "排名第一", "排在第一")):
        return 1, None

    m_top = re.search(
        r"(?:排在最前面的|排名前|前|top\s*)\s*(\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|所|所学校|家|个)?",
        text,
        re.I,
    )
    if not m_top:
        m_top = re.search(
            r"(?:哪|最前|排在最前)\s*(\d+|[一二两三四五六七八九十]{1,3})\s*(?:所|所学校|家|名|个).*(?:最多|最高|最好|最强)",
            text,
            re.I,
        )
    if m_top:
        num = _parse_cn_or_digit(m_top.group(1))
        if num and num > 0:
            return None, num

    m_rank = re.search(r"(?:排名?第|位列第|位居第|排第)\s*(\d+|[一二两三四五六七八九十]{1,3})", text)
    if m_rank:
        num = _parse_cn_or_digit(m_rank.group(1))
        if num and num > 0:
            return num, None

    m_rank2 = re.search(r"第\s*(\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|位|名次)", text)
    if m_rank2:
        num = _parse_cn_or_digit(m_rank2.group(1))
        if num and num > 0:
            return num, None

    m_colloquial = re.search(r"第\s*(\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|位)?\s*(?:是|为|算|算作)?\s*(?:哪|谁|什么)", text)
    if m_colloquial:
        num = _parse_cn_or_digit(m_colloquial.group(1))
        if num and num > 0:
            return num, None

    return None, None

def _ranking_evidence_span(
    text: str,
    *,
    target_rank: Optional[int],
    target_top_n: Optional[int],
) -> Optional[str]:
    if target_rank == -1:
        return next((token for token in (
            "最后一名", "倒数第一", "倒数第1", "倒数第一名", "排名最后", "排最后", "末位",
            "最低分学校", "分数最低的学校", "排名末位",
        ) if token in text), None)
    if target_rank is not None:
        match = re.search(
            r"(?:排名?第|位列第|位居第|排第|第)\s*(?:\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|位|名次)?",
            text,
        )
        if match:
            return match.group(0)
        return next((token for token in (
            "榜首", "最好的学校", "最强学校", "领跑学校", "最高分学校", "分数最高的学校", "第一名", "排第一", "排名第一", "排在第一",
        ) if token in text), None)
    if target_top_n is not None:
        match = re.search(
            r"(?:排名前|前|top\s*)\s*(?:\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|所|所学校|家|个)?",
            text,
            re.I,
        )
        return match.group(0) if match else None
    return None

def _is_pure_region(s: str) -> bool:
    pass
    if not s or len(s) < 2:
        return False

    if re.search(r"(?:高级中学|初级中学|实验学校|外国语学校|附中|附小|幼儿园|学院)$", s):
        return False
    if re.search(r"(?<!中小)学$", s) and not re.search(r"^(?:全|各类?|各级|所有|全部|全市|全区|全县|全省|按)", s):
        return False

    if re.search(r"(?:学校|中小学)$", s):
        try:
            from scripts.school_engine import find_schools
            exact_schools = find_schools(s, limit=3)
            if any(sc.get("school_name") == s for sc in exact_schools):
                return False
        except Exception:
            pass

    c = re.sub(r"^(?:把|给|在|对|对于|将|从|自|按|全|各个?|各级|各类|所有|全部)?", "", s).strip()
    c = re.sub(r"(?:的)?(?:所有|全部|各个?|各类|各级|全区|全市|全省)?(?:中小学|学校|中小学校)+$", "", c).strip()
    c = c.rstrip("的").strip()
    if not c or len(c) < 2:
        return False
    reg = detect_region(c)
    if not reg or not reg.get("name"):
        return False
    rn = reg["name"]

    if c == rn or c in (reg.get("province"), reg.get("city"), reg.get("district")):
        return True
    reg_names = [reg.get("name"), reg.get("province"), reg.get("city"), reg.get("district")]
    reg_names = [r for r in reg_names if r]
    if c in reg_names:
        return True
    c_core = re.sub(r"(省|市|区|县|旗|州|盟)+$", "", c)
    for rn in reg_names:
        rn_core = re.sub(r"(省|市|区|县|旗|州|盟)+$", "", rn)
        if c_core and rn_core and c_core == rn_core:
            return True
    return False

def _clean_region_token(s: str) -> Optional[str]:
    pass
    s = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", s.strip())
    raw_s = s.strip()
    s = _CHART_INSTRUCTION_RE.sub("", s).strip()
    s = _PREFIX_PAT.sub("", s).strip()

    s = re.sub(r"(?:在|于)?\s*20\d{2}\s*年?", "", s).strip()

    for _ in range(6):
        prev = s
        s = re.sub(r"[呢吧吗呀？\?。！\!，,、\s\"'“”‘’【】《》（）()\[\]]+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:各|分别|各自|一共|共)?(?:有|纳入|包含|覆盖)?(?:多少|几)?(?:所|家)?(?:中小学|学校|中小学校|校)?(?:数量|总数|校数|所数)?(?:被)?(?:纳入|进入|列入|参与|参加)?(?:到|进)?(?:数字化)?(?:监测|评估|测评|视导)?(?:样本库|库|名单)?(?:分别是多少|是多少|多少)?$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:被)?(?:纳入|进入|列入|参与|参加)?(?:到|进)?(?:数字化)?(?:监测|评估|测评|视导)?(?:样本库|库|名单)?$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:各|分别|各自)?(?:有|纳入|包含)?(?:多少所|几所|多少家|几家|多少|几|几个)?(?:中小学|学校|中小学校|校)?(?:被)?(?:纳入|进入|列入|参与|参加)?(?:到|进)?(?:数字化)?(?:监测|评估|测评|视导)?(?:样本库|库|名单)?$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:各所学校|各学校|各个学校|所有学校|全部学校|各校|各级学校|各类学校)?(?:数量|总数|校数|所数|所|家)?(?:被)?(?:纳入|进入|列入|参与|参加)?(?:到|进)?(?:数字化)?(?:监测|评估|测评|视导)?$", "", s).strip()
        s = s.rstrip("的").strip()
        s = _REGION_COMPARE_SCOPE_SUFFIX_RE.sub("", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:进行|开展|展开|做|比一比|比一下|对比|比较|对照|相对|相比|相较)+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = _SUFFIX_PAT.sub("", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", s.strip())
        if s == prev:
            break

    stem = re.sub(r"(?:各级|各类|所有|全部|各个?)?(?:中小学|学校|中小学校)?(?:数量|总数|校数|所数|所|家)?$", "", s).strip()
    stem = stem.rstrip("的").strip()
    stem = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", stem.strip())
    if not stem or len(stem) < 2:
        return None

    if re.search(r"(?:高级中学|初级中学|实验学校|小学|中学|幼儿园|学院|初中|高中|附中|附小|学校)$", raw_s):
        if not re.search(r"^(?:各级|各类|所有|全部|各个?|全区|全市|全县|全省|按)", raw_s):
            try:
                from scripts.school_engine import find_schools
                exact_schools = find_schools(raw_s, limit=3)
                if any(sc.get("school_name") == raw_s for sc in exact_schools):
                    return None
            except Exception:
                pass

    if _is_pure_region(stem):
        reg = detect_region(stem)
        if reg and reg.get("name"):
            return reg["name"]
    return None

def _extract_comparison_regions(text: str, context: Optional[Dict[str, Any]] = None) -> Optional[Tuple[str, str]]:
    pass
    ctx = context or {}
    last_region = ctx.get("last_region")

    if any(k in text for k in ("换到", "切换到", "转到")) and not any(cp in text for cp in _COMPARE_PATTERNS):
        return None

    clean_for_comp = text.replace("年份和维度", "").replace("维度和年份", "").replace("指标和维度", "")
    has_comp = (
        any(p in text for p in _COMPARE_PATTERNS)
        or ("和" in clean_for_comp and any(s in clean_for_comp for s in ("市", "省", "区", "县", "地区")))
        or (bool(last_region) and any(p in clean_for_comp for p in ("比", "对比", "比较", "相比", "与", "跟")))
    )
    if not has_comp:
        return None

    cleaned_text = _CHART_INSTRUCTION_RE.sub("", text)
    parts = _DELIM_RE.split(cleaned_text)
    regions = []
    for p in parts:
        r_name = _clean_region_token(p)
        if r_name:
            regions.append(r_name)

    if len(regions) >= 2:
        return regions[0], regions[1]

    years = _extract_years(text)
    has_longitudinal_hint = len(years) >= 2 or any(k in text for k in ("变化", "趋势", "提升", "增长", "下降", "变动", "回落", "走势", "演变"))
    if has_longitudinal_hint:
        return None

    has_school_hint = any(k in text for k in ("学校", "小学", "中学", "附中", "高中", "初中", "两所", "校"))
    if len(regions) == 1 and last_region and last_region != regions[0] and not has_school_hint:
        return last_region, regions[0]
    return None

def _clean_school_token(s: str) -> str:
    pass
    s = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", s.strip())
    s = _CHART_INSTRUCTION_RE.sub("", s).strip()
    s = _PREFIX_PAT.sub("", s).strip()
    s = re.sub(r"^(?:把|给|在|对|对于|将|从|自|请问|请查|请)?", "", s).strip()
    s = re.sub(r"(?:20\d{2}\s*年?(?:度)?的学校|20\d{2}\s*年?(?:度)?学校|的学校)+$", "", s).strip()
    s = re.sub(r"20\d{2}\s*年?", "", s).strip()

    for _ in range(6):
        prev = s
        s = re.sub(r"[呢吧吗呀？\?。！\!，,、\s\"'“”‘’【】《》（）()\[\]只仅并且]+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:(?:的学校|20\d{2}\s*年?的学校)?(?:数字化)?(?:模板)?(?:综合分各是多少|综合分及差值|综合分|学校等权均分|等权均分|大盘均分|数字化综合分|表现|情况|得分|分差|差距|各是多少|是多少|画柱状图|画图|说明分差|给出两地有效学校数|两地学校数|两校分差|差值))+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:在)?(?:数字资源|教育教学|数字素养|基础设施|教育治理|保障机制)(?:维度)?(?:得分)?$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:进行|开展|展开|做|做个|来|来看)?(?:各级|各类|两所|两校|各所|各个|这两所|那两所)?(?:学校层面|层面|学校|校级|综合|各维度|维度)?(?:的)?(?:对比|比较|对照|分析|比对|比一比|比下|相对|相比|相较)+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:进行|开展|展开|做|做个|来)+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = _SUFFIX_PAT.sub("", s).strip()
        s = re.sub(r"[呢吧吗呀？\?。！\!，,、\s\"'“”‘’【】《》（）()\[\]只仅并且]+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:两所学校|两所|两校|两所中小学|学校层面|层面|对比|比较|相对|对照|相对而言|相比之下|相较而言|相比|相较|各所学校|各校)+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:在)?(?:谁的|谁|哪个学校的|哪所学校的|哪个|哪所)?(?:数字化发展水平|数字化水平|数字化表现|数字化发展|数字化情况|数字化大盘|数字化方面|数字化能力|数字化|表现|情况|差距|差异|差别|特征|成效)+$", "", s).strip()
        s = s.rstrip("的").strip()
        s = re.sub(r"(?:哪个更好|哪个更高|哪个好|谁好|谁更高|谁更强|谁发展好|谁更胜一筹|哪所更好|哪所学校好|哪所好|哪家好|哪个强|哪个高|谁高|如何|怎样|怎么样|好不好)+$", "", s).strip()

        s = re.sub(r"(?:在|于)$", "", s).strip()
        s = s.rstrip("谁").rstrip("哪").strip()
        s = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", s.strip())
        if s == prev:
            break

    s = re.sub(r"^(?:比较|对比|请比较|请对比|探讨|分析)+", "", s).strip()
    s = s.rstrip("的").strip()
    s = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", s.strip())
    return s

def _extract_comparison_schools(text: str, context: Optional[Dict[str, Any]] = None) -> Optional[Tuple[str, str]]:
    pass
    ctx = context or {}
    last_school = ctx.get("last_school")
    has_comp = any(p in text for p in _COMPARE_PATTERNS) or bool(
        _SCHOOL_PAIR_CONNECTOR_RE.search(text)
    )
    if not has_comp:
        return None

    cleaned_text = _CHART_INSTRUCTION_RE.sub("", text)
    cleaned_text = re.sub(r"^(?:比较|对比|请比较|请对比|探讨|分析)+", "", cleaned_text).strip()
    parts = _DELIM_RE.split(cleaned_text)
    schools = []
    for p in parts:
        s = _clean_school_token(p)
        if len(s) >= 3 and not any(bad in s for bad in _INVALID_SCHOOL_SUBSTRINGS) and not _SCHOOL_FILTER_RE.match(s):
            if not _is_pure_region(s):
                if any(bad in s for bad in ("两地学校数", "哪边学校更多", "哪边实际参", "学校等权均分", "教育治理维度", "用柱状图展示分数", "画柱状图", "说明分差")):
                    continue
                is_school_like = bool(re.search(r"(?:高级中学|初级中学|实验学校|外国语学校|附中|附小|小学|中学|学校|幼儿园|学院|初中|高中|分校|校区|[一二三四五六七八九十\d]+[中高小])$", s))
                if not is_school_like:
                    try:
                        from scripts.school_engine import find_schools
                        hits = find_schools(s, limit=1)
                        is_school_like = bool(hits)
                    except Exception:
                        pass
                if is_school_like:
                    schools.append(s)

    if len(schools) == 2:
        return schools[0], schools[1]
    if len(schools) >= 3:
        return None
    if len(schools) == 1 and last_school and last_school != schools[0]:
        return last_school, schools[0]
    return None

def route_query(text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pass
    q_text = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", text.strip())

    if any(k in q_text for k in (
        "数学考试成绩", "语文考试成绩", "英语考试成绩", "学生数学考试", "学生学科成绩",
        "期末考试成绩", "学生考试成绩", "数学成绩", "考试成绩", "统考成绩", "学生数学"
    )):
        is_negated_or_indicator = (
            bool(re.search(r"(?:不用|不要|不|无需|排除|免于|切勿|别)\s*(?:评价|统计|计算|看|管|包括|含)?\s*考试成绩", q_text))
            or ("标准化分数" in q_text and "考试成绩" in q_text)
            or ("呈现考试" in q_text)
        )
        if not is_negated_or_indicator:
            return {
                "intent": "clarify",
                "reason": "unsupported_metric",
                "missing": ["需补充澄清：本系统仅监测学校教育数字化发展相关指标（共83项），未收录学生学科统考（如数学考试成绩）相关数据。"],
                "original": q_text,
            }

    if any(k in q_text for k in (
        "删除所有零值", "先删除所有零值", "删除零值", "先删除零值", "剔除零值", "过滤所有零值", "过滤零值", "剔除所有零值"
    )):
        return {
            "intent": "clarify",
            "reason": "unsupported_rule",
            "missing": ["需补充澄清：本系统按照国家教育数字化监测标准严格执行「保留零值、排除缺失值」的统计口径，暂不支持删除零值重新测算。"],
            "original": q_text,
        }

    has_national = any(k in q_text for k in ("全国", "各省", "所有省份", "全省", "所有省", "全国各省"))
    has_ranking_keyword = any(k in q_text for k in (
        "最好", "最高", "排名", "排第", "第一", "领先", "优劣", "名次", "前几", "最差", "榜首", "榜单",
        "谁最好", "哪个最好", "哪所最好", "哪个省", "哪所省", "哪些省", "水平最高", "大盘最高", "排行榜",
        "数字化水平", "数字化发展水平", "发展最好", "表现最好", "表现最优", "大盘"
    ))
    has_scope_entity = any(k in q_text for k in ("省", "省份", "地区", "全国", "各省"))
    if (has_national or any(k in q_text for k in ("各省", "哪个省", "哪个省份", "哪所省", "全国各省"))) and has_ranking_keyword and has_scope_entity:
        years = _extract_years(q_text)
        target_dimensions, _ = _extract_dimensions_and_stat(q_text)
        target_dimension = target_dimensions[0] if target_dimensions else None
        target_rank, target_top_n = _extract_rank_info(q_text)
        return {
            "intent": "national_ranking",
            "year": years[0] if years else None,
            "target_dimension": target_dimension,
            "target_dimensions": target_dimensions,
            "top_n": target_top_n or 15,
            "target_rank": target_rank,
            "province_only": province_only_scope(q_text),
            "original": q_text,
        }

    clear_school_filter = any(k in q_text for k in (
        "取消筛选", "取消过滤", "不再筛选", "不再过滤", "去掉筛选", "去掉过滤",
        "不限学段", "不限城乡", "全部学校", "所有学校",
    ))
    extracted_filter, q_text_no_filter = _extract_school_filter(q_text)
    if extracted_filter and not clear_school_filter:
        try:
            from packages.analysis_contract.capabilities import validate_region_request
            validate_region_request(
                statistic=None,
                aggregation="school_equal_mean",
                school_filter=extracted_filter,
            )
        except ValueError as exc:
            return {
                "intent": "clarify",
                "reason": "unsupported_filter_metadata",
                "missing": [str(exc).split(":", 1)[-1]],
                "original": q_text,
            }
    years = _extract_years(q_text)
    active_year = years[0] if years else None
    aggregation = _extract_aggregation(q_text)
    aggregation_explicit = _has_explicit_aggregation(q_text)

    ctx = context or {}
    last_school = ctx.get("last_school")
    last_region = ctx.get("last_region")
    last_region_compare_a = ctx.get("last_region_compare_a")
    last_region_compare_b = ctx.get("last_region_compare_b")
    last_region_longitudinal_period_a = ctx.get("last_region_longitudinal_period_a")
    last_region_longitudinal_period_b = ctx.get("last_region_longitudinal_period_b")
    last_target_rank = ctx.get("last_target_rank")
    last_target_top_n = ctx.get("last_target_top_n")
    last_school_compare_a = ctx.get("last_school_compare_a")
    last_school_compare_b = ctx.get("last_school_compare_b")
    last_teacher = ctx.get("last_teacher")
    last_year = ctx.get("last_year")
    last_dimension = ctx.get("last_dimension")
    last_statistic = ctx.get("last_statistic")
    last_aggregation = ctx.get("last_aggregation")
    last_school_filter = ctx.get("last_school_filter")
    last_school_fact_entity = ctx.get("last_school_fact_entity")
    last_school_fact_metric = ctx.get("last_school_fact_metric")

    target_rank, target_top_n = _extract_rank_info(q_text)
    ranking_target_explicit = target_rank is not None or target_top_n is not None
    raw_top_n = re.search(
        r"(?:排名前|前|top\s*)\s*(\d+)\s*(?:名|所|所学校|家|个)?",
        q_text,
        re.I,
    )
    invalid_top_n = (
        re.search(
            r"(?:排名前|前|top\s*)\s*(?:零|0|[-−－负]\s*(?:\d+|[一二两三四五六七八九十]{1,3}))"
            r"\s*(?:名|所|所学校|家|个)?",
            q_text,
            re.I,
        )
        or (raw_top_n if raw_top_n and not 1 <= int(raw_top_n.group(1)) <= 100 else None)
    )
    invalid_exact_rank = re.search(
        r"第\s*(?:零|0|[-−－负]\s*(?:\d+|[一二两三四五六七八九十]{1,3}))"
        r"\s*(?:名|位|名次)",
        q_text,
    )
    if invalid_top_n or invalid_exact_rank:
        return {
            "intent": "clarify",
            "reason": "invalid_region_ranking_target",
            "missing": ["排名目标必须为正整数；Top N 首批最多支持100名"],
            "original": q_text,
        }
    ranking_evidence_span = _ranking_evidence_span(
        q_text,
        target_rank=target_rank,
        target_top_n=target_top_n,
    )
    target_dimensions, target_stat = _extract_dimensions_and_stat(q_text)
    target_dimension = target_dimensions[0] if target_dimensions else None
    target_stat_explicit = target_stat is not None
    target_dimension_explicit = target_dimension is not None
    school_filter = None if clear_school_filter else extracted_filter

    has_school_pronoun = bool(re.search(r"(它|该校|这所学校|该学校|此校|该校的)", q_text))
    has_school_pair_pronoun = bool(re.search(r"(这两所学校|两所学校|这两校|两校|二者|它们)", q_text))
    has_region_pronoun = bool(re.search(r"(该地区|该县|该区|该市|这个地区|这片区域)", q_text))
    has_teacher_pronoun = bool(re.search(r"(?:该教师|这位老师|该老师|该教师的|这位老师的|该老师的|他的|她的|(?<![其吉利])(?:他|她)(?![们人]))", q_text))

    has_followup_particle = bool(re.match(r"^那[个些类一]?", q_text)) or q_text.endswith(("呢", "呢？", "呢?"))
    has_explicit_context_ref = (
        bool(ctx.get("pending_clarification_active")) or
        clear_school_filter or
        has_school_pronoun or has_school_pair_pronoun or has_region_pronoun or has_teacher_pronoun or has_followup_particle or
        any(k in q_text for k in (
            "其他不变", "换到", "沿用", "那第三名", "那第二名", "那第一名", "刚才", "上一轮", "之前",
            "不要用", "不用", "排除", "只看", "只接受", "仅看", "只算", "只用",
            "其他要求保持", "其他保持", "其余保持", "其余不变", "保持不变", "要求保持",
            "刚才的地区", "刚才的年份", "刚才的维度", "刚才的地区和年份", "刚才年份和维度", "维度和城市还是原来的",
            "仍是刚才", "还是原来的", "原要求", "原来要求", "刚才地区", "刚才年份",
            "换成", "换为", "改成", "改为", "改看", "转成", "替换为", "只换", "只改", "最后只换", "最后"
        ))
    )
    if not active_year and last_year and has_explicit_context_ref:
        active_year = last_year
    explicit_dimension_clear = any(k in q_text for k in ("全部83项", "全部指标", "整体", "综合均分"))
    if not target_dimension and last_dimension and has_explicit_context_ref and not explicit_dimension_clear:
        target_dimension = last_dimension
        if target_dimension and target_dimension not in target_dimensions:
            target_dimensions.append(target_dimension)
    if target_stat is None and last_statistic is not None and has_explicit_context_ref:
        target_stat = last_statistic
    if (
        target_rank is None
        and target_top_n is None
        and not target_stat_explicit
        and not target_dimension_explicit
        and not explicit_dimension_clear
        and has_explicit_context_ref
    ):
        target_rank = last_target_rank
        target_top_n = last_target_top_n
    if not aggregation_explicit and last_aggregation and has_explicit_context_ref:
        aggregation = last_aggregation
    if extracted_filter is None and not clear_school_filter and last_school_filter and has_explicit_context_ref:
        school_filter = last_school_filter

    teachers = _TEACHER_RE.findall(q_text)
    if teachers:
        return {
            "intent": "teacher_eval",
            "teacher_id": teachers[0],
            "years": years or ["2023-2024", "2024-2025"],
            "original": q_text,
        }
    if has_teacher_pronoun and last_teacher:
        return {
            "intent": "teacher_eval",
            "teacher_id": last_teacher,
            "years": years or ["2023-2024", "2024-2025"],
            "original": q_text,
        }

    has_school_hint = any(k in q_text for k in ("学校", "小学", "中学", "附中", "高中", "初中", "两所", "校"))
    school_compare_fact_metric = None if target_dimension else match_indicator(q_text)
    comp_pair = _extract_comparison_schools(q_text, context=ctx)
    if comp_pair is None and last_school_compare_a and last_school_compare_b and has_explicit_context_ref:
        if has_school_pronoun and not has_school_pair_pronoun:
            return {
                "intent": "clarify",
                "reason": "ambiguous_school_compare_side",
                "missing": ["需补充澄清：当前焦点包含两所学校，请明确学校A、学校B，或说明这是新的单校任务。"],
                "original": q_text,
            }
        if has_school_hint and not has_school_pair_pronoun:
            return {
                "intent": "clarify",
                "reason": "ambiguous_school_compare_side",
                "missing": ["需补充澄清：请说明要替换学校A、学校B，或重新提供两所学校的完整名称。"],
                "original": q_text,
            }
        comp_pair = (last_school_compare_a, last_school_compare_b)
    if comp_pair:
        if comp_pair[0] == comp_pair[1]:
            return {
                "intent": "clarify",
                "reason": "duplicate_school_compare_entity",
                "missing": ["需补充澄清：双校比较必须提供两所不同学校。"],
                "original": q_text,
            }
        if school_compare_fact_metric:
            is_item_comp = any(k in q_text for k in ("差值", "相减", "前者减后者", "画柱状图", "柱状图", "对比", "比较", "高出", "多少"))
            if is_item_comp and active_year:
                return {
                    "intent": "school_item_compare",
                    "operation": "item_compare",
                    "school_a": comp_pair[0],
                    "school_b": comp_pair[1],
                    "year": active_year,
                    "metric": school_compare_fact_metric,
                    "value_view": "raw_value",
                    "original": q_text,
                    "pending_provenance": ctx.get("pending_provenance") or {},
                }
            return {
                "intent": "clarify",
                "reason": "unsupported_school_compare_fact_metric",
                "missing": ["需补充澄清：双校首批契约只比较固定数字化模板综合分与六维整体结果，暂不支持对两校事实指标逐项比较。"],
                "original": q_text,
            }

        if target_stat is not None:
            return {
                "intent": "clarify",
                "reason": "unsupported_school_compare_statistic",
                "missing": ["需补充澄清：双校首批契约使用固定模板加权综合分，不支持另行指定均值、中位数、极值或数量口径。"],
                "original": q_text,
            }
        if len(years) > 1:
            return {
                "intent": "clarify",
                "reason": "unsupported_school_compare_cross_year",
                "missing": ["需补充澄清：双校首批契约要求两所学校使用同一显式年份，暂不支持跨年双校比较。"],
                "original": q_text,
            }
        if not active_year:
            school_a_name, school_b_name = comp_pair
            try:
                from scripts.school_engine import find_schools
                hits_a = find_schools(comp_pair[0], limit=1)
                hits_b = find_schools(comp_pair[1], limit=1)
                if not hits_a or not hits_b:
                    raise ValueError("unresolved")
                school_a_name = str(hits_a[0].get("school_name") or comp_pair[0])
                school_b_name = str(hits_b[0].get("school_name") or comp_pair[1])
                if school_a_name == school_b_name:
                    return {
                        "intent": "clarify",
                        "reason": "duplicate_school_compare_entity",
                        "missing": ["需补充澄清：两个名称解析到同一所学校，请提供另一所学校。"],
                        "original": q_text,
                    }
            except Exception:
                return {
                    "intent": "clarify",
                    "reason": "unresolved_school_compare_entity",
                    "missing": ["需补充澄清：请提供两所可识别的完整学校名称，并指定同一评估年份。"],
                    "original": q_text,
                }
            return {
                "intent": "clarify",
                "reason": "missing_year",
                "missing": ["需补充澄清：双校比较必须指定同一评估年份（例如：2020年）。"],
                "original": q_text,
                "pending_context": {
                    "last_school": None,
                    "last_school_compare_a": school_a_name,
                    "last_school_compare_b": school_b_name,
                    "last_year": None,
                },
                "pending_values": {
                    "entity_id": {"value": school_a_name, "evidence_span": comp_pair[0]},
                    "secondary_entity_id": {"value": school_b_name, "evidence_span": comp_pair[1]},
                },
            }
        return {
            "intent": "school_compare",
            "school_a": comp_pair[0],
            "school_b": comp_pair[1],
            "year": active_year,
            "target_dimension": target_dimension or (target_dimensions[0] if target_dimensions else None),
            "target_dimensions": target_dimensions,
            "original": q_text,
            "pending_provenance": ctx.get("pending_provenance") or {},
        }

    comp_regions = _extract_comparison_regions(q_text, context=ctx)
    explicit_compare_followup_region = detect_region(q_text)
    if (
        comp_regions is None
        and last_region_compare_a
        and last_region_compare_b
        and has_explicit_context_ref
        and explicit_compare_followup_region is not None
    ):
        return {
            "intent": "clarify",
            "reason": "ambiguous_region_compare_side",
            "missing": ["需补充澄清：请说明要替换区域A、区域B，或明确这是一个新的单区域任务。"],
            "original": q_text,
        }
    if (
        comp_regions is None
        and last_region_compare_a
        and last_region_compare_b
        and has_explicit_context_ref
        and not has_region_pronoun
        and explicit_compare_followup_region is None
    ):

        comp_regions = (last_region_compare_a, last_region_compare_b)
    if comp_regions:
        region_a = detect_region(comp_regions[0])
        region_b = detect_region(comp_regions[1])
        if not region_a or not region_b:
            return {
                "intent": "clarify",
                "reason": "unresolved_region_compare_entity",
                "missing": ["需补充澄清：请提供两个可识别且不同的行政区域。"],
                "original": q_text,
            }
        if region_a["full_name"] == region_b["full_name"]:
            return {
                "intent": "clarify",
                "reason": "duplicate_region_compare_entity",
                "missing": ["需补充澄清：双区域比较必须提供两个不同的行政区域。"],
                "original": q_text,
            }
        if target_stat not in {None, "mean", "count"}:
            return {
                "intent": "clarify",
                "reason": "unsupported_region_compare_statistic",
                "missing": ["需补充澄清：双区域首批契约仅支持均值或学校数量比较。"],
                "original": q_text,
            }
        if len(target_dimensions) > 1:
            return {
                "intent": "clarify",
                "reason": "unsupported_multi_dimension",
                "missing": ["需补充澄清：双区域首批契约仅支持整体或一个维度。"],
                "original": q_text,
            }
        if target_dimension and target_stat == "count":
            return {
                "intent": "clarify",
                "reason": "unsupported_region_compare_dimension_count",
                "missing": ["需补充澄清：学校数量比较不能同时限定问卷维度。"],
                "original": q_text,
            }
        if not active_year:
            return {
                "intent": "clarify",
                "reason": "missing_year",
                "missing": ["需补充澄清：双区域比较必须指定同一评估年份（例如：2020年）。"],
                "original": q_text,
                "pending_context": {
                    "last_region": None,
                    "last_region_compare_a": region_a["name"],
                    "last_region_compare_b": region_b["name"],
                    "last_dimension": target_dimension,
                    "last_statistic": target_stat,
                    "last_aggregation": aggregation,
                    "last_school_filter": school_filter,
                },
                "pending_values": {
                    "entity_id": {
                        "value": region_a["full_name"],
                        "evidence_span": region_a["name"] if region_a["name"] in q_text else comp_regions[0],
                    },
                    "secondary_entity_id": {
                        "value": region_b["full_name"],
                        "evidence_span": region_b["name"] if region_b["name"] in q_text else comp_regions[1],
                    },
                    **({"dimension": target_dimension} if target_dimension else {}),
                    **({"statistic": target_stat} if target_stat else {}),
                    **({"aggregation": aggregation} if aggregation_explicit else {}),
                    **({"school_filter": school_filter} if school_filter else {}),
                },
            }
        return {
            "intent": "region_compare",
            "region_a": region_a["name"],
            "region_b": region_b["name"],
            "year": active_year,
            "target_dimensions": target_dimensions,
            "target_dimension": target_dimension,
            "target_stat": target_stat,
            "school_filter": school_filter,
            "aggregation": aggregation,
            "original": q_text,
            "pending_provenance": ctx.get("pending_provenance") or {},
        }

    region_cand = detect_region(q_text) or (detect_region(q_text_no_filter) if q_text_no_filter != q_text else None)
    if not region_cand and (has_region_pronoun or has_explicit_context_ref) and last_region:
        region_cand = detect_region(last_region)

    calendar_years = [str(value) for value in years if re.fullmatch(r"20\d{2}", str(value))]
    directional_period_match = re.search(
        r"(?:从|由)?\s*(20\d{2})\s*年?\s*(?:到|至)\s*(20\d{2})\s*年?",
        q_text,
    )
    has_longitudinal_kw = (
        any(k in q_text for k in (
            "变化", "相比", "趋势", "增长", "下降", "提升", "变动", "演变", "回落", "走势",
            "两期", "跨年", "跨学年", "历年", "多年",

            "预测", "预估", "外推", "推算未来", "估算未来", "未来趋势", "明年",
        ))
        or directional_period_match is not None
        or ("比" in q_text and len(calendar_years) >= 2 and not comp_regions and not comp_pair)
    )

    if (
        region_cand
        and ranking_target_explicit
        and (len(calendar_years) >= 2 or has_longitudinal_kw)
    ):
        return {
            "intent": "clarify",
            "reason": "unsupported_region_ranking_multi_period",
            "missing": ["需补充澄清：区域排名首批仅支持单一明确年份，暂不支持跨年或两期排名。"],
            "original": q_text,
        }

    has_multi_year_intent = (
        has_longitudinal_kw
        or (len(calendar_years) >= 2 and not any(k in q_text for k in ("只看", "只接受", "只选", "锁定")))
        or bool(
            last_region_longitudinal_period_a
            and last_region_longitudinal_period_b
            and has_explicit_context_ref
        )
        or ctx.get("pending_clarification_reason") == "missing_region_longitudinal_period"
    )

    longitudinal_school_scan = _AGGREGATION_SCHOOL_PHRASE_RE.sub(" ", q_text_no_filter)
    longitudinal_school_scan = _REGION_LONGITUDINAL_GENERIC_SCHOOL_SCOPE_RE.sub(
        " ", longitudinal_school_scan
    )
    has_explicit_school_entity = False
    for raw_school in _SCHOOL_RE.findall(longitudinal_school_scan):
        clean_school = re.sub(r"20\d{2}\s*年?", "", raw_school).strip()
        clean_school = _PREFIX_PAT.sub("", clean_school).strip()
        clean_school = _SUFFIX_PAT.sub("", clean_school).strip()
        if (
            len(clean_school) >= 4
            and not _SCHOOL_AGGREGATION_RE.search(raw_school)
            and not any(bad in raw_school for bad in _INVALID_SCHOOL_SUBSTRINGS)
            and not _SCHOOL_FILTER_RE.search(clean_school)
            and not _is_pure_region(clean_school)
        ):
            has_explicit_school_entity = True
            break

    if has_multi_year_intent and has_explicit_school_entity:
        if len(calendar_years) >= 2:
            return {
                "intent": "school_compare",
                "school_a": clean_school,
                "school_b": clean_school,
                "year_a": calendar_years[0],
                "year_b": calendar_years[1],
                "year": f"{calendar_years[0]},{calendar_years[1]}",
                "original": q_text,
                "pending_provenance": ctx.get("pending_provenance") or {},
            }
        return {
            "intent": "clarify",
            "reason": "unsupported_school_longitudinal",
            "missing": ["需补充澄清：当前两期变化首片仅支持行政区域重复截面，不支持单校跨年变化或时间比较。"],
            "original": q_text,
        }

    if (
        region_cand
        and (_REGION_RANKING_SCOPE_RE.search(q_text) or ranking_target_explicit)
        and not comp_regions
        and not comp_pair
        and not has_explicit_school_entity
    ):
        for reason, pattern, message in _REGION_RANKING_UNSUPPORTED_INTENT_PATTERNS:
            if pattern.search(q_text):
                return {
                    "intent": "clarify",
                    "reason": reason,
                    "missing": [message],
                    "original": q_text,
                }

    if (
        region_cand
        and not ranking_target_explicit
        and not _REGION_RANKING_SCOPE_RE.search(q_text)
        and not has_multi_year_intent
        and not comp_regions
        and not comp_pair
        and not has_explicit_school_entity
    ):
        for reason, pattern, message in _REGION_EVALUATION_UNSUPPORTED_INTENT_PATTERNS:
            if pattern.search(q_text):
                return {
                    "intent": "clarify",
                    "reason": reason,
                    "missing": [message],
                    "original": q_text,
                }

    longitudinal_candidate = bool(
        has_multi_year_intent
        and not comp_regions
        and not comp_pair
        and not has_explicit_school_entity
    )
    pending_reason = str(ctx.get("pending_clarification_reason") or "")
    period_a = None
    period_b = None
    period_a_explicit = False
    period_b_explicit = False

    if longitudinal_candidate:
        for reason, pattern, message in _REGION_LONGITUDINAL_UNSUPPORTED_INTENT_PATTERNS:
            if pattern.search(q_text):
                return {
                    "intent": "clarify",
                    "reason": reason,
                    "missing": [message],
                    "original": q_text,
                }

    if longitudinal_candidate and any(str(value) not in calendar_years for value in years):
        return {
            "intent": "clarify",
            "reason": "unsupported_region_longitudinal_period_format",
            "missing": ["需补充澄清：两期截面变化首批仅接受两个明确日历年份（例如：2020年与2023年）。"],
            "original": q_text,
        }
    if longitudinal_candidate and len(calendar_years) > 2:
        return {
            "intent": "clarify",
            "reason": "unsupported_region_longitudinal_period_count",
            "missing": ["需补充澄清：两期截面变化必须且只能指定两个年份，暂不支持三期及以上趋势。"],
            "original": q_text,
        }
    if longitudinal_candidate and len(calendar_years) == 2:
        if calendar_years[0] == calendar_years[1]:
            return {
                "intent": "clarify",
                "reason": "duplicate_region_longitudinal_period",
                "missing": ["需补充澄清：两期截面变化必须提供两个不同年份。"],
                "original": q_text,
            }
        if directional_period_match is not None:
            explicit_start, explicit_end = directional_period_match.groups()
            if explicit_start >= explicit_end:
                return {
                    "intent": "clarify",
                    "reason": "invalid_region_longitudinal_period_order",
                    "missing": ["需补充澄清：从/由某年到某年的表达要求末期晚于基期；也可改用两个年份并说明按自然年归一。"],
                    "original": q_text,
                }
            period_a, period_b = explicit_start, explicit_end
        else:
            period_a, period_b = sorted(calendar_years)
        period_a_explicit = True
        period_b_explicit = True
    elif longitudinal_candidate and len(calendar_years) == 1:
        only_year = calendar_years[0]
        if pending_reason == "missing_region_longitudinal_period" and last_region_longitudinal_period_a:
            period_a, period_b = str(last_region_longitudinal_period_a), only_year
            if period_b <= period_a:
                return {
                    "intent": "clarify",
                    "reason": "invalid_region_longitudinal_period_order",
                    "missing": ["需补充澄清：末期必须晚于已保存的基期；也可直接重新提供两个完整年份。"],
                    "original": q_text,
                }
            period_b_explicit = True
        elif pending_reason == "missing_region_longitudinal_period" and last_region_longitudinal_period_b:
            period_a, period_b = only_year, str(last_region_longitudinal_period_b)
            if period_a >= period_b:
                return {
                    "intent": "clarify",
                    "reason": "invalid_region_longitudinal_period_order",
                    "missing": ["需补充澄清：基期必须早于已保存的末期；也可直接重新提供两个完整年份。"],
                    "original": q_text,
                }
            period_a_explicit = True
        elif last_region_longitudinal_period_a and last_region_longitudinal_period_b and has_explicit_context_ref:
            if any(k in q_text for k in ("基期", "前期", "起始年", "开始年", "前年")):
                period_a, period_b = only_year, str(last_region_longitudinal_period_b)
                if period_a >= period_b:
                    return {
                        "intent": "clarify",
                        "reason": "invalid_region_longitudinal_period_order",
                        "missing": ["需补充澄清：基期必须早于末期；也可直接给出两个完整年份。"],
                        "original": q_text,
                    }
                period_a_explicit = True
            elif any(k in q_text for k in ("末期", "后期", "目标年", "结束年", "后年")):
                period_a, period_b = str(last_region_longitudinal_period_a), only_year
                if period_b <= period_a:
                    return {
                        "intent": "clarify",
                        "reason": "invalid_region_longitudinal_period_order",
                        "missing": ["需补充澄清：末期必须晚于基期；也可直接给出两个完整年份。"],
                        "original": q_text,
                    }
                period_b_explicit = True
            else:
                return {
                    "intent": "clarify",
                    "reason": "ambiguous_region_longitudinal_period_side",
                    "missing": ["需补充澄清：请说明要修改基期还是末期，或直接给出两个完整年份。"],
                    "original": q_text,
                }
    elif longitudinal_candidate and (
        last_region_longitudinal_period_a
        and last_region_longitudinal_period_b
        and has_explicit_context_ref
    ):
        period_a = str(last_region_longitudinal_period_a)
        period_b = str(last_region_longitudinal_period_b)

    if longitudinal_candidate:
        if target_stat not in {None, "mean", "count"}:
            return {
                "intent": "clarify",
                "reason": "unsupported_region_longitudinal_statistic",
                "missing": ["需补充澄清：两期截面变化首批仅支持均值或整体有效学校数量。"],
                "original": q_text,
            }
        if len(target_dimensions) > 1:
            return {
                "intent": "clarify",
                "reason": "unsupported_region_longitudinal_multi_dimension",
                "missing": ["需补充澄清：两期截面变化首批仅支持整体或一个维度。"],
                "original": q_text,
            }
        if target_dimension and target_stat == "count":
            return {
                "intent": "clarify",
                "reason": "unsupported_region_longitudinal_dimension_count",
                "missing": ["需补充澄清：有效学校数量的两期变化不能同时限定问卷维度。"],
                "original": q_text,
            }
        if period_a and period_b and period_a == period_b:
            return {
                "intent": "clarify",
                "reason": "duplicate_region_longitudinal_period",
                "missing": ["需补充澄清：两期截面变化必须提供两个不同年份。"],
                "original": q_text,
            }
        if not region_cand and period_a and period_b:
            return {
                "intent": "clarify",
                "reason": "missing_region_longitudinal_region",
                "missing": ["需补充澄清：请指定要比较两个年份的同一行政区域。"],
                "original": q_text,
                "pending_context": {
                    "last_region": None,
                    "last_region_longitudinal_period_a": period_a,
                    "last_region_longitudinal_period_b": period_b,
                    "last_dimension": target_dimension,
                    "last_statistic": target_stat or "mean",
                    "last_aggregation": aggregation,
                    "last_school_filter": school_filter,
                },
                "pending_values": {
                    "period": {"value": period_a, "evidence_span": period_a if period_a_explicit else None},
                    "secondary_period": {"value": period_b, "evidence_span": period_b if period_b_explicit else None},
                    **({"dimension": target_dimension} if target_dimension else {}),
                    **({"statistic": target_stat} if target_stat else {}),
                    **({"aggregation": aggregation} if aggregation_explicit else {}),
                    **({"school_filter": school_filter} if school_filter else {}),
                },
            }
        if region_cand and not (period_a and period_b):
            saved_a = None
            saved_b = None
            if calendar_years:
                only_year = calendar_years[0]
                marked_as_start = bool(re.search(
                    rf"(?:从|基期|前期|起始年|开始年)\s*{re.escape(only_year)}",
                    q_text,
                ))
                marked_as_end = bool(re.search(
                    rf"(?:到|至|末期|后期|目标年|结束年)\s*{re.escape(only_year)}",
                    q_text,
                ))
                if marked_as_end and not marked_as_start:
                    saved_b = only_year
                else:
                    saved_a = only_year
            return {
                "intent": "clarify",
                "reason": "missing_region_longitudinal_period",
                "missing": ["需补充澄清：请提供两个不同的明确年份（例如：2020年与2023年）。"],
                "original": q_text,
                "pending_context": {
                    "last_region": region_cand["name"],
                    "last_region_longitudinal_period_a": saved_a,
                    "last_region_longitudinal_period_b": saved_b,
                    "last_dimension": target_dimension,
                    "last_statistic": target_stat or "mean",
                    "last_aggregation": aggregation,
                    "last_school_filter": school_filter,
                },
                "pending_values": {
                    "entity_id": {
                        "value": region_cand["full_name"],
                        "evidence_span": region_cand["name"] if region_cand["name"] in q_text else None,
                    },
                    **({"period": {"value": saved_a, "evidence_span": saved_a}} if saved_a else {}),
                    **({"secondary_period": {"value": saved_b, "evidence_span": saved_b}} if saved_b else {}),
                    **({"dimension": target_dimension} if target_dimension else {}),
                    **({"statistic": target_stat} if target_stat else {}),
                    **({"aggregation": aggregation} if aggregation_explicit else {}),
                    **({"school_filter": school_filter} if school_filter else {}),
                },
            }

    if period_a and period_b and region_cand and longitudinal_candidate:
        return {
            "intent": "region_longitudinal",
            "region": region_cand["name"],
            "region_info": region_cand,
            "year_a": period_a,
            "year_b": period_b,
            "target_dimensions": target_dimensions,
            "target_dimension": target_dimension,
            "target_stat": target_stat,
            "school_filter": school_filter,
            "aggregation": aggregation,
            "temporal_mode": "repeated_cross_section",
            "period_order_policy": "chronological_earlier_to_later",
            "original": q_text,
            "pending_provenance": ctx.get("pending_provenance") or {},
        }

    explicit_question_ids = extract_explicit_question_ids(q_text)
    valid_explicit_question_ids = [qid for qid in explicit_question_ids if 1 <= qid <= 83]
    invalid_explicit_question_ids = [qid for qid in explicit_question_ids if qid not in valid_explicit_question_ids]
    metric_info = (
        question_metric(valid_explicit_question_ids[0])
        if len(valid_explicit_question_ids) == 1 and not invalid_explicit_question_ids
        else (None if target_dimension or explicit_question_ids else match_indicator(q_text))
    )
    metric_from_pending = False
    metric_from_focus = False
    if metric_info is None and ctx.get("pending_clarification_active"):
        metric_info = ctx.get("pending_school_fact")
        metric_from_pending = metric_info is not None
    if (
        metric_info is None
        and years
        and has_explicit_context_ref
        and last_school
        and last_school == last_school_fact_entity
        and isinstance(last_school_fact_metric, dict)
        and last_school_fact_metric
        and not region_cand
        and not target_rank
        and not target_top_n
    ):

        metric_info = dict(last_school_fact_metric)
        metric_from_focus = True

    school_scan_text = _AGGREGATION_SCHOOL_PHRASE_RE.sub(" ", q_text)
    school_scan_text = _REGION_LONGITUDINAL_GENERIC_SCHOOL_SCOPE_RE.sub(" ", school_scan_text)
    school_scan_text = re.sub(
        r"(?:只回答|回答|给出|返回|只要|只看|查看)?(?:有效)?学校(?:总)?数(?![字据])",
        " ",
        school_scan_text,
    )
    school_scan_text = re.sub(
        r"(?:有效)?学校(?:共有|一共有|共有多少|有多少|有几所|几所|多少所)",
        " ",
        school_scan_text,
    )
    school_scan_text = re.sub(
        r"top\s*\d+\s*(?:名|所|家|个)?",
        " ",
        school_scan_text,
        flags=re.I,
    )
    school_scan_text = re.sub(
        r"(?:20\d{2}\s*年?(?:度)?的学校|的学校)(?=数字化|综合|整体|发展|大盘|模板|均分|得分|水平)",
        " ",
        school_scan_text,
    )
    raw_matches = _SCHOOL_RE.findall(school_scan_text)
    school_matches = []
    for m in raw_matches:
        if _SCHOOL_AGGREGATION_RE.search(m):
            continue
        if any(bad in m for bad in _INVALID_SCHOOL_SUBSTRINGS):
            continue

        should_split = any(cp in m for cp in _COMPARE_PATTERNS)
        if not should_split and any(k in m for k in ("和", "与", "跟", "及", "、")):
            sub_parts = _DELIM_RE.split(m)
            valid_sub_parts = [
                sp for sp in sub_parts
                if len(_clean_school_token(sp)) >= 4
                and any(_clean_school_token(sp).endswith(suf) for suf in ("学", "校", "中", "小", "园"))
            ]
            if len(valid_sub_parts) >= 2:
                should_split = True
        if should_split:
            sub_parts = _DELIM_RE.split(m)
            for sp in sub_parts:
                sp_c = _clean_school_token(sp)
                if len(sp_c) >= 4 and sp_c not in school_matches and not any(bad in sp_c for bad in _INVALID_SCHOOL_SUBSTRINGS) and not _is_pure_region(sp_c):
                    if not _SCHOOL_FILTER_RE.search(sp_c):
                        school_matches.append(sp_c)
                    else:
                        school_filter = sp_c
            continue

        clean_m = _clean_school_token(m)
        if any(bad in clean_m for bad in _INVALID_SCHOOL_SUBSTRINGS):
            continue
        if _SCHOOL_FILTER_RE.search(clean_m) or _SCHOOL_FILTER_RE.search(m):
            if not school_filter:
                school_filter = clean_m
            continue
        if _is_pure_region(clean_m):
            continue

        if re.search(r"(?:第[一二两三四五六七八九十\d]+(?:名|位|名次)|前[一二两三四五六七八九十\d]+(?:名|所|家|位)?|榜首|排名|位次)", m):
            continue

        if len(clean_m) < 4:
            continue

        cand_reg = detect_region(clean_m)
        if cand_reg and cand_reg.get("name"):
            reg_stem = cand_reg["name"].rstrip("市").rstrip("区").rstrip("县").rstrip("省")
            suffix_stage = re.sub(r"^" + re.escape(reg_stem), "", clean_m).strip()
            suffix_stage = re.sub(r"^(?:市|区|县|省)?", "", suffix_stage).strip()
            if suffix_stage in ("初中", "小学", "高中", "初级中学", "高级中学", "完全中学", "中职", "幼儿园", "学校", "中小学"):
                if not school_filter:
                    school_filter = suffix_stage
                continue

        try:
            from scripts.school_engine import find_schools
            test_hits = find_schools(clean_m, limit=1)
            if not test_hits:

                if any(k in clean_m for k in ("公办", "民办", "公立", "私立", "乡村", "城镇", "只纳入", "纳入")):
                    if not school_filter:
                        school_filter = clean_m
                    continue

                if clean_m.startswith("学校") or any(v in clean_m for v in ("实现", "拥有", "利用", "开展", "能够", "管理", "建设", "推进", "主管", "兼任", "配置", "是否", "工作内容", "投入", "平台", "云平台", "最近", "近一年", "数字化", "资产")):
                    continue
                if metric_info and clean_m in metric_info.get("name", ""):
                    continue
        except Exception:
            pass

        if clean_m not in school_matches and not _is_pure_region(clean_m):
            school_matches.append(clean_m)

    if len(school_matches) >= 3 and any(k in q_text for k in ("中间", "中位数", "排在中间", "正中间", "哪个在中间")):
        return {
            "intent": "multi_school_compare",
            "operation": "school_compare",
            "schools": school_matches,
            "year": active_year,
            "derived": "middle_school",
            "original": q_text,
            "pending_provenance": ctx.get("pending_provenance") or {},
        }

    if len(school_matches) >= 2:
        if school_matches[0] == school_matches[1]:
            return {
                "intent": "clarify",
                "reason": "duplicate_school_compare_entity",
                "missing": ["需补充澄清：双校比较必须提供两所不同学校。"],
                "original": q_text,
            }
        if school_compare_fact_metric:
            is_item_comp = any(k in q_text for k in ("差值", "相减", "前者减后者", "画柱状图", "柱状图", "对比", "比较", "高出", "多少"))
            if is_item_comp and active_year:
                return {
                    "intent": "school_item_compare",
                    "operation": "item_compare",
                    "school_a": school_matches[0],
                    "school_b": school_matches[1],
                    "year": active_year,
                    "metric": school_compare_fact_metric,
                    "value_view": "raw_value",
                    "original": q_text,
                    "pending_provenance": ctx.get("pending_provenance") or {},
                }
            return {
                "intent": "clarify",
                "reason": "unsupported_school_compare_fact_metric",
                "missing": ["需补充澄清：双校首批契约暂不支持事实指标逐项比较。"],
                "original": q_text,
            }
        if target_stat is not None:
            return {
                "intent": "clarify",
                "reason": "unsupported_school_compare_scope",
                "missing": ["需补充澄清：双校首批契约仅支持固定模板综合分及六维整体对比。"],
                "original": q_text,
            }
        if len(years) > 1:
            return {
                "intent": "clarify",
                "reason": "unsupported_school_compare_cross_year",
                "missing": ["需补充澄清：双校首批契约要求同一显式年份。"],
                "original": q_text,
            }
        if not active_year:
            return {
                "intent": "clarify",
                "reason": "missing_year",
                "missing": ["需补充澄清：双校比较必须指定同一评估年份。"],
                "original": q_text,
                "pending_context": {
                    "last_school": None,
                    "last_school_compare_a": school_matches[0],
                    "last_school_compare_b": school_matches[1],
                },
                "pending_values": {
                    "entity_id": {"value": school_matches[0], "evidence_span": school_matches[0]},
                    "secondary_entity_id": {"value": school_matches[1], "evidence_span": school_matches[1]},
                },
            }
        return {
            "intent": "school_compare",
            "school_a": school_matches[0],
            "school_b": school_matches[1],
            "year": active_year,
            "target_dimension": target_dimension or (target_dimensions[0] if target_dimensions else None),
            "target_dimensions": target_dimensions,
            "original": q_text,
            "pending_provenance": ctx.get("pending_provenance") or {},
        }

    from packages.semantic_query.runtime import _literal_region_spans, _literal_school_name_spans
    literal_regions=_literal_region_spans(q_text) if not _literal_school_name_spans(q_text) else []
    literal_region_info=detect_region(literal_regions[0]) if len(literal_regions)==1 else None
    if literal_region_info and extracted_filter and not _literal_school_name_spans(q_text):
        school_matches=[]
    preidentified_region_info = literal_region_info or detect_region(q_text) or (
        detect_region(q_text_no_filter) if q_text_no_filter != q_text else None
    )

    school_ids = _SCHOOL_ID_RE.findall(q_text)
    target_school = None
    school_from_focus = False
    if school_matches:
        target_school = school_matches[0]
    elif school_ids:
        target_school = f"id:{school_ids[0]}"
    elif (has_school_pronoun or not target_school) and last_school:
        if has_school_pronoun or (
            not preidentified_region_info
            and (
                metric_info
                or (
                    any(p in q_text for p in _RANKING_PATTERNS)
                    and not target_rank
                    and not target_top_n
                )
            )
        ):
            target_school = last_school
            school_from_focus = True
    elif not target_school and not (target_rank or target_top_n or target_dimension):
        c_text = _PREFIX_PAT.sub("", q_text).strip()
        c_text = _SUFFIX_PAT.sub("", c_text).strip()
        c_text = re.sub(r"[呢吧吗呀？\?。！\!]+$", "", c_text).strip()
        c_text = c_text.rstrip("的").strip()
        c_text_no_year = re.sub(r"20\d{2}\s*年?", "", c_text).strip()
        cand_r = detect_region(q_text) or detect_region(c_text) or detect_region(c_text_no_year)
        if (
            len(c_text) >= 3
            and not any(bad in c_text for bad in _INVALID_SCHOOL_SUBSTRINGS)
            and not _is_pure_region(c_text)
            and not _is_pure_region(c_text_no_year)
            and not _SCHOOL_FILTER_RE.search(c_text)
            and not cand_r
        ):
            try:
                from scripts.school_engine import find_schools
                test_hits = find_schools(c_text, limit=1)
                if test_hits:
                    target_school = c_text
            except Exception:
                pass

    if target_school and invalid_explicit_question_ids:
        return {
            "intent": "clarify",
            "reason": "school_item_question_id_not_in_catalogue",
            "missing": ["学校单题必须使用83题目录中的有效题号（1—83）。"],
            "original": q_text,
        }
    if target_school and len(valid_explicit_question_ids) > 1:
        return {
            "intent": "clarify",
            "reason": "unsupported_school_item_set",
            "missing": ["学校题集尚未进入当前首片；请一次只问一个题项。"],
            "original": q_text,
        }

    is_ranking = any(p in q_text for p in _RANKING_PATTERNS)
    if is_ranking and target_school:
        reg_cand = detect_region(q_text)
        r_name = reg_cand["name"] if reg_cand else None
        return {
            "intent": "school_ranking",
            "school": target_school,
            "region": r_name,
            "year": active_year,
            "original": q_text,
        }

    if metric_info and (target_school or not preidentified_region_info):
        if not target_school:
            if last_school:
                target_school = last_school
                school_from_focus = True
            elif has_school_pronoun:
                return {
                    "intent": "clarify",
                    "reason": "unresolved_school_pronoun",
                    "missing": ["需补充澄清：当前没有用户明确选定的学校，请提供学校名称；榜单或区域结果中的学校不会自动成为会话焦点。"],
                    "original": q_text,
                    "pending_context": {
                        "last_year": active_year,
                        "pending_school_fact": metric_info,
                    },
                    "pending_values": {
                        **({"period": active_year} if active_year else {}),
                    },
                }
            else:
                return {
                    "intent": "clarify",
                    "reason": "missing_school",
                    "missing": ["需补充澄清：该事实指标必须绑定一所明确学校，请提供学校名称或学校ID。"],
                    "original": q_text,
                    "pending_context": {
                        "last_year": active_year,
                        "pending_school_fact": metric_info,
                    },
                    "pending_values": {
                        **({"period": active_year} if active_year else {}),
                    },
                }
        vv = (
            "normalized_score"
            if re.search(r"归一(?:化)?(?:得分|分数|值)?|标准化(?:得分|分数|值)?", q_text)
            else "raw_value"
        )
        if metric_info and isinstance(metric_info, dict):
            metric_info["value_view"] = vv
        return {
            "intent": "school_fact",
            "school": target_school,
            "metric": metric_info,
            "year": active_year,
            "original": q_text,
            "pending_provenance": ctx.get("pending_provenance") or {},
            "metric_inherited": metric_from_focus,
            "school_inherited": school_from_focus,
            "period_only_followup": bool(
                metric_from_focus and school_from_focus and years
            ),
            "value_view": vv,
            **({
                "metric_source_turn": ctx.get("pending_clarification_turn_id"),
                "metric_evidence_span": ctx.get("pending_original_question"),
            } if metric_from_pending else {}),
        }

    region_info = preidentified_region_info
    if not region_info and (has_region_pronoun or target_rank or target_top_n or target_dimension or has_explicit_context_ref or any(p in q_text for p in _LEADERBOARD_PATTERNS)):
        if last_region:
            region_info = detect_region(last_region)

    if region_info:
        if not target_school:

            if _REGION_RANKING_SCOPE_RE.search(q_text) and not ranking_target_explicit:
                return {
                    "intent": "clarify",
                    "reason": "missing_region_ranking_target",
                    "missing": ["请指定第N名、倒数第一或Top N；当前首片不返回完整榜单。"],
                    "original": q_text,
                }

            if not active_year and not has_explicit_context_ref:
                r_name = region_info.get("name", "该地区")
                return {
                    "intent": "clarify",
                    "reason": "missing_year",
                    "missing": [f"需补充澄清：请指定评估年份（例如：{r_name}可用学年为 2020年、2021年、2022年、2023年）"],
                    "original": q_text,
                    "pending_context": {
                        "last_region": region_info["name"],
                        "last_target_rank": target_rank,
                        "last_target_top_n": target_top_n,
                        "last_dimension": target_dimension,
                        "last_statistic": target_stat,
                        "last_aggregation": aggregation,
                        "last_school_filter": school_filter,
                    },
                    "pending_values": {
                        "entity_id": {
                            "value": region_info["full_name"],
                            "evidence_span": next(
                                (
                                    candidate for candidate in (
                                        region_info["name"],
                                        region_info["name"].rstrip("省市县区旗州盟"),
                                    )
                                    if candidate and candidate in q_text
                                ),
                                None,
                            ),
                        },
                        **(
                            {"dimension": target_dimension}
                            if target_dimension else (
                                {"dimension": {"value": None, "evidence_span": next(
                                    (token for token in ("全部83项", "全部指标", "整体", "综合均分") if token in q_text),
                                    None,
                                )}}
                                if explicit_dimension_clear else {}
                            )
                        ),
                        **({"statistic": target_stat} if target_stat else {}),
                        **(
                            {"target_rank": {"value": target_rank, "evidence_span": ranking_evidence_span}}
                            if target_rank is not None else {}
                        ),
                        **(
                            {"target_top_n": {"value": target_top_n, "evidence_span": ranking_evidence_span}}
                            if target_top_n is not None else {}
                        ),
                        **({"aggregation": aggregation} if aggregation_explicit else {}),
                        **(
                            {"school_filter": school_filter}
                            if school_filter else (
                                {"school_filter": {"value": None, "evidence_span": next(
                                    (token for token in (
                                        "取消筛选", "取消过滤", "不再筛选", "不再过滤", "去掉筛选", "去掉过滤",
                                        "不限学段", "不限城乡", "全部学校", "所有学校",
                                    ) if token in q_text),
                                    None,
                                )}}
                                if clear_school_filter else {}
                            )
                        ),
                    },
                }
            if invalid_explicit_question_ids:
                return {
                    "intent": "clarify",
                    "reason": "region_item_question_id_not_in_catalogue",
                    "missing": ["区域单题必须使用83题目录中的有效题号（1—83）。"],
                    "original": q_text,
                }
            if len(valid_explicit_question_ids) > 1:
                return {
                    "intent": "clarify",
                    "reason": "unsupported_region_item_set",
                    "missing": ["区域题集尚未进入当前首片；请一次只问一个题项的归一得分均值。"],
                    "original": q_text,
                }
            if metric_info is not None:
                if re.search(
                    r"复合指标|组合指标|指标组合|composite\s+(?:metric|indicator)",
                    q_text,
                    re.I,
                ):
                    return {
                        "intent": "clarify",
                        "reason": "unsupported_region_item_metric_type",
                        "missing": ["区域单题首片只接受83题目录中的一个问卷题号，不支持复合指标或题项组合。"],
                        "original": q_text,
                    }
                if metric_info.get("type") != "question":
                    return {
                        "intent": "clarify",
                        "reason": "unsupported_region_item_metric_type",
                        "missing": ["区域单题首片只接受83题目录中的一个问卷题号，不支持复合指标或学校属性聚合。"],
                        "original": q_text,
                    }
                is_raw = bool(
                    (metric_info.get("unit") and metric_info["unit"] in q_text)
                    or re.search(r"多少\s*(?:mbps|台|个|所|门|本|件|套|位|人|节|次|条|元|万元|平米|平方米|小时|天|周|月|年)|mbps", q_text, re.I)
                    or re.search(r"原始(?:值|作答|编码)|raw\s*(?:value)?", q_text, re.I)
                )
                if is_raw and not metric_info.get("unit"):
                    return {
                        "intent": "clarify",
                        "reason": "unsupported_region_item_raw_aggregation",
                        "missing": ["区域单题首片只支持问卷归一得分均值；原始作答聚合需先明确题型和单位口径。"],
                        "original": q_text,
                    }
                if target_stat not in {None, "mean", "median", "mean_and_median"} or aggregation != "school_equal_mean":
                    return {
                        "intent": "clarify",
                        "reason": "unsupported_region_item_statistic_or_aggregation",
                        "missing": ["区域单题首片仅支持该题有效学校的学校等权归一得分均值。"],
                        "original": q_text,
                    }
                return {
                    "intent": "region_eval",
                    "region": region_info["name"],
                    "region_info": region_info,
                    "year": active_year,
                    "target_rank": None,
                    "target_top_n": None,
                    "target_dimension": None,
                    "target_dimensions": [],
                    "target_stat": target_stat or "mean",
                    "aggregation": "school_equal_mean",
                    "school_filter": school_filter,
                    "metric": metric_info,
                    "value_view": "raw_value" if is_raw else "normalized_score",
                    "original": q_text,
                    "pending_provenance": ctx.get("pending_provenance") or {},
                }
            return {
                "intent": "region_ranking" if (target_rank is not None or target_top_n is not None) else "region_eval",
                "region": region_info["name"],
                "region_info": region_info,
                "year": active_year,
                "target_rank": target_rank,
                "target_top_n": target_top_n,
                "target_dimension": target_dimension if len(target_dimensions) < 2 else None,
                "target_dimensions": target_dimensions,
                "target_stat": target_stat,
                "aggregation": aggregation,
                "school_filter": school_filter,
                "original": q_text,
                "pending_provenance": ctx.get("pending_provenance") or {},
                "target_rank_evidence": ranking_evidence_span,
                "target_top_n_evidence": ranking_evidence_span,
            }

    if target_school:
        return {
            "intent": "school_eval",
            "school": target_school,
            "years": [active_year] if active_year else [],
            "target_dimension": target_dimension if len(target_dimensions) < 2 else None,
            "target_dimensions": target_dimensions or ([target_dimension] if target_dimension else []),
            "original": q_text,
        }

    missing = []
    reason = None
    if years or target_rank is not None or target_top_n is not None or any(k in q_text for k in ("全部83项", "全部指标", "学校等权均分", "整体数字化均分", "数字化综合分", "学校均分")):
        reason = "missing_city"
        missing.append("需补充澄清：请指定具体的评估地区或城市名称（例如：武汉市、长沙市、南京市等）")
    elif target_stat:
        stat_desc = "平均值和中位数" if target_stat == "mean_and_median" else ("中位数" if target_stat == "median" else ("均分" if target_stat == "mean" else "统计量"))
        missing.append(f"您正在查询【{stat_desc}】，请指定具体的评估地区或学校名称（例如：武汉市2020年{stat_desc}是多少？）")
    elif "地区" in q_text:
        missing.append("请补充具体地区名称（例如：长沙县、七台河市、湖南省等）")
    elif "学校" in q_text:
        missing.append("请补充具体学校名称（例如：示例市示例区示例二小学、示例一小学等）")
    elif "老师" in q_text or "教师" in q_text:
        missing.append("请提供教师ID（例如：T001、T002）或学校名称")
    else:
        missing.append("未识别到查询对象。您可以自由提问：①微观配置（如：示例一小学配置了多少台教师平板电脑？）；②区域大盘（如：长沙县2021数字化水平怎么样？）；③相对排名（如：示例一小学在区里排第几？）；④整校评估（如：示例市示例区示例二小学2020数字化水平）")

    pending_context = {
        "last_year": active_year,
        "last_dimension": target_dimension,
        "last_statistic": target_stat,
        "last_aggregation": aggregation,
        "last_school_filter": school_filter,
        "last_target_rank": target_rank,
        "last_target_top_n": target_top_n,
    }
    pending_values = {
        **({"period": active_year} if active_year else {}),
        **(
            {"dimension": target_dimension}
            if target_dimension else (
                {"dimension": {"value": None, "evidence_span": next(
                    (token for token in ("全部83项", "全部指标", "整体", "综合均分") if token in q_text),
                    None,
                )}}
                if explicit_dimension_clear else {}
            )
        ),
        **({"statistic": target_stat} if target_stat else {}),
        **(
            {"target_rank": {"value": target_rank, "evidence_span": ranking_evidence_span}}
            if target_rank is not None else {}
        ),
        **(
            {"target_top_n": {"value": target_top_n, "evidence_span": ranking_evidence_span}}
            if target_top_n is not None else {}
        ),
        **({"aggregation": aggregation} if aggregation_explicit else {}),
        **(
            {"school_filter": school_filter}
            if school_filter else (
                {"school_filter": {"value": None, "evidence_span": next(
                    (token for token in (
                        "取消筛选", "取消过滤", "不再筛选", "不再过滤", "去掉筛选", "去掉过滤",
                        "不限学段", "不限城乡", "全部学校", "所有学校",
                    ) if token in q_text),
                    None,
                )}}
                if clear_school_filter else {}
            )
        ),
    }
    return {
        "intent": "clarify",
        "reason": reason,
        "missing": missing,
        "original": q_text,
        "pending_context": pending_context,
        "pending_values": pending_values,
    }
