"""Accesso ai dati Plenitude.

Questo modulo definisce l'interfaccia con cui il resto dell'integrazione ottiene le
bollette. Al momento è implementata solo la **sorgente locale** (`SorgenteCartella`):
le bollette PDF vengono lette da una cartella, oppure depositate lì da un'automazione
(ad esempio dall'integrazione IMAP di Home Assistant quando arriva la mail di Plenitude).

Una futura `SorgenteApp` che scarica le bollette direttamente dall'app Plenitude dovrà
implementare la stessa interfaccia `SorgenteBollette`: così il coordinatore e i sensori
non cambiano. Lo scheletro è lasciato apposta da completare.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class PlenitudeError(Exception):
    """Errore generico nell'accesso ai dati Plenitude."""


class SorgenteBollette(ABC):
    """Fornisce i PDF delle bollette, indipendentemente da dove arrivano."""

    @abstractmethod
    async def elenco_pdf(self) -> list[bytes]:
        """Ritorna il contenuto di tutte le bollette PDF disponibili."""


class SorgenteCartella(SorgenteBollette):
    """Legge le bollette PDF da una cartella del filesystem."""

    def __init__(self, cartella: Path) -> None:
        """Inizializza la sorgente sulla cartella indicata."""
        self._cartella = cartella

    async def elenco_pdf(self) -> list[bytes]:
        """Legge tutti i file .pdf della cartella (eseguito in un thread)."""
        import asyncio

        return await asyncio.get_running_loop().run_in_executor(None, self._leggi)

    def _leggi(self) -> list[bytes]:
        if not self._cartella.is_dir():
            raise PlenitudeError(f"cartella delle bollette non trovata: {self._cartella}")
        return [
            f.read_bytes()
            for f in sorted(self._cartella.iterdir())
            if f.suffix.lower() == ".pdf" and f.is_file()
        ]


class SorgenteApp(SorgenteBollette):
    """Scarica le bollette dall'app Plenitude (DA COMPLETARE).

    L'accesso all'app usa l'autenticazione SAP Gigya con verifica via email e un token
    di sessione firmato; ottenuto il token, le bollette si elencano e si scaricano dalle
    API dell'app. Questa classe è lasciata come scheletro: va completata con il flusso di
    login e le chiamate, mantenendo la firma di `elenco_pdf`.
    """

    def __init__(self, email: str, password: str) -> None:
        """Inizializza la sorgente con le credenziali del portale Plenitude."""
        self._email = email
        self._password = password

    async def elenco_pdf(self) -> list[bytes]:
        """Da implementare: login all'app e download dei PDF delle bollette."""
        raise PlenitudeError(
            "accesso all'app Plenitude non ancora disponibile: usa la sorgente a cartella"
        )
