"""Resolve the ordinary product's explicit query-recognition mode."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


SEMANTIC_MODE = "semantic"
RULE_MODE = "rule"
VALID_MODES = {SEMANTIC_MODE, RULE_MODE}


def resolve_query_mode(requested: Optional[str] = None) -> Dict[str, Any]:
    """Resolve one product request without borrowing experiment switches."""
    requested_value = str(requested or "").strip().lower()
    configured_default = str(
        os.environ.get("TEACHER_EVAL_QUERY_MODE") or SEMANTIC_MODE
    ).strip().lower()
    if configured_default not in VALID_MODES:
        configured_default = SEMANTIC_MODE
    active = requested_value or configured_default
    if active not in VALID_MODES:
        raise ValueError("query_mode_must_be_semantic_or_rule")
    return {
        "active_mode": active,
        "default_mode": configured_default,
        "source": "request" if requested_value else "environment_or_default",
        "semantic_enabled": active == SEMANTIC_MODE,
        "rule_baseline_selected": active == RULE_MODE,
        "automatic_rule_fallback": False,
    }

