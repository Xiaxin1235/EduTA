"""Native F/H task representation, compiler and database execution.

Promoted reusable contract logic; no experiment imports, reference answers,
model-output caches, HTML rendering, scoring or question-ID branches.
"""
import copy, json, math, re
from packages.semantic_query.catalogue import load_semantic_catalogue
from scripts.school_engine import evaluate_school, load_school_template
from scripts.region_engine import evaluate_region
from packages.semantic_query import typed_plan
OPS = {'subtract','divide','divide_percent','add','mean'}
FIELDS = ('kind','entity','year','dimension','topic','view','aggregation','statistic','school_filter','rank','top_n','semantic_type','region_identity')
def knowledge():
    return {'catalogue': copy.deepcopy(load_semantic_catalogue()['items']), 'topics':copy.deepcopy(load_semantic_catalogue()['topics']),
            'school_template': load_school_template(), 'context': {},
            'rules': {'region_mean': 'Native school_equal_mean: average non-NULL item scores per school, then equal school mean; normalized_value*100.',
                      'school_assessment': 'Use supplied native template, weights and missing-value rules.',
                      'count': 'Schools with at least one non-NULL selected normalized answer.',
                      'facts': 'Raw answers.value has catalogue units; normalized_score is answers.normalized_value*100 in 分.',
                      'arithmetic': 'Ordered subtract/divide/divide_percent; add/mean; any earlier query or step may be referenced. Subtract/add/mean require equal units; divide yields 倍, divide_percent yields %.',
                      'missing': 'Numeric zero is valid, NULL is NO_DATA, zero denominator is UNDEFINED_ZERO_DIVISOR.',
                      'identity': 'Exact unique school name and year; ambiguous names require clarification; no first-hit selection.',
                      'rounding': 'Native region results round each reported mean to 2 decimals; derived differences use these reported values.',
                      'delivery': 'Return requested nodes only; chart intent explicit boolean; deterministic comparison/trend text, no writer.'}}

def prompt(path, question, shared=None):
    if path not in {'F', 'H'}: raise ValueError('unknown_path')
    query = {'id': 'v1', 'kind': 'school_fact|school_assessment|region_assessment|region_ranking',
             'entity': 'original literal name', 'year': 'YYYY', 'dimension': None,
             'topic': None, 'view': 'raw_value|normalized_score|school_count', 'aggregation': 'none|school_equal_mean|answer_pooled_mean|template_weighted_dimension_mean|distinct_schools_with_valid_answer',
             'statistic': 'null|mean|count|median|max|min', 'indicator_ids': [],
             'school_filter': None, 'rank': None, 'top_n': None}
    schema = {'queries': [query]}
    if path == 'H':
        schema = {'scopes': [dict({k:v for k,v in query.items() if k in FIELDS}, id='s1')],
                  'queries': [{'id':'v1', 'scope':'s1', 'indicator_ids':[]}]}
    schema.update({'steps':[{'id':'d1','op':'subtract|divide|divide_percent|add|mean','args':['v1','v2']}],
                   'outputs':['v1','d1'], 'chart':{'requested':False,'outputs':[],'kind':'bar|line'},
                   'analysis':{'kind':'none|comparison|trend|calculation','args':[]}})
    shared=shared or knowledge()
    dimension_keys={x['dimension_key']:x['dimension'] for x in shared['catalogue'] if x.get('dimension_key')}
    contract={
        'dimension':{'canonical_names':sorted({x['dimension'] for x in shared['catalogue']}),
                     'accepted_catalogue_keys':dimension_keys,'unknown_values':'rejected'},
        'statistic':'For school_fact with one indicator and aggregation none, use null: retrieve that one recorded value. Catalogue value_type=count is its value type, not COUNT(rows). Region statistics retain mean/count/median/max/min.',
        'analysis':'none has args []; otherwise use two operand node IDs. An optional third ID must be an existing derived step of those two operands (comparison/trend: subtract; calculation: same ordered operands). Never add arbitrary nodes.',
        'analysis_intent':'Use comparison/trend/calculation only for an explicitly requested textual explanation. Numeric calculations or charts alone do not request prose.',
        'chart':'Include the requested quantity nodes, and a derived result when the question asks to show its sum/ratio/difference in the chart. Do not replace the requested operands.'}
    contract['semantic_types']='Optional semantic_type, region_identity and step comparison are compiler-owned v1 metadata. Omit them in parser JSON; legacy fields keep their meaning. Do not change raw to normalized, city to province, or ordered A-minus-B. Ambiguous geography remains unresolved.'
    return {'instruction':'Parse the original question to strict JSON. Bind objects, periods, catalogue scope, view, aggregation, ordered dependencies and requested outputs. No data answers, repair or writer.',
            'schema':schema, 'field_contract':contract, 'knowledge':shared, 'question':question}

