import copy
from packages.semantic_query.model_checker.sparse_contract import input_audit

def authorize(question, candidate, catalogue, schema, validation, decision):
    out = dict(allowed=False, targets={}, original_quotes=[], basis=[],
               invalid_proposals_executable=False, model_opinion_preserved=True)
    if not validation.get('checker_available'):
        return out

    out['invalid_proposals_ignored'] = not validation.get('suggestion_validation', {}).get('valid', True)
    audit = input_audit(question, candidate, catalogue, schema)
    out['code_detected_conflicts'] = copy.deepcopy(audit['deterministic_candidate_contradictions'])
    facts = decision.get('facts', {})
    if (decision.get('school_status') == 'clearly_wrong'
            and facts.get('requirement_state') == 'resolved'):
        out['targets']['schools'] = copy.deepcopy(facts['expected_schools'])
        out['original_quotes'].extend(facts['original_referent_quotes'] or
            [name for name in facts['expected_schools'] if name in question])
        out['basis'].append('code_unique_visible_school_binding')
    for conflict in audit['deterministic_candidate_contradictions']:
        field, target = conflict['field'], copy.deepcopy(conflict['expected'])
        requirements = [r for r in audit['requirements'] if r['value'] in target]
        if not requirements or not all(r['quote'] in question for r in requirements):
            continue
        if field == 'requested_outputs':
            target = list(dict.fromkeys(candidate.get(field, []) + target))
        out['targets'][field] = target
        out['original_quotes'].extend(r['quote'] for r in requirements)
        out['basis'].append('code_literal_original_requirement_'+field)
    for issue in validation.get('issues', []):
        if issue.get('type') != 'error': continue
        quoted = issue.get('evidence', {}).get('quotes', [])
        fields = issue.get('fields', [])
        if 'schools' in fields and decision.get('school_status') == 'clearly_wrong':
            facts = decision['facts']
            if facts['requirement_state'] == 'resolved' and any(
                q in question and (q in facts['original_referent_quotes'] or
                any(name in q for name in facts['expected_schools'])) for q in quoted):
                out['targets']['schools'] = copy.deepcopy(facts['expected_schools'])
                out['basis'].append('original_quote_plus_unique_visible_school_binding')
                out['original_quotes'].extend(quoted)
        for conflict in audit['deterministic_candidate_contradictions']:
            field = conflict['field']
            if field not in fields: continue
            requirements = [r for r in audit['requirements'] if r['value'] in conflict['expected']]
            if requirements and all(any(r['quote'] in q for q in quoted) for r in requirements):
                target = conflict['expected']
                if field == 'requested_outputs':
                    target = list(dict.fromkeys(candidate.get(field, []) + target))
                out['targets'][field] = copy.deepcopy(target)
                out['basis'].append('literal_original_requirement_'+field)
                out['original_quotes'].extend(quoted)
    out['original_quotes'] = list(dict.fromkeys(out['original_quotes']))
    out['allowed'] = bool(out['targets'])
    out['reason'] = ('independent_targets_only_model_verdict_preserved' if out['allowed'] else
                    'no_independent_target_retains_original_parse_and_opinion')
    return out

def validate_repair(question, before, after, authorization):
    if not authorization.get('allowed'): raise ValueError('no_independent_repair_authorization')
    changed = {k for k in set(before)|set(after) if before.get(k) != after.get(k)}
    if not changed <= set(authorization['targets']):
        raise ValueError('repair_changed_unbound_fields')
    for field, expected in authorization['targets'].items():
        if after.get(field) != expected: raise ValueError('repair_target_not_independently_bound_'+field)
    for field, quote in (after.get('evidence') or {}).items():
        if isinstance(quote,str) and quote and quote not in question:
            raise ValueError('repair_evidence_not_original_'+field)

    if after.get('evidence') != before.get('evidence'):
        raise ValueError('repair_changed_original_evidence')
