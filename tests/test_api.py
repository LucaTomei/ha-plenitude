"""Test del client dell'account Plenitude (Gigya e area clienti)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from http import HTTPStatus

from aiohttp import ClientSession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.plenitude.api import (
    AccountBloccato,
    ClientPlenitude,
    CodiceErrato,
    PlenitudeAuthError,
    PlenitudeError,
    Sessione,
    VerificaInCorso,
    firma_gigya,
)
from homeassistant.core import HomeAssistant

from .plenitude_finto import (
    CODICE,
    CONTO_GAS,
    CONTO_LUCE,
    EMAIL,
    PASSWORD,
    SESSIONE,
    FintoPlenitude,
)


@pytest.fixture
async def http(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> AsyncGenerator[ClientSession]:
    """Sessione HTTP collegata al backend finto."""
    sessione = aioclient_mock.create_session(hass.loop)
    yield sessione
    await sessione.close()


@pytest.fixture
def client(http: ClientSession) -> ClientPlenitude:
    """Client senza sessione."""
    return ClientPlenitude(http)


@pytest.fixture
def client_autenticato(http: ClientSession) -> ClientPlenitude:
    """Client con una sessione valida."""
    return ClientPlenitude(http, SESSIONE)


def test_firma_gigya() -> None:
    """La firma segue lo schema OAuth1 di Gigya (HMAC-SHA1 con il segreto in base64)."""
    firma = firma_gigya(
        "c2VncmV0by1kaS1wcm92YQ==",
        "https://accounts.eu1.gigya.com/accounts.getJWT",
        {"apikey": "chiave", "nonce": "1_2", "timestamp": "1700000000", "oauth_token": "st2.s.x"},
    )
    assert firma == "w/cyozGUrcW+Pxa/pEEY04lqK7M="
    # l'ordine dei parametri non conta
    assert firma == firma_gigya(
        "c2VncmV0by1kaS1wcm92YQ==",
        "https://accounts.eu1.gigya.com/accounts.getJWT",
        {"oauth_token": "st2.s.x", "timestamp": "1700000000", "nonce": "1_2", "apikey": "chiave"},
    )


async def test_accesso_con_verifica(finto: FintoPlenitude, client: ClientPlenitude) -> None:
    """Login → codice via email → sessione, con l'identificativo del dispositivo."""
    verifica = await client.accedi(EMAIL, PASSWORD)
    assert isinstance(verifica, VerificaInCorso)
    assert verifica.uid == SESSIONE.uid
    assert verifica.email_offuscata == "ma********@example.com"
    # il codice si invia solo su richiesta, dopo aver controllato l'account
    assert finto.codici_inviati == 0
    with pytest.raises(PlenitudeError):
        await client.verifica_codice(verifica, CODICE)
    verifica = await client.invia_codice(verifica)
    assert finto.codici_inviati == 1
    assert finto.parametri_gigya["accounts.login"]["gmid"] == "gmid.ver4.prova"
    assert finto.parametri_gigya["accounts.login"]["sessionExpiration"] == "31536000"

    sessione = await client.verifica_codice(verifica, f" {CODICE[:3]} {CODICE[3:]} ")
    assert sessione == SESSIONE
    assert finto.parametri_gigya["accounts.tfa.finalizeTFA"]["providerAssertion"] == (
        "provider-assertion"
    )


async def test_accesso_senza_verifica(finto: FintoPlenitude, client: ClientPlenitude) -> None:
    """Se Gigya non chiede la verifica la sessione arriva subito."""
    finto.richiede_verifica = False
    assert await client.accedi(EMAIL, PASSWORD) == SESSIONE


async def test_credenziali_errate(finto: FintoPlenitude, client: ClientPlenitude) -> None:
    """Password errata → errore di autenticazione, nessun codice inviato."""
    with pytest.raises(PlenitudeAuthError):
        await client.accedi(EMAIL, "sbagliata")
    assert finto.codici_inviati == 0


async def test_account_bloccato(finto: FintoPlenitude, client: ClientPlenitude) -> None:
    """Troppi tentativi → errore specifico."""
    finto.account_bloccato = True
    with pytest.raises(AccountBloccato):
        await client.accedi(EMAIL, PASSWORD)


async def test_codice_errato(finto: FintoPlenitude, client: ClientPlenitude) -> None:
    """Codice errato o vuoto → CodiceErrato, la verifica resta riutilizzabile."""
    verifica = await client.invia_codice(await client.accedi(EMAIL, PASSWORD))
    with pytest.raises(CodiceErrato):
        await client.verifica_codice(verifica, "000000")
    with pytest.raises(CodiceErrato):
        await client.verifica_codice(verifica, "  ")
    assert await client.verifica_codice(verifica, CODICE) == SESSIONE


async def test_asserzione_scaduta(finto: FintoPlenitude, client: ClientPlenitude) -> None:
    """Asserzione scaduta (stesso codice d'errore del codice errato): nuova asserzione e codice valido."""
    verifica = await client.invia_codice(await client.accedi(EMAIL, PASSWORD))
    finto.asserzione_scaduta = True
    assert await client.verifica_codice(verifica, CODICE) == SESSIONE
    assert finto.parametri_gigya["accounts.tfa.finalizeTFA"]["gigyaAssertion"] == "asserzione-1"


async def test_reinvio_codice(finto: FintoPlenitude, client: ClientPlenitude) -> None:
    """Un nuovo codice sostituisce il precedente."""
    verifica = await client.invia_codice(await client.accedi(EMAIL, PASSWORD))
    nuova = await client.invia_codice(verifica)
    assert finto.codici_inviati == 2
    assert nuova.phv_token == "phv-2"
    assert await client.verifica_codice(nuova, CODICE) == SESSIONE


