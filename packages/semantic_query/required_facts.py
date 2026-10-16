from __future__ import annotations

import math
import re
from typing import Any, Dict, List

def _numeric(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))

def required_facts(evidence: Dict[str, Any], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = []
    def add(key, value, entity=None, unit="分", **extra):
        if entity in (evidence.get('request_entity_aliases') or {}):
            extra.setdefault('entity_aliases',evidence['request_entity_aliases'][entity])
        facts.append({"key": key, "source_field": key, "entity": entity,
                      "period": evidence.get("year"), "value": value,
                      "unit": unit, "available": _numeric(value), **extra})

    if spec.get("operation") == "public_task":
        plan=spec.get("public_plan") or {}
        allowed={q["id"] for q in plan.get("queries",[])}|{q["id"] for q in plan.get("steps",[])}
        if (plan.get("selection") or {}).get("kind")=="middle":allowed.add("middle")
        for node in evidence.get("task_outputs") or []:
            if node.get("id") not in allowed:continue
            scope=node.get("scope") or {}
            common=dict(entity=scope.get("entity"),period=scope.get("year"),label=node.get("label"),scope=scope)
            if scope.get('source_id'):
                source=next((q for q in plan.get('queries',[]) if q['id']==scope['source_id']),{})
                common.update(entity=source.get('entity'),period=source.get('year'),selection_kind=(plan.get('selection') or {}).get('kind'))
            if isinstance(node.get("value"),list):
                for row in node["value"]:
                    if scope.get('kind')=='school_item_extremum':
                        add(node['id']+':q'+str(row['question_id']),row['score'],unit=node.get('unit'),
                            **dict(common,question_id=row['question_id'],label=row['label']))
                        continue
                    add(node["id"]+":"+str(row.get("school_id")),row.get("score"),unit=node.get("unit"),
                        **dict(common,entity=row.get("school_name"),rank=row.get("rank")))
            else:
                add(node["id"],node.get("value"),unit=node.get("unit"),rank=node.get("rank"),**common)
        return facts
    if spec.get("operation") == "region_compare" and spec.get("target_stat") != "count":
        add("total_score_a", evidence.get("total_score_a"), evidence.get("region_name_a"))
        add("total_score_b", evidence.get("total_score_b"), evidence.get("region_name_b"))
        if "difference" in (spec.get("requested_result_statistics") or []):
            reverse = spec.get("subtraction_direction") == -1
            add("diff", evidence.get("diff"), operation="subtract",
                ordered_entities=[evidence.get("region_name_b" if reverse else "region_name_a"),
                                  evidence.get("region_name_a" if reverse else "region_name_b")],
                basis=evidence.get("difference_basis") or "unspecified")
    elif spec.get("operation") == "region_longitudinal":
        for side in ("a", "b"):
            add("total_score_" + side, evidence.get("total_score_" + side, evidence.get("comparison_value_" + side)), evidence.get("region_name"),
                period=evidence.get("year_" + side))
    elif spec.get("operation") == "school_fact" and spec.get("granularity") == "item_set":
        for row in evidence.get("item_results") or []:
            add("item:" + str(row.get("question_id")), row.get("target_value"), evidence.get("school_name"),
                unit=row.get("unit") or "", question_id=row.get("question_id"), label=row.get("metric_name"))
        derived = evidence.get("derived_result") or {}
        if derived.get("operation") in {"subtract", "add", "divide", "divide_percent"}:
            add("derived_result.value", derived.get("value"), evidence.get("school_name"),
                unit=derived.get("unit") or "", operation=derived.get("operation"),
                ordered_qids=[derived.get("left_qid"), derived.get("right_qid")])
    elif spec.get("single_value_only"):
        stat = spec.get("target_stat") or "mean"
        dimension = spec.get("granularity") == "dimension"
        key = ("target_dimension_median_score" if dimension else "median_score") if stat == "median" else (
            "min_score" if stat == "min" else "max_score" if stat == "max" else
            "target_dimension_score" if dimension else "total_score")
        add(key, evidence.get(key), evidence.get("region_name"))
    elif spec.get('operation') == 'region_eval' and spec.get('granularity') == 'dimension_pair':
        entity=evidence.get('short_name') or evidence.get('region_name')
        for dim in spec.get('target_dimensions') or evidence.get('target_dimensions') or []:
            row=next((r for r in evidence.get('dimensions') or [] if r.get('dimension')==dim),{})
            add('dimension:'+dim,row.get('score'),entity,label=dim,source_field='dimensions.'+dim+'.score')
        add('diff',evidence.get('diff'),entity,operation='subtract',
            ordered_labels=spec.get('target_dimensions') or evidence.get('target_dimensions') or [])
    elif spec.get('operation') == 'region_eval':
        stat=spec.get('target_stat') or 'mean';dim=spec.get('granularity')=='dimension'
        entity=evidence.get('short_name') or evidence.get('region_name')
        keys=(['school_count'] if stat=='count' or spec.get('output_focus')=='school_count' else
              ['max_score','min_score'] if stat in {'min_and_max','max_and_min'} else
              ['min_score'] if stat=='min' else ['max_score'] if stat=='max' else
              ['total_score','median_score','mean_median_difference'] if stat=='mean_and_median' else
              ['target_dimension_median_score' if dim else 'median_score'] if stat=='median' else
              ['target_value'] if spec.get('granularity')=='item' else
              ['target_dimension_score' if dim else 'total_score'])
        for key in keys:
            value=evidence.get(key);source=key;extra={}
            if key=='target_dimension_median_score' and evidence.get('target_dimension') and evidence.get('target_stat')=='median':

                if value is None:value=evidence.get('median_score');source='median_score'
                elif _numeric(evidence.get('median_score')) and not _value_equal(str(value),evidence['median_score']):
                    extra['evidence_conflict']=True
            if key=='mean_median_difference' and _numeric(evidence.get('total_score')) and _numeric(evidence.get('median_score')):
                computed=round(evidence['total_score']-evidence['median_score'],2)
                if value is None:value=computed;source='total_score-median_score'
                elif not _value_equal(str(value),computed):extra['evidence_conflict']=True
                extra.update(operation='subtract',ordered_labels=['均值','中位数'])
            add(key,value,entity,unit='所' if key=='school_count' else evidence.get('unit') or '分',
                source_field=source,label=evidence.get('target_dimension') if dim else None,**extra)
    elif spec.get('operation') == 'school_eval':
        entity=evidence.get('school_name');dims=list(spec.get('target_dimensions') or []) or ([evidence['target_dimension']] if spec.get('granularity')=='dimension' and evidence.get('target_dimension') else [])
        if spec.get('granularity')=='overall':add('total_score',evidence.get('total_score'),entity)
        if spec.get('include_dimensions') or dims:
            rows=evidence.get('dimensions') or []
            for dim in dims or [row['dimension'] for row in rows]:
                row=next((row for row in rows if row.get('dimension')==dim),{})
                add('dimension:'+dim,row.get('score',evidence.get('target_dimension_score') if len(dims)==1 else None),entity,label=dim)
        elif spec.get('granularity')!='overall':add('total_score',evidence.get('total_score'),entity)
    elif spec.get('operation') == 'school_fact':
        add('target_value',evidence.get('target_value'),evidence.get('school_name'),
            unit=evidence.get('unit') or ('分' if spec.get('value_view')=='normalized_score' else ''),
            question_id=evidence.get('question_id'),label=evidence.get('metric_name'))
    elif spec.get('operation')=='region_compare' and spec.get('target_stat')=='count':
        for side in ('a','b'):
            add('school_count_'+side,evidence.get('school_count_'+side),evidence.get('region_name_'+side),unit='所')

    requested=set(spec.get('requested_result_statistics') or [])
    if spec.get('operation')=='region_compare':
        for base in ('school_count','answer_count'):
            if base in requested:requested.discard(base);requested.update({base+'_a',base+'_b'})
    for key in ('school_count','answer_count','school_count_a','school_count_b','answer_count_a','answer_count_b'):
        if key not in requested or any(f['key']==key for f in facts):continue
        side=key[-1] if key.endswith(('_a','_b')) else None
        entity=evidence.get('region_name_'+side) if side else evidence.get('short_name') or evidence.get('region_name') or evidence.get('school_name')
        add(key,evidence.get(key),entity,unit='所' if 'school' in key else '条')
    return facts

def verify_request_fact_slots(text,evidence,spec):
    pass
    from packages.semantic_query.fact_assertions import verify_scalar_slots
    return verify_scalar_slots(text,evidence,spec)

def _compact(text):
    return re.sub(r"[ \t\r*`_（）()]", "", str(text or "")).replace("−", "-").replace("－", "-").replace("＝", "=").replace("％", "%")

def _bind_public_view_line(line, previous, view):
    pass
    patterns={'raw_value':r'原始(?:值|数量|作答)|原值|登记数量|登记数值',
              'normalized_score':r'标准分|归一(?:化)?(?:分|值|得分)|百分制(?:分数|得分)?'}
    matches=sorted((m.start(),m.end(),v) for v,p in patterns.items() for m in re.finditer(p,line))
    selected=[x for x in matches if x[2]==view]
    if selected:
        pieces=[]
        for start,end,_ in selected:
            stops=[a for a,_,v in matches if a>=end and v!=view]
            pieces.append(line[start:min(stops) if stops else len(line)])
        return '；'.join(pieces),''
    if matches:return None,''
    if '|' in line:
        cells=line.strip('|').split('|')
        for prior in reversed(previous):
            if '|' not in prior:continue
            headers=prior.strip('|').split('|')
            columns=[i for i,h in enumerate(headers) if re.search(patterns[view],h)]
            if columns:
                return '|'.join(cells[i] for i in columns if i<len(cells))+'|','|'.join(headers[i] for i in columns)
    for prior in reversed(previous):
        prior_matches=sorted((m.start(),v) for v,p in patterns.items() for m in re.finditer(p,prior))
        if prior_matches:
            if {v for _,v in prior_matches}=={view}:return line,prior
            return None,''
    return None,''

def verify_public_facts(text, evidence, spec):
    pass
    if spec.get('operation')!='public_task':return []
    plan=spec.get('public_plan') or {}
    expected={q['id'] for q in plan.get('queries',[])}|{q['id'] for q in plan.get('steps',[])}
    if (plan.get('selection') or {}).get('kind')=='middle':expected.add('middle')
    if not expected:return []
    nodes=evidence.get('task_outputs') or []
    if {n.get('id') for n in nodes}!=expected:return ['public_required_evidence_node_set']
    facts=required_facts(evidence,spec)
    if not facts:return ['public_required_evidence_empty']
    answer=_compact(text)
    errors=[]
    queries={q['id']:q for q in plan.get('queries',[])}

    short_rank = (len(queries) == 1 and len(facts) == 1
                  and facts[0].get('rank') is not None
                  and re.fullmatch(r"[+-]?\d+(?:\.\d+)?分[，,、；;]?第\d+名(?:同分并列|同分并列。|同分并列[。.]?)?[。.]?", answer))
    if short_rank:
        answer = str(facts[0].get('entity') or '') + '/' + str(facts[0].get('period') or '') + '/' + answer

    ranked_entities={str(f.get('entity') or '') for f in facts if (f.get('scope') or {}).get('kind')=='region_item_rank'} - {''}

    lines=[]
    for row in answer.replace('。','\n').splitlines():
        lines.extend([row] if sum(e in row for e in ranked_entities)==1 else row.replace('；','\n').splitlines())
    lines=lines or [answer]
    if any(q.get('kind')=='region_item_rank' and q.get('aggregation')=='none' for q in queries.values()):
        for line in lines:
            affirmative=re.sub(r'(?:不是|并非|不属于|不按|不得|不做|不计算|没有|无跨校|无学校等权|未计算|未做)[^，,；;。\n]*', '', line)
            if re.search(r'校均|学校等权|统计量[：:]?(?:为)?均值|按.{0,8}平均', affirmative):
                errors.append('public_item_rank_aggregation_mislabel')
    for fact in facts:
        value=fact['value'];scope=fact.get('scope') or {};unit=fact.get('unit') or ''
        entity=str(fact.get('entity') or '');year=str(fact.get('period') or '')
        number=r'(?<![\d.])([+-]?\d+(?:\.\d+)?)(?![\d.])'
        if fact.get('selection_kind')=='middle':
            claims=[line for line in lines if re.search(r'中间|居中|正中',line)]
            named={str(q['entity']) for q in queries.values() if q.get('entity')}
            if not any(entity in line and any(_value_equal(m[1],value) for m in re.finditer(number,line)) for line in claims):
                errors.append('public_selected_object_missing:'+str(fact['key']))
            if any(any(e in line for e in named-{entity}) and entity not in line for line in claims):
                errors.append('public_selected_object_mismatch:'+str(fact['key']))
            continue
        found=False;mismatch=False;unknown_unit=False
        for i,line in enumerate(lines):
            local_found=False

            context='\n'.join(lines[max(0,i-4):i+1])
            if entity and entity not in context:
                named={str(q['entity']) for q in queries.values() if q.get('entity')}
                mentions=[(j,e) for j,prior in enumerate(lines[:i]) for e in named if e in prior]
                if not mentions or mentions[-1][1]!=entity:continue
                context=entity+'\n'+context
            if year and year not in context:
                earlier_years=re.findall(r'(?<!\d)20\d{2}(?!\d)','\n'.join(lines[:i]))
                if not earlier_years or earlier_years[-1]!=year:continue
                context=year+'\n'+context
            other_entities={str(q.get('entity')) for q in queries.values() if q.get('entity') and str(q['entity'])!=entity}
            other_years={str(q['year']) for q in queries.values() if str(q.get('year'))!=year}
            if entity and entity not in line and any(e in context for e in other_entities):continue
            if year and year not in line and any(y in context for y in other_years):continue

            cohort=scope.get('school_filter')
            if cohort and len({q.get('school_filter') for q in queries.values()
                               if q.get('entity')==scope.get('entity') and q.get('year')==scope.get('year')})>1:
                cohort_labels={str(q['school_filter']) for q in queries.values() if q.get('school_filter')}
                if cohort not in context:continue
                if cohort not in line and any(c in line for c in cohort_labels-{cohort}):continue
                if cohort in line:
                    start=line.index(cohort)+len(cohort)
                    ends=[line.find(c,start) for c in cohort_labels-{cohort} if line.find(c,start)>=0]
                    line=line[start:min(ends) if ends else len(line)]
            if scope.get('kind')=='school_item_extremum':
                qid=fact['question_id']
                if not (re.search(r'(?:Q|第)'+str(qid)+r'(?!\d)',line,re.I) or str(fact.get('label') or '') in line):continue
            if scope.get('question_id'):
                from packages.semantic_query.catalogue import question_metric
                metric=question_metric(scope['question_id'])['name']
                alternatives=[q for q in queries.values() if q.get('entity')==scope.get('entity') and q.get('year')==scope.get('year') and q.get('question_id')!=scope['question_id']]
                if alternatives:
                    names=[question_metric(q['question_id'])['name'] for q in alternatives if q.get('question_id')]
                    fragments={metric[a:b] for a in range(len(metric)) for b in range(a+4,len(metric)+1) if not any(metric[a:b] in n for n in names)}
                    if not (re.search(r'(?:Q|第)'+str(scope['question_id'])+r'(?!\d)',context,re.I) or any(v in context for v in fragments)):continue
            multiple_views=len({q.get('view') for q in queries.values() if all(q.get(k)==scope.get(k) for k in ('entity','year','question_id'))})>1
            view_unit_context=None
            if multiple_views:
                line,view_unit_context=_bind_public_view_line(line,lines[:i],scope.get('view'))
                if line is None:continue
            if not fact.get('available'):
                if re.search(r'无数据|缺失|未登记|不可计算',line):found=True
                continue
            for m in re.finditer(number,line):
                suffix=line[m.end():];prefix=line[:m.start()]

                if suffix.startswith(('年','题','名')) or re.search(r'(?:Q|第)$',prefix,re.I):continue
                from packages.semantic_query.fact_assertions import MEASURE, unit_alias
                measured=MEASURE.match(line,m.start())
                unit_matches=measured is not None and unit_alias(measured[2])==unit_alias(unit)
                if unit and not (unit_matches or ('|' in line and unit in (view_unit_context if multiple_views else context))):
                    if _value_equal(m[1],value):
                        if measured:errors.append('public_required_unit_mismatch:'+str(fact['key']))
                        elif re.match(r'[\u4e00-\u9fff]{2,}',suffix):unknown_unit=True
                    continue
                if scope.get('operation')=='subtract':
                    if not re.search(r'减|差|高|低|增加|下降|上升|变化',line):continue
                    directed=float(m[1])
                    if not _value_equal(m[1],value):

                        args=scope.get('args') or []
                        left=queries.get(args[0],{}) if args else {}
                        lanchor=str(left.get('entity') or '')
                        if len({q.get('entity') for q in queries.values()})==1:lanchor=str(left.get('year') or '')
                        if abs(directed-abs(value))<.005 and lanchor in line and ((value<0 and re.search(r'低|少|下降',line)) or (value>0 and re.search(r'高|多|上升',line))):local_found=True
                    else:local_found=True
                elif _value_equal(m[1],value):local_found=True
                elif (entity and entity in line and not any(e in line for e in other_entities)
                      and not any(y in line for y in other_years)
                      and not re.search(r'减|差|高|低|增加|下降|上升|变化',line)
                      and not (scope.get('question_id') and len([q for q in queries.values() if q.get('entity')==scope.get('entity') and q.get('year')==scope.get('year') and q.get('question_id')])>1)):
                    mismatch=True
            if fact.get('rank') is not None and local_found:
                rank=str(fact['rank'])
                if entity in line and any(int(m[1]) != int(rank) for m in re.finditer(r'(?:排名|名次)(?:为|是|[:：])?(?:第)?(\d+)',line)):
                    errors.append('public_required_rank_mismatch:'+str(fact['key']))
                rank_line=line
                following=lines[i+1] if i+1<len(lines) else ''
                if not any(e in following for e in other_entities) and not any(y in following for y in other_years):rank_line+='\n'+following
                if not re.search(r'(?:第\s*'+rank+r'(?!\d)(?:\s*名)?|^\s*'+rank+r'[.、]|\|'+rank+r'\|)',rank_line):local_found=False
            found=found or local_found
        if not found:errors.append(('unconfirmed:public_unit_expression_unproved:' if unknown_unit else 'public_required_fact_missing:')+str(fact['key']))

        if mismatch:errors.append('public_required_fact_mismatch:'+str(fact['key']))
    errors.extend(_verify_public_comparison_claims(lines,plan,nodes))
    return list(dict.fromkeys(errors))

def _verify_public_comparison_claims(lines, plan, nodes):
    pass
    values={n.get('id'):n for n in nodes}
    queries={q['id']:q for q in plan.get('queries',[])}
    errors=[]
    def anchors(q, other):
        if q.get('entity')!=other.get('entity'):return [str(q.get('entity') or '')]
        if q.get('year')!=other.get('year'):return [str(q.get('year') or '')]
        if q.get('question_id')!=other.get('question_id'):
            from packages.semantic_query.catalogue import question_metric
            return [f"Q{q['question_id']}",f"第{q['question_id']}题",question_metric(q['question_id'])['name']]
        if q.get('dimension')!=other.get('dimension'):return [str(q.get('dimension') or '')]
        if q.get('school_filter')!=other.get('school_filter'):return [str(q.get('school_filter') or '')]
        labels={'mean':['均值','平均值'],'median':['中位数'],'max':['最高值','最大值'],'min':['最低值','最小值']}
        return labels.get(q.get('statistic'),[])
    for step in plan.get('steps',[]):
        if step.get('operation')!='subtract' or len(step.get('args',[]))!=2:continue
        lid,rid=step['args'];lq,rq=queries.get(lid,{}),queries.get(rid,{})
        lv,rv=values.get(lid,{}).get('value'),values.get(rid,{}).get('value')
        if not _numeric(lv) or not _numeric(rv):continue
        difference=lv-rv;la,ra=anchors(lq,rq),anchors(rq,lq)
        la=[a for a in la if a];ra=[a for a in ra if a]
        if not la or not ra:continue

        order=list(queries)
        if len(order)==2:
            for key,labels in ((lid,la),(rid,ra)):
                labels.extend(['前项','前者','第一项'] if order.index(key)==0 else ['后项','后者','第二项'])
        for error in _bound_comparison_claims('\n'.join(lines),la,ra,lv,rv):
            errors.append('public_'+error+':'+step['id'])
    return list(dict.fromkeys(errors))

def _bound_comparison_claims(text, left_labels, right_labels, left, right, *, expected_difference=None, expected_unit=None):
    pass
    answer=_compact(text).replace('－','-').replace('＝','=')

    from packages.semantic_query.fact_assertions import _SUBTRACTION_FORMULA
    answer=_SUBTRACTION_FORMULA.sub(lambda m:m[6]+(m[7] or m[4] or m[2] or ''),answer)
    labels={_compact(label):side for side,items in enumerate((left_labels,right_labels))
            for label in items if label}
    pattern='|'.join(re.escape(x) for x in sorted(labels,key=len,reverse=True))
    if not pattern:return []
    anchor='(?:'+pattern+')'
    errors=[]
    values=[float(left),float(right)]
    delta = values[0]-values[1] if expected_difference is None else float(expected_difference)
    def ordered_delta(subject, other):
        return delta if (subject, other)==(0,1) else -delta
    measures=r'(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*(?:分|台|种|堂|所|Mbps|百分点|%)'
    if expected_unit:
        measures=r'(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*'+re.escape(expected_unit)
    def direction(subject,other,word):
        expected=ordered_delta(subject,other)
        positive=word in {'高于','超过','大于','多于','高','多','较高','更高','最高','增加','上升','往上'}
        negative=word in {'低于','少于','小于','低','少','较低','更低','最低','下降','减少','往下'}
        return (positive and expected<=0) or (negative and expected>=0) or (word=='等于' and abs(expected)>1e-9)
    def relation_unit_matches(clause, end):
        if not expected_unit:return True
        measured=re.search(r'(?<![\d.])[+-]?\d+(?:\.\d+)?(分|台|种|堂|所|Mbps|百分点|%)',clause[end:])
        return not measured or measured[1]==expected_unit
    for clause in re.split(r'[。；;\n，,]',answer):
        if re.search(r'若|假如|如果|不能推断|不能确定|不代表|并非|不是',clause):continue
        for m in re.finditer('('+anchor+')(高于|低于|超过|少于|大于|小于|等于)('+anchor+')',clause):
            s,o=labels[m[1]],labels[m[3]]
            if s!=o and relation_unit_matches(clause,m.end()) and direction(s,o,m[2]):errors.append('comparison_direction_mismatch')
        for m in re.finditer('('+anchor+')(?:比|较)('+anchor+')(高|低|多|少)',clause):
            s,o=labels[m[1]],labels[m[2]]
            if s!=o and relation_unit_matches(clause,m.end()) and direction(s,o,m[3]):errors.append('comparison_direction_mismatch')

        superlative=r'(较高|更高|最高|较低|更低|最低)(?:的)?(?:一项|项|指标|数值|得分)?(?:是|为)?[：:“”"]*('+anchor+')'
        for m in re.finditer(superlative,clause):
            s=labels[m[2]]
            if direction(s,1-s,m[1]):errors.append('comparison_direction_mismatch')
        for m in re.finditer('('+anchor+')(?:这一项|该项|一项)?(较高|更高|最高|较低|更低|最低)',clause):
            s=labels[m[1]]
            if direction(s,1-s,m[2]):errors.append('comparison_direction_mismatch')
        mentions=list(re.finditer(anchor,clause))

        for i,m in enumerate(mentions):
            if i==0:continue
            prev=mentions[i-1];s,o=labels[prev[0]],labels[m[0]]
            if s==o:continue
            gap=clause[prev.end():m.start()];tail=clause[m.end():]
            subtract=bool(re.fullmatch(r'(?:年)?(?:的)?(?:减|-)',gap))
            comparative=bool(re.fullmatch(r'(?:年)?(?:较|比|相比于|相较于|相对)',gap))
            temporal=bool(re.fullmatch(r'(?:年)?(?:到|至)',gap) and re.search(r'从|由',clause[:prev.start()]))
            if not (subtract or comparative or temporal):continue
            nums=list(re.finditer(measures,tail))
            if not nums:continue
            expected=ordered_delta(o,s) if temporal else ordered_delta(s,o)
            word=re.search(r'变化|增加|上升|增长|提高|提升|升高|往上|下降|减少|降低|下滑|往下|高|低|多|少',tail)
            if comparative or temporal:
                if not word:continue
                if word[0] in {'增加','上升','增长','提高','提升','升高','往上','高','多'}:
                    if expected<=0:errors.append('comparison_direction_mismatch')
                    target=abs(expected)
                elif word[0] in {'下降','减少','降低','下滑','往下','低','少'}:
                    if expected>=0:errors.append('comparison_direction_mismatch')
                    target=abs(expected)
                else:target=expected
            else:target=expected
            if not _value_equal(nums[-1][1],target):
                errors.append('subtraction_order_mismatch' if subtract else 'comparison_value_mismatch')
    return list(dict.fromkeys(errors))

def _value_equal(token, value):
    return _numeric(value) and abs(float(token) - float(value)) < 0.005000001

def _distinct_labels(row, other):
    pass
    name, other_name = _compact(row.get("metric_name")), _compact(other.get("metric_name"))
    labels = {f"Q{row.get('question_id')}", f"第{row.get('question_id')}题"}

    labels.update(part for part in re.findall(r'[\u4e00-\u9fff]{2,8}类',name)
                  for part in [part[-3:]] if part not in other_name)
    for size in range(len(name), 3, -1):
        for start in range(len(name) - size + 1):
            part = name[start:start + size]
            if part not in other_name:
                labels.add(part)
    return labels

def _check_named_values(text, slots, failure):
    pass
    answer = _compact(text)
    labels = []
    for index, slot in enumerate(slots):
        for label in sorted(slot["labels"], key=len, reverse=True):
            for match in re.finditer(re.escape(label), answer, re.I):
                labels.append((match.start(), match.end(), index))

    labels = sorted(set(labels))
    found = set()
    errors = []
    columns = {}
    table_units = {}
    for line in answer.splitlines():
        if not line.startswith('|'):
            continue
        cells = line.strip('|').split('|')
        identified = {}
        for i,cell in enumerate(cells):
            matches = {index for index,slot in enumerate(slots)
                       if any(re.search(re.escape(label) + (r'(?!\d)' if label[-1:].isdigit() else ''),cell,re.I)
                              for label in slot['labels'])}
            if len(matches) == 1:
                identified[i] = matches.pop()
        if len(identified) == len(slots) and not any(
                re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:%|分)?',cell) for cell in cells):
            columns = identified
            table_units = {i:('%' if '%' in cell else '分') for i,cell in enumerate(cells) if '%' in cell or '分' in cell}
            continue
        if not identified and not any(re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:%|分)?',cell) for cell in cells):
            for i,cell in enumerate(cells):
                if '%' in cell or '分' in cell:
                    table_units[i]='%' if '%' in cell else '分'
        if columns:
            for column,index in columns.items():
                if column >= len(cells):
                    continue
                m = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)(%|分)?', cells[column])
                if m:
                    if _value_equal(m[1],slots[index]['value']) and (m[2] or table_units.get(column)) == slots[index]['unit']:
                        found.add(index)
                    else:
                        errors.append(failure+'_mismatch')
        elif len(identified) == 1:
            label_column,index=next(iter(identified.items()))
            measures=[(i,re.fullmatch(r'([+-]?\d+(?:\.\d+)?)(%|分)?',cell)) for i,cell in enumerate(cells) if i!=label_column]
            measures=[(i,m) for i,m in measures if m]
            if len(measures)==1:
                column,m=measures[0]
                if _value_equal(m[1],slots[index]['value']) and (m[2] or table_units.get(column)) == slots[index]['unit']:
                    found.add(index)
                else:
                    errors.append(failure+'_mismatch')
    for m in re.finditer(r"(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*(%|(?:个)?百分点|分)(?![\u4e00-\u9fff])", answer):
        if answer[answer.rfind('\n',0,m.start())+1:m.start()].startswith('|'):
            continue
        prior = [label for label in labels if label[1] <= m.start()
                 and m.start() - label[1] <= 85
                 and not re.search(r"[。；;\n]", answer[label[1]:m.start()])]
        if not prior:
            continue
        best_start = max(label[0] for label in prior)
        indices = {label[2] for label in prior if label[0] == best_start}
        if len(indices) != 1:
            continue
        index = indices.pop()
        slot = slots[index]
        label = max((label for label in prior if label[2] == index), key=lambda label: label[0])
        gap = answer[label[1]:m.start()]

        named_operation = any(other[2] != index and other[1] <= operand[0]
                              and re.fullmatch(r"\s*(?:减|－|−|-)\s*", answer[other[1]:operand[0]])
                              for operand in prior if operand[2] == index and operand[1] == label[1]
                              for other in labels)
        comparative_magnitude = (bool(re.fullmatch(r'(?:年)?(?:更|较)?(?:高|低|多|少)(?:了|约)?',gap))
            and any(other[2]!=index and other[1]<=label[0]
                    and re.fullmatch(r'(?:年)?(?:比|较)',answer[other[1]:label[0]]) for other in labels))
        if comparative_magnitude:

            continue
        if named_operation or re.search(r"分别|减|差|变化|增加|减少|上升|下降|增长|提高|提升|升高|降低|下滑|[=－-]", gap) or re.search(r"减(?:第[一二]项|前项|后项|前者|后者)$", answer[:label[1]]):
            continue
        if _value_equal(m[1], slot["value"]) and m[2].replace("个", "") == slot["unit"]:
            found.add(index)
        else:
            errors.append(failure + "_mismatch")
    if len(found) != len(slots):

        for clause in re.split(r"[。；;\n]", answer):
            if "分别" not in clause:
                continue
            before, after = clause.split("分别", 1)
            positions = [min((before.find(label) for label in slot["labels"] if label in before), default=-1)
                         for slot in slots]
            measures = list(re.finditer(r"([+-]?\d+(?:\.\d+)?)\s*(%|(?:个)?百分点|分)", after))
            if len(measures) < len(slots):
                shared = re.fullmatch(r'[^0-9+-]*([+-]?\d+(?:\.\d+)?(?:[、,，和与]+[+-]?\d+(?:\.\d+)?)*)[（(]?(%|分)[）)]?.*', after)
                if shared:
                    numbers = list(re.finditer(r'[+-]?\d+(?:\.\d+)?', shared[1]))
                    measures = [(m[0], shared[2]) for m in numbers]
            if min(positions, default=-1) >= 0 and len(set(positions)) == len(slots) and len(measures) >= len(slots):
                order = sorted(range(len(slots)), key=positions.__getitem__)
                if all(_value_equal(measures[i][0] if isinstance(measures[i],tuple) else measures[i][1], slots[index]["value"])
                       and (measures[i][1] if isinstance(measures[i],tuple) else measures[i][2]).replace("个", "") == slots[index]["unit"]
                       for i, index in enumerate(order)):
                    found.update(order)
                else:
                    errors.append(failure + "_mismatch")
        if len(found) != len(slots):
            errors.append(failure + "_missing")
    return list(dict.fromkeys(errors))

