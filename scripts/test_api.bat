@echo off
REM ============================================================================
REM  Secure Context Pipeline - API smoke / mass test  (Windows CMD)
REM
REM  Usage:
REM     scripts\test_api.bat [BASE_URL] [API_KEY]
REM
REM  Examples:
REM     scripts\test_api.bat
REM     scripts\test_api.bat https://your-app.up.railway.app scp_yourkey
REM
REM  Defaults are the demo deployment. Requires curl (built into Windows 10/11).
REM ============================================================================
setlocal enabledelayedexpansion

set "BASE=%~1"
if "%BASE%"=="" set "BASE=https://merry-playfulness-production-d238.up.railway.app"
set "KEY=%~2"
if "%KEY%"=="" set "KEY=scp_Gf6tTE7qRnegYSEK3g1F4e7G5JLVsGD1"
set "TMP=%TEMP%\scp_resp.json"

set /a PASS=0
set /a FAIL=0

echo ============================================================
echo   Secure Context Pipeline - API tests
echo   Target : %BASE%
echo ============================================================
echo.

REM --- 1. health (no auth) ---------------------------------------------------
for /f %%H in ('curl -s -o nul -w "%%{http_code}" "%BASE%/health"') do set "CODE=%%H"
if "!CODE!"=="200" (echo [PASS] health returns 200 & set /a PASS+=1) else (echo [FAIL] health returned !CODE! & set /a FAIL+=1)

REM --- 2. auth gate: no key must be rejected 401 -----------------------------
for /f %%H in ('curl -s -o nul -w "%%{http_code}" -X POST "%BASE%/process" -H "Content-Type: application/json" -d "{\"text\":\"x\",\"task\":\"y\"}"') do set "CODE=%%H"
if "!CODE!"=="401" (echo [PASS] auth gate rejects missing key ^(401^) & set /a PASS+=1) else (echo [FAIL] auth gate returned !CODE! ^(expected 401^) & set /a FAIL+=1)

echo.
echo --- leakage checks on /obfuscate ^(PII must be ABSENT from payload^) -------

REM  args: label | text | pii-that-must-be-gone | value-that-must-remain
call :leakcheck "SSN+name+MRN"  "Patient: Jonathan Reyes\nSSN: 482-19-7734\nMRN: 4457812\n62-year-old Han Chinese male, dose 500 mg."  "482-19-7734"  "Han Chinese"
call :leakcheck "email+phone"   "Contact: Maria Reyes\nEmail: mreyes@example.com\nPhone: (415) 555-0177"  "mreyes@example.com"  ""
call :leakcheck "another SSN"   "Client: Dana Whitfield\nSSN: 019-28-4412\nViral load 250000 copies/mL"  "019-28-4412"  "250000 copies/mL"
call :leakcheck "surname-prose" "Patient: Aaron Kessler\nKessler tolerated the regimen well."  "Kessler tolerated"  ""

echo.
echo --- full round-trip on /process ^(restore + clean de-obf^) -----------------
call :roundtrip "round-trip #1" "Patient: Jonathan Reyes\nSSN: 482-19-7734\nMRN: 4457812"

echo.
echo ============================================================
echo   RESULT: !PASS! passed, !FAIL! failed
echo ============================================================
if not "%~3"=="" goto :eof
del "%TMP%" >nul 2>&1
if !FAIL! gtr 0 (exit /b 1) else (exit /b 0)


REM ---------------------------------------------------------------------------
:leakcheck
REM  %~1 label  %~2 text  %~3 must-be-absent  %~4 must-be-present ("" to skip)
curl -s -X POST "%BASE%/obfuscate" -H "Content-Type: application/json" -H "X-API-Key: %KEY%" -d "{\"text\":\"%~2\"}" > "%TMP%"
findstr /C:"%~3" "%TMP%" >nul
if errorlevel 1 (
  echo [PASS] %~1 : "%~3" removed from payload
  set /a PASS+=1
) else (
  echo [FAIL] %~1 : LEAK - "%~3" still present
  set /a FAIL+=1
)
if not "%~4"=="" (
  findstr /C:"%~4" "%TMP%" >nul
  if errorlevel 1 (
    echo [FAIL] %~1 : clinical value "%~4" was dropped
    set /a FAIL+=1
  ) else (
    echo [PASS] %~1 : clinical value "%~4" preserved
    set /a PASS+=1
  )
)
goto :eof


:roundtrip
REM  %~1 label  %~2 text
curl -s -X POST "%BASE%/process" -H "Content-Type: application/json" -H "X-API-Key: %KEY%" -d "{\"text\":\"%~2\",\"task\":\"Summarize.\"}" > "%TMP%"
findstr /C:"\"deobfuscation_clean\":true" "%TMP%" >nul
if errorlevel 1 (
  echo [FAIL] %~1 : de-obfuscation not clean ^(see %TMP%^)
  set /a FAIL+=1
) else (
  echo [PASS] %~1 : round-trip completed, de-obfuscation clean
  set /a PASS+=1
)
goto :eof
