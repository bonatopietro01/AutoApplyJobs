"""Local-only approximate pricing for complete applications and bulk refreshes."""

from __future__ import annotations

from typing import Any


# Public standard API rates in USD per million text tokens (input, cached input, output), checked 2026-10-01.
# The estimate never calls the provider and cannot read account-specific discounts.
MODEL_RATES = {
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
}
WEB_SEARCH_USD_PER_CALL = 0.01
WEB_SEARCH_MODEL = "gpt-4.1-mini"


def _rate(model: str) -> tuple[float, float, float]:
    return MODEL_RATES.get(model, MODEL_RATES["gpt-4o-mini"])


def _cached_aware_cost(input_tokens: float, cached_tokens: float, output_tokens: float,
                       rates: tuple[float, float, float]) -> float:
    input_rate, cached_rate, output_rate = rates
    cached_tokens = max(0.0, min(input_tokens, cached_tokens))
    return (((input_tokens - cached_tokens) * input_rate + cached_tokens * cached_rate)
            + output_tokens * output_rate) / 1_000_000


def _stage_output_cap(agent: dict[str, Any]) -> int:
    agent_id = str(agent.get("id") or "").casefold()
    phase = str(agent.get("phase") or "").casefold().replace(" ", "-")
    if agent_id == "job-company-research" or phase == "job-company-research":
        return 1_200
    if agent_id == "cv-match-writer" or phase in {"writing", "cv-match-writer"}:
        return 2_000
    if agent_id == "reviewer" or phase == "reviewer":
        return 1_800
    if agent_id == "main-coordinator" or phase == "main-coordinator":
        return 350
    return 3_500


