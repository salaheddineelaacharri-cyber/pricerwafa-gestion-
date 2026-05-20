# Investigation FPCT / ZC / TRI — écarts au 06/03/2026 vs référence 26/03/2026

_Analyse uniquement : aucune modification de code, aucun hardcode, pas d’export JSON volumineux. Chiffres extraits de `reports/analyse_ecarts_0603_vs_2603.md` ; échéanciers confirmés sur `dbo.echeancier_titre`._

---

## 1. Synthèse chiffrée (titres prioritaires)

| Code | Profil | Prix ref 26/03 | Prix calc 26/03 | Écart 26/03 | Prix ref 06/03 | Prix calc 06/03 | Écart 06/03 | Δ écart |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5061 | FIX/ZC/TRI/TRI/R/R/FPCT | 73 369,22 | 73 369,25 | +0,03 | 77 915,73 | 77 888,79 | −26,94 | −26,97 |
| 5107 | FIX/ZC/TRI/TRI/R/R/FPCT | 98 741,41 | 98 741,41 | 0,00 | 100 905,85 | 100 915,53 | +9,68 | +9,68 |
| 5116 | FIX/ZC/TRI/TRI/R/R/FPCT | 53 193,00 | 53 193,03 | +0,03 | 56 244,22 | 56 252,70 | +8,48 | +8,45 |
| 5117 | FIX/ZC/TRI/TRI/R/R/FPCT | 80 421,76 | 80 421,77 | +0,01 | 83 081,38 | 83 086,79 | +5,41 | +5,40 |
| 5122 | FIX/ZC/TRI/TRI/R/R/FPCT | 40 564,44 | 40 564,50 | +0,06 | 43 798,95 | 43 810,69 | +11,74 | +11,68 |
| 5151 | REV/ZC/TRI/FIN/R/360/FPCT | 100 024,48 | 100 024,64 | +0,16 | 100 598,47 | 100 622,76 | +24,29 | +24,13 |

**Remarque** : pour **5061**, le **prix de référence Manar** augmente fortement entre les deux dates (≈ +4 546) ; l’écart négatif au 06/03 combine donc **mouvement de valo** et **moteur**.

---

## 2. Données SQL (`dbo.referentiel_titre`)

| Code | TYPE | METHODE_VALO | Périodicités | Base | Cat. | Spread | Valeur taux | Échéance |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |
| 5061 | FIX | ZC | TRI / TRI | R/R | FPCT | 55 | 4,03 % | 2035-12-24 |
| 5107 | FIX | ZC | TRI / TRI | R/R | FPCT | 60 | 3,51 % | 2035-12-24 |
| 5116 | FIX | ZC | TRI / TRI | R/R | FPCT | 50 | 2,63 % | 2033-12-24 |
| 5117 | FIX | ZC | TRI / TRI | R/R | FPCT | 65 | 3,10 % | 2037-09-24 |
| 5122 | FIX | ZC | TRI / TRI | R/R | FPCT | 60 | 2,70 % | 2033-03-28 |
| 5151 | REV | ZC | TRI / FIN | R/360 | FPCT | 75 | 3,03 % | 2036-12-24 |

Les cinq **FIX** déclenchent la règle **`FIX_ZC_TRI_TRI_RR_RULE`** (FIX + ZC + TRI + TRI + **R/R**). **5151** est **REV** + **R/360** + **TRI/FIN** : branche **`use_rev_zc_tri_fin_r360`** (durées via `calculer_duree_affichage_rev`), pas la même tête de chaîne que les FIX, mais **même filtre de flux futurs** sur les montants PV (voir §3).

---

## 3. Tombées proches de mars 2026 (`dbo.echeancier_titre`)

| Titre | Tombées janv.–juin 2026 |
| --- | --- |
| **5061** | **2026-03-24** (758,26) ; 2026-06-24 |
| **5107** | **2026-03-24** (877,50) ; 2026-06-24 |
| **5116** | **2026-03-24** (375,23) ; 2026-06-24 |
| **5117** | **2026-03-24** (663,32) ; 2026-06-24 |
| **5122** | **2026-03-26** (294,28) ; 2026-06-26 |
| **5151** | **2026-03-24** (774,33) ; 2026-06-24 |

---

## 4. Logique code — FPCT / ZC / TRI (FIX)

### 4.1 Activation `FIX_ZC_TRI_TRI_RR_RULE`

Définie lorsque : **obligation FIX**, **METHODE_VALO** contient **ZC**, **PERIODICITE_COUPON** et **PERIODICITE_REMBOU** commencent par **TRI**, et **BASE_CALCUL** contient **R/R** (`obligation_amort_schedule.py`, vers L1238–1244 et L1618–1624).

Conséquence : **pas** de réécriture « fraction nominal » (`use_frac_n` forcé à faux, L1248–1249).

### 4.2 Flux futurs retenus pour le PV (point critique)

