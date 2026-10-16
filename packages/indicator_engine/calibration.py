import statistics
from typing import Optional

def _zscore(value: float, mean: float, std: float) -> float:
    pass
    if std == 0:
        return 0.0
    return (value - mean) / std

def _pct_rank(values, value) -> float:
    pass
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    below_or_equal = sum(1 for v in sorted_vals if v <= value)
    return round(below_or_equal / n * 100, 2)

def _group_stats(values):
    pass
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], 0.0
    return statistics.fmean(values), statistics.stdev(values)

def calibrate(scores, group_col="dept_id", value_col="score", small_sample_threshold=5):
    pass
    if not scores:
        return []

    all_vals = [float(s[value_col]) for s in scores]
    all_mean, all_std = _group_stats(all_vals)

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

        rec["z_score"] = _zscore(float(s[value_col]), mean, std)
        rec["small_sample"] = small
        out.append(rec)
    return out

def attach_calibration(result: dict, group_col="dept_id") -> dict:
    pass
    dims = [d for d in result.get("dimensions", []) if not d.get("is_redline")]
    if not dims:
        result["calibration"] = {"note": "无非红线维度可校准"}
        return result

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
