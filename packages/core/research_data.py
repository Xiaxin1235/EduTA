from __future__ import annotations
import copy, functools, hashlib, json, os, re
from contextvars import ContextVar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUEST = ContextVar('research_request', default='')
class SelectionError(ValueError): pass

def enabled():
    return os.environ.get('TEACHER_EVAL_RESEARCH_DISABLE') != '1' and config_path().is_file()

def config_path():
    return Path(os.environ.get('TEACHER_EVAL_RESEARCH_CONFIG') or ROOT/'config/research_dataset.json')

@functools.lru_cache(maxsize=4)
def _load(path):
    return json.loads(Path(path).read_text('utf-8'))

def config():
    cfg = copy.deepcopy(_load(str(config_path().resolve())))
    for item in cfg['data'].values():
        path = Path(item['path']).expanduser()
        if not path.is_absolute():
            path = config_path().resolve().parent / path
        item['path'] = str(path.resolve())
    return cfg
def registry():
    path = Path(config()['registry']).expanduser()
    if not path.is_absolute():
        path = config_path().resolve().parent / path
    return _load(str(path.resolve()))

@functools.lru_cache(maxsize=8)
def _verify_file(path, expected, size, mtime):
    with Path(path).open('rb') as f: observed = hashlib.file_digest(f, 'sha256').hexdigest()
    if observed != expected: raise SelectionError('FROZEN_RESEARCH_INPUT_HASH_MISMATCH')
    return observed

def identity():
    cfg = config(); result = {'version':cfg['version'], 'estimand':cfg['estimand'], 'read_only':True}
    for k, v in cfg['data'].items():
        p=Path(v['path']); st=p.stat()
        result[k]={'SHA256':_verify_file(str(p),v['SHA256'],st.st_size,st.st_mtime_ns),'bytes':st.st_size}
    result['registry_version']=registry()['version']
    return result

def attach(conn, source_path):
    pass
    cfg=config();identity()
    if Path(source_path).resolve()!=Path(cfg['data']['source']['path']).resolve():
        raise SelectionError('RESEARCH_SOURCE_PATH_MISMATCH')
    for label,k in [('research_base','base_v1'),('research_mapping','mapping_v5')]:
        conn.execute('ATTACH DATABASE ? AS '+label,(Path(cfg['data'][k]['path']).as_uri()+'?mode=ro',))
    conn.execute('''CREATE TEMP VIEW research_schools AS SELECT
      b.id,b.year,b.province,b.city,b.district,b.school_name,b.school_type,b.school_area_type,
      b.class_num,b.teacher_num,b.student_num,b.data_mark,b.region,
      CASE WHEN m.source_school_year_id IS NOT NULL THEN m.normalized_province ELSE b.normalized_province END AS query_province,
      CASE WHEN f.source_school_year_id IS NOT NULL THEN f.new_value WHEN m.source_school_year_id IS NOT NULL THEN m.normalized_city ELSE b.normalized_city END AS query_city,
      CASE WHEN m.source_school_year_id IS NOT NULL THEN m.normalized_district ELSE b.normalized_district END AS query_district,
      r.status AS research_status,
      CASE WHEN m.source_school_year_id IS NOT NULL THEN 'V4_EVIDENCE_OVERLAY' ELSE 'V1_FORMAT_BASE' END AS metadata_source,
      f.rule_id AS additional_format_rule
      FROM research_base.school_records b
      LEFT JOIN research_mapping.record_metadata m ON m.source_school_year_id=b.id AND m.year=b.year
      LEFT JOIN research_mapping.format_extension_v5 f ON f.source_school_year_id=b.id AND f.year=b.year
      LEFT JOIN research_mapping.research_review_v5 r ON r.source_school_year_id=b.id AND r.year=b.year''')
    conn.execute('''CREATE TEMP VIEW schools AS SELECT id,year,
      query_province AS province,query_city AS city,query_district AS district,
      school_name,school_type,school_area_type,class_num,teacher_num,student_num,data_mark,region,
      province AS raw_province,city AS raw_city,district AS raw_district,
      query_province,query_city,query_district,research_status,metadata_source,additional_format_rule
      FROM research_schools''')
    conn.execute('PRAGMA query_only=ON')

def request_scope(fn):
    @functools.wraps(fn)
    def run(question,*args,**kwargs):
        token=REQUEST.set(str(question))
        try:
            value=fn(question,*args,**kwargs)
            if enabled() and isinstance(value,dict): value['research_dataset']=identity()
            return value
        finally: REQUEST.reset(token)
    return run

