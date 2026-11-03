"""Shared model TaskDraft vocabulary; executable plans remain program-owned."""
REGIONAL_INTENTS = frozenset({'region_profile', 'region_position', 'province_ranking',
                              'region_multi_compare', 'region_series', 'national_eval'})
LEGACY_MODEL_INTENTS = frozenset({'clarify', 'region_eval', 'school_eval', 'school_fact',
                          'region_compare', 'school_compare', 'region_longitudinal',
                          'region_ranking', 'national_ranking'}) | REGIONAL_INTENTS
# One registry for typed intent vocabulary AND executable scope contracts.
# Legacy fallback remains available; new supported combinations compile before it.
CAPABILITY_REGISTRY = {
    **{i: dict(compiler='legacy') for i in LEGACY_MODEL_INTENTS - REGIONAL_INTENTS},
    **{i: dict(compiler='regional', max_years=4, dimensions=6,
              single_scope=i in {'region_profile', 'region_position'}) for i in REGIONAL_INTENTS},
}
# Legacy national ranking is an alias of the registered province compiler.
CAPABILITY_REGISTRY['national_ranking']=dict(compiler='regional',canonical_intent='province_ranking',max_years=1,dimensions=1)
MODEL_INTENTS = frozenset(CAPABILITY_REGISTRY)
COMPOSITION = dict(max_years=4, dimensions=6, school_items=83,
                   kinds={'school_score', 'school_fact', 'region_value'})

def supported_scope(intent, schools, regions, periods, dimensions=0, items=0, topics=()):
    """Shape contract shared by first-slice gates; identity is bound by tools."""
    if intent not in CAPABILITY_REGISTRY or schools and regions:
        return False
    objects=len(schools or regions)
    years=len(periods)
    if topics:
        from packages.semantic_query.catalogue import topic_catalogue
        if (dimensions or items or len(topics)!=len(set(topics)) or
                any(t not in {x['name'] for x in topic_catalogue()} for t in topics) or
                len(topics)>1 and (years!=1 or objects>1) or intent in {'region_profile','region_position'}):
            return False
    if not 1 <= years <= COMPOSITION['max_years'] or dimensions > COMPOSITION['dimensions']:
        return False
    if CAPABILITY_REGISTRY[intent].get('single_scope'):
        return objects == years == 1
    if intent in {'national_eval','national_ranking','province_ranking'} and objects==0:
        return years <= CAPABILITY_REGISTRY[intent].get('max_years',COMPOSITION['max_years'])
    if objects == 1:
        return (not items or items <= COMPOSITION['school_items'] and years == 1) and (dimensions <= 1 or years == 1)
    return years == 1 and dimensions <= 1 and items <= 1


def registered(intent):
    return intent in CAPABILITY_REGISTRY

def ranking_intent(intent):
    return registered(intent) and (intent in {'region_ranking', 'national_ranking'} or
           CAPABILITY_REGISTRY[intent].get('compiler') == 'regional')

EXECUTABLE_INTENTS = (MODEL_INTENTS - REGIONAL_INTENTS) | {'public_task', 'unavailable'}
TASK_FIELDS = frozenset({'intent', 'regions', 'schools', 'periods', 'dimension', 'statistic',
                        'aggregation', 'school_filter', 'question_ids', 'value_view', 'compound_key',
                        'school_field', 'target_rank', 'target_top_n', 'requested_outputs',
                        'clarify_reason', 'missing', 'evidence', 'confidence', 'ranking_order', 'benchmark', 'topics'})
CHOICE_FIELDS = {'ranking_order': {None, 'top', 'bottom'}, 'benchmark': {None, 'national', 'province'}}
