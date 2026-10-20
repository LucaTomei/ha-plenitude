"""Test della lettura delle bollette e del calcolo dei periodi tariffari."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from custom_components.plenitude import bollette

FIXTURES = Path(__file__).parent / "fixtures"


def _testo(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def test_metadati() -> None:
    """Periodo, PDR, coefficiente, consumo e totale vengono letti correttamente."""
    b = bollette.analizza(_testo("bolletta-sintetica.txt"))
    assert b is not None
    assert b.dal == date(2026, 6, 1)
    assert b.al == date(2026, 7, 31)
    assert b.pdr == "01234567890123"
    assert b.coefficiente == 0.99
    assert b.consumo_smc == 20
    assert b.importo == 50.0


def test_non_e_bolletta_gas() -> None:
    """Un testo che non è una bolletta gas Plenitude dà None."""
    assert bollette.analizza("una pagina qualsiasi") is None
    assert bollette.analizza("Periodo di fatturazione: dal 01/01/2026 al 31/01/2026") is None


def test_numero_italiano() -> None:
    """Conversione dei numeri in formato italiano."""
    assert bollette._numero("1.234,56") == 1234.56
    assert bollette._numero("0,50000000") == 0.5
    assert bollette._numero("-1,00") == -1.0


def test_voci_estratte() -> None:
    """Tutte le voci di dettaglio vengono riconosciute, con scaglione e regime."""
    b = bollette.analizza(_testo("bolletta-sintetica.txt"))
    ruoli = {v.ruolo for v in b.voci}
    assert ruoli == {
        "commercializzazione",
        "materia",
        "contributo",
        "quota_fissa",
        "distribuzione",
        "trasporto",
        "oneri",
        "accisa",
        "addizionale",
    }
    materia = sorted((v for v in b.voci if v.ruolo == "materia"), key=lambda v: v.dal)
    assert [v.prezzo for v in materia] == [0.5, 0.6]
    assert all(v.agevolata for v in materia)
    fisse = [v for v in b.voci if v.fissa]
    assert {v.ruolo for v in fisse} == {"commercializzazione", "quota_fissa"}
    assert all(not v.agevolata for v in fisse)


def test_periodi_sintetica() -> None:
    """Prezzi per scaglione e quota fissa calcolati dalla bolletta sintetica."""
    b = bollette.analizza(_testo("bolletta-sintetica.txt"))
    periodi = {p.dal: p for p in bollette.periodi_tariffari([b])}
    assert set(periodi) == {date(2026, 6, 1), date(2026, 7, 1)}

    giugno = periodi[date(2026, 6, 1)]
    assert giugno.prezzo_sc1 == pytest.approx(0.84 * 1.10)
    assert giugno.prezzo_sc2 == pytest.approx(1.11 * 1.10)
    assert giugno.prezzo_sc3 == pytest.approx(1.11 * 1.22)
    assert giugno.quota_fissa == pytest.approx((10 + 5) * 1.22)

    luglio = periodi[date(2026, 7, 1)]
    assert luglio.prezzo_sc2 == pytest.approx(1.21 * 1.10)
    # la materia cambia, le tariffe regolate restano
    assert luglio.prezzo_sc1 == pytest.approx(0.94 * 1.10)
    assert luglio.quota_fissa == pytest.approx((10 + 5) * 1.22)


def test_quota_fissa_distribuita_sui_mesi() -> None:
    """La quota fissa di una bolletta bimestrale vale per entrambi i mesi, non solo il primo."""
    b = bollette.analizza(_testo("bolletta-sintetica.txt"))
    periodi = bollette.periodi_tariffari([b])
    assert len(periodi) == 2
    assert periodi[0].quota_fissa == pytest.approx(periodi[1].quota_fissa)
    assert periodi[0].quota_fissa > 0


# --- Bollette reali (anonimizzate): il prezzo dello scaglione consumato deve tornare al centesimo.

# scaglione di consumo e prezzo €/Smc atteso (ricavato a mano dalle bollette)
_REALI = {
    "bolletta-gas-202501022.txt": [(date(2025, 8, 1), 2, 1.1942), (date(2025, 9, 1), 2, 1.1856)],
    "bolletta-gas-20260219.txt": [(date(2025, 12, 1), 2, 1.1522), (date(2026, 1, 1), 1, 0.8508)],
    "bolletta-gas-20260706.txt": [(date(2026, 4, 1), 2, 1.3460), (date(2026, 5, 1), 2, 1.3568)],
    "bolletta-gas-20260828.txt": [(date(2026, 6, 1), 2, 1.3603), (date(2026, 7, 1), 2, 1.4764)],
}


def test_bollette_reali_prezzo_scaglione_consumato() -> None:
    """Su tutte le bollette reali il prezzo dello scaglione consumato coincide con la bolletta."""
    tutte = [bollette.analizza(_testo(n)) for n in _REALI]
    assert all(b is not None for b in tutte)
    periodi = {p.dal: p for p in bollette.periodi_tariffari(tutte)}
    for righe in _REALI.values():
        for mese, scaglione, atteso in righe:
            prezzo = (periodi[mese].prezzo_sc1, periodi[mese].prezzo_sc2, periodi[mese].prezzo_sc3)[
                scaglione - 1
            ]
            assert prezzo == pytest.approx(atteso, abs=0.001), f"{mese} sc{scaglione}"


def test_bollette_reali_coefficiente_e_totali() -> None:
    """Coefficiente C e totale di ogni bolletta reale."""
    attesi = {
        "bolletta-gas-202501022.txt": (0.992189, 68.01),
        "bolletta-gas-20260219.txt": (0.992189, 222.68),
        "bolletta-gas-20260706.txt": (0.992189, 102.72),
        "bolletta-gas-20260828.txt": (0.992189, 63.32),
    }
    for nome, (coeff, totale) in attesi.items():
        b = bollette.analizza(_testo(nome))
        assert b.coefficiente == coeff
        assert b.importo == totale
        assert b.pdr == "01234567890123"


def test_quota_fissa_sempre_positiva_e_costante_nel_trimestre() -> None:
    """La quota fissa è coerente tra i mesi dello stesso trimestre."""
    tutte = [bollette.analizza(_testo(n)) for n in _REALI]
    periodi = bollette.periodi_tariffari(tutte)
    for p in periodi:
        assert 10 < p.quota_fissa < 30


def test_formato_2024() -> None:
    """Le bollette fino all'inizio del 2025 hanno intestazioni e date in un altro formato."""
    b = bollette.analizza(_testo("bolletta-gas-formato-2024.txt"))
    assert b is not None
    assert (b.dal, b.al) == (date(2024, 10, 1), date(2024, 11, 30))
    assert b.emissione == date(2024, 12, 4)
    assert b.importo == 109.14
    assert b.consumo_smc == 58
    assert b.coefficiente == 0.992189
    assert b.pdr == "01234567890123"
    assert {v.ruolo for v in b.voci} == {
        "commercializzazione",
        "sconto",
        "contributo",
        "materia",
        "quota_fissa",
        "distribuzione",
        "trasporto",
        "oneri",
        "accisa",
        "addizionale",
    }
    # la riga dopo il cambio pagina resta della voce "Addizionale enti locali"
    addizionale = [v for v in b.voci if v.ruolo == "addizionale"]
    assert len(addizionale) == 4
    # 32 righe di dettaglio, di cui 4 senza ruolo noto (CO2, oneri aggiuntivi) ignorate
    assert len(b.voci) == 28

    (ottobre, novembre) = bollette.periodi_tariffari([b])
    assert ottobre.dal == date(2024, 10, 1)
    assert novembre.quota_fissa == ottobre.quota_fissa
    # quota fissa: (10 - 1 + 7,01416667 - 1,9275) €/mese con IVA al 22%
    assert ottobre.quota_fissa == pytest.approx((10 - 1 + 7.01416667 - 1.9275) * 1.22, abs=1e-3)


