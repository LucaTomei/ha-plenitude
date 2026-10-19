"""Test dell'importazione dello storico dei consumi nelle statistiche."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.plenitude import statistiche
from custom_components.plenitude.account import ConsumoMese, Conto
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant

ID_GAS = "plenitude:consumo_gas_500000000001"


# Il recorder va preparato prima di Home Assistant: si sostituiscono le fixture automatiche
# di conftest (che usano `hass`) con versioni che chiedono prima il recorder.
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_db_url: str) -> None:
    """Database del recorder pronto prima di `hass`."""


@pytest.fixture(autouse=True)
def _cartella_pulita() -> None:
    """Questi test non usano cartelle."""


def _conto(consumi: dict[tuple[int, int], float], parziale: tuple[int, int] | None = None) -> Conto:
    conto = Conto(codice="500000000001", commodity="gas")
    conto.consumi = [
        ConsumoMese(mese=date(a, m, 1), valore=v, fasce={}, parziale=(a, m) == parziale)
        for (a, m), v in sorted(consumi.items())
    ]
    return conto


async def _somme(hass: HomeAssistant) -> list[tuple[str, float]]:
    await async_wait_recording_done(hass)
    righe = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2000, 1, 1, tzinfo=UTC),
        None,
        {ID_GAS},
        "hour",
        None,
        {"sum"},
    )
    from homeassistant.util import dt as dt_util

    return [
        (dt_util.as_local(datetime.fromtimestamp(r["start"], UTC)).strftime("%Y-%m"), r["sum"])
        for r in righe.get(ID_GAS, [])
    ]


async def test_importazione_e_aggiornamento(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Somma progressiva, mese in corso aggiornato, storico più vecchio non alterato."""
    await statistiche.importa_consumi(
        hass, {"g": _conto({(2026, 1): 100, (2026, 2): 80, (2026, 3): 5}, parziale=(2026, 3))}
    )
    assert await _somme(hass) == [("2026-01", 100), ("2026-02", 180), ("2026-03", 185)]

    metadati = await hass.async_add_executor_job(lambda: get_metadata(hass, statistic_ids={ID_GAS}))
    assert metadati[ID_GAS][1]["unit_of_measurement"] == "m³"
    assert metadati[ID_GAS][1]["has_sum"]

    # il mese in corso cresce, arriva un mese nuovo e l'area clienti non riporta più gennaio
    await statistiche.importa_consumi(
        hass, {"g": _conto({(2026, 2): 80, (2026, 3): 20, (2026, 4): 10})}
    )
    assert await _somme(hass) == [
        ("2026-01", 100),
        ("2026-02", 180),
        ("2026-03", 200),
        ("2026-04", 210),
    ]

    # una nuova importazione identica non cambia nulla
    await statistiche.importa_consumi(
        hass, {"g": _conto({(2026, 2): 80, (2026, 3): 20, (2026, 4): 10})}
    )
    assert (await _somme(hass))[-1] == ("2026-04", 210)


async def test_luce_in_kwh(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """La luce è importata in kWh."""
    conto = Conto(codice="500000000002", commodity="luce")
    conto.consumi = [ConsumoMese(mese=date(2026, 9, 1), valore=240, fasce={}, parziale=False)]
    await statistiche.importa_consumi(hass, {"l": conto})
    await async_wait_recording_done(hass)
    id_luce = "plenitude:consumo_luce_500000000002"
    metadati = await hass.async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids={id_luce})
    )
    assert metadati[id_luce][1]["unit_of_measurement"] == "kWh"


async def test_senza_recorder(hass: HomeAssistant) -> None:
    """Senza recorder non si importa nulla e non si va in errore."""
    await statistiche.importa_consumi(hass, {"g": _conto({(2026, 1): 1})})


async def test_mese_non_piu_riportato(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Un mese registrato che l'area clienti non riporta più non fa tornare indietro la somma."""
    await statistiche.importa_consumi(
        hass, {"g": _conto({(2026, 1): 100, (2026, 2): 80, (2026, 3): 5}, parziale=(2026, 3))}
    )
    await statistiche.importa_consumi(hass, {"g": _conto({(2026, 1): 100, (2026, 2): 90})})
    assert await _somme(hass) == [("2026-01", 100), ("2026-02", 190), ("2026-03", 190)]


async def test_importazioni_ravvicinate(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Due importazioni una dopo l'altra, senza attese: la seconda vede la prima."""
    for _ in range(3):
        await statistiche.importa_consumi(
            hass, {"g": _conto({(2026, 1): 100, (2026, 2): 80, (2026, 3): 5})}
        )
        await statistiche.importa_consumi(
            hass, {"g": _conto({(2026, 2): 80, (2026, 3): 20, (2026, 4): 10})}
        )
    assert await _somme(hass) == [
        ("2026-01", 100),
        ("2026-02", 180),
        ("2026-03", 200),
        ("2026-04", 210),
    ]


async def test_dopo_un_riavvio(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Dopo un riavvio le somme già registrate si rileggono dal database e si prosegue."""
    await statistiche.importa_consumi(
        hass, {"g": _conto({(2026, 1): 100, (2026, 2): 80, (2026, 3): 5}, parziale=(2026, 3))}
    )
    await async_wait_recording_done(hass)
    hass.data.pop("plenitude_statistiche")  # come dopo un riavvio
    await statistiche.importa_consumi(
        hass, {"g": _conto({(2026, 2): 80, (2026, 3): 20, (2026, 4): 10})}
    )
    assert await _somme(hass) == [
        ("2026-01", 100),
        ("2026-02", 180),
        ("2026-03", 200),
        ("2026-04", 210),
    ]
