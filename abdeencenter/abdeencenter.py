import re
import time
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException


# =========================
# CONFIG
# =========================
BASE = "https://abdeencenter.com"
SEARCH_URL = BASE + "/catalogsearch/result/?q={q}"

INPUT_FOLDER = r"."
INPUT_EXCEL  = r"JO CELL\Promate99.xlsx"


# Save behavior:
# - If None: overwrite same Excel file in-place (keeps existing cols, adds new ones)
# - If set: write to this new file name (safer)
OUTPUT_EXCEL = None  # e.g. "barcodes_with_scraped_data.xlsx"

# Column name containing barcodes (script will auto-detect from candidates; otherwise uses first column)
BARCODE_COL_CANDIDATES = ["barcode", "barCode", "Barcode", "BARCODE", "sku", "SKU"]

HEADLESS = False
WAIT_SEC = 20
SLEEP_BETWEEN = 0.5

# Append-only columns to add to your existing Excel
SCRAPED_COLUMNS = [
    "product_url",
    "scraped_title",
    "scraped_overview_text",
    "scraped_description_text",
    "scraped_image_urls",
    "image_count",
    "barcode_matched_on_page",
    "scrape_status",
]

# Optional: resume mode (skip rows already OK)
RESUME_SKIP_OK = True


# =========================
# HELPERS
# =========================
def pick_barcode_column(df: pd.DataFrame) -> str:
    for c in BARCODE_COL_CANDIDATES:
        if c in df.columns:
            return c
    return df.columns[0]


def clean_barcode(x) -> Optional[str]:
    if pd.isna(x):
        return None
    s = str(x).strip()
    if not s or s.lower() == "nan":
        return None
    # If Excel reads numeric barcodes like 12345.0
    s = re.sub(r"\.0$", "", s)
    return s


def safe_text(driver, by, value) -> str:
    try:
        el = driver.find_element(by, value)
        return (el.text or "").strip()
    except NoSuchElementException:
        return ""


def safe_inner_html(driver, by, value) -> str:
    try:
        el = driver.find_element(by, value)
        return (el.get_attribute("innerHTML") or "").strip()
    except NoSuchElementException:
        return ""


def page_contains_barcode(driver, barcode: str) -> bool:
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text or ""
        return barcode in body_text
    except Exception:
        return False


def first_search_result_url(driver) -> Optional[str]:
    """
    Find the first product link in Magento search results.
    We try a few common selectors.
    """
    selectors = [
        "ol.products li.product a.product-item-link",
        "ol.products li.product-item a.product-item-link",
        "ul.products li.product a.product-item-link",
        "li.product-item a.product-item-link",
        "a.product-item-link",
        "ol.products li.product-item a[href]",
        "ul.products li.product-item a[href]",
    ]

    for sel in selectors:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        els = [e for e in els if (e.get_attribute("href") or "").strip()]
        if els:
            return els[0].get_attribute("href")

    return None

from urllib.parse import urlparse

