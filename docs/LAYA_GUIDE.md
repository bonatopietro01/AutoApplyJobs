# Optional local model: Laya

Laya provides local decision and ranking steps when its runtime and checkpoint are available. It is not a cover-letter writer or a source of verified facts. See [LAYA_VALIDATION.md](LAYA_VALIDATION.md) for historical measurements, methodology, and remaining verification limits.

## Local Laya decisions

Laya is an optional local decision model used automatically when its runtime and
checkpoint are already available. It classifies and ranks supplied text but
does not browse, write cover letters, or verify facts. During generation it can
classify the role, rank the existing shortlist of cover-letter examples, select
relevant candidate excerpts when a long CV can be materially shortened, and
rank cited company-page excerpts and sourced facts. Those local selections are
passed explicitly to the writer. The Reviewer still receives the full CV and
candidate context and can restore relevant evidence omitted from the compact
writing packet. Cover-letter examples are style references only, never evidence
about the candidate.

Install the optional runtime in the application's environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-laya.txt
```

Restart the dashboard and check **Impostazioni → Laya locale** for availability.
An opt-out saved with an older version is reset to automatic mode on startup.
The app checks that the runtime and multilingual checkpoint already exist and
passes the verified local checkpoint directory to Laya; it never asks the Hub
to download model weights during generation. Candidate excerpts, selected style
examples, cited facts, and page excerpts share one heterogeneous local batch.
If the checkpoint is absent, Laya stays unavailable until you explicitly warm
the local cache, for example:

```powershell
.\.venv\Scripts\python.exe -c "from laya import Router; Router(device='cpu', max_loaded=1).load('multilingual')"
```

That explicit command may download the checkpoint and needs network access and
disk space. Input documents are not uploaded for local inference. Local Laya
adds no OpenAI calls, but its CPU ranking takes time and its scores are not
calibrated for this app; no quality, speed, or credit saving is guaranteed.

When Laya is available and the lightweight preflight succeeds, the configured Job & Company
Research and Main Coordinator positions stay in the agent sequence but use local
logic instead of OpenAI requests. Research copies the separate web-search summary
and extracts requirements verbatim from the saved posting. The coordinator runs
after the reviewer, applies deterministic readiness checks, and cannot clear an
existing review flag. This saves
two of the four agent API requests. The separate current-company web search still
uses the OpenAI key, as do CV Match & Writer and Reviewer. If Laya fails, all four
configured API-backed agent stages run as before. As another local-only step,
the app reads at most three HTTPS pages already cited by company research, with
redirects disabled and bounded response/text sizes. It caches the extracted
visible text against the exact saved research revision; Laya ranks short excerpts
and the writer receives selected snippets with their source URLs. Page-read
failures leave the original sourced research intact.

Generation preflight is cached against only the job fields used by that prediction.
Generation ranking caches scores and source offsets against the job, CV, context,
knowledge, research revision, and extracted page text; the cache stores selection
metadata, not duplicate CV text. The existing keyword selector first narrows
knowledge examples. Laya may reduce that shortlist to two only when style
relevance and coherence scores are clear; otherwise the full shortlist is kept.
Candidate text is shortened only for long inputs and only when multiple exact
excerpts pass a conservative score threshold and materially reduce the input.
The UI shows the selected examples, company source links, elapsed local ranking
time, and candidate-character reduction. The Reviewer always receives the full
candidate sources. Local scores are uncalibrated and never establish facts or
hiring probability; if Laya fails, the app passes the original full inputs.

**Analizza con Laya** remains a separate detailed diagnostic, cached against the
job, CV, context, examples, and existing postings. It batches candidate, style,
and possible-duplicate checks. Detailed scores are advisory and do not establish
facts. The shipped checkpoint has not been calibrated on this candidate's
applications; even high confidence can be wrong.

Configured agents remain sequential and receive compact role/seniority advisories
along with previous-stage results. Automatic generation does not call the
unreliable draft-language/specificity scorer; the configured Reviewer remains
responsible for letter review. An unavailable runtime/model, malformed
probabilities, or SDK input truncation produces an explicit unavailable state;
generation falls back to the original API stages. Local scores never automatically
approve a letter.

Settings estimates API costs for fresh company research and a research-cache
hit. It includes every configured agent, observed cached-token rates, per-phase
usage when available, and search-tool calls, and shows
100/1,000-application projections. The low/high range is a sensitivity scenario,
not a statistical interval or an account bill. Laya's CPU electricity cost is not
measured. The pipeline does not automatically retry agent calls; manual
regeneration starts a new run.

SDK/model references: [Laya runtime](https://github.com/NandhaKishorM/laya),
[Laya 0.3.21 package and batch API](https://pypi.org/project/laya/0.3.21/),
[multilingual model card and license](https://huggingface.co/convaiinnovations/laya-multilingual).


