# Coordinamento degli agenti installati

La chat principale seleziona i ruoli necessari e delega con gli strumenti realmente disponibili.
Ogni consegna contiene obiettivo, R-ID, criteri, file/fonti, permessi, skills, risultato atteso e destinatario.
I link qui sotto registrano i nomi; la presenza dei file non prova caricamento o esecuzione.

PromptAgent prepara prompt su richiesta; AppStarter distingue NEW/EXISTING/RUNNING. Architect propone contratti;
Backend, Frontend e AIEngineer implementano nelle proprie aree; Reviewer restituisce rilievi in sola lettura.
Evaluation misura nel progetto; AgentOptimizer migliora le specifiche nel repository canonico. Non installare il secondo nella app come fase implicita.
Se un ruolo nominato non è elencato qui, non è installato da questi pack.

| ID | Codex | Claude Code | Cursor |
|---|---|---|---|
| ai-engineer | [codex](../.codex/agents/ai-engineer.toml) | [claude](../.claude/agents/ai-engineer.md) | [cursor](../.cursor/agents/ai-engineer.md) |
| architect | [codex](../.codex/agents/architect.toml) | [claude](../.claude/agents/architect.md) | [cursor](../.cursor/agents/architect.md) |
| backend | [codex](../.codex/agents/backend.toml) | [claude](../.claude/agents/backend.md) | [cursor](../.cursor/agents/backend.md) |
| frontend | [codex](../.codex/agents/frontend.toml) | [claude](../.claude/agents/frontend.md) | [cursor](../.cursor/agents/frontend.md) |
| prompt-agent | [codex](../.codex/agents/prompt-agent.toml) | [claude](../.claude/agents/prompt-agent.md) | [cursor](../.cursor/agents/prompt-agent.md) |
| reviewer | [codex](../.codex/agents/reviewer.toml) | [claude](../.claude/agents/reviewer.md) | [cursor](../.cursor/agents/reviewer.md) |

## Attivazione e diagnosi

Per le procedure condivise applica [requirement-traceability](../.agents/skills/requirement-traceability/SKILL.md).
Usa le copie .claude/skills in Claude Code; .agents/skills in Codex/Cursor. Le due copie devono essere equivalenti.
Le skills handoff e retro richiedono invocazione esplicita. Non precaricarle o aggirare blocchi leggendo il file.
Prima del lavoro dipendente verifica disponibilità, ID, formato, percorso, permessi, versione e ricaricamento.
Per la verifica effettiva segui [runtime-check](../.agents/skills/agent-configuration/references/runtime-check.md).
Per l'installazione consulta [installation](../.agents/skills/agent-configuration/references/installation.md).
Gli esiti vanno distinti: file generati, configurazioni controllate, elementi riconosciuti, chiamate verificate.
Nei runtime salva BenchmarkReport.md e OptimizationReport.md in agent-kit/reports/ (o nella directory report concordata fuori discovery), mai nelle cartelle runtime agents.
Eredita modello e autorizzazioni della sessione. La sola lettura resta un vincolo del ruolo; in Codex non è una sandbox separata.
Non modificare l'istanza attiva dell'app né eseguire test/benchmark senza autorizzazione pertinente.
