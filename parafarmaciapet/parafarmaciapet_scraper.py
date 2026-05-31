#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import io
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

import argparse, re, time, os
from pathlib import Path
from typing import List, Optional, Set, Tuple

import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)

SEARCH_URL = "https://parafarmaciapet.com/en/?qsn={sku}"

# ---------------- helpers ----------------
def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def valid_profile_path(path: Optional[str]) -> bool:
    if not path:
        return False
    if "<YOU>" in path or "<" in path or ">" in path:
        return False
    return os.path.isdir(path)

# ---------------- webdriver ----------------
def setup_driver(headful: bool = False,
                 user_data_dir: Optional[str] = None,
                 profile_dir: Optional[str] = None) -> webdriver.Chrome:
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--lang=en-US,en")

    if valid_profile_path(user_data_dir):
        opts.add_argument(f"--user-data-dir={user_data_dir}")
        if profile_dir:
            opts.add_argument(f"--profile-directory={profile_dir}")
    else:
        if user_data_dir:
            print(f"[warn] Ignoring invalid Chrome profile path: {user_data_dir}")

    try:
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(45)
        return driver
    except WebDriverException as e:
        print(f"[FATAL] Could not start Chrome WebDriver: {e}", file=sys.stderr)
        raise

# ---------------- scraping core ----------------
def get_first_result_link(driver: webdriver.Chrome, wait: WebDriverWait) -> Optional[str]:
    """
    On the search page, cards use class 'sniperfast_product'. Grab first <a>.
    """
    try:
        card = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".sniperfast_product")))
        a = card.find_element(By.CSS_SELECTOR, "a[href]")
        href = a.get_attribute("href") or ""
        return href if href.startswith("http") else None
    except (TimeoutException, NoSuchElementException):
        return None

def collect_images(driver: webdriver.Chrome) -> List[str]:
    """
    Collect only the main full-size product image (largest one).
    """
    candidates: List[str] = []

    # Look for gallery images in known product containers
    selectors = ".images-container img, .product-images img, .slick-slide img"
    for im in driver.find_elements(By.CSS_SELECTOR, selectors):
        url = im.get_attribute("data-zoom-image") or \
              im.get_attribute("data-large_image") or \
              im.get_attribute("src") or ""
        if url:
            url = url.strip()
            if url.startswith("//"):
                url = "https:" + url
            if not any(bad in url for bad in ["placeholder", "data:image", "svg+xml"]):
                candidates.append(url)

    # Deduplicate while preserving order
    seen = set()
    unique = [x for x in candidates if not (x in seen or seen.add(x))]

    if not unique:
        return []

    # ✅ Choose the "largest" one → pick the longest URL (often includes size param)
    biggest = max(unique, key=len)
    return [biggest]

def scrape_product(driver: webdriver.Chrome, wait: WebDriverWait, pause_after_load: float = 1.0) -> Tuple[str, List[str]]:
    """
    Returns (description_text, image_urls)
    """
    # site needs a moment for DOM widgets
    time.sleep(max(0.0, pause_after_load))

    # Description (TEXT ONLY) under .product-description.cms-description
    desc_text = ""
    try:
        desc_el = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, ".product-description.cms-description")
        ))
        desc_text = clean_ws(desc_el.text)
    except TimeoutException:
        desc_text = ""

    # Images
    images = collect_images(driver)
    return desc_text, images