def test_periodo_in_lettere_non_valido() -> None:
    """Mese sconosciuto o date impossibili: la bolletta non viene letta."""
    testo = "Periodo di riferimento X\nda 1 Brumaio 2024 Y\na 30 Novembre 2024\nElementi di dettaglio gas"
    assert bollette.analizza(testo) is None
    testo = "Periodo di riferimento X\nda 31 Febbraio 2024 Y\na 30 Novembre 2024\nElementi di dettaglio gas"
    assert bollette.analizza(testo) is None


def test_scadenza_e_date_senza_spazi() -> None:
    """Una scadenza d'esempio nelle pagine informative non viene scambiata per quella vera."""
    testo = (
        "Periodo di fatturazione: dal 01/06/2025 al 31/07/2025\n"
        "Totale da pagare entro il 20/07/2025\n"
        "Fattura elettronica numero1valida ai fini fiscali - Data di emissione21/08/2025\n"
        "Totale da pagare entro il15/09/2025\n"
        "In un anno hai consumato 440 smc\n"
        "Elementi di dettaglio Gas\n"
    )
    b = bollette.analizza(testo)
    assert b.emissione == date(2025, 8, 21)
    assert b.scadenza == date(2025, 9, 15)
    assert b.consumo_annuo_smc == 440


def test_mesi_rifatturati_non_sommati() -> None:
    """Se una bolletta rifattura un mese già fatturato vale la più recente, senza doppioni."""
    prima = bollette.analizza(_testo("bolletta-gas-formato-2024.txt"))
    seconda = bollette.Bolletta(
        dal=date(2024, 12, 1),
        al=date(2025, 1, 31),
        emissione=date(2025, 2, 14),
        voci=[
            bollette.VoceCosto(
                "commercializzazione", date(2024, 11, 1), date(2024, 12, 31), 12.0, 0, True, False
            ),
            bollette.VoceCosto(
                "materia", date(2024, 11, 1), date(2024, 11, 30), 0.5, 0, False, True
            ),
        ],
    )
    periodi = {p.dal: p for p in bollette.periodi_tariffari([prima, seconda])}
    assert periodi[date(2024, 10, 1)].quota_fissa == pytest.approx(
        (10 - 1 + 7.01416667 - 1.9275) * 1.22, abs=1e-3
    )
    # novembre: solo la quota della seconda bolletta, non la somma delle due
    assert periodi[date(2024, 11, 1)].quota_fissa == pytest.approx(12 * 1.22, abs=1e-3)
    assert periodi[date(2024, 12, 1)].quota_fissa == pytest.approx(12 * 1.22, abs=1e-3)


