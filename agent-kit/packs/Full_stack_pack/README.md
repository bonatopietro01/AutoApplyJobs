# Full_stack_pack

Sviluppare funzionalità o correggere problemi in un progetto esistente. Include PromptAgent, Architect, Backend, Frontend, AIEngineer e Reviewer; gli specialisti lavorano solo nelle aree presenti nello stack reale.

## Installazione e uso

1. Apri il progetto di destinazione in Codex, Claude Code o Cursor e rendi accessibile l'intero pack, comprese le risorse delle skills. Se lo copi nel progetto, usa una cartella sorgente distinta dai file generati, per esempio `agent-kit/source/`. Non rinominare o perdere il descrittore del pack.
2. Incolla [PROMPTME.md](PROMPTME.md) e aggiungi la richiesta concreta. Il prompt contiene i comandi del generatore Python 3.11+ e la sequenza anteprima → applicazione → controllo statico → riconoscimento nel programma disponibile.
3. Il generatore prepara **tutti e tre gli ambienti**, non solo quello attivo: agenti in `.codex/agents/`, `.claude/agents/`, `.cursor/agents/`; tutte le 12 skills complete in `.agents/skills/` e `.claude/skills/`. Le due copie devono restare equivalenti. Crea soltanto configurazioni del progetto.
4. Il manifest `agent-kit/INSTALLATION.json` registra fonti, nomi e hash. Le sezioni delimitate di AGENTS.md e CLAUDE.md preservano il testo esistente. Aggiornamenti automatici solo per file propri invariati: personalizzazioni e conflitti si risolvono prima delle scritture.
5. Segui [runtime-check.md](Skills/agent-configuration/references/runtime-check.md). Se un ruolo o una skill non viene riconosciuto, diagnostica nome, formato, percorso, permessi, versione e ricaricamento prima del lavoro dipendente. Non è previsto un ripiego silenzioso nella chat principale.
6. Il risultato distingue file generati, configurazioni controllate, elementi riconosciuti e chiamate verificate. Gli altri programmi restano non verificati. Se Python o l'accesso ai file mancano, dichiara il prerequisito e prepara i passaggi necessari senza installare software automaticamente.

Le configurazioni riguardano l'assistente di coding, non agenti AI eseguiti dall'applicazione. Vedi [installation.md](Skills/agent-configuration/references/installation.md) per dettagli del generatore. I modelli sono ereditati dall'ambiente e le skills condizionali si caricano solo quando servono; handoff e retro conservano l'invocazione esplicita.

Questo pack non include Evaluation. Per un benchmark usa Evaluation_pack separatamente.

## PromptAgent nel workflow

Usa [PromptAgent](Agents/PromptAgent_General.md) quando vuoi trasformare un'idea o una richiesta in un prompt preciso, oppure migliorare un prompt esistente. Richiama `writing-for-agents` durante la stesura e `grilling` per decisioni sostanziali ancora aperte; ricava prima i fatti disponibili dai file.

Dopo i chiarimenti prepara il prompt con obiettivo, requisiti, vincoli, criteri di riuscita e passaggio ai ruoli pertinenti. Grilling non crea automaticamente un PRD. PromptAgent resta in sola lettura e non implementa codice né sostituisce l'analisi degli specialisti.

Se la richiesta di sviluppo è già definita, puoi coinvolgere direttamente i responsabili senza una fase obbligatoria di PromptAgent.

## Contenuto e aggiornamenti

- [PROMPTME.md](PROMPTME.md): installazione runtime e prompt operativo.
- [WORKFLOW.md](WORKFLOW.md): collaborazione e regole comuni.
- `Agents/`: specifiche esportate dei sei ruoli inclusi.
- [Skills/README.md](Skills/README.md): catalogo e dipendenze delle skills incluse. Leggi o carica solo quelle attivate dal task; non precaricarle tutte se il runtime supporta l'uso condizionale.

Le specifiche canoniche restano in `Agents/` e `Skills/` nel repository PROMPT_AGENTI. I file adattati nel progetto di destinazione sono copie derivate: non si aggiornano automaticamente. Prima di distribuirne una versione nuova confronta e conserva le personalizzazioni del progetto.
