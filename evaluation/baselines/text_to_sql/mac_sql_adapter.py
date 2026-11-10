"""MAC-SQL adapter: model transport, read-only connection, bounded SQL I/O.

The upstream prompts, talk methods, routing, pruning and MAX_ROUND are unchanged.
"""
import importlib
import sqlite3
import sys
import time
import types
from paths import ROOT, VENDOR, SCHEMA_DOC
from views import connect


class SQLTimeout(BaseException):
    pass


class SampleCursor:
    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.cursor()

    def execute(self, sql):
        deadline = time.monotonic() + 120
        self.connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        self.cursor.execute(sql)
        return self

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()


class SampleConnection:
    def __init__(self):
        self.connection = connect()

    @property
    def text_factory(self):
        return self.connection.text_factory

    @text_factory.setter
    def text_factory(self, value):
        self.connection.text_factory = value

    def cursor(self):
        return SampleCursor(self.connection)

    def close(self):
        self.connection.close()


def run_mac_sql(question, client, record_sql):
    schema_text = getattr(client, 'schema_doc_text', None)
    if schema_text is None:
        schema_text = SCHEMA_DOC.read_text(encoding='utf-8')
    sys.path.insert(0, str(VENDOR))
    # Supply the official API hook before import, so upstream's legacy retrying
    # core.llm and its openai==0.28 transport are never loaded.
    api = types.ModuleType('core.api')
    def safe_call_llm(prompt, **info):
        return client.call(prompt, stage=info.get('send_to') or 'Selector')
    api.safe_call_llm = safe_call_llm
    api.init_log_path = lambda path: None  # receipts are written by ModelClient
    sys.modules['core.api'] = api
    agents = importlib.import_module('core.agents')
    chat = importlib.import_module('core.chat_manager')
    agents.LLM_API_FUC = safe_call_llm
    connections = []

    def connect_views(ignored_path):
        conn = SampleConnection()
        connections.append(conn)
        return conn

    # Replace only agents' module-local DB handle, never sqlite3.connect globally.
    agents.sqlite3 = types.SimpleNamespace(connect=connect_views, Error=sqlite3.Error)
    client.sql_recorder = record_sql

    class AdaptedRefiner(agents.Refiner):
        def _execute_sql(self, sql, db_id):
            result = client.sql_recorder(sql)
            if result['timed_out']:
                raise SQLTimeout('SQL exceeded 120 seconds')
            # The official Refiner receives the same first-five-row interface.
            out = {'sql':sql, 'sqlite_error':result['error'] or '',
                   'exception_class':"<class 'sqlite3.Error'>" if result['error'] else ''}
            if result['error'] is None:
                out['data'] = [tuple(row) for row in result['rows'][:5]]
            return out

    class AdaptedManager(chat.ChatManager):
        def ping_network(self):
            # Do not spend an unrequested 'Hello world!' model call per question.
            return

        def _chat_single_round(self, message):
            super()._chat_single_round(message)
            client.ledger.event(client.qid, {'type':'mac_sql_state', 'message':message})

    # Override the execution boundary via subclass; Refiner.talk remains upstream.
    old_refiner = chat.Refiner
    chat.Refiner = AdaptedRefiner
    message = {'idx':question['baseline_ordinal'], 'db_id':'teachereval',
               'query':question['question'],
               'evidence':schema_text,
               'extracted_schema':{}, 'difficulty':'', 'ground_truth':'',
               'send_to':chat.SYSTEM_NAME}
    try:
        if not hasattr(client, 'mac_manager'):
            client.mac_manager = AdaptedManager(data_path=str(ROOT), tables_json_path=str(ROOT/'tables.json'),
                                               log_path='', model_name=client.identity['model'],
                                               dataset_name='bird', lazy=True, without_selector=False)
        manager = client.mac_manager
        manager.start(message)
    finally:
        chat.Refiner = old_refiner
        for conn in connections:
            conn.close()
    return message.get('pred', message.get('final_sql', '')), message
