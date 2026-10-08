"""Generated scorer faults must be detected without depending on authored gold labels."""

from evals.runners import run_scorer_mutations


def test_generated_scorer_mutations_are_killed_and_reported(tmp_path):
    folder = tmp_path / "campaign"
    result = run_scorer_mutations.run_campaign(folder)

    assert result["baseline_pass"] is True
    assert result["mutants"] == 24
    assert result["counts"] == {"killed": 24, "survived": 0, "invalid": 0}
    assert result["development_pass"] is True
    assert result["locked_release_pass"] is False
    assert all(row["expected_check"] in row["failed_checks"] for row in result["results"])
    for name in ("manifest.json", "case_results.jsonl", "summary.json", "report.html"):
        assert (folder / name).is_file()


def test_campaign_reports_a_survivor_if_authorization_scorer_is_disabled(tmp_path, monkeypatch):
    from evals.runners import score

    monkeypatch.setattr(score, "_ledger_authorized", lambda db, ledger: True)
    result = run_scorer_mutations.run_campaign(tmp_path / "broken", selected={"refund_audit_amount_plus_1"})

    assert result["baseline_pass"] is True
    assert result["counts"] == {"killed": 0, "survived": 1, "invalid": 0}
    assert result["development_pass"] is False