def to_h(flat):
    out = copy.deepcopy(flat); out['scopes']=[]; out['queries']=[]; scopes={}
    for q in flat['queries']:
        scope={k:q.get(k) for k in FIELDS}; key=json.dumps(scope,sort_keys=True,ensure_ascii=False)
        if key not in scopes:
            scopes[key]='s'+str(len(scopes)+1); out['scopes'].append(dict(scope,id=scopes[key]))
        out['queries'].append({'id':q['id'],'scope':scopes[key],'indicator_ids':q.get('indicator_ids',[])})
    return out

def compile_task(path, task, shared=None):
    shared=shared or knowledge(); task=copy.deepcopy(task)
    if path == 'H':
        scopes=task.pop('scopes',[]); sm={s['id']:s for s in scopes}
        if len(sm)!=len(scopes): raise ValueError('duplicate_scope')
        rows=[]
        for q in task.get('queries',[]):
            if set(q)-{'id','scope','indicator_ids'}: raise ValueError('scope_override')
            if q.get('scope') not in sm: raise ValueError('unknown_scope')
            rows.append(dict({k:v for k,v in sm[q['scope']].items() if k!='id'},id=q['id'],indicator_ids=q.get('indicator_ids',[])))
        task['queries']=rows
    elif path not in {'C0','F'}: raise ValueError('unknown_path')
    catalogue={x['id']:x for x in shared['catalogue']}; dims={x['dimension'] for x in catalogue.values()}
    dimension_keys={}
    for item in catalogue.values():
        key=item.get('dimension_key')
        if key:
            if key in dimension_keys and dimension_keys[key]!=item['dimension']: raise ValueError('ambiguous_dimension_key')
            dimension_keys[key]=item['dimension']
    seen=set()
    if not task.get('queries'): raise ValueError('empty_queries')
    for q in task['queries']:
        if not q.get('id') or q['id'] in seen: raise ValueError('duplicate_node')
        seen.add(q['id'])
        if q.get('kind') not in {'school_fact','school_assessment','region_assessment','region_ranking'}: raise ValueError('unsupported_kind')
        if not isinstance(q.get('entity'),str) or not q['entity']: raise ValueError('missing_entity')
        if not re.fullmatch(r'\d{4}',str(q.get('year',''))): raise ValueError('invalid_year')
        if q.get('view') not in {'raw_value','normalized_score','school_count'}: raise ValueError('invalid_view')
        if q.get('dimension') in dimension_keys: q['dimension']=dimension_keys[q['dimension']]
        if q.get('dimension') is not None and q['dimension'] not in dims: raise ValueError('unknown_dimension')
        if q.get('topic'):
            from packages.semantic_query.catalogue import topic_definition
            topic=topic_definition(q['topic'])
            if q.get('dimension') not in (None,topic['dimension']):raise ValueError('parent_binding_mismatch')
            q['dimension']=None
            if not q.get('indicator_ids'):q['indicator_ids']=list(topic['indicator_ids'])
            if q['view']!='normalized_score' or q.get('statistic')!='mean' or q.get('aggregation')!='school_equal_mean':
                raise ValueError('topic_normalized_mean_only')
            if topic.get('collapse_to_dimension'):
                q.update(topic=None,dimension=topic['collapse_to_dimension'],indicator_ids=[],aggregation='template_weighted_dimension_mean' if q['kind']=='school_assessment' else 'school_equal_mean')
        ids=q.get('indicator_ids',[])
        if not isinstance(ids,list) or len(ids)!=len(set(ids)) or any(type(i)!=int or i not in catalogue for i in ids): raise ValueError('invalid_indicators')
        for i in ids:
            if q.get('dimension') and q['dimension']!=catalogue[i]['dimension']: raise ValueError('parent_binding_mismatch')
            if q.get('topic') and q['topic']!=catalogue[i]['topic']: raise ValueError('parent_binding_mismatch')
        if q['kind']=='school_fact':
            if len(ids)!=1 or q.get('aggregation')!='none': raise ValueError('fact_requires_one_leaf')
            # This path retrieves one registered answer; no statistical reduction runs.
            if q.get('statistic') not in {None,'mean','count','median','max','min'}: raise ValueError('unsupported_statistic')
            q['statistic']=None
        elif q['kind']=='school_assessment':
            if q['view']!='normalized_score' or q.get('aggregation')!=('school_equal_mean' if q.get('topic') else 'template_weighted_dimension_mean'): raise ValueError('invalid_school_assessment')
        else:
            count_scope=q.get('statistic')=='count' and q['view']=='school_count' and q.get('aggregation')=='distinct_schools_with_valid_answer'
            if not count_scope and (q['view']!='normalized_score' or q.get('aggregation') not in {'school_equal_mean','answer_pooled_mean'}): raise ValueError('unsupported_region_view_or_aggregation')
        if q['kind']!='school_fact' and q.get('statistic') not in {'mean','count','median','max','min'}: raise ValueError('unsupported_statistic')
        if q['kind']=='region_ranking':
            if q.get('statistic')!='mean' or q.get('aggregation')!='school_equal_mean': raise ValueError('unsupported_ranking_semantics')
            if bool(q.get('rank')) == bool(q.get('top_n')): raise ValueError('ranking_target_required')
            if any(v is not None and (type(v)!=int or v<=0) for v in (q.get('rank'),q.get('top_n'))): raise ValueError('invalid_rank')
    for s in task.get('steps',[]):
        if not s.get('id') or s['id'] in seen: raise ValueError('duplicate_node')
        if s.get('op') not in OPS: raise ValueError('unknown_operation')
        args=s.get('args',[])
        if not args or any(a not in seen for a in args) or (s['op'] in {'subtract','divide','divide_percent'} and len(args)!=2): raise ValueError('invalid_dependency')
        seen.add(s['id'])
    if not task.get('outputs') or any(x not in seen for x in task['outputs']): raise ValueError('invalid_outputs')
    ch=task.get('chart',{})
    if type(ch.get('requested'))!=bool or ch.get('kind','bar') not in {'bar','line'}: raise ValueError('invalid_chart')
    if any(x not in seen for x in ch.get('outputs',[])) or (ch['requested'] and not ch.get('outputs')): raise ValueError('invalid_chart_outputs')
    an=task.get('analysis',{'kind':'none','args':[]})
    args=an.get('args',[])
    if an.get('kind') not in {'none','comparison','trend','calculation'} or not isinstance(args,list) or any(x not in seen for x in args): raise ValueError('invalid_analysis')
    if an['kind']=='none':
        if args: raise ValueError('invalid_analysis')
    elif len(args) not in {2,3}: raise ValueError('invalid_analysis')
    elif len(args)==3:
        step=next((s for s in task.get('steps',[]) if s['id']==args[2]),None)
        if step is None: raise ValueError('invalid_analysis_result')
        if an['kind']=='calculation':
            valid=step['args']==args[:2]
        else:
            valid=step['op']=='subtract' and step['args'] in [args[:2],list(reversed(args[:2]))]
        if not valid: raise ValueError('invalid_analysis_result')
    task['analysis']=an
    return typed_plan.bind_types(task,catalogue)[0]

