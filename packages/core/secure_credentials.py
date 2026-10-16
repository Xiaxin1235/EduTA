import ctypes
import base64
import json
import os
import tempfile
import threading
import uuid
from ctypes import wintypes
from pathlib import Path

class SecureCredentialError(Exception):
    pass

_FORMAT = "TeacherEval-DPAPI-v1"
_ENTROPY = b"TeacherEval/local-credential-store/v1"
_MAX_SIZE = 2 * 1024 * 1024
_LOCK = threading.RLock()

def storage_path() -> Path:
    configured = os.environ.get("TEACHER_EVAL_CREDENTIAL_STORE_PATH", "").strip()
    if configured:
        if not Path(configured).is_absolute():
            raise SecureCredentialError("credential_store_absolute_path_required")
        return Path(configured)
    from packages.core.frozen_paths import app_dir
    return Path(app_dir()) / "output" / "secure_config" / "llm_profiles.dpapi"

def _legacy_paths():

    from packages.core.frozen_paths import app_dir
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return (
        Path(appdata) / "TeacherEval" / "llm_config.json",
        Path(app_dir()) / "llm_config.json",
    )

class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

def _dpapi_libraries():
    if os.name != "nt":
        raise SecureCredentialError("secure_storage_windows_required")
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        common = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
                  ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                  wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        crypt32.CryptProtectData.argtypes = common
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                             *common[2:]]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        return crypt32, kernel32
    except Exception:
        raise SecureCredentialError("secure_storage_dpapi_unavailable") from None

def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf

def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    crypt32, kernel32 = _dpapi_libraries()
    source, source_buf = _blob(data)
    entropy, entropy_buf = _blob(_ENTROPY)
    output = _DataBlob()
    try:

        fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        description = None if decrypt else "TeacherEval local credentials"
        if not fn(ctypes.byref(source), description, ctypes.byref(entropy),
                  None, None, 0x1, ctypes.byref(output)):
            raise SecureCredentialError("secure_storage_decrypt_failed" if decrypt
                                        else "secure_storage_encrypt_failed")
        return ctypes.string_at(output.pbData, output.cbData)
    except SecureCredentialError:
        raise
    except Exception:
        raise SecureCredentialError("secure_storage_dpapi_failed") from None
    finally:
        ctypes.memset(source_buf, 0, len(data))
        ctypes.memset(entropy_buf, 0, len(_ENTROPY))
        if output.pbData:
            ctypes.memset(output.pbData, 0, output.cbData)
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))

def _validated_store(store):
    if not isinstance(store, dict) or not isinstance(store.get("profiles"), list):
        raise SecureCredentialError("secure_storage_invalid_store")
    active = store.get("active") or ""
    if not isinstance(active, str):
        raise SecureCredentialError("secure_storage_invalid_store")
    profiles, ids = [], set()
    for p in store["profiles"]:
        if not isinstance(p, dict):
            raise SecureCredentialError("secure_storage_invalid_store")
        fields = {}
        for name in ("id", "name", "provider", "api_base", "api_key", "model"):
            value = p.get(name) or ""
            if not isinstance(value, str):
                raise SecureCredentialError("secure_storage_invalid_store")
            fields[name] = value
        revision = p.get("credential_revision") or ""
        if not isinstance(revision, str):
            raise SecureCredentialError("secure_storage_invalid_store")
        fields["credential_revision"] = revision
        def normalized_key(value):
            for ch in ("\ufeff", "\u200b", "\u200c", "\u200d", "\xa0"):
                value = value.replace(ch, "")
            value = value.strip().strip("\"'").strip()
            if value.lower().startswith("bearer "):
                value = value[7:].strip()
            return "".join(value.split())
        candidates = {fields["api_key"], normalized_key(fields["api_key"])} - {""}
        for name in ("id", "name", "provider", "api_base", "model"):
            value = fields[name]
            if any(secret in value or secret in normalized_key(value) for secret in candidates):
                raise SecureCredentialError("secure_storage_secret_in_metadata")

        from urllib.parse import urlsplit
        try:
            endpoint = urlsplit(fields["api_base"])
            if endpoint.username is not None or endpoint.password is not None or endpoint.query or endpoint.fragment:
                raise SecureCredentialError("secure_storage_unsafe_endpoint")
        except ValueError:
            raise SecureCredentialError("secure_storage_unsafe_endpoint") from None
        if not fields["id"] or fields["id"] in ids:
            raise SecureCredentialError("secure_storage_invalid_store")
        ids.add(fields["id"])
        profiles.append(fields)
    if active and active not in ids:
        raise SecureCredentialError("secure_storage_invalid_store")
    return {"active": active, "profiles": profiles}

def _metadata(store):
    return {"active": store["active"], "profiles": [
        {**{k: p[k] for k in ("id", "name", "provider", "api_base", "model",
                              "credential_revision")},
         "has_api_key": bool(p["api_key"])} for p in store["profiles"]]}

