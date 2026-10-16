from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional

from .schema import AnalysisContract

ROOT = Path(__file__).resolve().parents[2]

def automatic_store_candidates() -> tuple[tuple[Path, str], ...]:
    pass
    if not getattr(sys, "frozen", False):
        return ((ROOT / "data" / "runtime" / "analysis_contracts.sqlite", "repository_runtime"),)

    raw_candidates = (
        (os.getenv("LOCALAPPDATA"), "local_appdata"),
        (os.getenv("APPDATA"), "roaming_appdata"),
        (str(Path.home() / ".TeacherEval"), "user_profile"),
        (str(Path(sys.executable).resolve().parent / "output" / "runtime"), "executable_output"),
    )
    candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for base, source in raw_candidates:
        if not base:
            continue
        directory = Path(base)
        if source in {"local_appdata", "roaming_appdata"}:
            directory = directory / "TeacherEval"
        path = directory / "analysis_contracts.sqlite"
        key = os.path.normcase(os.path.abspath(str(path)))
        if key not in seen:
            seen.add(key)
            candidates.append((path, source))
    return tuple(candidates)

def default_store_path() -> Path:
    configured = os.getenv("TEACHER_EVAL_CONTRACT_DB")
    if configured:
        return Path(configured)
    return automatic_store_candidates()[0][0]

def store_path_manifest() -> dict:
    pass
    configured = os.getenv("TEACHER_EVAL_CONTRACT_DB")
    if configured:
        candidates = ((Path(configured), "environment_override"),)
    else:
        candidates = automatic_store_candidates()
    return {
        "configured_by_environment": bool(configured),
        "preferred_path": str(candidates[0][0]),
        "preferred_source": candidates[0][1],
        "candidates": [
            {"path": str(path), "source": source}
            for path, source in candidates
        ],
    }

def contract_store_status() -> dict:
    pass
    manifest = store_path_manifest()
    store = ContractStore()
    try:
        with store._session() as conn:
            conn.execute("SELECT 1").fetchone()
        return {
            **manifest,
            "status": "available",
            "error_code": None,
            "active_path": str(store.path),
            "active_source": store.path_source,
            "fallback_used": str(store.path) != manifest["preferred_path"],
        }
    except Exception as exc:
        return {
            **manifest,
            "status": "unavailable",
            "error_code": "contract_store_unavailable",
            "active_path": str(store.path),
            "active_source": store.path_source,
            "error": str(exc),
            "fallback_used": False,
        }

class ContractStoreUnavailable(OSError):
    pass

class StaleBaseRevision(RuntimeError):
    def __init__(self, requested: int, current: int, *, attempt_id: Optional[str] = None):
        self.requested = requested
        self.current = current
        self.attempt_id = attempt_id
        super().__init__(f"stale_base_revision:{requested}!={current}")

