# User guide

This guide contains the full operating instructions for LinkedIn reading, the local dashboard, AI-assisted cover-letter preparation, and diagnostics. Run commands from the repository root. [Return to the README](../README.md).

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
`127.0.0.1`, using a free port assigned by the operating system. Pass
`--port <port>` to choose a fixed port; startup fails if that port is occupied.
The left column contains the saved jobs; selecting one shows its original
LinkedIn description, company and role analysis, personalization notes, editable
cover-letter text, and files. Search and status filters are local.

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
