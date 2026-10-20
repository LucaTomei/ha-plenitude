"""Storico dei consumi mensili come statistiche esterne, utilizzabili nel pannello Energia.

L'area clienti riporta i consumi mese per mese (dall'attivazione della fornitura): ogni
mese diventa un valore orario all'inizio del mese, con la somma progressiva. Gli ultimi
mesi vengono reimportati a ogni aggiornamento perché il mese in corso cresce e i consumi
stimati vengono rettificati dalle letture successive.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import logging

from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .account import GAS, LUCE, Conto
from .api import oscura
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Mesi già importati che si reimportano a ogni aggiornamento.
MESI_DA_AGGIORNARE = 3

_INIZIO_STORICO = datetime(2000, 1, 1, tzinfo=UTC)

# chiave in hass.data delle somme già importate (vedi `_registrate`)
_MEMORIA = f"{DOMAIN}_statistiche"

_UNITA: dict[str, tuple[str, str]] = {
    LUCE: (UnitOfEnergy.KILO_WATT_HOUR, "energy"),
    GAS: (UnitOfVolume.CUBIC_METERS, "volume"),
}


def id_statistica(conto: Conto) -> str:
    """Identificativo della statistica dei consumi di un conto."""
    return f"{DOMAIN}:consumo_{conto.commodity}_{conto.codice}".lower()


def _inizio_mese(mese: date) -> datetime:
    return dt_util.as_utc(
        datetime(mese.year, mese.month, 1, tzinfo=dt_util.get_default_time_zone())
    )


async def importa_consumi(hass: HomeAssistant, conti: dict[str, Conto]) -> None:
    """Importa i consumi mensili di tutti i conti (se il recorder è attivo)."""
    if "recorder" not in hass.config.components:
        return
    for conto in conti.values():
        if conto.commodity not in _UNITA or not conto.consumi:
            continue
        try:
            await _importa(hass, conto)
        except Exception:
            _LOGGER.exception(
                "Importazione dei consumi del conto %s non riuscita", oscura(conto.codice)
            )


async def _importa(hass: HomeAssistant, conto: Conto) -> None:
    unita, classe = _UNITA[conto.commodity]
    nome = "luce" if conto.commodity == LUCE else "gas"
    await _importa_serie(
        hass,
        id_statistica(conto),
        nome=f"Plenitude consumo {nome} {conto.codice}",
        unita=unita,
        classe=classe,
        valori=[(c.mese, c.valore) for c in conto.consumi],
    )


def id_statistica_spesa(conto: Conto) -> str:
    """Identificativo della statistica della spesa mensile di un conto."""
    return f"{DOMAIN}:spesa_{conto.commodity}_{conto.codice}".lower()


def id_statistica_spesa_totale(uid: str) -> str:
    """Identificativo della statistica della spesa mensile totale (luce + gas) dell'account."""
    return f"{DOMAIN}:spesa_totale_{uid}".lower()


def spese_totali(conti: dict[str, Conto]) -> dict[date, float]:
    """Spesa di luce e gas insieme, solo per i mesi già fatturati per tutte le forniture."""
    serie = [c.spese_mensili for c in conti.values() if c.commodity in _UNITA]
    serie = [s for s in serie if s]
    if not serie:
        return {}
    comuni = set.intersection(*(set(s) for s in serie))
    return {m: round(sum(s[m] for s in serie), 2) for m in sorted(comuni)}


async def importa_spese(hass: HomeAssistant, conti: dict[str, Conto], uid: str) -> None:
    """Importa la spesa mensile di ogni conto e quella totale (se il recorder è attivo)."""
    if "recorder" not in hass.config.components:
        return
    serie: list[tuple[str, str, dict[date, float]]] = [
        (
            id_statistica_spesa(c),
            f"Plenitude spesa {'luce' if c.commodity == LUCE else 'gas'} {c.codice}",
            c.spese_mensili,
        )
        for c in conti.values()
        if c.commodity in _UNITA
    ]
    serie.append((id_statistica_spesa_totale(uid), "Plenitude spesa totale", spese_totali(conti)))
    for statistic_id, nome, spese in serie:
        if not spese:
            continue
        try:
            await _importa_serie(
                hass,
                statistic_id,
                nome=nome,
                unita="EUR",
                classe=None,
                valori=list(spese.items()),
            )
        except Exception:
            _LOGGER.exception("Importazione della spesa %s non riuscita", oscura(statistic_id))


async def _importa_serie(
    hass: HomeAssistant,
    statistic_id: str,
    *,
    nome: str,
    unita: str,
    classe: str | None,
    valori: list[tuple[date, float]],
) -> None:
    """Importa una serie di valori mensili come somma progressiva."""
    from homeassistant.components.recorder.models import (
        StatisticData,
        StatisticMeanType,
        StatisticMetaData,
    )
    from homeassistant.components.recorder.statistics import async_add_external_statistics

    mesi = [(_inizio_mese(mese), valore) for mese, valore in valori]
    registrate = await _registrate(hass, statistic_id)

    primo = 0
    if registrate:
        ultimo_inizio = max(registrate)
        nuovi = next((i for i, (inizio, _) in enumerate(mesi) if inizio > ultimo_inizio), len(mesi))
        primo = max(0, nuovi - MESI_DA_AGGIORNARE)
    # somma progressiva registrata fino al mese precedente il primo da (re)importare
    precedenti = [inizio for inizio in registrate if inizio < mesi[primo][0]]
    somma = registrate[max(precedenti)] if precedenti else 0.0

    dati: list[StatisticData] = []
    for inizio, valore in mesi[primo:]:
        somma = round(somma + valore, 3)
        dati.append(StatisticData(start=inizio, sum=somma))
    # mesi già registrati che l'area clienti non riporta più (es. un mese parziale tolto):
    # restano a zero, così la somma non torna mai indietro
    dati.extend(
        StatisticData(start=inizio, sum=somma)
        for inizio in sorted(registrate)
        if inizio > mesi[-1][0]
    )
    registrate.update({d["start"]: d["sum"] for d in dati})

    metadati = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=nome,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=classe,
        unit_of_measurement=unita,
    )
    async_add_external_statistics(hass, metadati, dati)


async def _registrate(hass: HomeAssistant, statistic_id: str) -> dict[datetime, float]:
    """Somme progressive già importate, per inizio del mese.

    Si leggono dal database solo la prima volta; poi si tiene in memoria quello che è
    stato importato. Il recorder scrive in un secondo momento: rileggere dal database
    subito dopo un'importazione (due aggiornamenti ravvicinati) darebbe dati vecchi.
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    memoria: dict[str, dict[datetime, float]] = hass.data.setdefault(_MEMORIA, {})
    if statistic_id not in memoria:
        righe = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            _INIZIO_STORICO,
            None,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        memoria[statistic_id] = {
            datetime.fromtimestamp(r["start"], UTC): r.get("sum") or 0.0
            for r in righe.get(statistic_id, [])
        }
    return memoria[statistic_id]
