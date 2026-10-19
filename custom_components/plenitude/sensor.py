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
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, EntityCategory, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .account import GAS, LUCE, Conto
from .const import DOMAIN
from .coordinator import (
    DatiPlenitude,
    PlenitudeAccountCoordinator,
    PlenitudeConfigEntry,
    PlenitudeCoordinator,
)

PARALLEL_UPDATES = 0

UNITA_SMC = "Smc"

type Valore = float | int | str | date | datetime | None


# --- Bollette lette da cartella -------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PlenitudeSensorDescription(SensorEntityDescription):
    """Descrizione di un sensore Plenitude (bollette da cartella)."""

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
        native_unit_of_measurement=CURRENCY_EURO,
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


# --- Account Plenitude ----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ContoSensorDescription(SensorEntityDescription):
    """Descrizione di un sensore di un conto (luce o gas) dell'account."""

    valore: Callable[[Conto], Valore]
    attributi: Callable[[Conto], dict[str, Any] | None] = lambda _: None
    # tipi di fornitura per cui il sensore esiste
    forniture: frozenset[str] = frozenset({GAS, LUCE})
    unita_luce: str | None = None


def _iso(giorno: date | None) -> str | None:
    return giorno.isoformat() if giorno else None


def _attributi_ultima(conto: Conto) -> dict[str, Any] | None:
    if (b := conto.ultima) is None:
        return None
    attributi: dict[str, Any] = {
        "numero": b.numero,
        "emissione": _iso(b.emissione),
        "scadenza": _iso(b.scadenza),
        "stato_pagamento": b.stato_pagamento,
        "metodo_pagamento": b.metodo_pagamento,
        "da_pagare": b.da_pagare,
    }
    if (pdf := conto.ultima_pdf) is not None and pdf.importo == b.importo:
        attributi["periodo_dal"] = pdf.dal.isoformat()
        attributi["periodo_al"] = pdf.al.isoformat()
        attributi["consumo_smc"] = pdf.consumo_smc
    return attributi


def _attributi_da_pagare(conto: Conto) -> dict[str, Any]:
    elenco = conto.da_pagare
    return {
        "bollette": [
            {"numero": b.numero, "scadenza": _iso(b.scadenza), "importo": b.da_pagare}
            for b in elenco
        ],
        "prossima_scadenza": _iso(elenco[0].scadenza) if elenco else None,
    }


def _attributi_consumo(conto: Conto) -> dict[str, Any] | None:
    if (c := conto.ultimo_consumo) is None:
        return None
    attributi: dict[str, Any] = {"mese": c.mese.strftime("%Y-%m"), "parziale": c.parziale}
    attributi.update({f"fascia_{f.lower()}": v for f, v in c.fasce.items()})
    return attributi


def _attributi_12_mesi(conto: Conto) -> dict[str, Any] | None:
    completi = [c for c in conto.consumi if not c.parziale][-12:]
    if not completi:
        return None
    return {
        "dal": completi[0].mese.strftime("%Y-%m"),
        "al": completi[-1].mese.strftime("%Y-%m"),
    }


