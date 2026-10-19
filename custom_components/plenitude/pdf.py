"""Estrazione del testo dalle bollette PDF.

Usa pdfplumber (Python puro), che ricostruisce le colonne delle tabelle in modo
affidabile: è ciò che serve per leggere il dettaglio dei costi delle bollette Plenitude.
"""

from __future__ import annotations

import io


def estrai_testo(dati: bytes) -> str:
    """Testo di una bolletta PDF, pagina per pagina.

    L'import di pdfplumber è dentro la funzione perché pesante: così non rallenta
    l'avvio di Home Assistant e viene caricato solo quando serve leggere una bolletta.
    """
    import pdfplumber

    with pdfplumber.open(io.BytesIO(dati)) as documento:
        pagine = [pagina.extract_text() or "" for pagina in documento.pages]
    return "\n".join(pagine)
