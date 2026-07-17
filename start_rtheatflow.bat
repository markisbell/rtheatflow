@echo off
setlocal
rem ============================================================
rem  rtheatflow Starter: Backend (FastAPI, :8001) + UI (Vite, :5174)
rem  Doppelklick genuegt. Bereits laufende Server werden erkannt
rem  und nicht doppelt gestartet.
rem  Ports 8001/5174 mit Absicht: netzsim/rtpowerflow belegt
rem  8000/5173 - beide Plattformen laufen so parallel.
rem  Beenden: stop_rtheatflow.bat (oder die Serverfenster schliessen).
rem ============================================================
cd /d "%~dp0"

echo === rtheatflow Starter ===

rem ---------- Backend (Port 8001) ----------
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo Backend laeuft bereits auf Port 8001 - wird nicht neu gestartet.
    goto ui
)
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo FEHLER: Die virtuelle Umgebung .venv fehlt. Einmalig anlegen mit:
    echo    python -m venv .venv
    echo    .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo Starte Backend auf http://localhost:8001 ...
start "rtheatflow Backend" cmd /k "set PYTHONPATH=src&& .venv\Scripts\python.exe -m rtheatflow.main"

:ui
rem ---------- UI (Port 5174) ----------
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5174 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo UI laeuft bereits auf Port 5174 - wird nicht neu gestartet.
    goto browser
)
if not exist "ui\node_modules" (
    echo Hinweis: ui\node_modules fehlt - installiere einmalig die Abhaengigkeiten...
    pushd ui
    call npm install
    popd
)
echo Starte UI auf http://localhost:5174 ...
start "rtheatflow UI" cmd /k "cd /d ui && npm run dev"

:browser
rem ---------- warten, bis das Backend antwortet (max. ~60 s) ----------
rem (Kaltstart laedt pandapipes/numba - das kann 20-60 s dauern)
echo Warte auf das Backend ...
for /l %%i in (1,1,60) do (
    powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8001/health | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 goto up
    ping -n 2 127.0.0.1 >nul
)
echo WARNUNG: Backend antwortet noch nicht - Fenster "rtheatflow Backend" pruefen.
:up
start "" http://localhost:5174/
echo.
echo Fertig: UI unter http://localhost:5174 (Backend: http://localhost:8001)
echo Zum Beenden: stop_rtheatflow.bat ausfuehren oder die beiden Serverfenster schliessen.
ping -n 6 127.0.0.1 >nul
endlocal
