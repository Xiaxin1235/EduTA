import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from packages.core import secure_credentials

try:
    import openai
except ImportError:
    openai = None
try:
    import httpx
except ImportError:
    httpx = None

class GatewayError(Exception):
    pass

    def __init__(self, msg, provider=None, cause=None):
        super().__init__(msg)
        self.provider = provider
        self.cause = cause

class GatewayUnavailable(GatewayError):
    pass

class GatewayTLSVerificationError(GatewayError):
    pass

class GatewayTransientError(GatewayError):
    pass

class GatewayConnectError(GatewayTransientError):
    pass

def _guard_experiment_outbound(entry_point: str) -> None:
    pass
    from packages.experiment_protocol import (
        experiment_allows_llm_entry_point,
        experiment_blocks_llm_outbound,
        record_llm_blocked,
    )

    if experiment_blocks_llm_outbound() or not experiment_allows_llm_entry_point(entry_point):
        record_llm_blocked(entry_point)
        raise GatewayUnavailable("configured_experiment_mode_disallows_llm_entry_point")

def _record_experiment_outbound(entry_point: str) -> None:
    from packages.experiment_protocol import record_llm_outbound

    record_llm_outbound(entry_point)

_CONNECTION_ERROR_MARKERS = (
    "remotedisconnected",
    "connectionreseterror",
    "connectionabortederror",
    "connectionerror",
    "incompleteread",
    "badstatusline",
    "connection reset",
    "remote end closed",
    "connection aborted",
    "服务器中断连接",
)

def _is_connection_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if any(marker in name for marker in ("remotedisconnected", "connectionreset",
                                         "connectionaborted", "incompleteread",
                                         "badstatusline", "connectionerror")):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _CONNECTION_ERROR_MARKERS)

_SOCKET_CONNECT_MARKERS = (
    "winerror 10013",
    "winerror 10049",
    "winerror 10061",
    "winerror 10064",
    "winerror 10065",
    "connection refused",
    "actively refused",
    "getaddrinfo failed",
    "no route to host",
    "network is unreachable",
    "errno 111",
    "errno 113",
    "errno 101",
    "errno 13]",
)

def _is_socket_connect_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException):
        text += " " + str(reason).lower()
    return any(marker in text for marker in _SOCKET_CONNECT_MARKERS)

_FAKE_IP_NETWORKS = ("198.18.0.0/15", "192.0.2.0/24", "28.0.0.0/8")

def _fakeip_hint(url: str) -> str:
    pass
    try:
        import ipaddress
        import socket
        from urllib.parse import urlsplit
        host = urlsplit(url).hostname
        if not host:
            return ""
        for info in socket.getaddrinfo(host, None):
            try:
                ip = ipaddress.ip_address(info[4][0])
            except (ValueError, IndexError):
                continue
            for net in _FAKE_IP_NETWORKS:
                if ip in ipaddress.ip_network(net):
                    return (
                        f"；诊断提示：目标域名被解析到本机代理的 fake-ip 假地址段（{ip} ∈ {net}），"
                        "说明系统 DNS 已被 Clash/Vortex 类代理软件接管。请确认代理软件正在运行、"
                        "系统代理或 TUN 模式已生效；也可在代理规则中放行该网关域名，或暂时关闭系统代理后重试。"
                    )
    except Exception:
        pass
    return ""

DEFAULT_TIMEOUT_S = int(os.environ.get("TEACHER_EVAL_LLM_TIMEOUT_S", "180"))
MAX_TOKENS_CEILING = int(os.environ.get("TEACHER_EVAL_LLM_MAX_TOKENS_CEILING", "8192"))

@dataclass
class ChatRequest:
    provider: str
    task: str = "chat"
    model: Optional[str] = None
    messages: List[dict] = field(default_factory=list)
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout: int = DEFAULT_TIMEOUT_S
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    expected_profile_id: Optional[str] = None
    empty_retry_messages: Optional[List[dict]] = None
    max_attempts: int = 2
    max_retry_tokens: Optional[int] = None
    transport_attempts: int = 2
    single_endpoint: bool = False
    http_event_sink: object = None

def _builtin_providers_path():
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json")
    if os.path.isfile(here):
        return here
    try:
        from packages.core.frozen_paths import resource_root
        alt = os.path.join(resource_root(), "packages", "llm_gateway", "providers.json")
        if os.path.isfile(alt):
            return alt
    except Exception:
        pass
    return here

def user_config_path():
    return str(secure_credentials.storage_path())

def load_store() -> dict:
    try:
        return secure_credentials.load_store(path=user_config_path())
    except secure_credentials.SecureCredentialError as exc:
        raise GatewayUnavailable(str(exc)) from None

def _write_store(store: dict) -> None:
    try:
        secure_credentials.save_store(store, path=user_config_path())
    except secure_credentials.SecureCredentialError as exc:
        raise GatewayUnavailable(str(exc)) from None

def load_user_config() -> dict:
    pass
    store = load_store()
    profiles = store.get("profiles") or []
    if not profiles:
        return {}
    active = store.get("active")
    for p in profiles:
        if p.get("id") == active:
            return dict(p)
    return dict(profiles[0])

PRESETS = {

    "deepseek": {"model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY"},
    "qwen": {"model": "qwen-plus", "key_env": "QWEN_API_KEY"},
    "kimi": {"model": "moonshot-v1-8k", "key_env": "KIMI_API_KEY"},
    "openai": {"model": "gpt-4o-mini", "key_env": "OPENAI_API_KEY"},
}

def normalize_key(raw: str) -> str:
    pass
    if raw is None:
        return ""
    s = str(raw)
    for ch in ("\ufeff", "\u200b", "\u200c", "\u200d", "\xa0"):
        s = s.replace(ch, "")
    s = s.strip().strip("\"'").strip()
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    s = "".join(s.split())
    return s

