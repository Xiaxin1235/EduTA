"""区域数字化宏观评估引擎 (Region Engine)。

支持按区县、地级市、省份汇总区域内全量学校的数字化作答大盘：
- 区域综合均分与多维指标表现
- 六大维度均分（数字资源、教育教学、数字素养、基础设施、教育治理、保障机制）
- 区域学校数字化发展分层与比例分布
- 标杆示范学校排行榜 (Top 5) 与薄弱帮扶名单 (Bottom 5)
- 区域数字化主要优势与瓶颈短板诊断
"""

import os
import math
import re
import sqlite3
import statistics
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.school_engine import connect
from packages.analysis_contract.capabilities import REGION_RANKING_SPEC

STANDARD_DIMENSIONS = [
    "数字资源",
    "教育教学",
    "数字素养",
    "基础设施",
    "教育治理",
    "保障机制",
]


PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆"
]


def format_school_type(st: Optional[str]) -> str:
    """美化学校学段显示名称。"""
    if not st:
        return "中小学"
    st = str(st).strip()
    mapping = {
        "1-6": "小学(六年制)",
        "1-5": "小学(五年制)",
        "A.小学": "小学",
        "7-9": "初中(三年制)",
        "B.初级中学": "初级中学",
        "1-9": "九年一贯制",
        "D.九年一贯制学校": "九年一贯制学校",
        "10-12": "高中(三年制)",
        "C.高级中学": "高级中学",
        "7-12": "完全中学(初高中)",
        "1-12": "十二年一贯制",
    }
    return mapping.get(st, st)


def _province_scope_literal(clean: str, conn) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """Resolve a complete province literal before lower-level dirty registrations.

    Names come from the existing province vocabulary and source metadata. Bare
    aliases conflicting with a real city/county require an explicit level.
    """
    provinces = {}
    for row in conn.execute("SELECT DISTINCT province FROM schools WHERE province IS NOT NULL"):
        name = str(row["province"] or "").strip()
        short = next((p for p in PROVINCES if name.startswith(p)), None)
        if not short or not name.endswith(("省", "市", "自治区", "特别行政区")):
            continue
        aliases = {name, short}
        if name.endswith("自治区"):
            aliases.add(short + "自治区")
        provinces[name] = aliases
    whole = [name for name, aliases in provinces.items() if clean in aliases]
    if whole:
        if len(whole) != 1:
            return True, None, None
        name = whole[0]
        if clean != name:
            aliases = provinces[name]
            placeholders = ",".join("?" for _ in aliases)
            for field, suffixes in (("city", ("市",)), ("district", ("区", "县", "旗"))):
                variants = [clean] + [clean + suffix for suffix in suffixes]
                variants_sql = ",".join("?" for _ in variants)
                conflicts = conn.execute(
                    f"SELECT DISTINCT province, city, district FROM schools WHERE {field} IN ({variants_sql}) "
                    f"AND {field} NOT IN ({placeholders}) AND {field}<>province "
                    + ("AND district<>city AND (city LIKE '%市' OR city LIKE '%州' OR city LIKE '%盟' OR city LIKE '%地区') AND (city NOT LIKE province||'%' OR (city=province AND province LIKE '%市'))" if field == "district" else ""),
                    (*variants, *aliases),
                ).fetchall()
                if conflicts:
                    from packages.semantic_query.selective_clarification import observe_regions
                    province = {"where_sql": "province=?", "where_params": (name,), "name": name,
                                "province": name, "city": None, "district": None,
                                "region_type": "省份", "full_name": name}
                    candidates = [province] + [_qualified_region(r, "地级市" if field == "city" else "区县") for r in conflicts if _formal_city(r)]
                    return True, observe_regions(clean, candidates), None
        return True, {
            "where_sql": "province=?", "where_params": (name,), "name": name,
            "province": name, "city": None, "district": None,
            "region_type": "省份", "full_name": name,
        }, name
    ancestors = [name for name, aliases in provinces.items()
                 if any(a != next((p for p in PROVINCES if name.startswith(p)), None) and a in clean for a in aliases)]
    if len(ancestors) > 1:
        return True, None, None
    return False, None, ancestors[0] if ancestors else None


def _qualified_region(row, level: str, name: Optional[str] = None, *, include_city: bool = True,
                      city_keyword: Optional[str] = None, district_keyword: Optional[str] = None) -> Dict[str, Any]:
    province, city = row["province"], row["city"]
    district = row["district"] if level == "区县" else None
    clauses, values = ["province=?"], [province]
    if include_city:
        clauses.append("city=?")
        values.append(city)
    if district:
        clauses.append("district LIKE ?" if district_keyword else "district=?")
        values.append("%" + district_keyword + "%" if district_keyword else district)
    return {
        "where_sql": " AND ".join(clauses),
        "where_params": tuple(values), "province": province, "city": city, "district": district,
        "name": name or district or city, "region_type": level,
        "full_name": " ".join(dict.fromkeys(str(v) for v in (province, city, district) if v)),
    }


def _formal_city(row) -> bool:
    city, province = row["city"], row["province"]
    return bool(city and re.fullmatch(r"[\u4e00-\u9fff]{2,10}(?:市|州|盟|地区)", city)
                and not (province and province.endswith("市") and city != province)
                and (not city.startswith(province or "\0")
                     or (city == province and province.endswith("市"))))


