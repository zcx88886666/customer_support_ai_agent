"""Queued development evaluations use fixed no-key runners and local reports."""

from __future__ import annotations

import json

import pytest

from resolveai.evaluation_jobs import run_development_eval
from resolveai.jobs import make_app


def test_queued_policy_eval_writes_local_no_key_report(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'must-not-be-used')
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'must-not-be-used')
    report_root = tmp_path / 'queued-reports'
    result = run_development_eval('policy_rag', report_root, 'synthetic-job-1')
    assert result['status'] == 'pass'
    assert result['suite'] == 'policy_rag'
    assert result['provider_calls'] == 0
    assert result['child_report'].startswith('evals/reports/')
    summary = json.loads((report_root / 'synthetic-job-1' / 'summary.json').read_text(encoding='utf-8'))
    assert summary == result
    assert (report_root / 'synthetic-job-1' / 'runner.log').is_file()


@pytest.mark.parametrize('suite,job_id', [('custom_script', 'synthetic-job-1'),
                                          ('policy_rag', '../outside'),
                                          ('policy_rag', '')])
def test_queued_eval_rejects_unapproved_suite_or_output_name(tmp_path, suite, job_id):
    report_root = tmp_path / 'queued-reports'
    with pytest.raises(ValueError):
        run_development_eval(suite, report_root, job_id)
    assert not report_root.exists()


def test_eval_task_is_separate_from_refund_queue(tmp_path):
    app = make_app('eval-routing', f'sqlite:///{tmp_path / "jobs.db"}', 'memory://')
    bulk = app.conf.task_default_queue.removesuffix('jobs') + 'bulk'
    assert app.tasks['resolveai.jobs.development_eval'].queue == bulk
    assert app.amqp.queues[bulk].routing_key != app.amqp.queues[app.conf.task_default_queue].routing_key
