"""User-confirmed bridge to the immutable official paired client.

Disabled by default. This module never imports the daily gateway, reads legacy
credentials, selects an active profile, changes jobs, or automatically retries.
The HTTP layer must additionally authenticate local, same-origin POST requests.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CLIENT_PATH = ROOT / "experiments/causal_boundary_pairwise_client_20261002_sol/paired_client.py"
CLIENT_SHA = "f3da915ac5a8e8263317fd5a4346dd073fc9773278520a567a32a616af086645"
CLIENT_FINGERPRINT = "781489EFB42B0F4099C8BEF9C8300DC272339AC13FEFB90C5A3AC2A573B76FFD"
JOB_ROOT = ROOT / "experiments/causal_boundary_pairwise_114_vs_117_job_20261002_sol"
RESULT_ROOT = ROOT / "experiments/causal_boundary_pairwise_114_vs_117_results_20261002_sol"
JOB_SHAS = {
    1: "9c59c004b33a91007102dfaa20ab24b26b07ba2f08e044ac3c189c21be90fae8",
    2: "46f7b1893b353721d76063193e3bdf219106214dd7a9ba25174bc072ce366e3e",
    3: "4f13409ed6d6cd81593c9361c39165684cc6a03998ee4dc850e2a29a4b34a949",
}
TOKEN_TTL_SECONDS = 300
_PUBLIC_FIELDS = ("id", "provider", "api_base", "model", "credential_revision")


class BridgeError(Exception):
    """Only a fixed local code is exposed to the HTTP caller."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _batch(value):
    if type(value) is not int or value not in JOB_SHAS:
        raise BridgeError("BATCH_INVALID")
    return value


def _profile_metadata(profile, profile_id):
    if not isinstance(profile, dict):
        raise BridgeError("PROFILE_UNAVAILABLE")
    metadata = {field: profile.get(field) for field in _PUBLIC_FIELDS}
    if metadata["id"] != profile_id or any(
        not isinstance(value, str) or not value or len(value) > 512
        for value in metadata.values()
    ):
        raise BridgeError("PROFILE_METADATA_INVALID")
    # Never infer official identity from a provider label or a URL substring.
    api_base = metadata["api_base"]
    try:
        parsed = urlsplit(api_base)
        valid_base = (
            api_base in ("https://api.deepseek.com", "https://api.deepseek.com/",
                         "https://api.deepseek.com/v1", "https://api.deepseek.com/v1/")
            and
            parsed.scheme == "https" and parsed.netloc == "api.deepseek.com"
            and parsed.hostname == "api.deepseek.com" and parsed.port is None
            and parsed.username is None and parsed.password is None
            and parsed.path in ("", "/", "/v1", "/v1/")
            and not parsed.query and not parsed.fragment
            and api_base == api_base.strip()
            and "\\" not in api_base and not any(ord(c) < 32 for c in api_base)
        )
    except ValueError:
        valid_base = False
    if not valid_base or metadata["model"] != "deepseek-flash":
        raise BridgeError("OFFICIAL_PROFILE_REQUIRED")
    if metadata["provider"] not in ("deepseek", "deepseek_official", "custom"):
        raise BridgeError("OFFICIAL_PROFILE_REQUIRED")
    # A revision is random public metadata, never a hash of the credential.
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", metadata["credential_revision"]):
        raise BridgeError("PROFILE_METADATA_INVALID")
    return metadata


def _fingerprint(metadata):
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _import_client():
    # Pin the source *before* exec_module, not only through its own manifest.
    if hashlib.sha256(CLIENT_PATH.read_bytes()).hexdigest() != CLIENT_SHA:
        raise BridgeError("FROZEN_CLIENT_CHANGED")
    spec = importlib.util.spec_from_file_location("teachereval_frozen_paired_bridge_client", CLIENT_PATH)
    if spec is None or spec.loader is None:
        raise BridgeError("FROZEN_CLIENT_UNAVAILABLE")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    if client.client_identity() != CLIENT_FINGERPRINT:
        raise BridgeError("FROZEN_CLIENT_CHANGED")
    return client


def _read_batch(batch):
    """Read only local completion metadata; never read response bodies or keys."""
    folder = RESULT_ROOT / f"batch_{batch}"
    summary = folder / "batch_summary.json"
    if not folder.exists():
        return {"status": "NOT_STARTED", "summary_sha256": None}
    if not summary.is_file():
        return {"status": "STARTED_NO_RETRY", "summary_sha256": None}
    try:
        raw = summary.read_bytes()
        if len(raw) > 512 * 1024:
            raise ValueError()
        data = json.loads(raw)
        if (data.get("batch") != batch or data.get("job_sha256") != JOB_SHAS[batch]
                or data.get("status") not in ("COMPLETED", "STOPPED")):
            raise ValueError()
        return {"status": data["status"], "summary_sha256": hashlib.sha256(raw).hexdigest()}
    except Exception:
        return {"status": "RESULT_INVALID_NO_RETRY", "summary_sha256": None}


