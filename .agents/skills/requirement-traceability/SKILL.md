---
name: requirement-traceability
description: Mantieni ID, criteri, responsabili ed evidenze dei requisiti attraverso analisi, modifiche e review. Usala per richieste articolate o handoff tra ruoli.
---

# Tracciabilità dei requisiti e handoff

## Scopo

Mantieni ogni requisito riconoscibile dalla richiesta originale fino al risultato e alla review. Questa procedura è condivisa da Prompt Agent, App Starter, Architect, Backend, Frontend, AI Engineer, Evaluation Agent, AgentOptimizer e Reviewer quando il lavoro ha più requisiti o passa tra ruoli.

## Quando usarla

- Usala per richieste articolate, interventi su più aree o passaggi tra agenti.
- Per una modifica isolata e semplice, applica gli stessi principi con una checklist breve; non creare documentazione sproporzionata.

## Input e prerequisiti

- Richiesta originale, `project_scope`, `authorized_sources`, vincoli e criteri già noti.
- Stato corrente del progetto e artefatti verificati, se il task riguarda una codebase.
- Il coordinatore della chat conserva il registro comune. Se il registro manca, il primo ruolo che lavora sul task lo ricostruisce dalla richiesta prima di procedere; se non può farlo senza supposizioni sostanziali, segnala il blocco.

## Procedura

1. Estrai comportamenti, vincoli, casi limite e risultati attesi. Assegna ID stabili `R1`, `R2`, … senza accorpare esigenze distinte per brevità. Associa a ogni ID un criterio di accettazione osservabile o il motivo per cui non è definibile.
2. Per ogni ID registra pacchetto, dipendenze e responsabile. Quando lavori su codice, collega aree e file verificati nel sorgente. Se un grafo o indice è utile, applica [project-graph-context](../project-graph-context/SKILL.md): collega al requisito evidenze e limiti del grafo, conservati una sola volta nel TaskState. La skill mantiene consultazioni mirate, conferma nelle fonti e fallback quando l'indice manca o è obsoleto.
3. Prima di ogni handoff, confronta gli ID con la richiesta originale. Passa solo contesto essenziale, decisioni, interfacce condivise, rischi e riferimenti necessari. Mantieni anche gli ID `BLOCKED`, `OUT_OF_SCOPE` e `NOT_APPLICABLE` con un motivo; non cancellarli dal registro.
4. Prima di modificare, il responsabile legge TaskState, istruzioni applicabili e file coinvolti. Esegue il ciclo **READ → EDIT → VALIDATE**: modifica minima, controllo del risultato e collegamento di ogni cambiamento agli ID pertinenti. Se un piano non corrisponde materialmente al progetto o cambia un contratto condiviso, chiede una nuova decisione al coordinatore o all’Architect.
5. Dopo il lavoro, associa a ogni ID un’evidenza concreta oppure `BLOCKED`, `NOT_DONE`, `OUT_OF_SCOPE` o `NOT_APPLICABLE` con motivazione. Non usare la sola esistenza di un file o una verifica generica come prova del comportamento richiesto. Riporta controlli realmente eseguiti, falliti e non eseguiti; non aggiungere o lanciare test automaticamente se l’utente non li ha richiesti.
6. Il Reviewer confronta richiesta originale, registro, modifiche ed evidenze in sola lettura. Segnala ID scomparsi o dichiarati soddisfatti senza prova e rimanda i rilievi al responsabile dell’area. È previsto al massimo un ciclo di correzione mirata e nuova review; gli elementi irrisolti restano espliciti.

## Attivazione delle skills e consegna

