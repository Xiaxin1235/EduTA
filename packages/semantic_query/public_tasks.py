"""Mode-neutral composition of existing read-only school and region tools.

No experiment imports, answer keys, model calls, scoring, or question-bank branches.
The plan preserves every requested object, year, view, filter and ordered operand.
"""
from __future__ import annotations
import copy, hashlib, json, math, re
from packages.semantic_query.catalogue import load_semantic_catalogue, question_metric
from packages.semantic_query import typed_plan
from packages.semantic_query.task_draft import COMPOSITION, registered

def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def route_public_task(question, candidate, rt, *, literal_binding=True):
    if candidate.get('intent') and not registered(candidate['intent']): return None
    if candidate.get('topics'):
        from packages.semantic_query.topic_tasks import compile_topic_task
        return compile_topic_task(question,candidate,rt)
    text=rt._scope_assertion_text(question)
    # Unsupported extra operations/restrictions must not disappear when atoms compose.
    from packages.semantic_query.request_semantics import inference_text
    if re.search(r'因果|造成|导致|显著|预测|除以|相除|比值|相加|加起来|合计|求和|相乘|乘以|÷|×|至少|至多|大于|小于|不低于|不高于|≥|≤',inference_text(text)): return None
    schools=rt._literal_school_name_spans(text) if literal_binding else list(candidate.get('schools') or [])
    # Retain complete model literals when the generic lexical span is a shorter prefix.
    model_schools=list(candidate.get('schools') or [])
    if literal_binding and model_schools and not schools:
        return None  # Preserve the typed school lookup, including not-found output.
    for i,s in enumerate(schools):
        matches=[m for m in model_schools if isinstance(m,str) and m in text and m.startswith(s)]
        if len(matches)==1: schools[i]=matches[0]
    regions=rt._literal_region_spans(text) if literal_binding else list(candidate.get('regions') or [])
    years=rt._scope_years(text) if literal_binding else list(candidate.get('periods') or [])
    if schools and len(years)>1:
        from packages.semantic_query.regional_capabilities import years as capability_years
        years=capability_years(question,{})[0]
    if not years or len(years)>COMPOSITION['max_years'] or (schools and regions): return None
    school_filter=rt._literal_school_filter(text) if literal_binding else candidate.get('school_filter')
    if school_filter and (schools or re.search(r'公办|民办',school_filter)): return None
    filter_group=bool(re.search(r'乡村|农村',text) and re.search(r'城区|城镇|城市',text))
    if filter_group and (len(regions)>1 or len(set(re.findall(r'小学|初中|高中|幼儿园',text)))>1):return None
    if not schools and school_filter is None:
        if filter_group and not re.search(r'各.{0,8}多少所|两组.*学校数',text):return None
        if len(set(re.findall(r'小学|初中|高中|幼儿园',text)))>1:return None
    ids=rt._scope_question_ids(text) if literal_binding else list(candidate.get('question_ids') or [])
    aliases=rt._catalogue_alias_items(text)
    if not ids and aliases: ids=[i for i,_ in aliases]
    if not ids and candidate.get('question_ids') and candidate.get('evidence',{}).get('metric'):
        # Model understanding is used only with a literal metric span, as in the common parser.
        span=candidate['evidence']['metric']
        if isinstance(span,str) and span in question: ids=list(candidate['question_ids'])
    catalogue={x['id']:x for x in load_semantic_catalogue()['items']}
    from packages.semantic_query.request_semantics import bound_item_ids,school_count_requested,score_requested
    if literal_binding:ids=bound_item_ids(text,candidate,ids,catalogue)
    if len(ids)>COMPOSITION['school_items'] or any(type(i)!=int or i not in catalogue for i in ids): return None
    dims=rt._explicit_dimension_aliases(text)
    if len(dims)>COMPOSITION['dimensions']: return None
    dim=dims[0][0] if len(dims)==1 else candidate.get('dimension')
    if dim not in rt._DIMENSIONS: dim=None
    view=typed_plan.explicit_view(question,rt)
    dual_view=view=='mixed_raw_and_normalized_score'
    if dual_view and not (len(schools)==1 and len(years)==1 and len(ids)==1):
        return {'intent':'clarify','reason':'dual_view_scope_not_supported',
                'missing':['当前双视图输出支持一所学校、一个年度和一道题；请明确这一范围。'],'original':question}
    if view=='raw_value' and not ids: return None
    if view is None: view='raw_value' if ids else 'normalized_score'
    explicit_chart=rt._explicit_chart_request(question)
    chart=explicit_chart is True or (explicit_chart is not False and 'charts' in (candidate.get('requested_outputs') or []))
    plan={'queries':[],'steps':[],'selection':None,'chart_requested':chart}
    if school_filter and re.search(r'有效学校数|有效学校|纳入统计.{0,6}学校|多少所',text):
        plan['include_school_count'] = True
    def add(kind,entity,year,dimension=None,qid=None,statistic='mean',aggregation=None,school_filter=None,node_view=None):
        if aggregation is None:
            aggregation=(typed_plan.explicit_aggregation(question,rt) if statistic=='mean' else None) or 'school_equal_mean'
        if kind in {'school_score','school_dimension_rank'}:
            statistic='template_weighted_score'
            aggregation='template_weighted_dimension_mean'
        node='v'+str(len(plan['queries'])+1)
        plan['queries'].append(dict(id=node,kind=kind,entity=entity,year=str(year),dimension=dimension,
            question_id=qid,view=node_view or ('school_count' if statistic=='count' and not qid else view if qid else 'normalized_score'),statistic=statistic,
            aggregation=aggregation,school_filter=school_filter))
        return node
    def difference(nodes,reverse=False):
        if rt._comparison_difference_requested(question) or re.search(r'差多少|相差|差值|差几|相隔.{0,6}分|减|往上还是往下|变化',text):
            plan['steps'].append({'id':'d1','operation':'subtract','args':list(reversed(nodes)) if reverse else list(nodes)})
    if len(dims)>1 and not ids and len(years)==1 and len(schools)+len(regions)==1:
        kind='school_score' if schools else 'region_value'
        entity=(schools or regions)[0]
        nodes=[add(kind,entity,years[0],name,school_filter=school_filter) for name,_ in dims]
        plan['include_school_count'] = not schools
        if len(nodes)==2: difference(nodes)
        elif rt._comparison_difference_requested(question): return None
    elif dual_view:
        if catalogue[ids[0]]['coding_status']=='source_missing':
            return {'intent':'clarify','reason':'dual_view_source_coding_unavailable',
                    'missing':['该题原始值的源编码说明不足，无法完整交付原始值和标准分。'],'original':question}
        for requested_view in ('raw_value','normalized_score'):
            add('school_fact',schools[0],years[0],qid=ids[0],statistic=None,aggregation='none',node_view=requested_view)
        plan['measurement_note']='原始值保留登记量纲；标准分为登记归一值乘以100，两者分别报告。'
    elif not schools and len(regions)==2 and len(years)==1 and not ids and (school_count_requested(text) or re.search(r'哪边.{0,18}学校.{0,5}多|学校更多',text)) and not score_requested(text) and not re.search(r'均分|平均(?:分|得分)|(?:整体|综合|维度).{0,6}(?:得分|分数)|分差',text):
        nodes=[add('region_value',region,years[0],dim,statistic='count',school_filter=rt._literal_school_filter(text)) for region in regions]
        difference(nodes,reverse=bool(re.search(r'后者.{0,8}减.{0,8}前者',text)))
    elif len(schools)>=2 and len(years)==1 and not ids and (rt._OVERALL_SCOPE_RE.search(text) or dim):
        if len(schools)==2 and re.search(r'六维|六个维度|全维|雷达|分析|原因|建议|有效作答数|覆盖题数',text):return None
        nodes=[add('school_score',school,years[0],dim) for school in schools]
        if len(nodes)==2: difference(nodes,reverse=bool(re.search(r'后(?:者|校|项).{0,8}减.{0,8}前',text)))
        if re.search(r'中间|居中',text):
            if len(schools)%2==0: return {'intent':'clarify','reason':'even_population_middle_ambiguous','missing':['偶数所学校有两个中间位置，请指定。'],'original':question}
            plan['selection']={'kind':'middle','descending':True}
        elif re.search(r'排序|排位|排名|排第',text): plan['selection']={'kind':'all','descending':not bool(re.search(r'从低到高|升序',text))}
    elif len(schools)==1 and len(years) in {2,3,4} and not ids and (rt._OVERALL_SCOPE_RE.search(text) or dim):
        if re.search(r'因果|造成|导致|显著|预测|同一批',inference_text(text)): return None
        nodes=[add('school_score',schools[0],year,dim) for year in years]
        if len(nodes)==2:
            difference(nodes,reverse=not bool(re.search(r'前(?:者|年|项).{0,8}减.{0,8}后',text)))
        from packages.semantic_query.regional_capabilities import TEMPORAL_NOTE
        plan['temporal_note']=TEMPORAL_NOTE+'单校按各年同名年度登记的模板分分别报告。'
        if len(years)>2:
            from packages.semantic_query.regional_capabilities import TEMPORAL_NOTE
            plan['temporal_note']=TEMPORAL_NOTE+'单校按各年同名年度登记的模板分分别报告，不据此认定跨年身份或固定队列。'
    elif len(schools)==2 and len(years)==1 and len(ids)==1:
        item=catalogue[ids[0]]
        if view=='raw_value' and item['coding_status']=='source_missing': return None
        nodes=[add('school_fact',school,years[0],qid=ids[0],statistic=None,aggregation='none') for school in schools]
        difference(nodes,reverse=bool(re.search(r'后(?:者|校|项).{0,8}减.{0,8}前',text)))
    elif len(schools)==1 and len(years)==1 and len(ids)>=2:
        # Existing school item arithmetic owns cross-item raw scale rules.
        if view == 'raw_value' and (rt._comparison_difference_requested(question) or re.search(r'差多少|相差|差值|差几|相隔.{0,6}分|减|往上还是往下|变化', text)): return None
        nodes=[add('school_fact',schools[0],years[0],qid=qid,statistic=None,aggregation='none') for qid in ids]
        if len(nodes)==2: difference(nodes,reverse=bool(re.search(r'后(?:者|项).{0,8}减.{0,8}前',text)))
        elif rt._comparison_difference_requested(question): return None
    elif len(schools)==1 and len(years)==1 and not ids and dim and candidate.get('intent') == 'school_eval' and re.search(r'(?:最低|最小|最高|最大).{0,10}(?:题项|题目|各题|各项)|(?:题项|题目|各题|各项).{0,10}(?:最低|最小|最高|最大)',text):
        from packages.semantic_query.semantic_preflight import derive_requirements
        extrema=derive_requirements(question,list(catalogue.values()))['extrema']
        if len(extrema)!=1:return None
        add('school_score',schools[0],years[0],dim)
        add('school_item_extremum',schools[0],years[0],dim,statistic=extrema[0]['operator'],aggregation='none')
        plan['item_selection']={'ties':'all','basis':'unrounded_template_item_score','missing':'exclude_null_keep_zero'}
    elif len(schools)==1 and len(years)==1 and not ids and dim and re.search(r'本校.{0,12}(?:六|6|各).{0,12}(?:方面|维度)|六个方面|六维',text) and re.search(r'排第几|名次|排位|排名',text):
        add('school_dimension_rank',schools[0],years[0],dim)
    elif not schools and len(regions)==1 and len(years)==1:
        region=regions[0];year=years[0]
        rank,top=rt._explicit_ranking_target(text)
        m=re.search(r'哪([一二两三四五六七八九十\d]+)所学校',text)
        if m and re.search(r'最多|最高|最少|最低',text):
            from packages.semantic_query.catalogue import _question_number
            top=_question_number(m[1])
        ranking=rank is not None or top is not None
        if len(ids)==1 and ranking:
            if view=='raw_value' and catalogue[ids[0]]['coding_status']=='source_missing': return None
            if top is not None and not 1<=top<=100: return None
            node=add('region_item_rank',region,year,qid=ids[0],statistic=None,aggregation='none',school_filter=school_filter)
            plan['selection']={'kind':'rank','rank':rank,'top_n':top,'descending':not bool(re.search(r'最少|最低|从低到高|升序',text))}
        elif len(ids)==1 and (view=='raw_value' or rt._explicit_item_statistic(text) in {'median','mean_and_median','max','min'}):
            if view=='raw_value' and catalogue[ids[0]]['coding_status']=='source_missing': return None
            stats=requested_statistics(text)
            for stat in stats: add('region_value',region,year,qid=ids[0],statistic=stat,school_filter=school_filter)
            if len(stats)==2: difference([q['id'] for q in plan['queries']],reverse=bool(re.search(r'后者.{0,8}减.{0,8}前者',text)))
        elif not ids and re.search(r'最高|最大',text) and re.search(r'最低|最小',text):
            nodes=[add('region_value',region,year,dim,statistic=stat,school_filter=school_filter) for stat in ['max','min']]
            difference(nodes,reverse=bool(re.search(r'最低.{0,8}减.{0,8}最高|后者.{0,8}减.{0,8}前者',text)))
        elif not ids and re.search(r'每所学校|每校|学校等权',text) and re.search(r'每条|作答池化',text):
            nodes=[add('region_value',region,year,dim,aggregation=agg,school_filter=school_filter) for agg in ['school_equal_mean','answer_pooled_mean']]
            if text.find('每条')>=0 and text.find('每条')<min([text.find(x) for x in ['每所学校','每校','学校等权'] if x in text]):
                plan['queries'].reverse();nodes.reverse()
            difference(nodes,reverse=bool(re.search(r'后者.{0,8}减.{0,8}前者',text)))
        elif not ids and re.search(r'乡村|农村',text) and re.search(r'城区|城镇|城市',text) and re.search(r'各.{0,8}多少所|两组.*学校数',text):
            area = re.search(r'城区|城镇|城市',text).group()
            filters=['乡村',area]
            school_types=list(dict.fromkeys(re.findall(r'小学|初中|高中|幼儿园',text)))
            if school_types:filters=[f+' '+school_types[0] for f in filters]
            if min(text.find(x) for x in ['城区','城镇','城市'] if x in text)<min(text.find(x) for x in ['乡村','农村'] if x in text): filters.reverse()
            for f in filters:add('region_value',region,year,dim,statistic='count',school_filter=f)
        elif school_filter and len(ids) <= 1 and not ranking and (
            rt._explicit_item_statistic(text) in {'median', 'mean_and_median'} or
            re.search(r'乡村|农村|城区|城镇|城市|镇区', school_filter) and re.search(r'小学|初中|高中|幼儿园|中职|一贯制', school_filter)):
            stats = requested_statistics(text)
            if rt._explicit_region_school_count_only(text): stats = ['count']
            for stat in stats:
                add('region_value',region,year,dim,qid=ids[0] if ids else None,statistic=stat,school_filter=school_filter)
        else: return None
    else: return None
    if not plan['queries']: return None
    # Explicit non-supported composite requests are never flattened to this plan.
    if candidate.get('compound_key') or candidate.get('school_field'): return None
    if view=='normalized_score' and any(q['question_id'] and catalogue[q['question_id']]['value_type']=='ratio' for q in plan['queries']):
        plan['measurement_note']='这些数值是登记题项归一值转换的百分制标准分；分差不是教师实际比例差。'
    try:
        ordered=typed_plan.explicit_subtraction_operands(plan,question,rt)
    except ValueError:
        return {'intent':'clarify','reason':'explicit_comparison_operands_not_unique',
                'missing':['请明确唯一的减法顺序。'],'original':question}
    if ordered is not None:
        for step in plan['steps']:
            if step['operation']=='subtract' and set(step['args'])==set(ordered):
                step['args']=list(ordered)
    typed_plan.check_explicit_direction(plan,question,rt)
    plan=typed_plan.bind_types(plan,catalogue)[0]
    return {'intent':'public_task','public_plan':plan,'original':question,'year':years[0],
            'value_view':view,'target_dimension':dim,'requested_outputs':['direct_answer']+(['charts'] if chart else [])}

