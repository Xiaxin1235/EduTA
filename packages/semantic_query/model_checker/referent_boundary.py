"""Bounded school coreference policy; no answer lookup, task edits or I/O.

The only context projection is the real parser's existing public context. A
candidate never supplies its own antecedent. Unknown identity/alias mappings
are not guessed. Policy decisions are distinct from the saved model opinion.
"""
import copy
import re

from packages.semantic_query.runtime import _public_context

SCHOOL_KEYS = ('last_school', 'last_school_fact_entity',
               'last_school_compare_a', 'last_school_compare_b')
SCHOOL_FIELDS = frozenset(('schools', 'intent', 'missing', 'clarify_reason', 'evidence'))
REF = re.compile(r'上述两所学校|前述两所学校|上述学校|前述学校|这两所学校|两校|'
                 r'该校|本校|这所学校|这个学校|前者|后者')


def visible_context(context=None):
    if context is not None and not isinstance(context, dict):
        raise ValueError('context_must_be_real_context_mapping_or_none')
    public = _public_context(context)
    return {'state': 'present' if public else 'not_supplied', 'value': copy.deepcopy(public),
            'limitation': 'Only supplied existing parser context fields are visible. Missing '
                          'fields/antecedents are not inferred from the candidate or a reference.'}


def school_facts(question, candidate, context=None):
    public = visible_context(context)
    values = public['value']
    names = {k: v for k in SCHOOL_KEYS if isinstance((v := values.get(k)), str) and v.strip()}
    known = list(dict.fromkeys(names.values()))
    pair = [names.get('last_school_compare_a'), names.get('last_school_compare_b')]
    pair_valid = all(pair) and pair[0] != pair[1]
    # Bare 前者/后者 can refer to dimensions/metrics, not schools. Only a
    # supplied ordered school antecedent makes this bounded rule applicable.
    references = [m for m in REF.finditer(question)
                  if m.group() not in ('前者','后者') or pair_valid]
    # Explicit names already present in the actual public context can be read
    # literally; no school vocabulary, answer bank or fuzzy identity guessing.
    explicit = [n for n in known if n in question and not REF.fullmatch(n)]
    expected, state, basis = None, 'not_applicable', []
    if explicit:
        # Conservative scope: positive query clause only, with no negation,
        # exclusion, method example or school identity qualification.
        if re.search(r'不查|不看|不问|不是|排除|例如|比如|假设|别名|简称', question):
            state = 'unrecognized_scope'
        else:
            expected = sorted(explicit, key=question.index)
            state, basis = 'resolved', ['explicit_name_in_original_and_supplied_context']
    elif references and (known or candidate.get('schools') or
                         candidate.get('intent', '').startswith('school') or
                         'schools' in candidate.get('missing', []) or '学校' in question or '该校' in question):
        tokens = [m.group() for m in references]
        selections = []
        for token in tokens:
            if token in ('前者', '后者'):
                selections.append([pair[0 if token == '前者' else 1]] if pair_valid else None)
            elif token in ('上述两所学校', '前述两所学校', '这两所学校', '两校'):
                selections.append(pair if pair_valid and set(known) == set(pair) else None)
            elif token in ('上述学校', '前述学校'):
                selections.append(known if len(known) == 1 else None)
            else:
                selections.append(known if len(known) == 1 else None)
        if selections and all(s is not None for s in selections) and all(s == selections[0] for s in selections):
            expected, state = list(selections[0]), 'resolved'
            basis = ['original_referent', 'supplied_existing_context_fields']
        elif selections and all(s is not None for s in selections):
            state = 'unrecognized_scope'  # several ordered references require semantic direction checks
        else:
            state = 'multiple_candidates' if len(known) > 1 else 'missing_antecedent'
            basis = ['no_unique_antecedent_in_visible_input']
    actual = copy.deepcopy(candidate.get('schools', []))
    if state == 'resolved':
        if actual == expected or (set(actual) == set(expected) and len(actual) == len(expected)):
            # Object membership only; this does not certify comparison direction.
            parse_state = 'matches_resolved_object'
        elif actual and all(n in known for n in actual):
            parse_state = 'wrong_known_object'
        elif not actual:
            parse_state = 'omitted_resolved_object'
        else:
            parse_state = 'identity_mapping_unavailable'
    elif state in ('missing_antecedent', 'multiple_candidates'):
        parse_state = 'unsupported_completion' if actual else 'awaiting_information'
    else:
        parse_state = 'not_decided_by_boundary_rule'
    return dict(context=public, requirement_state=state, parser_state=parse_state,
                expected_schools=expected, actual_schools=actual, known_context_objects=known,
                original_referent_quotes=[m.group() for m in references], basis=basis,
                required_missing_information=('Unique school antecedent or explicit school name'
                    if state in ('missing_antecedent', 'multiple_candidates') else
                    'Verified school identity/alias mapping' if parse_state == 'identity_mapping_unavailable' else None),
                original_requirement_uncertain=state in ('missing_antecedent', 'multiple_candidates'),
                parser_unsupported_completion=parse_state == 'unsupported_completion')


