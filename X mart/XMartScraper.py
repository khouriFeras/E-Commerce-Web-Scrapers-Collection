import time
import argparse
import os
import re
import sys

# Windows consoles default to cp1252, which can't encode characters like
# "→" (the arrow used in progress prints). Force UTF-8 so these prints
# don't crash the scraper. The desktop_app launcher reads our stdout as UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
from urllib.parse import quote_plus, urljoin, urlparse
from typing import List, Dict, Any, Optional

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup

BASE_URL = "https://www.xmart.jo"


def build_driver(headful: bool = False) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if not headful:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


def search_and_click_first_result(
    driver, wait: WebDriverWait, item: str, pause: float = 2.0
) -> Optional[str]:
    search_url = f"{BASE_URL}/search?q={quote_plus(item)}"
    print(f"   → Searching: {item}")
    print(f"   → URL: {search_url}")

    try:
        driver.get(search_url)
        time.sleep(pause)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
        time.sleep(1)
    except TimeoutException:
        print("   → Timeout waiting for search results")
        return None
    except Exception as e:
        print(f"   → Error loading search page: {e}")
        return None

    # Try to find product links in search results
    product_selectors = [
        "a[href*='/products/']",
        "a[href*='/product/']",
        ".product-item a",
        ".product-card a",
        ".product-grid a",
        "a.product-link",
        "[class*='product'] a",
        "h3 a",
        "h2 a",
    ]

    for selector in product_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements[:5]:
                href = element.get_attribute("href")
                if href and ("xmart.jo" in href or href.startswith("/")):
                    if href.startswith("/"):
                        href = BASE_URL + href
                    if href.startswith("http") and "xmart.jo" in href:
                        print(f"   → Found product URL: {href}")
                        driver.get(href)
                        time.sleep(pause)
                        return href
        except Exception:
            continue

    # Fallback: click first clickable product card
    try:
        product_elements = driver.find_elements(
            By.CSS_SELECTOR,
            ".product-item, .product-card, [class*='product-grid'] li, [class*='products'] li",
        )
        if product_elements:
            first_product = product_elements[0]
            link = first_product.find_element(By.TAG_NAME, "a")
            href = link.get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = BASE_URL + href
                print(f"   → Clicking first product: {href}")
                driver.get(href)
                time.sleep(pause)
                return driver.current_url
    except Exception as e:
        print(f"   → Fallback click error: {e}")

    print(f"   → No product found for: {item}")
    return None


def extract_product_code(driver) -> str:
    """Extract product code/barcode from class='f8pr-codes'."""
    try:
        html = driver.page_source
        soup = BeautifulSoup(html, "lxml")
        el = soup.select_one(".f8pr-codes")
        if el:
            code = el.get_text(" ", strip=True)
            if code:
                print(f"   → Product code: {code}")
                return code
    except Exception as e:
        print(f"   → Error extracting product code: {e}")
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".f8pr-codes")
        code = el.text.strip()
        if code:
            print(f"   → Product code (Selenium): {code}")
            return code
    except Exception:
        pass
    print("   → No product code found")
    return ""


def extract_product_title(driver) -> str:
    """Extract product title — try h1 first, then common title selectors."""
    selectors = [
        "h1.product-title",
        "h1.product-name",
        "h1",
        ".product-title",
        ".product-name",
        "[itemprop='name']",
        ".page-title",
    ]
    for selector in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, selector)
            title = el.text.strip()
            if title:
                print(f"   → Title: {title[:80]}")
                return title
        except Exception:
            continue
    print("   → No title found")
    return ""


def extract_product_description(driver) -> str:
    """Extract description from class='tabs-inner'."""
    try:
        html = driver.page_source
        soup = BeautifulSoup(html, "lxml")
        el = soup.select_one(".tabs-inner")
        if el:
            desc = el.get_text(" ", strip=True)
            if desc:
                print(f"   → Description: {len(desc)} chars")
                return desc
    except Exception as e:
        print(f"   → Error extracting description (BS): {e}")
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".tabs-inner")
        desc = el.text.strip()
        if desc:
            print(f"   → Description (Selenium): {len(desc)} chars")
            return desc
    except Exception:
        pass
    print("   → No description found")
    return ""