def requested_statistics(text):
    patterns={'mean':r'平均|均值|均分','median':r'中位数','max':r'最高|最大','min':r'最低|最小'}
    matches=[(re.search(p,text).start(),s) for s,p in patterns.items() if re.search(p,text)]
    return [s for _,s in sorted(matches)] or ['mean']

def answer_spec(routed):
    plan=routed['public_plan']
    from packages.semantic_query import runtime as semantic_runtime
    typed_plan.check_explicit_direction(plan,routed.get('original',''),semantic_runtime)
    statistics=[q['statistic'] for q in plan['queries'] if q['statistic']]
    if plan.get('include_school_count'):statistics.append('school_count')
    capability=plan.get('capability') or {}
    if capability.get('ranking_ids') and (capability.get('mode') in {'region_profile','region_position','province_ranking'} or capability.get('city_list')):
        statistics.extend(['rank','ranking_population'])
    return {'version':'answer-spec-public-composition-v1','operation':'public_task','granularity':'public_task',
        'public_plan':copy.deepcopy(plan),'plan_fingerprint':fingerprint(plan),'chart_requested':plan['chart_requested'],
        'required_outputs':list(routed['requested_outputs']),'answer_mode':'direct','include_dimensions':False,
        'requested_result_statistics':statistics,
        'value_view':routed['value_view'],'prohibited_outputs':['unrequested_dimensions','generic_recommendations'],
        'topics':list(dict.fromkeys(q['topic'] for q in plan['queries'] if q.get('topic'))),'allowed_topics':[], 'style':'compact','prohibit_unsupported_causal_claims':True}

