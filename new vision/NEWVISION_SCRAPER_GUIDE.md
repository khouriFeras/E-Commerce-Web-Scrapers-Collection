# New Vision Scraper Guide

This guide explains how to use the New Vision scrapers to extract product information from the New Vision website.

## Available Scrapers

### 1. LGScraper.py - Specialized LG Refrigerators Scraper
- **Purpose**: Scrapes all LG refrigerators from the New Vision website
- **URL**: https://newvision.jo/product-category/refrigerators-ar/
- **Features**: 
  - Extracts product titles, prices, descriptions, and images
  - Handles Arabic content
  - Optimized for refrigerator products

### 2. newvision_universal_scraper.py - Universal Category Scraper
- **Purpose**: Scrapes any product category from New Vision
- **Features**:
  - Works with any category page
  - Enhanced data extraction (SKU, availability, discounts)
  - Auto-detects product category
  - More comprehensive product information

## Usage Examples

### Basic Usage - LG Refrigerators
```bash
# Scrape all LG refrigerators (headless mode)
python LGScraper.py

# Scrape with visible browser
python LGScraper.py --headful

# Custom output file
python LGScraper.py --output my_refrigerators.xlsx

# Faster scraping (1 second delay)
python LGScraper.py --delay 1.0
```

### Universal Scraper - Any Category
```bash
# Scrape TVs
python newvision_universal_scraper.py --url "https://newvision.jo/product-category/tvs-ar/" --output tvs.xlsx

# Scrape Washing Machines
python newvision_universal_scraper.py --url "https://newvision.jo/product-category/washing-machines-ar/" --output washing_machines.xlsx

# Scrape Air Conditioners
python newvision_universal_scraper.py --url "https://newvision.jo/product-category/air-conditioners-ar/" --output ac.xlsx

# Scrape with visible browser and custom delay
python newvision_universal_scraper.py --url "https://newvision.jo/product-category/refrigerators-ar/" --headful --delay 1.5
```

## Command Line Options

### LGScraper.py Options
- `--url`: Category URL (default: refrigerators page)
- `--output`, `-o`: Output Excel file path
- `--headful`: Run browser in visible mode
- `--delay`: Delay between requests in seconds

### newvision_universal_scraper.py Options
- `--url`: **Required** - Category URL to scrape
- `--output`, `-o`: Output Excel file path
- `--headful`: Run browser in visible mode
- `--delay`: Delay between requests in seconds

## Output Data

Both scrapers generate Excel files with the following columns:

### Basic Columns (LGScraper.py)
- `url`: Product URL
- `title`: Product title (Arabic)
- `price`: Current price
- `description`: Product description
- `images`: Semicolon-separated image URLs
- `specifications`: Product specifications
- `brand`: Brand name (LG)
- `category`: Product category
- `status`: Scraping status (SUCCESS/PARTIAL/FAILED)

### Enhanced Columns (newvision_universal_scraper.py)
- All basic columns plus:
- `original_price`: Original price (if discounted)
- `discount`: Discount percentage
- `features`: Product features
- `sku`: Product SKU
- `availability`: Stock availability
- `error`: Error message (if failed)

## Supported Categories

The universal scraper can handle any New Vision category:
- Refrigerators: `/product-category/refrigerators-ar/`
- TVs: `/product-category/tvs-ar/`
- Washing Machines: `/product-category/washing-machines-ar/`
- Air Conditioners: `/product-category/air-conditioners-ar/`
- Microwaves: `/product-category/microwaves-ar/`
- And more...

## Tips for Best Results

1. **Use appropriate delays**: 2-3 seconds between requests to avoid being blocked
2. **Monitor the output**: Use `--headful` to see what's happening
3. **Check the results**: Verify the Excel output for completeness
4. **Handle errors**: Some products may fail to scrape - check the status column
5. **Image quality**: Images are automatically cleaned to get original sizes

## Troubleshooting

### Common Issues
1. **No products found**: Check if the URL is correct and accessible
2. **Empty results**: The page might be loading dynamically - try increasing delays
3. **Browser errors**: Make sure Chrome is installed and up to date
4. **Permission errors**: Ensure you have write permissions for the output directory

### Error Status Meanings
- `SUCCESS`: Product scraped completely
- `PARTIAL`: Some data missing (e.g., no images or description)
- `FAILED`: Product could not be scraped

## Example Output

The scrapers will show progress like this:
```
🚀 Starting New Vision LG Scraper
   Base URL: https://newvision.jo/product-category/refrigerators-ar/
   Output: refrigerators.xlsx
   Delay: 2.0s between requests

🔍 Scraping product links from: https://newvision.jo/product-category/refrigerators-ar/
   → Scrolling to load all products...
   → Found 11 unique products

📦 Found 11 products to scrape
============================================================

[1/11] ثلاجة فريزر علوي ، 423 لتر سعة اجمالية...
   → Scraping: https://newvision.jo/product/glb-582gvlp-dpzpelf-ar/
     ✓ Title: ثلاجة فريزر علوي ، 423 لتر سعة اجمالية...
     ✓ Price: 479 JOD
     ✓ Images: 15
     ✓ Description: 156 chars

============================================================
📊 SCRAPING SUMMARY
============================================================
Total products: 11
Successful: 11
Partial: 0
Failed: 0
Total images: 155
Saved to: refrigerators.xlsx
```

