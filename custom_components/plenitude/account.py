"""Lettura dei dati dell'account Plenitude (risposte JSON dell'area clienti)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .bollette import Bolletta, PeriodoTariffario

GAS = "gas"
LUCE = "luce"

_COMMODITY: dict[str, str] = {"GAS": GAS, "POWER": LUCE, "LUCE": LUCE, "ELETTRICITA": LUCE}

# Fasce orarie dei consumi di energia elettrica, come le riporta l'app.
FASCE: tuple[str, ...] = ("F1", "F2", "F3")


@dataclass(frozen=True, slots=True)
class BollettaConto:
    """Una bolletta dell'elenco dell'area clienti."""

    numero: str
    emissione: date | None
    scadenza: date | None
    importo: float | None
    da_pagare: float
    stato_pagamento: str | None
    metodo_pagamento: str | None


@dataclass(frozen=True, slots=True)
class ConsumoMese:
    """Consumo di un mese (Smc per il gas, kWh per la luce)."""

    mese: date
    valore: float
    fasce: dict[str, float]
    parziale: bool


@dataclass(slots=True)
class Conto:
    """Un conto Plenitude: una fornitura di luce o gas con bollette e consumi."""

    codice: str
    commodity: str | None
    alias: str | None = None
    indirizzo: str | None = None
    stato: str | None = None
    pdr: str | None = None
    pod: str | None = None
    matricola: str | None = None
    prodotto: str | None = None
    bollette: list[BollettaConto] = field(default_factory=list)
    consumi: list[ConsumoMese] = field(default_factory=list)
    lettura: int | None = None
    data_lettura: date | None = None
    autolettura_dal: date | None = None
    autolettura_al: date | None = None
    # dati letti dai PDF delle bollette gas
    bollette_pdf: list[Bolletta] = field(default_factory=list)
    periodi: list[PeriodoTariffario] = field(default_factory=list)

    @property
    def ultima(self) -> BollettaConto | None:
        """Bolletta più recente."""
        return self.bollette[0] if self.bollette else None

    @property
    def totale_da_pagare(self) -> float:
        """Somma ancora da pagare su tutte le bollette."""
        return round(sum(b.da_pagare for b in self.bollette), 2)

    @property
    def da_pagare(self) -> list[BollettaConto]:
        """Bollette con un importo ancora da pagare, dalla scadenza più vicina."""
        return sorted(
            (b for b in self.bollette if b.da_pagare > 0),
            key=lambda b: b.scadenza or date.max,
        )

    @property
    def ultimo_consumo(self) -> ConsumoMese | None:
        """Consumo dell'ultimo mese completo (o dell'ultimo disponibile)."""
        completi = [c for c in self.consumi if not c.parziale]
        if completi:
            return completi[-1]
        return self.consumi[-1] if self.consumi else None

    @property
    def consumo_12_mesi(self) -> float | None:
        """Consumo degli ultimi 12 mesi completi."""
        completi = [c for c in self.consumi if not c.parziale]
        if not completi:
            return None
        return round(sum(c.valore for c in completi[-12:]), 3)

    @property
    def ultima_pdf(self) -> Bolletta | None:
        """Bolletta gas letta dal PDF più recente."""
        if not self.bollette_pdf:
            return None
        return max(self.bollette_pdf, key=lambda b: (b.emissione or b.al, b.al))


def _data(valore: Any) -> date | None:
    if not isinstance(valore, str) or not valore.strip():
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valore.strip()[:10], formato).date()
        except ValueError:
            continue
    return None


def _numero(valore: Any) -> float | None:
    if valore is None or isinstance(valore, bool):
        return None
    if isinstance(valore, int | float):
        return float(valore)
    testo = str(valore).strip().replace("€", "").strip()
    if not testo:
        return None
    if "," in testo:  # formato italiano "1.234,56"
        testo = testo.replace(".", "").replace(",", ".")
    try:
        return float(testo)
    except ValueError:
        return None


def _intero(valore: Any) -> int | None:
    numero = _numero(valore)
    return int(numero) if numero is not None else None


def commodity(valore: Any) -> str | None:
    """Tipo di fornitura normalizzato ("gas" o "luce")."""
    return _COMMODITY.get(str(valore or "").strip().upper())


def conto_da(dati: dict[str, Any]) -> Conto | None:
    """Conto dall'elenco dei conti; None per voci senza codice."""
    codice = str(dati.get("codice") or "").strip()
    if not codice:
        return None
    return Conto(
        codice=codice,
        commodity=commodity(dati.get("commodity")),
        alias=dati.get("alias"),
        indirizzo=dati.get("indirizzoFornitura"),
        stato=dati.get("tipo"),
    )


