@echo off
REM Bashiti Central Scraper - Quick Start Script

echo ========================================
echo Bashiti Central Scraper
echo ========================================
echo.

REM Check if input file exists
if not exist "bashitihardware.xls" (
    echo Error: bashitihardware.xls not found!
    echo Please make sure the Excel file is in the same directory.
    pause
    exit /b 1
)

echo Starting scraper...
echo Input: bashitihardware.xls
echo Output: bashiticentral_results.xlsx
echo.

REM Run the scraper
python bashiticentral_scraper.py --in bashitihardware.xls --out bashiticentral_results.xlsx --pause 2.0

echo.
echo ========================================
echo Scraping complete!
echo Check bashiticentral_results.xlsx for results
echo ========================================
pause












