from __future__ import annotations

import unittest

from linkedin_connector.cost_estimator import MODEL_RATES, estimate_bulk_cost, estimate_complete_application_cost


class CostEstimatorTests(unittest.TestCase):
    def test_gpt_56_luna_uses_the_published_cached_and_uncached_rates(self) -> None:
        self.assertEqual(MODEL_RATES["gpt-5.6-luna"], (0.20, 0.02, 1.20))
        self.assertGreater(MODEL_RATES["gpt-5.6-luna"][0], MODEL_RATES["gpt-4.1-nano"][0])
        self.assertGreater(MODEL_RATES["gpt-5.6-luna"][2], MODEL_RATES["gpt-4.1-nano"][2])

    def test_uses_cached_input_rates_and_measured_api_durations(self) -> None:
        estimate = estimate_bulk_cost(
            count=2,
            agents=[
                {"id": "cv-match-writer", "phase": "cv-match-writer", "enabled": True,
                 "model": "gpt-4.1-nano"},
                {"id": "main-coordinator", "phase": "main-coordinator", "enabled": True,
                 "model": "gpt-4.1-nano"},
            ],
            usage_profile=[
                {"model": "gpt-4.1-nano", "operation": "agent", "calls": 4,
                 "avg_input_tokens": 1_000, "avg_output_tokens": 100,
                 "avg_cached_input_tokens": 500, "avg_duration_ms": 2_000},
                {"model": "gpt-4.1-mini", "operation": "company_research", "calls": 1,
                 "avg_input_tokens": 5_000, "avg_output_tokens": 100,
                 "avg_search_calls": 1, "avg_cached_input_tokens": 2_500,
                 "avg_duration_ms": 4_000},
            ],
            research_enabled=True,
            default_model="gpt-4.1-nano",
        )
        self.assertEqual(estimate["expected"], 0.02323)
        self.assertEqual(estimate["duration_seconds"]["expected"], 16)
        self.assertEqual(estimate["estimated_api_calls"], 6)

    def test_complete_application_includes_research_all_agents_cache_and_projections(self) -> None:
        agents = [
            {"id": "job-company-research", "phase": "job-company-research", "enabled": True,
             "model": "gpt-4.1-nano"},
            {"id": "cv-match-writer", "phase": "cv-match-writer", "enabled": True,
             "model": "gpt-4.1-nano"},
            {"id": "reviewer", "phase": "reviewer", "enabled": True,
             "model": "gpt-4.1-nano"},
            {"id": "main-coordinator", "phase": "main-coordinator", "enabled": True,
             "model": "gpt-4.1-nano"},
        ]
        profile = [{"model": "gpt-4.1-nano", "operation": "agent", "stage": "", "calls": 6,
                    "avg_input_tokens": 2_710.5, "avg_output_tokens": 624.2,
                    "avg_cached_input_tokens": 0, "avg_duration_ms": 1_000}]

        estimate = estimate_complete_application_cost(
            agents=agents, usage_profile=profile, research_enabled=True,
            default_model="gpt-4.1-nano", laya_enabled=True,
        )
        fresh = estimate["fresh_research"]
        cached = estimate["research_cache_hit"]
        self.assertAlmostEqual(fresh["before_laya"]["expected"], 0.03088292, places=8)
        self.assertAlmostEqual(fresh["after_laya_success"]["expected"], 0.02984146, places=8)
        self.assertAlmostEqual(fresh["api_savings_usd"]["expected"], 0.00104146, places=8)
        self.assertAlmostEqual(fresh["expected_savings_percent"], 3.37, places=2)
        self.assertEqual(fresh["before_laya"]["estimated_api_calls"], 5)
        self.assertEqual(fresh["after_laya_success"]["estimated_api_calls"], 3)
        self.assertEqual(fresh["before_laya"]["estimated_web_search_calls"], 2)
        self.assertAlmostEqual(cached["before_laya"]["expected"], 0.00208292, places=8)
        self.assertAlmostEqual(cached["api_savings_usd"]["expected"], 0.00104146, places=8)
        self.assertEqual(fresh["fallback_if_laya_unavailable"], fresh["before_laya"])
        self.assertAlmostEqual(fresh["projections"]["100"]["api_savings_usd"]["expected"], 0.104146, places=6)
        self.assertAlmostEqual(fresh["projections"]["1000"]["api_savings_usd"]["expected"], 1.04146, places=5)
        self.assertEqual(estimate["research_tool_calls_saved"], 0)

    def test_stage_specific_usage_wins_over_pooled_model_average(self) -> None:
        estimate = estimate_bulk_cost(
            count=1,
            agents=[{"id": "cv-match-writer", "phase": "cv-match-writer", "enabled": True,
                     "model": "gpt-4.1-nano"}],
            usage_profile=[
                {"model": "gpt-4.1-nano", "operation": "agent", "stage": "cv-match-writer", "calls": 2,
                 "avg_input_tokens": 1_000, "avg_output_tokens": 100,
                 "avg_cached_input_tokens": 500, "avg_duration_ms": 500},
                {"model": "gpt-4.1-nano", "operation": "agent", "stage": "", "calls": 8,
                 "avg_input_tokens": 20_000, "avg_output_tokens": 1_500,
                 "avg_cached_input_tokens": 0, "avg_duration_ms": 5_000},
            ],
            research_enabled=False,
        )
        self.assertAlmostEqual(estimate["expected"], 0.0001025, places=8)
        self.assertEqual(estimate["assumptions"], [])

    def test_complete_application_without_history_uses_explicit_safe_fallbacks(self) -> None:
        agents = [
            {"id": "job-company-research", "phase": "job-company-research", "enabled": True,
             "model": "gpt-4.1-nano"},
            {"id": "cv-match-writer", "phase": "cv-match-writer", "enabled": True,
             "model": "gpt-4.1-nano"},
            {"id": "reviewer", "phase": "reviewer", "enabled": True,
             "model": "gpt-4.1-nano"},
            {"id": "main-coordinator", "phase": "main-coordinator", "enabled": True,
             "model": "gpt-4.1-nano"},
        ]
        estimate = estimate_complete_application_cost(
            agents=agents, usage_profile=[], research_enabled=True,
        )
        self.assertGreater(estimate["fresh_research"]["before_laya"]["expected"], 0)
        self.assertIn("research:gpt-4.1-mini", estimate["assumptions"])
        self.assertTrue(any("agent:gpt-4.1-nano" in item for item in estimate["assumptions"]))
        self.assertEqual(estimate["fresh_research"]["before_laya"]["estimated_web_search_calls"], 2)
        self.assertEqual(estimate["fresh_research"]["fallback_if_laya_unavailable"],
                         estimate["fresh_research"]["before_laya"])


if __name__ == "__main__":
    unittest.main()