class ContractStore:
    _FOCUS_PREDICATE = (
        "((business_status='ANSWER' AND verification_status='PASS') "
        "OR (business_status='NO_DATA' AND verification_status IN ('PASS','NOT_APPLICABLE')))"
    )

    @staticmethod
    def focus_eligible(business_status: str, verification_status: str) -> bool:
        return (
            business_status == "ANSWER" and verification_status == "PASS"
        ) or (
            business_status == "NO_DATA" and verification_status in {"PASS", "NOT_APPLICABLE"}
        )

    def __init__(self, path: Optional[Path] = None):
        configured = os.getenv("TEACHER_EVAL_CONTRACT_DB")
        if path is not None:
            self._candidates = ((Path(path), "explicit_argument"),)
            self._allow_fallback = False
        elif configured:
            self._candidates = ((Path(configured), "environment_override"),)
            self._allow_fallback = False
        else:
            self._candidates = automatic_store_candidates()
            self._allow_fallback = True
        self.path, self.path_source = self._candidates[0]

    @staticmethod
    def _connect_at(path: Path) -> sqlite3.Connection:
        conn: Optional[sqlite3.Connection] = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS task_revision (
                       session_id TEXT NOT NULL,
                       task_id TEXT NOT NULL,
                       revision INTEGER NOT NULL,
                       turn_id TEXT NOT NULL,
                       status TEXT NOT NULL,
                       contract_json TEXT NOT NULL,
                       execution_id TEXT,
                       business_status TEXT,
                       verification_status TEXT,
                       failure_json TEXT,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       PRIMARY KEY (task_id, revision)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS clarification_pending (
                       pending_id TEXT NOT NULL PRIMARY KEY,
                       session_id TEXT NOT NULL,
                       turn_id TEXT NOT NULL,
                       reason TEXT,
                       missing_json TEXT NOT NULL,
                       original_question TEXT NOT NULL,
                       routed_json TEXT NOT NULL,
                       context_json TEXT NOT NULL,
                       provenance_json TEXT NOT NULL,
                       base_task_id TEXT,
                       base_revision INTEGER,
                       parent_pending_id TEXT,
                       status TEXT NOT NULL,
                       resolution TEXT,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS clarification_pending_one_active "
                "ON clarification_pending(session_id) WHERE status='ACTIVE'"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS revision_conflict (
                       attempt_id TEXT NOT NULL PRIMARY KEY,
                       request_id TEXT NOT NULL,
                       session_id TEXT NOT NULL,
                       task_id TEXT,
                       requested_base_revision INTEGER NOT NULL,
                       current_revision INTEGER NOT NULL,
                       outcome TEXT NOT NULL,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(task_revision)")}
            if "business_status" not in columns:
                conn.execute("ALTER TABLE task_revision ADD COLUMN business_status TEXT")
            return conn
        except Exception:
            if conn is not None:
                conn.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        errors: list[str] = []
        for candidate, source in self._candidates:
            try:
                conn = self._connect_at(candidate)
                self.path = candidate
                self.path_source = source
                self._candidates = ((candidate, source),) + tuple(
                    item for item in self._candidates if item[0] != candidate
                )
                return conn
            except (OSError, sqlite3.OperationalError) as exc:
                errors.append(f"{source}:{candidate}:{exc}")
                if not self._allow_fallback:
                    break
        raise ContractStoreUnavailable(
            "contract_store_unavailable; attempts=" + " | ".join(errors)
        )

    @contextmanager
    def _session(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def latest(self, session_id: str) -> Optional[AnalysisContract]:
        with self._session() as conn:
            row = conn.execute(
                "SELECT contract_json FROM task_revision WHERE session_id=? AND status='COMMITTED' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return AnalysisContract.from_dict(json.loads(row[0])) if row else None

    def latest_focus(self, session_id: str) -> Optional[AnalysisContract]:
        pass
        with self._session() as conn:
            row = conn.execute(
                "SELECT contract_json FROM task_revision "
                "WHERE session_id=? AND status='COMMITTED' "
                f"AND {self._FOCUS_PREDICATE} "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return AnalysisContract.from_dict(json.loads(row[0])) if row else None

    def session_state(self, session_id: str) -> Dict[str, Any]:
        pass
        with self._session() as conn:
            conn.execute("BEGIN")
            head = conn.execute(
                "SELECT contract_json FROM task_revision WHERE session_id=? AND status='COMMITTED' "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1", (session_id,),
            ).fetchone()
            focus = conn.execute(
                "SELECT contract_json FROM task_revision WHERE session_id=? AND status='COMMITTED' "
                f"AND {self._FOCUS_PREDICATE} ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            pending = conn.execute(
                "SELECT * FROM clarification_pending WHERE session_id=? AND status='ACTIVE'",
                (session_id,),
            ).fetchone()
        return {
            "head": AnalysisContract.from_dict(json.loads(head[0])) if head else None,
            "focus": AnalysisContract.from_dict(json.loads(focus[0])) if focus else None,
            "pending": self._pending_from_row(pending) if pending else None,
        }

    @staticmethod
    def _check_base(conn: sqlite3.Connection, session_id: str, expected: Optional[int]) -> None:
        if expected is None:
            return
        row = conn.execute(
            "SELECT revision FROM task_revision WHERE session_id=? AND status='COMMITTED' "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1", (session_id,),
        ).fetchone()
        current = int(row[0]) if row else 0
        if current != expected:
            raise StaleBaseRevision(expected, current)

    def revision_for_restore(
        self,
        session_id: str,
        task_id: str,
        revision: int,
    ) -> AnalysisContract:
        pass
        with self._session() as conn:
            row = conn.execute(
                "SELECT contract_json,business_status,verification_status FROM task_revision "
                "WHERE session_id=? AND task_id=? AND revision=? AND status='COMMITTED'",
                (session_id, task_id, revision),
            ).fetchone()
        if not row:
            raise ValueError(f"restore_revision_not_found:{revision}")
        restorable = self.focus_eligible(row["business_status"], row["verification_status"])
        if not restorable:
            raise ValueError(f"restore_revision_not_trusted:{revision}")
        return AnalysisContract.from_dict(json.loads(row["contract_json"]))

    def revision_history(self, session_id: str, task_id: Optional[str] = None) -> list[Dict[str, Any]]:
        params: tuple[Any, ...]
        where = "session_id=?"
        params = (session_id,)
        if task_id:
            where += " AND task_id=?"
            params = (session_id, task_id)
        with self._session() as conn:
            rows = conn.execute(
                "SELECT task_id,revision,business_status,verification_status,contract_json,created_at "
                f"FROM task_revision WHERE {where} AND status='COMMITTED' "
                "ORDER BY revision ASC,rowid ASC",
                params,
            ).fetchall()
        history = []
        for row in rows:
            contract = AnalysisContract.from_dict(json.loads(row["contract_json"]))
            history.append({
                "task_id": row["task_id"],
                "revision": int(row["revision"]),
                "parent_revision": contract.parent_revision,
                "revision_action": contract.revision_action,
                "restored_from_revision": contract.restored_from_revision,
                "business_status": row["business_status"],
                "verification_status": row["verification_status"],
                "operation": contract.operation,
                "entity_type": contract.entity_type,
                "entity_id": contract.entity_id,
                "secondary_entity_id": contract.secondary_entity_id,
                "period": contract.period,
                "secondary_period": contract.secondary_period,
                "temporal_mode": contract.temporal_mode,
                "dimension": contract.dimension,
                "statistic": contract.statistic,
                "target_rank": contract.target_rank,
                "target_top_n": contract.target_top_n,
                "ranking_spec": contract.ranking_spec,
                "aggregation": contract.aggregation,
                "school_filter": contract.school_filter,
                "school_id": contract.school_id,
                "secondary_school_id": contract.secondary_school_id,
                "metric_key": contract.metric_key,
                "restorable": self.focus_eligible(row["business_status"], row["verification_status"]),
                "created_at": row["created_at"],
            })
        return history

    @staticmethod
    def _pending_from_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "pending_id": row["pending_id"],
            "session_id": row["session_id"],
            "turn_id": row["turn_id"],
            "reason": row["reason"],
            "missing": json.loads(row["missing_json"] or "[]"),
            "original_question": row["original_question"],
            "routed": json.loads(row["routed_json"] or "{}"),
            "context": json.loads(row["context_json"] or "{}"),
            "provenance": json.loads(row["provenance_json"] or "{}"),
            "base_task_id": row["base_task_id"],
            "base_revision": row["base_revision"],
            "parent_pending_id": row["parent_pending_id"],
            "status": row["status"],
            "resolution": row["resolution"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def active_pending(self, session_id: str) -> Optional[Dict[str, Any]]:
        pass
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM clarification_pending WHERE session_id=? AND status='ACTIVE' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return self._pending_from_row(row) if row else None

    def create_pending(
        self,
        *,
        session_id: str,
        reason: Optional[str],
        missing: list[str],
        original_question: str,
        routed: Dict[str, Any],
        context: Dict[str, Any],
        provenance: Dict[str, Any],
        base_task_id: Optional[str],
        base_revision: Optional[int],
        turn_id: Optional[str] = None,
        expected_head_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        pass
        pending_id = str(uuid.uuid4())
        turn_id = turn_id or str(uuid.uuid4())
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._check_base(conn, session_id, expected_head_revision)
            previous = conn.execute(
                "SELECT pending_id FROM clarification_pending "
                "WHERE session_id=? AND status='ACTIVE' ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            parent_pending_id = previous[0] if previous else None
            if parent_pending_id:
                conn.execute(
                    "UPDATE clarification_pending SET status='SUPERSEDED',resolution=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE pending_id=? AND status='ACTIVE'",
                    (f"superseded_by:{pending_id}", parent_pending_id),
                )
            conn.execute(
                "INSERT INTO clarification_pending("
                "pending_id,session_id,turn_id,reason,missing_json,original_question,routed_json,"
                "context_json,provenance_json,base_task_id,base_revision,parent_pending_id,status"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pending_id,
                    session_id,
                    turn_id,
                    reason,
                    json.dumps(missing, ensure_ascii=False),
                    original_question,
                    json.dumps(routed, ensure_ascii=False, sort_keys=True),
                    json.dumps(context, ensure_ascii=False, sort_keys=True),
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                    base_task_id,
                    base_revision,
                    parent_pending_id,
                    "ACTIVE",
                ),
            )
            row = conn.execute(
                "SELECT * FROM clarification_pending WHERE pending_id=?",
                (pending_id,),
            ).fetchone()
        return self._pending_from_row(row)

    def claim_selective_reply(self, session_id: str, reply: Dict[str, Any], *, request_id: str,
                              expected_head_revision: Optional[int] = None, flow_key=None, supplement=None) -> Dict[str, Any]:
        pass
        from packages.semantic_query.selective_clarification import resolve_reply
        import hashlib
        if not isinstance(reply, dict):
            raise ValueError("invalid_clarification_reply")
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._check_base(conn, session_id, expected_head_revision)
            row = conn.execute("SELECT * FROM clarification_pending WHERE pending_id=? AND session_id=?",
                               (reply.get("pending_id"), session_id)).fetchone()
            pending = self._pending_from_row(row) if row else None
            focus_row = conn.execute(
                "SELECT contract_json FROM task_revision WHERE session_id=? AND status='COMMITTED' "
                f"AND {self._FOCUS_PREDICATE} ORDER BY created_at DESC,rowid DESC LIMIT 1", (session_id,),
            ).fetchone()
            focus = AnalysisContract.from_dict(json.loads(focus_row[0])) if focus_row else None
            resolution = resolve_reply(pending, reply, session_id=session_id,
                                       focus_task_id=focus.task_id if focus else None,
                                       focus_revision=focus.revision if focus else None, flow_key=flow_key, supplement=supplement)
            resolution["pending"] = pending
            if resolution["action"] in {"choose", "amend", "cancel", "revise"}:
                routed = pending["routed"]
                if resolution["action"] in {"choose", "amend"}:
                    routed["selective_clarification"]["resolved_constraints"][resolution["slot"]] = resolution["constraint"]
                    routed["selective_clarification"]["unresolved_slots"] = [s for s in routed["selective_clarification"]["unresolved_slots"] if s != resolution["slot"]]
                routed["selective_clarification"]["submission"] = {
                    "request_id": request_id,
                    "digest": hashlib.sha256(json.dumps(reply, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                    "action": resolution["action"],
                }
                conn.execute("UPDATE clarification_pending SET routed_json=?,updated_at=CURRENT_TIMESTAMP WHERE pending_id=?",
                             (json.dumps(routed, ensure_ascii=False, sort_keys=True), pending["pending_id"]))
                if resolution["action"] == "cancel":
                    conn.execute("UPDATE clarification_pending SET status='CANCELLED',resolution='user_cancelled' WHERE pending_id=?",
                                 (pending["pending_id"],))
            return resolution

    def close_pending(
        self, pending_id: str, *, status: str, resolution: str,
        expected_head_revision: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        if status not in {"RESOLVED", "CANCELLED", "SUPERSEDED"}:
            raise ValueError(f"invalid_pending_status:{status}")
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                "SELECT session_id FROM clarification_pending WHERE pending_id=?", (pending_id,),
            ).fetchone()
            if pending is not None:
                self._check_base(conn, pending["session_id"], expected_head_revision)
            conn.execute(
                "UPDATE clarification_pending SET status=?,resolution=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE pending_id=? AND status='ACTIVE'",
                (status, resolution, pending_id),
            )
            row = conn.execute(
                "SELECT * FROM clarification_pending WHERE pending_id=?",
                (pending_id,),
            ).fetchone()
        return self._pending_from_row(row) if row else None

    def commit(self, contract: AnalysisContract, *, base_revision: Optional[int]) -> AnalysisContract:
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT revision FROM task_revision WHERE session_id=? "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (contract.session_id,),
            ).fetchone()
            current = int(row[0]) if row else 0
            if base_revision is not None and current != base_revision:
                raise StaleBaseRevision(base_revision, current)
            committed = replace(
                contract,
                revision=current + 1,
                parent_revision=current or None,
            )
            committed.validate()
            conn.execute(
                "INSERT INTO task_revision(session_id,task_id,revision,turn_id,status,contract_json) "
                "VALUES(?,?,?,?,?,?)",
                (
                    committed.session_id,
                    committed.task_id,
                    committed.revision,
                    committed.turn_id,
                    "COMMITTED",
                    json.dumps(committed.to_dict(), ensure_ascii=False, sort_keys=True),
                ),
            )
        return committed

    def record_revision_conflict(
        self,
        *,
        request_id: str,
        session_id: str,
        task_id: Optional[str],
        requested_base_revision: int,
        current_revision: int,
    ) -> str:
        attempt_id = str(uuid.uuid4())
        with self._session() as conn:
            conn.execute(
                "INSERT INTO revision_conflict(attempt_id,request_id,session_id,task_id,"
                "requested_base_revision,current_revision,outcome) VALUES(?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    request_id,
                    session_id,
                    task_id,
                    requested_base_revision,
                    current_revision,
                    "REJECTED_STALE_BASE",
                ),
            )
        return attempt_id

    def trusted(self, task_id: str, revision: int) -> AnalysisContract:
        with self._session() as conn:
            row = conn.execute(
                "SELECT contract_json FROM task_revision WHERE task_id=? AND revision=? AND status='COMMITTED'",
                (task_id, revision),
            ).fetchone()
        if not row:
            raise ValueError("trusted_contract_not_found")
        return AnalysisContract.from_dict(json.loads(row[0]))

    def record_execution(
        self,
        task_id: str,
        revision: int,
        *,
        execution_id: Optional[str],
        business_status: str,
        verification_status: str,
        failures: tuple[str, ...] = (),
        pending_id: Optional[str] = None,
        pending_status: Optional[str] = None,
        pending_resolution: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if pending_id is not None and pending_status not in {"RESOLVED", "SUPERSEDED"}:
            raise ValueError("invalid_execution_pending_status")
        pending_row = None
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            revision_row = conn.execute(
                "SELECT session_id FROM task_revision WHERE task_id=? AND revision=? AND status='COMMITTED'",
                (task_id, revision),
            ).fetchone()
            if revision_row is None:
                raise ValueError("execution_revision_not_found")
            conn.execute(
                "UPDATE task_revision SET execution_id=?,business_status=?,verification_status=?,failure_json=? "
                "WHERE task_id=? AND revision=?",
                (
                    execution_id,
                    business_status,
                    verification_status,
                    json.dumps(list(failures), ensure_ascii=False),
                    task_id,
                    revision,
                ),
            )
            if pending_id is not None and self.focus_eligible(business_status, verification_status):

                conn.execute(
                    "UPDATE clarification_pending SET status=?,resolution=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE pending_id=? AND session_id=? AND status='ACTIVE'",
                    (pending_status, pending_resolution, pending_id, revision_row["session_id"]),
                )
                pending_row = conn.execute(
                    "SELECT * FROM clarification_pending WHERE pending_id=? AND session_id=?",
                    (pending_id, revision_row["session_id"]),
                ).fetchone()
        return self._pending_from_row(pending_row) if pending_row else None
