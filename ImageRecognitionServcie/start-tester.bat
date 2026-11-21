@echo off
rem ============================================================
rem  OCR Tester: starts the OCR service and opens the local test page.
rem  The "OCR serwis" window is the running service; close it to stop the service.
rem ============================================================
setlocal
cd /d "%~dp0"
title OCR Tester

rem --- Local test settings (existing environment variables take precedence) ---
if not defined OCR_PORT set "OCR_PORT=8000"
if not defined OCR_MODEL_DIR set "OCR_MODEL_DIR=%~dp0models"
if not defined OCR_WORKERS set "OCR_WORKERS=2"
if not defined OCR_PADDLE_CPU_THREADS set "OCR_PADDLE_CPU_THREADS=4"
set "OCR_LOG_JSON=false"
rem A page opened from disk (file://) needs CORS to call the service.
set "OCR_CORS_ORIGINS=*"
set "READY_URL=http://localhost:%OCR_PORT%/readyz"

rem --- Is the service already running? ---
call :ready
if "%CODE%"=="200" (
    echo [OK] Serwis juz dziala na porcie %OCR_PORT%.
    goto :open
)

rem --- uv (Python and dependency manager) ---
set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV (
    python -m uv --version >nul 2>nul && set "UV=python -m uv"
)
if not defined UV (
    echo [..] Instaluje uv...
    python -m pip install --user --quiet uv
    python -m uv --version >nul 2>nul && set "UV=python -m uv"
)
if not defined UV (
    echo [BLAD] Nie znaleziono uv ani Pythona. Zainstaluj uv: https://docs.astral.sh/uv/
    goto :fail
)

echo [..] Instaluje / sprawdzam zaleznosci (pierwsze uruchomienie moze potrwac kilka minut)...
%UV% sync --quiet
if errorlevel 1 (
    echo [BLAD] Instalacja zaleznosci nie powiodla sie.
    goto :fail
)

echo [..] Uruchamiam serwis OCR w osobnym oknie (port %OCR_PORT%)...
start "OCR serwis" cmd /k %UV% run ocr-service

echo [..] Czekam, az model sie zaladuje (za pierwszym razem pobiera ~20 MB wag)...
set /a TRIES=0
:wait
call :ready
if "%CODE%"=="200" goto :ready_ok
set /a TRIES+=1
if %TRIES% GEQ 300 (
    echo [BLAD] Serwis nie wystartowal w 10 minut. Sprawdz okno "OCR serwis".
    goto :fail
)
ping -n 3 127.0.0.1 >nul
goto :wait

:ready_ok
echo [OK] Serwis gotowy.

:open
if defined OCR_TESTER_NO_BROWSER goto :done
echo [..] Otwieram strone testowa...
start "" "%~dp0tools\ocr-tester.html"

:done
echo.
echo Serwis:       http://localhost:%OCR_PORT%
echo Dokumentacja: http://localhost:%OCR_PORT%/docs
echo Aby zatrzymac serwis, zamknij okno "OCR serwis".
endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1

rem --- Sets CODE to the HTTP status of /readyz (000 = no connection) ---
:ready
set "CODE=000"
for /f %%c in ('curl.exe -s -o nul -m 3 -w "%%{http_code}" "%READY_URL%" 2^>nul') do set "CODE=%%c"
exit /b 0
