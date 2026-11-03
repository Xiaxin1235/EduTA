"""Registered secondary dimensions, using existing public query/region tools."""
import copy,re
from contextlib import closing
from packages.semantic_query.catalogue import topic_definition

NOTE='二级维度得分为该二级维度所含题项得分的均值，评估方案未单独定义此分数。'
INTENTS=frozenset({'region_eval','region_compare','region_multi_compare','region_longitudinal',
    'region_series','national_eval','province_ranking','national_ranking','region_ranking','school_eval','school_compare'})


def separately_requested(question):
    return bool(re.search(r'分别|逐项|每一项|各(?:自)?(?:的)?(?:得|有|是|为|多少|几分)|各项',question))


def include_dimension_topics(question,routed,candidate,model,rt):
    """Retain explicitly separate sibling scores using the existing two tools."""
    from packages.semantic_query.catalogue import topic_scope,topic_catalogue
    from packages.semantic_query import typed_plan
    parent=candidate.get('dimension')
    if not parent or not separately_requested(rt._scope_assertion_text(question)) or routed.get('intent') in {'clarify','unavailable'}:
        return routed
    siblings={t['name'] for t in topic_catalogue() if t['dimension']==parent}
    listed=list(dict.fromkeys(model.get('topics') or (topic_scope(rt._scope_assertion_text(question)) or {}).get('listed_topics') or []))
    if not siblings or set(listed)!=siblings:return routed
    from packages.core.research_data import bind_public_constraints
    routed=bind_public_constraints(question,routed)
    spec=rt._build_answer_spec(question,routed)
    plan=typed_plan.bind_routed_plan(routed,spec,rt._catalogue()[1])
    if not plan:return routed
    filters={q.get('school_filter') for q in plan['queries']}
    if len(filters)!=1:
        return dict(intent='clarify',original=question,reason='topic_scope_not_supported',missing=['逐项给出二级维度时，请使用同一学校筛选范围。'])
    c=dict(candidate,dimension=None,topics=list(listed),school_filter=next(iter(filters)))
    if c['intent']=='region_profile':c['intent']='region_eval'
    topics=compile_topic_task(question,c,rt)
    if topics['intent']=='clarify':return topics
    plan=copy.deepcopy(plan)
    for node in topics['public_plan']['queries']:
        node=copy.deepcopy(node);node['id']='v'+str(len(plan['queries'])+1)
        plan['queries'].append(node)
    plan['include_school_count']=True
    if plan.get('capability'):
        plan['capability']['target_ids'].extend(q['id'] for q in plan['queries'] if q.get('topic'))
    plan=typed_plan.bind_types(plan,rt._catalogue()[1])[0]
    return dict(routed,intent='public_task',public_plan=plan,
                requested_outputs=list(routed.get('requested_outputs') or ['direct_answer']),value_view='normalized_score')

def label(name):
    t=topic_definition(name)
    return f"二级维度：{name}（属{t['dimension']}，{len(t['indicator_ids'])}个题项）"

def school_score(school_id,name,connection=None):
    from scripts.school_engine import connect,get_school
    from packages.core.item_collection import collected_ids
    own=connection is None;conn=connection or connect()
    try:
        school=get_school(school_id,connection=conn)
        ids=topic_definition(name)['indicator_ids']
        row=conn.execute('SELECT AVG(normalized_value)*100,COUNT(normalized_value) FROM answers WHERE school_id=? AND question_id IN ('+','.join('?' for _ in ids)+')', (school_id,*ids)).fetchone()
        value=round(row[0],2) if row[0] is not None else None
        missing=not collected_ids(school['year'],ids)
        message='该二级维度所含题项当年全部未采集。' if missing else '没有有效作答。' if value is None else ''
        return dict(topic=name,topic_score=value,school_count=int(value is not None),answer_count=row[1],
            fact_data_status='NO_DATA' if value is None else 'ANSWER',message=message,
            direct_answer=label(name)+'：'+('无数据。'+message if value is None else str(value)+'分。')+NOTE,
            indicator_ids=ids,measurement_note=NOTE,all_items_uncollected=missing,school_name=school['school_name'],year=school['year'])
    finally:
        if own:conn.close()

