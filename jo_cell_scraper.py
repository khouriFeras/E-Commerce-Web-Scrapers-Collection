#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, time, argparse, sys
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
import pandas as pd
from lxml import html as LH
import requests
from tqdm import tqdm

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementClickInterceptedException
)

HOME = "https://jo-cell.com/"

def setup_driver(headful: bool):
    chrome_opts = Options()
    if not headful:
        chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--window-size=1366,900")
    driver = webdriver.Chrome(options=chrome_opts)
    driver.set_page_load_timeout(45)
    return driver

def clean_ws(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s

def absolutize(url: str, base: str) -> str:
    if not url:
        return ""
    return urljoin(base, url)

def to_fullsize_shopify(url: str) -> str:
    """
    Many Shopify-like images have ..._{wxh|w x}.jpg. Remove size segment for full-size.
    This is safe-ish as a best effort.
    """
    if not url:
        return url
    # Canonicalize Shopify CDN variants to the largest/original
    try:
        parts = urlsplit(url)
        path = parts.path
        # Remove trailing size tokens before extension (e.g., _600x, _800x800, _1024x)
        # Apply repeatedly in case of double sizing segments
        for _ in range(3):
            new_path = re.sub(r"_(?:\d+x\d*|\d*x\d+)(?:@[0-9]x)?(\.[a-zA-Z0-9]+)$", r"\1", path)
            if new_path == path:
                break
            path = new_path
        # Also strip inline size tokens that directly precede another underscore+token
        path = re.sub(r"_(?:\d+x\d*|\d*x\d+)(?=_)", "", path)

        # Drop width/height/quality/format query params; keep only version `v`
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() == "v"]
        query = urlencode(q) if q else ""
        return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))
    except Exception:
        # Fallback simple removal
        return re.sub(r"_(\d+x\d*|\d*x\d+)(@2x)?(\.[a-z]+)$", r"\3", url)

def dedupe_keep_order(seq):
    seen = set()
    out = []
    for x in seq:
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out

def wait(driver, seconds=15):
    return WebDriverWait(driver, seconds)

def open_home(driver):
    driver.get(HOME)
    # Accept cookies / close popups gracefully if they exist
    time.sleep(0.8)
    try:
        close_overlays(driver)
    except Exception:
        pass

def close_overlays(driver):
    """
    Close common newsletter/cookie overlays so we don't scrape them.
    Tries several selectors safely; ignores errors.
    """
    selectors = [
        "button[aria-label*='close' i]",
        "button[aria-label*='dismiss' i]",
        "button.close, .modal__close, .popup__close, .newsletter__close",
        "[class*='popup'] button, [class*='modal'] button",
        "#close, .close-button",
    ]
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els[:3]:
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(0.2)
        except Exception:
            continue

def search_sku(driver, sku: str, pause: float):
    """
    - Click search icon or focus the search bar
    - Paste sku, press ENTER
    - Return True if results page loads
    """
    try:
        # On many Shopify themes, search field is in header with name="q" or type="search"
        # Try multiple strategies.
        # 1) A visible search input
        inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="search"], input[name="q"], input[placeholder*="Search"], input[placeholder*="البحث"]')
        if not inputs:
            # Some themes require clicking a search icon button to reveal the input
            # Try a common button with aria-label or class containing "search"
            try:
                btn = driver.find_element(By.CSS_SELECTOR, 'button[aria-label*="Search"],button[aria-label*="search"],a[href*="search"]')
                btn.click()
                time.sleep(0.6)
                inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="search"], input[name="q"]')
            except Exception:
                pass

        if not inputs:
            # Fallback: try pressing "/" to focus search (some themes)
            driver.find_element(By.TAG_NAME, "body").send_keys("/")
            time.sleep(0.4)
            inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="search"], input[name="q"]')

        if not inputs:
            return False

        box = inputs[0]
        box.clear()
        box.send_keys(sku)
        time.sleep(0.1)
        box.send_keys(Keys.ENTER)

        # Wait until either search results or product page loads
        time.sleep(pause)
        try:
            close_overlays(driver)
        except Exception:
            pass
        return True
    except Exception:
        return False