def _read_envelope(path):
    target = Path(path) if path is not None else storage_path()
    with _LOCK:
        try:
            if not target.is_file():
                return None
            if target.stat().st_size > _MAX_SIZE:
                raise SecureCredentialError("secure_storage_invalid_file")
            envelope = json.loads(target.read_bytes().decode("utf-8"))
        except SecureCredentialError:
            raise
        except Exception:
            raise SecureCredentialError("secure_storage_read_failed") from None
        if not isinstance(envelope, dict) or envelope.get("format") != _FORMAT:
            raise SecureCredentialError("secure_storage_invalid_file")
        return envelope

def _public_metadata(envelope):
    public = envelope.get("public")
    if not isinstance(public, dict) or set(public) != {"active", "profiles"}:
        raise SecureCredentialError("secure_storage_invalid_file")
    profiles = public.get("profiles")
    if not isinstance(profiles, list):
        raise SecureCredentialError("secure_storage_invalid_file")
    expected = {"id", "name", "provider", "api_base", "model", "credential_revision", "has_api_key"}
    for p in profiles:
        if not isinstance(p, dict) or set(p) != expected or type(p["has_api_key"]) is not bool:
            raise SecureCredentialError("secure_storage_invalid_file")
        if any(not isinstance(p[k], str) for k in expected - {"has_api_key"}):
            raise SecureCredentialError("secure_storage_invalid_file")
        try:
            uuid.UUID(p["credential_revision"])
        except (ValueError, AttributeError):
            raise SecureCredentialError("secure_storage_invalid_file") from None

    _validated_store({"active": public["active"], "profiles": [
        {**p, "api_key": ""} for p in profiles]})
    return {"active": public["active"], "profiles": [dict(p) for p in profiles]}

def public_store(*, path=None) -> dict:
    pass
    envelope = _read_envelope(path)
    if envelope is None:
        return {"active": "", "profiles": []}
    return _public_metadata(envelope)

def public_profile(profile_id: str, *, path=None) -> dict:
    if not isinstance(profile_id, str) or not profile_id:
        raise SecureCredentialError("secure_storage_profile_required")
    for profile in public_store(path=path)["profiles"]:
        if profile["id"] == profile_id:
            return dict(profile)
    raise SecureCredentialError("secure_storage_profile_not_found")

def load_store(*, path=None) -> dict:
    pass
    _dpapi_libraries()
    envelope = _read_envelope(path)
    if envelope is None:
        return {"active": "", "profiles": []}
    public = _public_metadata(envelope)
    try:
        encrypted = base64.b64decode(envelope["protected"], validate=True)
        decoded = _crypt(encrypted, decrypt=True)
        payload = json.loads(decoded.decode("utf-8"))
        normalized = _validated_store(payload["store"])
        if payload.get("public") != public or _metadata(normalized) != public:
            raise SecureCredentialError("secure_storage_metadata_mismatch")
        return normalized
    except SecureCredentialError:
        raise
    except Exception:
        raise SecureCredentialError("secure_storage_invalid_file") from None

def save_store(store: dict, *, path=None) -> None:
    pass
    normalized = _validated_store(store)
    for profile in normalized["profiles"]:
        profile["credential_revision"] = str(uuid.uuid4())
    public = _metadata(normalized)
    payload = json.dumps({"store": normalized, "public": public}, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_SIZE // 2:
        raise SecureCredentialError("secure_storage_invalid_store")
    encrypted = json.dumps({"format": _FORMAT, "public": public,
                            "protected": base64.b64encode(_crypt(payload, decrypt=False)).decode("ascii")},
                           ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    target = Path(path) if path is not None else storage_path()
    temp_name = None
    with _LOCK:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent,
                                             prefix=".llm_profiles-", suffix=".dpapi",
                                             delete=False) as handle:
                temp_name = handle.name
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
            temp_name = None
        except Exception:
            raise SecureCredentialError("secure_storage_write_failed") from None
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

def storage_status(*, path=None) -> dict:
    pass
    target = Path(path) if path is not None else storage_path()
    try:
        _dpapi_libraries()

        _crypt(b"TeacherEval DPAPI availability probe (not credentials)", decrypt=False)
        available, error = True, ""
    except SecureCredentialError as exc:
        available, error = False, str(exc)
    encrypted_exists = target.is_file()
    legacy_exists = any(os.path.isfile(p) for p in _legacy_paths())
    return {
        "secure_storage": "windows_dpapi_current_user",
        "secure_storage_available": available,
        "secure_config_exists": encrypted_exists,
        "secure_storage_error": error,
        "legacy_config_detected": legacy_exists,
        "needs_resave": legacy_exists and not encrypted_exists,
    }

def load_profile(profile_id: str, *, path=None) -> dict:
    pass
    if not isinstance(profile_id, str) or not profile_id:
        raise SecureCredentialError("secure_storage_profile_required")
    store = load_store(path=path)
    for profile in store["profiles"]:
        if profile["id"] == profile_id:
            return dict(profile)
    raise SecureCredentialError("secure_storage_profile_not_found")
