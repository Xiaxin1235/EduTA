from __future__ import annotations
import re
from itertools import combinations
import copy
import math
from decimal import Decimal, ROUND_HALF_EVEN

NUMBER = r'(?<![A-Za-z\d.])([+-]?\d+(?:\.\d+)?)(?![\d.])'
MEASURE = re.compile(NUMBER+r'\s*(个学科|学科|人/台|台/人|Mbps|个百分点|百分点|课时|人次|种|分|台|所|人|个|项|名|位|%|次|间|条|堂|门)')
STAT_LABELS = {
    'total_score':['均值','均分','平均得分','平均分','综合分','综合得分','总体得分','整体得分'],
    'median_score':['中位数','中位分','中间位置'],
    'target_dimension_median_score':['中位数','中位分','中间位置'],
    'target_dimension_score':['均值','均分','平均得分','平均分'],
    'max_score':['最高分','最大值','最高得分'],
    'min_score':['最低分','最小值','最低得分'],
    'school_count':['有效学校数','有效学校','学校数','学校数量'],
    'answer_count':['有效作答数','作答数','作答条数'],
    'mean_median_difference':['均值减中位数','均分减中位数'],
}

def compact(text):
    return re.sub(r'[ \t\r*`_（）()]','',str(text or '')).replace('−','-').replace('－','-').replace('＝','=').replace('％','%')

def unit_alias(unit):return '学科' if unit=='个学科' else unit

def structural_count_supported(match, text, evidence, spec):
    pass
    if match[2] != '个' or not re.match(r'\s*维度', text[match.end():]):
        return None
    field = 'dimensions_comparison' if spec.get('operation') in {'region_compare','school_compare'} else 'dimensions'
    rows = evidence.get(field) or []
    names = [row.get('dimension') for row in rows if isinstance(row, dict)]
    if not names or len(names) != len(rows) or any(not name for name in names) or len(set(names)) != len(names):
        return False
    return match[1].isdigit() and int(match[1]) == len(names)

def equal(token, value, unit):
    tolerance=.005000001 if unit in {'分','%','百分点','Mbps','人/台','台/人'} else 1e-9
    return type(value) in {int,float} and abs(float(token)-float(value))<tolerance

def aliases(fact):
    key=fact['key']; out=list(STAT_LABELS.get(key,[]))
    if key.endswith(('_a','_b')):out+=STAT_LABELS.get(key[:-2],[])
    if fact.get('label'):out.append(compact(fact['label']))
    if key.startswith('dimension:'):out.append(key.split(':',1)[1])
    qid=fact.get('question_id') or (fact.get('scope') or {}).get('question_id')
    if qid:out += [f"Q{qid}",f"第{qid}题"]
    return [x for x in out if x]

def entity_aliases(fact):
    name=compact(fact.get('entity'))
    short=compact(str(fact.get('entity') or '').split(' ')[-1])
    ordinary=re.sub(r'(?:壮族自治区|回族自治区|维吾尔自治区|自治区|省|市)$','',short)
    return sorted({x for x in (name,short,ordinary,*(fact.get('entity_aliases') or [])) if x},key=len,reverse=True)

def scalar_inventory(evidence,spec):
    from packages.semantic_query.required_facts import required_facts
    facts=required_facts(evidence,spec)

    extra=[]
    if spec.get('operation') in {'region_eval','school_eval'}:
        for key in ('total_score','median_score','max_score','min_score','school_count','answer_count'):
            if any(f['key']==key for f in facts) or type(evidence.get(key)) not in {int,float}:continue
            extra.append(dict(key=key,value=evidence[key],unit='所' if key=='school_count' else '条' if key=='answer_count' else '分',available=True,entity=evidence.get('short_name') or evidence.get('region_name') or evidence.get('school_name'),period=evidence.get('year'),source_field=key))
    return facts,extra

def measurements_by_unit(evidence,spec):
    pass
    operation=spec.get('operation')
    if operation not in {'region_eval','school_eval','school_fact','public_task','region_compare'}:return None
    facts,extra=scalar_inventory(evidence,spec)
    output={}
    for f in facts+extra:
        if not f.get('available') or not f.get('unit'):continue
        output.setdefault(f['unit'],[]).append(f['value'])
        if f.get('operation')=='subtract':output[f['unit']].append(abs(f['value']))
    if operation=='region_compare':
        from packages.semantic_query.required_facts import comparison_measurements_by_unit
        prior=comparison_measurements_by_unit(evidence,spec)
        if prior is not None:return prior
    return output or None

def executed_population_scope(result):
    pass
    scope=result.get('execution_scope') or {}
    if 'school_filter' not in scope:return None
    return {key:copy.deepcopy(scope[key]) for key in
            ('query_template','region','region_a','region_b','period','school_filter') if key in scope}

_POPULATION_NOUN = re.compile(
    r'(?:本次|本轮|当前|本地区|该地区|两地|两市)?(?:(?:所|已)?'
    r'(?:纳入|参与|参加|进入|用于)(?:本次|本轮)?(?:统计|计算|评估|评价|比较|样本)的?'
    r'|全部|所有|全体|各|这些|上述|有效|受评|参评|样本)学校'
    r'(?=20\d{2}|[：:，,。；;\n/]|(?:为|有|共|数量|学校数|得分|均分|等权)|[+-]?\d|$)')

