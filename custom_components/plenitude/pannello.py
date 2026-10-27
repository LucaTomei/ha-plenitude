"""Servizio "Adotta un pannello": produzione del pannello virtuale e autoconsumo virtuale.

Il pannello è una quota di un impianto fotovoltaico. L'energia consumata in casa mentre
il pannello produce, fino alla sua produzione, è "autoconsumo virtuale": per quei kWh il
Corrispettivo Luce (perdite di rete incluse, al netto degli sconti dell'offerta) è
azzerato. Il conto si fa quarto d'ora per quarto d'ora, sui consumi del contatore 2G e
sulla produzione del pannello, come in bolletta. In cambio si paga un canone mensile che
dipende dalla taglia del pannello.

L'area clienti riporta produzione e consumi di ogni quarto d'ora in Wh, con l'ora locale
italiana di inizio del quarto d'ora. La produzione arriva a blocchi durante la giornata,
i consumi del contatore di solito il giorno dopo.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .account import Conto, _numero

FUSO = ZoneInfo("Europe/Rome")
QUARTO = timedelta(minutes=15)
ORA = timedelta(hours=1)

# Giorni di cui si chiedono di nuovo i dati anche se già completi (eventuali rettifiche).
GIORNI_RETTIFICHE = 3
# Giorni oltre i quali i dati mancanti (es. contatore che non ha trasmesso) non si
# richiedono più.
GIORNI_ATTESA_DATI = 45
# Anni in cui cercare l'attivazione del pannello.
ANNI_RICERCA = 10


@dataclass(frozen=True, slots=True)
class Pannello:
    """Un pannello virtuale adottato."""

    codice: str
    posizione: str | None
    taglia: str | None
    potenza: float | None  # kW
    stato: str | None
    canone: float | None = None  # €/mese, IVA esclusa


@dataclass(frozen=True, slots=True)
class Quarto:
    """Produzione e consumo di un quarto d'ora, in kWh (None se non ancora disponibili)."""

    inizio: datetime  # UTC
    produzione: float | None
    consumo: float | None

    @property
    def completo(self) -> bool:
        """Se si conoscono sia la produzione sia il consumo."""
        return self.produzione is not None and self.consumo is not None

    @property
    def autoconsumo(self) -> float:
        """Energia consumata coperta dalla produzione del pannello."""
        return min(self.produzione or 0.0, self.consumo or 0.0) if self.completo else 0.0

    @property
    def prelievo(self) -> float:
        """Energia consumata oltre la produzione del pannello (pagata per intero)."""
        return max((self.consumo or 0.0) - (self.produzione or 0.0), 0.0) if self.completo else 0.0

    @property
    def immissione(self) -> float:
        """Energia prodotta dal pannello e non consumata (non dà alcuno sconto)."""
        return max((self.produzione or 0.0) - (self.consumo or 0.0), 0.0) if self.completo else 0.0


@dataclass(slots=True)
class DatiPannello:
    """Pannelli di una fornitura di luce e i loro dati, dall'attivazione."""

    pannelli: list[Pannello] = field(default_factory=list)
    potenza_istantanea: float | None = None  # W
    # inizio (UTC) del primo quarto d'ora in cui il pannello ha prodotto
    attivo_dal: datetime | None = None
    quarti: dict[datetime, Quarto] = field(default_factory=dict)
    # totali del mese in corso e dall'attivazione, e valore di un kWh autoconsumato oggi
    # (€/kWh IVA esclusa): li calcola il coordinatore, che conosce le bollette e le opzioni
    mese: Riepilogo | None = None
    totale: Riepilogo | None = None
    prezzo: float | None = None

    @property
    def canone(self) -> float:
        """Canone mensile complessivo dei pannelli attivi, IVA esclusa."""
        return round(sum(p.canone or 0.0 for p in self.pannelli), 2)

    @property
    def potenza(self) -> float:
        """Potenza complessiva dei pannelli, in kW."""
        return round(sum(p.potenza or 0.0 for p in self.pannelli), 3)

    @property
    def ultima_produzione(self) -> datetime | None:
        """Fine (UTC) dell'ultimo quarto d'ora con la produzione."""
        noti = [q.inizio for q in self.quarti.values() if q.produzione is not None]
        return max(noti) + QUARTO if noti else None

    @property
    def ultimo_completo(self) -> datetime | None:
        """Fine (UTC) dell'ultimo quarto d'ora con produzione e consumo."""
        noti = [q.inizio for q in self.quarti.values() if q.completo]
        return max(noti) + QUARTO if noti else None


