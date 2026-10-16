from __future__ import annotations

from typing import Optional

SCHEMA_VERSION = "1.8"
CATALOGUE_VERSION = "school-analytics-contract-1.0"
RESOLVER_VERSION = "school-analytics-resolver-1.0"

SUPPORTED_OPERATIONS = {
    "region_evaluation",
    "school_fact",
    "school_evaluation",
    "region_compare",
    "school_compare",
    "region_longitudinal",
    "region_ranking",
}
SUPPORTED_STATISTICS = {None, "mean", "median", "mean_and_median", "count", "max", "min", "min_and_max", "max_and_min"}
SUPPORTED_REGION_COMPARE_STATISTICS = {None, "mean", "count"}
SUPPORTED_REGION_LONGITUDINAL_STATISTICS = {"mean", "count"}
SUPPORTED_REGION_RANKING_STATISTICS = {"mean"}
SUPPORTED_SCHOOL_COMPARE_STATISTICS = {"template_weighted_score"}
SUPPORTED_SCHOOL_COMPARE_AGGREGATIONS = {"template_weighted_dimension_mean"}
SUPPORTED_AGGREGATIONS = {"school_equal_mean", "answer_pooled_mean"}
SUPPORTED_STAGE_FILTERS = ("小学", "初中", "初级中学", "高中", "高级中学", "中职", "幼儿园", "九年一贯制", "十二年一贯制")
SUPPORTED_AREA_FILTERS = ("乡村", "农村", "镇区", "城镇", "城区", "城市")
UNTRUSTED_NATURE_FILTERS = ("公办", "公立", "民办", "私立")

REGION_RANKING_SPEC = {
    "score_basis": "per_school_mean_of_non_null_normalized_values_x100",
    "direction": "descending",
    "tie_policy": "raw_score_desc_then_school_id_asc_unique_ordinal",
    "raw_score_precision": "unrounded_float",
    "display_precision": 2,
    "top_n_policy": "exact_n_or_population_size_no_tie_expansion",
    "population_definition": "region_period_filter_schools_with_non_null_indicator_answers",
}

class CapabilityError(ValueError):
    pass

def validate_region_request(
    *,
    statistic: Optional[str],
    aggregation: str,
    school_filter: Optional[str],
) -> None:
    if statistic not in SUPPORTED_STATISTICS:
        raise CapabilityError(f"unsupported_statistic:{statistic}")
    if aggregation not in SUPPORTED_AGGREGATIONS:
        raise CapabilityError(f"unsupported_aggregation:{aggregation}")
    if school_filter and any(token in school_filter for token in UNTRUSTED_NATURE_FILTERS):
        raise CapabilityError(
            "missing_school_nature_metadata:当前真实库没有可靠的办学性质字段，"
            "不能用校名关键词把未知学校推断为公办或民办"
        )

def filter_is_structured(school_filter: Optional[str]) -> bool:
    if not school_filter:
        return True
    return any(token in school_filter for token in SUPPORTED_STAGE_FILTERS + SUPPORTED_AREA_FILTERS)

