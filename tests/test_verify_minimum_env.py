"""The documented no-key command must set child runner imports itself."""

from scripts.verify_minimum import ROOT, verification_env


def test_minimum_runner_sets_own_import_path_and_clears_credentials(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "fake-public")
    env = verification_env()
    assert env["PYTHONPATH"].split(":")[:2] == [str(ROOT / "apps/api"), str(ROOT)]
    assert env["AUTH_MODE"] == "mock"
    assert env["OPENROUTER_API_KEY"] == env["LANGFUSE_PUBLIC_KEY"] == ""