def unique_school(connection,entity,year):
    from scripts.school_engine import find_schools,expand_school_alias,get_school
    hits=find_schools(entity,year=year,limit=100,connection=connection)
    from packages.core.school_names import normalize_school_numbers
    key=normalize_school_numbers(expand_school_alias(entity))
    exact=[x for x in hits if normalize_school_numbers(expand_school_alias(x['school_name']))==key]
    selected=exact or hits
    if len(selected)!=1: raise ValueError('public_task_ambiguous_school' if selected else 'public_task_unavailable_school_year')
    return get_school(selected[0]['id'],connection=connection)

def execute(routed, connection=None, *, verify_execution=False):
    if routed.get('public_plan', {}).get('capability'):
        from packages.agent.tools.registry import get_default_registry
        return get_default_registry().get('regional_capability').execute(routed, connection=connection, verify_execution=verify_execution).data
    return _execute(routed, connection=connection, verify_execution=verify_execution)


def _execute(routed, connection=None, *, verify_execution=False):
    from scripts.school_engine import connect,evaluate_school
    from packages.semantic_query.topic_tasks import label as topic_label
    from scripts.fact_engine import query_school_fact
    from scripts.region_engine import evaluate_region
    plan=routed['public_plan']
    if any(s.get('operation')!='subtract' for s in plan.get('steps',[])):
        raise ValueError('public_task_unsupported_operation')
    owned=connection is None;conn=connection or connect()
    _,types=typed_plan.bind_types(plan,{x['id']:x for x in load_semantic_catalogue()['items']},emit=False)
    nodes=[];trace=[]
    try:
        if owned: conn.execute('BEGIN')
        from packages.semantic_query.regional_capabilities import profile_values
        batched=profile_values(plan,conn)
        for q in plan['queries']:
            q=copy.deepcopy(q)
            if q['kind'].startswith('region_'):
                from packages.semantic_query.regional_capabilities import bind_region as bind_capability_region
                q['region_identity']=(bind_capability_region(q,conn) if plan.get('capability') else typed_plan.bind_region(q,conn,routed.get('original')))
            typ=types[q['id']]
            unit='分';rank=None;v=None
            if q.get('availability') == 'UNCOLLECTED':
                from packages.core.item_collection import ITEM_NOTE
                nodes.append(dict(id=q['id'],label=q['entity']+'/'+q['year']+'/Q'+str(q['question_id']), value=None,unit=typ['unit'],status='UNAVAILABLE',message=ITEM_NOTE,scope=copy.deepcopy(q)))
                trace.append(dict(query=copy.deepcopy(q),raw_result={'business_status':'UNAVAILABLE','message':ITEM_NOTE},school_id=None))
                continue
            if q['kind'].startswith('school_'):
                school=unique_school(conn,q['entity'],q['year'])
                if q['kind']=='school_fact':
                    metric=question_metric(q['question_id']);metric['value_view']=q['view']
                    raw=query_school_fact(school['id'],metric,year=q['year'],connection=conn)
                    v=raw.get('normalized_score') if q['view']=='normalized_score' else raw.get('target_value')
                    unit='分' if q['view']=='normalized_score' else metric['unit']
                elif q['kind']=='school_item_extremum':
                    from scripts.school_engine import load_school_template
                    template=load_school_template()
                    metrics=next((d['metrics'] for d in template['dimensions'] if d['name']==q['dimension']),[])
                    if not metrics:raise ValueError('item_selection_dimension_membership_missing')
                    names={int(m['id']):m.get('content') or f"Q{m['id']}" for m in metrics}
                    rows=conn.execute('SELECT a.question_id,a.normalized_value FROM answers a JOIN schools s ON s.id=a.school_id WHERE a.school_id=? AND s.year=?', (school['id'],q['year'])).fetchall()
                    population=[];seen=set()
                    for row in rows:
                        qid=int(row['question_id'])
                        if qid not in names:continue
                        if qid in seen:raise ValueError('item_selection_duplicate_answer')
                        seen.add(qid)
                        if row['normalized_value'] is None:continue
                        nv=float(row['normalized_value'])
                        if not math.isfinite(nv):raise ValueError('item_selection_nonfinite')
                        population.append({'question_id':qid,'label':names[qid],'normalized_value':nv,
                                           'unrounded_score':max(0.0,min(100.0,nv*100.0))})
                    best=(min if q['statistic']=='min' else max)((x['unrounded_score'] for x in population),default=None)
                    v=[dict(x,score=round(x['unrounded_score'],2)) for x in sorted(population,key=lambda x:x['question_id']) if x['unrounded_score']==best] or None
                    raw={'population':population,'selection_basis':'unrounded_template_item_score','all_ties':True}
                else:
                    raw=evaluate_school(school['id'],connection=conn,target_dimension=None if q['kind']=='school_dimension_rank' else q['dimension'],topic=q.get('topic'))
                    if q['kind']=='school_dimension_rank':
                        dims=raw.get('dimensions') or [];row=next((x for x in dims if x['dimension']==q['dimension']),{})
                        v=row.get('score')
                        from packages.core.item_collection import enabled as r11_enabled
                        if not r11_enabled() and (len(dims)!=6 or any(x.get('score') is None for x in dims)):
                            raise ValueError('public_task_incomplete_dimension_ranking')
                        rank=1+sum(x['score']>v for x in dims if x.get('score') is not None) if v is not None else None
                    else:v=raw.get('topic_score') if q.get('topic') else raw.get('target_dimension_score') if q['dimension'] else raw.get('total_score')
                entity=school['school_name']
            else:
                ids=q.get('indicator_ids') or ([q['question_id']] if q['question_id'] else None)
                raw=batched[q['id']] if batched is not None else evaluate_region(q['entity'],year=q['year'],target_dimension=q['dimension'],target_stat=q['statistic'],
                    aggregation=q['aggregation'],indicator_ids=ids,value_view='normalized_score' if q['view']=='school_count' else q['view'],school_filter=q['school_filter'],
                    connection=conn,region_identity=q.get('region_identity'),topic=q.get('topic'),operation='region_ranking' if q['kind']=='region_item_rank' else 'region_evaluation',
                    target_rank=(plan.get('selection') or {}).get('rank'),target_top_n=(plan.get('selection') or {}).get('top_n'))
                empty=typed_plan.no_data_result(raw)
                entity=raw.get('region_name') or q['entity']
                unit=question_metric(q['question_id'])['unit'] if q['question_id'] and q['view']=='raw_value' else '所' if q['statistic']=='count' else '分'
                if empty:
                    v=None
                elif q['kind']=='region_item_rank':
                    # The common tool emits selected rows from the exact selected item population.
                    rows=raw['ranking_items'];v=rows
                    if not plan['selection']['descending']:
                        # Request the bottom bounded subset by reading the same raw item population.
                        from scripts.region_engine import detect_region
                        from packages.analysis_contract.service import _filter_sql
                        reg=typed_plan.region_sql(q['region_identity']);f,params=_filter_sql(q['school_filter'])
                        field='a.value' if q['view']=='raw_value' else 'a.normalized_value*100'
                        sql=f"SELECT s.id,s.school_name,AVG({field}) AS score FROM schools s JOIN answers a ON a.school_id=s.id WHERE ({reg['where_sql'].replace('city','s.city').replace('district','s.district').replace('province','s.province')}) AND s.year=? {f} AND a.question_id IN ({','.join('?' for _ in ids)}) AND {field} IS NOT NULL GROUP BY s.id ORDER BY score ASC,s.id ASC"
                        allrows=[dict(x) for x in conn.execute(sql,(*reg['where_params'],q['year'],*params,*ids))]
                        rows=[dict(x,rank=i+1,score=round(x['score'],2)) for i,x in enumerate(allrows)]
                        selected_rank=plan['selection']['rank']
                        if not plan['selection']['top_n'] and selected_rank!=-1 and not 1<=selected_rank<=len(rows):
                            raise ValueError('public_task_ranking_position_unavailable')
                        v=rows[:plan['selection']['top_n']] if plan['selection']['top_n'] else [rows[-1 if selected_rank==-1 else selected_rank-1]]
                    v=[{'school_id':r.get('school_id',r.get('id')),'school_name':r['school_name'],'rank':r['rank'],'score':r['score']} for r in v]
                else:
                    field={'mean':'target_dimension_score' if q['dimension'] else 'total_score','median':'median_score','max':'max_score','min':'min_score','count':'school_count'}[q['statistic']]
                    v=raw.get(field)
                if not empty and q['statistic']=='count' and q.get('dimension'):
                    v=raw.get('requested_school_count', raw.get('school_count'))
            typed_plan.no_data_result(raw)
            if isinstance(v,(int,float)) and not math.isfinite(v):raise ValueError('public_task_nonfinite_value')
            metric=topic_label(q['topic']) if q.get('topic') else question_metric(q['question_id'])['name'] if q['question_id'] else '有效学校数' if q['statistic']=='count' else q['dimension'] or ('学校综合分' if q['kind'].startswith('school_') else '整体题均分')
            label=f"{entity}/{q['year']}/{metric}"+('/'+q['school_filter'] if q['school_filter'] else '')
            if any(other['id']!=q['id'] and all(other.get(k)==q.get(k) for k in ('entity','year','question_id')) and other.get('view')!=q.get('view') for other in plan['queries']):
                label+='/'+{'raw_value':'原始值','normalized_score':'标准分'}[q['view']]
            if q['kind']=='region_value' and q['statistic']!='count':label+= '/'+{'mean':'均值','median':'中位数','max':'最高值','min':'最低值'}[q['statistic']]+('/学校等权' if q['aggregation']=='school_equal_mean' else '/作答池化')
            original_q=next(x for x in plan['queries'] if x['id']==q['id'])
            node=dict(id=q['id'],label=label,value=v,unit=unit,status='ANSWER' if v is not None else 'NO_DATA',scope=copy.deepcopy(original_q))
            if q['kind'].startswith('region_'):
                node['school_count']=raw.get('requested_school_count') if q.get('dimension') else raw.get('school_count',0)
            typed_plan.check_result_type(node,typ)
            if q.get('region_identity'):node['region_identity']=copy.deepcopy(q['region_identity'])
            if q.get('topic'):
                from packages.semantic_query.topic_tasks import NOTE
                node.update(measurement_note=NOTE,message=raw.get('message',''))
                if q['kind'].startswith('school_'):node['school_count']=raw.get('school_count',0)
                node['all_items_uncollected']=bool(raw.get('all_items_uncollected'))
                if node['all_items_uncollected']:node.pop('school_count',None)
            if rank is not None:node['rank']=rank
            nodes.append(node);trace.append({'query':copy.deepcopy(q),'school_id':school['id'] if q['kind'].startswith('school_') else None,'raw_result':raw})
        for step in plan['steps']:
            args=[next(x for x in nodes if x['id']==i) for i in step['args']]
            if len(args)!=2 or len({x['unit'] for x in args})!=1:raise ValueError('public_task_incompatible_operands')
            v=None if any(x['value'] is None for x in args) else round(args[0]['value']-args[1]['value'],2)
            nodes.append(dict(id=step['id'],label=f"{args[0]['label']} 减 {args[1]['label']}",value=v,unit=args[0]['unit'],status='ANSWER' if v is not None else 'NO_DATA',scope=copy.deepcopy(step)))
            typed_plan.check_result_type(nodes[-1],types[step['id']])
            nodes[-1]['comparison']=dict(object_node=step['args'][0],benchmark_node=step['args'][1],definition='A_MINUS_B')
        selection=plan.get('selection') or {}
        if selection.get('kind') in {'middle','all'}:
            if any(x['value'] is None for x in nodes):raise ValueError('public_task_incomplete_school_comparison')
            ordered=sorted(nodes,key=lambda x:(-x['value'] if selection['descending'] else x['value'],next(t['school_id'] for t in trace if t['query']['id']==x['id'])))
            rank=0;last=None
            for i,x in enumerate(ordered):
                if last is None or x['value']!=last:rank=i+1
                x['rank']=rank;last=x['value']
            if selection['kind']=='middle':
                middle=ordered[len(ordered)//2]
                nodes.append(dict(id='middle',label='所选学校排序的中间位置：'+middle['label'],value=middle['value'],unit=middle['unit'],status='ANSWER',scope={'source_id':middle['id']}))
        pieces=[]
        for n in nodes:
            if isinstance(n['value'],list) and n['scope']['kind']=='school_item_extremum':
                pieces.extend(f"{n['label']}/{'最低' if n['scope']['statistic']=='min' else '最高'}题项 Q{row['question_id']} {row['label']}：{row['score']}分" for row in n['value'])
            elif isinstance(n['value'],list):
                pieces.extend(f"第{r['rank']}名 {r['school_name']}：{r['score']}{n['unit']}" for r in n['value'])
            elif n.get('status')=='UNAVAILABLE':pieces.append(n['label']+'：'+n['message'])
            else:pieces.append(f"{n['label']}：{'无数据' if n['value'] is None else n['value']}{n['unit'] if n['value'] is not None else ''}"+(f"，第{n['rank']}名（同分并列）" if n.get('rank') else ''))
            if plan.get('include_school_count') and n.get('school_count') is not None and n['scope'].get('statistic') != 'count':
                pieces.append(n['label']+'：有效学校'+str(n['school_count'])+'所')
        primary=nodes[:len(plan['queries'])]
        if len(primary)==2 and all(isinstance(n['value'],(int,float)) for n in primary) and len({(n['unit'],n['scope']['view']) for n in primary})==1:
            a,b=primary
            if a['value']==b['value']:pieces.append('所问两项数值相同')
            else:pieces.append(('所问有效学校数更多的是' if a['unit']=='所' else '所问数值较高的是')+(a['label'] if a['value']>b['value'] else b['label']))
        if plan.get('temporal_note') and len(plan['queries'])==2:
            first,second=nodes[:2]
            if first['value'] is not None and second['value'] is not None:
                change=round(second['value']-first['value'],2)
                trend='上升' if change>0 else '下降' if change<0 else '持平'
                pieces.append(f"{second['scope']['year']}相对{first['scope']['year']}{trend}{abs(change)}分（后列年度减先列年度）")
        from packages.semantic_query.regional_capabilities import decorate
        capability_answer=decorate(nodes,trace,plan)
        result={'object_type':'structured_task','analysis_operation':'public_task','task_outputs':nodes,
            'direct_answer':'；'.join(pieces)+'。'+plan.get('temporal_note','')+plan.get('measurement_note',''),'task_analysis':plan.get('temporal_note','')+plan.get('measurement_note',''),
            'plan_fingerprint':fingerprint(plan),'public_plan':copy.deepcopy(plan),'execution_trace':trace,
            'fact_data_status':'ANSWER' if any(n['status']=='ANSWER' for n in nodes) else 'NO_DATA'}
        from packages.core.school_filters import filter_notes
        cohort_notes = list(dict.fromkeys(n for q in plan['queries'] for n in filter_notes(q['year'], q.get('school_filter'))))
        if cohort_notes:
            result['direct_answer'] += ' '.join(cohort_notes)
            result['program_notices'] = cohort_notes
        if plan.get('temporal_note') and len(plan['queries'])>2:
            result['program_notices']=[plan['temporal_note']]
        if capability_answer:
            result['direct_answer']=capability_answer
            result['task_analysis']=' '.join(plan['capability']['notes'])
            result['program_notices']=list(plan['capability']['notes'])
            cap=plan['capability']
            core=cap['ranking_ids'] if cap['mode']=='province_ranking' else cap['target_ids']
            result['fact_data_status']='ANSWER' if any(n['id'] in core and n['value'] is not None for n in nodes) else 'NO_DATA'
        if any(q.get('topic') for q in plan['queries']):
            from packages.semantic_query.topic_tasks import NOTE
            result['program_notices']=list(dict.fromkeys([*(result.get('program_notices') or []),NOTE]))
            if NOTE not in result['direct_answer']:result['direct_answer']+=' '+NOTE
            for n in nodes:
                if n.get('message') and n['message'] not in result['direct_answer']:result['direct_answer']+=' '+n['message']
        from packages.semantic_query.regional_capabilities import profile_elements
        result['profile_required_elements']=profile_elements(nodes,plan)
        result['public_execution_verification']=verify(result,conn) if verify_execution else {'status':'NOT_APPLICABLE_UNCONSTRAINED','checks':[],'failures':[]}
        if result['public_execution_verification']['status']=='FAIL':raise ValueError('public_task_execution_verification_failed')
        return result
    finally:
        if owned:conn.close()

def project(result,spec):
    if result.get('plan_fingerprint')!=spec.get('plan_fingerprint') or result.get('public_plan')!=spec.get('public_plan'):raise ValueError('public_projection_plan_mismatch')
    allowed={'profile_required_elements','program_notices','object_type','analysis_operation','task_outputs','task_analysis','direct_answer','plan_fingerprint','public_plan','fact_data_status','public_execution_verification','_id'}
    public={k:copy.deepcopy(v) for k,v in result.items() if k in allowed}
    from packages.semantic_query.regional_capabilities import visible_ids
    visible=visible_ids(result['public_plan'], public['task_outputs'])
    public['task_outputs']=[n for n in public['task_outputs'] if n['id'] in visible]
    if result['public_plan'].get('capability',{}).get('ranking_ids'):
        public['task_outputs'].sort(key=lambda n:(n.get('rank') or 10000,n['id']))
    public['answer_scope']={k:copy.deepcopy(v) for k,v in spec.items() if k!='public_plan'}
    from packages.semantic_query.unit_delivery import policy, notice
    unit_policy=policy(public,spec)
    if unit_policy:
        for node in public['task_outputs']:
            if node['id'] in {n['id'] for n in unit_policy['raw_nodes']} or unit_policy['same_field_arithmetic']:
                node['unit_display']='原字段登记值；单位含义待核'
            if node['id'] in unit_policy['deferred_nodes']:
                node['delivery_unresolved']=True
        public['task_analysis']=' '.join([public.get('task_analysis') or '',notice(unit_policy)]).strip()
    return public

def verify(result,connection):
    """G2/existing rule checks: raw-row replay, never called by G1.

    Reuse the established independent replay implementation; no result-cache replay.
    Composite tasks do not acquire production revisions or trusted-focus state.
    """
    from types import SimpleNamespace
    from packages.analysis_contract.service import ContractService,_school_evaluation_template_spec
    plan=result['public_plan'];failures=[];expected={};school_ids={};raw_expected={}
    catalogue=load_semantic_catalogue()['items']
    actual={n['id']:n for n in result['task_outputs']}
    if len(actual)!=len(result['task_outputs']):failures.append('duplicate_output_id')
    if result.get('plan_fingerprint')!=fingerprint(plan):failures.append('plan_fingerprint')
    for q in plan['queries']:
        rank=None
        if q['kind'].startswith('school_'):
            school=unique_school(connection,q['entity'],q['year']);school_ids[q['id']]=school['id']
            if q['kind']=='school_fact':
                row=connection.execute('SELECT value,normalized_value FROM answers WHERE school_id=? AND question_id=?',(school['id'],q['question_id'])).fetchone()
                val=row['value' if q['view']=='raw_value' else 'normalized_value'] if row else None
                if val is not None and q['view']=='normalized_score':val=round(val*100,1)
            elif q.get('topic'):
                ids=q['indicator_ids']
                row=connection.execute('SELECT AVG(normalized_value)*100 FROM answers WHERE school_id=? AND question_id IN ('+','.join('?' for _ in ids)+') AND normalized_value IS NOT NULL',(school['id'],*ids)).fetchone()
                val=round(row[0],2) if row[0] is not None else None
            elif q['kind']=='school_item_extremum':
                # Independent replay rebuilds population and rejects omitted/extra ties.
                from scripts.school_engine import load_school_template
                members={int(m['id']):m.get('content') or f"Q{m['id']}" for d in load_school_template()['dimensions'] if d['name']==q['dimension'] for m in d['metrics']}
                rows=connection.execute('SELECT a.question_id,a.normalized_value FROM answers a JOIN schools s ON s.id=a.school_id WHERE a.school_id=? AND s.year=?',(school['id'],q['year'])).fetchall()
                numbers={};duplicate=False
                for row in rows:
                    key=int(row['question_id'])
                    if key not in members:continue
                    if key in numbers:duplicate=True
                    numbers[key]=row['normalized_value']
                if duplicate:failures.append('item_selection_duplicate_answer')
                valid={key:float(number) for key,number in numbers.items() if number is not None}
                if any(not math.isfinite(number) for number in valid.values()):raise ValueError('item_replay_nonfinite')
                scores={key:max(0.,min(100.,number*100.)) for key,number in valid.items()}
                ordered=sorted(scores,key=lambda key:(scores[key] if q['statistic']=='min' else -scores[key],key))
                tied=[key for key in ordered if scores[key]==scores[ordered[0]]] if ordered else []
                val=[dict(question_id=key,label=members[key],normalized_value=valid[key],unrounded_score=scores[key],score=round(scores[key],2)) for key in sorted(tied)] or None
            else:
                _,_,ids,ms=_school_evaluation_template_spec(None if q['kind']=='school_dimension_rank' else q['dimension'])
                c=SimpleNamespace(period=q['year'],secondary_period=None,secondary_school_id=None,indicator_ids=ids,metric_spec=ms)
                replay=ContractService._independent_school_compare_side(None,c,school_id=school['id'],school_name=school['school_name'],connection=connection)
                val=replay['dimensions'].get(q['dimension']) if q['kind']=='school_dimension_rank' else replay['total_score']
                if q['kind']=='school_dimension_rank':rank=1+sum(v>val for v in replay['dimensions'].values() if v is not None) if val is not None else None
        else:
            ids=tuple(q.get('indicator_ids') or ([q['question_id']] if q['question_id'] else [x['id'] for x in catalogue if not q['dimension'] or x['dimension']==q['dimension']]))
            from packages.semantic_query.regional_capabilities import bind_region as bind_capability_region
            bound_identity=(bind_capability_region(q,connection) if plan.get('capability') else q.get('region_identity'))
            c=SimpleNamespace(region_keyword=q['entity'],region_identity=bound_identity,school_filter=q['school_filter'],indicator_ids=ids,
                period=q['year'],value_view='normalized_score' if q['view']=='school_count' else q['view'],metric_type='question' if q['question_id'] else 'school_template',aggregation=q['aggregation'])
            replay=ContractService._independent_replay(None,c,connection=connection)
            if q['kind']=='region_item_rank':
                rows=replay['ranked_schools']
                if not plan['selection']['descending']:rows=sorted(rows,key=lambda x:(x[1],x[0]))
                selected=[dict(school_id=sid,school_name=connection.execute('SELECT school_name FROM schools WHERE id=?',(sid,)).fetchone()[0],rank=i+1,score=round(score,2)) for i,(sid,score) in enumerate(rows)]
                sel=plan['selection'];val=(None if q.get('topic') and not selected else selected[:sel['top_n']] if sel['top_n'] else [selected[sel['rank']-1 if sel['rank']!=-1 else -1]])
            else:val=replay[{'mean':'total_score','median':'median_score','max':'max_score','min':'min_score','count':'school_count'}[q['statistic']]]
            raw_expected[q['id']]=val
            expected_count=replay['school_count']
            if q.get('topic'):
                from packages.core.item_collection import collected_ids
                if not collected_ids(q['year'],q['indicator_ids']):expected_count=None
            if plan.get('capability') and (actual.get(q['id'],{}).get('school_count')!=expected_count or actual.get(q['id'],{}).get('region_identity')!=bound_identity):
                failures.append('capability_population_binding:'+q['id'])
            if isinstance(val,(int,float)):val=round(val,2)
        expected[q['id']]=val
        node=actual.get(q['id'],{})
        if q.get('topic'):
            from packages.core.item_collection import collected_ids
            uncollected=not collected_ids(q['year'],q['indicator_ids'])
            if node.get('all_items_uncollected')!=uncollected or uncollected and 'school_count' in node:
                failures.append('topic_collection_binding:'+q['id'])
        equal=(node.get('value')==val if not isinstance(val,(int,float)) else isinstance(node.get('value'),(int,float)) and math.isclose(node['value'],val,abs_tol=.005))
        expected_unit=question_metric(q['question_id'])['unit'] if q['question_id'] and q['view']=='raw_value' else '所' if q['statistic']=='count' else '分'
        if not equal or node.get('scope')!=q or node.get('unit')!=expected_unit or node.get('rank')!=rank and q['kind']=='school_dimension_rank':failures.append('atom_binding_or_value:'+q['id'])
    for step in plan['steps']:
        a,b=(expected[x] for x in step['args']);val=None if a is None or b is None else round(a-b,2)
        if actual.get(step['id'],{}).get('value')!=val or actual.get(step['id'],{}).get('scope')!=step or actual.get(step['id'],{}).get('unit')!=actual[step['args'][0]].get('unit'):failures.append('ordered_step:'+step['id'])
        expected[step['id']]=val
    sel=plan.get('selection') or {}
    if sel.get('kind') in {'all','middle'}:
        ordered=sorted(plan['queries'],key=lambda q:(-expected[q['id']] if sel['descending'] else expected[q['id']],school_ids[q['id']]))
        for q in ordered:
            value=expected[q['id']];rank=1+sum(v>value if sel['descending'] else v<value for v in expected.values())
            if actual[q['id']].get('rank')!=rank:failures.append('selected_school_rank:'+q['id'])
        if sel['kind']=='middle':
            source=ordered[len(ordered)//2]['id']
            if actual.get('middle',{}).get('scope')!={'source_id':source} or actual.get('middle',{}).get('value')!=expected[source]:failures.append('middle_school')
    cap=plan.get('capability')
    if cap:
        population=[i for i in cap['ranking_ids'] if raw_expected.get(i) is not None]
        for i in population:
            rank=1+sum(raw_expected[j]>raw_expected[i] if cap['descending'] else raw_expected[j]<raw_expected[i] for j in population)
            if actual[i].get('rank')!=rank:failures.append('capability_rank:'+i)
    expected_ids=set(expected)|({'middle'} if sel.get('kind')=='middle' else set())
    if set(actual)!=expected_ids:failures.append('output_node_set')
    return {'status':'FAIL' if failures else 'PASS','checks':[{'name':'raw_row_independent_replay','status':'FAIL' if failures else 'PASS'}],'failures':failures,'revision_semantics':'NOT_APPLICABLE_PUBLIC_COMPOSITION'}

def chart_code(result,spec):
    """Chart only actual requested nodes, using labels/units bound to each node."""
    if not spec or not spec.get('chart_requested'):return ''
    result=project(result,spec)
    rows=[];title='所问对象与统计量'
    for n in result['task_outputs']:
        if n['id']=='middle' or n['id'].startswith('d'):continue
        if isinstance(n['value'],list) and n['scope']['kind']=='school_item_extremum':
            title=n['label']+' · 题项得分'
            rows.extend({'label':f"Q{x['question_id']} {x['label']}",'value':x['score'],'unit':n['unit']} for x in n['value'])
        elif isinstance(n['value'],list):
            title=n['label']+(' · 登记原值排名' if n['scope']['view']=='raw_value' else ' · 标准分排名')
            rows.extend({'label':f"第{r['rank']}名 {r['school_name']}",'value':r['score'],'unit':n['unit']} for r in n['value'])
        elif n['value'] is not None:rows.append({'label':n['label'],'value':n['value'],'unit':n.get('unit_display') or n['unit']})
    return bar_code(rows,title)

def bar_code(rows,title):
    if not rows or len({r['unit'] for r in rows})!=1 or any(r['value'] is None for r in rows):return ''
    return '''import matplotlib.pyplot as plt
import json,textwrap
rows=json.loads(%r)
fig,ax=plt.subplots(figsize=(max(7,len(rows)*1.7),4.8),dpi=160)
bars=ax.bar(range(len(rows)),[r['value'] for r in rows],color='#357a91')
if all(r['value']>=0 for r in rows):ax.set_ylim(0,max(r['value'] for r in rows)*1.18 or 1)
if rows[0]['unit']=='所':
    from matplotlib.ticker import MaxNLocator
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
ax.set_xticks(range(len(rows)),['\\n'.join(textwrap.wrap(r['label'],width=18)) for r in rows],ha='center',fontsize=9)
ax.set_ylabel('数值（'+rows[0]['unit']+'）')
ax.set_title('\\n'.join(textwrap.wrap(%r,width=38)),fontsize=11)
for b,r in zip(bars,rows):ax.annotate(str(r['value'])+r['unit'],(b.get_x()+b.get_width()/2,b.get_height()),xytext=(0,4),textcoords='offset points',ha='center')
fig.tight_layout()
''' % (json.dumps(rows,ensure_ascii=False),title)
