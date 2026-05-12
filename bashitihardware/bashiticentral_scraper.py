#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bashiti Hardware Scraper
Scrapes product data from https://bashitihardware.com via search:
  https://bashitihardware.com/ar/?s=SKU&post_type=product
Uses WooCommerce search results; handles no-results and error pages.
"""

import argparse
import os
import re
import time
import json
import sys
from urllib.parse import quote_plus
from typing import List, Dict, Any, Optional
from pathlib import Path
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup


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


def _get_search_result_product_links(driver):
    """
    Find all product page links on a WooCommerce search results page.
    Uses broad selectors so we don't miss results when the theme differs.
    """
    selectors = [
        "ul.products li.product a.woocommerce-loop-product__link",
        "ul.products li.product a[href*='/product/']",
        "ul.products li.product a",
        ".products li.product a",
        ".products .product a",
        "li.product a[href*='/product/']",
        ".product a[href*='/product/']",
        # Fallback: any product link in main content (avoid header/footer)
        "main a[href*='/product/']",
        "#main a[href*='/product/']",
        ".content a[href*='/product/']",
        "#content a[href*='/product/']",
        "a[href*='/product/'][href*='bashitihardware']",
    ]
    seen = set()
    for selector in selectors:
        for elem in driver.find_elements(By.CSS_SELECTOR, selector):
            href = elem.get_attribute("href")
            if href and "/product/" in href and "bashitihardware" in href and href not in seen:
                seen.add(href)
                yield href


def is_no_search_results(driver) -> bool:
    """
    Check if the current page is a WooCommerce "no products" search result.
    We prioritize finding product links first; only if none found do we say "no results".
    """
    try:
        # First: if we find any product link, we have results (don't trust "no products" text elsewhere)
        first_link = next(_get_search_result_product_links(driver), None)
        if first_link is not None:
            return False
        # No product links found - confirm with "no products" message in page
        page_text = driver.find_element(By.TAG_NAME, "body").text
        if "لا توجد منتجات" in page_text or "no products" in page_text.lower():
            return True
        # Still no links but no explicit message - treat as no results
        return True
    except Exception:
        return False


def get_product_url_from_search(driver, search_url: str, wait: WebDriverWait, pause: float) -> Optional[str]:
    """
    Open the search URL and return the first product page URL, or None if no results.
    """
    try:
        driver.get(search_url)
        time.sleep(pause)
        # Wait for results area or any product link (some themes load via JS)
        try:
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    ".products, .woocommerce-info, .woocommerce-no-products-found, "
                    "li.product, a[href*='/product/']"
                ))
            )
        except TimeoutException:
            pass
        # Extra moment for JS-rendered product links
        time.sleep(1.0)
        if is_no_search_results(driver):
            return None
        # Use same broad logic to get first product URL
        return next(_get_search_result_product_links(driver), None)
    except Exception:
        return None


def is_laravel_error_page(driver) -> bool:
    """
    Check if the current page is a Laravel error/debug page.
    Based on error page analysis: detects ErrorException with item_name property.
    """
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text
        
        # Check for ErrorException
        if "ErrorException" in page_text:
            return True
        
        # Check for specific error message pattern
        if "Attempt to read property" in page_text and "item_name" in page_text and "on null" in page_text:
            return True
        
        # Check for Laravel debug UI elements
        if any(tab in page_text for tab in ["STACK", "CONTEXT", "DEBUG", "FLARE"]):
            return True
        
        # Check page title
        page_title = driver.title.lower()
        if any(keyword in page_title for keyword in ["laravel", "exception", "error", "whoops"]):
            return True
        
        return False
    except Exception:
        return False


def extract_laravel_debug_info(driver) -> Dict[str, Any]:
    """
    Extract useful information from Laravel error pages.
    This helps understand the application structure and database schema.
    """
    debug_info = {
        "error_type": "ErrorException",
        "error_message": "Attempt to read property 'item_name' on null",
        "controller": "ShopController@single_product",
        "route": "single_product",
        "tables": [],
        "queries": []
    }
    
    try:
        page_source = driver.page_source
        
        # Extract table names from SQL queries
        tables = re.findall(r"from\s+`(\w+)`", page_source, re.IGNORECASE)
        debug_info["tables"] = list(set(tables))
        
        # Extract SQL queries (simplified)
        sql_matches = re.findall(r"select.*?from\s+`(\w+)`", page_source, re.IGNORECASE | re.DOTALL)
        if sql_matches:
            debug_info["queries"] = sql_matches[:5]
        
    except Exception as e:
        debug_info["extraction_error"] = str(e)
    
    return debug_info


def extract_title(driver) -> str:
    """Extract product title from product page. Title is in .product-title with trailing space."""
    try:
        # First try to find element with class="product-title " (with trailing space) - this is the title
        try:
            # Find all elements with product-title class
            elems = driver.find_elements(By.XPATH, "//*[contains(@class, 'product-title')]")
            for elem in elems:
                class_attr = elem.get_attribute("class") or ""
                text = elem.text.strip()
                
                # Check if class has trailing space "product-title " - this is the title
                if "product-title " in class_attr or class_attr.endswith("product-title "):
                    if text:
                        return text
        except Exception:
            pass
        
        # Fallback selectors
        title_selectors = [
            "h1.product-title",
            "h1",
            ".product-name",
            ".entry-title",
            "h1.page-title"
        ]
        
        for selector in title_selectors:
            try:
                title_elem = driver.find_element(By.CSS_SELECTOR, selector)
                title_text = title_elem.text.strip()
                if title_text:
                    return title_text
            except NoSuchElementException:
                continue
        
        return ""
    except Exception as e:
        return ""


def extract_price(driver) -> str:
    """Extract product price from product page."""
    try:
        price_selectors = [
            ".new-price",  # Exact class from user
            ".price",
            ".product-price",
            ".price .amount",
            "span.price",
            "span.new-price",
            "bdi",
            ".woocommerce-Price-amount",
            ".product-summary .price"
        ]
        
        for selector in price_selectors:
            try:
                price_elems = driver.find_elements(By.CSS_SELECTOR, selector)
                for price_elem in price_elems:
                    price_text = price_elem.text.strip()
                    if price_text and any(ch.isdigit() for ch in price_text):
                        return price_text
            except Exception:
                continue
        
        return ""
    except Exception as e:
        return ""


def extract_description(driver) -> str:
    """Extract product description from product page. Description is NOT in product-title elements."""
    try:
        # Description is NOT in product-title elements
        # Try common description selectors
        desc_selectors = [
            ".product-description",
            ".description",
            ".product-content",
            ".product-details",
            ".product-info",
            ".product-summary",
            ".woocommerce-product-details__short-description",
            ".entry-content",
            ".product-summary .description",
            "#tab-description",
            ".woocommerce-Tabs-panel--description",
            ".product-text",
            "[class*='description']",
            "[class*='content']",
            "p.description",
            "div.description"
        ]
        
        title_text = extract_title(driver)  # Get title to compare and exclude
        
        for selector in desc_selectors:
            try:
                desc_elems = driver.find_elements(By.CSS_SELECTOR, selector)
                for desc_elem in desc_elems:
                    desc_text = desc_elem.text.strip()
                    # Skip if it's the same as title or too short
                    if desc_text and desc_text != title_text and len(desc_text) > 20:
                        return desc_text
            except NoSuchElementException:
                continue
        
        # Try to find description by looking for paragraphs or divs with substantial text
        try:
            # Look for paragraphs that might contain description
            paragraphs = driver.find_elements(By.TAG_NAME, "p")
            for p in paragraphs:
                text = p.text.strip()
                # Skip if it's title or too short, but look for longer text
                if text and text != title_text and len(text) > 50:
                    # Check if it's not a price or SKU
                    if not re.match(r'^[\d\s.,]+$', text):  # Not just numbers
                        return text
        except Exception:
            pass
        
        return ""
    except Exception as e:
        return ""


def extract_images(driver, max_images: int = 10) -> List[str]:
    """Extract product images from product page. Prioritizes easyzoom images."""
    images = []
    
    try:
        # Priority 1: Look for images in easyzoom containers (main product images)
        # Easyzoom is often implemented as <a class="easyzoom"> with href pointing to full-size image
        easyzoom_selectors = [
            "a.easyzoom.easyzoom--overlay.is-ready",
            "a.easyzoom",
            ".easyzoom.easyzoom--overlay.is-ready",
            ".easyzoom",
            "[class*='easyzoom']"
        ]
        
        for selector in easyzoom_selectors:
            try:
                easyzoom_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in easyzoom_elements:
                    # First try href from anchor (full-size image)
                    src = elem.get_attribute("href")
                    
                    # If no href, look for img inside the element
                    if not src:
                        try:
                            img = elem.find_element(By.TAG_NAME, "img")
                            src = (img.get_attribute("src") or 
                                   img.get_attribute("data-src") or 
                                   img.get_attribute("data-large_image") or
                                   img.get_attribute("data-zoom_image"))
                        except NoSuchElementException:
                            continue
                    
                    if src:
                        # Handle relative URLs
                        if src.startswith("//"):
                            src = "https:" + src
                        elif src.startswith("/"):
                            src = "https://bashitihardware.com" + src
                        
                        if src.startswith("http") and "placeholder" not in src.lower():
                            if src not in images:
                                images.append(src)
                                
                                if len(images) >= max_images:
                                    break
                
                if images:
                    break
            except Exception:
                continue
        
        # Priority 2: Fallback to other product image selectors if easyzoom not found
        if not images:
            img_selectors = [
                ".product-gallery img",
                ".product-images img",
                ".woocommerce-product-gallery__image img",
                "img.product-image",
                ".product-thumbnails img",
                ".wp-post-image",
                ".product-single img"
            ]
            
            for selector in img_selectors:
                try:
                    img_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for img in img_elements:
                        src = (img.get_attribute("src") or 
                               img.get_attribute("data-src") or 
                               img.get_attribute("data-large_image") or
                               img.get_attribute("data-zoom_image"))
                        
                        if src:
                            # Handle relative URLs
                            if src.startswith("//"):
                                src = "https:" + src
                            elif src.startswith("/"):
                                src = "https://bashitihardware.com" + src
                            
                            if src.startswith("http") and "placeholder" not in src.lower():
                                if src not in images:
                                    images.append(src)
                                    
                                    if len(images) >= max_images:
                                        break
                    
                    if images:
                        break
                except Exception:
                    continue
        
        # Priority 3: Use BeautifulSoup to parse easyzoom elements from HTML
        if not images:
            try:
                soup = BeautifulSoup(driver.page_source, "html.parser")
                
                # First try easyzoom elements (often anchor tags)
                easyzoom_elements = soup.find_all(class_=re.compile("easyzoom"))
                for elem in easyzoom_elements:
                    # First try href from anchor (full-size image URL)
                    src = elem.get("href")
                    
                    # If no href, look for img tag inside easyzoom
                    if not src:
                        img = elem.find("img")
                        if img:
                            src = (img.get("src") or 
                                   img.get("data-src") or 
                                   img.get("data-large_image") or
                                   img.get("data-zoom_image"))
                    
                    if src:
                        if src.startswith("//"):
                            src = "https:" + src
                        elif src.startswith("/"):
                            src = "https://bashitihardware.com" + src
                        
                        if src.startswith("http") and "placeholder" not in src.lower():
                            if src not in images and len(images) < max_images:
                                images.append(src)
                
                # Final fallback: all images
                if not images:
                    for img in soup.find_all("img"):
                        src = (img.get("src") or 
                               img.get("data-src") or 
                               img.get("data-large_image"))
                        
                        if src:
                            if src.startswith("//"):
                                src = "https:" + src
                            elif src.startswith("/"):
                                src = "https://bashitihardware.com" + src
                            
                            if src.startswith("http") and "placeholder" not in src.lower():
                                if src not in images and len(images) < max_images:
                                    images.append(src)
            except Exception:
                pass
        
        return images[:max_images]
        
    except Exception as e:
        return []


def scrape_product(driver, product_url: str, wait: WebDriverWait, pause: float, max_images: int = 10) -> Dict[str, Any]:
    """
    Scrape data for a single product page (bashitihardware WooCommerce).
    Handles error pages when product doesn't exist.
    """
    result = {
        "ProductURL": product_url,
        "SKU": "",
        "Title": "",
        "Price": "",
        "Description": "",
        "Images": [],
        "Found": False,
        "Status": "PENDING"
    }
    
    try:
        print(f"   → Scraping: {product_url}")
        driver.get(product_url)
        time.sleep(pause)
        
        # Extract SKU - try from .product-title element (without trailing space) first, then from URL
        try:
            # SKU is in .product-title (without trailing space)
            # Find all product-title elements and get the one WITHOUT trailing space
            elems = driver.find_elements(By.XPATH, "//*[contains(@class, 'product-title')]")
            for elem in elems:
                class_attr = elem.get_attribute("class") or ""
                text = elem.text.strip()
                
                # Check if class does NOT have trailing space after "product-title"
                # This means: class contains "product-title" but NOT "product-title " (with space after)
                has_trailing_space = "product-title " in class_attr or class_attr.endswith("product-title ")
                
                if text and not has_trailing_space:
                    # This is the SKU element (without trailing space)
                    result["SKU"] = text
                    break
        except Exception:
            pass
        
        # Fallback to URL if SKU not found (WooCommerce: /product/... or from query)
        if not result["SKU"]:
            sku_match = re.search(r"/single-product/([^/]+)", product_url)
            if sku_match:
                result["SKU"] = sku_match.group(1)
            else:
                # WooCommerce product slug in URL
                slug_match = re.search(r"/product/([^/]+)/?", product_url)
                if slug_match:
                    result["SKU"] = slug_match.group(1)
        
        # Check if it's a Laravel error page
        if is_laravel_error_page(driver):
            print(f"   ⚠️  Product not found (Laravel error page)")
            result["Status"] = "NOT_FOUND"
            result["Note"] = "Product not found - SKU doesn't exist in database"
            
            # Extract debug info for learning (optional)
            debug_info = extract_laravel_debug_info(driver)
            result["DebugInfo"] = json.dumps(debug_info, indent=2)
            
            return result
        
        # Wait for product page to load
        try:
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR, 
                    "h1, .product-title, .product, .product-container, .product-main"
                ))
            )
        except TimeoutException:
            # Double-check if it's an error page
            if is_laravel_error_page(driver):
                result["Status"] = "NOT_FOUND"
                result["Note"] = "Product not found - Laravel error page"
                return result
            raise
        
        # Extract product data
        title = extract_title(driver)
        result["Title"] = title
        if title:
            print(f"   → Found title: {title[:60]}...")
        
        price = extract_price(driver)
        result["Price"] = price
        if price:
            print(f"   → Found price: {price}")
        
        description = extract_description(driver)
        result["Description"] = description
        if description:
            print(f"   → Found description: {len(description)} chars")
        
        images = extract_images(driver, max_images)
        result["Images"] = images
        if images:
            print(f"   → Found {len(images)} images")
        
        # Determine if product was found
        result["Found"] = bool(title or price or description or images)
        result["Status"] = "FOUND" if result["Found"] else "EMPTY"
        
        return result
        
    except TimeoutException:
        print(f"   → Timeout loading product page")
        result["Status"] = "TIMEOUT"
        result["Note"] = "Timeout loading page"
        
        # Check if it's an error page
        if is_laravel_error_page(driver):
            result["Status"] = "NOT_FOUND"
            result["Note"] = "Product not found - Laravel error page"
        
        return result
    except Exception as e:
        print(f"   → Error scraping product: {e}")
        result["Status"] = "ERROR"
        result["Note"] = f"Error: {e}"
        
        # Final check for error page
        if is_laravel_error_page(driver):
            result["Status"] = "NOT_FOUND"
            result["Note"] = "Product not found - Laravel error page"
        
        return result


def find_sku_or_url_column(df: pd.DataFrame, user_col: Optional[str] = None) -> Optional[str]:
    """
    Find SKU or URL column in DataFrame.
    Auto-detects common column names.
    """
    if user_col and user_col in df.columns:
        return user_col
    
    # Common SKU column names
    sku_patterns = [
        "sku", "item", "itemno", "item_no", "code", "product_code",
        "product_id", "id", "item_code", "itemnumber"
    ]
    
    # Common URL column names
    url_patterns = [
        "url", "link", "product_url", "producturl", "product_link",
        "href", "website", "source_url"
    ]
    
    # Check for exact matches first
    for col in df.columns:
        col_lower = str(col).lower()
        if user_col and col_lower == user_col.lower():
            return col
        if any(pattern in col_lower for pattern in sku_patterns + url_patterns):
            return col
    
    # Check if first column contains URLs
    if len(df.columns) > 0:
        first_col = df.columns[0]
        sample = df[first_col].dropna().astype(str).head(10)
        if sample.str.contains(r"^https?://", case=False, regex=True).any():
            return first_col

    # Fallback: if headers look meaningless (e.g. Unnamed) try a data-driven guess
    # by selecting the first column that has at least one plausible SKU-like value.
    def is_plausible(val: str) -> bool:
        # simple heuristic: alphanumeric with few symbols or starts with http
        val = val.strip()
        if not val or val.lower() in ("nan", "none"):
            return False
        if val.startswith("http"):
            return True
        # allow letters, digits, dashes, underscores, slashes
        if re.match(r"^[A-Za-z0-9_\-/]+$", val):
            return True
        return False

    for col in df.columns:
        sample_vals = df[col].dropna().astype(str).head(20)
        if any(is_plausible(str(v)) for v in sample_vals):
            # give user a heads up if we are guessing
            print(f"   → Auto-detected column '{col}' based on sample values")
            return col

    return None


def main():
    """Main function for CLI usage."""
    # Windows consoles often default to cp1252/cp1256 which can crash printing Arabic headers.
    # Force UTF-8 with replacement to keep the scraper running.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Bashiti Hardware Scraper - Scrapes products from bashitihardware.com via SKU search"
    )
    parser.add_argument(
        "--in", 
        dest="input_file", 
        # default refers to file located alongside this script; avoid backslashes to
        # prevent escape-sequence warnings and duplicate path segments when
        # resolved relative to script dir.
        default="injco_updated.xlsx",
        help="Input Excel file with SKUs or URLs (default: injco_updated.xlsx)"
    )
    parser.add_argument(
        "--out", 
        dest="output_file", 
        # Use a simple file name; the previous path introduced an escape-sequence
        # (\b) which erased characters in the printed output.
        default="injco_updated_scraped.xlsx",
        help="Output Excel file path (default: injco_updated_scraped.xlsx)"
    )
    parser.add_argument(
        "--sku-col", 
        dest="sku_col", 
        default=None,
        help="SKU or URL column name (auto-detect if omitted)"
    )
    parser.add_argument(
        "--url", 
        help="Single product URL to scrape"
    )
    parser.add_argument(
        "--sku", 
        help="Single SKU to scrape"
    )
    parser.add_argument(
        "--pause", 
        type=float, 
        default=2.0, 
        help="Pause between requests in seconds (default: 2.0)"
    )
    parser.add_argument(
        "--headless", 
        action="store_true", 
        help="Run in headless mode"
    )
    parser.add_argument(
        "--max-img", 
        type=int, 
        default=10, 
        help="Maximum images to collect per product (default: 10)"
    )
    parser.add_argument(
        "--sheet", 
        default=0,
        help="Excel sheet name or index (default: 0)"
    )
    
    args = parser.parse_args()
    
    base_url = "https://bashitihardware.com/ar"
    search_url_template = base_url + "/?s={}&post_type=product"
    
    # Create output directory if needed
    output_dir = Path(args.output_file).parent
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    
    # Build driver
    driver = build_driver(headless=args.headless)
    wait = WebDriverWait(driver, 20)
    
    try:
        print(f"🔍 Bashiti Hardware Scraper (search: bashitihardware.com/ar/?s=SKU&post_type=product)")
        print(f"Output: {args.output_file}")
        print(f"Pause: {args.pause}s")
        print()
        
        results = []
        
        def process_sku(sku: str):
            """Search by SKU, then scrape first product if found."""
            search_url = search_url_template.format(quote_plus(sku))
            print(f"   → Search: {search_url}")
            product_url = get_product_url_from_search(driver, search_url, wait, args.pause)
            if product_url is None:
                print(f"   ⚠️  Product not found (no search results)")
                return {
                    "ProductURL": search_url,
                    "SKU": sku,
                    "Title": "",
                    "Price": "",
                    "Description": "",
                    "Images": [],
                    "Found": False,
                    "Status": "NOT_FOUND",
                    "Note": "No products in search results"
                }
            return scrape_product(driver, product_url, wait, args.pause, args.max_img)
        
        # Handle single URL (direct product page)
        if args.url:
            product_url = args.url
            if not product_url.startswith("http"):
                product_url = search_url_template.format(quote_plus(args.url))
                product_url = get_product_url_from_search(driver, product_url, wait, args.pause)
                if product_url is None:
                    result = {
                        "ProductURL": search_url_template.format(quote_plus(args.url)),
                        "SKU": args.url,
                        "Title": "", "Price": "", "Description": "", "Images": [],
                        "Found": False, "Status": "NOT_FOUND", "Note": "No search results"
                    }
                else:
                    result = scrape_product(driver, product_url, wait, args.pause, args.max_img)
            else:
                result = scrape_product(driver, product_url, wait, args.pause, args.max_img)
            results.append(result)
        
        # Handle single SKU (search then scrape)
        elif args.sku:
            result = process_sku(args.sku)
            results.append(result)
        
        # Handle Excel file
        elif args.input_file:
            input_path = Path(args.input_file)
            if not input_path.is_absolute():
                # Try relative to script directory
                script_dir = Path(__file__).parent
                input_path = script_dir / args.input_file
            
            # If file doesn't exist, try alternative extensions
            if not input_path.exists():
                # Try .xls if .xlsx was provided, or .xlsx if .xls was provided
                alt_extensions = []
                if input_path.suffix == '.xlsx':
                    alt_extensions = ['.xls', '.XLS']
                elif input_path.suffix == '.xls':
                    alt_extensions = ['.xlsx', '.XLSX']
                else:
                    # No extension or other extension, try both
                    alt_extensions = ['.xls', '.xlsx', '.XLS', '.XLSX']
                
                found = False
                for ext in alt_extensions:
                    alt_path = input_path.with_suffix(ext)
                    if alt_path.exists():
                        input_path = alt_path
                        found = True
                        print(f"   → Found file: {input_path.name}")
                        break
                
                if not found:
                    print(f" Error: Input file not found: {input_path}")
                    print(f"   Tried: {input_path}")
                    for ext in alt_extensions:
                        alt_path = input_path.with_suffix(ext)
                        print(f"   Tried: {alt_path}")
                    return
            
            print(f" Loading Excel file: {input_path}")
            try:
                df = pd.read_excel(input_path, sheet_name=args.sheet)
                print(f" Loaded {len(df)} rows")
            except Exception as e:
                print(f" Error reading Excel file: {e}")
                return
            
            # Find SKU or URL column
            col_name = find_sku_or_url_column(df, args.sku_col)
            if not col_name:
                print(f" Error: Could not find SKU or URL column in Excel file")
                print(f"   Available columns: {list(df.columns)}")
                # show the first few rows to help user identify the right column
                preview = df.head(3).to_dict(orient="list")
                print(f"   Sample data (first 3 rows): {preview}")
                print(f"   Use --sku-col to specify column name or check the input file format")
                return
            
            print(f" Using column: '{col_name}'")

            # Scrape row-by-row (preserve original columns), caching by SKU/URL to avoid re-scraping duplicates.
            # This is important for sheets like injco_updated.xlsx which contain pricing columns per SKU.
            cache: Dict[str, Dict[str, Any]] = {}
            total_rows = len(df)

            # Ensure output columns exist (we keep any existing columns untouched)
            out_cols = ["ProductURL", "Title", "Price", "Description", "Images", "Found", "Status", "Note"]
            for c in out_cols:
                if c not in df.columns:
                    df[c] = ""

            for row_idx in range(total_rows):
                raw_value = df.at[row_idx, col_name]
                if pd.isna(raw_value):
                    continue
                value = str(raw_value).strip()
                if not value or value.lower() in ("nan", "none", ""):
                    continue

                print(f"\n[{row_idx+1}/{total_rows}] Processing: {value}")

                if value in cache:
                    result = cache[value]
                else:
                    if value.startswith("http"):
                        result = scrape_product(driver, value, wait, args.pause, args.max_img)
                    else:
                        result = process_sku(value)
                        if result.get("SKU") == "":
                            result["SKU"] = value
                    cache[value] = result

                results.append(result)

                # Write scraped data back into the same row
                df.at[row_idx, "ProductURL"] = result.get("ProductURL", "")
                df.at[row_idx, "Title"] = result.get("Title", "")
                df.at[row_idx, "Price"] = result.get("Price", "")
                df.at[row_idx, "Description"] = result.get("Description", "")
                imgs = result.get("Images", [])
                df.at[row_idx, "Images"] = ";".join(imgs) if isinstance(imgs, list) else (imgs or "")
                df.at[row_idx, "Found"] = bool(result.get("Found", False))
                df.at[row_idx, "Status"] = result.get("Status", "")
                df.at[row_idx, "Note"] = result.get("Note", "")

                # Status summary
                status = result.get("Status", "UNKNOWN")
                found = result.get("Found", False)
                title = (result.get("Title") or "")[:40]
                print(f"    → Status: {status} | Found: {found} | Title: {title}...")

                time.sleep(args.pause)
        
        else:
            print(" Error: Must provide --url, --sku, or --in")
            return
        
        # Create results DataFrame
        if not results:
            print(" No results to save")
            return
        
        # Save results.
        # If we processed an Excel sheet, df contains original columns + scraped columns.
        # Otherwise, save the standalone results dataframe.
        if args.input_file and "df" in locals():
            df.to_excel(args.output_file, index=False)
        else:
            results_df = pd.DataFrame(results)
            results_df["Images"] = results_df["Images"].apply(
                lambda lst: ";".join(lst) if isinstance(lst, list) and lst else ""
            )
            results_df.to_excel(args.output_file, index=False)
        
        # Print summary
        found_count = sum(1 for r in results if r.get("Found", False))
        not_found_count = sum(1 for r in results if r.get("Status") == "NOT_FOUND")
        error_count = sum(1 for r in results if r.get("Status") == "ERROR")
        
        print(f"\n Scraping Complete!")
        print(f"   Total products: {len(results)}")
        print(f"   Found: {found_count}")
        print(f"   Not Found: {not_found_count}")
        print(f"   Errors: {error_count}")
        print(f"   Success Rate: {found_count/len(results)*100:.1f}%")
        print(f"   Results saved to: {args.output_file}")
    finally:
        driver.quit()
if __name__ == "__main__":
    main()
