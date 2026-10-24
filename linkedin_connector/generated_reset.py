"""Explicit offline reset of generated letters with a recoverable backup.

The caller must stop the dashboard/workers before invoking this maintenance helper.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from linkedin_connector.database import JobStore, JobStoreError
from linkedin_connector.generation_store import _job_hash, _now


def reset_generated_letters(store: JobStore, backup_directory: Path) -> dict:
    root = store.database_path.parent.resolve()
    managed = (root / "cover_letters").resolve()
    if not managed.is_relative_to(root):
        raise JobStoreError("Directory delle lettere fuori dall'archivio gestito.")
    backup = Path(backup_directory).resolve()
    if backup.exists() or backup == root or backup.is_relative_to(managed):
        raise JobStoreError("Scegli una nuova directory di backup fuori dalle lettere.")
    connection = store._connection()
    moved = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "generation_tasks" in tables and connection.execute(
                "SELECT 1 FROM generation_tasks WHERE state='RUNNING' LIMIT 1").fetchone():
            raise JobStoreError("Ferma le generazioni prima del reset.")
        files = []
        for row in connection.execute("SELECT relative_path FROM cover_letters"):
            path = (root / row[0]).resolve()
            if not path.is_relative_to(managed) or path == managed:
                raise JobStoreError("File registrato fuori dalla directory delle lettere: reset annullato.")
            if path.exists():
                files.append(path)
        files = list(dict.fromkeys(files))
        counts = {"letter_texts": connection.execute("SELECT COUNT(*) FROM job_materials WHERE TRIM(cover_letter_text)!=''").fetchone()[0],
                  "file_records": connection.execute("SELECT COUNT(*) FROM cover_letters").fetchone()[0],
                  "files": len(files)}
        backup.mkdir(parents=True)
        # Use a separate read connection: backup() on our write transaction would wait.
        with closing(sqlite3.connect(store.database_path)) as source, closing(sqlite3.connect(backup / "jobs.sqlite3")) as destination:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise JobStoreError("Backup database non valido.")
        manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "counts": counts, "files": []}
        for path in files:
            relative = path.relative_to(root)
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise JobStoreError("Verifica backup file fallita.")
            manifest["files"].append({"relative_path": relative.as_posix(), "sha256": digest})
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        connection.execute("DELETE FROM cover_letters")
        connection.execute("DELETE FROM job_materials")
        for table in ("letter_versions", "cover_letter_feedback", "local_generation_context", "local_decisions", "bulk_regeneration_items", "bulk_regeneration_batches"):
            if table in tables:
                connection.execute(f"DELETE FROM {table}")
        connection.execute("UPDATE jobs SET status=CASE WHEN status='IGNORED' THEN status ELSE 'NEW' END, suppress_auto_generation=1")
        if "generation_tasks" in tables:
            connection.execute("DELETE FROM generation_tasks")
            for job in connection.execute("SELECT * FROM jobs"):
                connection.execute("INSERT INTO generation_tasks(linkedin_job_id,job_hash,state,requested_at) VALUES (?,?,'MANUAL',?)", (job["linkedin_job_id"], _job_hash(job), _now()))
        for path in files:
            path.unlink()
            moved.append(path)
        connection.commit()
        return {**counts, "backup_directory": str(backup)}
    except Exception:
        connection.rollback()
        for path in moved:
            shutil.copy2(backup / path.relative_to(root), path)
        raise
    finally:
        connection.close()
