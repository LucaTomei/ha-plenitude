"""Coordinatore Plenitude: legge le bollette e aggiorna i prezzi del gas in MyItalgas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import bollette, pdf
from .api import PlenitudeError, SorgenteBollette
from .bollette import Bolletta, PeriodoTariffario
from .const import (
    CONF_INTERVALLO_ORE,
    CONF_PDR_MYITALGAS,
    DOMAIN,
    INTERVALLO_ORE_DEFAULT,
)

_LOGGER = logging.getLogger(__name__)

MYITALGAS_DOMAIN = "myitalgas"
SERVIZIO_IMPOSTA = "imposta_periodo_tariffario"

type PlenitudeConfigEntry = ConfigEntry[PlenitudeCoordinator]


@dataclass(slots=True)
class DatiPlenitude:
    """Dati esposti alle entità."""

    bollette: list[Bolletta] = field(default_factory=list)
    periodi: list[PeriodoTariffario] = field(default_factory=list)
    ultimo_aggiornamento: datetime | None = None

    @property
    def ultima(self) -> Bolletta | None:
        """Bolletta più recente per data di emissione (o di periodo)."""
        if not self.bollette:
            return None
        return max(self.bollette, key=lambda b: (b.emissione or b.al, b.al))


class PlenitudeCoordinator(DataUpdateCoordinator[DatiPlenitude]):
    """Legge periodicamente le bollette e ne ricava prezzi e consumi."""

    config_entry: PlenitudeConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: PlenitudeConfigEntry, sorgente: SorgenteBollette
    ) -> None:
        """Inizializza il coordinatore."""
        ore = entry.options.get(CONF_INTERVALLO_ORE, INTERVALLO_ORE_DEFAULT)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(hours=ore),
        )
        self._sorgente = sorgente
        self._periodi_inviati: set[tuple] = set()

    async def _async_update_data(self) -> DatiPlenitude:
        """Legge le bollette e aggiorna i periodi tariffari in MyItalgas."""
        try:
            pdf_grezzi = await self._sorgente.elenco_pdf()
        except PlenitudeError as err:
            raise UpdateFailed(str(err)) from err

        lette = await self.hass.async_add_executor_job(_analizza_tutte, pdf_grezzi)
        # una bolletta per periodo di fatturazione; l'ultima emessa vince in caso di doppioni
        per_periodo: dict[tuple, Bolletta] = {}
        for b in sorted(lette, key=lambda b: b.emissione or b.al):
            per_periodo[(b.dal, b.al)] = b
        elenco = sorted(per_periodo.values(), key=lambda b: b.al)
        periodi = bollette.periodi_tariffari(elenco)

        await self._aggiorna_myitalgas(periodi)
        return DatiPlenitude(
            bollette=elenco, periodi=periodi, ultimo_aggiornamento=dt_util.utcnow()
        )

    async def _aggiorna_myitalgas(self, periodi: list[PeriodoTariffario]) -> None:
        """Crea/aggiorna in MyItalgas i periodi tariffari del gas, se una fornitura è collegata."""
        entry_id = self.config_entry.options.get(CONF_PDR_MYITALGAS)
        if not entry_id or not self.hass.services.has_service(MYITALGAS_DOMAIN, SERVIZIO_IMPOSTA):
            return
        for p in periodi:
            firma = (entry_id, p.dal, p.prezzo_sc1, p.prezzo_sc2, p.prezzo_sc3, p.quota_fissa)
            if firma in self._periodi_inviati:
                continue
            try:
                await self.hass.services.async_call(
                    MYITALGAS_DOMAIN,
                    SERVIZIO_IMPOSTA,
                    {
                        "config_entry_id": entry_id,
                        "dal": p.dal.isoformat(),
                        "prezzo_sc1": p.prezzo_sc1,
                        "prezzo_sc2": p.prezzo_sc2,
                        "prezzo_sc3": p.prezzo_sc3,
                        "quota_fissa": p.quota_fissa,
                    },
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.warning("Invio del periodo %s a MyItalgas non riuscito: %s", p.dal, err)
                continue
            self._periodi_inviati.add(firma)


def _analizza_tutte(pdf_grezzi: list[bytes]) -> list[Bolletta]:
    """Estrae il testo e analizza ogni PDF; ignora quelli illeggibili o non-gas."""
    lette: list[Bolletta] = []
    for dati in pdf_grezzi:
        try:
            testo = pdf.estrai_testo(dati)
        except Exception as err:
            _LOGGER.warning("Bolletta PDF non leggibile, ignorata: %s", err)
            continue
        if (b := bollette.analizza(testo)) is not None:
            lette.append(b)
    return lette
