# Laya integration and complete-application cost — 2026-09-30

Update 2026-10-03: Laya decisions are now attempted automatically when the
local runtime and checkpoint are available. The former dashboard opt-in control
was removed; saved opt-outs are migrated at dashboard startup. The figures below
remain historical estimates and are not a measurement of current API savings.

## Repository flow and scope

When enabled, generation runs a small local Laya preflight before the configured
agents. The existing job/company research phase still performs online research
through the OpenAI Responses API. The configured Job & Company Research phase
uses that research summary and extracts requirements verbatim from the job; the
CV Match & Writer and Reviewer remain API calls; after review, the configured
Main Coordinator phase checks readiness locally. Local replacements occupy
their original sequence positions, pass prior results onward, and are recorded
in `replaced_agent_stages`. If Laya is missing, fails, truncates an input, or
returns malformed data, both API-backed stages run instead. A failed or
incomplete workflow cannot commit PDF/DOCX output.

The automatic path now performs one role/seniority inference from the posting.
It does not rescore candidate excerpts, knowledge examples, or drafts for every
letter. A separate **Analizza con Laya** action retains the detailed advisory
analysis and batches compatible excerpt/style/duplicate decisions. Its candidate
and style scores remain diagnostic only: they do not hide CV evidence or select
knowledge examples. The existing deterministic selectors remain authoritative.
Job-only preflight results are cached using only the inputs used for that
decision; detailed results retain their fuller job/CV/context/knowledge cache key.

Laya is a typed decision model, not a text generator or search engine. It cannot
write the cover letter or replace the web search, writer, and reviewer. The
application used to be opt-in; current builds enable local decisions automatically
when the checkpoint is available and keep the existing PDF and
DOCX delivery path.

## Current package and hardware findings

- Installed and pinned: `laya==0.3.21`, CPU, multilingual checkpoint, `max_len=8192`.
- The 0.3.21 package supports ordered `Router.predict_batch` calls with
  `sort_by_length=True`, which the detailed scoring path already uses. The app
  now uses one job-only inference in the automatic path and reuses cached results.
- PyPI's newer 0.3.22 release was published on 2026-09-29. Its signed source
  commit changes only the version strings in three files; the tested 0.3.21 pin
  was retained rather than changing dependencies without a code or accuracy
  improvement.
- The official package documents multilingual input up to 8,192 tokens and
  warns that accuracy is more variable for long contexts. The classifier is
  loaded lazily on CPU in this app; the package lists the multilingual mmBERT
  base at 322 million parameters. The current app path has been tested on CPU,
  not GPU. The multilingual model card lists 51 languages and Apache 2.0 licensing.
- Version 0.3.21 documents an optional ONNX Runtime extra and per-channel INT8
  export for CPU. `onnxruntime` is not installed in this project environment;
  quantization was not enabled or claimed as a measured optimization.