- Seleziona dalla mappa del ruolo le skills pertinenti al requisito e alla fase; leggi il rispettivo `SKILL.md` prima di applicarlo. Carica riferimenti e script solo per il caso concreto. Se una dipendenza manca, segnala il limite e completa le parti indipendenti senza dichiararla applicata.
- Le skills rispettano scope, ownership, autorizzazioni e vincoli del ruolo, inclusa la sola lettura. Una procedura che richiede test, scritture, tool o subagenti si applica solo con capacità e autorizzazione pertinenti; una capacità mancante non giustifica esecuzioni simulate o risultati inventati.
- Segui il contratto di chiamata e diagnosi qui sotto prima del lavoro che dipende da un agente o da una skill. La ricerca autonoma dei fatti non modifica le decisioni riservate all'utente.
- Coinvolgi soltanto i ruoli necessari al requisito: un problema locale resta all'owner; Architect interviene sui contratti trasversali, PromptAgent sulle richieste esplicite di prompt, Evaluation sulle valutazioni richieste. La disponibilità di tutti i file non impone una pipeline completa. La chat principale conserva il coordinamento e chiama i subagenti: anche le deleghe prescritte da grilling o dai riferimenti di codebase-design passano da lei, con contesto autonomo e capacità verificate.
- Riutilizza prove e decisioni correnti. Per lo stesso difetto scegli la diagnosi proporzionata: `investigate-first` per l'indagine circoscritta, `diagnosing-bugs` per il caso difficile; un approfondimento riparte dalle evidenze disponibili. Distingui correzione funzionale e refactor strutturale.
- Quando la richiesta apre decisioni determinanti, usa [grilling](../grilling/SKILL.md); recupera dai file i fatti accessibili prima delle domande. Per requisiti già definiti continua il lavoro autorizzato senza introdurre un'intervista.
- Mantieni requisiti ed evidenze nel TaskState per i passaggi ordinari. Su richiesta di consegna a una nuova sessione usa [handoff](../handoff/SKILL.md), con riferimenti agli artefatti e ID ancora aperti. Un ruolo in sola lettura prepara il testo per il responsabile autorizzato al salvataggio.
- [retro](../retro/SKILL.md) si applica quando viene richiesta una retrospettiva. Le proposte si trasferiscono all'AgentOptimizer con evidenze; un miglioramento di prestazioni richiede un confronto misurato.
- Nel checkpoint indica soltanto le skills effettivamente applicate e gli eventuali limiti che incidono sui requisiti. L'uso di una skill non prova il superamento dei criteri del task.

## Contratto di chiamata e diagnosi

1. **Distingui le operazioni.** Richiamare un subagente crea un'esecuzione del ruolo registrato tramite lo strumento effettivamente esposto dalla sessione. Invocare una skill applica una procedura secondo il meccanismo e la politica del programma. Leggere una risorsa carica un riferimento o uno script e non prova né l'invocazione della skill né la sua esecuzione. Un link Markdown non compie nessuna di queste chiamate da solo.
2. **Risolvi il nome reale.** Usa ID e percorsi del manifest di installazione, confrontati con il catalogo del programma. Gli ID del pack sono `prompt-agent`, `app-starter`, `architect`, `backend`, `frontend`, `ai-engineer`, `reviewer`, `evaluation`, `agent-optimizer`; ogni pack ne installa solo il sottoinsieme dichiarato. Per ruoli preesistenti conserva una mappatura esplicita confermata. Non dedurre il nome chiamabile dal titolo Markdown e non inventare tool, parametri o sintassi di delega.
3. **Rispetta l'attivazione.** Carica la skill necessaria e le sole risorse pertinenti tramite le capacità disponibili. `handoff` e `retro` sono su richiesta esplicita: in Claude Code e Cursor l'utente usa `/handoff` o `/retro`; in Codex usa la selezione esplicita offerta dal client (per esempio `$handoff`/`$retro` in CLI o IDE). Se il programma richiede quel comando, mostra quello pertinente e attendilo. Non precaricare queste skills e non aggirare un blocco di invocazione leggendone o riproducendone la procedura. Per le altre skills la lettura delle istruzioni è ammessa se prevista dal programma e dalle autorizzazioni; dichiarala come lettura/applicazione, senza inventare un `Skill` tool universale.
4. **Prepara una consegna autonoma.** La chat principale passa al ruolo: obiettivo, ID e criteri, file e riferimenti verificati, scope e permessi, owner, decisioni, dipendenze, skills necessarie con motivo e input, output atteso e destinatario. Non presumere che un subagente erediti il transcript. Lo specialista restituisce copertura, evidenze, blocker e prossimo destinatario; la chat principale decide il passaggio successivo. Evaluation può definire e coordinare un runner autorizzato del benchmark, ma le deleghe tra questi ruoli nella chat restano al coordinatore.
5. **Diagnostica prima di proseguire.** Se un agente o una skill necessaria non è riconosciuto, ferma soltanto il lavoro dipendente. Controlla nome, formato e frontmatter/TOML, percorso relativo al progetto aperto, dipendenze, permessi/tool esposti, versione supportata e necessità di ricaricamento. In Claude Code verifica anche l'accesso al tool `Skill` quando la configurazione limita i tool e la compatibilità di eventuali preload. Confronta le copie omonime delle skills: copie divergenti sono un conflitto da risolvere. Ripeti la chiamata solo dopo una correzione pertinente; se il problema resta, consegna errore, evidenze e azione necessaria. Continua le parti indipendenti, senza sostituire tacitamente la delega con ruoli recitati nella chat.
6. **Preserva permessi e prove.** Il modello si eredita dall'ambiente per default; eventuali preferenze specifiche non si traducono in nomi di modelli di altri provider. I ruoli di analisi/review rispettano la sola lettura anche se possiedono tool più ampi. In Codex i permessi provengono dalla sessione: `sandbox_mode` nel TOML del ruolo non garantisce una sandbox distinta. Se serve una separazione tecnica non disponibile, segnala il limite e blocca il lavoro che la richiede. Distingui sempre `FILE_GENERATI`, `CONFIGURAZIONI_CONTROLLATE`, `ELEMENTI_RICONOSCIUTI` e `CHIAMATE_VERIFICATE`, con evidenza o `NON_VERIFICATO` per ciascun ambiente.

