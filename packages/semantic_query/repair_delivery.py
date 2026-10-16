from __future__ import annotations
import copy
import math
import re
from decimal import Decimal

def entity_names(question, name):
    pass
    name=str(name or '').split(' ')[-1]
    short=re.sub(r'(?:壮族自治区|回族自治区|维吾尔自治区|自治区|省|市)$','',name)
    names={v for v in (name,short) if v}
    if short:
        for m in re.finditer(r'('+re.escape(short)+r'地|[\u4e00-\u9fff]地|[\u4e00-\u9fff])[（(]'+re.escape(short)+r'[）)]',question):
            names.add(m[1]); names.add(re.sub(r'地$','',m[1]))
    return sorted(names,key=len,reverse=True)

def difference_request(question):
    clauses=re.split(r'[。；;\n]',question)
    return any(not re.search(r'(?:不要|不需|不必|无需|不求|不算).{0,5}(?:差|减)',c)
               and re.search(r'分差|差值|差(?:了)?多少|相差|差距|前(?:者)?减后(?:者)?|(?:算|计算|求|写出|给出).{0,28}减.{0,20}(?:差|结果)',c) for c in clauses)

def reconcile_request(question, evidence, spec):
    pass
    s,e=copy.deepcopy(spec),copy.deepcopy(evidence)
    if s.get('operation')=='region_eval' and s.get('target_stat') in {'mean','median','max','min'} and s.get('granularity') in {'overall','dimension','item'} and re.search(
            r'(?:只|仅)(?:回答|回复|给出|给|报|要)(?:这一项|一项|该项|这一|所问)?(?:数值|数字)|(?:请)?回复数值即可',question):

        positive=re.sub(r'(?:不要|不|别).{0,15}(?:画图|列出其他维度|解释原因|给排名|描述年度变化)', '', question)
        if not re.search(r'(?:并|同时|还要).{0,8}(?:画图|解释|分析|排名|变化)',positive):
            s.update(single_value_only=True, answer_mode='direct', required_outputs=['direct_answer'], chart_requested=False,
                     constraint_request_completion='explicit_single_value')
    if s.get('operation')=='region_compare' and s.get('target_stat')!='count' and difference_request(question):
        requested=list(s.get('requested_result_statistics') or [])
        s['requested_result_statistics']=list(dict.fromkeys(requested+['difference']))
        names=[entity_names(question,e.get('region_name_'+side)) for side in ('a','b')]
        e['request_entity_aliases']={e.get('region_name_'+side):names[i] for i,side in enumerate(('a','b')) if e.get('region_name_'+side)}
        directions=set()
        for i,j in ((0,1),(1,0)):
            for left in names[i]:
                for right in names[j]:
                    if re.search(re.escape(left)+r'\s*(?:减|−|-)\s*'+re.escape(right),question):directions.add(1 if i==0 else -1)
        if re.search(r'前(?:者)?减后(?:者)?',question):directions.add(1)
        if re.search(r'后(?:者)?减前(?:者)?',question):directions.add(-1)
        if len(directions)>1:
            s['constraint_request_conflict']='ambiguous_subtraction_order'
        elif directions:
            s['subtraction_direction']=directions.pop()
        a,b=e.get('total_score_a'),e.get('total_score_b')
        if e.get('diff') is None and all(type(v) in {int,float} and math.isfinite(v) for v in (a,b)) and e.get('difference_basis')!='raw_means_then_round':
            reverse=s.get('subtraction_direction')==-1

            value=float(round((Decimal(str(b))-Decimal(str(a))) if reverse else (Decimal(str(a))-Decimal(str(b))),2))
            e.update(diff=value,difference_basis='executed_score_slots',constraint_derivations=[dict(field='diff',operation='subtract',
                source_fields=['total_score_b','total_score_a'] if reverse else ['total_score_a','total_score_b'],value=value,unit='分')])
        s['constraint_request_completion']='explicit_difference_over_executed_pair'
    return s,e

def _winner_requirement(question,evidence,spec):
    if spec.get('operation')!='region_compare' or spec.get('target_stat')=='count':return None
    if not re.search(r'(?:哪(?:个|一)?(?:边|地|地方|方)?|谁).{0,15}(?:高|低)|哪个地方.{0,15}更高',question):return None
    a,b=evidence.get('total_score_a'),evidence.get('total_score_b')
    if not all(type(v) in {int,float} and math.isfinite(v) for v in (a,b)):return dict(available=False)
    return dict(available=True, left=evidence.get('region_name_a'),right=evidence.get('region_name_b'),relation='equal' if a==b else 'left_higher' if a>b else 'right_higher')

