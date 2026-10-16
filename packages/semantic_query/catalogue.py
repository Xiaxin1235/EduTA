from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from packages.core.frozen_paths import resource_root

CATALOGUE_FILE = "school_digitization_semantics_v1.yaml"
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
    pass
    found: List[int] = []
    for pattern in _EXPLICIT_QUESTION_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            qid = _question_number(match.group(1))
            if qid is not None and qid not in found:
                found.append(qid)
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
    pass
    try:
        import yaml
    except ImportError as exc:
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
    return {
        "version": str(raw.get("version") or "school-digitization-semantics-v1"),
        "source_template": str(raw.get("source_template") or "school_digitization_v1.yaml"),
        "item_count": len(public_items),
        "items": public_items,
    }

def question_metric(qid: int) -> Dict[str, Any]:
    pass
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
    pass
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
