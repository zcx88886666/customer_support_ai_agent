"""Owned Redis/PostgreSQL queued-job and outage recovery development probe."""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'apps/api', ROOT / 'packages'):
    sys.path.insert(0, str(path))

import httpx
import psycopg
from psycopg import sql
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from kombu.exceptions import OperationalError as BrokerOperationalError
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from resolveai import domain as d, models as m
from resolveai.db import make_engine
from resolveai.jobs import make_app
from resolveai.policy_retrieval import index_current
from resolveai.seed import seed_demo
from scripts.verify_postgres_server_crash import DisposablePostgres, docker, save_failure


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def require_owned_redis(info, container_id, image_id, run_id, name, port):
    labels = info.get('Config', {}).get('Labels') or {}
    binding = info.get('HostConfig', {}).get('PortBindings', {}).get('6379/tcp') or []
    if (not container_id or info.get('Id') != container_id or info.get('Image') != image_id
            or info.get('Name') != '/' + name
            or labels.get('resolveai.verifier') != 'celery-jobs'
            or labels.get('resolveai.run_id') != run_id or labels.get('resolveai.synthetic') != 'true'
            or binding != [{'HostIp': '127.0.0.1', 'HostPort': str(port)}]
            or info.get('HostConfig', {}).get('Tmpfs', {}).get('/data') != 'rw,size=64m'
            or any(mount.get('Type') != 'tmpfs' or mount.get('Destination') != '/data' for mount in info.get('Mounts', []))):
        raise RuntimeError('Refusing unrelated or exposed Redis fixture')


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError('Isolated job condition did not complete')