def population_scope_text(text,evidence,spec):
    pass
    errors=[];scope=evidence.get('executed_population_scope')
    if scope is None:scope=executed_population_scope(evidence)
    def mask(m):
        if re.search(r'全部|所有|全体',m[0]):
            if spec.get('operation') not in {'region_eval','region_compare'}:
                errors.append('request_population_scope_mismatch')
            elif scope is None:
                errors.append('unconfirmed:population_scope_evidence_unavailable')
            elif scope.get('school_filter') not in (None,'',{}):
                errors.append('request_population_scope_mismatch')
        return '#population#'
    return _POPULATION_NOUN.sub(mask,text),list(dict.fromkeys(errors))

def named_scope_matches(text):
    return list(re.finditer(r'[\u4e00-\u9fff]{2,12}(?:市|省|区|县)|'
                           r'[\u4e00-\u9fff]{2,35}(?:小学|中学|学校)'
                           r'(?=20\d{2}|[：:，,/]|(?:为|有)?[+-]?\d)',text))

def verify_explicit_scope(text,evidence,spec):
    pass
    from packages.semantic_query.required_facts import required_facts
    facts=required_facts(evidence,spec)
    if not facts:return []
    known=set()
    for f in facts:
        known.update(entity_aliases(f))
        scope=f.get('scope') or {}
        for key in ('province','city','district'):
            value=(scope.get('research_constraints') or {}).get(key)
            if value:known.add(compact(value))
    for key in ('region_name','short_name','school_name','region_name_a','region_name_b','school_name_a','school_name_b'):
        if evidence.get(key):known.update(compact(v) for v in str(evidence[key]).split(' '))
    scoped_answer,errors=population_scope_text(compact(text),evidence,spec)
    for clause in re.split(r'[。；;\n]',scoped_answer):
        if not MEASURE.search(clause):continue
        remaining=clause

        for f in facts:
            qid=f.get('question_id') or (f.get('scope') or {}).get('question_id')
            if qid:
                from packages.semantic_query.catalogue import question_metric
                name=compact(question_metric(qid).get('name') or '')
                for label in (name,re.sub(r'[.、，,]','',name)):
                    if label:remaining=remaining.replace(label,'#metric#')
        for name in sorted(known,key=len,reverse=True):remaining=remaining.replace(name,'')
        for m in named_scope_matches(remaining):
            if re.search(r'不是|不含|不包括|排除',remaining[max(0,m.start()-8):m.start()]):continue
            errors.append('unconfirmed:extra_named_scope:'+m[0])
    return list(dict.fromkeys(errors))

