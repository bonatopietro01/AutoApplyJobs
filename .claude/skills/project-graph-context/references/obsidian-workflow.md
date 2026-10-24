# Grafo Obsidian per la collaborazione

## Attivazione e accesso

Usa questa procedura quando la richiesta riguarda collaborazione, routing o dipendenze degli agenti in un vault Obsidian o in una raccolta equivalente di Markdown collegati. Il grafo nativo rappresenta note e link interni. Per consultare queste relazioni leggi i Markdown accessibili e i loro collegamenti: non serve controllare l'interfaccia grafica.

Non presumere un'API o un'esportazione del grafo già disponibile. `.obsidian/graph.json`, se presente, descrive filtri e impostazioni della vista; non è il dataset dei nodi e degli archi. Per un export esplicito registra formato, provenienza e revisione e confrontalo con i file attuali. Mantieni inalterate le impostazioni del vault se non richiesto.

## Analisi mirata

1. **Delimita i nodi.** Nel repository della raccolta parti da agenti e skills canonici. Escludi report storici, esempi e copie in Starter_pack dall'inventario dei ruoli; conserva i prompt di ingresso come contesto per capire le attivazioni. In un altro progetto identifica prima le convenzioni reali, senza imporre questi nomi di cartella.
2. **Segui i collegamenti.** Risolvi link Markdown relativi e wikilink rispetto al documento o al vault, distinguendo file, sezioni e risorse. Mantieni il percorso completo relativo come identità: più skills possono chiamare il proprio file SKILL.md. Un nome ambiguo o un riferimento non risolvibile resta tale; non scegliere una destinazione per supposizione.
3. **Classifica gli archi leggendo il contesto.** Distingui `usa_skill`, `dipende_da`, `consegna_a`, `concorda_con`, `review_di` e `riferimento`. Per ogni relazione pertinente registra sorgente, destinazione, condizione di attivazione e riferimento alla frase. Il tipo è verificato nel testo; un link estratto da solo non prova il significato. Il README può collegare tutti i ruoli per navigazione senza richiedere che lavorino tutti.
4. **Confronta grafo e contratto.** Cerca consegne descritte ma non collegate, destinatari non identificabili, input/output incompatibili, requisiti che non arrivano al ruolo successivo, owner concorrenti e dipendenze senza risorse. Per una skill apparentemente isolata controlla anche i richiami indiretti nelle procedure comuni. L'assenza di un link non dimostra che la collaborazione sia assente. Un ciclo di feedback con criterio di uscita, come owner → Reviewer → owner, può essere intenzionale.
5. **Produci un finding concreto.** Collega ogni problema alla catena pertinente, alla frase e al requisito. Distingui un miglioramento della navigazione da un difetto del contratto e da un errore osservato nelle tracce. Il numero di link o la centralità di un agente non misurano qualità, costo o contributo effettivo.
6. **Intervieni entro il ruolo.** AgentOptimizer in MIGLIORA può correggere link e contratti canonici preservando limiti e procedure valide; Evaluation propone correzioni e costruisce casi senza alterare i prompt sotto confronto. Architect e Reviewer restano in sola lettura. Gli altri owner modificano soltanto quanto assegnato. Aggiungi un link dove il testo descrive già una relazione, con la sua condizione; evita collegamenti indiscriminati tra tutti i ruoli.
7. **Controlla consegna e portabilità.** Dopo una revisione rileggi la catena interessata e i riferimenti. Prima di aggiornare un pack confronta le copie con i canonici e conserva personalizzazioni valide. Nella copia esportata collega solo i ruoli inclusi; se un ruolo esterno serve davvero, segnalane la dipendenza senza fingere che sia disponibile. I passaggi realmente eseguiti richiedono tracce o un benchmark: il grafo documentale resta analisi statica.

Il limite di consultazioni mirate e il formato `graph_context` restano quelli dello SKILL.md. Non caricare nel contesto dell'agente tutto il vault né creare un secondo inventario canonico. I diagrammi sono viste derivate dalle istruzioni.

## Output per AgentOptimizer ed Evaluation

Nel report esistente conserva soltanto: domanda analizzata → catena pertinente → condizione → fonte verificata → difetto o limite → modifica → caso di valutazione. Distingui archi documentati e passaggi osservati; marca come da ricontrollare le evidenze precedenti dopo una modifica dei file.

Esempio di caso da misurare: un task con Backend e Frontend deve trasferire gli stessi ID dei requisiti e un contratto API coerente fino al Reviewer. La presenza dei tre link da sola non soddisfa il caso. Per una modifica al routing confronta baseline e candidata con la procedura pipeline-benchmark, mantenendo casi e condizioni fissi.

Fonti sul comportamento di Obsidian, consultate l'11 ottobre 2026: [Graph view](https://obsidian.md/help/plugins/graph) e [Internal links](https://obsidian.md/help/links). La classificazione degli archi e i criteri di miglioramento sono procedure di questo repository, non funzioni native dichiarate di Obsidian.
