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

import argparse
import re
import time
from typing import List, Optional, Set

import pandas as pd
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options


HOME = "https://doublem-jo.com/"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape doublem-jo.com by SKU: search → first product → gallery images + وصف المنتج (plain text)."
    )
    p.add_argument("--in", dest="inp", required=True, help="Input Excel file path")
    p.add_argument("--out", dest="out", required=True, help="Output Excel file path")
    p.add_argument("--sheet", default=None, help="Worksheet name (default: first sheet)")
    p.add_argument("--header-row", type=int, default=0, help="Header row index (0-based, default=0)")
    p.add_argument("--sku-col", dest="sku_col", required=True, help="Column name containing SKUs")
    p.add_argument("--pause", type=float, default=1.0, help="Extra sleep after page loads (sec)")
    p.add_argument("--headful", action="store_true", help="Show browser (default = headless)")
    p.add_argument("--sample", type=int, default=0, help="Only process the first N rows (0 = all)")
    return p.parse_args()


def build_driver(headful: bool) -> webdriver.Chrome:
    chrome_opts = Options()
    if not headful:
        chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--window-size=1400,1000")
    chrome_opts.add_argument("--lang=ar-JO")
    chrome_opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    
    # Try webdriver_manager first, fallback to system ChromeDriver
    try:
        service = ChromeService(ChromeDriverManager().install())
        print("✓ Using webdriver_manager ChromeDriver")
    except Exception as e:
        print(f"webdriver_manager failed: {e}")
        print("Trying system ChromeDriver...")
        service = ChromeService()
    
    return webdriver.Chrome(service=service, options=chrome_opts)


def wait(driver, timeout: float = 12.0) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def safe_click(driver, el):
    try:
        driver.execute_script("arguments[0].click();", el)
    except WebDriverException:
        el.click()


def try_dismiss_popups(driver):
    selectors = [
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "button[class*='close']",
        "button[aria-label*='إغلاق']",
        "button[title*='Close']",
        "button[aria-label*='close']",
    ]
    for sel in selectors:
        try:
            for e in driver.find_elements(By.CSS_SELECTOR, sel):
                if e.is_displayed():
                    safe_click(driver, e)
                    time.sleep(0.2)
        except Exception:
            pass


def ensure_https(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


# Strip Shopify size suffixes (_800x, _large, etc.) before the extension.
SIZE_PAT = re.compile(
    r'_(\d+x\d+|\d+x|pico|icon|thumb|small|compact|medium|large|grande|1024x1024|2048x2048)(?=\.(?:jpe?g|png|webp|gif))(?!\w)',
    re.IGNORECASE,
)

def to_original_shopify(url: str) -> str:
    url = ensure_https(url)
    # Remove typical query size params
    url = re.sub(r'([?&])(width|height|crop|format|auto)=[^&]+', r'\1', url)
    url = re.sub(r'[?&]+$', '', url)
    # Remove suffixes
    url = re.sub(SIZE_PAT, '', url)
    url = re.sub(r'\?&', '?', url)
    return url


def pick_largest_from_srcset(srcset: str) -> Optional[str]:
    if not srcset:
        return None
    best_url = None
    best_w = -1
    for part in [p.strip() for p in srcset.split(",") if p.strip()]:
        m = re.match(r'(.+)\s+(\d+)w$', part)
        if m:
            u, w = m.group(1).strip(), int(m.group(2))
            if w > best_w:
                best_w, best_url = w, u
        else:
            best_url = part.split()[0]
    return ensure_https(best_url) if best_url else None


def open_home_and_search(driver, sku: str, pause: float) -> bool:
    driver.get(HOME)
    try_dismiss_popups(driver)
    try:
        search_input = None

        # Try visible search input
        for e in driver.find_elements(By.CSS_SELECTOR, "input[name='q'], input[type='search']"):
            if e.is_displayed():
                search_input = e
                break

        # If not visible, click search trigger then try again
        if not search_input:
            triggers = driver.find_elements(
                By.CSS_SELECTOR,
                "summary.header__icon--search, button.header__icon--search, "
                "summary[aria-controls='Search'], button[aria-controls='Search'], "
                "button[aria-label*='بحث'], summary[aria-label*='بحث']"
            )
            for t in triggers:
                if t.is_displayed():
                    safe_click(driver, t)
                    time.sleep(0.3)
                    for e in driver.find_elements(By.CSS_SELECTOR, "input[name='q'], input[type='search']"):
                        if e.is_displayed():
                            search_input = e
                            break
                if search_input:
                    break

        if not search_input:
            # Fallback: direct search results URL
            driver.get(f"{HOME}search?type=product&q={sku}")
        else:
            search_input.clear()
            search_input.send_keys(sku)
            search_input.send_keys(Keys.ENTER)

        wait(driver).until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/products/']")))
        time.sleep(pause)
        try_dismiss_popups(driver)
        return True
    except TimeoutException:
        return False


def click_first_result(driver, pause: float) -> bool:
    try:
        for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='/products/']"):
            if a.is_displayed():
                safe_click(driver, a)
                wait(driver).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
                time.sleep(pause)
                try_dismiss_popups(driver)
                return True
        return False
    except TimeoutException:
        return False


