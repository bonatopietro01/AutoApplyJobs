# Skills

Raccolta unica delle **14 skills presenti in questo progetto**: quattro procedure originali, sei scaricate da Matt Pocock e quattro da Caveman. `Skills/` è la cartella canonica; la provenienza di ogni skill è indicata nel catalogo.

## Struttura comune

```text
Skills/
├── README.md
├── agent-configuration/
│   └── SKILL.md
├── requirement-traceability/
│   └── SKILL.md
├── project-graph-context/
│   └── SKILL.md
├── pipeline-benchmark/
│   └── SKILL.md
├── grilling/
├── handoff/
├── writing-for-agents/
├── codebase-design/
├── diagnosing-bugs/
├── retro/
├── investigate-first/
├── surgical-patch/
├── safe-refactor/
└── verify-and-stop/
```

Ogni cartella contiene il proprio `SKILL.md`. Il campo `name` del frontmatter coincide con il nome della cartella; `description` spiega la capacità e quando applicarla. Eventuali riferimenti, script e metadati rimangono nella cartella della skill.

## Skills del progetto

| Skill | Quando usarla | Utilizzatori |
|---|---|---|
| [agent-configuration](agent-configuration/SKILL.md) | Creare o aggiornare un ruolo conservando personalizzazioni e limiti. | Tutti i ruoli, durante la configurazione. |
| [requirement-traceability](requirement-traceability/SKILL.md) | Conservare requisiti, criteri, responsabili ed evidenze nei passaggi. | Tutti i ruoli coinvolti in task articolati. |
| [project-graph-context](project-graph-context/SKILL.md) | Consultare grafi pertinenti e verificare le relazioni nelle fonti. | I ruoli che necessitano di contesto e dipendenze. |
| [pipeline-benchmark](pipeline-benchmark/SKILL.md) | Preparare casi ripetibili, misure e confronti di pipeline. | Evaluation e AgentOptimizer. |

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
| [diagnosing-bugs](diagnosing-bugs/SKILL.md) | Riprodurre, diagnosticare e correggere bug difficili o regressioni. | Il responsabile dell'area coinvolta. | Matt Pocock |
| [retro](retro/SKILL.md) | Analizzare una sessione conclusa e proporre miglioramenti dell'ambiente. | AgentOptimizer e coordinatore, su richiesta. | Matt Pocock |
| [investigate-first](investigate-first/SKILL.md) | Raccogliere prove e individuare una causa prima delle modifiche. | Il responsabile dell'analisi o dell'area. | Caveman |
| [surgical-patch](surgical-patch/SKILL.md) | Correggere un problema nel punto responsabile senza interventi estranei. | Backend, Frontend, AIEngineer secondo ownership. | Caveman |
| [safe-refactor](safe-refactor/SKILL.md) | Riorganizzare codice preservando comportamento e compatibilità. | AppStarter e responsabili delle aree coinvolte. | Caveman |
| [verify-and-stop](verify-and-stop/SKILL.md) | Verificare criteri di accettazione e riportare esiti senza ampliare il task. | Reviewer e responsabile della verifica. | Caveman |