def normalize_base(raw: str) -> str:
    pass
    if not raw:
        return ""
    s = str(raw).strip().strip("\"'").rstrip("/")
    s = s.replace("\\", "/")
    if s and not s.lower().startswith("http"):
        s = "".join(("https", "://", s))
    lower = s.lower()
    for suffix in ("/chat/completions", "/completions", "/models"):
        if lower.endswith(suffix):
            s = s[: -len(suffix)].rstrip("/")
            lower = s.lower()
            break
    while lower.endswith("/v1/v1"):
        s = s[:-3].rstrip("/")
        lower = s.lower()
    from urllib.parse import urlsplit
    try:
        endpoint = urlsplit(s)
        if endpoint.username is not None or endpoint.password is not None or endpoint.query or endpoint.fragment:
            raise GatewayError("gateway_unsafe_endpoint")
    except ValueError:
        raise GatewayError("gateway_unsafe_endpoint") from None

    if s and "/v1" not in lower:
        s = s.rstrip("/") + "/v1"
    return s

def _ssl_context():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

def _auth_header_sets(api_key: str):
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return [headers]

def _http_json(
    method: str, url: str, api_key: str, payload=None, timeout: int = 120,
    *, experiment_entry_point: str = "urllib_http_json",
) -> dict:
    _guard_experiment_outbound(experiment_entry_point)
    import ssl
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit
    if urlsplit(url).scheme.lower() != "https":
        raise GatewayTLSVerificationError("gateway_https_required")
    try:
        context = _ssl_context()
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise GatewayTLSVerificationError("gateway_tls_verification_required")
    except GatewayTLSVerificationError:
        raise
    except Exception:
        raise GatewayTLSVerificationError("gateway_tls_context_failed") from None
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "TeacherEval/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if body is not None:
        headers["Content-Type"] = "application/json"

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise GatewayError("gateway_redirect_rejected")

    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), _NoRedirect(),
    )
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        _record_experiment_outbound(experiment_entry_point)
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        if api_key:
            raw = raw.replace(api_key, "[credential redacted]")
        if _looks_html(raw):
            raise GatewayError("gateway_response_not_json")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise GatewayError("gateway_response_not_json") from None
        if isinstance(data, dict) and data.get("error"):

            raise GatewayError("gateway_upstream_error")
        return data if isinstance(data, dict) else {"data": data}
    except GatewayError:
        raise
    except ssl.SSLError:
        raise GatewayTLSVerificationError("gateway_tls_verification_failed") from None
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        label = "rate_limit" if code == 429 else "gateway_http_error"
        raise GatewayError(f"HTTP {code} {label}") from None
    except (TimeoutError, urllib.error.URLError) as exc:
        reason = getattr(exc, "reason", None)
        text = str(exc).lower()
        if isinstance(reason, ssl.SSLError) or "certificate_verify_failed" in text or "certificate verify failed" in text:
            raise GatewayTLSVerificationError("gateway_tls_verification_failed") from None
        if isinstance(exc, TimeoutError) or "timed out" in text:
            raise GatewayError("gateway_timeout: timed out") from None
        if _is_socket_connect_error(exc):
            raise GatewayConnectError("gateway_connect_failed") from None
        raise GatewayError("gateway_connection_failed") from None
    except Exception as exc:
        if _is_connection_error(exc):
            raise GatewayTransientError("gateway_connection_interrupted") from None
        raise GatewayError("gateway_request_failed") from None

def _looks_html(raw: str) -> bool:
    s = (raw or "").lstrip().lower()
    return s.startswith("<!doctype") or s.startswith("<html")

def _api_error_message(raw: str, status: int = 0) -> str:
    try:
        data = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    err = data.get("error") or data.get("message") or data.get("msg")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("msg") or str(err)
        typ = err.get("type") or ""
    else:
        msg = str(err or "")
        typ = data.get("type") or ""
    if not msg:
        return ""
    if status == 429 or "rate_limit" in str(typ):
        return f"中转站限流（429）：{msg}。接口是通的，请稍后再试，不要改地址。"
    return msg

def _candidate_bases(api_base: str) -> list:
    pass
    base = normalize_base(api_base)
    if not base:
        return []
    out = []

    def add(b):
        b = (b or "").rstrip("/")
        if b and b not in out:
            out.append(b)

    lower = base.lower()
    has_v1 = lower.endswith("/v1") or "/v1/" in lower
    if has_v1:
        add(base)
        return out
    add(base + "/v1")
    add(base + "/api/v1")
    add(base + "/openai/v1")
    return out

def _chat_url(api_base: str) -> str:
    base = _candidate_bases(api_base)[0] if _candidate_bases(api_base) else normalize_base(api_base)
    if base.lower().endswith("/chat/completions"):
        return base
    return base.rstrip("/") + "/chat/completions"

def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex[:10]

def _public_profile(p: dict) -> dict:
    key = p.get("api_key") or ""
    return {
        "id": p.get("id") or "",
        "name": p.get("name") or "未命名",
        "provider": p.get("provider") or "custom",
        "api_base": p.get("api_base") or "",
        "model": p.get("model") or "",
        "api_key_masked": _mask(key),
        "has_api_key": bool(normalize_key(key)),
    }

def save_user_config(cfg: dict) -> dict:
    pass
    from urllib.parse import urlsplit
    try:
        candidate = urlsplit(normalize_base(cfg.get("api_base") or ""))
        if candidate.username is not None or candidate.password is not None or candidate.query or candidate.fragment:
            raise GatewayError("gateway_unsafe_endpoint")
    except ValueError:
        raise GatewayError("gateway_unsafe_endpoint") from None
    store = load_store()
    profiles = store.get("profiles") or []
    name = (cfg.get("name") or "").strip()
    pid = (cfg.get("id") or "").strip()
    overwrite = bool(cfg.get("overwrite"))

    if cfg.get("clear_key") and not (cfg.get("api_base") or name or pid):
        _write_store({"active": "", "profiles": []})
        return public_status()

    target = None
    if overwrite and pid:
        target = next((p for p in profiles if p.get("id") == pid), None)
    if target is None and name:
        target = next((p for p in profiles if (p.get("name") or "") == name), None)
    if target is None:
        target = {"id": _new_id()}
        profiles.append(target)

    key = normalize_key(cfg.get("api_key") or "")
    if not key:
        key = normalize_key(target.get("api_key") or "")
    if cfg.get("clear_key"):
        key = ""
    provider = (cfg.get("provider") or target.get("provider") or "custom").strip() or "custom"
    preset = PRESETS.get(provider, {})
    base = normalize_base(cfg.get("api_base") or "") or normalize_base(target.get("api_base") or "") or preset.get("api_base", "")
    model = (cfg.get("model") or "").strip() or (target.get("model") or "").strip() or preset.get("model", "")

    target["provider"] = provider
    target["api_base"] = base
    target["api_key"] = key
    target["model"] = model
    if name:
        target["name"] = name
    elif not target.get("name"):
        target["name"] = f"{provider} / {model or '未选模型'}"

    store["profiles"] = profiles
    store["active"] = target["id"]
    _write_store(store)
    return public_status()

