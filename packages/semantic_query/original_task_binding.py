"""Conservative, independent original-question binding for two school counts.

This recognizer deliberately abstains outside a narrow catalogue-backed scope.
It never reads a model candidate when forming the original-question signature.
"""

from __future__ import annotations

import re
from contextlib import closing
from typing import Any, Dict, Optional

from packages.semantic_query.catalogue import load_semantic_catalogue
from scripts.school_engine import connect


_YEAR = re.compile(r"(?<!\d)(20\d{2})年?(?!\d)")
_SCHOOL = re.compile(r"[\u4e00-\u9fffA-Za-z·（）()]{2,}?(?:小学|中学|幼儿园|学校)")
_SUBTRACT = re.compile(r"前者\s*减\s*后者|前(?:一)?项\s*减\s*后(?:一)?项")
_REVERSE_SUBTRACT = re.compile(r"后者\s*减\s*前者|后(?:一)?项\s*减\s*前(?:一)?项")
_FORWARD_PERCENT = re.compile(r"前(?:者|一?项)\s*(?:除以|÷)\s*后(?:者|一?项)\s*(?:乘以|×|\*)?\s*100\s*[%％]")
_METHOD_OR_NEGATIVE_SCHOOL = re.compile(r"不问|不查|不看|不比较|不是|不要|别查|排除|仅举例|比如|例如|假设|假如|方法|口径|示例")


