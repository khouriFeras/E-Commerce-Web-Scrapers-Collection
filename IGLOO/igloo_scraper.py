#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import InvalidSessionIdException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://www.igloocoolers.com"
SEARCH_URL = BASE_URL + "/pages/search-results-page?q={sku}"

DEFAULT_INPUT = "Upload Products to Jaafar Shop - IGLOO.xlsx"
DEFAULT_OUTPUT = "Upload Products to Jaafar Shop - IGLOO_scraped.xlsx"

SKU_COL_CANDIDATES = ["SKU", "sku", "Sku", "Barcode", "barcode", "Item Code", "Code"]
WAIT_SEC = 25
PAUSE_SEC = 1.0


def build_driver(headless: bool) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1600,1200")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver


def pick_sku_column(df: pd.DataFrame) -> str:
    normalized = {str(c).strip().lower(): c for c in df.columns}
    for candidate in SKU_COL_CANDIDATES:
        hit = normalized.get(candidate.strip().lower())
        if hit is not None:
            return hit
    return df.columns[0]


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin(BASE_URL, url)
    return url


def image_identity_key(url: str) -> str:
    path = urlparse(url).path.lower()
    name = path.split("/")[-1]
    # Shopify image sizes usually have _WxH or WxH fragments.
    name = re.sub(r"_[0-9]{2,4}x[0-9]{2,4}(?=\.)", "", name)
    name = re.sub(r"[._-][0-9]{2,4}x[0-9]{2,4}(?=\.)", "", name)
    return name or path


def image_size_score(url: str) -> int:
    u = url.lower()
    score = 0

    for match in re.finditer(r"([0-9]{2,4})x([0-9]{2,4})", u):
        w, h = int(match.group(1)), int(match.group(2))
        score = max(score, w * h)

    for match in re.finditer(r"[?&]width=([0-9]{2,4})", u):
        w = int(match.group(1))
        score = max(score, w * w)

    if "master" in u or "original" in u or "large" in u:
        score += 800
    if "thumb" in u or "thumbnail" in u or "small" in u:
        score -= 5000
    if score == 0:
        score = 300

    return score


def pick_largest_images(candidates: List[str]) -> List[str]:
    best: Dict[str, tuple[int, str]] = {}
    order: List[str] = []
    for raw in candidates:
        u = normalize_url(raw)
        if not u.startswith("http"):
            continue
        if "placeholder" in u.lower():
            continue
        key = image_identity_key(u)
        if key not in order:
            order.append(key)
        score = image_size_score(u)
        if key not in best or score > best[key][0]:
            best[key] = (score, u)
    return [best[key][1] for key in order if key in best]


def extract_product_handle(url: str) -> str:
    """Get /products/<handle> from product URL."""
    m = re.search(r"/products/([^/?#]+)", url or "", flags=re.I)
    return m.group(1).strip() if m else ""


def fetch_images_from_shopify_product_json(product_url: str) -> List[str]:
    """
    Fetch full product image set from Shopify endpoint:
    https://www.igloocoolers.com/products/<handle>.js
    """
    handle = extract_product_handle(product_url)
    if not handle:
        return []

    endpoint = f"{BASE_URL}/products/{handle}.js"
    try:
        response = requests.get(endpoint, timeout=25)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []

    candidates: List[str] = []

    images = data.get("images", [])
    if isinstance(images, list):
        for img in images:
            if isinstance(img, str):
                candidates.append(img)
            elif isinstance(img, dict):
                src = img.get("src") or img.get("url")
                if isinstance(src, str):
                    candidates.append(src)

    # Some themes expose media objects with nested image/src fields.
    media = data.get("media", [])
    if isinstance(media, list):
        for item in media:
            if not isinstance(item, dict):
                continue
            src = (
                item.get("src")
                or item.get("url")
                or (item.get("image") or {}).get("src")
                or (item.get("image") or {}).get("url")
            )
            if isinstance(src, str):
                candidates.append(src)

    return pick_largest_images(candidates)


def get_first_product_result_url(driver: webdriver.Chrome, wait: WebDriverWait) -> Optional[str]:
    """
    Return the first *visible* product URL from the search results page.
    Igloo pages include many hidden menu links under /products/, so we only
    accept displayed anchors with non-zero dimensions.
    """
    selectors = [
        # Try likely search-result containers first.
        ".search-results a[href*='/products/']",
        "[class*='search'] a[href*='/products/']",
        "main a[href*='/products/']",
        # Fallback: all product links (filtered by visibility).
        "a[href*='/products/']",
    ]

    def _pick(_):
        for selector in selectors:
            elems = driver.find_elements(By.CSS_SELECTOR, selector)
            candidates: List[tuple[int, str]] = []
            for elem in elems:
                try:
                    href = normalize_url(elem.get_attribute("href") or "")
                    if "/products/" not in href:
                        continue
                    if not elem.is_displayed():
                        continue
                    size = elem.size or {}
                    if int(size.get("width", 0)) <= 0 or int(size.get("height", 0)) <= 0:
                        continue
                    y = int((elem.location or {}).get("y", 0))
                    candidates.append((y, href))
                except Exception:
                    continue

            if candidates:
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1]
        return False

    try:
        return wait.until(_pick)
    except TimeoutException:
        return None


