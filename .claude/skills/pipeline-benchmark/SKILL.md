---
name: pipeline-benchmark
description: Costruisci casi ripetibili, misura una pipeline di agenti e trasferisci risultati verificabili a chi ne migliora i prompt. Usala per benchmark, confronti tra versioni e diagnosi dei passaggi di consegne.
---

# Benchmark di pipeline e trasferimento dei risultati

## Scopo e input

Misura il risultato finale e il contributo dei singoli passaggi. Questa è la procedura comune di Evaluation e AgentOptimizer: criteri, metriche e formato del report hanno una sola fonte canonica.

Ricevi obiettivo, scope, fonti autorizzate, agenti effettivamente presenti, requisiti `R1…`, eventuali esecuzioni precedenti, baseline e candidata se disponibile, ambiente e budget. Ricava i dati dai file prima di chiedere informazioni. Usa [requirement-traceability](../requirement-traceability/SKILL.md) per requisiti e handoff; consulta [project-graph-context](../project-graph-context/SKILL.md) quando un grafo aiuta a ricostruire il flusso.

## Tipo di evidenza

Assegna al lavoro una modalità esplicita:

- `PIPELINE_REALE`: esecuzioni osservate tramite gli agenti e gli strumenti realmente configurati. Può produrre un benchmark della pipeline di destinazione.
- `RUOLI_NELLA_CHAT`: lo stesso assistente applica in sequenza le istruzioni dei ruoli. È una prova esplorativa; non dimostra le prestazioni di agenti indipendenti né rende disponibili consumi separati per ruolo.
- `ANALISI_STATICA`: lettura di specifiche, codice, log o artefatti. Produce rilievi documentati e un piano di benchmark; non dimostra il successo di esecuzioni nuove.

Se importi tracce reali, indica origine, versione e completezza: puoi analizzarle senza averle eseguite tu. Valutare di nuovo vecchi output non equivale a eseguire una nuova candidata. L'assenza di telemetria resta `NON MISURATO`; un valore ignoto non vale zero. Le modalità possono coesistere nel report, con evidenze distinte.

## Preparazione e condizioni del confronto

1. Inventaria ruoli, attivazione, input/output, limiti, skills e passaggi. Escludi report, dataset, log, archivi ed esempi dall'elenco degli agenti. Registra nome locale, responsabilità e percorso relativo; non imporre i nomi di questo repository.
2. Congela baseline e candidata con revisione o hash dei prompt, skills e configurazione del flusso. Registra anche revisione del progetto, modelli e impostazioni note, tool, contesto, cache, dataset e rubrica. Distingui la sequenza documentata da quella osservata nelle tracce.
3. Fissa prima dei risultati errori critici, soglie, obiettivo di miglioramento e compromessi ammessi. Se mancano soglie, usa come criterio iniziale: nessun nuovo errore critico, nessuna perdita di requisiti essenziali, qualità e affidabilità almeno pari alla baseline, un miglioramento osservato nella dimensione scelta e nessun peggioramento materiale nelle altre dimensioni misurate. Specifica tolleranze e incertezza; senza confronto sufficiente usa `DATI INSUFFICIENTI`.
4. Usa ambienti isolati, dati di prova e lo stesso stato iniziale per ogni caso e versione. Per task che cambiano codice, riparti dalla stessa copia: una variante non deve ereditare i fix dell'altra. Mantieni equivalenti contesto, disponibilità dei tool e condizioni della cache; separa cache fredda e calda se pertinenti. Annotazioni divergenti o fonti esterne mutate limitano il confronto.
5. Rispetta budget e permessi già concessi. La richiesta di benchmark comprende verifiche pertinenti e raccolta delle misure nell'ambiente autorizzato; non richiedere conferme per ogni passaggio già autorizzato. Per chiamate a pagamento senza budget o effetti esterni non autorizzati, chiedi soltanto il vincolo mancante e continua il lavoro indipendente.

## Dataset e rubriche

Punta a **20–50 casi rappresentativi**, adattando il numero al dominio e al budget. Riutilizza casi reali autorizzati e fallimenti noti. Puoi aggiungere casi sintetici esplicitamente etichettati, ricavati da comportamenti e rischi reali; non presentarli come uso osservato. Con pochi casi consegna un pilot con copertura e limiti dichiarati.

Per ogni caso conserva `case_id`, origine, categoria, input, contesto consentito, stato iniziale, requisiti, output atteso o rubrica, controlli e gravità degli errori. Per contenuti generativi usa criteri verificabili; la somiglianza parola per parola raramente basta. Non copiare dati riservati nel report condivisibile.

Copri task comuni, difficili e incompleti; routing corretto; attivazioni omesse o superflue; requisiti persi nei passaggi; tool non disponibili; contesto contraddittorio; retry e failure pertinenti. Quando applicabili, includi grafi assenti/obsoleti, cache hit/miss e contratti condivisi. Per codice valuta anche comportamento e compatibilità con controlli adatti al progetto: un file presente o un JSON valido non dimostra che il task funzioni.

