"""Lettura delle bollette Plenitude dal testo estratto dal PDF.

Dal testo si ricavano i dati della fornitura, i consumi e il dettaglio dei costi;
da questi si calcolano i periodi tariffari (prezzi per scaglione e quota fissa) che
l'integrazione MyItalgas usa per stimare la spesa del gas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Any

from .const import IVA_AGEVOLATA, IVA_ORDINARIA

# Versione della lettura dei PDF: va incrementata quando cambia `analizza`, così le
# bollette già lette e salvate nella cache vengono rilette.
VERSIONE_LETTURA = 8

# Scaglioni annui di consumo (Smc dal 1° gennaio): fino a 120, 121-480, oltre 480.
SOGLIE_SCAGLIONI: tuple[float, float] = (120.0, 480.0)

_RE_PERIODO = re.compile(
    r"Periodo di fatturazione:\s*dal\s+(\d{2}/\d{2}/\d{4})\s+al\s+(\d{2}/\d{2}/\d{4})"
)
# Formato delle bollette fino all'inizio del 2025: "Periodo di riferimento" su più righe,
# con le date in lettere ("da 1 Aprile 2025" / "a 31 Maggio 2025").
_RE_PERIODO_LETTERE = re.compile(
    r"Periodo di riferimento[^\n]*\n\s*da\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})[^\n]*\n"
    r"\s*a\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"
)
_RE_DETTAGLIO_GAS = re.compile(r"Elementi di dettaglio gas", re.I)
_RE_PDR = re.compile(r"\bPDR\b.*?(\d{14})", re.S)
_RE_POD = re.compile(r"\bPOD\b.*?(IT\d{3}E\d{8})", re.S)
_RE_COEFF = re.compile(r"[Cc]oefficiente (?:correttivo )?C\*?:\s*([\d,]+)")
# i numeri possono avere il punto delle migliaia ("1.451")
_RE_CONSUMO = re.compile(r"(?:Consumo fatturato\*?|Totale fornitura gas smc)\s+(\d[\d.]*)")
_RE_TOTALE = re.compile(
    r"(?:Totale da pagare per questa bolletta|Totale bolletta salvo conguaglio)\s+(-?\d[\d.,]*)"
)
# nel testo estratto alcuni PDF non hanno lo spazio prima della data ("emissione21/08/2025")
_RE_SCADENZA = re.compile(r"Totale da pagare entro il\s*(\d{2}/\d{2}/\d{4})")
_RE_EMISSIONE = re.compile(r"Data di emissione\s*(\d{2}/\d{2}/\d{4})")
_RE_EMISSIONE_PUNTI = re.compile(
    r"data di emissione numero cliente\s*\n\s*\d+\s+(\d{2})\.(\d{2})\.(\d{4})"
)
_RE_CONSUMO_ANNO = re.compile(r"In un anno hai consumato\s*(\d[\d.]*)\s*Smc", re.I)
_RE_DETTAGLIO = re.compile(
    r"DAL (\d{2}/\d{2}/\d{4}) AL (\d{2}/\d{2}/\d{4})\s+(€/\S+)\s+(-?[\d.,]+)\s+(.+?)\s+(AGE|ORD)\s*$"
)

# Riga di intestazione di una voce di dettaglio: "<etichetta> <colonne>". Le colonne sono
# "tipo prezzo prezzo smc scaglioni € regime iva" (o "... regime") nelle bollette recenti,
# "quantità €", "smc €" o "smc scaglioni €" in quelle fino all'inizio del 2025.
_RE_INTESTAZIONE = re.compile(
    r"(.+?)\s+(?:tipo prezzo\b.*\bregime(?: iva)?|quantità €|smc(?: scaglioni)? €)"
)

_MESI: dict[str, int] = {
    nome: numero
    for numero, nome in enumerate(
        (
            "gennaio",
            "febbraio",
            "marzo",
            "aprile",
            "maggio",
            "giugno",
            "luglio",
            "agosto",
            "settembre",
            "ottobre",
            "novembre",
            "dicembre",
        ),
        start=1,
    )
}

# Intestazioni delle voci di dettaglio -> (ruolo, è una quota fissa).
_COMPONENTI: dict[str, tuple[str, bool]] = {
    "Corrispettivo Gas Index": ("materia", False),
    "Corrispettivo Luce Index": ("indice_luce", False),
    "Dispacciamento": ("dispacciamento", False),
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


def bolletta_a_dict(b: Bolletta) -> dict[str, Any]:
    """Bolletta in forma serializzabile (per la cache delle bollette già lette)."""
    return {
        "dal": b.dal.isoformat(),
        "al": b.al.isoformat(),
        "pdr": b.pdr,
        "pod": b.pod,
        "coefficiente": b.coefficiente,
        "consumo_smc": b.consumo_smc,
        "consumo_annuo_smc": b.consumo_annuo_smc,
        "importo": b.importo,
        "scadenza": b.scadenza.isoformat() if b.scadenza else None,
        "emissione": b.emissione.isoformat() if b.emissione else None,
        "voci": [
            [
                v.ruolo,
                v.dal.isoformat(),
                v.al.isoformat(),
                v.prezzo,
                v.scaglione,
                v.fissa,
                v.agevolata,
            ]
            for v in b.voci
        ],
    }


def bolletta_da_dict(dati: dict[str, Any]) -> Bolletta:
    """Bolletta dalla forma serializzata; solleva un'eccezione se i dati non sono validi."""

    def giorno(valore: str | None) -> date | None:
        return date.fromisoformat(valore) if valore else None

    return Bolletta(
        dal=date.fromisoformat(dati["dal"]),
        al=date.fromisoformat(dati["al"]),
        pdr=dati.get("pdr"),
        pod=dati.get("pod"),
        coefficiente=dati.get("coefficiente"),
        consumo_smc=dati.get("consumo_smc"),
        consumo_annuo_smc=dati.get("consumo_annuo_smc"),
        importo=dati.get("importo"),
        scadenza=giorno(dati.get("scadenza")),
        emissione=giorno(dati.get("emissione")),
        voci=[
            VoceCosto(
                ruolo=r,
                dal=date.fromisoformat(d),
                al=date.fromisoformat(a),
                prezzo=float(p),
                scaglione=int(sc),
                fissa=bool(f),
                agevolata=bool(ag),
            )
            for r, d, a, p, sc, f, ag in dati["voci"]
        ],
    )


