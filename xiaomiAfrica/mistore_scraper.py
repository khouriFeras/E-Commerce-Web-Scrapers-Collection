#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MiStore Africa scraper

Scrapes product titles, descriptions and images from mistore.africa for SKUs in an Excel file.

Flow:
1. Read SKUs from Excel file
2. For each SKU, visit https://mistore.africa/product/{SKU}/
3. Extract product title from class="product_title entry-title wd-entities-title"
4. Extract product images
5. Extract description from class="wc-tab-inner wd-entry-content wd-scroll-content"
6. Click on all tabs with class="nav-link-text wd-tabs-title"
7. Extract content from class="wc-tab-inner wd-entry-content" for each tab
8. Combine everything into "title", "description" and "images" columns

Dependencies: pip install selenium pandas openpyxl
"""

import argparse
import time
import re
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

# ============================
# CONFIG
# ============================
SLEEP = 1.0
TIMEOUT = 15
BASE_URL = "https://mistore.africa/product"


def make_driver(headful: bool = False):
    """Create and configure Chrome driver."""
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1360,2200")
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--log-level=3")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver


def clean_text(text: str) -> str:
    """Clean and normalize text."""
    if not isinstance(text, str):
        return ""
    # Replace multiple whitespace with single space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_title(driver) -> str:
    """Extract product title from class='product_title entry-title wd-entities-title'."""
    try:
        element = driver.find_element(By.CSS_SELECTOR, ".product_title.entry-title.wd-entities-title")
        text = element.text or element.get_attribute("textContent") or ""
        return clean_text(text)
    except NoSuchElementException:
        return ""
    except Exception as e:
        print(f"  Warning: Error extracting title: {e}")
        return ""


def extract_main_description(driver) -> str:
    """Extract description from class='wc-tab-inner wd-entry-content wd-scroll-content'."""
    try:
        element = driver.find_element(By.CSS_SELECTOR, ".wc-tab-inner.wd-entry-content.wd-scroll-content")
        text = element.text or element.get_attribute("textContent") or ""
        return clean_text(text)
    except NoSuchElementException:
        return ""
    except Exception as e:
        print(f"  Warning: Error extracting main description: {e}")
        return ""


def click_all_tabs(driver, wait: WebDriverWait) -> List[str]:
    """Click all tabs with class='nav-link-text wd-tabs-title' and extract content."""
    descriptions = []
    
    try:
        # Find all tab links
        tab_elements = driver.find_elements(By.CSS_SELECTOR, ".nav-link-text.wd-tabs-title")
        
        if not tab_elements:
            print("  No tabs found with class 'nav-link-text wd-tabs-title'")
            return descriptions
        
        print(f"  Found {len(tab_elements)} tabs")
        
        # Click each tab and extract content
        for idx, tab in enumerate(tab_elements):
            try:
                # Scroll to tab
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", tab)
                time.sleep(0.3)
                
                # Get tab text for logging
                tab_text = tab.text.strip() or f"Tab {idx + 1}"
                print(f"  Clicking tab: {tab_text}")
                
                # Click the tab
                try:
                    tab.click()
                except Exception:
                    # Try JavaScript click if regular click fails
                    driver.execute_script("arguments[0].click();", tab)
                
                # Wait for content to load
                time.sleep(0.5)
                
                # Extract content from the active tab
                try:
                    content_elements = driver.find_elements(By.CSS_SELECTOR, ".wc-tab-inner.wd-entry-content")
                    
                    for content_el in content_elements:
                        # Check if element is visible (active tab)
                        if content_el.is_displayed():
                            text = content_el.text or content_el.get_attribute("textContent") or ""
                            text = clean_text(text)
                            if text and text not in descriptions:
                                descriptions.append(text)
                                print(f"    Extracted {len(text)} characters from {tab_text}")
                            break
                except Exception as e:
                    print(f"    Warning: Could not extract content from tab {tab_text}: {e}")
                    
            except StaleElementReferenceException:
                print(f"  Warning: Tab element became stale, skipping")
                continue
            except Exception as e:
                print(f"  Warning: Error clicking tab {idx + 1}: {e}")
                continue
        
    except NoSuchElementException:
        print("  No tab elements found")
    except Exception as e:
        print(f"  Warning: Error finding tabs: {e}")
    
    return descriptions


def extract_images(driver, base_url: str) -> List[str]:
    """Extract product images from product gallery containers only."""
    images = []
    seen_urls = set()
    
    # WooCommerce product gallery container selectors (most specific first)
    gallery_containers = [
        ".woocommerce-product-gallery",
        ".woocommerce-product-gallery__wrapper",
        ".product-gallery",
        ".product-images",
        ".product__gallery",
        ".product__media",
        ".product__images",
        ".product-photos",
        "div.product div.images",
        "[class*='product'][class*='gallery']",
        "[class*='product'][class*='image']",
    ]
    
    # Try to find product gallery container first
    gallery_element = None
    for selector in gallery_containers:
        try:
            gallery_element = driver.find_element(By.CSS_SELECTOR, selector)
            print(f"  Found product gallery container: {selector}")
            break
        except NoSuchElementException:
            continue
    
    # If no gallery container found, return empty (don't extract all page images)
    if not gallery_element:
        print("  No product gallery container found")
        return []
    
    # Extract images only from within the gallery container
    try:
        # Find all img elements within the gallery
        img_elements = gallery_element.find_elements(By.CSS_SELECTOR, "img")
        
        # Also check for anchor tags with href to images (often full-size versions)
        anchor_elements = gallery_element.find_elements(By.CSS_SELECTOR, "a[href]")
        
        # Process anchor tags first (usually full-size images)
        for anchor in anchor_elements:
            try:
                href = anchor.get_attribute("href") or ""
                if not href or href.startswith("data:"):
                    continue
                
                # Check if href points to an image
                if any(ext in href.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
                    # Convert relative URLs to absolute
                    if href.startswith("//"):
                        href = "https:" + href
                    elif href.startswith("/"):
                        href = urljoin(base_url, href)
                    
                    if href not in seen_urls:
                        seen_urls.add(href)
                        images.append(href)
                        print(f"    Found image (from anchor): {href[:80]}...")
            except Exception:
                continue
        
        # Process img elements
        for img in img_elements:
            try:
                # Try data attributes first (usually full-size/high-quality)
                srcset = img.get_attribute("srcset") or ""
                src = img.get_attribute("src") or ""
                data_src = img.get_attribute("data-src") or ""
                data_large = img.get_attribute("data-large_image") or ""
                data_zoom = img.get_attribute("data-zoom-image") or ""
                data_full = img.get_attribute("data-full") or ""
                
                # Get the best available URL (prefer high-quality data attributes)
                url = data_zoom or data_full or data_large or data_src or src
                
                # Parse srcset if available (take the largest)
                if srcset and not url:
                    srcset_parts = srcset.split(",")
                    if srcset_parts:
                        # Take the last part (usually highest resolution)
                        url = srcset_parts[-1].strip().split()[0]
                elif srcset and url:
                    # If both exist, prefer srcset if it's larger
                    srcset_parts = srcset.split(",")
                    if srcset_parts:
                        srcset_url = srcset_parts[-1].strip().split()[0]
                        if srcset_url:
                            url = srcset_url
                
                if not url or url.startswith("data:"):
                    continue
                
                # Convert relative URLs to absolute
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("/"):
                    url = urljoin(base_url, url)
                
                # Skip placeholder images
                url_lower = url.lower()
                skip_terms = ["placeholder", "logo", "icon", "favicon", "banner", 
                              "header", "footer", "loading", "spinner", "avatar"]
                if any(term in url_lower for term in skip_terms):
                    continue
                
                # Skip very small images
                if any(size in url_lower for size in ["16x16", "24x24", "32x32", "48x48", "64x64"]):
                    continue
                
                # Remove duplicates
                if url not in seen_urls:
                    seen_urls.add(url)
                    images.append(url)
                    print(f"    Found image: {url[:80]}...")
                    
            except Exception:
                continue
                
    except Exception as e:
        print(f"  Warning: Error extracting images from gallery: {e}")
    
    # Remove duplicates while preserving order
    unique_images = []
    seen = set()
    for img in images:
        if img not in seen:
            seen.add(img)
            unique_images.append(img)
    
    return unique_images


def scrape_product(driver, wait: WebDriverWait, sku: str) -> Tuple[str, str, List[str]]:
    """Scrape product title, description and images for a given SKU."""
    url = f"{BASE_URL}/{sku}/"
    
    try:
        print(f"  Loading: {url}")
        driver.get(url)
        
        # Wait for page to load
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(SLEEP)
        
        # Extract title
        title = extract_title(driver)
        print(f"  Title: {title[:60] if title else 'Not found'}...")
        
        # Extract images
        images = extract_images(driver, url)
        print(f"  Found {len(images)} images")
        
        # Extract main description
        main_desc = extract_main_description(driver)
        print(f"  Main description: {len(main_desc)} characters")
        
        # Click all tabs and extract their content
        tab_descriptions = click_all_tabs(driver, wait)
        
        # Combine all descriptions
        all_parts = []
        if main_desc:
            all_parts.append(main_desc)
        
        for tab_desc in tab_descriptions:
            if tab_desc and tab_desc not in all_parts:
                all_parts.append(tab_desc)
        
        combined = "\n\n".join(all_parts)
        return title, combined, images
        
    except TimeoutException:
        print(f"  Error: Timeout loading page")
        return "", "", []
    except Exception as e:
        print(f"  Error: {e}")
        return "", "", []


def main():
    ap = argparse.ArgumentParser(description="MiStore Africa scraper by SKU")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", required=True, help="Output Excel file")
    ap.add_argument("--sku-col", dest="sku_col", default="SKU", help="SKU column name (default: SKU)")
    ap.add_argument("--headful", action="store_true", help="Show browser window")
    args = ap.parse_args()

    input_path = Path(args.inp)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.inp}")
        return

    # Group B resume: read from output if it exists, else from input
    output_path = Path(args.out)
    data_path = output_path if output_path.exists() else input_path
    print(f"Reading SKUs from: {data_path}")
    df = pd.read_excel(data_path)

    if args.sku_col not in df.columns:
        print(f"Error: Column '{args.sku_col}' not found. Available columns: {list(df.columns)}")
        return
    if data_path == output_path:
        print(f"Resuming from existing output: {output_path}")

    # Ensure output columns exist
    for col in ("title", "description", "images"):
        if col not in df.columns:
            df[col] = ""

    driver = make_driver(headful=args.headful)
    wait = WebDriverWait(driver, TIMEOUT)

    try:
        total = len(df)
        for idx, row in df.iterrows():
            sku = str(row.get(args.sku_col, "")).strip()

            if not sku or sku.lower() in {"nan", "none", ""}:
                print(f"[{idx + 1}/{total}] Empty SKU, skipping")
                continue

            # Skip rows where description is already populated
            existing_desc = str(df.at[idx, "description"]).strip()
            if existing_desc not in ("", "nan"):
                print(f"[{idx + 1}/{total}] Skipping already-done SKU: {sku}")
                continue

            print(f"\n[{idx + 1}/{total}] Processing SKU: {sku}")
            title, desc, images = scrape_product(driver, wait, sku)

            # Join images with comma separator
            images_str = ", ".join(images) if images else ""

            df.at[idx, "title"] = title
            df.at[idx, "description"] = desc
            df.at[idx, "images"] = images_str

            if desc:
                print(f"  Success: {len(desc)} characters extracted, {len(images)} images")
            else:
                print(f"  No description found, {len(images)} images")

            time.sleep(SLEEP)

    finally:
        driver.quit()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, index=False)
    print(f"\nDone! Saved to: {args.out}")
    print(f"  Total rows: {total}")
    print(f"  Titles found: {sum(1 for t in df['title'] if str(t).strip() not in ('', 'nan'))}")
    print(f"  Descriptions found: {sum(1 for d in df['description'] if str(d).strip() not in ('', 'nan'))}")
    print(f"  Images found: {sum(1 for imgs in df['images'] if str(imgs).strip() not in ('', 'nan'))}")


if __name__ == "__main__":
    main()

