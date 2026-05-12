#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Organdle Website Scraper
Scrapes product images from organdle.co based on product names from Excel file.
"""

import argparse
import pandas as pd
import requests
import time
import logging
from typing import List, Optional, Dict, Any
from urllib.parse import quote, urljoin
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from tqdm import tqdm
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('organdle_scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class OrgandleScraper:
    """Scraper for Organdle website to extract product images."""
    
    def __init__(self, headless: bool = True, delay: float = 2.0):
        """
        Initialize the scraper.
        
        Args:
            headless: Whether to run browser in headless mode
            delay: Delay between requests in seconds
        """
        self.base_url = "https://organdle.co"
        self.search_url = "https://organdle.co/search?q="
        self.delay = delay
        self.driver = None
        self.headless = headless
        
    def setup_driver(self):
        """Setup Chrome WebDriver with appropriate options."""
        try:
            chrome_options = Options()
            
            if self.headless:
                chrome_options.add_argument("--headless")
            
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            chrome_options.add_argument("--disable-web-security")
            chrome_options.add_argument("--disable-features=VizDisplayCompositor")
            
            # Disable images to speed up loading (we'll enable when needed)
            prefs = {
                "profile.managed_default_content_settings.images": 2,
                "profile.default_content_setting_values.notifications": 2
            }
            chrome_options.add_experimental_option("prefs", prefs)
            
            # Try to use existing Chrome installation first
            try:
                # Try to use Chrome without downloading driver
                self.driver = webdriver.Chrome(options=chrome_options)
                logger.info("Chrome WebDriver setup successful (using existing installation)")
            except Exception:
                # Fallback to downloading driver
                try:
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
    
    def search_product(self, product_name: str) -> Optional[str]:
        """
        Search for a product and return the first product page URL.
        
        Args:
            product_name: Name of the product to search for
            
        Returns:
            Product page URL if found, None otherwise
        """
        try:
            # URL encode the product name
            encoded_name = quote(product_name)
            search_url = f"{self.search_url}{encoded_name}"
            
            logger.info(f"Searching for: {product_name}")
            self.driver.get(search_url)
            
            # Wait for search results to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Look for product links in search results
            # Try multiple selectors that might contain product links
            selectors = [
                "a[href*='/products/']",
                ".product-item a",
                ".search-result a",
                "a[href*='product']"
            ]
            
            product_links = []
            for selector in selectors:
                try:
                    links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    product_links.extend([link.get_attribute('href') for link in links if link.get_attribute('href')])
                except:
                    continue
            
            # Filter and clean URLs
            valid_links = []
            for link in product_links:
                if link and '/products/' in link and 'organdle.co' in link:
                    valid_links.append(link)
            
            # Remove duplicates while preserving order
            seen = set()
            unique_links = []
            for link in valid_links:
                if link not in seen:
                    seen.add(link)
                    unique_links.append(link)
            
            if unique_links:
                logger.info(f"Found {len(unique_links)} product(s) for '{product_name}'")
                return unique_links[0]  # Return first match
            else:
                logger.warning(f"No products found for '{product_name}'")
                return None
                
        except TimeoutException:
            logger.error(f"Timeout while searching for '{product_name}'")
            return None
        except Exception as e:
            logger.error(f"Error searching for '{product_name}': {e}")
            return None
    
    def extract_images_from_product_page(self, product_url: str) -> List[str]:
        """
        Extract image URLs from a product page.
        
        Args:
            product_url: URL of the product page
            
        Returns:
            List of image URLs
        """
        try:
            logger.info(f"Extracting images from: {product_url}")
            self.driver.get(product_url)
            
            # Wait for page to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Look for the specific slider gallery element
            slider_selectors = [
                "#Slider-Gallery-template--20051299533037__main",
                "[id*='Slider-Gallery']",
                "[class*='slider-gallery']",
                ".product-gallery",
                ".product-images"
            ]
            
            image_urls = []
            
            for selector in slider_selectors:
                try:
                    # Find the slider gallery element
                    gallery_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    
                    # Find all images within the gallery
                    images = gallery_element.find_elements(By.TAG_NAME, "img")
                    
                    for img in images:
                        src = img.get_attribute('src')
                        if src:
                            # Convert relative URLs to absolute URLs
                            if src.startswith('//'):
                                src = 'https:' + src
                            elif src.startswith('/'):
                                src = self.base_url + src
                            elif not src.startswith('http'):
                                src = urljoin(product_url, src)
                            
                            # Filter out very small images (likely icons or thumbnails)
                            width = img.get_attribute('width')
                            height = img.get_attribute('height')
                            
                            if width and height:
                                try:
                                    if int(width) >= 100 and int(height) >= 100:
                                        image_urls.append(src)
                                except ValueError:
                                    image_urls.append(src)
                            else:
                                image_urls.append(src)
                    
                    if image_urls:
                        logger.info(f"Found {len(image_urls)} images using selector: {selector}")
                        break
                        
                except NoSuchElementException:
                    continue
                except Exception as e:
                    logger.warning(f"Error with selector {selector}: {e}")
                    continue
            
            # If no images found in specific gallery, try general image extraction
            if not image_urls:
                logger.info("No images found in specific gallery, trying general extraction")
                all_images = self.driver.find_elements(By.TAG_NAME, "img")
                
                for img in all_images:
                    src = img.get_attribute('src')
                    if src and ('product' in src.lower() or 'candle' in src.lower()):
                        if src.startswith('//'):
                            src = 'https:' + src
                        elif src.startswith('/'):
                            src = self.base_url + src
                        elif not src.startswith('http'):
                            src = urljoin(product_url, src)
                        
                        image_urls.append(src)
            
            # Remove duplicates while preserving order
            seen = set()
            unique_images = []
            for img in image_urls:
                if img not in seen:
                    seen.add(img)
                    unique_images.append(img)
            
            logger.info(f"Extracted {len(unique_images)} unique images")
            return unique_images
            
        except TimeoutException:
            logger.error(f"Timeout while loading product page: {product_url}")
            return []
        except Exception as e:
            logger.error(f"Error extracting images from {product_url}: {e}")
            return []
    
    def scrape_product_images(self, product_name: str) -> List[str]:
        """
        Scrape images for a single product.
        
        Args:
            product_name: Name of the product to scrape
            
        Returns:
            List of image URLs
        """
        try:
            # Search for the product
            product_url = self.search_product(product_name)
            
            if not product_url:
                return []
            
            # Extract images from the product page
            images = self.extract_images_from_product_page(product_url)
            
            # Add delay between requests
            if self.delay > 0:
                time.sleep(self.delay)
            
            return images
            
        except Exception as e:
            logger.error(f"Error scraping images for '{product_name}': {e}")
            return []
    
    def scrape_excel_file(self, excel_file: str, output_file: str = None) -> pd.DataFrame:
        """
        Scrape images for all products in an Excel file.
        
        Args:
            excel_file: Path to the Excel file
            output_file: Path to save the updated Excel file
            
        Returns:
            Updated DataFrame with image URLs
        """
        try:
            # Read Excel file
            logger.info(f"Reading Excel file: {excel_file}")
            df = pd.read_excel(excel_file)
            
            # Check if 'اسم المنتج' column exists
            if 'اسم المنتج' not in df.columns:
                raise ValueError("Column 'اسم المنتج' not found in Excel file")
            
            # Add new column for images if it doesn't exist
            if 'imgs src' not in df.columns:
                df['imgs src'] = ''
            
            # Setup WebDriver
            self.setup_driver()
            
            try:
                # Process each product
                for index, row in tqdm(df.iterrows(), total=len(df), desc="Scraping products"):
                    product_name = row['اسم المنتج']
                    
                    if pd.isna(product_name) or not str(product_name).strip():
                        logger.warning(f"Empty product name at row {index}")
                        continue
                    
                    product_name = str(product_name).strip()
                    
                    # Skip if already processed
                    if pd.notna(row['imgs src']) and row['imgs src']:
                        logger.info(f"Product '{product_name}' already processed, skipping")
                        continue
                    
                    logger.info(f"Processing product {index + 1}/{len(df)}: {product_name}")
                    
                    # Scrape images
                    images = self.scrape_product_images(product_name)
                    
                    # Update DataFrame
                    if images:
                        df.at[index, 'imgs src'] = ';'.join(images)
                        df.at[index, 'Found'] = 'YES'
                        df.at[index, 'Status'] = f'Found {len(images)} images'
                        logger.info(f"Found {len(images)} images for '{product_name}'")
                    else:
                        df.at[index, 'Found'] = 'NO'
                        df.at[index, 'Status'] = 'No images found'
                        logger.warning(f"No images found for '{product_name}'")
                    
                    # Save progress every 10 products
                    if (index + 1) % 10 == 0:
                        if output_file:
                            df.to_excel(output_file, index=False)
                            logger.info(f"Progress saved at row {index + 1}")
            
            finally:
                # Close WebDriver
                if self.driver:
                    self.driver.quit()
                    logger.info("WebDriver closed")
            
            # Save final results
            if output_file:
                df.to_excel(output_file, index=False)
                logger.info(f"Results saved to: {output_file}")
            
            return df
            
        except Exception as e:
            logger.error(f"Error processing Excel file: {e}")
            raise


def main():
    """Main function to run the scraper."""
    parser = argparse.ArgumentParser(description="Organdle Website Image Scraper")
    parser.add_argument("--input", "-i", required=True, help="Input Excel file path")
    parser.add_argument("--output", "-o", help="Output Excel file path (default: adds '_scraped' to input name)")
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
        input_path = args.input
        if input_path.endswith('.xlsx'):
            output_file = input_path.replace('.xlsx', '_scraped.xlsx')
        else:
            output_file = input_path + '_scraped.xlsx'
    
    try:
        # Create scraper instance
        scraper = OrgandleScraper(headless=headless, delay=args.delay)
        
        # Run scraping
        logger.info("Starting Organdle scraper...")
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
        found_products = len(df[df['Found'] == 'YES']) if 'Found' in df.columns else 0
        total_images = sum(len(str(row['imgs src']).split(';')) for _, row in df.iterrows() if pd.notna(row['imgs src']) and row['imgs src'])
        
        logger.info("=" * 50)
        logger.info("SCRAPING COMPLETED")
        logger.info("=" * 50)
        logger.info(f"Total products processed: {total_products}")
        logger.info(f"Products with images found: {found_products}")
        logger.info(f"Total images extracted: {total_images}")
        logger.info(f"Results saved to: {output_file}")
        
    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