def winner_clause(text,question,evidence,spec):
    pass
    req=_winner_requirement(question,evidence,spec)
    if not req or not req['available']:return None
    groups=[entity_names(question,req[k]) for k in ('left','right')]
    qualifiers=r'(?:的)?(?:学校)?(?:等权)?(?:平均)?(?:得分|均分|分数|分)?'
    patterns=[]
    if req['relation']=='equal':
        patterns=[r'(?:两地|两者|二者|双方)(?:得分|均分|分数)?(?:相同|相等|一样|同分)']
        patterns += [re.escape(a)+r'(?:与|和|、|及)'+re.escape(b)+qualifiers+r'(?:相同|相等|一样|同分)' for a in groups[0] for b in groups[1]]
    else:
        hi=0 if req['relation']=='left_higher' else 1;lo=1-hi
        for h in groups[hi]:
            patterns.append(re.escape(h)+qualifiers+r'(?:更高|较高|最高)')
            for l in groups[lo]:
                patterns += [re.escape(h)+qualifiers+r'(?:高于|大于)'+re.escape(l),
                             re.escape(l)+qualifiers+r'(?:低于|小于)'+re.escape(h),
                             re.escape(h)+qualifiers+r'比'+re.escape(l)+r'(?:更|较)?高',
                             re.escape(l)+qualifiers+r'比'+re.escape(h)+r'(?:更|较)?低',
                             re.escape(l)+qualifiers+r'(?:更低|较低|最低)']
    for pattern in sorted(patterns,key=len,reverse=True):
        m=re.search(pattern,text)
        if m:
            prefix=text[max(0,m.start()-8):m.start()]
            suffix=text[m.end():m.end()+8]
            if not re.search(r'不是|并非|不比|是否|可能|未必|无法|未确认',prefix) and not re.match(r'[？?]|(?:的说法|这一说法|是)?(?:错误|不对|不正确)',suffix):return m[0]
    return None

def obligation_check(text,question,evidence,spec):
    from packages.semantic_query.required_facts import required_facts
    from packages.semantic_query.fact_assertions import verify_scalar_slots,compact
    facts=required_facts(evidence,spec); errors=verify_scalar_slots(text,evidence,spec)
    rows=[];answer=compact(text)
    entities={f.get('entity') for f in facts if f.get('entity') and not f.get('operation')}
    supported=spec.get('operation') in {'region_eval','school_eval','school_fact','region_compare'} and spec.get('granularity')!='item_set'
    for f in facts:
        local=[x for x in errors if x.endswith(':'+f['key'])]
        if supported and not spec.get('single_value_only') and len(entities)>1 and f.get('entity') and not any(n in answer for n in entity_names(question,f['entity'])):
            local.append('mandatory_object_missing:'+f['key'])
        state='UNAVAILABLE' if not f.get('available') else 'MISSING_OR_INVALID' if local else 'PRESENT' if supported else 'DELEGATED_TO_EXISTING_VERIFIER'
        rows.append(dict(key=f['key'],source_field=f.get('source_field'),entity=f.get('entity'),period=f.get('period'),value=f.get('value'),unit=f.get('unit'),
                         operation=f.get('operation'),ordered_entities=f.get('ordered_entities'),ordered_labels=f.get('ordered_labels'),ordered_qids=f.get('ordered_qids'),status=state,failures=local))
        errors.extend(x for x in local if x not in errors)
    difference=next((f for f in facts if f.get('key')=='diff' and f.get('available')),None)
    if difference and spec.get('operation')=='region_compare' and re.search(r'减',question):
        from packages.semantic_query.fact_assertions import arithmetic_assertions,equal
        allowances,_=arithmetic_assertions(text,evidence,spec)
        order=['total_score_b','total_score_a'] if spec.get('subtraction_direction')==-1 else ['total_score_a','total_score_b']
        if not any(p.get('directed') and p['proof']['source_fields']==order and equal(p['token'],difference['value'],difference['unit']) for p in allowances):
            error='mandatory_ordered_difference_missing:diff'
            errors.append(error)
            row=next(r for r in rows if r['key']=='diff');row['status']='MISSING_OR_INVALID';row['failures'].append(error)
    req=_winner_requirement(question,evidence,spec)
    if req:
        if req['available']:

            qualifiers=r'(?:的)?(?:学校)?(?:等权)?(?:平均)?(?:得分|均分|分数|分)?'
            for side,key in enumerate(('left','right')):
                positive=req['relation']==('left_higher' if side==0 else 'right_higher')
                negative=req['relation']==('right_higher' if side==0 else 'left_higher')
                for name in entity_names(question,req[key]):
                    for m in re.finditer(re.escape(name)+qualifiers+r'(更高|较高|最高|更低|较低|最低)',text):
                        if re.search(r'不是|并非|不比|是否|可能|未必|无法|未确认',text[max(0,m.start()-8):m.start()]):continue
                        if re.match(r'[？?]|(?:的说法|这一说法|是)?(?:错误|不对|不正确)',text[m.end():m.end()+8]):continue
                        if not (positive if m[1] in {'更高','较高','最高'} else negative):errors.append('mandatory_comparison_direction_mismatch')
        relation=winner_clause(text,question,evidence,spec)
        local=[] if relation else ['mandatory_winner_missing'] if req['available'] else ['unconfirmed:mandatory_winner_evidence_unavailable']
        errors.extend(local);rows.append(dict(key='qualitative_comparison',expected=req,status='PRESENT' if relation else 'MISSING_OR_INVALID',matched_text=relation,failures=local))
    if spec.get('constraint_request_conflict'):errors.append('unconfirmed:'+spec['constraint_request_conflict'])
    if spec.get('single_value_only'):
        from packages.semantic_query.required_facts import verify_single_value
        local=verify_single_value(text,evidence,spec)
        rows.append(dict(key='single_value_output_scope',expected='Only the requested scalar and optional score unit',status='PRESENT' if not local else 'MISSING_OR_INVALID',failures=local))
        errors.extend(local)
    errors=list(dict.fromkeys(errors))
    return dict(status='FAIL' if any(not x.startswith('unconfirmed:') for x in errors) else 'UNCONFIRMED' if errors else 'PASS',items=rows,failures=errors)

