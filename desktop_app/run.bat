@echo off
title JafarShop Scraper Launcher

:: Change to the Scrapers root directory (one level up from this batch file)
cd /d "%~dp0.."

:: Try scraper_env first, fall back to system Python
if exist "scraper_env\Scripts\python.exe" (
    set PYTHON=scraper_env\Scripts\python.exe
) else (
    set PYTHON=python
)

:: Install Flask if not already installed
%PYTHON% -m pip show flask >nul 2>&1 || %PYTHON% -m pip install flask pandas openpyxl

echo.
echo  Starting JafarShop Scraper Launcher...
echo  Browser will open automatically at http://localhost:5000
echo.
echo  Press Ctrl+C to stop.
echo.

%PYTHON% desktop_app/app.py

pause
