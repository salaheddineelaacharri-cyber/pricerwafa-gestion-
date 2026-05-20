"""
Ajoute ou complete la colonne « Date de jouissance » dans la feuille **marche**
de maroclear.xlsx.

Priorite :
1) Feuille **PRIME DE RISQUE** (ou toute feuille dont le nom contient « prime ») :
   si une ligne avec le meme CODE possede une date de jouissance renseignee,
   et que cette date est entre la date d’emission et la date d’echeance (marche),
   on la recopie dans marche.
2) Sinon :
   - libelle **ATYP** + date jj/mm/aaaa apres ATYP si entre emission et echeance ;
   - sinon : **jour et mois = date d’echeance**, **annee = annee d’emission ou la suivante** :
     meme calendrier que l’echeance, en choisissant la bonne annee (meme annee que l’emission
     si la date tombe le jour de l’emission ou apres, sinon annee suivante) ; si au-dela de
     l’echeance, repli sur la date d’emission.

Les cellules deja remplies dans marche ne sont pas ecrasees.

Usage :
  python scripts/completer_date_jouissance_prime.py [--file maroclear.xlsx]
  python scripts/completer_date_jouissance_prime.py [--prime-sheet "PRIME DE RISQUE"] [--dry-run]
"""

from __future__ import annotations

import argparse
import calendar
import re
import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd


def _norm_code(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _to_date(v: object) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    ts = pd.to_datetime(v, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return None
    return ts.date()


def _extract_atyp_jouissance(description: str) -> date | None:
    m = re.search(r"ATYP\s+(\d{1,2}/\d{1,2}/\d{4})", description or "", flags=re.IGNORECASE)
    if not m:
        return None
    ts = pd.to_datetime(m.group(1), dayfirst=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def _date_safe(year: int, month: int, day: int) -> date:
    """Construit une date ; si jour trop grand pour le mois (ex. 29/02), adapte au mois."""
    dim = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, dim))


def jouissance_mois_jour_depuis_echeance(date_emission: date, date_echeance: date) -> date:
    """
    Meme jour et mois que la date d'echeance ; annee = annee d'emission ou annee suivante :
    premiere date (jour/mois d'echeance) **a partir de** l'annee d'emission qui n'est pas
    **strictement avant** l'emission ; si cela depasse l'echeance, repli sur l'emission.
    """
    mois, jour = date_echeance.month, date_echeance.day
    y = date_emission.year
    cand = _date_safe(y, mois, jour)
    if cand < date_emission:
        y += 1
        cand = _date_safe(y, mois, jour)
    if cand > date_echeance:
        return date_emission
    return cand


def infer_jouissance_fallback(date_emission: date, date_echeance: date, libelle: str) -> date | None:
    """Prime deja traitee ailleurs : ATYP si valide, sinon regle jour/mois echeance + annee em ou em+1."""
    if date_emission is None or date_echeance is None:
        return None
    atyp = _extract_atyp_jouissance(libelle)
    if atyp is not None and date_emission <= atyp <= date_echeance:
        return atyp
    return jouissance_mois_jour_depuis_echeance(date_emission, date_echeance)


def _col_date_emission(df: pd.DataFrame):
    for c in df.columns:
        s = str(c).lower()
        if ("émission" in s or "emission" in s) and "éch" not in s.lower():
            return c
    raise ValueError("Colonne Date d'emission introuvable dans marche.")


def _col_date_echeance(df: pd.DataFrame):
    for c in df.columns:
        s = str(c).lower()
        if "échéance" in s or "echeance" in s or "echéance" in s:
            return c
    raise ValueError("Colonne Date d'echeance introuvable dans marche.")


def _find_jouissance_col(cols: list) -> str | None:
    for c in cols:
        if "jouis" in str(c).lower():
            return str(c)
    return None


def _find_code_col(cols: list) -> str:
    for c in cols:
        s = str(c).lower()
        if "code" in s or "maroclear" in s:
            return str(c)
    return str(cols[0])


def _find_marche_sheet(sheet_names: list[str]) -> str:
    for n in sheet_names:
        if n.lower().strip() == "marche":
            return n
    raise SystemExit(f"Feuille « marche » introuvable. Feuilles: {sheet_names}")


def _find_prime_sheet(sheet_names: list[str], explicit: str | None) -> str | None:
    if explicit:
        if explicit not in sheet_names:
            raise SystemExit(f"Feuille prime introuvable: {explicit!r}. Feuilles: {sheet_names}")
        return explicit
    scored: list[tuple[int, str]] = []
    for n in sheet_names:
        low = n.lower()
        if "marche" in low:
            continue
        score = 0
        if "prime" in low:
            score += 2
        if "risque" in low:
            score += 1
        if score:
            scored.append((score, n))
    if scored:
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]
    if "Feuil1" in sheet_names:
        return "Feuil1"
    return None


