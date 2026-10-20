"""L'integrazione resiste a risposte dell'area clienti diverse dal previsto."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.plenitude import account
from custom_components.plenitude.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import crea_entry_account
from .plenitude_finto import CONTO_GAS, CONTO_LUCE, FintoPlenitude

VALORI_STRANI: list[Any] = [None, "", "x", 0, -1, 1.5, [], {}, [None], [{}], {"a": None}, True]


@pytest.mark.parametrize("valore", VALORI_STRANI)
def test_conversioni_non_sollevano(valore: Any) -> None:
    """Le funzioni di lettura non sollevano eccezioni con valori di qualsiasi tipo."""
    conto = account.conto_da({"codice": "1", "commodity": valore}) or account.Conto("1", None)
    account.aggiungi_fornitura(conto, [{"tipo": valore, "status": valore, "ceScadute": valore}])
    account.bollette_da([{"numeroBolletta": "1", "importo": valore, "emissione": valore}])
    account.consumi_da(
        [{"type": "GAS", "consumptions": [{"year": valore, "numericalMonth": valore}]}], "gas"
    )
    account.aggiungi_autolettura(conto, {"valoreAutolettura": valore, "data": valore})


async def _avvia(hass: HomeAssistant):
    entry = crea_entry_account(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


@pytest.mark.parametrize(
    "modifica",
    [
        lambda f: setattr(f, "conti", [{"codice": CONTO_GAS}]),
        lambda f: f.bollette.update({CONTO_GAS: None}),
        lambda f: f.bollette.update({CONTO_GAS: [None, "x", {"numeroBolletta": None}]}),
        lambda f: f.bollette.update({CONTO_LUCE: [{"numeroBolletta": "1", "importo": "abc"}]}),
        lambda f: f.consumi.update({CONTO_GAS: None}),
        lambda f: f.consumi.update({CONTO_GAS: [{"type": "GAS", "consumptions": None}]}),
        lambda f: f.consumi.update({CONTO_LUCE: [None]}),
        lambda f: f.autoletture.update({CONTO_GAS: []}),
        lambda f: f.autoletture.update({CONTO_GAS: {"valoreAutolettura": "n/d"}}),
        lambda f: setattr(f, "avvisi", None),
        lambda f: setattr(f, "avvisi", "x"),
        lambda f: setattr(f, "avvisi", [None, 3, {"date": "31/02/2026", "description": 5}]),
    ],
)
async def test_risposte_strane(
    hass: HomeAssistant,
    finto: FintoPlenitude,
    modifica,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dati inattesi non bloccano l'integrazione: si carica, senza errori imprevisti."""
    modifica(finto)
    entry = await _avvia(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert "Unexpected error" not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.parametrize("conti", [None, [], [None, "x", 3]])
async def test_elenco_conti_non_valido(
    hass: HomeAssistant, finto: FintoPlenitude, conti, caplog: pytest.LogCaptureFixture
) -> None:
    """Un elenco dei conti vuoto o non valido è un errore temporaneo: si riprova più tardi
    senza cancellare nulla e senza chiedere un nuovo accesso."""
    finto.conti = conti
    entry = await _avvia(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert "Unexpected error" not in caplog.text


async def test_elenco_vuoto_dopo_avvio_non_cancella(
    hass: HomeAssistant, finto: FintoPlenitude, hass_storage: dict
) -> None:
    """Se dopo l'avvio l'elenco arriva vuoto, sensori e cache delle bollette restano."""
    entry = await _avvia(hass)
    chiave = f"{DOMAIN}.{entry.entry_id}.bollette"
    lette = len(hass_storage[chiave]["data"]["bollette"])
    finto.conti = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not entry.runtime_data.last_update_success
    assert len(hass_storage[chiave]["data"]["bollette"]) == lette
    assert entry.runtime_data.data.conti
