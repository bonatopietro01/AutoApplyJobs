# LinkedIn Job Connector

LinkedIn Job Connector reads jobs that the user has manually saved on LinkedIn,
stores a local snapshot in SQLite, and exposes that snapshot through MCP. Its
local dashboard can generate per-job analysis and cover letters through the
user's own OpenAI API key. It does not apply to jobs, store LinkedIn credentials,
bypass security checks, or perform mass scraping.

## Status

Implemented capabilities:

1. Manual authentication in a dedicated, visible Chrome profile.
2. Read one saved job and its visible description.
3. Read the bounded saved-jobs list sequentially, with complete descriptions.
4. Persist and deduplicate complete records in `data/jobs.sqlite3`; each job has
   a `NEW`, `PROCESSED`, or `IGNORED` state.
5. Expose local-database tools through the official MCP Python SDK.
6. Document local MCP and ChatGPT connection options below.
7. Provide a loopback-only dashboard for viewing saved jobs, editing research
   notes and letters, and archiving PDF/DOCX versions per job.
8. Optionally process new jobs with a configurable AI workflow, keeping job
   status and results in SQLite and generating matching PDF/DOCX drafts.

The MCP server reads the saved SQLite snapshot; it does not open LinkedIn or
refresh the list during a tool call. To refresh it, run the explicit sync
command. The database, Chrome session, and browser profile are local and
excluded from Git.

## Project layout

```text
linkedin_connector/   Browser, data models, SQLite store, dashboard server, and reader
dashboard/             Local dashboard HTML, CSS, and JavaScript
mcp_server/            MCP server backed by the SQLite store
tests/                 Reader, persistence, dashboard, and MCP tests
templates/user_profile/ Neutral first-run Markdown templates with no candidate-specific data
browser_profile/       Local Playwright session; ignored by Git
data/                  Local database, per-user Markdown files, candidate CV, logs, and letters; ignored by Git
main.py                Authentication, read, sync, and dashboard commands
```

## Setup on Windows

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Google Chrome or Microsoft Edge must already be installed. The project uses a
dedicated browser profile, not your personal Chrome profile. Do not copy or
commit `browser_profile/`, `data/`, or any local session files.

## Local Laya decisions (automatic when available)

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

## Authenticate

```powershell
.\.venv\Scripts\python.exe main.py
```

If LinkedIn asks for authentication, use the visible browser yourself. Return
to the terminal and press Enter only after the LinkedIn feed is visible. On
later runs, the same dedicated profile is reused. If LinkedIn displays a
CAPTCHA, 2FA prompt, or security challenge, complete it manually; this
application will not try to bypass it.

## Read jobs

Read one saved job without persisting it:

```powershell
.\.venv\Scripts\python.exe main.py --read-one-saved-job
```

Read all saved jobs and print a metadata summary without persisting them:

```powershell
.\.venv\Scripts\python.exe main.py --read-all-saved-jobs --summary
```

The reader uses the Saved tab counter when available, paginates sequentially,
opens one temporary detail tab at a time, pauses between detail pages, and
stops on a challenge, rate limit, missing card, or incomplete description.
There is a per-run cap of 100 jobs.

## Sync to SQLite

```powershell
.\.venv\Scripts\python.exe main.py --sync-saved-jobs
```

This reads the complete saved list first, then atomically inserts or refreshes
records in `data/jobs.sqlite3`. Running it again deduplicates by LinkedIn job
ID, updates the latest job details, and preserves each job's existing state and
first-seen date. If any job cannot be read completely, the sync stops without
writing a partial batch. Database writes are local; `data/` is ignored by Git.

## Local dashboard

```powershell
.\.venv\Scripts\python.exe main.py --dashboard
```

Oppure fai doppio clic su `avvia_dashboard.bat`.

The command opens the dashboard in your browser and serves it only on
`127.0.0.1` (port 8765 by default). The left column contains the saved jobs;
selecting one shows its original LinkedIn description, company and role
analysis, personalization notes, editable cover-letter text, and files. Search
and status filters are local.

When the dashboard opens, it reads only the local SQLite snapshot; it does not
refresh LinkedIn in the background. **Solo nuovi annunci** requires confirmation
before reusing the project Chrome session. It reads recent saved-job cards from
the top of LinkedIn's list and stops at the first ID already in SQLite, so it
opens detail pages only for newly saved jobs. If the read is interrupted or
incomplete, no partial batch is written and the existing local list is kept.
In **Impostazioni → Collegamento AI**, **Ricerca e aggiornamento dei salvati**
reads the complete list currently visible on LinkedIn, up to the existing
100-job per-pass safety limit. It imports newly seen
offers without queuing AI and refreshes already-local records whose first local
observation falls within the chosen period (or all local records). This date is
not LinkedIn's save date. LinkedIn does not expose a history of offers removed
before the app first imported them, so those cannot be reconstructed. Existing
analyses, letters, statuses, and notes are retained. For a deliberate full
refresh of every saved description, the explicit `--sync-saved-jobs` command
also remains available.

