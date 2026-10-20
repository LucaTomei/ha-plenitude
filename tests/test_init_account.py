"""Test dell'integrazione collegata a un account Plenitude."""

from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.plenitude import bollette
from custom_components.plenitude.const import (
    CARTELLA_ARCHIVIO,
    CONF_PDR_MYITALGAS,
    DOMAIN,
)
from custom_components.plenitude.diagnostics import async_get_config_entry_diagnostics
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import crea_entry_account
from .plenitude_finto import CONTI, CONTO_GAS, CONTO_LUCE, EMAIL, SESSIONE, FintoPlenitude


def _stato(hass: HomeAssistant, unique_id: str):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    stato = hass.states.get(entity_id)
    assert stato is not None, unique_id
    return stato


async def _avvia(hass: HomeAssistant, **opzioni):
    entry = crea_entry_account(hass, **opzioni)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _aggiorna(hass: HomeAssistant, entry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_sensori_gas(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Bollette, consumi e letture del conto gas."""
    entry = await _avvia(hass)
    assert entry.state is ConfigEntryState.LOADED
    importo = _stato(hass, f"{CONTO_GAS}_ultima_bolletta_importo")
    assert importo.state == "63.32"
    assert importo.attributes["unit_of_measurement"] == "€"
    assert importo.attributes["numero"] == "9000000004"
    assert importo.attributes["stato_pagamento"] == "PAGATA"
    # il PDF dell'ultima bolletta è stato letto: periodo e consumo fatturato
    assert importo.attributes["periodo_dal"] == "2026-06-01"
    assert importo.attributes["consumo_smc"] == 20
    assert _stato(hass, f"{CONTO_GAS}_ultima_bolletta_scadenza").state == "2026-09-22"
    assert _stato(hass, f"{CONTO_GAS}_ultima_bolletta_emissione").state == "2026-08-28"
    assert _stato(hass, f"{CONTO_GAS}_da_pagare").state == "0.0"
    assert _stato(hass, f"{CONTO_GAS}_bollette_da_pagare").state == "0"
    consumo = _stato(hass, f"{CONTO_GAS}_consumo_ultimo_mese")
    assert consumo.state == "17.0"
    assert consumo.attributes["unit_of_measurement"] == "Smc"
    assert consumo.attributes["mese"] == "2026-09"
    assert _stato(hass, f"{CONTO_GAS}_consumo_12_mesi").state == "271.0"
    lettura = _stato(hass, f"{CONTO_GAS}_lettura_contatore")
    assert lettura.state == "1637"
    assert lettura.attributes["unit_of_measurement"] == "m³"
    assert lettura.attributes["state_class"] == "total_increasing"
    assert lettura.attributes["data_lettura"] == "2026-09-30"
    autolettura = _stato(hass, f"{CONTO_GAS}_prossima_autolettura")
    assert autolettura.state == "2026-11-24"
    assert autolettura.attributes["fino_al"] == "2026-11-28"
    assert _stato(hass, f"{CONTO_GAS}_consumo_annuo").state == "475"
    assert _stato(hass, f"{CONTO_GAS}_bollette_lette").state == "4"


async def test_sensori_luce(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Bollette da pagare e consumi per fascia del conto luce; niente sensori solo-gas."""
    await _avvia(hass)
    assert _stato(hass, f"{CONTO_LUCE}_ultima_bolletta_importo").state == "328.78"
    da_pagare = _stato(hass, f"{CONTO_LUCE}_da_pagare")
    assert da_pagare.state == "349.28"
    assert da_pagare.attributes["prossima_scadenza"] == "2026-09-04"
    assert len(da_pagare.attributes["bollette"]) == 2
    assert _stato(hass, f"{CONTO_LUCE}_bollette_da_pagare").state == "2"
    consumo = _stato(hass, f"{CONTO_LUCE}_consumo_ultimo_mese")
    assert consumo.state == "240.0"
    assert consumo.attributes["unit_of_measurement"] == "kWh"
    assert consumo.attributes["device_class"] == "energy"
    assert consumo.attributes["fascia_f1"] == 96
    registro = er.async_get(hass)
    for chiave in ("prossima_autolettura", "prezzo_gas"):
        assert registro.async_get_entity_id("sensor", DOMAIN, f"{CONTO_LUCE}_{chiave}") is None
    for chiave in ("potenza_impegnata", "prezzo_energia"):
        assert registro.async_get_entity_id("sensor", DOMAIN, f"{CONTO_GAS}_{chiave}") is None
    # i servizi che non sono luce o gas non creano dispositivi
    dispositivi = dr.async_entries_for_config_entry(
        dr.async_get(hass), hass.config_entries.async_entries(DOMAIN)[0].entry_id
    )
    assert sorted(d.name for d in dispositivi) == ["Plenitude", "Plenitude Gas", "Plenitude Luce"]


async def test_pdf_archiviati(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """I PDF delle bollette (gas e ultime luce) vengono scaricati una sola volta."""
    entry = await _avvia(hass)
    assert sorted(finto.pdf_scaricati) == [
        "9000000001",
        "9000000002",
        "9000000003",
        "9000000004",
        "9100000002",
    ]
    cartella = Path(hass.config.path(CARTELLA_ARCHIVIO)) / CONTO_GAS
    assert await hass.async_add_executor_job(lambda: len(list(cartella.glob("*.pdf")))) == 4
    await _aggiorna(hass, entry)
    assert len(finto.pdf_scaricati) == 5


async def test_pdf_non_disponibile(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Un PDF non disponibile non blocca gli altri e non viene richiesto di continuo."""
    finto.bollette[CONTO_GAS].insert(
        0,
        {**finto.bollette[CONTO_GAS][0], "numeroBolletta": "9000000099", "emissione": "01/10/2026"},
    )
    entry = await _avvia(hass)
    assert len(finto.pdf_scaricati) == 5
    await _aggiorna(hass, entry)
    assert len(finto.pdf_scaricati) == 5
    assert _stato(hass, f"{CONTO_GAS}_bollette_lette").state == "4"


async def test_invio_periodi_a_myitalgas(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """I prezzi letti dai PDF scaricati arrivano a MyItalgas."""
    chiamate: list[ServiceCall] = []
    hass.services.async_register("myitalgas", "imposta_periodo_tariffario", chiamate.append)
    await _avvia(hass, **{CONF_PDR_MYITALGAS: "entry_myitalgas"})
    luglio = next(c for c in chiamate if c.data["dal"] == "2026-07-01")
    assert luglio.data["config_entry_id"] == "entry_myitalgas"
    assert luglio.data["prezzo_sc2"] == pytest.approx(1.4764, abs=0.001)
    assert luglio.data["quota_fissa"] == pytest.approx(17.60, abs=0.01)


async def test_sessione_scaduta(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Sessione non più valida → richiesta di un nuovo accesso."""
    finto.sessione_valida = False
    entry = await _avvia(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flussi = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flussi] == [SOURCE_REAUTH]


async def test_sessione_scaduta_durante_aggiornamento(
    hass: HomeAssistant, finto: FintoPlenitude
) -> None:
    """Se la sessione scade dopo l'avvio parte la richiesta di nuovo accesso."""
    entry = await _avvia(hass)
    finto.sessione_valida = False
    entry.runtime_data.client._gysession = None
    await _aggiorna(hass, entry)
    assert not entry.runtime_data.last_update_success
    flussi = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flussi] == [SOURCE_REAUTH]


async def test_portale_non_raggiungibile(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Portale in errore all'avvio → nuovo tentativo più tardi, nessuna richiesta di accesso."""
    finto.errori["/api/c360/init"] = HTTPStatus.SERVICE_UNAVAILABLE
    entry = await _avvia(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_errore_parziale_mantiene_dati(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Un servizio momentaneamente in errore non azzera i sensori che ne dipendono."""
    entry = await _avvia(hass)
    finto.errori["/api/consumptionData"] = HTTPStatus.INTERNAL_SERVER_ERROR
    finto.errori["/api/autoletture"] = HTTPStatus.INTERNAL_SERVER_ERROR
    finto.errori["/c360/api/conti"] = HTTPStatus.INTERNAL_SERVER_ERROR
    await _aggiorna(hass, entry)
    assert entry.runtime_data.last_update_success
    assert _stato(hass, f"{CONTO_GAS}_consumo_ultimo_mese").state == "17.0"
    assert _stato(hass, f"{CONTO_GAS}_lettura_contatore").state == "1637"
    assert _stato(hass, f"{CONTO_LUCE}_ultima_bolletta_importo").state == "328.78"


async def test_conto_nuovo_e_rimosso(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Un conto nuovo crea i suoi sensori; uno che sparisce li rende non disponibili."""
    entry = await _avvia(hass)
    finto.conti.append({**CONTI[1], "codice": "500000000009"})
    await _aggiorna(hass, entry)
    assert _stato(hass, "500000000009_ultima_bolletta_importo").state == "unknown"
    finto.conti = [c for c in finto.conti if c["codice"] != CONTO_LUCE]
    await _aggiorna(hass, entry)
    assert _stato(hass, f"{CONTO_LUCE}_ultima_bolletta_importo").state == "unavailable"
    assert _stato(hass, f"{CONTO_GAS}_ultima_bolletta_importo").state == "63.32"


async def test_nessun_conto(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Account senza conto predefinito → nuovo tentativo più tardi."""
    finto.errori["/api/c360/init"] = HTTPStatus.NO_CONTENT
    entry = await _avvia(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_ultimo_aggiornamento(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Sensore diagnostico dell'account."""
    entry = await _avvia(hass)
    stato = _stato(hass, f"{entry.entry_id}_ultimo_aggiornamento")
    assert stato.state not in ("unknown", "unavailable")


async def test_diagnostica_senza_dati_personali(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """La diagnostica non contiene credenziali, codici, indirizzi né identificativi."""
    entry = await _avvia(hass)
    testo = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for segreto in (
        EMAIL,
        SESSIONE.token,
        SESSIONE.segreto,
        SESSIONE.uid,
        SESSIONE.gmid,
        CONTO_GAS,
        CONTO_LUCE,
        "01234567890123",
        "IT001E00000001",
        "MAT0000000001",
        "VIA ROMA",
    ):
        assert segreto not in testo, segreto
    assert '"commodity": "gas"' in testo


async def test_scaricamento(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Unload pulito."""
    entry = await _avvia(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_riavvio(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Dopo un riavvio: nessun nuovo accesso, nessun download, nessun PDF riletto."""
    from custom_components.plenitude import pdf

    entry = await _avvia(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    jwt_prima = finto.jwt_emessi
    letture_prima = pdf.estrai_testo.call_count
    assert letture_prima == 5
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert pdf.estrai_testo.call_count == letture_prima
    assert entry.state is ConfigEntryState.LOADED
    assert finto.jwt_emessi == jwt_prima + 1
    assert "accounts.login" not in finto.parametri_gigya
    assert len(finto.pdf_scaricati) == 5
    assert _stato(hass, f"{CONTO_GAS}_bollette_lette").state == "4"
    assert _stato(hass, f"{CONTO_GAS}_ultima_bolletta_importo").state == "63.32"


async def test_nessun_avviso_di_home_assistant(
    hass: HomeAssistant, finto: FintoPlenitude, caplog: pytest.LogCaptureFixture
) -> None:
    """Avvio e scaricamento senza avvisi di Home Assistant (sessione HTTP, dispositivi).

    La chiusura della sessione allo scaricamento la fa l'helper di Home Assistant, che nei
    test è sostituito dal simulatore HTTP: qui si verifica che non ci siano avvisi.
    """
    entry = await _avvia(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "closes the Home Assistant aiohttp session" not in caplog.text
    assert "deprecated" not in caplog.text


async def test_arresto_di_home_assistant(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """All'arresto di Home Assistant la sessione HTTP dedicata viene chiusa."""
    from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE

    entry = await _avvia(hass)
    http = entry.runtime_data.client._http
    hass.bus.async_fire(EVENT_HOMEASSISTANT_CLOSE)
    await hass.async_block_till_done()
    assert http.closed


async def test_reauth_dopo_sessione_revocata_poi_ripristino(
    hass: HomeAssistant, finto: FintoPlenitude
) -> None:
    """Sessione revocata → nuovo accesso dall'interfaccia → integrazione di nuovo attiva."""
    from custom_components.plenitude.const import CONF_CODICE, CONF_EMAIL, CONF_PASSWORD

    from .plenitude_finto import CODICE, PASSWORD

    finto.sessione_valida = False
    entry = await _avvia(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flusso = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    finto.sessione_valida = True
    risultato = await hass.config_entries.flow.async_configure(
        flusso["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_CODICE: CODICE}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert risultato["reason"] == "reauth_successful"
    assert entry.state is ConfigEntryState.LOADED
    assert _stato(hass, f"{CONTO_GAS}_ultima_bolletta_importo").state == "63.32"


async def test_cache_bollette(
    hass: HomeAssistant, finto: FintoPlenitude, hass_storage: dict
) -> None:
    """Cache su disco: voci corrotte ignorate, PDF cancellati dimenticati, rimozione pulita."""
    from custom_components.plenitude import pdf

    entry = crea_entry_account(hass)
    chiave = f"{DOMAIN}.{entry.entry_id}.bollette"
    hass_storage[chiave] = {
        "version": 1,
        "key": chiave,
        "data": {
            "versione": bollette.VERSIONE_LETTURA,
            "bollette": {f"{CONTO_GAS}/9000000004.pdf": {"t": "x", "s": 1, "b": None, "p": None}},
        },
    }
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _stato(hass, f"{CONTO_GAS}_bollette_lette").state == "4"
    assert len(hass_storage[chiave]["data"]["bollette"]) == 5

    cartella = Path(hass.config.path(CARTELLA_ARCHIVIO)) / CONTO_GAS
    await hass.async_add_executor_job((cartella / "9000000001.pdf").unlink)
    finto.bollette[CONTO_GAS] = finto.bollette[CONTO_GAS][:3]
    letture = pdf.estrai_testo.call_count
    await _aggiorna(hass, entry)
    assert pdf.estrai_testo.call_count == letture
    assert len(hass_storage[chiave]["data"]["bollette"]) == 4
    assert _stato(hass, f"{CONTO_GAS}_bollette_lette").state == "3"

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert chiave not in hass_storage


async def test_rimozione_dispositivi(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Si possono eliminare solo i dispositivi di forniture non più presenti nell'account."""
    from custom_components.plenitude import async_remove_config_entry_device

    entry = await _avvia(hass)
    dispositivi = {
        codice: d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for _, codice in d.identifiers
    }
    gas, luce, account = (
        dispositivi[CONTO_GAS],
        dispositivi[CONTO_LUCE],
        dispositivi[entry.entry_id],
    )
    assert not await async_remove_config_entry_device(hass, entry, gas)
    assert not await async_remove_config_entry_device(hass, entry, account)
    finto.conti = [c for c in finto.conti if c["codice"] != CONTO_LUCE]
    await _aggiorna(hass, entry)
    assert await async_remove_config_entry_device(hass, entry, luce)


async def test_cache_di_altra_versione(
    hass: HomeAssistant, finto: FintoPlenitude, hass_storage: dict
) -> None:
    """Una cache scritta da un'altra versione del parser viene ignorata e i PDF riletti."""
    from custom_components.plenitude import pdf

    entry = crea_entry_account(hass)
    chiave = f"{DOMAIN}.{entry.entry_id}.bollette"
    hass_storage[chiave] = {
        "version": 1,
        "key": chiave,
        "data": {"versione": 0, "bollette": {f"{CONTO_GAS}/9000000004.pdf": {}}},
    }
    letture = pdf.estrai_testo.call_count
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert pdf.estrai_testo.call_count == letture + 5
    assert hass_storage[chiave]["data"]["versione"] == bollette.VERSIONE_LETTURA


async def test_pdf_in_formato_imprevisto(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Un PDF che fa fallire la lettura viene ignorato senza bloccare l'integrazione."""
    from custom_components.plenitude import bollette

    originale = bollette.analizza

    def analizza(testo: str):
        if "Data di emissione 28/08/2026" in testo:
            raise ValueError("formato imprevisto")
        return originale(testo)

    with patch("custom_components.plenitude.coordinator.bollette.analizza", analizza):
        entry = await _avvia(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert _stato(hass, f"{CONTO_GAS}_bollette_lette").state == "3"


async def test_download_ritentato_dopo_errore_temporaneo(
    hass: HomeAssistant, finto: FintoPlenitude
) -> None:
    """Un errore temporaneo nel download non esclude la bolletta: si riprova dopo."""
    finto.errori_temporanei_pdf = 1
    entry = await _avvia(hass)
    assert len(finto.pdf_scaricati) == 4
    await _aggiorna(hass, entry)
    assert len(finto.pdf_scaricati) == 5
    assert _stato(hass, f"{CONTO_GAS}_bollette_lette").state == "4"


async def test_account_bloccato_durante_aggiornamento(
    hass: HomeAssistant, finto: FintoPlenitude
) -> None:
    """Un blocco temporaneo dell'account non chiede un nuovo accesso: si riprova più tardi."""
    entry = await _avvia(hass)
    finto.blocco_durante_aggiornamento = True
    entry.runtime_data.client._gysession = None
    await _aggiorna(hass, entry)
    assert not entry.runtime_data.last_update_success
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_prezzo_medio(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Prezzo medio effettivo dall'ultima bolletta, in unità accettate dal pannello Energia."""
    from homeassistant.components.energy import validate

    await _avvia(hass)
    luce = _stato(hass, f"{CONTO_LUCE}_prezzo_medio")
    # totale della fornitura (310,78 €), senza i 18 € di canone RAI
    assert float(luce.state) == pytest.approx(310.78 / 470, abs=1e-4)
    assert luce.attributes["importo"] == 310.78
    assert luce.attributes["unit_of_measurement"] == "€/kWh"
    assert luce.attributes["unit_of_measurement"].endswith(validate.ENERGY_PRICE_UNITS)
    assert luce.attributes["periodo_dal"] == "2026-08-01"
    assert luce.attributes["consumo"] == 470
    assert "device_class" not in luce.attributes
    gas = _stato(hass, f"{CONTO_GAS}_prezzo_medio")
    assert float(gas.state) == pytest.approx(63.32 / 20, abs=1e-4)
    assert gas.attributes["unit_of_measurement"] == "€/m³"
    assert gas.attributes["unit_of_measurement"].endswith(validate.GAS_PRICE_UNITS)
    assert gas.attributes["numero"] == "9000000004"


async def test_pulsante_scarica_bollette(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Il pulsante riprova anche le bollette prima non disponibili e attende i download."""
    from .plenitude_finto import PDF_LUCE

    testo = PDF_LUCE.pop("9100000002")
    try:
        entry = await _avvia(hass)
        assert "9100000002" not in finto.pdf_scaricati
        assert _stato(hass, f"{CONTO_LUCE}_prezzo_medio").state == "unknown"
    finally:
        PDF_LUCE["9100000002"] = testo  # ora il documento è disponibile
    pulsante = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_scarica_bollette"
    )
    await hass.services.async_call("button", "press", {"entity_id": pulsante}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "9100000002" in finto.pdf_scaricati
    assert _stato(hass, f"{CONTO_LUCE}_prezzo_medio").state != "unknown"


async def test_pulsante_con_errore(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Se l'account non risponde il pulsante segnala l'errore all'utente."""
    from homeassistant.exceptions import HomeAssistantError

    entry = await _avvia(hass)
    finto.errori["/api/c360/init"] = HTTPStatus.SERVICE_UNAVAILABLE
    pulsante = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_scarica_bollette"
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": pulsante}, blocking=True)


async def test_nessun_pulsante_con_la_cartella(hass: HomeAssistant) -> None:
    """Con le bollette da cartella non c'è il pulsante (non c'è nulla da scaricare)."""
    from .conftest import crea_entry

    entry = crea_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entita = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert not [e for e in entita if e.domain == "button"]


async def test_prezzo_gas(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Prezzo per Smc dell'ultimo mese con tariffe, nello scaglione raggiunto quest'anno."""
    from homeassistant.components.energy import validate

    await _avvia(hass)
    stato = _stato(hass, f"{CONTO_GAS}_prezzo_gas")
    # consumi 2026 finti: 104 + 12 + 8 + 17 + 3 = 144 Smc → secondo scaglione
    assert stato.attributes["scaglione"] == 2
    assert stato.attributes["consumo_anno"] == 144
    assert stato.attributes["mese_tariffa"] == "2026-07"
    assert float(stato.state) == pytest.approx(1.4764, abs=0.001)
    assert stato.attributes["unit_of_measurement"].endswith(validate.GAS_PRICE_UNITS)
    assert (
        er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{CONTO_LUCE}_prezzo_gas") is None
    )


async def test_dettagli_luce_dalla_bolletta(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Dal PDF della bolletta luce: consumi, potenza, prezzo energia e lettura del contatore."""
    from homeassistant.components.energy import validate

    await _avvia(hass)
    importo = _stato(hass, f"{CONTO_LUCE}_ultima_bolletta_importo")
    assert importo.attributes["periodo_dal"] == "2026-08-01"
    assert importo.attributes["consumo_kwh"] == 708
    assert _stato(hass, f"{CONTO_LUCE}_consumo_ultima_bolletta").state == "708.0"
    annuo = _stato(hass, f"{CONTO_LUCE}_consumo_annuo")
    assert annuo.state == "2622.0"
    assert annuo.attributes["unit_of_measurement"] == "kWh"
    potenza = _stato(hass, f"{CONTO_LUCE}_potenza_impegnata")
    assert potenza.state == "3.0"
    assert potenza.attributes["unit_of_measurement"] == "kW"
    assert potenza.attributes["potenza_disponibile"] == 3.3
    prezzo = _stato(hass, f"{CONTO_LUCE}_prezzo_energia")
    assert float(prezzo.state) == pytest.approx((231.42 + 13.23) * 1.10 / 708, abs=1e-4)
    assert prezzo.attributes["unit_of_measurement"].endswith(validate.ENERGY_PRICE_UNITS)
    lettura = _stato(hass, f"{CONTO_LUCE}_lettura_contatore")
    assert lettura.state == str(3526 + 2150 + 3158)
    assert lettura.attributes["unit_of_measurement"] == "kWh"
    assert lettura.attributes["device_class"] == "energy"
    assert lettura.attributes["state_class"] == "total_increasing"
    assert lettura.attributes["fascia_f1"] == 3526
    assert lettura.attributes["data_lettura"] == "2026-09-30"
    lette = _stato(hass, f"{CONTO_LUCE}_bollette_lette")
    assert lette.state == "1"
    assert lette.attributes["periodi"] == ["2026-08-01/2026-09-30"]
    assert _stato(hass, f"{CONTO_GAS}_consumo_ultima_bolletta").state == "20"
