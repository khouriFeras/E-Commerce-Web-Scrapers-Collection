# -*- coding: utf-8 -*-
"""
Laroy/Duvo+ product scraper (Splide gallery support)
- Body (HTML): plain text (no HTML tags)
- Image Src: semicolon-separated product image URLs (deduped)
- Source_URL: product page opened

Usage (PowerShell):
  python layorGroup.py `
    --in "newData\\DUVO_with_tags - Copy.xlsx" `
    --out "newData\\DUVO_with_tagsScraped.xlsx" `
    --sku-col "Variant SKU" `
    --sleep 1.0 `
    --checkpoint 100 `
    --headful

Tip for Laroy pages with Splide:
  Add: --sel-gallery "#splide03-track"
"""

import os, time, random, re, argparse, logging, urllib.parse, math, json
from urllib.parse import urljoin, quote_plus
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

import pandas as pd
from bs4 import BeautifulSoup
from tqdm import tqdm

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, StaleElementReferenceException
)

# ---------------- Config ----------------
BASE_SEARCH_URL = "https://shop.laroyduvo.be/en/search?form=search&q_widget={query}"

# Selectors widened to include Splide containers you showed in the screenshot
DEFAULT_SELECTORS = dict(
    search_card_links = (
        "a.product-card, a.product__link, a.card--product, .product-card a, "
        ".product a[href*='/products/detail/'], a[href*='/products/detail/']"
    ),
    product_title = "h1, .product-title, .title h1",
    product_desc  = ".product-description, .description, #description, article .content, .prose",
    # legacy image nodes (not central now)
    product_imgs  = "img[src*='/uploads/'], .gallery img, .product-images img, .swiper-slide img, picture img, img[data-src]",
    # Product gallery containers (first match wins) + Splide variants
    product_gallery = ",".join([
        ".product-gallery", ".product__media", ".product-media", ".product__images",
        ".product__gallery", ".product-images", "#product-gallery", ".gallery",
        # Splide:
        "#splide03-track", ".section-thumbnail-inner.splide", ".splide", ".splide__track", ".splide__list",
        # Swiper fallbacks:
        ".swiper", ".swiper-wrapper",
        "[data-gallery]"
    ])
)

IMG_EXCLUDE_PAT    = re.compile(r"(logo|icon|placeholder|sprite|_thumb|/thumbs?/|/icons?/|/logos?/)", re.I)
IMG_EXT_PAT        = re.compile(r"\.(jpe?g|png|webp|gif|bmp)(\?.*)?$", re.I)
# allow both site domains and product-like paths
IMG_WHITELIST_PAT  = re.compile(r"/(uploads|product|products|product-images|media|images?/products?)/", re.I)
IMG_DOMAIN_ALLOW   = re.compile(r"(laroyduvo\.be|laroygroup\.com)", re.I)

# ---------------- Helpers ----------------
def human_sleep(base: float) -> None:
    jitter = random.uniform(0.2, 0.6)
    time.sleep(max(0.0, base + jitter))