class PairedExperimentBridge:
    def __init__(self, *, enabled=False, public_profile=None, load_profile=None,
                 client=None, clock=time.monotonic, summary_reader=None):
        # Client and summary injection are for offline tests; production uses pins.
        self._enabled = enabled is True
        self._public_profile = public_profile
        self._load_profile = load_profile
        self._client = client
        self._clock = clock
        self._summary_reader = summary_reader or _read_batch
        self._lock = threading.RLock()
        self._tokens = {}
        self._states = {}
        self._reviews = {}
        self._running = None
        self._worker_thread = None

    def _require_enabled(self):
        if not self._enabled:
            raise BridgeError("EXPERIMENT_BRIDGE_DISABLED_PENDING_REVIEW")
        if not callable(self._public_profile) or not callable(self._load_profile):
            raise BridgeError("SECURE_PROFILE_ACCESS_UNAVAILABLE")

    def _get_client(self):
        if self._client is None:
            try:
                self._client = _import_client()
            except BridgeError:
                raise
            except BaseException:
                raise BridgeError("FROZEN_CLIENT_UNAVAILABLE") from None
        return self._client

    def _summary(self, batch):
        try:
            result = self._summary_reader(batch)
            allowed = {"NOT_STARTED", "STARTED_NO_RETRY", "COMPLETED", "STOPPED", "RESULT_INVALID_NO_RETRY"}
            if result.get("status") not in allowed:
                raise ValueError()
            digest = result.get("summary_sha256")
            if result["status"] == "COMPLETED" and not (
                isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError()
            return {"status": result["status"], "summary_sha256": digest}
        except Exception:
            return {"status": "RESULT_INVALID_NO_RETRY", "summary_sha256": None}

    def _gate(self, batch):
        if self._running is not None:
            raise BridgeError("BATCH_ALREADY_RUNNING")
        if batch in self._states or self._summary(batch)["status"] != "NOT_STARTED":
            raise BridgeError("BATCH_ALREADY_STARTED_NO_RETRY")
        if batch > 1:
            prior = self._summary(batch - 1)
            if prior["status"] != "COMPLETED":
                raise BridgeError("PREVIOUS_BATCH_NOT_COMPLETED")
            if self._reviews.get(batch - 1) != prior["summary_sha256"]:
                raise BridgeError("PREVIOUS_BATCH_REVIEW_REQUIRED")

    def status(self):
        with self._lock:
            batches = []
            for batch in JOB_SHAS:
                summary = self._summary(batch)
                local = self._states.get(batch, {})
                batches.append({"batch": batch, "status": local.get("status", summary["status"]),
                                "review_acknowledged": summary["status"] == "COMPLETED"
                                and self._reviews.get(batch) == summary["summary_sha256"],
                                "completed_calls": local.get("completed_calls", 0),
                                "error_code": local.get("error_code")})
            return {"enabled": self._enabled, "running_batch": self._running,
                    "batches": batches, "requires_personal_confirmation": True,
                    "automatic_retries": 0, "automatic_next_batch": False}

    def prepare(self, batch, profile_id):
        batch = _batch(batch)
        if not isinstance(profile_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", profile_id):
            raise BridgeError("PROFILE_ID_INVALID")
        with self._lock:
            self._require_enabled()
            self._gate(batch)
            try:
                metadata = _profile_metadata(self._public_profile(profile_id), profile_id)
                client = self._get_client()
                path = JOB_ROOT / f"job_batch_{batch}.json"
                job, digest = client.load_job(path)
                if digest != JOB_SHAS[batch] or job.get("batch") != batch or len(job.get("calls", [])) != 28:
                    raise BridgeError("FROZEN_JOB_CHANGED")
            except BridgeError:
                raise
            except BaseException:
                raise BridgeError("OFFLINE_PREPARATION_FAILED") from None
            now = self._clock()
            self._tokens = {token: item for token, item in self._tokens.items()
                            if item["expires"] > now and item["batch"] != batch}
            if len(self._tokens) >= 16:
                raise BridgeError("TOO_MANY_PREPARATIONS")
            token = secrets.token_urlsafe(32)
            self._tokens[token] = {"batch": batch, "profile_id": profile_id, "path": path,
                                   "job_sha256": digest, "profile_fingerprint": _fingerprint(metadata),
                                   "expires": now + TOKEN_TTL_SECONDS}
            # No user-controlled label/exception/upstream response is reflected.
            return {"confirmation_token": token, "expires_in_seconds": TOKEN_TTL_SECONDS,
                    "batch": batch, "batch_count": 3, "pairs": 14, "planned_calls": 28,
                    "profile_id": profile_id, "endpoint": "https://api.deepseek.com/chat/completions",
                    "model": "deepseek-flash", "job_sha256": digest,
                    "client_source_fingerprint": CLIENT_FINGERPRINT,
                    "charges_api_usage": True, "requires_personal_confirmation": True,
                    "automatic_retries": 0, "automatic_next_batch": False}

    def start(self, token, *, confirmed=False):
        # Missing/false/string confirmation must not consume a token or load keys.
        if confirmed is not True:
            raise BridgeError("PERSONAL_CONFIRMATION_REQUIRED")
        if not isinstance(token, str) or len(token) > 128:
            raise BridgeError("CONFIRMATION_TOKEN_INVALID")
        with self._lock:
            self._require_enabled()
            item = self._tokens.pop(token, None)
            if item is None or item["expires"] <= self._clock():
                raise BridgeError("CONFIRMATION_TOKEN_INVALID_OR_EXPIRED")
            batch = item["batch"]
            self._gate(batch)
            try:
                metadata = _profile_metadata(self._public_profile(item["profile_id"]), item["profile_id"])
                if _fingerprint(metadata) != item["profile_fingerprint"]:
                    raise BridgeError("PROFILE_CHANGED_AFTER_PREPARATION")
                client = self._get_client()
                job, digest = client.load_job(item["path"])
                if digest != item["job_sha256"] or job.get("batch") != batch:
                    raise BridgeError("FROZEN_JOB_CHANGED")
                private = self._load_profile(item["profile_id"])
                private_metadata = _profile_metadata(private, item["profile_id"])
                if _fingerprint(private_metadata) != item["profile_fingerprint"]:
                    raise BridgeError("PROFILE_CHANGED_AFTER_PREPARATION")
                key = private.get("api_key")
                if not isinstance(key, str) or not key or len(key) > 4096:
                    raise BridgeError("SECURE_CREDENTIAL_UNAVAILABLE")
                del private
            except BridgeError:
                raise
            except BaseException:
                raise BridgeError("RUN_AUTHORIZATION_FAILED") from None
            self._running = batch
            self._states[batch] = {"status": "RUNNING", "completed_calls": 0}
            # No credential is placed on the bridge object, status, or token.
            try:
                worker = threading.Thread(target=self._execute, args=(client, key, item),
                                          name=f"paired-batch-{batch}", daemon=True)
                self._worker_thread = worker
                worker.start()
            except BaseException:
                self._states[batch] = {"status": "FAILED_NO_RETRY", "completed_calls": 0,
                                       "error_code": "BACKGROUND_START_FAILED"}
                self._running = None
                raise BridgeError("BACKGROUND_START_FAILED") from None
            return {"status": "RUNNING", "batch": batch, "planned_calls": 28,
                    "automatic_retries": 0, "automatic_next_batch": False}

    def _execute(self, client, key, item):
        batch = item["batch"]
        def progress(_event):
            # Do not expose response/usage/id/error text to the website.
            with self._lock:
                current = self._states[batch]
                current["completed_calls"] = min(28, current["completed_calls"] + 1)
        try:
            completed = client.execute_job(key, item["path"], item["job_sha256"], progress=progress)
            with self._lock:
                self._states[batch]["status"] = "COMPLETED" if completed is True else "STOPPED_NO_RETRY"
        except BaseException:
            with self._lock:
                self._states[batch].update(status="FAILED_NO_RETRY", error_code="FROZEN_EXECUTION_FAILED")
        finally:
            key = None
            with self._lock:
                self._running = None

    def reviewack(self, batch, *, confirmed=False):
        batch = _batch(batch)
        if confirmed is not True:
            raise BridgeError("PERSONAL_REVIEW_CONFIRMATION_REQUIRED")
        with self._lock:
            self._require_enabled()
            if self._running is not None:
                raise BridgeError("BATCH_ALREADY_RUNNING")
            summary = self._summary(batch)
            if summary["status"] != "COMPLETED":
                raise BridgeError("REVIEW_REQUIRES_COMPLETED_BATCH")
            self._reviews[batch] = summary["summary_sha256"]
            return {"batch": batch, "review_acknowledged": True,
                    "summary_sha256": summary["summary_sha256"], "automatic_next_batch": False}
