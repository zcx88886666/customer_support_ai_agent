"""Same development dialogue labels across mock and actual OIDC transports."""

from datetime import datetime, timezone

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


def test_incomplete_or_cancelled_probe_never_reports_twelve_passes():
    cases = run_intent_dialogue.load_cases()
    partial = [{'case_id': cases[0]['case_id'], 'risk_tier': cases[0]['risk_tier'], 'status': 'pass', 'checks': {'one': True}}]
    summary = probe.summarize(cases, partial, cancelled=True)
    assert summary['unique_cases'] == 12 and summary['executions'] == 1
    assert summary['counts'] == {'pass': 1, 'incomplete': 11}
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