def original_signature(question: str) -> Dict[str, Any]:
    """Return DETERMINATE or UNKNOWN; no candidate or conversation inheritance."""
    text = str(question or "")
    coverage = raw_pair_coverage_signature(text)
    if coverage is not None:
        return coverage
    pair = _literal_ratio_pair_signature(text)
    if pair is not None:
        return pair
    if _FORWARD_PERCENT.search(text):
        return _school_device_percent_signature(text)
    # Methodological/negative mentions are not a positive request.
    if re.search(r"不(?:是|要|看|问|计算|比较|展示).{0,35}(?:工作内容|多少种|前者减后者)|(?:仅|只是)(?:举例|示例|说明口径)", text):
        return {"status": "UNKNOWN", "reason": "negative_or_method_mention"}
    assertion = re.split(r"[。！？!?]", text, maxsplit=1)[0]
    if not (re.search(r"(?:教育数字化)?管理.{0,16}工作内容", assertion)
            and re.search(r"各有多少种|分别有多少种|各有几种|分别有几种", assertion)
            and _SUBTRACT.search(text)
            and not re.search(r"维度(?:得分|分数)|教育治理(?:得分|分数)|归一化|标准化|原始编码|百分比|除以|比值", text)):
        return {"status": "UNKNOWN", "reason": "scope_or_operation_not_unique"}
    year_mentions = list(_YEAR.finditer(text))
    if len(year_mentions) != 1:
        return {"status": "UNKNOWN", "reason": "year_not_unique"}
    years = [year_mentions[0].group(1)]
    school_mentions = list(_SCHOOL.finditer(text))
    if len(school_mentions) != 1:
        return {"status": "UNKNOWN", "reason": "school_not_unique"}
    school_mention = school_mentions[0]
    if school_mention.end() > len(assertion) or year_mentions[0].start() > len(assertion):
        return {"status": "UNKNOWN", "reason": "school_or_year_outside_assertion"}
    clause_start = max((text.rfind(char, 0, school_mention.start()) for char in "，,。；;！？!?"), default=-1)
    school_prefix = text[clause_start + 1:school_mention.start()]
    school_suffix = text[school_mention.end():school_mention.end() + 12]
    if _METHOD_OR_NEGATIVE_SCHOOL.search(school_prefix) or re.search(r"为例|仅作(?:示例|例子)|只是(?:示例|例子)|方法说明", school_suffix):
        return {"status": "UNKNOWN", "reason": "school_mention_not_asserted"}
    school_name = school_mention.group(0)
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT id, school_name FROM schools WHERE school_name=? AND year=?",
            (school_name, years[0]),
        ).fetchall()
    if len(rows) != 1:
        return {"status": "UNKNOWN", "reason": "school_identity_not_unique"}
    items = [item for item in load_semantic_catalogue()["items"] if
             item.get("pair_family") == "management_work_content_count"
             and item.get("value_type") == "count"
             and item.get("scale") == "raw_count"
             and item.get("unit") == "种"]
    matches = []
    for item in items:
        spans = [match for alias in item.get("pair_subject_aliases") or []
                 for match in re.finditer(re.escape(alias), assertion)]
        if spans:
            matches.append((min(match.start() for match in spans), int(item["id"])))
    if len(matches) != 2 or matches[0][0] == matches[1][0]:
        return {"status": "UNKNOWN", "reason": "catalogue_pair_not_unique"}
    ordered = [qid for _, qid in sorted(matches)]
    mentioned_qids = {int(value) for value in re.findall(r"(?i)(?:\bQ\s*|第\s*)(\d{1,3})(?:\s*题)?", text)}
    if mentioned_qids and mentioned_qids != set(ordered):
        return {"status": "UNKNOWN", "reason": "explicit_question_id_conflict"}
    if re.search(r"(?:不是|不用|不要|排除|不含).{0,10}(?:Q\s*\d+|第\s*\d+\s*题)", text, re.I):
        return {"status": "UNKNOWN", "reason": "negated_question_id"}
    # The operand phrase must occur after the two named subjects.
    subtract_mentions = list(_SUBTRACT.finditer(text))
    if len(subtract_mentions) != 1 or _REVERSE_SUBTRACT.search(text):
        return {"status": "UNKNOWN", "reason": "operand_direction_not_unique"}
    subtract = subtract_mentions[0]
    direction_context = text[max(0, subtract.start() - 14):subtract.end() + 18]
    if re.search(r"并非|不是|不按|不要|错误|示例|例子|仅举例", direction_context):
        return {"status": "UNKNOWN", "reason": "operand_direction_negated_or_example"}
    if subtract.start() <= max(position for position, _ in matches):
        return {"status": "UNKNOWN", "reason": "operand_order_not_unique"}
    chart_positive = bool(re.search(r"(?:两柱|柱状|柱形|条形|画图|绘图|图表).{0,8}图|用两柱图|画两柱图", text))
    chart_negative = bool(re.search(r"不(?:用|要|需|必)(?:画|做|生成|展示)?(?:两柱|柱状|柱形|条形|图表|图)|无需.{0,6}(?:画图|图表)", text))
    if chart_positive and chart_negative:
        return {"status": "UNKNOWN", "reason": "chart_polarity_conflict"}
    return {
        "status": "DETERMINATE", "object_type": "school", "school_id": int(rows[0]["id"]),
        "school_name": str(rows[0]["school_name"]), "year": years[0],
        "operation": "school_fact", "qids": ordered, "value_view": "raw_value",
        "derived": {"operation": "subtract", "left_qid": ordered[0], "right_qid": ordered[1]},
        "charts_required": chart_positive,
    }


def _canonical_quantity_name(value):
    """Normalize bounded count paraphrases; keep actors, units and time scopes."""
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(value or ""))
    text = text.replace("信息技术", "技术")
    text = re.sub(r"常态(?:化)?(?:教学)?应用", "常态应用", text)
    text = re.sub(r"(?:类别|种类)(?:数量|数)", "种类数", text)
    text = text.replace("措施种类数", "措施数")
    return text


def _quoted_quantity_pair(text, catalogue):
    """Every complete quoted count name must uniquely occur in the catalogue.

    No fuzzy similarity threshold, answer values, candidate ids, or school list.
    Units/type disambiguate count kinds from ratios, sessions and person-times.
    """
    quotes = list(re.finditer(r'“([^“”]+)”|"([^"\n]+)"', text))
    if len(quotes) != 2:
        return None
    result = []
    for quote in quotes:
        phrase = quote.group(1) or quote.group(2)
        canonical = _canonical_quantity_name(phrase)
        if len(canonical) < 5:
            return None
        matches = []
        for item in catalogue:
            if item.get('value_type') != 'count' or item.get('scale') != 'raw_count' or item.get('unit') != '种':
                continue
            names = [item.get('question'), item.get('summary'), *(item.get('aliases') or [])]
            if any(canonical in _canonical_quantity_name(name) for name in names):
                matches.append(item)
        if len(matches) != 1:
            return None
        item = matches[0]
        # An omitted actor may use a unique catalogue measure; an explicit
        # conflicting actor must never inherit that measure's school-leader scope.
        if 'school_leader' in (item.get('subjects') or []) and re.search(r'教师|老师|学生|教职工', text):
            return None
        result.append((quote.start(), item['id'], phrase))
    return result if result[0][1] != result[1][1] else None


