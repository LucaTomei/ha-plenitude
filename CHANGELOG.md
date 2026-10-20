# Novità

Tutte le modifiche rilevanti di Plenitude per Home Assistant.

## [0.5.1] - 2026-10-20

### Correzioni

- Il prezzo medio della luce non comprende più il canone RAI (era incluso nell'importo
  della bolletta): ora è il totale della sola fornitura diviso per i kWh.

## [0.5.0] - 2026-10-20

### Novità

- Le bollette della luce vengono lette come quelle del gas: consumo e periodo dell'ultima
  bolletta, consumo annuo, lettura del contatore (anche per fascia F1/F2/F3), potenza
  impegnata e numero di bollette lette.
- **Prezzo energia** della luce: €/kWh dei consumi con accise e IVA, senza quote fisse,
  quota potenza e canone.
- **Consumo ultima bolletta** anche per il gas.
- Torna il logo di Plenitude nell'elenco delle integrazioni.

## [0.4.1] - 2026-10-19

- Indicato in modo più evidente (README, HACS, configurazione) che l'integrazione è un
  progetto non ufficiale, non affiliato con Eni Plenitude.
- Il logo di Plenitude è sostituito da un'icona neutra.

## [0.4.0] - 2026-10-19

### Novità

- **Prezzo medio** per luce e gas: importo dell'ultima bolletta diviso per il consumo del
  periodo fatturato, tutto compreso. Per la luce è il prezzo da usare nel pannello Energia.
- **Prezzo gas**: prezzo per Smc nello scaglione raggiunto nell'anno, IVA inclusa, da usare
  nel pannello Energia per il gas.
- Pulsante **Scarica le bollette**: rilegge subito l'account e scarica le bollette mancanti,
  riprovando anche quelle che prima non erano disponibili.
- Vengono scaricate anche le ultime bollette della luce (per il periodo fatturato).

### Correzioni

- Storico dei consumi nel pannello Energia: con due aggiornamenti molto ravvicinati
  (ad esempio all'avvio e subito dopo il download dei PDF) la somma di un mese poteva
  restare a un valore vecchio.

## [0.3.0] - 2026-10-19

### Novità

- **Accesso all'account Plenitude** con email, password e codice di verifica via email:
  bollette, consumi e letture di luce e gas vengono letti e aggiornati in automatico.
  La sessione dura un anno e resta valida ai riavvii; se scade, Home Assistant chiede di
  autenticarsi di nuovo.
- Sensori per ogni fornitura: ultima bolletta (importo, emissione, scadenza, stato del
  pagamento), totale e numero di bollette da pagare, consumo dell'ultimo mese e degli
  ultimi 12 mesi (luce anche per fascia), lettura del contatore gas e prossima
  autolettura.
- Storico dei consumi mensili nel pannello Energia (statistiche esterne in kWh e m³).
- I PDF delle bollette gas vengono scaricati in `config/plenitude/archivio` e letti per
  inviare i prezzi a MyItalgas, come con la cartella.
- La cartella di PDF resta disponibile come alternativa all'account.

### Correzioni

- Lette anche le bollette gas nel formato usato fino all'inizio del 2025: lo storico dei
  prezzi parte dalle bollette più vecchie.
- Quando una bolletta rifattura mesi già fatturati (conguaglio con le letture reali), la
  quota fissa di quei mesi non viene più sommata due volte.
- La scadenza non viene più presa da un esempio nelle pagine informative della bolletta;
  date di emissione e consumo annuo letti anche quando nel PDF manca lo spazio.
- Un PDF in un formato non previsto viene ignorato invece di bloccare l'aggiornamento.
- I sensori degli importi indicano l'unità di misura (€).

## [0.2.0] - 2026-10-19

### Novità

- Logo e icona Plenitude nell'elenco delle integrazioni (tema chiaro e scuro).
- La cartella delle bollette viene creata da sola al primo avvio: l'integrazione si
  carica subito e attende i PDF, invece di restare in errore.

## [0.1.0] - 2026-10-19

- Prima versione. Legge le bollette gas Eni Plenitude in PDF da una cartella e ne ricava
  importo, scadenza, consumi e prezzi. Se collegata a una fornitura MyItalgas, crea lì i
  periodi tariffari del gas per la stima dei costi nel pannello Energia.
- L'accesso diretto all'app Plenitude non è incluso: il modulo `api.py` ne lascia
  l'interfaccia pronta da completare.
