"""
Lance l’API FastAPI avec rechargement fiable sous Windows :

- Surveille la **racine du projet** (``obligation_amort_schedule.py``, ``valuation_*.py``, …)
  et le dossier ``backend/``, car ``uvicorn --reload`` seul ne regarde parfois que le CWD.
- Fixe ``app_dir`` sur la racine : le worker importe bien le module à la racine (pas une autre copie du PATH).
- Évite les rechargements inutiles sur gros fichiers Excel / dépôt (``reload_excludes``).

Variables d’environnement utiles :

- ``PRICER_API_PORT`` : port (défaut **8001**, comme le proxy Vite ``vite.config.ts``).
- ``PRICER_NO_RELOAD=1`` ou ``PRICER_RELOAD=0`` : pas de rechargement auto — un seul processus ; après
  chaque modif du moteur, **redémarrer à la main**.
- **PowerShell** : ``set PRICER_NO_RELOAD=1`` ne définit **pas** une variable d’environnement pour Python.
  Utiliser : ``$env:PRICER_NO_RELOAD = '1' ; python run_api.py`` (ou ``$env:PRICER_RELOAD = '0'``).
- **cmd.exe** : ``set PRICER_NO_RELOAD=1&& python run_api.py``
- ``PRICER_RELOAD_DELAY`` : délai en secondes avant reload après sauvegarde (défaut **0.6** ; éditeurs
  qui écrivent le fichier en deux temps sur NTFS).

Usage (depuis la racine du dépôt)::

    python run_api.py
    $env:PRICER_NO_RELOAD='1'; python run_api.py
    set PRICER_NO_RELOAD=1&& python run_api.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    # Indispensable si tu lances ``python C:\...\run_api.py`` depuis un autre dossier :
    # sinon ``import backend`` peut pointer vers un **autre** projet nommé ``backend`` (mauvais /api/health).
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    port = int(os.environ.get("PRICER_API_PORT", "8001"))
    _nr = os.environ.get("PRICER_NO_RELOAD", "").strip().lower()
    _r = os.environ.get("PRICER_RELOAD", "").strip().lower()
    no_reload = _nr in ("1", "true", "yes", "oui") or _r in ("0", "false", "no", "non", "off")
    try:
        reload_delay = float(os.environ.get("PRICER_RELOAD_DELAY", "0.6"))
    except ValueError:
        reload_delay = 0.6
    reload_delay = max(0.1, min(reload_delay, 5.0))

    print(f"[pricer] Racine dépôt (reload) : {root}")
    print(f"[pricer] CWD au lancement      : {Path.cwd()}")
    print(f"[pricer] API sur http://127.0.0.1:{port}  (variable PRICER_API_PORT pour changer)")
    try:
        import obligation_amort_schedule as _oam

        _am_path = Path(_oam.__file__).resolve()
        _mt = _am_path.stat().st_mtime
        print(f"[pricer] Moteur amortissement chargé par ce processus : {_am_path}")
        print(f"[pricer] (mtime obligation_amort_schedule.py : {_mt:.3f})")
    except Exception as ex:  # pragma: no cover
        print(f"[pricer] Avertissement : impossible de localiser obligation_amort_schedule : {ex}")

    if no_reload:
        print(
            "[pricer] Reload désactivé (PRICER_NO_RELOAD ou PRICER_RELOAD=0) → "
            "un seul processus ; redémarrage manuel après chaque modif du moteur.",
        )
        uvicorn.run(
            "backend.main:app",
            host="127.0.0.1",
            port=port,
            reload=False,
            app_dir=str(root),
        )
    else:
        if sys.platform == "win32":
            print(
                "[pricer] Sous PowerShell, pour couper le reload : "
                "$env:PRICER_NO_RELOAD='1' ; python run_api.py   "
                "(la commande « set » est pour cmd.exe, pas pour PowerShell.)",
            )
        print(
            f"[pricer] Reload actif (délai {reload_delay}s) ; racine + backend/ surveillés. "
            "Si l’UI ne reflète pas le dernier code : désactivez le reload puis relancez, ou fermez tous les Python.",
        )
        uvicorn.run(
            "backend.main:app",
            host="127.0.0.1",
            port=port,
            reload=True,
            app_dir=str(root),
            reload_dirs=[str(root), str(root / "backend")],
            reload_includes=["*.py"],
            reload_excludes=[
                "run_api.py",
                "**/run_api.py",
                "*.xlsx",
                "*.xlsm",
                "*.csv",
                "**/.git/**",
                "**/node_modules/**",
                "**/__pycache__/**",
                "**/.venv/**",
            ],
            reload_delay=reload_delay,
        )