def raw_pair_coverage_signature(text):
    """Independent coverage of two explicitly named, catalogue-grounded records.

    Only names and type metadata are read. No answer values, model draft or
    benchmark identifiers participate in item selection. Short family aliases
    require their management-content context; ambiguous paraphrases abstain.
    """
    if not re.search(r"两项|两种|两条", text) or not re.search(r"原始值|照录两项值", text):
        return None
    if re.search(r"图表|画图|绘图|柱状|分析|建议|归一|标准化|综合得分|或者|或是|假设|例如|示例|为例|比较|对比", text):
        return None
    first_year = _YEAR.search(text)
    # Task-extension words belong to the requested scope, not the literal
    # institution name (for example a school containing "第三").
    scope_text = text[first_year.end():] if first_year else text
    if re.search(r"三项|第三|另外|另查|另加|再查|还有|顺便|再给|以及|不(?:要)?(?:查|查询|给出|核对|列出)|别查", scope_text):
        return None
    years = list(_YEAR.finditer(text))
    if len(years) != 1:
        return None
    suffix = text[years[0].end():]
    # A record label containing 学校 is not another entity. All other school
    # mentions after the anchored school/year are outside this single-school scope.
    if _SCHOOL.search(suffix.replace('两项学校管理记录', '两项记录')):
        return None
    affirmative = re.sub(r"不(?:要)?加总(?:或说明编码含义)?|不合并|不解释编码", "", suffix)
    if re.search(r"不|(?<!分)别(?:查|列|看|给|计算)|无需|无须|排除|仅|只", affirmative):
        return None
    prefix = re.sub(r"^(?:请查询|请给出|帮我核对|请核对|查询|核对|请问)", "", text[:years[0].start()]).removesuffix("在")
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z·（）()]{2,}(?:小学|中学|学校|幼儿园|中心校)", prefix):
        return None
    with closing(connect()) as conn:
        rows = conn.execute("SELECT id, school_name FROM schools WHERE school_name=? AND year=?", (prefix, years[0].group(1))).fetchall()
    if len(rows) != 1:
        return None
    # Keep item mentions, not instructions prohibiting interpretation/merging.
    body = re.split(r"两项原始值|两项学校管理记录|各登记了多少", text[years[0].end():], maxsplit=1)[0]
    if re.search(r"不|(?<!分)别(?:查|列|看|给)|排除|除了|三项|第三项|另加|另外", body):
        return None
    compact = lambda value: re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(value or ""))
    body = compact(body)
    catalogue = load_semantic_catalogue()['items']
    descriptions = {row['id']: [compact(value) for value in (row.get('question'), row.get('summary'), *(row.get('aliases') or []))] for row in catalogue}
    grounded = []
    for item in catalogue:
        hits = []
        other = [name for qid, names in descriptions.items() if qid != item['id'] for name in names]
        for name in descriptions[item['id']]:
            for size in range(len(name), 5, -1):
                for start in range(len(name) - size + 1):
                    fragment = name[start:start + size]
                    if fragment in body and not any(fragment in value for value in other):
                        hits.append((-size, body.index(fragment), fragment))
                if hits:
                    break
        if item.get('pair_family') == 'management_work_content_count':
            for alias in item.get('pair_subject_aliases') or []:
                for match in re.finditer(re.escape(alias) + r"(?:类)?(?:教育数字化|数字化)?管理(?:的)?(?:工作)?内容", body):
                    hits.append((-len(match.group()), match.start(), match.group()))
        if hits:
            best = min(hits)
            grounded.append((best[1], item['id'], best[2]))
    matching_basis = 'literal_catalogue_fragments'
    if len(grounded) != 2 or grounded[0][0] == grounded[1][0]:
        grounded = _quoted_quantity_pair(text[years[0].end():], catalogue)
        if grounded is None:
            return None
        matching_basis = 'typed_catalogue_quantity_paraphrases'
    grounded.sort()
    qids = [row[1] for row in grounded]
    explicit = {int(token) for groups in re.findall(r"Q\s*(\d+)|第(\d+)题", text, re.I) for token in groups if token}
    if explicit and explicit != set(qids):
        return None
    derived = None
    arithmetic = list(_SUBTRACT.finditer(text))
    if arithmetic:
        if (len(arithmetic) != 1 or _REVERSE_SUBTRACT.search(text)
                or re.search(r"不按|不要计算|不计算|不求|除以|相加|求和|合计|百分点", text)):
            return None
        selected = [item for item in catalogue if item['id'] in qids]
        if any(item.get('value_type') != 'count' or item.get('scale') != 'raw_count' for item in selected):
            return None
        derived = {'operation': 'subtract', 'left_qid': qids[0], 'right_qid': qids[1]}
    elif (not re.search(r"不合并|不要加总|分别列出", text)
          or re.search(r"计算|减|差值|之差|除以|求和|相加|合计|比值|乘以|加总|加起来|[－−÷×+*/-]", re.sub(r"不要加总|不合并", "", text))):
        return None
    return {'status': 'DETERMINATE', 'source': 'original_record_coverage', 'recoverable': True,
            'object_type': 'school', 'school_id': int(rows[0]['id']), 'school_name': prefix,
            'year': years[0].group(1), 'operation': 'school_fact', 'qids': qids,
            'value_view': 'raw_value', 'derived': derived, 'charts_required': False,
            'source_fragments': [row[2] for row in grounded], 'matching_basis': matching_basis}