def open_first_product_from_results(driver, pause: float) -> bool:
    """
    After a search, click the first product card; return True if navigated.
    Handles: direct product page (when search hits exact sku) OR grid results.
    """
    time.sleep(pause)
    cur = driver.current_url

    # If we already are on a product page, keep it
    if re.search(r"/products?/", cur):
        return True

    # Otherwise pick first product card
    try:
        # Common Shopify product grid cards:
        cards = driver.find_elements(By.CSS_SELECTOR,
            'a[href*="/products/"].product-item__image-wrapper, a[href*="/products/"].full-unstyled-link, a[href*="/products/"].product-item__title, a.card-wrapper[href*="/products/"]'
        )
        if not cards:
            # broader anchor search
            cards = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/products/"]')
        if not cards:
            return False
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", cards[0])
        time.sleep(0.2)
        cards[0].click()
        time.sleep(pause)
        try:
            close_overlays(driver)
        except Exception:
            pass
        return True
    except (NoSuchElementException, ElementClickInterceptedException, TimeoutException):
        return False

def expand_description_if_collapsed(driver, pause: float):
    """
    Click "View more" if the Description is collapsed.
    From your screenshot, button has class 'expandable-content__toggle' and the text 'View more'.
    """
    try:
        # Locate toggle by class or data attributes
        btns = driver.find_elements(By.CSS_SELECTOR, "button.expandable-content__toggle, .expandable-content__toggle")
        # Also try span with data-view-more text
        if not btns:
            btns = driver.find_elements(By.XPATH, "//span[@data-view-more or contains(., 'View more')]/ancestor::button")
        for btn in btns:
            label = btn.text.strip().lower()
            if "view more" in label or "عرض المزيد" in label or label == "":
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.25)
                btn.click()
                time.sleep(pause)
                break
    except Exception:
        pass

def get_description_html_and_text(driver) -> tuple[str, str]:
    """
    Grab description HTML block (Shopify often uses .rte).
    Returns (html, text_clean).
    """
    html_str = ""
    try:
        # Preferred theme-specific container provided by user
        found = None
        prefer = driver.find_elements(By.CSS_SELECTOR, ".product-block-list__item.product-block-list__item--description")
        if prefer:
            found = prefer[0]
        else:
            # Fallback: scope within a product container to avoid site-wide newsletter text
            product_sections = driver.find_elements(By.CSS_SELECTOR, "section.product, .product, .product-single, [id*='product']")
            desc_selectors = ".rte, .product__description, .product-description, .prose, div.rte.text--pull"
            for sec in product_sections[:3]:
                try:
                    cand = sec.find_elements(By.CSS_SELECTOR, desc_selectors)
                    if cand:
                        found = cand[0]
                        break
                except Exception:
                    continue
            if not found:
                # Global fallback
                cand = driver.find_elements(By.CSS_SELECTOR, desc_selectors)
                if cand:
                    found = cand[0]
        if found:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", found)
            time.sleep(0.2)
            html_str = found.get_attribute("innerHTML") or ""
    except Exception:
        pass

    doc = None
    text = ""
    if html_str:
        try:
            doc = LH.fromstring(f"<div>{html_str}</div>")
            text = " ".join(doc.xpath(".//text()"))
        except Exception:
            pass
    text = clean_ws(text)
    return html_str, text

