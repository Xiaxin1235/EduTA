"""Prepare, commit, bind, and independently verify supported analysis tasks."""

from __future__ import annotations

import copy
import hashlib
import math
import json
import os
import re
import sqlite3
import statistics
import time
import uuid
from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from scripts.region_engine import detect_region
from packages.semantic_query.tool_adapter import school_fact_metric_input
from packages.semantic_query.typed_plan import region_identity as _typed_region_identity
from scripts.school_engine import (
    _db_path,
    connect,
    expand_school_alias,
    find_schools,
    get_school,
    load_school_template,
)

from .capabilities import (
    CATALOGUE_VERSION,
    REGION_RANKING_SPEC,
    RESOLVER_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_REGION_COMPARE_STATISTICS,
    SUPPORTED_REGION_LONGITUDINAL_STATISTICS,
    SUPPORTED_REGION_RANKING_STATISTICS,
    SUPPORTED_SCHOOL_COMPARE_AGGREGATIONS,
    SUPPORTED_SCHOOL_COMPARE_STATISTICS,
    CapabilityError,
    filter_is_structured,
    validate_region_request,
)
from .schema import AnalysisContract, ExecutionReceipt, FieldSource
from .state import ContractStore, StaleBaseRevision


_STATISTIC_EVIDENCE = {
    "mean": ("均分", "平均分", "平均得分", "平均值", "平均数", "均值"),
    "median": ("中位数", "中位值"),
    "mean_and_median": ("均值与中位数", "均值和中位数", "均分与中位数", "平均值与中位数"),
    "count": ("学校数量", "学校总数", "多少所学校", "学校数", "多少学校", "多少个学校",
              "多少间学校", "几所学校", "几个学校", "几间学校", "有多少学校", "有多少所",
              "共有多少所", "共多少所", "各有多少所", "各有多少学校", "多少所"),
    "max": ("最高分", "最高得分", "分数最高"),
    "min": ("最低分", "最低得分", "分数最低"),
}
_AGGREGATION_EVIDENCE = {
    "school_equal_mean": ("学校等权", "每校等权", "按学校等权", "先按学校汇总", "各校均分再平均"),
    "answer_pooled_mean": ("作答池化", "池化平均", "合在一起平均", "所有作答合并平均", "不按学校汇总"),
}


def _requested_outputs(
    routed: Dict[str, Any],
    *,
    structured_key: str,
    default_charts: bool,
) -> Tuple[str, ...]:
    """Bind an experiment AnswerSpec to the contract without changing normal defaults."""
    selected = routed.get("requested_outputs")
    if not isinstance(selected, (list, tuple)) or not selected:
        values = ["direct_answer", structured_key]
        if default_charts:
            values.append("charts")
        return tuple(values)
    values = ["direct_answer", structured_key]
    for item in selected:
        value = str(item or "").strip()
        if value in {"analysis", "table", "charts", "dimension_scores", "catalogue_question_count"} and value not in values:
            values.append(value)
    for value in routed.get("requested_scope_outputs") or []:
        if value in {"dimension_scores", "catalogue_question_count"} and value not in values:
            values.append(value)
    return tuple(values)


def _semantic_evidence(routed: Dict[str, Any], field: str) -> Tuple[str, ...]:
    evidence = routed.get("semantic_evidence")
    if not isinstance(evidence, dict):
        return ()
    value = str(evidence.get(field) or "").strip()
    return (value,) if value else ()


class ContractViolation(RuntimeError):
    def __init__(self, failures: Iterable[str], *, test_control: Optional[Dict[str, Any]] = None):
        self.failures = tuple(sorted(set(failures)))
        self.test_control = test_control
        super().__init__("contract_verification_failed:" + ",".join(self.failures))


class ControlledTestFailure(RuntimeError):
    def __init__(self, message: str, *, stage: str):
        self.test_control = {
            "enabled": True,
            "case": stage,
            "synthetic_fault": True,
            "product_evidence_scope": "failure_cleanup_only",
        }
        super().__init__(message)


@dataclass
class ConsistentReadSnapshot:
    connection: sqlite3.Connection
    snapshot_id: str
    dataset_version: str = ""
    sqlite_data_version: int = 0
    consistency_mode: str = "sqlite_read_transaction_v1"
    connection_token: str = field(default_factory=lambda: str(uuid.uuid4()))
    phase: str = "acquiring_snapshot"
    trace_events: list[Dict[str, Any]] = field(default_factory=list)
    _sequence: int = 0

    def mark(self, stage: str, **details: Any) -> None:
        self._sequence += 1
        event = {
            "seq": self._sequence,
            "stage": stage,
            "phase": self.phase,
            "connection_token": self.connection_token,
            "transaction_active": bool(self.connection.in_transaction),
        }
        event.update(details)
        self.trace_events.append(event)

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.mark("phase_started")

    def capture_sql(self, statement: str) -> None:
        normalized = " ".join(str(statement or "").upper().split())
        role = None
        if normalized.startswith("BEGIN"):
            role = "transaction_begin"
        elif "SQLITE_SCHEMA" in normalized:
            role = "snapshot_probe"
        elif self.phase in {
            "region_execution", "region_compare_execution", "region_longitudinal_execution",
            "region_ranking_execution",
        } and normalized.startswith("SELECT"):
            role = "region_sql_aggregation" if "AVG(" in normalized and "GROUP BY" in normalized else "region_sql_read"
        elif self.phase == "school_compare_execution" and normalized.startswith("SELECT"):
            role = "school_compare_sql_read"
        elif self.phase == "school_fact_execution" and normalized.startswith("SELECT"):
            role = "school_fact_sql_read"
        elif self.phase == "independent_replay" and normalized.startswith("SELECT S.ID,A.NORMALIZED_VALUE"):
            role = "python_replay_raw_rows"
        elif self.phase == "independent_replay" and normalized.startswith("SELECT"):
            role = "independent_replay_raw_rows"
        if role:
            self.mark("sql", sql_role=role)


def _resolved_data_path(data_path: Optional[str] = None) -> Path:
    return Path(data_path or _db_path()).expanduser().resolve()


