@echo off
setlocal
rem ============================================================
rem  rtheatflow Stopper: beendet Backend, UI und verwaiste
rem  Hintergrundprozesse (node/esbuild/python) aus DIESEM Repo.
rem  Ein parallel laufendes netzsim/rtpowerflow (Ports 8000/5173)
rem  bleibt unberuehrt.
rem ============================================================
cd /d "%~dp0"

echo === rtheatflow Stopper ===

rem ---------- Serverfenster schliessen (samt Kindprozessen) ----------
taskkill /FI "WINDOWTITLE eq rtheatflow Backend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq rtheatflow UI*" /T /F >nul 2>&1

rem ---------- Besitzer der rtheatflow-Ports 8001/5174 beenden ----------
powershell -NoProfile -Command "foreach ($p in 8001,5174) { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }"

rem ---------- verwaiste Prozesse dieses Projekts beenden ----------
rem Erkennung ueber die KOMMANDOZEILE, nicht den Exe-Pfad: Vite laeuft als
rem "C:\Program Files\nodejs\node.exe <repo>\ui\...\vite.js" und das Backend
rem als relatives ".venv\Scripts\python.exe -m rtheatflow.main" - beide
rem wuerden einem Exe-Pfad-Filter entgehen (auch Reste aus geloeschten
rem Agent-Worktrees und Server auf abgewanderten Ports werden so gefunden).
powershell -NoProfile -Command "$root = (Get-Location).Path; Get-CimInstance Win32_Process | Where-Object { ($_.Name -in 'node.exe','esbuild.exe','python.exe') -and ( $_.CommandLine -like ('*' + $root + '*') -or $_.CommandLine -like '*rtheatflow.main*' ) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem ---------- Ergebnis pruefen ----------
powershell -NoProfile -Command "$busy = @(8001,5174 | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue }); if ($busy.Count) { Write-Host ('WARNUNG: Port(s) noch belegt: ' + ($busy -join ', ')) } else { Write-Host 'Alle rtheatflow-Dienste beendet - Ports 8001/5174 sind frei.' }"
ping -n 4 127.0.0.1 >nul
endlocal
