---
name: folder-structure
description: Mostra l’albero attuale di una cartella, individua i percorsi di file precisi o rileggi la struttura di una cartella già esaminata quando serve orientarsi o verificare un riordino.
---

# Folder structure

Individua la radice richiesta o già stabilita nella conversazione e resta nelle aree autorizzate. Se più radici sono plausibili e produrrebbero risposte diverse, chiedi quale usare. Ispeziona il filesystem in sola lettura con gli strumenti disponibili. Segui collegamenti simbolici o junction solo se la destinazione risolta è nel perimetro autorizzato e la lettura non crea cicli. Usa percorsi relativi alla radice nell’output.

- **Albero.** Rileggi la struttura attuale e mostra un albero testuale ordinato. Scegli profondità e rami in funzione della richiesta. Per una vista compatta, raggruppa o escludi directory generate e ripetitive. Indica profondità, esclusioni e rami non accessibili quando limitano la copertura. Se viene chiesto l’albero completo, espandi i rami autorizzati e segnala ogni limite residuo.
- **Ricerca.** Cerca prima per nome, percorso o criterio richiesto, senza costruire l’albero completo. Verifica i percorsi restituiti e riporta tutte le corrispondenze nel perimetro effettivamente cercato. Se un filtro ordinario potrebbe nascondere il file richiesto, includi la relativa area quando è autorizzata. Mostra solo i rami utili per orientarsi. Se non trovi corrispondenze, indica radice, aree cercate, filtri ed eventuali aree non accessibili. Una ricerca parziale permette di dire «nessuna corrispondenza nelle aree cercate».
- **Rilettura.** Quando si torna su una cartella e la struttura serve al nuovo lavoro, rileggi i rami pertinenti. Dopo un riordino, confronta percorsi aggiunti e rimossi solo se la vista precedente ha radice e copertura comparabili. Indica uno spostamento solo con una prova della corrispondenza tra origine e destinazione. Confronta la porzione comune se è identificabile e indica i limiti. Se manca una base comparabile, mostra la vista corrente.

Consegna in chat la radice, l’albero o i percorsi richiesti e i limiti che incidono sul risultato. Usa un eventuale albero già disponibile come riferimento per il confronto. Salva una copia della struttura solo su richiesta.