def process_decision(validation, question, candidate, context=None):
    """Keep raw verdict/issues; independently annotate a bounded rule decision.

    Invalid model interfaces remain unavailable. Rule facts cannot rescue them.
    Unresolved referents never pick a school or authorize a correction. Other
    semantic issues remain visible and participate in the aggregate verdict.
    """
    facts = school_facts(question, candidate, context)
    out = dict(model_status=validation.get('raw_status'),
               model_effective_status=validation.get('effective_status'),
               model_issues=copy.deepcopy(validation.get('issues', [])),
               model_response_rewritten=False, rule_applied=False, rule_status=None,
               school_status=None, facts=facts, issue_dispositions=[],
               correction_allowed=False, suggestions_executed=False, task_preserved=True,
               limitation='Boundary facts do not certify unrelated semantic judgments or unknown aliases.')
    if not validation.get('checker_available'):
        out['stop_domain'] = 'checker_unavailable'
        return out
    state, parse = facts['requirement_state'], facts['parser_state']
    if state in ('missing_antecedent', 'multiple_candidates'):
        school_status = 'cannot_determine'
    elif state == 'resolved':
        school_status = ('legal_equivalent' if parse == 'matches_resolved_object' else
                         'clearly_wrong' if parse in ('wrong_known_object', 'omitted_resolved_object') else
                         'cannot_determine')
    else:
        out['rule_status'] = validation['effective_status']
        return out
    out.update(rule_applied=True, school_status=school_status)
    other_types = []
    for i, issue in enumerate(validation.get('issues', [])):
        fields = set(issue.get('fields', []))
        quotes = issue.get('evidence', {}).get('quotes', [])
        # Only a school-bound opinion is replaceable. Mixed issues retain all
        # their assertions; no general semantic failure is swallowed here.
        school_only = bool(fields) and fields <= SCHOOL_FIELDS and 'schools' in fields
        anchored = any(q in question and (REF.search(q) or any(n in q for n in facts['known_context_objects'])) for q in quotes)
        direction_claim = bool(re.search(r'顺序|方向|相减|差值|减去|比较双方', issue.get('evidence', {}).get('reason', '')))
        bounded = school_only and anchored and not direction_claim
        out['issue_dispositions'].append(dict(index=i, school_boundary_only=bounded,
            disposition='retained_raw_school_opinion_with_independent_boundary_result' if bounded else 'retained_in_overall_aggregation'))
        if not bounded:
            other_types.append(issue['type'])
    out['rule_status'] = ('clearly_wrong' if school_status == 'clearly_wrong' or 'error' in other_types else
                          'cannot_determine' if school_status == 'cannot_determine' or 'uncertain' in other_types else
                          'legal_equivalent')
    # An unsupported selection is separately blocked, rather than mislabeled
    # as a known wrong school or hidden inside original demand ambiguity.
    out['original_task_follow_up'] = ('hold_unsupported_completion_without_selecting_school'
        if facts['parser_unsupported_completion'] else
        'preserve_original_clarification_need' if school_status == 'cannot_determine' else
        'record_definite_object_mismatch_no_automatic_edit' if school_status == 'clearly_wrong' else
        'school_dimension_matches_keep_other_checks')
    return out
