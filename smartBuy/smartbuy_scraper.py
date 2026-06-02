#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, sys, time, re, logging
from pathlib import Path
from typing import List, Optional, Set
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    WebDriverException, StaleElementReferenceException,
)

log = logging.getLogger("smartbuy")
log.setLevel(logging.INFO)
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
log.addHandler(_h)

SEARCH_URL = "https://smartbuy-me.com/search?type=product&q={query}"


# ─── utilities ────────────────────────────────────────────────────────────────

def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def shopify_full_size(url: str) -> str:
    """Strip Shopify CDN size/crop suffix (e.g. _800x600, _1024x_crop_center) to get the master image."""
    if not url:
        return url
    url = re.sub(r"_\d+x\d*(_crop_\w+)?(?=\.\w{2,4}(\?|$))", "", url)
    return url


def _normalise_img_url(url: str) -> Optional[str]:
    """Return a normalised Shopify CDN master-image URL, or None if unusable."""
    if not url or url.startswith("data:"):
        return None
    if url.startswith("//"):
        url = "https:" + url
    url = shopify_full_size(url)
    url = re.sub(r"[&?]width=\d+", "", url)
    url = re.sub(r"[&?]height=\d+", "", url)
    url = url.rstrip("?&")
    return url or None

def parse_srcset(srcset: str) -> List[str]:
    if not srcset:
        return []
    urls = []
    for part in srcset.split(","):
        token = part.strip().split()[0]
        if token:
            urls.append(token)
    return urls

def pick_best_img_url(img_el) -> Optional[str]:
    """Return the highest-res URL from a Selenium img element."""
    # data-zoom attributes carry the full-size URL on many Shopify themes
    for attr in ("data-zoom-image", "data-zoom", "data-large-image", "data-full-size"):
        val = img_el.get_attribute(attr)
        if val and not val.startswith("data:"):
            return shopify_full_size(val)
    # srcset: last entry is largest
    for attr in ("data-srcset", "srcset"):
        val = img_el.get_attribute(attr)
        if val:
            cands = parse_srcset(val)
            if cands:
                return shopify_full_size(cands[-1])
    # fallback
    for attr in ("data-src", "src"):
        val = img_el.get_attribute(attr)
        if val and not val.startswith("data:"):
            return shopify_full_size(val)
    return None


# ─── driver ───────────────────────────────────────────────────────────────────

def chrome_driver(headful: bool) -> webdriver.Chrome:
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver


# ─── site flows ───────────────────────────────────────────────────────────────

def search_and_open_first(driver, barcode: str, timeout: int) -> bool:
    """Navigate to the search URL for barcode and click the first product."""
    driver.get(SEARCH_URL.format(query=barcode))
    wait = WebDriverWait(driver, timeout)

    try:
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(0.4)
    except TimeoutException:
        return False

    for sel in (
        "a[href*='/products/']",
        "a.product-card__link",
        "a.full-unstyled-link",
        ".grid__item a",
        ".product-list a",
    ):
        try:
            links = [l for l in driver.find_elements(By.CSS_SELECTOR, sel) if l.is_displayed()]
            if links:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", links[0])
                links[0].click()
                return True
        except Exception:
            continue
    return False