Sources were checked 2026-09-30: [Laya 0.3.21 package/API and ONNX notes](https://pypi.org/project/laya/0.3.21/),
[0.3.22 release files](https://pypi.org/project/laya/0.3.22/),
[0.3.22 signed source commit](https://github.com/NandhaKishorM/laya/commit/6d942c92081fbc139e736bbd9ac0023223c29b7f),
[multilingual model card and license](https://huggingface.co/convaiinnovations/laya-multilingual).

## Measured local model behavior

`scripts/check_laya.py --workflow` ran with the installed checkpoint, synthetic
fixtures and a temporary database, with Hugging Face offline mode enabled. The
job-only preflight returned READY and classified the synthetic data analyst
role as software/data/AI at confidence 0.7454. With the model already warm, the
preflight took 1.36 seconds wall time; explicit detailed scoring of the same
small fixture took 2.80 seconds. The preflight reused its cache even when
candidate fixture text changed. This single-case comparison suggests about
51% less local inference time for this fixture; it is not an end-to-end letter
latency guarantee.

`scripts/benchmark_laya_decisions.py` ran ten fabricated cases in one batch in
47.50 seconds on this PC. Correct results: job category 8/10, candidate match
6/10, style relevance 5/10, and possible same vacancy 9/10. Some confidently
answered cases were wrong. These numbers are a small synthetic sanity check,
not a domain accuracy estimate. This evidence is why candidate/style rankings
remain advisory and the draft-language scorer is no longer run automatically.

The same smoke script measured an explicit synthetic draft's English score at
2.1/100 and specificity at 25.2/100. The role example was correct, but the
candidate-match classifiers could not reliably confirm a relevant experience.
No personal candidate data was used. `telemetry_summary()` has no saved local
preflight timings in the user's application database, so the interface does
not present the synthetic timing as a real-workload average. CPU power and
electricity use were not measured.

## Estimated API cost per completed application

This is a local estimate, not an account invoice. It uses public standard prices
checked 2026-09-30 and aggregates from the last 30 days only:

- Six historical `gpt-4.1-nano` agent calls averaged 2,710.5 input and 624.2
  output tokens, with no cached input recorded. Stage IDs were not recorded for
  these older rows, so the shared average is used for all four configured
  agents. No phase-specific `company_research` history was available.
- Four configured API agents use `gpt-4.1-nano`. A fresh research fallback
  assumes 18,000 input / 1,000 output tokens and two search calls; 18,000 input
  includes an estimated 16,000 search-context tokens plus the request. Cached
  research has no search request or search-content tokens.
- Public prices: `gpt-4.1-nano` $0.10/$0.025/$0.40 per million input/cached/output
  tokens; `gpt-4.1-mini` $0.40/$0.10/$1.60. Web search costs $0.01 per search
  call plus model-rated search-content tokens. OpenAI documents 8,000 fixed
  search-content input tokens per non-preview search call for `gpt-4.1-mini`.
- The range is a sensitivity scenario of 0.7× to 1.5× the central estimate,
  not a confidence interval. The code reports which model/phase data fell back
  to shared usage or conservative token assumptions.

| One complete run | Before Laya | After successful Laya | API saving |
| --- | ---: | ---: | ---: |
| Fresh web research | $0.03088292 | $0.02984146 | $0.00104146 (3.37%) |
| Research already cached | $0.00208292 | $0.00104146 | $0.00104146 (50%) |

Fresh-research total-cost sensitivity is $0.02161804 / $0.03088292 /
$0.04632438 before Laya and $0.02088902 / $0.02984146 / $0.04476219 after
successful Laya (low / central / high). For cached research it is
$0.00145804 / $0.00208292 / $0.00312438 before and $0.00072902 / $0.00104146 /
$0.00156219 after. These ranges are generated by the stated sensitivity factors.

For a fresh search, expected components are about $0.00208292 for four agent
calls and $0.0288 for the research Responses call and two searches. Laya replaces
two agent calls, leaving the research search, writer, and reviewer costs intact.
Fresh-research API requests are estimated as 5 before vs 3 after Laya (the two
internal web-search tool calls are shown separately); a research-cache hit is 4
vs 2 API requests. If Laya fails, the existing fallback has the full before
cost and no API calls are saved.

| Fresh research projection | Before | After success | Saving |
| --- | ---: | ---: | ---: |
| 100 applications | $3.088292 | $2.984146 | $0.104146 |
| 1,000 applications | $30.88292 | $29.84146 | $1.04146 |

Cached-research projections are $0.208292 vs $0.104146 per 100 applications,
and $2.08292 vs $1.04146 per 1,000. The fresh-search saving sensitivity range
is $0.00072902–$0.00156219 per application, $0.072902–$0.156219 per 100, and
$0.72902–$1.56219 per 1,000.

No automatic agent retry is implemented. A user-requested regeneration is a
separate run, not included in a single-completed-application estimate. The
history lacks per-run attempt grouping, stage IDs on older rows, live account
discounts, and research usage; therefore it cannot prove actual billed cost or
historical retry cost. Laya adds no OpenAI calls or per-token charge. Local CPU
electricity is not monetized because power draw and the user's tariff are not
measured. The conditional saving is not a forecast of success-weighted savings:
the local history is too small to estimate how often Laya preflight fails.

## Local tests and remaining verification

The automated tests use fake providers and fixtures; no paid API or LinkedIn
call is made. They cover usage by phase, cache rates, fresh/cached research,
100/1,000 projections, candidate-independent preflight caching, stage order and
fallback, and final document persistence. The browser test checks that the cost
projection renders in settings.

No real OpenAI generation/search, LinkedIn scan, or account invoice comparison
was performed. Real-world Laya success rate, candidate match quality, power
draw, and full-application latency remain unverified.

## Reproduce local model smoke test

```powershell
$env:HF_HUB_OFFLINE = '1'
.\.venv\Scripts\python.exe -u scripts/check_laya.py --workflow
.\.venv\Scripts\python.exe -u scripts/benchmark_laya_decisions.py
```