def process_sku(driver: webdriver.Chrome, sku: str, pause_after_load: float, timeout: int) -> dict:
    sku = str(sku).strip()
    if not sku or sku.lower() in ("nan", "none"):
        return {"Status": "skip: empty sku"}

    search_url = SEARCH_URL.format(sku=sku)
    try:
        driver.get(search_url)
    except Exception as e:
        return {"Status": f"error: load search ({e})"}

    wait = WebDriverWait(driver, timeout)

    href = get_first_result_link(driver, wait)
    if not href:
        return {"Status": "not_found", "Source_URL": search_url}

    try:
        driver.get(href)
        # wait for any of: desc, images, or a title-ish element
        WebDriverWait(driver, timeout).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-description.cms-description")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".images-container img, .product-images img, .slick-slide img")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1, .product-title, .page-title"))
            )
        )
    except TimeoutException:
        pass
    except Exception as e:
        return {"Status": f"error: load product ({e})", "Source_URL": href}

    desc_text, images = scrape_product(driver, wait, pause_after_load=pause_after_load)

    return {
        "Status": "ok" if (desc_text or images) else "ok:empty_fields",
        "Source_URL": href,
        # NOTE: per your request we keep the column name as 'Body (HTML)' but put CLEAN TEXT inside.
        "Body (HTML)": desc_text,
        "Image Src": ";".join(images) if images else "",
    }

# ---------------- CLI ----------------
def main():
    import argparse, sys
    from pathlib import Path

    p = argparse.ArgumentParser(description="Scrape description & images from parafarmaciapet.com by SKU search.")
    p.add_argument("--in", dest="inp", required=True, help="Input Excel/CSV file")
    p.add_argument("--out", dest="out", required=True, help="Output Excel/CSV file (created/overwritten)")
    p.add_argument("--sheet", default=None, help="Worksheet name for Excel")
    p.add_argument("--sku-col", default="رقم الباركود", help="Column containing SKU (default: رقم الباركود)")
    p.add_argument("--headful", action="store_true", help="Run Chrome with UI")
    p.add_argument("--profile", dest="profile", default=None, help="Chrome user-data-dir (optional)")
    p.add_argument("--profile-dir", dest="profile_dir", default=None, help="Chrome profile directory name (e.g., Default)")
    p.add_argument("--pause", type=float, default=1.0, help="Seconds to wait after product page load (default: 1.0)")
    p.add_argument("--timeout", type=int, default=20, help="Explicit wait timeout seconds (default: 20)")
    p.add_argument("--sample", type=int, default=None, help="Process only the first N rows (testing)")
    args = p.parse_args()

    inp = Path(args.inp)
    if not inp.exists():
        print(f"[FATAL] Input file not found: {inp}", file=sys.stderr)
        sys.exit(2)

    # Load data
    if inp.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(inp, sheet_name=args.sheet)
    else:
        df = pd.read_csv(inp)

    if args.sku_col not in df.columns:
        print(f"[FATAL] Column not found: {args.sku_col}", file=sys.stderr)
        sys.exit(2)

    if args.sample:
        df = df.iloc[:args.sample].copy()

    # Ensure output columns exist (we'll write only for found products)
    for col in ["Body (HTML)", "Image Src", "Source_URL", "Status"]:
        if col not in df.columns:
            df[col] = ""

    driver = setup_driver(headful=args.headful, user_data_dir=args.profile, profile_dir=args.profile_dir)

    try:
        skus = df[args.sku_col].tolist()
        total = len(skus)
        for i, sku in enumerate(skus, start=1):
            print(f"[{i}/{total}] SKU={sku!r}")
            try:
                res = process_sku(driver, sku, pause_after_load=args.pause, timeout=args.timeout)
            except Exception as e:
                res = {"Status": f"error: {e}"}

            # ❌ Skip writing if product not found (leave row untouched)
            if res.get("Status") == "not_found":
                print("  -> skipped (not found)")
                continue

            # ✅ Write results into the same row
            row_idx = df.index[i - 1]
            for k, v in res.items():
                if k not in df.columns:
                    df[k] = ""
                df.at[row_idx, k] = v
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    outp = Path(args.out)
    if outp.suffix.lower() in (".xlsx", ".xls"):
        df.to_excel(outp, index=False)
    else:
        df.to_csv(outp, index=False, encoding="utf-8-sig")

    print(f"Done. Wrote: {outp}")

if __name__ == "__main__":
    main()
