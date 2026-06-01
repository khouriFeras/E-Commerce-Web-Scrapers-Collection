#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import time
from typing import List, Dict, Any, Optional
import pandas as pd
from urllib.parse import urljoin, urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains
def build_driver(headful: bool = False) -> webdriver.Chrome:
    """Build Chrome driver with proper options."""
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver
def wait_css(driver, sel, timeout=15):
    """Wait for CSS selector to be present."""
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
def normalize(s: str) -> str:
    """Normalize string for comparison."""
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())
def clean_image_url(url: str) -> str:
    """Clean image URL by removing size parameters to get the original image."""
    if not url:
        return url
    # Remove common size parameters
    url = re.sub(r'-\d+x\d+\.', '.', url)  # Remove -700x465.jpg -> .jpg
    url = re.sub(r'[?&](w|h|width|height|size|resize|fit|format|auto)=[^&]*', '', url)
    url = re.sub(r'[?&]+$', '', url)
    return url
def extract_structured_specifications(driver) -> Dict[str, Any]:
    """Extract structured specifications from the product page."""
    specs_data = {
        'summary': {},
        'capacity': {},
        'general_functions': {},
        'refrigerator': {},
        'freezer': {}
    }
    
    try:
        # Get all text content from the page
        page_text = driver.find_element(By.TAG_NAME, 'body').text
        
        # Look for the specific specification sections based on the provided data
        lines = page_text.split('\n')
        current_section = 'summary'
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            # Check for section headers
            if 'الملخص' in line:
                current_section = 'summary'
                continue
            elif 'السعة' in line and 'لتر' in line:
                current_section = 'capacity'
                continue
            elif 'الوظائف العامة' in line:
                current_section = 'general_functions'
                continue
            elif 'الثلاجة' in line:
                current_section = 'refrigerator'
                continue
            elif 'المجمد' in line:
                current_section = 'freezer'
                continue
            
            # Try to extract key-value pairs
            # Look for various patterns that match the provided data
            patterns = [
                r'^(.+?)\s+(.+)$',  # General pattern: key value
                r'^(.+?):\s*(.+)$',  # Colon separator
                r'^(.+?)\s*-\s*(.+)$',  # Dash separator
                r'^(.+?)\s*\|\s*(.+)$',  # Pipe separator
            ]
            
            for pattern in patterns:
                match = re.match(pattern, line)
                if match:
                    key = match.group(1).strip()
                    value = match.group(2).strip()
                    
                    # Filter out very short or very long keys
                    if 2 <= len(key) <= 50 and len(value) <= 200:
                        # Clean up the key and value
                        key = re.sub(r'[^\w\s\u0600-\u06FF]', '', key)  # Keep Arabic and alphanumeric
                        value = re.sub(r'[^\w\s\u0600-\u06FF\-\.]', '', value)  # Keep Arabic, alphanumeric, dash, dot
                        
                        if key and value:
                            specs_data[current_section][key] = value
                            break
        
        # Also try to extract from specific elements
        spec_selectors = [
            '.woocommerce-product-attributes',
            '.product-attributes',
            '.specifications',
            '.product-specs',
            '.specs-section',
            '#specsTable',
            '.product-details',
            '.product-info',
            '.product-summary',
            '.product-description',
            '[class*="spec"]',
            '[id*="spec"]'
        ]
        
        for selector in spec_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.text.strip():
                        text_content = element.text.strip()
                        
                        # Process the text content
                        lines = text_content.split('\n')
                        current_section = 'summary'
                        
                        for line in lines:
                            line = line.strip()
                            if not line:
                                continue
                            
                            # Check for section headers
                            if 'الملخص' in line:
                                current_section = 'summary'
                            elif 'السعة' in line and 'لتر' in line:
                                current_section = 'capacity'
                            elif 'الوظائف العامة' in line:
                                current_section = 'general_functions'
                            elif 'الثلاجة' in line:
                                current_section = 'refrigerator'
                            elif 'المجمد' in line:
                                current_section = 'freezer'
                            else:
                                # Try to extract key-value pairs
                                separators = ['\t', ':', ' - ', ' | ', ' ']
                                for sep in separators:
                                    if sep in line and line.count(sep) == 1:
                                        parts = line.split(sep, 1)
                                        if len(parts) == 2:
                                            key = parts[0].strip()
                                            value = parts[1].strip()
                                            if key and value and len(key) < 100:
                                                specs_data[current_section][key] = value
                                                break
            except:
                continue
                
    except Exception as e:
        print(f"     × Error extracting structured specifications: {e}")
    
    return specs_data


