# Diagnostic Manar × courbe

- Tolérance absolue Manar : 0.02

|code|date|Prix_Manar|Prix_SQL_moteur|Prix_WG_piliers_excel|Δ_Manar_vs_SQL|Δ_Manar_vs_WG|Mr|Taux_interp_MR_pct|pilier_-j|pilier_+j|G2_joint|src_piliers_SQL|type|cause|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 100993 | 2026-01-02 | 101067.6100 | 101066.9219 | N/A | -0.6881 |  | 1623 | 2.597865 | 1461.0 | 1826.0 | 255.0 | MAR_JJ | amort/ATP/METH_ZC | Écart Manar hors tolérance (-0.6881). Pas de WG calé à cette date : Manar fichier possiblement basé sur un autre G2/grille. Date WG=2026-03-26 ≠ valeur test 2026-01-02 |
| 101005 | 2026-01-02 | 100510.5600 | 100509.3653 | N/A | -1.1947 |  | 2413 | 2.756217 | 2192.0 | 2557.0 | 255.0 | MAR_JJ | amort/ATP/METH_ZC | Écart Manar hors tolérance (-1.1947). Pas de WG calé à cette date : Manar fichier possiblement basé sur un autre G2/grille. Date WG=2026-03-26 ≠ valeur test 2026-01-02 |
| 100993 | 2026-03-06 | 101312.3900 | 101312.1007 | N/A | -0.2893 |  | 1560 | 2.759305 | 1461.0 | 1826.0 | 192.0 | MAR_JJ | amort/ATP/METH_ZC | Écart Manar hors tolérance (-0.2893). Pas de WG calé à cette date : Manar fichier possiblement basé sur un autre G2/grille. Date WG=2026-03-26 ≠ valeur test 2026-03-06 |
| 101005 | 2026-03-06 | 100615.0500 | 100614.9720 | N/A | -0.0780 |  | 2350 | 2.901028 | 2192.0 | 2557.0 | 192.0 | MAR_JJ | amort/ATP/METH_ZC | Écart Manar hors tolérance (-0.0780). Pas de WG calé à cette date : Manar fichier possiblement basé sur un autre G2/grille. Date WG=2026-03-26 ≠ valeur test 2026-03-06 |

---

## Analyse de sensibilité courbe

_Lignes incluses : écart Manar vs prix SQL moteur hors tolérance (0.02), sauf si `--sensitivity-all`._

### Sensibilité courbe — `100993` @ `2026-01-02`

- **Prix base (SQL actuel)** : 101066.921900
- **Prix Manar réf.** : 101067.610000  → **Δ Manar − base** = +0.688100
- **Premier pilier CT (j)** : 1
- **Joint G2 (j, MM finale CT)** : 255
- **Pilier courbe proche « inf » grille annuelle** (1461.0 j) : ('LT', 1564.0) ; **proche « sup »** (1826.0 j) : ('LT', 1928.0)

| Scénario (±1 bp) | Prix | Δ vs base | Δ vs Manar |
|---|---:|---:|---:|
| base_sql | 101066.921900 | 0 | +0.688100 |
| ct1er_plus1bp | 101066.921900 | +0.000000 | +0.688100 |
| ct1er_moins1bp | 101066.921900 | +0.000000 | +0.688100 |
| G2_joint_plus1bp | 101066.128300 | -0.793600 | +1.481700 |
| G2_joint_moins1bp | 101067.618300 | +0.696400 | -0.008300 |
| pil_prog_inf_LT_1564p1 | 101055.605200 | -11.316700 | +12.004800 |
| pil_prog_inf_LT_1564m1 | 101078.720900 | +11.799000 | -11.110900 |
| pil_prog_sup_LT_1928p1 | 101064.600500 | -2.321400 | +3.009500 |
| pil_prog_sup_LT_1928m1 | 101070.017500 | +3.095600 | -2.407500 |

- **|Variation prix| / bp** (1er pilier CT, symétrique ±1 bp) : **0.000000** (≈ |Δp| pour 1 bp)
- **|Variation prix| / bp** (joint G2 / pilier CT à 255 j, symétrique ±1 bp) : **0.745000**
- **Meilleur ajustement 1 bp testé** : `G2_joint_moins1bp` → prix 101067.618300
- **|Écart Manar − base|** initial : **0.688100** → après meilleur choc : **0.008300** (variation signée résiduelle : **-0.008300**)
- **Réduction relative de |écart|** (vs base avant chocs) : **98.8 %**

