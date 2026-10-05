"""A diagnostic crash tool must refuse unrelated Docker resources."""

from copy import deepcopy
import json
import subprocess

import pytest

from scripts.verify_postgres_server_crash import require_owned_container, require_owned_volume
from scripts import verify_postgres_server_crash as verifier


RUN = "synthetic-run-123"
CONTAINER = "a" * 64
VOLUME = "ra_serverkill_123"
LABELS = {"resolveai.verifier": "postgres-server-crash", "resolveai.run_id": RUN,
          "resolveai.synthetic": "true"}


def fixture_container():
    return {"Id": CONTAINER, "Config": {"Labels": dict(LABELS)},
            "Mounts": [{"Type": "volume", "Name": VOLUME, "Destination": "/var/lib/postgresql/data"}],
            "HostConfig": {"PortBindings": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "12345"}]}}}


def test_owned_resources_are_accepted():
    require_owned_container(fixture_container(), CONTAINER, RUN, VOLUME)
    require_owned_volume({"Name": VOLUME, "Labels": LABELS}, VOLUME, RUN)


def test_restart_unstable_docker_port_configuration_is_rejected():
    info = fixture_container()
    info["HostConfig"]["PortBindings"]["5432/tcp"][0]["HostPort"] = ""
    with pytest.raises(RuntimeError):
        require_owned_container(info, CONTAINER, RUN, VOLUME)


@pytest.mark.parametrize("change", ["id", "labels", "run", "synthetic", "mount", "bind", "port"])
def test_unrelated_or_exposed_container_is_never_accepted(change):
    info = deepcopy(fixture_container())
    if change == "id":
        info["Id"] = "b" * 64
    elif change == "labels":
        info["Config"]["Labels"] = None
    elif change == "run":
        info["Config"]["Labels"]["resolveai.run_id"] = "another-run"
    elif change == "synthetic":
        info["Config"]["Labels"]["resolveai.synthetic"] = "false"
    elif change == "mount":
        info["Mounts"][0]["Name"] = "compose_postgres_data"
    elif change == "bind":
        info["Mounts"][0]["Type"] = "bind"
    else:
        info["HostConfig"]["PortBindings"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
    with pytest.raises(RuntimeError):
        require_owned_container(info, CONTAINER, RUN, VOLUME)


@pytest.mark.parametrize("info", [
    {"Name": "compose_postgres_data", "Labels": LABELS},
    {"Name": VOLUME, "Labels": None},
    {"Name": VOLUME, "Labels": {**LABELS, "resolveai.run_id": "another-run"}},
])
def test_unrelated_volume_is_never_removed(info):
    with pytest.raises(RuntimeError):
        require_owned_volume(info, VOLUME, RUN)


@pytest.mark.parametrize("message, category", [
    ("address already in use; secret=do-not-record", "port_in_use"),
    ("Cannot connect to the Docker daemon; secret=do-not-record", "daemon_unavailable"),
])
def test_docker_failure_preserves_safe_cause_without_raw_output(monkeypatch, tmp_path, message, category):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 125, message))
    with pytest.raises(RuntimeError) as caught:
        verifier.docker("run", "--env", "secret=do-not-record")
    verifier.save_failure(tmp_path, caught.value)
    report_text = (tmp_path / "error.json").read_text()
    report = json.loads(report_text)
    assert report["diagnostics"] == {"operation": "run", "category": category, "exit_code": 125}
    assert "do-not-record" not in report_text
