"""
Template Python pour un workflow simple de pricing obligataire.
Ce fichier sert de base pedagogique et montre des commentaires explicatifs a chaque etape.
"""

from typing import Iterable


def bond_price(cash_flows: Iterable[float], ytm: float) -> float:
    """
    Calcule le prix theorique d'une obligation par actualisation des flux.

    Args:
        cash_flows: sequence des flux futurs (coupons + remboursement final).
        ytm: rendement par periode (ex: 0.05 pour 5%).

    Returns:
        Prix actualise total.
    """
    price = 0.0

    # Boucle sur chaque flux futur pour appliquer la formule d'actualisation.
    for period, cash_flow in enumerate(cash_flows, start=1):
        discount_factor = (1 + ytm) ** period
        present_value = cash_flow / discount_factor
        price += present_value

    return price


def example_run() -> None:
    """Execution d'exemple pour verifier rapidement le comportement du script."""
    # Exemple simplifie: 3 coupons de 5 puis remboursement final de 105.
    flows = [5.0, 5.0, 105.0]
    ytm = 0.04

    # Calcul du prix avec la fonction principale.
    price = bond_price(flows, ytm)

    # Affichage pour controle manuel et integration dans un rapport.
    print(f"Prix theorique: {price:.4f}")


if __name__ == "__main__":
    # Point d'entree du script lors d'une execution directe.
    example_run()