def _scroll_thumbnail_scroller(driver):
    # Scroll the horizontal thumbnail list to force lazy-load
    try:
        scroller = driver.find_element(By.CSS_SELECTOR, ".scroller__inner")
        driver.execute_script("""
            const el = arguments[0];
            const step = Math.max(200, el.clientWidth);
            const total = el.scrollWidth;
            function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
            (async () => {
              for (let x = 0; x <= total + step; x += step) {
                el.scrollTo({left: x, behavior: 'instant'});
                await sleep(120);
              }
              el.scrollTo({left: 0, behavior: 'instant'});
            })();
        """, scroller)
        time.sleep(0.4)
    except Exception:
        pass


def scrape_images(driver) -> List[str]:
    """
    ONLY images inside the .product-gallery__thumbnail-list.
    Try multiple selectors to find the thumbnail gallery.
    """
    urls: List[str] = []
    seen: Set[str] = set()

    def add_url(u: Optional[str]):
        if not u:
            return
        if "/cdn/shop/" not in u:
            return
        if ("/products/" not in u) and ("/files/" not in u):
            return
        u = to_original_shopify(u)
        if u not in seen:
            seen.add(u)
            urls.append(u)

    # Try multiple selectors to find the thumbnail gallery
    gallery_selectors = [
        ".product-gallery__thumbnail-list",  # Primary target
        ".product-gallery__thumbnail",       # Alternative
        ".product-gallery",                  # Broader gallery
        ".scroller",                         # Current working selector
        "[class*='gallery'][class*='thumbnail']",  # Any element with both gallery and thumbnail
        "[class*='thumbnail']"               # Any thumbnail element
    ]

    scope = None
    for selector in gallery_selectors:
        try:
            scope = driver.find_element(By.CSS_SELECTOR, selector)
            print(f"Found gallery using selector: {selector}")
            break
        except NoSuchElementException:
            continue

    if not scope:
        print("No gallery found with any selector")
        return urls

    try:
        _scroll_thumbnail_scroller(driver)

        # <img> entries
        for img in scope.find_elements(By.CSS_SELECTOR, "img"):
            try:
                srcset = img.get_attribute("srcset") or ""
                src = img.get_attribute("src") or ""
                add_url(pick_largest_from_srcset(srcset) if srcset else src)
            except Exception:
                continue

        # <source> inside <picture>
        for s in scope.find_elements(By.CSS_SELECTOR, "source[srcset]"):
            try:
                add_url(pick_largest_from_srcset(s.get_attribute("srcset") or ""))
            except Exception:
                continue

        # Links (sometimes thumbnails are anchors to full-size)
        for a in scope.find_elements(By.CSS_SELECTOR, "a[href]"):
            try:
                add_url(a.get_attribute("href") or "")
            except Exception:
                continue

    except Exception as e:
        print(f"Error processing gallery: {e}")

    return urls