def _data_source_id(data_path: str) -> str:
    """Return a non-reversible path identity for isolated-test diagnostics."""
    normalized = os.path.normcase(str(_resolved_data_path(data_path)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _dataset_version(data_path: Optional[str] = None) -> str:
    path = _resolved_data_path(data_path)
    stat = path.stat()
    wal_path = Path(f"{path}-wal")
    if wal_path.is_file():
        wal_stat = wal_path.stat()
        wal_part = f"wal:{wal_stat.st_size}:{wal_stat.st_mtime_ns}"
    else:
        wal_part = "wal:none"
    version = f"{path.name}:main:{stat.st_size}:{stat.st_mtime_ns}:{wal_part}"
    from packages.core.item_collection import enabled as r11_enabled, VERSION, rules
    return version+':'+VERSION+':'+rules()['version'] if r11_enabled() else version


def _test_gate(
    stage: str,
    snapshot: ConsistentReadSnapshot,
    *,
    expected_dataset_version: Optional[str] = None,
    data_path: Optional[str] = None,
) -> None:
    control_value = str(os.environ.get("TEACHER_EVAL_CONSISTENT_READ_TEST_CONTROL_DIR") or "").strip()
    failure_stage = str(os.environ.get("TEACHER_EVAL_CONSISTENT_READ_TEST_FAIL_STAGE") or "").strip()
    data_override = str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip()
    contract_override = str(os.environ.get("TEACHER_EVAL_CONTRACT_DB") or "").strip()
    if not control_value and not failure_stage:
        return
    if not data_override:
        raise RuntimeError("consistent_read_test_gate_requires_data_override")
    if failure_stage:
        if os.environ.get("TEACHER_EVAL_CONSISTENT_READ_TEST_MODE", "").strip() != "1":
            raise RuntimeError("consistent_read_test_failure_requires_explicit_mode")
        if not contract_override:
            raise RuntimeError("consistent_read_test_failure_requires_contract_override")
        allowed_failure_stages = {"before_school_fact_execution", "before_replay"}
        if failure_stage not in allowed_failure_stages:
            raise RuntimeError(f"unsupported_consistent_read_test_fail_stage:{failure_stage}")
        applies_here = failure_stage == stage and (
            stage != "before_replay"
            or snapshot.phase in {"school_fact_execution", "region_longitudinal_execution"}
        )
        if applies_here:
            snapshot.mark("test_fault_injected", gate=stage)
            raise ControlledTestFailure(
                f"controlled_consistent_read_test_failure:{stage}",
                stage=stage,
            )
    if not control_value:
        return
    control_dir = Path(control_value).expanduser().resolve()
    if not control_dir.is_dir():
        raise RuntimeError(f"consistent_read_test_control_dir_unavailable:{control_dir}")
    try:
        timeout_seconds = float(os.environ.get("TEACHER_EVAL_CONSISTENT_READ_TEST_TIMEOUT_SECONDS") or "30")
    except ValueError:
        timeout_seconds = 30.0
    timeout_seconds = min(max(timeout_seconds, 1.0), 120.0)
    prefix = f"{snapshot.snapshot_id}.{stage}"
    ready_path = control_dir / f"{prefix}.ready.json"
    continue_path = control_dir / f"{prefix}.continue"
    safe_source_diagnostics = {}
    if data_path:
        safe_source_diagnostics = {
            "data_source_file": _resolved_data_path(data_path).name,
            "data_source_id": _data_source_id(data_path),
            "expected_dataset_version": expected_dataset_version,
            "observed_dataset_version_at_gate": _dataset_version(data_path),
            "paths_redacted": True,
        }
    ready_path.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot.snapshot_id,
                "stage": stage,
                "connection_token": snapshot.connection_token,
                "transaction_active": bool(snapshot.connection.in_transaction),
                **safe_source_diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    snapshot.mark("test_gate_waiting", gate=stage)
    deadline = time.monotonic() + timeout_seconds
    while not continue_path.is_file():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"consistent_read_test_gate_timeout:{stage}")
        time.sleep(0.05)
    snapshot.mark("test_gate_released", gate=stage)


_SCHOOL_FACT_TAMPER_CASES = {
    "contract_school_id", "contract_school_name", "contract_period",
    "contract_metric_type", "contract_metric_key", "contract_metric_spec", "contract_indicator_ids",
    "result_school_id", "result_school_name", "result_period", "result_metric_type", "result_metric_name",
    "result_scope_school_id", "result_scope_period", "result_scope_metric_key", "result_scope_indicator_ids",
    "result_target_value", "result_component_total", "result_component_answer_count",
    "result_component_complete", "result_ratio", "result_fact_data_status",
}


def school_fact_test_control_status(*, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Describe the strictly gated in-memory school-fact tamper control."""
    case = str(os.environ.get("TEACHER_EVAL_SCHOOL_FACT_TAMPER_CASE") or "").strip()
    target_request_id = str(
        os.environ.get("TEACHER_EVAL_SCHOOL_FACT_TAMPER_REQUEST_ID") or ""
    ).strip()
    explicit_mode = os.environ.get("TEACHER_EVAL_SCHOOL_FACT_TAMPER_TEST_MODE", "").strip() == "1"
    data_override = bool(str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip())
    contract_override = bool(str(os.environ.get("TEACHER_EVAL_CONTRACT_DB") or "").strip())
    case_supported = case in _SCHOOL_FACT_TAMPER_CASES if case else False
    enabled = bool(case and explicit_mode and data_override and contract_override and case_supported)
    return {
        "configured": bool(case),
        "enabled": enabled,
        "requested_case": case if case else None,
        "case": case if enabled else None,
        "default_enabled": False,
        "requires_explicit_mode": True,
        "requires_data_override": True,
        "requires_contract_override": True,
        "case_supported": case_supported,
        "request_targeted": bool(target_request_id),
        "request_match": (
            None
            if request_id is None
            else bool(not target_request_id or request_id == target_request_id)
        ),
    }


def consistent_read_test_control_status() -> Dict[str, Any]:
    failure_stage = str(os.environ.get("TEACHER_EVAL_CONSISTENT_READ_TEST_FAIL_STAGE") or "").strip()
    explicit_mode = os.environ.get("TEACHER_EVAL_CONSISTENT_READ_TEST_MODE", "").strip() == "1"
    data_override = bool(str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip())
    contract_override = bool(str(os.environ.get("TEACHER_EVAL_CONTRACT_DB") or "").strip())
    supported = failure_stage in {"before_school_fact_execution", "before_replay"} if failure_stage else False
    enabled = bool(failure_stage and explicit_mode and data_override and contract_override and supported)
    return {
        "failure_configured": bool(failure_stage),
        "failure_enabled": enabled,
        "failure_stage": failure_stage if enabled else None,
        "default_enabled": False,
        "requires_explicit_mode": True,
        "requires_data_override": True,
        "requires_contract_override": True,
        "stage_supported": supported,
    }


def _controlled_school_fact_tamper(
    contract: AnalysisContract,
    result: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
) -> Tuple[AnalysisContract, Dict[str, Any], Optional[Dict[str, Any]]]:
    status = school_fact_test_control_status(request_id=request_id)
    if not status["configured"] or contract.operation != "school_fact":
        return contract, result, None
    if not status["enabled"]:
        raise RuntimeError("school_fact_tamper_test_control_not_safely_enabled")
    if status["request_targeted"] and request_id is None:
        raise RuntimeError("school_fact_tamper_test_control_requires_request_id")
    if status["request_match"] is False:
        return contract, result, None

    case = str(status["case"])
    controlled_contract = contract
    controlled_result = copy.deepcopy(result)
    scope = dict(controlled_result.get("execution_scope") or {})
    controlled_result["execution_scope"] = scope

    if case == "contract_school_id":
        controlled_contract = replace(contract, school_id=int(contract.school_id or 0) + 100000000)
    elif case == "contract_school_name":
        controlled_contract = replace(contract, entity_id=f"{contract.entity_id}[TAMPERED]")
    elif case == "contract_period":
        controlled_contract = replace(contract, period="1900")
    elif case == "contract_metric_type":
        replacement_type = "question" if contract.metric_type != "question" else "compound"
        controlled_contract = replace(contract, metric_type=replacement_type)
    elif case == "contract_metric_key":
        controlled_contract = replace(contract, metric_key=f"{contract.metric_key}:tampered")
    elif case == "contract_metric_spec":
        tampered_spec = dict(contract.metric_spec or {})
        tampered_spec["name"] = f"{contract.metric_name}[TAMPERED]"
        controlled_contract = replace(contract, metric_spec=tampered_spec)
    elif case == "contract_indicator_ids":
        controlled_contract = replace(contract, indicator_ids=tuple(contract.indicator_ids) + (999999999,))
    elif case == "result_school_id":
        controlled_result["school_id"] = int(contract.school_id or 0) + 100000000
    elif case == "result_school_name":
        controlled_result["school_name"] = f"{contract.entity_id}[TAMPERED]"
    elif case == "result_period":
        controlled_result["year"] = "1900"
    elif case == "result_metric_type":
        controlled_result["fact_type"] = "tampered"
    elif case == "result_metric_name":
        controlled_result["metric_name"] = f"{contract.metric_name}[TAMPERED]"
    elif case == "result_scope_school_id":
        scope["school_id"] = int(contract.school_id or 0) + 100000000
    elif case == "result_scope_period":
        scope["period"] = "1900"
    elif case == "result_scope_metric_key":
        scope["metric_key"] = f"{contract.metric_key}:tampered"
    elif case == "result_scope_indicator_ids":
        scope["indicator_ids"] = list(scope.get("indicator_ids") or []) + [999999999]
    elif case == "result_target_value":
        value = controlled_result.get("target_value")
        if value is None:
            controlled_result["target_value"] = 999999999
        elif isinstance(value, (int, float)):
            controlled_result["target_value"] = float(value) + 1.0
        else:
            controlled_result["target_value"] = f"{value}[TAMPERED]"
    elif case == "result_component_total":
        controlled_result["component_total"] = float(controlled_result.get("component_total") or 0) + 1.0
    elif case == "result_component_answer_count":
        controlled_result["component_answer_count"] = int(controlled_result.get("component_answer_count") or 0) + 1
    elif case == "result_component_complete":
        controlled_result["component_complete"] = not bool(controlled_result.get("component_complete"))
    elif case == "result_ratio":
        controlled_result["ratio"] = float(controlled_result.get("ratio") or 0) + 1.0
    elif case == "result_fact_data_status":
        controlled_result["fact_data_status"] = (
            "NO_DATA" if controlled_result.get("fact_data_status") == "ANSWER" else "ANSWER"
        )

    return controlled_contract, controlled_result, {
        "enabled": True,
        "case": case,
        "request_targeted": status["request_targeted"],
        "request_match": status["request_match"],
        "synthetic_tamper": True,
        "product_evidence_scope": "verifier_rejection_only",
    }


_N3_TAMPER_CASES = {
    "contract_entity", "contract_period", "contract_dimension",
    "contract_indicator_ids", "contract_dataset_version",
    "result_entity", "result_period", "result_dimension",
    "result_execution_scope_indicator_ids", "result_denominator", "result_score",
}


def n3_test_control_status(*, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Describe the gated N3 school-evaluation/region-item tamper control."""
    case = str(os.environ.get("TEACHER_EVAL_N3_TAMPER_CASE") or "").strip()
    target_request_id = str(
        os.environ.get("TEACHER_EVAL_N3_TAMPER_REQUEST_ID") or ""
    ).strip()
    explicit_mode = os.environ.get("TEACHER_EVAL_N3_TAMPER_TEST_MODE", "").strip() == "1"
    data_override = bool(str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip())
    contract_override = bool(str(os.environ.get("TEACHER_EVAL_CONTRACT_DB") or "").strip())
    case_supported = case in _N3_TAMPER_CASES if case else False
    enabled = bool(case and explicit_mode and data_override and contract_override and case_supported)
    return {
        "configured": bool(case),
        "enabled": enabled,
        "requested_case": case if case else None,
        "case": case if enabled else None,
        "supported_cases": sorted(_N3_TAMPER_CASES),
        "default_enabled": False,
        "requires_explicit_mode": True,
        "requires_data_override": True,
        "requires_contract_override": True,
        "case_supported": case_supported,
        "request_targeted": bool(target_request_id),
        "request_match": (
            None
            if request_id is None
            else bool(not target_request_id or request_id == target_request_id)
        ),
    }


def _controlled_n3_tamper(
    contract: AnalysisContract,
    result: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
) -> Tuple[AnalysisContract, Dict[str, Any], Optional[Dict[str, Any]]]:
    status = n3_test_control_status(request_id=request_id)
    is_n3_target = (
        contract.operation == "school_evaluation"
        or (contract.operation == "region_evaluation" and contract.metric_type == "question")
    )
    if not status["configured"] or not is_n3_target:
        return contract, result, None
    if not status["enabled"]:
        raise RuntimeError("n3_tamper_test_control_not_safely_enabled")
    if status["request_targeted"] and request_id is None:
        raise RuntimeError("n3_tamper_test_control_requires_request_id")
    if status["request_match"] is False:
        return contract, result, None

    case = str(status["case"])
    controlled_contract = contract
    controlled_result = copy.deepcopy(result)
    scope = dict(controlled_result.get("execution_scope") or {})
    controlled_result["execution_scope"] = scope
    if case == "contract_entity":
        if contract.operation == "school_evaluation":
            controlled_contract = replace(contract, school_id=int(contract.school_id or 0) + 100000000)
        else:
            controlled_contract = replace(contract, entity_id=f"{contract.entity_id}[TAMPERED]")
    elif case == "contract_period":
        controlled_contract = replace(contract, period="1900")
    elif case == "contract_dimension":
        controlled_contract = replace(
            contract,
            dimension="教育教学" if contract.dimension == "数字资源" else "数字资源",
        )
    elif case == "contract_indicator_ids":
        controlled_contract = replace(contract, indicator_ids=tuple(contract.indicator_ids) + (999999999,))
    elif case == "contract_dataset_version":
        controlled_contract = replace(contract, dataset_version=f"{contract.dataset_version}:tampered")
    elif case == "result_entity":
        if contract.operation == "school_evaluation":
            controlled_result["school_id"] = int(contract.school_id or 0) + 100000000
        else:
            controlled_result["region_name"] = f"{contract.entity_id}[TAMPERED]"
    elif case == "result_period":
        if contract.operation == "school_evaluation":
            controlled_result["school_years"] = ["1900"]
        else:
            controlled_result["year"] = "1900"
    elif case == "result_dimension":
        controlled_result["target_dimension"] = (
            "教育教学" if contract.dimension == "数字资源" else "数字资源"
        )
    elif case == "result_execution_scope_indicator_ids":
        scope["indicator_ids"] = list(scope.get("indicator_ids") or []) + [999999999]
    elif case == "result_denominator":
        key = "answered" if contract.operation == "school_evaluation" else "school_count"
        controlled_result[key] = int(controlled_result.get(key) or 0) + 1
    elif case == "result_score":
        controlled_result["total_score"] = float(controlled_result.get("total_score") or 0.0) + 1.0

    return controlled_contract, controlled_result, {
        "enabled": True,
        "case": case,
        "request_targeted": status["request_targeted"],
        "request_match": status["request_match"],
        "synthetic_tamper": True,
        "target_operation": contract.operation,
        "product_evidence_scope": "verifier_rejection_only",
    }


_SCHOOL_COMPARE_TAMPER_CASES = {
    "contract_school_a_id", "contract_school_b_id", "contract_school_a_name", "contract_school_b_name",
    "contract_period", "contract_indicator_ids", "result_school_a_id", "result_school_b_id",
    "result_school_a_name", "result_school_b_name", "result_period", "result_scope",
    "result_score_a", "result_score_b", "result_diff", "result_dimension_score", "result_winner",
}


def school_compare_test_control_status(*, request_id: Optional[str] = None) -> Dict[str, Any]:
    case = str(os.environ.get("TEACHER_EVAL_SCHOOL_COMPARE_TAMPER_CASE") or "").strip()
    target_request_id = str(
        os.environ.get("TEACHER_EVAL_SCHOOL_COMPARE_TAMPER_REQUEST_ID") or ""
    ).strip()
    explicit_mode = os.environ.get("TEACHER_EVAL_SCHOOL_COMPARE_TAMPER_TEST_MODE", "").strip() == "1"
    data_override = bool(str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip())
    contract_override = bool(str(os.environ.get("TEACHER_EVAL_CONTRACT_DB") or "").strip())
    case_supported = case in _SCHOOL_COMPARE_TAMPER_CASES if case else False
    enabled = bool(case and explicit_mode and data_override and contract_override and case_supported)
    return {
        "configured": bool(case),
        "enabled": enabled,
        "requested_case": case if case else None,
        "case": case if enabled else None,
        "default_enabled": False,
        "requires_explicit_mode": True,
        "requires_data_override": True,
        "requires_contract_override": True,
        "case_supported": case_supported,
        "request_targeted": bool(target_request_id),
        "request_match": (
            None
            if request_id is None
            else bool(not target_request_id or request_id == target_request_id)
        ),
    }


def _controlled_school_compare_tamper(
    contract: AnalysisContract,
    result: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
) -> Tuple[AnalysisContract, Dict[str, Any], Optional[Dict[str, Any]]]:
    status = school_compare_test_control_status(request_id=request_id)
    if not status["configured"] or contract.operation != "school_compare":
        return contract, result, None
    if not status["enabled"]:
        raise RuntimeError("school_compare_tamper_test_control_not_safely_enabled")
    if status["request_targeted"] and request_id is None:
        raise RuntimeError("school_compare_tamper_test_control_requires_request_id")
    if status["request_match"] is False:
        return contract, result, None

    case = str(status["case"])
    controlled_contract = contract
    controlled_result = copy.deepcopy(result)
    if case == "contract_school_a_id":
        controlled_contract = replace(contract, school_id=int(contract.school_id or 0) + 100000000)
    elif case == "contract_school_b_id":
        controlled_contract = replace(contract, secondary_school_id=int(contract.secondary_school_id or 0) + 100000000)
    elif case == "contract_school_a_name":
        controlled_contract = replace(contract, entity_id=f"{contract.entity_id}[TAMPERED]")
    elif case == "contract_school_b_name":
        controlled_contract = replace(contract, secondary_entity_id=f"{contract.secondary_entity_id}[TAMPERED]")
    elif case == "contract_period":
        controlled_contract = replace(contract, period="1900")
    elif case == "contract_indicator_ids":
        controlled_contract = replace(contract, indicator_ids=tuple(contract.indicator_ids) + (999999999,))
    elif case == "result_school_a_id":
        controlled_result.setdefault("school_a", {})["school_id"] = int(contract.school_id or 0) + 100000000
    elif case == "result_school_b_id":
        controlled_result.setdefault("school_b", {})["school_id"] = int(contract.secondary_school_id or 0) + 100000000
    elif case == "result_school_a_name":
        controlled_result["school_name_a"] = f"{contract.entity_id}[TAMPERED]"
    elif case == "result_school_b_name":
        controlled_result["school_name_b"] = f"{contract.secondary_entity_id}[TAMPERED]"
    elif case == "result_period":
        controlled_result["year_a"] = "1900"
    elif case == "result_scope":
        scope = dict(controlled_result.get("execution_scope") or {})
        scope["metric_key"] = f"{contract.metric_key}:tampered"
        controlled_result["execution_scope"] = scope
    elif case == "result_score_a":
        controlled_result["total_score_a"] = float(controlled_result.get("total_score_a") or 0) + 1.0
    elif case == "result_score_b":
        controlled_result["total_score_b"] = float(controlled_result.get("total_score_b") or 0) + 1.0
    elif case == "result_diff":
        controlled_result["total_diff"] = float(controlled_result.get("total_diff") or 0) + 1.0
    elif case == "result_dimension_score":
        rows = controlled_result.get("dimensions_comparison") or []
        if rows:
            rows[0]["score_a"] = float(rows[0].get("score_a") or 0) + 1.0
    elif case == "result_winner":
        controlled_result["winner"] = "B" if controlled_result.get("winner") == "A" else "A"

    return controlled_contract, controlled_result, {
        "enabled": True,
        "case": case,
        "request_targeted": status["request_targeted"],
        "request_match": status["request_match"],
        "synthetic_tamper": True,
        "product_evidence_scope": "verifier_rejection_only",
    }


_REGION_LONGITUDINAL_TAMPER_CASES = {
    "contract_region", "contract_period_a", "contract_period_b", "contract_indicator_ids",
    "contract_statistic", "contract_aggregation", "contract_filter",
    "result_region", "result_period_a", "result_period_b", "result_scope",
    "result_value_a", "result_value_b", "result_diff", "result_pct_change",
    "result_school_count_a", "result_school_count_b",
    "result_answer_count_a", "result_answer_count_b",
}


def region_longitudinal_test_control_status(
    *, request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Describe the strictly gated in-memory two-period tamper control."""
    case = str(
        os.environ.get("TEACHER_EVAL_REGION_LONGITUDINAL_TAMPER_CASE") or ""
    ).strip()
    target_request_id = str(
        os.environ.get("TEACHER_EVAL_REGION_LONGITUDINAL_TAMPER_REQUEST_ID") or ""
    ).strip()
    explicit_mode = (
        os.environ.get("TEACHER_EVAL_REGION_LONGITUDINAL_TAMPER_TEST_MODE", "").strip()
        == "1"
    )
    data_override = bool(str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip())
    contract_override = bool(str(os.environ.get("TEACHER_EVAL_CONTRACT_DB") or "").strip())
    case_supported = case in _REGION_LONGITUDINAL_TAMPER_CASES if case else False
    enabled = bool(case and explicit_mode and data_override and contract_override and case_supported)
    return {
        "configured": bool(case),
        "enabled": enabled,
        "requested_case": case if case else None,
        "case": case if enabled else None,
        "default_enabled": False,
        "requires_explicit_mode": True,
        "requires_data_override": True,
        "requires_contract_override": True,
        "case_supported": case_supported,
        "request_targeted": bool(target_request_id),
        "request_match": (
            None
            if request_id is None
            else bool(not target_request_id or request_id == target_request_id)
        ),
    }


def _controlled_region_longitudinal_tamper(
    contract: AnalysisContract,
    result: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
) -> Tuple[AnalysisContract, Dict[str, Any], Optional[Dict[str, Any]]]:
    status = region_longitudinal_test_control_status(request_id=request_id)
    if not status["configured"] or contract.operation != "region_longitudinal":
        return contract, result, None
    if not status["enabled"]:
        raise RuntimeError("region_longitudinal_tamper_test_control_not_safely_enabled")
    if status["request_targeted"] and request_id is None:
        raise RuntimeError("region_longitudinal_tamper_test_control_requires_request_id")
    if status["request_match"] is False:
        return contract, result, None

    case = str(status["case"])
    controlled_contract = contract
    controlled_result = copy.deepcopy(result)
    if case == "contract_region":
        controlled_contract = replace(contract, entity_id=f"{contract.entity_id}[TAMPERED]")
    elif case == "contract_period_a":
        controlled_contract = replace(contract, period="2000")
    elif case == "contract_period_b":
        controlled_contract = replace(contract, secondary_period="2099")
    elif case == "contract_indicator_ids":
        controlled_contract = replace(
            contract, indicator_ids=tuple(contract.indicator_ids) + (999999999,)
        )
    elif case == "contract_statistic":
        controlled_contract = replace(
            contract, statistic="count" if contract.statistic == "mean" else "mean"
        )
    elif case == "contract_aggregation":
        controlled_contract = replace(
            contract,
            aggregation=(
                "answer_pooled_mean"
                if contract.aggregation == "school_equal_mean"
                else "school_equal_mean"
            ),
        )
    elif case == "contract_filter":
        controlled_contract = replace(
            contract,
            school_filter="乡村" if contract.school_filter != "乡村" else "城区",
        )
    elif case == "result_region":
        controlled_result["region_name"] = f"{contract.entity_id}[TAMPERED]"
    elif case == "result_period_a":
        controlled_result["year_a"] = "2000"
    elif case == "result_period_b":
        controlled_result["year_b"] = "2099"
    elif case == "result_scope":
        scope = dict(controlled_result.get("execution_scope") or {})
        scope["comparison_direction"] = "primary_minus_secondary"
        controlled_result["execution_scope"] = scope
    elif case == "result_value_a":
        controlled_result["comparison_value_a"] = float(
            controlled_result.get("comparison_value_a") or 0
        ) + 1
    elif case == "result_value_b":
        controlled_result["comparison_value_b"] = float(
            controlled_result.get("comparison_value_b") or 0
        ) + 1
    elif case == "result_diff":
        controlled_result["diff"] = float(controlled_result.get("diff") or 0) + 1
    elif case == "result_pct_change":
        controlled_result["pct_change"] = float(
            controlled_result.get("pct_change") or 0
        ) + 1
    elif case in {
        "result_school_count_a", "result_school_count_b",
        "result_answer_count_a", "result_answer_count_b",
    }:
        field_name = case.removeprefix("result_")
        controlled_result[field_name] = int(controlled_result.get(field_name) or 0) + 1

    return controlled_contract, controlled_result, {
        "enabled": True,
        "case": case,
        "request_targeted": status["request_targeted"],
        "request_match": status["request_match"],
        "synthetic_tamper": True,
        "product_evidence_scope": "verifier_rejection_only",
    }


_REGION_RANKING_TAMPER_CASES = {
    "contract_region", "contract_period", "contract_indicator_ids", "contract_statistic",
    "contract_aggregation", "contract_filter", "contract_target_rank", "contract_target_top_n",
    "contract_ranking_spec", "result_region", "result_period", "result_target_rank",
    "result_target_top_n", "result_target_school", "result_top_n_order", "result_scope",
    "result_top_n_score", "result_returned_count", "result_ranking_spec",
    "result_population", "result_school_count", "result_answer_count",
}


def region_ranking_test_control_status(
    *, request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Describe the strictly gated in-memory region-ranking tamper control."""
    case = str(os.environ.get("TEACHER_EVAL_REGION_RANKING_TAMPER_CASE") or "").strip()
    target_request_id = str(
        os.environ.get("TEACHER_EVAL_REGION_RANKING_TAMPER_REQUEST_ID") or ""
    ).strip()
    explicit_mode = (
        os.environ.get("TEACHER_EVAL_REGION_RANKING_TAMPER_TEST_MODE", "").strip() == "1"
    )
    data_override = bool(str(os.environ.get("TEACHER_EVAL_DATA_DB") or "").strip())
    contract_override = bool(str(os.environ.get("TEACHER_EVAL_CONTRACT_DB") or "").strip())
    case_supported = case in _REGION_RANKING_TAMPER_CASES if case else False
    enabled = bool(case and explicit_mode and data_override and contract_override and case_supported)
    return {
        "configured": bool(case),
        "enabled": enabled,
        "requested_case": case if case else None,
        "case": case if enabled else None,
        "supported_cases": sorted(_REGION_RANKING_TAMPER_CASES),
        "default_enabled": False,
        "requires_explicit_mode": True,
        "requires_data_override": True,
        "requires_contract_override": True,
        "case_supported": case_supported,
        "request_targeted": bool(target_request_id),
        "request_match": (
            None if request_id is None
            else bool(not target_request_id or request_id == target_request_id)
        ),
    }


def _controlled_region_ranking_tamper(
    contract: AnalysisContract,
    result: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
) -> Tuple[AnalysisContract, Dict[str, Any], Optional[Dict[str, Any]]]:
    status = region_ranking_test_control_status(request_id=request_id)
    if not status["configured"] or contract.operation != "region_ranking":
        return contract, result, None
    if not status["enabled"]:
        raise RuntimeError("region_ranking_tamper_test_control_not_safely_enabled")
    if status["request_targeted"] and request_id is None:
        raise RuntimeError("region_ranking_tamper_test_control_requires_request_id")
    if status["request_match"] is False:
        return contract, result, None

    case = str(status["case"])
    controlled_contract = contract
    controlled_result = copy.deepcopy(result)
    if case == "contract_region":
        controlled_contract = replace(contract, entity_id=f"{contract.entity_id}[TAMPERED]")
    elif case == "contract_period":
        controlled_contract = replace(contract, period="1900")
    elif case == "contract_indicator_ids":
        controlled_contract = replace(
            contract, indicator_ids=tuple(contract.indicator_ids) + (999999999,)
        )
    elif case == "contract_statistic":
        controlled_contract = replace(contract, statistic="median")
    elif case == "contract_aggregation":
        controlled_contract = replace(contract, aggregation="answer_pooled_mean")
    elif case == "contract_filter":
        controlled_contract = replace(
            contract, school_filter="乡村" if contract.school_filter != "乡村" else "城区"
        )
    elif case == "contract_target_rank":
        controlled_contract = replace(contract, target_rank=(contract.target_rank or 1) + 1)
    elif case == "contract_target_top_n":
        controlled_contract = replace(contract, target_top_n=(contract.target_top_n or 1) + 1)
    elif case == "contract_ranking_spec":
        spec = dict(contract.ranking_spec or {})
        spec["tie_policy"] = "tampered"
        controlled_contract = replace(contract, ranking_spec=spec)
    elif case == "result_region":
        controlled_result["region_name"] = f"{contract.entity_id}[TAMPERED]"
    elif case == "result_period":
        controlled_result["year"] = "1900"
    elif case == "result_target_rank":
        controlled_result["target_rank"] = (contract.target_rank or 1) + 1
    elif case == "result_target_top_n":
        controlled_result["target_top_n"] = (contract.target_top_n or 1) + 1
    elif case == "result_target_school":
        target = dict(controlled_result.get("target_school") or {})
        target["id"] = int(target.get("id") or 0) + 100000000
        controlled_result["target_school"] = target
    elif case == "result_top_n_order":
        rows = list(controlled_result.get("top_schools") or ())
        if len(rows) >= 2:
            rows[0], rows[1] = rows[1], rows[0]
        controlled_result["top_schools"] = rows
        controlled_result["ranking_items"] = list(rows)
    elif case == "result_top_n_score":
        rows = [dict(item) for item in (controlled_result.get("top_schools") or ())]
        if rows:
            rows[0]["score"] = float(rows[0].get("score") or 0) + 1
            rows[0]["avg_score"] = rows[0]["score"]
            rows[0]["total_score"] = rows[0]["score"]
        controlled_result["top_schools"] = rows
        controlled_result["ranking_items"] = list(rows)
    elif case == "result_returned_count":
        controlled_result["returned_school_count"] = int(
            controlled_result.get("returned_school_count") or 0
        ) + 1
    elif case == "result_ranking_spec":
        spec = dict(controlled_result.get("ranking_spec") or {})
        spec["display_precision"] = 3
        controlled_result["ranking_spec"] = spec
    elif case == "result_population":
        population = dict(controlled_result.get("ranking_population") or {})
        population["eligible_school_count"] = int(
            population.get("eligible_school_count") or 0
        ) + 1
        controlled_result["ranking_population"] = population
    elif case == "result_scope":
        scope = dict(controlled_result.get("execution_scope") or {})
        spec = dict(scope.get("ranking_spec") or {})
        spec["direction"] = "ascending"
        scope["ranking_spec"] = spec
        controlled_result["execution_scope"] = scope
    elif case == "result_school_count":
        controlled_result["school_count"] = int(controlled_result.get("school_count") or 0) + 1
    elif case == "result_answer_count":
        controlled_result["answer_count"] = int(controlled_result.get("answer_count") or 0) + 1

    return controlled_contract, controlled_result, {
        "enabled": True,
        "case": case,
        "request_targeted": status["request_targeted"],
        "request_match": status["request_match"],
        "synthetic_tamper": True,
        "product_evidence_scope": "verifier_rejection_only",
    }


def _filter_sql(school_filter: Optional[str]) -> Tuple[str, Tuple[Any, ...]]:
    from packages.core.school_filters import filter_sql
    return filter_sql(school_filter)


def _indicator_ids(
    target_dimension: Optional[str],
    *,
    connection: Optional[sqlite3.Connection] = None,
) -> Tuple[int, ...]:
    if connection is None:
        with closing(connect()) as conn:
            return _indicator_ids(target_dimension, connection=conn)
    if target_dimension:
        rows = connection.execute(
            "SELECT id FROM questions WHERE level1_name=? ORDER BY id",
            (target_dimension,),
        ).fetchall()
    else:
        rows = connection.execute("SELECT id FROM questions ORDER BY id").fetchall()
    ids = tuple(int(row[0]) for row in rows)
    if not ids:
        raise CapabilityError(f"unknown_indicator_scope:{target_dimension}")
    return ids


def _school_fact_spec(metric_info: Dict[str, Any]) -> Tuple[str, str, Tuple[int, ...], Dict[str, Any]]:
    metric_type = str(metric_info.get("type") or "")
    if metric_type == "question":
        qid = int(metric_info.get("qid"))
        metric_key = f"question:{qid}"
        indicator_ids = (qid,)
    elif metric_type == "question_set":
        indicator_ids = tuple(metric_info.get("qids") or ())
        items = metric_info.get("items") or []
        if (
            len(indicator_ids) != 2 or len(set(indicator_ids)) != 2
            or any(type(qid) is not int for qid in indicator_ids)
            or len(items) != 2
            or any(item.get("type") != "question" or item.get("qid") != qid
                   for item, qid in zip(items, indicator_ids))
        ):
            raise CapabilityError("invalid_school_question_set")
        derived = metric_info.get("derived")
        if derived is not None and any(
            item.get("coding_status") == "source_missing" for item in items
        ):
            raise CapabilityError("unsupported_school_question_set_coded_arithmetic")
        if derived is not None and (
            not isinstance(derived, dict)
            or derived.get("operation") not in {"subtract", "divide_percent", "add", "divide", "sum", "ratio"}
            or (derived.get("left_qid"), derived.get("right_qid")) != indicator_ids
        ):
            raise CapabilityError("invalid_school_question_set_derived")
        metric_key = f"question_set:{indicator_ids[0]},{indicator_ids[1]}"
    elif metric_type == "compound":
        indicator_ids = tuple(int(qid) for qid in (metric_info.get("qids") or ()))
        metric_key = f"compound:{metric_info.get('compound_key') or metric_info.get('name') or ''}"
    elif metric_type == "school_field":
        field_name = str(metric_info.get("field") or "")
        if field_name not in {"teacher_num", "student_num", "class_num", "school_area_type", "school_type"}:
            raise CapabilityError(f"unsupported_school_field:{field_name}")
        metric_key = f"school_field:{field_name}"
        indicator_ids = ()
    else:
        raise CapabilityError(f"unsupported_school_fact_metric:{metric_type}")
    if metric_type in {"question", "question_set", "compound"} and not indicator_ids:
        raise CapabilityError("missing_school_fact_indicators")
    metric_spec = {
        key: value
        for key, value in metric_info.items()
        if key in {
            "type", "qid", "qids", "field", "name", "dimension", "unit", "related",
            "compound_key", "formula", "school_field", "desc", "value_type",
            "scale", "coding_status", "coding_notes", "items", "derived", "value_view",
        }
    }
    return metric_type, metric_key, indicator_ids, metric_spec


def _school_compare_template_spec() -> Tuple[str, str, Tuple[int, ...], Dict[str, Any]]:
    template = load_school_template()
    if not isinstance(template, dict):
        raise CapabilityError("school_compare_template_unavailable")
    dimensions = []
    indicator_ids = []
    for dimension in template.get("dimensions") or ():
        qids = tuple(int(metric["id"]) for metric in (dimension.get("metrics") or ()))
        if not qids:
            continue
        indicator_ids.extend(qids)
        dimensions.append({
            "name": str(dimension.get("name") or ""),
            "weight": float(dimension.get("weight") or 0),
            "indicator_ids": list(qids),
        })
    ordered_ids = tuple(dict.fromkeys(indicator_ids))
    if not ordered_ids or not dimensions:
        raise CapabilityError("school_compare_template_empty")
    template_name = str((template.get("template") or {}).get("name") or "学校数字化评估")
    metric_key = "school_digitization_v1"
    return "school_template", metric_key, ordered_ids, {
        "type": "school_template",
        "template_key": metric_key,
        "name": template_name,
        "complete_template_required": True,
        "dimensions": dimensions,
    }


def _school_evaluation_template_spec(
    target_dimension: Optional[str],
    target_dimensions: Tuple[str, ...] = (),
) -> Tuple[str, str, Tuple[int, ...], Dict[str, Any]]:
    template = load_school_template()
    if not isinstance(template, dict):
        raise CapabilityError("school_evaluation_template_unavailable")
    dimensions = []
    indicator_ids = []
    template_rows = list(template.get("dimensions") or ())
    if target_dimensions:
        by_name = {str(item.get("name") or ""): item for item in template_rows}
        if any(name not in by_name for name in target_dimensions):
            raise CapabilityError("school_evaluation_template_scope_empty")
        template_rows = [by_name[name] for name in target_dimensions]
    for dimension in template_rows:
        name = str(dimension.get("name") or "")
        if target_dimension and name != target_dimension:
            continue
        qids = tuple(int(metric["id"]) for metric in (dimension.get("metrics") or ()))
        if not qids:
            continue
        indicator_ids.extend(qids)
        dimensions.append({
            "name": name,
            "weight": float(dimension.get("weight") or 0),
            "indicator_ids": list(qids),
        })
    ordered_ids = tuple(dict.fromkeys(indicator_ids))
    if not ordered_ids or not dimensions:
        raise CapabilityError("school_evaluation_template_scope_empty")
    template_name = str((template.get("template") or {}).get("name") or "学校数字化评估")
    metric_key = "school_digitization_v1"
    return "school_template", metric_key, ordered_ids, {
        "type": "school_template",
        "template_key": metric_key,
        "name": template_name,
        "complete_template_required": False,
        "dimensions": dimensions,
    }


def _resolve_school_for_period(
    token: Any,
    period: str,
    connection: sqlite3.Connection,
    *,
    error_prefix: str,
) -> Dict[str, Any]:
    raw = str(token or "").strip()
    if isinstance(token, int) or raw.isdigit():
        school = get_school(int(token), connection=connection)
        if school and str(school.get("year") or "") != period:
            school = None
    elif raw.startswith("id:"):
        school = get_school(int(raw.split(":", 1)[1]), connection=connection)
        if school and str(school.get("year") or "") != period:
            school = None
    else:
        hits = find_schools(raw, year=period, limit=10, connection=connection)
        expanded = expand_school_alias(raw)
        exact = [
            hit for hit in hits
            if str(hit.get("school_name") or "") in {raw, expanded}
        ]
        if len(exact) == 1:
            school = get_school(int(exact[0]["id"]), connection=connection)
        elif len(exact) > 1 or len(hits) > 1:
            raise CapabilityError(f"ambiguous_{error_prefix}:{raw}:{period}")
        else:
            school = get_school(int(hits[0]["id"]), connection=connection) if hits else None
    if not school:
        raise CapabilityError(f"unresolved_{error_prefix}:{raw}:{period}")
    return school


def _source(
    value: Any,
    question: str,
    turn_id: str,
    previous: Optional[AnalysisContract],
    field: str,
    *,
    evidence_candidates: Tuple[str, ...] = (),
    pending_source: Optional[Dict[str, Any]] = None,
) -> FieldSource:
    text_value = "" if value is None else str(value)
    for evidence in (text_value, *evidence_candidates):
        if evidence and evidence in question:
            return FieldSource(value=value, origin="user_explicit", source_turn=turn_id, evidence_span=evidence)
    if pending_source is not None and pending_source.get("value") == value:
        return FieldSource(
            value=value,
            origin="user_explicit",
            source_turn=str(pending_source.get("source_turn") or turn_id),
            evidence_span=pending_source.get("evidence_span"),
        )
    if previous is not None and getattr(previous, field, object()) == value:
        return FieldSource(value=value, origin="inherited", source_turn=turn_id, inherited_from=previous.revision)
    return FieldSource(value=value, origin="catalogue_or_default", source_turn=turn_id)


def _ranking_evidence_candidates(
    question: str,
    *,
    target_rank: Optional[int] = None,
    target_top_n: Optional[int] = None,
) -> Tuple[str, ...]:
    patterns: list[str] = []
    if target_rank == -1:
        patterns.extend((r"倒数第?\s*[一1]\s*名?", r"最后一(?:名|所)", r"(?:排名|排)最后", r"末位"))
    elif target_rank is not None:
        patterns.extend((
            r"(?:排名?第|位列第|位居第|排第)\s*(?:\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|位)?",
            r"榜首|第一名|排第一|排名第一|最高分学校|分数最高的学校",
        ))
    if target_top_n is not None:
        patterns.append(r"(?:排名前|前)\s*(?:\d+|[一二两三四五六七八九十]{1,3})\s*(?:名|所|所学校|家|个)?")
    matches = []
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            matches.append(match.group(0))
    return tuple(dict.fromkeys(matches))


class ContractService:
    def __init__(self, store: Optional[ContractStore] = None):
        self.store = store or ContractStore()
        self._pending_transition: Dict[str, Any] = {}
        self.pending_transition_result: Optional[Dict[str, Any]] = None
        # Freeze the resolved data source for this request-scoped service. This
        # prevents prepare/execute/replay from silently switching between two
        # same-named SQLite files when environment state changes mid-request.
        self._data_path = str(_resolved_data_path())

    def _connect(self) -> sqlite3.Connection:
        return connect(self._data_path)

    def _dataset_version(self) -> str:
        return _dataset_version(self._data_path)

    def _indicator_ids(
        self,
        target_dimension: Optional[str],
        *,
        connection: Optional[sqlite3.Connection] = None,
    ) -> Tuple[int, ...]:
        if connection is not None:
            return _indicator_ids(target_dimension, connection=connection)
        with closing(self._connect()) as conn:
            return _indicator_ids(target_dimension, connection=conn)

    @staticmethod
    def context_from(contract: AnalysisContract) -> Dict[str, Any]:
        """Project a verified/NO_DATA contract into the legacy router context."""
        operation_alias = {
            "school_evaluation": "school_eval",
            "school_fact": "school_fact",
            "region_evaluation": "region_eval",
            "region_compare": "region_compare",
            "school_compare": "school_compare",
            "region_longitudinal": "region_longitudinal",
            "region_ranking": "region_ranking",
        }.get(contract.operation, contract.operation)
        if contract.operation in {"school_evaluation", "school_fact", "school_compare"}:
            scope_object_type = "school"
        elif contract.operation in {
            "region_evaluation", "region_compare", "region_longitudinal", "region_ranking"
        }:
            scope_object_type = "region"
        else:
            scope_object_type = None
        if contract.operation == "school_fact" or (
            contract.operation == "region_evaluation" and contract.metric_type == "question"
        ):
            scope_granularity = "item"
        elif contract.operation in {"school_evaluation", "region_evaluation"}:
            scope_granularity = "dimension" if contract.dimension else "overall"
        else:
            scope_granularity = "multi_scope"
        scope_context = {
            "last_value_view": contract.value_view,
            "last_scope_object_type": scope_object_type,
            "last_scope_operation": operation_alias,
            "last_scope_granularity": scope_granularity,
            "last_scope_metric_key": contract.metric_key if scope_granularity == "item" else None,
        }
        if contract.operation == "school_evaluation":
            return {
                **scope_context,
                "last_region": None,
                "last_region_compare_a": None,
                "last_region_compare_b": None,
                "last_region_longitudinal_period_a": None,
                "last_region_longitudinal_period_b": None,
                "last_target_rank": None,
                "last_target_top_n": None,
                "last_school_compare_a": None,
                "last_school_compare_b": None,
                "last_school": contract.entity_id,
                "last_school_fact_entity": None,
                "last_school_fact_metric": None,
                "last_school_fact_metric_key": None,
                "last_school_fact_metric_revision": None,
                "last_year": contract.period,
                "last_dimension": contract.dimension,
                "last_statistic": None,
                "last_aggregation": None,
                "last_school_filter": None,
                "contract_focus_task_id": contract.task_id,
                "contract_focus_revision": contract.revision,
                "contract_context_source": "trusted_store",
            }
        if contract.operation == "school_fact":
            metric_context = dict(contract.metric_spec or {})
            metric_context.setdefault("type", contract.metric_type)
            metric_context.setdefault("name", contract.metric_name)
            return {
                **scope_context,
                "last_region": None,
                "last_region_compare_a": None,
                "last_region_compare_b": None,
                "last_region_longitudinal_period_a": None,
                "last_region_longitudinal_period_b": None,
                "last_target_rank": None,
                "last_target_top_n": None,
                "last_school_compare_a": None,
                "last_school_compare_b": None,
                "last_school": contract.entity_id,
                "last_year": contract.period,
                "last_school_fact_entity": contract.entity_id,
                "last_school_fact_metric": metric_context,
                "last_school_fact_metric_key": contract.metric_key,
                "last_school_fact_metric_revision": contract.revision,
                "last_dimension": None,
                "last_statistic": None,
                "last_aggregation": None,
                "last_school_filter": None,
                "contract_focus_task_id": contract.task_id,
                "contract_focus_revision": contract.revision,
                "contract_context_source": "trusted_store",
            }
        if contract.operation == "region_compare":
            return {
                **scope_context,
                "last_region": None,
                "last_region_compare_a": contract.region_keyword,
                "last_region_compare_b": contract.secondary_region_keyword,
                "last_region_longitudinal_period_a": None,
                "last_region_longitudinal_period_b": None,
                "last_target_rank": None,
                "last_target_top_n": None,
                "last_school": None,
                "last_school_compare_a": None,
                "last_school_compare_b": None,
                "last_school_fact_entity": None,
                "last_school_fact_metric": None,
                "last_school_fact_metric_key": None,
                "last_school_fact_metric_revision": None,
                "last_year": contract.period,
                "last_dimension": contract.dimension,
                "last_statistic": contract.statistic,
                "last_aggregation": contract.aggregation,
                "last_school_filter": contract.school_filter,
                "contract_focus_task_id": contract.task_id,
                "contract_focus_revision": contract.revision,
                "contract_context_source": "trusted_store",
            }
        if contract.operation == "region_longitudinal":
            return {
                **scope_context,
                "last_region": contract.region_keyword,
                "last_region_compare_a": None,
                "last_region_compare_b": None,
                "last_region_longitudinal_period_a": contract.period,
                "last_region_longitudinal_period_b": contract.secondary_period,
                "last_target_rank": None,
                "last_target_top_n": None,
                "last_school": None,
                "last_school_compare_a": None,
                "last_school_compare_b": None,
                "last_school_fact_entity": None,
                "last_school_fact_metric": None,
                "last_school_fact_metric_key": None,
                "last_school_fact_metric_revision": None,
                "last_year": contract.secondary_period,
                "last_dimension": contract.dimension,
                "last_statistic": contract.statistic,
                "last_aggregation": contract.aggregation,
                "last_school_filter": contract.school_filter,
                "contract_focus_task_id": contract.task_id,
                "contract_focus_revision": contract.revision,
                "contract_context_source": "trusted_store",
            }
        if contract.operation == "school_compare":
            return {
                **scope_context,
                "last_region": None,
                "last_region_compare_a": None,
                "last_region_compare_b": None,
                "last_region_longitudinal_period_a": None,
                "last_region_longitudinal_period_b": None,
                "last_target_rank": None,
                "last_target_top_n": None,
                "last_school": None,
                "last_school_compare_a": contract.entity_id,
                "last_school_compare_b": contract.secondary_entity_id,
                "last_school_fact_entity": None,
                "last_school_fact_metric": None,
                "last_school_fact_metric_key": None,
                "last_school_fact_metric_revision": None,
                "last_year": contract.period,
                "last_dimension": None,
                "last_statistic": None,
                "last_aggregation": None,
                "last_school_filter": None,
                "contract_focus_task_id": contract.task_id,
                "contract_focus_revision": contract.revision,
                "contract_context_source": "trusted_store",
            }
        return {
            **scope_context,
            "last_region": contract.region_keyword,
            "last_region_identity": copy.deepcopy(contract.region_identity),
            "last_region_compare_a": None,
            "last_region_compare_b": None,
            "last_region_longitudinal_period_a": None,
            "last_region_longitudinal_period_b": None,
            "last_target_rank": contract.target_rank if contract.operation == "region_ranking" else None,
            "last_target_top_n": contract.target_top_n if contract.operation == "region_ranking" else None,
            "last_school_compare_a": None,
            "last_school_compare_b": None,
            "last_school": None,
            "last_school_fact_entity": None,
            "last_school_fact_metric": None,
            "last_school_fact_metric_key": None,
            "last_school_fact_metric_revision": None,
            "last_year": contract.period,
            "last_dimension": contract.dimension,
            "last_statistic": contract.statistic,
            "last_aggregation": contract.aggregation,
            "last_school_filter": contract.school_filter,
            "contract_focus_task_id": contract.task_id,
            "contract_focus_revision": contract.revision,
            "contract_context_source": "trusted_store",
        }

    def session_context(self, session_id: str) -> Dict[str, Any]:
        focus = self.store.latest_focus(session_id)
        return self.context_from(focus) if focus is not None else {}

    def session_state(
        self, session_id: str, *, base_revision: Optional[int], request_id: str,
    ) -> Dict[str, Any]:
        state = self.store.session_state(session_id)
        head = state["head"]
        current = head.revision if head else 0
        if base_revision is not None and base_revision != current:
            error = StaleBaseRevision(base_revision, current)
            self._record_state_conflict(session_id, request_id, error)
            raise error
        return {
            **state,
            "head_revision": current,
            "context": self.context_from(state["focus"]) if state["focus"] else {},
        }

    def _record_state_conflict(self, session_id: str, request_id: str, error: StaleBaseRevision) -> None:
        head = self.store.latest(session_id)
        error.attempt_id = self.store.record_revision_conflict(
            request_id=request_id, session_id=session_id,
            task_id=head.task_id if head else None,
            requested_base_revision=error.requested, current_revision=error.current,
        )

    def set_pending_transition(self, pending: Optional[Dict[str, Any]], *, applied: bool) -> None:
        """Bind request-local completion intent, never a conversational focus."""
        self._pending_transition = {
            "pending_id": pending["pending_id"],
            "pending_status": "RESOLVED" if applied else "SUPERSEDED",
            "pending_resolution": "clarification_completed" if applied else "new_request_completed",
        } if pending else {}
        self.pending_transition_result = None

    def _record_execution(self, task_id: str, revision: int, **kwargs) -> None:
        self.pending_transition_result = self.store.record_execution(
            task_id, revision, **kwargs, **self._pending_transition,
        )

    def restore_revision(
        self,
        session_id: str,
        target_revision: int,
        question: str,
        *,
        base_revision: Optional[int] = None,
        request_id: str,
    ) -> AnalysisContract:
        """Create a new head revision from a trusted historical task state."""
        head = self.store.latest(session_id)
        if head is None:
            raise ValueError("restore_without_revision_history")
        current_revision = head.revision
        if base_revision is not None and base_revision != current_revision:
            attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id,
                requested_base_revision=base_revision,
                current_revision=current_revision,
            )
            raise StaleBaseRevision(base_revision, current_revision, attempt_id=attempt_id)
        if target_revision >= head.revision:
            raise ValueError(f"restore_target_must_be_older:{target_revision}>={head.revision}")
        source = self.store.revision_for_restore(session_id, head.task_id, target_revision)
        turn_id = str(uuid.uuid4())
        evidence = question.strip() or f"revision {target_revision}"
        restored_sources = {
            key: FieldSource(
                value=value.value,
                origin="restored",
                source_turn=turn_id,
                evidence_span=evidence,
                inherited_from=target_revision,
            )
            for key, value in source.sources.items()
        }
        draft = replace(
            source,
            revision=0,
            parent_revision=head.revision,
            turn_id=turn_id,
            dataset_version=self._dataset_version(),
            catalogue_version=CATALOGUE_VERSION,
            resolver_version=RESOLVER_VERSION,
            schema_version=SCHEMA_VERSION,
            indicator_ids=(
                tuple(dict.fromkeys(qid for name in source.target_dimensions for qid in self._indicator_ids(name)))
                if source.operation == "region_evaluation" and source.target_dimensions
                else self._indicator_ids(source.dimension)
                if source.operation in {"region_evaluation", "region_compare", "region_longitudinal", "region_ranking"}
                and not (
                    source.operation == "region_evaluation"
                    and source.metric_type == "question"
                )
                else source.indicator_ids
            ),
            sources=restored_sources,
            revision_action="RESTORE",
            restored_from_revision=target_revision,
        )
        try:
            return self.store.commit(draft, base_revision=current_revision)
        except StaleBaseRevision as exc:
            exc.attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id,
                requested_base_revision=exc.requested,
                current_revision=exc.current,
            )
            raise

    @staticmethod
    def routed_from_contract(contract: AnalysisContract, question: str) -> Dict[str, Any]:
        output_scope = {
            "value_view": contract.value_view,
            "requested_outputs": list(contract.requested_outputs),
        }
        if contract.operation == "school_evaluation":
            return {
                **output_scope,
                "intent": "school_eval",
                "school": f"id:{contract.school_id}",
                "year": contract.period,
                "years": [contract.period],
                "target_dimension": contract.dimension,
                "target_dimensions": [contract.dimension] if contract.dimension else [],
                "original": question,
                "revision_restore": {
                    "restored_from_revision": contract.restored_from_revision,
                    "created_revision": contract.revision,
                    "parent_revision": contract.parent_revision,
                },
            }
        if contract.operation == "school_fact":
            return {
                **output_scope,
                "intent": "school_fact",
                "school": f"id:{contract.school_id}",
                "metric": dict(contract.metric_spec or {}),
                "year": contract.period,
                "original": question,
                "revision_restore": {
                    "restored_from_revision": contract.restored_from_revision,
                    "created_revision": contract.revision,
                    "parent_revision": contract.parent_revision,
                },
            }
        if contract.operation == "region_compare":
            return {
                **output_scope,
                "intent": "region_compare",
                "region_a": contract.region_keyword,
                "region_b": contract.secondary_region_keyword,
                "year": contract.period,
                "target_dimension": contract.dimension,
                "target_dimensions": [contract.dimension] if contract.dimension else [],
                "target_stat": contract.statistic,
                "school_filter": contract.school_filter,
                "aggregation": contract.aggregation,
                "original": question,
                "revision_restore": {
                    "restored_from_revision": contract.restored_from_revision,
                    "created_revision": contract.revision,
                    "parent_revision": contract.parent_revision,
                },
            }
        if contract.operation == "region_longitudinal":
            return {
                **output_scope,
                "intent": "region_longitudinal",
                "region": contract.region_keyword,
                "year_a": contract.period,
                "year_b": contract.secondary_period,
                "target_dimension": contract.dimension,
                "target_dimensions": [contract.dimension] if contract.dimension else [],
                "target_stat": contract.statistic,
                "school_filter": contract.school_filter,
                "aggregation": contract.aggregation,
                "temporal_mode": contract.temporal_mode,
                "period_order_policy": "chronological_earlier_to_later",
                "original": question,
                "revision_restore": {
                    "restored_from_revision": contract.restored_from_revision,
                    "created_revision": contract.revision,
                    "parent_revision": contract.parent_revision,
                },
            }
        if contract.operation == "region_ranking":
            return {
                **output_scope,
                "intent": "region_ranking",
                "region": contract.region_keyword,
                "year": contract.period,
                "target_dimension": None,
                "target_dimensions": [],
                "target_stat": contract.statistic,
                "target_rank": contract.target_rank,
                "target_top_n": contract.target_top_n,
                "school_filter": contract.school_filter,
                "aggregation": contract.aggregation,
                "original": question,
                "revision_restore": {
                    "restored_from_revision": contract.restored_from_revision,
                    "created_revision": contract.revision,
                    "parent_revision": contract.parent_revision,
                },
            }
        if contract.operation == "school_compare":
            return {
                **output_scope,
                "intent": "school_compare",
                "school_a": f"id:{contract.school_id}",
                "school_b": f"id:{contract.secondary_school_id}",
                "year": contract.period,
                "original": question,
                "revision_restore": {
                    "restored_from_revision": contract.restored_from_revision,
                    "created_revision": contract.revision,
                    "parent_revision": contract.parent_revision,
                },
            }
        routed = {
            **output_scope,
            "intent": "region_eval",
            "region": contract.region_keyword,
            "year": contract.period,
            "target_dimension": contract.dimension,
            "target_dimensions": None,
            "target_stat": contract.statistic,
            "target_rank": contract.target_rank,
            "target_top_n": contract.target_top_n,
            "school_filter": contract.school_filter,
            "aggregation": contract.aggregation,
            "original": question,
            "revision_restore": {
                "restored_from_revision": contract.restored_from_revision,
                "created_revision": contract.revision,
                "parent_revision": contract.parent_revision,
            },
        }
        if contract.metric_type == "question":
            routed["metric"] = dict(contract.metric_spec or {})
        return routed

    def active_pending(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.store.active_pending(session_id)

    @staticmethod
    def context_from_pending(pending: Dict[str, Any]) -> Dict[str, Any]:
        context = dict(pending.get("context") or {})
        context.update({
            "pending_clarification_active": True,
            "pending_clarification_id": pending.get("pending_id"),
            "pending_clarification_turn_id": pending.get("turn_id"),
            "pending_original_question": pending.get("original_question"),
            "pending_clarification_reason": pending.get("reason"),
            "pending_provenance": pending.get("provenance") or {},
        })
        return context

    def open_pending(
        self,
        session_id: str,
        question: str,
        routed: Dict[str, Any],
        *,
        reason: Optional[str] = None,
        missing: Optional[list[str]] = None,
        base_revision: Optional[int] = None,
        request_id: Optional[str] = None,
        inherited_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        focus = self.store.latest_focus(session_id)
        context = self.merge_pending_context(inherited_context or {}, routed.get("pending_context") or {})
        values = dict(routed.get("pending_values") or {})
        pending_turn = str(uuid.uuid4())
        provenance = {}
        for field, specification in values.items():
            if isinstance(specification, dict) and "value" in specification:
                value = specification.get("value")
                evidence = specification.get("evidence_span")
            else:
                value = specification
                evidence = str(value) if value is not None and str(value) in question else None
            provenance[field] = {
                "value": value,
                "origin": "user_explicit",
                "source_turn": pending_turn,
                "evidence_span": evidence,
            }
        try:
            return self.store.create_pending(
                session_id=session_id,
                reason=reason if reason is not None else routed.get("reason"),
                missing=list(missing if missing is not None else (routed.get("missing") or [])),
                original_question=question,
                routed=routed,
                context=context,
                provenance=provenance,
                base_task_id=focus.task_id if focus else None,
                base_revision=focus.revision if focus else None,
                turn_id=pending_turn,
                expected_head_revision=base_revision,
            )
        except StaleBaseRevision as exc:
            self._record_state_conflict(session_id, request_id or pending_turn, exc)
            raise

    @staticmethod
    def merge_pending_context(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        """Keep a clarification self-contained without borrowing another task."""
        incompatible = any(
            patch.get(key) is not None and patch.get(key) != base.get(key)
            for key in ("last_school", "last_region", "last_school_compare_a", "last_school_compare_b",
                        "last_region_compare_a", "last_region_compare_b")
        )
        context = {} if incompatible else {
            key: copy.deepcopy(value) for key, value in base.items()
            if key.startswith("last_")
        }
        context.update(copy.deepcopy(patch))
        return context

    def close_pending(
        self, pending_id: str, *, status: str, resolution: str,
        base_revision: Optional[int] = None, request_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            return self.store.close_pending(
                pending_id, status=status, resolution=resolution,
                expected_head_revision=base_revision,
            )
        except StaleBaseRevision as exc:
            if session_id is not None:
                self._record_state_conflict(session_id, request_id or str(uuid.uuid4()), exc)
            raise

    def prepare_region(
        self,
        session_id: str,
        question: str,
        routed: Dict[str, Any],
        *,
        base_revision: Optional[int] = None,
        request_id: str,
        operation: str = "region_evaluation",
    ) -> AnalysisContract:
        if operation not in {"region_evaluation", "region_ranking"}:
            raise CapabilityError(f"unsupported_region_contract_operation:{operation}")
        aggregation = routed.get("aggregation") or "school_equal_mean"
        statistic = routed.get("target_stat")
        school_filter = routed.get("school_filter")
        validate_region_request(statistic=statistic, aggregation=aggregation, school_filter=school_filter)
        if statistic in ("median", "mean_and_median") and aggregation != "school_equal_mean":
            raise CapabilityError("unsupported_region_median_aggregation")
        target_dims = tuple(routed.get("target_dimensions") or ())
        is_pair_comp = len(target_dims) == 2 and any(k in question for k in ("比较", "前者减后者", "画图", "分差", "两块", "两整块", "分别", "各得多少分"))
        if len(target_dims) > 1 and not is_pair_comp:
            raise CapabilityError("unsupported_multi_dimension:多维并列查询尚未纳入首批强制契约能力")
        if is_pair_comp and (aggregation != "school_equal_mean" or statistic not in {None, "mean"} or routed.get("metric")):
            raise CapabilityError("unsupported_region_dimension_pair_scope")
        with closing(self._connect()) as conn:
            region = detect_region(routed.get("region") or "", connection=conn)
        if not region:
            raise CapabilityError("unresolved_region")
        period = str(routed.get("year") or "").strip()
        if not period:
            raise CapabilityError("missing_period")
        head = self.store.latest(session_id)
        previous_focus = self.store.latest_focus(session_id)
        previous_period = previous_focus
        compatible_operations = {operation}
        if operation in {"region_evaluation", "region_ranking"}:
            compatible_operations.update({"region_evaluation", "region_ranking"})
        previous = (
            previous_focus
            if previous_focus is not None and previous_focus.operation in compatible_operations
            else None
        )
        current_revision = head.revision if head else 0
        if base_revision is not None and base_revision != current_revision:
            attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=base_revision,
                current_revision=current_revision,
            )
            raise StaleBaseRevision(base_revision, current_revision, attempt_id=attempt_id)
        turn_id = str(uuid.uuid4())
        dimension = None if is_pair_comp else routed.get("target_dimension")
        metric_info = dict(routed.get("metric") or {})
        metric_type = metric_key = metric_name = None
        metric_spec = None
        if metric_info:
            metric_type, metric_key, indicators, metric_spec = _school_fact_spec(metric_info)
            if metric_type != "question" or len(indicators) != 1:
                raise CapabilityError("unsupported_region_item_metric")
            if dimension is not None or statistic not in {None, "mean", "median", "mean_and_median"}:
                raise CapabilityError("unsupported_region_item_scope")
            if aggregation != "school_equal_mean":
                raise CapabilityError("unsupported_region_item_aggregation")
            metric_name = str(metric_info.get("name") or metric_key)
            if statistic is None:
                statistic = "mean"
        else:
            indicators = (
                tuple(dict.fromkeys(qid for name in target_dims for qid in self._indicator_ids(name)))
                if is_pair_comp else self._indicator_ids(dimension)
            )
        requested_statistics = (
            ("mean", "median") if statistic == "mean_and_median"
            else (("max", "min") if statistic in ("min_and_max", "max_and_min")
            else ((statistic or "mean"),))
        )
        pending_provenance = routed.get("pending_provenance") or {}
        target_rank = routed.get("target_rank")
        target_top_n = routed.get("target_top_n")
        ranking_evidence = _ranking_evidence_candidates(
            question,
            target_rank=target_rank,
            target_top_n=target_top_n,
        )
        draft = AnalysisContract(
            task_id=head.task_id if head else str(uuid.uuid4()),
            revision=0,
            parent_revision=head.revision if head else None,
            turn_id=turn_id,
            session_id=session_id,
            dataset_version=self._dataset_version(),
            catalogue_version=CATALOGUE_VERSION,
            resolver_version=RESOLVER_VERSION,
            entity_id=region["full_name"],
            region_keyword=region["full_name"],
            region_identity=_typed_region_identity(region),
            period=period,
            dimension=dimension,
            target_dimensions=target_dims if is_pair_comp else (),
            indicator_ids=indicators,
            statistic=statistic,
            target_rank=target_rank,
            target_top_n=target_top_n,
            aggregation=aggregation,
            school_filter=school_filter,
            requested_outputs=_requested_outputs(
                routed,
                structured_key=("ranking_result" if operation == "region_ranking" else "structured_result"),
                # A single-item fact has no implicit visualization contract.
                # Explicit chart requests remain honored through routed output
                # requirements, while plain item questions stay text-only.
                default_charts=metric_type != "question",
            ),
            requested_statistics=requested_statistics,
            operation=operation,
            value_view=routed.get("value_view") or "normalized_score",
            entity_type=("region_school_population" if operation == "region_ranking" else "region"),
            ranking_spec=(dict(REGION_RANKING_SPEC) if operation == "region_ranking" else None),
            metric_type=metric_type,
            metric_key=metric_key,
            metric_name=metric_name,
            metric_spec=metric_spec,
            sources={
                "entity_id": _source(
                    region["full_name"],
                    question,
                    turn_id,
                    previous,
                    "entity_id",
                    evidence_candidates=(str(routed.get("region") or ""), str(region.get("name") or "")),
                    pending_source=pending_provenance.get("entity_id"),
                ),
                "period": _source(
                    period,
                    question,
                    turn_id,
                    previous_period,
                    "period",
                    pending_source=pending_provenance.get("period"),
                ),
                "dimension": _source(
                    dimension,
                    question,
                    turn_id,
                    previous,
                    "dimension",
                    evidence_candidates=(
                        (("整体", "全部83项", "全部指标", "综合均分") if dimension is None else ())
                        + _semantic_evidence(routed, "dimension")
                    ),
                    pending_source=pending_provenance.get("dimension"),
                ),
                "statistic": _source(
                    statistic,
                    question,
                    turn_id,
                    previous,
                    "statistic",
                    evidence_candidates=(
                        _STATISTIC_EVIDENCE.get(statistic, ())
                        + _semantic_evidence(routed, "statistic")
                    ),
                    pending_source=pending_provenance.get("statistic"),
                ),
                "target_rank": _source(
                    target_rank,
                    question,
                    turn_id,
                    previous,
                    "target_rank",
                    evidence_candidates=ranking_evidence if target_rank is not None else (),
                    pending_source=pending_provenance.get("target_rank"),
                ),
                "target_top_n": _source(
                    target_top_n,
                    question,
                    turn_id,
                    previous,
                    "target_top_n",
                    evidence_candidates=ranking_evidence if target_top_n is not None else (),
                    pending_source=pending_provenance.get("target_top_n"),
                ),
                "aggregation": _source(
                    aggregation,
                    question,
                    turn_id,
                    previous,
                    "aggregation",
                    evidence_candidates=(
                        _AGGREGATION_EVIDENCE.get(aggregation, ())
                        + _semantic_evidence(routed, "aggregation")
                    ),
                    pending_source=pending_provenance.get("aggregation"),
                ),
                "school_filter": _source(
                    school_filter,
                    question,
                    turn_id,
                    previous,
                    "school_filter",
                    evidence_candidates=(
                        "取消筛选", "取消过滤", "不再筛选", "不再过滤", "去掉筛选", "去掉过滤",
                        "不限学段", "不限城乡", "全部学校", "所有学校",
                    )
                    if school_filter is None else (),
                    pending_source=pending_provenance.get("school_filter"),
                ),
                **({
                    "metric_key": FieldSource(
                        value=metric_key,
                        origin="catalogue_or_default",
                        source_turn=turn_id,
                        evidence_span=routed.get("metric_evidence_span"),
                    )
                } if metric_key else {}),
            },
        )
        try:
            return self.store.commit(draft, base_revision=current_revision)
        except StaleBaseRevision as exc:
            exc.attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=exc.requested,
                current_revision=exc.current,
            )
            raise

    def prepare_region_ranking(
        self,
        session_id: str,
        question: str,
        routed: Dict[str, Any],
        *,
        base_revision: Optional[int] = None,
        request_id: str,
    ) -> AnalysisContract:
        """Resolve and commit one-region, one-period deterministic school ranking."""
        target_rank = routed.get("target_rank")
        target_top_n = routed.get("target_top_n")
        if (target_rank is None) == (target_top_n is None):
            raise CapabilityError("invalid_region_ranking_target:请明确一个精准名次或前N名")
        if target_rank is not None and target_rank != -1 and target_rank < 1:
            raise CapabilityError("invalid_region_ranking_rank:名次必须为正整数或倒数第一")
        if target_top_n is not None and not 1 <= target_top_n <= 100:
            raise CapabilityError("invalid_region_ranking_top_n:首批仅支持前1至100名")
        if routed.get("target_dimension") or routed.get("target_dimensions"):
            raise CapabilityError("unsupported_region_ranking_dimension:首批区域排名仅支持整体综合分")
        routed_statistic = routed.get("target_stat")
        if routed_statistic not in {None, "mean", "max", "min"}:
            raise CapabilityError("unsupported_region_ranking_statistic:首批排名仅按学校综合均分排序")
        aggregation = routed.get("aggregation") or "school_equal_mean"
        if aggregation != "school_equal_mean":
            raise CapabilityError("unsupported_region_ranking_aggregation:区域学校排名固定按各校综合均分排序")
        school_filter = routed.get("school_filter")
        if school_filter and not filter_is_structured(school_filter):
            raise CapabilityError("unsupported_region_ranking_filter:排名仅支持可靠的学段或城乡筛选")
        normalized = dict(routed)
        normalized.update({
            "target_dimension": None,
            "target_dimensions": [],
            "target_stat": "mean",
            "aggregation": "school_equal_mean",
        })
        return self.prepare_region(
            session_id,
            question,
            normalized,
            base_revision=base_revision,
            request_id=request_id,
            operation="region_ranking",
        )

    def prepare_region_compare(
        self,
        session_id: str,
        question: str,
        routed: Dict[str, Any],
        *,
        base_revision: Optional[int] = None,
        request_id: str,
    ) -> AnalysisContract:
        """Resolve and commit a bounded two-region, one-period comparison."""
        aggregation = routed.get("aggregation") or "school_equal_mean"
        statistic = routed.get("target_stat")
        school_filter = routed.get("school_filter")
        validate_region_request(statistic=statistic, aggregation=aggregation, school_filter=school_filter)
        if statistic not in SUPPORTED_REGION_COMPARE_STATISTICS:
            raise CapabilityError(
                "unsupported_region_compare_statistic:双区域首批契约仅支持均值或学校数量比较"
            )
        if len(routed.get("target_dimensions") or ()) > 1:
            raise CapabilityError("unsupported_multi_dimension:双区域首批契约仅支持整体或单一维度")
        dimension = routed.get("target_dimension")
        if dimension and statistic == "count":
            raise CapabilityError(
                "unsupported_region_compare_dimension_count:学校数量比较不能同时限定问卷维度"
            )
        with closing(self._connect()) as conn:
            region_a = detect_region(routed.get("region_a") or "", connection=conn)
            region_b = detect_region(routed.get("region_b") or "", connection=conn)
        if not region_a or not region_b:
            raise CapabilityError("unresolved_region_compare_entity")
        if region_a["full_name"] == region_b["full_name"]:
            raise CapabilityError("duplicate_region_compare_entity")
        period = str(routed.get("year") or "").strip()
        if not period:
            raise CapabilityError("missing_period")

        head = self.store.latest(session_id)
        current_revision = head.revision if head else 0
        if base_revision is not None and base_revision != current_revision:
            attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=base_revision,
                current_revision=current_revision,
            )
            raise StaleBaseRevision(base_revision, current_revision, attempt_id=attempt_id)
        previous_focus = self.store.latest_focus(session_id)
        previous = (
            previous_focus
            if previous_focus is not None and previous_focus.operation == "region_compare"
            else None
        )
        turn_id = str(uuid.uuid4())
        indicators = self._indicator_ids(dimension)
        pending_provenance = routed.get("pending_provenance") or {}
        draft = AnalysisContract(
            task_id=head.task_id if head else str(uuid.uuid4()),
            revision=0,
            parent_revision=head.revision if head else None,
            turn_id=turn_id,
            session_id=session_id,
            dataset_version=self._dataset_version(),
            catalogue_version=CATALOGUE_VERSION,
            resolver_version=RESOLVER_VERSION,
            entity_id=region_a["full_name"],
            region_keyword=region_a["full_name"],
            secondary_entity_id=region_b["full_name"],
            secondary_region_keyword=region_b["full_name"],
            period=period,
            dimension=dimension,
            indicator_ids=indicators,
            statistic=statistic,
            target_rank=None,
            target_top_n=None,
            aggregation=aggregation,
            school_filter=school_filter,
            requested_outputs=_requested_outputs(
                routed, structured_key="structured_result", default_charts=True
            ),
            requested_statistics=tuple(dict.fromkeys(
                ((statistic or "mean"),) + tuple(
                    item for item in (routed.get("requested_result_statistics") or ())
                    if item in {"school_count", "school_count_a", "school_count_b", "difference"}
                )
            )),
            operation="region_compare",
            value_view="normalized_score",
            entity_type="region_pair",
            sources={
                "entity_id": _source(
                    region_a["full_name"], question, turn_id, previous, "entity_id",
                    evidence_candidates=(str(routed.get("region_a") or ""), region_a["name"]),
                    pending_source=pending_provenance.get("entity_id"),
                ),
                "secondary_entity_id": _source(
                    region_b["full_name"], question, turn_id, previous, "secondary_entity_id",
                    evidence_candidates=(str(routed.get("region_b") or ""), region_b["name"]),
                    pending_source=pending_provenance.get("secondary_entity_id"),
                ),
                "period": _source(
                    period, question, turn_id, previous, "period",
                    pending_source=pending_provenance.get("period"),
                ),
                "dimension": _source(
                    dimension, question, turn_id, previous, "dimension",
                    evidence_candidates=(
                        (("整体", "全部83项", "全部指标", "综合均分") if dimension is None else ())
                        + _semantic_evidence(routed, "dimension")
                    ),
                    pending_source=pending_provenance.get("dimension"),
                ),
                "statistic": _source(
                    statistic, question, turn_id, previous, "statistic",
                    evidence_candidates=(
                        _STATISTIC_EVIDENCE.get(statistic, ())
                        + _semantic_evidence(routed, "statistic")
                    ),
                    pending_source=pending_provenance.get("statistic"),
                ),
                "aggregation": _source(
                    aggregation, question, turn_id, previous, "aggregation",
                    evidence_candidates=(
                        _AGGREGATION_EVIDENCE.get(aggregation, ())
                        + _semantic_evidence(routed, "aggregation")
                    ),
                    pending_source=pending_provenance.get("aggregation"),
                ),
                "school_filter": _source(
                    school_filter, question, turn_id, previous, "school_filter",
                    evidence_candidates=(
                        "取消筛选", "取消过滤", "不再筛选", "不再过滤", "去掉筛选", "去掉过滤",
                        "不限学段", "不限城乡", "全部学校", "所有学校",
                    ) if school_filter is None else (),
                    pending_source=pending_provenance.get("school_filter"),
                ),
            },
        )
        try:
            return self.store.commit(draft, base_revision=current_revision)
        except StaleBaseRevision as exc:
            exc.attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=exc.requested,
                current_revision=exc.current,
            )
            raise

    def prepare_region_longitudinal(
        self,
        session_id: str,
        question: str,
        routed: Dict[str, Any],
        *,
        base_revision: Optional[int] = None,
        request_id: str,
    ) -> AnalysisContract:
        """Resolve and commit one region across two annual cross-sections."""
        aggregation = routed.get("aggregation") or "school_equal_mean"
        statistic = routed.get("target_stat") or "mean"
        school_filter = routed.get("school_filter")
        validate_region_request(
            statistic=statistic,
            aggregation=aggregation,
            school_filter=school_filter,
        )
        if statistic not in SUPPORTED_REGION_LONGITUDINAL_STATISTICS:
            raise CapabilityError(
                "unsupported_region_longitudinal_statistic:两期截面变化首批仅支持均值或整体有效学校数量"
            )
        if len(routed.get("target_dimensions") or ()) > 1:
            raise CapabilityError(
                "unsupported_region_longitudinal_multi_dimension:两期截面变化首批仅支持整体或单一维度"
            )
        dimension = routed.get("target_dimension")
        if dimension and statistic == "count":
            raise CapabilityError(
                "unsupported_region_longitudinal_dimension_count:有效学校数量不能同时限定问卷维度"
            )
        if routed.get("temporal_mode") not in {None, "repeated_cross_section"}:
            raise CapabilityError("unsupported_region_longitudinal_temporal_mode")

        with closing(self._connect()) as conn:
            region = detect_region(routed.get("region") or "", connection=conn)
        if not region:
            raise CapabilityError("unresolved_region_longitudinal_entity")
        raw_periods = (
            str(routed.get("year_a") or "").strip(),
            str(routed.get("year_b") or "").strip(),
        )
        if any(len(value) != 4 or not value.isdigit() or not value.startswith("20") for value in raw_periods):
            raise CapabilityError("invalid_region_longitudinal_period_format")
        period, secondary_period = raw_periods
        if period == secondary_period:
            raise CapabilityError("duplicate_region_longitudinal_period")
        if period > secondary_period:
            raise CapabilityError("invalid_region_longitudinal_period_order")

        head = self.store.latest(session_id)
        current_revision = head.revision if head else 0
        if base_revision is not None and base_revision != current_revision:
            attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=base_revision,
                current_revision=current_revision,
            )
            raise StaleBaseRevision(base_revision, current_revision, attempt_id=attempt_id)
        previous_focus = self.store.latest_focus(session_id)
        previous = (
            previous_focus
            if previous_focus is not None and previous_focus.operation == "region_longitudinal"
            else None
        )
        turn_id = str(uuid.uuid4())
        pending_provenance = routed.get("pending_provenance") or {}
        indicators = self._indicator_ids(dimension)
        temporal_mode = "repeated_cross_section"
        draft = AnalysisContract(
            task_id=head.task_id if head else str(uuid.uuid4()),
            revision=0,
            parent_revision=head.revision if head else None,
            turn_id=turn_id,
            session_id=session_id,
            dataset_version=self._dataset_version(),
            catalogue_version=CATALOGUE_VERSION,
            resolver_version=RESOLVER_VERSION,
            entity_id=region["full_name"],
            region_keyword=region["full_name"],
            period=period,
            secondary_period=secondary_period,
            temporal_mode=temporal_mode,
            dimension=dimension,
            indicator_ids=indicators,
            statistic=statistic,
            target_rank=None,
            target_top_n=None,
            aggregation=aggregation,
            school_filter=school_filter,
            requested_outputs=_requested_outputs(
                routed, structured_key="structured_result", default_charts=True
            ),
            requested_statistics=(statistic,),
            operation="region_longitudinal",
            value_view="normalized_score",
            entity_type="region_period_pair",
            sources={
                "entity_id": _source(
                    region["full_name"], question, turn_id, previous, "entity_id",
                    evidence_candidates=(str(routed.get("region") or ""), region["name"]),
                    pending_source=pending_provenance.get("entity_id"),
                ),
                "period": _source(
                    period, question, turn_id, previous, "period",
                    pending_source=pending_provenance.get("period"),
                ),
                "secondary_period": _source(
                    secondary_period, question, turn_id, previous, "secondary_period",
                    pending_source=pending_provenance.get("secondary_period"),
                ),
                "temporal_mode": _source(
                    temporal_mode, question, turn_id, previous, "temporal_mode",
                ),
                "dimension": _source(
                    dimension, question, turn_id, previous, "dimension",
                    evidence_candidates=(
                        (("整体", "全部83项", "全部指标", "综合均分") if dimension is None else ())
                        + _semantic_evidence(routed, "dimension")
                    ),
                    pending_source=pending_provenance.get("dimension"),
                ),
                "statistic": _source(
                    statistic, question, turn_id, previous, "statistic",
                    evidence_candidates=(
                        _STATISTIC_EVIDENCE.get(statistic, ())
                        + _semantic_evidence(routed, "statistic")
                    ),
                    pending_source=pending_provenance.get("statistic"),
                ),
                "aggregation": _source(
                    aggregation, question, turn_id, previous, "aggregation",
                    evidence_candidates=(
                        _AGGREGATION_EVIDENCE.get(aggregation, ())
                        + _semantic_evidence(routed, "aggregation")
                    ),
                    pending_source=pending_provenance.get("aggregation"),
                ),
                "school_filter": _source(
                    school_filter, question, turn_id, previous, "school_filter",
                    evidence_candidates=(
                        "取消筛选", "取消过滤", "不再筛选", "不再过滤", "去掉筛选", "去掉过滤",
                        "不限学段", "不限城乡", "全部学校", "所有学校",
                    ) if school_filter is None else (),
                    pending_source=pending_provenance.get("school_filter"),
                ),
            },
        )
        try:
            return self.store.commit(draft, base_revision=current_revision)
        except StaleBaseRevision as exc:
            exc.attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=exc.requested,
                current_revision=exc.current,
            )
            raise

    def prepare_school_compare(
        self,
        session_id: str,
        question: str,
        routed: Dict[str, Any],
        *,
        base_revision: Optional[int] = None,
        request_id: str,
    ) -> AnalysisContract:
        """Resolve and commit a bounded two-school, one-period template comparison."""
        period = str(routed.get("year") or "").strip()
        period_a = str(routed.get("year_a") or (period.split(",")[0] if "," in period else period)).strip()
        period_b = str(routed.get("year_b") or (period.split(",")[1] if "," in period else period)).strip()
        if not period_a or not period_b:
            raise CapabilityError("missing_school_compare_period")
        metric_type, metric_key, indicator_ids, metric_spec = _school_compare_template_spec()
        dimensions = tuple(routed.get("target_dimensions") or ())
        dimension = str(routed.get("target_dimension") or "").strip() or None
        known_dimensions = {str(item.get("name") or "") for item in metric_spec["dimensions"]}
        if len(dimensions) > 1 or (dimensions and dimensions[0] != dimension) or (dimension and dimension not in known_dimensions):
            raise CapabilityError("unsupported_school_compare_dimension")
        statistic = next(iter(SUPPORTED_SCHOOL_COMPARE_STATISTICS))
        aggregation = next(iter(SUPPORTED_SCHOOL_COMPARE_AGGREGATIONS))

        def resolve_school(token: Any, conn: sqlite3.Connection, label: str) -> Dict[str, Any]:
            raw = str(token or "").strip()
            target_period = period_a if label == "a" else period_b
            if isinstance(token, int) or raw.isdigit():
                school = get_school(int(token), connection=conn)
                if school and str(school.get("year") or "") != target_period:
                    school = None
            elif raw.startswith("id:"):
                school = get_school(int(raw.split(":", 1)[1]), connection=conn)
                if school and str(school.get("year") or "") != target_period:
                    school = None
            else:
                hits = find_schools(raw, year=target_period, limit=10, connection=conn)
                expanded = expand_school_alias(raw)
                exact = [
                    hit for hit in hits
                    if str(hit.get("school_name") or "") in {raw, expanded}
                ]
                if len(exact) == 1:
                    school = get_school(int(exact[0]["id"]), connection=conn)
                elif len(exact) > 1:
                    raise CapabilityError(f"ambiguous_school_compare_entity_{label}:{raw}:{target_period}")
                else:
                    if len(hits) > 1:
                        raise CapabilityError(f"ambiguous_school_compare_entity_{label}:{raw}:{target_period}")
                    school = get_school(int(hits[0]["id"]), connection=conn) if hits else None
            if not school:
                raise CapabilityError(f"unresolved_school_compare_entity_{label}:{raw}:{target_period}")
            return school

        with closing(self._connect()) as conn:
            school_a = resolve_school(routed.get("school_a"), conn, "a")
            school_b = resolve_school(routed.get("school_b"), conn, "b")
            marks = ",".join("?" for _ in indicator_ids)
            known_ids = {
                int(row[0])
                for row in conn.execute(
                    f"SELECT id FROM questions WHERE id IN ({marks})", indicator_ids
                ).fetchall()
            }
            if known_ids != set(indicator_ids):
                raise CapabilityError("unknown_school_compare_indicator")

        school_a_id = int(school_a["id"])
        school_b_id = int(school_b["id"])
        school_a_name = str(school_a.get("school_name") or "").strip()
        school_b_name = str(school_b.get("school_name") or "").strip()
        if school_a_id == school_b_id or (school_a_name == school_b_name and period_a == period_b):
            raise CapabilityError("duplicate_school_compare_entity")

        head = self.store.latest(session_id)
        current_revision = head.revision if head else 0
        if base_revision is not None and base_revision != current_revision:
            attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=base_revision,
                current_revision=current_revision,
            )
            raise StaleBaseRevision(base_revision, current_revision, attempt_id=attempt_id)
        previous_focus = self.store.latest_focus(session_id)
        previous = (
            previous_focus
            if previous_focus is not None and previous_focus.operation == "school_compare"
            else None
        )
        turn_id = str(uuid.uuid4())
        pending_provenance = routed.get("pending_provenance") or {}
        draft = AnalysisContract(
            task_id=head.task_id if head else str(uuid.uuid4()),
            revision=0,
            parent_revision=head.revision if head else None,
            turn_id=turn_id,
            session_id=session_id,
            dataset_version=self._dataset_version(),
            catalogue_version=CATALOGUE_VERSION,
            resolver_version=RESOLVER_VERSION,
            entity_id=school_a_name,
            secondary_entity_id=school_b_name,
            region_keyword="",
            period=period_a,
            secondary_period=period_b if period_a != period_b else None,
            dimension=dimension,
            indicator_ids=indicator_ids,
            statistic=statistic,
            target_rank=None,
            target_top_n=None,
            aggregation=aggregation,
            school_filter=None,
            requested_outputs=_requested_outputs(
                routed, structured_key="structured_result", default_charts=True
            ),
            requested_statistics=(statistic,),
            operation="school_compare",
            value_view="normalized_score",
            entity_type="school_pair",
            school_id=school_a_id,
            secondary_school_id=school_b_id,
            metric_type=metric_type,
            metric_key=metric_key,
            metric_name=str(metric_spec.get("name") or metric_key),
            metric_spec=metric_spec,
            null_policy="require_complete_template_no_nulls",
            sources={
                "entity_id": _source(
                    school_a_name, question, turn_id, previous, "entity_id",
                    evidence_candidates=(str(routed.get("school_a") or ""),),
                    pending_source=pending_provenance.get("entity_id"),
                ),
                "secondary_entity_id": _source(
                    school_b_name, question, turn_id, previous, "secondary_entity_id",
                    evidence_candidates=(str(routed.get("school_b") or ""),),
                    pending_source=pending_provenance.get("secondary_entity_id"),
                ),
                "period": _source(
                    period_a, question, turn_id, previous, "period",
                    pending_source=pending_provenance.get("period"),
                ),
                **({"secondary_period": _source(
                    period_b, question, turn_id, previous, "secondary_period",
                    pending_source=pending_provenance.get("secondary_period"),
                )} if period_a != period_b else {}),
                "metric_key": FieldSource(
                    value=metric_key,
                    origin="catalogue_or_default",
                    source_turn=turn_id,
                ),
                "statistic": FieldSource(
                    value=statistic,
                    origin="catalogue_or_default",
                    source_turn=turn_id,
                ),
                "aggregation": FieldSource(
                    value=aggregation,
                    origin="catalogue_or_default",
                    source_turn=turn_id,
                ),
                **({"dimension": _source(
                    dimension, question, turn_id, previous, "dimension",
                    evidence_candidates=(str((routed.get("semantic_evidence") or {}).get("dimension") or dimension),),
                    pending_source=pending_provenance.get("dimension"),
                )} if dimension else {}),
            },
        )
        try:
            return self.store.commit(draft, base_revision=current_revision)
        except StaleBaseRevision as exc:
            exc.attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=exc.requested,
                current_revision=exc.current,
            )
            raise

    def prepare_school_evaluation(
        self,
        session_id: str,
        question: str,
        routed: Dict[str, Any],
        *,
        base_revision: Optional[int] = None,
        request_id: str,
    ) -> AnalysisContract:
        """Resolve and commit one-school overall or one-dimension evaluation."""
        routed_years = list(routed.get("years") or ())
        period = str(
            routed.get("year") or (routed_years[0] if routed_years else "")
        ).strip()
        if not period:
            raise CapabilityError("missing_school_evaluation_period")
        target_dims = tuple(routed.get("target_dimensions") or ())
        pair_dims = target_dims if len(target_dims) == 2 else ()
        if len(target_dims) > 2 or len(set(target_dims)) != len(target_dims):
            raise CapabilityError("unsupported_school_dimension_set")
        dimension = None if pair_dims else str(routed.get("target_dimension") or "").strip() or None
        metric_type, metric_key, indicator_ids, metric_spec = (
            _school_evaluation_template_spec(dimension, pair_dims)
        )
        statistic = next(iter(SUPPORTED_SCHOOL_COMPARE_STATISTICS))
        aggregation = next(iter(SUPPORTED_SCHOOL_COMPARE_AGGREGATIONS))
        with closing(self._connect()) as conn:
            school = _resolve_school_for_period(
                routed.get("school"),
                period,
                conn,
                error_prefix="school_evaluation_entity",
            )
            marks = ",".join("?" for _ in indicator_ids)
            known_ids = {
                int(row[0])
                for row in conn.execute(
                    f"SELECT id FROM questions WHERE id IN ({marks})", indicator_ids
                ).fetchall()
            }
            if known_ids != set(indicator_ids):
                raise CapabilityError("unknown_school_evaluation_indicator")

        head = self.store.latest(session_id)
        current_revision = head.revision if head else 0
        if base_revision is not None and base_revision != current_revision:
            attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=base_revision,
                current_revision=current_revision,
            )
            raise StaleBaseRevision(base_revision, current_revision, attempt_id=attempt_id)
        previous_focus = self.store.latest_focus(session_id)
        previous = (
            previous_focus
            if previous_focus is not None
            and previous_focus.operation in {"school_evaluation", "school_fact"}
            else None
        )
        school_id = int(school["id"])
        school_name = str(school.get("school_name") or "").strip()
        turn_id = str(uuid.uuid4())
        pending_provenance = routed.get("pending_provenance") or {}
        draft = AnalysisContract(
            task_id=head.task_id if head else str(uuid.uuid4()),
            revision=0,
            parent_revision=head.revision if head else None,
            turn_id=turn_id,
            session_id=session_id,
            dataset_version=self._dataset_version(),
            catalogue_version=CATALOGUE_VERSION,
            resolver_version=RESOLVER_VERSION,
            entity_id=school_name,
            region_keyword="",
            period=period,
            dimension=dimension,
            target_dimensions=pair_dims,
            indicator_ids=indicator_ids,
            statistic=statistic,
            target_rank=None,
            target_top_n=None,
            aggregation=aggregation,
            school_filter=None,
            requested_outputs=_requested_outputs(
                routed, structured_key="structured_result", default_charts=False
            ),
            requested_statistics=tuple(dict.fromkeys(
                (statistic,) + tuple(
                    item for item in (routed.get("requested_result_statistics") or ())
                    if item in {"dimension_scores", "dimension_extrema"}
                )
            )),
            operation="school_evaluation",
            value_view="normalized_score",
            entity_type="school",
            school_id=school_id,
            metric_type=metric_type,
            metric_key=metric_key,
            metric_name=str(metric_spec.get("name") or metric_key),
            metric_spec=metric_spec,
            sources={
                "entity_id": _source(
                    school_name,
                    question,
                    turn_id,
                    previous,
                    "entity_id",
                    evidence_candidates=(str(routed.get("school") or ""),),
                    pending_source=pending_provenance.get("entity_id"),
                ),
                "period": _source(
                    period,
                    question,
                    turn_id,
                    previous,
                    "period",
                    pending_source=pending_provenance.get("period"),
                ),
                "dimension": _source(
                    dimension,
                    question,
                    turn_id,
                    previous,
                    "dimension",
                    evidence_candidates=(
                        (("总体", "整体", "综合") if dimension is None else ())
                        + _semantic_evidence(routed, "dimension")
                    ),
                    pending_source=pending_provenance.get("dimension"),
                ),
                "metric_key": FieldSource(
                    value=metric_key,
                    origin="catalogue_or_default",
                    source_turn=turn_id,
                ),
                "statistic": FieldSource(
                    value=statistic,
                    origin="catalogue_or_default",
                    source_turn=turn_id,
                ),
                "aggregation": FieldSource(
                    value=aggregation,
                    origin="catalogue_or_default",
                    source_turn=turn_id,
                ),
            },
        )
        try:
            return self.store.commit(draft, base_revision=current_revision)
        except StaleBaseRevision as exc:
            exc.attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=exc.requested,
                current_revision=exc.current,
            )
            raise

    def prepare_school_fact(
        self,
        session_id: str,
        question: str,
        routed: Dict[str, Any],
        *,
        base_revision: Optional[int] = None,
        request_id: str,
    ) -> AnalysisContract:
        """Resolve and commit a school fact query before the fact engine executes."""
        metric_info = school_fact_metric_input(routed)
        metric_type, metric_key, indicator_ids, metric_spec = _school_fact_spec(metric_info)
        head = self.store.latest(session_id)
        previous = self.store.latest_focus(session_id)
        current_revision = head.revision if head else 0
        if base_revision is not None and base_revision != current_revision:
            attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=base_revision,
                current_revision=current_revision,
            )
            raise StaleBaseRevision(base_revision, current_revision, attempt_id=attempt_id)

        school_token = routed.get("school")
        period_hint = str(routed.get("year") or "").strip() or None
        period_only_followup = bool(routed.get("period_only_followup"))
        with closing(self._connect()) as conn:
            if isinstance(school_token, int) or str(school_token or "").isdigit():
                school = get_school(int(school_token), connection=conn)
            elif str(school_token or "").startswith("id:"):
                school = get_school(int(str(school_token).split(":", 1)[1]), connection=conn)
            else:
                raw_school = str(school_token or "").strip()
                hits = find_schools(raw_school, year=period_hint, limit=20, connection=conn)
                expanded_school = expand_school_alias(raw_school)
                exact = [
                    hit for hit in hits
                    if str(hit.get("school_name") or "").strip() in {
                        raw_school,
                        expanded_school,
                    }
                ]
                if len(exact) == 1:
                    school = get_school(int(exact[0]["id"]), connection=conn)
                elif len(exact) > 1 or len(hits) > 1:
                    candidates = [
                        {
                            "school_id": int(hit.get("id") or 0),
                            "school_name": str(hit.get("school_name") or "").strip(),
                            "year": str(hit.get("year") or "").strip(),
                        }
                        for hit in hits[:8]
                    ]
                    raise CapabilityError(
                        (
                            "ambiguous_inherited_school_fact_period:"
                            if period_only_followup
                            else "ambiguous_school_fact_entity:"
                        )
                        + raw_school
                        + ":"
                        + json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))
                    )
                else:
                    school = get_school(int(hits[0]["id"]), connection=conn) if hits else None
            if indicator_ids:
                marks = ",".join("?" for _ in indicator_ids)
                known_ids = {
                    int(row[0])
                    for row in conn.execute(
                        f"SELECT id FROM questions WHERE id IN ({marks})", indicator_ids
                    ).fetchall()
                }
                if known_ids != set(indicator_ids):
                    raise CapabilityError("unknown_school_fact_indicator")
        if (
            school
            and period_hint
            and (
                isinstance(school_token, int)
                or str(school_token or "").isdigit()
                or str(school_token or "").startswith("id:")
            )
            and str(school.get("year") or "").strip() != period_hint
        ):
            raise CapabilityError(
                f"school_fact_id_period_mismatch:{school_token}:{period_hint}"
            )
        if not school:
            if period_only_followup:
                raise CapabilityError(
                    f"unresolved_inherited_school_fact_period:{school_token}:{period_hint or ''}"
                )
            raise CapabilityError(
                f"unresolved_school_fact_entity:{school_token}"
                + (f":{period_hint}" if period_hint else "")
            )

        school_id = int(school["id"])
        school_name = str(school.get("school_name") or "").strip()
        period = str(school.get("year") or period_hint or "").strip()
        if not school_name or not period:
            raise CapabilityError("incomplete_school_fact_entity")
        turn_id = str(uuid.uuid4())
        previous_school_scope = (
            previous
            if previous is not None
            and previous.operation in {"school_evaluation", "school_fact"}
            else None
        )
        previous_school = (
            previous if previous is not None and previous.operation == "school_fact" else None
        )
        metric_name = str(metric_info.get("name") or metric_key)
        pending_provenance = routed.get("pending_provenance") or {}
        draft = AnalysisContract(
            task_id=head.task_id if head else str(uuid.uuid4()),
            revision=0,
            parent_revision=head.revision if head else None,
            turn_id=turn_id,
            session_id=session_id,
            dataset_version=self._dataset_version(),
            catalogue_version=CATALOGUE_VERSION,
            resolver_version=RESOLVER_VERSION,
            entity_id=school_name,
            region_keyword="",
            period=period,
            dimension=None,
            indicator_ids=indicator_ids,
            statistic=None,
            target_rank=None,
            target_top_n=None,
            aggregation="not_applicable",
            school_filter=None,
            requested_outputs=_requested_outputs(
                routed, structured_key="structured_result", default_charts=False
            ),
            requested_statistics=(),
            operation="school_fact",
            value_view=str(routed.get("value_view") or "raw_value"),
            entity_type="school",
            school_id=school_id,
            metric_type=metric_type,
            metric_key=metric_key,
            metric_name=metric_name,
            metric_spec=metric_spec,
            sources={
                "entity_id": _source(
                    school_name,
                    question,
                    turn_id,
                    previous_school_scope,
                    "entity_id",
                    evidence_candidates=(
                        ()
                        if routed.get("school_inherited")
                        else (str(school_token or ""),)
                    ),
                    pending_source=pending_provenance.get("entity_id"),
                ),
                "period": _source(
                    period,
                    question,
                    turn_id,
                    previous_school_scope,
                    "period",
                    pending_source=pending_provenance.get("period"),
                ),
                "metric_key": (
                    FieldSource(
                        value=metric_key,
                        origin="inherited",
                        source_turn=turn_id,
                        inherited_from=previous_school.revision,
                    )
                    if routed.get("metric_inherited")
                    and previous_school is not None
                    and previous_school.metric_key == metric_key
                    else FieldSource(
                        value=metric_key,
                        origin="user_explicit",
                        source_turn=str(routed.get("metric_source_turn") or turn_id),
                        evidence_span=str(routed.get("metric_evidence_span") or question.strip() or metric_name),
                    )
                ),
            },
        )
        try:
            return self.store.commit(draft, base_revision=current_revision)
        except StaleBaseRevision as exc:
            exc.attempt_id = self.store.record_revision_conflict(
                request_id=request_id,
                session_id=session_id,
                task_id=head.task_id if head else None,
                requested_base_revision=exc.requested,
                current_revision=exc.current,
            )
            raise

    def record_failure(
        self,
        contract: AnalysisContract,
        reason: str,
        *,
        status: str = "ERROR",
        verification_status: Optional[str] = None,
    ) -> None:
        self._record_execution(
            contract.task_id,
            contract.revision,
            execution_id=None,
            business_status=status,
            verification_status=(
                verification_status
                or ("NOT_APPLICABLE" if status in ("NO_DATA", "CLARIFY") else "NOT_CHECKED")
            ),
            failures=(reason,),
        )

    @contextmanager
    def consistent_read(self, contract: AnalysisContract):
        """Pin execution and independent replay to one SQLite read transaction."""
        conn = self._connect()
        snapshot = ConsistentReadSnapshot(connection=conn, snapshot_id=str(uuid.uuid4()))
        try:
            conn.set_trace_callback(snapshot.capture_sql)
            _test_gate(
                "before_snapshot",
                snapshot,
                expected_dataset_version=contract.dataset_version,
                data_path=self._data_path,
            )
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            snapshot.mark("transaction_started")
            # Establish the snapshot before checking the file fingerprint. A change
            # before acquisition is rejected; a later commit cannot change this view.
            conn.execute("SELECT name FROM sqlite_schema ORDER BY name LIMIT 1").fetchone()
            observed_version = self._dataset_version()
            if observed_version != contract.dataset_version:
                raise RuntimeError(
                    "consistent_read_dataset_changed_before_execution:"
                    f"expected={contract.dataset_version};observed={observed_version}"
                )
            snapshot.dataset_version = observed_version
            snapshot.sqlite_data_version = int(conn.execute("PRAGMA data_version").fetchone()[0])
            snapshot.mark("snapshot_established")
            yield snapshot
        finally:
            conn.set_trace_callback(None)
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def bind_and_verify(
        self,
        contract: AnalysisContract,
        result: Dict[str, Any],
        *,
        read_snapshot: Optional[ConsistentReadSnapshot] = None,
        request_id: Optional[str] = None,
        answer_spec: Optional[Dict[str, Any]] = None,
        verify_execution: bool = True,
    ) -> Dict[str, Any]:
        trusted = self.store.trusted(contract.task_id, contract.revision)
        if not verify_execution:
            # A1 retains the committed task, consistent-read tool call and
            # public AnswerSpec projection. Binding/replay are not evaluated.
            # An unchecked execution must never be recorded as PASS.
            trusted.validate()
            if read_snapshot is None or not read_snapshot.connection.in_transaction:
                raise ValueError("consistent_read_snapshot_required")
            if answer_spec is not None:
                from packages.semantic_query.answer_scope import project_result_scope, project_report_scope, render_scoped_markdown
                public = project_result_scope(result, answer_spec)
                project_report_scope({}, public, answer_spec)
                render_scoped_markdown(public, answer_spec)
            execution_id = str(result.get("_id") or uuid.uuid4())
            result["contract"] = trusted.to_dict()
            result["execution_receipt"] = {
                "execution_id": execution_id,
                "contract_revision": trusted.revision,
                "dataset_version": read_snapshot.dataset_version,
                "snapshot_id": read_snapshot.snapshot_id,
                "read_consistency": "same_snapshot_unverified_result",
            }
            result["contract_verification"] = {
                "status": "NOT_CHECKED_ABLATION_A1",
                "checks": [
                    {"name": "task_binding", "status": "NOT_CHECKED"},
                    {"name": "independent_replay", "status": "NOT_CHECKED"},
                ],
                "failures": [],
            }
            result["contract_mode"] = "experiment_execution_unverified"
            result["contract_store"] = {
                "status": "available", "file": os.path.basename(str(self.store.path)),
                "source": self.store.path_source, "paths_redacted": True,
            }
            self._record_execution(
                trusted.task_id, trusted.revision, execution_id=execution_id,
                business_status=str(result.get("fact_data_status") or "ANSWER"),
                verification_status="NOT_CHECKED", failures=(),
            )
            return result
        trusted, result, test_control = _controlled_school_fact_tamper(
            trusted,
            result,
            request_id=request_id,
        )
        trusted, result, school_compare_test_control = _controlled_school_compare_tamper(
            trusted,
            result,
            request_id=request_id,
        )
        if school_compare_test_control is not None:
            test_control = school_compare_test_control
        trusted, result, longitudinal_test_control = _controlled_region_longitudinal_tamper(
            trusted,
            result,
            request_id=request_id,
        )
        if longitudinal_test_control is not None:
            test_control = longitudinal_test_control
        trusted, result, ranking_test_control = _controlled_region_ranking_tamper(
            trusted,
            result,
            request_id=request_id,
        )
        if ranking_test_control is not None:
            test_control = ranking_test_control
        trusted, result, n3_test_control = _controlled_n3_tamper(
            trusted,
            result,
            request_id=request_id,
        )
        if n3_test_control is not None:
            test_control = n3_test_control
        try:
            failures, receipt, checks = self._verify(
                trusted,
                result,
                read_snapshot=read_snapshot,
            )
        except ValueError as exc:
            if test_control is not None:
                failure = f"contract_validation:{exc}"
                self._record_execution(
                    trusted.task_id,
                    trusted.revision,
                    execution_id=str(result.get("_id") or uuid.uuid4()),
                    business_status="ERROR",
                    verification_status="FAIL",
                    failures=(failure,),
                )
                raise ContractViolation(
                    (failure,),
                    test_control=test_control,
                ) from exc
            raise
        # Publication binding is part of this request's acceptance, before the
        # atomic trusted-focus / Pending completion write. A rejected public
        # view must never resolve a clarification or become inherited focus.
        result["contract"] = trusted.to_dict()
        result["execution_receipt"] = receipt.to_dict()
        result["contract_verification"] = {"status": "PASS" if not failures else "FAIL", "checks": checks, "failures": list(failures)}
        result["contract_mode"] = "enforced"
        if not failures and answer_spec is not None:
            from packages.semantic_query.answer_scope import project_result_scope, project_report_scope, render_scoped_markdown
            try:
                public = project_result_scope(result, answer_spec)
                project_report_scope({}, public, answer_spec)
                render_scoped_markdown(public, answer_spec)
            except ValueError as exc:
                failures = tuple(failures) + (str(exc),)
        status = "FAIL" if failures else "PASS"
        business_status = str(result.get("fact_data_status") or "ANSWER")
        self._record_execution(
            trusted.task_id,
            trusted.revision,
            execution_id=receipt.execution_id,
            business_status=business_status if not failures else "ERROR",
            verification_status=status,
            failures=failures,
        )
        if failures:
            raise ContractViolation(failures, test_control=test_control)
        result["contract"] = trusted.to_dict()
        result["contract_store"] = {
            "status": "available",
            "file": os.path.basename(str(self.store.path)),
            "source": self.store.path_source,
            "paths_redacted": True,
        }
        result["execution_receipt"] = receipt.to_dict()
        result["contract_verification"] = {"status": "PASS", "checks": checks, "failures": []}
        result["contract_mode"] = "enforced"
        if test_control is not None:
            result["contract_test_control"] = test_control
        return result

    @staticmethod
    def controlled_test_gate(stage: str, snapshot: ConsistentReadSnapshot) -> None:
        _test_gate(stage, snapshot)

    def _verify(
        self,
        contract: AnalysisContract,
        result: Dict[str, Any],
        *,
        read_snapshot: Optional[ConsistentReadSnapshot],
    ):
        contract.validate()
        if contract.operation == "school_evaluation":
            return self._verify_school_evaluation(contract, result, read_snapshot=read_snapshot)
        if contract.operation == "school_fact":
            return self._verify_school_fact(contract, result, read_snapshot=read_snapshot)
        if contract.operation == "region_compare":
            return self._verify_region_compare(contract, result, read_snapshot=read_snapshot)
        if contract.operation == "region_longitudinal":
            return self._verify_region_longitudinal(
                contract,
                result,
                read_snapshot=read_snapshot,
            )
        if contract.operation == "school_compare":
            return self._verify_school_compare(contract, result, read_snapshot=read_snapshot)
        failures = []
        checks = []
        snapshot_failures = []
        if read_snapshot is None:
            snapshot_failures.append("missing_consistent_read_snapshot")
        else:
            if read_snapshot.dataset_version != contract.dataset_version:
                snapshot_failures.append("snapshot_dataset_version")
            if not read_snapshot.connection.in_transaction:
                snapshot_failures.append("snapshot_transaction_inactive")
        failures.extend(snapshot_failures)
        checks.append({"name": "consistent_read_snapshot", "status": "FAIL" if snapshot_failures else "PASS"})
        if read_snapshot is None or read_snapshot.dataset_version != contract.dataset_version:
            failures.append("binding_dataset_version")
        if result.get("object_type") != "region":
            failures.append("binding_object_type")
        if result.get("analysis_operation") != contract.operation:
            failures.append("binding_operation")
        if result.get("region_name") != contract.entity_id:
            failures.append("binding_region")
        if str(result.get("year")) != contract.period:
            failures.append("binding_period")
        if result.get("aggregation") != contract.aggregation:
            failures.append("binding_aggregation")
        if result.get("school_filter") != contract.school_filter:
            failures.append("binding_filter")
        if result.get("target_stat") != contract.statistic:
            failures.append("binding_statistic")
        if result.get("target_dimension") != contract.dimension:
            failures.append("binding_dimension")
        if result.get("target_rank") != contract.target_rank:
            failures.append("binding_target_rank")
        if result.get("target_top_n") != contract.target_top_n:
            failures.append("binding_target_top_n")
        if contract.metric_type == "question":
            if result.get("metric_key") != contract.metric_key:
                failures.append("binding_metric_key")
            if result.get("metric_name") != contract.metric_name:
                failures.append("binding_metric_name")
            if result.get("value_view") != contract.value_view:
                failures.append("binding_value_view")
        execution_scope = result.get("execution_scope") or {}
        expected_scope = (
            {
                "query_template": "region_ranking_v1",
                "region": contract.entity_id,
                "period": contract.period,
                "dimension": None,
                "statistic": "mean",
                "aggregation": "school_equal_mean",
                "school_filter": contract.school_filter,
                "target_rank": contract.target_rank,
                "target_top_n": contract.target_top_n,
                "indicator_ids": list(contract.indicator_ids),
                "ranking_spec": dict(REGION_RANKING_SPEC),
            }
            if contract.operation == "region_ranking"
            else ({
                "query_template": "region_item_mean_v1",
                "region": contract.entity_id,
                "period": contract.period,
                "dimension": None,
                "statistic": contract.statistic,
                "aggregation": "school_equal_mean",
                "school_filter": contract.school_filter,
                "indicator_ids": list(contract.indicator_ids),
            } if contract.metric_type == "question" else {
                "query_template": "region_evaluation_v2",
                "region": contract.entity_id,
                "period": contract.period,
                "dimension": contract.dimension,
                "statistic": contract.statistic,
                "aggregation": contract.aggregation,
                "school_filter": contract.school_filter,
                **({"dimensions": list(contract.target_dimensions), "indicator_ids": list(contract.indicator_ids)} if contract.target_dimensions else {}),
            })
        )
        if execution_scope != expected_scope:
            failures.append("binding_execution_scope")
        if contract.operation == "region_ranking":
            if tuple(contract.indicator_ids) != self._indicator_ids(
                None,
                connection=read_snapshot.connection if read_snapshot is not None else None,
            ):
                failures.append("binding_indicator_ids")
            if contract.target_rank is None and result.get("target_school") is not None:
                failures.append("binding_unexpected_target_school")
            if result.get("ranking_spec") != contract.ranking_spec:
                failures.append("binding_ranking_spec")
            population = result.get("ranking_population") or {}
            if int(population.get("eligible_school_count") or 0) != int(result.get("school_count") or 0):
                failures.append("binding_ranking_population_school_count")
            if int(population.get("unfiltered_school_count") or 0) != int(result.get("total_unfiltered_count") or 0):
                failures.append("binding_ranking_population_unfiltered_count")
            if int(population.get("answer_count") or 0) != int(result.get("answer_count") or 0):
                failures.append("binding_ranking_population_answer_count")
            if population.get("school_filter") != contract.school_filter:
                failures.append("binding_ranking_population_filter")
        elif contract.metric_type == "question":
            metric_spec = dict(contract.metric_spec or {})
            if tuple(contract.indicator_ids) != (int(metric_spec.get("qid") or -1),):
                failures.append("binding_indicator_ids")
            if contract.metric_key != f"question:{metric_spec.get('qid')}":
                failures.append("binding_contract_metric_key")
        checks.append({"name": "contract_binding", "status": "FAIL" if failures else "PASS"})

        coverage_failures = []
        direct_answer = str(result.get("direct_answer") or "")
        if not direct_answer:
            coverage_failures.append("missing_direct_answer")
        if contract.statistic in ("median", "mean_and_median") and "中位" not in direct_answer:
            coverage_failures.append("missing_requested_median")
        if contract.statistic == "count" and result.get("school_count") is None:
            coverage_failures.append("missing_requested_count")
        if contract.dimension and result.get("target_dimension_score") is None:
            coverage_failures.append("missing_requested_dimension")
        if contract.target_dimensions:
            details = result.get("target_dimensions_detail") or []
            if [row.get("dimension") for row in details] != list(contract.target_dimensions):
                coverage_failures.append("missing_requested_dimension_pair")
            if any(row.get("score") is None for row in details) or result.get("diff") is None:
                coverage_failures.append("missing_requested_dimension_pair_score")
        if contract.metric_type == "question" and result.get("total_score") is None:
            coverage_failures.append("missing_requested_item_score")
        if contract.target_rank is not None and not result.get("target_school"):
            coverage_failures.append("missing_requested_rank")
        if contract.target_top_n is not None:
            expected_returned = min(contract.target_top_n, int(result.get("school_count") or 0))
            if len(result.get("top_schools") or ()) != expected_returned:
                coverage_failures.append("missing_requested_top_n")
            if len(result.get("ranking_items") or ()) != expected_returned:
                coverage_failures.append("missing_ranking_items")
            if int(result.get("returned_school_count") or 0) != expected_returned:
                coverage_failures.append("invalid_returned_school_count")
        if contract.operation == "region_ranking" and contract.target_rank is not None:
            if len(result.get("ranking_items") or ()) != 1:
                coverage_failures.append("missing_exact_rank_item")
            if int(result.get("returned_school_count") or 0) != 1:
                coverage_failures.append("invalid_returned_school_count")
        failures.extend(coverage_failures)
        checks.append({"name": "requested_output_coverage", "status": "FAIL" if coverage_failures else "PASS"})

        if read_snapshot is not None:
            _test_gate("before_replay", read_snapshot)
            read_snapshot.set_phase("independent_replay")
        replay = self._independent_replay(
            contract,
            connection=read_snapshot.connection if read_snapshot is not None else None,
        )
        pair_replays = {}
        if contract.target_dimensions:
            for name in contract.target_dimensions:
                pair_contract = replace(contract, dimension=name, target_dimensions=(), indicator_ids=self._indicator_ids(
                    name, connection=read_snapshot.connection if read_snapshot is not None else None,
                ))
                pair_replays[name] = self._independent_replay(
                    pair_contract,
                    connection=read_snapshot.connection if read_snapshot is not None else None,
                )
        if read_snapshot is not None:
            read_snapshot.mark("independent_replay_completed")
        numeric_failures = []
        count_fields = (
            ("requested_school_count", "requested_answer_count")
            if contract.dimension else ("school_count", "answer_count")
        )
        for result_key, replay_key in zip(count_fields, ("school_count", "answer_count")):
            if int(result.get(result_key) or 0) != int(replay[replay_key]):
                numeric_failures.append(f"replay_{result_key}")
        expected_score = replay["total_score"]
        result_score = result.get("target_dimension_score") if contract.dimension else result.get("total_score")
        if expected_score is None:
            if result_score is not None:
                numeric_failures.append("replay_requested_score")
        elif not isinstance(result_score, (int, float)) or not math.isclose(
            float(result_score), round(expected_score, 2), abs_tol=0.005
        ):
            numeric_failures.append("replay_requested_score")
        if contract.statistic in ("median", "mean_and_median") and not math.isclose(
            float(result.get("median_score")), round(float(replay["median_score"]), 2), abs_tol=0.005
        ):
            numeric_failures.append("replay_median_score")
        if contract.dimension and contract.statistic in ("median", "mean_and_median"):
            dimension_median = result.get("target_dimension_median_score")
            if not isinstance(dimension_median, (int, float)) or not math.isclose(
                float(dimension_median), round(float(replay["median_score"]), 2), abs_tol=0.005
            ):
                numeric_failures.append("replay_target_dimension_median_score")
        if contract.statistic == "mean_and_median":
            difference = result.get("mean_median_difference")
            if not isinstance(difference, (int, float)) or not math.isclose(
                float(difference),
                round(float(replay["total_score"]) - float(replay["median_score"]), 2),
                abs_tol=0.005,
            ):
                numeric_failures.append("replay_mean_median_difference")
        if contract.target_dimensions:
            actual_details = result.get("target_dimensions_detail") or []
            actual_by_name = {row.get("dimension"): row.get("score") for row in actual_details}
            expected_pair = []
            for name in contract.target_dimensions:
                expected = pair_replays[name].get("total_score")
                actual = actual_by_name.get(name)
                if expected is None or not isinstance(actual, (int, float)) or not math.isclose(
                    float(actual), round(float(expected), 2), abs_tol=0.01
                ):
                    numeric_failures.append(f"replay_dimension_pair_{name}")
                expected_pair.append(expected)
            if all(value is not None for value in expected_pair):
                expected_diff = round(round(float(expected_pair[0]), 2) - round(float(expected_pair[1]), 2), 2)
                if not isinstance(result.get("diff"), (int, float)) or not math.isclose(
                    float(result["diff"]), expected_diff, abs_tol=0.005
                ):
                    numeric_failures.append("replay_dimension_pair_difference")
        for statistic_name, result_key in (("max", "max_score"), ("min", "min_score")):
            if (contract.statistic == statistic_name or contract.statistic in ("min_and_max", "max_and_min")) and not math.isclose(
                float(result.get(result_key)), round(float(replay[result_key]), 2), abs_tol=0.005
            ):
                numeric_failures.append(f"replay_{result_key}")
        ranked = replay["ranked_schools"]
        if contract.target_rank is not None and ranked:
            rank_index = len(ranked) - 1 if contract.target_rank == -1 else contract.target_rank - 1
            target = result.get("target_school") or {}
            if rank_index < 0 or rank_index >= len(ranked) or int(target.get("id") or -1) != ranked[rank_index][0]:
                numeric_failures.append("replay_target_rank")
            elif not isinstance(target.get("score"), (int, float)) or not math.isclose(
                float(target["score"]), round(float(ranked[rank_index][1]), 2), abs_tol=0.005
            ):
                numeric_failures.append("replay_target_rank_score")
        if contract.target_top_n is not None:
            expected_ids = [item[0] for item in ranked[: contract.target_top_n]]
            actual_ids = [int(item.get("id")) for item in (result.get("top_schools") or ())[: contract.target_top_n]]
            if actual_ids != expected_ids:
                numeric_failures.append("replay_target_top_n")
            for actual, expected in zip(
                result.get("top_schools") or (),
                ranked[: contract.target_top_n],
            ):
                if not isinstance(actual.get("score"), (int, float)) or not math.isclose(
                    float(actual["score"]), round(float(expected[1]), 2), abs_tol=0.005
                ):
                    numeric_failures.append("replay_target_top_n_score")
                    break
        failures.extend(numeric_failures)
        checks.append({"name": "independent_replay", "status": "FAIL" if numeric_failures else "PASS"})

        execution_id = str(result.get("_id") or uuid.uuid4())
        receipt = ExecutionReceipt(
            execution_id=execution_id,
            contract_revision=contract.revision,
            dataset_version=contract.dataset_version,
            actual_region=str(result.get("region_name") or ""),
            actual_period=str(result.get("year") or ""),
            school_count=int(result.get(count_fields[0]) or 0),
            answer_count=int(result.get(count_fields[1]) or 0),
            aggregation=str(result.get("aggregation") or ""),
            query_template=str(execution_scope.get("query_template") or ""),
            parameters=(
                (
                    contract.entity_id,
                    contract.period,
                    contract.target_rank,
                    contract.target_top_n,
                    contract.statistic,
                    contract.aggregation,
                    contract.school_filter,
                    contract.ranking_spec,
                )
                if contract.operation == "region_ranking"
                else (
                    contract.entity_id,
                    contract.period,
                    contract.dimension,
                    contract.statistic,
                    contract.aggregation,
                    contract.school_filter,
                )
            ),
            read_consistency=read_snapshot.consistency_mode if read_snapshot is not None else "unverified",
            snapshot_id=read_snapshot.snapshot_id if read_snapshot is not None else None,
            sqlite_data_version=read_snapshot.sqlite_data_version if read_snapshot is not None else None,
            connection_token=read_snapshot.connection_token if read_snapshot is not None else None,
            transaction_trace=tuple(read_snapshot.trace_events) if read_snapshot is not None else (),
            operation=contract.operation,
            actual_entity_id=contract.entity_id,
        )
        return tuple(sorted(set(failures))), receipt, checks

    def _verify_region_compare(
        self,
        contract: AnalysisContract,
        result: Dict[str, Any],
        *,
        read_snapshot: Optional[ConsistentReadSnapshot],
    ):
        failures: list[str] = []
        checks: list[Dict[str, str]] = []
        snapshot_failures = []
        if read_snapshot is None:
            snapshot_failures.append("missing_consistent_read_snapshot")
        else:
            if read_snapshot.dataset_version != contract.dataset_version:
                snapshot_failures.append("snapshot_dataset_version")
            if not read_snapshot.connection.in_transaction:
                snapshot_failures.append("snapshot_transaction_inactive")
        failures.extend(snapshot_failures)
        checks.append({"name": "consistent_read_snapshot", "status": "FAIL" if snapshot_failures else "PASS"})

        binding_failures = []
        if result.get("object_type") != "region_compare":
            binding_failures.append("binding_object_type")
        if result.get("full_name_a") != contract.entity_id:
            binding_failures.append("binding_region_a")
        if result.get("full_name_b") != contract.secondary_entity_id:
            binding_failures.append("binding_region_b")
        if str(result.get("year") or "") != contract.period:
            binding_failures.append("binding_period")
        if result.get("aggregation") != contract.aggregation:
            binding_failures.append("binding_aggregation")
        if result.get("school_filter") != contract.school_filter:
            binding_failures.append("binding_filter")
        if result.get("target_stat") != contract.statistic:
            binding_failures.append("binding_statistic")
        if result.get("target_dimension") != contract.dimension:
            binding_failures.append("binding_dimension")
        if list(result.get("target_dimensions") or []) != (list(contract.target_dimensions) or ([contract.dimension] if contract.dimension else [])):
            binding_failures.append("binding_dimensions")
        execution_scope = result.get("execution_scope") or {}
        expected_scope = {
            "query_template": "region_compare_v1",
            "region_a": contract.entity_id,
            "region_b": contract.secondary_entity_id,
            "period": contract.period,
            "dimension": contract.dimension,
            "statistic": contract.statistic,
            "aggregation": contract.aggregation,
            "school_filter": contract.school_filter,
        }
        if execution_scope != expected_scope:
            binding_failures.append("binding_execution_scope")
        for label, detail, entity_id in (
            ("a", result.get("region_a_detail") or {}, contract.entity_id),
            ("b", result.get("region_b_detail") or {}, contract.secondary_entity_id),
        ):
            expected_detail_scope = {
                "query_template": "region_evaluation_v2",
                "region": entity_id,
                "period": contract.period,
                "dimension": contract.dimension,
                "statistic": contract.statistic,
                "aggregation": contract.aggregation,
                "school_filter": contract.school_filter,
            }
            if detail.get("execution_scope") != expected_detail_scope:
                binding_failures.append(f"binding_region_{label}_execution_scope")
        failures.extend(binding_failures)
        checks.append({"name": "contract_binding", "status": "FAIL" if binding_failures else "PASS"})

        coverage_failures = []
        if not str(result.get("direct_answer") or "").strip():
            coverage_failures.append("missing_direct_answer")
        if result.get("school_count_a") is None or result.get("school_count_b") is None:
            coverage_failures.append("missing_bilateral_school_counts")
        if contract.statistic == "count":
            if "所" not in str(result.get("direct_answer") or ""):
                coverage_failures.append("missing_requested_count")
        elif result.get("total_score_a") is None or result.get("total_score_b") is None:
            coverage_failures.append("missing_bilateral_scores")
        if contract.dimension and (
            result.get("target_dimension_score_a") is None
            or result.get("target_dimension_score_b") is None
        ):
            coverage_failures.append("missing_requested_dimension")
        failures.extend(coverage_failures)
        checks.append({"name": "requested_output_coverage", "status": "FAIL" if coverage_failures else "PASS"})

        if read_snapshot is not None:
            _test_gate("before_replay", read_snapshot)
            read_snapshot.set_phase("independent_replay")
        replay_a = self._independent_replay(
            contract,
            connection=read_snapshot.connection if read_snapshot is not None else None,
        )
        replay_b = self._independent_replay(
            replace(
                contract,
                entity_id=str(contract.secondary_entity_id),
                region_keyword=str(contract.secondary_region_keyword),
            ),
            connection=read_snapshot.connection if read_snapshot is not None else None,
        )
        if read_snapshot is not None:
            read_snapshot.mark("independent_replay_completed", replay_sides=2)
        replay_failures = []
        count_keys = (
            ("requested_school_count_a", "requested_answer_count_a", "requested_school_count_b", "requested_answer_count_b")
            if contract.dimension
            else ("school_count_a", "answer_count_a", "school_count_b", "answer_count_b")
        )
        for result_key, expected in (
            (count_keys[0], replay_a["school_count"]),
            (count_keys[1], replay_a["answer_count"]),
            (count_keys[2], replay_b["school_count"]),
            (count_keys[3], replay_b["answer_count"]),
        ):
            if int(result.get(result_key) or 0) != int(expected or 0):
                replay_failures.append(f"replay_{result_key}")
        # Public text and charts consume these side aliases.  Verify the same
        # dimension-specific population that the independent replay checked.
        if contract.dimension:
            for side, expected in (("a", replay_a["school_count"]), ("b", replay_b["school_count"])):
                if result.get(f"school_count_{side}") != expected:
                    replay_failures.append(f"replay_school_count_{side}")

        replay_scores = (replay_a.get("total_score"), replay_b.get("total_score"))
        result_scores = (result.get("total_score_a"), result.get("total_score_b"))
        for label, actual, expected in zip(("a", "b"), result_scores, replay_scores):
            if expected is None:
                if actual is not None:
                    replay_failures.append(f"replay_score_{label}")
            elif not isinstance(actual, (int, float)) or not math.isclose(
                float(actual), round(float(expected), 2), abs_tol=0.005
            ):
                replay_failures.append(f"replay_score_{label}")
        if all(score is not None for score in replay_scores):
            # The independent query retains both means before display rounding.
            # A delta is rounded only after their subtraction.
            expected_diff = round(float(replay_scores[0]) - float(replay_scores[1]), 2)
            if result.get("difference_basis") != "raw_means_then_round":
                replay_failures.append("replay_difference_basis")
            if not isinstance(result.get("diff"), (int, float)) or not math.isclose(
                float(result["diff"]), expected_diff, abs_tol=0.005
            ):
                replay_failures.append("replay_score_diff")
            expected_winner = "A" if expected_diff > 0 else ("B" if expected_diff < 0 else "tie")
            if result.get("winner") != expected_winner:
                replay_failures.append("replay_winner")
        failures.extend(replay_failures)
        checks.append({"name": "independent_replay", "status": "FAIL" if replay_failures else "PASS"})

        receipt = ExecutionReceipt(
            execution_id=str(result.get("_id") or uuid.uuid4()),
            contract_revision=contract.revision,
            dataset_version=contract.dataset_version,
            actual_region=str(result.get("full_name_a") or ""),
            actual_secondary_region=str(result.get("full_name_b") or ""),
            actual_period=str(result.get("year") or ""),
            school_count=int(result.get(count_keys[0]) or 0) + int(result.get(count_keys[2]) or 0),
            answer_count=int(result.get(count_keys[1]) or 0) + int(result.get(count_keys[3]) or 0),
            aggregation=str(result.get("aggregation") or ""),
            query_template=str(execution_scope.get("query_template") or ""),
            parameters=(
                contract.entity_id,
                contract.secondary_entity_id,
                contract.period,
                contract.dimension,
                contract.statistic,
                contract.aggregation,
                contract.school_filter,
            ),
            read_consistency=read_snapshot.consistency_mode if read_snapshot is not None else "unverified",
            snapshot_id=read_snapshot.snapshot_id if read_snapshot is not None else None,
            sqlite_data_version=read_snapshot.sqlite_data_version if read_snapshot is not None else None,
            connection_token=read_snapshot.connection_token if read_snapshot is not None else None,
            transaction_trace=tuple(read_snapshot.trace_events) if read_snapshot is not None else (),
            operation="region_compare",
        )
        return tuple(sorted(set(failures))), receipt, checks

    def _verify_region_longitudinal(
        self,
        contract: AnalysisContract,
        result: Dict[str, Any],
        *,
        read_snapshot: Optional[ConsistentReadSnapshot],
    ):
        failures: list[str] = []
        checks: list[Dict[str, str]] = []
        snapshot_failures = []
        if read_snapshot is None:
            snapshot_failures.append("missing_consistent_read_snapshot")
        else:
            if read_snapshot.dataset_version != contract.dataset_version:
                snapshot_failures.append("snapshot_dataset_version")
            if not read_snapshot.connection.in_transaction:
                snapshot_failures.append("snapshot_transaction_inactive")
        failures.extend(snapshot_failures)
        checks.append({
            "name": "consistent_read_snapshot",
            "status": "FAIL" if snapshot_failures else "PASS",
        })

        binding_failures = []
        if result.get("object_type") != "region_longitudinal":
            binding_failures.append("binding_object_type")
        if result.get("region_name") != contract.entity_id:
            binding_failures.append("binding_region")
        if str(result.get("year_a") or "") != contract.period:
            binding_failures.append("binding_period_a")
        if str(result.get("year_b") or "") != str(contract.secondary_period or ""):
            binding_failures.append("binding_period_b")
        if result.get("temporal_mode") != contract.temporal_mode:
            binding_failures.append("binding_temporal_mode")
        if result.get("period_order_policy") != "chronological_earlier_to_later":
            binding_failures.append("binding_period_order_policy")
        if result.get("comparison_direction") != "secondary_minus_primary":
            binding_failures.append("binding_comparison_direction")
        if result.get("school_years") != [contract.period, contract.secondary_period]:
            binding_failures.append("binding_school_years")
        expected_value_type = (
            "effective_school_count"
            if contract.statistic == "count"
            else ("dimension_mean" if contract.dimension else "overall_mean")
        )
        expected_value_unit = "所" if contract.statistic == "count" else "分"
        if result.get("comparison_value_type") != expected_value_type:
            binding_failures.append("binding_comparison_value_type")
        if result.get("value_unit") != expected_value_unit:
            binding_failures.append("binding_value_unit")
        if tuple(contract.indicator_ids) != self._indicator_ids(
            contract.dimension,
            connection=read_snapshot.connection if read_snapshot is not None else None,
        ):
            binding_failures.append("binding_indicator_ids")
        if result.get("aggregation") != contract.aggregation:
            binding_failures.append("binding_aggregation")
        if result.get("school_filter") != contract.school_filter:
            binding_failures.append("binding_filter")
        if result.get("target_stat") != contract.statistic:
            binding_failures.append("binding_statistic")
        if result.get("target_dimension") != contract.dimension:
            binding_failures.append("binding_dimension")
        execution_scope = result.get("execution_scope") or {}
        expected_scope = {
            "query_template": "region_longitudinal_v1",
            "region": contract.entity_id,
            "period_a": contract.period,
            "period_b": contract.secondary_period,
            "dimension": contract.dimension,
            "statistic": contract.statistic,
            "aggregation": contract.aggregation,
            "school_filter": contract.school_filter,
            "temporal_mode": contract.temporal_mode,
            "comparison_direction": "secondary_minus_primary",
        }
        if execution_scope != expected_scope:
            binding_failures.append("binding_execution_scope")
        for label, detail, period in (
            ("a", result.get("period_a") or {}, contract.period),
            ("b", result.get("period_b") or {}, contract.secondary_period),
        ):
            expected_detail_scope = {
                "query_template": "region_evaluation_v2",
                "region": contract.entity_id,
                "period": period,
                "dimension": contract.dimension,
                "statistic": contract.statistic,
                "aggregation": contract.aggregation,
                "school_filter": contract.school_filter,
            }
            if detail.get("execution_scope") != expected_detail_scope:
                binding_failures.append(f"binding_period_{label}_execution_scope")
        failures.extend(binding_failures)
        checks.append({
            "name": "contract_binding",
            "status": "FAIL" if binding_failures else "PASS",
        })

        coverage_failures = []
        if not str(result.get("direct_answer") or "").strip():
            coverage_failures.append("missing_direct_answer")
        if result.get("fact_data_status") != "ANSWER":
            coverage_failures.append("invalid_business_status")
        for field_name in (
            "comparison_value_a", "comparison_value_b", "school_count_a", "school_count_b",
            "answer_count_a", "answer_count_b", "total_unfiltered_count_a",
            "total_unfiltered_count_b", "diff", "relative_change_status",
        ):
            if result.get(field_name) is None:
                coverage_failures.append(f"missing_{field_name}")
        if contract.statistic == "mean" and (
            result.get("total_score_a") is None or result.get("total_score_b") is None
        ):
            coverage_failures.append("missing_bitemporal_scores")
        if contract.statistic == "count" and result.get("value_unit") != "所":
            coverage_failures.append("invalid_count_unit")
        if contract.dimension and (
            result.get("target_dimension_score_a") is None
            or result.get("target_dimension_score_b") is None
        ):
            coverage_failures.append("missing_bitemporal_dimension_scores")
        if "重复截面" not in str(result.get("direct_answer") or ""):
            coverage_failures.append("missing_repeated_cross_section_disclosure")
        failures.extend(coverage_failures)
        checks.append({
            "name": "requested_output_coverage",
            "status": "FAIL" if coverage_failures else "PASS",
        })

        if read_snapshot is not None:
            _test_gate("before_replay", read_snapshot)
            read_snapshot.set_phase("independent_replay")
        replay_a = self._independent_replay(
            contract,
            connection=read_snapshot.connection if read_snapshot is not None else None,
        )
        replay_b = self._independent_replay(
            replace(contract, period=str(contract.secondary_period or "")),
            connection=read_snapshot.connection if read_snapshot is not None else None,
        )
        if read_snapshot is not None:
            read_snapshot.mark("independent_replay_completed", replay_periods=2)

        replay_failures = []
        for label, replay in (("a", replay_a), ("b", replay_b)):
            if int(result.get(f"total_unfiltered_count_{label}") or 0) != int(
                replay["total_unfiltered_count"] or 0
            ):
                replay_failures.append(f"replay_total_unfiltered_count_{label}")
            if int(result.get(f"school_count_{label}") or 0) != int(replay["school_count"] or 0):
                replay_failures.append(f"replay_school_count_{label}")
            if int(result.get(f"answer_count_{label}") or 0) != int(replay["answer_count"] or 0):
                replay_failures.append(f"replay_answer_count_{label}")

        if contract.statistic == "count":
            expected_a = int(replay_a["school_count"] or 0)
            expected_b = int(replay_b["school_count"] or 0)
        else:
            replay_scores = (replay_a.get("total_score"), replay_b.get("total_score"))
            if any(value is None for value in replay_scores):
                replay_failures.append("replay_missing_score")
                expected_a = expected_b = None
            else:
                expected_a = round(float(replay_scores[0]), 2)
                expected_b = round(float(replay_scores[1]), 2)
        actual_a = result.get("comparison_value_a")
        actual_b = result.get("comparison_value_b")
        for label, actual, expected in (("a", actual_a, expected_a), ("b", actual_b, expected_b)):
            if expected is None:
                if actual is not None:
                    replay_failures.append(f"replay_value_{label}")
            elif not isinstance(actual, (int, float)) or not math.isclose(
                float(actual), float(expected), abs_tol=0.005
            ):
                replay_failures.append(f"replay_value_{label}")
        if expected_a is not None and expected_b is not None:
            expected_diff = round(float(expected_b) - float(expected_a), 2)
            if not isinstance(result.get("diff"), (int, float)) or not math.isclose(
                float(result["diff"]), expected_diff, abs_tol=0.005
            ):
                replay_failures.append("replay_diff")
            expected_trend = (
                ("增加" if expected_diff > 0 else "减少")
                if contract.statistic == "count"
                else ("提升" if expected_diff > 0 else "下降")
            ) if expected_diff != 0 else "持平"
            if result.get("trend") != expected_trend:
                replay_failures.append("replay_trend")
            expected_pct = (
                round(expected_diff / float(expected_a) * 100, 1)
                if float(expected_a) != 0 else None
            )
            actual_pct = result.get("pct_change")
            if expected_pct is None:
                if actual_pct is not None:
                    replay_failures.append("replay_pct_change")
                if result.get("relative_change_status") != "NOT_APPLICABLE_ZERO_BASELINE":
                    replay_failures.append("replay_relative_change_status")
            elif not isinstance(actual_pct, (int, float)) or not math.isclose(
                float(actual_pct), expected_pct, abs_tol=0.05
            ):
                replay_failures.append("replay_pct_change")
            elif result.get("relative_change_status") != "AVAILABLE":
                replay_failures.append("replay_relative_change_status")
        failures.extend(replay_failures)
        checks.append({
            "name": "independent_replay",
            "status": "FAIL" if replay_failures else "PASS",
        })

        receipt = ExecutionReceipt(
            execution_id=str(result.get("_id") or uuid.uuid4()),
            contract_revision=contract.revision,
            dataset_version=contract.dataset_version,
            actual_region=str(result.get("region_name") or ""),
            actual_period=str(result.get("year_a") or ""),
            actual_secondary_period=str(result.get("year_b") or ""),
            school_count=int(result.get("school_count_a") or 0) + int(result.get("school_count_b") or 0),
            answer_count=int(result.get("answer_count_a") or 0) + int(result.get("answer_count_b") or 0),
            school_count_a=int(result.get("school_count_a") or 0),
            school_count_b=int(result.get("school_count_b") or 0),
            answer_count_a=int(result.get("answer_count_a") or 0),
            answer_count_b=int(result.get("answer_count_b") or 0),
            aggregation=str(result.get("aggregation") or ""),
            query_template=str(execution_scope.get("query_template") or ""),
            parameters=(
                contract.entity_id,
                contract.period,
                contract.secondary_period,
                contract.temporal_mode,
                contract.dimension,
                contract.statistic,
                contract.aggregation,
                contract.school_filter,
            ),
            read_consistency=read_snapshot.consistency_mode if read_snapshot is not None else "unverified",
            snapshot_id=read_snapshot.snapshot_id if read_snapshot is not None else None,
            sqlite_data_version=read_snapshot.sqlite_data_version if read_snapshot is not None else None,
            connection_token=read_snapshot.connection_token if read_snapshot is not None else None,
            transaction_trace=tuple(read_snapshot.trace_events) if read_snapshot is not None else (),
            operation="region_longitudinal",
            temporal_mode=contract.temporal_mode,
        )
        return tuple(sorted(set(failures))), receipt, checks

    def _verify_school_evaluation(
        self,
        contract: AnalysisContract,
        result: Dict[str, Any],
        *,
        read_snapshot: Optional[ConsistentReadSnapshot],
    ):
        failures: list[str] = []
        checks: list[Dict[str, str]] = []
        snapshot_failures = []
        if read_snapshot is None:
            snapshot_failures.append("missing_consistent_read_snapshot")
        else:
            if read_snapshot.dataset_version != contract.dataset_version:
                snapshot_failures.append("snapshot_dataset_version")
            if not read_snapshot.connection.in_transaction:
                snapshot_failures.append("snapshot_transaction_inactive")
        failures.extend(snapshot_failures)
        checks.append({"name": "consistent_read_snapshot", "status": "FAIL" if snapshot_failures else "PASS"})

        binding_failures = []
        if result.get("object_type") != "school":
            binding_failures.append("binding_object_type")
        if result.get("analysis_operation") != "school_evaluation":
            binding_failures.append("binding_operation")
        if int(result.get("school_id") or 0) != int(contract.school_id or 0):
            binding_failures.append("binding_school_id")
        if result.get("school_name") != contract.entity_id:
            binding_failures.append("binding_school_name")
        years = result.get("school_years") or []
        if not years or str(years[0]) != contract.period:
            binding_failures.append("binding_period")
        if result.get("target_dimension") != contract.dimension:
            binding_failures.append("binding_dimension")
        execution_scope = result.get("execution_scope") or {}
        expected_scope = {
            "query_template": "school_evaluation_v1",
            "school_id": int(contract.school_id or 0),
            "school_name": contract.entity_id,
            "period": contract.period,
            "dimension": contract.dimension,
            **({"dimensions": list(contract.target_dimensions)} if contract.target_dimensions else {}),
            "indicator_ids": list(contract.indicator_ids),
            "metric_key": contract.metric_key,
            "statistic": contract.statistic,
            "aggregation": contract.aggregation,
        }
        if execution_scope != expected_scope:
            binding_failures.append("binding_execution_scope")
        failures.extend(binding_failures)
        checks.append({"name": "contract_binding", "status": "FAIL" if binding_failures else "PASS"})

        coverage_failures = []
        if not str(result.get("direct_answer") or "").strip():
            coverage_failures.append("missing_direct_answer")
        if result.get("total_score") is None:
            coverage_failures.append("missing_school_score")
        if int(result.get("total_metrics") or 0) != len(contract.indicator_ids):
            coverage_failures.append("invalid_school_metric_count")
        expected_dimensions = [
            str(item.get("name") or "")
            for item in ((contract.metric_spec or {}).get("dimensions") or ())
        ]
        actual_dimensions = {
            str(item.get("dimension") or "")
            for item in (result.get("dimensions") or ())
        }
        if not expected_dimensions or actual_dimensions != set(expected_dimensions):
            coverage_failures.append("missing_school_dimensions")
        extrema_rows = result.get('dimensions') or ()
        from packages.core.item_collection import collected_ids, enabled as r11_enabled
        if r11_enabled():
            available_dimensions = {d['name'] for d in (contract.metric_spec or {}).get('dimensions', ())
                                    if collected_ids(contract.period, d.get('indicator_ids', ()))}
            extrema_rows = [row for row in extrema_rows if row.get('dimension') in available_dimensions]
        if "dimension_extrema" in contract.requested_statistics and (
            len(expected_dimensions) != 6
            or any(not isinstance(row.get("score"), (int, float)) for row in extrema_rows)
        ):
            coverage_failures.append("missing_school_dimension_extrema_population")
        if contract.target_dimensions and (
            [row.get("dimension") for row in (result.get("dimensions") or [])] != list(contract.target_dimensions)
            or result.get("diff") is None
        ):
            coverage_failures.append("missing_school_dimension_pair")
        failures.extend(coverage_failures)
        checks.append({"name": "requested_output_coverage", "status": "FAIL" if coverage_failures else "PASS"})

        if read_snapshot is not None:
            _test_gate("before_replay", read_snapshot)
            read_snapshot.set_phase("independent_replay")
        replay_failures = []
        replay: Dict[str, Any] = {}
        try:
            replay = self._independent_school_compare_side(
                contract,
                school_id=int(contract.school_id or 0),
                school_name=contract.entity_id,
                connection=read_snapshot.connection if read_snapshot is not None else None,
            )
        except ContractViolation as exc:
            replay_failures.extend(f"replay_{failure}" for failure in exc.failures)
        if read_snapshot is not None:
            read_snapshot.mark("independent_replay_completed", replay_sides=1)
        expected_score = replay.get("total_score")
        actual_score = result.get("total_score")
        if expected_score is None:
            if actual_score is not None:
                replay_failures.append("replay_school_score")
        elif not isinstance(actual_score, (int, float)) or not math.isclose(
            float(actual_score), float(expected_score), abs_tol=0.005
        ):
            replay_failures.append("replay_school_score")
        if int(result.get("answered") or 0) != int(replay.get("answered") or 0):
            replay_failures.append("replay_school_answer_count")
        if int(result.get("total_metrics") or 0) != int(replay.get("total_metrics") or 0):
            replay_failures.append("replay_school_metric_count")
        if not isinstance(result.get("data_coverage"), (int, float)) or not math.isclose(
            float(result["data_coverage"]), float(replay.get("data_coverage") or 0.0), abs_tol=0.005
        ):
            replay_failures.append("replay_school_data_coverage")
        if not isinstance(result.get("overall_confidence"), (int, float)) or not math.isclose(
            float(result["overall_confidence"]), float(replay.get("overall_confidence") or 0.0), abs_tol=0.005
        ):
            replay_failures.append("replay_school_overall_confidence")
        actual_dim_rows = {
            str(item.get("dimension") or ""): item
            for item in (result.get("dimensions") or ())
        }
        for name in expected_dimensions:
            actual_row = actual_dim_rows.get(name) or {}
            actual = actual_row.get("score")
            expected = (replay.get("dimensions") or {}).get(name)
            if expected is None:
                if actual is not None:
                    replay_failures.append(f"replay_dimension_{name}")
            elif not isinstance(actual, (int, float)) or not math.isclose(
                float(actual), float(expected), abs_tol=0.005
            ):
                replay_failures.append(f"replay_dimension_{name}")
            expected_confidence = round(
                int((replay.get("dimension_answered") or {}).get(name) or 0)
                / int((replay.get("dimension_totals") or {}).get(name) or 1),
                2,
            )
            if not isinstance(actual_row.get("confidence"), (int, float)) or not math.isclose(
                float(actual_row["confidence"]), expected_confidence, abs_tol=0.005
            ):
                replay_failures.append(f"replay_dimension_confidence_{name}")
        if contract.target_dimensions:
            scores = [(replay.get("dimensions") or {}).get(name) for name in contract.target_dimensions]
            if all(score is not None for score in scores):
                expected_diff = round(round(float(scores[0]), 2) - round(float(scores[1]), 2), 2)
                if not isinstance(result.get("diff"), (int, float)) or not math.isclose(
                    float(result["diff"]), expected_diff, abs_tol=0.005
                ):
                    replay_failures.append("replay_school_dimension_pair_difference")
        failures.extend(replay_failures)
        checks.append({"name": "independent_replay", "status": "FAIL" if replay_failures else "PASS"})

        receipt = ExecutionReceipt(
            execution_id=str(result.get("_id") or uuid.uuid4()),
            contract_revision=contract.revision,
            dataset_version=contract.dataset_version,
            actual_region=contract.entity_id,
            actual_period=contract.period,
            school_count=1,
            answer_count=int(replay.get("answered") or 0),
            aggregation=contract.aggregation,
            query_template=str(execution_scope.get("query_template") or ""),
            parameters=(contract.school_id, contract.period, contract.dimension, *contract.indicator_ids),
            read_consistency=read_snapshot.consistency_mode if read_snapshot is not None else "unverified",
            snapshot_id=read_snapshot.snapshot_id if read_snapshot is not None else None,
            sqlite_data_version=read_snapshot.sqlite_data_version if read_snapshot is not None else None,
            connection_token=read_snapshot.connection_token if read_snapshot is not None else None,
            transaction_trace=tuple(read_snapshot.trace_events) if read_snapshot is not None else (),
            operation="school_evaluation",
            actual_entity_id=str(contract.school_id),
            metric_key=contract.metric_key,
        )
        return tuple(sorted(set(failures))), receipt, checks

    def _verify_school_compare(
        self,
        contract: AnalysisContract,
        result: Dict[str, Any],
        *,
        read_snapshot: Optional[ConsistentReadSnapshot],
    ):
        failures: list[str] = []
        checks: list[Dict[str, str]] = []
        snapshot_failures = []
        if read_snapshot is None:
            snapshot_failures.append("missing_consistent_read_snapshot")
        else:
            if read_snapshot.dataset_version != contract.dataset_version:
                snapshot_failures.append("snapshot_dataset_version")
            if not read_snapshot.connection.in_transaction:
                snapshot_failures.append("snapshot_transaction_inactive")
        failures.extend(snapshot_failures)
        checks.append({"name": "consistent_read_snapshot", "status": "FAIL" if snapshot_failures else "PASS"})

        binding_failures = []
        school_a = result.get("school_a") or {}
        school_b = result.get("school_b") or {}
        if result.get("object_type") != "school_compare":
            binding_failures.append("binding_object_type")
        if (contract.metric_spec or {}).get("complete_template_required") is not True:
            binding_failures.append("binding_complete_template_policy")
        if contract.null_policy != "require_complete_template_no_nulls":
            binding_failures.append("binding_null_policy")
        if int(school_a.get("school_id") or 0) != int(contract.school_id or 0):
            binding_failures.append("binding_school_a_id")
        if int(school_b.get("school_id") or 0) != int(contract.secondary_school_id or 0):
            binding_failures.append("binding_school_b_id")
        if result.get("school_name_a") != contract.entity_id:
            binding_failures.append("binding_school_a_name")
        if result.get("school_name_b") != contract.secondary_entity_id:
            binding_failures.append("binding_school_b_name")
        exp_period_a = contract.period
        exp_period_b = contract.secondary_period or contract.period
        if str(result.get("year_a") or "") != exp_period_a or str(result.get("year_b") or "") != exp_period_b:
            binding_failures.append("binding_period")
        execution_scope = result.get("execution_scope") or {}
        expected_scope = {
            "query_template": "school_compare_v1",
            "school_id_a": int(contract.school_id or 0),
            "school_id_b": int(contract.secondary_school_id or 0),
            "school_name_a": contract.entity_id,
            "school_name_b": contract.secondary_entity_id,
            "period": contract.period if not contract.secondary_period or contract.period == contract.secondary_period else None,
            "metric_key": contract.metric_key,
            "statistic": contract.statistic,
            "aggregation": contract.aggregation,
        }
        if contract.dimension:
            expected_scope["dimension"] = contract.dimension
        if execution_scope != expected_scope:
            binding_failures.append("binding_execution_scope")
        failures.extend(binding_failures)
        checks.append({"name": "contract_binding", "status": "FAIL" if binding_failures else "PASS"})

        coverage_failures = []
        expected_metric_count = len(contract.indicator_ids)
        from packages.core.item_collection import collected_ids
        for label, side in (("a", school_a), ("b", school_b)):
            expected_answered = len(collected_ids(exp_period_a if label == 'a' else exp_period_b, contract.indicator_ids))
            if (
                int(side.get("total_metrics") or 0) != expected_metric_count
                or int(side.get("answered") or 0) != expected_answered
            ):
                coverage_failures.append(f"incomplete_template_{label}")
        if not str(result.get("direct_answer") or "").strip():
            coverage_failures.append("missing_direct_answer")
        if result.get("total_score_a") is None or result.get("total_score_b") is None:
            coverage_failures.append("missing_bilateral_scores")
        if contract.dimension and (
            result.get("target_dimension") != contract.dimension
            or not isinstance(result.get("target_dimension_score_a"), (int, float))
            or not isinstance(result.get("target_dimension_score_b"), (int, float))
        ):
            coverage_failures.append("missing_target_dimension_scores")
        expected_dimensions = [
            str(item.get("name") or "")
            for item in ((contract.metric_spec or {}).get("dimensions") or ())
        ]
        actual_dimensions = {
            str(item.get("dimension") or "")
            for item in (result.get("dimensions_comparison") or ())
        }
        if not expected_dimensions or actual_dimensions != set(expected_dimensions):
            coverage_failures.append("missing_bilateral_dimensions")
        if not result.get("radar_data_a") or not result.get("radar_data_b"):
            coverage_failures.append("missing_bilateral_chart_data")
        failures.extend(coverage_failures)
        checks.append({"name": "requested_output_coverage", "status": "FAIL" if coverage_failures else "PASS"})

        if read_snapshot is not None:
            _test_gate("before_replay", read_snapshot)
            read_snapshot.set_phase("independent_replay")
        replay_failures = []
        replay_a: Dict[str, Any] = {}
        replay_b: Dict[str, Any] = {}
        try:
            replay_a = self._independent_school_compare_side(
                contract,
                school_id=int(contract.school_id or 0),
                school_name=contract.entity_id,
                connection=read_snapshot.connection if read_snapshot is not None else None,
            )
        except ContractViolation as exc:
            replay_failures.extend(f"replay_a_{failure}" for failure in exc.failures)
        try:
            replay_b = self._independent_school_compare_side(
                contract,
                school_id=int(contract.secondary_school_id or 0),
                school_name=str(contract.secondary_entity_id or ""),
                connection=read_snapshot.connection if read_snapshot is not None else None,
            )
        except ContractViolation as exc:
            replay_failures.extend(f"replay_b_{failure}" for failure in exc.failures)
        if read_snapshot is not None:
            read_snapshot.mark("independent_replay_completed", replay_sides=2)
        for label, actual, replay in (
            ("a", result.get("total_score_a"), replay_a),
            ("b", result.get("total_score_b"), replay_b),
        ):
            expected = replay.get("total_score")
            if expected is None:
                if actual is not None:
                    replay_failures.append(f"replay_school_score_{label}")
            elif not isinstance(actual, (int, float)) or not math.isclose(
                float(actual), float(expected), abs_tol=0.005
            ):
                replay_failures.append(f"replay_school_score_{label}")
            side = school_a if label == "a" else school_b
            if int(side.get("answered") or 0) != int(replay.get("answered") or 0):
                replay_failures.append(f"replay_school_answer_count_{label}")
            if int(side.get("total_metrics") or 0) != int(replay.get("total_metrics") or 0):
                replay_failures.append(f"replay_school_metric_count_{label}")

        score_a = replay_a.get("total_score")
        score_b = replay_b.get("total_score")
        if score_a is not None and score_b is not None:
            expected_diff = round(float(score_a) - float(score_b), 2)
            if not isinstance(result.get("total_diff"), (int, float)) or not math.isclose(
                float(result["total_diff"]), expected_diff, abs_tol=0.005
            ):
                replay_failures.append("replay_school_score_diff")
            expected_winner = "A" if expected_diff > 0 else ("B" if expected_diff < 0 else "TIE")
            if result.get("winner") != expected_winner:
                replay_failures.append("replay_school_winner")

        actual_dim_rows = {
            str(item.get("dimension") or ""): item
            for item in (result.get("dimensions_comparison") or ())
        }
        for name in expected_dimensions:
            row = actual_dim_rows.get(name) or {}
            nullable_a = (replay_a.get('dimensions') or {}).get(name)
            nullable_b = (replay_b.get('dimensions') or {}).get(name)
            from packages.core.item_collection import enabled as r11_enabled
            if r11_enabled() and (nullable_a is None or nullable_b is None):
                if row.get('score_a') != nullable_a or row.get('score_b') != nullable_b or row.get('diff') is not None or row.get('lead_tag') != 'NO_DATA':
                    replay_failures.append(f'replay_unavailable_dimension_{name}')
                continue
            expected_a = float((replay_a.get("dimensions") or {}).get(name) or 0.0)
            expected_b = float((replay_b.get("dimensions") or {}).get(name) or 0.0)
            expected_diff = round(expected_a - expected_b, 2)
            if not isinstance(row.get("score_a"), (int, float)) or not math.isclose(float(row["score_a"]), expected_a, abs_tol=0.005):
                replay_failures.append(f"replay_dimension_{name}_a")
            if not isinstance(row.get("score_b"), (int, float)) or not math.isclose(float(row["score_b"]), expected_b, abs_tol=0.005):
                replay_failures.append(f"replay_dimension_{name}_b")
            if not isinstance(row.get("diff"), (int, float)) or not math.isclose(float(row["diff"]), expected_diff, abs_tol=0.005):
                replay_failures.append(f"replay_dimension_{name}_diff")
            expected_lead = "A" if expected_diff > 0.01 else ("B" if expected_diff < -0.01 else "TIE")
            if row.get("lead_tag") != expected_lead:
                replay_failures.append(f"replay_dimension_{name}_leader")
        if contract.dimension:
            selected_a = float((replay_a.get("dimensions") or {}).get(contract.dimension) or 0.0)
            selected_b = float((replay_b.get("dimensions") or {}).get(contract.dimension) or 0.0)
            selected_diff = round(selected_a - selected_b, 2)
            for label, actual, expected in (
                ("a", result.get("target_dimension_score_a"), selected_a),
                ("b", result.get("target_dimension_score_b"), selected_b),
                ("comparison_a", result.get("comparison_value_a"), selected_a),
                ("comparison_b", result.get("comparison_value_b"), selected_b),
                ("diff", result.get("diff"), selected_diff),
            ):
                if not isinstance(actual, (int, float)) or not math.isclose(float(actual), expected, abs_tol=0.005):
                    replay_failures.append(f"replay_target_dimension_{label}")
        failures.extend(replay_failures)
        checks.append({"name": "independent_replay", "status": "FAIL" if replay_failures else "PASS"})

        receipt = ExecutionReceipt(
            execution_id=str(result.get("_id") or uuid.uuid4()),
            contract_revision=contract.revision,
            dataset_version=contract.dataset_version,
            actual_region="",
            actual_period=contract.period,
            school_count=2,
            answer_count=int(replay_a.get("answered") or 0) + int(replay_b.get("answered") or 0),
            aggregation=contract.aggregation,
            query_template=str(execution_scope.get("query_template") or ""),
            parameters=(
                contract.school_id,
                contract.secondary_school_id,
                contract.period,
                contract.metric_key,
                contract.statistic,
                contract.aggregation,
                *( (contract.dimension,) if contract.dimension else () ),
            ),
            read_consistency=read_snapshot.consistency_mode if read_snapshot is not None else "unverified",
            snapshot_id=read_snapshot.snapshot_id if read_snapshot is not None else None,
            sqlite_data_version=read_snapshot.sqlite_data_version if read_snapshot is not None else None,
            connection_token=read_snapshot.connection_token if read_snapshot is not None else None,
            transaction_trace=tuple(read_snapshot.trace_events) if read_snapshot is not None else (),
            operation="school_compare",
            actual_entity_id=contract.entity_id,
            actual_secondary_entity_id=contract.secondary_entity_id,
            metric_key=contract.metric_key,
        )
        return tuple(sorted(set(failures))), receipt, checks

    def _verify_school_fact(
        self,
        contract: AnalysisContract,
        result: Dict[str, Any],
        *,
        read_snapshot: Optional[ConsistentReadSnapshot],
    ):
        if contract.metric_type == "question_set":
            return self._verify_school_fact_set(contract, result, read_snapshot=read_snapshot)
        failures: list[str] = []
        checks: list[Dict[str, str]] = []
        snapshot_failures = []
        if read_snapshot is None:
            snapshot_failures.append("missing_consistent_read_snapshot")
        else:
            if read_snapshot.dataset_version != contract.dataset_version:
                snapshot_failures.append("snapshot_dataset_version")
            if not read_snapshot.connection.in_transaction:
                snapshot_failures.append("snapshot_transaction_inactive")
        failures.extend(snapshot_failures)
        checks.append({"name": "consistent_read_snapshot", "status": "FAIL" if snapshot_failures else "PASS"})

        binding_failures = []
        metric_spec = dict(contract.metric_spec or {})
        if metric_spec.get("type") != contract.metric_type or metric_spec.get("name") != contract.metric_name:
            binding_failures.append("binding_contract_metric_spec")
        if contract.metric_type == "question":
            try:
                spec_qid = int(metric_spec.get("qid"))
            except (TypeError, ValueError):
                spec_qid = -1
                binding_failures.append("binding_contract_metric_spec")
            if tuple(contract.indicator_ids) != (spec_qid,):
                binding_failures.append("binding_contract_indicator_ids")
            if contract.metric_key != f"question:{metric_spec.get('qid')}":
                binding_failures.append("binding_contract_metric_key")
        elif contract.metric_type == "compound":
            try:
                spec_ids = tuple(int(qid) for qid in (metric_spec.get("qids") or ()))
            except (TypeError, ValueError):
                spec_ids = ()
                binding_failures.append("binding_contract_metric_spec")
            if tuple(contract.indicator_ids) != spec_ids:
                binding_failures.append("binding_contract_indicator_ids")
            expected_metric_key = f"compound:{metric_spec.get('compound_key') or metric_spec.get('name') or ''}"
            if contract.metric_key != expected_metric_key:
                binding_failures.append("binding_contract_metric_key")
        elif contract.metric_type == "school_field":
            expected_metric_key = f"school_field:{metric_spec.get('field') or ''}"
            if contract.indicator_ids:
                binding_failures.append("binding_contract_indicator_ids")
            if contract.metric_key != expected_metric_key:
                binding_failures.append("binding_contract_metric_key")
        if result.get("object_type") != "school_fact":
            binding_failures.append("binding_object_type")
        if int(result.get("school_id") or -1) != contract.school_id:
            binding_failures.append("binding_school_id")
        if result.get("school_name") != contract.entity_id:
            binding_failures.append("binding_school_name")
        if str(result.get("year") or "") != contract.period:
            binding_failures.append("binding_period")
        if result.get("fact_type") != contract.metric_type:
            binding_failures.append("binding_metric_type")
        if result.get("metric_name") != contract.metric_name:
            binding_failures.append("binding_metric_name")
        execution_scope = result.get("execution_scope") or {}
        expected_scope = {
            "query_template": "school_fact_v1",
            "school_id": contract.school_id,
            "school_name": contract.entity_id,
            "period": contract.period,
            "metric_type": contract.metric_type,
            "metric_key": contract.metric_key,
            "indicator_ids": list(contract.indicator_ids),
        }
        if execution_scope != expected_scope:
            binding_failures.append("binding_execution_scope")
        failures.extend(binding_failures)
        checks.append({"name": "contract_binding", "status": "FAIL" if binding_failures else "PASS"})

        coverage_failures = []
        if not str(result.get("direct_answer") or "").strip():
            coverage_failures.append("missing_direct_answer")
        if result.get("fact_data_status") not in {"ANSWER", "NO_DATA"}:
            coverage_failures.append("missing_fact_data_status")
        elif result.get("fact_data_status") == "ANSWER" and result.get("target_value") is None:
            coverage_failures.append("missing_answer_value")
        elif result.get("fact_data_status") == "NO_DATA" and result.get("target_value") is not None:
            coverage_failures.append("no_data_has_target_value")
        if result.get("fact_data_status") == "NO_DATA" and result.get("ratio") is not None:
            coverage_failures.append("no_data_has_ratio")
        if (
            result.get("fact_data_status") == "ANSWER"
            and contract.metric_key in {"compound:生机比", "compound:师机比"}
            and result.get("ratio") is None
        ):
            coverage_failures.append("missing_answer_ratio")
        failures.extend(coverage_failures)
        checks.append({"name": "requested_output_coverage", "status": "FAIL" if coverage_failures else "PASS"})

        if binding_failures:
            checks.append({"name": "independent_replay", "status": "NOT_APPLICABLE"})
            execution_scope = result.get("execution_scope") or {}
            receipt = ExecutionReceipt(
                execution_id=str(result.get("_id") or uuid.uuid4()),
                contract_revision=contract.revision,
                dataset_version=contract.dataset_version,
                actual_region=contract.entity_id,
                actual_period=str(result.get("year") or ""),
                school_count=1,
                answer_count=0,
                aggregation="not_applicable",
                query_template=str(execution_scope.get("query_template") or ""),
                parameters=(contract.school_id, contract.period, contract.metric_key, *contract.indicator_ids),
                read_consistency=read_snapshot.consistency_mode if read_snapshot is not None else "unverified",
                snapshot_id=read_snapshot.snapshot_id if read_snapshot is not None else None,
                sqlite_data_version=read_snapshot.sqlite_data_version if read_snapshot is not None else None,
                connection_token=read_snapshot.connection_token if read_snapshot is not None else None,
                transaction_trace=tuple(read_snapshot.trace_events) if read_snapshot is not None else (),
                operation="school_fact",
                actual_entity_id=str(contract.school_id),
                metric_key=contract.metric_key,
            )
            return tuple(sorted(set(failures))), receipt, checks

        if read_snapshot is not None:
            _test_gate("before_replay", read_snapshot)
            read_snapshot.set_phase("independent_replay")
        replay = self._independent_school_fact(
            contract,
            connection=read_snapshot.connection if read_snapshot is not None else None,
        )
        if read_snapshot is not None:
            read_snapshot.mark("independent_replay_completed")
        replay_failures = []
        if result.get("fact_data_status") != replay["fact_data_status"]:
            replay_failures.append("replay_fact_data_status")
        if not self._fact_value_equal(result.get("target_value"), replay.get("target_value")):
            replay_failures.append("replay_target_value")
        for key in (
            "normalized_score", "ratio", "component_total",
            "component_answer_count", "component_complete",
        ):
            if key in replay and not self._fact_value_equal(result.get(key), replay.get(key)):
                replay_failures.append(f"replay_{key}")
        failures.extend(replay_failures)
        checks.append({"name": "independent_replay", "status": "FAIL" if replay_failures else "PASS"})

        receipt = ExecutionReceipt(
            execution_id=str(result.get("_id") or uuid.uuid4()),
            contract_revision=contract.revision,
            dataset_version=contract.dataset_version,
            actual_region=contract.entity_id,
            actual_period=str(result.get("year") or ""),
            school_count=1,
            answer_count=int(replay.get("answer_count") or 0),
            aggregation="not_applicable",
            query_template=str(execution_scope.get("query_template") or ""),
            parameters=(contract.school_id, contract.period, contract.metric_key, *contract.indicator_ids),
            read_consistency=read_snapshot.consistency_mode if read_snapshot is not None else "unverified",
            snapshot_id=read_snapshot.snapshot_id if read_snapshot is not None else None,
            sqlite_data_version=read_snapshot.sqlite_data_version if read_snapshot is not None else None,
            connection_token=read_snapshot.connection_token if read_snapshot is not None else None,
            transaction_trace=tuple(read_snapshot.trace_events) if read_snapshot is not None else (),
            operation="school_fact",
            actual_entity_id=str(contract.school_id),
            metric_key=contract.metric_key,
        )
        return tuple(sorted(set(failures))), receipt, checks

    def _verify_school_fact_set(
        self, contract: AnalysisContract, result: Dict[str, Any], *,
        read_snapshot: Optional[ConsistentReadSnapshot],
    ):
        failures: list[str] = []
        checks: list[Dict[str, str]] = []
        snapshot_ok = (
            read_snapshot is not None
            and read_snapshot.dataset_version == contract.dataset_version
            and read_snapshot.connection.in_transaction
        )
        if not snapshot_ok:
            failures.append("missing_consistent_read_snapshot")
        checks.append({"name": "consistent_read_snapshot", "status": "PASS" if snapshot_ok else "FAIL"})
        scope = result.get("execution_scope") or {}
        expected_scope = {
            "query_template": "school_fact_set_v1",
            "school_id": contract.school_id,
            "school_name": contract.entity_id,
            "period": contract.period,
            "metric_type": "question_set",
            "metric_key": contract.metric_key,
            "indicator_ids": list(contract.indicator_ids),
            "derived": (contract.metric_spec or {}).get("derived"),
        }
        binding_ok = (
            result.get("object_type") == "school_fact"
            and result.get("fact_type") == "question_set"
            and result.get("school_id") == contract.school_id
            and result.get("school_name") == contract.entity_id
            and str(result.get("year") or "") == contract.period
            and result.get("metric_name") == contract.metric_name
            and scope == expected_scope
        )
        if not binding_ok:
            failures.append("binding_school_question_set")
        checks.append({"name": "contract_binding", "status": "PASS" if binding_ok else "FAIL"})
        if read_snapshot is not None:
            _test_gate("before_replay", read_snapshot)
            read_snapshot.set_phase("independent_replay")
        replay = self._independent_school_fact(
            contract, connection=read_snapshot.connection if read_snapshot is not None else None,
        )
        if read_snapshot is not None:
            read_snapshot.mark("independent_replay_completed")
        actual_items = result.get("item_results") or []
        expected_items = replay["item_results"]
        if len(actual_items) != 2:
            failures.append("missing_school_question_set_items")
        else:
            for actual, expected in zip(actual_items, expected_items):
                if (
                    actual.get("question_id") != expected["question_id"]
                    or actual.get("metric_name") != expected["metric_name"]
                    or actual.get("unit") != expected["unit"]
                    or actual.get("coding_status") != expected["coding_status"]
                    or actual.get("fact_data_status") != expected["fact_data_status"]
                    or not self._fact_value_equal(actual.get("target_value"), expected["target_value"])
                ):
                    failures.append("replay_school_question_set_item")
        if result.get("fact_data_status") != replay["fact_data_status"]:
            failures.append("replay_school_question_set_status")
        actual_derived = result.get("derived_result")
        expected_derived = replay["derived_result"]
        if bool(actual_derived) != bool(expected_derived):
            failures.append("replay_school_question_set_derived")
        elif actual_derived and expected_derived and (
            any(actual_derived.get(key) != expected_derived[key]
                for key in ("operation", "left_qid", "right_qid", "unit"))
            or not self._fact_value_equal(actual_derived.get("value"), expected_derived["value"])
        ):
            failures.append("replay_school_question_set_derived")
        coverage_ok = bool(str(result.get("direct_answer") or "").strip()) and len(actual_items) == 2
        if not coverage_ok:
            failures.append("missing_school_question_set_output")
        checks.append({"name": "requested_output_coverage", "status": "PASS" if coverage_ok else "FAIL"})
        replay_ok = not any(f.startswith("replay_") or f == "missing_school_question_set_items" for f in failures)
        checks.append({"name": "independent_replay", "status": "PASS" if replay_ok else "FAIL"})
        receipt = ExecutionReceipt(
            execution_id=str(result.get("_id") or uuid.uuid4()),
            contract_revision=contract.revision,
            dataset_version=contract.dataset_version,
            actual_region=contract.entity_id,
            actual_period=str(result.get("year") or ""),
            school_count=1,
            answer_count=int(replay["answer_count"]),
            aggregation="not_applicable",
            query_template=str(scope.get("query_template") or ""),
            parameters=(contract.school_id, contract.period, contract.metric_key, *contract.indicator_ids),
            read_consistency=read_snapshot.consistency_mode if read_snapshot is not None else "unverified",
            snapshot_id=read_snapshot.snapshot_id if read_snapshot is not None else None,
            sqlite_data_version=read_snapshot.sqlite_data_version if read_snapshot is not None else None,
            connection_token=read_snapshot.connection_token if read_snapshot is not None else None,
            transaction_trace=tuple(read_snapshot.trace_events) if read_snapshot is not None else (),
            operation="school_fact",
            actual_entity_id=str(contract.school_id),
            metric_key=contract.metric_key,
        )
        return tuple(sorted(set(failures))), receipt, checks

    @staticmethod
    def _fact_value_equal(actual: Any, expected: Any) -> bool:
        if actual is None or expected is None:
            return actual is None and expected is None
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            return math.isclose(float(actual), float(expected), abs_tol=0.005)
        return str(actual) == str(expected)

    def _independent_school_compare_side(
        self,
        contract: AnalysisContract,
        *,
        school_id: int,
        school_name: str,
        connection: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        if connection is None:
            with closing(self._connect()) as conn:
                return self._independent_school_compare_side(
                    contract,
                    school_id=school_id,
                    school_name=school_name,
                    connection=conn,
                )
        school = connection.execute(
            "SELECT id,school_name,year FROM schools WHERE id=?", (school_id,)
        ).fetchone()
        if not school:
            raise ContractViolation(("replay_school_compare_entity_unresolved",))
        expected_period = (
            contract.secondary_period
            if school_id == contract.secondary_school_id and contract.secondary_period
            else contract.period
        )
        if str(school["school_name"] or "") != school_name or str(school["year"] or "") != expected_period:
            raise ContractViolation(("replay_school_compare_binding",))
        marks = ",".join("?" for _ in contract.indicator_ids)
        rows = connection.execute(
            f"SELECT question_id,normalized_value FROM answers "
            f"WHERE school_id=? AND question_id IN ({marks}) ORDER BY question_id",
            (school_id, *contract.indicator_ids),
        ).fetchall()
        values = {
            int(row["question_id"]): max(0.0, min(100.0, float(row["normalized_value"]) * 100.0))
            for row in rows
            if row["normalized_value"] is not None
        }
        from packages.core.item_collection import collected_ids
        if (
            (contract.metric_spec or {}).get("complete_template_required") is True
            and len(values) != len(collected_ids(expected_period, contract.indicator_ids))
        ):
            raise ContractViolation(("incomplete_school_compare_template",))
        dimensions: Dict[str, Optional[float]] = {}
        dimension_answered: Dict[str, int] = {}
        dimension_totals: Dict[str, int] = {}
        total = 0.0
        covered_weight = 0.0
        weight_sum = 0.0
        for dimension in ((contract.metric_spec or {}).get("dimensions") or ()):
            name = str(dimension.get("name") or "")
            qids = tuple(int(qid) for qid in (dimension.get("indicator_ids") or ()))
            dimension_values = [values[qid] for qid in qids if qid in values]
            score = round(math.fsum(dimension_values) / len(dimension_values), 2) if dimension_values else None
            dimensions[name] = score
            dimension_answered[name] = len(dimension_values)
            dimension_totals[name] = len(qids)
            weight = float(dimension.get("weight") or 0)
            weight_sum += weight
            if score is not None:
                total += score * weight
                covered_weight += weight
        total_score = round(total / covered_weight, 2) if covered_weight else None
        return {
            "school_id": school_id,
            "school_name": school_name,
            "period": contract.period,
            "total_score": total_score,
            "dimensions": dimensions,
            "dimension_answered": dimension_answered,
            "dimension_totals": dimension_totals,
            "answered": len(values),
            "total_metrics": len(contract.indicator_ids),
            "data_coverage": round(len(values) / len(contract.indicator_ids), 2) if contract.indicator_ids else 0.0,
            "overall_confidence": round(covered_weight / weight_sum, 2) if weight_sum else 0.0,
        }

    def _independent_school_fact(
        self,
        contract: AnalysisContract,
        *,
        connection: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        if connection is None:
            with closing(self._connect()) as conn:
                return self._independent_school_fact(contract, connection=conn)
        school = connection.execute(
            "SELECT * FROM schools WHERE id=?", (int(contract.school_id),)
        ).fetchone()
        if not school:
            raise ContractViolation(("replay_school_unresolved",))
        if str(school["school_name"] or "") != contract.entity_id or str(school["year"] or "") != contract.period:
            raise ContractViolation(("replay_school_binding",))
        if contract.metric_type == "question_set":
            specs = list((contract.metric_spec or {}).get("items") or [])
            marks = ",".join("?" for _ in contract.indicator_ids)
            rows = connection.execute(
                f"SELECT question_id,value,normalized_value FROM answers WHERE school_id=? AND question_id IN ({marks})",
                (int(contract.school_id), *contract.indicator_ids),
            ).fetchall()
            row_map = {int(row["question_id"]): row for row in rows}
            is_norm = contract.value_view == "normalized_score"
            items = []
            for qid, spec in zip(contract.indicator_ids, specs):
                r = row_map.get(qid)
                if r is None:
                    value = None
                    display = None
                elif is_norm:
                    norm = r["normalized_value"]
                    value = norm
                    display = round(norm * 100, 1) if norm is not None else None
                else:
                    value = r["value"]
                    display = int(value) if value is not None and value == int(value) else value
                items.append({
                    "question_id": qid,
                    "metric_name": str(spec["name"]),
                    "target_value": display,
                    "unit": "分" if is_norm else str(spec.get("unit") or ""),
                    "coding_status": str(spec.get("coding_status") or "not_applicable"),
                    "fact_data_status": "ANSWER" if display is not None else "NO_DATA",
                })
            complete = all(item["fact_data_status"] == "ANSWER" for item in items)
            derived_result = None
            derived = (contract.metric_spec or {}).get("derived")
            if complete and isinstance(derived, dict):
                left, right = (float(item["target_value"]) for item in items)
                op = derived["operation"]
                norm_op = "add" if op in {"sum", "add"} else ("divide" if op in {"ratio", "divide"} else op)
                if norm_op in {"divide_percent", "divide"} and right == 0:
                    complete = False
                else:
                    if norm_op == "subtract":
                        value = left - right
                        unit = items[0]["unit"]
                    elif norm_op == "divide_percent":
                        value = left / right * 100
                        unit = "%"
                    elif norm_op == "add":
                        value = left + right
                        unit = items[0]["unit"]
                    elif norm_op == "divide":
                        value = left / right
                        unit = "倍"
                    else:
                        value = left - right
                        unit = items[0]["unit"]
                    derived_result = {
                        "operation": norm_op,
                        "left_qid": contract.indicator_ids[0],
                        "right_qid": contract.indicator_ids[1],
                        "value": round(value, 6),
                        "unit": unit,
                    }
            return {
                "item_results": items,
                "derived_result": derived_result,
                "fact_data_status": "ANSWER" if complete else "NO_DATA",
                "answer_count": sum(item["fact_data_status"] == "ANSWER" for item in items),
            }
        if contract.metric_type == "school_field":
            field_name = str((contract.metric_spec or {}).get("field") or "")
            if field_name not in {"teacher_num", "student_num", "class_num", "school_area_type", "school_type"}:
                raise ContractViolation(("replay_unsupported_school_field",))
            value = school[field_name]
            missing_value = value is None or (isinstance(value, str) and not value.strip())
            return {
                "target_value": None if missing_value else value,
                "fact_data_status": "NO_DATA" if missing_value else "ANSWER",
                "answer_count": 0 if missing_value else 1,
            }
        if contract.metric_type == "question":
            row = connection.execute(
                "SELECT value,normalized_value FROM answers WHERE school_id=? AND question_id=?",
                (int(contract.school_id), int(contract.indicator_ids[0])),
            ).fetchone()
            value = row["value"] if row else None
            normalized = row["normalized_value"] if row else None
            display = int(value) if value is not None and value == int(value) else value
            return {
                "target_value": display,
                "normalized_score": round(float(normalized) * 100, 1) if normalized is not None else None,
                "fact_data_status": "ANSWER" if row is not None and value is not None else "NO_DATA",
                "answer_count": 1 if row is not None and value is not None else 0,
            }
        marks = ",".join("?" for _ in contract.indicator_ids)
        rows = connection.execute(
            f"SELECT question_id,value FROM answers WHERE school_id=? AND question_id IN ({marks})",
            (int(contract.school_id), *contract.indicator_ids),
        ).fetchall()
        values = [float(row["value"]) for row in rows if row["value"] is not None]
        total = math.fsum(values)
        total_display = int(total) if total == int(total) else total
        answered_ids = {int(row["question_id"]) for row in rows if row["value"] is not None}
        component_complete = answered_ids == set(contract.indicator_ids)
        replay: Dict[str, Any] = {
            "target_value": None,
            "component_total": total_display if values else None,
            "component_answer_count": len(values),
            "component_complete": component_complete,
            "fact_data_status": "NO_DATA",
            "answer_count": len(values),
        }
        compound_key = str((contract.metric_spec or {}).get("compound_key") or "")
        student_value = school["student_num"]
        teacher_value = school["teacher_num"]
        student_count = (
            0.0
            if student_value is None or (isinstance(student_value, str) and not student_value.strip())
            else float(student_value)
        )
        teacher_count = (
            0.0
            if teacher_value is None or (isinstance(teacher_value, str) and not teacher_value.strip())
            else float(teacher_value)
        )
        if (
            compound_key == "生机比" and component_complete and total > 0
            and student_count > 0
        ):
            replay["ratio"] = round(student_count / total, 2)
            replay["target_value"] = replay["ratio"]
            replay["fact_data_status"] = "ANSWER"
        elif (
            compound_key == "师机比" and component_complete
            and teacher_count > 0
        ):
            replay["ratio"] = round(total / teacher_count, 2)
            replay["target_value"] = replay["ratio"]
            replay["fact_data_status"] = "ANSWER"
        elif compound_key not in {"生机比", "师机比"} and component_complete:
            replay["target_value"] = total_display
            replay["fact_data_status"] = "ANSWER"
        return replay

    def _independent_replay(
        self,
        contract: AnalysisContract,
        *,
        connection: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        if contract.region_identity is not None:
            from packages.semantic_query.typed_plan import region_sql
            region = region_sql(contract.region_identity)
        else:
            region = detect_region(contract.region_keyword, connection=connection)
        if not region:
            raise ContractViolation(("replay_region_unresolved",))
        filter_sql, filter_params = _filter_sql(contract.school_filter)
        marks = ",".join("?" for _ in contract.indicator_ids)
        where = region["where_sql"].replace("city", "s.city").replace("district", "s.district").replace("province", "s.province")
        val_col = "a.value" if getattr(contract, "value_view", None) == "raw_value" and getattr(contract, "metric_type", None) == "question" else "a.normalized_value"
        scale = 1 if getattr(contract, "value_view", None) == "raw_value" and getattr(contract, "metric_type", None) == "question" else 100
        sql = (
            f"SELECT s.id,{val_col} FROM schools s JOIN answers a ON a.school_id=s.id "
            f"WHERE ({where}) AND s.year=?{filter_sql} AND a.question_id IN ({marks}) "
            f"AND {val_col} IS NOT NULL ORDER BY s.id,a.question_id"
        )
        unfiltered_sql = f"SELECT COUNT(*) FROM schools s WHERE ({where}) AND s.year=?"
        params = (*region["where_params"], contract.period, *filter_params, *contract.indicator_ids)
        unfiltered_params = (*region["where_params"], contract.period)
        grouped = defaultdict(list)
        if connection is not None:
            total_unfiltered_count = int(connection.execute(unfiltered_sql, unfiltered_params).fetchone()[0])
            rows = connection.execute(sql, params)
            for row in rows:
                grouped[int(row[0])].append(float(row[1]))
        else:
            with closing(self._connect()) as conn:
                total_unfiltered_count = int(conn.execute(unfiltered_sql, unfiltered_params).fetchone()[0])
                for row in conn.execute(sql, params):
                    grouped[int(row[0])].append(float(row[1]))
        if not grouped:
            return {"total_score": None, "median_score": None, "max_score": None, "min_score": None, "ranked_schools": [], "total_unfiltered_count": total_unfiltered_count, "school_count": 0, "answer_count": 0}
        school_scores = [math.fsum(values) / len(values) * scale for values in grouped.values()]
        ranked_schools = sorted(
            ((school_id, math.fsum(values) / len(values) * scale) for school_id, values in grouped.items()),
            key=lambda item: (-item[1], item[0]),
        )
        answer_count = sum(len(values) for values in grouped.values())
        total_score = (
            math.fsum(school_scores) / len(school_scores)
            if contract.aggregation == "school_equal_mean"
            else math.fsum(value for values in grouped.values() for value in values) / answer_count * scale
        )
        return {
            "total_score": total_score,
            "median_score": statistics.median(school_scores),
            "max_score": max(school_scores),
            "min_score": min(school_scores),
            "ranked_schools": ranked_schools,
            "total_unfiltered_count": total_unfiltered_count,
            "school_count": len(school_scores),
            "answer_count": answer_count,
        }
