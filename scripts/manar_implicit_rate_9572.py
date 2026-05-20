"""
Taux implicite Manar pour 9572 (06/03/2026) — lecture seule, sans modifier le moteur.

Usage : python scripts/manar_implicit_rate_9572.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from backend import main as api
from pricing.curves.zc_interpolation_excel import (
    taux_secondaire_interpole_formule_b,
    vba_interpolate,
    vba_interpolate_extrapolate,
    _dict_to_sorted_arrays,
)


def _bp(a: float, b: float) -> float:
    return (a - b) * 10000.0


def _r_implicite(pm: float, fv: float, jours: int) -> float:
    t = jours / 360.0
    den = fv / pm
    return (den - 1.0) / t


def _prime_rev_aa_pct(spread_dec: float) -> float:
    from obligation_amort_schedule import _prime_pct_excel_rev_aa

    return float(_prime_pct_excel_rev_aa(spread_dec))


def main() -> None:
    iso = "2026-03-06"
    PM = 100245.40
    FV = 104978.19
    jours = 348
    spread_dec = 0.025
    r_moteur_pct = 4.88300
    r_bam_dec = 0.02383379822540846
    r_full_plus = r_bam_dec + spread_dec

    t = jours / 360.0
    r_manar = _r_implicite(PM, FV, jours)
    r_manar_pct = r_manar * 100.0

    r_moteur_dec = r_moteur_pct / 100.0
    courbe_implicite = r_manar - spread_dec
    courbe_implicite_pct = courbe_implicite * 100.0

    print("=== Taux implicite Manar (linéaire ACT/360) ===\n")
    print(f"  PM = {PM:.2f}  FV = {FV:.2f}  jours = {jours}  t = {jours}/360 = {t:.17f}")
    print(f"  1 + r*t = FV/PM = {FV/PM:.12f}")
    print(f"  r_manar (décimal)     = {r_manar:.17f}")
    print(f"  r_manar (%)           = {r_manar_pct:.8f} %")
    print()
    print(f"  r_moteur_arrondi      = {r_moteur_pct:.5f} %  (décimal {r_moteur_dec:.17f})")
    print(f"  r_bam_full+spread     = {r_full_plus*100:.10f} %  (décimal {r_full_plus:.17f})")
    print()
    print(f"  Écart r_manar − r_moteur_arrondi     = {_bp(r_manar, r_moteur_dec):+.4f} bp")
    print(f"  Écart r_manar − r_bam_full+spread    = {_bp(r_manar, r_full_plus):+.4f} bp")
    print()
    print(f"  Taux courbe implicite (r_manar − spread) = {courbe_implicite_pct:.8f} %")
    print(f"                                             = {courbe_implicite:.17f} (décimal)")
    print()

    # BAM du 06/03/2026 (histo MAR_JJ — requiert SQL comme l’API)
    try:
        pillars = api._extraire_piliers_depuis_histo(ROOT, iso, "MAR_JJ")
    except Exception as e:
        print(
            "=== Poursuite BAM : SQL/histo indisponible — calculs d’interpolation ignorés ===\n"
        )
        print(f"  ({type(e).__name__})\n")
        chk = FV / PM / (1 + r_manar * t)
        print(f"  Vérif FV/(PM*(1+r_manar*t)) = {chk:.12f} ( attendu 1.0 )")
        return

    req = api.CurveRequest(
        short=[api.PillarShort(**p) for p in pillars["short"]],
        long=[api.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )
    bam_cc, bam_cl = api._courbes_bam_depuis_requete(req)
    mx, tx = _dict_to_sorted_arrays(bam_cc)
    my, ty = _dict_to_sorted_arrays(bam_cl)
    g2 = float(mx[-1])

    print("=== Poursuite : quelle formule BAM se rapproche du taux courbe implicite ? ===\n")
    print(f"  Dernier pilier CT (G2 équivalent) = {g2:.2f} j")
    print(f"  Zone pour K=348 : {'CT pur' if 348 <= g2 else 'transition MM→monétaire' if 348 < 365 else 'LT'}")
    print()

    def try_label(name: str, r_sec_dec: float) -> None:
        d = (r_sec_dec - courbe_implicite) * 10000.0
        print(f"  {name:<55} {r_sec_dec*100:>14.8f} %  (Δ vs implicite {d:+.4f} bp)")

    # Référence moteur
    for k in (347, 348, 349, 365, 366):
        r_nd = taux_secondaire_interpole_formule_b(float(k), bam_cc, bam_cl, ndigits=None)
        r_6 = taux_secondaire_interpole_formule_b(float(k), bam_cc, bam_cl, ndigits=6)
        try_label(f"Formule B K={k} ndigits=None", r_nd)
        if abs(r_6 - r_nd) > 1e-12:
            try_label(f"Formule B K={k} ndigits=6", r_6)

    # Transition zone explicit ((1+r_lt)^(k/365)-1)*360/k
    if 348 > g2 and 348 < 365.0:
        r_long = float(vba_interpolate_extrapolate(my, ty, 348.0))
        r_mon = ((1.0 + r_long) ** (348.0 / 365.0) - 1.0) * (360.0 / 348.0)
        try_label("Transition: r_lt puis ((1+r_lt)^(348/365)-1)*360/348 brut", r_mon)
        for nd in (5, 6):
            r_l2 = round(r_long + 1e-15, int(nd))
            r_mon2 = ((1.0 + r_l2) ** (348.0 / 365.0) - 1.0) * (360.0 / 348.0)
            try_label(f"Transition: r_lt round {nd} dec puis conversion", r_mon2)

    # Si on interpolait en LT alors que K<365 (erreur de zone)
    r_wrong_lt = float(vba_interpolate_extrapolate(my, ty, 348.0))
    try_label("Erreur zone: LT interpolate_extrapolate seul (sans conversion MM)", r_wrong_lt)

    # CT seul à 348 si on forçait CT (incorrect si 348 > G2)
    if 348 <= g2:
        r_ct = float(vba_interpolate(mx, tx, 348.0))
        try_label("CT seul vba_interpolate(mx,tx,348)", r_ct)

    # Dichotomie : maturité K équivalente sur la même courbe BAM
    print("\n  --- Dichotomie sur K (Formule B ndigits=None, même courbe) ---")
    target = courbe_implicite

    def f_sec(k: float) -> float:
        return taux_secondaire_interpole_formule_b(k, bam_cc, bam_cl, ndigits=None)

    lo, hi = 300.0, 380.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if f_sec(mid) < target:
            lo = mid
        else:
            hi = mid
    k_star = (lo + hi) / 2.0
    print(f"  K tel que secondaire(K) ≈ courbe implicite : K* ≈ {k_star:.6f} j (f={f_sec(k_star):.12f})")
    print(f"  Écart à K=348 : {k_star - 348:+.6f} jours")

    # YTM implicite avec + prime chaîne Manar hypothétique?
    pr_pct = _prime_rev_aa_pct(spread_dec)
    print(f"\n  Prime % Excel REV+AA pour spread {spread_dec}: {pr_pct:.5f} %")

    # Si Manar arrondit courbe à 3 déc puis ajoute prime : taux actu = round(courbe*100,3)/100 + spread?
    print(f"  Si courbe implicite arrondie 3 déc. % puis + prime : TA% = {round(courbe_implicite*100,3) + pr_pct:.5f} %")

    chk = FV / PM / (1 + r_manar * t)
    print(f"\n  Vérif FV/(PM*(1+r_manar*t)) = {chk:.12f} ( attendu 1.0 )")


if __name__ == "__main__":
    main()
