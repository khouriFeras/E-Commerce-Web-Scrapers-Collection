#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Organdle Website Scraper (Simple Version)
Scrapes product images from organdle.co using requests and BeautifulSoup.
"""

import argparse
import pandas as pd
import requests
import time
import logging
from typing import List, Optional
from urllib.parse import quote, urljoin, urlparse
from bs4 import BeautifulSoup
import re
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('organdle_scraper_simple.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class OrgandleScraperSimple:
    """Simple scraper for Organdle website using requests and BeautifulSoup."""
    
    def __init__(self, delay: float = 2.0):
        """
        Initialize the scraper.
        
        Args:
            delay: Delay between requests in seconds
        """
        self.base_url = "https://organdle.co"
        self.search_url = "https://organdle.co/search?q="
        self.delay = delay
        
        # Setup session with headers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
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
            response = self.session.get(search_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for product links in search results
            product_links = []
            
            # Try multiple selectors that might contain product links
            selectors = [
                "a[href*='/products/']",
                ".product-item a",
                ".search-result a",
                "a[href*='product']",
                "a[href*='/products']"
            ]
            
            for selector in selectors:
                links = soup.select(selector)
                for link in links:
                    href = link.get('href')
                    if href:
                        if href.startswith('/'):
                            href = self.base_url + href
                        elif not href.startswith('http'):
                            href = urljoin(search_url, href)
                        
                        if '/products/' in href and 'organdle.co' in href:
                            product_links.append(href)
            
            # Remove duplicates while preserving order
            seen = set()
            unique_links = []
            for link in product_links:
                if link not in seen:
                    seen.add(link)
                    unique_links.append(link)
            
            if unique_links:
                logger.info(f"Found {len(unique_links)} product(s) for '{product_name}'")
                return unique_links[0]  # Return first match
            else:
                logger.warning(f"No products found for '{product_name}'")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Request error while searching for '{product_name}': {e}")
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
            response = self.session.get(product_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for the specific slider gallery element
            slider_selectors = [
                "#Slider-Gallery-template--20051299533037__main",
                "[id*='Slider-Gallery']",
                "[class*='slider-gallery']",
                ".product-gallery",
                ".product-images",
                ".media-gallery"
            ]
            
            image_urls = []
            
            for selector in slider_selectors:
                try:
                    # Find the slider gallery element
                    gallery_element = soup.select_one(selector)
                    
                    if gallery_element:
                        # Find all images within the gallery
                        images = gallery_element.find_all('img')
                        
                        for img in images:
                            src = img.get('src')
                            if src:
                                # Convert relative URLs to absolute URLs
                                if src.startswith('//'):
                                    src = 'https:' + src
                                elif src.startswith('/'):
                                    src = self.base_url + src
                                elif not src.startswith('http'):
                                    src = urljoin(product_url, src)
                                
                                # Filter out very small images (likely icons or thumbnails)
                                width = img.get('width')
                                height = img.get('height')
                                
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
                            
                except Exception as e:
                    logger.warning(f"Error with selector {selector}: {e}")
                    continue
            
            # If no images found in specific gallery, try general image extraction
            if not image_urls:
                logger.info("No images found in specific gallery, trying general extraction")
                all_images = soup.find_all('img')
                
                for img in all_images:
                    src = img.get('src')
                    if src and ('product' in src.lower() or 'candle' in src.lower() or 'lollipop' in src.lower()):
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
            
        except requests.RequestException as e:
            logger.error(f"Request error while loading product page: {product_url} - {e}")
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
            
            # Add new columns for results if they don't exist
            if 'imgs src' not in df.columns:
                df['imgs src'] = ''
            if 'Found' not in df.columns:
                df['Found'] = ''
            if 'Status' not in df.columns:
                df['Status'] = ''
            
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
                
                # Save progress every 5 products
                if (index + 1) % 5 == 0:
                    if output_file:
                        df.to_excel(output_file, index=False)
                        logger.info(f"Progress saved at row {index + 1}")
            
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
    parser = argparse.ArgumentParser(description="Organdle Website Image Scraper (Simple Version)")
    parser.add_argument("--input", "-i", required=True, help="Input Excel file path")
    parser.add_argument("--output", "-o", help="Output Excel file path (default: adds '_scraped' to input name)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests in seconds (default: 2.0)")
    parser.add_argument("--limit", type=int, help="Process only first N rows")
    
    args = parser.parse_args()
    
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
        scraper = OrgandleScraperSimple(delay=args.delay)
        
        # Run scraping
        logger.info("Starting Organdle scraper (simple version)...")
        logger.info(f"Input file: {args.input}")
        logger.info(f"Output file: {output_file}")
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

