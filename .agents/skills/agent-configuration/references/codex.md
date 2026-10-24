# Adattamento Codex

Fonti ufficiali consultate il 14 ottobre 2026: [subagents](https://developers.openai.com/codex/subagents), [skills](https://developers.openai.com/codex/skills).

Il generatore scrive `.codex/agents/<id>.toml` con `name`, `description` e `developer_instructions`. `name` è l'identità registrata; il filename coincide. Modello e ragionamento restano ereditati. La preferenza Codex di PromptAgent è un'indicazione nelle istruzioni, non una modifica automatica del modello.

I permessi del subagente provengono dalla sessione. Impostare `sandbox_mode` nel TOML personalizzato non garantisce una sandbox separata: il generatore non aggiunge quel campo. PromptAgent, Architect e Reviewer mantengono la sola lettura come vincolo nelle istruzioni. Verifica i permessi effettivi prima di delegare.

Le skills sono in `.agents/skills/<nome>/SKILL.md`. Conserva `agents/openai.yaml`, inclusa `policy.allow_implicit_invocation: false` per handoff e retro. Nella CLI/IDE il richiamo esplicito usa `$nome-skill` o il selettore `/skills`; nelle altre interfacce usa il selettore effettivamente disponibile. Non interpretare `/nome-agente` come un comando Codex universale.

La chat principale usa lo strumento di delega esposto, con il nome registrato e i parametri documentati nella sessione. Le istruzioni di progetto autorizzano la delega pertinente senza imporre tutti i ruoli. Controlla eventuali impostazioni `[agents]`, attendibilità del progetto e limiti della sessione. Il generatore non sovrascrive `config.toml`. Se gli elementi non compaiono, diagnostica il catalogo e la versione e riapri la sessione quando necessario.
