"""Mode-neutral semantic types for existing task plans (no model or data answers).

Optional v1 metadata is derived from executable fields, never an authority to
change them. Legacy plans infer the same metadata without changing their view,
aggregation, operand order, or output selection.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re

VERSION = 'typed-metric-plan-v1'
NO_DATA_STATUSES = {'unavailable_year', 'empty_filter'}


def explicit_view(question, runtime):
    view=runtime._explicit_value_view(question)
    if re.search(r'原值',runtime._scope_assertion_text(question)):
        if view=='normalized_score':return 'mixed_raw_and_normalized_score'
        if view is None:return 'raw_value'
    return view


def explicit_aggregation(question, runtime):
    text=runtime._scope_assertion_text(question)
    if runtime._explicit_answer_pooled_mean(question) or re.search(r'作答.{0,8}(?:放在一起|合在一起).{0,8}平均',text):
        return 'answer_pooled_mean'
    if re.search(r'(?:学校等权|每校等权|先算每校均分|先按学校汇总)',text):
        return 'school_equal_mean'
    return None


def explicit_subtraction_operands(plan, question, runtime):
    """Return uniquely requested operand nodes, without changing a plan."""
    queries=plan['queries']
    if len(queries)!=2:
        return
    text=runtime._scope_assertion_text(question or '')
    # Every discriminating component may name an operand. Shared entity/year
    # do not make distinct questionnaire items the same execution identity.
    from packages.semantic_query.catalogue import load_semantic_catalogue, _EXPLICIT_QUESTION_PATTERNS, _question_number
    for pattern in _EXPLICIT_QUESTION_PATTERNS:
        text=pattern.sub(lambda m:'Q'+str(_question_number(m.group(1))),text)
    catalogue={x['id']:x for x in load_semantic_catalogue()['items']}
    groups=[[],[]]
    labels={'mean':['均值','平均值','均分'],'median':['中位数'],'max':['最高值','最大值'],'min':['最低值','最小值'],
            'raw_value':['原始值','原值'],'normalized_score':['标准分','归一化分'],
            'school_equal_mean':['学校等权','每校等权'],'answer_pooled_mean':['作答池化']}
    for i,q in enumerate(queries):
        other=queries[1-i]
        for field in ('entity','year','question_id','dimension','statistic','view','aggregation','school_filter'):
            value=q.get(field)
            if value is None or value==other.get(field):continue
            if field=='entity':groups[i].extend(q.get('direction_aliases') or [str(value),re.sub(r'\s+','',str(value))])
            elif field=='question_id':
                item=catalogue.get(value,{})
                groups[i].extend(['Q'+str(value),item.get('question',''),item.get('summary','')]+list(item.get('aliases') or []))
            elif field=='dimension':groups[i].extend(runtime._DIMENSION_ALIASES.get(value,[value]))
            else:groups[i].extend(labels.get(value,[str(value)]))
    shared=set(groups[0])&set(groups[1])
    groups=[[x for x in dict.fromkeys(g) if x and x not in shared] for g in groups]
    if not all(groups):return None
    expected=[]
    for a,b in [(0,1),(1,0)]:
        if any(re.search(re.escape(left)+r'(?:年)?\s*(?:减去|减|[-−])\s*'+re.escape(right)+r'(?!\d)',text,re.I)
               for left in groups[a] for right in groups[b]):
            expected.append([queries[a]['id'],queries[b]['id']])
    mentions=[min([text.find(token) for token in group if token in text] or [-1]) for group in groups]
    if all(i>=0 for i in mentions):
        order=sorted(range(2),key=lambda i:mentions[i])
        if re.search(r'后者\s*减(?:去)?\s*前者',text):
            expected.append([queries[i]['id'] for i in reversed(order)])
        elif re.search(r'前者\s*减(?:去)?\s*后者',text):
            expected.append([queries[i]['id'] for i in order])
    unique={tuple(x) for x in expected}
    if len(unique)>1:
        raise ValueError('typed_comparison_direction_uncertain')
    if len(unique)==1:
        return list(next(iter(unique)))
    return None


def check_explicit_direction(plan, question, runtime):
    """Bind unambiguous literal/positional A-minus-B; leave other wording open."""
    wanted=explicit_subtraction_operands(plan, question, runtime)
    if wanted is not None:
        queries=plan['queries']
        for step in plan.get('steps',[]):
            if step.get('op',step.get('operation'))=='subtract' and set(step['args'])=={q['id'] for q in queries} and step['args']!=wanted:
                raise ValueError('typed_explicit_comparison_direction_mismatch')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf8')).hexdigest()


def metric_type(query, catalogue):
    ids = query.get('indicator_ids') or ([query['question_id']] if query.get('question_id') else [])
    if query.get('topic'):
        from packages.semantic_query.catalogue import topic_definition
        members=topic_definition(query['topic'])['indicator_ids']
        if (query.get('dimension') or query.get('question_id') or set(ids)!=set(members) or
                query.get('view')!='normalized_score' or query.get('statistic')!='mean' or query.get('aggregation')!='school_equal_mean'):
            raise ValueError('typed_topic_binding_mismatch')
    view = query.get('view')
    kind = query['kind']
    stat = query.get('statistic')
    agg = query.get('aggregation')
    count = view == 'school_count' or (kind == 'region_value' and stat == 'count' and not ids)
    if count:
        if ids and view != 'school_count':
            raise ValueError('typed_count_view_mismatch')
        family, scale, unit, transform = 'school_count', 'distinct_valid_schools', '所', 'identity'
    elif view == 'raw_value':
        if len(ids) != 1 or ids[0] not in catalogue:
            raise ValueError('typed_raw_requires_one_catalogue_measure')
        if kind not in {'school_fact', 'region_value', 'region_item_rank'}:
            raise ValueError('typed_raw_assessment_not_supported')
        family, scale, unit, transform = 'raw', 'catalogue:'+str(ids[0]), catalogue[ids[0]]['unit'], 'identity'
    elif view == 'normalized_score':
        family, scale, unit, transform = 'score', 'score_0_100', '分', 'normalized_value_times_100'
        if kind in {'school_score', 'school_assessment', 'school_dimension_rank'}:
            transform = 'normalized_item_mean' if query.get('topic') else 'native_template_weighted_score'
    else:
        raise ValueError('typed_unknown_view')
    source_scales=[{k:catalogue[i].get(k) for k in ('id','unit','value_type','scale','coding_status')} for i in ids]
    arithmetic=['subtract','add','mean','divide','divide_percent']
    if family == 'raw' and (catalogue[ids[0]].get('value_type') in {'category','boolean'} or catalogue[ids[0]].get('scale') == 'coded_category'):
        arithmetic=[]
    basis = 'registered_answer' if agg == 'none' else 'answer' if agg == 'answer_pooled_mean' else 'school'
    result = dict(version=VERSION, family=family, scale_id=scale, unit=unit,
                  source_view=view, allowed_transform=transform,
                  metric_ids=list(ids), dimension=query.get('dimension'),
                  statistical_unit=basis, aggregation=agg, statistic=stat,
                  source_scales=source_scales, allowed_operations=arithmetic,
                  object_binding={k:copy.deepcopy(query.get(k)) for k in ('kind','entity','year','school_filter','dimension','topic')})
    supplied = query.get('semantic_type')
    if supplied is not None and supplied != result:
        raise ValueError('typed_metric_binding_mismatch')
    return result


def operation_type(step, types):
    op = step.get('op', step.get('operation'))
    operands = [types[x] for x in step['args']]
    if not operands or op not in {'subtract', 'add', 'mean', 'divide', 'divide_percent'}:
        raise ValueError('typed_invalid_operation')
    if len({(x['family'], x['scale_id'], x['unit']) for x in operands}) != 1:
        raise ValueError('typed_incompatible_scales')
    if any(op not in x['allowed_operations'] for x in operands):
        raise ValueError('typed_operation_not_allowed_for_scale')
    # Equal display units do not license arithmetic across different raw measures.
    result = copy.deepcopy(operands[0])
    result.update(source_view='derived', allowed_transform=op, statistical_unit='derived_operand',
                  aggregation='ordered_'+op, statistic=None,
                  object_binding={'ordered_operands':[copy.deepcopy(x['object_binding']) for x in operands]})
    if op in {'divide', 'divide_percent'}:
        result.update(family='ratio', scale_id='ratio_of:'+operands[0]['scale_id'],
                      unit='倍' if op == 'divide' else '%')
    direction = None
    if op == 'subtract':
        direction = dict(object_node=step['args'][0], benchmark_node=step['args'][1], definition='A_MINUS_B')
    if step.get('comparison') is not None and step['comparison'] != direction:
        raise ValueError('typed_comparison_direction_mismatch')
    if step.get('semantic_type') is not None and step['semantic_type'] != result:
        raise ValueError('typed_operation_binding_mismatch')
    return result, direction


def bind_types(plan, catalogue, *, emit=True):
    """Validate existing metadata or infer it; never rewrite execution fields."""
    result = copy.deepcopy(plan)
    version=result.get('semantic_type_version')
    if version is not None:
        if version!=VERSION:
            raise ValueError('typed_unknown_plan_version')
        if any(q.get('semantic_type') is None for q in result['queries']) or any(s.get('semantic_type') is None or (s.get('op',s.get('operation'))=='subtract' and s.get('comparison') is None) for s in result.get('steps',[])):
            raise ValueError('typed_plan_metadata_missing')
    if emit:
        result['semantic_type_version']=VERSION
    types = {}
    for query in result['queries']:
        types[query['id']] = metric_type(query, catalogue)
        if emit:
            query['semantic_type'] = copy.deepcopy(types[query['id']])
    for step in result.get('steps', []):
        typ, direction = operation_type(step, types)
        types[step['id']] = typ
        if emit:
            step['semantic_type'] = copy.deepcopy(typ)
            if direction is not None:
                step['comparison'] = direction
    return result, types


def bind_routed_plan(routed, spec, catalogue):
    """Project ordinary compiled scopes into the EXISTING public-plan format.

    This closes the metadata handoff, without replacing native tools or changing
    their values. Geography still needs the execution connection via bind_regions.
    Unsupported task families retain their existing compiler, not a guessed plan.
    """
    if routed.get('public_plan') is not None:
        result = bind_types(routed['public_plan'], catalogue)[0]
    else:
        operation = spec.get('operation')
        if operation not in {'school_eval', 'region_eval', 'school_compare', 'region_compare'}:
            return None
        if routed.get('metric') or routed.get('metric_set') or routed.get('metric_info'):
            return None  # Existing item/public/native-FH paths own their leaf types.
        school = operation.startswith('school_')
        entities = ([routed.get('school_a'), routed.get('school_b')] if operation=='school_compare' else
                    [routed.get('region_a'), routed.get('region_b')] if operation=='region_compare' else
                    [routed.get('school') or routed.get('school_name')] if school else
                    [routed.get('region') or routed.get('region_keyword') or routed.get('region_name')])
        year = routed.get('year')
        if not all(isinstance(e, str) and e for e in entities) or not year:
            return None
        dims = spec.get('target_dimensions') or [routed.get('target_dimension')]
        if len(entities)>1 and len(dims)>1:
            return None  # No flattening of an unimplemented cross-product.
        queries = []
        for entity in entities:
            for dimension in dims:
                count = spec.get('target_stat')=='count'
                queries.append(dict(id='v'+str(len(queries)+1), kind='school_score' if school else 'region_value',
                    entity=entity, year=str(year), dimension=dimension, question_id=None,
                    view='school_count' if count else 'normalized_score',
                    statistic='template_weighted_score' if school else spec.get('target_stat') or 'mean',
                    aggregation='template_weighted_dimension_mean' if school else routed.get('aggregation') or 'school_equal_mean',
                    school_filter=routed.get('school_filter')))
        steps=[]
        if len(queries)==2 and 'difference' in spec.get('requested_result_statistics', []):
            operands=[q['id'] for q in queries]
            if spec.get('subtraction_direction')==-1:operands.reverse()
            steps.append(dict(id='d1',operation='subtract',args=operands))
        result=bind_types(dict(queries=queries,steps=steps,selection=None,
                               chart_requested=bool(spec.get('chart_requested'))),catalogue)[0]
    # The metadata bridge must not silently certify the opposite operation.
    from packages.semantic_query import runtime
    check_explicit_direction(result, routed.get('original') or '', runtime)
    requirements=spec.get('atomic_requirements') or {}
    bindings=[]
    for atom in requirements.get('atoms', []):
        nodes=[]
        for q in result['queries']:
            if atom['kind']=='dimension' and q.get('dimension')==atom['value']:nodes.append(q['id'])
            if atom['kind']=='indicator' and q.get('question_id')==atom['value']:nodes.append(q['id'])
            if atom['kind']=='value_view' and q.get('view')==atom['value']:nodes.append(q['id'])
        if atom['kind']=='result' and atom['value']=='difference':
            nodes.extend(s['id'] for s in result['steps'] if s.get('operation')=='subtract')
        bindings.append(dict(requirement_id=atom['id'],node_ids=nodes,
                             output_fields=['chart_requested'] if atom['value']=='charts' else
                             ['requested_outputs'] if atom['kind']=='output' else []))
    result['requirement_bindings']=bindings
    result['original_question_sha256']=requirements.get('question_sha256')
    return result


def region_identity(region):
    level = {'省份':'province', '地级市':'city', '城市':'city', '区县':'district',
             'province':'province', 'city':'city', 'district':'district', 'national':'national', 'corps':'corps', '单列兵团':'corps'}.get(region.get('region_type'))
    if not level:
        raise ValueError('typed_region_level_unresolved')
    location = {k:region.get(k) for k in ('province', 'city', 'district')}
    if level == 'national':
        location.update(province=None, city=None, district=None)
    if level == 'province':
        location.update(city=None, district=None)
    elif level == 'city':
        location['district'] = None
    required = ([] if level == 'national' else ['province']) + (['city'] if level in {'city', 'district'} else []) + (['district'] if level == 'district' else [])
    if any(not isinstance(location[k], str) or not location[k].strip() for k in required):
        raise ValueError('typed_region_parent_unresolved')
    exclusions = region.get('exclusions')
    if exclusions is None:
        exclusions = [{'field':x['field'][8:], 'value':x['value']} for x in region.get('constraint_trace', []) if x.get('field','').startswith('exclude_')]
    if not isinstance(exclusions,list) or any(set(x)!={'field','value'} or x['field'] not in location or not isinstance(x['value'],str) for x in exclusions):
        raise ValueError('typed_region_exclusion_invalid')
    exclusions = sorted(exclusions,key=lambda x:(x['field'],x['value']))
    payload = dict(level=level, exclusions=exclusions, **location)
    if region.get('city_group'):
        if level != 'city':
            raise ValueError('typed_city_group_level_mismatch')
        payload['city_group'] = True
    return dict(version=VERSION, stable_id='geo-path-sha256:'+_digest(payload), **payload)


def region_sql(identity):
    """Generate exact SQL from validated identity; no caller-supplied SQL accepted."""
    expected = region_identity(dict(identity, region_type=identity.get('level')))
    if identity != expected:
        raise ValueError('typed_region_identity_mismatch')
    if identity['level'] == 'national':
        from packages.semantic_query.regional_capabilities import PROVINCES
        return dict(where_sql='province IN ('+','.join('?' for _ in PROVINCES)+')', where_params=PROVINCES,
                    name='全国', full_name='全国', region_type='national', province=None, city=None, district=None)
    fields = ['province'] + (['city'] if identity['level'] in {'city', 'district'} else []) + (['district'] if identity['level'] == 'district' else [])
    name = identity[fields[-1]]
    clauses = []; params = []
    for k in fields:
        if k == 'city' and identity.get('city_group'):
            from packages.core.geography_registry import city_group_spellings
            spellings = city_group_spellings(identity['province'], identity['city'])
            clauses.append('TRIM(city) IN ('+','.join('?' for _ in spellings)+')')
            params.extend(spellings)
        else:
            clauses.append(k+'=?'); params.append(identity[k])
    for exclusion in identity['exclusions']:
        field=exclusion['field'];clauses.append('('+field+' IS NULL OR '+field+'<>?)');params.append(exclusion['value'])
    return dict(where_sql=' AND '.join(clauses),
                where_params=tuple(params), name=name,
                # A display path may repeat the same name at different levels
                # (municipalities). Keep both levels in identity and SQL, while
                # using the resolver's canonical, non-repeated display name.
                full_name=' '.join(dict.fromkeys(identity[k] for k in fields)),
                region_type={'province':'省份','city':'地级市','district':'区县','corps':'单列兵团'}[identity['level']],
                **{k:identity[k] for k in ('province','city','district')})


def bind_region(query, connection, context=None):
    """Use actual public geography/context; unresolved is explicit uncertainty."""
    from scripts.region_engine import detect_region
    from packages.core.research_data import enabled, geographic_request, REQUEST, SelectionError
    if query['entity'] == '全国':
        return region_identity(dict(region_type='national'))
    if enabled():
        try:
            region = geographic_request(query['entity'], REQUEST.get() if context is None else context, active_year=query.get('year'))
        except SelectionError as exc:
            raise ValueError('typed_region_uncertain:'+str(exc)) from exc
    else:
        region = detect_region(query['entity'], connection=connection)
        if region is None and context:
            # Explicit full names in the actual question can resolve a bare alias.
            aliases = []
            for field, suffix in [('province','省'), ('city','市')]:
                names = {r[0] for r in connection.execute('SELECT DISTINCT '+field+' FROM schools') if r[0]}
                aliases.extend(name for name in names if name.endswith(suffix) and name[:-1] == query['entity'] and name in context
                               and not re.search(r'(?:不是|不指|不要|不含|排除)\s*'+re.escape(name),context))
            if len(set(aliases)) == 1:
                region = detect_region(aliases[0], connection=connection)
    if not region:
        raise ValueError('typed_region_uncertain')
    identity = region_identity(region)
    # A city alias embedded in a full province literal cannot select that city.
    explicit_province = query['entity'].endswith(('省','自治区','特别行政区'))
    if context and query['entity'] not in {identity['province'],identity['city'],identity['district']}:
        levels=[]
        for level in ('province','city'):
            name=identity[level]
            if name and name.startswith(query['entity']) and name in context and not re.search(r'(?:不是|不指|不要|不含|排除)\s*'+re.escape(name),context):
                levels.append(level)
        if len(levels)>1:
            raise ValueError('typed_region_context_uncertain')
        if levels and levels[0]!=identity['level']:
            raise ValueError('typed_region_context_level_mismatch')
    if explicit_province and identity['level'] != 'province':
        raise ValueError('typed_region_level_mismatch')
    if query.get('region_identity') is not None and query['region_identity'] != identity:
        raise ValueError('typed_region_binding_mismatch')
    return identity


def bind_regions(plan, connection, context=None):
    result = copy.deepcopy(plan)
    for query in result['queries']:
        if query['kind'].startswith('region_'):
            query['region_identity'] = bind_region(query, connection, context)
    return result


def check_result_type(node, typ):
    if node.get('unit') != typ['unit']:
        raise ValueError('typed_execution_unit_mismatch')
    node['semantic_type'] = copy.deepcopy(typ)
    return node


def no_data_result(raw):
    """Only documented empty populations/years are valid no-data results."""
    if 'error' in raw and raw.get('status') not in NO_DATA_STATUSES:
        raise ValueError('typed_execution_error:'+str(raw.get('status', 'query')))
    return raw.get('status') in NO_DATA_STATUSES