Una prova esplorativa con ruoli applicati nella stessa chat è un'attività diversa: è ammessa solo se richiesta esplicitamente, dopo aver dichiarato l'assenza della delega reale, e non sblocca criteri che richiedono agenti indipendenti. Per dettagli aggiornati di installazione e chiamata consulta [agent-configuration](../agent-configuration/SKILL.md) e il riferimento del programma effettivo.

## Provenienza degli artefatti di dati

Quando il requisito crea o trasforma dati, conserva per ogni artefatto pertinente: riferimento e owner, schema/formato, origine e fonti autorizzate, trasformazioni o regola di generazione, versione o hash quando necessario e controlli eseguiti o mancanti. Mantieni questi metadati accanto all'artefatto o nel registro esistente; passa nel TaskState solo i riferimenti necessari.

Distingui dati reali autorizzati, dati derivati e campioni sintetici etichettati. I campioni sintetici non attestano fatti reali; separa fixture, seed, cache e dati persistenti secondo la destinazione. Collega schema, contenuto e vincoli semantici ai criteri del requisito, riutilizzando i controlli del progetto. Per confronti e valutazioni preserva casi, attese e separazione dai dati di sviluppo. La creazione di un file o di uno script non dimostra l'esecuzione della trasformazione né autorizza modifiche ai dati in uso.

## TaskState minimo

Usa solo i campi pertinenti e mantieni il contenuto compatto. Uno schema portabile è:

~~~yaml
task_id: string
project_scope: string
authorized_sources: []
objective: string
constraints: []
assumptions: []
requirement_coverage:
  - id: R1
    requirement: string
    acceptance_criteria: []
    package: P1
    owner: string
    relevant_files: []
    changes: []
    evidence: []
    status: READY | SATISFIED | FAILED | BLOCKED | NOT_DONE | NOT_MEASURED | OUT_OF_SCOPE | NOT_APPLICABLE | FINDING
    reason: string
work_packages:
  - id: P1
    requirement_ids: []
    depends_on: []
    deliverable: string
decisions: []
graph_context: [] # se utile; schema e limiti nella skill project-graph-context
validation:
  performed: []
  not_performed: []
blockers: []
status: READY | DONE | BLOCKED | REVIEW
~~~

## Output atteso

Un registro in cui ogni ID originale ha criterio, responsabile, stato ed evidenza o motivo; i passaggi tra ruoli non perdono requisiti. I singoli agenti aggiungono soltanto i campi necessari al proprio lavoro.

## Limiti e dipendenze

- Rispetta sempre `project_scope`, `authorized_sources` e permessi del ruolo. La disponibilità di uno strumento non autorizza attività esterne, deploy, invii o letture fuori scope.
- Non includere transcript integrali, dump di indice, segreti o dati personali non necessari.
- La skill descrive una procedura; non attiva agenti, esegue codice o configura un runtime.

## Agenti che possono richiamarla

Prompt Agent, App Starter, Architect, Backend, Frontend, AI Engineer, Evaluation Agent, AgentOptimizer e Reviewer, ciascuno per la propria parte del registro e dell’handoff.
