"""A small real-browser smoke test for the local dashboard controls."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from linkedin_connector.dashboard import DashboardHTTPServer
from linkedin_connector.database import JobStore
from tests.test_database import make_job


class DashboardBrowserTests(unittest.TestCase):
    def test_openai_workspace_hides_linkedin_actions_on_mobile(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = JobStore(Path(temp_dir) / "data" / "jobs.sqlite3")
            store.sync_jobs([make_job()])
            store.create_manual_job(
                "Prepare weekly reports and analyze sales data for the operations team.",
                title="Manual saved role", company="Manual Example", location="Milan, Italy",
            )
            store.create_chatgpt_job(
                title="Data Analyst",
                company="Example Corp",
                location="Milan, Italy",
                description=("Build useful reports for business teams, analyze data, "
                            "and communicate findings to stakeholders."),
                job_url="https://www.linkedin.com/jobs/view/123456789/",
                application_url="https://apply.example.org/start/123456789",
                cover_letter_text="Dear hiring team, I am applying for this role.",
            )
            server = DashboardHTTPServer(
                ("127.0.0.1", 0), store,
                profile_directory=Path(temp_dir) / "data" / "user_profile",
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    try:
                        browser = playwright.chromium.launch(channel="chrome", headless=True)
                    except Exception as error:
                        self.skipTest(f"Chrome is not available for the smoke test: {type(error).__name__}")
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        job_requests: list[str] = []
                        page.on("request", lambda request: job_requests.append(request.url)
                                if "/api/jobs?" in request.url else None)
                        page.goto(f"http://127.0.0.1:{server.server_port}/")
                        page.locator("#workspace-title").filter(has_text="LinkedIn").wait_for()
                        page.locator('[data-workspace="openai"]').click()
                        page.locator("#workspace-title").filter(has_text="Open AI").wait_for()
                        self.assertFalse(page.locator("#add-manual-job").is_visible())
                        self.assertFalse(page.locator("#sync-button").is_visible())
                        page.locator(".job-row-item").first.wait_for()
                        row_origins = page.locator(".job-row-item").evaluate_all(
                            "rows => rows.map(row => row.innerText)"
                        )
                        self.assertEqual(
                            len(row_origins), 1,
                            f"workspace={page.evaluate('state.workspace')}, requests={job_requests}, rows={row_origins}",
                        )
                        self.assertTrue(page.locator(".job-source-badge").is_visible())
                        page.locator("#application-link").wait_for(state="visible")
                        self.assertEqual(
                            page.locator("#linkedin-link").get_attribute("href"),
                            "https://www.linkedin.com/jobs/view/123456789/",
                        )
                        self.assertEqual(
                            page.locator("#application-link").get_attribute("href"),
                            "https://apply.example.org/start/123456789",
                        )
                        self.assertLessEqual(
                            page.evaluate("document.documentElement.scrollWidth"), 390,
                        )
                        page.screenshot(
                            path=str(Path(tempfile.gettempdir()) / "job-letter-studio-openai-mobile.png"),
                            full_page=False,
                        )
                        page.locator('[data-workspace="linkedin"]').click()
                        page.locator("#workspace-title").filter(has_text="LinkedIn").wait_for()
                        self.assertTrue(page.locator("#add-manual-job").is_visible())
                        self.assertTrue(page.locator("#sync-button").is_visible())
                        manual_row = page.locator(".job-row").filter(has_text="Manual saved role")
                        manual_row.wait_for()
                        manual_row.click()
                        page.locator("#detail-title").filter(has_text="Manual saved role").wait_for()
                        self.assertFalse(page.locator("#linkedin-link").is_visible())
                        self.assertFalse(page.locator("#application-link").is_visible())
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_ai_settings_and_regeneration_controls_render_in_chrome(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = JobStore(Path(temp_dir) / "data" / "jobs.sqlite3")
            store.sync_jobs([make_job(), make_job("987654321", title="Operations Analyst")])
            store.save_saved_sync_report({
                "started_at": "2026-10-03T14:30:00+00:00",
                "ended_at": "2026-10-03T14:30:18+00:00",
                "duration_ms": 18_000,
                "status": "complete",
                "membership_verified": True,
                "counts": {
                    "discovered": 35, "inserted": 1, "updated": 1,
                    "maintained": 33, "removed_from_linkedin": 0,
                    "with_letters": 1, "preserved_files": 1,
                    "unreadable_descriptions": 0, "not_imported": 0,
                },
                "events": [
                    {"type": "added", "title": "Product Analyst", "company": "Acme",
                     "changed_fields": [], "timestamp": "2026-10-03T14:30:10+00:00"},
                    {"type": "updated", "title": "Operations Analyst", "company": "Beta",
                     "changed_fields": ["sede", "descrizione"], "timestamp": "2026-10-03T14:30:14+00:00"},
                ],
                "unreadable": [],
                "read_outcomes": [{
                    "linkedin_job_id": "987654321", "title": "Operations Analyst",
                    "status": "read", "page_opened": True, "detail_opened": True,
                    "description_read_status": "complete", "attempts": 2,
                    "duration_ms": 1250, "import_status": "updated",
                }],
                "request_id": "report-smoke-test",
            })
            connection = store._connection()
            try:
                connection.execute("UPDATE jobs SET first_saved_at=? WHERE linkedin_job_id=?",
                                   ("2026-09-28T12:00:00+00:00", "123456789"))
                connection.execute("UPDATE jobs SET first_saved_at=? WHERE linkedin_job_id=?",
                                   ("2026-09-27T12:00:00+00:00", "987654321"))
                connection.commit()
            finally:
                connection.close()
            store.save_cover_letter_file(
                "123456789", original_filename="existing-letter.pdf",
                file_format="pdf", contents=b"%PDF-test",
            )
            store.save_job_materials("123456789", cover_letter_text="Dear hiring team,")
            server = DashboardHTTPServer(("127.0.0.1", 0), store,
                profile_directory=Path(temp_dir) / "data" / "user_profile")
            server.application_data.save_research(
                "123456789", state="READY", summary="Verified research summary about the company.",
                facts=[{"claim": "A documented company initiative.", "kind": "documented_fact",
                        "source_urls": ["https://company.example/news"]}],
                sources=[{"url": "https://company.example/news", "title": "Company newsroom"}],
                search_calls=1,
            )
            server.candidate_profile.save("Pietro-CV-demo.txt", "txt", b"Verified candidate evidence.")
            from tests.test_local_decisions import FakeRouter
            server.generation.local_decisions.router_factory = FakeRouter
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    try:
                        browser = playwright.chromium.launch(channel="chrome", headless=True)
                    except Exception as error:
                        self.skipTest(f"Chrome is not available for the smoke test: {type(error).__name__}")
                    try:
                        page = browser.new_page()
                        page.set_viewport_size({"width":390,"height":844})
                        errors: list[str] = []
                        api_failures: list[str] = []
                        page.on("pageerror", lambda error: errors.append(str(error)))

                        def check_api_response(response):
                            if "/api/" not in response.url or response.status < 400:
                                return
                            expected_missing_setup = (
                                response.status == 409 and
                                response.url.split("?", 1)[0].endswith("/regenerate")
                            )
                            if not expected_missing_setup:
                                api_failures.append(f"{response.status} {response.url}")

                        page.on("response", check_api_response)
                        page.goto(f"http://127.0.0.1:{server.server_port}/")
                        page.locator("#detail-title").wait_for()
                        page.evaluate("""() => {
                          const container = document.createElement('div');
                          container.id = 'laya-summary-smoke';
                          document.body.append(container);
                          renderGenerationSelection(container, {
                            state: 'READY', elapsed_seconds: 1.2,
                            knowledge_selection_method: 'preselezione locale',
                            candidate_excerpts: [{source: 'CV'}], selected_examples: ['example.txt'],
                            preprocessing_seconds: 0.4, ranking_seconds: 0.1,
                            laya_inference_seconds: 0.5, candidate_source_chars: 500,
                          });
                        }""")
                        self.assertIn("contesto 1.20s", page.locator("#laya-summary-smoke > p").inner_text())
                        self.assertIn("1 prove CV", page.locator("#laya-summary-smoke > p").inner_text())
                        self.assertFalse(page.locator("#laya-summary-smoke details").get_attribute("open"))
                        page.locator("#laya-summary-smoke details > summary").click()
                        self.assertIn("inferenza Laya 0.50s", page.locator("#laya-summary-smoke .generation-selection-detail-content").inner_text())
                        self.assertIn("superano il totale", page.evaluate("formatGenerationTimingDelta(1000, 1500)"))
                        self.assertIn("Tempo non attribuito", page.evaluate("formatGenerationTimingDelta(1500, 1000)"))
                        self.assertEqual(page.locator("text=File della candidatura").count(), 0)
                        self.assertEqual(page.locator("#export-letter-text").inner_text(), "Scarica PDF")
                        self.assertEqual(page.locator("#export-letter-word").inner_text(), "Scarica Word")
                        self.assertIsNone(page.locator("#cover-letter-information").get_attribute("open"))
                        page.locator("#cover-letter-information > summary").click()
                        self.assertEqual(page.locator("#cover-letter-information .laya-card").count(), 1)
                        self.assertEqual(page.locator("#cover-letter-information .research-disclosure").count(), 1)
                        self.assertEqual(page.locator("#cover-letter-information .personalization-disclosure").count(), 1)
                        self.assertEqual(page.locator("#detail-actions #mark-application-sent").count(), 1)
                        self.assertEqual(page.locator("#application-tracker #mark-application-sent").count(), 0)
                        self.assertEqual(page.locator("#opportunity-summary").count(), 0)
                        self.assertIsNone(page.locator(".research-disclosure").get_attribute("open"))
                        self.assertIn("2 tentativi", page.locator("#saved-sync-read-events").inner_text())
                        self.assertIn("1.25 s", page.locator("#saved-sync-read-events").inner_text())
                        self.assertEqual(page.locator("#detail-title").inner_text(), "Data Analyst")
                        page.set_viewport_size({"width":1280,"height":1000})
                        self.assertTrue(page.evaluate("() => { const a=document.querySelector('.original-description'); const b=document.querySelector('.research-disclosure'); return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING); }"))
                        page.locator(".research-disclosure > summary").click()
                        self.assertGreater(page.locator("#research-sources a").count(), 0)
                        page.locator(".research-disclosure > summary").click()
                        editor = page.locator("#cover-letter-text")
                        self.assertEqual(editor.get_attribute("rows"), "24")
                        self.assertGreater(editor.bounding_box()["height"], 540)
                        self.assertEqual(editor.evaluate("el => getComputedStyle(el).resize"), "vertical")
                        with page.expect_download() as download_info:
                            page.locator("#export-letter-text").click()
                        self.assertEqual(download_info.value.suggested_filename, "Example Corp - Data Analyst - 123456789.pdf")
                        self.assertEqual(page.evaluate("[jobDocumentBaseName(state.detail.job) + '.pdf', jobDocumentBaseName(state.detail.job) + '.docx']"), ["Example Corp - Data Analyst - 123456789.pdf", "Example Corp - Data Analyst - 123456789.docx"])
                        self.assertEqual(len(store.get_job_bundle("123456789")["cover_letters"]), 2)
                        writes = page.evaluate("async () => { const files=new Set(['Example Corp - Data Analyst - 123456789.pdf']); const written=[]; const folder={getFileHandle:async(name,options)=>{if(options && options.create===false){if(files.has(name))return {}; throw new DOMException('missing','NotFoundError');} if(files.has(name))throw new Error('overwrite'); files.add(name); return {createWritable:async()=>({write:async()=>written.push(name),close:async()=>{}})};}}; state.directoryHandle={name:'Selected',queryPermission:async()=> 'granted',getDirectoryHandle:async()=>folder}; const first=await deliverDocument(new Blob(['letter']),'Example Corp - Data Analyst - 123456789.pdf',state.detail.job); const second=await deliverDocument(new Blob(['letter']),'Example Corp - Data Analyst - 123456789.pdf',state.detail.job); state.directoryHandle=null; return [first,second,written]; }")
                        self.assertEqual(writes, ["directory", "directory", ["Example Corp - Data Analyst - 123456789 (2).pdf", "Example Corp - Data Analyst - 123456789 (3).pdf"]])
                        editor.fill("Unsaved draft\n" * 35)
                        editor.focus()
                        editor.evaluate("el => { el.setSelectionRange(4, 11); el.scrollTop = 120; }")
                        before = editor.evaluate("el => [el.value, el.selectionStart, el.selectionEnd, el.scrollTop]")
                        page.evaluate("renderDetail(false)")
                        self.assertEqual(editor.evaluate("el => [el.value, el.selectionStart, el.selectionEnd, el.scrollTop]"), before)
                        self.assertTrue(editor.evaluate("el => document.activeElement === el"))
                        page.evaluate("selectJob(state.selectedId)")
                        self.assertEqual(editor.input_value(), before[0])
                        # Save the captured snapshot while another keystroke arrives.
                        page.evaluate("""() => {
                          window.originalDraftRequest = request;
                          request = (path, options) => path.endsWith('/materials')
                            ? new Promise(resolve => { window.releaseDraftSave = resolve; })
                            : window.originalDraftRequest(path, options);
                          saveMaterials();
                        }""")
                        editor.fill("New typing during save")
                        page.evaluate("window.releaseDraftSave({saved:true})")
                        page.wait_for_timeout(30)
                        self.assertEqual(editor.input_value(), "New typing during save")
                        self.assertTrue(page.evaluate("state.dirty"))
                        page.evaluate("request = window.originalDraftRequest; state.dirty = false")
                        page.evaluate("selectJob(state.selectedId)")
                        page.evaluate("""() => {
                          window.delayedOldDetail = state.detail;
                          window.originalSelectionRequest = request;
                          request = (path, options) => path === '/api/jobs/123456789'
                            ? new Promise(resolve => { window.releaseOldDetail = resolve; })
                            : window.originalSelectionRequest(path, options);
                          selectJob('123456789');
                        }""")
                        page.evaluate("selectJob('987654321')")
                        self.assertEqual(page.locator("#detail-title").inner_text(), "Operations Analyst")
                        page.evaluate("window.releaseOldDetail(window.delayedOldDetail)")
                        page.wait_for_timeout(30)
                        self.assertEqual(page.locator("#detail-title").inner_text(), "Operations Analyst")
                        page.evaluate("() => { request = window.originalSelectionRequest; }")
                        page.evaluate("selectJob('123456789')")
                        page.set_viewport_size({"width":390,"height":844})
                        self.assertLess(editor.bounding_box()["height"], 450)
                        self.assertLessEqual(editor.bounding_box()["width"], 390)
                        self.assertTrue(editor.evaluate("el => el.scrollWidth <= el.clientWidth"))
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"),
                                             page.evaluate("document.documentElement.clientWidth"))

                        first_row_text = page.locator(".job-row-item").first.inner_text()
                        self.assertIn("Salvata il 28/09/2026", first_row_text)
                        self.assertIn("Cover letter creata", first_row_text)
                        self.assertNotIn("file", first_row_text.casefold())
                        self.assertIn("Cover letter da creare", page.locator(".job-row-item").nth(1).inner_text())
                        page.locator("#job-sort").select_option("oldest")
                        self.assertIn("Operations Analyst", page.locator(".job-row-item").first.inner_text())
                        self.assertEqual(page.locator("#detail-title").inner_text(), "Data Analyst")
                        page.locator("#job-sort").select_option("newest")
                        page.locator(".research-disclosure > summary").click()
                        self.assertIn("Verified research summary", page.locator("#research-summary").inner_text())
                        self.assertEqual(page.locator(".research-source-link").first.get_attribute("href"),
                                         "https://company.example/news")
                        self.assertFalse(page.locator(".chat-card").count())
                        self.assertTrue(page.locator("#regenerate-job").is_visible())
                        self.assertEqual(page.locator("#availability-note").count(), 0)
                        regenerate_box = page.locator("#regenerate-job").bounding_box()
                        linkedin_box = page.locator("#linkedin-link").bounding_box()
                        sent_box = page.locator("#mark-application-sent").bounding_box()
                        self.assertEqual(regenerate_box["width"], linkedin_box["width"])
                        self.assertEqual(regenerate_box["width"], sent_box["width"])
                        self.assertEqual(regenerate_box["height"], linkedin_box["height"])
                        self.assertEqual(regenerate_box["height"], sent_box["height"])
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
                        self.assertEqual(page.locator("#letter-history-title").count(), 0)
                        self.assertEqual(page.locator(".letter-version-list").count(), 0)
                        page.locator(".job-row").nth(1).click()
                        page.locator("#detail-title").filter(has_text="Operations Analyst").wait_for()
                        page.set_viewport_size({"width": 390, "height": 844})
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
                        page.set_viewport_size({"width": 1280, "height": 900})
                        page.locator("#settings-button").click()
                        self.assertFalse(page.locator(".ai-setting").get_attribute("open"))
                        page.locator(".ai-setting > summary").click()
                        page.locator("#ai-max-auto-job-age-days").wait_for(state="visible")
                        self.assertLess(page.locator(".bulk-operations-setting").evaluate("el => el.getBoundingClientRect().top"), page.locator(".ai-setting").evaluate("el => el.getBoundingClientRect().top"))
                        self.assertLess(page.locator("#bulk-regeneration-mode").evaluate("el => el.getBoundingClientRect().top"), page.locator("#recover-saved-jobs").evaluate("el => el.getBoundingClientRect().top"))
                        page.locator(".laya-setting > summary").click()
                        page.locator(".laya-setting details > summary").click()
                        page.locator("#laya-cost-estimate").filter(has_text="100 candidature").wait_for()
                        self.assertIn("1.000", page.locator("#laya-cost-estimate").inner_text())
                        self.assertEqual(page.locator("#laya-enabled").count(), 0)
                        self.assertEqual(page.locator("#save-laya-settings").count(), 0)
                        page.locator("#laya-status").filter(has_text="Laya").wait_for()
                        self.assertEqual(page.locator("#ai-max-auto-job-age-days").input_value(), "90")
                        compact_columns = page.locator(".ai-settings-grid").evaluate(
                            "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                        )
                        self.assertGreaterEqual(compact_columns, 3)
                        page.set_viewport_size({"width": 390, "height": 844})
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
                        page.set_viewport_size({"width": 1280, "height": 900})
                        page.locator(".agents-setting > summary").click()
                        try:
                            page.locator("#ai-agent-list .agent-editor-card").first.wait_for(timeout=5_000)
                        except Exception as error:
                            self.fail(f"Agenti non caricati: {error}; UI={page.locator('#ai-connection-status').inner_text()}; JS={errors}; API={api_failures}")
                        self.assertEqual(page.locator("#ai-agent-list .agent-editor-card").count(), 4)
                        self.assertEqual(page.locator("#ai-model option").count(), 5)
                        self.assertTrue(page.locator("#bulk-regeneration-scope").is_visible())
                        self.assertTrue(page.locator("#bulk-regeneration-mode").is_visible())
                        self.assertTrue(page.locator("#recover-saved-jobs").is_visible())
                        page.locator("#saved-sync-report").wait_for(state="visible")
                        self.assertIn("aggiunti 1", page.locator("#saved-sync-report-summary").inner_text())
                        self.assertIn("Dettaglio modifiche (2)", page.locator("#saved-sync-report-details-title").inner_text())
                        page.locator("#saved-sync-report-details > summary").click()
                        self.assertIn("Product Analyst — Acme", page.locator("#saved-sync-report-events").inner_text())
                        self.assertIn("descrizione", page.locator("#saved-sync-report-events").inner_text())
                        self.assertIn("Ultima sincronizzazione completa riuscita", page.locator("#saved-sync-report-last-success").inner_text())
                        self.assertFalse(page.locator("#stale-generation-note").count())
                        self.assertTrue(page.locator("#copy-filtered-logs").count() == 1)
                        self.assertEqual(page.locator(".candidate-profile-setting").count(), 0)
                        self.assertEqual(page.locator(".candidate-context-setting .candidate-cv-block").count(), 1)
                        page.locator(".candidate-context-setting > summary").click()
                        self.assertIn("Pietro-CV-demo.txt", page.locator("#cv-status").inner_text())
                        page.locator(".knowledge-setting > summary").click()
                        page.locator("#knowledge-file").set_input_files({
                            "name": "Pietro style example.txt", "mimeType": "text/plain",
                            "buffer": b"A concise, evidence-led cover letter example.",
                        })
                        self.assertIn("Pietro style example.txt", page.locator("#knowledge-file-name").inner_text())
                        page.locator("#upload-knowledge").click()
                        page.locator("#knowledge-archive-summary").filter(has_text="1/10").wait_for()
                        self.assertFalse(page.locator("#knowledge-archive").evaluate("archive => archive.open"))
                        page.locator("#knowledge-archive > summary").click()
                        page.locator(".knowledge-item > summary").filter(has_text="Pietro style example.txt").wait_for()
                        page.locator("#add-ai-agent").click()
                        self.assertEqual(page.locator("#ai-agent-list .agent-editor-card").count(), 5)
                        page.locator("#ai-agent-list .agent-editor-card").last.locator("input[type=text]").first.fill("<img src=x onerror=alert(1)>")
                        page.locator("#ai-agent-list .agent-editor-card").last.locator("select").select_option("gpt-4.1-nano")
                        page.locator("#save-ai-agents").click()
                        page.get_by_text("Agenti aggiornati in AGENTS.md.").wait_for()
                        self.assertEqual(page.locator("#ai-agent-list img").count(), 0)
                        self.assertEqual(server.agent_prompt.agents()[-1]["model"], "gpt-4.1-nano")
                        page.locator("#close-settings").click()
                        page.locator(".laya-card > summary").click()
                        page.locator("#analyze-laya").click()
                        page.locator("#laya-result").filter(has_text="Pertinenza degli estratti CV: 90/100").wait_for()
                        page.set_viewport_size({"width": 390, "height": 844})
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
                        page.set_viewport_size({"width": 1280, "height": 900})
                        self.assertFalse(server.store.get_job_bundle("987654321")["cover_letters"])
                        page.locator("#regenerate-job").click()
                        page.locator("#settings-dialog").wait_for(state="visible")
                        page.locator("#close-settings").click()
                        self.assertEqual(page.locator(".job-row-menu-trigger").count(), 2)
                        page.once("dialog", lambda dialog: dialog.accept())
                        page.locator(".job-row-menu-trigger").first.click()
                        page.locator(".job-row-forget").first.click()
                        page.locator(".job-row-item").nth(1).wait_for(state="detached")
                        self.assertIsNone(server.store.get_job("123456789"))
                        self.assertIsNotNone(server.store.get_job("987654321"))
                        self.assertEqual(errors, [])
                        self.assertEqual(api_failures, [])
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
