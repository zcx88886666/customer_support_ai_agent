"""Development dialogues through owned PostgreSQL, actual OIDC and MCP."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'apps/api'))
from evals.runners.run_business_oidc_postgres import available_port, database_url, process_env, spawn_api, wait_healthy
from evals.runners.run_intent_dialogue import DATASET, load_cases, score as dialogue_score
from resolveai import domain as d, models as m
from resolveai.db import make_engine
from resolveai.models_config import ModelRegistry
from resolveai.policy_retrieval import index_bundle
from resolveai.prompts import PromptRegistry
from resolveai.seed import seed_demo
from scripts.verify_oidc import login
from scripts.verify_postgres_server_crash import DisposablePostgres, save_failure


def customer_account(customer_id):
    accounts = {'cust-01': 'customer-one', 'cust-02': 'customer-two'}
    if customer_id not in accounts:
        raise ValueError('Dialogue fixture requires an explicit supported demo customer')
    return accounts[customer_id]


def replay_environment(url, namespace, *, live_model=False):
    env = process_env(url)
    env.update({'LONG_TERM_MEMORY_MODE': 'off', 'JOB_NAMESPACE': namespace, 'AGENT_REQUEST_TIMEOUT_SECONDS': '25',
                'PROMPT_RELEASE': os.environ.get('PROMPT_RELEASE', 'specialists-dev-v1')})
    if live_model:
        key = os.environ.get('OPENROUTER_API_KEY')
        if not key:
            raise ValueError('Live OIDC dialogue replay requires a local OpenRouter key')
        env['OPENROUTER_API_KEY'] = key
    return env


def seed_extra_package(db, case, at):
    extra = case['fixture'].get('extra_shipment')
    if not extra:
        return
    order = d.owned_order(db, case['fixture']['customer_id'], extra['order_id'])
    if extra['order_id'] != case['group_keys']['order']:
        raise ValueError('Extra package must belong to the declared fixture order')
    shipped_at, delivered_at = at - timedelta(days=2), at - timedelta(days=1)
    if d.aware(order.placed_at) >= shipped_at:
        raise ValueError('Extra package fixture chronology is invalid')
    db.add(m.Shipment(id=extra['id'], order_id=order.id, status='delivered', delivered_at=delivered_at, version=1))
    db.flush()
    for status, occurred_at in (('shipped', shipped_at), ('delivered', delivered_at)):
        db.add(m.ShipmentEvent(shipment_id=extra['id'], status=status, occurred_at=occurred_at))


def public_turn(row):
    fields = ('http_status', 'status', 'code', 'route', 'intents', 'plan_revision', 'shipment_options',
              'trace_id', 'return_count', 'ledger_count', 'ticket_count', 'model_attempts',
              'reported_cost_usd', 'reported_input_tokens', 'reported_output_tokens', 'unknown_usage_calls')
    return {key: row[key] for key in fields if key in row}


def live_cost_reached(rows, maximum):
    return sum(row.get('provider_usage', {}).get('reported_cost_usd', 0) for row in rows) >= maximum


def summarize(cases, rows, *, cancelled=False, live_model=False):
    expected = {case['case_id'] for case in cases}
    actual = {row['case_id']: row for row in rows}
    complete = len(rows) == len(cases) and set(actual) == expected
    counts = Counter(row['status'] for row in rows)
    missing = len(expected - set(actual))
    if missing:
        counts['incomplete'] += missing
    return {'suite': 'intent_dialogue_dev_v1_oidc_postgres' + ('_live_model' if live_model else ''),
            'unique_cases': len(cases),
            'executions': len(rows), 'counts': dict(counts), 'cancelled': cancelled,
            'critical_failures': [case['case_id'] for case in cases if case['risk_tier'] == 'critical'
                                  and actual.get(case['case_id'], {}).get('status') != 'pass'],
            'development_pass': complete and not cancelled and all(row['status'] == 'pass' for row in rows),
            'release_gate_pass': False}


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def save_case_failure(folder, case_id, error):
    case_folder = folder / case_id
    case_folder.mkdir(exist_ok=True)
    save_failure(case_folder, error)
    return str((case_folder / 'error.json').relative_to(folder))


def run_case(case, run_id, server, tokens, folder, seed_clock, *, live_model=False):
    name = 'ra_dialogue_' + uuid4().hex[:16]
    account = customer_account(case['fixture']['customer_id'])
    with psycopg.connect(server.admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url = database_url(server.admin_url, name, sqlalchemy=True)
    env = replay_environment(url, name, live_model=live_model)
    engine = make_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    processes, turns, checks = [], [], {}
    row = {'case_id': case['case_id'], 'risk_tier': case['risk_tier'], 'split': 'dev',
           'database': name, 'status': 'incomplete', 'checks': checks, 'turns': [], 'api_restarts': 0}
    started = time.monotonic()
    try:
        with (folder / (case['case_id'] + '.migration.log')).open('w') as log:
            subprocess.run([str(Path(sys.executable).with_name('alembic')), 'upgrade', 'head'], cwd=ROOT,
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
        with factory.begin() as db:
            seed_demo(db, seed_clock)
            seed_extra_package(db, case, seed_clock)
            index_bundle(db, 'policy-demo-v1')
        api_port, mcp_port = available_port(), available_port()
        env['COMMERCE_MCP_URL'] = f'http://127.0.0.1:{mcp_port}/mcp'
        with (folder / (case['case_id'] + '.api.log')).open('w') as api_log, (folder / (case['case_id'] + '.mcp.log')).open('w') as mcp_log:
            mcp = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'services.commerce_mcp.server:app',
                                    '--host', '127.0.0.1', '--port', str(mcp_port), '--no-access-log'],
                                   cwd=ROOT, env=env, stdout=mcp_log, stderr=subprocess.STDOUT)
            processes.append(mcp)
            api = spawn_api(api_port, env, api_log)
            processes.append(api)
            wait_healthy(api_port, api)
            with httpx.Client(base_url=f'http://127.0.0.1:{api_port}', trust_env=False, timeout=30) as client:
                headers = {'Authorization': 'Bearer ' + tokens[account], 'x-eval-run-id': run_id,
                           'x-eval-case-id': case['case_id']}
                checks['mock_headers_rejected'] = client.get('/orders', headers={'x-mock-actor': case['fixture']['customer_id'], 'x-mock-role': 'customer'}).status_code == 401
                for index, turn in enumerate(case['dialogue_script']):
                    if index == 1:
                        stop(api)
                        api = spawn_api(api_port, env, api_log)
                        processes.append(api)
                        wait_healthy(api_port, api)
                        row['api_restarts'] += 1
                    response = client.post('/chat', json={**turn, 'thread_id': case['case_id'],
                                           'agent_mode': case['fixture'].get('agent_mode', 'single')}, headers=headers)
                    payload = response.json()
                    usage = payload.get('resource_usage') or {}
                    with factory() as db:
                        counts = {key: db.scalar(select(func.count()).select_from(model)) for key, model in
                                  (('return_count', m.ReturnRequest), ('ledger_count', m.RefundLedger), ('ticket_count', m.Ticket))}
                    turns.append({'http_status': response.status_code, 'status': payload.get('status'),
                                  'code': payload.get('code'), 'route': payload.get('route', {}).get('route'),
                                  'intents': payload.get('route', {}).get('intents', []),
                                  'plan_revision': payload.get('plan_revision'), 'answer': payload.get('answer', ''),
                                  'shipment_options': payload.get('shipment_options', []),
                                  'trace_id': response.headers.get('x-trace-id'),
                                  'model_attempts': usage.get('llm_attempts'),
                                  'reported_cost_usd': usage.get('reported_cost_usd'),
                                  'reported_input_tokens': usage.get('reported_input_tokens'),
                                  'reported_output_tokens': usage.get('reported_output_tokens'),
                                  'unknown_usage_calls': usage.get('unknown_usage_calls'), **counts})
                other_account = 'customer-two' if account == 'customer-one' else 'customer-one'
                foreign = client.post('/chat', json={'thread_id': case['case_id'], 'message': '我要人工客服'},
                                      headers={'Authorization': 'Bearer ' + tokens[other_account]})
                checks['foreign_customer_cannot_resume_thread'] = foreign.status_code == 404
            with factory() as db:
                checks.update(dialogue_score(case, turns, db))
                thread = db.get(m.ThreadState, case['case_id'])
                checks['thread_owner_preserved'] = bool(thread and thread.customer_id == case['fixture']['customer_id'])
            if live_model:
                checks['live_usage_accounted'] = all(
                    type(turn['model_attempts']) is int and turn['model_attempts'] >= 0
                    and type(turn['reported_cost_usd']) in (int, float) and turn['reported_cost_usd'] >= 0
                    and turn['unknown_usage_calls'] == 0 for turn in turns if turn['http_status'] == 200)
            else:
                checks['no_paid_model_calls'] = all(turn['model_attempts'] == 0 for turn in turns if turn['http_status'] == 200)
            checks['restart_keeps_dialogue_state'] = row['api_restarts'] == int(len(turns) > 1)
            row['status'] = 'pass' if all(checks.values()) else 'fail'
    except (Exception, KeyboardInterrupt) as error:
        row['error_type'] = type(error).__name__
        row['error_log'] = save_case_failure(folder, case['case_id'], error)
    finally:
        cleanup_errors = []
        for process in reversed(processes):
            try:
                stop(process)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        engine.dispose()
        row['turns'] = [public_turn(turn) for turn in turns]
        row['seed_clock'] = seed_clock.isoformat()
        row['latency_ms'] = round((time.monotonic() - started) * 1000, 2)
        row['provider_usage'] = {
            'model_attempts': sum(turn['model_attempts'] or 0 for turn in turns),
            'reported_cost_usd': round(sum(turn['reported_cost_usd'] or 0 for turn in turns), 8),
            'unknown_usage_calls': sum(turn['unknown_usage_calls'] or 0 for turn in turns)}
        row['child_cleanup_errors'] = cleanup_errors
        if cleanup_errors:
            row['status'] = 'incomplete'
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-model', action='store_true', help='Use the configured model in the private OIDC API')
    parser.add_argument('--stop-after-reported-cost-usd', type=float, default=0.02,
                        help='Stop before the next case after this reported cost; one case may cross it')
    args = parser.parse_args()
    if not math.isfinite(args.stop_after_reported_cost_usd) or args.stop_after_reported_cost_usd <= 0:
        parser.error('The reported-cost stop must be finite and positive')
    if args.live_model:
        from evals.runners.run_paired_model import load_local_key
        load_local_key()
    cases = load_cases()
    registry = PromptRegistry(os.environ.get('PROMPT_RELEASE', 'specialists-dev-v1'))
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + ('-dialogue-oidc-live-' if args.live_model else '-dialogue-oidc-') + uuid4().hex[:6]
    folder = ROOT / 'evals/reports' / run_id
    folder.mkdir(parents=True)
    server = DisposablePostgres(run_id, folder)
    rows, error_type, cleanup, cancelled = [], None, {}, False
    seed_clock = datetime.now(timezone.utc)
    try:
        accounts = json.loads((ROOT / '.local/demo-accounts.json').read_text())
        tokens = {account: login(account, accounts[account]['password']) for account in ('customer-one', 'customer-two')}
        server.create()
        for case in cases:
            row = run_case(case, run_id, server, tokens, folder, seed_clock, live_model=args.live_model)
            rows.append(row)
            with (folder / 'case_results.jsonl').open('a') as output:
                output.write(json.dumps(row, ensure_ascii=False) + '\n')
            print(json.dumps({'case_id': row['case_id'], 'status': row['status']}), flush=True)
            if row.get('error_type') == 'KeyboardInterrupt':
                cancelled = True
                break
            if args.live_model and (live_cost_reached(rows, args.stop_after_reported_cost_usd)
                                    or row['provider_usage']['unknown_usage_calls']):
                break
    except (Exception, KeyboardInterrupt) as error:
        cancelled = isinstance(error, KeyboardInterrupt)
        error_type = type(error).__name__
        save_failure(folder, error)
    finally:
        summary = summarize(cases, rows, cancelled=cancelled, live_model=args.live_model)
        try:
            cleanup = server.cleanup(summary['development_pass'] and error_type is None)
        except Exception as error:
            error_type = type(error).__name__
            save_failure(folder, error)
    summary.update({'run_id': run_id, 'error_type': error_type, 'cleanup': cleanup,
                    'development_pass': summary['development_pass'] and error_type is None})
    if args.live_model:
        summary['provider_usage'] = {
            'model_attempts': sum(row['provider_usage']['model_attempts'] for row in rows),
            'reported_cost_usd': round(sum(row['provider_usage']['reported_cost_usd'] for row in rows), 8),
            'unknown_usage_calls': sum(row['provider_usage']['unknown_usage_calls'] for row in rows)}
    manifest = {'run_id': run_id, 'suite': summary['suite'], 'split': 'dev', 'synthetic': True,
                'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'seed_clock': seed_clock.isoformat(), 'auth': 'real-Keycloak-OIDC-code-PKCE',
                'database': 'fresh-migrated-PostgreSQL-per-case', 'mcp': 'private-stateless-HTTP-per-case',
                'model': ModelRegistry().get('intent').model if args.live_model else 'deterministic-no-key',
                'api_restart_after_first_turn': True,
                'stop_after_reported_cost_usd': args.stop_after_reported_cost_usd if args.live_model else None,
                'source_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in
                                  ('scripts/verify_intent_dialogue_oidc.py', 'evals/runners/run_intent_dialogue.py',
                                   'evals/datasets/intent_dialogue_dev_v1.jsonl', 'apps/api/resolveai/agent.py')},
                'prompt_release_id': registry.release_id, 'prompt_hashes': registry.manifest['prompts'],
                'container_id': server.container_id, 'image_id': server.image_id, 'resources': cleanup,
                'review_status': 'pending_independent_human_review', 'release_gate_pass': False}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    if not (folder / 'case_results.jsonl').exists():
        (folder / 'case_results.jsonl').write_text('')
    (folder / 'report.html').write_text("<!doctype html><meta charset='utf-8'><title>Real OIDC dialogue</title><pre>" + html.escape(json.dumps({'summary': summary, 'cases': rows}, ensure_ascii=False, indent=2)) + '</pre>')
    print(json.dumps({**summary, 'report_dir': str(folder)}))
    return 130 if cancelled else 0 if summary['development_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
