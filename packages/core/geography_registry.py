"""One registration-derived geography resolver for every research query route."""
from __future__ import annotations

from functools import lru_cache
import re

from packages.core.research_data import SelectionError, registry, config


class GeographyAmbiguity(SelectionError):
    def __init__(self, token, candidates):
        self.token = token
        self.candidates = candidates
        labels = [c['full_name']+('（省级）' if c['region_type'] == '省份' else '（城市）' if c['region_type'] == '城市' else '') for c in candidates]
        super().__init__('“'+token+'”对应多个地区，请明确所属省、市或行政层级：'+'；'.join(labels)+'。')


def canonical_city(value, province, reg=None):
    """City-field identity from the existing dictionary, within its unique parent."""
    reg=registry() if reg is None else reg
    value=str(value or '').strip(' ')
    city=reg['city_aliases'].get(value,value)
    if reg['parent_by_city'].get(city)==province:
        return city
    choices={c['city'] for c in reg['name_candidates'].get(value,[])
             if c['level']=='city' and reg['parent_by_city'].get(c['city'])==province}
    return next(iter(choices)) if len(choices)==1 else None


def city_group_spellings(province, city):
    """The sole alias-membership definition for grouped-city SQL and replay."""
    reg=registry()
    if reg['parent_by_city'].get(city)!=province:
        raise SelectionError('CITY_GROUP_PARENT_NOT_REGISTERED')
    names=set(reg['name_candidates']) | set(reg['city_aliases']) | {city}
    return tuple(sorted(n for n in names if canonical_city(n,province,reg)==city))


def city_groups(connection, province, year):
    """Registration counts, not answer counts; never changes single-city filters."""
    groups={};excluded=0;total=0
    rows=connection.execute('SELECT city,COUNT(*) AS n FROM schools WHERE province=? AND year=? GROUP BY city',
                            (province,str(year))).fetchall()
    for row in rows:
        total+=row['n']
        city=canonical_city(row['city'],province)
        if city is None:
            excluded+=row['n']
        else:
            groups[city]=groups.get(city,0)+row['n']
    return dict(province=province,year=str(year),cities=[dict(city=c,registered_records=n) for c,n in sorted(groups.items())],
                canonical_city_count=len(groups),excluded_records=excluded,total_registered_records=total)


def is_full():
    return registry()['version'].startswith('geo-select-v5-full-')


CORPS='新疆生产建设兵团'


def scope(identity, exclusions=()):
    level = identity['level']
    fields = ['province'] + (['city'] if level in {'city','district'} else []) + (['district'] if level == 'district' else [])
    clauses = [key+'=?' for key in fields]
    params = [identity[key] for key in fields]
    trace = [dict(field=k, value=identity[k], operation='EXACT_AND', source='FROZEN_FULL_GEOGRAPHY_REGISTRY') for k in fields]
    for field, value in sorted(set(exclusions)):
        clauses.append('('+field+' IS NULL OR '+field+'<>?)')
        params.append(value)
        trace.append(dict(field='exclude_'+field, value=value, operation='EXPLICIT_NOT_EXACT'))
    values = {key: identity.get(key) if key in fields else None for key in ('province','city','district')}
    return dict(where_sql=' AND '.join(clauses), where_params=tuple(params), name=identity[fields[-1]],
                full_name=' '.join(dict.fromkeys(identity[k] for k in fields)),
                region_type={'province':'省份','city':'城市','district':'区县','corps':'单列兵团'}[level],
                constraint_trace=trace, registry_version=registry()['version'], research_estimand=config()['estimand'], **values)


def _allowed(candidate, year, reg):
    if candidate['level'] != 'district':
        return True
    # District-field strings are not proof of a valid parent city. Require the
    # parent itself in the same registry, including its registered alias paths.
    if not any(c['level']=='city' and c['province']==candidate['province'] and c['city']==candidate['city']
               for c in reg['name_candidates'].get(candidate['city'], [])):
        return False
    return any(p['province'] == candidate['province'] and p['city'] == candidate['city'] and year in p['years']
               for p in reg['district_parents'][candidate['district']])


