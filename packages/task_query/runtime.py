import copy, hashlib, json, os, re, threading
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from . import plan
from scripts.school_engine import connect, _db_path
VERSION='structured-task-1.0'
_LOCK=threading.RLock()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def now():return datetime.now(timezone.utc).isoformat()
def compile_task(arm,raw,shared,question,connection):
    candidate=copy.deepcopy(raw);ch=candidate.get('chart',{})
    from packages.semantic_query.runtime import _explicit_chart_request
    if _explicit_chart_request(question) is True and ch.get('requested') is False:
        raise ValueError('explicit_requested_chart_omitted')
    if ch.get('requested') is False:
        if ch.get('outputs')!=[]:raise ValueError('inactive_chart_has_outputs')
        ch['kind']='bar'
    result=plan.compile_task(arm,candidate,shared)
    from packages.semantic_query import runtime as semantic_runtime
    explicit_view=plan.typed_plan.explicit_view(question,semantic_runtime)
    plan.typed_plan.check_explicit_direction(result,question,semantic_runtime)
    if explicit_view in {'raw_value','normalized_score'} and any(q['view']!=explicit_view for q in result['queries'] if q['view']!='school_count'):
        raise ValueError('typed_explicit_view_mismatch')
    explicit_aggregation=plan.typed_plan.explicit_aggregation(question,semantic_runtime)
    if explicit_aggregation and any(q['aggregation']!=explicit_aggregation for q in result['queries'] if q['kind'].startswith('region_') and q['view']!='school_count'):
        raise ValueError('typed_explicit_aggregation_mismatch')
    result=plan.typed_plan.bind_regions(result,connection,question)
    for q in result['queries']:
        if q['kind']=='school_fact' and q.get('school_filter') is not None:
            location=q['school_filter']
            if not isinstance(location,dict) or set(location)!={'province','city','district'} or any(not isinstance(v,str) or not v.strip() for v in location.values()):raise ValueError('exact_literal_location_required')
    return result

class Executor(plan.Executor):
    def _query(self,q,cat):
        location=q.get('school_filter')
        if q['kind']!='school_fact' or location is None:return super()._query(q,cat)
        params=[q['entity'],q['year']]+[location[k] for k in ['province','city','district']]
        sql='SELECT id,school_name,year,province,city,district,school_type FROM schools WHERE school_name=? AND year=? AND province=? AND city=? AND district=?'
        rows=[dict(r) for r in self.connection.execute(sql,params)]
        i=q['indicator_ids'][0];item=cat[i];unit=item['unit'] if q['view']=='raw_value' else '分'
        label='/'.join(params[2:]+params[:2]+['Q'+str(i)+':'+item['question']])
        trace={'identity_SQL':sql,'identity_params':params,'matches':rows}
        if len(rows)!=1:return {'value':None,'unit':unit,'status':'CLARIFY_IDENTITY' if rows else 'NO_DATA','label':label},trace
        a=self.connection.execute('SELECT question_id,value,normalized_value FROM answers WHERE school_id=? AND question_id=?',(rows[0]['id'],i)).fetchone()
        value=a['value' if q['view']=='raw_value' else 'normalized_value'] if a else None
        if q['view']=='normalized_score' and value is not None:value*=100
        trace.update(school=rows[0],answer=dict(a) if a else None)
        return {'value':value,'unit':unit,'status':'ANSWER' if value is not None else 'NO_DATA','label':label},trace

def analysis_text(task,execution):
    an=task['analysis']
    if an['kind']=='none':return ''
    a,b=[execution['nodes'][i] for i in an['args'][:2]]
    if not all(isinstance(n['value'],(int,float)) for n in [a,b]) or a['unit']!=b['unit']:return '数据或单位不足，未作比较结论。'
    if an['kind']=='calculation':
        step=next((s for s in task['steps'] if s['id']==an['args'][2]),None) if len(an['args'])==3 else next((s for s in task['steps'] if s['args']==an['args']),None)
        return plan.calculation_explanation(task,execution,step)
    if an['kind']=='trend':return '后期上升' if b['value']>a['value'] else '后期下降' if b['value']<a['value'] else '两期相同'
    return a['label']+'更高' if a['value']>b['value'] else b['label']+'更高' if a['value']<b['value'] else '两者相同'

def native_report(task,execution):
    report={'overview':{'object_type':'structured_task','execution_version':VERSION},'task_outputs':[dict(v,node_id=i) for i,v in execution['outputs'].items()],'task_analysis':analysis_text(task,execution),'charts':[]}
    if task['chart']['requested']:
        from packages.agent.tools.chart_builder import EducationalChartBuilder
        from packages.agent.tools.python_runner import PythonInterpreterTool
        groups={}
        for i in task['chart']['outputs']:
            n=execution['nodes'][i]
            if type(n['value']) in {int,float}:groups.setdefault(n['unit'],[]).append(dict(n,node_id=i))
        for unit,nodes in groups.items():
            code=EducationalChartBuilder.build_task_values_code(nodes,task['chart']['kind'],unit)
            result=PythonInterpreterTool().execute(code=code,chart_title=task['chart']['kind']+' · '+unit)
            if not result.success or not result.artifacts:raise RuntimeError('native_chart_tool_failed:'+str(result.error))
            report['charts']+=result.artifacts
    return report