def stop_owned_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-celery-' + uuid4().hex[:6]
    report = ROOT / 'evals/reports' / run_id
    report.mkdir(parents=True)
    pg_report = report / 'postgres'
    pg_report.mkdir()
    pg = DisposablePostgres(run_id, pg_report)
    redis_name = 'ra_jobs_redis_' + uuid4().hex[:16]
    redis_port = free_port()
    redis_id = redis_image = ''
    api = worker = beat = None
    engine = app = None
    checks = {}
    error_type = None
    cleanup = {}
    task_ids = []
    try:
        pg.create()
        redis_image = docker('image', 'inspect', 'redis:7-alpine', '--format', '{{.Id}}')
        redis_id = docker('run', '--detach', '--pull', 'never', '--name', redis_name,
                          '--label', 'resolveai.verifier=celery-jobs', '--label', 'resolveai.run_id=' + run_id,
                          '--label', 'resolveai.synthetic=true', '--memory', '96m', '--cpus', '.5',
                          '--publish', f'127.0.0.1:{redis_port}:6379', '--tmpfs', '/data:rw,size=64m',
                          redis_image, 'redis-server', '--appendonly', 'yes')
        def owned_redis():
            info = json.loads(docker('inspect', redis_id))[0]
            require_owned_redis(info, redis_id, redis_image, run_id, redis_name, redis_port)
            return info
        owned_redis()
        broker_url = f'redis://127.0.0.1:{redis_port}/0'
        redis = Redis.from_url(broker_url, socket_timeout=2, socket_connect_timeout=2)
        def redis_ready():
            try:
                return redis.ping()
            except Exception:
                return False
        wait_for(redis_ready)
        database = 'ra_jobs_' + uuid4().hex[:16]
        with psycopg.connect(pg.admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        url = pg.admin_url.replace('/postgres', '/' + database).replace('postgresql://', 'postgresql+psycopg://', 1)
        namespace = 'job-' + uuid4().hex[:16]
        env = {**os.environ, 'DATABASE_URL': url, 'CELERY_BROKER_URL': broker_url, 'JOB_NAMESPACE': namespace,
               'AUTH_MODE': 'mock', 'LONG_TERM_MEMORY_MODE': 'off', 'OPENROUTER_API_KEY': '',
               'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': '', 'OTEL_EXPORTER_OTLP_ENDPOINT': '',
               'OTEL_METRICS_EXPORTER': 'none', 'PROMPT_RELEASE': 'specialists-dev-v1',
               'PYTHONPATH': ':'.join(str(path) for path in (ROOT, ROOT / 'apps/api', ROOT / 'packages'))}
        with (report / 'migrations.log').open('w') as log:
            subprocess.run([str(Path(sys.executable).with_name('alembic')), 'upgrade', 'head'], env=env, cwd=ROOT,
                           stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
        engine = make_engine(url)
        factory = sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        with factory.begin() as db:
            seed_demo(db, now)
            proposals = []
            for number in (1, 6):
                key = str(number).zfill(2)
                request = d.create_return(db, 'cust-01', 'demo-order-' + key, 'demo-item-' + key, 1,
                                          'synthetic job', True, 'jobs-return-' + key, now)
                d.record_receipt(db, 'warehouse-job', request.id, 1, now)
                d.record_inspection(db, 'warehouse-job', request.id, True, 'intact', now)
                proposals.append(d.create_proposal(db, request.id, now).id)
            for number, age in ((3, 6.5), (4, 8)):
                key = str(number).zfill(2)
                request = d.create_return(db, 'cust-01', 'demo-order-' + key, 'demo-item-' + key, 1,
                                          'synthetic alert', True, 'jobs-return-' + key, now)
                receipt = d.record_receipt(db, 'warehouse-job', request.id, 1, now)
                receipt.received_at = now - timedelta(days=age)
        port = free_port()
        api_url = f'http://127.0.0.1:{port}'
        def start_process(args, log_name):
            with (report / log_name).open('a') as log:
                return subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        api = start_process(['-m', 'uvicorn', 'resolveai.api:app', '--host', '127.0.0.1', '--port', str(port)], 'api.log')
        def api_ready():
            if api.poll() is not None:
                raise RuntimeError('Isolated API exited')
            try:
                return httpx.get(api_url + '/health', timeout=1).status_code == 200
            except httpx.HTTPError:
                return False
        wait_for(api_ready)
        app = make_app(namespace, url, broker_url)
        worker_args = ['-m', 'celery', '-A', 'resolveai.jobs:app', 'worker', '--loglevel=INFO', '--concurrency=2',
                       '--without-gossip', '--without-mingle', '--hostname=isolated-jobs@%h']
        worker = start_process(worker_args, 'worker.log')
        def worker_ready():
            if worker.poll() is not None:
                raise RuntimeError('Isolated Celery worker exited')
            try:
                with app.connection_for_read() as connection:
                    return bool(app.control.ping(timeout=1, connection=connection))
            except (RedisConnectionError, BrokerOperationalError, OSError):
                return False
        wait_for(worker_ready)
        def send(name, sent_namespace=namespace):
            task_id = app.send_task('resolveai.jobs.' + name, args=[sent_namespace], expires=90).id
            task_ids.append(task_id)
            return task_id
        def wait_task(task_id, failed=False):
            def complete():
                lines = (report / 'worker.log').read_text().splitlines()
                return any(task_id in line and ('raised unexpected' if failed else 'succeeded') in line for line in lines)
            wait_for(complete)
        def ledger_count():
            with factory() as db:
                return db.scalar(select(func.count()).select_from(m.RefundLedger))
        def approve(proposal_id):
            with httpx.Client(timeout=15) as client:
                response = client.post(api_url + '/supervisor/proposals/' + proposal_id + '/decision',
                                       json={'approve': True}, headers={'x-mock-actor': 'supervisor-job', 'x-mock-role': 'supervisor'})
                response.raise_for_status()
            return response.status_code == 200
        wait_task(send('refunds'))
        checks['unapproved_jobs_create_no_ledger'] = ledger_count() == 0
        checks['approval_http_commits'] = approve(proposals[0])
        wait_task(send('refunds', 'foreign-case'), failed=True)
        checks['wrong_namespace_creates_no_ledger'] = ledger_count() == 0
        foreign = make_app(namespace, url + '_other', broker_url)
        foreign_id = foreign.send_task('resolveai.jobs.refunds', args=[namespace], expires=90).id
        task_ids.append(foreign_id)
        time.sleep(1)
        checks['other_database_queue_unconsumed'] = redis.llen(
            foreign.conf.broker_transport_options['global_keyprefix'] + foreign.conf.task_default_queue) == 1 and ledger_count() == 0
        duplicate_ids = [send('refunds'), send('refunds')]
        for task_id in duplicate_ids:
            wait_task(task_id)
        checks['duplicate_jobs_issue_once'] = ledger_count() == 1
        for task_id in [send('deadlines'), send('deadlines')]:
            wait_task(task_id)
        with factory() as db:
            checks['deadline_jobs_deduplicate'] = db.scalar(select(func.count()).select_from(m.RefundDeadlineAlert)) == 2
            checks['deadline_kinds_correct'] = {a.kind for a in db.scalars(select(m.RefundDeadlineAlert)).all()} == {'due_soon', 'overdue'}
        wait_task(send('policy_index'))
        with factory() as db:
            checks['queued_policy_index_current'] = index_current(db, 'policy-demo-v1')
        stop_owned_process(worker)
        replay_id = send('refunds')
        worker = start_process(worker_args, 'worker.log')
        wait_for(worker_ready)
        wait_task(replay_id)
        checks['worker_restart_replays_without_duplicate'] = ledger_count() == 1
        owned_redis()
        docker('stop', '--time', '2', redis_id)
        checks['approval_commits_during_broker_outage'] = approve(proposals[1])
        with factory() as db:
            checks['outage_approval_stays_authorized_in_sql'] = db.get(m.RefundProposal, proposals[1]).status == 'approved' and ledger_count() == 1
        # The tmpfs broker intentionally loses its queue on restart. SQL and
        # a normal thirty-second Beat scan must recover the approved work.
        owned_redis()
        docker('start', redis_id)
        wait_for(redis_ready)
        # The verifier's pre-outage control transport may retain a dead
        # socket. Recreate its client independently of worker recovery.
        app.close()
        app = make_app(namespace, url, broker_url)
        stop_owned_process(worker)
        worker = start_process(worker_args, 'worker.log')
        wait_for(worker_ready)
        beat = start_process(['-m', 'celery', '-A', 'resolveai.jobs:app', 'beat', '--loglevel=INFO',
                              '--schedule=' + str(report / 'beat-schedule')], 'beat.log')
        wait_for(lambda: ledger_count() == 2, timeout=65)
        checks['beat_recovers_approved_sql_after_queue_loss'] = ledger_count() == 2
        stop_owned_process(beat)
        beat = None
        wait_task(send('refunds'))
        with factory() as db:
            ledgers = db.scalars(select(m.RefundLedger)).all()
            audits = db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == 'issue_refund')).all()
            checks['final_two_unique_authorized_ledgers'] = len(ledgers) == len(audits) == 2 and len({x.proposal_id for x in ledgers}) == 2
            checks['money_quantity_and_approval_match'] = all(
                db.get(m.OrderItem, entry.order_item_id).refunded_cents == entry.amount_cents == db.get(m.RefundProposal, entry.proposal_id).amount_cents
                and db.get(m.OrderItem, entry.order_item_id).refunded_quantity == 1
                and db.scalar(select(m.Approval).where(m.Approval.proposal_id == entry.proposal_id)).decision == 'approved'
                for entry in ledgers)
            checks['final_alerts_and_audits_deduplicate'] = (
                db.scalar(select(func.count()).select_from(m.RefundDeadlineAlert)) == 2
                and db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action.like('refund_deadline_%'))) == 2)
    except Exception as exc:
        error_type = type(exc).__name__
        save_failure(report, exc)
    finally:
        for process in (beat, worker, api):
            try:
                stop_owned_process(process)
            except Exception as exc:
                error_type = type(exc).__name__
                save_failure(report, exc)
        if engine:
            engine.dispose()
        if app:
            app.close()
        success = bool(checks and all(checks.values()) and not error_type)
        try:
            if redis_id:
                info = json.loads(docker('inspect', redis_id))[0]
                require_owned_redis(info, redis_id, redis_image, run_id, redis_name, redis_port)
                (report / 'redis.log').write_text(docker('logs', redis_id) + '\n')
                if success:
                    docker('rm', '--force', redis_id)
                elif info['State']['Running']:
                    docker('stop', '--time', '2', redis_id)
                cleanup['redis_removed'] = success
        except Exception as exc:
            error_type = type(exc).__name__
            save_failure(report, exc)
        try:
            cleanup['postgres'] = pg.cleanup(success=bool(success and not error_type))
        except Exception as exc:
            error_type = type(exc).__name__
            save_failure(pg_report, exc)
    passed = bool(checks and all(checks.values()) and not error_type)
    manifest = {'run_id': run_id, 'suite': 'celery-jobs-v1', 'split': 'dev', 'synthetic': True,
                'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'hashes': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                    Path(__file__), ROOT / 'apps/api/resolveai/jobs.py', ROOT / 'apps/api/resolveai/worker.py')},
                'postgres_id': pg.container_id, 'postgres_image': pg.image_id, 'redis_id': redis_id, 'redis_image': redis_image,
                'task_ids': task_ids, 'cleanup': cleanup, 'locked_release_ready': False}
    summary = {'run_id': run_id, 'cases': 1, 'passed': int(passed), 'incomplete': int(not passed),
               'failed_checks': sum(not value for value in checks.values()), 'checks': checks, 'error_type': error_type,
               'cleanup': cleanup, 'locked_release_ready': False}
    (report / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (report / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (report / 'case_results.jsonl').write_text(json.dumps(summary) + '\n')
    (report / 'report.html').write_text('<!doctype html><meta charset=utf-8><title>Celery jobs</title><pre>' + html.escape(json.dumps(summary, indent=2)) + '</pre>')
    print(json.dumps({**summary, 'report_dir': str(report)}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