SENSORI_CONTO: tuple[ContoSensorDescription, ...] = (
    ContoSensorDescription(
        key="ultima_bolletta_importo",
        translation_key="ultima_bolletta_importo",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        valore=lambda c: c.ultima.importo if c.ultima else None,
        attributi=_attributi_ultima,
    ),
    ContoSensorDescription(
        key="ultima_bolletta_scadenza",
        translation_key="ultima_bolletta_scadenza",
        device_class=SensorDeviceClass.DATE,
        valore=lambda c: c.ultima.scadenza if c.ultima else None,
    ),
    ContoSensorDescription(
        key="ultima_bolletta_emissione",
        translation_key="ultima_bolletta_emissione",
        device_class=SensorDeviceClass.DATE,
        valore=lambda c: c.ultima.emissione if c.ultima else None,
    ),
    ContoSensorDescription(
        key="da_pagare",
        translation_key="da_pagare",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        valore=lambda c: c.totale_da_pagare,
        attributi=_attributi_da_pagare,
    ),
    ContoSensorDescription(
        key="bollette_da_pagare",
        translation_key="bollette_da_pagare",
        valore=lambda c: len(c.da_pagare),
    ),
    ContoSensorDescription(
        key="consumo_ultimo_mese",
        translation_key="consumo_ultimo_mese",
        native_unit_of_measurement=UNITA_SMC,
        unita_luce=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        valore=lambda c: c.ultimo_consumo.valore if c.ultimo_consumo else None,
        attributi=_attributi_consumo,
    ),
    ContoSensorDescription(
        key="consumo_12_mesi",
        translation_key="consumo_12_mesi",
        native_unit_of_measurement=UNITA_SMC,
        unita_luce=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        valore=lambda c: c.consumo_12_mesi,
        attributi=_attributi_12_mesi,
    ),
    ContoSensorDescription(
        key="lettura_contatore",
        translation_key="lettura_contatore",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        forniture=frozenset({GAS}),
        valore=lambda c: c.lettura,
        attributi=lambda c: {"data_lettura": _iso(c.data_lettura)},
    ),
    ContoSensorDescription(
        key="prossima_autolettura",
        translation_key="prossima_autolettura",
        device_class=SensorDeviceClass.DATE,
        forniture=frozenset({GAS}),
        valore=lambda c: c.autolettura_dal,
        attributi=lambda c: {"fino_al": _iso(c.autolettura_al)},
    ),
    ContoSensorDescription(
        key="consumo_annuo",
        translation_key="consumo_annuo",
        native_unit_of_measurement=UNITA_SMC,
        suggested_display_precision=0,
        forniture=frozenset({GAS}),
        valore=lambda c: c.ultima_pdf.consumo_annuo_smc if c.ultima_pdf else None,
    ),
    ContoSensorDescription(
        key="bollette_lette",
        translation_key="bollette_lette",
        entity_category=EntityCategory.DIAGNOSTIC,
        forniture=frozenset({GAS}),
        valore=lambda c: len(c.bollette_pdf),
        attributi=lambda c: {"periodi_tariffari": [p.dal.isoformat() for p in c.periodi]},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenitudeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crea i sensori."""
    coordinator = entry.runtime_data
    if isinstance(coordinator, PlenitudeCoordinator):
        async_add_entities(PlenitudeSensor(coordinator, d) for d in SENSORI)
        return

    async_add_entities([UltimoAggiornamentoSensor(coordinator)])
    noti: set[str] = set()

    @callback
    def _aggiungi_conti() -> None:
        nuovi = [c for c in coordinator.data.conti.values() if c.codice not in noti]
        if not nuovi:
            return
        noti.update(c.codice for c in nuovi)
        async_add_entities(
            ContoSensor(coordinator, conto.codice, descrizione)
            for conto in nuovi
            for descrizione in SENSORI_CONTO
            if conto.commodity in descrizione.forniture
        )

    _aggiungi_conti()
    entry.async_on_unload(coordinator.async_add_listener(_aggiungi_conti))


class PlenitudeSensor(CoordinatorEntity[PlenitudeCoordinator], SensorEntity):
    """Sensore basato sulle bollette Plenitude lette da cartella."""

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


def _info_account(coordinator: PlenitudeAccountCoordinator) -> DeviceInfo:
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Plenitude",
        manufacturer="Eni Plenitude",
        model="Account",
    )


class UltimoAggiornamentoSensor(CoordinatorEntity[PlenitudeAccountCoordinator], SensorEntity):
    """Ultima lettura riuscita dell'account."""

    _attr_has_entity_name = True
    _attr_translation_key = "ultimo_aggiornamento"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PlenitudeAccountCoordinator) -> None:
        """Inizializza il sensore."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_ultimo_aggiornamento"
        self._attr_device_info = _info_account(coordinator)

    @property
    def native_value(self) -> datetime | None:
        """Ora dell'ultimo aggiornamento."""
        return self.coordinator.data.ultimo_aggiornamento


class ContoSensor(CoordinatorEntity[PlenitudeAccountCoordinator], SensorEntity):
    """Sensore di un conto (fornitura di luce o gas) dell'account."""

    _attr_has_entity_name = True
    entity_description: ContoSensorDescription

    def __init__(
        self,
        coordinator: PlenitudeAccountCoordinator,
        codice: str,
        descrizione: ContoSensorDescription,
    ) -> None:
        """Inizializza il sensore."""
        super().__init__(coordinator)
        self._codice = codice
        conto = coordinator.data.conti[codice]
        self.entity_description = descrizione
        if conto.commodity == LUCE and descrizione.unita_luce:
            self._attr_native_unit_of_measurement = descrizione.unita_luce
            self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_unique_id = f"{codice}_{descrizione.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, codice)},
            name=_nome_dispositivo(coordinator, conto),
            manufacturer="Eni Plenitude",
            model=conto.prodotto,
            serial_number=conto.matricola,
        )

    @property
    def _conto(self) -> Conto | None:
        return self.coordinator.data.conti.get(self._codice)

    @property
    def available(self) -> bool:
        """Disponibile finché il conto compare nell'account."""
        return super().available and self._conto is not None

    @property
    def native_value(self) -> Valore:
        """Valore del sensore."""
        conto = self._conto
        return self.entity_description.valore(conto) if conto else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attributi aggiuntivi."""
        conto = self._conto
        return self.entity_description.attributi(conto) if conto else None


def _nome_dispositivo(coordinator: PlenitudeAccountCoordinator, conto: Conto) -> str:
    nome = "Plenitude Gas" if conto.commodity == GAS else "Plenitude Luce"
    stessi = [c for c in coordinator.data.conti.values() if c.commodity == conto.commodity]
    if len(stessi) > 1:
        return f"{nome} {conto.codice}"
    return nome