def activate_profile(profile_id: str) -> dict:
    store = load_store()
    if not any(p.get("id") == profile_id for p in store.get("profiles") or []):
        raise KeyError(f"配置 {profile_id} 不存在")
    store["active"] = profile_id
    _write_store(store)
    return public_status()

def delete_profile(profile_id: str) -> dict:
    store = load_store()
    profiles = [p for p in (store.get("profiles") or []) if p.get("id") != profile_id]
    active = store.get("active")
    if active == profile_id:
        active = profiles[0]["id"] if profiles else ""
    _write_store({"active": active, "profiles": profiles})
    return public_status()

def _mask(key: str) -> str:
    return "已加密" if key else ""

def _endpoint_from_user(user: dict) -> Optional[dict]:
    pass
    provider = (user.get("provider") or "custom").strip() or "custom"
    preset = PRESETS.get(provider, {})
    key = normalize_key(user.get("api_key") or "")
    if not key:
        env_name = preset.get("key_env")
        if env_name:
            key = normalize_key(os.environ.get(env_name) or "")
    if not key:
        for env_name in ("DEEPSEEK_API_KEY", "QWEN_API_KEY", "KIMI_API_KEY", "OPENAI_API_KEY"):
            key = normalize_key(os.environ.get(env_name) or "")
            if key:
                break
    base = normalize_base(user.get("api_base") or "") or preset.get("api_base", "")
    model = (user.get("model") or "").strip() or preset.get("model") or "deepseek-chat"
    if not key or not base:
        return None
    return {"provider": provider, "api_base": base, "api_key": key, "model": model}

def active_endpoint() -> Optional[dict]:
    pass
    return _endpoint_from_user(load_user_config())

def public_status() -> dict:
    pass
    storage = secure_credentials.storage_status(path=user_config_path())
    try:
        store = secure_credentials.public_store(path=user_config_path())
    except secure_credentials.SecureCredentialError as exc:
        store = {"active": "", "profiles": []}
        storage["secure_storage_error"] = str(exc)
        storage["needs_resave"] = True
    cfg = next((p for p in store["profiles"] if p["id"] == store["active"]), {})
    from packages.experiment_protocol import experiment_mode_status
    experiment = experiment_mode_status()
    configured = bool(cfg.get("has_api_key") and cfg.get("api_base")
                      and storage["secure_storage_available"] and not storage["secure_storage_error"])
    profiles = [{**p, "api_key_masked": "已加密" if p["has_api_key"] else ""}
                for p in store["profiles"]]
    return {
        "configured": configured,
        "id": cfg.get("id") or "", "name": cfg.get("name") or "",
        "provider": cfg.get("provider") or "", "api_base": cfg.get("api_base") or "",
        "model": cfg.get("model") or "",
        "api_key_masked": "已加密" if cfg.get("has_api_key") else "",
        "has_api_key": bool(cfg.get("has_api_key")),
        "env_keys": {name: bool(os.environ.get(env)) for name, env in
                     (("deepseek", "DEEPSEEK_API_KEY"), ("qwen", "QWEN_API_KEY"),
                      ("kimi", "KIMI_API_KEY"), ("openai", "OPENAI_API_KEY"))},
        "config_file": os.path.basename(user_config_path()), "config_path_redacted": True,
        "chat_url": _chat_url(cfg["api_base"]) if configured else "",
        "active": store["active"], "profiles": profiles,
        "model_calls_allowed": bool(experiment.get("llm_calls_allowed")),
        "experiment_mode": experiment.get("active_mode"),
        **storage,
        "note": "配置已用 Windows 当前用户 DPAPI 加密；旧明文配置不会读取或迁移，请本人重新填写保存。",
    }

def _load_providers(path=None):
    if path is None:
        path = _builtin_providers_path()

    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            providers = json.load(f)["providers"]
    else:
        providers = []
    user = load_user_config()
    if user.get("api_key") or user.get("api_base"):
        custom = {
            "id": user.get("provider") or "custom",
            "api_base": user.get("api_base") or "",
            "model": user.get("model") or "deepseek-chat",
            "key_env": None,
            "api_key": user.get("api_key") or "",
            "priority": 0,
            "capable_tasks": ["chat", "reasoning", "analysis"],
        }

        replaced = False
        out = []
        for p in providers:
            if p["id"] == custom["id"]:
                merged = dict(p)
                if custom["api_base"]:
                    merged["api_base"] = custom["api_base"]
                if custom["model"]:
                    merged["model"] = custom["model"]
                if custom["api_key"]:
                    merged["api_key"] = custom["api_key"]
                merged["priority"] = 0
                out.append(merged)
                replaced = True
            else:
                out.append(p)
        if not replaced:
            out.insert(0, custom)
        return out
    return providers

def _get_key(provider_cfg) -> Optional[str]:
    pass
    if provider_cfg.get("api_key"):
        return provider_cfg["api_key"]
    user = load_user_config()
    if user.get("api_key") and (
        not user.get("provider") or user.get("provider") in (provider_cfg.get("id"), "custom")
    ):
        return user["api_key"]
    env = provider_cfg.get("key_env")
    if env:
        return os.environ.get(env)
    return None

def _client_for(provider_cfg):
    pass
    if openai is not None:
        return openai, "openai"
    if httpx is not None:
        return httpx, "httpx"
    return None, "urllib"