def _data(testo: str) -> date:
    return datetime.strptime(testo, "%d/%m/%Y").date()


def _data_o_none(testo: str) -> date | None:
    """Data "gg/mm/aaaa", o None se impossibile (es. 31/02)."""
    try:
        return _data(testo)
    except ValueError:
        return None


def _data_valida(anno: int, mese: int, giorno: int) -> date | None:
    try:
        return date(anno, mese, giorno)
    except ValueError:
        return None


def periodo_fatturazione(testo: str) -> tuple[date, date] | None:
    """Periodo di fatturazione di una bolletta Plenitude, di luce o di gas."""
    return _periodo(testo)


def _periodo(testo: str) -> tuple[date, date] | None:
    """Periodo di fatturazione, nel formato attuale o in quello fino all'inizio del 2025."""
    if m := _RE_PERIODO.search(testo):
        dal, al = _data_o_none(m.group(1)), _data_o_none(m.group(2))
        return (dal, al) if dal is not None and al is not None and dal <= al else None
    if m := _RE_PERIODO_LETTERE.search(testo):
        g1, m1, a1, g2, m2, a2 = m.groups()
        mese1, mese2 = _MESI.get(m1.lower()), _MESI.get(m2.lower())
        if mese1 is None or mese2 is None:
            return None
        dal = _data_valida(int(a1), mese1, int(g1))
        al = _data_valida(int(a2), mese2, int(g2))
        if dal is not None and al is not None and dal <= al:
            return dal, al
    return None


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
        if m := _RE_INTESTAZIONE.fullmatch(intestazione):
            trovato = _ruolo_da_intestazione(m.group(1))
            ruolo, fissa = trovato if trovato else (None, False)
            continue
        if intestazione.startswith(("Totale", "Spesa per", "Riepilogo")):
            ruolo = None
            continue
        m = _RE_DETTAGLIO.search(riga)
        if m is None or ruolo is None:
            continue
        dal_testo, al_testo, _unita, prezzo, mezzo, regime = m.groups()
        dal, al = _data_o_none(dal_testo), _data_o_none(al_testo)
        if dal is None or al is None or dal > al:
            continue
        numeri = mezzo.split()
        # voci a scaglione: "smc scaglione importo" (3 numeri); senza: "smc/quantità importo"
        scaglione = int(numeri[1]) if len(numeri) == 3 else 0
        voci.append(
            VoceCosto(
                ruolo=ruolo,
                dal=dal,
                al=al,
                prezzo=_numero(prezzo),
                scaglione=scaglione,
                fissa=fissa,
                agevolata=regime == "AGE",
            )
        )
    return voci