def clean_image_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    filename_match = re.search(r"/([^/]+\.(jpg|jpeg|png|gif|webp))$", path, re.I)
    if filename_match:
        return f"{parsed.netloc}:{filename_match.group(1).lower()}"
    path = re.sub(r"/(\d+)x(\d+)/", "/", path)
    path = re.sub(r"/(thumb|small|medium|large|big|full|original|zoom)[_-]", "/", path, flags=re.I)
    return f"{parsed.netloc}:{path}"


def get_image_size_score(url: str) -> int:
    url_lower = url.lower()
    score = 0
    w_match = re.search(r"[?&](?:w|width)=(\d+)", url_lower)
    if w_match:
        score = int(w_match.group(1))
    h_match = re.search(r"[?&](?:h|height)=(\d+)", url_lower)
    if h_match:
        h = int(h_match.group(1))
        score = score * h if score > 0 else h
    if any(x in url_lower for x in ["original", "full", "large", "big", "zoom"]):
        score = max(score, 1000)
    elif any(x in url_lower for x in ["medium", "med"]):
        score = max(score, 500)
    elif any(x in url_lower for x in ["small", "thumb", "mini"]):
        score = max(score, 100)
    dim_match = re.search(r"/(\d{3,4})x(\d{3,4})/", url_lower)
    if dim_match:
        w, h = int(dim_match.group(1)), int(dim_match.group(2))
        score = max(score, w * h // 100)
    if score == 0 and not any(x in url_lower for x in ["thumb", "small", "mini", "100x", "200x", "300x"]):
        score = 500
    return score


def deduplicate_images(urls: List[str]) -> List[str]:
    """Group by base URL, keep highest-resolution variant per image."""
    grouped: Dict[str, list] = {}
    for url in urls:
        base = clean_image_url(url)
        score = get_image_size_score(url)
        grouped.setdefault(base, []).append((url, score))
    result = []
    for variants in grouped.values():
        variants.sort(key=lambda x: x[1], reverse=True)
        result.append((variants[0][0], variants[0][1]))
    result.sort(key=lambda x: x[1], reverse=True)
    return [url for url, _ in result]


def extract_product_images(driver) -> List[str]:
    """
    Extract images from the xmart.jo product image swiper gallery only.

    xmart.jo uses a numbered swiper-pagination class (e.g. swiper-pagination-5695)
    on the product gallery. The actual images live in the sibling .swiper-wrapper
    > .swiper-slide elements inside that same swiper container.

    We anchor the search to known product-image wrapper selectors first, then
    fall back to the numbered-pagination heuristic. We deliberately avoid grabbing
    generic .swiper-slide images across the whole page so that related-products
    carousels and other sliders are not included.
    """
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
    time.sleep(1)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)

    all_urls: List[str] = []
    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")

    skip_terms = ["logo", "icon", "favicon", "banner", "placeholder", "sprite", "data:image", "base64"]

    def is_valid(src: str) -> bool:
        if not src or not src.startswith("http"):
            return False
        return not any(t in src.lower() for t in skip_terms)

    def make_absolute(src: str) -> str:
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("/"):
            return BASE_URL + src
        return src

    def collect_imgs_from_wrapper(wrapper) -> List[str]:
        found = []
        slides = wrapper.find_all(
            class_=lambda c: c and "swiper-slide" in (" ".join(c) if isinstance(c, list) else c)
        )
        for slide in slides:
            for img in slide.find_all("img"):
                src = (img.get("src") or img.get("data-src") or
                       img.get("data-lazy-src") or img.get("data-zoom-image") or
                       img.get("data-original"))
                if src:
                    src = make_absolute(src)
                    if is_valid(src):
                        found.append(src)
        return found

    # ── Strategy 1: .l4pr-container — xmart.jo's product image wrapper ──────
    # This is the confirmed product image container on xmart.jo (single or
    # multi-image). Try BeautifulSoup first, then Selenium for dynamic content.
    try:
        container = soup.select_one(".l4pr-container")
        if container:
            for img in container.find_all("img"):
                src = (img.get("src") or img.get("data-src") or
                       img.get("data-lazy-src") or img.get("data-zoom-image") or
                       img.get("data-original"))
                if src:
                    src = make_absolute(src)
                    if is_valid(src):
                        all_urls.append(src)
            if all_urls:
                print(f"   → Found product images via .l4pr-container")
    except Exception as e:
        print(f"   → .l4pr-container (BS) error: {e}")

    if not all_urls:
        try:
            container_el = driver.find_element(By.CSS_SELECTOR, ".l4pr-container")
            for img_el in container_el.find_elements(By.TAG_NAME, "img"):
                src = (img_el.get_attribute("src") or
                       img_el.get_attribute("data-src") or
                       img_el.get_attribute("data-lazy-src") or
                       img_el.get_attribute("data-zoom-image") or
                       img_el.get_attribute("data-original"))
                if src:
                    src = make_absolute(src)
                    if is_valid(src):
                        all_urls.append(src)
            if all_urls:
                print(f"   → Found product images via .l4pr-container (Selenium)")
        except Exception:
            pass

    # ── Strategy 2: swiper inside known product-image wrappers ───────────────
    # Only used if .l4pr-container yields nothing.
    if not all_urls:
        product_image_selectors = [
            "[class*='f8pr-product-image']",
            "[class*='product-image']",
            "[class*='product-media']",
            "[class*='product-gallery']",
            ".product-images",
            ".product-media",
            ".product-gallery",
        ]
        for sel in product_image_selectors:
            container = soup.select_one(sel)
            if not container:
                continue
            wrapper = container.find(
                class_=lambda c: c and "swiper-wrapper" in (" ".join(c) if isinstance(c, list) else c)
            )
            if wrapper:
                all_urls = collect_imgs_from_wrapper(wrapper)
                if all_urls:
                    print(f"   → Found product images via '{sel}' swiper")
                    break

    # ── Strategy 3: numbered swiper-pagination heuristic ────────────────────
    # Last resort swiper approach — finds swiper-pagination-<id> elements and
    # walks up to the container, taking only the first match found.
    if not all_urls:
        pagination_els = soup.find_all(
            class_=lambda c: c and re.search(
                r"swiper-pagination-\d+", " ".join(c) if isinstance(c, list) else c
            )
        )
        for pagination_el in pagination_els:
            container = pagination_el.parent
            for _ in range(5):
                if container is None:
                    break
                wrapper = container.find(
                    class_=lambda c: c and "swiper-wrapper" in (
                        " ".join(c) if isinstance(c, list) else c
                    )
                )
                if wrapper:
                    all_urls = collect_imgs_from_wrapper(wrapper)
                    if all_urls:
                        print("   → Found product images via swiper-pagination-<id> heuristic")
                        break
                container = container.parent
            if all_urls:
                break

    if not all_urls:
        print("   → No product images found")
        return []

    images = deduplicate_images(all_urls)
    print(f"   → Found {len(images)} images (from {len(all_urls)} candidates)")
    return images


