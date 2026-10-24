"""MCP server exposing locally persisted saved jobs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import logging

from mcp.server import MCPServer

from linkedin_connector.database import DEFAULT_DATABASE_PATH, JobStore
from linkedin_connector.letter_documents import render_letter_document
from linkedin_connector.observability import configure_logging, log_event


def _configured_database_path() -> Path:
    configured = os.environ.get("LINKEDIN_CONNECTOR_DATABASE_PATH", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATABASE_PATH


def create_server(store: JobStore | None = None) -> MCPServer:
    """Build an MCP server whose tools never contact LinkedIn."""
    job_store = store or JobStore(_configured_database_path())
    server = MCPServer(
        name="linkedin-job-connector",
        title="LinkedIn Job Tracker",
        description="Search and manage the user's manually saved LinkedIn jobs.",
        instructions=(
            "Read from the local SQLite snapshot only. These tools do not browse LinkedIn. "
            "Only mark a job processed after the user confirms it has been handled."
        ),
        version="1.0.0",
    )

    @server.tool(structured_output=True)
    def get_new_jobs(limit: int = 100) -> dict[str, Any]:
        """List saved jobs whose local status is NEW."""
        jobs = job_store.get_new_jobs(limit=limit)
        return {"count": len(jobs), "jobs": jobs}

    @server.tool(structured_output=True)
    def get_saved_jobs(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """List locally saved jobs, optionally filtering by NEW, PROCESSED, or IGNORED."""
        jobs = job_store.get_saved_jobs(status=status, limit=limit)
        return {"count": len(jobs), "jobs": jobs}

    @server.tool(structured_output=True)
    def get_job(job_id: str) -> dict[str, Any]:
        """Get one saved job by its LinkedIn job ID."""
        job = job_store.get_job(job_id)
        return {"found": job is not None, "job": job}

    @server.tool(structured_output=True)
    def search_jobs(query: str, limit: int = 100) -> dict[str, Any]:
        """Search saved-job titles, companies, locations, and descriptions."""
        jobs = job_store.search_jobs(query, limit=limit)
        return {"count": len(jobs), "jobs": jobs}

    @server.tool(structured_output=True)
    def mark_processed(job_id: str) -> dict[str, Any]:
        """Mark one saved job PROCESSED; this changes only its local database status."""
        job = job_store.mark_processed(job_id)
        return {"success": True, "job": job}

    @server.tool(structured_output=True)
    def save_prepared_job(
        title: str, company: str, location: str, description: str,
        application_url: str, cover_letter_text: str,
        role_analysis: str = "", company_analysis: str = "",
        personalization_notes: str = "", source_references: list[str] | None = None,
    ) -> dict[str, Any]:
        """Save one complete prepared opportunity and its local PDF/DOCX without API generation."""
        # Render before touching the database so missing document dependencies or invalid
        # letter content cannot leave a partially created job.
        pdf = render_letter_document(cover_letter_text, "pdf")
        docx = render_letter_document(cover_letter_text, "docx")
        job, created = job_store.create_chatgpt_job(
            title=title, company=company, location=location, description=description,
            application_url=application_url, cover_letter_text=cover_letter_text,
            role_analysis=role_analysis, company_analysis=company_analysis,
            personalization_notes=personalization_notes, source_references=source_references,
            document_contents={"pdf": pdf, "docx": docx},
        )
        if not created:
            return {"success": True, "created": False, "job": job,
                    "message": "Duplicate canonical URL; existing job and materials were preserved."}
        bundle = job_store.get_job_bundle(str(job["linkedin_job_id"]))
        return {"success": True, "created": True, "job": job,
                "files": bundle["cover_letters"] if bundle else []}

    return server


def main() -> None:
    """Run the local stdio MCP server (the MCP host launches this process)."""
    database_path = _configured_database_path()
    configure_logging(database_path.parent / "logs")
    log_event(
        logging.INFO,
        "mcp_server",
        "mcp.started",
        "Local stdio MCP server started.",
    )
    create_server(JobStore(database_path)).run(transport="stdio")


if __name__ == "__main__":
    main()