At first launch, the app creates per-installation `data/user_profile/AGENTS.md`
and `data/user_profile/CANDIDATE_CONTEXT.md` from neutral templates only when
they do not already exist. Those files, the SQLite database, CV, examples, and
generated letters are local to that installation and must not be copied when
sharing the app. Your existing files are never replaced during startup.

For a new user, open **Impostazioni → Contesto del candidato**: upload a CV in
the same section as its Markdown context, check the extracted-text preview,
and use **Crea bozza dal CV**. The generated
Markdown is only a draft; review it, optionally answer or skip the short open
questions, then choose **Copia bozza nell’editor** and **Salva contesto**. The
current context stays intact until this explicit save. A newly uploaded CV
does not silently rewrite the context; the interface offers a reviewed draft
and a text diff. You can instead import supported text or documents, or edit
and paste your own information. This does not connect to a ChatGPT account.
Supported CV/context formats are text PDF, DOCX, TXT, and Markdown; scanned PDFs
cannot be read. CV uploads are limited to 10 MB and 50,000 extracted characters.
PDF text extraction uses `pypdf`; install updated requirements with
`python -m pip install -r requirements.txt` if needed.

### Automatic cover letters

Open **Impostazioni → Collegamento AI**. Enter your own OpenAI API key, save it,
then use **Verifica**. Upload and review a CV, review the candidate context,
read the data-transfer notice, and explicitly enable consent and automation.
**Cerca online** is enabled for new installations so each generated application
can use current company research; you can turn it off in Settings. New
applications are researched, analyzed, drafted, and saved as PDF and Word
automatically after import. The app never submits an application and never
opens LinkedIn just by opening the dashboard.

The key is stored by the operating system credential backend (Windows
Credential Manager on Windows), separate from the jobs database and project
files. If no secure backend is available, set the environment variable
`LINKEDIN_CONNECTOR_OPENAI_API_KEY` before launching the app. The environment
key takes precedence and must be removed from the environment, not the UI.
The API key is never returned to the browser after saving it. The settings
show only whether a key exists and where it came from. **Rimuovi** deletes a
saved system credential and disables automatic processing.

`data/user_profile/AGENTS.md` is the only persistent source of agent names,
roles, order, active state, and instructions. The guided cards and Markdown
editor read and update the same sections; other free-form Markdown sections are
preserved. New installations get neutral editable roles. If an older
`automation.json` also defines agents, Settings shows both lists and whether
they differ, makes recoverable copies, and asks which version to keep before
removing the duplicate definition. Changes to the model, agents, CV, context,
or shared rules mark older results for review rather than silently replacing
them.

When automatic processing is enabled, the local server queues newly imported
jobs and jobs with changed content, without duplicating completed work. Work
is bounded by the **Annunci per ciclo** control and resumes while the app is
running; pending work resumes after the next start. LinkedIn refresh remains a
separate, explicit **Solo nuovi annunci** action. The full recovery scan is
also explicit and does not queue AI for new imports. Those jobs remain eligible
for the **Crea solo le nuove** bulk option. The reader remains read-only.

During a full scan, the visible LinkedIn job IDs are tracked separately from
whether each description can be read. A single unavailable detail is retried
once and then reported while the reader continues; an existing local record and
its materials are kept, and a new item without a complete description is not
imported. Jobs are hidden from the active list only after the complete Saved
ID list is verified. The latest recovery report persists across dashboard
reloads and shows start/end times, duration, additions, actual field changes,
unchanged count, removals from Saved, preserved letter/file totals, and
unreadable or unimported details. It distinguishes the latest attempt from the
last fully successful comparison. Per-job `last_seen_at` and `last_changed_at`
are separate so an unchanged scan does not look like a data modification.

Automatic generation also skips jobs whose local `first_saved_at` timestamp is
older than **Età massima per l’elaborazione automatica** (90 days by default,
configurable from 1 to 3,650 days in Settings). This is the first date the app
stored the job, not LinkedIn's publication date; older jobs and their materials
are retained. Their detail view explains the skip, and the per-job regenerate
button remains an explicit override. The app does not individually query old
LinkedIn URLs to guess whether a posting closed.