@dataclass(frozen=True, slots=True)
class Riepilogo:
    """Totali di un periodo, in kWh ed euro (IVA inclusa)."""

    produzione: float
    consumo: float
    autoconsumo: float
    prelievo: float
    immissione: float
    risparmio: float | None
    canone: float

    @property
    def netto(self) -> float | None:
        """Risparmio meno il canone del pannello."""
        return round(self.risparmio - self.canone, 2) if self.risparmio is not None else None

    @property
    def quota_autoconsumata(self) -> float | None:
        """Percentuale della produzione consumata in casa (nei quarti d'ora con i consumi)."""
        prodotta = self.autoconsumo + self.immissione
        return round(100 * self.autoconsumo / prodotta, 1) if prodotta > 0 else None

    @property
    def copertura(self) -> float | None:
        """Percentuale dei consumi coperta dal pannello."""
        if self.consumo <= 0:
            return None
        return round(100 * self.autoconsumo / self.consumo, 1)


# --- Lettura delle risposte dell'area clienti ------------------------------------------


def _testo(valore: Any) -> str | None:
    testo = str(valore or "").strip()
    return testo or None


def pannelli_da(risposta: Any) -> list[Pannello]:
    """Pannelli attivi dai dettagli della fornitura."""
    voci = risposta.get("panels") if isinstance(risposta, dict) else None
    pannelli: list[Pannello] = []
    for voce in voci if isinstance(voci, list) else []:
        if not isinstance(voce, dict) or not (codice := _testo(voce.get("identifier"))):
            continue
        stato = _testo(voce.get("state"))
        if stato is not None and stato.upper() != "ATTIVO":
            continue
        pannelli.append(
            Pannello(
                codice=codice,
                posizione=_testo(voce.get("position")),
                taglia=_testo(voce.get("size")),
                potenza=_numero(voce.get("power")),
                stato=stato,
            )
        )
    return pannelli


def canone_da(risposta: Any, codice: str) -> float | None:
    """Canone mensile (€, IVA esclusa) della taglia di pannello indicata, dal catalogo."""
    corpo = risposta.get("body") if isinstance(risposta, dict) else None
    pannello = corpo.get("virtualPanel") if isinstance(corpo, dict) else None
    luoghi = pannello.get("locations") if isinstance(pannello, dict) else None
    for luogo in luoghi if isinstance(luoghi, list) else []:
        taglie = luogo.get("sizes") if isinstance(luogo, dict) else None
        for taglia in taglie if isinstance(taglie, list) else []:
            if isinstance(taglia, dict) and taglia.get("productId") == codice:
                return _numero(taglia.get("price"))
    return None


def potenza_da(risposta: Any) -> float | None:
    """Potenza istantanea complessiva (W) dei pannelli.

    L'area clienti la chiama `instantProductionWh`, ma è la potenza media dell'ultimo
    intervallo: alle ore centrali di una giornata di sole è vicina alla potenza nominale.
    """
    voci = [v for v in risposta if isinstance(v, dict)] if isinstance(risposta, list) else []
    valori = [n for v in voci if (n := _numero(v.get("instantProductionWh"))) is not None]
    return round(sum(valori), 2) if valori else None


