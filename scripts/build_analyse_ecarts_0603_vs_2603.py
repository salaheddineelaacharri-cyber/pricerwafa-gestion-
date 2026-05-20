"""
Construit ``reports/analyse_ecarts_0603_vs_2603.md`` : comparaison légère Prix Manar
(calculé vs valo référence) pour 2026-03-26 (base acceptée) et 2026-03-06.

Prérequis : même environnement que ``marche_valorize`` (SQL BAM, Excel, ``prix mar.xlsx``
avec les deux dates si possible).

Usage :
  python scripts/build_analyse_ecarts_0603_vs_2603.py
"""
from __future__ import annotations

import json
import math
import sys
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "analyse_ecarts_0603_vs_2603.md"

D_REF = "2026-03-26"
D_PROB = "2026-03-06"
TOL = 0.02
INCOHERENCE_DELTA = 0.10

sys.path.insert(0, str(ROOT))


def _norm_code(v: object) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _curve_for_date(api: Any, root: Path, iso: str) -> Any:
    pillars = api._extraire_piliers_depuis_histo(root, iso, "MAR_JJ")
    return api.CurveRequest(
        short=[api.PillarShort(**p) for p in pillars["short"]],
        long=[api.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )


def _valorize(api: Any, root: Path, iso: str) -> list[dict]:
    req = api.MarcheValorizeRequest(
        valuation_date=iso,
        curve=_curve_for_date(api, root, iso),
        prix_manarr_pricer_tous=True,
    )
    res = api.marche_valorize(req)
    body = res.body if hasattr(res, "body") else res
    if isinstance(body, (bytes, str)):
        data = json.loads(body)
    else:
        data = dict(body)
    return list(data.get("prix_manarr") or [])


def _f(v: object) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


def _statut_26(_ecart: float | None) -> str:
    return "acceptable (date de référence validée par l’utilisateur)"


def _statut_06(ecart: float | None) -> str:
    if ecart is None:
        return "N/A (donnée manquante)"
    if abs(ecart) <= TOL:
        return "acceptable"
    return "grand écart"


def _groupe_cause(profil: str) -> str:
    p = (profil or "").upper()
    if "BSF" in p:
        return "Profil BSF : vérifier courbe / date de courbe, coupon couru, flux proches de la valorisation."
    if "FPCT" in p or "TRI" in p:
        return "FPCT / périodicité courte : vérifier durées ZC (premier pas), filtrage des flux, cohérence TRI."
    if "REV" in p:
        return "REV : vérifier taux révisé, branche ZC vs AA, flux résiduels."
    if "ZC" in p:
        return "ZC : vérifier échéancier ZC UI vs maturité en jours, spread, annualisation secondaire."
    if "AMORT" in p or "AMORTISS" in p:
        return "Amortissable : vérifier chaîne d’amortissement SQL vs FEC, intérêts pleine précision PV."
    return "Générique : données SQL, date de valorisation titre, courbe BAM du jour."


def main() -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# Analyse des écarts de prix — 06/03/2026 vs 26/03/2026",
        "",
        f"_Rapport généré automatiquement le {gen}._",
        "",
        "## 1. Résumé global",
        "",
    ]

    err: str | None = None
    rows_ref: list[dict] = []
    rows_prob: list[dict] = []
    try:
        from backend import main as api  # noqa: E402

        rows_ref = _valorize(api, ROOT, D_REF)
        rows_prob = _valorize(api, ROOT, D_PROB)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    if err:
        lines.extend(
            [
                f"**Erreur lors de la valorisation** : `{err}`",
                "",
                "Sans exécution réussie de l’API interne, les tableaux chiffrés ne peuvent pas être remplis. "
                "Vérifier la connexion SQL, les fichiers Excel (référentiel, ``prix mar.xlsx`` avec les dates "
                "demandées) puis relancer :",
                "",
                "```text",
                "python scripts/build_analyse_ecarts_0603_vs_2603.py",
                "```",
                "",
                f"## 2. Tableau — {D_REF} (référence)",
                "",
                "_Non généré (valorisation indisponible)._",
                "",
                f"## 3. Tableau — {D_PROB}",
                "",
                "_Non généré (valorisation indisponible)._",
                "",
            ]
        )
        lines.extend(_static_sections())
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        print("written", REPORT, "(avec erreur valorisation)")
        return

    m_ref = {_norm_code(r.get("titre")): r for r in rows_ref if _norm_code(r.get("titre"))}
    m_prob = {_norm_code(r.get("titre")): r for r in rows_prob if _norm_code(r.get("titre"))}
    codes = sorted(set(m_ref) & set(m_prob))

    grand_06: list[str] = []
    incoh: list[str] = []
    for c in codes:
        e_ref = _f(m_ref[c].get("ecart_prix_arrondi_valo"))
        e06 = _f(m_prob[c].get("ecart_prix_arrondi_valo"))
        if e06 is not None and abs(e06) > TOL:
            grand_06.append(c)
        if e06 is not None and e_ref is not None:
            if abs(e06) > TOL and abs(e_ref) <= TOL and abs(e06 - e_ref) > INCOHERENCE_DELTA:
                incoh.append(c)
            elif abs(e06) > TOL and abs(e06 - e_ref) > INCOHERENCE_DELTA:
                incoh.append(c)

    incoh = sorted(set(incoh))
    grand_06 = sorted(set(grand_06))

    lines.extend(
        [
            f"- **Codes comparés sur les deux dates** : {len(codes)}",
            f"- **Titres avec écart absolu > {TOL} au {D_PROB}** : {len(grand_06)}",
            f"- **Titres jugés incohérents avec la base {D_REF}** "
            f"(écart large au 06/03 alors que la base reste proche de zéro, ou forte variation d’écart) : "
            f"{len(incoh)}",
            "",
            f"Seuil « grand écart » au 06/03 : **|écart| > {TOL}** (cohérent avec la tolérance ±0,02 du pricer). "
            f"Seuil d’**incohérence entre dates** : **|écart(06/03) − écart(26/03)| > {INCOHERENCE_DELTA}** "
            "avec écart significatif au 06/03.",
            "",
            "**Règle de statut** : au **26/03/2026**, tous les écarts affichés sont qualifiés "
            "**acceptables** (référence utilisateur), sans tentative de correction. "
            f"Au **{D_PROB}**, le statut « grand écart » suit uniquement le seuil ci-dessus.",
            "",
        ]
    )

    def row_cells(c: str, m: dict[str, dict]) -> list[str]:
        r = m[c]
        refp = _f(r.get("valo"))
        calc = _f(r.get("prix_arrondi"))
        ec = _f(r.get("ecart_prix_arrondi_valo"))
        typ = str(r.get("profil_metier") or "")
        ref_s = f"{refp:.4f}" if refp is not None else ""
        calc_s = f"{calc:.4f}" if calc is not None else ""
        ec_s = f"{ec:+.4f}" if ec is not None else ""
        abs_s = f"{abs(ec):.4f}" if ec is not None else ""
        return [c, typ, ref_s, calc_s, ec_s, abs_s]

    lines.append(f"## 2. Tableau — {D_REF} (référence)")
    lines.append("")
    h = ["code titre", "type (profil)", "prix référence (valo)", "prix calculé", "écart", "écart abs.", "statut"]
    trows = []
    for c in codes:
        cells = row_cells(c, m_ref)
        e = _f(m_ref[c].get("ecart_prix_arrondi_valo"))
        trows.append(cells + [_statut_26(e)])
    lines.append(_md_table(h, trows))

    lines.append(f"## 3. Tableau — {D_PROB}")
    lines.append("")
    trows2 = []
    for c in codes:
        cells = row_cells(c, m_prob)
        e = _f(m_prob[c].get("ecart_prix_arrondi_valo"))
        trows2.append(cells + [_statut_06(e)])
    lines.append(_md_table(h, trows2))

    lines.append("## 4. Titres avec grands écarts au 06/03/2026")
    lines.append("")
    if not grand_06:
        lines.append("_Aucun titre ne dépasse le seuil au 06/03 sur l’intersection des codes._")
        lines.append("")
    else:
        lines.append(_md_table(h, [row_cells(c, m_prob) + [_statut_06(_f(m_prob[c].get("ecart_prix_arrondi_valo")))] for c in grand_06]))
        lines.append("")

    lines.append("## 5. Comparaison 26/03 vs 06/03 pour les codes problématiques au 06/03")
    lines.append("")
    comp_h = [
        "code",
        "type 26/03",
        "type 06/03",
        "écart 26/03",
        "écart 06/03",
        "Δ écart",
        "remarque",
    ]
    comp_rows: list[list[str]] = []
    for c in grand_06:
        r1, r2 = m_ref[c], m_prob[c]
        e1 = _f(r1.get("ecart_prix_arrondi_valo"))
        e2 = _f(r2.get("ecart_prix_arrondi_valo"))
        p1 = str(r1.get("profil_metier") or "")
        p2 = str(r2.get("profil_metier") or "")
        de = (e2 - e1) if (e1 is not None and e2 is not None) else None
        note = ""
        if p1 != p2:
            note = "profil métier différent entre les deux dates"
        elif e1 is not None and abs(e1) <= TOL and e2 is not None and abs(e2) > TOL:
            note = "écart faible au 26/03, fort au 06/03"
        elif de is not None and abs(de) > INCOHERENCE_DELTA:
            note = "forte variation d’écart entre les deux dates"
        comp_rows.append(
            [
                c,
                p1,
                p2,
                f"{e1:+.4f}" if e1 is not None else "",
                f"{e2:+.4f}" if e2 is not None else "",
                f"{de:+.4f}" if de is not None else "",
                note,
            ]
        )
    lines.append(_md_table(comp_h, comp_rows))

    lines.append("## 6. Causes probables par groupe (heuristique)")
    lines.append("")
    by_g: dict[str, list[str]] = {}
    for c in grand_06:
        g = _groupe_cause(str(m_prob[c].get("profil_metier") or ""))
        by_g.setdefault(g, []).append(c)
    for g, cs in sorted(by_g.items(), key=lambda x: -len(x[1])):
        lines.append(f"- **{g}**")
        lines.append(f"  - Codes : {', '.join(cs[:40])}" + (" …" if len(cs) > 40 else ""))
    lines.append("")

    lines.extend(
        [
            "## 7. Fichiers / zones de code à inspecter (sans modification appliquée ici)",
            "",
            "- ``obligation_amort_schedule.py`` : construction des flux, durées, branches ZC/AA/REV/FPCT, coupon couru.",
            "- ``backend/main.py`` : lecture Prix Manar, valorisation slice, courbes BAM.",
            "- Modules appelés par ``_valoriser_slice_feuil1_batch`` / pricer obligation selon votre arborescence.",
            "",
            "## 8. Corrections proposées (non appliquées)",
            "",
            "1. Pour les écarts **soudains** au 06/03 avec base correcte au 26/03 : comparer les **jeux de données SQL** "
            "(échéancier, coupon, nominal) entre les deux dates de valorisation, et la **courbe** effectivement chargée.",
            "2. Pour **FPCT / TRI** : vérifier le premier pas de durée et l’alignement avec l’échéancier ZC.",
            "3. Pour **BSF** : vérifier la **date de courbe**, le **coupon couru** et l’inclusion du **premier flux** après valorisation.",
            "4. Éviter toute **correction par code** ou **hardcode de date** ; traiter par règle métier générique une fois la cause identifiée.",
            "",
            "## 9. Méthodologie et interdits respectés",
            "",
            "- **Aucun hardcode** par code titre ou par date dans le script de ce rapport.",
            "- **Aucune correction** du prix final, **aucun ajustement** pour forcer l’alignement Manar.",
            "- **Aucun export JSON volumineux** : seule cette synthèse Markdown.",
            "",
        ]
    )

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("written", REPORT)


