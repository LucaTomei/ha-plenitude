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

from . import account, bollette, pannello, pdf, statistiche
from .account import GAS, LUCE, Conto
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
    CONF_PREZZO_PANNELLO,
    DOMAIN,
    INTERVALLO_ORE_DEFAULT,
    INTERVALLO_QUARTI_MINUTI,
    IVA_AGEVOLATA,
    MAX_BOLLETTE_PDF,
    MAX_BOLLETTE_PDF_LUCE,
    MAX_GIORNI_PANNELLO,
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

    Per le forniture di luce con "Adotta un pannello" legge anche la produzione dei
    pannelli e i consumi di ogni quarto d'ora; questi si aggiornano più spesso
    (`async_aggiorna_pannelli`) e si conservano su disco dall'attivazione del pannello.
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
        # un aggiornamento è in corso: i risultati del background li applica lui
        self._in_aggiornamento = False
        self._ricalcolo_pendente = False
        # "Adotta un pannello": per conto, giorno di attivazione e quarti d'ora di ogni
        # giorno (righe come le riporta l'area clienti), salvati su disco
        self._memoria_pannelli: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.pannelli"
        )
        self._pannelli: dict[str, dict[str, Any]] | None = None
        self._lettura_quarti: dict[str, float] = {}
        self._canoni: dict[str, float | None] = {}
        self._lock_pannelli = asyncio.Lock()

    async def _async_update_data(self) -> DatiAccount:
        """Legge i dati dell'account."""
        self._in_aggiornamento = True
        try:
            return await self._aggiorna()
        finally:
            self._in_aggiornamento = False

    async def _aggiorna(self) -> DatiAccount:
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
        await self._leggi_pannelli(conti, precedenti, dettagli=True)

        if self._lette is None:
            self._lette = await self._carica_memoria()
        self._applica_pdf(conti)
        await self._pianifica_lavoro(conti)
        avvisi = await self._avvisi(
            dati_utente, precedenti_avvisi=self.data.avvisi if self.data else []
        )
        # i PDF letti in background mentre l'aggiornamento è in corso finiscono nella
        # cache: prima di chiudere si riapplicano, finché non ne arrivano altri
        consumi = True
        while True:
            self._ricalcolo_pendente = False
            self._applica_pdf(conti)
            await self._aggiorna_derivati(conti, consumi=consumi)
            consumi = False
            if not self._ricalcolo_pendente:
                break
        self._applica_pdf(conti)
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
                conto.codice_fornitura = precedente.codice_fornitura
                conto.condizioni_dal = precedente.condizioni_dal
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
        # il valore dell'autoconsumo dipende dalle bollette lette: si ricalcola anche qui
        await self._derivati_pannelli(conti)

    async def _derivati_pannelli(self, conti: dict[str, Conto]) -> None:
        """Totali e statistiche dei pannelli, mai mentre i loro dati si stanno leggendo."""
        async with self._lock_pannelli:
            adesso = dt_util.utcnow()
            for conto in conti.values():
                if conto.pannello is not None:
                    pannello.aggiorna_riepiloghi(
                        conto.pannello,
                        pannello.prezzo_pannello(conto, self.prezzo_pannello),
                        IVA_AGEVOLATA,
                        adesso,
                    )
            await statistiche.importa_pannello(self.hass, conti, self.prezzo_pannello)

    @property
    def prezzo_pannello(self) -> float | None:
        """Corrispettivo Luce indicato nelle opzioni per i periodi non ancora fatturati."""
        valore = self.config_entry.options.get(CONF_PREZZO_PANNELLO)
        return float(valore) if valore else None

    async def async_aggiorna_pannelli(self, _ora: datetime | None = None) -> None:
        """Produzione istantanea e quarti d'ora dei pannelli, fra un aggiornamento e l'altro."""
        if self.data is None or self._in_aggiornamento:
            return
        conti = self.data.conti
        if not any(c.pannello is not None for c in conti.values()):
            return
        await self._leggi_pannelli(conti, conti, dettagli=False)
        await self._derivati_pannelli(conti)
        # senza async_set_updated_data, che sposterebbe il prossimo aggiornamento completo
        self.async_update_listeners()

    async def _leggi_pannelli(
        self, conti: dict[str, Conto], precedenti: dict[str, Conto], *, dettagli: bool
    ) -> None:
        """Pannelli virtuali delle forniture di luce e loro dati.

        Con `dettagli` si rileggono anche i pannelli adottati (e si scopre se una
        fornitura ne ha di nuovi); altrimenti solo produzione e quarti d'ora.
        """
        async with self._lock_pannelli:
            if self._pannelli is None:
                dati = await self._memoria_pannelli.async_load() or {}
                self._pannelli = dati.get("conti") or {}
            luce = [c for c in conti.values() if c.commodity == LUCE]
            risultati = await asyncio.gather(
                *(self._leggi_pannello(c, precedenti.get(c.codice), dettagli) for c in luce),
                return_exceptions=True,
            )
            # quanto letto si salva anche se un altro conto è andato in errore
            if any(r is True for r in risultati):
                await self._memoria_pannelli.async_save({"conti": self._pannelli})
            for risultato in risultati:
                if isinstance(risultato, BaseException):
                    raise risultato

    async def _leggi_pannello(self, conto: Conto, precedente: Conto | None, dettagli: bool) -> bool:
        """Pannelli, produzione istantanea e quarti d'ora di un conto. Ritorna se ha letto
        quarti d'ora nuovi.

        Un errore dell'area clienti lascia i dati precedenti: il servizio dei pannelli è
        indipendente dal resto dell'account. I dati nuovi si preparano a parte e si
        pubblicano nel conto solo alla fine.
        """
        vecchio = precedente.pannello if precedente else None
        conto.pannello = vecchio
        if not conto.codice_fornitura or not conto.pod or (not dettagli and vecchio is None):
            return False
        nel_log = oscura(conto.codice)
        if dettagli or vecchio is None:
            try:
                risposta = await self.client.pannelli(conto.codice, conto.codice_fornitura)
                pannelli = [
                    replace(p, canone=await self._canone(p.codice))
                    for p in pannello.pannelli_da(risposta)
                ]
            except PlenitudeError as err:
                _LOGGER.debug("Pannelli del conto %s non letti: %s", nel_log, err)
                return False
        else:
            pannelli = vecchio.pannelli
        if not pannelli:
            conto.pannello = None
            return False
        dati = pannello.DatiPannello(
            pannelli=pannelli,
            potenza_istantanea=vecchio.potenza_istantanea if vecchio else None,
            attivo_dal=vecchio.attivo_dal if vecchio else None,
            quarti=vecchio.quarti if vecchio else {},
        )
        try:
            dati.potenza_istantanea = pannello.potenza_da(
                await self.client.produzione_istantanea(
                    conto.codice,
                    conto.codice_fornitura,
                    [
                        {
                            "identifier": p.codice,
                            "position": p.posizione,
                            "power": f"{p.potenza:g}" if p.potenza is not None else None,
                        }
                        for p in pannelli
                    ],
                )
            )
        except PlenitudeError as err:
            # meglio nessun valore che l'ultimo letto spacciato per attuale
            dati.potenza_istantanea = None
            _LOGGER.debug("Produzione istantanea del conto %s non letta: %s", nel_log, err)
        letti = await self._leggi_quarti(conto, dati, forza=dettagli)
        conto.pannello = dati
        return letti

    async def _canone(self, codice: str) -> float | None:
        """Canone mensile di un pannello, dal catalogo (letto finché non si trova)."""
        if codice not in self._canoni:
            try:
                canone = pannello.canone_da(await self.client.prodotto(codice), codice)
            except PlenitudeError as err:
                _LOGGER.debug("Canone del pannello %s non letto: %s", codice, err)
                return None
            if canone is None:
                _LOGGER.debug("Canone del pannello %s non presente nel catalogo", codice)
                return None
            self._canoni[codice] = canone
        return self._canoni[codice]

    async def _leggi_quarti(self, conto: Conto, dati: pannello.DatiPannello, forza: bool) -> bool:
        """Quarti d'ora dall'attivazione: si chiedono solo i giorni nuovi o incompleti."""
        assert self._pannelli is not None
        salvati = self._pannelli.setdefault(conto.codice, {})
        giorni: dict[str, list[list[Any]]] = salvati.setdefault("giorni", {})
        adesso = time.monotonic()
        letti = False
        if forza or adesso - self._lettura_quarti.get(conto.codice, -1e9) >= (
            INTERVALLO_QUARTI_MINUTI * 60
        ):
            letti, riuscita = await self._scarica_quarti(conto, dati, salvati, giorni)
            if riuscita:  # dopo un errore si riprova al giro successivo
                self._lettura_quarti[conto.codice] = adesso
        if letti or not dati.quarti:
            nuovi = pannello.DatiPannello()
            pannello.unisci(
                nuovi, (q for righe in giorni.values() for q in pannello.quarti_da_righe(righe))
            )
            dati.quarti, dati.attivo_dal = nuovi.quarti, nuovi.attivo_dal
        return letti

    async def _scarica_quarti(
        self,
        conto: Conto,
        dati: pannello.DatiPannello,
        salvati: dict[str, Any],
        giorni: dict[str, list[list[Any]]],
    ) -> tuple[bool, bool]:
        """Legge i giorni da aggiornare. Ritorna se ha letto dati e se non ci sono stati errori."""
        assert conto.pod
        nel_log = oscura(conto.codice)
        try:
            if not salvati.get("inizio"):
                inizio = await self._cerca_attivazione(conto)
                if inizio is None:
                    _LOGGER.debug("Il pannello del conto %s non ha ancora prodotto", nel_log)
                    return False, True
                salvati["inizio"] = inizio.isoformat()
        except PlenitudeError as err:
            _LOGGER.debug("Attivazione del pannello del conto %s non letta: %s", nel_log, err)
            return False, False
        da_leggere = pannello.giorni_da_leggere(
            giorni,
            date.fromisoformat(salvati["inizio"]),
            dt_util.utcnow(),
            MAX_GIORNI_PANNELLO,
            dati.attivo_dal,
        )
        limite = asyncio.Semaphore(4)

        async def leggi(giorno: date) -> tuple[date, Any]:
            async with limite:
                return giorno, await self.client.produzioni_consumi(
                    conto.codice, conto.pod or "", "QUARTER_HOURS", giorno
                )

        letti, riuscita = False, True
        for risultato in await asyncio.gather(
            *(leggi(g) for g in da_leggere), return_exceptions=True
        ):
            if isinstance(risultato, PlenitudeError):
                _LOGGER.debug("Quarti d'ora del conto %s non letti: %s", nel_log, risultato)
                riuscita = False
                continue
            if isinstance(risultato, BaseException):
                raise risultato
            giorno, risposta = risultato
            if righe := pannello.righe_giorno(risposta):
                giorni[giorno.isoformat()] = righe
                letti = True
        return letti, riuscita

    async def _cerca_attivazione(self, conto: Conto) -> date | None:
        """Primo giorno in cui il pannello ha prodotto (dai totali annui e mensili)."""
        assert conto.pod
        oggi = pannello.giorno_locale(dt_util.utcnow())
        primo_mese: date | None = None
        for anno in range(oggi.year, oggi.year - pannello.ANNI_RICERCA, -1):
            risposta = await self.client.produzioni_consumi(
                conto.codice, conto.pod, "YEARLY", date(anno, 1, 1)
            )
            mese = pannello.primo_periodo_con_produzione(risposta)
            if mese is None:
                if anno == oggi.year:
                    continue  # inizio d'anno: il pannello può essere attivo dall'anno prima
                break
            primo_mese = mese
            if mese.month != 1:
                break
        if primo_mese is None:
            return None
        risposta = await self.client.produzioni_consumi(
            conto.codice, conto.pod, "MONTHLY", primo_mese
        )
        return pannello.primo_periodo_con_produzione(risposta) or primo_mese

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
        await self._ricalcola()

    async def _ricalcola(self) -> None:
        """Aggiorna i dati con le bollette appena lette, senza richiamare l'area clienti."""
        if self._in_aggiornamento:
            # ci pensa l'aggiornamento in corso, prima di chiudersi
            self._ricalcolo_pendente = True
            return
        if self.data is None:
            return
        self._applica_pdf(self.data.conti)
        await self._aggiorna_derivati(self.data.conti, consumi=False)
        if self._in_aggiornamento or self.data is None:
            self._ricalcolo_pendente = True
            return
        # i dati possono essere cambiati durante l'attesa: si riapplica la cache all'ultimo
        self._applica_pdf(self.data.conti)
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
