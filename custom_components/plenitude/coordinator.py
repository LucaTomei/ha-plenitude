"""Coordinatori Plenitude: leggono bollette e consumi e aggiornano i prezzi del gas in MyItalgas."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
import logging
from pathlib import Path
import re
import time
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
    avvisi: list[account.Avviso] = field(default_factory=list)
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
        # bollette il cui PDF non è disponibile: si riprovano dopo RIPROVA_PDF secondi
        self._pdf_non_disponibili: dict[tuple[str, str], float] = {}
        # unica attività in background: download dei PDF mancanti e lettura di quelli nuovi
        self._lavoro: asyncio.Task | None = None

    async def _async_update_data(self) -> DatiAccount:
        """Legge i dati dell'account."""
        try:
            utente = await self.client.utente()
            dati_utente = utente if isinstance(utente, dict) else {}
            predefinito = str(dati_utente.get("codiceContoDefault") or "").strip()
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

        if not elenco:
            # un account con un conto predefinito ha almeno un conto: risposta anomala
            raise UpdateFailed("elenco dei conti vuoto")
        precedenti = self.data.conti if self.data else {}
        conti: dict[str, Conto] = {}
        for voce in elenco:
            conto = account.conto_da(voce)
            # servizi diversi da luce e gas (es. polizze, wallbox) esclusi
            if conto is not None and conto.commodity is not None:
                conti[conto.codice] = conto
        # i conti si leggono in parallelo, come fa l'app
        await asyncio.gather(*(self._completa(c, precedenti.get(c.codice)) for c in conti.values()))

        if self._lette is None:
            self._lette = await self._carica_memoria()
        self._applica_pdf(conti)
        await self._pianifica_lavoro(conti)
        await self._aggiorna_derivati(conti, consumi=True)
        avvisi = await self._avvisi(
            dati_utente, precedenti_avvisi=self.data.avvisi if self.data else []
        )
        return DatiAccount(conti=conti, avvisi=avvisi, ultimo_aggiornamento=dt_util.utcnow())

    async def _avvisi(
        self, dati_utente: dict[str, Any], precedenti_avvisi: list[account.Avviso]
    ) -> list[account.Avviso]:
        """Comunicazioni dell'area clienti; se non si riescono a leggere restano le precedenti."""
        codice_cliente = str(dati_utente.get("codiceCliente") or "").strip()
        if not codice_cliente:
            return precedenti_avvisi
        try:
            return account.avvisi_da(await self.client.avvisi(codice_cliente))
        except AccountBloccato as err:
            raise UpdateFailed(str(err)) from err
        except PlenitudeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PlenitudeError as err:
            _LOGGER.debug("Comunicazioni non lette: %s", err)
            return precedenti_avvisi

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

    async def _aggiorna_derivati(self, conti: dict[str, Conto], *, consumi: bool) -> None:
        """Prezzi a MyItalgas e statistiche, a partire dai dati dei conti."""
        await self._myitalgas.invia(
            self.config_entry.options.get(CONF_PDR_MYITALGAS), self._periodi_collegati(conti)
        )
        if consumi:
            await statistiche.importa_consumi(self.hass, conti)
        if self.client.sessione is not None:
            await statistiche.importa_spese(self.hass, conti, self.client.sessione.uid)

    async def async_scarica_bollette(self) -> bool:
        """Rilegge l'account e scarica subito le bollette mancanti, attendendo la fine.

        Riprova anche le bollette che in precedenza non erano disponibili. Ritorna se
        l'aggiornamento è riuscito.
        """
        self._pdf_non_disponibili.clear()
        await self.async_refresh()
        # un'attività già in corso può non comprendere le bollette appena ammesse: finita
        # quella, se c'è ancora qualcosa da fare se ne avvia un'altra
        for _ in range(3):
            in_corso = self._lavoro is not None and not self._lavoro.done()
            if not in_corso and (
                not self.data or not await self._pianifica_lavoro(self.data.conti)
            ):
                break
            assert self._lavoro is not None
            await asyncio.wait({self._lavoro})
        return self.last_update_success

    def cartella_conto(self, codice: str) -> Path:
        """Cartella in cui si archiviano i PDF delle bollette di un conto."""
        return self._archivio / codice

    async def _carica_memoria(self) -> dict[str, _Letta]:
        """Bollette già lette, salvate su disco.

        Quelle lette da una versione precedente del lettore restano valide finché non
        vengono rilette in background (vedi `_esamina_archivio`).
        """
        lette: dict[str, _Letta] = {}
        dati = (await self._memoria.async_load()) or {}
        versione = dati.get("versione")
        for nome, voce in (dati.get("bollette") or {}).items():
            try:
                bolletta = bollette.bolletta_da_dict(voce["b"]) if voce["b"] else None
                periodo = (
                    (date.fromisoformat(voce["p"][0]), date.fromisoformat(voce["p"][1]))
                    if voce["p"]
                    else None
                )
                luce = bollette.bolletta_luce_da_dict(voce["l"]) if voce.get("l") else None
                letta_con = int(voce.get("v", versione) or 0)
                lette[nome] = (int(voce["t"]), int(voce["s"]), bolletta, periodo, luce, letta_con)
            except (KeyError, TypeError, ValueError, AttributeError) as err:
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
                    "v": versione,
                }
                for nome, (t, s, b, p, luce, versione) in (self._lette or {}).items()
            },
        }

    def _applica_pdf(self, conti: dict[str, Conto]) -> None:
        """Dati delle bollette PDF (dalla cache, senza leggere file) nei conti."""
        assert self._lette is not None
        for conto in conti.values():
            gas: list[Bolletta] = []
            periodi: dict[str, tuple[date, date]] = {}
            luce: dict[str, BollettaLuce] = {}
            prefisso = f"{conto.codice}/"
            for nome, (_t, _s, bolletta, periodo, letta_luce, _v) in self._lette.items():
                if not nome.startswith(prefisso):
                    continue
                numero = nome[len(prefisso) :].removesuffix(".pdf")
                if bolletta is not None:
                    gas.append(bolletta)
                if periodo is not None:
                    periodi[numero] = periodo
                if letta_luce is not None:
                    luce[numero] = letta_luce
            conto.periodi_bollette = periodi
            if conto.commodity == GAS:
                conto.bollette_pdf = unisci_bollette(gas)
                conto.periodi = bollette.periodi_tariffari(conto.bollette_pdf)
            else:
                conto.bollette_luce = luce
                account.aggiungi_lettura_luce(conto)

    async def _pianifica_lavoro(self, conti: dict[str, Conto]) -> bool:
        """Avvia in background download e letture dei PDF, se servono. Ritorna se è partito."""
        if self._lavoro is not None and not self._lavoro.done():
            return False
        adesso = time.monotonic()
        attese: dict[str, list[tuple[str, str, Path]]] = {}
        for conto in conti.values():
            quante = MAX_BOLLETTE_PDF if conto.commodity == GAS else MAX_BOLLETTE_PDF_LUCE
            attese[conto.codice] = [
                (conto.codice, b.numero, self.cartella_conto(conto.codice) / f"{b.numero}.pdf")
                for b in conto.bollette[:quante]
                if _RE_NUMERO_SICURO.fullmatch(b.numero)
                and adesso - self._pdf_non_disponibili.get((conto.codice, b.numero), -RIPROVA_PDF)
                >= RIPROVA_PDF
            ]
        mancanti, da_leggere, presenti = await self.hass.async_add_executor_job(
            self._esamina_archivio, attese
        )
        # nella cache restano solo i PDF presenti nell'archivio dei conti attuali
        assert self._lette is not None
        orfane = [n for n in self._lette if n not in presenti]
        for nome in orfane:
            del self._lette[nome]
        if orfane:
            self._applica_pdf(conti)
            await self._memoria.async_save(self._dati_memoria())
        if not mancanti and not da_leggere:
            return False
        self._lavoro = self.config_entry.async_create_background_task(
            self.hass, self._lavora(mancanti, da_leggere), f"{DOMAIN} bollette PDF"
        )
        return True

    def _esamina_archivio(
        self, attese: dict[str, list[tuple[str, str, Path]]]
    ) -> tuple[list[tuple[str, str, Path]], list[tuple[str, Path]], set[str]]:
        """PDF da scaricare, PDF da leggere (nuovi, modificati o letti da un lettore più
        vecchio) e PDF presenti, guardando solo data e dimensione dei file."""
        assert self._lette is not None
        mancanti: list[tuple[str, str, Path]] = []
        da_leggere: list[tuple[str, Path]] = []
        presenti: set[str] = set()
        for codice, voci in attese.items():
            for _codice, _numero, percorso in voci:
                if not percorso.is_file():
                    mancanti.append((_codice, _numero, percorso))
            cartella = self.cartella_conto(codice)
            if not cartella.is_dir():
                continue
            for percorso in sorted(cartella.glob("*.pdf")):
                nome = f"{codice}/{percorso.name}"
                try:
                    info = percorso.stat()
                except OSError:
                    continue
                presenti.add(nome)
                letta = self._lette.get(nome)
                if (
                    letta is None
                    or letta[:2] != (info.st_mtime_ns, info.st_size)
                    or letta[5] != bollette.VERSIONE_LETTURA
                ):
                    da_leggere.append((nome, percorso))
        return mancanti, da_leggere, presenti

    async def _lavora(
        self, mancanti: list[tuple[str, str, Path]], da_leggere: list[tuple[str, Path]]
    ) -> None:
        """Scarica i PDF mancanti e legge quelli nuovi, uno alla volta, poi ricalcola i dati.

        Ogni PDF viene letto una volta sola: il risultato resta nella cache su disco.
        """
        da_leggere = list(da_leggere)
        for codice, numero, percorso in mancanti:
            try:
                dati = await self.client.pdf_bolletta(codice, numero)
            except PlenitudeAuthError:
                break  # se ne occupa il prossimo aggiornamento, che chiede un nuovo accesso
            except RisorsaNonDisponibile as err:
                # documento non (ancora) disponibile: si riprova fra un giorno
                _LOGGER.debug("PDF della bolletta %s non disponibile: %s", oscura(numero), err)
                self._pdf_non_disponibili[(codice, numero)] = time.monotonic()
                continue
            except PlenitudeError as err:
                # errore temporaneo: si riprova al prossimo aggiornamento
                _LOGGER.debug("PDF della bolletta %s non scaricato: %s", oscura(numero), err)
                continue
            try:
                await self.hass.async_add_executor_job(_salva, percorso, dati)
            except OSError as err:
                _LOGGER.warning("Impossibile salvare la bolletta %s: %s", oscura(numero), err)
                continue
            da_leggere.append((f"{codice}/{percorso.name}", percorso))
        assert self._lette is not None
        lette = 0
        for nome, percorso in da_leggere:
            letta = await self.hass.async_add_executor_job(_leggi_file, percorso)
            if letta is not None:
                self._lette[nome] = letta
                lette += 1
        _LOGGER.debug("Scaricate %d bollette PDF, lette %d", len(da_leggere) - lette, lette)
        if not lette:
            return
        await self._memoria.async_save(self._dati_memoria())
        if self.data is not None:
            self._applica_pdf(self.data.conti)
            await self._aggiorna_derivati(self.data.conti, consumi=False)
            self.async_set_updated_data(replace(self.data))

    def _periodi_collegati(self, conti: dict[str, Conto]) -> list[PeriodoTariffario]:
        """Periodi tariffari del conto gas collegato a MyItalgas."""
        scelto = self.config_entry.options.get(CONF_CONTO_GAS)
        if scelto in conti and conti[scelto].commodity == GAS:
            return conti[scelto].periodi
        gas = [c for c in conti.values() if c.commodity == GAS]
        return gas[0].periodi if gas else []


def _leggi_file(percorso: Path) -> _Letta | None:
    """Legge un PDF dell'archivio (nel thread di lavoro)."""
    try:
        info = percorso.stat()
        dati = percorso.read_bytes()
    except OSError as err:
        _LOGGER.debug("Bolletta %s non leggibile: %s", percorso.name, err)
        return None
    return (info.st_mtime_ns, info.st_size, *_analizza_pdf(dati), bollette.VERSIONE_LETTURA)


def _salva(percorso: Path, dati: bytes) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    temporaneo = percorso.with_suffix(".tmp")
    temporaneo.write_bytes(dati)
    temporaneo.replace(percorso)


# (data di modifica in ns, dimensione, bolletta gas, periodo fatturato, bolletta luce,
#  versione del lettore)
type _Letta = tuple[int, int, Bolletta | None, tuple[date, date] | None, BollettaLuce | None, int]

# numero di bolletta usabile come nome di file
_RE_NUMERO_SICURO = re.compile(r"[A-Za-z0-9][\w-]*")

# dopo quanto si riprova a scaricare un PDF non disponibile (secondi)
RIPROVA_PDF = 24 * 3600


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
