"""Tests for SQLite persistence and job-state handling."""

from __future__ import annotations

import tempfile
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path

from linkedin_connector.application_store import ApplicationStore
from linkedin_connector.database import JobStore, JobStoreError
from linkedin_connector.generation_store import GenerationStore
from linkedin_connector.models import LinkedInJob


def make_job(
    job_id: str = "123456789",
    *,
    title: str = "Data Analyst",
    description: str = "Build useful reports",
    complete: bool = True,
    query: str = "?trk=saved_jobs",
) -> LinkedInJob:
    return LinkedInJob(
        linkedin_job_id=job_id,
        title=title,
        company="Example Corp",
        location="Milan, Italy",
        linkedin_url=f"https://www.linkedin.com/jobs/view/{job_id}/{query}",
        description=description,
        description_complete=complete,
    )


class JobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "jobs.sqlite3"
        self.store = JobStore(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sync_deduplicates_and_updates_without_resetting_status(self) -> None:
        first = self.store.sync_jobs([make_job(), make_job()])
        self.assertEqual(first, {"inserted": 1, "refreshed": 0, "total": 1})
        initial = self.store.get_job("123456789")
        self.assertIsNotNone(initial)
        assert initial is not None
        self.assertEqual(initial["status"], "NEW")
        self.assertTrue(initial["linkedin_saved"])
        self.assertEqual(initial["normalized_url"], "https://www.linkedin.com/jobs/view/123456789/")

        processed = self.store.mark_processed("123456789")
        self.assertEqual(processed["status"], "PROCESSED")
        update = self.store.sync_jobs([make_job(title="Senior Data Analyst", description="Updated")])

        self.assertEqual(update, {"inserted": 0, "refreshed": 1, "total": 1})
        refreshed = self.store.get_job("123456789")
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed["title"], "Senior Data Analyst")
        self.assertEqual(refreshed["description"], "Updated")
        self.assertEqual(refreshed["status"], "PROCESSED")
        self.assertEqual(refreshed["first_saved_at"], initial["first_saved_at"])

    def test_new_filter_search_and_job_lookup(self) -> None:
        self.store.sync_jobs([make_job(), make_job("987654321", title="UX Researcher")])

        self.assertEqual(len(self.store.get_new_jobs()), 2)
        self.assertEqual(self.store.get_linkedin_job_ids(), {"123456789", "987654321"})
        self.assertEqual(len(self.store.search_jobs("research")), 1)
        self.assertEqual(self.store.get_job("987654321")["title"], "UX Researcher")
        self.assertIsNone(self.store.get_job("missing"))

    def test_manual_job_is_local_visible_deduplicated_and_excluded_from_linkedin_scans(self) -> None:
        description = (
            "Senior Operations Analyst\nBuild reporting workflows, coordinate stakeholders, "
            "and improve data quality across regional teams."
        )
        job, created = self.store.create_manual_job(
            description, company="Example Org", location="Milan"
        )
        self.assertTrue(created)
        self.assertEqual(job["source"], "manual")
        self.assertEqual(job["title"], "Senior Operations Analyst")
        self.assertEqual(job["company"], "Example Org")
        self.assertEqual(job["linkedin_url"], "")
        self.assertFalse(job["linkedin_saved"])
        self.assertEqual(job["suppress_auto_generation"], 1)
        self.assertTrue(str(job["linkedin_job_id"]).startswith("manual_"))
        self.assertEqual(self.store.get_linkedin_job_ids(include_unsaved=True), set())
        self.assertEqual(self.store.get_job_first_saved_at(), {})

        listed = self.store.get_dashboard_jobs()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["source"], "manual")
        self.assertEqual(listed[0]["job_id"], job["linkedin_job_id"])
        self.assertEqual(self.store.get_saved_jobs()[0]["linkedin_job_id"], job["linkedin_job_id"])

        same_offer = "  ".join(description.split()).swapcase()
        duplicate, duplicate_created = self.store.create_manual_job(same_offer, location="Milan")
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["linkedin_job_id"], job["linkedin_job_id"])
        self.assertEqual(len(self.store.get_dashboard_jobs()), 1)

        generations = GenerationStore(self.store)
        self.assertEqual(generations.reconcile(), 0)
        self.assertIsNone(generations.get(str(job["linkedin_job_id"])))
        self.assertIsNone(generations.claim_next(auto_enabled=True))

    def test_manual_job_rejects_blank_short_or_invalid_text(self) -> None:
        for description in ("", "too short", "\x00" + "valid text " * 5):
            with self.subTest(description=description[:12]), self.assertRaises(JobStoreError):
                self.store.create_manual_job(description)
        self.assertEqual(self.store.get_dashboard_jobs(), [])
        with self.assertRaisesRegex(JobStoreError, "località"):
            self.store.create_manual_job(
                "Senior analyst role\nBuild useful reporting workflows for regional teams."
            )

    def test_dashboard_cover_letter_status_uses_non_empty_text_not_export_files(self) -> None:
        self.store.sync_jobs([make_job()])
        self.assertFalse(self.store.get_dashboard_jobs()[0]["has_cover_letter"])
        self.store.save_job_materials("123456789", cover_letter_text="   \n ")
        self.assertFalse(self.store.get_dashboard_jobs()[0]["has_cover_letter"])
        self.store.save_job_materials("123456789", cover_letter_text="Dear hiring team,")
        listed = self.store.get_dashboard_jobs()[0]
        self.assertTrue(listed["has_cover_letter"])
        self.assertEqual(listed["cover_letter_count"], 0)

    def test_ignored_state_is_not_overwritten_by_mark_processed(self) -> None:
        self.store.sync_jobs([make_job()])
        connection = self.store._connection()
        try:
            connection.execute("UPDATE jobs SET status = 'IGNORED'")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(JobStoreError, "IGNORED"):
            self.store.mark_processed("123456789")
        self.assertEqual(self.store.get_job("123456789")["status"], "IGNORED")

    def test_incomplete_description_rejects_the_entire_sync(self) -> None:
        with self.assertRaisesRegex(JobStoreError, "descrizione completa"):
            self.store.sync_jobs([make_job(), make_job("987654321", complete=False)])
        self.assertEqual(self.store.get_saved_jobs(), [])

    def test_existing_job_database_migrates_recovery_generation_flag(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """CREATE TABLE jobs (
                    job_key TEXT PRIMARY KEY, linkedin_job_id TEXT UNIQUE,
                    normalized_url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    company TEXT, location TEXT, linkedin_url TEXT NOT NULL,
                    description TEXT NOT NULL, description_complete INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'NEW', first_saved_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL)"""
            )
            connection.commit()
        finally:
            connection.close()
        migrated = JobStore(legacy_path)
        connection = migrated._connection()
        try:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        finally:
            connection.close()
        self.assertIn("suppress_auto_generation", columns)
        self.assertIn("linkedin_saved", columns)
        self.assertIn("source", columns)
        self.assertIn("source_fingerprint", columns)
        self.assertIn("last_changed_at", columns)

    def test_chatgpt_job_migration_preserves_existing_job_and_materials(self) -> None:
        self.store.sync_jobs([make_job()])
        self.store.save_job_materials("123456789", cover_letter_text="Existing letter")
        migrated = JobStore(self.database_path)
        job = migrated.get_job("123456789")
        bundle = migrated.get_job_bundle("123456789")
        self.assertEqual(job["origin"], "linkedin")
        self.assertEqual(job["application_status"], "NOT_APPLIED")
        self.assertEqual(bundle["materials"]["cover_letter_text"], "Existing letter")

    def test_legacy_migration_preserves_job_materials_and_cover_letter_file_reference(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy-with-materials.sqlite3"
        legacy_file = Path(self.temp_dir.name) / "cover_letters" / "123456789" / "letter.pdf"
        legacy_file.parent.mkdir(parents=True)
        legacy_file.write_bytes(b"existing-pdf")
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript("""
                CREATE TABLE jobs (
                    job_key TEXT PRIMARY KEY, linkedin_job_id TEXT UNIQUE,
                    normalized_url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    company TEXT, location TEXT, linkedin_url TEXT NOT NULL,
                    description TEXT NOT NULL, description_complete INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'NEW', first_saved_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL, last_changed_at TEXT NOT NULL DEFAULT '',
                    suppress_auto_generation INTEGER NOT NULL DEFAULT 0,
                    linkedin_saved INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'linkedin', source_fingerprint TEXT
                );
                CREATE TABLE job_materials (
                    linkedin_job_id TEXT PRIMARY KEY REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    company_analysis TEXT NOT NULL DEFAULT '', role_analysis TEXT NOT NULL DEFAULT '',
                    personalization_notes TEXT NOT NULL DEFAULT '', cover_letter_text TEXT NOT NULL DEFAULT '',
                    agent_name TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE cover_letters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    linkedin_job_id TEXT NOT NULL REFERENCES jobs(linkedin_job_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL, file_format TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE, original_filename TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(linkedin_job_id,file_format,version)
                );
                INSERT INTO jobs (job_key,linkedin_job_id,normalized_url,title,company,location,
                    linkedin_url,description,description_complete,status,first_saved_at,last_seen_at,
                    source) VALUES ('legacy-key','123456789','https://www.linkedin.com/jobs/view/123456789/',
                    'Legacy analyst','Example Co','Zurich','https://www.linkedin.com/jobs/view/123456789/',
                    'Build reports and preserve this complete legacy listing.',1,'PROCESSED','2026-01-01','2026-01-01','linkedin');
                INSERT INTO job_materials VALUES ('123456789','Company notes','Role notes','Fit notes',
                    'Legacy letter','Legacy agent','2026-01-01');
                INSERT INTO cover_letters VALUES (7,'123456789',1,'pdf',
                    'cover_letters/123456789/letter.pdf','letter.pdf','2026-01-01');
            """)
            connection.commit()
        finally:
            connection.close()

        migrated = JobStore(legacy_path)
        bundle = migrated.get_job_bundle("123456789")
        self.assertEqual(bundle["job"]["application_url"], "https://www.linkedin.com/jobs/view/123456789/")
        self.assertEqual(bundle["job"]["application_status"], "NOT_APPLIED")
        self.assertEqual(bundle["job"]["source_references"], "[]")
        self.assertEqual(bundle["job"]["status"], "PROCESSED")
        self.assertEqual(bundle["materials"]["cover_letter_text"], "Legacy letter")
        self.assertEqual(bundle["cover_letters"][0]["id"], 7)
        letter_record, restored_path = migrated.get_cover_letter_file(7)
        self.assertEqual(letter_record["relative_path"], "cover_letters/123456789/letter.pdf")
        self.assertEqual(restored_path.read_bytes(), b"existing-pdf")
        self.assertEqual(legacy_file.read_bytes(), b"existing-pdf")

    def test_prepared_job_rolls_back_rows_and_first_file_if_second_file_registration_fails(self) -> None:
        from unittest.mock import patch
        from linkedin_connector.database import os as database_os
        replace = database_os.replace
        calls = 0

        def fail_on_second_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("second document registration failed")
            return replace(source, destination)

        with patch("linkedin_connector.database.os.replace", side_effect=fail_on_second_replace):
            with self.assertRaises(OSError):
                self.store.create_chatgpt_job(
                    title="Analyst", company="Example", location="Zurich",
                    description="Build reports and improve data quality across regional teams.",
                    application_url="https://jobs.example.com/roles/atomic",
                    cover_letter_text="Prepared letter",
                    document_contents={"pdf": b"pdf-bytes", "docx": b"docx-bytes"},
                )
        self.assertEqual(self.store.get_dashboard_jobs(), [])
        self.assertEqual(list((self.database_path.parent / "cover_letters").rglob("*.pdf")), [])
        self.assertEqual(list((self.database_path.parent / "cover_letters").rglob("*.docx")), [])

    def test_prepared_job_cleans_temporary_file_when_document_write_fails(self) -> None:
        from unittest.mock import patch

        original_open = Path.open

        class PartialWriteFailure:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def write(self, contents):
                self.handle.write(contents[:1])
                raise OSError("document write failed after creating temporary file")

        def fail_document_write(path, mode="r", *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            if mode == "xb" and path.suffix == ".tmp" and path.parent.parent.name == "cover_letters":
                return PartialWriteFailure(handle)
            return handle

        with patch("linkedin_connector.database.Path.open", new=fail_document_write):
            with self.assertRaisesRegex(OSError, "document write failed"):
                self.store.create_chatgpt_job(
                    title="Analyst", company="Example", location="Zurich",
                    description="Build reports and improve data quality across regional teams.",
                    application_url="https://jobs.example.com/roles/write-failure",
                    cover_letter_text="Prepared letter",
                    document_contents={"pdf": b"pdf-bytes", "docx": b"docx-bytes"},
                )

        cover_letters = self.database_path.parent / "cover_letters"
        self.assertEqual(self.store.get_dashboard_jobs(), [])
        self.assertEqual(list(cover_letters.rglob("*.tmp")), [])
        self.assertEqual(list(cover_letters.rglob("*.pdf")), [])
        self.assertEqual(list(cover_letters.rglob("*.docx")), [])

    def test_chatgpt_job_saves_canonical_url_and_deduplicates_without_overwriting(self) -> None:
        job, created = self.store.create_chatgpt_job(
            title="Analyst", company="Example", location="Zurich",
            description="Build reports and improve data quality across regional teams.",
            application_url="https://Jobs.Example.com/roles/123?utm_source=chatgpt#apply",
            cover_letter_text="Prepared letter", role_analysis="Strong fit",
            personalization_notes="Visa status da verificare",
        )
        self.assertTrue(created)
        self.assertEqual(job["origin"], "chatgpt")
        self.assertEqual(job["application_url"], "https://jobs.example.com/roles/123")
        self.store.save_job_materials(job["linkedin_job_id"], cover_letter_text="My edited letter")
        duplicate, duplicate_created = self.store.create_chatgpt_job(
            title="Different title", company="Different", location="Zurich",
            description="Another complete description for this same role and employer.",
            application_url="https://jobs.example.com/roles/123/",
            cover_letter_text="New generated letter",
        )
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["linkedin_job_id"], job["linkedin_job_id"])
        self.assertEqual(self.store.get_job_bundle(job["linkedin_job_id"])["materials"]["cover_letter_text"], "My edited letter")

    def test_application_status_is_separate_and_manual(self) -> None:
        job, _ = self.store.create_chatgpt_job(
            title="Analyst", company="Example", location="Zurich",
            description="Build reports and improve data quality across regional teams.",
            application_url="https://jobs.example.com/roles/123",
            cover_letter_text="Prepared letter",
        )
        changed = self.store.mark_application_status(job["linkedin_job_id"], "APPLIED")
        self.assertEqual(changed["application_status"], "APPLIED")
        self.assertEqual(changed["status"], "NEW")

    def test_complete_saved_reconciliation_hides_removed_jobs_but_retains_cover_letters(self) -> None:
        self.store.sync_jobs([
            make_job(),
            make_job("222222222", title="Removed without letter"),
            make_job("333333333", title="Removed with letter"),
        ])
        self.store.save_job_materials("333333333", cover_letter_text="Keep this letter")
        self.store.mark_processed("333333333")
        generations = GenerationStore(self.store)
        generations.reconcile()

        counts = self.store.sync_jobs(
            [make_job()], suppress_auto_generation=True, reconcile_saved=True
        )

        self.assertEqual(counts["removed_from_linkedin"], 2)
        self.assertEqual(counts["hidden_without_letter"], 1)
        self.assertEqual(counts["preserved_with_letter"], 1)
        self.assertEqual(counts["cancelled_generation_tasks"], 1)
        self.assertFalse(self.store.get_job("222222222")["linkedin_saved"])
        self.assertEqual(generations.get("222222222")["state"], "MANUAL")
        self.assertEqual(generations.get("222222222")["error_code"], "linkedin_unsaved")
        self.assertFalse(any(row["linkedin_job_id"] == "222222222"
                             for row in self.store.get_dashboard_jobs()))
        retained = self.store.get_job("333333333")
        self.assertFalse(retained["linkedin_saved"])
        self.assertEqual(retained["status"], "PROCESSED")
        self.assertNotIn("333333333", {row["linkedin_job_id"] for row in self.store.get_saved_jobs()})
        self.assertEqual(
            self.store.get_job_bundle("333333333")["materials"]["cover_letter_text"],
            "Keep this letter",
        )
        self.assertEqual(self.store.get_linkedin_job_ids(), {"123456789"})
        self.assertEqual(self.store.get_linkedin_job_ids(include_unsaved=True),
                         {"123456789", "222222222", "333333333"})
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE generation_tasks SET state='MANUAL' WHERE linkedin_job_id='123456789'"
            )
            connection.commit()
        finally:
            connection.close()
        preview = generations.preview_bulk_regeneration(only_new=True)
        self.assertEqual(preview["job_ids"], ["123456789"])

        self.store.sync_jobs([make_job("222222222", title="Re-saved role")])
        generations.reconcile()
        self.assertTrue(self.store.get_job("222222222")["linkedin_saved"])
        self.assertEqual(generations.get("222222222")["state"], "PENDING")

    def test_verified_ids_survive_unreadable_details_and_report_actual_changes(self) -> None:
        self.store.sync_jobs([
            make_job("123456789", title="Visible but unreadable"),
            make_job("222222222", title="No longer saved"),
        ])
        self.store.save_job_materials("123456789", cover_letter_text="Keep current letter")
        current_file = self.store.save_cover_letter_file(
            "123456789", original_filename="current.pdf", file_format="pdf", contents=b"%PDF current"
        )
        self.store.save_job_materials("222222222", cover_letter_text="Keep archived letter")
        removed_file = self.store.save_cover_letter_file(
            "222222222", original_filename="archived.pdf", file_format="pdf", contents=b"%PDF archived"
        )
        self.store.mark_processed("123456789")
        before = self.store.get_job("123456789")
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE jobs SET last_seen_at='2000-01-01T00:00:00+00:00' WHERE linkedin_job_id='123456789'"
            )
            connection.commit()
        finally:
            connection.close()

        counts = self.store.sync_jobs(
            [make_job("333333333", title="New readable")],
            suppress_auto_generation=True,
            reconcile_saved=True,
            visible_ids=["123456789", "333333333", "444444444", "555555555"],
            unreadable=[
                {"linkedin_job_id": "123456789", "title": "Visible but unreadable",
                 "company": "Example Corp", "reason": "Description unavailable"},
                {"linkedin_job_id": "444444444", "title": "New incomplete",
                 "company": "New Corp", "reason": "Description unavailable"},
            ],
            skipped_missing_title=[
                {"linkedin_job_id": "555555555", "title": None,
                 "company": None, "reason": "Title unavailable"},
            ],
        )

        retained = self.store.get_job("123456789")
        self.assertTrue(retained["linkedin_saved"])
        self.assertEqual(retained["description"], before["description"])
        self.assertEqual(retained["status"], "PROCESSED")
        self.assertEqual(retained["last_changed_at"], before["last_changed_at"])
        self.assertNotEqual(retained["last_seen_at"], "2000-01-01T00:00:00+00:00")
        self.assertIsNone(self.store.get_job("444444444"))
        self.assertFalse(self.store.get_job("222222222")["linkedin_saved"])
        self.assertEqual(self.store.get_job_bundle("123456789")["materials"]["cover_letter_text"],
                         "Keep current letter")
        self.assertEqual(self.store.get_job_bundle("222222222")["materials"]["cover_letter_text"],
                         "Keep archived letter")
        self.assertTrue((self.database_path.parent / current_file["relative_path"]).is_file())
        self.assertTrue((self.database_path.parent / removed_file["relative_path"]).is_file())
        self.assertEqual(counts["inserted"], 1)
        self.assertEqual(counts["removed_from_linkedin"], 1)
        self.assertEqual(counts["unreadable_descriptions"], 2)
        self.assertEqual(counts["skipped_missing_title"], 1)
        self.assertEqual(counts["not_imported"], 2)
        self.assertEqual(counts["preserved_files"], 2)
        types = [event["type"] for event in counts["events"]]
        self.assertIn("added", types)
        self.assertIn("maintained", types)
        self.assertIn("not_imported", types)
        missing_title_event = next(
            event for event in counts["events"]
            if event.get("linkedin_job_id") == "555555555"
        )
        self.assertEqual(missing_title_event["reason"], "Title unavailable")
        self.assertIn("removed_from_saved", types)

    def test_sync_report_persists_and_keeps_successful_timestamp_separate(self) -> None:
        partial = {
            "started_at": "2026-10-03T10:00:00+00:00", "ended_at": "2026-10-03T10:00:02+00:00",
            "duration_ms": 2000, "status": "partial", "membership_verified": True,
            "counts": {}, "events": [], "request_id": "partial-run",
        }
        self.store.save_saved_sync_report(partial)
        self.assertIsNone(self.store.get_saved_sync_report()["last_successful_full_sync_at"])

        complete = {**partial, "status": "complete", "request_id": "complete-run"}
        self.store.save_saved_sync_report(complete)
        later_partial = {**partial, "ended_at": "2026-10-03T11:00:02+00:00", "request_id": "later-partial"}
        self.store.save_saved_sync_report(later_partial)

        reopened = JobStore(self.database_path)
        saved = reopened.get_saved_sync_report()
        self.assertEqual(saved["last_report"], later_partial)
        self.assertEqual(saved["last_successful_full_sync_at"], complete["ended_at"])

    def test_unchanged_scan_updates_last_seen_without_changing_last_changed(self) -> None:
        self.store.sync_jobs([make_job()])
        before = self.store.get_job("123456789")
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE jobs SET last_seen_at='2000-01-01T00:00:00+00:00' WHERE linkedin_job_id='123456789'"
            )
            connection.commit()
        finally:
            connection.close()
        result = self.store.sync_jobs(
            [make_job()], reconcile_saved=True, visible_ids=["123456789"]
        )
        after = self.store.get_job("123456789")
        self.assertEqual(after["last_changed_at"], before["last_changed_at"])
        self.assertNotEqual(after["last_seen_at"], "2000-01-01T00:00:00+00:00")
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["maintained"], 1)

        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE jobs SET last_changed_at='2000-01-01T00:00:00+00:00' WHERE linkedin_job_id='123456789'"
            )
            connection.commit()
        finally:
            connection.close()
        changed = self.store.sync_jobs(
            [make_job(title="Senior Data Analyst")],
            reconcile_saved=True, visible_ids=["123456789"],
        )
        changed_row = self.store.get_job("123456789")
        update_event = next(event for event in changed["events"] if event["type"] == "updated")
        self.assertNotEqual(changed_row["last_changed_at"], "2000-01-01T00:00:00+00:00")
        self.assertEqual(update_event["changed_fields"], ["titolo"])

    def test_incomplete_saved_reconciliation_rolls_back_all_updates(self) -> None:
        self.store.sync_jobs([make_job(), make_job("222222222")])
        generations = GenerationStore(self.store)
        with self.assertRaisesRegex(JobStoreError, "descrizione completa"):
            self.store.sync_jobs(
                [make_job(title="Would update"), make_job("333333333", complete=False)],
                reconcile_saved=True,
            )
        self.assertEqual(self.store.get_job("123456789")["title"], "Data Analyst")
        self.assertTrue(self.store.get_job("123456789")["linkedin_saved"])
        self.assertTrue(self.store.get_job("222222222")["linkedin_saved"])
        self.assertIsNone(self.store.get_job("333333333"))
        self.assertIsNone(generations.get("123456789"))

    def test_missing_linkedin_location_is_optional_and_known_location_is_preserved(self) -> None:
        self.store.sync_jobs([make_job()])
        changed_without_location = replace(make_job(title="Changed title"), location=None)
        self.store.sync_jobs([changed_without_location], reconcile_saved=True,
                             visible_ids=["123456789"])
        current = self.store.get_job("123456789")
        self.assertEqual(current["title"], "Changed title")
        self.assertEqual(current["location"], "Milan, Italy")
        self.assertTrue(current["linkedin_saved"])

        new_without_location = replace(make_job("987654321"), location=None)
        self.store.sync_jobs([new_without_location])
        self.assertIsNone(self.store.get_job("987654321")["location"])

    def test_saved_reconciliation_waits_for_an_active_generation_without_partial_updates(self) -> None:
        self.store.sync_jobs([make_job(), make_job("222222222")])
        generations = GenerationStore(self.store)
        generations.reconcile()
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE generation_tasks SET state='RUNNING' WHERE linkedin_job_id='222222222'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(JobStoreError, "Nessun dato è stato modificato"):
            self.store.sync_jobs(
                [make_job(title="Would update")], reconcile_saved=True
            )

        self.assertEqual(self.store.get_job("123456789")["title"], "Data Analyst")
        self.assertTrue(self.store.get_job("123456789")["linkedin_saved"])
        self.assertTrue(self.store.get_job("222222222")["linkedin_saved"])
        self.assertEqual(generations.get("222222222")["state"], "RUNNING")

    def test_recovery_import_does_not_reset_existing_letter_or_job_status(self) -> None:
        self.store.sync_jobs([make_job()])
        self.store.save_job_materials("123456789", cover_letter_text="Existing letter.")
        self.store.mark_processed("123456789")
        before = self.store.get_job("123456789")
        self.store.sync_jobs(
            [make_job(title="Refreshed title")], suppress_auto_generation=True
        )
        after = self.store.get_job("123456789")
        bundle = self.store.get_job_bundle("123456789")
        self.assertEqual(after["title"], "Refreshed title")
        self.assertEqual(after["status"], before["status"])
        self.assertEqual(after["suppress_auto_generation"], 0)
        self.assertEqual(bundle["materials"]["cover_letter_text"], "Existing letter.")

    def test_invalid_url_or_empty_search_is_rejected(self) -> None:
        invalid = LinkedInJob(
            linkedin_job_id="123456789",
            title="Data Analyst",
            company=None,
            location=None,
            linkedin_url="http://linkedin.com/jobs/view/123456789/",
            description="Description",
            description_complete=True,
        )
        with self.assertRaisesRegex(JobStoreError, "HTTPS LinkedIn"):
            self.store.sync_jobs([invalid])
        with self.assertRaisesRegex(JobStoreError, "query must not be empty"):
            self.store.search_jobs("  ")

    def test_processed_transition_requires_existing_job(self) -> None:
        with self.assertRaisesRegex(JobStoreError, "No saved job"):
            self.store.mark_processed("not-found")

    def test_dashboard_materials_and_versioned_cover_letter_files(self) -> None:
        self.store.sync_jobs([make_job()])
        materials = self.store.save_job_materials(
            "123456789",
            company_analysis="A global analytics team.",
            role_analysis="Build reports and dashboards.",
            personalization_notes="Connects reporting experience to their product.",
            cover_letter_text="Dear hiring team,\n\nI am applying...",
            agent_name="Letter Agent",
        )
        self.assertEqual(materials["agent_name"], "Letter Agent")

        pdf_one = self.store.save_cover_letter_file(
            "123456789",
            original_filename="letter.pdf",
            file_format="pdf",
            contents=b"%PDF-test v1",
        )
        docx_one = self.store.save_cover_letter_file(
            "123456789",
            original_filename="letter.docx",
            file_format="docx",
            contents=b"PK\\x03\\x04-test v1",
        )
        pdf_two = self.store.save_cover_letter_file(
            "123456789",
            original_filename="letter-final.pdf",
            file_format="pdf",
            contents=b"%PDF-test v2",
        )

        bundle = self.store.get_job_bundle("123456789")
        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle["materials"]["company_analysis"], "A global analytics team.")
        self.assertEqual([row["version"] for row in bundle["cover_letters"]], [2, 1, 1])
        self.assertEqual(pdf_one["relative_path"], "cover_letters/123456789/cover-letter-v01.pdf")
        self.assertEqual(docx_one["version"], 1)
        self.assertEqual(pdf_two["version"], 2)

        file_record = self.store.get_cover_letter_file(int(pdf_two["id"]))
        self.assertIsNotNone(file_record)
        assert file_record is not None
        self.assertEqual(file_record[1].read_bytes(), b"%PDF-test v2")

    def test_dashboard_materials_reject_unknown_job_and_oversized_text(self) -> None:
        with self.assertRaisesRegex(JobStoreError, "No saved job"):
            self.store.save_job_materials("missing", company_analysis="x")
        self.store.sync_jobs([make_job()])
        with self.assertRaisesRegex(JobStoreError, "character limit"):
            self.store.save_job_materials("123456789", company_analysis="x" * 20_001)

    def test_forgetting_job_cascades_private_state_and_suppresses_future_sync(self) -> None:
        self.store.sync_jobs([make_job()])
        ApplicationStore(self.store)
        tasks = GenerationStore(self.store)
        self.store.save_job_materials("123456789", cover_letter_text="A saved draft.")
        saved_file = self.store.save_cover_letter_file(
            "123456789", original_filename="letter.pdf", file_format="pdf", contents=b"%PDF-test"
        )
        ApplicationStore(self.store).save_research(
            "123456789", state="READY", summary="A cited company summary."
        )
        ApplicationStore(self.store).add_chat_message("123456789", "user", "Keep this local chat private.")
        tasks.queue_manual("123456789")
        connection = self.store._connection()
        try:
            connection.execute(
                """INSERT INTO api_operation_usage
                   (linkedin_job_id,model,operation,input_tokens,output_tokens,search_calls,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                ("123456789", "test-model", "agent", 12, 4, 0, "2026-09-27T00:00:00+00:00"),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(self.store.forget_job("123456789"), "deleted")
        self.assertIsNone(self.store.get_job("123456789"))
        self.assertTrue((self.database_path.parent / saved_file["relative_path"]).is_file())
        connection = self.store._connection()
        try:
            for table in (
                "job_materials", "cover_letters", "generation_tasks", "ai_usage",
                "company_research", "job_chat_messages", "letter_versions", "cover_letter_feedback",
            ):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            usage = connection.execute(
                "SELECT linkedin_job_id,input_tokens,output_tokens FROM api_operation_usage"
            ).fetchone()
            self.assertEqual((usage["linkedin_job_id"], usage["input_tokens"], usage["output_tokens"]),
                             (None, 12, 4))
            tombstone = connection.execute("SELECT job_id_hash FROM forgotten_jobs").fetchone()
            self.assertEqual(len(tombstone["job_id_hash"]), 64)
            self.assertNotIn("123456789", tombstone["job_id_hash"])
        finally:
            connection.close()

        self.assertEqual(self.store.sync_jobs([make_job()]), {"inserted": 0, "refreshed": 0, "total": 0})
        self.assertIsNone(self.store.get_job("123456789"))

    def test_forgetting_job_is_rejected_while_generation_is_running(self) -> None:
        self.store.sync_jobs([make_job()])
        GenerationStore(self.store).queue_manual("123456789")
        connection = self.store._connection()
        try:
            connection.execute("UPDATE generation_tasks SET state='RUNNING' WHERE linkedin_job_id='123456789'")
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(self.store.forget_job("123456789"), "running")
        self.assertIsNotNone(self.store.get_job("123456789"))
        connection = self.store._connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM forgotten_jobs").fetchone()[0], 0)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
