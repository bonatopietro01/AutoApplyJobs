# Skills

Raccolta di **12 skills portabili**: cinque procedure condivise del progetto, cinque scaricate da Matt Pocock e due da Caveman. Le sette skills del laboratorio locale sono separate dalla raccolta portabile e non vengono esportate nei pack.

## Struttura comune

```text
Skills/
├── README.md
├── agent-configuration/
│   ├── SKILL.md
│   ├── scripts/install_agent_pack.py
│   └── references/ # installazione, tre ambienti e diagnosi
├── requirement-traceability/
│   └── SKILL.md
├── project-graph-context/
│   └── SKILL.md
├── pipeline-benchmark/
│   └── SKILL.md
├── folder-structure/
│   └── SKILL.md
├── grilling/
├── handoff/
├── writing-for-agents/
├── codebase-design/
├── retro/
├── investigate-first/
├── safe-refactor/
```

Ogni cartella contiene il proprio `SKILL.md`. Il campo `name` del frontmatter coincide con il nome della cartella; `description` spiega la capacità e quando applicarla. Eventuali riferimenti, script e metadati rimangono nella cartella della skill.

## Skills del progetto

Le cinque skills condivise sono elencate qui; le sette procedure importate sono descritte più avanti.

| Skill | Quando usarla | Utilizzatori |
|---|---|---|
| [agent-configuration](agent-configuration/SKILL.md) | Creare o aggiornare un ruolo conservando personalizzazioni e limiti. | Tutti i ruoli, durante la configurazione. |
| [requirement-traceability](requirement-traceability/SKILL.md) | Conservare requisiti, criteri, responsabili ed evidenze nei passaggi. | Tutti i ruoli coinvolti in task articolati. |
| [project-graph-context](project-graph-context/SKILL.md) | Consultare grafi pertinenti e verificare le relazioni nelle fonti. | I ruoli che necessitano di contesto e dipendenze. |
| [pipeline-benchmark](pipeline-benchmark/SKILL.md) | Preparare casi ripetibili, misure e confronti di pipeline. | Evaluation (esecuzione e misure), AIEngineer (prepara la consegna per il confronto), AgentOptimizer (candidate e prova successiva), Reviewer (controlla gli artefatti del benchmark in sola lettura). |
| [folder-structure](folder-structure/SKILL.md) | Mostrare l’albero attuale, trovare file precisi o rileggere una struttura già esaminata. | PromptAgent, AppStarter, Architect, Evaluation e chi deve individuare percorsi. |

Mappatura dei file precedenti:

| Prima | Adesso |
|---|---|
| `Skills/ConfigurazioneAgenti.md` | `Skills/agent-configuration/SKILL.md` |
| `Skills/TracciabilitaRequisiti.md` | `Skills/requirement-traceability/SKILL.md` |
| `Skills/ContestoGrafiProgetto.md` | `Skills/project-graph-context/SKILL.md` |
| `Skills/BenchmarkPipeline.md` | `Skills/pipeline-benchmark/SKILL.md` |

Le istruzioni originali sono state conservate; i riferimenti relativi sono stati adattati alla nuova posizione. I vecchi file piatti sono stati sostituiti dalle cartelle canoniche elencate sopra.

## Skills scaricate

| Skill | Quando usarla | Ruoli cui può servire | Provenienza |
|---|---|---|---|
| [grilling](grilling/SKILL.md) | Chiarire decisioni e dipendenze con domande a tappe. | PromptAgent, AppStarter, Architect. | Matt Pocock |
| [handoff](handoff/SKILL.md) | Preparare il contesto per una nuova chat o un altro agente. | Chi consegna il lavoro. | Matt Pocock |
| [writing-for-agents](writing-for-agents/SKILL.md) | Scrivere istruzioni, skills e documentazione leggibile dagli agenti. | PromptAgent, AgentOptimizer, AIEngineer. | Matt Pocock |
| [codebase-design](codebase-design/SKILL.md) | Progettare confini dei moduli, interfacce e testabilità. | Architect, AppStarter e specialisti. | Matt Pocock |
| [retro](retro/SKILL.md) | Analizzare una sessione conclusa e proporre miglioramenti dell'ambiente. | AgentOptimizer e coordinatore, su richiesta. | Matt Pocock |
| [investigate-first](investigate-first/SKILL.md) | Raccogliere prove e individuare una causa prima delle modifiche. | Il responsabile dell'analisi o dell'area. | Caveman |
| [safe-refactor](safe-refactor/SKILL.md) | Riorganizzare codice preservando comportamento e compatibilità. | AppStarter e responsabili delle aree coinvolte. | Caveman |

