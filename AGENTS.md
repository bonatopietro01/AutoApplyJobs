# Agenti di sviluppo

Per le richieste di implementazione, usa una successione coordinata: analyzer produce analisi e TaskState in sola lettura; writer possiede l'implementazione e le verifiche autorizzate; reviewer resta in sola lettura ed è attivato secondo il gate stabilito dall'Analyzer. Per attività trasversali usa architect; assegna backend, frontend o ai-engineer solo per aree distinte e con un unico owner per file.

Per richieste esplicite di preparazione di un prompt usa prompt-agent; non implementare il prompt salvo richiesta separata.

Il coordinatore passa i risultati tra le fasi e prosegue il lavoro risolvibile quando un ruolo esaurisce i tentativi. TaskState compatto: obiettivo, scope, fonti autorizzate, criteri, vincoli, file pertinenti, fatti, assunzioni, piano, verifiche, gate e blocker. Evita transcript completi e segreti.

Gli agenti ereditano modello e permessi di esecuzione della sessione: non dedurre sandbox o runtime dal solo file di configurazione. Preferenze di ragionamento: Analyzer e Reviewer high; Writer e PromptAgent medium; gli specialisti seguono la sessione. Le istruzioni superiori e quelle dell'utente prevalgono. Distingui sempre verifiche locali da servizi reali.


Gate di review: NONE per cambi isolati a basso rischio; OPTIONAL per impatto condiviso medio; REQUIRED per autenticazione, sicurezza o privacy, persistenza o migrazioni, integrazioni esterne sostanziali, cancellazioni, refactor ad alto impatto o richiesta esplicita. Non abbassare un gate REQUIRED.

<!-- agent-pack:start -->
## Agenti e skills del progetto

Per task coperti dai ruoli installati, la chat principale coordina la delega pertinente. Leggi [workflow e nomi registrati](agent-kit/WORKFLOW.md), poi solo le skills necessarie. Per capacità mancanti diagnostica prima del lavoro dipendente. Mantieni istruzioni e permessi di questo progetto.
<!-- agent-pack:end -->