```1403:1406:obligation_amort_schedule.py
    flux_restant: list[float] = [
        float(flux_pv_numerateur[i]) if cols_dates[i] > d_valo else 0.0 for i in range(n)
    ]
```

**Strictement `>`** : une tombée **égale** à la date de valorisation est **exclue** ; une tombée **après** la valorisation est **incluse**.

- **Valorisation 06/03/2026** : tombée **24/03/2026** → incluse pour 5061, 5107, 5116, 5117, 5151.
- **Valorisation 26/03/2026** : **24/03/2026** n’est **pas** > 26/03 → **exclue** ; le premier flux futur devient **juin 2026**.

Pour **5122**, tombée **26/03/2026** :

- Au **06/03** : 26/03 > 06/03 → flux de mars **inclus**.
- Au **26/03** : 26/03 > 26/03 est **faux** → flux du **26/03 exclu** ; premier flux futur **juin 2026**.

### 4.3 Premier pas trimestriel et durées (`FIX_ZC_TRI_TRI_RR_RULE`)

```1652:1667:obligation_amort_schedule.py
                elif is_trimestriel:
                    if FIX_ZC_TRI_TRI_RR_RULE:
                        if tri_first_duration is None:
                            d_prev = cols_dates[i - 1]
                            den_i = int((d_pay - d_prev).days)
                            tri_first_duration = round(
                                0.0 if den_i <= 0 else (max(0, jours) / den_i) * 0.25,
                                5,
                            )
                            duree_exacte = tri_first_duration
                        else:
                            duree_exacte = tri_first_duration + (k_tri - 1) * 0.25
```

La **première colonne future** change (24/03 vs 06/24 ou 26/03 vs 06/26) → **jours** `jours = (d_pay - d_valo).days` et **dénominateur** `den_i` du trimestre changent → **`tri_first_duration`** et toute la suite **+0,25** par trimestre sont **recalées**.

### 4.4 Taux ZC et spread

Pour ZC + `FIX_ZC_TRI_TRI_RR_RULE`, le taux par colonne lit **`taux_zc_schedule_a(_round_excel(du_zc, fix_zc_an_duration_precision))`** avec `du_zc = duree_calc_ans[i]` (L2046–2055). Précision **10 décimales** pour FPCT (non BSF), L1629–1630.

Le taux d’actualisation combine **courbe ZC** + **spread** en décimal avec arrondi **Quantize 0,00001** (L2089–2095).

### 4.5 Coupon couru

Calculé après la grille : `_coupon_couru_schedule(d_valo, cols_dates, interets)` — dépend des **intérêts** de ligne et de la position de **d_valo** entre deux tombées ; varie entre les deux dates mais l’effet est en général **plus petit** que le saut de **premier flux / première durée** pour ces profils.

### 4.6 Courbe

Les piliers viennent de **`dbo.histo_courbe_taux`** pour la date de valorisation (ex. **MAR_JJ**). Les taux **diffèrent** entre le 06/03 et le 26/03 → **sensibilité additionnelle** sur chaque `du_zc`.

### 4.7 Branche **5151** (REV / ZC / TRI / FIN / R/360)

`use_rev_zc_tri_fin_r360` (L1467–1473) : durées issues de **`calculer_duree_affichage_rev`** (fichier `bond_pricing.py`), pas du bloc `tri_first_duration` FIX ci-dessus. En revanche, **`flux_restant`** et la construction des **flux** restent communes à l’échéancier : le **24/03** bascule encore entre « futur » (06/03) et « passé ou non futur » (26/03) au sens du **`>`**.

---

## 5. Comparaison 06/03 vs 26/03 — ce qui change (par titre)

| Code | 1re tombée future au 06/03 | 1re tombée future au 26/03 | Lecture |
| --- | --- | --- | --- |
| 5061 | **2026-03-24** | **2026-06-24** | Saut dû au **`>`** sur `d_valo` ; premier trimestre et flux PV **recalculés**. |
| 5107 | 2026-03-24 | 2026-06-24 | Idem. |
| 5116 | 2026-03-24 | 2026-06-24 | Idem. |
| 5117 | 2026-03-24 | 2026-06-24 | Idem. |
| 5122 | **2026-03-26** | **2026-06-26** | Tombée **le jour même** que la valo 26/03 → **exclue** au 26/03, **incluse** au 06/03. |
| 5151 | 2026-03-24 | 2026-06-24 | Même frontière calendaire ; logique REV ZC TRI sur **durées** + flux. |

**Variables qui changent systématiquement** : nombre / périmètre des **flux futurs** pris en NPV (au moins le **premier**), **première durée trimestrielle**, en cascade **taux ZC** (`schedule_a` sur nouveau `du`), **spread** inchangé en référentiel mais appliqué sur **nouveau** taux actuariel, **coupon couru** (secondaire), **courbe** du jour (effet **additif**).

---

## 6. Cause dominante (question 6)

