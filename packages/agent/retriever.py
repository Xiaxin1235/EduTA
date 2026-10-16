import json
import os
from typing import Dict, List, Optional

_CACHE: Dict[str, dict] = {}
CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "..", "data", "demo", "output", "retriever_cache.json")

def _load_cache():
    global _CACHE
    if _CACHE:
        return _CACHE
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            _CACHE = json.load(f)
    except Exception:
        _CACHE = {}
    return _CACHE

def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _cache_key(metric: str, teacher_id: str, years: tuple) -> str:
    return f"{metric}:{teacher_id}:{','.join(years)}"

def retrieve(task: dict, tables: dict, metric_funcs: dict, teacher_id: str,
             years: list, use_cache: bool = True) -> List[dict]:
    pass
    if use_cache:
        cached = _load_cache().get(_cache_key("dim", teacher_id, tuple(years)))
        if cached is not None:
            return cached.get("evidence", [])

    evidence = []
    dim_key = task.get("dimension")
    if task.get("is_redline"):

        if "redline_check" in metric_funcs:
            r = metric_funcs["redline_check"](
                {"teacher_id": teacher_id}, years, tables)
            evidence = r.get("evidence", [])
    else:

        for mkey, fn in metric_funcs.items():
            r = fn({"teacher_id": teacher_id}, years, tables)
            if r.get("value") is not None:
                evidence.extend(r.get("evidence", []))

    if use_cache:
        _load_cache()
        _CACHE[_cache_key("dim", teacher_id, tuple(years))] = {"evidence": evidence}
        _save_cache(_CACHE)

    return evidence

def evidence_has_values(evidence: List[dict]) -> bool:
    return any(e.get("raw") is not None for e in evidence)