@lru_cache(maxsize=4)
def _pattern(names):
    return re.compile('|'.join(re.escape(x) for x in sorted(names, key=lambda x:(-len(x),x))))


def mentions(text, *, exact_alias=False):
    reg = registry()
    names = set(reg['province_aliases']) | set(reg['parent_by_city']) | set(reg['city_parent_ties']) | set(reg['district_parents'])
    # Ambiguous city short names remain tokens, even though they are not safe aliases.
    names.update(c[:-1] for c in set(reg['parent_by_city']) | set(reg['city_parent_ties']) if c.endswith('市'))
    clean = str(text).strip(' ，,。？！!?')
    if exact_alias and clean in reg['name_candidates']:
        return [dict(start=0, end=len(clean), token=clean, candidates=reg['name_candidates'][clean], negative=False)]
    result = []
    for match in _pattern(tuple(sorted(names))).finditer(text):
        token = match.group()
        candidates = reg['name_candidates'].get(token, [])
        # A complete province spelling is an explicit administrative level.
        if token in reg['province_aliases'] and token == reg['province_aliases'][token] and not token.endswith('市'):
            candidates = [c for c in candidates if c['level'] == 'province']
        negative = bool(re.search(r'(?:排除|不含|不包括|剔除|不要纳入|除去|不要|不是|不看)\s*$', text[:match.start()]))
        result.append(dict(start=match.start(), end=match.end(), token=token, candidates=candidates, negative=negative))
    return result


def groups(text):
    found = [m for m in mentions(text) if not m['negative']]
    # Resolve original mentions once, using explicit level and parent context.
    provinces=[m for m in found if m['candidates'] and all(c['level']=='province' for c in m['candidates'])]
    parents=[m for m in provinces if re.search(r'在|位于|属于|隶属',text[max(0,m['start']-3):m['start']]) or
        re.match(r'(?:里|内|中|的.{0,6}(?:地级市|城市))',text[m['end']:])]
    province_context=bool(re.search(r'[两二三四五六七八九十\d]+省|省级|省份',text)) or bool(
        [m for m in provinces if m not in parents] and len(found)>1 and re.search(r'和|与|、|比较|对比|差距',text))
    city_context=bool(re.search(r'各市|全市|城市|座城市|地级市|[一二两三四五六七八九十几\d]+(?:个)?市',text)) or (len(found)>1 and bool(re.search(r'和|与|、|比较|对比',text)) and
        any(len({c['city'] for c in m['candidates'] if c['level']=='city'})==1 and m['token'].endswith('市') for m in found))
    district_context=bool(re.search(r'区县|县级|区级|各县|各区|[一二两三四五六七八九十几\d]+(?:个)?县',text))
    # Infer a level only inside the same parallel list. Freeze the evidence
    # before narrowing: two ambiguous objects cannot disambiguate each other.
    parallel=[]
    for m in found:
        if parallel and re.fullmatch(r'\s*(?:和|与|跟|及|以及|、|，|,|比较|对比|相比)\s*',text[parallel[-1][-1]['end']:m['start']]):
            parallel[-1].append(m)
        else:parallel.append([m])
    known_levels={id(m):next(iter(levels)) for m in found
                  if len(levels:={c['level'] for c in m['candidates']})==1 and 'province' not in levels}
    peer_levels={id(m):next(iter(levels)) for batch in parallel if len(batch)>1 for m in batch
                 if all(id(peer) in known_levels for peer in batch if peer is not m)
                 and len(levels:={known_levels[id(peer)] for peer in batch if peer is not m})==1}
    parent_provinces={c['province'] for m in parents for c in m['candidates']}
    for m in found:
        if parent_provinces and m not in parents and not m['token'].endswith(('省','自治区')):
            choices=[c for c in m['candidates'] if c['province'] in parent_provinces]
            if choices:m['candidates']=choices
        provincial=[c for c in m['candidates'] if c['level']=='province']
        if province_context and provincial and not m['token'].endswith(('市','区','县','旗')):
            m['candidates']=provincial
        cities={c['city'] for c in m['candidates'] if c['level']=='city'}
        peer_level=peer_levels.get(id(m))
        if peer_level and not m['token'].endswith(('省','市','区','县','旗','自治区')):
            peers=[c for c in m['candidates'] if c['level']==peer_level]
            if peers:m['candidates']=peers
        if district_context and not m['token'].endswith(('省','市','区','县','旗','自治区')):
            districts=[c for c in m['candidates'] if c['level']=='district']
            if districts:m['candidates']=districts
        if city_context and len(cities)==1 and not m['token'].endswith(('区','县','旗')):
            cities_only=[c for c in m['candidates'] if c['level']=='city']
            if cities_only:m['candidates']=cities_only
    batches = []
    levels = {'province':1, 'city':2, 'district':3}
    for match in found:
        if batches:
            previous = batches[-1][-1]
            gap = text[previous['end']:match['start']]
            prior_level = max(levels[c['level']] for c in previous['candidates'])
            next_level = min(levels[c['level']] for c in match['candidates'])
            nested_alias = any(c['level'] == 'district' and _compatible(c, p)
                               for c in match['candidates'] for p in previous['candidates'] if p['level'] in {'province','city'})
            if (next_level > prior_level or nested_alias) and not re.search(r'和|与|以及|对比|比较|、|，|,|；|;|。|减', gap):
                batches[-1].append(match)
                continue
        batches.append([match])
    return batches


