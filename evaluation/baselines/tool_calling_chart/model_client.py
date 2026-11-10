"""One official HTTP transmission per model turn, with no transport retry."""
import http.client
import json
import time
import urllib.error
import urllib.request

from paths import MODEL_PLAN, enable_candidate, read_json


class TransportFailure(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelClient:
    def __init__(self, ledger, max_tokens=16384):
        enable_candidate()
        config = read_json(MODEL_PLAN / 'CONFIG.json')
        protocol = read_json(MODEL_PLAN / 'RUN_PROTOCOL.json')
        identity = config['model_identity']
        if identity['model'] != 'deepseek-flash' or identity['api_base'] != 'https://api.deepseek.com/v1':
            raise ValueError('existing_baseline_model_identity_changed')
        from packages.core.secure_credentials import load_profile
        # The credential stays inside the authenticated client. Never log it.
        profile = load_profile(identity['profile_id'],
                               path=config['common_environment']['TEACHER_EVAL_CREDENTIAL_STORE_PATH'])
        for key in ('provider', 'model', 'api_base'):
            if profile[key] != identity[key]:
                raise ValueError('frozen_profile_changed:' + key)
        self.key = profile['api_key'].strip()
        if self.key.lower().startswith('bearer '):
            self.key = self.key[7:].strip()
        if not self.key:
            raise ValueError('frozen_profile_key_missing')
        self.identity = {k: identity[k] for k in ('profile_id', 'provider', 'model', 'api_base')}
        self.temperature = protocol['parameters']['parser_temperature']
        if self.temperature != 0.0:
            raise ValueError('existing_parser_temperature_changed')
        self.max_tokens = max_tokens
        self.ledger = ledger
        self.calls = []
        self.qid = None
        self.opener = urllib.request.build_opener(NoRedirect())

    def begin(self, qid):
        self.qid, self.calls = qid, []

    def call(self, messages, tools, allow_tools=True):
        payload = dict(model=self.identity['model'], temperature=self.temperature,
                       max_tokens=self.max_tokens, messages=messages, stream=False,
                       tools=tools, tool_choice='auto' if allow_tools else 'none')
        url = self.identity['api_base'].rstrip('/') + '/chat/completions'
        request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                    headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'}, method='POST')
        if not self.calls:
            self.ledger.admit(self.qid)
        receipt = dict(call_index=len(self.calls) + 1, model_requested=payload['model'],
                       endpoint=url, temperature=self.temperature, max_tokens=self.max_tokens,
                       transport_attempts=1, usage=None, status='SENT')
        self.calls.append(receipt)
        self.ledger.event(self.qid, dict(type='model_request', **receipt, messages=messages,
                                         tool_choice=payload['tool_choice']))
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=120) as response:
                body = response.read().decode('utf-8')
                receipt['http_status'] = response.status
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, UnicodeError) as exc:
            # Keep safe error codes only. HTTP error bodies may contain secrets.
            receipt.update(status='NE', error=type(exc).__name__, http_status=getattr(exc, 'code', None),
                           elapsed_s=time.monotonic() - started)
            self.ledger.event(self.qid, dict(type='model_response', **receipt))
            raise TransportFailure(receipt['error']) from None
        receipt['elapsed_s'] = time.monotonic() - started
        try:
            data = json.loads(body)
            message = data['choices'][0]['message']
            if not isinstance(message, dict):
                raise TypeError('message_must_be_object')
            receipt.update(status='RECORDED', usage=data.get('usage'), model_returned=data.get('model'),
                           response_id=data.get('id'), finish_reason=data['choices'][0].get('finish_reason'),
                           message=message)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            receipt.update(status='NE', error='invalid_model_response:' + type(exc).__name__)
            self.ledger.event(self.qid, dict(type='model_response', **receipt))
            raise TransportFailure(receipt['error']) from None
        self.ledger.event(self.qid, dict(type='model_response', **receipt))
        return message