def analizza(testo: str) -> Bolletta | None:
    """Estrae i dati da una bolletta gas. None se non è una bolletta gas Plenitude leggibile."""
    periodo = _periodo(testo)
    if periodo is None or not _RE_DETTAGLIO_GAS.search(testo):
        return None
    bolletta = Bolletta(dal=periodo[0], al=periodo[1])
    if m := _RE_PDR.search(testo):
        bolletta.pdr = m.group(1)
    if m := _RE_POD.search(testo):
        bolletta.pod = m.group(1)
    if m := _RE_COEFF.search(testo):
        bolletta.coefficiente = _numero(m.group(1))
    if m := _RE_CONSUMO.search(testo):
        bolletta.consumo_smc = int(_numero(m.group(1)))
    if m := _RE_TOTALE.search(testo):
        bolletta.importo = _numero(m.group(1))
    if m := _RE_CONSUMO_ANNO.search(testo):
        bolletta.consumo_annuo_smc = int(_numero(m.group(1)))
    bolletta.emissione, bolletta.scadenza = _emissione_e_scadenza(testo)
    bolletta.voci = _voci(testo)
    return bolletta


def _emissione_e_scadenza(testo: str) -> tuple[date | None, date | None]:
    emissione: date | None = None
    if m := _RE_EMISSIONE.search(testo):
        emissione = _data(m.group(1))
    elif m := _RE_EMISSIONE_PUNTI.search(testo):
        emissione = _data_valida(int(m[3]), int(m[2]), int(m[1]))
    # le pagine informative possono riportare una scadenza d'esempio: si sceglie la prima
    # non precedente all'emissione
    scadenze = [
        d for m in _RE_SCADENZA.finditer(testo) if (d := _data_o_none(m.group(1))) is not None
    ]
    if emissione is not None:
        scadenze = [d for d in scadenze if d >= emissione] or scadenze
    return emissione, (scadenze[0] if scadenze else None)


# --- Bollette luce -------------------------------------------------------------------

_RE_DETTAGLIO_LUCE = re.compile(r"Elementi di dettaglio luce", re.I)
# "Consumo fatturato 708 0 0 708" (fasce e totale) oppure "Consumo fatturato 469"
_RE_CONSUMO_LUCE = re.compile(r"Consumo fatturato((?:[ \t]+\d[\d.]*)+)")
_RE_CONSUMO_ANNO_LUCE = re.compile(r"In un anno hai consumato\s*([\d.]+)\s*kWh", re.I)
_RE_POTENZA = re.compile(r"(\d+(?:,\d+)?)\s*kW\s+(\d+(?:,\d+)?)\s*kW\b")
# riepilogo: "QUOTA PER CONSUMI [708 kWh] 0,326864 231,42 €" (i kWh a volte mancano)
_RE_QUOTA_CONSUMI = re.compile(
    r"QUOTA PER CONSUMI\s+(?:([\d.]+)\s*kWh\s+)?(\d+,\d+)\s+(-?[\d.,]+)\s*€"
)
_RE_QUOTA_FISSA = re.compile(r"QUOTA FISSA\s+(?:\d+\s+mes[ei]\s+)?([\d,]+)")
_RE_QUOTA_POTENZA = re.compile(r"QUOTA POTENZA\s+(?:[\d,]+\s*kW\s*x\s*\d+\s+mes[ei]\s+)?(\d+,\d+)")
# formato fino all'inizio del 2025
_RE_CONSUMO_LUCE_2024 = re.compile(r"consumo rilevato \([^)]*\)\s+(\d[\d.]*)")
_RE_POTENZA_2024 = re.compile(r"potenza impegnata:\s*(\d+(?:,\d+)?)\s*kW")
# totale della fornitura di luce, senza il canone RAI e le altre partite
_RE_TOTALE_FORNITURA_LUCE = re.compile(
    r"(?:Totale fornitura di luce e imposte|Totale bolletta salvo ricalcolo)\s+(-?\d[\d.,]*)\s*€",
    re.I,
)
_RE_CANONE_RAI = re.compile(
    r"Totale canone di abbonamento alla televisione per uso privato\s+(-?[\d.,]+)\s*€"
)
_RE_IMPOSTE_LUCE = re.compile(r"Totale Imposte\s+(-?[\d.,]+)\s*€")
_RE_LETTURA_LUCE = re.compile(
    r"(?:Rilevat[ao]|Stimat[ao]|Autolettura)[^\n]*?(\d{2}/\d{2}/\d{4})\s+(\d+)\s+(\d+)\s+(\d+)"
)


