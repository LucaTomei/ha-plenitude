"""Test dell'integrazione Plenitude in Home Assistant."""

from __future__ import annotations

import pytest

from custom_components.plenitude.const import CONF_PDR_MYITALGAS, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er

from .conftest import crea_entry, prepara_cartella

REALI = [
    "bolletta-gas-202501022.txt",
    "bolletta-gas-20260219.txt",
    "bolletta-gas-20260706.txt",
    "bolletta-gas-20260828.txt",
]


def _stato(hass: HomeAssistant, chiave: str):
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    entity_id = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.unique_id.endswith(f"_{chiave}")
    )
    stato = hass.states.get(entity_id)
    assert stato is not None, chiave
    return stato


async def _avvia(hass: HomeAssistant, bollette: list[str], **opzioni):
    prepara_cartella(hass, bollette)
    entry = crea_entry(hass, **opzioni)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_sensori(hass: HomeAssistant) -> None:
    """Le bollette vengono lette e i sensori valorizzati con l'ultima."""
    await _avvia(hass, REALI)
    assert _stato(hass, "ultima_bolletta_importo").state == "63.32"
    assert _stato(hass, "ultima_bolletta_scadenza").state == "2026-09-22"
    assert _stato(hass, "ultima_bolletta_consumo").state == "20"
    assert _stato(hass, "consumo_annuo").state == "475"
    assert _stato(hass, "bollette_lette").state == "4"
    assert _stato(hass, "ultima_bolletta_importo").attributes["periodo_dal"] == "2026-06-01"


async def test_cartella_creata_automaticamente(hass: HomeAssistant) -> None:
    """Se la cartella non esiste viene creata e l'integrazione si avvia con zero bollette."""
    from pathlib import Path

    from .conftest import CARTELLA

    cartella = Path(hass.config.path(CARTELLA))
    assert not await hass.async_add_executor_job(cartella.exists)
    entry = crea_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.async_add_executor_job(cartella.is_dir)
    assert _stato(hass, "bollette_lette").state == "0"


async def test_cartella_vuota(hass: HomeAssistant) -> None:
    """Cartella esistente ma senza bollette: integrazione attiva, sensori sconosciuti."""
    await _avvia(hass, [])
    assert _stato(hass, "bollette_lette").state == "0"
    assert _stato(hass, "ultima_bolletta_importo").state == "unknown"


async def test_bolletta_illeggibile_ignorata(hass: HomeAssistant) -> None:
    """Un PDF non-bolletta viene ignorato senza far fallire l'aggiornamento."""
    await _avvia(hass, ["bolletta-gas-20260828.txt"])
    cartella = prepara_cartella(hass, [])
    (cartella / "spam.pdf").write_text("non è una bolletta")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.last_update_success
    assert _stato(hass, "bollette_lette").state == "1"


async def test_invio_periodi_a_myitalgas(hass: HomeAssistant) -> None:
    """Con una fornitura MyItalgas collegata, i periodi tariffari vengono inviati."""
    chiamate: list[ServiceCall] = []

    def registra(call: ServiceCall) -> None:
        chiamate.append(call)

    hass.services.async_register("myitalgas", "imposta_periodo_tariffario", registra)

    await _avvia(hass, REALI, **{CONF_PDR_MYITALGAS: "entry_myitalgas_123"})
    # un periodo per ogni mese coperto dalle bollette
    mesi = {c.data["dal"] for c in chiamate}
    assert "2026-07-01" in mesi
    assert "2026-01-01" in mesi
    assert len(mesi) >= 8
    luglio = next(c for c in chiamate if c.data["dal"] == "2026-07-01")
    assert luglio.data["config_entry_id"] == "entry_myitalgas_123"
    assert luglio.data["prezzo_sc2"] == pytest.approx(1.4764, abs=0.001)
    assert luglio.data["quota_fissa"] == pytest.approx(17.60, abs=0.01)


async def test_niente_invio_senza_servizio(hass: HomeAssistant) -> None:
    """Se MyItalgas non è installato, l'integrazione non va in errore."""
    entry = await _avvia(hass, REALI, **{CONF_PDR_MYITALGAS: "entry_x"})
    assert entry.runtime_data.last_update_success


async def test_periodi_inviati_una_sola_volta(hass: HomeAssistant) -> None:
    """Gli stessi periodi non vengono reinviati a ogni aggiornamento."""
    chiamate: list[ServiceCall] = []

    def registra(call: ServiceCall) -> None:
        chiamate.append(call)

    hass.services.async_register("myitalgas", "imposta_periodo_tariffario", registra)
    entry = await _avvia(hass, REALI, **{CONF_PDR_MYITALGAS: "entry_y"})
    primo_giro = len(chiamate)
    assert primo_giro >= 8
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(chiamate) == primo_giro  # nessun reinvio


async def test_scaricamento(hass: HomeAssistant) -> None:
    """Unload pulito."""
    entry = await _avvia(hass, REALI)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
