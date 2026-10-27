"""Test di "Adotta un pannello": quarti d'ora, autoconsumo virtuale, risparmio e sensori."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import itertools

from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.plenitude import pannello
from custom_components.plenitude.account import Conto
from custom_components.plenitude.bollette import BollettaLuce, analizza_luce
from custom_components.plenitude.const import CONF_PREZZO_PANNELLO, DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import FIXTURES, crea_entry_account
from .plenitude_finto import (
    CONSUMO_QUARTO_WH,
    CONTO_GAS,
    CONTO_LUCE,
    FintoPlenitude,
    produzione_quarto,
    produzioni_consumi,
)

ADESSO = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)  # 14:00 in Italia


def _righe(giorno: date) -> list[list]:
    return pannello.righe_giorno(produzioni_consumi("QUARTER_HOURS", giorno))


def _dati(*giorni: date) -> pannello.DatiPannello:
    dati = pannello.DatiPannello(
        pannelli=[pannello.Pannello("SKU", "Prova", "L", 0.9, "ATTIVO", canone=5.0)]
    )
    pannello.unisci(dati, (q for g in giorni for q in pannello.quarti_da_righe(_righe(g))))
    return dati


GIORNI = (date(2026, 10, 23), date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26))


def test_quarti_e_attivazione() -> None:
    """Ora locale convertita in UTC, Wh in kWh, nulla prima del primo quarto d'ora prodotto."""
    dati = _dati(*GIORNI)
    # attivo dalle 00:15 del 24/10 (ora legale, UTC+2)
    assert dati.attivo_dal == datetime(2026, 10, 23, 22, 15, tzinfo=UTC)
    assert min(dati.quarti) == dati.attivo_dal
    q = dati.quarti[datetime(2026, 10, 24, 10, 0, tzinfo=UTC)]  # 12:00 in Italia
    assert q.produzione == round(produzione_quarto(datetime(2026, 10, 24, 12, 0)) / 1000, 6)
    assert q.consumo == CONSUMO_QUARTO_WH / 1000
    assert q.autoconsumo == min(q.produzione, q.consumo)
    assert q.prelievo == pytest.approx(max(q.consumo - q.produzione, 0))
    assert q.immissione == pytest.approx(max(q.produzione - q.consumo, 0))
    # consumi fino alle 02:45 del 25/10 (ora legale), produzione fino al 26/10
    assert dati.ultimo_completo == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
    assert dati.ultima_produzione == datetime(2026, 10, 26, 23, 0, tzinfo=UTC)


def test_cambio_ora() -> None:
    """96 quarti d'ora anche nei giorni del cambio dell'ora: niente buchi né sovrapposizioni."""
    # 25/10/2026: le 2:00-2:59 si ripetono, si attribuiscono alla prima (ora legale)
    autunno = pannello.quarti_da_righe(_righe(date(2026, 10, 25)))
    assert len(autunno) == 96
    assert autunno[8].inizio == datetime(2026, 10, 25, 0, 0, tzinfo=UTC)  # 02:00 CEST
    assert autunno[12].inizio == datetime(2026, 10, 25, 2, 0, tzinfo=UTC)  # 03:00 CET
    # 29/03/2026: le 2:00-2:59 non esistono e si scartano
    righe = [
        [f"2026-03-29T{h:02d}:{m:02d}:00", 0.0, 50.0] for h in range(24) for m in (0, 15, 30, 45)
    ]
    primavera = pannello.quarti_da_righe(righe)
    assert len(primavera) == 92
    inizi = [q.inizio for q in primavera]
    assert len(set(inizi)) == 92
    assert datetime(2026, 3, 29, 1, 0, tzinfo=UTC) in inizi  # 03:00 CEST


