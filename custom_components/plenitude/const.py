"""Costanti dell'integrazione Plenitude."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "plenitude"

CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_CODICE: Final = "codice"
CONF_REINVIA: Final = "reinvia"
CONF_SESSIONE: Final = "sessione"
CONF_CARTELLA_BOLLETTE: Final = "cartella_bollette"
CONF_PDR_MYITALGAS: Final = "config_entry_myitalgas"
CONF_INTERVALLO_ORE: Final = "intervallo_ore"
CONF_CONTO_GAS: Final = "conto_gas"
CONF_PREZZO_PANNELLO: Final = "prezzo_pannello"

INTERVALLO_ORE_DEFAULT: Final = 12
INTERVALLO_ORE_MIN: Final = 1
INTERVALLO_ORE_MAX: Final = 168

# Cartella (sotto la config di Home Assistant) dove cercare le bollette PDF.
CARTELLA_BOLLETTE_DEFAULT: Final = "plenitude/bollette"

# Cartella (sotto la config di Home Assistant) dove vengono archiviati i PDF scaricati
# dall'account, una sottocartella per conto.
CARTELLA_ARCHIVIO: Final = "plenitude/archivio"
# Quante bollette gas al massimo scaricare per costruire lo storico dei prezzi.
MAX_BOLLETTE_PDF: Final = 24
# Bollette luce da scaricare (dati dell'ultima bolletta, prezzi e spesa di ogni mese).
MAX_BOLLETTE_PDF_LUCE: Final = 24

# IVA: agevolata (consumi) e ordinaria (quote fisse e consumi oltre 480 Smc/anno).
IVA_AGEVOLATA: Final = 1.10
IVA_ORDINARIA: Final = 1.22

# "Adotta un pannello": ogni quanto si legge la produzione istantanea (minuti) e ogni
# quanto i dati dei quarti d'ora (minuti), che arrivano a blocchi durante la giornata.
INTERVALLO_PANNELLO_MINUTI: Final = 15
INTERVALLO_QUARTI_MINUTI: Final = 60
# Giorni di quarti d'ora letti al massimo per aggiornamento (il primo avvio recupera lo
# storico dall'attivazione un po' alla volta).
MAX_GIORNI_PANNELLO: Final = 31

STORAGE_VERSION: Final = 1
