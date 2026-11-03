"""M07 · Retriever：只负责从契约表/指标引擎取证据。

硬规则：Evidence 为空的任务，下游不得编造数值，只能输出 gap。
实现：优先查指标仓库缓存（原型用内存 dict + JSON 文件兜底），未命中走 M02 引擎计算。
"""

import json
import os
from typing import Dict, List, Optional

# 指标仓库缓存（原型：内存 + 磁盘文件兜底）
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
        pass  # 缓存写入失败不应阻塞评估


def _cache_key(metric: str, teacher_id: str, years: tuple) -> str:
    return f"{metric}:{teacher_id}:{','.join(years)}"


def retrieve(task: dict, tables: dict, metric_funcs: dict, teacher_id: str,
             years: list, use_cache: bool = True) -> List[dict]:
    """按任务拿证据块。task 须含 dimension / is_redline / metric key。

    返回 list[dict]：每个证据 `{metric, raw, source, period, formula}`（与 M02 一致）。
    无数据时返回 []（调用方需输出 gap，不得编造）。
    """
    if use_cache:
        cached = _load_cache().get(_cache_key("dim", teacher_id, tuple(years)))
        if cached is not None:
            return cached.get("evidence", [])

    # 当前原型一张任务 = 一个维度（M02 引擎的 evaluate_teacher 一次算全维度），
    # 这里按维度从 M02 的评估结果里抽取证据；metric_funcs 直接调用同一套语义。
    evidence = []
    dim_key = task.get("dimension")
    if task.get("is_redline"):
        # 红线维度独立取
        if "redline_check" in metric_funcs:
            r = metric_funcs["redline_check"](
                {"teacher_id": teacher_id}, years, tables)
            evidence = r.get("evidence", [])
    else:
        # 用 M02 已算好的维度证据：这里简化——按 metric 逐个调用
        # （正式版应查指标仓库/预计算结果；原型直接复算保证可跑）
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