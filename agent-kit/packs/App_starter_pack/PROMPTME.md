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

Nomi registrati del pack: `prompt-agent`, `app-starter`, `architect`, `backend`, `frontend`, `ai-engineer`, `reviewer`.

## Richiesta operativa

Voglio impostare questo progetto con AppStarter. Se manca l'idea, chiedimela; se il progetto esiste, ricava prima dai file il suo scopo e la struttura. Identifica lo stato:
- NEW: progetto da zero; definisci obiettivo, utenti, funzionalità iniziali, limiti, stack motivato e struttura proporzionata.
- EXISTING: progetto già creato; mappa ingressi, moduli, dati, dipendenze e avvio prima di proporre un riordino.
- RUNNING: progetto in uso, distribuito o dallo stato sconosciuto; proteggi l'istanza attiva e lavora in una copia separata prima di spostamenti del codice.

Usa grilling per decisioni importanti ancora aperte: cerca autonomamente i fatti nei file, proponi risposte consigliate e attendi la conferma del quadro prima dell'implementazione. Non ripetere domande già risolte. Non presumere Flask, React o un altro stack. Consulta documentazione ufficiale quando servono versioni e compatibilità aggiornate; dichiara verifiche indisponibili.

Se ti chiedo di creare o migliorare il prompt della richiesta, coinvolgi PromptAgent: chiarisce i punti decisionali con grilling quando necessario e restituisce il prompt pronto. Il coordinatore conserva le decisioni nel TaskState. Se chiedo soltanto un prompt, fermati dopo la consegna; l'esecuzione successiva deve risultare dalla mia richiesta. Per sviluppo già definito passa direttamente ai ruoli pertinenti. PromptAgent resta in sola lettura e non sostituisce Architect o gli specialisti.
Il preflight di agent-configuration conserva gli agenti equivalenti e segnala i conflitti prima di installare; non creare copie concorrenti dello stesso ruolo. AppStarter imposta la base; Architect definisce contratti trasversali in sola lettura; Backend, Frontend e AIEngineer intervengono soltanto nelle aree presenti. La chat principale coordina TaskState con requisiti R1..., criteri, owner, dipendenze, stato ed evidenze.

Per NEW prepara scaffold, ingressi, configurazioni senza segreti, istruzioni di avvio, collocazione di test, cache e dati quando necessari. Non aggiungere infrastruttura speculativa. Implementa soltanto le funzionalità iniziali richieste.
Per EXISTING/RUNNING prepara mappa origine-destinazione e ripristino, conserva comportamenti e contratti, procedi in blocchi reversibili. Se mancano evidenze sufficienti per un riordino sicuro, configura le istruzioni e consegna il piano con i limiti. Non eseguire deploy, riavvii dell'istanza attiva o migrazioni reali dei dati come parte implicita del riordino.

Non creare, modificare o eliminare test senza richiesta esplicita; esegui test soltanto se richiesti. Una richiesta generica di verificare, controllare o fare review non autorizza test, typecheck, build, lint, benchmark o avvio: ciascuna attività richiede autorizzazione e scope propri. Se un controllo è necessario ma non autorizzato, dichiaralo e limita le operazioni dipendenti. Solo la chat principale avvia subagenti e Reviewer; raccoglie checkpoint dagli owner e, quando serve, avvia una review finale integrata. La copertura requisiti resta nel TaskState; il checkpoint riporta file, sintesi, controlli con comando/metodo ed esito, rischi, blocker e prossimo destinatario.
Consegna struttura, istruzioni create o integrate, file modificati, avvio, evidenze disponibili e punti aperti. Distingui scaffold preparato e app realmente eseguita. Handoff a una nuova chat e retro solo quando richiesti.

Inizia leggendo i file; chiedi soltanto l'idea o le decisioni essenziali che non puoi ricavare.
