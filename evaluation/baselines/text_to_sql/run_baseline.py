"""Formal sends are for the subsequent authorized operator, never setup checks."""
import argparse
import contextlib
import hashlib
import json
import re
import time
from pathlib import Path
from ledger import Ledger
from model_adapter import ModelClient, TransportFailure, extract_sql
from mac_sql_adapter import SQLTimeout
from paths import ROOT, SCHEMA_DOC, frozen_bank, questions, read_json, write_json
from views import execute_sql

B1_INSTRUCTION = '''你是 Text-to-SQL 系统。仅依据下列公共目录、数据字段和计算规则回答问题。
生成一条 SQLite 只读 SELECT/WITH 查询，返回回答问题所需的全部数值，最多 50 行。
只能查询 TEMP VIEW schools 和 answers。只输出以下三种 JSON 之一：
{{"sql": "一条只读 SQL"}}
{{"unanswerable": "数据不能回答或请求超出能力的原因"}}
{{"clarify": "题目有歧义，需要向用户澄清的问题"}}
按数据目录和请求判断是否可答；不能用虚构字段或无关数值代替缺失数据。

{schema}

问题：{question}'''


def schema_doc_label(path):
    stem = re.sub(r'^schema[_-]?doc[_-]?', '', Path(path).stem, flags=re.I)
    return 'doc' + re.sub(r'[\W_]+', '', stem).lower()


def parse_b1_reply(reply):
    text = reply.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.I | re.S)
    body = fenced.group(1) if fenced else text
    if not body.startswith('{'):
        # Retain the previous SQL/code-fence representation as well.
        return 'sql', extract_sql(text), ''
    data = json.loads(body)
    kinds = [key for key in ('sql', 'unanswerable', 'clarify') if key in data]
    if len(kinds) != 1 or not isinstance(data[kinds[0]], str) or not data[kinds[0]].strip():
        raise ValueError('b1_response_requires_one_sql_unanswerable_or_clarify')
    return kinds[0], data[kinds[0]].strip(), data.get('explanation', '')


def save_visible_answer(ledger, out):
    result = out['final_result']
    lines = []
    if result is not None:
        lines.append('结果表（最多前50行）')
        lines.append('\t'.join(result['columns']))
        for row in result['rows'][:50]:
            lines.append('\t'.join('NULL' if v is None else str(v).replace('\t', '\\t').replace('\n', '\\n') for v in row))
        if not result['rows']:
            lines.append('（无结果行）')
        if result['over_50_rows']:
            lines.append('（结果超过50行，此处仅展示前50行）')
        if result['error']:
            lines.append('SQL执行错误：' + result['error'])
    if out.get('unanswerable'):
        lines.append('不可回答：' + out['unanswerable'])
    if out.get('clarify'):
        lines.append('需要澄清：' + out['clarify'])
    if out.get('model_explanation'):
        lines += ['模型说明：', out['model_explanation']]
    if out['status'] == 'NE':
        lines.append('未测成：' + out['error'])
    path = ledger.root/'results'/out['question_id']/'visible_answer.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    out['visible_answer_file'] = path.relative_to(ledger.root).as_posix()


