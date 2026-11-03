"""RP component: one verified requirement list, before tools and before writing."""
import copy,hashlib,json,re

VERSION='verified-requirements-v2-20261019'
PROFILE_ELEMENTS=(('overall_and_count','整体得分和有效学校数'),('national_comparison','与全国比较'),
                  ('province_rank','省级排名'),('dimension_extrema','最强和最弱维度'))

def extract(question,context=None,model=None,*,resolved=None):
    from packages.semantic_query import runtime as rt,regional_capabilities as cap
    from packages.semantic_query.semantic_preflight import atomic_requirements
    from packages.core.geography_registry import ambiguity_boundary
    from packages.core.school_registry import match_school_name
    from packages.core.school_filters import normalize_school_filter
    context=context or {};text=rt._scope_assertion_text(question)
    periods,defaulted=cap.years(question,context)
    ambiguity=ambiguity_boundary(question,periods[0] if periods else None)
    schools=[match_school_name(s,periods[0] if len(periods)==1 else None) for s in rt._literal_school_name_spans(question)]
    regions=[] if schools or ambiguity else cap.region_literals(question,rt,periods[0] if periods else None)
    # A location embedded in an unregistered school name is not a verified
    # request for that location's aggregate. Keep the typed school lookup.
    lexical_schools=rt._lexical_school_name_spans(question)
    regions=[r for r in regions if not any(r.split()[-1] in school for school in lexical_schools)]
    # An unresolved reference is not permission to invent a focus.
    if not regions and not schools and context.get('last_region') and re.search(r'该省|这个省|这个城市|该市',text):
        regions=[context['last_region']]
    base=atomic_requirements(question)
    dimensions=list(dict.fromkeys(a['value'] for a in base['atoms'] if a['kind']=='dimension'))
    items=list(dict.fromkeys(a['value'] for a in base['atoms'] if a['kind']=='indicator'))
    items=[i for i in items if i in rt._catalogue()[1]]
    from packages.semantic_query.request_semantics import bound_item_ids,school_count_requested,province_position,score_requested
    completed=bound_item_ids(text,model or {},items,rt._catalogue()[1])
    completed_model_items=completed!=items
    if completed_model_items:dimensions=[]
    items=completed
    from packages.semantic_query.catalogue import topic_scope
    secondary=topic_scope(text)
    topics=[]
    if secondary and not rt._scope_question_ids(text):
        topics=secondary['topics']
        dimensions=[secondary['dimension']] if secondary['dimension'] else []
        items=[]
    from packages.core.research_data import explicit_school_filter
    school_filter,known=normalize_school_filter(
        (explicit_school_filter(question) if not schools and not lexical_schools else None) or rt._literal_school_filter(question))
    overall=not topics and not dimensions and not items and bool(rt._OVERALL_SCOPE_RE.search(text) or schools and rt._school_dimension_extrema_request(text))
    provincial_position=province_position(text,model)
    bound=resolved if resolved is not None else (model or {})
    bound_metric=any(bound.get(k) for k in ('dimension','topics','question_ids','compound_key','school_field'))
    profile=not topics and not schools and not dimensions and not items and not bound_metric and cap.open_profile(text,overview=True) and not provincial_position
    statistics=[]
    for stat,pattern in [('mean',r'均值|平均|均分'),('median',r'中位数')]:
        if re.search(pattern,text):statistics.append(stat)
    if school_count_requested(text):statistics.append('count')
    if bound_metric and not schools and cap.open_profile(text,overview=True) and not provincial_position:
        if not any(s!='count' for s in statistics):statistics.insert(0,'mean')
        if 'count' not in statistics:statistics.append('count')
    topic_ranking=bool(topics and (model or {}).get('intent') in {'region_ranking','province_ranking','national_ranking'})
    if regions and not profile and not topic_ranking:
        for stat,pattern in [('max',r'最高|最大'),('min',r'最低|最小')]:
            if re.search(pattern,text):statistics.append(stat)
    if topic_ranking and not any(stat!='count' for stat in statistics):
        statistics.insert(0,'mean')
    if regions and not schools and not items and score_requested(text) and not any(stat!='count' for stat in statistics):
        statistics.insert(0,'mean')
    atoms=copy.deepcopy(base['atoms'])
    if completed_model_items or topics:atoms=[a for a in atoms if a['kind']!='dimension']
    def add(kind,value,label,source='registered_literal'):
        identity=hashlib.sha256(json.dumps([kind,value],ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]
        if not any(a['id']=='req_'+identity for a in atoms):
            atoms.append(dict(id='req_'+identity,kind=kind,value=value,label=label,source=source,verified=True))
    for value in schools:add('school',value,'学校：'+value)
    for value in regions:add('region',value,'地区：'+value)
    for value in items:add('indicator',value,'题项：'+str(value),'registered_model_task' if completed_model_items else 'registered_literal')
    for value in topics:add('topic',value,'二级维度：'+value)
    covered_parent=dimensions[0] if len(dimensions)==1 else bound.get('dimension')
    if covered_parent and not topics and not items:
        for value in (secondary or {}).get('listed_topics',[]):
            add('topic',value,'组成：'+value,'compiled_dimension_scope')
        aspect=re.search(r'在[^，,。；;！？!?]{1,60}?(?:上|方面|这[一二两几]块)|[^，,。；;！？!?]{1,40}?这一块',text)
        if aspect:add('aspect',dict(text=aspect.group(0),dimension=covered_parent),'所问方面：'+aspect.group(0),'compiled_dimension_scope')
    for value in periods:add('year',value,'年份：'+value,'latest_data_year_policy' if defaulted else 'registered_year')
    if school_filter and known:add('filter',school_filter,'学校筛选：'+school_filter)
    for value in statistics:add('statistic',value,'统计量：'+dict(mean='均值',median='中位数',count='有效学校数',max='最高值',min='最低值')[value])
    if overall:add('overall',True,'整体得分')
    if profile:
        for key,label in PROFILE_ELEMENTS:add('profile',key,label,'registered_profile_contract')
    if schools and rt._school_dimension_extrema_request(text):add('school_dimensions',True,'学校六维得分及所问最强/最弱维度')
    rank,top=rt._explicit_ranking_target(text)
    if provincial_position or rank is not None or top is not None or re.search(r'排名|排第几|名次',text):
        add('ranking',dict(rank=rank,top_n=top,scope='province' if provincial_position else None),
            '省内名次和参与排名城市数' if provincial_position else '排名'+('前'+str(top)+'名' if top else ''))
    for a in atoms:
        a.setdefault('label',a['kind']+'：'+str(a['value']));a.setdefault('verified',a['kind']!='unresolved')
    return dict(version=VERSION,question_sha256=base['question_sha256'],requirements=atoms,regions=regions,schools=schools,
                periods=periods,dimensions=dimensions,question_ids=items,school_filter=school_filter,topics=topics,
                statistics=statistics,overall=overall,profile=profile,default_year=defaulted,
                ambiguity=ambiguity,explicit_status=base['status'])

def reconcile(question,model,candidate,checklist,normalized_model):
    """Only registered conflicts are corrected; otherwise keep model judgments."""
    c=copy.deepcopy(candidate);changes=[]
    def put(field,value):
        before=model.get(field)
        if before!=value or c.get(field)!=value:
            changes.append(dict(field=field,before=before,normalized_before=c.get(field),after=value,operation='fill' if before in (None,[], '') else 'correct'))
        c[field]=value
    required={}
    for field in ('regions','schools','periods','question_ids'):
        if checklist[field]:required[field]=checklist[field]
    dims=checklist['dimensions']
    topics=checklist.get('topics') or []
    if topics:
        required.update(topics=topics,dimension=None,question_ids=[])
        if c.get('intent')=='region_profile':put('intent','region_eval')
    elif c.get('topics') and (checklist['question_ids'] or dims):
        required['topics']=[]
    if len(dims)==1:required['dimension']=dims[0]
    elif checklist['overall']:required['dimension']=None
    if checklist['school_filter']:required['school_filter']=checklist['school_filter']
    if checklist['question_ids']:required['dimension']=None
    ranking=next((a['value'] for a in checklist['requirements'] if a['kind']=='ranking'),{})
    if ranking.get('rank') is not None:required['target_rank']=ranking['rank']
    if ranking.get('top_n') is not None:required['target_top_n']=ranking['top_n']
    # Fields with no verified contrary requirement keep the original model value.
    # Explicit regional/school scope and explicit metric grain remain exclusive.
    for field in ('dimension','question_ids','school_filter','statistic','aggregation','value_view','compound_key','school_field'):
        if field not in required and normalized_model.get(field) not in (None,[], ''):
            if not (field in {'dimension','question_ids','compound_key','school_field'} and (checklist['overall'] or checklist['question_ids'] or dims or topics)):
                c[field]=normalized_model[field]
    for field,value in required.items():put(field,value)
    outputs=list(dict.fromkeys([*(normalized_model.get('requested_outputs') or []),*(c.get('requested_outputs') or [])]))
    if any(a['kind']=='output' and a['value']=='charts' for a in checklist['requirements']):
        outputs=list(dict.fromkeys([*outputs,'charts']))
    if outputs:put('requested_outputs',outputs)
    stats=[s for s in checklist['statistics'] if s!='count']
    if len(stats)==1:put('statistic',stats[0])
    elif len(stats)>1:put('statistic',stats[0])
    elif checklist['statistics']==['count']:put('statistic','count')
    restored=False
    metric=checklist['overall'] or checklist['profile'] or dims or topics or checklist['question_ids']
    if model.get('intent')=='clarify' and metric and checklist['periods'] and not checklist['ambiguity'] and checklist['explicit_status']!='UNKNOWN':
        n=len(checklist['regions']);s=len(checklist['schools']);y=len(checklist['periods'])
        intent=('school_fact' if checklist['question_ids'] else 'school_eval') if s==1 else 'school_compare' if s>1 and y==1 else (
            'region_profile' if checklist['profile'] and n==1 else 'region_multi_compare' if n>=3 and y==1 else
            'region_compare' if n==2 and y==1 else 'region_series' if n==1 and y>=3 else
            'region_longitudinal' if n==1 and y==2 else 'region_eval' if n==1 else None)
        from packages.semantic_query.task_draft import supported_scope
        if intent and supported_scope(intent,checklist['schools'],checklist['regions'],checklist['periods'],len(dims),len(checklist['question_ids']),topics=topics):
            put('intent',intent);c['missing']=[];c['clarify_reason']=None;restored=True
    return c,dict(checklist=checklist,changes=changes,clarify_to_execute=restored)

def enrich_route(question,routed,checklist,candidate):
    """Use existing public leaf tools; no new calculation or SQL definition."""
    from packages.semantic_query import runtime as rt,typed_plan
    r=copy.deepcopy(routed)
    if r.get('intent') in {'clarify','unavailable'}:return r
    wants_count='count' in checklist['statistics']
    stats=[s for s in checklist['statistics'] if s!='count']
    wants_difference=any(a['kind']=='result' and a['value']=='difference' for a in checklist['requirements'])
    if not r.get('public_plan') and (wants_count or len(stats)>1):
        # Finalize the verified cohort before emitting its immutable type binding.
        if checklist['school_filter']:r['school_filter']=checklist['school_filter']
        spec=rt._build_answer_spec(question,r)
        if spec.get('target_stat')=='mean_and_median':spec['target_stat']='mean'
        plan=typed_plan.bind_routed_plan(r,spec,rt._catalogue()[1])
        if plan:
            r.update(intent='public_task',public_plan=plan,value_view=spec.get('value_view') or 'normalized_score')
    plan=r.get('public_plan')
    if plan:
        if wants_count:plan['include_school_count']=True
        if stats and not plan.get('capability'):
            base=list(plan['queries'])
            for q in base:
                if q['kind']!='region_value':continue
                for stat in stats:
                    if any(all(x.get(k)==q.get(k) for k in ('kind','entity','year','dimension','question_id','view','school_filter')) and x.get('statistic')==stat for x in plan['queries']):continue
                    node=copy.deepcopy(q);node.update(id='v'+str(len(plan['queries'])+1),statistic=stat)
                    plan['queries'].append(node)
        if wants_difference and not plan.get('steps') and len(plan['queries'])==2:
            args=typed_plan.explicit_subtraction_operands(plan,question,rt) or [q['id'] for q in plan['queries']]
            plan['steps']=[dict(id='d1',operation='subtract',args=args)]
        if any(a['kind']=='output' and a['value']=='charts' for a in checklist['requirements']):plan['chart_requested']=True
        r['public_plan']=typed_plan.bind_types(plan,rt._catalogue()[1])[0]
    r['requested_outputs']=list(dict.fromkeys([*(r.get('requested_outputs') or ['direct_answer']),*(candidate.get('requested_outputs') or [])]))
    if checklist['default_year']:
        r['program_notices']=list(dict.fromkeys([*(r.get('program_notices') or []),'按数据最新年2023年回答。']))
    r['requirements_checklist']=checklist
    return r

def fact_cards(evidence,spec):
    """Bind each requested element to existing executed facts, or explicit absence."""
    from packages.semantic_query.required_facts import required_facts
    facts=required_facts(evidence,spec);cards=[]
    for f in facts:
        if not f.get('scope'):
            key=f['key']
            statistic=('count' if f.get('unit')=='所' else 'median' if 'median' in key else
                       'max' if key=='max_score' else 'min' if key=='min_score' else spec.get('target_stat') or 'mean')
            f['scope']=dict(dimension=(f.get('label') or evidence.get('target_dimension')) if spec.get('granularity') in {'dimension','dimension_pair'} else None,
                            question_id=f.get('question_id') or evidence.get('question_id'),statistic=statistic,
                            school_filter=evidence.get('school_filter'),operation=f.get('operation'))
    for a in (spec.get('requirements_checklist') or {}).get('requirements',[]):
        kind,value=a['kind'],a['value'];matched=[];coverage=[]
        if kind=='profile':matched=[x for x in evidence.get('profile_required_elements') or [] if x['element']==value]
        elif kind=='school_dimensions':matched=[f for f in facts if f['key'].startswith('dimension:')]
        elif kind=='output' and value in spec.get('required_outputs',[]):
            matched=[dict(output=value,requested=True,source='AnswerSpec',
                          values=facts if value=='charts' else None)]
        elif kind=='ranking' and evidence.get('ranking_items'):
            matched=copy.deepcopy(evidence['ranking_items'])
        elif kind=='topic':
            matched=[f for f in facts if (f.get('scope') or {}).get('topic')==value]
            if not matched:
                for f in facts:
                    component=next((t for t in f.get('dimension_components',[]) if t['name']==value),None)
                    if component:
                        matched.append(f);coverage.append(dict(fact_key=f['key'],**component))
        elif kind=='aspect':
            matched=[f for f in facts if f.get('dimension')==value['dimension'] and f.get('dimension_components')]
            coverage=[dict(fact_key=f['key'],dimension=value['dimension'],text=value['text'],message='所问方面已按'+value['dimension']+'维度得分回答') for f in matched]
        else:
            for f in facts:
                scope=f.get('scope') or {}
                ok=(kind in {'region','school'} and value in [f.get('entity'),*(f.get('entity_aliases') or [])] or kind=='year' and str(f.get('period'))==value or
                    kind=='topic' and scope.get('topic')==value or
                    kind=='dimension' and scope.get('dimension')==value or kind=='indicator' and scope.get('question_id')==value or
                    kind=='filter' and scope.get('school_filter')==value or kind=='statistic' and (scope.get('statistic')==value or value=='count' and f.get('unit')=='所') or
                    kind=='overall' and not scope.get('dimension') and not scope.get('question_id') or
                    kind=='result' and value=='difference' and scope.get('operation')=='subtract' or kind=='ranking' and f.get('rank') is not None)
                if ok:matched.append(f)
        cards.append(dict(requirement_id=a['id'],label=a['label'],facts=matched,status='AVAILABLE' if matched else 'NOT_PROVIDED',
                          missing_message=None if matched else '当前工具未提供这一要素，不得补造。'))
        if coverage:cards[-1]['coverage']=coverage
    return cards
