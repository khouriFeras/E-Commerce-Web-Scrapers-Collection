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

:: Install all dependencies
%PYTHON% -m pip install -r desktop_app\requirements.txt

:: Install Playwright browsers (needed for the suppliers scraper)
%PYTHON% -m playwright install chromium

echo.
echo  Starting JafarShop Scraper Launcher...
echo  Browser will open automatically at http://localhost:5000
echo.
echo  Press Ctrl+C to stop.
echo.

%PYTHON% desktop_app/app.py

pause
