"""Test della lettura dei dati JSON dell'account."""

from __future__ import annotations

from datetime import date

from custom_components.plenitude import account

from .plenitude_finto import AUTOLETTURE, BOLLETTE, CONSUMI, CONTI, CONTO_GAS, CONTO_LUCE, FORNITURE


def test_conti() -> None:
    """Solo le voci con un codice; luce e gas normalizzati, gli altri servizi senza tipo."""
    conti = [account.conto_da(v) for v in CONTI]
    assert [(c.codice, c.commodity) for c in conti if c] == [
        (CONTO_GAS, "gas"),
        (CONTO_LUCE, "luce"),
        ("500000000003", None),
    ]
    assert account.conto_da({"codice": ""}) is None


def test_fornitura() -> None:
    """PDR per il gas, POD per la luce."""
    gas = account.conto_da(CONTI[0])
    account.aggiungi_fornitura(gas, FORNITURE[CONTO_GAS])
    assert (gas.pdr, gas.pod, gas.matricola) == ("01234567890123", None, "MAT0000000001")
    luce = account.conto_da(CONTI[1])
    account.aggiungi_fornitura(luce, FORNITURE[CONTO_LUCE])
    assert (luce.pdr, luce.pod) == (None, "IT001E00000001")
    # nessuna fornitura: il conto resta com'è
    vuoto = account.conto_da(CONTI[0])
    account.aggiungi_fornitura(vuoto, [])
    assert vuoto.pdr is None


def test_fornitura_attiva_prima() -> None:
    """Se ci sono più forniture si usa quella attiva."""
    conto = account.conto_da(CONTI[0])
    account.aggiungi_fornitura(
        conto,
        [
            {"tipo": "GAS", "pdr": "vecchio", "status": "CESSATO"},
            {"tipo": "GAS", "pdr": "nuovo", "status": "ATTIVO"},
        ],
    )
    assert conto.pdr == "nuovo"


def test_bollette() -> None:
    """Bollette dalla più recente, importi da pagare e totale."""
    conto = account.conto_da(CONTI[1])
    conto.bollette = account.bollette_da(list(reversed(BOLLETTE[CONTO_LUCE])))
    assert [b.numero for b in conto.bollette] == ["9100000002", "9100000001"]
    ultima = conto.ultima
    assert ultima.emissione == date(2026, 10, 9)
    assert ultima.scadenza == date(2026, 11, 3)
    assert ultima.importo == 328.78
    assert conto.totale_da_pagare == 349.28
    assert [b.numero for b in conto.da_pagare] == ["9100000001", "9100000002"]


def test_bollette_valori_strani() -> None:
    """Voci senza numero ignorate; importi in formato italiano o mancanti gestiti."""
    elenco = account.bollette_da(
        [
            {"numeroBolletta": None},
            {"numeroBolletta": "1", "importo": "1.234,56", "importoPagare": None},
            {"numeroBolletta": "2", "importo": None, "importoPagare": "-3.0", "emissione": "x"},
        ]
    )
    assert [(b.numero, b.importo, b.da_pagare, b.emissione) for b in elenco] == [
        ("2", None, 0.0, None),
        ("1", 1234.56, 0.0, None),
    ]


def test_consumi_gas() -> None:
    """Consumi mensili in ordine; il mese parziale non conta come ultimo mese completo."""
    conto = account.conto_da(CONTI[0])
    conto.consumi = account.consumi_da(CONSUMI[CONTO_GAS], "gas")
    assert [c.mese for c in conto.consumi][:2] == [date(2025, 11, 1), date(2025, 12, 1)]
    assert conto.consumi[-1].parziale
    assert conto.ultimo_consumo.mese == date(2026, 9, 1)
    assert conto.ultimo_consumo.valore == 17
    assert conto.consumo_12_mesi == 40 + 90 + 104 + 17


def test_consumi_luce_fasce() -> None:
    """Per la luce il consumo è la somma delle fasce F1, F2, F3."""
    consumi = account.consumi_da(CONSUMI[CONTO_LUCE], "luce")
    assert consumi[-1].valore == 96 + 63 + 81
    assert consumi[-1].fasce == {"F1": 96, "F2": 63, "F3": 81}


def test_consumi_vuoti_o_altro_tipo() -> None:
    """Nessun consumo per il tipo richiesto → lista vuota."""
    assert account.consumi_da([], "gas") == []
    assert account.consumi_da(CONSUMI[CONTO_LUCE] + CONSUMI[CONTO_GAS], "gas")[0].fasce == {}
    assert (
        account.consumi_da(
            [{"type": "GAS", "consumptions": [{"year": "2026", "numericalMonth": 13}]}], "gas"
        )
        == []
    )


def test_autolettura() -> None:
    """Ultima lettura e finestra di autolettura."""
    conto = account.conto_da(CONTI[0])
    account.aggiungi_autolettura(conto, AUTOLETTURE[CONTO_GAS])
    assert conto.lettura == 1637
    assert conto.data_lettura == date(2026, 9, 30)
    assert (conto.autolettura_dal, conto.autolettura_al) == (date(2026, 11, 24), date(2026, 11, 28))
    luce = account.conto_da(CONTI[1])
    account.aggiungi_autolettura(luce, AUTOLETTURE[CONTO_LUCE])
    assert luce.lettura is None
    account.aggiungi_autolettura(luce, None)
    assert luce.autolettura_dal == date(2026, 11, 24)


def test_conto_vuoto() -> None:
    """Un conto senza dati non solleva errori."""
    conto = account.conto_da(CONTI[0])
    assert conto.ultima is None
    assert conto.ultimo_consumo is None
    assert conto.consumo_12_mesi is None
    assert conto.totale_da_pagare == 0
    assert conto.ultima_pdf is None
