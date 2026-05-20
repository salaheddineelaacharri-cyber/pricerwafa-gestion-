# Investigation REV / taux variable — écarts au 06/03/2026 vs référence 26/03/2026

_Document d’analyse uniquement : aucune correction appliquée, aucun hardcode ajouté, pas d’export JSON volumineux. Données chiffrées issues de `reports/analyse_ecarts_0603_vs_2603.md` ; complément SQL via `dbo.referentiel_titre` et `dbo.echeancier_titre` (requêtes ponctuelles)._

---

## 1. Périmètre REV « grands écarts » au 06/03

Titres **REV** dépassant |écart| > 0,02 au **06/03/2026** dans le rapport d’analyse : **16** codes — les **14** listés comme « REV classique » dans `reports/synthese_causes_ecarts_0603.md` **+ 9363 + 9576** (cas extrêmes, hors lot homogène ~0,76–0,93).

---

## 2. Données agrégées (tous les REV à grand écart)

| Code | Profil | Prix ref 26/03 | Prix calc 26/03 | Écart 26/03 | Prix ref 06/03 | Prix calc 06/03 | Écart 06/03 | Δ écart |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 9363 | REV/AA/AN/FIN/R/R/ORD | 106 940,63 | 106 940,63 | 0,00 | 108 292,84 | 106 697,24 | **−1 595,60** | −1 595,60 |
| 9576 | REV/AA/AN/FIN/R/360/OBL | 100 046,07 | 100 046,07 | 0,00 | 105 018,41 | 104 815,93 | **−202,48** | −202,48 |
| 9070 | REV/AA/AN/FIN/R/R/ORD | 101 775,64 | 101 775,64 | 0,00 | 101 572,56 | 101 573,17 | +0,61 | +0,61 |
| 9302 | REV/AA/AN/FIN/R/R/ORD | 100 789,09 | 100 789,09 | 0,00 | 100 658,16 | 100 658,95 | +0,79 | +0,79 |
| 9307 | REV/AA/AN/FIN/R/R/ORD | 100 960,41 | 100 960,41 | 0,00 | 100 791,96 | 100 792,76 | +0,80 | +0,80 |
| 9390 | REV/AA/AN/FIN/R/R/OBL | 101 024,82 | 101 024,82 | 0,00 | 100 823,48 | 100 824,28 | +0,80 | +0,80 |
| 9429 | REV/AA/AN/FIN/R/R/ORD | 101 013,28 | 101 013,28 | 0,00 | 100 814,58 | 100 815,38 | +0,80 | +0,80 |
| 9487 | REV/AA/AN/AN/R/360/OBL | 75 765,34 | 75 765,36 | +0,02 | 75 650,48 | 75 649,45 | −1,03 | −1,05 |
| 9488 | REV/AA/AN/FIN/R/360/OBL | 101 073,02 | 101 073,02 | 0,00 | 100 868,58 | 100 869,37 | +0,79 | +0,79 |
| 9491 | REV/AA/AN/FIN/R/360/OBL | 100 609,72 | 100 609,72 | 0,00 | 100 499,55 | 100 500,36 | +0,81 | +0,81 |
| 9524 | REV/AA/AN/FIN/R/R/ORD | 100 788,03 | 100 788,03 | 0,00 | 100 567,39 | 100 568,17 | +0,78 | +0,78 |
| 9538 | REV/ZC/SEM/SEM/R/360/OBL | 12 601,84 | 12 601,09 | −0,75 | 12 582,99 | 12 581,85 | −1,14 | −0,39 |
| 9572 | REV/AA/AN/FIN/R/R/ORD | 100 438,35 | 100 438,35 | 0,00 | 100 245,40 | 100 246,33 | +0,93 | +0,93 |
| 9593 | REV/AA/AN/FIN/R/360/ORD | 100 748,68 | 100 748,68 | 0,00 | 100 636,99 | 100 637,76 | +0,77 | +0,77 |
| 9606 | REV/AA/AN/AN/R/360/OBL | 100 551,75 | 100 551,75 | 0,00 | 100 455,07 | 100 455,88 | +0,81 | +0,81 |
| 9754 | REV/AA/AN/FIN/R/360/OBL | 101 024,41 | 101 024,41 | 0,00 | 100 863,81 | 100 864,57 | +0,76 | +0,76 |

---

## 3. Classement par famille (REV uniquement)