def _static_sections() -> list[str]:
    """Contenu méthodologique si la valorisation n’a pas pu s’exécuter (ex. SQL indisponible)."""
    return [
        "## 4. Titres avec grands écarts au 06/03/2026",
        "",
        "_Non calculé : relancer le script lorsque SQL Server et les classeurs sont disponibles._",
        "",
        "## 5. Comparaison 26/03 vs 06/03 pour les codes problématiques au 06/03",
        "",
        "Lorsque les données sont disponibles, pour chaque code retenu au §4 :",
        "",
        "- **Même type ?** : comparer ``profil_metier`` entre les deux réponses ``prix_manarr``.",
        "- **Même logique ?** : si le profil diverge, chercher une différence de ligne référentiel / SQL entre dates.",
        "- **Même source SQL ?** : même nombre de lignes d’échéancier, mêmes dates de tombées, mêmes montants.",
        "- **Même méthode de courbe ?** : vérifier ``METHODE_VALO`` / ZC vs AA et la courbe effective (piliers du jour).",
        "- **Même règle coupon couru ?** : position par rapport à la prochaine tombée de coupon.",
        "- **Même nombre de flux futurs ?** : comparer les colonnes strictement postérieures à la date de valorisation.",
        "- **Même famille de calcul ?** : BSF / FPCT / REV / ZC / amortissable (branches dans ``construire_tableau_amortissement``).",
        "",
        "## 6. Causes probables par groupe de titres (checklist)",
        "",
        "1. **Courbe de taux** : mauvais fichier d’historique ou mauvaise date de jointure CT/LT.",
        "2. **Date de courbe** : valorisation avec piliers d’un jour alors que les flux attendent un autre contexte.",
        "3. **Coupon couru** : décalage d’un jour sur la prochaine tombée ou convention ACT.",
        "4. **Filtrage des flux futurs** : flux à la date de valorisation inclus/exclu à tort.",
        "5. **Flux proche de la valorisation** : premier flux post-valo mal actualisé (durée nulle vs fraction).",
        "6. **Par type** : BSF (spread + secondaire), FPCT (durées TRI + ZC), REV (révision), ZC (échéancier), amortissable (FEC vs formule).",
        "7. **Données SQL** : ligne absente ou modifiée entre le 06/03 et le 26/03 (snapshot différent).",
        "",
        "## 7. Fichiers / fonctions suspectes (inspection ciblée, sans changement ici)",
        "",
        "- ``obligation_amort_schedule.py`` — ``construire_tableau_amortissement`` : flux, durées, ZC/AA/REV.",
        "- ``backend/main.py`` — ``_valoriser_prix_manarr_rows``, ``marche_valorize``, lecture ``prix mar.xlsx``.",
        "- ``valuation_zc_obligations.py`` / modules pricing appelés depuis la valorisation slice.",
        "",
        "## 8. Corrections proposées (non appliquées dans ce livrable)",
        "",
        "- Corriger la **cause racine** (données ou règle générique), pas le prix affiché.",
        "- Éviter tout **patch par code titre** ou **par date** dans le moteur.",
        "- Après identification, ajouter des **tests de régression** sur deux dates (06/03 vs 26/03) si pertinent.",
        "",
        "## 9. Confirmation méthodologique",
        "",
        "- Ce rapport et le script associé **n’utilisent aucun hardcode** par code titre ni par date pour forcer un prix.",
        "- **Aucune correction** du prix final ni **ajustement** pour coller artificiellement à Manar.",
        "",
    ]


if __name__ == "__main__":
    main()