def canonical_task(task,shared=None):
    """Compare executable semantics, retaining operation direction and requested delivery."""
    shared=shared or knowledge(); catalogue={x['id']:x for x in shared['catalogue']}
    nodes={}; queries=[]; steps=[]
    for q in task['queries']:
        row={k:q.get(k) for k in ('kind','entity','year','dimension','indicator_ids','view','aggregation','statistic','school_filter','rank','top_n')}
        row['semantic_type']=typed_plan.metric_type(q,catalogue)
        row['region_identity']=q.get('region_identity')
        if row['kind']=='school_fact' and row['aggregation']=='none':
            row['statistic']=None
            if row['dimension'] is None: row['dimension']=catalogue[row['indicator_ids'][0]]['dimension']
        key=json.dumps(row,sort_keys=True,ensure_ascii=False); nodes[q['id']]=key; queries.append(key)
    for step in task.get('steps',[]):
        key=json.dumps({'op':step['op'],'args':[nodes[i] for i in step['args']]},sort_keys=True,ensure_ascii=False)
        nodes[step['id']]=key; steps.append(key)
    ch=task['chart']; an=task['analysis']
    # A compiler-validated third analysis node repeats an already checked step.
    delivery={'outputs':sorted(nodes[i] for i in task['outputs']),
              'chart_requested':ch['requested'],'chart_outputs':sorted(nodes[i] for i in ch['outputs']) if ch['requested'] else [],
              'chart_kind':ch['kind'] if ch['requested'] else None,
              'analysis_kind':an['kind'],'analysis_args':[nodes[i] for i in an['args'][:2]]}
    return {'queries':sorted(queries),'steps':steps},delivery,nodes

