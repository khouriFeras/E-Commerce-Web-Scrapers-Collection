#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Arabi E-mart scraper:
#   for each SKU -> search https://arabiemart.com/<locale>/products/search?keyword=<sku>
#   -> open the FIRST product -> verify the SKU appears on that product page
#   -> scrape description (Body HTML) + product images.
# If the SKU is not on the opened product page (fuzzy/suggested match), the row is skipped.

import argparse, os, re, time
from typing import List, Optional, Tuple
from urllib.parse import quote_plus
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

BASE = "https://arabiemart.com"
DEFAULT_LOCALE = "jo-en"

# Product detail pages: /jo-en/product/<slug>-<id>/<vendor>
PRODUCT_LINK_SEL = "a[href*='/product/']"
# Product description/detail block on the product page.
DESC_SEL = "div.single-product-detail"
# Related / other-merchant product cards — their images are NOT this product's.
RELATED_CARD_CLASS = "product-card-third"

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

def normalize(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())

# ---------------- image collection ----------------

def _img_key(u: str) -> str:
    """Dedup key: strip the CDN transform tail and any -WxH size suffix so that
    different renditions of the same source photo collapse to one key."""
    name = u.split("?")[0].split("/")[-1]
    name = re.split(r"\.(?:jpe?g|png|webp|gif)_", name, maxsplit=1, flags=re.I)[0]
    name = re.sub(r"-\d+x\d+$", "", name)
    return name.lower()

def _img_size_score(u: str) -> int:
    m = re.search(r"-(\d+)x(\d+)\.", u)
    if m:
        return int(m.group(1)) * int(m.group(2))
    return 10 ** 9  # no size suffix -> treat as the original/largest rendition

def collect_product_images(driver) -> List[str]:
    """Collect this product's images (cdn.acabes.com), excluding related/merchant
    product cards, and keep the largest rendition of each distinct photo."""
    js = (
        "return [...document.querySelectorAll('img')]"
        f"  .filter(i => !i.closest('.{RELATED_CARD_CLASS}'))"
        "  .map(i => i.currentSrc || i.getAttribute('src') || '')"
        "  .filter(s => s.includes('cdn.acabes'));"
    )
    try:
        raw = driver.execute_script(js) or []
    except Exception as e:
        print(f"   → image JS failed: {e}")
        raw = []

    best = {}   # key -> (url, score)
    order = []  # preserve first-seen order
    for u in raw:
        low = u.lower()
        if any(t in low for t in ["logo", "favicon", "placeholder", "sprite", "loading"]):
            continue
        k = _img_key(u)
        score = _img_size_score(u)
        if k not in best:
            order.append(k)
            best[k] = (u, score)
        elif score > best[k][1]:
            best[k] = (u, score)

    ordered = [best[k][0] for k in order]
    print(f"   → Found {len(ordered)} unique product images")
    return ordered

def get_description_html(driver) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, DESC_SEL)
        return (el.get_attribute("innerHTML") or "").strip()
    except NoSuchElementException:
        return ""

# ---------------- search -> open first product ----------------

def search_and_open_first_product(driver, sku: str, pause: float, locale: str) -> Optional[str]:
    search_url = f"{BASE}/{locale}/products/search?keyword={quote_plus(sku)}"
    print(f"   → Searching: {search_url}")
    driver.get(search_url)

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, PRODUCT_LINK_SEL))
        )
    except TimeoutException:
        print(f"   × No product results")
        return None

    href = None
    for link in driver.find_elements(By.CSS_SELECTOR, PRODUCT_LINK_SEL):
        h = (link.get_attribute("href") or "").strip()
        if h and "/product/" in h:
            href = h
            break
    if not href:
        print(f"   × No product link found")
        return None

    print(f"   → First product: {href}")
    driver.get(href)
    time.sleep(pause)
    return driver.current_url

# ---------------- SKU verification on the product page ----------------

def page_contains_sku(driver, target_sku: str) -> bool:
    # Check only the product's own detail block + page title. The full page body
    # includes "Similar Product" / "Merchant Other Product" sections whose model
    # numbers cause false positives (e.g. searching TAC-12CHSD/TPH11 matches a
    # listed TAC-12CHSD/TPH11I on an unrelated product's page).
    parts = []
    try:
        parts.append(driver.find_element(By.CSS_SELECTOR, DESC_SEL).text or "")
    except NoSuchElementException:
        pass
    parts.append(driver.title or "")
    hay = " ".join(parts)
    if target_sku.lower() in hay.lower():
        return True
    return normalize(target_sku) in normalize(hay)

# ---------------- scrape ----------------

def scrape_product(driver) -> Tuple[str, str]:
    body_html = get_description_html(driver)
    images = collect_product_images(driver)
    return body_html, ";".join(images)

# ---------------- per-SKU orchestrator ----------------

def run_for_sku(driver, sku: str, pause: float, locale: str) -> Tuple[str, str, str]:
    # 1) search + open the first product
    url = search_and_open_first_product(driver, sku, pause, locale)
    if not url:
        return "", "", ""

    # 2) wait for the product page to render
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, DESC_SEL))
        )
    except TimeoutException:
        pass

    # 3) verify the SKU actually appears on the opened product page. The site returns
    #    fuzzy suggestions, so the first product is often a different variant.
    if not page_contains_sku(driver, sku):
        print(f"   × SKU not on product page (fuzzy match) → skipping")
        return "", "", ""

    # 4) scrape description + images
    body_html, image_src = scrape_product(driver)
    return body_html, image_src, url

# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="Arabiemart scraper: search -> first product -> verify SKU -> scrape.")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", dest="out", required=True, help="Output Excel file")
    ap.add_argument("--sheet", dest="sheet", default=None, help="Worksheet name (default: first)")
    ap.add_argument("--sku-col", dest="sku_col", required=True, help="Column containing SKUs")
    ap.add_argument("--locale", default=DEFAULT_LOCALE, help=f"Site locale segment (default: {DEFAULT_LOCALE})")
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

            print(f"[{i+1}/{len(df)}] SKU={sku} -> search/open/verify/scrape")
            try:
                body_html, image_src, url = run_for_sku(driver, sku, args.pause, args.locale)
            except TimeoutException:
                body_html, image_src, url = "", "", ""

            if url:
                df.at[i, "Body (HTML)"] = body_html
                df.at[i, "Image Src"]   = image_src
                df.at[i, "Source_URL"]  = url
                print(f"   OK: {url} | images={len(image_src.split(';')) if image_src else 0}")
            else:
                print(f"   x Not found")

    finally:
        driver.quit()

    # Normalize file extension to lowercase for pandas compatibility
    output_path = str(args.out).lower()
    if not output_path.endswith('.xlsx'):
        output_path = output_path.replace('.xls', '.xlsx')

    df.to_excel(output_path, index=False)
    print(f"\nDone -> {output_path}")

if __name__ == "__main__":
    main()
