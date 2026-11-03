"""M09 · REST API（FastAPI，可降级）。

FastAPI 未安装时降级为 BaseApp（仅提供 serve() 提示 + 核心逻辑可直接 import）；
安装 fastapi+uvicorn 后提供完整端点（对齐《系统设计文档-v0.1.md》§6 API 草案）：

  POST /api/v1/chat              对话式评估入口（宽泛问题 → 结构报告+证据）
  POST /api/v1/evaluations       发起批量评估（同步；队列化留待后续）
  GET  /api/v1/evaluations/{id}  取单份结果
  GET  /api/v1/teachers/{id}/reports   教师历次报告
  GET  /api/v1/teachers/{id}/trends    多周期趋势（雷达数据，供 M10 用）
  GET  /api/v1/templates         模板列表（评估框架即配置的读接口）
  GET  /api/v1/teachers          演示教师列表
  GET  /api/v1/teachers/{id}/profile  教师简介（档案+数据覆盖）
  GET  /api/v1/schools           按校名检索
  GET  /api/v1/schools/{id}/profile   学校简介（档案+作答覆盖）
  GET  /api/v1/llm/status            模型密钥配置状态（不回完整 key）
  POST /api/v1/llm/config            保存官方/中转 API 地址与密钥
  POST /api/v1/llm/ping              探测模型接口是否通

只读原则：本模块不写任何业务库，结果驻留内存（持久化交给 M05/M11 之后）。
权限为占位（get_current_user 桩 + TODO），完整授权在 M11。
"""

import copy
import csv
import json
import os
import re
import sys
import uuid
from typing import List, Optional

_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from packages.core.frozen_paths import resource_root  # noqa: E402
from scripts.indicator_engine import evaluate_teacher, load_yaml  # noqa: E402
from packages.agent.planner import parse_question, plan  # noqa: E402
from packages.agent.critic import review as critic_review  # noqa: E402
from packages.agent.composer import compose, render_markdown  # noqa: E402
from packages.indicator_engine.calibration import attach_calibration  # noqa: E402
from packages.agent.router import route_query  # noqa: E402
from packages.analysis_contract import StaleBaseRevision  # noqa: E402
from packages.semantic_query.tool_adapter import school_fact_metric_input  # noqa: E402

ROOT = resource_root()

_MEMORY: dict = {}          # result_id -> 结果
_TEACHERS: List[str] = []
_TEMPLATE_CACHE = {}
_CONTRACT_STORE_RUNTIME_STATUS: Optional[dict] = None


def initialize_contract_store_status(*, force: bool = False) -> dict:
    """Turn store availability into diagnostic state, never a startup error."""
    global _CONTRACT_STORE_RUNTIME_STATUS
    if force or _CONTRACT_STORE_RUNTIME_STATUS is None:
        try:
            from packages.analysis_contract.state import contract_store_status
            _CONTRACT_STORE_RUNTIME_STATUS = contract_store_status()
        except Exception as exc:
            _CONTRACT_STORE_RUNTIME_STATUS = {
                "status": "unavailable",
                "error_code": "contract_store_unavailable",
                "error": str(exc),
                "fallback_used": False,
            }
    return dict(_CONTRACT_STORE_RUNTIME_STATUS)


def _public_path_name(value) -> Optional[str]:
    text = str(value or "").strip()
    return os.path.basename(text) if text else None


def _public_data_source_status(status: dict) -> dict:
    return {
        "status": status.get("status"),
        "active_file": _public_path_name(status.get("active_path")),
        "active_source": status.get("active_source"),
        "configured_by_environment": bool(status.get("configured_by_environment")),
        "read_only": bool(status.get("read_only")),
        "paths_redacted": True,
    }


def _public_contract_store_status(status: dict) -> dict:
    candidates = []
    for item in status.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append({
            "file": _public_path_name(item.get("path")),
            "source": item.get("source"),
        })
    return {
        "status": status.get("status"),
        "error_code": status.get("error_code"),
        "preferred_file": _public_path_name(status.get("preferred_path")),
        "preferred_source": status.get("preferred_source"),
        "candidates": candidates,
        "active_file": _public_path_name(status.get("active_path")),
        "active_source": status.get("active_source"),
        "fallback_used": bool(status.get("fallback_used")),
        "paths_redacted": True,
    }


def _public_active_contract_store(contract_service) -> dict:
    """Project an opened contract store into public response metadata."""
    return {
        "status": "available",
        "file": _public_path_name(contract_service.store.path),
        "source": contract_service.store.path_source,
        "paths_redacted": True,
    }


def _public_error_text(value, *, fallback: str) -> str:
    """Keep public diagnostics useful without echoing local absolute paths."""
    text = str(value or "").strip()
    if not text:
        return fallback
    if re.search(r"(?i)(?:[a-z]:[\\/]|\\\\)", text):
        return fallback
    return text


def _experiment_parser_error_code(value) -> str:
    """Promote only stable, allowlisted parser diagnostics to the public code."""
    detail = _public_error_text(value, fallback="experiment_llm_parse_failed")
    if re.fullmatch(
        r"experiment_explicit_statistic_mismatch:(?:median|mean_and_median)",
        detail,
    ):
        return detail
    if detail.startswith("experiment_parser_empty_content:"):
        return "experiment_parser_empty_content"
    if detail.startswith("experiment_parser_repair_failed:"):
        return "experiment_parser_repair_failed"
    if detail.startswith("experiment_parser_gateway_failed:"):
        return "experiment_parser_gateway_failed"
    return "experiment_llm_parse_failed"


def _experiment_parser_diagnostic(value) -> Optional[dict]:
    """Parse allowlisted parser summaries without exposing model text."""
    detail = _public_error_text(value, fallback="experiment_llm_parse_failed")
    prefixes = (
        "experiment_parser_empty_content:",
        "experiment_parser_repair_failed:",
        "experiment_parser_gateway_failed:",
    )
    prefix = next((item for item in prefixes if detail.startswith(item)), None)
    if prefix is None:
        return None
    try:
        payload = json.loads(detail[len(prefix):])
    except (TypeError, ValueError):
        return None
    attempts = []
    allowed_finish_reasons = {None, "stop", "length", "content_filter", "tool_calls", "other"}
    allowed_sources = {"content", "reasoning_json_object"}
    allowed_variants = {"primary", "empty_content_retry", "parser_repair"}
    for item in payload.get("attempts") or []:
        if not isinstance(item, dict):
            continue

        def bounded_int(field: str) -> Optional[int]:
            try:
                return max(0, int(item.get(field)))
            except (TypeError, ValueError):
                return None

        finish_reason = item.get("finish_reason")
        attempts.append({
            "max_tokens": bounded_int("max_tokens"),
            "finish_reason": finish_reason if finish_reason in allowed_finish_reasons else "other",
            "content_chars": bounded_int("content_chars"),
            "reasoning_chars": bounded_int("reasoning_chars"),
            "content_source": (
                item.get("content_source")
                if item.get("content_source") in allowed_sources
                else "content"
            ),
            "message_variant": (
                item.get("message_variant")
                if item.get("message_variant") in allowed_variants
                else "primary"
            ),
        })
    code = prefix[:-1]
    diagnostic = {
        "code": code,
        "attempts": attempts,
        "raw_content_exposed": False,
        "raw_reasoning_exposed": False,
    }
    if code in {"experiment_parser_repair_failed", "experiment_parser_gateway_failed"}:
        allowed_failures = {
            "experiment_parser_candidate_rejected",
            "experiment_parser_empty_content",
            "experiment_parser_evidence_invalid",
            "experiment_parser_integer_field_invalid",
            "experiment_parser_intent_not_supported",
            "experiment_parser_json_object_required",
            "experiment_parser_missing_json_object",
            "experiment_parser_period_invalid",
            "experiment_parser_requested_output_not_supported",
            "experiment_parser_requested_outputs_invalid",
            "experiment_parser_school_filter_invalid",
            "experiment_parser_unknown_evidence_fields",
            "experiment_parser_unknown_fields",
            "experiment_school_fact_requires_one_question_id",
            "experiment_question_id_not_in_catalogue",
            "experiment_compound_metric_not_in_catalogue",
            "experiment_school_field_not_in_catalogue",
            "experiment_dimension_not_in_catalogue",
            "experiment_statistic_not_supported",
            "experiment_aggregation_not_supported",
            "experiment_value_view_not_supported",
            "experiment_school_fact_scope_incomplete",
            "experiment_school_evaluation_scope_incomplete",
            "experiment_school_compare_scope_incomplete",
            "experiment_region_compare_scope_incomplete",
            "experiment_region_longitudinal_scope_incomplete",
            "experiment_region_ranking_scope_incomplete",
            "experiment_region_ranking_target_invalid",
            "experiment_region_scope_incomplete",
            "experiment_region_item_dimension_mismatch",
            "experiment_explicit_statistic_mismatch",
            "experiment_parser_gateway_auth_rejected",
            "experiment_parser_gateway_connect_failed",
            "experiment_parser_gateway_connection_interrupted",
            "experiment_parser_gateway_endpoint_not_found",
            "experiment_parser_gateway_failed",
            "experiment_parser_gateway_profile_unavailable",
            "experiment_parser_gateway_rate_limited",
            "experiment_parser_gateway_timeout",
            "experiment_parser_gateway_upstream_unavailable",
        }
        if code == "experiment_parser_gateway_failed":
            stage = payload.get("stage")
            diagnostic["stage"] = stage if stage in {"primary", "parser_repair"} else "primary"
        for field in ("initial_failure", "final_failure"):
            if field not in payload:
                continue
            failure = payload.get(field)
            diagnostic[field] = (
                failure if failure in allowed_failures
                else "experiment_parser_candidate_rejected"
            )
    return diagnostic


def _public_chart_artifact(artifact: dict) -> dict:
    """Expose a chart handle without its server-local filesystem location."""
    public = dict(artifact or {})
    local_path = (
        public.pop("file_path", None)
        or public.pop("image_path", None)
        or public.pop("path", None)
    )
    if not public.get("filename") and local_path:
        public["filename"] = _public_path_name(local_path)
    public["paths_redacted"] = True
    return public


def _public_tool_call(tool_call: dict, charts_by_id: dict) -> dict:
    """Project tool evidence while reusing the redacted public chart objects."""
    public = dict(tool_call or {})
    public_artifacts = []
    for artifact in public.get("artifacts") or []:
        artifact_id = artifact.get("id") if isinstance(artifact, dict) else None
        projected = charts_by_id.get(artifact_id) if artifact_id else None
        public_artifacts.append(
            projected if projected is not None else _public_chart_artifact(artifact)
        )
    public["artifacts"] = public_artifacts
    for field in ("stdout", "stderr", "error"):
        if public.get(field):
            public[field] = _public_error_text(
                public[field], fallback=f"tool_{field}_path_redacted"
            )
    return public


def _presentation_verification(result: dict, tool_calls: list, charts: list, *, requested_outputs=None) -> dict:
    """Describe post-contract chart delivery without changing trusted computation state."""
    contract = result.get("contract") or {}
    requested_outputs = tuple(contract.get("requested_outputs") or ()) if requested_outputs is None else tuple(requested_outputs)
    chart_requested = "charts" in requested_outputs
    business_status = str(result.get("fact_data_status") or "ANSWER").upper()
    chart_required = chart_requested and business_status == "ANSWER"
    failed_calls = [
        call for call in (tool_calls or [])
        if isinstance(call, dict) and call.get("success") is False
    ]

    if not chart_requested:
        status = "NOT_APPLICABLE"
        failures = []
        reason = "chart_not_requested"
    elif not chart_required:
        status = "NOT_APPLICABLE"
        failures = []
        reason = "business_status_has_no_chart"
    elif failed_calls:
        status = "FAIL"
        failures = ["chart_generation_failed"]
        reason = "chart_tool_failed"
    elif not charts:
        status = "FAIL"
        failures = ["missing_requested_chart"]
        reason = "chart_artifact_missing"
    else:
        status = "PASS"
        failures = []
        reason = "chart_artifact_generated"

    from packages.agent.tools.chart_scope import (
        validate_dimension_compare_charts, validate_overall_compare_charts,
        validate_dimension_pair_charts, validate_school_item_set_total_chart,
    )
    chart_scope = validate_dimension_compare_charts(result, charts) if chart_required else {"status": "NOT_APPLICABLE", "failures": []}
    if chart_required and chart_scope["status"] == "NOT_APPLICABLE":
        chart_scope = validate_overall_compare_charts(result, charts)
    if chart_required and chart_scope["status"] == "NOT_APPLICABLE":
        chart_scope = validate_dimension_pair_charts(result, charts)
    if chart_required and chart_scope["status"] == "NOT_APPLICABLE":
        chart_scope = validate_school_item_set_total_chart(result, charts)
    if chart_scope["status"] == "FAIL":
        status = "FAIL"
        failures = list(dict.fromkeys(failures + chart_scope["failures"]))
        reason = "chart_rendered_scope_failed"

    return {
        "status": status,
        "scope": "presentation_delivery",
        "rendered_scope": chart_scope,
        "task_id": contract.get("task_id"),
        "contract_revision": contract.get("revision"),
        "execution_id": (result.get("execution_receipt") or {}).get("execution_id"),
        "chart_requested": chart_requested,
        "chart_required": chart_required,
        "chart_attempted": bool(tool_calls),
        "chart_count": len(charts or []),
        "reason": reason,
        "failures": failures,
        "trusted_computation_unchanged": True,
    }


def _apply_presentation_coverage(response: dict) -> None:
    """Overlay truthful delivery coverage while preserving the committed core PASS."""
    presentation = response.get("presentation_verification") or {}
    verification = response.get("contract_verification")
    if not isinstance(verification, dict) or not presentation.get("chart_requested"):
        return

    core_status = verification.get("status")
    checks = [dict(item) for item in (verification.get("checks") or [])]
    coverage = next(
        (item for item in checks if item.get("name") == "requested_output_coverage"),
        None,
    )
    if coverage is None:
        coverage = {"name": "requested_output_coverage", "status": "NOT_CHECKED"}
        checks.append(coverage)

    coverage["core_result_status"] = coverage.get("status")
    coverage["core_result_scope"] = "trusted_data_and_computation"
    coverage["scope"] = "end_to_end_requested_output_delivery"
    coverage["presentation_status"] = presentation.get("status")
    if presentation.get("status") == "FAIL":
        coverage["status"] = "FAIL"
        coverage["failures"] = list(presentation.get("failures") or ())
    elif presentation.get("status") == "NOT_APPLICABLE":
        coverage["status"] = "NOT_APPLICABLE"
        coverage["reason"] = presentation.get("reason")
    elif presentation.get("status") == "NOT_CHECKED":
        coverage["status"] = "NOT_CHECKED"
        coverage["reason"] = presentation.get("reason")

    projected = dict(verification)
    projected["checks"] = checks
    projected["core_status"] = core_status
    projected["status_scope"] = "trusted_data_and_computation"
    projected["presentation_status"] = presentation.get("status")
    projected["presentation_failures"] = list(presentation.get("failures") or ())
    if presentation.get("status") == "FAIL":
        projected["overall_delivery_status"] = "DEGRADED"
    elif presentation.get("status") == "NOT_CHECKED":
        projected["overall_delivery_status"] = "NOT_CHECKED"
    else:
        projected["overall_delivery_status"] = core_status
    response["contract_verification"] = projected
    nested_result = response.get("result")
    if isinstance(nested_result, dict):
        nested_projection = dict(nested_result)
        nested_projection["contract_verification"] = dict(projected)
        nested_projection["presentation_verification"] = dict(presentation)
        response["result"] = nested_projection


def _load_teachers():
    global _TEACHERS
    if not _TEACHERS:
        p = os.path.join(ROOT, "data", "demo", "teacher.csv")
        try:
            with open(p, newline="", encoding="utf-8") as f:
                _TEACHERS = [r["teacher_id"] for r in csv.DictReader(f)]
        except Exception:
            _TEACHERS = []
    return _TEACHERS


def _load_tpl():
    if "tpl" not in _TEMPLATE_CACHE:
        _TEMPLATE_CACHE["tpl"] = load_yaml("2025-2026_v1")
    return _TEMPLATE_CACHE["tpl"]


def _run_evaluation(teacher_id: str, years: List[str]):
    tpl = _load_tpl()
    if tpl is None:
        raise ValueError("评估模板不可用（PyYAML 未安装或模板文件缺失）")
    res = evaluate_teacher(teacher_id, years, tpl)
    if "error" in res:
        raise ValueError(res["error"])
    attach_calibration(res, group_col="dept_id")
    result_id = str(uuid.uuid4())
    _MEMORY[result_id] = {"_id": result_id, **res}
    return _MEMORY[result_id]


