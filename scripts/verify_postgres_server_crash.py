"""Crash only an owned disposable PostgreSQL server; verify WAL/business recovery."""

from __future__ import annotations

import hashlib
import html
import json
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_transaction_kill_postgres import run_case

IMAGE = "pgvector/pgvector:pg17"
DATA_PATH = "/var/lib/postgresql/data"


def labels(run_id: str) -> dict[str, str]:
    return {"resolveai.verifier": "postgres-server-crash", "resolveai.run_id": run_id,
            "resolveai.synthetic": "true"}


def require_owned_volume(info: dict, volume: str, run_id: str) -> None:
    actual = info.get("Labels") or {}
    if info.get("Name") != volume or any(actual.get(key) != value for key, value in labels(run_id).items()):
        raise RuntimeError("Refusing unrelated Docker volume")


def require_owned_container(info: dict, container_id: str, run_id: str, volume: str) -> None:
    actual = info.get("Config", {}).get("Labels") or {}
    mounts = info.get("Mounts") or []
    # HostConfig preserves the binding even while a killed server is stopped.
    ports = info.get("HostConfig", {}).get("PortBindings", {}).get("5432/tcp") or []
    host_port = str(ports[0].get("HostPort", "")) if len(ports) == 1 else ""
    if (not container_id or info.get("Id") != container_id
            or any(actual.get(key) != value for key, value in labels(run_id).items())
            or len(mounts) != 1 or mounts[0].get("Type") != "volume"
            or mounts[0].get("Name") != volume or mounts[0].get("Destination") != DATA_PATH
            or len(ports) != 1 or ports[0].get("HostIp") != "127.0.0.1"
            or not host_port.isdecimal() or not 0 < int(host_port) < 65536):
        raise RuntimeError("Refusing unrelated or exposed Docker container")


class DockerFailure(RuntimeError):
    def __init__(self, operation: str, category: str, exit_code: int | None):
        super().__init__("Disposable Docker operation failed")
        self.diagnostics = {"operation": operation, "category": category, "exit_code": exit_code}


