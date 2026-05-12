#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Benson's Pet Center Scraper
Scrapes product data from https://shop.bensonspet.com by searching SKUs
"""

import argparse
import os
import re
import time
from typing import List, Optional, Tuple, Dict, Any
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys


def build_driver(headless: bool = True) -> webdriver.Chrome:
    """Build Chrome driver with proper options."""
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver


def find_sku_column(df: pd.DataFrame, user_col: Optional[str]) -> str:
    """Find the SKU column in the DataFrame."""
    if user_col:
        if user_col in df.columns:
            return user_col
        raise ValueError(f"--sku-col '{user_col}' not found. Available: {list(df.columns)}")
    
    # Look for common SKU column names
    for c in df.columns:
        if "sku" in str(c).lower():
            return c
    
    # Look for barcode/barcode number columns
    for candidate in ["رقم الباركود", "Barcode", "Barcode Number", "Item", "Item No", "ItemNo", "Code", "Product Code", "Variant SKU"]:
        if candidate in df.columns:
            return candidate
    
    return df.columns[0]


def search_for_sku(driver, sku: str, wait: WebDriverWait) -> bool:
    """Search for SKU on Benson's Pet Center website."""
    base_url = "https://shop.bensonspet.com"
    
    # Add leading zero if SKU doesn't start with 0 and is 11 digits
    search_sku = sku
    if len(sku) == 11 and not sku.startswith('0'):
        search_sku = '0' + sku
        print(f"   → Added leading zero: {sku} -> {search_sku}")
    
    # Go to the search page with the SKU
    search_url = f"{base_url}/search?q={search_sku}&options%5Bprefix%5D=last"
    print(f"   → Searching: {search_url}")
    
    try:
        driver.get(search_url)
        
        # Wait for search results to load
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".search-results")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-item")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-card")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "main")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".search")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1")),  # "Search results" or "No results found"
                EC.presence_of_element_located((By.CSS_SELECTOR, "h2")),  # "1 result" text
                EC.presence_of_element_located((By.CSS_SELECTOR, "h3"))   # Product titles
            )
        )
        print(f"   → Search results loaded")
        return True
        
    except TimeoutException:
        print(f"   → Timeout waiting for search results")
        return False


def extract_product_data(driver) -> Dict[str, Any]:
    """Extract product data from the current page."""
    result = {
        "ProductURL": driver.current_url,
        "Description": "",
        "Images": [],
        "Found": False
    }
    
    try:
        
        # Extract description
        desc_selectors = [
            ".product-description",
            ".product__description",
            ".product-details",
            ".product-info",
            "[data-testid='product-description']",
            ".description",
            ".product-content",
            ".product-summary"
        ]
        
        for selector in desc_selectors:
            try:
                desc_el = driver.find_element(By.CSS_SELECTOR, selector)
                desc_text = desc_el.text.strip()
                if desc_text and len(desc_text) > 20:
                    result["Description"] = desc_text
                    print(f"   → Found description: {len(desc_text)} chars")
                    break
            except NoSuchElementException:
                continue
        
        # Extract images
        img_selectors = [
            ".product-images img",
            ".product__media img",
            ".product-gallery img",
            ".product-photos img",
            "img[alt*='product']",
            "img[src*='product']",
            ".product-item img"
        ]
        
        images = []
        for selector in img_selectors:
            try:
                img_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for img in img_elements:
                    src = img.get_attribute("src")
                    if src and not src.startswith("data:") and "placeholder" not in src.lower():
                        # Convert relative URLs to absolute
                        if src.startswith("//"):
                            src = "https:" + src
                        elif src.startswith("/"):
                            src = "https://shop.bensonspet.com" + src
                        images.append(src)
                        print(f"   → Found image: {src[:80]}...")
            except Exception:
                continue
        
        # Remove duplicates while preserving order
        seen = set()
        result["Images"] = [img for img in images if not (img in seen or seen.add(img))]
        
        # Check if product was found
        result["Found"] = bool(result["Description"] or result["Images"])
        
        return result
        
    except Exception as e:
        print(f"   → Error extracting product data: {e}")
        return result


def click_first_product(driver, wait: WebDriverWait) -> bool:
    """Click on the first product in search results."""
    product_selectors = [
        # Benson's Pet Center specific selectors based on the search results structure
        "a[href*='/products/']",
        "a[href*='/product/']",
        ".product-item a",
        ".product-card a", 
        ".product a",
        "article a",
        ".search-result a",
        # Additional selectors for the specific structure we see
        "h3 a",  # The product title links
        "a[href*='kong']",  # Specific to the Kong product we see
        "a[href*='dog-toy']",
        "a[href*='snuzzles']"
    ]
    
    for selector in product_selectors:
        try:
            product_links = driver.find_elements(By.CSS_SELECTOR, selector)
            if product_links:
                # Filter out non-product links
                valid_links = []
                for link in product_links:
                    href = link.get_attribute("href") or ""
                    if href and ("/product" in href or "/products/" in href) and not any(x in href for x in ["/account", "/login", "/cart", "/search"]):
                        valid_links.append(link)
                
                if valid_links:
                    first_link = valid_links[0]
                    href = first_link.get_attribute("href")
                    print(f"   → Found product link: {href}")
                    
                    # Try JavaScript click first
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", first_link)
                        time.sleep(0.5)
                        driver.execute_script("arguments[0].click();", first_link)
                        print(f"   → JavaScript click executed")
                        
                        # Wait for navigation
                        try:
                            wait.until(EC.url_changes(driver.current_url))
                            print(f"   → Navigated to product page: {driver.current_url}")
                            return True
                        except TimeoutException:
                            print(f"   → No navigation after JS click, trying direct navigation")
                            driver.get(href)
                            return True
                    except Exception as e:
                        print(f"   → JavaScript click failed: {e}, trying direct navigation")
                        driver.get(href)
                        return True
                        
        except Exception as e:
            print(f"   → Error with selector {selector}: {e}")
            continue
    
    print(f"   → No product links found to click")
    return False


