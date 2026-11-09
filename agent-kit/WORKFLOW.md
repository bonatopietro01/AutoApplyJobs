# Coordinamento degli agenti installati

Solo la chat principale avvia i subagenti e Reviewer. Raccoglie i checkpoint degli owner e avvia, quando serve, una sola review finale sul lavoro integrato, passando TaskState e checkpoint.
Review intermedie solo per blocker o decisioni che fermano il lavoro. Per una modifica locale a un file senza impatti su contratti, sicurezza, dati o autenticazione basta la checklist breve; altrove proporziona la profondità al rischio.
Dopo un rilievo fondato, l'owner corregge una volta; il recheck riguarda il difetto e i file dipendenti. La chat principale non ripete controlli validi già riportati dal Reviewer.
Registra comandi/metodi, esiti e stato; riusa i risultati validi e ripeti solo i controlli invalidati dalle modifiche.
Grilling cerca prima i fatti con lettura o ricerca diretta; DESIGN-IT-TWICE non impone subagenti: la chat principale decide se l'esplorazione indipendente serve.
Ogni consegna contiene obiettivo, R-ID, criteri, file/fonti, permessi, skills, risultato atteso e destinatario.
La copertura resta una sola volta nel TaskState; i checkpoint riportano file, sintesi, controlli con comando/esito, rischi, blocker e prossimo destinatario.
I link qui sotto registrano i nomi; la presenza dei file non prova caricamento o esecuzione.

PromptAgent prepara prompt su richiesta; AppStarter distingue NEW/EXISTING/RUNNING. Architect propone contratti;
Backend, Frontend e AIEngineer implementano nelle proprie aree; Reviewer restituisce rilievi in sola lettura.
Evaluation misura nel progetto; AgentOptimizer migliora le specifiche nel repository canonico. Non installare il secondo nella app come fase implicita.
Se un ruolo nominato non è elencato qui, non è installato da questi pack.

| ID | Codex | Claude Code | Cursor |
|---|---|---|---|
| ai-engineer | [codex](../.codex/agents/ai-engineer.toml) | [claude](../.claude/agents/ai-engineer.md) | [cursor](../.cursor/agents/ai-engineer.md) |
| app-starter | [codex](../.codex/agents/app-starter.toml) | [claude](../.claude/agents/app-starter.md) | [cursor](../.cursor/agents/app-starter.md) |
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
Non modificare l'istanza attiva dell'app. Test, typecheck, build, lint, benchmark e runtime richiedono autorizzazione pertinente; la parola verifica da sola non basta.
Non creare, modificare o eliminare test senza richiesta esplicita. Riusa esiti validi e ripeti solo controlli invalidati dalle modifiche.
