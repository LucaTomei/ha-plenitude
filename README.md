<h1 align="center">Plenitude per Home Assistant</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/LucaTomei/ha-plenitude/releases"><img src="https://img.shields.io/github/v/release/LucaTomei/ha-plenitude" alt="Release"></a>
  <a href="https://github.com/LucaTomei/ha-plenitude/actions/workflows/validazione.yml"><img src="https://github.com/LucaTomei/ha-plenitude/actions/workflows/validazione.yml/badge.svg" alt="Validazione"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.11%2B-03A9F4" alt="Home Assistant 2025.11+">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/LucaTomei/ha-plenitude" alt="Licenza MIT"></a>
</p>

Porta in Home Assistant i dati delle bollette gas di **Eni Plenitude**: importo, scadenza,
consumi e prezzi. I prezzi del gas vengono passati all'integrazione
[MyItalgas](https://github.com/LucaTomei/ha-myitalgas) per stimare la spesa nel pannello
Energia con gli scaglioni e le quote fisse reali della bolletta.

Le bollette vengono lette dai **PDF**: li metti in una cartella, oppure li fai depositare
lì da un'automazione (ad esempio dalla posta con l'integrazione IMAP). L'accesso diretto
all'app Plenitude non è incluso (vedi [Accesso all'app](#accesso-allapp)).

---

## Indice

- [Cosa ottieni](#cosa-ottieni)
- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Mettere le bollette nella cartella](#mettere-le-bollette-nella-cartella)
- [Collegamento con MyItalgas](#collegamento-con-myitalgas)
- [Entità](#entità)
- [Accesso all'app](#accesso-allapp)
- [Privacy](#privacy)
- [Risoluzione dei problemi](#risoluzione-dei-problemi)
- [Sviluppo](#sviluppo)
- [Note legali](#note-legali)

## Cosa ottieni

- **Ultima bolletta**: importo, scadenza, periodo e consumo del periodo.
- **Consumo annuo** riportato in bolletta.
- **Prezzi del gas** ricavati dal dettaglio della bolletta (prezzo per i tre scaglioni
  annui — fino a 120, 121-480, oltre 480 Smc — e quota fissa, IVA inclusa) e passati a
  MyItalgas come periodi tariffari.
- Funziona con le bollette che hai già: lo storico dei prezzi si costruisce man mano che
  aggiungi le bollette vecchie.

## Requisiti

- **Home Assistant 2025.11 o successivo.**
- Le **bollette gas Plenitude in PDF** (dall'area clienti o dalle email).
- Facoltativo ma consigliato: l'integrazione **MyItalgas** configurata sulla stessa
  fornitura, per vedere consumi e costi nel pannello Energia.

## Installazione

### HACS (consigliata)

1. HACS → tre puntini in alto a destra → **Repository personalizzati**.
2. Repository `https://github.com/LucaTomei/ha-plenitude`, tipo **Integrazione**, **Aggiungi**.
3. Cerca **Plenitude**, aprilo, **Scarica**, poi riavvia Home Assistant.

### Manuale

Scarica l'ultima release, copia la cartella `custom_components/plenitude` in
`config/custom_components/` e riavvia Home Assistant.

## Configurazione

**Impostazioni → Dispositivi e servizi → + Aggiungi integrazione → Plenitude.**

| Campo | Descrizione |
|---|---|
| Cartella delle bollette | Cartella, sotto la configurazione di Home Assistant, dove metti i PDF. Predefinita `plenitude/bollette` (cioè `config/plenitude/bollette`). |
| Intervallo di aggiornamento | Ogni quante ore ricontrollare la cartella (1–168). |
| Fornitura gas MyItalgas | Facoltativa: la fornitura MyItalgas a cui inviare i prezzi del gas. |

Le stesse voci si cambiano in seguito da **Configura**.

## Mettere le bollette nella cartella

**A mano:** crea la cartella `config/plenitude/bollette` (con il File editor o via Samba)
e copiaci i PDF delle bollette gas scaricati dall'area clienti Plenitude. Più ne metti,
più indietro va lo storico dei prezzi. Puoi rimuoverli dopo l'importazione: i periodi
tariffari restano in MyItalgas.

**In automatico dalle email (consigliato):** se ricevi le bollette via email con il PDF
allegato, usa l'integrazione ufficiale **IMAP** di Home Assistant per salvare l'allegato
nella cartella appena arriva. In sintesi:

1. Configura l'integrazione **IMAP** sulla casella dove arrivano le mail di Plenitude.
2. Crea un'automazione che, all'arrivo di una mail da Plenitude, salva l'allegato PDF in
   `config/plenitude/bollette` (azione *Imap: scarica allegato* / `imap.download`, oppure
   uno script Python a seconda della tua configurazione).

Da quel momento le nuove bollette vengono importate da sole.

## Collegamento con MyItalgas

Se scegli una **fornitura MyItalgas** nella configurazione, a ogni lettura Plenitude crea
o aggiorna in MyItalgas i **periodi tariffari** del gas: un periodo per ogni mese presente
nelle bollette, con i tre prezzi per scaglione e la quota fissa, IVA inclusa. Così il
sensore *Costo stimato* e la statistica dei costi di MyItalgas usano i prezzi reali.

Il prezzo dello scaglione effettivamente consumato in ogni mese è preso esattamente dalla
bolletta; per gli altri scaglioni, quando la bolletta non li riporta (perché non
raggiunti), il prezzo è stimato dal valore noto più vicino.

## Entità

Un dispositivo **Plenitude** con:

| Entità | Descrizione |
|---|---|
| Importo ultima bolletta | Totale da pagare dell'ultima bolletta (€) |
| Scadenza ultima bolletta | Data di scadenza |
| Consumo ultima bolletta | Smc del periodo fatturato |
| Consumo annuo | Smc consumati nell'anno, come riportato in bolletta |
| Bollette lette | Diagnostica: quante bollette sono state lette |
| Ultimo aggiornamento | Diagnostica: ultima lettura della cartella |

## Accesso all'app

Questa integrazione **non** accede all'app o all'API di Plenitude: legge solo i PDF che
gli fornisci. Il modulo `custom_components/plenitude/api.py` lascia l'interfaccia
`SorgenteBollette` pronta: chi volesse aggiungere il download diretto dall'app può
implementare `SorgenteApp.elenco_pdf` senza toccare il resto dell'integrazione.

## Privacy

- Le bollette restano sul tuo Home Assistant; niente viene inviato a terzi.
- La **diagnostica** scaricabile oscura PDR, POD e il riferimento alla fornitura MyItalgas.

## Risoluzione dei problemi

| Problema | Soluzione |
|---|---|
| L'integrazione va in *riprova* | La cartella delle bollette non esiste ancora: creala e aggiungi almeno un PDF. |
| *Bollette lette* resta a 0 | Nella cartella non ci sono PDF di bollette **gas** Plenitude leggibili. |
| I prezzi non arrivano in MyItalgas | Verifica di aver scelto la fornitura MyItalgas nelle opzioni e che MyItalgas sia installato. |
| Un PDF non viene letto | Le bollette molto vecchie possono avere un formato diverso: apri una segnalazione allegando il log (senza dati personali). |

Per il debug: **Impostazioni → Dispositivi e servizi → Plenitude → ⋮ → Abilita il debug**.

## Sviluppo

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

I test girano su bollette di esempio anonimizzate (nessun dato personale) e su un
portale dei prezzi ricostruito dal testo delle bollette reali: nessun accesso a
Plenitude. Per pubblicare una versione: aggiungi la voce in `CHANGELOG.md` e lancia
`scripts/rilascia.sh X.Y.Z`.

## Note legali

Integrazione **non ufficiale**, non affiliata né approvata da Eni Plenitude S.p.A. I
marchi citati appartengono ai rispettivi titolari e sono usati solo per identificare il
servizio. Distribuita con licenza [MIT](LICENSE).
