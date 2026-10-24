# Installazione locale dei pack

Prerequisito: Python 3.11 o successivo, senza librerie esterne. Verifica l'interprete disponibile (`python --version` o `py -3 --version` su Windows); se manca, segnala il prerequisito. Non installarlo automaticamente. I comandi seguenti usano `python`: sostituiscilo con l'interprete verificato.

Dalla cartella del pack, con percorsi fra virgolette:

```text
python Skills/agent-configuration/scripts/install_agent_pack.py --pack "." --project "PERCORSO PROGETTO"
python Skills/agent-configuration/scripts/install_agent_pack.py --pack "." --project "PERCORSO PROGETTO" --apply
python Skills/agent-configuration/scripts/install_agent_pack.py --pack "." --project "PERCORSO PROGETTO" --check
```

Il progetto deve esistere. Sono preparati tutti e tre gli ambienti, anche se uno solo è disponibile: [.codex/agents per Codex](codex.md), [.claude/agents per Claude Code](claude-code.md), [.cursor/agents per Cursor](cursor.md). Le skills complete sono copiate in `.agents/skills` e `.claude/skills`; non servono symlink. Copiare il pack come allegato a una chat senza filesystem non esegue questi comandi.

`pack.json` fissa ID, skills e ruoli per fase. La [mappa dei ruoli](roles.json) associa specifica canonica, nome registrato, descrizione e sola lettura. Tutti i pack includono le quattordici skills; la disponibilità non significa applicarle tutte a ogni task. Script e riferimenti vengono copiati integralmente, esclusi cache Python e bytecode. Il validatore controlla frontmatter scalare `name`/`description`, riferimenti Markdown relativi a file e dipendenze incluse; non è un parser YAML generale né verifica frammenti, URL web o link costruiti dinamicamente.

Evaluation usa la fase predefinita `project`: installa soltanto `evaluation` nell'app. Per il repository canonico destinatario, usa `--phase optimizer` su tutti e tre i comandi: installa `agent-optimizer`. Non attiva automaticamente l'altro ruolo né trasferisce report fra progetti. Il laboratorio di questa raccolta è protetto dal generatore: i suoi agenti locali restano separati.

## File posseduti, aggiornamenti e conflitti

`agent-kit/INSTALLATION.json` conserva schema, versione generatore, pack/fase, mappa ruoli, percorsi, fonti relative al pack e SHA-256. Non conserva percorsi assoluti dell'autore. Le configurazioni generate funzionano senza accesso al pack originale; per confrontare una nuova versione con `--check` serve il pack passato come fonte.

I report prodotti in seguito vanno in `agent-kit/reports/` o in una cartella report concordata esterna alle directory di discovery. Il generatore non crea report o cartelle vuote anticipatamente: Evaluation/AgentOptimizer li salvano quando servono, entro i permessi ricevuti.

Il generatore aggiunge una sezione delimitata in `AGENTS.md` e `CLAUDE.md` verso `agent-kit/WORKFLOW.md`, preservando byte per byte il resto delle istruzioni. Non modifica permessi, impostazioni globali o configurazioni del modello. Non imposta il runtime AI dell'applicazione.

- Anteprima e check non scrivono. Un conflitto blocca l'intera installazione prima delle scritture.
- Un file creato dal generatore è aggiornabile soltanto se coincide con il suo hash registrato. Una personalizzazione viene preservata e segnalata.
- Un file preesistente identico può essere riutilizzato; non diventa automaticamente aggiornabile dal generatore.
- Pack diversi possono condividere ruoli e skills identici. Versioni divergenti di un file condiviso richiedono riconciliazione: niente sovrascrittura silenziosa o precedenza per ordine d'installazione.
- Un file precedentemente incluso ma ora rimosso dalla fonte richiede una migrazione esplicita. Il generatore non cancella automaticamente ruoli o risorse.
- Nomi duplicati nelle cartelle di discovery del progetto, copie divergenti, risorse mancanti e symlink/junction bloccano il piano. Configurazioni globali, plugin, cartelle antenate e impostazioni remote richiedono il controllo nel programma.
- Le scritture sono preparate in file temporanei locali, con sostituzione del manifest per ultima e ripristino su errori intercettati. Non è una transazione resistente a spegnimenti o arresti forzati: dopo un'interruzione esegui `--check` e confronta i file prima di riprovare.

Codici di uscita: `0` anteprima/applicazione riuscita o check coerente; `1` check con file mancanti/da aggiornare; `2` conflitto, fonte non valida o errore. Non cancellare il manifest o gli hash per aggirare un conflitto: confronta base, personalizzazione e nuova fonte, conserva le modifiche e prepara una migrazione consapevole.

## Dopo il controllo statico

Segui [runtime-check](runtime-check.md) nel programma di destinazione. Il generatore registra sempre `NOT_VERIFIED` per i runtime: una configurazione valida non dimostra riconoscimento, invocazione o rispetto operativo dei ruoli.
