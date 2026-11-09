---
name: safe-refactor
description: Plan or perform an authorized structural code change while preserving behavior. Use for extraction, consolidation, or ownership moves; validation remains bounded by the task's permissions.
---

# Safe refactor

Define the behavior-preservation boundary and identify the smallest relevant evidence before structural edits. A generic request to preserve or verify behavior does not authorize tests, builds, typechecks, lint, benchmarks or runtime actions.

- Keep feature changes outside refactor.
- Move one ownership boundary at a time.
- Preserve public interfaces, failure behavior, ordering, and compatibility unless explicitly scoped.
- Keep intermediate states coherent. Do not create, modify or delete tests without an explicit request; run tests or build checks only when separately authorized.
- Avoid dependency or configuration growth without correctness need.

Reuse existing evidence while it remains valid. After changes, repeat only checks invalidated by the affected files and only within authorized scope. If execution checks are not authorized, report the static evidence and propose the smallest next proof. Stop when requested structure is achieved and its evidence or limits are recorded.
