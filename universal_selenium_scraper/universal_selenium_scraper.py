#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Universal Selenium Scraper
Based on the working arabiMart.py patterns
Works for JoCell, AmmanCart, and other dynamic sites
"""

import argparse
import os
import re
import time
from typing import List, Optional, Tuple
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException


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


def parse_srcset(srcset: str) -> Optional[str]:
    """Parse srcset and return best URL."""
    if not srcset:
        return None
    best_url, best_w = None, -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try: 
                w = int(bits[1][:-1])
            except: 
                w = 0
        if w > best_w:
            best_w, best_url = w, url
    return best_url or None


def absolutize(url: str, base_url: str) -> str:
    """Convert relative URL to absolute."""
    if not url: 
        return url
    if url.startswith("//"): 
        return "https:" + url
    if url.startswith("/"):  
        return base_url + url
    return url


def clean_image_url(u: str) -> str:
    """Clean image URL by removing size parameters."""
    if not u: 
        return u
    u = re.sub(r"([?&])(w|h|width|height|fit|format|auto)=[^&]+", r"\1", u)
    u = re.sub(r"[?&]+$", "", u)
    return u


def collect_product_images(driver, base_url: str) -> List[str]:
    """Collect product images using universal selectors."""
    print(f"   → Searching for product images...")
    
    # Universal image selectors
    img_selectors = [
        # Product gallery selectors
        ".product-gallery img",
        ".product-images img", 
        ".product-photos img",
        ".gallery img",
        ".images img",
        ".photos img",
        ".thumbnails img",
        ".preview img",
        ".main-image img",
        ".featured-image img",
        ".product-image img",
        ".product-media img",
        
        # E-commerce platform selectors
        ".woocommerce-product-gallery img",
        ".product__media-list img",
        ".product__gallery img",
        ".product__images img",
        
        # Generic selectors
        "img[src*='product']",
        "img[src*='item']",
        "img[src*='gallery']",
        "img[src*='photo']",
        "img[data-src*='product']",
        "img[data-image]",
        "img[data-zoom-image]",
        
        # Class-based selectors
        "[class*='gallery'] img",
        "[class*='image'] img",
        "[class*='photo'] img",
        "[class*='thumbnail'] img",
        "[class*='preview'] img",
        "[class*='main'] img",
        "[class*='featured'] img"
    ]
    
    candidates = set()
    
    for selector in img_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                print(f"   → Found {len(elements)} images with selector: {selector}")
                
                for img in elements:
                    try:
                        srcset = img.get_attribute("srcset") or ""
                        src = img.get_attribute("src") or ""
                        alt = img.get_attribute("alt") or ""
                        
                        # Get the best URL
                        url = parse_srcset(srcset) if srcset else src
                        url = absolutize(url, base_url)
                        
                        if url and not url.startswith("data:"):
                            candidates.add((url, alt))
                            print(f"   → Product image: {url[:80]}...")
                    except Exception:
                        continue
        except Exception as e:
            print(f"   → Selector {selector} failed: {e}")
            continue

    # Filter out non-product images
    filtered = []
    url_groups = {}  # base_url -> (full_url, alt, size_estimate)
    
    for url, alt in candidates:
        low_url = url.lower()
        low_alt = alt.lower()
        
        # Skip obvious non-product images
        skip_terms = [
            "logo", "icon", "favicon", "banner", "header", "footer", 
            "nav", "menu", "button", "arrow", "social", "facebook", 
            "instagram", "twitter", "youtube", "payment", "visa", 
            "mastercard", "loading", "spinner", "error", "404"
        ]
        
        if any(term in low_url or term in low_alt for term in skip_terms):
            continue
            
        # Skip very small images
        if any(size in low_url for size in ["16x16", "24x24", "32x32", "48x48"]):
            continue
            
        cleaned_url = clean_image_url(url)
        
        # Extract base URL (remove size parameters)
        base_url_clean = re.sub(r'[?&](w|h|width|height|size|resize|fit|format|auto)=[^&]*', '', cleaned_url)
        base_url_clean = re.sub(r'[?&]+$', '', base_url_clean)
        
        # Estimate image size from URL parameters
        size_estimate = 0
        size_match = re.search(r'[?&](w|width)=(\d+)', cleaned_url)
        if size_match:
            size_estimate = int(size_match.group(2))
        else:
            # If no size parameter, check for common size indicators in URL
            if any(size in cleaned_url for size in ['_large', '_big', '_full', '_original']):
                size_estimate = 1000
            elif any(size in cleaned_url for size in ['_medium', '_med']):
                size_estimate = 500
            elif any(size in cleaned_url for size in ['_small', '_thumb', '_mini']):
                size_estimate = 200
            else:
                size_estimate = 500  # default
        
        # Keep only the largest version of each base URL
        if base_url_clean not in url_groups or size_estimate > url_groups[base_url_clean][2]:
            url_groups[base_url_clean] = (cleaned_url, alt, size_estimate)
            print(f"   → Added image: {cleaned_url[:80]}... (size: {size_estimate})")

    # Extract final URLs
    ordered = []
    for base_url_clean, (final_url, alt, size) in url_groups.items():
        ordered.append(final_url)
        print(f"   → Final image: {final_url[:80]}... (size: {size})")

    print(f"   → Found {len(ordered)} unique product images")
    return ordered


def get_description_html(driver) -> str:
    """Get product description using universal selectors."""
    # Universal description selectors
    desc_selectors = [
        # Specific selectors
        "p.whitespace-pre-wrap.mt-4.pt-4.border-t.break-words",  # ArabiMart
        ".product-description",
        ".product-info", 
        ".product-details",
        ".product-summary",
        ".product-content",
        ".product-text",
        ".description",
        ".item-description",
        ".item-info",
        ".item-details",
        ".product-specifications",
        ".specifications",
        ".features",
        ".product-features",
        
        # E-commerce platform selectors
        ".woocommerce-product-details__short-description",
        ".woocommerce-Tabs-panel--description",
        "#tab-description",
        ".product__description",
        ".product-single__description",
        
        # Generic selectors
        "[itemprop='description']",
        ".ProductMeta__Description",
        "#product-description",
        "[class*='description']",
        "[class*='info']",
        "[class*='details']",
        "[class*='content']",
        "[class*='text']",
        "[class*='summary']"
    ]
    
    for selector in desc_selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, selector)
            html = (el.get_attribute("innerHTML") or "").strip()
            if html and len(html) > 30:
                print(f"   → Found description with selector: {selector}")
                return html
        except NoSuchElementException:
            continue
    
    print(f"   → No description found with any selector")
    return ""


def search_and_open_first_product(driver, site_url: str, sku: str, pause: float) -> Optional[str]:
    """Search for SKU and click first product result."""
    
    # Generate search URL based on site
    if "jo-cell.com" in site_url:
        search_url = f"{site_url}/search?type=product&q={sku}"
    elif "ammancart.com" in site_url:
        search_url = f"{site_url}/a/search?q={sku}&options%5Bprefix%5D=last"
    elif "arabiemart.com" in site_url:
        search_url = f"{site_url}/search?keyword={sku}"
    elif "organdle.co" in site_url:
        search_url = f"{site_url}/search?q={sku}"
    else:
        # Generic search URL
        search_url = f"{site_url}/search?q={sku}"
    
    print(f"   → Searching: {search_url}")
    driver.get(search_url)

    # Wait for search results to load
    print(f"   → Waiting for search results...")
    try:
        WebDriverWait(driver, 20).until(
            EC.any_of(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/products/"]')),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/product/"]')),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/items/"]')),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "article.card")),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "main a[href*='/product']")),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".product-item")),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".product-card")),
                # Add Organdle specific selectors
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='product']")),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".product a")),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='products']"))
            )
        )
        print(f"   → Search results loaded")
    except TimeoutException:
        print(f"   → Timeout waiting for search results")
        return None

    # Find product links - try multiple selectors
    links = []
    selectors = [
        'a[href*="/products/"]',
        'a[href*="/product/"]', 
        'a[href*="/items/"]',
        "article.card a[href*='/product']",
        "main a[href*='/product']",
        "a[href*='/product']",
        ".product-item a",
        ".product-card a",
        ".product a",
        ".item a",
        ".result a",
        # Add Organdle specific selectors
        "a[href*='product']",
        "a[href*='products']"
    ]
    
    for sel in selectors:
        try:
            found_links = driver.find_elements(By.CSS_SELECTOR, sel)
            for link in found_links:
                href = link.get_attribute("href") or ""
                if href and ("/product" in href or "/items/" in href):
                    # Skip non-product links
                    if not any(x in href for x in ["/account", "/login", "/orders", "/cart", "/search", "/category", "/filter"]):
                        links.append(link)
                        print(f"   → Found product link: {href}")
            if links:
                break
        except Exception as e:
            print(f"   → Selector {sel} failed: {e}")
            continue
    
    if not links:
        print(f"   × No product links found")
        return None

    # Use the first product link
    link = links[0]
    href = link.get_attribute("href")
    print(f"   → Using first product: {href}")

    # Try to click the link
    old_url = driver.current_url
    print(f"   → Current URL before click: {old_url}")
    
    try:
        # Scroll to element first
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        time.sleep(0.5)
        
        # Try clicking
        print(f"   → Attempting to click...")
        link.click()
        print(f"   → Click executed")
        
        # Wait for navigation
        try:
            WebDriverWait(driver, 10).until(EC.url_changes(old_url))
            print(f"   → Navigation successful, new URL: {driver.current_url}")
        except TimeoutException:
            print(f"   → No navigation detected, trying direct navigation")
            driver.get(href)
            
    except Exception as e:
        print(f"   → Click failed: {e}, trying JavaScript click")
        try:
            driver.execute_script("arguments[0].click();", link)
            print(f"   → JavaScript click executed")
            try:
                WebDriverWait(driver, 10).until(EC.url_changes(old_url))
                print(f"   → Navigation successful after JS click")
            except TimeoutException:
                print(f"   → No navigation after JS click, using direct navigation")
                driver.get(href)
        except Exception as e2:
            print(f"   → JavaScript click also failed: {e2}, using direct navigation")
            driver.get(href)
    
    time.sleep(pause)
    final_url = driver.current_url
    print(f"   → Final URL: {final_url}")
    return final_url


def scrape_product(driver, base_url: str) -> Tuple[str, str]:
    """Scrape product description and images."""
    body_html = get_description_html(driver)
    images = collect_product_images(driver, base_url)
    return body_html, ";".join(images)


def run_for_sku(driver, site_url: str, sku: str, pause: float) -> Tuple[str, str, str]:
    """Run complete scraping process for one SKU."""
    # 1+2) search and click first product
    url = search_and_open_first_product(driver, site_url, sku, pause)
    if not url:
        return "", "", ""

    # wait for product signals
    try:
        WebDriverWait(driver, 15).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-title")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-name")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".description"))
            )
        )
    except TimeoutException:
        pass

    # 3) scrape
    body_html, image_src = scrape_product(driver, site_url)

    # 4) return
    return body_html, image_src, url


def main():
    """Main function for CLI usage."""
    ap = argparse.ArgumentParser(description="Universal Selenium scraper for dynamic e-commerce sites.")
    ap.add_argument("--site", required=True, help="Site URL (e.g., https://jo-cell.com)")
    ap.add_argument("--sku", required=True, help="SKU to search for")
    ap.add_argument("--out", dest="out", required=True, help="Output Excel file")
    ap.add_argument("--pause", type=float, default=1.0, help="Pause between steps (sec)")
    ap.add_argument("--headful", action="store_true", help="Headed Chrome")
    args = ap.parse_args()

    # Create output directory
    output_dir = os.path.dirname(args.out)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Build driver
    driver = build_driver(args.headful)

    try:
        print(f"🔍 Universal Selenium Scraper")
        print(f"Site: {args.site}")
        print(f"SKU: {args.sku}")
        print(f"Output: {args.out}")
        print()

        body_html, image_src, url = run_for_sku(driver, args.site, args.sku, args.pause)

        # Create results DataFrame
        result = {
            'URL': args.site,
            'SKU': args.sku,
            'Product URL': url,
            'Description': body_html,
            'Images': image_src,
            'Image Count': len(image_src.split(';')) if image_src else 0,
            'Status': 'SUCCESS' if (body_html and image_src) else 'PARTIAL' if (body_html or image_src) else 'FAILED',
            'Timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }

        df = pd.DataFrame([result])
        df.to_excel(args.out, index=False)
        
        print(f"\n📊 Results:")
        print(f"  Product URL: {url}")
        print(f"  Description: {len(body_html)} characters")
        print(f"  Images: {len(image_src.split(';')) if image_src else 0}")
        print(f"  Status: {result['Status']}")
        print(f"  Saved to: {args.out}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()

