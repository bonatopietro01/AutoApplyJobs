# Installazione e avvio

Leggi WORKFLOW.md, Skills/README.md e Skills/agent-configuration/SKILL.md di questo pack. Installa le configurazioni prima del lavoro dipendente. Le specifiche in Agents/ sono fonti da adattare; non dimostrano che un subagente sia già disponibile.

1. Identifica la radice del progetto di destinazione, la cartella completa del pack, le istruzioni esistenti e il programma effettivamente in uso. Conserva personalizzazioni, permessi e ruoli equivalenti. Non installare nel repository sorgente del laboratorio e non modificare configurazioni globali.
2. Verifica Python 3.11 o successivo. Se manca, indica il prerequisito e fermati sull'installazione senza scaricarlo automaticamente. Usa il generatore incluso; sostituisci i segnaposto con percorsi reali e mantieni le virgolette per percorsi con spazi:

   ```text
   python "<pack>/Skills/agent-configuration/scripts/install_agent_pack.py" --pack "<pack>" --project "<progetto>"
   python "<pack>/Skills/agent-configuration/scripts/install_agent_pack.py" --pack "<pack>" --project "<progetto>" --apply
   python "<pack>/Skills/agent-configuration/scripts/install_agent_pack.py" --pack "<pack>" --project "<progetto>" --check
   ```

   Esegui prima l'anteprima. Risolvi i conflitti segnalati prima delle scritture, preservando i file personalizzati; poi applica e controlla. Questa richiesta autorizza i controlli statici dell'installazione, non test indiscriminati dell'applicazione. Se non puoi eseguire comandi o scrivere file, consegna il limite e i passaggi necessari: non dichiarare l'installazione completata.
3. Prepara tutti e tre gli ambienti: agenti TOML in `.codex/agents/`, Markdown con frontmatter in `.claude/agents/` e `.cursor/agents/`; skills complete in `.agents/skills/` per Codex/Cursor e `.claude/skills/` per Claude Code. Il generatore include le 12 skills, metadati, dipendenze e risorse. Mantieni equivalenti le due copie: Cursor può leggerle entrambe; una divergenza dello stesso nome è un conflitto. Leggi i testi solo quando pertinenti, senza precaricare tutto.
4. Usa il manifest `agent-kit/INSTALLATION.json` per fonti, nomi registrati, percorsi e hash. Il generatore integra sezioni delimitate di coordinamento in AGENTS.md e CLAUDE.md e crea il workflow di coordinamento `agent-kit/WORKFLOW.md`, preservando il resto. Aggiorna automaticamente soltanto file propri invariati; non sovrascrivere personalizzazioni o configurazioni di altri pack.
5. Segui `Skills/agent-configuration/references/runtime-check.md` per verificare scoperta e chiamate nel programma disponibile. Usa il nome registrato e lo schema degli strumenti realmente esposti. Distinguere una delega, un'invocazione di skill e la lettura di una risorsa è obbligatorio: leggere uno SKILL.md non prova che la skill sia stata invocata dal programma. Le risorse tecniche dei tre adattamenti sono in `references/codex.md`, `references/claude-code.md` e `references/cursor.md` della stessa skill.
6. Se un agente o una skill necessari non sono riconosciuti o una chiamata fallisce, diagnostica nome, formato, percorso, permessi, versione e necessità di ricaricamento. Ripeti solo dopo una correzione pertinente. Non proseguire con il lavoro dipendente e non sostituire silenziosamente la delega con un ruolo recitato nella chat. Puoi completare attività indipendenti e documentare il blocco. Per ambienti non disponibili indica NON VERIFICATO.

La chat principale coordina. Ogni incarico include obiettivo, requisiti, file, permessi, skills necessarie, risultato atteso e destinatario; non presumere che un subagente abbia la cronologia. Conserva i limiti di sola lettura: in Codex il ruolo eredita i permessi della sessione, un campo sandbox_mode non garantisce isolamento separato. In Claude Code verifica che l'accesso a Skill sia consentito e precarica soltanto procedure sempre necessarie e attivabili. In Cursor usa solo campi documentati per quel programma. I modelli ereditano l'ambiente; la preferenza Codex di PromptAgent non diventa un modello Claude o Cursor.

