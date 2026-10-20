"""Coordinatori Plenitude: leggono bollette e consumi e aggiornano i prezzi del gas in MyItalgas."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import account, bollette, pdf, statistiche
from .account import GAS, Conto
from .api import (
    AccountBloccato,
    ClientPlenitude,
    PlenitudeAuthError,
    PlenitudeError,
    RisorsaNonDisponibile,
    SorgenteBollette,
    oscura,
)
from .bollette import Bolletta, BollettaLuce, PeriodoTariffario
from .const import (
    CONF_CONTO_GAS,
    CONF_INTERVALLO_ORE,
    CONF_PDR_MYITALGAS,
    DOMAIN,
    INTERVALLO_ORE_DEFAULT,
    MAX_BOLLETTE_PDF,
    MAX_BOLLETTE_PDF_LUCE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

MYITALGAS_DOMAIN = "myitalgas"
SERVIZIO_IMPOSTA = "imposta_periodo_tariffario"

type PlenitudeConfigEntry = ConfigEntry[PlenitudeCoordinator | PlenitudeAccountCoordinator]


@dataclass(slots=True)
class DatiPlenitude:
    """Dati esposti alle entità (bollette lette da una cartella)."""

    bollette: list[Bolletta] = field(default_factory=list)
    periodi: list[PeriodoTariffario] = field(default_factory=list)
    ultimo_aggiornamento: datetime | None = None

    @property
    def ultima(self) -> Bolletta | None:
        """Bolletta più recente per data di emissione (o di periodo)."""
        if not self.bollette:
            return None
        return max(self.bollette, key=lambda b: (b.emissione or b.al, b.al))


@dataclass(slots=True)
class DatiAccount:
    """Dati dell'account Plenitude esposti alle entità."""

    conti: dict[str, Conto] = field(default_factory=dict)
    ultimo_aggiornamento: datetime | None = None


def _intervallo(entry: ConfigEntry) -> timedelta:
    return timedelta(hours=entry.options.get(CONF_INTERVALLO_ORE, INTERVALLO_ORE_DEFAULT))


def unisci_bollette(lette: list[Bolletta]) -> list[Bolletta]:
    """Una bolletta per periodo di fatturazione (l'ultima emessa vince), in ordine di periodo."""
    per_periodo: dict[tuple, Bolletta] = {}
    for b in sorted(lette, key=lambda b: b.emissione or b.al):
        per_periodo[(b.dal, b.al)] = b
    return sorted(per_periodo.values(), key=lambda b: b.al)


class _InvioMyItalgas:
    """Invia a MyItalgas i periodi tariffari, ciascuno una sola volta."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._inviati: set[tuple] = set()

    async def invia(self, entry_id: str | None, periodi: list[PeriodoTariffario]) -> None:
        """Crea/aggiorna in MyItalgas i periodi tariffari del gas, se una fornitura è collegata."""
        if not entry_id or not self._hass.services.has_service(MYITALGAS_DOMAIN, SERVIZIO_IMPOSTA):
            return
        for p in periodi:
            firma = (entry_id, p.dal, p.prezzo_sc1, p.prezzo_sc2, p.prezzo_sc3, p.quota_fissa)
            if firma in self._inviati:
                continue
            try:
                await self._hass.services.async_call(
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
            self._inviati.add(firma)


class PlenitudeCoordinator(DataUpdateCoordinator[DatiPlenitude]):
    """Legge periodicamente le bollette da una cartella e ne ricava prezzi e consumi."""

    config_entry: PlenitudeConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: PlenitudeConfigEntry, sorgente: SorgenteBollette
    ) -> None:
        """Inizializza il coordinatore."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=_intervallo(entry),
        )
        self._sorgente = sorgente
        self._myitalgas = _InvioMyItalgas(hass)

    async def _async_update_data(self) -> DatiPlenitude:
        """Legge le bollette e aggiorna i periodi tariffari in MyItalgas."""
        try:
            pdf_grezzi = await self._sorgente.elenco_pdf()
        except PlenitudeError as err:
            raise UpdateFailed(str(err)) from err

        lette = await self.hass.async_add_executor_job(_analizza_tutte, pdf_grezzi)
        elenco = unisci_bollette(lette)
        periodi = bollette.periodi_tariffari(elenco)

        await self._myitalgas.invia(self.config_entry.options.get(CONF_PDR_MYITALGAS), periodi)
        return DatiPlenitude(
            bollette=elenco, periodi=periodi, ultimo_aggiornamento=dt_util.utcnow()
        )


