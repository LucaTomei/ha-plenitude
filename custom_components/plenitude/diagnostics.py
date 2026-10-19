"""Diagnostica Plenitude (dati personali oscurati)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_PDR_MYITALGAS
from .coordinator import PlenitudeConfigEntry

DA_OSCURARE = {CONF_PDR_MYITALGAS, "pdr", "pod", "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PlenitudeConfigEntry
) -> dict[str, Any]:
    """Diagnostica dell'integrazione."""
    dati = entry.runtime_data.data
    return {
        "entry": async_redact_data(entry.as_dict(), DA_OSCURARE),
        "bollette": [
            {
                "dal": b.dal.isoformat(),
                "al": b.al.isoformat(),
                "consumo_smc": b.consumo_smc,
                "importo": b.importo,
                "coefficiente": b.coefficiente,
            }
            for b in dati.bollette
        ]
        if dati
        else None,
        "periodi_tariffari": [
            {
                "dal": p.dal.isoformat(),
                "prezzo_sc1": p.prezzo_sc1,
                "prezzo_sc2": p.prezzo_sc2,
                "prezzo_sc3": p.prezzo_sc3,
                "quota_fissa": p.quota_fissa,
            }
            for p in dati.periodi
        ]
        if dati
        else None,
        "ultimo_aggiornamento": dati.ultimo_aggiornamento.isoformat()
        if dati and dati.ultimo_aggiornamento
        else None,
    }