def verify_scalar_slots(text,evidence,spec):
    operation=spec.get('operation')
    if operation not in {'region_eval','school_eval','school_fact','region_compare'} or operation=='school_fact' and spec.get('granularity')=='item_set':return []
    if evidence.get('fact_data_status')=='NO_DATA':return []
    required,extra=scalar_inventory(evidence,spec)
    if not required:return ['unconfirmed:request_required_facts_empty']
    slots=required+extra;answer=compact(text);errors=[];found=set()
    for f in required:
        if f.get('evidence_conflict'):errors.append('evidence_conflict:'+f['key'])
        elif not f['available']:errors.append('unconfirmed:request_required_evidence_unavailable:'+f['key'])
    periods={str(f['period']) for f in required if f.get('period')}
    if any(y not in periods for y in re.findall(r'(?<!\d)(?:19|20)\d{2}(?=年)',answer)):
        errors.append('request_period_mismatch')
    dims=set(spec.get('target_dimensions') or ([evidence.get('target_dimension')] if evidence.get('target_dimension') else []))
    if spec.get('granularity') in {'dimension','dimension_pair'}:
        from packages.semantic_query.runtime import _DIMENSIONS
        if any(d in answer for d in _DIMENSIONS-dims):errors.append('request_dimension_scope_mismatch')
    formula_spans=[m.span() for m in _SUBTRACTION_FORMULA.finditer(answer)]
    for m in MEASURE.finditer(answer):
        if any(start<=m.start()<end for start,end in formula_spans):continue
        structural = structural_count_supported(m, answer, evidence, spec)
        if structural is not None:
            if not structural:errors.append('request_dimension_cardinality_mismatch')
            continue
        prefix=answer[:m.start()];clause=re.split(r'[。；;\n]',prefix)[-1]
        unit=unit_alias(m[2])

        if re.search(r'[=]|减|差值|分差|数量差|相差|差距|绝对(?:差|值)|差(?:了|为|是)?$|(?:比|较).{0,22}(?:高|低|多|少)(?:了|约)?$',clause):continue
        if re.match(r'\s*[-+]\s*\d',answer[m.end():]):continue
        labels=[]
        for i,f in enumerate(slots):
            for label in aliases(f):
                for hit in re.finditer(re.escape(label),clause,re.I):
                    labels.append((hit.start(),len(label),i))
        if labels:
            best=max((pos,size) for pos,size,_ in labels)
            candidates=[i for pos,size,i in labels if (pos,size)==best]
        else:candidates=[i for i,f in enumerate(slots) if f.get('available') and unit_alias(f.get('unit') or '')==unit]
        mentions=[(hit.start(),i) for i,f in enumerate(slots) for name in entity_aliases(f) for hit in re.finditer(re.escape(name),prefix)]
        if mentions:
            nearest=max(pos for pos,_ in mentions);entities={slots[i].get('entity') for pos,i in mentions if pos==nearest}
            candidates=[i for i in candidates if slots[i].get('entity') in entities]
        elif len({f.get('entity') for f in required if f.get('entity')})>1:

            if not answer[answer.rfind('\n',0,m.start())+1:m.start()].startswith('|'):
                errors.append('unconfirmed:request_measurement_object_binding');continue
        known=[a for f in slots for a in entity_aliases(f)]
        scope_text,population_errors=population_scope_text(clause,evidence,spec)
        errors.extend(population_errors)
        for name in sorted(set(known),key=len,reverse=True):scope_text=scope_text.replace(name,'')
        foreign=named_scope_matches(scope_text)
        if foreign:
            errors.append('request_named_object_mismatch');continue

        matches=[i for i in candidates if slots[i].get('available') and unit_alias(slots[i].get('unit') or '')==unit and equal(m[1],slots[i]['value'],unit)]
        if matches:
            found.update(i for i in matches if i<len(required))
            displayed=Decimal(m[1])
            if not any(displayed==Decimal(str(slots[i]['value'])) or displayed.as_tuple().exponent>=Decimal(str(slots[i]['value'])).as_tuple().exponent for i in matches):
                errors.append('unconfirmed:request_operand_precision_unavailable')
        elif candidates:
            errors.append('request_named_value_or_unit_mismatch:'+slots[candidates[0]]['key'])
        else:
            errors.append('unconfirmed:request_measurement_binding')
    if len(required)==1 and re.fullmatch(r'[+-]?\d+(?:\.\d+)?[。.]?',answer) and equal(answer.rstrip('。.'),required[0]['value'],required[0].get('unit')):found.add(0)
    if any(line.startswith('|') for line in answer.splitlines()):
        from packages.semantic_query.required_facts import _check_named_values
        entities={f.get('entity') for f in required}
        for i,f in enumerate(required):
            if f.get('operation') or not f.get('available') or f.get('unit') not in {'分','%'}:continue
            names=entity_aliases(f) if len(entities)>1 else aliases(f)
            if not names:continue
            table_errors=_check_named_values(answer,[dict(labels=set(names),value=f['value'],unit=f['unit'])],'request_table_value')
            if not table_errors:found.add(i)
            elif 'request_table_value_mismatch' in table_errors:errors.append('request_table_value_mismatch:'+f['key'])
    for i,f in enumerate(required):
        if f.get('operation')=='subtract':
            if any(equal(m[1],f['value'],m[2]) for m in MEASURE.finditer(answer) if re.search(r'减|差|[=]',answer[max(0,m.start()-60):m.start()])):found.add(i)
            allowances,conflicts=arithmetic_assertions(answer,evidence,spec)
            if not conflicts and any(p['directed'] and (equal(str(p['proof']['value']),f['value'],f['unit']) or equal(str(-p['proof']['value']),f['value'],f['unit'])) for p in allowances):found.add(i)
        if f['available'] and i not in found:errors.append('request_required_value_missing:'+f['key'])
    if any(f['available'] for f in required) and re.search(r'无法给出|无可用数据|所需证据不可用|暂无可核验|没有可核验',answer):errors.append('answer_denies_available_evidence')
    return list(dict.fromkeys(errors))

def executed_arithmetic_precision(result):
    pass
    if result.get('object_type')!='region_compare' or result.get('target_stat')=='count':return None
    if result.get('difference_basis')!='raw_means_then_round':return None
    fields=['total_score_a','total_score_b']
    values=[result.get(k) for k in fields+['diff']]
    if not all(type(v) in {int,float} and math.isfinite(v) for v in values):return None
    verification=result.get('contract_verification') or {}
    return dict(operation='subtract',source_fields=fields,result_field='diff',
                operands=values[:2],value=values[2],unit='分',basis='raw_means_then_round',
                display_decimals=2,result_decimals=2,
                entities=[result.get('region_name_a'),result.get('region_name_b')],period=result.get('year'),
                dimension=result.get('target_dimension'),aggregation=result.get('aggregation'),statistic=result.get('target_stat'),
                execution_scope=copy.deepcopy(result.get('execution_scope')),
                verification_status=verification.get('status'),
                comparison_direction=result.get('comparison_direction'))