def repair_record_coverage(signature, candidate, original_candidate=None):
    """Repair a valid draft only for the same explicitly grounded school/year."""
    identity = candidate if original_candidate is None else original_candidate
    if (signature and signature.get('matching_basis') == 'typed_catalogue_quantity_paraphrases'
            and set(identity.get('question_ids') or []) != set(signature['qids'])):
        # Short paraphrases must agree with the original model's two-item set;
        # the independent original order, rather than that array order, binds math.
        return None
    if not (signature and identity.get('schools') == [signature['school_name']]
            and [str(year) for year in identity.get('periods') or []] == [signature['year']]
            and not identity.get('regions') and not identity.get('school_filter')
            and not identity.get('compound_key') and not identity.get('school_field')
            and identity.get('statistic') in {None, ''}
            and not candidate.get('regions')
            and not candidate.get('school_filter')
            and not candidate.get('compound_key') and not candidate.get('school_field')
            and candidate.get('statistic') in {None, ''}
            and set(candidate.get('requested_outputs') or []) <= {'direct_answer'}):
        return None
    result = dict(candidate)
    result.update(intent='school_fact', question_ids=list(signature['qids']), dimension=None,
                  schools=[signature['school_name']], periods=[signature['year']],
                  value_view='raw_value', clarify_reason=None, missing=[])
    if result.get('evidence') is not None:
        evidence = dict(result['evidence'])
        for key in ('school_a', 'school_b', 'region', 'region_a', 'region_b', 'dimension'):
            evidence.pop(key, None)
        evidence.update(school=signature['school_name'], period=signature['year'])
        result['evidence'] = evidence
    return result