def _ora_utc(locale: datetime) -> datetime | None:
    """Ora UTC di un'ora locale italiana; None se l'ora non esiste (cambio dell'ora).

    Nel giorno del ritorno all'ora solare l'area clienti riporta comunque 96 quarti
    d'ora, con le ore dalle 2 alle 3 una volta sola: si attribuiscono alla prima delle due
    (ora legale, `fold=0`).
    """
    utc = locale.replace(tzinfo=FUSO).astimezone(UTC)
    if utc.astimezone(FUSO).replace(tzinfo=None) != locale:
        return None
    return utc


def righe_giorno(risposta: Any) -> list[list[Any]]:
    """Righe [ora locale "AAAA-MM-GGTHH:MM:SS", produzione Wh, consumo Wh] di un giorno.

    È la forma in cui si conservano su disco; i valori mancanti restano None.
    """
    if not isinstance(risposta, dict):
        return []
    unita = str(risposta.get("measureUnit") or "").strip().lower()
    if unita and unita != "wh":
        return []  # granularità diversa da quella chiesta
    voci = risposta.get("energyProducedConsumed")
    righe = []
    for voce in voci if isinstance(voci, list) else []:
        if not isinstance(voce, dict) or not isinstance(voce.get("timeUnit"), str):
            continue
        righe.append(
            [
                voce["timeUnit"][:19],
                _numero(voce.get("energyProduced")),
                _numero(voce.get("energyConsumed")),
            ]
        )
    return righe


def quarti_da_righe(righe: Iterable[list[Any]]) -> list[Quarto]:
    """Quarti d'ora (in UTC, valori in kWh) dalle righe di un giorno."""
    quarti: dict[datetime, Quarto] = {}
    for riga in righe:
        try:
            locale = datetime.fromisoformat(riga[0])
        except (TypeError, ValueError, IndexError):
            continue
        if (inizio := _ora_utc(locale)) is None or inizio in quarti:
            continue
        produzione = round(riga[1] / 1000, 6) if riga[1] is not None else None
        consumo = round(riga[2] / 1000, 6) if riga[2] is not None else None
        quarti[inizio] = Quarto(inizio=inizio, produzione=produzione, consumo=consumo)
    return list(quarti.values())


def primo_periodo_con_produzione(risposta: Any) -> date | None:
    """Primo periodo (giorno o mese, secondo la granularità) in cui il pannello ha prodotto."""
    voci = risposta.get("energyProducedConsumed") if isinstance(risposta, dict) else None
    periodi = []
    for voce in voci if isinstance(voci, list) else []:
        if not isinstance(voce, dict) or _numero(voce.get("energyProduced")) is None:
            continue
        try:
            periodi.append(date.fromisoformat(str(voce.get("timeUnit"))[:10]))
        except ValueError:
            continue
    return min(periodi) if periodi else None


def unisci(dati: DatiPannello, quarti: Iterable[Quarto]) -> None:
    """Aggiunge i quarti d'ora letti e ricalcola l'attivazione.

    Il pannello è attivo dal primo quarto d'ora con una produzione; i quarti d'ora
    precedenti (quando il pannello non c'era) si scartano.
    """
    for q in quarti:
        dati.quarti[q.inizio] = q
    prodotti = [q.inizio for q in dati.quarti.values() if q.produzione is not None]
    dati.attivo_dal = min(prodotti) if prodotti else None
    if dati.attivo_dal is not None:
        dati.quarti = {
            inizio: q for inizio, q in sorted(dati.quarti.items()) if inizio >= dati.attivo_dal
        }


# --- Giorni da richiedere --------------------------------------------------------------


def giorno_locale(istante: datetime) -> date:
    """Giorno in Italia di un istante."""
    return istante.astimezone(FUSO).date()


def giorno_completo(
    righe: list[list[Any]], adesso: datetime, attivo_dal: datetime | None = None
) -> bool:
    """Se un giorno ha tutti i dati dei quarti d'ora già trascorsi (dall'attivazione)."""
    if not righe:
        return False
    for q in quarti_da_righe(righe):
        if attivo_dal is not None and q.inizio < attivo_dal:
            continue
        if q.inizio + QUARTO <= adesso and not q.completo:
            return False
    return giorno_locale(adesso) > date.fromisoformat(righe[0][0][:10])


