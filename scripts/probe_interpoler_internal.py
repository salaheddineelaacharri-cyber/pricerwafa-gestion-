"""Sonde interne du comportement VBA `interpoler` avec différents profils de tableau.

On instrumente la fonction Excel pour révéler ses tableaux internes : on appelle
``interpoler(_mat1, taux1, m)`` puis on lit les cellules ``_mat1`` cellule par
cellule pour comprendre la liste effective utilisée par la macro.
"""

from __future__ import annotations

import os
import sys
import win32com.client


PATH = os.path.abspath(r"2026-PRICER_WG_CORRIGE .xlsm")
SHEET = "Feuil1"


def main() -> int:
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.AutomationSecurity = 1
    try:
        wb = excel.Workbooks.Open(PATH, ReadOnly=False, UpdateLinks=0)
        ct = wb.Worksheets("Courbe des taux")
        for r in range(2, 12):
            a = ct.Cells(r, 1)
            b = ct.Cells(r, 2)
            d = ct.Cells(r, 4)
            e = ct.Cells(r, 5)
            print(
                f"row {r}: "
                f"A={a.Value!r} (formula={a.Formula!r}) | "
                f"B={b.Value!r} | "
                f"D={d.Value!r} | "
                f"E={e.Value!r}"
            )
        # Ajout d'un test : faisons écrire un module VBA temporaire qui imprime mat() et taux().
        vbproj = wb.VBProject
        mod = vbproj.VBComponents.Add(1)  # vbext_ct_StdModule
        mod.Name = "TempProbe"
        code = (
            "Public Function ProbeInterpoler(ByVal col_maturite As Range, ByVal col_taux As Range, m) As String\n"
            "  Dim s As String, i As Integer, val As Variant\n"
            "  s = \"len=\" & col_maturite.Rows.Count\n"
            "  For i = 1 To col_maturite.Rows.Count\n"
            "    val = col_maturite.Cells(i, 1).Value\n"
            "    s = s & \" | i=\" & i & \" type=\" & TypeName(val) & \" v=\" & CStr(val)\n"
            "  Next i\n"
            "  ProbeInterpoler = s\n"
            "End Function\n"
        )
        mod.CodeModule.AddFromString(code)
        target = wb.Worksheets(SHEET).Cells(220, 30)
        target.Formula = "=ProbeInterpoler(_mat1,taux1,365)"
        print("\nProbeInterpoler(_mat1,taux1,365):")
        print(target.Value)
        target.Formula = "=ProbeInterpoler(_mat2,taux2,365)"
        print("\nProbeInterpoler(_mat2,taux2,365):")
        print(target.Value)
        target.ClearContents()
        # On retire le module ajouté à chaud sans persister.
        vbproj.VBComponents.Remove(mod)
        wb.Close(SaveChanges=False)
        return 0
    finally:
        excel.Quit()


if __name__ == "__main__":
    sys.exit(main())
