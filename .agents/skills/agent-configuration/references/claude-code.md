# Adattamento Claude Code

Fonti ufficiali consultate il 14 ottobre 2026: [sub-agents](https://code.claude.com/docs/en/sub-agents), [skills](https://code.claude.com/docs/en/skills), [memoria di progetto](https://code.claude.com/docs/en/memory).

Il generatore scrive `.claude/agents/<id>.md` con frontmatter `name`, `description`, `model: inherit`. PromptAgent, Architect e Reviewer hanno `tools: Read, Glob, Grep, Skill`: niente scritture o shell in questa allowlist. Gli altri ruoli ereditano i tool della sessione. Prima della chiamata controlla che i tool ammessi esistano: una versione o policy incompatibile richiede diagnosi e adattamento mirato, non una chiamata inventata.

`Skill` è compreso nell'allowlist dei ruoli in sola lettura; questo non autorizza scritture o procedure incompatibili. Le skills risiedono in `.claude/skills`. Il generatore non precarica procedure: usa `skills:` soltanto per esigenze permanenti verificate; il precaricamento inserisce il corpo completo. Le skills con `disable-model-invocation: true` non vanno precaricate.

Handoff e retro richiedono l'invocazione utente `/handoff` e `/retro`. Se il programma blocca la chiamata, non riprodurre la procedura tramite lettura del file per aggirarlo. Le risorse ordinarie si leggono quando la procedura autorizzata le richiede.

Usa `/agents` per ispezionare il catalogo quando disponibile. Delega attraverso lo strumento realmente esposto dalla versione (`Agent` nelle versioni correnti); non copiare parametri da un'altra piattaforma. Il subagente non riceve la cronologia della chat: passa una consegna autonoma con requisiti, file, permessi, skills e output. La chat principale coordina i passaggi. Le istruzioni brevi in `CLAUDE.md` puntano al workflow installato. Dopo nuove configurazioni verifica riconoscimento e necessità di ricaricare la sessione.
