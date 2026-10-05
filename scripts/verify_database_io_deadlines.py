"""Owned synthetic PostgreSQL proof of pool, connector and checkpoint deadlines."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import TypedDict
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'apps/api'))
from scripts.verify_postgres_server_crash import DisposablePostgres, save_failure


def child():
    from langgraph.graph import END, START, StateGraph
    from sqlalchemy import func, select, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import Session
    from resolveai import checkpoint, models as m
    from resolveai.agent import run_chat
    from resolveai.database_io import BudgetQueuePool, connect_with_deadline
    from resolveai.db import engine, SessionLocal
    from resolveai.request_budget import BudgetExceeded, BudgetLimits, RequestBudget, budget_scope
    from resolveai.schemas import ChatInput
    from resolveai.seed import seed_demo

    uri = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
    checks, measured = {}, {}
    def budget(seconds=0.2):
        return budget_scope(RequestBudget(BudgetLimits(timeout_seconds=seconds)))
    def cancelled(label, function, error_type=BudgetExceeded):
        started = time.monotonic()
        try:
            with budget():
                function()
        except error_type:
            elapsed = time.monotonic() - started
            measured[label + '_seconds'] = round(elapsed, 4)
            checks[label + '_bounded'] = 0.16 <= elapsed < 0.5
        else:
            checks[label + '_bounded'] = False

    with SessionLocal.begin() as db:
        seed_demo(db, datetime.now(timezone.utc))
    checkpoint.setup_checkpointer()

    pool = BudgetQueuePool(lambda: connect_with_deadline(uri), pool_size=1, max_overflow=0,
                           timeout=3, reset_on_return=None)
    held = pool.connect()
    try:
        cancelled('pool_admission', pool.connect)
    finally:
        held.close()
    with pool.connect() as connection:
        checks['pool_recovers_without_timeout_mutation'] = connection.cursor().execute('SELECT 1').fetchone() == (1,) and pool.timeout() == 3
    pool.dispose()

    with Session(engine) as db:
        db.execute(text('SELECT 1'))
        def slow_business():
            try:
                db.execute(text('SELECT pg_sleep(0.6)'))
            except DBAPIError as error:
                checks['business_timeout_sqlstate'] = error.orig.sqlstate == '57014'
                raise
        cancelled('business_statement', slow_business, DBAPIError)
        db.rollback()
        checks['sqlalchemy_rollback_and_fresh_reuse'] = db.execute(text('SELECT 1')).scalar_one() == 1
        db.rollback()
    # No server statement timeout: prove the client's socket deadline itself.
    direct = connect_with_deadline(uri, autocommit=True)
    try:
        cancelled('connected_socket_wait', lambda: direct.execute('SELECT pg_sleep(0.6)'), psycopg.errors.QueryCanceled)
        checks['expired_socket_connection_closed'] = direct.closed
    finally:
        direct.close()

    class State(TypedDict):
        value: str
    builder = StateGraph(State)
    builder.add_node('observe', lambda state: {'value': state['value']})
    builder.add_edge(START, 'observe')
    builder.add_edge('observe', END)
    config = {'configurable': {'thread_id': 'deadline-probe'}}
    with checkpoint.parent_checkpointer() as saver:
        graph = builder.compile(checkpointer=saver)
        checks['checkpoint_initial_roundtrip'] = graph.invoke({'value': 'synthetic'}, config)['value'] == 'synthetic'
    with psycopg.connect(uri) as blocker:
        blocker.execute('LOCK TABLE checkpoints IN ACCESS EXCLUSIVE MODE')
        def read_checkpoint():
            with checkpoint.parent_checkpointer() as saver:
                saver.get_tuple(config)
        cancelled('checkpoint_read_lock', read_checkpoint)
        blocker.rollback()
    with checkpoint.parent_checkpointer() as saver:
        checks['checkpoint_read_after_timeout'] = builder.compile(checkpointer=saver).get_state(config).values == {'value': 'synthetic'}
    with psycopg.connect(uri) as blocker:
        blocker.execute('LOCK TABLE checkpoint_writes IN ACCESS EXCLUSIVE MODE')
        def background_write():
            with checkpoint.parent_checkpointer() as saver:
                builder.compile(checkpointer=saver).invoke({'value': 'synthetic'}, config)
        cancelled('checkpoint_background_write_lock', background_write)
        blocker.rollback()
    with checkpoint.parent_checkpointer() as saver:
        graph = builder.compile(checkpointer=saver)
        checks['checkpoint_fresh_resume_coherent'] = graph.invoke(None, config)['value'] == 'synthetic' and not graph.get_state(config).next

    # Actual parent checkpoint read lock must produce the existing safe handoff.
    with psycopg.connect(uri) as blocker:
        blocker.execute('LOCK TABLE checkpoints IN ACCESS EXCLUSIVE MODE')
        started = time.monotonic()
        with SessionLocal() as db:
            result = run_chat(db, 'cust-01', ChatInput(thread_id='deadline-handoff', message='包裹到了吗', order_id='demo-order-02'))
            db.commit()
        elapsed = time.monotonic() - started
        measured['chat_handoff_seconds'] = round(elapsed, 4)
        checks['chat_checkpoint_timeout_safe_handoff'] = result['status'] == 'handoff' and result['findings'] == [] and elapsed < 0.8
        checks['chat_handoff_uses_no_models'] = result['resource_usage']['llm_attempts'] == 0
        blocker.rollback()

    # A real TCP peer accepts but never answers libpq's initial handshake.
    accepted, finish = threading.Event(), threading.Event()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(2)
    def peer():
        with listener:
            connection, _ = listener.accept()
            with connection:
                accepted.set()
                finish.wait(timeout=3)
    thread = threading.Thread(target=peer, daemon=True)
    thread.start()
    try:
        stalled_uri = 'postgresql://synthetic@127.0.0.1:' + str(listener.getsockname()[1]) + '/synthetic?sslmode=disable'
        cancelled('connection_handshake', lambda: connect_with_deadline(stalled_uri))
        checks['stalled_peer_accepted'] = accepted.is_set()
    finally:
        finish.set()
        thread.join(timeout=2)
    with SessionLocal() as db:
        checks['no_return_refund_or_approval_mutations'] = all(db.scalar(select(func.count()).select_from(model)) == 0 for model in (m.ReturnRequest, m.RefundProposal, m.Approval, m.RefundLedger))
        checks['one_owned_handoff_ticket'] = db.scalar(select(func.count()).select_from(m.Ticket)) == 1 and db.get(m.ThreadState, 'deadline-handoff').customer_id == 'cust-01'
        checks['no_business_action_audits'] = db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action.in_(['create_return', 'approve_refund', 'issue_refund']))) == 0
    engine.dispose()
    print(json.dumps({'checks': checks, 'measurements': measured}))
    return 0 if all(checks.values()) else 1


def main():
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-db-deadline-' + uuid4().hex[:6]
    report_dir = ROOT / 'evals/reports' / run_id
    report_dir.mkdir(parents=True)
    server = DisposablePostgres(run_id, report_dir)
    result, error_type, cleanup = {}, None, {}
    completed, cancelled_run = False, False
    process = None
    try:
        server.create()
        database = 'io_deadlines_' + uuid4().hex[:12]
        with psycopg.connect(server.admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        url = server.admin_url.rsplit('/', 1)[0] + '/' + database
        env = {**os.environ, 'DATABASE_URL': url.replace('postgresql://', 'postgresql+psycopg://', 1),
               'AUTH_MODE': 'mock', 'AGENT_REQUEST_TIMEOUT_SECONDS': '0.25', 'LONG_TERM_MEMORY_MODE': 'off',
               'OPENROUTER_API_KEY': '', 'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': '',
               'OTEL_EXPORTER_OTLP_ENDPOINT': '', 'OTEL_METRICS_EXPORTER': 'none'}
        with (report_dir / 'migration.log').open('w') as log:
            subprocess.run([str(Path(sys.executable).with_name('alembic')), 'upgrade', 'head'], cwd=ROOT,
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
        with (report_dir / 'probe.log').open('w') as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child'], cwd=ROOT,
                                       env=env, stdout=subprocess.PIPE, stderr=log, text=True)
            output, _ = process.communicate(timeout=30)
            log.write(output)
            result = json.loads(output)
            completed = process.returncode == 0 and bool(result['checks']) and all(result['checks'].values())
    except (Exception, KeyboardInterrupt) as error:
        cancelled_run = isinstance(error, KeyboardInterrupt)
        error_type = type(error).__name__
        save_failure(report_dir, error)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        try:
            cleanup = server.cleanup(completed and error_type is None)
        except Exception as error:
            error_type = type(error).__name__
            save_failure(report_dir, error)
    checks = result.get('checks', {})
    summary = {'run_id': run_id, 'passed': completed and error_type is None,
               'checks': checks, 'measurements': result.get('measurements', {}),
               'incomplete': int(not completed), 'error_type': error_type, 'cleanup': cleanup, 'locked_release_ready': False}
    manifest = {'run_id': run_id, 'suite': 'database-io-deadline-v1', 'split': 'dev', 'synthetic': True,
                'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'source_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in
                                  ('apps/api/resolveai/database_io.py', 'apps/api/resolveai/db.py',
                                   'apps/api/resolveai/checkpoint.py', 'scripts/verify_database_io_deadlines.py')},
                'container_id': server.container_id, 'image_id': server.image_id, 'resources': cleanup}
    (report_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (report_dir / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (report_dir / 'case_results.jsonl').write_text(json.dumps(summary) + '\n')
    (report_dir / 'report.html').write_text("<!doctype html><meta charset='utf-8'><title>Database I/O deadlines</title><pre>" + html.escape(json.dumps(summary, indent=2)) + '</pre>')
    print(json.dumps({**summary, 'report_dir': str(report_dir)}))
    return 130 if cancelled_run else 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(child() if '--child' in sys.argv else main())