**Conclusion (stricte)** : **Principalement courbe** — règle 1 : après le meilleur choc ±1 bp, **|écart résiduel| ≤ tolérance**. Meilleur scénario **`G2_joint_moins1bp`** ramène quasi Manar (**-0.008300**).

**Décomposition indicative (METH_ZC)**

- **Effet courbe** : Dominant : les niveaux MAR_JJ (CT / LT / joint G2) absorbent l’écart au sens utilisé ci-dessous.
- **Méthode ZC / NPV** : Secondaire tant que résidu après choc dans la tol ; valider la cohérence NPV / flux vs Manar après alignement courbe.
- **Interpolation ZC / grille échéancier** : Secondaire même pour METH_ZC : l’écart disparaît par simple translation de niveau de courbe / pilier sous test.


### Sensibilité courbe — `101005` @ `2026-01-02`

- **Prix base (SQL actuel)** : 100509.365300
- **Prix Manar réf.** : 100510.560000  → **Δ Manar − base** = +1.194700
- **Premier pilier CT (j)** : 1
- **Joint G2 (j, MM finale CT)** : 255
- **Pilier courbe proche « inf » grille annuelle** (2192.0 j) : ('LT', 1928.0) ; **proche « sup »** (2557.0 j) : ('LT', 1928.0)

| Scénario (±1 bp) | Prix | Δ vs base | Δ vs Manar |
|---|---:|---:|---:|
| base_sql | 100509.365300 | 0 | +1.194700 |
| ct1er_plus1bp | 100509.365300 | +0.000000 | +1.194700 |
| ct1er_moins1bp | 100509.365300 | +0.000000 | +1.194700 |
| G2_joint_plus1bp | 100508.563600 | -0.801700 | +1.996400 |
| G2_joint_moins1bp | 100509.916200 | +0.550900 | +0.643800 |
| pil_prog_inf_LT_1928p1 | 100494.911100 | -14.454200 | +15.648900 |
| pil_prog_inf_LT_1928m1 | 100522.540400 | +13.175100 | -11.980400 |
| pil_prog_sup_LT_1928p1 | 100494.911100 | -14.454200 | +15.648900 |
| pil_prog_sup_LT_1928m1 | 100522.540400 | +13.175100 | -11.980400 |

- **|Variation prix| / bp** (1er pilier CT, symétrique ±1 bp) : **0.000000** (≈ |Δp| pour 1 bp)
- **|Variation prix| / bp** (joint G2 / pilier CT à 255 j, symétrique ±1 bp) : **0.676300**
- **Meilleur ajustement 1 bp testé** : `G2_joint_moins1bp` → prix 100509.916200
- **|Écart Manar − base|** initial : **1.194700** → après meilleur choc : **0.643800** (variation signée résiduelle : **+0.643800**)
- **Réduction relative de |écart|** (vs base avant chocs) : **46.1 %**

**Conclusion (stricte)** : **Partiellement courbe** — règle 2 : le meilleur choc réduit **|écart|** d’au moins **40 %**, mais le résidu **0.643800** (|Δ| après choc) reste **> tolérance** (0.02). Réduction observée ≈ **46.1 %** ; écart résiduel signé **+0.643800** ; scénario **`G2_joint_moins1bp`**.

**Décomposition indicative (METH_ZC)**

- **Effet courbe** : Contribution marquée (~46 % du |Δ| expliquée par niveaux MAR_JJ / chocs testés).
- **Méthode ZC / NPV** : À investiguer : **méthode ZC / tableau amortissement / NPV titre** hors simple translation ±1 bp (résidu > tol).
- **Interpolation ZC / grille échéancier** : Priorité plutôt **niveau court / joint G2** qu’interpolation longue courbe (meilleur choc non sur bornes « inf/sup » programme). Vérifier néanmoins ZC discrétisée vs Manar.


### Sensibilité courbe — `100993` @ `2026-03-06`

- **Prix base (SQL actuel)** : 101312.100700
- **Prix Manar réf.** : 101312.390000  → **Δ Manar − base** = +0.289300
- **Premier pilier CT (j)** : 1
- **Joint G2 (j, MM finale CT)** : 192
- **Pilier courbe proche « inf » grille annuelle** (1461.0 j) : ('LT', 1501.0) ; **proche « sup »** (1826.0 j) : ('LT', 1865.0)