| Famille | Nb | Exemples | \|écart\| moy. (06/03) | \|écart\| max | Lecture métier |
| --- | ---: | --- | ---: | ---: | --- |
| **Cas extrêmes** | 2 | 9363, 9576 | ≈ 899 | 1 595,6 | Hors gabarit « bruit de courbe » ; investigation données + logique de tombée. |
| **REV / AA / R/R / ORD** | 6 | 9070, 9302, 9307, 9429, 9524, 9572 | ≈ 0,80 | 0,93 | Écarts **homogènes ~+0,61 à +0,93** : piste **biais commun** (courbe AA, spread, arrondis). |
| **REV / AA / R/360 / OBL ou ORD** | 6 | 9488, 9491, 9593, 9606, 9754, 9487 | ≈ 0,85 | 1,03 | Même ordre de grandeur ; **9487** un peu plus marqué (profil AN/AN). |
| **REV / ZC / SEM** | 1 | 9538 | 1,14 | 1,14 | Branche **ZC + semestriel** ; logique d’actualisation distincte des REV AA. |
| **REV / AA / R/R / OBL** | 1 | 9390 | 0,80 | 0,80 | Aligné sur le cluster ORD. |

---

## 4. Logique code — REV / taux variable (rappel ciblé)

| Thème | Comportement principal | Fichiers / symboles |
| --- | --- | --- |
| **Indice de la prochaine révision** `i_rev` | Première colonne d’échéancier avec **date > date de valorisation** ; définit `flux[i_rev]`, `capital_restant_fin_periode[i_rev]`, durée jusqu’à `cols_dates[i_rev]`. | `obligation_amort_schedule.py` (recherche `i_rev`), `calculate_rev_bond_price` (import `bond_pricing` ou définition locale). |
| **Taux de référence REV** | Cas général : `_taux_courbe_rev_pour_colonne` avec **maturité résiduelle** (dernière tombée − valo) et `taux_secondaire_a_j` (AA) ou ZC selon `METHODE_VALO` ; branches spécifiques pour combinaisons périodicité / base (ex. `use_rev_aa_tri_fin_r360`, `use_rev_full_flux_zc_rr_an`, …). | `obligation_amort_schedule.py` (~L1775–2025). |
| **Formule prix clean** | **AA + R/360** (sous-ensembles) : souvent **linéaire** `/(1 + r × t)` ; **ZC** : **puissance** `/(1+r)^t` ; chemin **ACT/360 jours/360** dans `prix_rev_lineaire_act360` / `calculate_rev_bond_price`. | `backend/app/services/bond_pricing.py` (`prix_rev_lineaire_act360`, `calculate_rev_bond_price`, `calculer_duree_affichage_rev`). |
| **Spread** | Lu depuis la valorisation puis `_spread_depuis_ref` ; arrondi prime type Excel. | `obligation_amort_schedule.py`, `valuation_zc_obligations.py` (`spread_decimal_arrondi_prime_pct3`, `normaliser_spread_emission`). |
| **Coupon couru** | `_coupon_couru_schedule(d_valo, cols_dates, interets)` après construction des **intérêts** de ligne (dépend des flux SQL / règles période). | `obligation_amort_schedule.py` (vers fin de `construire_tableau_amortissement`). |
| **Courbe** | Piliers issus de **`dbo.histo_courbe_taux`** (ex. `MAR_JJ`) pour la date de valorisation demandée. | `backend/main.py` (`charger_histo_courbe_taux`), `pricing/data_access.py`. |
| **Données titre** | Référentiel + échéancier SQL ; taux facial via `VALEUR_TAUX` / `_taux_coupon_depuis_ref`. | `pricing/data_access.py` (`charger_referentiel_titre`, `charger_echeancier_titre`), `valuation_zc_obligations.py` (pipeline `valoriser_dataframe_base_titre`). |

**Note gouvernance** : le fichier `obligation_amort_schedule.py` contient encore des **branches conditionnelles par code** (ex. 9487, 5156, 5166, 5119, 9408) pour certains cas REV / ZC. **9363 et 9576** passent par la **logique générique** documentée ci-dessus (pas ces overrides).

---

## 5. Données SQL observées (investigation ciblée)

### 5.1 `dbo.referentiel_titre`

| Code | Éléments saillants |
| --- | --- |
| **9363** | REV, **AA**, coupon **R/R**, remb. **FIN**, base **R/R**, `valeur_taux` **4,72%**, spread **106,6** (champ `spread_emission`), échéance **2033-05-14**, `date_revision` **2033-05-14**, courbe **MAR_JJ**. |
| **9576** | REV, **AA**, `methode_coupon` **366/360**, remb. **FIN**, base **R/360**, `valeur_taux` **4,90%** (libellé mentionne 5,91 % révisable — **vérifier cohérence** titre / coupon SQL), spread **250**, `date_revision` **2027-03-17**, échéance **2033-03-17**. |

