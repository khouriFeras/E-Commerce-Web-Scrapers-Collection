#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
G-Guard Website Scraper
Scrapes product images, titles, and prices from gguard.com based on SKUs from Excel file.
"""

import argparse
import pandas as pd
import time
import logging
from pathlib import Path
from typing import Optional, Dict, List
from urllib.parse import quote, urljoin
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from tqdm import tqdm
import re

# Configure logging with UTF-8 encoding
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('gguard_scraper.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def save_excel_file(df: pd.DataFrame, file_path: str) -> str:
    """
    Save DataFrame to Excel file, handling both .xls and .xlsx formats.
    For .xls files, converts to .xlsx format.
    
    Returns:
        The actual file path used for saving
    """
    file_path_obj = Path(file_path)
    file_ext = file_path_obj.suffix.lower()
    
    if file_ext == '.xls':
        # Convert .xls to .xlsx (pandas can't write .xls without xlwt)
        output_path = file_path_obj.with_suffix('.xlsx')
        logger.info(f"Converting .xls to .xlsx format: {output_path}")
        df.to_excel(output_path, index=False, engine='openpyxl')
        return str(output_path)
    elif file_ext == '.xlsx':
        df.to_excel(file_path, index=False, engine='openpyxl')
        return file_path
    else:
        # Default to .xlsx
        output_path = file_path_obj.with_suffix('.xlsx')
        logger.info(f"Saving as .xlsx: {output_path}")
        df.to_excel(output_path, index=False, engine='openpyxl')
        return str(output_path)


class GGuardScraper:
    """Scraper for G-Guard website to extract product images, titles, and prices."""
    
    def __init__(self, headless: bool = True, delay: float = 2.0):
        """
        Initialize the scraper.
        
        Args:
            headless: Whether to run browser in headless mode
            delay: Delay between requests in seconds
        """
        self.base_url = "https://gguard.com"
        self.search_url_template = "https://gguard.com/?s={sku}&post_type=product"
        self.delay = delay
        self.driver = None
        self.headless = headless
        
    def setup_driver(self):
        """Setup Chrome WebDriver with appropriate options."""
        try:
            chrome_options = Options()
            
            if self.headless:
                chrome_options.add_argument("--headless=new")
            
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            chrome_options.add_argument("--disable-web-security")
            chrome_options.add_argument("--disable-features=VizDisplayCompositor")
            
            # Enable images
            prefs = {
                "profile.managed_default_content_settings.images": 1,
                "profile.default_content_setting_values.notifications": 2
            }
            chrome_options.add_experimental_option("prefs", prefs)
            
            # Try to use existing Chrome installation first
            try:
                self.driver = webdriver.Chrome(options=chrome_options)
                logger.info("Chrome WebDriver setup successful (using existing installation)")
            except Exception:
                # Fallback to downloading driver
                try:
                    from webdriver_manager.chrome import ChromeDriverManager
                    from selenium.webdriver.chrome.service import Service
                    service = Service(ChromeDriverManager().install())
                    self.driver = webdriver.Chrome(service=service, options=chrome_options)
                    logger.info("Chrome WebDriver setup successful (downloaded driver)")
                except Exception as e2:
                    logger.error(f"Failed to setup WebDriver with ChromeDriverManager: {e2}")
                    raise
            
            self.driver.set_page_load_timeout(30)
            
        except Exception as e:
            logger.error(f"Failed to setup WebDriver: {e}")
            raise
    
    def search_product(self, sku: str) -> Optional[str]:
        """
        Search for a product by SKU and return the first product page URL.
        
        Args:
            sku: SKU of the product to search for
            
        Returns:
            Product page URL if found, None otherwise
        """
        try:
            # URL encode the SKU
            encoded_sku = quote(sku)
            search_url = self.search_url_template.format(sku=encoded_sku)
            
            logger.info(f"Searching for SKU: {sku}")
            self.driver.get(search_url)
            
            # Wait for search results to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(1)  # Give page a moment to render
            
            # Look for product links in search results
            # Try multiple selectors that might contain product links
            selectors = [
                "a[href*='/product/']",
                ".product-item a",
                ".woocommerce-loop-product__link",
                ".product a",
                "article.product a",
                ".wd-products-grid a",
                "a[href*='product']"
            ]
            
            product_links = []
            for selector in selectors:
                try:
                    links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for link in links:
                        href = link.get_attribute('href')
                        if href and ('/product/' in href or 'product' in href.lower()):
                            # Filter out non-product pages
                            if href.startswith('http') and 'gguard.com' in href:
                                product_links.append(href)
                except:
                    continue
            
            # Also try finding links by text content or data attributes
            if not product_links:
                try:
                    all_links = self.driver.find_elements(By.TAG_NAME, "a")
                    for link in all_links:
                        href = link.get_attribute('href')
                        if href and '/product/' in href and 'gguard.com' in href:
                            product_links.append(href)
                except:
                    pass
            
            # Remove duplicates while preserving order
            seen = set()
            unique_links = []
            for link in product_links:
                if link not in seen:
                    seen.add(link)
                    unique_links.append(link)
            
            if unique_links:
                logger.info(f"Found {len(unique_links)} product(s) for SKU '{sku}'")
                return unique_links[0]  # Return first match
            else:
                logger.warning(f"No products found for SKU '{sku}'")
                return None
                
        except TimeoutException:
            logger.error(f"Timeout while searching for SKU '{sku}'")
            return None
        except Exception as e:
            logger.error(f"Error searching for SKU '{sku}': {e}")
            return None
    
    def extract_product_data(self, product_url: str) -> Dict[str, str]:
        """
        Extract product image, title, and price from a product page.
        
        Args:
            product_url: URL of the product page
            
        Returns:
            Dictionary with image_url, title, old_price, new_price
        """
        result = {
            "image_url": "",
            "title": "",
            "old_price": "",
            "new_price": ""
        }
        
        try:
            logger.info(f"Extracting data from: {product_url}")
            self.driver.get(product_url)
            
            # Wait for page to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(1)  # Give page a moment to render
            
            # Extract image from class="product-images wd-grid-col"
            try:
                image_container = self.driver.find_element(By.CSS_SELECTOR, ".product-images.wd-grid-col")
                images = image_container.find_elements(By.TAG_NAME, "img")
                
                # Try to get the main image (usually the first one, or one with data-zoom-image)
                img_src = None
                for img in images:
                    # Prefer data-zoom-image or data-large_image attributes
                    img_src = img.get_attribute('data-zoom-image') or \
                             img.get_attribute('data-large_image') or \
                             img.get_attribute('data-src') or \
                             img.get_attribute('src')
                    
                    if img_src:
                        # Skip placeholder images
                        if 'placeholder' in img_src.lower() or 'data:image' in img_src.lower():
                            continue
                        break
                
                if img_src:
                    # Convert relative URLs to absolute URLs
                    if img_src.startswith('//'):
                        img_src = 'https:' + img_src
                    elif img_src.startswith('/'):
                        img_src = self.base_url + img_src
                    elif not img_src.startswith('http'):
                        img_src = urljoin(product_url, img_src)
                    result["image_url"] = img_src
                    logger.info(f"Found image: {img_src}")
            except NoSuchElementException:
                # Try alternative selectors
                try:
                    alt_selectors = [
                        ".product-images img",
                        ".woocommerce-product-gallery__image img",
                        ".product-gallery img"
                    ]
                    for selector in alt_selectors:
                        try:
                            img = self.driver.find_element(By.CSS_SELECTOR, selector)
                            img_src = img.get_attribute('src') or img.get_attribute('data-src')
                            if img_src and 'placeholder' not in img_src.lower():
                                if img_src.startswith('//'):
                                    img_src = 'https:' + img_src
                                elif img_src.startswith('/'):
                                    img_src = self.base_url + img_src
                                elif not img_src.startswith('http'):
                                    img_src = urljoin(product_url, img_src)
                                result["image_url"] = img_src
                                logger.info(f"Found image (alternative): {img_src}")
                                break
                        except NoSuchElementException:
                            continue
                except Exception as e2:
                    logger.warning(f"Error with alternative image extraction: {e2}")
            except Exception as e:
                logger.warning(f"Error extracting image: {e}")
            
            # Extract title from class="product_title entry-title wd-entities-title"
            try:
                title_element = self.driver.find_element(By.CSS_SELECTOR, ".product_title.entry-title.wd-entities-title")
                result["title"] = title_element.text.strip()
                logger.info(f"Found title: {result['title']}")
            except NoSuchElementException:
                # Try alternative selectors
                try:
                    alt_selectors = [
                        "h1.product_title",
                        ".product_title",
                        "h1.entry-title",
                        ".entry-title",
                        "h1"
                    ]
                    for selector in alt_selectors:
                        try:
                            title_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                            title_text = title_elem.text.strip()
                            if title_text:
                                result["title"] = title_text
                                logger.info(f"Found title (alternative): {result['title']}")
                                break
                        except NoSuchElementException:
                            continue
                except Exception as e2:
                    logger.warning(f"Error with alternative title extraction: {e2}")
            except Exception as e:
                logger.warning(f"Error extracting title: {e}")
            
            # Extract prices from class="price"
            try:
                price_container = self.driver.find_element(By.CSS_SELECTOR, ".price")
                price_elements = price_container.find_elements(By.CSS_SELECTOR, "*")
                
                # Look for elements with aria-hidden="true" (old price)
                for price_elem in price_elements:
                    aria_hidden = price_elem.get_attribute('aria-hidden')
                    price_text = price_elem.text.strip()
                    
                    if price_text:
                        if aria_hidden == "true":
                            # This is the old price
                            result["old_price"] = price_text
                            logger.info(f"Found old price: {price_text}")
                        elif not result["new_price"]:
                            # This might be the new price (if no aria-hidden)
                            result["new_price"] = price_text
                            logger.info(f"Found new price: {price_text}")
                
                # If we still don't have prices, try getting text from the container itself
                if not result["old_price"] and not result["new_price"]:
                    container_text = price_container.text.strip()
                    if container_text:
                        # Try to parse prices from the text
                        # Look for patterns like "د.ا288.00" or "د.ا219.00"
                        prices = re.findall(r'د\.ا[\d,]+\.?\d*', container_text)
                        if len(prices) >= 2:
                            result["old_price"] = prices[0]
                            result["new_price"] = prices[-1]
                        elif len(prices) == 1:
                            result["new_price"] = prices[0]
                
                # Alternative: Look for del/ins tags or specific price classes
                if not result["old_price"]:
                    try:
                        del_elem = price_container.find_element(By.CSS_SELECTOR, "del, .woocommerce-Price-amount.amount del")
                        result["old_price"] = del_elem.text.strip()
                    except NoSuchElementException:
                        pass
                
                if not result["new_price"]:
                    try:
                        # Look for ins or the last price amount
                        ins_elem = price_container.find_element(By.CSS_SELECTOR, "ins .woocommerce-Price-amount.amount, .woocommerce-Price-amount.amount:not(del)")
                        result["new_price"] = ins_elem.text.strip()
                    except NoSuchElementException:
                        # Try getting the last price amount
                        try:
                            amounts = price_container.find_elements(By.CSS_SELECTOR, ".woocommerce-Price-amount.amount")
                            if amounts:
                                result["new_price"] = amounts[-1].text.strip()
                        except:
                            pass
                            
            except NoSuchElementException:
                logger.warning("Price container not found")
            except Exception as e:
                logger.warning(f"Error extracting prices: {e}")
            
        except TimeoutException:
            logger.error(f"Timeout while loading product page: {product_url}")
        except Exception as e:
            logger.error(f"Error extracting data from {product_url}: {e}")
        
        return result
    
    def scrape_product(self, sku: str) -> Dict[str, str]:
        """
        Scrape data for a single product by SKU.
        
        Args:
            sku: SKU of the product to scrape
            
        Returns:
            Dictionary with scraped data
        """
        try:
            # Search for the product
            product_url = self.search_product(sku)
            
            if not product_url:
                return {
                    "SKU": sku,
                    "image_url": "",
                    "title": "",
                    "old_price": "",
                    "new_price": "",
                    "product_url": "",
                    "status": "not_found"
                }
            
            # Extract data from the product page
            data = self.extract_product_data(product_url)
            data["SKU"] = sku
            data["product_url"] = product_url
            data["status"] = "found"
            
            # Add delay between requests
            if self.delay > 0:
                time.sleep(self.delay)
            
            return data
            
        except Exception as e:
            logger.error(f"Error scraping product for SKU '{sku}': {e}")
            return {
                "SKU": sku,
                "image_url": "",
                "title": "",
                "old_price": "",
                "new_price": "",
                "product_url": "",
                "status": f"error: {e}"
            }
    
    def scrape_excel_file(self, excel_file: str, output_file: str = None) -> pd.DataFrame:
        """
        Scrape data for all products in an Excel file.
        
        Args:
            excel_file: Path to the Excel file
            output_file: Path to save the updated Excel file (default: same as input)
            
        Returns:
            Updated DataFrame with scraped data
        """
        try:
            # Read Excel file (supports both .xls and .xlsx)
            logger.info(f"Reading Excel file: {excel_file}")
            file_ext = Path(excel_file).suffix.lower()
            if file_ext == '.xls':
                # Try reading with xlrd engine, fallback to openpyxl if xlrd not available
                try:
                    df = pd.read_excel(excel_file, engine='xlrd')
                except Exception:
                    # If xlrd fails, try without specifying engine
                    df = pd.read_excel(excel_file)
            else:
                df = pd.read_excel(excel_file, engine='openpyxl')
            
            # Check if 'SKU' column exists
            if 'SKU' not in df.columns:
                raise ValueError("Column 'SKU' not found in Excel file")
            
            # Add new columns for scraped data if they don't exist
            new_columns = {
                'image_url': '',
                'title': '',
                'old_price': '',
                'new_price': '',
                'product_url': '',
                'scrape_status': ''
            }
            for col, default_val in new_columns.items():
                if col not in df.columns:
                    df[col] = default_val
            
            # Setup WebDriver
            self.setup_driver()
            
            try:
                # Process each SKU
                for index, row in tqdm(df.iterrows(), total=len(df), desc="Scraping products"):
                    sku = row['SKU']
                    
                    if pd.isna(sku) or not str(sku).strip():
                        logger.warning(f"Empty SKU at row {index}")
                        df.at[index, 'scrape_status'] = 'skip: empty sku'
                        continue
                    
                    sku = str(sku).strip()
                    
                    # Skip if already processed (has product_url)
                    if pd.notna(row.get('product_url')) and row.get('product_url'):
                        logger.info(f"SKU '{sku}' already processed, skipping")
                        continue
                    
                    logger.info(f"Processing SKU {index + 1}/{len(df)}: {sku}")
                    
                    # Scrape product data
                    data = self.scrape_product(sku)
                    
                    # Update DataFrame
                    df.at[index, 'image_url'] = data.get('image_url', '')
                    df.at[index, 'title'] = data.get('title', '')
                    df.at[index, 'old_price'] = data.get('old_price', '')
                    df.at[index, 'new_price'] = data.get('new_price', '')
                    df.at[index, 'product_url'] = data.get('product_url', '')
                    df.at[index, 'scrape_status'] = data.get('status', '')
                    
                    # Save progress every 5 products
                    if (index + 1) % 5 == 0:
                        if output_file:
                            actual_output = save_excel_file(df, output_file)
                            logger.info(f"Progress saved at row {index + 1} to {actual_output}")
            
            finally:
                # Close WebDriver
                if self.driver:
                    self.driver.quit()
                    logger.info("WebDriver closed")
            
            # Save final results
            if output_file:
                actual_output = save_excel_file(df, output_file)
                logger.info(f"Results saved to: {actual_output}")
            
            return df
            
        except Exception as e:
            logger.error(f"Error processing Excel file: {e}")
            raise


def main():
    """Main function to run the scraper."""
    parser = argparse.ArgumentParser(description="G-Guard Website Scraper")
    parser.add_argument("--input", "-i", required=True, help="Input Excel file path")
    parser.add_argument("--output", "-o", help="Output Excel file path (default: same as input)")
    parser.add_argument("--headless", action="store_true", default=True, help="Run in headless mode (default: True)")
    parser.add_argument("--headful", action="store_true", help="Run with visible browser window")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests in seconds (default: 2.0)")
    parser.add_argument("--limit", type=int, help="Process only first N rows")
    
    args = parser.parse_args()
    
    # Determine headless mode
    headless = not args.headful if args.headful else args.headless
    
    # Determine output file
    if args.output:
        output_file = args.output
    else:
        output_file = args.input  # Default: overwrite input file
    
    try:
        # Create scraper instance
        scraper = GGuardScraper(headless=headless, delay=args.delay)
        
        # Run scraping
        logger.info("Starting G-Guard scraper...")
        logger.info(f"Input file: {args.input}")
        logger.info(f"Output file: {output_file}")
        logger.info(f"Headless mode: {headless}")
        logger.info(f"Delay: {args.delay}s")
        
        df = scraper.scrape_excel_file(args.input, output_file)
        
        # Apply limit if specified
        if args.limit:
            df = df.head(args.limit)
        
        # Print summary
        total_products = len(df)
        found_products = len(df[df['scrape_status'] == 'found']) if 'scrape_status' in df.columns else 0
        not_found_products = len(df[df['scrape_status'] == 'not_found']) if 'scrape_status' in df.columns else 0
        
        logger.info("=" * 50)
        logger.info("SCRAPING COMPLETED")
        logger.info("=" * 50)
        logger.info(f"Total products processed: {total_products}")
        logger.info(f"Products found: {found_products}")
        logger.info(f"Products not found: {not_found_products}")
        logger.info(f"Results saved to: {output_file}")
        
    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())

