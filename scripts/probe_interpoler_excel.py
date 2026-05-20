"""Sonde le comportement réel de la fonction VBA `interpoler` du classeur Excel.

Ouvre `2026-PRICER_WG_CORRIGE .xlsm`, écrit dans une cellule libre la formule
``=interpoler(_mat1, taux1, x)`` et lit la valeur retournée.
Permet de comprendre :
- comment ``interpoler`` gère ``m > max(_mat1)`` (extrapolation, dernier taux,
  fallback ``_mat2``, autre…) ;
- comment ``interpoler`` gère ``m`` interne, juste pour vérification.
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
    # 1 = msoAutomationSecurityLow → autoriser les macros (pour évaluer interpoler)
    excel.AutomationSecurity = 1
    try:
        wb = excel.Workbooks.Open(PATH, ReadOnly=False, UpdateLinks=0)
        ws = wb.Worksheets(SHEET)
        # Probe une série de m sur _mat1 / taux1 et _mat2 / taux2.
        cas = [
            ("interpoler(_mat1,taux1,1)", 1),
            ("interpoler(_mat1,taux1,53)", 53),
            ("interpoler(_mat1,taux1,144)", 144),
            ("interpoler(_mat1,taux1,326)", 326),
            ("interpoler(_mat1,taux1,365)", 365),
            ("interpoler(_mat1,taux1,400)", 400),
            ("interpoler(_mat1,taux1,500)", 500),
            ("interpoler(_mat1,taux1,1000)", 1000),
            ("interpoler(_mat2,taux2,1)", 1),
            ("interpoler(_mat2,taux2,265)", 265),
            ("interpoler(_mat2,taux2,326)", 326),
            ("interpoler(_mat2,taux2,365)", 365),
            ("interpoler(_mat2,taux2,400)", 400),
            ("interpoler(_mat2,taux2,500)", 500),
            ("interpoler(_mat2,taux2,543)", 543),
            ("interpoler(_mat2,taux2,1000)", 1000),
            ("interpoler(_mat2,taux2,1481)", 1481),
            ("interpoler(_mat2,taux2,2000)", 2000),
        ]
        target_cell = ws.Cells(200, 30)  # AD200 ailleurs : libre.
        out = []
        for label, m in cas:
            target_cell.Formula = f"=interpoler(_mat1,taux1,{m})" if "_mat1" in label else f"=interpoler(_mat2,taux2,{m})"
            v = target_cell.Value
            out.append((label, v))
        target_cell.ClearContents()
        wb.Close(SaveChanges=False)
        for label, v in out:
            print(f"{label:50s} -> {v}")
        return 0
    finally:
        excel.Quit()


if __name__ == "__main__":
    sys.exit(main())