`handoff` e `retro` restano su invocazione esplicita: `/handoff` e `/retro` in Claude Code/Cursor, `$handoff` e `$retro` nei client Codex che supportano tale sintassi, oppure il selettore disponibile. Se il programma richiede l'invocazione dell'utente, indica il comando e attendila; non aggirare il blocco leggendo ed eseguendo la procedura. Una consegna ordinaria usa TaskState.

Consegna quattro stati separati con evidenze: file generati, configurazioni controllate, elementi riconosciuti, chiamate effettivamente verificate. La presenza dei file e un --check riuscito non dimostrano esecuzione dei tre programmi.

Nomi registrati del pack: `prompt-agent`, `architect`, `backend`, `frontend`, `ai-engineer`, `reviewer`.

## Richiesta operativa

Voglio sviluppare o migliorare l'app presente in questo progetto. Se non ho indicato una funzionalità o un problema, chiedimi quale risultato ottenere; la sola disponibilità del pack non autorizza un riordino generale. Prima di modificare, analizza istruzioni locali, struttura, stack reale, ingressi e file pertinenti. Preserva comportamento e convenzioni valide. Non presumere linguaggi o framework.

Se ti chiedo di creare o migliorare il prompt della richiesta, coinvolgi PromptAgent: chiarisce i punti decisionali con grilling quando necessario e restituisce il prompt pronto. Il coordinatore conserva le decisioni nel TaskState. Se chiedo soltanto un prompt, fermati dopo la consegna; l'esecuzione successiva deve risultare dalla mia richiesta. Per sviluppo già definito passa direttamente ai ruoli pertinenti. PromptAgent resta in sola lettura e non sostituisce Architect o gli specialisti.
Usa i ruoli registrati dall’installazione; risolvi eventuali equivalenze e personalizzazioni tramite agent-configuration prima di delegare. Solo la chat principale avvia subagenti e Reviewer. Architect decide in sola lettura soltanto contratti e scelte trasversali. Backend possiede server, business logic, dati e integrazioni; Frontend interfacce e consumo API; AIEngineer prompt, estrazione, retrieval e orchestrazione AI quando il progetto li richiede. Non introdurre AI o aree inesistenti. Dopo i checkpoint degli owner, la chat principale avvia al massimo una review finale sul lavoro integrato; review intermedie solo per blocker o decisioni che impediscono il seguito.

La chat principale coordina requisiti R1..., criteri verificabili, owner, dipendenze, decisioni, stato ed evidenze nel TaskState; la copertura compare solo lì. Assegna un owner per file condiviso, concorda contratti prima di dividere il lavoro e conserva anche requisiti bloccati o fuori scope con motivo. Ogni owner invia un checkpoint compatto con file modificati, sintesi, controlli svolti con comando/metodo ed esito, rischi aperti, blocker e prossimo destinatario; rimanda al TaskState per la copertura. Usa grilling soltanto per decisioni sostanziali mancanti, con proposte consigliate; ricava i fatti dal repository.

Applica codebase-design quando scegli interfacce, investigate-first quando la causa è incerta, requirement-traceability per una correzione circoscritta a causa chiara e safe-refactor per riordini richiesti. Usa project-graph-context solo se utile, confermando nel sorgente le relazioni. Per dati creati o trasformati documenta schema, provenienza, trasformazioni e distinzione reale/sintetico. Non caricare tutte le skills per ogni attività.

Implementa la richiesta in blocchi completi e circoscritti. Non modificare l'istanza attiva, distribuire, inviare messaggi o migrare dati reali senza autorizzazione pertinente. Non creare, modificare o eliminare test senza richiesta esplicita; esegui test soltanto se richiesti. Una richiesta generica di verificare, controllare o fare review non autorizza test, typecheck, build, lint, benchmark o avvio: ciascuna attività richiede autorizzazione e scope propri. Registra il comando e riusa prove valide; ripeti soltanto i controlli invalidati dalle modifiche.

La chat principale passa al Reviewer il TaskState e i checkpoint per la review finale; l'owner corregge i difetti fondati e si ricontrollano solo il difetto e i file dipendenti. Per modifiche locali a un solo file senza impatto su contratti, sicurezza, dati o autenticazione basta la checklist breve. Concludi con requisiti soddisfatti/aperti dal TaskState, modifiche, controlli effettivi e limiti. Non promettere funzionamento totale da una lettura dei file. Handoff a nuova sessione e retro soltanto su richiesta.

Inizia dall'analisi e dalla richiesta di sviluppo che ti ho fornito.
