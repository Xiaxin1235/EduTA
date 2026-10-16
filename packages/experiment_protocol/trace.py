from __future__ import annotations

import hashlib
from contextvars import ContextVar
import json
import os
from pathlib import Path
from typing import Any

_STAGE_EVENTS: ContextVar[list[dict[str, Any]] | None] = ContextVar("experiment_stage_events", default=None)

def begin_request_trace():
    return _STAGE_EVENTS.set([])

def end_request_trace(token) -> None:
    _STAGE_EVENTS.reset(token)

def stage_event(stage: str, **payload: Any) -> None:
    events = _STAGE_EVENTS.get()
    if events is not None:
        events.append({"stage": stage, **payload})

def request_events() -> list[dict[str, Any]]:
    return list(_STAGE_EVENTS.get() or ())

def save_request_trace(run_id: str, request_id: str, payload: dict[str, Any]) -> str:
    root = Path(os.environ["TEACHER_EVAL_EXPERIMENT_TRACE_DIR"]).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("isolated_trace_directory_required")
    run_key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    request_key = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:20]
    name = f"{run_key}_{request_key}.json"
    path = root / name

    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, default=str, indent=2)
    return name
