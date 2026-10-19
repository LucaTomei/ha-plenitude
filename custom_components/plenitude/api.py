"""Accesso ai dati Plenitude.

Due sorgenti di bollette implementano la stessa interfaccia `SorgenteBollette`:

- `SorgenteCartella`: legge i PDF da una cartella (copiati a mano o depositati da
  un'automazione, ad esempio dall'integrazione IMAP).
- `ClientPlenitude`: accede all'account Plenitude come fa l'app. L'accesso usa SAP
  Gigya (email, password e codice di verifica inviato per email); la sessione ottenuta
  dura un anno e firma le richieste successive. Con la sessione si ottiene un token
  di breve durata (JWT) che il portale scambia con il token `gysession` usato da tutte
  le API dell'area clienti.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import base64
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
import logging
from pathlib import Path
import re
import secrets
import time
from typing import Any
from urllib.parse import quote
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

GIGYA_API_KEY = "4_lwKeG5fvfcjYz7Z89duFLg"
GIGYA_URL = "https://accounts.eu1.gigya.com"
PORTALE_URL = "https://eniplenitude.com/serviceDAp"
USER_AGENT = "MyEni/4 CFNetwork/3826.500.131 Darwin/24.5.0"

# Durata richiesta per la sessione Gigya (secondi): un anno, come l'app.
DURATA_SESSIONE = 31_536_000
# Il token gysession viene rinnovato prima della scadenza del JWT da cui deriva (5 minuti).
VALIDITA_GYSESSION = 240

TIMEOUT = aiohttp.ClientTimeout(total=60)

# Codici di errore Gigya.
_GIGYA_TFA_IN_SOSPESO = 403101
_GIGYA_CREDENZIALI_ERRATE = 403042
_GIGYA_CODICE_ERRATO = 400006
_GIGYA_ACCOUNT_BLOCCATO = 403120
# sessione non valida o revocata, firma rifiutata, account disattivato
_GIGYA_ERRORI_SESSIONE = frozenset({403005, 403003, 403041})


class PlenitudeError(Exception):
    """Errore generico nell'accesso ai dati Plenitude."""


class PlenitudeAuthError(PlenitudeError):
    """Credenziali errate o sessione scaduta: serve un nuovo accesso."""


class CodiceErrato(PlenitudeError):
    """Codice di verifica errato o scaduto."""


class AccountBloccato(PlenitudeAuthError):
    """Troppi tentativi: l'account è temporaneamente bloccato."""


class RisorsaNonDisponibile(PlenitudeError):
    """Il documento o la risorsa richiesta non esiste (errore definitivo, non temporaneo)."""


def oscura(testo: str) -> str:
    """Testo per i log senza codici di conto o cliente (sequenze di 6 o più cifre)."""
    return re.sub(r"\d{6,}", lambda m: f"…{m.group()[-3:]}", testo)


@dataclass(frozen=True, slots=True)
class Sessione:
    """Sessione Gigya dell'account (dura un anno)."""

    uid: str
    token: str
    segreto: str
    gmid: str
    ucid: str


@dataclass(frozen=True, slots=True)
class VerificaInCorso:
    """Stato dell'accesso in attesa del codice di verifica inviato per email.

    Dopo `accedi` il codice non è ancora stato inviato (`phv_token` vuoto): si invia con
    `invia_codice`, così chi chiama può prima controllare l'account (`uid`).
    """

    uid: str
    reg_token: str
    asserzione: str
    phv_token: str
    email_id: str
    email_offuscata: str
    gmid: str
    ucid: str


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
        return await asyncio.get_running_loop().run_in_executor(None, self._leggi)

    def _leggi(self) -> list[bytes]:
        if not self._cartella.is_dir():
            raise PlenitudeError(f"cartella delle bollette non trovata: {self._cartella}")
        return [
            f.read_bytes()
            for f in sorted(self._cartella.iterdir())
            if f.suffix.lower() == ".pdf" and f.is_file()
        ]