@dataclass(slots=True)
class BollettaLuce:
    """Dati estratti da una bolletta luce Plenitude."""

    dal: date
    al: date
    emissione: date | None = None
    scadenza: date | None = None
    importo: float | None = None
    totale_fornitura: float | None = None
    consumo_kwh: float | None = None
    consumo_annuo_kwh: float | None = None
    potenza_impegnata: float | None = None
    potenza_disponibile: float | None = None
    # riepilogo "quota per consumi" (IVA esclusa): kWh, €/kWh medio, importo
    consumo_quota_kwh: float | None = None
    prezzo_quota_consumi: float | None = None
    importo_quota_consumi: float | None = None
    quota_fissa_mese: float | None = None
    quota_potenza_kw_mese: float | None = None
    canone_rai: float | None = None
    imposte: float | None = None
    data_lettura: date | None = None
    letture: dict[str, int] = field(default_factory=dict)
    # indice della materia prima (PUN) applicato in ogni mese, €/kWh
    indici: dict[date, float] = field(default_factory=dict)
    # spread ("contributo al consumo") e dispacciamento dell'ultimo mese, €/kWh
    contributo: float | None = None
    dispacciamento: float | None = None
    # Corrispettivo Luce di ogni periodo, perdite di rete incluse e al netto degli sconti
    # sul corrispettivo, IVA esclusa: (dal, al, €/kWh). È il valore dell'energia
    # autoconsumata con "Adotta un pannello".
    corrispettivi: list[tuple[date, date, float]] = field(default_factory=list)

    @property
    def prezzo_energia(self) -> float | None:
        """€/kWh dei consumi con accise e IVA, senza quote fisse, quota potenza e canone."""
        kwh = self.consumo_quota_kwh or self.consumo_kwh
        if not self.importo_quota_consumi or not kwh:
            return None
        variabile = self.importo_quota_consumi + (self.imposte or 0.0)
        return round(variabile * IVA_AGEVOLATA / kwh, 4)

    @property
    def prezzo_medio(self) -> float | None:
        """€/kWh medi della fornitura: consumi, accise, quota fissa e quota potenza, con IVA.

        Si calcola dalle voci di costo e non dal totale, che con un conguaglio può
        comprendere rettifiche di periodi precedenti; il canone RAI è escluso.
        """
        variabile = self.prezzo_energia
        kwh = self.consumo_quota_kwh or self.consumo_kwh
        if variabile is None or not kwh:
            return None
        mesi = len(_mesi_coperti(self.dal, self.al)) or 1
        fissa = (self.quota_fissa_mese or 0.0) + (self.quota_potenza_kw_mese or 0.0) * (
            self.potenza_impegnata or 0.0
        )
        return round(variabile + fissa * IVA_AGEVOLATA * mesi / kwh, 4)

    def quota_fissa_mensile(self) -> float:
        """Parte fissa di ogni mese: quota fissa e quota potenza con IVA, più il canone RAI."""
        mesi = len(_mesi_coperti(self.dal, self.al)) or 1
        fissa = (self.quota_fissa_mese or 0.0) + (self.quota_potenza_kw_mese or 0.0) * (
            self.potenza_impegnata or 0.0
        )
        return fissa * IVA_AGEVOLATA + (self.canone_rai or 0.0) / mesi

    @property
    def lettura_totale(self) -> int | None:
        """Lettura del contatore: somma delle tre fasce."""
        return sum(self.letture.values()) if self.letture else None


