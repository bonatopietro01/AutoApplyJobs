# Adattamento Cursor

Fonti ufficiali consultate il 14 ottobre 2026: [subagents](https://cursor.com/docs/subagents), [skills](https://cursor.com/docs/skills), [skills help](https://cursor.com/help/customization/skills), [rules](https://cursor.com/docs/rules).

Il generatore scrive `.cursor/agents/<id>.md` con `name`, `description`, `model: inherit` e `readonly` secondo il ruolo. Non trasferisce campi Claude quali `tools` o `skills`. Le istruzioni comuni sono in `AGENTS.md`, senza aggiungere regole `.mdc` superflue.

Cursor può leggere anche gli agenti compatibili in `.claude/agents` e `.codex/agents`; la configurazione `.cursor` dello stesso nome ha precedenza. Mantieni identità coerenti e controlla il catalogo del programma, specialmente con estensioni, configurazioni globali o versioni differenti.

Le skills usano `.agents/skills`. Cursor scopre anche `.claude/skills`: le copie installate dal generatore sono identiche, compresi riferimenti, script e politiche. Non assumere una precedenza documentata fra skills omonime; se compaiono più voci verifica provenienza e contenuto. Una copia divergente è un conflitto e va risolta prima del lavoro dipendente.

Per una skill con `disable-model-invocation: true`, chiedi il richiamo esplicito `/nome-skill`; conserva questa politica per handoff e retro. Per un ruolo usa il nome registrato nel comando o nella richiesta supportati dalla versione e lo strumento di delega esposto dalla sessione. Un collegamento Markdown non esegue né un agente né una skill. Se manca il riconoscimento, controlla percorso, frontmatter, modalità, versione, permessi e necessità di una nuova sessione.
