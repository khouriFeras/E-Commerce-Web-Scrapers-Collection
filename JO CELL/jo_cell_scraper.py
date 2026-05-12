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
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementClickInterceptedException
)

HOME = "https://jo-cell.com/"

def setup_driver(headful: bool):
    edge_opts = Options()
    if not headful:
        edge_opts.add_argument("--headless=new")
    edge_opts.add_argument("--no-sandbox")
    edge_opts.add_argument("--disable-gpu")
    edge_opts.add_argument("--disable-dev-shm-usage")
    edge_opts.add_argument("--window-size=1366,900")
    driver = webdriver.Edge(options=edge_opts)
    driver.set_page_load_timeout(45)
    return driver

def clean_ws(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s

def norm_sku(s: str) -> str:
    """
    Normalize SKU/model strings for comparison.
    - Uppercase
    - Remove whitespace and common separators
    - Keep only A-Z0-9 to be resilient to formatting like "ABC-123" vs "ABC 123"
    """
    s = (s or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^A-Z0-9]+", "", s)
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

def get_product_title(driver) -> str:
    """
    Get product title from .product-meta__title and h1 (e.g. .product-meta__title h1).
    Returns cleaned title string or empty if not found.
    """
    try:
        # Prefer h1 inside product-meta__title (user-specified)
        els = driver.find_elements(By.CSS_SELECTOR, ".product-meta__title h1")
        if not els:
            els = driver.find_elements(By.CSS_SELECTOR, ".product-meta__title")
        for el in els[:1]:
            text = (el.text or "").strip()
            if text:
                return clean_ws(text)
        return ""
    except Exception:
        return ""

def get_product_sku_number(driver) -> str:
    """
    Extract product SKU/model number from theme element:
      class="product-meta__sku-number"
    Returns cleaned string (may be empty).
    """
    try:
        # Wait briefly for the SKU element to appear (some themes load it after paint).
        try:
            wait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".product-meta__sku-number")))
        except Exception:
            pass

        els = driver.find_elements(By.CSS_SELECTOR, ".product-meta__sku-number")
        for el in els[:3]:
            # Try visible text first
            txt = clean_ws(el.text or "")
            if txt:
                return txt
            # Fallbacks: sometimes SKU is injected as attribute content
            for attr in ("content", "data-sku", "data-value", "value", "aria-label"):
                v = clean_ws(el.get_attribute(attr) or "")
                if v:
                    return v
        return ""
    except Exception:
        return ""


def get_price(driver) -> str:
    """
    Get product price from element with class="price" (or price--large, price__regular, etc.).
    Returns cleaned price string or empty if not found.
    """
    try:
        # Prefer main price container; Shopify often uses .price, .price--large, .price__regular
        for sel in [".price", ".price--large", ".price__regular", "[class*='price']"]:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els[:5]:
                # Skip if inside header/footer/cart
                try:
                    parent = el.find_element(By.XPATH, "./ancestor::*[contains(@class,'header') or contains(@class,'footer') or contains(@class,'cart')][1]")
                    if parent:
                        continue
                except Exception:
                    pass
                text = (el.text or "").strip()
                if text and re.search(r"[\d.,]", text):
                    return clean_ws(text)
        return ""
    except Exception:
        return ""

