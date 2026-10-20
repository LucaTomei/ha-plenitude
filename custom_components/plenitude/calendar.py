"""Calendario Plenitude: scadenze delle bollette, autoletture e comunicazioni."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .account import GAS
from .coordinator import DatiAccount, PlenitudeAccountCoordinator, PlenitudeConfigEntry
from .sensor import info_account

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenitudeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crea il calendario (solo per l'account Plenitude)."""
    coordinator = entry.runtime_data
    if isinstance(coordinator, PlenitudeAccountCoordinator):
        async_add_entities([PlenitudeCalendar(coordinator)])


def _euro(valore: float) -> str:
    return f"{valore:.2f} €".replace(".", ",")


def eventi(dati: DatiAccount) -> list[CalendarEvent]:
    """Eventi di tutto il giorno, in ordine di data."""
    elenco: list[CalendarEvent] = []
    for conto in dati.conti.values():
        nome = "gas" if conto.commodity == GAS else "luce"
        for b in conto.bollette:
            if b.scadenza is None:
                continue
            importo = f" · {_euro(b.importo)}" if b.importo is not None else ""
            descrizione = [f"Bolletta {b.numero}"]
            if b.stato_pagamento:
                descrizione.append(f"Stato: {b.stato_pagamento.replace('_', ' ').lower()}")
            if b.da_pagare > 0:
                descrizione.append(f"Da pagare: {_euro(b.da_pagare)}")
            elenco.append(
                CalendarEvent(
                    start=b.scadenza,
                    end=b.scadenza + timedelta(days=1),
                    summary=f"Scadenza bolletta {nome}{importo}",
                    description="\n".join(descrizione),
                    uid=f"bolletta_{conto.codice}_{b.numero}",
                )
            )
        if conto.autolettura_dal is not None and conto.commodity == GAS:
            fine = max(conto.autolettura_al or conto.autolettura_dal, conto.autolettura_dal)
            elenco.append(
                CalendarEvent(
                    start=conto.autolettura_dal,
                    end=fine + timedelta(days=1),
                    summary=f"Autolettura {nome}",
                    description="Finestra per comunicare l'autolettura del contatore",
                    uid=f"autolettura_{conto.codice}_{conto.autolettura_dal.isoformat()}",
                )
            )
    for avviso in dati.avvisi:
        if avviso.data is None:
            continue
        elenco.append(
            CalendarEvent(
                start=avviso.data,
                end=avviso.data + timedelta(days=1),
                summary=avviso.descrizione,
                uid=f"avviso_{avviso.identificativo or avviso.data.isoformat()}",
            )
        )
    elenco.sort(key=lambda e: (e.start, e.summary))
    return elenco


class PlenitudeCalendar(CoordinatorEntity[PlenitudeAccountCoordinator], CalendarEntity):
    """Scadenze delle bollette, finestre di autolettura e comunicazioni."""

    _attr_has_entity_name = True
    _attr_translation_key = "scadenze"

    def __init__(self, coordinator: PlenitudeAccountCoordinator) -> None:
        """Inizializza il calendario."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_scadenze"
        self._attr_device_info = info_account(coordinator)

    @property
    def event(self) -> CalendarEvent | None:
        """Prossimo evento (o quello in corso)."""
        oggi = dt_util.now().date()
        return next((e for e in eventi(self.coordinator.data) if e.end > oggi), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Eventi nell'intervallo richiesto."""
        inizio, fine = _giorno(start_date), _giorno(end_date)
        return [e for e in eventi(self.coordinator.data) if e.end > inizio and e.start <= fine]


def _giorno(istante: datetime) -> date:
    return dt_util.as_local(istante).date()
