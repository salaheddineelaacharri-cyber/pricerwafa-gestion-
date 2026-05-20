"""
Test d'hypothèse : frontière flux (strict > vs inclusif >=) pour FPCT / ZC / TRI.

Recalcule uniquement les codes ciblés via ``marche_valorize`` (prix Manar tous),
en basculant ``backend.main._obl_amort_mod.USE_INCLUSIVE_CASHFLOW_DATE``.

Le flag est toujours remis à False en fin de script.

Usage (racine projet) :
  python scripts/test_cashflow_boundary_fpct.py

Sortie : tableau Markdown sur stdout + écriture ``reports/test_cashflow_boundary_fpct.md``.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api  # noqa: E402

CODES = ["5061", "5107", "5116", "5117", "5122", "5151"]
DATES = ["2026-03-06", "2026-03-26"]
TOL = 0.02
TOL_STABLE_PRIX = 0.02
TOL_STABLE_ECART = 0.02
ISO_26 = "2026-03-26"


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


def _valorize(root: Path, iso: str, *, inclusive: bool) -> list[dict]:
    mod = api._obl_amort_mod
    prev = bool(getattr(mod, "USE_INCLUSIVE_CASHFLOW_DATE", False))
    mod.USE_INCLUSIVE_CASHFLOW_DATE = inclusive
    try:
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
    finally:
        mod.USE_INCLUSIVE_CASHFLOW_DATE = prev


def _row_by_code(rows: list[dict]) -> dict[str, dict]:
    return {_norm_code(r.get("titre")): r for r in rows if _norm_code(r.get("titre"))}


def _f(x: object) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def main() -> int:
    root = ROOT
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "test_cashflow_boundary_fpct.md"
    mod = api._obl_amort_mod
    if not hasattr(mod, "USE_INCLUSIVE_CASHFLOW_DATE"):
        msg = (
            "# Test frontière flux — obsolète\n\n"
            "Le moteur `obligation_amort_schedule` n’expose plus le flag "
            "`USE_INCLUSIVE_CASHFLOW_DATE` (comportement strict `>` restauré).\n"
        )
        out_path.write_text(msg, encoding="utf-8")
        print(msg)
        return 0

    # Données : (date, mode) -> map code -> row
    cache: dict[tuple[str, bool], dict[str, dict]] = {}
    err: str | None = None
    try:
        for iso in DATES:
            for inclusive in (False, True):
                rows = _valorize(root, iso, inclusive=inclusive)
                cache[(iso, inclusive)] = _row_by_code(rows)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        if hasattr(mod, "USE_INCLUSIVE_CASHFLOW_DATE"):
            mod.USE_INCLUSIVE_CASHFLOW_DATE = False

    lines: list[str] = []
    lines.append("# Test frontière flux (FPCT / ZC / TRI — hypothèse `>` vs `>=`)\n")
    lines.append("")
    lines.append("## Paramètres\n")
    lines.append(f"- Codes : {', '.join(CODES)}")
    lines.append(f"- Dates : {', '.join(DATES)}")
    lines.append(
        f"- Stabilité 26/03 (inclusif vs strict, même date validée) : "
        f"|prix_inc - prix_str| < {TOL_STABLE_PRIX} et |ecart_inc - ecart_str| < {TOL_STABLE_ECART}."
    )
    lines.append(f"- Reference ecart Manar (indicatif) : |ecart| <= {TOL} comme dans l'API prix Manar.")
    lines.append("- Mode **strict** : `USE_INCLUSIVE_CASHFLOW_DATE = False` (défaut).")
    lines.append("- Mode **inclusif** : `USE_INCLUSIVE_CASHFLOW_DATE = True` (expérimental).")
    lines.append("")
    lines.append("## Mode C — date de règlement\n")
    lines.append(
        "Dans ``construire_tableau_amortissement``, les colonnes PV utilisent ``L['date']`` "
        "issue de l’échéancier long ; il n’y a **pas** aujourd’hui de liste parallèle "
        "« tombée vs règlement » par colonne. Un test `date_flux > date_reglement` distinct "
        "de la valorisation n’est **pas câblé** sans enrichir les lignes d’échéancier."
    )
    lines.append("")

    ok_26_by_code: dict[str, bool | None] = {c: None for c in CODES}

    if err:
        lines.append("## Erreur d’exécution\n")
        lines.append(f"```\n{err}\n```\n")
        lines.append("")
        lines.append("## Tableau comparatif\n")
        lines.append("*Non généré (valorisation indisponible).*")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        msg = "\n".join(lines) + "\n"
        print(msg.encode(enc, errors="replace").decode(enc, errors="replace"))
        return 1

    header = (
        "| code | date_valo | prix_ref (valo) | prix strict | ecart strict | "
        "prix inclusif | ecart inclusif | Delta ecart (inc-str) | 26/03 stable (inc vs str) |"
    )
    sep = "|" + "|".join(["---"] * 9) + "|"
    lines.append("## Tableau comparatif\n")
    lines.append(header)
    lines.append(sep)

    for iso in DATES:
        m_s = cache.get((iso, False), {})
        m_i = cache.get((iso, True), {})
        for code in CODES:
            r_s = m_s.get(code)
            r_i = m_i.get(code)
            valo = _f(r_s.get("valo")) if r_s else float("nan")
            if r_s is None and r_i is not None:
                valo = _f(r_i.get("valo"))
            p_s = _f(r_s.get("prix_arrondi")) if r_s else float("nan")
            e_s = _f(r_s.get("ecart_prix_arrondi_valo")) if r_s else float("nan")
            p_i = _f(r_i.get("prix_arrondi")) if r_i else float("nan")
            e_i = _f(r_i.get("ecart_prix_arrondi_valo")) if r_i else float("nan")
            d_e = e_i - e_s if math.isfinite(e_i) and math.isfinite(e_s) else float("nan")

            if iso == ISO_26:
                ok_26_by_code[code] = bool(
                    math.isfinite(p_s)
                    and math.isfinite(p_i)
                    and abs(p_i - p_s) < TOL_STABLE_PRIX
                    and math.isfinite(e_s)
                    and math.isfinite(e_i)
                    and abs(e_i - e_s) < TOL_STABLE_ECART
                )

            ok_col = ""
            if iso != ISO_26:
                ok_col = "-"
            elif ok_26_by_code[code] is None:
                ok_col = "n/a"
            else:
                ok_col = "oui" if ok_26_by_code[code] else "non"

            def fmt(x: float, nd: int = 4) -> str:
                if not math.isfinite(x):
                    return "n/a"
                return f"{x:.{nd}f}"

            lines.append(
                f"| {code} | {iso} | {fmt(valo, 4)} | {fmt(p_s, 4)} | {fmt(e_s, 2)} | "
                f"{fmt(p_i, 4)} | {fmt(e_i, 2)} | {fmt(d_e, 2)} | {ok_col} |"
            )

    lines.append("")
    lines.append("## Synthèse automatique (indicative)\n")

    def abs_es_06(c: str) -> float:
        r = cache.get(("2026-03-06", False), {}).get(c)
        if not r:
            return float("nan")
        return abs(_f(r.get("ecart_prix_arrondi_valo")))

    def abs_ei_06(c: str) -> float:
        r = cache.get(("2026-03-06", True), {}).get(c)
        if not r:
            return float("nan")
        return abs(_f(r.get("ecart_prix_arrondi_valo")))

    improved_06 = [c for c in CODES if math.isfinite(abs_es_06(c)) and math.isfinite(abs_ei_06(c)) and abs_ei_06(c) < abs_es_06(c) - 1e-9]
    worsened_06 = [c for c in CODES if math.isfinite(abs_es_06(c)) and math.isfinite(abs_ei_06(c)) and abs_ei_06(c) > abs_es_06(c) + 1e-9]

    all_26_stable = all(ok_26_by_code.get(c) is True for c in CODES)
    unstable_26 = [c for c in CODES if ok_26_by_code.get(c) is False]

    lines.append(
        f"- **06/03** : codes avec |ecart| strictement reduit en inclusif : {', '.join(improved_06) or 'aucun'} ; "
        f"aggravés : {', '.join(worsened_06) or 'aucun'}."
    )
    lines.append(
        f"- **26/03 stabilite (prix/ecart inclusif vs strict)** : "
        f"{'tous stables' if all_26_stable else 'instables : ' + ', '.join(unstable_26)}."
    )
    lines.append("")
    lines.append("## Conclusion métier (dérivée du tableau ci-dessus)\n")
    if improved_06:
        lines.append(
            "- **Hypothèse `>` strict sur le 06/03** : partiellement soutenue : "
            f"|écart| diminue en inclusif pour {', '.join(improved_06)}."
        )
    elif worsened_06:
        lines.append(
            "- **Hypothèse `>` strict sur le 06/03** : le mode inclusif **dégrade** l'écart au 06/03 pour "
            f"{', '.join(worsened_06)} (contre-intuitif ; à analyser)."
        )
    else:
        lines.append(
            "- **Hypothèse `>` strict sur le 06/03** : sur cet échantillon, le passage en `>=` "
            "ne modifie ni le prix ni l'écart au 06/03 : la seule relaxation `>` / `>=` testée ici "
            "n'explique pas à elle seule les grands écarts du 06/03 pour ces titres."
        )
    if all_26_stable:
        lines.append(
            "- **Passage en `>=` au 26/03** : stable vs strict sur tous les codes testés ; "
            "une généralisation mériterait toutefois un périmètre élargi."
        )
    else:
        lines.append(
            f"- **Passage en `>=` au 26/03** : **régression** pour au moins un code "
            f"({', '.join(unstable_26)}) : **ne pas** activer le flag globalement sans analyse ciblée."
        )
    lines.append(
        "- **Date de règlement (mode C)** : non testée en machine : pas de colonne « règlement » "
        "distincte par flux dans la grille PV actuelle (voir section Mode C)."
    )
    lines.append(
        "- **Recommandation** : conserver le comportement historique (`>`) par défaut ; "
        "si le métier veut creuser `>=`, isoler les profils concernés et traiter les cas "
        "où une tombée coïncide avec la valorisation (effet prix matériel, ex. 5122 au 26/03)."
    )

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(enc, errors="replace").decode(enc, errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