def _codice_sbagliato(corpo: dict[str, Any]) -> bool:
    """Gigya segnala il codice errato come parametro non valido, con un dettaglio sul codice."""
    return (
        corpo.get("errorCode") == _GIGYA_CODICE_ERRATO
        and "code" in str(corpo.get("errorDetails", "")).lower()
    )


def _codifica(valore: str) -> str:
    return quote(valore, safe="-_.~")


def firma_gigya(segreto: str, url: str, parametri: dict[str, str]) -> str:
    """Firma HMAC-SHA1 di una richiesta Gigya autenticata con la sessione."""
    stringa = "&".join(f"{_codifica(k)}={_codifica(v)}" for k, v in sorted(parametri.items()))
    base = f"POST&{_codifica(url)}&{_codifica(stringa)}"
    digest = hmac.new(base64.b64decode(segreto), base.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


class ClientPlenitude:
    """Client dell'area clienti Plenitude (stesse API dell'app)."""

    def __init__(self, http: aiohttp.ClientSession, sessione: Sessione | None = None) -> None:
        """Inizializza il client; la sessione serve per tutto tranne l'accesso."""
        self._http = http
        self.sessione = sessione
        # identificativi del dispositivo registrato presso Gigya (obbligatori per la verifica)
        self._gmid = sessione.gmid if sessione else ""
        self._ucid = sessione.ucid if sessione else ""
        self._gysession: str | None = None
        self._gysession_scadenza = 0.0
        self._lock = asyncio.Lock()

    # --- Gigya -----------------------------------------------------------------------

    async def _gigya(
        self, metodo: str, parametri: dict[str, str], firmata: bool = False
    ) -> dict[str, Any]:
        url = f"{GIGYA_URL}/{metodo}"
        dati = {
            "apikey": GIGYA_API_KEY,
            "format": "json",
            "httpStatusCodes": "false",
            "targetEnv": "mobile",
            "nonce": f"{int(time.time())}_{secrets.randbelow(2**31)}",
            **parametri,
        }
        if self._gmid:
            dati["gmid"] = self._gmid
            dati["ucid"] = self._ucid
        if firmata:
            if self.sessione is None:
                raise PlenitudeAuthError("nessuna sessione")
            dati["oauth_token"] = self.sessione.token
            dati["timestamp"] = str(int(time.time()))
            dati["sig"] = firma_gigya(self.sessione.segreto, url, dati)
        try:
            async with self._http.post(
                url,
                data=dati,
                headers={"User-Agent": USER_AGENT, "apikey": GIGYA_API_KEY},
                timeout=TIMEOUT,
            ) as risposta:
                corpo = await risposta.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise PlenitudeError(f"{metodo}: errore di comunicazione: {err}") from err
        if not isinstance(corpo, dict):
            raise PlenitudeError(f"{metodo}: risposta non valida")
        return corpo

    @staticmethod
    def _errore_gigya(metodo: str, corpo: dict[str, Any]) -> PlenitudeError:
        codice = corpo.get("errorCode")
        messaggio = f"{metodo}: {codice} {corpo.get('errorMessage', '')}".strip()
        if codice == _GIGYA_ACCOUNT_BLOCCATO:
            return AccountBloccato(messaggio)
        if codice == _GIGYA_CREDENZIALI_ERRATE or codice in _GIGYA_ERRORI_SESSIONE:
            return PlenitudeAuthError(messaggio)
        if codice == _GIGYA_CODICE_ERRATO:
            return CodiceErrato(messaggio)
        return PlenitudeError(messaggio)

    async def _gigya_ok(
        self, metodo: str, parametri: dict[str, str], firmata: bool = False
    ) -> dict[str, Any]:
        corpo = await self._gigya(metodo, parametri, firmata)
        if corpo.get("errorCode") != 0:
            raise self._errore_gigya(metodo, corpo)
        return corpo

    def _sessione_da(self, corpo: dict[str, Any]) -> Sessione:
        info = corpo.get("sessionInfo") or {}
        token, segreto, uid = info.get("sessionToken"), info.get("sessionSecret"), corpo.get("UID")
        if not (token and segreto and uid):
            raise PlenitudeError("sessione non presente nella risposta")
        return Sessione(uid=uid, token=token, segreto=segreto, gmid=self._gmid, ucid=self._ucid)

    async def _registra_dispositivo(self) -> None:
        """Ottiene da Gigya gli identificativi del dispositivo, come fa l'app al primo avvio."""
        self._gmid = self._ucid = ""
        corpo = await self._gigya_ok("socialize.getIDs", {})
        if not corpo.get("gmid") or not corpo.get("ucid"):
            raise PlenitudeError("identificativo del dispositivo non ottenuto")
        self._gmid, self._ucid = corpo["gmid"], corpo["ucid"]

    async def accedi(self, email: str, password: str) -> Sessione | VerificaInCorso:
        """Primo passo dell'accesso.

        Se l'account richiede la verifica (sempre, per un nuovo dispositivo) ritorna lo
        stato da completare con `invia_codice` e poi `verifica_codice`.
        """
        await self._registra_dispositivo()
        corpo = await self._gigya(
            "accounts.login",
            {
                "loginID": email.strip(),
                "password": password,
                "include": "profile,data,id_token",
                "lang": "it",
                "loginMode": "standard",
                "sessionExpiration": str(DURATA_SESSIONE),
            },
        )
        codice = corpo.get("errorCode")
        if codice == 0:
            self.sessione = self._sessione_da(corpo)
            return self.sessione
        if codice != _GIGYA_TFA_IN_SOSPESO:
            raise self._errore_gigya("accounts.login", corpo)
        reg_token, uid = corpo.get("regToken"), corpo.get("UID")
        if not reg_token or not uid:
            raise PlenitudeError("accounts.login: risposta di verifica incompleta")
        asserzione = await self._asserzione(reg_token)
        email_tfa = await self._gigya_ok(
            "accounts.tfa.email.getEmails", {"gigyaAssertion": asserzione}
        )
        indirizzi = [e for e in email_tfa.get("emails") or [] if e.get("id")]
        if not indirizzi:
            raise PlenitudeError("nessun indirizzo email per la verifica")
        return VerificaInCorso(
            uid=uid,
            reg_token=reg_token,
            asserzione=asserzione,
            phv_token="",
            email_id=indirizzi[0]["id"],
            email_offuscata=indirizzi[0].get("obfuscated") or "",
            gmid=self._gmid,
            ucid=self._ucid,
        )

    async def _asserzione(self, reg_token: str) -> str:
        risposta = await self._gigya_ok(
            "accounts.tfa.initTFA",
            {"regToken": reg_token, "provider": "gigyaEmail", "mode": "verify"},
        )
        if not risposta.get("gigyaAssertion"):
            raise PlenitudeError("accounts.tfa.initTFA: asserzione assente")
        return risposta["gigyaAssertion"]

    async def invia_codice(self, verifica: VerificaInCorso) -> VerificaInCorso:
        """Invia (o reinvia) il codice di verifica per email; ritorna lo stato aggiornato."""
        self._gmid, self._ucid = verifica.gmid, verifica.ucid
        asserzione = await self._asserzione(verifica.reg_token)
        risposta = await self._gigya_ok(
            "accounts.tfa.email.sendVerificationCode",
            {
                "gigyaAssertion": asserzione,
                "emailID": verifica.email_id,
                "regToken": verifica.reg_token,
                "lang": "it",
            },
        )
        if not risposta.get("phvToken"):
            raise PlenitudeError("accounts.tfa.email.sendVerificationCode: token assente")
        return replace(verifica, asserzione=asserzione, phv_token=risposta["phvToken"])

    async def verifica_codice(self, verifica: VerificaInCorso, codice: str) -> Sessione:
        """Completa l'accesso con il codice ricevuto per email."""
        self._gmid, self._ucid = verifica.gmid, verifica.ucid
        if not verifica.phv_token:
            raise PlenitudeError("codice di verifica non ancora inviato")
        codice = "".join(c for c in codice if c.isdigit())
        if not codice:
            raise CodiceErrato("codice vuoto")
        asserzione = verifica.asserzione
        corpo = await self._conferma_codice(verifica, asserzione, codice)
        if corpo.get("errorCode") != 0 and not _codice_sbagliato(corpo):
            # l'asserzione dura 5 minuti: se è scaduta se ne chiede un'altra e si riprova
            asserzione = await self._asserzione(verifica.reg_token)
            corpo = await self._conferma_codice(verifica, asserzione, codice)
        if corpo.get("errorCode") != 0:
            raise self._errore_gigya("accounts.tfa.email.completeVerification", corpo)
        if not corpo.get("providerAssertion"):
            raise PlenitudeError("accounts.tfa.email.completeVerification: asserzione assente")
        await self._gigya_ok(
            "accounts.tfa.finalizeTFA",
            {
                "gigyaAssertion": asserzione,
                "providerAssertion": corpo["providerAssertion"],
                "regToken": verifica.reg_token,
                "tempDevice": "false",
            },
        )
        finale = await self._gigya_ok(
            "accounts.finalizeRegistration",
            {"regToken": verifica.reg_token, "include": "profile,data,id_token"},
        )
        self.sessione = self._sessione_da(finale)
        return self.sessione

    async def _conferma_codice(
        self, verifica: VerificaInCorso, asserzione: str, codice: str
    ) -> dict[str, Any]:
        return await self._gigya(
            "accounts.tfa.email.completeVerification",
            {
                "gigyaAssertion": asserzione,
                "phvToken": verifica.phv_token,
                "code": codice,
                "regToken": verifica.reg_token,
            },
        )

    async def _jwt(self) -> str:
        corpo = await self._gigya_ok("accounts.getJWT", {}, firmata=True)
        if not corpo.get("id_token"):
            raise PlenitudeError("accounts.getJWT: token assente")
        return corpo["id_token"]

    # --- Portale -----------------------------------------------------------------------

    async def _token_portale(self, rifiutato: str | None = None) -> str:
        """Token `gysession` del portale, ricavato dal JWT della sessione Gigya.

        Il portale lo imposta anche come cookie: per questo ogni client deve avere una
        sessione HTTP (e quindi un archivio di cookie) tutta sua.
        """
        async with self._lock:
            # se il token è già stato rinnovato da un'altra richiesta si usa quello nuovo
            if (
                self._gysession
                and self._gysession != rifiutato
                and time.monotonic() < self._gysession_scadenza
            ):
                return self._gysession
            jwt = await self._jwt()
            try:
                risposta = await self._richiesta(
                    "GET", "/api/c360/getJwe", intestazioni={"jwt": jwt}
                )
            except _NonAutorizzato as err:
                raise PlenitudeError(f"token del portale rifiutato: {err}") from err
            if not isinstance(risposta, dict) or not risposta.get("jwe"):
                raise PlenitudeError("token del portale non ottenuto")
            self._gysession = risposta["jwe"]
            self._gysession_scadenza = time.monotonic() + VALIDITA_GYSESSION
            return self._gysession

    async def _richiesta(
        self,
        metodo: str,
        percorso: str,
        *,
        intestazioni: dict[str, str] | None = None,
        parametri: dict[str, str] | None = None,
        corpo: Any = None,
        binario: bool = False,
    ) -> Any:
        url = f"{PORTALE_URL}{percorso}"
        query = {**(parametri or {}), "logHash": str(uuid.uuid4()), "channel": "IOS"}
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Expires": "0",
            "Cache-Control": "no-cache",
            **(intestazioni or {}),
        }
        try:
            async with self._http.request(
                metodo, url, params=query, headers=headers, json=corpo, timeout=TIMEOUT
            ) as risposta:
                if risposta.status in (401, 403):
                    raise _NonAutorizzato(f"{oscura(percorso)}: HTTP {risposta.status}")
                if risposta.status == 204:
                    return None
                if risposta.status == 404:
                    raise RisorsaNonDisponibile(f"{oscura(percorso)}: HTTP 404")
                if risposta.status >= 400:
                    raise PlenitudeError(f"{oscura(percorso)}: HTTP {risposta.status}")
                if binario:
                    return await risposta.read()
                testo = await risposta.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise PlenitudeError(f"{oscura(percorso)}: errore di comunicazione: {err}") from err
        if not testo.strip():
            return None
        try:
            return json.loads(testo)
        except ValueError as err:
            raise PlenitudeError(f"{oscura(percorso)}: risposta non valida") from err

    async def _api(
        self,
        metodo: str,
        percorso: str,
        *,
        intestazioni: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Chiamata autenticata; se il token del portale viene rifiutato lo rinnova una volta.

        Un rifiuto ripetuto non è trattato come credenziali scadute (quelle le segnala
        Gigya nel rinnovo del token): è un errore temporaneo del portale.
        """
        token: str | None = None
        for tentativo in range(2):
            token = await self._token_portale(rifiutato=token)
            try:
                return await self._richiesta(
                    metodo,
                    percorso,
                    intestazioni={"gysession": token, **(intestazioni or {})},
                    **kwargs,
                )
            except _NonAutorizzato as err:
                if tentativo:
                    raise PlenitudeError(str(err)) from err
                _LOGGER.debug("Token del portale rifiutato, lo rinnovo: %s", err)
        raise PlenitudeError(oscura(percorso))  # pragma: no cover

    async def utente(self) -> dict[str, Any]:
        """Dati dell'utente: codice cliente e conto predefinito."""
        return await self._api(
            "POST",
            "/api/c360/init",
            intestazioni={"initapplication": "APPS_IOS"},
            corpo={
                "versioneApp": "6.17.0.4",
                "sistemaOperativo": "iOS",
                "tipoDevice": "Handset",
                "deviceNotificationsConsent": False,
            },
        )

    async def conti(self, codice_conto: str) -> list[dict[str, Any]]:
        """Tutti i conti (forniture luce e gas) dell'utente."""
        risposta = await self._api("GET", f"/api/contiServices/conti/{codice_conto}")
        return list((risposta or {}).get("conto") or [])

    async def forniture(self, codice_conto: str) -> list[dict[str, Any]]:
        """Forniture (PDR/POD, contatore, offerta) di un conto."""
        risposta = await self._api(
            "GET", f"/api/contiServices/conti/{codice_conto}/lista-forniture"
        )
        return list((risposta or {}).get("fornitura") or [])

    async def bollette(self, codice_conto: str) -> list[dict[str, Any]]:
        """Elenco delle bollette di un conto (dalla più recente)."""
        risposta = await self._api(
            "GET", f"/c360/api/conti/{codice_conto}/bollette", intestazioni={"canale": "MOBILE"}
        )
        return list((risposta or {}).get("bollette") or [])

    async def consumi(self, codice_conto: str) -> list[dict[str, Any]]:
        """Consumi mensili delle forniture di un conto."""
        risposta = await self._api(
            "GET", "/api/consumptionData", parametri={"codiceContoCliente": codice_conto}
        )
        return list((risposta or {}).get("supplies") or [])

    async def autolettura(self, codice_conto: str) -> dict[str, Any] | None:
        """Ultima autolettura e prossima finestra utile."""
        risposta = await self._api(
            "GET", f"/api/autoletture/{codice_conto}/autoletture", parametri={"filter": "last"}
        )
        return risposta if isinstance(risposta, dict) else None

    async def pdf_bolletta(self, codice_conto: str, numero_bolletta: str) -> bytes:
        """PDF di una bolletta."""
        dati = await self._api(
            "GET",
            f"/c360/api/conti/{codice_conto}/download-doc-pdf",
            parametri={"numeroFattura": numero_bolletta},
            binario=True,
        )
        if not dati or not dati.startswith(b"%PDF"):
            raise RisorsaNonDisponibile(f"PDF della bolletta {oscura(numero_bolletta)} assente")
        return dati


class _NonAutorizzato(PlenitudeError):
    """Il portale ha rifiutato il token (interno al client)."""
