#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, os, re, time
from typing import List, Optional, Tuple
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

# ---------------- utils ----------------

def build_driver(headful: bool, profile: Optional[str] = None) -> webdriver.Chrome:
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1200")
    if profile:
        opts.add_argument(f"--user-data-dir={os.path.abspath(profile)}")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver

def wait_css(driver, sel, timeout=15):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))

def normalize(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())

def parse_srcset(srcset: str) -> Optional[str]:
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
            try: w = int(bits[1][:-1])
            except: w = 0
        if w > best_w:
            best_w, best_url = w, url
    return best_url or None

def absolutize(url: str) -> str:
    if not url: return url
    if url.startswith("//"): return "https:" + url
    if url.startswith("/"):  return "https://arabiemart.com" + url
    return url

def clean_image_url(u: str) -> str:
    if not u: return u
    u = re.sub(r"([?&])(w|h|width|height|fit|format|auto)=[^&]+", r"\1", u)
    u = re.sub(r"[?&]+$", "", u)
    return u

# ---------------- scraping pieces ----------------

DESC_SEL = "p.whitespace-pre-wrap.mt-4.pt-4.border-t.break-words"
GALLERY_SCOPE = "div.mt-4.flex.me-4"  # you asked to use this container

def collect_product_images(driver) -> List[str]:
    print(f"   → Searching for product images...")
    
    # Target the specific UL structure you mentioned
    ul_selectors = [
        "ul.w-full.relative.flex.whitespace-nowrap.overflow-x-auto.lg\\:overflow-hidden",
        "ul[class*='w-full'][class*='relative'][class*='flex']",
        "ul[class*='whitespace-nowrap'][class*='overflow-x-auto']"
    ]
    
    candidates = set()
    
    for ul_selector in ul_selectors:
        try:
            ul_elements = driver.find_elements(By.CSS_SELECTOR, ul_selector)
            if ul_elements:
                print(f"   → Found {len(ul_elements)} UL elements with selector: {ul_selector}")
                
                for ul in ul_elements:
                    # Find all li elements inside this ul
                    li_elements = ul.find_elements(By.CSS_SELECTOR, "li")
                    print(f"   → Found {len(li_elements)} li elements in UL")
                    
                    for li in li_elements:
                        # Find images inside each li
                        img_elements = li.find_elements(By.CSS_SELECTOR, "img, picture source, picture img")
                        for img in img_elements:
                            try:
                                srcset = img.get_attribute("srcset") or ""
                                src = img.get_attribute("src") or ""
                                alt = img.get_attribute("alt") or ""
                                
                                # Get the best URL
                                url = parse_srcset(srcset) if srcset else src
                                url = absolutize(url)
                                
                                if url and not url.startswith("data:"):
                                    candidates.add((url, alt))
                                    print(f"   → Product image: {url[:80]}...")
                            except Exception:
                                continue
        except Exception as e:
            print(f"   → Selector {ul_selector} failed: {e}")
            continue

    # Fallback: try the original gallery scope if no UL found
    if not candidates:
        print(f"   → No UL gallery found, trying fallback selectors...")
        fallback_selectors = [
            f"{GALLERY_SCOPE} img",
            f"{GALLERY_SCOPE} picture source",
            f"{GALLERY_SCOPE} picture img",
            "main img"
        ]
        
        for css in fallback_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, css)
                if elements:
                    print(f"   → Found {len(elements)} images with fallback selector: {css}")
                    for el in elements:
                        try:
                            srcset = el.get_attribute("srcset") or ""
                            src = el.get_attribute("src") or ""
                            alt = el.get_attribute("alt") or ""
                            
                            url = parse_srcset(srcset) if srcset else src
                            url = absolutize(url)
                            
                            if url and not url.startswith("data:"):
                                candidates.add((url, alt))
                                print(f"   → Fallback image: {url[:80]}...")
                        except Exception:
                            continue
            except Exception:
                continue

    # Filter out non-product images and group by base URL
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
        base_url = re.sub(r'[?&](w|h|width|height|size|resize|fit|format|auto)=[^&]*', '', cleaned_url)
        base_url = re.sub(r'[?&]+$', '', base_url)
        
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
        if base_url not in url_groups or size_estimate > url_groups[base_url][2]:
            url_groups[base_url] = (cleaned_url, alt, size_estimate)
            print(f"   → Added image: {cleaned_url[:80]}... (size: {size_estimate})")

    # Extract final URLs
    ordered = []
    for base_url, (final_url, alt, size) in url_groups.items():
        ordered.append(final_url)
        print(f"   → Final image: {final_url[:80]}... (size: {size})")

    print(f"   → Found {len(ordered)} unique product images")
    return ordered

def get_description_html(driver) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, DESC_SEL)
        return (el.get_attribute("innerHTML") or "").strip()
    except NoSuchElementException:
        return ""

# ---------------- step 1+2: search then click first product ----------------