def scrape_one(driver, sku: str, pause: float, captcha_wait_sec: int = 0, wait_for_enter: bool = False):
    """
    Return dict with fields: sku, Product_URL, Title, Price, Image_Src, Body_HTML, Body_Text
    Skip gracefully if no product found.
    """
    result = {
        "SKU": sku,
        "Product_URL": "",
        "Website_SKU": "",
        "Title": "",
        "Price": "",
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

        # Keep a handle to the search results page so we can try multiple candidates.
        search_url = driver.current_url

        if wait_for_enter:
            input("Solve the CAPTCHA in the browser, then press Enter here to continue...")
        elif captcha_wait_sec > 0:
            time.sleep(captcha_wait_sec)

        # Collect candidate product URLs from the search results (and/or current page).
        candidate_urls: list[str] = []

        # If we already landed on a product page, treat it as first candidate.
        cur = driver.current_url
        if re.search(r"/products?/", cur):
            candidate_urls.append(cur)

        # If we are on a search results page, collect ONLY product-card links (avoid menu/footer noise).
        if not re.search(r"/products?/", cur):
            try:
                card_selectors = [
                    'a[href*="/products/"].product-item__image-wrapper',
                    'a[href*="/products/"].full-unstyled-link',
                    'a[href*="/products/"].product-item__title',
                    'a.card-wrapper[href*="/products/"]',
                ]
                links = []
                for sel in card_selectors:
                    links.extend(driver.find_elements(By.CSS_SELECTOR, sel))
                if not links:
                    # fallback: still restrict to anchors likely inside results grid
                    links = driver.find_elements(By.CSS_SELECTOR, "main a[href*='/products/'], #MainContent a[href*='/products/']")
                for a in links:
                    href = a.get_attribute("href") or ""
                    if href and href not in candidate_urls:
                        candidate_urls.append(href)
            except Exception:
                pass

        # As a fallback, use the previous behavior to click the first card, then capture URL.
        if not candidate_urls:
            if not open_first_product_from_results(driver, pause):
                return result
            if driver.current_url:
                candidate_urls.append(driver.current_url)

        # Try a few candidates until we find a matching SKU on the page.
        target = norm_sku(sku)
        matched = False
        tried = 0
        MAX_CANDIDATES = 8
        for url in candidate_urls[:MAX_CANDIDATES]:
            tried += 1
            try:
                if driver.current_url != url:
                    driver.get(url)
                    time.sleep(pause)
                    try:
                        close_overlays(driver)
                    except Exception:
                        pass
            except Exception:
                continue

            website_sku = get_product_sku_number(driver)
            result["Website_SKU"] = website_sku
            ws_norm = norm_sku(website_sku)

            # Require strong match: exact normalized match, or containment when one side is a strict subset.
            if target and ws_norm and (ws_norm == target or ws_norm in target or target in ws_norm):
                matched = True
                break

            # If we started on a product page but it doesn't match, go back to search page once
            # and keep trying other candidates if we have them.
            if tried == 1 and re.search(r"/products?/", search_url) and search_url != driver.current_url:
                # search_url itself might be product; ignore
                pass

        if not matched:
            result["Status"] = "SKU_MISMATCH_OR_NOT_FOUND"
            return result

        # we are on product page now
        result["Product_URL"] = driver.current_url
        # capture again after navigation (in case it loaded late)
        if not result.get("Website_SKU"):
            result["Website_SKU"] = get_product_sku_number(driver)

        # Title (.product-meta__title h1)
        result["Title"] = get_product_title(driver)

        # Price (class="price")
        result["Price"] = get_price(driver)

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
            "  python jo_cell_scraper.py --in \"jj.Xlsx\" --out \"jo_cell_scraped.xlsx\" --headful --pause 1.0\n"
            "  python jo_cell_scraper.py --in \"jj.Xlsx\" --sku-col \"SKU / Model Number\" --out \"jo_cell_scraped.xlsx\"\n"
            "  python jo_cell_scraper.py --in \"skus.csv\" --sku-col \"sku\" --out \"out.csv\"\n"
            "  python jo_cell_scraper.py --in \"jj.Xlsx\" --sheet \"Sheet1\" --out results.xlsx\n"
            "  # Sample run: process only the first product (first non-empty Variant SKU)\n"
            "  python jo_cell_scraper.py --in \"jj.Xlsx\" --first-only --out sample.xlsx --headful\n"
            "  # Wait for CAPTCHA: solve in browser, then press Enter to continue\n"
            "  python jo_cell_scraper.py --in \"jj.Xlsx\" --out jo_cell_scraped.xlsx --headful --wait-for-enter\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--in", dest="inp", required=True, help="Input Excel/CSV file with SKUs")
    p.add_argument("--sheet", default=None, help="Worksheet name if Excel")
    p.add_argument("--sku-col", default="Variant SKU", help="Column name that contains the SKU to search (e.g. 'Variant SKU' or 'SKU')")
    p.add_argument("--out", required=True, help="Output Excel/CSV file (by extension)")
    p.add_argument("--headful", action="store_true", help="Run with a visible browser")
    p.add_argument("--pause", type=float, default=1.0, help="Base sleep between steps (seconds)")
    p.add_argument("--first-only", action="store_true", help="Process only the first non-empty SKU")
    p.add_argument("--captcha-wait", type=int, default=0, help="Seconds to wait after each search (for solving CAPTCHA); 0 = no extra wait")
    p.add_argument("--wait-for-enter", action="store_true", help="After each search, wait for Enter in terminal before continuing (for manual CAPTCHA)")
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
            r = scrape_one(driver, sku, args.pause, captcha_wait_sec=args.captcha_wait, wait_for_enter=args.wait_for_enter)
            results.append(r)
    finally:
        driver.quit()

    out_df = pd.DataFrame(results)
    # Merge back to the original DF if you want to keep other columns:
    merged = df.copy()
    # Map results by SKU
    by_sku = {r["SKU"]: r for r in results}
    merged["Product_URL"] = merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Product_URL", ""))
    merged["Website_SKU"] = merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Website_SKU", ""))
    merged["Title"]       = merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Title", ""))
    merged["Price"]       = merged[args.sku_col].map(lambda s: by_sku.get(str(s).strip(), {}).get("Price", ""))
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
