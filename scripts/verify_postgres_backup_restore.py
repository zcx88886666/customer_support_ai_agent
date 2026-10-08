"""Restore private synthetic PostgreSQL dumps into a distinct owned server."""

from __future__ import annotations

import hashlib
import html
import json
import os
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, TypedDict
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_postgres_server_crash import DisposablePostgres, DockerFailure, save_failure
from scripts.verify_transaction_kill_postgres import run_case


def database_url(admin_url: str, database: str) -> str:
    parts = urlsplit(admin_url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, "", ""))


def canonical_json_row(value: str) -> str:
    """Preserve every JSON numeric token's precision and scalar type."""
    def encode(node):
        if isinstance(node, Decimal):
            return str(node)
        if isinstance(node, dict):
            return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + encode(node[key])
                                  for key in sorted(node)) + "}"
        if isinstance(node, list):
            return "[" + ",".join(encode(item) for item in node) + "]"
        return json.dumps(node, ensure_ascii=False, separators=(",", ":"))

    return encode(json.loads(value, parse_float=Decimal, parse_int=Decimal))


def snapshot_database(url: str) -> dict:
    result = {"tables": {}}
    with psycopg.connect(url, options="-c statement_timeout=10000") as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        for (table,) in tables:
            rows = connection.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(sql.Identifier("public", table))).fetchall()
            canonical = sorted(canonical_json_row(row[0]) for row in rows)
            result["tables"][table] = {"count": len(rows), "sha256": hashlib.sha256("\n".join(canonical).encode()).hexdigest()}
        queries = {
            "columns": "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated,a.attcollation::regcollation::text FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
            "constraints": "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,k.conname",
            "indexes": "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname",
            "extensions": "SELECT extname,extversion FROM pg_extension ORDER BY extname",
            "sequences": "SELECT sequencename,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename",
        }
        for name, query in queries.items():
            result[name] = [list(row) for row in connection.execute(query).fetchall()]
        result["sequence_calls"] = [
            [name, *connection.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(
                sql.Identifier("public", name))).fetchone()]
            for name, *_ in result["sequences"]]
    return result


def restore_checks(before: dict, after: dict) -> dict[str, bool]:
    original, restored = before["tables"], after["tables"]
    same_tables = set(original) == set(restored)
    checks = {"all_tables_match": same_tables,
              "table_counts_match": same_tables and all(original[name]["count"] == restored[name]["count"] for name in original),
              "table_rows_match": same_tables and all(original[name]["sha256"] == restored[name]["sha256"] for name in original),
              "checkpoint_records_restored": restored.get("checkpoints", {}).get("count", 0) > 0}
    checks.update({name + "_match": before[name] == after[name] for name in (
        "columns", "constraints", "indexes", "extensions", "sequences", "sequence_calls")})
    return checks


class CheckpointState(TypedDict):
    stage: str


def checkpoint_roundtrip(url: str, stage: str, *, create: bool) -> bool:
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(CheckpointState)
    builder.add_node("observe", lambda state: {})
    builder.add_edge(START, "observe")
    builder.add_edge("observe", END)
    config = {"configurable": {"thread_id": "backup-" + stage}}
    with PostgresSaver.from_conn_string(url) as saver:
        if create:
            saver.setup()
        graph = builder.compile(checkpointer=saver)
        if create:
            graph.invoke({"stage": stage}, config=config)
        return graph.get_state(config).values == {"stage": stage}


