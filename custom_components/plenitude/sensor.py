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
from homeassistant.const import (
    CURRENCY_EURO,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .account import GAS, LUCE, Conto
from .const import DOMAIN
from .coordinator import (
    DatiPlenitude,
    PlenitudeAccountCoordinator,
    PlenitudeConfigEntry,
    PlenitudeCoordinator,
)
from .pannello import DatiPannello, Riepilogo
from .statistiche import spese_totali

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
    # per la luce: unità (al posto di quella del gas) e classe del sensore
    unita_luce: str | None = None
    classe_luce: SensorDeviceClass | None = SensorDeviceClass.ENERGY


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
    if (luce := conto.bollette_luce.get(b.numero)) is not None:
        attributi["periodo_dal"] = luce.dal.isoformat()
        attributi["periodo_al"] = luce.al.isoformat()
        attributi["consumo_kwh"] = luce.consumo_kwh
    return attributi


def _attributi_spese(spese: dict[date, float]) -> dict[str, Any] | None:
    """Mese dell'ultima spesa e spese degli ultimi 12 mesi fatturati."""
    if not spese:
        return None
    mesi = sorted(spese)[-12:]
    return {
        "mese": mesi[-1].strftime("%Y-%m"),
        "mesi": {m.strftime("%Y-%m"): spese[m] for m in mesi},
        "totale_12_mesi": round(sum(spese[m] for m in mesi), 2),
    }


def _ultima_spesa(spese: dict[date, float]) -> float | None:
    return spese[max(spese)] if spese else None


def _attributi_indice(conto: Conto) -> dict[str, Any] | None:
    """Mese dell'ultimo indice, storico degli ultimi 12 mesi, spread e dispacciamento."""
    indici = conto.indici
    if not indici:
        return None
    mesi = sorted(indici)[-12:]
    attributi: dict[str, Any] = {
        "mese": mesi[-1].strftime("%Y-%m"),
        "mesi": {m.strftime("%Y-%m"): indici[m] for m in mesi},
        "spread": conto.spread,
    }
    if (luce := conto.ultima_luce) is not None and luce.dispacciamento is not None:
        attributi["dispacciamento"] = luce.dispacciamento
    return attributi


def _ultimo_indice(conto: Conto) -> float | None:
    indici = conto.indici
    return indici[max(indici)] if indici else None


def _attributi_offerta(conto: Conto) -> dict[str, Any]:
    return {
        "tipologia": conto.tipologia_offerta,
        "mercato": conto.mercato,
        "condizioni_dal": _iso(conto.condizioni_dal),
        "condizioni_al": _iso(conto.condizioni_al),
        "condizioni_scadute": conto.condizioni_scadute,
    }


def _attributi_lettura(conto: Conto) -> dict[str, Any]:
    attributi: dict[str, Any] = {"data_lettura": _iso(conto.data_lettura)}
    attributi.update({f"fascia_{f.lower()}": v for f, v in conto.letture_fasce.items()})
    return attributi


def _attributi_bollette_lette(conto: Conto) -> dict[str, Any]:
    if conto.commodity == GAS:
        return {"periodi_tariffari": [p.dal.isoformat() for p in conto.periodi]}
    return {
        "periodi": [
            f"{b.dal.isoformat()}/{b.al.isoformat()}"
            for b in sorted(conto.bollette_luce.values(), key=lambda b: b.dal)
        ]
    }


def _attributi_potenza(conto: Conto) -> dict[str, Any] | None:
    if (luce := conto.ultima_luce) is None:
        return None
    return {
        "potenza_disponibile": luce.potenza_disponibile,
        "quota_potenza": luce.quota_potenza_kw_mese,
    }


def _attributi_prezzo_energia(conto: Conto) -> dict[str, Any] | None:
    if (luce := conto.ultima_luce) is None:
        return None
    return {
        "periodo_dal": luce.dal.isoformat(),
        "periodo_al": luce.al.isoformat(),
        "prezzo_quota_consumi": luce.prezzo_quota_consumi,
        "imposte": luce.imposte,
        "quota_fissa": luce.quota_fissa_mese,
    }


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
    if c.anno_precedente is not None:
        attributi["anno_precedente"] = c.anno_precedente
    if c.differenza_percentuale is not None:
        attributi["differenza_percentuale"] = c.differenza_percentuale
    attributi.update({f"fascia_{f.lower()}": v for f, v in c.fasce.items()})
    return attributi


def _attributi_prezzo(conto: Conto) -> dict[str, Any] | None:
    if (p := conto.prezzo_medio) is None:
        return None
    return {
        "numero": p.numero,
        "periodo_dal": p.dal.isoformat(),
        "periodo_al": p.al.isoformat(),
        "consumo": p.consumo,
        "importo": p.importo,
    }


def _attributi_prezzo_gas(conto: Conto) -> dict[str, Any] | None:
    if (p := conto.prezzo_gas) is None:
        return None
    return {
        "scaglione": p.scaglione,
        "mese_tariffa": p.mese.strftime("%Y-%m"),
        "consumo_anno": p.consumo_anno,
    }


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
        key="spesa_ultimo_mese",
        translation_key="spesa_ultimo_mese",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        valore=lambda c: _ultima_spesa(c.spese_mensili),
        attributi=lambda c: _attributi_spese(c.spese_mensili),
    ),
    ContoSensorDescription(
        key="pun",
        translation_key="pun",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
        suggested_display_precision=4,
        forniture=frozenset({LUCE}),
        valore=_ultimo_indice,
        attributi=_attributi_indice,
    ),
    ContoSensorDescription(
        key="indice_gas",
        translation_key="indice_gas",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UNITA_SMC}",
        suggested_display_precision=4,
        forniture=frozenset({GAS}),
        valore=_ultimo_indice,
        attributi=_attributi_indice,
    ),
    ContoSensorDescription(
        key="offerta",
        translation_key="offerta",
        valore=lambda c: c.prodotto,
        attributi=_attributi_offerta,
    ),
    ContoSensorDescription(
        key="prezzo_medio",
        translation_key="prezzo_medio",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfVolume.CUBIC_METERS}",
        unita_luce=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
        classe_luce=None,
        suggested_display_precision=3,
        valore=lambda c: c.prezzo_medio.valore if c.prezzo_medio else None,
        attributi=_attributi_prezzo,
    ),
    ContoSensorDescription(
        key="prezzo_gas",
        translation_key="prezzo_gas",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfVolume.CUBIC_METERS}",
        suggested_display_precision=3,
        forniture=frozenset({GAS}),
        valore=lambda c: c.prezzo_gas.valore if c.prezzo_gas else None,
        attributi=_attributi_prezzo_gas,
    ),
    ContoSensorDescription(
        key="lettura_contatore",
        translation_key="lettura_contatore",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        unita_luce=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        valore=lambda c: c.lettura,
        attributi=_attributi_lettura,
    ),
    ContoSensorDescription(
        key="consumo_ultima_bolletta",
        translation_key="consumo_ultima_bolletta",
        native_unit_of_measurement=UNITA_SMC,
        unita_luce=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        valore=lambda c: c.consumo_ultima_bolletta,
    ),
    ContoSensorDescription(
        key="potenza_impegnata",
        translation_key="potenza_impegnata",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=1,
        forniture=frozenset({LUCE}),
        valore=lambda c: c.ultima_luce.potenza_impegnata if c.ultima_luce else None,
        attributi=_attributi_potenza,
    ),
    ContoSensorDescription(
        key="prezzo_energia",
        translation_key="prezzo_energia",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
        suggested_display_precision=3,
        forniture=frozenset({LUCE}),
        valore=lambda c: c.ultima_luce.prezzo_energia if c.ultima_luce else None,
        attributi=_attributi_prezzo_energia,
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
        unita_luce=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        valore=lambda c: c.consumo_annuo,
    ),
    ContoSensorDescription(
        key="bollette_lette",
        translation_key="bollette_lette",
        entity_category=EntityCategory.DIAGNOSTIC,
        valore=lambda c: c.bollette_lette,
        attributi=_attributi_bollette_lette,
    ),
)