def detect_region(
    keyword: str,
    connection: Optional[sqlite3.Connection] = None,
    *, _metadata_only: bool = False, question: str = '', active_year=None,
) -> Optional[Dict[str, Any]]:
    """从关键词中识别行政区划（区县 > 地级市 > 省份），支持组合与自然语言提取。"""
    import re
    from packages.core.research_data import enabled, resolve_region, geographic_request, REQUEST, SelectionError, registry
    if enabled() and (not _metadata_only or registry()['version'].startswith('geo-select-v5-full-')):
        try:
            return geographic_request(keyword, question or REQUEST.get(), active_year) if question or active_year is not None else resolve_region(keyword)
        except SelectionError:
            return None

    clean = re.sub(r"20\d{2}\s*年?", " ", keyword)
    clean = re.sub(r"[，,]?\s*(?:用图展示|以图展示|以图表展示|通过图表展示|通过图展示|用图表展示|画图展示|画图对比|画图|生成图表|用图)[^，。？！]*", " ", clean)

    # 递归清洗前导前缀与标点符号
    while True:
        prev = clean
        clean = re.sub(r"^[，,、\s]+", "", clean)
        clean = re.sub(
            r"^(?:不要用|不用|排除|不要|除开|去掉|剔除|除了|非|我要|我想|想给|给|针对|关于|对|把|让|想对|想为|为|向|请给出|请列出|给出|列出|换到|换成|切换到|"
            r"沿用刚才的年份和维度|沿用刚才的年份|沿用刚才的维度|沿用|其他不变|"
            r"只接受|只看|只算|只统计|仅统计|统计|只纳入|纳入|算算|算|请问下|如果|若是|若|那|那么|"
            r"评估|查看|分析|查询|计算|测算|了解|请问|想问|给我|帮我|查一查|查下|查查|查|求|求解|求取|求下|检索|搜索|调取|获取|"
            r"说说|看一下|总结|诊断|我想看|想看|探讨|比较|对比)+",
            "",
            clean
        ).strip()
        clean = re.sub(r"^[，,、\s]+", "", clean)
        if clean == prev:
            break

    # 清除学段与办学性质过滤词
    clean = re.sub(
        r"(?:只纳入|纳入|仅包含|包含|只含|筛选|仅限|按|只看|只算|只统计|仅统计|统计)?\s*"
        r"(?:公办|民办|公立|私立|乡村|农村|镇区|城镇|城市)?\s*"
        r"(?:学校)?\s*"
        r"(?:初中(?:学校)?|小学(?:学校)?|高中(?:学校)?|初级中学|高级中学|中职|幼儿园|九年一贯制|十二年一贯制|中小学)",
        " ",
        clean
    )
    clean = re.sub(r"(?:只纳入|纳入|仅包含|包含|只含|筛选|仅限|按|只看|只算|只统计|仅统计|统计)\s*(?:公办|民办|公立|私立|乡村|农村|城镇)(?:学校)?", " ", clean)

    # 清除对比与纵向词汇及连接词
    clean = re.sub(r"(?:对比|比较|相对|变化|变动|上升多少|上升|下降|提升|增幅|趋势|走向|相比|比一比|比一下)+", " ", clean)
    # Do not erase internal name characters, e.g. 呼和浩特市 or 新和县.
    clean = re.sub(r"(?<=[省市县区旗州盟])(?:和|与|及|到|至)(?=[\u4e00-\u9fff])", " ", clean)

    clean = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", clean)
    clean_words = sorted([
        "的教学水平", "的数字化水平", "的数字化", "的教学", "的教育水平", "的水平",
        "教学水平", "数字化水平", "数字化", "水平", "怎么样", "大盘", "情况",
        "如何", "怎样", "表现", "？", "?", "整校", "整体", "具体", "有哪些",
        "有什么", "指标", "概况", "数据", "报告", "的整体数字化均分", "整体数字化均分",
        "全部指标的均分", "全部指标", "全部83项的数字化平均分", "全部83项", "的均分", "均分",
        "平均得分", "平均分", "平均值", "平均数", "均值", "中位数", "中位值", "是多少", "分别多少", "哪个高", "谁更高", "哪个更高",
        "谁高", "有提升吗", "高吗", "最高分学校是谁", "最低分学校是谁", "排名最后的学校",
        "最后一名", "最高分", "最低分", "是谁", "是哪所", "哪所", "谁", "两个维度", "维度",
        "各有多少所学校", "各有多少所", "有多少所学校", "有多少所", "学校数量", "各有多少", "有多少",
        "全部学校全部题目的作答放在一起平均", "全部题目的作答放在一起平均", "全部作答放在一起平均",
        "合在一起平均", "作答放在一起平均", "所有作答合并平均", "作答池化", "不要先按学校汇总", "不先按学校汇总",
        "先算每校均分再对学校等权平均", "学校等权平均", "每校等权平均", "学校等权均分", "每校均分", "等权平均", "池化平均", "池化",
        "现有数据中最近一年", "现有数据中最新一年", "现有数据中", "最近可用年", "最新可用年", "最近一年", "最新一年",
        "只接受2024年", "不要用2022年", "不是长沙市", "不是武汉市", "不是南京市", "不是杭州市", "不是成都市", "不是西安市",
        "先前说教育教学不对这次要", "先前说教育教学不对", "这次要",
        "纳入监测", "参与监测", "列入监测", "纳入评估", "纳入", "监测",
        "数字资源", "教育教学", "数字素养", "基础设施", "教育治理", "保障机制", "的", "有", "吗"
    ], key=len, reverse=True)
    for w in clean_words:
        clean = clean.replace(w, " ")
    clean = re.sub(r"(?:个|间|所)学校", " ", clean)

    clean = re.sub(r"[，,、？\?。！\!；;]+", " ", clean)
    clean = re.sub(r"^[\s\"'“”‘’【】《》（）()\[\]]+|[\s\"'“”‘’【】《》（）()\[\]]+$", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean or len(clean) < 2:
        return None

    conn = connection or connect()
    owns_connection = connection is None
    handled, province_scope, province_parent = _province_scope_literal(clean, conn)
    if handled:
        if owns_connection:
            conn.close()
        return province_scope
    if province_parent:
        short = next(p for p in PROVINCES if province_parent.startswith(p))
        for ancestor in sorted({province_parent, short + "自治区"}, key=len, reverse=True):
            clean = clean.replace(ancestor, " ").strip()
    parent_sql = " AND province=?" if province_parent else ""
    parent_params = (province_parent,) if province_parent else ()

    # 分解连续的行政区划层级 (如 七台河市新兴区 -> 七台河市 新兴区)
    m_compound = re.search(r"([\u4e00-\u9fff]{2,5}(?:省|市))([\u4e00-\u9fff]{2,5}(?:市|区|县|旗))", clean)
    if m_compound:
        clean = clean.replace(m_compound.group(0), f" {m_compound.group(1)} {m_compound.group(2)} ")

    # 提取行政区划单元 (如 七台河市、新兴区、长沙县、武汉市)
    raw_tokens = re.findall(r"[\u4e00-\u9fff]{2,6}(?:省|市|县|区|旗|州|盟)", clean)
    tokens = []
    for t in raw_tokens:
        t_c = re.sub(r"^(?:不要用|不用|排除|我要|我想|请给出|给出|换到|换成|切换到|沿用|只接受|只看|只算|只统计|仅统计|统计|只纳入|只含|只|把|对|在|从|是|有|算|查一查|查下|查查|查|求|求解|求取|求下|检索|搜索)+", "", t).strip()
        if len(t_c) >= 2:
            tokens.append(t_c)

    # 分词候选补全（支持纯地名如 武汉、长沙、七台河）
    for part in clean.split():
        p_c = part.strip()
        if len(p_c) >= 2 and p_c not in tokens:
            tokens.append(p_c)

    city_cand = None
    dist_cand = None
    prov_cand = None
    for t in tokens:
        if t.endswith(("区", "县", "旗")) and not dist_cand:
            dist_cand = t
        elif t.endswith("市") and not city_cand:
            city_cand = t
        elif t.endswith("省") and not prov_cand:
            prov_cand = t

    # 1. 组合情况：City + District (如：七台河市新兴区、武汉市蔡甸区)
    if city_cand and dist_cand:
        rows = conn.execute(
            "SELECT province, city, district, COUNT(*) as cnt FROM schools "
            "WHERE city LIKE ? AND district LIKE ? AND city NOT LIKE '%学校%'" + parent_sql
            + " GROUP BY province,city,district",
            (f"%{city_cand}%", f"%{dist_cand}%", *parent_params),
        ).fetchall()
        if len(rows) > 1:
            from packages.semantic_query.selective_clarification import observe_regions
            selected = observe_regions(city_cand + dist_cand, [_qualified_region(r, "区县") for r in rows])
            if owns_connection: conn.close()
            return selected
        if rows:
            row = rows[0]
            if owns_connection:
                conn.close()
            return _qualified_region(row, "区县", row["city"] + row["district"],
                                     city_keyword=city_cand, district_keyword=dist_cand)
        # An explicit child pair must not fall through to its city or province.
        if owns_connection: conn.close()
        return None

    # 2. 地市匹配优先（若包含市级特征，如 武汉市、七台河市、长沙市）
    cand_cities = ([city_cand] if city_cand else []) + [t for t in tokens if t.endswith("市") or not t.endswith(("区", "县", "旗", "省"))]
    for cand in cand_cities:
        if not cand or cand.endswith("省"):
            continue
        rows = conn.execute(
            "SELECT province, city, COUNT(*) as cnt FROM schools "
            "WHERE (city=? OR city LIKE ?) AND city NOT LIKE '%学校%' AND city NOT LIKE '%小学%' AND city NOT LIKE '%中学%' AND city NOT LIKE '%幼儿园%'"
            + parent_sql + " GROUP BY province,city",
            (cand, f"%{cand}%", *parent_params),
        ).fetchall()
        formal = [r for r in rows if _formal_city(r)]
        rows = formal or rows
        exact = [r for r in rows if r["city"] == cand]
        rows = [r for r in (exact or rows) if r["cnt"] > 5]
        if len(rows) > 1:
            from packages.semantic_query.selective_clarification import observe_regions
            selected = observe_regions(cand, [_qualified_region(r, "地级市") for r in rows])
            if owns_connection: conn.close()
            return selected
        if rows and rows[0]["city"] and rows[0]["cnt"] > 5:
            row = rows[0]
            if not cand.endswith("市"):
                county_conflicts = conn.execute(
                    "SELECT DISTINCT province,city,district FROM schools WHERE district IN (?,?,?,?) "
                    "AND district<>city AND district<>province" + parent_sql,
                    (cand, cand + "区", cand + "县", cand + "旗", *parent_params),
                ).fetchall()
                county_conflicts = [r for r in county_conflicts if _formal_city(r)
                                    and r["district"].endswith(("区", "县", "旗"))]
                if county_conflicts:
                    from packages.semantic_query.selective_clarification import observe_regions
                    selected = observe_regions(cand, [_qualified_region(row, "地级市")] +
                                               [_qualified_region(r, "区县") for r in county_conflicts])
                    if owns_connection: conn.close()
                    return selected
            if owns_connection:
                conn.close()
            return _qualified_region(row, "地级市", city_keyword=cand)

    # 3. 单独区县匹配 (如 新兴区、长沙县、蔡甸区)
    _GENERIC_REGION_STOPWORDS = {
        "地区", "该地区", "本地区", "这个地区", "所在地区", "片区", "校区", "城区", "辖区", "同地区", "刚才的地区", "上一地区", "全区"
    }
    cand_districts = (
        [dist_cand] if dist_cand and dist_cand not in _GENERIC_REGION_STOPWORDS else []
    ) + [
        t for t in tokens
        if t.endswith(("区", "县", "旗")) and t not in _GENERIC_REGION_STOPWORDS
    ]
    for cand in cand_districts:
        if not cand:
            continue
        rows = conn.execute(
            "SELECT province, city, district, COUNT(*) as cnt FROM schools "
            "WHERE (district=? OR district LIKE ?) AND district NOT LIKE '%学校%' AND district NOT LIKE '%小学%'"
            + parent_sql + " GROUP BY province,city,district",
            (cand, f"%{cand}%", *parent_params),
        ).fetchall()
        exact = [r for r in rows if r["district"] == cand]
        rows = exact or rows
        # Lower-field garbage must not create a second administrative identity.
        formal = [r for r in rows if _formal_city(r)]
        rows = formal or rows
        if len(rows) > 1:
            from packages.semantic_query.selective_clarification import observe_regions
            selected = observe_regions(cand, [_qualified_region(r, "区县") for r in rows])
            if owns_connection: conn.close()
            return selected
        if rows and rows[0]["district"] and not rows[0]["district"].endswith("省"):
            row = rows[0]
            if owns_connection:
                conn.close()
            return _qualified_region(row, "区县", include_city=False)

    if province_parent or city_cand or dist_cand:
        # A mismatching explicit ancestor/child is unresolved, never a broader province.
        if owns_connection: conn.close()
        return None

    # Complete province literals were resolved above. An unresolved compound
    # must not widen to a province merely because it contains a province alias.
    if owns_connection:
        conn.close()
    return None


def evaluate_region(
    region_keyword: str,
    year: Optional[str] = None,
    target_rank: Optional[int] = None,
    target_top_n: Optional[int] = None,
    target_dimension: Optional[str] = None,
    target_dimensions: Optional[List[str]] = None,
    target_stat: Optional[str] = None,
    school_filter: Optional[str] = None,
    original_query: Optional[str] = None,
    aggregation: Optional[str] = "school_equal_mean",
    operation: str = "region_evaluation",
    indicator_ids: Optional[List[int]] = None,
    value_view: Optional[str] = "normalized_score",
    connection: Optional[sqlite3.Connection] = None,
    region_identity: Optional[Dict[str, Any]] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """对指定区域开展宏观数字化水平评估，支持维度、统计量、精准名次与前N名提取。"""
    if topic is not None:
        from packages.semantic_query.catalogue import topic_definition
        from packages.semantic_query.topic_tasks import NOTE,label
        from packages.core.item_collection import collected_ids
        if value_view!='normalized_score' or target_stat not in (None,'mean') or aggregation!='school_equal_mean':
            raise ValueError('topic_normalized_mean_only')
        ids=topic_definition(topic)['indicator_ids']
        result=evaluate_region(region_keyword,year=year,target_rank=target_rank,target_top_n=target_top_n,
            target_stat='mean',school_filter=school_filter,original_query=original_query,aggregation='school_equal_mean',
            operation=operation,indicator_ids=ids,value_view='normalized_score',connection=connection,region_identity=region_identity)
        result.update(topic=topic,measurement_note=NOTE,indicator_ids=ids)
        if not collected_ids(year,ids):
            result.update(status='empty_filter',school_count=0,all_items_uncollected=True,message='该二级维度所含题项当年全部未采集。')
        result['direct_answer']=label(topic)+'：'+('无数据。'+result.get('message','') if result.get('error') else str(result.get('total_score'))+'分。')+NOTE
        return result
    if region_identity is not None:
        from packages.semantic_query.typed_plan import region_sql
        reg = region_sql(region_identity)
    else:
        reg = detect_region(region_keyword, connection=connection, question=original_query or '', active_year=year)
    if not reg:
        return {"error": f"未能识别地区「{region_keyword}」（支持如：长沙县、七台河市、湖南省等区县、地级市与省份名称）"}

    where_sql = reg["where_sql"]
    where_params = reg["where_params"]

    conn = connection or connect()
    owns_connection = connection is None

    # 1. 探查该地区所有有记录的学年与学校数量
    year_rows = conn.execute(
        f"SELECT year, COUNT(*) as cnt FROM schools WHERE {where_sql} GROUP BY year ORDER BY cnt DESC",
        where_params,
    ).fetchall()
    if not year_rows:
        if owns_connection:
            conn.close()
        return {"error": f"未检索到地区「{reg['name']}」的学校建档数据", "status": "empty_filter"}

    all_years = sorted(list({str(r["year"]) for r in year_rows}))
    if year is not None and str(year).strip():
        req_year = str(year).strip()
        if req_year in ("latest", "最近", "最新", "最近一年", "最新一年", "现有数据中最近一年", "最近可用年", "最新可用年"):
            active_year = max(all_years)
            default_year_applied = False
        elif req_year not in all_years:
            if owns_connection:
                conn.close()
            return {
                "error": f"地区「{reg['name']}」在 {req_year} 年无监测数据（可用学年：{', '.join(all_years)}）",
                "requested_year": req_year,
                "available_years": all_years,
                "status": "unavailable_year",
            }
        else:
            active_year = req_year
            default_year_applied = False
    else:
        # 用户未指定年份时，采用有说明的默认策略：采用建档学校样本最全的年份
        active_year = str(year_rows[0]["year"])
        default_year_applied = True

    # 2. 统计当前学年未筛选前的基准学校总数
    full_where_sql = f"({where_sql}) AND year=?"
    full_where_params = where_params + (active_year,)

    schools_in_year = conn.execute(
        f"SELECT id, school_name, school_type, teacher_num, student_num FROM schools WHERE {full_where_sql}",
        full_where_params,
    ).fetchall()
    total_region_schools = len(schools_in_year)
    if total_region_schools == 0:
        if owns_connection:
            conn.close()
        return {
            "error": f"地区「{reg['name']}」在 {active_year} 年无监测学校记录（可用学年：{', '.join(all_years)}）",
            "requested_year": str(year).strip() if (year is not None and str(year).strip()) else None,
            "available_years": all_years,
            "status": "unavailable_year",
        }

    # 3. 构建群体筛选过滤子句 (办学性质、学段、城乡)
    filter_clause = ""
    if school_filter:
        # 办学性质缺少可靠结构化字段，禁止通过校名关键词推断。
        if any(k in school_filter for k in ("公办", "公立", "民办", "私立")):
            if owns_connection:
                conn.close()
            return {
                "error": "当前真实库没有可靠的办学性质字段，无法按公办/民办筛选；校名关键词仅能视为未知推断，不能作为统计真值",
                "status": "unsupported_filter_metadata",
            }

        from packages.core.school_filters import filter_sql
        filter_clause, _ = filter_sql(school_filter)

    # 4. 统计各校平均得分并排序
    s_where = f"({where_sql.replace('city', 's.city').replace('district', 's.district').replace('province', 's.province')}) AND s.year=?{filter_clause}"
    ranking_indicator_ids = tuple(int(value) for value in (indicator_ids or ()))
    if target_dimension and not ranking_indicator_ids:
        from packages.semantic_query.catalogue import load_semantic_catalogue
        ranking_indicator_ids = tuple(x['id'] for x in load_semantic_catalogue()['items'] if x['dimension'] == target_dimension)
    ranking_indicator_clause = ""
    ranking_indicator_params: Tuple[Any, ...] = ()
    if ranking_indicator_ids:
        ranking_indicator_clause = (
            " AND a.question_id IN ("
            + ",".join("?" for _ in ranking_indicator_ids)
            + ")"
        )
        ranking_indicator_params = ranking_indicator_ids
    val_expr = "a.value" if value_view == "raw_value" else "a.normalized_value*100"
    val_filter = "a.value IS NOT NULL" if value_view == "raw_value" else "a.normalized_value IS NOT NULL"
    school_scores_rows = conn.execute(
        f"""
        SELECT s.id, s.school_name, s.school_type, s.school_area_type,
               AVG({val_expr}) as avg_score,
               COUNT({val_expr}) as answered_cnt
        FROM schools s
        JOIN answers a ON s.id = a.school_id
        WHERE {s_where} AND {val_filter}{ranking_indicator_clause}
        GROUP BY s.id
        ORDER BY avg_score DESC, s.id ASC
        """,
        full_where_params + ranking_indicator_params,
    ).fetchall()

    school_count = len(school_scores_rows)
    if school_count == 0:
        if owns_connection:
            conn.close()
        return {
            "error": f"地区「{reg['name']}」在 {active_year} 年没有符合筛选条件的有效学校作答",
            "requested_year": str(year).strip() if (year is not None and str(year).strip()) else None,
            "available_years": all_years,
            "status": "empty_filter",
        }
    if operation == "region_ranking" and target_rank not in (None, -1) and target_rank > school_count:
        if owns_connection:
            conn.close()
        return {
            "error": (
                f"{reg['full_name']}在 {active_year} 年该排名总体仅有 {school_count} 所有效学校，"
                f"无第 {target_rank} 名可返回"
            ),
            "status": "unavailable_rank",
            "requested_rank": target_rank,
            "eligible_school_count": school_count,
        }

    scores = [float(r["avg_score"]) for r in school_scores_rows if r["avg_score"] is not None]
    if not scores:
        if owns_connection:
            conn.close()
        return {"error": f"地区「{reg['name']}」在 {active_year} 年尚未录入有效问卷作答"}

    raw_avg_score = math.fsum(scores) / len(scores)
    avg_score = round(raw_avg_score, 2)
    max_score = round(max(scores), 2)
    min_score = round(min(scores), 2)
    sorted_scores = sorted(scores)
    raw_median_score = statistics.median(sorted_scores)
    median_score = round(raw_median_score, 2)
    mean_median_difference = round(raw_avg_score - raw_median_score, 2)
    mean_median_relation = "两者相同" if mean_median_difference == 0 else "两者不同"

    # 若指定作答池化平均 (answer_pooled_mean)，对所有学校、全部题目的原始作答合并池化计算
    n_answers = sum(int(r["answered_cnt"] or 0) for r in school_scores_rows)
    if aggregation == "answer_pooled_mean":
        pooled_dim_where = f"{s_where} AND q.level1_name=?" if target_dimension else s_where
        pooled_dim_params = full_where_params + (target_dimension,) if target_dimension else full_where_params
        pooled_from = "FROM schools s JOIN answers a ON s.id = a.school_id JOIN questions q ON a.question_id = q.id" if target_dimension else "FROM schools s JOIN answers a ON s.id = a.school_id"
        pooled_row = conn.execute(
            f"""
            SELECT AVG(a.normalized_value)*100 as pooled_score,
                   COUNT(DISTINCT s.id) as n_schools,
                   COUNT(a.value) as n_answers
            {pooled_from}
            WHERE {pooled_dim_where} AND a.normalized_value IS NOT NULL{ranking_indicator_clause}
            """,
            pooled_dim_params + ranking_indicator_params,
        ).fetchone()
        raw_avg_score = float(pooled_row["pooled_score"]) if pooled_row and pooled_row["pooled_score"] is not None else raw_avg_score
        pooled_score = round(raw_avg_score, 2)
        n_answers = int(pooled_row["n_answers"]) if pooled_row and pooled_row["n_answers"] else 0
        avg_score = pooled_score

    # 5. 六大维度均分汇总
    if aggregation == "answer_pooled_mean":
        dim_sql = f"""
            SELECT q.level1_name, ROUND(AVG(a.normalized_value)*100, 2) AS dim_score,
                   AVG(a.normalized_value)*100 AS raw_dim_score,
                   COUNT(a.normalized_value) AS cnt, COUNT(DISTINCT s.id) AS school_count
            FROM schools s JOIN answers a ON s.id=a.school_id JOIN questions q ON a.question_id=q.id
            WHERE {s_where} AND a.normalized_value IS NOT NULL GROUP BY q.level1_name
        """
    else:
        dim_sql = f"""
            SELECT level1_name, ROUND(AVG(school_dim_score), 2) AS dim_score,
                   AVG(school_dim_score) AS raw_dim_score,
                   SUM(cnt) AS cnt, COUNT(*) AS school_count
            FROM (
                SELECT s.id, q.level1_name, AVG(a.normalized_value)*100 AS school_dim_score,
                       COUNT(a.normalized_value) AS cnt
                FROM schools s JOIN answers a ON s.id=a.school_id JOIN questions q ON a.question_id=q.id
                WHERE {s_where} AND a.normalized_value IS NOT NULL
                GROUP BY s.id, q.level1_name
            ) GROUP BY level1_name
        """
    dim_rows = conn.execute(dim_sql, full_where_params).fetchall()

    dim_map = {r["level1_name"]: float(r["dim_score"]) for r in dim_rows}
    raw_dim_map = {r["level1_name"]: float(r["raw_dim_score"]) for r in dim_rows}
    dim_counts = {
        r["level1_name"]: {"school_count": int(r["school_count"] or 0), "answer_count": int(r["cnt"] or 0)}
        for r in dim_rows
    }
    dimensions_out = []
    dim_radar = {}
    for d_name in STANDARD_DIMENSIONS:
        sc = dim_map.get(d_name, 0.0)
        dimensions_out.append({
            "dimension": d_name,
            "score": sc,
            "weight": round(1.0 / len(STANDARD_DIMENSIONS), 4),
            "conclusion": f"区域均分 {sc} 分",
        })
        dim_radar[d_name] = sc

    # 6. 分层结构统计
    tiers = [
        {"tier": "优秀梯队 (≥20分)", "min": 20.0, "max": 999.0, "count": 0},
        {"tier": "良好梯队 (15~20分)", "min": 15.0, "max": 20.0, "count": 0},
        {"tier": "平稳发展 (10~15分)", "min": 10.0, "max": 15.0, "count": 0},
        {"tier": "薄弱待建 (<10分)", "min": 0.0, "max": 10.0, "count": 0},
    ]
    for s in scores:
        for t in tiers:
            if t["min"] <= s < t["max"] or (s == t["max"] and t["max"] == 999.0):
                t["count"] += 1
                break

    for t in tiers:
        t["pct"] = f"{(t['count'] / len(scores) * 100):.1f}%"

    # 7. Top 榜单与目标名次/极值提取
    target_school = None
    direct_answer = None
    target_dimensions_detail = None
    diff = None
    effective_top_n = (
        min(target_top_n, len(school_scores_rows))
        if operation == "region_ranking" and target_top_n
        else 5
    )
    if operation != "region_ranking" and target_rank and target_rank > effective_top_n and target_rank <= len(school_scores_rows):
        effective_top_n = min(len(school_scores_rows), target_rank + 2)

    top_schools = [
        {
            "rank": idx + 1,
            "id": r["id"],
            "school_name": r["school_name"],
            "school_type": format_school_type(r["school_type"]),
            "score": round(float(r["avg_score"]), 2),
            "avg_score": round(float(r["avg_score"]), 2),
            "total_score": round(float(r["avg_score"]), 2),
            "is_target": bool(target_rank and (idx + 1 == target_rank)),
        }
        for idx, r in enumerate(school_scores_rows[:effective_top_n])
    ]

    bottom_schools = [
        {
            "rank": len(school_scores_rows) - idx,
            "id": r["id"],
            "school_name": r["school_name"],
            "school_type": format_school_type(r["school_type"]),
            "score": round(float(r["avg_score"]), 2),
            "avg_score": round(float(r["avg_score"]), 2),
            "total_score": round(float(r["avg_score"]), 2),
        }
        for idx, r in enumerate(reversed(school_scores_rows[-5:]))
    ]

    # 若指定了具体名次 (如第 2 名、第 1 名、榜首、倒数第 1)
    if target_rank is not None:
        rank_idx = len(school_scores_rows) - 1 if target_rank == -1 else target_rank - 1
        if 0 <= rank_idx < len(school_scores_rows):
            actual_rank = rank_idx + 1
            t_row = school_scores_rows[rank_idx]
            t_name = t_row["school_name"]
            t_type = format_school_type(t_row["school_type"])
            t_score = round(float(t_row["avg_score"]), 2)
            top_pct = round(actual_rank / len(school_scores_rows) * 100, 1)
            beat_pct = round((len(school_scores_rows) - actual_rank) / len(school_scores_rows) * 100, 1)
            diff_avg = round(t_score - avg_score, 2)
            lead_str = f"高于全区均分 +{diff_avg} 分" if diff_avg >= 0 else f"低于全区均分 {diff_avg} 分"

            target_school = {
                "rank": actual_rank,
                "id": t_row["id"],
                "school_name": t_name,
                "school_type": t_type,
                "score": t_score,
                "is_target": True,
                "top_pct": top_pct,
                "beat_pct": beat_pct,
                "diff_from_avg": diff_avg,
            }
            if target_rank == -1:
                direct_answer = (
                    f"{reg['full_name']} 在 {active_year} 年各学校数字化综合分的最低分为 **{t_score} 分**"
                    f"（由倒数第 1 名 / 第 {actual_rank} 名【**{t_name}**】({t_type})取得，"
                    f"在全区 {len(school_scores_rows)} 所监测学校中位列末位，{lead_str}）。"
                )
            else:
                direct_answer = (
                    f"{reg['full_name']}数字化水平第 **{actual_rank}** 名是【**{t_name}**】（{t_type}），"
                    f"{active_year} 年数字化综合得分 **{t_score} 分**"
                    f"（在全区 {len(school_scores_rows)} 所监测学校中位列前 **{top_pct}%**，超越全区 **{beat_pct}%** 的学校，{lead_str}）。"
                )

    # 若指定了前 N 名榜单提问
    elif target_top_n:
        n_items = min(target_top_n, len(school_scores_rows))
        top_items = [
            f"第 {idx+1} 名【{r['school_name']}】({round(float(r['avg_score']), 2)}分)"
            for idx, r in enumerate(school_scores_rows[:n_items])
        ]
        direct_answer = f"{reg['full_name']}在 {active_year} 年数字化发展排名前 {n_items} 的学校依次为：{'；'.join(top_items)}。"

    # 若指定了多维度并列提问 (如：数字资源、教育教学两个维度分别多少 / 哪个更高)
    elif target_dimensions and len(target_dimensions) >= 2:
        dim_info_list = []
        for d in target_dimensions:
            if d in dim_map:
                sorted_d_ranks = sorted(dim_map.items(), key=lambda x: x[1], reverse=True)
                d_rank = [k for k, _ in sorted_d_ranks].index(d) + 1
                dim_info_list.append((d, dim_map[d], d_rank))
        if len(dim_info_list) >= 2:
            target_dimensions_detail = [
                {"dimension": d, "score": sc, "rank": rk}
                for d, sc, rk in dim_info_list
            ]
            dim_strs = [f"【{d}】等权均分为{sc:.2f}分" for d, sc, rk in dim_info_list]
            d1_name, d1_sc, _ = dim_info_list[0]
            d2_name, d2_sc, _ = dim_info_list[1]
            diff = round(d1_sc - d2_sc, 2)
            filter_str = f"纳入【{school_filter}】后，" if school_filter else ""
            direct_answer = (
                f"{reg.get('name') or reg['full_name']}在{active_year}年{filter_str}"
                f"{'，'.join(dim_strs)}，"
                f"前者减后者差值为{diff:+.2f}分（分差{diff:.2f}分）。"
            )

    # 学段筛选与统计量直答（仅在未被上位目标排位/榜单/多维对比命中的情况下生成）
    target_dimension_score = dim_map.get(target_dimension) if target_dimension else None
    if direct_answer is None:
        if school_filter:
            filter_label = school_filter
            if target_dimension and target_dimension in dim_map and target_stat in ("median", "mean_and_median"):
                target_dimension_score = dim_map[target_dimension]
                if target_stat == "mean_and_median":
                    direct_answer = (
                        f"{reg['full_name']} 在 {active_year} 年纳入【{filter_label}】后，"
                        f"【{target_dimension}】维度各学校得分的均值为 **{avg_score} 分**，"
                        f"中位数为 **{median_score} 分**，均值减中位数为 **{mean_median_difference} 分**，{mean_median_relation}。"
                    )
                else:
                    direct_answer = (
                        f"{reg['full_name']} 在 {active_year} 年纳入【{filter_label}】后，"
                        f"【{target_dimension}】维度各学校得分的中位数为 **{median_score} 分**。"
                    )
            elif target_dimension and target_dimension in dim_map:
                target_dimension_score = dim_map[target_dimension]
                direct_answer = (
                    f"{reg['full_name']} 在 {active_year} 年纳入【{filter_label}】后，"
                    f"【{target_dimension}】维度的区域平均得分为 **{dim_map[target_dimension]} 分**，"
                    f"共监测 {len(scores)} 所学校（全区在册样本共 {total_region_schools} 所）。"
                )
            elif target_stat == "mean_and_median":
                direct_answer = (
                    f"{reg['full_name']} 在 {active_year} 年纳入【{filter_label}】后，"
                    f"数字化综合均分（平均值）为 **{avg_score} 分**，中位数为 **{median_score} 分**，"
                    f"均值减中位数为 **{mean_median_difference} 分**，{mean_median_relation}，"
                    f"共监测 {len(scores)} 所学校（全区在册样本共 {total_region_schools} 所）。"
                )
            elif target_stat == "median":
                direct_answer = (
                    f"{reg['full_name']} 在 {active_year} 年纳入【{filter_label}】后，"
                    f"各学校数字化综合分的中位数为 **{median_score} 分**，"
                    f"共监测 {len(scores)} 所学校（全区在册样本共 {total_region_schools} 所）。"
                )
            elif target_stat == "count":
                direct_answer = (
                    f"{reg['full_name']} 在 {active_year} 年纳入【{filter_label}】后，"
                    f"共监测记录 **{len(scores)} 所学校**（全区在册样本共 {total_region_schools} 所学校，筛选后综合均分为 {avg_score} 分）。"
                )
            else:
                direct_answer = (
                    f"{reg['full_name']} 在 {active_year} 年纳入【{filter_label}】后，"
                    f"数字化综合均分为 **{avg_score} 分**（最高分 {max_score} 分，"
                    f"最低分 {min_score} 分，共监测 {len(scores)} 所学校，全区在册样本共 {total_region_schools} 所）。"
                )

        # 若指定了特定单宏观维度提问
        elif target_dimension and target_dimension in dim_map and target_stat in ("median", "mean_and_median"):
            target_dimension_score = dim_map[target_dimension]
            if target_stat == "mean_and_median":
                direct_answer = (
                    f"{reg['full_name']} 在 {active_year} 年【{target_dimension}】维度各学校得分的"
                    f"均值为 **{avg_score} 分**，中位数为 **{median_score} 分**，"
                    f"均值减中位数为 **{mean_median_difference} 分**，{mean_median_relation}。"
                )
            else:
                direct_answer = (
                    f"{reg['full_name']} 在 {active_year} 年【{target_dimension}】维度各学校得分的"
                    f"中位数为 **{median_score} 分**。"
                )

        elif target_dimension and target_dimension in dim_map:
            d_score = dim_map[target_dimension]
            target_dimension_score = d_score
            sorted_d_ranks = sorted(dim_map.items(), key=lambda x: x[1], reverse=True)
            d_rank = [k for k, _ in sorted_d_ranks].index(target_dimension) + 1 if target_dimension in [k for k, _ in sorted_d_ranks] else 1
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年【{target_dimension}】维度的区域平均得分为 **{d_score} 分**"
                f"（在六大监测维度中位列第 {d_rank} 名，区域综合总均分为 {avg_score} 分，共监测 {len(scores)} 所学校）。"
            )

        # 若指定了平均值和中位数并列统计量提问
        elif target_stat == "mean_and_median":
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年各学校数字化综合均分（平均值）为 **{avg_score} 分**，"
                f"中位数为 **{median_score} 分**，均值减中位数为 **{mean_median_difference} 分**，{mean_median_relation}"
                f"（最高分 {max_score} 分，最低分 {min_score} 分，共监测 {len(scores)} 所学校）。"
            )

        # 若指定了中位数统计量提问
        elif target_stat == "median":
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年各学校数字化综合分的中位数为 **{median_score} 分**"
                f"（区域综合均分为 {avg_score} 分，最高分 {max_score} 分，最低分 {min_score} 分，共监测 {len(scores)} 所学校）。"
            )

        # 若指定了学校总数统计量提问
        elif target_stat == "count":
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年共有 **{len(scores)} 所中小学校**纳入数字化监测评估"
                f"（区域数字化综合总均分为 {avg_score} 分）。"
            )

        # 若指定了均分统计量提问 (均分 / 平均分)
        elif target_stat == "mean":
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年数字化综合均分为 **{avg_score} 分**"
                f"（最高分 {max_score} 分，最低分 {min_score} 分，中位数 {median_score} 分，共监测 {len(scores)} 所学校）。"
            )

        # 若指定了极值统计量提问 (最高分 / 最低分)
        elif target_stat == "max":
            t_top = top_schools[0] if top_schools else {}
            t_name = t_top.get("school_name", "")
            t_type = t_top.get("school_type", "")
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年各学校数字化综合分的最高分为 **{max_score} 分**"
                f"（由第 1 名【{t_name}】{f'({t_type})' if t_type else ''}取得，区域综合均分为 {avg_score} 分，共监测 {len(scores)} 所学校）。"
            )
            target_school = {
                "rank": 1,
                "id": t_top.get("id"),
                "school_name": t_name,
                "school_type": t_type,
                "score": max_score,
                "diff_from_avg": round(max_score - avg_score, 2),
            }
        elif target_stat == "min":
            t_bot = school_scores_rows[-1]
            t_name = t_bot["school_name"]
            t_type = format_school_type(t_bot["school_type"])
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年各学校数字化综合分的最低分为 **{min_score} 分**"
                f"（由第 {len(school_scores_rows)} 名【{t_name}】({t_type})取得，区域综合均分为 {avg_score} 分，共监测 {len(scores)} 所学校）。"
            )
            target_school = {
                "rank": len(school_scores_rows),
                "id": t_bot["id"],
                "school_name": t_name,
                "school_type": t_type,
                "score": min_score,
                "diff_from_avg": round(min_score - avg_score, 2),
            }
        elif target_stat in ("min_and_max", "max_and_min"):
            t_top = school_scores_rows[0]
            t_bot = school_scores_rows[-1]
            diff = round(max_score - min_score, 2)
            direct_answer = (
                f"{reg['full_name']}在 {active_year} 年有记录的学校中，"
                f"整体分数最高端为 {max_score:.2f} 分（【{t_top['school_name']}】），"
                f"最低端为 {min_score:.2f} 分（【{t_bot['school_name']}】），"
                f"两端相隔 {diff:.2f} 分（共监测 {len(scores)} 所学校）。"
            )

    # 兜底直接结论（雷达图或大盘常态提问）
    if direct_answer is None:
        if aggregation == "answer_pooled_mean":
            dim_clause = f"【{target_dimension}】维度" if target_dimension else "全部83项指标"
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年（全部作答池化平均 / 原始作答直接合并计算，非学校等权均分）{dim_clause}均分为 **{avg_score} 分**"
                f"（覆盖 {len(scores)} 所学校，共 {n_answers} 项有效作答）。"
            )
        elif original_query and any(k in original_query for k in ("雷达图", "六维", "六大维度", "各维度", "维度分布")):
            dim_parts = [f"【{d['dimension']}】{d['score']}分" for d in dimensions_out]
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年数字化大盘综合均分为 **{avg_score} 分**，"
                f"六大维度表现分别为：{'，'.join(dim_parts)}（共监测 {len(scores)} 所学校）。"
            )
        else:
            direct_answer = (
                f"{reg['full_name']} 在 {active_year} 年数字化大盘综合均分为 **{avg_score} 分**"
                f"（最高分 {max_score} 分，最低分 {min_score} 分，中位数 {median_score} 分，共监测 {len(scores)} 所学校）。"
            )

    # 8. 寻找优势与短板维度
    sorted_dims = sorted(dim_map.items(), key=lambda x: x[1], reverse=True)
    strongest_dim = sorted_dims[0] if sorted_dims else ("-", 0)
    weakest_dim = sorted_dims[-1] if sorted_dims else ("-", 0)

    if owns_connection:
        conn.close()

    ranking_items = (
        list(top_schools)
        if operation == "region_ranking" and target_top_n is not None
        else ([dict(target_school)] if operation == "region_ranking" and target_school else [])
    )
    execution_scope = {
        "query_template": "region_evaluation_v2",
        "region": reg["full_name"],
        "period": active_year,
        "dimension": target_dimension,
        "statistic": target_stat,
        "aggregation": aggregation,
        "school_filter": school_filter,
    }
    if operation == "region_evaluation" and target_dimensions and len(target_dimensions) == 2:
        execution_scope["dimensions"] = list(target_dimensions)
        execution_scope["indicator_ids"] = list(ranking_indicator_ids)
    if operation == "region_evaluation" and target_dimension is None and len(ranking_indicator_ids) == 1:
        execution_scope = {
            "query_template": "region_item_mean_v1",
            "region": reg["full_name"],
            "period": active_year,
            "dimension": None,
            "statistic": target_stat or "mean",
            "aggregation": "school_equal_mean",
            "school_filter": school_filter,
            "indicator_ids": list(ranking_indicator_ids),
        }
    if operation == "region_ranking":
        execution_scope = {
            "query_template": "region_ranking_v1",
            "region": reg["full_name"],
            "period": active_year,
            "dimension": None,
            "statistic": "mean",
            "aggregation": "school_equal_mean",
            "school_filter": school_filter,
            "target_rank": target_rank,
            "target_top_n": target_top_n,
            "indicator_ids": list(ranking_indicator_ids),
            "ranking_spec": dict(REGION_RANKING_SPEC),
        }

    result = {
        "object_type": "region",
        "analysis_operation": operation,
        "region_name": reg["full_name"],
        "short_name": reg["name"],
        "region_type": reg["region_type"],
        "school_years": [active_year],
        "year": active_year,
        "requested_year": str(year).strip() if (year is not None and str(year).strip()) else None,
        "default_year_applied": default_year_applied,
        "all_years": all_years,
        "aggregation": aggregation,
        "answer_count": n_answers,
        "school_count": school_count,
        "total_unfiltered_count": total_region_schools,
        "total_score": avg_score,
        "raw_total_score": raw_avg_score,
        "raw_dimension_scores": raw_dim_map,  # internal execution precision; never a display recomputation
        "max_score": max_score,
        "min_score": min_score,
        "median_score": median_score,
        "target_dimension_median_score": median_score if target_dimension else None,
        "mean_median_difference": mean_median_difference if target_stat == "mean_and_median" else None,
        "strongest_dim": f"{strongest_dim[0]}（{strongest_dim[1]}分）",
        "weakest_dim": f"{weakest_dim[0]}（{weakest_dim[1]}分）",
        "dimensions": dimensions_out,
        "radar_data": dim_radar,
        "tiers": tiers,
        "top_schools": top_schools,
        "bottom_schools": bottom_schools,
        "target_rank": target_rank,
        "target_top_n": target_top_n,
        "target_school": target_school,
        "ranking_items": ranking_items,
        "returned_school_count": len(ranking_items),
        "ranking_spec": dict(REGION_RANKING_SPEC) if operation == "region_ranking" else None,
        "ranking_population": (
            {
                "eligible_school_count": school_count,
                "unfiltered_school_count": total_region_schools,
                "answer_count": n_answers,
                "school_filter": school_filter,
            }
            if operation == "region_ranking" else None
        ),
        "target_dimension": target_dimension,
        "target_dimension_score": target_dimension_score,
        "raw_target_dimension_score": raw_dim_map.get(target_dimension) if target_dimension else None,
        "requested_school_count": (
            dim_counts.get(target_dimension, {}).get("school_count") if target_dimension else school_count
        ),
        "requested_answer_count": (
            dim_counts.get(target_dimension, {}).get("answer_count") if target_dimension else n_answers
        ),
        "target_dimensions": target_dimensions,
        "target_dimensions_detail": target_dimensions_detail,
        "diff": diff,
        "requested_difference": diff,
        "target_stat": target_stat,
        "school_filter": school_filter,
        "value_view": value_view,
        "original_query": original_query,
        "execution_scope": execution_scope,
        "school_name": target_school["school_name"] if target_school else None,
        "direct_answer": direct_answer,
        "data_coverage": 1.0,
        "overall_confidence": 1.0,
        "conclusion": (
            f"{reg['full_name']} 在 {active_year} 年（全部作答池化平均 / 原始作答直接合并计算，非学校等权均分）数字化均分为 **{avg_score} 分**，"
            f"共覆盖 {school_count} 所学校、{n_answers} 项有效作答。"
            if aggregation == "answer_pooled_mean"
            else (
                f"{reg['full_name']} 在 {active_year} 年共有 {school_count} 所中小学校纳入数字化监测评估，"
                f"区域数字化综合均分为 **{avg_score} 分**（最高分 {max_score} 分，最低分 {min_score} 分）。"
                f"其中相对优势维度为【{strongest_dim[0]}】（{strongest_dim[1]}分），"
                f"最主要的发展短板为【{weakest_dim[0]}】（{weakest_dim[1]}分）。"
            )
        ),
    }
    return result