def _content_text(value) -> str:
    pass
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            _content_text(
                part.get("text")
                if isinstance(part, dict) and "text" in part
                else part.get("content")
                if isinstance(part, dict) and "content" in part
                else part.get("value")
                if isinstance(part, dict) and "value" in part
                else part
            )
            for part in value
        )
    if isinstance(value, dict):
        if "intent" in value:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        for key in ("text", "content", "value", "output_text"):
            if key in value:
                return _content_text(value.get(key))
        return ""
    return str(value)

def _strict_json_object_text(value) -> str:
    pass
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raw = _content_text(value).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

def _extract_payload(data, *, allow_reasoning_json: bool = False) -> dict:
    pass
    if not isinstance(data, dict):
        raise GatewayError("gateway_response_invalid_object")
    if data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            raise GatewayError("gateway_upstream_error")
        raise GatewayError("gateway_upstream_error")
    choices = data.get("choices") or []
    if not choices:
        raise GatewayError("gateway_response_missing_choices")
    choice = choices[0] or {}
    msg = choice.get("message")
    if msg is None and "text" not in choice:
        raise GatewayError("gateway_response_missing_content")
    msg = msg or {}
    content = _content_text(msg.get("content"))
    if not content:
        content = _content_text(choice.get("text"))
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    content_source = "content"
    if allow_reasoning_json and not content.strip():
        strict_reasoning_json = _strict_json_object_text(reasoning)
        if strict_reasoning_json:
            content = strict_reasoning_json
            content_source = "reasoning_json_object"
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return {
        "content": str(content),
        "content_source": content_source,
        "finish_reason": choice.get("finish_reason"),
        "usage": usage,
        "reasoning_chars": len(str(reasoning)),
    }

def _extract_content(data) -> str:
    return _extract_payload(data)["content"]

def _request_payload(req: ChatRequest) -> dict:
    pass
    from urllib.parse import urlparse
    payload = {
        "model": req.model,
        "messages": req.messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
    }
    if (req.task in {"semantic_parser", "experiment_parser", "semantic_check", "semantic_repair"}
            and urlparse(req.api_base or "").hostname == "api.deepseek.com"
            and req.model in {"deepseek-flash", "deepseek-v4-pro"}):
        payload["thinking"] = {"type": "disabled"}
    return payload

def _urllib_call(req: ChatRequest) -> dict:
    import time
    payload = _request_payload(req)
    if req.single_endpoint:

        url = req.api_base.rstrip("/") + "/chat/completions"
        if req.http_event_sink:
            req.http_event_sink("started", {"url": url, "payload": payload})
        try:
            data = _http_json("POST", url, req.api_key, payload,
                              timeout=req.timeout,
                              experiment_entry_point=f"urllib:{req.task or 'chat'}")
        except Exception as exc:
            if req.http_event_sink:
                req.http_event_sink("failed", {"error_type": type(exc).__name__})
            raise
        if req.http_event_sink:
            req.http_event_sink("returned", data)
        return _extract_payload(data, allow_reasoning_json=req.task in {"semantic_parser", "experiment_parser"})
    last = None
    tried = []
    for base in _candidate_bases(req.api_base):
        url = base.rstrip("/") + "/chat/completions"
        tried.append(url)

        transport_attempts = max(1, min(2, int(req.transport_attempts)))
        for attempt in range(transport_attempts):
            try:
                data = _http_json(
                    "POST",
                    url,
                    req.api_key,
                    payload,
                    timeout=req.timeout,
                    experiment_entry_point=f"urllib:{req.task or 'chat'}",
                )
                return _extract_payload(
                    data,
                    allow_reasoning_json=req.task in {"semantic_parser", "experiment_parser"},
                )
            except GatewayTLSVerificationError:
                raise
            except GatewayError as e:
                last = e
                msg = str(e).lower()
                is_timeout = ("timed out" in msg or "超时" in msg)
                if is_timeout:
                    if attempt + 1 < transport_attempts:
                        time.sleep(1.0)
                        continue
                    raise GatewayError(
                        f"上游模型响应超时（>{req.timeout}s，已重试 1 次）。"
                        f"长报告解读可适当调大 TEACHER_EVAL_LLM_TIMEOUT_S。"
                    )
                is_transient = (
                    not is_timeout
                    and (
                        isinstance(e, GatewayTransientError)
                        or "temporarily unavailable" in msg
                        or "502" in msg
                        or "503" in msg
                        or "504" in msg
                        or "overloaded" in msg
                        or "busy" in msg
                        or "gateway timeout" in msg
                        or "bad gateway" in msg
                    )
                )
                if is_transient and attempt + 1 < transport_attempts:
                    time.sleep(2.0)
                    continue

                if "限流" in str(e) or "rate_limit" in msg:
                    raise
                if is_transient:
                    if isinstance(e, GatewayConnectError):

                        raise GatewayConnectError(
                            f"{e}（已自动重试 1 次仍失败。请求尚未到达上游，通常是本机网络或代理软件状态问题，"
                            f"而非模型服务故障；请检查代理软件与系统代理设置后点击「重试云端大模型」。）",
                            cause=e,
                        )
                    if isinstance(e, GatewayTransientError):
                        raise GatewayTransientError(
                            f"上游连接被中断（{e}）。长文本生成期间中转站可能掐断连接，已自动重试 1 次仍失败，"
                            f"请稍后点击「重试云端大模型」。",
                            cause=e,
                        )
                    raise GatewayError(f"上游模型服务集群瞬时繁忙（{e}）。请稍后点击「重新解读」发起复试。")
                if "@ http" in msg or is_timeout:
                    raise
                break
    raise GatewayError((str(last) if last else "调用失败") + "；已尝试：" + " 、 ".join(tried))

def _http_call(client, req: ChatRequest):
    pass
    entry_point = f"httpx:{req.task or 'chat'}"
    _guard_experiment_outbound(entry_point)
    url = _chat_url(req.api_base)
    payload = _request_payload(req)
    headers = {"Authorization": f"Bearer {req.api_key}", "Content-Type": "application/json"}
    _record_experiment_outbound(entry_point)
    resp = client.post(url, json=payload, headers=headers, timeout=req.timeout)
    if resp.status_code >= 400:
        raise GatewayError(f"HTTP {int(resp.status_code)} gateway_http_error")
    return _extract_payload(
        resp.json(),
        allow_reasoning_json=req.task in {"semantic_parser", "experiment_parser"},
    )