def clean_text(html_or_text: str) -> str:
    """Return plain text (no HTML tags)."""
    if not isinstance(html_or_text, str):
        return ""
    soup = BeautifulSoup(html_or_text, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    txt = soup.get_text(" ", strip=True)
    return re.sub(r"\s{2,}", " ", txt).strip()

def first_existing(df: pd.DataFrame, names: List[str]) -> Optional[str]:
    lower_map = {c.lower().strip(): c for c in df.columns}
    for n in names:
        k = n.lower().strip()
        if k in lower_map:
            return lower_map[k]
    return None

def uniq_keep_order(seq: List[str]) -> List[str]:
    seen = set(); out = []
    for s in seq:
        if isinstance(s, str):
            s = s.strip()
        if s and s not in seen:
            seen.add(s); out.append(s)
    return out

def absolute_url(base: str, url: str) -> str:
    try: return urljoin(base, url)
    except Exception: return url

def strip_size_params(u: str) -> str:
    try:
        p = urllib.parse.urlparse(u)
        return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except Exception:
        return u

def is_valid_img_url(u) -> bool:
    if u is None: return False
    if isinstance(u, float):
        try:
            if math.isnan(u): return False
        except Exception:
            pass
    u = str(u).strip()
    if not u or u.lower() in {"nan", "none", "null"}:
        return False
    domain_ok = bool(IMG_DOMAIN_ALLOW.search(u))
    path_ok   = bool(IMG_WHITELIST_PAT.search(u))
    return bool(IMG_EXT_PAT.search(u) and not IMG_EXCLUDE_PAT.search(u) and (domain_ok or path_ok))

def parse_semicolon_list(val) -> List[str]:
    """Parse 'a; b; c' into a clean list; ignore NaN/'nan'/non-image tokens."""
    if val is None: return []
    if isinstance(val, float):
        try:
            if math.isnan(val): return []
        except Exception:
            pass
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return []
    parts = [p.strip() for p in s.split(";")]
    return [p for p in parts if is_valid_img_url(p)]

def write_image_src(df: pd.DataFrame, idx: int, urls: List[str]) -> None:
    """Always write a plain string (never NaN/None)."""
    cleaned = [u.strip() for u in urls if is_valid_img_url(u)]
    df.at[idx, "Image Src"] = "; ".join(uniq_keep_order(cleaned)) if cleaned else ""

@dataclass
class Selectors:
    search_card_links: str
    product_title: str
    product_desc: str
    product_imgs: str
    product_gallery: str
    @classmethod
    def from_args(cls, args) -> "Selectors":
        return cls(
            search_card_links = args.sel_search or DEFAULT_SELECTORS["search_card_links"],
            product_title     = args.sel_title  or DEFAULT_SELECTORS["product_title"],
            product_desc      = args.sel_desc   or DEFAULT_SELECTORS["product_desc"],
            product_imgs      = args.sel_imgs   or DEFAULT_SELECTORS["product_imgs"],
            product_gallery   = getattr(args, "sel_gallery", None) or DEFAULT_SELECTORS["product_gallery"],
        )

def make_driver(headless: bool = True) -> webdriver.Chrome:
    opts = ChromeOptions()
    if headless: opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu"); opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1400,1000"); opts.add_argument("--lang=en-US,en")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver

def search_and_open_first(driver: webdriver.Chrome, query: str, sels: Selectors, timeout: int = 20) -> Optional[str]:
    url = BASE_SEARCH_URL.format(query=quote_plus(query))
    driver.get(url)
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, sels.search_card_links))
        )
    except TimeoutException:
        return None

    links = driver.find_elements(By.CSS_SELECTOR, sels.search_card_links)
    for a in links:
        try:
            href = a.get_attribute("href") or ""
            if "/products/" in href:
                driver.get(href)
                return driver.current_url
        except (StaleElementReferenceException, WebDriverException):
            continue
    return None

# ---------------- Image strategies ----------------
def _click_swiper_splide_nav(driver, root):
    # Try splide/swiper next/prev to reveal more slides
    for sel in [
        ".splide__arrow--next", ".splide__arrow--prev", ".splide__arrow",
        ".swiper-button-next", ".swiper-button-prev",
        ".swiper-button-next:not(.swiper-button-disabled)",
        ".swiper-button-prev:not(.swiper-button-disabled)"
    ]:
        try:
            btns = root.find_elements(By.CSS_SELECTOR, sel)
            for b in btns[:6]:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    time.sleep(0.05)
                    b.click()
                    time.sleep(0.08)
                except Exception:
                    continue
        except Exception:
            pass

