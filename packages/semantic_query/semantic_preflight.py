from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any
from packages.experiment_protocol.mode import requirement_preservation_enabled

VERSION = "semantic-preflight-1.0"
CHECK_FIELDS = {'intent', 'regions', 'schools', 'periods', 'dimension', 'statistic',
                'aggregation', 'school_filter', 'question_ids', 'value_view',
                'compound_key', 'school_field', 'target_rank', 'target_top_n', 'requested_outputs'}

def model_checker_research_enabled() -> bool:
    pass
    import os
    return os.environ.get('TE_RESEARCH_LLM_SEMANTIC_CHECKER', '0') == '1'

def public_requirement_coverage_enabled() -> bool:
    pass
    import os
    return os.environ.get('TE_PUBLIC_REQUIREMENT_COVERAGE', '1') != '0'

def dimension_pair_requirements(question: str) -> dict:
    pass
    from packages.semantic_query import runtime as rt
    text = rt._scope_assertion_text(question)
    matches = rt._explicit_dimension_aliases(question)
    names = [name for name, _ in matches]
    request = bool(re.search(r'比较|对比|哪.{0,12}高|高低|得分|均分|分数|多少分|差值|分差|差额|之差|相差|减|画图|画在一起', text))
    pair = names if len(names) == 2 and request and not rt._scope_question_ids(text) else []
    difference = bool(pair and re.search(r'分差|差值|差额|之差|差多少|相差|减', text))
    forward = bool(re.search(r'前(?:者|一项|项).{0,3}减.{0,3}后(?:者|一项|项)|第一项.{0,3}减.{0,3}第二项', text))
    reverse = bool(re.search(r'后(?:者|一项|项).{0,3}减.{0,3}前(?:者|一项|项)|第二项.{0,3}减.{0,3}第一项', text))
    if pair and difference:
        from packages.semantic_query.typed_plan import explicit_subtraction_operands
        queries=[dict(id='v'+str(i+1),entity=name,year=None,
                      direction_aliases=list(rt._DIMENSION_ALIASES[name])) for i,name in enumerate(pair)]
        try:
            ordered=explicit_subtraction_operands({'queries':queries},question,rt)
            if ordered==['v1','v2']:forward=True
            if ordered==['v2','v1']:reverse=True
        except ValueError:
            forward=reverse=True
    unknown = bool(request and re.search(r'另一(?:块|个维度|维度).{0,10}(?:未说明|未指明|待定|不清楚)|另一块尚未说明', text))
    return {'dimensions': pair, 'matches': matches, 'difference': difference,
            'operands': list(reversed(pair)) if reverse and not forward else list(pair),
            'uncertain': unknown or bool(forward and reverse),
            'reason': 'unnamed_dimension_or_conflicting_operand_direction' if unknown or forward and reverse else None}

def atomic_requirements(question: str) -> dict:
    pass
    from packages.semantic_query import runtime as rt
    text = rt._scope_assertion_text(question)
    pair = dimension_pair_requirements(question)
    atoms = []
    def add(kind, value, quote):
        if not quote or quote not in question:
            return
        start = text.find(quote)
        if start < 0:
            return
        left = max((question.rfind(c, 0, start) for c in '。；;！？!?\n'), default=-1) + 1
        ends = [question.find(c, start + len(quote)) for c in '。；;！？!?\n']
        right = min([x for x in ends if x >= 0] or [len(question)])
        semantic = json.dumps([kind, value], ensure_ascii=False, sort_keys=True)
        atoms.append({'id': 'req_' + hashlib.sha256(semantic.encode()).hexdigest()[:16],
                      'kind': kind, 'value': value, 'evidence': {'quote': quote, 'span': [start, start+len(quote)],
                      'sentence': question[left:right], 'sentence_span': [left, right]}})
    for name, quote in pair['matches']:
        add('dimension', name, quote)
    indicators = rt._scope_question_ids(question)
    aliases = [] if indicators else rt._catalogue_alias_items(question)
    for qid in indicators:
        match = next((m for m in re.finditer(r'(?i)Q\s*\d+|第\s*\d+\s*题', text)
                      if re.search(r'\d+', m.group()).group() == str(qid)), None)
        if match: add('indicator', qid, match.group())
    for qid, quote in aliases:
        add('indicator', qid, quote)

    raw_view=re.search(r'原始(?:值|数量|作答)|原值|登记数量|登记数值',text)
    score_view=re.search(r'标准分|归一(?:化)?(?:分|值|得分)|百分制',text)
    if raw_view and score_view:
        add('value_view','raw_value',raw_view.group())
        add('value_view','normalized_score',score_view.group())
    chart = rt._explicit_chart_request(question)
    direct = re.search(r'列出|给出|是多少|多少分|得分|均分|分数|原始值|查询', text)
    if direct: add('output', 'direct_answer', direct.group())
    if chart is True:
        match = re.search(r'图表|柱状图|柱形图|两柱图|条形图|折线图|雷达图|画图|绘图|画在一起', text)
        if match: add('output', 'charts', match.group())
    if pair['difference'] or rt._comparison_difference_requested(question):
        match = re.search(r'分差|差值|差额|之差|差多少|相差|减', text)
        add('result', 'difference', match.group())
    if pair['uncertain']:
        add('unresolved', pair['reason'], next((m.group() for m in re.finditer(r'另一块尚未说明|另一(?:块|个维度|维度).{0,10}(?:未说明|未指明|待定|不清楚)', text)), text.strip()))

    unique = {a['id']: a for a in reversed(atoms)}
    return {'version': 'explicit-atomic-requirements-v1',
            'question_sha256': hashlib.sha256(str(question).encode()).hexdigest(),
            'status': 'UNKNOWN' if pair['uncertain'] else 'EXPLICIT_SUBSET',
            'completeness_certified': False, 'atoms': list(reversed(list(unique.values()))),
            'dimension_pair': pair['dimensions'], 'difference_operands': pair['operands'] if pair['difference'] else [],
            'execution_dimensions': pair['operands'] if pair['difference'] else pair['dimensions']}

