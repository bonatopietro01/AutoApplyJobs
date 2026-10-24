"""Durable, per-job AI work queue and atomic generated-material commits."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from linkedin_connector.database import JobStore, JobStoreError


DAILY_LIMIT_WAIT_STAGE = "Limite AI · ripresa automatica al rinnovo o dopo modifica Impostazioni"
AUTO_AGE_LIMIT_ERROR = "auto_age_limit"
AUTO_AGE_UNKNOWN_ERROR = "age_unverified"
UNSAVED_ON_LINKEDIN_ERROR = "linkedin_unsaved"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _utc_day_bounds() -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    return f"{today.isoformat()}T00:00:00", f"{(today + timedelta(days=1)).isoformat()}T00:00:00"


def _job_hash(job: sqlite3.Row) -> str:
    source = [job[key] for key in ("linkedin_job_id", "title", "company", "location", "description")]
    return hashlib.sha256(json.dumps(source, ensure_ascii=False).encode("utf-8")).hexdigest()


class GenerationStore:
    def __init__(self, store: JobStore) -> None:
        self.store = store
        connection = store._connection()
        try:
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS generation_tasks (
                    linkedin_job_id TEXT PRIMARY KEY REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    job_hash TEXT NOT NULL,
                    completed_hash TEXT,
                    state TEXT NOT NULL CHECK (state IN
                        ('PENDING','RUNNING','READY','REVIEW','ERROR','STALE','MANUAL')),
                    manual_request INTEGER NOT NULL DEFAULT 0 CHECK (manual_request IN (0,1)),
                    stage TEXT NOT NULL DEFAULT '',
                    gaps TEXT NOT NULL DEFAULT '',
                    requested_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_code TEXT,
                    error_request_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_generation_tasks_state_requested
                    ON generation_tasks(state, manual_request DESC, requested_at);
                CREATE TABLE IF NOT EXISTS ai_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedin_job_id TEXT NOT NULL REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    operation TEXT NOT NULL DEFAULT 'agent',
                    search_calls INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_operation_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedin_job_id TEXT REFERENCES jobs(linkedin_job_id) ON DELETE SET NULL,
                    model TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    search_calls INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS letter_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedin_job_id TEXT NOT NULL REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    letter_text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0,1)),
                    created_at TEXT NOT NULL,
                    UNIQUE(linkedin_job_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_letter_versions_job
                    ON letter_versions(linkedin_job_id, version DESC);"""
            )
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS bulk_regeneration_batches (
                    batch_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    scope_days INTEGER,
                    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS bulk_regeneration_items (
                    batch_id TEXT NOT NULL REFERENCES bulk_regeneration_batches(batch_id) ON DELETE CASCADE,
                    linkedin_job_id TEXT NOT NULL REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    queued_requested_at TEXT NOT NULL,
                    previous_state TEXT NOT NULL,
                    previous_stage TEXT NOT NULL,
                    previous_manual_request INTEGER NOT NULL,
                    previous_completed_hash TEXT,
                    previous_completed_at TEXT,
                    previous_gaps TEXT NOT NULL,
                    previous_error_code TEXT,
                    previous_error_request_id TEXT,
                    previous_requested_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','CANCELLED')),
                    PRIMARY KEY(batch_id, linkedin_job_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bulk_regeneration_items_batch
                    ON bulk_regeneration_items(batch_id, status);"""
            )
            for table in ("ai_usage", "api_operation_usage"):
                columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}
                if table == "ai_usage" and "operation" not in columns:
                    connection.execute("ALTER TABLE ai_usage ADD COLUMN operation TEXT NOT NULL DEFAULT 'agent'")
                for column in ("search_calls", "cached_input_tokens", "duration_ms"):
                    if column not in columns:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                        )
            usage_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(api_operation_usage)")}
            if "stage" not in usage_columns:
                connection.execute("ALTER TABLE api_operation_usage ADD COLUMN stage TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_usage_created_operation "
                "ON ai_usage(created_at, operation)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_operation_usage_created_operation "
                "ON api_operation_usage(created_at, operation)"
            )
            connection.execute("UPDATE generation_tasks SET state = 'PENDING', stage = '' WHERE state = 'RUNNING'")
            connection.commit()
        finally:
            connection.close()

    def reconcile(self) -> int:
        """Queue missing/new/changed jobs without replacing pre-existing manual materials."""
        connection = self.store._connection()
        queued = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE generation_tasks SET state='PENDING', stage=?
                   WHERE state='ERROR' AND error_code='usage_limit'
                     AND linkedin_job_id IN (SELECT linkedin_job_id FROM jobs WHERE linkedin_saved=1)
                     AND NOT EXISTS (SELECT 1 FROM job_materials AS m
                                     WHERE m.linkedin_job_id=generation_tasks.linkedin_job_id
                                       AND TRIM(COALESCE(m.cover_letter_text,''))!='')
                     AND NOT EXISTS (SELECT 1 FROM cover_letters AS f
                                     WHERE f.linkedin_job_id=generation_tasks.linkedin_job_id)""",
                (DAILY_LIMIT_WAIT_STAGE,),
            )
            queued += max(0, int(cursor.rowcount))
            connection.execute(
                """UPDATE generation_tasks SET state='MANUAL',stage=''
                   WHERE state='PENDING' AND manual_request=0
                     AND (EXISTS (SELECT 1 FROM job_materials AS m
                                  WHERE m.linkedin_job_id=generation_tasks.linkedin_job_id
                                    AND TRIM(COALESCE(m.cover_letter_text,''))!='')
                          OR EXISTS (SELECT 1 FROM cover_letters AS f
                                     WHERE f.linkedin_job_id=generation_tasks.linkedin_job_id))"""
            )
            rows = connection.execute(
                """SELECT j.linkedin_job_id, j.title, j.company, j.location, j.description,
                          j.linkedin_saved,
                          j.suppress_auto_generation,
                          m.linkedin_job_id AS material_id, m.cover_letter_text,
                          EXISTS(SELECT 1 FROM cover_letters AS f
                                 WHERE f.linkedin_job_id=j.linkedin_job_id) AS has_file,
                          t.job_hash AS prior_hash, t.state AS prior_state,
                          t.error_code AS prior_error_code
                   FROM jobs AS j
                   LEFT JOIN job_materials AS m ON m.linkedin_job_id = j.linkedin_job_id
                   LEFT JOIN generation_tasks AS t ON t.linkedin_job_id = j.linkedin_job_id
                   WHERE j.status != 'IGNORED'"""
            ).fetchall()
            for row in rows:
                if not row["linkedin_saved"]:
                    continue
                job_id = str(row["linkedin_job_id"])
                digest = _job_hash(row)
                has_letter = bool(str(row["cover_letter_text"] or "").strip()) or bool(row["has_file"])
                if (row["prior_state"] == "MANUAL"
                        and row["prior_error_code"] == UNSAVED_ON_LINKEDIN_ERROR):
                    state = "MANUAL" if has_letter or row["suppress_auto_generation"] else "PENDING"
                    connection.execute(
                        """UPDATE generation_tasks SET job_hash=?,state=?,stage='',manual_request=0,
                                  completed_hash=CASE WHEN ?='MANUAL' THEN ? ELSE NULL END,
                                  error_code=NULL,error_request_id=NULL,requested_at=?
                           WHERE linkedin_job_id=?""",
                        (digest, state, state, digest, _now(), job_id),
                    )
                    queued += int(state == "PENDING")
                    continue
                if row["prior_hash"] is None:
                    state = "MANUAL" if (
                        row["material_id"] is not None or has_letter or row["suppress_auto_generation"]
                    ) else "PENDING"
                    connection.execute(
                        """INSERT INTO generation_tasks
                           (linkedin_job_id, job_hash, completed_hash, state, requested_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (job_id, digest, digest if state == "MANUAL" else None, state, _now()),
                    )
                    queued += int(state == "PENDING")
                elif digest != row["prior_hash"]:
                    state = "MANUAL" if (
                        row["prior_state"] == "MANUAL" or has_letter or row["suppress_auto_generation"]
                    ) else "PENDING"
                    connection.execute(
                        """UPDATE generation_tasks SET job_hash = ?, state = ?, stage = '',
                           error_code = NULL, error_request_id = NULL, requested_at = ?
                           WHERE linkedin_job_id = ?""",
                        (digest, state, _now(), job_id),
                    )
                    queued += int(state == "PENDING")
            connection.commit()
            return queued
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_recovered_jobs(self, job_ids: list[str]) -> int:
        """Create dormant task records so recovered jobs can be explicitly queued later."""
        if not job_ids:
            return 0
        connection = self.store._connection()
        inserted = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            for job_id in dict.fromkeys(str(value) for value in job_ids):
                row = connection.execute(
                    "SELECT * FROM jobs WHERE linkedin_job_id=? AND suppress_auto_generation=1",
                    (job_id,),
                ).fetchone()
                if row is None:
                    continue
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO generation_tasks
                       (linkedin_job_id,job_hash,completed_hash,state,requested_at)
                       VALUES (?,?,NULL,'MANUAL',?)""",
                    (job_id, _job_hash(row), _now()),
                )
                inserted += max(0, int(cursor.rowcount))
            connection.commit()
            return inserted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def apply_auto_age_limit(self, max_age_days: int) -> dict[str, int]:
        """Skip old automatic work, while allowing explicit user requests through."""
        if type(max_age_days) is not int or not 1 <= max_age_days <= 3_650:
            raise JobStoreError("L’età massima automatica deve essere compresa tra 1 e 3.650 giorni.")
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        connection = self.store._connection()
        skipped = released = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT t.linkedin_job_id,t.state,t.error_code,j.first_saved_at
                   FROM generation_tasks AS t
                   JOIN jobs AS j ON j.linkedin_job_id=t.linkedin_job_id
                   WHERE j.status!='IGNORED' AND t.manual_request=0 AND
                     (t.state='PENDING' OR (t.state='STALE' AND t.error_code IN (?,?)))""",
                (AUTO_AGE_LIMIT_ERROR, AUTO_AGE_UNKNOWN_ERROR),
            ).fetchall()
            now = _now()
            unverified = 0
            for row in rows:
                saved_text = row["first_saved_at"]
                too_old = False
                verifiable = True
                if isinstance(saved_text, str) and saved_text:
                    try:
                        saved_at = datetime.fromisoformat(saved_text)
                        if saved_at.tzinfo is None:
                            saved_at = saved_at.replace(tzinfo=timezone.utc)
                        too_old = saved_at < cutoff
                        verifiable = saved_at <= datetime.now(timezone.utc) + timedelta(minutes=5)
                    except ValueError:
                        # An unparseable date is unknown, not proof that a job is old.
                        verifiable = False
                else:
                    verifiable = False
                if not verifiable:
                    if row["state"] != "STALE" or row["error_code"] != AUTO_AGE_UNKNOWN_ERROR:
                        connection.execute(
                            """UPDATE generation_tasks SET state='STALE',
                               stage='Da verificare · data del primo salvataggio locale non leggibile',
                               error_code=?,error_request_id=NULL
                               WHERE linkedin_job_id=? AND manual_request=0""",
                            (AUTO_AGE_UNKNOWN_ERROR, row["linkedin_job_id"]),
                        )
                        unverified += 1
                    continue
                if too_old:
                    connection.execute(
                        """UPDATE generation_tasks SET state='STALE',stage=?,error_code=?,
                           error_request_id=NULL WHERE linkedin_job_id=? AND manual_request=0""",
                        (f"Saltato automaticamente · primo salvataggio locale oltre {max_age_days} giorni",
                         AUTO_AGE_LIMIT_ERROR, row["linkedin_job_id"]),
                    )
                    skipped += int(row["state"] != "STALE" or row["error_code"] != AUTO_AGE_LIMIT_ERROR)
                elif row["state"] == "STALE" and row["error_code"] in {
                    AUTO_AGE_LIMIT_ERROR, AUTO_AGE_UNKNOWN_ERROR
                }:
                    connection.execute(
                        """UPDATE generation_tasks SET state='PENDING',stage='',error_code=NULL,
                           error_request_id=NULL,requested_at=? WHERE linkedin_job_id=? AND manual_request=0""",
                        (now, row["linkedin_job_id"]),
                    )
                    released += 1
            connection.commit()
            return {"skipped": skipped, "released": released, "unverified": unverified}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def queue_manual(self, job_id: str) -> dict[str, Any]:
        connection = self.store._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT linkedin_job_id, title, company, location, description FROM jobs WHERE linkedin_job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise JobStoreError("No saved job found with this ID.")
            row = connection.execute(
                "SELECT state FROM generation_tasks WHERE linkedin_job_id = ?", (job_id,)
            ).fetchone()
            if row is not None and row["state"] == "RUNNING":
                raise JobStoreError("La posizione e gia in coda o in elaborazione.")
            digest = _job_hash(job)
            connection.execute(
                """INSERT INTO generation_tasks
                   (linkedin_job_id, job_hash, state, manual_request, requested_at)
                   VALUES (?, ?, 'PENDING', 1, ?)
                   ON CONFLICT(linkedin_job_id) DO UPDATE SET
                     job_hash = excluded.job_hash, state = 'PENDING', manual_request = 1,
                     stage = '', requested_at = excluded.requested_at,
                     error_code = NULL, error_request_id = NULL""",
                (job_id, digest, _now()),
            )
            connection.commit()
            return self.get(job_id) or {}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_next(self, *, auto_enabled: bool) -> dict[str, Any] | None:
        connection = self.store._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT t.* FROM generation_tasks AS t
                   JOIN jobs AS j ON j.linkedin_job_id = t.linkedin_job_id
                   WHERE t.state = 'PENDING'
                     AND (t.manual_request = 1 OR
                          (j.linkedin_saved=1 AND j.status != 'IGNORED' AND ? = 1))
                   ORDER BY t.manual_request DESC, t.requested_at, t.linkedin_job_id LIMIT 1""",
                (int(auto_enabled),),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """UPDATE generation_tasks SET state = 'RUNNING', stage = 'Preparazione',
                   error_code = NULL, error_request_id = NULL WHERE linkedin_job_id = ?""",
                (row["linkedin_job_id"],),
            )
            connection.commit()
            return dict(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def has_queued(self, *, auto_enabled: bool) -> bool:
        connection = self.store._connection()
        try:
            row = connection.execute(
                """SELECT 1 FROM generation_tasks AS t
                   JOIN jobs AS j ON j.linkedin_job_id = t.linkedin_job_id
                   WHERE t.state='PENDING'
                     AND (t.manual_request=1 OR
                          (j.linkedin_saved=1 AND j.status != 'IGNORED' AND ?=1)) LIMIT 1""",
                (int(auto_enabled),),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def daily_limit_status(self, settings: dict[str, Any]) -> dict[str, Any] | None:
        usage = self.summary()["usage"]
        total_limit = int(settings["max_daily_calls"])
        total_used = int(usage["calls_today"])
        if total_used >= total_limit:
            return {"name": "max_daily_calls", "used": total_used, "limit": total_limit}
        agent_limit = int(settings["max_daily_agent_calls"])
        agent_used = int(usage["today_by_operation"].get("agent", 0))
        if agent_limit == 0 or agent_used >= agent_limit:
            return {"name": "max_daily_agent_calls", "used": agent_used, "limit": agent_limit}
        return None

    def preview_bulk_regeneration(self, scope_days: int | None = None,
                                  max_age_days: int | None = None,
                                  only_new: bool = False) -> dict[str, Any]:
        if type(only_new) is not bool:
            raise JobStoreError("La selezione delle nuove cover letter non è valida.")
        if scope_days is not None and (type(scope_days) is not int or not 1 <= scope_days <= 3650):
            raise JobStoreError("Scegli un intervallo da 1 a 3.650 giorni oppure tutti gli annunci.")
        if max_age_days is not None and (type(max_age_days) is not int or not 1 <= max_age_days <= 3_650):
            raise JobStoreError("L’età massima automatica deve essere compresa tra 1 e 3.650 giorni.")
        connection = self.store._connection()
        try:
            rows = connection.execute(
                """SELECT j.linkedin_job_id,j.first_saved_at,j.status,j.linkedin_saved,
                          m.cover_letter_text,t.state,
                          EXISTS(SELECT 1 FROM cover_letters AS f
                                 WHERE f.linkedin_job_id=j.linkedin_job_id) AS has_file
                   FROM jobs AS j
                   LEFT JOIN job_materials AS m ON m.linkedin_job_id=j.linkedin_job_id
                   LEFT JOIN generation_tasks AS t ON t.linkedin_job_id=j.linkedin_job_id
                   ORDER BY j.first_saved_at DESC"""
            ).fetchall()
            now = datetime.now(timezone.utc)
            scope_cutoff = now - timedelta(days=scope_days) if scope_days is not None else None
            age_cutoff = now - timedelta(days=max_age_days) if max_age_days is not None else None
            job_ids: list[str] = []
            scoped_candidates = 0
            stored_letters = sum(
                bool(str(row["cover_letter_text"] or "").strip()) or bool(row["has_file"])
                for row in rows
            )
            excluded = {"fuori_intervallo": 0, "oltre_soglia_eta": 0,
                        "eta_non_verificabile": 0, "non_corrisponde_al_tipo": 0,
                        "gia_in_coda_o_in_corso": 0, "stato_non_rigenerabile": 0}
            eligible_states = {"READY", "REVIEW", "ERROR", "STALE", "MANUAL"}
            for row in rows:
                has_letter = bool(str(row["cover_letter_text"] or "").strip()) or bool(row["has_file"])
                if scope_cutoff is not None or age_cutoff is not None:
                    try:
                        saved_at = datetime.fromisoformat(str(row["first_saved_at"]))
                    except (TypeError, ValueError):
                        excluded["eta_non_verificabile"] += 1
                        continue
                    if saved_at.tzinfo is None:
                        saved_at = saved_at.replace(tzinfo=timezone.utc)
                    if saved_at > now + timedelta(minutes=5):
                        excluded["eta_non_verificabile"] += 1
                        continue
                    if scope_cutoff is not None and saved_at < scope_cutoff:
                        excluded["fuori_intervallo"] += 1
                        continue
                    if age_cutoff is not None and saved_at < age_cutoff:
                        excluded["oltre_soglia_eta"] += 1
                        continue
                if has_letter == only_new:
                    excluded["non_corrisponde_al_tipo"] += 1
                    continue
                if only_new and (not row["linkedin_saved"] or row["status"] == "IGNORED"):
                    excluded["non_corrisponde_al_tipo"] += 1
                    continue
                scoped_candidates += 1
                if row["state"] in {"PENDING", "RUNNING"}:
                    excluded["gia_in_coda_o_in_corso"] += 1
                    continue
                if row["state"] not in eligible_states:
                    excluded["stato_non_rigenerabile"] += 1
                    continue
                job_ids.append(str(row["linkedin_job_id"]))
            if len(job_ids) > 1_000:
                raise JobStoreError("La selezione supera 1.000 annunci; scegli un intervallo più breve.")
            return {"scope_days": scope_days, "max_age_days": max_age_days,
                    "only_new": only_new,
                    "job_ids": job_ids, "eligible_count": len(job_ids),
                    "total_count": len(rows), "stored_cover_letter_count": stored_letters,
                    "scope_cover_letter_count": scoped_candidates,
                    "scope_candidate_count": scoped_candidates,
                    "excluded": excluded}
        finally:
            connection.close()

    def queue_bulk_regeneration(self, batch_id: str, job_ids: list[str],
                                scope_days: int | None = None,
                                max_age_days: int | None = None,
                                only_new: bool = False) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", batch_id) or not job_ids or len(job_ids) > 1_000:
            raise JobStoreError("Piano di rigenerazione non valido o scaduto.")
        if type(only_new) is not bool:
            raise JobStoreError("La selezione delle nuove cover letter non è valida.")
        connection = self.store._connection()
        now = _now()
        queued = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            if scope_days is not None and (type(scope_days) is not int or not 1 <= scope_days <= 3650):
                raise JobStoreError("Intervallo di rigenerazione non valido.")
            if max_age_days is not None and (type(max_age_days) is not int or not 1 <= max_age_days <= 3_650):
                raise JobStoreError("Età massima per la rigenerazione non valida.")
            connection.execute(
                "INSERT INTO bulk_regeneration_batches(batch_id,created_at,scope_days) VALUES (?,?,?)",
                (batch_id, now, scope_days),
            )
            placeholders = ",".join("?" for _ in job_ids)
            rows = connection.execute(
                f"""SELECT j.linkedin_job_id,j.first_saved_at,j.status,j.linkedin_saved,
                           j.title,j.company,j.location,j.description,
                           m.cover_letter_text,t.*,
                           EXISTS(SELECT 1 FROM cover_letters AS f
                                  WHERE f.linkedin_job_id=j.linkedin_job_id) AS has_file
                    FROM jobs AS j
                    LEFT JOIN job_materials AS m ON m.linkedin_job_id=j.linkedin_job_id
                    LEFT JOIN generation_tasks AS t ON t.linkedin_job_id=j.linkedin_job_id
                    WHERE j.linkedin_job_id IN ({placeholders})""",
                tuple(job_ids),
            ).fetchall()
            requested = set(job_ids)
            found = {str(row["linkedin_job_id"]) for row in rows}
            if requested - found:
                raise JobStoreError("Uno o più annunci non sono più disponibili per la rigenerazione.")
            eligible_states = {"READY", "REVIEW", "ERROR", "STALE", "MANUAL"}
            now_utc = datetime.now(timezone.utc)
            cutoff = now_utc - timedelta(days=scope_days) if scope_days is not None else None
            age_cutoff = now_utc - timedelta(days=max_age_days) if max_age_days is not None else None
            for row in rows:
                has_letter = bool(str(row["cover_letter_text"] or "").strip()) or bool(row["has_file"])
                if has_letter == only_new:
                    raise JobStoreError("Un annuncio selezionato non è più idoneo.")
                if only_new and (not row["linkedin_saved"] or row["status"] == "IGNORED"):
                    raise JobStoreError("Un annuncio non è più presente nei Salvati di LinkedIn.")
                if cutoff is not None or age_cutoff is not None:
                    try:
                        saved_at = datetime.fromisoformat(str(row["first_saved_at"]))
                    except (TypeError, ValueError) as error:
                        raise JobStoreError("La data di un annuncio non è verificabile; calcola una nuova stima.") from error
                    if saved_at.tzinfo is None:
                        saved_at = saved_at.replace(tzinfo=timezone.utc)
                    if ((cutoff is not None and saved_at < cutoff) or
                            (age_cutoff is not None and saved_at < age_cutoff) or
                            saved_at > now_utc + timedelta(minutes=5)):
                        raise JobStoreError("Un annuncio non rientra più nell’intervallo selezionato.")
                if row["state"] not in eligible_states:
                    raise JobStoreError("Un annuncio selezionato è già in coda, in corso o non rigenerabile.")
                job_id = str(row["linkedin_job_id"])
                connection.execute(
                    """INSERT INTO bulk_regeneration_items
                       (batch_id,linkedin_job_id,queued_requested_at,previous_state,previous_stage,
                        previous_manual_request,previous_completed_hash,previous_completed_at,previous_gaps,
                        previous_error_code,previous_error_request_id,previous_requested_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch_id, job_id, now, row["state"], row["stage"], row["manual_request"],
                     row["completed_hash"], row["completed_at"], row["gaps"], row["error_code"],
                     row["error_request_id"], row["requested_at"]),
                )
                connection.execute(
                    """UPDATE generation_tasks SET state='PENDING',manual_request=1,stage='',requested_at=?,
                       error_code=NULL,error_request_id=NULL WHERE linkedin_job_id=?""",
                    (now, job_id),
                )
                queued += 1
            if not queued:
                raise JobStoreError("Non ci sono cover letter idonee da rigenerare.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.bulk_regeneration_status(batch_id)

    def bulk_regeneration_status(self, batch_id: str) -> dict[str, Any] | None:
        connection = self.store._connection()
        try:
            batch = connection.execute(
                "SELECT batch_id,created_at,scope_days,cancel_requested FROM bulk_regeneration_batches WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                return None
            rows = connection.execute(
                """SELECT i.status AS item_status,i.queued_requested_at,i.previous_error_request_id,
                          i.previous_completed_at,
                          t.state,t.completed_at,t.error_request_id
                   FROM bulk_regeneration_items AS i
                   JOIN generation_tasks AS t ON t.linkedin_job_id=i.linkedin_job_id
                   WHERE i.batch_id=?""", (batch_id,),
            ).fetchall()
            counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0}
            for row in rows:
                if row["item_status"] == "CANCELLED":
                    counts["cancelled"] += 1
                elif row["state"] == "PENDING":
                    counts["queued"] += 1
                elif row["state"] == "RUNNING":
                    counts["running"] += 1
                elif (row["state"] in {"READY", "REVIEW"} and row["completed_at"]
                      and row["completed_at"] != row["previous_completed_at"]
                      and row["completed_at"] > row["queued_requested_at"]):
                    counts["completed"] += 1
                elif row["state"] == "ERROR" and row["error_request_id"] != row["previous_error_request_id"]:
                    counts["failed"] += 1
                else:
                    counts["queued"] += 1
            return {**dict(batch), "count": len(rows), "counts": counts,
                    "complete": counts["queued"] == 0 and counts["running"] == 0,
                    "cancel_requested": bool(batch["cancel_requested"])}
        finally:
            connection.close()

    def cancel_bulk_regeneration(self, batch_id: str) -> dict[str, Any] | None:
        connection = self.store._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                "SELECT batch_id FROM bulk_regeneration_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if batch is None:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE bulk_regeneration_batches SET cancel_requested=1 WHERE batch_id=?", (batch_id,)
            )
            items = connection.execute(
                """SELECT i.*,t.state,t.requested_at FROM bulk_regeneration_items AS i
                   JOIN generation_tasks AS t ON t.linkedin_job_id=i.linkedin_job_id
                   WHERE i.batch_id=? AND i.status='QUEUED'""", (batch_id,),
            ).fetchall()
            for item in items:
                if item["state"] != "PENDING" or item["requested_at"] != item["queued_requested_at"]:
                    continue
                connection.execute(
                    """UPDATE generation_tasks SET state=?,stage=?,manual_request=?,completed_hash=?,completed_at=?,
                       gaps=?,error_code=?,error_request_id=?,requested_at=?
                       WHERE linkedin_job_id=? AND state='PENDING' AND requested_at=?""",
                    (item["previous_state"], item["previous_stage"], item["previous_manual_request"],
                     item["previous_completed_hash"], item["previous_completed_at"], item["previous_gaps"],
                     item["previous_error_code"], item["previous_error_request_id"], item["previous_requested_at"],
                     item["linkedin_job_id"], item["queued_requested_at"]),
                )
                connection.execute(
                    "UPDATE bulk_regeneration_items SET status='CANCELLED' WHERE batch_id=? AND linkedin_job_id=?",
                    (batch_id, item["linkedin_job_id"]),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.bulk_regeneration_status(batch_id)

    def mark_pending_for_daily_limit(self) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE generation_tasks SET stage=? WHERE state='PENDING' AND stage != ?",
                (DAILY_LIMIT_WAIT_STAGE, DAILY_LIMIT_WAIT_STAGE),
            )
            connection.commit()
        finally:
            connection.close()

    def clear_pending_daily_limit(self) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                """UPDATE generation_tasks SET stage='', error_code=NULL, error_request_id=NULL
                   WHERE state='PENDING' AND stage=?""",
                (DAILY_LIMIT_WAIT_STAGE,),
            )
            connection.commit()
        finally:
            connection.close()

    def defer_running_for_daily_limit(self, job_id: str, job_hash: str, request_id: str) -> bool:
        connection = self.store._connection()
        try:
            cursor = connection.execute(
                """UPDATE generation_tasks SET state='PENDING', stage=?, error_code='usage_limit',
                   error_request_id=?, requested_at=?
                   WHERE linkedin_job_id=? AND job_hash=? AND state='RUNNING'""",
                (DAILY_LIMIT_WAIT_STAGE, request_id[:64], _now(), job_id, job_hash),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def set_stage(self, job_id: str, job_hash: str, stage: str) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                """UPDATE generation_tasks SET stage = ?
                   WHERE linkedin_job_id = ? AND job_hash = ? AND state = 'RUNNING'""",
                (stage[:80], job_id, job_hash),
            )
            connection.commit()
        finally:
            connection.close()

    def is_running(self, job_id: str, job_hash: str) -> bool:
        connection = self.store._connection()
        try:
            row = connection.execute(
                "SELECT state, job_hash FROM generation_tasks WHERE linkedin_job_id = ?", (job_id,)
            ).fetchone()
            return bool(row and row["state"] == "RUNNING" and row["job_hash"] == job_hash)
        finally:
            connection.close()

    def fail(self, job_id: str, job_hash: str, code: str, request_id: str) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                """UPDATE generation_tasks SET state = 'ERROR', stage = '', manual_request = 0,
                   error_code = ?, error_request_id = ?
                   WHERE linkedin_job_id = ? AND job_hash = ? AND state = 'RUNNING'""",
                (code[:64], request_id, job_id, job_hash),
            )
            connection.commit()
        finally:
            connection.close()

    def requeue_running(self, job_id: str, job_hash: str) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                """UPDATE generation_tasks SET state = 'PENDING', stage = ''
                   WHERE linkedin_job_id = ? AND job_hash = ? AND state = 'RUNNING'""",
                (job_id, job_hash),
            )
            connection.commit()
        finally:
            connection.close()

    def mark_stale(self) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE generation_tasks SET state = 'STALE' WHERE state IN ('READY','REVIEW')"
            )
            connection.execute(
                "UPDATE generation_tasks SET state = 'PENDING', stage = '' WHERE state = 'RUNNING'"
            )
            connection.commit()
        finally:
            connection.close()

    def mark_manual_edit(self, job_id: str) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                """UPDATE generation_tasks SET state = 'MANUAL', stage = '', manual_request = 0
                   WHERE linkedin_job_id = ?""", (job_id,)
            )
            connection.commit()
        finally:
            connection.close()

    def get(self, job_id: str) -> dict[str, Any] | None:
        connection = self.store._connection()
        try:
            row = connection.execute("SELECT * FROM generation_tasks WHERE linkedin_job_id = ?", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def all_states(self) -> dict[str, dict[str, Any]]:
        connection = self.store._connection()
        try:
            rows = connection.execute("SELECT * FROM generation_tasks").fetchall()
            return {str(row["linkedin_job_id"]): dict(row) for row in rows}
        finally:
            connection.close()

    def summary(self) -> dict[str, Any]:
        connection = self.store._connection()
        try:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM generation_tasks GROUP BY state"
            ).fetchall()
            last = connection.execute("SELECT MAX(completed_at) AS last_completed FROM generation_tasks").fetchone()
            usage = connection.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
                "COALESCE(SUM(search_calls), 0) AS search_calls, "
                "COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens, "
                "COALESCE(SUM(duration_ms), 0) AS duration_ms FROM "
                "(SELECT input_tokens,output_tokens,search_calls,cached_input_tokens,duration_ms FROM ai_usage UNION ALL "
                "SELECT input_tokens,output_tokens,search_calls,cached_input_tokens,duration_ms FROM api_operation_usage)"
            ).fetchone()
            today_start, tomorrow_start = _utc_day_bounds()
            calls_today = connection.execute(
                "SELECT COUNT(*) AS n FROM "
                "(SELECT created_at FROM ai_usage WHERE created_at >= ? AND created_at < ? UNION ALL "
                "SELECT created_at FROM api_operation_usage WHERE created_at >= ? AND created_at < ?)",
                (today_start, tomorrow_start, today_start, tomorrow_start),
            ).fetchone()["n"]
            daily_rows = connection.execute(
                """SELECT operation,COUNT(*) AS n FROM (
                       SELECT operation,created_at FROM ai_usage WHERE created_at >= ? AND created_at < ? UNION ALL
                       SELECT operation,created_at FROM api_operation_usage WHERE created_at >= ? AND created_at < ?
                   ) GROUP BY operation""",
                (today_start, tomorrow_start, today_start, tomorrow_start),
            ).fetchall()
            by_operation: dict[str, int] = {}
            for row in daily_rows:
                operation = str(row["operation"] or "agent")
                display = "feedback_revision" if operation == "candidate_context" else operation
                by_operation[display] = by_operation.get(display, 0) + int(row["n"])
            return {"counts": {str(row["state"]): int(row["count"]) for row in rows},
                    "last_completed": last["last_completed"] if last else None,
                    "usage": {**dict(usage), "calls_today": int(calls_today),
                              "today_by_operation": by_operation} if usage else
                             {"calls": 0, "input_tokens": 0, "output_tokens": 0, "search_calls": 0,
                              "cached_input_tokens": 0, "duration_ms": 0,
                              "calls_today": int(calls_today), "today_by_operation": by_operation}}
        finally:
            connection.close()

    def usage_profile(self, days: int = 30) -> list[dict[str, Any]]:
        """Return bounded local usage averages, without exposing prompt or result text."""
        days = max(1, min(int(days), 365))
        connection = self.store._connection()
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
            rows = connection.execute(
                """SELECT model,operation,COALESCE(stage,'') AS stage,COUNT(*) AS calls,
                          AVG(input_tokens) AS avg_input_tokens,
                          AVG(output_tokens) AS avg_output_tokens,
                          AVG(search_calls) AS avg_search_calls,
                          AVG(cached_input_tokens) AS avg_cached_input_tokens,
                          AVG(duration_ms) AS avg_duration_ms
                   FROM api_operation_usage WHERE created_at >= ?
                   GROUP BY model,operation,COALESCE(stage,'') ORDER BY calls DESC""",
                (cutoff,),
            ).fetchall()
            return [{key: (int(row[key]) if key == "calls" else
                           round(float(row[key] or 0), 1) if key.startswith("avg_") else str(row[key]))
                     for key in ("model", "operation", "stage", "calls", "avg_input_tokens",
                                 "avg_output_tokens", "avg_search_calls", "avg_cached_input_tokens",
                                 "avg_duration_ms")} for row in rows]
        finally:
            connection.close()

    def record_usage(self, job_id: str, model: str, usage: dict[str, int], operation: str = "agent") -> None:
        reservation = self.reserve_api_call(job_id, model, operation, daily_limit=2_000)
        if reservation is not None:
            self.finish_api_call(reservation, usage)

    def reserve_api_call(self, job_id: str | None, model: str, operation: str, *,
                         daily_limit: int, category_limit: int | None = None,
                         stage: str = "") -> int | None:
        connection = self.store._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            today_start, tomorrow_start = _utc_day_bounds()
            count = int(connection.execute(
                """SELECT (SELECT COUNT(*) FROM api_operation_usage WHERE created_at>=? AND created_at<?) +
                          (SELECT COUNT(*) FROM ai_usage WHERE created_at>=? AND created_at<?) AS n""",
                (today_start, tomorrow_start, today_start, tomorrow_start),
            ).fetchone()["n"])
            if count >= daily_limit:
                connection.rollback()
                return None
            if category_limit is not None:
                category_ops = {
                    "feedback_revision": ("feedback_revision", "candidate_context"),
                }.get(operation, (operation,))
                placeholders = ",".join("?" for _ in category_ops)
                used = int(connection.execute(
                    f"""SELECT COUNT(*) AS n FROM (
                            SELECT operation,created_at FROM ai_usage
                              WHERE created_at>=? AND created_at<? AND operation IN ({placeholders}) UNION ALL
                            SELECT operation,created_at FROM api_operation_usage
                              WHERE created_at>=? AND created_at<? AND operation IN ({placeholders})
                        )""",
                    (today_start, tomorrow_start, *category_ops,
                     today_start, tomorrow_start, *category_ops),
                ).fetchone()["n"])
                if used >= category_limit:
                    connection.rollback()
                    return None
            cursor = connection.execute(
                "INSERT INTO api_operation_usage(linkedin_job_id,model,operation,stage,created_at) VALUES (?,?,?,?,?)",
                (job_id, model[:80], operation[:32], str(stage or "")[:80], _now()),
            )
            reservation = int(cursor.lastrowid)
            connection.commit()
            return reservation
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_api_call(self, reservation: int, usage: dict[str, int]) -> None:
        connection = self.store._connection()
        try:
            connection.execute(
                """UPDATE api_operation_usage SET input_tokens=?, output_tokens=?, search_calls=?,
                          cached_input_tokens=?, duration_ms=? WHERE id=?""",
                (int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
                 int(usage.get("search_calls", 0)), int(usage.get("cached_input_tokens", 0)),
                 int(usage.get("duration_ms", 0)), reservation),
            )
            connection.commit()
        finally:
            connection.close()

    def commit_result(
        self, job_id: str, job_hash: str, result: dict[str, Any],
        pdf_bytes: bytes | None, docx_bytes: bytes | None, model: str,
    ) -> bool:
        """Atomically save reviewed text; explicit legacy callers may also supply files."""
        connection = self.store._connection()
        temp_paths: list[Path] = []
        final_paths: list[Path] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT state, job_hash FROM generation_tasks WHERE linkedin_job_id = ?", (job_id,)
            ).fetchone()
            if task is None or task["state"] != "RUNNING" or task["job_hash"] != job_hash:
                connection.rollback()
                return False
            data_root = self.store.database_path.parent.resolve()
            folder = (data_root / "cover_letters" / job_id).resolve()
            if not folder.is_relative_to(data_root / "cover_letters"):
                raise JobStoreError("Invalid generated letter folder.")
            if pdf_bytes is not None or docx_bytes is not None:
                folder.mkdir(parents=True, exist_ok=True)
            now = _now()
            for file_format, contents in (("pdf", pdf_bytes), ("docx", docx_bytes)):
                if contents is None:
                    continue
                if not contents or len(contents) > 20 * 1024 * 1024:
                    raise JobStoreError("Generated document size is invalid.")
                row = connection.execute(
                    """SELECT COALESCE(MAX(version), 0) + 1 AS version FROM cover_letters
                       WHERE linkedin_job_id = ? AND file_format = ?""", (job_id, file_format)
                ).fetchone()
                version = int(row["version"])
                final = folder / f"cover-letter-v{version:02d}.{file_format}"
                while final.exists():
                    version += 1
                    final = folder / f"cover-letter-v{version:02d}.{file_format}"
                temp = folder / f".{secrets.token_hex(8)}.tmp"
                with temp.open("xb") as output:
                    output.write(contents)
                    output.flush()
                    os.fsync(output.fileno())
                temp_paths.append(temp)
                os.replace(temp, final)
                final_paths.append(final)
                connection.execute(
                    """INSERT INTO cover_letters
                       (linkedin_job_id, version, file_format, relative_path, original_filename, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (job_id, version, file_format, final.relative_to(data_root).as_posix(),
                     f"AI cover letter.{file_format}", now),
                )
            connection.execute(
                """INSERT INTO job_materials
                   (linkedin_job_id, company_analysis, role_analysis, personalization_notes,
                    cover_letter_text, agent_name, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(linkedin_job_id) DO UPDATE SET
                     company_analysis = excluded.company_analysis,
                     role_analysis = excluded.role_analysis,
                     personalization_notes = excluded.personalization_notes,
                     cover_letter_text = excluded.cover_letter_text,
                     agent_name = excluded.agent_name, updated_at = excluded.updated_at""",
                (job_id, result["company_analysis"], result["role_analysis"],
                 result["personalization_notes"], result["cover_letter_text"],
                 f"OpenAI · {model}"[:200], now),
            )
            self._save_letter_version(connection, job_id, result["cover_letter_text"], "generation", now)
            connection.execute(
                """UPDATE generation_tasks SET state = ?, stage = '', manual_request = 0,
                   completed_hash = ?, completed_at = ?, gaps = ?,
                   error_code = NULL, error_request_id = NULL
                   WHERE linkedin_job_id = ?""",
                ("REVIEW" if result["needs_review"] or result["gaps"] else "READY",
                 job_hash, now, result["gaps"], job_id),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            for path in final_paths:
                path.unlink(missing_ok=True)
            raise
        finally:
            for path in temp_paths:
                path.unlink(missing_ok=True)
            connection.close()

    def archive_edited_letter(
        self, job_id: str, source_text: str, pdf_bytes: bytes, docx_bytes: bytes,
    ) -> None:
        """Create a matching PDF/DOCX pair for the current saved manual text."""
        connection = self.store._connection()
        temp_paths: list[Path] = []
        final_paths: list[Path] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT cover_letter_text FROM job_materials WHERE linkedin_job_id = ?", (job_id,)
            ).fetchone()
            if row is None or not row["cover_letter_text"]:
                raise JobStoreError("Salva prima il testo della cover letter.")
            if row["cover_letter_text"] != source_text:
                raise JobStoreError("La cover letter e cambiata: aggiorna la pagina e riprova.")
            data_root = self.store.database_path.parent.resolve()
            folder = (data_root / "cover_letters" / job_id).resolve()
            if not folder.is_relative_to(data_root / "cover_letters"):
                raise JobStoreError("Invalid letter folder.")
            folder.mkdir(parents=True, exist_ok=True)
            now = _now()
            for file_format, contents in (("pdf", pdf_bytes), ("docx", docx_bytes)):
                if not contents or len(contents) > 20 * 1024 * 1024:
                    raise JobStoreError("Generated document size is invalid.")
                row = connection.execute(
                    """SELECT COALESCE(MAX(version), 0) + 1 AS version FROM cover_letters
                       WHERE linkedin_job_id = ? AND file_format = ?""", (job_id, file_format)
                ).fetchone()
                version = int(row["version"])
                final = folder / f"cover-letter-v{version:02d}.{file_format}"
                while final.exists():
                    version += 1
                    final = folder / f"cover-letter-v{version:02d}.{file_format}"
                temp = folder / f".{secrets.token_hex(8)}.tmp"
                with temp.open("xb") as output:
                    output.write(contents)
                    output.flush()
                    os.fsync(output.fileno())
                temp_paths.append(temp)
                os.replace(temp, final)
                final_paths.append(final)
                connection.execute(
                    """INSERT INTO cover_letters
                       (linkedin_job_id, version, file_format, relative_path, original_filename, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (job_id, version, file_format, final.relative_to(data_root).as_posix(),
                     f"Edited cover letter.{file_format}", now),
                )
            connection.execute(
                "UPDATE generation_tasks SET state = 'MANUAL', stage = '' WHERE linkedin_job_id = ?",
                (job_id,),
            )
            self._save_letter_version(connection, job_id, source_text, "manual", now)
            connection.commit()
        except Exception:
            connection.rollback()
            for path in final_paths:
                path.unlink(missing_ok=True)
            raise
        finally:
            for path in temp_paths:
                path.unlink(missing_ok=True)
            connection.close()

    @staticmethod
    def _save_letter_version(connection: sqlite3.Connection, job_id: str, text: str,
                             source: str, created_at: str) -> int:
        active = connection.execute(
            "SELECT version,letter_text FROM letter_versions WHERE linkedin_job_id = ? AND active = 1",
            (job_id,),
        ).fetchone()
        if active is not None and active["letter_text"] == text:
            return int(active["version"])
        connection.execute("UPDATE letter_versions SET active = 0 WHERE linkedin_job_id = ?", (job_id,))
        version = int(connection.execute(
            "SELECT COALESCE(MAX(version),0)+1 AS n FROM letter_versions WHERE linkedin_job_id = ?",
            (job_id,),
        ).fetchone()["n"])
        connection.execute(
            """INSERT INTO letter_versions
               (linkedin_job_id,version,letter_text,source,active,created_at) VALUES (?,?,?,?,1,?)""",
            (job_id, version, text, source[:32], created_at),
        )
        return version

    def letter_versions(self, job_id: str, limit: int = 50) -> list[dict[str, Any]]:
        connection = self.store._connection()
        try:
            rows = connection.execute(
                """SELECT version,letter_text,source,active,created_at FROM letter_versions
                   WHERE linkedin_job_id = ? ORDER BY version DESC LIMIT ?""",
                (job_id, max(1, min(limit, 100))),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def ensure_current_version(self, job_id: str, text: str) -> int | None:
        if not text:
            return None
        connection = self.store._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = self._save_letter_version(connection, job_id, text, "legacy", _now())
            connection.commit()
            return version
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def restore_letter_version(self, job_id: str, version: int) -> dict[str, Any]:
        connection = self.store._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT letter_text FROM letter_versions WHERE linkedin_job_id = ? AND version = ?",
                (job_id, version),
            ).fetchone()
            if row is None:
                raise JobStoreError("Versione cover letter non trovata.")
            connection.execute("UPDATE letter_versions SET active = 0 WHERE linkedin_job_id = ?", (job_id,))
            connection.execute(
                "UPDATE letter_versions SET active = 1 WHERE linkedin_job_id = ? AND version = ?",
                (job_id, version),
            )
            connection.execute(
                """UPDATE job_materials SET cover_letter_text = ?, updated_at = ?
                   WHERE linkedin_job_id = ?""", (row["letter_text"], _now(), job_id),
            )
            connection.execute(
                "UPDATE generation_tasks SET state = 'MANUAL', stage = '' WHERE linkedin_job_id = ?",
                (job_id,),
            )
            connection.commit()
            return {"version": version, "letter_text": str(row["letter_text"])}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
