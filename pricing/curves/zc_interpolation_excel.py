"""
Interpolation des taux ZC alignée sur Excel / VBA.

- ``interpoler`` (VBA) : première maille de maturité >= m, interpolation linéaire ;
  en-deçà du premier pilier → premier taux ; au-delà du dernier → dernier taux.
- Formule type cellule (SI / ARRONDI) ::

    SI(K>365 ; interpoler(_mat2;taux2;K) ;
      SI(K<=G2 ; interpoler(_mat1;taux1;K) ;
        interpoler(_mat2 ; taux2_convertis_MM ; K)))

  puis ARRONDI(..., ndigits) sur le taux **décimal** (souvent **5** pour un % type **2,284**).
"""

from __future__ import annotations

import numpy as np

# Arrondi du taux secondaire Formule B (décimal) après interpolation, avant « Taux AA » + prime
# (aligné ``ZC_ARRONDI_TAUX_SECONDAIRE`` / classeur WG ; évite 2,356 % vs 2,357 % sur le même K).
NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT: int = 6


def _dict_to_sorted_arrays(d: dict[float, float]) -> tuple[np.ndarray, np.ndarray]:
    xs = sorted(float(k) for k in d.keys())
    if not xs:
        raise ValueError("Courbe ZC vide.")
    return np.array(xs, dtype=float), np.array([float(d[x]) for x in xs], dtype=float)


def vba_interpolate(maturites: np.ndarray, taux: np.ndarray, m: float) -> float:
    """
    Équivalent de la fonction VBA ``interpoler`` (colonnes maturité / taux, m en jours).
    """
    mats = np.asarray(maturites, dtype=float)
    taus = np.asarray(taux, dtype=float)
    if mats.size == 0:
        raise ValueError("interpoler : maturités vides.")
    o = np.argsort(mats)
    mats, taus = mats[o], taus[o]
    m = float(m)
    idx = int(np.searchsorted(mats, m, side="left"))
    if idx == 0:
        return float(taus[0])
    if idx >= len(mats):
        return float(taus[-1])
    m0, m1 = mats[idx - 1], mats[idx]
    t0, t1 = taus[idx - 1], taus[idx]
    if abs(m1 - m0) < 1e-15:
        return float(t1)
    return float((t1 - t0) / (m1 - m0) * (m - m0) + t0)


def vba_interpolate_extrapolate(maturites: np.ndarray, taux: np.ndarray, m: float) -> float:
    """
    Interpolation Excel avec extrapolation linéaire aux bornes.

    Le classeur ``2026-PRICER_WG_CORRIGE`` utilise cette convention sur la courbe longue
    (ex. maturités 30 ans au-delà du dernier pilier BAM).
    """
    mats = np.asarray(maturites, dtype=float)
    taus = np.asarray(taux, dtype=float)
    if mats.size == 0:
        raise ValueError("interpoler : maturités vides.")
    o = np.argsort(mats)
    mats, taus = mats[o], taus[o]
    x = float(m)
    if mats.size == 1:
        return float(taus[0])
    if x <= float(mats[0]):
        m0, m1 = float(mats[0]), float(mats[1])
        t0, t1 = float(taus[0]), float(taus[1])
    elif x >= float(mats[-1]):
        m0, m1 = float(mats[-2]), float(mats[-1])
        t0, t1 = float(taus[-2]), float(taus[-1])
    else:
        return vba_interpolate(mats, taus, x)
    if abs(m1 - m0) < 1e-15:
        return t1
    return float((t1 - t0) / (m1 - m0) * (x - m0) + t0)


def _taux_actuariel_vers_monetaire(r_actuariel: float, jours: float) -> float:
    """Convertit un taux actuariel en taux monétaire équivalent sur ``jours``."""
    j = float(jours)
    r = float(r_actuariel)
    if j <= 0.0:
        return r
    return float(((1.0 + r) ** (j / 365.0) - 1.0) * (360.0 / j))


def taux_zc_cellule_excel_trizone(
    k_jours: float,
    courbe_court: dict[float, float],
    courbe_long: dict[float, float],
    *,
    seuil_g2: float,
    base: float = 365.0,
    ndigits: int | None = 5,
) -> float:
    """
    Reproduit la logique Excel (taux en décimal, ex. 0,0227 pour 2,27 %).

    ``seuil_g2`` : même rôle que ``'Courbe des taux'!$G$2`` (jours).
    ``base`` : nom de la variable Excel ``base`` dans ``^(K64/base)``.
    """
    mx, tx = _dict_to_sorted_arrays(courbe_court)
    my, ty = _dict_to_sorted_arrays(courbe_long)
    k = float(k_jours)
    if k > 365.0:
        r = vba_interpolate_extrapolate(my, ty, k)
    elif k <= float(seuil_g2):
        r = vba_interpolate(mx, tx, k)
    else:
        r_long = vba_interpolate_extrapolate(my, ty, k)
        if k <= 0:
            r = r_long
        else:
            r = ((1.0 + r_long) ** (k / float(base)) - 1.0) * (360.0 / k)
    if ndigits is not None:
        r = round(float(r) + 1e-15, int(ndigits))
    return float(r)


def taux_secondaire_interpole_formule_b(
    k_jours: float,
    courbe_court: dict[float, float],
    courbe_long: dict[float, float],
    *,
    ndigits: int | None = NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT,
) -> float:
    """
    « Taux secondaire interpolé » (Formule B, grille BAM) :

    - ``K >= 365`` : ``interpoler`` sur la grille **long terme** (taux actuariels), **1 an inclus**
      (alignement Manar : à 365 j le secondaire suit la LT, pas l’extrapolation CT 326→543).
    - ``K < 365`` : **court terme** (MM). Dans la zone de transition aprÃ¨s le dernier pilier
      court terme, les piliers LT encadrants sont d'abord convertis en monÃ©taire, puis interpolÃ©s
      en monÃ©taire (pas interpolation actuarielle puis conversion Ã  K).

    En valorisation : **YTM** = ce taux + prime de risque (décimal).
    """
    mx, tx = _dict_to_sorted_arrays(courbe_court)
    my, ty = _dict_to_sorted_arrays(courbe_long)
    k = float(k_jours)
    if k > 365.0:
        r = vba_interpolate_extrapolate(my, ty, k)
    elif k <= float(mx[-1]):
        r = vba_interpolate(mx, tx, k)
    else:
        ty_mm = np.array(
            [_taux_actuariel_vers_monetaire(float(r_i), float(d_i)) for d_i, r_i in zip(my, ty)],
            dtype=float,
        )
        r = vba_interpolate_extrapolate(my, ty_mm, k)
    if ndigits is not None:
        r = round(float(r) + 1e-15, int(ndigits))
    return float(r)