def verify_sku(driver, barcode: str, timeout: int) -> bool:
    """Return True if product page SKU contains the barcode."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except TimeoutException:
        return False

    clean_bc = re.sub(r"\D", "", str(barcode))

    for sel in (".product-meta__sku-number", "[class*='sku-number']", "[class*='sku_number']"):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                page_sku = re.sub(r"\D", "", norm_ws(el.text))
                if page_sku and clean_bc and page_sku == clean_bc:
                    return True
                if page_sku:
                    log.warning(f"  SKU mismatch: expected {clean_bc}, got '{page_sku}'")
        except Exception:
            continue
    return False


def collect_images(driver) -> List[str]:
    """Return full-size product image URLs using thumbnail anchors and main gallery imgs."""
    urls: List[str] = []
    seen: Set[str] = set()

    def add(url: str) -> None:
        u = _normalise_img_url(url)
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    # ── strategy 1: thumbnail anchors ────────────────────────────────────────
    thumbs = driver.find_elements(By.CSS_SELECTOR, ".scroller a.product-gallery__thumbnail")
    if not thumbs:
        thumbs = driver.find_elements(By.CSS_SELECTOR, "a.product-gallery__thumbnail")

    if not thumbs:
        log.warning("  No .product-gallery__thumbnail anchors found")

    for a in thumbs:
        try:
            # href is often a direct CDN link on this Shopify theme
            href = (a.get_attribute("href") or "").strip()
            if href:
                add(href)
            # img inside anchor carries srcset / data-zoom with higher-res URLs
            for img in a.find_elements(By.TAG_NAME, "img"):
                u = pick_best_img_url(img)
                if u:
                    add(u)
        except StaleElementReferenceException:
            continue

    # ── strategy 2: main gallery imgs (data-zoom-image / srcset) ─────────────
    for sel in (
        ".product-gallery__main img",
        ".product-gallery__media img",
        ".product-gallery__slide img",
        ".product__media img",
        ".product-single__photo img",
    ):
        for img in driver.find_elements(By.CSS_SELECTOR, sel):
            try:
                u = pick_best_img_url(img)
                if u:
                    add(u)
            except StaleElementReferenceException:
                continue

    log.debug(f"  product images found: {len(urls)}")
    return urls


def collect_description(driver) -> str:
    """Return concatenated text from .card__section elements."""
    parts = []
    seen: Set[str] = set()

    for sel in (".card__section", ".product-description", ".product__description",
                "[class*='product-desc']", "[class*='product_desc']"):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                txt = norm_ws(el.text)
                key = txt.lower()
                if txt and len(txt) > 15 and key not in seen:
                    seen.add(key)
                    parts.append(txt)
            if parts:
                break
        except Exception:
            continue

    return "\n\n".join(parts)


# ─── per-item orchestrator ────────────────────────────────────────────────────

def scrape_one(driver, barcode: str, timeout: int) -> dict:
    result = {
        "found": False,
        "sku_matched": False,
        "image_count": 0,
        "image_urls": "",
        "description": "",
        "source_url": "",
    }

    if not search_and_open_first(driver, barcode, timeout):
        log.warning(f"  No search results for {barcode}")
        return result

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, "h1"))
        )
        time.sleep(0.5)
    except TimeoutException:
        pass

    result["found"] = True
    result["source_url"] = driver.current_url
    result["sku_matched"] = verify_sku(driver, barcode, timeout)

    if not result["sku_matched"]:
        log.warning(f"  {barcode}: SKU did not match — data still collected")

    imgs = collect_images(driver)
    result["image_count"] = len(imgs)
    result["image_urls"] = ";".join(imgs)
    result["description"] = collect_description(driver)

    log.info(f"  sku_ok={result['sku_matched']}  imgs={len(imgs)}  desc_len={len(result['description'])}")
    return result


# ─── I/O helpers ─────────────────────────────────────────────────────────────

def _load_df(inp_path: str) -> pd.DataFrame:
    """Load the GGuard price-list Excel (real header is on row index 1)."""
    df = pd.read_excel(inp_path, header=1)
    return df


def _save(df: pd.DataFrame, out_path: str) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if out_path.lower().endswith((".xlsx", ".xls")):
        df.to_excel(out_path, index=False)
    else:
        df.to_csv(out_path, index=False, encoding="utf-8-sig")


# ─── main run ─────────────────────────────────────────────────────────────────

def run(args) -> None:
    df = _load_df(args.inp)

    # Resolve barcode column
    bc_col = args.barcode_col
    if bc_col not in df.columns:
        # Auto-detect: first column whose values look like 12–14 digit EAN codes
        for col in df.columns:
            sample = df[col].dropna().astype(str).head(8)
            if sample.str.match(r"^\d{12,14}$").any():
                bc_col = col
                log.info(f"Auto-detected barcode column: '{col}'")
                break

    if bc_col not in df.columns:
        raise SystemExit(f"Barcode column '{bc_col}' not found. Available: {list(df.columns)}")

    for col in ("Image Src", "Description_SB", "Source_URL_SB", "SKU_Matched"):
        if col not in df.columns:
            df[col] = ""

    driver = chrome_driver(args.headful)
    try:
        subset = df.head(args.limit) if args.limit > 0 else df
        total = len(subset)

        for idx, row in subset.iterrows():
            barcode = str(row[bc_col]).strip()
            if not barcode or barcode.lower() in ("nan", "none", ""):
                continue

            # Resume: skip already-scraped rows unless --redo
            existing_url = str(row.get("Source_URL_SB", "") or "")
            if not args.redo and existing_url.startswith("http"):
                log.info(f"[{idx}/{total}] {barcode} — already scraped, skipping")
                continue

            log.info(f"[{idx}/{total}] {barcode}")
            try:
                r = scrape_one(driver, barcode, args.timeout)
                df.at[idx, "Image Src"]      = r["image_urls"]
                df.at[idx, "Description_SB"] = r["description"]
                df.at[idx, "Source_URL_SB"]  = r["source_url"]
                df.at[idx, "SKU_Matched"]    = r["sku_matched"]
            except Exception as exc:
                log.warning(f"[{idx}] Error scraping {barcode}: {exc}")
                continue

            if args.checkpoint_every and (idx + 1) % args.checkpoint_every == 0:
                _save(df, args.out)
                log.info(f"Checkpoint saved @ row {idx + 1}")

            time.sleep(max(0.0, args.delay))

        _save(df, args.out)
        log.info(f"Done. {total} rows written → {args.out}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def main() -> None:
    p = argparse.ArgumentParser(
        description="Scrape SmartBuy ME by barcode: verify SKU, collect images + description."
    )
    p.add_argument("--in",  dest="inp",  required=True, help="Input .xlsx (GGuard price list)")
    p.add_argument("--out", dest="out",  required=True, help="Output .xlsx")
    p.add_argument("--barcode-col", default="Unnamed: 1",
                   help="Column name that holds the EAN barcodes (default: 'Unnamed: 1')")
    p.add_argument("--headful",  action="store_true", help="Show browser window")
    p.add_argument("--delay",    type=float, default=1.0,  help="Seconds between items")
    p.add_argument("--timeout",  type=int,   default=20,   help="Per-page timeout (s)")
    p.add_argument("--checkpoint-every", type=int, default=10,
                   help="Auto-save every N rows (0 = only at end)")
    p.add_argument("--redo",  action="store_true", help="Re-scrape rows that already have a URL")
    p.add_argument("--limit", type=int, default=0, help="Cap at first N rows (0 = all)")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