# --- Adotta un pannello ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PannelloSensorDescription(SensorEntityDescription):
    """Descrizione di un sensore dei pannelli virtuali di una fornitura di luce."""

    valore: Callable[[DatiPannello], Valore]
    attributi: Callable[[DatiPannello], dict[str, Any] | None] = lambda _: None


def _fino_al(dati: DatiPannello) -> str | None:
    """Fine (ora locale) dell'ultimo quarto d'ora con produzione e consumi."""
    fine = dati.ultimo_completo
    return dt_util.as_local(fine).isoformat() if fine else None


def _attributi_energia(r: Riepilogo | None, dati: DatiPannello) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "consumo": r.consumo,
        "autoconsumo": r.autoconsumo,
        "prelievo": r.prelievo,
        "non_autoconsumata": r.immissione,
        "quota_autoconsumata": r.quota_autoconsumata,
        "copertura_consumi": r.copertura,
        "dati_fino_al": _fino_al(dati),
    }


def _attributi_risparmio(r: Riepilogo | None, dati: DatiPannello) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "autoconsumo": r.autoconsumo,
        "canone": r.canone,
        "netto": r.netto,
        "prezzo_kwh": dati.prezzo,
        "dati_fino_al": _fino_al(dati),
    }


def _attributi_pannelli(dati: DatiPannello) -> dict[str, Any]:
    return {
        "pannelli": [
            {
                "codice": p.codice,
                "posizione": p.posizione,
                "taglia": p.taglia,
                "potenza_kw": p.potenza,
                "canone_mensile": p.canone,
            }
            for p in dati.pannelli
        ],
        "potenza_nominale_kw": dati.potenza,
        "attivo_dal": dt_util.as_local(dati.attivo_dal).isoformat() if dati.attivo_dal else None,
    }