def test_serie_orarie() -> None:
    """Le serie orarie tornano con i totali; prelievo + autoconsumo = consumo."""
    dati = _dati(*GIORNI)
    serie = pannello.serie_orarie(dati, lambda _g: 0.2, 1.1)
    completi = [q for q in dati.quarti.values() if q.completo]
    assert sum(serie["produzione"].values()) == pytest.approx(
        sum(q.produzione or 0 for q in dati.quarti.values())
    )
    autoconsumo = sum(serie["autoconsumo"].values())
    assert autoconsumo == pytest.approx(sum(q.autoconsumo for q in completi))
    assert autoconsumo + sum(serie["prelievo"].values()) == pytest.approx(
        sum(q.consumo or 0 for q in completi)
    )
    assert autoconsumo + sum(serie["immissione"].values()) == pytest.approx(
        sum(q.produzione or 0 for q in completi)
    )
    assert sum(serie["risparmio"].values()) == pytest.approx(autoconsumo * 0.2 * 1.1)
    # ore consecutive dall'ora dell'attivazione
    ore = sorted(serie["produzione"])
    assert ore[0] == datetime(2026, 10, 23, 22, 0, tzinfo=UTC)
    assert all(b - a == timedelta(hours=1) for a, b in itertools.pairwise(ore))
    assert max(serie["autoconsumo"]) == datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    # senza prezzo il risparmio si ferma prima della prima ora con autoconsumo
    prima = min(o for o, v in serie["autoconsumo"].items() if v > 0)
    senza = pannello.serie_orarie(dati, lambda _g: None, 1.1)["risparmio"]
    assert senza
    assert max(senza) < prima
    assert not any(senza.values())
    # prezzo noto solo dal 25/10: il 24/10 non ha risparmio, quindi nemmeno i giorni dopo
    parziale = pannello.serie_orarie(dati, lambda g: 0.2 if g >= date(2026, 10, 25) else None, 1.1)[
        "risparmio"
    ]
    assert max(parziale) < prima


def test_riepilogo() -> None:
    """Totali, risparmio con IVA e canone in proporzione ai giorni con i dati."""
    dati = _dati(*GIORNI)
    r = pannello.riepilogo(dati, lambda _g: 0.2, 1.1)
    assert r is not None
    completi = [q for q in dati.quarti.values() if q.completo]
    autoconsumo = sum(q.autoconsumo for q in completi)
    assert r.autoconsumo == round(autoconsumo, 3)
    assert r.risparmio == round(autoconsumo * 0.2 * 1.1, 2)
    # 24 e 25 ottobre: due giorni di canone su 31
    assert r.canone == round(5.0 * 1.1 * 2 / 31, 2)
    assert r.netto == round(r.risparmio - r.canone, 2)
    assert r.copertura == round(100 * autoconsumo / sum(q.consumo or 0 for q in completi), 1)
    # mese senza dati del pannello
    vuoto = pannello.riepilogo(dati, lambda _g: 0.2, 1.1, date(2026, 9, 1))
    assert vuoto is not None
    assert (vuoto.produzione, vuoto.risparmio, vuoto.canone) == (0, 0, 0)
    # prezzo non noto: risparmio sconosciuto, non zero
    assert pannello.riepilogo(dati, lambda _g: None, 1.1).risparmio is None
    assert pannello.riepilogo(pannello.DatiPannello(), lambda _g: 0.2, 1.1) is None


def test_canone_solo_con_pannello_attivo() -> None:
    """Un periodo senza pannello (rimosso e poi riadottato) non paga canone."""
    dati = _dati(*GIORNI)
    # il pannello non produce più il 25/10: quel giorno resta solo il consumo
    for inizio, q in list(dati.quarti.items()):
        if pannello.giorno_locale(inizio) == date(2026, 10, 25):
            dati.quarti[inizio] = pannello.Quarto(inizio, None, q.consumo)
    r = pannello.riepilogo(dati, lambda _g: 0.2, 1.1)
    assert r is not None
    assert r.canone == round(5.0 * 1.1 / 31, 2)  # solo il 24/10


def test_giorni_da_leggere() -> None:
    """Si chiedono i giorni mai letti, quelli recenti e quelli ancora incompleti."""
    dati = _dati(*GIORNI)
    salvati = {g.isoformat(): _righe(g) for g in GIORNI}
    giorni = pannello.giorni_da_leggere(salvati, date(2026, 10, 24), ADESSO, 31, dati.attivo_dal)
    assert giorni == [date(2026, 10, d) for d in (24, 25, 26, 27)]
    # dieci giorni dopo: prima gli ultimi quattro giorni, poi quelli ancora incompleti (25 e
    # 26/10) e quelli mai letti; il 24/10, completo, non si chiede più
    dopo = ADESSO + timedelta(days=10)
    giorni = pannello.giorni_da_leggere(salvati, date(2026, 10, 24), dopo, 31, dati.attivo_dal)
    assert giorni[:4] == [date(2026, 11, d) for d in (3, 4, 5, 6)]
    assert giorni[4:] == [
        *(date(2026, 10, d) for d in range(25, 32)),
        *(date(2026, 11, d) for d in (1, 2)),
    ]
    # limite di giorni per aggiornamento: i più recenti ci sono sempre
    lunghi = pannello.giorni_da_leggere({}, date(2026, 1, 1), ADESSO, 31)
    assert len(lunghi) == 31
    assert lunghi[:4] == [date(2026, 10, d) for d in (24, 25, 26, 27)]
    assert lunghi[4] == date(2026, 1, 1)


