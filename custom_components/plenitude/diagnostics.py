"""Diagnostica Plenitude (dati personali oscurati)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .account import Conto
from .bollette import Bolletta, PeriodoTariffario
from .const import CONF_CONTO_GAS, CONF_EMAIL, CONF_PDR_MYITALGAS, CONF_SESSIONE
from .coordinator import DatiAccount, DatiPlenitude, PlenitudeConfigEntry

DA_OSCURARE = {
    CONF_PDR_MYITALGAS,
    CONF_CONTO_GAS,
    CONF_EMAIL,
    CONF_SESSIONE,
    "pdr",
    "pod",
    "title",
    "unique_id",
}


def _bollette_pdf(bollette: list[Bolletta]) -> list[dict[str, Any]]:
    return [
        {
            "dal": b.dal.isoformat(),
            "al": b.al.isoformat(),
            "consumo_smc": b.consumo_smc,
            "importo": b.importo,
            "coefficiente": b.coefficiente,
        }
        for b in bollette
    ]


def _periodi(periodi: list[PeriodoTariffario]) -> list[dict[str, Any]]:
    return [
        {
            "dal": p.dal.isoformat(),
            "prezzo_sc1": p.prezzo_sc1,
            "prezzo_sc2": p.prezzo_sc2,
            "prezzo_sc3": p.prezzo_sc3,
            "quota_fissa": p.quota_fissa,
        }
        for p in periodi
    ]


def _conto(indice: int, conto: Conto) -> dict[str, Any]:
    """Un conto senza codici, indirizzi né identificativi della fornitura."""
    return {
        "conto": f"conto_{indice}",
        "commodity": conto.commodity,
        "stato": conto.stato,
        "prodotto": conto.prodotto,
        "bollette": [
            {
                "emissione": b.emissione.isoformat() if b.emissione else None,
                "scadenza": b.scadenza.isoformat() if b.scadenza else None,
                "importo": b.importo,
                "da_pagare": b.da_pagare,
                "stato_pagamento": b.stato_pagamento,
            }
            for b in conto.bollette
        ],
        "mesi_di_consumo": len(conto.consumi),
        "ultimo_consumo": (
            {
                "mese": conto.ultimo_consumo.mese.isoformat(),
                "valore": conto.ultimo_consumo.valore,
                "fasce": conto.ultimo_consumo.fasce,
            }
            if conto.ultimo_consumo
            else None
        ),
        "lettura": conto.lettura,
        "data_lettura": conto.data_lettura.isoformat() if conto.data_lettura else None,
        "bollette_pdf": _bollette_pdf(conto.bollette_pdf),
        "periodi_tariffari": _periodi(conto.periodi),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PlenitudeConfigEntry
) -> dict[str, Any]:
    """Diagnostica dell'integrazione."""
    dati = entry.runtime_data.data
    risultato: dict[str, Any] = {"entry": async_redact_data(entry.as_dict(), DA_OSCURARE)}
    if isinstance(dati, DatiAccount):
        risultato["conti"] = [_conto(i, c) for i, c in enumerate(dati.conti.values(), 1)]
    elif isinstance(dati, DatiPlenitude):
        risultato["bollette"] = _bollette_pdf(dati.bollette)
        risultato["periodi_tariffari"] = _periodi(dati.periodi)
    else:
        risultato["bollette"] = None
        risultato["periodi_tariffari"] = None
    risultato["ultimo_aggiornamento"] = (
        dati.ultimo_aggiornamento.isoformat() if dati and dati.ultimo_aggiornamento else None
    )
    return risultato
