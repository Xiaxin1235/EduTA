"""Fail-closed, process-local controls for P4 experiment method identities.

G0 is the frozen deterministic baseline. All model modes share one real LLM
parser/catalogue/tool/writer path. A1–A3 vary only the declared G2 controls.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict


_LOCK = threading.Lock()
_COUNTERS: Counter[str] = Counter()
_ENTRY_POINTS: Counter[str] = Counter()
_PROFILE_BINDINGS: Dict[str, str] = {}
_REQUIREMENT_PRESERVATION = ContextVar("requirement_preservation", default=None)

_SUPPORTED_MODES = {
    "g0_rule",
    "g1_llm_unconstrained",
    "g2_llm_constrained",
    "a1_no_execution_verification",
    "a2_no_output_verification_or_repair",
    "a3_no_answer_repair",
}
_ABLATION_MODES = {
    "a1_no_execution_verification",
    "a2_no_output_verification_or_repair",
    "a3_no_answer_repair",
}
_LLM_MODES = {"g1_llm_unconstrained", "g2_llm_constrained"} | _ABLATION_MODES


def mechanism_flags(mode: str) -> Dict[str, bool | str]:
    """Predeclared treatment matrix; never derive ablations from the G1 switch."""
    g2_family = mode == "g2_llm_constrained" or mode in _ABLATION_MODES
    flags = {
        "g2_first_prompt": g2_family,
        "original_record_coverage": g2_family and mode != "a1_no_execution_verification",
        "required_fact_verification": g2_family and mode != "a2_no_output_verification_or_repair",
        "execution_verification": g2_family and mode != "a1_no_execution_verification",
        "output_verification": g2_family and mode != "a2_no_output_verification_or_repair",
        "answer_repair": mode in {"g2_llm_constrained", "a1_no_execution_verification"},
        "delivery_policy": "block",
        "requirement_preservation": True,
        "catalogue_annotation": True,
    }
    if mode == "g1_llm_unconstrained":
        setting = os.environ.get("TE_ABLATION_REQUIREMENT_PRESERVATION", "1")
        if setting not in {"0", "1"}:
            raise ValueError("requirement_preservation_control_must_be_0_or_1")
        flags["requirement_preservation"] = setting == "1"
        setting = os.environ.get("TE_ABLATION_CATALOGUE_ANNOTATION", "1")
        if setting not in {"0", "1"}:
            raise ValueError("catalogue_annotation_control_must_be_0_or_1")
        flags["catalogue_annotation"] = setting == "1"
    # This experiment override changes only the existing answer-repair gate.
    # Keep the G2 identity, first writer, checks and publication rules intact.
    if mode == "g2_llm_constrained":
        setting = os.environ.get("TE_ABLATION_ANSWER_REPAIR", "1")
        if setting not in {"0", "1"}:
            raise ValueError("answer_repair_control_must_be_0_or_1")
        flags["answer_repair"] = setting == "1"
        delivery_policy = os.environ.get("TE_DELIVERY_POLICY", "block")
        if delivery_policy not in {"block", "warn"}:
            raise ValueError("delivery_policy_must_be_block_or_warn")
        flags["delivery_policy"] = delivery_policy
        if delivery_policy == "warn":
            flags["answer_repair"] = False
    return flags


# The diagnostic ping is allowed only to verify the bound profile and outbound
# path. It is not a product/parser/writer stage and never produces an answer.
_ALLOWED_LLM_TASKS = {
    "experiment_diagnostic",
    "experiment_parser",
    "experiment_writer",
    "experiment_repair",
}


def _text(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _run_id_hash(value: str) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def experiment_mode_status() -> Dict[str, Any]:
    requested = _text("TEACHER_EVAL_EXPERIMENT_MODE").lower()
    explicit_mode = _text("TEACHER_EVAL_EXPERIMENT_MODE_ENABLED") == "1"
    run_id = _text("TEACHER_EVAL_EXPERIMENT_RUN_ID")
    profile_id = _text("TEACHER_EVAL_EXPERIMENT_PROFILE_ID")
    trace_dir = _text("TEACHER_EVAL_EXPERIMENT_TRACE_DIR")
    data_override = bool(_text("TEACHER_EVAL_DATA_DB"))
    contract_override = bool(_text("TEACHER_EVAL_CONTRACT_DB"))
    repair_control_valid = (
        requested != "g2_llm_constrained"
        or os.environ.get("TE_ABLATION_ANSWER_REPAIR", "1") in {"0", "1"}
    )
    delivery_policy_valid = (
        requested != "g2_llm_constrained"
        or os.environ.get("TE_DELIVERY_POLICY", "block") in {"block", "warn"}
    )
    requirement_preservation_valid = (
        requested != "g1_llm_unconstrained"
        or os.environ.get("TE_ABLATION_REQUIREMENT_PRESERVATION", "1") in {"0", "1"}
    )
    catalogue_annotation_valid = (
        requested != "g1_llm_unconstrained"
        or os.environ.get("TE_ABLATION_CATALOGUE_ANNOTATION", "1") in {"0", "1"}
    )
    supported = requested in ({""} | _SUPPORTED_MODES)
    enabled = bool(
        requested in _SUPPORTED_MODES
        and explicit_mode
        and run_id
        and data_override
        and contract_override
        and (requested not in _LLM_MODES or profile_id)
        and (requested not in _ABLATION_MODES or (os.path.isabs(trace_dir) and os.path.isdir(trace_dir)))
        and repair_control_valid
        and delivery_policy_valid
        and requirement_preservation_valid
        and catalogue_annotation_valid
    )
    if not requested:
        reason = "not_configured"
    elif requested not in _SUPPORTED_MODES:
        reason = "experiment_mode_not_implemented"
    elif not explicit_mode:
        reason = "explicit_mode_required"
    elif not run_id:
        reason = "run_id_required"
    elif not data_override or not contract_override:
        reason = "isolated_data_and_contract_overrides_required"
    elif requested in _LLM_MODES and not profile_id:
        reason = "bound_llm_profile_required"
    elif not repair_control_valid:
        reason = "answer_repair_control_must_be_0_or_1"
    elif not delivery_policy_valid:
        reason = "delivery_policy_must_be_block_or_warn"
    elif not requirement_preservation_valid:
        reason = "requirement_preservation_control_must_be_0_or_1"
    elif not catalogue_annotation_valid:
        reason = "catalogue_annotation_control_must_be_0_or_1"
    elif requested in _ABLATION_MODES and not (os.path.isabs(trace_dir) and os.path.isdir(trace_dir)):
        reason = "isolated_trace_directory_required"
    else:
        reason = None
    with _LOCK:
        counters = dict(_COUNTERS)
        entry_points = dict(_ENTRY_POINTS)
        profile_binding_locked = bool(
            _PROFILE_BINDINGS.get(f"{_run_id_hash(run_id)}:{requested}")
        )
    return {
        "configured": bool(requested),
        "enabled": enabled,
        "requested_mode": requested or None,
        "active_mode": requested if enabled else None,
        "reason": reason,
        "supported": supported,
        "default_enabled": False,
        "execution_protocol": "g012-original100-first-attempt",
        "parser_repair_allowed": False,
        "per_stage_max_attempts": 1,
        "per_stage_transport_attempts": 1,
        "requires_explicit_mode": True,
        "requires_run_id": True,
        "requires_data_override": True,
        "requires_contract_override": True,
        "requires_trace_directory_for_ablation": True,
        "run_id_hash": _run_id_hash(run_id),
        "profile_id_hash": _run_id_hash(profile_id),
        "profile_binding_locked": profile_binding_locked,
        "method_identity": (
            {
                "g0_rule": "frozen_rule_router_deterministic_tools_rule_text",
                "g1_llm_unconstrained": "shared_llm_catalogue_router_tools_unconstrained_delivery",
                "g2_llm_constrained": "shared_llm_catalogue_router_tools_contract_replay_answer_scope",
                "a1_no_execution_verification": "g2_path_without_binding_and_numeric_replay",
                "a2_no_output_verification_or_repair": "g2_path_without_output_checks_and_answer_repair",
                "a3_no_answer_repair": "g2_path_without_answer_repair",
            }.get(requested)
            if enabled
            else None
        ),
        # Normal product operation remains unchanged. G0 and incomplete/unknown
        # experiment identities fail closed; only complete G1/G2 identities may
        # use the bound model profile.
        "llm_calls_allowed": bool(not requested or (enabled and requested in _LLM_MODES)),
        "allowed_llm_tasks": sorted(_ALLOWED_LLM_TASKS | ({"semantic_check", "semantic_repair"} if requested == "g2_llm_constrained" and _text("TE_SEMANTIC_PREFLIGHT") == "code_model" else set())) if enabled and requested in _LLM_MODES else [],
        "proposed_constraints_enabled": bool(enabled and requested != "g1_llm_unconstrained" and requested in _LLM_MODES),
        "mechanisms": mechanism_flags(requested) if enabled and requested in _LLM_MODES else None,
        "constraint_profile": (
            "product_ablation_" + requested if enabled and requested in _ABLATION_MODES
            else "full_contract_replay_answer_scope" if enabled and requested == "g2_llm_constrained"
            else "shared_tools_without_proposed_constraints" if enabled and requested == "g1_llm_unconstrained"
            else "frozen_rule_existing_checks" if enabled and requested == "g0_rule"
            else None
        ),
        "llm_outbound_calls": int(counters.get("llm_outbound_calls", 0)),
        "llm_blocked_attempts": int(counters.get("llm_blocked_attempts", 0)),
        "rule_outputs": int(counters.get("rule_outputs", 0)),
        "llm_parser_outputs": int(counters.get("llm_parser_outputs", 0)),
        "llm_writer_outputs": int(counters.get("llm_writer_outputs", 0)),
        "llm_repair_outputs": int(counters.get("llm_repair_outputs", 0)),
        "llm_semantic_check_outputs": int(counters.get("llm_semantic_check_outputs", 0)),
        "llm_semantic_repair_outputs": int(counters.get("llm_semantic_repair_outputs", 0)),
        "semantic_preflight_mode": _text("TE_SEMANTIC_PREFLIGHT") or "none",
        "entry_points": entry_points,
        "paths_redacted": True,
        "g0_rule": "validated_baseline",
        "g1_llm_unconstrained": "implemented_unverified_candidate",
        "g2_llm_constrained": "implemented_unverified_candidate",
    }


def requirement_preservation_enabled() -> bool:
    """Effective G1 treatment, including calls outside the parser entry point."""
    scoped = _REQUIREMENT_PRESERVATION.get()
    if scoped is not None:
        return scoped
    status = experiment_mode_status()
    return not (
        status.get("active_mode") == "g1_llm_unconstrained"
        and not status["mechanisms"]["requirement_preservation"]
    )


@contextmanager
def requirement_preservation_scope(enabled: bool):
    """Request-local compiler setting; also used for observation on copies."""
    token = _REQUIREMENT_PRESERVATION.set(enabled)
    try:
        yield
    finally:
        _REQUIREMENT_PRESERVATION.reset(token)


def g0_rule_enabled() -> bool:
    status = experiment_mode_status()
    return bool(status.get("enabled") and status.get("active_mode") == "g0_rule")


def llm_experiment_enabled() -> bool:
    status = experiment_mode_status()
    return bool(status.get("enabled") and status.get("active_mode") in _LLM_MODES)


def proposed_constraints_enabled() -> bool:
    status = experiment_mode_status()
    return bool(status.get("enabled") and status.get("proposed_constraints_enabled"))


def bound_profile_id() -> str:
    """Internal-only profile binding; never include the raw value in public status."""
    return _text("TEACHER_EVAL_EXPERIMENT_PROFILE_ID")


def bind_experiment_profile_fingerprint(fingerprint: str) -> bool:
    """Lock one run to the first complete profile snapshot without exposing it."""
    status = experiment_mode_status()
    if not status.get("enabled") or status.get("active_mode") not in _LLM_MODES:
        return True
    value = str(fingerprint or "").strip()
    if not value:
        return False
    key = f"{status.get('run_id_hash')}:{status.get('active_mode')}"
    with _LOCK:
        bound = _PROFILE_BINDINGS.get(key)
        if bound is None:
            _PROFILE_BINDINGS[key] = value
            return True
        return bound == value


def experiment_blocks_llm_outbound() -> bool:
    status = experiment_mode_status()
    return bool(status.get("configured") and not status.get("llm_calls_allowed"))


def experiment_allows_llm_entry_point(entry_point: str) -> bool:
    """Allow only the registered common-path stages in an active G1/G2 run."""
    status = experiment_mode_status()
    if not status.get("configured"):
        return True
    if not status.get("enabled") or status.get("active_mode") not in _LLM_MODES:
        return False
    task = str(entry_point or "").strip().rsplit(":", 1)[-1]
    if task in {'semantic_check', 'semantic_repair'}:
        return status.get('active_mode') == 'g2_llm_constrained' and _text('TE_SEMANTIC_PREFLIGHT') == 'code_model'
    return task in _ALLOWED_LLM_TASKS


def _increment(counter: str, entry_point: str) -> None:
    safe_entry = str(entry_point or "unknown").strip() or "unknown"
    with _LOCK:
        _COUNTERS[counter] += 1
        _ENTRY_POINTS[f"{counter}:{safe_entry}"] += 1


def record_llm_blocked(entry_point: str) -> None:
    _increment("llm_blocked_attempts", entry_point)


def record_llm_outbound(entry_point: str) -> None:
    _increment("llm_outbound_calls", entry_point)


def record_rule_output(entry_point: str) -> None:
    _increment("rule_outputs", entry_point)


def record_llm_stage(stage: str, entry_point: str) -> None:
    safe_stage = str(stage or "unknown").strip().lower()
    if safe_stage not in {"parser", "writer", "repair", "semantic_check", "semantic_repair"}:
        raise ValueError("unsupported_experiment_llm_stage")
    _increment(f"llm_{safe_stage}_outputs", entry_point)