def _literal_ratio_pair_signature(text):
    """Two unique catalogue descriptions, one explicit school/year and order.

    This independent check reads neither the draft nor reference answer values.
    It abstains when the original text cannot identify exactly two ratio items.
    """
    if not re.search(r"第一项\s*减\s*第二项|前项\s*减\s*后项|前者\s*减\s*后者", text):
        return None
    if "百分点" not in text or re.search(r"不(?:要|算|计算|按)|示例|假设|或者|或是", text):
        return None
    if re.search(r"图表|折线图|柱状图|表格|绘图|画图|分析|建议|排名", text):
        return None
    years = list(_YEAR.finditer(text))
    if len(years) != 1:
        return None
    prefix = re.sub(r"^(?:请查询|请核对|查询|核对|请问)", "", text[:years[0].start()]).removesuffix("在")
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z·（）()]{2,}(?:小学|中学|学校|小学校|中心校)", prefix):
        return None
    with closing(connect()) as conn:
        rows = conn.execute("SELECT id, school_name FROM schools WHERE school_name=? AND year=?", (prefix, years[0].group(1))).fetchall()
    if len(rows) != 1:
        return None
    compact = lambda value: re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(value or ""))
    assertion = re.split(r"[:：?？]", text, maxsplit=1)[0]
    body = compact(assertion[years[0].end():])
    catalogue = load_semantic_catalogue()['items']
    descriptions = {row['id']: [compact(value) for value in (row.get('question'), row.get('summary'), *(row.get('aliases') or []))] for row in catalogue}
    grounded = []
    for item in catalogue:
        if item.get('value_type') != 'ratio' or item.get('unit') != '%':
            continue
        matches = []
        other = [name for qid, names in descriptions.items() if qid != item['id'] for name in names]
        for name in descriptions[item['id']]:
            for size in range(len(name), 5, -1):
                for start in range(len(name) - size + 1):
                    fragment = name[start:start + size]
                    if fragment in body and not any(fragment in value for value in other):
                        matches.append((-size, body.index(fragment), fragment))
                if matches:
                    break
        if matches:
            grounded.append((min(matches)[1], item['id'], min(matches)[2]))
    if len(grounded) != 2 or grounded[0][0] == grounded[1][0]:
        return None
    grounded.sort()
    qids = [row[1] for row in grounded]
    explicit = {int(token) for groups in re.findall(r"Q\s*(\d+)|第(\d+)题", text, re.I) for token in groups if token}
    if explicit and explicit != set(qids):
        return None
    return {'status':'DETERMINATE', 'source':'unique_catalogue_literal_fragments',
            'recoverable':True, 'object_type':'school', 'school_id':int(rows[0]['id']),
            'school_name':str(rows[0]['school_name']), 'year':years[0].group(1),
            'operation':'school_fact', 'qids':qids, 'value_view':'raw_value',
            'derived':{'operation':'subtract','left_qid':qids[0],'right_qid':qids[1],'unit':'百分点'},
            'charts_required':False, 'source_fragments':[row[2] for row in grounded]}


def grounded_repair_candidate(signature, candidate):
    """Repair item mapping only when literal identity and units are determinate."""
    if not (signature.get('recoverable') and candidate.get('schools') == [signature.get('school_name')]
            and [str(year) for year in candidate.get('periods') or []] == [signature.get('year')]
            and candidate.get('value_view') == 'raw_value'
            and set(candidate.get('requested_outputs') or []) <= {'direct_answer'}):
        return None
    result = dict(candidate)
    result.update(intent='school_fact', question_ids=list(signature['qids']), dimension=None,
                  target_stat=None, regions=[], compound_key=None, school_field=None,
                  requested_outputs=['direct_answer'])
    return result