def scrape_item(
    driver, wait: WebDriverWait, item: str, pause: float = 2.0
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "Item": item,
        "Product URL": "",
        "Product Code": "",
        "Title": "",
        "Description": "",
        "Images": "",
        "Image Count": 0,
        "Status": "FAILED",
        "Note": "",
    }

    try:
        product_url = search_and_click_first_result(driver, wait, item, pause)
        if not product_url:
            result["Note"] = "No search results found"
            return result

        result["Product URL"] = product_url

        try:
            wait.until(
                EC.any_of(
                    EC.presence_of_element_located((By.TAG_NAME, "h1")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".tabs-inner")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "body")),
                )
            )
        except TimeoutException:
            result["Note"] = "Page load timeout"
            return result

        time.sleep(pause)

        result["Product Code"] = extract_product_code(driver)
        result["Title"] = extract_product_title(driver)
        result["Description"] = extract_product_description(driver)

        images = extract_product_images(driver)
        result["Images"] = " | ".join(images)
        result["Image Count"] = len(images)

        if result["Title"] and images:
            result["Status"] = "SUCCESS"
        elif result["Title"] or images or result["Description"]:
            result["Status"] = "PARTIAL"
        else:
            result["Status"] = "FAILED"
            result["Note"] = "No data extracted"

        print(f"   → Status: {result['Status']}")

    except Exception as e:
        result["Status"] = "ERROR"
        result["Note"] = str(e)[:200]
        print(f"   → Error: {e}")

    return result