Congela dataset e rubrica per il confronto. Dopo l'ottimizzazione controlla anche casi riservati alla verifica o casi nuovi rappresentativi: aggiungili in una versione successiva, senza cambiare silenziosamente il benchmark. Non modificare attese o soglie per far passare la candidata.

## Controlli e tracing

Usa prima controlli deterministici pertinenti: campi/sezioni, lunghezza, schema, placeholder, duplicati, numero di fonti, URL e regole di contratto. Distingui formato valido di un URL da disponibilità verificata online. Controlla il contenuto e il comportamento oltre all'esistenza degli artefatti. Preferisci verifiche esistenti; aggiungi solo quelle necessarie alla domanda del benchmark.

Per qualità semantica, aderenza al ruolo e groundedness, usa una rubrica esplicita e, se disponibile e autorizzato, un modello giudice. Calibralo su un campione valutato da una persona e conserva i disaccordi. Se nessuna persona ha controllato il campione, dichiaralo. Nei confronti oscura la versione e alterna l'ordine quando possibile; evita di premiare la sola verbosità. Il giudice non sostituisce i controlli eseguibili con codice.

Assegna `run_id` a ciascuna esecuzione di un caso e `step_id` univoco a ogni chiamata. Registra agente/componente, versione, modello, inizio/fine e durata, token input/output, cache hit/miss/unknown, tool, retry, errori ed esito. Registra anche passaggi di consegne con mittente, destinatario, ID dei requisiti ed evidenze ricevute. Usa i dati resi disponibili dagli strumenti: non richiedere né registrare ragionamenti interni privati, segreti o contenuti personali superflui.

Tieni separati consumi della pipeline, coordinamento e valutazione, mostrando anche il totale del benchmark. Includi chiamate, token e costo del modello giudice. Non stimare i token contando parole senza dichiarare metodo e incertezza; se mancano strumenti affidabili, lascia il campo non misurato.

## Metriche

| Metrica | Calcolo e significato |
|---|---|
| Task success | Casi riusciti / casi eseguiti secondo tutti i criteri essenziali. Mostra numeratore e denominatore. |
| Groundedness | Affermazioni verificabili supportate / affermazioni verificabili valutate, con riferimenti e gravità di quelle non supportate. `NON APPLICABILE` se il task non le contiene. |
| Calls/job | Chiamate osservate per esecuzione, indicando modello, tool, coordinamento e retry; separa quelle dei valutatori. |
| Tokens/job | Token input/output osservati per modello ed esecuzione; segnala eventuali contatori parziali. |
| Costo/job | Costo osservato, oppure stima etichettata con tariffe, data, valuta e assunzioni. Le chiamate sono un proxy, non una misura monetaria. |
| Latency p50/p95 | Tempo completo del job e durata per passaggio, con unità, metodo dei quantili e numerosità. Nei flussi paralleli individua il percorso critico: non sommare durate sovrapposte come latenza totale. Il p95 di pochi casi è instabile. |
| Affidabilità | Errori, timeout, output invalidi e retry / esecuzioni; distingui guasti della pipeline, del tool e della fonte. |
| Human edits | Output modificati da una persona / output realmente controllati, con tipo di correzione e tempo se raccolto. L'assenza di feedback non prova zero edit. |
| Collaborazione | Requisiti omessi, campi/evidenze persi, routing errato, ownership in conflitto, riletture o chiamate duplicate osservate. Un handoff conta come completo solo se preserva criteri e limiti necessari al destinatario. |

Riporta metriche per pipeline, agente e passaggio quando osservabili, aggregati e casi peggiorati. Nei test di codice collega gli esiti ai controlli funzionali del caso. Non nascondere queste dimensioni in un punteggio unico; un eventuale voto di rubrica deve avere scala e criteri espliciti, distinti dalle misure di prestazione.

## Confronto e contributo dei passaggi

Esegui baseline e candidata sugli stessi casi. Prima fai un pilot economico di 3–5 casi per verificare esecuzione e raccolta; il pilot non sostituisce il confronto rappresentativo. Ripeti i casi instabili quando il budget lo consente, registrando numero di ripetizioni e variabilità. Per run successivi ricrea lo stato iniziale; non contare tentativi dello stesso caso come nuovi casi indipendenti.

Per capire quale passaggio serve, prova **una variazione alla volta**: eliminare un'analisi ripetuta, ridurre contesto duplicato, cambiare ordine, rendere condizionale un passaggio o sostituire un modello. Rimuovere, fondere o introdurre ruoli resta una candidata, finché non è confrontata. Conserva i controlli e i confini obbligatori delle specifiche; non saltare il Reviewer richiesto per migliorare la latenza. Evita una ricerca combinatoria: in assenza di altri vincoli proponi al massimo due candidate prioritarie e registra un limite di retry compatibile con quello dei ruoli.

