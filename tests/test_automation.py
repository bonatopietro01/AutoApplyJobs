"""Real queue behavior with a simulated AI provider and no paid API calls."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from linkedin_connector.ai_config import (
    AIConfigurationError, AISettingsStore, APIKeyStore,
    ENV_API_KEY, default_settings,
)
from linkedin_connector.ai_provider import AIProviderError, OpenAIProvider, merge_stage_output
from linkedin_connector.database import JobStore, JobStoreError
from linkedin_connector.generation import GenerationCoordinator
from linkedin_connector.generation_store import GenerationStore
from linkedin_connector.letter_documents import render_letter_documents
from linkedin_connector.profile import AgentPromptStore, CandidateProfileStore
from tests.test_database import make_job


TEST_AGENT = {"id": "writer", "name": "Writer", "phase": "Writing", "enabled": True,
              "instructions": "Write an evidence-based application letter."}
TEST_WORKFLOW_AGENTS = [
    {"id": "job-company-research", "name": "Job & Company Research",
     "phase": "job-company-research", "instructions": "Research the company."},
    {"id": "cv-match-writer", "name": "CV Match & Writer",
     "phase": "cv-match-writer", "instructions": "Match evidence and write the letter."},
    {"id": "reviewer", "name": "Reviewer", "phase": "reviewer", "instructions": "Review the letter."},
    {"id": "main-coordinator", "name": "Main Coordinator",
     "phase": "main-coordinator", "instructions": "Finalize the deliverables."},
]
TEST_AGENTS_MARKDOWN = "# Test workflow\n\n## Internal roles and workflow\n\n" + "\n\n".join(
    f"### {index}. {agent['name']}\n<!-- agent:id={agent['id']} phase={agent['phase'].casefold()} enabled=true -->\n\n"
    f"{agent['instructions']}"
    for index, agent in enumerate(TEST_WORKFLOW_AGENTS, 1)
) + "\n"


class FakeCredentialBackend:
    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.passwords.pop((service, username), None)


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.agent_runs: list[dict[str, object]] = []
        self.sequence: list[str] = []
        self.fail_job: str | None = None
        self.fail_research_code: str | None = None
        self.fail_unexpected: str | None = None
        self.research_calls = 0
        self.research_job_ids: list[str] = []
        self.hold_writer_job: str | None = None
        self.writer_started = threading.Event()
        self.allow_writer = threading.Event()
        self.allow_writer.set()

    def run_agent(self, *, agent, global_instructions, job, cv_text, candidate_context, previous,
                  research=None, knowledge=None, candidate_evidence=None):
        job_id = str(job["linkedin_job_id"])
        if agent["id"] == "cv-match-writer" and job_id == self.hold_writer_job:
            self.writer_started.set()
            self.allow_writer.wait(timeout=10)
        self.calls.append((job_id, agent["id"]))
        self.sequence.append(str(agent["id"]))
        self.agent_runs.append({
            "job_id": job_id,
            "agent_id": agent["id"],
            "previous": dict(previous),
            "research": dict(research or {}),
            "knowledge": [dict(example) for example in (knowledge or [])],
            "cv_text": cv_text,
            "candidate_context": candidate_context,
            "candidate_evidence": [dict(item) for item in (candidate_evidence or [])],
        })
        if self.fail_unexpected:
            raise RuntimeError(self.fail_unexpected)
        if self.fail_job == job_id:
            raise AIProviderError("Synthetic provider failure", "rate_limit")
        self.last_usage = {"input_tokens": 50, "output_tokens": 20}
        output = {
            "company_analysis": f"Company for {job_id}",
            "role_analysis": f"Role for {job_id}",
            "personalization_notes": f"Matched to {cv_text}",
            "cover_letter_text": f"Dear team, I am applying for {job_id}.",
            "gaps": "", "needs_review": False,
        }
        # Mirror the real provider's stage merge: the writer schema cannot replace role analysis.
        if agent["id"] in {"cv-match-writer", "reviewer"} and previous.get("local_decisions"):
            output["role_analysis"] = previous["role_analysis"]
        return output

    def research_company(self, *, job, global_instructions):
        self.research_calls += 1
        self.research_job_ids.append(str(job["linkedin_job_id"]))
        self.sequence.append("web_research")
        self.last_usage = {"input_tokens": 0, "output_tokens": 0, "search_calls": 1}
        if self.fail_research_code:
            raise AIProviderError("Synthetic research failure", self.fail_research_code)
        return {"summary": f"Verified company information for {job['company']}.",
                "facts": [{"claim": "An official company fact.", "kind": "documented_fact",
                           "source_urls": ["https://example.com/company"]}],
                "gaps": "", "sources": [{"url": "https://example.com/company", "title": "Official company site"}],
                "search_calls": 1}


class AutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.research_pages_patch = patch("linkedin_connector.research_pages.fetch_research_pages", return_value=[])
        self.research_pages_patch.start()
        self.addCleanup(self.research_pages_patch.stop)
        self.environment_patch = patch.dict(os.environ, {ENV_API_KEY: ""})
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = JobStore(self.root / "data" / "jobs.sqlite3")
        self.store.sync_jobs([make_job("123456789"), make_job("987654321")])
        self.tasks = GenerationStore(self.store)
        self.backend = FakeCredentialBackend()
        self.keys = APIKeyStore(self.store.database_path, backend=self.backend)
        self.keys.save("sk-test-key-for-local-tests")
        self.config = AISettingsStore(self.root / "data" / "user_profile" / "automation.json")
        settings = default_settings()
        settings["enabled"] = settings["consent"] = True
        settings["max_per_cycle"] = 2
        settings["web_research_enabled"] = True
        self.config.save(settings)
        self.agents = AgentPromptStore(self.root / "data" / "user_profile" / "AGENTS.md")
        self.agents.save(TEST_AGENTS_MARKDOWN)
        self.context = AgentPromptStore(self.root / "data" / "user_profile" / "CANDIDATE_CONTEXT.md")
        self.context.save("# Candidate\nWorks with reports.")
        self.profile = CandidateProfileStore(self.root / "data")
        self.profile.save("cv.txt", "txt", b"Builds reports")
        self.provider = FakeProvider()
        self.coordinator = GenerationCoordinator(
            self.store, self.tasks, self.config, self.keys, self.agents,
            self.context, self.profile,
            provider_factory=lambda key, model: self.provider,
            renderer=lambda text: (b"%PDF-1.7\n" + text.encode(), b"PK" + text.encode()),
        )
        self.coordinator.local_decisions.save_settings({"enabled": False})

    def tearDown(self) -> None:
        self.coordinator.stop()
        self.temp_dir.cleanup()

    def wait_for(self, job_id: str, state: str) -> None:
        until = time.monotonic() + 4
        while time.monotonic() < until:
            if self.tasks.get(job_id)["state"] == state:
                return
            time.sleep(0.02)
        self.fail(f"Job {job_id} did not reach {state}: {self.tasks.get(job_id)}")

    def test_stop_closes_local_decision_worker(self) -> None:
        from unittest.mock import patch

        with patch.object(self.coordinator.local_decisions, "close") as close_worker:
            self.coordinator.stop()
        close_worker.assert_called_once_with()

    def test_new_settings_enable_web_research_but_keep_automation_and_consent_off(self) -> None:
        settings = default_settings()
        self.assertTrue(settings["web_research_enabled"])
        self.assertEqual(settings["model"], "gpt-5.6-luna")
        self.assertEqual(settings["max_daily_calls"], 200)
        self.assertEqual(settings["max_daily_agent_calls"], 200)
        self.assertFalse(settings["enabled"])
        self.assertFalse(settings["consent"])

    def test_company_research_prefetch_runs_while_current_job_is_writing_and_is_reused(self) -> None:
        connection = self.store._connection()
        try:
            connection.execute("UPDATE jobs SET company='Shared Example Company'")
            connection.commit()
        finally:
            connection.close()
        self.provider.hold_writer_job = "123456789"
        self.provider.allow_writer.clear()
        self.tasks.reconcile()
        self.coordinator.start()

        self.assertTrue(self.provider.writer_started.wait(timeout=4), "first application did not reach writing")
        try:
            deadline = time.monotonic() + 4
            cached = None
            while time.monotonic() < deadline:
                cached = self.coordinator.application_data.get_research("987654321")
                if cached and cached.get("state") == "READY" and self.tasks.get("987654321")["state"] == "PENDING":
                    break
                time.sleep(0.02)
            self.assertIsNotNone(cached, "next queued application was not researched during writing")
            self.assertEqual(cached["state"], "READY")
            self.assertEqual(self.tasks.get("987654321")["state"], "PENDING")
            self.assertEqual(self.provider.research_calls, 1, "same-company research should be served from cache")
        finally:
            # Always release the first worker before temporary SQLite cleanup.
            self.provider.allow_writer.set()
        self.wait_for("123456789", "READY")
        self.wait_for("987654321", "READY")
        second_job_agents = [agent_id for job_id, agent_id in self.provider.calls if job_id == "987654321"]
        self.assertNotIn("job-company-research", second_job_agents)
        self.assertEqual(second_job_agents, ["cv-match-writer", "reviewer"])

    def test_durable_company_database_skips_web_and_only_calls_writer_and_reviewer(self) -> None:
        company_source = {"url": "https://example.com/about", "title": "Official company profile"}
        self.assertTrue(self.coordinator.application_data.save_company_research(
            "Example Corp", {
                "summary": "The company publishes official analytics products.",
                "sources": [company_source],
                "facts": [{"claim": "The company publishes analytics products.",
                           "kind": "documented_fact", "source_urls": [company_source["url"]]}],
                "gaps": "", "search_calls": 1,
            }))
        self.coordinator.application_data.add_knowledge(
            "style.txt", "txt", b"Example style", "Example style: concise opening and evidence-led paragraphs.")
        from tests.test_local_decisions import FakeRouter
        self.coordinator.local_decisions.router_factory = FakeRouter
        self.coordinator.local_decisions.save_settings({"enabled": True})
        self.tasks.reconcile()
        with patch("linkedin_connector.generation.cached_research_pages",
                   side_effect=AssertionError("Cached company profiles must not trigger a page fetch")):
            self.coordinator._process(self.tasks.claim_next(auto_enabled=True), self.config.load())

        self.assertEqual(self.tasks.get("123456789")["state"], "READY")
        self.assertEqual(self.provider.research_calls, 0)
        self.assertEqual([agent_id for job_id, agent_id in self.provider.calls if job_id == "123456789"],
                         ["cv-match-writer", "reviewer"])
        writer = next(run for run in self.provider.agent_runs if run["agent_id"] == "cv-match-writer")
        self.assertIn("analytics products", writer["research"]["summary"])
        self.assertEqual(writer["research"]["sources"][0]["url"], company_source["url"])
        self.assertEqual(writer["knowledge"][0]["text"],
                         "Example style: concise opening and evidence-led paragraphs.")
        bundle = self.store.get_job_bundle("123456789")
        self.assertEqual(bundle["cover_letters"], [])

    def test_agent_markdown_is_the_only_source_and_key_is_separate_from_settings(self) -> None:
        self.assertNotIn("sk-test-key", self.config.path.read_text(encoding="utf-8"))
        self.assertEqual(self.keys.get(), "sk-test-key-for-local-tests")
        customized = self.config.load()
        self.assertNotIn("agents", customized)
        self.assertEqual(self.agents.agents()[0]["name"], "Job & Company Research")
        replacement = [{**TEST_AGENT, "name": "Custom writer"}]
        self.agents.replace_agents(replacement)
        self.assertEqual(self.agents.agents()[0]["name"], "Custom writer")
        self.keys.delete()
        self.assertIsNone(self.keys.get())
        with patch.dict(os.environ, {ENV_API_KEY: "sk-env-only-test-value"}):
            self.assertEqual(self.keys.get(), "sk-env-only-test-value")
            self.assertEqual(self.keys.source(), "ambiente")
        other = APIKeyStore(self.root / "other_installation" / "jobs.sqlite3", backend=self.backend)
        self.assertIsNone(other.get())
        other_settings = AISettingsStore(self.root / "other_installation" / "automation.json")
        self.assertNotIn("agents", other_settings.load())

    def test_age_setting_migrates_old_configuration_and_validates_bounds(self) -> None:
        raw = self.config.load()
        raw.pop("max_auto_job_age_days")
        self.config.path.write_text(json.dumps({**raw, "schema_version": 2}), encoding="utf-8")
        self.assertEqual(self.config.load()["max_auto_job_age_days"], 90)
        invalid = default_settings()
        invalid["max_auto_job_age_days"] = 3_651
        with self.assertRaises(AIConfigurationError):
            self.config.save(invalid)

    def test_manual_request_can_claim_archived_job_when_automation_is_disabled(self) -> None:
        connection = self.store._connection()
        try:
            connection.execute("UPDATE jobs SET status='IGNORED' WHERE linkedin_job_id=?", ("123456789",))
            connection.commit()
        finally:
            connection.close()
        self.tasks.reconcile()

        automatic = self.tasks.claim_next(auto_enabled=True)
        self.assertIsNotNone(automatic)
        self.assertEqual(automatic["linkedin_job_id"], "987654321")
        self.tasks.queue_manual("123456789")
        manual = self.tasks.claim_next(auto_enabled=False)
        self.assertIsNotNone(manual)
        self.assertEqual(manual["linkedin_job_id"], "123456789")
        self.assertEqual(manual["manual_request"], 1)

    def test_old_automatic_job_is_skipped_without_calls_but_manual_retry_is_allowed(self) -> None:
        old_saved_at = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE jobs SET first_saved_at=? WHERE linkedin_job_id=?",
                (old_saved_at, "123456789"),
            )
            connection.execute("UPDATE jobs SET status='IGNORED' WHERE linkedin_job_id=?", ("987654321",))
            connection.commit()
        finally:
            connection.close()

        self.coordinator.start()
        self.wait_for("123456789", "STALE")
        skipped = self.tasks.get("123456789")
        self.assertEqual(skipped["error_code"], "auto_age_limit")
        self.assertIn("90 giorni", skipped["stage"])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.provider.research_calls, 0)

        self.coordinator.request_one("123456789")
        self.wait_for("123456789", "READY")
        self.assertEqual(len(self.provider.calls), 4)
        self.assertEqual(self.provider.research_calls, 1)

    def test_age_limit_change_releases_only_automatic_jobs_now_in_range(self) -> None:
        old_saved_at = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE jobs SET first_saved_at=? WHERE linkedin_job_id=?",
                (old_saved_at, "123456789"),
            )
            connection.execute("UPDATE jobs SET status='IGNORED' WHERE linkedin_job_id=?", ("987654321",))
            connection.commit()
        finally:
            connection.close()
        self.tasks.reconcile()

        self.assertEqual(self.tasks.apply_auto_age_limit(90), {"skipped": 1, "released": 0, "unverified": 0})
        self.assertEqual(self.tasks.apply_auto_age_limit(180), {"skipped": 0, "released": 1, "unverified": 0})
        self.assertEqual(self.tasks.get("123456789")["state"], "PENDING")
        self.assertEqual(self.tasks.apply_auto_age_limit(90), {"skipped": 1, "released": 0, "unverified": 0})

        self.tasks.queue_manual("123456789")
        self.assertEqual(self.tasks.apply_auto_age_limit(90), {"skipped": 0, "released": 0, "unverified": 0})
        self.assertEqual(self.tasks.get("123456789")["state"], "PENDING")
        self.assertEqual(self.tasks.get("123456789")["manual_request"], 1)
        self.assertEqual(self.provider.calls, [])

    def test_generation_uses_knowledge_in_order_and_archives_matching_documents(self) -> None:
        from docx import Document
        from pypdf import PdfReader

        example_text = "Example style marker: direct opening and two concrete paragraphs."
        self.coordinator.application_data.add_knowledge(
            "style-example.txt", "txt", example_text.encode("utf-8"), example_text,
        )
        self.coordinator.renderer = Mock(side_effect=AssertionError("automatic render forbidden"))

        from linkedin_connector.generated_reset import reset_generated_letters
        self.tasks.reconcile()
        self.store.save_job_materials("123456789", cover_letter_text="Previous generated letter")
        reset_generated_letters(self.store, self.store.database_path.parent / "reset-backup")
        self.tasks.queue_manual("123456789")
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        self.assertIsNotNone(task)
        self.coordinator._process(task, self.config.load())
        self.assertEqual(self.tasks.get("123456789")["state"], "READY")

        ordered_runs = [run for run in self.provider.agent_runs if run["job_id"] == "123456789"]
        self.assertEqual(
            [run["agent_id"] for run in ordered_runs],
            ["job-company-research", "cv-match-writer", "reviewer", "main-coordinator"],
        )
        self.assertEqual(self.provider.sequence, [
            "web_research", "job-company-research", "cv-match-writer", "reviewer", "main-coordinator",
        ])
        writer_run = next(run for run in ordered_runs if run["agent_id"] == "cv-match-writer")
        self.assertEqual(writer_run["knowledge"][0]["text"], example_text)
        self.assertEqual(writer_run["cv_text"], "Builds reports")
        self.assertNotIn("Builds reports", writer_run["knowledge"][0]["text"])
        self.assertEqual(writer_run["previous"]["company_analysis"], "Company for 123456789")
        self.assertEqual(writer_run["research"]["sources"][0]["url"], "https://example.com/company")
        review_run = next(run for run in ordered_runs if run["agent_id"] == "reviewer")
        self.assertEqual(review_run["previous"]["cover_letter_text"], "Dear team, I am applying for 123456789.")
        coordinator_run = next(run for run in ordered_runs if run["agent_id"] == "main-coordinator")
        self.assertEqual(coordinator_run["previous"]["personalization_notes"],
                         "Matched to Builds reports")
        self.assertEqual(self.provider.research_calls, 1)

        bundle = self.store.get_job_bundle("123456789")
        self.assertEqual(bundle["cover_letters"], [])
        self.assertIn("Dear team", bundle["materials"]["cover_letter_text"])

    def test_laya_precedes_agents_and_intermediate_decisions_reach_reviewer(self) -> None:
        from tests.test_local_decisions import FakeRouter
        router = FakeRouter()
        engine = self.coordinator.local_decisions
        engine.router_factory = lambda: router
        engine.save_settings({"enabled": True})
        self.coordinator.application_data.add_knowledge("style.txt", "txt", b"Style example", "Style example")
        connection = self.store._connection()
        try:
            connection.execute("UPDATE jobs SET description=? WHERE linkedin_job_id=?",
                               ("Build useful reports using Python dashboards to support business teams.", "123456789"))
            connection.commit()
        finally:
            connection.close()
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        self.coordinator._process(task, self.config.load())
        self.assertTrue(router.calls)
        self.assertEqual(self.provider.sequence, ["web_research", "cv-match-writer", "reviewer"])
        self.assertTrue(all(run["previous"]["local_decisions"]["state"] == "READY" for run in self.provider.agent_runs))
        reviewer = next(run for run in self.provider.agent_runs if run["agent_id"] == "reviewer")
        self.assertNotIn("draft_review", reviewer["previous"]["local_decisions"])
        writer = next(run for run in self.provider.agent_runs if run["agent_id"] == "cv-match-writer")
        self.assertEqual(writer["knowledge"][0]["filename"], "style.txt")
        self.assertEqual(writer["cv_text"], "Builds reports")
        bundle = self.store.get_job_bundle("123456789")
        self.assertEqual(len(bundle["cover_letters"]), 0)
        self.assertIn("Build useful reports", bundle["materials"]["role_analysis"])
        self.assertEqual(self.coordinator.local_decisions.get("123456789")["replaced_agent_stages"],
                         ["job-company-research", "main-coordinator"])
        connection = self.store._connection()
        try:
            usage = connection.execute("SELECT operation,COUNT(*) AS n FROM api_operation_usage WHERE linkedin_job_id=? GROUP BY operation",
                                       ("123456789",)).fetchall()
        finally:
            connection.close()
        self.assertEqual({row["operation"]: row["n"] for row in usage}, {"agent": 2, "company_research": 1})
        generation_stages = {(row["agent_id"], row["previous"]["cover_letter_text"]
                              if row["agent_id"] == "reviewer" else "")
                             for row in self.provider.agent_runs}
        self.assertIn(("reviewer", "Dear team, I am applying for 123456789."), generation_stages)

    def test_laya_ranks_local_knowledge_and_source_pages_and_reduces_writer_evidence_only(self) -> None:
        from tests.test_local_decisions import FakeRouter
        from linkedin_connector.research_pages import research_page_cache_key
        router = FakeRouter()
        engine = self.coordinator.local_decisions
        engine.router_factory = lambda: router
        engine.save_settings({"enabled": True})
        self.coordinator.application_data.add_knowledge(
            "cover-letter-example.txt", "txt", b"Example",
            "I am pleased to apply for this role. My experience aligns with the team's goals.")
        cv_text = "\n\n".join(
            ["Build useful reports with Python dashboards for cross-functional teams. " * 10]
            + [f"Provide administrative summaries for project group {index}. " * 10 for index in range(7)]
        )
        self.profile.save("cv.txt", "txt", cv_text.encode())
        page_text = "The company builds analytics products for financial services teams and supports data-driven operations."
        pages = [{"url": "https://example.com/company", "title": "Official company page", "state": "READY",
                  "error_code": "", "text": page_text}]
        with patch("linkedin_connector.research_pages.fetch_research_pages", return_value=pages):
            self.tasks.reconcile()
            with self.assertLogs("linkedin_connector", level="INFO") as captured:
                self.coordinator._process(self.tasks.claim_next(auto_enabled=True), self.config.load())

        timing_records = [record for record in captured.records
                          if getattr(record, "event", "") == "generation.stage_timing"]
        stages = {record.context["stage"] for record in timing_records}
        self.assertIn("company_research", stages)
        self.assertIn("context_prepare", stages)
        self.assertIn("final_validation", stages)
        completed = next(record for record in captured.records
                         if getattr(record, "event", "") == "generation.completed")
        self.assertGreaterEqual(completed.context["duration_ms"], 0)

        self.assertEqual(self.provider.sequence, ["web_research", "cv-match-writer", "reviewer"])
        self.assertEqual(len(router.batch_calls), 1)
        self.assertGreaterEqual(router.batch_calls[0]["count"], 3)
        writer = next(run for run in self.provider.agent_runs if run["agent_id"] == "cv-match-writer")
        reviewer = next(run for run in self.provider.agent_runs if run["agent_id"] == "reviewer")
        self.assertEqual(writer["knowledge"][0]["filename"], "cover-letter-example.txt")
        self.assertTrue(writer["candidate_evidence"])
        candidate_sources = {"cv": cv_text, "candidate_context": self.context.load()}
        self.assertTrue(all(item["text"] in candidate_sources[item["source"]]
                            for item in writer["candidate_evidence"]))
        self.assertLess(sum(len(item["text"]) for item in writer["candidate_evidence"]), len(cv_text))
        self.assertEqual(reviewer["cv_text"], cv_text.strip())
        self.assertEqual(reviewer["candidate_evidence"], [])
        self.assertEqual(writer["research"]["selected_source_excerpts"][0]["url"], pages[0]["url"])
        self.assertEqual(writer["research"]["selected_source_excerpts"][0]["text"], page_text)
        saved_pages = self.coordinator.application_data.get_research_pages(
            "123456789", research_page_cache_key(self.coordinator.application_data.get_research("123456789")))
        self.assertEqual(saved_pages, pages)
        summary = engine.get("123456789")["generation_selection"]
        self.assertTrue(summary["candidate_reduced"])
        self.assertEqual(summary["selected_examples"], ["cover-letter-example.txt"])
        self.assertEqual(summary["selected_sources"][0]["url"], pages[0]["url"])
        calls_after_first_generation = len(router.calls)
        cached = engine.prepare_generation_context(
            self.store.get_job("123456789"), cv_text.strip(), self.context.load(),
            self.coordinator.application_data.get_research("123456789"), pages)
        self.assertTrue(cached["summary"]["cached"])
        self.assertEqual(len(router.calls), calls_after_first_generation)

    def test_laya_context_failure_keeps_writer_reviewer_and_knowledge(self) -> None:
        from tests.test_local_decisions import FakeRouter
        router = FakeRouter()
        router.truncated = True
        self.coordinator.local_decisions.router_factory = lambda: router
        self.coordinator.local_decisions.save_settings({"enabled": True})
        self.coordinator.application_data.add_knowledge("style.txt", "txt", b"Style example", "Style example")
        self.tasks.reconcile()
        self.coordinator._process(self.tasks.claim_next(auto_enabled=True), self.config.load())
        # The deterministic preflight can still replace the decision-only research
        # and coordinator calls; writing and review remain API-backed.
        self.assertEqual([run["agent_id"] for run in self.provider.agent_runs],
                         ["cv-match-writer", "reviewer"])
        self.assertEqual(self.provider.agent_runs[0]["knowledge"][0]["filename"], "style.txt")
        writer_decisions = self.provider.agent_runs[0]["previous"]["local_decisions"]
        self.assertEqual(writer_decisions["state"], "READY")
        self.assertEqual(writer_decisions["knowledge_selection_method"], "deterministic_keyword_match")
        self.assertEqual(writer_decisions["generation_selection"]["state"], "UNAVAILABLE")
        self.assertEqual(len(self.store.get_job_bundle("123456789")["cover_letters"]), 0)

    def test_unknown_first_saved_date_is_not_assumed_fresh_or_expired(self) -> None:
        connection = self.store._connection()
        try:
            connection.execute("UPDATE jobs SET first_saved_at=? WHERE linkedin_job_id=?", ("unknown", "123456789"))
            connection.execute("UPDATE jobs SET status='IGNORED' WHERE linkedin_job_id=?", ("987654321",))
            connection.commit()
        finally:
            connection.close()
        self.tasks.reconcile()
        self.assertEqual(self.tasks.apply_auto_age_limit(90), {"skipped": 0, "released": 0, "unverified": 1})
        task = self.tasks.get("123456789")
        self.assertEqual(task["state"], "STALE")
        self.assertEqual(task["error_code"], "age_unverified")
        self.assertIn("Da verificare", task["stage"])
        self.assertEqual(self.provider.calls, [])

        corrected = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        connection = self.store._connection()
        try:
            connection.execute("UPDATE jobs SET first_saved_at=? WHERE linkedin_job_id=?", (corrected, "123456789"))
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.tasks.apply_auto_age_limit(90), {"skipped": 0, "released": 1, "unverified": 0})
        self.assertEqual(self.tasks.get("123456789")["state"], "PENDING")

    def test_recent_failed_company_research_is_reused_without_repeating_api_call(self) -> None:
        connection = self.store._connection()
        try:
            connection.execute("UPDATE jobs SET status='IGNORED' WHERE linkedin_job_id=?", ("987654321",))
            connection.commit()
        finally:
            connection.close()
        self.provider.fail_research_code = "network"
        self.coordinator.start()
        self.wait_for("123456789", "REVIEW")
        self.assertEqual(self.provider.research_calls, 1)
        self.assertEqual(self.coordinator.application_data.get_research("123456789")["error_code"], "network")
        bundle = self.store.get_job_bundle("123456789")
        self.assertTrue(bundle["materials"]["cover_letter_text"])
        self.assertEqual(bundle["cover_letters"], [])

        self.coordinator.request_one("123456789")
        self.wait_for("123456789", "REVIEW")
        self.assertEqual(self.provider.research_calls, 1)
        self.assertEqual(len(self.provider.calls), 7)
        self.assertEqual(
            self.tasks.summary()["usage"]["today_by_operation"],
            {"agent": 7, "company_research": 1},
        )

    def test_research_errors_fixed_by_settings_are_not_negative_cached(self) -> None:
        from linkedin_connector.generation import _research_from_record

        record = {
            "job_hash": "same-job", "state": "UNAVAILABLE",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        for error_code in ("authentication", "model_unavailable", "research_limit", "research_disabled"):
            with self.subTest(error_code=error_code):
                cached, hit = _research_from_record(
                    {**record, "error_code": error_code}, "same-job", enabled=True)
                self.assertIsNone(cached)
                self.assertFalse(hit)

    def test_internal_failure_logs_safe_stack_frames_without_exception_text(self) -> None:
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        self.assertIsNotNone(task)
        secret = "sk-sensitive-marker-must-not-be-logged"
        self.provider.fail_unexpected = secret

        with self.assertLogs("linkedin_connector", level="ERROR") as captured:
            self.coordinator._process(task, self.config.load())

        failure = next(record for record in captured.records if getattr(record, "event", "") == "generation.failed")
        self.assertEqual(failure.details["exception_type"], "RuntimeError")
        self.assertIn("run_agent", failure.details["traceback_frames"])
        self.assertNotIn(secret, failure.details["traceback_frames"])
        self.assertEqual(len(failure.context["generation_id"]), 16)
        self.assertEqual(failure.context["outcome"], "failed")
        self.assertGreaterEqual(failure.context["duration_ms"], 0)
        self.assertEqual(self.tasks.get(task["linkedin_job_id"])["error_code"], "internal_error")

    def test_company_research_failure_still_records_phase_duration(self) -> None:
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        with patch.object(self.coordinator.application_data, "save_research",
                          side_effect=RuntimeError("synthetic research persistence failure")):
            with self.assertLogs("linkedin_connector", level="INFO") as captured:
                self.coordinator._process(task, self.config.load())

        research = next(record for record in captured.records
                        if getattr(record, "event", "") == "generation.stage_timing"
                        and record.context.get("stage") == "company_research")
        self.assertEqual(research.context["outcome"], "failed")
        self.assertGreaterEqual(research.context["duration_ms"], 0)

    def test_text_save_failure_still_records_phase_duration(self) -> None:
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        with patch.object(self.tasks, "commit_result",
                          side_effect=RuntimeError("synthetic text persistence failure")):
            with self.assertLogs("linkedin_connector", level="INFO") as captured:
                self.coordinator._process(task, self.config.load())

        saving = next(record for record in captured.records
                      if getattr(record, "event", "") == "generation.stage_timing"
                      and record.context.get("stage") == "text_save")
        self.assertEqual(saving.context["outcome"], "failed")
        self.assertGreaterEqual(saving.context["duration_ms"], 0)

    def test_candidate_input_failure_still_records_phase_duration(self) -> None:
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        with patch.object(self.coordinator.candidate_profile, "read_text",
                          side_effect=RuntimeError("synthetic CV read failure")):
            with self.assertLogs("linkedin_connector", level="INFO") as captured:
                self.coordinator._process(task, self.config.load())

        input_read = next(record for record in captured.records
                          if getattr(record, "event", "") == "generation.stage_timing"
                          and record.context.get("stage") == "input_read")
        self.assertEqual(input_read.context["outcome"], "failed")
        self.assertGreaterEqual(input_read.context["duration_ms"], 0)

    def test_context_preparation_failure_still_records_phase_duration(self) -> None:
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        with patch.object(self.coordinator.local_decisions, "prepare_generation_context",
                          side_effect=RuntimeError("synthetic context preparation failure")):
            with self.assertLogs("linkedin_connector", level="INFO") as captured:
                self.coordinator._process(task, self.config.load())

        context = next(record for record in captured.records
                       if getattr(record, "event", "") == "generation.stage_timing"
                       and record.context.get("stage") == "context_prepare")
        self.assertEqual(context.context["outcome"], "failed")
        self.assertGreaterEqual(context.context["duration_ms"], 0)

    def test_usage_daily_filters_use_the_new_created_operation_index(self) -> None:
        connection = self.store._connection()
        try:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT operation FROM api_operation_usage "
                "WHERE created_at>=? AND created_at<? GROUP BY operation",
                ("2026-01-01T00:00:00", "2026-01-02T00:00:00"),
            ).fetchall()
        finally:
            connection.close()
        self.assertIn(
            "idx_api_operation_usage_created_operation",
            " ".join(str(row[3]) for row in plan),
        )

    def test_usage_tracks_cached_tokens_and_measured_api_duration(self) -> None:
        reservation = self.tasks.reserve_api_call(
            "123456789", "gpt-4.1-nano", "agent", daily_limit=100, stage="cv-match-writer",
        )
        self.assertIsNotNone(reservation)
        assert reservation is not None
        self.tasks.finish_api_call(reservation, {
            "input_tokens": 120, "cached_input_tokens": 30, "output_tokens": 18,
            "search_calls": 0, "duration_ms": 1_900,
        })
        usage = self.tasks.summary()["usage"]
        self.assertEqual(usage["cached_input_tokens"], 30)
        self.assertEqual(usage["duration_ms"], 1_900)
        profile = self.tasks.usage_profile()
        agent_row = next(row for row in profile if row["model"] == "gpt-4.1-nano")
        self.assertEqual(agent_row["stage"], "cv-match-writer")
        self.assertEqual(agent_row["avg_cached_input_tokens"], 30)
        self.assertEqual(agent_row["avg_duration_ms"], 1_900)
        self.assertNotIn("cv_text", agent_row)
        self.assertNotIn("prompt", agent_row)
        connection = self.store._connection()
        try:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(api_operation_usage)")}
        finally:
            connection.close()
        self.assertFalse(columns & {"prompt", "input_text", "result_text", "cv_text", "letter_text"})

    def test_auto_queue_keeps_existing_letters_until_regeneration_is_requested(self) -> None:
        self.coordinator.start()
        self.wait_for("123456789", "READY")
        self.wait_for("987654321", "READY")
        initial_calls = len(self.provider.calls)
        self.assertEqual(initial_calls, 6)
        self.assertEqual(self.tasks.summary()["usage"], {
            "calls": 7, "input_tokens": 300, "output_tokens": 120, "search_calls": 1,
            "cached_input_tokens": 0, "duration_ms": 0,
            "calls_today": 7, "today_by_operation": {"agent": 6, "company_research": 1},
        })
        first = self.store.get_job_bundle("123456789")
        other = self.store.get_job_bundle("987654321")
        self.assertEqual(len(first["cover_letters"]), 0)
        self.assertEqual(len(other["cover_letters"]), 0)

        self.store.sync_jobs([make_job("123456789"), make_job("987654321")])
        self.assertEqual(self.tasks.reconcile(), 0)
        self.coordinator.wake()
        time.sleep(0.1)
        self.assertEqual(len(self.provider.calls), initial_calls)

        self.store.sync_jobs([make_job("123456789", description="Changed duties")])
        self.assertEqual(self.tasks.reconcile(), 0)
        self.assertEqual(self.tasks.get("123456789")["state"], "MANUAL")
        self.coordinator.wake()
        time.sleep(0.1)
        self.assertEqual(len(self.provider.calls), initial_calls)
        self.assertEqual(len(self.store.get_job_bundle("123456789")["cover_letters"]), 0)

        self.coordinator.request_one("123456789")
        until = time.monotonic() + 4
        while time.monotonic() < until and len(self.provider.calls) < initial_calls + 2:
            time.sleep(0.02)
        self.wait_for("123456789", "READY")
        self.assertEqual(len(self.provider.calls), initial_calls + 2)
        self.assertEqual(len(self.store.get_job_bundle("987654321")["cover_letters"]), 0)

        before = len(self.provider.calls)
        self.coordinator.request_one("987654321")
        self.wait_for("987654321", "READY")
        self.assertEqual(len(self.provider.calls), before + 2)
        self.assertEqual(len(self.store.get_job_bundle("123456789")["cover_letters"]), 0)

    def test_daily_call_limit_keeps_jobs_queued_and_resumes_when_limit_changes(self) -> None:
        settings = self.config.load()
        settings["max_daily_calls"] = 1
        settings["max_daily_agent_calls"] = 20
        self.config.save(settings)
        reservation = self.tasks.reserve_api_call(
            "123456789", "test-model", "chat", daily_limit=10, category_limit=10,
        )
        self.assertIsNotNone(reservation)
        self.tasks.finish_api_call(reservation, {"input_tokens": 0, "output_tokens": 0})

        self.coordinator.start()
        until = time.monotonic() + 3
        while time.monotonic() < until:
            task = self.tasks.get("123456789")
            if task and "Limite AI" in task["stage"]:
                break
            time.sleep(0.02)
        waiting = self.tasks.get("123456789")
        self.assertEqual(waiting["state"], "PENDING")
        self.assertIn("ripresa automatica", waiting["stage"])
        self.assertEqual(self.provider.calls, [])

        settings["max_daily_calls"] = 20
        self.config.save(settings)
        self.coordinator.wake()
        self.wait_for("123456789", "READY")
        self.wait_for("987654321", "READY")
        self.assertEqual(len(self.provider.calls), 6)

    def test_daily_agent_category_limit_is_detected_separately(self) -> None:
        reservation = self.tasks.reserve_api_call(
            "123456789", "test-model", "agent", daily_limit=20, category_limit=20,
        )
        self.assertIsNotNone(reservation)
        self.tasks.finish_api_call(reservation, {"input_tokens": 0, "output_tokens": 0})
        settings = self.config.load()
        settings["max_daily_calls"] = 20
        settings["max_daily_agent_calls"] = 1

        self.assertEqual(self.tasks.daily_limit_status(settings), {
            "name": "max_daily_agent_calls", "used": 1, "limit": 1,
        })

    def test_startup_requeues_jobs_blocked_by_a_previous_daily_limit(self) -> None:
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        self.assertIsNotNone(task)
        self.tasks.fail(task["linkedin_job_id"], task["job_hash"], "usage_limit", "a1b2c3d4e5f60718")
        self.assertEqual(self.tasks.get(task["linkedin_job_id"])["state"], "ERROR")

        self.assertEqual(self.tasks.reconcile(), 1)
        recovered = self.tasks.get(task["linkedin_job_id"])
        self.assertEqual(recovered["state"], "PENDING")
        self.assertEqual(recovered["error_code"], "usage_limit")
        self.assertEqual(recovered["error_request_id"], "a1b2c3d4e5f60718")
        self.assertIn("ripresa automatica", recovered["stage"])

    def test_reconcile_does_not_requeue_a_job_with_an_existing_letter(self) -> None:
        self.tasks.reconcile()
        self.store.save_job_materials("123456789", cover_letter_text="Saved cover letter")
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE generation_tasks SET state='ERROR',error_code='usage_limit' "
                "WHERE linkedin_job_id='123456789'"
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.tasks.reconcile(), 0)
        self.assertEqual(self.tasks.get("123456789")["state"], "ERROR")
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE generation_tasks SET state='PENDING',manual_request=0 "
                "WHERE linkedin_job_id='123456789'"
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.tasks.reconcile(), 0)
        self.assertEqual(self.tasks.get("123456789")["state"], "MANUAL")

    def test_old_daily_limit_error_is_skipped_before_any_provider_retry(self) -> None:
        old_saved_at = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE jobs SET first_saved_at=? WHERE linkedin_job_id=?",
                (old_saved_at, "123456789"),
            )
            connection.execute("UPDATE jobs SET status='IGNORED' WHERE linkedin_job_id=?", ("987654321",))
            connection.commit()
        finally:
            connection.close()
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        self.assertIsNotNone(task)
        self.tasks.fail(task["linkedin_job_id"], task["job_hash"], "usage_limit", "1234567890abcdef")

        self.coordinator.start()
        self.wait_for("123456789", "STALE")
        recovered = self.tasks.get("123456789")
        self.assertEqual(recovered["error_code"], "auto_age_limit")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.provider.research_calls, 0)

    def test_no_provider_call_before_explicit_consent(self) -> None:
        settings = self.config.load()
        settings["consent"] = False
        self.config.save(settings)
        self.coordinator.start()
        time.sleep(0.1)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.tasks.get("123456789")["state"], "PENDING")
        settings["consent"] = True
        self.config.save(settings)
        self.coordinator.wake()
        self.wait_for("123456789", "READY")

    def test_failure_preserves_previous_materials_and_retry_recovers(self) -> None:
        self.coordinator.start()
        self.wait_for("123456789", "READY")
        previous = self.store.get_job_bundle("123456789")
        self.provider.fail_job = "123456789"
        self.coordinator.request_one("123456789")
        self.wait_for("123456789", "ERROR")
        after = self.store.get_job_bundle("123456789")
        self.assertEqual(after["materials"], previous["materials"])
        self.assertEqual(after["cover_letters"], previous["cover_letters"])
        self.assertEqual(self.tasks.get("123456789")["error_code"], "rate_limit")
        self.provider.fail_job = None
        self.coordinator.request_one("123456789")
        self.wait_for("123456789", "READY")
        self.assertEqual(len(self.store.get_job_bundle("123456789")["cover_letters"]), 0)

    def test_running_job_recovers_on_restart_and_duplicate_claim_is_blocked(self) -> None:
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        self.assertIsNotNone(task)
        with self.assertRaises(JobStoreError):
            self.tasks.queue_manual(task["linkedin_job_id"])
        restarted = GenerationStore(self.store)
        self.assertEqual(restarted.get(task["linkedin_job_id"])["state"], "PENDING")

    def test_revoking_consent_stops_following_agent_calls_and_keeps_job_pending(self) -> None:
        self.tasks.reconcile()
        task = self.tasks.claim_next(auto_enabled=True)
        self.assertIsNotNone(task)

        class RevokingProvider(FakeProvider):
            def run_agent(inner, **kwargs):
                result = super().run_agent(**kwargs)
                settings = self.config.load()
                settings["consent"] = False
                self.config.save(settings)
                return result

        provider = RevokingProvider()
        self.coordinator.provider_factory = lambda key, model: provider
        self.coordinator._process(task, self.config.load())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.tasks.get(task["linkedin_job_id"])["state"], "PENDING")
        self.assertIsNone(self.store.get_job_bundle(task["linkedin_job_id"])["materials"])

    def test_generated_pdf_and_docx_use_the_same_source_text(self) -> None:
        from docx import Document
        from pypdf import PdfReader

        text = "Dear team,\n\nI build reports for your role.\n\nSincerely,\nCandidate"
        pdf, docx = render_letter_documents(text)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(docx.startswith(b"PK"))
        docx_lines = [paragraph.text for paragraph in Document(BytesIO(docx)).paragraphs]
        self.assertEqual(docx_lines, text.split("\n"))
        extracted = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        self.assertIn("I build reports for your role.", extracted)

    def test_provider_uses_structured_response_and_does_not_request_storage(self) -> None:
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self, _):
                return json.dumps({"status": "completed", "usage": {"input_tokens": 25, "output_tokens": 10,
                    "input_tokens_details": {"cached_tokens": 4}}, "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": json.dumps({
                            "personalization_notes": "Match", "cover_letter_text": "Dear team",
                            "gaps": "", "needs_review": False,
                        })}]}]}).encode()

        def transport(request, timeout):
            calls.append((request, timeout))
            return Response()

        provider = OpenAIProvider("sk-test-key-for-local-tests", "gpt-4o-mini", transport=transport)
        example = {"filename": "style-example.txt", "text": "Reusable opening and paragraph style."}
        output = provider.run_agent(
            agent=TEST_AGENT, global_instructions="Rules", job={"title": "Role"},
            cv_text="Private CV marker", candidate_context="Private context marker",
            previous={"company_analysis": "Company", "role_analysis": "Role",
                      "personalization_notes": "", "cover_letter_text": "", "gaps": "",
                      "needs_review": False, "local_decisions": {"state": "READY", "role": "software-data-ai", "evidence": [{"text": "Private CV marker", "source": "cv"}]}},
            knowledge=[example],
        )
        self.assertEqual(output["cover_letter_text"], "Dear team")
        self.assertEqual(provider.last_usage["input_tokens"], 25)
        self.assertEqual(provider.last_usage["output_tokens"], 10)
        self.assertEqual(provider.last_usage["cached_input_tokens"], 4)
        self.assertEqual(provider.last_usage["search_calls"], 0)
        self.assertGreaterEqual(provider.last_usage["duration_ms"], 0)
        request = calls[0][0]
        body = json.loads(request.data)
        self.assertFalse(body["store"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertEqual(body["max_output_tokens"], 2_000)
        self.assertEqual(body["text"]["format"]["schema"]["required"],
                         ["personalization_notes", "cover_letter_text", "gaps", "needs_review"])
        self.assertTrue(body["prompt_cache_key"].startswith("job-application-"))
        reference = json.loads(body["input"][0]["content"][0]["text"])
        self.assertEqual(reference["cover_letter_style_examples"], [example])
        self.assertEqual(reference["local_decisions_advisory"]["role"], "software-data-ai")
        self.assertNotIn("evidence", reference["local_decisions_advisory"])
        self.assertNotIn("candidate_excerpt", reference["local_decisions_advisory"])
        self.assertEqual(reference["candidate_cv_text"], "Private CV marker")
        self.assertIn("Scores do not prove qualification", body["instructions"])
        self.assertIn("non copiare", body["instructions"].casefold())
        self.assertIn("uniche fonti dei fatti sul candidato", body["instructions"])
        self.assertIn("esclusivamente in inglese", body["instructions"])
        self.assertNotIn("Private CV marker", request.headers["Authorization"])

        compact_evidence = [{"source": "cv", "offset": 120, "text": "Built Python dashboards.", "score": 91.0}]
        provider.run_agent(
            agent=TEST_AGENT, global_instructions="Rules", job={"title": "Role"},
            cv_text="Private CV marker", candidate_context="Private context marker",
            previous={"company_analysis": "Company", "role_analysis": "Role",
                      "personalization_notes": "", "cover_letter_text": "", "gaps": "",
                      "needs_review": False},
            knowledge=[example], candidate_evidence=compact_evidence,
            research={"summary": "Company", "sources": [{"url": "https://example.com/about", "title": "About"}],
                      "selected_source_excerpts": [{"url": "https://example.com/about", "title": "About",
                                                   "text": "The company builds analytics products."}]},
        )
        compact_request = calls[1][0]
        compact_reference = json.loads(json.loads(compact_request.data)["input"][0]["content"][0]["text"])
        self.assertNotIn("candidate_cv_text", compact_reference)
        self.assertNotIn("candidate_context", compact_reference)
        self.assertNotIn("Private CV marker", compact_request.data.decode())
        self.assertEqual(compact_reference["candidate_evidence_excerpts"][0]["text"], "Built Python dashboards.")
        self.assertEqual(compact_reference["company_research"]["selected_source_excerpts"][0]["url"],
                         "https://example.com/about")

    def test_reviewer_and_coordinator_stage_results_preserve_letter_and_flag_issues(self) -> None:
        previous = {"company_analysis": "Company", "role_analysis": "Role",
                    "personalization_notes": "Evidence match", "cover_letter_text": "English draft",
                    "gaps": "", "needs_review": False}
        reviewed = merge_stage_output(previous, {
            "revised_letter": "", "review_notes": "Check the role title against the posting.",
            "needs_review": True,
        }, "reviewer")
        self.assertEqual(reviewed["cover_letter_text"], "English draft")
        self.assertIn("Check the role title", reviewed["gaps"])
        self.assertTrue(reviewed["needs_review"])
        coordinated = merge_stage_output(reviewed, {
            "ready_to_deliver": False, "coordination_notes": "Manual review is required.",
            "needs_review": False,
        }, "coordinator")
        self.assertEqual(coordinated["cover_letter_text"], "English draft")
        self.assertIn("Manual review is required", coordinated["gaps"])
        self.assertTrue(coordinated["needs_review"])

    def test_company_research_requires_real_search_caps_tools_and_keeps_only_cited_claim_sources(self) -> None:
        calls = []
        source_url = "https://example.com/company/news"

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self, _):
                return json.dumps({
                    "status": "completed",
                    "usage": {"input_tokens": 30, "output_tokens": 20},
                    "output": [
                        {"type": "web_search_call", "action": {"sources": [
                            {"url": source_url, "title": "Company newsroom"},
                        ]}},
                        {"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                            "summary": "A researched summary.",
                            "facts": [
                                {"claim": "Company announced a new program.", "kind": "documented_fact",
                                 "source_urls": [source_url, "https://fabricated.example/claim"]},
                                {"claim": "The program may be relevant to the role.", "kind": "interpretation",
                                 "source_urls": [source_url]},
                            ],
                            "gaps": "No office-specific details found.",
                        })}]},
                    ],
                }).encode()

        def transport(request, timeout):
            calls.append((request, timeout))
            return Response()

        provider = OpenAIProvider("sk-test-key-for-local-tests", "gpt-4o-mini", transport=transport)
        result = provider.research_company(job={"company": "Example", "title": "Role"}, global_instructions="Prioritize official sources.")
        body = json.loads(calls[0][0].data)
        self.assertEqual(body["model"], "gpt-4.1-mini")
        self.assertEqual(body["tool_choice"], "required")
        self.assertEqual(body["max_tool_calls"], 1)
        self.assertEqual(body["tools"][0]["search_context_size"], "low")
        self.assertLess(body["max_output_tokens"], 2_000)
        self.assertNotIn("Prioritize official sources.", body["instructions"])
        self.assertNotIn("Role", body["instructions"])
        self.assertEqual(
            json.loads(body["input"][0]["content"][0]["text"]),
            {"company": "Example", "location": None},
        )
        self.assertFalse(body["store"])
        self.assertEqual(result["facts"][0]["source_urls"], [source_url])
        self.assertEqual(result["sources"][0]["title"], "Company newsroom")
        self.assertEqual(result["search_calls"], 1)
