"""Pulsanti Plenitude."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PlenitudeAccountCoordinator, PlenitudeConfigEntry
from .sensor import info_account

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenitudeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crea i pulsanti (solo per l'account Plenitude)."""
    coordinator = entry.runtime_data
    if isinstance(coordinator, PlenitudeAccountCoordinator):
        async_add_entities([ScaricaBolletteButton(coordinator)])


class ScaricaBolletteButton(CoordinatorEntity[PlenitudeAccountCoordinator], ButtonEntity):
    """Rilegge subito l'account e scarica tutte le bollette PDF mancanti."""

    _attr_has_entity_name = True
    _attr_translation_key = "scarica_bollette"

    def __init__(self, coordinator: PlenitudeAccountCoordinator) -> None:
        """Inizializza il pulsante."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_scarica_bollette"
        self._attr_device_info = info_account(coordinator)

    async def async_press(self) -> None:
        """Aggiorna i dati e attende la fine dei download."""
        if not await self.coordinator.async_scarica_bollette():
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="aggiornamento_non_riuscito"
            )
