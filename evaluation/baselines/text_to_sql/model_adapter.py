"""One HTTP transmission per call; the frozen DPAPI profile is the only key source."""
import json
import http.client
import re
import sys
import time
import urllib.error
import urllib.request
from paths import FORMAL, read_json


class TransportFailure(BaseException):
    """Bypass MAC-SQL Selector's catch-and-continue on transport exceptions."""


class ModelClient:
    def __init__(self, ledger):
        cfg = read_json(FORMAL / 'plan/CONFIG.json')
        self.identity = cfg['model_identity']
        self.parameters = read_json(FORMAL / 'plan/RUN_PROTOCOL.json')['parameters']
        sys.path.insert(0, str(FORMAL / 'frozen/candidate'))
        from packages.core.secure_credentials import load_profile
        self.profile = load_profile(self.identity['profile_id'],
                                   path=cfg['common_environment']['TEACHER_EVAL_CREDENTIAL_STORE_PATH'])
        for key in ('provider','api_base','model'):
            if self.profile[key] != self.identity[key]:
                raise ValueError('frozen_profile_changed:' + key)
        self.key = self.profile['api_key'].strip()
        if self.key.lower().startswith('bearer '):
            self.key = self.key[7:].strip()
        if not self.key:
            raise ValueError('frozen_profile_key_missing')
        self.ledger = ledger
        self.calls = []
        self.qid = None

    def begin(self, qid):
        self.qid, self.calls = qid, []

    def redact(self, text):
        return str(text).replace(self.profile['api_key'], '[REDACTED]').replace(self.key, '[REDACTED]')

    def call(self, prompt, stage='generate', messages=None):
        repair = stage in ('repair','Refiner')
        role = 'repair' if repair else 'parser'
        payload = {'model': self.identity['model'],
                   'temperature': self.parameters[role + '_temperature'],
                   'max_tokens': self.parameters[role + '_max_tokens'],
                   'messages': messages or [{'role':'user','content':prompt}], 'stream':False}
        url = self.identity['api_base'].rstrip('/') + '/chat/completions'
        request = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                    headers={'Authorization':'Bearer ' + self.key, 'Content-Type':'application/json'}, method='POST')
        if not self.calls:
            self.ledger.admit(self.qid)
        receipt = {'call_index':len(self.calls)+1, 'stage':stage, 'model_requested':payload['model'],
                   'endpoint':url, 'temperature':payload['temperature'], 'max_tokens':payload['max_tokens'],
                   'transport_attempts':1, 'usage':None, 'status':'SENT'}
        self.calls.append(receipt)
        self.ledger.event(self.qid, {'type':'model_request', **receipt,
                                    'messages': self.redact(json.dumps(payload['messages'], ensure_ascii=False))})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode('utf-8')
                receipt['http_status'] = response.status
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, UnicodeError) as exc:
            # No alias, fallback endpoint, SDK retry, ping, or second transmission.
            receipt.update(status='NE', error=self.redact(exc), elapsed_s=time.monotonic()-started)
            self.ledger.event(self.qid, {'type':'model_response', **receipt})
            raise TransportFailure(receipt['error']) from None
        receipt['elapsed_s'] = time.monotonic() - started
        receipt['raw_response'] = self.redact(body)
        try:
            data = json.loads(body)
            receipt.update(usage=data.get('usage'), model_returned=data.get('model'),
                           response_id=data.get('id'), status='RECORDED')
            choice = data['choices'][0]
            content = choice['message'].get('content') or ''
            receipt.update(content=self.redact(content), finish_reason=choice.get('finish_reason'))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            receipt.update(status='NE', error='invalid_model_response:' + type(exc).__name__)
            self.ledger.event(self.qid, {'type':'model_response', **receipt})
            raise TransportFailure(receipt['error']) from None
        self.ledger.event(self.qid, {'type':'model_response', **receipt})
        return receipt['content']


def extract_sql(text):
    blocks = re.findall(r'```sql\s*(.*?)```', text, re.I | re.S)
    return (blocks[-1] if blocks else text).strip()
