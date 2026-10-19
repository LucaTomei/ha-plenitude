"""Test della configurazione da interfaccia."""

from __future__ import annotations

from unittest.mock import patch

from custom_components.plenitude.const import (
    CONF_CARTELLA_BOLLETTE,
    CONF_INTERVALLO_ORE,
    CONF_PDR_MYITALGAS,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import crea_entry, prepara_cartella


async def test_configurazione(hass: HomeAssistant) -> None:
    """Configurazione con cartella e intervallo."""
    prepara_cartella(hass, [])
    risultato = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert risultato["type"] is FlowResultType.FORM
    with patch("custom_components.plenitude.async_setup_entry", return_value=True) as setup:
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"],
            {CONF_CARTELLA_BOLLETTE: "plenitude/bollette", CONF_INTERVALLO_ORE: 6},
        )
        await hass.async_block_till_done()
    assert risultato["type"] is FlowResultType.CREATE_ENTRY
    assert risultato["data"] == {CONF_CARTELLA_BOLLETTE: "plenitude/bollette"}
    assert risultato["options"] == {
        CONF_CARTELLA_BOLLETTE: "plenitude/bollette",
        CONF_INTERVALLO_ORE: 6,
    }
    assert len(setup.mock_calls) == 1


async def test_istanza_unica(hass: HomeAssistant) -> None:
    """Non si configura due volte."""
    crea_entry(hass)
    risultato = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert risultato["type"] is FlowResultType.ABORT
    assert risultato["reason"] == "already_configured"


async def test_opzioni(hass: HomeAssistant) -> None:
    """Le opzioni vengono salvate e l'integrazione ricaricata."""
    prepara_cartella(hass, [])
    entry = crea_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    risultato = await hass.config_entries.options.async_init(entry.entry_id)
    assert risultato["type"] is FlowResultType.FORM
    risultato = await hass.config_entries.options.async_configure(
        risultato["flow_id"],
        {
            CONF_CARTELLA_BOLLETTE: "plenitude/bollette",
            CONF_INTERVALLO_ORE: 24,
            CONF_PDR_MYITALGAS: "entry_myitalgas",
        },
    )
    await hass.async_block_till_done()
    assert risultato["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_INTERVALLO_ORE] == 24
    assert entry.options[CONF_PDR_MYITALGAS] == "entry_myitalgas"
    assert entry.state is ConfigEntryState.LOADED
