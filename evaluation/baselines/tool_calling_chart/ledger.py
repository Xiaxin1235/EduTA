"""Durable question admission and append-only per-call/per-execution receipts."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from paths import write_json


def now():
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    def __init__(self, run_dir, system, questions):
        self.root = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(run_dir / 'ledger.sqlite')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS questions(qid TEXT PRIMARY KEY, ordinal INTEGER, question TEXT, state TEXT, updated TEXT)')
        for i, q in enumerate(questions, 1):
            self.db.execute('INSERT OR IGNORE INTO questions VALUES(?,?,?,?,?)',
                            (q['question_id'], i, q['question'], 'NOT_SENT', now()))
        self.db.commit()
        observed = self.db.execute('SELECT qid,question FROM questions ORDER BY ordinal').fetchall()
        if observed != [(q['question_id'],q['question']) for q in questions]:
            raise ValueError('ledger_question_bank_changed')
        self.system = system

    def state(self, qid):
        return self.db.execute('SELECT state FROM questions WHERE qid=?', (qid,)).fetchone()[0]

    def admit(self, qid):
        # Atomic admission also prevents simultaneous runners from resending a question.
        with self.db:
            cur = self.db.execute("UPDATE questions SET state='SENT',updated=? WHERE qid=? AND state='NOT_SENT'", (now(), qid))
            if cur.rowcount != 1:
                raise RuntimeError('question_already_admitted:' + qid)

    def record(self, qid, outcome):
        write_json(self.root / 'results' / (qid + '.json'), outcome)
        self.mark_recorded(qid)

    def mark_recorded(self, qid):
        with self.db:
            self.db.execute("UPDATE questions SET state='RECORDED',updated=? WHERE qid=?", (now(), qid))

    def event(self, qid, value):
        path = self.root / 'events' / (qid + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'at': now(), **value}, ensure_ascii=False, allow_nan=False) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def close(self):
        self.db.close()