def _finish(
    result: dict,
    question: Optional[str] = None,
    routed_info: Optional[dict] = None,
    *,
    requested_outputs: Optional[List[str]] = None,
    proposed_constraints: bool = True,
    answer_spec: Optional[dict] = None,
    verification_variant: Optional[str] = None,
) -> dict:
    report = compose(result)
    if answer_spec:
        from packages.semantic_query.answer_scope import project_report_scope

        report = project_report_scope(report, result, answer_spec, question or "")
    evidence = []
    for d in result.get("dimensions", []):
        evidence.extend(d.get("evidence", []))
    c = critic_review(result, evidence, with_llm=False)

    # 模块化 Agent 工具调用编排：自动调用本地 Python 工具画图
    tool_calls = []
    charts = []
    chart_requested = requested_outputs is None or "charts" in requested_outputs
    if chart_requested:
        try:
            from packages.agent.tools import orchestrator
            orch_res = orchestrator.orchestrate_visualization(
                result, question=question, answer_spec=answer_spec
            )
            tool_calls = orch_res.get("tool_calls", [])
            charts = orch_res.get("charts", [])
            report["tool_calls"] = tool_calls
            report["charts"] = charts
        except Exception as e:
            tool_calls = [{
                "tool": "orchestrator",
                "success": False,
                "error": _public_error_text(e, fallback="report_orchestrator_failed"),
            }]

    presentation = (
        _presentation_verification(result, tool_calls, charts, requested_outputs=requested_outputs)
        if proposed_constraints
        else {
            "status": "NOT_CHECKED" if verification_variant == "a2_no_output_verification_or_repair" else "NOT_APPLICABLE_UNCONSTRAINED",
            "scope": "ablation_a2_output_unchecked" if verification_variant == "a2_no_output_verification_or_repair" else "experiment_g1_delivery_observation",
            "chart_requested": chart_requested,
            "chart_attempted": bool(tool_calls),
            "chart_count": len(charts),
            "failures": [],
        }
    )
    report["presentation_verification"] = presentation

    # The chart builder consumes the same dimension rows as the trusted
    # computation.  Expose that source packet to the multimodal auditor so a
    # generated regional chart can be checked against the exact values used
    # for the plotted coordinates, rather than being left NOT_CHECKED.
    if charts and result.get("dimensions"):
        chart_dims = [
            {
                "dimension": item.get("dimension"),
                "score_a": item.get("score"),
            }
            for item in result.get("dimensions", [])
            if item.get("dimension") and item.get("score") is not None
        ]
        if chart_dims:
            report["radar_data"] = {
                item["dimension"]: item["score_a"] for item in chart_dims
            }
            report["dimensions_comparison"] = chart_dims

    # 质检审计与四大系统性能量化指标核算（针对基础结果）
    audit_data = None
    system_metrics = None
    try:
        if not proposed_constraints:
            raise LookupError("experiment_g1_skips_proposed_multimodal_audit")
        if verification_variant == "a1_no_execution_verification":
            from packages.agent.audit_agent import audit_output_only
            audit_res = audit_output_only(result, report, charts, llm_output=None)
        else:
            from packages.agent.audit_agent import audit_query_result
            audit_res = audit_query_result(
                question=question or str(result.get("school_name") or result.get("region_name") or ""),
                result=result, report=report, charts=charts,
                llm_output=None, routed_info=routed_info,
            )
        audit_data = audit_res.get("audit")
        system_metrics = audit_res.get("system_metrics")
        multimodal = (audit_data or {}).get("multimodal") or {}
        if presentation.get("chart_required") and multimodal.get("interlocked_status") == "FAIL":
            presentation["status"] = "FAIL"
            presentation["failures"] = list(dict.fromkeys(
                list(presentation.get("failures") or ()) + ["chart_validation_failed"]
            ))
            presentation["reason"] = "chart_delivery_or_validation_failed"
        report["audit"] = audit_data
        report["system_metrics"] = system_metrics
    except LookupError:
        audit_data = {
            "status": "NOT_CHECKED" if verification_variant == "a2_no_output_verification_or_repair" else "NOT_APPLICABLE_UNCONSTRAINED",
            "scope": "ablation_a2_output_unchecked" if verification_variant == "a2_no_output_verification_or_repair" else "experiment_g1_without_proposed_delivery_checks",
        }
        system_metrics = None
        report["audit"] = audit_data
        report["system_metrics"] = system_metrics
    except Exception:
        if presentation.get("chart_required") and presentation.get("status") == "PASS":
            presentation["status"] = "NOT_CHECKED"
            presentation["reason"] = "chart_validation_not_completed"
            presentation["failures"] = ["chart_validation_not_completed"]

    public_charts = [_public_chart_artifact(chart) for chart in charts]
    public_charts_by_id = {
        chart.get("id"): chart for chart in public_charts if chart.get("id")
    }
    public_tool_calls = [
        _public_tool_call(tool_call, public_charts_by_id)
        for tool_call in tool_calls
    ]
    report["charts"] = public_charts
    report["tool_calls"] = public_tool_calls

    if answer_spec:
        from packages.semantic_query.answer_scope import render_scoped_markdown

        markdown = render_scoped_markdown(result, answer_spec)
    else:
        markdown = render_markdown(result)
    return {
        "clarify": False,
        "result_id": result["_id"],
        "result": result,
        "report": report,
        "critic": c,
        "audit": audit_data,
        "system_metrics": system_metrics,
        "markdown": markdown,
        "tool_calls": public_tool_calls,
        "charts": public_charts,
        "presentation_verification": dict(presentation),
    }


def _unavailable_response(routed, session_id, request_id, base_revision, parser_trace=None):
    """Terminal data-availability message; no pending clarification or tools."""
    return dict(clarify=False, business_status='UNAVAILABLE', question=routed,
                message=routed['message'], markdown=routed['message'], session_id=session_id,
                tool_calls=[], fact_tool_calls=[], charts=[], fallback=False,
                revision_control=_revision_control(request_id, base_revision, 'NOT_COMMITTED_UNAVAILABLE'),
                llm_parser=parser_trace,
                execution_guard=dict(fact_execution_started=False, fact_tool_calls=0,
                                     pending_opened=False, revision_committed=False))


def _run_school_evaluation(
    keyword: str,
    years: List[str],
    *,
    target_dimension: Optional[str] = None,
    target_dimensions: Optional[List[str]] = None,
    indicator_ids: Optional[List[int]] = None,
    connection=None,
) -> dict:
    from scripts.school_engine import find_schools, evaluate_school, get_school
    year = years[0] if years else None
    sid = None
    if keyword.startswith("id:"):
        sid = int(keyword.split(":", 1)[1])
        school = get_school(sid, connection=connection)
        if not school:
            raise ValueError(f"学校 id={sid} 不存在")
    else:
        hits = find_schools(keyword, year=year, limit=8, connection=connection)
        if not hits:
            raise ValueError(f"未找到学校「{keyword}」" + (f"（年份 {year}）" if year else ""))
        if len(hits) > 1 and not year:
            from scripts.school_engine import expand_school_alias
            exp_kw = expand_school_alias(keyword)
            exact_hits = [h for h in hits if h["school_name"] in (keyword, exp_kw)]
            if exact_hits:
                sid = exact_hits[0]["id"]
            else:
                names = [f"{h['school_name']}（{h['year']}，id={h['id']}）" for h in hits[:6]]
                raise ValueError("匹配到多条学校记录，请补充年份或学校 id：" + "；".join(names))
        else:
            sid = hits[0]["id"]
    res = evaluate_school(
        sid,
        connection=connection,
        target_dimension=target_dimension,
        target_dimensions=target_dimensions,
        indicator_ids=indicator_ids,
    )
    if "error" in res:
        raise ValueError(res["error"])
    if res.get("total_score") is None:
        raise ValueError("该校在所选评估范围尚未录入有效问卷作答")
    result_id = str(uuid.uuid4())
    _MEMORY[result_id] = {"_id": result_id, **res}
    return _MEMORY[result_id]


def _run_school_fact(
    school_keyword: str,
    metric_info: dict,
    year: Optional[str] = None,
    connection=None,
) -> dict:
    from scripts.fact_engine import query_school_fact
    res = query_school_fact(school_keyword, metric_info, year=year, connection=connection)
    if "error" in res:
        raise ValueError(res["error"])
    result_id = str(uuid.uuid4())
    packed = {"_id": result_id, **res}
    _MEMORY[result_id] = packed
    return packed


_SESSIONS: dict = {}
_EXPERIMENT_SESSIONS: dict = {}


def _run_region_evaluation(
    region_keyword: str,
    year: Optional[str] = None,
    target_rank: Optional[int] = None,
    target_top_n: Optional[int] = None,
    target_dimension: Optional[str] = None,
    target_dimensions: Optional[List[str]] = None,
    target_stat: Optional[str] = None,
    school_filter: Optional[str] = None,
    original_query: Optional[str] = None,
    aggregation: Optional[str] = "school_equal_mean",
    operation: str = "region_evaluation",
    indicator_ids: Optional[List[int]] = None,
    value_view: Optional[str] = "normalized_score",
    connection=None,
    region_identity=None,
) -> dict:
    from scripts.region_engine import evaluate_region
    res = evaluate_region(
        region_keyword,
        year=year,
        target_rank=target_rank,
        target_top_n=target_top_n,
        target_dimension=target_dimension,
        target_dimensions=target_dimensions,
        target_stat=target_stat,
        school_filter=school_filter,
        original_query=original_query,
        aggregation=aggregation,
        operation=operation,
        indicator_ids=indicator_ids,
        value_view=value_view,
        connection=connection,
        region_identity=region_identity,
    )
    if "error" in res:
        raise ValueError(res["error"])
    result_id = str(uuid.uuid4())
    packed = {"_id": result_id, **res}
    _MEMORY[result_id] = packed
    return packed


def _run_school_ranking(school_keyword: str, region_keyword: Optional[str] = None, year: Optional[str] = None) -> dict:
    from scripts.region_engine import query_school_ranking
    res = query_school_ranking(school_keyword, region_keyword=region_keyword, year=year)
    if "error" in res:
        raise ValueError(res["error"])
    result_id = str(uuid.uuid4())
    packed = {"_id": result_id, **res}
    _MEMORY[result_id] = packed
    return packed


def _run_school_compare(
    school_a: str,
    school_b: str,
    year: Optional[str] = None,
    connection=None,
    require_complete_template: bool = False,
    target_dimension: Optional[str] = None,
) -> dict:
    from scripts.school_engine import compare_schools
    res = compare_schools(
        school_a,
        school_b,
        year=year,
        connection=connection,
        require_complete_template=require_complete_template,
    )
    if "error" in res:
        raise ValueError(res["error"])
    if target_dimension:
        matches = [row for row in res.get("dimensions_comparison") or [] if row.get("dimension") == target_dimension]
        if len(matches) != 1:
            raise ValueError("school_compare_dimension_unavailable")
        row = matches[0]
        score_a, score_b = float(row["score_a"]), float(row["score_b"])
        difference = round(score_a - score_b, 2)
        res.update({
            "target_dimension": target_dimension,
            "target_dimension_score_a": score_a,
            "target_dimension_score_b": score_b,
            "comparison_value_a": score_a,
            "comparison_value_b": score_b,
            "diff": difference,
            "direct_answer": (
                f"{res['school_name_a']}与{res['school_name_b']}{res['year_a']}年"
                f"【{target_dimension}】维度得分分别为{score_a:.2f}分、{score_b:.2f}分，"
                f"前者减后者为{difference:+.2f}分。"
            ),
        })
        res["execution_scope"]["dimension"] = target_dimension
    result_id = str(uuid.uuid4())
    packed = {"_id": result_id, **res}
    _MEMORY[result_id] = packed
    return packed


def _run_school_item_compare(
    school_a: str,
    school_b: str,
    metric_info: dict,
    year: Optional[str] = None,
    connection=None,
) -> dict:
    from scripts.fact_engine import query_school_fact
    r_a = query_school_fact(school_a, metric_info, year=year, connection=connection)
    r_b = query_school_fact(school_b, metric_info, year=year, connection=connection)
    if "error" in r_a:
        raise ValueError(r_a["error"])
    if "error" in r_b:
        raise ValueError(r_b["error"])

    field = "normalized_score" if metric_info.get("value_view") == "normalized_score" else "target_value"
    val_a = float(r_a[field]) if r_a.get(field) is not None else None
    val_b = float(r_b[field]) if r_b.get(field) is not None else None
    diff = round(val_a - val_b, 2) if val_a is not None and val_b is not None else None
    name_a = r_a.get("school_name") or school_a
    name_b = r_b.get("school_name") or school_b
    unit = "分" if field == "normalized_score" else r_a.get("unit") or r_b.get("unit") or ""
    metric_name = r_a.get("metric_name") or metric_info.get("name") or "指标"
    yr_str = f"{year}年" if year else ""

    text_a = f"{val_a:g}{unit}" if val_a is not None else "无数据"
    text_b = f"{val_b:g}{unit}" if val_b is not None else "无数据"
    delta = f"前者减后者差值为{diff:+.2f}{unit}。" if diff is not None else "缺失值不参与差值计算。"
    direct_answer = f"{name_a}{yr_str}“{metric_name}”为{text_a}，{name_b}为{text_b}，{delta}"
    result_id = str(uuid.uuid4())
    res = {
        "_id": result_id,
        "object_type": "school_item_compare",
        "operation": "item_compare",
        "analysis_operation": "item_compare",
        "school_name_a": name_a,
        "school_name_b": name_b,
        "school_a": name_a,
        "school_b": name_b,
        "year": year,
        "metric_name": metric_name,
        "unit": unit,
        "fact_data_status": "ANSWER" if diff is not None else "NO_DATA",
        "val_a": val_a,
        "val_b": val_b,
        "diff": diff,
        "requested_difference": diff,
        "direct_answer": direct_answer,
        "conclusion": direct_answer,
        "items": [
            {"school_name": name_a, "value": val_a, "unit": unit},
            {"school_name": name_b, "value": val_b, "unit": unit},
        ],
    }
    _MEMORY[result_id] = res
    return res


def _run_multi_school_compare(
    schools: List[str],
    year: Optional[str] = None,
    derived: Optional[str] = "middle_school",
    connection=None,
) -> dict:
    from scripts.school_engine import evaluate_school, find_schools
    school_results = []
    for s in schools:
        hits = find_schools(s, year=year, limit=1, connection=connection)
        if not hits:
            hits = find_schools(s, limit=1, connection=connection)
        if not hits:
            raise ValueError(f"未找到学校「{s}」")
        sid = hits[0]["id"]
        res = evaluate_school(sid, connection=connection)
        score = res.get("total_score")
        school_results.append({
            "school_id": sid,
            "school_name": hits[0]["school_name"],
            "score": score,
            "year": hits[0].get("year") or year,
        })

    sorted_schools = sorted(school_results, key=lambda x: x["score"] if x["score"] is not None else -999)
    mid_idx = len(sorted_schools) // 2
    middle_school = sorted_schools[mid_idx]

    score_strs = [f"{item['school_name']}综合得分为{item['score']:.2f}分" for item in sorted_schools]
    direct_answer = (
        f"在所选的三所学校中，{'，'.join(score_strs)}。"
        f"按综合得分排序后，排在中间的是【{middle_school['school_name']}】，综合得分为{middle_school['score']:.2f}分。"
    )
    result_id = str(uuid.uuid4())
    res = {
        "_id": result_id,
        "object_type": "multi_school_compare",
        "operation": "school_compare",
        "analysis_operation": "school_compare",
        "schools": school_results,
        "ordered_schools": sorted_schools,
        "middle": middle_school,
        "year": year,
        "direct_answer": direct_answer,
        "conclusion": direct_answer,
        "no_chart": True,
    }
    _MEMORY[result_id] = res
    return res


def _run_region_compare(
    region_a: str,
    region_b: str,
    year: Optional[str] = None,
    target_stat: Optional[str] = None,
    target_dimension: Optional[str] = None,
    target_dimensions: Optional[List[str]] = None,
    school_filter: Optional[str] = None,
    aggregation: str = "school_equal_mean",
    original_query: Optional[str] = None,
    connection=None,
) -> dict:
    from scripts.region_engine import compare_regions
    res = compare_regions(
        region_a,
        region_b,
        year=year,
        target_stat=target_stat,
        target_dimension=target_dimension,
        target_dimensions=target_dimensions,
        school_filter=school_filter,
        aggregation=aggregation,
        original_query=original_query,
        connection=connection,
    )
    if "error" in res:
        raise ValueError(res["error"])
    result_id = str(uuid.uuid4())
    packed = {"_id": result_id, **res}
    _MEMORY[result_id] = packed
    return packed


def _run_region_longitudinal(
    region_keyword: str,
    year_a: str,
    year_b: str,
    school_filter: Optional[str] = None,
    target_dimensions: Optional[List[str]] = None,
    target_dimension: Optional[str] = None,
    target_stat: Optional[str] = "mean",
    aggregation: str = "school_equal_mean",
    original_query: Optional[str] = None,
    connection=None,
) -> dict:
    from scripts.region_engine import evaluate_region_longitudinal
    res = evaluate_region_longitudinal(
        region_keyword,
        year_a,
        year_b,
        school_filter=school_filter,
        target_dimensions=target_dimensions,
        target_dimension=target_dimension,
        target_stat=target_stat,
        aggregation=aggregation,
        original_query=original_query,
        connection=connection,
    )
    if "error" in res:
        raise ValueError(res["error"])
    result_id = str(uuid.uuid4())
    packed = {"_id": result_id, **res}
    _MEMORY[result_id] = packed
    return packed


def _run_unconstrained_experiment(routed: dict) -> dict:
    """Execute G1 with the same deterministic tools but without proposed checks.

    This path intentionally does not create a task contract, revision, trusted
    focus, independent replay, or presentation-verification receipt. Basic
    typed tool arguments and read-only database boundaries still apply.
    """
    from packages.experiment_protocol.trace import stage_event

    def _invoke_tool(fn, *args, **kwargs):
        # Record the arguments actually passed to the shared tool, including
        # defaults and derived values that are absent from the routed draft.
        stage_event(
            "actual_tool_request",
            function=fn.__name__,
            args=copy.deepcopy(args),
            kwargs=copy.deepcopy(kwargs),
        )
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            stage_event("actual_tool_error", function=fn.__name__, error_type=type(exc).__name__)
            raise

    intent = routed.get("intent")
    if intent == "public_task":
        from packages.semantic_query.public_tasks import execute
        return _invoke_tool(execute, routed, verify_execution=False)
    if intent == "school_fact":
        return _invoke_tool(_run_school_fact,
            routed["school"],
            school_fact_metric_input(routed),
            year=routed.get("year"),
        )
    if intent == "school_eval":
        return _invoke_tool(_run_school_evaluation,
            routed["school"],
            routed.get("years") or [routed.get("year")],
            target_dimension=routed.get("target_dimension"),
            target_dimensions=routed.get("target_dimensions"),
        )
    if intent == "school_compare":
        return _invoke_tool(_run_school_compare,
            routed["school_a"],
            routed["school_b"],
            year=routed.get("year"),
            require_complete_template=True,
            target_dimension=routed.get("target_dimension"),
        )
    if intent == "school_item_compare":
        return _invoke_tool(_run_school_item_compare,
            routed["school_a"],
            routed["school_b"],
            school_fact_metric_input(routed),
            year=routed.get("year"),
        )
    if intent == "multi_school_compare":
        return _invoke_tool(_run_multi_school_compare,
            routed["schools"],
            year=routed.get("year"),
            derived=routed.get("derived"),
        )
    if intent == "region_compare":
        return _invoke_tool(_run_region_compare,
            routed["region_a"],
            routed["region_b"],
            year=routed.get("year"),
            target_stat=routed.get("target_stat"),
            target_dimension=routed.get("target_dimension"),
            target_dimensions=routed.get("target_dimensions"),
            school_filter=routed.get("school_filter"),
            aggregation=routed.get("aggregation", "school_equal_mean"),
            original_query=routed.get("original"),
        )
    if intent == "region_longitudinal":
        years = list(routed.get("years") or [])
        year_a = routed.get("year") or (years[0] if years else None)
        year_b = routed.get("secondary_year") or (years[1] if len(years) > 1 else None)
        if not year_a or not year_b:
            raise ValueError("experiment_region_longitudinal_scope_incomplete")
        return _invoke_tool(_run_region_longitudinal,
            routed["region"],
            str(year_a),
            str(year_b),
            school_filter=routed.get("school_filter"),
            target_dimensions=routed.get("target_dimensions"),
            target_dimension=routed.get("target_dimension"),
            target_stat=routed.get("target_stat") or "mean",
            aggregation=routed.get("aggregation", "school_equal_mean"),
            original_query=routed.get("original"),
        )
    if intent in {"region_eval", "region_ranking"}:
        from scripts.region_engine import detect_region
        from packages.semantic_query.typed_plan import region_identity
        bound_region = detect_region(routed["region"])
        if not bound_region:
            raise ValueError("unresolved_region")
        result = _invoke_tool(_run_region_evaluation,
            routed["region"],
            year=routed.get("year"),
            target_rank=routed.get("target_rank"),
            target_top_n=routed.get("target_top_n"),
            target_dimension=routed.get("target_dimension"),
            target_dimensions=routed.get("target_dimensions"),
            target_stat=("mean" if intent == "region_ranking" else routed.get("target_stat")),
            school_filter=routed.get("school_filter"),
            original_query=routed.get("original"),
            aggregation=("school_equal_mean" if intent == "region_ranking" else routed.get("aggregation", "school_equal_mean")),
            operation=("region_ranking" if intent == "region_ranking" else "region_evaluation"),
            value_view=routed.get("value_view") or "normalized_score",
            region_identity=region_identity(bound_region),
            indicator_ids=(
                [int((routed.get("metric") or {}).get("qid"))]
                if intent == "region_eval" and (routed.get("metric") or {}).get("type") == "question"
                else None
            ),
        )
        metric = routed.get("metric") or {}
        if intent == "region_eval" and metric.get("type") == "question":
            result.update({
                "metric_type": "question",
                "metric_key": f"question:{int(metric['qid'])}",
                "metric_name": metric.get("name"),
                "question_id": int(metric["qid"]),
                "question_content": metric.get("name"),
                "value_view": routed.get("value_view") or "normalized_score",
                "unit": metric.get("unit") or "分",
                "normalized_score": result.get("total_score"),
                "target_stat": routed.get("target_stat"),
            })
        return result
    raise ValueError("experiment_intent_not_supported_by_common_tools")


