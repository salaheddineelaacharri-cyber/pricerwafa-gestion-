"""
Diagnostics racine des écarts de valorisation (Formule B, classification).

Aucune logique par code titre : uniquement des heuristiques sur profils référentiel + deltas entre dates.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from pricing.curves.zc_interpolation_excel import taux_secondaire_interpole_formule_b


def _sorted_xy(cc: dict[float, float]) -> tuple[np.ndarray, np.ndarray]:
    xs = sorted(float(k) for k in cc.keys())
    return np.array(xs, dtype=float), np.array([float(cc[x]) for x in xs], dtype=float)


def diagnostic_interpolation_formule_b(
    maturite_jours: float,
    courbe_court: dict[float, float],
    courbe_long: dict[float, float],
) -> dict[str, Any]:
    """
    Décrit la zone CT / LT / transition et les piliers encadrants utilisés par ``taux_secondaire_interpole_formule_b``.
    """
    mx, tx = _sorted_xy(courbe_court)
    my, ty = _sorted_xy(courbe_long)
    k = float(maturite_jours)
    max_ct = float(mx[-1]) if mx.size else 0.0
    r = float(
        taux_secondaire_interpole_formule_b(
            k, courbe_court, courbe_long, ndigits=None
        )
    )

    if k >= 365.0:
        zone = "LT"
        mm, tt = my, ty
        idx = int(np.searchsorted(mm, k, side="left"))
        if idx == 0:
            pa, pb = float(mm[0]), float(mm[1]) if mm.size > 1 else float(mm[0])
            ta, tb = float(tt[0]), float(tt[1]) if tt.size > 1 else float(tt[0])
        elif idx >= len(mm):
            pa, pb = float(mm[-2]), float(mm[-1])
            ta, tb = float(tt[-2]), float(tt[-1])
        else:
            pa, pb = float(mm[idx - 1]), float(mm[idx])
            ta, tb = float(tt[idx - 1]), float(tt[idx])
        methode = "vba_interpolate_extrapolate(grille_LT)"
    elif k <= max_ct:
        zone = "CT"
        mm, tt = mx, tx
        idx = int(np.searchsorted(mm, k, side="left"))
        if idx == 0:
            pa, pb = float(mm[0]), float(mm[1]) if mm.size > 1 else float(mm[0])
            ta, tb = float(tt[0]), float(tt[1]) if tt.size > 1 else float(tt[0])
        elif idx >= len(mm):
            pa, pb = float(mm[-2]), float(mm[-1])
            ta, tb = float(tt[-2]), float(tt[-1])
        else:
            pa, pb = float(mm[idx - 1]), float(mm[idx])
            ta, tb = float(tt[idx - 1]), float(tt[idx])
        methode = "vba_interpolate(grille_CT)"
    else:
        zone = "TRANSITION_CT_to_LT"
        pa = max_ct
        ta = float(tx[-1])
        if my.size > 0:
            pb = float(my[0])
            tb = float(ty[0])
        else:
            pb = k
            tb = float("nan")
        methode = (
            "Entre MM dernier pilier CT et formule ((1+r_LT)^(k/365)-1)*360/k ; "
            "pas d’interpolation linéaire simple sur une seule grille."
        )

    return {
        "maturite_jours": k,
        "zone_CT_LT": zone,
        "pilier_avant_j": pa,
        "pilier_apres_j": pb,
        "taux_pilier_avant_dec": ta,
        "taux_pilier_apres_dec": tb,
        "taux_interpole_formule_b_dec": r,
        "methode_interpolation": methode,
        "max_pilier_CT_j": max_ct,
        "passage_zone_transition": bool(max_ct < k < 365.0),
        "frontiere_365": k >= 365.0,
    }


def _norm_txt(x: object) -> str:
    return str(x or "").strip().upper()


def inferer_type_amortissement(row: dict[str, Any]) -> str:
    """AA / ZC / TA heuristique selon référentiel + moteur."""
    mot = ""
    cat = ""
    meth_val = ""
    for k, v in row.items():
        ku = str(k).strip().upper()
        if ku == "METHODE_COUPON":
            mot = _norm_txt(v)
        elif ku == "CATEGORIE":
            cat = _norm_txt(v)
        elif ku == "METHODE_VALO":
            meth_val = _norm_txt(v)
    moteur = _norm_txt(row.get("moteur_prix"))
    if "AMORT" in mot or "LINE" in mot or "ANNUIT" in mot:
        return "AA"
    if cat == "FPCT" or "TA" in meth_val:
        return "TA"
    if moteur == "ZC" or "ZC" in meth_val:
        return "ZC"
    return "AUTRE"


def inferer_periodicite_code(row: dict[str, Any]) -> str:
    p = ""
    for k, v in row.items():
        if str(k).strip().upper() == "PERIODICITE_COUPON":
            p = _norm_txt(v)
            break
    if p.startswith("SEM"):
        return "SEM"
    if p.startswith("TRI"):
        return "TRI"
    if p.startswith("AN"):
        return "AN"
    return p or "?"


def classifier_cause_probable(
    row: dict[str, Any],
    *,
    ecart_abs: float,
    prix_manar_ok: bool,
    compare_prev: dict[str, Any] | None,
) -> tuple[str, str]:
    """
    Retourne (cause_code, commentaire court).
    Causes : familles demandées par l'utilisateur (pas de branche par CODE).
    """
    cat = ""
    tt = ""
    for k, v in row.items():
        ku = str(k).strip().upper()
        if ku == "CATEGORIE":
            cat = _norm_txt(v)
        elif ku == "TYPE_TAUX":
            tt = _norm_txt(v)
    moteur = _norm_txt(row.get("moteur_prix"))
    base = ""
    meth_coupon = ""
    for k, v in row.items():
        ku = str(k).strip().upper()
        if ku == "BASE_CALCUL":
            base = _norm_txt(v)
        elif ku == "METHODE_COUPON":
            meth_coupon = _norm_txt(v)
    ia = inferer_type_amortissement(row)

    hints: list[str] = []

    if not prix_manar_ok:
        return "problème données référentiel", "Prix Manar / valo absent ou illisible pour cette date."

    if ecart_abs < 0.02:
        return "problème arrondi/précision", "|écart| négligeable vs tolérance typique ±0,02."

    if cat == "FPCT":
        hints.append("FPCT : flux/jouissance/prorata spécifiques.")
        return "problème FPCT", " ; ".join(hints)

    if tt == "REV":
        hints.append("Titre REV : maturité résiduelle révisée / flux réduit.")
        return "problème taux révisable", " ; ".join(hints)

    if ia == "ZC" or moteur == "ZC":
        return "problème zéro-coupon", "Moteur ZC : écarts souvent liés à maille courbe, spread ou periodicité coupon."

    if "R/360" in base and "R/R" in meth_coupon:
        hints.append("Mix base R/360 et méthode coupon R/R : sensibilité coupon couru / ACT.")

    if ia == "AA":
        return "problème amortissable", "Profil amortissable / échéancier : vérifier tombées et CRD."

    if cat == "BDT":
        return "problème BDT", "BDT : rendement marché vs secondaire, ou règles spécifiques garantie."

    interp_prev = compare_prev.get("taux_interpole_formule_b_dec") if compare_prev else None
    interp_cur = row.get("taux_interpole_diag")
    if (
        compare_prev is not None
        and interp_prev is not None
        and interp_cur is not None
        and math.isfinite(float(interp_prev))
        and math.isfinite(float(interp_cur))
    ):
        dz = abs(float(interp_cur) - float(interp_prev))
        zprev = _norm_txt(str(compare_prev.get("zone_CT_LT")))
        zn = _norm_txt(str(row.get("zone_CT_LT_diag")))
        if dz > 5e-4 or zprev != zn:
            hints.append(
                f"Saut interpolation ou zone CT/LT ({zprev!s}→{zn!s}), Δtaux≈{dz:.6f}."
            )

    if compare_prev:
        nf = compare_prev.get("nb_flux_futurs")
        n0 = row.get("nb_flux_futurs")
        if nf is not None and n0 is not None and int(nf) != int(n0):
            return "problème flux futurs", f"Nombre de flux utilisés change : {nf} → {n0}."

    if compare_prev:
        d0 = compare_prev.get("prochain_flux_date")
        d1 = row.get("prochain_flux_date")
        if d0 and d1 and str(d0) != str(d1):
            hints.append(f"Prochain flux date {d0} → {d1}.")

    if hints:
        return "problème interpolation courbe", " ".join(hints)

    if ecart_abs < 1.0:
        return "problème arrondi/précision", "Écart modéré : cumul arrondis taux %, prix clean/dirty, ou table Valorisation."

    return "problème données référentiel", "Écart significatif sans signal automatique fort ; audit manuel SQL/referentiel."


def serialiser_flux_debug(row: dict[str, Any]) -> str:
    """Flux futurs + DF + PV en JSON compact pour CSV."""
    parts: list[dict[str, Any]] = []
    dates = row.get("trace_flux_dates_iso") or []
    mts = row.get("trace_flux_montants") or []
    dfs = row.get("trace_discount_factors") or []
    pvs = row.get("trace_pv_flows") or []
    days = row.get("trace_pay_days")
    if days is not None:
        cfs = row.get("trace_cash_flows") or []
        rts = row.get("trace_rates_actu_par_flux") or []
        for i in range(len(days)):
            item: dict[str, Any] = {
                "pay_day": days[i] if i < len(days) else None,
                "cash": cfs[i] if i < len(cfs) else None,
                "taux_actu": rts[i] if i < len(rts) else None,
                "df": dfs[i] if i < len(dfs) else None,
                "pv": pvs[i] if i < len(pvs) else None,
            }
            parts.append(item)
    else:
        for i in range(len(dates)):
            item = {
                "date": dates[i] if i < len(dates) else None,
                "cash": mts[i] if i < len(mts) else None,
                "df": dfs[i] if i < len(dfs) else None,
                "pv": pvs[i] if i < len(pvs) else None,
            }
            parts.append(item)
    try:
        return json.dumps(parts, ensure_ascii=False)
    except TypeError:
        return str(parts)


def comparer_deux_dates(
    a: dict[str, Any],
    b: dict[str, Any],
) -> str:
    """Résumé comparatif 02/01 vs 06/03 pour colonne commentaire."""
    chunks: list[str] = []
    if int(a.get("nb_flux_futurs") or -1) != int(b.get("nb_flux_futurs") or -2):
        chunks.append(
            f"nb_flux {a.get('nb_flux_futurs')}→{b.get('nb_flux_futurs')}"
        )
    if str(a.get("prochain_flux_date")) != str(b.get("prochain_flux_date")):
        chunks.append(
            f"prochain_coupon {a.get('prochain_flux_date')}→{b.get('prochain_flux_date')}"
        )
    ta = a.get("taux_interpole_diag")
    tb = b.get("taux_interpole_diag")
    if (
        ta is not None
        and tb is not None
        and math.isfinite(float(ta))
        and math.isfinite(float(tb))
        and abs(float(tb) - float(ta)) > 1e-6
    ):
        chunks.append(f"Δtaux_interp={float(tb)-float(ta):.8f}")
    if str(a.get("BASE_CALCUL")) != str(b.get("BASE_CALCUL")):
        chunks.append("BASE_CALCUL différente (anomalie référentiel).")
    cca = float(a.get("coupon_couru") or 0)
    ccb = float(b.get("coupon_couru") or 0)
    if math.isfinite(cca) and math.isfinite(ccb):
        chunks.append(f"Δcoupon_couru={ccb-cca:.6f}")
    ea = float(a.get("ecart") or float("nan"))
    eb = float(b.get("ecart") or float("nan"))
    if math.isfinite(ea) and math.isfinite(eb):
        chunks.append(
            f"signe_ecart même sens={ea * eb > 0}; stabilité |Δécart|={abs(abs(eb)-abs(ea)):.4f}"
        )
    return "; ".join(chunks) if chunks else "peu de variation structurante auto-détectée"


def resumes_groupes(ecarts_par_code: dict[str, float], rows_by_code_cat: dict[str, dict]) -> dict[str, list[str]]:
    """Regroupe les codes par familles demandées dans le cahier des charges."""
    zc: list[str] = []
    fpct: list[str] = []
    rev_aa: list[str] = []
    bdt: list[str] = []
    huge: list[str] = []
    medium: list[str] = []
    tiny: list[str] = []

    for code, ec in ecarts_par_code.items():
        if not math.isfinite(ec):
            continue
        ae = abs(ec)
        if ae > 100:
            huge.append(code)
        elif ae >= 1:
            medium.append(code)
        else:
            tiny.append(code)

    for code, meta in rows_by_code_cat.items():
        cat = _norm_txt(meta.get("CATEGORIE"))
        tt = _norm_txt(meta.get("TYPE_TAUX"))
        ia = inferer_type_amortissement(meta)
        if cat == "FPCT":
            fpct.append(code)
        if cat == "BDT":
            bdt.append(code)
        if tt == "REV" or ia == "AA":
            rev_aa.append(code)
        if ia == "ZC":
            zc.append(code)

    return {
        "zc": sorted(set(zc)),
        "fpct": sorted(set(fpct)),
        "rev_aa": sorted(set(rev_aa)),
        "bdt": sorted(set(bdt)),
        "ecart_gt_100": sorted(huge),
        "ecart_1_a_100": sorted(medium),
        "ecart_lt_1": sorted(tiny),
    }
