# Design It Twice

Use this method when the user asks to compare materially different interfaces or when a consequential design choice remains unresolved after direct analysis. Based on "Design It Twice" (Ousterhout): the first idea may not be the best. The main chat decides whether independent exploration is worth delegating; this skill never starts sub-agents itself.

Uses the vocabulary in [SKILL.md](SKILL.md): **module**, **interface**, **seam**, **adapter**, **leverage**.

## Process

### 1. Frame the problem space

Before comparing alternatives, write a concise explanation of the problem space for the chosen candidate:

- The constraints any new interface would need to satisfy
- The dependencies it would rely on, and which category they fall into (see [DEEPENING.md](DEEPENING.md))
- A rough illustrative code sketch to ground the constraints, not a proposal, just a way to make the constraints concrete

Share this with the user or coordinator. Continue with direct analysis when it can settle the choice.

### 2. Explore alternatives

Do not require a minimum number of agents. If the main chat authorizes independent exploration, give each owner a distinct design constraint and a bounded brief; otherwise compare a small number of plausible alternatives directly.

Use separate technical briefs only for authorized independent exploration (file paths, coupling details, dependency category from [DEEPENING.md](DEEPENING.md), what sits behind the seam). Select only the design constraints that expose meaningful trade-offs; they are options, not a required number of agents:

- Minimize the interface: aim for 1–3 entry points; maximize leverage per entry point.
- Maximize flexibility: support only the use cases and extensions justified by requirements.
- Optimize for the common caller: make the default case simple.
- When relevant, design around ports and adapters for cross-seam dependencies.

Include [SKILL.md](SKILL.md) vocabulary in briefs so alternatives use consistent architecture language.

Each alternative should describe:

1. Interface (types, methods, params, plus invariants, ordering, error modes)
2. Usage example showing how callers use it
3. What the implementation hides behind the seam
4. Dependency strategy and adapters (see [DEEPENING.md](DEEPENING.md))
5. Trade-offs: where leverage is high, where it's thin

### 3. Present and compare

Present designs sequentially so the user can absorb each one, then compare them in prose. Contrast by **depth** (leverage at the interface), **locality** (where change concentrates), and **seam placement**.

After comparing, give your own recommendation: which design you think is strongest and why. If elements from different designs would combine well, propose a hybrid. Be opinionated: the user wants a strong read, not a menu.
