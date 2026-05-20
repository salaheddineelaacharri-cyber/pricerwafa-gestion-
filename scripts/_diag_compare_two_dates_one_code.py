"""Diagnostic ponctuel : comparer une valorisation entre deux dates (sans modifier le moteur)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api  # noqa: E402


def norm_code(x: object) -> str:
    s = str(x or "").strip()
    return s[:-2] if s.endswith(".0") else s


def curve_bundle(iso: str) -> tuple[api.CurveRequest, dict]:
    pil = api._extraire_piliers_depuis_histo(ROOT, iso, "MAR_JJ")
    cr = api.CurveRequest(
        short=[api.PillarShort(**p) for p in pil["short"]],
        long=[api.PillarLong(**p) for p in pil["long"]],
        joint_days=float(pil.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )
    return cr, pil


def valorize(iso: str, code: str) -> tuple[dict, dict]:
    cr, pil = curve_bundle(iso)
    req = api.MarcheValorizeRequest(
        valuation_date=iso,
        curve=cr,
        code_maroclear=code,
        prix_manarr_pricer_tous=True,
    )
    res = api.marche_valorize(req)
    raw = res.body
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    return json.loads(raw), pil


def row_by(rows: list, code: str) -> dict | None:
    for r in rows or []:
        if norm_code(r.get("CODE")) == norm_code(code):
            return r
    return None


def tab_by(tabs: list, code: str) -> dict | None:
    for t in tabs or []:
        if norm_code(t.get("code")) == norm_code(code):
            return t
    return None


def pm_by(prix_manarr: list, code: str) -> dict | None:
    for pr in prix_manarr or []:
        if norm_code(pr.get("titre")) == norm_code(code):
            return pr
    return None


def pilier_plus_proche(points: list, target_days: float) -> dict | None:
    best = None
    for p in points:
        d = float(p["maturity_days"])
        if best is None or abs(d - target_days) < abs(float(best["maturity_days"]) - target_days):
            best = p
    return best


def interp_segment(mat_j: float, pil: dict) -> tuple[float | None, float | None, float | None, str]:
    pts = sorted(
        [(float(p["maturity_days"]), float(p["rate_pct"])) for p in pil["points"]],
        key=lambda x: x[0],
    )
    if not pts:
        return None, None, None, "aucun point"
    if mat_j <= pts[0][0]:
        return pts[0][0], pts[0][0], 0.0, "coincide pilier min"
    for i in range(len(pts) - 1):
        d0, r0 = pts[i]
        d1, r1 = pts[i + 1]
        if d0 <= mat_j <= d1:
            w = (mat_j - d0) / (d1 - d0) if d1 != d0 else 0.0
            return d0, d1, w, f"interpolation lineaire taux % entre {d0:.0f}j et {d1:.0f}j"
    return pts[-1][0], pts[-1][0], 0.0, "coincide pilier max"


def flows_future(tab: dict | None) -> list[tuple]:
    if not tab:
        return []
    cols = tab.get("columns") or []
    by_label = {r["label"]: r.get("values") or [] for r in tab.get("rows") or []}
    out: list[tuple] = []
    for i, c in enumerate(cols):
        try:
            fr = float(by_label.get("Flux restant", [0] * len(cols))[i] or 0.0)
        except (TypeError, ValueError):
            fr = 0.0
        if fr <= 0:
            continue
        du = by_label.get("durée", [None] * len(cols))[i]
        tz = by_label.get("Taux ZC", [None] * len(cols))[i]
        pr = by_label.get("Prime", [None] * len(cols))[i]
        ta = by_label.get("Taux d'actualisation", [None] * len(cols))[i]
        fl = by_label.get("Flux", [None] * len(cols))[i]
        fa = by_label.get("Flux actualisé", [None] * len(cols))[i]
        try:
            fac = float(fa) / float(fr) if fr and fa is not None else None
        except (TypeError, ValueError, ZeroDivisionError):
            fac = None
        out.append((c, du, tz, pr, ta, fl, fr, fa, fac))
    return out


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else ""
    d1 = sys.argv[2] if len(sys.argv) > 2 else "2026-03-06"
    d2 = sys.argv[3] if len(sys.argv) > 3 else "2026-03-26"
    if not code:
        print("Usage: python scripts/_diag_compare_two_dates_one_code.py CODE [DATE1] [DATE2]")
        sys.exit(1)

    bundle: dict[str, tuple[dict, dict, dict | None, dict | None, dict | None]] = {}
    for iso in (d1, d2):
        data, pil = valorize(iso, code)
        row = row_by(data.get("rows"), code)
        tab = tab_by(data.get("amortissement_tables"), code)
        pm = pm_by(data.get("prix_manarr"), code)
        bundle[iso] = (data, pil, row, tab, pm)

    def fnum(x, nd=6):
        if x is None:
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return str(x)
        return round(v, nd) if math.isfinite(v) else None

    for iso in (d1, d2):
        data, pil, row, tab, pm = bundle[iso]
        print("=" * 72)
        print(f"DATE VALO: {iso}")
        print(f"COURBE (MAR_JJ) — date utilisée SQL: {pil.get('date_used')}  (demandée: {pil.get('date_requested')})")
        print(f"Source: {pil.get('source_file')}  nom courbe: {pil.get('courbe')}")
        print(f"Nb piliers points: {len(pil.get('points', []))}  joint_days: {pil.get('joint_days')}  split: {pil.get('split_maturity_days')}j")

        pts = pil.get("points") or []
        p5 = pilier_plus_proche(pts, 1825.0)
        p10 = pilier_plus_proche(pts, 3650.0)
        print(f"Pilier proche 5A (~1825j): {p5}")
        print(f"Pilier proche 10A (~3650j): {p10}")

        mj = None
        if row:
            mj = row.get("Maturité résiduelle (jours)")
            if mj is None:
                mj = row.get("Maturite residuelle (jours)")
        mat_j = float(mj) if mj is not None and str(mj).strip() != "" else None
        d0, d1b, w, note = interp_segment(mat_j, pil) if mat_j is not None else (None, None, None, "N/A")
        print(f"Maturité résiduelle (j): {mat_j}")
        print(f"Interpolation sur courbe_taux: {note}  w={fnum(w, 8) if w is not None else None} (poids sur segment haut)")

        if row:
            print(
                "Synthèse ligne marché:",
                json.dumps(
                    {
                        "Prix arrondi (moteur)": fnum(row.get("Prix arrondi"), 4),
                        "Prix clean": fnum(row.get("Prix clean"), 4),
                        "Coupon couru": fnum(row.get("Coupon couru"), 4),
                        "YTM / TRI %": fnum(row.get("Rendement (YTM)"), 5),
                        "Duration": fnum(row.get("Duration titre"), 4),
                        "Sensibilité": fnum(row.get("Sensibilité"), 4),
                        "Convexité": fnum(row.get("Convexité"), 4),
                    },
                    ensure_ascii=False,
                ),
            )
        else:
            print("Ligne marché: ABSENTE")

        if pm:
            valo = pm.get("valo")
            pa = pm.get("prix_arrondi")
            ec = pm.get("ecart_prix_arrondi_valo")
            print(
                "Prix Manar (fichier):",
                json.dumps(
                    {
                        "valo_fichier": fnum(valo, 4),
                        "prix_arrondi_moteur": fnum(pa, 4),
                        "ecart_moteur_moins_fichier": fnum(ec, 4),
                        "source_ecart": pm.get("source_ecart"),
                    },
                    ensure_ascii=False,
                ),
            )
        else:
            print("Prix Manar: pas de ligne fichier pour ce code à cette date")

        if tab:
            print(
                "Table amortissement (agrégats):",
                json.dumps(
                    {
                        "prix_somme_flux_actualises": tab.get("prix_somme_flux_actualises"),
                        "ytm_actuariel": tab.get("ytm_actuariel"),
                        "maturite_residuelle_jours": tab.get("maturite_residuelle_jours"),
                        "coupon_couru_schedule": fnum(tab.get("coupon_couru_schedule"), 6),
                        "methode_valo": tab.get("methode_valo"),
                        "courbe_zc_active": tab.get("courbe_zc_active"),
                    },
                    ensure_ascii=False,
                ),
            )
            print("--- Flux futurs (date, durée, TZC%, prime%, taux act%, flux, flux rest., F act., facteur)")
            for t in flows_future(tab):
                print("  ", t)
        else:
            print("Table amortissement: ABSENTE")

    r1, r2 = bundle[d1][2] or {}, bundle[d2][2] or {}
    t1, t2 = bundle[d1][3], bundle[d2][3]
    print("=" * 72)
    print("COMPARAISON", d2, "moins", d1)
    for label, k in [
        ("Prix moteur", "Prix arrondi"),
        ("Coupon couru", "Coupon couru"),
        ("YTM", "Rendement (YTM)"),
        ("Duration", "Duration titre"),
        ("Sensibilité", "Sensibilité"),
        ("Convexité", "Convexité"),
    ]:
        try:
            a = float(r1.get(k)) if r1.get(k) is not None else None
            b = float(r2.get(k)) if r2.get(k) is not None else None
            if a is not None and b is not None:
                print(f"  {label}: delta = {round(b - a, 6)}  ({a} -> {b})")
            else:
                print(f"  {label}: N/A")
        except (TypeError, ValueError):
            print(f"  {label}: N/A")

    f1, f2 = flows_future(t1), flows_future(t2)
    print(f"Nb flux futurs: {d1}={len(f1)}  {d2}={len(f2)}")
    if len(f1) == len(f2) and f1:
        for i, (a, b) in enumerate(zip(f1, f2)):
            if a[0] != b[0]:
                print(f"  Attention ordre dates colonne {i}: {a[0]} vs {b[0]}")
            diffs = []
            for j, name in enumerate(
                ["", "durée", "TZC", "prime", "taux act", "flux", "flux rest", "F act", "facteur"]
            ):
                if j == 0:
                    continue
                if a[j] != b[j]:
                    try:
                        if abs(float(a[j]) - float(b[j])) > 1e-9:
                            diffs.append((name, a[j], b[j]))
                    except (TypeError, ValueError):
                        diffs.append((name, a[j], b[j]))
            if diffs:
                print(f"  Flux #{i} date {a[0]}: écarts {diffs}")
    print("=" * 72)
    print(
        "CONCLUSION (heuristique): si seuls TZC/taux act/durée/F act changent entre les deux dates, "
        "l'écart Manar vs moteur au 06/03 vient surtout de la courbe BAM du 06/03 (et de la maturité résiduelle). "
        "Si coupon couru ou flux nominal diffèrent, vérifier aussi la position dans l'année coupon."
    )


if __name__ == "__main__":
    main()