def _collect_from_elem_js(driver, elem):
    # Collect src, data-src, srcset, data-srcset; <source>, <a href img>, background-image
    js = r"""
    const root = arguments[0];
    const urls = new Set();

    function addFromSrcset(ss) {
      if (!ss) return;
      ss.split(',').forEach(part => {
        const u = part.trim().split(' ')[0];
        if (u) urls.add(u);
      });
    }

    root.querySelectorAll('img').forEach(img => {
      const s1 = img.getAttribute('src');
      const s2 = img.getAttribute('data-src');
      const s3 = img.getAttribute('data-original');
      if (s1) urls.add(s1);
      if (s2) urls.add(s2);
      if (s3) urls.add(s3);
      addFromSrcset(img.getAttribute('srcset'));
      addFromSrcset(img.getAttribute('data-srcset'));
    });

    root.querySelectorAll('source').forEach(src => {
      addFromSrcset(src.getAttribute('srcset'));
      addFromSrcset(src.getAttribute('data-srcset'));
    });

    root.querySelectorAll('a[href]').forEach(a => {
      const href = a.getAttribute('href') || '';
      if (/\.(jpe?g|png|webp|gif|bmp)(\?.*)?$/i.test(href)) urls.add(href);
    });

    root.querySelectorAll('*').forEach(el => {
      const bg = window.getComputedStyle(el).getPropertyValue('background-image');
      if (bg && bg.startsWith('url(')) {
        const m = bg.match(/url\(["']?(.*?)["']?\)/i);
        if (m && m[1]) urls.add(m[1]);
      }
    });

    return Array.from(urls);
    """
    try:
        return driver.execute_script(js, elem) or []
    except Exception:
        return []

def _collect_gallery_first(driver, sels) -> List[str]:
    # Find gallery root (prefer Splide you showed)
    try:
        gallery_root = driver.find_element(By.CSS_SELECTOR, sels.product_gallery)
    except Exception:
        try:
            title_el = driver.find_element(By.CSS_SELECTOR, sels.product_title)
            gallery_root = title_el.find_element(By.XPATH, "ancestor::section[1]")
        except Exception:
            return []

    # bring into view
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", gallery_root)
    except Exception:
        pass

    _click_swiper_splide_nav(driver, gallery_root)

    # Click thumbnails inside gallery (Splide first; then fallbacks)
    try:
        thumbs = gallery_root.find_elements(
            By.CSS_SELECTOR,
            "li.splide__slide img, ul.splide__list img, #splide03-list img, "
            ".thumbSpec img, .splide__track img, "
            ".swiper-slide img, .swiper-wrapper img, .thumbnails img, [role='tab'] img, [data-thumb] img"
        )
        for t in thumbs[:24]:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
                time.sleep(0.05); t.click(); time.sleep(0.08)
            except Exception:
                continue
    except Exception:
        pass

    raw = _collect_from_elem_js(driver, gallery_root)
    base = driver.current_url
    urls = [absolute_url(base, u) for u in raw if u]
    urls = [strip_size_params(u) for u in urls]
    urls = [u for u in urls if is_valid_img_url(u)]
    return uniq_keep_order(urls)

def _collect_page_fallback(driver) -> List[str]:
    try:
        root = driver.find_element(By.TAG_NAME, "body")
    except Exception:
        return []
    raw = _collect_from_elem_js(driver, root)
    base = driver.current_url
    urls = [absolute_url(base, u) for u in raw if u]
    urls = [strip_size_params(u) for u in urls]
    urls = [u for u in urls if is_valid_img_url(u)]
    return uniq_keep_order(urls)

def _collect_jsonld_opengraph(driver) -> List[str]:
    out: List[str] = []
    try:
        html = driver.page_source
    except Exception:
        return out

    soup = BeautifulSoup(html, "html.parser")

    # JSON-LD Product
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict):
                imgs = obj.get("image") or obj.get("images")
                if isinstance(imgs, str):
                    out.append(imgs)
                elif isinstance(imgs, list):
                    out.extend([x for x in imgs if isinstance(x, str)])

    # OpenGraph
    for prop in ["og:image", "og:image:secure_url"]:
        for m in soup.find_all("meta", attrs={"property": prop}):
            c = (m.get("content") or "").strip()
            if c:
                out.append(c)

    base = driver.current_url
    urls = [absolute_url(base, u) for u in out if u]
    urls = [strip_size_params(u) for u in urls]
    urls = [u for u in urls if is_valid_img_url(u)]
    return uniq_keep_order(urls)