def analizza_luce(testo: str) -> BollettaLuce | None:
    """Estrae i dati da una bolletta luce. None se non è una bolletta luce Plenitude."""
    periodo = _periodo(testo)
    if periodo is None or not _RE_DETTAGLIO_LUCE.search(testo):
        return None
    b = BollettaLuce(dal=periodo[0], al=periodo[1])
    b.emissione, b.scadenza = _emissione_e_scadenza(testo)
    if m := _RE_TOTALE.search(testo):
        b.importo = _numero(m.group(1))
    if m := _RE_TOTALE_FORNITURA_LUCE.search(testo):
        b.totale_fornitura = _numero(m.group(1))
    if m := _RE_CONSUMO_LUCE.search(testo):
        b.consumo_kwh = _numero(m.group(1).split()[-1])
    if m := _RE_CONSUMO_ANNO_LUCE.search(testo):
        b.consumo_annuo_kwh = _numero(m.group(1))
    if m := _RE_POTENZA.search(testo):
        b.potenza_impegnata, b.potenza_disponibile = _numero(m[1]), _numero(m[2])
    if b.consumo_kwh is None and (m := _RE_CONSUMO_LUCE_2024.search(testo)):
        b.consumo_kwh = _numero(m[1])
    if b.potenza_impegnata is None and (m := _RE_POTENZA_2024.search(testo)):
        b.potenza_impegnata = _numero(m[1])
    if m := _RE_QUOTA_CONSUMI.search(testo):
        b.consumo_quota_kwh = _numero(m[1]) if m[1] else None
        b.prezzo_quota_consumi = _numero(m[2])
        b.importo_quota_consumi = _numero(m[3])
    if m := _RE_QUOTA_FISSA.search(testo):
        b.quota_fissa_mese = _numero(m[1])
    if m := _RE_QUOTA_POTENZA.search(testo):
        b.quota_potenza_kw_mese = _numero(m[1])
    if m := _RE_IMPOSTE_LUCE.search(testo):
        b.imposte = _numero(m[1])
    if m := _RE_CANONE_RAI.search(testo):
        b.canone_rai = _numero(m[1])
    voci = sorted(_voci(testo), key=lambda v: v.dal)
    b.indici = {_primo_del_mese(v.dal): v.prezzo for v in voci if v.ruolo == "indice_luce"}
    for v in voci:
        if v.ruolo == "contributo":
            b.contributo = v.prezzo
        elif v.ruolo == "dispacciamento":
            b.dispacciamento = v.prezzo
    b.corrispettivi = _corrispettivi_luce(testo)
    letture = [
        (d, m) for m in _RE_LETTURA_LUCE.finditer(testo) if (d := _data_o_none(m[1])) is not None
    ]
    if letture:
        giorno, m = max(letture, key=lambda x: x[0])
        b.data_lettura = giorno
        b.letture = {"F1": int(m[2]), "F2": int(m[3]), "F3": int(m[4])}
    return b


def _corrispettivi_luce(testo: str) -> list[tuple[date, date, float]]:
    """Corrispettivo Luce effettivo di ogni periodo, dalle righe di dettaglio della vendita.

    Si sommano gli importi del corrispettivo, delle perdite di rete sul corrispettivo e
    degli sconti a consumo (€/kWh) e si dividono per i kWh consumati: è quanto si paga
    per ogni kWh di Corrispettivo Luce, comprese le perdite e al netto degli sconti.
    """
    importi: dict[tuple[date, date], list[float]] = {}  # periodo -> [kWh, €]
    tipo: str | None = None
    for riga in testo.splitlines():
        intestazione = riga.strip()
        if m := _RE_INTESTAZIONE.fullmatch(intestazione):
            etichetta = re.sub(r"\s*\(\d+\)$", "", m.group(1)).strip()
            if etichetta.startswith("Corrispettivo Luce"):
                tipo = "corrispettivo"
            elif etichetta.startswith("Perdite su Corrispettivo Luce"):
                tipo = "perdite"
            elif etichetta.startswith("Sconto"):
                tipo = "sconto"
            else:
                tipo = None
            continue
        if intestazione.startswith(("Totale", "Spesa per", "Riepilogo")):
            tipo = None
            continue
        m = _RE_DETTAGLIO.search(riga)
        if m is None or tipo is None:
            continue
        dal, al = _data_o_none(m[1]), _data_o_none(m[2])
        numeri = m[5].split()
        if dal is None or al is None or m[3] != "€/kWh" or len(numeri) != 2:
            continue
        voce = importi.setdefault((dal, al), [0.0, 0.0])
        if tipo == "corrispettivo":
            voce[0] += _numero(numeri[0])
        voce[1] += _numero(numeri[1])
    return [
        (dal, al, round(euro / kwh, 6))
        for (dal, al), (kwh, euro) in sorted(importi.items())
        if kwh > 0
    ]


