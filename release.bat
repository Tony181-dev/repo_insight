@echo off
rem Tao goi phat hanh de dua cho nguoi dung, KHONG kem repo ma nguon:
rem   dist\repomap-<version>.tar.gz  va  dist\repomap-<version>.zip
rem Trong goi: repomap.pyz, install.sh, README.txt, HUONG_DAN.html.
rem   release.bat          chay unit test roi tao goi
rem   release.bat notest   bo qua unit test
setlocal
cd /d "%~dp0"
set "RC=0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Khong tim thay Python. Cai Python 3.8+ tu https://www.python.org roi chay lai.
    set "RC=1"
    goto :end
)

set "ARGS=--test"
if /i "%~1"=="notest" set "ARGS="

%PY% tools\make_release.py %ARGS%
if errorlevel 1 (
    echo.
    echo Tao goi that bai, xem loi o tren.
    set "RC=1"
    goto :end
)

echo.
echo Gui file .tar.gz cho nguoi dung. Ho cai tren may build bang:
echo   tar xzf repomap-*.tar.gz
echo   cd repomap-*
echo   sh install.sh

:end
echo.
pause
endlocal & exit /b %RC%
