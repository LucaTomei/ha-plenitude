<h1 align="center">Plenitude per Home Assistant</h1>

<p align="center">
  <b>Integrazione non ufficiale</b> che porta in Home Assistant bollette, consumi e letture<br>
  di luce e gas del <b>tuo</b> account Eni Plenitude, anche nel pannello Energia.
</p>

<p align="center">
  <a href="https://github.com/LucaTomei/ha-plenitude/releases"><img src="https://img.shields.io/github/v/release/LucaTomei/ha-plenitude?style=flat-square&color=00A651" alt="Release"></a>
  <a href="https://github.com/LucaTomei/ha-plenitude/actions/workflows/validazione.yml"><img src="https://img.shields.io/github/actions/workflow/status/LucaTomei/ha-plenitude/validazione.yml?branch=main&style=flat-square&label=validazione" alt="Validazione"></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square" alt="HACS Custom"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.11%2B-03A9F4?style=flat-square&logo=homeassistant&logoColor=white" alt="Home Assistant 2025.11+">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/LucaTomei/ha-plenitude?style=flat-square" alt="Licenza MIT"></a>
</p>

> [!CAUTION]
> **Progetto personale e non ufficiale.** Non è affiliato, collegato, sponsorizzato,
> approvato né supportato da Eni Plenitude S.p.A., da Eni S.p.A. o da altre società del
> gruppo. Per qualsiasi questione sulla tua fornitura rivolgiti a Plenitude; per problemi
> di questa integrazione apri una issue qui. Vedi le [note legali](#%EF%B8%8F-note-legali).

## 🚀 Installazione in 3 click

| | Click | Cosa succede |
|:---:|:---:|---|
| **1** | [![Apri in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=LucaTomei&repository=ha-plenitude&category=integration) | Si apre HACS nel tuo Home Assistant: conferma l'aggiunta del repository, poi **Scarica**. |
| **2** | [![Riavvia Home Assistant](https://my.home-assistant.io/badges/server_controls.svg)](https://my.home-assistant.io/redirect/server_controls/) | **Riavvia** Home Assistant, così carica l'integrazione appena scaricata. |
| **3** | [![Aggiungi l'integrazione](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=plenitude) | Si apre la configurazione di **Plenitude**: scegli *Account Plenitude* e accedi. |

> [!WARNING]
> Il pulsante **3** funziona solo dopo i passi 1 e 2. Se Home Assistant risponde
> *"Questa integrazione non supporta la configurazione tramite l'interfaccia utente"*,
> l'integrazione non è ancora scaricata o Home Assistant non è stato riavviato.

<sub>I pulsanti usano <a href="https://my.home-assistant.io">My Home Assistant</a>: la prima volta ti chiede l'indirizzo del tuo Home Assistant.</sub>

---

## ✨ In breve

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>🧾 Bollette</h3>
      Importo, emissione e scadenza dell'ultima bolletta, stato del pagamento, totale e
      numero di bollette ancora <b>da pagare</b>, per luce e gas.
    </td>
    <td width="50%" valign="top">
      <h3>📈 Consumi</h3>
      Consumo dell'ultimo mese e degli ultimi 12 mesi, in kWh per la luce (anche per fascia
      <b>F1/F2/F3</b>) e in Smc per il gas.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>⚡ Pannello Energia</h3>
      Storico dei consumi mensili <b>dall'attivazione della fornitura</b>, pronto da
      aggiungere come consumo dalla rete (luce) e consumo di gas.
    </td>
    <td width="50%" valign="top">
      <h3>🔥 Gas e MyItalgas</h3>
      Lettura del contatore, prossima autolettura e <b>prezzi reali</b> per scaglione e
      quota fissa, letti dalle bollette e passati a
      <a href="https://github.com/LucaTomei/ha-myitalgas">MyItalgas</a>.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>🔐 Accesso sicuro</h3>
      Email, password e codice di verifica via email, come nell'app. La password
      <b>non viene salvata</b>; la sessione dura un anno e sopravvive ai riavvii.
    </td>
    <td width="50%" valign="top">
      <h3>🔄 Tutto automatico</h3>
      Nuove bollette, nuove forniture e PDF scaricati da soli. Se la sessione scade,
      Home Assistant ti chiede di <b>riautenticarti</b>.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>☀️ Adotta un pannello</h3>
      Produzione del pannello virtuale, <b>autoconsumo virtuale</b> quarto d'ora per quarto
      d'ora e <b>risparmio</b> in bolletta, dall'attivazione del pannello.
    </td>
    <td width="50%" valign="top">
      <h3>🔆 Pannello Energia orario</h3>
      Con il pannello: produzione, prelievo dalla rete ed energia non autoconsumata
      <b>ora per ora</b>, come un impianto fotovoltaico vero.
    </td>
  </tr>
</table>

```mermaid
flowchart LR
    A["👤 Account Plenitude"] --> B["🧾 Bollette<br/>📈 Consumi<br/>🔢 Letture"]
    B --> C["📊 Sensori"]
    B --> D["⚡ Pannello Energia"]
    A --> E["📄 PDF bollette gas"]
    F["📁 Cartella di PDF"] -.-> E
    E --> G["💶 Prezzi per scaglione<br/>+ quota fissa"]
    G --> H["MyItalgas"]
```

> [!TIP]
> Non vuoi collegare l'account? Puoi anche solo mettere le bollette gas in PDF in una
> cartella: vedi [Bollette da cartella](#-bollette-da-cartella).

## 📚 Indice

- [Installazione in 3 click](#-installazione-in-3-click)
- [Requisiti](#-requisiti)
- [Installazione](#-installazione)
- [Configurazione](#%EF%B8%8F-configurazione)
- [Entità](#-entità)
- [Pannello Energia](#-pannello-energia)
- [Adotta un pannello](#%EF%B8%8F-adotta-un-pannello)
- [Collegamento con MyItalgas](#-collegamento-con-myitalgas)
- [Bollette da cartella](#-bollette-da-cartella)
- [Come funziona l'accesso](#-come-funziona-laccesso)
- [Privacy](#%EF%B8%8F-privacy)
- [Risoluzione dei problemi](#-risoluzione-dei-problemi)
- [Sviluppo](#%EF%B8%8F-sviluppo)
- [Note legali](#%EF%B8%8F-note-legali)

## 📋 Requisiti

- **Home Assistant 2025.11** o successivo.
- Un account **Plenitude** (lo stesso dell'app), oppure le bollette gas in PDF.
- Facoltativo: l'integrazione **MyItalgas** configurata sulla stessa fornitura gas.

## 📦 Installazione

### HACS (consigliata)

Il modo più rapido è l'[installazione in 3 click](#-installazione-in-3-click). Senza i
pulsanti:

1. HACS → tre puntini in alto a destra → **Repository personalizzati**.
2. Repository `https://github.com/LucaTomei/ha-plenitude`, tipo **Integrazione**, **Aggiungi**.
3. Cerca **Plenitude**, aprilo, **Scarica**, poi riavvia Home Assistant.

### Manuale

Scarica `plenitude.zip` dall'[ultima release](https://github.com/LucaTomei/ha-plenitude/releases/latest),
estrailo in `config/custom_components/plenitude` e riavvia Home Assistant.

## ⚙️ Configurazione

Dopo installazione e riavvio:
[![Aggiungi l'integrazione](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=plenitude)

Oppure **Impostazioni → Dispositivi e servizi → + Aggiungi integrazione → Plenitude**, poi
scegli **Account Plenitude**:

| Passo | Cosa fare |
|:---:|---|
| 1️⃣ | Inserisci **email e password** dell'app Plenitude. |
| 2️⃣ | Inserisci il **codice di 6 cifre** che Plenitude ti invia per email. Non arriva? Spunta *Invia un nuovo codice*. |
| 3️⃣ | Scegli **ogni quante ore** aggiornare e, se vuoi, la **fornitura MyItalgas** da aggiornare con i prezzi. |

Fatto: dopo pochi secondi compaiono i dispositivi **Plenitude Luce** e **Plenitude Gas**;
i PDF delle bollette di luce e gas si scaricano in background nei minuti successivi.

> [!NOTE]
> Puoi aggiungere più account Plenitude. La password serve solo per l'accesso e non viene
> salvata.

Le opzioni si cambiano in seguito da **Configura**:

| Opzione | Descrizione |
|---|---|
| Intervallo di aggiornamento | Ogni quante ore aggiornare i dati (1–168, predefinito 12). |
| Fornitura gas MyItalgas | Facoltativa: la fornitura MyItalgas a cui inviare i prezzi del gas. |
| Conto gas collegato a MyItalgas | Compare solo se l'account ha più forniture gas. |
| Corrispettivo Luce per il pannello virtuale | Facoltativo, solo con *Adotta un pannello*: vedi [Valore dell'energia autoconsumata](#-valore-dellenergia-autoconsumata). |
| Cartella delle bollette | Solo per le bollette da cartella. |

## 📊 Entità

Con l'account trovi un dispositivo **Plenitude** per l'account e uno per ogni fornitura
(**Plenitude Luce**, **Plenitude Gas**).

| Entità | 💡 Luce | 🔥 Gas | Descrizione |
|---|:---:|:---:|---|
| Importo ultima bolletta | ✅ | ✅ | € dell'ultima bolletta; negli attributi numero, periodo, consumo, scadenza, stato e metodo di pagamento |
| Scadenza ultima bolletta | ✅ | ✅ | Data di scadenza |
| Emissione ultima bolletta | ✅ | ✅ | Data di emissione |
| Da pagare | ✅ | ✅ | Totale ancora da pagare; negli attributi le bollette e la prossima scadenza |
| Bollette da pagare | ✅ | ✅ | Quante bollette hanno un importo da pagare |
| Spesa ultimo mese | ✅ | ✅ | € dell'ultimo mese fatturato; negli attributi la spesa di ogni mese degli ultimi 12 |
| Consumo ultimo mese | ✅ | ✅ | kWh o Smc dell'ultimo mese completo; negli attributi lo stesso mese dell'anno prima e la differenza in %, per la luce anche le fasce |
| Consumo ultimi 12 mesi | ✅ | ✅ | kWh o Smc degli ultimi 12 mesi completi |
| Prezzo medio | ✅ | ✅ | €/kWh o €/m³ effettivi dell'ultima bolletta: importo totale ÷ consumo del periodo (luce: senza canone RAI) |
| Prezzo energia | ✅ | | €/kWh dei consumi con accise e IVA, senza quota fissa, quota potenza e canone |
| PUN | ✅ | | PUN applicato in bolletta nell'ultimo mese fatturato (€/kWh), con lo storico dei mesi, lo spread e il dispacciamento |
| Indice gas (PSV) | | ✅ | Indice del gas applicato in bolletta nell'ultimo mese fatturato (€/Smc), con lo storico dei mesi e lo spread |
| Offerta | ✅ | ✅ | Nome dell'offerta; negli attributi tipo, mercato e validità delle condizioni economiche (anche se scadute) |
| Prezzo gas | | ✅ | €/m³ del gas nello scaglione raggiunto quest'anno, IVA inclusa, senza quote fisse |
| Consumo ultima bolletta | ✅ | ✅ | kWh o Smc fatturati nell'ultima bolletta |
| Consumo annuo | ✅ | ✅ | kWh o Smc dell'ultimo anno, come riportato nell'ultima bolletta |
| Lettura contatore | ✅ | ✅ | Ultima lettura (kWh, anche per fascia F1/F2/F3, oppure m³), con la data |
| Potenza impegnata | ✅ | | kW del contratto; negli attributi la potenza disponibile e la quota potenza |
| Prossima autolettura | | ✅ | Inizio della prossima finestra per l'autolettura |
| Bollette lette | ✅ | ✅ | Diagnostica: bollette PDF lette (per il gas anche i periodi tariffari ricavati) |
| Spesa totale ultimo mese | | | Sul dispositivo dell'account: luce + gas dell'ultimo mese fatturato per entrambi, con i 12 mesi precedenti |
| Comunicazioni non lette | | | Sul dispositivo dell'account: comunicazioni dell'area clienti non lette (es. proposte di rinnovo delle condizioni), con l'elenco |
| Scadenze (calendario) | | | Sul dispositivo dell'account: scadenze delle bollette con l'importo, finestra dell'autolettura del gas e date delle comunicazioni |
| Ultimo aggiornamento | | | Diagnostica, sul dispositivo dell'account: ultima lettura riuscita |
| Scarica le bollette | | | Pulsante, sul dispositivo dell'account: rilegge subito l'account e scarica le bollette mancanti |
| Pannello: … | ✅ | | Solo con *Adotta un pannello*: vedi [Adotta un pannello](#%EF%B8%8F-adotta-un-pannello) |

<details>
<summary><b>Esempio di automazione: avviso per una bolletta da pagare</b></summary>

```yaml
automation:
  - alias: "Plenitude: bolletta luce da pagare"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.plenitude_luce_da_pagare
        above: 0
    actions:
      - action: notify.notify
        data:
          title: "Bolletta luce da pagare"
          message: >
            {{ states('sensor.plenitude_luce_da_pagare') }} € entro il
            {{ state_attr('sensor.plenitude_luce_da_pagare', 'prossima_scadenza') }}
```

Gli identificativi delle entità dipendono dalla lingua di Home Assistant: controllali in
**Impostazioni → Dispositivi e servizi → Plenitude**.

</details>

<details>
<summary><b>Entità con le bollette da cartella</b></summary>

*Importo*, *Scadenza* e *Consumo* dell'ultima bolletta, *Consumo annuo*, *Bollette lette*
e *Ultimo aggiornamento*.

</details>

## ⚡ Pannello Energia

Con l'account, lo storico dei consumi mensili di ogni fornitura viene importato come
statistica esterna, da aggiungere in **Impostazioni → Pannelli → Energia**:

| Statistica | Unità | Dove aggiungerla |
|---|:---:|---|
| `plenitude:consumo_luce_<conto>` | kWh | **Rete elettrica → Consumo dalla rete** |
| `plenitude:consumo_gas_<conto>` | m³ | **Consumo di gas** |

I valori sono **mensili** (registrati all'inizio di ogni mese, con lo storico
dall'attivazione della fornitura): guardali per mese o per anno. Il mese in corso e i
due precedenti vengono aggiornati a ogni lettura, così le stime vengono sostituite dai
consumi reali.

### 📅 Spesa di ogni mese

Le bollette coprono di solito due mesi: l'integrazione le divide mese per mese. A ogni
mese va la sua parte fissa (gas: quota fissa del mese; luce: quota fissa, quota potenza e
canone RAI) e il resto dell'importo è diviso in proporzione ai consumi di ciascun mese.
La somma dei mesi è sempre uguale a quanto hai pagato.

Oltre ai sensori *Spesa ultimo mese* e *Spesa totale ultimo mese*, le spese sono
importate come statistiche in euro, con lo storico di tutte le bollette scaricate:

| Statistica | Contenuto |
|---|---|
| `plenitude:spesa_luce_<conto>` | Spesa della luce |
| `plenitude:spesa_gas_<conto>` | Spesa del gas |
| `plenitude:spesa_totale_<account>` | Luce + gas, nei mesi già fatturati per entrambi |

<details>
<summary><b>Scheda con il grafico della spesa mese per mese</b></summary>

Aggiungi una scheda manuale alla plancia (sostituisci gli identificativi con i tuoi, che
trovi in **Strumenti per sviluppatori → Statistiche** cercando "plenitude"):

```yaml
type: statistics-graph
title: Spesa Plenitude
chart_type: bar
period: month
days_to_show: 365
stat_types:
  - change
entities:
  - plenitude:spesa_luce_<conto>
  - plenitude:spesa_gas_<conto>
```

</details>

### 💶 Costi nel pannello Energia

Nella configurazione di ogni sorgente scegli **"Utilizza un'entità con il prezzo corrente"**:

| Sorgente | Entità da scegliere | Perché |
|---|---|---|
| ⚡ Luce | **Plenitude Luce · Prezzo medio** (€/kWh) | Importo dell'ultima bolletta diviso per i kWh fatturati: comprende energia, trasporto, oneri, quote fisse, imposte e IVA, quindi il totale nel pannello si avvicina alla bolletta. Se vuoi vedere solo il costo dei consumi usa invece **Prezzo energia**. |
| 🔥 Gas | **Plenitude Gas · Prezzo gas** (€/m³) | Prezzo reale del gas per lo scaglione raggiunto quest'anno. Il *prezzo medio* del gas non è adatto: d'estate le quote fisse lo gonfiano. |

> [!NOTE]
> Gli Smc (metri cubi standard) delle bollette sono trattati come m³ (differiscono per il
> coefficiente C, circa l'1%), perché il pannello Energia accetta i prezzi del gas in €/m³.

> [!NOTE]
> Il solo PUN (prezzo all'ingrosso) copre circa metà del costo reale della luce: per
> avere nel pannello la spesa vera usa il *Prezzo medio* di Plenitude. Per il gas il
> pannello non può aggiungere le quote fisse mensili (circa 17 € al mese, IVA inclusa):
> il costo mostrato è quello dei consumi.

> [!IMPORTANT]
> Per il gas puoi usare in alternativa il sensore **Lettura contatore** (m³, totale
> crescente): scegli **uno solo** dei due, altrimenti i consumi vengono contati due volte.
> Se hai MyItalgas, i suoi consumi giornalieri sono più precisi: in quel caso usa
> MyItalgas per il gas e Plenitude per i prezzi.

## ☀️ Adotta un pannello

Se sulla fornitura di luce hai attivato **Adotta un pannello**, l'integrazione legge
dall'area clienti la produzione del pannello e i consumi del contatore 2G **quarto d'ora
per quarto d'ora**, e calcola l'energia che "rientra in casa" e quanto ti fa risparmiare,
con le regole delle condizioni economiche del servizio:

- **Autoconsumo virtuale**: in ogni quarto d'ora, l'energia consumata mentre il pannello
  produce, fino alla sua produzione (il minimo tra consumo e produzione). La produzione
  in eccesso e i consumi quando il pannello non produce non danno sconti.
- **Risparmio**: su quei kWh il *Corrispettivo Luce* (perdite di rete comprese, al netto
  degli sconti dell'offerta) è azzerato. Il risparmio è kWh autoconsumati × Corrispettivo
  Luce, con l'IVA al 10%.
- **Canone**: il canone mensile del pannello (dalla taglia: ad esempio 5 €/mese per il
  Large) più IVA, contato giorno per giorno. *Risparmio − canone* è il **risparmio netto**.

Tutto parte dal **primo quarto d'ora in cui il pannello ha prodotto**: i giorni prima
dell'attivazione, quando il pannello non c'era, non contano. Lo storico dall'attivazione
viene recuperato da solo e conservato su disco.

| Entità (dispositivo Plenitude Luce) | Descrizione |
|---|---|
| Potenza pannello | W prodotti in questo momento (aggiornata ogni 15 minuti); negli attributi i pannelli, la potenza nominale e la data di attivazione |
| Produzione pannello del mese | kWh prodotti nel mese; negli attributi consumo, autoconsumo, prelievo dalla rete, produzione non autoconsumata, % autoconsumata e % dei consumi coperta |
| Autoconsumo pannello del mese | kWh autoconsumati nel mese, con gli stessi attributi |
| Risparmio pannello del mese | € risparmiati nel mese (IVA inclusa); negli attributi canone, risparmio netto e valore di un kWh |
| Produzione pannello totale | kWh prodotti dall'attivazione |
| Risparmio pannello totale | € risparmiati dall'attivazione, con canone e netto |
| Valore energia autoconsumata | Diagnostica: Corrispettivo Luce usato oggi (€/kWh, IVA esclusa) |

> [!NOTE]
> La produzione arriva a blocchi durante la giornata, i consumi del contatore di solito il
> giorno dopo: autoconsumo e risparmio arrivano fino all'ultimo quarto d'ora con entrambi
> i dati (attributo *Dati fino al*). I giorni recenti si rileggono ogni ora, così i dati
> arrivati in ritardo o rettificati entrano da soli.

### 🔆 Pannello Energia con il pannello virtuale

Sono importate statistiche **orarie** dall'attivazione del pannello:

| Statistica | Unità | Dove aggiungerla |
|---|:---:|---|
| `plenitude:pannello_prelievo_<conto>` | kWh | **Rete elettrica → Consumo dalla rete** |
| `plenitude:pannello_immissione_<conto>` | kWh | **Rete elettrica → Ritorno alla rete** (compenso: nessuno) |
| `plenitude:pannello_produzione_<conto>` | kWh | **Pannelli solari → Produzione solare** |
| `plenitude:pannello_autoconsumo_<conto>` | kWh | Facoltativa, per i grafici: energia autoconsumata |
| `plenitude:pannello_risparmio_<conto>` | € | Facoltativa, per i grafici: risparmio ora per ora |

Così il pannello Energia funziona come con un impianto vero: consumo della casa =
prelievo + produzione − produzione non autoconsumata, cioè esattamente i consumi del
contatore; vedi quanta energia arriva dal pannello, la quota autoconsumata e
l'autosufficienza. Per il costo del prelievo usa il *Prezzo energia* o il *Prezzo medio*:
il pannello Energia lo applica solo al prelievo, mentre sull'energia autoconsumata in
bolletta restano comunque rete, oneri e imposte (si azzera solo il Corrispettivo Luce).

> [!IMPORTANT]
> Queste statistiche sostituiscono, dall'attivazione del pannello, il *Consumo dalla rete*
> mensile (`plenitude:consumo_luce_<conto>`) o un tuo misuratore (es. Shelly) usato come
> consumo dalla rete: usa **una sola** fonte, altrimenti i consumi si contano due volte.
> I dati Plenitude arrivano con circa un giorno di ritardo; un misuratore in casa resta
> utile come dispositivo singolo per i consumi in tempo reale.

### 💶 Valore dell'energia autoconsumata

Il Corrispettivo Luce di ogni giorno si prende, nell'ordine:

1. dalla **bolletta** che fattura quel giorno (Corrispettivo Luce + perdite di rete +
   sconti a consumo, diviso per i kWh): è il valore esatto;
2. dall'opzione **Corrispettivo Luce per il pannello virtuale**, se l'hai indicata: il
   valore "incluse le perdite di rete" delle tue condizioni economiche, meno gli sconti
   sul corrispettivo (ad esempio Special Days o domiciliazione), IVA esclusa;
3. dall'ultima bolletta con le **condizioni economiche in corso** (esatto per un prezzo
   fisso, una stima per un prezzo indicizzato).

Se le condizioni economiche sono appena cambiate e non c'è ancora una bolletta né
l'opzione, il risparmio resta *sconosciuto* (non zero) finché il valore non arriva; quando
arriva una bolletta, risparmio e statistiche si ricalcolano da soli.

## 🔗 Collegamento con MyItalgas

Se scegli una **fornitura MyItalgas** nelle opzioni, a ogni aggiornamento Plenitude crea
o aggiorna in MyItalgas i **periodi tariffari** del gas: un periodo per ogni mese presente
nelle bollette, con i tre prezzi per scaglione e la quota fissa, IVA inclusa. Così il
sensore *Costo stimato* e la statistica dei costi di MyItalgas usano i prezzi reali.

<details>
<summary><b>Come vengono calcolati i prezzi</b></summary>

- Le bollette riportano le tariffe per lo **scaglione annuo di consumo** raggiunto in ogni
  mese (fino a 120, 121–480, oltre 480 Smc): quel prezzo è preso esattamente dalla bolletta.
- Per gli scaglioni non raggiunti si usa il valore noto più vicino.
- Oltre 480 Smc/anno l'IVA passa dal 10% al 22%.
- Quando una bolletta rifattura mesi già fatturati (conguaglio con le letture reali),
  valgono le voci della bolletta più recente.
- Vengono lette sia le bollette attuali sia quelle nel formato usato fino all'inizio del 2025.

Con l'account, i PDF delle ultime 24 bollette gas vengono scaricati una sola volta in
`config/plenitude/archivio/<conto>/` e letti da lì.

</details>

## 📁 Bollette da cartella

Senza account, le bollette gas in PDF vengono lette da una cartella: scegli **Cartella di bollette PDF** quando aggiungi l'integrazione. La cartella predefinita è `config/plenitude/bollette`
e viene creata da sola al primo avvio.

- **A mano:** copia nella cartella i PDF delle bollette gas scaricati dall'area clienti
  Plenitude. Più ne metti, più indietro va lo storico dei prezzi. Puoi rimuoverli dopo
  l'importazione: i periodi tariffari restano in MyItalgas.
- **In automatico dalle email:** se ricevi le bollette via email con il PDF allegato, usa
  l'integrazione ufficiale **IMAP** di Home Assistant per salvare l'allegato nella cartella
  appena arriva (azione *Imap: scarica allegato* / `imap.download`).

## 🪶 Leggera per Home Assistant

- Un aggiornamento ogni 12 ore (modificabile), con poche chiamate all'area clienti.
- Con *Adotta un pannello*: la potenza ogni 15 minuti e i quarti d'ora dei giorni recenti
  ogni ora; i giorni già completi non si richiedono più.
- I PDF delle bollette si scaricano e si leggono **una volta sola**, in background, uno
  alla volta: il risultato resta in una cache su disco e dopo un riavvio non si rileggono.
- Gli aggiornamenti non leggono mai PDF: avvio e aggiornamenti richiedono pochi secondi.
- Un PDF non ancora disponibile si riprova dopo un giorno, non a ogni aggiornamento.

## 🔐 Come funziona l'accesso

L'integrazione usa gli stessi servizi dell'app Plenitude. All'accesso con email, password
e codice di verifica si ottiene una sessione che dura **un anno**, salvata nella
configurazione di Home Assistant: dopo un riavvio non serve rifare nulla.

Se la sessione scade o viene revocata (ad esempio cambiando password), Home Assistant
mostra la richiesta di **riautenticazione** in *Impostazioni → Dispositivi e servizi*:
basta inserire di nuovo password e codice. Se Plenitude è momentaneamente irraggiungibile,
l'integrazione riprova da sola senza chiederti nulla.

## 🛡️ Privacy

- I dati restano sul tuo Home Assistant e vengono richiesti solo a Plenitude.
- La password non viene salvata: si conserva solo la sessione dell'account.
- I PDF scaricati restano in `config/plenitude/archivio/`; puoi cancellarli quando vuoi
  (verranno riscaricati solo quelli che servono).
- La **diagnostica** scaricabile e i log non contengono credenziali, sessione, codici dei
  conti, PDR, POD né indirizzi.

## 🧰 Risoluzione dei problemi

| Problema | Soluzione |
|---|---|
| *"Questa integrazione non supporta la configurazione tramite l'interfaccia utente"* | L'integrazione non è installata o Home Assistant non è stato riavviato: fai i passi 1 e 2 dell'[installazione](#-installazione-in-3-click). |
| *Email o password errate* | Usa le stesse credenziali dell'app Plenitude. |
| Il codice non arriva | Controlla lo spam, poi spunta *Invia un nuovo codice*. |
| *La verifica è scaduta* | Chiudi la finestra e ricomincia l'aggiunta dell'integrazione. |
| *Account bloccato temporaneamente* | Troppi tentativi: riprova dopo qualche minuto. |
| Richiesta di riautenticazione | La sessione è scaduta o revocata: inserisci di nuovo password e codice. |
| L'integrazione va in *riprova* | Plenitude non raggiungibile: riprova da sola più tardi. |
| *Bollette lette* resta a 0 | I PDF sono ancora in download (pochi minuti) o non sono bollette leggibili. |
| Manca una bolletta o un prezzo | Premi **Scarica le bollette** sul dispositivo *Plenitude*: rilegge l'account e riprova i download. |
| Una fornitura non c'è più | Il suo dispositivo diventa non disponibile: puoi eliminarlo dalla pagina del dispositivo. |
| I prezzi non arrivano in MyItalgas | Verifica la fornitura MyItalgas nelle opzioni e che MyItalgas sia installato. |

Per il debug: **Impostazioni → Dispositivi e servizi → Plenitude → ⋮ → Abilita il debug**.
Per segnalare un problema apri una
[issue](https://github.com/LucaTomei/ha-plenitude/issues) allegando la diagnostica
(è già priva di dati personali).

## 🛠️ Sviluppo

<details>
<summary><b>Test e rilascio</b></summary>

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

I test girano su un account Plenitude simulato e su bollette di esempio anonimizzate
(nessun dato personale, nessun accesso reale a Plenitude), sia sull'ultima versione di
Home Assistant sia sulla 2025.11.

Per pubblicare una versione: aggiungi la voce in `CHANGELOG.md` e lancia
`scripts/rilascia.sh X.Y.Z`.

</details>

## ⚖️ Note legali

- **Progetto indipendente.** Questa integrazione è un progetto personale, sviluppato
  nel tempo libero e distribuito gratuitamente. Non è affiliata, collegata, sponsorizzata,
  approvata né supportata da Eni Plenitude S.p.A., da Eni S.p.A. o da altre società del
  gruppo, e non ne rappresenta in alcun modo la posizione.
- **Marchi.** "Eni", "Plenitude", i relativi loghi e gli altri marchi citati appartengono
  ai rispettivi titolari. Sono usati solo per indicare con quale servizio funziona
  l'integrazione, senza alcuna pretesa di titolarità o di approvazione.
- **Uso dei dati.** L'integrazione accede soltanto all'account di chi la configura, con le
  sue credenziali, agli stessi dati che quella persona vede nell'app Plenitude, e li
  conserva solo nella sua installazione di Home Assistant. Ogni utente è responsabile
  dell'uso che ne fa e del rispetto delle condizioni del servizio Plenitude.
- **Nessuna garanzia.** Il software è fornito "così com'è", senza garanzie, come previsto
  dalla licenza [MIT](LICENSE). I dati mostrati sono indicativi: per importi, scadenze e
  consumi fanno fede le bollette e l'area clienti Plenitude.
- **Richieste dei titolari.** Se sei titolare di un diritto e ritieni che qualcosa in
  questo repository debba essere modificato o rimosso, apri una issue: la richiesta verrà
  valutata e gestita prontamente.