def execute_question(question,arm,request_id,session_id):
    if arm not in {'F','H'}:raise ValueError('fh_arm_must_be_F_or_H')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',request_id):raise ValueError('invalid_request_id')
    with _LOCK:
        from packages.core.frozen_paths import output_dir
        root=Path(os.environ.get('TEACHER_EVAL_FH_TRACE_DIR') or str(Path(output_dir())/'task_query'))
        root.mkdir(parents=True,exist_ok=True);folder=root/request_id
        if folder.exists():
            if (folder/'RESPONSE.json').exists():return json.loads((folder/'RESPONSE.json').read_text(encoding='utf8'))
            return {'session_id':session_id,'business_status':'ERROR','error_code':'request_already_started_no_retry','next_prompt':'该请求已经启动；未重复调用模型。'}
        folder.mkdir();write(folder/'INPUT.json',{'question':question,'arm':arm,'request_id':request_id,'session_id':session_id,'version':VERSION,'started_at':now()})
        ledger_path=root/'HTTP_LEDGER.json';ledger=json.loads(ledger_path.read_text(encoding='utf8')) if ledger_path.exists() else []
        response={'session_id':session_id,'query_mode':{'active_mode':'native_fh','arm':arm,'automatic_rule_fallback':False},'fh_execution_version':VERSION,'revision_control':{'request_id':request_id,'outcome':'COMMITTED','committed_revision':1}}
        conn=None;event=None;phase='configuration'
        try:
            from packages.core.secure_credentials import public_store
            from packages.llm_gateway import gateway as g
            meta=public_store();active=next(p for p in meta['profiles'] if p['id']==meta['active'])
            profile={k:active[k] for k in ['id','provider','model','credential_revision','has_api_key']};profile['endpoint']=g._chat_url(active['api_base'])
            if profile['model']!='deepseek-flash' or urlsplit(profile['endpoint']).hostname!='api.deepseek.com':raise ValueError('official_deepseek_flash_profile_required')
            bind=root/'PROFILE_BINDING.json'
            if bind.exists() and json.loads(bind.read_text(encoding='utf8'))!=profile:raise ValueError('profile_changed_during_run')
            if not bind.exists():write(bind,profile)
            shared=plan.knowledge();prompt=plan.prompt(arm,question,shared)
            prompt['field_contract']['school_location']='When explicitly present, school_filter must bind exact province/city/district literals; no school record ID.'
            messages=[{'role':'system','content':'只返回严格JSON对象。'},{'role':'user','content':json.dumps(prompt,ensure_ascii=False)}]
            write(folder/'PUBLIC_PROMPT.json',prompt)
            phase='model_transport'
            def observe(kind,data):
                nonlocal event
                if kind=='started':
                    cap=int(os.environ.get('TEACHER_EVAL_FH_HTTP_CAP','0'))
                    if cap and len(ledger)>=cap:raise RuntimeError('run_HTTP_cap_reached')
                    if event is not None:raise RuntimeError('single_task_transport_already_started')
                    event={'request_id':request_id,'arm':arm,'attempt':len(ledger)+1,'status':'RESERVED_BEFORE_TRANSPORT','started_at':now()};ledger.append(event)
                    write(folder/'REQUEST.json',data);write(ledger_path,ledger)
                elif kind=='returned':
                    write(folder/'RAW_RESPONSE.json',data);event.update(status='TRANSPORT_RETURNED',usage=data.get('usage',{}),returned_model=data.get('model'),finished_at=now());write(ledger_path,ledger)
                elif kind=='failed':
                    event.update(status='TRANSPORT_FAILED',error_type=data['error_type'],finished_at=now());write(ledger_path,ledger)
            detail=g.call_detailed(g.ChatRequest(provider=profile['provider'],task='semantic_parser',model=profile['model'],messages=messages,temperature=0.0,max_tokens=4096,max_attempts=1,transport_attempts=1,single_endpoint=True,expected_profile_id=profile['id'],http_event_sink=observe))
            write(folder/'DETAIL.json',detail);candidate=json.loads(detail['content']);write(folder/'PARSED.json',candidate)
            conn=connect();conn.execute('PRAGMA query_only=ON');sql=[];conn.set_trace_callback(sql.append)
            phase='task_compilation';task=compile_task(arm,candidate,shared,question,conn);write(folder/'PLAN.json',task)
            phase='database_execution';execution=Executor(conn,shared).execute(task);write(folder/'EXECUTION.json',execution);write(folder/'SQL_TRACE.json',sql)
            phase='native_report';report=native_report(task,execution);write(folder/'REPORT.json',report)
            response.update(report=report,result={'object_type':'structured_task'},business_status=execution['status'],task_plan=task,task_execution=execution,charts=report['charts'],model_usage=detail.get('usage',{}),model_parser={'model':profile['model'],'arm':arm,'new_parser':True,'attempts':detail.get('attempts',[])},repair_calls=0,writer_calls=0)
        except Exception as exc:
            response.update(business_status='ERROR',error_code=str(exc),next_prompt='结构化任务未完成：'+str(exc),failure_phase=phase,error_type=type(exc).__name__)
        finally:
            if conn:conn.close()
            write(folder/'RESPONSE.json',response)
        return response