def precision_errors(evidence):
    precision=evidence.get('executed_arithmetic_precision')
    if not precision:return []
    if precision.get('verification_status')=='FAIL':
        return ['evidence_conflict:arithmetic_execution_verification']
    if precision.get('verification_status') not in (None,'PASS'):
        return ['unconfirmed:arithmetic_execution_verification']
    if precision.get('comparison_direction')!='primary_minus_secondary':
        return ['unconfirmed:arithmetic_execution_direction']
    scope=precision.get('execution_scope') or {}
    if scope.get('query_template')!='region_compare_v1' or str(scope.get('period'))!=str(precision.get('period')):
        return ['unconfirmed:arithmetic_execution_scope']
    if [str(scope.get(k) or '').split(' ')[-1] for k in ('region_a','region_b')]!=precision.get('entities'):
        return ['evidence_conflict:arithmetic_execution_object_binding']
    for scope_key,field in (('dimension','target_dimension'),('aggregation','aggregation'),('statistic','target_stat')):
        if scope_key in scope and scope[scope_key]!=evidence.get(field):
            return ['evidence_conflict:arithmetic_execution_'+scope_key+'_binding']
    left,right=map(lambda v:Decimal(str(v)),precision['operands'])

    if abs(Decimal(str(precision['value']))-(left-right))>=Decimal('0.015'):
        return ['evidence_conflict:arithmetic_precision_binding']
    for field,value in zip(precision['source_fields']+['diff'],precision['operands']+[precision['value']]):
        if evidence.get(field)!=value:return ['evidence_conflict:arithmetic_source_binding']
    if precision.get('entities')!=[evidence.get('region_name_a'),evidence.get('region_name_b')] or str(precision.get('period'))!=str(evidence.get('year')):
        return ['evidence_conflict:arithmetic_scope_binding']
    return []

_FORMULA_NUMBER=r'[+-]?\d+(?:\.\d+)?'
_FORMULA_UNIT=r'(?:个学科|学科|人/台|台/人|Mbps|百分点|课时|人次|种|分|台|所|人|个|项|名|位|%|次|间|条|堂)'
_SUBTRACTION_FORMULA=re.compile(
    r'(?<![A-Za-z\d.])('+_FORMULA_NUMBER+r')('+_FORMULA_UNIT+r')?\s*(?:-|减)\s*'
    r'('+_FORMULA_NUMBER+r')('+_FORMULA_UNIT+r')?\s*[，,]?\s*(=|≈|等于)\s*'
    r'('+_FORMULA_NUMBER+r')('+_FORMULA_UNIT+r')?')

def _subtraction_labels(proof,facts):
    pass
    x,y=proof['left'],proof['right']
    if x.get('entity')!=y.get('entity'):la,ra=entity_aliases(x),entity_aliases(y)
    elif x.get('period')!=y.get('period'):la,ra=[str(x.get('period'))+'年'],[str(y.get('period'))+'年']
    else:la,ra=aliases(x),aliases(y)
    if len(facts)==2:
        la+=['前者','前项','第一项'] if facts.index(x)==0 else ['后者','后项','第二项']
        ra+=['前者','前项','第一项'] if facts.index(y)==0 else ['后者','后项','第二项']
    return la,ra

