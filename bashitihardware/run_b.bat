@echo off
set PYTHONIOENCODING=utf-8
python -u "D:\JafarShop\Scrapers\bashitihardware\bashiticentral_scraper.py" --in "D:\JafarShop\Scrapers\bashitihardware\b.xlsx" --out "D:\JafarShop\Scrapers\bashitihardware\b_scraped.xlsx" --sku-col SKU --headless --pause 1.5