def query(kind,entity,year,dimension=None,ids=None,view=None,statistic='mean',aggregation=None,topic=None,**kw):
    return dict(kind=kind,entity=entity,year=str(year),dimension=dimension,topic=topic,
                indicator_ids=ids or [],view=view or ('school_count' if statistic=='count' else 'raw_value' if kind=='school_fact' else 'normalized_score'),
                statistic=statistic,aggregation='distinct_schools_with_valid_answer' if statistic=='count' else aggregation or ('school_equal_mean' if topic else 'none' if kind=='school_fact' else 'template_weighted_dimension_mean' if kind=='school_assessment' else 'school_equal_mean'),
                school_filter=None,rank=None,top_n=None,**kw)

class Executor:
    def __init__(self,connection,shared=None): self.connection=connection; self.shared=shared or knowledge(); self.cache={}
    def execute(self,task):
        nodes={}; trace=[]; cat={x['id']:x for x in self.shared['catalogue']}
        task,types=typed_plan.bind_types(task,cat)
        task=typed_plan.bind_regions(task,self.connection)
        for q in task['queries']:
            key=json.dumps({k:v for k,v in q.items() if k!='id'},sort_keys=True,ensure_ascii=False)
            if key not in self.cache: self.cache[key]=self._query(q,cat)
            node,raw=copy.deepcopy(self.cache[key]); typed_plan.check_result_type(node,types[q['id']])
            if q.get('region_identity'):node['region_identity']=copy.deepcopy(q['region_identity'])
            nodes[q['id']]=node; trace.append({'id':q['id'],'parameters':q,'native_result':raw,'node':node})
        for s in task.get('steps',[]):
            args=[nodes[x] for x in s['args']]; units={x['unit'] for x in args}; values=[x['value'] for x in args]
            if len(units)!=1: raise ValueError('incompatible_units')
            if any(isinstance(v,(list,dict)) for v in values): raise ValueError('non_scalar_dependency')
            status='ANSWER'; value=None
            if any(v is None for v in values): status='NO_DATA'
            elif s['op'] in {'divide','divide_percent'} and values[1]==0: status='UNDEFINED_ZERO_DIVISOR'
            elif s['op']=='subtract': value=values[0]-values[1]
            elif s['op']=='add': value=sum(values)
            elif s['op']=='mean': value=sum(values)/len(values)
            else: value=values[0]/values[1]*(100 if s['op']=='divide_percent' else 1)
            if value is not None and not math.isfinite(value): raise ValueError('non_finite')
            unit='倍' if s['op']=='divide' else '%' if s['op']=='divide_percent' else args[0]['unit']
            nodes[s['id']]={'value':value,'unit':unit,'status':status,'label':s['op']+'('+','.join(s['args'])+')'}
            typed_plan.check_result_type(nodes[s['id']],types[s['id']])
            if s.get('comparison'):nodes[s['id']]['comparison']=copy.deepcopy(s['comparison'])
            trace.append({'id':s['id'],'operation':s,'input_values':values,'node':nodes[s['id']]})
        return {'nodes':nodes,'outputs':{i:nodes[i] for i in task['outputs']},'trace':trace,'status':'ANSWER' if all(nodes[i]['status']=='ANSWER' for i in task['outputs']) else 'PARTIAL'}
    def _query(self,q,cat):
        c=self.connection; kind=q['kind']; unit='分'; raw=None; value=None
        if kind.startswith('school_'):
            rows=[dict(x) for x in c.execute('SELECT id,school_name,year,province,city,district FROM schools WHERE school_name=? AND year=?',(q['entity'],q['year']))]
            if len(rows)!=1: return ({'value':None,'unit':q['semantic_type']['unit'],'status':'CLARIFY_IDENTITY' if rows else 'NO_DATA','label':q['entity']+'/'+q['year']}, {'matches':rows})
            sid=rows[0]['id']
            if kind=='school_fact':
                i=q['indicator_ids'][0]; a=c.execute('SELECT question_id,value,normalized_value FROM answers WHERE school_id=? AND question_id=?',(sid,i)).fetchone()
                raw={'school':rows[0],'answer':dict(a) if a else None}; value=a['value' if q['view']=='raw_value' else 'normalized_value'] if a else None
                if q['view']=='normalized_score' and value is not None: value*=100
                unit=cat[i]['unit'] if q['view']=='raw_value' else '分'
            else:
                raw=evaluate_school(sid,connection=c,target_dimension=q.get('dimension'),indicator_ids=q.get('indicator_ids'),topic=q.get('topic'))
                value=raw.get('topic_score') if q.get('topic') else raw.get('target_dimension_score') if q.get('dimension') else raw.get('total_score')
                if q.get('dimension') and value is None: value=next((x.get('score') for x in raw.get('dimensions',[]) if x.get('dimension',x.get('name'))==q['dimension']),None)
        else:
            raw=evaluate_region(q['entity'],year=q['year'],target_dimension=q.get('dimension'),target_stat=q['statistic'],school_filter=q.get('school_filter'),aggregation='school_equal_mean' if q['statistic']=='count' else q['aggregation'],indicator_ids=q.get('indicator_ids'),value_view='normalized_score' if q['statistic']=='count' else q['view'],target_rank=q.get('rank'),target_top_n=q.get('top_n'),operation='region_ranking' if kind=='region_ranking' else 'region_evaluation',topic=q.get('topic'),connection=c,region_identity=q.get('region_identity'))
            empty=typed_plan.no_data_result(raw)
            field='target_dimension_score' if q.get('dimension') and q['statistic']=='mean' else {'mean':'total_score','count':'school_count','median':'median_score','max':'max_score','min':'min_score'}[q['statistic']]
            value=None if empty else raw.get('ranking_items') if kind=='region_ranking' else raw.get(field); unit='所' if q['statistic']=='count' else '分'
        scope=';'.join('Q'+str(i)+':'+cat[i]['question'] for i in q.get('indicator_ids',[])) if q.get('indicator_ids') else q.get('dimension') or 'overall'
        if q.get('topic'):
            from packages.semantic_query.topic_tasks import label
            scope=label(q['topic'])
        node={'value':value,'unit':unit,'status':'ANSWER' if value is not None else 'NO_DATA','label':q['entity']+'/'+q['year']+'/'+scope}
        if q.get('topic'):
            from packages.semantic_query.topic_tasks import NOTE
            node.update(topic=q['topic'],measurement_note=NOTE,all_items_uncollected=bool(raw.get('all_items_uncollected')))
            if not node['all_items_uncollected']:node['school_count']=raw.get('school_count',0)
            node['label']+='。'+NOTE
        return node,raw