Fonti: [mattpocock/skills](https://github.com/mattpocock/skills) e [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman). È una selezione locale: le altre skills e gli installer descritti nei repository originali non fanno parte di questo pacchetto. I testi e i file di supporto scaricati sono stati mantenuti invariati; `surgical-patch` è stata spostata fuori da `investigate-first` perché è una skill autonoma.

## Dipendenze e risorse presenti

- **writing-for-agents:** include `SKILL-MECHANICS.md`, richiamato quando si scrive una skill.
- **codebase-design:** include `DEEPENING.md` e `DESIGN-IT-TWICE.md`, da leggere per i rispettivi casi.
- **diagnosing-bugs:** include `scripts/hitl-loop.template.sh`, un modello per una riproduzione guidata con Bash; va adattato al caso prima dell'uso.
- **retro:** richiama `writing-for-agents`; esporta entrambe.
- **requirement-traceability:** richiama `project-graph-context` quando un grafo è utile.
- **requirement-traceability:** contiene anche le regole comuni di attivazione delle skills e provenienza dei dati; richiama `grilling` per decisioni aperte, `handoff` per consegna a una nuova sessione richiesta e `retro` per una retrospettiva richiesta. Queste dipendenze si applicano nei rispettivi casi.
- **pipeline-benchmark:** richiama `requirement-traceability` e `project-graph-context`.
- **project-graph-context:** include [references/obsidian-workflow.md](project-graph-context/references/obsidian-workflow.md), da leggere per analizzare collaborazione e dipendenze degli agenti nel grafo dei Markdown. Distingue link, contratti e passaggi osservati.
- Le dieci skills scaricate includono `agents/openai.yaml`: sono metadati opzionali per l'ambiente compatibile. Le quattro originali utilizzano il solo `SKILL.md`.

`handoff` e `retro` conservano l'attivazione esplicita prevista dai file scaricati. Disponibilità di tool, subagenti, script e verifiche dipende dall'ambiente di destinazione e dalle autorizzazioni del task.

## Come scegliere e combinarle

1. Leggi nome e descrizione e scegli solo le skills pertinenti. La disponibilità nel catalogo non rende obbligatorio caricarle tutte.
2. Per domande aperte usa `grilling`. Per requisiti già definiti conserva il registro di `requirement-traceability`.
3. Per una consegna fra ruoli mantieni requisiti e stati con `requirement-traceability`; `handoff` può produrre il documento per una nuova sessione senza ricopiare gli artefatti.
4. Per diagnosi semplici usa `investigate-first`; per bug difficili valuta `diagnosing-bugs`. Scegli la procedura adatta, evitando due analisi equivalenti dello stesso problema.
5. Per una correzione usa `surgical-patch`; per un riordino usa `safe-refactor`; per una verifica richiesta usa `verify-and-stop`.
6. Per migliorare i prompt usa `writing-for-agents` e le evidenze del benchmark. `retro` raccoglie proposte dalla sessione; i benefici si verificano con `pipeline-benchmark`.

Le skills operano entro responsabilità e permessi del ruolo configurato. Architect, Reviewer e PromptAgent mantengono i loro limiti di sola lettura. Requisiti, ownership e criteri hanno una sola fonte nel TaskState; le skills aggiuntive applicano il metodo senza creare registri paralleli.

I sei agenti inclusi in questo pack hanno una sezione **Skills per il task** con i richiami da applicare durante il lavoro; il [workflow di questo pack](../WORKFLOW.md#collaborazione-e-regole-comuni) riepiloga le interconnessioni. Per creazione o trasformazione di dati usa i contratti del ruolo e la sezione **Provenienza degli artefatti di dati** di [requirement-traceability](requirement-traceability/SKILL.md): schema, fonte, trasformazione, owner, dati sintetici etichettati ed evidenze. I file scaricati mantengono le loro procedure originali; l'integrazione avviene nei ruoli e nella procedura condivisa.

## Esportare o usare in chat

- Copia **la cartella completa della skill**, incluse risorse e dipendenze pertinenti; conserva attribuzioni e condizioni di licenza della fonte.
- Per gli agenti di questo repository mantieni `Agents/` e `Skills/` affiancate oppure aggiorna i riferimenti nel pacchetto derivato.
- In una chat senza accesso ai file allega o incolla i testi necessari con il nome della skill come etichetta: diversi file chiamati `SKILL.md` devono restare riconoscibili.
- Esempio di richiesta: «Usa la skill `safe-refactor` in `Skills/safe-refactor/SKILL.md` per il riordino richiesto, mantenendo limiti e ownership degli agenti».
- Il semplice salvataggio dei file non installa skills né attiva agenti. L'eventuale adattamento al formato di una piattaforma si effettua nell'ambiente di destinazione.

Per strumenti dedicati alle skills o sottoagenti indisponibili, applica le regole di adattamento di requirement-traceability. Grilling raccoglie decisioni; PromptAgent le usa per il prompt finale, mentre un PRD viene prodotto solo su richiesta. agent-configuration descrive anche come aggiornare i pack preservando le personalizzazioni.
