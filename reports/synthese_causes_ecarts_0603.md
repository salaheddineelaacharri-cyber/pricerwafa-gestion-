# Synthèse métier / technique — causes probables des écarts au 06/03/2026

_Source : lecture de `reports/analyse_ecarts_0603_vs_2603.md` (330 titres comparés, seuil « grand écart » au 06/03 : |écart| > 0,02). Aucune modification de code ni de pricing._

## Contexte

La date **26/03/2026** sert de **référence validée** : les écarts y sont considérés acceptables. Le **06/03/2026** met en évidence **58 titres** hors tolérance, dont **47** avec une **forte variation d’écart** par rapport au 26/03 (|Δ écart| > 0,10 selon le rapport source).

---

## 1. Classement des 58 grands écarts (06/03) par famille

_Règle de classement **exclusive** (chaque titre dans une seule famille, par priorité) : **1)** cas extrême si |écart| ≥ 20 **2)** FPCT ou logique TRI courte (profil contenant `FPCT`, ou `TRI` sans `FPCT` pour les sous-annuels type 9580 / 9689–9757) **3)** BSF + ZC + amortissement annuel **4)** REV « classique » (FIN/AN, sans FPCT ni cas extrême) **5)** FIX/AA sans ZC (obligation classique) **6)** autres (FIX/ZC/AN sur ORD/OBL/SEM, hors BSF)._

| Famille | Nb titres | Exemples de codes | |écart| moyen (approx.) | |écart| max |
| --- | ---: | --- | ---: | ---: |
| **Cas extrêmes isolés** | 6 | 9363, 9576, 5061, 5151, 201657, 201868 | ≈ 317 | **1 595,6** |
| **FPCT / ZC / TRI** | 13 | 5106, 5107, 5116, 5117, 5122, 5156, 5166, 9580, 9689, 9690, 9755, 9756, 9757 | ≈ 3,8 | **11,7** |
| **BSF / ZC / AN** | 6 | 100925, 100948, 100993, 100995, 101005, 101006 | ≈ 0,18 | **0,29** |
| **REV / taux variable** (hors FPCT, hors extrêmes) | 14 | 9070, 9302, 9307, 9390, 9429, 9487, 9488, 9491, 9524, 9538, 9572, 9593, 9606, 9754 | ≈ 0,91 | **1,14** |
| **Obligations classiques FIX / AA** | 1 | 100957 | 0,58 | **0,58** |
| **Autres** (FIX/ZC/AN sur ORD/OBL/SEM, etc.) | 18 | 2151, 9346, 9351, 9395, 9402, 9411, 9424, 9431, 9452, 9473, 9502, 9518, 9626, 9686, 9703, 9707, 9714, 9744 | ≈ 0,16 | **0,47** |
| **Total** | **58** | | | |

### Cause probable par famille

- **Cas extrêmes** : priorité à **données** (SQL, nominal, échéancier, ligne Manar vs moteur) et à un **événement de flux / révision** mal capté ; pour **201657 / 201868 (BDT)**, profil différent du reste du portefeuille → **piste données + traitement BDT** plutôt que courbe seule.
- **FPCT / ZC / TRI** : **courbe ZC** (piliers, interpolation, **premier pas de durée** trimestrielle), **filtrage / pondération des flux** proches de la valorisation, cohérence **R/R vs R/360** selon le titre.
- **BSF / ZC / AN** : écarts **faibles** (souvent < 0,3) → plutôt **sensibilité ZC + arrondis** ou **position sur la courbe** entre les deux dates, moins un bug massif.
- **REV « classique »** : **taux révisé / prochaine date de fixing**, **coupon couru** autour du reset, **courbe AA** (secondaire) ; la **concentration des écarts ~0,7–0,9** sur plusieurs titres suggère un **biais système faible** (même ordre de grandeur) plutôt qu’une erreur isolée.
- **FIX/AA (100957)** : cas **isolé** parmi les BSF à fort écart relatif → **coupon couru** ou **flux / spread** sur ce titre à vérifier en priorité sur la fiche.
- **Autres ZC/AN** : écarts **très modérés** (majoritairement < 0,35) → **courbe + conventions d’arrondi** ; peu prioritaire sauf contrôle global.

---

## 2. Dix titres prioritaires à investiguer

