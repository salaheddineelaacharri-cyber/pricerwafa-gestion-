"""
Export CSV diagnostic écarts valorisation (mode TRACE, aucune logique par code).

Usage (racine projet, SQL + prix Manar comme l’API) ::
  set PRICER_VALO_TRACE=1
  python scripts/debug_valorisation_ecarts_export.py

Sortie : ``debug_valorisation_ecarts_0201_0603.csv`` à la racine du projet.

Les codes analysés sont passés en arguments optionnels ; sinon la liste par défaut du périmètre demandé.
"""

from __future__ import annotations

import csv
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PRICER_VALO_TRACE", "1")

from backend import main as api
from pricing.debug_valorisation_trace import (
    classifier_cause_probable,
    comparer_deux_dates,
    diagnostic_interpolation_formule_b,
    inferer_periodicite_code,
    inferer_type_amortissement,
    resumes_groupes,
    serialiser_flux_debug,
)
from valuation_zc_obligations import charger_courbe_zc_depuis_fichier, valoriser_dataframe_base_titre

DATE_ISO_A = "2026-01-02"
DATE_ISO_B = "2026-03-06"
OUT_CSV = ROOT / "debug_valorisation_ecarts_0201_0603.csv"

DEFAULT_CODES = [
    "2151",
    "5061",
    "5106",
    "5107",
    "5116",
    "5117",
    "5122",
    "5151",
    "9070",
    "9302",
    "9307",
    "9346",
    "9351",
    "9363",
    "9395",
    "9402",
    "9411",
    "9424",
    "9452",
    "9473",
    "9487",
    "9488",
    "9502",
    "9518",
    "9524",
    "9538",
    "9572",
    "9576",
    "9580",
    "9626",
    "9686",
    "9689",
    "9690",
    "9703",
    "9707",
    "9714",
    "9744",
    "9755",
    "9756",
    "9757",
    "100948",
    "100993",
    "100995",
    "101005",
    "101006",
    "201657",
    "201868",
]


def _norm_code(v: object) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _pick(row: dict, *names: str) -> object:
    for n in names:
        for k, v in row.items():
            if str(k).strip().upper() == n.strip().upper():
                return v
    return None


