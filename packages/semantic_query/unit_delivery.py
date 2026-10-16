import copy
import re

MARK = '（原字段登记值）'
NUMBER = r'[+\-−－]?\d+(?:\.\d+)?'

def policy(evidence, spec):
    from packages.semantic_query.fact_assertions import unit_evidence_review
    from packages.semantic_query.catalogue import question_metric
    rows, errors = unit_evidence_review(evidence, spec)
    if not rows or spec.get('operation') != 'public_task':
        return None
    nodes = evidence.get('task_outputs') or []
    atoms = [n for n in nodes if not (n.get('scope') or {}).get('operation')]
    affected = {r['source_field'] for r in rows}

    def identity(n):
        s = n.get('scope') or {}; t = s.get('semantic_type') or n.get('semantic_type') or {}
        return (s.get('kind') == 'school_fact' and s.get('view') == 'raw_value'
                and s.get('aggregation') == 'none' and t.get('allowed_transform') == 'identity'
                and t.get('source_view') == 'raw_value' and t.get('family') == 'raw'
                and t.get('metric_ids') == [s.get('question_id')]
                and t.get('statistical_unit') == 'registered_answer'
                and type(n.get('value')) in (int, float))
    raw = [n for n in atoms if identity(n)]
    qids = {n['scope'].get('question_id') for n in atoms}
    units = {n.get('unit') for n in atoms}
    same = (bool(atoms) and len(raw) == len(atoms) and affected == {n['id'] for n in atoms}
            and len(qids) == len(units) == 1 and None not in qids and None not in units
            and len({n['scope'].get('year') for n in atoms}) == 1)
    derived = [n for n in nodes if (n.get('scope') or {}).get('operation')]
    safe = same and all(n['scope']['operation'] in ('subtract', 'add')
                        and set(n['scope'].get('args') or []) <= {a['id'] for a in atoms}
                        for n in derived)
    fields = list(dict.fromkeys(question_metric(n['scope']['question_id']).get('name') or n['label'] for n in raw))
    return dict(status='UNCONFIRMED', same_field_arithmetic=bool(safe),
                executed_unit=next(iter(units)) if len(units) == 1 else None,
                original_fields=fields, rows=rows, errors=errors, raw_nodes=raw,
                deferred_nodes=[] if safe else [n['id'] for n in derived],
                reason='same_source_field_identity_no_conversion' if safe else 'unit_meaning_may_affect_comparison_or_conversion')

def notice(p):
    names='、'.join('“'+s+'”' for s in p['original_fields'])
    return ('原字段：'+names+'。以上标为原字段登记值的数字仅表示该字段的登记数值；'
            '单位含义待核，不能据此解释为课时、次数或其他计量单位。')

def partial_text(p):
    lines=[f"{n['label']}：{n['value']}{MARK}。" for n in p['raw_nodes']]
    return '\n'.join(lines+[notice(p), '相关换算或比较结论未决。请提供这些原字段的计量定义、换算关系及是否采用相同登记口径的说明。'])

def project_text(text, p):
    if not p or not text:
        return text
    if not p['same_field_arithmetic']:
        return partial_text(p)
    unit=p['executed_unit']

    text=re.sub(r'('+NUMBER+r')\s*'+re.escape(unit), lambda m:m[1]+MARK, text)
    return text if notice(p) in text else text+'\n\n'+notice(p)

def validation_input(text, evidence, spec):
    pass
    p=policy(evidence,spec)
    errors=[]
    if p:

        for clause in re.split(r'[。；;\n]',text):
            declared='|'.join(re.escape(u) for r in p['rows'] for u in r['declared_units'])
            for match in re.finditer(r'(?:单位(?:为|是|就是)|(?:'+declared+r')(?:就是|等于|代表|指))\s*[“"‘]?\s*(?:课时|次数|次|小时|节课|门课)',clause):
                if not re.search(r'(?:不能认为|不可认为|并非|不是说|不能说|不表示)\s*$',clause[max(0,match.start()-8):match.start()]):
                    errors.append('unsupported_unit_equivalence')
    if not p or MARK not in text:
        return text,evidence,spec,errors,p
    if not p['same_field_arithmetic']:

        if text != partial_text(p):
            return text,evidence,spec,errors+['unconfirmed:unit_comparison_requires_definition'],p
        evidence=copy.deepcopy(evidence);spec=copy.deepcopy(spec)
        ids={n['id'] for n in p['raw_nodes']}
        evidence['task_outputs']=copy.deepcopy(p['raw_nodes'])
        plan=spec.get('public_plan') or {}
        plan['queries']=[q for q in plan.get('queries',[]) if q['id'] in ids]
        plan['steps']=[];plan.pop('selection',None);plan.pop('choose_extreme',None)
        evidence.pop('required_facts',None)

        for n in p['raw_nodes']:
            literal=f"{n['label']}：{n['value']}{MARK}"
            text=text.replace(literal,f"{n['label']}：{n['value']}{n['unit']}")
        return text,evidence,spec,errors+['unconfirmed:unit_comparison_requires_definition'],p
    if not all(name in text for name in p['original_fields']) or '单位含义待核' not in text:
        return text,evidence,spec,errors+['unconfirmed:unit_limitation_missing'],p
    text=re.sub(r'('+NUMBER+r')'+re.escape(MARK), lambda m:m[1]+p['executed_unit'], text)
    return text,evidence,spec,errors,p

def only_unit_unknown(v):
    pending=v.get('inconclusive') or []
    return (v.get('status')=='UNCONFIRMED' and not v.get('violations') and bool(pending)
            and all(s.startswith(('unconfirmed:unit_catalogue_conflict:',
                                  'unconfirmed:unit_comparison_requires_definition')) for s in pending))

def delivery_allowed(v, text, p):
    return bool(p and p['raw_nodes'] and only_unit_unknown(v) and MARK in text and '单位含义待核' in text
                and all(name in text for name in p['original_fields'])
                and (p['same_field_arithmetic'] or text==partial_text(p)))