def _public_text(text):

    from packages.semantic_query.runtime import _literal_school_name_spans
    for school in _literal_school_name_spans(text):
        if re.search(r'(?:乡村|农村|城区|城镇|城市|镇区)(?:的)?(?:小学|初中|高中|幼儿园)$',school) or school in ('小学','初中','高中','幼儿园'):continue
        text=text.replace(school,' '*len(school))
    return text

def _positive(text, literal):
    return bool(re.search(r'(?<![\u4e00-\u9fff])'+re.escape(literal),text)) or bool(re.search(re.escape(literal),text))

def _registered_metadata_region(keyword, question, active_year):
    pass
    reg = registry()
    years = re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', question)
    from packages.experiment_protocol.mode import requirement_preservation_enabled
    if not requirement_preservation_enabled():
        years = []
    if any(y not in reg['application_years'] for y in years) or (active_year is not None and str(active_year) not in reg['application_years']):
        raise SelectionError('UNSUPPORTED_OR_MISSING_YEAR')

    if re.search(r'排除|不含|不包括|剔除|不要纳入|除去', question):
        raise SelectionError('UNSUPPORTED_METADATA_REGION_EXCLUSION')
    if re.search(r'(?:不要|不是|不看)\s*[^，,。；;！？!?]{0,12}(?:省|市|县|区|旗)', question):
        raise SelectionError('UNSUPPORTED_METADATA_REGION_EXCLUSION')
    from contextlib import closing
    from scripts.region_engine import detect_region
    from scripts.school_engine import connect
    from packages.semantic_query.typed_plan import region_identity, region_sql
    from packages.semantic_query.runtime import _literal_region_spans, _scope_assertion_text
    positive = _scope_assertion_text(_public_text(question))
    literals = _literal_region_spans(positive)

    complete_paths = re.findall(r'[\u4e00-\u9fff]{2,10}?(?:自治区|特别行政区|省|市)(?:\s*[\u4e00-\u9fff]{2,10}?(?:市|州|盟))?\s*[\u4e00-\u9fff]{2,10}?(?:区|县|旗)', positive)
    for path in complete_paths:
        path = re.sub(r'^(?:只看|只查|只统计|仅限|仅统计|纳入|统计)+', '', path)
        literals = [value for value in literals if value not in path]
        if path not in literals:
            literals.append(path)
    literals = [re.sub(r'^(?:只看|只查|只统计|仅限|仅统计|纳入|统计)+', '', value) for value in literals]

    components = re.findall(r'[\u4e00-\u9fff]{2,10}?(?:自治区|特别行政区|省|市|县|区|旗)', ' '.join(literals))
    def level(value):
        if value.endswith(('省', '自治区', '特别行政区')): return 'province'
        return 'city' if value.endswith('市') else 'district'
    for tier in ('province', 'city', 'district'):
        if len({x for x in components if level(x) == tier}) > 1:
            raise SelectionError('AMBIGUOUS_EXPLICIT_METADATA_REGION')
    original_keyword = ' '.join(literals) if literals else keyword
    with closing(connect()) as conn:
        found = detect_region(original_keyword, connection=conn, _metadata_only=True)
        if found and positive and not literals:
            name = str(found['name'])
            bare = re.sub(r'(?:省|市|县|区|旗|自治区)$', '', name)
            if name not in positive and bare and bare in positive:

                found = detect_region(bare, connection=conn, _metadata_only=True)
    if not found:
        raise SelectionError('UNSUPPORTED_OR_AMBIGUOUS_CITY')
    if any(part not in {found.get('province'), found.get('city'), found.get('district')} for part in components):
        raise SelectionError('CONTRADICTORY_EXPLICIT_METADATA_REGION')
    scope = region_sql(region_identity(found))
    scope['constraint_trace'] = [dict(field=k, value=scope[k], operation='EXACT_AND', source='FROZEN_V5_REGISTERED_METADATA')
                                 for k in ('province', 'city', 'district') if scope.get(k)]
    scope.update(registry_version=reg['version'], research_estimand=config()['estimand'])
    return scope