def requirement_correspondence(requirements: dict, routed: dict, spec: dict) -> dict:
    pass
    plan = routed.get('public_plan') or {}
    queries = plan.get('queries') or []
    dimensions = set(spec.get('target_dimensions') or []) | {routed.get('target_dimension')}
    dimensions.update(q.get('dimension') for q in queries)
    metric = routed.get('metric') or routed.get('metric_info') or routed.get('metric_set') or {}
    ids = set(spec.get('indicator_ids') or []) | set(metric.get('qids') or []) | {metric.get('qid')}
    ids.update(q.get('question_id') for q in queries)
    rows = []
    for atom in requirements['atoms']:
        kind, value = atom['kind'], atom['value']
        if kind == 'dimension':

            covered = value in dimensions or bool(ids - {None}) and not requirements['dimension_pair']
            fields = ['target_dimensions', 'target_dimension', 'public_plan.queries.dimension']
        elif kind == 'indicator':
            covered, fields = value in ids, ['metric.qid', 'metric_set.qids', 'public_plan.queries.question_id']
        elif kind == 'value_view':
            covered, fields = value in {q.get('view') for q in queries}, ['public_plan.queries.view']
        elif kind == 'output':
            covered, fields = value in spec.get('required_outputs', []), ['required_outputs']
        elif kind == 'result':
            covered = value in spec.get('requested_result_statistics', []) or bool(plan.get('steps'))
            fields = ['requested_result_statistics', 'public_plan.steps']
        else:
            covered, fields = False, []
        rows.append({'requirement_id': atom['id'], 'kind': kind, 'value': value,
                     'status': 'UNRESOLVED' if kind == 'unresolved' else 'COVERED' if covered else 'MISSING',
                     'task_fields': fields, 'plan_fields': fields})
    return {'basis': 'original affirmative literals versus actual common compiler fields',
            'completeness_certified': False, 'rows': rows,
            'missing_ids': [r['requirement_id'] for r in rows if r['status'] == 'MISSING']}

def _callback_result(callback, prompt, ledger):

    ledger.update(usage=None, model=None, provider_response_id=None, elapsed_ms=None,
                  finish_reason=None, attempts=None, transport_retries=None, http_attempts=None)
    try:
        response = callback(prompt)
    except Exception as exc:
        if isinstance(getattr(exc, 'receipt', None), dict):
            ledger.update({k: exc.receipt.get(k) for k in ('usage','model','provider_response_id','elapsed_ms','finish_reason','attempts','transport_retries','http_attempts','model_source','requested_model')})
        raise
    if isinstance(response, dict) and set(response) == {'result', 'receipt'}:
        receipt = response['receipt']
        if not isinstance(receipt, dict):
            raise ValueError('callback_receipt_invalid')
        ledger.update({k: receipt.get(k) for k in ('usage', 'model', 'provider_response_id', 'elapsed_ms', 'finish_reason', 'attempts', 'http_attempts','transport_retries','model_source','requested_model')})

        if (receipt.get('transport_retries') not in (0, None)
                or (receipt.get('http_attempts') or 0) > 1
                or isinstance(receipt.get('attempts'),list) and len(receipt['attempts'])>1):
            raise ValueError('callback_retry_limit_exceeded')
        response = response['result']
    return json.loads(response) if isinstance(response, str) else response

