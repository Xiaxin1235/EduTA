from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Tuple

from .capabilities import (
    SCHEMA_VERSION,
    REGION_RANKING_SPEC,
    SUPPORTED_AGGREGATIONS,
    SUPPORTED_OPERATIONS,
    SUPPORTED_REGION_COMPARE_STATISTICS,
    SUPPORTED_REGION_LONGITUDINAL_STATISTICS,
    SUPPORTED_REGION_RANKING_STATISTICS,
    SUPPORTED_SCHOOL_COMPARE_AGGREGATIONS,
    SUPPORTED_SCHOOL_COMPARE_STATISTICS,
    SUPPORTED_STATISTICS,
)

@dataclass(frozen=True)
class FieldSource:
    value: Any
    origin: str
    source_turn: str
    evidence_span: Optional[str] = None
    inherited_from: Optional[int] = None

@dataclass(frozen=True)
class AnalysisContract:
    task_id: str
    revision: int
    parent_revision: Optional[int]
    turn_id: str
    session_id: str
    dataset_version: str
    catalogue_version: str
    resolver_version: str
    entity_id: str
    region_keyword: str
    period: str
    dimension: Optional[str]
    indicator_ids: Tuple[int, ...]
    statistic: Optional[str]
    target_rank: Optional[int]
    target_top_n: Optional[int]
    aggregation: str
    school_filter: Optional[str]
    requested_outputs: Tuple[str, ...]
    requested_statistics: Tuple[str, ...]
    sources: Dict[str, FieldSource] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    operation: str = "region_evaluation"
    entity_type: str = "region"
    zero_policy: str = "retain_stored_zeros"
    null_policy: str = "exclude_null_normalized_values"
    normalization: str = "stored_normalized_value_v1"
    revision_action: str = "UPSERT"
    restored_from_revision: Optional[int] = None
    school_id: Optional[int] = None
    metric_type: Optional[str] = None
    metric_key: Optional[str] = None
    metric_name: Optional[str] = None
    metric_spec: Optional[Dict[str, Any]] = None
    secondary_entity_id: Optional[str] = None
    secondary_region_keyword: Optional[str] = None
    secondary_school_id: Optional[int] = None
    secondary_period: Optional[str] = None
    temporal_mode: Optional[str] = None
    ranking_spec: Optional[Dict[str, Any]] = None

    value_view: Optional[str] = None

    target_dimensions: Tuple[str, ...] = ()
    region_identity: Optional[Dict[str, Any]] = None

    def validate(self) -> None:
        if self.region_identity is not None:
            from packages.semantic_query.typed_plan import region_sql
            region_sql(self.region_identity)
        if not all((self.task_id, self.turn_id, self.session_id, self.dataset_version, self.entity_id, self.period)):
            raise ValueError("incomplete_contract")
        if self.revision < 1:
            raise ValueError("invalid_revision")
        if self.operation not in SUPPORTED_OPERATIONS:
            raise ValueError("unsupported_operation")
        if self.value_view not in {None, "raw_value", "normalized_score"}:
            raise ValueError("invalid_contract_value_view")
        if self.target_dimensions:
            if (
                len(self.target_dimensions) != 2
                or len(set(self.target_dimensions)) != 2
                or any(item not in {"数字资源", "教育教学", "数字素养", "基础设施", "教育治理", "保障机制"} for item in self.target_dimensions)
                or self.operation not in {"school_evaluation", "region_evaluation"}
                or self.dimension is not None
                or self.value_view != "normalized_score"
            ):
                raise ValueError("invalid_dimension_pair_contract")
        if self.operation not in {"school_fact", "region_evaluation"} and self.value_view == "raw_value":
            raise ValueError("unsupported_contract_value_view")
        if self.operation == "region_evaluation" and not self.indicator_ids:
            raise ValueError("missing_region_indicators")
        if len(set(self.indicator_ids)) != len(self.indicator_ids) or any(type(q) is not int or q < 1 for q in self.indicator_ids):
            raise ValueError("invalid_indicator_ids")
        if self.operation == "region_evaluation":
            if self.statistic not in SUPPORTED_STATISTICS or self.aggregation not in SUPPORTED_AGGREGATIONS:
                raise ValueError("unsupported_contract_rule")
            if self.target_dimensions and (
                self.statistic not in {None, "mean"}
                or self.aggregation != "school_equal_mean"
                or self.metric_type is not None
            ):
                raise ValueError("unsupported_region_dimension_pair_scope")
            if self.metric_type is not None:
                if self.metric_type != "question" or len(self.indicator_ids) != 1:
                    raise ValueError("invalid_region_item_metric")
                if not self.metric_key or not self.metric_name or not isinstance(self.metric_spec, dict):
                    raise ValueError("incomplete_region_item_metric")
                if self.metric_key != f"question:{self.indicator_ids[0]}":
                    raise ValueError("invalid_region_item_metric_key")
                if self.metric_spec.get("type") != "question" or self.metric_spec.get("qid") != self.indicator_ids[0]:
                    raise ValueError("invalid_region_item_metric_binding")
                if self.dimension is not None or self.statistic not in {None, "mean", "median", "mean_and_median"}:
                    raise ValueError("unsupported_region_item_scope")
                if self.aggregation != "school_equal_mean":
                    raise ValueError("unsupported_region_item_aggregation")
            elif any(value is not None for value in (self.metric_key, self.metric_name, self.metric_spec)):
                raise ValueError("unexpected_region_metric")
        elif self.operation == "region_compare":
            if self.entity_type != "region_pair":
                raise ValueError("invalid_region_compare_entity_type")
            if not self.secondary_entity_id or not self.secondary_region_keyword:
                raise ValueError("incomplete_region_compare_entity")
            if self.entity_id == self.secondary_entity_id:
                raise ValueError("duplicate_region_compare_entity")
            if not self.region_keyword or not self.indicator_ids:
                raise ValueError("incomplete_region_compare_scope")
            if self.statistic not in SUPPORTED_REGION_COMPARE_STATISTICS:
                raise ValueError("unsupported_region_compare_statistic")
            if self.aggregation not in SUPPORTED_AGGREGATIONS:
                raise ValueError("unsupported_contract_rule")
            if self.dimension and self.statistic == "count":
                raise ValueError("unsupported_region_compare_dimension_count")
            if self.target_rank is not None or self.target_top_n is not None:
                raise ValueError("unsupported_region_compare_ranking")
        elif self.operation == "region_longitudinal":
            if self.entity_type != "region_period_pair":
                raise ValueError("invalid_region_longitudinal_entity_type")
            if not self.region_keyword or not self.indicator_ids or not self.secondary_period:
                raise ValueError("incomplete_region_longitudinal_scope")
            if any(
                len(value) != 4 or not value.isdigit() or not value.startswith("20")
                for value in (self.period, self.secondary_period)
            ):
                raise ValueError("invalid_region_longitudinal_period_format")
            if self.period == self.secondary_period:
                raise ValueError("duplicate_region_longitudinal_period")
            if self.period > self.secondary_period:
                raise ValueError("invalid_region_longitudinal_period_order")
            if self.temporal_mode != "repeated_cross_section":
                raise ValueError("invalid_region_longitudinal_temporal_mode")
            if self.statistic not in SUPPORTED_REGION_LONGITUDINAL_STATISTICS:
                raise ValueError("unsupported_region_longitudinal_statistic")
            if self.aggregation not in SUPPORTED_AGGREGATIONS:
                raise ValueError("unsupported_contract_rule")
            if self.dimension and self.statistic == "count":
                raise ValueError("unsupported_region_longitudinal_dimension_count")
            if self.target_rank is not None or self.target_top_n is not None:
                raise ValueError("unsupported_region_longitudinal_ranking")
        elif self.operation == "region_ranking":
            if self.entity_type != "region_school_population":
                raise ValueError("invalid_region_ranking_entity_type")
            if not self.region_keyword or not self.indicator_ids:
                raise ValueError("incomplete_region_ranking_scope")
            if self.dimension is not None:
                raise ValueError("unsupported_region_ranking_dimension")
            if self.statistic not in SUPPORTED_REGION_RANKING_STATISTICS:
                raise ValueError("unsupported_region_ranking_statistic")
            if self.aggregation != "school_equal_mean":
                raise ValueError("unsupported_region_ranking_aggregation")
            if (self.target_rank is None) == (self.target_top_n is None):
                raise ValueError("invalid_region_ranking_target")
            if self.target_rank is not None and self.target_rank != -1 and self.target_rank < 1:
                raise ValueError("invalid_region_ranking_rank")
            if self.target_top_n is not None and not 1 <= self.target_top_n <= 100:
                raise ValueError("invalid_region_ranking_top_n")
            if self.ranking_spec != REGION_RANKING_SPEC:
                raise ValueError("invalid_region_ranking_spec")
        elif self.operation == "school_compare":
            if self.entity_type != "school_pair":
                raise ValueError("invalid_school_compare_entity_type")
            if not isinstance(self.school_id, int) or not isinstance(self.secondary_school_id, int):
                raise ValueError("incomplete_school_compare_entity")
            if not self.secondary_entity_id:
                raise ValueError("incomplete_school_compare_entity")
            if self.school_id == self.secondary_school_id or (
                self.entity_id == self.secondary_entity_id
                and (not self.secondary_period or self.period == self.secondary_period)
            ):
                raise ValueError("duplicate_school_compare_entity")
            if not self.indicator_ids or self.metric_type != "school_template":
                raise ValueError("incomplete_school_compare_scope")
            if not self.metric_key or not isinstance(self.metric_spec, dict):
                raise ValueError("incomplete_school_compare_template")
            complete_template_required = self.metric_spec.get("complete_template_required")
            if complete_template_required not in {None, True}:
                raise ValueError("invalid_school_compare_missing_policy")
            if (
                complete_template_required is True
                and self.null_policy != "require_complete_template_no_nulls"
            ):
                raise ValueError("invalid_school_compare_null_policy")
            if self.statistic not in SUPPORTED_SCHOOL_COMPARE_STATISTICS:
                raise ValueError("unsupported_school_compare_statistic")
            if self.aggregation not in SUPPORTED_SCHOOL_COMPARE_AGGREGATIONS:
                raise ValueError("unsupported_school_compare_aggregation")
            known_dimensions = {
                str(item.get("name") or "")
                for item in self.metric_spec.get("dimensions") or []
            }
            if self.dimension is not None and self.dimension not in known_dimensions:
                raise ValueError("unsupported_school_compare_dimension")
            if self.school_filter is not None:
                raise ValueError("unsupported_school_compare_scope")
            if self.target_rank is not None or self.target_top_n is not None:
                raise ValueError("unsupported_school_compare_ranking")
        elif self.operation == "school_evaluation":
            if self.entity_type != "school" or not isinstance(self.school_id, int):
                raise ValueError("invalid_school_evaluation_entity")
            if not self.indicator_ids or self.metric_type != "school_template":
                raise ValueError("incomplete_school_evaluation_scope")
            if not self.metric_key or not self.metric_name or not isinstance(self.metric_spec, dict):
                raise ValueError("incomplete_school_evaluation_template")
            if self.metric_spec.get("type") != "school_template":
                raise ValueError("invalid_school_evaluation_template_type")
            if self.metric_spec.get("complete_template_required") is not False:
                raise ValueError("invalid_school_evaluation_missing_policy")
            dimensions = self.metric_spec.get("dimensions") or []
            if not dimensions:
                raise ValueError("empty_school_evaluation_template")
            spec_ids = tuple(
                int(qid)
                for item in dimensions
                for qid in (item.get("indicator_ids") or ())
            )
            if tuple(self.indicator_ids) != tuple(dict.fromkeys(spec_ids)):
                raise ValueError("invalid_school_evaluation_indicators")
            if self.dimension is not None and (
                len(dimensions) != 1 or str(dimensions[0].get("name") or "") != self.dimension
            ):
                raise ValueError("invalid_school_evaluation_dimension")
            if self.target_dimensions and [str(item.get("name") or "") for item in dimensions] != list(self.target_dimensions):
                raise ValueError("invalid_school_evaluation_dimension_pair")
            if self.statistic not in SUPPORTED_SCHOOL_COMPARE_STATISTICS:
                raise ValueError("unsupported_school_evaluation_statistic")
            if self.aggregation not in SUPPORTED_SCHOOL_COMPARE_AGGREGATIONS:
                raise ValueError("unsupported_school_evaluation_aggregation")
            if self.school_filter is not None or self.target_rank is not None or self.target_top_n is not None:
                raise ValueError("unsupported_school_evaluation_scope")
        elif self.operation == "school_fact":
            if self.entity_type != "school" or not isinstance(self.school_id, int):
                raise ValueError("invalid_school_fact_entity")
            if self.metric_type not in {"question", "question_set", "compound", "school_field"}:
                raise ValueError("invalid_school_fact_metric_type")
            if not self.metric_key or not self.metric_name or not isinstance(self.metric_spec, dict):
                raise ValueError("incomplete_school_fact_metric")
            if self.metric_type in {"question", "question_set", "compound"} and not self.indicator_ids:
                raise ValueError("missing_school_fact_indicators")
            if self.statistic is not None or self.aggregation != "not_applicable":
                raise ValueError("unsupported_school_fact_statistic")
            if self.dimension is not None or self.school_filter is not None or self.target_rank is not None or self.target_top_n is not None:
                raise ValueError("unsupported_school_fact_scope")
            if self.metric_spec.get("type") != self.metric_type:
                raise ValueError("invalid_school_fact_metric_binding")
            if self.metric_type == "question" and (
                len(self.indicator_ids) != 1
                or self.metric_key != f"question:{self.indicator_ids[0]}"
                or type(self.metric_spec.get("qid")) is not int
                or self.metric_spec.get("qid") != self.indicator_ids[0]
            ):
                raise ValueError("invalid_school_fact_question_binding")
            if self.metric_type == "question_set":
                spec = self.metric_spec
                ids = tuple(spec.get("qids") or ())
                items = spec.get("items") or []
                derived = spec.get("derived")
                if (
                    len(ids) != 2 or len(set(ids)) != 2
                    or any(type(qid) is not int for qid in ids)
                    or ids != self.indicator_ids
                    or len(items) != 2
                    or any(item.get("type") != "question" or item.get("qid") != qid
                           for item, qid in zip(items, ids))
                    or self.metric_key != f"question_set:{ids[0]},{ids[1]}"
                ):
                    raise ValueError("invalid_school_question_set_binding")
                if derived is not None and (
                    not isinstance(derived, dict)
                    or derived.get("operation") not in {"subtract", "divide_percent", "add", "divide", "sum", "ratio"}
                    or (derived.get("left_qid"), derived.get("right_qid")) != ids
                ):
                    raise ValueError("invalid_school_question_set_derived")
            if self.metric_type == "compound" and tuple(self.metric_spec.get("qids") or ()) != self.indicator_ids:
                raise ValueError("invalid_school_fact_compound_binding")
            if self.metric_type == "school_field" and (
                self.indicator_ids
                or self.metric_spec.get("field") not in {"teacher_num", "student_num", "class_num", "school_area_type", "school_type"}
                or self.metric_key != f"school_field:{self.metric_spec.get('field')}"
            ):
                raise ValueError("invalid_school_fact_field_binding")
        if self.revision_action not in {"UPSERT", "RESTORE"}:
            raise ValueError("invalid_revision_action")
        if self.revision_action == "RESTORE" and (
            not isinstance(self.restored_from_revision, int) or self.restored_from_revision < 1
        ):
            raise ValueError("missing_restored_from_revision")
        if self.revision_action == "UPSERT" and self.restored_from_revision is not None:
            raise ValueError("unexpected_restored_from_revision")

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["indicator_ids"] = list(self.indicator_ids)
        payload["requested_outputs"] = list(self.requested_outputs)
        payload["requested_statistics"] = list(self.requested_statistics)
        payload["target_dimensions"] = list(self.target_dimensions)
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AnalysisContract":
        data = dict(payload)

        data["indicator_ids"] = tuple(data.get("indicator_ids") or ())
        data["requested_outputs"] = tuple(data.get("requested_outputs") or ())
        data["requested_statistics"] = tuple(data.get("requested_statistics") or ())
        data["target_dimensions"] = tuple(data.get("target_dimensions") or ())
        data["sources"] = {
            key: value if isinstance(value, FieldSource) else FieldSource(**value)
            for key, value in (data.get("sources") or {}).items()
        }
        contract = cls(**data)
        contract.validate()
        return contract