| Rang | Code | Motif de priorité |
| ---: | --- | --- |
| 1 | **9363** | Écart absolu maximal ; OK au 26/03 |
| 2 | **9576** | Second écart extrême ; OK au 26/03 |
| 3 | **5061** | Fort écart + **forte variation** FPCT/ZC/TRI |
| 4 | **201868** | BDT ; écart ~28 ; stable au 26/03 |
| 5 | **201657** | BDT ; écart ~26 ; stable au 26/03 |
| 6 | **5151** | REV + FPCT + ZC ; écart ~24 + variation |
| 7 | **5122** | FPCT ; plus grand écart de la sous-famille « courante » (~11,7) |
| 8 | **5107** | FPCT ; écart ~9,7 ; représentatif |
| 9 | **5116** | FPCT ; écart ~8,5 + variation marquée |
| 10 | **5117** | FPCT ; écart ~5,4 ; complète le lot FPCT pour analyse transversale |

---

## 3. Fiche rapide par titre prioritaire

_Données extraites des tableaux §2 et §3 du rapport source (prix référence = valo Manar, prix calculé = pricer)._

| Code | Type | Prix ref 26/03 | Écart 26/03 | Prix ref 06/03 | Écart 06/03 | Cause probable (non corrective) |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 9363 | REV/AA/AN/FIN/R/R/ORD | 106 940,63 | 0,00 | 108 292,84 | **−1 595,60** | Données / flux / **reset REV** ou incohérence **valo vs SQL** sur le 06/03 |
| 9576 | REV/AA/AN/FIN/R/360/OBL | 100 046,07 | 0,00 | 105 018,41 | **−202,48** | Même famille : **taux variable + convention R/360** ; snapshot SQL ou ligne de valorisation |
| 5061 | FIX/ZC/TRI/TRI/R/R/FPCT | 73 369,22 | +0,03 | 77 915,73 | **−26,94** | **ZC + durées TRI** ; position courbe très différente au 06/03 |
| 201868 | FIX/AA/AN/FIN/R/R/BDT | 101 263,54 | 0,00 | 101 177,29 | **−28,20** | **Profil BDT** : jeu de données ou règle métier BDT vs moteur standard |
| 201657 | FIX/AA/AN/FIN/R/R/BDT | 100 621,83 | 0,00 | 100 529,99 | **−26,23** | Idem BDT |
| 5151 | REV/ZC/TRI/FIN/R/360/FPCT | 100 024,48 | +0,16 | 100 598,47 | **+24,29** | **Combinaison REV + ZC + TRI** : reset, courbe ZC, premier pas trimestriel |
| 5122 | FIX/ZC/TRI/TRI/R/R/FPCT | 40 564,44 | +0,06 | 43 798,95 | **+11,74** | FPCT / **flux futurs** + **courbe ZC** |
| 5107 | FIX/ZC/TRI/TRI/R/R/FPCT | 98 741,41 | 0,00 | 100 905,85 | **+9,68** | Idem FPCT/ZC/TRI |
| 5116 | FIX/ZC/TRI/TRI/R/R/FPCT | 53 193,00 | +0,03 | 56 244,22 | **+8,48** | Idem ; forte variation d’écart |
| 5117 | FIX/ZC/TRI/TRI/R/R/FPCT | 80 421,76 | +0,01 | 83 081,38 | **+5,41** | Idem |

---

## 4. Conclusion (orientation décision)

Il ne s’agit **pas d’une cause unique**. On observe **plusieurs familles de problèmes** :

1. **Deux anomalies dominantes (9363, 9576)** : ordre de grandeur incompatible avec un simple décalage de courbe ou d’arrondi → **piste « données SQL / ligne Manar / logique REV (flux, fixing, nominal) »** en premier, avant toute retouche de formules.
2. **Un lot structurant FPCT / ZC / TRI (5061, 5107–5117, 5122, 5151, etc.)** : cohérent avec des questions de **courbe ZC**, de **calcul des durées** (surtout premier trimestre) et d’**actualisation des flux** — c’est la **deuxième grande famille**, alignée sur le constat « correct au 26/03, dégradé au 06/03 ».
3. **Deux titres BDT** avec écarts ~26–28 alors qu’ils étaient parfaitement alignés au 26/03 → **problème probable de périmètre données ou de branche métier BDT**, pas seulement la courbe marché.
4. **Coupon couru** : pertinent surtout pour **100957** (seul FIX/AA « classique » hors tolérance) et, de façon transversale, pour les **REV** autour des dates de tombée — mais la **homogénéité ~0,8** sur beaucoup de REV suggère aussi un **petit biais commun** (courbe secondaire, convention, ou arrondi de chaîne).
5. **Les 18 « autres » ZC/AN** : écarts faibles → **bruit de courbe / d’arrondi** plausible ; **faible priorité** sauf audit global.

**En synthèse** : investiguer d’abord **données & REV (extrêmes + BDT)**, en parallèle **FPCT/ZC/TRI (courbe + durées + flux)** ; la **courbe seule** n’explique pas l’ensemble ; le **coupon couru** est une **piste secondaire ciblée** plutôt que la cause principale du volume des 58 lignes.
