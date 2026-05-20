"""
Diagnostic multi-dates pour obligations FIX/ZC (ex. BDT) : durée, taux ZC, taux implicite Manar.

Reprend le pipeline marché : histo MAR_JJ → courbe tracée → échéancier ZC → tableau d’amortissement.
Ne modifie pas le moteur ni la logique de pricing.

Usage (racine projet) ::
  python scripts/diag_zc_manar_multidate.py 201868 \\
    --pair 2026-03-26:101263.54 --pair 2026-03-06:101177.29 --pair 2026-01-02:100814.26

Sortie : tableau comparatif markdown / texte sur stdout.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api
from obligation_amort_schedule import construire_tables_amortissement_pour_valorisation
from pricing.debug_valorisation_trace import diagnostic_interpolation_formule_b
from pricing.curves.zc_interpolation_excel import taux_secondaire_interpole_formule_b
from valuation_zc_obligations import charger_courbe_zc_depuis_fichier
from valuation_zc_obligations import detecter_colonnes_base_titre
from valuation_zc_obligations import valoriser_dataframe_base_titre


def _norm_code(v: object) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _row_vals(tab: dict[str, Any], label: str) -> list[Any]:
    for r in tab.get("rows") or []:
        if str(r.get("label")) == label:
            return list(r.get("values") or [])
    return []


def _idx_future(cols_iso: list[str], d_valo_iso: str) -> int:
    dv = date.fromisoformat(str(d_valo_iso)[:10])
    for i, c in enumerate(cols_iso):
        if date.fromisoformat(str(c)[:10]) > dv:
            return i
    return -1


def _rdisc_zc_moteur(rz: float, spread: float) -> float:
    return float(
        Decimal(str(float(rz) + float(spread))).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    )


def _implicit_r(fv: float, px: float, du: float) -> float:
    if px <= 0 or du <= 0 or fv <= 0:
        return float("nan")
    return (float(fv) / float(px)) ** (1.0 / float(du)) - 1.0


def _implicit_r_linear_act360_j(fv: float, px: float, jours: int) -> float:
    """Taux implicite si prix = FV / (1 + r * j/360) (REV AA non-ZC, Excel)."""
    if px <= 0 or fv <= 0 or int(jours) <= 0:
        return float("nan")
    t = float(int(jours)) / 360.0
    return (float(fv) / float(px) - 1.0) / t


def _engine_uses_rev_linear_act360(tab: dict[str, Any]) -> bool:
    dbg = tab.get("debug_rev")
    if not dbg or not isinstance(dbg, dict):
        return False
    return "jours/360" in str(dbg.get("formule", ""))


def _fnum(x: float, nd: int = 6) -> str:
    if x is None or (isinstance(x, float) and (not math.isfinite(x))):
        return "n/a"
    return f"{float(x):.{nd}f}"


def _bp(a: float, b: float) -> float:
    return (float(a) - float(b)) * 10000.0


def _zc_schedule_brackets(jours: float, sched: list[dict]) -> str:
    if not sched or len(sched) < 2:
        return "n/a"
    xs = sorted(float(r["Maturity_days"]) for r in sched)
    j = float(jours)
    if j <= xs[0]:
        lo, hi = xs[0], xs[1]
    elif j >= xs[-1]:
        lo, hi = xs[-2], xs[-1]
    else:
        lo = hi = xs[0]
        for i in range(len(xs) - 1):
            if xs[i] <= j <= xs[i + 1]:
                lo, hi = xs[i], xs[i + 1]
                break
    return f"{lo:.0f}–{hi:.0f} j"


def _taux_courbe_label(tab: dict[str, Any]) -> str:
    for lab in ("Taux ZC", "Taux AA"):
        if _row_vals(tab, lab):
            return lab
    return "Taux (?)"


def _pct_row_to_dec(v: Any) -> float:
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return float("nan")


def _pilier_label(pillars: dict) -> str:
    s = [f"{float(p['maturity_days']):.0f}" for p in pillars.get("short") or []]
    l_ = [f"{float(p['maturity_days']):.0f}" for p in pillars.get("long") or []]
    return f"CT[{','.join(s)}] LT[{','.join(l_)}]"


def main() -> None:
    ap = argparse.ArgumentParser(description="Comparatif Manar vs moteur ZC multi-dates.")
    ap.add_argument("code", help="Code Maroclear")
    ap.add_argument(
        "--pair",
        action="append",
        required=True,
        help="Date ISO et prix Manar, ex. 2026-03-26:101263.54 (répéter --pair)",
    )
    args = ap.parse_args()
    code = _norm_code(args.code)

    pairs: list[tuple[str, float]] = []
    for p in args.pair:
        if ":" not in p:
            print(f"Ignoré (--pair sans ':'): {p}", file=sys.stderr)
            continue
        d, _, v = p.partition(":")
        d = d.strip()[:10]
        try:
            pm = float(v.replace(",", ".").strip())
        except ValueError:
            print(f"Ignoré (prix invalide): {p}", file=sys.stderr)
            continue
        pairs.append((d, pm))
    if not pairs:
        print("Aucune paire date:prix valide.", file=sys.stderr)
        sys.exit(1)

    zc_path = ROOT / "pricing/curves/courbe_zc.py"
    courbe_fallback = charger_courbe_zc_depuis_fichier(zc_path)
    xlsx = api.resoudre_fichier_base_titre_oblig(ROOT, None)

    rows_out: list[dict[str, Any]] = []

    for iso, prix_manar in sorted(pairs, key=lambda t: t[0]):
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

        def _ts_amort(j: float) -> float:
            return float(
                taux_secondaire_interpole_formule_b(float(j), bam_cc, bam_cl, ndigits=None),
            )

        curve_tracee = api._make_curve(req_curve)
        sched_zc = api._schedule_table_records(curve_tracee, root=ROOT, date_courbe=iso)

        def _fn_j(j: float) -> float:
            return api._interp_taux_zc_actuariel_depuis_schedule_jours(j, sched_zc)

        def _fn_a(a: float) -> float:
            return api._interp_taux_zc_depuis_schedule_annuel(a, sched_zc)

        df_in = api._charger_base_titre_oblg_cache(xlsx, [code])
        if df_in.empty:
            rows_out.append({"date": iso, "erreur": "référentiel vide"})
            continue

        df_out, det = valoriser_dataframe_base_titre(
            df_in,
            courbe_fallback,
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
            dct = row.to_dict()
            if _norm_code(dct.get(col_code) if col_code else dct.get("CODE")) == code:
                raw = dct
                break
        if not raw:
            rows_out.append({"date": iso, "erreur": "code absent du résultat valorisation"})
            continue

        ui = api._row_to_marche_ui(raw, iso)
        amort_tables = construire_tables_amortissement_pour_valorisation(
            xlsx,
            [raw],
            [ui],
            valuation_date=iso,
            taux_secondaire_a_j=_ts_amort,
            taux_zc_schedule_j=_fn_j,
            taux_zc_schedule_a=_fn_a,
            df_work=df_in,
            col_code_fichier=det.get("col_code") or col_code,
            det_cols=det,
            codes_filter=[code],
        )
        tab = next((t for t in amort_tables if _norm_code(t.get("code")) == code), None)
        if not tab:
            rows_out.append({"date": iso, "erreur": "pas de tableau amortissement"})
            continue

        d_valo = str(tab.get("date_valorisation_utilisee_iso") or iso)[:10]
        cols_iso = [str(c) for c in (tab.get("columns") or [])]
        i_f = _idx_future(cols_iso, d_valo)
        if i_f < 0:
            rows_out.append({"date": iso, "erreur": "aucune tombée future"})
            continue

        jours = (date.fromisoformat(cols_iso[i_f][:10]) - date.fromisoformat(d_valo)).days
        du_vals = _row_vals(tab, "durée")
        du = float(du_vals[i_f]) if i_f < len(du_vals) and du_vals[i_f] is not None else float(jours) / 365.0

        courbe_lbl = _taux_courbe_label(tab)
        ta_vals = _row_vals(tab, "Taux d'actualisation")
        r_table_actu = (
            _pct_row_to_dec(ta_vals[i_f]) if i_f < len(ta_vals) and ta_vals[i_f] is not None else float("nan")
        )

        flux = [float(x or 0) for x in _row_vals(tab, "Flux")]
        cap = [float(x or 0) for x in _row_vals(tab, "Capital restant")]
        fv = float(flux[i_f]) + float(cap[i_f])
        if fv <= 0:
            fr = _row_vals(tab, "Flux restant")
            if i_f < len(fr):
                fv = float(fr[i_f]) + float(cap[i_f])

        prix_moteur = float(tab.get("prix_somme_flux_actualises") or 0.0)
        spread_dec = float(tab.get("spread_decimal_reference") or 0.0)

        zc_j = float(_fn_j(float(jours)))
        zc_a_du = float(_fn_a(float(du)))
        sec_fb = float(taux_secondaire_interpole_formule_b(float(jours), bam_cc, bam_cl, ndigits=None))
        sec_fb_6 = float(taux_secondaire_interpole_formule_b(float(jours), bam_cc, bam_cl, ndigits=6))

        r_moteur = _rdisc_zc_moteur(zc_j, spread_dec)
        rz_pct_r3 = round(zc_j * 100.0, 3) / 100.0
        r_from_rz3 = _rdisc_zc_moteur(rz_pct_r3, spread_dec)

        r_moteur_bs_comp = _implicit_r(fv, prix_moteur, du)
        r_manar_comp = _implicit_r(fv, prix_manar, du)
        r_moteur_bs_comp_cl = r_moteur_bs_comp if math.isfinite(r_moteur_bs_comp) else float("nan")
        r_moteur_bs_lin = _implicit_r_linear_act360_j(fv, prix_moteur, int(jours))
        r_manar_lin = _implicit_r_linear_act360_j(fv, prix_manar, int(jours))

        rev_lin = _engine_uses_rev_linear_act360(tab)
        r_moteur_eff = r_moteur_bs_lin if rev_lin else r_moteur_bs_comp_cl
        r_manar_eff = r_manar_lin if rev_lin else r_manar_comp
        formule_moteur = str((tab.get("debug_rev") or {}).get("formule", "")) if tab.get("debug_rev") else ""

        diag = diagnostic_interpolation_formule_b(float(jours), bam_cc, bam_cl)

        bp_manar_vs_table = (
            _bp(r_manar_eff, r_table_actu)
            if math.isfinite(r_manar_eff) and math.isfinite(r_table_actu)
            else float("nan")
        )
        bp_table_vs_bs = (
            _bp(r_table_actu, r_moteur_eff)
            if math.isfinite(r_table_actu) and math.isfinite(r_moteur_eff)
            else float("nan")
        )

        rows_out.append(
            {
                "date": iso,
                "jours_ech": jours,
                "du_annees": du,
                "FV": fv,
                "prix_moteur": prix_moteur,
                "prix_manar": prix_manar,
                "ecart_prix": round(prix_moteur - prix_manar, 4),
                "conv_moteur": "REV lin j/360" if rev_lin else "comp (du tableau)",
                "formule_moteur": formule_moteur,
                "taux_courbe_lbl": courbe_lbl,
                "r_table_actu": r_table_actu,
                "r_moteur_bs_comp": r_moteur_bs_comp_cl,
                "r_moteur_bs_lin": r_moteur_bs_lin,
                "r_moteur_eff": r_moteur_eff,
                "r_manar_comp": r_manar_comp,
                "r_manar_lin": r_manar_lin,
                "r_manar_eff": r_manar_eff,
                "r_moteur_bs": r_moteur_bs_comp_cl,
                "r_manar_implicite": r_manar_comp,
                "r_moteur_sched": r_moteur,
                "bp_manar_vs_table": bp_manar_vs_table,
                "bp_table_vs_bs": bp_table_vs_bs,
                "bp_manar_moins_r_moteur": _bp(r_manar_comp, r_moteur) if math.isfinite(r_manar_comp) else float("nan"),
                "zone_FormuleB": diag.get("zone_CT_LT"),
                "pilier_av_j": diag.get("pilier_avant_j"),
                "pilier_ap_j": diag.get("pilier_apres_j"),
                "taux_ZC_schedule_j": zc_j,
                "taux_ZC_schedule_annuel": zc_a_du,
                "taux_secondaire_FB_ndigits_None": sec_fb,
                "taux_secondaire_FB_ndigits_6": sec_fb_6,
                "rdisc_si_rz_pct_3dec": r_from_rz3,
                "zc_brackets_j": _zc_schedule_brackets(float(jours), sched_zc),
                "piliers_BAM_resume": _pilier_label(pillars),
                "methode_valo": tab.get("methode_valo"),
                "courbe_zc": tab.get("courbe_zc_active"),
                "spread_dec": spread_dec,
            }
        )

    print("# Comparatif diagnostic ZC / Manar (pipeline inchangé)\n")
    print(f"- **Code** : {code}")
    print(
        "- **Taux d'actualisation (grille)** : cellule **Taux d'actualisation** / 100 à la tombée future analysée.\n"
        "- **Convention moteur** : déduite de `debug_rev.formule` du tableau — pour **REV + AA sans ZC**, le moteur utilise "
        "souvent **`FV / (1 + r × j/360)`** (linéaire ACT/360), pas `(1+r)^{du}` avec `du` en années.\n"
        "- **Colonnes taux** : **r BS cohérent** et **r Man cohérent** reprennent la **même** convention que le moteur "
        "(linéaire ou composée) ; **r Man (comp)** = implicite **`FV/(1+r)^{du}`** avec `du` = ligne durée (demande analyse).\n"
        "- **Hypothèse ZC schedule** : `r_disc` = ARRONDI(`rz` + spread ; 5 déc.) — utile si `courbe_zc_active`, "
        "sinon colonnes indicatives.\n"
    )

    hdr = (
        "| Date | j | du | FV | Px mot | Px Man | Δ px | Conv moteur | Courbe | r tab | "
        "r BS cohérent | r Man cohérent | r Man (comp) | Δ bp Man−tab | Δ bp tab−BS | "
        "r ZCsched+sp | Δ bp Man_comp−ZC | Zone FB | ZC(j) | ZC(ann.) | Sec.FB | Sec.FB6 | rz%3d | ZC[j] |"
    )
    sep = "|" + "|".join(["---"] * 26) + "|"
    print(hdr)
    print(sep)
    for r in rows_out:
        if r.get("erreur"):
            print(f"| {r['date']} | | | | | | **{r['erreur']}** | | | | | | | | | | | | | | | | | | | | |")
            continue
        print(
            f"| {r['date']} | {r['jours_ech']} | {_fnum(r['du_annees'], 8)} | {_fnum(r['FV'], 2)} | "
            f"{_fnum(r['prix_moteur'], 2)} | {_fnum(r['prix_manar'], 2)} | {_fnum(r['ecart_prix'], 2)} | "
            f"{r.get('conv_moteur', '')} | {r.get('taux_courbe_lbl', '')} | {_fnum(r['r_table_actu'], 8)} | "
            f"{_fnum(r['r_moteur_eff'], 8)} | {_fnum(r['r_manar_eff'], 8)} | {_fnum(r['r_manar_comp'], 8)} | "
            f"{_fnum(r['bp_manar_vs_table'], 3)} | {_fnum(r['bp_table_vs_bs'], 3)} | "
            f"{_fnum(r['r_moteur_sched'], 8)} | {_fnum(r['bp_manar_moins_r_moteur'], 3)} | {r.get('zone_FormuleB', '')} | "
            f"{_fnum(r['taux_ZC_schedule_j'], 8)} | {_fnum(r['taux_ZC_schedule_annuel'], 8)} | "
            f"{_fnum(r['taux_secondaire_FB_ndigits_None'], 8)} | {_fnum(r['taux_secondaire_FB_ndigits_6'], 8)} | "
            f"{_fnum(r['rdisc_si_rz_pct_3dec'], 8)} | {r.get('zc_brackets_j', '')} |"
        )

    print("\n## Formule moteur (debug tableau)\n")
    print("| Date | debug_rev.formule |")
    print("|---|---|")
    for r in rows_out:
        if r.get("erreur"):
            continue
        print(f"| {r['date']} | `{r.get('formule_moteur','')}` |")

    print("\n## Détail piliers & méta\n")
    print("| Date | Piliers BAM (résumé) | METHODE_VALO | ZC actif | spread |")
    print("|---|---|---|---|---|")
    for r in rows_out:
        if r.get("erreur"):
            continue
        print(
            f"| {r['date']} | `{r.get('piliers_BAM_resume', '')}` | {r.get('methode_valo', '')} | "
            f"{r.get('courbe_zc', '')} | {r.get('spread_dec', '')} |"
        )

    print("\n## Lecture\n")
    print(
        "- **r tab** : **Taux d'actualisation** affiché (tombée) / 100.\n"
        "- **r BS cohérent** : taux implicite du **prix moteur** avec la **même** convention que `debug_rev.formule` "
        "(linéaire `j/360` ou composée `(1+r)^{du}`).\n"
        "- **r Man cohérent** : idem pour le **prix Manar** — l’écart de prix se lit surtout vs **r tab** via **Δ bp Man−tab**.\n"
        "- **r Man (comp)** : implicite `FV/(1+r)^{du}` avec `du` = ligne durée (**ne pas** comparer à `r tab` si le moteur est en linéaire REV).\n"
        "- **Δ bp tab−BS** : contrôle interne `(r_tab − r_BS_cohérent)×10 000` (~0 si arrondis cohérents).\n"
        "- **r ZCsched+sp / Δ bp Man_comp−ZC** : hypothèse courbe ZC bootstrap + spread ; pour **AA sans courbe ZC**, ce n’est pas le taux du PV affiché.\n"
    )


if __name__ == "__main__":
    main()
