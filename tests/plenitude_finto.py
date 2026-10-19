"""Simulazione di Gigya e dell'area clienti Plenitude per i test.

I dati sono inventati ma hanno la stessa forma delle risposte reali.
"""

from __future__ import annotations

import asyncio
import copy
from http import HTTPStatus
from pathlib import Path
import re
from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.plenitude.api import Sessione

FIXTURES = Path(__file__).parent / "fixtures"

GIGYA = "https://accounts.eu1.gigya.com"
PORTALE = "https://eniplenitude.com/serviceDAp"

EMAIL = "mario.rossi@example.com"
PASSWORD = "Password!1"
CODICE = "123456"
UID = "0123456789abcdef0123456789abcdef"

CONTO_GAS = "500000000001"
CONTO_LUCE = "500000000002"

SESSIONE = Sessione(
    uid=UID,
    token="st2.s.sessione-di-prova",
    segreto="c2VncmV0by1kaS1wcm92YQ==",
    gmid="gmid.ver4.prova",
    ucid="ucid-prova",
)

CONTI: list[dict[str, Any]] = [
    {
        "codice": CONTO_GAS,
        "alias": "GAS",
        "tipo": "ATTIVO",
        "indirizzoFornitura": "VIA ROMA 1, 00100, ROMA (RM)",
        "commodity": "GAS",
        "assetType": "COMMODITY",
    },
    {
        "codice": CONTO_LUCE,
        "alias": "LUCE",
        "tipo": "ATTIVO",
        "indirizzoFornitura": "VIA ROMA 1, 00100, ROMA (RM)",
        "commodity": "POWER",
        "assetType": "COMMODITY",
    },
    {
        "codice": "500000000003",
        "alias": "POLIZZA",
        "tipo": "ATTIVO",
        "commodity": None,
        "assetType": "EXTRACOMMODITY",
    },
]

FORNITURE: dict[str, list[dict[str, Any]]] = {
    CONTO_GAS: [
        {
            "tipo": "GAS",
            "podPdr": "01234567890123",
            "pdr": "01234567890123",
            "pod": None,
            "matricolaContatore": "MAT0000000001",
            "prodotto": "Offerta Gas di prova",
            "status": "ATTIVO",
        }
    ],
    CONTO_LUCE: [
        {
            "tipo": "POWER",
            "podPdr": "IT001E00000001",
            "pdr": None,
            "pod": "IT001E00000001",
            "matricolaContatore": "MAT0000000002",
            "prodotto": "Offerta Luce di prova",
            "status": "ATTIVO",
        }
    ],
}


def _bolletta(numero: str, emissione: str, scadenza: str, importo: float, da_pagare: float):
    return {
        "numeroBolletta": numero,
        "emissione": emissione,
        "scadenza": scadenza,
        "importo": importo,
        "importoPagare": str(da_pagare),
        "statoPagamento": "PAGATA" if not da_pagare else "DA_PAGARE",
        "metodoPagamento": "DOMICILIAZIONE_BANCARIA",
        "documentType": "B",
        "statoBollettaView": "VISIBILE",
    }


BOLLETTE: dict[str, list[dict[str, Any]]] = {
    CONTO_GAS: [
        _bolletta("9000000004", "28/08/2026", "22/09/2026", 63.32, 0.0),
        _bolletta("9000000003", "06/07/2026", "31/07/2026", 51.10, 0.0),
        _bolletta("9000000002", "19/02/2026", "16/03/2026", 210.40, 0.0),
        _bolletta("9000000001", "22/10/2025", "17/11/2025", 30.0, 0.0),
    ],
    CONTO_LUCE: [
        _bolletta("9100000002", "09/10/2026", "03/11/2026", 328.78, 328.78),
        _bolletta("9100000001", "10/08/2026", "04/09/2026", 153.97, 20.5),
    ],
}

# PDF (testo delle fixture) delle bollette gas
PDF_GAS: dict[str, str] = {
    "9000000004": "bolletta-gas-20260828.txt",
    "9000000003": "bolletta-gas-20260706.txt",
    "9000000002": "bolletta-gas-20260219.txt",
    "9000000001": "bolletta-gas-202501022.txt",
}


# PDF (testo) delle bollette luce: serve solo il periodo fatturato
PDF_LUCE: dict[str, str] = {
    "9100000002": (
        "Bolletta luce\nPeriodo di fatturazione: dal 01/08/2026 al 30/09/2026\n"
        "Totale da pagare 328,78 € Indice di riferimento: PUN Index GME\n"
        "Elementi di dettaglio Luce\n"
    ),
}