def estimate_bulk_cost(*, count: int, agents: list[dict[str, Any]],
                       usage_profile: list[dict[str, Any]], research_enabled: bool,
                       research_jobs_count: int | None = None,
                       default_model: str = "gpt-4o-mini") -> dict[str, Any]:
    """Estimate cost from 30-day local token averages, with explicit fallbacks."""
    by_key = {(str(row["model"]), str(row["operation"]), str(row.get("stage") or "")): row
              for row in usage_profile}
    total_usd = 0.0
    agent_usd = 0.0
    research_usd = 0.0
    calls = 0
    estimated_search_calls = 0
    assumed_calls: list[str] = []
    expected_api_seconds = 0.0
    low_api_seconds = 0.0
    high_api_seconds = 0.0

    for agent in agents:
        if not agent.get("enabled"):
            continue
        model = str(agent.get("model") or default_model)
        stage = str(agent.get("id") or agent.get("phase") or "").casefold()
        row = by_key.get((model, "agent", stage))
        if row is None:
            row = by_key.get((model, "agent", ""))
            if row and stage:
                assumed_calls.append(f"agent:{model}:{stage}:pooled_{row.get('calls', 0)}_calls")
        if row and int(row.get("calls") or 0) > 0:
            input_tokens = _observed_average(row, "avg_input_tokens", 22_000)
            output_tokens = _observed_average(row, "avg_output_tokens", 1_500)
            cached_tokens = _observed_average(row, "avg_cached_input_tokens", 0)
            measured_seconds = _observed_average(row, "avg_duration_ms", 0) / 1_000
        else:
            # Conservative fallback; actual prompts vary with CV and job length.
            input_tokens, output_tokens, cached_tokens, measured_seconds = (
                22_000.0, float(min(1_500, _stage_output_cap(agent))), 0.0, 0.0,
            )
            assumed_calls.append(f"agent:{model}")
        stage_usd = count * _cached_aware_cost(input_tokens, cached_tokens, output_tokens, _rate(model))
        total_usd += stage_usd
        agent_usd += stage_usd
        if measured_seconds > 0:
            expected_api_seconds += count * measured_seconds
            low_api_seconds += count * measured_seconds * 0.7
            high_api_seconds += count * measured_seconds * 1.5
        else:
            low_api_seconds += count * 15
            expected_api_seconds += count * 45
            high_api_seconds += count * 90
        calls += count

    research_count = (count if research_jobs_count is None else max(0, min(count, research_jobs_count)))
    if research_enabled and research_count:
        model = WEB_SEARCH_MODEL
        row = by_key.get((model, "company_research", "company_research"))
        if row is None:
            row = by_key.get((model, "company_research", ""))
        if row and int(row.get("calls") or 0) > 0:
            input_tokens = _observed_average(row, "avg_input_tokens", 18_000)
            output_tokens = _observed_average(row, "avg_output_tokens", 1_000)
            search_calls = _observed_average(row, "avg_search_calls", 2)
            cached_tokens = _observed_average(row, "avg_cached_input_tokens", 0)
            measured_seconds = _observed_average(row, "avg_duration_ms", 0) / 1_000
        else:
            input_tokens, output_tokens, search_calls, cached_tokens, measured_seconds = (
                18_000.0, 1_000.0, 2.0, 0.0, 0.0,
            )
            assumed_calls.append("research:gpt-4.1-mini")
        research_token_usd = research_count * _cached_aware_cost(
            input_tokens, cached_tokens, output_tokens, _rate(model))
        research_tool_usd = research_count * search_calls * WEB_SEARCH_USD_PER_CALL
        research_usd += research_token_usd + research_tool_usd
        total_usd += research_token_usd + research_tool_usd
        estimated_search_calls = round(research_count * search_calls)
        calls += research_count
        if measured_seconds > 0:
            expected_api_seconds += research_count * measured_seconds
            low_api_seconds += research_count * measured_seconds * 0.7
            high_api_seconds += research_count * measured_seconds * 1.5
        else:
            low_api_seconds += research_count * 30
            expected_api_seconds += research_count * 60
            high_api_seconds += research_count * 90

    # A range communicates uncertainty without pretending to be a bill quote.
    low = total_usd * 0.7
    high = total_usd * 1.5
    agent_calls = count * sum(bool(agent.get("enabled")) for agent in agents)
    research_calls = research_count if research_enabled else 0
    duration_seconds = {
        "low": round(low_api_seconds),
        "expected": round(expected_api_seconds),
        "high": round(high_api_seconds),
        "note": "Usa i tempi API locali recenti quando disponibili; altrimenti applica una stima prudenziale per chiamata. Esclude coda, rendering e rallentamenti futuri.",
    }
    return {
        "currency": "USD",
        "low": round(low, 8),
        "expected": round(total_usd, 8),
        "high": round(high, 8),
        "estimated_api_calls": calls,
        "estimated_web_search_calls": estimated_search_calls,
        "jobs": count,
        "enabled_agents": sum(bool(agent.get("enabled")) for agent in agents),
        "research_jobs": research_count if research_enabled else 0,
        "research_enabled": bool(research_enabled),
        "agent_cost_usd": {"low": round(agent_usd * 0.7, 8), "expected": round(agent_usd, 8),
                           "high": round(agent_usd * 1.5, 8)},
        "research_cost_usd": {"low": round(research_usd * 0.7, 8), "expected": round(research_usd, 8),
                              "high": round(research_usd * 1.5, 8)},
        "duration_seconds": duration_seconds,
        "assumptions": sorted(set(assumed_calls)),
        "price_source_checked": "2026-10-01",
        "note": "Stima locale da medie degli ultimi 30 giorni, inclusi i token in cache osservati, o ipotesi conservative; non legge saldo, sconti o accordi dell’account.",
    }


def _observed_average(row: dict[str, Any], key: str, fallback: float) -> float:
    value = row.get(key)
    return fallback if value is None else max(0.0, float(value))


