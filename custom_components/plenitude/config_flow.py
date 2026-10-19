"""Configurazione dell'integrazione Plenitude dall'interfaccia."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    ConfigEntrySelector,
    ConfigEntrySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CARTELLA_BOLLETTE_DEFAULT,
    CONF_CARTELLA_BOLLETTE,
    CONF_INTERVALLO_ORE,
    CONF_PDR_MYITALGAS,
    DOMAIN,
    INTERVALLO_ORE_DEFAULT,
    INTERVALLO_ORE_MAX,
    INTERVALLO_ORE_MIN,
)

MYITALGAS_DOMAIN = "myitalgas"


def _schema_opzioni(valori: dict[str, Any], includi_cartella: bool) -> vol.Schema:
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
    return vol.Schema(campi)


def _normalizza(user_input: dict[str, Any]) -> dict[str, Any]:
    opzioni = {
        CONF_INTERVALLO_ORE: int(user_input[CONF_INTERVALLO_ORE]),
    }
    if CONF_CARTELLA_BOLLETTE in user_input:
        opzioni[CONF_CARTELLA_BOLLETTE] = user_input[CONF_CARTELLA_BOLLETTE].strip()
    if user_input.get(CONF_PDR_MYITALGAS):
        opzioni[CONF_PDR_MYITALGAS] = user_input[CONF_PDR_MYITALGAS]
    return opzioni


class PlenitudeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flusso di configurazione Plenitude."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PlenitudeOptionsFlow:
        """Opzioni dell'integrazione."""
        return PlenitudeOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Imposta la cartella delle bollette e il collegamento a MyItalgas."""
        self._async_abort_entries_match()
        if user_input is not None:
            cartella = user_input[CONF_CARTELLA_BOLLETTE].strip()
            opzioni = _normalizza({**user_input, CONF_CARTELLA_BOLLETTE: cartella})
            return self.async_create_entry(
                title="Plenitude",
                data={CONF_CARTELLA_BOLLETTE: Path(cartella).as_posix()},
                options=opzioni,
            )
        return self.async_show_form(
            step_id="user", data_schema=_schema_opzioni({}, includi_cartella=True)
        )


class PlenitudeOptionsFlow(OptionsFlowWithReload):
    """Opzioni: cartella, intervallo e collegamento a MyItalgas."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Modifica delle opzioni."""
        if user_input is not None:
            return self.async_create_entry(data=_normalizza(user_input))
        return self.async_show_form(
            step_id="init",
            data_schema=_schema_opzioni(dict(self.config_entry.options), includi_cartella=True),
        )
