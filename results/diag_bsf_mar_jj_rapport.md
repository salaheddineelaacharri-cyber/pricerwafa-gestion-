# Diagnostic MAR_JJ — profil BSF ZC (sans modification du moteur)
**Dates de valorisation :** 2026-03-26, 2026-03-06, 2026-01-02  
**Profil cible (référence) :** `FIX/ZC/AN/AN/R/R/BSF`  
**Mode test G2 :** ajout d’un pilier CT synthétique à **326.0 j** (taux MM = extrapolation linéaire VBA des piliers CT SQL) lorsque `max(CT) < 326`.  
---
## 1. Extraction MAR_JJ par date (SQL)

### 1.1 Courbe SQL — **26/03/2026**
| Champ | Valeur |
|---|---|
| date_valorisation | 2026-03-26 |
| date_courbe_sql | 2026-03-26 |
| joint_long_day (G2) | 326.0 |
| joint_days (G2 − 1) | 325.0 |

**Piliers CT (≤ 365 j, taux % MM)**

| Maturité (j) | Taux (%) |
|---:|---:|
| 1 | 2.27 |
| 53 | 2.27 |
| 144 | 2.34 |
| 326 | 2.46 |

**Piliers LT (> 365 j, taux % actuariel)**

| Maturité (j) | Taux (%) |
|---:|---:|
| 543 | 2.59 |
| 1481 | 2.98 |
| 1845 | 3.08 |
| 3371 | 3.28 |
| 4862 | 3.51 |
| 7081 | 3.65 |
| 10616 | 4.08 |

### 1.2 Courbe SQL — **06/03/2026**
| Champ | Valeur |
|---|---|
| date_valorisation | 2026-03-06 |
| date_courbe_sql | 2026-03-06 |
| joint_long_day (G2) | 192.0 |
| joint_days (G2 − 1) | 191.0 |

**Piliers CT (≤ 365 j, taux % MM)**

| Maturité (j) | Taux (%) |
|---:|---:|
| 1 | 2.25 |
| 73 | 2.25 |
| 164 | 2.29 |
| 192 | 2.3 |

**Piliers LT (> 365 j, taux % actuariel)**

| Maturité (j) | Taux (%) |
|---:|---:|
| 374 | 2.43 |
| 1501 | 2.74 |
| 1865 | 2.82 |
| 3391 | 3.03 |
| 4882 | 3.27 |
| 7101 | 3.62 |
| 10636 | 4.0 |

### 1.3 Courbe SQL — **02/01/2026**
| Champ | Valeur |
|---|---|
| date_valorisation | 2026-01-02 |
| date_courbe_sql | 2026-01-02 |
| joint_long_day (G2) | 255.0 |
| joint_days (G2 − 1) | 254.0 |

**Piliers CT (≤ 365 j, taux % MM)**

| Maturité (j) | Taux (%) |
|---:|---:|
| 1 | 2.21 |
| 73 | 2.21 |
| 136 | 2.23 |
| 255 | 2.25 |

**Piliers LT (> 365 j, taux % actuariel)**

| Maturité (j) | Taux (%) |
|---:|---:|
| 437 | 2.38 |
| 1564 | 2.58 |
| 1928 | 2.65 |
| 3454 | 2.94 |
| 4945 | 3.21 |
| 7164 | 3.49 |
| 10699 | 3.89 |


## 2. Tableaux comparatifs par date (7 titres BSF)

### Valorisation **2026-03-26** (G2 SQL = **326.0** j)

| Titre | Profil ref. | Mr (j) | Zone Mr | Transition (flux) | Taux 2nd MR (%)* | Taux ZC act. MR (%)* | Prix SQL | Prix G2=326 | Valo fichier | Écart SQL−fichier | Écart G326−fichier |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 101005 | FIX/ZC/AN/AN/R/R/BSF | 2330 | LT | non | 3.1436 | 3.1624 | 100111.9835 | 100111.9835 | 100111.9700 | 0.0135 | 0.0135 |
| 100993 | FIX/ZC/AN/AN/R/R/BSF | 1540 | LT | non | 2.9962 | 3.0065 | 101089.8485 | 101089.8485 | 101089.8500 | -0.0015 | -0.0015 |
| 100948 | FIX/ZC/AN/AN/R/R/BSF | 845 | LT | non | 2.7156 | 2.7188 | 77169.9442 | 77169.9442 | 77169.9500 | -0.0058 | -0.0058 |
| 100937 | FIX/ZC/AN/AN/R/R/BSF | 395 | LT | non | 2.5269 | 2.5271 | 69225.8144 | 69225.8144 | 69225.8100 | 0.0044 | 0.0044 |
| 100995 | FIX/ZC/AN/AN/R/R/BSF | 1545 | LT | non | 2.9976 | 3.0080 | 101114.6730 | 101114.6730 | 101114.6800 | -0.0070 | -0.0070 |
| 100925 | FIX/ZC/AN/AN/R/R/BSF | 299 | CT | non | 2.4422 | 2.4815 | 33738.3134 | 33738.3134 | 33738.3300 | -0.0166 | -0.0166 |
| 101006 | FIX/ZC/AN/AN/R/R/BSF | 1600 | LT | non | 3.0127 | 3.0242 | 100577.6813 | 100577.6813 | 100577.6800 | 0.0013 | 0.0013 |

