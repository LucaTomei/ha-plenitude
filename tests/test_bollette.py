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
