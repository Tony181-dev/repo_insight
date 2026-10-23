@echo off
rem Tao goi phat hanh de dua cho nguoi dung, KHONG kem repo ma nguon:
rem   dist\repomap-<version>.zip (chi mot goi)
rem Trong goi: repomap.pyz, install.sh, README.txt, HUONG_DAN.html.
rem   release.bat          chay unit test roi tao goi
rem   release.bat notest   bo qua unit test
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
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
echo Gui duy nhat file dist\repomap-*.zip vua tao.
echo Giai nen vao thu muc moi, bam repomap-pc.cmd de thay PC server cu.
echo Sau do Push to server roi Connect de cap nhat may build.

:end
echo.
if not defined REPOMAP_NO_PAUSE pause
popd
endlocal & exit /b %RC%