def collect_product_images(driver, sels) -> List[str]:
    imgs = _collect_gallery_first(driver, sels)
    if len(imgs) >= 1: return imgs

    imgs = _collect_page_fallback(driver)
    if len(imgs) >= 1: return imgs

    imgs = _collect_jsonld_opengraph(driver)
    if len(imgs) >= 1: return imgs

    logging.info("No images found after all strategies.")
    return []

# ---------------- Scrape page ----------------
def scrape_product_page(driver: webdriver.Chrome, sels: Selectors, timeout: int = 20) -> Dict[str, Any]:
    # Title
    title = ""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, sels.product_title)))
        el = driver.find_element(By.CSS_SELECTOR, sels.product_title)
        title = clean_text(el.get_attribute("innerHTML") or el.text)
    except Exception:
        pass

    # Description (plain text)
    desc_text = ""
    try:
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        main_parts: List[str] = []
        h = soup.select_one(".h5.mb-3")
        if h:
            ht = h.get_text(" ", strip=True)
            if ht: main_parts.append(clean_text(ht))
            for sib in h.next_siblings:
                if getattr(sib, "name", None) in ["h1","h2","h3","h4","h5","h6"]: break
                if getattr(sib, "name", None) in ["p", "div", "span", "li"]:
                    txt = sib.get_text(" ", strip=True)
                    if txt: main_parts.append(clean_text(txt))
        main_desc = " ".join(main_parts).strip()

        specs_list: List[str] = []
        for cand in soup.select(".h5.mb-3"):
            if (cand.get_text(strip=True) or "").strip().lower() == "specifications":
                ul = cand.find_next(["ul", "ol"])
                if ul:
                    for li in ul.find_all("li"):
                        t = li.get_text(" ", strip=True)
                        if t: specs_list.append(clean_text(t))
                break

        if specs_list:
            specs_block = "\n".join(f"- {s}" for s in specs_list)
            desc_text = (main_desc + "\n\n" + specs_block).strip() if main_desc else specs_block
        else:
            desc_text = main_desc

        if not desc_text:
            els = driver.find_elements(By.CSS_SELECTOR, sels.product_desc)
            chunks = []
            for el in els[:3]:
                raw = el.get_attribute("innerHTML") or el.text
                chunks.append(clean_text(raw))
            desc_text = " ".join(chunks).strip()
    except Exception:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sels.product_desc)
            chunks = []
            for el in els[:3]:
                raw = el.get_attribute("innerHTML") or el.text
                chunks.append(clean_text(raw))
            desc_text = " ".join(chunks).strip()
        except Exception:
            desc_text = ""

    # Images (multi-strategy with Splide)
    try:
        img_urls = collect_product_images(driver, sels)
    except Exception:
        img_urls = []

    return dict(
        Product_Title=title,
        Product_Description=desc_text,
        Image_List=img_urls,
    )

