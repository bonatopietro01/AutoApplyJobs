"""Local SQLite persistence for manually saved LinkedIn jobs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit
from urllib.parse import parse_qsl, urlencode

from linkedin_connector.models import LinkedInJob
from linkedin_connector.observability import log_event, log_exception

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "jobs.sqlite3"
JOB_STATUSES = ("NEW", "PROCESSED", "IGNORED")
JOB_ID_PATTERN = re.compile(r"/jobs/view/(\d+)", re.IGNORECASE)
ACTIVE_JOB_FILTER = "(j.source = 'manual' OR j.linkedin_saved = 1)"
MAX_PREPARED_TITLE_CHARS = 160
MAX_PREPARED_COMPANY_CHARS = 160
MAX_PREPARED_LOCATION_CHARS = 160
MAX_PREPARED_DESCRIPTION_CHARS = 50_000
MAX_PREPARED_LETTER_CHARS = 60_000
MAX_PREPARED_URL_CHARS = 2_000
MAX_SOURCE_REFERENCE_CHARS = 2_000
MAX_SOURCE_REFERENCE_COUNT = 50
MAX_SOURCE_REFERENCE_TOTAL_CHARS = 30_000


class JobStoreError(ValueError):
    """Raised when a job record or requested state transition is invalid."""


def normalize_linkedin_url(url: str) -> str:
    """Return a stable LinkedIn job URL, excluding tracking parameters."""
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        raise JobStoreError("Only HTTPS LinkedIn job URLs can be stored.")
    match = JOB_ID_PATTERN.search(parsed.path)
    if not match:
        raise JobStoreError("The LinkedIn URL does not contain a stable job ID.")
    return urlunsplit(("https", "www.linkedin.com", f"/jobs/view/{match.group(1)}/", "", ""))


def normalize_application_url(url: str) -> str:
    """Canonicalize a public HTTPS application URL for stable deduplication."""
    if not isinstance(url, str) or not url.strip():
        raise JobStoreError("An application URL is required.")
    clean_url = url.strip()
    if len(clean_url) > MAX_PREPARED_URL_CHARS:
        raise JobStoreError(f"URLs must be no longer than {MAX_PREPARED_URL_CHARS} characters.")
    parsed = urlsplit(clean_url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not host or parsed.username or parsed.password:
        raise JobStoreError("Application URLs must be valid public HTTPS URLs.")
    if (host == "linkedin.com" or host.endswith(".linkedin.com")) and JOB_ID_PATTERN.search(parsed.path):
        return normalize_linkedin_url(url)
    try:
        port = parsed.port
    except ValueError as error:
        raise JobStoreError("The application URL has an invalid port.") from error
    netloc = host if port in (None, 443) else f"{host}:{port}"
    tracking = {"fbclid", "gclid", "ref", "source", "trk"}
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                             if not key.casefold().startswith("utm_") and key.casefold() not in tracking))
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunsplit(("https", netloc, path, query, ""))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _forgotten_job_hash(job_id: str) -> str:
    return hashlib.sha256(f"linkedin-job:{job_id}".encode("utf-8")).hexdigest()


def _serialize_row(row: sqlite3.Row) -> dict[str, object]:
    result = dict(row)
    if "description_complete" in result:
        result["description_complete"] = bool(result["description_complete"])
    if "has_materials" in result:
        result["has_materials"] = bool(result["has_materials"])
    if "linkedin_saved" in result:
        result["linkedin_saved"] = bool(result["linkedin_saved"])
    return result


class JobStore:
    """Transactional store for complete saved-job records."""

    def __init__(self, database_path: Path | str = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connection()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_key TEXT PRIMARY KEY,
                    linkedin_job_id TEXT UNIQUE,
                    normalized_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT,
                    location TEXT,
                    linkedin_url TEXT NOT NULL,
                    description TEXT NOT NULL,
                    description_complete INTEGER NOT NULL CHECK (description_complete IN (0, 1)),
                    status TEXT NOT NULL DEFAULT 'NEW'
                        CHECK (status IN ('NEW', 'PROCESSED', 'IGNORED')),
                    first_saved_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_changed_at TEXT NOT NULL DEFAULT '',
                    suppress_auto_generation INTEGER NOT NULL DEFAULT 0
                        CHECK (suppress_auto_generation IN (0, 1)),
                    linkedin_saved INTEGER NOT NULL DEFAULT 1
                        CHECK (linkedin_saved IN (0, 1)),
                    source TEXT NOT NULL DEFAULT 'linkedin'
                        CHECK (source IN ('linkedin', 'manual')),
                    source_fingerprint TEXT,
                    origin TEXT NOT NULL DEFAULT 'linkedin'
                        CHECK (origin IN ('linkedin', 'manual', 'chatgpt')),
                    application_url TEXT NOT NULL DEFAULT '',
                    application_status TEXT NOT NULL DEFAULT 'NOT_APPLIED'
                        CHECK (application_status IN ('NOT_APPLIED', 'APPLIED', 'REJECTED')),
                    application_status_updated_at TEXT NOT NULL DEFAULT '',
                    source_references TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status_first_saved
                    ON jobs(status, first_saved_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_title_company
                    ON jobs(title, company);
                CREATE TABLE IF NOT EXISTS job_materials (
                    linkedin_job_id TEXT PRIMARY KEY
                        REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    company_analysis TEXT NOT NULL DEFAULT '',
                    role_analysis TEXT NOT NULL DEFAULT '',
                    personalization_notes TEXT NOT NULL DEFAULT '',
                    cover_letter_text TEXT NOT NULL DEFAULT '',
                    agent_name TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cover_letters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedin_job_id TEXT NOT NULL
                        REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    file_format TEXT NOT NULL CHECK (file_format IN ('pdf', 'docx')),
                    relative_path TEXT NOT NULL UNIQUE,
                    original_filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(linkedin_job_id, file_format, version)
                );
                CREATE INDEX IF NOT EXISTS idx_cover_letters_job_version
                    ON cover_letters(linkedin_job_id, version DESC);
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS forgotten_jobs (
                    job_id_hash TEXT PRIMARY KEY CHECK (length(job_id_hash) = 64),
                    forgotten_at TEXT NOT NULL
                );
                """
            )
            job_columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(jobs)")
            }
            if "suppress_auto_generation" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN suppress_auto_generation "
                    "INTEGER NOT NULL DEFAULT 0 CHECK (suppress_auto_generation IN (0, 1))"
                )
            if "linkedin_saved" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN linkedin_saved "
                    "INTEGER NOT NULL DEFAULT 1 CHECK (linkedin_saved IN (0, 1))"
                )
            if "source" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN source TEXT NOT NULL DEFAULT 'linkedin' "
                    "CHECK (source IN ('linkedin', 'manual'))"
                )
            if "source_fingerprint" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN source_fingerprint TEXT")
            if "last_changed_at" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN last_changed_at TEXT NOT NULL DEFAULT ''")
            migrations = {
                "origin": "TEXT NOT NULL DEFAULT 'linkedin' CHECK (origin IN ('linkedin', 'manual', 'chatgpt'))",
                "application_url": "TEXT NOT NULL DEFAULT ''",
                "application_status": "TEXT NOT NULL DEFAULT 'NOT_APPLIED' CHECK (application_status IN ('NOT_APPLIED', 'APPLIED', 'REJECTED'))",
                "application_status_updated_at": "TEXT NOT NULL DEFAULT ''",
                "source_references": "TEXT NOT NULL DEFAULT '[]'",
            }
            for name, definition in migrations.items():
                if name not in job_columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            connection.execute("UPDATE jobs SET origin='manual' WHERE source='manual' AND origin='linkedin'")
            connection.execute("UPDATE jobs SET application_url=linkedin_url WHERE application_url='' AND source='linkedin'")
            connection.execute(
                "UPDATE jobs SET last_changed_at=last_seen_at WHERE last_changed_at=''"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_job_fingerprint "
                "ON jobs(source_fingerprint) WHERE source='manual' AND source_fingerprint IS NOT NULL"
            )
            connection.commit()
            self._migrate_origin_scoped_url_uniqueness(connection)
        finally:
            connection.close()

    @staticmethod
    def _migrate_origin_scoped_url_uniqueness(connection: sqlite3.Connection) -> None:
        """Allow one canonical URL per preparation origin, preserving related rows."""
        unique_url_index = False
        for index in connection.execute("PRAGMA index_list(jobs)").fetchall():
            if not int(index["unique"]):
                continue
            columns = [str(column["name"]) for column in connection.execute(
                f"PRAGMA index_info('{str(index['name']).replace(chr(39), chr(39) * 2)}')"
            ).fetchall()]
            if columns == ["normalized_url"]:
                unique_url_index = True
                break

        if unique_url_index:
            connection.commit()
            connection.execute("PRAGMA foreign_keys = OFF")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE jobs_origin_migration (
                    job_key TEXT PRIMARY KEY,
                    linkedin_job_id TEXT UNIQUE,
                    normalized_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT,
                    location TEXT,
                    linkedin_url TEXT NOT NULL,
                    description TEXT NOT NULL,
                    description_complete INTEGER NOT NULL CHECK (description_complete IN (0, 1)),
                    status TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW', 'PROCESSED', 'IGNORED')),
                    first_saved_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_changed_at TEXT NOT NULL DEFAULT '',
                    suppress_auto_generation INTEGER NOT NULL DEFAULT 0 CHECK (suppress_auto_generation IN (0, 1)),
                    linkedin_saved INTEGER NOT NULL DEFAULT 1 CHECK (linkedin_saved IN (0, 1)),
                    source TEXT NOT NULL DEFAULT 'linkedin' CHECK (source IN ('linkedin', 'manual')),
                    source_fingerprint TEXT,
                    origin TEXT NOT NULL DEFAULT 'linkedin' CHECK (origin IN ('linkedin', 'manual', 'chatgpt')),
                    application_url TEXT NOT NULL DEFAULT '',
                    application_status TEXT NOT NULL DEFAULT 'NOT_APPLIED'
                        CHECK (application_status IN ('NOT_APPLIED', 'APPLIED', 'REJECTED')),
                    application_status_updated_at TEXT NOT NULL DEFAULT '',
                    source_references TEXT NOT NULL DEFAULT '[]'
                )""")
                columns = (
                    "job_key,linkedin_job_id,normalized_url,title,company,location,linkedin_url,description,"
                    "description_complete,status,first_saved_at,last_seen_at,last_changed_at,"
                    "suppress_auto_generation,linkedin_saved,source,source_fingerprint,origin,application_url,"
                    "application_status,application_status_updated_at,source_references"
                )
                connection.execute(
                    f"INSERT INTO jobs_origin_migration ({columns}) SELECT {columns} FROM jobs"
                )
                connection.execute("DROP TABLE jobs")
                connection.execute("ALTER TABLE jobs_origin_migration RENAME TO jobs")
                connection.execute(
                    "CREATE INDEX idx_jobs_status_first_saved ON jobs(status, first_saved_at DESC)"
                )
                connection.execute("CREATE INDEX idx_jobs_title_company ON jobs(title, company)")
                connection.execute(
                    "CREATE UNIQUE INDEX idx_manual_job_fingerprint "
                    "ON jobs(source_fingerprint) WHERE source='manual' AND source_fingerprint IS NOT NULL"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("PRAGMA foreign_keys = ON")

        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_origin_normalized_url ON jobs(origin, normalized_url)"
        )
        connection.commit()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _fetch_all(self, sql: str, parameters: tuple[object, ...] = ()) -> list[dict[str, object]]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connection()
            rows = connection.execute(sql, parameters).fetchall()
            result = [_serialize_row(row) for row in rows]
        except sqlite3.Error as error:
            log_exception(
                "database", "db.read_failed", "A local database read failed.", error=error
            )
            raise
        finally:
            if connection is not None:
                connection.close()
        log_event(
            logging.INFO,
            "database",
            "db.read_completed",
            "A local database read completed.",
            context={"count": len(result)},
        )
        return result

    def _fetch_one(self, sql: str, parameters: tuple[object, ...]) -> dict[str, object] | None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connection()
            row = connection.execute(sql, parameters).fetchone()
            result = _serialize_row(row) if row else None
        except sqlite3.Error as error:
            log_exception(
                "database", "db.read_failed", "A local database read failed.", error=error
            )
            raise
        finally:
            if connection is not None:
                connection.close()
        log_event(
            logging.INFO,
            "database",
            "db.read_completed",
            "A local database read completed.",
            context={"count": int(result is not None)},
        )
        return result

    def sync_jobs(
        self,
        jobs: Iterable[LinkedInJob],
        *,
        suppress_auto_generation: bool = False,
        reconcile_saved: bool = False,
        visible_ids: Iterable[str] | None = None,
        unreadable: Iterable[dict[str, str | None]] = (),
        skipped_missing_title: Iterable[dict[str, str | None]] = (),
    ) -> dict[str, object]:
        """Insert or refresh jobs, optionally reconciling a verified complete Saved list."""
        if type(reconcile_saved) is not bool:
            raise JobStoreError("The LinkedIn Saved-list reconciliation option is invalid.")
        batch = list(jobs)
        timestamp = _utc_now()
        seen_keys: set[str] = set()
        visible_id_list = list(dict.fromkeys(
            str(job_id).strip() for job_id in (visible_ids if visible_ids is not None else ())
            if str(job_id).strip()
        ))
        unreadable_rows = list(unreadable)
        skipped_title_rows = list(skipped_missing_title)
        read_failure_by_id = {
            str(item.get("linkedin_job_id") or ""): item
            for item in (*unreadable_rows, *skipped_title_rows)
            if item.get("linkedin_job_id")
        }
        events: list[dict[str, object]] = []
        connection = self._connection()
        inserted = 0
        refreshed = 0
        suppressed = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            forgotten_hashes = {
                str(row["job_id_hash"])
                for row in connection.execute("SELECT job_id_hash FROM forgotten_jobs")
            }
            for job in batch:
                normalized_url = normalize_linkedin_url(job.linkedin_url)
                url_id = JOB_ID_PATTERN.search(urlsplit(normalized_url).path)
                linkedin_job_id = job.linkedin_job_id.strip() if job.linkedin_job_id else ""
                if not linkedin_job_id and url_id:
                    linkedin_job_id = url_id.group(1)
                if linkedin_job_id and url_id and linkedin_job_id != url_id.group(1):
                    raise JobStoreError("LinkedIn job ID does not match the job URL.")
                if not linkedin_job_id:
                    raise JobStoreError("A LinkedIn job ID is required for deduplication.")
                key = f"linkedin:{linkedin_job_id}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                identity_hash = _forgotten_job_hash(linkedin_job_id)
                if identity_hash in forgotten_hashes:
                    suppressed += 1
                    events.append({
                        "type": "suppressed_forgotten", "linkedin_job_id": linkedin_job_id,
                        "title": job.title.strip() if job.title else None,
                        "company": job.company, "changed_fields": [],
                        "reason": "Offerta rimossa manualmente e non reimportabile.",
                        "timestamp": timestamp,
                    })
                    continue
                missing_content = []
                if not str(job.title or "").strip():
                    missing_content.append("titolo")
                if not str(job.description or "").strip() or not job.description_complete:
                    missing_content.append("descrizione completa")
                if missing_content:
                    raise JobStoreError(
                        f"Annuncio LinkedIn {linkedin_job_id}: manca {', '.join(missing_content)}. "
                        "Sincronizzazione interrotta; la snapshot locale è rimasta invariata."
                    )
                record = [
                    key,
                    linkedin_job_id,
                    normalized_url,
                    job.title.strip(),
                    job.company,
                    job.location,
                    normalized_url,
                    job.description.strip(),
                    1,
                    1,
                    timestamp,
                    timestamp,
                ]
                existing = connection.execute(
                    """SELECT * FROM jobs
                       WHERE job_key = ? OR linkedin_job_id = ?
                          OR (normalized_url = ? AND origin != 'chatgpt')""",
                    (key, linkedin_job_id, record[2]),
                ).fetchone()
                if existing:
                    if not record[5] and existing["location"]:
                        record[5] = existing["location"]
                    changed_fields = [
                        label for column, value, label in (
                            ("title", record[3], "titolo"),
                            ("company", record[4], "azienda"),
                            ("location", record[5], "sede"),
                            ("description", record[7], "descrizione"),
                        ) if existing[column] != value
                    ]
                    if not existing["linkedin_saved"]:
                        changed_fields.append("stato Salvati")
                    connection.execute(
                        """UPDATE jobs SET linkedin_job_id = ?, normalized_url = ?, title = ?,
                                  company = ?, location = ?, linkedin_url = ?, description = ?,
                                  description_complete = ?, linkedin_saved = 1, last_seen_at = ?,
                                  last_changed_at = CASE WHEN ? THEN ? ELSE last_changed_at END
                           WHERE job_key = ?""",
                        (
                            linkedin_job_id,
                            record[2],
                            record[3],
                            record[4],
                            record[5],
                            record[6],
                            record[7],
                            record[8],
                            record[11],
                            int(bool(changed_fields)),
                            timestamp,
                            existing["job_key"],
                        ),
                    )
                    refreshed += 1
                    if changed_fields:
                        events.append({
                            "type": "updated", "linkedin_job_id": linkedin_job_id,
                            "title": record[3], "company": record[4],
                            "changed_fields": changed_fields, "timestamp": timestamp,
                        })
                    else:
                        events.append({
                            "type": "maintained", "linkedin_job_id": linkedin_job_id,
                            "title": record[3], "company": record[4],
                            "changed_fields": [], "timestamp": timestamp,
                        })
                else:
                    connection.execute(
                        """INSERT INTO jobs (
                               job_key, linkedin_job_id, normalized_url, title, company, location,
                               linkedin_url, description, description_complete, linkedin_saved, first_saved_at,
                           last_seen_at, last_changed_at, suppress_auto_generation
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (*record, timestamp, int(suppress_auto_generation)),
                    )
                    inserted += 1
                    events.append({
                        "type": "added", "linkedin_job_id": linkedin_job_id,
                        "title": record[3], "company": record[4],
                        "changed_fields": [], "timestamp": timestamp,
                    })

            # A visible ID remains saved even when its detail page did not expose
            # a description. Preserve the existing local record verbatim in that case.
            detail_ids = {key.removeprefix("linkedin:") for key in seen_keys}
            for job_id in visible_id_list:
                if job_id in detail_ids:
                    continue
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE source='linkedin' AND linkedin_job_id=?",
                    (job_id,),
                ).fetchone()
                if existing is None:
                    card = read_failure_by_id.get(job_id, {})
                    events.append({
                        "type": "not_imported", "linkedin_job_id": job_id,
                        "title": card.get("title"), "company": card.get("company"),
                        "reason": card.get("reason") or "Descrizione non disponibile.",
                        "timestamp": timestamp,
                    })
                    continue
                changed = not bool(existing["linkedin_saved"])
                connection.execute(
                    """UPDATE jobs SET linkedin_saved=1, last_seen_at=?,
                              last_changed_at=CASE WHEN ? THEN ? ELSE last_changed_at END
                       WHERE job_key=?""",
                    (timestamp, int(changed), timestamp, existing["job_key"]),
                )
                if changed:
                    events.append({
                        "type": "updated", "linkedin_job_id": job_id,
                        "title": existing["title"], "company": existing["company"],
                        "changed_fields": ["stato Salvati"], "timestamp": timestamp,
                    })
                else:
                    events.append({
                        "type": "maintained", "linkedin_job_id": job_id,
                        "title": existing["title"], "company": existing["company"],
                        "changed_fields": [], "timestamp": timestamp,
                        "details_readable": False,
                    })

            removed_from_linkedin = 0
            preserved_with_letter = 0
            removed_with_files = 0
            hidden_without_letter = 0
            cancelled_generation_tasks = 0
            preserved_events: list[dict[str, object]] = []
            if reconcile_saved:
                scanned_ids = visible_id_list or [key.removeprefix("linkedin:") for key in seen_keys]
                scanned_ids = list(dict.fromkeys(scanned_ids))
                not_in_scan = (
                    f"j.linkedin_job_id NOT IN ({','.join('?' for _ in scanned_ids)})"
                    if scanned_ids else "1 = 1"
                )
                not_in_scan_update = (
                    f"linkedin_job_id NOT IN ({','.join('?' for _ in scanned_ids)})"
                    if scanned_ids else "1 = 1"
                )
                task_table_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='generation_tasks'"
                ).fetchone() is not None
                if task_table_exists:
                    missing_rows = connection.execute(
                        f"""SELECT j.linkedin_job_id, j.title, j.company, j.suppress_auto_generation,
                                   CASE WHEN TRIM(COALESCE(m.cover_letter_text, '')) != ''
                                             OR EXISTS (SELECT 1 FROM cover_letters AS c
                                                        WHERE c.linkedin_job_id=j.linkedin_job_id)
                                        THEN 1 ELSE 0 END AS has_letter,
                                   EXISTS (SELECT 1 FROM cover_letters AS c
                                           WHERE c.linkedin_job_id=j.linkedin_job_id) AS has_files,
                                   t.state AS task_state, t.manual_request
                            FROM jobs AS j
                            LEFT JOIN job_materials AS m ON m.linkedin_job_id=j.linkedin_job_id
                            LEFT JOIN generation_tasks AS t ON t.linkedin_job_id=j.linkedin_job_id
                            WHERE j.source='linkedin' AND j.linkedin_saved=1 AND {not_in_scan}""",
                        tuple(scanned_ids),
                    ).fetchall()
                else:
                    missing_rows = connection.execute(
                        f"""SELECT j.linkedin_job_id, j.title, j.company, j.suppress_auto_generation,
                                   CASE WHEN TRIM(COALESCE(m.cover_letter_text, '')) != ''
                                             OR EXISTS (SELECT 1 FROM cover_letters AS c
                                                        WHERE c.linkedin_job_id=j.linkedin_job_id)
                                        THEN 1 ELSE 0 END AS has_letter,
                                   EXISTS (SELECT 1 FROM cover_letters AS c
                                           WHERE c.linkedin_job_id=j.linkedin_job_id) AS has_files,
                                   NULL AS task_state, 0 AS manual_request
                            FROM jobs AS j
                            LEFT JOIN job_materials AS m ON m.linkedin_job_id=j.linkedin_job_id
                            WHERE j.source='linkedin' AND j.linkedin_saved=1 AND {not_in_scan}""",
                        tuple(scanned_ids),
                    ).fetchall()

                running_ids = [str(row["linkedin_job_id"]) for row in missing_rows
                               if row["task_state"] == "RUNNING"]
                if running_ids:
                    raise JobStoreError(
                        "La scansione è completa, ma alcune candidature non più salvate hanno "
                        "un’analisi AI già in corso. Nessun dato è stato modificato; riprova "
                        "dopo la fine di quelle attività."
                    )

                removed_from_linkedin = len(missing_rows)
                preserved_with_letter = sum(bool(row["has_letter"]) for row in missing_rows)
                removed_with_files = sum(bool(row["has_files"]) for row in missing_rows)
                hidden_without_letter = removed_from_linkedin - preserved_with_letter
                for row in missing_rows:
                    job_id = str(row["linkedin_job_id"])
                    preserved_events.append({
                        "type": "removed_from_saved", "linkedin_job_id": job_id,
                        "title": row["title"], "company": row["company"],
                        "changed_fields": ["stato Salvati"],
                        "has_letter": bool(row["has_letter"]), "timestamp": timestamp,
                    })
                    if row["task_state"] != "PENDING":
                        continue
                    may_resume_if_resaved = (
                        not row["has_letter"]
                        and not row["suppress_auto_generation"]
                        and not row["manual_request"]
                    )
                    connection.execute(
                        """UPDATE generation_tasks SET state='MANUAL', manual_request=0, stage='',
                                  error_code=?, error_request_id=NULL
                           WHERE linkedin_job_id=? AND state='PENDING'""",
                        ("linkedin_unsaved" if may_resume_if_resaved else None, job_id),
                    )
                    connection.execute(
                        """UPDATE bulk_regeneration_items SET status='CANCELLED'
                           WHERE linkedin_job_id=? AND status='QUEUED'""",
                        (job_id,),
                    )
                    cancelled_generation_tasks += 1

                connection.execute(
                    f"UPDATE jobs SET linkedin_saved=0, last_changed_at=? WHERE source='linkedin' AND linkedin_saved=1 AND {not_in_scan_update}",
                    (timestamp, *scanned_ids),
                )
            if visible_id_list:
                placeholders = ",".join("?" for _ in visible_id_list)
                artifact_counts = connection.execute(
                    f"""SELECT COUNT(DISTINCT j.linkedin_job_id) AS with_letter,
                               COUNT(DISTINCT c.id) AS files
                        FROM jobs AS j
                        LEFT JOIN job_materials AS m ON m.linkedin_job_id=j.linkedin_job_id
                        LEFT JOIN cover_letters AS c ON c.linkedin_job_id=j.linkedin_job_id
                        WHERE j.linkedin_job_id IN ({placeholders})
                          AND (TRIM(COALESCE(m.cover_letter_text, ''))!='' OR c.id IS NOT NULL)""",
                    tuple(visible_id_list),
                ).fetchone()
            else:
                artifact_counts = {"with_letter": 0, "files": 0}
            events.extend(preserved_events)
            for card in (*unreadable_rows, *skipped_title_rows):
                job_id = str(card.get("linkedin_job_id") or "")
                if job_id and job_id not in visible_id_list:
                    continue
                if job_id not in detail_ids and any(
                    event.get("linkedin_job_id") == job_id and event.get("type") == "maintained"
                    for event in events
                ):
                    for event in events:
                        if event.get("linkedin_job_id") == job_id and event.get("type") == "maintained":
                            event["reason"] = card.get("reason")
            connection.execute(
                """INSERT INTO app_metadata (key, value) VALUES ('last_sync_at', ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (timestamp,),
            )
            connection.commit()
        except Exception as error:
            connection.rollback()
            log_exception(
                "database",
                "jobs.sync.failed",
                "Atomic saved-job synchronization failed.",
                error=error,
            )
            raise
        finally:
            connection.close()
        log_event(
            logging.INFO,
            "database",
            "jobs.sync.committed",
            "Saved-job synchronization committed to the local database.",
            context={"inserted": inserted, "refreshed": refreshed,
                     "suppressed_forgotten": suppressed, "total": inserted + refreshed},
        )
        counts: dict[str, object] = {
            "inserted": inserted, "refreshed": refreshed, "total": inserted + refreshed,
        }
        if visible_ids is not None or unreadable_rows or skipped_title_rows:
            counts.update({
                "events": events,
                "updated": sum(event["type"] == "updated" for event in events),
                "maintained": sum(event["type"] == "maintained" for event in events),
                "visible": len(visible_id_list),
                "unreadable_descriptions": len(unreadable_rows),
                "skipped_missing_title": len(skipped_title_rows),
                "not_imported": sum(event["type"] == "not_imported" for event in events),
                "with_letters": int(artifact_counts["with_letter"]) + preserved_with_letter,
                "preserved_files": int(artifact_counts["files"]) + removed_with_files,
            })
        if reconcile_saved:
            counts.update({
                "removed_from_linkedin": removed_from_linkedin,
                "preserved_with_letter": preserved_with_letter,
                "hidden_without_letter": hidden_without_letter,
                "cancelled_generation_tasks": cancelled_generation_tasks,
            })
        return counts

    def forget_job(self, job_id: str) -> str:
        """Remove all job-linked database state and prevent automatic re-import."""
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT linkedin_job_id FROM jobs WHERE linkedin_job_id = ?", (job_id,)
            ).fetchone()
            if job is None:
                connection.rollback()
                return "missing"

            task_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='generation_tasks'"
            ).fetchone()
            if task_table:
                task = connection.execute(
                    "SELECT state FROM generation_tasks WHERE linkedin_job_id = ?", (job_id,)
                ).fetchone()
                if task is not None and task["state"] == "RUNNING":
                    connection.rollback()
                    return "running"

            connection.execute(
                "INSERT OR IGNORE INTO forgotten_jobs(job_id_hash, forgotten_at) VALUES (?, ?)",
                (_forgotten_job_hash(str(job["linkedin_job_id"])), _utc_now()),
            )
            deleted = connection.execute(
                "DELETE FROM jobs WHERE linkedin_job_id = ?", (job_id,)
            ).rowcount
            if deleted != 1:
                connection.rollback()
                return "missing"
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        log_event(logging.INFO, "database", "jobs.memory_forgotten",
                  "A saved job and its linked database records were removed.")
        return "deleted"

    def get_new_jobs(self, limit: int = 100) -> list[dict[str, object]]:
        return self.get_saved_jobs(status="NEW", limit=limit)

    def get_saved_jobs(
        self, status: str | None = None, limit: int = 100
    ) -> list[dict[str, object]]:
        if limit < 1 or limit > 1000:
            raise JobStoreError("limit must be between 1 and 1000.")
        if status is not None and status not in JOB_STATUSES:
            raise JobStoreError(f"status must be one of: {', '.join(JOB_STATUSES)}.")
        if status is None:
            return self._fetch_all(
                f"SELECT j.* FROM jobs AS j WHERE {ACTIVE_JOB_FILTER} "
                "ORDER BY j.first_saved_at DESC, j.job_key LIMIT ?", (limit,)
            )
        return self._fetch_all(
            f"SELECT j.* FROM jobs AS j WHERE {ACTIVE_JOB_FILTER} AND j.status = ? "
            "ORDER BY j.first_saved_at DESC, j.job_key LIMIT ?",
            (status, limit),
        )

    def get_job(self, job_id: str) -> dict[str, object] | None:
        return self._fetch_one(
            "SELECT * FROM jobs WHERE linkedin_job_id = ? OR job_key = ?", (job_id, job_id)
        )

    def get_chatgpt_job_by_url(self, url: str) -> dict[str, object] | None:
        """Return a compact ChatGPT-origin match for one canonical announcement URL."""
        canonical_url = normalize_application_url(url)
        legacy_url = canonical_url.rstrip("/")
        return self._fetch_one(
            """SELECT linkedin_job_id, origin, title, company, normalized_url FROM jobs
               WHERE origin='chatgpt' AND normalized_url IN (?,?)
               ORDER BY CASE WHEN normalized_url=? THEN 0 ELSE 1 END LIMIT 1""",
            (canonical_url, legacy_url, canonical_url),
        )

    def create_manual_job(
        self,
        description: str,
        *,
        title: str = "",
        company: str = "",
        location: str = "",
    ) -> tuple[dict[str, object], bool]:
        """Save a manually entered job offer using only local storage."""
        if not isinstance(description, str):
            raise JobStoreError("Incolla il testo dell’offerta di lavoro.")
        clean_description = description.strip()
        if len(clean_description) > 50_000:
            raise JobStoreError("Il testo dell’offerta supera il limite di 50.000 caratteri.")
        if len(clean_description) < 30 or len(clean_description.split()) < 5:
            raise JobStoreError("Il testo è troppo breve: incolla i requisiti o la descrizione dell’offerta.")
        if "\x00" in clean_description:
            raise JobStoreError("Il testo dell’offerta contiene caratteri non validi.")

        def clean_field(value: str, name: str) -> str:
            if not isinstance(value, str):
                raise JobStoreError(f"Il campo {name} non è valido.")
            result = value.strip()
            if len(result) > 160:
                raise JobStoreError(f"Il campo {name} supera il limite di 160 caratteri.")
            if any(ord(character) < 32 and character not in "\t\n\r" for character in result):
                raise JobStoreError(f"Il campo {name} contiene caratteri non validi.")
            return result

        clean_title = clean_field(title, "titolo")
        clean_company = clean_field(company, "azienda")
        clean_location = clean_field(location, "sede")
        if not clean_title:
            clean_title = next(
                (line.strip()[:160] for line in clean_description.splitlines() if line.strip()),
                "Offerta inserita manualmente",
            )
        normalized_text = " ".join(clean_description.split()).casefold()
        fingerprint = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        now = _utc_now()
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT * FROM jobs WHERE source='manual' AND source_fingerprint=?",
                (fingerprint,),
            ).fetchone()
            if duplicate is not None:
                connection.commit()
                return _serialize_row(duplicate), False
            if not clean_location:
                raise JobStoreError("Manca la località dell’offerta. Inseriscila per salvare l’annuncio.")

            local_id = f"manual_{secrets.token_hex(16)}"
            connection.execute(
                """INSERT INTO jobs (
                       job_key, linkedin_job_id, normalized_url, title, company, location,
                       linkedin_url, description, description_complete, status,
                       first_saved_at, last_seen_at, last_changed_at, suppress_auto_generation,
                       linkedin_saved, source, source_fingerprint
                   ) VALUES (?, ?, ?, ?, ?, ?, '', ?, 1, 'NEW', ?, ?, ?, 1, 0, 'manual', ?)""",
                (
                    local_id, local_id, f"manual://{local_id}", clean_title,
                    clean_company or None, clean_location or None, clean_description,
                    now, now, now, fingerprint,
                ),
            )
            row = connection.execute("SELECT * FROM jobs WHERE job_key=?", (local_id,)).fetchone()
            connection.commit()
            assert row is not None
            return _serialize_row(row), True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_chatgpt_job(
        self, *, title: str, company: str, location: str, description: str,
        application_url: str, cover_letter_text: str, job_url: str | None = None,
        role_analysis: str = "",
        company_analysis: str = "", personalization_notes: str = "",
        source_references: list[str] | None = None,
        document_contents: dict[str, bytes] | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Persist one prepared ChatGPT opportunity without queueing API generation."""
        announcement_url = application_url if job_url is None or (isinstance(job_url, str) and not job_url.strip()) else job_url
        canonical_url = normalize_application_url(announcement_url)
        canonical_application_url = normalize_application_url(application_url)
        values = {"title": title, "company": company, "location": location,
                  "description": description, "cover_letter_text": cover_letter_text,
                  "role_analysis": role_analysis, "company_analysis": company_analysis,
                  "personalization_notes": personalization_notes}
        limits = {"title": MAX_PREPARED_TITLE_CHARS,
                  "company": MAX_PREPARED_COMPANY_CHARS,
                  "location": MAX_PREPARED_LOCATION_CHARS,
                  "description": MAX_PREPARED_DESCRIPTION_CHARS,
                  "cover_letter_text": MAX_PREPARED_LETTER_CHARS,
                  "role_analysis": 20_000, "company_analysis": 20_000,
                  "personalization_notes": 20_000}
        clean: dict[str, str] = {}
        for name, value in values.items():
            if not isinstance(value, str):
                raise JobStoreError(f"{name} must be text.")
            clean[name] = value.strip()
            if len(clean[name]) > limits[name]:
                raise JobStoreError(f"{name} exceeds the {limits[name]}-character limit.")
        for name in ("title", "company", "location", "description", "cover_letter_text"):
            if not clean[name]:
                raise JobStoreError(f"{name} is required.")
        if len(clean["description"]) < 30 or len(clean["description"].split()) < 5:
            raise JobStoreError("The complete job description is required.")
        if "\x00" in clean["description"] or "\x00" in clean["cover_letter_text"]:
            raise JobStoreError("Job text contains invalid characters.")
        references = [] if source_references is None else source_references
        if (not isinstance(references, list) or len(references) > MAX_SOURCE_REFERENCE_COUNT or
                any(not isinstance(item, str) or len(item) > MAX_SOURCE_REFERENCE_CHARS for item in references) or
                sum(len(item) for item in references if isinstance(item, str)) > MAX_SOURCE_REFERENCE_TOTAL_CHARS):
            raise JobStoreError("source_references must be a list of text URLs or citations.")
        documents = document_contents or {}
        if document_contents is not None and set(documents) != {"pdf", "docx"}:
            raise JobStoreError("Prepared jobs must include both PDF and DOCX documents.")
        for file_format, contents in documents.items():
            if not isinstance(contents, bytes) or not contents or len(contents) > 20 * 1024 * 1024:
                raise JobStoreError(f"The prepared {file_format} document is invalid.")
        now = _utc_now()
        connection = self._connection()
        temporary_paths: list[Path] = []
        final_paths: list[Path] = []
        final_directory: Path | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            legacy_url = canonical_url.rstrip("/")
            duplicate = connection.execute(
                "SELECT * FROM jobs WHERE origin='chatgpt' AND normalized_url IN (?,?) "
                "ORDER BY CASE WHEN normalized_url=? THEN 0 ELSE 1 END LIMIT 1",
                (canonical_url, legacy_url, canonical_url),
            ).fetchone()
            if duplicate is not None:
                connection.commit()
                return _serialize_row(duplicate), False
            local_id = f"manual_{secrets.token_hex(16)}"
            connection.execute(
                """INSERT INTO jobs (job_key,linkedin_job_id,normalized_url,title,company,location,
                   linkedin_url,description,description_complete,status,first_saved_at,last_seen_at,last_changed_at,
                   suppress_auto_generation,linkedin_saved,source,origin,application_url,application_status,
                   application_status_updated_at,source_references)
                   VALUES (?,?,?,?,?,?, ?, ?,1,'NEW',?,?,?,1,0,'manual','chatgpt',?,'NOT_APPLIED','',?)""",
                (local_id, local_id, canonical_url, clean["title"], clean["company"], clean["location"],
                 canonical_url, clean["description"], now, now, now, canonical_application_url,
                 json.dumps(references, ensure_ascii=False)),
            )
            connection.execute(
                """INSERT INTO job_materials (linkedin_job_id,company_analysis,role_analysis,
                   personalization_notes,cover_letter_text,agent_name,updated_at)
                   VALUES (?,?,?,?,?,'ChatGPT',?)""",
                (local_id, clean["company_analysis"], clean["role_analysis"],
                 clean["personalization_notes"], clean["cover_letter_text"], now),
            )
            if documents:
                data_root = self.database_path.parent.resolve()
                letters_root = (data_root / "cover_letters").resolve()
                final_directory = (letters_root / local_id).resolve()
                if not final_directory.is_relative_to(letters_root):
                    raise JobStoreError("The cover-letter path escaped its managed folder.")
                final_directory.mkdir(parents=True, exist_ok=True)
                for file_format in ("pdf", "docx"):
                    version = 1
                    final_path = final_directory / f"cover-letter-v{version:02d}.{file_format}"
                    while final_path.exists():
                        version += 1
                        final_path = final_directory / f"cover-letter-v{version:02d}.{file_format}"
                    temporary_path = final_path.with_name(f".{secrets.token_hex(8)}.tmp")
                    temporary_paths.append(temporary_path)
                    with temporary_path.open("xb") as output:
                        output.write(documents[file_format])
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary_path, final_path)
                    temporary_paths.remove(temporary_path)
                    final_paths.append(final_path)
                    relative_path = final_path.relative_to(data_root).as_posix()
                    connection.execute(
                        """INSERT INTO cover_letters (linkedin_job_id,version,file_format,
                           relative_path,original_filename,created_at) VALUES (?,?,?,?,?,?)""",
                        (local_id, version, file_format, relative_path,
                         f"cover-letter.{file_format}", now),
                    )
            row = connection.execute("SELECT * FROM jobs WHERE job_key=?", (local_id,)).fetchone()
            connection.commit()
            assert row is not None
            return _serialize_row(row), True
        except Exception:
            connection.rollback()
            for path in temporary_paths + final_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if final_directory is not None:
                try:
                    final_directory.rmdir()
                except OSError:
                    pass
            raise
        finally:
            connection.close()

    def mark_application_status(self, job_id: str, status: str) -> dict[str, object]:
        if status not in {"APPLIED", "REJECTED"}:
            raise JobStoreError("Application status must be APPLIED or REJECTED.")
        connection = self._connection()
        try:
            cursor = connection.execute(
                "UPDATE jobs SET application_status=?, application_status_updated_at=? WHERE linkedin_job_id=? OR job_key=?",
                (status, _utc_now(), job_id, job_id),
            )
            if cursor.rowcount == 0:
                raise JobStoreError(f"No saved job found with ID {job_id}.")
            connection.commit()
            row = connection.execute("SELECT * FROM jobs WHERE linkedin_job_id=? OR job_key=?", (job_id, job_id)).fetchone()
            assert row is not None
            return _serialize_row(row)
        finally:
            connection.close()

    def search_jobs(self, query: str, limit: int = 100) -> list[dict[str, object]]:
        if limit < 1 or limit > 1000:
            raise JobStoreError("limit must be between 1 and 1000.")
        query = query.strip()
        if not query:
            raise JobStoreError("query must not be empty.")
        pattern = f"%{query}%"
        return self._fetch_all(
            f"""SELECT j.* FROM jobs AS j
               WHERE {ACTIVE_JOB_FILTER}
                 AND (j.title LIKE ? COLLATE NOCASE
                  OR j.company LIKE ? COLLATE NOCASE
                  OR j.location LIKE ? COLLATE NOCASE
                  OR j.description LIKE ? COLLATE NOCASE)
               ORDER BY j.first_saved_at DESC, j.job_key LIMIT ?""",
            (pattern, pattern, pattern, pattern, limit),
        )

    def mark_processed(self, job_id: str) -> dict[str, object]:
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status FROM jobs WHERE linkedin_job_id = ? OR job_key = ?",
                (job_id, job_id),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise JobStoreError(f"No saved job found with ID {job_id}.")
            if current["status"] == "IGNORED":
                connection.rollback()
                raise JobStoreError("An IGNORED job cannot be marked as PROCESSED.")
            cursor = connection.execute(
                "UPDATE jobs SET status = 'PROCESSED' WHERE linkedin_job_id = ? OR job_key = ?",
                (job_id, job_id),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                raise JobStoreError(f"No saved job found with ID {job_id}.")
            row = connection.execute(
                "SELECT * FROM jobs WHERE linkedin_job_id = ? OR job_key = ?", (job_id, job_id)
            ).fetchone()
            connection.commit()
            assert row is not None
            log_event(
                logging.INFO,
                "database",
                "jobs.marked_processed",
                "Saved job marked as processed in the local database.",
                context={"count": 1},
            )
            return _serialize_row(row)
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            log_exception(
                "database",
                "jobs.mark_processed_failed",
                "A saved job could not be marked as processed.",
                error=error,
            )
            raise
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get_dashboard_jobs(
        self, *, query: str = "", status: str | None = None, limit: int = 1000,
        workspace: str | None = None,
    ) -> list[dict[str, object]]:
        """Return compact job rows and letter counts for the local dashboard."""
        if limit < 1 or limit > 1000:
            raise JobStoreError("limit must be between 1 and 1000.")
        if status is not None and status not in JOB_STATUSES:
            raise JobStoreError(f"status must be one of: {', '.join(JOB_STATUSES)}.")
        if workspace not in (None, "linkedin", "openai"):
            raise JobStoreError("workspace must be linkedin or openai.")
        query = query.strip()
        pattern = f"%{query}%" if query else None
        workspace_filter = ""
        parameters: list[object] = [status, status]
        if workspace == "openai":
            workspace_filter = "AND j.origin='chatgpt'"
        elif workspace == "linkedin":
            workspace_filter = "AND j.origin!='chatgpt'"
        parameters.extend((pattern, pattern, pattern, pattern, pattern, limit))
        return self._fetch_all(
            f"""SELECT j.linkedin_job_id, j.linkedin_job_id AS job_id, j.source, j.origin,
                      j.title, j.company, j.location,
                      j.linkedin_saved,
                      j.linkedin_url, j.application_url, j.application_status,
                      j.status, j.first_saved_at, j.last_seen_at, j.last_changed_at,
                      substr(j.description, 1, 260) AS description_preview,
                      CASE WHEN m.linkedin_job_id IS NULL THEN 0 ELSE 1 END AS has_materials,
                      CASE WHEN TRIM(COALESCE(m.cover_letter_text,'')) != '' THEN 1 ELSE 0 END AS has_cover_letter,
                      COUNT(c.id) AS cover_letter_count
               FROM jobs AS j
               LEFT JOIN job_materials AS m ON m.linkedin_job_id = j.linkedin_job_id
               LEFT JOIN cover_letters AS c ON c.linkedin_job_id = j.linkedin_job_id
                WHERE {ACTIVE_JOB_FILTER}
                  AND (? IS NULL OR j.status = ?)
                  {workspace_filter}
                 AND (? IS NULL OR j.title LIKE ? COLLATE NOCASE
                      OR j.company LIKE ? COLLATE NOCASE
                      OR j.location LIKE ? COLLATE NOCASE
                      OR j.description LIKE ? COLLATE NOCASE)
               GROUP BY j.linkedin_job_id
               ORDER BY CASE j.status WHEN 'NEW' THEN 0 WHEN 'PROCESSED' THEN 1 ELSE 2 END,
                        j.first_saved_at DESC, j.title COLLATE NOCASE
               LIMIT ?""",
            tuple(parameters),
        )

    def get_linkedin_job_ids(self, *, include_unsaved: bool = False) -> set[str]:
        """Return current Saved IDs; optionally include locally retained withdrawn jobs."""
        condition = "source='linkedin' AND linkedin_job_id IS NOT NULL" if include_unsaved else (
            "source='linkedin' AND linkedin_job_id IS NOT NULL AND linkedin_saved=1"
        )
        rows = self._fetch_all(f"SELECT linkedin_job_id FROM jobs WHERE {condition}")
        return {str(row["linkedin_job_id"]) for row in rows}

    def get_job_first_saved_at(self) -> dict[str, str]:
        """Return local first-observed timestamps; these are not LinkedIn save dates."""
        rows = self._fetch_all(
            "SELECT linkedin_job_id, first_saved_at FROM jobs "
            "WHERE source='linkedin' AND linkedin_job_id IS NOT NULL"
        )
        return {str(row["linkedin_job_id"]): str(row["first_saved_at"]) for row in rows}

    def get_last_sync_at(self) -> str | None:
        connection = self._connection()
        try:
            row = connection.execute("SELECT value FROM app_metadata WHERE key = 'last_sync_at'").fetchone()
            return str(row["value"]) if row else None
        finally:
            connection.close()

    def save_saved_sync_report(self, report: dict[str, object]) -> None:
        """Persist the most recent full Saved-list comparison across dashboard restarts."""
        serialized = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO app_metadata(key, value) VALUES('last_saved_sync_report', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (serialized,),
            )
            if report.get("status") == "complete" and report.get("membership_verified") is True:
                successful_at = str(report.get("ended_at") or _utc_now())
                connection.execute(
                    """INSERT INTO app_metadata(key, value)
                       VALUES('last_successful_full_sync_at', ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (successful_at,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_saved_sync_report(self) -> dict[str, object]:
        connection = self._connection()
        try:
            rows = connection.execute(
                "SELECT key, value FROM app_metadata WHERE key IN (?, ?)",
                ("last_saved_sync_report", "last_successful_full_sync_at"),
            ).fetchall()
        finally:
            connection.close()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        report: dict[str, object] | None = None
        if values.get("last_saved_sync_report"):
            try:
                parsed = json.loads(values["last_saved_sync_report"])
                report = parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                report = None
        return {
            "last_report": report,
            "last_successful_full_sync_at": values.get("last_successful_full_sync_at"),
        }

    def get_job_bundle(self, job_id: str) -> dict[str, object] | None:
        """Return a job, agent notes, and its saved letter files for the dashboard."""
        job = self.get_job(job_id)
        if job is None:
            return None
        materials = self._fetch_one(
            "SELECT * FROM job_materials WHERE linkedin_job_id = ?",
            (str(job["linkedin_job_id"]),),
        )
        letters = self._fetch_all(
            """SELECT id, linkedin_job_id, version, file_format, original_filename, created_at
               FROM cover_letters WHERE linkedin_job_id = ?
               ORDER BY version DESC, file_format""",
            (str(job["linkedin_job_id"]),),
        )
        return {"job": job, "materials": materials, "cover_letters": letters}

    def save_job_materials(
        self,
        job_id: str,
        *,
        company_analysis: str = "",
        role_analysis: str = "",
        personalization_notes: str = "",
        cover_letter_text: str = "",
        agent_name: str = "",
    ) -> dict[str, object]:
        """Save editable agent findings and cover-letter text for one saved job."""
        fields = {
            "company_analysis": (company_analysis, 20_000),
            "role_analysis": (role_analysis, 20_000),
            "personalization_notes": (personalization_notes, 20_000),
            "cover_letter_text": (cover_letter_text, 60_000),
            "agent_name": (agent_name, 200),
        }
        normalized: dict[str, str] = {}
        for field, (value, max_length) in fields.items():
            if not isinstance(value, str):
                raise JobStoreError(f"{field} must be text.")
            if len(value) > max_length:
                raise JobStoreError(f"{field} exceeds the {max_length}-character limit.")
            normalized[field] = value.strip()

        now = _utc_now()
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT linkedin_job_id FROM jobs WHERE linkedin_job_id = ? OR job_key = ?",
                (job_id, job_id),
            ).fetchone()
            if job is None:
                raise JobStoreError(f"No saved job found with ID {job_id}.")
            linkedin_job_id = str(job["linkedin_job_id"])
            connection.execute(
                """INSERT INTO job_materials (
                       linkedin_job_id, company_analysis, role_analysis, personalization_notes,
                       cover_letter_text, agent_name, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(linkedin_job_id) DO UPDATE SET
                       company_analysis = excluded.company_analysis,
                       role_analysis = excluded.role_analysis,
                       personalization_notes = excluded.personalization_notes,
                       cover_letter_text = excluded.cover_letter_text,
                       agent_name = excluded.agent_name,
                       updated_at = excluded.updated_at""",
                (
                    linkedin_job_id,
                    normalized["company_analysis"],
                    normalized["role_analysis"],
                    normalized["personalization_notes"],
                    normalized["cover_letter_text"],
                    normalized["agent_name"],
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM job_materials WHERE linkedin_job_id = ?", (linkedin_job_id,)
            ).fetchone()
            connection.commit()
            assert row is not None
            log_event(
                logging.INFO,
                "database",
                "materials.saved",
                "Application materials saved to the local database.",
                context={"count": sum(bool(value) for value in normalized.values())},
            )
            return _serialize_row(row)
        except JobStoreError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            log_exception(
                "database",
                "materials.save_failed",
                "Application materials could not be saved to the local database.",
                error=error,
            )
            raise
        finally:
            connection.close()

    def save_cover_letter_file(
        self,
        job_id: str,
        *,
        original_filename: str,
        file_format: str,
        contents: bytes,
    ) -> dict[str, object]:
        """Store a PDF/DOCX in a per-job folder and register a new version."""
        file_format = file_format.casefold().lstrip(".")
        if file_format not in {"pdf", "docx"}:
            raise JobStoreError("Only PDF and DOCX cover letters are accepted.")
        if not contents:
            raise JobStoreError("The uploaded cover letter is empty.")
        if len(contents) > 20 * 1024 * 1024:
            raise JobStoreError("The uploaded cover letter exceeds the 20 MB limit.")
        clean_filename = Path(original_filename.replace("\\", "/")).name.strip()
        if not clean_filename or len(clean_filename) > 240:
            raise JobStoreError("The uploaded filename is invalid or too long.")

        data_root = self.database_path.parent.resolve()
        letters_root = (data_root / "cover_letters").resolve()
        if not letters_root.is_relative_to(data_root):
            raise JobStoreError("The cover-letter folder must stay under the local data directory.")

        connection = self._connection()
        temp_path: Path | None = None
        final_path: Path | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT linkedin_job_id FROM jobs WHERE linkedin_job_id = ? OR job_key = ?",
                (job_id, job_id),
            ).fetchone()
            if job is None:
                raise JobStoreError(f"No saved job found with ID {job_id}.")
            linkedin_job_id = str(job["linkedin_job_id"])
            row = connection.execute(
                """SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                   FROM cover_letters WHERE linkedin_job_id = ? AND file_format = ?""",
                (linkedin_job_id, file_format),
            ).fetchone()
            version = int(row["next_version"])
            relative_path = Path("cover_letters") / linkedin_job_id
            final_path = (data_root / relative_path / f"cover-letter-v{version:02d}.{file_format}").resolve()
            while final_path.exists():
                version += 1
                final_path = (
                    data_root / relative_path / f"cover-letter-v{version:02d}.{file_format}"
                ).resolve()
            relative_path = relative_path / f"cover-letter-v{version:02d}.{file_format}"
            if not final_path.is_relative_to(letters_root):
                raise JobStoreError("The cover-letter path escaped its managed folder.")
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = final_path.with_name(f".{secrets.token_hex(8)}.tmp")
            with temp_path.open("xb") as output:
                output.write(contents)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, final_path)
            now = _utc_now()
            cursor = connection.execute(
                """INSERT INTO cover_letters (
                       linkedin_job_id, version, file_format, relative_path,
                       original_filename, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    linkedin_job_id,
                    version,
                    file_format,
                    relative_path.as_posix(),
                    clean_filename,
                    now,
                ),
            )
            letter_id = int(cursor.lastrowid)
            saved = connection.execute(
                """SELECT id, linkedin_job_id, version, file_format,
                          relative_path, original_filename, created_at
                   FROM cover_letters WHERE id = ?""",
                (letter_id,),
            ).fetchone()
            connection.commit()
            assert saved is not None
            log_event(
                logging.INFO,
                "database",
                "cover_letter.archived",
                "Cover-letter file archived in the local data directory.",
                context={"file_format": file_format, "file_size": len(contents), "version": version},
            )
            return _serialize_row(saved)
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if final_path is not None:
                final_path.unlink(missing_ok=True)
            log_exception(
                "database",
                "cover_letter.archive_failed",
                "Cover-letter file could not be committed to the local data directory.",
                error=error,
                context={"file_format": file_format, "file_size": len(contents)},
            )
            raise
        finally:
            connection.close()

    def get_cover_letter_file(self, letter_id: int) -> tuple[dict[str, object], Path] | None:
        """Resolve a registered letter file while enforcing the managed-root boundary."""
        record = self._fetch_one(
            "SELECT * FROM cover_letters WHERE id = ?", (letter_id,)
        )
        if record is None:
            return None
        data_root = self.database_path.parent.resolve()
        letters_root = (data_root / "cover_letters").resolve()
        file_path = (data_root / str(record["relative_path"])).resolve()
        if not file_path.is_relative_to(letters_root):
            raise JobStoreError("The registered cover-letter path is outside its managed folder.")
        if not file_path.is_file():
            return None
        return record, file_path