def _mese(anno: int, mese: int, **valori: Any) -> dict[str, Any]:
    return {"year": str(anno), "numericalMonth": mese - 1, "isPartial": False, **valori}


CONSUMI: dict[str, list[dict[str, Any]]] = {
    CONTO_GAS: [
        {
            "type": "GAS",
            "consumptions": [
                _mese(2025, 11, consumptions=40),
                _mese(2025, 12, consumptions=90),
                _mese(2026, 1, consumptions=104),
                _mese(2026, 6, consumptions=12),
                _mese(2026, 7, consumptions=8),
                _mese(2026, 9, consumptions=17),
                {**_mese(2026, 10, consumptions=3), "isPartial": True},
            ],
        }
    ],
    CONTO_LUCE: [
        {
            "type": "LUCE",
            "consumptions": [
                _mese(2026, 8, consumptionsF1=90, consumptionsF2=60, consumptionsF3=80),
                _mese(2026, 9, consumptionsF1=96, consumptionsF2=63, consumptionsF3=81),
            ],
        }
    ],
}

AUTOLETTURE: dict[str, dict[str, Any]] = {
    CONTO_GAS: {
        "commodity": "GAS",
        "data": "30/09/2026",
        "dataAutoletturaDa": "24/11/2026",
        "dataAutoletturaA": "28/11/2026",
        "valoreAutolettura": "1637",
    },
    CONTO_LUCE: {
        "commodity": "POWER",
        "data": None,
        "dataAutoletturaDa": "24/11/2026",
        "dataAutoletturaA": "28/11/2026",
        "valoreAutolettura": None,
    },
}


def _gigya(dati: dict[str, Any], codice: int = 0, **extra: Any) -> dict[str, Any]:
    return {"errorCode": codice, "statusCode": 200 if not codice else 403, **dati, **extra}


