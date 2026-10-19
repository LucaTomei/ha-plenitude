<p align="center">
  <img src="custom_components/plenitude/brand/logo.png" alt="Plenitude" height="64">
</p>

<h1 align="center">Plenitude per Home Assistant</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/LucaTomei/ha-plenitude/releases"><img src="https://img.shields.io/github/v/release/LucaTomei/ha-plenitude" alt="Release"></a>
  <a href="https://github.com/LucaTomei/ha-plenitude/actions/workflows/validazione.yml"><img src="https://github.com/LucaTomei/ha-plenitude/actions/workflows/validazione.yml/badge.svg" alt="Validazione"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.11%2B-03A9F4" alt="Home Assistant 2025.11+">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/LucaTomei/ha-plenitude" alt="Licenza MIT"></a>
</p>

Porta in Home Assistant i dati del tuo account **Eni Plenitude**, luce e gas: bollette
(importo, scadenza, da pagare), consumi mese per mese, lettura del contatore gas e storico
dei consumi nel pannello Energia. I prezzi del gas letti dalle bollette vengono passati
all'integrazione [MyItalgas](https://github.com/LucaTomei/ha-myitalgas) per stimare la spesa
con gli scaglioni e le quote fisse reali.

Due modi di usarla:

- **Account Plenitude** (consigliato): accedi con email e password dell'app e il codice di
  verifica che ricevi per email. Tutto viene letto e aggiornato da solo, comprese le
  bollette PDF.
