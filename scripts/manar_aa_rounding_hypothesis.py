"""
Hypothèses d'arrondi Manar vs moteur (REV + AA, Formule B, prix linéaire ACT/360).

Reprend le pipeline marché (piliers MAR_JJ → grilles BAM → même lambda ``taux_secondaire`` que la valorisation),
puis **rejoue** uniquement les étapes d'arrondi du **taux courbe en %** avant :
  ``ta_pct = ARRONDI(tz_pct + prime_pct ; 5)`` et ``prix = ARRONDI(FV / (1 + ta_dec * j/360) ; 5)``.

Ne modifie pas le moteur ; le code titre est un argument (pas de hardcode).

Correspondance avec la grille demandée :
  - **A** → ``A_moteur_round3`` ; **B** → ``B_round4_then_round3`` ; **C** → ``C_ceil_millieme_pct`` ;
  - **D** → ``D_excel_ROUND_3dec`` ; **E** → ``E_arrondi_piliers_6dec_avant_interpolation`` ;
  - **F** → ``F_arrondi_apres_interpolation_ndigits6`` ; **G–I** variantes (floor, trunc, arrondi déc. 5).

Usage ::
  python scripts/manar_aa_rounding_hypothesis.py 201868 \\
    --pair 2026-03-26:101263.54 --pair 2026-03-06:101177.29 --pair 2026-01-02:100814.26

Options ::
  --trace   : détail des étapes (taux brut, après ndigits=6, arrondi %, prime, prix moteur grille).
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api
from obligation_amort_schedule import _prime_pct_excel_rev_aa
from pricing.debug_valorisation_trace import diagnostic_interpolation_formule_b
from pricing.curves.zc_interpolation_excel import taux_secondaire_interpole_formule_b
from valuation_zc_obligations import charger_courbe_zc_depuis_fichier
from valuation_zc_obligations import valoriser_dataframe_base_titre


def _norm_code(v: object) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _parse_pairs(pairs: list[str]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for p in pairs or []:
        if ":" not in p:
            print(f"Ignoré (--pair sans ':'): {p}", file=sys.stderr)
            continue
        d, _, v = p.partition(":")
        d = d.strip()[:10]
        try:
            out.append((d, float(v.replace(",", ".").strip())))
        except ValueError:
            print(f"Ignoré (prix invalide): {p}", file=sys.stderr)
    return out


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


def _prix_rev_lineaire(fv: float, taux_dec: float, jours_pv: int) -> float:
    """Aligné sur ``prix_rev_lineaire_act360`` (ARRONDI prix ; 5 déc.)."""
    t = max(0, int(jours_pv)) / 360.0
    den = 1.0 + float(taux_dec) * t
    if den <= 0.0 or not math.isfinite(den):
        return float("nan")
    return round(float(fv) / den + 1e-12, 5)


def _excel_round_half_up_pct(pct: float, n_dec: int) -> float:
    q = "0." + "0" * (n_dec - 1) + "1" if n_dec > 0 else "1"
    return float(Decimal(str(pct)).quantize(Decimal(q), rounding=ROUND_HALF_UP))


def _curves_round_values(cc: dict[float, float], nd: int) -> dict[float, float]:
    return {float(k): round(float(v) + 1e-15, nd) for k, v in cc.items()}


@dataclass
class RateContext:
    date_iso: str
    j_lookup: float
    j_pv: int
    fv: float
    spread_dec: float
    pr_pct: float
    bam_cc: dict[float, float]
    bam_cl: dict[float, float]
    r_brut_dec: float
    r_after_ndigits6_dec: float
    raw_pct_full: float
    diag_fb: dict[str, Any]
    prix_moteur_grille: float | None
    taux_actu_grille_pct: float | None
    taux_aa_grille_pct: float | None


def _build_context_for_date(
    *,
    code: str,
    iso: str,
    xlsx: Path,
    courbe_fallback: Any,
) -> RateContext | None:
    from obligation_amort_schedule import construire_tables_amortissement_pour_valorisation

    try:
        pillars = api._extraire_piliers_depuis_histo(ROOT, iso, "MAR_JJ")
    except Exception as e:
        print(f"{iso}: impossible de charger les piliers (SQL / histo) : {e}", file=sys.stderr)
        return None

    req_curve = api.CurveRequest(
        short=[api.PillarShort(**p) for p in pillars["short"]],
        long=[api.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )
    bam_cc, bam_cl = api._courbes_bam_depuis_requete(req_curve)

    curve_tracee = api._make_curve(req_curve)
    sched_zc = api._schedule_table_records(curve_tracee, root=ROOT, date_courbe=iso)

    def _ts_amort(j: float) -> float:
        return float(taux_secondaire_interpole_formule_b(float(j), bam_cc, bam_cl, ndigits=None))

    def _fn_j(j: float) -> float:
        return api._interp_taux_zc_actuariel_depuis_schedule_jours(j, sched_zc)

    def _fn_a(a: float) -> float:
        return api._interp_taux_zc_depuis_schedule_annuel(a, sched_zc)

    df_in = api._charger_base_titre_oblg_cache(xlsx, [code])
    if df_in.empty:
        print(f"{iso}: référentiel vide.", file=sys.stderr)
        return None

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
        print(f"{iso}: code absent du résultat valorisation.", file=sys.stderr)
        return None

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
        print(f"{iso}: pas de tableau d'amortissement.", file=sys.stderr)
        return None

    d_valo = str(tab.get("date_valorisation_utilisee_iso") or iso)[:10]
    cols_iso = [str(c) for c in (tab.get("columns") or [])]
    i_f = _idx_future(cols_iso, d_valo)
    if i_f < 0:
        print(f"{iso}: aucune tombée future.", file=sys.stderr)
        return None

    j_pv = int((date.fromisoformat(cols_iso[i_f][:10]) - date.fromisoformat(d_valo)).days)
    j_lookup = float(max(1, int(tab.get("maturite_residuelle_jours") or j_pv)))

    flux = [float(x or 0) for x in _row_vals(tab, "Flux")]
    cap = [float(x or 0) for x in _row_vals(tab, "Capital restant")]
    fv = float(flux[i_f]) + float(cap[i_f])
    if fv <= 0:
        fr = _row_vals(tab, "Flux restant")
        if i_f < len(fr):
            fv = float(fr[i_f]) + float(cap[i_f])

    spread_dec = float(tab.get("spread_decimal_reference") or 0.0)
    pr_pct = float(_prime_pct_excel_rev_aa(spread_dec))

    r_brut_dec = float(taux_secondaire_interpole_formule_b(float(j_lookup), bam_cc, bam_cl, ndigits=None))
    r_nd6 = float(taux_secondaire_interpole_formule_b(float(j_lookup), bam_cc, bam_cl, ndigits=6))

    dbg = tab.get("debug_rev") or {}
    j_dbg = int(dbg.get("jours_calculs") or j_pv)

    prix_tab = tab.get("prix_somme_flux_actualises")
    ta_vals = _row_vals(tab, "Taux d'actualisation")
    aa_vals = _row_vals(tab, "Taux AA")
    ta_g = float(ta_vals[i_f]) if i_f < len(ta_vals) and ta_vals[i_f] is not None else None
    aa_g = float(aa_vals[i_f]) if i_f < len(aa_vals) and aa_vals[i_f] is not None else None

    return RateContext(
        date_iso=iso,
        j_lookup=j_lookup,
        j_pv=int(j_dbg),
        fv=fv,
        spread_dec=spread_dec,
        pr_pct=pr_pct,
        bam_cc=bam_cc,
        bam_cl=bam_cl,
        r_brut_dec=r_brut_dec,
        r_after_ndigits6_dec=r_nd6,
        raw_pct_full=r_brut_dec * 100.0,
        diag_fb=diagnostic_interpolation_formule_b(float(j_lookup), bam_cc, bam_cl),
        prix_moteur_grille=float(prix_tab) if prix_tab is not None else None,
        taux_actu_grille_pct=ta_g,
        taux_aa_grille_pct=aa_g,
    )


def _moteur_tz_pct(ctx: RateContext) -> float:
    return round(ctx.r_brut_dec * 100.0, 3)


ConventionFn = Callable[[RateContext], tuple[float, str]]


def _conventions() -> list[tuple[str, ConventionFn]]:
    def A_moteur(c: RateContext) -> tuple[float, str]:
        return round(c.raw_pct_full, 3), "Python round(% courbe, 3) — même logique que `obligation_amort_schedule` tz_rev_pct hors 9487/5166"

    def B_round4_then_3(c: RateContext) -> tuple[float, str]:
        return round(round(c.raw_pct_full, 4), 3), "round(round(pct, 4), 3)"

    def C_ceil_milliemes_pct(c: RateContext) -> tuple[float, str]:
        x = float(c.raw_pct_full)
        return math.ceil(x * 1000.0 - 1e-12) / 1000.0, "ceil au millième (sur la valeur %)"

    def D_excel_round3(c: RateContext) -> tuple[float, str]:
        return _excel_round_half_up_pct(c.raw_pct_full, 3), "Decimal quantize 0.001 HALF_UP (ARRONDI Excel 3 déc.)"

    def E_piliers_arrondis_6_dec(c: RateContext) -> tuple[float, str]:
        cc6 = _curves_round_values(c.bam_cc, 6)
        cl6 = _curves_round_values(c.bam_cl, 6)
        r = float(taux_secondaire_interpole_formule_b(float(c.j_lookup), cc6, cl6, ndigits=None))
        return round(r * 100.0, 3), "piliers CT/LT arrondis 6 déc. puis Formule B (ndigits=None), puis round(% ,3)"

    def F_interpolation_ndigits_6(c: RateContext) -> tuple[float, str]:
        r = float(taux_secondaire_interpole_formule_b(float(c.j_lookup), c.bam_cc, c.bam_cl, ndigits=6))
        return round(r * 100.0, 3), "Formule B avec ndigits=6 sur le taux **décimal**, puis round(% ,3)"

    def G_floor_milliemes_pct(c: RateContext) -> tuple[float, str]:
        x = float(c.raw_pct_full)
        return math.floor(x * 1000.0 + 1e-12) / 1000.0, "floor au millième (%)"

    def H_trunc_milliemes_pct(c: RateContext) -> tuple[float, str]:
        x = float(c.raw_pct_full)
        return math.trunc(x * 1000.0) / 1000.0, "truncate vers 0 au millième (%)"

    def I_round_pct_avant_plus_prime(c: RateContext) -> tuple[float, str]:
        # Variante : arrondir le décimal à 5 déc. avant passage en %
        r5 = round(c.r_brut_dec + 1e-15, 5)
        return round(r5 * 100.0, 3), "round(taux_décimal_brut, 5) puis round(% ,3)"

    return [
        ("A_moteur_round3", A_moteur),
        ("B_round4_then_round3", B_round4_then_3),
        ("C_ceil_millieme_pct", C_ceil_milliemes_pct),
        ("D_excel_ROUND_3dec", D_excel_round3),
        ("E_arrondi_piliers_6dec_avant_interpolation", E_piliers_arrondis_6_dec),
        ("F_arrondi_apres_interpolation_ndigits6", F_interpolation_ndigits_6),
        ("G_floor_millieme_pct", G_floor_milliemes_pct),
        ("H_trunc_millieme_pct", H_trunc_milliemes_pct),
        ("I_round_dec_brut_5_avant_pct", I_round_pct_avant_plus_prime),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="Grille d'hypothèses d'arrondi taux AA / prix linéaire vs Manar.")
    ap.add_argument("code", help="Code Maroclear")
    ap.add_argument(
        "--pair",
        action="append",
        required=True,
        help="date:prix_manar (répéter)",
    )
    ap.add_argument("--trace", action="store_true", help="Détail des étapes intermédiaires par date.")
    args = ap.parse_args()
    code = _norm_code(args.code)
    pairs = _parse_pairs(args.pair)
    if not pairs:
        print("Aucune paire valide.", file=sys.stderr)
        sys.exit(1)

    zc_path = ROOT / "pricing/curves/courbe_zc.py"
    courbe_fallback = charger_courbe_zc_depuis_fichier(zc_path)
    xlsx = api.resoudre_fichier_base_titre_oblig(ROOT, None)

    print("# Grille hypothèses arrondi Manar (AA Formule B → % → prime → prix linéaire)\n")
    print(f"- **Code** : `{code}`")
    print(
        "- **Chaîne moteur (REV AA non-ZC)** : `r_courbe_dec` = `taux_secondaire_interpole_formule_b(K, CT, LT, ndigits=None)` "
        "avec **K** = `maturite_residuelle_jours` du tableau ; puis `tz_pct = round(r*100, 3)` ; "
        "`prime_pct = _prime_pct_excel_rev_aa(spread)` ; `ta_pct = round(tz_pct + prime_pct, 5)` ; "
        "`prix = ARRONDI(FV/(1 + ta_dec * j_pv/360); 5)` avec **j_pv** = `debug_rev.jours_calculs`.\n"
    )

    convs = _conventions()
    print(
        "| date | taux_brut_dec | convention | tz_pct | ta_pct | taux_final_pct | prix_final | ecart_vs_manar | note |"
    )
    print("|" + "|".join(["---"] * 9) + "|")

    for iso, prix_manar in sorted(pairs, key=lambda t: t[0]):
        ctx = _build_context_for_date(code=code, iso=iso, xlsx=xlsx, courbe_fallback=courbe_fallback)
        if ctx is None:
            continue

        tz_engine = _moteur_tz_pct(ctx)
        ta_engine = round(tz_engine + ctx.pr_pct, 5)
        px_engine_conv = _prix_rev_lineaire(ctx.fv, ta_engine / 100.0, ctx.j_pv)

        if args.trace:
            d = ctx.diag_fb
            print(f"\n## Trace `{iso}`\n")
            print(f"- **K lookup (mat. résid.)** : {ctx.j_lookup:g} j  · **j_pv (debug / pricing)** : {ctx.j_pv} j")
            if int(ctx.j_lookup) != int(ctx.j_pv):
                print("  - *(Attention : K ≠ j_pv — cas rare ; prix utilise j_pv, courbe K.)*")
            print(f"- **Zone Formule B** : {d.get('zone_CT_LT')} ; piliers encadrants (j) : {d.get('pilier_avant_j'):g} → {d.get('pilier_apres_j'):g}")
            print(f"- **taux_interpole Formule B (ndigits=None)** : `{ctx.r_brut_dec:.12f}` déc. (= `{ctx.raw_pct_full:.12f}` % brut)")
            print(f"- **après ndigits=6 (post-interpolation)** : `{ctx.r_after_ndigits6_dec:.12f}` déc.")
            print(f"- **prime_pct (spread → Excel REV)** : `{ctx.pr_pct:.6f}` % (spread_dec={ctx.spread_dec})")
            print(f"- **Grille moteur** : prix={ctx.prix_moteur_grille} ; Taux AA col.={ctx.taux_aa_grille_pct} % ; Taux actu={ctx.taux_actu_grille_pct} %")
            print(f"- **tz_pct** (après arrondi % courbe, étape moteur) : `{tz_engine:.8f}` %")
            print(f"- **Avant ta_pct** : tz + prime = `{tz_engine + ctx.pr_pct:.12f}` %")
            print(f"- **ta_pct** (ARRONDI 5 déc. sur %) : `{ta_engine:.8f}` %")
            print(f"- **prix recalculé (j_pv)** : `{px_engine_conv:.8f}`")
            r_man_imp_pct = ((ctx.fv / float(prix_manar) - 1.0) / (max(1, ctx.j_pv) / 360.0)) * 100.0
            print(f"- **Taux implicite Manar (lin. j/360, même j_pv)** : `{r_man_imp_pct:.8f}` %")
            print(
                "- **Décimales** : interpolation en flottant IEEE ; affichage **12 déc.** pour le diagnostic, "
                "pas de « nombre exact » au-delà de la précision machine.\n"
            )

        best = None  # (abs_ec, name, ta, px)
        for name, fn in convs:
            tz, note = fn(ctx)
            ta = round(tz + ctx.pr_pct, 5)
            px = _prix_rev_lineaire(ctx.fv, ta / 100.0, ctx.j_pv)
            ec = round(float(px) - float(prix_manar), 4)
            cand = (abs(ec), name, ta, px, ec)
            if best is None or cand[0] < best[0]:
                best = cand
            note_safe = note.replace("|", "/")[:72]
            print(
                f"| {iso} | {ctx.r_brut_dec:.12f} | {name} | {tz:.5f} | {ta:.5f} | {ta:.5f} | {px:.5f} | {ec:+.4f} | {note_safe} |"
            )
        if best is not None:
            _, bn, bta, bpx, bec = best
            print(
                f"| {iso} | {ctx.r_brut_dec:.12f} | **min_écart** | — | {bta:.5f} | {bta:.5f} | {bpx:.5f} | {bec:+.4f} | meilleur: `{bn}` |"
            )

        px_disp = float(ctx.prix_moteur_grille) if ctx.prix_moteur_grille is not None else float(px_engine_conv)
        ec_eng = round(px_disp - float(prix_manar), 4)
        print(
            f"| {iso} | {ctx.r_brut_dec:.12f} | **SYN_moteur** | {tz_engine:.5f} | {ta_engine:.5f} | {ta_engine:.5f} | "
            f"{px_disp:.5f} | {ec_eng:+.4f} | recalcul moteur vs prix tableau |"
        )

    print("\n## Lecture\n")
    print(
        "Chercher une ligne avec **prix_final** qui colle au prix Manar et **taux_final_pct** = "
        "taux implicite Manar affiché (ex. 2,334 %). `C_ceil_millieme_pct` ou `B_round4_then_round3` "
        "sont des candidats typiques pour un écart de **+0,001 %** sur le % courbe.\n"
    )


if __name__ == "__main__":
    main()
