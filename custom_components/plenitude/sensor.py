"""Sensori Plenitude."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import DatiPlenitude, PlenitudeConfigEntry, PlenitudeCoordinator

PARALLEL_UPDATES = 0

UNITA_SMC = "Smc"

type Valore = float | int | str | date | datetime | None


@dataclass(frozen=True, kw_only=True)
class PlenitudeSensorDescription(SensorEntityDescription):
    """Descrizione di un sensore Plenitude."""

    valore: Callable[[DatiPlenitude], Valore]
    attributi: Callable[[DatiPlenitude], dict[str, Any] | None] = lambda _: None


def _attributi_bolletta(dati: DatiPlenitude) -> dict[str, Any] | None:
    if (b := dati.ultima) is None:
        return None
    return {
        "periodo_dal": b.dal.isoformat(),
        "periodo_al": b.al.isoformat(),
        "emissione": b.emissione.isoformat() if b.emissione else None,
        "consumo_smc": b.consumo_smc,
    }


SENSORI: tuple[PlenitudeSensorDescription, ...] = (
    PlenitudeSensorDescription(
        key="ultima_bolletta_importo",
        translation_key="ultima_bolletta_importo",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        valore=lambda d: d.ultima.importo if d.ultima else None,
        attributi=_attributi_bolletta,
    ),
    PlenitudeSensorDescription(
        key="ultima_bolletta_scadenza",
        translation_key="ultima_bolletta_scadenza",
        device_class=SensorDeviceClass.DATE,
        valore=lambda d: d.ultima.scadenza if d.ultima else None,
    ),
    PlenitudeSensorDescription(
        key="ultima_bolletta_consumo",
        translation_key="ultima_bolletta_consumo",
        native_unit_of_measurement=UNITA_SMC,
        suggested_display_precision=0,
        valore=lambda d: d.ultima.consumo_smc if d.ultima else None,
        attributi=_attributi_bolletta,
    ),
    PlenitudeSensorDescription(
        key="consumo_annuo",
        translation_key="consumo_annuo",
        native_unit_of_measurement=UNITA_SMC,
        suggested_display_precision=0,
        valore=lambda d: d.ultima.consumo_annuo_smc if d.ultima else None,
    ),
    PlenitudeSensorDescription(
        key="bollette_lette",
        translation_key="bollette_lette",
        entity_category=EntityCategory.DIAGNOSTIC,
        valore=lambda d: len(d.bollette),
        attributi=lambda d: {
            "periodi_tariffari": [p.dal.isoformat() for p in d.periodi],
        },
    ),
    PlenitudeSensorDescription(
        key="ultimo_aggiornamento",
        translation_key="ultimo_aggiornamento",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        valore=lambda d: d.ultimo_aggiornamento,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenitudeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crea i sensori."""
    coordinator = entry.runtime_data
    async_add_entities(PlenitudeSensor(coordinator, d) for d in SENSORI)


class PlenitudeSensor(CoordinatorEntity[PlenitudeCoordinator], SensorEntity):
    """Sensore basato sulle bollette Plenitude."""

    _attr_has_entity_name = True
    entity_description: PlenitudeSensorDescription

    def __init__(
        self, coordinator: PlenitudeCoordinator, descrizione: PlenitudeSensorDescription
    ) -> None:
        """Inizializza il sensore."""
        super().__init__(coordinator)
        self.entity_description = descrizione
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{descrizione.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(coordinator.config_entry.domain, coordinator.config_entry.entry_id)},
            name="Plenitude",
            manufacturer="Eni Plenitude",
        )

    @property
    def native_value(self) -> Valore:
        """Valore del sensore."""
        return self.entity_description.valore(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attributi aggiuntivi."""
        return self.entity_description.attributi(self.coordinator.data)
