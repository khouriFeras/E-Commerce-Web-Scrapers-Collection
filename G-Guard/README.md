# G-Guard Scraper

Scraper for extracting product images, titles, and prices from gguard.com based on SKUs from an Excel file.

## Features

- Reads SKUs from Excel file
- Searches for products on gguard.com
- Extracts:
  - Product image (from `.product-images.wd-grid-col`)
  - Product title (from `.product_title.entry-title.wd-entities-title`)
  - Old price (with `aria-hidden="true"`)
  - New price (current price)
- Appends scraped data to the original Excel file without changing existing data

## Usage

### Basic Usage

```bash
python gguard_scraper.py --input "jafar shop (1).xls"
```

This will:
- Read SKUs from the Excel file
- Scrape product data for each SKU
- Save results back to the same file (adds new columns: `image_url`, `title`, `old_price`, `new_price`, `product_url`, `scrape_status`)

### With Custom Output File

```bash
python gguard_scraper.py --input "jafar shop (1).xls" --output "jafar shop_scraped.xls"
```

### Run with Visible Browser (for debugging)

```bash
python gguard_scraper.py --input "jafar shop (1).xls" --headful
```

### Process Only First N Rows (for testing)

```bash
python gguard_scraper.py --input "jafar shop (1).xls" --limit 5
```

### Adjust Delay Between Requests

```bash
python gguard_scraper.py --input "jafar shop (1).xls" --delay 3.0
```

## Command Line Arguments

- `--input`, `-i`: (Required) Path to input Excel file
- `--output`, `-o`: (Optional) Path to output Excel file (default: same as input)
- `--headless`: Run in headless mode (default: True)
- `--headful`: Run with visible browser window
- `--delay`: Delay between requests in seconds (default: 2.0)
- `--limit`: Process only first N rows (for testing)

## Output Columns

The scraper adds the following columns to your Excel file:

- `image_url`: URL of the product image
- `title`: Product title
- `old_price`: Original price (if available)
- `new_price`: Current price
- `product_url`: URL of the product page
- `scrape_status`: Status of the scrape (`found`, `not_found`, `error`, etc.)

## Notes

- The scraper preserves all existing data in the Excel file
- If a product is already scraped (has a `product_url`), it will be skipped
- Progress is saved every 5 products
- Logs are saved to `gguard_scraper.log`

## Requirements

- Python 3.7+
- pandas
- selenium
- openpyxl (for Excel support)
- tqdm (for progress bar)
- webdriver-manager (optional, for automatic Chrome driver management)

Install dependencies:
```bash
pip install pandas selenium openpyxl tqdm webdriver-manager
```


