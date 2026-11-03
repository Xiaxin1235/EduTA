"""Run the project's own school evaluation engine on sample schools to see what the DB yields."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, r"D:\teacher evaluation")
from scripts import school_engine as se  # noqa: E402

for sid in (1, 11, 100, 200000):
    res = se.evaluate_school(sid)
    print("=" * 70)
    print("school_id", sid, "->", res.get("school_name"), "| year", res.get("school_years"))
    print("  total_score      :", res.get("total_score"))
    print("  data_coverage    :", res.get("data_coverage"))
    print("  overall_conf     :", res.get("overall_confidence"))
    print("  answered/total   :", res.get("answered"), "/", res.get("total_metrics"))
    for d in res.get("dimensions", []):
        print(f"    {str(d['dimension']):<12} score={d['score']} "
              f"conf={d['confidence']} weight={d['weight']} gaps={len(d['gap'])}")
