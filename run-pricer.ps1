# Pricer WG — démarrage simple : API (8001) + Vite (5177), même logique que ``python run_api.py``.
$Root = $PSScriptRoot
Set-Location $Root

function Stop-PythonOnPort {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $procId = $_.OwningProcess
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -eq "python") {
            Write-Host "Arret Python sur port $Port (PID $procId)..." -ForegroundColor Yellow
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 400
}

Write-Host "Liberation du port 8001 (evite ancien worker / 404)..." -ForegroundColor DarkGray
Stop-PythonOnPort -Port 8001

Write-Host "Demarrage backend : http://127.0.0.1:8001  (run_api.py + reload fiable)" -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $Root -ArgumentList @(
    "-NoExit",
    "-NoProfile",
    "-Command",
    "Set-Location '$Root'; python run_api.py"
)

Start-Sleep -Seconds 2

$Frontend = Join-Path $Root "frontend"
Write-Host "Demarrage frontend : http://localhost:5177" -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $Frontend -ArgumentList @(
    "-NoExit",
    "-NoProfile",
    "-Command",
    "Set-Location '$Frontend'; npm run dev"
)

Start-Sleep -Seconds 5
Write-Host "Ouverture du navigateur..." -ForegroundColor Green
Start-Process "http://localhost:5177/"
Write-Host ""
Write-Host "Sante API : http://127.0.0.1:8001/api/health  (doit contenir amort_engine_id)" -ForegroundColor DarkGray
Write-Host "Ferme les deux fenetres PowerShell (Backend / Frontend) pour arreter." -ForegroundColor Yellow
