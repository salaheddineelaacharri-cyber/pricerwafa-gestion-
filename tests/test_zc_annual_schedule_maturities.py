"""Grille maturités échéancier ZC annuel (calendrier + fusion CT)."""
from __future__ import annotations

from datetime import date

from backend.main import _calendar_year_spot_maturity_days


def test_calendar_spots_2026_03_26_two_years_includes_leap() -> None:
    d0 = date(2026, 3, 26)
    spots = _calendar_year_spot_maturity_days(d0, start_n=2, end_n=6)
    assert spots == [731.0, 1096.0, 1461.0, 1826.0, 2192.0]


def test_merge_ct_365_calendar_prefix_2026_03_26() -> None:
    """Validation demandée : préfixe 1, 53, 144, 326, 365, 731, … pour ancrage 26/03/2026."""
    d0 = date(2026, 3, 26)
    ct = [1.0, 53.0, 144.0, 326.0]
    merged = sorted({*ct, 365.0, *_calendar_year_spot_maturity_days(d0)})
    assert merged[:10] == [1.0, 53.0, 144.0, 326.0, 365.0, 731.0, 1096.0, 1461.0, 1826.0, 2192.0]


def test_merge_ct_365_calendar_prefix_2026_03_06() -> None:
    """CT type BAM 06/03/2026 + 365 + calendrier (2e année = 731 j avec passage bissextile)."""
    d0 = date(2026, 3, 6)
    ct = [1.0, 73.0, 164.0, 192.0]
    merged = sorted({*ct, 365.0, *_calendar_year_spot_maturity_days(d0)})
    assert merged[:5] == [1.0, 73.0, 164.0, 192.0, 365.0]
    assert merged[5] == 731.0
