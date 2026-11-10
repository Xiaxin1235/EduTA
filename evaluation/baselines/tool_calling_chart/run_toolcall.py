"""Direct tool-calling baseline. Banks are supplied only at runtime."""
import argparse
import os
import hashlib
import json
import re
import time
from pathlib import Path

from paths import ROOT, frozen_bank, label, read_json, write_json
from ledger import Ledger
from model_client import ModelClient, TransportFailure

MAX_TOOL_CALLS = 8


def prior_messages(prior_steps):
    messages = []
    for step in prior_steps:
        if 'role' in step and step['role'] in ('user', 'assistant') and isinstance(step.get('content'), str):
            messages.append({'role': step['role'], 'content': step['content']})
        elif isinstance(step.get('question'), str) and isinstance(step.get('answer'), str):
            messages.extend([{'role': 'user', 'content': step['question']},
                             {'role': 'assistant', 'content': step['answer']}])
        else:
            raise ValueError('unsupported_prior_step_format')
    return messages


def tool_summary(value):
    """Flatten result fields into a compact table, without rewriting the answer."""
    rows = []

    fields = {'success', 'error', 'error_type', 'message', 'notice', 'measurement_note',
              'region', 'region_name', 'region_full_name', 'school', 'school_name',
              'year', 'year_a', 'year_b', 'total_score', 'target_dimension', 'topic',
              'topic_score', 'target_dimension_score', 'school_count', 'requested_school_count',
              'answer_count', 'n_answers', 'median_score', 'max_score', 'min_score',
              'target_value', 'normalized_score', 'unit', 'rank', 'score', 'dimension',
              'difference_b_minus_a', 'difference_field', 'eligible_count', 'all_items_uncollected',
              'diff', 'total_diff', 'score_diff', 'comparison_value', 'value_unit', 'pct_change',
              'target_stat', 'aggregation', 'school_filter', 'value_view'}
    containers = {'data', 'result', 'regions', 'annual_results', 'schools', 'a', 'b',
                  'dimensions', 'dimensions_comparison', 'ranking', 'ranking_items', 'target_school',
                  'unavailable', 'school_a', 'school_b'}

    def add(path, item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in fields or key.removesuffix('_a').removesuffix('_b') in fields or key in containers:
                    add(path + '.' + key if path else key, child)
        elif isinstance(item, list):
            for n, child in enumerate(item):
                add(path + '[' + str(n) + ']', child)
        elif item is not None:
            rows.append((path, str(item).replace('\t', ' ').replace('\n', ' ')))

    add('', value)
    return rows


def save_visible(ledger, out):
    lines = [out['final_answer']]
    for call in out['tool_calls']:
        lines.extend(['', '工具结果 ' + str(call['index']) + ' ' + call['name'],
                      '参数\t' + json.dumps(call['arguments'], ensure_ascii=False), '字段\t值'])
        lines.extend(field + '\t' + value for field, value in tool_summary(call['result']))
    if out.get('error'):
        lines.extend(['', '运行状态\t' + out['status'], '错误\t' + out['error']])
    path = ledger.root / 'results' / out['question_id'] / 'visible_answer.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    out['visible_answer_file'] = path.relative_to(ledger.root).as_posix()


def run_question(q, client, executor, ledger, system_prompt):
    qid = q['question_id']
    started = time.monotonic()
    messages = [{'role': 'system', 'content': system_prompt}] + prior_messages(q.get('prior_steps', []))
    messages.append({'role': 'user', 'content': q['question']})
    out = dict(question_id=qid, system='TOOLCALL', question=q['question'], prior_steps=q.get('prior_steps', []),
               state='RECORDED', status='FAIL', tool_calls=[], rejected_tool_calls=[], final_answer='', final_response_raw='')
    client.begin(qid)
    try:
        # At most eight executed functions plus a final model answer turn.
        for turn in range(MAX_TOOL_CALLS + 1):
            message = client.call(messages, executor.schemas, len(out['tool_calls']) < MAX_TOOL_CALLS)
            messages.append(message)
            calls = message.get('tool_calls') or []
            if not calls:
                raw = message.get('content') or ''
                out['final_response_raw'] = raw
                match = re.match(r'^\[(EXECUTED|CLARIFY|UNANSWERABLE)\]\s*\r?\n', raw)
                if match:
                    out.update(status=match[1], final_answer=raw[match.end():])
                else:
                    out.update(status='FAIL', final_answer=raw, error='missing_final_status_header')
                if not out['final_answer'].strip():
                    out.update(status='FAIL', error='empty_final_answer')
                if out['status'] == 'EXECUTED' and not out['tool_calls']:
                    out.update(status='FAIL', error='executed_without_tool_result')
                if client.calls[-1].get('finish_reason') == 'length':
                    out.update(status='FAIL', error='model_output_truncated')
                break
            for call in calls:
                function = call['function']
                arguments = function.get('arguments', '')
                if len(out['tool_calls']) >= MAX_TOOL_CALLS:
                    # Complete the API transcript, but execute no ninth tool.
                    result = {'success': False, 'error': 'tool_call_budget_exhausted'}
                    out['rejected_tool_calls'].append(dict(tool_call=call, result=result))
                    ledger.event(qid, dict(type='tool_call_rejected', tool_call=call, result=result))
                else:
                    tool_started = time.monotonic()
                    parsed = None
                    try:
                        parsed = json.loads(arguments)
                        result = executor.call(function['name'], parsed)
                    except (ValueError, TypeError) as exc:
                        result = {'success': False, 'error': str(exc), 'error_type': type(exc).__name__}
                    record = dict(index=len(out['tool_calls']) + 1, tool_call_id=call['id'],
                                  name=function['name'], arguments=parsed if parsed is not None else arguments,
                                  arguments_raw=arguments, result=result,
                                  elapsed_s=time.monotonic() - tool_started)
                    out['tool_calls'].append(record)
                    ledger.event(qid, dict(type='tool_execution', **record))
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(result, ensure_ascii=False, allow_nan=False)})
        else:
            out.update(status='FAIL', error='no_final_answer_within_call_budget')
    except TransportFailure as exc:
        out.update(status='NE', error=str(exc))
    except Exception as exc:
        # Preserve SENT and all evidence. A local defect cannot authorize resend.
        ledger.event(qid, dict(type='runner_error', error_type=type(exc).__name__, state=ledger.state(qid)))
        raise
    out.update(model_calls=client.calls, model_call_count=len(client.calls),
               tokens={key: sum((c.get('usage') or {}).get(key, 0) for c in client.calls)
                       for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
               unknown_usage_calls=sum(c.get('usage') is None for c in client.calls),
               elapsed_s=time.monotonic() - started)
    save_visible(ledger, out)
    ledger.record(qid, out)
    return out


def run_bank(bank_id, bank, tag, max_tokens=16384, start=1, end=None, pause_on_ne=False):
    from tool_layer import ToolExecutor, prompt_text
    end = len(bank) if end is None else end
    if not 1 <= start <= end <= len(bank):
        raise ValueError('require_1_le_start_le_end_le_question_count')
    root = ROOT / 'runs' / label(tag)
    os.environ['TEACHER_EVAL_CHARTS_DIR'] = str(root / 'charts')   # rendered PNGs stay with the run
    executor = ToolExecutor()
    prompt = prompt_text()
    metadata = dict(bank_id=bank_id, bank_sha256=hashlib.sha256(json.dumps(bank, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                    max_tokens=max_tokens, temperature=0.0, max_tool_calls=MAX_TOOL_CALLS,
                    prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                    tools_sha256=hashlib.sha256(json.dumps(executor.schemas, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                    implementation_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in ('paths.py', 'ledger.py', 'model_client.py', 'run_toolcall.py', 'tool_layer.py')})
    metadata_path = root / 'RUN_METADATA.json'
    if metadata_path.exists():
        if read_json(metadata_path) != metadata:
            raise ValueError('run_inputs_changed')
    else:
        write_json(metadata_path, metadata)
        (root / 'SYSTEM_PROMPT_FULL.txt').write_text(prompt, encoding='utf-8')
        write_json(root / 'TOOLS.json', executor.schemas)
    if hashlib.sha256((root / 'SYSTEM_PROMPT_FULL.txt').read_text(encoding='utf-8').encode()).hexdigest() != metadata['prompt_sha256']:
        raise ValueError('saved_system_prompt_changed')
    ledger = Ledger(root, 'TOOLCALL', bank)
    try:
        pending = []
        for q in bank[start - 1:end]:
            state = ledger.state(q['question_id'])
            if state == 'RECORDED':
                continue
            if state == 'SENT':
                saved = root / 'results' / (q['question_id'] + '.json')
                if not saved.exists():
                    raise RuntimeError('SENT_without_durable_result_reconcile_never_resend:' + q['question_id'])
                result = read_json(saved)
                if result['question_id'] != q['question_id'] or result['system'] != 'TOOLCALL' or result['state'] != 'RECORDED':
                    raise RuntimeError('saved_result_identity_mismatch')
                if not (root / result['visible_answer_file']).is_file():
                    raise RuntimeError('visible_answer_missing')
                ledger.mark_recorded(q['question_id'])
                continue
            pending.append(q)
        if not pending:
            return 0
        client = ModelClient(ledger, max_tokens)
        identity_path = root / 'MODEL_IDENTITY.json'
        if identity_path.exists():
            if read_json(identity_path) != client.identity:
                raise ValueError('run_model_identity_changed')
        else:
            write_json(identity_path, client.identity)
        for q in pending:
            result = run_question(q, client, executor, ledger, prompt)
            print(q['question_id'], result['status'], 'calls=' + str(result['model_call_count']), flush=True)
            if pause_on_ne and result['status'] == 'NE':
                print('PAUSE_AFTER_NE', q['question_id'], result.get('error'), flush=True)
                return 3
        return 0
    finally:
        ledger.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank-freeze', type=Path, required=True)
    parser.add_argument('--start', type=int, default=1)
    parser.add_argument('--end', type=int)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--max-tokens', type=int, default=16384)
    parser.add_argument('--pause-on-ne', action='store_true')
    args = parser.parse_args(argv)
    if args.max_tokens < 1:
        parser.error('max_tokens must be positive')
    bank_id, bank = frozen_bank(args.bank_freeze)
    return run_bank(bank_id, bank, args.tag, args.max_tokens, args.start, args.end, args.pause_on_ne)


if __name__ == '__main__':
    raise SystemExit(main())
