"""Storico dei consumi mensili come statistiche esterne, utilizzabili nel pannello Energia.

L'area clienti riporta i consumi mese per mese (dall'attivazione della fornitura): ogni
mese diventa un valore orario all'inizio del mese, con la somma progressiva. Gli ultimi
mesi vengono reimportati a ogni aggiornamento perché il mese in corso cresce e i consumi
stimati vengono rettificati dalle letture successive.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.models import (
        StatisticData,
        StatisticMeanType,
        StatisticMetaData,
    )
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
        get_last_statistics,
        statistics_during_period,
    )

    statistic_id = id_statistica(conto)
    mesi = [(_inizio_mese(c.mese), c.valore) for c in conto.consumi]
    recorder = get_instance(hass)

    ultima = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    primo = 0
    if righe := ultima.get(statistic_id):
        ultimo_inizio = datetime.fromtimestamp(righe[0]["start"], UTC)
        nuovi = next((i for i, (inizio, _) in enumerate(mesi) if inizio > ultimo_inizio), len(mesi))
        primo = max(0, nuovi - MESI_DA_AGGIORNARE)
    somma = 0.0
    if righe:
        # somma progressiva registrata fino al mese precedente il primo da (re)importare
        precedenti = await recorder.async_add_executor_job(
            statistics_during_period,
            hass,
            _INIZIO_STORICO,
            mesi[primo][0],
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        if storico := precedenti.get(statistic_id):
            somma = storico[-1].get("sum") or 0.0

    dati: list[StatisticData] = []
    for inizio, valore in mesi[primo:]:
        somma = round(somma + valore, 3)
        dati.append(StatisticData(start=inizio, sum=somma))
    if righe and datetime.fromtimestamp(righe[0]["start"], UTC) > mesi[-1][0]:
        # mesi già registrati che l'area clienti non riporta più (es. un mese parziale
        # tolto): restano a consumo zero, così la somma non torna mai indietro
        successivi = await recorder.async_add_executor_job(
            statistics_during_period,
            hass,
            mesi[-1][0] + timedelta(hours=1),
            None,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        dati.extend(
            StatisticData(start=datetime.fromtimestamp(r["start"], UTC), sum=somma)
            for r in successivi.get(statistic_id, [])
        )

    unita, classe = _UNITA[conto.commodity]
    nome = "luce" if conto.commodity == LUCE else "gas"
    metadati = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"Plenitude consumo {nome} {conto.codice}",
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=classe,
        unit_of_measurement=unita,
    )
    async_add_external_statistics(hass, metadati, dati)