def verify_numeric_subtractions(answer,proofs):
    pass
    allowed=[];errors=[];facts=[]
    for p in proofs:
        for f in (p['left'],p['right']):
            if f not in facts:facts.append(f)

    annotation=re.compile(r'('+_FORMULA_NUMBER+r')('+_FORMULA_UNIT+r')?\(([^()]+)\)(?=\s*(?:-|=|≈))')
    for m in annotation.finditer(answer):
        label=m[3];candidates=list(facts);explicit=False
        entities={f.get('entity') for f in facts if any(n in label for n in entity_aliases(f))}
        if entities:candidates=[f for f in candidates if f.get('entity') in entities];explicit=True
        years=re.findall(r'(?<!\d)((?:19|20)\d{2})(?:年|/|$)',label)
        if years:candidates=[f for f in candidates if str(f.get('period')) in years];explicit=True
        qids=[a or b for a,b in re.findall(r'(?:[Qq](\d+)|第(\d+)题)',label)]
        if qids:candidates=[f for f in candidates if str(f.get('question_id') or (f.get('scope') or {}).get('question_id')) in qids];explicit=True
        distinct=[f for f in candidates if any(n in label for n in aliases(f))]
        if distinct:candidates=distinct;explicit=True
        token=m[1]

        if token.startswith('-') and answer[max(0,m.start()-1):m.start()]==')':token=token[1:]
        matched=[f for f in candidates if equal(token,f['value'],unit_alias(m[2] or f.get('unit') or ''))]
        if explicit and not matched:errors.append('arithmetic_annotated_operand_mismatch')
        elif not explicit or len(matched)!=1:errors.append('unconfirmed:arithmetic_annotated_operand_binding')
        elif m[2] and unit_alias(m[2])!=unit_alias(matched[0].get('unit')):errors.append('arithmetic_formula_unit_mismatch')
    answer=annotation.sub(lambda m:m[1]+(m[2] or ''),answer)
    for m in _SUBTRACTION_FORMULA.finditer(answer):
        a,b,c=map(Decimal,(m[1],m[3],m[6]));computed=a-b
        clause=re.split(r'[。；;\n]',answer[:m.start()])[-1]+answer[m.end():].split('。')[0].split('\n')[0]
        declared=re.search(r'(?:保留|精确到|舍入到)(\d+)位(?:小数)?',clause)
        if declared and int(declared[1])!=-c.as_tuple().exponent:
            errors.append('arithmetic_declared_result_precision_mismatch')
        rounding=m[5]=='≈' or bool(re.search(r'四舍五入|保留\d+位|舍入到',clause))
        units=[unit_alias(u) for u in (m[2],m[4],m[7]) if u]
        pairs=[p for p in proofs if not units or all(u==p['unit'] for u in units)]
        def operand_matches(token,value):
            actual=Decimal(str(value));display=Decimal(token)
            return display==actual or (rounding and actual.quantize(Decimal(1).scaleb(display.as_tuple().exponent),rounding=ROUND_HALF_EVEN)==display)
        prefix=re.split(r'[。；;\n，,]',answer[:m.start()])[-1]
        header=re.fullmatch(r'(?:计算式|计算|算式|按)?(.+?)(?:减|-)(.+?)[:：]',prefix)
        named_pairs=None
        if header:

            named_pairs=[p for p in proofs if any(n in header[1] for n in _subtraction_labels(p,facts)[0])
                         and any(n in header[2] for n in _subtraction_labels(p,facts)[1])]
            if len(named_pairs)!=1:
                errors.append('unconfirmed:arithmetic_formula_named_operand_binding')
            elif not (operand_matches(m[1],named_pairs[0]['left']['value']) and operand_matches(m[3],named_pairs[0]['right']['value'])):
                errors.append('arithmetic_formula_named_operand_mismatch')
        bound=[p for p in pairs if (named_pairs is None or p in named_pairs)
               and operand_matches(m[1],p['left']['value']) and operand_matches(m[3],p['right']['value'])]

        if len(bound)>1:
            named=[p for p in bound if any(n in clause for n in entity_aliases(p['left'])+aliases(p['left'])) and any(n in clause for n in entity_aliases(p['right'])+aliases(p['right']))]
            if len(named)==1:bound=named
        unit=units[0] if units else (bound[0]['unit'] if len(bound)==1 else '')
        exact= c==computed
        rounded=rounding and unit in {'分','%','百分点','Mbps','人/台','台/人'} and computed.quantize(Decimal(1).scaleb(c.as_tuple().exponent),rounding=ROUND_HALF_EVEN)==c

        raw_approx=bool(len(bound)==1 and m[5]=='≈' and bound[0].get('basis')=='raw_means_then_round'
                        and re.search(r'未舍入|未四舍五入|原始均值',clause)
                        and re.search(r'显示|已舍入|已四舍五入',clause)
                        and c==Decimal(str(bound[0]['value'])))
        if not (exact or rounded or raw_approx):
            errors.append('arithmetic_displayed_equation_mismatch:'+m[0]+';displayed_subtraction='+str(computed))
        numeric_pairs=[p for p in proofs if operand_matches(m[1],p['left']['value']) and operand_matches(m[3],p['right']['value'])]
        if units and numeric_pairs and not any(all(u==p['unit'] for u in units) for p in numeric_pairs):errors.append('arithmetic_formula_unit_mismatch')
        if len(set(units))>1:errors.append('arithmetic_formula_unit_mismatch')
        elif not pairs and units:errors.append('arithmetic_formula_unit_mismatch')
        if len(bound)!=1:
            named=[p for p in pairs if any(n in clause for n in entity_aliases(p['left'])) and any(n in clause for n in entity_aliases(p['right']))]
            if named and not any(equal(m[1],p['left']['value'],unit) and equal(m[3],p['right']['value'],unit) for p in named):
                errors.append('arithmetic_formula_operand_mismatch')
            else:errors.append('unconfirmed:arithmetic_formula_operand_precision_or_binding')
        elif exact or rounded or raw_approx:
            proof=bound[0]

            allowed.append(dict(token=m[6],unit=unit,proof=proof,
                                directed=c==Decimal(str(proof['value'])),
                                expression=m[0],displayed_subtraction=str(computed),
                                result_precision=-c.as_tuple().exponent,rounding=rounding))
            for token,u in ((m[1],m[2]),(m[3],m[4])):
                if u:allowed.append(dict(token=token,unit=u,proof=proof,directed=False,expression=m[0]))
    return allowed,list(dict.fromkeys(errors))

def subtraction_proofs(evidence,spec):
    from packages.semantic_query.required_facts import required_facts
    inventory=required_facts(evidence,spec)
    facts=[f for f in inventory if f.get('available') and not f.get('operation') and not (f.get('scope') or {}).get('operation')]
    proofs=[]
    for left,right in combinations(facts,2):
        if left.get('unit')!=right.get('unit'):continue

        a,b=left.get('scope') or {},right.get('scope') or {}
        if any(a.get(k)!=b.get(k) for k in ('view','aggregation','statistic')):continue
        if left.get('question_id')!=right.get('question_id'):continue
        if a.get('dimension')!=b.get('dimension'):continue
        for x,y in ((left,right),(right,left)):
            value=round(x['value']-y['value'],2)
            proof=dict(left=x,right=y,value=value,unit=x.get('unit') or '',source_fields=[x['source_field'],y['source_field']],basis='executed_operands_then_round')
            precision=evidence.get('executed_arithmetic_precision')
            if precision and set(proof['source_fields'])==set(precision['source_fields']):
                forward=proof['source_fields']==precision['source_fields']
                proof.update(value=precision['value'] if forward else -precision['value'],basis=precision['basis'],precision=precision)
            elif set(proof['source_fields'])=={'total_score_a','total_score_b'}:
                difference=next((f for f in inventory if f.get('operation')=='subtract' and f.get('available') and f.get('unit')==proof['unit']),None)
                if difference:
                    direction=1 if difference.get('ordered_entities')==[x.get('entity'),y.get('entity')] else -1
                    executed=direction*difference['value']
                    if not equal(str(executed),value,proof['unit']):
                        proof.update(value=executed,basis='unknown_precision_basis',inconclusive='unconfirmed:arithmetic_difference_precision_basis')
            proofs.append(proof)
    return proofs

