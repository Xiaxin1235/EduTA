"""Experiment adapters over the shared semantic-query runtime.

G1/G2 keep profile binding, outbound-stage audit, and treatment identity here.
Catalogue loading, TaskDraft resolution, AnswerSpec, evidence projection,
writer prompts, and verification live in ``packages.semantic_query.runtime``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from packages.experiment_protocol.mode import bound_profile_id, record_llm_stage
from packages.semantic_query.runtime import (
    COMMON_PATH_VERSION,
    PARSER_PROMPT_VERSION,
    SEMANTIC_CATALOGUE_VERSION,
    WRITER_PROMPT_VERSION,
    build_answer_spec,
    common_evidence_packet,
    generate_semantic_answer,
    parse_semantic_route,
    verify_answer_text,
)


def parse_llm_route(
    question: str,
    context: Optional[Dict[str, Any]],
    experiment_status: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Parse one G1/G2 request through the shared semantic implementation."""
    mode = str(experiment_status.get("active_mode") or "")
    if mode not in {"g1_llm_unconstrained", "g2_llm_constrained", "a1_no_execution_verification", "a2_no_output_verification_or_repair", "a3_no_answer_repair"}:
        raise ValueError("llm_experiment_mode_required")
    import os
    constraint_mode = os.environ.get('TE_SEMANTIC_PREFLIGHT', 'none') if mode == 'g2_llm_constrained' else 'none'
    from packages.semantic_query.semantic_preflight import model_checker_research_enabled
    if constraint_mode == 'code_model' and not model_checker_research_enabled():
        constraint_mode = 'code'
    checker = repair = None
    if constraint_mode == 'code_model':
        from packages.semantic_query.model_checker.gateway_adapter import make_gateway_model_call
        from packages.semantic_query.semantic_repair_gateway import make_repair_callback
        checker = make_gateway_model_call(expected_profile_id=bound_profile_id())
        repair = make_repair_callback(expected_profile_id=bound_profile_id())
    return parse_semantic_route(
        question,
        context,
        mode=mode,
        expected_profile_id=bound_profile_id(),
        record_stage=record_llm_stage,
        semantic_constraint_mode=constraint_mode,
        semantic_check_callback=checker, semantic_repair_callback=repair,
    )


def generate_llm_answer(
    question: str,
    result: Dict[str, Any],
    answer_spec: Dict[str, Any],
    experiment_status: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate one G1/G2 answer while preserving experiment controls."""
    mode = str(experiment_status.get("active_mode") or "")
    if mode not in {"g1_llm_unconstrained", "g2_llm_constrained", "a1_no_execution_verification", "a2_no_output_verification_or_repair", "a3_no_answer_repair"}:
        raise ValueError("llm_experiment_mode_required")
    return generate_semantic_answer(
        question,
        result,
        answer_spec,
        mode=mode,
        expected_profile_id=bound_profile_id(),
        record_stage=record_llm_stage,
        mechanisms=experiment_status.get("mechanisms"),
    )


__all__ = [
    "COMMON_PATH_VERSION",
    "PARSER_PROMPT_VERSION",
    "WRITER_PROMPT_VERSION",
    "SEMANTIC_CATALOGUE_VERSION",
    "parse_llm_route",
    "build_answer_spec",
    "common_evidence_packet",
    "verify_answer_text",
    "generate_llm_answer",
]