Each item in the left-hand job list has a small **⋯** menu with **Rimuovi dalla memoria**. After confirmation, the job and its analysis, company research, chat, and generation history are deleted from the local database. A local hash of the LinkedIn job ID is retained only to keep a still-saved LinkedIn job from being imported again. Historical call/token totals remain, but are unlinked from that job. PDF/DOCX files already stored under `data/cover_letters/` and files downloaded to the selected folder are not deleted; they remain on disk but are no longer listed in the dashboard. This does not unsave or otherwise change anything on LinkedIn. Removal is refused while that job is actively being generated.

Each job has **Rigenera analisi e lettera** and a separate company research
refresh. Research uses OpenAI's Responses API `web_search` tool with
`gpt-4.1-mini` and the same key (independent of the selected writing model),
performs a real online search (up to three built-in tool calls per research response), prefers
official sources, and stores the consultation time, sourced claims, URLs,
source titles, and a distinction between documented facts and interpretations.
The sources are clickable in that job. Job pages are untrusted data; their
instructions are not followed. Successful research is cached for up to seven
days, and a recent failed attempt is briefly cached for 15 minutes to avoid
paying to repeat the same failing lookup during immediate retries. An explicit
**Cerca / aggiorna fonti** action still requests a fresh search. If search is
disabled, hits a limit, or fails, generation can continue from available
verified material and flags the lack of sources for review.

To revise a single job, use **Rigenera analisi e lettera**. The 1–10 score and
comment are saved locally against the active version; they do not call the AI
or change `AGENTS.md`. The company research panel displays a text summary,
sourced claims, and clickable links to the consulted websites.

**Impostazioni → Cover Letter knowledge** holds up to ten local PDF, Word, or
text examples (maximum 8 MB per file and 30,000 extracted characters each).
The pipeline selects at most three relevant examples per job to limit token
use. You can preview extracted text, replace, or remove each file; from an open
job you can also save the current cover letter as a style example. Examples
are not agent instructions and their facts are not copied as candidate
evidence. If none are loaded, generation proceeds normally. With consent
enabled, selected example text may be sent to OpenAI along with job inputs.

Settings also provides a bulk regeneration preview. Choose already-saved
letters or **Crea solo le nuove** to select applications without an existing
letter text or archived PDF/DOCX, then choose a recent date window, review a
local cost and time range, and explicitly confirm the exact previewed set. The
estimate uses recent local token averages where available and
public standard API prices, or a conservative fallback; it cannot read the
account balance, discounts, or contract pricing and is not a quote. The queue
respects daily call limits. You can cancel work not yet started; a running job
is allowed to finish.

The selected main model applies to agents by default. Each agent can inherit
that choice or select a different model in **Agenti configurabili**. Neutral
first-run instructions assign low-cost `gpt-4.1-nano` to routine research and
coordination roles; model assignments remain editable in Settings and in the
local `AGENTS.md` metadata.

If you edit a saved letter, click **Salva modifiche**, then **Crea PDF e Word dal
testo** to archive a new matching document pair. Previous files remain in the
local archive when new versions are created.

Each API call is bounded by the total daily-call ceiling and the separate
daily limits for automatic agent stages, company research, and
revisions/context drafts. All limits can be changed in Settings; a value
of zero disables that category. Detailed usage counters are tucked into the
**Uso AI** dropdown beside the saved-job count. These are call-count limits,
not a money-denominated budget; actual charges depend on model, token volume,
account-specific terms, and provider pricing. Check the OpenAI usage page for
actual charges. The Responses API uses structured output and `store: false`;
this avoids retaining responses for API retrieval, but does not mean the
provider does no processing under its policies. No key, CV, complete job text,
chat, sample letter, or generated letter is written to app logs.

When an automatic cover-letter job reaches a daily API-call limit, it stays in
the queue instead of becoming a permanent error. The worker resumes it after
the UTC daily counter resets, or sooner after the relevant limit is increased
in Settings. Jobs previously marked with the `usage_limit` error are returned
to the queue when the application starts; other errors still require a manual
retry. The dashboard shows that queued jobs are waiting on the daily limit.

You can still manually edit analyses and letters, or upload a final PDF/DOCX.

The dashboard itself listens only on localhost. However, `.gitignore` only
prevents Git tracking; it does not disable OneDrive or another folder-sync
service. If the project is stored in a synchronized folder, its `data/` files
may also be synchronized by that service. Keep `data/` out of any app package
shared with another person.