def _compatible(path, ancestor):
    levels = {'province':1, 'city':2, 'district':3}
    return levels[path['level']] >= levels[ancestor['level']] and all(
        ancestor.get(k) is None or path.get(k) == ancestor[k] for k in ('province','city','district'))


def _solve(tokens, year, *, prefer_province=False):
    reg = registry()
    choices = [[c for c in match['candidates'] if _allowed(c, year, reg)] for match in tokens]
    paths = {tuple(c.get(k) for k in ('level','province','city','district')): c for cs in choices for c in cs}
    result = [c for c in paths.values() if all(any(_compatible(c, other) for other in cs) for cs in choices)]
    # A bare city and its own same-name county denote the prefectural city by
    # default. Explicit county/peer-level context has already narrowed choices.
    # Compare full identities, so cross-province names still require clarification.
    if len(tokens)==1 and not tokens[0]['token'].endswith(('省','市','区','县','旗','自治区')) and len(result)==2:
        city=next((c for c in result if c['level']=='city'),None)
        district=next((c for c in result if c['level']=='district'),None)
        bare=tokens[0]['token']
        if city and district and city['city']==bare+'市' and district['district']==bare+'县' and _compatible(district,city):
            result=[city]
    if len(tokens) == 1 and tokens[0]['token'] in reg['city_aliases']:
        result = [c for c in result if c['level'] == 'city']
    if len(tokens) == 1 and not prefer_province and tokens[0]['token'] in set(reg['parent_by_city']) | set(reg['city_parent_ties']):
        result = [c for c in result if c['level'] == 'city']
    # Municipal province/city spelling represents the same named administrative
    # unit. Keep the old six-city query scope unless the typed task is provincial.
    if len(result) == 2:
        province = next((c for c in result if c['level'] == 'province'), None)
        city = next((c for c in result if c['level'] == 'city'), None)
        if province and city and province['province'] == city['province'] == city['city']:
            result = [province if prefer_province else city]
    if prefer_province:
        provincial = [c for c in result if c['level'] == 'province']
        if provincial:
            result = provincial
    if not result:
        raise SelectionError('DISTRICT_YEAR_NOT_REGISTERED' if any(c['level']=='district' for cs in choices for c in cs) else 'CONTRADICTORY_OR_UNREGISTERED_REGION')
    if len(result) > 1:
        candidates = [scope(c) for c in result]
        token = ''.join(t['token'] for t in tokens)
        from packages.semantic_query.selective_clarification import observe_regions
        selected = observe_regions(token, candidates)
        if selected:
            return selected
        raise GeographyAmbiguity(token, candidates)
    return scope(result[0])


