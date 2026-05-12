@echo off
echo Starting Universal Scraper HTTP API...
echo.
echo This will start the Python API server that n8n can call
echo.
echo Make sure you have:
echo 1. Python installed
echo 2. All required packages installed (pip install -r requirements.txt)
echo 3. Chrome browser installed
echo.
echo The API will be available at: http://localhost:5000
echo.
echo Press any key to start...
pause

cd /d "%~dp0"
python scraper_http_api.py

echo.
echo API server stopped.
pause




