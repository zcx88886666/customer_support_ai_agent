"""Interrupting the actual verifier must retain an incomplete local report."""

import json

import pytest

from scripts import verify_celery_jobs as verifier


def test_cancelled_probe_preserves_resources_and_emits_incomplete_report(tmp_path, monkeypatch, capsys):
    cleanup_calls = []

    class InterruptedServer:
        container_id = image_id = ''

        def __init__(self, run_id, report_dir):
            pass

        def create(self):
            raise KeyboardInterrupt()

        def cleanup(self, success):
            cleanup_calls.append(success)
            return {'removed': False}

    monkeypatch.setattr(verifier, 'DisposablePostgres', InterruptedServer)
    monkeypatch.setattr(verifier, 'free_port', lambda: 12345)
    monkeypatch.setattr(verifier, 'REPORT_ROOT', tmp_path, raising=False)
    try:
        result = verifier.main()
    except KeyboardInterrupt:
        pytest.fail('Cancellation escaped without an incomplete report')
    assert result == 130
    assert cleanup_calls == [False]
    row = json.loads(capsys.readouterr().out.strip())
    assert row['passed'] == 0 and row['incomplete'] == 1 and row['error_type'] == 'KeyboardInterrupt'
    assert (tmp_path / row['run_id'] / 'summary.json').exists()


@pytest.mark.skipif(not __import__('os').environ.get('CELERY_CANCEL_TEST_DOCKER'), reason='requires owned Docker Redis/PostgreSQL cancellation fixtures')
def test_actual_sigint_after_job_success_keeps_incomplete_fixtures(tmp_path):
    import os
    from pathlib import Path
    import signal
    import subprocess
    import sys
    import time

    code = ('from pathlib import Path; import sys; from scripts import verify_celery_jobs as v; '
            'v.REPORT_ROOT=Path(sys.argv[1]); sys.exit(v.main())')
    log_path = tmp_path / 'launcher.log'
    with log_path.open('w') as log:
        process = subprocess.Popen([sys.executable, '-c', code, str(tmp_path)], cwd=verifier.ROOT,
                                   stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            assert process.poll() is None, log_path.read_text()
            folders = list(tmp_path.glob('*-celery-*'))
            if folders and (folders[0] / 'worker.log').exists():
                text = (folders[0] / 'worker.log').read_text()
                if 'resolveai.jobs.refunds[' in text and 'succeeded' in text:
                    time.sleep(0.1)
                    break
            time.sleep(0.1)
        else:
            pytest.fail('Owned probe never reached its first successful job')
        os.kill(process.pid, signal.SIGINT)
        assert process.wait(timeout=40) == 130, log_path.read_text()
        folder = folders[0]
        summary = json.loads((folder / 'summary.json').read_text())
        manifest = json.loads((folder / 'manifest.json').read_text())
        assert summary['passed'] == 0 and summary['incomplete'] == 1
        assert any(summary['checks'].values()) and summary['checks']['probe_completed'] is False
        assert summary['cleanup']['redis_removed'] is False
        assert summary['cleanup']['postgres']['removed'] is False
        for container_id in (manifest['redis_id'], manifest['postgres_id']):
            result = subprocess.run(['docker', 'inspect', container_id], text=True, capture_output=True, check=True, timeout=10)
            info = json.loads(result.stdout)[0]
            assert info['Config']['Labels']['resolveai.run_id'] == summary['run_id']
            assert info['State']['Running'] is False
        # Keep the successful negative-test fixture logs for operator review.
        print('Cancellation report:', folder)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=40)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
