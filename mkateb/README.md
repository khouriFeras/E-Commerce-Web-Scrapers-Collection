# MKateb Scraper

This scraper extracts product information from mkateb.com based on Product Codes in an Excel file.

## Files

- **makteb.py** - Original single-sheet scraper
- **makteb_all_sheets.py** - Multi-sheet scraper for all product categories
- **run_all_sheets_scraper.bat** - Batch file to run the multi-sheet scraper
- **Jafar shop Products list (1)futher home.xlsx** - Input file with 8 sheets

## Input File Structure

The input Excel file contains 8 sheets:
1. Kodak
2. Promate
3. Scrubdaddy
4. Skullcandy
5. Drinkmate
6. Fellows
7. Philips
8. Deli

Each sheet must have a **Product Code** column that contains the SKU/product codes to search for.

## How to Run

### Option 1: Using Batch File (Windows)
```bash
run_all_sheets_scraper.bat
```

### Option 2: Manual Execution
```bash
# Activate virtual environment
cd D:\JafarShop\Scrapers
call scraper_env\Scripts\activate.bat

# Run the scraper
python mkateb\makteb_all_sheets.py
```

## Output

The scraper creates **makteb_all_sheets_scraped.xlsx** with:

### Original Columns
- All columns from the input sheets are preserved

### New Scraped Columns
- **scraped_url** - Direct product URL
- **scraped_title** - Product title
- **scraped_description** - Product description text
- **scraped_images** - Semicolon-separated image URLs
- **scraping_error** - Error message (if any)

## Features

- ✅ Processes all 8 sheets automatically
- ✅ Preserves all original data
- ✅ Appends scraped data as new columns
- ✅ Handles missing/empty Product Codes
- ✅ Comprehensive error logging
- ✅ Progress tracking for each sheet
- ✅ Extracts product titles, descriptions, and images

## Requirements

- Python 3.x
- Selenium
- Pandas
- Chrome WebDriver
- openpyxl

## Notes

- The scraper uses Selenium with Chrome browser
- Each product search may take 2-3 seconds
- Browser window will open during scraping
- Empty Product Codes are skipped automatically
- Errors are logged in the "scraping_error" column
