"""Frozen input locations. This module never loads reference answers at runtime."""
import json
import os
import re
import sys
from pathlib import Path

# Reading frozen project modules must not create __pycache__ in old directories.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
CANDIDATE = PROJECT / 'experiments/project1_rp_ablation_candidate_20261017/candidate'
FORMAL = PROJECT / 'experiments/project1_rp_ablation_formal100_20261017'
QUEUE = FORMAL / 'plan/FROZEN_QUEUE.json'
REFERENCE = FORMAL / 'grading/reference100/REFERENCE_100.json'
VENDOR = ROOT / 'third_party/MAC-SQL'
SCHEMA_DOC = ROOT / 'SCHEMA_DOC_v2_R11.md'
UNCOLLECTED_ITEMS = Path(os.environ.get('TE_UNCOLLECTED_ITEMS', 'research_rules/UNCOLLECTED_ITEMS_v1.json'))  # path made configurable for release


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def questions():
    # Preserve G1 question text and order verbatim; no reference joins.
    return [q for q in read_json(QUEUE) if q['arm'] == 'G1']


def frozen_bank(path):
    """Read only the supplied bank's identity and ordered question text."""
    data = read_json(path)
    bank_id = data['bank_id']
    if not isinstance(bank_id, str) or not re.fullmatch(r'[\w.-]+', bank_id) or bank_id in ('.', '..'):
        raise ValueError('bank_id_must_be_a_directory_label')
    bank = [{'question_id': q['id'], 'question': q['text'], 'prior_steps': []}
            for q in data['questions']]
    return bank_id, bank


def write_json(path, value):
    # Exclusive creation preserves previous runs, interrupted work, and failures.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
