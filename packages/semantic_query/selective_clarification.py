"""Selective clarification from evidence already discovered by public binding.

No model, answer scoring, or business execution dependency. Supplements reuse
the existing metadata binder. Intent uncertainty stays separate from data
availability, and capture is request-local.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar

VERSION = "selective-clarification-v1"
TTL_SECONDS = 900
_CAPTURE = ContextVar("selective_binding_capture", default=None)
_CONTEXT = ContextVar("selective_binding_context", default={})


def pending_session(session_id, mode):
    """G1 clarification state is separate from both production and G2 focus."""
    if mode.get("active_mode") == "g1_llm_unconstrained":
        return "selective-g1:" + str(mode.get("run_id_hash") or "") + ":" + session_id
    return session_id


def _option(label, difference, constraint, source, aliases=()):
    return {"option_id": hashlib.sha256(json.dumps(constraint, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16],
            "label": label, "difference": difference, "constraint": constraint,
            "evidence_source": source, "aliases": list(aliases)}


def _slot_boundary(question, candidate, slot, token, options, remaining=None, original_question=None):
    issued = time.time()
    spec = {"version": VERSION, "slot": slot, "token": token, "original_question": original_question or question,
            "working_question": question, "options": options, "issued_at": issued,
            "expires_at": issued + TTL_SECONDS, "task_candidate": copy.deepcopy(candidate),
            "original_route": {}, "resolved_constraints": {}, "unresolved_slots": remaining or [slot]}
    labels = {"school": "所指学校", "value_view": "原始值或标准分", "aggregation": "统计单位", "statistic": "统计量", "dimension": "要查询的维度"}
    return {"intent": "clarify", "reason": "selective_" + slot + "_ambiguity", "original": question,
            "missing": ["请确认" + labels[slot] + "，不同解释会改变结果。"], "selective_clarification": spec}


def aspect_boundary(question,candidate):
    """An explicit unresolved aspect is not permission to answer overall score."""
    intent=candidate.get('intent') or ''
    if intent in {'clarify','region_position'} or intent.endswith('_ranking'):return None
    if any(candidate.get(k) for k in ('dimension','topics','question_ids','compound_key','school_field')):
        return None
    from packages.semantic_query import runtime as rt
    text=rt._scope_assertion_text(question)
    if rt._OVERALL_SCOPE_RE.search(text) or not re.search(r'得分|分数|均分|多少分|平均.{0,4}分|怎么样|如何|什么水平',text):
        return None
    aspect=re.search(r'在[^，,。；;！？!?]{1,60}?(?:上|方面|这[一二两几]块)|[^，,。；;！？!?]{1,40}?这一块',text)
    if not aspect:
        return None
    from packages.semantic_query.catalogue import dimension_meanings
    options=[_option(d['name'],d['description'],{'dimension':d['name']},'source_questionnaire_dimension_meanings') for d in dimension_meanings()]
    return _slot_boundary(question,candidate,'dimension',aspect.group(0),options)


def draft_boundary(question, candidate, context, by_id):
    """Bounded intent recovery from parsed slots + actual context/catalogue.

    Defaults remain defaults. Unknown parser fields alone never manufacture
    alternatives. A literal unresolved choice or a real multi-school reference
    must be present, and the common capability must support both meanings.
    """
    c = copy.deepcopy(candidate)
    q = question
    from packages.experiment_protocol.mode import requirement_preservation_enabled
    preserve = requirement_preservation_enabled()
    if not preserve and c.get('intent') == 'clarify':
        return q, c, None
    from packages.semantic_query import runtime as rt
    # Mask exclusions in place: option spans must retain original offsets.
    # A quoted alternative rejected by the user is not an unresolved choice.
    affirmative = rt._scope_assertion_text(q)
    # Only the existing one-subject evaluation/fact capabilities are extended.
    permitted = {"school_fact", "school_eval", "region_eval", "clarify"}
    if c.get("intent") not in permitted:
        return q, c, None
    def alternatives(left, right):
        join = r"\s*(?:还是|或是|抑或)\s*"
        return re.search("(?:" + left + join + right + "|" + right + join + left + ")", affirmative)
    view = alternatives(r"(?:原始(?:登记)?(?:值|数量|作答)|原值)", r"(?:标准(?:化)?(?:分|得分)|归一化(?:得分|分数))")
    aggregation = alternatives(r"(?:学校等权(?:平均)?|先算每校均分再平均)", r"(?:作答池化(?:平均)?|所有作答合并平均)")
    statistic = alternatives(r"(?:均值|均分|平均值)", r"(?:中位数|中位值)")
    # '分别给出/比较两种' is an explicit request for both, not an intent gap.
    if re.search(r"分别给出|两种都|两者都|比较两种", affirmative):
        view = aggregation = statistic = None
    if not preserve:
        view = aggregation = statistic = None
    qids = c.get("question_ids") or []
    if c.get("intent") == "clarify":
        if not (view or aggregation or statistic or re.search(r"前者|后者|该校|这所学校|那所学校|其中一所", q)):
            return question, candidate, None
        if len(qids) == 1 and qids[0] in by_id and (c.get("schools") or re.search(r"该校|这所学校|那所学校|其中一所", q)):
            c["intent"] = "school_fact"
        elif c.get("regions") and len(c.get("periods") or []) == 1:
            c["intent"] = "region_eval"
        elif c.get("dimension") in rt._DIMENSIONS and re.search(r"该校|这所学校|那所学校|其中一所", q):
            c["intent"] = "school_eval"
        else:
            return question, candidate, None
    reference = re.search(r"前者(?:那所学校|学校)?|后者(?:那所学校|学校)?|刚才那所学校|其中一所学校|其中那所学校|那所学校|这所学校|该学校|该校", q)
    if reference and c["intent"] in {"school_fact", "school_eval"}:
        literal = [s for s in rt._literal_school_name_spans(q) if not rt._is_contextual_entity_reference(s)]
        if not literal:
            names = []
            operation = context.get("last_scope_operation")
            if operation == "school_compare":
                names = context.get("last_successful_school_candidates") or [context.get("last_school_compare_a"), context.get("last_school_compare_b")]
            elif operation in {"school_fact", "school_eval"}:
                names = [context.get("last_school") or context.get("last_school_fact_entity")]
            names = list(dict.fromkeys(n for n in names if isinstance(n, str) and n.strip() and not rt._is_contextual_entity_reference(n)))
            if preserve and not rt._scope_years(q) and re.fullmatch(r"20\d{2}", str(context.get("last_year") or "")):
                c["periods"] = [context["last_year"]]
                q += "，年份" + context["last_year"] + "年"
                c.setdefault("evidence", {})["period"] = context["last_year"]
            selected = None
            if len(names) == 1:
                selected = names[0]
            elif len(names) == 2 and reference.group().startswith(("前者", "后者")):
                selected = names[0 if reference.group().startswith("前者") else 1]
            if selected:
                q = q[:reference.start()] + selected + q[reference.end():]
                c["schools"] = [selected]
                c.setdefault("evidence", {})["school"] = selected
            elif 2 <= len(names) <= 3:
                options = [_option(n, "仅查询此前成功上下文中的" + n + "；保留当前年份和指标。", {"school": n},
                                   "actual_successful_school_context", aliases=("前者" if i == 0 else "后者",)) for i, n in enumerate(names)]
                remaining = ["school"] + (["value_view"] if view else []) + (["aggregation"] if aggregation else []) + (["statistic"] if statistic else [])
                return q, c, _slot_boundary(q, c, "school", reference.group(), options, remaining, original_question=question)
            else:
                # Do not let a model-selected school stand in for no antecedent.
                return q, c, {"intent": "clarify", "reason": "scope_reference_not_unique", "original": q,
                              "missing": ["当前上下文不能唯一确定所指学校，请提供学校名称。"]}
    if view and len(qids) == 1 and qids[0] in by_id and c["intent"] == "school_fact":
        item = by_id[qids[0]]
        if item.get("scale") and item.get("value_type") and item.get("unit"):
            options = [_option("原始值", "读取第" + str(qids[0]) + "题登记值，单位为" + item["unit"] + "。",
                               {"value_view": "raw_value"}, "trusted_metric_catalogue", aliases=("原值", "原始数量", "看原始值")),
                       _option("标准分", "读取同一题既有归一化得分，按标准评分尺度展示；不等同原始单位。",
                               {"value_view": "normalized_score"}, "trusted_metric_catalogue", aliases=("标准化得分", "归一化得分", "看标准分"))]
            return q, c, _slot_boundary(q, c, "value_view", view.group(), options, original_question=question)
    if aggregation and c["intent"] == "region_eval" and not qids and c.get("statistic") != "count":
        if c.get("statistic") == "median" and not statistic:
            return q, c, {"intent": "clarify", "reason": "unsupported_region_median_aggregation", "original": q,
                          "missing": ["现有中位数按每校得分计算，不支持作答池化；请明确使用学校等权中位数或作答池化均值。"]}
        options = [_option("学校等权平均", "先算各校均分，再让每所学校权重相同。", {"aggregation": "school_equal_mean"},
                           "common_region_aggregation_capability", aliases=("学校等权", "按学校等权", "先算每校均分再平均")),
                   _option("作答池化平均", "合并全部有效作答求均值，有效作答较多的学校权重更大。", {"aggregation": "answer_pooled_mean"},
                           "common_region_aggregation_capability", aliases=("作答池化", "按作答池化", "所有作答合并平均"))]
        return q, c, _slot_boundary(q, c, "aggregation", aggregation.group(), options,
                                    ["aggregation"] + (["statistic"] if statistic else []), original_question=question)
    if statistic and c["intent"] == "region_eval" and not qids and c.get("statistic") != "count":
        if c.get("aggregation") == "answer_pooled_mean":
            return q, c, {"intent": "clarify", "reason": "unsupported_region_median_aggregation", "original": q,
                          "missing": ["已选作答池化，现有能力只支持均值；中位数需学校等权。请明确完整口径。"]}
        options = [_option("均值", "对该范围现有得分求算术平均。", {"statistic": "mean"}, "common_region_statistic_capability", aliases=("均分", "平均值")),
                   _option("中位数", "按现有得分排序后取中间位置，不等同算术平均。", {"statistic": "median"}, "common_region_statistic_capability", aliases=("中位值",))]
        return q, c, _slot_boundary(q, c, "statistic", statistic.group(), options, original_question=question)
    return q, c, None


def carry_confirmations(routed, resolution):
    spec = routed.get("selective_clarification")
    if not resolution:
        return routed
    if not spec:
        if routed.get("intent") == "clarify":
            # Unsupported cross-slot capabilities remain ordinary clarification,
            # but the existing pending JSON still retains the same root task.
            routed["selective_task_state"] = {
                "original_question": resolution["spec"]["original_question"],
                "working_question": routed.get("original") or resolution["question"],
                "task_candidate": copy.deepcopy(resolution.get("candidate")),
                "resolved_constraints": {**resolution["spec"].get("resolved_constraints", {}),
                                         resolution["slot"]: resolution["constraint"]},
                "unresolved_slots": [s for s in resolution["spec"]["unresolved_slots"] if s != resolution["slot"]],
            }
        return routed
    spec["original_question"] = resolution["spec"]["original_question"]
    spec["resolved_constraints"] = {**resolution["spec"].get("resolved_constraints", {}),
                                     resolution["slot"]: resolution["constraint"]}
    spec["confirmed_choices"] = {**resolution["spec"].get("confirmed_choices", {}),
                                  resolution["slot"]: {"options": resolution["spec"]["options"],
                                                       "label": resolution["replacement"]}}
    return routed


@contextmanager
def capture_bindings():
    token = _CAPTURE.set([])
    context_token = _CONTEXT.set({})
    try:
        yield
    finally:
        _CONTEXT.reset(context_token)
        _CAPTURE.reset(token)


def set_binding_context(context, question):
    # A stored focus is evidence only for an actual continuation, not an
    # implicit default for every new question mentioning the same bare name.
    compatible = bool(re.search(r"继续|沿用|刚才|上次|同一|该地区|这个地区", question))
    _CONTEXT.set(copy.deepcopy(context) if compatible else {})


def observations():
    return copy.deepcopy(_CAPTURE.get() or [])


def region_identity(region):
    return {k: region.get(k) for k in ("province", "city", "district", "region_type", "full_name", "name")}


def observe_regions(token, candidates):
    """Called ONLY at an existing binding's multiple-match branch.

    Return a unique identity from compatible real context, otherwise retain
    all distinct candidates; never truncate a larger set into a false choice.
    """
    unique = {}
    for candidate in candidates:
        if not candidate.get("province") or not candidate.get("full_name"):
            continue
        identity = region_identity(candidate)
        key = json.dumps(identity, ensure_ascii=False, sort_keys=True)
        unique[key] = copy.deepcopy(candidate)
    candidates = [unique[k] for k in sorted(unique)]
    if len(candidates) < 2:
        return None
    context = _CONTEXT.get()
    identity = context.get("last_region_identity")
    focus = context.get("last_region")
    matched = [c for c in candidates if (
        isinstance(identity, dict) and all(k in identity for k in ("province", "city", "district"))
        and all(c.get(k) == identity[k] for k in ("province", "city", "district"))
    ) or (focus and focus == c.get("full_name"))]
    if len(matched) == 1:
        capture = _CAPTURE.get()
        if capture is not None:
            # An earlier API preflight may have observed the same ambiguity
            # before durable focus restoration. A later proven binding settles
            # that slot; do not keep stale evidence and ask again.
            capture[:] = [e for e in capture if e.get("token") != str(token)]
        return matched[0]
    capture = _CAPTURE.get()
    if capture is not None:
        capture.append({"slot": "region", "token": str(token), "source": "existing_region_binding_metadata",
                        "candidates": [region_identity(c) for c in candidates]})
    return None


def _description(region):
    if not region.get("city"):
        return "统计" + region["province"] + "全省学校，覆盖省内各市。"
    if not region.get("district"):
        return "只统计" + region["full_name"] + "内的学校。"
    return "只统计" + region["full_name"] + "这一行政区县内的学校。"


def attach(question, routed, context=None, *, evidence=None, now=None):
    """Attach choices only for one proven, result-affecting region slot.

    Empty results, year/field coverage, unsupported capabilities, and parser
    guesses without binding witnesses never produce interpretation choices.
    """
    events = evidence if evidence is not None else observations()
    candidate = routed.get("_selective_task_candidate")
    task_intent = (candidate or routed).get("intent")
    if routed.get("intent") == "clarify" and routed.get("reason") not in {
        "missing_city", "missing_region", "missing_region_longitudinal_region", "llm_requires_clarification",
        "region_scope_not_unique", "ambiguous_region",
        "scope_reference_not_unique",
        "UNSUPPORTED_OR_AMBIGUOUS_CITY",
    }:
        return routed
    if task_intent not in {"region_eval", "region_longitudinal"} and not (
        routed.get("intent") == "clarify" and routed.get("reason") in {"missing_city", "missing_region", "missing_region_longitudinal_region"}
    ):
        return routed
    if candidate and (len(candidate.get("regions") or []) != 1 or candidate.get("schools")):
        return routed
    usable = {}
    for event in events:
        token = event.get("token", "")
        if event.get("source") != "existing_region_binding_metadata" or not token or token not in question:
            continue
        if not token.endswith(("省", "市", "区", "县", "旗", "州", "盟")) and re.search(re.escape(token) + r"(?:省|市|区|县|旗|自治区)", question):
            continue  # An explicit level already settles a bare-name observation.
        if re.search(r"(?:排除|不含|不要|不包括|剔除)\s*$", question.split(token)[0]):
            continue
        usable[json.dumps(event, ensure_ascii=False, sort_keys=True)] = event
    if not usable:
        return routed
    unresolved = {"intent": "clarify", "reason": "selective_region_scope_required", "original": question,
                  "missing": ["地区存在多个实际绑定，请补充完整的省、市或区县名称。"]}
    if len(usable) != 1:
        return unresolved
    event = next(iter(usable.values()))
    if question.count(event["token"]) != 1:
        return unresolved
    candidates = event.get("candidates") or []
    if not 2 <= len(candidates) <= 3:
        return unresolved
    options = []
    for region in candidates:
        if not region.get("province") or not region.get("full_name"):
            return routed
        option_id = hashlib.sha256(json.dumps(region, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        options.append({"option_id": option_id, "label": region["full_name"], "difference": _description(region),
                        "constraint": region, "evidence_source": event["source"]})
    if len({o["option_id"] for o in options}) != len(options):
        return routed
    issued = time.time() if now is None else now
    spec = {"version": VERSION, "slot": "region", "token": event["token"],
            "original_question": question, "working_question": question, "options": options, "issued_at": issued,
            "expires_at": issued + TTL_SECONDS, "task_candidate": copy.deepcopy(candidate),
            "original_route": copy.deepcopy({k: v for k, v in routed.items() if k != "_selective_task_candidate"}),
            "resolved_constraints": {}, "unresolved_slots": ["region"]}
    out = {"intent": "clarify", "reason": "selective_region_ambiguity", "original": question,
           "missing": ["这个地区名称对应不同统计范围，请选择。"],
           "pending_context": copy.deepcopy(routed.get("pending_context") or {}),
           "selective_clarification": spec}
    return out


def public_spec(pending):
    spec = (pending.get("routed") or {}).get("selective_clarification") if pending else None
    if not spec:
        return None
    return {"version": VERSION, "pending_id": pending["pending_id"], "slot": spec["slot"],
            "original_question": spec["original_question"], "expires_at": spec["expires_at"],
            "unresolved_slots": spec["unresolved_slots"],
            "confirmed_slots": [{"slot": slot, "label": value["label"]} for slot, value in spec.get("confirmed_choices", {}).items()],
            "options": [{k: option[k] for k in ("option_id", "label", "difference")} for option in spec["options"]],
            "allow_amend": True, "allow_none": True, "allow_cancel": True}


def _amend_text(reply):
    text = str(reply.get("text") or "").strip()
    return re.sub(r"^(?:改成|改为|其实是|我要|是)", "", text).strip(" 。！!？?")


def bind_supplement(pending, reply):
    """Bind a novel explicit place using the EXISTING metadata resolver only.

    No model inference or task rewrite is accepted here. The trusted witness
    is passed internally; external reply dictionaries cannot supply it.
    """
    if not isinstance(reply, dict) or reply.get("action") != "amend" or not pending or reply.get("pending_id") != pending.get("pending_id"):
        return None
    spec = (pending.get("routed") or {}).get("selective_clarification") or {}
    if spec.get("slot") == "school":
        text = _amend_text(reply)
        if any(text in [o["label"]] + o.get("aliases", []) for o in spec.get("options", [])):
            return None
        if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z（）()·\s]{2,80}(?:小学|中学|学校|附中|高中|初中)", text):
            return None
        periods = (spec.get("task_candidate") or {}).get("periods") or []
        if len(periods) != 1:
            return None
        from scripts.school_engine import find_schools
        rows = find_schools(text, year=periods[0], limit=20)
        if len(rows) != 1 or not rows[0].get("school_name"):
            return None
        return {"source": "existing_school_binding_metadata", "text": text,
                "constraint": {"school": rows[0]["school_name"]}}
    if spec.get("slot") != "region":
        return None
    text = _amend_text(reply)
    if any(re.sub(r"\s+", "", text) in {re.sub(r"\s+", "", o["label"]), re.sub(r"\s+", "", o["constraint"]["name"])} for o in spec.get("options", [])):
        return None
    if not re.fullmatch(r"[\u4e00-\u9fff\s]{2,40}(?:省|市|区|县|旗|州|盟|自治区|特别行政区)", text):
        return None
    if re.search(r"不要|排除|不含|取消|均分|教学|年份|改口径", text):
        return None
    from scripts.region_engine import detect_region
    region = detect_region(text)
    if not region:
        return None
    return {"source": "existing_region_binding_metadata", "text": text, "constraint": region_identity(region)}


def resolve_reply(pending, reply, *, session_id, focus_task_id, focus_revision, now=None, flow_key=None, supplement=None):
    """Validate an opaque choice against the stored task, never interpret text as IDs."""
    if not isinstance(reply, dict) or set(reply) - {"pending_id", "action", "option_id", "text", "slot"}:
        raise ValueError("invalid_clarification_reply")
    if not pending or pending.get("session_id") != session_id or reply.get("pending_id") != pending.get("pending_id"):
        raise ValueError("clarification_not_in_session")
    spec = (pending.get("routed") or {}).get("selective_clarification")
    if not spec or spec.get("version") != VERSION:
        raise ValueError("clarification_not_selective")
    if spec.get("flow_key") is not None and spec["flow_key"] != flow_key:
        raise ValueError("clarification_flow_changed")
    if spec.get("submission"):
        raise ValueError("clarification_already_submitted")
    if pending.get("status") != "ACTIVE":
        raise ValueError("clarification_not_active")
    if pending.get("base_task_id") != focus_task_id or pending.get("base_revision") != focus_revision:
        raise ValueError("clarification_task_changed")
    action = reply.get("action")
    if action not in {"choose", "amend", "none", "cancel", "revise"}:
        raise ValueError("invalid_clarification_action")
    if action != "cancel" and (time.time() if now is None else now) >= spec["expires_at"]:
        raise ValueError("clarification_expired")
    if action in {"none", "cancel"}:
        if reply.get("option_id") or reply.get("text") or reply.get("slot"):
            raise ValueError("invalid_clarification_reply")
        return {"action": action, "spec": spec}
    if action == "revise":
        slot = reply.get("slot")
        previous = spec.get("confirmed_choices", {}).get(slot)
        if not previous or reply.get("option_id") or reply.get("text"):
            raise ValueError("clarification_revision_not_confirmed")
        new_spec = copy.deepcopy(spec)
        new_spec.pop("submission", None)
        new_spec.update(slot=slot, token=previous["label"], options=previous["options"])
        new_spec["unresolved_slots"] = list(dict.fromkeys([slot] + spec["unresolved_slots"]))
        return {"action": action, "spec": spec, "new_spec": new_spec}
    if reply.get("slot"):
        raise ValueError("invalid_clarification_reply")
    if action == "choose":
        if reply.get("text"):
            raise ValueError("invalid_clarification_reply")
        options = [o for o in spec["options"] if o["option_id"] == reply.get("option_id")]
    else:
        # Supplements/corrections bind only this slot. Novel identities need a
        # new binding witness, so keep this task pending instead of guessing.
        if reply.get("option_id"):
            raise ValueError("invalid_clarification_reply")
        text = _amend_text(reply)
        options = [o for o in spec["options"] if re.sub(r"\s+", "", text) in
                   {re.sub(r"\s+", "", value) for value in [o["label"], o["constraint"].get("name", "")] + o.get("aliases", []) if value}]
        if not options and supplement and supplement.get("source") == "existing_region_binding_metadata" and supplement.get("text") == text:
            region = supplement["constraint"]
            if region.get("province") and region.get("full_name"):
                options = [{"option_id": None, "label": region["full_name"], "constraint": region}]
        if not options and spec["slot"] == "school" and supplement and supplement.get("source") == "existing_school_binding_metadata" and supplement.get("text") == text:
            school = supplement.get("constraint", {}).get("school")
            if school:
                options = [{"option_id": None, "label": school, "constraint": {"school": school}}]
    if len(options) != 1:
        raise ValueError("clarification_supplement_not_unique")
    chosen = options[0]
    question = spec.get("working_question", spec["original_question"]).replace(spec["token"], chosen["label"], 1)
    candidate = copy.deepcopy(spec.get("task_candidate"))
    if candidate:
        if spec["slot"] == "region":
            candidate["regions"] = [chosen["label"]]
            candidate.setdefault("evidence", {})["region"] = chosen["label"]
        elif spec["slot"] == "school":
            candidate["schools"] = [chosen["constraint"]["school"]]
            candidate.setdefault("evidence", {})["school"] = chosen["constraint"]["school"]
        else:
            candidate[spec["slot"]] = chosen["constraint"][spec["slot"]]
        candidate["clarify_reason"] = None
        candidate["missing"] = []
    return {"action": action, "spec": spec, "question": question, "candidate": candidate,
            "slot": spec["slot"], "constraint": copy.deepcopy(chosen["constraint"]), "option_id": chosen["option_id"],
            "replacement": chosen["label"]}


def stopped(session_id, reason, *, pending=None):
    return {"clarify": True, "session_id": session_id, "business_status": "CLARIFY",
            "error_code": reason, "next_prompt": "此选择未执行。请使用当前任务的有效选项，或补充该字段、取消任务。",
            "clarification": public_spec(pending), "tool_calls": [], "fact_tool_calls": [],
            "execution_guard": {"model_parser_invoked": False, "fact_execution_started": False, "fact_tool_calls": 0}}