def collect_product_image_candidates(driver: webdriver.Chrome) -> List[str]:
    out: List[str] = []

    # 1) Structured data is the cleanest source for the current product images.
    try:
        soup = BeautifulSoup(driver.page_source or "", "html.parser")
        for script in soup.select("script[type='application/ld+json']"):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue

            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("@type", "")).lower()
                if node_type != "product":
                    continue
                images = node.get("image", [])
                if isinstance(images, str):
                    out.append(images)
                elif isinstance(images, list):
                    for img in images:
                        if isinstance(img, str):
                            out.append(img)
    except Exception:
        pass

    # If JSON-LD already provided images, that is usually enough and product-specific.
    if out:
        return out

    # 2) DOM fallback scoped as much as possible to product section.
    # Restrict scraping to product media/gallery areas only.
    # Avoid generic page <img> nodes (related products, icons, badges).
    gallery_roots = [
        "main-product",
        "media-gallery",
        ".product__media-wrapper",
        ".product__media-list-wrapper",
        "ul.product__media-list",
        ".product-gallery",
        ".product-single__photos",
        ".product-images",
    ]
    image_selectors = [
        "img[data-zoom-image]",
        "img[data-large-image]",
        "img.product__media-image",
        "img.media__image",
        "[data-media-id] img",
        "li.product__media-item img",
        "slider-component img",
        ".swiper-slide img",
        ".thumbnail-list img",
    ]

    roots = []
    for root_selector in gallery_roots:
        roots.extend(driver.find_elements(By.CSS_SELECTOR, root_selector))

    # Fallback: still avoid fully generic page imgs by limiting to known product sections.
    if not roots:
        roots.extend(
            driver.find_elements(By.CSS_SELECTOR, "main.product, .product, #MainContent .product")
        )

    for root in roots:
        for selector in image_selectors:
            for img in root.find_elements(By.CSS_SELECTOR, selector):
                for attr in (
                    "data-zoom-image",
                    "data-large-image",
                    "data-src",
                    "data-original",
                    "src",
                ):
                    value = img.get_attribute(attr)
                    if value:
                        out.append(value)
                srcset = img.get_attribute("srcset") or ""
                for part in srcset.split(","):
                    part = part.strip()
                    if part:
                        out.append(part.split()[0])
    return out


def scrape_images_from_first_result(
    driver: webdriver.Chrome,
    wait: WebDriverWait,
    sku: str,
) -> Dict[str, object]:
    result: Dict[str, object] = {"status": "not_found", "note": "", "images": []}
    code = str(sku or "").strip()
    if not code or code.lower() == "nan":
        result["status"] = "empty_sku"
        result["note"] = "SKU is empty"
        return result

    search_url = SEARCH_URL.format(sku=quote_plus(code))
    driver.get(search_url)

    try:
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except TimeoutException:
        result["status"] = "search_timeout"
        result["note"] = "Search page did not load"
        return result

    time.sleep(PAUSE_SEC)

    # Some searches can redirect straight to a product page.
    current_url = driver.current_url or ""
    if "/products/" in current_url:
        first_product_url = current_url
    else:
        first_product_url = get_first_product_result_url(driver, wait)

    if not first_product_url:
        result["status"] = "no_result"
        result["note"] = f"No visible product results found for {code}"
        return result

    try:
        driver.get(first_product_url)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(PAUSE_SEC)
    except TimeoutException:
        result["status"] = "no_result"
        result["note"] = f"No product results found for {code}"
        return result
    except Exception as exc:
        result["status"] = "click_failed"
        result["note"] = f"Could not open first result: {type(exc).__name__}: {exc}"
        return result

    # Prefer official Shopify product JSON to get full gallery for this product.
    current_product_url = driver.current_url or first_product_url
    json_images = fetch_images_from_shopify_product_json(current_product_url)
    candidates = list(json_images)
    if not candidates:
        candidates = collect_product_image_candidates(driver)
    images = pick_largest_images(candidates)
    # Keep likely product-CDN images only.
    images = [
        u
        for u in images
        if any(token in u.lower() for token in ("/products/", "cdn.shopify.com", "igloocoolers.com"))
    ]

    if images:
        result["status"] = "ok"
        result["note"] = "Clicked first result and scraped product images"
    else:
        result["status"] = "no_images"
        result["note"] = "First product opened but no images extracted"
    result["images"] = images
    return result


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="IGLOO scraper: search by SKU, click first result, save imgs column."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Input Excel path.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output Excel path.")
    parser.add_argument("--headless", default="false", help="true/false.")
    parser.add_argument("--limit", type=int, default=0, help="Process first N rows only (0=all).")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = script_dir / input_path

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = script_dir / output_path

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    df = pd.read_excel(input_path)
    sku_col = pick_sku_column(df)
    print(f"Using SKU column: {sku_col}")

    if "imgs" not in df.columns:
        df["imgs"] = ""
    df["imgs"] = df["imgs"].astype("object")

    driver = build_driver(headless=parse_bool(args.headless))
    wait = WebDriverWait(driver, WAIT_SEC)

    try:
        total = len(df) if args.limit <= 0 else min(len(df), args.limit)
        print(f"Rows to process: {total}")
        for idx in range(total):
            sku = str(df.at[idx, sku_col])
            print(f"[{idx + 1}/{total}] sku={sku}")
            try:
                scraped = scrape_images_from_first_result(driver, wait, sku)
            except (InvalidSessionIdException, WebDriverException):
                # Headed runs can lose session if the browser window is closed.
                # Recreate the driver once and retry the same SKU.
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = build_driver(headless=parse_bool(args.headless))
                wait = WebDriverWait(driver, WAIT_SEC)
                scraped = scrape_images_from_first_result(driver, wait, sku)
            images = scraped["images"] if isinstance(scraped.get("images"), list) else []
            df.at[idx, "imgs"] = ";".join(images)
            print(f"   -> status={scraped['status']} images={len(images)}")
    finally:
        driver.quit()

    df.to_excel(output_path, index=False)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
