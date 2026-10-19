"""Integrazione Plenitude: bollette luce e gas dall'account Eni Plenitude."""

from __future__ import annotations

from pathlib import Path

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import SorgenteCartella
from .const import CARTELLA_BOLLETTE_DEFAULT, CONF_CARTELLA_BOLLETTE
from .coordinator import PlenitudeConfigEntry, PlenitudeCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: PlenitudeConfigEntry) -> bool:
    """Configura l'integrazione Plenitude."""
    cartella = Path(
        hass.config.path(
            entry.options.get(
                CONF_CARTELLA_BOLLETTE,
                entry.data.get(CONF_CARTELLA_BOLLETTE, CARTELLA_BOLLETTE_DEFAULT),
            )
        )
    )
    # si crea la cartella se non esiste: l'integrazione si avvia e attende le bollette
    await hass.async_add_executor_job(lambda: cartella.mkdir(parents=True, exist_ok=True))
    sorgente = SorgenteCartella(cartella)
    coordinator = PlenitudeCoordinator(hass, entry, sorgente)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PlenitudeConfigEntry) -> bool:
    """Rimuove l'integrazione."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
