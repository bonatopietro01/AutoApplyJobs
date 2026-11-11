# MCP and ChatGPT guide

This guide documents the MCP tools backed by the local SQLite database and the optional ChatGPT connection. The local MCP server does not browse LinkedIn or refresh the saved-jobs list. [Return to the README](../README.md).

## MCP server

The MCP server operates on the local SQLite snapshot. Read tools do not browse
LinkedIn. Its local write actions are limited to marking a record processed and
saving a ChatGPT-prepared application with its generated documents:

| Tool | Purpose |
| --- | --- |
| `get_new_jobs` | List jobs with status `NEW` |
| `get_saved_jobs` | List all jobs or filter by `NEW`, `PROCESSED`, `IGNORED` |
| `get_job` | Fetch one job by LinkedIn job ID |
| `search_jobs` | Search title, company, location, and description |
| `mark_processed` | Change one local job state to `PROCESSED` |
| `check_prepared_job_url` | Check one canonical URL against ChatGPT-prepared records only, returning compact metadata |
| `get_cover_letter_context` | Return the saved CV, candidate context, Writer guidance, and up to three relevant cover-letter examples for a named offer |
| `save_prepared_job` | Save a ChatGPT-prepared offer, announcement and application URLs, analysis, sources, letter, PDF, and DOCX without queueing app API generation |

`get_cover_letter_context` sends only the requested, bounded excerpts to the
connected ChatGPT app: up to 14,000 CV characters, 8,000 candidate-context
characters, 6,000 Writer-instruction characters, and at most three existing
style examples. The examples guide tone and structure; they are not evidence
of the candidate's experience. The MCP server does not log these returned
contents. Review your profile and examples before enabling access.

`save_prepared_job` stores the record with origin `chatgpt`; the dashboard shows
it in “Il tuo spazio candidature: Open AI”. Jobs and letters prepared through
the app remain in the LinkedIn space. The same canonical URL may exist once in
each space; repeat saves inside one space keep the existing record and letter.
The MCP save call requires the announcement URL and application URL separately.
Each URL is limited to 2,000 characters.
It accepts up to 50 source references, each at most 2,000 characters, with a
30,000-character combined limit. The description and cover letter are limited
to 50,000 and 60,000 characters respectively; tool validation happens before
PDF/DOCX rendering.

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

1. Confirm that you are using ChatGPT web in a Business or Enterprise/Edu
   workspace and have permission to add and use custom MCP servers. Current
   OpenAI documentation lists custom MCP connections and full MCP support for
   those plans on web only; custom MCP servers are not available in the ChatGPT
   mobile app. Business workspace admins/owners and Enterprise/Edu authorized
   users have different creation and publishing permissions.
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
4. In ChatGPT web, open Plugins, select the plus button, then “Add custom MCP
   server”. Choose Tunnel, select the running tunnel, configure authentication,
   accept the risk warning, and create it as a plugin. Review the discovered
   read and write tools before relying on them. Scheduled-task support for this
   custom MCP action still needs to be confirmed in the target account; actions
   that require approval may pause a task until approved.
5. Edit the existing ChatGPT scheduled task only after confirming that its
   account supports the custom app and its read and write actions. Keep its
   current schedule, time zone, and profession filters. Add the following
   instructions to that task; do not create another schedule:

   ```text
   Use the connected Job Letter Studio MCP app on every run. Do not rely on
   files attached to this project or on memory from an earlier chat.

   Keep the existing search schedule, Switzerland geography, and professional
   areas already specified in this task. Exclude jobs that require German.
   Search only for genuinely new matching offers, up to 20 per run. Do not
   backfill old shortlists or force the count to 20 when fewer suitable new
   offers exist. Before preparing each offer, call check_prepared_job_url with
   its announcement URL. Skip it only when that tool returns found=true for an
   origin=chatgpt record. A LinkedIn or manual record with the same URL is not
   a duplicate for this routine: prepare and save a separate Open AI record.
   The tool returns only a compact match, not descriptions or letters. Continue
   searching only while there are more qualifying new offers and the
   20-new-offer limit has not been reached.

   For each new offer, call get_cover_letter_context with its title, company,
   location, and complete description before writing. If the announcement is
   unavailable or its description is incomplete, do not fabricate missing
   fields; report it under offers to verify or failed. Use the CV and confirmed
   candidate context for facts about the candidate. Use style examples only
   for tone, structure, length, and detail. Never copy example experience or
   invent skills, languages, qualifications, results, work authorization, or
   availability. Put uncertain personal requirements in role_analysis and
   explicitly label them “Da verificare”. If required context is missing,
   report the gap and do not invent a replacement. If style_examples is empty,
   state that no style examples were available and follow only the writing
   instructions returned by the tool; do not claim or infer a saved preference.

   Prepare a tailored cover letter and call save_prepared_job once for each
   qualifying offer. Pass job_url as the announcement URL and application_url
   as the actual application link; if the same page serves both purposes, use
   that URL for both. Include the complete description, source URLs,
   role_analysis (fit and requirements to verify), company_analysis, and
   personalization_notes. Never fill or submit application forms and never
   use the app's API generation pipeline; the user applies manually.

   Count a new offer as saved only when the MCP result confirms success=true
   and created=true. Treat created=false as a duplicate, preserve the existing
   record, and do not rewrite its letter. If the MCP call fails or the result
   is uncertain, report it as failed and do not claim it was saved. Report
   separately: saved, duplicates, offers with requirements “Da verificare”,
   and failed saves. Include each offer's title, company, and announcement
   URL in the run summary. If there are no new matches, say so.
   ```

   A task that needs approval for each write is not unattended. Test one
   controlled run and confirm the record appears in the Open AI space before
   relying on it.

The routine, account permissions, custom app and tunnel are not activated by
this repository. Scheduled-task support for a custom MCP write action must be
checked in the target ChatGPT account. This local setup also requires the PC,
app database and tunnel client to stay available during the run.

Only tool results requested in ChatGPT are sent to ChatGPT. This includes CV,
candidate context and cover-letter examples when `get_cover_letter_context` is
called. The tunnel keeps the MCP process private but does not keep requested
contents private from the AI service using those tools. Read OpenAI's current
guides before configuring account or organization permissions:

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
