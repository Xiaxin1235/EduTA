"""Explicit runtime bank input and read-only source locations."""
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
PROJECT = Path(os.environ.get('TE_TOOLCALL_PROJECT', '.'))
CANDIDATE = PROJECT / 'experiments/project1_subdim_fix9_candidate_20261031/candidate'
BASELINE = PROJECT / 'experiments/project1_text2sql_baselines_v3_20261021'
MODEL_PLAN = PROJECT / 'experiments/project1_rp_ablation_formal100_20261017/plan'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\w.-]+', value) or value in ('.', '..'):
        raise ValueError('invalid_directory_label')
    return value


def frozen_bank(path):
    data = read_json(path)
    bank = [{'question_id': label(q['id']), 'question': q['text'],
             'prior_steps': q.get('prior_steps', [])} for q in data['questions']]
    if len({q['question_id'] for q in bank}) != len(bank):
        raise ValueError('duplicate_question_id')
    return label(data['bank_id']), bank


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def enable_candidate():
    if str(CANDIDATE) not in sys.path:
        sys.path.insert(0, str(CANDIDATE))
    from packages.core import research_data
    if not research_data.enabled() or research_data.config_path().resolve() != CANDIDATE / 'config/research_dataset.json':
        raise RuntimeError('frozen_candidate_research_configuration_required')
