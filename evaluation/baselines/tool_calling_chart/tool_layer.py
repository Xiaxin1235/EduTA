"""Thin wrappers around fixed candidate calculators. No task compilation."""
import json
from contextlib import closing

from paths import ROOT, enable_candidate

enable_candidate()
from scripts import school_engine as school, region_engine as region, fact_engine as fact
from packages.analysis_contract import capabilities
from packages.core import geography_registry as geography
from packages.semantic_query import catalogue
from packages.semantic_query.public_tasks import unique_school
from packages.semantic_query.runtime import _DIMENSION_ALIASES, _explicit_dimension_aliases, _catalogue_alias_items
from packages.agent.indicator_catalog import match_indicator
from packages.semantic_query.typed_plan import region_identity


def prompt_text():
    catalog = catalogue.load_semantic_catalogue()
    directory = dict(version=catalog['version'], items=catalog['items'],
                     topics=catalog['topics'], dimensions=catalogue.dimension_meanings(),
                     dimension_aliases=_DIMENSION_ALIASES)
    filters = ('学段与城乡规则采用候选当前工具。2020—2022按登记年级范围，2023按类别。'
               '城区/城市仅城区，镇区仅镇区，城镇含城区和镇区，乡村/农村仅乡村。'
               '不同属性取交集。公办/民办没有可靠结构化字段。')
    return (ROOT.joinpath('SYSTEM_PROMPT.txt').read_text(encoding='utf-8')
            + '\n指标目录\n' + json.dumps(directory, ensure_ascii=False)
            + '\n报告规则\n' + ROOT.joinpath('REPORT_RULES.md').read_text(encoding='utf-8')
            + '\n' + filters)


def definitions():
    text = {'type': 'string'}
    national_description = '全国指31个省级行政区，按学校等权统计，不含新疆生产建设兵团、香港、澳门和台湾。'
    region_keyword = dict(text, description=national_description)
    year = {'type': 'string', 'enum': ['2020', '2021', '2022', '2023']}
    metric = {
        'target_dimension': dict(text, description='一级维度名称或目录别名'),
        'topic': dict(text, description='一个二级维度名称或目录别名，仅标准分、均值、学校等权'),
        'indicator_ids': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1, 'maximum': 83},
                          'minItems': 1, 'maxItems': 83},
        'value_view': {'type': 'string', 'enum': ['raw_value', 'normalized_score'], 'default': 'normalized_score'},
        'school_filter': dict(text, description='候选支持的学段、城乡字符串，可组合，如乡村小学'),
    }
    evaluation = dict(metric,
        target_stat={'type': 'string', 'enum': sorted(x for x in capabilities.SUPPORTED_STATISTICS if x)},
        aggregation={'type': 'string', 'enum': sorted(capabilities.SUPPORTED_AGGREGATIONS), 'default': 'school_equal_mean'})
    specs = [
        ('evaluate_region', '地区整体、一级、二级维度或题项统计，均值、中位数、极值、计数。',
         dict(evaluation, region_keyword=region_keyword, year=year), ['region_keyword', 'year']),
        ('compare_regions', '两个及以上地区同年同口径比较。返回每个地区的原始计算结果。',
         dict(evaluation, regions={'type': 'array', 'items': text, 'minItems': 2, 'maxItems': 31,
                                   'description': national_description}, year=year), ['regions', 'year']),
        ('region_series', '同一地区2至4年的独立年度截面，逐年返回计算结果。',
         dict(evaluation, region_keyword=region_keyword, years={'type': 'array', 'items': year, 'minItems': 2, 'maxItems': 4, 'uniqueItems': True}), ['region_keyword', 'years']),
        ('region_change', '地区两年变化，整体/一级维度使用原纵向工具。二级/题项按两期工具值相减。',
         dict(evaluation, region_keyword=region_keyword, year_a=year, year_b=year), ['region_keyword', 'year_a', 'year_b']),
        ('geographic_ranking', '全国31省级行政区排名或一个省内的城市排名，学校等权。兵团单列。',
         dict(metric, year=year, scope={'type': 'string', 'enum': ['national_31', 'province_cities']},
              province=text, top_n={'type': 'integer', 'minimum': 1},
              order={'type': 'string', 'enum': ['top', 'bottom'], 'default': 'top'}), ['scope', 'year']),
        ('school_ranking', '地区内学校排名，按原始每校均分降序、年度ID升序，支持第N、倒数第一或Top N。',
         dict(metric, region_keyword=text, year=year,
              target_rank={'type': 'integer', 'description': '正整数名次，-1为倒数第一'},
              target_top_n={'type': 'integer', 'minimum': 1}), ['region_keyword', 'year']),
        ('evaluate_school', '单校模板整体综合分、六维、一级或二级维度得分。',
         dict(school_keyword=text, year=year, target_dimension=metric['target_dimension'], topic=metric['topic']), ['school_keyword', 'year']),
        ('school_item', '单校题项、登记字段或已有复合指标。metric_or_text用Q题号、目录别名或指标文字，默认原值。',
         dict(school_keyword=text, year=year, metric_or_text=text,
              value_view={'type': 'string', 'enum': ['raw_value', 'normalized_score'], 'default': 'raw_value'}), ['school_keyword', 'year', 'metric_or_text']),
        ('compare_schools', '多校同年模板综合分、一级或二级维度比较。',
         dict(schools={'type': 'array', 'items': text, 'minItems': 2, 'maxItems': 10}, year=year,
              target_dimension=metric['target_dimension'], topic=metric['topic']), ['schools', 'year']),
        ('draw_chart', '把已由其他工具返回的数值画成柱状图，使用与系统页面相同的图表组件。rows每项一根柱子：label、value、unit；同一张图单位必须相同。返回生成的PNG文件。',
         dict(title=dict(text, description='图表标题'),
              rows={'type': 'array', 'minItems': 1, 'maxItems': 31, 'items': {'type': 'object', 'additionalProperties': False,
                    'properties': {'label': text, 'value': {'type': 'number'}, 'unit': text}, 'required': ['label', 'value', 'unit']}}),
         ['title', 'rows']),
    ]
    return [{'type': 'function', 'function': dict(name=name, description=description,
            parameters=dict(type='object', properties=properties, required=required, additionalProperties=False))}
            for name, description, properties, required in specs]