def get_product_images(driver) -> list[str]:
    """
    Collect product gallery images only (avoid site chrome).
    We try common Shopify selectors:
      - [data-media-id] containers
      - .product__media img
      - .product-media img
      - img[data-media-id], img[src*='/products/']
    Then convert to absolute & full-size and dedupe.
    """
    base = driver.current_url
    imgs: list[tuple[str, str]] = []  # (canonical_url, original_url)

    # STRICT: only within product gallery containers
    try:
        gallery_roots = driver.find_elements(
            By.XPATH,
            "//*[contains(@class,'product-gallery') or contains(@class,'product__media') or contains(@class,'gallery')][not(ancestor::*[contains(@class,'header') or contains(@class,'footer')]) ]"
        )
        for root in gallery_roots[:3]:
            # Images inside gallery
            for el in root.find_elements(By.CSS_SELECTOR, "img, source"):
                src = el.get_attribute("src") or el.get_attribute("data-src") or el.get_attribute("data-zoom-src")
                if not src:
                    srcset = el.get_attribute("srcset") or ""
                    if srcset:
                        last = srcset.split(",")[-1].strip().split(" ")[0]
                        src = last
                if not src:
                    continue
                if ("/products/" in src) or ("/cdn/shop/files/" in src):
                    orig = absolutize(src, base)
                    canon = to_fullsize_shopify(orig)
                    imgs.append((canon, orig))
            # Anchors that link to product images
            for a in root.find_elements(By.CSS_SELECTOR, "a[href]"):
                href = a.get_attribute("href")
                if not href:
                    continue
                if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", href, re.I) and ("/products/" in href or "/cdn/shop/files/" in href):
                    orig = absolutize(href, base)
                    canon = to_fullsize_shopify(orig)
                    imgs.append((canon, orig))
    except Exception:
        pass

    # Fallback: previous heuristics if strict search fails
    if not imgs:
        selectors = [
            "[data-media-id] img",
            ".product__media img",
            ".product-media img",
            ".media img",
            "img[src*='/products/']",
            "img[data-src*='/products/']",
        ]
        for sel in selectors:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    src = el.get_attribute("src") or el.get_attribute("data-src") or el.get_attribute("data-zoom-src")
                    if not src:
                        srcset = el.get_attribute("srcset") or ""
                        if srcset:
                            last = srcset.split(",")[-1].strip().split(" ")[0]
                            src = last
                    if src and ("/products/" in src or "/cdn/shop/files/" in src):
                        orig = absolutize(src, base)
                        canon = to_fullsize_shopify(orig)
                        imgs.append((canon, orig))
            except Exception:
                pass

        try:
            for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='/products/'], a[href*='/cdn/shop/files/']"):
                href = a.get_attribute("href")
                if href and re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", href, re.I):
                    orig = absolutize(href, base)
                    canon = to_fullsize_shopify(orig)
                    imgs.append((canon, orig))
        except Exception:
            pass

    # Validate canonical URLs; if 404, fall back to original
    final_urls = []
    seen = set()
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    for canon, orig in imgs:
        pick = canon
        if canon != orig:
            try:
                resp = requests.head(canon, timeout=4, allow_redirects=True, headers=hdrs)
                if resp.status_code >= 400:
                    # Some CDNs don't allow HEAD
                    resp = requests.get(canon, timeout=5, stream=True, headers=hdrs)
                if resp.status_code >= 400:
                    pick = orig
            except Exception:
                pick = orig
        if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", pick, re.I) and pick not in seen:
            final_urls.append(pick)
            seen.add(pick)

    return final_urls

def scrape_one(driver, sku: str, pause: float):
    """
    Return dict with fields: sku, Product_URL, Image_Src, Body_HTML, Body_Text
    Skip gracefully if no product found.
    """
    result = {
        "SKU": sku,
        "Product_URL": "",
        "Image Src": "",
        "Body (HTML)": "",
        "Body_Text": "",
        "Status": "NOT_FOUND"
    }

    try:
        open_home(driver)
        ok = search_sku(driver, sku, pause)
        if not ok:
            return result

        if not open_first_product_from_results(driver, pause):
            return result

        # we are on product page now
        result["Product_URL"] = driver.current_url

        # Expand Description if collapsed
        expand_description_if_collapsed(driver, pause)

        # Description
        html_str, text = get_description_html_and_text(driver)
        result["Body (HTML)"] = html_str
        result["Body_Text"] = text

        # Images
        imgs = get_product_images(driver)
        result["Image Src"] = ";".join(imgs)

        # If nothing meaningful, consider not found
        if not imgs and not html_str and not text:
            result["Status"] = "FOUND_EMPTY"
        else:
            result["Status"] = "OK"

        return result

    except Exception as e:
        result["Status"] = f"ERROR: {type(e).__name__}"
        return result