def geographic_request(keyword, question='', active_year=None):
    pass
    reg=registry(); text=str(keyword); whole=_public_text(question) if question else text

    whole=re.sub(r'(?:区县|区、县|区级|县级)(?:及以上|以上)(?=教育云)', lambda m:' '*len(m.group()), whole)
    for name in sorted(set(reg['city_aliases'])|set(reg['parent_by_city']),key=len,reverse=True):
        text=re.sub(r'(?:排除|不含|不包括|剔除|不要纳入|除去)\s*'+re.escape(name),' ',text)
    aliases=reg['city_aliases']; cities=[]
    for alias in sorted(set(aliases)|set(reg['parent_by_city']),key=len,reverse=True):
        city=aliases.get(alias,alias)
        if alias in text and city not in cities:cities.append(city)
    if not cities:
        return _registered_metadata_region(text, whole, active_year)
    if len(cities)!=1: raise SelectionError('UNSUPPORTED_OR_AMBIGUOUS_CITY')
    city=cities[0]; parent=reg['parent_by_city'][city]
    positives=[];exclusions=[];cleanwhole=whole

    for field,names in [('province',set(reg['parent_by_city'].values())),('city',set(aliases)|set(reg['parent_by_city']))]:
        for name in sorted(names,key=len,reverse=True):
            pattern=r'(?:排除|不含|不包括|剔除|不要纳入|除去)\s*'+re.escape(name)
            if re.search(pattern,cleanwhole):
                val=reg.get(field+'_aliases',{}).get(name,name)
                if (field,val) not in exclusions:exclusions.append((field,val))
                cleanwhole=re.sub(pattern,' ',cleanwhole)
    wholecities={aliases.get(a,a) for a in aliases if a in cleanwhole}
    if len(wholecities)==1 and city not in wholecities:
        raise SelectionError('CONFLICTING_EXPLICIT_REQUEST_CITY')
    for alias,p in reg['province_aliases'].items():

        if alias.endswith(('省','自治区')) and alias in cleanwhole: positives.append(p)
    from scripts.region_engine import PROVINCES
    for short in PROVINCES:
        full=short+'省'
        if full in cleanwhole:positives.append(full)
    positives=list(dict.fromkeys(positives))
    if len(wholecities)<=1 and positives and (len(positives)>1 or positives[0]!=parent):
        raise SelectionError('CONTRADICTORY_EXPLICIT_PROVINCE_CITY')
    years=re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)',whole)
    if any(y not in reg['application_years'] for y in years):raise SelectionError('UNSUPPORTED_OR_MISSING_YEAR')
    if active_year is not None and str(active_year) not in reg['application_years']:
        raise SelectionError('UNSUPPORTED_OR_MISSING_YEAR')
    dictionary_year=str(active_year) if active_year is not None else (years[0] if len(set(years))==1 else reg['application_years'][-1])
    candidates=reg['districts'].get(dictionary_year,{}).get(city,[])
    district=None
    for name in sorted(candidates,key=len,reverse=True):
        alias=name[:-1] if name.endswith(('区','县')) else name
        neg=r'(?:排除|不含|不包括|剔除|不要纳入|除去)\s*'+re.escape(alias)+r'(?:区|县)?'
        if re.search(neg,cleanwhole):
            exclusions.append(('district',name));cleanwhole=re.sub(neg,' ',cleanwhole)
        elif name in text or re.search(r'(?:市|只看|仅限|在|纳入|、|\s)'+re.escape(alias)+r'(?:区|县)?',cleanwhole):
            if district and district!=name:raise SelectionError('AMBIGUOUS_DISTRICT')
            district=name

    unknown=re.findall(r'([\u4e00-\u9fff]{2,6}(?:区|县))',cleanwhole)
    for token in unknown:
        if token in ('城区','城镇或城区','市区','地区','区域','本区') or token.endswith(('地区','城区','镇区')):continue
        if not any(token.endswith(d) for d in candidates) and not any(t in token for t in ['数字','机制','素养','教学']):
            raise SelectionError('DISTRICT_YEAR_NOT_REGISTERED')
    clauses=['province=?','city=?'];params=[parent,city]
    trace=[{'field':'province','value':parent,'source':'FROZEN_CITY_PARENT_REGISTRY'}, {'field':'city','value':city,'operation':'EXACT_AND'}]
    if district:clauses.append('district=?');params.append(district);trace.append({'field':'district','value':district,'operation':'EXACT_AND'})
    for field,value in dict.fromkeys(exclusions):
        clauses.append('('+field+' IS NULL OR '+field+'<>?)');params.append(value)
        trace.append({'field':'exclude_'+field,'value':value,'operation':'EXPLICIT_NOT_EXACT'})
    return {'where_sql':' AND '.join(clauses),'where_params':tuple(params),'province':parent,'city':city,
            'district':district,'name':district or city,'region_type':'区县' if district else '城市','full_name':' '.join(dict.fromkeys([parent,city]+([district] if district else []))),
            'constraint_trace':trace,'registry_version':reg['version'],'research_estimand':config()['estimand']}