def _update_public_composition_context(ctx: dict, routed: dict, result: dict) -> None:
    """Project successful executed school nodes; never infer focus from prose.

    This is shared conversational memory, without trusted revision semantics.
    Ambiguous referents are kept as an ordered set for later user selection.
    """
    plan = result.get("public_plan") or {}
    queries = plan.get("queries") or []
    outputs = {x.get("id"): x for x in result.get("task_outputs") or []}
    if result.get("fact_data_status") != "ANSWER" or not queries:
        return
    # Restrict this bridge to executed school nodes at one annual scope.
    if any(q.get("kind") not in {"school_fact", "school_item_extremum"} or
           outputs.get(q.get("id"), {}).get("status") != "ANSWER" for q in queries):
        return
    years = list(dict.fromkeys(str(q.get("year") or "") for q in queries))
    names = list(dict.fromkeys(str(q.get("entity") or "") for q in queries))
    if len(years) != 1 or not years[0] or not 1 <= len(names) <= 3 or not all(names):
        return
    for key in list(ctx):
        if key.startswith("last_") or key.startswith("pending_") or key == "_pending_clarification":
            ctx.pop(key, None)
    ctx.update(last_year=years[0], last_scope_object_type="school",
               last_scope_operation="school_compare" if len(names) > 1 else "school_fact",
               last_scope_granularity="item", last_successful_school_candidates=names,
               last_school=names[0] if len(names) == 1 else None,
               last_school_compare_a=names[0] if len(names) > 1 else None,
               last_school_compare_b=names[1] if len(names) > 1 else None,
               public_context_source="successful_public_composition",
               public_context_plan_fingerprint=result.get("plan_fingerprint"),
               public_context_result_id=result.get("_id"),
               public_context_revision_semantics="NOT_APPLICABLE_PUBLIC_COMPOSITION")
    views = list(dict.fromkeys(q.get("view") for q in queries))
    if len(views) == 1:
        ctx["last_value_view"] = views[0]


def _update_unconstrained_experiment_context(ctx: dict, routed: dict, result: dict) -> None:
    """Maintain ordinary G1 conversation context without trusted-contract semantics."""
    for key in list(ctx):
        if key.startswith("pending_") or key == "_pending_clarification":
            ctx.pop(key, None)
    intent = routed.get("intent")
    # Only this successful-execution path publishes the view. Clarification
    # drafts and failed requests cannot replace it.
    ctx["last_value_view"] = routed.get("value_view") or (
        "raw_value" if intent == "school_fact" else "normalized_score"
    )
    year = routed.get("year") or result.get("year")
    if year:
        ctx["last_year"] = str(year)
    if intent == "school_fact":
        ctx["last_school_compare_a"] = ctx["last_school_compare_b"] = None
        school_name = result.get("school_name") or routed.get("school")
        if school_name:
            ctx["last_school"] = school_name
            ctx["last_school_fact_entity"] = school_name
        if routed.get("metric_set") or routed.get("metric"):
            ctx["last_school_fact_metric"] = dict(routed.get("metric_set") or routed.get("metric"))
        ctx["last_region"] = None
        ctx["last_dimension"] = None
    elif intent == "school_eval":
        ctx["last_school_compare_a"] = ctx["last_school_compare_b"] = None
        ctx["last_school"] = result.get("school_name") or routed.get("school")
        ctx["last_dimension"] = routed.get("target_dimension")
        ctx["last_region"] = None
        ctx["last_school_fact_entity"] = None
        ctx["last_school_fact_metric"] = None
    elif intent == "school_compare":
        ctx["last_scope_operation"] = "school_compare"
        ctx["last_scope_object_type"] = "school"
        ctx["last_school"] = None
        ctx["last_school_compare_a"] = result.get("school_name_a") or routed.get("school_a")
        ctx["last_school_compare_b"] = result.get("school_name_b") or routed.get("school_b")
    elif intent == "region_compare":
        ctx["last_region"] = None
        ctx["last_region_compare_a"] = result.get("region_name_a") or routed.get("region_a")
        ctx["last_region_compare_b"] = result.get("region_name_b") or routed.get("region_b")
    elif intent == "region_longitudinal":
        years = list(routed.get("years") or [])
        ctx["last_region"] = result.get("short_name") or routed.get("region")
        if len(years) == 2:
            ctx["last_region_longitudinal_period_a"] = years[0]
            ctx["last_region_longitudinal_period_b"] = years[1]
            ctx["last_year"] = years[1]
    elif intent in {"region_eval", "region_ranking"}:
        ctx["last_region"] = result.get("short_name") or routed.get("region")
        ctx["last_school"] = None
        ctx["last_school_fact_entity"] = None
        ctx["last_school_fact_metric"] = None
        ctx["last_target_rank"] = routed.get("target_rank")
        ctx["last_target_top_n"] = routed.get("target_top_n")
    if intent in {"region_eval", "region_compare", "region_longitudinal", "region_ranking"}:
        ctx["last_dimension"] = routed.get("target_dimension")
        ctx["last_statistic"] = routed.get("target_stat")
        ctx["last_aggregation"] = routed.get("aggregation")
        ctx["last_school_filter"] = routed.get("school_filter")
    if intent in {"school_eval", "school_fact", "region_eval"}:
        metric = routed.get("metric") if isinstance(routed.get("metric"), dict) else None
        ctx["last_scope_object_type"] = "school" if intent.startswith("school_") else "region"
        ctx["last_scope_operation"] = intent
        ctx["last_scope_granularity"] = (
            "item" if intent == "school_fact" or metric is not None
            else "dimension" if routed.get("target_dimension")
            else "overall"
        )
        ctx["last_scope_metric_key"] = (
            f"question:{int(metric['qid'])}"
            if metric and metric.get("type") == "question" and metric.get("qid") is not None
            else None
        )
    else:
        ctx["last_scope_object_type"] = (
            "school" if intent == "school_compare"
            else "region" if intent in {"region_compare", "region_longitudinal", "region_ranking"}
            else None
        )
        ctx["last_scope_operation"] = intent
        ctx["last_scope_granularity"] = "multi_scope"
        ctx["last_scope_metric_key"] = None


def _run_national_ranking(
    year: Optional[str] = None,
    target_dimension: Optional[str] = None,
    top_n: Optional[int] = None,
    original_query: Optional[str] = None,
    verified_overall_top: bool = False,
    province_only: bool = False,
) -> dict:
    if verified_overall_top:
        from scripts.region_engine import evaluate_national_overall_top
        res = evaluate_national_overall_top(year, province_only=province_only)
    else:
        from scripts.region_engine import evaluate_national_ranking
        res = evaluate_national_ranking(
            year=year,
            target_dimension=target_dimension,
            top_n=top_n,
            original_query=original_query,
        )
    if "error" in res:
        raise ValueError(res["error"])
    result_id = str(uuid.uuid4())
    packed = {"_id": result_id, **res}
    _MEMORY[result_id] = packed
    return packed


def _pending_public(pending: Optional[dict], *, restored: bool = False, applied: bool = False) -> Optional[dict]:
    if not pending:
        return None
    return {
        "pending_id": pending.get("pending_id"),
        "status": pending.get("status"),
        "reason": pending.get("reason"),
        "missing": pending.get("missing") or [],
        "base_task_id": pending.get("base_task_id"),
        "base_revision": pending.get("base_revision"),
        "parent_pending_id": pending.get("parent_pending_id"),
        "restored": restored,
        "applied": applied,
        "resolution": pending.get("resolution"),
    }


def _clarification_execution_guard(
    routed: dict,
    pending: Optional[dict],
    *,
    model_parser_invoked: bool,
) -> dict:
    """Publish the actual no-business-execution receipt for a clarification.

    A durable pending is an allowed clarification-state write, not a business
    fact execution or contract revision. Exposing both facts prevents tests
    from having to infer zero side effects from an absent field while retaining
    the pending needed for a later bounded answer.
    """
    return {
        "version": "clarification-execution-guard-v1",
        "reason": routed.get("reason") or routed.get("clarify_reason"),
        "matched": True,
        "model_parser_invoked": bool(model_parser_invoked),
        "fact_execution_started": False,
        "fact_tool_calls": 0,
        "contract_prepared": False,
        "pending_opened": pending is not None,
        "revision_committed": False,
    }


def _clarification_allows_pending(routed: dict) -> bool:
    return str(routed.get("reason") or "") not in {
        "question_id_not_in_catalogue", "unsupported_school_fact_statistic",
        "unsupported_region_item_raw_aggregation", "unsupported_region_item_set",
        "unsupported_region_item_statistic_or_aggregation", "unsupported_school_compare_fact_metric",
        "unsupported_region_ranking_dimension",
        "unsupported_school_item_set", "unsupported_dimension_set",
        "unsupported_mixed_value_view_comparison", "unsupported_evaluation_raw_value",
        "unsupported_scope_cardinality", "scope_reference_not_unique",
        "selective_region_scope_required",
    }


def _public_clarification_prompt(routed: dict, original_question: str) -> str:
    """Keep parser field names out of the user-facing clarification."""
    missing = [str(item) for item in (routed.get("missing") or [])]
    reason = str(routed.get("reason") or routed.get("clarify_reason") or "")
    if reason == "device_category_required" or "device_category" in missing:
        audience = "教师" if "教师" in original_question else "学生" if "学生" in original_question else "这类"
        return (
            f"请明确学校统一配备的{audience}设备类型：台式机、笔记本电脑、平板电脑或移动终端？"
            "不同设备类别不能合并计数。"
        )
    return "；".join(missing) if missing else "请补充本次查询缺少的具体范围。"


def _is_pending_cancel(question: str) -> bool:
    normalized = re.sub(r"[\s，,。.!！?？]", "", question)
    return normalized in {
        "取消", "算了", "不用了", "先不查了", "先不查", "放弃",
        "取消这个问题", "取消这次查询", "取消刚才的问题", "放弃这次查询",
    }


def _is_explicit_new_task(question: str) -> bool:
    return bool(re.search(r"(?:换个|换一个|另起(?:一个)?|新建(?:一个)?|开始(?:一个)?)(?:新)?任务|另一个任务|重新开始", question))


def _should_apply_pending(pending: dict, question: str) -> bool:
    """Apply a pending patch only to a plausible clarification reply."""
    reason = pending.get("reason")
    context = pending.get("context") or {}
    if _is_explicit_new_task(question):
        return False
    # A complete object/year request stands on its own, even when it happens
    # to fill the missing field of an unrelated clarification.
    years = re.findall(r"20\d{2}", question)
    if years and re.search(r"(?:[\u4e00-\u9fff]{2,}(?:市|县|区|小学|中学|附中|学校)|\bid\s*:\s*\d+)", question, re.I):
        return False
    if context.get("last_year") and any(year != str(context["last_year"]) for year in years):
        if reason not in {"missing_year", "missing_region_longitudinal_period"}:
            return False
    missing = pending.get("missing") or []
    if reason == "device_category_required" or "device_category" in missing:
        # Only the requested category fills this field. A general short reply
        # such as '那就总体' must not activate school/device context.
        entity_text = re.sub(r"学校(?:统一)?(?:配备|配的)", "", question)
        if re.search(r"[\u4e00-\u9fff]{2,}(?:小学|中学|附中|高中|初中|学校|市|县|区)|\bid\s*:", entity_text, re.I):
            return False
        categories = {
            "desktop" if token.startswith("台式") else
            "laptop" if token.startswith("笔记本") else
            "tablet" if token.startswith("平板") else "mobile"
            for token in re.findall(r"台式机|台式电脑|笔记本(?:电脑)?|平板(?:电脑)?|移动终端", question)
        }
        return len(categories) == 1 and not re.search(r"不要|不查|取消|改查.*(?:市|县|区)", question)
    try:
        from scripts.region_engine import detect_region
        explicit_region = detect_region(question)
    except Exception:
        explicit_region = None
    pending_region = context.get("last_region")
    if reason == "school_population_scope_required":
        from packages.semantic_query.runtime import is_region_population_confirmation

        return is_region_population_confirmation(question)
    if reason in {"missing_year", "missing_region_longitudinal_period"}:
        if explicit_region and pending_region and explicit_region.get("name") != pending_region:
            return False
        return bool(re.search(r"20\d{2}\s*年?", question))
    if reason in {"missing_city", "missing_region_longitudinal_region"}:
        return bool(explicit_region)
    if reason in {"unresolved_school_pronoun", "missing_school"}:
        return bool(
            re.search(r"(?:学校|小学|中学|附中|高中|初中)", question)
            or re.search(r"\bid\s*:\s*\d+", question, flags=re.IGNORECASE)
        )
    return bool(
        len(question.strip()) <= 18
        and (
            question.strip().endswith(("呢", "呢？", "呢?"))
            or any(token in question for token in ("那就", "改成", "改为", "换成", "换为", "按默认", "沿用", "其他不变"))
        )
    )


