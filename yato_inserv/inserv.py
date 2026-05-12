#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INSERV.LV SKU scraper: reads SKUs from Excel, searches inserv.lv, clicks first
product, scrapes images (full-size, semicolon-separated) and text from product page.

If you get "Access denied": pip install undetected-chromedriver
Then run again (USE_UNDETECTED is True by default).
"""

import sys
import io
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ------------------------- Config -------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_EXCEL = SCRIPT_DIR / "yato.xlsx"
SKU_COL = "SKU"  # Override or auto-detect if not present
SEARCH_URL = "https://www.inserv.lv/en/products/search?q={sku}"
BASE_URL = "https://www.inserv.lv"
HEADLESS = False
TIMEOUT = 30
# Use undetected-chromedriver to avoid "Access denied" (pip install undetected-chromedriver)
USE_UNDETECTED = True
# Human-like delays (seconds): randomized so the site doesn't see a fixed pattern
PAGE_LOAD_WAIT_MIN = 1.5
PAGE_LOAD_WAIT_MAX = 3.5
BETWEEN_PRODUCTS_MIN = 3.0
BETWEEN_PRODUCTS_MAX = 6.0
BETWEEN_CLICK_MIN = 0.8
BETWEEN_CLICK_MAX = 2.0
TEST_MODE = False   # Set to False for full run
TEST_LIMIT = 5
OUTPUT_EXCEL = SCRIPT_DIR / "inserv_scraped_test.xlsx" if TEST_MODE else SCRIPT_DIR / "inserv_scraped.xlsx"
# Realistic Chrome User-Agent (update version if needed)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
# -------------------------


def make_driver():
    """Create Chrome driver. Prefer undetected_chromedriver to avoid access denied."""
    if USE_UNDETECTED:
        try:
            import undetected_chromedriver as uc
            opts = uc.ChromeOptions()
            if HEADLESS:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--window-size=1366,900")
            opts.add_argument(f"--user-agent={USER_AGENT}")
            d = uc.Chrome(options=opts, use_subprocess=True)
            d.set_page_load_timeout(45)
            return d
        except ImportError:
            print("Note: pip install undetected-chromedriver to reduce 'Access denied'. Using standard Chrome.")
        except Exception as e:
            print(f"Note: undetected_chromedriver failed ({e}). Using standard Chrome.")

    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument(f"--user-agent={USER_AGENT}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-infobars")
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--log-level=3")
    d = webdriver.Chrome(options=opts)
    d.set_page_load_timeout(45)
    # Reduce automation fingerprint (some sites check navigator.webdriver)
    try:
        d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })
    except Exception:
        pass
    return d


def clean_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s).strip()


def to_full_size_url(url: str) -> str:
    """Convert thumbnail/small image URL to full-size. INSERV uses /small/ vs /large/ in path."""
    if not url:
        return url
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("/"):
        u = urljoin(BASE_URL, u)
    # INSERV: /storage/products/small/ -> /large/, /gallery/small/ -> /gallery/large/
    u = u.replace("/small/", "/large/")
    u = re.sub(r'[?&](w|width|h|height|size|resize|scale|fit|quality)=\d+', '', u)
    u = re.sub(r'_(\d+)x(\d+)\.(jpg|jpeg|png|webp|gif)', r'.\3', u, flags=re.IGNORECASE)
    u = re.sub(r'/\d+x\d+/', '/', u)
    return u


def human_sleep(min_sec: float, max_sec: float) -> None:
    """Sleep for a random duration to mimic human behavior."""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)


def human_scroll(driver) -> None:
    """Scroll the page a bit like a human reading."""
    try:
        total_height = driver.execute_script("return document.body.scrollHeight")
        if total_height > 800:
            scroll = random.randint(200, min(500, total_height // 3))
            driver.execute_script(f"window.scrollBy(0, {scroll});")
            time.sleep(random.uniform(0.3, 0.8))
    except Exception:
        pass


def close_overlays(driver):
    """Close cookie/popup overlays if present."""
    selectors = [
        "button[aria-label*='close' i]",
        "button[aria-label*='accept' i]",
        "button[aria-label*='dismiss' i]",
        ".cookie-consent button",
        "[class*='cookie'] button",
        "[class*='popup'] button",
        ".modal button.close",
    ]
    for sel in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel)[:3]:
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(0.2)
        except Exception:
            continue


def find_sku_column(df: pd.DataFrame) -> str:
    if SKU_COL and str(SKU_COL).strip() in df.columns:
        return SKU_COL
    for c in df.columns:
        if "sku" in str(c).lower():
            return c
    for candidate in ["Article", "Code", "Product Code", "Item"]:
        if candidate in df.columns:
            return candidate
    return df.columns[0]


def scrape_product(driver, wait, sku: str) -> dict:
    """Search for SKU, click first product, scrape images and text. Returns dict with SKU, Images, Description, Status."""
    result = {"SKU": sku, "Images": "", "Description": "", "Status": ""}
    try:
        url = SEARCH_URL.format(sku=sku)
        driver.get(url)
        human_sleep(PAGE_LOAD_WAIT_MIN, PAGE_LOAD_WAIT_MAX)
        close_overlays(driver)
        human_sleep(0.3, 0.7)

        # Simulate looking at search results before clicking
        human_scroll(driver)
        human_sleep(BETWEEN_CLICK_MIN, BETWEEN_CLICK_MAX)

        # Click first product link
        product_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/en/products/"]')
        first_link = None
        for a in product_links:
            href = (a.get_attribute("href") or "")
            if re.search(r"/en/products/\d+/", href):
                first_link = a
                break
        if not first_link:
            result["Status"] = "NOT_FOUND"
            return result

        first_link.click()
        human_sleep(PAGE_LOAD_WAIT_MIN, PAGE_LOAD_WAIT_MAX)
        human_scroll(driver)
        human_sleep(0.4, 1.0)

        # Click expand chevron to show full description (class="fa fa-chevron-up show-when-group1-active")
        try:
            for sel in [
                ".fa.fa-chevron-up.show-when-group1-active",
                "[class*='chevron-up'][class*='show-when-group1-active']",
                "i.fa-chevron-up.show-when-group1-active",
                ".show-when-group1-active.fa-chevron-up",
            ]:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, sel)
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        human_sleep(0.5, 1.2)
                        break
                except Exception:
                    continue
        except Exception:
            pass

        # Images: from gallery container and anywhere on page; prefer data-full / href to full image
        seen = set()
        img_urls = []
        def add_image_url(u):
            if not u:
                return
            u = u.split("#")[0].strip()
            if u.startswith("//"):
                u = "https:" + u
            elif u.startswith("/"):
                u = urljoin(BASE_URL, u)
            u = to_full_size_url(u)
            if u and u not in seen and "data:image" not in u.lower():
                seen.add(u)
                img_urls.append(u)

        for selector in [
            ".flex.gap-2.flex-wrap.mb-2 img",
            "[class*='flex'][class*='gap-2'][class*='mb-2'] img",
            "div.flex.gap-2.flex-wrap.mb-2 img",
            "img[src*='inserv.lv/storage/products']",
            "img[src*='/storage/products/']",
        ]:
            try:
                imgs = driver.find_elements(By.CSS_SELECTOR, selector)
                for img in imgs:
                    u = (
                        img.get_attribute("data-full")
                        or img.get_attribute("data-zoom-src")
                        or img.get_attribute("data-src")
                        or img.get_attribute("src")
                        or img.get_attribute("data-original")
                    )
                    add_image_url(u)
                    # srcset often has larger sizes (e.g. "url 1x, url2 2x"); take last or largest
                    srcset = img.get_attribute("srcset") or ""
                    if srcset:
                        for part in srcset.split(","):
                            part = part.strip().split(None, 1)[0]
                            add_image_url(part)
                if img_urls:
                    break
            except Exception:
                continue
        # Also try links (a href) in gallery that point to full-size images
        try:
            for a in driver.find_elements(By.CSS_SELECTOR, ".flex.gap-2.flex-wrap.mb-2 a[href*='.jpg'], .flex.gap-2.flex-wrap.mb-2 a[href*='.jpeg'], .flex.gap-2.flex-wrap.mb-2 a[href*='.png']"):
                u = a.get_attribute("href")
                if u:
                    u = to_full_size_url(u)
                    if u and u not in seen:
                        seen.add(u)
                        img_urls.append(u)
        except Exception:
            pass
        result["Images"] = "; ".join(img_urls)

        # Description: only from shadow-none border-none pl-2 and table table--striped table--cozy
        desc_parts = []
        try:
            # 1) Elements with class="shadow-none border-none pl-2"
            for sel in [
                ".shadow-none.border-none.pl-2",
                "[class*='shadow-none'][class*='border-none'][class*='pl-2']",
            ]:
                try:
                    for el in driver.find_elements(By.CSS_SELECTOR, sel):
                        t = (el.text or "").strip()
                        if t:
                            desc_parts.append(clean_text(t))
                    if desc_parts:
                        break
                except Exception:
                    continue
            # 2) Table with class="table table--striped table--cozy"
            for sel in [
                "table.table.table--striped.table--cozy",
                "table.table--striped.table--cozy",
                "[class*='table'][class*='table--striped'][class*='table--cozy']",
            ]:
                try:
                    for tbl in driver.find_elements(By.CSS_SELECTOR, sel):
                        t = (tbl.text or "").strip()
                        if t and t not in desc_parts:
                            desc_parts.append(clean_text(t))
                    break
                except Exception:
                    continue
        except Exception:
            pass
        result["Description"] = "\n\n".join(desc_parts) if desc_parts else ""

        result["Status"] = "SUCCESS" if (result["Images"] or result["Description"]) else "NO_DATA"
    except Exception as e:
        result["Status"] = f"ERROR: {str(e)}"
    return result


def main():
    input_path = Path(INPUT_EXCEL)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return

    try:
        df = pd.read_excel(input_path, engine="openpyxl")
    except Exception as e:
        print(f"Error reading Excel: {e}")
        return

    if df.empty:
        print("Error: Excel file is empty.")
        return

    sku_col = find_sku_column(df)
    print(f"Using SKU column: '{sku_col}'")

    if TEST_MODE:
        df = df.head(TEST_LIMIT).copy()
        print(f"TEST MODE: Processing only first {TEST_LIMIT} items")

    driver = make_driver()
    wait = WebDriverWait(driver, TIMEOUT)
    results = []

    try:
        # Warm-up: visit homepage first so the first request looks like a normal user (reduces access denied)
        print("Opening site homepage...")
        driver.get(BASE_URL + "/en")
        human_sleep(2.0, 4.0)
        close_overlays(driver)
        human_sleep(1.0, 2.0)

        skus = df[sku_col].astype(str).fillna("").tolist()
        total = len(skus)
        for idx, sku in enumerate(skus, 1):
            sku = str(sku).strip()
            if not sku or sku.lower() in ("nan", "none", ""):
                print(f"[{idx}/{total}] Skipping empty SKU")
                results.append({"SKU": sku, "Images": "", "Description": "", "Status": "EMPTY_SKU"})
                continue
            print(f"[{idx}/{total}] Processing SKU: {sku}")
            result = scrape_product(driver, wait, sku)
            results.append(result)
            print(f"    -> {result['Status']} | Images: {len(result['Images'].split(';')) if result['Images'] else 0}")
            human_sleep(BETWEEN_PRODUCTS_MIN, BETWEEN_PRODUCTS_MAX)
    finally:
        driver.quit()

    results_df = pd.DataFrame(results)
    try:
        output_df = df.merge(results_df, left_on=sku_col, right_on="SKU", how="left", suffixes=("", "_scraped"))
        # Keep one key column: if original had different name, drop the right-side "SKU"
        if "SKU_scraped" in output_df.columns:
            output_df = output_df.drop(columns=["SKU_scraped"])
        if sku_col != "SKU" and "SKU" in output_df.columns:
            output_df = output_df.drop(columns=["SKU"], errors="ignore")
    except Exception:
        output_df = pd.concat([df, results_df], axis=1)
    if len(output_df) == 0:
        output_df = results_df

    output_path = Path(OUTPUT_EXCEL)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_excel(output_path, index=False, engine="openpyxl")
    print(f"\nDone! Results saved to: {output_path}")


if __name__ == "__main__":
    main()