def giorni_da_leggere(
    salvati: dict[str, list[list[Any]]],
    dal: date,
    adesso: datetime,
    massimo: int,
    attivo_dal: datetime | None = None,
) -> list[date]:
    """Giorni di cui chiedere i quarti d'ora: prima i più recenti, poi lo storico.

    Si chiedono i giorni mai letti, quelli recenti (rettifiche) e quelli a cui mancano
    ancora dati, finché è ragionevole aspettarli. I giorni recenti vengono per primi, così
    durante il recupero di un lungo storico i dati di oggi restano aggiornati.
    """
    oggi = giorno_locale(adesso)
    recenti = [
        g for g in (oggi - timedelta(days=n) for n in range(GIORNI_RETTIFICHE, -1, -1)) if g >= dal
    ]
    storico: list[date] = []
    giorno = dal
    while giorno < (recenti[0] if recenti else oggi + timedelta(days=1)):
        righe = salvati.get(giorno.isoformat())
        if righe is None or (
            (oggi - giorno).days <= GIORNI_ATTESA_DATI
            and not giorno_completo(righe, adesso, attivo_dal)
        ):
            storico.append(giorno)
            if len(storico) >= massimo:
                break
        giorno += timedelta(days=1)
    return (recenti + storico)[:massimo]


# --- Serie orarie e totali ---------------------------------------------------------------

# prezzo dell'energia autoconsumata in un giorno: €/kWh IVA esclusa, None se non noto
type Prezzo = Callable[[date], float | None]


def prezzo_pannello(conto: Conto, manuale: float | None = None) -> Prezzo:
    """Valore di un kWh autoconsumato in un giorno: Corrispettivo Luce, IVA esclusa.

    Per i giorni già fatturati vale il Corrispettivo Luce della bolletta (perdite
    incluse, al netto degli sconti). Per gli altri il valore indicato nelle opzioni, se
    c'è, altrimenti quello dell'ultima bolletta con le condizioni economiche in corso: per
    un prezzo fisso è esatto, per un prezzo indicizzato è una stima che la bolletta
    successiva corregge.
    """
    periodi: list[tuple[date, date, float]] = []
    for b in sorted(conto.bollette_luce.values(), key=lambda b: (b.al, b.emissione or b.al)):
        periodi.extend(b.corrispettivi)
    periodi.reverse()  # la bolletta più recente prevale (eventuali ricalcoli)
    in_corso = [p for p in periodi if conto.condizioni_dal is None or p[0] >= conto.condizioni_dal]
    stima = max(in_corso, key=lambda p: p[1])[2] if in_corso else None

    def prezzo(giorno: date) -> float | None:
        for dal, al, valore in periodi:
            if dal <= giorno <= al:
                return valore
        return manuale if manuale is not None else stima

    return prezzo


