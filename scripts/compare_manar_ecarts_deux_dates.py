"""
Compare les écarts « Prix arrondi − valo Manar » entre deux dates de valorisation.

Usage (depuis la racine du projet) :
  python scripts/compare_manar_ecarts_deux_dates.py 2026-03-26 2026-03-06

Prérequis :
  - SQL BAM + référentiel comme pour la valorisation habituelle ;
  - ``prix mar.xlsx`` avec une ligne par (titre, date) pour **chaque** date comparée,
    sinon le fichier retombe sur ``prix manarrr.xlsx`` (souvent une seule date) et
    les écarts du 06/03 ne sont **pas** comparables au 26/03 (référence Manar fausse).

Sortie : codes où |écart(d1) − écart(d2)| > 0,02 (pattern d’écart différent).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api


def _norm_code(v: object) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _curve_for_date(root: Path, iso: str) -> api.CurveRequest:
    pillars = api._extraire_piliers_depuis_histo(root, iso, "MAR_JJ")
    return api.CurveRequest(
        short=[api.PillarShort(**p) for p in pillars["short"]],
        long=[api.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )


def _valorize(root: Path, iso: str) -> list[dict]:
    req = api.MarcheValorizeRequest(
        valuation_date=iso,
        curve=_curve_for_date(root, iso),
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
    d1 = sys.argv[1] if len(sys.argv) > 1 else "2026-03-26"
    d2 = sys.argv[2] if len(sys.argv) > 2 else "2026-03-06"
    root = ROOT
    print(f"Valorisation Prix Manar : {d1} puis {d2} …", flush=True)
    rows1 = _valorize(root, d1)
    rows2 = _valorize(root, d2)
    m1 = {_norm_code(r.get("titre")): r for r in rows1 if _norm_code(r.get("titre"))}
    m2 = {_norm_code(r.get("titre")): r for r in rows2 if _norm_code(r.get("titre"))}
    codes = sorted(set(m1) & set(m2))
    if not codes:
        print("Aucun code commun entre les deux réponses prix_manarr.")
        return

    diff_pattern: list[tuple[str, float, float, float]] = []
    ok_both = 0
    for c in codes:
        e1 = m1[c].get("ecart_prix_arrondi_valo")
        e2 = m2[c].get("ecart_prix_arrondi_valo")
        f1 = float(e1) if e1 is not None else float("nan")
        f2 = float(e2) if e2 is not None else float("nan")
        if abs(f1 - f2) > 0.02:
            diff_pattern.append((c, f1, f2, f1 - f2))

        if math.isfinite(f1) and math.isfinite(f2) and abs(f1) <= 0.02 and abs(f2) <= 0.02:
            ok_both += 1

    print(f"Codes communs : {len(codes)} | tolerance +/-0.02 sur les deux dates : {ok_both}")
    print(f"Codes avec |ecart({d1}) - ecart({d2})| > 0.02 : {len(diff_pattern)}")
    diff_pattern.sort(key=lambda t: abs(t[3]), reverse=True)
    for c, e1, e2, delta in diff_pattern[:80]:
        p1 = m1[c].get("profil_metier")
        p2 = m2[c].get("profil_metier")
        print(f"  {c}  ecart {d1}={e1:+.2f}  ecart {d2}={e2:+.2f}  delta={delta:+.2f}  profils {p1!s} / {p2!s}")


if __name__ == "__main__":
    main()