def _sf(v: object, default: float = float("nan")) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _curve_req(root: Path, iso: str) -> api.CurveRequest:
    pillars = api._extraire_piliers_depuis_histo(root, iso, "MAR_JJ")
    return api.CurveRequest(
        short=[api.PillarShort(**p) for p in pillars["short"]],
        long=[api.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )


def _pilier_list_label(pillars: dict) -> str:
    s = [f"{float(p['maturity_days']):.0f}" for p in pillars.get("short") or []]
    l_ = [f"{float(p['maturity_days']):.0f}" for p in pillars.get("long") or []]
    return f"CT[{','.join(s)}];LT[{','.join(l_)}]"


def _manar_map(root: Path, iso: str) -> dict[str, float]:
    rows, _ = api._lire_prix_manarr_table(root, iso)
    out: dict[str, float] = {}
    for r in rows:
        c = _norm_code(r.get("titre"))
        v = _sf(r.get("valo"), float("nan"))
        if c and math.isfinite(v):
            out[c] = float(v)
    return out


def _row_for_date(
    root: Path,
    iso: str,
    codes: list[str],
) -> tuple[dict[str, dict], dict[str, Any], str]:
    """Valorise tous les codes demandés pour une date ; retourne map code -> ligne brute + meta."""
    zc_path = root / "pricing/curves/courbe_zc.py"
    courbe = charger_courbe_zc_depuis_fichier(zc_path)
    pillars_dict = api._extraire_piliers_depuis_histo(root, iso, "MAR_JJ")
    req_curve = _curve_req(root, iso)
    bam_cc, bam_cl = api._courbes_bam_depuis_requete(req_curve)
    xlsx = api.resoudre_fichier_base_titre_oblig(root, None)
    df_in = api._charger_base_titre_oblg_cache(xlsx, codes)
    if df_in.empty:
        return {}, {"erreur": "referentiel vide pour ces codes"}, _pilier_list_label(pillars_dict)

    df_out, meta = valoriser_dataframe_base_titre(
        df_in,
        courbe,
        valuation_date=iso,
        bam_courbe_court=bam_cc,
        bam_courbe_long=bam_cl,
        progress_label=None,
    )
    by_code: dict[str, dict] = {}
    if df_out.empty:
        return {}, meta, _pilier_list_label(pillars_dict)

    col_code = None
    for c in df_out.columns:
        if str(c).strip().upper() == "CODE":
            col_code = c
            break
    for _, row in df_out.iterrows():
        d = row.to_dict()
        cc = _norm_code(d.get(col_code) if col_code else d.get("CODE"))
        if cc:
            by_code[cc] = d
    meta["pilier_label"] = _pilier_list_label(pillars_dict)
    meta["joint_day"] = pillars_dict.get("joint_long_day")
    meta["bam_cc"] = bam_cc
    meta["bam_cl"] = bam_cl
    meta["pillars_raw"] = pillars_dict
    return by_code, meta, meta["pilier_label"]


def _build_csv_row(
    code: str,
    iso: str,
    raw: dict,
    meta: dict,
    manar: dict[str, float],
    compare_other: dict | None,
) -> dict[str, Any]:
    bam_cc = meta.get("bam_cc") or {}
    bam_cl = meta.get("bam_cl") or {}
    mat_j = _sf(raw.get("maturite_residuelle_jours"))
    diag = diagnostic_interpolation_formule_b(mat_j, bam_cc, bam_cl)

    ui = api._row_to_marche_ui(raw, iso)
    pa = _sf(ui.get("Prix arrondi"))
    pm = manar.get(code)
    pm_ok = pm is not None and math.isfinite(pm)
    ecart = round(pa - float(pm), 4) if pm_ok and math.isfinite(pa) else float("nan")

    dirty = _sf(ui.get("Prix dirty"), _sf(raw.get("prix_dirty")))
    clean = _sf(ui.get("Prix clean"), _sf(raw.get("prix_clean_atp")))
    cc = _sf(ui.get("Coupon courru"), _sf(raw.get("coupon_courru_atp")))
    ytm = _sf(ui.get("YTM"), _sf(raw.get("ytm")))

    trace_dates = raw.get("trace_flux_dates_iso") or []
    trace_days = raw.get("trace_pay_days") or []
    nb_flux = len(trace_dates) if trace_dates else len(trace_days)
    if trace_dates:
        pfd = trace_dates[0]
        pfm = _sf((raw.get("trace_flux_montants") or [None])[0])
    elif trace_days:
        pfd = f"{trace_days[0]:.2f}j"
        pfm = _sf((raw.get("trace_cash_flows") or [None])[0])
    else:
        pfd, pfm = "", float("nan")

    somme_pv = _sf(raw.get("trace_somme_pv"))

    row_diag = {
        **raw,
        "nb_flux_futurs": nb_flux,
        "prochain_flux_date": pfd,
        "prochain_flux_montant": pfm,
        "taux_interpole_diag": diag["taux_interpole_formule_b_dec"],
        "zone_CT_LT_diag": diag["zone_CT_LT"],
        "ecart": ecart,
        "coupon_couru": cc,
    }

    cause, comm = classifier_cause_probable(
        row_diag,
        ecart_abs=abs(ecart) if math.isfinite(ecart) else 999.0,
        prix_manar_ok=pm_ok,
        compare_prev=compare_other,
    )
    comp = ""
    if compare_other:
        comp = comparer_deux_dates(compare_other, row_diag)

    status = "OK"
    if not pm_ok:
        status = "WARN_MANAR"
    elif not math.isfinite(ecart):
        status = "ERROR"
    elif abs(ecart) > 0.02:
        status = "ECART"

    cat = str(_pick(raw, "CATEGORIE") or "")
    base_txt = str(_pick(raw, "BASE_CALCUL") or "")
    type_taux = str(_pick(raw, "TYPE_TAUX") or "")

    return {
        "date_valo": iso,
        "code_titre": code,
        "famille_source": str(meta.get("taux_secondaire_source") or meta.get("valuation_date_utilisee") or ""),
        "prix_moteur": round(pa, 6) if math.isfinite(pa) else "",
        "prix_manar": round(float(pm), 6) if pm_ok else "",
        "ecart": round(ecart, 6) if math.isfinite(ecart) else "",
        "status": status,
        "date_emission": str(_pick(raw, "date_emission_iso") or _pick(raw, "DATE_EMISSION") or ""),
        "date_echeance": str(_pick(raw, "DATE_ECHEANCE") or ""),
        "maturite_residuelle": round(mat_j, 6) if math.isfinite(mat_j) else "",
        "capital_restant_du": _sf(_pick(raw, "NOMINAL"), _sf(raw.get("nominal_valo"))),
        "taux_coupon": _sf(raw.get("taux_coupon_decimal")) * 100.0
        if math.isfinite(_sf(raw.get("taux_coupon_decimal")))
        else "",
        "coupon_couru": round(cc, 6) if math.isfinite(cc) else "",
        "dirty_price": round(dirty, 6) if math.isfinite(dirty) else "",
        "clean_price": round(clean, 6) if math.isfinite(clean) else "",
        "ytm": round(ytm, 8) if math.isfinite(ytm) else "",
        "courbe_utilisee": f"BAM MAR_JJ / HISTO_COURBE_TAUX ({iso})",
        "pilier_avant": diag["pilier_avant_j"],
        "pilier_apres": diag["pilier_apres_j"],
        "taux_pilier_avant": diag["taux_pilier_avant_dec"],
        "taux_pilier_apres": diag["taux_pilier_apres_dec"],
        "taux_interpole": diag["taux_interpole_formule_b_dec"],
        "methode_interpolation": diag["methode_interpolation"],
        "zone_CT_LT": diag["zone_CT_LT"],
        "nb_flux_futurs": nb_flux,
        "prochain_flux_date": pfd,
        "prochain_flux_montant": pfm if math.isfinite(pfm) else "",
        "somme_flux_actualises": somme_pv if math.isfinite(somme_pv) else "",
        "cause_probable": cause,
        "commentaire_diagnostic": " | ".join(
            x for x in (comm, comp) if x
        ),
        "_profil_type_taux": type_taux,
        "_profil_cat": cat,
        "_profil_amort": inferer_type_amortissement(raw),
        "_profil_period": inferer_periodicite_code(raw),
        "_profil_base": base_txt,
        "_piliers_disponibles": meta.get("pilier_label", ""),
        "_moteur": raw.get("moteur_prix"),
        "_detail_flux_json": serialiser_flux_debug(raw),
        "_point_CT": diag["zone_CT_LT"] == "CT",
        "_point_LT": diag["zone_CT_LT"] == "LT",
        "_passage_transition": diag.get("passage_zone_transition"),
        "_joint_J": meta.get("joint_day"),
    }


def main() -> None:
    codes = [c.strip() for c in (sys.argv[1:] or []) if c.strip()]
    if not codes:
        codes = list(DEFAULT_CODES)

    print(f"TRACE PRICER_VALO_TRACE={os.environ.get('PRICER_VALO_TRACE')} | {len(codes)} codes", flush=True)

    m_a, meta_a, _ = _row_for_date(ROOT, DATE_ISO_A, codes)
    m_b, meta_b, _ = _row_for_date(ROOT, DATE_ISO_B, codes)

    manar_a = _manar_map(ROOT, DATE_ISO_A)
    manar_b = _manar_map(ROOT, DATE_ISO_B)

    rows_out: list[dict[str, Any]] = []
    for code in codes:
        raw_a = m_a.get(code)
        raw_b = m_b.get(code)
        if raw_a:
            rows_out.append(
                _build_csv_row(code, DATE_ISO_A, raw_a, meta_a, manar_a, None)
            )
        else:
            rows_out.append(
                {
                    "date_valo": DATE_ISO_A,
                    "code_titre": code,
                    "status": "ABSENT_MOTEUR",
                    "commentaire_diagnostic": "Pas de ligne valorisée (référentiel / échéance / données manquantes).",
                }
            )
        if raw_b:
            other = None
            if raw_a:
                man_a = manar_a.get(code)
                ui_a = api._row_to_marche_ui(raw_a, DATE_ISO_A)
                ea = (
                    round(_sf(ui_a.get("Prix arrondi")) - float(man_a), 4)
                    if man_a is not None
                    else float("nan")
                )
                other = {
                    **raw_a,
                    "nb_flux_futurs": len(
                        raw_a.get("trace_flux_dates_iso") or raw_a.get("trace_pay_days") or []
                    ),
                    "prochain_flux_date": (
                        (raw_a.get("trace_flux_dates_iso") or [""])[0]
                        if raw_a.get("trace_flux_dates_iso")
                        else (
                            f'{raw_a.get("trace_pay_days", [""])[0]:.2f}j'
                            if raw_a.get("trace_pay_days")
                            else ""
                        )
                    ),
                    "taux_interpole_diag": diagnostic_interpolation_formule_b(
                        _sf(raw_a.get("maturite_residuelle_jours")),
                        meta_a.get("bam_cc") or {},
                        meta_a.get("bam_cl") or {},
                    )["taux_interpole_formule_b_dec"],
                    "zone_CT_LT_diag": diagnostic_interpolation_formule_b(
                        _sf(raw_a.get("maturite_residuelle_jours")),
                        meta_a.get("bam_cc") or {},
                        meta_a.get("bam_cl") or {},
                    )["zone_CT_LT"],
                    "coupon_couru": _sf(raw_a.get("coupon_courru_atp")),
                    "BASE_CALCUL": _pick(raw_a, "BASE_CALCUL"),
                    "ecart": ea,
                }
            rows_out.append(
                _build_csv_row(code, DATE_ISO_B, raw_b, meta_b, manar_b, other)
            )
        else:
            rows_out.append(
                {
                    "date_valo": DATE_ISO_B,
                    "code_titre": code,
                    "status": "ABSENT_MOTEUR",
                    "commentaire_diagnostic": "Pas de ligne valorisée (référentiel / échéance / données manquantes).",
                }
            )

    columns = [
        "date_valo",
        "code_titre",
        "famille_source",
        "prix_moteur",
        "prix_manar",
        "ecart",
        "status",
        "date_emission",
        "date_echeance",
        "maturite_residuelle",
        "capital_restant_du",
        "taux_coupon",
        "coupon_couru",
        "dirty_price",
        "clean_price",
        "ytm",
        "courbe_utilisee",
        "pilier_avant",
        "pilier_apres",
        "taux_pilier_avant",
        "taux_pilier_apres",
        "taux_interpole",
        "methode_interpolation",
        "zone_CT_LT",
        "nb_flux_futurs",
        "prochain_flux_date",
        "prochain_flux_montant",
        "somme_flux_actualises",
        "cause_probable",
        "commentaire_diagnostic",
        "type_taux_FIX_REV",
        "type_amortissement_AA_ZC_TA",
        "periodicite_AN_SEM_TRI",
        "base_calcul",
        "categorie_ORD_OBL_FPCT_BDT_BSF_CD",
        "piliers_CT_LT_disponibles",
        "moteur_ATP_ZC",
        "detail_flux_json",
        "point_CT",
        "point_LT",
        "passage_CT_LT_transition",
        "joint_J",
    ]

    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows_out:
            r2 = dict(r)
            r2["type_taux_FIX_REV"] = r2.pop("_profil_type_taux", "")
            r2["type_amortissement_AA_ZC_TA"] = r2.pop("_profil_amort", "")
            r2["periodicite_AN_SEM_TRI"] = r2.pop("_profil_period", "")
            r2["base_calcul"] = r2.pop("_profil_base", "")
            r2["categorie_ORD_OBL_FPCT_BDT_BSF_CD"] = r2.pop("_profil_cat", "")
            r2["piliers_CT_LT_disponibles"] = r2.pop("_piliers_disponibles", "")
            r2["moteur_ATP_ZC"] = r2.pop("_moteur", "")
            r2["detail_flux_json"] = r2.pop("_detail_flux_json", "")
            r2["point_CT"] = r2.pop("_point_CT", "")
            r2["point_LT"] = r2.pop("_point_LT", "")
            r2["passage_CT_LT_transition"] = r2.pop("_passage_transition", "")
            r2["joint_J"] = r2.pop("_joint_J", "")
            w.writerow({k: r2.get(k, "") for k in columns})

    print(f"Écrit : {OUT_CSV}", flush=True)

    ecarts_moy: dict[str, float] = {}
    meta_cat: dict[str, dict] = {}
    for code in codes:
        ra = m_a.get(code)
        rb = m_b.get(code)
        if not ra or not rb:
            continue
        ua = api._row_to_marche_ui(ra, DATE_ISO_A)
        ub = api._row_to_marche_ui(rb, DATE_ISO_B)
        ma = manar_a.get(code)
        mb = manar_b.get(code)
        if ma is None or mb is None:
            continue
        ea = _sf(ua.get("Prix arrondi")) - float(ma)
        eb = _sf(ub.get("Prix arrondi")) - float(mb)
        ecarts_moy[code] = 0.5 * (abs(ea) + abs(eb))
        meta_cat[code] = ra

    groups = resumes_groupes(ecarts_moy, meta_cat)
    print("\n--- Résumé groupé (périmètre avec valorisation sur les 2 dates) ---\n")
    for label, arr in groups.items():
        print(f"{label}: {', '.join(arr) if arr else '(vide)'}")


if __name__ == "__main__":
    main()