Generated and uploaded PDF/DOCX files are archived in new versions under
`data/cover_letters/<linkedin_job_id>/`; the dashboard offers PDF preview and
Word opening through Windows' default app. In **Impostazioni**, choose a
destination folder in Chrome or Edge; then **Scarica** writes the chosen file
to that folder, inside a company-and-role subfolder named with the LinkedIn job
ID so positions with the same title do not overwrite each other. Without a selected folder,
the browser's standard download location is used. Automatic generation saves
files in the app's internal archive; click **Scarica** for each format to copy
it to the chosen external folder. The folder permission is
managed by the browser and can be removed from the same settings menu. Each PDF
and Word archive file keeps its own version sequence. The app validates file
type and size, and never opens an uploaded Word document without a separate
user click and confirmation.

### Diagnostica e log

The **Impostazioni → Diagnostica e log** section shows recent local application
events, with filters for severity and component, text search, and expandable
redacted error details. Requests have a trace ID shown in the `X-Request-ID`
response header and in the viewer; use it to connect a dashboard error to its
server-side events. The JSON Lines files are stored under `data/logs/` (by
default `data/logs/events.jsonl`, alongside the local database). Rotation is
limited to 5 MB per file and five backups, for a maximum of six files.

Logs contain timestamps, severity, component, event names, safe operational
counts, request IDs, durations, and redacted exception traces. They do not
record passwords, cookies, tokens, full request bodies, job descriptions,
cover-letter text, or agent prompts. Browser errors keep only the error class,
HTTP status and local app file/line frames; raw browser messages and stack text
are discarded. These events are size- and rate-limited and accepted only from
the local dashboard origin. Log files
are read locally by the dashboard, are not uploaded, and remain excluded from
version control with the rest of `data/`. If the log folder cannot be opened,
the application continues without file logging.

Stop the server with Ctrl+C in the terminal. The dashboard has no public
network listener and all saved letter files remain in the ignored local `data/`
directory.

## MCP server

The MCP server is read-only except for the explicit `mark_processed` tool. Its
tools always operate on the local SQLite snapshot:

| Tool | Purpose |
| --- | --- |
| `get_new_jobs` | List jobs with status `NEW` |
| `get_saved_jobs` | List all jobs or filter by `NEW`, `PROCESSED`, `IGNORED` |
| `get_job` | Fetch one job by LinkedIn job ID |
| `search_jobs` | Search title, company, location, and description |
| `mark_processed` | Change one local job state to `PROCESSED` |

For a local MCP client that supports launching a stdio server, run its command
from the project directory:

```powershell
.\.venv\Scripts\python.exe -m mcp_server.server
```

The process waits silently for an MCP host over stdin/stdout. Do not type into
that terminal; stop it with Ctrl+C. A compatible host should launch this
process directly using the same command and project directory. The server
does not expose an HTTP port by default.

## Connect to ChatGPT

ChatGPT does not launch a local stdio server directly. Its custom MCP app
connection requires a remote MCP route; for a developer machine, use OpenAI's
Secure MCP Tunnel rather than exposing this server or opening an inbound port.
This project does not create a tunnel or upload the database automatically.

1. Confirm that your ChatGPT account/workspace supports Developer Mode and the
   MCP actions you intend to use. Availability and write-action permissions
   depend on your plan and workspace administrator.
2. In the OpenAI Platform, create a Secure MCP Tunnel and associate it with the
   ChatGPT workspace you will use. You need the appropriate tunnel permissions
   and a runtime API key. Keep that key in your environment or tunnel-client
   configuration; never put it in this repository.
3. Install the official `tunnel-client` using the download provided in Platform
   tunnel settings. In PowerShell, from this project directory, initialize a
   local stdio profile that launches:

   ```powershell
   $env:CONTROL_PLANE_API_KEY = "<runtime API key>"
   tunnel-client init --sample sample_mcp_stdio_local --profile linkedin-jobs `
     --tunnel-id "<tunnel id>" `
     --mcp-command ".\.venv\Scripts\python.exe -m mcp_server.server"
   tunnel-client doctor --profile linkedin-jobs --explain
   tunnel-client run --profile linkedin-jobs
   ```

   Keep the terminal running while using the ChatGPT app. Follow the official
   Secure MCP Tunnel guide if the current `tunnel-client` release changes its
   initialization syntax.
4. In ChatGPT Developer Mode, create a custom app and choose the Tunnel
   connection. Select the tunnel, scan the tools, and review the available
   actions before creating the app. Start a chat, select the app, and ask it to
   search or summarize your saved jobs. Confirm any request to run
   `mark_processed` only when you want that job's local state changed.

Only tool results requested in ChatGPT are sent to ChatGPT; the tunnel keeps
the MCP process private but does not make job descriptions private from the AI
service using those tools. Read OpenAI's current guides before configuring
account or organization permissions:

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)

## Checks

```powershell
.\.venv\Scripts\python.exe -m compileall -q linkedin_connector mcp_server tests main.py
.\.venv\Scripts\python.exe -m unittest discover -v
```
