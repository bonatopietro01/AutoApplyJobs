"""In-memory protocol tests for the local MCP tools."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client, StdioServerParameters

from linkedin_connector.database import JobStore
from linkedin_connector.application_store import ApplicationStore
from linkedin_connector.profile import CandidateProfileStore
from tests.test_database import make_job
from mcp_server.server import create_server


class MCPServerTests(unittest.TestCase):
    def test_cover_letter_context_tool_returns_bounded_candidate_and_matching_style_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "user_profile"
            profile.mkdir()
            (profile / "CANDIDATE_CONTEXT.md").write_text("context-marker-83c1", encoding="utf-8")
            (profile / "AGENTS.md").write_text(
                "# Roles\n\n## Internal roles and workflow\n\n"
                "### 1. Writer\n<!-- agent:id=writer phase=writing enabled=true -->\n\n"
                "Use a concise, factual tone.\n",
                encoding="utf-8",
            )
            store = JobStore(root / "jobs.sqlite3")
            CandidateProfileStore(root).save("Candidate.txt", "txt", b"cv-marker-3a24")
            application_data = ApplicationStore(store)
            style_example = application_data.add_knowledge(
                "Preferred letter.txt", "txt", b"style", "Data Analyst letters use a direct opening."
            )
            server = create_server(store, profile_directory=profile)

            async def exercise() -> None:
                async with Client(server) as client:
                    names = {tool.name for tool in (await client.list_tools()).tools}
                    self.assertIn("get_cover_letter_context", names)
                    result = await client.call_tool("get_cover_letter_context", {
                        "title": "Data Analyst", "company": "Example Corp", "location": "Zurich",
                        "description": "Analyze data and build useful reports for business teams.",
                    })
                    context = result.structured_content
                    self.assertEqual(context["cv_text"], "cv-marker-3a24")
                    self.assertEqual(context["candidate_context"], "context-marker-83c1")
                    self.assertIn("concise, factual tone", context["writing_instructions"])
                    self.assertEqual(context["style_examples"][0]["filename"], "Preferred letter.txt")
                    self.assertIn("style guide", context["style_examples_policy"].casefold())
                    application_data.delete_knowledge(style_example["id"])
                    without_examples = await client.call_tool("get_cover_letter_context", {
                        "title": "Data Analyst", "company": "Example Corp", "location": "Zurich",
                        "description": "Analyze data and build useful reports for business teams.",
                    })
                    self.assertEqual(without_examples.structured_content["style_examples"], [])
                    self.assertIn(
                        "Esempi stilistici cover letter non disponibili",
                        without_examples.structured_content["missing_context"],
                    )
                    oversized = await client.call_tool("get_cover_letter_context", {
                        "title": "Data Analyst", "company": "Example Corp", "location": "Zurich",
                        "description": "x" * 50_001,
                    })
                    self.assertTrue(oversized.is_error)

            asyncio.run(exercise())

    def test_prepared_job_tool_saves_letter_documents_and_does_not_queue_api_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JobStore(Path(temp_dir) / "jobs.sqlite3")
            server = create_server(store)
            arguments = {
                "title": "Research Analyst", "company": "Example Labs", "location": "Zurich",
                "description": "Build analytical reports and improve data quality for research teams.",
                "job_url": "https://jobs.example.org/role/42?utm_source=chatgpt",
                "application_url": "https://apply.example.org/start/42",
                "cover_letter_text": "Dear hiring team, I am applying with relevant experience.",
                "role_analysis": "Good match", "personalization_notes": "Work permit to verify",
                "source_references": ["https://jobs.example.org/role/42"],
            }

            async def exercise() -> None:
                async with Client(server) as client:
                    tools = await client.list_tools()
                    self.assertIn("save_prepared_job", {tool.name for tool in tools.tools})
                    unchecked = await client.call_tool("check_prepared_job_url", {
                        "job_url": arguments["job_url"],
                    })
                    self.assertFalse(unchecked.structured_content["found"])
                    result = await client.call_tool("save_prepared_job", arguments)
                    self.assertTrue(result.structured_content["created"])
                    job_id = result.structured_content["job"]["linkedin_job_id"]
                    bundle = store.get_job_bundle(job_id)
                    self.assertEqual({item["file_format"] for item in bundle["cover_letters"]}, {"pdf", "docx"})
                    self.assertEqual(
                        {Path(item["relative_path"]).name for item in store._fetch_all(
                            "SELECT relative_path FROM cover_letters WHERE linkedin_job_id = ?", (job_id,)
                        )},
                        {"cover-letter-v01.pdf", "cover-letter-v01.docx"},
                    )
                    self.assertEqual(
                        {item["original_filename"] for item in bundle["cover_letters"]},
                        {"cover-letter.pdf", "cover-letter.docx"},
                    )
                    self.assertEqual(bundle["materials"]["cover_letter_text"], arguments["cover_letter_text"])
                    self.assertEqual(bundle["job"]["normalized_url"], "https://jobs.example.org/role/42")
                    self.assertEqual(bundle["job"]["linkedin_url"], "https://jobs.example.org/role/42")
                    self.assertEqual(bundle["job"]["application_url"], "https://apply.example.org/start/42")
                    self.assertEqual(bundle["job"]["suppress_auto_generation"], 1)
                    checked = await client.call_tool("check_prepared_job_url", {
                        "job_url": arguments["job_url"],
                    })
                    self.assertTrue(checked.structured_content["found"])
                    self.assertEqual(checked.structured_content["job"]["linkedin_job_id"], job_id)
                    duplicate = await client.call_tool("save_prepared_job", {**arguments, "cover_letter_text": "New text"})
                    self.assertFalse(duplicate.structured_content["created"])
                    self.assertEqual(store.get_job_bundle(job_id)["materials"]["cover_letter_text"], arguments["cover_letter_text"])
                    with patch("mcp_server.server.render_letter_document") as renderer:
                        oversized_letter = await client.call_tool(
                            "save_prepared_job", {**arguments, "cover_letter_text": "x" * 60_001}
                        )
                        self.assertTrue(oversized_letter.is_error)
                        renderer.assert_not_called()
                    with patch("mcp_server.server.render_letter_document") as renderer:
                        too_many_sources = await client.call_tool(
                            "save_prepared_job", {**arguments, "source_references": ["https://example.org"] * 51}
                        )
                        self.assertTrue(too_many_sources.is_error)
                        renderer.assert_not_called()
                    with patch("mcp_server.server.render_letter_document") as renderer:
                        oversized_url = await client.call_tool(
                            "save_prepared_job", {**arguments, "job_url": "https://example.org/" + "x" * 2_000}
                        )
                        self.assertTrue(oversized_url.is_error)
                        renderer.assert_not_called()

            asyncio.run(exercise())

    def test_tools_read_local_snapshot_and_mark_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JobStore(Path(temp_dir) / "jobs.sqlite3")
            store.sync_jobs([make_job()])
            server = create_server(store)

            async def exercise_tools() -> None:
                async with Client(server) as client:
                    tools = await client.list_tools()
                    self.assertEqual(
                        {tool.name for tool in tools.tools},
                        {"get_new_jobs", "get_saved_jobs", "get_job", "search_jobs", "mark_processed",
                         "get_cover_letter_context", "check_prepared_job_url", "save_prepared_job"},
                    )

                    new_jobs = await client.call_tool("get_new_jobs", {})
                    self.assertEqual(new_jobs.structured_content["count"], 1)
                    self.assertEqual(new_jobs.structured_content["jobs"][0]["linkedin_job_id"], "123456789")

                    found = await client.call_tool("get_job", {"job_id": "123456789"})
                    self.assertTrue(found.structured_content["found"])

                    search = await client.call_tool("search_jobs", {"query": "reports"})
                    self.assertEqual(search.structured_content["count"], 1)

                    changed = await client.call_tool("mark_processed", {"job_id": "123456789"})
                    self.assertTrue(changed.structured_content["success"])

                    new_jobs = await client.call_tool("get_new_jobs", {})
                    self.assertEqual(new_jobs.structured_content["count"], 0)
                    processed = await client.call_tool(
                        "get_saved_jobs", {"status": "PROCESSED"}
                    )
                    self.assertEqual(processed.structured_content["count"], 1)

            asyncio.run(exercise_tools())

    def test_stdio_entrypoint_starts_and_serves_tools(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "jobs.sqlite3"
            JobStore(database_path).sync_jobs([make_job()])
            server_process = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcp_server.server"],
                env={**os.environ, "LINKEDIN_CONNECTOR_DATABASE_PATH": str(database_path)},
                cwd=project_dir,
            )

            async def exercise_process() -> None:
                async with Client(server_process) as client:
                    tools = await client.list_tools()
                    self.assertIn("get_new_jobs", {tool.name for tool in tools.tools})
                    result = await client.call_tool("get_new_jobs", {"limit": 1})
                    self.assertEqual(result.structured_content["count"], 1)
                    self.assertEqual(result.structured_content["jobs"][0]["linkedin_job_id"], "123456789")

            asyncio.run(exercise_process())


if __name__ == "__main__":
    unittest.main()
