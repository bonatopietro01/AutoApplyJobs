"""Local per-job research, chat, feedback, and style-example persistence tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from linkedin_connector.application_store import ApplicationStore
from linkedin_connector.database import JobStore, JobStoreError
from linkedin_connector.generation_store import GenerationStore
from linkedin_connector.research_pages import research_page_cache_key
from tests.test_database import make_job


class ApplicationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.job_store = JobStore(Path(self.temp_dir.name) / "jobs.sqlite3")
        self.job_store.sync_jobs([make_job("123456789"), make_job("987654321")])
        self.app = ApplicationStore(self.job_store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_company_research_persists_consultation_date_facts_and_only_cited_urls(self) -> None:
        source = {"url": "https://example.com/news", "title": "Official newsroom"}
        saved = self.app.save_research(
            "123456789", state="READY", summary="A sourced company overview.",
            sources=[source], facts=[
                {"claim": "The company launched a program.", "kind": "documented_fact",
                 "source_urls": [source["url"], "https://invented.invalid/source"]},
                {"claim": "This may matter to the role.", "kind": "interpretation",
                 "source_urls": [source["url"]]},
                {"claim": "An unsourced claim must not be retained.", "kind": "interpretation",
                 "source_urls": []},
            ], gaps="No local-office details found.", search_calls=1, job_hash="abc123",
        )
        self.assertEqual(saved["sources"], [source])
        self.assertEqual(saved["facts"][0]["source_urls"], [source["url"]])
        self.assertEqual(saved["facts"][1]["kind"], "interpretation")
        self.assertEqual(len(saved["facts"]), 2)
        self.assertIn("T", saved["updated_at"])
        self.assertEqual(saved["gaps"], "No local-office details found.")

    def test_company_database_reuses_source_backed_facts_without_expiry(self) -> None:
        source = {"url": "https://example.com/about", "title": "Official company profile"}
        research = {"summary": "Public company overview.", "sources": [source],
                    "facts": [{"claim": "The company works in analytics.",
                               "kind": "documented_fact", "source_urls": [source["url"]]}],
                    "gaps": "No local-office information found.", "search_calls": 1}
        self.assertTrue(self.app.save_company_research("Example, Inc.", research))
        cached = self.app.get_company_research("  EXAMPLE INC  ")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["summary"], research["summary"])
        self.assertEqual(cached["facts"][0]["source_urls"], [source["url"]])
        self.assertEqual(cached["cache_scope"], "company")
        self.assertIsNone(self.app.get_company_research("Different Company"))
        self.assertFalse(self.app.save_company_research(
            "No Sources Ltd", {"summary": "Unverified", "sources": [], "facts": []}))

        connection = self.job_store._connection()
        try:
            # The former expiring cache may contain a stale row; the durable profile is independent of it.
            connection.execute("UPDATE company_research_cache SET expires_at='2000-01-01T00:00:00+00:00'")
            connection.commit()
        finally:
            connection.close()
        self.assertIsNotNone(self.app.get_company_research("Example Inc"))
        reopened = ApplicationStore(self.job_store)
        self.assertEqual(reopened.get_company_research("Example Inc")["summary"], research["summary"])

    def test_company_database_migrates_legacy_expired_research(self) -> None:
        source = {"url": "https://example.com/about", "title": "Company"}
        connection = self.job_store._connection()
        try:
            connection.execute(
                """INSERT INTO company_research_cache
                   (company_key,company_name,summary,sources_json,facts_json,gaps,search_calls,
                    updated_at,expires_at,last_used_at,use_count)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                ("legacy-key", "Legacy Company", "Migrated company profile.",
                 '[{"url":"https://example.com/about","title":"Company"}]',
                 '[{"claim":"Verified fact","kind":"documented_fact","source_urls":["https://example.com/about"]}]',
                 "", 1, "2020-01-01T00:00:00+00:00", "2020-01-08T00:00:00+00:00",
                 "2020-01-01T00:00:00+00:00", 3),
            )
            connection.commit()
        finally:
            connection.close()
        reopened = ApplicationStore(self.job_store)
        migrated = reopened.get_company_research("Legacy Company")
        self.assertIsNotNone(migrated)
        self.assertEqual(migrated["summary"], "Migrated company profile.")
        self.assertEqual(migrated["sources"], [source])
        self.assertEqual(migrated["facts"][0]["claim"], "Verified fact")
        self.assertEqual(migrated["researched_at"], "2020-01-01T00:00:00+00:00")

    def test_company_page_cache_is_bounded_bound_to_cited_sources_and_research_revision(self) -> None:
        source = {"url": "https://example.com/company", "title": "Company"}
        saved = self.app.save_research("123456789", state="READY", sources=[source], job_hash="job1")
        key = research_page_cache_key(saved)
        page = {"url": source["url"], "title": source["title"], "state": "READY",
                "error_code": "", "text": "Official company page content."}
        self.app.save_research_pages("123456789", key, [page,
            {**page, "url": "https://not-cited.example/"}, {**page, "url": source["url"]}])
        self.assertEqual(self.app.get_research_pages("123456789", key), [page])
        self.assertIsNone(self.app.get_research_pages("123456789", "different-research"))
        revised = self.app.save_research("123456789", state="READY", sources=[source], job_hash="job2")
        self.assertNotEqual(key, research_page_cache_key(revised))

    def test_company_page_cache_keeps_at_most_four_distinct_sites_and_safe_timings(self) -> None:
        urls = [
            "https://www.example.com/about",
            "https://example.com/careers",
            "https://jobs.example.org/company",
            "https://press.example.net/news",
            "https://company.example.edu/about",
            "https://another.example.io/about",
        ]
        saved = self.app.save_research(
            "123456789", state="READY", summary="Company research.",
            sources=[{"url": url, "title": f"Source {index}"} for index, url in enumerate(urls)],
            job_hash="bounded-sites",
        )
        key = research_page_cache_key(saved)
        pages = [{"url": url, "title": f"Source {index}", "state": "READY",
                  "error_code": "", "text": "Public company page.",
                  "duration_ms": 14 + index, "fetched_at": "2026-10-04T08:00:00+00:00"}
                 for index, url in enumerate(urls)]
        self.app.save_research_pages("123456789", key, pages)
        cached = self.app.get_research_pages("123456789", key)
        self.assertEqual(len(cached), 4)
        self.assertEqual(len({url.split("/")[2].removeprefix("www.") for url in
                              (item["url"] for item in cached)}), 4)
        self.assertEqual(cached[0]["duration_ms"], 14)
        self.assertEqual(cached[0]["fetched_at"], "2026-10-04T08:00:00+00:00")

    def test_chat_feedback_and_history_are_isolated_by_job(self) -> None:
        self.app.add_chat_message("123456789", "user", "Make the opening more specific.")
        history = self.app.add_chat_message(
            "123456789", "assistant", "I made the opening more concrete.",
            {"cover_letter_text": "Dear team", "sources": []},
        )
        self.app.add_chat_message("987654321", "user", "A different role question.")
        self.assertEqual(len(history), 2)
        self.assertEqual(len(self.app.chat_history("987654321")), 1)
        self.assertEqual(history[-1]["proposal"]["cover_letter_text"], "Dear team")
        self.app.add_feedback("123456789", 1, 2, "too generic")
        self.assertEqual(self.app.feedback_history("123456789")[0]["rating"], 2)
        self.assertEqual(self.app.feedback_history("987654321"), [])

    def test_knowledge_library_caps_at_ten_and_supports_in_place_replacement(self) -> None:
        saved = []
        for index in range(10):
            saved.append(self.app.add_knowledge(f"sample-{index}.txt", "txt", b"Old sample", "Old style"))
        with self.assertRaises(JobStoreError):
            self.app.add_knowledge("eleventh.txt", "txt", b"Too many", "Too many")
        replaced = self.app.replace_knowledge(saved[0]["id"], "updated.md", "md", b"New", "Updated style")
        self.assertEqual(len(self.app.list_knowledge()), 10)
        self.assertEqual(replaced["filename"], "updated.md")
        self.assertEqual(self.app.knowledge_text()[0]["text"], "Updated style")
        self.assertTrue(self.app.delete_knowledge(saved[1]["id"]))
        self.assertEqual(len(self.app.list_knowledge()), 9)

    def test_daily_call_reservations_enforce_total_and_category_limits(self) -> None:
        tasks = GenerationStore(self.job_store)
        first = tasks.reserve_api_call("123456789", "test-model", "chat", daily_limit=5, category_limit=1)
        self.assertIsNotNone(first)
        self.assertIsNone(tasks.reserve_api_call("123456789", "test-model", "chat", daily_limit=5, category_limit=1))
        research = tasks.reserve_api_call("987654321", "test-model", "company_research", daily_limit=5, category_limit=2)
        self.assertIsNotNone(research)
        summary = tasks.summary()["usage"]
        self.assertEqual(summary["calls_today"], 2)
        self.assertEqual(summary["today_by_operation"]["chat"], 1)
        self.assertEqual(summary["today_by_operation"]["company_research"], 1)
        self.assertIsNone(tasks.reserve_api_call("123456789", "test-model", "agent", daily_limit=2, category_limit=10))
        self.assertIsNone(tasks.reserve_api_call("123456789", "test-model", "agent", daily_limit=10, category_limit=0))
        self.assertEqual(tasks.daily_limit_status({"max_daily_calls": 10, "max_daily_agent_calls": 0}),
                         {"name": "max_daily_agent_calls", "used": 0, "limit": 0})


if __name__ == "__main__":
    unittest.main()