def calculation_explanation(task,result,step):
    """Describe the executed operation using its operands' measurement semantics."""
    if step is None: return '未找到可验证的计算步骤，未生成计算结论。'
    queries={q['id']:q for q in task['queries']}
    def measure(node_id):
        q=queries.get(node_id,{}); unit=result['nodes'][node_id]['unit']
        if q.get('view')=='normalized_score' or unit=='分':
            return '均分' if q.get('kind') in {'region_assessment','region_ranking'} and q.get('statistic')=='mean' else '分值'
        if q.get('view')=='school_count' or unit in {'个','台','所','人','间','套','件','册','门','种','项','次','节','张'}: return '数量'
        return '记录值'
    measures={measure(i) for i in step['args']}
    term=next(iter(measures)) if len(measures)==1 else '数值'
    op=step['op']
    if op=='divide': return f'比值按前者{term}除以后者{term}计算，为无量纲倍数；只比较报告{term}，不推断重叠或原因。'
    if op=='divide_percent': return f'百分比按前者{term}除以后者{term}再乘100计算，单位为%；不推断重叠或原因。'
    if op=='subtract': return f'有符号差按前者{term}减后者{term}计算，保留原单位；只比较报告{term}，不解释为集合差或因果。'
    if op=='add': return f'合计为两项报告{term}相加，保留原单位。'
    if op=='mean': return f'均值按各项报告{term}之和除以项数计算，保留原单位。'
    return '未找到支持的计算说明。'