def aggiungi_fornitura(conto: Conto, forniture: list[dict[str, Any]]) -> None:
    """Completa il conto con i dati della (prima) fornitura attiva."""
    if not forniture:
        return
    attive = [f for f in forniture if str(f.get("status", "")).upper() == "ATTIVO"]
    fornitura = (attive or forniture)[0]
    conto.commodity = conto.commodity or commodity(fornitura.get("tipo"))
    conto.pdr = fornitura.get("pdr") or (
        fornitura.get("podPdr") if conto.commodity == GAS else None
    )
    conto.pod = fornitura.get("pod") or (
        fornitura.get("podPdr") if conto.commodity == LUCE else None
    )
    conto.matricola = fornitura.get("matricolaContatore")
    conto.prodotto = fornitura.get("prodotto")


def bollette_da(elenco: list[dict[str, Any]]) -> list[BollettaConto]:
    """Bollette dall'elenco dell'area clienti, dalla più recente."""
    bollette: list[BollettaConto] = []
    for voce in elenco:
        numero = str(voce.get("numeroBolletta") or "").strip()
        if not numero:
            continue
        bollette.append(
            BollettaConto(
                numero=numero,
                emissione=_data(voce.get("emissione")),
                scadenza=_data(voce.get("scadenza")),
                importo=_numero(voce.get("importo")),
                da_pagare=max(_numero(voce.get("importoPagare")) or 0.0, 0.0),
                stato_pagamento=voce.get("statoPagamento"),
                metodo_pagamento=voce.get("metodoPagamento"),
            )
        )
    bollette.sort(key=lambda b: (b.emissione or date.min, b.numero), reverse=True)
    return bollette


def consumi_da(forniture: list[dict[str, Any]], tipo: str | None) -> list[ConsumoMese]:
    """Consumi mensili, in ordine cronologico, della fornitura del tipo indicato."""
    scelta = next(
        (f for f in forniture if commodity(f.get("type")) == tipo),
        forniture[0] if len(forniture) == 1 else None,
    )
    if scelta is None:
        return []
    mesi: dict[date, ConsumoMese] = {}
    for voce in scelta.get("consumptions") or []:
        anno, mese = _intero(voce.get("year")), _intero(voce.get("numericalMonth"))
        if anno is None or mese is None or not 0 <= mese <= 11:
            continue
        fasce = {f: v for f in FASCE if (v := _numero(voce.get(f"consumptions{f}"))) is not None}
        totale = _numero(voce.get("consumptions"))
        if totale is None:
            if not fasce:
                continue
            totale = sum(fasce.values())
        giorno = date(anno, mese + 1, 1)
        mesi[giorno] = ConsumoMese(
            mese=giorno,
            valore=round(totale, 3),
            fasce=fasce,
            parziale=bool(voce.get("isPartial")),
        )
    return [mesi[m] for m in sorted(mesi)]


def aggiungi_autolettura(conto: Conto, dati: dict[str, Any] | None) -> None:
    """Ultima lettura del contatore e finestra della prossima autolettura."""
    if not dati:
        return
    conto.lettura = _intero(dati.get("valoreAutolettura"))
    conto.data_lettura = _data(dati.get("data"))
    conto.autolettura_dal = _data(dati.get("dataAutoletturaDa"))
    conto.autolettura_al = _data(dati.get("dataAutoletturaA"))
