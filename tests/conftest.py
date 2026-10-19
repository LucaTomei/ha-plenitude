"""Fixture comuni dei test."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.plenitude.const import (
    CONF_CARTELLA_BOLLETTE,
    CONF_INTERVALLO_ORE,
    DOMAIN,
)
from homeassistant.core import HomeAssistant

FIXTURES = Path(__file__).parent / "fixtures"
CARTELLA = "plenitude/bollette"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Abilita le integrazioni personalizzate in ogni test."""


@pytest.fixture(autouse=True)
def _cartella_pulita(hass: HomeAssistant) -> Generator[None]:
    """La config dir dei test è condivisa: si svuota la cartella bollette prima di ogni test."""
    cartella = Path(hass.config.path(CARTELLA))
    if cartella.exists():
        shutil.rmtree(cartella)
    yield
    if cartella.exists():
        shutil.rmtree(cartella)


@pytest.fixture(autouse=True)
def _pdf_come_testo() -> Generator[None]:
    """Nei test il PDF è il testo stesso: si evita di generare veri PDF e di usare pdfplumber."""
    with patch(
        "custom_components.plenitude.coordinator.pdf.estrai_testo",
        side_effect=lambda dati: dati.decode("utf-8"),
    ):
        yield


def prepara_cartella(hass: HomeAssistant, bollette: list[str]) -> Path:
    """Copia le fixture di testo come finti PDF nella cartella delle bollette."""
    cartella = Path(hass.config.path(CARTELLA))
    cartella.mkdir(parents=True, exist_ok=True)
    for nome in bollette:
        shutil.copyfile(FIXTURES / nome, cartella / nome.replace(".txt", ".pdf"))
    return cartella


def crea_entry(hass: HomeAssistant, **opzioni) -> MockConfigEntry:
    """Config entry dell'integrazione Plenitude."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Plenitude",
        data={CONF_CARTELLA_BOLLETTE: CARTELLA},
        options={CONF_CARTELLA_BOLLETTE: CARTELLA, CONF_INTERVALLO_ORE: 12, **opzioni},
    )
    entry.add_to_hass(hass)
    return entry