def _parse_revision_restore(question: str) -> Optional[int]:
    patterns = (
        r"(?:恢复|回到|还原|切回)\s*(?:到\s*)?(?:修订|revision)\s*#?\s*(\d+)",
        r"(?:恢复|回到|还原|切回)\s*(?:到\s*)?第\s*(\d+)\s*(?:版|个版本|次修订)",
        r"(?:恢复|回到|还原|切回)\s*(?:到\s*)?版本\s*#?\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _looks_like_revision_restore(question: str) -> bool:
    return bool(
        re.search(r"(?:恢复|回到|还原|切回).{0,8}(?:修订|revision|版本|上一版)", question, flags=re.IGNORECASE)
    )


_PERSISTED_CONTEXT_REFERENCE_TOKENS = (
    "那", "它", "它们", "该校", "这所学校", "这两所学校", "两校", "二者", "这个学校",
    "该地区", "该市", "这个地区", "其他不变", "其余不变", "保持不变", "继续", "仍",
    "沿用", "上一轮", "刚才", "原来的", "换成", "换为", "改成", "改为", "改看", "只换", "只改",
    "取消筛选", "取消过滤", "不再筛选", "不再过滤", "去掉筛选", "去掉过滤",
    "不限学段", "不限城乡", "全部学校", "所有学校",
    "其中", "这一块", "这一维", "同年", "同地区", "同一年", "上一次成功",
)
_DURABLE_FOCUS_SCOPE_RETURN_RE = re.compile(
    r"(?:恢复|回到|返回|切回|退回)(?:到)?(?:此前|之前|原来)?(?:的)?"
    r"(?:(?P<object>学校|单校|区域|地区|区县)(?:的)?)?"
    r"(?:总体|整体|综合)(?:评估|情况|水平|表现|概况)?",
    flags=re.IGNORECASE,
)


def _durable_focus_object_hint(question: str) -> Optional[str]:
    """Return the explicit object class in a durable scope-return phrase."""
    match = _DURABLE_FOCUS_SCOPE_RETURN_RE.search(str(question or "").strip())
    if match is None:
        return None
    qualifier = str(match.group("object") or "").strip()
    if qualifier in {"学校", "单校"}:
        return "school"
    if qualifier in {"区域", "地区", "区县"}:
        return "region"
    return None


def _persisted_context_matches_object_hint(
    question: str, restored_context: dict
) -> bool:
    """Prevent an object-qualified return from inheriting another object type."""
    requested = _durable_focus_object_hint(question)
    if requested is None:
        return True
    stored = str(restored_context.get("last_scope_object_type") or "").strip()
    return stored == requested


def _needs_persisted_context_restore(question: str) -> bool:
    """Return whether an empty memory session must reload its trusted head.

    A model/profile or service restart clears process-local experiment context
    but not committed revisions. Explicit scope-return utterances are
    contextual follow-ups even without a pronoun; a bare fresh request such as
    ``请评估总体情况`` remains ambiguous and cannot inherit unrelated state.
    """
    text = str(question or "").strip()
    return bool(
        any(token in text for token in _PERSISTED_CONTEXT_REFERENCE_TOKENS)
        or _DURABLE_FOCUS_SCOPE_RETURN_RE.search(text)
        or text.endswith(("呢", "呢？", "呢?"))
    )



def _revision_control(
    request_id: str,
    provided_base_revision: Optional[int],
    outcome: str,
    **extra,
) -> dict:
    return {
        "request_id": request_id,
        "provided_base_revision": provided_base_revision,
        "outcome": outcome,
        **extra,
    }


def _committed_audit_revision_control(
    request_id: str,
    provided_base_revision: Optional[int],
    contract,
    *,
    failure_stage: str,
) -> dict:
    return _revision_control(
        request_id,
        provided_base_revision,
        "COMMITTED_AUDIT_ONLY",
        commit_status="COMMITTED",
        result_status="FAILED",
        failure_stage=failure_stage,
        focus_eligible=False,
        audit_only=True,
        accepted_base_revision=contract.parent_revision or 0,
        committed_revision=contract.revision,
    )


def _stale_revision_response(
    *,
    session_id: str,
    question: dict,
    request_id: str,
    provided_base_revision: Optional[int],
    error: StaleBaseRevision,
    contract_service,
) -> dict:
    return {
        "clarify": True,
        "question": question,
        "next_prompt": (
            f"该请求基于过期修订 revision {error.requested}，当前头部为 revision {error.current}；"
            "本次未提交，请基于当前修订重试。"
        ),
        "session_id": session_id,
        "business_status": "ERROR",
        "error_code": "stale_base_revision",
        "contract_mode": "enforced",
        "revision_control": _revision_control(
            request_id,
            provided_base_revision,
            "REJECTED_STALE_BASE",
            current_revision=error.current,
            conflict_attempt_id=error.attempt_id,
        ),
        "contract_store": _public_active_contract_store(contract_service),
    }


from packages.core.research_data import request_scope as _research_request_scope

@_research_request_scope
def chat(question: str, session_id: Optional[str] = "default", *, base_revision: Optional[int] = None,
         request_id: Optional[str] = None, query_mode: Optional[str] = None, fh_arm: Optional[str] = None,
         clarification_reply: Optional[dict] = None) -> dict:
    from packages.semantic_query import selective_clarification as selective
    with selective.capture_bindings():
        resolution = None
        service = None
        sid = session_id or "default"
        rid = request_id or str(uuid.uuid4())
        if clarification_reply is not None:
            if fh_arm is not None:
                return selective.stopped(sid, "clarification_flow_not_supported")
            from packages.analysis_contract import ContractService
            try:
                service = ContractService()
                from packages.experiment_protocol import experiment_mode_status
                from packages.semantic_query.mode import resolve_query_mode
                mode = experiment_mode_status()
                flow_key = str(mode.get("active_mode") or "") if mode.get("enabled") else resolve_query_mode(query_mode)["active_mode"]
                pending_sid = selective.pending_session(sid, mode)
                supplement = selective.bind_supplement(service.active_pending(pending_sid), clarification_reply)
                resolution = service.store.claim_selective_reply(
                    pending_sid, clarification_reply, request_id=rid, expected_head_revision=0 if pending_sid != sid else base_revision,
                    flow_key=flow_key, supplement=supplement)
            except (ValueError, StaleBaseRevision) as exc:
                return selective.stopped(sid, str(exc).split(":")[0], pending=service.active_pending(locals().get("pending_sid", sid)) if service else None)
            if resolution["action"] == "cancel":
                return {**selective.stopped(sid, "pending_cancelled"), "business_status": "CANCELLED",
                        "next_prompt": "已取消这条待澄清任务。"}
            if resolution["action"] == "none":
                return {**selective.stopped(sid, "clarification_needs_supplement", pending=resolution["pending"]),
                        "next_prompt": "请补充当前未决字段；也可以另起任务或取消。"}
            if resolution["action"] == "revise":
                routed = {"intent": "clarify", "reason": "selective_revision", "missing": ["请修改已确认字段。"],
                          "selective_clarification": resolution["new_spec"]}
                new_pending = service.open_pending(pending_sid, resolution["new_spec"]["working_question"], routed,
                                                   base_revision=0 if pending_sid != sid else base_revision, request_id=rid,
                                                   inherited_context=resolution["pending"].get("context") or {})
                if pending_sid != sid:
                    key = f"{mode.get('run_id_hash')}:{mode.get('active_mode')}:{sid}"
                    context = copy.deepcopy(_EXPERIMENT_SESSIONS.get(key) or {})
                    context["_pending_clarification"] = {"selective_pending_id": new_pending["pending_id"]}
                    _EXPERIMENT_SESSIONS[key] = context
                return {**selective.stopped(sid, "clarification_revision", pending=new_pending), "next_prompt": "请重新选择该字段；其余已确认约束保留。"}
            question = resolution["question"]
        from packages.core.research_data import REQUEST as research_request
        research_token = research_request.set(question) if resolution else None
        try:
            result = _chat_impl(question, session_id, base_revision=base_revision, request_id=rid,
                                query_mode=query_mode, fh_arm=fh_arm, selective_resolution=resolution)
            if resolution:
                result["clarification_resolution"] = {
                    "pending_id": resolution["pending"]["pending_id"], "slot": resolution["slot"],
                    "constraint": resolution["constraint"], "original_question": resolution["spec"]["original_question"],
                    "resolved_question": question, "extra_model_calls": 0,
                }
            return result
        finally:
            if research_token is not None:
                research_request.reset(research_token)
            if resolution and service:
                service.close_pending(resolution["pending"]["pending_id"], status="RESOLVED",
                                      resolution="confirmed_intent_attempt_finished")


def _chat_impl(
    question: str,
    session_id: Optional[str] = "default",
    *,
    base_revision: Optional[int] = None,
    request_id: Optional[str] = None,
    query_mode: Optional[str] = None,
    fh_arm: Optional[str] = None,
    selective_resolution: Optional[dict] = None,
) -> dict:
    """开放式多意图对话评估入口（支持多轮上下文与实体继承）：
    自由提问 → 上下文消解与路由 → 教师评估 / 学校评估 / 事实指标问答 / 区域大盘 / 相对排位 / 双校对比 / 双区域对比。

    返回 dict：
      {clarify: true, question, next_prompt, session_id}          需澄清引导
      {result_id, result, report, critic, markdown, session_id}   完成
    """
    from time import perf_counter
    request_started = perf_counter()
    sid = session_id or "default"
    rid = (request_id or str(uuid.uuid4())).strip()
    if not rid:
        rid = str(uuid.uuid4())
    if fh_arm is not None:
        from packages.task_query.runtime import execute_question
        return execute_question(question, fh_arm, rid, sid)
    from packages.experiment_protocol import experiment_mode_status
    experiment_mode = experiment_mode_status()
    if experiment_mode.get("configured") and not experiment_mode.get("enabled"):
        return {
            "session_id": sid,
            "business_status": "ERROR",
            "error_code": "experiment_mode_unavailable",
            "next_prompt": "实验模式配置不完整或尚未实现，已拒绝执行以避免混淆方法身份。",
            "experiment_mode": experiment_mode,
            "revision_control": _revision_control(
                rid, base_revision, "NOT_COMMITTED_EXPERIMENT_MODE_UNAVAILABLE"
            ),
        }
    if base_revision is not None and base_revision < 0:
        raise ValueError("base_revision_must_be_non_negative")
    active_experiment_mode = str(experiment_mode.get("active_mode") or "")
    llm_experiment = active_experiment_mode in {
        "g1_llm_unconstrained",
        "g2_llm_constrained",
        "a1_no_execution_verification",
        "a2_no_output_verification_or_repair",
        "a3_no_answer_repair",
    }
    mechanisms = experiment_mode.get("mechanisms") or {}
    g1_unconstrained = active_experiment_mode == "g1_llm_unconstrained"
    from packages.semantic_query.mode import resolve_query_mode
    product_query_mode = (
        {
            "active_mode": "experiment_controlled",
            "default_mode": None,
            "source": "experiment",
            "semantic_enabled": False,
            "rule_baseline_selected": False,
            "automatic_rule_fallback": False,
        }
        if experiment_mode.get("enabled")
        else resolve_query_mode(query_mode)
    )
    semantic_product = bool(product_query_mode.get("semantic_enabled"))
    experiment_session_key = (
        f"{experiment_mode.get('run_id_hash')}:{active_experiment_mode}:{sid}"
        if llm_experiment else sid
    )
    session_store = _EXPERIMENT_SESSIONS if llm_experiment else _SESSIONS
    # Parser and pending overlays belong to this request until a successful
    # state transition. In particular, parser ERROR must not mutate a cached
    # trusted focus or leave pending flags on the next request.
    ctx = copy.deepcopy(session_store.get(experiment_session_key, {}))
    session_was_empty = not ctx
    answer_spec = None
    llm_parser_trace = None
    semantic_profile_id = None

    # Treatment-invariant, explicit unsupported scopes close before the model
    # parser, pending-state access, contract creation or any deterministic fact
    # tool. The explicit empty tool list and receipt make zero side effects
    # auditable rather than inferred from missing fields.
    from packages.semantic_query.runtime import (
        preflight_school_fact_statistic_boundary,
        preflight_semantic_scope_boundary,
    )

    boundary_preflight = (
        preflight_semantic_scope_boundary(question)
        or preflight_school_fact_statistic_boundary(question)
    )
    if boundary_preflight is not None:
        if boundary_preflight.get('intent') == 'unavailable':
            return _unavailable_response(boundary_preflight, sid, rid, base_revision)
        execution_guard = dict(
            boundary_preflight.get("pre_execution_guard") or {}
        )
        response = {
            "clarify": True,
            "question": boundary_preflight,
            "next_prompt": "；".join(boundary_preflight.get("missing") or []),
            "session_id": sid,
            "business_status": "CLARIFY",
            "contract": None,
            "tool_calls": [],
            "fact_tool_calls": [],
            "fallback": False,
            "execution_guard": execution_guard,
            "contract_store": {
                "status": "not_opened_pre_execution_guard",
                "paths_redacted": True,
            },
            "revision_control": _revision_control(
                rid,
                base_revision,
                (
                    "NOT_APPLICABLE_EXPERIMENT_G1"
                    if g1_unconstrained
                    else "NOT_COMMITTED_CLARIFY"
                ),
            ),
        }
        if llm_experiment:
            response["experiment_mode"] = experiment_mode
            response["experiment_trace"] = {
                "preflight": execution_guard,
                "parser": {
                    "skipped": True,
                    "reason": boundary_preflight.get("reason"),
                },
            }
            if g1_unconstrained:
                response["contract_mode"] = "experiment_unconstrained"
        else:
            response["query_mode"] = product_query_mode
            response["semantic_trace"] = {
                "preflight": execution_guard,
                "parser": {
                    "skipped": True,
                    "reason": boundary_preflight.get("reason"),
                },
            }
        return response

    contract_service = None
    request_base_revision = base_revision
    active_pending = None
    pending_matches_focus = False
    pending_applied = False
    contract = None
    revision_restore = None
    restore_target = _parse_revision_restore(question)
    restore_intent = _looks_like_revision_restore(question)
    context_restore_error = None
    contract_store_runtime = (
        {
            "status": "not_applicable_experiment_g1",
            "error_code": None,
            "fallback_used": False,
        }
        if g1_unconstrained
        else initialize_contract_store_status()
    )
    contract_store_public = _public_contract_store_status(contract_store_runtime)
    contract_store_available = contract_store_runtime.get("status") == "available"
    if not contract_store_available and not g1_unconstrained:
        context_restore_error = str(
            contract_store_runtime.get("error")
            or contract_store_runtime.get("error_code")
            or "contract_store_unavailable"
        )
    context_restore = {"attempted": False, "restored": False}
    may_need_persisted_context = (
        _needs_persisted_context_restore(question)
        or bool(ctx) and (llm_experiment or semantic_product or ctx.get("contract_context_source") == "trusted_store")
    )
    if not g1_unconstrained and contract_store_available:
        try:
            from packages.analysis_contract import ContractService
            contract_service = ContractService()
            state = contract_service.session_state(sid, base_revision=base_revision, request_id=rid)
            request_base_revision = state["head_revision"]
            active_pending = state["pending"]
            focus = state["focus"]
            if selective_resolution and (not active_pending or active_pending["pending_id"] != selective_resolution["pending"]["pending_id"]):
                from packages.semantic_query import selective_clarification as selective
                return selective.stopped(sid, "clarification_task_changed")
            pending_matches_focus = bool(active_pending) and (
                active_pending.get("base_task_id") == (focus.task_id if focus else None)
                and active_pending.get("base_revision") == (focus.revision if focus else None)
            )
            restored_context = state["context"] if may_need_persisted_context else {}
            if (may_need_persisted_context and ctx.get("public_context_source") == "successful_public_composition"
                    and ctx.get("public_context_base_revision") == state["head_revision"]):
                # A newer successful composition has no revision of its own.
                # Keep its actual same-process focus while the store head is
                # unchanged; a later committed revision invalidates it.
                restored_context = copy.deepcopy(ctx)
            context_restore["attempted"] = bool(may_need_persisted_context)
            if llm_experiment or semantic_product or may_need_persisted_context:
                ctx = {}
            requested_object_type = _durable_focus_object_hint(question)
            stored_object_type = str(
                restored_context.get("last_scope_object_type") or ""
            ).strip() or None
            if restored_context and _persisted_context_matches_object_hint(
                question, restored_context
            ):
                ctx = copy.deepcopy(restored_context)
                context_restore.update({
                    "restored": True,
                    "source": restored_context.get("contract_context_source"),
                    "task_id": restored_context.get("contract_focus_task_id"),
                    "revision": restored_context.get("contract_focus_revision"),
                })
            elif restored_context and requested_object_type is not None:
                context_restore.update({
                    "reason": "trusted_focus_object_mismatch",
                    "requested_object_type": requested_object_type,
                    "stored_object_type": stored_object_type,
                })
        except StaleBaseRevision as exc:
            return _stale_revision_response(
                session_id=sid, question={"original": question}, request_id=rid,
                provided_base_revision=base_revision, error=exc, contract_service=contract_service,
            )
        except Exception as exc:
            contract_store_available = False
            ctx = {}
            context_restore_error = str(exc)
            context_restore["error_code"] = "contract_context_restore_failed"
            context_restore["error_type"] = type(exc).__name__
            return {
                "session_id": sid, "business_status": "ERROR",
                "error_code": "contract_context_restore_failed",
                "next_prompt": "无法读取持久任务状态，本轮未执行；请恢复状态库后重试。",
                "context_restore": context_restore,
                "contract_store": {**contract_store_public, "status": "unavailable", "error_code": "contract_store_unavailable"},
                "revision_control": _revision_control(rid, base_revision, "NOT_COMMITTED_STORE_UNAVAILABLE"),
            }

    from packages.semantic_query import selective_clarification as selective
    selective.set_binding_context(ctx, question)
    if g1_unconstrained:
        # G1's ordinary clarification memory is separate from successful focus.
        # Only a matching reply overlays this request; parser failures never
        # publish the overlay, and G1 still has no production Pending identity.
        volatile_pending = ctx.pop("_pending_clarification", None)
        if selective_resolution or (volatile_pending or {}).get("selective_pending_id"):
            from packages.analysis_contract import ContractService
            contract_service = ContractService()
            active_pending = contract_service.active_pending(selective.pending_session(sid, experiment_mode))
            if selective_resolution:
                if not active_pending or active_pending["pending_id"] != selective_resolution["pending"]["pending_id"]:
                    return selective.stopped(sid, "clarification_task_changed")
                ctx = contract_service.context_from_pending(active_pending)
                pending_applied = True
            elif active_pending and _is_pending_cancel(question):
                contract_service.close_pending(active_pending["pending_id"], status="CANCELLED", resolution="user_cancelled")
                return {**selective.stopped(sid, "pending_cancelled"), "business_status": "CANCELLED", "next_prompt": "已取消这条待澄清任务。"}
            elif active_pending and not _is_explicit_new_task(question) and len(question.strip()) <= 18 and not re.search(r"20\d{2}", question):
                return selective.stopped(sid, "clarification_requires_structured_reply", pending=active_pending)
            elif active_pending:
                contract_service.close_pending(active_pending["pending_id"], status="SUPERSEDED", resolution="new_independent_request")
                active_pending = None
        elif volatile_pending and not restore_intent and _should_apply_pending(volatile_pending, question):
            from packages.analysis_contract import ContractService
            ctx = ContractService.merge_pending_context(ctx, volatile_pending.get("context") or {})
            ctx["pending_clarification_reason"] = volatile_pending.get("reason")

    try:
        if g1_unconstrained:
            raise LookupError("experiment_g1_uses_isolated_in_memory_context")
        if not contract_store_available:
            raise RuntimeError(context_restore_error or "contract_store_unavailable")
        from packages.analysis_contract import ContractService
        contract_service = contract_service or ContractService()
        if active_pending and _is_pending_cancel(question):
            closed = contract_service.close_pending(
                active_pending["pending_id"],
                status="CANCELLED",
                resolution="user_cancelled",
                base_revision=request_base_revision, request_id=rid, session_id=sid,
            )
            return {
                "clarify": True,
                "question": {"intent": "clarify", "reason": "pending_cancelled", "original": question},
                "next_prompt": "已取消上一条待澄清任务；最后一个可信分析焦点未改变。",
                "session_id": sid,
                "business_status": "CANCELLED",
                "pending_clarification": _pending_public(closed, restored=session_was_empty),
                "contract_store": _public_active_contract_store(contract_service),
                "revision_control": _revision_control(
                    rid, base_revision, "NOT_COMMITTED_PENDING_CANCELLED"
                ),
            }
        if selective_resolution:
            ctx = contract_service.context_from_pending(selective_resolution["pending"])
            if selective_resolution["slot"] == "region":
                ctx["last_region"] = selective_resolution["constraint"]["full_name"]
                ctx["last_region_identity"] = selective_resolution["constraint"]
            elif selective_resolution["slot"] == "school":
                ctx["last_school"] = selective_resolution["constraint"]["school"]
            pending_applied = True
        elif active_pending and (active_pending.get("routed") or {}).get("selective_clarification"):
            # Free text is never a choice instruction. Explicit new questions
            # go through ordinary parsing; short replies keep this slot pending.
            if not _is_explicit_new_task(question) and len(question.strip()) <= 18 and not re.search(r"20\d{2}", question):
                return selective.stopped(sid, "clarification_requires_structured_reply", pending=active_pending)
            contract_service.close_pending(active_pending["pending_id"], status="SUPERSEDED", resolution="new_independent_request")
            active_pending = None
        elif active_pending and pending_matches_focus and not restore_intent and _should_apply_pending(active_pending, question):
            ctx = contract_service.context_from_pending(active_pending)
            pending_applied = True
        contract_service.set_pending_transition(active_pending, applied=pending_applied)
    except StaleBaseRevision as exc:
        return _stale_revision_response(
            session_id=sid, question={"original": question}, request_id=rid,
            provided_base_revision=base_revision, error=exc, contract_service=contract_service,
        )
    except LookupError:
        pass
    except Exception as exc:
        if context_restore_error is None:
            context_restore_error = str(exc)

    if _is_explicit_new_task(question):
        ctx = {}

    if g1_unconstrained and restore_intent:
        return {
            "clarify": True,
            "question": {"intent": "revision_restore", "target_revision": restore_target},
            "next_prompt": "G1不启用所提修订与恢复约束；请直接重新描述所需任务。",
            "session_id": sid,
            "business_status": "CLARIFY",
            "experiment_mode": experiment_mode,
            "contract_mode": "experiment_unconstrained",
            "revision_control": _revision_control(
                rid, base_revision, "NOT_APPLICABLE_EXPERIMENT_G1"
            ),
        }

    if restore_intent and restore_target is None:
        history = []
        if contract_store_available and contract_service is not None:
            head = contract_service.store.latest(sid)
            history = contract_service.store.revision_history(sid, head.task_id if head else None)
        return {
            "clarify": True,
            "question": {"intent": "revision_restore", "target_revision": None},
            "next_prompt": "请明确要恢复的revision编号，例如：恢复到revision 1。",
            "session_id": sid,
            "business_status": "CLARIFY",
            "revision_history": history,
            "contract_store": contract_store_public,
            "revision_control": _revision_control(
                rid, base_revision, "NOT_COMMITTED_RESTORE_TARGET_REQUIRED"
            ),
        }
    if restore_target is not None:
        if not contract_store_available or contract_service is None:
            return {
                "clarify": True,
                "question": {"intent": "revision_restore", "target_revision": restore_target},
                "next_prompt": "契约状态库不可用，不能安全恢复历史修订。",
                "session_id": sid,
                "business_status": "ERROR",
                "contract_store": contract_store_public,
                "revision_control": _revision_control(
                    rid, base_revision, "NOT_COMMITTED_STORE_UNAVAILABLE"
                ),
            }
        try:
            contract = contract_service.restore_revision(
                sid,
                restore_target,
                question,
                base_revision=request_base_revision,
                request_id=rid,
            )
            routed = contract_service.routed_from_contract(contract, question)
            revision_restore = routed["revision_restore"]
            if llm_experiment or semantic_product:
                from packages.semantic_query import build_answer_spec
                answer_spec = build_answer_spec(question, routed)
        except StaleBaseRevision as exc:
            return _stale_revision_response(
                session_id=sid,
                question={"intent": "revision_restore", "target_revision": restore_target},
                request_id=rid,
                provided_base_revision=base_revision,
                error=exc,
                contract_service=contract_service,
            )
        except ValueError as exc:
            head = contract_service.store.latest(sid)
            history = contract_service.store.revision_history(sid, head.task_id if head else None)
            return {
                "clarify": True,
                "question": {"intent": "revision_restore", "target_revision": restore_target},
                "next_prompt": "无法恢复该修订；请选择历史中标记为可恢复且早于当前头部的revision。",
                "session_id": sid,
                "business_status": "CLARIFY",
                "revision_restore_error": _public_error_text(
                    exc, fallback="invalid_revision_restore"
                ),
                "revision_history": history,
                "contract_store": _public_active_contract_store(contract_service),
                "revision_control": _revision_control(
                    rid, base_revision, "NOT_COMMITTED_INVALID_RESTORE_TARGET"
                ),
            }
    else:
        if selective_resolution:
            from packages.semantic_query import runtime as public_runtime
            candidate = selective_resolution.get("candidate")
            if candidate and candidate.get("intent") != "clarify":
                _, by_id = public_runtime._catalogue()
                routed = public_runtime._route_from_candidate(question, candidate, by_id, ctx)
            else:
                routed = route_query(question, context=ctx)
            answer_spec = public_runtime.build_answer_spec(question, routed)
            llm_parser_trace = {"source": "user_confirmed_saved_task", "model_calls": 0}
        elif llm_experiment:
            try:
                from packages.experiment_protocol.llm_common import parse_llm_route

                routed, answer_spec, llm_parser_trace = parse_llm_route(
                    question,
                    ctx,
                    experiment_mode,
                )
            except Exception as exc:
                parser_error = _public_error_text(
                    exc, fallback="experiment_llm_parse_failed"
                )
                parser_failure = {
                    "clarify": True,
                    "question": {"intent": "experiment_llm_parse_failed"},
                    "next_prompt": "实验LLM未能生成可执行的结构化任务，已停止本轮且未回退到规则路由。",
                    "session_id": sid,
                    "business_status": "ERROR",
                    "error_code": _experiment_parser_error_code(parser_error),
                    "parser_error": parser_error,
                    "experiment_mode": experiment_mode_status(),
                    "revision_control": _revision_control(
                        rid, base_revision, "NOT_COMMITTED_EXPERIMENT_PARSE_FAILED"
                    ),
                }
                parser_diagnostic = _experiment_parser_diagnostic(parser_error)
                if parser_diagnostic is not None:
                    parser_failure["parser_diagnostic"] = parser_diagnostic
                return parser_failure
        elif semantic_product:
            try:
                from packages.llm_gateway.gateway import load_user_config
                from packages.semantic_query import parse_semantic_route
                from packages.semantic_query.task_draft import EXECUTABLE_INTENTS

                semantic_profile_id = str(load_user_config().get("id") or "").strip() or None
                routed, answer_spec, llm_parser_trace = parse_semantic_route(
                    question,
                    ctx,
                    mode="product_semantic_constrained",
                    expected_profile_id=semantic_profile_id,
                    allowed_intents=EXECUTABLE_INTENTS,
                )
            except Exception as exc:
                parser_error = _public_error_text(
                    exc, fallback="semantic_query_parse_failed"
                )
                parser_failure = {
                    "clarify": True,
                    "question": {"intent": "semantic_query_parse_failed"},
                    "next_prompt": (
                        "智能语义识别未能形成可执行任务；本轮已停止且不会静默回退到规则识别。"
                        "请检查活动模型配置，或显式切换到规则对照模式。"
                    ),
                    "session_id": sid,
                    "business_status": "ERROR",
                    "error_code": "semantic_query_parse_failed",
                    "parser_error": parser_error,
                    "query_mode": product_query_mode,
                    "revision_control": _revision_control(
                        rid, base_revision, "NOT_COMMITTED_SEMANTIC_PARSE_FAILED"
                    ),
                }
                parser_diagnostic = _experiment_parser_diagnostic(parser_error)
                if parser_diagnostic is not None:
                    parser_failure["parser_diagnostic"] = parser_diagnostic
                return parser_failure
        else:
            routed = route_query(question, context=ctx)
            if (
                (
                    product_query_mode.get("rule_baseline_selected")
                    or active_experiment_mode == "g0_rule"
                )
                and routed.get("intent") != "clarify"
            ):
                from packages.semantic_query import bind_rule_output_scope

                # The explicit rule comparator still binds user-visible output
                # requirements to the contract.  Otherwise an absent legacy
                # field is interpreted as "charts by default", so a plain text
                # request can be mislabeled as a failed chart delivery.
                routed, answer_spec = bind_rule_output_scope(question, routed)
    if llm_experiment and routed.get("intent") == "national_ranking":
        routed = {
            "intent": "clarify",
            "reason": "legacy_boundary",
            "missing": ["全国省域排名尚未纳入本轮 G1/G2 共同实验的生产契约能力。"],
            "original": question,
        }
        from packages.semantic_query import build_answer_spec
        answer_spec = build_answer_spec(question, routed)
    if not (llm_experiment or semantic_product):
        from packages.semantic_query.public_tasks import route_public_task
        from packages.semantic_query import runtime as public_runtime
        from packages.semantic_query.regional_capabilities import route as route_capability
        extended = route_capability(question, {}, ctx, public_runtime) or route_public_task(question, {}, public_runtime)
        if extended is not None:
            routed = extended
            answer_spec = public_runtime.build_answer_spec(question, routed)
    from packages.core.research_data import enabled as research_enabled, bind_public_constraints, SelectionError
    if research_enabled():
        try:
            routed = bind_public_constraints(question, routed)
        except SelectionError as exc:
            saved_candidate = copy.deepcopy(routed.get("_selective_task_candidate"))
            routed = {"intent": "clarify", "reason": str(exc), "missing": [str(exc)], "original": question,
                      "_selective_task_candidate": saved_candidate}
        from packages.semantic_query import build_answer_spec
        answer_spec = build_answer_spec(question, routed)
        answer_spec["research_public_contract"] = routed.get("research_public_contract")
    if not routed.get("selective_clarification"):
        routed = selective.attach(question, routed, ctx)
    routed = selective.carry_confirmations(routed, selective_resolution)
    if routed.get("selective_clarification"):
        routed["selective_clarification"]["flow_key"] = active_experiment_mode if experiment_mode.get("enabled") else product_query_mode["active_mode"]
        from packages.semantic_query import build_answer_spec
        answer_spec = build_answer_spec(question, routed)
    intent = routed.get("intent")
    if llm_experiment:
        from packages.experiment_protocol.trace import stage_event
        stage_event("canonical_tool_request", routed=copy.deepcopy(routed), answer_spec=copy.deepcopy(answer_spec))

    if intent == 'unavailable':
        return _unavailable_response(routed, sid, rid, base_revision, llm_parser_trace)
    if intent == "clarify":
        pending = None
        if (not g1_unconstrained or routed.get("selective_clarification")) and _clarification_allows_pending(routed):
            try:
                if not contract_store_available and not g1_unconstrained:
                    raise RuntimeError(context_restore_error or "contract_store_unavailable")
                from packages.analysis_contract import ContractService
                contract_service = contract_service or ContractService()
                pending = contract_service.open_pending(
                    selective.pending_session(sid, experiment_mode), question, routed,
                    base_revision=0 if g1_unconstrained else request_base_revision, request_id=rid,
                    inherited_context=(ctx if pending_applied or _needs_persisted_context_restore(question) or routed.get("selective_clarification") else {}),
                )
            except StaleBaseRevision as exc:
                return _stale_revision_response(
                    session_id=sid, question=routed, request_id=rid,
                    provided_base_revision=base_revision, error=exc, contract_service=contract_service,
                )
            except Exception as exc:
                context_restore_error = context_restore_error or str(exc)
        response = {
            "clarify": True,
            "question": routed,
            "next_prompt": _public_clarification_prompt(routed, question),
            "session_id": sid,
            "business_status": "CLARIFY",
            "tool_calls": [],
            "fact_tool_calls": [],
            "fallback": False,
            "execution_guard": _clarification_execution_guard(
                routed,
                pending,
                model_parser_invoked=bool(llm_experiment or semantic_product),
            ),
            "contract_store": contract_store_public,
            "revision_control": _revision_control(
                rid, base_revision, "NOT_COMMITTED_CLARIFY"
            ),
        }
        if not llm_experiment:
            response["query_mode"] = product_query_mode
        if pending is not None:
            response["clarification"] = selective.public_spec(pending)
            response["pending_clarification"] = _pending_public(
                pending,
                restored=session_was_empty and active_pending is not None,
                applied=pending_applied,
            )
            response["contract_store"] = _public_active_contract_store(contract_service)
        if context_restore_error:
            response["context_restore_error"] = (
                contract_store_runtime.get("error_code")
                or "contract_context_restore_failed"
            )
        if context_restore["attempted"]:
            response["context_restore"] = context_restore
        if llm_experiment:
            response["answer_spec"] = answer_spec
            response["experiment_trace"] = {"parser": llm_parser_trace}
            response["experiment_mode"] = experiment_mode_status()
        elif semantic_product:
            response["answer_spec"] = answer_spec
            response["semantic_trace"] = {"parser": llm_parser_trace}
            response["query_mode"] = product_query_mode
        if g1_unconstrained:
            if _clarification_allows_pending(routed):
                successful_context = copy.deepcopy(session_store.get(experiment_session_key) or {})
                successful_context["_pending_clarification"] = {
                    "reason": routed.get("reason"), "missing": list(routed.get("missing") or []),
                    "context": copy.deepcopy(routed.get("pending_context") or {}),
                    "selective_pending_id": pending["pending_id"] if pending and routed.get("selective_clarification") else None,
                }
                session_store[experiment_session_key] = successful_context
            response["contract_mode"] = "experiment_unconstrained"
            response["contract_store"] = {
                "status": "not_applicable_experiment_g1",
                "paths_redacted": True,
            }
            response["revision_control"] = _revision_control(
                rid, base_revision, "NOT_APPLICABLE_EXPERIMENT_G1"
            )
        return response

    if answer_spec is not None:
        # Deterministic publication flags, after strict model-draft validation.
        # Persist these in the existing requested_outputs list for RESTORE.
        routed["requested_scope_outputs"] = (
            (["dimension_scores"] if answer_spec.get("include_dimensions") else [])
            + (["catalogue_question_count"] if "catalogue_question_count" in (answer_spec.get("requested_result_statistics") or []) else [])
        )
    try:
        if intent == "public_task":
            from packages.semantic_query.public_tasks import execute
            result = execute(routed, verify_execution=not g1_unconstrained and mechanisms.get("execution_verification", True))
            result['_id'] = str(uuid.uuid4())
            _MEMORY[result['_id']] = result
        elif g1_unconstrained:
            result = _run_unconstrained_experiment(routed)
        elif intent == "region_compare":
            from packages.analysis_contract import ContractService
            contract_service = contract_service or ContractService()
            contract = contract or contract_service.prepare_region_compare(
                sid,
                question,
                routed,
                base_revision=request_base_revision,
                request_id=rid,
            )
            with contract_service.consistent_read(contract) as read_snapshot:
                read_snapshot.set_phase("region_compare_execution")
                result = _run_region_compare(
                    contract.region_keyword,
                    str(contract.secondary_region_keyword),
                    year=contract.period,
                    target_stat=contract.statistic,
                    target_dimension=contract.dimension,
                    target_dimensions=[contract.dimension] if contract.dimension else [],
                    school_filter=contract.school_filter,
                    aggregation=contract.aggregation,
                    original_query=routed.get("original"),
                    connection=read_snapshot.connection,
                )
                read_snapshot.mark("region_compare_execution_completed")
                if llm_experiment:
                    from packages.experiment_protocol.trace import stage_event
                    stage_event("raw_tool_result_preverification", result=copy.deepcopy(result),
                                contract=contract.to_dict())
                result = contract_service.bind_and_verify(
                    contract,
                    result,
                    read_snapshot=read_snapshot,
                    request_id=rid,
                    answer_spec=answer_spec,
                    verify_execution=mechanisms.get("execution_verification", True),
                )
        elif intent == "school_compare":
            from packages.analysis_contract import ContractService
            contract_service = contract_service or ContractService()
            contract = contract or contract_service.prepare_school_compare(
                sid,
                question,
                routed,
                base_revision=request_base_revision,
                request_id=rid,
            )
            with contract_service.consistent_read(contract) as read_snapshot:
                read_snapshot.set_phase("school_compare_execution")
                result = _run_school_compare(
                    f"id:{contract.school_id}",
                    f"id:{contract.secondary_school_id}",
                    year=contract.period if not contract.secondary_period or contract.period == contract.secondary_period else None,
                    connection=read_snapshot.connection,
                    require_complete_template=True,
                    target_dimension=contract.dimension,
                )
                read_snapshot.mark("school_compare_execution_completed")
                if llm_experiment:
                    from packages.experiment_protocol.trace import stage_event
                    stage_event("raw_tool_result_preverification", result=copy.deepcopy(result),
                                contract=contract.to_dict())
                result = contract_service.bind_and_verify(
                    contract,
                    result,
                    read_snapshot=read_snapshot,
                    request_id=rid,
                    answer_spec=answer_spec,
                    verify_execution=mechanisms.get("execution_verification", True),
                )
        elif intent == "school_item_compare":
            result = _run_school_item_compare(
                routed["school_a"],
                routed["school_b"],
                school_fact_metric_input(routed),
                year=routed.get("year"),
            )
        elif intent == "multi_school_compare":
            result = _run_multi_school_compare(
                routed["schools"],
                year=routed.get("year"),
                derived=routed.get("derived"),
            )
        elif intent == "school_fact":
            from packages.analysis_contract import ContractService
            contract_service = contract_service or ContractService()
            contract = contract or contract_service.prepare_school_fact(
                sid,
                question,
                routed,
                base_revision=request_base_revision,
                request_id=rid,
            )
            with contract_service.consistent_read(contract) as read_snapshot:
                read_snapshot.set_phase("school_fact_execution")
                contract_service.controlled_test_gate("before_school_fact_execution", read_snapshot)
                result = _run_school_fact(
                    f"id:{contract.school_id}",
                    {**dict(contract.metric_spec or {}), **({"value_view": contract.value_view} if contract.value_view else {})},
                    year=contract.period,
                    connection=read_snapshot.connection,
                )
                read_snapshot.mark("school_fact_execution_completed")
                if llm_experiment:
                    from packages.experiment_protocol.trace import stage_event
                    stage_event("raw_tool_result_preverification", result=copy.deepcopy(result),
                                contract=contract.to_dict())
                result = contract_service.bind_and_verify(
                    contract,
                    result,
                    read_snapshot=read_snapshot,
                    request_id=rid,
                    answer_spec=answer_spec,
                    verify_execution=mechanisms.get("execution_verification", True),
                )
        elif intent == "school_ranking":
            result = _run_school_ranking(
                routed["school"],
                region_keyword=routed.get("region"),
                year=routed.get("year"),
            )
        elif intent == "region_longitudinal":
            from packages.analysis_contract import ContractService
            contract_service = contract_service or ContractService()
            contract = contract or contract_service.prepare_region_longitudinal(
                sid,
                question,
                routed,
                base_revision=request_base_revision,
                request_id=rid,
            )
            with contract_service.consistent_read(contract) as read_snapshot:
                read_snapshot.set_phase("region_longitudinal_execution")
                result = _run_region_longitudinal(
                    contract.region_keyword,
                    contract.period,
                    str(contract.secondary_period),
                    school_filter=contract.school_filter,
                    target_dimensions=[contract.dimension] if contract.dimension else [],
                    target_dimension=contract.dimension,
                    target_stat=contract.statistic,
                    aggregation=contract.aggregation,
                    original_query=routed.get("original"),
                    connection=read_snapshot.connection,
                )
                read_snapshot.mark("region_longitudinal_execution_completed", periods=2)
                if llm_experiment:
                    from packages.experiment_protocol.trace import stage_event
                    stage_event("raw_tool_result_preverification", result=copy.deepcopy(result),
                                contract=contract.to_dict())
                result = contract_service.bind_and_verify(
                    contract,
                    result,
                    read_snapshot=read_snapshot,
                    request_id=rid,
                    answer_spec=answer_spec,
                    verify_execution=mechanisms.get("execution_verification", True),
                )
        elif intent == "region_ranking":
            from packages.analysis_contract import ContractService
            contract_service = contract_service or ContractService()
            contract = contract or contract_service.prepare_region_ranking(
                sid,
                question,
                routed,
                base_revision=request_base_revision,
                request_id=rid,
            )
            with contract_service.consistent_read(contract) as read_snapshot:
                read_snapshot.set_phase("region_ranking_execution")
                result = _run_region_evaluation(
                    contract.region_keyword,
                    year=contract.period,
                    target_rank=contract.target_rank,
                    target_top_n=contract.target_top_n,
                    target_dimension=None,
                    target_dimensions=[],
                    target_stat="mean",
                    school_filter=contract.school_filter,
                    original_query=routed.get("original"),
                    aggregation="school_equal_mean",
                    operation="region_ranking",
                    indicator_ids=list(contract.indicator_ids),
                    connection=read_snapshot.connection,
                )
                read_snapshot.mark("region_ranking_execution_completed")
                if llm_experiment:
                    from packages.experiment_protocol.trace import stage_event
                    stage_event("raw_tool_result_preverification", result=copy.deepcopy(result),
                                contract=contract.to_dict())
                result = contract_service.bind_and_verify(
                    contract,
                    result,
                    read_snapshot=read_snapshot,
                    request_id=rid,
                    answer_spec=answer_spec,
                    verify_execution=mechanisms.get("execution_verification", True),
                )
        elif intent == "region_eval":
            from packages.analysis_contract import ContractService
            contract_service = contract_service or ContractService()
            contract = contract or contract_service.prepare_region(
                sid,
                question,
                routed,
                base_revision=request_base_revision,
                request_id=rid,
            )
            with contract_service.consistent_read(contract) as read_snapshot:
                read_snapshot.set_phase("region_execution")
                result = _run_region_evaluation(
                    contract.region_keyword,
                    year=contract.period,
                    target_rank=contract.target_rank,
                    target_top_n=contract.target_top_n,
                    target_dimension=contract.dimension,
                    target_dimensions=list(contract.target_dimensions) or ([contract.dimension] if contract.dimension else []),
                    target_stat=contract.statistic,
                    school_filter=contract.school_filter,
                    original_query=routed.get("original"),
                    aggregation=contract.aggregation,
                    indicator_ids=list(contract.indicator_ids),
                    value_view=contract.value_view,
                    region_identity=contract.region_identity,
                    connection=read_snapshot.connection,
                )
                if contract.metric_type == "question":
                    result.update({
                        "metric_type": "question",
                        "metric_key": contract.metric_key,
                        "metric_name": contract.metric_name,
                        "question_id": contract.indicator_ids[0],
                        "question_content": contract.metric_name,
                        "value_view": contract.value_view or "normalized_score",
                        "unit": (contract.metric_spec or {}).get("unit") or "分",
                        "normalized_score": result.get("total_score"),
                        "target_stat": contract.statistic,
                    })
                read_snapshot.mark("region_execution_completed")
                if llm_experiment:
                    from packages.experiment_protocol.trace import stage_event
                    stage_event("raw_tool_result_preverification", result=copy.deepcopy(result),
                                contract=contract.to_dict())
                result = contract_service.bind_and_verify(
                    contract,
                    result,
                    read_snapshot=read_snapshot,
                    request_id=rid,
                    answer_spec=answer_spec,
                    verify_execution=mechanisms.get("execution_verification", True),
                )
        elif intent == "school_eval":
            from packages.analysis_contract import ContractService
            contract_service = contract_service or ContractService()
            contract = contract or contract_service.prepare_school_evaluation(
                sid,
                question,
                routed,
                base_revision=request_base_revision,
                request_id=rid,
            )
            with contract_service.consistent_read(contract) as read_snapshot:
                read_snapshot.set_phase("school_evaluation_execution")
                result = _run_school_evaluation(
                    f"id:{contract.school_id}",
                    [contract.period],
                    target_dimension=contract.dimension,
                    target_dimensions=list(contract.target_dimensions) or ([contract.dimension] if contract.dimension else []),
                    indicator_ids=list(contract.indicator_ids),
                    connection=read_snapshot.connection,
                )
                read_snapshot.mark("school_evaluation_execution_completed")
                if llm_experiment:
                    from packages.experiment_protocol.trace import stage_event
                    stage_event("raw_tool_result_preverification", result=copy.deepcopy(result),
                                contract=contract.to_dict())
                result = contract_service.bind_and_verify(
                    contract,
                    result,
                    read_snapshot=read_snapshot,
                    request_id=rid,
                    answer_spec=answer_spec,
                    verify_execution=mechanisms.get("execution_verification", True),
                )
        elif intent == "national_ranking":
            result = _run_national_ranking(
                year=routed.get("year"),
                target_dimension=routed.get("target_dimension"),
                top_n=routed.get("top_n"),
                original_query=routed.get("original"),
                verified_overall_top=not experiment_mode.get("enabled"),
                province_only=bool(routed.get("province_only")),
            )
        else:
            # 默认：教师综合评估
            teacher_id = routed.get("teacher_id")
            if not teacher_id:
                # 兼容旧式 planner 解析兜底
                q = parse_question(question)
                if q.clarify_needed:
                    return {
                        "clarify": True,
                        "question": q.__dict__,
                        "next_prompt": f"请补充：{', '.join(q.missing)}",
                        "session_id": sid,
                    }
                teacher_id = q.objects[0]
                years = q.years or ["2023-2024", "2024-2025"]
            else:
                years = routed.get("years") or ["2023-2024", "2024-2025"]
            result = _run_evaluation(teacher_id, years)

    except StaleBaseRevision as exc:
        return _stale_revision_response(
            session_id=sid,
            question=routed,
            request_id=rid,
            provided_base_revision=base_revision,
            error=exc,
            contract_service=contract_service,
        )
    except ValueError as e:
        error_text = str(e)
        from packages.semantic_query.regional_capabilities import data_error_message
        unavailable_message = data_error_message(e)
        public_error_text = unavailable_message or _public_error_text(
            error_text, fallback="contract_execution_clarify"
        )
        is_no_data = bool(unavailable_message) or any(token in error_text for token in (
            "无监测数据", "无监测学校记录", "没有符合筛选条件", "尚未录入有效问卷作答",
            "无第",
        ))
        if contract is not None and contract_service is not None:
            contract_service.record_failure(contract, error_text, status="NO_DATA" if is_no_data else "CLARIFY")
            if is_no_data:
                session_store[experiment_session_key] = contract_service.session_context(sid)
        pending_state = contract_service.pending_transition_result if contract_service else None
        if contract_service is not None:
            try:
                if not is_no_data and active_pending is None:
                    pending_state = contract_service.open_pending(
                        sid,
                        question,
                        routed,
                        reason="execution_clarify",
                        missing=[public_error_text],
                        base_revision=contract.revision if contract is not None else request_base_revision,
                        request_id=rid,
                        inherited_context=ctx if pending_applied or _needs_persisted_context_restore(question) else {},
                    )
            except StaleBaseRevision as exc:
                if contract is None:
                    return _stale_revision_response(
                        session_id=sid, question=routed, request_id=rid,
                        provided_base_revision=base_revision, error=exc, contract_service=contract_service,
                    )
                context_restore_error = "pending_state_changed_after_execution"
            except Exception as exc:
                context_restore_error = context_restore_error or str(exc)
        response = {
            "clarify": True,
            "question": routed,
            "next_prompt": public_error_text,
            "session_id": sid,
            "contract": contract.to_dict() if contract is not None else None,
            "business_status": "NO_DATA" if is_no_data else "CLARIFY",
        }
        if pending_state is not None:
            response["pending_clarification"] = _pending_public(
                pending_state,
                restored=session_was_empty and active_pending is not None,
                applied=pending_applied,
            )
        if not contract_store_available:
            response["contract_store"] = contract_store_public
        if contract is not None and contract_service is not None:
            response["contract_mode"] = "enforced"
            response["contract_verification"] = {
                "status": "NOT_APPLICABLE" if is_no_data else "NOT_CHECKED",
                "checks": [],
                "failures": [public_error_text],
            }
            response["contract_store"] = _public_active_contract_store(contract_service)
            if is_no_data:
                response["session_context"] = contract_service.context_from(contract)
            response["revision_control"] = _revision_control(
                rid,
                base_revision,
                "COMMITTED" if is_no_data else "COMMITTED_AUDIT_ONLY",
                focus_eligible=is_no_data,
                audit_only=not is_no_data,
                accepted_base_revision=contract.parent_revision or 0,
                committed_revision=contract.revision,
            )
        else:
            response["revision_control"] = _revision_control(
                rid, base_revision, "NOT_COMMITTED_EXECUTION_CLARIFY"
            )
        if context_restore["attempted"]:
            response["context_restore"] = context_restore
        if g1_unconstrained:
            response["contract_mode"] = "experiment_unconstrained"
            response["contract_store"] = {"status": "not_applicable_experiment_g1", "paths_redacted": True}
            response["revision_control"] = _revision_control(
                rid, base_revision, "NOT_APPLICABLE_EXPERIMENT_G1"
            )
        if llm_experiment:
            response["answer_spec"] = answer_spec
            response["experiment_trace"] = {"parser": llm_parser_trace}
            response["experiment_mode"] = experiment_mode_status()
        elif semantic_product:
            response["answer_spec"] = answer_spec
            response["semantic_trace"] = {"parser": llm_parser_trace}
            response["query_mode"] = product_query_mode
        return response
    except RuntimeError as e:
        public_error_text = _public_error_text(
            e, fallback="contract_execution_or_verification_failed"
        )
        if (
            contract is not None
            and contract_service is not None
            and not str(e).startswith("contract_verification_failed:")
        ):
            contract_service.record_failure(
                contract,
                str(e),
                verification_status=(
                    "FAIL" if getattr(e, "test_control", None) is not None else None
                ),
            )
        response = {
            "clarify": True,
            "question": routed,
            "next_prompt": (
                "实验工具执行失败；本轮未建立契约修订，请检查任务范围或数据。"
                if g1_unconstrained
                else "结果未通过任务契约核验，已阻止呈现，请检查执行范围与统计口径。"
            ),
            "session_id": sid,
            "contract": contract.to_dict() if contract is not None else None,
            "business_status": "ERROR",
            "verification_error": public_error_text,
            "contract_store": contract_store_public,
        }
        if getattr(e, "test_control", None) is not None:
            response["contract_test_control"] = e.test_control
        if contract is not None and contract_service is not None:
            response["contract_mode"] = "enforced"
            response["contract_verification"] = {
                "status": "FAIL",
                "checks": [],
                "failures": [public_error_text],
            }
            response["contract_store"] = _public_active_contract_store(contract_service)
            response["revision_control"] = _committed_audit_revision_control(
                rid,
                base_revision,
                contract,
                failure_stage=(
                    "VERIFICATION_FAILED"
                    if str(e).startswith("contract_verification_failed:")
                    else "EXECUTION_FAILED"
                ),
            )
        else:
            response["revision_control"] = _revision_control(
                rid, base_revision, "NOT_COMMITTED_EXECUTION_ERROR"
            )
        if context_restore["attempted"]:
            response["context_restore"] = context_restore
        if g1_unconstrained:
            response["contract_mode"] = "experiment_unconstrained"
            response["contract_store"] = {"status": "not_applicable_experiment_g1", "paths_redacted": True}
            response["revision_control"] = _revision_control(
                rid, base_revision, "NOT_APPLICABLE_EXPERIMENT_G1"
            )
        if llm_experiment:
            response["answer_spec"] = answer_spec
            response["experiment_trace"] = {"parser": llm_parser_trace}
            response["experiment_mode"] = experiment_mode_status()
        elif semantic_product:
            response["answer_spec"] = answer_spec
            response["semantic_trace"] = {"parser": llm_parser_trace}
            response["query_mode"] = product_query_mode
        return response
    except Exception as e:
        public_error_text = _public_error_text(
            e, fallback="contract_execution_failed"
        )
        if contract is not None and contract_service is not None:
            contract_service.record_failure(contract, str(e))
        response = {
            "clarify": True,
            "question": routed,
            "next_prompt": (
                "实验工具执行失败；本轮未建立契约修订，请稍后重试或检查服务日志。"
                if g1_unconstrained
                else "契约执行失败，已保留最后有效任务状态，请稍后重试或检查服务日志。"
            ),
            "session_id": sid,
            "contract": contract.to_dict() if contract is not None else None,
            "business_status": "ERROR",
            "execution_error": public_error_text,
            "contract_store": contract_store_public,
            "revision_control": (
                _committed_audit_revision_control(
                    rid,
                    base_revision,
                    contract,
                    failure_stage="EXECUTION_FAILED",
                )
                if contract is not None
                else _revision_control(
                    rid, base_revision, "NOT_COMMITTED_EXECUTION_ERROR"
                )
            ),
        }
        if context_restore["attempted"]:
            response["context_restore"] = context_restore
        if g1_unconstrained:
            response["contract_mode"] = "experiment_unconstrained"
            response["contract_store"] = {"status": "not_applicable_experiment_g1", "paths_redacted": True}
            response["revision_control"] = _revision_control(
                rid, base_revision, "NOT_APPLICABLE_EXPERIMENT_G1"
            )
        if llm_experiment:
            response["answer_spec"] = answer_spec
            response["experiment_trace"] = {"parser": llm_parser_trace}
            response["experiment_mode"] = experiment_mode_status()
        elif semantic_product:
            response["answer_spec"] = answer_spec
            response["semantic_trace"] = {"parser": llm_parser_trace}
            response["query_mode"] = product_query_mode
        return response

    execution_result = result
    if llm_experiment:
        from packages.experiment_protocol.trace import stage_event
        stage_event("raw_tool_result", result=copy.deepcopy(execution_result),
                    contract=contract.to_dict() if contract is not None else None)
    public_result = result
    if semantic_product:
        from packages.semantic_query import build_answer_spec, project_result_scope

        answer_spec = answer_spec or build_answer_spec(question, routed)
    if answer_spec is not None:
        from packages.semantic_query import project_result_scope

        # Contract verification above used the full execution result.  From
        # this point onward, expose only the AnswerSpec-bound public view to the
        # report, chart builder, and page response. The G1 writer also receives
        # an AnswerSpec-bound evidence projection while retaining its
        # unconstrained verification and experiment identity.
        try:
            public_result = project_result_scope(execution_result, answer_spec, question)
        except ValueError as exc:
            # Enforced computation prechecks the same pure projection before
            # committing focus/Pending. Keep a structured receipt here too,
            # including contract-free G1, instead of leaking a raw HTTP error.
            error_code = str(exc) if str(exc).startswith("public_projection_") else "public_projection_failed"
            a1_unverified = active_experiment_mode == "a1_no_execution_verification"
            if contract is not None and contract_service is not None:
                contract_service.record_failure(
                    contract, error_code,
                    verification_status="NOT_CHECKED" if a1_unverified else "FAIL",
                )
            return {
                "session_id": sid, "business_status": "ERROR", "error_code": error_code,
                "next_prompt": "回答范围核验失败，本轮结果已阻止呈现。",
                "contract_mode": "experiment_unconstrained" if g1_unconstrained else "experiment_execution_unverified" if a1_unverified else "enforced",
                "contract": contract.to_dict() if contract is not None else None,
                "contract_verification": (
                    {"status": "NOT_CHECKED_ABLATION_A1", "checks": [
                        {"name": "task_binding", "status": "NOT_CHECKED"},
                        {"name": "independent_replay", "status": "NOT_CHECKED"},
                    ], "failures": []}
                    if a1_unverified else {"status": "FAIL", "checks": [], "failures": [error_code]}
                ),
                "public_projection_verification": {"status": "FAIL", "failures": [error_code]},
                "revision_control": _committed_audit_revision_control(
                    rid, base_revision, contract, failure_stage="PUBLIC_PROJECTION_FAILED"
                ) if contract is not None else _revision_control(
                    rid, base_revision,
                    "NOT_APPLICABLE_EXPERIMENT_G1" if g1_unconstrained else "NOT_COMMITTED_EXECUTION_ERROR",
                ),
            }
        if not g1_unconstrained:
            result = public_result

    # 强制契约路径仅从可信契约投影会话焦点；兼容路径暂保留旧上下文适配。
    if contract is not None and contract_service is not None:
        ctx = contract_service.session_context(sid)
    elif intent == "public_task":
        _update_public_composition_context(ctx, routed, execution_result)
        if ctx.get("public_context_source") == "successful_public_composition":
            ctx["public_context_base_revision"] = request_base_revision
    elif g1_unconstrained:
        _update_unconstrained_experiment_context(ctx, routed, execution_result)
    else:
        # 区域宏观结果中的榜首学校不是用户焦点，不能写入 last_school。
        if result.get("object_type") in ("region", "region_longitudinal"):
            pass
        elif result.get("school_name_a"):
            ctx["last_school"] = result["school_name_a"]
        elif result.get("school_name"):
            ctx["last_school"] = result["school_name"]

        if result.get("region_name_a"):
            ctx["last_region"] = result["region_name_a"]
        elif result.get("short_name") or result.get("region_name"):
            ctx["last_region"] = result.get("short_name") or result.get("region_name")
        if result.get("target_dimension"):
            ctx["last_dimension"] = result["target_dimension"]
        if result.get("year_b"):
            ctx["last_year"] = result["year_b"]
        elif result.get("year"):
            ctx["last_year"] = result["year"]
        elif result.get("school_years"):
            ctx["last_year"] = result["school_years"][0]
        if result.get("teacher_id"):
            ctx["last_teacher"] = result["teacher_id"]
    session_store[experiment_session_key] = copy.deepcopy(ctx)

    out = _finish(
        public_result if g1_unconstrained else result,
        question=question,
        routed_info=routed,
        requested_outputs=(
            list(routed.get("requested_outputs") or [])
            if (
                llm_experiment
                or semantic_product
                or product_query_mode.get("rule_baseline_selected")
                or active_experiment_mode == "g0_rule"
            )
            else None
        ),
        proposed_constraints=mechanisms.get("output_verification", not g1_unconstrained),
        verification_variant=active_experiment_mode,
        # G1 remains contract-free and its writer still receives the full
        # execution result, but the public page must not leak blocks outside
        # the shared AnswerSpec (for example tiers/Top 5 on an item request).
        answer_spec=answer_spec,
    )
    out["business_status"] = (
        str(result.get("fact_data_status") or "ANSWER")
        if result.get("fact_data_status") is not None
        else "ANSWER"
    )
    if context_restore["attempted"]:
        out["context_restore"] = context_restore
    if contract is not None:
        out["contract_mode"] = result.get("contract_mode") or "enforced"
        out["contract"] = result.get("contract")
        out["contract_store"] = result.get("contract_store")
        out["execution_receipt"] = result.get("execution_receipt")
        out["contract_verification"] = result.get("contract_verification")
        _apply_presentation_coverage(out)
        out["session_context"] = contract_service.context_from(contract)
        out["revision_control"] = _revision_control(
            rid,
            base_revision,
            "COMMITTED_AUDIT_ONLY" if active_experiment_mode == "a1_no_execution_verification" else "COMMITTED",
            focus_eligible=active_experiment_mode != "a1_no_execution_verification",
            audit_only=active_experiment_mode == "a1_no_execution_verification",
            accepted_base_revision=contract.parent_revision or 0,
            committed_revision=contract.revision,
        )
        if revision_restore is not None:
            out["revision_restore"] = revision_restore
            out["revision_history"] = contract_service.store.revision_history(sid, contract.task_id)
        binding = {
            "task_id": contract.task_id,
            "contract_revision": contract.revision,
            "result_id": result.get("_id"),
        }
        if out.get("report") is not None:
            out["report"]["contract_binding"] = binding
        for chart in out.get("charts") or []:
            chart["contract_binding"] = binding
    elif g1_unconstrained:
        out["contract_mode"] = "experiment_unconstrained"
        out["contract_store"] = {
            "status": "not_applicable_experiment_g1",
            "paths_redacted": True,
        }
        out["revision_control"] = _revision_control(
            rid, base_revision, "NOT_APPLICABLE_EXPERIMENT_G1"
        )
    elif intent == "public_task":
        out["contract_mode"] = "public_composition_verified_no_revision" if result.get("public_execution_verification",{}).get("status") == "PASS" else "public_composition_unverified"
        out["contract_verification"] = execution_result.get("public_execution_verification")
        out["revision_control"] = _revision_control(rid, base_revision, "NOT_APPLICABLE_PUBLIC_COMPOSITION")
    else:
        out["contract_mode"] = "legacy_unverified"
        out["revision_control"] = _revision_control(
            rid, base_revision, "NOT_APPLICABLE_LEGACY"
        )
    out["session_id"] = sid
    if intent == "public_task" and ctx.get("public_context_source"):
        out["session_context"] = copy.deepcopy(ctx)
    if not llm_experiment:
        # Expose the selected ordinary-entry mode on every completed response,
        # including the explicit rule comparison path.  The UI and evidence
        # collectors must not infer mode from a prose label or fallback flag.
        out["query_mode"] = product_query_mode
    if answer_spec is not None and (
        product_query_mode.get("rule_baseline_selected")
        or active_experiment_mode == "g0_rule"
    ):
        # Make the deterministic scope inspectable in rule-comparator evidence;
        # capability metadata alone is not proof that projection was applied.
        out["answer_spec"] = answer_spec
    if contract_service is not None and contract_service.pending_transition_result is not None:
        out["pending_clarification"] = _pending_public(
            contract_service.pending_transition_result,
            restored=session_was_empty,
            applied=pending_applied,
        )
    if experiment_mode.get("active_mode") == "g0_rule":
        from packages.agent.expert_advisor import generate_expert_advice
        from packages.experiment_protocol import record_rule_output

        if answer_spec is not None and answer_spec.get("answer_mode") == "direct":
            from packages.semantic_query.runtime import (
                common_evidence_packet,
                direct_answer_with_filter_scope,
                verify_answer_text,
            )

            evidence_packet = common_evidence_packet(result)
            direct_text = direct_answer_with_filter_scope(result.get("direct_answer"), evidence_packet, answer_spec)
            verification = verify_answer_text(
                direct_text, question, evidence_packet, answer_spec
            )
            out["llm"] = {
                "text": direct_text if verification.get("status") == "PASS" else "",
                "is_fallback": False,
                "provider": "deterministic_rule_answer_scope",
                "model": "none",
                "model_invoked": False,
                "experiment_mode": "g0_rule",
                "delivery_status": (
                    "ANSWER" if verification.get("status") == "PASS" else "BLOCKED"
                ),
                "answer_verification": verification,
            }
            if verification.get("status") != "PASS":
                out["overall_delivery_status"] = "DEGRADED"
        else:
            out["llm"] = {
                "text": generate_expert_advice(result, question),
                "is_fallback": False,
                "provider": "local_expert_engine",
                "model": "rule-based-advisor",
                "model_invoked": False,
                "experiment_mode": "g0_rule",
            }
        record_rule_output("chat_result")
    elif llm_experiment:
        try:
            from packages.experiment_protocol.llm_common import (
                build_answer_spec,
                generate_llm_answer,
            )

            answer_spec = answer_spec or build_answer_spec(question, routed)
            llm_response, llm_writer_trace = generate_llm_answer(
                question,
                execution_result if g1_unconstrained else result,
                answer_spec,
                experiment_mode,
            )
            out["llm"] = llm_response
            out["answer_spec"] = answer_spec
            out["experiment_trace"] = {
                "parser": llm_parser_trace,
                "writer": llm_writer_trace,
                "constraints_enabled": bool(
                    experiment_mode.get("proposed_constraints_enabled")
                ),
            }
            if llm_response.get("delivery_status") == "BLOCKED":
                out["overall_delivery_status"] = "DEGRADED"
        except Exception as exc:
            out["llm"] = {
                "text": "",
                "error": _public_error_text(
                    exc, fallback="experiment_llm_delivery_failed"
                ),
                "model_invoked": True,
                "is_fallback": False,
                "experiment_mode": active_experiment_mode,
                "delivery_status": "ERROR",
            }
            out["answer_spec"] = answer_spec
            out["experiment_trace"] = {
                "parser": llm_parser_trace,
                "constraints_enabled": bool(
                    experiment_mode.get("proposed_constraints_enabled")
                ),
                "writer_error": _public_error_text(
                    exc, fallback="experiment_llm_delivery_failed"
                ),
            }
            out["overall_delivery_status"] = "DEGRADED"
    elif semantic_product:
        try:
            from packages.semantic_query import (
                build_answer_spec,
                generate_semantic_answer,
            )
            from packages.semantic_query.runtime import (
                COMMON_PATH_VERSION,
                common_evidence_packet,
                direct_answer_with_filter_scope,
                verify_answer_text,
            )

            answer_spec = answer_spec or build_answer_spec(question, routed)
            if answer_spec.get("answer_mode") == "direct":
                evidence_packet = common_evidence_packet(result)
                direct_text = direct_answer_with_filter_scope(result.get("direct_answer"), evidence_packet, answer_spec)
                verification = verify_answer_text(
                    direct_text, question, evidence_packet, answer_spec
                )
                llm_response = {
                    "provider": "deterministic_answer_scope",
                    "model": "none",
                    "text": direct_text if verification.get("status") == "PASS" else "",
                    "model_invoked": False,
                    "is_fallback": False,
                    "query_mode": "product_semantic_constrained",
                    "delivery_status": (
                        "ANSWER" if verification.get("status") == "PASS" else "BLOCKED"
                    ),
                    "answer_verification": verification,
                    "repaired": False,
                }
                llm_writer_trace = {
                    "common_path_version": COMMON_PATH_VERSION,
                    "prompt_version": "deterministic-direct-answer-v1",
                    "answer_spec": answer_spec,
                    "evidence_packet": evidence_packet,
                    "deterministic": True,
                    "verification": verification,
                }
            else:
                llm_response, llm_writer_trace = generate_semantic_answer(
                    question,
                    result,
                    answer_spec,
                    mode="product_semantic_constrained",
                    expected_profile_id=semantic_profile_id,
                )
            out["llm"] = llm_response
            out["answer_spec"] = answer_spec
            out["semantic_trace"] = {
                "parser": llm_parser_trace,
                "writer": llm_writer_trace,
                "constraints_enabled": True,
            }
            if llm_response.get("delivery_status") == "BLOCKED":
                out["overall_delivery_status"] = "DEGRADED"
        except Exception as exc:
            out["llm"] = {
                "text": "",
                "error": _public_error_text(
                    exc, fallback="semantic_answer_delivery_failed"
                ),
                "model_invoked": True,
                "is_fallback": False,
                "query_mode": "product_semantic_constrained",
                "delivery_status": "ERROR",
            }
            out["answer_spec"] = answer_spec
            out["semantic_trace"] = {
                "parser": llm_parser_trace,
                "constraints_enabled": True,
                "writer_error": _public_error_text(
                    exc, fallback="semantic_answer_delivery_failed"
                ),
            }
            out["overall_delivery_status"] = "DEGRADED"
    elif product_query_mode.get("rule_baseline_selected") and answer_spec is not None:
        from packages.semantic_query.runtime import (
            common_evidence_packet,
            direct_answer_with_filter_scope,
            verify_answer_text,
        )

        evidence_packet = common_evidence_packet(result)
        direct_text = direct_answer_with_filter_scope(result.get("direct_answer"), evidence_packet, answer_spec)
        verification = verify_answer_text(
            direct_text, question, evidence_packet, answer_spec
        )
        out["llm"] = {
            "provider": "deterministic_rule_answer_scope",
            "model": "none",
            "text": direct_text if verification.get("status") == "PASS" else "",
            "model_invoked": False,
            "is_fallback": False,
            "query_mode": "product_rule_comparator",
            "delivery_status": (
                "ANSWER" if verification.get("status") == "PASS" else "BLOCKED"
            ),
            "answer_verification": verification,
        }
        if verification.get("status") != "PASS":
            out["overall_delivery_status"] = "DEGRADED"
    else:
        try:
            from packages.llm_gateway.gateway import interpret_report, public_status
            if public_status().get("configured"):
                llm_res = interpret_report(out.get("markdown") or "", question, result=result)
                if not llm_res.get("text") or not str(llm_res.get("text")).strip():
                    from packages.agent.expert_advisor import generate_expert_advice
                    fallback_text = generate_expert_advice(result, question)
                    out["llm"] = {
                        "provider": llm_res.get("provider") or "local_expert_engine",
                        "model": llm_res.get("model") or "rule-based-advisor",
                        "text": fallback_text,
                        "is_fallback": True,
                        "fallback_reason": _public_error_text(
                            llm_res.get("fallback_reason"),
                            fallback=(
                                "上游模型返回正文为空 "
                                f"(finish_reason={llm_res.get('finish_reason') or 'empty'})，"
                                "已自动回退至本地规则专家研判"
                            ),
                        ),
                        "finish_reason": llm_res.get("finish_reason"),
                        "usage": llm_res.get("usage"),
                        "attempts": llm_res.get("attempts"),
                    }
                else:
                    out["llm"] = llm_res
        except Exception as e:
            try:
                from packages.agent.expert_advisor import generate_expert_advice
                fallback_text = generate_expert_advice(result, question)
                out["llm"] = {
                    "text": fallback_text,
                    "is_fallback": True,
                    "fallback_reason": _public_error_text(
                        e, fallback="llm_interpretation_failed"
                    ),
                    "provider": "local_expert_engine",
                    "model": "rule-based-advisor"
                }
            except Exception:
                out["llm"] = {
                    "error": _public_error_text(e, fallback="llm_interpretation_failed")
                }

    # 专职质检审计 Agent 执行全链路核验与四大系统性能量化指标核算
    try:
        if not mechanisms.get("output_verification", not g1_unconstrained):
            raise LookupError("experiment_skips_proposed_final_audit")
        if active_experiment_mode == "a1_no_execution_verification":
            from packages.agent.audit_agent import audit_output_only
            audit_out = audit_output_only(
                result, out.get("report") or {}, out.get("charts") or [], out.get("llm"),
            )
        else:
            from packages.agent.audit_agent import audit_query_result
            audit_out = audit_query_result(
                question=question, result=result, report=out.get("report") or {},
                charts=out.get("charts") or [], llm_output=out.get("llm"),
                routed_info=routed,
                writer_context=(out.get("experiment_trace") or {}).get("writer"),
            )
        out["audit"] = audit_out.get("audit")
        out["system_metrics"] = audit_out.get("system_metrics")
        if out.get("report") is not None:
            out["report"]["audit"] = audit_out.get("audit")
            out["report"]["system_metrics"] = audit_out.get("system_metrics")
    except LookupError:
        out["audit"] = {
            "status": "NOT_CHECKED" if active_experiment_mode == "a2_no_output_verification_or_repair" else "NOT_APPLICABLE_UNCONSTRAINED",
            "scope": "ablation_a2_output_unchecked" if active_experiment_mode == "a2_no_output_verification_or_repair" else "experiment_g1_without_proposed_output_audit",
        }
        out["system_metrics"] = None
    except Exception as e:
        out["audit_error"] = _public_error_text(e, fallback="result_audit_failed")

    if llm_experiment and os.environ.get("TEACHER_EVAL_EXPERIMENT_TRACE_DIR"):
        from packages.experiment_protocol.trace import request_events, save_request_trace

        private_trace = {
            "request_id": rid,
            "session_id": sid,
            "question": question,
            "variant": active_experiment_mode,
            "elapsed_ms": round((perf_counter() - request_started) * 1000, 2),
            "mechanisms": mechanisms,
            "parser": llm_parser_trace,
            "writer": out.get("experiment_trace", {}).get("writer"),
            "contract": result.get("contract"),
            "execution_receipt": result.get("execution_receipt"),
            "execution_verification": result.get("contract_verification"),
            "presentation_verification": out.get("presentation_verification"),
            "stage_events": request_events(),
            "public_page": {key: value for key, value in out.items() if key != "experiment_trace"},
        }
        trace_name = save_request_trace(
            os.environ.get("TEACHER_EVAL_EXPERIMENT_RUN_ID", ""), rid, private_trace,
        )
        out["experiment_trace_file"] = trace_name
    if isinstance(out.get("experiment_trace", {}).get("writer"), dict):
        # Failed drafts and feedback belong to the isolated evidence file.
        public_writer = dict(out["experiment_trace"]["writer"])
        for secret in ("first_answer", "repair_prompt", "repair_answer", "final_candidate"):
            public_writer.pop(secret, None)
        out["experiment_trace"]["writer"] = public_writer
    out["experiment_mode"] = experiment_mode_status()
    out["query_mode"] = product_query_mode
    return out


def batch_evaluate(teacher_ids: List[str], years: List[str]) -> dict:
    """批量评估（同步）。返回 {results: [{teacher_id, result_id, total_score, error?}]}。"""
    out = []
    for tid in teacher_ids:
        try:
            r = _run_evaluation(tid, years)
            out.append({"teacher_id": tid, "result_id": r["_id"],
                        "total_score": r.get("total_score")})
        except ValueError as e:
            out.append({"teacher_id": tid, "error": str(e)})
    return {"results": out}


def get_result(result_id: str) -> dict:
    if result_id not in _MEMORY:
        raise KeyError(f"result_id {result_id} 不存在")
    return _MEMORY[result_id]


def teacher_reports(teacher_id: str) -> dict:
    """该教师历次结果（原型：内存中该教师的结果按时间逆序）。"""
    rows = [v for v in _MEMORY.values() if v.get("teacher_id") == teacher_id]
    rows.sort(key=lambda v: v.get("school_years", []), reverse=True)
    return {"teacher_id": teacher_id, "reports": rows}


def teacher_trends(teacher_id: str, years: List[str]) -> dict:
    """多周期趋势（雷达数据，供 M10 用）。原型：近年结果聚合。"""
    rows = [v for v in _MEMORY.values() if v.get("teacher_id") == teacher_id]
    # 按学年维度聚合平均得分（原型简化为取最近一条；多条时做平均）
    per_year = {}
    for v in rows:
        for y in v.get("school_years", []):
            per_year.setdefault(y, []).append(v.get("total_score", 0))
    trend = {y: round(sum(s) / len(s), 2) for y, s in per_year.items() if s}
    return {"teacher_id": teacher_id, "trend": trend,
            "radar_last": _radar(rows[-1] if rows else None)}


def _radar(result) -> dict:
    if not result:
        return {}
    dims = {}
    for d in result.get("dimensions", []):
        if not d.get("is_redline") and d.get("score") is not None:
            dims[d["dimension"]] = d["score"]
    return dims


def templates() -> dict:
    tpl = _load_tpl()
    return {"templates": [{"name": (tpl.get("template") or {}).get("name"),
                           "school_year": (tpl.get("template") or {}).get("school_year")}]}


def _read_csv(name: str):
    p = os.path.join(ROOT, "data", "demo", name)
    if not os.path.isfile(p):
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


TEACHER_NAME_MAP = {
    "T001": "张老师",
    "T002": "李老师",
    "T003": "王老师",
    "T004": "赵老师",
}

TEACHER_TITLE_MAP = {
    "高级": "高级教师 (正高级/高级职称)",
    "中级": "一级教师 (中级职称)",
    "初级": "二级/初级教师",
}


def list_teachers() -> dict:
    teachers = []
    for r in _read_csv("teacher.csv"):
        t = dict(r)
        tid = t.get("teacher_id", "")
        t["name"] = TEACHER_NAME_MAP.get(tid, "示例教师")
        t["title_desc"] = TEACHER_TITLE_MAP.get(t.get("title", ""), t.get("title", ""))
        teachers.append(t)
    return {"teachers": teachers}


def teacher_profile(teacher_id: str) -> dict:
    """演示库教师简介：档案 + 各源数据条数（便于查询，不含评分）。"""
    row = next((r for r in _read_csv("teacher.csv") if r.get("teacher_id") == teacher_id), None)
    if not row:
        raise KeyError(f"教师 {teacher_id} 不存在")

    def _count(name):
        return [r for r in _read_csv(name) if r.get("teacher_id") == teacher_id]

    courses = _count("course_period.csv")
    years = sorted({r.get("school_year") for r in courses if r.get("school_year")})
    title = row.get("title", "")
    return {
        "object_type": "teacher",
        "teacher_id": teacher_id,
        "name": TEACHER_NAME_MAP.get(teacher_id, "示例教师"),
        "name_hash": row.get("name_hash"),
        "dept_id": row.get("dept_id"),
        "title": title,
        "title_desc": TEACHER_TITLE_MAP.get(title, title),
        "hire_year": row.get("hire_year"),
        "is_active": row.get("is_active"),
        "years": years,
        "coverage": {
            "course_period": len(courses),
            "lesson_plan": len(_count("lesson_plan.csv")),
            "class_observation": len(_count("class_observation.csv")),
            "exam_score": len(_count("exam_score.csv")),
            "research_record": len(_count("research_record.csv")),
            "student_survey": len(_count("student_survey.csv")),
            "complaint_record": len(_count("complaint_record.csv")),
            "discipline_record": len(_count("discipline_record.csv")),
        },
        "note": "演示数据为脱敏虚构档案；真实学校数字化数据请走学校简介。",
    }


def _llm_public() -> dict:
    try:
        from packages.llm_gateway.gateway import public_status
        return public_status()
    except Exception as e:
        return {"configured": False, "error": str(e)}


def school_profile(school_id: int) -> dict:
    from scripts.school_engine import connect, get_school
    school = get_school(school_id)
    if not school:
        raise KeyError(f"学校 {school_id} 不存在")
    conn = connect()
    n_ans = conn.execute(
        "SELECT COUNT(*) AS n FROM answers WHERE school_id=?", (int(school_id),)
    ).fetchone()["n"]
    n_q = conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
    years = [r["year"] for r in conn.execute(
        "SELECT year FROM schools WHERE school_name=? ORDER BY year",
        (school.get("school_name"),)).fetchall()]
    conn.close()
    return {
        "object_type": "school",
        **school,
        "answered": n_ans,
        "total_questions": n_q,
        "same_name_years": years,
        "note": "真实数字化问卷档案；评分请走学校评估报告。",
    }


# ---------------------------------------------------------------- FastAPI 层
try:
    from fastapi import FastAPI, HTTPException, Query  # noqa: F401
    from pydantic import BaseModel, Field, StrictBool  # noqa: F401

    class ChatIn(BaseModel):
        question: str = Field(..., min_length=2, description="宽泛评估问题")
        session_id: Optional[str] = Field(default="default", description="多轮上下文会话ID")
        base_revision: Optional[int] = Field(default=None, ge=0, description="客户端已知的会话头部修订")
        request_id: Optional[str] = Field(default=None, min_length=1, max_length=100, description="客户端请求追踪ID")
        query_mode: Optional[str] = Field(default=None, description="普通产品识别模式：semantic或rule")
        fh_arm: Optional[str] = Field(default=None, description="原生结构化任务表示：F或H；每次任务一次解析，无writer/repair")
        clarification_reply: Optional[dict] = Field(default=None, description="当前澄清的pending_id及choose/amend/none/cancel；选项只传option_id")

    class BatchIn(BaseModel):
        teacher_ids: List[str]
        years: List[str] = Field(default_factory=lambda: ["2023-2024", "2024-2025"])

    class TrendIn(BaseModel):
        years: List[str] = Field(default_factory=lambda: ["2023-2024", "2024-2025"])

    class LlmConfigIn(BaseModel):
        provider: str = Field(default="custom", description="deepseek/qwen/kimi/openai/custom")
        api_base: str = Field(default="", description="官方或中转站 OpenAI 兼容地址")
        api_key: str = Field(default="", description="API Key；留空表示不改已有密钥")
        model: str = Field(default="", description="模型名，如 deepseek-chat")
        name: str = Field(default="", description="配置名称，保存整套档案用")
        id: str = Field(default="", description="已有档案 id；仅 overwrite=true 时用来覆盖选中项")
        overwrite: bool = False
        clear_key: bool = False

    class LlmProfileIn(BaseModel):
        id: str

    class LlmPingIn(BaseModel):
        profile_id: str = Field(default="", max_length=128)
        official_only: StrictBool = False

    class LlmDiscoverIn(BaseModel):
        api_base: str = ""
        api_key: str = ""
        profile_id: str = Field(default="", description="页面读取到的活动配置 id；空密钥调用用于并发绑定")
        save: bool = False
        provider: str = "custom"

    app = FastAPI(title="教师评估 AI 平台 API", version="0.14.0-constraint-v6-20261011",
                  description="只读评估服务；结果缓存驻留内存，契约修订独立持久化")
    from packages.core.local_browser_security import LocalBrowserSecurityMiddleware
    app.add_middleware(LocalBrowserSecurityMiddleware)

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @app.exception_handler(RequestValidationError)
    async def _safe_input_error(request, exc):
        # Pydantic's ordinary response can echo a submitted credential value.
        return JSONResponse({"error": "REQUEST_INPUT_INVALID"}, status_code=422)

    from packages.core.secure_credentials import SecureCredentialError

    @app.exception_handler(SecureCredentialError)
    async def _safe_credential_error(request, exc):
        return JSONResponse({"error": "SECURE_STORAGE_UNAVAILABLE", "needs_personal_resave": True},
                            status_code=400)

    # 权限：接 M11 authz（原型鉴权仍宽松——默认 admin；真实鉴权由部署方对接 SSO 后在
    # get_current_user 中注入用户对象，并用 can_view/can_view_redline 做数据范围闸门）。
    def get_current_user():
        user = {"user_id": "prototype", "role": "admin", "scope": None,
                "note": "M11 后请由部署方注入真实用户对象"}
        return user

    def _require_view(user, teacher_id):
        from packages.core.authz import can_view
        if not can_view(user.get("role", "admin"), teacher_id, user.get("scope")):
            raise HTTPException(status_code=403, detail="无权查看该教师数据")

    @app.get("/health")
    def health():
        from scripts.school_engine import _db_path, data_source_status
        real_db = _db_path()
        data_source = data_source_status()
        db_stats = {}
        if os.path.isfile(real_db):
            try:
                from scripts.school_engine import connect
                conn = connect()
                s_count = conn.execute("SELECT count(*) FROM schools").fetchone()[0]
                q_count = conn.execute("SELECT count(*) FROM questions").fetchone()[0]
                a_count = conn.execute("SELECT count(*) FROM answers").fetchone()[0]
                conn.close()
                db_stats = {
                    "schools_count": s_count,
                    "questions_count": q_count,
                    "answers_count": a_count,
                    "ready": True
                }
            except Exception as e:
                db_stats = {
                    "error_code": "data_source_probe_failed",
                    "error_type": type(e).__name__,
                    "ready": False,
                }
        try:
            from packages.analysis_contract.capabilities import public_manifest
            from packages.analysis_contract.service import (
                consistent_read_test_control_status,
                n3_test_control_status,
                region_longitudinal_test_control_status,
                region_ranking_test_control_status,
                school_compare_test_control_status,
                school_fact_test_control_status,
            )
            contract_capabilities = public_manifest()
            contract_test_controls = {
                "school_fact_test_control": school_fact_test_control_status(),
                "school_compare_test_control": school_compare_test_control_status(),
                "region_longitudinal_test_control": region_longitudinal_test_control_status(),
                "region_ranking_test_control": region_ranking_test_control_status(),
                "n3_test_control": n3_test_control_status(),
                "consistent_read_test_control": consistent_read_test_control_status(),
            }
        except Exception as e:
            contract_capabilities = {
                "status": "error",
                "error_code": "contract_capability_probe_failed",
                "error_type": type(e).__name__,
            }
            contract_test_controls = {}
        try:
            contract_store = initialize_contract_store_status()
        except Exception as e:
            contract_store = {
                "status": "unavailable",
                "error_code": "contract_store_unavailable",
                "error": str(e),
                "fallback_used": False,
            }
        from packages.experiment_protocol import experiment_mode_status
        from packages.semantic_query import load_semantic_catalogue, resolve_query_mode

        try:
            semantic_catalogue = load_semantic_catalogue()
            semantic_status = {
                "status": "available",
                "version": semantic_catalogue.get("version"),
                "item_count": semantic_catalogue.get("item_count"),
            }
        except Exception as exc:
            semantic_status = {
                "status": "unavailable",
                "error_code": _public_error_text(
                    exc, fallback="semantic_catalogue_unavailable"
                ),
            }

        return {
            "status": "ok",
            "service_version": "0.14.0-constraint-v6-20261011",
            "native_fh": {"enabled": True, "arms": ["F", "H"], "version": "native-fh-task-query-v1.0-20261006", "parser_calls_per_task": 1, "repair_calls": 0, "writer_calls": 0},
            "teachers": _load_teachers(),
            "real_db": os.path.isfile(real_db),
            "data_source": _public_data_source_status(data_source),
            "db_stats": db_stats,
            "llm": _llm_public(),
            "query_mode": resolve_query_mode(),
            "semantic_catalogue": semantic_status,
            "experiment_mode": experiment_mode_status(),
            "analysis_contract": contract_capabilities,
            "contract_store": _public_contract_store_status(contract_store),
            "presentation_test_control": {
                "enabled": os.getenv("TEACHER_EVAL_PRESENTATION_TEST_MODE", "").strip() == "1",
                "gate": "environment_and_url",
                "url_parameter": "presentation_test=1",
                "synthetic_responses_are_product_evidence": False,
            },
            **contract_test_controls,
            "endpoints": [
                "GET /health",
                "POST /api/v1/chat",
                "GET /api/v1/analysis-contract/capabilities",
                "GET /api/v1/teachers",
                "GET /api/v1/teachers/{id}/profile",
                "GET /api/v1/teachers/{id}/trends",
                "GET /api/v1/schools?q=&year=",
                "GET /api/v1/schools/{id}/profile",
                "GET /api/v1/schools/{id}/report",
                "GET /api/v1/llm/status",
                "POST /api/v1/llm/config",
                "POST /api/v1/llm/ping",
                "GET /api/v1/experiment/status",
            ],
        }

    @app.post("/api/v1/chat")
    def api_chat(body: ChatIn):
        from time import perf_counter
        from packages.experiment_protocol import experiment_mode_status
        from packages.experiment_protocol.trace import (
            begin_request_trace, end_request_trace, request_events, save_request_trace,
        )

        request_started = perf_counter()
        request_id = body.request_id or str(uuid.uuid4())
        experiment = experiment_mode_status()
        trace_token = begin_request_trace()

        def log_early(payload: dict) -> None:
            if experiment.get("enabled") and os.environ.get("TEACHER_EVAL_EXPERIMENT_TRACE_DIR"):
                save_request_trace(
                    os.environ.get("TEACHER_EVAL_EXPERIMENT_RUN_ID", ""), request_id,
                    {"request_id": request_id, "session_id": body.session_id,
                     "question": body.question, "variant": experiment.get("active_mode"),
                     "mechanisms": experiment.get("mechanisms"),
                     "elapsed_ms": round((perf_counter() - request_started) * 1000, 2),
                     "early_result": payload, "stage_events": request_events()},
                )
        try:
            response = chat(
                body.question,
                session_id=body.session_id,
                base_revision=body.base_revision,
                request_id=request_id,
                query_mode=body.query_mode,
                fh_arm=body.fh_arm,
                clarification_reply=body.clarification_reply,
            )
            if "experiment_trace_file" not in response:
                log_early(response)
            return response
        except ValueError as e:
            log_early({"business_status": "ERROR", "exception_type": "ValueError", "detail": str(e)})
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_early({"business_status": "ERROR", "exception_type": type(e).__name__, "detail": str(e)})
            raise
        finally:
            end_request_trace(trace_token)

    @app.get("/api/v1/analysis-contract/capabilities")
    def api_analysis_contract_capabilities():
        from packages.analysis_contract.capabilities import public_manifest
        return public_manifest()

    @app.post("/api/v1/evaluations")
    def api_batch(body: BatchIn):
        return batch_evaluate(body.teacher_ids, body.years)

    @app.get("/api/v1/evaluations/{result_id}")
    def api_result(result_id: str):
        try:
            return get_result(result_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="result 不存在")

    @app.get("/api/v1/teachers")
    def api_teachers():
        return list_teachers()

    @app.get("/api/v1/teachers/{teacher_id}/profile")
    def api_teacher_profile(teacher_id: str):
        try:
            return teacher_profile(teacher_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/v1/teachers/{teacher_id}/reports")
    def api_reports(teacher_id: str):
        return teacher_reports(teacher_id)

    @app.get("/api/v1/teachers/{teacher_id}/trends")
    def api_trends(teacher_id: str):
        return teacher_trends(teacher_id, ["2023-2024", "2024-2025"])

    @app.get("/api/v1/templates")
    def api_templates():
        return templates()

    @app.get("/api/v1/schools")
    def api_schools(q: str = Query(..., min_length=1), year: Optional[str] = None, limit: int = 20):
        from scripts.school_engine import find_schools
        return {"schools": find_schools(q, year=year, limit=min(limit, 50))}

    @app.get("/api/v1/schools/{school_id}/profile")
    def api_school_profile(school_id: int):
        try:
            return school_profile(school_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))

    @app.get("/api/v1/schools/{school_id}/report")
    def api_school_report(school_id: int):
        from scripts.school_engine import evaluate_school
        res = evaluate_school(school_id)
        if "error" in res:
            raise HTTPException(status_code=404, detail=res["error"])
        result_id = str(uuid.uuid4())
        packed = {"_id": result_id, **res}
        _MEMORY[result_id] = packed
        return _finish(packed)

    @app.get("/api/v1/schools/compare")
    def api_schools_compare(school_a: str = Query(..., min_length=1), school_b: str = Query(..., min_length=1), year: Optional[str] = None):
        try:
            packed = _run_school_compare(school_a, school_b, year=year)
            response = _finish(packed)
            response["contract_mode"] = "legacy_unverified"
            response["revision_control"] = {"outcome": "NOT_APPLICABLE_LEGACY"}
            return response
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/v1/regions/compare")
    def api_regions_compare(region_a: str = Query(..., min_length=1), region_b: str = Query(..., min_length=1), year: Optional[str] = None):
        try:
            packed = _run_region_compare(region_a, region_b, year=year)
            response = _finish(packed)
            response["contract_mode"] = "legacy_unverified"
            response["revision_control"] = {"outcome": "NOT_APPLICABLE_LEGACY"}
            return response
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/v1/llm/status")
    def api_llm_status():
        return _llm_public()

    def _guard_experiment_profile_mutation() -> None:
        from packages.experiment_protocol import experiment_mode_status

        if experiment_mode_status().get("enabled"):
            raise HTTPException(status_code=409, detail="experiment_profile_locked")

    @app.post("/api/v1/llm/config")
    def api_llm_config(body: LlmConfigIn):
        _guard_experiment_profile_mutation()
        from packages.llm_gateway.gateway import save_user_config
        return save_user_config(body.model_dump())

    # Independent bridge: daily parser/UI version and frozen experiment payloads
    # remain separate. Enable only after independent security acceptance.
    from pydantic import StrictBool, StrictInt
    from packages.core import secure_credentials as _secure_credentials
    from packages.core.paired_experiment_bridge import PairedExperimentBridge, BridgeError

    _paired_bridge = PairedExperimentBridge(
        enabled=os.environ.get("TEACHER_EVAL_SECURE_PAIRED_ENABLED") == "1",
        public_profile=_secure_credentials.public_profile,
        load_profile=_secure_credentials.load_profile,
    )

    class PairedPrepareIn(BaseModel):
        batch: StrictInt = Field(ge=1, le=3)
        profile_id: str = Field(min_length=1, max_length=128)

    class PairedStartIn(BaseModel):
        confirmation_token: str = Field(min_length=32, max_length=128)
        confirmed: StrictBool = False

    class PairedReviewIn(BaseModel):
        batch: StrictInt = Field(ge=1, le=3)
        confirmed: StrictBool = False

    @app.exception_handler(BridgeError)
    async def _safe_paired_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=409)

    @app.get("/api/v1/paired-experiment/status")
    def api_paired_status():
        return _paired_bridge.status()

    @app.post("/api/v1/paired-experiment/prepare")
    def api_paired_prepare(body: PairedPrepareIn):
        return _paired_bridge.prepare(body.batch, body.profile_id)

    @app.post("/api/v1/paired-experiment/start")
    def api_paired_start(body: PairedStartIn):
        return _paired_bridge.start(body.confirmation_token, confirmed=body.confirmed)

    @app.post("/api/v1/paired-experiment/reviewack")
    def api_paired_review(body: PairedReviewIn):
        return _paired_bridge.reviewack(body.batch, confirmed=body.confirmed)

    @app.post("/api/v1/llm/ping")
    def api_llm_ping(body: Optional[LlmPingIn] = None):
        from packages.llm_gateway.gateway import ping
        return ping(expected_profile_id=body.profile_id if body else "",
                    official_only=body.official_only if body else False)

    @app.post("/api/v1/llm/models")
    def api_llm_models(body: LlmDiscoverIn):
        from packages.llm_gateway.gateway import list_models, save_user_config
        if body.save and (body.api_key or body.api_base):
            _guard_experiment_profile_mutation()
            save_user_config({
                "provider": body.provider or "custom",
                "api_base": body.api_base,
                "api_key": body.api_key,
            })
        return list_models(api_base=body.api_base, api_key=body.api_key, profile_id=body.profile_id)

    @app.get("/api/v1/llm/models")
    def api_llm_models_saved():
        from packages.llm_gateway.gateway import list_models
        return list_models()

    @app.post("/api/v1/llm/profiles/activate")
    def api_llm_activate(body: LlmProfileIn):
        _guard_experiment_profile_mutation()
        from packages.llm_gateway.gateway import activate_profile
        try:
            return activate_profile(body.id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/v1/llm/profiles/delete")
    def api_llm_delete(body: LlmProfileIn):
        _guard_experiment_profile_mutation()
        from packages.llm_gateway.gateway import delete_profile
        return delete_profile(body.id)

    @app.get("/api/v1/charts/{filename}")
    def api_chart_image(filename: str, download: bool = False):
        from fastapi.responses import FileResponse
        safe_fn = os.path.basename(filename)
        from packages.core.frozen_paths import charts_dir
        chart_path = os.path.join(charts_dir(), safe_fn)
        if not os.path.isfile(chart_path):
            raise HTTPException(status_code=404, detail="图表文件未找到或已清理")
        return FileResponse(chart_path, media_type="image/png", filename=safe_fn if download else None)

    @app.get("/api/v1/tools")
    def api_tools():
        from packages.agent.tools import default_registry
        return {"tools": default_registry.get_schemas()}

    @app.post("/api/v1/llm/interpret")
    def api_llm_interpret(body: dict):
        markdown = body.get("markdown", "")
        question = body.get("question", "")
        result = body.get("result")
        from packages.experiment_protocol import (
            experiment_mode_status,
            record_rule_output,
        )
        experiment = experiment_mode_status()
        if experiment.get("configured") and not experiment.get("enabled"):
            return {
                "error": "experiment_mode_unavailable",
                "reason": experiment.get("reason"),
                "is_fallback": False,
                "model_invoked": False,
                "experiment_mode": experiment,
            }
        if experiment.get("active_mode") == "g0_rule":
            from packages.agent.expert_advisor import generate_expert_advice

            record_rule_output("llm_interpret_endpoint")
            return {
                "text": generate_expert_advice(result or {}, question),
                "is_fallback": False,
                "provider": "local_expert_engine",
                "model": "rule-based-advisor",
                "model_invoked": False,
                "experiment_mode": "g0_rule",
            }
        if experiment.get("active_mode") in {"g1_llm_unconstrained", "g2_llm_constrained", "a1_no_execution_verification", "a2_no_output_verification_or_repair", "a3_no_answer_repair"}:
            return {
                "error": "experiment_interpret_endpoint_not_applicable",
                "reason": "实验G1/G2只允许chat共同写作路径，禁止另开未绑定TaskDraft与AnswerSpec的解释调用。",
                "is_fallback": False,
                "model_invoked": False,
                "experiment_mode": experiment,
            }
        from packages.llm_gateway.gateway import interpret_report
        try:
            return interpret_report(markdown, question, result=result)
        except Exception as e:
            try:
                from packages.agent.expert_advisor import generate_expert_advice
                fallback_text = generate_expert_advice(result or {}, question)
                return {
                    "text": fallback_text,
                    "is_fallback": True,
                    "fallback_reason": str(e),
                    "provider": "local_expert_engine",
                    "model": "rule-based-advisor"
                }
            except Exception:
                return {"error": str(e)}

    @app.get("/api/v1/experiment/status")
    def api_experiment_status():
        from packages.experiment_protocol import experiment_mode_status

        return experiment_mode_status()


    FASTAPI_AVAILABLE = True

except ImportError:
    # fastapi 未安装：降级。BaseApp.serve() 提示安装；核心逻辑（chat/batch/...）仍可直接调用。
    FASTAPI_AVAILABLE = False

    class BaseApp:
        """fastapi 未安装时的降级壳。"""

        def serve(self):
            print("[M09] fastapi 未安装，无法启动 HTTP 服务。")
            print("     安装：pip install -r apps/api/requirements.txt")
            print("     启动：uvicorn apps.api.main:app --reload")

        def health(self):
            return {"status": "ok (degrated, no fastapi)",
                    "teachers": _load_teachers(),
                    "install": "pip install -r apps/api/requirements.txt"}

    app = BaseApp()


if __name__ == "__main__":
    # 命令行冒烟：走核心逻辑（不需要 fastapi）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("fastapi:", "available" if FASTAPI_AVAILABLE else "not installed (degraded)")
    r = chat("T001 2024-2025 教学水平？")
    print("chat clarify:", r.get("clarify"), "| result_id:", r.get("result_id"))
    print("batch:", batch_evaluate(["T001", "T003"], ["2023-2024"])["results"])
    print("trends:", teacher_trends("T001", ["2023-2024"])["trend"])