def _verify_kind_count_difference(text, rows, derived):
    pass
    if (derived.get('operation') != 'subtract' or derived.get('unit') != '种'
            or not _numeric(derived.get('value'))):
        return []
    by_qid = {row.get('question_id'): row.get('target_value') for row in rows}
    left, right = by_qid.get(derived.get('left_qid')), by_qid.get(derived.get('right_qid'))
    if not _numeric(left) or not _numeric(right):
        return ['item_set_count_difference_evidence_missing']
    answer = _compact(text)
    def count_equal(token, value):
        return _numeric(value) and abs(float(token) - float(value)) < 1e-9
    number = r'([+-]?\d+(?:\.\d+)?)'
    unit = r'(种|人次|次|%|分)?'
    errors = []
    formula = number + unit + r'[-－]' + number + unit + r'[=＝]' + number + unit
    formula_matches = list(re.finditer(formula, answer))
    for match in formula_matches:
        if (not count_equal(match[1], left) or not count_equal(match[3], right)
                or not count_equal(match[5], derived['value'])
                or any(match[i] not in (None, '种') for i in (2, 4, 6))):
            errors.append('item_set_count_difference_formula_mismatch')
    scalar = r'(?:差值|数量差|差为|差是|第一项减第二项|前项减后项|前者减后者)(?:的差值)?[：:=为是]*' + number + unit
    for match in re.finditer(scalar, answer):

        if re.match(r'[-－][+-]?\d+(?:\.\d+)?(?:种)?[=＝]', answer[match.end():]):
            continue
        if not count_equal(match[1], derived['value']) or match[2] not in (None, '种'):
            errors.append('item_set_count_difference_claim_mismatch')
    supported = [left, right, derived['value']]
    for match in re.finditer(r'(?<![\d.])' + number + r'(种|人次|次|%|分)', answer):
        token = match[1]
        if token.startswith('-') and any(match.end(1) == calc.end(3) and match.start(1) == calc.start(3)-1 for calc in formula_matches):
            token = token[1:]
        if match[2] != '种' or not any(count_equal(token, value) for value in supported):
            errors.append('item_set_count_measurement_unsupported')
    return list(dict.fromkeys(errors))

