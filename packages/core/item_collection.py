"""R11: author-supplied annual item availability, applied once at read time."""

VERSION = 'R11-uncollected-as-missing-v1-20261019'
ITEM_NOTE = '该题项当年未采集'
TEMPORAL_NOTE = '各年采集的题项不完全相同，得分只按当年已采集的题项计算。'


def enabled():
    from packages.core.research_data import enabled as research_enabled, config
    return research_enabled() and bool(config().get('uncollected_items'))


def rules():
    from packages.core.research_data import ROOT, config, _load
    return _load(str((ROOT/config()['uncollected_items']).resolve()))


def collected_ids(year, ids):
    missing = set(rules()['items_by_year'].get(str(year), ())) if enabled() else set()
    return tuple(i for i in ids if int(i) not in missing)


def install(connection):
    """The sole row-exclusion point; main tables and original values stay intact.

    Every existing tool and independent replay reads ``answers`` through this
    TEMP view. Only the explicitly listed year/item pairs disappear; a genuine
    collected zero remains an answer. The TEMP table lives in this connection.
    """
    pairs = [(str(year), int(qid)) for year, ids in rules()['items_by_year'].items() for qid in ids]
    connection.execute('CREATE TEMP TABLE r11_uncollected_items (year TEXT, question_id INTEGER, PRIMARY KEY(year,question_id)) WITHOUT ROWID')
    connection.executemany('INSERT INTO r11_uncollected_items VALUES (?,?)', pairs)
    connection.commit()  # Finish TEMP setup before callers open their read snapshot.
    connection.execute('''CREATE TEMP VIEW answers AS
      SELECT a.* FROM main.answers a
      WHERE NOT EXISTS (
        SELECT 1 FROM main.schools annual JOIN r11_uncollected_items u
          ON u.year=CAST(annual.year AS TEXT) AND u.question_id=a.question_id
        WHERE annual.id=a.school_id)''')


def boundary(question, candidate=None, context=None):
    """A fixed availability response for a requested item, before any values."""
    if not enabled():
        return None
    from packages.semantic_query import runtime as rt
    from packages.semantic_query.regional_capabilities import years
    # A year-less preflight has not yet inherited the request's trusted focus.
    import re
    if not re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question) and not (context or {}).get('last_year'):
        return None
    ids = rt._scope_question_ids(question) or [qid for qid, _ in rt._catalogue_alias_items(question)]
    if not ids:
        span = ((candidate or {}).get('evidence') or {}).get('metric')
        if isinstance(span,str) and span and span in question:
            ids = list((candidate or {}).get('question_ids') or [])
    if not ids:
        return None
    periods = years(question, context or {})[0]
    return unavailable(question, [(year, int(qid)) for year in periods for qid in ids])


def unavailable(question, pairs):
    pairs = list(dict.fromkeys((str(year), int(qid)) for year,qid in pairs))
    missing = [(str(year), int(qid)) for year,qid in pairs if int(qid) not in collected_ids(year, [qid])]
    if not missing or len(missing) < len(pairs):
        return None
    message = '；'.join(str(year)+'年第'+str(qid)+'题：'+ITEM_NOTE for year,qid in missing)+'。'
    return dict(intent='unavailable', reason='uncollected_item', data_category='uncollected_item',
                original=question, message=message, business_status='UNAVAILABLE',
                uncollected_items=[dict(year=y, question_id=i) for y,i in missing], collection_policy=VERSION)


def bind_availability(question, routed):
    """Use final task fields after context, item and object reconciliation."""
    if not enabled() or routed.get('intent') in {'clarify','unavailable'}:
        return routed
    pairs = []
    if routed.get('public_plan'):
        pairs = [(q['year'],q['question_id']) for q in routed['public_plan']['queries'] if q.get('question_id')]
    else:
        metric = routed.get('metric') or routed.get('metric_info') or {}
        ids = [metric['qid']] if metric.get('qid') else list((routed.get('metric_set') or {}).get('qids') or [])
        periods = routed.get('years') or [routed[k] for k in ('year','year_a','year_b','secondary_year') if routed.get(k)]
        pairs = [(year,qid) for year in dict.fromkeys(periods) for qid in ids]
    missing = unavailable(question,pairs)
    if missing:
        return missing
    if routed.get('public_plan'):
        for q in routed['public_plan']['queries']:
            if q.get('question_id') and not collected_ids(q['year'], [q['question_id']]):
                q['availability'] = 'UNCOLLECTED'
    return routed


def temporal_notes(result, spec=None):
    if not enabled():
        return []
    periods = set()
    for source in (result, spec or {}):
        for key in ('year_a','year_b','secondary_year'):
            if source.get(key) is not None:
                periods.add(str(source[key]))
        for key in ('years','school_years'):
            periods.update(str(y) for y in source.get(key) or [])
        periods.update(str(q['year']) for q in (source.get('public_plan') or {}).get('queries', []) if q.get('year'))
    if len(periods) > 1:
        from packages.semantic_query.regional_capabilities import TEMPORAL_NOTE as ANNUAL_NOTE
        return [ANNUAL_NOTE, TEMPORAL_NOTE]
    return []