def test_risposte() -> None:
    """Pannelli, canone e potenza istantanea dalle risposte dell'area clienti."""
    pannelli = pannello.pannelli_da(
        {
            "panels": [
                {
                    "identifier": "SKU_A",
                    "position": "X",
                    "size": "L",
                    "power": "0.9",
                    "state": "ATTIVO",
                },
                {"identifier": "SKU_B", "power": "0.3", "state": "CESSATO"},
                {"position": "senza codice"},
            ]
        }
    )
    assert [(p.codice, p.potenza) for p in pannelli] == [("SKU_A", 0.9)]
    assert pannello.pannelli_da({"panels": None}) == []
    catalogo = {
        "body": {
            "virtualPanel": {"locations": [{"sizes": [{"productId": "SKU_A", "price": "5.00"}]}]}
        }
    }
    assert pannello.canone_da(catalogo, "SKU_A") == 5.0
    assert pannello.canone_da(catalogo, "SKU_Z") is None
    assert pannello.potenza_da([{"instantProductionWh": 577.58}, {"x": 1}]) == 577.58
    assert pannello.potenza_da([]) is None
    # granularità in kWh al posto dei quarti d'ora: ignorata
    assert pannello.righe_giorno({"measureUnit": "KWh", "energyProducedConsumed": []}) == []


def test_prezzo_dalle_bollette() -> None:
    """Bolletta per i giorni fatturati, poi opzione, poi ultima bolletta delle condizioni in corso."""
    conto = Conto(codice=CONTO_LUCE, commodity="luce")
    conto.condizioni_dal = date(2026, 10, 20)
    vecchia = BollettaLuce(dal=date(2026, 8, 1), al=date(2026, 9, 30))
    vecchia.corrispettivi = [(date(2026, 8, 1), date(2026, 9, 30), 0.25)]
    nuova = BollettaLuce(dal=date(2026, 10, 1), al=date(2026, 11, 30))
    nuova.corrispettivi = [
        (date(2026, 10, 1), date(2026, 10, 19), 0.25),
        (date(2026, 10, 20), date(2026, 11, 30), 0.21),
    ]
    conto.bollette_luce = {"1": vecchia}
    prezzo = pannello.prezzo_pannello(conto)
    assert prezzo(date(2026, 9, 1)) == 0.25
    # condizioni cambiate il 20/10 e non ancora fatturate: nessuna stima
    assert prezzo(date(2026, 10, 25)) is None
    assert pannello.prezzo_pannello(conto, 0.19)(date(2026, 10, 25)) == 0.19
    conto.bollette_luce = {"1": vecchia, "2": nuova}
    prezzo = pannello.prezzo_pannello(conto, 0.19)
    assert prezzo(date(2026, 10, 25)) == 0.21  # la bolletta prevale sull'opzione
    assert pannello.prezzo_pannello(conto)(date(2026, 12, 5)) == 0.21  # stima


def test_corrispettivo_dalla_bolletta() -> None:
    """Corrispettivo Luce con perdite e sconti a consumo, al netto delle altre voci."""
    testo = (FIXTURES / "bolletta-luce-sintetica.txt").read_text(encoding="utf-8")
    testo = testo.replace(
        "Contributo al consumo tipo prezzo prezzo kWh € regime iva",
        "Perdite su Corrispettivo Luce Index tipo prezzo prezzo kWh € regime iva\n"
        "DAL 01/08/2026 AL 31/08/2026 €/kWh 0,17999900 47 8,46 AGE\n"
        "DAL 01/09/2026 AL 30/09/2026 €/kWh 0,20754200 24 4,98 AGE\n"
        "Sconto Special Days tipo prezzo prezzo kWh € regime iva\n"
        "DAL 01/09/2026 AL 30/09/2026 €/kWh -0,06226260 240 -14,94 AGE\n"
        "Contributo al consumo tipo prezzo prezzo kWh € regime iva",
    )
    luce = analizza_luce(testo)
    assert luce is not None
    assert luce.corrispettivi == [
        (date(2026, 8, 1), date(2026, 8, 31), round((84.24 + 8.46) / 468, 6)),
        (date(2026, 9, 1), date(2026, 9, 30), round((49.81 + 4.98 - 14.94) / 240, 6)),
    ]


def _stato(hass: HomeAssistant, chiave: str):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{CONTO_LUCE}_{chiave}")
    assert entity_id is not None, chiave
    return hass.states.get(entity_id)