Attribuisci un beneficio a una modifica soltanto con dati comparabili. Una differenza osservata in un solo progetto non prova che il prompt canonico migliori in tutti gli ambienti. Con dati parziali indica l'ipotesi, il caso che la sostiene e la prova successiva necessaria.

Raccomanda `PROMUOVI` se supera i criteri fissati, migliora il profilo concordato e non introduce regressioni critiche; `NON PROMUOVERE` se il confronto è sufficiente e fallisce un gate o manca il miglioramento; `DATI INSUFFICIENTI` se il confronto non consente di decidere. La raccomandazione non effettua deploy né applica la versione al progetto.

## File e contratto di trasferimento

Salva il report leggibile **`BenchmarkReport.md`** nella cartella generica delle specifiche degli agenti valutati. Se gli agenti sono registrati in directory di discovery come `.codex/agents/`, `.claude/agents/` o `.cursor/agents/`, salva gli artefatti nella cartella report del progetto già designata fuori dalla discovery; in assenza di una convenzione usa `agent-kit/reports/`, relativo alla radice del progetto. Questa separazione vale anche per `OptimizationReport.md`, dataset e tracce: le directory di discovery contengono configurazioni degli agenti e non report che il programma potrebbe tentare di caricare come ruoli. Verifica che la destinazione rispetti scope e permessi e riporta il percorso realmente usato.

Il report contiene risultati e dati essenziali per AgentOptimizer. Prima di aggiornarlo preserva risultati precedenti tramite il meccanismo di versionamento esistente o sezioni identificabili; non accumulare copie per ogni micro-passaggio.

Riutilizza dataset e tracce già presenti nel progetto fuori dalle directory di discovery. Quando crei un benchmark eseguibile e manca una collocazione convenzionale, usa accanto al report soltanto `BenchmarkCases.jsonl` e `BenchmarkRuns.jsonl` per casi e record necessari alla ripetizione. Sono artefatti del benchmark, non nuove specifiche. Un'analisi statica può documentare i casi proposti nel report. Evita nuove directory oltre alla collocazione sicura degli artefatti, dashboard, servizi o formati di piattaforma se non servono alla misura.

Il report deve includere un blocco strutturato con questo contratto minimo. Espandi le liste con risultati reali; usa `null` e una ragione per valori ignoti. Lo schema seguente è un modello, non un risultato già ottenuto:

~~~yaml
schema_version: agent-benchmark/v1
report_id: identificatore
project_id: identificatore condivisibile
evidence_mode: PIPELINE_REALE | RUOLI_NELLA_CHAT | ANALISI_STATICA
execution_status: EXECUTED | PARTIAL | PLAN_ONLY | BLOCKED
versions:
  project_revision: revisione_o_unknown
  baseline: riferimento_o_unknown
  candidate: riferimento_o_null
  dataset: versione_o_unknown
  rubric: versione_o_unknown
agent_map: [] # nome locale, ruolo, percorso relativo, hash/versione, origine canonica se nota
workflow: [] # ordine/dipendenze, handoff dichiarati e osservati
conditions: [] # modelli, tool, contesto, stato iniziale, cache, ripetizioni, budget
predefined_gates: []
cases: [] # case_id, origine, categoria, requisiti, rubrica, artifact_ref condivisibile
runs: [] # run_id, case_id, variante, step_ids, esito, evidence_refs
metrics: [] # nome, variante/agente, valore, unità, stato, n, metodo, evidence_refs, limite
findings: [] # finding_id F1…, case/run/step, agente, difetto, gravità, evidenze, ipotesi di modifica
worsened_case_ids: []
human_validation: [] # campione controllato, disaccordi e verifiche mancanti
recommendation: PROMUOVI | NON PROMUOVERE | DATI INSUFFICIENTI
next_experiments: []
limits: []
~~~

Accompagna il blocco con: domanda, scope, versione del flusso, tabella metriche baseline/candidata/differenza, errori critici, valutazione dei passaggi, casi peggiorati e interventi prioritari. Ogni intervento cita `F`, caso/esecuzione, agente, causa osservata o ipotizzata, modifica precisa e test necessario. Non trasferire numeri privi di provenienza.

Prepara riferimenti relativi e dati minimizzati: chi riceve solo il report deve capire versioni, risultati, limiti e modifiche proposte. Se servono file accessibili solo nel progetto, indica esattamente quali estratti anonimizzati allegare. Senza possibilità di scrittura consegna il testo pronto come file virtuale e dichiara che non è stato salvato.

## Riferimenti metodologici

Consultati il 9 ottobre 2026: [Evaluate agent workflows](https://developers.openai.com/api/docs/guides/agent-evals) per tracce e confronti ripetibili; [Testing Agent Skills Systematically with Evals](https://developers.openai.com/blog/eval-skills) per criteri di successo e controlli del comportamento; [Build an Agent Improvement Loop](https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop) per trasformare evidenze in modifiche verificabili. Sono riferimenti di metodo: questa skill non richiede quei prodotti o dipendenze.
