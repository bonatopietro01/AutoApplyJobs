"""HTTP and upload-boundary tests for the local dashboard."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from linkedin_connector.dashboard import DashboardHTTPServer, SyncCoordinator
from linkedin_connector.database import JobStore
from linkedin_connector.reader import LinkedInReadError
from linkedin_connector.ai_config import default_settings
from tests.test_automation import FakeCredentialBackend, FakeProvider
from tests.test_database import make_job


TEST_AGENTS_MARKDOWN = ("# Test agents\n\nOne unified workflow.\n\n"
                        "## Internal roles and workflow\n\n### 1. Writer\n"
                        "<!-- agent:id=writer phase=writing enabled=true -->\n\n"
                        "Build useful reports and write an evidence-based letter.\n")


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.temp_dir.name) / "data" / "jobs.sqlite3")
        self.store.sync_jobs([make_job()])
        self.profile_directory = Path(self.temp_dir.name) / "user_profile"
        self.profile_directory.mkdir()
        (self.profile_directory / "AGENTS.md").write_text(
            TEST_AGENTS_MARKDOWN, encoding="utf-8"
        )
        (self.profile_directory / "CANDIDATE_CONTEXT.md").write_text(
            "# Candidate notes\n\ncontext-marker-83c1\n", encoding="utf-8"
        )
        self.server = DashboardHTTPServer(
            ("127.0.0.1", 0), self.store, profile_directory=self.profile_directory
        )
        # Keep ordinary route tests deterministic; Laya behavior is covered with
        # an explicit fake router in its dedicated tests.
        self.server.generation.local_decisions.save_settings({"enabled": False})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp_dir.cleanup()

    def request(self, path: str, *, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None):
        request_headers = dict(headers or {})
        request = Request(
            f"{self.base_url}{path}", data=body, headers=request_headers, method=method
        )
        with urlopen(request, timeout=3) as response:
            return response.status, response.headers, response.read()

    def test_dashboard_lists_jobs_and_renders_local_page(self) -> None:
        status, headers, body = self.request("/api/jobs")
        self.assertEqual(status, 200)
        self.assertRegex(headers.get("X-Request-ID", ""), re.compile(r"^[a-f0-9]{16}$"))
        payload = json.loads(body)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["jobs"][0]["linkedin_job_id"], "123456789")

        _, headers, page = self.request("/")
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn(b"Annunci salvati", page)
        self.assertIn(b"CANDIDATE_CONTEXT.md", page)
        self.assertIn(b"id=\"cv-file\"", page)
        self.assertNotIn(b"prepare-agent-prompt", page)
        self.assertIn(b"regenerate-job", page)
        self.assertIn(b"ai-api-key", page)
        self.assertIn(b'id="ai-max-auto-job-age-days"', page)
        self.assertNotIn(b"Disponibilit\xc3\xa0 LinkedIn", page)
        self.assertIn(b'id="recover-saved-jobs"', page)
        self.assertNotIn(b"saved-recovery-period", page)
        self.assertNotIn(b"stale-generation-note", page)
        self.assertIn(b'id="bulk-regeneration-mode"', page)
        self.assertIn(b'id="add-manual-job"', page)
        self.assertIn(b'id="manual-job-description"', page)
        self.assertIn(b'id="mark-application-sent"', page)
        self.assertIn(b'id="cover-letter-information"', page)
        cover_letter_information = page.split(b'id="cover-letter-information"', 1)[0].rsplit(b"<", 1)[-1]
        self.assertEqual(cover_letter_information, b'details class="content-card cover-letter-information" ')
        self.assertNotIn(b'cover-letter-information" open', page)
        self.assertIn(b'<summary class="cover-letter-information-summary">', page)
        self.assertEqual(page.count(b'class="section-disclosure-summary"'), 3)
        detail_actions = page.split(b'id="detail-actions"', 1)[1].split(b"</div>\n          </div>", 1)[0]
        self.assertIn(b'id="mark-application-sent"', detail_actions)
        self.assertNotIn(b'id="mark-application-sent"', page.split(b'id="application-tracker"', 1)[1].split(b"</div>", 1)[0])
        app_status, _, app_script = self.request("/app.js")
        self.assertEqual(app_status, 200)
        self.assertIn(b"bulkOperations.after(aiSettings)", app_script)
        self.assertIn(b"accordion.open = false", app_script)
        self.assertIn(b"function jobDocumentBaseName(job)", app_script)

    def test_manual_application_status_route_marks_only_application_state(self) -> None:
        job, _ = self.store.create_chatgpt_job(
            title="Analyst", company="Example", location="Zurich",
            description="Build reports and improve data quality across regional teams.",
            application_url="https://jobs.example.org/roles/1",
            cover_letter_text="Dear team, I am applying.",
        )
        status, _, body = self.request(
            f"/api/jobs/{job['linkedin_job_id']}/application-status", method="POST",
            body=b'{"status":"APPLIED"}',
            headers={"Origin": self.base_url, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        updated = json.loads(body)["job"]
        self.assertEqual(updated["application_status"], "APPLIED")
        self.assertEqual(updated["status"], "NEW")

    def test_laya_endpoint_runs_offline_with_no_openai_key(self) -> None:
        from tests.test_local_decisions import FakeRouter
        self.server.candidate_profile.save("cv.txt", "txt", b"Builds Python reports.")
        engine = self.server.generation.local_decisions
        engine.router_factory = FakeRouter
        status, _, body = self.request("/api/laya/settings")
        self.assertFalse(json.loads(body)["settings"]["enabled"])
        self.request("/api/laya/settings", method="POST", body=b'{"enabled":true}', headers={"Content-Type": "application/json", "Origin": self.base_url})
        with patch("linkedin_connector.dashboard.OpenAIProvider", side_effect=AssertionError("No paid provider")), patch.object(self.server.sync_coordinator, "start", side_effect=AssertionError("No LinkedIn scan")):
            status, _, body = self.request("/api/jobs/123456789/local-decisions", method="POST", body=b"", headers={"Origin": self.base_url})
            self.assertEqual(status, 202)
            until = time.monotonic() + 3
            while time.monotonic() < until:
                _, _, body = self.request("/api/jobs/123456789")
                result = json.loads(body)["local_decisions"]
                if result and result["state"] != "RUNNING":
                    break
                time.sleep(.02)
            self.assertEqual(result["state"], "READY")
        self.assertEqual(self.server.store.get_job_bundle("123456789")["cover_letters"], [])

        _, script_headers, script = self.request("/app.js")
        self.assertEqual(script_headers.get("Cache-Control"), "no-store")
        self.assertIn(
            b'byId("upload-knowledge").addEventListener("click", () => uploadKnowledge());',
            script,
        )
        self.assertNotIn(
            b'byId("upload-knowledge").addEventListener("click", uploadKnowledge);',
            script,
        )
        self.assertIn(b'request("/api/jobs/manual"', script)
        self.assertIn(b'job.source === "manual"', script)
        self.assertIn(b'job.application_url || job.linkedin_url', script)
        self.assertIn(b'/application-status', script)

    def test_manual_job_endpoint_saves_and_lists_offline_without_starting_services(self) -> None:
        description = (
            "Operations Analyst\nCoordinate reporting workflows, maintain data quality, "
            "and share insights with cross-functional teams."
        )
        payload = {"description": description, "company": "Example Org", "location": "Milan"}
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        calls_before = self.server.generation_tasks.summary()["usage"]["calls"]

        with (
            patch("linkedin_connector.dashboard.OpenAIProvider") as openai,
            patch.object(self.server.sync_coordinator, "start") as sync_start,
            patch.object(self.server.generation, "wake") as generation_wake,
        ):
            status, _, body = self.request(
                "/api/jobs/manual", method="POST", body=json.dumps(payload).encode(), headers=headers
            )
            created = json.loads(body)
            self.assertEqual(status, 201)
            self.assertTrue(created["created"])
            manual_job = created["job"]
            self.assertTrue(manual_job["job_id"].startswith("manual_"))
            self.assertIsNone(manual_job["linkedin_job_id"])
            self.assertEqual(manual_job["linkedin_url"], "")
            self.assertNotIn("normalized_url", manual_job)
            self.assertEqual(manual_job["source"], "manual")

            _, _, list_body = self.request("/api/jobs")
            listed = next(job for job in json.loads(list_body)["jobs"] if job["source"] == "manual")
            self.assertEqual(listed["job_id"], manual_job["job_id"])
            self.assertIsNone(listed["linkedin_job_id"])

            _, _, detail_body = self.request(f"/api/jobs/{manual_job['job_id']}")
            detail = json.loads(detail_body)["job"]
            self.assertEqual(detail["source"], "manual")
            self.assertIsNone(detail["linkedin_job_id"])
            self.assertNotIn("normalized_url", detail)
            openai.assert_not_called()
            sync_start.assert_not_called()
            generation_wake.assert_not_called()

        self.assertEqual(self.server.generation_tasks.summary()["usage"]["calls"], calls_before)
        self.assertIsNone(self.server.generation_tasks.get(manual_job["job_id"]))

        _, _, duplicate_body = self.request(
            "/api/jobs/manual", method="POST",
            body=json.dumps({"description": "  ".join(description.split()).swapcase()}).encode(),
            headers=headers,
        )
        duplicate = json.loads(duplicate_body)
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["job"]["job_id"], manual_job["job_id"])

    def test_manual_job_endpoint_reports_invalid_text_without_saving(self) -> None:
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        initial_count = len(self.store.get_dashboard_jobs())
        for description in ("", "too short"):
            with self.subTest(description=description), self.assertRaises(HTTPError) as error:
                self.request("/api/jobs/manual", method="POST",
                    body=json.dumps({"description": description}).encode(), headers=headers)
            self.assertEqual(error.exception.code, 400)
            self.assertIn("testo", json.loads(error.exception.read())["error"])
        self.assertEqual(len(self.store.get_dashboard_jobs()), initial_count)

    def test_recovery_rejects_old_period_payload_and_logs_the_current_route(self) -> None:
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        with patch.object(self.server.sync_coordinator, "start") as start:
            with self.assertRaises(HTTPError) as error:
                self.request("/api/sync/recover", method="POST",
                    body=json.dumps({"probe": True}).encode(), headers=headers)
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(json.loads(error.exception.read())["error"],
                "La richiesta di scansione deve essere un oggetto JSON vuoto.")
            start.assert_not_called()

        request_id = error.exception.headers["X-Request-ID"]
        _, _, log_body = self.request(f"/api/diagnostics/logs?limit=20&q={request_id}")
        matching = json.loads(log_body)["events"]
        completion = next(event for event in matching if event["event"] == "http.request.completed")
        self.assertEqual(completion["context"]["route"], "sync.recover")
        self.assertEqual(completion["context"]["status"], 400)

    def test_forget_job_endpoint_is_protected_and_keeps_cover_letter_files(self) -> None:
        self.store.save_job_materials("123456789", cover_letter_text="A saved letter.")
        saved = self.store.save_cover_letter_file(
            "123456789", original_filename="letter.pdf", file_format="pdf", contents=b"%PDF-test"
        )
        wrong_origin = {"Origin": "http://attacker.example"}
        with self.assertRaises(HTTPError) as error:
            self.request("/api/jobs/123456789/memory", method="DELETE", headers=wrong_origin)
        self.assertEqual(error.exception.code, 403)

        _, _, body = self.request(
            "/api/jobs/123456789/memory", method="DELETE", headers={"Origin": self.base_url}
        )
        self.assertEqual(json.loads(body), {
            "deleted": True, "files_retained": True, "reimport_suppressed": True,
        })
        self.assertIsNone(self.store.get_job("123456789"))
        self.assertTrue((self.store.database_path.parent / saved["relative_path"]).is_file())
        self.assertEqual(self.store.sync_jobs([make_job()]), {"inserted": 0, "refreshed": 0, "total": 0})

    def test_forget_job_endpoint_refuses_a_running_generation(self) -> None:
        self.server.generation_tasks.queue_manual("123456789")
        connection = self.store._connection()
        try:
            connection.execute("UPDATE generation_tasks SET state='RUNNING' WHERE linkedin_job_id='123456789'")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/jobs/123456789/memory", method="DELETE", headers={"Origin": self.base_url}
            )
        self.assertEqual(error.exception.code, 409)
        self.assertIsNotNone(self.store.get_job("123456789"))

    def test_ai_settings_key_and_single_job_regeneration_are_local_and_protected(self) -> None:
        secret = "sk-local-test-key-secret-5312"
        self.server.api_keys._backend = FakeCredentialBackend()
        provider = FakeProvider()
        self.server.generation.provider_factory = lambda key, model: provider
        self.server.candidate_profile.save("cv.txt", "txt", b"private-cv-marker-2195")
        origin = {"Origin": self.base_url, "Content-Type": "application/json"}

        _, _, initial = self.request("/api/ai/settings")
        self.assertFalse(json.loads(initial)["key_configured"])
        self.assertEqual(len(json.loads(initial)["agents"]), 1)
        self.assertNotIn("agents", json.loads(initial)["settings"])
        with self.assertRaises(HTTPError) as error:
            self.request("/api/ai/key", method="POST", body=json.dumps({"api_key": secret}).encode(),
                         headers={"Origin": "http://attacker.example", "Content-Type": "application/json"})
        self.assertEqual(error.exception.code, 403)

        self.request("/api/ai/key", method="POST", body=json.dumps({"api_key": secret}).encode(), headers=origin)
        settings = default_settings()
        settings["consent"] = True
        settings["enabled"] = False
        settings["web_research_enabled"] = True
        settings["max_daily_agent_calls"] = 7
        self.request("/api/ai/settings", method="POST", body=json.dumps(settings).encode(), headers=origin)
        _, _, status_body = self.request("/api/ai/settings")
        exposed = json.loads(status_body)
        self.assertTrue(exposed["key_configured"])
        self.assertEqual(exposed["settings"]["max_daily_agent_calls"], 7)
        self.assertEqual(exposed["settings"]["max_auto_job_age_days"], 90)
        self.assertNotIn(secret, status_body.decode())
        self.assertIn("automazione_disattivata", exposed["status"]["missing"])

        with patch("linkedin_connector.dashboard.OpenAIProvider") as client:
            client.return_value.verify.return_value = None
            _, _, verified = self.request("/api/ai/key/verify", method="POST", headers={"Origin": self.base_url})
            self.assertTrue(json.loads(verified)["verified"])

        _, _, queued = self.request("/api/jobs/123456789/regenerate", method="POST",
                                     headers={"Origin": self.base_url})
        self.assertEqual(json.loads(queued)["generation"]["linkedin_job_id"], "123456789")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and self.server.generation_tasks.get("123456789")["state"] != "READY":
            time.sleep(0.02)
        self.assertEqual(self.server.generation_tasks.get("123456789")["state"], "READY")
        _, _, detail = self.request("/api/jobs/123456789")
        self.assertEqual(json.loads(detail)["generation"]["state"], "READY")
        self.assertEqual(len(json.loads(detail)["cover_letters"]), 0)
        self.assertEqual(provider.calls[0][0], "123456789")

        updated_materials = dict(json.loads(detail)["materials"])
        updated_materials["cover_letter_text"] = "Dear team, this is my reviewed final letter."
        editable = {key: updated_materials[key] for key in (
            "company_analysis", "role_analysis", "personalization_notes",
            "cover_letter_text", "agent_name",
        )}
        self.request("/api/jobs/123456789/materials", method="POST",
                     body=json.dumps(editable).encode(), headers=origin)
        self.request("/api/jobs/123456789/documents", method="POST", body=json.dumps({"format":"pdf"}).encode(), headers=origin)
        _, _, edited_detail = self.request("/api/jobs/123456789")
        self.assertEqual(json.loads(edited_detail)["generation"]["state"], "MANUAL")
        self.assertEqual(len(json.loads(edited_detail)["cover_letters"]), 0)

        _, _, logs_body = self.request("/api/diagnostics/logs?limit=100")
        self.assertNotIn(secret, logs_body.decode())
        self.assertNotIn("private-cv-marker-2195", logs_body.decode())
        self.request("/api/ai/key", method="DELETE", headers={"Origin": self.base_url})
        _, _, after_delete = self.request("/api/ai/settings")
        self.assertFalse(json.loads(after_delete)["key_configured"])

    def test_legacy_agent_migration_requires_choice_and_preserves_recovery_copies(self) -> None:
        legacy = {**default_settings(), "agents": [{
            "id": "legacy-writer", "name": "Legacy writer", "phase": "Writing",
            "enabled": True, "instructions": "Use concise evidence-led writing.",
        }]}
        self.server.ai_settings.path.write_text(json.dumps(legacy), encoding="utf-8")
        _, _, body = self.request("/api/ai/settings")
        migration = json.loads(body)["agent_migration"]
        self.assertTrue(migration["required"])
        self.assertTrue(migration["conflict"])

        origin = {"Origin": self.base_url, "Content-Type": "application/json"}
        with self.assertRaises(HTTPError) as error:
            self.request("/api/settings/agents", method="POST",
                         body=json.dumps({"agents": migration["markdown_agents"]}).encode(), headers=origin)
        self.assertEqual(error.exception.code, 409)

        _, _, result_body = self.request("/api/ai/agents/migration", method="POST",
            body=json.dumps({"choice": "use_saved_agents"}).encode(), headers=origin)
        result = json.loads(result_body)
        self.assertTrue(Path(result["backups"]["settings_backup"]).is_file())
        self.assertTrue(Path(result["backups"]["agents_backup"]).is_file())
        self.assertEqual(self.server.agent_prompt.agents()[0]["name"], "Legacy writer")
        stored = json.loads(self.server.ai_settings.path.read_text(encoding="utf-8"))
        self.assertNotIn("agents", stored)
        self.assertEqual(stored["schema_version"], 2)

    def test_guided_agent_edit_updates_markdown_without_erasing_shared_rules(self) -> None:
        _, _, current = self.request("/api/settings/agent-instructions")
        current_text = json.loads(current)["instructions"]
        agent = json.loads(current)["agents"][0]
        agent["instructions"] = "Review the evidence and write a concise letter."
        origin = {"Origin": self.base_url, "Content-Type": "application/json"}
        _, _, response = self.request("/api/settings/agents", method="POST",
            body=json.dumps({"agents": [agent]}).encode(), headers=origin)
        self.assertEqual(json.loads(response)["agents"][0]["instructions"], agent["instructions"])
        self.assertIn("One unified workflow.", self.server.agent_prompt.load())
        self.assertIn("Internal roles and workflow", self.server.agent_prompt.load())
        self.assertNotEqual(self.server.agent_prompt.load(), current_text)

    def test_context_import_and_cv_preview_are_local(self) -> None:
        boundary = "----ContextFileBoundary"
        content = "Preferred location: Chicago."
        multipart = (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="notes.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n" + content + f"\r\n--{boundary}--\r\n").encode()
        _, _, imported = self.request("/api/profile/context/import", method="POST", body=multipart,
            headers={"Origin": self.base_url, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(json.loads(imported)["context"], content)
        self.assertNotIn(content, self.server.candidate_context.load())

        self.server.candidate_profile.save("cv.txt", "txt", b"CV preview marker")
        _, _, preview = self.request("/api/profile/cv/text")
        self.assertEqual(json.loads(preview)["text"], "CV preview marker")

    def test_local_knowledge_upload_replace_limit_preview_and_delete(self) -> None:
        origin = self.base_url
        for index in range(10):
            boundary = f"----KnowledgeBoundary{index}"
            text = f"Sample letter {index}"
            multipart = (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="sample-{index}.txt"\r\n'
                "Content-Type: text/plain\r\n\r\n" + text + f"\r\n--{boundary}--\r\n").encode()
            self.request("/api/knowledge", method="POST", body=multipart,
                headers={"Origin": origin, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        _, _, listing = self.request("/api/knowledge")
        files = json.loads(listing)["files"]
        self.assertEqual(len(files), 10)
        _, _, preview = self.request(f"/api/knowledge/{files[0]['id']}")
        self.assertEqual(json.loads(preview)["extracted_text"], "Sample letter 0")

        boundary = "----KnowledgeReplacement"
        multipart = (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="replacement.md"\r\n'
            "Content-Type: text/markdown\r\n\r\nNew approved sample\r\n"
            f"--{boundary}--\r\n").encode()
        self.request(f"/api/knowledge/{files[0]['id']}/replace", method="POST", body=multipart,
            headers={"Origin": origin, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(len(self.server.application_data.list_knowledge()), 10)
        self.assertEqual(self.server.application_data.knowledge_text()[0]["text"], "New approved sample")
        self.request(f"/api/knowledge/{files[0]['id']}", method="DELETE", headers={"Origin": origin})
        self.assertEqual(len(self.server.application_data.list_knowledge()), 9)

    def test_research_endpoint_stores_real_citations_and_consults_one_job(self) -> None:
        self.server.api_keys._backend = FakeCredentialBackend()
        self.server.api_keys.save("sk-local-research-test-key")
        settings = default_settings()
        settings["consent"] = True
        settings["web_research_enabled"] = True
        self.server.ai_settings.save(settings)
        cited_url = "https://company.example/news"

        class ResearchProvider:
            last_usage = {"input_tokens": 20, "output_tokens": 12, "search_calls": 1}
            def __init__(self, *_): pass
            def research_company(self, **_):
                return {"summary": "Company research summary", "facts": [
                    {"claim": "A documented company initiative.", "kind": "documented_fact", "source_urls": [cited_url]},
                ], "gaps": "No location details found.", "sources": [
                    {"url": cited_url, "title": "Official newsroom"},
                ], "search_calls": 1}

        with patch("linkedin_connector.dashboard.OpenAIProvider", ResearchProvider):
            _, _, body = self.request("/api/jobs/123456789/research", method="POST",
                                      headers={"Origin": self.base_url})
        response = json.loads(body)
        result = response["research"]
        self.assertTrue(response["company_profile_saved"])
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["facts"][0]["source_urls"], [cited_url])
        self.assertIn("T", result["updated_at"])
        self.assertEqual(result["gaps"], "No location details found.")
        self.assertEqual(self.server.application_data.get_company_research("Example Corp")["summary"],
                         "Company research summary")
        from linkedin_connector.research_pages import research_page_cache_key
        page_text = "Company develops services for customers and shares current product information."
        page = {"url": cited_url, "title": "Official newsroom", "state": "READY",
                "error_code": "", "text": page_text}
        self.server.application_data.save_research_pages(
            "123456789", research_page_cache_key(result), [page])
        detail = json.loads(self.request("/api/jobs/123456789")[2])
        self.assertEqual(detail["research"]["page_texts"], [page])

    def test_removed_chatbot_routes_are_unavailable_and_job_detail_is_clean(self) -> None:
        detail = json.loads(self.request("/api/jobs/123456789")[2])
        self.assertNotIn("chat", detail)
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        for path, method, body in (
            ("/api/jobs/123456789/chat", "POST", b'{"message":"revise"}'),
            ("/api/jobs/123456789/chat/apply", "POST", b'{"cover_letter_text":"text"}'),
            ("/api/ai/chat", "POST", b'{"message":"revise"}'),
        ):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path, method=method, body=body, headers=headers)
            self.assertEqual(error.exception.code, 404)

    def test_feedback_is_saved_locally_without_changing_letter_or_calling_ai(self) -> None:
        original = self.store.save_job_materials("123456789", company_analysis="Company",
            role_analysis="Role", personalization_notes="Match", cover_letter_text="Dear team.",
            agent_name="Writer")
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        payload = json.dumps({"rating": 3, "comment": "too generic"}).encode()
        usage_before = self.server.generation_tasks.summary()["usage"]["calls"]
        with patch("linkedin_connector.dashboard.OpenAIProvider", side_effect=AssertionError("feedback must not call AI")):
            _, _, response = self.request("/api/jobs/123456789/feedback", method="POST",
                body=payload, headers=headers)
        result = json.loads(response)
        self.assertEqual(result["feedback"][0]["comment"], "too generic")
        self.assertEqual(result["feedback"][0]["rating"], 3)
        self.assertEqual(result["feedback"][0]["version"], result["version"])
        self.assertEqual(self.store.get_job_bundle("123456789")["materials"]["cover_letter_text"],
                         original["cover_letter_text"])
        self.assertEqual(self.server.generation_tasks.summary()["usage"]["calls"], usage_before)

    def test_current_cover_letter_can_be_saved_to_local_knowledge_once(self) -> None:
        self.store.save_job_materials("123456789", cover_letter_text="A job-specific style example.")
        headers = {"Origin": self.base_url}
        _, _, response = self.request("/api/jobs/123456789/knowledge", method="POST", headers=headers)
        result = json.loads(response)
        self.assertEqual(result["max_files"], 10)
        self.assertEqual(result["file"]["file_format"], "txt")
        self.assertEqual(self.server.application_data.knowledge_text()[0]["text"],
                         "A job-specific style example.")
        with self.assertRaises(HTTPError) as error:
            self.request("/api/jobs/123456789/knowledge", method="POST", headers=headers)
        self.assertEqual(error.exception.code, 409)

    def test_bulk_regeneration_requires_preview_confirmation_and_can_cancel_queued_work(self) -> None:
        self.server.generation.stop()
        self.server.generation.wake = lambda: None
        self.server.api_keys._backend = FakeCredentialBackend()
        self.server.api_keys.save("sk-local-bulk-test-key")
        self.server.candidate_profile.save("cv.txt", "txt", b"Evidence for a role")
        settings = default_settings()
        settings["consent"] = True
        settings["enabled"] = False
        self.server.ai_settings.save(settings)
        self.store.save_job_materials("123456789", cover_letter_text="Existing local letter.")
        self.server.generation_tasks.mark_manual_edit("123456789")
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        calls_before = self.server.generation_tasks.summary()["usage"]["calls"]
        _, _, preview_body = self.request("/api/ai/bulk/preview", method="POST",
            body=json.dumps({"scope_days": None, "only_new": False}).encode(), headers=headers)
        preview = json.loads(preview_body)
        self.assertEqual(preview["preview"]["eligible_count"], 1)
        self.assertGreater(preview["estimate"]["expected"], 0)
        self.assertGreater(preview["estimate"]["duration_seconds"]["expected"], 0)
        self.assertTrue(preview["ready_for_ai"])
        self.assertEqual(self.server.generation_tasks.summary()["usage"]["calls"], calls_before)
        self.assertNotIn("job_ids", preview["preview"])

        _, _, queued_body = self.request("/api/ai/bulk/confirm", method="POST",
            body=json.dumps({"plan_token": preview["plan_token"]}).encode(), headers=headers)
        queued = json.loads(queued_body)
        self.assertEqual(queued["counts"]["queued"], 1)
        with self.assertRaises(HTTPError) as replay:
            self.request("/api/ai/bulk/confirm", method="POST",
                body=json.dumps({"plan_token": preview["plan_token"]}).encode(), headers=headers)
        self.assertEqual(replay.exception.code, 409)

        _, _, cancelled_body = self.request(f"/api/ai/bulk/{queued['batch_id']}/cancel", method="POST",
            headers={"Origin": self.base_url})
        cancelled = json.loads(cancelled_body)
        self.assertEqual(cancelled["counts"]["cancelled"], 1)
        self.assertEqual(self.server.generation_tasks.get("123456789")["state"], "MANUAL")

    def test_bulk_preview_all_includes_old_and_ignored_jobs(self) -> None:
        self.store.save_job_materials("123456789", cover_letter_text="Existing local letter.")
        self.server.generation_tasks.mark_manual_edit("123456789")
        self.server.generation_tasks.reconcile()
        old_saved_at = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        connection = self.store._connection()
        try:
            connection.execute(
                "UPDATE jobs SET first_saved_at=?,status='IGNORED' WHERE linkedin_job_id=?",
                (old_saved_at, "123456789"),
            )
            connection.commit()
        finally:
            connection.close()
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        _, _, response = self.request("/api/ai/bulk/preview", method="POST",
            body=json.dumps({"scope_days": None, "only_new": False}).encode(), headers=headers)
        preview = json.loads(response)
        self.assertEqual(preview["preview"]["eligible_count"], 1)
        self.assertEqual(preview["preview"]["stored_cover_letter_count"], 1)
        self.assertEqual(preview["preview"]["excluded"]["oltre_soglia_eta"], 0)
        self.assertIsNotNone(preview["plan_token"])
        self.assertEqual(self.server.generation_tasks.summary()["usage"]["calls"], 0)

    def test_bulk_preview_does_not_estimate_web_search_for_cached_company(self) -> None:
        source = {"url": "https://example.com/about", "title": "Official company page"}
        saved = self.server.application_data.save_company_research("Example Corp", {
            "summary": "Public company overview.", "sources": [source],
            "facts": [{"claim": "The company works in analytics.",
                       "kind": "documented_fact", "source_urls": [source["url"]]}],
            "search_calls": 1,
        })
        self.assertTrue(saved)
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        _, _, response = self.request("/api/ai/bulk/preview", method="POST",
            body=json.dumps({"scope_days": None, "only_new": False}).encode(), headers=headers)
        preview = json.loads(response)
        self.assertEqual(preview["estimate"]["research_jobs"], 0)
        cached = self.server.application_data.get_company_research("Example Corp", mark_used=False)
        self.assertEqual(cached["use_count"], 0)

    def test_only_new_bulk_preview_and_confirmation_keep_the_same_file_aware_selection(self) -> None:
        recovered = make_job("987654321", title="New role")
        file_only = make_job("555555555", title="Uploaded letter role")
        self.store.sync_jobs([recovered, file_only], suppress_auto_generation=True)
        self.server.generation_tasks.mark_recovered_jobs([recovered.linkedin_job_id,
                                                           file_only.linkedin_job_id])
        self.store.save_cover_letter_file(
            file_only.linkedin_job_id, original_filename="old-letter.pdf",
            file_format="pdf", contents=b"%PDF-test",
        )
        self.store.save_job_materials("123456789", cover_letter_text="Existing letter")
        self.server.generation_tasks.mark_manual_edit("123456789")

        settings = default_settings()
        settings["consent"] = True
        settings["enabled"] = False
        self.server.ai_settings.save(settings)
        self.server.generation.wake = lambda: None
        self.server.api_keys._backend = FakeCredentialBackend()
        self.server.api_keys.save("sk-local-new-only-test-key")
        self.server.candidate_profile.save("cv.txt", "txt", b"Candidate evidence")
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        _, _, preview_body = self.request("/api/ai/bulk/preview", method="POST",
            body=json.dumps({"scope_days": None, "only_new": True}).encode(), headers=headers)
        preview = json.loads(preview_body)
        self.assertEqual(preview["preview"]["eligible_count"], 1)
        self.assertEqual(preview["preview"]["scope_candidate_count"], 1)
        self.assertEqual(preview["preview"]["stored_cover_letter_count"], 2)
        self.assertTrue(preview["ready_for_ai"])

        _, _, queued_body = self.request("/api/ai/bulk/confirm", method="POST",
            body=json.dumps({"plan_token": preview["plan_token"]}).encode(), headers=headers)
        queued = json.loads(queued_body)
        connection = self.store._connection()
        try:
            selected = [row["linkedin_job_id"] for row in connection.execute(
                "SELECT linkedin_job_id FROM bulk_regeneration_items WHERE batch_id=?",
                (queued["batch_id"],),
            )]
        finally:
            connection.close()
        self.assertEqual(selected, [recovered.linkedin_job_id])

    def test_full_recovery_reconciles_current_list_and_preserves_existing_letter_data(self) -> None:
        existing_id = "111111111"
        old_job = make_job("123456789", title="Older local offer")
        recent_job = make_job(existing_id, title="Updated local offer")
        self.store.sync_jobs([
            make_job(existing_id, title="Original local offer"),
            make_job("222222222", title="Removed without letter"),
            make_job("333333333", title="Removed with letter"),
        ])
        self.store.save_job_materials(existing_id, cover_letter_text="Keep this letter")
        self.server.generation_tasks.mark_manual_edit(existing_id)
        self.store.save_job_materials("333333333", cover_letter_text="Keep archived letter")
        self.store.mark_processed("333333333")
        self.server.generation_tasks.reconcile()
        self.assertEqual(self.server.generation_tasks.get("222222222")["state"], "PENDING")
        old_time = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        connection = self.store._connection()
        try:
            connection.execute("UPDATE jobs SET first_saved_at=? WHERE linkedin_job_id=?",
                               (old_time, "123456789"))
            connection.commit()
        finally:
            connection.close()
        self.server.sync_coordinator.full_reader = lambda: [old_job, recent_job,
                                                              make_job("987654321", title="Newly recovered")]
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        _, _, body = self.request("/api/sync/recover", method="POST",
            body=json.dumps({}).encode(), headers=headers)
        started = json.loads(body)
        self.assertIn(started["status"], {"running", "complete"})
        until = time.monotonic() + 4
        result = started
        while time.monotonic() < until:
            _, _, status_body = self.request("/api/sync/status")
            result = json.loads(status_body)
            if not result.get("running"):
                break
            time.sleep(0.02)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["counts"]["discovered"], 3)
        self.assertEqual(result["counts"]["inserted"], 1)
        self.assertEqual(result["counts"]["refreshed"], 2)
        self.assertEqual(result["counts"]["removed_from_linkedin"], 2)
        self.assertEqual(result["counts"]["hidden_without_letter"], 1)
        self.assertEqual(result["counts"]["preserved_with_letter"], 1)
        self.assertEqual(result["counts"]["cancelled_generation_tasks"], 1)
        self.assertEqual(self.store.get_job("123456789")["title"], "Older local offer")
        self.assertEqual(self.store.get_job(existing_id)["title"], "Updated local offer")
        self.assertEqual(self.store.get_job(existing_id)["status"], "NEW")
        self.assertEqual(self.store.get_job_bundle(existing_id)["materials"]["cover_letter_text"],
                         "Keep this letter")
        self.assertFalse(self.store.get_job("222222222")["linkedin_saved"])
        self.assertEqual(self.server.generation_tasks.get("222222222")["state"], "MANUAL")
        self.assertEqual(self.store.get_job("333333333")["status"], "PROCESSED")
        self.assertFalse(self.store.get_job("333333333")["linkedin_saved"])
        self.assertEqual(self.store.get_job_bundle("333333333")["materials"]["cover_letter_text"],
                         "Keep archived letter")
        listed_ids = {row["linkedin_job_id"] for row in self.store.get_dashboard_jobs()}
        self.assertNotIn("222222222", listed_ids)
        self.assertNotIn("333333333", listed_ids)
        recovered = self.store.get_job("987654321")
        self.assertEqual(recovered["suppress_auto_generation"], 1)
        self.assertEqual(self.server.generation_tasks.get("987654321")["state"], "MANUAL")

    def test_failed_full_recovery_keeps_the_local_snapshot_unchanged(self) -> None:
        self.store.sync_jobs([make_job("222222222", title="Keep until complete")])
        self.server.sync_coordinator.full_reader = lambda: (_ for _ in ()).throw(
            RuntimeError("LinkedIn list was incomplete")
        )
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        _, _, body = self.request("/api/sync/recover", method="POST",
                                  body=json.dumps({}).encode(), headers=headers)
        result = json.loads(body)
        until = time.monotonic() + 4
        while result.get("running") and time.monotonic() < until:
            time.sleep(0.02)
            _, _, status_body = self.request("/api/sync/status")
            result = json.loads(status_body)
        self.assertEqual(result["status"], "error")
        self.assertTrue(self.store.get_job("222222222")["linkedin_saved"])
        self.assertIsNone(self.store.get_job("987654321"))

    def test_unverified_count_mismatch_imports_visible_jobs_without_hiding_absent_local_jobs(self) -> None:
        from linkedin_connector.reader import SavedJobCards

        self.store.sync_jobs([make_job("222222222", title="Keep in local list")])
        self.store.save_job_materials("222222222", cover_letter_text="Existing paid letter")
        self.server.sync_coordinator.full_reader = lambda: SavedJobCards(
            [make_job("987654321", title="Visible new job")], count_mismatch=True
        )
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        _, _, body = self.request("/api/sync/recover", method="POST",
                                  body=json.dumps({}).encode(), headers=headers)
        result = json.loads(body)
        until = time.monotonic() + 4
        while result.get("running") and time.monotonic() < until:
            time.sleep(0.02)
            _, _, status_body = self.request("/api/sync/status")
            result = json.loads(status_body)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["counts"]["saved_count_mismatch"])
        self.assertTrue(self.store.get_job("222222222")["linkedin_saved"])
        self.assertEqual(self.store.get_job_bundle("222222222")["materials"]["cover_letter_text"],
                         "Existing paid letter")
        self.assertIsNotNone(self.store.get_job("987654321"))
        self.assertEqual(self.store.get_job("987654321")["suppress_auto_generation"], 1)
        self.assertEqual(self.server.generation_tasks.get("987654321")["state"], "MANUAL")

    def test_stable_count_mismatch_reconciles_removed_jobs_and_reports_completion(self) -> None:
        from linkedin_connector.reader import SavedJobCards

        self.store.sync_jobs([make_job("222222222", title="Removed from LinkedIn")])
        self.store.save_job_materials("222222222", cover_letter_text="Keep my letter")
        self.server.sync_coordinator.full_reader = lambda: SavedJobCards(
            [make_job("987654321", title="Still saved")],
            count_mismatch=True,
            visible_ids=["987654321"],
            membership_verified=True,
        )
        _, _, body = self.request(
            "/api/sync/recover", method="POST", body=b"{}",
            headers={"Origin": self.base_url, "Content-Type": "application/json"},
        )
        result = json.loads(body)
        until = time.monotonic() + 4
        while result.get("running") and time.monotonic() < until:
            time.sleep(0.02)
            _, _, status_body = self.request("/api/sync/status")
            result = json.loads(status_body)

        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["counts"]["saved_count_mismatch"])
        self.assertTrue(result["counts"]["membership_verified"])
        self.assertFalse(self.store.get_job("222222222")["linkedin_saved"])
        self.assertEqual(
            self.store.get_job_bundle("222222222")["materials"]["cover_letter_text"],
            "Keep my letter",
        )
        self.assertIsNotNone(self.store.get_job("987654321"))

    def test_confirmed_empty_saved_list_hides_linkedin_jobs_and_keeps_cover_letters(self) -> None:
        from linkedin_connector.reader import SavedJobCards

        self.store.save_job_materials("123456789", cover_letter_text="Keep this letter")
        self.server.sync_coordinator.full_reader = lambda: SavedJobCards(
            [], visible_ids=[], membership_verified=True
        )
        _, _, body = self.request(
            "/api/sync/recover", method="POST", body=b"{}",
            headers={"Origin": self.base_url, "Content-Type": "application/json"},
        )
        result = json.loads(body)
        until = time.monotonic() + 4
        while result.get("running") and time.monotonic() < until:
            time.sleep(0.02)
            _, _, status_body = self.request("/api/sync/status")
            result = json.loads(status_body)

        self.assertEqual(result["status"], "complete")
        self.assertFalse(self.store.get_job("123456789")["linkedin_saved"])
        self.assertEqual(
            self.store.get_job_bundle("123456789")["materials"]["cover_letter_text"],
            "Keep this letter",
        )

    def test_recovery_continues_after_unreadable_details_and_persists_timed_report(self) -> None:
        from linkedin_connector.reader import SavedJobCards

        self.store.save_job_materials("123456789", cover_letter_text="Preserve this letter")
        self.store.sync_jobs([make_job("222222222", title="Removed with letter")])
        self.store.save_job_materials("222222222", cover_letter_text="Retain archived letter")
        self.server.sync_coordinator.full_reader = lambda: SavedJobCards(
            [make_job("987654321", title="Readable new role")],
            visible_ids=["123456789", "987654321", "444444444"],
            membership_verified=True,
            outcomes=[{
                "linkedin_job_id": "987654321", "title": "Readable new role",
                "status": "read", "page_opened": True, "detail_opened": True,
                "description_read_status": "complete", "attempts": 1, "duration_ms": 37,
            }],
            unreadable=[
                {"linkedin_job_id": "123456789", "title": "Data Analyst",
                 "company": "Example Corp", "reason": "Description unavailable"},
                {"linkedin_job_id": "444444444", "title": "New unreadable role",
                 "company": "New Corp", "reason": "Description unavailable"},
            ],
        )
        _, _, body = self.request(
            "/api/sync/recover", method="POST", body=b"{}",
            headers={"Origin": self.base_url, "Content-Type": "application/json"},
        )
        result = json.loads(body)
        until = time.monotonic() + 4
        while result.get("running") and time.monotonic() < until:
            time.sleep(0.02)
            _, _, status_body = self.request("/api/sync/status")
            result = json.loads(status_body)

        report = result["last_report"]
        self.assertEqual(result["status"], "partial")
        self.assertTrue(report["membership_verified"])
        self.assertLessEqual(report["started_at"], report["ended_at"])
        self.assertGreaterEqual(report["duration_ms"], 0)
        self.assertEqual(report["counts"]["discovered"], 3)
        self.assertEqual(report["counts"]["inserted"], 1)
        self.assertEqual(report["counts"]["removed_from_linkedin"], 1)
        self.assertEqual(report["counts"]["unreadable_descriptions"], 2)
        read_outcome = report["read_outcomes"][0]
        self.assertEqual(read_outcome["duration_ms"], 37)
        self.assertTrue(read_outcome["page_opened"])
        self.assertTrue(read_outcome["detail_opened"])
        self.assertEqual(read_outcome["description_read_status"], "complete")
        self.assertEqual(read_outcome["import_status"], "added")
        event_types = {event["type"] for event in report["events"]}
        self.assertTrue({"added", "maintained", "not_imported", "removed_from_saved"}.issubset(event_types))
        self.assertTrue(self.store.get_job("123456789")["linkedin_saved"])
        self.assertFalse(self.store.get_job("222222222")["linkedin_saved"])
        self.assertEqual(self.store.get_job_bundle("123456789")["materials"]["cover_letter_text"],
                         "Preserve this letter")
        self.assertEqual(self.store.get_job_bundle("222222222")["materials"]["cover_letter_text"],
                         "Retain archived letter")
        self.assertIsNone(self.store.get_job("444444444"))
        self.assertIsNone(result["last_successful_full_sync_at"])

    def test_incomplete_linkedin_read_reports_partial_without_changing_database(self) -> None:
        self.server.sync_coordinator.full_reader = lambda: (_ for _ in ()).throw(
            LinkedInReadError("LinkedIn showed inconsistent Saved cards.")
        )
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        _, _, body = self.request("/api/sync/recover", method="POST",
                                  body=b"{}", headers=headers)
        result = json.loads(body)
        until = time.monotonic() + 4
        while result.get("running") and time.monotonic() < until:
            time.sleep(0.02)
            _, _, status_body = self.request("/api/sync/status")
            result = json.loads(status_body)
        self.assertEqual(result["status"], "partial")
        self.assertIn("Nessun dato locale", result["message"])
        self.assertNotIn("counts", result)
        self.assertTrue(self.store.get_job("123456789")["linkedin_saved"])

    def test_dashboard_migrates_prior_laya_opt_out_to_automatic_mode(self) -> None:
        other_profile = Path(self.temp_dir.name) / "other_profile"
        other_profile.mkdir()
        (other_profile / "laya.json").write_text('{"enabled": false}', encoding="utf-8")
        other_store = JobStore(other_profile / "jobs.sqlite3")
        server = DashboardHTTPServer(("127.0.0.1", 0), other_store,
                                     profile_directory=other_profile)
        try:
            self.assertTrue(server.generation.local_decisions.settings()["enabled"])
        finally:
            server.server_close()

    def test_incremental_refresh_queues_new_job_for_ai_when_enabled(self) -> None:
        self.server.api_keys._backend = FakeCredentialBackend()
        self.server.api_keys.save("sk-local-refresh-test-key")
        self.server.candidate_profile.save("cv.txt", "txt", b"Builds reports")
        self.store.save_job_materials("123456789", cover_letter_text="Manual existing letter")
        self.server.generation_tasks.mark_manual_edit("123456789")
        provider = FakeProvider()
        self.server.generation.provider_factory = lambda key, model: provider
        settings = default_settings()
        settings["consent"] = settings["enabled"] = True
        settings["web_research_enabled"] = True
        self.server.ai_settings.save(settings)
        self.server.sync_coordinator.reader = lambda existing_ids: [make_job("987654321")]
        self.request("/api/sync", method="POST", headers={"Origin": self.base_url})
        until = time.monotonic() + 4
        while time.monotonic() < until:
            task = self.server.generation_tasks.get("987654321")
            if task and task["state"] == "READY":
                break
            time.sleep(0.02)
        self.assertEqual(self.server.generation_tasks.get("987654321")["state"], "READY")
        self.assertEqual(self.server.generation_tasks.get("123456789")["state"], "MANUAL")
        self.assertTrue(all(job_id == "987654321" for job_id, _ in provider.calls))

    def test_diagnostic_view_is_bounded_and_correlates_api_requests(self) -> None:
        _, job_headers, _ = self.request("/api/jobs")
        request_id = job_headers["X-Request-ID"]

        _, _, body = self.request(f"/api/diagnostics/logs?limit=20&q={request_id}")
        result = json.loads(body)
        matching = [event for event in result["events"] if event["request_id"] == request_id]
        http_event = next(event for event in matching if event["event"] == "http.request.completed")
        self.assertEqual(http_event["context"]["route"], "jobs")
        self.assertTrue(any(event["event"] == "db.read_completed" for event in matching))
        self.assertEqual(result["path"], str(self.server.log_directory))

        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        _, _, filtered_body = self.request(f"/api/diagnostics/logs?since={future}")
        filtered = json.loads(filtered_body)
        self.assertEqual(filtered["events"], [])
        self.assertEqual(filtered["since"], future)

        with self.assertRaises(HTTPError) as error:
            self.request("/api/diagnostics/logs?limit=999")
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/diagnostics/logs?since=not-a-timestamp")
        self.assertEqual(error.exception.code, 400)

    def test_ai_usage_profile_exposes_average_stage_duration_without_prompt_data(self) -> None:
        tasks = self.server.generation_tasks
        reservation = tasks.reserve_api_call(
            "123456789", "gpt-5.6-luna", "agent", daily_limit=10,
            category_limit=10, stage="cv-match-writer")
        self.assertIsNotNone(reservation)
        tasks.finish_api_call(reservation, {"input_tokens": 100, "output_tokens": 50, "duration_ms": 2_400})

        status, _, body = self.request("/api/ai/usage-profile?days=30")
        result = json.loads(body)
        self.assertEqual(status, 200)
        row = next(item for item in result["stages"] if item["stage"] == "cv-match-writer")
        self.assertEqual(row["calls"], 1)
        self.assertEqual(row["avg_duration_ms"], 2_400.0)
        self.assertNotIn("input", row)

    def test_browser_errors_are_limited_redacted_and_logged_same_origin_only(self) -> None:
        payload = {
            "kind": "javascript_error",
            "message": "Unexpected token=browser-secret user@example.com",
            "error_type": "TypeError",
            "source": "/app.js",
            "line": 7,
            "column": 12,
            "stack": "TypeError: api_key=stack-secret\n    at render (http://127.0.0.1:8765/app.js:7:12)",
            "request_id": "1234567890abcdef",
        }
        _, _, body = self.request(
            "/api/diagnostics/client-error",
            method="POST",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Origin": self.base_url, "Content-Type": "application/json"},
        )
        self.assertTrue(json.loads(body)["accepted"])

        _, _, log_body = self.request("/api/diagnostics/logs?level=ERROR&q=javascript_error")
        events = json.loads(log_body)["events"]
        browser_event = next(event for event in events if event["component"] == "browser")
        self.assertNotIn("browser-secret", json.dumps(browser_event))
        self.assertNotIn("stack-secret", json.dumps(browser_event))
        self.assertNotIn("user@example.com", json.dumps(browser_event))
        self.assertEqual(
            browser_event["details"]["client_message"],
            "Browser reported an unhandled TypeError.",
        )
        self.assertEqual(browser_event["details"]["client_stack"], "at /app.js:7:12")
        self.assertEqual(browser_event["details"]["line"], 7)

        request = Request(
            f"{self.base_url}/api/diagnostics/client-error",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Origin": "http://attacker.example", "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 403)

    def test_browser_error_payload_size_is_limited(self) -> None:
        payload = {
            "kind": "javascript_error",
            "message": "Synthetic error.",
            "stack": "x" * 12_100,
        }
        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/diagnostics/client-error",
                method="POST",
                body=json.dumps(payload).encode("utf-8"),
                headers={"Origin": self.base_url, "Content-Type": "application/json"},
            )
        self.assertEqual(error.exception.code, 413)

    def test_browser_error_rate_limit_and_host_validation(self) -> None:
        payload = {"kind": "javascript_error", "message": "Synthetic error."}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Origin": self.base_url, "Content-Type": "application/json"}
        for _ in range(20):
            status, _, _ = self.request(
                "/api/diagnostics/client-error", method="POST", body=body, headers=headers
            )
            self.assertEqual(status, 202)

        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/diagnostics/client-error", method="POST", body=body, headers=headers
            )
        self.assertEqual(error.exception.code, 429)

        wrong_host_headers = {**headers, "Host": "attacker.example"}
        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/diagnostics/client-error",
                method="POST",
                body=body,
                headers=wrong_host_headers,
            )
        self.assertEqual(error.exception.code, 403)

    def test_single_markdown_agent_file_is_readable_and_saved_locally(self) -> None:
        _, _, body = self.request("/api/settings/agent-instructions")
        defaults = json.loads(body)
        self.assertEqual(defaults["instructions"], TEST_AGENTS_MARKDOWN)
        self.assertEqual(defaults["agents"][0]["name"], "Writer")
        self.assertEqual(defaults["path"], "data/user_profile/AGENTS.md")

        updated = ("# Workflow personale\n\nRegole condivise.\n\n## Internal roles and workflow\n\n"
                   "### 1. Reviewer\n<!-- agent:id=reviewer phase=review enabled=true -->\n\n"
                   "Controlla i fatti e la lingua.\n")
        status, _, saved_body = self.request(
            "/api/settings/agent-instructions",
            method="POST",
            body=json.dumps({"instructions": updated}).encode("utf-8"),
            headers={"Origin": self.base_url, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(saved_body)["instructions"], updated)
        _, _, reread_body = self.request("/api/settings/agent-instructions")
        self.assertEqual(json.loads(reread_body)["instructions"], updated)

    def test_candidate_context_is_a_separate_editable_local_markdown_file(self) -> None:
        _, _, body = self.request("/api/settings/candidate-context")
        context = json.loads(body)
        self.assertIn("context-marker-83c1", context["context"])
        self.assertEqual(context["path"], "data/user_profile/CANDIDATE_CONTEXT.md")

        updated = "# Candidate notes\n\nEdited context marker.\n"
        status, _, saved_body = self.request(
            "/api/settings/candidate-context",
            method="POST",
            body=json.dumps({"context": updated}).encode("utf-8"),
            headers={"Origin": self.base_url, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(saved_body)["context"], updated)
        self.assertEqual(
            (self.profile_directory / "CANDIDATE_CONTEXT.md").read_text(encoding="utf-8"),
            updated,
        )

    def test_cv_is_local_and_old_manual_agent_prompt_route_is_removed(self) -> None:
        boundary = "----CandidateCVBoundary"
        cv_text = "Esperienza: analisi dati locale-marker-7f4a. Python e reporting."
        multipart = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="Candidate-CV.txt"\r\n'
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            f"{cv_text}\r\n--{boundary}--\r\n"
        ).encode("utf-8")
        headers = {"Origin": self.base_url, "Content-Type": f"multipart/form-data; boundary={boundary}"}
        status, _, response_body = self.request(
            "/api/profile/cv", method="POST", body=multipart, headers=headers
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(response_body)["cv"]["format"], "txt")
        self.assertEqual(json.loads(response_body)["cv"]["filename"], "Candidate-CV.txt")
        self.assertTrue(json.loads(response_body)["cv"]["original_filename_available"])
        stored = list((self.store.database_path.parent / "candidate_profile").iterdir())
        self.assertEqual({path.name for path in stored}, {"cv.txt", "cv-metadata.json"})

        with self.assertRaises(HTTPError) as error:
            self.request("/api/jobs/123456789/agent-prompt", method="POST",
                         headers={"Origin": self.base_url})
        self.assertEqual(error.exception.code, 404)

        _, _, logs_body = self.request("/api/diagnostics/logs?limit=100")
        logs = json.dumps(json.loads(logs_body))
        self.assertNotIn("locale-marker-7f4a", logs)
        self.assertNotIn("context-marker-83c1", logs)

        _, _, deleted_body = self.request(
            "/api/profile/cv", method="DELETE", headers={"Origin": self.base_url}
        )
        self.assertFalse(json.loads(deleted_body)["cv"]["has_cv"])

    def test_cv_requires_same_origin_and_upload_is_size_limited(self) -> None:
        body = b"--x--\r\n"
        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/profile/cv",
                method="POST",
                body=body,
                headers={"Origin": "http://attacker.example", "Content-Type": "multipart/form-data; boundary=x"},
            )
        self.assertEqual(error.exception.code, 403)

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.putrequest("POST", "/api/profile/cv")
            connection.putheader("Host", f"127.0.0.1:{self.server.server_port}")
            connection.putheader("Origin", self.base_url)
            connection.putheader("Content-Type", "multipart/form-data; boundary=x")
            connection.putheader("Content-Length", str(10 * 1024 * 1024 + 64_001))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()

    def test_removed_agent_prompt_route_and_cv_can_be_removed_locally(self) -> None:
        with self.assertRaises(HTTPError) as error:
            self.request("/api/jobs/123456789/agent-prompt", method="POST",
                         headers={"Origin": self.base_url})
        self.assertEqual(error.exception.code, 404)

        with self.assertRaises(HTTPError) as error:
            self.request(
                "/api/profile/cv",
                method="DELETE",
                headers={"Origin": "http://attacker.example"},
            )
        self.assertEqual(error.exception.code, 403)

    def test_incremental_sync_passes_existing_ids_to_reader(self) -> None:
        seen_ids: list[set[str]] = []
        coordinator = SyncCoordinator(
            self.store,
            reader=lambda existing: (seen_ids.append(existing) or []),
        )

        coordinator._run()

        self.assertEqual(seen_ids, [{"123456789"}])
        snapshot = coordinator.snapshot()
        self.assertEqual(snapshot["status"], "complete")
        self.assertEqual(snapshot["counts"]["inserted"], 0)
        self.assertEqual(snapshot["counts"]["discovered"], 0)

    def test_requested_export_uses_current_text_single_format_and_preserves_text_on_failure(self):
        from unittest.mock import patch
        origin = {"Origin": self.base_url, "Content-Type": "application/json"}
        self.store.save_job_materials("123456789", cover_letter_text="Latest saved letter")
        with patch("linkedin_connector.dashboard.render_letter_document", return_value=b"requested-pdf") as render:
            status, headers, body = self.request("/api/jobs/123456789/documents", method="POST", body=json.dumps({"format":"pdf"}).encode(), headers=origin)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"requested-pdf")
        self.assertIn('filename="cover-letter-v01.pdf"', headers.get("Content-Disposition", ""))
        render.assert_called_once_with("Latest saved letter", "pdf")
        self.assertEqual(len(self.store.get_job_bundle("123456789")["cover_letters"]), 1)
        self.store.save_job_materials("123456789", cover_letter_text="Changed letter")
        with patch("linkedin_connector.dashboard.render_letter_document", return_value=b"requested-word") as render:
            status, headers, body = self.request("/api/jobs/123456789/documents", method="POST", body=json.dumps({"format":"docx"}).encode(), headers=origin)
        render.assert_called_once_with("Changed letter", "docx")
        self.assertEqual(body, b"requested-word")
        self.assertIn('filename="cover-letter-v01.docx"', headers.get("Content-Disposition", ""))
        self.assertEqual(len(self.store.get_job_bundle("123456789")["cover_letters"]), 2)
        with patch("linkedin_connector.dashboard.render_letter_document", side_effect=RuntimeError("renderer unavailable")):
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/jobs/123456789/documents", method="POST", body=json.dumps({"format":"pdf"}).encode(), headers=origin)
            self.assertEqual(caught.exception.code, 500)
            caught.exception.close()
        self.assertEqual(self.store.get_job_bundle("123456789")["materials"]["cover_letter_text"], "Changed letter")

    def test_same_origin_material_save_and_pdf_archive(self) -> None:
        origin = {"Origin": self.base_url}
        materials = json.dumps(
            {
                "company_analysis": "Product-focused data team.",
                "role_analysis": "Build business reports.",
                "personalization_notes": "Highlights relevant project work.",
                "cover_letter_text": "Dear team, I am applying.",
                "agent_name": "Test Research Agent",
            }
        ).encode("utf-8")
        _, _, saved_materials = self.request(
            "/api/jobs/123456789/materials",
            method="POST",
            body=materials,
            headers={**origin, "Content-Type": "application/json"},
        )
        self.assertEqual(
            json.loads(saved_materials)["materials"]["agent_name"], "Test Research Agent"
        )

        boundary = "----DashboardUploadBoundary"
        pdf = b"%PDF-1.7\ncover letter\n%%EOF"
        multipart = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="application.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode("ascii") + pdf + f"\r\n--{boundary}--\r\n".encode("ascii")
        _, _, uploaded = self.request(
            "/api/jobs/123456789/letters",
            method="POST",
            body=multipart,
            headers={**origin, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        record = json.loads(uploaded)["cover_letter"]
        self.assertEqual(record["relative_path"], "cover_letters/123456789/cover-letter-v01.pdf")

        _, headers, contents = self.request(f"/api/letters/{record['id']}/file")
        self.assertEqual(contents, pdf)
        self.assertIn("inline", headers.get("Content-Disposition", ""))

    def test_rejects_untrusted_origin_and_non_pdf_payload(self) -> None:
        request = Request(
            f"{self.base_url}/api/sync",
            data=b"{}",
            headers={"Origin": "http://attacker.example", "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 403)

        boundary = "----InvalidUpload"
        multipart = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="not-a-pdf.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
            "not really a pdf\r\n"
            f"--{boundary}--\r\n"
        ).encode("ascii")
        request = Request(
            f"{self.base_url}/api/jobs/123456789/letters",
            data=multipart,
            headers={
                "Origin": self.base_url,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as upload_error:
            urlopen(request, timeout=3)
        self.assertEqual(upload_error.exception.code, 400)

    def test_rejects_host_header_rebinding(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("GET", "/api/jobs", headers={"Host": "attacker.example"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
        finally:
            connection.close()

    def test_word_open_requires_a_user_action_and_opens_registered_local_file(self) -> None:
        letter = self.store.save_cover_letter_file(
            "123456789",
            original_filename="letter.docx",
            file_format="docx",
            contents=b"test document",
        )
        request = Request(
            f"{self.base_url}/api/letters/{letter['id']}/open",
            data=b"",
            headers={"Origin": self.base_url},
            method="POST",
        )
        with patch("linkedin_connector.dashboard.os.startfile", create=True) as open_file:
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
            open_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