def scrape_sku(driver, sku: str, wait: WebDriverWait, pause: float) -> Dict[str, Any]:
    """Scrape data for a single SKU."""
    print(f"   → Scraping SKU: {sku}")
    
    # Search for the SKU
    if not search_for_sku(driver, sku, wait):
        return {
            "SKU": sku,
            "Found": False,
            "ProductURL": "",
            "Description": "",
            "Images": [],
            "Note": "Search failed or no results"
        }
    
    # Check if "No results found" message is present
    try:
        no_results = driver.find_element(By.XPATH, "//*[contains(text(), 'No results found')]")
        if no_results:
            return {
                "SKU": sku,
                "Found": False,
                "ProductURL": driver.current_url,
                "Description": "",
                "Images": [],
                "Note": "No results found for this SKU"
            }
    except NoSuchElementException:
        pass  # No "No results found" message, continue with normal flow
    
    time.sleep(pause)
    
    # Try to click first product if we're on search results
    current_url = driver.current_url
    if "/search" in current_url:
        if not click_first_product(driver, wait):
            return {
                "SKU": sku,
                "Found": False,
                "ProductURL": current_url,
                "Description": "",
                "Images": [],
                "Note": "No product found in search results"
            }
        time.sleep(pause)
    
    # Extract product data
    result = extract_product_data(driver)
    result["SKU"] = sku
    
    return result


def main():
    """Main function for CLI usage."""
    ap = argparse.ArgumentParser(description="Benson's Pet Center scraper by SKU")
    ap.add_argument("--in", dest="input_file", required=True, help="Input Excel file path")
    ap.add_argument("--out", dest="output_file", required=True, help="Output Excel file path")
    ap.add_argument("--sku-col", dest="sku_col", default=None, help="SKU column name (auto-detect if omitted)")
    ap.add_argument("--pause", type=float, default=2.0, help="Pause between requests (seconds)")
    ap.add_argument("--headless", action="store_true", help="Run in headless mode")
    ap.add_argument("--max-img", type=int, default=10, help="Maximum images to collect per product")
    args = ap.parse_args()

    # Create output directory if needed
    output_dir = os.path.dirname(args.output_file)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Read input Excel file
    try:
        df = pd.read_excel(args.input_file)
        print(f"📊 Loaded {len(df)} rows from {args.input_file}")
    except Exception as e:
        print(f"❌ Error reading input file: {e}")
        return

    # Find SKU column
    try:
        sku_col = find_sku_column(df, args.sku_col)
        print(f"📋 Using SKU column: '{sku_col}'")
    except Exception as e:
        print(f"❌ Error finding SKU column: {e}")
        return

    # Build driver
    driver = build_driver(headless=args.headless)
    wait = WebDriverWait(driver, 20)

    try:
        print(f"🔍 Benson's Pet Center Scraper")
        print(f"Input: {args.input_file}")
        print(f"Output: {args.output_file}")
        print(f"Pause: {args.pause}s")
        print()

        results = []
        total = len(df)
        
        for idx, row in df.iterrows():
            sku = str(row[sku_col]).strip()
            if not sku or sku.lower() in {"nan", "none", ""}:
                print(f"[{idx+1}/{total}] SKU='{sku}' -> Empty SKU, skipped.")
                continue
            
            print(f"[{idx+1}/{total}] Processing SKU: {sku}")
            
            try:
                result = scrape_sku(driver, sku, wait, args.pause)
                results.append(result)
                
                status = "FOUND" if result.get("Found") else f"MISS ({result.get('Note', '')})"
                img_count = len(result.get("Images", []))
                print(f"    → {status}; Images: {img_count}; URL: {result.get('ProductURL', '')}")
                
            except Exception as e:
                print(f"    → ERROR: {e}")
                results.append({
                    "SKU": sku,
                    "Found": False,
                    "ProductURL": "",
                    "Title": "",
                    "Price": "",
                    "Description": "",
                    "Images": [],
                    "Brand": "",
                    "Categories": "",
                    "Availability": "",
                    "Note": f"Error: {e}"
                })
            
            time.sleep(args.pause)

        # Create results DataFrame
        results_df = pd.DataFrame(results)
        
        # Convert Images list to semicolon-separated string for single IMGS column
        results_df["IMGS"] = results_df["Images"].apply(
            lambda lst: ";".join(lst) if isinstance(lst, list) and lst else ""
        )
        
        # Merge with original data
        df["_SKU_SCRAPE_KEY"] = df[sku_col].astype(str).str.strip()
        results_df["_SKU_SCRAPE_KEY"] = results_df["SKU"].astype(str).str.strip()
        
        # Keep only the 2 new columns we want to add
        new_cols = ["_SKU_SCRAPE_KEY", "Description", "IMGS"]
        results_df = results_df[new_cols]
        
        # Merge with original DataFrame - this will keep ALL original columns and add only the 2 new ones
        merged_df = df.merge(results_df, on="_SKU_SCRAPE_KEY", how="left").drop(columns=["_SKU_SCRAPE_KEY"])
        
        # Save results
        merged_df.to_excel(args.output_file, index=False)
        
        # Print summary
        found_count = sum(1 for result in results if result.get("Found", False))
        print(f"\n📊 Scraping Complete!")
        print(f"   Total SKUs: {total}")
        print(f"   Found: {found_count}")
        print(f"   Success Rate: {found_count/total*100:.1f}%")
        print(f"   Results saved to: {args.output_file}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
