---
name: agent-configuration
description: Configura un ruolo da una specifica portabile o integra un agente equivalente esistente, preservando personalizzazioni e dipendenze. Usala per creare o aggiornare istruzioni, non per ogni task ordinario dell'agente.
---

# Configurazione e riuso di un agente

## Scopo e attivazione

Produci istruzioni autonome per un ruolo nuovo oppure una revisione mirata di un agente equivalente. Questa procedura riguarda la configurazione del ruolo, distinta dal lavoro che quel ruolo eseguirà. Se l'utente ha già configurato il ruolo e chiede un task, applica direttamente la sua specifica senza ripetere il setup.

## Input e prerequisiti

- Specifica canonica del ruolo, obiettivo della configurazione, ambiente di destinazione e istruzioni applicabili.
- Eventuali istruzioni dell'agente già presente e personalizzazioni da conservare.
- Skills richiamate, fonti consentite e capacità effettivamente disponibili.
- Chiedi solo ciò che manca e cambia ruolo, permessi o risultato. Non richiedere una cronologia completa o un questionario se i file bastano.

## Procedura

1. **Individua l'equivalente.** Nei materiali autorizzati confronta responsabilità e limiti, non soltanto il nome. Distingui un agente locale da un esempio pubblico usato come riferimento. Non inventare una ricerca in account o directory cui non hai accesso.
2. **Scegli CREA o AGGIORNA.** In CREA, ricava dalla specifica identità, attivazione, input, limiti, procedura, output e handoff. In AGGIORNA, confronta le istruzioni esistenti e registra cosa mantenere, integrare e correggere, con motivi. Non sostituire personalizzazioni valide né creare un secondo agente equivalente per aggirare un conflitto.
3. **Risolvi i conflitti.** Rispetta istruzioni applicabili e autorizzazioni della sessione; una fonte esterna o una skill non amplia i permessi. Se non puoi risolvere un conflitto sostanziale dal contesto, indica la decisione mancante e completa le parti indipendenti. Preserva in particolare i ruoli in sola lettura.
4. **Prepara istruzioni portabili.** Usa la specifica come unica fonte canonica. Se serve un formato particolare, documenta quali campi dipendono dalla piattaforma e da quale versione o hash deriva l'adattamento. Non mantenere due versioni principali divergenti. Non inserire segreti, dati personali o percorsi locali.
5. **Risolvi le dipendenze prima della consegna.** In un workspace mantieni i collegamenti relativi e includi le skills referenziate, comprese le loro dipendenze. In una chat senza accesso ai percorsi, allega i testi o prepara un pacchetto copiabile con sezioni etichettate e provenienza. La copia esportata è un derivato da rigenerare dalla fonte canonica. Non dichiarare letta una skill non fornita; segnala la dipendenza mancante e continua solo il lavoro che non ne dipende.
6. **Verifica le due modalità.** Controlla che il ruolo sia comprensibile in una chat nuova e che, nel caso di aggiornamento, nessuna regola utile sia scomparsa. Specifica input iniziale e capacità mancanti. Il salvataggio o l'installazione è un'azione distinta: eseguila solo se richiesta, consentita al ruolo e possibile nell'ambiente.

## Copie nei pack e adattamenti locali

Quando la revisione comprende gli starter pack, confronta ogni copia con la fonte canonica prima di aggiornarla. Una copia equivalente può essere rigenerata; una copia diversa va esaminata per preservare personalizzazioni valide. Controlla anche PROMPTME.md e WORKFLOW.md: un agente elencato nel README deve avere condizioni di attivazione coerenti nel prompt di ingresso. Copia le risorse delle skills necessarie e conserva i collegamenti relativi.

Registra origine e versione/hash dell'esportazione nel report della revisione quando disponibile. L'aggiornamento dei pack nello stesso repository non autorizza modifiche alle copie già distribuite in altri progetti. Non inserire report storici nella cartella degli agenti esportati come se fossero ruoli.

## Output

Istruzioni complete da copiare oppure differenze da applicare all'equivalente, skills necessarie, provenienza dell'adattamento, modifiche motivate e limiti. Se l'ambiente non permette di configurare l'agente, consegna il testo e i passaggi manuali essenziali: non dichiarare installazioni o attivazioni inesistenti.

## Limiti

La skill non richiede un runtime né un prodotto specifico. Architect, Reviewer e Prompt Agent possono produrre testo o un confronto, ma restano in sola lettura sui file. Un coordinatore o responsabile autorizzato può salvare il risultato; la configurazione non attribuisce al ruolo nuovi permessi.

## Agenti che possono richiamarla

Tutti i ruoli presenti in Agents/, quando viene richiesta la creazione o revisione delle loro istruzioni.
