# Bashiti Central Scraper

Scraper for extracting product data from [Bashiti Central](https://www.bashiticentral.com).

## Features

- ✅ **Handles Laravel Error Pages**: Automatically detects when products don't exist and marks them as "NOT_FOUND"
- ✅ **Comprehensive Data Extraction**: Extracts title, price, description, and images
- ✅ **Excel Support**: Reads from Excel files and writes results back
- ✅ **Auto-Detection**: Automatically finds SKU/URL columns in Excel files
- ✅ **Robust Error Handling**: Handles timeouts, errors, and missing products gracefully
- ✅ **Debug Information**: Extracts useful debug info from Laravel error pages for learning

## Installation

```bash
pip install selenium pandas openpyxl beautifulsoup4
```

## Usage

### Basic Usage - Scrape from Excel File

```bash
python bashiticentral_scraper.py --in bashitihardware.xls --out results.xlsx
```

### Scrape Single SKU

```bash
python bashiticentral_scraper.py --sku AS5482402 --out result.xlsx
```

### Scrape Single URL

```bash
python bashiticentral_scraper.py --url "https://www.bashiticentral.com/en/single-product/AS5482402" --out result.xlsx
```

### Scrape with Custom Column Name

```bash
python bashiticentral_scraper.py --in bashitihardware.xls --sku-col "Item Code" --out results.xlsx
```

### Run in Headless Mode

```bash
python bashiticentral_scraper.py --in bashitihardware.xls --out results.xlsx --headless
```

### Adjust Pause Between Requests

```bash
python bashiticentral_scraper.py --in bashitihardware.xls --out results.xlsx --pause 3.0
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--in` | Input Excel file with SKUs or URLs | `bashitihardware.xls` |
| `--out` | Output Excel file path | `bashiticentral_results.xlsx` |
| `--sku-col` | SKU or URL column name (auto-detect if omitted) | Auto-detect |
| `--url` | Single product URL to scrape | - |
| `--sku` | Single SKU to scrape | - |
| `--pause` | Pause between requests (seconds) | `2.0` |
| `--headless` | Run in headless mode | `False` |
| `--max-img` | Maximum images per product | `10` |
| `--sheet` | Excel sheet name or index | `0` |

## Input Format

The scraper expects an Excel file with at least one column containing:
- **SKUs** (e.g., `AS5482402`) - Will be converted to URLs automatically
- **URLs** (e.g., `https://www.bashiticentral.com/en/single-product/AS5482402`)

### Auto-Detected Column Names

The scraper automatically looks for columns with these names:
- SKU columns: `sku`, `item`, `itemno`, `item_no`, `code`, `product_code`, `item_code`
- URL columns: `url`, `link`, `product_url`, `producturl`, `product_link`, `href`

## Output Format

The output Excel file contains the following columns:

| Column | Description |
|--------|-------------|
| `ProductURL` | The product URL that was scraped |
| `SKU` | The SKU extracted from the URL |
| `Title` | Product title |
| `Price` | Product price |
| `Description` | Product description |
| `Images` | Semicolon-separated list of image URLs |
| `Found` | Boolean indicating if product data was found |
| `Status` | Status: `FOUND`, `NOT_FOUND`, `ERROR`, `TIMEOUT`, `EMPTY` |
| `Note` | Additional notes or error messages |
| `DebugInfo` | JSON debug information (for NOT_FOUND products) |

### Status Values

- **FOUND**: Product data was successfully extracted
- **NOT_FOUND**: Product doesn't exist (Laravel error page detected)
- **ERROR**: An error occurred while scraping
- **TIMEOUT**: Page load timeout
- **EMPTY**: Page loaded but no product data found

## Handling Laravel Error Pages

When a product doesn't exist (e.g., SKU `AS5482402`), the website shows a Laravel error page with:
- Error: `Attempt to read property "item_name" on null`
- Controller: `ShopController@single_product`
- Database tables revealed in debug info

The scraper:
1. ✅ Detects these error pages automatically
2. ✅ Marks products as `NOT_FOUND`
3. ✅ Extracts debug information (database tables, queries)
4. ✅ Continues to next product without crashing

## Example Output

```
🔍 Bashiti Central Scraper
Output: results.xlsx
Pause: 2.0s

📊 Loading Excel file: bashitihardware.xls
📊 Loaded 6052 rows
📋 Using column: 'SKU'
📋 Found 1000 unique SKUs/URLs to scrape

[1/1000] Processing: AS5482402
   → Scraping: https://www.bashiticentral.com/en/single-product/AS5482402
   ⚠️  Product not found (Laravel error page)
    → Status: NOT_FOUND | Found: False | Title: ...

[2/1000] Processing: AS1234567
   → Scraping: https://www.bashiticentral.com/en/single-product/AS1234567
   → Found title: Samsung Galaxy S21...
   → Found price: 899.00 د.ا
   → Found description: 1234 chars
   → Found 5 images
    → Status: FOUND | Found: True | Title: Samsung Galaxy S21...

📊 Scraping Complete!
   Total products: 1000
   Found: 750
   Not Found: 200
   Errors: 50
   Success Rate: 75.0%
   Results saved to: results.xlsx
```

## Tips

1. **Start with a small test**: Test with a few SKUs first before running on large datasets
2. **Use headless mode**: Run with `--headless` on servers or when you don't need to see the browser
3. **Adjust pause time**: Increase `--pause` if you're getting rate-limited
4. **Check NOT_FOUND products**: Review the `DebugInfo` column to understand why products weren't found
5. **Monitor progress**: Watch the console output for real-time scraping status

## Troubleshooting

### "Could not find SKU or URL column"
- Use `--sku-col` to specify the exact column name
- Check that your Excel file has at least one column with SKUs or URLs

### "Timeout loading page"
- Increase the pause time with `--pause 3.0` or higher
- Check your internet connection
- The product might be loading slowly

### "Product not found" for valid SKUs
- The SKU might not exist in the database
- Check the actual URL in a browser to verify
- Review the `DebugInfo` column for more details

## Notes

- The scraper respects the website by including delays between requests
- Laravel error pages are detected and handled gracefully
- All extracted data is saved to Excel for easy analysis
- Debug information is preserved for products that don't exist












