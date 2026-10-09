"""Queued synthetic data generation publishes only validated, named worlds."""

from __future__ import annotations

import json
from queue import Empty

import pytest

from resolveai.bulk_jobs import generate_world
from resolveai.jobs import make_app
from scripts.run_celery_worker import queue_for_kind


CLOCK = "2026-09-29T12:00:00+00:00"


def test_bulk_world_job_publishes_validated_demo_and_reuses_exact_retry(tmp_path):
    app = make_app('bulk-test', f'sqlite:///{tmp_path / "jobs.db"}', 'memory://',
                   bulk_data_root=tmp_path / 'worlds')
    task = app.tasks['resolveai.jobs.generate_world']
    first = task.apply(args=['bulk-test', 'demo-one', 'demo', 7, CLOCK], throw=True).get()
    world = tmp_path / 'worlds' / 'demo-one'
    assert first == {'profile': 'demo', 'orders_checked': 25, 'violation_count': 0,
                     'output_name': 'demo-one'}
    assert world.is_dir()
    report = json.loads((world / 'data_quality_report.json').read_text(encoding='utf-8'))
    assert report['seed'] == 7
    assert report['quality_validation']['file_hashes_match'] is True
    original_report = (world / 'data_quality_report.json').read_bytes()
    assert task.apply(args=['bulk-test', 'demo-one', 'demo', 7, CLOCK], throw=True).get() == first
    assert (world / 'data_quality_report.json').read_bytes() == original_report


def test_bulk_generation_routes_away_from_refund_and_deadline_queue(tmp_path):
    app = make_app('bulk-routing', f'sqlite:///{tmp_path / "jobs.db"}', 'memory://',
                   bulk_data_root=tmp_path / 'worlds')
    bulk_queue = app.conf.task_default_queue.removesuffix('jobs') + 'bulk'
    assert app.amqp.queues[bulk_queue].routing_key != app.amqp.queues[app.conf.task_default_queue].routing_key
    with app.connection_for_read() as connection:
        bulk = connection.SimpleQueue(bulk_queue)
        operational = connection.SimpleQueue(app.conf.task_default_queue)
        try:
            app.tasks['resolveai.jobs.generate_world'].apply_async(
                args=('bulk-routing', 'demo-one', 'demo', 7, CLOCK))
            message = bulk.get(block=False)
            assert message.payload[0] == ['bulk-routing', 'demo-one', 'demo', 7, CLOCK]
            message.ack()
            with pytest.raises(Empty):
                operational.get(block=False)
        finally:
            bulk.close()
            operational.close()


def test_worker_queue_selection_keeps_bulk_off_operational_children(tmp_path):
    app = make_app('worker-routing', f'sqlite:///{tmp_path / "jobs.db"}', 'memory://')
    assert queue_for_kind(app, 'operational') == app.conf.task_default_queue
    assert queue_for_kind(app, 'bulk') == app.conf.task_default_queue.removesuffix('jobs') + 'bulk'
    with pytest.raises(ValueError, match='worker kind'):
        queue_for_kind(app, 'unknown')


def test_bulk_world_retry_rejects_different_generation_contract(tmp_path):
    root = tmp_path / 'worlds'
    generate_world(root, 'demo-one', 'demo', 7, CLOCK)
    with pytest.raises(ValueError, match='different generation contract'):
        generate_world(root, 'demo-one', 'demo', 8, CLOCK)


@pytest.mark.parametrize('name', ['', '../other', 'other/name', 'bad name', '.' * 2])
def test_bulk_world_rejects_unsafe_names_before_writing(tmp_path, name):
    root = tmp_path / 'worlds'
    with pytest.raises(ValueError, match='output name'):
        generate_world(root, name, 'demo', 7, CLOCK)
    assert not root.exists()


def test_bulk_world_rejects_partial_output_and_foreign_namespace(tmp_path):
    root = tmp_path / 'worlds'
    partial = root / 'demo-one'
    partial.mkdir(parents=True)
    (partial / 'note.txt').write_text('incomplete', encoding='utf-8')
    with pytest.raises(ValueError, match='incomplete'):
        generate_world(root, 'demo-one', 'demo', 7, CLOCK)
    assert (partial / 'note.txt').read_text(encoding='utf-8') == 'incomplete'
    app = make_app('bulk-test', f'sqlite:///{tmp_path / "jobs.db"}', 'memory://',
                   bulk_data_root=root)
    with pytest.raises(ValueError, match='namespace'):
        app.tasks['resolveai.jobs.generate_world'].apply(
            args=['other', 'demo-two', 'demo', 7, CLOCK], throw=True).get()
    assert not (root / 'demo-two').exists()


def test_bulk_world_rejects_symlink_output_before_reading_existing_world(tmp_path):
    generate_world(tmp_path, 'outside', 'demo', 7, CLOCK)
    root = tmp_path / 'worlds'
    root.mkdir()
    (root / 'demo-one').symlink_to(tmp_path / 'outside', target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        generate_world(root, 'demo-one', 'demo', 7, CLOCK)


@pytest.mark.parametrize('profile,seed,clock', [
    ('unknown', 7, CLOCK), ('demo', True, CLOCK), ('demo', -1, CLOCK),
    ('demo', 7, '2026-09-29T12:00:00'), ('demo', 7, 'not-a-clock'),
])
def test_bulk_world_rejects_invalid_generation_settings(tmp_path, profile, seed, clock):
    root = tmp_path / 'worlds'
    with pytest.raises(ValueError):
        generate_world(root, 'demo-one', profile, seed, clock)
    assert not root.exists()