def _clauses(text: str):

    for match in re.finditer(r"[^，,。；;！？!?\n]+", text):
        clause = match.group()
        if re.search(r"不问|不要|无需|不需要|不比较|不看|不按", clause):
            continue
        yield clause, match.start(), match.end()

def derive_requirements(question: str, catalogue: list[dict]) -> dict:
    clauses = list(_clauses(question))
    affirmative = "，".join(c for c, _, _ in clauses)

    extrema = []
    for clause, start, end in clauses:
        if not re.search(r"题项|题目|各题|各项", clause):
            continue
        for match in re.finditer(r"最低|最小|最高|最大", clause):
            extrema.append({"kind": "item_extremum", "scope": "items_within_dimension",
                            "operator": "min" if match.group() in {"最低", "最小"} else "max",
                            "span": [start, end], "text": clause,
                            "ties": "all" if re.search(r"同分.{0,8}全部|并列.{0,8}全部|全部并列", affirmative) else "unspecified"})
    raw_spans, score_spans = [], []
    for clause, start, end in clauses:
        if re.search(r"原始(?:值|数量|作答)|原值|登记数量|登记数值", clause):
            raw_spans.append({"span": [start, end], "text": clause})
        if re.search(r"标准分|归一(?:化)?(?:分|值|得分)|百分制|得分|(?<!百)分数", clause):
            score_spans.append({"span": [start, end], "text": clause})
    view = None
    if raw_spans and not score_spans:
        view = "raw_value"
    elif score_spans and not raw_spans and not extrema:
        view = "normalized_score"

    return {"atomic_requirements": atomic_requirements(question), "extrema": extrema, "value_view": view, "raw_spans": raw_spans,
            "score_spans": score_spans, "dual_view": bool(raw_spans and score_spans),
            "block_scope_requested": bool(re.search(r"这一整块|这整块|整个维度|这一维度", affirmative)),
            "dimension_labels": sorted({str(i.get("dimension")) for i in catalogue if i.get("dimension")})}

def _dimension_evidence(question: str, catalogue: list[dict]) -> list[dict]:
    pass
    text = "，".join(c for c, _, _ in _clauses(question))
    grams = {text[i:i+2] for i in range(len(text)-1) if re.fullmatch(r"[\u4e00-\u9fff]{2}", text[i:i+2])}
    ranked = []
    for dim in sorted({str(i.get("dimension")) for i in catalogue if i.get("dimension")}):
        hits = []
        for item in catalogue:
            if item.get("dimension") != dim:
                continue
            document = " ".join(str(item.get(k) or "") for k in ("question", "summary", "topic"))
            overlap = sorted(g for g in grams if g in document)
            if overlap:
                hits.append({"id": item["id"], "question": item.get("question"), "overlap": overlap})
        ranked.append({"dimension": dim, "advisory_overlap": len({g for i in hits for g in i['overlap']}),
                       "items": sorted(hits, key=lambda x: -len(x['overlap']))[:4]})
    return sorted(ranked, key=lambda x: (-x['advisory_overlap'], x['dimension']))

