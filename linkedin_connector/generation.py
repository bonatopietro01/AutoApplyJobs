"""Background, bounded AI generation for individual locally saved jobs."""

from __future__ import annotations

import logging
import hashlib
import json
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from linkedin_connector.ai_config import AISettingsStore, APIKeyStore
from linkedin_connector.ai_provider import AIProviderError, OpenAIProvider, WEB_SEARCH_MODEL, merge_stage_output
from linkedin_connector.application_store import ApplicationStore
from linkedin_connector.local_decisions import LocalDecisionEngine
from linkedin_connector.database import JobStore, JobStoreError
from linkedin_connector.generation_store import GenerationStore
from linkedin_connector.letter_documents import render_letter_documents
from linkedin_connector.observability import bind_request_id, log_event, new_request_id
from linkedin_connector.profile import AgentPromptStore, CandidateProfileStore, LocalProfileError
from linkedin_connector.research_pages import cached_research_pages

_RESEARCH_FAILURE_CACHE_CODES = {
    "network", "rate_limit", "http_error", "provider_error", "invalid_response", "incomplete",
}


def _safe_exception_details(error: BaseException) -> dict[str, str]:
    """Keep exception type and code frames, but omit arbitrary exception text and locals."""
    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    return {
        "exception_type": type(error).__name__[:100],
        "traceback_frames": "".join(traceback.format_list(frames))[-6_000:],
    }


def _research_from_record(record: dict[str, Any] | None, job_hash: str, *, enabled: bool) -> tuple[dict[str, Any] | None, bool]:
    """Return fresh research or a short negative-cache hit; never reuse another job's data."""
    if not record or record.get("job_hash") != job_hash:
        return None, False
    try:
        updated_at = datetime.fromisoformat(str(record["updated_at"]))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated_at
    except (KeyError, TypeError, ValueError):
        return None, False
    if age < timedelta(0):
        return None, False
    if record.get("state") == "READY" and age < timedelta(days=7):
        return {
            "summary": record["summary"], "sources": record.get("sources", []),
            "facts": record.get("facts", []), "gaps": record.get("gaps", ""),
            "search_calls": int(record.get("search_calls") or 0),
            "cache_scope": str(record.get("cache_scope") or "job"),
        }, True
    if (enabled and record.get("state") == "UNAVAILABLE" and
            record.get("error_code") in _RESEARCH_FAILURE_CACHE_CODES and
            age < timedelta(minutes=15)):
        return {
            "summary": record.get("summary") or "Ricerca online non disponibile.",
            "sources": record.get("sources", []), "facts": record.get("facts", []),
            "gaps": record.get("gaps") or "Usare solo le informazioni verificate nell'annuncio.",
            "search_calls": 0, "cache_scope": "job",
        }, True
    return None, False


