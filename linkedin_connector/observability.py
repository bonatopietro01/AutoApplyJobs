"""Bounded, local-only structured logging and request tracing."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
MAX_LOG_LINE_BYTES = 20_000
MAX_LOG_READ_BYTES_PER_FILE = 512 * 1024
MAX_LOG_QUERY_LENGTH = 120
MAX_LOG_LIMIT = 200

_REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{16}$")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)([\"']?\b(?:password|passwd|token|secret|authorization|cookie|api[-_ ]?key)\b[\"']?\s*[:=]\s*[\"']?)[^\s,;&\"']+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|token|key|api_key|code)=)[^&\s]+"
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_JOB_URL_PATTERN = re.compile(r"(?i)(/jobs/view/)\d+")
_JOB_ID_TEXT_PATTERN = re.compile(r"(?i)(\b(?:saved job|job id)\s+#?)\d{5,}")
_WINDOWS_PROFILE_PATH_PATTERN = re.compile(r"(?i)([a-z]:\\Users\\)[^\\]+")
_UNIX_PROFILE_PATH_PATTERN = re.compile(r"(?i)(/(?:home|Users)/)[^/]+")

_SAFE_CONTEXT_KEYS = {
    "route",
    "method",
    "status",
    "duration_ms",
    "count",
    "inserted",
    "refreshed",
    "total",
    "discovered",
    "existing_count",
    "file_format",
    "file_size",
    "version",
    "level",
    "component",
    "request_id",
    "event_type",
    "source",
    "line",
    "column",
    "agent_count",
    "error_code",
    "limit_name",
    "limit",
    "resume_at",
    "stage", "outcome", "cache_scope", "research_cache_scope", "api_duration_ms",
    "generation_id", "queue_ms", "cached", "agent_stages", "overlap", "page_count",
    "candidate_index_chunks", "candidate_role_keywords", "preprocessing_ms",
    "candidate_retrieval_ms", "ranking_ms", "inference_ms",
    "worker_startup_ms", "candidate_source_chars", "candidate_writer_chars",
}
_SAFE_DETAIL_KEYS = {
    "client_stack",
    "client_message",
    "source",
    "line",
    "column",
    "kind",
    "client_request_id",
    "exception_type",
    "traceback_frames",
}

_request_id_context: ContextVar[str | None] = ContextVar(
    "linkedin_connector_request_id", default=None
)
_configuration_guard = threading.RLock()
_active_log_directory: Path | None = None
_active_handler: RotatingFileHandler | None = None


class ProjectLogFilter(logging.Filter):
    """Keep this file focused on this project's own diagnostics."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(("linkedin_connector", "mcp_server")) or record.name in {
            "main",
            "__main__",
        }


def redact_text(value: object, *, max_length: int = MAX_LOG_LINE_BYTES) -> str:
    """Remove common credential/PII patterns and bound untrusted strings."""
    text = str(value).replace("\x00", "")
    text = _BEARER_PATTERN.sub("Bearer [redacted]", text)
    text = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1[redacted]", text)
    text = _SECRET_QUERY_PATTERN.sub(r"\1[redacted]", text)
    text = _EMAIL_PATTERN.sub("[email redacted]", text)
    text = _JOB_URL_PATTERN.sub(r"\1[job-id]", text)
    text = _JOB_ID_TEXT_PATTERN.sub(r"\1[job-id]", text)
    text = _WINDOWS_PROFILE_PATH_PATTERN.sub(r"\1[user]", text)
    text = _UNIX_PROFILE_PATH_PATTERN.sub(r"\1[user]", text)
    if len(text) > max_length:
        text = text[:max_length] + "…[truncated]"
    return text


