"""Load and validate the versioned 83-item semantic catalogue."""

from __future__ import annotations

import os
import json
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from packages.core.frozen_paths import resource_root


CATALOGUE_FILE = "school_digitization_semantics_v2.yaml"
EXPECTED_IDS = set(range(1, 84))
EXPECTED_DIMENSIONS = {
    "数字资源",
    "教育教学",
    "数字素养",
    "基础设施",
    "教育治理",
    "保障机制",
}

_EXPLICIT_QUESTION_PATTERNS = (
    re.compile(r"第\s*([0-9一二两三四五六七八九十百]+)\s*(?:题|项)"),
    re.compile(r"(?<![A-Za-z0-9])q(?:uestion)?\s*[-_#:]?\s*([0-9]{1,3})(?![0-9])", re.I),
)


def _question_number(token: str) -> Optional[int]:
    text = str(token or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if "百" in text:
        left, right = text.split("百", 1)
        if left not in digits:
            return None
        suffix = _question_number(right) if right else 0
        return None if suffix is None else digits[left] * 100 + suffix
    if "十" in text:
        left, right = text.split("十", 1)
        tens = 1 if not left else digits.get(left)
        ones = 0 if not right else digits.get(right)
        return None if tens is None or ones is None else tens * 10 + ones
    if len(text) == 1 and text in digits:
        return digits[text]
    return None


def extract_explicit_question_ids(text: str) -> List[int]:
    """Return ordered, deduplicated questionnaire ids written explicitly by the user."""
    positions=[]
    for pattern in _EXPLICIT_QUESTION_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            qid = _question_number(match.group(1))
            if qid is not None:positions.append((match.start(),qid))
    numbers=r'[0-9一二两三四五六七八九十百]+'
    for match in re.finditer(r'第\s*('+numbers+r'(?:\s*[、,，和与及]\s*第?\s*'+numbers+r')+)\s*(?:题|项)',str(text or '')):
        for number in re.finditer(numbers,match.group(1)):
            qid=_question_number(number.group())
            if qid is not None:positions.append((match.start(1)+number.start(),qid))
    found: List[int] = []
    for _,qid in sorted(positions):
        if qid not in found:found.append(qid)
    return found


def _catalogue_path() -> str:
    candidates = [
        os.path.join(resource_root(), "evaluation_templates", CATALOGUE_FILE),
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "evaluation_templates",
            CATALOGUE_FILE,
        ),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(f"semantic_catalogue_missing:{CATALOGUE_FILE}")


@lru_cache(maxsize=2)
def load_semantic_catalogue() -> Dict[str, Any]:
    """Return a validated, prompt-safe catalogue without answer values."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - runtime dependency boundary
        raise RuntimeError("semantic_catalogue_yaml_unavailable") from exc

    with open(_catalogue_path(), encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("semantic_catalogue_defaults_invalid")
    annotations = raw.get("items")
    if not isinstance(annotations, list):
        raise ValueError("semantic_catalogue_items_invalid")
    by_id: Dict[int, Dict[str, Any]] = {}
    for item in annotations:
        if not isinstance(item, dict):
            raise ValueError("semantic_catalogue_item_invalid")
        qid = int(item.get("id") or 0)
        if qid in by_id:
            raise ValueError(f"semantic_catalogue_duplicate_id:{qid}")
        by_id[qid] = {**defaults, **dict(item)}
    if set(by_id) != EXPECTED_IDS:
        missing = sorted(EXPECTED_IDS - set(by_id))
        extra = sorted(set(by_id) - EXPECTED_IDS)
        raise ValueError(f"semantic_catalogue_id_set_invalid:missing={missing}:extra={extra}")

    from scripts.school_engine import load_school_template

    template = load_school_template() or {}
    source_by_id: Dict[int, Dict[str, Any]] = {}
    for dimension in template.get("dimensions") or []:
        dimension_name = str(dimension.get("name") or "").strip()
        for metric in dimension.get("metrics") or []:
            qid = int(metric.get("id") or 0)
            source_by_id[qid] = {
                "question": str(metric.get("content") or "").strip(),
                "dimension": dimension_name,
                "dimension_key": str(dimension.get("key") or "").strip(),
                "topic": str(metric.get("level2") or "").strip(),
            }
    if set(source_by_id) != EXPECTED_IDS:
        raise ValueError("semantic_catalogue_source_template_not_83")

    public_items = []
    for qid in sorted(EXPECTED_IDS):
        source = source_by_id[qid]
        annotation = by_id[qid]
        if source["dimension"] not in EXPECTED_DIMENSIONS:
            raise ValueError(f"semantic_catalogue_dimension_invalid:{qid}")
        if not annotation.get("summary") or not annotation.get("value_type"):
            raise ValueError(f"semantic_catalogue_annotation_incomplete:{qid}")
        public_items.append({
            "id": qid,
            **source,
            "summary": str(annotation.get("summary") or "").strip(),
            "subjects": [str(v) for v in annotation.get("subjects") or ["school"]],
            "value_type": str(annotation.get("value_type") or "").strip(),
            "unit": str(annotation.get("unit") or "").strip(),
            "time_window": str(annotation.get("time_window") or "record_year").strip(),
            "scale": str(annotation.get("scale") or "raw_value").strip(),
            "coding_status": str(annotation.get("coding_status") or "not_applicable").strip(),
            "aliases": [str(v) for v in annotation.get("aliases") or []],
            "confusable_with": [int(v) for v in annotation.get("confusable_with") or []],
            "notes": str(annotation.get("notes") or "").strip(),
        })
    topics = []
    for topic in raw.get('topics') or []:
        members = [i['id'] for i in public_items if i['topic'] == topic['name'] and i['dimension'] == topic['dimension']]
        if not members:
            raise ValueError('semantic_topic_not_in_template:' + topic['name'])
        topics.append({**topic, 'indicator_ids': members, 'confusable_items': members})
    if {i['topic'] for i in public_items} != {t['name'] for t in topics}:
        raise ValueError('semantic_topic_template_membership_mismatch')
    return {
        "version": str(raw.get("version") or "school-digitization-semantics-v1"),
        "source_template": str(raw.get("source_template") or "school_digitization_v1.yaml"),
        "item_count": len(public_items),
        "items": public_items,
        "topics": topics,
    }


def topic_catalogue():
    return load_semantic_catalogue()['topics']


@lru_cache(maxsize=1)
def dimension_meanings():
    """Fixed source-questionnaire descriptions, shared by every parser arm."""
    path=os.path.join(os.path.dirname(_catalogue_path()),'school_digitization_dimension_meanings_v1.json')
    with open(path,encoding='utf-8') as handle:
        return json.load(handle)['dimensions']


def dimension_meanings_prompt():
    return '一级与二级维度含义（源问卷原文；按含义选择指标粒度，不确定时澄清）：只描述某二级维度含义而未说名称时仍选该topics，不因它属于某一级就改用父级分；只有整体父级范围或列全组成才按原归并规则。\n'+'\n'.join(
        d['name']+'：'+d['description']+'；二级：'+'；'.join(t['name']+'：'+t['description'] for t in d['topics'])
        for d in dimension_meanings())+'\n'


def topic_definition(name):
    topic=next((t for t in topic_catalogue() if t['name'] == name),None)
    if topic is None:
        raise ValueError('semantic_topic_not_in_catalogue:'+str(name))
    return topic


def dimension_components(dimension,year):
    """Composition and R11 availability only; no score or new data query."""
    from packages.core.item_collection import collected_ids
    rows=[]
    for t in topic_catalogue():
        if t['dimension']!=dimension:continue
        missing=year is not None and not collected_ids(year,t['indicator_ids'])
        rows.append(dict(name=t['name'],dimension=dimension,indicator_ids=list(t['indicator_ids']),
            year=year,all_items_uncollected=bool(missing),
            message='当年未采集，不计入' if missing else '已包含在'+dimension+'维度得分中'))
    return rows


def topic_scope(text):
    """Only registered topic phrases bind; explicit/longer leaf phrases win."""
    if extract_explicit_question_ids(text):
        return None
    # Whole source descriptions are registered semantics, not new aliases or
    # fuzzy word overlap. Reuse their topic's normal leaf/quantity precedence.
    parent_descriptions={d['description'] for d in dimension_meanings()}
    for dimension in dimension_meanings():
        for topic in dimension['topics']:
            if topic['description'] not in parent_descriptions:
                text=text.replace(topic['description'],topic['name'])
    from packages.semantic_query.runtime import _DESCRIPTIVE_DIMENSIONS
    described=[(name,pattern.search(text)) for name,pattern in _DESCRIPTIVE_DIMENSIONS.items()]
    described=[(name,match) for name,match in described if match]
    if described and not any(alias in text for topic in topic_catalogue() for alias in [topic['name'],*topic['aliases']]):
        if len(described)==1:
            name,match=described[0]
            return dict(dimension=name,topics=[],quotes=[match.group(0)])
        return None
    hits=[]
    for topic in topic_catalogue():
        for alias in [topic['name'], *topic['aliases']]:
            for match in re.finditer(re.escape(alias), text):
                hits.append((match.start(),match.end(),topic,alias))
    hits=[h for h in hits if not any(k[0]<=h[0] and k[1]>=h[1] and k[1]-k[0]>h[1]-h[0] for k in hits)]
    if not hits:
        return None
    # Quantity requests and longer registered item names remain leaf requests.
    if re.search(r'多少(?:台|人|元|间|种|个|兆)|几(?:台|人|元|间)|带宽|占比|比例|百分比|原始值|原值',text):
        return None
    for item in load_semantic_catalogue()['items']:
        for alias in item['aliases']:
            if alias in text and any(h[3] in alias and alias!=h[3] for h in hits):
                return None
    # Share literal item evidence with school-fact binding, including registered
    # full names omitted from aliases. Embedded topic words cannot steal a leaf.
    from packages.semantic_query.runtime import _catalogue_alias_items
    for _,literal in _catalogue_alias_items(text):
        if any(h[3] in literal and literal!=h[3] for h in hits):
            return None
    # An incomplete literal enumeration cannot contradict a model's whole
    # dimension. Bound the metric phrase, excluding entity and output lists.
    first,last=min(h[0] for h in hits),max(h[1] for h in hits)
    starts=list(re.finditer(r'在|学校(?:的)?|20\d{2}年?|[，,。；;！？!?]',text[:first]))
    start=starts[-1].end() if starts else 0
    end_match=re.search(r'方面|这些|条件|平均|均分|得分|分数|做得|怎么样|如何|是多少',text[last:])
    end=last+end_match.start() if end_match else len(text)
    separators=list(re.finditer(r'、|以及|和|与|及',text[start:end]))
    if separators:
        edges=[start,*[start+m.end() for m in separators]]
        stops=[*[start+m.start() for m in separators],end]
        for left,right in zip(edges,stops):
            if text[left:right].strip() and not any(a<right and b>left for a,b,_,_ in hits):
                return None
    selected=list(dict.fromkeys(h[2]['name'] for h in sorted(hits,key=lambda h:h[0])))
    definitions=[topic_definition(n) for n in selected]
    parents={t['dimension'] for t in definitions}
    if len(parents)==1:
        parent=next(iter(parents))
        siblings={t['name'] for t in topic_catalogue() if t['dimension']==parent}
        if set(selected)==siblings or all(t.get('collapse_to_dimension') for t in definitions):
            return dict(dimension=parent,topics=[],listed_topics=selected,quotes=list(dict.fromkeys(h[3] for h in hits)))
    return dict(dimension=None,topics=selected,quotes=list(dict.fromkeys(h[3] for h in hits)))


def question_metric(qid: int) -> Dict[str, Any]:
    """Project one trusted catalogue item into the metric shape used by routers."""
    question_id = int(qid)
    item = next(
        (row for row in load_semantic_catalogue().get("items") or [] if int(row.get("id") or 0) == question_id),
        None,
    )
    if item is None:
        raise ValueError("semantic_question_id_not_in_catalogue")
    return {
        "type": "question",
        "qid": question_id,
        "name": str(item.get("question") or ""),
        "dimension": str(item.get("dimension") or ""),
        "unit": str(item.get("unit") or ""),
        "related": list(item.get("confusable_with") or []),
        "value_type": str(item.get("value_type") or ""),
        "scale": str(item.get("scale") or ""),
        "coding_status": str(item.get("coding_status") or "not_applicable"),
        "coding_notes": str(item.get("notes") or ""),
    }


def described_dimension_binding(text: str, items=None) -> Dict[str, Any]:
    """Bind a whole personnel-capability block from three catalogue meanings.

    All three independent attributes must be affirmative and converge on one
    dimension. This is neither character-overlap retrieval nor an item selector.
    Dimension names and item ids come exclusively from the common catalogue.
    """
    out = {"dimension": None, "quote": None, "basis": [], "catalogue_ids": []}
    if extract_explicit_question_ids(text) or not re.search(r"整块|整个维度|这一维度", text):
        return out
    if re.search(r"不问|不看|不要|排除|除外|不含|不包括", text):
        return out
    if not re.search(r"师生|教师|学生", text) or not re.search(r"数字|信息|技术", text):
        return out
    patterns = {
        "capability": r"(?:能够|能力|熟练).{0,18}(?:信息|数字|技术)|(?:信息|数字|技术).{0,18}(?:能力|熟练|能够)",
        "training": r"培训",
        "practice": r"实践|展示活动|比赛|评比",
    }
    if not all(re.search(p, text) for p in patterns.values()):
        return out
    items = items if items is not None else load_semantic_catalogue()["items"]
    hits = {}
    for cue, pattern in patterns.items():
        hits[cue] = [i for i in items if set(i.get("subjects") or []) & {"teacher", "student"}
                     and re.search(pattern, " ".join(str(i.get(k) or "") for k in ("question", "summary")))]
    dimensions = set.intersection(*({i["dimension"] for i in rows} for rows in hits.values()))
    if len(dimensions) != 1:
        return out
    dim = next(iter(dimensions))
    selected = {cue: [i["id"] for i in rows if i["dimension"] == dim] for cue, rows in hits.items()}
    return {"dimension": dim, "quote": text, "basis": selected,
            "catalogue_ids": sorted({i for ids in selected.values() for i in ids})}
