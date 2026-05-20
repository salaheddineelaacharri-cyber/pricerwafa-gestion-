# Diagnostic hybride CT / LT — CODE **9424**

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

## 2026-01-02

**Erreur** : `HTTPException` — 503: SQL Server indisponible: SQL Server est indisponible ou inaccessible (localhost\SQLEXPRESS, DESKTOP-5K88T8O\SQLEXPRESS / obligation): ('08001', "[08001] [Microsoft][ODBC Driver 17 for SQL Server]Chiffrement non pris en charge sur le client. (21) (SQLDriverConnect); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Fournisseur SSL : Aucune information d’identification n’est disponible dans le package de sécurité\r\n (-2146893042); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Le client n'a pas pu établir la connexion (21); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Attribut de chaîne de connexion non valide (0); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Une erreur liée au réseau ou spécifique à l'instance s'est produite lors de l'établissement d'une connexion à SQL Server. Le serveur est introuvable ou n'est pas accessible. Vérifiez si le nom de l'instance est correct et si SQL Server est configuré pour autoriser les connexions distantes. Pour plus d'informations, consultez la documentation en ligne de SQL Server. (-2146893042)")

## 2026-03-06

**Erreur** : `HTTPException` — 503: SQL Server indisponible: SQL Server est indisponible ou inaccessible (localhost\SQLEXPRESS, DESKTOP-5K88T8O\SQLEXPRESS / obligation): ('08001', "[08001] [Microsoft][ODBC Driver 17 for SQL Server]Chiffrement non pris en charge sur le client. (21) (SQLDriverConnect); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Fournisseur SSL : Aucune information d’identification n’est disponible dans le package de sécurité\r\n (-2146893042); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Le client n'a pas pu établir la connexion (21); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Attribut de chaîne de connexion non valide (0); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Une erreur liée au réseau ou spécifique à l'instance s'est produite lors de l'établissement d'une connexion à SQL Server. Le serveur est introuvable ou n'est pas accessible. Vérifiez si le nom de l'instance est correct et si SQL Server est configuré pour autoriser les connexions distantes. Pour plus d'informations, consultez la documentation en ligne de SQL Server. (-2146893042)")

## 2026-03-26

**Erreur** : `HTTPException` — 503: SQL Server indisponible: SQL Server est indisponible ou inaccessible (localhost\SQLEXPRESS, DESKTOP-5K88T8O\SQLEXPRESS / obligation): ('08001', "[08001] [Microsoft][ODBC Driver 17 for SQL Server]Chiffrement non pris en charge sur le client. (21) (SQLDriverConnect); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Fournisseur SSL : Aucune information d’identification n’est disponible dans le package de sécurité\r\n (-2146893042); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Le client n'a pas pu établir la connexion (21); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Attribut de chaîne de connexion non valide (0); [08001] [Microsoft][ODBC Driver 17 for SQL Server]Une erreur liée au réseau ou spécifique à l'instance s'est produite lors de l'établissement d'une connexion à SQL Server. Le serveur est introuvable ou n'est pas accessible. Vérifiez si le nom de l'instance est correct et si SQL Server est configuré pour autoriser les connexions distantes. Pour plus d'informations, consultez la documentation en ligne de SQL Server. (-2146893042)")