def get_gallery_image_urls(driver) -> List[str]:
    """
    Magento gallery usually renders both thumbnails and full-size images.
    Strategy:
      - Collect URLs from full-size attributes first (data-zoom-image/data-full/data-large)
      - Collect <img src/data-src> as fallback
      - Deduplicate by filename (same image different sizes)
      - Prefer "full" URLs via ranking
    """

    def norm(u: str) -> str:
        u = (u or "").strip()
        if not u:
            return ""
        if u.startswith("//"):
            u = "https:" + u
        return u

    def filename_key(u: str) -> str:
        # dedupe key = filename without query string
        try:
            p = urlparse(u)
            path = p.path or ""
            return path.split("/")[-1].lower()
        except Exception:
            return u.split("?")[0].split("/")[-1].lower()

    def rank(u: str) -> int:
        """
        Higher is better (more likely full-size).
        You can tweak these if you observe different patterns on Abdeen.
        """
        u_l = u.lower()

        score = 0
        # Prefer explicit full/zoom sources
        if "data-zoom-image" in u_l:
            score += 100  # (won't happen here, but kept conceptually)
        if "zoom" in u_l:
            score += 40
        if "large" in u_l:
            score += 20
        if "full" in u_l:
            score += 20

        # Thumbnails often contain these patterns
        if "thumbnail" in u_l:
            score -= 50
        if "thumb" in u_l:
            score -= 30
        if "small" in u_l:
            score -= 20
        if "swatch" in u_l:
            score -= 20
        if "product_page_image_small" in u_l:
            score -= 60

        # Cache URLs can appear for both, but thumbnails are often more "cache-y"
        # so only a small penalty
        if "/cache/" in u_l:
            score -= 5

        return score

    candidates: List[str] = []

    # A) Prefer full-size attributes anywhere on the page
    full_attr_selectors = [
        "[data-zoom-image]",
        "[data-full]",
        "[data-large]",
        "[data-large-image]",
    ]
    for sel in full_attr_selectors:
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            for attr in ("data-zoom-image", "data-full", "data-large", "data-large-image"):
                u = norm(el.get_attribute(attr))
                if u:
                    candidates.append(u)

    # B) Then collect from inside the gallery container (includes thumbs; used as fallback)
    try:
        gallery = driver.find_element(By.CSS_SELECTOR, '[data-gallery-role="gallery"]')
        imgs = gallery.find_elements(By.CSS_SELECTOR, "img")
        for img in imgs:
            for attr in ("src", "data-src"):
                u = norm(img.get_attribute(attr))
                if u:
                    candidates.append(u)
    except NoSuchElementException:
        pass

    # C) Deduplicate by filename; keep the best-ranked URL per filename
    best_by_file: Dict[str, str] = {}
    best_score: Dict[str, int] = {}

    for u in candidates:
        key = filename_key(u)
        s = rank(u)
        if (key not in best_by_file) or (s > best_score[key]):
            best_by_file[key] = u
            best_score[key] = s

    # Keep stable order by appearance in candidates, but only include chosen best
    seen_keys = set()
    final_urls: List[str] = []
    for u in candidates:
        key = filename_key(u)
        if key in seen_keys:
            continue
        if best_by_file.get(key) == u:
            final_urls.append(u)
            seen_keys.add(key)

    return final_urls


# =========================
# SCRAPER CORE
# =========================
def build_driver() -> webdriver.Chrome:
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--log-level=3")
    return webdriver.Chrome(options=opts)
def wait_for_gallery_images(driver, wait: WebDriverWait, min_images: int = 1, stable_rounds: int = 3, poll_s: float = 0.6) -> List[str]:
    """
    Wait until the Magento gallery images finish loading.
    We consider it 'ready' when:
      - we have at least min_images, AND
      - the set of URLs is stable for stable_rounds polls
    """
    # Ensure gallery exists
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '[data-gallery-role="gallery"]')))
    except TimeoutException:
        # If no gallery, return whatever we can find immediately
        return get_gallery_image_urls(driver)

    # Trigger lazy-loading: scroll gallery into view
    try:
        gallery = driver.find_element(By.CSS_SELECTOR, '[data-gallery-role="gallery"]')
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", gallery)
        time.sleep(0.3)
    except Exception:
        pass

    last = None
    stable = 0

    # Poll for stability
    for _ in range(25):  # ~25 * poll_s seconds max
        urls = get_gallery_image_urls(driver)
        cur = tuple(urls)

        if len(urls) >= min_images and cur == last:
            stable += 1
            if stable >= stable_rounds:
                return urls
        else:
            stable = 0

        last = cur
        time.sleep(poll_s)

    # Return best effort after timeout
    return list(last) if last else get_gallery_image_urls(driver)


