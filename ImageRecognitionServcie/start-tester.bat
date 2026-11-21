@echo off
rem ============================================================
rem  OCR Tester: starts the OCR service (+ a callback receiver) and opens the test page.
rem  Windows "OCR serwis" / "OCR odbiornik callbackow" = running processes; close them to stop.
rem  Default = callback mode (result is POSTed to the local receiver).
rem  set TESTER_MODE=sync before running to test the plain request/response mode.
rem ============================================================
setlocal
cd /d "%~dp0"
title OCR Tester

rem --- Local test settings (existing environment variables take precedence) ---
if not defined OCR_PORT set "OCR_PORT=8000"
if not defined OCR_RECEIVER_PORT set "OCR_RECEIVER_PORT=9000"
if not defined OCR_MODEL_DIR set "OCR_MODEL_DIR=%~dp0models"
if not defined OCR_WORKERS set "OCR_WORKERS=2"
if not defined OCR_PADDLE_CPU_THREADS set "OCR_PADDLE_CPU_THREADS=4"
set "OCR_LOG_JSON=false"
rem A page opened from disk (file://) needs CORS to call the service.
set "OCR_CORS_ORIGINS=*"

set "CALLBACK=1"
if /i "%TESTER_MODE%"=="sync" set "CALLBACK="
if defined CALLBACK (
    if not defined OCR_CALLBACK_URL set "OCR_CALLBACK_URL=http://127.0.0.1:%OCR_RECEIVER_PORT%/ocr-results"
    if not defined OCR_CALLBACK_TOKEN set "OCR_CALLBACK_TOKEN=tester-token"
) else (
    set "OCR_CALLBACK_URL="
)
rem The receiver checks the same token the service sends.
set "RECEIVER_TOKEN=%OCR_CALLBACK_TOKEN%"

set "READY_URL=http://localhost:%OCR_PORT%/readyz"
set "RECEIVER_URL=http://127.0.0.1:%OCR_RECEIVER_PORT%/healthz"

rem --- What is already running? ---
call :probe "%READY_URL%"
set "SVC_CODE=%CODE%"
set "RCV_CODE=200"
if defined CALLBACK call :probe "%RECEIVER_URL%"
if defined CALLBACK set "RCV_CODE=%CODE%"

if "%SVC_CODE%"=="200" if "%RCV_CODE%"=="200" (
    echo [OK] Serwis i odbiornik juz dzialaja.
    echo      Jesli serwis byl uruchomiony w innym trybie, zamknij jego okno i uruchom ten plik ponownie.
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

if not "%RCV_CODE%"=="200" (
    echo [..] Uruchamiam odbiornik callbackow w osobnym oknie, port %OCR_RECEIVER_PORT%
    start "OCR odbiornik callbackow" cmd /k %UV% run python examples\callback_receiver.py --port %OCR_RECEIVER_PORT%
)
if "%SVC_CODE%"=="200" goto :wait_start
if defined CALLBACK echo [..] Uruchamiam serwis OCR w trybie callback, port %OCR_PORT%, wyniki na %OCR_CALLBACK_URL%
if not defined CALLBACK echo [..] Uruchamiam serwis OCR w trybie odpowiedzi synchronicznej, port %OCR_PORT%
start "OCR serwis" cmd /k %UV% run ocr-service

:wait_start
echo [..] Czekam, az wszystko wystartuje (za pierwszym razem serwis pobiera ~20 MB wag)...
set /a TRIES=0
:wait
call :probe "%READY_URL%"
set "SVC_CODE=%CODE%"
if defined CALLBACK call :probe "%RECEIVER_URL%"
if defined CALLBACK set "RCV_CODE=%CODE%"
if "%SVC_CODE%"=="200" if "%RCV_CODE%"=="200" goto :ready_ok
set /a TRIES+=1
if %TRIES% GEQ 300 (
    echo [BLAD] Nie wystartowalo w 10 minut. Sprawdz okna "OCR serwis" i "OCR odbiornik callbackow".
    goto :fail
)
ping -n 3 127.0.0.1 >nul
goto :wait

:ready_ok
echo [OK] Wszystko gotowe.

:open
if defined OCR_TESTER_NO_BROWSER goto :done
echo [..] Otwieram strone testowa...
start "" "%~dp0tools\ocr-tester.html"

:done
echo.
echo Serwis:     http://localhost:%OCR_PORT%   - dokumentacja: /docs
if defined CALLBACK echo Odbiornik:  http://127.0.0.1:%OCR_RECEIVER_PORT%   - wyniki: /results/REQUEST_ID
echo Aby zatrzymac, zamknij okna "OCR serwis" i "OCR odbiornik callbackow".
endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1

rem --- Sets CODE to the HTTP status of the given URL (000 = no connection) ---
:probe
set "CODE=000"
for /f %%c in ('curl.exe -s -o nul -m 3 -w "%%{http_code}" "%~1" 2^>nul') do set "CODE=%%c"
exit /b 0