def public_manifest() -> dict:
    pass
    return {
        "catalogue_version": CATALOGUE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "value_view_binding": {
            "introduced_in": "1.8",
            "values": ["raw_value", "normalized_score"],
            "legacy_missing": "UNBOUND_LEGACY",
            "legacy_view_verification": "NOT_CHECKED",
            "public_projection_before_trusted_completion": True,
        },
        "enforced_operations": sorted(SUPPORTED_OPERATIONS),
        "statistics": sorted(value for value in SUPPORTED_STATISTICS if value is not None),
        "aggregations": sorted(SUPPORTED_AGGREGATIONS),
        "school_fact": {
            "metric_types": ["question", "question_set", "compound", "school_field"],
            "execution_binding": "school_fact_v1",
            "question_set_execution_binding": "school_fact_set_v1",
            "question_set_cardinality": 2,
            "independent_replay": True,
            "missing_answer_status": "NO_DATA",
            "compound_requires_complete_components": True,
            "ratio_requires_denominator": True,
            "ratio_target_value": "computed_ratio_not_component_sum",
            "period_omitted_policy": "trusted_inheritance_then_latest_catalogue_record",
            "period_only_followup_inherits_school_and_metric": True,
            "period_only_followup_target_period_identity_policy": "resolve_same_name_within_requested_period_or_clarify",
            "period_only_followup_unresolved_status": "CLARIFY_without_revision",
            "school_id_period_mismatch_policy": "fail_closed_without_period_rewrite",
            "stable_cross_period_school_code_available": False,
            "blank_school_field_status": "NO_DATA_null",
            "blank_ratio_denominator_status": "NO_DATA_null",
            "controlled_tamper_test_mode": "explicit_mode_and_temporary_data_and_contract_overrides",
            "controlled_tamper_default_enabled": False,
            "controlled_tamper_request_scope": "optional_exact_request_id",
            "controlled_tamper_receipt_includes_request_match": True,
            "ambiguous_entity_policy": "fail_closed_with_candidates",
            "raw_and_normalized_output_policy": "answer_spec_mutually_exclusive",
            "source_missing_coded_category_policy": "return_raw_code_without_category_or_boolean_interpretation",
            "source_missing_count_policy": "return_verified_count_without_inventing_option_names",
        },
        "ordinary_query_semantics": {
            "default_mode": "semantic",
            "explicit_comparison_mode": "rule",
            "automatic_rule_fallback": False,
            "catalogue_version": "school-digitization-semantics-v1",
            "catalogue_item_count": 83,
            "shared_with_experiment_modes": ["g1_llm_unconstrained", "g2_llm_constrained", "a1_no_execution_verification", "a2_no_output_verification_or_repair", "a3_no_answer_repair"],
            "first_slice_operations": [
                "region_evaluation_overall_or_one_dimension",
                "region_evaluation_one_item_normalized_mean",
                "school_evaluation_overall_or_one_dimension",
                "school_fact_one_item",
            ],
            "answer_scope_version": "answer-spec-v2",
            "public_result_projection": "answer_spec_bound_after_contract_verification",
            "region_first_slice_statistics": ["mean", "median", "mean_and_median"],
            "out_of_catalogue_question_policy": "deterministic_clarify_without_parser_repair",
            "region_identity_policy": "canonical_administrative_scope_before_execution",
            "scope_transition_first_slice": "same_object_same_period_overall_dimension_item_overall",
            "scope_transition_inherits": ["entity", "period"],
            "scope_transition_clears_incompatible_fields": True,
            "scope_transition_excludes": ["comparison", "ranking", "longitudinal", "multi_item"],
            "pre_execution_scope_boundaries": {
                "region_item_non_mean": "unsupported_region_item_statistic_or_aggregation",
                "multi_dimension": "unsupported_dimension_set",
                "region_sample_count_comparison": "unsupported_region_sample_count_comparison",
                "region_sample_count_longitudinal": "unsupported_region_sample_count_longitudinal",
                "region_sample_count_cross_scope": "unsupported_region_sample_count_cross_scope",
            },
            "scope_boundary_side_effect_policy": "no_parser_tool_contract_pending_or_revision",
        },
        "region_compare": {
            "execution_binding": "region_compare_v1",
            "entity_cardinality": 2,
            "period_policy": "same_explicit_period",
            "supported_statistics": ["mean", "count"],
            "supported_dimensions": "overall_or_one_dimension",
            "shared_filter_and_aggregation": True,
            "execution_and_bilateral_replay_share_snapshot": True,
            "ambiguous_single_region_focus": False,
            "direct_get_endpoint_contract_mode": "legacy_unverified",
        },
        "school_evaluation": {
            "execution_binding": "school_evaluation_v1",
            "entity_cardinality": 1,
            "period_policy": "one_explicit_period",
            "supported_dimensions": "overall_or_one_dimension",
            "statistic": "template_weighted_score",
            "aggregation": "template_weighted_dimension_mean",
            "missing_indicator_policy": "score_available_items_and_report_coverage",
            "execution_and_replay_share_snapshot": True,
            "independent_replay": True,
            "durable_typed_focus": True,
            "controlled_tamper_test_mode": "TEACHER_EVAL_N3_TAMPER_TEST_MODE_with_temporary_data_and_contract_overrides",
            "controlled_tamper_default_enabled": False,
            "controlled_tamper_request_scope": "optional_exact_request_id",
            "controlled_tamper_receipt_includes_request_match": True,
        },
        "region_item": {
            "execution_binding": "region_item_mean_v1",
            "item_cardinality": 1,
            "value_view": "normalized_score",
            "statistic": "mean",
            "aggregation": "school_equal_mean",
            "denominator": "schools_with_non_null_normalized_value_for_requested_item",
            "raw_value_aggregation": "unsupported_clarify",
            "execution_and_replay_share_snapshot": True,
            "controlled_tamper_test_mode": "TEACHER_EVAL_N3_TAMPER_TEST_MODE_with_temporary_data_and_contract_overrides",
            "controlled_tamper_default_enabled": False,
            "controlled_tamper_request_scope": "optional_exact_request_id",
            "controlled_tamper_receipt_includes_request_match": True,
        },
        "region_longitudinal": {
            "execution_binding": "region_longitudinal_v1",
            "entity_cardinality": 1,
            "period_cardinality": 2,
            "period_policy": "two_distinct_explicit_calendar_years",
            "period_order_policy": "chronological_earlier_to_later",
            "temporal_mode": "repeated_cross_section",
            "same_school_cohort_claim": False,
            "supported_statistics": ["mean", "count"],
            "supported_dimensions": "overall_or_one_dimension_mean;overall_only_count",
            "shared_filter_and_aggregation": True,
            "relative_change_policy": "null_when_baseline_zero",
            "missing_period_data_status": "NO_DATA",
            "execution_and_bitemporal_replay_share_snapshot": True,
            "durable_typed_focus": True,
            "controlled_tamper_test_mode": "explicit_mode_and_temporary_data_and_contract_overrides",
            "controlled_tamper_default_enabled": False,
            "controlled_tamper_request_scope": "optional_exact_request_id",
            "controlled_tamper_receipt_includes_request_match": True,
            "unsupported_inference_requests": ["significance", "causal_inference", "prediction", "same_school_cohort"],
            "presentation_delivery_verification": True,
            "presentation_failure_changes_trusted_focus": False,
        },
        "region_ranking": {
            "execution_binding": "region_ranking_v1",
            "entity_cardinality": 1,
            "period_policy": "one_explicit_calendar_year",
            "supported_targets": ["exact_rank", "top_n", "last_rank"],
            "supported_statistics": ["mean"],
            "supported_dimensions": "overall_only",
            "aggregation": "school_equal_mean",
            "structured_school_filter": True,
            "top_n_max": 100,
            "ranking_spec": dict(REGION_RANKING_SPEC),
            "execution_and_replay_share_snapshot": True,
            "ranking_result_becomes_school_focus": False,
            "durable_typed_focus": True,
            "controlled_tamper_test_mode": "explicit_mode_and_temporary_data_and_contract_overrides",
            "controlled_tamper_default_enabled": False,
            "controlled_tamper_request_scope": "optional_exact_request_id",
            "controlled_tamper_receipt_includes_request_match": True,
        },
        "school_compare": {
            "execution_binding": "school_compare_v1",
            "entity_cardinality": 2,
            "period_policy": "same_explicit_period",
            "supported_statistics": ["template_weighted_score"],
            "supported_dimensions": "six_dimension_template_and_overall",
            "aggregation": "template_weighted_dimension_mean",
            "complete_template_required": True,
            "missing_indicator_status": "NO_DATA",
            "execution_and_bilateral_replay_share_snapshot": True,
            "ambiguous_single_school_focus": False,
            "direct_get_endpoint_contract_mode": "legacy_unverified",
            "controlled_tamper_test_mode": "explicit_mode_and_temporary_data_and_contract_overrides",
            "controlled_tamper_default_enabled": False,
            "controlled_tamper_request_scope": "optional_exact_request_id",
            "controlled_tamper_receipt_includes_request_match": True,
        },
        "structured_filters": {
            "school_stage": list(SUPPORTED_STAGE_FILTERS),
            "school_area": list(SUPPORTED_AREA_FILTERS),
            "school_nature": {"status": "unavailable", "reason": "missing_reliable_metadata"},
        },
        "session_state": {
            "durable_region_focus": True,
            "durable_region_compare_focus": True,
            "durable_region_longitudinal_focus": True,
            "durable_region_ranking_focus": True,
            "durable_school_compare_focus": True,
            "durable_school_evaluation_focus": True,
            "durable_school_fact_focus": True,
            "restorable_fields": ["entity_id", "secondary_entity_id", "period", "secondary_period", "temporal_mode", "dimension", "statistic", "aggregation", "school_filter", "target_rank", "target_top_n", "ranking_spec", "school_id", "secondary_school_id", "metric_type", "metric_key", "metric_spec"],
            "explicit_clear_fields": ["dimension", "school_filter"],
            "scope_context_fields": ["object_type", "operation", "granularity", "metric_key"],
            "field_sources": ["user_explicit", "inherited", "restored", "catalogue_or_default"],
            "failed_revision_becomes_focus": False,
            "no_data_revision_becomes_focus": True,
            "durable_pending_clarification": True,
            "pending_statuses": ["ACTIVE", "RESOLVED", "CANCELLED", "SUPERSEDED"],
            "pending_changes_trusted_focus": False,
            "school_population_scope_confirmation": "inherit_region_and_period_only_from_matching_active_pending",
            "explicit_store_unavailable_mode": "service_degraded_diagnostic_no_fallback",
            "explicit_revision_restore": True,
            "restore_creates_new_revision": True,
            "restorable_revision_statuses": ["ANSWER_PASS", "NO_DATA"],
            "failed_revision_restorable": False,
            "client_base_revision": True,
            "optimistic_concurrency": "session_head",
            "base_revision_optional_for_legacy_clients": True,
            "stale_base_result": "ERROR",
            "stale_attempt_audit": True,
            "stale_changes_trusted_focus": False,
            "failed_execution_revision_outcome": "COMMITTED_AUDIT_ONLY",
            "failed_execution_revision_persisted": True,
            "failed_execution_changes_trusted_focus": False,
        },
        "execution_consistency": {
            "mode": "sqlite_read_transaction_v1",
            "execution_and_replay_share_snapshot": True,
            "independent_aggregation_implementations": True,
            "dataset_change_before_execution": "fail_closed",
            "transaction_trace": "sanitized_stage_and_sql_role_sequence",
            "temporary_data_override": "TEACHER_EVAL_DATA_DB_read_only",
            "test_control_gate": "requires_explicit_data_override",
            "failure_injection": "explicit_mode_and_temporary_data_and_contract_overrides",
            "failure_injection_default_enabled": False,
        },
        "presentation_consistency": {
            "client_request_sequence_guard": True,
            "overlapping_requests_allowed": True,
            "latest_request_owns_result_panel": True,
            "late_response_behavior": "history_only",
            "late_response_changes_page_result": False,
            "base_revision_update": "monotonic_max",
            "request_history_limit": 50,
            "controlled_test_mode": "TEACHER_EVAL_PRESENTATION_TEST_MODE=1_and_presentation_test=1",
            "controlled_test_default_enabled": False,
            "synthetic_test_is_real_api_evidence": False,
        },
        "public_status_security": {
            "full_api_key_returned": False,
            "profile_keys_returned": False,
            "masked_key_metadata_only": True,
            "input_key_value_not_rehydrated": True,
            "health_filesystem_paths_redacted": True,
            "llm_config_path_returned": False,
            "packaged_real_database": False,
            "packaged_provider_registry": False,
            "runtime_database_override_supported": True,
        },
        "unsupported_in_first_slice": ["semantic_region_evaluation_median", "dimension_median", "multi_dimension", "region_item_raw_aggregation", "region_item_set", "school_topic_item_set", "region_causal_inference", "region_compare_median", "region_compare_extrema", "region_ranking_dimension", "region_ranking_answer_pooled", "region_ranking_multi_period", "region_ranking_significance", "region_ranking_causal_inference", "region_longitudinal_three_or_more_periods", "region_longitudinal_same_school_cohort", "region_longitudinal_significance", "region_longitudinal_causal_inference", "region_longitudinal_prediction", "school_compare_dimension_only", "school_compare_fact_metric", "school_ranking", "national_ranking", "teacher"],
    }