def verify_bound_facts(text, evidence, spec):
    pass
    if evidence.get("fact_data_status") == "NO_DATA":
        return []
    if spec.get("operation") == "region_longitudinal":
        if not spec.get('chart_requested') and not any(str(evidence.get('year_' + side)) + '年' in text for side in ('a','b')):
            return []
        slots = [{"labels": {str(evidence.get("year_" + side)) + "年", str(evidence.get("year_" + side))},
                  "value": evidence.get("total_score_" + side, evidence.get("comparison_value_" + side)), "unit": "分"} for side in ("a", "b")]
        if any(not _numeric(slot['value']) for slot in slots):
            return ["longitudinal_period_evidence_missing"]
        errors=_check_named_values(text, slots, "longitudinal_period_value")
        for error in _bound_comparison_claims(text,
                list(slots[0]['labels'])+['前期','前者','第一期'],
                list(slots[1]['labels'])+['后期','后者','第二期'],
                slots[0]['value'],slots[1]['value']):
            errors.append('longitudinal_'+error)
        return list(dict.fromkeys(errors))
    if spec.get("operation") != "school_fact" or spec.get("granularity") != "item_set":
        return []
    rows = evidence.get("item_results") or []
    if (len(rows) == 2 and all(row.get('unit') == '种' and row.get('value_type') == 'count'
                               and row.get('scale') == 'raw_count' for row in rows)):
        return _verify_kind_count_difference(text, rows, evidence.get('derived_result') or {})
    if len(rows) != 2 or any(row.get("unit") != "%" for row in rows):
        return []
    slots = []
    for index, row in enumerate(rows):
        labels = _distinct_labels(row, rows[1-index])
        labels.update({"第一项" if index == 0 else "第二项", "前项" if index == 0 else "后项",
                       "前者" if index == 0 else "后者"})
        slots.append({"labels": labels, "value": row.get("target_value"), "unit": "%"})
    errors = _check_named_values(text, slots, "item_set_percent_value")
    derived = evidence.get("derived_result") or {}
    if derived.get("unit") == "百分点":
        answer = _compact(text)
        by_qid = {row.get('question_id'):row.get('target_value') for row in rows}
        left, right = by_qid.get(derived.get('left_qid')), by_qid.get(derived.get('right_qid'))
        formula = r'([+-]?\d+(?:\.\d+)?)%\s*[-－−]\s*([+-]?\d+(?:\.\d+)?)%\s*[=＝]\s*([+-]?\d+(?:\.\d+)?)((?:个)?百分点|%)'
        for calc in re.finditer(formula, answer):
            if (not _value_equal(calc[1],left) or not _value_equal(calc[2],right)
                    or not _value_equal(calc[3],derived.get('value')) or calc[4]=='%'):
                errors.append('item_set_percentage_point_formula_mismatch')
        row_labels = {row.get('question_id'): _distinct_labels(row, rows[1-i]) for i,row in enumerate(rows)}
        def names(qid):
            return '(?:' + '|'.join(re.escape(v) for v in sorted(row_labels.get(qid,()),key=len,reverse=True)) + ')'
        for lqid,rqid in [(derived.get('left_qid'),derived.get('right_qid')),
                          (derived.get('right_qid'),derived.get('left_qid'))]:
            named = names(lqid) + r'\s*(?:减|－|−|-)\s*' + names(rqid) + r'(?:的(?:差值|比例差))?[：:=为是]*([+-]?\d+(?:\.\d+)?)(%|(?:个)?百分点)'
            for calc in re.finditer(named,answer):
                if (lqid != derived.get('left_qid') or calc[2]=='%'
                        or not _value_equal(calc[1],derived.get('value'))):
                    errors.append('item_set_named_subtraction_mismatch')
        for match in re.finditer(r"(?:差值|比例差|相差|差为|差是|第一项减第二项|前项减后项|前者减后者)(?:的比例差)?[：:=为是]*([+-]?\d+(?:\.\d+)?)\s*(%|(?:个)?百分点)", answer):

            if match[2]=='%' and re.match(r'\s*[-－−]\s*[+-]?\d+(?:\.\d+)?%\s*[=＝]\s*[+-]?\d+(?:\.\d+)?(?:个)?百分点',answer[match.end():]):
                continue
            if match[2] == "%" or not _value_equal(match[1], derived.get("value")):
                errors.append("item_set_percentage_point_claim_mismatch")
    return list(dict.fromkeys(errors))

