# Diagnostic hybride CT / LT — CODE **9606**

Script : `scripts/diag_9606_hybrid_ct_lt.py` (hors production, aucune modification du moteur).

Si une capture Manar indiquait un écart (ex. +0,81 pt) alors que ce rapport montre ~0 sur ``prix_somme_flux_actualises``, comparer la **même colonne** côté Manar (clean vs dirty) et la **version du moteur**.

**Scénarios** :
- **A** : `taux_secondaire_interpole_formule_b` (logique actuelle pricing).
- **B** : interpolation **linéaire** sur la colonne **Taux** (MM) de l’échéancier ZC tracé (`_schedule_table_records`).
- **C** : zone ]G2 ; 365[ : **extrapolation CT seule** (`vba_interpolate_extrapolate` sur la grille MM court terme).
- **D** : zone ]G2 ; 365[ : droite MM entre **(G2 ; MM(G2))** et **(365 ; B₃₆₅)** où `B₃₆₅` = taux **Taux** échéancier au point le plus proche de 365 j.

Arrondi final : même `ndigits` que la production pour B/C/D (comparabilité).

## Tableau comparatif (synthèse)

| date_valo | K_premier_flux | dernier_CT | premier_LT | branche_A | taux_secondaire_A_ndigits | taux_actu_debug_rev_pct | prix_moteur_A | prix_Manar | écart_A |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-01-02 | 361 | 255 | 437.0 | hybride_CT_365 | 0.023097 | 2.71 | 100029.3156 | 100029.32 | -0.0044 |
| 2026-03-06 | 298 | 192 | 374.0 | hybride_CT_365 | 0.023565 | 2.757 | 100455.0711 | 100455.07 | +0.0011 |
| 2026-03-26 | 278 | 326 | 543.0 | CT | 0.024284 | 2.828 | 100551.7463 | 100551.75 | -0.0037 |

## Date valorisation `2026-01-02`

- **Piliers CT (j)** : `[1.0, 73.0, 136.0, 255.0]`
- **Piliers LT SQL (j)** : `[437.0, 1564.0, 1928.0, 3454.0, 4945.0, 7164.0, 10699.0]`
- **dernier_CT (G2)** = `255` j — **premier_LT SQL** = `437.0`
- **Écart (premier_LT_SQL − dernier_CT)** = `182.0` j
- **B(365) colonne Taux échéancier ZC** (MM, décimal) = `0.02312176`
- **METHODE_VALO** = `AA` | **REV** = `True`

### Premier flux futur

- Date : **2026-12-29** — **K** = `361` j
- Branche Formule B (scénario A) : **hybride_CT_365**
- Taux secondaire avant `ndigits` : `0.023096517961376014`
- Après arrondi moteur (`ndigits=6`) : `0.023097`

**debug_rev (moteur)** :

```json
{
  "date_valorisation": "2026-01-02",
  "date_prochaine_revision": "2026-12-29",
  "jours_calculs": 361,
  "duree_act360": 1.0027777778,
  "duree_exposant_ligne": 1.0027777778,
  "taux_actualisation_pct": 2.71,
  "flux_prochain": 2747.64,
  "capital_restant": 100000.0,
  "crd_pv": {
    "CODE": "9606",
    "TYPE_TAUX": "REV",
    "PERIODICITE_REMBOU": "AN",
    "CATEGORIE": "OBL",
    "S_CATEGORIE": "OBLSUB",
    "regle_crd_utilisee": "REV_EXISTANT",
    "CAPITAL_RESTANT_SQL": 100000.0,
    "CAPITAL_AMORTIS_SQL": 0.0,
    "CRD_DEBUT_PV": null,
    "numerateur_PV": 102747.64
  },
  "prix_calcule": 100029.32,
  "formule": "(flux + capital) / (1 + taux * jours/360)"
}
```

### Détail par flux futur (scénario **A** — interpolation production)

| date_valo | date_flux | K_jours | duree_affichee | dernier_CT | premier_LT_sql | gap_CT_LT | branche_Formule_B | taux_CT_si_CT | taux_LT_extrapole | taux_secondaire_avant_ndigits | taux_courbe_pct_affiche | ligne_taux_courbe | prime_pct | taux_actu_pct | facteur_actu_REV_lineaire | flux | PV_flux |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-01-02 | 2026-12-29 | 361 | 1.002777778 | 255 | 437 | 182 | hybride_CT_365 |  | 0.02342028502 | 0.02309651796 | 2.31 | Taux AA | 0.4 | 2.71 | 0.9735436801 | 2747.64 | 100029.3156 |
| 2026-01-02 | 2027-12-29 | 726 | 2.016666667 | 255 | 437 | 182 | LT |  | 0.02431286602 | 0.02431286602 | 2.31 | Taux AA | 0.4 | 2.71 | 0.9481803629 | 3518.19 | 0 |
| 2026-01-02 | 2028-12-29 | 1092 | 3.033333333 | 255 | 437 | 182 | LT |  | 0.02496237799 | 0.02496237799 | 2.31 | Taux AA | 0.4 | 2.71 | 0.9240407687 | 3518.19 | 0 |
| 2026-01-02 | 2029-12-29 | 1457 | 4.047222222 | 255 | 437 | 182 | LT |  | 0.02561011535 | 0.02561011535 | 2.31 | Taux AA | 0.4 | 2.71 | 0.9011609206 | 23518.19 | 0 |
| 2026-01-02 | 2030-12-29 | 1822 | 5.061111111 | 255 | 437 | 182 | LT |  | 0.02629615385 | 0.02629615385 | 2.31 | Taux AA | 0.4 | 2.71 | 0.8793867352 | 22814.56 | 0 |
| 2026-01-02 | 2031-12-29 | 2187 | 6.075 | 255 | 437 | 182 | LT |  | 0.02699220183 | 0.02699220183 | 2.31 | Taux AA | 0.4 | 2.71 | 0.8586399572 | 22110.92 | 0 |
| 2026-01-02 | 2032-12-29 | 2553 | 7.091666667 | 255 | 437 | 182 | LT |  | 0.02768774574 | 0.02768774574 | 2.31 | Taux AA | 0.4 | 2.71 | 0.8387965785 | 21407.28 | 0 |
| 2026-01-02 | 2033-12-29 | 2918 | 8.105555556 | 255 | 437 | 182 | LT |  | 0.02838138925 | 0.02838138925 | 2.31 | Taux AA | 0.4 | 2.71 | 0.8199002546 | 20703.64 | 0 |
### Prix par scénario (rejeu ``construire_tables`` + callback `taux_secondaire_a_j`)

| Scénario | prix_somme_flux_actualises | Écart vs Manar |
| --- | ---: | ---: |
| A | 100029.3156 | -0.0044 |
| B | 100029.3156 | -0.0044 |
| C | 100070.3468 | +41.0268 |
| D | 100029.3156 | -0.0044 |
> **Prix arrondi** (ligne marché après ``valoriser_dataframe_base_titre``, inchangé par ces rejeux) : `96364.181191` — le champ JSON ``prix_actualise`` du tableau d’amortissement est le **dirty** (clean + coupon couru), à ne pas confondre avec le clean Manar.

**Scénario le plus proche de Manar** : `A` (|Δ| min = `0.0044000000052619725`)

## Date valorisation `2026-03-06`

- **Piliers CT (j)** : `[1.0, 73.0, 164.0, 192.0]`
- **Piliers LT SQL (j)** : `[374.0, 1501.0, 1865.0, 3391.0, 4882.0, 7101.0, 10636.0]`
- **dernier_CT (G2)** = `192` j — **premier_LT SQL** = `374.0`
- **Écart (premier_LT_SQL − dernier_CT)** = `182.0` j
- **B(365) colonne Taux échéancier ZC** (MM, décimal) = `0.02392607`
- **METHODE_VALO** = `AA` | **REV** = `True`

### Premier flux futur

- Date : **2026-12-29** — **K** = `298` j
- Branche Formule B (scénario A) : **hybride_CT_365**
- Taux secondaire avant `ndigits` : `0.023564909836016196`
- Après arrondi moteur (`ndigits=6`) : `0.023565`

**debug_rev (moteur)** :

```json
{
  "date_valorisation": "2026-03-06",
  "date_prochaine_revision": "2026-12-29",
  "jours_calculs": 298,
  "duree_act360": 0.8277777778,
  "duree_exposant_ligne": 0.8277777778,
  "taux_actualisation_pct": 2.757,
  "flux_prochain": 2747.64,
  "capital_restant": 100000.0,
  "crd_pv": {
    "CODE": "9606",
    "TYPE_TAUX": "REV",
    "PERIODICITE_REMBOU": "AN",
    "CATEGORIE": "OBL",
    "S_CATEGORIE": "OBLSUB",
    "regle_crd_utilisee": "REV_EXISTANT",
    "CAPITAL_RESTANT_SQL": 100000.0,
    "CAPITAL_AMORTIS_SQL": 0.0,
    "CRD_DEBUT_PV": null,
    "numerateur_PV": 102747.64
  },
  "prix_calcule": 100455.07,
  "formule": "(flux + capital) / (1 + taux * jours/360)"
}
```

### Détail par flux futur (scénario **A** — interpolation production)

| date_valo | date_flux | K_jours | duree_affichee | dernier_CT | premier_LT_sql | gap_CT_LT | branche_Formule_B | taux_CT_si_CT | taux_LT_extrapole | taux_secondaire_avant_ndigits | taux_courbe_pct_affiche | ligne_taux_courbe | prime_pct | taux_actu_pct | facteur_actu_REV_lineaire | flux | PV_flux |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-03-06 | 2026-12-29 | 298 | 0.8277777778 | 192 | 374 | 182 | hybride_CT_365 |  | 0.02394433035 | 0.02356490984 | 2.357 | Taux AA | 0.4 | 2.757 | 0.9776873815 | 2747.64 | 100455.0711 |
| 2026-03-06 | 2027-12-29 | 663 | 1.841666667 | 192 | 374 | 182 | LT |  | 0.02509494232 | 0.02509494232 | 2.357 | Taux AA | 0.4 | 2.757 | 0.9516787494 | 3518.19 | 0 |
| 2026-03-06 | 2028-12-29 | 1029 | 2.858333333 | 192 | 374 | 182 | LT |  | 0.02610168589 | 0.02610168589 | 2.357 | Taux AA | 0.4 | 2.757 | 0.9269522251 | 3518.19 | 0 |
| 2026-03-06 | 2029-12-29 | 1394 | 3.872222222 | 192 | 374 | 182 | LT |  | 0.02710567879 | 0.02710567879 | 2.357 | Taux AA | 0.4 | 2.757 | 0.9035405689 | 23518.19 | 0 |
| 2026-03-06 | 2030-12-29 | 1759 | 4.886111111 | 192 | 374 | 182 | LT |  | 0.02796703297 | 0.02796703297 | 2.357 | Taux AA | 0.4 | 2.757 | 0.8812823775 | 22814.56 | 0 |
| 2026-03-06 | 2031-12-29 | 2124 | 5.9 | 192 | 374 | 182 | LT |  | 0.02855642202 | 0.02855642202 | 2.357 | Taux AA | 0.4 | 2.757 | 0.8600944556 | 22110.92 | 0 |
| 2026-03-06 | 2032-12-29 | 2490 | 6.916666667 | 192 | 374 | 182 | LT |  | 0.02906009174 | 0.02906009174 | 2.357 | Taux AA | 0.4 | 2.757 | 0.8398473997 | 21407.28 | 0 |
| 2026-03-06 | 2033-12-29 | 2855 | 7.930555556 | 192 | 374 | 182 | LT |  | 0.02956238532 | 0.02956238532 | 2.357 | Taux AA | 0.4 | 2.757 | 0.8205832364 | 20703.64 | 0 |
### Prix par scénario (rejeu ``construire_tables`` + callback `taux_secondaire_a_j`)

| Scénario | prix_somme_flux_actualises | Écart vs Manar |
| --- | ---: | ---: |
| A | 100455.0711 | +0.0011 |
| B | 100455.0711 | +0.0011 |
| C | 100470.5203 | +15.4503 |
| D | 100455.0711 | +0.0011 |
> **Prix arrondi** (ligne marché après ``valoriser_dataframe_base_titre``, inchangé par ces rejeux) : `96113.225416` — le champ JSON ``prix_actualise`` du tableau d’amortissement est le **dirty** (clean + coupon couru), à ne pas confondre avec le clean Manar.

**Scénario le plus proche de Manar** : `A` (|Δ| min = `0.0010999999940395355`)

## Date valorisation `2026-03-26`

- **Piliers CT (j)** : `[1.0, 53.0, 144.0, 326.0]`
- **Piliers LT SQL (j)** : `[543.0, 1481.0, 1845.0, 3371.0, 4862.0, 7081.0, 10616.0]`
- **dernier_CT (G2)** = `326` j — **premier_LT SQL** = `543.0`
- **Écart (premier_LT_SQL − dernier_CT)** = `217.0` j
- **B(365) colonne Taux échéancier ZC** (MM, décimal) = `0.02479874`
- **METHODE_VALO** = `AA` | **REV** = `True`

### Premier flux futur

- Date : **2026-12-29** — **K** = `278` j
- Branche Formule B (scénario A) : **CT**
- Taux secondaire avant `ndigits` : `0.024283516483516482`
- Après arrondi moteur (`ndigits=6`) : `0.024284`

**debug_rev (moteur)** :

```json
{
  "date_valorisation": "2026-03-26",
  "date_prochaine_revision": "2026-12-29",
  "jours_calculs": 278,
  "duree_act360": 0.7722222222,
  "duree_exposant_ligne": 0.7722222222,
  "taux_actualisation_pct": 2.828,
  "flux_prochain": 2747.64,
  "capital_restant": 100000.0,
  "crd_pv": {
    "CODE": "9606",
    "TYPE_TAUX": "REV",
    "PERIODICITE_REMBOU": "AN",
    "CATEGORIE": "OBL",
    "S_CATEGORIE": "OBLSUB",
    "regle_crd_utilisee": "REV_EXISTANT",
    "CAPITAL_RESTANT_SQL": 100000.0,
    "CAPITAL_AMORTIS_SQL": 0.0,
    "CRD_DEBUT_PV": null,
    "numerateur_PV": 102747.64
  },
  "prix_calcule": 100551.75,
  "formule": "(flux + capital) / (1 + taux * jours/360)"
}
```

### Détail par flux futur (scénario **A** — interpolation production)

| date_valo | date_flux | K_jours | duree_affichee | dernier_CT | premier_LT_sql | gap_CT_LT | branche_Formule_B | taux_CT_si_CT | taux_LT_extrapole | taux_secondaire_avant_ndigits | taux_courbe_pct_affiche | ligne_taux_courbe | prime_pct | taux_actu_pct | facteur_actu_REV_lineaire | flux | PV_flux |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-03-26 | 2026-12-29 | 278 | 0.7722222222 | 326 | 543 | 217 | CT | 0.02428351648 |  | 0.02428351648 | 2.428 | Taux AA | 0.4 | 2.828 | 0.9786282807 | 2747.64 | 100551.7463 |
| 2026-03-26 | 2027-12-29 | 643 | 1.786111111 | 326 | 543 | 217 | LT |  | 0.02631577825 | 0.02631577825 | 2.428 | Taux AA | 0.4 | 2.828 | 0.9519174844 | 3518.19 | 0 |
| 2026-03-26 | 2028-12-29 | 1009 | 2.802777778 | 326 | 543 | 217 | LT |  | 0.02783752665 | 0.02783752665 | 2.428 | Taux AA | 0.4 | 2.828 | 0.9265585977 | 3518.19 | 0 |
| 2026-03-26 | 2029-12-29 | 1374 | 3.816666667 | 326 | 543 | 217 | LT |  | 0.02935511727 | 0.02935511727 | 2.428 | Taux AA | 0.4 | 2.828 | 0.9025797535 | 23518.19 | 0 |
| 2026-03-26 | 2030-12-29 | 1739 | 4.830555556 | 326 | 543 | 217 | LT |  | 0.03050879121 | 0.03050879121 | 2.428 | Taux AA | 0.4 | 2.828 | 0.8798107195 | 22814.56 | 0 |
| 2026-03-26 | 2031-12-29 | 2104 | 5.844444444 | 326 | 543 | 217 | LT |  | 0.03113944954 | 0.03113944954 | 2.428 | Taux AA | 0.4 | 2.828 | 0.8581621904 | 22110.92 | 0 |
| 2026-03-26 | 2032-12-29 | 2470 | 6.861111111 | 326 | 543 | 217 | LT |  | 0.03161913499 | 0.03161913499 | 2.428 | Taux AA | 0.4 | 2.828 | 0.8374983366 | 21407.28 | 0 |
| 2026-03-26 | 2033-12-29 | 2835 | 7.875 | 326 | 543 | 217 | LT |  | 0.03209750983 | 0.03209750983 | 2.428 | Taux AA | 0.4 | 2.828 | 0.817858764 | 20703.64 | 0 |
### Prix par scénario (rejeu ``construire_tables`` + callback `taux_secondaire_a_j`)

| Scénario | prix_somme_flux_actualises | Écart vs Manar |
| --- | ---: | ---: |
| A | 100551.7463 | -0.0037 |
| B | 100551.7463 | -0.0037 |
| C | 100551.7463 | -0.0037 |
| D | 100551.7463 | -0.0037 |
> **Prix arrondi** (ligne marché après ``valoriser_dataframe_base_titre``, inchangé par ces rejeux) : `94638.348408` — le champ JSON ``prix_actualise`` du tableau d’amortissement est le **dirty** (clean + coupon couru), à ne pas confondre avec le clean Manar.

**Scénario le plus proche de Manar** : `A` (|Δ| min = `0.003700000001117587`)
