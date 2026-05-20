"""
Courbe ZC bootstrap (placeholder projet).

Format attendu par le pipeline:
- COURBE_ZC: dict {maturite_jours: taux_decimal}
  ou
- COURBE_ZC_DF: DataFrame avec colonnes `maturite_jours`, `taux_decimal`

Interpolation (``ZC_FORMULE_EXCEL_TRIZONE``) : même structure que la cellule Excel
``ARRONDI(...; n)`` sur le taux **décimal** (souvent **5** décimales pour retrouver un affichage **2,284 %** ;
avec **4** décimales on obtient **0,0228** → **2,280 %**). Régler ``ZC_SEUIL_G2_JOURS`` sur ``'Courbe des taux'!$G$2``.
"""

COURBE_ZC = {
    1: 0.02270000,
    53: 0.02270000,
    144: 0.02340000,
    326: 0.02460000,
    365: 0.02514317,
    730: 0.02669800,
    1096: 0.02825700,
    1461: 0.02983200,
    1826: 0.03090900,
    2191: 0.03143200,
    2557: 0.03193700,
    2922: 0.03245100,
    3287: 0.03297500,
    3652: 0.03358500,
    4018: 0.03423100,
    4383: 0.03488800,
    4748: 0.03555800,
    5113: 0.03594500,
    5479: 0.03620400,
    5844: 0.03647100,
    6209: 0.03674500,
    6574: 0.03702700,
    6940: 0.03731800,
    7305: 0.03781100,
    7670: 0.03844400,
    8035: 0.03909400,
    8401: 0.03976400,
    8766: 0.04045300,
    9131: 0.04116400,
    9496: 0.04190000,
    9862: 0.04266600,
    10227: 0.04345900,
    10592: 0.04428500,
    10957: 0.04514900,
}

# --- Paramètres interpolation type Excel (voir docstring du module) ---
ZC_FORMULE_EXCEL_TRIZONE: bool = True
ZC_SEUIL_G2_JOURS: float = 365.0
ZC_BASE_CONVERSION: float = 365.0
ZC_ARRONDI_DECIMALES: int = 5
# Arrondi sur le taux secondaire interpolé (Formule B) avant + spread ; ``None`` = pas d’arrondi.
ZC_ARRONDI_TAUX_SECONDAIRE: int | None = 6

COURBE_ZC_COURT: dict[float, float] | None = None
COURBE_ZC_LONG: dict[float, float] | None = None