def _openai_call(client, req: ChatRequest):
    pass
    entry_point = f"openai:{req.task or 'chat'}"
    _guard_experiment_outbound(entry_point)
    c = client.OpenAI(api_key=req.api_key, base_url=normalize_base(req.api_base), timeout=float(req.timeout))
    _record_experiment_outbound(entry_point)
    payload = _request_payload(req)
    extra = {"thinking": payload.pop("thinking")} if "thinking" in payload else {}
    data = c.chat.completions.create(
        **payload, timeout=float(req.timeout), **({"extra_body": extra} if extra else {}),
    )
    choice = data.choices[0]
    usage = getattr(data, "usage", None)
    content = _content_text(choice.message.content)
    reasoning = (
        getattr(choice.message, "reasoning_content", None)
        or getattr(choice.message, "reasoning", None)
        or ""
    )
    content_source = "content"
    if req.task in {"semantic_parser", "experiment_parser"} and not content.strip():
        strict_reasoning_json = _strict_json_object_text(reasoning)
        if strict_reasoning_json:
            content = strict_reasoning_json
            content_source = "reasoning_json_object"
    return {
        "content": content,
        "content_source": content_source,
        "finish_reason": getattr(choice, "finish_reason", None),
        "usage": usage.model_dump() if hasattr(usage, "model_dump") else {},
        "reasoning_chars": len(_content_text(reasoning)),
    }

