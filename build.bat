@echo off
rem Dong goi repomap tren Windows -> dist\repomap.pyz (copy sang may build Linux de chay).
rem   build.bat           chay unit test roi dong goi
rem   build.bat notest    bo qua unit test
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Khong tim thay Python. Cai Python 3.8+ tu https://www.python.org roi chay lai.
    exit /b 1
)

set "ARGS=--test"
if /i "%~1"=="notest" set "ARGS="

%PY% tools\build_pyz.py %ARGS%
if errorlevel 1 (
    echo Dong goi that bai.
    exit /b 1
)

echo.
echo Buoc tiep theo, copy sang may build va chay:
echo   scp dist\repomap.pyz user@build01:~/bin/
echo   ssh user@build01 "python3 ~/bin/repomap.pyz ~/work/ws"
endlocal
