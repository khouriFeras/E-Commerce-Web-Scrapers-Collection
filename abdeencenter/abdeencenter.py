import argparse
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

SCRIPT_DIR = Path(__file__).resolve().parent

# Column containing product identifiers (barcode/SKU).
# Script auto-detects from these names; falls back to first column if none match.
BARCODE_COL_CANDIDATES = [
    "Barcode",
    "barcode",
    "SKU",
    "sku",
    "Sku",
    "Item Code",
    "item code",
]

HEADLESS = False
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


def _norm_id(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip()).lower()


def get_page_sku(driver) -> str:
    """SKU from Magento `.product.attribute.sku` block."""
    value = safe_text(driver, By.CSS_SELECTOR, ".product.attribute.sku .value")
    if value:
        return value
    block = safe_text(driver, By.CSS_SELECTOR, ".product.attribute.sku")
    if block:
        return re.sub(r"^(?:SKU|Sku)\s*[:#]?\s*", "", block.strip(), flags=re.I).strip()
    return ""


def barcode_matches_sku_attribute(driver, barcode: str) -> bool:
    """True if barcode appears inside `.product.attribute.sku` text (not exact match)."""
    page_sku = get_page_sku(driver)
    if not page_sku or not barcode:
        return False
    needle = _norm_id(barcode)
    haystack = _norm_id(page_sku)
    if not needle or not haystack:
        return False
    return needle in haystack


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
        """Higher is better — prefer largest dimensions in URL."""
        u_l = u.lower()
        score = 0

        for m in re.finditer(r"(\d{2,4})x(\d{2,4})", u_l):
            w, h = int(m.group(1)), int(m.group(2))
            score = max(score, w * h)

        m = re.search(r"/cache/w(\d+)(?:/|$)", u_l)
        if m:
            w = int(m.group(1))
            score = max(score, w * w)

        if "zoom" in u_l or "large" in u_l or "full" in u_l:
            score += 500
        if "thumbnail" in u_l or "thumb" in u_l:
            score -= 10_000
        if "small" in u_l or "swatch" in u_l or "product_page_image_small" in u_l:
            score -= 5_000
        if not re.search(r"-\d+x\d+\.", u_l) and "/cache/w" not in u_l:
            score += 1_000

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
            for attr in ("src", "data-src", "data-zoom-image", "data-large-image"):
                u = norm(img.get_attribute(attr))
                if u:
                    candidates.append(u)
            srcset = img.get_attribute("srcset") or ""
            for part in srcset.split(","):
                part = part.strip()
                if part:
                    u = norm(part.split()[0])
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
def build_driver(headful: bool = False) -> webdriver.Chrome:
    opts = Options()
    if not headful:
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

    # 3) Wait product page (title + SKU block)
    try:
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, '[data-ui-id="page-title-wrapper"], .product.attribute.sku')
            )
        )
    except TimeoutException:
        out["status"] = "product_page_timeout"
        return out

    # 4) Validate barcode against `.product.attribute.sku` before scraping
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".product.attribute.sku")))
    except TimeoutException:
        out["status"] = "sku_element_missing"
        return out

    out["barcode_matched_on_page"] = barcode_matches_sku_attribute(driver, barcode)
    if not out["barcode_matched_on_page"]:
        page_sku = get_page_sku(driver)
        out["status"] = "sku_element_missing" if not page_sku else "sku_mismatch"
        return out

    out["status"] = "ok"

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
    ap = argparse.ArgumentParser(description="Abdeen Center scraper by barcode/SKU")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel or CSV file")
    ap.add_argument("--out", required=True, help="Output Excel file")
    ap.add_argument("--sku-col", dest="sku_col", default=None, help="Barcode/SKU column name (auto-detect if omitted)")
    ap.add_argument("--headful", action="store_true", help="Show browser window")
    args = ap.parse_args()

    inp_path = Path(args.inp)
    if not inp_path.exists():
        raise FileNotFoundError(f"Input file not found: {inp_path.resolve()}")

    ext = inp_path.suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(inp_path)
    elif ext in {".xls", ".xlsx", ".xlsm", ".xlsb", ".odf", ".ods", ".odt"}:
        df = pd.read_excel(inp_path)
    else:
        raise ValueError(f"Unsupported input file type: {inp_path.name}")

    barcode_col = args.sku_col if (args.sku_col and args.sku_col in df.columns) else pick_barcode_column(df)

    for c in SCRAPED_COLUMNS:
        if c not in df.columns:
            df[c] = ""

    driver = build_driver(headful=args.headful)
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

    out_path = Path(args.out)
    df.to_excel(out_path, index=False)
    print(f"\nSaved: {out_path.resolve()} ({len(df)} rows)")
    print("Done.")


if __name__ == "__main__":
    main()
