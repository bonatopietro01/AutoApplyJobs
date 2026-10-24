"""Tests for bounded local diagnostics logging and trace metadata."""

from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from linkedin_connector.observability import (
    LogQueryError,
    bind_request_id,
    close_logging,
    configure_logging,
    log_event,
    log_exception,
    read_recent_events,
    redact_text,
)


class ObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_directory = Path(self.temp_dir.name) / "data" / "logs"
        self.assertTrue(configure_logging(self.log_directory))

    def tearDown(self) -> None:
        close_logging(self.log_directory)
        self.temp_dir.cleanup()

    def test_stage_timing_metadata_reaches_diagnostics_without_private_inputs(self):
        log_event(logging.INFO, "generation", "generation.stage_timing", "Stage completed.",
                  context={"stage":"writer", "outcome":"ready", "generation_id":"a1b2c3d4e5f60718", "duration_ms":42, "queue_ms":12, "cached":True, "api_duration_ms":30, "cv_text":"private"})
        records = read_recent_events(self.log_directory, limit=10)
        event = next(record for record in records if record["event"] == "generation.stage_timing")
        self.assertEqual(event["context"]["stage"], "writer")
        self.assertEqual(event["context"]["duration_ms"], 42)
        self.assertTrue(event["context"]["cached"])
        self.assertNotIn("cv_text", event["context"])

    def test_structured_events_include_trace_id_and_redact_credentials_and_email(self) -> None:
        request_id = "a1b2c3d4e5f60718"
        with bind_request_id(request_id):
            log_event(
                logging.INFO,
                "dashboard",
                "test.started",
                "Checked password=hunter2 api_key=topsecret user@example.com",
                context={"route": "jobs.detail", "status": 200, "unsafe_body": "should not persist"},
            )
            log_event(
                logging.WARNING,
                "generation",
                "generation.waiting_for_daily_limit",
                "Queued AI jobs are waiting for the daily call limit to renew.",
                context={"error_code": "usage_limit", "limit_name": "max_daily_calls",
                         "count": 100, "limit": 100,
                         "resume_at": "2026-09-28T00:00:00+00:00",
                         "unsafe_body": "private request data"},
            )
            log_event(
                logging.ERROR,
                "generation",
                "generation.failed",
                "AI generation failed due to an internal error.",
                context={"error_code": "internal_error"},
                details={"exception_type": "RuntimeError",
                         "traceback_frames": "  File 'generation.py', line 12, in _process\n    run_agent()\n"},
            )

            try:
                raise RuntimeError("token=private-value")
            except RuntimeError as error:
                log_exception("test", "test.failed", "Synthetic failure.", error=error)

        entries = read_recent_events(self.log_directory, limit=10)
        started = next(entry for entry in entries if entry["event"] == "test.started")
        failed = next(entry for entry in entries if entry["event"] == "test.failed")
        self.assertEqual(started["request_id"], request_id)
        self.assertNotIn("hunter2", started["message"])
        self.assertNotIn("topsecret", started["message"])
        self.assertNotIn("user@example.com", started["message"])
        self.assertNotIn("unsafe_body", json.dumps(started))
        self.assertEqual(started["context"]["route"], "jobs.detail")
        self.assertIn("exception", failed)
        self.assertNotIn("private-value", failed["exception"])
        quota_wait = next(entry for entry in entries if entry["event"] == "generation.waiting_for_daily_limit")
        self.assertEqual(quota_wait["context"]["error_code"], "usage_limit")
        self.assertEqual(quota_wait["context"]["limit_name"], "max_daily_calls")
        self.assertEqual(quota_wait["context"]["limit"], 100)
        self.assertEqual(quota_wait["context"]["resume_at"], "2026-09-28T00:00:00+00:00")
        self.assertNotIn("unsafe_body", json.dumps(quota_wait))
        diagnostic = next(entry for entry in entries if entry["event"] == "generation.failed")
        self.assertEqual(diagnostic["details"]["exception_type"], "RuntimeError")
        self.assertIn("run_agent", diagnostic["details"]["traceback_frames"])

    def test_recent_log_filters_and_bounds_are_applied(self) -> None:
        log_event(logging.INFO, "database", "db.read", "Loaded local snapshot.")
        log_event(logging.ERROR, "dashboard", "api.failed", "API operation failed.")

        errors = read_recent_events(self.log_directory, level="ERROR", query="failed")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["component"], "dashboard")
        with self.assertRaises(LogQueryError):
            read_recent_events(self.log_directory, limit=201)
        with self.assertRaises(LogQueryError):
            read_recent_events(self.log_directory, query="x" * 121)

    def test_since_filter_limits_diagnostics_to_the_current_connection(self) -> None:
        boundary = datetime.now(timezone.utc).replace(microsecond=0)
        old = (boundary - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        current = (boundary + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        path = self.log_directory / "events.jsonl"
        path.write_text(
            json.dumps({"timestamp": old, "level": "INFO", "event": "previous.session"}) + "\n"
            + json.dumps({"timestamp": current, "level": "INFO", "event": "current.session"}) + "\n",
            encoding="utf-8",
        )

        events = read_recent_events(self.log_directory, since=boundary.isoformat().replace("+00:00", "Z"))
        self.assertEqual([event["event"] for event in events], ["current.session"])
        with self.assertRaises(LogQueryError):
            read_recent_events(self.log_directory, since="not-a-timestamp")

    def test_rotation_keeps_a_bounded_number_of_valid_jsonl_files(self) -> None:
        close_logging(self.log_directory)
        self.assertTrue(configure_logging(self.log_directory, max_bytes=1_024, backup_count=2))
        for index in range(8):
            log_event(
                logging.INFO,
                "test",
                "rotation.sample",
                f"event-{index}-" + "x" * 160,
            )
        close_logging(self.log_directory)

        files = list(self.log_directory.glob("events.jsonl*"))
        self.assertLessEqual(len(files), 3)
        self.assertGreaterEqual(len(files), 2)
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                self.assertIsInstance(json.loads(line), dict)

    def test_startup_continues_when_log_directory_cannot_be_created(self) -> None:
        close_logging(self.log_directory)
        blocked = Path(self.temp_dir.name) / "not-a-directory"
        blocked.write_text("file", encoding="utf-8")

        self.assertFalse(configure_logging(blocked / "logs"))
        log_event(logging.INFO, "test", "logging.optional", "Logging must not stop the app.")

    def test_redactor_covers_bearer_query_and_job_url_patterns(self) -> None:
        text = redact_text(
            "Bearer abc.def token=qwerty "
            "https://www.linkedin.com/jobs/view/123456789/?access_token=hidden"
        )
        for private_value in ("abc.def", "qwerty", "123456789", "hidden"):
            self.assertNotIn(private_value, text)
        local_context = redact_text(r"C:\Users\alice\project\test.py:12 saved job 987654321 failed")
        self.assertNotIn("alice", local_context)
        self.assertNotIn("987654321", local_context)

    def test_secrets_are_redacted_before_events_reach_other_logging_handlers(self) -> None:
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            log_event(logging.ERROR, "test", "redaction.check", '"password":"visible-secret"')
            try:
                raise RuntimeError('"token":"trace-secret"')
            except RuntimeError as error:
                log_exception("test", "redaction.exception", "Synthetic failure.", error=error)
        finally:
            root_logger.removeHandler(handler)
            handler.close()

        self.assertNotIn("visible-secret", output.getvalue())
        self.assertNotIn("trace-secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
