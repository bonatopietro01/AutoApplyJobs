"""Command-line entry point for the LinkedIn Job Connector."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys

from linkedin_connector.browser import authenticate
from linkedin_connector.database import DEFAULT_DATABASE_PATH, JobStore, JobStoreError
from linkedin_connector.observability import configure_logging, log_event, log_exception
from linkedin_connector.reader import (
    LinkedInReadError,
    read_all_saved_jobs,
    read_one_saved_job,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="LinkedIn Job Connector")
    read_mode = parser.add_mutually_exclusive_group()
    read_mode.add_argument(
        "--read-one-saved-job",
        action="store_true",
        help="read exactly one saved job and print its fields as JSON",
    )
    read_mode.add_argument(
        "--read-all-saved-jobs",
        action="store_true",
        help="read all saved jobs and complete visible descriptions sequentially",
    )
    read_mode.add_argument(
        "--sync-saved-jobs",
        action="store_true",
        help="read all saved jobs and persist complete records in the local SQLite database",
    )
    read_mode.add_argument(
        "--dashboard",
        action="store_true",
        help="open the local jobs and cover-letter dashboard",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="localhost port for --dashboard (default: 8765)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="with --read-all-saved-jobs, print metadata and description lengths only",
    )
    args = parser.parse_args()

    if args.summary and not args.read_all_saved_jobs:
        parser.error("--summary can only be used with --read-all-saved-jobs")
    if not args.dashboard and args.port != 8765:
        parser.error("--port can only be used with --dashboard")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    configure_logging(DEFAULT_DATABASE_PATH.parent / "logs")
    log_event(
        logging.INFO,
        "application",
        "application.command_started",
        "Application command started.",
    )
    if args.dashboard:
        from linkedin_connector.dashboard import serve_dashboard

        try:
            serve_dashboard(port=args.port, open_browser=True)
        except OSError as error:
            logging.error("Could not start the local dashboard; see local diagnostics.")
            log_exception(
                "dashboard",
                "dashboard.start_failed",
                "Could not start the local dashboard.",
                error=error,
            )
            return 1
        return 0
    if args.read_one_saved_job:
        try:
            job = read_one_saved_job()
        except LinkedInReadError as error:
            logging.error("Could not read the saved LinkedIn job; see local diagnostics.")
            log_exception(
                "linkedin_reader", "reader.single_failed", "Could not read one saved job.", error=error
            )
            return 1
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        print(json.dumps(job.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.read_all_saved_jobs:
        try:
            jobs = read_all_saved_jobs()
        except LinkedInReadError as error:
            logging.error("Could not read the saved LinkedIn jobs; see local diagnostics.")
            log_exception(
                "linkedin_reader", "reader.full_read_failed", "Could not read saved jobs.", error=error
            )
            return 1
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        if args.summary:
            result = {
                "job_count": len(jobs),
                "complete_description_count": sum(
                    job.description_complete for job in jobs
                ),
                "jobs": [
                    {
                        "linkedin_job_id": job.linkedin_job_id,
                        "title": job.title,
                        "company": job.company,
                        "location": job.location,
                        "description_characters": len(job.description or ""),
                        "description_complete": job.description_complete,
                    }
                    for job in jobs
                ],
            }
        else:
            result = [job.to_dict() for job in jobs]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.sync_saved_jobs:
        try:
            jobs = read_all_saved_jobs()
            counts = JobStore().sync_jobs(jobs)
        except (LinkedInReadError, JobStoreError, sqlite3.Error) as error:
            logging.error("The saved-jobs synchronization failed; see local diagnostics.")
            log_exception(
                "linkedin_sync",
                "sync.cli_failed",
                "Full saved-jobs synchronization failed.",
                error=error,
            )
            return 1
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return 0

    return 0 if authenticate() else 1


if __name__ == "__main__":
    raise SystemExit(main())
