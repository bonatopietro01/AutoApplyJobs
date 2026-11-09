---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

Run only after explicit user invocation under the host's policy: `/handoff` in Claude Code or Cursor; the explicit skill selector in the current Codex client (for example `$handoff` in CLI/IDE). If the host requires that command, show it and wait. Reading this file does not bypass a blocked invocation. Apply the [shared invocation contract](../requirement-traceability/SKILL.md#contratto-di-chiamata-e-diagnosi).

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save to the temporary directory of the user's OS (`$TMPDIR`, else `/tmp`; `%TEMP%` on Windows) when that location and writing are permitted. A read-only role returns the text to the authorized coordinator to save; report an unavailable location instead of silently writing elsewhere.

Include a "suggested skills" section naming each skill, its accessible path, activation condition, required input and expected result. The next session resolves invocation against its actual host and tools; do not prescribe a universal `Skill` call. Mark explicit-only skills as requiring the user's command. Include target agent's registered ID if known and distinguish the document from an actual subagent call or message to another chat.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.