def build_prime_jouissance_map(dfp: pd.DataFrame) -> dict[str, date]:
    """CODE -> date de jouissance (premiere valeur valide par code)."""
    if dfp is None or dfp.empty:
        return {}
    col_code = _find_code_col(list(dfp.columns))
    col_dj = _find_jouissance_col(list(dfp.columns))
    if not col_dj:
        return {}
    out: dict[str, date] = {}
    for _, row in dfp.iterrows():
        c = _norm_code(row[col_code])
        if not c:
            continue
        d = _to_date(row[col_dj])
        if d is None:
            continue
        if c not in out:
            out[c] = d
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Date de jouissance sur feuille marche (prime puis fallback).")
    ap.add_argument("--file", type=Path, default=Path("maroclear.xlsx"))
    ap.add_argument("--prime-sheet", type=str, default=None, help='Ex: "PRIME DE RISQUE"')
    ap.add_argument(
        "--recalculer-tout",
        action="store_true",
        help="Reecrit toutes les dates de jouissance (sinon seules les cellules vides sont remplies).",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = args.file.resolve()
    if not path.exists():
        raise SystemExit(f"Fichier introuvable: {path}")

    xls = pd.ExcelFile(path)
    sh_marche = _find_marche_sheet(xls.sheet_names)
    sh_prime = _find_prime_sheet(xls.sheet_names, args.prime_sheet)

    dfm = pd.read_excel(path, sheet_name=sh_marche, header=0)
    if dfm.empty:
        raise SystemExit("Feuille marche vide.")

    code_col_m = _find_code_col(list(dfm.columns))
    lib_col_m = next(c for c in dfm.columns if "libell" in str(c).lower() or "long" in str(c).lower())
    dem_col = _col_date_emission(dfm)
    dec_col = _col_date_echeance(dfm)

    col_dj_m = _find_jouissance_col(list(dfm.columns))
    if col_dj_m is None:
        col_dj_m = "Date de jouissance"
        dfm[col_dj_m] = pd.NaT

    already = int(pd.to_datetime(dfm[col_dj_m], errors="coerce", dayfirst=True).notna().sum())

    prime_map: dict[str, date] = {}
    if sh_prime:
        try:
            dfp = pd.read_excel(path, sheet_name=sh_prime, header=0)
            prime_map = build_prime_jouissance_map(dfp)
        except Exception as e:
            print(f"Avertissement: lecture feuille prime « {sh_prime} »: {e}")

    from_prime = 0
    from_fallback = 0
    unchanged = 0
    no_dates = 0
    calc_dates: list[object] = []

    recalc = bool(args.recalculer_tout)

    for _, row in dfm.iterrows():
        cur = row[col_dj_m]
        if not recalc and pd.notna(cur) and str(cur).strip() != "":
            calc_dates.append(None)
            unchanged += 1
            continue

        code = _norm_code(row[code_col_m])
        em = _to_date(row.get(dem_col))
        ec = _to_date(row.get(dec_col))
        lib = str(row.get(lib_col_m, "") or "")

        if em is None or ec is None:
            calc_dates.append(None)
            no_dates += 1
            continue

        dj: date | None = None
        src_prime = False
        if code in prime_map:
            cand = prime_map[code]
            if em <= cand <= ec:
                dj = cand
                src_prime = True

        if dj is None:
            dj = infer_jouissance_fallback(em, ec, lib)

        if dj is None:
            calc_dates.append(None)
            no_dates += 1
            continue

        calc_dates.append(datetime(dj.year, dj.month, dj.day))
        if src_prime:
            from_prime += 1
        else:
            from_fallback += 1

    series_calc = pd.to_datetime(calc_dates, errors="coerce", dayfirst=True)
    dfm["_dj_calc"] = series_calc
    if recalc:
        mask = dfm["_dj_calc"].notna()
    else:
        mask = dfm[col_dj_m].isna() & dfm["_dj_calc"].notna()
    n_update = int(mask.sum())

    if not args.dry_run and n_update > 0:
        backup = path.with_name(path.stem + "_backup_avant_jouissance_marche" + path.suffix)
        shutil.copy2(path, backup)
        dfm.loc[mask, col_dj_m] = dfm.loc[mask, "_dj_calc"]
        dfm = dfm.drop(columns=["_dj_calc"], errors="ignore")
        with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            dfm.to_excel(writer, sheet_name=sh_marche, index=False)
        print(f"Sauvegarde: {backup}")
        print(f"Fichier mis a jour: {path}")
    else:
        dfm = dfm.drop(columns=["_dj_calc"], errors="ignore")

    print(f"Feuille marche: {sh_marche!r} | Colonne ajoutee / remplie: {col_dj_m!r}")
    if sh_prime:
        print(f"Feuille prime (reference): {sh_prime!r} | Codes avec date dans prime: {len(prime_map)}")
    else:
        print("Aucune feuille prime detectee: fallback ATYP ou jour/mois echeance (annee em ou em+1).")
    print(f"Deja renseigne dans marche (inchangé): {already}")
    print(f"Completes depuis PRIME (coherentes em/ec): {from_prime}")
    print(f"Completes par fallback (ATYP ou memes jour/mois que echeance): {from_fallback}")
    print(f"Total nouvelles dates ecrites: {n_update}")
    if args.dry_run:
        print("(dry-run, aucun fichier modifie)")


if __name__ == "__main__":
    main()