def _school_device_percent_signature(text: str) -> Dict[str, Any]:
    """Bind an explicit ordered device percentage independently of the model."""
    if re.search(r"(?:不|无需|别)(?:要|用|计算|求|给出)?.{0,8}(?:前项|前者|百分比|比例)|后(?:者|一?项)\s*(?:除以|÷)\s*前", text):
        return {"status": "UNKNOWN", "reason": "percent_request_negated_or_conflicting"}
    assertion = re.split(r"[。！？!?]", text, maxsplit=1)[0]
    year_matches = list(_YEAR.finditer(assertion))
    if len(year_matches) != 1:
        return {"status": "UNKNOWN", "reason": "school_or_year_not_unique"}
    school_name = re.sub(r"^(?:请查询|请问|查询)", "", assertion[:year_matches[0].start()])
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z·（）()]{2,}?(?:小学|中学|幼儿园|学校)", school_name):
        return {"status": "UNKNOWN", "reason": "school_identity_not_unique"}
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT id, school_name FROM schools WHERE school_name=? AND year=?",
            (school_name, year_matches[0].group(1)),
        ).fetchall()
    if len(rows) != 1:
        return {"status": "UNKNOWN", "reason": "school_identity_not_unique"}
    matches = []
    for item in load_semantic_catalogue()["items"]:
        if (item.get("value_type") == "count" and item.get("scale") == "raw_count"
                and item.get("unit") == "台"):
            name = str(item.get("question") or "")
            hits = list(re.finditer(re.escape(name), assertion)) if name else []
            if hits:
                matches.append((hits[0].start(), int(item["id"])))
    if len(matches) != 2 or matches[0][0] == matches[1][0]:
        return {"status": "UNKNOWN", "reason": "device_pair_not_unique"}
    ordered = [qid for _, qid in sorted(matches)]
    if _FORWARD_PERCENT.search(text).start() <= max(position for position, _ in matches):
        return {"status": "UNKNOWN", "reason": "percent_operand_order_not_unique"}
    return {
        "status": "DETERMINATE", "object_type": "school", "school_id": int(rows[0]["id"]),
        "school_name": str(rows[0]["school_name"]), "year": year_matches[0].group(1),
        "operation": "school_fact", "qids": ordered, "value_view": "raw_value",
        "derived": {"operation": "divide_percent", "left_qid": ordered[0], "right_qid": ordered[1]},
        "charts_required": False,
    }


def route_matches(signature: Dict[str, Any], routed: Dict[str, Any], answer_spec: Dict[str, Any]) -> bool:
    if signature.get("status") != "DETERMINATE":
        return True
    metric = routed.get("metric_set") or {}
    return bool(
        routed.get("intent") == "school_fact"
        and routed.get("school") == signature["school_name"]
        and str(routed.get("year")) == signature["year"]
        and routed.get("value_view") == signature["value_view"]
        and metric.get("type") == "question_set"
        and metric.get("qids") == signature["qids"]
        and metric.get("derived") == signature["derived"]
        and answer_spec.get("operation") == "school_fact"
        and answer_spec.get("granularity") == "item_set"
        and answer_spec.get("value_view") == "raw_value"
        and (not signature["charts_required"] or "charts" in (answer_spec.get("required_outputs") or []))
    )


def candidate_matches(signature: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """A candidate supplies identities and raw view; the question supplies direction."""
    try:
        qids = [int(qid) for qid in candidate.get("question_ids") or []]
    except (TypeError, ValueError):
        return False
    if not (candidate.get("intent") == "school_fact"
            and candidate.get("schools") == [signature["school_name"]]
            and [str(year) for year in candidate.get("periods") or []] == [signature["year"]]
            and len(qids) == 2 and set(qids) == set(signature["qids"])
            and candidate.get("value_view") == "raw_value"):
        return False
    declared = candidate.get("derived") or candidate.get("operation")
    if declared:
        return declared == signature["derived"]
    return True


def receipt_matches(signature: Dict[str, Any], contract: Any, result: Dict[str, Any], receipt: Any) -> bool:
    if signature.get("status") != "DETERMINATE":
        return True
    scope = result.get("execution_scope") or {}
    return bool(
        contract.entity_id == signature["school_name"]
        and contract.school_id == signature["school_id"]
        and contract.period == signature["year"]
        and contract.value_view == "raw_value"
        and list(contract.indicator_ids) == signature["qids"]
        and (contract.metric_spec or {}).get("derived") == signature["derived"]
        and scope.get("school_id") == signature["school_id"]
        and scope.get("period") == signature["year"]
        and scope.get("indicator_ids") == signature["qids"]
        and scope.get("derived") == signature["derived"]
        and receipt.operation == "school_fact"
        and receipt.query_template == "school_fact_set_v1"
        and receipt.actual_entity_id == str(signature["school_id"])
        and receipt.actual_period == signature["year"]
        and tuple(receipt.parameters) == (
            signature["school_id"], signature["year"], contract.metric_key, *signature["qids"]
        )
    )