def constrain(question: str, model: dict, candidate: dict, catalogue: list[dict]) -> tuple[dict, dict]:
    requirements = derive_requirements(question, catalogue)
    patched = copy.deepcopy(candidate)
    findings, patches = [], []
    items = {i['id']: i for i in catalogue}
    selected = candidate.get("question_ids") or []
    known_items = bool(selected) and all(type(q) is int and q in items for q in selected)

    expected_view = requirements['value_view']
    if known_items and expected_view and candidate.get('value_view') != expected_view:
        findings.append({"code": "explicit_item_view_conflict", "status": "CONTRADICTION",
                         "observed": candidate.get('value_view'), "expected": expected_view,
                         "evidence": requirements['raw_spans'] if expected_view == 'raw_value' else requirements['score_spans']})
        patched['value_view'] = expected_view
        patches.append({"field": "value_view", "before": candidate.get('value_view'), "after": expected_view})
    if requirements['dual_view']:
        findings.append({"code": "dual_view_request", "status": "REQUIRES_COMPOSITION",
                         "reason": "Both requested views require independent execution nodes and required facts; the shared coverage gate checks both."})
    extrema = requirements['extrema']
    dimension_item_request = (bool(extrema) and candidate.get('intent') == 'school_eval'
                              and candidate.get('dimension') in requirements['dimension_labels']
                              and len(candidate.get('schools') or []) == 1
                              and not selected and not candidate.get('regions'))
    if dimension_item_request and (len(extrema)>1 or re.search(r'只列(?:出)?(?:一|1|一个|一项)|仅列(?:出)?(?:一|1)|任选|同分.{0,12}(?:编号|题号).{0,12}(?:先|小|大)',question)):
        findings.append({'code':'item_selection_limit_not_supported','status':'UNSUPPORTED',
                         'reason':'Shared item selection supports one extremum and all ties; this request needs a different selection contract.'})
    item_scope = (len(extrema) == 1 and candidate.get('intent') == 'school_eval'
                  and candidate.get('dimension') in requirements['dimension_labels']
                  and len(candidate.get('schools') or []) == 1
                  and not selected and not candidate.get('regions'))
    if item_scope:

        outside = question
        for e in reversed(extrema):
            outside = outside[:e['span'][0]] + ' '*(e['span'][1]-e['span'][0]) + outside[e['span'][1]:]
        if not re.search(r"最低|最小|最高|最大|均值|平均分|中位数", outside):
            if candidate.get('statistic') == extrema[0]['operator']:
                patched['statistic'] = None
                patches.append({"field": "statistic", "before": candidate['statistic'], "after": None})
                findings.append({"code": "item_selection_as_school_statistic", "status": "CONTRADICTION", "evidence": extrema})

    repair_recommended = False
    retrieval = []
    if (candidate.get('intent') == 'clarify' and requirements['block_scope_requested']
            and len(candidate.get('periods') or []) == 1
            and len((candidate.get('schools') or []) + (candidate.get('regions') or [])) == 1):
        findings.append({"code": "unresolved_descriptive_dimension", "status": "INFORMATION_INSUFFICIENT",
                         "reason": "Object/year and requested block are supplied; compare measurement meanings in shared catalogue once. Do not force a dimension."})
        retrieval = _dimension_evidence(question, catalogue)
        repair_recommended = True
    trace = {"version": VERSION, "requirements": requirements, "findings": findings,
             "patches": patches, "changed": patched != candidate,
             "repair_recommended": repair_recommended, "catalogue_retrieval": retrieval,
             "unsupported": any(f['status'] == 'UNSUPPORTED' for f in findings),
             "classification": ("CONTRADICTION" if any(f['status']=='CONTRADICTION' for f in findings)
                                else "INFORMATION_INSUFFICIENT" if any(f['status']=='INFORMATION_INSUFFICIENT' for f in findings)
                                else "COMPATIBLE_OR_UNCHECKED")}
    if not requirement_preservation_enabled():
        trace.update(gate='OBSERVE_ONLY_ABLATION', would_do=(
            'clarify' if trace['unsupported'] else 'restore' if patched != candidate else 'none'),
            would_change=trace['changed'], changed=False)
        return copy.deepcopy(candidate), trace
    return patched, trace

def repair_prompt(question: str, candidate: dict, catalogue: list[dict], trace: dict) -> str:

    from packages.semantic_query.model_checker.semantic_model_checker import FIELD_SCHEMAS
    return "一次语义解析复核；不计算、不回答问题。使用同一共享目录，不添加题外知识。\n" + json.dumps({
        "question": question, "candidate": candidate, "findings": trace['findings'],
        "independently_bound_repair": trace.get('repair_authorization'),
        "visible_context": trace.get('visible_context'),
        "advisory_retrieval": trace['catalogue_retrieval'], "shared_catalogue": catalogue,
        "candidate_schema": {"type":"object","properties":FIELD_SCHEMAS,"additionalProperties":False},
        "instructions": "只执行independently_bound_repair.targets中的修正，其他字段逐字保留。原文evidence必须完全保留；前者是原文引文，上下文校名是学校目标，不能互相替代。不执行检查器可选建议，不补猜测值。没有绑定目标则不得修改。不计算业务答案。",
        "response": {"candidate": "同原解析器字段/schema的完整JSON对象", "conflict_explanation": "说明测量语义及依据原句的逐字片段；不能给业务数值"}},
        ensure_ascii=False, separators=(',', ':'))