def compile_topic_task(question,candidate,rt):
    from packages.semantic_query.task_draft import supported_scope
    from packages.semantic_query import typed_plan
    from packages.semantic_query.regional_capabilities import PROVINCES,TEMPORAL_NOTE
    from packages.semantic_query.catalogue import load_semantic_catalogue
    c=candidate;topics=c.get('topics') or [];intent=c.get('intent')
    if intent not in INTENTS or not supported_scope(intent,c.get('schools') or [],c.get('regions') or [],c.get('periods') or [],topics=topics):
        return dict(intent='clarify',original=question,reason='topic_scope_not_supported',missing=['请限定已登记的二级维度及相容对象、年份；多个二级维度只支持同一年。'])
    if c.get('value_view') not in (None,'normalized_score') or c.get('statistic') not in (None,'mean') or c.get('aggregation') not in (None,'school_equal_mean'):
        return dict(intent='clarify',original=question,reason='topic_normalized_mean_only',missing=['二级维度只提供归一化得分的题项均值；原值及其他统计请指定题项。'])
    regions=c.get('regions') or [];schools=c.get('schools') or [];years=c['periods']
    plan=dict(queries=[],steps=[],selection=None,chart_requested='charts' in (c.get('requested_outputs') or []),include_school_count=True,measurement_note=NOTE)
    def add(entity,year,name,kind):
        node=dict(id='v'+str(len(plan['queries'])+1),kind=kind,entity=entity,year=str(year),dimension=None,topic=name,
            question_id=None,indicator_ids=list(topic_definition(name)['indicator_ids']),view='normalized_score',statistic='mean',aggregation='school_equal_mean',school_filter=c.get('school_filter'))
        plan['queries'].append(node);return node['id']
    ranking=intent in {'national_ranking','province_ranking'}
    school_ranking=intent=='region_ranking'
    entities=(PROVINCES if ranking else schools or regions or ['全国'])
    for entity in entities:
        for year in years:
            for name in topics:add(entity,year,name,'school_score' if schools else 'region_item_rank' if school_ranking else 'region_value')
    if school_ranking:
        if len(topics)!=1 or len(years)!=1 or len(regions)!=1:
            return dict(intent='clarify',original=question,reason='topic_ranking_scope',missing=['学校排名请指定一个二级维度、一个地区和一年。'])
        rank,top=rt._explicit_ranking_target(question)
        rank=c.get('target_rank') or rank;top=c.get('target_top_n') or top
        if rank is None and top is None:
            return dict(intent='clarify',original=question,reason='ranking_target_required',missing=['请指定名次或前N所学校。'])
        plan['selection']=dict(kind='rank',rank=rank,top_n=top,descending=c.get('ranking_order')!='bottom')
    if ranking:
        if len(topics)!=1 or len(years)!=1:
            return dict(intent='clarify',original=question,reason='topic_ranking_scope',missing=['省级排名请指定一个二级维度和一年。'])
        plan['capability']=dict(mode='province_ranking',target_ids=[q['id'] for q in plan['queries']],benchmark_ids=[],dimension_ids=[],
            ranking_ids=[q['id'] for q in plan['queries']],notes=[NOTE],descending=c.get('ranking_order')!='bottom',top_n=c.get('target_top_n') or 1)
    if len(years)>1:plan['temporal_note']=TEMPORAL_NOTE
    if len(schools)>1 and len(topics)==1:
        import re
        if re.search(r'中间|居中',question):
            if len(schools)%2==0:
                return dict(intent='clarify',original=question,reason='even_population_middle_ambiguous',missing=['偶数所学校有两个中间位置，请指定。'])
            plan['selection']=dict(kind='middle',descending=True)
        elif re.search(r'排序|排位|排名|排第',question):
            plan['selection']=dict(kind='all',descending=not bool(re.search(r'从低到高|升序',question)))
    # Separate scores for partial topic lists; never synthesize a combined score.
    if len(plan['queries'])==2 and len(topics)==1 and (rt._comparison_difference_requested(question) or len(years)==2):
        args=[q['id'] for q in plan['queries']]
        if len(years)==2:args.reverse()
        plan['steps']=[dict(id='d1',operation='subtract',args=args)]
    if not schools:
        from scripts.school_engine import connect
        from packages.semantic_query.regional_capabilities import bind_region
        with closing(connect()) as connection:
            for query in plan['queries']:
                query['region_identity']=bind_region(query,connection,question)
    plan=typed_plan.bind_types(plan,{i['id']:i for i in load_semantic_catalogue()['items']})[0]
    return dict(intent='public_task',original=question,year=str(years[0]),public_plan=plan,value_view='normalized_score',
        requested_outputs=list(c.get('requested_outputs') or ['direct_answer']),topics=list(topics),program_notices=[NOTE])