async def test_sensori_pannello(
    hass: HomeAssistant, finto: FintoPlenitude, freezer: FrozenDateTimeFactory
) -> None:
    """Sensori del pannello sulla fornitura di luce, dall'attivazione; nessuno sul gas."""
    freezer.move_to(ADESSO)
    entry = crea_entry_account(hass, **{CONF_PREZZO_PANNELLO: 0.2})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    dati = _dati(*GIORNI, date(2026, 10, 27))
    atteso = pannello.riepilogo(dati, lambda _g: 0.2, 1.1, date(2026, 10, 1))
    assert atteso is not None

    potenza = _stato(hass, "pannello_potenza")
    assert potenza.state == "577.58"
    assert potenza.attributes["unit_of_measurement"] == "W"
    assert potenza.attributes["potenza_nominale_kw"] == 0.9
    assert potenza.attributes["pannelli"][0]["canone_mensile"] == 5.0
    assert potenza.attributes["attivo_dal"].startswith("2026-10-23T15:15:00")  # ora del Pacifico
    produzione = _stato(hass, "pannello_produzione_mese")
    assert float(produzione.state) == atteso.produzione
    assert float(_stato(hass, "pannello_autoconsumo_mese").state) == atteso.autoconsumo
    risparmio = _stato(hass, "pannello_risparmio_mese")
    assert float(risparmio.state) == atteso.risparmio
    assert risparmio.attributes["canone"] == atteso.canone
    assert risparmio.attributes["netto"] == atteso.netto
    assert risparmio.attributes["prezzo_kwh"] == 0.2
    assert float(_stato(hass, "pannello_risparmio_totale").state) == atteso.risparmio
    assert float(_stato(hass, "pannello_prezzo").state) == 0.2
    registro = er.async_get(hass)
    assert registro.async_get_entity_id("sensor", DOMAIN, f"{CONTO_GAS}_pannello_potenza") is None

    # aggiornamento dei soli pannelli: nuova potenza, nessuna richiesta del resto dell'account
    finto.potenza_istantanea = 120.0
    finto.richieste_pannelli.clear()
    await entry.runtime_data.async_aggiorna_pannelli()
    await hass.async_block_till_done()
    assert _stato(hass, "pannello_potenza").state == "120.0"
    assert finto.richieste_pannelli == ["supply/1-PROVA01/instant-production"]

    # dopo un'ora si rileggono i giorni recenti e incompleti, non lo storico già completo
    freezer.tick(timedelta(hours=1))
    finto.richieste_pannelli.clear()
    await entry.runtime_data.async_aggiorna_pannelli()
    await hass.async_block_till_done()
    assert (
        finto.richieste_pannelli.count("pod/IT001E00000001/productions-consumes QUARTER_HOURS") == 4
    )
    assert not any("YEARLY" in r for r in finto.richieste_pannelli)


async def test_pannello_dalla_memoria(
    hass: HomeAssistant, finto: FintoPlenitude, freezer: FrozenDateTimeFactory
) -> None:
    """Dopo un riavvio i quarti d'ora già letti non si richiedono; il pannello rimosso sparisce."""
    freezer.move_to(ADESSO)
    entry = crea_entry_account(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    # senza opzione vale il Corrispettivo Luce dell'ultima bolletta (settembre)
    risparmio = _stato(hass, "pannello_risparmio_mese")
    assert risparmio.attributes["prezzo_kwh"] == round(49.81 / 240, 6)
    dati = _dati(*GIORNI, date(2026, 10, 27))
    atteso = pannello.riepilogo(dati, lambda _g: round(49.81 / 240, 6), 1.1, date(2026, 10, 1))
    assert atteso is not None
    assert float(risparmio.state) == atteso.risparmio
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    finto.richieste_pannelli.clear()
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    letti = [r for r in finto.richieste_pannelli if "QUARTER_HOURS" in r]
    assert len(letti) == 4  # solo i giorni recenti, l'attivazione è già nota
    assert not any("YEARLY" in r or "MONTHLY" in r for r in finto.richieste_pannelli)

    finto.pannelli = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _stato(hass, "pannello_potenza").state == "unavailable"


async def test_senza_pannello(hass: HomeAssistant, finto: FintoPlenitude) -> None:
    """Una fornitura senza pannelli non ha i sensori del pannello."""
    finto.pannelli = []
    entry = crea_entry_account(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registro = er.async_get(hass)
    assert registro.async_get_entity_id("sensor", DOMAIN, f"{CONTO_LUCE}_pannello_potenza") is None
    assert finto.richieste_pannelli == ["supply/1-PROVA01/details"]
