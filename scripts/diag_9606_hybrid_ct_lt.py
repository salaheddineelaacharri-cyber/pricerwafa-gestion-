"""
Diagnostic CT / LT / zone hybride (Formule B vs échéancier ZC) — **hors production**.

Cas cible par défaut : CODE 9606, dates 2026-01-02 / 2026-03-06 / 2026-03-26.
Aucun ``if code == 9606`` dans le moteur : tout est paramétrable en CLI.

Usage (racine projet) ::
  python scripts/diag_9606_hybrid_ct_lt.py
  python scripts/diag_9606_hybrid_ct_lt.py --code 9606 --dates 2026-01-02 2026-03-06 2026-03-26 \\
      --manar 2026-03-06=100455.07 --out results/diag_9606_ct_lt.md

Prérequis : ``dbo.histo_courbe_taux`` + base titre (même chaîne que l’API).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api
from obligation_amort_schedule import construire_tables_amortissement_pour_valorisation
from pricing.curves.zc_interpolation_excel import (
    NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT,
    _dict_to_sorted_arrays,
    taux_secondaire_interpole_formule_b,
    vba_interpolate,
    vba_interpolate_extrapolate,
)
from valuation_zc_obligations import charger_courbe_zc_depuis_fichier, detecter_colonnes_base_titre
from valuation_zc_obligations import valoriser_dataframe_base_titre


def _norm_code(v: object) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _parse_manar_pairs(pairs: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in pairs or []:
        if "=" not in p:
            continue
        a, b = p.split("=", 1)
        iso = str(a).strip()[:10]
        out[iso] = float(str(b).replace(",", ".").strip())
    return out


def _pillar_ct_lt_lists(pillars: dict[str, Any]) -> tuple[list[float], list[float]]:
    ct = sorted(float(p["maturity_days"]) for p in pillars.get("short") or [])
    lt = sorted(float(p["maturity_days"]) for p in pillars.get("long") or [])
    return ct, lt


def _premier_lt_sql(pillars: dict[str, Any]) -> float | None:
    """Plus petit pilier LT issu du SQL (hors point synthétique Excel ajouté ensuite)."""
    xs = [float(p["maturity_days"]) for p in pillars.get("long") or []]
    return float(min(xs)) if xs else None


def _formule_b_decompose(
    k: float,
    bam_cc: dict[float, float],
    bam_cl: dict[float, float],
) -> dict[str, Any]:
    mx, tx = _dict_to_sorted_arrays(bam_cc)
    my, ty = _dict_to_sorted_arrays(bam_cl)
    g2 = float(mx[-1])
    k = float(k)
    out: dict[str, Any] = {
        "K": k,
        "dernier_CT": g2,
        "premier_LT_grille": float(my[0]) if my.size else None,
        "taux_CT_si_CT": None,
        "taux_LT_extrapole_si_hybride": None,
        "taux_secondaire_avant_ndigits": None,
        "branche": "",
    }
    if k >= 365.0:
        out["branche"] = "LT"
        r_lt = float(vba_interpolate_extrapolate(my, ty, k))
        out["taux_LT_extrapole_si_hybride"] = r_lt
        r_raw = r_lt
    elif k <= g2:
        out["branche"] = "CT"
        r_ct = float(vba_interpolate(mx, tx, k))
        out["taux_CT_si_CT"] = r_ct
        r_raw = r_ct
    else:
        out["branche"] = "hybride_CT_365"
        r_lt = float(vba_interpolate_extrapolate(my, ty, k))
        out["taux_LT_extrapole_si_hybride"] = r_lt
        r_raw = ((1.0 + r_lt) ** (k / 365.0) - 1.0) * (360.0 / k) if k > 0 else r_lt
    out["taux_secondaire_avant_ndigits"] = float(r_raw)
    nd = NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT
    out["taux_secondaire_apres_ndigits"] = float(
        taux_secondaire_interpole_formule_b(k, bam_cc, bam_cl, ndigits=nd)
    )
    return out


def _schedule_taux_mm_by_maturity(
    curve: Any,
    *,
    root: Path,
    date_courbe: str,
) -> tuple[np.ndarray, np.ndarray]:
    rows = api._schedule_table_records(curve, root=root, date_courbe=date_courbe, courbe="MAR_JJ")
    xs = np.array([float(r["Maturity_days"]) for r in rows], dtype=float)
    ys = np.array([float(r["Taux_pct"]) / 100.0 for r in rows], dtype=float)
    o = np.argsort(xs)
    return xs[o], ys[o]


def _taux_scenario_B_schedule_mm(
    k: float,
    *,
    sched_x: np.ndarray,
    sched_y: np.ndarray,
) -> float:
    """Interpolation linéaire sur la colonne **Taux** (MM) de l’échéancier ZC tracé (cf. ``_schedule_table_records``)."""
    return float(vba_interpolate(sched_x, sched_y, float(k)))


def _taux_scenario_C_ct_extrap(
    k: float,
    bam_cc: dict[float, float],
    bam_cl: dict[float, float],
) -> float:
    """Test : extrapolation **CT seule** entre dernier_CT et 365 (MM), sans conversion LT/hybride Formule B."""
    mx, tx = _dict_to_sorted_arrays(bam_cc)
    my, ty = _dict_to_sorted_arrays(bam_cl)
    g2 = float(mx[-1])
    kk = float(k)
    if kk >= 365.0:
        return float(vba_interpolate_extrapolate(my, ty, kk))
    if kk <= g2:
        return float(vba_interpolate(mx, tx, kk))
    return float(vba_interpolate_extrapolate(mx, tx, kk))


def _taux_scenario_D_fixed(
    k: float,
    bam_cc: dict[float, float],
    bam_cl: dict[float, float],
    *,
    g2: float,
    mm_at_g2: float,
    b365: float,
) -> float:
    kk = float(k)
    if kk >= 365.0:
        my, ty = _dict_to_sorted_arrays(bam_cl)
        return float(vba_interpolate_extrapolate(my, ty, kk))
    if kk <= g2:
        mx, tx = _dict_to_sorted_arrays(bam_cc)
        return float(vba_interpolate(mx, tx, kk))
    if kk >= 365.0 - 1e-9:
        return float(b365)
    t = (kk - g2) / (365.0 - g2)
    return float(mm_at_g2 + t * (float(b365) - float(mm_at_g2)))


def _apply_ndigits(r: float) -> float:
    """Même post-traitement que ``taux_secondaire_interpole_formule_b`` (voir module zc_interpolation_excel)."""
    nd = int(NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT)
    return float(round(float(r) + 1e-15, nd))


def _ts_factory(
    scenario: str,
    *,
    bam_cc: dict[float, float],
    bam_cl: dict[float, float],
    sched_x: np.ndarray,
    sched_y: np.ndarray,
    g2: float,
    mm_at_g2: float,
    b365: float,
) -> Callable[[float], float]:
    def _wrap(j: float) -> float:
        jj = float(j)
        if scenario == "A":
            r = taux_secondaire_interpole_formule_b(
                jj, bam_cc, bam_cl, ndigits=NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT
            )
        elif scenario == "B":
            r = _taux_scenario_B_schedule_mm(jj, sched_x=sched_x, sched_y=sched_y)
            r = _apply_ndigits(r)
        elif scenario == "C":
            r = _taux_scenario_C_ct_extrap(jj, bam_cc, bam_cl)
            r = _apply_ndigits(r)
        elif scenario == "D":
            r = _taux_scenario_D_fixed(jj, bam_cc, bam_cl, g2=g2, mm_at_g2=mm_at_g2, b365=b365)
            r = _apply_ndigits(r)
        else:
            raise ValueError(scenario)
        return float(r)

    return _wrap


def _row_by_label(tab: dict[str, Any], label: str) -> list[Any]:
    for r in tab.get("rows") or []:
        if str(r.get("label") or "") == label:
            return list(r.get("values") or [])
    for r in tab.get("rows") or []:
        if label in str(r.get("label") or ""):
            return list(r.get("values") or [])
    return []


def _taux_courbe_labels(tab: dict[str, Any]) -> tuple[str, list[Any]]:
    for lab in ("Taux AA", "Taux ZC"):
        v = _row_by_label(tab, lab)
        if v:
            return lab, v
    return "", []


def _run_one_date(
    *,
    code: str,
    iso: str,
    manar: float | None,
    root: Path,
    scenarios: tuple[str, ...],
) -> dict[str, Any]:
    pillars = api._extraire_piliers_depuis_histo(root, iso, "MAR_JJ")
    ct_list, lt_list = _pillar_ct_lt_lists(pillars)
    premier_lt = _premier_lt_sql(pillars)

    req_curve = api.CurveRequest(
        short=[api.PillarShort(**p) for p in pillars["short"]],
        long=[api.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
        zc_schedule_anchor_date=iso,
    )
    bam_cc, bam_cl = api._courbes_bam_depuis_requete(req_curve)
    curve = api._make_curve(req_curve)
    sched_x, sched_y = _schedule_taux_mm_by_maturity(curve, root=root, date_courbe=iso)

    mx, tx = _dict_to_sorted_arrays(bam_cc)
    g2 = float(mx[-1])
    mm_at_g2 = float(vba_interpolate(mx, tx, g2))
    idx365 = int(np.argmin(np.abs(sched_x - 365.0)))
    b365 = float(sched_y[idx365])

    zc_path = root / "pricing/curves/courbe_zc.py"
    courbe_zc = charger_courbe_zc_depuis_fichier(zc_path)
    xlsx = api.resoudre_fichier_base_titre_oblig(root, None)
    df_in = api._charger_base_titre_oblg_cache(xlsx, [code])
    if df_in.empty:
        raise RuntimeError(f"Référentiel : aucune ligne pour CODE {code}")

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
        if _norm_code(d.get(col_code) if col_code else d.get("CODE")) == code:
            raw = d
            break
    if not raw:
        raise RuntimeError("Valorisation : code absent du résultat")

    ui = api._row_to_marche_ui(raw, iso)
    det = detecter_colonnes_base_titre(df_in)

    per_scenario: dict[str, Any] = {}
    for sc in scenarios:
        ts_fn = _ts_factory(
            sc,
            bam_cc=bam_cc,
            bam_cl=bam_cl,
            sched_x=sched_x,
            sched_y=sched_y,
            g2=g2,
            mm_at_g2=mm_at_g2,
            b365=b365,
        )
        tabs = construire_tables_amortissement_pour_valorisation(
            xlsx,
            [raw],
            [ui],
            valuation_date=iso,
            taux_secondaire_a_j=ts_fn,
            taux_zc_schedule_j=None,
            taux_zc_schedule_a=None,
            df_work=df_in,
            col_code_fichier=det.get("col_code") or col_code,
            det_cols=det,
            codes_filter=[code],
        )
        tab = next((t for t in tabs if _norm_code(t.get("code")) == code), None)
        if not tab:
            raise RuntimeError(f"Amortissement introuvable (scénario {sc})")
        prix_moteur = tab.get("prix_somme_flux_actualises")
        if prix_moteur is None:
            prix_moteur = ui.get("Prix arrondi")
        prix_tab_hi = tab.get("prix_actualise")
        per_scenario[sc] = {
            "table": tab,
            "prix_moteur": float(prix_moteur) if prix_moteur is not None else None,
            "prix_dirty_tab": float(prix_tab_hi) if prix_tab_hi is not None else None,
        }

    tab_a = per_scenario["A"]["table"]
    cols_iso = [str(c)[:10] for c in (tab_a.get("columns") or [])]
    d_valo = date.fromisoformat(str(tab_a.get("date_valorisation_utilisee_iso") or iso)[:10])

    dbg = tab_a.get("debug_rev") or {}
    k_first: int | None = None
    date_first: str | None = None
    for i, c in enumerate(tab_a.get("columns") or []):
        try:
            dc = date.fromisoformat(str(c)[:10])
        except Exception:
            continue
        if dc > d_valo:
            k_first = (dc - d_valo).days
            date_first = str(c)[:10]
            break

    flux = [float(x or 0) for x in _row_by_label(tab_a, "Flux")]
    flux_act = [float(x or 0) for x in _row_by_label(tab_a, "Flux actualisé")]
    duree = [float(x or 0) for x in _row_by_label(tab_a, "durée")]
    prime_r = [float(x or 0) for x in _row_by_label(tab_a, "Prime")]
    t_actu_r = [float(x or 0) for x in _row_by_label(tab_a, "Taux d'actualisation")]
    lbl_tc, taux_courbe_r = _taux_courbe_labels(tab_a)

    detail_rows: list[dict[str, Any]] = []
    for i, c in enumerate(tab_a.get("columns") or []):
        try:
            dc = date.fromisoformat(str(c)[:10])
        except Exception:
            continue
        if dc <= d_valo:
            continue
        k = (dc - d_valo).days
        dec = _formule_b_decompose(float(k), bam_cc, bam_cl)
        df_lin = math.nan
        if i < len(duree) and i < len(t_actu_r):
            try:
                rdec = float(t_actu_r[i]) / 100.0
                tau = float(duree[i])
                den = 1.0 + rdec * tau
                df_lin = (1.0 / den) if den > 0 and math.isfinite(den) else math.nan
            except Exception:
                df_lin = math.nan
        detail_rows.append(
            {
                "date_valo": d_valo.isoformat(),
                "date_flux": str(c)[:10],
                "K_jours": k,
                "duree_affichee": float(duree[i]) if i < len(duree) else None,
                "dernier_CT": g2,
                "premier_LT_sql": premier_lt,
                "gap_CT_LT": (float(premier_lt) - g2) if premier_lt is not None else None,
                "branche_Formule_B": dec["branche"],
                "taux_CT_si_CT": dec["taux_CT_si_CT"],
                "taux_LT_extrapole": dec["taux_LT_extrapole_si_hybride"],
                "taux_secondaire_avant_ndigits": dec["taux_secondaire_avant_ndigits"],
                "taux_courbe_pct_affiche": float(taux_courbe_r[i]) if i < len(taux_courbe_r) else None,
                "ligne_taux_courbe": lbl_tc,
                "prime_pct": float(prime_r[i]) if i < len(prime_r) else None,
                "taux_actu_pct": float(t_actu_r[i]) if i < len(t_actu_r) else None,
                "facteur_actu_REV_lineaire": df_lin,
                "flux": float(flux[i]) if i < len(flux) else None,
                "PV_flux": float(flux_act[i]) if i < len(flux_act) else None,
            }
        )

    dec_first = _formule_b_decompose(float(k_first or 0), bam_cc, bam_cl) if k_first else {}

    prix_a = per_scenario["A"]["prix_moteur"]
    ecart = (float(prix_a) - float(manar)) if (manar is not None and prix_a is not None) else None

    best_sc = None
    best_abs = math.inf
    if manar is not None:
        for sc, payload in per_scenario.items():
            px = payload.get("prix_moteur")
            if px is None:
                continue
            ad = abs(float(px) - float(manar))
            if ad < best_abs:
                best_abs = ad
                best_sc = sc

    return {
        "iso": iso,
        "code": code,
        "ct_piliers": ct_list,
        "lt_piliers": lt_list,
        "dernier_CT": g2,
        "premier_LT_sql": premier_lt,
        "joint_days_req": float(req_curve.joint_days),
        "b365_schedule_mm": b365,
        "k_premier_flux": k_first,
        "date_premier_flux": date_first,
        "decompose_premier_flux": dec_first,
        "detail_flux": detail_rows,
        "per_scenario_prix": {sc: per_scenario[sc]["prix_moteur"] for sc in scenarios},
        "prix_moteur_A": prix_a,
        "prix_manar": manar,
        "ecart_A_vs_manar": ecart,
        "scenario_plus_proche_manar": best_sc,
        "ecart_min_abs": best_abs if (best_sc is not None and math.isfinite(best_abs)) else None,
        "debug_rev": dbg,
        "methode_valo": tab_a.get("methode_valo"),
        "pricing_rev": tab_a.get("pricing_rev_bond"),
    }


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for r in rows:
        cells = []
        for x in r:
            if x is None:
                cells.append("")
            elif isinstance(x, float):
                cells.append(f"{x:.6g}".replace(".", ",") if abs(x) < 0.0001 else f"{x:.10g}".rstrip("0").rstrip("."))
            else:
                cells.append(str(x))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    import logging

    logging.getLogger("obligation_amort_schedule").setLevel(logging.ERROR)

    ap = argparse.ArgumentParser(description="Diagnostic hybride CT/LT (script autonome).")
    ap.add_argument("--code", default="9606", help="Code Maroclear")
    ap.add_argument(
        "--dates",
        nargs="+",
        default=["2026-01-02", "2026-03-06", "2026-03-26"],
        help="Dates valorisation ISO",
    )
    ap.add_argument(
        "--manar",
        action="append",
        default=[],
        metavar="AAAA-MM-JJ=prix",
        help="Prix Manar par date, ex. 2026-03-06=100455.07 (répéter l’option pour plusieurs dates)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Fichier Markdown de sortie (défaut : results/diag_hybrid_ct_lt_<code>.md)",
    )
    args = ap.parse_args()
    code = _norm_code(args.code)
    manar_map = _parse_manar_pairs(list(args.manar))
    scenarios: tuple[str, ...] = ("A", "B", "C", "D")
    out_path = args.out
    if out_path is None:
        out_path = ROOT / "results" / f"diag_hybrid_ct_lt_{code}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    blocks: list[str] = []
    summary_rows: list[list[Any]] = []

    for iso in args.dates:
        iso = str(iso).strip()[:10]
        manar = manar_map.get(iso)
        if manar is None:
            try:
                rows_m, _ = api._lire_prix_manarr_table(ROOT, iso)
                for r in rows_m:
                    if _norm_code(r.get("titre")) == code:
                        v = r.get("valo")
                        if v is not None and str(v).strip() != "":
                            manar = float(str(v).replace(",", ".").replace(" ", ""))
                        break
            except Exception:
                pass
        try:
            block = _run_one_date(code=code, iso=iso, manar=manar, root=ROOT, scenarios=scenarios)
        except Exception as e:
            blocks.append(f"\n## {iso}\n\n**Erreur** : `{type(e).__name__}` — {e}\n")
            continue

        ct = block["ct_piliers"]
        lt = block["lt_piliers"]
        gap_ctl = None
        if block["premier_LT_sql"] is not None:
            gap_ctl = float(block["premier_LT_sql"]) - float(block["dernier_CT"])
        blocks.append(
            f"\n## Date valorisation `{iso}`\n\n"
            f"- **Piliers CT (j)** : `{ct}`\n"
            f"- **Piliers LT SQL (j)** : `{lt}`\n"
            f"- **dernier_CT (G2)** = `{block['dernier_CT']:.6g}` j — **premier_LT SQL** = `{block['premier_LT_sql']}`\n"
            f"- **Écart (premier_LT_SQL − dernier_CT)** = `{gap_ctl}` j\n"
            f"- **B(365) colonne Taux échéancier ZC** (MM, décimal) = `{block['b365_schedule_mm']:.8f}`\n"
            f"- **METHODE_VALO** = `{block['methode_valo']}` | **REV** = `{block['pricing_rev']}`\n"
        )

        k0 = block["k_premier_flux"]
        df0 = block["decompose_premier_flux"]
        blocks.append(
            f"\n### Premier flux futur\n\n"
            f"- Date : **{block['date_premier_flux']}** — **K** = `{k0}` j\n"
            f"- Branche Formule B (scénario A) : **{df0.get('branche')}**\n"
            f"- Taux secondaire avant `ndigits` : `{df0.get('taux_secondaire_avant_ndigits')}`\n"
            f"- Après arrondi moteur (`ndigits={NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT}`) : `{df0.get('taux_secondaire_apres_ndigits')}`\n"
        )

        if block.get("debug_rev"):
            blocks.append(
                f"\n**debug_rev (moteur)** :\n\n```json\n{json.dumps(block['debug_rev'], ensure_ascii=False, indent=2)}\n```\n"
            )

        hdr = list(block["detail_flux"][0].keys()) if block["detail_flux"] else []
        if hdr:
            rows_m = [[row.get(h) for h in hdr] for row in block["detail_flux"]]
            blocks.append("\n### Détail par flux futur (scénario **A** — interpolation production)\n\n")
            blocks.append(_md_table(hdr, rows_m))

        blocks.append("\n### Prix par scénario (rejeu ``construire_tables`` + callback `taux_secondaire_a_j`)\n\n")
        pr_lines = [
            "| Scénario | prix_somme_flux_actualises | Écart vs Manar |",
            "| --- | ---: | ---: |",
        ]
        for sc in scenarios:
            px = block["per_scenario_prix"].get(sc)
            ec = ""
            if manar is not None and px is not None:
                ec = f"{float(px) - float(manar):+.4f}"
            pr_lines.append(f"| {sc} | {px} | {ec} |")
        blocks.append("\n".join(pr_lines))
        blocks.append(
            "\n> Les scénarios B–D ne modifient que le callback ``taux_secondaire_a_j`` pour le rejeu "
            "``construire_tables_amortissement_pour_valorisation``. Le champ JSON ``prix_actualise`` "
            "du tableau amortissement est le **prix dirty** (clean + coupon couru), distinct du clean Manar / ΣPV.\n"
        )
        if block.get("scenario_plus_proche_manar"):
            blocks.append(
                f"\n**Scénario le plus proche de Manar** : `{block['scenario_plus_proche_manar']}` "
                f"(|Δ| min = `{block['ecart_min_abs']}`)\n"
            )
        else:
            blocks.append("\n**Scénario le plus proche de Manar** : — (prix Manar non fourni pour cette date)\n")

        summary_rows.append(
            [
                iso,
                k0,
                f"{block['dernier_CT']:.0f}",
                str(block["premier_LT_sql"]),
                str(df0.get("branche")),
                f"{df0.get('taux_secondaire_apres_ndigits')}",
                str(block["debug_rev"].get("taux_actualisation_pct")) if block.get("debug_rev") else "",
                str(block["prix_moteur_A"]),
                str(manar) if manar is not None else "—",
                f"{block['ecart_A_vs_manar']:+.4f}" if block.get("ecart_A_vs_manar") is not None else "—",
            ]
        )

    intro = (
        f"# Diagnostic hybride CT / LT — CODE **{code}**\n\n"
        "Script : `scripts/diag_9606_hybrid_ct_lt.py` (hors production, aucune modification du moteur).\n\n"
        "Si une capture Manar indiquait un écart (ex. +0,81 pt) alors que ce rapport montre ~0 sur ``prix_somme_flux_actualises``, "
        "comparer la **même colonne** côté Manar (clean vs dirty) et la **version du moteur**.\n\n"
        "**Scénarios** :\n"
        "- **A** : `taux_secondaire_interpole_formule_b` (logique actuelle pricing).\n"
        "- **B** : interpolation **linéaire** sur la colonne **Taux** (MM) de l’échéancier ZC tracé (`_schedule_table_records`).\n"
        "- **C** : zone ]G2 ; 365[ : **extrapolation CT seule** (`vba_interpolate_extrapolate` sur la grille MM court terme).\n"
        "- **D** : zone ]G2 ; 365[ : droite MM entre **(G2 ; MM(G2))** et **(365 ; B₃₆₅)** où `B₃₆₅` = taux **Taux** échéancier au point le plus proche de 365 j.\n\n"
        "Arrondi final : même `ndigits` que la production pour B/C/D (comparabilité).\n\n"
    )
    summary = "## Tableau comparatif (synthèse)\n\n" + _md_table(
        [
            "date_valo",
            "K_premier_flux",
            "dernier_CT",
            "premier_LT",
            "branche_A",
            "taux_secondaire_A_ndigits",
            "taux_actu_debug_rev_pct",
            "prix_moteur_A",
            "prix_Manar",
            "écart_A",
        ],
        summary_rows,
    )

    out_path.write_text(intro + summary + "\n" + "".join(blocks), encoding="utf-8")
    print(f"Écrit : {out_path}", flush=True)


if __name__ == "__main__":
    main()