def _safe_scalar(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return redact_text(value, max_length=1_000)
    if isinstance(value, (list, tuple)):
        return [redact_text(item, max_length=200) for item in value[:20]]
    return redact_text(type(value).__name__, max_length=100)


class StructuredJSONFormatter(logging.Formatter):
    """Serialize only explicitly safe logging fields as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        request_id = getattr(record, "request_id", None) or _request_id_context.get()
        if not isinstance(request_id, str) or not _REQUEST_ID_PATTERN.fullmatch(request_id):
            request_id = None

        raw_context = getattr(record, "context", {})
        context = {
            key: _safe_scalar(value)
            for key, value in raw_context.items()
            if key in _SAFE_CONTEXT_KEYS
        } if isinstance(raw_context, dict) else {}
        details = getattr(record, "details", {})
        safe_details = {
            key: (
                redact_text(
                    value,
                    max_length=8_000 if key == "client_stack" else 1_000 if key == "client_message" else 500,
                )
                if isinstance(value, str)
                else _safe_scalar(value)
            )
            for key, value in details.items()
            if key in _SAFE_DETAIL_KEYS and isinstance(value, (str, int, float))
        } if isinstance(details, dict) else {}

        item: dict[str, object] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "component": redact_text(getattr(record, "component", record.name), max_length=100),
            "event": redact_text(getattr(record, "event", "log"), max_length=120),
            "message": redact_text(record.getMessage(), max_length=2_000),
            "request_id": request_id,
            "context": context,
        }
        if safe_details:
            item["details"] = safe_details
        if record.exc_info:
            item["exception"] = redact_text(
                "".join(traceback.format_exception(*record.exc_info)), max_length=12_000
            )
        safe_exception = getattr(record, "safe_exception", None)
        if isinstance(safe_exception, str):
            item["exception"] = redact_text(safe_exception, max_length=12_000)
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def current_request_id() -> str | None:
    """Return the trace identifier bound to the current request/thread."""
    return _request_id_context.get()


def new_request_id() -> str:
    return secrets.token_hex(8)


@contextmanager
def bind_request_id(request_id: str) -> Iterator[None]:
    """Bind an identifier to log records emitted in this execution context."""
    token = _request_id_context.set(request_id)
    try:
        yield
    finally:
        _request_id_context.reset(token)


def log_event(
    level: int,
    component: str,
    event: str,
    message: str,
    *,
    context: dict[str, object] | None = None,
    details: dict[str, object] | None = None,
    exc_info: object = None,
) -> None:
    """Write a redacted event to every handler, with safe metadata for JSON logs."""
    safe_exception = None
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        safe_exception = redact_text(
            "".join(traceback.format_exception(*exc_info)), max_length=12_000
        )
    elif exc_info is True:
        safe_exception = redact_text(traceback.format_exc(), max_length=12_000)
    logging.getLogger("linkedin_connector").log(
        level,
        redact_text(message, max_length=2_000),
        extra={
            "component": component,
            "event": event,
            "context": context or {},
            "details": details or {},
            "safe_exception": safe_exception,
        },
    )


def log_exception(
    component: str,
    event: str,
    message: str,
    *,
    error: BaseException | None = None,
    context: dict[str, object] | None = None,
) -> None:
    """Write a redacted exception and traceback without capturing request bodies."""
    exc_info: object = (
        (type(error), error, error.__traceback__) if error is not None else True
    )
    log_event(
        logging.ERROR,
        component,
        event,
        message,
        context=context,
        exc_info=exc_info,
    )


def configure_logging(
    log_directory: Path | str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> bool:
    """Configure bounded JSONL logging, returning false rather than failing startup."""
    global _active_log_directory, _active_handler
    target = Path(log_directory).expanduser().resolve()
    with _configuration_guard:
        if _active_handler is not None and _active_log_directory == target:
            return True
        try:
            target.mkdir(parents=True, exist_ok=True)
            new_handler = RotatingFileHandler(
                target / "events.jsonl",
                maxBytes=max(1_024, int(max_bytes)),
                backupCount=max(1, min(int(backup_count), 20)),
                encoding="utf-8",
                delay=False,
            )
        except (OSError, ValueError, TypeError):
            return False

        new_handler.setLevel(logging.INFO)
        new_handler.setFormatter(StructuredJSONFormatter())
        new_handler.addFilter(ProjectLogFilter())
        root_logger = logging.getLogger()
        root_logger.setLevel(min(root_logger.level or logging.INFO, logging.INFO))
        old_handler = _active_handler
        if old_handler is not None:
            root_logger.removeHandler(old_handler)
            old_handler.close()
        root_logger.addHandler(new_handler)
        _active_handler = new_handler
        _active_log_directory = target

    log_event(
        logging.INFO,
        "observability",
        "logging.ready",
        "Local structured logging configured.",
        context={"file_size": max(1_024, int(max_bytes)), "count": max(1, min(int(backup_count), 20)) + 1},
    )
    return True


def close_logging(log_directory: Path | str) -> None:
    """Close this project's active file handler when its owning app shuts down."""
    global _active_log_directory, _active_handler
    target = Path(log_directory).expanduser().resolve()
    with _configuration_guard:
        if _active_handler is None or _active_log_directory != target:
            return
        handler = _active_handler
        _active_handler = None
        _active_log_directory = None
        root_logger = logging.getLogger()
        root_logger.removeHandler(handler)
        handler.flush()
        handler.close()


def logging_is_configured(log_directory: Path | str) -> bool:
    target = Path(log_directory).expanduser().resolve()
    with _configuration_guard:
        return _active_handler is not None and _active_log_directory == target


class LogQueryError(ValueError):
    """An invalid diagnostic log filter."""


def _tail_json_records(path: Path, max_bytes: int) -> Iterator[dict[str, object]]:
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            offset = max(0, size - max_bytes)
            stream.seek(offset)
            content = stream.read(max_bytes)
    except FileNotFoundError:
        return

    if offset and b"\n" in content:
        content = content.split(b"\n", 1)[1]
    for line in reversed(content.splitlines()):
        if not line or len(line) > MAX_LOG_LINE_BYTES:
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            yield record


def read_recent_events(
    log_directory: Path | str,
    *,
    limit: int = 100,
    level: str | None = None,
    component: str | None = None,
    query: str = "",
    since: str | None = None,
) -> list[dict[str, object]]:
    """Read a bounded recent slice, optionally limited to a connection timestamp."""
    if not 1 <= limit <= MAX_LOG_LIMIT:
        raise LogQueryError(f"limit deve essere tra 1 e {MAX_LOG_LIMIT}.")
    if level:
        level = level.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise LogQueryError("Il livello di log non è riconosciuto.")
    if component and (len(component) > 80 or not re.fullmatch(r"[a-zA-Z0-9_.-]+", component)):
        raise LogQueryError("Il componente di log non è valido.")
    query = query.strip()
    if len(query) > MAX_LOG_QUERY_LENGTH:
        raise LogQueryError(f"La ricerca non può superare {MAX_LOG_QUERY_LENGTH} caratteri.")
    since_time = None
    if since:
        try:
            since_time = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if since_time.tzinfo is None:
                since_time = since_time.replace(tzinfo=timezone.utc)
            since_time = since_time.astimezone(timezone.utc)
        except (ValueError, TypeError):
            raise LogQueryError("Il limite temporale della connessione non è valido.") from None

    target = Path(log_directory).expanduser().resolve()
    search = query.casefold()
    files = [target / "events.jsonl"] + [
        target / f"events.jsonl.{index}"
        for index in range(1, DEFAULT_BACKUP_COUNT + 1)
    ]
    results: list[dict[str, object]] = []
    for path in files:
        try:
            records = _tail_json_records(path, MAX_LOG_READ_BYTES_PER_FILE)
            for record in records:
                if since_time is not None:
                    try:
                        record_time = datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
                        if record_time.tzinfo is None:
                            record_time = record_time.replace(tzinfo=timezone.utc)
                        if record_time.astimezone(timezone.utc) < since_time:
                            continue
                    except (ValueError, TypeError):
                        continue
                if level and record.get("level") != level:
                    continue
                if component and record.get("component") != component:
                    continue
                if search and search not in json.dumps(record, ensure_ascii=False).casefold():
                    continue
                results.append(record)
                if len(results) >= limit:
                    return results
        except OSError:
            log_exception(
                "observability",
                "logs.read_failed",
                "Could not read a local diagnostic log file.",
            )
            raise
    return results