| Scénario (±1 bp) | Prix | Δ vs base | Δ vs Manar |
|---|---:|---:|---:|
| base_sql | 101312.100700 | 0 | +0.289300 |
| ct1er_plus1bp | 101312.041000 | -0.059700 | +0.349000 |
| ct1er_moins1bp | 101312.220100 | +0.119400 | +0.169900 |
| G2_joint_plus1bp | 101312.100700 | +0.000000 | +0.289300 |
| G2_joint_moins1bp | 101312.100700 | +0.000000 | +0.289300 |
| pil_prog_inf_LT_1501p1 | 101299.281500 | -12.819200 | +13.108500 |
| pil_prog_inf_LT_1501m1 | 101323.577400 | +11.476700 | -11.187400 |
| pil_prog_sup_LT_1865p1 | 101309.876200 | -2.224500 | +2.513800 |
| pil_prog_sup_LT_1865m1 | 101313.584000 | +1.483300 | -1.194000 |

- **|Variation prix| / bp** (1er pilier CT, symétrique ±1 bp) : **0.089550** (≈ |Δp| pour 1 bp)
- **|Variation prix| / bp** (joint G2 / pilier CT à 192 j, symétrique ±1 bp) : **0.000000**
- **Meilleur ajustement 1 bp testé** : `ct1er_moins1bp` → prix 101312.220100
- **|Écart Manar − base|** initial : **0.289300** → après meilleur choc : **0.169900** (variation signée résiduelle : **+0.169900**)
- **Réduction relative de |écart|** (vs base avant chocs) : **41.3 %**

**Conclusion (stricte)** : **Partiellement courbe** — règle 2 : le meilleur choc réduit **|écart|** d’au moins **40 %**, mais le résidu **0.169900** (|Δ| après choc) reste **> tolérance** (0.02). Réduction observée ≈ **41.3 %** ; écart résiduel signé **+0.169900** ; scénario **`ct1er_moins1bp`**.

**Décomposition indicative (METH_ZC)**

- **Effet courbe** : Contribution marquée (~41 % du |Δ| expliquée par niveaux MAR_JJ / chocs testés).
- **Méthode ZC / NPV** : À investiguer : **méthode ZC / tableau amortissement / NPV titre** hors simple translation ±1 bp (résidu > tol).
- **Interpolation ZC / grille échéancier** : Priorité plutôt **niveau court / joint G2** qu’interpolation longue courbe (meilleur choc non sur bornes « inf/sup » programme). Vérifier néanmoins ZC discrétisée vs Manar.


### Sensibilité courbe — `101005` @ `2026-03-06`

- **Prix base (SQL actuel)** : 100614.972000
- **Prix Manar réf.** : 100615.050000  → **Δ Manar − base** = +0.078000
- **Premier pilier CT (j)** : 1
- **Joint G2 (j, MM finale CT)** : 192
- **Pilier courbe proche « inf » grille annuelle** (2192.0 j) : ('LT', 1865.0) ; **proche « sup »** (2557.0 j) : ('LT', 1865.0)

| Scénario (±1 bp) | Prix | Δ vs base | Δ vs Manar |
|---|---:|---:|---:|
| base_sql | 100614.972000 | 0 | +0.078000 |
| ct1er_plus1bp | 100614.972000 | +0.000000 | +0.078000 |
| ct1er_moins1bp | 100614.972000 | +0.000000 | +0.078000 |
| G2_joint_plus1bp | 100614.972000 | +0.000000 | +0.078000 |
| G2_joint_moins1bp | 100615.196600 | +0.224600 | -0.146600 |
| pil_prog_inf_LT_1865p1 | 100601.594900 | -13.377100 | +13.455100 |
| pil_prog_inf_LT_1865m1 | 100628.355700 | +13.383700 | -13.305700 |
| pil_prog_sup_LT_1865p1 | 100601.594900 | -13.377100 | +13.455100 |
| pil_prog_sup_LT_1865m1 | 100628.355700 | +13.383700 | -13.305700 |

- **|Variation prix| / bp** (1er pilier CT, symétrique ±1 bp) : **0.000000** (≈ |Δp| pour 1 bp)
- **|Variation prix| / bp** (joint G2 / pilier CT à 192 j, symétrique ±1 bp) : **0.112300**
- **Meilleur ajustement 1 bp testé** : `base_sql` → prix 100614.972000
- **|Écart Manar − base|** initial : **0.078000** → après meilleur choc : **0.078000** (variation signée résiduelle : **+0.078000**)
- **Réduction relative de |écart|** (vs base avant chocs) : **0.0 %**