| Hypothèse | Verdict |
| --- | --- |
| **Flux du 24/03/2026** (et 26/03 pour 5122) | **Très probable (fort)** : aligné sur la règle **`cols_dates[i] > d_valo`** et les dates SQL. |
| **Inclusion / exclusion de la tombée proche** | **Oui (fort)** : c’est le **même** mécanisme que la ligne du tableau ci-dessus. |
| **Première durée trimestrielle** | **Oui (fort)** : recalculée quand l’index de la **première** colonne future change. |
| **Courbe ZC du 06/03** | **Partiel (moyen)** : amplifie l’écart mais seul ne justifie pas la **structure** différente entre titres au même jour de courbe si la frontière de flux était identique. |
| **Coupon couru** | **Faible à moyen** : contributif, surtout entre deux tombées. |
| **Données SQL** | **Moyen** : les dates **24/03** et **26/03** sont **cohérentes** avec l’écart observé ; incohérence SQL **peu probable** pour ce motif précis. **5061** : vérifier en plus la **cohérence** des **valo Manar** entre dates (mouvement de ref important). |

---

## 7. Fiches par titre (constat / suspect / cause / correction proposée / confiance)

### 5061 — FIX FPCT

| Élément | Contenu |
| --- | --- |
| **Constat** | Forte variation d’écart ; **prix ref** Manar monte beaucoup au 06/03. |
| **Variable suspecte** | Frontière **24/03** + **valo Manar** du 06/03. |
| **Fichier / fonction** | `construire_tableau_amortissement` (`flux_restant`, bloc **`FIX_ZC_TRI_TRI_RR_RULE`** durées) ; lecture Manar `backend/main.py`. |
| **Cause probable** | Combinaison **exclusion du coupon 24/03 au 26/03** et **référence Marché** différente entre feuilles / dates. |
| **Correction proposée (non appliquée)** | Vérifier sur classeur Manar si la **tombée du 24/03** est incluse au 26/03 ; si oui, **documenter l’écart de convention** `>` vs `>=` / règle Excel ; aligner spec ou **documenter** l’écart résiduel. |
| **Confiance** | **Fort** sur le rôle du **24/03** ; **moyen** sur la part **Manar** vs moteur. |

### 5107, 5116, 5117 — FIX FPCT

| Élément | Contenu |
| --- | --- |
| **Constat** | Écarts positifs modérés au 06/03 ; quasi nuls au 26/03. |
| **Variable suspecte** | Même frontière **24/03** ; courbe secondaire du 06/03. |
| **Fichier / fonction** | Idem **5061** (même règle `flux_restant` + `tri_first_duration`). |
| **Cause probable** | **Inclusion du trimestre 24/03** au 06/03 vs exclusion au 26/03 + **décalage de courbe**. |
| **Correction proposée** | Analyse métier : **harmoniser** avec Excel le test **strict** sur la date de tombée ; tests de régression sur **deux dates** de part et d’autre du **24/03**. |
| **Confiance** | **Fort**. |

### 5122 — FIX FPCT

| Élément | Contenu |
| --- | --- |
| **Constat** | Tombée **26/03** ; valo **référence** le même jour. |
| **Variable suspecte** | **`cols_dates[i] > d_valo`** exclut la tombée **égale** au jour de valorisation. |
| **Fichier / fonction** | Même `flux_restant` ; `bond_pricing` / durées si besoin pour cohérence Excel. |
| **Cause probable** | **Cas limite « valo = date de coupon »** : flux du jour **hors** NPV au 26/03, **dans** au 06/03. |
| **Correction proposée** | Trancher métier : **inclure** la tombée le jour J si c’est la règle Manar ; sinon documenter. |
| **Confiance** | **Fort** sur le mécanisme **≥ vs >**. |

### 5151 — REV / ZC / TRI / FIN / R/360 / FPCT

| Élément | Contenu |
| --- | --- |
| **Constat** | Plus grand écart FPCT + REV ; **Δ écart** important. |
| **Variable suspecte** | **24/03** + branche **`use_rev_zc_tri_fin_r360`** (durées **R/360** + ZC) + courbe. |
| **Fichier / fonction** | `obligation_amort_schedule.py` (bloc **REV**, `calculer_duree_affichage_rev`, `use_rev_zc_tri_fin_r360`) ; `bond_pricing.py` ; `flux_restant`. |
| **Cause probable** | Même **saut de flux** qu’en FIX + **sensibilité** linéaire / trimestre **R/360** sur le premier flux. |
| **Correction proposée** | Même question d’**inclusivité** de date ; valider **364/360** (champ `methode_coupon`) vs ce que le moteur applique sur les intérêts. |
| **Confiance** | **Fort** sur le **24/03** ; **moyen** sur **364/360** sans lecture détaillée des intérêts SQL. |

---

## 8. Interdits respectés

Aucune correction automatique, aucun hardcode par code, aucun ajustement du prix final, aucune modification des formules dans ce livrable. Les propositions sont **analytiques** et visent à **préserver** la validation du **26/03/2026** une fois la **règle métier** explicitement choisie.