def verified_fact_repair(evidence, spec):
    pass
    if set(spec.get("required_outputs") or []) != {"direct_answer"}:
        return None
    if spec.get("operation") != "school_fact" or spec.get("granularity") != "item_set":
        return None
    rows, derived = evidence.get("item_results") or [], evidence.get("derived_result") or {}
    if (len(rows) != 2 or any(row.get("unit") != "%" or not _numeric(row.get("target_value")) for row in rows)
            or derived.get("operation") != "subtract" or derived.get("unit") != "百分点"
            or [row.get("question_id") for row in rows] != list(spec.get("indicator_ids") or [])
            or [row.get("question_id") for row in rows] != [derived.get("left_qid"), derived.get("right_qid")]
            or not _numeric(derived.get("value"))
            or not _value_equal(str(rows[0]['target_value'] - rows[1]['target_value']), derived['value'])):
        return None
    parts = [f"第{row['question_id']}题“{row.get('metric_name') or ''}”：{row['target_value']:g}%。" for row in rows]
    parts.append(f"前项减后项的比例差为{derived['value']:+g}个百分点。")
    return "".join(parts)

def verify_single_value(text: str, evidence: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    if not spec.get("single_value_only") or evidence.get("fact_data_status") == "NO_DATA":
        return []
    facts = required_facts(evidence, spec)
    if len(facts) != 1 or not facts[0]["available"]:
        return ["required_single_value_evidence_missing"]
    compact = re.sub(r"[\s*`]", "", str(text or "")).replace("−", "-")
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(?:分)?[。.]?", compact)
    if not match:
        return ["single_value_only_scope_mismatch"]
    if float(match[1]) != float(f"{facts[0]['value']:.2f}"):
        return ["required_single_value_mismatch"]
    return []

def measurement_values(evidence: Dict[str, Any]) -> List[float]:
    pass
    keys = {
        "target_value", "normalized_score", "total_score", "max_score", "min_score",
        "median_score", "mean_median_difference", "target_dimension_median_score",
        "score_a", "score_b", "score_diff", "comparison_value_a", "comparison_value_b",
        "total_score_a", "total_score_b", "total_diff", "overall_score_a", "overall_score_b",
        "diff", "pct_change", "target_dimension_score", "target_dimension_score_a",
        "target_dimension_score_b", "school_count", "school_count_a", "school_count_b",
        "answer_count", "answer_count_a", "answer_count_b", "requested_school_count",
        "requested_school_count_a", "requested_school_count_b", "requested_answer_count",
        "requested_answer_count_a", "requested_answer_count_b", "score", "value",
        "rank", "target_rank", "returned_school_count", "answered", "total_metrics",
        "catalogue_question_count", "valid_answer_count", "valid_count",
    }
    containers = {"item_results", "derived_result", "dimensions", "dimensions_comparison",
                  "dimension_comparison", "dimension_deltas", "ranking_items",
                  "target_dimension_scores", "target_dimensions_detail", "task_outputs"}
    values = []
    def visit(obj):
        if isinstance(obj, list):
            for row in obj:
                visit(row)
        elif isinstance(obj, dict):
            for key, value in obj.items():
                if key in keys and _numeric(value):
                    values.append(value)
                elif key in containers:
                    visit(value)
                elif key == 'value' and isinstance(value,list):
                    visit(value)
    visit(evidence)
    return values

def comparison_measurements_by_unit(evidence: Dict[str, Any], spec: Dict[str, Any]):
    pass
    if spec.get("operation") != "region_compare" or spec.get("target_stat") == "count":
        return None
    def values(keys):
        return [evidence[key] for key in keys if _numeric(evidence.get(key))]
    scores = values(("total_score_a", "total_score_b", "diff"))
    if _numeric(evidence.get("diff")):
        scores.append(abs(evidence["diff"]))
    requested = set(spec.get("requested_result_statistics") or [])
    counts = bool(requested & {"school_count", "school_count_a", "school_count_b"})
    answers = bool(requested & {"answer_count", "answer_count_a", "answer_count_b"})
    return {
        "分": scores,
        "所": values(("school_count_a", "school_count_b",
                       "requested_school_count_a", "requested_school_count_b")) if counts else [],
        "项": values(("answer_count_a", "answer_count_b",
                       "requested_answer_count_a", "requested_answer_count_b")) if answers else [],
    }