def arithmetic_assertions(text,evidence,spec):
    pass
    answer=compact(text);proofs=subtraction_proofs(evidence,spec);allowed=[];errors=precision_errors(evidence)

    answer=re.sub(r'(?<![，,])(?=(?:即|亦即|也就是)(?:相差|差距|绝对差|绝对值))',',',answer)

    facts=[]
    for proof in proofs:
        for fact in (proof['left'],proof['right']):
            if fact not in facts:facts.append(fact)
    for proof in proofs:
        x,y=proof['left'],proof['right'];unit=proof['unit'];value=proof['value']
        if proof.get('inconclusive'):errors.append(proof['inconclusive'])
        la,ra=_subtraction_labels(proof,facts)
        if not la or not ra:continue
        from packages.semantic_query.required_facts import _bound_comparison_claims
        relation_answer=answer
        if unit!='分' and len({f.get('unit') for f in facts})>1:
            relation_answer='。'.join(c for c in re.split(r'[。；;\n]',answer)
                                     if any(unit_alias(m[2])==unit for m in MEASURE.finditer(c)))

        relation_prose=_SUBTRACTION_FORMULA.sub('#equation#',relation_answer)
        errors.extend(_bound_comparison_claims(relation_prose,la,ra,x['value'],y['value'],expected_difference=value,expected_unit=unit))
        subject='(?:'+'|'.join(re.escape(a) for a in la)+')'
        other='(?:'+'|'.join(re.escape(a) for a in ra)+')'
        qualifier=r'(?:的)?(?:学校)?(?:平均)?(?:得分|均分|分数|数量)?'
        for relation in re.finditer(subject+qualifier+r'(高于|低于|多于|少于|等于)'+other,relation_answer):
            if ((relation[1] in {'高于','多于'} and value<=0)
                    or (relation[1] in {'低于','少于'} and value>=0)
                    or (relation[1]=='等于' and value!=0)):
                errors.append('comparison_direction_mismatch')

        for clause in re.split(r'[。；;\n]',answer):
            named=any(a in clause for a in la) and any(b in clause for b in ra)
            formula_ok=False
            formula_spans=[m.span() for m in _SUBTRACTION_FORMULA.finditer(clause)]
            if named and re.search(r'差|减|比|较|高于|低于|多于|少于',clause) or formula_ok:
                for m in MEASURE.finditer(clause):

                    if any(start<=m.start()<end for start,end in formula_spans):continue
                    if unit_alias(m[2])!=unit:continue

                    full_before=clause[:m.start()]
                    before=re.split(r'[，,]',full_before)[-1]
                    ordered=subject+r'(?:年)?(?:的)?(?:减|-)'+other+r'(?:年)?'
                    magnitude=bool(re.search(r'(?:相差|差距|绝对差|绝对值)(?:为|是|约|了)?$',before))

                    order_context=full_before if re.search(r'差值|分差|方向',before) and not magnitude else before
                    is_ordered=bool(re.search(ordered,order_context))
                    if is_ordered and not magnitude and not equal(m[1],value,unit):
                        errors.append('arithmetic_subtraction_order_mismatch')
                    if magnitude and not equal(m[1],abs(value),unit):
                        errors.append('arithmetic_magnitude_mismatch')
                    derived=bool(re.search(r'[=]|减|差(?:了|为|是|值)?|(?:比|较).{0,22}(?:高|低|多|少)(?:了|约)?$',before))
                    if (derived or magnitude) and (equal(m[1],value,unit) or equal(m[1],abs(value),unit)):
                        directed=not magnitude and (is_ordered or bool(re.search(r'减|[=]|比|较|高于|低于|多于|少于',before)))
                        allowed.append(dict(token=m[1],unit=unit,proof=proof,directed=directed))
    formula_text=re.sub(r'[ \t\r*`_]','',str(text)).replace('−','-').replace('－','-').replace('＝','=').replace('（','(').replace('）',')')
    formula_allowances,formula_errors=verify_numeric_subtractions(formula_text,proofs)
    allowed.extend(formula_allowances);errors.extend(formula_errors)
    return allowed,list(dict.fromkeys(errors))

def constraint_inputs(question,evidence,spec):
    from packages.semantic_query.repair_delivery import reconcile_request
    return reconcile_request(question,evidence,spec)