Fonti: [mattpocock/skills](https://github.com/mattpocock/skills) e [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman). È una selezione locale: le altre skills e gli installer descritti nei repository originali non fanno parte di questo pacchetto. Le procedure mantenute conservano attribuzioni e scopi; i richiami specifici del runtime sono adattati per Codex, Claude Code e Cursor. `diagnosing-bugs`, `surgical-patch` e `verify-and-stop` sono state rimosse perché duplicavano diagnosi, modifica circoscritta e controlli già coperti dalle procedure comuni; i riferimenti attivi sono stati aggiornati.

## Dipendenze e risorse presenti

- **agent-configuration:** include il generatore Python 3.11+ senza dipendenze esterne e riferimenti per [installazione](agent-configuration/references/installation.md), [Codex](agent-configuration/references/codex.md), [Claude Code](agent-configuration/references/claude-code.md), [Cursor](agent-configuration/references/cursor.md) e [diagnosi delle chiamate](agent-configuration/references/runtime-check.md).
- **writing-for-agents:** include `SKILL-MECHANICS.md`, richiamato quando si scrive una skill.
- **codebase-design:** include `DEEPENING.md` e `DESIGN-IT-TWICE.md`, da leggere per i rispettivi casi.
- **retro:** richiama `writing-for-agents`; esporta entrambe.
- **requirement-traceability:** richiama `project-graph-context` quando un grafo è utile.
- **requirement-traceability:** contiene anche le regole comuni di attivazione delle skills e provenienza dei dati; richiama `grilling` per decisioni aperte, `handoff` per consegna a una nuova sessione richiesta e `retro` per una retrospettiva richiesta. Queste dipendenze si applicano nei rispettivi casi.
- **pipeline-benchmark:** richiama `requirement-traceability` e `project-graph-context`.
- **project-graph-context:** include [references/obsidian-workflow.md](project-graph-context/references/obsidian-workflow.md), da leggere per analizzare collaborazione e dipendenze degli agenti nel grafo dei Markdown. Distingue link, contratti e passaggi osservati.
- Le sette skills scaricate includono `agents/openai.yaml`: sono metadati opzionali per l'ambiente compatibile. Le cinque skills condivise non richiedono quel metadato; conservano SKILL.md e le proprie risorse.

`handoff` e `retro` conservano l'attivazione esplicita prevista dai file scaricati. Disponibilità di tool, subagenti, script e verifiche dipende dall'ambiente di destinazione e dalle autorizzazioni del task.

## Come scegliere e combinarle

1. Leggi nome e descrizione e scegli solo le skills pertinenti. La disponibilità nel catalogo non rende obbligatorio caricarle tutte.
2. Per domande aperte usa `grilling`. Per requisiti già definiti conserva il registro di `requirement-traceability`.
3. Per una consegna fra ruoli mantieni requisiti e stati con `requirement-traceability`; `handoff` può produrre il documento per una nuova sessione senza ricopiare gli artefatti.
4. Per cause incerte usa `investigate-first`; quando la causa è chiara applica direttamente la modifica minima. Non duplicare una diagnosi già valida.
5. Per un riordino strutturale usa `safe-refactor`. Per prove e limiti applica il contratto comune in `requirement-traceability`; una generica richiesta di verifica non autorizza test, build, typecheck, lint, benchmark o runtime.
6. Per migliorare i prompt usa `writing-for-agents` e le evidenze del benchmark. `retro` raccoglie proposte dalla sessione; i benefici si verificano con `pipeline-benchmark`.

Le skills operano entro responsabilità e permessi del ruolo configurato. Architect, Reviewer e PromptAgent mantengono i loro limiti di sola lettura. Requisiti, ownership e criteri hanno una sola fonte nel TaskState; le skills aggiuntive applicano il metodo senza creare registri paralleli.

I nove agenti portabili includono una sezione **Skills per il task**; la [mappa nel workflow del pack](../WORKFLOW.md#collaborazione-e-regole-comuni) riepiloga le interconnessioni. I ruoli e le skills usati per mantenere questa raccolta sono separati dai prodotti condivisibili. Per creazione o trasformazione di dati usa i contratti del ruolo e la sezione **Provenienza degli artefatti di dati** di [requirement-traceability](requirement-traceability/SKILL.md): schema, fonte, trasformazione, owner, dati sintetici etichettati ed evidenze. Le procedure importate mantengono il proprio scopo; i richiami vengono adattati al programma senza inventare tool o aggirare policy manuali.

## Esportare o usare in chat

- Copia **la cartella completa della skill**, incluse risorse e dipendenze pertinenti; conserva attribuzioni e condizioni di licenza della fonte.
- Per gli starter pack copia le skills portabili dichiarate da `pack.json`, senza file di configurazione specifici del repository sorgente.
- Per gli agenti di questo repository mantieni `Agents/` e `Skills/` affiancate oppure aggiorna i riferimenti nel pacchetto derivato.
- In una chat senza accesso ai file allega o incolla i testi necessari con il nome della skill come etichetta: diversi file chiamati `SKILL.md` devono restare riconoscibili.
- Esempio di richiesta: «Usa la skill `safe-refactor` in `Skills/safe-refactor/SKILL.md` per il riordino richiesto, mantenendo limiti e ownership degli agenti».
- Il semplice salvataggio dei file non installa skills né attiva agenti. L'eventuale adattamento al formato di una piattaforma si effettua nell'ambiente di destinazione.

Se una skill o un subagente necessari non sono disponibili, applica la diagnosi di requirement-traceability prima del lavoro dipendente; non sostituire silenziosamente una chiamata con una lettura o un ruolo nella chat. Grilling raccoglie decisioni; PromptAgent le usa per il prompt finale, mentre un PRD viene prodotto solo su richiesta. agent-configuration descrive anche come aggiornare i pack preservando le personalizzazioni.

## Installazione e attivazione nei tre programmi

Nei pack software il generatore di agent-configuration prepara le skills dichiarate da `pack.json`, con risorse, dipendenze e metadati, in `.agents/skills/` (Codex e Cursor) e `.claude/skills/` (Claude Code). Mantieni equivalenti le copie dello stesso nome; il manifest registra i file inclusi. Prima di aggiornare confronta le copie e risolvi le divergenze preservando le personalizzazioni.

Una delega usa il nome registrato dell'agente; una skill usa l'invocazione supportata dalla sessione; una risorsa è un file da leggere quando la procedura lo richiede. Non inventare un generico Skill tool su ogni programma. In Claude Code controlla l'accesso a Skill e precarica solo procedure sempre necessarie, mai quelle manuali. `handoff` e `retro` richiedono `/handoff` o `/retro` in Claude Code/Cursor, oppure `$handoff` e `$retro` nei client Codex che supportano questa sintassi; usa il selettore effettivo quando diverso. Se è necessaria un'invocazione dell'utente, attendila senza eludere il blocco leggendo ed eseguendo il file.

Per l'installazione usa [installation.md](agent-configuration/references/installation.md); per scoperta e chiamate usa [runtime-check.md](agent-configuration/references/runtime-check.md). Configurazione generata, controllo statico, riconoscimento e chiamata riuscita sono prove distinte.