def main():
    parser = argparse.ArgumentParser(description="XMart.jo scraper")
    parser.add_argument(
        "--input",
        default="Gguard.xlsx",
        help="Input Excel file (default: Gguard.xlsx)",
    )
    parser.add_argument(
        "--output",
        default="xmart_scraped_results.xlsx",
        help="Output Excel file (default: xmart_scraped_results.xlsx)",
    )
    parser.add_argument(
        "--item-col",
        default="Items",
        help="Column name in the Excel file (default: Items)",
    )
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--start-row", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(script_dir, args.input)
    output_path = os.path.join(script_dir, args.output)

    if not os.path.exists(input_path):
        print(f"Error: Input file not found: {input_path}")
        return

    print(f" Reading: {input_path}")
    df = pd.read_excel(input_path)

    # Find items column
    item_col = args.item_col
    if item_col not in df.columns:
        possible = [c for c in df.columns if "item" in str(c).lower()]
        if possible:
            item_col = possible[0]
            print(f" Using column '{item_col}'")
        else:
            print(f"Error: Column '{item_col}' not found. Available: {list(df.columns)}")
            return

    items = df[item_col].fillna("").astype(str).tolist()
    items = [s.strip() for s in items if s.strip()]

    # Build lookup to carry all input columns (e.g. Barcode) into output rows
    scraped_col_names = {"Item", "Product URL", "Product Code", "Title", "Description",
                         "Images", "Image Count", "Status", "Note"}
    extra_input_cols = [c for c in df.columns if c != item_col and c not in scraped_col_names]
    input_lookup: Dict[str, Dict] = {}
    for _, row in df.iterrows():
        key = str(row[item_col]).strip()
        if key:
            input_lookup[key] = {col: row[col] for col in extra_input_cols}

    if args.limit:
        print(f" TEST MODE: limited to {args.limit} items")

    print(f" Found {len(items)} items")
    if extra_input_cols:
        print(f" Carrying over input columns: {extra_input_cols}")
    print(f" Starting from row {args.start_row}")

    # Resume logic
    existing_df = None
    already_done: set = set()
    if os.path.exists(output_path):
        try:
            existing_df = pd.read_excel(output_path)
            if "Item" in existing_df.columns:
                already_done = set(
                    existing_df["Item"]
                    .astype(str)
                    .str.strip()
                )
            print(f" Resuming: {len(already_done)} items already in output")
        except Exception as e:
            print(f" Warning: could not read existing output ({e}), starting fresh")
            existing_df = None

    print(" Starting browser...")
    driver = build_driver(headful=args.headful)
    wait = WebDriverWait(driver, 20)

    results = []
    start_idx = args.start_row
    end_idx = start_idx + args.limit if args.limit else len(items)
    items_to_process = items[start_idx:end_idx]
    total = len(items_to_process)

    try:
        for idx, item in enumerate(items_to_process, start=start_idx):
            if item in already_done:
                print(f"\n[{idx + 1 - start_idx}/{total}] Skipping already-done: {item}")
                continue

            print(f"\n[{idx + 1 - start_idx}/{total}] Processing: {item}")
            result = scrape_item(driver, wait, item, args.pause)
            for col, val in input_lookup.get(item, {}).items():
                result[col] = val
            results.append(result)

            # Checkpoint every 10 items
            if (idx + 1) % 10 == 0:
                cp_path = output_path.replace(".xlsx", f"_checkpoint_{idx + 1}.xlsx")
                cp_new = pd.DataFrame(results)
                if existing_df is not None and len(cp_new) > 0:
                    pd.concat([existing_df, cp_new], join="outer", ignore_index=True).to_excel(cp_path, index=False)
                else:
                    cp_new.to_excel(cp_path, index=False)
                print(f" Checkpoint saved: {cp_path}")

            time.sleep(args.pause)

        print(f"\n Saving results to: {output_path}")
        new_df = pd.DataFrame(results)
        if existing_df is not None and len(new_df) > 0:
            final_df = pd.concat([existing_df, new_df], join="outer", ignore_index=True)
        elif existing_df is not None:
            final_df = existing_df
        else:
            final_df = new_df
        final_df.to_excel(output_path, index=False)

        success = sum(1 for r in results if r["Status"] == "SUCCESS")
        partial = sum(1 for r in results if r["Status"] == "PARTIAL")
        failed = sum(1 for r in results if r["Status"] in ["FAILED", "ERROR"])
        print(f"\n Summary: {success} success, {partial} partial, {failed} failed")
        print(f" Results: {output_path}")

    finally:
        driver.quit()
        print(" Browser closed")


if __name__ == "__main__":
    main()