def evaluate_region_longitudinal(
    region_keyword: str,
    year_a: str,
    year_b: str,
    school_filter: Optional[str] = None,
    target_dimensions: Optional[List[str]] = None,
    target_dimension: Optional[str] = None,
    target_stat: Optional[str] = "mean",
    aggregation: str = "school_equal_mean",
    original_query: Optional[str] = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Compare two annual regional cross-sections under one shared scope."""
    y_start, y_end = str(year_a).strip(), str(year_b).strip()
    if any(len(value) != 4 or not value.isdigit() or not value.startswith("20") for value in (y_start, y_end)):
        return {"error": "两期截面变化首批仅接受两个明确日历年份"}
    if y_start == y_end:
        return {"error": "两期截面变化必须提供两个不同年份"}
    if y_start > y_end:
        return {"error": "两期截面变化要求基期早于末期；反向表达须先在路由层明确归一"}
    target_stat = target_stat or "mean"
    if target_stat not in {"mean", "count"}:
        return {"error": f"两期截面变化不支持统计量「{target_stat}」"}
    if len(target_dimensions or ()) > 1:
        return {"error": "两期截面变化首批仅支持整体或一个维度"}
    if target_dimension and target_stat == "count":
        return {"error": "有效学校数量的两期变化不能同时限定问卷维度"}

    conn = connection or connect()
    owns_connection = connection is None
    try:
        if owns_connection and not conn.in_transaction:
            conn.execute("BEGIN")
        reg = detect_region(region_keyword, connection=conn)
        if not reg:
            return {"error": f"未能识别地区「{region_keyword}」（支持区县、地级市与省份名称）"}

        evaluation_args = {
            "school_filter": school_filter,
            "target_dimension": target_dimension,
            "target_dimensions": target_dimensions,
            "target_stat": target_stat,
            "aggregation": aggregation,
            "original_query": original_query,
            "connection": conn,
        }
        eval_a = evaluate_region(region_keyword, year=y_start, **evaluation_args)
        if "error" in eval_a:
            return {"error": f"基期 {y_start} 年：{eval_a['error']}"}
        eval_b = evaluate_region(region_keyword, year=y_end, **evaluation_args)
        if "error" in eval_b:
            return {"error": f"末期 {y_end} 年：{eval_b['error']}"}

        score_key = "target_dimension_score" if target_dimension else "total_score"
        score_a = eval_a.get(score_key)
        score_b = eval_b.get(score_key)
        if target_stat == "mean" and (score_a is None or score_b is None):
            return {"error": "两期中至少一期没有符合筛选条件的有效学校作答"}

        school_count_a = int((
            eval_a.get("requested_school_count")
            if target_dimension else eval_a.get("school_count")
        ) or 0)
        school_count_b = int((
            eval_b.get("requested_school_count")
            if target_dimension else eval_b.get("school_count")
        ) or 0)
        answer_count_a = int((
            eval_a.get("requested_answer_count")
            if target_dimension else eval_a.get("answer_count")
        ) or 0)
        answer_count_b = int((
            eval_b.get("requested_answer_count")
            if target_dimension else eval_b.get("answer_count")
        ) or 0)
        if target_stat == "count":
            value_a = school_count_a
            value_b = school_count_b
            value_unit = "所"
            value_type = "effective_school_count"
            trend_up, trend_down = "增加", "减少"
        else:
            value_a = round(float(score_a), 2)
            value_b = round(float(score_b), 2)
            value_unit = "分"
            value_type = "dimension_mean" if target_dimension else "overall_mean"
            trend_up, trend_down = "提升", "下降"

        diff = round(value_b - value_a, 2)
        pct_change = round(diff / value_a * 100, 1) if value_a != 0 else None
        relative_change_status = "AVAILABLE" if value_a != 0 else "NOT_APPLICABLE_ZERO_BASELINE"
        trend = trend_up if diff > 0 else (trend_down if diff < 0 else "持平")
        diff_sign = f"+{diff}" if diff > 0 else str(diff)
        pct_text = (
            f"相对变化 {pct_change:+.1f}%"
            if pct_change is not None
            else "基期为 0，相对变化不适用"
        )
        filter_text = f"纳入【{school_filter}】后，" if school_filter else ""
        aggregation_text = (
            "全部有效作答池化平均"
            if aggregation == "answer_pooled_mean"
            else "学校等权平均"
        )
        metric_text = (
            "有效学校数量"
            if target_stat == "count"
            else (f"【{target_dimension}】维度均分" if target_dimension else "数字化综合均分")
        )
        scope_note = (
            "学校计数沿用同一筛选范围，均值聚合方式不改变计数"
            if target_stat == "count"
            else f"均值口径：{aggregation_text}"
        )
        direct_answer = (
            f"{reg['full_name']} 的两个年度重复截面按自然年份归一为 {y_start} 年基期、{y_end} 年末期；"
            f"{filter_text}{metric_text}从 **{value_a}{value_unit}** 变为 **{value_b}{value_unit}**，"
            f"{trend} {diff_sign}{value_unit}（{pct_text}）。"
            f"两期分别覆盖 {school_count_a}/{school_count_b} 所有效学校、"
            f"{answer_count_a}/{answer_count_b} 条有效作答；"
            f"这是年度重复截面变化（{scope_note}），不代表追踪同一批学校。"
        )

        def period_summary(evaluation: Dict[str, Any], period: str, value: Any) -> Dict[str, Any]:
            return {
                "region_name": evaluation.get("region_name"),
                "year": period,
                "target_value": value,
                "value_unit": value_unit,
                "overall_score": evaluation.get("total_score"),
                "target_dimension_score": evaluation.get("target_dimension_score"),
                "total_unfiltered_count": evaluation.get("total_unfiltered_count"),
                "school_count": (
                    evaluation.get("requested_school_count")
                    if target_dimension else evaluation.get("school_count")
                ),
                "answer_count": (
                    evaluation.get("requested_answer_count")
                    if target_dimension else evaluation.get("answer_count")
                ),
                "target_dimension": target_dimension,
                "target_stat": target_stat,
                "aggregation": aggregation,
                "school_filter": school_filter,
                "execution_scope": evaluation.get("execution_scope"),
            }

        execution_scope = {
            "query_template": "region_longitudinal_v1",
            "region": reg["full_name"],
            "period_a": y_start,
            "period_b": y_end,
            "dimension": target_dimension,
            "statistic": target_stat,
            "aggregation": aggregation,
            "school_filter": school_filter,
            "temporal_mode": "repeated_cross_section",
            "comparison_direction": "secondary_minus_primary",
        }
        return {
            "object_type": "region_longitudinal",
            "fact_data_status": "ANSWER",
            "region_name": reg["full_name"],
            "short_name": reg["name"],
            "region_type": reg["region_type"],
            "year_a": y_start,
            "year_b": y_end,
            "school_years": [y_start, y_end],
            "temporal_mode": "repeated_cross_section",
            "period_order_policy": "chronological_earlier_to_later",
            "comparison_direction": "secondary_minus_primary",
            "comparison_value_type": value_type,
            "comparison_value_a": value_a,
            "comparison_value_b": value_b,
            "target_value_a": value_a,
            "target_value_b": value_b,
            "value_unit": value_unit,
            "target_stat": target_stat,
            "target_dimension": target_dimension,
            "target_dimensions": target_dimensions,
            "aggregation": aggregation,
            "school_filter": school_filter,
            "total_score_a": value_a if target_stat == "mean" else None,
            "total_score_b": value_b if target_stat == "mean" else None,
            "overall_score_a": eval_a.get("total_score"),
            "overall_score_b": eval_b.get("total_score"),
            "target_dimension_score_a": eval_a.get("target_dimension_score"),
            "target_dimension_score_b": eval_b.get("target_dimension_score"),
            "diff": diff,
            "pct_change": pct_change,
            "relative_change_status": relative_change_status,
            "trend": trend,
            "total_unfiltered_count_a": eval_a.get("total_unfiltered_count"),
            "total_unfiltered_count_b": eval_b.get("total_unfiltered_count"),
            "school_count_a": school_count_a,
            "school_count_b": school_count_b,
            "answer_count_a": answer_count_a,
            "answer_count_b": answer_count_b,
            "requested_school_count_a": eval_a.get("requested_school_count"),
            "requested_school_count_b": eval_b.get("requested_school_count"),
            "requested_answer_count_a": eval_a.get("requested_answer_count"),
            "requested_answer_count_b": eval_b.get("requested_answer_count"),
            "dimension_deltas": [],
            "radar_data": {},
            "radar_comp_data": {},
            "period_a": period_summary(eval_a, y_start, value_a),
            "period_b": period_summary(eval_b, y_end, value_b),
            "top_gainer": None,
            "top_decliner": None,
            "execution_scope": execution_scope,
            "direct_answer": direct_answer,
            "conclusion": direct_answer,
            "data_coverage": 1.0,
            "overall_confidence": 1.0,
        }
    finally:
        if owns_connection:
            if conn.in_transaction:
                conn.rollback()
            conn.close()


def query_school_ranking(
    school_keyword_or_id: Any,
    region_keyword: Optional[str] = None,
    year: Optional[str] = None,
) -> Dict[str, Any]:
    """查询某所学校在所属区县或指定区域内的数字化综合排位、百分位及与区域均值对比。"""
    from scripts.school_engine import find_schools, get_school

    school = None
    if isinstance(school_keyword_or_id, int) or (
        isinstance(school_keyword_or_id, str) and school_keyword_or_id.isdigit()
    ):
        school = get_school(int(school_keyword_or_id))
    elif isinstance(school_keyword_or_id, str) and school_keyword_or_id.startswith("id:"):
        school = get_school(int(school_keyword_or_id.split(":", 1)[1]))
    else:
        hits = find_schools(str(school_keyword_or_id).strip(), year=year, limit=5)
        if not hits:
            return {"error": f"未检索到匹配的学校「{school_keyword_or_id}」" + (f"（年份 {year}）" if year else "")}
        school = hits[0]

    if not school:
        return {"error": "未找到有效学校档案"}

    school_id = school["id"]
    school_name = school["school_name"]
    active_year = str(school.get("year") or year or "2023")

    # 确定对比区域
    reg = None
    if region_keyword:
        reg = detect_region(region_keyword)
    if not reg:
        if school.get("district"):
            reg = detect_region(school["district"])
        elif school.get("city"):
            reg = detect_region(school["city"])

    if not reg:
        return {"error": f"学校「{school_name}」档案缺少所属区县或地市信息，无法计算区域排位"}

    where_sql = reg["where_sql"]
    where_params = reg["where_params"]
    s_where = f"({where_sql.replace('city', 's.city').replace('district', 's.district').replace('province', 's.province')}) AND s.year=?"
    full_where_params = where_params + (active_year,)

    conn = connect()

    # 1. 区域内所有学校得分排行榜
    school_scores_rows = conn.execute(
        f"""
        SELECT s.id, s.school_name, s.school_type,
               AVG(a.normalized_value)*100 as avg_score
        FROM schools s
        JOIN answers a ON s.id = a.school_id
        WHERE {s_where} AND a.normalized_value IS NOT NULL
        GROUP BY s.id
        ORDER BY avg_score DESC, s.id ASC
        """,
        full_where_params,
    ).fetchall()

    if not school_scores_rows:
        conn.close()
        return {"error": f"区域「{reg['name']}」在 {active_year} 年无有效作答记录"}

    total_schools = len(school_scores_rows)
    all_scores = [float(r["avg_score"]) for r in school_scores_rows if r["avg_score"] is not None]
    region_avg = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0

    target_rank = None
    target_score = None
    for idx, r in enumerate(school_scores_rows):
        if r["id"] == school_id:
            target_rank = idx + 1
            target_score = round(float(r["avg_score"]), 2)
            break

    if target_rank is None:
        conn.close()
        return {
            "error": f"学校「{school_name}」在 {active_year} 年尚未录入有效作答，无法在区域「{reg['name']}」中计算排位"
        }

    top_pct = round(target_rank / total_schools * 100, 1)
    beat_pct = round((total_schools - target_rank) / total_schools * 100, 1)
    diff_from_avg = round(target_score - region_avg, 2)

    # 2. 该校六维得分 vs 区域六维均分
    school_dim_rows = conn.execute(
        """
        SELECT q.level1_name, ROUND(AVG(a.normalized_value)*100, 2) as dim_score
        FROM answers a
        JOIN questions q ON a.question_id = q.id
        WHERE a.school_id = ?
        GROUP BY q.level1_name
        """,
        (school_id,),
    ).fetchall()
    school_dim_map = {r["level1_name"]: float(r["dim_score"]) for r in school_dim_rows}

    reg_dim_rows = conn.execute(
        f"""
        SELECT q.level1_name, ROUND(AVG(a.normalized_value)*100, 2) as dim_score
        FROM schools s
        JOIN answers a ON s.id = a.school_id
        JOIN questions q ON a.question_id = q.id
        WHERE {s_where}
        GROUP BY q.level1_name
        """,
        full_where_params,
    ).fetchall()
    reg_dim_map = {r["level1_name"]: float(r["dim_score"]) for r in reg_dim_rows}

    dim_comparison = []
    radar_school = {}
    radar_region = {}
    for d_name in STANDARD_DIMENSIONS:
        sc_s = school_dim_map.get(d_name, 0.0)
        sc_r = reg_dim_map.get(d_name, 0.0)
        radar_school[d_name] = sc_s
        radar_region[d_name] = sc_r
        d_diff = round(sc_s - sc_r, 2)
        dim_comparison.append({
            "dimension": d_name,
            "school_score": sc_s,
            "region_avg": sc_r,
            "diff": d_diff,
            "is_lead": d_diff >= 0,
        })

    # 3. 取目标校前后各 2 所临近学校展示梯度
    start_idx = max(0, target_rank - 3)
    end_idx = min(total_schools, target_rank + 2)
    peer_schools = [
        {
            "rank": i + 1,
            "id": school_scores_rows[i]["id"],
            "school_name": school_scores_rows[i]["school_name"],
            "school_type": school_scores_rows[i]["school_type"],
            "score": round(float(school_scores_rows[i]["avg_score"]), 2),
            "is_target": school_scores_rows[i]["id"] == school_id,
        }
        for i in range(start_idx, end_idx)
    ]

    conn.close()

    lead_str = f"高于全区均分 +{diff_from_avg} 分" if diff_from_avg >= 0 else f"低于全区均分 {diff_from_avg} 分"

    return {
        "object_type": "school_ranking",
        "school_id": school_id,
        "school_name": school_name,
        "school_years": [active_year],
        "year": active_year,
        "region_name": reg["full_name"],
        "short_name": reg["name"],
        "region_type": reg["region_type"],
        "rank": target_rank,
        "total_schools": total_schools,
        "top_pct": top_pct,
        "beat_pct": beat_pct,
        "percentile": top_pct,
        "total_score": target_score,
        "region_avg": region_avg,
        "diff": diff_from_avg,
        "direct_answer": (
            f"**{school_name}** 在 {reg['full_name']} {active_year} 年共 {total_schools} 所监测学校中综合排位为 **第 {target_rank} 名**"
            f"（位列全区前 **{top_pct}%**，超越全区 **{beat_pct}%** 的学校）。"
            f"该校综合得分 **{target_score} 分**，{lead_str}（全区均分为 {region_avg} 分）。"
        ),
        "dimensions_comparison": dim_comparison,
        "radar_data": radar_school,
        "radar_region_data": radar_region,
        "peer_schools": peer_schools,
        "data_coverage": 1.0,
        "overall_confidence": 1.0,
        "disclaimer": "本排名基于区域全量中小学在国家/省数字化问卷官方填报作答折算均分，供教育视导与学校定位参考。",
    }


def compare_regions(
    region_a_kw: str,
    region_b_kw: str,
    year: Optional[str] = None,
    target_stat: Optional[str] = None,
    target_dimension: Optional[str] = None,
    target_dimensions: Optional[List[str]] = None,
    school_filter: Optional[str] = None,
    aggregation: str = "school_equal_mean",
    original_query: Optional[str] = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """双区域数字化宏观比对核心引擎。

    输入两个行政区划名称（如 武汉市 vs 上海市，或 长沙县 vs 蔡甸区），
    可指定年份，返回区域级、指标级、领跑校与均分总分的全面对比结果。
    """
    evaluation_args = {
        "year": year,
        "target_dimension": target_dimension,
        "target_dimensions": target_dimensions,
        "target_stat": target_stat,
        "school_filter": school_filter,
        "aggregation": aggregation,
        "original_query": original_query,
        "connection": connection,
    }
    res_a = evaluate_region(region_a_kw, **evaluation_args)
    if "error" in res_a:
        return {"error": f"未检索到对比区域 A「{region_a_kw}」: {res_a['error']}"}

    res_b = evaluate_region(region_b_kw, **evaluation_args)
    if "error" in res_b:
        return {"error": f"未检索到对比区域 B「{region_b_kw}」: {res_b['error']}"}

    # 年份对齐：未指定年份时，优先选取两区域共同存在的最新年份
    actual_year = year
    if not actual_year:
        years_a = set(res_a.get("all_years", []))
        years_b = set(res_b.get("all_years", []))
        common_years = sorted(list(years_a & years_b), reverse=True)
        if common_years:
            actual_year = common_years[0]
            if res_a.get("school_years", [None])[0] != actual_year:
                res_a = evaluate_region(region_a_kw, **{**evaluation_args, "year": actual_year})
            if res_b.get("school_years", [None])[0] != actual_year:
                res_b = evaluate_region(region_b_kw, **{**evaluation_args, "year": actual_year})
        else:
            actual_year = res_a.get("school_years", [None])[0] or res_b.get("school_years", [None])[0] or "2021"

    score_field = "target_dimension_score" if target_dimension else "total_score"
    score_a = round(float(res_a.get(score_field) or 0.0), 2)
    score_b = round(float(res_b.get(score_field) or 0.0), 2)
    raw_field = "raw_target_dimension_score" if target_dimension else "raw_total_score"
    raw_a, raw_b = res_a.get(raw_field), res_b.get(raw_field)
    if raw_a is None or raw_b is None:
        return {"error": "region_compare_raw_mean_unavailable"}
    diff = round(float(raw_a) - float(raw_b), 2)

    name_a = res_a.get("short_name") or res_a.get("region_name") or str(region_a_kw)
    name_b = res_b.get("short_name") or res_b.get("region_name") or str(region_b_kw)

    if diff > 0:
        winner = "A"
        winner_name = name_a
    elif diff < 0:
        winner = "B"
        winner_name = name_b
    else:
        winner = "tie"
        winner_name = "持平"

    # 六大维度对比
    dims_a = {d["dimension"]: float(d.get("score") or 0.0) for d in res_a.get("dimensions", [])}
    dims_b = {d["dimension"]: float(d.get("score") or 0.0) for d in res_b.get("dimensions", [])}
    raw_dims_a = res_a.get("raw_dimension_scores") or {}
    raw_dims_b = res_b.get("raw_dimension_scores") or {}
    all_dims = ["数字资源", "教育教学", "数字素养", "基础设施", "教育治理", "保障机制"]
    dim_comparisons = []
    wins_a = 0
    wins_b = 0
    ties = 0

    for d in all_dims:
        sa = dims_a.get(d)
        sb = dims_b.get(d)
        if sa is not None and sb is not None:
            # Use the same executed precision as the primary delta. Displayed
            # means are presentation values and cannot be subtracted again.
            ra, rb = raw_dims_a.get(d), raw_dims_b.get(d)
            d_diff = diff if d == target_dimension else (
                round(float(ra) - float(rb), 2) if ra is not None and rb is not None else None)
            if d_diff is None:
                d_win, d_win_name = None, "数据未覆盖"
            elif d_diff > 0:
                d_win = "A"
                d_win_name = name_a
                wins_a += 1
            elif d_diff < 0:
                d_win = "B"
                d_win_name = name_b
                wins_b += 1
            else:
                d_win = "tie"
                d_win_name = "持平"
                ties += 1
        else:
            d_diff = None
            d_win = None
            d_win_name = "数据未覆盖"

        dim_comparisons.append({
            "dimension": d,
            "score_a": sa,
            "score_b": sb,
            "diff": d_diff,
            "winner": d_win,
            "winner_name": d_win_name,
            "lead_tag": d_win,
        })

    # 标杆学校对比
    top_a = res_a.get("top_schools", [{}])[0] if res_a.get("top_schools") else {}
    top_b = res_b.get("top_schools", [{}])[0] if res_b.get("top_schools") else {}

    cnt_a = (
        res_a.get("requested_school_count") if target_dimension else res_a.get("school_count")
    ) or 0
    cnt_b = (
        res_b.get("requested_school_count") if target_dimension else res_b.get("school_count")
    ) or 0
    answer_cnt_a = (
        res_a.get("requested_answer_count") if target_dimension else res_a.get("answer_count")
    ) or 0
    answer_cnt_b = (
        res_b.get("requested_answer_count") if target_dimension else res_b.get("answer_count")
    ) or 0

    # 生成客观严谨的结论句
    scope_label = f"纳入【{school_filter}】后，" if school_filter else ""
    aggregation_label = (
        "全部作答池化平均" if aggregation == "answer_pooled_mean" else "学校等权平均"
    )
    if target_stat == "count":
        diff_cnt = cnt_a - cnt_b
        if diff_cnt > 0:
            cnt_comp_str = f"【{name_a}】在册监测学校数量比【{name_b}】多 {abs(diff_cnt)} 所"
        elif diff_cnt < 0:
            cnt_comp_str = f"【{name_b}】在册监测学校数量比【{name_a}】多 {abs(diff_cnt)} 所"
        else:
            cnt_comp_str = "两地在册监测学校数量完全一致"
        conclusion = (
            f"在 {actual_year} 年数字化监测中，{scope_label}【{name_a}】共监测记录 **{cnt_a} 所** 中小学校，"
            f"【{name_b}】共监测记录 **{cnt_b} 所** 中小学校（{cnt_comp_str}）。"
            f"两地数字化综合均分分别为 {score_a} 分与 {score_b} 分。"
        )
    elif target_dimension:
        if winner == "tie":
            comparison_text = f"两地【{target_dimension}】维度均分持平，均为 {score_a} 分"
        else:
            lose_name = name_b if winner == "A" else name_a
            comparison_text = (
                f"【{winner_name}】的【{target_dimension}】维度均分 {max(score_a, score_b)} 分，"
                f"高于【{lose_name}】的 {min(score_a, score_b)} 分，分差为 +{abs(diff)} 分"
            )
        conclusion = (
            f"{actual_year} 年{scope_label}按{aggregation_label}口径比较，{comparison_text}。"
            f"【{name_a}】覆盖 {cnt_a} 所学校，【{name_b}】覆盖 {cnt_b} 所学校。"
        )
    elif winner == "tie":
        conclusion = (
            f"【{name_a}】与【{name_b}】在 {actual_year} 年数字化综合均分完全持平，均为 {score_a} 分。"
            f"其中【{name_a}】在 {wins_a} 个维度占优，【{name_b}】在 {wins_b} 个维度占优。"
            f"【{name_a}】共监测 {res_a.get('school_count', '-')} 所学校，【{name_b}】共监测 {res_b.get('school_count', '-')} 所学校。"
        )
    else:
        adv = abs(diff)
        win_score = score_a if winner == "A" else score_b
        lose_score = score_b if winner == "A" else score_a
        lose_name = name_b if winner == "A" else name_a
        conclusion = (
            f"【{winner_name}】（{win_score} 分）区域数字化综合水平高于【{lose_name}】（{lose_score} 分），"
            f"综合均分差为 +{adv} 分。在 6 个核心监测维度中，【{name_a}】在 {wins_a} 个维度占优，"
            f"【{name_b}】在 {wins_b} 个维度占优" + (f"，{ties} 个维度持平。" if ties else "。")
            + f"【{name_a}】共监测 {res_a.get('school_count', '-')} 所学校，【{name_b}】共监测 {res_b.get('school_count', '-')} 所学校。"
        )
    # 样本覆盖度与代表性核验（针对如 599 校 vs 3 校严重不均衡案例）
    sample_representation_note = None
    if min(cnt_a, cnt_b) < 10 or (min(cnt_a, cnt_b) > 0 and max(cnt_a, cnt_b) / min(cnt_a, cnt_b) >= 5):
        ratio_str = f"{max(cnt_a, cnt_b) / max(1, min(cnt_a, cnt_b)):.1f}:1"
        sample_representation_note = (
            f"【样本代表性与覆盖度提示】{name_a}（{cnt_a} 所）与 {name_b}（{cnt_b} 所）的在册监测样本量差距显著"
            f"（比例约为 {ratio_str}），低样本量区域的统计均分受个别学校填报数据影响显著，"
            f"本比对仅客观反映数据库当前在册样本表现，不可直接等同于两市整体宏观教育数字化优劣。"
        )
        conclusion += f"\n\n{sample_representation_note}"

    return {
        "object_type": "region_compare",
        "region_name_a": name_a,
        "region_name_b": name_b,
        "full_name_a": res_a.get("region_name") or name_a,
        "full_name_b": res_b.get("region_name") or name_b,
        "school_years": [actual_year],
        "year": actual_year,
        "sample_representation_note": sample_representation_note,
        "year_desc": f"{actual_year} 年",
        "total_score_a": score_a,
        "total_score_b": score_b,
        "total_diff": diff,
        "score_diff": diff,
        "diff": diff,
        "difference_basis": "raw_means_then_round",
        "comparison_direction": "primary_minus_secondary",
        "winner": winner,
        "winner_name": winner_name,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "ties": ties,
        "school_count_a": cnt_a,
        "school_count_b": cnt_b,
        "answer_count_a": answer_cnt_a,
        "answer_count_b": answer_cnt_b,
        "overall_school_count_a": res_a.get("school_count"),
        "overall_school_count_b": res_b.get("school_count"),
        "overall_answer_count_a": res_a.get("answer_count"),
        "overall_answer_count_b": res_b.get("answer_count"),
        "requested_school_count_a": res_a.get("requested_school_count"),
        "requested_school_count_b": res_b.get("requested_school_count"),
        "requested_answer_count_a": res_a.get("requested_answer_count"),
        "requested_answer_count_b": res_b.get("requested_answer_count"),
        "target_dimension": target_dimension,
        "target_dimensions": target_dimensions,
        "target_dimension_score_a": res_a.get("target_dimension_score"),
        "target_dimension_score_b": res_b.get("target_dimension_score"),
        "max_score_a": res_a.get("max_score"),
        "max_score_b": res_b.get("max_score"),
        "min_score_a": res_a.get("min_score"),
        "min_score_b": res_b.get("min_score"),
        "median_score_a": res_a.get("median_score"),
        "median_score_b": res_b.get("median_score"),
        "top_school_a": top_a,
        "top_school_b": top_b,
        "dimensions_comparison": dim_comparisons,
        "radar_data_a": res_a.get("radar_data", {}),
        "radar_data_b": res_b.get("radar_data", {}),
        "region_a_detail": res_a,
        "region_b_detail": res_b,
        "target_stat": target_stat,
        "school_filter": school_filter,
        "aggregation": aggregation,
        "execution_scope": {
            "query_template": "region_compare_v1",
            "region_a": res_a.get("region_name") or name_a,
            "region_b": res_b.get("region_name") or name_b,
            "period": str(actual_year),
            "dimension": target_dimension,
            "statistic": target_stat,
            "aggregation": aggregation,
            "school_filter": school_filter,
        },
        "direct_answer": conclusion,
        "conclusion": conclusion,
        "data_coverage": 1.0,
        "overall_confidence": 1.0,
        "disclaimer": "本报告基于两地全量中小学在国家/省数字化监测评估中的官方填报作答折算均分，供宏观教育统筹与区域视导参考。",
    }


def load_national_province_summary(year=None) -> Dict[str, Any]:
    """加载全国各省份预计算宏观大盘底座（支持 frozen PyInstaller 环境与源码模式）。"""
    from packages.core.item_collection import enabled as r11_enabled
    if r11_enabled():
        # The historical disk cache has no R11 item policy. Reuse the same
        # region tool over the current connection instead of reading its scores.
        from packages.core.research_data import config
        from packages.semantic_query.typed_plan import region_identity
        available = config()['application_years']
        selected = str(year) if str(year) in available else available[-1]
        provinces = {}
        with connect() as connection:
            names = [r[0] for r in connection.execute('SELECT DISTINCT province FROM schools WHERE year=? AND province IS NOT NULL', (selected,))]
            for name in names:
                identity = region_identity(dict(region_type='province', province=name))
                result = evaluate_region(name, year=selected, connection=connection, region_identity=identity)
                if result.get('total_score') is None:
                    continue
                count = result['school_count']
                provinces[name] = dict(province=name, year=selected, school_count=count,
                    avg_score=result['total_score'], min_score=result.get('min_score'), max_score=result.get('max_score'),
                    sample_tier='规模化样本' if count >= 1000 else '常态化样本' if count >= 100 else '小微样本',
                    tier_desc='', dimensions={d['dimension']:d['score'] for d in result.get('dimensions', [])})
        return dict(metadata=dict(available_years=available), by_year={selected:dict(provinces=provinces)})
    import json
    candidates = []
    try:
        from packages.core.frozen_paths import resource_root
        candidates.append(os.path.join(resource_root(), "data", "real", "province_summary.json"))
    except Exception:
        pass
    candidates.append(os.path.join(ROOT, "data", "real", "province_summary.json"))

    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return {}


def evaluate_national_overall_top(year: str, *, province_only: bool = False) -> Dict[str, Any]:
    """Recompute one year's provincial-level leader from the read-only source DB."""
    from scripts.school_engine import connect

    year_text = str(year or "").strip()
    if not re.fullmatch(r"20\d{2}", year_text):
        return {"error": "请指定一个明确的公历年份"}
    query = """
        WITH school_means AS (
            SELECT s.id, s.province, AVG(a.normalized_value) * 100.0 AS score
            FROM schools AS s
            JOIN answers AS a ON a.school_id = s.id
            WHERE s.year = ? AND a.normalized_value IS NOT NULL
            GROUP BY s.id
        )
        SELECT province, COUNT(*) AS school_count, AVG(score) AS score
        FROM school_means
        WHERE province IS NOT NULL AND TRIM(province) <> ''
        GROUP BY province
    """
    with connect() as connection:
        rows = connection.execute(query, (int(year_text),)).fetchall()
    valid_suffixes = ("省", "市", "自治区", "特别行政区")
    candidates = [
        {
            "province": str(row["province"]),
            "year": year_text,
            "score": round(float(row["score"]), 2),
            "unrounded_score": float(row["score"]),
            "school_count": int(row["school_count"]),
        }
        for row in rows
        if row["score"] is not None
        and str(row["province"]).endswith(("省",) if province_only else valid_suffixes)
    ]
    candidates.sort(key=lambda item: (-item["unrounded_score"], item["province"]))
    if not candidates:
        return {"error": f"{year_text}年没有可用的省级学校数字化得分"}
    for rank, item in enumerate(candidates, 1):
        item["rank"] = rank
        item["sample_tier"] = (
            "规模化样本" if item["school_count"] >= 1000
            else "常态化样本" if item["school_count"] >= 100 else "小微样本"
        )
    winner = candidates[0]
    scope = "行政省" if province_only else "省级地区（含直辖市）"
    direct_answer = (
        f"按库内有效学校的各校归一得分均值再作学校等权平均，"
        f"{year_text}年{scope}中最高的是{winner['province']}，"
        f"综合均分{winner['score']:.2f}分，有效学校{winner['school_count']}所。"
    )
    return {
        "object_type": "national_ranking",
        "year": year_text,
        "target_dimension": None,
        "province_only": province_only,
        "total_provinces": len(candidates),
        "rankings": candidates,
        "overall_champion": winner,
        "direct_answer": direct_answer,
        "conclusion": direct_answer,
        "data_source": "read_only_sqlite",
        "disclaimer": "仅比较数据库内该年有有效问卷得分的学校，不代表未登记学校或真实行政区全量。",
    }


def evaluate_national_ranking(
    year: Optional[str] = None,
    target_dimension: Optional[str] = None,
    top_n: Optional[int] = None,
    school_filter: Optional[str] = None,
    original_query: Optional[str] = None,
) -> Dict[str, Any]:
    """全国各省份教育数字化水平综合与细分维度横向排名测算。

    支持：
    1. 指定学年全国大排名（默认自动选取最新可用年份）；
    2. 支持全维度综合均分或六大细分维度定向排名（如 数字素养最高省份）；
    3. 样本规模分层标定（规模化样本 >= 1000 所、常态化样本 100~999 所、小微样本 < 100 所）；
    4. 自动生成用于可视化柱状图与质检 AUDIT 的结构化证据链。
    """
    summary_data = load_national_province_summary(year)
    by_year = summary_data.get("by_year", {})
    available_years = summary_data.get("metadata", {}).get("available_years", ["2020", "2021", "2022", "2023"])

    actual_year = str(year).strip() if year and str(year).strip() in by_year else (
        available_years[-1] if available_years else "2023"
    )

    year_data = by_year.get(actual_year, {})
    provinces_dict = year_data.get("provinces", {})

    if not provinces_dict:
        return {
            "error": f"暂无 {actual_year} 年全国各省份教育数字化监测汇总数据",
            "available_years": available_years,
            "requested_year": year,
        }

    # 确定排序依据得分
    rank_items = []
    for p_name, p_info in provinces_dict.items():
        if target_dimension:
            score = p_info.get('dimensions', {}).get(target_dimension)
        else:
            score = p_info.get("avg_score", 0.0)
        if score is None:
            continue

        rank_items.append({
            "province": p_name,
            "year": actual_year,
            "score": round(score, 2),
            "avg_score": p_info.get("avg_score", 0.0),
            "school_count": p_info.get("school_count", 0),
            "sample_tier": p_info.get("sample_tier", "常态化样本"),
            "tier_desc": p_info.get("tier_desc", ""),
            "min_score": p_info.get("min_score", 0.0),
            "max_score": p_info.get("max_score", 0.0),
            "dimensions": p_info.get("dimensions", {}),
        })

    if not rank_items:
        return dict(error='该年度所问维度没有有效的已采集题项得分。', year=actual_year, status='unavailable_data')
    # 降序排序
    rank_items.sort(key=lambda x: x["score"], reverse=True)
    for idx, item in enumerate(rank_items, 1):
        item["rank"] = idx

    # 分组统计
    large_scale = [it for it in rank_items if it["school_count"] >= 1000]
    for idx, it in enumerate(large_scale, 1):
        it["rank_tier"] = idx

    normal_scale = [it for it in rank_items if 100 <= it["school_count"] < 1000]
    for idx, it in enumerate(normal_scale, 1):
        it["rank_tier"] = idx

    small_scale = [it for it in rank_items if it["school_count"] < 100]
    for idx, it in enumerate(small_scale, 1):
        it["rank_tier"] = idx

    overall_champion = rank_items[0] if rank_items else None
    large_champion = large_scale[0] if large_scale else overall_champion
    normal_champion = normal_scale[0] if normal_scale else None

    dim_prefix = f"在【{target_dimension}】维度上，" if target_dimension else ""
    score_label = f"【{target_dimension}】得分" if target_dimension else "综合均分"

    if overall_champion and overall_champion["school_count"] < 100 and large_champion and large_champion != overall_champion:
        direct_answer = (
            f"在 {actual_year} 年全国教育数字化监测中，{dim_prefix}"
            f"具有统计代表性的**规模化监测省份（在册学校 ≥ 1000 所）中，"
            f"【{large_champion['province']}】位列全国第一**（{score_label} **{large_champion['score']} 分**，在册监测 {large_champion['school_count']} 所学校）；"
            f"【{large_scale[1]['province']}】以 **{large_scale[1]['score']} 分**（{large_scale[1]['school_count']} 所学校）位列第二。"
            f"常态化监测省份（100~999 所）中，【{normal_champion['province']}】（**{normal_champion['score']} 分**）表现最优。\n\n"
            f"【学术统计说明】在全部 {len(rank_items)} 个在册省份全样本算术排序中，"
            f"【{overall_champion['province']}】（{overall_champion['score']} 分）名义分值最高，"
            f"但其在册填报样本仅 {overall_champion['school_count']} 所，受极少数单校作答偶然性影响显著，不宜直接作为省域宏观对比评价依据。"
        )
    else:
        direct_answer = (
            f"在 {actual_year} 年全国教育数字化监测中，{dim_prefix}"
            f"**【{large_champion['province']}】综合发展水平位列全国第一**（{score_label} **{large_champion['score']} 分**，全省在册监测覆盖 {large_champion['school_count']} 所学校），"
            f"在全国规模化省份中展现出显著的发展引领优势。"
        )

    # 准备图表数据（展示 Top 15 省份）
    chart_top = rank_items[:15]
    charts_data = {
        "title": f"{actual_year} 年全国各省教育数字化水平排行榜 Top 15" if not target_dimension else f"{actual_year} 年全国各省【{target_dimension}】得分排行榜 Top 15",
        "provinces": [it["province"] for it in chart_top],
        "scores": [it["score"] for it in chart_top],
        "school_counts": [it["school_count"] for it in chart_top],
        "tiers": [it["sample_tier"] for it in chart_top],
    }

    n_limit = top_n if top_n and top_n > 0 else len(rank_items)
    display_rankings = rank_items[:n_limit]

    return {
        "object_type": "national_ranking",
        "year": actual_year,
        "target_dimension": target_dimension,
        "school_filter": school_filter,
        "total_provinces": len(rank_items),
        "rankings": display_rankings,
        "top_provinces": rank_items[:10],
        "large_scale_rankings": large_scale[:10],
        "normal_scale_rankings": normal_scale[:10],
        "small_scale_rankings": small_scale,
        "overall_champion": overall_champion,
        "large_champion": large_champion,
        "normal_champion": normal_champion,
        "direct_answer": direct_answer,
        "conclusion": direct_answer,
        "charts_data": charts_data,
        "disclaimer": "本榜单基于各省在册中小学在全国教育数字化监测平台的官方填报作答折算均分，分层展示不同样本规模下的实际表现，供宏观政策研判参考。",
        "overall_confidence": 1.0,
        "data_coverage": 1.0,
    }
