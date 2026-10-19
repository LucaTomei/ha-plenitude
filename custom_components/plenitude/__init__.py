"""Integrazione Plenitude: bollette, consumi e letture di luce e gas dall'account Eni Plenitude."""

from __future__ import annotations

from pathlib import Path

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .api import ClientPlenitude, Sessione, SorgenteCartella
from .const import (
    CARTELLA_ARCHIVIO,
    CARTELLA_BOLLETTE_DEFAULT,
    CONF_CARTELLA_BOLLETTE,
    CONF_SESSIONE,
    DOMAIN,
    STORAGE_VERSION,
)
from .coordinator import PlenitudeAccountCoordinator, PlenitudeConfigEntry, PlenitudeCoordinator

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]


def sessione_da_entry(entry: PlenitudeConfigEntry) -> Sessione:
    """Sessione Gigya salvata nella configurazione."""
    return Sessione(**entry.data[CONF_SESSIONE])


async def async_setup_entry(hass: HomeAssistant, entry: PlenitudeConfigEntry) -> bool:
    """Configura l'integrazione Plenitude."""
    if CONF_SESSIONE in entry.data:
        coordinator: (
            PlenitudeCoordinator | PlenitudeAccountCoordinator
        ) = await _coordinatore_account(hass, entry)
    else:
        coordinator = await _coordinatore_cartella(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _coordinatore_cartella(
    hass: HomeAssistant, entry: PlenitudeConfigEntry
) -> PlenitudeCoordinator:
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
    return PlenitudeCoordinator(hass, entry, SorgenteCartella(cartella))


async def _coordinatore_account(
    hass: HomeAssistant, entry: PlenitudeConfigEntry
) -> PlenitudeAccountCoordinator:
    # sessione HTTP dedicata (il portale usa un cookie di sessione legato all'account);
    # Home Assistant la chiude allo scaricamento dell'entry e all'arresto
    http = async_create_clientsession(hass)
    client = ClientPlenitude(http, sessione_da_entry(entry))
    return PlenitudeAccountCoordinator(
        hass, entry, client, Path(hass.config.path(CARTELLA_ARCHIVIO))
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PlenitudeConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Consente di eliminare dall'interfaccia i dispositivi di forniture non più nell'account."""
    coordinator = getattr(entry, "runtime_data", None)
    if not isinstance(coordinator, PlenitudeAccountCoordinator) or coordinator.data is None:
        return False
    codici = {codice for dominio, codice in device.identifiers if dominio == DOMAIN}
    if entry.entry_id in codici:
        return False  # il dispositivo dell'account resta finché c'è l'integrazione
    return not codici & set(coordinator.data.conti)


async def async_unload_entry(hass: HomeAssistant, entry: PlenitudeConfigEntry) -> bool:
    """Rimuove l'integrazione."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: PlenitudeConfigEntry) -> None:
    """Alla rimozione dell'integrazione cancella la cache delle bollette lette."""
    if CONF_SESSIONE in entry.data:
        await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.bollette").async_remove()