def _locally_replaced_agents(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mirror GenerationCoordinator's two safe replacement slots and order gate."""
    replacements: list[dict[str, Any]] = []
    reviewer_completed = False
    for agent in agents:
        if not agent.get("enabled"):
            continue
        agent_id = str(agent.get("id") or "").casefold()
        phase = str(agent.get("phase") or "").casefold().replace(" ", "-")
        if agent_id == "job-company-research" or phase in {"research", "job-company-research"}:
            replacements.append(agent)
        elif ((agent_id == "main-coordinator" or phase in {"coordination", "main-coordinator"})
              and reviewer_completed):
            replacements.append(agent)
        if agent_id in {"reviewer", "critical-review"} or phase in {"review", "reviewer", "critical-review"}:
            reviewer_completed = True
    return replacements


def estimate_complete_application_cost(*, agents: list[dict[str, Any]],
                                       usage_profile: list[dict[str, Any]],
                                       research_enabled: bool,
                                       default_model: str = "gpt-4o-mini",
                                       laya_enabled: bool = False) -> dict[str, Any]:
    """Compare one complete cover-letter run, conditional on a successful Laya preflight."""
    replaced = _locally_replaced_agents(agents)
    replaced_keys = {(str(agent.get("id") or ""), str(agent.get("phase") or "")) for agent in replaced}
    remaining = [agent for agent in agents if (str(agent.get("id") or ""), str(agent.get("phase") or ""))
                 not in replaced_keys]

    def summarize(research_jobs: int) -> dict[str, Any]:
        before = estimate_bulk_cost(count=1, agents=agents, usage_profile=usage_profile,
                                    research_enabled=research_enabled, research_jobs_count=research_jobs,
                                    default_model=default_model)
        after = estimate_bulk_cost(count=1, agents=remaining, usage_profile=usage_profile,
                                   research_enabled=research_enabled, research_jobs_count=research_jobs,
                                   default_model=default_model)
        saved = max(0.0, float(before["agent_cost_usd"]["expected"])
                    - float(after["agent_cost_usd"]["expected"]))
        pct = (saved / float(before["expected"]) * 100) if before["expected"] else 0.0
        before_summary = _cost_summary(before)
        after_summary = _cost_summary(after)
        savings = {"low": round(saved * 0.7, 8), "expected": round(saved, 8),
                   "high": round(saved * 1.5, 8)}
        projections: dict[str, Any] = {}
        for volume in (100, 1_000):
            projections[str(volume)] = {
                "before_laya": {key: round(float(before_summary[key]) * volume, 6)
                                for key in ("low", "expected", "high")},
                "after_laya_success": {key: round(float(after_summary[key]) * volume, 6)
                                       for key in ("low", "expected", "high")},
                "api_savings_usd": {key: round(float(savings[key]) * volume, 6)
                                    for key in ("low", "expected", "high")},
            }
        return {
            "before_laya": before_summary,
            "after_laya_success": after_summary,
            "api_savings_usd": savings,
            "projections": projections,
            "expected_savings_percent": round(pct, 2),
            "fallback_if_laya_unavailable": _cost_summary(before),
        }

    fresh = summarize(1 if research_enabled else 0)
    cached = summarize(0)
    return {
        "currency": "USD",
        "price_source_checked": "2026-10-01",
        "laya_enabled": bool(laya_enabled),
        "scenario": "successful local preflight; original API workflow is the fallback",
        "replaced_agent_ids": [str(agent.get("id") or agent.get("phase") or "") for agent in replaced],
        "api_requests_saved": len(replaced),
        "research_tool_calls_saved": 0,
        "research_enabled": bool(research_enabled),
        "fresh_research": fresh,
        "research_cache_hit": cached,
        "notes": [
            "Il confronto include ricerca web, agenti attivi e output finale; scrittura/revisione e ricerca web restano conteggiate dopo Laya.",
            "La ricerca web in cache entro la validità configurata non aggiunge una chiamata API.",
            "Il risparmio è condizionato al preflight Laya riuscito; in caso di errore il fallback non risparmia chiamate.",
            "La pipeline non ritenta automaticamente le fasi agente; una rigenerazione manuale è un nuovo run e non è inclusa nei valori per candidatura.",
            "Il costo locale di CPU non è monetizzato: il consumo elettrico non è misurato su questo PC.",
            "Gli intervalli basso/alto applicano fattori di sensibilità 0,7x/1,5x alla stima centrale; non sono intervalli statistici.",
        ],
        "assumptions": sorted(set(fresh["before_laya"]["assumptions"])
                               | set(fresh["after_laya_success"]["assumptions"])),
    }


def _cost_summary(estimate: dict[str, Any]) -> dict[str, Any]:
    return {key: estimate[key] for key in ("low", "expected", "high", "estimated_api_calls",
                                           "estimated_web_search_calls", "enabled_agents",
                                           "agent_cost_usd", "research_cost_usd", "assumptions")}
