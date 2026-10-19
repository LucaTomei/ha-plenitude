"""Lettura delle bollette Plenitude dal testo estratto dal PDF.

Dal testo si ricavano i dati della fornitura, i consumi e il dettaglio dei costi;
da questi si calcolano i periodi tariffari (prezzi per scaglione e quota fissa) che
l'integrazione MyItalgas usa per stimare la spesa del gas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re

from .const import IVA_AGEVOLATA, IVA_ORDINARIA

# Scaglioni annui di consumo (Smc dal 1° gennaio): fino a 120, 121-480, oltre 480.
SOGLIE_SCAGLIONI: tuple[float, float] = (120.0, 480.0)

_RE_PERIODO = re.compile(
    r"Periodo di fatturazione:\s*dal\s+(\d{2}/\d{2}/\d{4})\s+al\s+(\d{2}/\d{2}/\d{4})"
)
_RE_PDR = re.compile(r"\bPDR\b.*?(\d{14})", re.S)
_RE_POD = re.compile(r"\bPOD\b.*?(IT\d{3}E\d{8})", re.S)
_RE_COEFF = re.compile(r"Coefficiente C\*?:\s*([\d,]+)")
_RE_CONSUMO = re.compile(r"Consumo fatturato\*?\s+(\d+)")
_RE_TOTALE = re.compile(r"Totale da pagare per questa bolletta\s+([\d.,]+)")
_RE_SCADENZA = re.compile(r"Totale da pagare entro il\s+(\d{2}/\d{2}/\d{4})")
_RE_EMISSIONE = re.compile(r"Data di emissione\s+(\d{2}/\d{2}/\d{4})")
_RE_CONSUMO_ANNO = re.compile(r"In un anno hai consumato\s+(\d+)\s+Smc")
_RE_DETTAGLIO = re.compile(
    r"DAL (\d{2}/\d{2}/\d{4}) AL (\d{2}/\d{2}/\d{4})\s+(€/\S+)\s+(-?[\d.,]+)\s+(.+?)\s+(AGE|ORD)\s*$"
)

# Intestazioni delle voci di dettaglio -> (ruolo, è una quota fissa).
_COMPONENTI: dict[str, tuple[str, bool]] = {
    "Corrispettivo Gas Index": ("materia", False),
    "Contributo al consumo": ("contributo", False),
    "Quota Energia (distribuzione)": ("distribuzione", False),
    "Quota Energia (trasporto)": ("trasporto", False),
    "Imposta erariale": ("accisa", False),
    "Addizionale enti locali": ("addizionale", False),
    "Commercializzazione e vendita - parte fissa": ("commercializzazione", True),
    "Sconto Domiciliazione": ("sconto", True),
}


def _numero(testo: str) -> float:
    """Converte un numero in formato italiano ("1.234,56" -> 1234.56)."""
    pulito = testo.strip().replace(".", "").replace(",", ".")
    return float(pulito)


@dataclass(frozen=True, slots=True)
class VoceCosto:
    """Una riga di dettaglio dei costi."""

    ruolo: str
    dal: date
    al: date
    prezzo: float
    scaglione: int  # 1, 2 o 3; 0 per le quote fisse
    fissa: bool
    agevolata: bool  # IVA agevolata (10%) invece dell'ordinaria (22%)


@dataclass(frozen=True, slots=True)
class PeriodoTariffario:
    """Prezzi per scaglione (€/Smc) e quota fissa (€/mese), IVA inclusa."""

    dal: date
    prezzo_sc1: float
    prezzo_sc2: float
    prezzo_sc3: float
    quota_fissa: float


@dataclass(slots=True)
class Bolletta:
    """Dati estratti da una bolletta gas Plenitude."""

    dal: date
    al: date
    pdr: str | None = None
    pod: str | None = None
    coefficiente: float | None = None
    consumo_smc: int | None = None
    consumo_annuo_smc: int | None = None
    importo: float | None = None
    scadenza: date | None = None
    emissione: date | None = None
    voci: list[VoceCosto] = field(default_factory=list)


def _data(testo: str) -> date:
    return datetime.strptime(testo, "%d/%m/%Y").date()


def _ruolo_da_intestazione(etichetta: str) -> tuple[str, bool] | None:
    """Ruolo e flag "quota fissa" a partire dall'etichetta di una voce di dettaglio."""
    etichetta = re.sub(r"\s*\(\d+\)$", "", etichetta).strip()  # toglie "(3)", "(4)"
    if etichetta in _COMPONENTI:
        return _COMPONENTI[etichetta]
    if etichetta.startswith("Quota fissa"):
        return "quota_fissa", True
    if etichetta.startswith("Quota energia"):  # sotto "oneri generali di sistema"
        return "oneri", False
    return None


def _voci(testo: str) -> list[VoceCosto]:
    voci: list[VoceCosto] = []
    ruolo: str | None = None
    fissa = False
    for riga in testo.splitlines():
        intestazione = riga.strip()
        # riga di intestazione di una voce: "<etichetta> tipo prezzo ... regime iva"
        if intestazione.endswith("regime iva") and " tipo prezzo" in intestazione:
            trovato = _ruolo_da_intestazione(intestazione.split(" tipo prezzo", 1)[0])
            ruolo, fissa = trovato if trovato else (None, False)
            continue
        if intestazione.startswith(("Totale", "Spesa per", "Riepilogo")):
            ruolo = None
            continue
        m = _RE_DETTAGLIO.search(riga)
        if m is None or ruolo is None:
            continue
        dal, al, _unita, prezzo, mezzo, regime = m.groups()
        numeri = mezzo.split()
        # voci a scaglione: "smc scaglione importo" (3 numeri); senza: "smc/quantità importo"
        scaglione = int(numeri[1]) if len(numeri) == 3 else 0
        voci.append(
            VoceCosto(
                ruolo=ruolo,
                dal=_data(dal),
                al=_data(al),
                prezzo=_numero(prezzo),
                scaglione=scaglione,
                fissa=fissa,
                agevolata=regime == "AGE",
            )
        )
    return voci