def get_product_links(driver, base_url: str) -> List[Dict[str, str]]:
    """Get all product links from the category page."""
    print(f"🔍 Scraping product links from: {base_url}")
    
    driver.get(base_url)
    time.sleep(5)
    
    # Scroll to load all products
    print("   → Scrolling to load all products...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_attempts = 0
    max_scroll_attempts = 10
    
    while scroll_attempts < max_scroll_attempts:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        new_height = driver.execute_script("return document.body.scrollHeight")
        print(f"   → Scroll attempt {scroll_attempts + 1}: height {last_height} -> {new_height}")
        
        if new_height == last_height:
            # Try scrolling a bit more to trigger lazy loading
            driver.execute_script("window.scrollBy(0, 500);")
            time.sleep(2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
        
        last_height = new_height
        scroll_attempts += 1
    
    # Final scroll to top to ensure all elements are loaded
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(2)
    
    # Find all product links
    products = []
    try:
        # Try multiple selectors for product links
        selectors = [
            'a[href*="/product/"]',
            '.product-item a',
            '.product-card a',
            '.woocommerce-loop-product__link',
            '.woocommerce-loop-product__title a',
            '.product a',
            '.woocommerce ul.products li a',
            'ul.products li a'
        ]
        
        all_links = set()
        for selector in selectors:
            try:
                links = driver.find_elements(By.CSS_SELECTOR, selector)
                print(f"   → Selector '{selector}' found {len(links)} links")
                for link in links:
                    href = link.get_attribute("href")
                    if href and "/product/" in href and "/product-category/" not in href:
                        title = link.text.strip()
                        if title and not title.startswith('%') and len(title) > 10:
                            all_links.add((href, title))
            except Exception as e:
                print(f"   → Selector {selector} failed: {e}")
                continue
        
        # Convert set to list and remove duplicates
        for href, title in all_links:
            products.append({
                'url': href,
                'title': title
            })
        
        # Remove duplicates by URL
        seen_urls = set()
        unique_products = []
        for product in products:
            if product['url'] not in seen_urls:
                seen_urls.add(product['url'])
                unique_products.append(product)
        
        print(f"   → Found {len(unique_products)} unique products")
        return unique_products
        
    except Exception as e:
        print(f"   × Error getting product links: {e}")
        return []


def extract_product_details(driver, product_url: str) -> Dict[str, Any]:
    """Extract detailed information from a product page."""
    print(f"   → Scraping: {product_url}")
    
    try:
        driver.get(product_url)
        time.sleep(3)
        
        # Scroll to load all content
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.5);")
        time.sleep(3)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        
        
        # Extract product information
        result = {
            'url': product_url,
            'title': '',
            'old_price': '',
            'new_price': '',
            'description': '',
            'sku': '',
            'images': [],
            'status': 'SUCCESS'
        }
        
        # Extract title
        try:
            title_selectors = [
                'h1.product-title',
                '.product_title',
                'h1',
                '.product-name'
            ]
            for selector in title_selectors:
                try:
                    title_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if title_el.text.strip():
                        result['title'] = title_el.text.strip()
                        break
                except:
                    continue
        except Exception as e:
            print(f"     × Error extracting title: {e}")
        
        # Extract old and new prices
        try:
            print(f"     → Debug: Looking for prices...")
            
            # Look for the specific price container
            price_container = None
            try:
                price_container = driver.find_element(By.CSS_SELECTOR, '.price.pewc-main-price')
                print(f"     ✓ Found price container: .price.pewc-main-price")
            except:
                try:
                    price_container = driver.find_element(By.CSS_SELECTOR, '.price')
                    print(f"     ✓ Found price container: .price")
                except:
                    print(f"     × No price container found")
            
            old_price = ""
            new_price = ""
            
            if price_container:
                # First, try to find del and ins tags specifically
                try:
                    del_elements = price_container.find_elements(By.CSS_SELECTOR, 'del .woocommerce-Price-amount, del .amount, del bdi, del')
                    for element in del_elements:
                        text = element.text.strip()
                        if text and not old_price:
                            old_price = text
                            print(f"     ✓ Found old price in del tag: {old_price}")
                            break
                except:
                    pass
                
                try:
                    ins_elements = price_container.find_elements(By.CSS_SELECTOR, 'ins .woocommerce-Price-amount, ins .amount, ins bdi, ins')
                    for element in ins_elements:
                        text = element.text.strip()
                        if text and not new_price:
                            new_price = text
                            print(f"     ✓ Found new price in ins tag: {new_price}")
                            break
                except:
                    pass
                
                # If we still don't have both prices, look for all price elements
                if not old_price or not new_price:
                    all_price_elements = price_container.find_elements(By.CSS_SELECTOR, '.woocommerce-Price-amount, .amount, bdi, span')
                    print(f"     → Found {len(all_price_elements)} price elements in container")
                    
                    for element in all_price_elements:
                        text = element.text.strip()
                        if not text:
                            continue
                        
                        # Check if this element is inside a del tag or has strikethrough styling
                        try:
                            parent = element.find_element(By.XPATH, "..")
                            is_strikethrough = (
                                parent.tag_name == 'del' or 
                                'text-decoration' in (parent.get_attribute('style') or '') or 
                                'strikethrough' in (parent.get_attribute('style') or '') or
                                'line-through' in (parent.get_attribute('style') or '')
                            )
                            
                            if is_strikethrough and not old_price:
                                old_price = text
                                print(f"     ✓ Found old price (strikethrough): {old_price}")
                            elif not is_strikethrough and not new_price:
                                new_price = text
                                print(f"     ✓ Found new price: {new_price}")
                        except:
                            # If we can't check the parent, and we don't have a new price yet, use this
                            if not new_price:
                                new_price = text
                                print(f"     ✓ Found new price (fallback): {new_price}")
                
                # If we still don't have both prices, try to get all text and parse it
                if not old_price or not new_price:
                    container_text = price_container.text.strip()
                    print(f"     → Container text: {container_text}")
                    
                    # Look for patterns like "999 JOD 699 JOD" or similar
                    import re
                    price_pattern = r'(\d+(?:,\d+)*)\s*JOD'
                    prices = re.findall(price_pattern, container_text)
                    print(f"     → Found prices in text: {prices}")
                    
                    if len(prices) >= 2:
                        # Convert prices to numbers for comparison
                        price_values = []
                        for price_str in prices:
                            try:
                                # Remove commas and convert to int
                                value = int(price_str.replace(',', ''))
                                price_values.append((value, price_str))
                            except:
                                continue
                        
                        if len(price_values) >= 2:
                            # Sort by value (descending) - higher price is usually the old price
                            price_values.sort(key=lambda x: x[0], reverse=True)
                            
                            if not old_price:
                                old_price = f"{price_values[0][1]} JOD"
                                print(f"     ✓ Extracted old price from text (higher value): {old_price}")
                            if not new_price:
                                new_price = f"{price_values[1][1]} JOD"
                                print(f"     ✓ Extracted new price from text (lower value): {new_price}")
                    elif len(prices) == 1:
                        if not new_price:
                            new_price = f"{prices[0]} JOD"
                            print(f"     ✓ Extracted single price from text: {new_price}")
            else:
                print(f"     × No price container found, trying fallback selectors")
                # Fallback to original method
                try:
                    price_el = driver.find_element(By.CSS_SELECTOR, '.woocommerce-Price-amount, .amount')
                    new_price = price_el.text.strip()
                    print(f"     ✓ Found price (fallback): {new_price}")
                except:
                    pass
            
            if not old_price:
                print(f"     × No old price found")
            if not new_price:
                print(f"     × No new price found")
            
            result['old_price'] = old_price
            result['new_price'] = new_price
            result['price'] = f"Old: {old_price} | New: {new_price}" if old_price and new_price else (new_price or old_price)
            
        except Exception as e:
            print(f"     × Error extracting prices: {e}")
            result['old_price'] = ""
            result['new_price'] = ""
            result['price'] = ""
        
        
        # Extract SKU
        try:
            sku_el = driver.find_element(By.CSS_SELECTOR, '.sku')
            if sku_el.text.strip():
                result['sku'] = sku_el.text.strip()
                print(f"     ✓ Found SKU: {sku_el.text.strip()}")
            else:
                print(f"     × No content in SKU")
        except Exception as e:
            print(f"     × Error extracting SKU: {e}")
            result['sku'] = ""
        
        # Extract description (this will be the main content)
        try:
            desc_el = driver.find_element(By.CSS_SELECTOR, '.woocommerce-product-details__short-description')
            if desc_el.text.strip():
                result['description'] = desc_el.text.strip()
                print(f"     ✓ Found description: {len(desc_el.text.strip())} chars")
            else:
                print(f"     × No description content found")
        except Exception as e:
            print(f"     × Error extracting description: {e}")
            result['description'] = ""
        
        # Extract images
        try:
            image_selectors = [
                'img[src*="product"]',
                '.product-images img',
                '.woocommerce-product-gallery img',
                '.product-gallery img',
                '.product-photos img',
                '.gallery img'
            ]
            
            image_urls = set()
            for selector in image_selectors:
                try:
                    imgs = driver.find_elements(By.CSS_SELECTOR, selector)
                    for img in imgs:
                        src = img.get_attribute("src")
                        if src and not src.startswith("data:") and "placeholder" not in src.lower():
                            # Clean the URL to get original size
                            clean_url = clean_image_url(src)
                            image_urls.add(clean_url)
                except:
                    continue
            
            result['images'] = list(image_urls)
            
        except Exception as e:
            print(f"      Error extracting images: {e}")
        
        # Validate results
        if not result['title'] and not result['description'] and not result['images']:
            result['status'] = 'FAILED'
            result['error'] = 'No product data found'
        elif not result['title'] or not result['images']:
            result['status'] = 'PARTIAL'
        
        print(f"     Title: {result['title'][:50]}...")
        print(f"     Old Price: {result['old_price']}")
        print(f"     New Price: {result['new_price']}")
        print(f"     Description: {len(result['description'])} chars")
        print(f"     SKU: {result['sku']}")
        print(f"     Images: {len(result['images'])}")
        
        return result
        
    except Exception as e:
        print(f"   × Error scraping product: {e}")
        return {
            'url': product_url,
            'title': '',
            'old_price': '',
            'new_price': '',
            'description': '',
            'sku': '',
            'images': [],
            'status': 'FAILED',
            'error': str(e)
        }


def scrape_all_products(base_url: str, output_file: str, headful: bool = False, delay: float = 2.0) -> None:
    """Scrape all products from the New Vision refrigerators page."""

    # Resume logic: load existing output and skip already-done URLs
    existing_df = None
    already_done_urls: set = set()
    if os.path.exists(output_file):
        try:
            existing_df = pd.read_excel(output_file)
            if "url" in existing_df.columns and "status" in existing_df.columns:
                already_done_urls = set(
                    existing_df.loc[existing_df["status"] == "SUCCESS", "url"].astype(str).str.strip()
                )
            print(f"Resuming: {len(already_done_urls)} URLs already done, will skip them")
        except Exception as e:
            print(f"Warning: could not read existing output ({e}), starting fresh")
            existing_df = None

    driver = build_driver(headful)

    try:
        print(f"Starting New Vision LG Scraper")
        print(f"   Base URL: {base_url}")
        print(f"   Output: {output_file}")
        print(f"   Delay: {delay}s between requests")
        print()

        # Get all product links
        products = get_product_links(driver, base_url)

        if not products:
            print("No products found!")
            return

        print(f"\nFound {len(products)} products to scrape")
        print("=" * 60)

        # Scrape each product
        all_results = []
        for i, product in enumerate(products, 1):
            if product['url'] in already_done_urls:
                print(f"\n[{i}/{len(products)}] Skipping already-done: {product['url'][:60]}")
                continue
            print(f"\n[{i}/{len(products)}] {product['title'][:50]}...")

            result = extract_product_details(driver, product['url'])
            all_results.append(result)

            # Add delay between requests
            if i < len(products):
                time.sleep(delay)

        # Create DataFrame and save
        new_df = pd.DataFrame(all_results)

        # Convert images list to semicolon-separated string
        if len(new_df) > 0:
            new_df['images'] = new_df['images'].apply(lambda x: ';'.join(x) if isinstance(x, list) else '')

            # Reorder columns
            columns = ['url', 'title', 'old_price', 'new_price', 'description', 'sku', 'images', 'status']
            if 'error' in new_df.columns:
                columns.append('error')
            existing_cols = [c for c in columns if c in new_df.columns]
            new_df = new_df[existing_cols]

        if existing_df is not None and len(new_df) > 0:
            final_df = pd.concat([existing_df, new_df], ignore_index=True)
        elif existing_df is not None:
            final_df = existing_df
        else:
            final_df = new_df

        # Save to Excel
        final_df.to_excel(output_file, index=False)
        
        # Print summary
        print("\n" + "=" * 60)
        print(" SCRAPING SUMMARY")
        print("=" * 60)
        print(f"Total products: {len(all_results)}")
        print(f"Successful: {len([r for r in all_results if r['status'] == 'SUCCESS'])}")
        print(f"Partial: {len([r for r in all_results if r['status'] == 'PARTIAL'])}")
        print(f"Failed: {len([r for r in all_results if r['status'] == 'FAILED'])}")
        print(f"Total images: {sum(len(r['images']) for r in all_results if isinstance(r['images'], list))}")
        print(f"Saved to: {output_file}")
        
    finally:
        driver.quit()


def main():
    """Main function for CLI usage."""
    parser = argparse.ArgumentParser(description="New Vision LG Refrigerators Scraper")
    parser.add_argument("--url", default="https://newvision.jo/product-category/refrigerators-ar/", 
                       help="URL of the refrigerators category page")
    parser.add_argument("--output", "-o", default="newvision_lg_refrigerators.xlsx", 
                       help="Output Excel file path")
    parser.add_argument("--headful", action="store_true", 
                       help="Run browser in headful mode (visible)")
    parser.add_argument("--delay", type=float, default=2.0, 
                       help="Delay between requests in seconds")
    
    args = parser.parse_args()
    
    # Create output directory if it doesn't exist
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    scrape_all_products(args.url, args.output, args.headful, args.delay)


if __name__ == "__main__":
    main()

