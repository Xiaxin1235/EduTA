"""Compile regional capabilities into existing, typed, read-only region tools."""
from __future__ import annotations

import copy
import re
from contextlib import closing

PROVINCES = (
    '北京市', '天津市', '河北省', '山西省', '内蒙古自治区', '辽宁省', '吉林省',
    '黑龙江省', '上海市', '江苏省', '浙江省', '安徽省', '福建省', '江西省',
    '山东省', '河南省', '湖北省', '湖南省', '广东省', '广西壮族自治区', '海南省',
    '重庆市', '四川省', '贵州省', '云南省', '西藏自治区', '陕西省', '甘肃省',
    '青海省', '宁夏回族自治区', '新疆维吾尔自治区',
)
from packages.semantic_query.task_draft import REGIONAL_INTENTS as INTENTS, CAPABILITY_REGISTRY, COMPOSITION
from packages.core.school_filters import filter_notes
from packages.semantic_query.request_semantics import inference_text,open_profile,score_comparison as is_score_comparison
INFERENCE = re.compile(r'为什么|为何|原因|因果|归因|造成|导致|预测|预估|未来|明年|显著|统计检验|假设检验|p\s*值|置信区间', re.I)
INFERENCE_NOTE = '这些数据只能提供描述性结果，不能回答原因、作因果推断、预测未来或进行显著性检验。'
TEMPORAL_NOTE = '各年是独立的年度截面，学校不是同一批，不能据此说同一批学校进步了。'
SMALL_NOTE = '有效学校少于30所，样本过少，不能代表该地区整体。'
SCOPE_NOTE = '全国排名范围为31个省级行政区，不含新疆生产建设兵团和港澳台；兵团单列。'
CATALOGUE_AVAILABILITY_PROMPT = (
    '先对照完整83题及维度目录核对所问测量对象、属性、单位和时间窗口。主题相似但测量不同不能替代。'
    '请求含义明确而目录没有可对应的题项、学校字段、复合指标或维度时，使用intent=clarify、'
    'clarify_reason=catalogue_measurement_unavailable，指标字段均留空、missing=[]；程序直接说明无此数据和可查范围，不反问口径。'
    '指标本身多义或缺对象才按原澄清规则。目录已登记的原始值、比例、周课时及常态应用不因含时间、教师或学生词而判缺失。\n'
)
PROMPT = (
    '\n新增区域能力（下列规定替代旧规则中对应的数量/全国榜首/缺年份限制）：'
    '省级行政区含省、直辖市、自治区；新疆指新疆维吾尔自治区，兵团单列。'
    'region_profile用于地区“怎么样/如何/好不好/处于什么水平”，程序同时计算整体分、有效学校数、全国平均及六维最高最低，省级另给全国排名。'
    'national_eval用于全国学校等权均值；province_ranking用于全国31省级行政区前N/后N，target_top_n填N，ranking_order填top或bottom。'
    'region_position用于省在全国的位置或城市相对本省均值及省内城市排名，benchmark填national或province。'
    'region_multi_compare保留3个及以上省份/城市；region_series保留同一地区或学校3至4年；regions和periods不得截断。'
    '年度截面不是固定同校队列。无年份且上下文无年份时periods填数据最新年2023；有上下文则继承；“去年”在无上下文时也按数据最新年2023处理并写明；其余无法确定的相对年份须澄清。'
    '未指定范围的这几年/近几年/这四年/越来越等多年问法使用全部可用年度2020至2023并写明。'
    '2020至2022学段按登记的年级范围判定，2023按学段类别。城区只含城区，镇区只含镇区，城镇含城区和镇区，乡村只含乡村。'
    '单校六维概况使用school_eval、dimension=null，程序返回六维学校模板分及最高最低维度。地区筛选可组合整体/单维/单题和均值/中位数/有效学校数。'
    '数据外年份、港澳台、学生成绩、经费金额、满意度、个人数据应保留请求对象，程序按缺失类型说明没有数据，不反问指标。'
    '吉林须问省或市，同名区县须问城市，无上下文代词须问所指。因果/预测/显著性/个人层面请求先返回分类UNAVAILABLE，不先就次要地名或指标反问。\n'
)


def aliases():
    return {re.sub(r'(?:维吾尔自治区|壮族自治区|回族自治区|自治区|省|市)$', '', p): p
            for p in PROVINCES}


def clarification(question, reason, message):
    return dict(intent='clarify', original=question, reason=reason, missing=[message])


def unavailable(question, category, message):
    return dict(intent='unavailable', original=question, reason='unavailable_data',
                data_category=category, message=message, business_status='UNAVAILABLE')


def catalogue_missing_boundary(question, candidate):
    """Decode the existing parser's catalogue decision, not a keyword detector."""
    if candidate.get('intent')!='clarify' or candidate.get('clarify_reason')!='catalogue_measurement_unavailable':return None
    if any(candidate.get(k) for k in ('dimension','topics','question_ids','compound_key','school_field')):return None
    from packages.semantic_query.catalogue import extract_explicit_question_ids,load_semantic_catalogue
    if extract_explicit_question_ids(question):return None
    catalogue=load_semantic_catalogue()
    result=unavailable(question,'catalogue_measurement',
        '现有目录没有登记你所问的这类测量数据，不能用相近主题的得分代替。'
        '可查学校年度数字化整体、一级和二级维度得分、已登记题项原始值，以及教师数、学生数等学校登记字段。')
    result['catalogue_check']=dict(source='same_semantic_parser',decision='NO_MATCH_REPORTED',
        catalogue_version=catalogue['version'],item_count=catalogue['item_count'])
    return result


def multi_year_request(question):
    return bool(re.search(r'这几年|近几年|过去几年|这[三四34]年|近[三四34]年|最近[三四34]年|历年|多年|逐年|跨年|年际|趋势|越来越', question))