def check_prompt(question: str, candidate: dict, catalogue: list[dict], schema: dict) -> str:
    return "独立语义检查；不计算业务答案、不查询数据库。检查结论不以候选自述或confidence为依据。\n" + json.dumps({
        'question': question, 'candidate': candidate, 'shared_catalogue': catalogue, 'shared_schema': schema,
        'instructions': '对照原问题、目录测量对象/属性/单位/时间和schema，检查条件覆盖、指代、指标层级/口径、年份、比较方向和必答输出。合理隐含语义和等价运算应接受。目录不足、执行能力不足、措辞歧义或无法表达的运算应cannot_determine，不得猜定答案或强制改维度。每个明确冲突必须引用原问题逐字片段，指出真实字段当前值、理由及修正建议；检出率与自信不能代替证据。',
        'response_schema': {'verdict': 'clearly_wrong|legal_equivalent|cannot_determine',
                            'findings': [{'field': 'candidate schema field', 'observed': 'actual field value',
                                          'original_quote': 'literal substring of question', 'reason': 'semantic conflict explanation',
                                          'suggested_value': 'schema-compatible value or null if not expressible'}],
                            'explanation': 'brief reason; no business numeric answers'}}, ensure_ascii=False, separators=(',', ':'))

def check_once(question: str, candidate: dict, catalogue: list[dict], schema: dict, callback=None) -> dict:
    result = {'calls': 0, 'transport_retries': 0, 'status': 'CALLBACK_NOT_CONFIGURED',
              'verdict': 'cannot_determine', 'findings': [], 'accepted': False}
    if callback is None:
        return result
    result['calls'] = 1
    try:
        response = _callback_result(callback, check_prompt(question, candidate, catalogue, schema), result)
        if not isinstance(response, dict) or set(response) != {'verdict', 'findings', 'explanation'}:
            raise ValueError('checker_response_shape')
        if response['verdict'] not in {'clearly_wrong', 'legal_equivalent', 'cannot_determine'}:
            raise ValueError('checker_verdict_invalid')
        if not isinstance(response['findings'], list) or not isinstance(response['explanation'], str) or not response['explanation'].strip():
            raise ValueError('checker_explanation_missing')
        for finding in response['findings']:
            if not isinstance(finding, dict) or set(finding) != {'field', 'observed', 'original_quote', 'reason', 'suggested_value'}:
                raise ValueError('checker_finding_shape')
            field, quote = finding['field'], finding['original_quote']
            if field not in CHECK_FIELDS or finding['observed'] != candidate.get(field):
                raise ValueError('checker_unbound_field')
            if not isinstance(quote, str) or not quote.strip() or quote not in question:
                raise ValueError('checker_quote_not_in_original')
            if not isinstance(finding['reason'], str) or not finding['reason'].strip():
                raise ValueError('checker_reason_missing')
        if response['verdict'] == 'clearly_wrong' and not response['findings']:
            raise ValueError('checker_wrong_without_evidence')
        if response['verdict'] == 'legal_equivalent' and response['findings']:
            raise ValueError('checker_equivalent_with_conflicts')
        result.update(response, status='VALIDATED_MODEL_OPINION', accepted=True)
    except Exception as exc:
        result.update(status='REJECTED_OR_FAILED', error=type(exc).__name__ + ':' + str(exc))
    return result

