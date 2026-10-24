# Collaborazione e regole comuni

I file in Agents/ sono specifiche di ruolo; le cartelle in Skills/ contengono procedure e risorse. Leggi le specifiche disponibili e seleziona le skills secondo le sezioni "Skills per il task". Copiare i file non attiva automaticamente un runtime.

La chat principale coordina richiesta, scope, fonti autorizzate e TaskState: requisiti R1..., criteri, owner, dipendenze, decisioni, stato ed evidenze. Mantieni anche i requisiti bloccati e non applicabili con un motivo. Ogni file condiviso ha un owner. Prima di attività parallele fissa contratti e confini.

Quando presente, AppStarter distingue NEW, EXISTING e RUNNING. Architect propone decisioni trasversali in sola lettura. Backend, Frontend e AIEngineer implementano soltanto nelle aree realmente presenti. Reviewer conclude in sola lettura; i rilievi tornano all'owner. Non serve coinvolgere tutti i ruoli per ogni richiesta.

Grilling chiarisce decisioni importanti aperte con round di domande e proposte consigliate; i fatti accessibili si ricavano dai file. I passaggi ordinari mantengono TaskState; handoff a nuova sessione e retro si usano su richiesta. Grafi e indici sono supporti da confermare nel sorgente.

Dati creati o trasformati devono mantenere schema, origine, trasformazioni, owner e distinzione reale/sintetico. Seleziona le risorse della skill necessarie al caso e segnala dipendenze indisponibili. Mantieni permessi dei ruoli e istruzioni del progetto.

Evaluation misura le pipeline con versioni, casi e condizioni fissi e salva BenchmarkReport.md nella cartella degli agenti valutati. AgentOptimizer usa quel report nel repository canonico per revisioni mirate e salva OptimizationReport.md. Non correggere i prompt mentre sono sotto confronto. Una modifica senza benchmark resta da validare; misure mancanti non diventano zero.

Non aggiungere né eseguire test senza una richiesta di testare o verificare. La richiesta di benchmark autorizza le prove pertinenti entro scope e budget stabiliti. Non introdurre implicitamente deploy, operazioni distruttive o cambiamenti all'istanza attiva. Distingui analisi statica, ruoli applicati nella chat e agenti eseguiti realmente.

Questi file sono copie esportate delle fonti canoniche del repository PROMPT_AGENTI. Le personalizzazioni appartengono al progetto di destinazione; gli aggiornamenti del repository non si propagano automaticamente.
Quando incluso, PromptAgent si attiva per richieste esplicite di creare o migliorare prompt. Le decisioni raccolte con grilling alimentano il prompt finale dopo la conferma; PRD soltanto se richiesto, salvataggio al coordinatore autorizzato. Per adattamenti a tool o delega mancanti applica requirement-traceability.

Per richieste che analizzano la collaborazione nel grafo Obsidian usa project-graph-context e il riferimento obsidian-workflow.md: segui i link dei Markdown, verifica condizioni e contratti, distingui fonti canoniche e copie. Il grafo documenta relazioni; Evaluation confronta quelle relazioni con i passaggi osservati senza riscrivere i prompt congelati.
