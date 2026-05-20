@echo off
title Pricer API
cd /d "%~dp0"
echo [pricer] Meme port que le front (defaut 8001). Pour changer : set PRICER_API_PORT=8000
echo [pricer] CMD - sans reload auto : set PRICER_NO_RELOAD=1^&^& python run_api.py
echo [pricer] PowerShell - sans reload : $env:PRICER_NO_RELOAD='1' ; python run_api.py
echo [pricer] Si le moteur semble ancien : pas de « set » sous PowerShell — utiliser $env:...
pip install -r requirements.txt -q
python run_api.py
pause