def run_question(system, q, client, ledger):
    qid = q['question_id']
    started = time.monotonic()
    executions = []
    out = {'question_id':qid, 'system':system, 'question':q['question'],
           'prior_steps':q['prior_steps'], 'state':'RECORDED', 'status':'EXECUTED',
           'generated_sql':[], 'sql_executions':executions, 'final_sql':None, 'final_result':None,
           'answer_type':None, 'model_explanation':''}
    client.begin(qid)

    def record_sql(sql):
        result = execute_sql(sql)
        executions.append(result)
        out.update(final_sql=sql, final_result=result)
        ledger.event(qid, {'type':'sql_execution', **result})
        return result

    def accept_b1_reply(reply):
        kind, value, explanation = parse_b1_reply(reply)
        out.update(answer_type=kind, model_explanation=explanation)
        if kind == 'sql':
            out['generated_sql'].append(value)
            return record_sql(value)
        out.update(status=kind.upper(), final_sql=None, final_result=None)
        out[kind] = value
        return None

    try:
        if system == 'B1':
            schema_text = getattr(client, 'schema_doc_text', None)
            if schema_text is None:
                schema_text = SCHEMA_DOC.read_text(encoding='utf-8')
            prompt = B1_INSTRUCTION.format(schema=schema_text,
                                           question=q['question'])
            messages = [{'role':'user','content':prompt}]
            reply = client.call(prompt, messages=messages)
            result = accept_b1_reply(reply)
            if result is not None and result['error'] and not result['timed_out']:
                messages += [{'role':'assistant','content':reply},
                             {'role':'user','content':'SQL 执行报错：' + result['error'] +
                              '\n请修改一次，仍按 SQL / unanswerable / clarify 三选一的 JSON 格式输出。'}]
                result = accept_b1_reply(client.call('', stage='repair', messages=messages))
        else:
            from mac_sql_adapter import run_mac_sql
            stdout = ledger.root/'events'/(qid + '.mac_sql_stdout.txt')
            stdout.parent.mkdir(parents=True, exist_ok=True)
            # Upstream emits schema/routing traces; keep them within this run.
            # NOT_SENT preparation can be resumed; retain any earlier local log.
            with stdout.open('a', encoding='utf-8') as f, contextlib.redirect_stdout(f):
                sql, message = run_mac_sql(q, client, record_sql)
            out['mac_sql_message'] = message
            out['answer_type'] = 'sql'
            if client.calls:
                out['model_explanation'] = re.sub(r'```sql\s*.*?```', '', client.calls[-1].get('content', ''), flags=re.I | re.S).strip()
            # The official last refinement can be unexecuted at MAX_ROUND.
            result = executions[-1] if executions and executions[-1]['sql'] == sql else record_sql(sql)
        if result is not None and (result['error'] or result['over_50_rows']):
            out['status'] = 'FAIL'
    except TransportFailure as exc:
        out.update(status='NE', error=str(exc))
    except SQLTimeout as exc:
        out.update(status='FAIL', error=str(exc), final_sql=executions[-1]['sql'], final_result=executions[-1])
    except Exception as exc:
        # A local implementation error does not create a model score or permit
        # a resend. Keep NOT_SENT/SENT and the durable receipts for the operator.
        ledger.event(qid, {'type':'runner_error', 'state':ledger.state(qid),
                          'error':client.redact(exc)})
        raise
    if system == 'B2':
        out['generated_sql'] = [block.strip() for call in client.calls
                                for block in re.findall(r'```sql\s*(.*?)```', call.get('content',''), re.I|re.S)]
    out.update(model_calls=client.calls, model_call_count=len(client.calls),
               tokens={'prompt_tokens':sum(c['usage'].get('prompt_tokens',0) for c in client.calls if c['usage']),
                       'completion_tokens':sum(c['usage'].get('completion_tokens',0) for c in client.calls if c['usage']),
                       'total_tokens':sum(c['usage'].get('total_tokens',0) for c in client.calls if c['usage']),
                       'unknown_usage_calls':sum(c['usage'] is None for c in client.calls)},
               elapsed_s=time.monotonic()-started)
    save_visible_answer(ledger, out)
    ledger.record(qid, out)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system', choices=['B1','B2'], required=True)
    parser.add_argument('--bank-freeze', type=Path, help='runtime-only frozen bank JSON; bank_id and ordered questions(id,text)')
    parser.add_argument('--schema-doc', type=Path, help='shared UTF-8 schema document; default SCHEMA_DOC_v2_R11.md')
    parser.add_argument('--start', type=int, default=1, help='1-based question ordinal, inclusive')
    parser.add_argument('--end', type=int, help='inclusive; default is the last question')
    # Added by Claude 2026-10-18: the frozen parser budget (3072) is used up by the model's
    # reasoning tokens before any SQL is written; a larger budget runs in its own folder.
    parser.add_argument('--max-tokens', type=int, default=16384, help='parser and repair max_tokens; default 16384')
    # Added by Claude 2026-10-18: like the evaluation harness, stop after a transport
    # failure (that question stays NE, never resent); run_with_pause.sh resumes after
    # the connection is confirmed, so no further questions are sent into an outage.
    parser.add_argument('--pause-on-ne', action='store_true', help='exit with code 3 after an NE question')
    parser.add_argument('--tag', default='', help='suffix for a separate complete run folder')
    args = parser.parse_args(argv)
    bank_id, bank = frozen_bank(args.bank_freeze) if args.bank_freeze else (None, questions())
    args.end = args.end if args.end is not None else len(bank)
    if not 1 <= args.start <= args.end <= len(bank):
        parser.error('require 1 <= start <= end <= question count')
    run_name = args.system + (f'_t{args.max_tokens}' if args.max_tokens else '') + (f'_{args.tag}' if args.tag else '')
    if bank_id:
        run_name += '_' + bank_id
    if args.schema_doc:
        run_name += '_' + schema_doc_label(args.schema_doc)
    schema_doc = (args.schema_doc or SCHEMA_DOC).resolve()
    schema_text = schema_doc.read_text(encoding='utf-8')
    metadata = dict(schema_doc_path=str(schema_doc), schema_doc_sha256=hashlib.sha256(schema_doc.read_bytes()).hexdigest())
    run_dir = ROOT/'runs'/run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = run_dir/'RUN_METADATA.json'
    if metadata_path.exists():
        if read_json(metadata_path) != metadata:
            raise ValueError('run_schema_document_changed')
    else:
        write_json(metadata_path, metadata)
    ledger = Ledger(run_dir, args.system, bank)
    # Local profile/schema preparation precedes admission; errors here are NOT_SENT.
    client = ModelClient(ledger)
    client.schema_doc_text = schema_text
    if args.max_tokens:
        client.parameters = {**client.parameters, 'parser_max_tokens': args.max_tokens, 'repair_max_tokens': args.max_tokens}
    try:
        for i in range(args.start, args.end+1):
            q = {**bank[i-1], 'baseline_ordinal':i}
            qid, state = q['question_id'], ledger.state(q['question_id'])
            if state == 'RECORDED':
                continue
            if state == 'SENT':
                saved = ledger.root/'results'/(qid + '.json')
                if saved.exists():
                    old = read_json(saved)
                    if old['question_id'] != qid or old['system'] != args.system or old['state'] != 'RECORDED':
                        raise RuntimeError('saved_result_identity_mismatch')
                    ledger.mark_recorded(qid)
                    continue
                raise SystemExit(qid + ': SENT without durable result; reconcile events, never resend automatically')
            result = run_question(args.system, q, client, ledger)
            print(qid, result['status'], 'calls=' + str(result['model_call_count']), flush=True)
            if args.pause_on_ne and result['status'] == 'NE':
                print('PAUSE_AFTER_NE', qid, result.get('error'), flush=True)
                raise SystemExit(3)
    finally:
        ledger.close()


if __name__ == '__main__':
    main()