def docker_stream(server: DisposablePostgres, command: list[str], archive: Path, *, restore: bool, log_path: Path) -> None:
    server.owned()
    with log_path.open("wb") as log:
        if restore:
            with archive.open("rb") as source:
                result = subprocess.run(["docker", "exec", "-i", server.container_id, *command],
                                        stdin=source, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        else:
            descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                result = subprocess.run(["docker", "exec", server.container_id, *command],
                                        stdout=target, stderr=log, timeout=60)
    if result.returncode:
        raise DockerFailure("exec", "restore_failed" if restore else "dump_failed", result.returncode)


def restore_guarded(archive: Path, expected_sha256: str, restore: Callable[[], None]) -> None:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("Archive hash mismatch")
    restore()


def probe_damaged_archive(
        target: DisposablePostgres, target_url: str, archive: Path,
        restore_command: list[str], stage_dir: Path) -> dict[str, bool]:
    """Prove a truncated archive cannot leave a usable partial restore."""
    data = archive.read_bytes()
    if len(data) < 32:
        raise ValueError("Archive too small for damage probe")
    damaged = stage_dir / "database.truncated.dump"
    descriptor = os.open(damaged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target_file:
        target_file.write(data[:len(data) // 2])
    try:
        docker_stream(target, restore_command, damaged, restore=True,
                      log_path=stage_dir / "truncated-restore.log")
    except DockerFailure:
        rejected = True
    else:
        rejected = False
    with psycopg.connect(target_url, options="-c statement_timeout=10000") as connection:
        table_count = connection.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
    return {"damaged_archive_rejected": rejected,
            "damaged_restore_left_no_tables": table_count == 0,
            "damaged_archive_is_private_0600": stat.S_IMODE(damaged.stat().st_mode) == 0o600}


def replay_restored_business(url: str, stage: str, report_dir: Path) -> list[dict]:
    env = {**os.environ, "DATABASE_URL": url.replace("postgresql://", "postgresql+psycopg://", 1),
           "AUTH_MODE": "mock", "LONG_TERM_MEMORY_MODE": "off", "OPENROUTER_API_KEY": "",
           "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "", "OTEL_EXPORTER_OTLP_ENDPOINT": "",
           "OTEL_METRICS_EXPORTER": "none"}
    if stage == "return":
        code = ("import json; from datetime import datetime,timezone; from resolveai.db import SessionLocal; "
                "from resolveai.domain import create_return; db=SessionLocal(); "
                "r=create_return(db,'cust-01','demo-order-01','demo-item-01',1,'synthetic crash recovery',True,'transaction-kill-return',datetime.now(timezone.utc)); "
                "db.commit(); print(json.dumps({'return_id':r.id})); db.close()")
    else:
        code = "import json; from resolveai.worker import issue_approved_once; print(json.dumps({'issued':issue_approved_once()}))"
    results = []
    for attempt in range(2):
        with (report_dir / f"{stage}.restored-replay-{attempt + 1}.log").open("w") as log:
            outcome = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                     stderr=log, text=True, check=True, timeout=30)
            log.write(outcome.stdout)
            results.append(json.loads(outcome.stdout))
    return results


def verify_case(stage: str, source: DisposablePostgres, target: DisposablePostgres, run_id: str, report_dir: Path) -> dict:
    checks = {}
    row = {"case_id": "backup-restore-" + stage, "checks": checks, "status": "incomplete"}
    stage_dir = report_dir / stage
    stage_dir.mkdir()
    try:
        fixture = run_case(stage, source.admin_url, run_id, stage_dir)
        row["database"] = database = fixture["database"]
        checks.update({"source_" + key: value for key, value in fixture["checks"].items()})
        if fixture["status"] != "pass":
            raise RuntimeError("Source recovery fixture did not pass")
        source_url = database_url(source.admin_url, database)
        target_url = database_url(target.admin_url, database)
        checks["source_checkpoint_recorded"] = checkpoint_roundtrip(source_url, stage, create=True)
        with psycopg.connect(source_url, autocommit=True) as connection:
            connection.execute("CREATE SEQUENCE backup_restore_probe START WITH 42")
            connection.execute("SELECT nextval('backup_restore_probe')")
        before = snapshot_database(source_url)
        (stage_dir / "source-snapshot.json").write_text(json.dumps(before, indent=2) + "\n")
        archive = stage_dir / "database.dump"
        docker_stream(source, ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--username=resolveai", "--dbname=" + database],
                      archive, restore=False, log_path=stage_dir / "dump.log")
        row["archive"] = {"path": str(archive.relative_to(report_dir)), "bytes": archive.stat().st_size,
                          "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
        checks["archive_is_private_0600"] = stat.S_IMODE(archive.stat().st_mode) == 0o600
        checks["archive_is_nonempty"] = archive.stat().st_size > 0
        checks["source_target_are_distinct"] = source.container_id != target.container_id and source.volume != target.volume
        target.owned()
        with psycopg.connect(target.admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        restore_command = ["pg_restore", "--single-transaction", "--exit-on-error", "--no-owner",
                           "--no-acl", "--username=resolveai", "--dbname=" + database]
        checks.update(probe_damaged_archive(target, target_url, archive, restore_command, stage_dir))
        if not all(checks.values()):
            row["status"] = "fail"
            return row
        started = time.monotonic()
        restore_guarded(archive, row["archive"]["sha256"],
                        lambda: docker_stream(target, restore_command, archive, restore=True,
                                              log_path=stage_dir / "restore.log"))
        row["restore_seconds"] = round(time.monotonic() - started, 4)
        after = snapshot_database(target_url)
        (stage_dir / "restored-snapshot.json").write_text(json.dumps(after, indent=2) + "\n")
        checks.update(restore_checks(before, after))
        checks["archive_hash_unchanged"] = hashlib.sha256(archive.read_bytes()).hexdigest() == row["archive"]["sha256"]
        if not all(checks.values()):
            row["status"] = "fail"
            return row
        checks["fresh_checkpointer_reads_restored_state"] = checkpoint_roundtrip(target_url, stage, create=False)
        replay = replay_restored_business(target_url, stage, stage_dir)
        checks["restored_business_replay_is_idempotent"] = (replay[0] == replay[1] if stage == "return" else replay == [{"issued": []}, {"issued": []}])
        checks["business_replay_preserves_all_records"] = snapshot_database(target_url) == after
        row["table_count"] = len(after["tables"])
        row["status"] = "pass" if all(checks.values()) else "fail"
    except Exception as exc:
        save_failure(stage_dir, exc)
        row["error_type"] = type(exc).__name__
    return row


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-backup-restore-" + uuid4().hex[:6]
    report_dir = ROOT / "evals/reports" / run_id
    report_dir.mkdir(parents=True)
    source = DisposablePostgres(run_id + "-source", report_dir / "source-server")
    target = DisposablePostgres(run_id + "-target", report_dir / "target-server")
    source.report_dir.mkdir()
    target.report_dir.mkdir()
    rows, cleanup = [], {}
    error_type = None
    manifest = {"run_id": run_id, "suite": "postgres-backup-restore-v2", "split": "dev", "synthetic": True, "case_count": 2,
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "verifier_hashes": {name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() for name in (
                    "verify_postgres_backup_restore.py", "verify_postgres_server_crash.py", "verify_transaction_kill_postgres.py")},
                "locked_release_ready": False, "offline_copy_verified": False}
    try:
        source.create()
        target.create()
        for stage in ("return", "refund"):
            row = verify_case(stage, source, target, run_id, report_dir)
            rows.append(row)
            print(json.dumps({"case_id": row["case_id"], "status": row["status"]}), flush=True)
            if row["status"] != "pass":
                break
    except Exception as exc:
        save_failure(report_dir, exc)
        error_type = type(exc).__name__
    finally:
        success = len(rows) == 2 and all(row["status"] == "pass" for row in rows) and error_type is None
        for name, server in (("source", source), ("target", target)):
            try:
                cleanup[name] = server.cleanup(success)
            except Exception as exc:
                save_failure(server.report_dir, exc)
                error_type = type(exc).__name__
                cleanup[name] = {"container": server.name, "volume": server.volume, "removed": False, "error_type": error_type}
        manifest.update({"source_image_id": source.image_id, "target_image_id": target.image_id,
                         "source_container_id": source.container_id, "target_container_id": target.container_id, "cleanup": cleanup})
        (report_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (report_dir / "case_results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = {"run_id": run_id, "cases": 2, "executed": len(rows), "passed": sum(row["status"] == "pass" for row in rows),
               "incomplete": 2 - len(rows) + sum(row["status"] == "incomplete" for row in rows),
               "failed_checks": sum(not value for row in rows for value in row["checks"].values()),
               "checks": sum(len(row["checks"]) for row in rows), "error_type": error_type,
               "cleanup": cleanup, "locked_release_ready": False, "offline_copy_verified": False}
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (report_dir / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>Local PostgreSQL restore</title>"
                                          "<h1>Local synthetic PostgreSQL backup restore</h1><pre>" +
                                          html.escape(json.dumps({"summary": summary, "cases": rows}, indent=2)) + "</pre>")
    print(json.dumps({**summary, "report_dir": str(report_dir)}))
    return 0 if summary["passed"] == 2 and not error_type else 1


if __name__ == "__main__":
    raise SystemExit(main())