def years(question, context):
    found = list(dict.fromkeys(re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', question)))
    span = re.search(r'((?:19|20)\d{2})\s*年?\s*(?:到|至|[-—–~～])\s*((?:19|20)\d{2})', question)
    if span and int(span[1]) <= int(span[2]) and int(span[2])-int(span[1]) < 10:
        found = [str(y) for y in range(int(span[1]), int(span[2])+1)]
    if re.search(r'预测|预估|未来|明年', question):
        from packages.core.research_data import registry
        available = registry()['application_years']
        if not found or any(y not in available for y in found):
            return [y for y in found if y in available] or list(available), False
    if found:
        if re.search(r'(?:19|20)\d{2}\s*年?\s*(?:还是|或者|或|/)\s*(?:19|20)\d{2}', question):
            return [], False
        return found, False
    if multi_year_request(question):
        from packages.core.research_data import registry
        available = registry()['application_years']
        return list(available), False
    if '去年' in question and not context.get('last_year'):
        return ['2023'], True
    if re.search(r'今年|去年|前年|\d{2}年|几几年|某年|最新|最近一年', question):
        if re.search(r'最新|最近一年', question):
            return ['2023'], False
        return [], False
    inherited = str(context.get('last_year') or '')
    return [inherited if re.fullmatch(r'20\d{2}', inherited) else '2023'], not bool(inherited)


def unavailable_measure(question):
    """Separate missing measurements from inferences over available scores."""
    from packages.semantic_query.request_semantics import money_amount_request
    question = inference_text(question)
    checks = [
        ('personal', r'(?:教师|老师|校长|学生|个人).{0,24}(?:个人|每人|每位|个体|个人层面)|(?:每|各|哪|某)(?:一)?(?:位|名|个)(?:教师|老师|校长|学生)|个人(?:得分|表现|评价|数据)',
         '没有教师、校长或学生的个人层面数据；本系统的观测单位是学校年度登记。'),
        ('student_outcome', r'学生.{0,8}成绩|学生.{0,12}(?:考试|测验|学业).{0,8}(?:分|成绩)|升学率', '没有学生成绩或升学率数据；数字化评价得分不能代替学生学习成绩。'),
        ('funding', None,
         '没有经费金额数据；保障机制得分不是经费金额。'),
        ('satisfaction', r'满意度|满意不满意|满意吗|(?:教师|老师|校长|学生|家长)(?:对.{0,12}|的)(?:主观)?(?:评价|看法|感受)|对.{0,12}(?:评价如何|怎么评价)',
         '没有个人满意度、主观评价或意见调查数据。'),
        ('duration', r'学生.{0,8}(?:学习|上网).{0,5}(?:时长|时间|多久|小时|分钟)|使用.{0,5}(?:时长|时间|多久|小时|分钟)|(?:每天|每周).{0,12}(?:用多久|用多长|小时|分钟|课时|节课)',
         '没有使用时长数据；现有登记得分不能换算为使用小时数或分钟数。'),
    ]
    for category, pattern, message in checks:
        if (money_amount_request(question) if category=='funding' else re.search(pattern, question)):
            if category=='duration':
                from packages.semantic_query import runtime as rt
                ids=rt._scope_question_ids(question) or [i for i,_ in rt._catalogue_alias_items(question)]
                if any(rt._catalogue()[1].get(i,{}).get('unit','').startswith('课时') for i in ids):continue
            result=unavailable(question, category, message)
            if category in {'personal','duration'}:
                result['message']+='可查学校年度的数字化整体、一级和二级维度得分，以及已登记题项的原始值。'
            result['request_type']=dict(category=category,measurement_available=False,source='request_subject_and_measurement')
            return result
    return None


def type_unavailable(question):
    """Request type precedes geography ambiguity, for both correction arms."""
    from packages.semantic_query.runtime import _scope_assertion_text
    text = inference_text(question)
    missing = unavailable_measure(text)
    if missing:
        return missing
    for category, pattern, message in (
        ('significance', r'显著|统计检验|假设检验|p\s*值|置信区间', '无法进行显著性检验或提供推断统计；现有工具只计算描述性统计。'),
        ('prediction', r'预测|预估|未来|明年', '无法预测未来结果；现有数据仅为2020–2023年的年度登记。'),
        ('causality', r'为什么|为何|原因|因果|归因|造成|导致', '无法从年度登记数据解释原因或作因果推断；可以查询描述性得分与差异。'),
    ):
        if re.search(pattern, text, re.I):
            return unavailable(question, category, message)
    explicit_years = re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', text)
    if any(y not in {'2020','2021','2022','2023'} for y in explicit_years):
        return unavailable(question, 'year', '没有所请求年份的数据；数据仅覆盖2020–2023年。')
    if re.search(r'香港|澳门|台湾|港澳台', text):
        return unavailable(question, 'geography', '没有港澳台的评价数据；全国范围为31个省级行政区，兵团单列。')
    return None


def boundary(question, context=None, *, check_reference=True):
    """Known data availability and unresolved time/reference, shared by all intents."""
    context = context or {}
    missing = type_unavailable(question)
    if missing:
        return missing
    from packages.core.item_collection import boundary as collection_boundary
    collection_guard = collection_boundary(question, context=context)
    if collection_guard:
        return collection_guard
    missing = unavailable_measure(question)
    if missing:
        return missing
    ys, _ = years(question, context)
    if any(y not in {'2020', '2021', '2022', '2023'} for y in ys):
        return unavailable(question, 'year', '没有所请求年份的数据；数据仅覆盖2020–2023年。')
    if not ys:
        return clarification(question, 'ambiguous_year', '您具体指哪一年？可查2020–2023年。')
    if re.search(r'香港|澳门|台湾|港澳台', question):
        return unavailable(question, 'geography', '没有港澳台的评价数据；全国范围为31个省级行政区，兵团单列。')
    return reference_boundary(question,context) if check_reference else None


def reference_boundary(question,context=None):
    from packages.semantic_query.request_semantics import request_text
    context=context or {}
    text=request_text(question)
    if re.search(r'那个城市|这个城市|该市',text) and not context.get('last_region'):
        return clarification(question,'missing_city_referent','您指的是哪个城市？')
    if not (context.get('last_region') or context.get('last_school')) and re.search(
        r'(?<!其)它|(?:这个|那个|该)(?:省|城市|市|地区|学校|地方)|该地|(?:这个|那个)(?=和|与|跟|怎么样|如何|排|比(?!例))',text):
        return clarification(question,'scope_reference_not_unique','“它/这个/那个/该地”具体指哪个地区或学校？')
    return None


def region_literals(question, rt, active_year=None):
    """Use existing lexical paths, adding complete province aliases outside school names."""
    from packages.core.research_data import enabled
    from packages.core.geography_registry import is_full, region_literals as full_literals
    if enabled() and is_full():
        return full_literals(question, active_year)
    text = rt._scope_assertion_text(question)
    for school in rt._literal_school_name_spans(text):
        text = text.replace(school, ' ' * len(school))
    matches = []
    from packages.core.research_data import registry
    for alias, city in registry()['city_aliases'].items():
        for match in re.finditer(re.escape(alias), text):
            matches.append((match.start(), match.end(), city, match.group()))
    for short, full in aliases().items():
        for match in re.finditer(re.escape(full) + '|' + re.escape(short), text):
            end = match.end()
            # A province prefix of a city/district compound is its ancestor.
            matches.append((match.start(), end, full, match.group()))
    raw = rt._literal_region_spans(text)
    for literal in raw:
        if re.search(r'所在省|本省|全省|这个省|全国|省内|省份|省级|该省', literal):
            continue
        literal = re.sub(r'^(?:和|与|及|以及|对比|比较|在|比)+', '', literal)
        if literal.endswith('省') and literal not in PROVINCES:
            continue
        pos = text.find(literal)
        if pos >= 0:
            matches.append((pos, pos+len(literal), literal, literal))
    matches.sort(key=lambda m: (m[0], -(m[1]-m[0])))
    selected = []
    for start, end, canonical, literal in matches:
        if any(a <= start and b >= end for a, b, _, _ in selected):
            continue
        selected.append((start, end, canonical, literal))
    return list(dict.fromkeys(c for _, _, c, _ in selected))


def bind_region(query, connection, question=''):
    """Exact identities for new tasks, including empty provinces and tiny cities."""
    from packages.semantic_query.typed_plan import region_identity
    entity = query['entity']
    if query.get('city_group'):
        parts=entity.split()
        return region_identity(dict(region_type='city',province=parts[0],city=parts[-1],city_group=True))
    if entity == '新疆生产建设兵团':
        return region_identity(dict(region_type='corps',province=entity))
    if entity == '全国':
        return region_identity(dict(region_type='national'))
    from packages.core.research_data import enabled
    from packages.core.geography_registry import is_full, resolve
    if enabled() and is_full():
        reg = resolve(entity, question, active_year=query.get('year'), prefer_province=entity in PROVINCES)
        return region_identity(reg)
    if entity in PROVINCES:
        return region_identity(dict(region_type='province', province=entity))
    parts = entity.split()
    name = parts[-1]
    field = 'city' if name.endswith(('市', '州', '盟', '地区')) else 'district'
    suffix = '' if len(parts) == 1 else ' AND province=?'
    rows = connection.execute('SELECT DISTINCT province,city,district FROM schools WHERE '+field+'=?'+suffix,
                              (name,) if len(parts) == 1 else (name, parts[0])).fetchall()
    identities = {tuple(row[k] for k in ('province', 'city')) + ((row['district'],) if field == 'district' else ()) for row in rows}
    if len(identities) != 1:
        from scripts.region_engine import detect_region
        existing = detect_region(entity, connection=connection, _metadata_only=True)
        if existing:
            return region_identity(existing)
        raise ValueError('capability_region_ambiguous_or_unavailable:'+entity)
    values = next(iter(identities))
    return region_identity(dict(region_type=field, province=values[0], city=values[1],
                                district=values[2] if field == 'district' else None))


def route(question, candidate, context, rt):
    """New tasks compile to public_plan; legacy supported tasks keep their old route."""
    from packages.experiment_protocol.mode import requirement_preservation_enabled
    preserve = requirement_preservation_enabled()
    canonical=CAPABILITY_REGISTRY.get(candidate.get('intent'),{}).get('canonical_intent')
    if canonical:
        candidate={**candidate,'intent':canonical,'target_top_n':candidate.get('target_top_n') or 1}
    missing = type_unavailable(question)
    if missing:
        return missing
    from packages.core.research_data import enabled
    from packages.core.geography_registry import is_full, ambiguity_boundary
    full_registry = enabled() and is_full()
    context_city = False
    if full_registry:
        from packages.core.geography_registry import groups
        from packages.core.research_data import registry
        context_city = any((preserve or m['token'] in (candidate.get('regions') or [])) and
            m['candidates'] and all(c['level']=='city' for c in m['candidates']) and
            any(c['level']=='district' for c in registry()['name_candidates'].get(m['token'], []))
            for batch in groups(question) for m in batch)
    from packages.core.item_collection import boundary as collection_boundary
    collection_guard = collection_boundary(question, candidate, context)
    if collection_guard:
        return collection_guard
    open_question = open_profile(question) and not candidate.get('dimension') and not candidate.get('question_ids')
    score_comparison = is_score_comparison(question)
    if not preserve and candidate.get('intent') not in INTENTS and not open_question and not score_comparison and not context_city and '新疆生产建设兵团' not in (candidate.get('regions') or []):
        return None
    if not preserve and candidate.get('intent') == 'region_series' and candidate.get('schools'):
        from packages.semantic_query.typed_plan import bind_types
        schools = candidate['schools']
        periods = candidate.get('periods') or []
        if len(schools) != 1 or not 3 <= len(periods) <= 4:
            return clarification(question, 'capability_scope_required', '请指定一所学校和三至四个年份。')
        queries = [dict(id='v'+str(i+1), kind='school_score', entity=schools[0], year=str(y),
                        dimension=candidate.get('dimension'), question_id=None, view='normalized_score',
                        statistic='template_weighted_score', aggregation='template_weighted_dimension_mean', school_filter=None)
                   for i, y in enumerate(periods)]
        plan = bind_types(dict(queries=queries, steps=[], selection=None, chart_requested='charts' in (candidate.get('requested_outputs') or []), temporal_note=TEMPORAL_NOTE), {})[0]
        return dict(intent='public_task', public_plan=plan, original=question, year=str(periods[0]),
                    value_view='normalized_score', requested_outputs=list(candidate.get('requested_outputs') or ['direct_answer']))
    if preserve:
        guard = boundary(question, context)
        if guard:
            return guard
        # A rule-only route has no model TaskDraft to overwrite; retain its
        # existing lexical delegation. Model correction requires registration.
        school_spans=rt._literal_school_name_spans(question) if candidate else rt._lexical_school_name_spans(question)
        if school_spans or candidate.get('schools') and str(candidate.get('intent') or '').startswith('school_'):
            return None
        if full_registry:
            effective_years, _ = years(question, context)
            guard = ambiguity_boundary(question, effective_years[0] if effective_years else None)
            if guard:
                guard['_selective_task_candidate'] = dict(candidate)
                return guard
    ys, defaulted = years(question, context)
    regions = region_literals(question, rt, ys[0] if ys else None) if preserve else list(candidate.get('regions') or [])
    from packages.semantic_query.request_semantics import province_position,bound_item_ids,school_count_requested
    provincial_position=province_position(question,candidate)
    if full_registry and (context_city or provincial_position):
        from packages.core.geography_registry import resolve
        regions=[resolve(r,question,ys[0])['full_name'] for r in regions]
    # A city versus its explicitly named parent is one position task.
    explicit_parent = None
    if preserve and len(regions) == 2 and (provincial_position or re.search(r'平均|均分|高|低|相对|位置|省内|全省|排第|排名|第几', question)):
        from scripts.school_engine import connect
        from packages.semantic_query.typed_plan import region_sql
        with closing(connect()) as connection:
            identities = [region_sql(bind_region(dict(entity=r, year=ys[0]), connection, question)) for r in regions]
        city = next((r for r in identities if r.get('city') and not r.get('district')), None)
        province = next((r for r in identities if r.get('province') and not r.get('city')), None)
        if city and province and city['province'] == province['province']:
            regions = [city['full_name']]
            explicit_parent = province['province']
    if preserve and not regions:
        regions = [r for r in candidate.get('regions') or [] if isinstance(r, str) and
                   not (r.endswith('省') and r not in PROVINCES) and
                   (r in question or re.sub(r'(?:市|县|区)$', '', r) in question)]
    if not preserve:
        ys = list(candidate.get('periods') or ys)
        defaulted = defaulted and ys == ['2023']
    if not regions and re.search(r'这个省|该省|这个城市|该市|这个地区|它', question) and context.get('last_region'):
        regions = [context['last_region']]
    if preserve and not full_registry and '吉林省' in regions and re.search(r'吉林(?!省|市)', question):
        return clarification(question, 'ambiguous_region_level', '您指吉林省还是吉林市？')
    schools = rt._literal_school_name_spans(question)
    city_list = len(regions) == 1 and regions[0] in PROVINCES and bool(re.search(
        r'(?:各|所有|全省|省内).{0,4}城市|城市(?:排名|列表|名单)|有(?:哪些|多少)个?城市', question))
    if preserve and schools:
        return None
    if preserve and not city_list and re.search(r'哪(?:一)?所学校|哪个学校|学校.{0,8}(?:排名|第[一二三四五六七八九十\d]+名)', question):
        return None  # School rankings retain their existing tool and operands.
    if preserve and not full_registry and len(regions) == 1 and regions[0].endswith(('区', '县', '旗')) and regions[0] not in PROVINCES:
        from scripts.school_engine import connect
        with closing(connect()) as conn:
            rows = conn.execute('SELECT DISTINCT province,city FROM schools WHERE district=? AND year=?', (regions[0], ys[0])).fetchall()
        if len(rows) > 1:
            return clarification(question, 'ambiguous_district_city', regions[0]+'属于哪个城市？库中有：'+'、'.join(r['province']+' '+r['city'] for r in rows)+'。')
    score_comparison = score_comparison and len(regions) == 2 and len(ys) == 1
    profile = (open_profile(question) or candidate.get('intent') == 'region_profile') and not provincial_position
    position = bool(re.search(r'全国.{0,10}(?:排名|第几|位置)|(?:在|全)省内|省内|所在省|本省|全国平均|省平均|全省平均', question))
    position = position or bool(regions and regions[0] in PROVINCES and re.search(r'排名|排第|名次', question))
    position = position or bool(explicit_parent) or candidate.get('intent') == 'region_position'
    national = bool(re.search(r'全国|各省|省份|省级行政区|省级地区', question))
    if not preserve:
        national = candidate.get('intent') in {'national_eval', 'province_ranking'} or candidate.get('benchmark') == 'national'
    province_list = national and bool(re.search(r'(?:前|后|倒数|最高|最低|最差|最好)\s*[一二两三四五六七八九十\d]*\s*(?:名|个|省)|各省.*排序|省份.*排序', question))
    if not (context_city or city_list or score_comparison or candidate.get('intent') in INTENTS or profile or position or province_list or INFERENCE.search(inference_text(question)) or rt._literal_school_filter(question) or len(regions) >= 3 or len(ys) >= 3 or any(r in PROVINCES or r=='新疆生产建设兵团' for r in regions) or national and re.search(r'平均|均分|均值', question)):
        return None
    if not regions and not national:
        return clarification(question, 'capability_scope_required', '请指定所问地区；全国任务请明确全国范围。') if candidate.get('intent') in INTENTS else None
    ids = rt._scope_question_ids(question) if preserve else list(candidate.get('question_ids') or [])
    dim = (rt._explicit_dimension_alias(question)[0] or (candidate.get('dimension') if candidate.get('dimension') in rt._DIMENSIONS else None)) if preserve else candidate.get('dimension')
    if not ids and preserve:
        ids = [qid for qid, _ in rt._catalogue_alias_items(question)]
    metric_span = (candidate.get('evidence') or {}).get('metric')
    if not ids and candidate.get('question_ids') and isinstance(metric_span, str) and metric_span and metric_span in question:
        ids = list(candidate['question_ids'])
    if preserve:
        completed=bound_item_ids(question,candidate,ids,rt._catalogue()[1])
        if completed!=ids:dim=None
        ids=completed
    # Multi-metric composition is owned by the same registered public compiler.
    if len(rt._explicit_dimension_aliases(question)) > 1 and preserve:
        return None
    if len(ids) > 1 or len(ys) > CAPABILITY_REGISTRY.get(candidate.get('intent'), COMPOSITION).get('max_years', COMPOSITION['max_years']):
        return clarification(question, 'capability_scope_required', '请限定为一个题项、一个维度或整体，以及最多四个年份。')
    qid = ids[0] if ids else None
    profile = profile and not dim and not qid and not re.search(r'比较|对比', question)
    school_filter = rt._literal_school_filter(question) if preserve else candidate.get('school_filter')
    if preserve and not school_filter:
        from packages.core.school_filters import generic_school_text
        stage = re.search(r'小学|初中|高中|初级中学|高级中学|幼儿园|中职|九年一贯制|十二年一贯制', generic_school_text(question))
        school_filter = stage.group() if stage else None
    if len(regions) > 1 and len(ys) > 1:
        return clarification(question, 'capability_scope_required', '请选同一年比较多个地区，或选同一地区查看多年变化。')
    mode = ('province_ranking' if province_list or candidate.get('intent') == 'province_ranking' else 'region_multi_compare' if len(regions) >= 3 else
            'region_series' if len(ys) >= 3 else 'region_profile' if profile else
            'region_position' if position and regions else 'national_eval' if not regions else 'region_eval')
    if city_list:
        mode = 'region_multi_compare'
    if regions==['新疆生产建设兵团']:
        mode='region_eval';profile=False
    composite_filter = bool(re.search(r'乡村|农村|城区|城镇|城市|镇区', school_filter or '') and re.search(r'小学|初中|高中|幼儿园|中职|一贯制', school_filter or ''))
    filtered_task = bool(school_filter and len(regions) == len(ys) == 1 and
                         (composite_filter or qid or rt._explicit_item_statistic(question) in {'median', 'mean_and_median'}))
    descriptive = bool(INFERENCE.search(inference_text(question)))
    if mode == 'region_eval' and regions!=['新疆生产建设兵团'] and not context_city and not score_comparison and not filtered_task and not descriptive and len(ys) <= 2 and all(r in question for r in regions) and candidate.get('intent') not in INTENTS:
        return None  # Preserve existing explicit-name statistics and comparisons.
    if mode == 'region_eval' and regions!=['新疆生产建设兵团'] and not context_city and not score_comparison and not filtered_task and not descriptive and len(regions) <= 2 and all(r.endswith('市') for r in regions) and candidate.get('intent') not in INTENTS:
        return None
    if mode == 'region_eval' and len(ys) == 2:
        return None  # The existing two-year compiler owns this contract.
    if candidate.get('intent') in INTENTS and not preserve and not city_list and regions!=['新疆生产建设兵团']:
        mode = 'region_profile' if open_question and not provincial_position else candidate['intent']
        profile = mode == 'region_profile'
    chart = (preserve and rt._explicit_chart_request(question) is True) or 'charts' in (candidate.get('requested_outputs') or [])
    statistic = rt._explicit_item_statistic(question) if preserve else candidate.get('statistic')
    statistic = 'mean' if score_comparison else statistic or 'mean'
    if preserve and not score_comparison and rt._explicit_region_school_count_only(question):
        statistic = 'count'
    if preserve and mode == 'region_eval' and rt._explicit_answer_pooled_mean(question):
        return None
    if statistic not in {'mean', 'median', 'min', 'max', 'count'}:
        return None
    plan = dict(queries=[], steps=[], selection=None, chart_requested=chart)
    if school_count_requested(question):
        plan['include_school_count'] = True
    cap = dict(mode=mode, target_ids=[], benchmark_ids=[], dimension_ids=[], ranking_ids=[],
               default_year=defaulted, notes=[], descending=not bool(re.search(r'后\s*\d|后[一二三四五六七八九十]|倒数|最低|最差|从低到高|升序', question)))
    if regions==['新疆生产建设兵团']:
        cap['notes'].append('新疆生产建设兵团为单列对象，不并入新疆维吾尔自治区，不参与31个省级行政区排名；排名请求仅给得分，不赋名次。')
    if defaulted:
        cap['notes'].append('未指定可确定的年份，按数据最新年2023年回答。' if '去年' in question else '以下为2023年数据。')
    if INFERENCE.search(inference_text(question)):
        cap['notes'].append(INFERENCE_NOTE)
    if len(ys) > 1:
        cap['notes'].append(TEMPORAL_NOTE)
        if multi_year_request(question) and not re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question):
            cap['notes'].append('使用全部可用年份：'+'、'.join(ys)+'年。')
    for year in ys:
        cap['notes'].extend(filter_notes(year, school_filter))
    cap['notes'] = list(dict.fromkeys(cap['notes']))
    def add(entity, year, dimension=dim, item=qid):
        node = dict(kind='region_value', entity=entity, year=str(year), dimension=dimension,
                    question_id=item, view='school_count' if statistic == 'count' and mode == 'region_eval' else 'raw_value' if item and rt._explicit_value_view(question) == 'raw_value' else candidate.get('value_view') if item and not preserve and candidate.get('value_view') in {'raw_value','normalized_score'} else 'normalized_score',
                    statistic=statistic if mode == 'region_eval' else 'mean', aggregation='school_equal_mean', school_filter=school_filter)
        for old in plan['queries']:
            if all(old.get(k) == v for k, v in node.items()):
                return old['id']
        node['id'] = 'v'+str(len(plan['queries'])+1)
        plan['queries'].append(node)
        return node['id']
    def city_ranking(province, year):
        from scripts.school_engine import connect
        from packages.core.geography_registry import city_groups
        with closing(connect()) as conn:
            grouping = city_groups(conn,province,year)
        ids=[]
        for city in grouping['cities']:
            node_id=add(province+' '+city['city'],year)
            next(q for q in plan['queries'] if q['id']==node_id)['city_group']=True
            ids.append(node_id)
        cap['city_grouping']={k:v for k,v in grouping.items() if k!='cities'}
        cap['notes'].append('省内城市按地理字典的规范名称合并分组；'+str(year)+'年城市字段无法对应到本省城市的学校登记排除'+str(grouping['excluded_records'])+'条。仅影响城市分组，不改变单城市及省级查询的筛选口径。')
        return ids
    if city_list:
        cap['city_list']=True
        cap['target_ids']=city_ranking(regions[0],ys[0])
        cap['ranking_ids']=list(cap['target_ids'])
        if not cap['target_ids']:
            return unavailable(question,'city_grouping','没有可按地理字典识别的城市分组；排除'+str(cap['city_grouping']['excluded_records'])+'条学校登记。')
    else:
        for entity in regions or ['全国']:
            for year in ys:
                cap['target_ids'].append(add(entity, year))
    if mode in {'region_profile', 'region_position', 'province_ranking'}:
        if len(ys) != 1 or (CAPABILITY_REGISTRY[mode].get('single_scope') and len(regions) != 1):
            return clarification(question, 'capability_scope_required', '全国/省内位置或开放式评估请指定一个地区和一个年份。')
        if mode == 'region_profile':
            # Profile has a fixed overall + six-dimension contract.
            cap['target_ids'] = [add(regions[0], ys[0], None, None)]
            cap['dimension_ids'] = [add(regions[0], ys[0], d, None) for d in sorted(rt._DIMENSIONS)]
        cap['benchmark_ids'] = [add('全国', ys[0], None if profile else dim, None if profile else qid)]
        cap['notes'].append(SCOPE_NOTE)
        if mode == 'province_ranking' or regions and regions[0] in PROVINCES:
            cap['ranking_ids'] = [add(p, ys[0], None if profile else dim, None if profile else qid) for p in PROVINCES]
        elif mode in {'region_profile', 'region_position'} and (bool(provincial_position or explicit_parent or re.search(r'省内|所在省|本省|省平均|全省平均', question)) if preserve else candidate.get('benchmark') == 'province'):
            from scripts.school_engine import connect
            from scripts.region_engine import detect_region, _formal_city
            with closing(connect()) as conn:
                from packages.semantic_query.typed_plan import region_sql
                reg = region_sql(bind_region({'entity': regions[0]}, conn, question))
                if not reg or reg.get('region_type') not in {'城市', '地级市'}:
                    return clarification(question, 'ambiguous_city', '请明确哪个城市，以及它所属的省份。')
                cap['benchmark_ids'] = [add(reg['province'], ys[0])]
                cap['ranking_ids'] = city_ranking(reg['province'],ys[0])
        if mode == 'region_profile' and regions[0] not in PROVINCES:
            from scripts.school_engine import connect
            from packages.semantic_query.typed_plan import region_sql
            with closing(connect()) as conn:
                parent = region_sql(bind_region(dict(entity=regions[0], year=ys[0]), conn, question)).get('province')
            if parent:
                cap['province_target_id'] = add(parent, ys[0], None, None)
                cap['ranking_ids'] = [add(p, ys[0], None, None) for p in PROVINCES]
        target = cap['target_ids'][0]
        benchmark = cap['benchmark_ids'][0]
        if target != benchmark:
            plan['steps'].append(dict(id='d1', operation='subtract', args=[target, benchmark]))
    if mode == 'region_multi_compare':
        cap['ranking_ids'] = list(cap['target_ids'])
    if mode == 'region_eval' and len(cap['target_ids']) == 2:
        cap['ranking_ids'] = list(cap['target_ids'])
        if rt._comparison_difference_requested(question):
            plan['steps'].append(dict(id='d1', operation='subtract', args=list(cap['target_ids'])))
    if mode == 'province_ranking':
        from packages.semantic_query.catalogue import _question_number
        match = re.search(r'(?:前|后|倒数|最高|最低|最好|最差)\s*([一二两三四五六七八九十\d]+)', question)
        cap['top_n'] = _question_number(match[1]) if preserve and match else candidate.get('target_top_n') or 31
        if type(cap['top_n']) is not int or not 1 <= cap['top_n'] <= 31:
            return clarification(question, 'invalid_province_count', '请指定1至31个省级行政区。')
    if not preserve:
        cap['descending'] = candidate.get('ranking_order') != 'bottom'
    plan['capability'] = cap
    from packages.semantic_query.catalogue import load_semantic_catalogue
    from packages.semantic_query.typed_plan import bind_types
    plan = bind_types(plan, {x['id']: x for x in load_semantic_catalogue()['items']})[0]
    return dict(intent='public_task', public_plan=plan, original=question, year=ys[0],
                value_view='normalized_score', target_dimension=dim,
                requested_outputs=['direct_answer', 'table']+(['charts'] if chart else []))


def profile_values(plan, connection):
    """Batch a provincial profile's existing scalar nodes in one read snapshot.

    One school-mean population supplies national and 31 provincial means. A
    second grouped read supplies the target's six dimensions. No persisted
    cache, changed estimand, or replacement of independent verification.
    """
    import math
    from collections import defaultdict
    from packages.core.school_filters import filter_sql
    from packages.semantic_query.typed_plan import region_sql
    queries=plan['queries'];cap=plan.get('capability') or {}
    if cap.get('mode')!='region_profile':return None
    if len({(q['year'],q.get('school_filter')) for q in queries})!=1:return None
    if any(q['kind']!='region_value' or q['statistic']!='mean' or q['aggregation']!='school_equal_mean'
           or q['view']!='normalized_score' or q.get('question_id') or q.get('topic')
           or (q.get('region_identity') or {}).get('level') not in {'province','national'}
           or (q.get('region_identity') or {}).get('exclusions') for q in queries):return None
    target=next(q for q in queries if q['id']==cap['target_ids'][0])
    if target['region_identity']['province'] not in PROVINCES:return None
    year=target['year'];cohort=target.get('school_filter');f,_=filter_sql(cohort)
    marks=','.join('?' for _ in PROVINCES)
    rows=connection.execute(f'''SELECT s.id,s.province,AVG(a.normalized_value*100) AS score
        FROM schools s JOIN answers a ON s.id=a.school_id
        WHERE s.year=? AND s.province IN ({marks}){f} AND a.normalized_value IS NOT NULL
        GROUP BY s.id ORDER BY score DESC,s.id ASC''',(year,*PROVINCES)).fetchall()
    populations=defaultdict(list)
    for row in rows:
        populations[row['province']].append(float(row['score']))
    national=[float(row['score']) for row in rows]
    scope=region_sql(target['region_identity'])
    where=scope['where_sql'].replace('province','s.province')
    dimensions=connection.execute(f'''SELECT level1_name,ROUND(AVG(score),2) AS score,
        AVG(score) AS raw_score,COUNT(*) AS school_count FROM (
          SELECT s.id,q.level1_name,AVG(a.normalized_value)*100 AS score
          FROM schools s JOIN answers a ON s.id=a.school_id JOIN questions q ON a.question_id=q.id
          WHERE ({where}) AND s.year=?{f} AND a.normalized_value IS NOT NULL
          GROUP BY s.id,q.level1_name) GROUP BY level1_name''',(*scope['where_params'],year)).fetchall()
    dimensions={r['level1_name']:dict(r) for r in dimensions}
    values={}
    for q in queries:
        identity=q['region_identity'];population=national if identity['level']=='national' else populations[identity['province']]
        average=math.fsum(population)/len(population) if population else None
        d=dimensions.get(q.get('dimension'),{})
        value=d.get('score') if q.get('dimension') else round(average,2) if average is not None else None
        raw=dict(region_name=q['entity'],year=year,total_score=value,raw_total_score=average,
                 school_count=len(population),target_dimension_score=value,
                 raw_target_dimension_score=d.get('raw_score'),requested_school_count=d.get('school_count',0))
        if value is None:raw.update(status='empty_filter',error='没有符合筛选条件的有效学校作答')
        values[q['id']]=raw
    return values


def research_constraints(question, routed):
    """Bind the same v5 view once per new plan, preserving annual identities."""
    from scripts.school_engine import connect
    from packages.core.research_data import config, registry, SelectionError
    from packages.semantic_query.typed_plan import region_sql
    out = copy.deepcopy(routed)
    with closing(connect()) as conn:
        for q in out['public_plan']['queries']:
            if str(q['year']) not in registry()['application_years']:
                raise SelectionError('UNSUPPORTED_OR_MISSING_YEAR')
            identity = bind_region(q, conn, question)
            q['region_identity'] = identity
            scope = region_sql(identity)
            q['research_constraints'] = dict(province=scope['province'], city=scope['city'], district=scope['district'],
                year=q['year'], school_filter=q.get('school_filter'), shared_public_compiler=True,
                constraint_trace=[dict(field=k, value=v, operation='EXACT_AND', source='FROZEN_V5_REGISTERED_METADATA')
                                  for k, v in identity.items() if k in {'province','city','district'} and v])
    out['research_public_contract'] = dict(estimand=config()['estimand'], scope_source='TYPED_V5_ANNUAL_CAPABILITY', g2_independent_check=False)
    return out


def decorate(nodes, trace, plan):
    """Deterministic presentation derived only from the executed common tools."""
    cap = plan.get('capability')
    if not cap:
        return ''
    by_id = {n['id']: n for n in nodes}
    raw_by_id = {t['query']['id']: t['raw_result'] for t in trace}
    for node in nodes:
        raw = raw_by_id.get(node['id'], {})
        q = node['scope']
        if q.get('kind') != 'region_value':
            continue
        if node.get('all_items_uncollected'):
            node['unrounded_value']=None
            continue
        node['school_count'] = raw.get('requested_school_count') if q.get('dimension') else raw.get('school_count', 0)
        node['unrounded_value'] = raw.get('raw_target_dimension_score') if q.get('dimension') else raw.get('raw_total_score')
        if node['unrounded_value'] is None or q.get('statistic') != 'mean':
            node['unrounded_value'] = node['value']
        if node['school_count'] is not None and node['school_count'] < 30:
            node['sample_notice'] = SMALL_NOTE
    population = [by_id[i] for i in cap['ranking_ids'] if by_id[i]['value'] is not None]
    population.sort(key=lambda n: ((-1 if cap['descending'] else 1)*n['unrounded_value'], n['label']))
    for n in population:
        n['rank'] = 1+sum(other['unrounded_value'] > n['unrounded_value'] if cap['descending'] else other['unrounded_value'] < n['unrounded_value'] for other in population)
        n['ranking_population'] = len(population)
        national_roster={by_id[i]['scope']['entity'] for i in cap['ranking_ids']}==set(PROVINCES)
        kind='national_provinces' if national_roster else 'province_cities' if cap.get('city_grouping') else 'comparison_set'
        n['ranking_scope']=dict(kind=kind,registered_population=len(cap['ranking_ids']),valid_population=len(population),
            label='全国31个省级行政区' if national_roster else '省内城市' if kind=='province_cities' else '所比较的'+str(len(cap['ranking_ids']))+'个地区')
    targets = [by_id[i] for i in cap['target_ids']]
    pieces = []
    shown = population[:cap['top_n']] if cap['mode'] == 'province_ranking' else population if cap.get('city_list') else targets
    for n in shown:
        count_text=f"，有效学校{n.get('school_count',0)}所" if school_count_visible(plan) else ''
        pieces.append(f"{n['label']}：{n['value']}{n['unit']}"+count_text if n['value'] is not None else f"{n['label']}：没有有效数据")
        if n.get('sample_notice'):
            pieces.append(n['label']+'：'+SMALL_NOTE)
        if n.get('rank'):
            pieces.append(f"在{n['ranking_scope']['label']}中第{n['rank']}名（有效地区{n['ranking_population']}个，同分并列）")
    if cap['ranking_ids'] and cap['mode'] not in {'region_multi_compare', 'province_ranking'}:
        target = by_id[cap['province_target_id']] if cap.get('province_target_id') else targets[0]
        path=lambda n: tuple(n['region_identity'].get(k) for k in ('level','province','city','district'))
        matched = next((n for n in population if path(n) == path(target)), None)
        if matched:
            target.update(rank=matched['rank'], ranking_population=len(population),ranking_scope=matched['ranking_scope'])
            if matched['scope'].get('city_group'):
                pieces.append(f"规范城市分组得分{matched['value']}分，有效学校{matched.get('school_count',0)}所")
                if matched.get('sample_notice'):
                    pieces.append(SMALL_NOTE)
            pieces.append(f"{target['scope']['entity']}在{matched['ranking_scope']['label']}中第{matched['rank']}名（有效地区{len(population)}个，同分并列）")
    if len(population) < len(cap['ranking_ids']):
        pieces.append(f"排名范围中有{len(cap['ranking_ids'])-len(population)}个地区没有有效数据，不赋名次。")
    for i in cap['benchmark_ids']:
        n = by_id[i]
        pieces.append(f"{n['label']}：{n['value']}{n['unit']}，有效学校{n.get('school_count', 0)}所" if n['value'] is not None else f"{n['label']}：没有有效数据")
        if n.get('sample_notice'):
            pieces.append(n['label']+'：'+SMALL_NOTE)
    if len(targets) == 2 and all(n['value'] is not None and n['unit'] == '分' for n in targets):
        a,b = targets
        pieces.append('两地得分相同' if a['value'] == b['value'] else '得分较高的是'+(a['label'] if a['value'] > b['value'] else b['label']))
    for step in plan['steps']:
        a, b = (by_id[i] for i in step['args'])
        if a['value'] is not None and b['value'] is not None:
            difference = by_id[step['id']]['value']
            pieces.append(f"相对{b['scope']['entity']}基准{'高' if difference > 0 else '低' if difference < 0 else '持平'}{abs(difference)}{by_id[step['id']]['unit']}（所问地区减基准）")
    dims = [by_id[i] for i in cap['dimension_ids'] if by_id[i]['value'] is not None]
    if dims:
        for label, extreme in [('最高', max(n['unrounded_value'] for n in dims)), ('最低', min(n['unrounded_value'] for n in dims))]:
            pieces.append('六维中得分'+label+'：'+'、'.join(f"{n['scope']['dimension']} {n['value']}分" for n in dims if n['unrounded_value'] == extreme))
        if len(dims) < 6:
            pieces.append('部分维度没有有效数据，最高最低仅针对有数据的维度。')
    if cap.get('city_grouping'):
        pieces.append('参与排名城市'+str(len(population))+'个')
    notes = list(cap['notes'])
    if any(q['entity'] == '新疆维吾尔自治区' for q in plan['queries']) and SCOPE_NOTE not in notes:
        notes.append('新疆按新疆维吾尔自治区统计，不含新疆生产建设兵团；兵团单列。')
    return '；'.join(pieces)+'。'+' '.join(notes)


def profile_elements(nodes, plan):
    """Four mandatory fact cards, built only from executed tool nodes."""
    from packages.semantic_query.requirement_checklist import PROFILE_ELEMENTS
    cap=plan.get('capability') or {}
    if cap.get('mode') != 'region_profile':
        return []
    by_id={n['id']:n for n in nodes}
    target=by_id[cap['target_ids'][0]]
    benchmark=by_id[cap['benchmark_ids'][0]]
    rank_target=by_id.get(cap.get('province_target_id'), target)
    valid=[by_id[i] for i in cap['dimension_ids'] if by_id[i]['value'] is not None]
    extrema={label:[n['scope']['dimension'] for n in valid if n['unrounded_value']==fn(v['unrounded_value'] for v in valid)]
             for label,fn in [('strongest',max),('weakest',min)]} if valid else {'strongest':[], 'weakest':[]}
    values=[dict(value=target['value'],unit=target['unit'],school_count=target.get('school_count')),
            dict(target=target['value'],national=benchmark['value'],difference=None if target['value'] is None or benchmark['value'] is None else round(target['value']-benchmark['value'],2)),
            dict(entity=rank_target['scope']['entity'],rank=rank_target.get('rank'),population=rank_target.get('ranking_population'),ranking_scope=rank_target.get('ranking_scope'),missing_message='没有有效省级排名数据'),
            dict(**extrema,missing_message='没有有效维度数据，无法判断最强最弱')]
    return [dict(element=key,**value) for (key,_),value in zip(PROFILE_ELEMENTS,values)]


def school_count_visible(plan):
    cap=plan.get('capability') or {}
    comparison=cap.get('mode') in {'region_eval','region_multi_compare'} and not cap.get('city_list') and len(cap.get('target_ids') or [])>1
    return not comparison or bool(plan.get('include_school_count'))


def visible_ids(plan, nodes):
    cap = plan.get('capability')
    if not cap:
        return {n['id'] for n in nodes}
    selected = set(cap['target_ids'] + cap['benchmark_ids'] + cap['dimension_ids'])
    if cap.get('province_target_id'):
        selected.add(cap['province_target_id'])
    selected.update(s['id'] for s in plan['steps'])
    if cap['mode'] == 'province_ranking':
        population = [n for n in nodes if n['id'] in cap['ranking_ids'] and n['value'] is not None]
        population.sort(key=lambda n: ((-1 if cap['descending'] else 1)*n['unrounded_value'], n['label']))
        selected = {n['id'] for n in population[:cap['top_n']]}
    return selected


def prepare_legacy(question, candidate, context):
    """Missing time defaults independently of RP; literal correction stays RP gated."""
    from packages.experiment_protocol.mode import requirement_preservation_enabled
    ys, defaulted = years(question, context)
    c = copy.deepcopy(candidate)
    q = question
    notes = []
    for year in ys:
        notes.extend(filter_notes(year, candidate.get('school_filter')))
    if multi_year_request(question) and not re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question):
        c['periods'] = ys
        q += '，年份'+ '、'.join(ys) +'年'
        notes.append('使用全部可用年份：'+'、'.join(ys)+'年。')
    if not re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question) and len(ys) == 1 and (defaulted or not c.get('periods')):
        c['periods'] = ys
        q += '，年份'+ys[0]+'年'
        if defaulted:
            notes.append('未指定可确定的年份，按数据最新年2023年回答。' if '去年' in question else '以下为2023年数据。')
        if c.get('intent') == 'clarify' and c.get('clarify_reason') in {'missing_year', 'missing_period', None}:
            if c.get('schools'):
                c['intent'] = 'school_fact' if c.get('question_ids') or c.get('school_field') or c.get('compound_key') else 'school_eval'
            elif c.get('regions'):
                c['intent'] = 'region_eval'
    if INFERENCE.search(inference_text(question)) and requirement_preservation_enabled():
        notes.append(INFERENCE_NOTE)
        q = INFERENCE.sub('', q)
    return q, c, notes