class PlenitudeAccountCoordinator(DataUpdateCoordinator[DatiAccount]):
    """Legge dall'account Plenitude conti, bollette, consumi e letture.

    I PDF delle bollette gas vengono scaricati in background in un archivio locale (una
    sottocartella per conto) e letti per ricavare i prezzi del gas per MyItalgas.
    """

    config_entry: PlenitudeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PlenitudeConfigEntry,
        client: ClientPlenitude,
        archivio: Path,
    ) -> None:
        """Inizializza il coordinatore."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=_intervallo(entry),
        )
        self.client = client
        self._archivio = archivio
        self._myitalgas = _InvioMyItalgas(hass)
        # bollette PDF già lette, per file "<conto>/<numero>.pdf": (data di modifica in ns,
        # dimensione, bolletta gas o None, periodo fatturato o None). Salvate su disco
        # perché leggere un PDF richiede tempo: dopo un riavvio non si rileggono.
        self._memoria: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.bollette"
        )
        self._lette: dict[str, _Letta] | None = None
        self._pdf_non_disponibili: set[tuple[str, str]] = set()
        self._download: asyncio.Task | None = None

    async def _async_update_data(self) -> DatiAccount:
        """Legge i dati dell'account."""
        try:
            utente = await self.client.utente()
            predefinito = str((utente or {}).get("codiceContoDefault") or "").strip()
            if not predefinito:
                raise UpdateFailed("nessun conto associato all'account Plenitude")
            elenco = await self.client.conti(predefinito)
        except AccountBloccato as err:
            # blocco temporaneo per troppi tentativi: si riprova, un nuovo accesso fallirebbe
            raise UpdateFailed(str(err)) from err
        except PlenitudeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PlenitudeError as err:
            raise UpdateFailed(str(err)) from err

        precedenti = self.data.conti if self.data else {}
        conti: dict[str, Conto] = {}
        for voce in elenco:
            conto = account.conto_da(voce)
            # servizi diversi da luce e gas (es. polizze, wallbox) esclusi
            if conto is not None and conto.commodity is not None:
                conti[conto.codice] = conto
        # i conti si leggono in parallelo, come fa l'app
        await asyncio.gather(*(self._completa(c, precedenti.get(c.codice)) for c in conti.values()))

        await self._leggi_pdf(conti)
        await self._myitalgas.invia(
            self.config_entry.options.get(CONF_PDR_MYITALGAS), self._periodi_collegati(conti)
        )
        await statistiche.importa_consumi(self.hass, conti)
        return DatiAccount(conti=conti, ultimo_aggiornamento=dt_util.utcnow())

    async def _completa(self, conto: Conto, precedente: Conto | None) -> None:
        """Forniture, bollette, consumi e letture di un conto, richiesti in parallelo.

        Se una singola lettura non riesce si mantiene il dato precedente: un servizio
        momentaneamente non disponibile non deve azzerare i sensori.
        """
        codice = conto.codice
        nel_log = oscura(codice)
        forniture, elenco_bollette, consumi, autolettura = await asyncio.gather(
            self.client.forniture(codice),
            self.client.bollette(codice),
            self.client.consumi(codice),
            self.client.autolettura(codice),
            return_exceptions=True,
        )
        risultati = (forniture, elenco_bollette, consumi, autolettura)
        for risultato in risultati:
            if isinstance(risultato, AccountBloccato):
                raise UpdateFailed(str(risultato)) from risultato
            if isinstance(risultato, PlenitudeAuthError):
                raise ConfigEntryAuthFailed(str(risultato)) from risultato
        for risultato in risultati:
            if isinstance(risultato, BaseException) and not isinstance(risultato, PlenitudeError):
                raise risultato

        if isinstance(forniture, PlenitudeError):
            _LOGGER.debug("Forniture del conto %s non lette: %s", nel_log, forniture)
            if precedente:
                conto.pdr, conto.pod = precedente.pdr, precedente.pod
                conto.matricola, conto.prodotto = precedente.matricola, precedente.prodotto
        else:
            account.aggiungi_fornitura(conto, forniture)
        if isinstance(elenco_bollette, PlenitudeError):
            _LOGGER.warning("Bollette del conto %s non lette: %s", nel_log, elenco_bollette)
            if precedente:
                conto.bollette = precedente.bollette
        else:
            conto.bollette = account.bollette_da(elenco_bollette)
        if isinstance(consumi, PlenitudeError):
            _LOGGER.debug("Consumi del conto %s non letti: %s", nel_log, consumi)
            if precedente:
                conto.consumi = precedente.consumi
        else:
            conto.consumi = account.consumi_da(consumi, conto.commodity)
        if isinstance(autolettura, PlenitudeError):
            _LOGGER.debug("Letture del conto %s non lette: %s", nel_log, autolettura)
            if precedente:
                conto.lettura, conto.data_lettura = precedente.lettura, precedente.data_lettura
                conto.autolettura_dal = precedente.autolettura_dal
                conto.autolettura_al = precedente.autolettura_al
        else:
            account.aggiungi_autolettura(conto, autolettura)

    async def async_scarica_bollette(self) -> bool:
        """Rilegge l'account e scarica subito le bollette mancanti, attendendo la fine.

        Riprova anche le bollette che in precedenza non erano disponibili. Ritorna se
        l'aggiornamento è riuscito.
        """
        self._pdf_non_disponibili.clear()
        await self.async_refresh()
        if self._download is not None and not self._download.done():
            await asyncio.shield(self._download)
        return self.last_update_success

    def cartella_conto(self, codice: str) -> Path:
        """Cartella in cui si archiviano i PDF delle bollette di un conto."""
        return self._archivio / codice

    async def _carica_memoria(self) -> dict[str, _Letta]:
        lette: dict[str, _Letta] = {}
        dati = (await self._memoria.async_load()) or {}
        if dati.get("versione") != bollette.VERSIONE_LETTURA:
            return lette  # letta con un'altra versione del parser: si rileggono i PDF
        for nome, voce in (dati.get("bollette") or {}).items():
            try:
                bolletta = bollette.bolletta_da_dict(voce["b"]) if voce["b"] else None
                periodo = (
                    (date.fromisoformat(voce["p"][0]), date.fromisoformat(voce["p"][1]))
                    if voce["p"]
                    else None
                )
                luce = bollette.bolletta_luce_da_dict(voce["l"]) if voce["l"] else None
                lette[nome] = (int(voce["t"]), int(voce["s"]), bolletta, periodo, luce)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Voce %s della cache delle bollette ignorata: %s", nome, err)
        return lette

    def _dati_memoria(self) -> dict[str, Any]:
        return {
            "versione": bollette.VERSIONE_LETTURA,
            "bollette": {
                nome: {
                    "t": t,
                    "s": s,
                    "b": bollette.bolletta_a_dict(b) if b else None,
                    "p": [p[0].isoformat(), p[1].isoformat()] if p else None,
                    "l": bollette.bolletta_luce_a_dict(luce) if luce else None,
                }
                for nome, (t, s, b, p, luce) in (self._lette or {}).items()
            },
        }

    async def _leggi_pdf(self, conti: dict[str, Conto]) -> None:
        """Legge le bollette archiviate e avvia il download di quelle mancanti.

        Del gas si scaricano le ultime 24 bollette (prezzi per MyItalgas), della luce le
        ultime 3 (periodo fatturato, per il prezzo medio).
        """
        if self._lette is None:
            self._lette = await self._carica_memoria()
        # conti non più nell'account: le loro bollette escono dalla cache
        orfane = [n for n in self._lette if n.split("/", 1)[0] not in conti]
        for nome in orfane:
            del self._lette[nome]
        cambiate = bool(orfane)
        mancanti: list[tuple[str, str, Path]] = []
        for conto in conti.values():
            cartella = self.cartella_conto(conto.codice)
            quante = MAX_BOLLETTE_PDF if conto.commodity == GAS else MAX_BOLLETTE_PDF_LUCE
            for b in conto.bollette[:quante]:
                percorso = cartella / f"{b.numero}.pdf"
                if (conto.codice, b.numero) in self._pdf_non_disponibili:
                    continue
                if not await self.hass.async_add_executor_job(percorso.is_file):
                    mancanti.append((conto.codice, b.numero, percorso))
            lette, periodi, luce, cambiate_conto = await self.hass.async_add_executor_job(
                self._analizza_cartella, conto.codice, cartella
            )
            cambiate |= cambiate_conto
            conto.periodi_bollette = periodi
            if conto.commodity == GAS:
                conto.bollette_pdf = unisci_bollette(lette)
                conto.periodi = bollette.periodi_tariffari(conto.bollette_pdf)
            else:
                conto.bollette_luce = luce
                account.aggiungi_lettura_luce(conto)
        if cambiate:
            await self._memoria.async_save(self._dati_memoria())
        if mancanti and (self._download is None or self._download.done()):
            self._download = self.config_entry.async_create_background_task(
                self.hass, self._scarica(mancanti), f"{DOMAIN} download bollette"
            )

    def _analizza_cartella(
        self, codice: str, cartella: Path
    ) -> tuple[list[Bolletta], dict[str, tuple[date, date]], dict[str, BollettaLuce], bool]:
        """Legge i PDF di un conto; quelli già letti e non modificati non si rileggono.

        Ritorna le bollette gas, il periodo fatturato di ogni bolletta, le bollette luce
        (per numero) e se la cache è cambiata.
        """
        assert self._lette is not None
        cambiate = False
        presenti: set[str] = set()
        lette: list[Bolletta] = []
        periodi: dict[str, tuple[date, date]] = {}
        luce: dict[str, BollettaLuce] = {}
        if cartella.is_dir():
            for percorso in sorted(cartella.glob("*.pdf")):
                nome = f"{codice}/{percorso.name}"
                try:
                    info = percorso.stat()
                    memorizzata = self._lette.get(nome)
                    if memorizzata is None or memorizzata[:2] != (info.st_mtime_ns, info.st_size):
                        memorizzata = (
                            info.st_mtime_ns,
                            info.st_size,
                            *_analizza_pdf(percorso.read_bytes()),
                        )
                        self._lette[nome] = memorizzata
                        cambiate = True
                except OSError as err:
                    _LOGGER.debug("Bolletta %s non leggibile: %s", percorso, err)
                    continue
                presenti.add(nome)
                if memorizzata[2] is not None:
                    lette.append(memorizzata[2])
                if memorizzata[3] is not None:
                    periodi[percorso.stem] = memorizzata[3]
                if memorizzata[4] is not None:
                    luce[percorso.stem] = memorizzata[4]
        # i PDF cancellati dall'archivio escono dalla cache
        for nome in [n for n in self._lette if n.startswith(f"{codice}/") and n not in presenti]:
            del self._lette[nome]
            cambiate = True
        return lette, periodi, luce, cambiate

    async def _scarica(self, mancanti: list[tuple[str, str, Path]]) -> None:
        """Scarica i PDF mancanti, poi aggiorna i dati."""
        scaricati = 0
        for codice, numero, percorso in mancanti:
            try:
                dati = await self.client.pdf_bolletta(codice, numero)
            except PlenitudeAuthError:
                return  # se ne occupa il prossimo aggiornamento, che chiede un nuovo accesso
            except RisorsaNonDisponibile as err:
                # documento assente: non si richiede più fino al prossimo riavvio
                _LOGGER.debug("PDF della bolletta %s non disponibile: %s", oscura(numero), err)
                self._pdf_non_disponibili.add((codice, numero))
                continue
            except PlenitudeError as err:
                # errore temporaneo: si riprova al prossimo aggiornamento
                _LOGGER.debug("PDF della bolletta %s non scaricato: %s", oscura(numero), err)
                continue
            await self.hass.async_add_executor_job(_salva, percorso, dati)
            scaricati += 1
        _LOGGER.debug("Scaricate %d bollette PDF", scaricati)
        if scaricati:
            await self.async_request_refresh()

    def _periodi_collegati(self, conti: dict[str, Conto]) -> list[PeriodoTariffario]:
        """Periodi tariffari del conto gas collegato a MyItalgas."""
        scelto = self.config_entry.options.get(CONF_CONTO_GAS)
        if scelto in conti and conti[scelto].commodity == GAS:
            return conti[scelto].periodi
        gas = [c for c in conti.values() if c.commodity == GAS]
        return gas[0].periodi if gas else []


