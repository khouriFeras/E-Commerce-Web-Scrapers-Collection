# E-Commerce Web Scrapers Collection

A comprehensive collection of Python web scrapers for various e-commerce websites. Each scraper is designed to extract product information including descriptions, images, prices, and other relevant data from different online stores.

> Features

- **12+ Specialized Scrapers** for different e-commerce sites
- **Automated Browser Control** using Selenium and Playwright
- **Excel/CSV Support** for input/output data processing
- **Robust Error Handling** and retry mechanisms
- **Headless/Headful Modes** for different use cases
- **Progress Tracking** with checkpoint saves
- **Multi-threading Support** where applicable

## 📋 Supported Websites

| Scraper                  | Website                               | Description                                          |
| ------------------------ | ------------------------------------- | ---------------------------------------------------- |
| `arabiMart.py`         | [Arabi E-Mart](https://arabiemart.com)   | Product search and data extraction                   |
| `birdsland_scraper.py` | [Birdsland JO](https://birdslandjo.com)  | SKU-based product scraping                           |
| `ferplast_scraper.py`  | [Ferplast](https://int.ferplast.com)     | Product characteristics and descriptions             |
| `InfoScraper.py`       | [Arabi E-Mart](https://arabiemart.com)   | General product information scraper                  |
| `jo_cell_scraper.py`   | [Jo-Cell](https://jo-cell.com)           | Mobile accessories and electronics                   |
| `layorGroup.py`        | [Laroy/Duvo+](https://shop.laroyduvo.be) | Product gallery and descriptions                     |
| `linker.py`            | [Arabi E-Mart](https://arabiemart.com)   | Seller linking and URL resolution                    |
| `scraperGoat.py`       | Multiple sites                        | Generic product scraper for various e-commerce sites |
| `suppliersScrappar.py` | [Arabi E-Mart](https://arabiemart.com)   | Supplier information extraction                      |
| `woolapet_scraper.py`  | [Woolapet](https://woolapet.es)          | Pet supplies and accessories                         |
| `zepter_scraper.py`    | [Zepter JO](https://shop.zepter.com.jo)  | Kitchen and home products                            |
| `samsungaci.py`        | [Samsung ACI](https://samsungaci.com)    | Samsung products and accessories                     |

## Installation

### Prerequisites

- Python 3.8 or higher
- Chrome browser (for Selenium)
- Git

### Quick Setup

1. **Clone the repository:**

   ```bash
   git clone [PRIVATE_REPO_URL]
   cd scrapers
   ```
2. **Create and activate virtual environment:**

   ```bash
   # Windows
   python -m venv scraper_env
   scraper_env\Scripts\activate

   # macOS/Linux
   python3 -m venv scraper_env
   source scraper_env/bin/activate
   ```
3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   playwright install
   ```
4. **Verify installation:**

   ```bash
   python -c "import arabiMart; print('Setup successful!')"
   ```

## Usage

### Basic Usage

Each scraper follows a similar pattern:

```bash
python scraper_name.py --in input.xlsx --out output.xlsx --sku-col "SKU"
```

### Example Commands

```bash
# Arabi Mart scraper
python arabiMart.py --in products.xlsx --out results.xlsx --sku-col "Variant SKU" --headful

# Birdsland scraper (headless mode)
python birdsland_scraper.py --in skus.csv --out output.csv --sku-col "SKU" --headless

# Generic scraper for multiple URLs
python scraperGoat.py --in urls.txt --out results.csv

# Samsung ACI scraper with custom settings
python samsungaci.py --in data.xlsx --sku-col "Item" --sheet "Products" --pause 1.5
```

### Common Parameters

- `--in`: Input file (Excel .xlsx or CSV)
- `--out`: Output file path
- `--sku-col`: Column name containing SKUs/product codes
- `--headful`: Run with visible browser window
- `--headless`: Run in background (default)
- `--pause`: Delay between requests (seconds)
- `--limit`: Process only first N rows
- `--sample`: Randomly sample N rows
- `--checkpoint`: Save progress every N rows

## Project Structure

```
ecommerce-scrapers/
├── 📄 README.md                 # This file
├── 📄 requirements.txt          # Python dependencies
├── 📄 LICENSE                   # MIT License
├── 📄 CONTRIBUTING.md           # Contribution guidelines
├── 🔧 setup.py                  # Installation script
├── 🔧 activate_env.bat          # Windows activation script
├── 🕷️ arabiMart.py             # Arabi E-Mart scraper
├── 🕷️ birdsland_scraper.py     # Birdsland JO scraper
├── 🕷️ ferplast_scraper.py      # Ferplast scraper
├── 🕷️ InfoScraper.py           # General info scraper
├── 🕷️ jo_cell_scraper.py       # Jo-Cell scraper
├── 🕷️ layorGroup.py            # Laroy/Duvo+ scraper
├── 🕷️ linker.py                # Seller linker
├── 🕷️ scraperGoat.py           # Generic scraper
├── 🕷️ suppliersScrappar.py     # Supplier scraper
├── 🕷️ woolapet_scraper.py      # Woolapet scraper
├── 🕷️ zepter_scraper.py        # Zepter JO scraper
├── 🕷️ samsungaci.py            # Samsung ACI scraper
├── 📁 examples/                 # Example input files
├── 📁 docs/                     # Documentation
└── 📁 tests/                    # Unit tests
```

## Dependencies

### Core Libraries

- **selenium** (4.35.0) - Web browser automation
- **playwright** (1.55.0) - Modern browser automation
- **pandas** (2.3.2) - Data manipulation and analysis
- **beautifulsoup4** (4.13.5) - HTML/XML parsing
- **requests** (2.32.5) - HTTP library
- **lxml** (6.0.1) - XML/HTML processing

### Data Processing

- **openpyxl** (3.1.5) - Excel file handling
- **tqdm** (4.67.1) - Progress bars
- **tldextract** (5.3.0) - URL domain extraction

### Utilities

- **webdriver-manager** (4.0.2) - Automatic driver management

## Output Format

All scrapers output data in a consistent format:

| Column          | Description                            |
| --------------- | -------------------------------------- |
| `SKU`         | Original product code/SKU              |
| `Description` | Product description (HTML or text)     |
| `Image Src`   | Semicolon-separated image URLs         |
| `Source_URL`  | Product page URL                       |
| `Found`       | YES/NO indicating if product was found |
| `Status`      | Additional status information          |

## Important Notes

### Legal and Ethical Considerations

- **Respect robots.txt**: Always check website's robots.txt file
- **Rate Limiting**: Use appropriate delays between requests
- **Terms of Service**: Ensure compliance with website terms
- **Data Usage**: Use scraped data responsibly and legally

### Technical Considerations

- **Browser Requirements**: Chrome browser required for Selenium
- **Memory Usage**: Large datasets may require significant RAM
- **Network Stability**: Ensure stable internet connection
- **Anti-bot Measures**: Some sites may implement anti-scraping measures

## Testing

Run tests to verify everything works:

```bash
# Test all scrapers can import
python -c "import arabiMart, birdsland_scraper, ferplast_scraper; print('All imports successful!')"

# Test with sample data
python scraperGoat.py --url "https://example.com/product" --out test.csv
```

## Adding New Scrapers

1. Follow the existing code structure
2. Include comprehensive error handling
3. Add command-line argument support
4. Include usage documentation
5. Test with sample data

### Code Review Process

- All changes require team review
- Test with sample data before submission
- Update documentation for new features
- Follow established coding standards

## v1.0.0

- Initial release with 12 scrapers
- Support for major e-commerce sites
- Excel/CSV input/output support
- Comprehensive error handling
- Documentation and examples
#
