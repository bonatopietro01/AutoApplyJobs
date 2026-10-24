"""In-memory protocol tests for the local MCP tools."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import Client, StdioServerParameters

from linkedin_connector.database import JobStore
from tests.test_database import make_job
from mcp_server.server import create_server


class MCPServerTests(unittest.TestCase):
    def test_prepared_job_tool_saves_letter_documents_and_does_not_queue_api_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JobStore(Path(temp_dir) / "jobs.sqlite3")
            server = create_server(store)
            arguments = {
                "title": "Research Analyst", "company": "Example Labs", "location": "Zurich",
                "description": "Build analytical reports and improve data quality for research teams.",
                "application_url": "https://jobs.example.org/role/42?utm_source=chatgpt",
                "cover_letter_text": "Dear hiring team, I am applying with relevant experience.",
                "role_analysis": "Good match", "personalization_notes": "Work permit to verify",
                "source_references": ["https://jobs.example.org/role/42"],
            }

            async def exercise() -> None:
                async with Client(server) as client:
                    tools = await client.list_tools()
                    self.assertIn("save_prepared_job", {tool.name for tool in tools.tools})
                    result = await client.call_tool("save_prepared_job", arguments)
                    self.assertTrue(result.structured_content["created"])
                    job_id = result.structured_content["job"]["linkedin_job_id"]
                    bundle = store.get_job_bundle(job_id)
                    self.assertEqual({item["file_format"] for item in bundle["cover_letters"]}, {"pdf", "docx"})
                    self.assertEqual(bundle["materials"]["cover_letter_text"], arguments["cover_letter_text"])
                    self.assertEqual(bundle["job"]["application_url"], "https://jobs.example.org/role/42")
                    self.assertEqual(bundle["job"]["suppress_auto_generation"], 1)
                    duplicate = await client.call_tool("save_prepared_job", {**arguments, "cover_letter_text": "New text"})
                    self.assertFalse(duplicate.structured_content["created"])
                    self.assertEqual(store.get_job_bundle(job_id)["materials"]["cover_letter_text"], arguments["cover_letter_text"])

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
                        {"get_new_jobs", "get_saved_jobs", "get_job", "search_jobs", "mark_processed", "save_prepared_job"},
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