def _localized_supplier_patch(first,candidate,question,evidence,spec,first_check):
    pass
    from packages.semantic_query.runtime import verify_answer_text
    from packages.semantic_query.fact_assertions import MEASURE,equal,unit_alias
    from packages.semantic_query.required_facts import required_facts
    failures=set(first_check.get('failures') or [])
    if (not failures or first_check.get('status')!='FAIL'
            or spec.get('operation')!='region_compare' or spec.get('target_stat')=='count'
            or any(f.startswith('evidence_conflict:') for f in failures)):
        return None
    allowed=('request_named_value_or_unit_mismatch:','request_required_value_missing:',
             'unsupported_numbers:','dimension_compare_answer_value_',
             'dimension_compare_answer_winner_', 'dimension_compare_answer_side_value_or_count_mismatch',
             'unconfirmed:unsupported_numbers:', 'unconfirmed:request_measurement_binding',
             'comparison_direction_mismatch', 'mandatory_winner_', 'mandatory_comparison_direction_mismatch')
    if any(not f.startswith(allowed) for f in failures):return None
    if verify_answer_text(candidate,question,evidence,spec,verify_new_constraints=True)['status']!='PASS':return None
    facts=required_facts(evidence,spec)
    if any(not f.get('available') or f.get('evidence_conflict') for f in facts):return None
    edits=[]
    for fact in facts:
        if fact['key'] not in {'total_score_a','total_score_b'}:continue
        names=entity_names(question,fact['entity'])
        pattern=r'(?:'+ '|'.join(re.escape(n) for n in names)+r')(?:的)?(?:学校等权)?(?:均分|得分|分数)?(?:为|是|：|:)?\s*'
        spans=[]
        for match in re.finditer(pattern,first):
            prefix=re.split(r'[。；;\n]',first[:match.end()])[-1]
            if re.search(r'减|差|[=]',prefix):continue
            measure=MEASURE.match(first,match.end())
            if measure:spans.append(measure)
        if not spans and 'request_required_value_missing:'+fact['key'] in failures:
            observed=[]
            for match in re.finditer(pattern,candidate):
                prefix=re.split(r'[。；;\n]',candidate[:match.end()])[-1]
                if re.search(r'减|差|[=]',prefix):continue
                measure=MEASURE.match(candidate,match.end())
                if measure and equal(measure[1],fact['value'],fact['unit']) and unit_alias(measure[2])==fact['unit']:
                    observed.append(candidate[match.start():measure.end()])
            if len(observed)==1:
                edits.append(dict(start=len(first),end=len(first),before='',after=('' if first.endswith('。') else '。')+observed[0]+'。',
                    field=fact['key'],source_field=fact['source_field'],source='validated_supplier_named_measurement'))
            continue

        if len(spans)!=1:continue
        measure=spans[0]
        if equal(measure[1],fact['value'],fact['unit']) and unit_alias(measure[2])==fact['unit']:continue
        tail=first[measure.end():measure.end()+4]
        if re.match(r'\s*[-+×*/÷=]',tail):return None
        replacement=f"{fact['value']:g}{fact['unit']}"
        edits.append(dict(start=measure.start(),end=measure.end(),before=measure[0],after=replacement,
                          field=fact['key'],source_field=fact['source_field']))
    if failures & {'mandatory_comparison_direction_mismatch','mandatory_winner_missing','dimension_compare_answer_winner_mismatch','dimension_compare_answer_winner_missing'}:
        relation=winner_clause(candidate,question,evidence,spec)
        if relation:
            matches=[]
            for name in set(entity_names(question,evidence.get('region_name_a'))+entity_names(question,evidence.get('region_name_b'))):
                for m in re.finditer(re.escape(name)+r'(?:的)?(?:学校等权)?(?:均分|得分|分数)?(?:更高|较高|最高|更低|较低|最低)',first):
                    matches.append(m)
            unique={(m.start(),m.end()):m for m in matches}

            longest=[m for m in unique.values() if not any(n.start()<=m.start() and n.end()>=m.end() and n.span()!=m.span() for n in unique.values())]
            if len(longest)==1:
                m=longest[0]
                if not re.search(r'未必|不一定|不是|无法|不能|并非|是否|尚未',first[max(0,m.start()-8):m.start()]) and not re.match(r'[？?]|(?:的说法|的判断|是)?(?:不对|错误|不正确)',first[m.end():m.end()+8]):
                    edits.append(dict(start=m.start(),end=m.end(),before=m[0],after=relation,field='qualitative_comparison',source='validated_supplier_relation'))
    rejected=dict(action='reject_nonlocal_supplier_rewrite',changed=False,
                  reason='unique_valid_local_patch_unavailable',retained_first_verbatim=True)
    if not edits:return first,rejected
    edits.sort(key=lambda e:e['start'])
    if any(a['end']>b['start'] for a,b in zip(edits,edits[1:])):return first,rejected
    text=first
    for edit in reversed(edits):text=text[:edit['start']]+edit['after']+text[edit['end']:]
    validation=verify_answer_text(text,question,evidence,spec,verify_new_constraints=True)
    if validation['status']!='PASS':return first,rejected
    return text,dict(action='retain_first_patch_unique_verified_spans',changed=True,patches=edits,
        outside_patch_text_preserved=True,request_and_evidence_unchanged=True,
        protected_fields_before=obligation_check(first,question,evidence,spec)['items'],
        protected_fields_after=validation['request_obligations']['items'])