class ToolExecutor:
    def __init__(self):
        self.schemas = definitions()
        self.by_name = {s['function']['name']: s['function']['parameters'] for s in self.schemas}

    def call(self, name, arguments):
        try:
            if name not in self.by_name:
                raise ValueError('unknown_registered_function:' + name)
            validate_argument(arguments, self.by_name[name])
            data = getattr(self, name)(**arguments)
            return {'success': not bool(data.get('error')), 'data': data}
        except (ValueError, TypeError) as exc:
            return {'success': False, 'error': str(exc), 'error_type': type(exc).__name__,
                    'candidates': getattr(exc, 'candidates', [])}

    def _metric(self, values):
        values = dict(values)
        if sum(bool(values.get(key)) for key in ('target_dimension', 'topic', 'indicator_ids')) > 1:
            raise ValueError('choose_one_metric_granularity')
        dimension = values.get('target_dimension')
        if dimension:
            matches = _explicit_dimension_aliases(dimension)
            if len(matches) != 1:
                raise ValueError('dimension_unknown_or_ambiguous:' + dimension)
            values['target_dimension'] = matches[0][0]
        topic = values.get('topic')
        if topic:
            matches = [t['name'] for t in catalogue.topic_catalogue()
                       if topic == t['name'] or topic in t.get('aliases', [])]
            if len(matches) != 1:
                raise ValueError('topic_unknown_or_ambiguous:' + topic)
            values['topic'] = matches[0]
        return values

    def evaluate_region(self, region_keyword, year, **values):
        values = self._metric(values)
        if region_keyword == '全国':
            identity = region_identity(dict(region_type='national'))
            return region.evaluate_region(region_keyword, year=year, region_identity=identity, **values)
        return self._region_value(region_keyword, year, values)

    def _region_value(self, entity, year, values, *, prefer_province=False, city_group=False):
        # Only the fixed geographic-path helper is used. No typed task is built.
        resolved = geography.resolve(entity, active_year=year, prefer_province=prefer_province)
        identity = region_identity(dict(resolved, city_group=city_group))
        return region.evaluate_region(entity, year=year, region_identity=identity, **values)

    def compare_regions(self, regions, year, **values):
        values = self._metric(values)
        if (len(regions) == 2 and '全国' not in regions and not values.get('topic') and not values.get('indicator_ids')
                and values.get('value_view', 'normalized_score') == 'normalized_score'
                and values.get('target_stat') in (None, 'mean', 'count')):
            for entity in regions:
                geography.resolve(entity, active_year=year)
            values.pop('value_view', None)
            return region.compare_regions(regions[0], regions[1], year=year, **values)
        return {'year': year, 'regions': [{'region': entity, 'result': self.evaluate_region(entity, year, **values)}
                                        for entity in regions]}

    def region_series(self, region_keyword, years, **values):
        return {'region': region_keyword, 'annual_results': [
            {'year': year, 'result': self.evaluate_region(region_keyword, year, **values)} for year in years],
            'notice': '各年为独立年度截面，采集题项可能不同。'}

    def region_change(self, region_keyword, year_a, year_b, **values):
        if year_a >= year_b:
            raise ValueError('year_a_must_precede_year_b')
        values = self._metric(values)
        if region_keyword != '全国':
            geography.resolve(region_keyword, active_year=year_a)
        if not values.get('topic') and not values.get('indicator_ids'):
            if values.pop('value_view', 'normalized_score') != 'normalized_score':
                raise ValueError('overall_dimension_normalized_score_only')
            if region_keyword != '全国':
                return region.evaluate_region_longitudinal(region_keyword, year_a, year_b, **values)
        a = self.evaluate_region(region_keyword, year_a, **values)
        b = self.evaluate_region(region_keyword, year_b, **values)
        stat = values.get('target_stat', 'mean')
        if stat not in ('mean', 'count', 'median', 'max', 'min'):
            raise ValueError('change_requires_single_statistic')
        key = {'count': 'school_count', 'median': 'median_score', 'max': 'max_score', 'min': 'min_score'}.get(stat, 'total_score')
        difference = None if a.get('error') or b.get('error') else round(b[key] - a[key], 2)
        return {'year_a': year_a, 'year_b': year_b, 'a': a, 'b': b,
                'difference_b_minus_a': difference, 'difference_field': key,
                'notice': '各年为独立年度截面，差值按两位工具值相减。'}

    def geographic_ranking(self, scope, year, province=None, top_n=None, order='top', **values):
        values = self._metric(values)
        with closing(school.connect()) as connection:
            if scope == 'national_31':
                if province is not None:
                    raise ValueError('national_31_has_no_province_parameter')
                from packages.semantic_query.regional_capabilities import PROVINCES
                entities = list(PROVINCES)
                membership = {'scope': 'national_31', 'notice': '31个省级行政区，不含兵团和港澳台。'}
            else:
                if province is None:
                    raise ValueError('province_required')
                resolved = geography.resolve(province, active_year=year, prefer_province=True)
                if resolved['region_type'] != '省份':
                    raise ValueError('province_level_required')
                membership = geography.city_groups(connection, resolved['province'], year)
                entities = [resolved['province'] + ' ' + row['city'] for row in membership['cities']]
            rows = []
            for entity in entities:
                result = self._region_value(entity, year, values,
                                            prefer_province=scope == 'national_31',
                                            city_group=scope == 'province_cities')
                if result.get('error'):
                    rows.append({'region': entity, 'error': result['error'], 'score': None})
                else:
                    score = result.get('raw_target_dimension_score') if values.get('target_dimension') else result.get('raw_total_score')
                    if score is None:
                        raise ValueError('raw_ranking_score_missing')
                    count = result.get('requested_school_count') if values.get('target_dimension') else result['school_count']
                    rows.append({'region': entity, 'score': score, 'school_count': count})
            eligible = [row for row in rows if row['score'] is not None]
            eligible.sort(key=lambda row: ((-row['score'] if order == 'top' else row['score']), row['region']))
            ranked = []
            for row in eligible:
                higher = sum(other['score'] > row['score'] if order == 'top' else other['score'] < row['score']
                             for other in eligible)
                ranked.append(dict(row, rank=1 + higher, score=round(row['score'], 2), unrounded_score=row['score']))
            return {'membership': membership, 'year': year, 'order': order,
                    'ranking': ranked[:top_n], 'unavailable': [r for r in rows if r['score'] is None],
                    'tie_policy': '未舍入分数排序，同分并列，显示顺序按地区名称', 'eligible_count': len(ranked)}

    def school_ranking(self, region_keyword, year, target_rank=None, target_top_n=None, **values):
        if (target_rank is None) == (target_top_n is None):
            raise ValueError('choose_target_rank_or_target_top_n')
        if target_rank is not None and target_rank != -1 and target_rank < 1:
            raise ValueError('target_rank_positive_or_minus_one')
        return self._region_value(region_keyword, year, self._metric(dict(
            values, target_rank=target_rank, target_top_n=target_top_n, operation='region_ranking')))

    def evaluate_school(self, school_keyword, year, **values):
        values = self._metric(values)
        with closing(school.connect()) as connection:
            selected = unique_school(connection, school_keyword, year)
            return school.evaluate_school(selected['id'], connection=connection, **values)

    def school_item(self, school_keyword, year, metric_or_text, value_view='raw_value'):
        ids = catalogue.extract_explicit_question_ids(metric_or_text)
        if len(ids) > 1:
            raise ValueError('school_item_requires_one_item_per_call')
        aliases = [] if ids else _catalogue_alias_items(metric_or_text)
        if len(aliases) > 1:
            raise ValueError('metric_ambiguous_choose_question_id:' + str([qid for qid, _ in aliases]))
        if ids or aliases:
            metric = catalogue.question_metric(ids[0] if ids else aliases[0][0])
        else:
            metric = match_indicator(metric_or_text)
            # The legacy matcher has a fuzzy fallback and a default tablet owner.
            # Leaf selection uses the shared literal resolver to retain ambiguity.
            if metric and metric['type'] == 'question':
                raise ValueError('metric_unknown_or_ambiguous_choose_catalogue_question_id')
        if not metric:
            raise ValueError('metric_unknown_or_ambiguous')
        metric = dict(metric, value_view=value_view)
        with closing(school.connect()) as connection:
            selected = unique_school(connection, school_keyword, year)
            return fact.query_school_fact(selected['id'], metric, year=year, connection=connection)

    def draw_chart(self, title, rows):
        # EduTA's own chart path: bar_code() builds the Matplotlib script, PythonInterpreterTool renders it to PNG.
        from packages.semantic_query.public_tasks import bar_code
        from packages.agent.tools.python_runner import PythonInterpreterTool
        code = bar_code([dict(label=r['label'], value=r['value'], unit=r['unit']) for r in rows], title)
        if not code:
            raise ValueError('chart_rows_need_one_unit_and_numeric_values')
        rendered = PythonInterpreterTool().execute(code, chart_title=title)
        files = [a['file_path'] for a in rendered.artifacts if a.get('type') == 'image/png']
        if not rendered.success or not files:
            raise ValueError('chart_rendering_failed:' + str(rendered.error))
        return {'title': title, 'bars': len(rows), 'chart_files': files}

    def compare_schools(self, schools, year, **values):
        if len(schools) == 2 and not values:
            with closing(school.connect()) as connection:
                selected = [unique_school(connection, name, year) for name in schools]
                return school.compare_schools(selected[0]['id'], selected[1]['id'], year=year, connection=connection)
        return {'year': year, 'schools': [{'school': name, 'result': self.evaluate_school(name, year, **values)}
                                        for name in schools]}


def validate_argument(value, schema):
    """Validate the small JSON Schema subset emitted by definitions()."""
    kind = schema['type']
    types = {'object': dict, 'array': list, 'string': str, 'integer': int}
    if kind == 'number':
        if type(value) not in (int, float):
            raise ValueError('parameter_type_required:number')
        return
    if type(value) is not types[kind]:
        raise ValueError('parameter_type_required:' + kind)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('parameter_value_outside_enum:' + str(value))
    if kind == 'object':
        if set(schema['required']) - value.keys() or value.keys() - schema['properties'].keys():
            raise ValueError('missing_or_unknown_function_parameter')
        for key, item in value.items():
            validate_argument(item, schema['properties'][key])
    elif kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', len(value)):
            raise ValueError('parameter_array_length')
        if schema.get('uniqueItems') and len(set(value)) != len(value):
            raise ValueError('parameter_array_duplicates')
        for item in value:
            validate_argument(item, schema['items'])
    elif kind == 'integer':
        if value < schema.get('minimum', value) or value > schema.get('maximum', value):
            raise ValueError('parameter_integer_range')
