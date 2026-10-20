"""Test della configurazione da interfaccia."""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import patch

from custom_components.plenitude.api import PlenitudeError
from custom_components.plenitude.const import (
    CONF_CARTELLA_BOLLETTE,
    CONF_CODICE,
    CONF_CONTO_GAS,
    CONF_EMAIL,
    CONF_INTERVALLO_ORE,
    CONF_PASSWORD,
    CONF_PDR_MYITALGAS,
    CONF_REINVIA,
    CONF_SESSIONE,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import crea_entry, crea_entry_account, prepara_cartella
from .plenitude_finto import CODICE, CONTI, EMAIL, PASSWORD, SESSIONE, FintoPlenitude


async def _menu(hass: HomeAssistant, scelta: str):
    risultato = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert risultato["type"] is FlowResultType.MENU
    assert risultato["menu_options"] == ["account", "cartella"]
    return await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {"next_step_id": scelta}
    )


# --- Cartella ---------------------------------------------------------------------------


async def test_configurazione(hass: HomeAssistant) -> None:
    """Configurazione con cartella e intervallo."""
    prepara_cartella(hass, [])
    risultato = await _menu(hass, "cartella")
    assert risultato["type"] is FlowResultType.FORM
    assert risultato["step_id"] == "cartella"
    with patch("custom_components.plenitude.async_setup_entry", return_value=True) as setup:
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"],
            {CONF_CARTELLA_BOLLETTE: "plenitude/bollette", CONF_INTERVALLO_ORE: 6},
        )
        await hass.async_block_till_done()
    assert risultato["type"] is FlowResultType.CREATE_ENTRY
    assert risultato["data"] == {CONF_CARTELLA_BOLLETTE: "plenitude/bollette"}
    assert risultato["options"] == {
        CONF_CARTELLA_BOLLETTE: "plenitude/bollette",
        CONF_INTERVALLO_ORE: 6,
    }
    assert len(setup.mock_calls) == 1


async def test_istanza_unica(hass: HomeAssistant) -> None:
    """La cartella non si configura due volte."""
    crea_entry(hass)
    risultato = await _menu(hass, "cartella")
    assert risultato["type"] is FlowResultType.ABORT
    assert risultato["reason"] == "already_configured"


async def test_cartella_con_account_presente(hass: HomeAssistant) -> None:
    """Un account già configurato non impedisce di aggiungere la cartella."""
    crea_entry_account(hass)
    risultato = await _menu(hass, "cartella")
    assert risultato["type"] is FlowResultType.FORM


async def test_opzioni(hass: HomeAssistant) -> None:
    """Le opzioni vengono salvate e l'integrazione ricaricata."""
    prepara_cartella(hass, [])
    entry = crea_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    risultato = await hass.config_entries.options.async_init(entry.entry_id)
    assert risultato["type"] is FlowResultType.FORM
    risultato = await hass.config_entries.options.async_configure(
        risultato["flow_id"],
        {
            CONF_CARTELLA_BOLLETTE: "plenitude/bollette",
            CONF_INTERVALLO_ORE: 24,
            CONF_PDR_MYITALGAS: "entry_myitalgas",
        },
    )
    await hass.async_block_till_done()
    assert risultato["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_INTERVALLO_ORE] == 24
    assert entry.options[CONF_PDR_MYITALGAS] == "entry_myitalgas"
    assert entry.state is ConfigEntryState.LOADED


# --- Account ----------------------------------------------------------------------------


async def _fino_al_codice(hass: HomeAssistant):
    risultato = await _menu(hass, "account")
    assert risultato["type"] is FlowResultType.FORM
    assert risultato["step_id"] == "account"
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_EMAIL: f" {EMAIL} ", CONF_PASSWORD: PASSWORD}
    )
    assert risultato["type"] is FlowResultType.FORM
    assert risultato["step_id"] == "codice"
    assert risultato["description_placeholders"] == {"email": "ma********@example.com"}
    return risultato