def numeric_roles(text,evidence,spec):
    pass
    facts,_=scalar_inventory(evidence,spec)
    qids={str(f.get('question_id') or (f.get('scope') or {}).get('question_id')) for f in facts}
    qids.discard('None')
    if evidence.get('question_id') is not None:qids.add(str(evidence['question_id']))
    years={str(f['period']) for f in facts if f.get('period') is not None}
    years.update(str(evidence[k]) for k in ('year','year_a','year_b') if evidence.get(k) is not None)
    proofs=[];errors=[]
    def record(m,group,role,valid,basis):
        proofs.append(dict(span=list(m.span(group)),token=m[group],role=role,status='UNCONFIRMED' if valid is None else 'PASS' if valid else 'FAIL',basis=basis))
        if valid is None:errors.append('unconfirmed:numeric_role_'+role+'_evidence_missing:'+m[group])
        elif not valid:errors.append('numeric_role_'+role+'_mismatch:'+m[group])
    for m in re.finditer(r'(?<![A-Za-z0-9])(?:[Qq]\s*(\d+)|第\s*(\d+)\s*题)',text):
        g=1 if m[1] is not None else 2
        record(m,g,'question_id',m[g] in qids,sorted(qids))
    for m in re.finditer(r'(?<!\d)((?:19|20)\d{2})(?=年|/)',text):
        record(m,1,'period',m[1] in years,sorted(years))
    bullets=list(re.finditer(r'(?m)^\s*(\d+)[.、]\s+(?=\S)',text))
    if bullets:
        sequence=[int(m[1]) for m in bullets]
        valid=sequence==list(range(1,len(bullets)+1))
        for m in bullets:record(m,1,'list_ordinal',valid,'consecutive line-leading list markers')
    transforms=[]
    for f in facts:
        scope=f.get('scope') or {}
        sem=scope.get('semantic_type') or {}
        if sem.get('allowed_transform')=='normalized_value_times_100':transforms.append(f['source_field'])

    for m in re.finditer(r'((?:登记)?归一(?:化)?值|标准化值|原始值)\s*(?:乘以?|[×*])\s*([+-]?\d+(?:\.\d+)?)',text):
        valid=False if m[1]=='原始值' or Decimal(m[2])!=100 else True if transforms else None
        record(m,2,'transform_constant',valid,transforms)

    for m in re.finditer(r'(?:[Qq]\s*(\d+)|第\s*(\d+)\s*题)(?:的)?(标准分|标准化分|原始值|得分)?(?:为|是|[:：])?\s*([+-]?\d+(?:\.\d+)?)\s*('+_FORMULA_UNIT+r')',text):

        if re.search(r'(?:Q\d+|第\d+题)\s*(?:减(?:去)?|[-−－])\s*$',text[:m.start()]):continue
        qid=m[1] or m[2];view='raw_value' if m[3]=='原始值' else 'normalized_score' if m[3] in {'标准分','标准化分'} else None
        candidates=[f for f in facts if str(f.get('question_id') or (f.get('scope') or {}).get('question_id'))==qid
                    and (view is None or (f.get('scope') or {}).get('view',spec.get('value_view'))==view)]
        prefix=text[:m.start()]
        mentions=[(hit.start(),f.get('entity')) for f in candidates for n in entity_aliases(f) for hit in re.finditer(re.escape(n),prefix)]
        if mentions:
            nearest=max(pos for pos,_ in mentions)
            entities={e for pos,e in mentions if pos==nearest}
            candidates=[f for f in candidates if f.get('entity') in entities]
        periods=list(re.finditer(r'(?<!\d)((?:19|20)\d{2})年',prefix))
        if periods:candidates=[f for f in candidates if str(f.get('period'))==periods[-1][1]]
        valid=None if not candidates else any(unit_alias(m[5])==unit_alias(f.get('unit')) and equal(m[4],f['value'],unit_alias(m[5])) for f in candidates)
        record(m,4,'item_value_binding',valid,[dict(source_field=f['source_field'],entity=f.get('entity'),period=f.get('period'),question_id=qid,value=f['value'],unit=f.get('unit')) for f in candidates])
    return proofs,list(dict.fromkeys(errors))

def explicit_listing_order(text,question,evidence,spec):
    pass
    facts,_=scalar_inventory(evidence,spec)
    groups=[]
    for f in facts:
        if f.get('operation') or (f.get('scope') or {}).get('operation'):continue
        groups.append((f,aliases(f)+entity_aliases(f)))
    groups=[(f,[n for n in names if not any(n in other_names for j,(_,other_names) in enumerate(groups) if i!=j)])
            for i,(f,names) in enumerate(groups)]
    errors=[]
    for clause in re.split(r'[。；;\n]',compact(question)):
        if not (re.search(r'按.+(?:的)?顺序|依次|先.+再|先.+后',clause) and re.search(r'列|给|写|输出|展示|报告|依次',clause)):continue
        requested=[]
        for i,(f,names) in enumerate(groups):
            hits=[clause.find(n) for n in names if n and n in clause]
            if hits:requested.append((min(hits),i))

        requested.sort()
        if len(requested)<2 or len({p for p,_ in requested})!=len(requested):continue
        positions=[]
        answer=compact(text)
        for _,i in requested:
            f,names=groups[i];hits=[]
            for m in MEASURE.finditer(answer):
                if not equal(m[1],f.get('value'),unit_alias(m[2])):continue
                prefix=re.split(r'[，,。；;\n]',answer[:m.start()])[-1]
                if any(n in prefix for n in names):hits.append(m.start())
            if hits:positions.append(min(hits))
        if len(positions)==len(requested) and positions!=sorted(positions):errors.append('explicit_listing_order_mismatch')
    return errors