def minimal_repair(first,candidate,question,evidence,spec,first_check):
    pass
    if not candidate.strip():return candidate,dict(action='empty_supplier_candidate',changed=False)
    failures=set(first_check.get('failures') or [])
    if failures=={'mandatory_winner_missing'}:
        clause=winner_clause(candidate,question,evidence,spec)
        if clause:
            return first.rstrip()+('' if first.rstrip().endswith('。') else '。')+clause+'。',dict(action='retain_verified_first_add_observed_relation',changed=True,retained_first_verbatim=True,relation_source=clause)
    missing_difference={'request_required_value_missing:diff','mandatory_ordered_difference_missing:diff','dimension_compare_answer_difference_direction'}
    if failures and failures<=missing_difference:
        from packages.semantic_query.required_facts import required_facts
        fact=next((f for f in required_facts(evidence,spec) if f['key']=='diff' and f['available']),None)
        if fact and len(fact.get('ordered_entities') or [])==2 and all(fact['ordered_entities']):
            left,right=fact['ordered_entities']
            clause=f"{left}减{right}为{fact['value']:+.2f}{fact['unit']}。"
            return first.rstrip()+('' if first.rstrip().endswith('。') else '。')+clause,dict(action='retain_verified_first_add_executed_difference',changed=True,
                retained_first_verbatim=True,added_fact=copy.deepcopy(fact),derivation=copy.deepcopy(evidence.get('constraint_derivations')))
    if spec.get('single_value_only') and failures=={'single_value_only_scope_mismatch'}:
        from packages.semantic_query.required_facts import required_facts
        from packages.semantic_query.fact_assertions import MEASURE,unit_alias,equal
        facts=required_facts(evidence,spec)
        if len(facts)==1 and facts[0]['available']:
            fact=facts[0]
            matches=[m for m in MEASURE.finditer(first) if unit_alias(m[2])==fact['unit'] and equal(m[1],fact['value'],fact['unit'])]
            if len(matches)==1:return matches[0][0],dict(action='retain_verified_value_remove_unrequested_context',changed=True,retained_measurement_verbatim=True)
    local=_localized_supplier_patch(first,candidate,question,evidence,spec,first_check)
    if local:return local
    from packages.semantic_query.fact_assertions import compact
    expressions={p['expression'] for p in first_check.get('arithmetic_proofs') or []
                 if p.get('directed') and p.get('expression') and 'displayed_subtraction' in p}
    if any(compact(expression) not in compact(candidate) for expression in expressions):
        return first,dict(action='reject_supplier_loss_of_verified_expression',changed=False,
                          retained_first_verbatim=True,protected_expressions=sorted(expressions))
    return candidate,dict(action='supplier_candidate_requires_full_validation',changed=False)