*« Taux 2nd » = Formule B (BAM) au **Mr** ; « Taux ZC act. » = colonne **TauxZCActuariel** de l’échéancier tracé au **Mr** (jours), comme référence univoque.

**Paramètres courbe cette date :** `mm_cutoff` SQL = **326** j ; avec test G2 = **326** j.

### Valorisation **2026-03-06** (G2 SQL = **192.0** j)

| Titre | Profil ref. | Mr (j) | Zone Mr | Transition (flux) | Taux 2nd MR (%)* | Taux ZC act. MR (%)* | Prix SQL | Prix G2=326 | Valo fichier | Écart SQL−fichier | Écart G326−fichier |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 101005 | FIX/ZC/AN/AN/R/R/BSF | 2350 | LT | non | 2.8867 | 2.9010 | 100614.9720 | 100615.7894 | 100615.0500 | -0.0780 | 0.7394 |
| 100993 | FIX/ZC/AN/AN/R/R/BSF | 1560 | LT | non | 2.7530 | 2.7593 | 101312.1007 | 101312.8947 | 101312.3900 | -0.2893 | 0.5047 |
| 100948 | FIX/ZC/AN/AN/R/R/BSF | 865 | LT | non | 2.5651 | 2.5672 | 77165.0982 | 77166.1224 | 77165.1900 | -0.0918 | 0.9324 |
| 100937 | FIX/ZC/AN/AN/R/R/BSF | 415 | LT | non | 2.4413 | 2.4400 | 69151.6340 | 69153.1135 | 69151.6400 | -0.0060 | 1.4735 |
| 100995 | FIX/ZC/AN/AN/R/R/BSF | 1565 | LT | non | 2.7541 | 2.7605 | 101339.4829 | 101339.5417 | 101339.7300 | -0.2471 | -0.1883 |
| 100925 | FIX/ZC/AN/AN/R/R/BSF | 319 | transition | oui | 2.3678 | 2.4047 | 33705.7990 | 33712.6708 | 33706.0900 | -0.2910 | 6.5808 |
| 101006 | FIX/ZC/AN/AN/R/R/BSF | 1620 | LT | non | 2.7662 | 2.7734 | 100835.5881 | 100836.6717 | 100835.6900 | -0.1019 | 0.9817 |

*« Taux 2nd » = Formule B (BAM) au **Mr** ; « Taux ZC act. » = colonne **TauxZCActuariel** de l’échéancier tracé au **Mr** (jours), comme référence univoque.

**Paramètres courbe cette date :** `mm_cutoff` SQL = **192** j ; avec test G2 = **326** j.

### Valorisation **2026-01-02** (G2 SQL = **255.0** j)

| Titre | Profil ref. | Mr (j) | Zone Mr | Transition (flux) | Taux 2nd MR (%)* | Taux ZC act. MR (%)* | Prix SQL | Prix G2=326 | Valo fichier | Écart SQL−fichier | Écart G326−fichier |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 101005 | FIX/ZC/AN/AN/R/R/BSF | 2413 | LT | non | 2.7422 | 2.7562 | 100509.3653 | 100511.7209 | 100510.5600 | -1.1947 | 1.1609 |
| 100993 | FIX/ZC/AN/AN/R/R/BSF | 1623 | LT | non | 2.5913 | 2.5979 | 101066.9219 | 101070.1124 | 101067.6100 | -0.6881 | 2.5024 |
| 100948 | FIX/ZC/AN/AN/R/R/BSF | 928 | LT | non | 2.4671 | 2.4689 | 76869.6002 | 76873.9535 | 76870.9000 | -1.2998 | 3.0535 |
| 100937 | FIX/ZC/AN/AN/R/R/BSF | 478 | LT | non | 2.3873 | 2.3718 | 68852.6948 | 68858.2151 | 68852.9100 | -0.2152 | 5.3051 |
| 100995 | FIX/ZC/AN/AN/R/R/BSF | 1628 | LT | non | 2.5923 | 2.5988 | 101097.9692 | 101100.9143 | 101098.3600 | -0.3908 | 2.5543 |
| 100925 | FIX/ZC/AN/AN/R/R/BSF | 382 | LT | non | 2.3525 | 2.3484 | 69300.2913 | 69306.0977 | 69300.3000 | -0.0087 | 5.7977 |
| 101006 | FIX/ZC/AN/AN/R/R/BSF | 1683 | LT | non | 2.6029 | 2.6096 | 100614.2788 | 100617.6890 | 100615.8700 | -1.5912 | 1.8190 |