**Conclusion (stricte)** : **Probablement pas courbe seule** — règle 3 : la réduction de |écart| est **< 40 %** (0.0 %). Les chocs ±1 bp sur les piliers testés **n’expliquent pas** l’écart Manar.

**Décomposition indicative (METH_ZC)**

- **Effet courbe** : Limité au sens des chocs pilier testés.
- **Méthode ZC / NPV** : **Priorité** : **méthode ZC / NPV / flux** (construction cash-flows, paramètres titre, arrondis) vs Manar.
- **Interpolation ZC / grille échéancier** : Interpolation ZC : secondaire sauf preuve contraire (chocs courbe peu efficaces).


---

## Tableau de décision synthèse

| code | date | problème courbe | problème interpolation | problème flux/coupon couru | problème ZC | problème non identifié | écart ini. valeur abs.(M−SQL) | écart ini. signé | meilleur scénario | réduction Δ abs. % | reste signé (M− prix choc) | conclusion |
|---|---|:--:|:--:|:--:|:--:|:--:|---:|---:|---|---:|---:|---|
| 100993 | 2026-01-02 | ✓ |  |  |  |  | 0.6881 | +0.6881 | G2_joint_moins1bp | 98.8 | -0.0083 | **Principalement courbe** — règle 1 : après le meilleur choc ±1 bp, **∣écart résiduel∣ ≤ tolérance**. Meilleur scénario **`G2_joint_moins1bp`** ramène quasi Manar (**-0.008300**). Effet courbe (ZC): Dominant : les niveaux MAR_JJ (CT / LT / joint G2) absorbent l’écart au sens utilisé ci-dessous. │ Méthode ZC/NPV: Secondaire tant que résidu après choc dans la tol ; valider la cohérence NPV / flux vs Manar après alignement courbe. │ Interpolation ZC: Secondaire même pour METH_ZC : l’écart disparaît par simple translation de niveau de courbe / pilier sous test. |
| 101005 | 2026-01-02 | ✓ |  |  | ✓ |  | 1.1947 | +1.1947 | G2_joint_moins1bp | 46.1 | +0.6438 | **Partiellement courbe** — règle 2 : le meilleur choc réduit **∣écart∣** d’au moins **40 %**, mais le résidu **0.643800** (∣Δ∣ après choc) reste **> tolérance** (0.02). Réduction observée ≈ **46.1 %** ; écart résiduel signé **+0.643800** ; scénario **`G2_joint_moins1bp`**. Effet courbe (ZC): Contribution marquée (~46 % du ∣Δ∣ expliquée par niveaux MAR_JJ / chocs testés). │ Méthode ZC/NPV: À investiguer : **méthode ZC / tableau amortissement / NPV titre** hors simple translation ±1 bp (résidu > tol). │ Interpolation ZC: Priorité plutôt **niveau court / joint G2** qu’interpolation longue courbe (meilleur choc non s |
| 100993 | 2026-03-06 | ✓ |  |  | ✓ |  | 0.2893 | +0.2893 | ct1er_moins1bp | 41.3 | +0.1699 | **Partiellement courbe** — règle 2 : le meilleur choc réduit **∣écart∣** d’au moins **40 %**, mais le résidu **0.169900** (∣Δ∣ après choc) reste **> tolérance** (0.02). Réduction observée ≈ **41.3 %** ; écart résiduel signé **+0.169900** ; scénario **`ct1er_moins1bp`**. Effet courbe (ZC): Contribution marquée (~41 % du ∣Δ∣ expliquée par niveaux MAR_JJ / chocs testés). │ Méthode ZC/NPV: À investiguer : **méthode ZC / tableau amortissement / NPV titre** hors simple translation ±1 bp (résidu > tol). │ Interpolation ZC: Priorité plutôt **niveau court / joint G2** qu’interpolation longue courbe (meilleur choc non sur  |
| 101005 | 2026-03-06 |  |  |  | ✓ |  | 0.0780 | +0.0780 | base_sql | 0.0 | +0.0780 | **Probablement pas courbe seule** — règle 3 : la réduction de ∣écart∣ est **< 40 %** (0.0 %). Les chocs ±1 bp sur les piliers testés **n’expliquent pas** l’écart Manar. Effet courbe (ZC): Limité au sens des chocs pilier testés. │ Méthode ZC/NPV: **Priorité** : **méthode ZC / NPV / flux** (construction cash-flows, paramètres titre, arrondis) vs Manar. │ Interpolation ZC: Interpolation ZC : secondaire sauf preuve contraire (chocs courbe peu efficaces). |