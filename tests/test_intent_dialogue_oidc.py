"""Same development dialogue labels across mock and actual OIDC transports."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from evals.runners import run_intent_dialogue
from resolveai import models as m
from scripts import verify_intent_dialogue_oidc as probe


def test_only_explicit_demo_customer_accounts_are_supported():
    assert probe.customer_account('cust-01') == 'customer-one'
    assert probe.customer_account('cust-02') == 'customer-two'
    with pytest.raises(ValueError):
        probe.customer_account('unknown-customer')


def test_extra_package_fixture_preserves_causal_events(session_factory):
    case = next(c for c in run_intent_dialogue.load_cases() if c['case_id'] == 'intent-dialogue-package-choice')
    at = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
    with session_factory.begin() as db:
        probe.seed_extra_package(db, case, at)
    with session_factory() as db:
        shipment = db.get(m.Shipment, case['fixture']['extra_shipment']['id'])
        events = db.scalars(select(m.ShipmentEvent).where(m.ShipmentEvent.shipment_id == shipment.id)
                            .order_by(m.ShipmentEvent.occurred_at)).all()
        assert [e.status for e in events] == ['shipped', 'delivered']
        assert events[0].occurred_at < events[1].occurred_at == shipment.delivered_at
        assert db.get(m.Order, shipment.order_id).placed_at < events[0].occurred_at


def test_incomplete_or_cancelled_probe_never_reports_all_cases_passing():
    cases = run_intent_dialogue.load_cases()
    partial = [{'case_id': cases[0]['case_id'], 'risk_tier': cases[0]['risk_tier'], 'status': 'pass', 'checks': {'one': True}}]
    summary = probe.summarize(cases, partial, cancelled=True)
    assert summary['unique_cases'] == len(cases) and summary['executions'] == 1
    assert summary['counts'] == {'pass': 1, 'incomplete': len(cases) - 1}
    assert summary['development_pass'] is False and summary['release_gate_pass'] is False
    assert cases[1]['case_id'] in summary['critical_failures']


def test_public_turn_report_omits_raw_answer_and_token():
    row = {'http_status': 200, 'answer': 'synthetic private answer', 'status': 'answered', 'token': 'synthetic-secret',
           'trace_id': 'test-trace', 'return_count': 0}
    public = probe.public_turn(row)
    assert 'answer' not in public and 'token' not in public
    assert public['trace_id'] == 'test-trace' and public['return_count'] == 0


def test_replay_environment_locks_prompt_and_disables_external_calls(monkeypatch):
    for key in ('OPENROUTER_API_KEY', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY'):
        monkeypatch.setenv(key, 'synthetic-secret')
    monkeypatch.delenv('PROMPT_RELEASE', raising=False)
    env = probe.replay_environment('postgresql+psycopg://synthetic@localhost:5432/synthetic', 'fixture-namespace')
    assert env['PROMPT_RELEASE'] == 'specialists-dev-v1'
    assert env['AUTH_MODE'] == 'oidc' and env['JOB_NAMESPACE'] == 'fixture-namespace'
    assert all(env[key] == '' for key in ('OPENROUTER_API_KEY', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY'))


def test_live_replay_passes_only_model_key_to_private_api(monkeypatch):
    for key in ('OPENROUTER_API_KEY', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY'):
        monkeypatch.setenv(key, 'synthetic-secret')
    env = probe.replay_environment('postgresql+psycopg://synthetic@localhost:5432/synthetic',
                                   'fixture-namespace', live_model=True)
    assert env['OPENROUTER_API_KEY'] == 'synthetic-secret'
    assert env['LANGFUSE_PUBLIC_KEY'] == env['LANGFUSE_SECRET_KEY'] == ''


def test_live_cost_stop_marks_missing_cases_incomplete():
    cases = run_intent_dialogue.load_cases()
    rows = [{'case_id': cases[0]['case_id'], 'risk_tier': cases[0]['risk_tier'], 'status': 'pass',
             'provider_usage': {'reported_cost_usd': 0.03, 'model_attempts': 2, 'unknown_usage_calls': 0}}]
    assert probe.live_cost_reached(rows, 0.02)
    summary = probe.summarize(cases, rows, live_model=True)
    assert summary['counts'] == {'pass': 1, 'incomplete': len(cases) - 1}
    assert summary['development_pass'] is False and summary['release_gate_pass'] is False


def test_same_scorer_rejects_changed_per_turn_state_and_ledger_count(db):
    case = next(c for c in run_intent_dialogue.load_cases() if c['case_id'] == 'intent-dialogue-explicit-human')
    db.add(m.Ticket(customer_id='cust-01', topic='synthetic human request'))
    from resolveai.domain import audit
    audit(db, 'cust-01', 'create_ticket', 'ticket', 'synthetic-ticket')
    db.flush()
    turns = [{'http_status': 200, 'status': 'handoff', 'return_count': 0, 'ledger_count': 0, 'ticket_count': 1}]
    assert all(probe.dialogue_score(case, turns, db).values())
    assert not all(probe.dialogue_score(case, [{**turns[0], 'ledger_count': 1}], db).values())
    assert not all(probe.dialogue_score(case, [{**turns[0], 'status': 'answered'}], db).values())


def test_each_case_failure_keeps_its_own_stack(tmp_path):
    for case_id, error_type in (('first', ValueError), ('second', RuntimeError)):
        try:
            raise error_type('synthetic ' + case_id)
        except Exception as error:
            probe.save_case_failure(tmp_path, case_id, error)
    assert (tmp_path / 'first' / 'error.json').exists()
    assert (tmp_path / 'second' / 'error.json').exists()
    assert (tmp_path / 'first' / 'error.json').read_text() != (tmp_path / 'second' / 'error.json').read_text()


def test_interrupt_keeps_active_case_turns_database_and_child_cleanup(tmp_path, monkeypatch):
    case = next(c for c in run_intent_dialogue.load_cases() if c['case_id'] == 'intent-dialogue-tracking-followup')
    class Admin:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, *_args): pass
    class Database:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def scalar(self, *_args): return 0
    class Factory:
        def begin(self): return Database()
        def __call__(self): return Database()
    class Engine:
        def dispose(self): pass
    class Process:
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0
    stopped = []
    def stop(process): stopped.append(process)
    class Response:
        status_code = 200
        headers = {}
        def json(self):
            return {'status': 'answered', 'answer': 'synthetic private first turn',
                    'route': {'route': 'knowledge', 'intents': []}, 'plan_revision': 1,
                    'resource_usage': {'llm_attempts': 0}}
    class Client:
        calls = 0
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, *_args, **_kwargs): return SimpleNamespace(status_code=401)
        def post(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise KeyboardInterrupt()
            return Response()
    monkeypatch.setattr(probe.psycopg, 'connect', lambda *_a, **_k: Admin())
    monkeypatch.setattr(probe, 'make_engine', lambda *_a: Engine())
    monkeypatch.setattr(probe, 'sessionmaker', lambda *_a, **_k: Factory())
    monkeypatch.setattr(probe, 'seed_demo', lambda *_a: None)
    monkeypatch.setattr(probe, 'seed_extra_package', lambda *_a: None)
    monkeypatch.setattr(probe, 'index_bundle', lambda *_a: None)
    monkeypatch.setattr(probe.subprocess, 'run', lambda *_a, **_k: None)
    monkeypatch.setattr(probe.subprocess, 'Popen', lambda *_a, **_k: Process())
    monkeypatch.setattr(probe, 'spawn_api', lambda *_a, **_k: Process())
    monkeypatch.setattr(probe, 'wait_healthy', lambda *_a: None)
    monkeypatch.setattr(probe, 'available_port', lambda: 12345)
    monkeypatch.setattr(probe.httpx, 'Client', Client)
    monkeypatch.setattr(probe, 'stop', stop)
    row = probe.run_case(case, 'synthetic-run', SimpleNamespace(admin_url='postgresql://synthetic@localhost/postgres'),
                         {'customer-one': 'synthetic-token', 'customer-two': 'synthetic-other'},
                         tmp_path, datetime.now(timezone.utc))
    assert row['status'] == 'incomplete' and row['error_type'] == 'KeyboardInterrupt'
    assert row['database'].startswith('ra_dialogue_')
    assert len(row['turns']) == 1 and row['turns'][0]['return_count'] == 0
    assert 'answer' not in row['turns'][0]
    assert len(set(stopped)) == 3  # old API, restarted API and MCP
    assert (tmp_path / case['case_id'] / 'error.json').exists()
