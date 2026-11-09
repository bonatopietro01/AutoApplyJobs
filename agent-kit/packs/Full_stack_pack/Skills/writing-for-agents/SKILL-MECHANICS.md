# Skill mechanics

The skill-specific branch of [writing-for-agents](SKILL.md): packaging, activation policy and router skills. The writing guidance stays in `SKILL.md`; installation and runtime references stay in [agent-configuration](../agent-configuration/SKILL.md).

## Package and identity

Use one directory per skill, with an exact `SKILL.md` entry point. Begin the file with `---` on its first line and valid YAML frontmatter containing `name` and `description`. Keep the name aligned with the directory and unique within the installed catalog. Include required scripts, references and metadata with working relative links; a reference file is not a separate skill.

Use the description as a concise discovery pointer: say what the skill does and the concrete conditions for using it. Put procedures in the body and conditional detail in linked resources. The host decides which metadata it loads, when it refreshes the catalog and which invocation controls it supports; do not promise universal context loading or zero token cost.

## Invocation policy by host

Choose whether the model may select the skill when relevant or the user must explicitly request it. Preserve that decision when adapting a skill; a routing instruction does not override a host-enforced restriction.

| Host | Model selection and explicit invocation | Explicit-only metadata |
|---|---|---|
| Codex | Use the actual client's skill selector; CLI/IDE can use `$skill-name`. | Preserve `policy.allow_implicit_invocation: false` in `agents/openai.yaml`. Do not assume a Claude frontmatter field controls Codex. |
| Claude Code | Ordinary skills may be selected by Claude or invoked with `/skill-name`. | `disable-model-invocation: true` reserves invocation for the user. Such skills must not be placed in a subagent's `skills` preload list. |
| Cursor | Skills can be discovered when relevant or invoked with `/skill-name`. | Preserve `disable-model-invocation: true` for explicit-only skills. |

Check the installed client/version and visible catalog. The shared package can carry metadata for more than one host, but support for a field in one program does not prove support in another. If the required invocation is unavailable, diagnose it before dependent work; do not bypass a blocked skill by reproducing its procedure from files.

In Claude Code, a subagent with a tool allowlist needs access to `Skill` to invoke skills that way. Preload only always-needed, model-invocable procedures compatible with the role. A preload is not a declaration that every available skill must run. In other hosts use the capabilities exposed by that session, rather than copying Claude tool names or frontmatter options.

## Splitting by invocation

Create a separate model-invocable skill when it has a distinct trigger or several procedures need it. Keep shared neutral reference in a linked resource when no independent invocation is needed. Two explicit-only skills can share that resource; neither automatically invokes the other. Preserve a single procedure source instead of copying bodies into agent configurations or navigation files.

## Router skills

A router names the relevant procedure, its condition, inputs and expected result. It can select a model-invocable skill using supported mechanisms. For an explicit-only skill it explains the user's required command and waits when required by the host. A link is navigation, not execution; selecting a skill is distinct from dispatching a subagent.

Consulted 2026-10-14: [Codex skills](https://developers.openai.com/codex/skills), [Claude Code skills](https://code.claude.com/docs/en/skills), [Claude Code subagents](https://code.claude.com/docs/en/sub-agents), [Cursor skills](https://cursor.com/docs/skills). Check current official references when the destination version behaves differently.
