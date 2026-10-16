from typing import Any, Dict

def school_fact_metric_input(routed: Dict[str, Any]) -> Dict[str, Any]:
    pass
    metric = dict(routed.get("metric_set") or routed.get("metric") or {})
    value_view = routed.get("value_view")
    if value_view:
        metric["value_view"] = value_view
    return metric