### 5.2 `dbo.echeancier_titre`

| Code | Fait structurant pour la comparaison des dates |
| --- | --- |
| **9363** | Tombées annuelles **14 mai** ; la première date **strictement postérieure** au **06/03/2026** et au **26/03/2026** reste **2026-05-14** (même événement, coupon **4 720**). **Pas de saut d’indice `i_rev` entre les deux dates de valorisation** d’après l’échéancier actuel. |
| **9576** | Tombées autour du **17 mars** ; ligne **2026-03-17** (coupon **5 170,83**), puis **2027-03-17** (coupon **4 968,06**). Le **06/03/2026** est **avant** le 17/03/2026 ; le **26/03/2026** est **après** le 17/03/2026. Donc **la prochaine tombée « future » change de ligne d’échéancier** : **2026-03-17** vs **2027-03-17** → flux, coupon et **horizon de duration** différents. |

### 5.3 `dbo.histo_courbe_taux`

Présence d’enregistrements pour la courbe **MAR_JJ** aux dates **2026-03-06** et **2026-03-26** (jeu de données non vide). Les taux interpolés **changent** entre les deux dates → impact attendu sur `taux_secondaire_a_j` et sur le prix, **en plus** de l’effet tombée pour 9576.

---

## 6. Comparaison 06/03 vs 26/03 — ce qui change (par titre prioritaire)

### 6.1 **9576** — confiance **forte**

| Variable | 06/03/2026 | 26/03/2026 | Commentaire |
| --- | --- | --- | --- |
| **Prochaine tombée > valo** | **2026-03-17** | **2027-03-17** | Passage **coupon de mars 2026** encore à payer vs **coupon de mars 2027** comme premier flux. |
| **Flux + capital cible REV** | Coupon **5 170,83** (+ encours 100k jusqu’à maturité) | Coupon **4 968,06** (+ 100k) | Montants et dates **très différents**. |
| **Durée / jours jusqu’à flux** | ~**11** jours | ~**356** jours | Formule **linéaire R/360** hypersensible au numérateur/dénominateur sur courte maturité. |
| **Courbe** | Piliers **06/03** | Piliers **26/03** | Déformation du taux AA interpolé. |
| **Taux facial réf.** | Inchangé en base (ligne SQL) | Idem | `valeur_taux` 4,9 % — écart éventuel libellé / coupon à contrôler métier. |

**Variable suspecte** : **sélection de l’indice `i_rev` (frontière de coupon au 17/03)** + interaction **R/360 + 366/360**.  
**Fonctions suspectes** : recherche `i_rev` dans `construire_tableau_amortissement` ; `calculer_duree_affichage_rev` / `prix_rev_lineaire_act360` dans `bond_pricing.py` ; `_taux_courbe_rev_pour_colonne`.  
**Cause probable** : comportement **attendu du modèle** lorsque la valorisation **traverse une date de coupon** ; l’écart massif vs Manar au 06/03 peut combiner ce **saut de flux** et une **valo de référence (Manar)** construite avec une **hypothèse différente** (ex. même prochain flux que le 26/03, ou autre convention).  
**Correction proposée (non appliquée)** : documenter / aligner la **règle métier** « jour de coupon » (inclus/exclus) avec Manar ; vérifier que la **ligne Prix Manar** au 06/03 utilise bien la **même définition de prochain flux** ; si Manar fige un autre scénario, **ajuster la spec** plutôt que patcher un code titre.  
**Niveau de confiance** : **fort** sur le rôle de la **tombée du 17/03/2026**.

### 6.2 **9363** — confiance **moyenne à forte** sur la piste « référence + courbe », **moyenne** sur la cause unique

| Variable | 06/03 vs 26/03 | Commentaire |
| --- | --- | --- |
| **Prochaine tombée > valo** | **2026-05-14** dans les deux cas | Pas d’effet « saut de coupon » de type 9576. |
| **Courbe MAR_JJ** | Différente entre les deux dates | Fait baisser le prix calculé au 06/03 alors que le 26/03 était aligné. |
| **Prix référence Manar** | **+1 352** entre les deux dates | La **valo Manar** monte fortement au 06/03 alors que le **calcul** baisse légèrement → **écart** explosif. |
| **Données SQL échéancier** | Stable (coupon 4 720 annuel) | Peu probable que l’écart vienne d’un changement de ligne d’échéancier entre les deux extractions. |