def resolve(keyword, question='', active_year=None, *, prefer_province=False):
    reg = registry()
    years = re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', question)
    if any(y not in reg['application_years'] for y in years) or active_year is not None and str(active_year) not in reg['application_years']:
        raise SelectionError('UNSUPPORTED_OR_MISSING_YEAR')
    year = str(active_year) if active_year is not None else years[0] if len(set(years)) == 1 else reg['application_years'][-1]
    from packages.core.research_data import _public_text
    if re.fullmatch(r'(?:新疆生产建设)?兵团',str(keyword).strip()):
        return scope(dict(level='corps',province=CORPS,city=None,district=None))
    tokens = [m for m in mentions(_public_text(str(keyword)), exact_alias=True) if not m['negative']]
    if not tokens:
        raise SelectionError('UNSUPPORTED_OR_AMBIGUOUS_CITY')
    original_tokens=[m for batch in groups(_public_text(question)) for m in batch] if question else []
    for token in tokens:
        originals=[m for m in original_tokens if m['token']==token['token']]
        if len(originals)==1:
            token['candidates']=originals[0]['candidates']
    # The model cannot settle an ambiguous original spelling by adding a level.
    # For a single original path, retain its explicit parent/district as well.
    if question:
        original_groups = groups(question)
        if len(original_groups) == 1:
            original = original_groups[0]
            related = any(a.get('province') == b.get('province') and
                          (a.get('city') is None or b.get('city') is None or a.get('city') == b.get('city'))
                          for t in tokens for a in t['candidates'] for t2 in original for b in t2['candidates'])
            if related:
                tokens = original
    resolved = _solve(tokens, year, prefer_province=prefer_province)
    exclusions = []
    for match in mentions(question or str(keyword)):
        if match['negative']:
            excluded = _solve([match], year)
            field = 'district' if excluded['district'] else 'city' if excluded['city'] else 'province'
            exclusions.append((field, excluded[field]))
    if exclusions:
        identity = dict(level='district' if resolved['district'] else 'city' if resolved['city'] else 'province',
                        **{k:resolved[k] for k in ('province','city','district')})
        resolved = scope(identity, exclusions)
    return resolved


def ambiguity_boundary(question, active_year=None):
    """Specific questions from the same dictionary, before a model can guess."""
    from packages.core.research_data import _public_text
    text = re.sub(r'(?:新疆生产建设)?兵团',lambda m:' '*len(m[0]),_public_text(question))
    year_tokens = re.findall(r'(?<!\d)(20\d{2})(?!\d)', text)
    year = str(active_year) if active_year is not None else year_tokens[0] if len(set(year_tokens)) == 1 else registry()['application_years'][-1]
    for batch in groups(text):
        try:
            _solve(batch, year)
        except GeographyAmbiguity as error:
            return dict(intent='clarify', reason='ambiguous_region', original=question,
                        missing=[str(error)], region_candidates=error.candidates)
        except SelectionError:
            # A known conflicting parent/path is explicit, not a broader scope.
            return dict(intent='clarify', reason='contradictory_region', original=question,
                        missing=['地区的上级省、市与登记字典不一致，请核对：'+''.join(t['token'] for t in batch)+'。'])
    return None


def region_literals(question, active_year=None):
    from packages.core.research_data import _public_text
    text = re.sub(r'(?:新疆生产建设)?兵团',lambda m:' '*len(m[0]),_public_text(question))
    year_tokens = re.findall(r'(?<!\d)(20\d{2})(?!\d)', text)
    year = str(active_year) if active_year is not None else year_tokens[0] if year_tokens else registry()['application_years'][-1]
    result = []
    for batch in groups(text):
        found = _solve(batch, year)
        result.append(found['full_name'])
    if re.search(r'(?:新疆生产建设)?兵团',question) and not re.search(r'(?:不含|不包括|排除)\s*(?:新疆生产建设)?兵团',question):
        result.insert(0,CORPS)
    return list(dict.fromkeys(result))