def docker(*args: str, timeout: float = 30) -> str:
    result = subprocess.run(["docker", *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout)
    if result.returncode:
        # Keep a known cause and exit code, never raw output or command args.
        output = result.stdout.lower()
        category = ("port_in_use" if "address already in use" in output or "port is already allocated" in output
                    else "daemon_unavailable" if "cannot connect to the docker daemon" in output
                    else "image_missing" if "no such image" in output else "command_failed")
        raise DockerFailure(args[0], category, result.returncode)
    return result.stdout.strip()


class DisposablePostgres:
    def __init__(self, run_id: str, report_dir: Path):
        self.run_id = run_id
        self.report_dir = report_dir
        suffix = uuid4().hex[:16]
        self.name = "ra_serverkill_" + suffix
        self.volume = self.name + "_data"
        self.volume_created = False
        self.container_id = ""
        self.image_id = ""
        self.admin_url = ""

    def owned(self) -> dict:
        info = json.loads(docker("inspect", self.container_id))[0]
        require_owned_container(info, self.container_id, self.run_id, self.volume)
        require_owned_volume(json.loads(docker("volume", "inspect", self.volume))[0], self.volume, self.run_id)
        return info

    def ready(self) -> None:
        deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(self.admin_url, connect_timeout=2, autocommit=True,
                                     options="-c statement_timeout=2000") as connection:
                    if connection.execute("SELECT 1").fetchone() == (1,):
                        return
            except psycopg.Error:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned PostgreSQL server did not become ready")
            time.sleep(0.1)

    def create(self) -> None:
        self.image_id = json.loads(docker("image", "inspect", IMAGE))[0]["Id"]
        label_args = [arg for key, value in labels(self.run_id).items() for arg in ("--label", key + "=" + value)]
        docker("volume", "create", *label_args, self.volume)
        self.volume_created = True
        with socket.socket() as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            host_port = str(port_probe.getsockname()[1])
        # An immutable cached image and explicit owned volume prevent selection
        # of the Compose database or an image pull. Fixture trust auth is bound
        # to a loopback ephemeral port and contains only synthetic data.
        self.container_id = docker("run", "--detach", "--pull", "never", "--name", self.name,
                                   *label_args, "--memory", "384m", "--cpus", "1", "--restart", "no",
                                   "--publish", "127.0.0.1:" + host_port + ":5432", "--volume", self.volume + ":" + DATA_PATH,
                                   "--env", "POSTGRES_USER=resolveai", "--env", "POSTGRES_DB=postgres",
                                   "--env", "POSTGRES_HOST_AUTH_METHOD=trust", self.image_id)
        info = self.owned()
        binding = info["NetworkSettings"]["Ports"]["5432/tcp"][0]
        if binding["HostIp"] != "127.0.0.1":
            raise RuntimeError("Owned PostgreSQL port is not loopback")
        self.admin_url = "postgresql://resolveai@127.0.0.1:" + binding["HostPort"] + "/postgres"
        self.ready()

    def crash_and_restart(self, stage: str) -> dict[str, bool]:
        self.owned()
        with psycopg.connect(self.admin_url, autocommit=True) as connection:
            durability = all(connection.execute("SHOW " + name).fetchone()[0] == "on"
                             for name in ("fsync", "full_page_writes", "synchronous_commit"))
        if not durability:
            raise RuntimeError("Owned PostgreSQL durability settings are disabled")
        since = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        started = time.monotonic()
        docker("kill", "--signal", "KILL", self.container_id)
        stopped = self.owned()
        killed = stopped["State"]["Running"] is False and stopped["State"]["ExitCode"] == 137
        if not killed:
            raise RuntimeError("Owned PostgreSQL server was not SIGKILLed")
        docker("start", self.container_id)
        self.ready()
        log = docker("logs", "--since", since, self.container_id)
        (self.report_dir / f"{stage}.server-crash.log").write_text(log + "\n")
        elapsed = round(time.monotonic() - started, 4)
        (self.report_dir / f"{stage}.restart.json").write_text(json.dumps({"seconds": elapsed, "exit_code": 137}) + "\n")
        restarted = self.owned()
        return {"postgres_durability_enabled": durability, "owned_server_sigkill_exit_137": killed,
                "same_owned_container_running": restarted["State"]["Running"] is True,
                "same_owned_volume_mounted": True,
                "wal_interruption_observed": "database system was interrupted" in log,
                "wal_automatic_recovery_observed": "automatic recovery in progress" in log,
                "postgres_ready_after_wal_recovery": "database system is ready to accept connections" in log}

    def cleanup(self, success: bool) -> dict:
        result = {"container": self.name, "volume": self.volume, "removed": False}
        if self.container_id:
            info = self.owned()
            (self.report_dir / "server.log").write_text(docker("logs", self.container_id) + "\n")
            if success:
                docker("rm", "--force", self.container_id)
            elif info["State"]["Running"]:
                docker("stop", "--time", "2", self.container_id)
        if self.volume_created and success:
            require_owned_volume(json.loads(docker("volume", "inspect", self.volume))[0], self.volume, self.run_id)
            docker("volume", "rm", self.volume)
            result["removed"] = True
        return result


def save_failure(report_dir: Path, exc: Exception) -> None:
    failure = {"error_type": type(exc).__name__, "stack": [
        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_tb(exc.__traceback__)]}
    if isinstance(exc, DockerFailure):
        failure["diagnostics"] = exc.diagnostics
    (report_dir / "error.json").write_text(json.dumps(failure, indent=2) + "\n")


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-serverkill-" + uuid4().hex[:6]
    report_dir = ROOT / "evals/reports" / run_id
    report_dir.mkdir(parents=True)
    server = DisposablePostgres(run_id, report_dir)
    rows = []
    error_type = None
    cleanup = {}
    manifest = {"run_id": run_id, "suite": "postgres-server-crash-v1", "split": "dev", "synthetic": True,
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "transaction_verifier_sha256": hashlib.sha256((ROOT / "scripts/verify_transaction_kill_postgres.py").read_bytes()).hexdigest(),
                "case_count": 2, "locked_release_ready": False}
    try:
        server.create()
        for stage in ("return", "refund"):
            row = run_case(stage, server.admin_url, run_id, report_dir, server.crash_and_restart)
            row["case_id"] = "server-crash-" + stage
            rows.append(row)
            print(json.dumps({"case_id": row["case_id"], "status": row["status"]}), flush=True)
            if row["status"] != "pass":
                break
    except Exception as exc:
        error_type = type(exc).__name__
        save_failure(report_dir, exc)
    finally:
        success = len(rows) == 2 and all(row["status"] == "pass" for row in rows) and error_type is None
        try:
            cleanup = server.cleanup(success)
        except Exception as exc:
            error_type = type(exc).__name__
            save_failure(report_dir, exc)
            cleanup = {"container": server.name, "volume": server.volume, "removed": False, "error_type": error_type}
        manifest.update({"image_id": server.image_id, "container_id": server.container_id, "resources": cleanup})
        (report_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (report_dir / "case_results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = {"run_id": run_id, "cases": 2, "executed": len(rows), "passed": sum(row["status"] == "pass" for row in rows),
               "incomplete": 2 - len(rows) + sum(row["status"] == "incomplete" for row in rows),
               "failed_checks": sum(not value for row in rows for value in row["checks"].values()),
               "checks": sum(len(row["checks"]) for row in rows), "cleanup": cleanup,
               "error_type": error_type, "locked_release_ready": False}
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (report_dir / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>PostgreSQL server crash</title>"
                                          "<h1>Disposable PostgreSQL server crash</h1><pre>" +
                                          html.escape(json.dumps({"summary": summary, "cases": rows}, indent=2)) + "</pre>")
    print(json.dumps({**summary, "report_dir": str(report_dir)}))
    return 0 if summary["passed"] == 2 and not error_type else 1


if __name__ == "__main__":
    raise SystemExit(main())