class FintoPlenitude:
    """Backend finto: registra le risposte su `aioclient_mock` e ne tiene lo stato."""

    def __init__(self, aioclient_mock: AiohttpClientMocker) -> None:
        """Registra le risposte di Gigya e del portale."""
        self.mock = aioclient_mock
        self.richiede_verifica = True
        self.sessione_valida = True
        self.account_bloccato = False
        self.asserzione_scaduta = False
        self.token_rifiutati = 0
        self.token_emessi = 0
        self.token_revocati: set[str] = set()
        self.errori_temporanei_pdf = 0
        self.blocco_durante_aggiornamento = False
        self.codici_inviati = 0
        self.jwt_emessi = 0
        self.conti = copy.deepcopy(CONTI)
        self.bollette = copy.deepcopy(BOLLETTE)
        self.consumi = copy.deepcopy(CONSUMI)
        self.autoletture = copy.deepcopy(AUTOLETTURE)
        self.errori: dict[str, HTTPStatus] = {}
        self.pdf_scaricati: list[str] = []
        self.parametri_gigya: dict[str, dict[str, Any]] = {}
        aioclient_mock.post(re.compile(re.escape(GIGYA) + r"/.*"), side_effect=self._gigya)
        aioclient_mock.get(re.compile(re.escape(PORTALE) + r"/.*"), side_effect=self._portale)
        aioclient_mock.post(re.compile(re.escape(PORTALE) + r"/.*"), side_effect=self._portale)

    def _risposta(self, method: str, url: URL, **kwargs: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, **kwargs)

    async def _gigya(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        metodo = url.path.lstrip("/")
        parametri = dict(data or {})
        self.parametri_gigya[metodo] = parametri
        corpo = self._rispondi_gigya(metodo, parametri)
        return self._risposta(method, url, json=corpo)

    def _rispondi_gigya(self, metodo: str, p: dict[str, Any]) -> dict[str, Any]:
        sessione = {
            "UID": UID,
            "sessionInfo": {
                "sessionToken": SESSIONE.token,
                "sessionSecret": SESSIONE.segreto,
            },
        }
        if metodo == "socialize.getIDs":
            return _gigya({"gmid": SESSIONE.gmid, "ucid": SESSIONE.ucid})
        if metodo == "accounts.login":
            if self.account_bloccato:
                return _gigya({}, 403120, errorMessage="Account temporarily locked out")
            if p.get("loginID") != EMAIL or p.get("password") != PASSWORD:
                return _gigya({}, 403042, errorMessage="Invalid LoginID")
            if not self.richiede_verifica:
                return _gigya(sessione)
            return _gigya({"regToken": "reg-token", "UID": UID}, 403101)
        if metodo == "accounts.tfa.initTFA":
            return _gigya({"gigyaAssertion": f"asserzione-{self.codici_inviati}"})
        if metodo == "accounts.tfa.email.getEmails":
            return _gigya({"emails": [{"id": "email-1", "obfuscated": "ma********@example.com"}]})
        if metodo == "accounts.tfa.email.sendVerificationCode":
            self.codici_inviati += 1
            return _gigya({"phvToken": f"phv-{self.codici_inviati}"})
        if metodo == "accounts.tfa.email.completeVerification":
            if self.asserzione_scaduta and p.get("gigyaAssertion") == "asserzione-0":
                self.asserzione_scaduta = False
                # stesso codice di errore del codice errato, ma con un altro dettaglio
                return _gigya(
                    {},
                    400006,
                    errorMessage="Invalid parameter value",
                    errorDetails="Invalid gigyaAssertion",
                )
            if p.get("code") != CODICE:
                return _gigya(
                    {},
                    400006,
                    errorMessage="Invalid parameter value",
                    errorDetails="Wrong verification code",
                )
            return _gigya({"providerAssertion": "provider-assertion"})
        if metodo == "accounts.tfa.finalizeTFA":
            return _gigya({})
        if metodo == "accounts.finalizeRegistration":
            return _gigya(sessione)
        if metodo == "accounts.getJWT":
            if self.blocco_durante_aggiornamento:
                return _gigya({}, 403120, errorMessage="Account temporarily locked out")
            if not self.sessione_valida:
                return _gigya({}, 403005, errorMessage="Unauthorized user")
            self.jwt_emessi += 1
            return _gigya({"id_token": f"jwt-{self.jwt_emessi}"})
        return _gigya({}, 400001, errorMessage="metodo sconosciuto")

    async def _portale(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        intestazioni = self.mock.mock_calls[-1][3] or {}
        # come la rete vera: le richieste in parallelo si sovrappongono
        await asyncio.sleep(0)
        percorso = url.path.removeprefix("/serviceDAp")
        for prefisso, stato in self.errori.items():
            if percorso.startswith(prefisso):
                return self._risposta(method, url, status=stato, text="")
        if percorso == "/api/c360/getJwe":
            self.token_emessi += 1
            return self._risposta(
                method, url, json={"uid": UID, "jwe": f"gysession-{self.token_emessi}"}
            )
        if intestazioni.get("gysession") in self.token_revocati:
            return self._risposta(method, url, status=HTTPStatus.UNAUTHORIZED, text="")
        if self.token_rifiutati:
            self.token_rifiutati -= 1
            return self._risposta(method, url, status=HTTPStatus.UNAUTHORIZED, text="")
        if percorso == "/api/c360/init":
            return self._risposta(
                method, url, json={"codiceCliente": "800000001", "codiceContoDefault": CONTO_GAS}
            )
        if m := re.fullmatch(r"/api/contiServices/conti/(\d+)", percorso):
            return self._risposta(method, url, json={"conto": self.conti})
        if m := re.fullmatch(r"/api/contiServices/conti/(\d+)/lista-forniture", percorso):
            return self._risposta(method, url, json={"fornitura": FORNITURE.get(m[1], [])})
        if m := re.fullmatch(r"/c360/api/conti/(\d+)/bollette", percorso):
            return self._risposta(method, url, json={"bollette": self.bollette.get(m[1], [])})
        if percorso == "/api/consumptionData":
            conto = url.query.get("codiceContoCliente", "")
            return self._risposta(method, url, json={"supplies": self.consumi.get(conto, [])})
        if m := re.fullmatch(r"/api/autoletture/(\d+)/autoletture", percorso):
            return self._risposta(method, url, json=self.autoletture.get(m[1]))
        if m := re.fullmatch(r"/c360/api/conti/(\d+)/download-doc-pdf", percorso):
            numero = url.query.get("numeroFattura", "")
            if self.errori_temporanei_pdf:
                self.errori_temporanei_pdf -= 1
                return self._risposta(method, url, status=HTTPStatus.BAD_GATEWAY, text="")
            if numero in PDF_LUCE:
                self.pdf_scaricati.append(numero)
                return self._risposta(
                    method, url, response=b"%PDF-1.7\n" + PDF_LUCE[numero].encode()
                )
            if numero not in PDF_GAS:
                return self._risposta(method, url, json={"errore": "documento assente"})
            self.pdf_scaricati.append(numero)
            testo = (FIXTURES / PDF_GAS[numero]).read_bytes()
            return self._risposta(method, url, response=b"%PDF-1.7\n" + testo)
        return self._risposta(method, url, status=HTTPStatus.NOT_FOUND, text="")
