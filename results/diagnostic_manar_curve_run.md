# Diagnostic Manar × courbe

- Tolérance absolue Manar : 0.02

|code|date|Prix_Manar|Prix_SQL_moteur|Prix_WG_piliers_excel|Δ_Manar_vs_SQL|Δ_Manar_vs_WG|Mr|Taux_interp_MR_pct|pilier_-j|pilier_+j|G2_joint|src_piliers_SQL|type|cause|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 100993 | 2026-01-02 | 101067.6100 | 101066.9219 | N/A | -0.6881 |  | 1623 | 2.597865 | 1461.0 | 1826.0 | 255.0 | MAR_JJ | amort/ATP/METH_ZC | Écart Manar hors tolérence (-0.6881). Pas de WG calé à cette date : Manar fichier possiblement basé sur un autre G2/grille. Date WG=2026-03-26 ≠ valeur test 2026-01-02 |
| 101005 | 2026-01-02 | 100510.5600 | 100509.3653 | N/A | -1.1947 |  | 2413 | 2.756217 | 2192.0 | 2557.0 | 255.0 | MAR_JJ | amort/ATP/METH_ZC | Écart Manar hors tolérence (-1.1947). Pas de WG calé à cette date : Manar fichier possiblement basé sur un autre G2/grille. Date WG=2026-03-26 ≠ valeur test 2026-01-02 |
| 100993 | 2026-03-06 | 101312.3900 | 101312.1007 | N/A | -0.2893 |  | 1560 | 2.759305 | 1461.0 | 1826.0 | 192.0 | MAR_JJ | amort/ATP/METH_ZC | Écart Manar hors tolérence (-0.2893). Pas de WG calé à cette date : Manar fichier possiblement basé sur un autre G2/grille. Date WG=2026-03-26 ≠ valeur test 2026-03-06 |
| 101005 | 2026-03-06 | 100615.0500 | 100614.9720 | N/A | -0.0780 |  | 2350 | 2.901028 | 2192.0 | 2557.0 | 192.0 | MAR_JJ | amort/ATP/METH_ZC | Écart Manar hors tolérence (-0.0780). Pas de WG calé à cette date : Manar fichier possiblement basé sur un autre G2/grille. Date WG=2026-03-26 ≠ valeur test 2026-03-06 |
| 100993 | 2026-03-26 | 101089.8500 | 101089.8485 | 101089.8485 | -0.0015 | -0.0015 | 1540 | 3.006516 | 1461.0 | 1826.0 | 326.0 | MAR_JJ | amort/ATP/METH_ZC | Écart résiduel ≤ tolérance Manar. Incohérence joint/piliers WG/SQL probable écart Manar hors 26/03. Δ joint G2: SQL=326.0 vs WG=543.0. |
| 101005 | 2026-03-26 | 100111.9700 | 100111.9835 | 100111.9835 | +0.0135 | +0.0135 | 2330 | 3.162378 | 2192.0 | 2557.0 | 326.0 | MAR_JJ | amort/ATP/METH_ZC | Écart résiduel ≤ tolérance Manar. Incohérence joint/piliers WG/SQL probable écart Manar hors 26/03. Δ joint G2: SQL=326.0 vs WG=543.0. |