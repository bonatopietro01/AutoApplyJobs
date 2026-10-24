# Full_stack_pack

Sviluppare funzionalità o correggere problemi in un progetto esistente. Include PromptAgent, Architect, Backend, Frontend, AIEngineer e Reviewer; usa soltanto le aree presenti.
## Prompt Agent nel workflow

Il pack include [PromptAgent](Agents/PromptAgent_General.md). Usalo quando vuoi trasformare un'idea o una richiesta in un prompt preciso, oppure migliorare un prompt esistente. Richiama writing-for-agents e, per decisioni importanti ancora aperte, grilling; ricava dai file i fatti accessibili prima di farti domande.

Dopo i chiarimenti prepara il prompt con obiettivo, requisiti, vincoli, criteri di riuscita e passaggio ai ruoli pertinenti. La creazione di un PRD non è prevista automaticamente da grilling. PromptAgent resta dedicato ai prompt e non implementa codice né sostituisce l'analisi degli specialisti.

Puoi chiedere: «Usa PromptAgent per chiarire questa richiesta con grilling dove necessario e scrivere il prompt operativo; poi passa il lavoro ai ruoli di sviluppo pertinenti». Se la richiesta di sviluppo è già definita, puoi coinvolgere direttamente i responsabili senza una fase obbligatoria di PromptAgent.
## Come usarlo

1. Copia tutta questa cartella nel progetto, per esempio sotto agent-kit/, mantenendo Agents/ e Skills/ affiancate.
2. Apri una chat con accesso ai file del progetto e incolla il contenuto di [PROMPTME.md](PROMPTME.md). Aggiungi l'idea, la richiesta o il risultato desiderato.
3. PROMPTME.md contiene le istruzioni di attivazione: lettura del progetto, selezione delle skills, collaborazione, limiti e consegna.
4. In una chat senza accesso al filesystem allega le specifiche pertinenti, WORKFLOW.md e le skills necessarie con le loro risorse. Un percorso locale da solo non fornisce il contenuto.
5. Per Evaluation scegli soltanto il prompt della fase voluta: benchmark nel progetto oppure miglioramento nel repository canonico.

## Contenuto e aggiornamenti

- [PROMPTME.md](PROMPTME.md): prompt da incollare.
- [WORKFLOW.md](WORKFLOW.md): collaborazione e regole comuni.
- Agents/: ruoli selezionati per questo pack, incluso PromptAgent.
- [Skills/README.md](Skills/README.md): catalogo delle 14 skills, conservate con tutte le risorse disponibili per evitare dipendenze locali mancanti. Non vanno caricate tutte per ogni attività.

Le specifiche e le skills sono copie per esportazione; le fonti canoniche restano Agents/ e Skills/ nel repository originale. Le copie non si aggiornano automaticamente. Rigenera il pack dalle fonti aggiornate prima di distribuirlo nuovamente; preserva separatamente le personalizzazioni del progetto di destinazione. Strumenti, servizi e capacità esterni richiesti dalle skills devono essere disponibili nell'ambiente: il pack non li installa.