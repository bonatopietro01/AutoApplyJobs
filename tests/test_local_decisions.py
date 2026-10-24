"""Local decisions tested with typed SDK fixtures, never remote AI or LinkedIn."""
import tempfile
import queue
import threading
import time
import unittest
import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import patch

from linkedin_connector.ai_config import AIConfigurationError
from linkedin_connector.application_store import ApplicationStore
from linkedin_connector.database import JobStore
from linkedin_connector.local_decisions import LocalDecisionEngine, LocalInferenceUnavailable
from tests.test_database import make_job


class FakeRouter:
    def __init__(self):
        self.calls = []
        self.batch_calls = []
        self.truncated = False

    def predict(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        answers = {}
        for name, question in questions.items():
            if question["type"] == "choice":
                choice = "software-data-ai" if name == "role" else "experienced"
                answers[name] = {"choice": choice, "answer_confidence": .85, "probabilities": {choice: .85}}
            else:
                negative = "wrong-style" in str(state) or "unsupported" in str(state)
                answers[name] = {"noul": .1 if negative else .9}
        return {"answers": answers, "usage": {"truncated": self.truncated}, "routing": {"model": "multilingual"}}

    def predict_batch(self, requests, **kwargs):
        self.batch_calls.append({"count": len(requests), **kwargs})
        return [self.predict(item["state"], item["questions"], model=item["model"], max_len=item["max_len"])
                for item in requests]


class FakeProcessQueue:
    def __init__(self):
        self.items = queue.Queue()
        self.on_put = None
        self.join_cancelled = False
        self.pending_write = False

    def put(self, value):
        self.items.put(value)
        self.pending_write = True
        if self.on_put is not None:
            self.on_put(value)

    def get_nowait(self):
        return self.items.get_nowait()

    def get(self, timeout=None):
        return self.items.get(timeout=timeout)

    def cancel_join_thread(self): self.join_cancelled = True
    def close(self): pass
    def join_thread(self):
        if self.pending_write and not self.join_cancelled:
            raise RuntimeError("queue feeder would block on an unconsumed request")


class FakeProcessContext:
    def __init__(self, *, respond=True, startup_duration_ms=0):
        self.respond = respond
        self.startup_duration_ms = startup_duration_ms
        self.queues = []
        self.processes = []

    def Queue(self, maxsize=0):
        channel = FakeProcessQueue()
        self.queues.append(channel)
        return channel

    def Process(self, *, target, args, daemon):
        requests, responses = args[1:]
        process = FakeInferenceProcess(requests, responses, respond=self.respond,
                                       startup_duration_ms=self.startup_duration_ms)
        self.processes.append(process)
        return process


class FakeInferenceProcess:
    def __init__(self, requests, responses, *, respond, startup_duration_ms=0):
        self.requests, self.responses, self.respond = requests, responses, respond
        self.startup_duration_ms = startup_duration_ms
        self.alive = False
        self.terminated = False
        self.exitcode = None

    def start(self):
        self.alive = True
        self.responses.put({"kind": "ready", "startup_duration_ms": self.startup_duration_ms})

        def answer(request):
            if request is None or not self.respond:
                return
            questions = request["questions"]
            answers = {}
            for name, question in questions.items():
                if question["type"] == "choice":
                    answers[name] = {"choice": "software-data-ai", "answer_confidence": .85}
                else:
                    answers[name] = {"noul": .9}
            result = answers if request["operation"] == "predict" else [answers for _ in request["states"]]
            self.responses.put({"kind": "result", "request_id": request["request_id"],
                                "ok": True, "result": result})

        self.requests.on_put = answer

    def is_alive(self): return self.alive
    def terminate(self):
        self.terminated = True
        self.alive = False
        self.exitcode = 1
    def join(self, timeout=None): pass


class LocalDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JobStore(Path(self.temp.name) / "jobs.sqlite3")
        self.store.sync_jobs([make_job(), make_job("987654321")])
        self.application = ApplicationStore(self.store)
        self.router = FakeRouter()
        self.engine = LocalDecisionEngine(self.store, self.application, Path(self.temp.name) / "laya.json", router_factory=lambda: self.router)
        self.engine.save_settings({"enabled": False})
        self.job = self.store.get_job("123456789")

    def test_disabled_does_not_load_model(self):
        self.assertEqual(self.engine.analyze(self.job, "cv", "context")["state"], "DISABLED")
        self.assertFalse(self.router.calls)

    def test_laya_is_enabled_by_default_but_missing_checkpoint_falls_back_without_download(self):
        engine = LocalDecisionEngine(self.store, self.application,
                                     Path(self.temp.name) / "missing-model.json")
        with patch("linkedin_connector.local_decisions._local_checkpoint_available", return_value=False):
            self.assertTrue(engine.settings()["enabled"])
            status = engine.save_settings({"enabled": True})
        self.assertTrue(status["settings"]["enabled"])
        self.assertFalse(status["active"])

    def test_sources_scores_selection_cache_and_invalidation(self):
        self.engine.save_settings({"enabled": True})
        self.application.add_knowledge("style.txt", "txt", b"Style", "Style useful in application.")
        self.application.add_knowledge("other.txt", "txt", b"Other", "wrong-style example")
        result = self.engine.analyze(self.job, "Built Python dashboards.", "Studied engineering.")
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["match_score"], 90)
        self.assertEqual(result["evidence"][0]["text"], "Built Python dashboards.")
        self.assertFalse(result["knowledge"][0]["facts_verified"])
        self.assertEqual(self.engine.select_examples(result, self.job), self.application.relevant_knowledge(self.job))
        self.assertEqual(result["possible_duplicates"][0]["job_id"], "987654321")
        self.assertTrue(all(call[2] == {"model": "multilingual", "max_len": 8192} for call in self.router.calls))
        self.assertGreaterEqual(len(self.router.batch_calls), 3)
        self.assertTrue(all(call["sort_by_length"] is True for call in self.router.batch_calls))
        self.assertTrue(all(call["batch_size"] == min(call["count"], 8) for call in self.router.batch_calls))
        call_count = len(self.router.calls)
        self.assertTrue(self.engine.analyze(self.job, "Built Python dashboards.", "Studied engineering.")["cached"])
        self.assertEqual(call_count, len(self.router.calls))
        self.engine.analyze(self.job, "Updated evidence.", "Studied engineering.")
        self.assertGreater(len(self.router.calls), call_count)
        self.assertEqual(len(self.store.get_dashboard_jobs()), 2)

    def test_preflight_is_job_only_cached_and_strips_detailed_candidate_coverage(self):
        self.engine.save_settings({"enabled": True})
        self.application.add_knowledge("style.txt", "txt", b"Style", "A concise style example.")
        detailed = self.engine.analyze(self.job, "CV evidence", "candidate context")
        self.assertGreater(detailed["coverage"]["candidate_fragments_total"], 0)
        call_count = len(self.router.calls)

        preflight = self.engine.analyze(self.job, "changed CV", "changed context", detailed=False)
        self.assertTrue(preflight["cached"])
        self.assertEqual(preflight["analysis_level"], "preflight")
        self.assertEqual(preflight["coverage"]["candidate_fragments_total"], 0)
        self.assertEqual(preflight["coverage"]["duplicate_candidates_total"], 0)
        self.assertNotIn("evidence", preflight)
        self.assertNotIn("knowledge", preflight)
        self.assertEqual(len(self.router.calls), call_count)

        job_only = self.engine.analyze({**self.job, "description": "A changed job description."},
                                       "CV evidence", "candidate context", detailed=False)
        self.assertFalse(job_only["cached"])
        self.assertEqual(job_only["knowledge_selection_method"], "deterministic_keyword_match")
        self.assertEqual(len(self.router.calls), call_count)

    def test_generation_preflight_runs_only_one_inference_batch(self):
        self.engine.save_settings({"enabled": True})
        self.application.add_knowledge("style.txt", "txt", b"Style", "A concise style example.")
        ambiguous_job = {
            **self.job,
            "title": "Analyst",
            "description": (
                "Build Python software and APIs while supporting clinical pharmaceutical research "
                "and operations teams."
            ),
        }
        result = self.engine.analyze(ambiguous_job, "candidate evidence", "candidate context", detailed=False)
        self.assertEqual(result["state"], "READY")
        self.assertEqual(len(self.router.calls), 1)
        self.assertNotIn("candidate_evidence", self.router.calls[0][0])
        self.assertNotIn("style_example", self.router.calls[0][0])

    def test_clear_preflight_uses_deterministic_keywords_without_laya(self):
        self.engine.save_settings({"enabled": True})
        clear_job = {
            **self.job,
            "title": "Data Analyst",
            "description": "Build data analysis reports with Python for business teams.",
        }
        result = self.engine.analyze(clear_job, "candidate evidence", "candidate context", detailed=False)
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["knowledge_selection_method"], "deterministic_keyword_match")
        self.assertFalse(self.router.calls)

    def test_generation_context_without_knowledge_or_research_is_a_successful_fallback(self):
        self.engine.save_settings({"enabled": True})
        result = self.engine.prepare_generation_context(self.job, "Short CV evidence", "", {}, [])
        self.assertEqual(result["summary"]["state"], "READY")
        self.assertEqual(result["knowledge"], [])
        self.assertIsNone(result["candidate_evidence"])
        self.assertEqual(result["writer_research"]["facts"], [])
        self.assertFalse(self.router.calls)

    def test_candidate_keyword_index_is_local_reused_invalidated_and_keeps_source_offsets(self):
        self.engine.save_settings({"enabled": True})
        experience = (
            "COMPLETE_EXPERIENCE_START\nData Engineer built Python data pipelines and ETL services. "
            + ("Documented analytics workflows and operational responsibilities. " * 14)
            + "PrivateCandidateMarker delivered analytics workflows.\nCOMPLETE_EXPERIENCE_END"
        )
        unrelated = "Stakeholder planning documentation and team collaboration. " * 15
        cv = "\n\n".join([experience, *(unrelated + str(index) for index in range(5))])
        role = {**self.job, "title": "Data Engineer",
                "description": "Build Python data pipelines and ETL services for analytics."}
        first_started = time.perf_counter()
        first = self.engine.prepare_generation_context(role, cv, "", {}, [])
        first_elapsed = time.perf_counter() - first_started
        self.assertFalse(first["summary"]["candidate_index_cached"])
        self.assertTrue(first["summary"]["candidate_reduced"])
        self.assertLess(first["summary"]["candidate_writer_chars"], len(cv))
        self.assertTrue(first["candidate_evidence"])
        self.assertEqual(first["candidate_evidence"][0]["source"], "cv")
        self.assertGreaterEqual(first["candidate_evidence"][0]["offset"], 0)
        self.assertIn("COMPLETE_EXPERIENCE_START", first["candidate_evidence"][0]["text"])
        self.assertIn("COMPLETE_EXPERIENCE_END", first["candidate_evidence"][0]["text"])

        next_role = {**self.store.get_job("987654321"), "title": "Data Platform Engineer",
                     "description": "Python ETL and analytics pipelines."}
        next_role_started = time.perf_counter()
        second = self.engine.prepare_generation_context(next_role, cv, "", {}, [])
        second_elapsed = time.perf_counter() - next_role_started
        self.assertTrue(second["summary"]["candidate_index_cached"])
        self.assertEqual(second["summary"]["candidate_index_chunks"], first["summary"]["candidate_index_chunks"])

        changed_cv = cv + " Kubernetes platform operations."
        changed = self.engine.prepare_generation_context(next_role, changed_cv, "", {}, [])
        self.assertFalse(changed["summary"]["candidate_index_cached"])
        repeated = self.engine.prepare_generation_context(next_role, changed_cv, "", {}, [])
        self.assertTrue(repeated["summary"]["candidate_index_cached"])

        with self.engine._connection() as connection:
            row = connection.execute(
                "SELECT version,input_hash,index_json FROM candidate_keyword_index WHERE index_key='active'"
            ).fetchone()
        self.assertEqual(row["version"], "candidate-keyword-index-2-hmac-paragraphs")
        index_text = row["index_json"]
        self.assertNotIn("PrivateCandidateMarker", index_text)
        self.assertNotIn("Python data engineering experience", index_text)
        self.assertNotIn(hashlib.sha256(b"python").hexdigest(), index_text)
        secret = (Path(self.temp.name) / "candidate_keyword_index.key").read_bytes()
        keyed_python = hmac.new(secret, b"python", hashlib.sha256).hexdigest()
        self.assertIn(keyed_python, index_text)
        self.assertNotIn(secret.hex(), index_text)
        self.assertEqual(self.router.calls, [])
        self.assertGreaterEqual(first_elapsed, 0)
        self.assertGreaterEqual(second_elapsed, 0)
        self.assertLessEqual(second["summary"]["candidate_index_build_seconds"],
                             first["summary"]["candidate_index_build_seconds"] + 0.05)

    def test_laya_disabled_fallback_keeps_complete_candidate_sources(self):
        self.engine.save_settings({"enabled": False})
        cv = ("Data Engineer built Python data pipelines. " * 150)
        role = {**self.job, "title": "Data Engineer", "description": "Build Python data pipelines."}
        result = self.engine.prepare_generation_context(role, cv, "", {}, [])
        self.assertEqual(result["summary"]["state"], "FALLBACK")
        self.assertIsNone(result["candidate_evidence"])
        self.assertFalse(result["summary"]["candidate_reduced"])
        self.assertEqual(result["summary"]["candidate_writer_chars"], len(cv))

    def test_laya_timeout_uses_one_worker_and_does_not_queue_another_inference(self):
        started = threading.Event()
        release = threading.Event()

        class SlowRouter(FakeRouter):
            def predict(self, state, questions, **kwargs):
                started.set()
                release.wait(2)
                return super().predict(state, questions, **kwargs)

        slow = SlowRouter()
        engine = LocalDecisionEngine(
            self.store, self.application, Path(self.temp.name) / "slow-laya.json",
            router_factory=lambda: slow, inference_timeout_seconds=0.05,
        )
        question = {"role": {"type": "noul", "instructions": "test"}}
        try:
            with self.assertRaises(LocalInferenceUnavailable) as timed_out:
                engine._predict({"job": "Role"}, question, stage="laya_test")
            self.assertEqual(timed_out.exception.code, "timeout")
            self.assertTrue(started.is_set())
            with self.assertRaises(LocalInferenceUnavailable) as deferred:
                engine._predict({"job": "Role"}, question, stage="laya_test")
            self.assertEqual(deferred.exception.code, "busy")
            self.assertEqual(len(slow.calls), 0)
        finally:
            release.set()
        slot = engine._inference_slot
        self.assertIsNotNone(slot)
        self.assertTrue(slot[0].wait(1))
        self.assertEqual(engine._predict({"job": "Role"}, question)["role"]["noul"], .9)
        self.assertEqual(len(slow.calls), 2)

    def test_real_laya_path_uses_a_killable_process_and_stops_it_on_timeout(self):
        question = {"role": {"type": "noul", "instructions": "test"}}
        context = FakeProcessContext(startup_duration_ms=2_345)
        engine = LocalDecisionEngine(
            self.store, self.application, Path(self.temp.name) / "process-laya.json",
            process_context_factory=lambda: context, inference_timeout_seconds=.1,
        )
        with patch("linkedin_connector.local_decisions._local_checkpoint_directory",
                   return_value=Path(self.temp.name)):
            result = engine._predict({"job": "Role"}, question, stage="laya_process_test")
        self.assertEqual(result["role"]["noul"], .9)
        self.assertTrue(engine.status()["loaded"])
        self.assertEqual(engine._laya_worker_ready_duration_ms, 2_345)
        engine.close()
        self.assertTrue(context.processes[0].terminated)

        stalled_context = FakeProcessContext(respond=False)
        stalled = LocalDecisionEngine(
            self.store, self.application, Path(self.temp.name) / "stalled-laya.json",
            process_context_factory=lambda: stalled_context, inference_timeout_seconds=.05,
        )
        with patch("linkedin_connector.local_decisions._local_checkpoint_directory",
                   return_value=Path(self.temp.name)):
            with self.assertRaises(LocalInferenceUnavailable) as timed_out:
                stalled._predict({"job": "Role"}, question, stage="laya_process_test")
        self.assertEqual(timed_out.exception.code, "timeout")
        self.assertTrue(stalled_context.processes[0].terminated)
        self.assertTrue(all(channel.join_cancelled for channel in stalled_context.queues))
        self.assertIsNone(stalled._laya_process)

    def test_generation_preflight_exposes_timeout_and_returns_to_original_agent_flow(self):
        started = threading.Event()
        release = threading.Event()

        class SlowRouter(FakeRouter):
            def predict(self, state, questions, **kwargs):
                started.set()
                release.wait(2)
                return super().predict(state, questions, **kwargs)

        engine = LocalDecisionEngine(
            self.store, self.application, Path(self.temp.name) / "slow-preflight.json",
            router_factory=lambda: SlowRouter(), inference_timeout_seconds=0.05,
        )
        engine.save_settings({"enabled": True})
        began = time.monotonic()
        ambiguous_job = {
            **self.job,
            "title": "Analyst",
            "description": (
                "Build Python software and APIs while supporting clinical pharmaceutical research "
                "and operations teams."
            ),
        }
        result = engine.analyze(ambiguous_job, "candidate CV", "candidate context", detailed=False)
        self.assertEqual(result["state"], "UNAVAILABLE")
        self.assertEqual(result["error_code"], "laya_timeout")
        self.assertTrue(started.is_set())
        self.assertLess(time.monotonic() - began, 0.5)
        release.set()
        self.assertTrue(engine._inference_slot[0].wait(1))

    def test_generation_selection_uses_a_bounded_laya_shortlist_and_caches_it(self):
        self.engine.save_settings({"enabled": True})
        for index in range(5):
            self.application.add_knowledge(
                f"sample-{index}.txt", "txt", b"Example",
                f"Role relevant professional example {index}.",
            )
        facts = [{"claim": f"Company supports role-related work item {index}.",
                  "kind": "documented_fact", "source_urls": ["https://company.example/about"]}
                 for index in range(8)]
        pages = [{"url": f"https://site{index}.example/about", "title": "About",
                  "state": "READY", "text": "Company role operations and engineering. " * 5}
                 for index in range(4)]
        long_cv = ("Python engineering operations data experience. " * 250)
        result = self.engine.prepare_generation_context(
            self.job, long_cv, "Candidate engineering experience.", 
            {"summary": "Company profile", "facts": facts, "sources": []}, pages,
        )
        self.assertEqual(result["summary"]["state"], "READY")
        self.assertEqual(len(self.router.batch_calls), 1)
        self.assertLessEqual(self.router.batch_calls[0]["count"], 8)
        self.assertLessEqual(len(result["knowledge"]), 3)
        call_count = len(self.router.batch_calls)
        cached = self.engine.prepare_generation_context(
            self.job, long_cv, "Candidate engineering experience.",
            {"summary": "Company profile", "facts": facts, "sources": []}, pages,
        )
        self.assertTrue(cached["summary"]["cached"])
        self.assertEqual(len(self.router.batch_calls), call_count)

    def test_empty_sources_and_coverage_are_explicit(self):
        self.engine.save_settings({"enabled": True})
        result = self.engine.analyze(self.job, "", "")
        self.assertEqual(result["state"], "READY")
        self.assertIsNone(result["match_score"])
        self.assertEqual(result["knowledge"], [])
        self.assertEqual(self.engine.select_examples(result, self.job), [])
        result = self.engine.analyze({**self.job, "description": "a" * 6000}, "abc " * 9000, "")
        self.assertTrue(result["coverage"]["job_partial"])
        self.assertEqual(result["coverage"]["candidate_fragments_scored"], 8)
        self.assertGreater(result["coverage"]["candidate_fragments_total"], 8)

    def test_truncated_malformed_and_unavailable_do_not_invent_scores(self):
        self.engine.save_settings({"enabled": True})
        self.router.truncated = True
        self.assertEqual(self.engine.analyze(self.job, "cv", "context")["state"], "UNAVAILABLE")
        self.assertEqual(self.engine.get("123456789")["state"], "UNAVAILABLE")
        self.router.truncated = False
        self.router.predict = lambda *args, **kwargs: {"answers": {"role": {"choice": "other", "answer_confidence": float("nan")}, "seniority": {"choice": "unknown"}}}
        self.assertEqual(self.engine.analyze(self.job, "cv", "context")["state"], "UNAVAILABLE")

    def test_intermediate_review_is_advisory(self):
        review = self.engine.review_draft(self.job, "Dear hiring team, I built Python dashboards.")
        self.assertEqual(review["english_score"], 90)
        self.assertFalse(review["facts_verified"])

    def test_local_agent_replacements_use_exact_source_text_and_keep_review_gate(self):
        from linkedin_connector.ai_provider import merge_stage_output
        decisions = {"state":"READY", "role":"software-data-ai", "role_confidence":.8}
        job = {**self.job, "description":"Responsibilities:\n- Responsibilities include building Python dashboards for operations reporting.\n- Experience: analyse business data and automate reports."}
        initial = {"company_analysis":"Verified company summary", "role_analysis":"", "personalization_notes":"",
                   "cover_letter_text":"", "gaps":"", "needs_review":False}
        research = {"summary":"Verified company summary", "sources":[{"url":"https://example.test"}], "gaps":""}
        agent = [{"id":"reviewer", "enabled":True}]
        output = LocalDecisionEngine.local_agent_output("research", job, research, decisions, initial, agent)
        merged = merge_stage_output(initial, output, "research")
        self.assertIn("Python dashboards for operations reporting.", merged["role_analysis"])
        self.assertIn("Verified company summary", merged["company_analysis"])
        self.assertNotIn("company", merged["role_analysis"].casefold())
        draft = {**merged, "cover_letter_text":"Dear team", "needs_review":False, "gaps":""}
        coordinator = LocalDecisionEngine.local_agent_output("coordinator", job, research, decisions, draft, agent)
        self.assertTrue(coordinator["ready_to_deliver"])
        draft["needs_review"] = True
        coordinator = LocalDecisionEngine.local_agent_output("coordinator", job, research, decisions, draft, agent)
        self.assertFalse(coordinator["ready_to_deliver"])
        self.assertTrue(coordinator["needs_review"])

    def test_uncalibrated_style_scores_do_not_override_existing_reference_selection(self):
        self.engine.save_settings({"enabled": True})
        self.application.add_knowledge("example.txt", "txt", b"style", "wrong-style low-score example")
        result = self.engine.analyze(self.job, "cv", "context")
        self.assertEqual(result["knowledge_selection_method"], "existing_selector_advisory_only")
        self.assertEqual(self.engine.select_examples(result, self.job), self.application.relevant_knowledge(self.job))
        self.assertEqual(result["domain_calibration"], "not_calibrated")
