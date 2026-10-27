"""Configurazione dell'integrazione Plenitude dall'interfaccia."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    ConfigEntrySelector,
    ConfigEntrySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .account import GAS
from .api import (
    AccountBloccato,
    ClientPlenitude,
    CodiceErrato,
    PlenitudeAuthError,
    PlenitudeError,
    Sessione,
    VerificaInCorso,
)
from .const import (
    CARTELLA_BOLLETTE_DEFAULT,
    CONF_CARTELLA_BOLLETTE,
    CONF_CODICE,
    CONF_CONTO_GAS,
    CONF_EMAIL,
    CONF_INTERVALLO_ORE,
    CONF_PASSWORD,
    CONF_PDR_MYITALGAS,
    CONF_PREZZO_PANNELLO,
    CONF_REINVIA,
    CONF_SESSIONE,
    DOMAIN,
    INTERVALLO_ORE_DEFAULT,
    INTERVALLO_ORE_MAX,
    INTERVALLO_ORE_MIN,
)

_LOGGER = logging.getLogger(__name__)

MYITALGAS_DOMAIN = "myitalgas"


def _schema_opzioni(
    valori: dict[str, Any],
    includi_cartella: bool,
    conti_gas: list[SelectOptionDict] | None = None,
    includi_pannello: bool = False,
) -> vol.Schema:
    campi: dict[Any, Any] = {}
    if includi_cartella:
        campi[
            vol.Required(
                CONF_CARTELLA_BOLLETTE,
                default=valori.get(CONF_CARTELLA_BOLLETTE, CARTELLA_BOLLETTE_DEFAULT),
            )
        ] = TextSelector()
    campi[
        vol.Required(
            CONF_INTERVALLO_ORE, default=valori.get(CONF_INTERVALLO_ORE, INTERVALLO_ORE_DEFAULT)
        )
    ] = NumberSelector(
        NumberSelectorConfig(
            min=INTERVALLO_ORE_MIN,
            max=INTERVALLO_ORE_MAX,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="h",
        )
    )
    campi[
        vol.Optional(
            CONF_PDR_MYITALGAS,
            description={"suggested_value": valori.get(CONF_PDR_MYITALGAS)},
        )
    ] = ConfigEntrySelector(ConfigEntrySelectorConfig(integration=MYITALGAS_DOMAIN))
    if conti_gas and len(conti_gas) > 1:
        campi[
            vol.Optional(
                CONF_CONTO_GAS,
                description={"suggested_value": valori.get(CONF_CONTO_GAS)},
            )
        ] = SelectSelector(
            SelectSelectorConfig(options=conti_gas, mode=SelectSelectorMode.DROPDOWN)
        )
    if includi_pannello:
        campi[
            vol.Optional(
                CONF_PREZZO_PANNELLO,
                description={"suggested_value": valori.get(CONF_PREZZO_PANNELLO)},
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=2,
                step="any",
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="€/kWh",
            )
        )
    return vol.Schema(campi)


def _normalizza(user_input: dict[str, Any]) -> dict[str, Any]:
    opzioni = {
        CONF_INTERVALLO_ORE: int(user_input[CONF_INTERVALLO_ORE]),
    }
    if CONF_CARTELLA_BOLLETTE in user_input:
        opzioni[CONF_CARTELLA_BOLLETTE] = user_input[CONF_CARTELLA_BOLLETTE].strip()
    if user_input.get(CONF_PDR_MYITALGAS):
        opzioni[CONF_PDR_MYITALGAS] = user_input[CONF_PDR_MYITALGAS]
    if user_input.get(CONF_CONTO_GAS):
        opzioni[CONF_CONTO_GAS] = user_input[CONF_CONTO_GAS]
    if user_input.get(CONF_PREZZO_PANNELLO):
        opzioni[CONF_PREZZO_PANNELLO] = float(user_input[CONF_PREZZO_PANNELLO])
    return opzioni


SCHEMA_ACCESSO = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)

SCHEMA_CODICE = vol.Schema(
    {
        vol.Optional(CONF_CODICE): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="one-time-code")
        ),
        vol.Optional(CONF_REINVIA, default=False): BooleanSelector(),
    }
)


class PlenitudeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flusso di configurazione Plenitude."""

    VERSION = 1

    def __init__(self) -> None:
        """Stato del flusso."""
        self._email: str | None = None
        self._verifica: VerificaInCorso | None = None
        self._sessione: Sessione | None = None
        self._errore_invio = "cannot_connect"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PlenitudeOptionsFlow:
        """Opzioni dell'integrazione."""
        return PlenitudeOptionsFlow()

    async def _con_client[T](self, azione: Callable[[ClientPlenitude], Awaitable[T]]) -> T:
        """Esegue un'azione di accesso (solo Gigya, che non usa cookie) con un nuovo client."""
        return await azione(ClientPlenitude(async_get_clientsession(self.hass)))

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Scelta della sorgente delle bollette."""
        return self.async_show_menu(step_id="user", menu_options=["account", "cartella"])

    # --- Account Plenitude ------------------------------------------------------------

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Email e password dell'account Plenitude."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            errors = await self._accedi(self._email, user_input[CONF_PASSWORD])
            if not errors and (risultato := await self._dopo_accesso()) is not None:
                return risultato
            errors = errors or {"base": self._errore_invio}
        return self.async_show_form(
            step_id="account",
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA_ACCESSO, {CONF_EMAIL: self._email} if self._email else {}
            ),
            errors=errors,
        )

    async def _accedi(self, email: str, password: str) -> dict[str, str]:
        """Primo passo dell'accesso; ritorna gli errori da mostrare."""
        try:
            esito = await self._con_client(lambda c: c.accedi(email, password))
        except AccountBloccato:
            return {"base": "account_bloccato"}
        except PlenitudeAuthError:
            return {"base": "invalid_auth"}
        except PlenitudeError as err:
            _LOGGER.debug("Accesso a Plenitude non riuscito: %s", err)
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Errore inatteso nell'accesso a Plenitude")
            return {"base": "unknown"}
        if isinstance(esito, Sessione):
            self._sessione, self._verifica = esito, None
        else:
            self._sessione, self._verifica = None, esito
        return {}

    async def _dopo_accesso(self) -> ConfigFlowResult | None:
        """Dopo email e password: controlla l'account e invia il codice di verifica.

        L'account si controlla prima di inviare il codice, così un account già
        configurato (o diverso da quello da riautenticare) non riceve email inutili.
        Ritorna None se l'invio del codice non riesce.
        """
        if self._sessione is not None:
            return await self._sessione_ottenuta()
        assert self._verifica is not None
        await self._controlla_account(self._verifica.uid)
        verifica = self._verifica
        try:
            self._verifica = await self._con_client(lambda c: c.invia_codice(verifica))
        except AccountBloccato:
            self._errore_invio = "account_bloccato"
            return None
        except PlenitudeError as err:
            _LOGGER.debug("Codice di verifica non inviato: %s", err)
            self._errore_invio = "cannot_connect"
            return None
        except Exception:
            _LOGGER.exception("Errore inatteso nell'invio del codice di verifica")
            self._errore_invio = "unknown"
            return None
        return await self.async_step_codice()

    async def _controlla_account(self, uid: str) -> None:
        """Interrompe il flusso se l'account è già configurato o non è quello da riautenticare."""
        await self.async_set_unique_id(uid)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="account_diverso")
        else:
            self._abort_if_unique_id_configured()

    async def async_step_codice(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Codice di verifica ricevuto per email."""
        assert self._verifica is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            verifica = self._verifica
            if user_input.get(CONF_REINVIA):
                try:
                    self._verifica = await self._con_client(lambda c: c.invia_codice(verifica))
                except PlenitudeError as err:
                    _LOGGER.debug("Nuovo codice non inviato: %s", err)
                    errors["base"] = "verifica_scaduta"
                except Exception:
                    _LOGGER.exception("Errore inatteso nell'invio di un nuovo codice")
                    errors["base"] = "unknown"
            elif not (codice := (user_input.get(CONF_CODICE) or "").strip()):
                errors[CONF_CODICE] = "codice_errato"
            else:
                try:
                    self._sessione = await self._con_client(
                        lambda c: c.verifica_codice(verifica, codice)
                    )
                except CodiceErrato:
                    errors[CONF_CODICE] = "codice_errato"
                except AccountBloccato:
                    errors["base"] = "account_bloccato"
                except PlenitudeError as err:
                    _LOGGER.debug("Verifica del codice non riuscita: %s", err)
                    errors["base"] = "verifica_scaduta"
                except Exception:
                    _LOGGER.exception("Errore inatteso nella verifica del codice")
                    errors["base"] = "unknown"
                else:
                    return await self._sessione_ottenuta()
        return self.async_show_form(
            step_id="codice",
            data_schema=SCHEMA_CODICE,
            errors=errors,
            description_placeholders={"email": self._verifica.email_offuscata},
        )

    async def _sessione_ottenuta(self) -> ConfigFlowResult:
        assert self._sessione is not None
        await self._controlla_account(self._sessione.uid)
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                title=f"Plenitude ({self._email})",
                data_updates={CONF_EMAIL: self._email, CONF_SESSIONE: asdict(self._sessione)},
            )
        return await self.async_step_opzioni()

    async def async_step_opzioni(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Intervallo di aggiornamento e collegamento a MyItalgas."""
        assert self._sessione is not None
        if user_input is not None:
            return self.async_create_entry(
                title=f"Plenitude ({self._email})",
                data={CONF_EMAIL: self._email, CONF_SESSIONE: asdict(self._sessione)},
                options=_normalizza(user_input),
            )
        return self.async_show_form(
            step_id="opzioni", data_schema=_schema_opzioni({}, includi_cartella=False)
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """La sessione è scaduta o è stata revocata: serve un nuovo accesso."""
        self._email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nuovo accesso con email e password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            errors = await self._accedi(self._email, user_input[CONF_PASSWORD])
            if not errors and (risultato := await self._dopo_accesso()) is not None:
                return risultato
            errors = errors or {"base": self._errore_invio}
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA_ACCESSO, {CONF_EMAIL: self._email} if self._email else {}
            ),
            errors=errors,
        )

    # --- Cartella di PDF --------------------------------------------------------------

    async def async_step_cartella(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Imposta la cartella delle bollette e il collegamento a MyItalgas."""
        if any(CONF_SESSIONE not in e.data for e in self._async_current_entries()):
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            cartella = user_input[CONF_CARTELLA_BOLLETTE].strip()
            opzioni = _normalizza({**user_input, CONF_CARTELLA_BOLLETTE: cartella})
            return self.async_create_entry(
                title="Plenitude",
                data={CONF_CARTELLA_BOLLETTE: Path(cartella).as_posix()},
                options=opzioni,
            )
        return self.async_show_form(
            step_id="cartella", data_schema=_schema_opzioni({}, includi_cartella=True)
        )


class PlenitudeOptionsFlow(OptionsFlowWithReload):
    """Opzioni: cartella (solo per le bollette da cartella), intervallo, MyItalgas e
    Corrispettivo Luce per il pannello virtuale (solo per l'account)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Modifica delle opzioni."""
        da_account = CONF_SESSIONE in self.config_entry.data
        if user_input is not None:
            opzioni = _normalizza(user_input)
            # il conto gas si sceglie solo a integrazione caricata: se il campo non c'era
            # si mantiene la scelta precedente
            if (
                da_account
                and CONF_CONTO_GAS not in user_input
                and not self._conti_gas()
                and (precedente := self.config_entry.options.get(CONF_CONTO_GAS))
            ):
                opzioni[CONF_CONTO_GAS] = precedente
            return self.async_create_entry(data=opzioni)
        return self.async_show_form(
            step_id="init",
            data_schema=_schema_opzioni(
                dict(self.config_entry.options),
                includi_cartella=not da_account,
                conti_gas=self._conti_gas() if da_account else None,
                includi_pannello=da_account,
            ),
        )

    def _conti_gas(self) -> list[SelectOptionDict]:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        dati = getattr(coordinator, "data", None)
        conti = getattr(dati, "conti", None) or {}
        return [
            SelectOptionDict(
                value=c.codice,
                label=f"{c.codice} – {c.indirizzo}" if c.indirizzo else c.codice,
            )
            for c in conti.values()
            if c.commodity == GAS
        ]