def extract_description_text(driver) -> str:
    """
    Extract 'وصف المنتج' section and RETURN **PLAIN TEXT** (no HTML tags).
    Fallback to generic product description if needed.
    """
    # 1) <details><summary>وصف المنتج</summary>...</details>
    try:
        details = driver.find_elements(By.XPATH, "//details[.//summary[contains(., 'وصف المنتج')]]")
        if details:
            d = details[0]
            if not d.get_attribute("open"):
                try:
                    summary = d.find_element(By.TAG_NAME, "summary")
                    safe_click(driver, summary)
                    time.sleep(0.2)
                except Exception:
                    pass
            for sel in [".product__accordion-content", ".accordion__content", "[class*='content']"]:
                try:
                    cont = d.find_element(By.CSS_SELECTOR, sel)
                    html = cont.get_attribute("innerHTML") or ""
                    txt = html_to_text(html)
                    if txt:
                        return txt
                except NoSuchElementException:
                    continue
    except Exception:
        pass

    # 2) Heading then next sibling
    try:
        node = driver.find_element(
            By.XPATH,
            "//*[self::h1 or self::h2 or self::h3 or self::h4][contains(., 'وصف المنتج')]/following-sibling::*[1]"
        )
        html = node.get_attribute("innerHTML") or node.get_attribute("outerHTML") or ""
        txt = html_to_text(html)
        if txt:
            return txt
    except Exception:
        pass

    # 3) Generic Shopify description blocks
    for sel in [".product__description", ".product-single__description", "[class*='rte']"]:
        try:
            cont = driver.find_element(By.CSS_SELECTOR, sel)
            html = cont.get_attribute("innerHTML") or cont.get_attribute("outerHTML") or ""
            txt = html_to_text(html)
            if txt:
                return txt
        except NoSuchElementException:
            continue

    return ""


def html_to_text(html: str) -> str:
    """
    Convert HTML to clean plain text:
    - strips tags
    - preserves basic line breaks for <p>, <li>, <br>, headings
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")

    # Add line breaks for some block-level elements to keep structure
    for tag in soup.find_all(["br"]):
        tag.replace_with("\n")
    for tag in soup.find_all(["p", "li", "div", "h1", "h2", "h3", "h4"]):
        if tag.text and not tag.text.endswith("\n"):
            tag.append("\n")

    text = soup.get_text(separator="", strip=True)
    # Normalize multiple newlines + trim spaces
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def run(args: argparse.Namespace) -> None:
    # Load Excel
    df = pd.read_excel(args.inp, sheet_name=args.sheet, header=args.header_row)
    if args.sku_col not in df.columns:
        raise SystemExit(f"SKU column '{args.sku_col}' not found in sheet. Columns: {list(df.columns)}")

    # Ensure output columns
    for col in ["Body (HTML)", "Image Src", "Source_URL", "Status"]:
        if col not in df.columns:
            df[col] = ""

    # Sample limit
    total_rows = len(df)
    limit = min(args.sample, total_rows) if args.sample and args.sample > 0 else total_rows

    driver = build_driver(args.headful)
    try:
        processed = 0
        for i, row in df.iterrows():
            if processed >= limit:
                break

            sku = str(row[args.sku_col]).strip()
            if not sku or sku.lower() in {"nan", "none"}:
                df.at[i, "Status"] = "SKIP: empty SKU"
                continue

            try:
                ok = open_home_and_search(driver, sku, args.pause)
                if not ok:
                    df.at[i, "Status"] = "NOT_FOUND"
                    processed += 1
                    continue

                if not click_first_result(driver, args.pause):
                    df.at[i, "Status"] = "NOT_FOUND"
                    processed += 1
                    continue

                prod_url = driver.current_url
                images = scrape_images(driver)
                desc_text = extract_description_text(driver)  # PLAIN TEXT (no tags)

                df.at[i, "Source_URL"] = prod_url
                df.at[i, "Image Src"] = ";".join(images) if images else ""
                df.at[i, "Body (HTML)"] = desc_text  # keeping column name for compatibility
                df.at[i, "Status"] = "OK"
                processed += 1

            except TimeoutException:
                df.at[i, "Status"] = "ERROR: timeout"
                processed += 1
            except Exception as e:
                df.at[i, "Status"] = f"ERROR: {type(e).__name__}: {e}"
                processed += 1

            # Save after each row during sampling to see quick results
            df.to_excel(args.out, index=False)

        # Final save
        df.to_excel(args.out, index=False)

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    args = parse_args()
    run(args)