def unit_evidence_review(evidence,spec):
    pass
    from packages.agent.indicator_catalog import PRESET_RULES
    facts,_=scalar_inventory(evidence,spec)
    rows=[];errors=[]
    for f in facts:
        scope=f.get('scope') or {};qid=f.get('question_id') or scope.get('question_id') or evidence.get('question_id')
        if not qid or scope.get('operation') or f.get('operation'):continue
        view=scope.get('view') or spec.get('value_view')
        if view=='normalized_score':continue
        sources=[dict(source='packages.agent.indicator_catalog.PRESET_RULES',entry=i,unit=r.get('unit'),name=r.get('name'))
                 for i,r in enumerate(PRESET_RULES) if str(r.get('qid'))==str(qid) and r.get('unit')]
        units={unit_alias(r['unit']) for r in sources}|{unit_alias(f.get('unit') or '')}
        units.discard('')

        if '个' in units and any(r['unit']=='个'+str(f.get('unit') or '') for r in sources):units.discard('个')
        if '个' in units and f.get('unit'):
            from packages.semantic_query.catalogue import question_metric
            metric=question_metric(qid)
            if metric.get('value_type')=='count' and re.search(re.escape(f['unit'])+r'(?:数量|数)$',metric.get('name') or ''):
                units.discard('个')
        if len(units)>1:
            rows.append(dict(source_field=f['source_field'],question_id=qid,executed_unit=f.get('unit'),declared_units=sorted(units),sources=sources,status='UNCONFIRMED'))
            errors.append('unconfirmed:unit_catalogue_conflict:'+f['source_field'])
        elif not f.get('unit'):
            errors.append('unconfirmed:unit_definition_missing:'+f['source_field'])
    return rows,list(dict.fromkeys(errors))

def verify_kind_items(text,evidence,spec):
    pass
    rows=evidence.get('item_results') or [];derived=evidence.get('derived_result') or {}
    if len(rows)!=2 or not all(r.get('unit')=='种' for r in rows) or derived.get('operation')!='subtract':return None
    from packages.semantic_query.required_facts import _distinct_labels,_bound_comparison_claims,_verify_kind_count_difference
    answer=compact(text);errors=[]
    by={r['question_id']:r for r in rows}
    if derived.get('left_qid') not in by or derived.get('right_qid') not in by:return ['unconfirmed:item_set_operands_unavailable']
    left,right=by[derived['left_qid']],by[derived['right_qid']]
    if any(type(r.get('target_value')) not in {int,float} for r in rows):return ['unconfirmed:item_set_evidence_unavailable']
    labels=[_distinct_labels(r,rows[1-i]) for i,r in enumerate(rows)]
    for i,r in enumerate(rows):
        labels[i].update({'前者','前项','第一项'} if i==0 else {'后者','后项','第二项'})
        found=False
        for clause in re.split(r'[，,。；;\n]',answer):
            for m in MEASURE.finditer(clause):
                prior=[(hit.start(),i) for i,names in enumerate(labels) for name in names for hit in re.finditer(re.escape(name),clause[:m.start()])]
                if not prior or max(prior)[1]!=i or re.search(r'减|差|[=]|比',clause[:m.start()]):continue
                if m[2]=='种' and equal(m[1],r['target_value'],'种'):found=True
                else:errors.append('item_set_named_count_mismatch')
        if not found:errors.append('item_set_named_count_missing')
    li=rows.index(left);ri=rows.index(right)
    errors+=_bound_comparison_claims(answer,list(labels[li]),list(labels[ri]),left['target_value'],right['target_value'])
    difference=left['target_value']-right['target_value']
    if not equal(str(derived.get('value')),difference,'种'):errors.append('evidence_conflict:derived_result.value')

    if re.search(r'\d+(?:种)?-\d+(?:种)?=',answer):errors+=_verify_kind_count_difference(answer,rows,derived)
    proven=False
    for clause in re.split(r'[。；;\n]',answer):
        if not any(a in clause for a in labels[li]) or not any(b in clause for b in labels[ri]):continue
        for m in MEASURE.finditer(clause):
            if m[2]!='种':continue
            prefix=clause[:m.start()]
            if re.search(r'减|差|[=]',prefix) and equal(m[1],difference,'种'):proven=True
            if re.search(r'比.+(?:多|少)$',prefix) and equal(m[1],abs(difference),'种'):proven=True
    if not proven:errors.append('item_set_subtraction_direction_unconfirmed')
    for i,names in enumerate(labels):
        for name in names:
            if re.search(re.escape(name)+r'(?:更多|较多)',answer) and rows[i]['target_value']<=rows[1-i]['target_value']:errors.append('comparison_direction_mismatch')
    return list(dict.fromkeys(errors))
