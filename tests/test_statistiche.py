"""Test dell'importazione dello storico dei consumi nelle statistiche."""

from __future__ import annotations

from datetime import UTC, date, datetime
import itertools

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.plenitude import statistiche
from custom_components.plenitude.account import BollettaConto, ConsumoMese, Conto
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


async def test_spese_mensili(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """La spesa di ogni mese diventa una statistica in euro, per conto e totale."""
    from custom_components.plenitude.bollette import PeriodoTariffario

    gas = _conto({(2026, 6): 12, (2026, 7): 8})
    gas.bollette = [BollettaConto("1", date(2026, 8, 1), None, 63.32, 0.0, None, None)]
    gas.periodi_bollette = {"1": (date(2026, 6, 1), date(2026, 7, 31))}
    gas.periodi = [PeriodoTariffario(date(2026, m, 1), 1, 1, 1, 17.6) for m in (6, 7)]
    await statistiche.importa_spese(hass, {"g": gas}, "UID123")
    await async_wait_recording_done(hass)
    for statistic_id in (statistiche.id_statistica_spesa(gas), "plenitude:spesa_totale_uid123"):
        righe = await hass.async_add_executor_job(
            statistics_during_period,
            hass,
            datetime(2000, 1, 1, tzinfo=UTC),
            None,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        assert [r["sum"] for r in righe[statistic_id]][-1] == pytest.approx(63.32, abs=0.01)
    metadati = await hass.async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids={statistiche.id_statistica_spesa(gas)})
    )
    assert metadati[statistiche.id_statistica_spesa(gas)][1]["unit_of_measurement"] == "EUR"


async def test_mese_arrivato_in_ritardo(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Un mese mancante che arriva dopo (es. bolletta scaricata in ritardo) entra nella somma."""
    await statistiche.importa_consumi(
        hass, {"g": _conto({(2026, 1): 10, (2026, 2): 10, (2026, 5): 10, (2026, 6): 10})}
    )
    await statistiche.importa_consumi(hass, {"g": _conto({(2026, m): 10 for m in range(1, 7)})})
    somme = await _somme(hass)
    assert somme[-1] == ("2026-06", 60)
    assert ("2026-03", 30) in somme


async def test_mese_tolto_non_fa_scendere(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Un mese registrato che sparisce dalla fonte vale zero: la somma non scende mai."""
    await statistiche.importa_consumi(hass, {"g": _conto({(2026, m): 10 for m in range(1, 7)})})
    valori = {(2026, m): 10 for m in range(1, 7)}
    del valori[(2026, 4)]
    valori[(2026, 5)] = 1
    await statistiche.importa_consumi(hass, {"g": _conto(valori)})
    somme = [s for _, s in await _somme(hass)]
    assert all(b >= a for a, b in itertools.pairwise(somme))


def _conto_pannello(quarti: list[tuple[str, float | None, float | None]]) -> Conto:
    """Conto luce con un pannello e i quarti d'ora indicati (ora italiana, Wh)."""
    from custom_components.plenitude import pannello

    conto = Conto(codice="500000000002", commodity="luce")
    conto.pannello = pannello.DatiPannello()
    pannello.unisci(conto.pannello, pannello.quarti_da_righe([[o, p, c] for o, p, c in quarti]))
    return conto


async def _somme_orarie(hass: HomeAssistant, statistic_id: str) -> list[tuple[str, float]]:
    await async_wait_recording_done(hass)
    righe = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2000, 1, 1, tzinfo=UTC),
        None,
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    return [
        (datetime.fromtimestamp(r["start"], UTC).strftime("%d %H"), round(r["sum"], 4))
        for r in righe.get(statistic_id, [])
    ]


async def test_pannello_orario(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Statistiche orarie dall'attivazione; i consumi arrivati dopo si aggiungono."""
    produzione = "plenitude:pannello_produzione_500000000002"
    autoconsumo = "plenitude:pannello_autoconsumo_500000000002"
    prelievo = "plenitude:pannello_prelievo_500000000002"
    risparmio = "plenitude:pannello_risparmio_500000000002"
    # 12:00 e 13:00 del 24/10 in Italia = 10 e 11 UTC; consumi solo per la prima ora
    quarti = [
        ("2026-10-24T11:45:00", None, 100.0),  # prima dell'attivazione
        *((f"2026-10-24T12:{m:02d}:00", 200.0, 100.0) for m in (0, 15, 30, 45)),
        *((f"2026-10-24T13:{m:02d}:00", 100.0, None) for m in (0, 15, 30, 45)),
    ]
    conto = _conto_pannello(quarti)
    await statistiche.importa_pannello(hass, {"l": conto}, 0.2)
    assert await _somme_orarie(hass, produzione) == [("24 10", 0.8), ("24 11", 1.2)]
    assert await _somme_orarie(hass, autoconsumo) == [("24 10", 0.4)]
    assert await _somme_orarie(hass, prelievo) == [("24 10", 0.0)]
    assert await _somme_orarie(hass, risparmio) == [("24 10", round(0.4 * 0.2 * 1.1, 4))]
    metadati = await hass.async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids={produzione, risparmio})
    )
    assert metadati[produzione][1]["unit_of_measurement"] == "kWh"
    assert metadati[risparmio][1]["unit_of_measurement"] == "EUR"

    # arrivano i consumi delle 13: l'ora si aggiunge, la precedente resta
    quarti[5:] = [(f"2026-10-24T13:{m:02d}:00", 100.0, 150.0) for m in (0, 15, 30, 45)]
    await statistiche.importa_pannello(hass, {"l": _conto_pannello(quarti)}, 0.2)
    assert await _somme_orarie(hass, autoconsumo) == [("24 10", 0.4), ("24 11", 0.8)]
    assert await _somme_orarie(hass, prelievo) == [("24 10", 0.0), ("24 11", 0.2)]
