@echo off
REM G-Guard Scraper Runner
REM This script runs the G-Guard scraper with the Excel file

cd /d "%~dp0"
python gguard_scraper.py --input "jafar shop (1).xls" --headful

pause