def serie_orarie(
    dati: DatiPannello, prezzo: Prezzo, iva: float
) -> dict[str, dict[datetime, float]]:
    """Valori di ogni ora (UTC) dall'attivazione all'ultima ora con dati.

    La produzione arriva fino all'ultima ora prodotta; autoconsumo, prelievo, energia
    non autoconsumata e risparmio fino all'ultima ora con anche i consumi. Le ore
    intermedie senza dati valgono zero (si correggono quando i dati arrivano). Il
    risparmio si ferma prima della prima ora di cui non si conosce il prezzo, come i
    sensori: meglio un dato che arriva dopo che un risparmio sottostimato.
    """
    serie: dict[str, dict[datetime, float]] = {
        "produzione": {},
        "autoconsumo": {},
        "prelievo": {},
        "immissione": {},
        "risparmio": {},
    }
    if dati.attivo_dal is None:
        return serie
    inizio = dati.attivo_dal.replace(minute=0, second=0, microsecond=0)
    fine_produzione, fine_completi = dati.ultima_produzione, dati.ultimo_completo
    senza_prezzo: datetime | None = None
    ora = inizio
    while fine_produzione is not None and ora < fine_produzione:
        serie["produzione"][ora] = 0.0
        if fine_completi is not None and ora < fine_completi:
            for nome in ("autoconsumo", "prelievo", "immissione", "risparmio"):
                serie[nome][ora] = 0.0
        ora += ORA
    for q in dati.quarti.values():
        ora = q.inizio.replace(minute=0, second=0, microsecond=0)
        if q.produzione is not None and ora in serie["produzione"]:
            serie["produzione"][ora] += q.produzione
        if not q.completo or ora not in serie["autoconsumo"]:
            continue
        serie["autoconsumo"][ora] += q.autoconsumo
        serie["prelievo"][ora] += q.prelievo
        serie["immissione"][ora] += q.immissione
        if not q.autoconsumo:
            continue
        if (p := prezzo(giorno_locale(q.inizio))) is None:
            senza_prezzo = ora if senza_prezzo is None else min(senza_prezzo, ora)
        else:
            serie["risparmio"][ora] += q.autoconsumo * p * iva
    if senza_prezzo is not None:
        serie["risparmio"] = {o: v for o, v in serie["risparmio"].items() if o < senza_prezzo}
    return {nome: {o: round(v, 6) for o, v in valori.items()} for nome, valori in serie.items()}


def _giorni_del_mese(giorno: date) -> int:
    successivo = date(giorno.year + giorno.month // 12, giorno.month % 12 + 1, 1)
    return (successivo - date(giorno.year, giorno.month, 1)).days


def aggiorna_riepiloghi(dati: DatiPannello, prezzo: Prezzo, iva: float, adesso: datetime) -> None:
    """Totali del mese in corso e dall'attivazione, e prezzo di oggi."""
    oggi = giorno_locale(adesso)
    dati.mese = riepilogo(dati, prezzo, iva, date(oggi.year, oggi.month, 1))
    dati.totale = riepilogo(dati, prezzo, iva)
    dati.prezzo = prezzo(oggi)


def riepilogo(
    dati: DatiPannello, prezzo: Prezzo, iva: float, mese: date | None = None
) -> Riepilogo | None:
    """Totali dall'attivazione, o del solo mese indicato.

    Il canone si conta giorno per giorno (come in bolletta per i mesi parziali), per i
    giorni in cui il pannello era attivo (con una produzione registrata) fino all'ultimo
    giorno con i consumi: risparmio e canone riguardano gli stessi giorni, e un periodo
    senza pannello (rimosso e poi riadottato) non paga canone.
    """
    if dati.attivo_dal is None:
        return None
    quarti = [
        q
        for q in dati.quarti.values()
        if mese is None
        or (giorno_locale(q.inizio).year, giorno_locale(q.inizio).month) == (mese.year, mese.month)
    ]
    completi = [q for q in quarti if q.completo]
    risparmio: float | None = 0.0
    for q in completi:
        if not q.autoconsumo:
            continue
        if (p := prezzo(giorno_locale(q.inizio))) is None:
            risparmio = None
            break
        risparmio += q.autoconsumo * p * iva
    canone = 0.0
    if (fine := dati.ultimo_completo) is not None:
        attivi = {
            giorno_locale(q.inizio) for q in quarti if q.produzione is not None and q.inizio < fine
        }
        canone = sum(dati.canone * iva / _giorni_del_mese(g) for g in attivi)
    return Riepilogo(
        produzione=round(sum(q.produzione or 0.0 for q in quarti), 3),
        consumo=round(sum(q.consumo or 0.0 for q in completi), 3),
        autoconsumo=round(sum(q.autoconsumo for q in completi), 3),
        prelievo=round(sum(q.prelievo for q in completi), 3),
        immissione=round(sum(q.immissione for q in completi), 3),
        risparmio=round(risparmio, 2) if risparmio is not None else None,
        canone=round(canone, 2),
    )
