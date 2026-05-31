#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Arabi E-Mart scraper:
- Reads an Excel/CSV of product queries (name or SKU)
- For each query: search on arabiemart.com, open best result
- Extracts: product_title, description_text, image URLs (list; full size)
- Writes results to Excel/CSV

Usage (examples):
  python arabiemart_scraper.py --in products.xlsx --out results.xlsx --query-col "Product Name"
  python arabiemart_scraper.py --in products.csv  --out results.csv  --query-col "sku" --headless

Notes:
- Works with the public site structure observed on 2025-08-27.
- Requires: selenium, pandas, openpyxl (for .xlsx), webdriver-manager (optional convenience)
"""

from __future__ import annotations
import argparse, time, re, sys, math
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Optional: auto driver
try:
    from webdriver_manager.chrome import ChromeDriverManager  # type: ignore
    AUTO_DRIVER = True
except Exception:
    AUTO_DRIVER = False


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def best_match_link(links: List[webdriver.remote.webelement.WebElement], query: str):
    """
    Pick the link whose visible text best matches the query.
    Simple heuristic: prefer contains; fallback to first.
    """
    q = norm(query)
    if not links:
        return None
    # Try exact contains
    for a in links:
        t = norm(a.text)
        if q and q in t:
            return a
    # Try partial token overlap
    q_tokens = set(q.split())
    scored = []
    for a in links:
        t = norm(a.text)
        if not t:
            continue
        t_tokens = set(t.split())
        overlap = len(q_tokens & t_tokens)
        scored.append((overlap, len(t_tokens), a))
    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][2]
    return links[0]


from selenium.common.exceptions import TimeoutException

def search_and_open(driver: webdriver.Chrome, query: str, timeout: int = 20) -> bool:
    driver.get("https://arabiemart.com/")
    wait = WebDriverWait(driver, timeout)

    # 1) Find a search box (try home, then /search)
    selectors = [
        'input[type="search"]',
        'input[placeholder*="Search" i]',
        'input[aria-label*="Search" i]',
        'input[name="search"]',
        'header input',
    ]
    search_box = None
    for attempt in (0, 1):
        try:
            if attempt == 1:
                driver.get("https://arabiemart.com/search")
            for sel in selectors:
                try:
                    el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                    if el.is_displayed():
                        search_box = el
                        break
                except TimeoutException:
                    pass
            if search_box:
                break
        except TimeoutException:
            pass
    if not search_box:
        return False

    search_box.clear()
    search_box.send_keys(query)
    search_box.send_keys(Keys.ENTER)

    # 2) Wait briefly for any result grid; if none, bail out cleanly
    try:
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/items/"]')))
        time.sleep(0.6)
    except TimeoutException:
        return False  # no hits

    links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/items/"]')
    if not links:
        return False

    link = best_match_link(links, query)
    if not link:
        return False

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
    link.click()
    return True


def extract_title(driver: webdriver.Chrome, timeout: int = 20) -> str:
    wait = WebDriverWait(driver, timeout)
    # h1 is typically the title on item pages
    try:
        h1 = wait.until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
        return h1.text.strip()
    except Exception:
        return ""


def extract_description(driver: webdriver.Chrome) -> str:
    """
    Heuristics: take the largest text block in the main content area near the price/Add to cart.
    We also read <p> blocks under the section that contains the <h1>.
    """
    # Try a few likely containers
    candidates_css = [
        "main",                     # general
        '[class*="product"]',       # generic
        '[class*="item"]',          # generic
        "[role='main']",
        "body",
    ]
    text_chunks: List[str] = []

    for sel in candidates_css:
        try:
            node = driver.find_element(By.CSS_SELECTOR, sel)
            ps = node.find_elements(By.TAG_NAME, "p")
            # Use visible, meaningful paragraphs
            for p in ps:
                t = p.text.strip()
                if len(t) >= 40 and not re.search(r"(Add to cart|Return & refund|Shipping Policy|Privacy Policy)", t, re.I):
                    text_chunks.append(t)
        except Exception:
            pass

    # De-duplicate while preserving order
    seen = set(); cleaned = []
    for t in text_chunks:
        k = norm(t)
        if k and k not in seen:
            seen.add(k)
            cleaned.append(t)

    # If nothing found, fall back to any long text in body
    if not cleaned:
        try:
            t = driver.find_element(By.TAG_NAME, "body").text
            t = re.sub(r"\s+", " ", t).strip()
            # cut footer/legal noise if we can detect “Quick Links” etc.
            t = re.split(r"(Quick Links|About Us|FAQ)", t)[0].strip()
            return t[:5000]
        except Exception:
            return ""

    # Prefer the biggest contiguous paragraph block
    cleaned.sort(key=len, reverse=True)
    desc = cleaned[0]
    # If there are multiple meaningful paragraphs, join some of them
    for extra in cleaned[1:5]:
        if extra not in desc and len(extra) > 60 and len(desc) < 5000:
            desc += "\n\n" + extra
    return desc


ABSOLUTIZE_JS = r"""
const toAbs = url => {
  try { return new URL(url, location.href).href; } catch(e) { return url; }
};

function visible(el) {
  const rect = el.getBoundingClientRect();
  return rect.width > 5 && rect.height > 5 && !!el.offsetParent;
}