- **Cartella di PDF**: metti le bollette gas in una cartella (a mano o con
  un'automazione). Nessun accesso all'account.

```
  account Plenitude ──▶ bollette, consumi, letture ──▶ sensori + pannello Energia
         │
         └──▶ PDF bollette gas ──▶ prezzi per scaglione + quota fissa ──▶ MyItalgas
```

---

## Indice

- [Cosa ottieni](#cosa-ottieni)
- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Entità](#entità)
- [Pannello Energia](#pannello-energia)
- [Collegamento con MyItalgas](#collegamento-con-myitalgas)
- [Bollette da cartella](#bollette-da-cartella)
- [Come funziona l'accesso](#come-funziona-laccesso)
- [Privacy](#privacy)
- [Risoluzione dei problemi](#risoluzione-dei-problemi)
- [Sviluppo](#sviluppo)
- [Note legali](#note-legali)

## Cosa ottieni

- **Per ogni fornitura (luce e gas)**: importo, emissione e scadenza dell'ultima bolletta,
  stato del pagamento, totale e numero di bollette **da pagare**, consumo dell'ultimo mese
  (per la luce anche per fascia F1/F2/F3) e degli ultimi 12 mesi.
- **Gas**: lettura del contatore, prossima finestra di autolettura, consumo annuo e prezzi
  ricavati dal dettaglio delle bollette (tre scaglioni annui e quota fissa, IVA inclusa).
- **Storico dei consumi** nel pannello Energia, dall'attivazione della fornitura.
- Le nuove forniture dell'account compaiono da sole.

## Requisiti

- **Home Assistant 2025.11 o successivo.**
- Un account Plenitude (lo stesso dell'app), oppure le bollette gas in PDF.
- Facoltativo: l'integrazione **MyItalgas** configurata sulla stessa fornitura gas.

## Installazione

### HACS (consigliata)

1. HACS → tre puntini in alto a destra → **Repository personalizzati**.
2. Repository `https://github.com/LucaTomei/ha-plenitude`, tipo **Integrazione**, **Aggiungi**.
3. Cerca **Plenitude**, aprilo, **Scarica**, poi riavvia Home Assistant.

### Manuale

Scarica l'ultima release, copia la cartella `custom_components/plenitude` in
`config/custom_components/` e riavvia Home Assistant.

## Configurazione

**Impostazioni → Dispositivi e servizi → + Aggiungi integrazione → Plenitude**, poi scegli:

**Account Plenitude**

1. Inserisci email e password dell'app Plenitude.
2. Plenitude ti invia per email un codice di 6 cifre: inseriscilo. Se non arriva, spunta
   *Invia un nuovo codice*.
3. Scegli l'intervallo di aggiornamento e, se vuoi, la fornitura MyItalgas da aggiornare.

Puoi aggiungere più account. La password non viene salvata.

**Cartella di PDF**: vedi [Bollette da cartella](#bollette-da-cartella).

Le opzioni si cambiano in seguito da **Configura**:

| Opzione | Descrizione |
|---|---|
| Intervallo di aggiornamento | Ogni quante ore aggiornare i dati (1–168, predefinito 12). |
| Fornitura gas MyItalgas | Facoltativa: la fornitura MyItalgas a cui inviare i prezzi del gas. |
| Conto gas collegato a MyItalgas | Solo se l'account ha più forniture gas. |
| Cartella delle bollette | Solo per le bollette da cartella. |

## Entità

Con l'account, un dispositivo **Plenitude** per l'account e uno per ogni fornitura
(**Plenitude Gas**, **Plenitude Luce**):

| Entità | Forniture | Descrizione |
|---|---|---|
| Importo ultima bolletta | luce, gas | € dell'ultima bolletta; negli attributi numero, scadenza, stato e metodo di pagamento |
| Scadenza ultima bolletta | luce, gas | Data di scadenza |
| Emissione ultima bolletta | luce, gas | Data di emissione |
| Da pagare | luce, gas | Totale ancora da pagare; negli attributi le bollette e la prossima scadenza |
| Bollette da pagare | luce, gas | Quante bollette hanno un importo da pagare |
| Consumo ultimo mese | luce, gas | kWh o Smc dell'ultimo mese completo; per la luce anche le fasce |
| Consumo ultimi 12 mesi | luce, gas | kWh o Smc degli ultimi 12 mesi completi |
| Lettura contatore | gas | Ultima lettura (m³), con la data |
| Prossima autolettura | gas | Inizio della prossima finestra per l'autolettura |
| Consumo annuo | gas | Smc dell'anno, come riportato nell'ultima bolletta |
| Bollette lette | gas | Diagnostica: bollette PDF lette e periodi tariffari ricavati |
| Ultimo aggiornamento | account | Diagnostica: ultima lettura riuscita |

Con la cartella di PDF: *Importo*, *Scadenza* e *Consumo* dell'ultima bolletta, *Consumo
annuo*, *Bollette lette* e *Ultimo aggiornamento*.

## Pannello Energia

Con l'account, lo storico dei consumi mensili di ogni fornitura viene importato come
statistica esterna:

- `plenitude:consumo_luce_<conto>` in kWh → **Consumo dalla rete**;
- `plenitude:consumo_gas_<conto>` in m³ → **Consumo di gas**.

Aggiungile da **Impostazioni → Pannelli → Energia**. I valori sono mensili (registrati
all'inizio di ogni mese, con lo storico dall'attivazione della fornitura): vanno guardati
per mese o per anno. Il mese in corso e i due precedenti vengono aggiornati a ogni
lettura, così le stime vengono sostituite dai consumi reali.

Per il gas puoi usare in alternativa il sensore *Lettura contatore* (m³, totale
crescente): scegli **uno solo** dei due, altrimenti i consumi vengono contati due volte.
Se hai MyItalgas, i consumi giornalieri del contatore sono più precisi: in quel caso usa
MyItalgas per il gas e Plenitude per i prezzi.

## Collegamento con MyItalgas

Se scegli una **fornitura MyItalgas** nelle opzioni, a ogni aggiornamento Plenitude crea
o aggiorna in MyItalgas i **periodi tariffari** del gas: un periodo per ogni mese presente
nelle bollette, con i tre prezzi per scaglione e la quota fissa, IVA inclusa. Così il
sensore *Costo stimato* e la statistica dei costi di MyItalgas usano i prezzi reali.

Il prezzo dello scaglione effettivamente consumato in ogni mese è preso esattamente dalla
bolletta; per gli altri scaglioni, quando la bolletta non li riporta (perché non
raggiunti), il prezzo è stimato dal valore noto più vicino.

Con l'account, i PDF delle ultime 24 bollette gas vengono scaricati in
`config/plenitude/archivio/<conto>/` (una volta sola) e letti da lì.

## Bollette da cartella

Senza account, le bollette gas in PDF vengono lette da una cartella (predefinita
`plenitude/bollette`, cioè `config/plenitude/bollette`, creata da sola al primo avvio).

**A mano:** copia nella cartella i PDF delle bollette gas scaricati dall'area clienti
Plenitude. Più ne metti, più indietro va lo storico dei prezzi. Puoi rimuoverli dopo
l'importazione: i periodi tariffari restano in MyItalgas.

**In automatico dalle email:** se ricevi le bollette via email con il PDF allegato, usa
l'integrazione ufficiale **IMAP** di Home Assistant per salvare l'allegato nella cartella
appena arriva (azione *Imap: scarica allegato* / `imap.download`).

## Come funziona l'accesso

L'integrazione usa gli stessi servizi dell'app Plenitude. All'accesso con email, password
e codice di verifica si ottiene una sessione che dura **un anno** ed è salvata nella
configurazione di Home Assistant: dopo un riavvio non serve rifare nulla. Se la sessione
scade o viene revocata (ad esempio cambiando password), Home Assistant mostra la richiesta
di **riautenticazione** in *Impostazioni → Dispositivi e servizi*: basta inserire di nuovo
password e codice.

## Privacy

- I dati restano sul tuo Home Assistant e vengono richiesti solo a Plenitude.
- La password non viene salvata: si conserva solo la sessione dell'account.
- I PDF scaricati restano in `config/plenitude/archivio/`; puoi cancellarli quando vuoi
  (verranno riscaricati solo quelli che servono).
- La **diagnostica** scaricabile oscura email, sessione, codici dei conti, PDR, POD,
  indirizzi e il riferimento alla fornitura MyItalgas.

## Risoluzione dei problemi

| Problema | Soluzione |
|---|---|
| *Email o password errate* | Usa le stesse credenziali dell'app Plenitude. |
| Il codice non arriva | Controlla lo spam, poi spunta *Invia un nuovo codice*. |
| *La verifica è scaduta* | Chiudi la finestra e ricomincia l'aggiunta dell'integrazione. |
| *Account bloccato temporaneamente* | Troppi tentativi: riprova dopo qualche minuto. |
| Richiesta di riautenticazione | La sessione è scaduta o revocata: inserisci di nuovo password e codice. |
| L'integrazione va in *riprova* | Plenitude non raggiungibile: riprova da sola più tardi. |
| *Bollette lette* resta a 0 | I PDF sono ancora in download (pochi minuti) o non sono bollette gas leggibili. |
| Una fornitura non c'è più | Il suo dispositivo diventa non disponibile: puoi eliminarlo dalla pagina del dispositivo. |
| I prezzi non arrivano in MyItalgas | Verifica la fornitura MyItalgas nelle opzioni e che MyItalgas sia installato. |

Per il debug: **Impostazioni → Dispositivi e servizi → Plenitude → ⋮ → Abilita il debug**.

## Sviluppo

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

I test girano su un account Plenitude simulato e su bollette di esempio anonimizzate
(nessun dato personale, nessun accesso reale a Plenitude). Per pubblicare una versione:
aggiungi la voce in `CHANGELOG.md` e lancia `scripts/rilascia.sh X.Y.Z`.

## Note legali

Integrazione **non ufficiale**, non affiliata né approvata da Eni Plenitude S.p.A. I
marchi citati appartengono ai rispettivi titolari e sono usati solo per identificare il
servizio. Distribuita con licenza [MIT](LICENSE).