async def test_account(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Email e password, codice di verifica, opzioni → entry con la sessione (senza password)."""
    risultato = await _fino_al_codice(hass)
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_CODICE: CODICE}
    )
    assert risultato["type"] is FlowResultType.FORM
    assert risultato["step_id"] == "opzioni"
    with patch("custom_components.plenitude.async_setup_entry", return_value=True):
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"],
            {CONF_INTERVALLO_ORE: 6, CONF_PDR_MYITALGAS: "entry_myitalgas"},
        )
        await hass.async_block_till_done()
    assert risultato["type"] is FlowResultType.CREATE_ENTRY
    assert risultato["title"] == f"Plenitude ({EMAIL})"
    assert risultato["result"].unique_id == SESSIONE.uid
    assert risultato["data"] == {CONF_EMAIL: EMAIL, CONF_SESSIONE: asdict(SESSIONE)}
    assert PASSWORD not in str(risultato["data"])
    assert risultato["options"] == {
        CONF_INTERVALLO_ORE: 6,
        CONF_PDR_MYITALGAS: "entry_myitalgas",
    }


async def test_account_senza_verifica(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Se Gigya non chiede il codice si passa direttamente alle opzioni."""
    finto.richiede_verifica = False
    risultato = await _menu(hass, "account")
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert risultato["step_id"] == "opzioni"


async def test_account_errori_accesso(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Password errata, account bloccato, rete assente."""
    risultato = await _menu(hass, "account")
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "sbagliata"}
    )
    assert risultato["errors"] == {"base": "invalid_auth"}
    finto.account_bloccato = True
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert risultato["errors"] == {"base": "account_bloccato"}
    finto.account_bloccato = False
    with patch(
        "custom_components.plenitude.config_flow.ClientPlenitude.accedi",
        side_effect=PlenitudeError("rete"),
    ):
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert risultato["errors"] == {"base": "cannot_connect"}
    with patch(
        "custom_components.plenitude.config_flow.ClientPlenitude.accedi",
        side_effect=RuntimeError("bug"),
    ):
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert risultato["errors"] == {"base": "unknown"}
    # dopo gli errori l'accesso riesce
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert risultato["step_id"] == "codice"


async def test_codice_errato_e_reinvio(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Codice errato o vuoto → errore; si può chiedere un nuovo codice e usarlo."""
    risultato = await _fino_al_codice(hass)
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_CODICE: "000000"}
    )
    assert risultato["errors"] == {CONF_CODICE: "codice_errato"}
    risultato = await hass.config_entries.flow.async_configure(risultato["flow_id"], {})
    assert risultato["errors"] == {CONF_CODICE: "codice_errato"}
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_REINVIA: True}
    )
    assert risultato["step_id"] == "codice"
    assert risultato["errors"] == {}
    assert finto.codici_inviati == 2
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_CODICE: CODICE, CONF_REINVIA: False}
    )
    assert risultato["step_id"] == "opzioni"


async def test_verifica_scaduta(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Errore della verifica diverso dal codice errato → messaggio per ricominciare."""
    risultato = await _fino_al_codice(hass)
    with patch(
        "custom_components.plenitude.config_flow.ClientPlenitude.verifica_codice",
        side_effect=PlenitudeError("scaduta"),
    ):
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"], {CONF_CODICE: CODICE}
        )
    assert risultato["errors"] == {"base": "verifica_scaduta"}
    with patch(
        "custom_components.plenitude.config_flow.ClientPlenitude.invia_codice",
        side_effect=PlenitudeError("scaduta"),
    ):
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"], {CONF_REINVIA: True}
        )
    assert risultato["errors"] == {"base": "verifica_scaduta"}


async def test_account_gia_configurato(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Lo stesso account non si configura due volte, e non riceve un codice inutile."""
    crea_entry_account(hass)
    risultato = await _menu(hass, "account")
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert risultato["type"] is FlowResultType.ABORT
    assert risultato["reason"] == "already_configured"
    assert finto.codici_inviati == 0


async def test_invio_codice_non_riuscito(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Se il codice non si riesce a inviare si resta sul modulo di accesso con un errore."""
    risultato = await _menu(hass, "account")
    with patch(
        "custom_components.plenitude.config_flow.ClientPlenitude.invia_codice",
        side_effect=PlenitudeError("rete"),
    ):
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert risultato["step_id"] == "account"
    assert risultato["errors"] == {"base": "cannot_connect"}


async def test_reauth(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Nuovo accesso: la sessione viene sostituita e l'integrazione ricaricata."""
    entry = crea_entry_account(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_SESSIONE: {**asdict(SESSIONE), "token": "vecchio"}}
    )
    risultato = await entry.start_reauth_flow(hass)
    assert risultato["step_id"] == "reauth_confirm"
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert risultato["step_id"] == "codice"
    with patch("custom_components.plenitude.async_setup_entry", return_value=True):
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"], {CONF_CODICE: CODICE}
        )
        await hass.async_block_till_done()
    assert risultato["type"] is FlowResultType.ABORT
    assert risultato["reason"] == "reauth_successful"
    assert entry.data[CONF_SESSIONE] == asdict(SESSIONE)
    assert entry.title == f"Plenitude ({EMAIL})"