def analizza(testo: str) -> Bolletta | None:
    """Estrae i dati da una bolletta gas. None se non è una bolletta gas Plenitude leggibile."""
    periodo = _RE_PERIODO.search(testo)
    if periodo is None or "Elementi di dettaglio Gas" not in testo:
        return None
    bolletta = Bolletta(dal=_data(periodo.group(1)), al=_data(periodo.group(2)))
    if m := _RE_PDR.search(testo):
        bolletta.pdr = m.group(1)
    if m := _RE_POD.search(testo):
        bolletta.pod = m.group(1)
    if m := _RE_COEFF.search(testo):
        bolletta.coefficiente = _numero(m.group(1))
    if m := _RE_CONSUMO.search(testo):
        bolletta.consumo_smc = int(m.group(1))
    if m := _RE_TOTALE.search(testo):
        bolletta.importo = _numero(m.group(1))
    if m := _RE_CONSUMO_ANNO.search(testo):
        bolletta.consumo_annuo_smc = int(m.group(1))
    if m := _RE_SCADENZA.search(testo):
        bolletta.scadenza = _data(m.group(1))
    if m := _RE_EMISSIONE.search(testo):
        bolletta.emissione = _data(m.group(1))
    bolletta.voci = _voci(testo)
    return bolletta


# Voci uguali su tutti gli scaglioni (cambiano solo nel tempo); le altre variano anche
# per scaglione di consumo annuo (accisa e addizionale per legge, distribuzione e oneri
# per delibera ARERA).
_RUOLI_VARIABILI: tuple[str, ...] = (
    "materia",
    "contributo",
    "distribuzione",
    "trasporto",
    "oneri",
    "accisa",
    "addizionale",
)
_RUOLI_PIATTI: frozenset[str] = frozenset({"materia", "contributo", "trasporto"})


def _primo_del_mese(giorno: date) -> date:
    return date(giorno.year, giorno.month, 1)


def _mesi_coperti(dal: date, al: date) -> list[date]:
    mesi = []
    corrente = _primo_del_mese(dal)
    while corrente <= al:
        mesi.append(corrente)
        corrente = date(corrente.year + corrente.month // 12, corrente.month % 12 + 1, 1)
    return mesi


def periodi_tariffari(bollette: list[Bolletta]) -> list[PeriodoTariffario]:
    """Calcola un periodo tariffario per ogni mese presente nelle bollette.

    Ogni bolletta riporta le tariffe regolate solo per lo scaglione di consumo
    effettivamente raggiunto nel mese, quindi il prezzo dello scaglione *consumato* è
    sempre esatto. Per gli altri scaglioni si riusa l'ultimo valore noto (le tariffe
    regolate cambiano per trimestre); se uno scaglione non è mai comparso se ne stima
    il prezzo da quello noto più vicino. Materia prima e contributo variano ogni mese e
    sono uguali su tutti gli scaglioni.
    """
    voci = sorted((v for b in bollette for v in b.voci), key=lambda v: v.dal)
    quota_per_mese: dict[date, float] = {}
    variabili_per_mese: dict[date, list[VoceCosto]] = {}
    for v in voci:
        if v.fissa:
            iva = IVA_AGEVOLATA if v.agevolata else IVA_ORDINARIA
            for mese in _mesi_coperti(v.dal, v.al):
                quota_per_mese[mese] = quota_per_mese.get(mese, 0.0) + v.prezzo * iva
        else:
            variabili_per_mese.setdefault(_primo_del_mese(v.dal), []).append(v)

    noti: dict[tuple[str, int], float] = {}
    periodi: dict[date, PeriodoTariffario] = {}
    for mese in sorted(set(variabili_per_mese) | set(quota_per_mese)):
        for v in variabili_per_mese.get(mese, []):
            scaglione = 0 if v.ruolo in _RUOLI_PIATTI else v.scaglione
            noti[(v.ruolo, scaglione)] = v.prezzo

        def tariffa(ruolo: str, scaglione: int) -> float:
            chiave = 0 if ruolo in _RUOLI_PIATTI else scaglione
            if (ruolo, chiave) in noti:
                return noti[(ruolo, chiave)]
            # scaglione mai visto: si stima da quello noto più vicino
            vicini = sorted(
                (s for (r, s) in noti if r == ruolo), key=lambda s: (abs(s - scaglione), s)
            )
            return noti[(ruolo, vicini[0])] if vicini else 0.0

        def variabile(scaglione: int) -> float:
            return IVA_AGEVOLATA * sum(tariffa(r, scaglione) for r in _RUOLI_VARIABILI)

        sc1 = round(variabile(1), 6)
        sc2 = round(variabile(2), 6)
        # oltre 480 Smc/anno le tariffe restano quelle del 2° scaglione ma l'IVA è al 22%
        sc3 = round(variabile(2) / IVA_AGEVOLATA * IVA_ORDINARIA, 6)
        periodi[mese] = PeriodoTariffario(
            mese, sc1, sc2, sc3, round(quota_per_mese.get(mese, 0.0), 4)
        )

    return [periodi[m] for m in sorted(periodi)]