def search_and_open_first_product(driver, sku: str, pause: float) -> Optional[str]:
    """Search using direct URL and click first product result."""
    search_url = f"https://arabiemart.com/search?keyword={sku}"
    print(f"   → Searching: {search_url}")
    driver.get(search_url)

    # Wait for search results to load
    print(f"   → Waiting for search results...")
    try:
        WebDriverWait(driver, 20).until(
            EC.any_of(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/items/"]')),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "article.card")),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "main a[href*='/product']"))
            )
        )
        print(f"   → Search results loaded")
    except TimeoutException:
        print(f"   → Timeout waiting for search results")
        return None

    # Find product links - try multiple selectors
    links = []
    selectors = [
        'a[href*="/items/"]',  # From your working code
        "article.card a[href*='/product']",
        "main a[href*='/product']",
        "a[href*='/product']"
    ]
    
    for sel in selectors:
        try:
            found_links = driver.find_elements(By.CSS_SELECTOR, sel)
            for link in found_links:
                href = link.get_attribute("href") or ""
                if href and ("/product" in href or "/items/" in href):
                    # Skip non-product links
                    if not any(x in href for x in ["/account", "/login", "/orders", "/cart", "/search"]):
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

    # Check if link is visible and clickable
    try:
        is_displayed = link.is_displayed()
        is_enabled = link.is_enabled()
        print(f"   → Link visible: {is_displayed}, enabled: {is_enabled}")
    except Exception as e:
        print(f"   → Error checking link state: {e}")

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

# ---------------- step 3: check SKU on product page ----------------

def page_contains_sku(driver, target_sku: str) -> bool:
    raw = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    if target_sku.lower() in raw:
        return True
    return normalize(target_sku) in normalize(raw)

# ---------------- step 4: scrape ----------------

def scrape_product(driver) -> Tuple[str, str]:
    body_html = get_description_html(driver)
    images = collect_product_images(driver)
    return body_html, ";".join(images)

# ---------------- per-SKU orchestrator (1..5) ----------------

def run_for_sku(driver, sku: str, pause: float) -> Tuple[str, str, str]:
    # 1+2) search and click first product
    url = search_and_open_first_product(driver, sku, pause)
    if not url:
        return "", "", ""

    # wait for product signals
    try:
        WebDriverWait(driver, 15).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1.bigtitle")),
                EC.presence_of_element_located((By.CSS_SELECTOR, DESC_SEL))
            )
        )
    except TimeoutException:
        pass

    # 3) scrape (no SKU checking)
    body_html, image_src = scrape_product(driver)

    # 4) return (repeat happens in caller loop)
    return body_html, image_src, url

# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="Arabiemart scraper: search -> click first -> scrape -> repeat.")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", dest="out", required=True, help="Output Excel file")
    ap.add_argument("--sheet", dest="sheet", default=None, help="Worksheet name (default: first)")
    ap.add_argument("--sku-col", dest="sku_col", required=True, help="Column containing SKUs")
    ap.add_argument("--pause", type=float, default=1.0, help="Pause between steps (sec)")
    ap.add_argument("--headful", action="store_true", help="Headed Chrome")
    ap.add_argument("--profile", default=None, help="Chrome user-data-dir (optional)")
    args = ap.parse_args()

    # read Excel
    df = pd.read_excel(args.inp, sheet_name=args.sheet)

    if isinstance(df, dict):  # multiple sheets
        if args.sheet is None:
            first_sheet = list(df.keys())[0]
            print(f"Multiple sheets detected. Using first sheet: '{first_sheet}'")
            df = df[first_sheet]
        else:
            df = df[args.sheet]

    # ensure output columns exist
    for col in ["Body (HTML)", "Image Src", "Source_URL"]:
        if col not in df.columns:
            df[col] = ""


    driver = build_driver(args.headful, args.profile)

    try:
        for i, row in df.iterrows():
            sku = str(row[args.sku_col]).strip()
            if not sku or sku.lower() in ("nan", "none"):
                continue

            print(f"[{i+1}/{len(df)}] SKU={sku} → search/click/scrape")
            try:
                body_html, image_src, url = run_for_sku(driver, sku, args.pause)
            except TimeoutException:
                body_html, image_src, url = "", "", ""

            if url:
                df.at[i, "Body (HTML)"] = body_html
                df.at[i, "Image Src"]   = image_src
                df.at[i, "Source_URL"]  = url
                print(f"   ✓ OK: {url} | images={len(image_src.split(';')) if image_src else 0}")
            else:
                print(f"   × Not found")

    finally:
        driver.quit()

    # Normalize file extension to lowercase for pandas compatibility
    output_path = str(args.out).lower()
    if not output_path.endswith('.xlsx'):
        output_path = output_path.replace('.xls', '.xlsx')
    
    df.to_excel(output_path, index=False)
    print(f"\nDone → {output_path}")

if __name__ == "__main__":
    main()
