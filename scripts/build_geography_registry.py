"""Build a deterministic geography registry from the unchanged read-only v5 view.

Majority registration is a query membership policy, not a correction of school
metadata or an independently verified administrative geography.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.semantic_query.regional_capabilities import PROVINCES

VERSION = 'geo-select-v5-full-v1-fix1-20261019'
CITY_MIN_EVIDENCE = dict(years=2, school_year_records=10)
CORPS = '新疆生产建设兵团'
CITY_CATEGORY_LABELS = {'直辖市', '地级市', '县级市', '自治州', '地市', '城市', '省辖市'}
DISTRICT_CATEGORY_LABELS = {'地区', '区域', '区县', '县区', '市区', '全区', '本区', '该区', '辖区', '镇区', '市辖区', '县辖区', '城区', '郊区'}
QUERY = 'SELECT province,city,district,year,COUNT(*) AS n FROM schools GROUP BY province,city,district,year'


def city_name(value):
    return bool(value and value not in CITY_CATEGORY_LABELS and re.fullmatch(r'[\u4e00-\u9fff]{2,10}(?:市|州|盟|地区)', value)
                and not re.search(r'省|自治区|特别行政区|学校|小学|中学|幼儿园|街道', value))


def district_name(value, province, city):
    return bool(value and re.fullmatch(r'[\u4e00-\u9fff]{1,12}(?:区|县|旗|市)', value)
                and not re.search(r'省|自治区|学校|小学|中学|幼儿园|街道', value)
                and value not in set(PROVINCES) | CITY_CATEGORY_LABELS | DISTRICT_CATEGORY_LABELS | {CORPS, province, city})


def province_aliases():
    result = {}
    for name in (*PROVINCES, CORPS):
        short = re.sub(r'(?:维吾尔自治区|壮族自治区|回族自治区|自治区|省|市)$', '', name)
        for alias in {name, short} | ({'兵团', '新疆兵团', '生产建设兵团'} if name == CORPS else set()):
            result[alias] = name
    return dict(sorted(result.items()))


def build(rows, source_identity, years, legacy_cities):
    counts = defaultdict(Counter)
    city_years = defaultdict(set)
    for row in rows:
        counts[row['city'] or ''][row['province'] or ''] += row['n']
        city_years[row['city'] or ''].add(str(row['year']))
    evidence = {c: dict(years=sorted(city_years[c]), school_year_records=sum(dist.values()))
                for c, dist in counts.items() if city_name(c)}
    insufficient = {c: e for c, e in evidence.items()
                    if len(e['years']) < CITY_MIN_EVIDENCE['years'] and e['school_year_records'] < CITY_MIN_EVIDENCE['school_year_records']}
    parents, ties, winners = {}, {}, {}
    for name, distribution in sorted(counts.items()):
        if not city_name(name) or name in insufficient:
            continue
        high = max(distribution.values())
        winning = sorted(p for p, n in distribution.items() if n == high)
        winners[name] = winning
        if len(winning) == 1:
            parents[name] = winning[0]
        else:
            ties[name] = winning

    districts = defaultdict(lambda: defaultdict(set))
    district_parents = defaultdict(lambda: defaultdict(set))
    anomalies = Counter()
    for row in rows:
        p, c, d, y, n = (row[k] for k in ('province', 'city', 'district', 'year', 'n'))
        if c not in winners:
            continue
        if p not in winners[c]:
            anomalies[(c, '|'.join(winners[c]), p or '', y)] += n
            continue
        if district_name(d, p, c):
            districts[y][c].add(d)
            district_parents[d][(p, c)].add(y)
    district_index = {d: [dict(province=p, city=c, years=sorted(ys)) for (p, c), ys in sorted(paths.items())]
                      for d, paths in sorted(district_parents.items())}
    prov_aliases = province_aliases()
    candidates = defaultdict(list)
    for alias, p in prov_aliases.items():
        candidates[alias].append(dict(level='province', province=p, city=None, district=None))
    for c, winning in winners.items():
        for p in winning:
            value = dict(level='city', province=p, city=c, district=None)
            candidates[c].append(value)
            if c.endswith('市'):
                candidates[c[:-1]].append(value)
    for d, paths in district_index.items():
        for path in paths:
            candidates[d].append(dict(level='district', province=path['province'], city=path['city'], district=d))
            if d.endswith(('区', '县', '旗')) and len(d) > 2:
                candidates[d[:-1]].append(dict(level='district', province=path['province'], city=path['city'], district=d))
    candidates = {a: sorted({json.dumps(x, ensure_ascii=False, sort_keys=True) for x in cs}) for a, cs in candidates.items()}
    candidates = {a: [json.loads(x) for x in cs] for a, cs in sorted(candidates.items())}
    # Canonical names remain available even when the spelling is ambiguous.
    aliases = {}
    for c in winners:
        if c.endswith('市'):
            short = c[:-1]
            named_conflicts = [x for x in candidates[short] if x['level'] != 'district' or x['district'] == short]
            if len(candidates[short]) == 1 or c in legacy_cities and len(named_conflicts) == 1:
                aliases[short] = c
    conflicts = {a: cs for a, cs in candidates.items() if len(cs) > 1 and a not in aliases}
    registry = dict(version=VERSION, application_years=list(years),
        city_evidence=evidence, insufficient_city_evidence=insufficient,
        provinces=[dict(name=p, aliases=[a for a, v in prov_aliases.items() if v == p], national_31=p in PROVINCES,
                        separate_corps=p == CORPS) for p in (*PROVINCES, CORPS)],
        parent_by_city=dict(sorted(parents.items())), city_parent_ties=dict(sorted(ties.items())),
        city_aliases=dict(sorted(aliases.items())), province_aliases=prov_aliases,
        districts={y: {c: sorted(ds) for c, ds in sorted(cs.items())} for y, cs in sorted(districts.items())},
        district_parents=district_index, name_candidates=candidates, ambiguous_names=conflicts,
        province_city_homonyms={a: cs for a, cs in conflicts.items() if {'province','city'} <= {x['level'] for x in cs}},
        source=dict(view='schools', fields=['province','city','district','year'], sql=QUERY, identity=source_identity),
        policy=dict(city_min_evidence=CITY_MIN_EVIDENCE,
                    city_evidence_rule='at least two available years OR ten school-year records; otherwise registration anomaly, excluded from resolution candidates',
                    parent='unique maximum number of school-year records across all available years',
                    tied_parent='keep alternatives; explicit parent required; no alphabetical tie-break',
                    minority='exclude from the city query using exact province AND city; never update records',
                    city_name_pattern='2-10 Chinese characters followed by 市/州/盟/地区; no province/address/school markers',
                    city_category_labels=sorted(CITY_CATEGORY_LABELS),
                    district_category_labels=sorted(DISTRICT_CATEGORY_LABELS),
                    non_city_values='all preserved in GEOGRAPHY_CITY_VALUES_FULL_v1.csv; not used as place tokens',
                    legacy_municipal_names='default city scope for municipality name shared with its province; typed provincial tasks may select the provincial scope',
                    legacy_city_short_names=sorted(legacy_cities),
                    city_short_name_conflicts='province, other city, and county names/aliases require clarification; retain the original six-city short-name interpretation',
                    evidence='registration-derived membership policy, not verified administrative identity'))
    anomaly_rows = [dict(city=c, dominant_province=p, registered_province=r, year=y, record_count=n)
                    for (c,p,r,y), n in sorted(anomalies.items())]
    values = [dict(value=c, kind='insufficient_city_evidence' if c in insufficient else 'city_name' if city_name(c) else 'non_city_registration',
                   registered_province=p, record_count=n,
                   dominant_province='|'.join(winners.get(c, [])))
              for c, dist in sorted(counts.items()) for p, n in sorted(dist.items())]
    stats = dict(school_year_records=sum(r['n'] for r in rows), province_units=32, national_province_units=31,
                 city_field_distinct_values=len(counts), city_names=len(winners), cities_with_unique_parent=len(parents),
                  cities_with_tied_parent=len(ties), non_city_field_values=sum(not city_name(c) for c in counts),
                  insufficient_city_names=len(insufficient), insufficient_city_records=sum(e['school_year_records'] for e in insufficient.values()),
                 district_names=len(district_index), district_paths=sum(len(ps) for ps in district_index.values()),
                 city_short_aliases=len(aliases), province_aliases=len(prov_aliases),
                 ambiguous_aliases=len(conflicts), homonymous_district_names=sum(len(ps)>1 for ps in district_index.values()),
                 province_city_homonyms=len(registry['province_city_homonyms']),
                 anomalous_cities=len({r['city'] for r in anomaly_rows}), anomalous_records=sum(r['record_count'] for r in anomaly_rows))
    registry['statistics'] = stats
    return registry, anomaly_rows, values


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    from scripts.school_engine import connect
    from packages.core.research_data import config, identity
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(QUERY)]
    source_identity = identity()
    source_identity.pop('registry_version', None)
    legacy = json.loads((ROOT/'research_rules/GEOGRAPHY_REGISTRY.json').read_text('utf-8'))
    full, anomalies, values = build(rows, source_identity, config()['application_years'], set(legacy['parent_by_city']))
    target = ROOT/'research_rules'
    (target/'GEOGRAPHY_REGISTRY_FULL_v1.json').write_text(json.dumps(full, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    write_csv(target/'GEOGRAPHY_REGISTRATION_ANOMALIES_v1.csv', anomalies,
              ['city','dominant_province','registered_province','year','record_count'])
    write_csv(target/'GEOGRAPHY_CITY_VALUES_FULL_v1.csv', values,
              ['value','kind','registered_province','record_count','dominant_province'])
    write_csv(target/'GEOGRAPHY_PARENT_TIES_v1.csv',
              [dict(city=c, candidate_province=p) for c, ps in full['city_parent_ties'].items() for p in ps],
              ['city','candidate_province'])
    write_csv(target/'GEOGRAPHY_INSUFFICIENT_CITY_EVIDENCE_v1.csv',
              [dict(city=c, registered_province=r['registered_province'], record_count=r['record_count'],
                    years='|'.join(full['insufficient_city_evidence'][c]['years']))
               for r in values if (c := r['value']) in full['insufficient_city_evidence']],
              ['city','registered_province','record_count','years'])
    print(json.dumps(full['statistics'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