async def test_reauth_altro_account(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Il nuovo accesso deve essere sullo stesso account."""
    entry = crea_entry_account(hass)
    hass.config_entries.async_update_entry(entry, unique_id="un-altro-uid")
    risultato = await entry.start_reauth_flow(hass)
    risultato = await hass.config_entries.flow.async_configure(
        risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert risultato["type"] is FlowResultType.ABORT
    assert risultato["reason"] == "account_diverso"
    assert finto.codici_inviati == 0


async def test_opzioni_account(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Le opzioni dell'account non chiedono la cartella; il conto gas solo se più di uno."""
    entry = crea_entry_account(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    risultato = await hass.config_entries.options.async_init(entry.entry_id)
    campi = {str(k) for k in risultato["data_schema"].schema}
    assert CONF_CARTELLA_BOLLETTE not in campi
    assert CONF_CONTO_GAS not in campi
    risultato = await hass.config_entries.options.async_configure(
        risultato["flow_id"], {CONF_INTERVALLO_ORE: 24}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options == {CONF_INTERVALLO_ORE: 24}
    assert entry.state is ConfigEntryState.LOADED

    # con due conti gas si può scegliere quale collegare a MyItalgas
    finto.conti.append({**CONTI[0], "codice": "500000000008"})
    await entry.runtime_data.async_refresh()
    risultato = await hass.config_entries.options.async_init(entry.entry_id)
    campi = {str(k) for k in risultato["data_schema"].schema}
    assert CONF_CONTO_GAS in campi
    risultato = await hass.config_entries.options.async_configure(
        risultato["flow_id"], {CONF_INTERVALLO_ORE: 24, CONF_CONTO_GAS: "500000000008"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options[CONF_CONTO_GAS] == "500000000008"


async def test_invio_codice_account_bloccato(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Se l'account si blocca all'invio del codice, lo si dice chiaramente."""
    from custom_components.plenitude.api import AccountBloccato

    risultato = await _menu(hass, "account")
    with patch(
        "custom_components.plenitude.config_flow.ClientPlenitude.invia_codice",
        side_effect=AccountBloccato("bloccato"),
    ):
        risultato = await hass.config_entries.flow.async_configure(
            risultato["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert risultato["errors"] == {"base": "account_bloccato"}


async def test_opzioni_mantengono_conto_gas_se_non_caricata(
    hass: HomeAssistant, finto: FintoPlenitude
) -> None:
    """Salvando le opzioni a integrazione non caricata, il conto gas scelto resta."""
    entry = crea_entry_account(hass, **{CONF_CONTO_GAS: "500000000008"})
    risultato = await hass.config_entries.options.async_init(entry.entry_id)
    risultato = await hass.config_entries.options.async_configure(
        risultato["flow_id"], {CONF_INTERVALLO_ORE: 6}
    )
    assert entry.options[CONF_CONTO_GAS] == "500000000008"