def scrape_one_barcode(driver: webdriver.Chrome, wait: WebDriverWait, barcode: str) -> Dict:
    out = {
        "barcode": barcode,
        "status": "",
        "product_url": "",
        "title": "",
        "overview_text": "",
        "overview_html": "",
        "description_text": "",
        "description_html": "",
        "image_urls": "",
        "image_count": 0,
        "barcode_matched_on_page": False,
    }

    # 1) Search
    driver.get(SEARCH_URL.format(q=barcode))

    try:
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "ol.products, ul.products")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".message.notice, .message.info, .message.empty")),
            )
        )
    except TimeoutException:
        out["status"] = "search_timeout"
        return out

    # 2) First result
    url = first_search_result_url(driver)
    if not url:
        out["status"] = "no_results_or_cant_find_first_item"
        return out

    out["product_url"] = url
    driver.get(url)

    # 3) Wait title
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '[data-ui-id="page-title-wrapper"]')))
    except TimeoutException:
        out["status"] = "product_page_timeout"
        return out

    # 4) Verify barcode exists on page
    out["barcode_matched_on_page"] = page_contains_barcode(driver, barcode)
    out["status"] = "ok" if out["barcode_matched_on_page"] else "barcode_not_found_on_product_page"

    # 5) Scrape title
    out["title"] = safe_text(driver, By.CSS_SELECTOR, '[data-ui-id="page-title-wrapper"]')

    # 6) Scrape overview + description blocks
    out["overview_text"] = safe_text(driver, By.CSS_SELECTOR, ".product.attribute.overview")
    out["overview_html"] = safe_inner_html(driver, By.CSS_SELECTOR, ".product.attribute.overview")

    out["description_text"] = safe_text(driver, By.CSS_SELECTOR, ".product.attribute.description")
    out["description_html"] = safe_inner_html(driver, By.CSS_SELECTOR, ".product.attribute.description")

    # 7) Images
    imgs = wait_for_gallery_images(driver, wait, min_images=1, stable_rounds=3, poll_s=0.6)
    out["image_count"] = len(imgs)
    out["image_urls"] = "; \n".join(imgs)

    return out


# =========================
# MAIN
# =========================
def main():
    inp_path = Path(INPUT_FOLDER) / INPUT_EXCEL
    if not inp_path.exists():
        raise FileNotFoundError(f"Excel file not found: {inp_path.resolve()}")

    df = pd.read_excel(inp_path)
    barcode_col = pick_barcode_column(df)

    # Ensure scraped columns exist (append to existing cols)
    for c in SCRAPED_COLUMNS:
        if c not in df.columns:
            df[c] = ""

    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SEC)

    try:
        total = len(df)
        for idx in range(total):
            bc = clean_barcode(df.at[idx, barcode_col])
            if not bc:
                continue

            if RESUME_SKIP_OK:
                existing_status = str(df.at[idx, "scrape_status"]).strip().lower()
                if existing_status == "ok":
                    continue

            print(f"[{idx+1}/{total}] barcode={bc}")

            try:
                scraped = scrape_one_barcode(driver, wait, bc)

                df.at[idx, "product_url"] = scraped.get("product_url", "")
                df.at[idx, "scraped_title"] = scraped.get("title", "")
                df.at[idx, "scraped_overview_text"] = scraped.get("overview_text", "")
                df.at[idx, "scraped_description_text"] = scraped.get("description_text", "")
                df.at[idx, "scraped_image_urls"] = scraped.get("image_urls", "")
                df.at[idx, "image_count"] = scraped.get("image_count", 0)
                df.at[idx, "barcode_matched_on_page"] = scraped.get("barcode_matched_on_page", False)
                df.at[idx, "scrape_status"] = scraped.get("status", "")

            except WebDriverException as e:
                df.at[idx, "scrape_status"] = f"webdriver_error: {type(e).__name__}"
            except Exception as e:
                df.at[idx, "scrape_status"] = f"error: {type(e).__name__}"

            time.sleep(SLEEP_BETWEEN)

    finally:
        driver.quit()

    # Save
    if OUTPUT_EXCEL:
        out_path = Path(INPUT_FOLDER) / OUTPUT_EXCEL
    else:
        out_path = inp_path  # overwrite same file (columns appended)

    df.to_excel(out_path, index=False)
    print(f"\nSaved: {out_path.resolve()} ({len(df)} rows)")
    print("Done.")


if __name__ == "__main__":
    main()
