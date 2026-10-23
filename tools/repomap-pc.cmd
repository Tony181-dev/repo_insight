@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem repomap tren PC: mo trang Connect (co Deploy) / Saved servers (http://127.0.0.1:6960/), khong de lai cua so cmd.
rem Can Python 3.8+ (python.org) va OpenSSH client (co san tren Windows 10/11). Dat file nay canh repomap.pyz.
set "PYZ=%~dp0repomap.pyz"
if not exist "%PYZ%" (echo repomap.pyz not found next to this file: %PYZ% & pause & exit /b 1)
pushd "%~dp0" || exit /b 1
where pyw >nul 2>nul && (start "" pyw -3 "%PYZ%" --local --replace %* & popd & exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw "%PYZ%" --local --replace %* & popd & exit /b 0)
echo Python 3.8+ was not found. Install it from https://www.python.org/downloads/ and run this file again.
pause
exit /b 1