class GenerationCoordinator:
    def __init__(
        self, store: JobStore, tasks: GenerationStore, settings: AISettingsStore,
        keys: APIKeyStore, agents_markdown: AgentPromptStore,
        candidate_context: AgentPromptStore, candidate_profile: CandidateProfileStore,
        application_data: ApplicationStore | None = None,
        *, provider_factory: Callable[..., Any] = OpenAIProvider,
        renderer: Callable[[str], tuple[bytes, bytes]] = render_letter_documents,
    ) -> None:
        self.store = store
        self.tasks = tasks
        self.settings = settings
        self.keys = keys
        self.agents_markdown = agents_markdown
        self.candidate_context = candidate_context
        self.candidate_profile = candidate_profile
        self.application_data = application_data or ApplicationStore(store)
        self.local_decisions = LocalDecisionEngine(store, self.application_data, settings.path.with_name("laya.json"))
        self.provider_factory = provider_factory
        self.renderer = renderer
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_quota_notice: tuple[str, str, int] | None = None
        self._research_prefetch_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="company-research-prefetch")
        self._research_prefetch_guard = threading.Lock()
        self._research_prefetch_future: Future[Any] | None = None

    def start(self) -> None:
        self.tasks.reconcile()
        self._thread = threading.Thread(target=self._loop, name="cover-letter-generation", daemon=True)
        self._thread.start()
        self.wake()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._research_prefetch_executor.shutdown(wait=True, cancel_futures=True)
        self.local_decisions.close()

    def wake(self) -> None:
        self._wake.set()

    @staticmethod
    def _job_research_hash(job: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(
            [job.get(k) for k in ("linkedin_job_id", "title", "company", "location", "description")],
            ensure_ascii=False).encode("utf-8")).hexdigest()

    def _company_cache_research(self, job_id: str, job_hash: str, company: str) -> dict[str, Any] | None:
        cached = self.application_data.get_company_research(company)
        if not cached:
            return None
        research = {key: cached.get(key) for key in ("summary", "sources", "facts", "gaps")}
        research["search_calls"] = 0
        research["cache_scope"] = "company"
        research["researched_at"] = cached.get("researched_at") or cached.get("updated_at") or ""
        self.application_data.save_research(
            job_id, state="READY", summary=str(research.get("summary") or ""),
            sources=research.get("sources") or [], facts=research.get("facts") or [],
            gaps=str(research.get("gaps") or ""), search_calls=0,
            job_hash=job_hash, cache_scope="company", researched_at=research["researched_at"])
        return research

    @staticmethod
    def _cached_research_stage_output(job: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
        """Reuse sourced company facts and extract role requirements from this posting only."""
        description = str(job.get("description") or "")
        lines = [" ".join(line.split()) for line in description.splitlines()]
        lines = [line for line in lines if len(line) >= 35]
        signals = ("responsibilit", "require", "qualification", "skill", "experience", "must ",
                   "should ", "preferred", "you will", "we are looking", "knowledge of", "ability to")
        selected = [line for line in lines if any(signal in line.casefold() for signal in signals)]
        selected = (selected or lines)[:5]
        role_lines = [f"Role: {str(job.get('title') or 'not specified')}.",
                      "Requirements below are extracted from this job posting:",
                      *(f"- {line[:700]}" for line in selected)]
        if not selected:
            role_lines.append("No readable requirements were extracted; check the original posting.")
        gaps = str(research.get("gaps") or "")
        if not selected:
            gaps = (gaps + "\n" if gaps else "") + "No readable requirements could be extracted from the job posting."
        sources = research.get("sources") or []
        return {"company_analysis": str(research.get("summary") or "No company research available.")[:8_000],
                "role_analysis": "\n".join(role_lines)[:8_000],
                "gaps": gaps[:4_000],
                "needs_review": bool(gaps or not sources or not selected)}

    def _schedule_research_prefetch(self, settings: dict[str, Any]) -> None:
        if (self._thread is None or self._stop.is_set() or not settings.get("web_research_enabled") or
                (not settings.get("enabled") and not self.tasks.has_queued(auto_enabled=False))):
            return
        with self._research_prefetch_guard:
            if self._research_prefetch_future is not None and not self._research_prefetch_future.done():
                return
            self._research_prefetch_future = self._research_prefetch_executor.submit(
                self._prefetch_next_company_research, dict(settings))

    def _prefetch_next_company_research(self, settings: dict[str, Any]) -> None:
        """Run only the next queued application's research while another letter advances."""
        task: dict[str, Any] | None = None
        reservation: int | None = None
        job_id = ""
        digest = ""
        request_id = new_request_id()
        with bind_request_id(request_id):
            try:
                if self._stop.is_set() or self.readiness(require_auto=False):
                    return
                current_settings = self.settings.load()
                if not current_settings["web_research_enabled"]:
                    return
                key = self.keys.get()
                if not key:
                    return
                task = self.tasks.claim_next(auto_enabled=current_settings["enabled"])
                if task is None:
                    return
                job_id, digest = str(task["linkedin_job_id"]), str(task["job_hash"])
                self.tasks.set_stage(job_id, digest, "Ricerca aziendale in parallelo")
                job = self.store.get_job(job_id)
                if job is None or not self.tasks.is_running(job_id, digest):
                    return
                job_hash = self._job_research_hash(job)
                record = self.application_data.get_research(job_id)
                research, hit = _research_from_record(record, job_hash, enabled=True)
                if hit and record and record.get("state") == "READY":
                    log_event(logging.INFO, "generation", "generation.research_prefetch_cache_hit",
                              "The next queued job already has reusable company research.",
                              context={"cache_scope": (research or {}).get("cache_scope", "job")})
                    return
                company_research = self._company_cache_research(
                    job_id, job_hash, str(job.get("company") or ""))
                if company_research:
                    log_event(logging.INFO, "generation", "generation.research_prefetch_cache_hit",
                              "The next queued job reused a saved company profile.",
                              context={"cache_scope": "company"})
                    return
                if hit:
                    return
                current_settings = self.settings.load()
                if (self._stop.is_set() or not current_settings["consent"] or
                        self.keys.get() != key or not current_settings["web_research_enabled"] or
                        (not current_settings["enabled"] and not task["manual_request"])):
                    return
                reservation = self.tasks.reserve_api_call(
                    job_id, WEB_SEARCH_MODEL, "company_research",
                    daily_limit=settings["max_daily_calls"],
                    category_limit=settings["max_daily_research_calls"], stage="company_research_prefetch")
                if reservation is None:
                    log_event(logging.INFO, "generation", "generation.research_prefetch_deferred",
                              "Prefetch skipped because the daily research limit is reached.",
                              context={"error_code": "usage_limit"})
                    return
                log_event(logging.INFO, "generation", "generation.research_prefetch_started",
                          "The next queued company's research started while the current application continues.")
                provider = self.provider_factory(key, str(settings["model"]))
                provider.last_usage = {}
                try:
                    research = provider.research_company(job=job, global_instructions=self.agents_markdown.general_instructions())
                    current_settings = self.settings.load()
                    if (self.tasks.is_running(job_id, digest) and current_settings["consent"] and
                            self.keys.get() == key and current_settings["web_research_enabled"]):
                        saved = self.application_data.save_research(
                            job_id, state="READY", summary=research["summary"],
                            sources=research.get("sources", []), facts=research.get("facts", []),
                            gaps=research.get("gaps", ""), search_calls=research.get("search_calls", 0),
                            job_hash=job_hash)
                        cached = self.application_data.save_company_research(str(job.get("company") or ""), saved)
                        if cached:
                            self.application_data.save_research(
                                job_id, state="READY", summary=saved["summary"],
                                sources=saved.get("sources", []), facts=saved.get("facts", []),
                                gaps=saved.get("gaps", ""), search_calls=saved.get("search_calls", 0),
                                job_hash=job_hash, cache_scope="company",
                                researched_at=saved.get("researched_at", ""))
                        log_event(logging.INFO, "generation", "generation.research_prefetch_completed",
                                  "Research for the next queued job is ready for reuse.",
                                  context={"cache_saved": cached, "source_count": len(saved.get("sources", [])),
                                           "duration_ms": int(getattr(provider, "last_duration_ms", 0))})
                except AIProviderError as error:
                    if self.tasks.is_running(job_id, digest):
                        self.application_data.save_research(
                            job_id, state="UNAVAILABLE", error_code=error.code, job_hash=job_hash)
                    log_event(logging.WARNING, "generation", "generation.research_prefetch_unavailable",
                              "Research prefetch failed; the next workflow will use available local evidence.",
                              context={"error_code": error.code})
                finally:
                    usage = dict(getattr(provider, "last_usage", {}) or {})
                    usage.setdefault("duration_ms", int(getattr(provider, "last_duration_ms", 0)))
                    self.tasks.finish_api_call(reservation, usage)
                    reservation = None
            except Exception as error:
                log_event(logging.WARNING, "generation", "generation.research_prefetch_error",
                          "Research prefetch stopped; the main generation queue remains available.",
                          context={"error_code": "prefetch_error"}, details=_safe_exception_details(error))
            finally:
                if reservation is not None:
                    self.tasks.finish_api_call(reservation, {})
                if task is not None and job_id and digest and self.tasks.is_running(job_id, digest):
                    self.tasks.requeue_running(job_id, digest)
                    self.wake()

    def readiness(self, *, require_auto: bool = True) -> list[str]:
        settings = self.settings.load()
        missing = []
        if require_auto and not settings["enabled"]:
            missing.append("automazione_disattivata")
        if self.settings.migration_pending_on_disk():
            missing.append("migrazione_agenti_richiesta")
        if not settings["consent"]:
            missing.append("consenso_mancante")
        if not self.keys.get():
            missing.append("chiave_api_mancante")
        if not self.candidate_profile.status()["has_cv"]:
            missing.append("cv_mancante")
        try:
            self.agents_markdown.agents()
        except LocalProfileError:
            missing.append("agents_md_da_correggere")
        return missing

    def status(self) -> dict[str, Any]:
        missing = self.readiness()
        return {"ready": not missing, "missing": missing,
                "summary": self.tasks.summary(), "key_source": self.keys.source(),
                "last_linkedin_sync": self.store.get_last_sync_at()}

    def request_one(self, job_id: str) -> dict[str, Any]:
        missing = self.readiness(require_auto=False)
        if missing:
            labels = {"consenso_mancante": "consenso", "chiave_api_mancante": "chiave API",
                      "cv_mancante": "CV", "agents_md_da_correggere": "AGENTS.md",
                      "migrazione_agenti_richiesta": "migrazione degli agenti"}
            raise JobStoreError("Configura " + ", ".join(labels.get(item, item) for item in missing)
                                + " prima di rigenerare.")
        task = self.tasks.queue_manual(job_id)
        self.wake()
        return task

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=60)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                missing = self.readiness(require_auto=False)
                if missing:
                    continue
                settings = self.settings.load()
                age_result = self.tasks.apply_auto_age_limit(settings["max_auto_job_age_days"])
                if age_result["skipped"]:
                    log_event(logging.INFO, "generation", "generation.jobs_skipped_for_age_limit",
                              "Automatic generation skipped jobs saved outside the configured age window.",
                              context={"count": age_result["skipped"],
                                       "limit": settings["max_auto_job_age_days"]})
                if age_result["released"]:
                    log_event(logging.INFO, "generation", "generation.jobs_released_by_age_limit_change",
                              "Previously age-limited jobs are eligible under the updated setting.",
                              context={"count": age_result["released"],
                                       "limit": settings["max_auto_job_age_days"]})
                if age_result["unverified"]:
                    log_event(logging.WARNING, "generation", "generation.jobs_needing_age_verification",
                              "Automatic generation skipped jobs with an unreadable local first-saved date.",
                              context={"count": age_result["unverified"]})
                if not self.tasks.has_queued(auto_enabled=settings["enabled"]):
                    self.tasks.clear_pending_daily_limit()
                    self._last_quota_notice = None
                    continue
                limit = self.tasks.daily_limit_status(settings)
                if limit is not None:
                    self.tasks.mark_pending_for_daily_limit()
                    today = datetime.now(timezone.utc).date().isoformat()
                    notice = (today, str(limit["name"]), int(limit["limit"]))
                    if notice != self._last_quota_notice:
                        resume_at = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat() + "T00:00:00+00:00"
                        log_event(logging.WARNING, "generation", "generation.waiting_for_daily_limit",
                                  "Queued AI jobs are waiting for the daily call limit to renew.",
                                  context={"error_code": "usage_limit", "limit_name": limit["name"],
                                           "count": limit["used"], "limit": limit["limit"],
                                           "resume_at": resume_at})
                        self._last_quota_notice = notice
                    continue
                self.tasks.clear_pending_daily_limit()
                self._last_quota_notice = None
                processed = 0
                for _ in range(settings["max_per_cycle"]):
                    if self._stop.is_set():
                        break
                    task = self.tasks.claim_next(auto_enabled=settings["enabled"])
                    if task is None:
                        break
                    processed += 1
                    if self._process(task, settings):
                        break
                # A bounded cycle resumes after one minute if more work remains.
                if processed == settings["max_per_cycle"] and not self._stop.is_set():
                    self._wake.wait(timeout=60)
                    self._wake.set()
            except Exception as error:
                log_event(logging.ERROR, "generation", "generation.worker_error",
                          "The local generation worker encountered an internal error.",
                          context={"error_code": "internal_error"},
                          details=_safe_exception_details(error))

    def _process(self, task: dict[str, Any], settings: dict[str, Any]) -> bool:
        job_id = str(task["linkedin_job_id"])
        digest = str(task["job_hash"])
        request_id = new_request_id()
        workflow_started = time.perf_counter()
        with bind_request_id(request_id):
            phase_started: dict[str, float] = {}
            phase_logged: set[str] = set()

            def start_phase(name: str) -> None:
                phase_started[name] = time.perf_counter()

            def finish_phase(name: str, outcome: str, **details: Any) -> None:
                started = phase_started.get(name)
                if started is None or name in phase_logged:
                    return
                phase_logged.add(name)
                log_event(logging.INFO, "generation", "generation.stage_timing",
                          "Generation phase duration recorded.",
                          context={"generation_id": request_id, "stage": name,
                                   "duration_ms": round((time.perf_counter() - started) * 1_000),
                                   "outcome": outcome, **details})

            def finish_pending_phases(outcome: str) -> None:
                for name in tuple(phase_started):
                    finish_phase(name, outcome)

            log_event(logging.INFO, "generation", "generation.started",
                      "AI generation started for one saved job.",
                      context={"generation_id": request_id, "manual": bool(task["manual_request"]), "queue_ms": max(0, round((datetime.now(timezone.utc)-datetime.fromisoformat(task["requested_at"])).total_seconds()*1000))})
            try:
                start_phase("generation_setup")
                job = self.store.get_job(job_id)
                if job is None:
                    raise AIProviderError("Annuncio non trovato.", "missing_job")
                key = self.keys.get()
                if not key:
                    raise AIProviderError("Chiave API assente.", "authentication")
                finish_phase("generation_setup", "ready")
                start_phase("input_read")
                cv_text = self.candidate_profile.read_text()
                context = self.candidate_context.load()
                global_rules = self.agents_markdown.general_instructions()
                agents = self.agents_markdown.agents()
                finish_phase("input_read", "ready")
                self.tasks.set_stage(job_id, digest, "local-decisions")
                # Generation needs only the job-level typed decision. Detailed candidate,
                # style and duplicate scores remain an explicit UI action because they
                # are advisory and were adding several extra CPU batches per letter.
                local_started = time.perf_counter()
                local_decisions = self.local_decisions.analyze(job, cv_text, context, detailed=False)
                log_event(logging.INFO, "generation", "generation.stage_timing",
                          "Local decision and role preflight completed.",
                          context={"generation_id": request_id, "stage": "laya_preflight", "duration_ms": round((time.perf_counter() - local_started) * 1_000),
                                   "outcome": local_decisions.get("state", "UNKNOWN")})
                if local_decisions.get("state") == "READY":
                    self.local_decisions.clear_replaced_stages(job_id)
                    local_decisions["replaced_agent_stages"] = []
                start_phase("provider_setup")
                provider = self.provider_factory(key, settings["model"])
                job_hash = self._job_research_hash(job)
                finish_phase("provider_setup", "ready")
                cache_started = time.perf_counter()
                research_record = self.application_data.get_research(job_id)
                research, research_cache_hit = _research_from_record(
                    research_record, job_hash, enabled=settings["web_research_enabled"])
                if (not research_cache_hit or
                        (research_record and research_record.get("state") != "READY") or
                        (research or {}).get("cache_scope") != "company"):
                    company_cached = self._company_cache_research(
                        job_id, job_hash, str(job.get("company") or ""))
                    if company_cached:
                        research, research_cache_hit = company_cached, True
                cache_lookup_ms = round((time.perf_counter() - cache_started) * 1_000)
                log_event(logging.INFO, "generation", "generation.stage_timing", "Research cache checked.",
                          context={"generation_id": request_id, "stage": "cache_check", "duration_ms": cache_lookup_ms, "outcome": "hit" if research_cache_hit else "miss", "cached": research_cache_hit})
                start_phase("company_research")
                if research_cache_hit:
                    log_event(logging.INFO, "generation", "generation.research_cache_hit",
                              "Existing company research was reused without an API call.",
                              context={"cache_scope": (research or {}).get("cache_scope", "job"),
                                       "duration_ms": cache_lookup_ms})
                elif settings["web_research_enabled"]:
                    self.tasks.set_stage(job_id, digest, "Ricerca web aziendale")
                    reservation = self.tasks.reserve_api_call(
                        job_id, WEB_SEARCH_MODEL, "company_research",
                        daily_limit=settings["max_daily_calls"],
                        category_limit=settings["max_daily_research_calls"],
                        stage="company_research")
                    if reservation is None:
                        self.application_data.save_research(
                            job_id, state="UNAVAILABLE", error_code="research_limit", job_hash=job_hash)
                        research = {"summary": "Ricerca online non eseguita: limite giornaliero raggiunto.",
                                    "sources": [], "facts": [],
                                    "gaps": "Ricerca online non disponibile per il limite giornaliero; usare solo fatti verificati nell'annuncio."}
                    else:
                        provider.last_usage = {}
                        try:
                            research = provider.research_company(job=job, global_instructions=global_rules)
                            self.application_data.save_research(
                                job_id, state="READY", summary=research["summary"], sources=research["sources"],
                                facts=research.get("facts", []), gaps=research.get("gaps", ""),
                                search_calls=research.get("search_calls", 0), job_hash=job_hash)
                            saved_research = self.application_data.get_research(job_id) or {}
                            company_cached = self.application_data.save_company_research(
                                str(job.get("company") or ""), saved_research)
                            research["cache_scope"] = "company" if company_cached else "job"
                            if company_cached:
                                self.application_data.save_research(
                                    job_id, state="READY", summary=saved_research["summary"],
                                    sources=saved_research.get("sources", []),
                                    facts=saved_research.get("facts", []),
                                    gaps=saved_research.get("gaps", ""),
                                    search_calls=saved_research.get("search_calls", 0),
                                    job_hash=job_hash, cache_scope="company",
                                    researched_at=saved_research.get("researched_at", ""))
                            log_event(logging.INFO, "generation", "generation.company_research_cached",
                                      "Verified sourced company information was added to the reusable local cache.",
                                      context={"cache_saved": company_cached,
                                               "source_count": len(saved_research.get("sources", []))})
                        except AIProviderError as error:
                            self.application_data.save_research(
                                job_id, state="UNAVAILABLE", error_code=error.code, job_hash=job_hash)
                            research = {"summary": "Ricerca online non disponibile.", "sources": [], "facts": [],
                                        "gaps": "Usare solo le informazioni verificate nell'annuncio."}
                            log_event(logging.WARNING, "generation", "generation.research_unavailable",
                                      "Company research could not be completed; generation will use verified local material.",
                                      context={"error_code": error.code})
                        finally:
                            usage = dict(provider.last_usage)
                            usage.setdefault("duration_ms", int(getattr(provider, "last_duration_ms", 0)))
                            self.tasks.finish_api_call(reservation, usage)
                else:
                    self.application_data.save_research(
                        job_id, state="UNAVAILABLE", error_code="research_disabled", job_hash=job_hash)
                    research = {"summary": "Ricerca online non attivata nelle Impostazioni.",
                                "sources": [], "facts": [], "gaps": "Nessuna fonte aziendale consultata."}
                research_outcome = (
                    "cache" if research_cache_hit else
                    "disabled" if not settings["web_research_enabled"] else
                    "searched" if research and research.get("sources") else "unavailable"
                )
                finish_phase("company_research", research_outcome,
                             cached=research_cache_hit,
                             source_count=len((research or {}).get("sources") or []))
                if not research:
                    research = {"summary": "Nessuna ricerca aziendale disponibile.", "sources": [], "facts": [],
                                "gaps": "Nessuna fonte verificata."}
                research_pages: list[dict[str, Any]] = []
                research_record = self.application_data.get_research(job_id)
                if (research.get("sources") and research_record and research_record.get("state") == "READY"):
                    start_phase("web_pages")
                    research = {**research, "job_hash": research_record.get("job_hash"),
                                "updated_at": research_record.get("updated_at")}
                    pages_outcome = "ready"
                    try:
                        research_pages = cached_research_pages(self.application_data, job_id, research)
                        research["page_texts"] = research_pages
                    except Exception as error:
                        pages_outcome = "unavailable"
                        log_event(logging.WARNING, "generation", "generation.research_page_read_unavailable",
                                  "Cited company pages could not be cached for local ranking; original research retained.",
                                  context={"error_code": "page_cache_unavailable"})
                        research_pages = []
                    finally:
                        finish_phase("web_pages", pages_outcome, page_count=len(research_pages),
                                     cached=bool(research_pages) and all(page.get("cached") is True
                                                                         for page in research_pages))
                start_phase("context_prepare")
                generation_context = self.local_decisions.prepare_generation_context(
                    job, cv_text, context, research, research_pages)
                selection_timing = generation_context.get("summary", {})
                finish_phase("context_prepare", selection_timing.get("state", "ready"),
                             candidate_index_cached=selection_timing.get("candidate_index_cached"),
                             candidate_index_chunks=selection_timing.get("candidate_index_chunks"),
                             preprocessing_ms=round(float(selection_timing.get("preprocessing_seconds", 0)) * 1000),
                             candidate_retrieval_ms=round(float(selection_timing.get("candidate_retrieval_seconds", 0)) * 1000),
                             ranking_ms=round(float(selection_timing.get("ranking_seconds", 0)) * 1000),
                             inference_ms=round(float(selection_timing.get("laya_inference_seconds", 0)) * 1000),
                             worker_startup_ms=selection_timing.get("laya_worker_startup_ms"),
                             candidate_source_chars=selection_timing.get("candidate_source_chars"),
                             candidate_writer_chars=selection_timing.get("candidate_writer_chars"))
                style_examples = generation_context["knowledge"]
                writer_research = generation_context["writer_research"]
                if local_decisions.get("state") != "DISABLED":
                    updated_decisions = self.local_decisions.record_generation_selection(
                        job_id, generation_context["summary"])
                    if updated_decisions:
                        local_decisions = (self.local_decisions._preflight_view(
                            updated_decisions, cached=bool(local_decisions.get("cached")))
                            if local_decisions.get("state") == "READY" else updated_decisions)
                result: dict[str, Any] = {
                    "company_analysis": research["summary"], "role_analysis": "", "personalization_notes": "",
                    "cover_letter_text": "", "gaps": research.get("gaps", ""),
                    "needs_review": bool(research.get("gaps") or not research.get("sources")),
                }
                active_agents = [agent for agent in agents if agent["enabled"]]
                used_models: set[str] = set()
                replaced_stages: list[str] = []
                completed_agents: list[dict[str, Any]] = []
                has_research_agent = any(
                    str(item.get("id") or "").casefold() == "job-company-research" or
                    str(item.get("phase") or "").casefold().replace(" ", "-") in {"research", "job-company-research"}
                    for item in active_agents)
                if not has_research_agent:
                    self._schedule_research_prefetch(settings)
                for index, agent in enumerate(active_agents):
                    if not self.tasks.is_running(job_id, digest):
                        finish_pending_phases("superseded")
                        log_event(logging.WARNING, "generation", "generation.superseded",
                                  "The job changed or left the queue before its next stage.",
                                  context={"generation_id": request_id, "outcome": "superseded",
                                           "duration_ms": round((time.perf_counter() - workflow_started) * 1_000)})
                        return False
                    current_settings = self.settings.load()
                    if (not current_settings["consent"] or self.keys.get() != key or
                            (not current_settings["enabled"] and not task["manual_request"])):
                        self.tasks.requeue_running(job_id, digest)
                        finish_pending_phases("deferred")
                        log_event(logging.INFO, "generation", "generation.deferred",
                                  "Generation returned to the queue because its settings or consent changed.",
                                  context={"generation_id": request_id, "outcome": "deferred",
                                           "duration_ms": round((time.perf_counter() - workflow_started) * 1_000)})
                        return False
                    self.tasks.set_stage(job_id, digest, str(agent["phase"]))
                    agent_id = str(agent.get("id") or "").casefold()
                    agent_phase = str(agent.get("phase") or "").casefold().replace(" ", "-")
                    is_research_agent = (agent_id == "job-company-research" or
                                         agent_phase in {"research", "job-company-research"})
                    stage_started = time.perf_counter()
                    if research_cache_hit and is_research_agent:
                        stage_input = {key: value for key, value in result.items() if key != "local_decisions"}
                        result = merge_stage_output(
                            stage_input, self._cached_research_stage_output(job, research or {}), "research")
                        if local_decisions.get("state") != "DISABLED":
                            result["local_decisions"] = local_decisions
                        completed_agents.append(agent)
                        log_event(logging.INFO, "generation", "generation.research_agent_skipped_cache",
                                  "The configured company research agent was skipped because a saved company profile was reused.",
                                  context={"stage_index": index + 1, "stage_count": len(active_agents),
                                           "cache_scope": (research or {}).get("cache_scope", "job")})
                        log_event(logging.INFO, "generation", "generation.stage_timing",
                                  "Cached research stage completed without an API call.",
                                  context={"generation_id": request_id, "stage": agent_id or agent_phase,
                                           "duration_ms": round((time.perf_counter() - stage_started) * 1_000),
                                           "outcome": "cache"})
                        self._schedule_research_prefetch(settings)
                        continue
                    local_stage = ("research" if agent_id == "job-company-research" or agent_phase in {"research", "job-company-research"}
                                   else "coordinator" if agent_id == "main-coordinator" or agent_phase in {"coordination", "main-coordinator"}
                                   else None)
                    if (local_stage == "coordinator" and not any(
                            item.get("id") in {"reviewer", "critical-review"} or
                            str(item.get("phase") or "").casefold().replace(" ", "-") in {"review", "reviewer", "critical-review"}
                            for item in completed_agents)):
                        local_stage = None
                    local_mode = self.local_decisions.settings()["enabled"]
                    durable_company_coordinator = (
                        local_stage == "coordinator" and research_cache_hit and
                        (research or {}).get("cache_scope") == "company")
                    use_local_stage = bool(
                        local_stage and (
                            (local_mode and local_decisions.get("state") == "READY") or
                            durable_company_coordinator
                        ))
                    if use_local_stage:
                        local_output = self.local_decisions.local_agent_output(
                            local_stage, job, research, local_decisions, result, completed_agents)
                        # Local advisories are coordinator state, not part of the strict
                        # application result schema validated by merge_stage_output.
                        stage_input = {key: value for key, value in result.items()
                                       if key != "local_decisions"}
                        result = merge_stage_output(stage_input, local_output, local_stage)
                        replaced_stages.append(str(agent.get("id") or local_stage))
                        if local_mode and local_decisions.get("state") == "READY":
                            updated_decisions = self.local_decisions.record_replaced_stage(
                                job_id, str(agent.get("id") or local_stage))
                            if updated_decisions:
                                local_decisions = updated_decisions
                            result["local_decisions"] = local_decisions
                        completed_agents.append(agent)
                        log_event(logging.INFO, "generation", "generation.local_agent_stage_completed",
                                  ("Company research was already stored, so deterministic local coordination replaced the final API stage."
                                   if durable_company_coordinator and not (local_mode and local_decisions.get("state") == "READY")
                                   else "An API-backed decision-only agent stage ran with local rules and Laya instead."),
                                  context={"stage_index": index + 1, "stage_count": len(active_agents),
                                           "local_stage": local_stage,
                                           "reason": "durable_company_profile" if durable_company_coordinator else "laya_local"})
                        log_event(logging.INFO, "generation", "generation.stage_timing",
                                  "Local agent stage completed.",
                                  context={"generation_id": request_id, "stage": agent_id or agent_phase,
                                           "duration_ms": round((time.perf_counter() - stage_started) * 1_000),
                                           "outcome": "local"})
                        if is_research_agent:
                            self._schedule_research_prefetch(settings)
                        continue
                    agent_model = str(agent.get("model") or settings["model"])
                    agent_provider = self.provider_factory(key, agent_model)
                    reservation = self.tasks.reserve_api_call(
                        job_id, agent_model, "agent", daily_limit=settings["max_daily_calls"],
                        category_limit=settings["max_daily_agent_calls"],
                        stage=agent_id or agent_phase)
                    if reservation is None:
                        raise AIProviderError("Limite giornaliero di chiamate AI raggiunto.", "usage_limit")
                    log_event(logging.INFO, "generation", "generation.stage_started",
                              "AI generation stage started.",
                              context={"stage_index": index + 1, "stage_count": len(active_agents),
                                       "stage": agent_id or agent_phase})
                    agent_provider.last_usage = {}
                    used_models.add(agent_model)
                    stage_outcome = "error"
                    try:
                        if local_decisions.get("state") != "DISABLED":
                            result["local_decisions"] = local_decisions
                        is_writer = (agent_id in {"cv-match-writer", "candidate-matching-writing"} or
                                     agent_phase in {"writing", "matching", "cv-match-writer"})
                        result = agent_provider.run_agent(
                            agent=agent, global_instructions=global_rules, job=job,
                            cv_text=cv_text, candidate_context=context, previous=result,
                            research=writer_research if is_writer else research, knowledge=style_examples,
                            candidate_evidence=(generation_context.get("candidate_evidence") if is_writer else None),
                        )
                        # run_agent intentionally returns only application fields. Keep the
                        # compact local preflight available to subsequent sequential stages.
                        if local_decisions.get("state") != "DISABLED":
                            result["local_decisions"] = local_decisions
                        completed_agents.append(agent)
                        stage_outcome = "completed"
                    finally:
                        usage = getattr(agent_provider, "last_usage", None)
                        usage = dict(usage) if isinstance(usage, dict) else {}
                        usage.setdefault("duration_ms", int(getattr(agent_provider, "last_duration_ms", 0)))
                        self.tasks.finish_api_call(reservation, usage)
                        log_event(logging.INFO, "generation", "generation.stage_timing",
                                  "AI agent stage duration recorded.",
                                  context={"generation_id": request_id, "stage": agent_id or agent_phase,
                                           "duration_ms": round((time.perf_counter() - stage_started) * 1_000),
                                           "api_duration_ms": int(getattr(agent_provider, "last_duration_ms", 0)),
                                           "outcome": stage_outcome})
                    if is_research_agent:
                        self._schedule_research_prefetch(settings)
                start_phase("final_validation")
                current_settings = self.settings.load()
                if (not current_settings["consent"] or self.keys.get() != key or
                        (not current_settings["enabled"] and not task["manual_request"])):
                    self.tasks.requeue_running(job_id, digest)
                    finish_pending_phases("deferred")
                    log_event(logging.INFO, "generation", "generation.deferred",
                              "Generation returned to the queue because its settings or consent changed.",
                              context={"generation_id": request_id, "outcome": "deferred",
                                       "duration_ms": round((time.perf_counter() - workflow_started) * 1_000)})
                    return False
                if not result["cover_letter_text"]:
                    raise AIProviderError("L'agente non ha prodotto una cover letter.", "invalid_response")
                if research.get("gaps") or not research.get("sources"):
                    issue = str(research.get("gaps") or "Nessuna fonte aziendale verificata.")
                    if issue not in result["gaps"]:
                        result["gaps"] = (result["gaps"].rstrip() + "\n" + issue).strip()
                    result["needs_review"] = True
                finish_phase("final_validation", "ready_to_save")
                if local_decisions.get("state") == "READY":
                    result["local_decisions"] = {**local_decisions,
                                                  "replaced_agent_stages": replaced_stages}
                model_label = "+".join(sorted(used_models)) or settings["model"]
                if replaced_stages:
                    model_label += ("+Laya locale" if local_decisions.get("state") == "READY"
                                    else "+coordinamento locale")
                start_phase("text_save")
                save_outcome = "failed"
                try:
                    committed = self.tasks.commit_result(job_id, digest, result, None, None, model_label)
                    save_outcome = "saved" if committed else "superseded"
                finally:
                    finish_phase("text_save", save_outcome)
                if committed:
                    log_event(logging.INFO, "generation", "generation.completed",
                              "AI analysis and letter were saved for one job.",
                              context={"generation_id": request_id, "needs_review": result["needs_review"],
                                       "duration_ms": round((time.perf_counter() - workflow_started) * 1_000),
                                       "agent_stages": len(active_agents),
                                       "research_cache_scope": (research or {}).get("cache_scope", "none")})
                else:
                    log_event(logging.WARNING, "generation", "generation.superseded",
                              "A newer job revision superseded the AI result.",
                              context={"generation_id": request_id, "outcome": "superseded",
                                       "duration_ms": round((time.perf_counter() - workflow_started) * 1_000)})
            except (AIProviderError, LocalProfileError, JobStoreError) as error:
                code = error.code if isinstance(error, AIProviderError) else "local_input"
                if code == "usage_limit":
                    self.tasks.defer_running_for_daily_limit(job_id, digest, request_id)
                    finish_pending_phases("waiting_for_daily_limit")
                    limit = self.tasks.daily_limit_status(settings) or {
                        "name": "daily_api_calls", "used": 0, "limit": settings["max_daily_calls"]
                    }
                    today = datetime.now(timezone.utc).date().isoformat()
                    self._last_quota_notice = (today, str(limit["name"]), int(limit["limit"]))
                    resume_at = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat() + "T00:00:00+00:00"
                    log_event(logging.WARNING, "generation", "generation.waiting_for_daily_limit",
                              "This job remains queued until the daily AI call limit renews.",
                              context={"error_code": code, "limit_name": limit["name"],
                                       "count": limit["used"], "limit": limit["limit"],
                                       "resume_at": resume_at, "generation_id": request_id,
                                       "outcome": "waiting_for_daily_limit",
                                       "duration_ms": round((time.perf_counter() - workflow_started) * 1_000)})
                    return True
                finish_pending_phases("failed")
                self.tasks.fail(job_id, digest, code, request_id)
                log_event(logging.ERROR, "generation", "generation.failed",
                          "AI generation failed for one job.",
                          context={"generation_id": request_id, "outcome": "failed", "error_code": code,
                                   "duration_ms": round((time.perf_counter() - workflow_started) * 1_000)})
            except Exception as error:
                finish_pending_phases("failed")
                self.tasks.fail(job_id, digest, "internal_error", request_id)
                log_event(logging.ERROR, "generation", "generation.failed",
                          "AI generation failed due to an internal error.",
                          context={"generation_id": request_id, "outcome": "failed", "error_code": "internal_error",
                                   "duration_ms": round((time.perf_counter() - workflow_started) * 1_000)},
                          details=_safe_exception_details(error))
            return False