@pytest.mark.parametrize(
    "nome",
    [
        "bolletta-sintetica.txt",
        "bolletta-gas-20260828.txt",
        "bolletta-gas-formato-2024.txt",
    ],
)
def test_cache_andata_e_ritorno(nome: str) -> None:
    """La forma salvata nella cache ricostruisce la stessa bolletta."""
    b = bollette.analizza(_testo(nome))
    assert bollette.bolletta_da_dict(bollette.bolletta_a_dict(b)) == b


def test_bolletta_luce() -> None:
    """Dati della bolletta luce: consumi, potenza, quote, imposte e letture per fascia."""
    b = bollette.analizza_luce(_testo("bolletta-luce-sintetica.txt"))
    assert b is not None
    assert (b.dal, b.al) == (date(2026, 8, 1), date(2026, 9, 30))
    assert (b.emissione, b.scadenza) == (date(2026, 10, 9), date(2026, 11, 3))
    assert b.importo == 328.78
    assert b.totale_fornitura == 310.78  # senza canone RAI
    assert (b.consumo_kwh, b.consumo_annuo_kwh) == (708, 2622)
    assert (b.potenza_impegnata, b.potenza_disponibile) == (3, 3.3)
    assert (b.consumo_quota_kwh, b.prezzo_quota_consumi, b.importo_quota_consumi) == (
        708,
        0.326864,
        231.42,
    )
    assert (b.quota_fissa_mese, b.quota_potenza_kw_mese, b.imposte) == (13.01, 1.976667, 13.23)
    assert b.data_lettura == date(2026, 9, 30)
    assert b.letture == {"F1": 3526, "F2": 2150, "F3": 3158}
    assert b.lettura_totale == 3526 + 2150 + 3158
    assert b.prezzo_energia == round((231.42 + 13.23) * 1.10 / 708, 4)
    assert b.indici == {date(2026, 8, 1): 0.179999, date(2026, 9, 1): 0.207542}
    assert (b.contributo, b.dispacciamento) == (0.041, 0.02556)
    assert bollette.bolletta_luce_da_dict(bollette.bolletta_luce_a_dict(b)) == b
    # una bolletta luce non è una bolletta gas, e viceversa
    assert bollette.analizza(_testo("bolletta-luce-sintetica.txt")) is None
    assert bollette.analizza_luce(_testo("bolletta-gas-20260828.txt")) is None


def test_bolletta_luce_incompleta() -> None:
    """Se mancano dei riquadri la bolletta si legge lo stesso, con i soli dati presenti."""
    testo = "Periodo di fatturazione: dal 01/08/2026 al 30/09/2026\nElementi di dettaglio Luce\n"
    b = bollette.analizza_luce(testo)
    assert b is not None
    assert b.consumo_kwh is None
    assert b.prezzo_energia is None
    assert b.lettura_totale is None