def main():
    p = argparse.ArgumentParser(
        description="Scrape jo-cell.com by SKU list",
        epilog=(
            "Examples:\n"
            "  python jo_cell_scraper.py --in \"Ankar.xlsx\" --sku-col \"SKU\" --out \"jo_cell_scraped.xlsx\" --headful --pause 1.0\n"
            "  python jo_cell_scraper.py --in \"skus.csv\" --sku-col \"sku\" --out \"out.csv\"\n"
            "  python jo_cell_scraper.py --in \"Ankar.xlsx\" --sheet \"Sheet1\" --sku-col \"SKU\" --out results.xlsx\n"
            "  # Sample run: process only the first product (first non-empty SKU)\n"
            "  python jo_cell_scraper.py --in \"Ankar.xlsx\" --sku-col \"SKU\" --first-only --out sample.xlsx --headful\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--in", dest="inp", required=True, help="Input Excel/CSV file with SKUs")
    p.add_argument("--sheet", default=None, help="Worksheet name if Excel")
    p.add_argument("--sku-col", default="SKU", help="Column name that contains the SKU to search")
    p.add_argument("--out", required=True, help="Output Excel/CSV file (by extension)")
    p.add_argument("--headful", action="store_true", help="Run with a visible browser")
    p.add_argument("--pause", type=float, default=1.0, help="Base sleep between steps (seconds)")
    p.add_argument("--first-only", action="store_true", help="Process only the first non-empty SKU")
    args = p.parse_args()

    inp_path = Path(args.inp)
    if not inp_path.exists():
        print(f"Input not found: {inp_path}", file=sys.stderr)
        sys.exit(2)

    # Read input
    if inp_path.suffix.lower() in [".xlsx", ".xls"]:
        # If sheet is not specified, read the first sheet only (avoid dict)
        sheet = args.sheet if args.sheet not in (None, "") else 0
        df = pd.read_excel(inp_path, sheet_name=sheet)
        # If user passed sheet=None explicitly, pandas may return a dict. Handle gracefully.
        if isinstance(df, dict):
            if args.sheet and args.sheet in df:
                df = df[args.sheet]
            else:
                first = next(iter(df.values()))
                df = first
    else:
        df = pd.read_csv(inp_path)

    if args.sku_col not in df.columns:
        raise SystemExit(f"Column '{args.sku_col}' not found. Available: {list(df.columns)}")

    skus = [str(x).strip() for x in df[args.sku_col].fillna("").tolist()]
    skus = [s for s in skus if s]
    if args.first_only and skus:
        skus = skus[:1]

    driver = setup_driver(args.headful)

    results = []
    try:
        for sku in tqdm(skus, desc="Scraping"):
            r = scrape_one(driver, sku, args.pause)
            results.append(r)
    finally:
        driver.quit()

    out_df = pd.DataFrame(results)
    # Merge back to the original DF if you want to keep other columns:
    merged = df.copy()
    # Map results by SKU
    by_sku = {r["SKU"]: r for r in results}
    merged["Product_URL"] = merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Product_URL", ""))
    merged["Image Src"]   = merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Image Src", ""))
    merged["Body (HTML)"] = merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Body (HTML)", ""))
    merged["Body_Text"]   = merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Body_Text", ""))
    merged["Scrape_Status"]= merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Status", ""))

    out_path = Path(args.out)
    if out_path.suffix.lower() in [".xlsx", ".xls"]:
        merged.to_excel(out_path, index=False)
    else:
        merged.to_csv(out_path, index=False, encoding="utf-8-sig")

    print(f"Done. Wrote: {out_path}")

if __name__ == "__main__":
    main()