def validate_projection_binding(result: Dict[str, Any], answer_spec: Dict[str, Any]) -> None:
    pass
    if result.get("contract_mode") != "enforced":
        return
    contract = result.get("contract")
    receipt = result.get("execution_receipt")
    verification = result.get("contract_verification") or {}
    if not isinstance(contract, dict) or not isinstance(receipt, dict):
        raise ValueError("public_projection_missing_contract_binding")
    if verification.get("status") != "PASS" or verification.get("failures"):
        raise ValueError("public_projection_requires_verified_computation")
    checks = verification.get("checks") or []
    if not checks or any(check.get("status") == "FAIL" for check in checks):
        raise ValueError("public_projection_requires_verified_computation")
    checked = {check.get("name"): check.get("status") for check in checks}
    for name in ("consistent_read_snapshot", "contract_binding", "requested_output_coverage", "independent_replay"):
        if checked.get(name) != "PASS":
            raise ValueError(f"public_projection_requires_{name}")
    operation = {"school_eval": "school_evaluation", "region_eval": "region_evaluation"}.get(answer_spec.get("operation"), answer_spec.get("operation"))
    if operation != contract.get("operation") or receipt.get("operation") != operation:
        raise ValueError("public_projection_operation_mismatch")
    if receipt.get("contract_revision") != contract.get("revision") or receipt.get("dataset_version") != contract.get("dataset_version"):
        raise ValueError("public_projection_receipt_mismatch")
    if result.get("_id") is not None and receipt.get("execution_id") != result.get("_id"):
        raise ValueError("public_projection_execution_mismatch")
    granularity = (
        "item_set" if contract.get("operation") == "school_fact" and contract.get("metric_type") == "question_set"
        else "item" if contract.get("operation") == "school_fact" or contract.get("metric_type") == "question"
        else "dimension_pair" if len(contract.get("target_dimensions") or ()) == 2
        else "dimension" if contract.get("dimension") else "overall"
    )
    if (answer_spec.get("granularity") or "overall") != granularity:
        raise ValueError("public_projection_granularity_mismatch")
    if operation == "school_evaluation":
        scope_slots = {"dimension_scores", "dimension_extrema"}
        requested = set(answer_spec.get("requested_result_statistics") or ()) & scope_slots
        contracted = set(contract.get("requested_statistics") or ()) & scope_slots
        if requested != contracted:
            raise ValueError("public_projection_school_profile_scope_mismatch")
    if operation == "region_compare":
        requested = set(answer_spec.get("requested_result_statistics") or ())
        contracted = set(contract.get("requested_statistics") or ())
        count_sides = {"school_count_a", "school_count_b"}
        if (requested & count_sides) != (contracted & count_sides):
            raise ValueError("public_projection_region_count_scope_mismatch")
        if requested & count_sides and (
            not count_sides <= requested
            or result.get("school_count_a") is None
            or result.get("school_count_b") is None
        ):
            raise ValueError("public_projection_region_count_incomplete")
    if granularity == "dimension_pair" and list(answer_spec.get("target_dimensions") or []) != list(contract.get("target_dimensions") or []):
        raise ValueError("public_projection_dimension_pair_mismatch")
    if granularity == "item_set":
        expected_ids = contract.get("indicator_ids")
        result_ids = result.get("indicator_ids")
        if result_ids is None:
            result_ids = (result.get("execution_scope") or {}).get("indicator_ids")
        item_ids = [item.get("question_id") for item in result.get("item_results") or []]
        if (
            answer_spec.get("value_view") not in {"raw_value", "normalized_score"}
            or answer_spec.get("indicator_ids") != expected_ids
            or result_ids != expected_ids
            or item_ids != expected_ids
        ):
            raise ValueError("public_projection_question_set_scope_mismatch")
    if answer_spec.get("target_stat") and answer_spec["target_stat"] != contract.get("statistic"):
        raise ValueError("public_projection_statistic_mismatch")
    if contract.get("value_view") is not None and contract["value_view"] != answer_spec.get("value_view"):
        raise ValueError("public_projection_value_view_mismatch")

@dataclass(frozen=True)
class ExecutionReceipt:
    execution_id: str
    contract_revision: int
    dataset_version: str
    actual_region: str
    actual_period: str
    school_count: int
    answer_count: int
    aggregation: str
    query_template: str
    parameters: Tuple[Any, ...]
    read_consistency: str = "unverified"
    snapshot_id: Optional[str] = None
    sqlite_data_version: Optional[int] = None
    connection_token: Optional[str] = None
    transaction_trace: Tuple[Dict[str, Any], ...] = ()
    operation: str = "region_evaluation"
    actual_entity_id: Optional[str] = None
    metric_key: Optional[str] = None
    actual_secondary_region: Optional[str] = None
    actual_secondary_entity_id: Optional[str] = None
    actual_secondary_period: Optional[str] = None
    temporal_mode: Optional[str] = None
    school_count_a: Optional[int] = None
    school_count_b: Optional[int] = None
    answer_count_a: Optional[int] = None
    answer_count_b: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["parameters"] = list(self.parameters)
        payload["transaction_trace"] = list(self.transaction_trace)
        return payload
