---
name: grilling
description: Clarify material open decisions about a plan or idea when the user asks for grilling or the task cannot proceed safely without those decisions. Do not interview when the requirements are already complete.
---

Clarify only material decisions that remain open. Map them as a **design tree**: every decision branches into the decisions that depend on it. If the task is already sufficiently specified, continue without an interview.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

Format a round like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Word each question so "yes" accepts your recommended answer.

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. First use direct, scoped file or tool searches when they can answer the question. Do not start a sub-agent from this skill: if independent exploration is genuinely needed, present that to the main chat, which decides whether to delegate. The _decisions_ are the user's: put each necessary decision to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.
