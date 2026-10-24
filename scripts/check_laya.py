"""Explicit local-only Laya smoke check with synthetic data; never downloads weights."""
import json
import time
import argparse
import tempfile
import os
from pathlib import Path
import sys

os.environ["HF_HUB_OFFLINE"] = "1"

from laya import Router

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linkedin_connector.local_decisions import _local_checkpoint_directory

parser = argparse.ArgumentParser()
parser.add_argument("--workflow", action="store_true", help="Also exercise local preflight/cache with synthetic sources")
args = parser.parse_args()

started = time.monotonic()
model_directory = _local_checkpoint_directory()
if model_directory is None:
    raise SystemExit("Cached multilingual Laya checkpoint is not available")
router = Router(models={"multilingual": str(model_directory)}, device="cpu", max_loaded=1)
result = router.predict(
    {"job": "Data analyst: build Python dashboards and reports.",
     "candidate": "Built Python dashboards for operations reporting."},
    {"relevant": {"type": "noul", "instructions": "Does the candidate have experience relevant to the job?"}},
    model="multilingual", max_len=8192,
)
print(json.dumps({"answers": result["answers"], "routing": result.get("routing"),
                  "usage": result.get("usage"), "elapsed_seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
for candidate in ("Built Python dashboards for operations reporting.", "Baked bread and decorated wedding cakes."):
    comparison = router.predict({"job": "Data analyst: build Python dashboards and reports.", "candidate": candidate}, {
        "match": {"type": "choice", "instructions": "Evaluate whether the candidate experience is relevant to the job.",
                  "criteria": {"relevant": "The candidate has concrete experience relevant to this job.", "unrelated": "The candidate experience is unrelated to the job.", "uncertain": "There is insufficient information to judge relevance."}},
        "binary_descriptions": {"type": "noul", "instructions": "Does the candidate have relevant experience?", "criteria": {"true": "The candidate has relevant experience.", "false": "The candidate does not have relevant experience."}}
    }, model="multilingual", max_len=8192)
    print(json.dumps({"candidate_fixture": candidate, "comparison": comparison["answers"]}))
language = router.predict("Dear hiring team, I am applying for the Data Analyst role.", {
    "language": {"type": "choice", "instructions": "What language is the cover letter in?", "criteria": {"english": "The letter is written in English.", "other": "The letter is written in another language or unreadable."}}
}, model="multilingual", max_len=8192)
print(json.dumps({"language_fixture": language["answers"]}))
if args.workflow:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from linkedin_connector.database import JobStore
    from linkedin_connector.application_store import ApplicationStore
    from linkedin_connector.local_decisions import LocalDecisionEngine
    from tests.test_database import make_job
    with tempfile.TemporaryDirectory() as directory:
        store = JobStore(Path(directory) / "jobs.sqlite3")
        store.sync_jobs([make_job(description="Data analyst: build Python dashboards and reports.")])
        application = ApplicationStore(store)
        application.add_knowledge("style.txt", "txt", b"Dear team", "Dear hiring team, I am applying for the analyst role. I developed reporting tools in Python. Thank you for considering my application.")
        engine = LocalDecisionEngine(store, application, Path(directory) / "laya.json")
        engine._router = router
        engine.save_settings({"enabled": True})
        job = store.get_job("123456789")
        cv_fixture = "Built Python dashboards for operations reporting."
        preflight_started = time.monotonic()
        preflight = engine.analyze(job, cv_fixture, "", detailed=False)
        preflight_elapsed = time.monotonic() - preflight_started
        preflight_cached = engine.analyze(job, "Changed CV fixture", "Changed context fixture", detailed=False)
        detailed = engine.analyze(job, cv_fixture, "")
        review = engine.review_draft(job, "Dear hiring team at Example Corp, I am applying for the Data Analyst role. I built Python dashboards for reporting.")
        detailed_cached = engine.analyze(job, cv_fixture, "")
        research_fixture = {
            "summary": "A synthetic company research summary.",
            "sources": [{"url": "https://example.com/company", "title": "Synthetic official company page"}],
            "facts": [{"claim": "The company builds analytics products for business teams.",
                       "kind": "documented_fact", "source_urls": ["https://example.com/company"]}],
            "gaps": "",
        }
        page_fixture = [{"url": "https://example.com/company", "title": "Synthetic official company page",
                         "state": "READY", "error_code": "",
                         "text": "The company builds analytics products and reporting tools for business teams."}]
        long_cv_fixture = ("Built Python dashboards and reporting workflows for business teams. " * 120)
        generation_context = engine.prepare_generation_context(job, long_cv_fixture, "", research_fixture, page_fixture)
        cached_generation_context = engine.prepare_generation_context(job, long_cv_fixture, "", research_fixture, page_fixture)
        print(json.dumps({
            "preflight": {key: preflight.get(key) for key in ("state", "analysis_level", "role", "role_confidence", "elapsed_seconds", "error_code")},
            "preflight_wall_seconds": round(preflight_elapsed, 2),
            "preflight_cache_reused_after_candidate_change": preflight_cached.get("cached"),
            "detailed": {key: detailed.get(key) for key in ("state", "analysis_level", "match_score", "knowledge_selection_method", "elapsed_seconds", "error_code")},
            "detailed_cache_reused": detailed_cached.get("cached"),
            "draft_review_advisory_only": review,
            "generation_context": generation_context["summary"],
            "generation_context_cache_reused": cached_generation_context["summary"].get("cached"),
        }, ensure_ascii=False))
        if (preflight["state"] != "READY" or preflight["analysis_level"] != "preflight" or
                not preflight_cached.get("cached") or detailed["state"] != "READY" or
                not detailed_cached.get("cached") or generation_context["summary"]["state"] != "READY" or
                not cached_generation_context["summary"].get("cached")):
            raise SystemExit("Real local preflight/ranking/cache failed")
