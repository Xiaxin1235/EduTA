"""考核性 R1 · 评分校准模块。

把不同部门（教研组）的原始分统一到可横向比较的尺度：
  原始分 + 同组百分位排名（pct_rank）+ 组内 z-score。

小样本规则：组内样本 < 5 时，z-score 回退到全校（跨学科合并）口径，
并显著标注 small_sample=True，避免单个小部门的 z-score 失真。
"""

import statistics
from typing import Optional


def _zscore(value: float, mean: float, std: float) -> float:
    """单样本标准化 z 值。组内标准差为 0 时返回 0（所有样本相同）。"""
    if std == 0:
        return 0.0
    return (value - mean) / std


def _pct_rank(values, value) -> float:
    """组内百分位排名（0~100）。取"≤ value 的比例"再 ×100，第一名=100。"""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    below_or_equal = sum(1 for v in sorted_vals if v <= value)
    return round(below_or_equal / n * 100, 2)


def _group_stats(values):
    """返回 (mean, std)；单样本组 std 人为置 0，不崩溃。"""
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], 0.0
    return statistics.fmean(values), statistics.stdev(values)


def calibrate(scores, group_col="dept_id", value_col="score", small_sample_threshold=5):
    """对一组评分记录做同组校准。

    参数
    ----
    scores: list[dict] —— 每项含 group_col 与 value_col 两个键。
    group_col: 分桶键（原型用 dept_id 模拟学科组）。
    value_col: 待校准的数值键。
    small_sample_threshold: 组内样本小于该阈值时 z-score 回退全校口径。

    返回
    ----
    list[dict] —— 每条记录新增：
      pct_rank    同组百分位排名（0~100）
      z_score     组内 z-score（小样本回退全校口径）
      small_sample bool
    """
    if not scores:
        return []

    # 全校口径兜底
    all_vals = [float(s[value_col]) for s in scores]
    all_mean, all_std = _group_stats(all_vals)

    # 按组分桶
    groups = {}
    for s in scores:
        groups.setdefault(s[group_col], []).append(s)

    out = []
    for s in scores:
        g = groups[s[group_col]]
        vals = [float(x[value_col]) for x in g]
        small = len(vals) < small_sample_threshold
        if small:
            mean, std = all_mean, all_std
        else:
            mean, std = _group_stats(vals)
        rec = dict(s)
        rec["pct_rank"] = _pct_rank(vals, float(s[value_col]))
        # z-score 保留全精度（中间计算值，显示层再格式化）
        rec["z_score"] = _zscore(float(s[value_col]), mean, std)
        rec["small_sample"] = small
        out.append(rec)
    return out


def attach_calibration(result: dict, group_col="dept_id") -> dict:
    """把校准信息附加到评估报告的 calibration 区块。

    输入评估结果需含 dimensions 列表（M02 结构），本函数为
    dimension 记录附上 per_dimension 校准；当结果只有单教师时，
    校准基于本次报告内的维度原始分（原型语义，生产版按全校教师
    批量校准，见 SPEC 的分桶规则）。
    """
    dims = [d for d in result.get("dimensions", []) if not d.get("is_redline")]
    if not dims:
        result["calibration"] = {"note": "无非红线维度可校准"}
        return result
    # 分组标签用报告的实际部门（teacher_dept），回退到 group_col
    dept = result.get("teacher_dept") or group_col
    rows = [
        {"dept_id": dept, "score": d["score"]}
        for d in dims if d.get("score") is not None
    ]
    cal = calibrate(rows, group_col="dept_id", value_col="score")
    by_score = {}
    for r in cal:
        by_score.setdefault(round(r["score"], 6), []).append(r)
    result["calibration"] = {"per_dimension": cal, "group_col": dept}
    return result