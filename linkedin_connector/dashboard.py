"""Loopback-only dashboard for saved jobs and associated application materials."""

from __future__ import annotations

import email.policy
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import webbrowser
import zipfile
from datetime import datetime, timezone
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import Error as PlaywrightError

from linkedin_connector.ai_config import (
    AIConfigurationError, AISettingsStore, APIKeyStore, MODELS, default_settings,
)
from linkedin_connector.ai_provider import AIProviderError, OpenAIProvider, WEB_SEARCH_MODEL
from linkedin_connector.application_store import (
    ApplicationStore, MAX_KNOWLEDGE_FILE_BYTES, MAX_KNOWLEDGE_FILES, MAX_KNOWLEDGE_TEXT_CHARS,
)
from linkedin_connector.cost_estimator import estimate_bulk_cost, estimate_complete_application_cost
from linkedin_connector.database import JobStore, JobStoreError
from linkedin_connector.generation import GenerationCoordinator
from linkedin_connector.generation_store import GenerationStore
from linkedin_connector.letter_documents import render_letter_documents, render_letter_document
from linkedin_connector.observability import (
    MAX_LOG_LIMIT,
    LogQueryError,
    bind_request_id,
    close_logging,
    configure_logging,
    log_event,
    log_exception,
    logging_is_configured,
    new_request_id,
    read_recent_events,
)
from linkedin_connector.profile import (
    AgentPromptStore,
    CandidateProfileStore,
    LocalProfileError,
    MAX_AGENT_MARKDOWN_CHARS,
    SUPPORTED_CV_FORMATS,
    extract_cv_text,
)
from linkedin_connector.research_pages import research_page_cache_key
from linkedin_connector.reader import (
    LinkedInReadError, ManualLinkedInActionRequired, read_all_saved_jobs, read_new_saved_jobs,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ASSETS = PROJECT_ROOT / "dashboard"
MAX_JSON_BYTES = 150_000
MAX_AGENT_MARKDOWN_BYTES = 400_000
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_CV_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_CLIENT_LOG_BYTES = 12_000
MAX_CLIENT_LOGS_PER_MINUTE = 20
CLIENT_LOG_KINDS = {"javascript_error", "unhandled_rejection", "api_failure"}
CLIENT_REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{16}$")
CLIENT_ERROR_TYPES = {
    "AbortError",
    "AggregateError",
    "DataCloneError",
    "DOMException",
    "Error",
    "EvalError",
    "InvalidStateError",
    "NetworkError",
    "NotAllowedError",
    "NotFoundError",
    "NotSupportedError",
    "QuotaExceededError",
    "RangeError",
    "ReferenceError",
    "SecurityError",
    "SyntaxError",
    "TimeoutError",
    "TypeError",
    "URIError",
}
CLIENT_STACK_FRAME_PATTERN = re.compile(r"/(app\.js|styles\.css):(\d+):(\d+)")
JOB_ID_ROUTE = r"(?:\d+|manual_[a-f0-9]{32})"
JOB_PATH_PATTERN = re.compile(rf"^/api/jobs/({JOB_ID_ROUTE})$")
LETTER_FILE_PATTERN = re.compile(r"^/api/letters/(\d+)/file$")
class DashboardRequestError(ValueError):
    """A request failed local dashboard validation."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _safe_client_stack(stack: str) -> str:
    """Keep only local app source locations, never exception text or page data."""
    frames: list[str] = []
    for line in stack.splitlines():
        match = CLIENT_STACK_FRAME_PATTERN.search(line)
        if not match:
            continue
        filename, line_number, column_number = match.groups()
        if int(line_number) <= 10_000_000 and int(column_number) <= 10_000_000:
            frames.append(f"at /{filename}:{line_number}:{column_number}")
        if len(frames) >= 20:
            break
    return "\n".join(frames)


def _safe_client_message(kind: str, error_type: str, status: int) -> str:
    if kind == "api_failure":
        if 100 <= status <= 599:
            return f"Local API request failed (HTTP {status})."
        return "Local API request failed (network error)."
    if error_type not in CLIENT_ERROR_TYPES:
        error_type = "Error"
    return f"Browser reported an unhandled {error_type}."


def _job_fingerprint(job: dict[str, Any]) -> str:
    fields = [job.get(key) for key in (
        "linkedin_job_id", "title", "company", "location", "description")]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()


class SyncCoordinator:
    """Run an explicit, read-only LinkedIn refresh without blocking dashboard requests."""

    def __init__(
        self,
        store: JobStore,
        reader: Callable[[set[str]], list[Any]] = read_new_saved_jobs,
        after_sync: Callable[[], None] | None = None,
        full_reader: Callable[[], list[Any]] = read_all_saved_jobs,
        after_recovery: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.store = store
        self.reader = reader
        self.after_sync = after_sync
        self.full_reader = full_reader
        self.after_recovery = after_recovery
        self._guard = threading.Lock()
        self._state: dict[str, object] = {"running": False, "status": "idle"}

    def snapshot(self) -> dict[str, object]:
        with self._guard:
            result = dict(self._state)
        try:
            result.update(self.store.get_saved_sync_report())
        except Exception as error:
            log_exception(
                "linkedin_sync", "sync.report_read_failed",
                "The persisted Saved-list report could not be read.", error=error,
            )
        return result

    def start(self, *, recovery: bool = False) -> dict[str, object]:
        with self._guard:
            if self._state["running"]:
                if bool(self._state.get("recovery")) != recovery:
                    return {
                        "running": False,
                        "status": "error",
                        "message": "È già in corso un’altra lettura di LinkedIn.",
                    }
                return dict(self._state)
            request_id = new_request_id()
            started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._state = {
                "running": True,
                "status": "running",
                "message": "Sto leggendo gli annunci salvati in LinkedIn…",
                "request_id": request_id,
                "recovery": recovery,
                "started_at": started_at,
            }
            if recovery:
                self._state["report"] = {
                    "started_at": started_at,
                    "ended_at": None,
                    "duration_ms": None,
                    "status": "running",
                    "membership_verified": False,
                    "counts": {},
                    "events": [],
                    "unreadable": [],
                    "read_outcomes": [],
                    "request_id": request_id,
                }
        if recovery:
            try:
                self.store.save_saved_sync_report(self._state["report"])
            except Exception as error:
                log_exception(
                    "linkedin_sync", "sync.report_write_failed",
                    "The running Saved-list report could not be persisted.", error=error,
                )
        log_event(
            logging.INFO,
            "linkedin_sync",
            "sync.started",
            "Full saved-job refresh requested." if recovery else "Incremental LinkedIn refresh requested.",
            context={"request_id": request_id, "recovery": recovery},
        )
        threading.Thread(
            target=self._run,
            args=(request_id, recovery, started_at),
            name="linkedin-read-sync",
            daemon=True,
        ).start()
        return self.snapshot()

    def _run(
        self,
        request_id: str | None = None,
        recovery: bool = False,
        started_at: str | None = None,
    ) -> None:
        request_id = request_id or new_request_id()
        if started_at is None:
            started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        started_perf = time.perf_counter()
        recovery_task_error = False
        count_mismatch = False
        membership_verified = False
        unreadable: list[dict[str, str | None]] = []
        skipped_missing_title: list[dict[str, str | None]] = []
        read_outcomes: list[dict[str, object]] = []
        counts: dict[str, object] = {}
        status = "error"
        message = "Aggiornamento non riuscito. Consulta Diagnostica e log usando il riferimento indicato."
        with bind_request_id(request_id):
            try:
                if recovery:
                    existing_job_ids = self.store.get_linkedin_job_ids(include_unsaved=True)
                    jobs = self.full_reader()
                    count_mismatch = bool(getattr(jobs, "count_mismatch", False))
                    membership_verified = bool(
                        getattr(jobs, "membership_verified", not count_mismatch)
                    )
                    unreadable = list(getattr(jobs, "unreadable", ()))
                    skipped_missing_title = list(getattr(jobs, "skipped_missing_title", ()))
                    read_outcomes = list(getattr(jobs, "outcomes", ()))
                    visible_ids = list(getattr(jobs, "visible_ids", ()) or ())
                    if not visible_ids:
                        visible_ids = list(dict.fromkeys(
                            str(job.linkedin_job_id) for job in jobs
                        ))
                    new_job_ids = [str(job.linkedin_job_id) for job in jobs
                                   if str(job.linkedin_job_id) not in existing_job_ids]
                    counts = self.store.sync_jobs(
                        jobs, suppress_auto_generation=True,
                        reconcile_saved=membership_verified,
                        visible_ids=visible_ids,
                        unreadable=unreadable,
                        skipped_missing_title=skipped_missing_title,
                    )
                    import_events = {
                        str(event.get("linkedin_job_id")): str(event.get("type"))
                        for event in counts.get("events", [])
                        if event.get("linkedin_job_id")
                    }
                    for outcome in read_outcomes:
                        job_id = str(outcome.get("linkedin_job_id") or "")
                        outcome["import_status"] = import_events.get(job_id, "not_processed")
                        if outcome["import_status"] == "not_processed" and job_id:
                            outcome["import_status"] = (
                                "suppressed_forgotten"
                                if counts.get("suppressed_forgotten", 0) else "not_processed"
                            )
                    counts.update({
                        "discovered": len(visible_ids),
                        "suppressed_forgotten": max(0, len(jobs) - counts["total"]),
                        "saved_count_mismatch": count_mismatch,
                        "membership_verified": membership_verified,
                        "read_outcomes": read_outcomes,
                        "skipped_missing_title": len(skipped_missing_title),
                    })
                    if self.after_recovery is not None and new_job_ids:
                        try:
                            self.after_recovery(new_job_ids)
                        except Exception as error:
                            recovery_task_error = True
                            log_exception(
                                "generation", "generation.recovery_tasks_failed",
                                "Jobs were imported but dormant task records could not be initialized.",
                                error=error,
                            )
                else:
                    existing_job_ids = self.store.get_linkedin_job_ids()
                    log_event(
                        logging.INFO,
                        "linkedin_sync",
                        "sync.scan.started",
                        "Starting incremental scan from the newest saved-job cards.",
                        context={"existing_count": len(existing_job_ids)},
                    )
                    jobs = self.reader(existing_job_ids)
                    counts = self.store.sync_jobs(jobs)
                    counts["discovered"] = len(jobs)
                    if self.after_sync is not None:
                        try:
                            self.after_sync()
                        except Exception:
                            log_event(logging.ERROR, "generation", "generation.queue_failed",
                                      "New jobs were saved but the AI queue could not be refreshed.")
                log_event(
                    logging.INFO,
                    "linkedin_sync",
                    "sync.completed",
                    ("Saved-job refresh imported visible cards without reconciling absent jobs."
                     if recovery and count_mismatch and not membership_verified else
                     "Full saved-job refresh completed." if recovery else
                     "Incremental LinkedIn refresh completed."),
                    context=counts,
                )
                status = (
                    "error" if recovery_task_error else
                    "partial" if recovery and (
                        not membership_verified or unreadable or skipped_missing_title
                    ) else
                    "complete"
                )
                if recovery_task_error:
                    message = (
                        "Gli annunci sono stati aggiornati, ma non è stato possibile preparare le attività. "
                        "Nessuna cover letter è stata avviata."
                    )
                elif not membership_verified:
                    message = (
                        "Elenco Salvati incompleto o non verificato: ho mantenuto i dati locali e non ho "
                        "nascosto annunci. Le schede leggibili sono state aggiornate."
                    )
                elif unreadable:
                    message = (
                        "Confronto completato: le descrizioni non leggibili sono state saltate; i dati "
                        "locali esistenti sono conservati e le nuove offerte incomplete non sono state importate."
                    )
                elif skipped_missing_title:
                    message = (
                        "Confronto completato: alcune descrizioni sono state lette ma il titolo "
                        "non è stato recuperato; queste offerte non sono state importate."
                    )
                elif recovery and count_mismatch:
                    message = (
                        "Elenco Salvati aggiornato: due scansioni complete hanno confermato gli stessi post "
                        "anche se il contatore LinkedIn non coincide. I post rimossi sono stati nascosti "
                        "conservando lettere e analisi."
                    )
                elif recovery:
                    message = (
                        "Confronto completato. Le nuove offerte sono state importate senza avviare l’AI; "
                        "le offerte assenti dai Salvati sono state nascoste conservando lettere e analisi."
                    )
                else:
                    message = "Controllo incrementale completato. Nessuna candidatura è stata inviata."
                state: dict[str, object] = {
                    "running": False,
                    "status": status,
                    "message": message,
                    "counts": counts,
                    "request_id": request_id,
                }
            except LinkedInReadError as error:
                log_exception(
                    "linkedin_sync", "sync.failed",
                    "LinkedIn saved-job reading stopped; the previous local snapshot was kept.",
                    error=error,
                )
                state = {
                    "running": False,
                    "status": ("partial" if recovery and not isinstance(error, ManualLinkedInActionRequired)
                               else "error"),
                    "message": f"Lettura LinkedIn incompleta: {error} Nessun dato locale è stato modificato.",
                    "request_id": request_id,
                }
                status = str(state["status"])
                message = str(state["message"])
            except PlaywrightError as error:
                log_exception("linkedin_sync", "sync.pagination_failed",
                              "LinkedIn browser reading failed; local jobs were kept.", error=error)
                status = "partial" if recovery else "error"
                message = "Lettura LinkedIn incompleta: navigazione non verificata. Nessun dato locale è stato modificato."
                state = {"running": False, "status": status, "message": message,
                         "request_id": request_id}
            except (JobStoreError, OSError) as error:
                log_exception(
                    "linkedin_sync",
                    "sync.failed",
                    "LinkedIn saved-job refresh failed; the previous local snapshot was kept.",
                    error=error,
                )
                state = {
                    "running": False,
                    "status": "error",
                    "message": "Aggiornamento non riuscito. Consulta Diagnostica e log usando il riferimento indicato.",
                    "request_id": request_id,
                }
                status = "error"
                message = str(state["message"])
            except Exception as error:
                log_exception(
                    "linkedin_sync",
                    "sync.unexpected_error",
                    "Unexpected error during LinkedIn saved-job refresh.",
                    error=error,
                )
                state = {
                    "running": False,
                    "status": "error",
                    "message": "Aggiornamento non riuscito. Consulta Diagnostica e log usando il riferimento indicato.",
                    "request_id": request_id,
                }
                status = "error"
                message = str(state["message"])
            if recovery:
                ended_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                report = {
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "duration_ms": max(0, int((time.perf_counter() - started_perf) * 1000)),
                    "status": status,
                    "membership_verified": membership_verified,
                    "counts": counts,
                    "events": counts.get("events", []),
                    "unreadable": unreadable,
                    "skipped_missing_title": skipped_missing_title,
                    "read_outcomes": read_outcomes,
                    "message": message,
                    "request_id": request_id,
                }
                state["report"] = report
                try:
                    self.store.save_saved_sync_report(report)
                except Exception as error:
                    log_exception(
                        "linkedin_sync", "sync.report_write_failed",
                        "The completed Saved-list report could not be persisted.", error=error,
                    )
            with self._guard:
                self._state = state


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        store: JobStore,
        *,
        profile_directory: Path | None = None,
    ) -> None:
        self.store = store
        self.log_directory = store.database_path.parent / "logs"
        self.logging_available = configure_logging(self.log_directory)
        self.user_profile_directory = profile_directory or store.database_path.parent / "user_profile"
        template_directory = PROJECT_ROOT / "templates" / "user_profile"
        self.agent_prompt = AgentPromptStore(
            self.user_profile_directory / "AGENTS.md",
            template_directory / "AGENTS.md.template",
        )
        self.candidate_context = AgentPromptStore(
            self.user_profile_directory / "CANDIDATE_CONTEXT.md",
            template_directory / "CANDIDATE_CONTEXT.md.template",
            label="file Markdown del contesto candidato",
        )
        self.candidate_profile = CandidateProfileStore(store.database_path.parent)
        self.ai_settings = AISettingsStore(self.user_profile_directory / "automation.json")
        self.api_keys = APIKeyStore(store.database_path)
        self.application_data = ApplicationStore(store)
        self.generation_tasks = GenerationStore(store)
        self.generation = GenerationCoordinator(
            store, self.generation_tasks, self.ai_settings, self.api_keys,
            self.agent_prompt, self.candidate_context, self.candidate_profile, self.application_data,
        )
        if not self.generation.local_decisions.settings()["enabled"]:
            # Migrate an opt-out saved through the former settings control.
            self.generation.local_decisions.save_settings({"enabled": True})
        self.sync_coordinator = SyncCoordinator(
            store,
            after_sync=self._after_sync,
            after_recovery=self.generation_tasks.mark_recovered_jobs,
        )
        self._client_log_guard = threading.Lock()
        self._client_log_window_started = time.monotonic()
        self._client_log_count = 0
        self._bulk_plan_guard = threading.Lock()
        self._bulk_plans: dict[str, dict[str, Any]] = {}
        super().__init__(address, DashboardRequestHandler)
        self.generation.start()

    def _after_sync(self) -> None:
        self.generation_tasks.reconcile()
        self.generation.wake()

    def allow_client_log(self) -> bool:
        """Apply a process-wide per-minute cap to browser-reported failures."""
        now = time.monotonic()
        with self._client_log_guard:
            if now - self._client_log_window_started >= 60:
                self._client_log_window_started = now
                self._client_log_count = 0
            if self._client_log_count >= MAX_CLIENT_LOGS_PER_MINUTE:
                return False
            self._client_log_count += 1
            return True

    def save_bulk_plan(self, plan: dict[str, Any]) -> tuple[str, str]:
        """Keep a short-lived, single-use preview plan in process memory."""
        now = time.time()
        with self._bulk_plan_guard:
            self._bulk_plans = {key: item for key, item in self._bulk_plans.items()
                                if item["expires_epoch"] > now}
            token = secrets.token_hex(16)
            expires = datetime.fromtimestamp(now + 600, timezone.utc).isoformat(timespec="seconds")
            self._bulk_plans[token] = {**plan, "expires_epoch": now + 600}
            return token, expires

    def consume_bulk_plan(self, token: str) -> dict[str, Any] | None:
        with self._bulk_plan_guard:
            plan = self._bulk_plans.pop(token, None)
        if plan is None or plan["expires_epoch"] <= time.time():
            return None
        return plan

    def server_close(self) -> None:
        self.generation.stop()
        log_event(
            logging.INFO,
            "dashboard",
            "dashboard.stopped",
            "Local dashboard server stopped.",
        )
        super().server_close()
        close_logging(self.log_directory)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    def handle_one_request(self) -> None:
        self.request_id = new_request_id()
        self._request_started = time.perf_counter()
        self._response_started = False
        self._response_status: int | None = None
        with bind_request_id(self.request_id):
            try:
                super().handle_one_request()
            except Exception as error:
                log_exception(
                    "http",
                    "http.unhandled_exception",
                    "Unhandled dashboard request error.",
                    error=error,
                    context={
                        "route": self._route_label(),
                        "method": getattr(self, "command", "UNKNOWN"),
                    },
                )
                if not self._response_started:
                    try:
                        self._error("Errore interno della dashboard.", 500)
                    except OSError:
                        self.close_connection = True
                else:
                    self.close_connection = True
            finally:
                status = self._response_status or 0
                duration_ms = max(0, int((time.perf_counter() - self._request_started) * 1000))
                level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
                log_event(
                    level,
                    "http",
                    "http.request.completed",
                    "Dashboard request completed.",
                    context={
                        "route": self._route_label(),
                        "method": getattr(self, "command", "UNKNOWN"),
                        "status": status,
                        "duration_ms": duration_ms,
                    },
                )

    def _route_label(self) -> str:
        path = urlsplit(getattr(self, "path", "")).path
        if path in {"/", "/app.js", "/styles.css"}:
            return "dashboard_asset"
        if path in {
            "/api/jobs",
            "/api/jobs/manual",
            "/api/sync",
            "/api/sync/recover",
            "/api/sync/status",
            "/api/settings/agent-instructions",
            "/api/settings/candidate-context",
            "/api/profile/cv",
            "/api/diagnostics/logs",
            "/api/diagnostics/client-error",
            "/api/ai/settings",
            "/api/laya/settings",
            "/api/ai/status",
            "/api/ai/bulk/preview",
            "/api/ai/bulk/confirm",
            "/api/ai/key",
            "/api/ai/key/verify",
            "/api/ai/agents/migration",
            "/api/ai/feedback",
            "/api/profile/context/draft",
            "/api/profile/context/import",
            "/api/knowledge",
        }:
            return path.removeprefix("/api/").replace("/", ".")
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}/regenerate", path):
            return "jobs.regenerate"
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}/local-decisions", path):
            return "jobs.local_decisions"
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}/feedback", path):
            return "jobs.feedback"
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}/knowledge", path):
            return "jobs.knowledge"
        if re.fullmatch(r"/api/ai/bulk/[a-f0-9]{32}(?:/cancel)?", path):
            return "ai.bulk_regeneration"
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}/documents", path):
            return "jobs.documents"
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}/memory", path):
            return "jobs.memory_forget"
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}", path):
            return "jobs.detail"
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}/materials", path):
            return "jobs.materials"
        if re.fullmatch(rf"/api/jobs/{JOB_ID_ROUTE}/letters", path):
            return "jobs.letters"
        if re.fullmatch(r"/api/letters/\d+/file", path):
            return "letters.file"
        if re.fullmatch(r"/api/letters/\d+/open", path):
            return "letters.open"
        return "not_found"

    def send_response(self, code: int, message: str | None = None) -> None:
        super().send_response(code, message)
        self._response_started = True
        self._response_status = int(code)
        request_id = getattr(self, "request_id", None)
        if request_id:
            self.send_header("X-Request-ID", request_id)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        try:
            self._response_status = int(code)
        except (TypeError, ValueError):
            pass

    def log_message(self, format_string: str, *args: object) -> None:
        log_event(
            logging.WARNING,
            "http",
            "http.protocol_message",
            "The local HTTP server reported a protocol message.",
            context={"route": self._route_label()},
        )

    def _send_headers(self, status: int, content_type: str, content_length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        if status >= 500:
            error_info = sys.exc_info()
            if error_info[0] is not None and error_info[1] is not None:
                log_exception(
                    "http",
                    "http.server_error",
                    "Dashboard endpoint returned an internal error.",
                    error=error_info[1],
                    context={"route": self._route_label(), "status": status},
                )
            else:
                log_event(
                    logging.ERROR,
                    "http",
                    "http.server_error",
                    "Dashboard endpoint returned an internal error.",
                    context={"route": self._route_label(), "status": status},
                )
        elif status >= 400:
            log_event(
                logging.WARNING,
                "http",
                "http.client_error",
                "Dashboard endpoint returned a client error.",
                context={"route": self._route_label(), "status": status},
            )
        self._send_json(status, {"error": message})

    def _loopback_host_ok(self) -> bool:
        host_header = self.headers.get("Host", "")
        try:
            parsed = urlsplit(f"http://{host_header}")
            return (
                parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port == self.server.server_port
            )
        except ValueError:
            return False

    def _same_origin_ok(self) -> bool:
        if not self._loopback_host_ok():
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return False
        try:
            parsed = urlsplit(origin)
            return (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port == self.server.server_port
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            return False

    def _read_body(self, max_bytes: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError as error:
            raise DashboardRequestError("Content-Length non valido.") from error
        if length < 0:
            raise DashboardRequestError("Il corpo della richiesta è obbligatorio.")
        if length > max_bytes:
            raise DashboardRequestError("Il file o il contenuto supera il limite consentito.", 413)
        return self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._loopback_host_ok():
            self._error("La dashboard accetta richieste solo da localhost.", 403)
            return
        request = urlsplit(self.path)
        if request.path == "/api/laya/settings":
            try:
                self._send_json(200, self.server.generation.local_decisions.status())
            except AIConfigurationError as error:
                self._error(str(error), error.status)
            return
        if request.path == "/api/laya/cost-estimate":
            try:
                settings = self.server.ai_settings.load()
                estimate = estimate_complete_application_cost(
                    agents=self.server.agent_prompt.agents(),
                    usage_profile=self.server.generation_tasks.usage_profile(days=30),
                    research_enabled=bool(settings["web_research_enabled"]),
                    default_model=str(settings["model"]),
                    laya_enabled=bool(self.server.generation.local_decisions.settings()["enabled"]),
                )
                estimate["local_inference"] = self.server.generation.local_decisions.telemetry_summary()
                self._send_json(200, estimate)
            except Exception as error:
                log_event(logging.WARNING, "dashboard", "laya.cost_estimate_unavailable",
                          "Complete-application cost estimate is unavailable.",
                          context={"error_code": "cost_estimate_unavailable"})
                self._error("Stima dei costi non disponibile.", 500)
            return
        if request.path == "/api/ai/usage-profile":
            params = parse_qs(request.query)
            try:
                days = int(params.get("days", ["30"])[0])
                if not 1 <= days <= 365:
                    raise ValueError()
                rows = self.server.generation_tasks.usage_profile(days=days)
            except (ValueError, sqlite3.Error):
                self._error("Intervallo tempi non valido.")
                return
            self._send_json(200, {"days": days, "stages": rows})
            return
        if request.path == "/api/jobs":
            params = parse_qs(request.query)
            query = params.get("q", [""])[0]
            status = params.get("status", [""])[0] or None
            try:
                jobs = self.server.store.get_dashboard_jobs(query=query, status=status)
            except JobStoreError as error:
                self._error(str(error))
                return
            except sqlite3.Error as error:
                log_exception(
                    "database", "jobs.dashboard_read_failed", "Could not read jobs for the dashboard.", error=error
                )
                self._error("Database locale non disponibile.", 500)
                return
            states = self.server.generation_tasks.all_states()
            for job in jobs:
                internal_job_id = str(job["job_id"])
                job["generation"] = states.get(internal_job_id)
                if job["source"] == "manual":
                    job["linkedin_job_id"] = None
                    if job["generation"] is not None:
                        job["generation"] = dict(job["generation"])
                        job["generation"]["job_id"] = internal_job_id
                        job["generation"]["linkedin_job_id"] = None
            self._send_json(200, {"count": len(jobs), "jobs": jobs})
            return

        if request.path == "/api/ai/settings":
            try:
                settings = self.server.ai_settings.load()
                status = self.server.generation.status()
                migration_required = self.server.ai_settings.migration_pending_on_disk()
                legacy_agents = []
                legacy_error = ""
                if migration_required:
                    try:
                        legacy_agents = self.server.ai_settings.legacy_agents()
                    except AIConfigurationError as error:
                        legacy_error = str(error)
                try:
                    markdown_agents = self.server.agent_prompt.agents()
                    markdown_valid = True
                except LocalProfileError:
                    markdown_agents = []
                    markdown_valid = False
            except AIConfigurationError as error:
                self._error(str(error), error.status)
                return
            self._send_json(200, {"settings": settings, "models": list(MODELS),
                                  "key_configured": bool(self.server.api_keys.get()),
                                  "key_source": self.server.api_keys.source(),
                                  "agents": markdown_agents,
                                  "agent_migration": {"required": migration_required,
                                      "legacy_agents": legacy_agents,
                                      "legacy_error": legacy_error,
                                      "markdown_valid": markdown_valid,
                                      "markdown_agents": markdown_agents,
                                      "conflict": migration_required and [
                                          {key: agent.get(key) for key in ("id", "name", "phase", "enabled", "instructions", "model")}
                                          for agent in legacy_agents
                                      ] != [
                                          {key: agent.get(key) for key in ("id", "name", "phase", "enabled", "instructions", "model")}
                                          for agent in markdown_agents
                                      ]},
                                  "learning": self.server.application_data.learning_history(),
                                  "status": status})
            return

        if request.path == "/api/ai/status":
            try:
                status = self.server.generation.status()
            except AIConfigurationError as error:
                self._error(str(error), error.status)
                return
            self._send_json(200, status)
            return

        bulk_status_match = re.fullmatch(r"/api/ai/bulk/([a-f0-9]{32})", request.path)
        if bulk_status_match:
            status = self.server.generation_tasks.bulk_regeneration_status(bulk_status_match.group(1))
            if status is None:
                self._error("Operazione di rigenerazione non trovata.", 404)
            else:
                self._send_json(200, status)
            return

        if request.path == "/api/sync/status":
            self._send_json(200, self.server.sync_coordinator.snapshot())
            return

        if request.path == "/api/diagnostics/logs":
            params = parse_qs(request.query)
            try:
                limit = int(params.get("limit", ["100"])[0])
                events = read_recent_events(
                    self.server.log_directory,
                    limit=limit,
                    level=params.get("level", [""])[0] or None,
                    component=params.get("component", [""])[0] or None,
                    query=params.get("q", [""])[0],
                    since=params.get("since", [""])[0] or None,
                )
            except (ValueError, LogQueryError) as error:
                self._error(str(error))
                return
            except OSError as error:
                log_exception(
                    "observability", "logs.dashboard_read_failed", "Could not read recent local dashboard logs.", error=error
                )
                self._error("Impossibile leggere i log locali.", 500)
                return
            self._send_json(
                200,
                {
                    "events": events,
                    "count": len(events),
                    "limit": limit,
                    "since": params.get("since", [""])[0] or None,
                    "path": str(self.server.log_directory),
                    "logging_available": self.server.logging_available,
                },
            )
            return

        if request.path == "/api/settings/agent-instructions":
            try:
                instructions = self.server.agent_prompt.load()
                agents = self.server.agent_prompt.agents()
            except LocalProfileError as error:
                self._error(str(error), error.status)
            else:
                self._send_json(200, {"instructions": instructions, "agents": agents,
                    "path": "data/user_profile/AGENTS.md",
                    "learning": self.server.application_data.learning_history()})
            return

        if request.path == "/api/settings/candidate-context":
            try:
                context = self.server.candidate_context.load()
            except LocalProfileError as error:
                self._error(str(error), error.status)
            else:
                self._send_json(200, {"context": context, "path": "data/user_profile/CANDIDATE_CONTEXT.md"})
            return

        if request.path == "/api/profile/cv/text":
            try:
                text = self.server.candidate_profile.read_text()
            except LocalProfileError as error:
                self._error(str(error), error.status)
            else:
                self._send_json(200, {"text": text})
            return

        if request.path == "/api/profile/cv":
            try:
                status = self.server.candidate_profile.status()
            except LocalProfileError as error:
                self._error(str(error), error.status)
            else:
                self._send_json(200, status)
            return

        if request.path == "/api/knowledge":
            self._send_json(200, {"files": self.server.application_data.list_knowledge(),
                                  "max_files": MAX_KNOWLEDGE_FILES})
            return

        knowledge_match = re.fullmatch(r"/api/knowledge/(\d+)", request.path)
        if knowledge_match:
            item = self.server.application_data.knowledge_file(int(knowledge_match.group(1)))
            if item is None:
                self._error("Esempio non trovato.", 404)
            else:
                item.pop("content", None)
                self._send_json(200, item)
            return

        job_match = JOB_PATH_PATTERN.fullmatch(request.path)
        if job_match:
            try:
                bundle = self.server.store.get_job_bundle(job_match.group(1))
            except sqlite3.Error as error:
                log_exception(
                    "database", "jobs.detail_read_failed", "Could not read job detail for the dashboard.", error=error
                )
                self._error("Database locale non disponibile.", 500)
                return
            if bundle is None:
                self._error("Annuncio non trovato.", 404)
            else:
                internal_job_id = job_match.group(1)
                bundle["generation"] = self.server.generation_tasks.get(internal_job_id)
                bundle["job"]["job_id"] = internal_job_id
                if bundle["job"].get("source") == "manual":
                    bundle["job"]["linkedin_job_id"] = None
                    bundle["job"].pop("source_fingerprint", None)
                    bundle["job"].pop("normalized_url", None)
                    if bundle["generation"] is not None:
                        bundle["generation"]["job_id"] = internal_job_id
                        bundle["generation"]["linkedin_job_id"] = None
                    materials = bundle.get("materials")
                    if materials is not None:
                        materials["job_id"] = internal_job_id
                        materials["linkedin_job_id"] = None
                    for letter in bundle.get("cover_letters", []):
                        letter["job_id"] = internal_job_id
                        letter["linkedin_job_id"] = None
                materials = bundle.get("materials") or {}
                if materials.get("cover_letter_text"):
                    self.server.generation_tasks.ensure_current_version(
                        job_match.group(1), str(materials["cover_letter_text"]))
                bundle["research"] = self.server.application_data.get_research(job_match.group(1))
                if bundle["research"]:
                    cached_pages = self.server.application_data.get_research_pages(
                        job_match.group(1), research_page_cache_key(bundle["research"]))
                    bundle["research"]["page_texts"] = cached_pages or []
                bundle["local_decisions"] = self.server.generation.local_decisions.get(job_match.group(1))
                bundle["feedback"] = self.server.application_data.feedback_history(job_match.group(1))
                self._send_json(200, bundle)
            return

        file_match = LETTER_FILE_PATTERN.fullmatch(request.path)
        if file_match:
            try:
                file_info = self.server.store.get_cover_letter_file(int(file_match.group(1)))
            except JobStoreError as error:
                self._error(str(error), 404)
                return
            if file_info is None:
                self._error("File cover letter non trovato.", 404)
                return
            record, file_path = file_info
            try:
                contents = file_path.read_bytes()
            except OSError as error:
                log_exception(
                    "storage", "cover_letter.read_failed", "Could not read the registered cover-letter file.", error=error
                )
                self._error("Impossibile leggere il file cover letter archiviato.", 500)
                return
            file_format = str(record["file_format"])
            media_type = "application/pdf" if file_format == "pdf" else (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            download = parse_qs(request.query).get("download", [""])[0] == "1"
            disposition = "attachment" if download or file_format == "docx" else "inline"
            safe_name = f"cover-letter-v{int(record['version']):02d}.{file_format}"
            self._send_headers(200, media_type, len(contents))
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{quote(safe_name)}",
            )
            self.end_headers()
            self.wfile.write(contents)
            return

        asset_name = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}.get(
            request.path
        )
        if asset_name:
            asset_path = DASHBOARD_ASSETS / asset_name
            content_type = {
                "index.html": "text/html; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8",
                "styles.css": "text/css; charset=utf-8",
            }[asset_name]
            body = asset_path.read_bytes()
            self._send_headers(200, content_type, len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        self._error("Risorsa non trovata.", 404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._same_origin_ok():
            self._error("Richiesta rifiutata: origine locale non valida.", 403)
            return
        request = urlsplit(self.path)
        if request.path == "/api/laya/settings":
            try:
                payload = json.loads(self._read_body(200).decode("utf-8"))
                self._send_json(200, self.server.generation.local_decisions.save_settings(payload))
            except AIConfigurationError as error:
                self._error(str(error), error.status)
            except (ValueError, UnicodeDecodeError):
                self._error("Configurazione Laya non valida.")
            return
        local_match = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/local-decisions", request.path)
        if local_match:
            try:
                job = self.server.store.get_job(local_match.group(1))
                if job is None:
                    raise DashboardRequestError("Annuncio non trovato.", 404)
                engine = self.server.generation.local_decisions
                if not engine.settings()["enabled"]:
                    raise DashboardRequestError("Attiva Laya nelle Impostazioni.", 409)
                status = engine.status()
                if not status["installed"]:
                    raise DashboardRequestError("Runtime Laya non installato. Segui la procedura nel README.", 409)
                if not status["checkpoint_available"]:
                    raise DashboardRequestError("Checkpoint Laya non presente in locale. Caricalo esplicitamente seguendo README.md.", 409)
                engine.start(job, self.server.candidate_profile.read_text(), self.server.candidate_context.load())
                self._send_json(202, {"local_decisions": engine.get(local_match.group(1))})
            except (AIConfigurationError, DashboardRequestError) as error:
                self._error(str(error), error.status)
            except LocalProfileError as error:
                self._error(str(error), 409)
            return
        if request.path == "/api/ai/bulk/preview":
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(4_000).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"scope_days", "only_new"}:
                    raise DashboardRequestError("Specifica l’intervallo della rigenerazione.")
                scope_days = payload["scope_days"]
                if scope_days is not None and type(scope_days) is not int:
                    raise DashboardRequestError("Intervallo non valido.")
                only_new = payload["only_new"]
                if type(only_new) is not bool:
                    raise DashboardRequestError("Specifica se includere solo le nuove cover letter.")
                agents = self.server.agent_prompt.agents()
                settings = self.server.ai_settings.load()
                preview = self.server.generation_tasks.preview_bulk_regeneration(
                    scope_days, only_new=only_new)
                usage_profile = self.server.generation_tasks.usage_profile()
                usage_today = self.server.generation_tasks.summary()["usage"]
                research_jobs = 0
                if settings["web_research_enabled"]:
                    for job_id in preview["job_ids"]:
                        job = self.server.store.get_job(job_id)
                        research = self.server.application_data.get_research(job_id)
                        fresh = False
                        if job and research and research.get("state") == "READY" and research.get("job_hash") == _job_fingerprint(job):
                            try:
                                updated = datetime.fromisoformat(str(research["updated_at"]))
                                if updated.tzinfo is None:
                                    updated = updated.replace(tzinfo=timezone.utc)
                                fresh = (datetime.now(timezone.utc) - updated).days < 7
                            except (KeyError, ValueError, TypeError):
                                fresh = False
                        if job and not fresh:
                            # The generation worker checks shared company research
                            # before making a web call, so the preview must use the
                            # same rule when estimating research spend.
                            fresh = self.server.application_data.get_company_research(
                                str(job.get("company") or ""), mark_used=False
                            ) is not None
                        research_jobs += int(not fresh)
                estimate = estimate_bulk_cost(
                    count=preview["eligible_count"], agents=agents,
                    usage_profile=usage_profile,
                    research_enabled=bool(settings["web_research_enabled"]),
                    research_jobs_count=research_jobs,
                    default_model=str(settings["model"]),
                )
                token = expires_at = None
                if preview["eligible_count"]:
                    token, expires_at = self.server.save_bulk_plan({
                        "job_ids": preview["job_ids"], "scope_days": scope_days,
                        "max_age_days": None, "only_new": only_new,
                    })
                ready_missing = self.server.generation.readiness(require_auto=False)
                self._send_json(200, {
                    "preview": {key: value for key, value in preview.items() if key != "job_ids"},
                    "estimate": estimate, "plan_token": token, "expires_at": expires_at,
                    "ready_for_ai": not bool(ready_missing), "missing": ready_missing,
                    "daily_limits": {
                        "total": settings["max_daily_calls"],
                        "total_used": int(usage_today.get("calls_today", 0)),
                        "agents": settings["max_daily_agent_calls"],
                        "agents_used": int(usage_today.get("today_by_operation", {}).get("agent", 0)),
                    },
                })
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except (AIConfigurationError, LocalProfileError, JobStoreError) as error:
                self._error(str(error), getattr(error, "status", 400))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
            return

        if request.path == "/api/ai/bulk/confirm":
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(4_000).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"plan_token"}:
                    raise DashboardRequestError("Conferma un piano valido.")
                token = payload["plan_token"]
                if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
                    raise DashboardRequestError("Piano di rigenerazione non valido o scaduto.", 409)
                plan = self.server.consume_bulk_plan(token)
                if plan is None:
                    raise DashboardRequestError("Piano scaduto o già utilizzato. Calcola una nuova stima.", 409)
                missing = self.server.generation.readiness(require_auto=False)
                if missing:
                    raise DashboardRequestError("Completa la configurazione AI prima della conferma.", 409)
                settings = self.server.ai_settings.load()
                batch = self.server.generation_tasks.queue_bulk_regeneration(
                    token, plan["job_ids"], plan["scope_days"],
                    plan.get("max_age_days"), plan.get("only_new", False))
                self.server.generation.wake()
                log_event(logging.INFO, "generation", "generation.bulk_queued",
                          "A confirmed bulk regeneration batch was queued.",
                          context={"count": batch["count"], "scope_days": batch["scope_days"]})
                self._send_json(202, batch)
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except (AIConfigurationError, LocalProfileError, JobStoreError) as error:
                self._error(str(error), getattr(error, "status", 400))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
            return

        bulk_cancel_match = re.fullmatch(r"/api/ai/bulk/([a-f0-9]{32})/cancel", request.path)
        if bulk_cancel_match:
            batch = self.server.generation_tasks.cancel_bulk_regeneration(bulk_cancel_match.group(1))
            if batch is None:
                self._error("Operazione di rigenerazione non trovata.", 404)
            else:
                self.server.generation.wake()
                self._send_json(200, batch)
            return

        if request.path == "/api/ai/agents/migration":
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(4_000).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"choice"}:
                    raise DashboardRequestError("Specifica la scelta di migrazione.")
                backups = self.server.ai_settings.finish_agent_migration(
                    payload["choice"], self.server.agent_prompt)
                self.server.generation_tasks.mark_stale()
                self.server.generation.wake()
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except (AIConfigurationError, LocalProfileError) as error:
                self._error(str(error), error.status)
                return
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
                return
            self._send_json(200, {"migrated": True, "backups": backups})
            return
        if request.path == "/api/diagnostics/client-error":
            if not self.server.allow_client_log():
                self._error("Troppi eventi diagnostici dal browser; riprova tra un minuto.", 429)
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(MAX_CLIENT_LOG_BYTES).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise DashboardRequestError("L’evento diagnostico deve essere un oggetto JSON.")
                allowed = {
                    "kind", "message", "error_type", "status", "source", "line",
                    "column", "stack", "request_id",
                }
                if payload.keys() - allowed:
                    raise DashboardRequestError("L’evento diagnostico contiene campi non riconosciuti.")
                kind = payload.get("kind")
                message = payload.get("message")
                if kind not in CLIENT_LOG_KINDS or not isinstance(message, str) or not message.strip():
                    raise DashboardRequestError("Tipo o messaggio dell’evento diagnostico non valido.")
                if len(message) > 1_000:
                    raise DashboardRequestError("Il messaggio diagnostico supera il limite consentito.")
                source = payload.get("source", "")
                stack = payload.get("stack", "")
                if not isinstance(source, str) or source not in {"/", "/app.js", "/styles.css", ""}:
                    source = ""
                if not isinstance(stack, str) or len(stack) > 5_000:
                    stack = ""
                stack = _safe_client_stack(stack)
                error_type = payload.get("error_type", "Error")
                if not isinstance(error_type, str) or error_type not in CLIENT_ERROR_TYPES:
                    error_type = "Error"
                status = payload.get("status", 0)
                if type(status) is not int or not 100 <= status <= 599:
                    status = 0
                details: dict[str, object] = {
                    "kind": kind,
                    "source": source,
                    "client_message": _safe_client_message(kind, error_type, status),
                    "client_stack": stack,
                }
                for key in ("line", "column"):
                    value = payload.get(key)
                    if type(value) is int and 0 <= value <= 10_000_000:
                        details[key] = value
                client_request_id = payload.get("request_id")
                context: dict[str, object] = {"event_type": kind}
                if isinstance(client_request_id, str) and CLIENT_REQUEST_ID_PATTERN.fullmatch(client_request_id):
                    context["request_id"] = client_request_id
                    details["client_request_id"] = client_request_id
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self._error(f"JSON non valido: {error}")
                return
            log_event(
                logging.ERROR,
                "browser",
                str(kind),
                "Client-side dashboard failure reported.",
                context=context,
                details=details,
            )
            self._send_json(202, {"accepted": True})
            return

        if request.path == "/api/sync/recover":
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(4_000).decode("utf-8"))
                if not isinstance(payload, dict) or payload:
                    raise DashboardRequestError("La richiesta di scansione deve essere un oggetto JSON vuoto.")
                state = self.server.sync_coordinator.start(recovery=True)
                if state.get("status") == "error":
                    self._error(str(state.get("message") or "Lettura non disponibile."), 409)
                else:
                    self._send_json(202 if state.get("running") else 200, state)
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
            return

        if request.path == "/api/jobs/manual":
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(250_000).decode("utf-8"))
                allowed = {"description", "title", "company", "location"}
                if not isinstance(payload, dict) or payload.keys() - allowed:
                    raise DashboardRequestError(
                        "L’offerta manuale può contenere testo, titolo, azienda e sede."
                    )
                job, created = self.server.store.create_manual_job(
                    payload.get("description"),
                    title=payload.get("title", ""),
                    company=payload.get("company", ""),
                    location=payload.get("location", ""),
                )
                job["job_id"] = str(job["linkedin_job_id"])
                job["linkedin_job_id"] = None
                job.pop("source_fingerprint", None)
                job.pop("normalized_url", None)
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except JobStoreError as error:
                self._error(str(error), 400)
                return
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
                return
            except sqlite3.Error:
                self._error("Impossibile salvare l’offerta nel database locale.", 500)
                return
            self._send_json(201 if created else 200, {"created": created, "job": job})
            return

        application_status_match = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/application-status", request.path)
        if application_status_match:
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(2_000).decode("utf-8"))
                if payload != {"status": "APPLIED"}:
                    raise DashboardRequestError("È possibile registrare manualmente solo lo stato inviata.")
                job = self.server.store.mark_application_status(application_status_match.group(1), "APPLIED")
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
                return
            except JobStoreError as error:
                self._error(str(error), 404 if str(error).startswith("No saved job") else 400)
                return
            self._send_json(200, {"job": job})
            return

        if request.path == "/api/sync":
            state = self.server.sync_coordinator.start()
            if state.get("status") == "error":
                self._error(str(state.get("message") or "Lettura non disponibile."), 409)
                return
            self._send_json(202 if state.get("running") else 200, state)
            return

        if request.path == "/api/ai/settings":
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(MAX_JSON_BYTES).decode("utf-8"))
                previous = self.server.ai_settings.load()
                settings = self.server.ai_settings.save(payload)
                if (previous["model"] != settings["model"] or
                        previous["web_research_enabled"] != settings["web_research_enabled"]):
                    self.server.generation_tasks.mark_stale()
                self.server.generation.wake()
            except (DashboardRequestError, AIConfigurationError) as error:
                self._error(str(error), error.status)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
            else:
                self._send_json(200, {"settings": settings, "status": self.server.generation.status()})
            return

        if request.path == "/api/ai/key":
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(2_000).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"api_key"}:
                    raise DashboardRequestError("Specifica solo api_key.")
                self.server.api_keys.save(payload["api_key"])
                self.server.generation.wake()
            except (DashboardRequestError, AIConfigurationError) as error:
                self._error(str(error), error.status)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
            else:
                self._send_json(200, {"key_configured": True, "key_source": self.server.api_keys.source()})
            return

        if request.path == "/api/ai/key/verify":
            try:
                api_key = self.server.api_keys.get()
                if not api_key:
                    raise DashboardRequestError("Configura prima la chiave API.", 409)
                provider = OpenAIProvider(api_key, self.server.ai_settings.load()["model"])
                provider.verify()
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except AIConfigurationError as error:
                self._error(str(error), error.status)
            except AIProviderError as error:
                log_event(logging.WARNING, "generation", "provider.verify_failed",
                          "AI provider verification failed.", context={"error_code": error.code})
                self._error(str(error), 502)
            else:
                self._send_json(200, {"verified": True})
            return

        if request.path == "/api/profile/context/draft":
            reservation = None
            provider = None
            try:
                settings = self.server.ai_settings.load()
                if not settings["consent"]:
                    raise DashboardRequestError("Accetta prima l'invio dei dati ai servizi AI.", 409)
                api_key = self.server.api_keys.get()
                if not api_key:
                    raise DashboardRequestError("Configura prima la chiave API.", 409)
                provider = OpenAIProvider(api_key, settings["model"])
                reservation = self.server.generation_tasks.reserve_api_call(
                    None, settings["model"], "candidate_context", daily_limit=settings["max_daily_calls"],
                    category_limit=settings["max_daily_revision_calls"], stage="candidate_context")
                if reservation is None:
                    raise DashboardRequestError("Limite giornaliero di chiamate AI raggiunto.", 429)
                current_context = self.server.candidate_context.load()
                result = provider.draft_candidate_context(
                    cv_text=self.server.candidate_profile.read_text(), current_context=current_context)
                old_notes = current_context.strip()
                if old_notes and old_notes not in result["context_markdown"]:
                    merged = (result["context_markdown"].rstrip()
                              + "\n\n## Note precedenti conservate per revisione\n\n"
                              + old_notes + "\n")
                    if len(merged) > MAX_AGENT_MARKDOWN_CHARS:
                        raise AIProviderError(
                            "La bozza e le note precedenti superano il limite. Riduci il contesto e riprova.",
                            "input_too_large")
                    result["context_markdown"] = merged
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except (AIConfigurationError, LocalProfileError) as error:
                self._error(str(error), error.status)
                return
            except AIProviderError as error:
                self._error(str(error), 502)
                return
            finally:
                if reservation is not None:
                    usage = dict(provider.last_usage) if provider else {}
                    if provider:
                        usage.setdefault("duration_ms", int(getattr(provider, "last_duration_ms", 0)))
                    self.server.generation_tasks.finish_api_call(
                        reservation, usage)
            self._send_json(200, result)
            return

        if request.path == "/api/profile/context/import":
            try:
                body = self._read_body(MAX_CV_UPLOAD_BYTES + 64_000)
                filename, file_format, contents = _extract_upload(
                    self.headers.get("Content-Type", ""), body,
                    allowed_formats=SUPPORTED_CV_FORMATS, max_bytes=MAX_CV_UPLOAD_BYTES,
                    purpose="contesto candidato")
                imported = extract_cv_text(file_format, contents)
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except LocalProfileError as error:
                self._error(str(error), error.status)
                return
            self._send_json(200, {"filename": Path(filename).name, "context": imported})
            return

        if request.path == "/api/knowledge":
            try:
                body = self._read_body(MAX_KNOWLEDGE_FILE_BYTES + 64_000)
                filename, file_format, contents = _extract_upload(
                    self.headers.get("Content-Type", ""), body,
                    allowed_formats={"pdf", "docx", "txt", "md"},
                    max_bytes=MAX_KNOWLEDGE_FILE_BYTES, purpose="Cover Letter knowledge")
                if file_format in {"pdf", "docx"}:
                    _validate_file_contents(file_format, contents)
                extracted = extract_cv_text(file_format, contents)
                if len(extracted) > MAX_KNOWLEDGE_TEXT_CHARS:
                    raise DashboardRequestError("Il testo estratto supera 30.000 caratteri.", 413)
                saved = self.server.application_data.add_knowledge(filename, file_format, contents, extracted)
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except (LocalProfileError, JobStoreError) as error:
                self._error(str(error), 400)
                return
            except sqlite3.Error:
                self._error("Impossibile salvare l'esempio nel database locale.", 500)
                return
            self._send_json(201, {"file": saved, "files": self.server.application_data.list_knowledge()})
            return

        knowledge_replace = re.fullmatch(r"/api/knowledge/(\d+)/replace", request.path)
        if knowledge_replace:
            try:
                body = self._read_body(MAX_KNOWLEDGE_FILE_BYTES + 64_000)
                filename, file_format, contents = _extract_upload(
                    self.headers.get("Content-Type", ""), body,
                    allowed_formats={"pdf", "docx", "txt", "md"},
                    max_bytes=MAX_KNOWLEDGE_FILE_BYTES, purpose="Cover Letter knowledge")
                if file_format in {"pdf", "docx"}:
                    _validate_file_contents(file_format, contents)
                extracted = extract_cv_text(file_format, contents)
                if len(extracted) > MAX_KNOWLEDGE_TEXT_CHARS:
                    raise DashboardRequestError("Il testo estratto supera 30.000 caratteri.", 413)
                saved = self.server.application_data.replace_knowledge(
                    int(knowledge_replace.group(1)), filename, file_format, contents, extracted)
                if saved is None:
                    raise DashboardRequestError("Esempio non trovato.", 404)
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except (LocalProfileError, JobStoreError) as error:
                self._error(str(error), 400)
                return
            except sqlite3.Error:
                self._error("Impossibile sostituire l'esempio locale.", 500)
                return
            self._send_json(200, {"file": saved, "files": self.server.application_data.list_knowledge()})
            return

        research_match = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/research", request.path)
        if research_match:
            job_id = research_match.group(1)
            reservation = None
            provider = None
            try:
                settings = self.server.ai_settings.load()
                if not settings["consent"] or not settings["web_research_enabled"]:
                    raise DashboardRequestError("Attiva consenso AI e ricerca web nelle Impostazioni.", 409)
                api_key = self.server.api_keys.get()
                job = self.server.store.get_job(job_id)
                if job is None:
                    raise DashboardRequestError("Annuncio non trovato.", 404)
                if not api_key:
                    raise DashboardRequestError("Configura prima la chiave API.", 409)
                provider = OpenAIProvider(api_key, settings["model"])
                reservation = self.server.generation_tasks.reserve_api_call(
                    job_id, WEB_SEARCH_MODEL, "company_research", daily_limit=settings["max_daily_calls"],
                    category_limit=settings["max_daily_research_calls"], stage="company_research")
                if reservation is None:
                    raise DashboardRequestError("Limite giornaliero di chiamate AI raggiunto.", 429)
                result = provider.research_company(job=job, global_instructions=self.server.agent_prompt.general_instructions())
                digest = _job_fingerprint(job)
                saved = self.server.application_data.save_research(
                    job_id, state="READY", summary=result["summary"], sources=result["sources"],
                    facts=result.get("facts", []), gaps=result.get("gaps", ""),
                    search_calls=result.get("search_calls", 0), job_hash=digest)
                company_profile_saved = self.server.application_data.save_company_research(
                    str(job.get("company") or ""), saved)
                if company_profile_saved:
                    saved = self.server.application_data.save_research(
                        job_id, state="READY", summary=saved["summary"],
                        sources=saved.get("sources", []), facts=saved.get("facts", []),
                        gaps=saved.get("gaps", ""), search_calls=saved.get("search_calls", 0),
                        job_hash=digest, cache_scope="company",
                        researched_at=saved.get("researched_at", ""))
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except (AIConfigurationError, LocalProfileError) as error:
                self._error(str(error), error.status)
                return
            except AIProviderError as error:
                if job_id and provider is not None:
                    try:
                        self.server.application_data.save_research(
                            job_id, state="UNAVAILABLE", error_code=error.code)
                    except Exception:
                        pass
                self._error(str(error), 502)
                return
            finally:
                if reservation is not None:
                    usage = dict(provider.last_usage) if provider else {}
                    if provider:
                        usage.setdefault("duration_ms", int(getattr(provider, "last_duration_ms", 0)))
                    self.server.generation_tasks.finish_api_call(
                        reservation, usage)
            self._send_json(200, {"research": saved, "company_profile_saved": company_profile_saved})
            return

        feedback_match = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/feedback", request.path)
        if feedback_match:
            job_id = feedback_match.group(1)
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(8_000).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"rating", "comment"}:
                    raise DashboardRequestError("Specifica voto e commento.")
                rating, comment = payload["rating"], payload["comment"]
                if type(rating) is not int or not 1 <= rating <= 10 or not isinstance(comment, str) or len(comment) > 240:
                    raise DashboardRequestError("Il voto deve essere da 1 a 10 e il commento non oltre 240 caratteri.")
                bundle = self.server.store.get_job_bundle(job_id)
                if bundle is None:
                    raise DashboardRequestError("Annuncio non trovato.", 404)
                materials = bundle.get("materials") or {}
                letter = str(materials.get("cover_letter_text") or "").strip()
                if not letter:
                    raise DashboardRequestError("Crea prima la cover letter.", 409)
                self.server.generation_tasks.ensure_current_version(job_id, letter)
                versions = self.server.generation_tasks.letter_versions(job_id)
                active = next((item for item in versions if item["active"]), None)
                if active is None:
                    raise DashboardRequestError("Non riesco a identificare la versione attiva.", 409)
                self.server.application_data.add_feedback(job_id, int(active["version"]), rating, comment)
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except JobStoreError as error:
                self._error(str(error), getattr(error, "status", 400))
                return
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
                return
            self._send_json(200, {"version": int(active["version"]),
                "feedback": self.server.application_data.feedback_history(job_id)})
            return

        knowledge_match = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/knowledge", request.path)
        if knowledge_match:
            job_id = knowledge_match.group(1)
            try:
                bundle = self.server.store.get_job_bundle(job_id)
                if bundle is None:
                    raise DashboardRequestError("Annuncio non trovato.", 404)
                materials = bundle.get("materials") or {}
                letter = str(materials.get("cover_letter_text") or "").strip()
                if not letter:
                    raise DashboardRequestError("Crea prima la cover letter.", 409)
                if len(letter) > MAX_KNOWLEDGE_TEXT_CHARS:
                    raise DashboardRequestError("La lettera supera il limite di Cover Letter knowledge.", 413)
                normalized = " ".join(letter.split())
                if any(" ".join(item["text"].split()) == normalized
                       for item in self.server.application_data.knowledge_text()):
                    raise DashboardRequestError("Questa cover letter è già presente nella knowledge.", 409)
                saved = self.server.application_data.add_knowledge(
                    f"annuncio-{job_id}-cover-letter.txt", "txt", letter.encode("utf-8"), letter)
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except JobStoreError as error:
                self._error(str(error), 409)
                return
            self._send_json(201, {"file": saved,
                "files": self.server.application_data.list_knowledge(),
                "max_files": MAX_KNOWLEDGE_FILES})
            return

        regenerate = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/regenerate", request.path)
        if regenerate:
            try:
                task = self.server.generation.request_one(regenerate.group(1))
            except JobStoreError as error:
                status = 404 if str(error).startswith("No saved job") else 409
                self._error(str(error), status)
            except (AIConfigurationError, LocalProfileError) as error:
                self._error(str(error), error.status)
            else:
                self._send_json(202, {"generation": task})
            return

        documents = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/documents", request.path)
        if documents:
            try:
                job_id = documents.group(1)
                bundle = self.server.store.get_job_bundle(job_id)
                if bundle is None:
                    raise DashboardRequestError("Annuncio non trovato.", 404)
                letter_text = str((bundle["materials"] or {}).get("cover_letter_text") or "")
                if not letter_text:
                    raise DashboardRequestError("Salva prima il testo della cover letter.", 409)
                payload = json.loads(self._read_body(MAX_JSON_BYTES).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"format"} or payload["format"] not in {"pdf", "docx"}:
                    raise DashboardRequestError("Scegli il formato PDF o Word.", 400)
                file_format = payload["format"]
                export_started = time.perf_counter()
                content = render_letter_document(letter_text, file_format)
                saved_file = self.server.store.save_cover_letter_file(
                    job_id,
                    original_filename=f"cover-letter.{file_format}",
                    file_format=file_format,
                    contents=content,
                )
                log_event(logging.INFO, "generation", "generation.stage_timing", "Requested document exported from current saved text.",
                          context={"generation_id": new_request_id(), "stage": "export_"+file_format, "duration_ms": round((time.perf_counter()-export_started)*1000), "outcome": "ready", "file_format": file_format})
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error("Richiesta di esportazione non valida.", 400)
            except JobStoreError as error:
                self._error(str(error), 409)
            except Exception:
                log_event(logging.ERROR, "storage", "cover_letter.render_failed",
                          "Could not render the saved local letter documents.")
                self._error("Impossibile creare i documenti dalla lettera salvata.", 500)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf" if file_format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                safe_name = Path(str(saved_file["relative_path"])).name
                self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            return

        if request.path == "/api/settings/agent-instructions":
            try:
                if self.server.ai_settings.migration_pending_on_disk():
                    raise DashboardRequestError("Completa prima la scelta di migrazione degli agenti.", 409)
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(MAX_AGENT_MARKDOWN_BYTES).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"instructions"}:
                    raise DashboardRequestError("Il JSON deve contenere solo il campo instructions.")
                instructions = payload["instructions"]
                if isinstance(instructions, str) and len(instructions) > MAX_AGENT_MARKDOWN_CHARS:
                    raise DashboardRequestError("Il file Markdown supera il limite di 100.000 caratteri.", 413)
                from linkedin_connector.profile import parse_agent_markdown
                parse_agent_markdown(instructions)
                saved = self.server.agent_prompt.save(instructions)
                self.server.generation_tasks.mark_stale()
                self.server.generation.wake()
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except LocalProfileError as error:
                self._error(str(error), error.status)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self._error(f"JSON non valido: {error}")
            else:
                self._send_json(200, {"instructions": saved, "agents": self.server.agent_prompt.agents(),
                                      "path": "data/user_profile/AGENTS.md"})
            return

        if request.path == "/api/settings/agents":
            try:
                if self.server.ai_settings.migration_pending_on_disk():
                    raise DashboardRequestError("Completa prima la scelta di migrazione degli agenti.", 409)
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(MAX_AGENT_MARKDOWN_BYTES).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"agents"}:
                    raise DashboardRequestError("Specifica solo le sezioni agente.")
                saved = self.server.agent_prompt.replace_agents(payload["agents"])
                self.server.generation_tasks.mark_stale()
                self.server.generation.wake()
            except DashboardRequestError as error:
                self._error(str(error), error.status)
                return
            except LocalProfileError as error:
                self._error(str(error), error.status)
                return
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error("JSON non valido.")
                return
            self._send_json(200, {"instructions": saved, "agents": self.server.agent_prompt.agents()})
            return

        if request.path == "/api/settings/candidate-context":
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(MAX_AGENT_MARKDOWN_BYTES).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"context"}:
                    raise DashboardRequestError("Il JSON deve contenere solo il campo context.")
                context = payload["context"]
                if isinstance(context, str) and len(context) > MAX_AGENT_MARKDOWN_CHARS:
                    raise DashboardRequestError("Il file del contesto supera il limite di 100.000 caratteri.", 413)
                saved = self.server.candidate_context.save(context)
                self.server.generation_tasks.mark_stale()
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except LocalProfileError as error:
                self._error(str(error), error.status)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self._error(f"JSON non valido: {error}")
            else:
                self._send_json(200, {"context": saved, "path": "data/user_profile/CANDIDATE_CONTEXT.md"})
            return

        if request.path == "/api/profile/cv":
            try:
                body = self._read_body(MAX_CV_UPLOAD_BYTES + 64_000)
                filename, file_format, contents = _extract_upload(
                    self.headers.get("Content-Type", ""),
                    body,
                    allowed_formats=SUPPORTED_CV_FORMATS,
                    max_bytes=MAX_CV_UPLOAD_BYTES,
                    purpose="CV",
                )
                saved = self.server.candidate_profile.save(filename, file_format, contents)
                self.server.generation_tasks.mark_stale()
                self.server.generation.wake()
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except LocalProfileError as error:
                self._error(str(error), error.status)
            else:
                self._send_json(201, {"cv": saved})
            return

        open_match = re.fullmatch(r"/api/letters/(\d+)/open", request.path)
        if open_match:
            try:
                file_info = self.server.store.get_cover_letter_file(int(open_match.group(1)))
                if file_info is None:
                    raise DashboardRequestError("File cover letter non trovato.", 404)
                record, file_path = file_info
                if record["file_format"] != "docx":
                    raise DashboardRequestError("Questo comando apre solo documenti Word.")
                opener = getattr(os, "startfile", None)
                if opener is None:
                    raise DashboardRequestError(
                        "L’apertura diretta in Word è disponibile su Windows; scarica il file invece.",
                        501,
                    )
                opener(str(file_path))
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except JobStoreError as error:
                self._error(str(error), 500)
            except (OSError, sqlite3.Error) as error:
                log_exception(
                    "storage", "cover_letter.open_failed", "Could not open the selected local Word document.", error=error
                )
                self._error("Non riesco ad aprire il documento nell’app predefinita.", 500)
            else:
                self._send_json(200, {"opened": True})
            return

        restore_match = re.fullmatch(
            rf"/api/jobs/({JOB_ID_ROUTE})/versions/(\d+)/restore", request.path
        )
        if restore_match:
            job_id, version = restore_match.group(1), int(restore_match.group(2))
            try:
                restored = self.server.generation_tasks.restore_letter_version(job_id, version)
            except JobStoreError as error:
                self._error(str(error), 404)
                return
            except Exception:
                self._error("Impossibile ripristinare questa versione.", 500)
                return
            self._send_json(200, {"restored": version,
                "history": self.server.generation_tasks.letter_versions(job_id)})
            return

        material_match = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/materials", request.path)
        if material_match:
            try:
                if self.headers.get_content_type() != "application/json":
                    raise DashboardRequestError("Il contenuto deve essere JSON.", 415)
                payload = json.loads(self._read_body(MAX_JSON_BYTES).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise DashboardRequestError("Il contenuto JSON deve essere un oggetto.")
                allowed = {
                    "company_analysis",
                    "role_analysis",
                    "personalization_notes",
                    "cover_letter_text",
                    "agent_name",
                }
                if payload.keys() - allowed:
                    raise DashboardRequestError("Il JSON contiene campi non riconosciuti.")
                saved = self.server.store.save_job_materials(
                    material_match.group(1), **payload
                )
                self.server.generation_tasks.mark_manual_edit(material_match.group(1))
                if "cover_letter_text" in payload:
                    self.server.generation_tasks.ensure_current_version(
                        material_match.group(1), str(payload["cover_letter_text"]))
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self._error(f"JSON non valido: {error}")
            except JobStoreError as error:
                status = 404 if str(error).startswith("No saved job") else 400
                self._error(str(error), status)
            except sqlite3.Error as error:
                log_exception(
                    "database", "materials.save_failed", "Could not save application materials.", error=error
                )
                self._error("Impossibile salvare le note nel database locale.", 500)
            else:
                self._send_json(200, {"materials": saved})
            return

        letter_match = re.fullmatch(rf"/api/jobs/({JOB_ID_ROUTE})/letters", request.path)
        if letter_match:
            try:
                content_type = self.headers.get("Content-Type", "")
                body = self._read_body(MAX_UPLOAD_BYTES + 64_000)
                filename, file_format, contents = _extract_upload(content_type, body)
                _validate_file_contents(file_format, contents)
                saved = self.server.store.save_cover_letter_file(
                    letter_match.group(1),
                    original_filename=filename,
                    file_format=file_format,
                    contents=contents,
                )
                self.server.generation_tasks.mark_manual_edit(letter_match.group(1))
            except DashboardRequestError as error:
                self._error(str(error), error.status)
            except JobStoreError as error:
                status = 404 if str(error).startswith("No saved job") else 400
                self._error(str(error), status)
            except (OSError, sqlite3.Error) as error:
                log_exception(
                    "storage", "cover_letter.upload_failed", "Could not archive the uploaded cover letter.", error=error
                )
                self._error("Impossibile archiviare il file nella cartella locale.", 500)
            else:
                self._send_json(201, {"cover_letter": saved})
            return

        self._error("Endpoint non trovato.", 404)

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._same_origin_ok():
            self._error("Richiesta rifiutata: origine locale non valida.", 403)
            return
        if urlsplit(self.path).path == "/api/ai/key":
            try:
                if self.server.api_keys.source() == "ambiente":
                    raise AIConfigurationError(
                        "Rimuovi la variabile d'ambiente della chiave API dal sistema.", 409
                    )
                self.server.api_keys.delete()
                settings = self.server.ai_settings.load()
                settings["enabled"] = False
                self.server.ai_settings.save(settings)
            except AIConfigurationError as error:
                self._error(str(error), error.status)
            else:
                self._send_json(200, {"key_configured": False})
            return
        job_forget = re.fullmatch(
            rf"/api/jobs/({JOB_ID_ROUTE})/memory", urlsplit(self.path).path
        )
        if job_forget:
            try:
                result = self.server.store.forget_job(job_forget.group(1))
            except sqlite3.Error as error:
                log_exception("database", "jobs.memory_forget_failed",
                              "Could not remove a saved job from local memory.", error=error)
                self._error("Database locale non disponibile.", 500)
                return
            if result == "missing":
                self._error("Annuncio non trovato.", 404)
            elif result == "running":
                self._error("Attendi la fine dell’analisi prima di rimuovere l’annuncio.", 409)
            else:
                self.server.generation.wake()
                self._send_json(200, {"deleted": True, "files_retained": True,
                                      "reimport_suppressed": True})
            return
        knowledge_delete = re.fullmatch(r"/api/knowledge/(\d+)", urlsplit(self.path).path)
        if knowledge_delete:
            deleted = self.server.application_data.delete_knowledge(int(knowledge_delete.group(1)))
            if not deleted:
                self._error("Esempio non trovato.", 404)
            else:
                self._send_json(200, {"deleted": True, "files": self.server.application_data.list_knowledge()})
            return
        learning_undo = re.fullmatch(r"/api/agents/learning/(\d+)/undo", urlsplit(self.path).path)
        if learning_undo:
            learning_id = int(learning_undo.group(1))
            learning = self.server.application_data.get_learning(learning_id)
            if learning is None:
                self._error("Regola appresa non trovata.", 404)
                return
            try:
                self.server.agent_prompt.remove_learning_rule(learning["agent_id"], learning["rule"])
                self.server.application_data.delete_learning(learning_id)
                self.server.generation_tasks.mark_stale()
            except LocalProfileError as error:
                self._error(str(error), error.status)
                return
            self._send_json(200, {"removed": True,
                "agents": self.server.agent_prompt.agents(),
                "learning": self.server.application_data.learning_history()})
            return
        if urlsplit(self.path).path != "/api/profile/cv":
            self._error("Endpoint non trovato.", 404)
            return
        try:
            self.server.candidate_profile.delete()
            self.server.generation_tasks.mark_stale()
        except LocalProfileError as error:
            self._error(str(error), error.status)
        else:
            self._send_json(200, {"cv": self.server.candidate_profile.status()})


def _extract_upload(
    content_type: str,
    body: bytes,
    *,
    allowed_formats: set[str] | None = None,
    max_bytes: int = MAX_UPLOAD_BYTES,
    purpose: str = "cover letter",
) -> tuple[str, str, bytes]:
    allowed_formats = allowed_formats or {"pdf", "docx"}
    if not content_type.casefold().startswith("multipart/form-data"):
        raise DashboardRequestError(f"Carica un file valido per {purpose}.", 415)
    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii", "strict")
        + body
    )
    message = BytesParser(policy=email.policy.default).parsebytes(envelope)
    if not message.is_multipart():
        raise DashboardRequestError("Il modulo di caricamento non è valido.")
    uploaded: list[tuple[str, str, bytes]] = []
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") != "file":
            continue
        filename = part.get_filename() or ""
        file_format = Path(filename.replace("\\", "/")).suffix.casefold().lstrip(".")
        contents = part.get_payload(decode=True) or b""
        uploaded.append((filename, file_format, contents))
    if len(uploaded) != 1:
        raise DashboardRequestError(f"Seleziona esattamente un file per {purpose}.")
    filename, file_format, contents = uploaded[0]
    if file_format not in allowed_formats:
        formats = ", ".join(sorted(allowed_formats)).upper()
        raise DashboardRequestError(f"Per {purpose} sono consentiti solo file {formats}.")
    if len(contents) > max_bytes:
        raise DashboardRequestError(f"Il file supera il limite di {max_bytes // (1024 * 1024)} MB.", 413)
    return filename, file_format, contents


def _validate_file_contents(file_format: str, contents: bytes) -> None:
    if file_format == "pdf":
        if not contents[:1024].lstrip().startswith(b"%PDF-"):
            raise DashboardRequestError("Il contenuto non sembra essere un file PDF valido.")
        return
    try:
        with zipfile.ZipFile(BytesIO(contents)) as archive:
            names = archive.namelist()
            if "word/document.xml" not in names or len(names) > 2_000:
                raise DashboardRequestError("Il file DOCX non contiene un documento Word valido.")
            if sum(info.file_size for info in archive.infolist()) > MAX_UPLOAD_BYTES:
                raise DashboardRequestError("Il DOCX supera il limite di dimensione estratta.", 413)
    except zipfile.BadZipFile as error:
        raise DashboardRequestError("Il contenuto non è un file DOCX valido.") from error


def serve_dashboard(
    *, port: int = 8765, host: str = "127.0.0.1", open_browser: bool = False
) -> None:
    """Serve the local dashboard on loopback only."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The dashboard may only bind to the local machine.")
    store = JobStore()
    server = DashboardHTTPServer((host, port), store)
    address_host = "127.0.0.1" if host == "localhost" else host
    url = f"http://{address_host}:{server.server_port}/"
    print(f"Dashboard disponibile in {url}. Premi Ctrl+C per chiuderla.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard chiusa.", flush=True)
    finally:
        server.server_close()