async def test_dati_account(finto: FintoPlenitude, client_autenticato: ClientPlenitude) -> None:
    """Le chiamate all'area clienti restituiscono i dati del conto."""
    c = client_autenticato
    assert (await c.utente())["codiceContoDefault"] == CONTO_GAS
    assert [x["codice"] for x in await c.conti(CONTO_GAS)][:2] == [CONTO_GAS, CONTO_LUCE]
    assert (await c.forniture(CONTO_GAS))[0]["pdr"] == "01234567890123"
    assert len(await c.bollette(CONTO_LUCE)) == 2
    assert (await c.consumi(CONTO_GAS))[0]["type"] == "GAS"
    assert (await c.autolettura(CONTO_GAS))["valoreAutolettura"] == "1637"
    assert (await c.pdf_bolletta(CONTO_GAS, "9000000004")).startswith(b"%PDF")
    # il token del portale viene riusato finché è valido
    assert finto.jwt_emessi == 1


async def test_token_firmato(finto: FintoPlenitude, client_autenticato: ClientPlenitude) -> None:
    """La richiesta del JWT è firmata con la sessione."""
    await client_autenticato.utente()
    parametri = finto.parametri_gigya["accounts.getJWT"]
    assert parametri["oauth_token"] == SESSIONE.token
    firma = parametri.pop("sig")
    assert firma == firma_gigya(
        SESSIONE.segreto, "https://accounts.eu1.gigya.com/accounts.getJWT", parametri
    )


async def test_token_rinnovato(finto: FintoPlenitude, client_autenticato: ClientPlenitude) -> None:
    """Un token rifiutato viene rinnovato una volta."""
    await client_autenticato.utente()
    finto.token_rifiutati = 1
    assert await client_autenticato.conti(CONTO_GAS)
    assert finto.jwt_emessi == 2


async def test_rifiuto_ripetuto_non_e_auth(
    finto: FintoPlenitude, client_autenticato: ClientPlenitude
) -> None:
    """Un rifiuto ripetuto del portale è un errore temporaneo, non una sessione scaduta."""
    finto.token_rifiutati = 2
    with pytest.raises(PlenitudeError) as err:
        await client_autenticato.conti(CONTO_GAS)
    assert not isinstance(err.value, PlenitudeAuthError)


async def test_sessione_scaduta(finto: FintoPlenitude, client_autenticato: ClientPlenitude) -> None:
    """Se Gigya non riconosce più la sessione serve un nuovo accesso."""
    finto.sessione_valida = False
    with pytest.raises(PlenitudeAuthError):
        await client_autenticato.utente()


async def test_senza_sessione(finto: FintoPlenitude, client: ClientPlenitude) -> None:
    """Senza sessione le API dell'area clienti non sono accessibili."""
    with pytest.raises(PlenitudeAuthError):
        await client.utente()


async def test_errore_http(finto: FintoPlenitude, client_autenticato: ClientPlenitude) -> None:
    """Errori del server → PlenitudeError."""
    finto.errori["/c360/api/conti"] = HTTPStatus.INTERNAL_SERVER_ERROR
    with pytest.raises(PlenitudeError):
        await client_autenticato.bollette(CONTO_GAS)


async def test_pdf_assente(finto: FintoPlenitude, client_autenticato: ClientPlenitude) -> None:
    """Una risposta che non è un PDF non viene accettata come bolletta."""
    with pytest.raises(PlenitudeError):
        await client_autenticato.pdf_bolletta(CONTO_GAS, "1234")


async def test_errore_di_rete(aioclient_mock: AiohttpClientMocker, client: ClientPlenitude) -> None:
    """Gli errori di rete diventano PlenitudeError."""
    aioclient_mock.post(
        "https://accounts.eu1.gigya.com/socialize.getIDs", exc=TimeoutError("lento")
    )
    with pytest.raises(PlenitudeError):
        await client.accedi(EMAIL, PASSWORD)


def test_sessione_serializzabile() -> None:
    """La sessione si salva nella config entry come dizionario e si ricostruisce."""
    from dataclasses import asdict

    assert Sessione(**asdict(SESSIONE)) == SESSIONE


async def test_rinnovo_token_concorrente(
    finto: FintoPlenitude, client_autenticato: ClientPlenitude
) -> None:
    """Più richieste rifiutate insieme rinnovano il token una sola volta."""
    import asyncio

    await client_autenticato.utente()
    finto.token_revocati.add(f"gysession-{finto.token_emessi}")
    risultati = await asyncio.gather(
        client_autenticato.forniture(CONTO_GAS),
        client_autenticato.bollette(CONTO_GAS),
        client_autenticato.consumi(CONTO_GAS),
    )
    assert all(risultati)
    assert finto.jwt_emessi == 2
    assert finto.token_emessi == 2


def test_oscura() -> None:
    """Nei log i codici di conto e cliente restano riconoscibili solo dalle ultime cifre."""
    from custom_components.plenitude.api import oscura

    assert oscura("/c360/api/conti/500000000001/bollette") == "/c360/api/conti/…001/bollette"
    assert oscura("12345") == "12345"


async def test_errori_senza_codici(
    finto: FintoPlenitude, client_autenticato: ClientPlenitude
) -> None:
    """I messaggi d'errore (che finiscono nei log) non contengono il codice del conto."""
    finto.errori["/c360/api/conti"] = HTTPStatus.INTERNAL_SERVER_ERROR
    with pytest.raises(PlenitudeError) as err:
        await client_autenticato.bollette(CONTO_GAS)
    assert CONTO_GAS not in str(err.value)
