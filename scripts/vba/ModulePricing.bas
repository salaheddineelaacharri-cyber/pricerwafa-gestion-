Attribute VB_Name = "ModulePricing"
Option Explicit

' Calcule le prix theorique d'une obligation a partir d'un tableau de flux.
' cashFlows: tableau VBA (1..N) contenant coupons et principal.
' ytm: rendement par periode (ex: 0.05 = 5%).
Public Function BondPrice(ByVal cashFlows As Variant, ByVal ytm As Double) As Double
    Dim i As Long
    Dim discountFactor As Double
    Dim pv As Double

    BondPrice = 0#

    ' Boucle sur chaque periode pour actualiser chaque flux.
    For i = LBound(cashFlows) To UBound(cashFlows)
        discountFactor = (1# + ytm) ^ i
        pv = cashFlows(i) / discountFactor
        BondPrice = BondPrice + pv
    Next i
End Function

' Procedure de demonstration pour tester rapidement la fonction.
Public Sub RunPricingExample()
    Dim flows(1 To 3) As Double
    Dim ytm As Double
    Dim price As Double

    ' Initialisation d'un cas simple (2 coupons + remboursement final).
    flows(1) = 5#
    flows(2) = 5#
    flows(3) = 105#
    ytm = 0.04

    ' Calcul du prix et affichage dans la fenetre Immediate.
    price = BondPrice(flows, ytm)
    Debug.Print "Prix theorique: "; Format(price, "0.0000")
End Sub

