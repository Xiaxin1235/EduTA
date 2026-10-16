import copy

from packages.semantic_query.model_checker.semantic_model_checker import InvalidEvidence

def scope_projection(summary, catalogue):
    if summary is None:
        return None
    if not isinstance(summary, dict):
        raise InvalidEvidence('effective_summary_must_be_object')
    available = summary.get('compilation_available') is True
    declared = available and summary.get('granularity') in ('dimension', 'dimension_pair')
    dimensions = []
    if declared:
        raw_dimensions = summary.get('target_dimensions')
        if raw_dimensions is not None and not isinstance(raw_dimensions, list):
            raise InvalidEvidence('invalid_effective_dimension_scope')
        dimensions = raw_dimensions or []
        if not dimensions and summary.get('target_dimension'):
            dimensions = [summary['target_dimension']]
        labels = {item.get('dimension') for item in catalogue if item.get('dimension')}
        if (not isinstance(dimensions, list)
                or any(not isinstance(d, str) or d not in labels for d in dimensions)
                or len(dimensions) != len(set(dimensions))):
            raise InvalidEvidence('invalid_effective_dimension_scope')
        expected_count = 2 if summary['granularity'] == 'dimension_pair' else 1
        if len(dimensions) != expected_count:
            raise InvalidEvidence('invalid_effective_dimension_cardinality')
    return dict(source='task_summary.effective_task', compilation_available=available,
                dimension_scope_available=declared,
                granularity=summary.get('granularity'),
                covered_dimensions=copy.deepcopy(dimensions),
                dimension_targets=[dict(dimension=d, scope='whole_dimension',
                    catalogue_ids=[item['id'] for item in catalogue if item.get('dimension') == d])
                    for d in dimensions],
                requested_result_statistics=copy.deepcopy(summary.get('requested_result_statistics') or []),
                required_outputs=copy.deepcopy(summary.get('required_outputs') or []),
                limitation='Execution scope observation only. Original requirements and semantic correctness must still be reviewed.')

def validate_scope_binding(binding, scope, catalogue):
    pass
    if not isinstance(binding, dict) or set(binding) != {
            'required_dimensions', 'covered_dimensions', 'missing_dimensions'}:
        return ['invalid_dimension_scope_binding']
    labels = {item.get('dimension') for item in catalogue if item.get('dimension')}
    for values in binding.values():
        if (not isinstance(values, list) or any(not isinstance(d, str) or d not in labels for d in values)
                or len(values) != len(set(values))):
            return ['invalid_dimension_scope_values']
    if not scope or not scope['dimension_scope_available']:
        return ['dimension_scope_source_unavailable']
    required = binding['required_dimensions']
    covered = binding['covered_dimensions']
    missing = binding['missing_dimensions']
    errors = []

    if set(covered) != set(scope['covered_dimensions']):
        errors.append('covered_dimensions_mismatch_effective_task')
    if set(missing) != set(required) - set(scope['covered_dimensions']):
        errors.append('missing_dimensions_mismatch_effective_task')
    if set(required) == set(scope['covered_dimensions']):
        errors.append('dimension_conflict_without_scope_difference')
    return errors