def bolletta_luce_a_dict(b: BollettaLuce) -> dict[str, Any]:
    """Bolletta luce in forma serializzabile (per la cache delle bollette già lette)."""
    dati = {nome: getattr(b, nome) for nome in BollettaLuce.__slots__}
    for nome in ("dal", "al", "emissione", "scadenza", "data_lettura"):
        dati[nome] = dati[nome].isoformat() if dati[nome] else None
    dati["indici"] = {mese.isoformat(): valore for mese, valore in b.indici.items()}
    dati["corrispettivi"] = [[dal.isoformat(), al.isoformat(), v] for dal, al, v in b.corrispettivi]
    return dati


def bolletta_luce_da_dict(dati: dict[str, Any]) -> BollettaLuce:
    """Bolletta luce dalla forma serializzata."""
    valori = {nome: dati.get(nome) for nome in BollettaLuce.__slots__}
    for nome in ("dal", "al", "emissione", "scadenza", "data_lettura"):
        valori[nome] = date.fromisoformat(valori[nome]) if valori[nome] else None
    if valori["dal"] is None or valori["al"] is None:
        raise ValueError("periodo assente")
    valori["letture"] = {k: int(v) for k, v in (valori["letture"] or {}).items()}
    valori["indici"] = {
        date.fromisoformat(mese): float(valore) for mese, valore in (valori["indici"] or {}).items()
    }
    valori["corrispettivi"] = [
        (date.fromisoformat(dal), date.fromisoformat(al), float(v))
        for dal, al, v in valori["corrispettivi"] or []
    ]
    return BollettaLuce(**valori)


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


def _frazione_del_mese(mese: date, dal: date, al: date) -> float:
    """Frazione dei giorni del mese compresi tra dal e al (estremi inclusi)."""
    fine_mese = date(mese.year + mese.month // 12, mese.month % 12 + 1, 1)
    giorni_mese = (fine_mese - mese).days
    inizio, fine = max(dal, mese), min(al, date.fromordinal(fine_mese.toordinal() - 1))
    return max(0, (fine - inizio).days + 1) / giorni_mese


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
    # Una bolletta può rifatturare mesi già fatturati (conguaglio con le letture reali,
    # stornando gli acconti): per ogni mese valgono le voci della bolletta più recente.
    # (le rettifiche emesse dopo valgono anche se riguardano un periodo precedente)
    ordinate = sorted(bollette, key=lambda b: (b.emissione or b.al, b.al))
    quota_per_mese: dict[date, float] = {}
    for b in ordinate:
        quote_bolletta: dict[date, float] = {}
        for v in b.voci:
            if v.fissa:
                iva = IVA_AGEVOLATA if v.agevolata else IVA_ORDINARIA
                # il prezzo è mensile: un mese coperto solo in parte (o diviso su più
                # righe) conta per la frazione di giorni coperti
                for mese in _mesi_coperti(v.dal, v.al):
                    quota = v.prezzo * iva * _frazione_del_mese(mese, v.dal, v.al)
                    quote_bolletta[mese] = quote_bolletta.get(mese, 0.0) + quota
        quota_per_mese.update(quote_bolletta)

    voci = sorted((v for b in ordinate for v in b.voci), key=lambda v: v.dal)
    variabili_per_mese: dict[date, list[VoceCosto]] = {}
    for v in voci:
        if not v.fissa:
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