def call_detailed(req: ChatRequest) -> dict:
    pass
    user_cfg = load_user_config()
    ep = _endpoint_from_user(user_cfg)
    if req.expected_profile_id:
        active_profile_id = str(user_cfg.get("id") or "").strip()
        if active_profile_id != str(req.expected_profile_id).strip():
            raise GatewayUnavailable("experiment_bound_profile_changed")
        if ep is None:
            raise GatewayUnavailable("experiment_bound_profile_unavailable")
        from packages.experiment_protocol import bind_experiment_profile_fingerprint

        profile_fingerprint = hashlib.sha256(json.dumps({
            "id": active_profile_id,
            "provider": ep.get("provider"),
            "api_base": ep.get("api_base"),
            "model": ep.get("model"),
            "api_key": ep.get("api_key"),
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if not bind_experiment_profile_fingerprint(profile_fingerprint):
            raise GatewayUnavailable("experiment_bound_profile_changed")

        req.provider = ep["provider"]
        req.api_key = normalize_key(ep["api_key"])
        req.api_base = normalize_base(ep["api_base"])
        req.model = ep["model"]
    elif ep and (not req.api_key or not req.api_base):
        req.provider = req.provider or ep["provider"]
        req.api_key = normalize_key(req.api_key or ep["api_key"])
        req.api_base = normalize_base(req.api_base or ep["api_base"])
        req.model = req.model or ep["model"]
    else:
        providers = {p["id"]: p for p in _load_providers()}
        cfg = providers.get(req.provider) if req.provider else None
        if cfg:
            req.api_key = normalize_key(req.api_key or _get_key(cfg) or "")
            req.api_base = normalize_base(req.api_base or cfg.get("api_base") or "")
            req.model = req.model or cfg.get("model")
        else:
            req.api_key = normalize_key(req.api_key or "")
            req.api_base = normalize_base(req.api_base or "")
    if not req.api_key:
        raise GatewayError("缺少 API Key：请在「模型密钥」页填写官方或中转站密钥", provider=req.provider)
    if not req.api_base:
        raise GatewayError("缺少 API 地址：请在本地模型配置中填写官方或中转站地址", provider=req.provider)
    req.model = req.model or "deepseek-chat"

    attempts = []
    budget = max(1, int(req.max_tokens or 0))
    retry_ceiling = MAX_TOKENS_CEILING
    if req.max_retry_tokens is not None:
        retry_ceiling = min(retry_ceiling, max(budget, int(req.max_retry_tokens)))
    result = {}
    original_messages = list(req.messages)
    max_attempts = max(1, min(2, int(req.max_attempts or 1)))
    for round_index in range(max_attempts):
        req.max_tokens = budget
        retry_prompt_used = round_index > 0 and bool(req.empty_retry_messages)
        if retry_prompt_used:
            req.messages = list(req.empty_retry_messages or ())
        try:

            result = _urllib_call(req)
        except GatewayError:
            raise
        except Exception as e:
            raise GatewayError("gateway_request_failed", provider=req.provider) from None

        content = result.get("content") or ""
        attempts.append({
            "max_tokens": budget,
            "finish_reason": result.get("finish_reason"),
            "content_chars": len(content),
            "reasoning_chars": result.get("reasoning_chars"),
            "content_source": result.get("content_source") or "content",
            "message_variant": "empty_content_retry" if retry_prompt_used else "primary",
            "timeout_s": req.timeout,
        })
        if content.strip():
            break
        truncated = result.get("finish_reason") == "length"
        budget_exhausted = truncated or bool(result.get("reasoning_chars"))
        if round_index == 0 and max_attempts > 1 and budget_exhausted:
            budget = min(max(budget * 3, budget + 1024), retry_ceiling)
            continue
        if round_index == 0 and max_attempts > 1:
            continue
        break

    req.messages = original_messages
    result["attempts"] = attempts
    result["escalated"] = len(attempts) > 1 and attempts[-1]["max_tokens"] > attempts[0]["max_tokens"]
    result["provider"] = req.provider
    result["model"] = req.model
    result["truncated"] = result.get("finish_reason") == "length"
    return result

def call(req: ChatRequest) -> str:
    pass
    return call_detailed(req).get("content") or ""

def route(task: str = "chat", fallback: bool = True) -> Optional[dict]:
    pass
    ep = active_endpoint()
    if ep:
        return {"provider": ep["provider"], "model": ep["model"]}
    providers = sorted(_load_providers(), key=lambda p: p.get("priority", 99))
    for p in providers:
        if task in p.get("capable_tasks", []) and _get_key(p):
            return {"provider": p["id"], "model": p["model"]}
    if not fallback:
        return None
    for p in providers:
        if task in p.get("capable_tasks", []):
            return {"provider": p["id"], "model": p["model"]}
    return None

def cross_check(prompt: str, n: int = 2) -> dict:
    pass
    providers = sorted(_load_providers(), key=lambda p: p.get("priority", 99))
    picked = [p for p in providers if "chat" in p.get("capable_tasks", [])][:n]
    responses = []
    for p in picked:
        try:
            text = call(ChatRequest(provider=p["id"], task="chat", messages=[
                {"role": "user", "content": prompt},
            ]))
            responses.append({"provider": p["id"], "text": text})
        except GatewayError:
            continue
    agreed = len(responses) >= 2 and responses[0]["text"].strip() == responses[1]["text"].strip()
    return {"responses": responses, "agreed": agreed}

def interpret_report(markdown: str, question: str, result: Optional[dict] = None) -> dict:
    pass
    routed = route("chat")
    if not routed:
        raise GatewayUnavailable("未配置模型：请在「接口配置」填写官方或中转站 API 地址与密钥")

    if "双校数字化多维智能比对" in markdown or "双校智能比对" in markdown or (result and result.get("object_type") == "school_compare"):
        prompt = (
            "你是教育视导与学校发展咨询专家。下面是规则引擎根据两所学校真实填报数据计算的双校数字化对比报告（数字已锁定，不得篡改）。\n"
            "请用中文直接进行专业对比研判：\n"
            "1）第一句话明确告知两校综合得分及领先归属；\n"
            "2）详细评述优势校在哪些维度具备显著示范效应，对标校主要短板在哪里；\n"
            "3）为对标校提出具体改进举措，并给出两校数字化结对互鉴建议。严禁捏造报告中没有的数字。\n"
            f"用户提问：{question}\n\n双校对比数据：\n{markdown[:6000]}"
        )
    elif "双区域宏观横向比对" in markdown or (result and result.get("object_type") == "region_compare"):
        prompt = (
            "你是区域教育数字化监测与决策咨询专家。下面是规则引擎计算的两区域数字化大盘横向对比数据（数字已锁定，不得篡改）。\n"
            "请用中文进行宏观对比诊断：\n"
            "1）评述两区域整体大盘均分差距与领先归属；\n"
            "2）剖析两地在六大维度上的结构性优劣势互补特征；\n"
            "3）为教育行政管理部门提出跨区域数字化互通互鉴政策建议。不要编造数字。\n"
            f"用户提问：{question}\n\n双区域对比数据：\n{markdown[:6000]}"
        )
    elif "学校指标事实查询" in markdown:
        prompt = (
            "你是教育数字化评估与问答助手。下面是规则引擎从官方作答库查出的学校具体指标事实（数字真实确凿，不得篡改）。\n"
            "请用中文直接、自然、专业地回答用户提问：\n"
            "1）第一句话直接准确给出该校该指标的具体配置数量/数值；\n"
            "2）结合报告中的关联设备配置与师生规模做客观简评（如师均配比、充裕程度）；\n"
            "3）提及历史对比（若有历史数据）。严禁捏造报告中没有的数字。\n"
            f"用户提问：{question}\n\n事实数据报告：\n{markdown[:6000]}"
        )
    elif "区域学校数字化排名报告" in markdown or (
        result
        and result.get("object_type") == "region"
        and result.get("analysis_operation") == "region_ranking"
    ):
        prompt = (
            "你是区域教育数字化监测助手。下面是规则引擎按固定口径计算并完成契约核验的区域学校排名（名次、学校、得分和总体规模均已锁定，不得篡改）。\n"
            "请用中文简洁回答用户：第一句直接复述请求对应的名次或前N名；随后说明排序口径为各校非空标准化作答均值乘100，按未舍入得分降序、同分按学校ID升序形成唯一序号，展示保留两位小数。"
            "不得补写榜单之外的学校、名次、分数或因果结论，也不得把榜单学校解释为用户已选定的学校焦点。\n"
            f"用户提问：{question}\n\n区域排名数据：\n{markdown[:6000]}"
        )
    elif "区域数字化宏观评估报告" in markdown or (result and result.get("object_type") == "region"):
        prompt = (
            "你是区域教育数字化监测与决策咨询专家。下面是规则引擎根据区域全量中小学真实作答统计生成的区域大盘数据（数字已由系统计算，不得篡改）。\n"
            "请用中文进行宏观诊断解读：\n"
            "1）评述该区域整体数字化发展水平与学校分层结构（优良中差梯队）；\n"
            "2）重点剖析该区域的突出优势维度与核心制约短板；\n"
            "3）结合标杆学校经验，为教育局/管理者提出针对性的区域数字化推进建议。不要编造数字。\n"
            f"用户提问：{question}\n\n区域大盘数据：\n{markdown[:6000]}"
        )
    elif "学校区域数字化排位与对比" in markdown or (result and result.get("object_type") == "school_ranking"):
        prompt = (
            "你是教育视导与学校发展咨询专家。下面是规则引擎根据真实作答库计算出的学校在所属区域的排位对比报告（数字真实确凿，不得篡改）。\n"
            "请用中文直接、客观测度并回答用户提问：\n"
            "1）第一句话明确告知该校在全区的综合排位、超越比例及综合得分；\n"
            "2）详细评述该校相对于全区均值的领先优势维度与相对滞后短板；\n"
            "3）为该校校长和教研团队提出切实可行的针对性改进建议。严禁捏造报告中没有的数字。\n"
            f"用户提问：{question}\n\n排位对比数据：\n{markdown[:6000]}"
        )
    elif "全国省域教育数字化格局宏观研判" in markdown or "全国各省教育数字化" in markdown or (result and result.get("object_type") == "national_ranking"):
        prompt = (
            "你是国家教育数字化战略与省级教育督导评估专家。下面是规则引擎根据全国各省在册中小学真实填报作答统计生成的省域宏观研判数据（数字已锁定，不得篡改、不得新增）。\n"
            "请用中文撰写省域层面的宏观研判与视导建议：\n"
            "1）第一句点明本年度全国省域教育数字化格局的总体特征与榜首省份及其得分；\n"
            "2）按样本规模分层（大规模≥1000所、中等100-999所、小微规模）评述各层标杆省份的示范意义与梯队差距；\n"
            "3）面向教育行政管理部门提出可落地的省域协同、跨省结对与薄弱区域帮扶建议；\n"
            "4）最后必须提示：各省填报在册学校覆盖水平存在客观阶段性差异，小微样本省份的单校偶发值影响波动较大，制定推进规划时应基于全量普查常态化数据做增值性复盘。\n"
            "严禁捏造报告中没有的数字或省份。\n"
            f"用户提问：{question}\n\n省域宏观研判数据：\n{markdown[:6000]}"
        )
    else:
        prompt = (
            "你是教育评估助手。下面是规则引擎算出的评估报告（数字已由系统计算，不得改数）。\n"
            "请用中文简要解读：1）总分与覆盖率含义；2）最弱维度及可能原因；"
            "3）给评审委员会的复核要点。不要编造报告中没有的数字。\n"
            f"用户问题：{question}\n\n报告：\n{markdown[:6000]}"
        )

    try:
        detail = call_detailed(ChatRequest(
            provider=routed["provider"],
            model=routed.get("model"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4096,
            timeout=DEFAULT_TIMEOUT_S,
        ))
        text = detail.get("content") or ""
        if not text.strip():
            from packages.agent.expert_advisor import generate_expert_advice
            fallback_text = generate_expert_advice(result or {}, question)
            return {
                "provider": detail.get("provider") or routed["provider"],
                "model": detail.get("model") or routed.get("model"),
                "text": fallback_text,
                "is_fallback": True,
                "fallback_reason": _empty_reason(detail),
                "finish_reason": detail.get("finish_reason"),
                "usage": detail.get("usage"),
                "attempts": detail.get("attempts"),
                "upstream_empty": True,
            }
        return {
            "provider": detail.get("provider") or routed["provider"],
            "model": detail.get("model") or routed.get("model"),
            "text": text,
            "finish_reason": detail.get("finish_reason"),
            "usage": detail.get("usage"),
            "attempts": detail.get("attempts"),
            "escalated": detail.get("escalated"),
        }
    except Exception as e:
        try:
            from packages.agent.expert_advisor import generate_expert_advice
            fallback_text = generate_expert_advice(result or {}, question)
            return {
                "provider": "local_expert_engine",
                "model": "rule-based-advisor",
                "text": fallback_text,
                "is_fallback": True,
                "fallback_reason": str(e),
                "upstream_empty": False,
            }
        except Exception:
            raise e

def _empty_reason(detail: dict) -> str:
    pass
    finish = detail.get("finish_reason")
    attempts = detail.get("attempts") or []
    reasoning = attempts[-1].get("reasoning_chars") if attempts else 0
    used = attempts[-1].get("max_tokens") if attempts else None
    if finish == "length":
        return (
            f"上游输出预算耗尽被截断（finish_reason=length，max_tokens={used}）。"
            f"该模型为推理型时，推理内容会占用输出预算。"
        )
    if reasoning:
        return f"上游仅返回推理内容、正文为空（reasoning_chars={reasoning}，max_tokens={used}）。"
    return f"上游模型返回正文为空（finish_reason={finish or 'empty'}，max_tokens={used}）。"

def _model_id(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("id") or item.get("name") or item.get("model") or "").strip()
    return ""

def list_models(api_base: str = "", api_key: str = "", profile_id: str = "") -> dict:
    pass
    submitted_key = normalize_key(api_key)
    expected_profile_id = (profile_id or "").strip()
    profile_id_used = ""
    credential_source = "submitted_key"
    if submitted_key:
        base = normalize_base(api_base)
        key = submitted_key
        ep = {}
    else:
        store = load_store()
        active_profile_id = (store.get("active") or "").strip()
        if expected_profile_id and expected_profile_id != active_profile_id:
            return {
                "ok": False,
                "models": [],
                "error": "活动模型配置已变化，请刷新配置状态后重试。",
                "credential_source": "active_profile",
                "profile_id_used": active_profile_id,
            }
        active_profile = next(
            (p for p in (store.get("profiles") or []) if p.get("id") == active_profile_id),
            None,
        )
        ep = _endpoint_from_user(active_profile) if active_profile else None
        ep = ep or {}
        base = ep.get("api_base") or ""
        key = ep.get("api_key") or ""
        profile_id_used = active_profile_id
        credential_source = "active_profile"
    if not base or not key:
        return {
            "ok": False,
            "models": [],
            "error": "请先填写 API 地址和密钥（可先保存，或在拉取时一并提交）。",
            "credential_source": credential_source,
            "profile_id_used": profile_id_used,
        }
    last = None
    data = None
    url = ""
    tried = []
    for b in _candidate_bases(base):
        url = b.rstrip("/") + "/models"
        tried.append(url)
        try:
            data = _http_json("GET", url, key, timeout=30)
            break
        except GatewayTLSVerificationError as e:
            return {"ok": False, "models": [], "error": str(e), "error_code": str(e),
                    "retryable": False, "credential_source": credential_source,
                    "profile_id_used": profile_id_used}
        except GatewayError as e:
            last = e
            data = None
    if data is None:
        return {"ok": False, "models": [], "error": str(last) if last else "识别失败",
                "url": url, "tried": tried, "api_base": base, "api_key_masked": _mask(key),
                "credential_source": credential_source, "profile_id_used": profile_id_used}
    raw = []
    if isinstance(data, dict):
        raw = data.get("data") or data.get("models") or data.get("result") or []
    elif isinstance(data, list):
        raw = data
    ids = []
    seen = set()
    for item in raw:
        mid = _model_id(item)
        if mid and mid not in seen:
            seen.add(mid)
            ids.append(mid)
    ids.sort()
    current = (ep.get("model") or "").strip()
    return {
        "ok": True,
        "models": ids,
        "count": len(ids),
        "current": current if current in ids else (ids[0] if ids else current),
        "url": url,
        "api_base": base,
        "api_key_masked": _mask(key),
        "credential_source": credential_source,
        "profile_id_used": profile_id_used,
    }

def ping(*, expected_profile_id: str = "", official_only: bool = False) -> dict:
    pass
    if expected_profile_id:
        public = secure_credentials.public_store(path=user_config_path())
        if public["active"] != expected_profile_id:
            return {"ok": False, "error_code": "diagnostic_selected_profile_changed",
                    "failure_stage": "profile_resolution", "request_sent": False,
                    "request_attempts": 0, "automatic_retries": 0,
                    "usage": {}, "usage_status": "UNAVAILABLE",
                    "cost": None, "cost_status": "NOT_ESTIMATED"}
        selected = secure_credentials.load_profile(expected_profile_id, path=user_config_path())
        ep = {field: selected[field] for field in ("provider", "model", "api_base", "api_key")}
    else:
        ep = active_endpoint()
    if not ep:
        return {
            "ok": False,
            "error_code": "profile_not_configured",
            "failure_stage": "profile_resolution",
            "error_type": "GatewayUnavailable",
            "message": "尚未配置可用的模型网关，请先保存模型配置。",
            "retryable": False,
            "request_sent": False,
        }
    request_attempts = 0
    response_received = False
    try:
        if official_only:
            from urllib.parse import urlsplit
            base = urlsplit(ep["api_base"])
            if (ep["api_base"] not in ("https://api.deepseek.com", "https://api.deepseek.com/",
                                      "https://api.deepseek.com/v1", "https://api.deepseek.com/v1/")
                    or base.netloc != "api.deepseek.com"
                    or ep["model"] != "deepseek-flash"):
                raise GatewayUnavailable("diagnostic_official_profile_required")
        payload = {"model": ep["model"], "messages": [{"role": "user", "content": "只回复：ok"}],
                   "temperature": 0, "max_tokens": 8}
        if official_only:
            payload["thinking"] = {"type": "disabled"}
        request_attempts = 1
        data = _http_json("POST", _chat_url(ep["api_base"]), ep["api_key"], payload,
                          timeout=30, experiment_entry_point="urllib:experiment_diagnostic")
        response_received = True

        key = normalize_key(ep["api_key"])
        variants = {key, json.dumps(key, ensure_ascii=True)[1:-1],
                    json.dumps(key, ensure_ascii=False)[1:-1]} - {""}
        def reflected(value):
            if isinstance(value, str):
                return "[credential redacted]" in value or any(secret in value for secret in variants)
            if isinstance(value, dict):
                return any(reflected(k) or reflected(v) for k, v in value.items())
            if isinstance(value, list):
                return any(reflected(v) for v in value)
            return False
        if reflected(data):
            raise GatewayError("gateway_credential_in_response")
        detail = _extract_payload(data)
        usage = {name: value for name, value in detail.get("usage", {}).items()
                 if name in {"prompt_tokens", "completion_tokens", "total_tokens",
                             "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"}
                 and type(value) is int and value >= 0}
        finish = detail.get("finish_reason")
        text = detail.get("content", "")
        return {"ok": True, "provider": ep["provider"], "model": ep["model"],
                "api_key_masked": _mask(ep["api_key"]),
                "reply": "ok" if text.strip().lower() == "ok" else "response_received",
                "response_received": True, "request_sent": True,
                "request_attempts": 1, "automatic_retries": 0,
                "usage": usage, "usage_status": "RETURNED" if usage else "NOT_RETURNED",
                "finish_reason": finish if finish is None or isinstance(finish, str) and finish in {"stop", "length", "tool_calls", "content_filter"} else "UNKNOWN",
                "upstream_response_id": data.get("id") if isinstance(data.get("id"), str) and len(data["id"]) <= 200 else None,
                "returned_model": data.get("model") if isinstance(data.get("model"), str) and len(data["model"]) <= 200 else None,
                "cost": None, "cost_status": "NOT_ESTIMATED"}
    except GatewayError as e:

        text = str(e).lower()
        if isinstance(e, GatewayTLSVerificationError):
            error_code = "gateway_tls_verification_failed"
            failure_stage = "tls_validation"
            message = "TLS certificate validation failed; request stopped without insecure fallback."
            retryable = False
            request_sent = False
        elif isinstance(e, GatewayConnectError):
            error_code = "gateway_connect_failed"
            failure_stage = "gateway_connect"
            message = "模型网关连接未建立，请检查代理、DNS、TUN 或防火墙配置。"
            retryable = True
            request_sent = False
        elif isinstance(e, GatewayTransientError):
            error_code = "gateway_connection_interrupted"
            failure_stage = "gateway_request"
            message = "模型网关连接在请求过程中中断，请稍后重试。"
            retryable = True
            request_sent = None
        elif "timed out" in text or "超时" in text:
            error_code = "gateway_timeout"
            failure_stage = "gateway_request"
            message = "模型网关请求超时，请检查网络或稍后重试。"
            retryable = True
            request_sent = None
        elif re.search(r"\bhttp\s*(401|403)\b|\b(401|403)\b", text):
            error_code = "gateway_auth_rejected"
            failure_stage = "gateway_response"
            message = "模型网关拒绝了鉴权请求，请检查当前配置。"
            retryable = False
            request_sent = True
        elif "configured_experiment_mode_disallows_llm_entry_point" in text:
            error_code = "experiment_outbound_blocked"
            failure_stage = "experiment_gate"
            message = "当前实验模式禁止向外部模型网关发送请求。"
            retryable = False
            request_sent = False
        else:
            error_code = "gateway_request_failed"
            failure_stage = "gateway_request"
            message = "模型网关请求失败，请查看服务端审计记录。"
            retryable = False
            request_sent = None
        return {
            "ok": False,
            "provider": ep["provider"],
            "model": ep["model"],
            "api_key_masked": _mask(ep["api_key"]),
            "error_code": error_code,
            "failure_stage": failure_stage,
            "error_type": type(e).__name__,
            "message": message,
            "retryable": retryable,
            "request_sent": True if response_received else request_sent,
            "response_received": response_received,
            "request_attempts": request_attempts,
            "automatic_retries": 0,
            "usage": {}, "usage_status": "UNAVAILABLE",
            "cost": None, "cost_status": "NOT_ESTIMATED",
        }

if __name__ == "__main__":
    print("可用模型路由（chat 任务）：", route("chat"))
