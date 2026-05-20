"""
Autopsie mathématique REV + AA + in fine (sans modifier le moteur).

Reproduit le chemin ``marche_valorize`` : Formule B BAM avec ``ndigits=None``,
puis ``construire_tables_amortissement_pour_valorisation`` (grille = prix Manar / UI).

Usage (racine projet) ::
  python scripts/autopsie_rev_code.py 9572 2026-03-26
  python scripts/autopsie_rev_code.py 9572 2026-03-26 --manar 100245.40
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backend import main as api
from obligation_amort_schedule import _prime_pct_excel_rev_aa
from obligation_amort_schedule import construire_tables_amortissement_pour_valorisation
from pricing.curves.zc_interpolation_excel import taux_secondaire_interpole_formule_b
from valuation_zc_obligations import charger_courbe_zc_depuis_fichier
from valuation_zc_obligations import detecter_colonnes_base_titre
from valuation_zc_obligations import valoriser_dataframe_base_titre


def _norm_code(v: object) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _row_vals_by_label(tab: dict[str, Any], label: str) -> list[Any]:
    for r in tab.get("rows") or []:
        if str(r.get("label")) == label:
            return list(r.get("values") or [])
    return []


def _idx_future_col(cols_iso: list[str], d_valo_iso: str) -> int:
    from datetime import date

    dv = date.fromisoformat(d_valo_iso[:10])
    for i, c in enumerate(cols_iso):
        d = date.fromisoformat(str(c)[:10])
        if d > dv:
            return i
    return -1


def _prix_rev_linear(fv: float, r_dec: float, jours: int) -> tuple[float, float, float]:
    """Même noyau que le moteur : t = j/360, prix = round(fv/(1+r*t)+1e-12, 5)."""
    t = max(0, int(jours)) / 360.0
    den = 1.0 + float(r_dec) * float(t)
    if den <= 0.0 or not math.isfinite(den):
        return 0.0, t, den
    raw = float(fv) / den
    return round(raw + 1e-12, 5), t, den


def _chain_prix_clean(prix_rev_5: float) -> tuple[float, float, float]:
    """Colonnes : ARRONDI 5 dec puis ARRONDI 4 (flux act.) puis ARRONDI 2 (clean cible YTM)."""
    p4 = round(float(prix_rev_5) + 1e-12, 4)
    sum_act = p4  # une seule colonne REV
    clean2 = round(sum_act + 1e-12, 2)
    return p4, sum_act, clean2


def main() -> None:
    ap = argparse.ArgumentParser(description="Autopsie REV/AA/FIN pour un code titre.")
    ap.add_argument("code", help="Code Maroclear, ex. 9572")
    ap.add_argument("date_iso", help="Date valorisation AAAA-MM-JJ")
    ap.add_argument("--manar", type=float, default=None, help="Prix Manar de référence (override fichier)")
    args = ap.parse_args()
    code = _norm_code(args.code)
    iso = str(args.date_iso).strip()[:10]

    zc_path = ROOT / "pricing/curves/courbe_zc.py"
    courbe_zc = charger_courbe_zc_depuis_fichier(zc_path)
    pillars = api._extraire_piliers_depuis_histo(ROOT, iso, "MAR_JJ")
    req_curve = api.CurveRequest(
        short=[api.PillarShort(**p) for p in pillars["short"]],
        long=[api.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )
    bam_cc, bam_cl = api._courbes_bam_depuis_requete(req_curve)

    def ts_engine(j: float) -> float:
        return float(
            taux_secondaire_interpole_formule_b(float(j), bam_cc, bam_cl, ndigits=None)
        )

    xlsx = api.resoudre_fichier_base_titre_oblig(ROOT, None)
    df_in = api._charger_base_titre_oblg_cache(xlsx, [code])
    if df_in.empty:
        print("ERREUR : aucune ligne référentiel pour ce code.", flush=True)
        sys.exit(1)

    df_out, _meta = valoriser_dataframe_base_titre(
        df_in,
        courbe_zc,
        valuation_date=iso,
        bam_courbe_court=bam_cc,
        bam_courbe_long=bam_cl,
        progress_label=None,
    )
    col_code = None
    for c in df_out.columns:
        if str(c).strip().upper() == "CODE":
            col_code = c
            break
    raw: dict[str, Any] = {}
    for _, row in df_out.iterrows():
        d = row.to_dict()
        cc = _norm_code(d.get(col_code) if col_code else d.get("CODE"))
        if cc == code:
            raw = d
            break
    if not raw:
        print("ERREUR : valorisation n'a pas produit ce code.", flush=True)
        sys.exit(1)

    ui = api._row_to_marche_ui(raw, iso)
    det = detecter_colonnes_base_titre(df_in)
    amort_tables = construire_tables_amortissement_pour_valorisation(
        xlsx,
        [raw],
        [ui],
        valuation_date=iso,
        taux_secondaire_a_j=ts_engine,
        taux_zc_schedule_j=None,
        taux_zc_schedule_a=None,
        df_work=df_in,
        col_code_fichier=det.get("col_code") or col_code,
        det_cols=det,
        codes_filter=[code],
    )
    tab = next((t for t in amort_tables if _norm_code(t.get("code")) == code), None)
    if not tab:
        print("ERREUR : pas de tableau d'amortissement pour ce code.", flush=True)
        sys.exit(1)

    dbg = tab.get("debug_rev") or {}
    cols = [str(c) for c in (tab.get("columns") or [])]
    d_valo_iso = str(tab.get("date_valorisation_utilisee_iso") or dbg.get("date_valorisation") or iso)
    i_rev = _idx_future_col(cols, d_valo_iso)
    if i_rev < 0:
        print("ERREUR : pas de colonne future > date valo.", flush=True)
        sys.exit(1)

    flux = [float(x or 0.0) for x in _row_vals_by_label(tab, "Flux")]
    cap = [float(x or 0.0) for x in _row_vals_by_label(tab, "Capital restant")]
    intr = [float(x or 0.0) for x in _row_vals_by_label(tab, "Intérêts")]
    flux_rest = [float(x or 0.0) for x in _row_vals_by_label(tab, "Flux restant")]
    taux_aa = [float(x or 0.0) for x in _row_vals_by_label(tab, "Taux AA")]
    prime = [float(x or 0.0) for x in _row_vals_by_label(tab, "Prime")]
    taux_actu = [float(x or 0.0) for x in _row_vals_by_label(tab, "Taux d'actualisation")]
    duree_disp = _row_vals_by_label(tab, "durée")
    flux_act = [float(x or 0.0) for x in _row_vals_by_label(tab, "Flux actualisé")]

    jours = int(dbg.get("jours_calculs") or max(0, (pd.Timestamp(cols[i_rev]).date() - pd.Timestamp(d_valo_iso).date()).days))
    date_flux = cols[i_rev]
    spread_dec = float(tab.get("spread_decimal_reference") or raw.get("spread_decimal_valo") or 0.0)

    fv_display = float(flux[i_rev]) + float(cap[i_rev])
    # « Flux restant » dans la grille = coupon (+ amort) en numérateur PV, **sans** le capital in fine ;
    # le FV du pricing REV = flux ligne + capital (identique à flux_restant + capital si flux aligné SQL).
    fv_from_restant_cap = (
        float(flux_rest[i_rev]) + float(cap[i_rev]) if i_rev < len(flux_rest) else fv_display
    )

    r_courbe_j = ts_engine(float(max(1, jours)))
    tz3 = round(r_courbe_j * 100.0, 3)
    pr_pct = float(_prime_pct_excel_rev_aa(spread_dec))
    ta_pct_engine = round(tz3 + pr_pct, 5)
    r_disc_engine = ta_pct_engine / 100.0

    t_expo_dbg = float(dbg.get("duree_act360") or (jours / 360.0))

    prix_rev_moteur, t_engine, den_engine = _prix_rev_linear(fv_display, r_disc_engine, jours)
    p4, sum_act, clean2 = _chain_prix_clean(prix_rev_moteur)

    prix_somme_tab = tab.get("prix_somme_flux_actualises")
    prix_ui = ui.get("Prix arrondi")

    manar = args.manar
    if manar is None:
        try:
            rows_m, _ = api._lire_prix_manarr_table(ROOT, iso)
            for r in rows_m:
                if _norm_code(r.get("titre")) == code:
                    manar = float(r.get("valo"))
                    break
        except Exception:
            manar = None

    def _ecart(px: float | None) -> str:
        if manar is None or px is None or not math.isfinite(float(px)):
            return "N/A"
        return f"{float(px) - float(manar):+.6f}"

    print("=" * 88)
    print(f"AUTOPSIE REV / AA / IN FINE  —  CODE {code}  —  date fichier/API : {iso}")
    print(f"Date valorisation utilisée par l’échéancier : {d_valo_iso}")
    print(f"Branche grille : REV={tab.get('pricing_rev_bond')} | courbe ZC active={tab.get('courbe_zc_active')} | "
          f"METHODE_VALO={tab.get('methode_valo')} | PERIOD_REMBO={tab.get('periodicite_rembou')}")
    print("=" * 88)

    print("\n--- 1) Entrées exactes (moteur + colonnes tableau) ---\n")
    print(f"  date_valo (ISO)     : {d_valo_iso}")
    print(f"  date prochain flux  : {date_flux}")
    print(f"  jours (calendaire)  : {jours}  (ecart tombe - valo, .days)")
    print(f"  durée ACT/360       : t = {jours}/360 = {jours/360.0:.17f}")
    print(f"  durée ligne tableau « durée » (affichage) : {duree_disp[i_rev] if i_rev < len(duree_disp) else 'N/A'}")
    print(f"  spread décimal (réf + valo) : {spread_dec:.10f}")
    print(f"  taux BAM brut (Formule B, j={max(1,jours)}, ndigits=None) : {r_courbe_j:.17f} ({r_courbe_j*100:.13f} %)")
    print(f"  Après arrondi % AA moteur REV+AA (3 déc. % sur le secondaire) : {tz3:.5f} %")
    print(f"  Prime % (_prime_pct_excel_rev_aa) : {pr_pct:.5f} %")
    print(f"  Taux d’actualisation % (round(tz+prime,5)) : {ta_pct_engine:.5f} %")
    print(f"  Taux d’actualisation décimal utilisé au dénominateur : {r_disc_engine:.17f}")
    print(f"  Colonne tableau « Taux AA » (i_rev) : {taux_aa[i_rev] if i_rev < len(taux_aa) else 'N/A'}")
    print(f"  Colonne tableau « Prime » (i_rev)   : {prime[i_rev] if i_rev < len(prime) else 'N/A'}")
    print(f"  Colonne tableau « Taux d’actualisation » % : {taux_actu[i_rev] if i_rev < len(taux_actu) else 'N/A'}")

    print("\n--- 2) Décomposition du FV ---\n")
    print(f"  Capital restant fin période (affiché) : {cap[i_rev]:.4f}")
    print(f"  Ligne « Intérêts » (coupon affiché, souvent arrondi 2 déc.) : {intr[i_rev]:.4f}")
    print(f"  Flux affiché (2 déc. dans la ligne Flux) : {flux[i_rev]:.4f}")
    print(f"  FV pricing moteur = flux[i_rev] + capital[i_rev] = {fv_display:.10f}")
    print(
        f"  « Flux restant » (coupon côté numérateur ; in fine sans capital) : "
        f"{flux_rest[i_rev] if i_rev < len(flux_rest) else float('nan'):.10f}"
    )
    print(f"  FV via (flux restant + capital) : {fv_from_restant_cap:.10f}")
    if abs(fv_from_restant_cap - fv_display) > 1e-9:
        print(f"  Δ vs FV pricing (flux+cap) : {fv_from_restant_cap - fv_display:+.10f}")
    else:
        print("  Δ : nul — coupon SQL / affiché aligne flux et flux restant + capital.")
    print(f"  Dénominateur moteur : 1 + r × t = {den_engine:.17f}  (r={r_disc_engine:.17f}, t={t_engine:.17f})")

    print("\n--- 3) Décomposition du prix ---\n")
    print("  Formule (REV + AA + in fine, non ZC dans ce profil) :")
    print("    prix_5dec = ARRONDI( (FV) / (1 + taux_actu_dec × (jours/360)) ; 5 )")
    print(f"  Prix avant arrondi 5 déc. (non arrondi intermédiaire) : {fv_display / den_engine:.15f}")
    print(f"  Prix après ARRONDI(..., 5) : {prix_rev_moteur:.10f}")
    print(f"  Flux actualisé colonne (ARRONDI prix ; 4) : {flux_act[i_rev] if i_rev < len(flux_act) else 'N/A'}")
    print(f"  Somme des flux actualisés (tableau) : {prix_somme_tab}")
    print(f"  Prix clean arrondi 2 déc. (cible YTM / Manar) : {clean2:.2f}")
    print(f"  Prix arrondi UI (moteur après pipeline) : {prix_ui}")
    p4_engine = round(float(prix_rev_moteur) + 1e-12, 4)
    print(f"  Deltas arrondis : 5→4 déc. : {p4_engine - prix_rev_moteur:+.10f} | "
          f"4→2 déc. : {clean2 - p4_engine:+.10f}")

    print("\n--- 4) Scénarios comparatifs (recalcul hors moteur, mêmes entrées sauf mention) ---\n")
    if manar is not None and math.isfinite(manar):
        print(f"  Référence Manar : {manar:.2f}\n")
    else:
        print("  Référence Manar : (non lue — passer --manar ou vérifier prix manarrr / prix mar)\n")

    scenarios: list[tuple[str, float, float, int]] = []

    def add(name: str, fv: float, rdec: float, j: int) -> None:
        p5, _, _ = _prix_rev_linear(fv, rdec, j)
        _, _, c2 = _chain_prix_clean(p5)
        scenarios.append((name, c2, p5, j))

    # A) flux arrondi affiché, chaîne taux moteur
    add("A — FV = flux affiché + cap ; taux = chaîne moteur (tz 3 dec + prime Excel)", fv_display, r_disc_engine, jours)
    # B) même FV si coupon affiché = SQL (sinon il faudrait « coupon_brut » non arrondi depuis l’échéancier).
    add("B — FV = flux restant + capital (équivalent moteur si pas d’écart SQL)", fv_from_restant_cap, r_disc_engine, jours)
    # C) taux non arrondi (%): r = r_courbe_j + spread_dec sans passer par tz3/prime arrondis
    add("C — FV affiché ; taux = (r_BAM brut + spread) sans arrondi intermédiaire en %", fv_display, r_courbe_j + spread_dec, jours)
    # D : seconde convention d’arrondi « Taux AA » (6 déc. % sur secondaire), prime 3 déc., ta 5 déc.
    tz6 = round(r_courbe_j * 100.0, 6)
    ta6 = round(tz6 + round(spread_dec * 100.0, 3), 5)  # prime still spreadsheet?
    r_d = ta6 / 100.0
    p5d, _, _ = _prix_rev_linear(fv_display, r_d, jours)
    _, _, c2d = _chain_prix_clean(p5d)
    scenarios.append(("D — Taux AA sur secondaire arrondi 6 déc. (%) + prime 3 déc.", c2d, p5d, jours))
    # E duration: t rounded to 10 dec like duree_ans display
    t_rd10 = round(jours / 360.0, 10)
    den_e = 1.0 + r_disc_engine * t_rd10
    p5e = round(fv_display / den_e + 1e-12, 5)
    _, _, c2e = _chain_prix_clean(p5e)
    scenarios.append(("E — Durée t = ARRONDI(j/360 ; 10) au lieu de float exact", c2e, p5e, jours))
    # F ACT/365 linear
    t365 = max(0, jours) / 365.0
    den_f = 1.0 + r_disc_engine * t365
    p5f = round(fv_display / den_f + 1e-12, 5) if den_f > 0 else float("nan")
    _, _, c2f = _chain_prix_clean(p5f)
    scenarios.append(("F — Linéaire avec jours/365 au lieu de /360", c2f, p5f, jours))

    print(f"{'Scénario':<62} {'Prix clean':>12} {'Écart vs Manar':>16}")
    print("-" * 92)
    for name, c2, p5, _j in scenarios:
        print(f"{name:<62} {c2:12.2f} {_ecart(c2):>16}")

    print("\n--- Synthèse : origine probable des ~0,93 pt ---\n")
    p_a = next((c2 for name, c2, _, _ in scenarios if name.startswith("A —")), None)
    p_c = next((c2 for name, c2, _, _ in scenarios if name.startswith("C —")), None)
    if (
        manar is not None
        and math.isfinite(manar)
        and p_a is not None
        and p_c is not None
        and math.isfinite(p_a)
        and math.isfinite(p_c)
    ):
        d_taux = round(float(p_a) - float(p_c), 4)
        d_man_c = round(float(p_c) - float(manar), 4)
        print(
            f"  Décomposition indicielle à date fixe : écart total moteur (A) − Manar ≈ "
            f"{round(float(p_a) - float(manar), 4):.4f} ≈ "
            f"({d_taux:.4f} dû à la chaîne d’arrondis % moteur vs taux « brut + spread ») "
            f"+ ({d_man_c:.4f} écart résiduel Manar vs scénario C).\n"
        )
    print(
        "  Pour ce profil, le moteur fixe le taux AA sur la **première tombée** (jours jusqu’à la prochaine "
        "échéance), puis applique **ARRONDI(secondaire % ; 3)** + prime (_prime_pct_excel_rev_aa_), "
        "puis **ARRONDI(tz+prime ; 5)** en pourcentage.\n"
        "  Le prix est **linéaire ACT/360** : FV/(1+r×j/360), FV = flux affiché + capital, flux SQL souvent à 2 déc.\n"
        "  Comparez les scénarios B (FV pleine précision) et C (taux sans cascade d’arrondis %) à A : "
        "celui dont l’écart vs Manar se rapproche le plus indique si Manar est plus « continu » en taux ou en flux."
    )
    print("=" * 88)


if __name__ == "__main__":
    main()