*« Taux 2nd » = Formule B (BAM) au **Mr** ; « Taux ZC act. » = colonne **TauxZCActuariel** de l’échéancier tracé au **Mr** (jours), comme référence univoque.

**Paramètres courbe cette date :** `mm_cutoff` SQL = **255** j ; avec test G2 = **326** j.

---
## 3. Corrélation écarts / zone transition (sous G2 … 365 j)

Pour chaque titre, la colonne **Transition (flux)** = au moins un flux futur avec G2 &lt; j ≤ 365 j (j = jours jusqu’à la tombée). Sur des amortissements annuels multi-années, la transition est en général **touchée** dès que G2 &lt; 365 et qu’il existe des tombées dans l’intervalle **]G2 , 365]** jours.

Les écarts **ne sont pas** réservés aux seuls titres dont **Mr** est dans la transition : le prix agrège **tous** les flux — un décalage du taux sur une tombée en transition change le NPV même si Mr est en LT.

---
## 4. Conclusion (lecture métier)

1. **Table `histo_courbe_taux` ?**  
   - Si les **maturités CT** (piliers ≤ 365) **varient** selon `DATE_COURBE` (ex. dernier CT 192 j le 06/03 et 326 j le 26/03), ce n’est **pas** une erreur SQL en soi : c’est le **jeu de piliers BAM** stocké pour ce jour.  
   - **Problème possible** : jeu **incomplet** ou **incohérent** vs la publication BAM officialisée (il manque un pilier CT attendu, ou les taux ne correspondent pas à la circulaire du jour).

2. **Règle `joint_long_day` / G2 ?**  
   - Dans le moteur, **G2 = max(maturités CT)** des piliers SQL ; la **zone de transition** (Entre G2 et 365 j) utilise la **rampe monétaire** Excel. Changer G2 change **tous** les taux ZC actuariels interpolés sur ces maturités.

3. **Fichier Prix Manar calé sur une autre courbe ?**  
   - **Test de sensibilité** (pilier MM synthétique à 326 j, extrapolé depuis les CT SQL) :
     - **2026-03-06** — vs Valo fichier : |écart| **réduit** avec pilier synth. 326 j : 100995 ; **augmenté** : 101005, 100993, 100948, 100937, 100925, 101006.
     - **2026-01-02** — vs Valo fichier : |écart| **réduit** avec pilier synth. 326 j : 101005 ; **augmenté** : 100993, 100948, 100937, 100995, 100925, 101006.
   - Ce test **ne** reproduit pas une publication BAM officielle : il modifie artificiellement G2. Les effets sont **mixtes** (certaines lignes se rapprochent, d’autres **s’éloignent**). Il n’y a **pas** de preuve que toute la Valo fichier soit passée sous une courbe « G2=326 » unique. Des **écarts du même signe** sur plusieurs BSF pour une `DATE_COURBE` donnée orientent plutôt vers une question de **niveaux de taux** ou de **grille CT/LT** dans SQL vs la référence métier utilisée pour le fichier.

4. **Correction sans hardcode titre par titre**  
   - **Aligner** le contenu de `dbo.histo_courbe_taux` pour chaque `DATE_COURBE` sur la **grille CT/LT** BAM attendue (mêmes maturités et taux que la circulaire / export Manar).  
   - **Ou** documenter que la **Valo fichier** est figée pour une **courbe de référence** (ex. dernier CT à 326 j) et fournir cette grille en import SQL — **sans** surcoût par titre.  
   - **Éviter** de forcer G2=326 en dur dans le code : à traiter au niveau **données** (complétude des piliers CT) ou **date de courbe** unique contractuelle.

_Rapport généré par `scripts/diag_bsf_mar_jj_complet.py` — 2026-05-12T17:09:39_