# ---------------- Main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel/CSV path")
    ap.add_argument("--out", dest="out", required=True, help="Output Excel/CSV path")
    ap.add_argument("--sku-col", dest="sku_col", required=True, help="Column with search term (SKU/barcode/title)")
    ap.add_argument("--sleep", type=float, default=0.8, help="Base sleep per row (seconds)")
    ap.add_argument("--checkpoint", type=int, default=0, help="Save a checkpoint every N processed rows (0=off)")
    ap.add_argument("--headful", action="store_true", help="Open a visible Chrome window")
    ap.add_argument("--sel-search", dest="sel_search", help="CSS for product cards on search page")
    ap.add_argument("--sel-title",  dest="sel_title",  help="CSS for product title")
    ap.add_argument("--sel-desc",   dest="sel_desc",   help="CSS for product description")
    ap.add_argument("--sel-imgs",   dest="sel_imgs",   help="CSS for product images (fallback)")
    ap.add_argument("--sel-gallery", dest="sel_gallery", help="CSS for product gallery container (best)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")

    inp = Path(args.inp); outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)

    # Load input
    if inp.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(inp)
    elif inp.suffix.lower() == ".csv":
        df = pd.read_csv(inp)
    else:
        raise ValueError(f"Unsupported input extension: {inp.suffix}")

    # Resolve SKU column
    if args.sku_col not in df.columns:
        sku_aliases = [args.sku_col, "Variant SKU", "SKU", "رقم الباركود", "كود المنتج - SKU", "Barcode", "EAN", "UPC", "رقم المادة"]
        resolved = first_existing(df, sku_aliases)
        if resolved:
            logging.info("SKU column not found as '%s', using detected column: '%s'", args.sku_col, resolved)
            args.sku_col = resolved
        else:
            raise ValueError(f"Column '{args.sku_col}' not found. Available: {list(df.columns)}")

    # Ensure output columns
    if "Body (HTML)" not in df.columns: df["Body (HTML)"] = ""
    if "Source_URL" not in df.columns: df["Source_URL"] = ""
    if "Image Src" not in df.columns: df["Image Src"] = ""

    # Force string dtype & normalize 'nan' for Image Src
    df["Image Src"] = df["Image Src"].astype("string").fillna("")
    df["Image Src"] = df["Image Src"].str.replace(r"^\s*(nan|none|null)\s*$", "", regex=True)

    sels = Selectors.from_args(args)
    driver = make_driver(headless=(not args.headful))

    processed = 0
    try:
        for idx in tqdm(range(len(df)), desc="Scraping"):
            cell = df.loc[idx, args.sku_col]
            if pd.isna(cell): continue
            q = str(cell).strip()
            if not q: continue

            try:
                url = search_and_open_first(driver, query=q, sels=sels, timeout=20)
                if not url:
                    df.at[idx, "Source_URL"] = ""
                else:
                    df.at[idx, "Source_URL"] = url
                    data = scrape_product_page(driver, sels=sels, timeout=20)

                    # Description (plain text)
                    df.at[idx, "Body (HTML)"] = (data.get("Product_Description") or "").strip()

                    # Images -> Image Src (semicolon-separated)
                    existing = parse_semicolon_list(df.at[idx, "Image Src"])
                    scraped  = data.get("Image_List") or []
                    merged   = uniq_keep_order(existing + [u for u in scraped if is_valid_img_url(u)])
                    write_image_src(df, idx, merged)

            except Exception as e:
                logging.warning("Row %s (%s) failed: %s", idx, q, e)

            human_sleep(args.sleep)
            processed += 1

            # Checkpoint
            if args.checkpoint and processed % args.checkpoint == 0:
                chk = outp.with_name(f"{outp.stem}.checkpoint_{processed}{outp.suffix}")
                try:
                    if outp.suffix.lower() in {".xlsx", ".xls"}: df.to_excel(chk, index=False)
                    else: df.to_csv(chk, index=False)
                    logging.info("Checkpoint saved: %s", chk)
                except Exception as e:
                    logging.warning("Failed to save checkpoint %s: %s", chk, e)

    finally:
        try: driver.quit()
        except Exception: pass

    # Final normalization
    df["Image Src"] = df["Image Src"].astype("string").fillna("")
    df["Image Src"] = df["Image Src"].str.replace(r"^\s*(nan|none|null)\s*$", "", regex=True)

    # Save
    if outp.suffix.lower() in {".xlsx", ".xls"}:
        df.to_excel(outp, index=False)
    elif outp.suffix.lower() == ".csv":
        df.to_csv(outp, index=False)
    else:
        df.to_excel(outp, index=False)

    logging.info("Done. Saved to %s", outp)

if __name__ == "__main__":
    main()