**Variable suspecte** : **divergence valo Manar vs sortie moteur** + **sensibilité courbe** ; secondaire : **spread** (106,6 en base — vérifier unité / normalisation dans le pipeline).  
**Fichiers suspectés** : `backend/main.py` (`_valoriser_prix_manarr_rows`, lecture `prix mar.xlsx`) ; `obligation_amort_schedule.py` (bloc REV + spread) ; interpolation `taux_secondaire_a_j`.  
**Cause probable** : **jeu de données de référence (fichier Manar)** ou **courbe du 06/03** non cohérente avec l’hypothèse Manar ; peu probable seul bug d’`i_rev` vu l’échéancier.  
**Correction proposée (non appliquée)** : contrôle **ligne à ligne** `prix mar.xlsx` (titre, date, valo) pour 9363 ; rejeu avec **même** jeu de courbe qu’au 26/03 pour isoler l’effet « courbe seule » ; audit **spread** réellement injecté dans `spread_decimal_valo`.  
**Niveau de confiance** : **moyen** sur la part exacte Manar vs courbe ; **fort** sur l’absence de saut de tombée type 9576.

### 6.3 **Lot ~0,76–0,93 (REV AA FIN R/R ou R/360)** — confiance **moyenne**

| Variable | Lecture |
| --- | --- |
| **Courbe** | Déplacement CT entre 06/03 et 26/03 → décalage **systématique** du même signe possible sur un panier homogène. |
| **Spread / arrondis** | Même spread référentiel + arrondis **prime 3 déc.** → petit biais commun. |
| **Coupon couru** | `_coupon_couru_schedule` dépend de `d_valo` ; variation **limitée** si pas de traversée de coupon (sauf titres proches d’une tombée). |

**Cause probable** : **courbe secondaire + conventions d’arrondi** + léger **déphasage de dates** sur titres proches d’une tombée.  
**Correction proposée (non appliquée)** : profil type sur **un** titre du lot (ex. 9572) : dump UI des **taux AA** utilisés et **coupon couru** aux deux dates pour confirmer.  
**Niveau de confiance** : **moyen**.

### 6.4 **9538 (REV / ZC / SEM)** — confiance **moyenne**

Branche **ZC** pour REV (actualisation puissance, échéancier ZC) + **semestriel** → autre surface de code que le cluster AA. **Cause probable** : **courbe ZC** + **durées semestrielles** ; pas le même mécanisme que 9576.  
**Fichiers** : `obligation_amort_schedule.py` (`use_rev_full_flux_zc_rr_an`, `use_zc_courbe`, boucle flux REV ZC).  
**Niveau de confiance** : **moyen**.

---

## 7. Synthèse décisionnelle

- **Plusieurs causes**, pas une seule :  
  1) **Frontière de date de coupon** (cas **9576**, **fort**) ;  
  2) **Écart de référence / courbe** entre Manar et moteur, amplifié sur **9363** où la **valo Manar** bouge beaucoup (**moyen** à **moyen-fort**) ;  
  3) **Lot homogène ~0,8** → plutôt **courbe + spread + arrondis** (**moyen**) ;  
  4) **9538** → piste **REV ZC semestriel** (**moyen**).

- **Ce n’est pas « uniquement coupon couru »** : il est pertinent pour les titres **à cheval sur une tombée** (9576) et en support pour le lot faible, mais **n’explique pas seul** 9363 ni l’homogénéité du cluster ORD.

- **Données SQL** : pour **9576**, les lignes `echeancier_titre` **expliquent mécaniquement** le changement de comportement entre les deux dates de valorisation. Pour **9363**, le SQL est **stable** sur la prochaine tombée ; la piste **Manar + courbe** prime.

---

## 8. Requêtes SQL utiles pour la suite (sans les exécuter ici en masse)

```sql
-- Référentiel ciblé
SELECT code, type_taux, valeur_taux, methode_coupon, periodicite_coupon, periodicite_rembou,
       base_calcul, methode_valo, spread_emission, date_echeance, date_revision
FROM dbo.referentiel_titre WHERE code IN ('9363','9576', …);

-- Échéancier : repérer la première date_reglement > @date_valo
SELECT titre, num_evenement, date_reglement, coupon_brut, capital_amortis
FROM dbo.echeancier_titre WHERE titre = '9576' ORDER BY date_reglement;

-- Courbe du jour
SELECT * FROM dbo.histo_courbe_taux WHERE courbe = 'MAR_JJ' AND date_courbe = '2026-03-06';
```

---

## 9. Interdits respectés

Aucune modification de formules, aucun hardcode par code titre, aucun ajustement du prix final, aucun gros export JSON ; le rapport ne prescrit pas de changement qui **casse** la validation du **26/03/2026** sans analyse métier ultérieure.
