"""
Relance la valorisation Prix Manar (tous les titres du tableau) pour plusieurs dates
et exporte un CSV comparatif : Valo, Prix arrondi, écart, profil, source prix.

Usage (racine projet) :
  python scripts/prix_manar_full_compare_dates.py
  python scripts/prix_manar_full_compare_dates.py --dates 2026-03-26 2026-03-06 2026-01-02

Prérequis : SQL BAM (histo_courbe_taux), référentiel, ``prix mar.xlsx`` avec une ligne
par (titre, date) pour **chaque** date comparée — sinon la lecture retombe sur
``prix manarrr.xlsx`` et la **Valo** peut être la même pour toutes les dates (écarts non comparables).

Sortie : ``results/prix_manar_compare_dates.csv``
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _norm_code(v: object) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _f(v: object) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _curve_for_date(api: object, iso: str):
    pillars = api._extraire_piliers_depuis_histo(ROOT, iso, "MAR_JJ")
    return api.CurveRequest(
        short=[api.PillarShort(**p) for p in pillars["short"]],
        long=[api.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )


def _valorize(api: object, iso: str) -> list[dict]:
    req = api.MarcheValorizeRequest(
        valuation_date=iso,
        curve=_curve_for_date(api, iso),
        prix_manarr_pricer_tous=True,
    )
    res = api.marche_valorize(req)
    body = res.body if hasattr(res, "body") else res
    if isinstance(body, (bytes, str)):
        data = json.loads(body)
    else:
        data = dict(body)
    return list(data.get("prix_manarr") or [])


def main() -> None:
    ap = argparse.ArgumentParser(description="Comparaison Prix Manar multi-dates.")
    ap.add_argument(
        "--dates",
        nargs="+",
        default=["2026-03-26", "2026-03-06", "2026-01-02"],
        help="Dates ISO (YYYY-MM-DD), dans l'ordre souhaité pour les colonnes.",
    )
    ap.add_argument(
        "--tol",
        type=float,
        default=0.02,
        help="Tolérance ± sur écart (pour colonne statut).",
    )
    args = ap.parse_args()
    dates: list[str] = args.dates
    tol = float(args.tol)

    from backend import main as api  # noqa: E402

    per_date: dict[str, dict[str, dict]] = {}
    for iso in dates:
        rows = _valorize(api, iso)
        per_date[iso] = {_norm_code(r.get("titre")): r for r in rows if _norm_code(r.get("titre"))}

    all_codes = sorted(set(c for m in per_date.values() for c in m))

    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "prix_manar_compare_dates.csv"

    # Colonnes dynamiques par date
    fieldnames = ["code"]
    for iso in dates:
        d = iso.replace("-", "")[2:]
        fieldnames.extend(
            [
                f"valo_{d}",
                f"prix_arrondi_{d}",
                f"ecart_{d}",
                f"profil_{d}",
                f"source_prix_{d}",
                f"source_ecart_{d}",
            ]
        )
    fieldnames.extend(["delta_ecart_premier_vs_second", "note"])

    def iso_tail(iso: str) -> str:
        return iso.replace("-", "")[2:]

    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for code in all_codes:
            row: dict[str, str] = {"code": code}
            ecarts: list[float | None] = []
            for iso in dates:
                t = iso_tail(iso)
                r = per_date[iso].get(code)
                if not r:
                    row[f"valo_{t}"] = ""
                    row[f"prix_arrondi_{t}"] = ""
                    row[f"ecart_{t}"] = ""
                    row[f"profil_{t}"] = ""
                    row[f"source_prix_{t}"] = ""
                    row[f"source_ecart_{t}"] = ""
                    ecarts.append(None)
                    continue
                v = _f(r.get("valo"))
                pa = _f(r.get("prix_arrondi"))
                ec = _f(r.get("ecart_prix_arrondi_valo"))
                row[f"valo_{t}"] = f"{v:.6f}" if v is not None else ""
                row[f"prix_arrondi_{t}"] = f"{pa:.6f}" if pa is not None else ""
                row[f"ecart_{t}"] = f"{ec:.6f}" if ec is not None else ""
                row[f"profil_{t}"] = str(r.get("profil_metier") or "")
                row[f"source_prix_{t}"] = str(r.get("source_prix_arrondi") or "")
                row[f"source_ecart_{t}"] = str(r.get("source_ecart") or "")
                ecarts.append(ec)

            note = ""
            delta_s = ""
            if len(dates) >= 2:
                e0, e1 = ecarts[0], ecarts[1]
                if e0 is not None and e1 is not None:
                    de = e1 - e0
                    delta_s = f"{de:.6f}"
                    if abs(e0) <= tol and abs(e1) <= tol:
                        note = "OK les deux dates"
                    elif abs(e0) <= tol and abs(e1) > tol:
                        note = "OK ref_date | ecart autre date"
                    elif abs(e0) > tol and abs(e1) > tol:
                        if abs(de) <= tol:
                            note = "meme ecart (biais stable vs Manar)"
                        elif abs(de) <= 0.1:
                            note = "ecarts deux dates proches"
                        else:
                            note = "ecart change fortement entre dates"
                    else:
                        note = "autre"
                row["delta_ecart_premier_vs_second"] = delta_s
            else:
                row["delta_ecart_premier_vs_second"] = ""
            row["note"] = note
            w.writerow(row)

    # Résumé stdout
    n = len(all_codes)
    print(f"Dates : {dates}")
    print(f"Codes dans le tableau Prix Manar (union) : {n}")
    print(f"CSV écrit : {out_csv}")

    # Petites stats si au moins 2 dates
    if len(dates) >= 2:
        both = 0
        shift = 0
        stable_bias = 0
        for code in all_codes:
            r0 = per_date[dates[0]].get(code)
            r1 = per_date[dates[1]].get(code)
            if not r0 or not r1:
                continue
            e0 = _f(r0.get("ecart_prix_arrondi_valo"))
            e1 = _f(r1.get("ecart_prix_arrondi_valo"))
            if e0 is None or e1 is None:
                continue
            if abs(e0) <= tol and abs(e1) <= tol:
                both += 1
            elif abs(e1 - e0) > 0.1:
                shift += 1
            if abs(e0) > tol and abs(e1) > tol and abs(e1 - e0) <= tol:
                stable_bias += 1
        print(f"Les deux dates dans tolérance ±{tol} : {both}")
        print(f"|delta ecart| > 0.10 entre date1 et date2 : {shift}")
        print(f"Même écart (biais stable) sur les deux dates : {stable_bias}")


if __name__ == "__main__":
    main()
