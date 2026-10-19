"""Costanti dell'integrazione Plenitude."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "plenitude"

CONF_EMAIL: Final = "email"
CONF_CODICE: Final = "codice"
CONF_CARTELLA_BOLLETTE: Final = "cartella_bollette"
CONF_PDR_MYITALGAS: Final = "config_entry_myitalgas"
CONF_INTERVALLO_ORE: Final = "intervallo_ore"

INTERVALLO_ORE_DEFAULT: Final = 12
INTERVALLO_ORE_MIN: Final = 1
INTERVALLO_ORE_MAX: Final = 168

# Cartella (sotto la config di Home Assistant) dove cercare le bollette PDF.
CARTELLA_BOLLETTE_DEFAULT: Final = "plenitude/bollette"

# IVA: agevolata (consumi) e ordinaria (quote fisse e consumi oltre 480 Smc/anno).
IVA_AGEVOLATA: Final = 1.10
IVA_ORDINARIA: Final = 1.22

STORAGE_VERSION: Final = 1
