@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 goto usepython
py -3 app.py
if errorlevel 1 pause
exit /b
:usepython
where python >nul 2>&1
if errorlevel 1 goto missing
python app.py
if errorlevel 1 pause
exit /b
:missing
echo Install Python 3.10 or newer from python.org with Tcl/Tk support.
pause