def resolve_region(keyword):
    return geographic_request(keyword,REQUEST.get())

def explicit_school_filter(question):
    text=_public_text(question)
    area=list(dict.fromkeys(re.findall(r'乡村|农村|城区|城镇|城市|镇区',text)))
    stage=list(dict.fromkeys(re.findall(r'九年一贯制|十二年一贯制|小学|初级中学|初中|高级中学|高中|幼儿园|中职',text)))

    if len(area)>1 or len(stage)>1:return None
    if re.search(r'(?:排除|不含|不包括|剔除|不要纳入)\s*(?:乡村|农村|城区|城镇|城市|镇区|小学|初中|高中|幼儿园)',text):
        return None
    return ' '.join(area+stage) or None

def bind_public_constraints(question,routed):
    pass
    if not enabled() or routed.get('intent')=='clarify':return routed
    out=copy.deepcopy(routed)
    intent=out.get('intent');targets=[]
    if intent=='public_task':targets=[q for q in out['public_plan']['queries'] if q['kind'].startswith('region_')]
    elif str(intent).startswith('region_'):targets=[out]
    if not targets:return out
    from packages.semantic_query.runtime import _literal_school_filter
    from packages.experiment_protocol.mode import requirement_preservation_enabled
    preserve = requirement_preservation_enabled()
    if preserve and re.search(r'(?:排除|不含|不包括|剔除|不要纳入)\s*(?:乡村|农村|城区|城镇|城市|镇区|小学|初中|高中|幼儿园)',_public_text(question)):
        raise SelectionError('UNSUPPORTED_NEGATED_SCHOOL_ATTRIBUTE')
    literal=(explicit_school_filter(question) or _literal_school_filter(question)) if preserve else None
    years=set(re.findall(r'(?<!\d)(20\d{2})(?!\d)',question)) if preserve else set()
    for q in targets:
        entity=q.get('entity') if intent=='public_task' else (q.get('region_keyword') or q.get('region'))
        if not entity:continue

        if intent=='region_longitudinal' or q.get('year_a') is not None or q.get('year_b') is not None:
            if not q.get('year_a') or not q.get('year_b'):raise SelectionError('UNSUPPORTED_OR_MISSING_YEAR')
            annual_nodes=[('year_a',q['year_a']),('year_b',q['year_b'])]
        elif isinstance(q.get('years'),list) and q['years']:
            annual_nodes=[('years['+str(i)+']',y) for i,y in enumerate(q['years'])]
        else:
            if not q.get('year') and len(years)==1:q['year']=next(iter(years))
            if not q.get('year'):raise SelectionError('UNSUPPORTED_OR_MISSING_YEAR')
            annual_nodes=[('year',q['year'])]
        scopes=[]
        for field,node_year in annual_nodes:
            if years and str(node_year) not in years:raise SelectionError('CONFLICTING_EXPLICIT_REQUEST_YEAR')
            scope=geographic_request(entity,question,active_year=node_year)
            scope['constraint_trace'].append({'field':'year','value':node_year,'task_field':field,'operation':'EXPLICIT_ANNUAL_ID_AND'})
            if literal:scope['constraint_trace'].append({'field':'school_filter','value':literal,'operation':'ALL_EXPLICIT_ATTRIBUTES_AND'})
            scopes.append({'province':scope['province'],'city':scope['city'],'district':scope['district'],
                           'constraint_trace':scope['constraint_trace'],'school_filter':literal or q.get('school_filter'),
                           'year':node_year,'task_field':field,'shared_public_compiler':True})
        q['research_constraints']=scopes[0] if len(scopes)==1 else {'annual_scopes':scopes,'shared_public_compiler':True}
        if literal:q['school_filter']=literal
    out['research_public_contract']={'estimand':config()['estimand'],'scope_source':'EXPLICIT_USER_REQUEST_AND_FROZEN_DICTIONARY','g2_independent_check':False}
    return out