def append_notices(result, spec):
    out = copy.deepcopy(result)
    notices = list(spec.get('program_notices') or [])
    notices.extend(out.get('program_notices') or [])
    from packages.core.item_collection import temporal_notes
    notices.extend(temporal_notes(out, spec))
    notices.extend(filter_notes(out.get('year') or out.get('requested_year'), out.get('school_filter')))
    for q in (out.get('public_plan') or {}).get('queries', []):
        notices.extend(filter_notes(q['year'], q.get('school_filter')))
    nodes = out.get('task_outputs') or []
    allowed = visible_ids(out['public_plan'], nodes) if out.get('public_plan') else {n['id'] for n in nodes}
    for node in nodes:
        if node['id'] not in allowed:
            continue
        count = node.get('school_count')
        if isinstance(count, int) and count < 30 and (node.get('scope') or {}).get('kind', '').startswith('region_'):
            notices.append(SMALL_NOTE)
        elif node.get('sample_notice'):
            notices.append(SMALL_NOTE)
    if out.get('object_type', '').startswith('region'):
        for field in ('school_count', 'school_count_a', 'school_count_b', 'requested_school_count'):
            n = out.get(field)
            if isinstance(n, int) and n < 30:
                label = {'school_count_a':'前一地区', 'school_count_b':'后一地区'}.get(field, '本次范围')
                notices.append(label+'有效学校'+str(n)+'所：'+SMALL_NOTE)
    notices = list(dict.fromkeys(notices))
    if notices:
        out['program_notices'] = notices
        out['direct_answer'] = ' '.join([str(out.get('direct_answer') or ''), *[n for n in notices if n not in str(out.get('direct_answer') or '')]]).strip()
    return out


def data_error_message(error):
    text = str(error)
    if any(token in text for token in ('未找到学校', '学校 id=', 'public_task_unavailable_school_year')):
        return '没有查到所请求学校在该年份的数据；请核对学校全名和年份。'
    return None