function inMain(el) {
  // Prefer elements near the title/add-to-cart area, avoid header/footer
  const footer = document.querySelector('footer');
  const header = document.querySelector('header');
  const withinFooter = footer && footer.contains(el);
  const withinHeader = header && header.contains(el);
  return !withinFooter && !withinHeader;
}

const urls = new Set();

// 1) <img> tags
document.querySelectorAll('img').forEach(img => {
  if (!visible(img) || !inMain(img)) return;
  const w = img.naturalWidth || img.width || 0;
  const h = img.naturalHeight || img.height || 0;
  if (Math.max(w, h) < 200) return; // skip tiny icons
  let src = img.getAttribute('src') || img.getAttribute('data-src') || '';
  if (!src) return;
  src = toAbs(src);
  urls.add(src);
});

// 2) CSS background-image
document.querySelectorAll('*').forEach(el => {
  if (!visible(el) || !inMain(el)) return;
  const bg = getComputedStyle(el).backgroundImage;
  if (bg && bg.startsWith('url(')) {
    const m = bg.match(/url\(["']?(.*?)["']?\)/);
    if (m && m[1]) urls.add(toAbs(m[1]));
  }
});

// Filter out known non-product images (payment logos, analytics, sprites)
const blacklistRe = /(clicky|master|visa|instagram|facebook|youtube|logo|icon|sprite)/i;
const out = Array.from(urls).filter(u => !blacklistRe.test(u));

// Return
return out;
"""

def extract_images(driver: webdriver.Chrome) -> List[str]:
    try:
        urls = driver.execute_script(ABSOLUTIZE_JS)
        # Basic cleanup: remove query strings that just resize
        cleaned = []
        seen = set()
        for u in urls:
            u2 = re.sub(r"(\?|#).*$", "", u).strip()
            if u2 not in seen:
                seen.add(u2)
                cleaned.append(u2)
        return cleaned
    except Exception:
        return []


def scrape_one(driver: webdriver.Chrome, query: str, timeout: int = 20) -> Dict[str, Any]:
    ok = search_and_open(driver, query, timeout=timeout)
    if not ok:
        return {
            "query": query,
            "found": False,
            "product_title": "",
            "description": "",
            "image_count": 0,
            "image_urls": ""
        }
    title = extract_title(driver, timeout=timeout)
    desc  = extract_description(driver)
    imgs  = extract_images(driver)
    return {
        "query": query,
        "found": True,
        "product_title": title,
        "description": desc,
        "image_count": len(imgs),
        "image_urls": ";".join(imgs)
    }


def run(input_path: Path, out_path: Path, query_col: str, headless: bool, delay: float, timeout: int):
    # Load input
    if input_path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(input_path)
    elif input_path.suffix.lower() in [".csv"]:
        df = pd.read_csv(input_path)
    else:
        raise SystemExit("Unsupported input format. Use .xlsx or .csv")

    if query_col not in df.columns:
        raise SystemExit(f"Column '{query_col}' not found in input. Available: {list(df.columns)}")

    # Driver
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--log-level=3")           # only fatal
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-features=OptimizationHints,Translate,MediaSessionService,PreloadMediaEngagementData,AutofillServerCommunication")
    opts.add_argument("--disable-speech-api")


    if AUTO_DRIVER:
        driver = webdriver.Chrome(ChromeDriverManager().install(), options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    rows: List[Dict[str, Any]] = []
    try:
        for i, val in enumerate(df[query_col].astype(str).fillna("").tolist(), 1):
            q = val.strip()
            if not q:
                rows.append({
                    "query": "",
                    "found": False,
                    "product_title": "",
                    "description": "",
                    "image_count": 0,
                    "image_urls": ""
                })
                continue

            print(f"[{i}/{len(df)}] Searching: {q}")
            data = scrape_one(driver, q, timeout=timeout)
            rows.append(data)
            # polite delay
            time.sleep(max(0.0, delay))
    finally:
        driver.quit()

    out_df = pd.DataFrame(rows)
    # Join back to original file (keep your other columns)
    merged = df.copy()
    merged["product_title_am"] = out_df["product_title"]
    merged["description_am"]    = out_df["description"]
    merged["image_count_am"]    = out_df["image_count"]
    merged["image_urls_am"]     = out_df["image_urls"]

    # Save
    if out_path.suffix.lower() in [".xlsx", ".xls"]:
        merged.to_excel(out_path, index=False)
    elif out_path.suffix.lower() == ".csv":
        merged.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        # default to xlsx
        merged.to_excel(out_path.with_suffix(".xlsx"), index=False)

    print(f"Saved -> {out_path}")


def main():
    p = argparse.ArgumentParser(description="Scrape Arabi E-Mart product images & description by search query.")
    p.add_argument("--in", dest="inp", required=True, help="Input .xlsx or .csv")
    p.add_argument("--out", dest="out", required=True, help="Output .xlsx or .csv")
    p.add_argument("--query-col", required=True, help="Column with product name or SKU to search")
    p.add_argument("--headless", action="store_true", help="Run Chrome headless")
    p.add_argument("--delay", type=float, default=1.0, help="Delay (seconds) between items")
    p.add_argument("--timeout", type=int, default=25, help="Per-page timeout seconds")
    args = p.parse_args()

    run(Path(args.inp), Path(args.out), args.query_col, args.headless, args.delay, args.timeout)


if __name__ == "__main__":
    main()
