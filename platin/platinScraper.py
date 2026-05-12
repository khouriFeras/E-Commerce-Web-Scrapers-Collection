#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BASE_URL = "https://www.platin-imex.co"


@dataclass
class ProductResult:
    product_url: str
    product_id: str
    name: str
    description: str
    images: List[str]
    video_url: str
    status: str


def build_driver(headful: bool = False) -> webdriver.Chrome:
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver


def _abs_url(u: str, base: str = BASE_URL) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return urljoin(base, u)
    return u


def _normalize_ws(s: str) -> str:
    s = s or ""
    s = s.replace("\u200f", " ").replace("\u200e", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def _extract_product_id(product_url: str) -> str:
    m = re.search(r"[?&]id=(\d+)", product_url)
    return m.group(1) if m else ""


def collect_product_links(listing_urls: Sequence[str], timeout: int = 30) -> List[str]:
    """
    Listing pages contain product cards in `.cards_con` that redirect to `/ar/productDetails?id=...`.
    We can collect these links via simple HTTP.
    """
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0"})

    found: List[str] = []
    seen = set()

    for u in listing_urls:
        r = sess.get(u, timeout=timeout)
        r.raise_for_status()
        # Grab productDetails links (absolute or relative).
        hrefs = re.findall(r'href="([^"]*productDetails\?id=\d+[^"]*)"', r.text, flags=re.IGNORECASE)
        # Also handle single quotes just in case.
        hrefs += re.findall(r"href='([^']*productDetails\?id=\d+[^']*)'", r.text, flags=re.IGNORECASE)

        for h in hrefs:
            full = _abs_url(h, base=BASE_URL)
            if "/ar/productDetails" not in full:
                continue
            if full in seen:
                continue
            seen.add(full)
            found.append(full)

    return found


def _size_score_from_url(u: str) -> int:
    """
    Heuristic to prefer "largest/highest resolution" images.
    Looks for patterns like 800x800 or w=1200.
    """
    if not u:
        return 0
    u_low = u.lower()
    score = 0

    m = re.search(r"(\d{2,5})x(\d{2,5})", u_low)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        score = max(score, w * h)

    m = re.search(r"[?&](?:w|width)=(\d{2,5})", u_low)
    if m:
        w = int(m.group(1))
        score = max(score, w * w)

    if any(tag in u_low for tag in ["original", "full", "large", "big"]):
        score = max(score, 2_000_000)

    return score


def _dedupe_keep_best(urls: Iterable[str]) -> List[str]:
    """
    Dedupe while keeping the 'largest' variant per normalized key.
    """
    best: Dict[str, Tuple[str, int]] = {}

    for u in urls:
        u = _abs_url(u, base=BASE_URL)
        if not u or u.startswith("data:"):
            continue
        # Normalize key by stripping common size query params.
        key = re.sub(r"[?&](?:w|h|width|height|fit|format|auto)=[^&]+", "", u, flags=re.IGNORECASE)
        key = re.sub(r"[?&]+$", "", key)
        s = _size_score_from_url(u)
        if key not in best or s > best[key][1]:
            best[key] = (u, s)

    ordered = sorted(best.values(), key=lambda x: x[1], reverse=True)
    return [u for (u, _s) in ordered]


def _dedupe_preserve_order_keep_best(urls: Iterable[str]) -> List[str]:
    """
    Dedupe while preserving first-seen order of unique images,
    but if we see a larger variant of an already-seen image later,
    we replace the stored URL in-place.
    """
    order: List[str] = []
    key_to_index: Dict[str, int] = {}
    key_to_score: Dict[str, int] = {}

    for raw in urls:
        u = _abs_url(raw, base=BASE_URL)
        if not u or u.startswith("data:"):
            continue
        key = re.sub(r"[?&](?:w|h|width|height|fit|format|auto)=[^&]+", "", u, flags=re.IGNORECASE)
        key = re.sub(r"[?&]+$", "", key)
        s = _size_score_from_url(u)

        if key not in key_to_index:
            key_to_index[key] = len(order)
            key_to_score[key] = s
            order.append(u)
        else:
            if s > key_to_score.get(key, -1):
                idx = key_to_index[key]
                order[idx] = u
                key_to_score[key] = s

    return order


def extract_images_from_slick(driver: webdriver.Chrome) -> List[str]:
    """
    Extract ALL images from the slider/gallery.
    We prefer slick containers but fall back to other common gallery selectors.
    """
    selectors = [
        ".slick-track img",
        ".slick-slide img",
        "[class*='slick'] img",
        ".product-slider img",
        ".product-gallery img",
        ".gallery img",
    ]

    candidates: List[str] = []
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            continue

        for el in els:
            try:
                for attr in ("src", "data-lazy", "data-src", "data-original"):
                    v = el.get_attribute(attr)
                    if v:
                        candidates.append(v)
                # Sometimes images are in inline styles.
                style = el.get_attribute("style") or ""
                m = re.search(r'url\\([\"\\\']?([^\"\\\')]+)', style)
                if m:
                    candidates.append(m.group(1))
            except Exception:
                continue

        # If we found images in a slick selector, stop early (we already have the slider set).
        if sel in (".slick-track img", ".slick-slide img", "[class*='slick'] img") and candidates:
            break

    # Filter out obvious non-product assets.
    filtered = []
    for u in candidates:
        ul = (u or "").lower()
        if any(x in ul for x in ["logo", "icon", "favicon", "sprite", "loading", "spinner"]):
            continue
        filtered.append(u)

    return _dedupe_preserve_order_keep_best(filtered)


def extract_video_link(driver: webdriver.Chrome) -> str:
    # Explicit requirement: if there is id="sliderVideo" extract its link.
    try:
        vid = driver.find_elements(By.CSS_SELECTOR, "#sliderVideo")
        if vid:
            el = vid[0]
            for attr in ("src", "href", "data-src"):
                v = el.get_attribute(attr)
                if v:
                    return _abs_url(v, base=BASE_URL)
            # iframe inside
            try:
                iframe = el.find_elements(By.CSS_SELECTOR, "iframe[src]")
                if iframe:
                    return _abs_url(iframe[0].get_attribute("src"), base=BASE_URL)
            except Exception:
                pass
    except Exception:
        pass

    # Fallback: any iframe with video hosts
    try:
        ifr = driver.find_elements(By.CSS_SELECTOR, "iframe[src]")
        for el in ifr:
            src = el.get_attribute("src") or ""
            if any(x in src.lower() for x in ["youtube", "youtu.be", "vimeo"]):
                return src
    except Exception:
        pass

    return ""


def extract_description(driver: webdriver.Chrome) -> str:
    """
    Click the 'التفاصيل الفنية' header if present, then extract its text.
    On this site the body text typically includes markers:
    'التفاصيل الفنية' ... 'قيم المنتج' or 'منتجات ذات الصلة'
    """
    label = "التفاصيل الفنية"
    header_el = None
    try:
        els = driver.find_elements(By.XPATH, f"//*[normalize-space()='{label}']")
        if els:
            header_el = els[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", header_el)
            try:
                header_el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", header_el)
            time.sleep(0.6)
    except Exception:
        header_el = None

    # Best-effort: try to read the content area near the clicked header in the DOM.
    if header_el is not None:
        try:
            txt = driver.execute_script(
                """
                const el = arguments[0];
                const pickText = (node) => (node && (node.innerText || node.textContent) || '').trim();

                // Common accordion/tab patterns:
                // - header is a <button>/<a>, content is next sibling or in parent sibling
                const direct = pickText(el.nextElementSibling);
                if (direct && direct.length > 40) return direct;

                const parentNext = pickText(el.parentElement && el.parentElement.nextElementSibling);
                if (parentNext && parentNext.length > 40) return parentNext;

                const wrap = el.closest('section,article,div,li');
                if (wrap) {
                  // Look for the largest text block inside the wrapper excluding the header itself
                  const blocks = Array.from(wrap.querySelectorAll('div,ul,ol,p')).map(n => pickText(n)).filter(t => t && t.length > 20);
                  blocks.sort((a,b) => b.length - a.length);
                  if (blocks.length) return blocks[0];
                }
                return '';
                """,
                header_el,
            )
            txt = (txt or "").strip()
            if txt:
                # Remove the header label if included at the beginning.
                txt = re.sub(rf"^\s*{re.escape(label)}\s*", "", txt)
                return txt.strip()
        except Exception:
            pass

    # Fallback: extract from whole body text by section markers.
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        body_text = ""

    body_text = body_text.replace("\r\n", "\n")
    if label not in body_text:
        return ""

    after = body_text.split(label, 1)[1]
    for stop in ("قيم المنتج", "منتجات ذات الصلة", "منتجات ذات الصله", "عروض مميزة"):
        if stop in after:
            after = after.split(stop, 1)[0]
    after = after.strip()
    lines = [ln.strip() for ln in after.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def extract_name(driver: webdriver.Chrome) -> str:
    # Exact requirement: class="item_title" inside <h4 inside class="text"
    for sel in (".text h4.item_title", "h4.item_title", ".item_title"):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                txt = _normalize_ws(el.text)
                if txt and len(txt) >= 2:
                    return txt
        except Exception:
            continue

    # Fallback: first meaningful heading on page.
    for sel in ("h1", "h2", "h3", "h4"):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                txt = _normalize_ws(el.text)
                if txt and len(txt) >= 3 and "تفاصيل المنتج" not in txt and "الرئيسية" not in txt:
                    return txt
        except Exception:
            continue
    return ""


def scrape_product(driver: webdriver.Chrome, product_url: str, wait_timeout: int = 25) -> ProductResult:
    product_id = _extract_product_id(product_url)
    try:
        driver.get(product_url)
    except Exception:
        # Retry once
        time.sleep(1.0)
        driver.get(product_url)

    # Wait for body to exist.
    try:
        WebDriverWait(driver, wait_timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except Exception:
        pass

    # Give slick/gallery JS a moment.
    time.sleep(1.2)

    name = extract_name(driver)
    desc = extract_description(driver)
    images = extract_images_from_slick(driver)
    video = extract_video_link(driver)

    status = "SUCCESS"
    if not desc and not images and not video:
        status = "FAILED"
    elif not desc or not images:
        status = "PARTIAL"

    return ProductResult(
        product_url=product_url,
        product_id=product_id,
        name=name,
        description=desc,
        images=images,
        video_url=video,
        status=status,
    )


def ensure_parent_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Platin scraper: scrape technical details, slider images, and optional sliderVideo from Platin subcategory listing pages."
    )
    ap.add_argument("--page", action="append", dest="pages", default=[], help="Listing page URL (repeatable)")
    ap.add_argument("--subcat", type=int, default=34, help="productsOfSubCategories/<subcat> id (default: 34)")
    ap.add_argument("--pages-count", type=int, default=2, help="How many pages to scrape for the subcategory (default: 2)")
    ap.add_argument("--out", required=True, help="Output Excel file path (.xlsx)")
    ap.add_argument("--headful", action="store_true", help="Run Chrome headed")
    ap.add_argument("--limit", type=int, default=0, help="Limit number of products (0 = no limit)")
    args = ap.parse_args()

    if args.pages:
        listing_urls = args.pages
    else:
        pages_count = max(1, int(args.pages_count or 1))
        listing_urls = [
            f"https://platin-imex.co/ar/productsOfSubCategories/{args.subcat}?page={p}"
            for p in range(1, pages_count + 1)
        ]

    links = collect_product_links(listing_urls)
    if args.limit and args.limit > 0:
        links = links[: args.limit]

    ensure_parent_dir(args.out)

    driver = build_driver(headful=args.headful)
    rows = []

    try:
        for i, url in enumerate(links, 1):
            print(f"[{i}/{len(links)}] {url}")
            try:
                res = scrape_product(driver, url)
            except Exception as e:
                res = ProductResult(
                    product_url=url,
                    product_id=_extract_product_id(url),
                    name="",
                    description="",
                    images=[],
                    video_url="",
                    status=f"ERROR: {e.__class__.__name__}",
                )

            rows.append(
                {
                    "product_id": res.product_id,
                    "product_url": res.product_url,
                    "item_title": res.name,
                    "description": res.description,
                    "images": ";".join(res.images),
                    "image_count": len(res.images),
                    "video_url": res.video_url,
                    "status": res.status,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

    finally:
        driver.quit()

    df = pd.DataFrame(rows)
    df.to_excel(args.out, index=False)
    print(f"Saved: {args.out} ({len(df)} rows)")


if __name__ == "__main__":
    main()