def _salva(percorso: Path, dati: bytes) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    temporaneo = percorso.with_suffix(".tmp")
    temporaneo.write_bytes(dati)
    temporaneo.replace(percorso)


# (data di modifica in ns, dimensione, bolletta gas, periodo fatturato, bolletta luce)
type _Letta = tuple[int, int, Bolletta | None, tuple[date, date] | None, BollettaLuce | None]


def _analizza_pdf(
    dati: bytes,
) -> tuple[Bolletta | None, tuple[date, date] | None, BollettaLuce | None]:
    """Bolletta gas, periodo fatturato e bolletta luce di un PDF (None dove non si applica)."""
    try:
        testo = pdf.estrai_testo(dati)
    except Exception as err:
        _LOGGER.warning("Bolletta PDF non leggibile, ignorata: %s", err)
        return None, None, None
    try:
        return (
            bollette.analizza(testo),
            bollette.periodo_fatturazione(testo),
            bollette.analizza_luce(testo),
        )
    except Exception:
        _LOGGER.exception("Bolletta PDF in un formato non previsto, ignorata")
        return None, None, None


def _analizza(dati: bytes) -> Bolletta | None:
    return _analizza_pdf(dati)[0]


def _analizza_tutte(pdf_grezzi: list[bytes]) -> list[Bolletta]:
    """Estrae il testo e analizza ogni PDF; ignora quelli illeggibili o non-gas."""
    return [b for dati in pdf_grezzi if (b := _analizza(dati)) is not None]
