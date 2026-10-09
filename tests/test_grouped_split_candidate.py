"""A future reviewed split must preserve fixture and template isolation."""

from __future__ import annotations

import csv
import hashlib
import json
import sys

import pytest

from scripts import prepare_minimum_review
from scripts import validate_minimum_review
from scripts.prepare_grouped_split import plan_grouped_split, prepare_candidate


def case(case_id, suite, group):
    return {"case_id": case_id, "suite": suite, "group_keys": {
        "customer": f"customer-{group}", "order": f"order-{group}",
        "source": f"source-{group}", "template": f"template-{group}",
        "family": f"family-{group}"}}


def test_grouped_split_keeps_shared_cross_suite_families_together():
    cases = [case(f"{group}-{suite}", suite, group)
             for group in "abcd" for suite in ("core_business", "intent_route")]
    result = plan_grouped_split(cases)
    assert len(result) == 8
    assert {result[row["case_id"]] for row in cases} == {"dev", "locked_candidate"}
    assert all(result[f"{group}-core_business"] == result[f"{group}-intent_route"]
               for group in "abcd")
    assert 0.30 <= list(result.values()).count("locked_candidate") / 8 <= 0.50
    assert all({result[f"{group}-{suite}"] for group in "abcd"} ==
               {"dev", "locked_candidate"} for suite in ("core_business", "intent_route"))


def test_grouped_split_rejects_suite_with_only_one_independent_family():
    cases = [case("core-a", "core_business", "a"), case("core-b", "core_business", "b"),
             case("intent-a", "intent_route", "a")]
    with pytest.raises(ValueError, match='independent group'):
        plan_grouped_split(cases)


def test_current_166_case_development_packet_cannot_be_relabelled_locked():
    cases, _ = prepare_minimum_review.load_packet_cases()
    assert len(cases) >= 166
    with pytest.raises(ValueError, match='independent group'):
        plan_grouped_split(cases)


def test_pending_review_packet_cannot_prepare_candidate(tmp_path, monkeypatch):
    packet = tmp_path / "packet"
    monkeypatch.setattr(sys, 'argv', ['prepare_minimum_review.py', '--output', str(packet)])
    prepare_minimum_review.main()
    with pytest.raises(ValueError, match='independent reviews'):
        prepare_candidate(packet, tmp_path / 'candidate')
    assert not (tmp_path / 'candidate').exists()


def test_complete_synthetic_forms_produce_only_candidate_split(tmp_path, monkeypatch):
    packet = tmp_path / 'packet'
    monkeypatch.setattr(sys, 'argv', ['prepare_minimum_review.py', '--output', str(packet)])
    prepare_minimum_review.main()
    case_path = packet / 'review_cases.jsonl'
    cases = [json.loads(line) for line in case_path.read_text(encoding='utf-8').splitlines()]
    source_meta = json.loads((packet / 'manifest.json').read_text(encoding='utf-8'))['source_datasets']
    suite_positions = {}
    for row in cases:
        position = suite_positions.get(row['suite'], 0)
        suite_positions[row['suite']] = position + 1
        group = str(position % 4)
        row['group_keys'] = {key: f'new-family-{group}' for key in
                             ('customer', 'order', 'source', 'template', 'family')}
    case_path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in cases),
                         encoding='utf-8')
    manifest_path = packet / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['packet_sha256'] = hashlib.sha256(case_path.read_bytes()).hexdigest()
    manifest['group_components_by_suite'] = prepare_minimum_review.group_component_counts(cases)
    sizes = prepare_minimum_review.group_component_sizes(cases)
    manifest['group_components_all_suites'] = len(sizes)
    manifest['largest_group_component_cases'] = max(sizes)
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.setattr(validate_minimum_review, 'load_packet_cases', lambda: (cases, source_meta))

    for filename, reviewer in (('reviewer_a.csv', 'human-a'), ('reviewer_b.csv', 'human-b')):
        path = packet / filename
        with path.open(encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        for row in rows:
            row.update({'reviewer_id': reviewer, 'decision': 'accept', 'notes': 'Reviewed',
                        'reviewed_at_utc': '2026-10-09T12:00:00Z'})
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    path = packet / 'adjudication.csv'
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        row.update({'adjudicator_id': 'human-c', 'final_decision': 'accept',
                    'notes': 'Accepted', 'reviewed_at_utc': '2026-10-09T13:00:00Z'})
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    output = tmp_path / 'candidate'
    result = prepare_candidate(packet, output)
    assert result['locked_release_pass'] is False
    assert result['human_independence_verified_by_software'] is False
    assert result['case_count'] == len(cases)
    assert 0.30 <= result['split_counts']['locked_candidate'] / len(cases) <= 0.50
    reviewed = [json.loads(line) for line in (output / 'reviewed_cases.jsonl').read_text(encoding='utf-8').splitlines()]
    assert all({reviewed[index]['split'] for index in component} <= {'dev', 'locked_candidate'}
               and len({reviewed[index]['split'] for index in component}) == 1
               for component in prepare_minimum_review.group_components(reviewed))
