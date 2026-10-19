"""Le statistiche dei consumi sono accettate dal pannello Energia di Home Assistant."""

from __future__ import annotations

from datetime import date

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.plenitude import statistiche
from custom_components.plenitude.account import ConsumoMese, Conto
from homeassistant.components.energy import data as energia, validate
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import get_metadata
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


# Il recorder va preparato prima di Home Assistant (vedi test_statistiche).
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_db_url: str) -> None:
    """Database del recorder pronto prima di `hass`."""


@pytest.fixture(autouse=True)
def _cartella_pulita() -> None:
    """Questi test non usano cartelle."""


def _conto(codice: str, commodity: str) -> Conto:
    conto = Conto(codice=codice, commodity=commodity)
    conto.consumi = [
        ConsumoMese(mese=date(2026, m, 1), valore=100 + m, fasce={}, parziale=False)
        for m in range(1, 7)
    ]
    return conto


def _sorgente_rete(statistic_id: str) -> dict:
    """Sorgente "rete" nel formato della versione di Home Assistant in uso."""
    if hasattr(energia, "_migrate_legacy_grid_to_unified"):
        return {
            "type": "grid",
            "stat_energy_from": statistic_id,
            "stat_energy_to": None,
            "stat_cost": None,
            "entity_energy_price": None,
            "number_energy_price": None,
            "stat_compensation": None,
            "entity_energy_price_export": None,
            "number_energy_price_export": None,
            "cost_adjustment_day": 0,
        }
    return {
        "type": "grid",
        "flow_from": [
            {
                "stat_energy_from": statistic_id,
                "stat_cost": None,
                "entity_energy_from": None,
                "entity_energy_price": None,
                "number_energy_price": None,
            }
        ],
        "flow_to": [],
        "cost_adjustment_day": 0,
    }


async def test_statistiche_valide_per_energia(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Luce come consumo dalla rete e gas come consumo di gas: nessun problema segnalato."""
    luce = _conto("500000000002", "luce")
    gas = _conto("500000000001", "gas")
    await statistiche.importa_consumi(hass, {"l": luce, "g": gas})
    await async_wait_recording_done(hass)

    assert await async_setup_component(hass, "energy", {})
    gestore = await energia.async_get_manager(hass)
    gestore.data = {
        "energy_sources": [
            _sorgente_rete(statistiche.id_statistica(luce)),
            {
                "type": "gas",
                "stat_energy_from": statistiche.id_statistica(gas),
                "stat_cost": None,
                "entity_energy_price": None,
                "number_energy_price": None,
            },
        ],
        "device_consumption": [],
    }
    esito = await validate.async_validate(hass)
    assert esito.as_dict()["energy_sources"] == [[], []]

    # il pannello propone le statistiche esterne in base alla classe di unità
    # (energia per la rete, volume o energia per il gas) e ne converte le unità
    metadati = await hass.async_add_executor_job(
        lambda: get_metadata(
            hass,
            statistic_ids={statistiche.id_statistica(luce), statistiche.id_statistica(gas)},
        )
    )
    meta_luce = metadati[statistiche.id_statistica(luce)][1]
    meta_gas = metadati[statistiche.id_statistica(gas)][1]
    assert meta_luce["has_sum"]
    assert meta_gas["has_sum"]
    assert meta_luce["unit_class"] == "energy"
    assert meta_luce["unit_of_measurement"] in validate.ENERGY_USAGE_UNITS["energy"]
    assert meta_gas["unit_class"] == "volume"
    assert meta_gas["unit_of_measurement"] in validate.GAS_USAGE_UNITS["gas"]


async def test_statistica_assente_segnalata(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Controprova: una statistica mai importata viene segnalata dalla validazione."""
    assert await async_setup_component(hass, "energy", {})
    gestore = await energia.async_get_manager(hass)
    gestore.data = {
        "energy_sources": [_sorgente_rete("plenitude:consumo_luce_000")],
        "device_consumption": [],
    }
    esito = (await validate.async_validate(hass)).as_dict()
    assert esito["energy_sources"][0][0]["type"] == "statistics_not_defined"