def run_preflight(question: str, model: dict, candidate: dict, catalogue: list[dict], *,
                  mode='code', checker=None, repair=None, validate=None, schema=None, context=None,
                  effective_summary=None):
    if mode not in {'none', 'code', 'code_model'}:
        raise ValueError('semantic_constraint_mode_invalid')
    if mode == 'none':
        return copy.deepcopy(candidate), {'version': VERSION, 'mode': mode, 'changed': False,
                                          'unsupported': False, 'model_check': {'calls': 0, 'status': 'DISABLED'},
                                          'model_repair': {'calls': 0, 'status': 'DISABLED'}}
    patched, trace = constrain(question, model, candidate, catalogue)
    trace['mode'] = mode
    trace['model_check'] = {'calls': 0, 'status': 'DISABLED'}
    trace['model_repair'] = {'calls': 0, 'status': 'DISABLED'}
    if mode == 'code_model' and not model_checker_research_enabled():
        trace['model_check']['status'] = 'DISABLED_DEFAULT_RESEARCH_OPT_IN_REQUIRED'
    if mode == 'code_model' and model_checker_research_enabled() and not trace['unsupported']:
        from packages.semantic_query.model_checker.semantic_model_checker import FIELD_SCHEMAS
        from packages.semantic_query.model_checker.diagnostic_contract import check_once as check_semantics
        from packages.semantic_query.model_checker.referent_boundary import process_decision, visible_context
        from packages.semantic_query.model_checker.repair_binding import authorize

        parser_schema = {'type': 'object', 'properties': copy.deepcopy(FIELD_SCHEMAS), 'additionalProperties': False}
        summary = effective_summary(patched) if callable(effective_summary) else None
        receipt = check_semantics(question, patched, catalogue, parser_schema, expanded=True,
                                 context=context, model_call=checker, effective_summary=summary)
        trace['effective_task_summary'] = summary
        decision = process_decision(receipt, question, patched, context)
        authorization = authorize(question, patched, catalogue, parser_schema, receipt, decision)
        trace.update(rule_decision=decision, repair_authorization=authorization, visible_context=visible_context(context))
        trace['model_check'] = {**receipt, **receipt['check_call'],
                                'component_status': receipt['check_call']['status'],
                                'accepted': receipt['checker_available'],
                                'effective_verdict': receipt['effective_status']}
        opinion = trace['model_check']
        trace['code_repair_advisory'] = trace['repair_recommended']
        trace['repair_recommended'] = authorization['allowed']

        final, trace['model_repair'] = repair_once(question, patched, catalogue, trace, repair, validate)
        trace['changed'] = final != candidate

        trace['original_parse_followup'] = {
            'policy': ('continue_repaired_parse_path' if trace['model_repair'].get('accepted')
                       else 'continue_original_parse_path'),
            'checker_available': opinion.get('checker_available', False),
            'checker_failure_kind': opinion.get('checker_failure_kind'),
            'effective_verdict': opinion.get('effective_verdict'),
            'rule_status': decision.get('rule_status'),
            'unsupported_completion_blocked': decision['facts']['parser_unsupported_completion'],
            'input_intent': candidate.get('intent'), 'retained_intent': final.get('intent'),
            'candidate_retained_after_shared_checks': final == patched,
            'shared_code_changed': patched != candidate,
            'semantic_repair_requested': trace['repair_recommended'],
            'semantic_repair_accepted': trace['model_repair'].get('accepted', False),
            'semantic_certified': False,
            'user_ambiguity_introduced_by_checker': False,
            'reason': ('checker_unavailable' if not opinion.get('checker_available')
                       else 'supported_uncertainty_retains_parse' if opinion.get('effective_verdict') == 'cannot_determine'
                       else 'conflict_lacks_sufficient_basis' if opinion.get('effective_verdict') == 'clearly_wrong' and not opinion.get('evidence_sufficient')
                       else 'evidence_bound_repair_attempted' if trace['repair_recommended']
                       else 'validated_opinion_retains_parse'),
        }
        return final, trace
    return patched, trace

def repair_once(question: str, candidate: dict, catalogue: list[dict], trace: dict,
                callback=None, validate=None) -> tuple[dict, dict]:
    pass
    result = {"requested": trace['repair_recommended'], "calls": 0, "accepted": False,
              "status": "NOT_NEEDED" if not trace['repair_recommended'] else "CALLBACK_NOT_CONFIGURED"}
    if not trace['repair_recommended'] or callback is None:
        return candidate, result
    result['calls'] = 1
    try:
        response = _callback_result(callback, repair_prompt(question, candidate, catalogue, trace), result)
        if not isinstance(response, dict) or set(response) != {'candidate', 'conflict_explanation'}:
            raise ValueError('repair_response_shape')
        repaired, explanation = response['candidate'], response['conflict_explanation']
        if not isinstance(repaired, dict) or not isinstance(explanation, str) or not explanation.strip():
            raise ValueError('repair_explanation_or_candidate_missing')
        if validate is None:
            raise ValueError('shared_schema_validator_required')
        validate(repaired)
        from packages.semantic_query.model_checker.repair_binding import validate_repair
        validate_repair(question, candidate, repaired, trace.get('repair_authorization', {}))
        checked, check = constrain(question, repaired, repaired, catalogue)
        if check['changed'] or check['unsupported']:
            raise ValueError('repair_conflicts_with_original_requirements')
        result.update(accepted=True, status='ACCEPTED_UNVERIFIED_SEMANTICS', conflict_explanation=explanation)

        return checked, result
    except Exception as exc:
        result.update(status='REJECTED_OR_FAILED', error=type(exc).__name__ + ':' + str(exc))
        return candidate, result