_KWH = UnitOfEnergy.KILO_WATT_HOUR

SENSORI_PANNELLO: tuple[PannelloSensorDescription, ...] = (
    PannelloSensorDescription(
        key="pannello_potenza",
        translation_key="pannello_potenza",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        valore=lambda d: d.potenza_istantanea,
        attributi=_attributi_pannelli,
    ),
    PannelloSensorDescription(
        key="pannello_produzione_mese",
        translation_key="pannello_produzione_mese",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=_KWH,
        suggested_display_precision=2,
        valore=lambda d: d.mese.produzione if d.mese else None,
        attributi=lambda d: _attributi_energia(d.mese, d),
    ),
    PannelloSensorDescription(
        key="pannello_autoconsumo_mese",
        translation_key="pannello_autoconsumo_mese",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=_KWH,
        suggested_display_precision=2,
        valore=lambda d: d.mese.autoconsumo if d.mese else None,
        attributi=lambda d: _attributi_energia(d.mese, d),
    ),
    PannelloSensorDescription(
        key="pannello_risparmio_mese",
        translation_key="pannello_risparmio_mese",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        valore=lambda d: d.mese.risparmio if d.mese else None,
        attributi=lambda d: _attributi_risparmio(d.mese, d),
    ),
    PannelloSensorDescription(
        key="pannello_produzione_totale",
        translation_key="pannello_produzione_totale",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=_KWH,
        suggested_display_precision=2,
        valore=lambda d: d.totale.produzione if d.totale else None,
        attributi=lambda d: _attributi_energia(d.totale, d),
    ),
    PannelloSensorDescription(
        key="pannello_risparmio_totale",
        translation_key="pannello_risparmio_totale",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        valore=lambda d: d.totale.risparmio if d.totale else None,
        attributi=lambda d: _attributi_risparmio(d.totale, d),
    ),
    PannelloSensorDescription(
        key="pannello_prezzo",
        translation_key="pannello_prezzo",
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
        suggested_display_precision=4,
        entity_category=EntityCategory.DIAGNOSTIC,
        valore=lambda d: d.prezzo,
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

    async_add_entities(
        [
            UltimoAggiornamentoSensor(coordinator),
            SpesaTotaleSensor(coordinator),
            ComunicazioniSensor(coordinator),
        ]
    )
    noti: set[str] = set()
    con_pannello: set[str] = set()

    @callback
    def _aggiungi_conti() -> None:
        nuovi = [c for c in coordinator.data.conti.values() if c.codice not in noti]
        noti.update(c.codice for c in nuovi)
        entita: list[SensorEntity] = [
            ContoSensor(coordinator, conto.codice, descrizione)
            for conto in nuovi
            for descrizione in SENSORI_CONTO
            if conto.commodity in descrizione.forniture
        ]
        # i sensori dei pannelli compaiono quando la fornitura ne adotta uno
        pannelli = [
            c
            for c in coordinator.data.conti.values()
            if c.pannello is not None and c.codice not in con_pannello
        ]
        con_pannello.update(c.codice for c in pannelli)
        entita.extend(
            PannelloSensor(coordinator, conto.codice, descrizione)
            for conto in pannelli
            for descrizione in SENSORI_PANNELLO
        )
        if entita:
            async_add_entities(entita)

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


def info_account(coordinator: PlenitudeAccountCoordinator) -> DeviceInfo:
    """Dispositivo che rappresenta l'account."""
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
        self._attr_device_info = info_account(coordinator)

    @property
    def native_value(self) -> datetime | None:
        """Ora dell'ultimo aggiornamento."""
        return self.coordinator.data.ultimo_aggiornamento


class ComunicazioniSensor(CoordinatorEntity[PlenitudeAccountCoordinator], SensorEntity):
    """Comunicazioni non lette dell'area clienti (es. proposte di rinnovo delle condizioni)."""

    _attr_has_entity_name = True
    _attr_translation_key = "comunicazioni"

    def __init__(self, coordinator: PlenitudeAccountCoordinator) -> None:
        """Inizializza il sensore."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_comunicazioni"
        self._attr_device_info = info_account(coordinator)

    @property
    def native_value(self) -> int:
        """Quante comunicazioni non sono ancora state lette."""
        return sum(1 for a in self.coordinator.data.avvisi if not a.letta)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Le ultime comunicazioni."""
        return {
            "comunicazioni": [
                {
                    "data": _iso(a.data),
                    "descrizione": a.descrizione,
                    "fornitura": a.fornitura,
                    "letta": a.letta,
                }
                for a in self.coordinator.data.avvisi[:10]
            ]
        }


class SpesaTotaleSensor(CoordinatorEntity[PlenitudeAccountCoordinator], SensorEntity):
    """Spesa di luce e gas insieme nell'ultimo mese già fatturato per tutte le forniture."""

    _attr_has_entity_name = True
    _attr_translation_key = "spesa_totale_ultimo_mese"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: PlenitudeAccountCoordinator) -> None:
        """Inizializza il sensore."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_spesa_totale_ultimo_mese"
        self._attr_device_info = info_account(coordinator)

    @property
    def native_value(self) -> float | None:
        """Spesa totale dell'ultimo mese."""
        return _ultima_spesa(spese_totali(self.coordinator.data.conti))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Spese degli ultimi 12 mesi."""
        return _attributi_spese(spese_totali(self.coordinator.data.conti))


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
            if descrizione.classe_luce:
                self._attr_device_class = descrizione.classe_luce
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


class PannelloSensor(CoordinatorEntity[PlenitudeAccountCoordinator], SensorEntity):
    """Sensore dei pannelli virtuali ("Adotta un pannello") di una fornitura di luce."""

    _attr_has_entity_name = True
    entity_description: PannelloSensorDescription

    def __init__(
        self,
        coordinator: PlenitudeAccountCoordinator,
        codice: str,
        descrizione: PannelloSensorDescription,
    ) -> None:
        """Inizializza il sensore, nel dispositivo della fornitura di luce."""
        super().__init__(coordinator)
        self._codice = codice
        conto = coordinator.data.conti[codice]
        self.entity_description = descrizione
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
        """Disponibile finché la fornitura ha un pannello."""
        conto = self._conto
        return super().available and conto is not None and conto.pannello is not None

    @property
    def native_value(self) -> Valore:
        """Valore del sensore."""
        conto = self._conto
        if conto is None or conto.pannello is None:
            return None
        return self.entity_description.valore(conto.pannello)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attributi aggiuntivi."""
        conto = self._conto
        if conto is None or conto.pannello is None:
            return None
        return self.entity_description.attributi(conto.pannello)
