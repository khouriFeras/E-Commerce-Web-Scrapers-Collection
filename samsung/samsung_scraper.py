#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Samsung product-page scraper.

Reads URLs from Links.txt, resolves Bing redirect links to direct product URLs,
and scrapes:
- title
- merged description from feature-benefit and product-spec blocks
- SKU
- price
- stock note when no longer available
- highest-resolution image URLs from gallery thumbnails
"""

from __future__ import annotations

import argparse
import base64
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "Links.txt"
DEFAULT_OUTPUT = SCRIPT_DIR / "samsung_scraped.xlsx"


def build_driver(headless: bool = True) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1600,1400")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def read_links(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Links file not found: {path}")
    urls: List[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line:
            urls.append(line)
    return urls


def _safe_b64_decode(value: str) -> str:
    payload = value.strip()
    if payload.startswith("a1"):
        payload = payload[2:]
    # Bing payload is commonly urlsafe base64 without padding.
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload).decode("utf-8", errors="ignore")
        return decoded.strip()
    except Exception:
        return ""


def normalize_input_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "bing.com" in host:
        q = parse_qs(parsed.query)
        token = q.get("u", [""])[0]
        token = unquote(token).strip()
        if token:
            if token.startswith(("http://", "https://")):
                return token
            decoded = _safe_b64_decode(token)
            if decoded.startswith(("http://", "https://")):
                return decoded
    return url


def safe_first_text(driver: webdriver.Chrome, selector: str) -> str:
    try:
        return clean_text(driver.find_element(By.CSS_SELECTOR, selector).text)
    except NoSuchElementException:
        return ""


def safe_all_text(driver: webdriver.Chrome, selector: str) -> List[str]:
    out: List[str] = []
    try:
        for e in driver.find_elements(By.CSS_SELECTOR, selector):
            txt = clean_text(e.text)
            if txt:
                out.append(txt)
    except Exception:
        return out
    return out


def extract_sku(driver: webdriver.Chrome) -> str:
    sku_selectors = [
        ".pdd39-anchor-nav__info-sku",
        "[class*='info-sku']",
        "[data-testid*='sku']",
    ]

    def _normalize_sku_text(raw: str) -> str:
        txt = clean_text(raw)
        if not txt:
            return ""
        txt = re.sub(r"^\s*(?:sku|model\s*code)\s*[:#]?\s*", "", txt, flags=re.I).strip()
        m = re.search(r"([A-Z0-9][A-Z0-9._/\-]{3,})", txt, flags=re.I)
        return (m.group(1) if m else txt).strip()

    for selector in sku_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            elements = []
        for el in elements:
            candidates = [
                el.text or "",
                el.get_attribute("textContent") or "",
                el.get_attribute("innerText") or "",
            ]
            for raw in candidates:
                sku = _normalize_sku_text(raw)
                if sku:
                    return sku

    body_text = clean_text(driver.find_element(By.TAG_NAME, "body").text)
    patterns = [
        r"\bsku\b\s*[:#]?\s*([A-Z0-9._/\-]{3,})",
        r"\bmodel\s*code\b\s*[:#]?\s*([A-Z0-9._/\-]{3,})",
    ]
    for pat in patterns:
        m = re.search(pat, body_text, re.I)
        if m:
            return clean_text(m.group(1))

    # Samsung model-code fallback (e.g. DV90T6240LX/FH) even without SKU label.
    m = re.search(r"\b([A-Z]{2,}[A-Z0-9]{6,}/[A-Z0-9]{2,4})\b", body_text, re.I)
    if m:
        return clean_text(m.group(1)).upper()

    # URL fallback when path ends with model slug like "...-dv90t6240lx-fh/".
    try:
        path = urlparse(driver.current_url or "").path.lower().strip("/")
        tail = path.split("/")[-1] if path else ""
        m2 = re.search(r"-([a-z0-9]{7,})-([a-z0-9]{2,4})$", tail)
        if m2:
            return f"{m2.group(1).upper()}/{m2.group(2).upper()}"
    except Exception:
        pass
    return ""


def extract_title(driver: webdriver.Chrome) -> str:
    selectors = [
        ".pdd39-anchor-nav__headline-wrap",
        "h1",
        "main h1",
        ".pd-buying__title",
        ".product-title",
        "h2",
    ]
    bad_titles = {
        "samsung business solutions | samsung business jordan",
        "samsung",
    }
    for selector in selectors:
        for text in safe_all_text(driver, selector):
            t = clean_text(text)
            if len(t) < 8:
                continue
            if t.lower() in bad_titles:
                continue
            return t
    return ""


def parse_srcset_best(srcset: str) -> Optional[str]:
    srcset = (srcset or "").strip()
    if not srcset:
        return None
    best_url: Optional[str] = None
    best_score = -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0].strip()
        score = 0
        if len(bits) > 1:
            m = re.match(r"(\d+)w$", bits[1].strip())
            if m:
                score = int(m.group(1))
        if score > best_score:
            best_score = score
            best_url = url
    return best_url


def _img_identity(url: str) -> str:
    path = urlparse(url).path.lower()
    name = (path.split("/")[-1] or "").split("?")[0]
    name = re.sub(r"-thumb-\d+(?=$|\.)", "", name)
    name = re.sub(r"-\d+(?=$|\.)", "", name)
    name = re.sub(r"-\d+x\d+(?=$|\.)", "", name)
    return name or path


def _size_score(url: str) -> int:
    u = url.lower()
    score = 0
    for m in re.finditer(r"(\d{2,4})x(\d{2,4})", u):
        w, h = int(m.group(1)), int(m.group(2))
        score = max(score, w * h)
    for m in re.finditer(r"\$(\d{2,4})_(\d{2,4})_[a-z]+\$", u):
        w, h = int(m.group(1)), int(m.group(2))
        score = max(score, w * h)
    for m in re.finditer(r"([?&](?:w|width)=)(\d+)", u):
        w = int(m.group(2))
        score = max(score, w * w)
    if "thumbnail" in u or "thumb" in u:
        score -= 100000
    if "small" in u:
        score -= 50000
    if "large" in u or "original" in u or "high" in u:
        score += 5000
    return score


def absolutize(url: str, page_url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        p = urlparse(page_url)
        return f"{p.scheme}://{p.netloc}{u}"
    return u


def _samsung_image_variants(url: str) -> List[str]:
    u = (url or "").strip()
    if not u or "images.samsung.com/is/image/samsung/" not in u.lower():
        return [u] if u else []
    variants = [u]

    # Keep candidate with no image-style token to avoid hard thumbnail sizes.
    no_token = re.sub(r"\?\$[^$]+\$", "", u)
    if no_token != u:
        variants.append(no_token)

    # Promote likely thumbnail file names to base gallery file names.
    unthumb = no_token.replace("-thumb-", "-")
    if unthumb != no_token:
        variants.append(unthumb)

    # Promote to a larger image preset while keeping fallback forms.
    variants.append(re.sub(r"(\?\$[^$]+\$)?$", "?$2000_2000_PNG$", unthumb))

    # Deduplicate while preserving order.
    out: List[str] = []
    seen = set()
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def collect_high_res_images(driver: webdriver.Chrome, page_url: str) -> List[str]:
    candidates: List[str] = []

    thumbs = driver.find_elements(By.CSS_SELECTOR, ".hdd02-gallery__thumbnail")
    for thumb in thumbs:
        nodes = [thumb]
        try:
            nodes.extend(thumb.find_elements(By.CSS_SELECTOR, "img, source, a"))
        except Exception:
            pass
        for node in nodes:
            for attr in (
                "href",
                "src",
                "data-src",
                "data-lazy-src",
                "data-image",
                "data-image-src",
                "data-large-image",
                "data-zoom-image",
                "data-original",
            ):
                val = node.get_attribute(attr) or ""
                if val:
                    candidates.append(absolutize(val, page_url))
            srcset = node.get_attribute("srcset") or ""
            best_srcset = parse_srcset_best(srcset)
            if best_srcset:
                candidates.append(absolutize(best_srcset, page_url))

    # Click thumbnails and capture active/main image each time.
    for thumb in thumbs:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", thumb)
            driver.execute_script("arguments[0].click();", thumb)
            time.sleep(0.12)
        except Exception:
            continue
        for sel in (
            ".hdd02-gallery img",
            ".hdd02-gallery__main img",
            ".hdd02-gallery__visual img",
        ):
            try:
                for img in driver.find_elements(By.CSS_SELECTOR, sel):
                    for attr in ("src", "data-src", "data-large-image", "data-zoom-image", "data-original"):
                        val = img.get_attribute(attr) or ""
                        if val:
                            candidates.append(absolutize(val, page_url))
                    best_srcset = parse_srcset_best(img.get_attribute("srcset") or "")
                    if best_srcset:
                        candidates.append(absolutize(best_srcset, page_url))
            except Exception:
                continue

    best_by_id: Dict[str, tuple[int, str]] = {}
    order: List[str] = []
    for raw in candidates:
        for candidate in _samsung_image_variants(clean_text(raw)):
            u = clean_text(candidate)
            if not u or not u.startswith("http"):
                continue
            key = _img_identity(u)
            if key not in order:
                order.append(key)
            s = _size_score(u)
            prev = best_by_id.get(key)
            if prev is None or s > prev[0]:
                best_by_id[key] = (s, u)

    final_urls = [best_by_id[k][1] for k in order if k in best_by_id]
    return final_urls


def expand_collapsed_specs(driver: webdriver.Chrome, max_rounds: int = 6) -> None:
    for _ in range(max_rounds):
        collapsed = driver.find_elements(
            By.CSS_SELECTOR, ".pdd32-product-spec__inner [aria-expanded='false']"
        )
        if not collapsed:
            return
        changed = False
        for el in collapsed:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.1)
                driver.execute_script("arguments[0].click();", el)
                changed = True
            except Exception:
                try:
                    el.click()
                    changed = True
                except Exception:
                    continue
            time.sleep(0.15)
        if not changed:
            return


def scrape_product(
    driver: webdriver.Chrome,
    wait: WebDriverWait,
    input_url: str,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "input_url": input_url,
        "final_url": "",
        "title": "",
        "description": "",
        "sku": "",
        "price": "",
        "availability_note": "",
        "images": "",
        "status": "ERROR",
        "error": "",
    }

    normalized_url = normalize_input_url(input_url)
    if not normalized_url:
        record["status"] = "BAD_URL"
        record["error"] = "Empty or invalid URL"
        return record

    try:
        driver.get(normalized_url)
        time.sleep(1.0)
    except Exception as e:
        err = str(e)
        # Some Samsung pages keep loading long scripts; keep partial DOM when timeout occurs.
        if "timeout" in err.lower():
            record["error"] = err
            try:
                driver.execute_script("window.stop();")
                time.sleep(0.5)
            except Exception:
                pass
        else:
            record["status"] = "NAV_ERROR"
            record["error"] = err
            return record

    final_url = (driver.current_url or "").strip()
    record["final_url"] = final_url

    try:
        wait.until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    ".pdd39-anchor-nav__headline-wrap, .pdd39-anchor-nav__info-sku, "
                    ".feature-benefit__text-wrap, .pdd32-product-spec__inner",
                )
            )
        )
    except TimeoutException:
        # Continue and attempt best-effort scraping from loaded page.
        pass

    expand_collapsed_specs(driver)

    title = extract_title(driver)
    sku = extract_sku(driver)

    feature_parts = safe_all_text(driver, ".feature-benefit__text-wrap")
    spec_parts = safe_all_text(driver, ".pdd32-product-spec__inner")
    description_chunks = []
    if feature_parts:
        description_chunks.append("\n".join(feature_parts))
    if spec_parts:
        description_chunks.append("\n".join(spec_parts))
    description = "\n\n".join(x for x in description_chunks if x).strip()

    price = safe_first_text(driver, ".pd-buying-price__new-price-currency")
    if not price:
        price = safe_first_text(driver, ".pdd36-recommendation-oos-new__price-current")
    availability_note = ""
    if not price:
        cta_text = safe_first_text(driver, ".pdd39-anchor-nav__cta.pd-buying-price__cta")
        cta_low = cta_text.lower()
        if cta_text:
            if "no longer available" in cta_low:
                availability_note = "no longer available"
            else:
                availability_note = cta_text

    images = collect_high_res_images(driver, final_url or normalized_url)

    record["title"] = title
    record["description"] = description
    record["sku"] = sku
    record["price"] = price
    record["availability_note"] = availability_note
    record["images"] = ";".join(images)
    record["status"] = "OK" if any((title, description, sku, price, images)) else "NO_DATA"
    return record


def run(urls: Iterable[str], output_path: Path, headless: bool, wait_sec: int) -> pd.DataFrame:
    driver = build_driver(headless=headless)
    wait = WebDriverWait(driver, wait_sec)
    rows: List[Dict[str, Any]] = []
    try:
        for idx, url in enumerate(urls, start=1):
            print(f"[{idx}] Scraping: {url}")
            row = scrape_product(driver, wait, url)
            rows.append(row)
            print(
                f"    status={row.get('status')} title={'yes' if row.get('title') else 'no'} "
                f"price={'yes' if row.get('price') else 'no'} images={len((row.get('images') or '').split(';')) if row.get('images') else 0}"
            )
    finally:
        driver.quit()

    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".csv":
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
    else:
        df.to_excel(output_path, index=False)
    return df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Samsung product pages from links list.",
    )
    parser.add_argument(
        "--links",
        default=str(DEFAULT_INPUT),
        help="Path to links text file (default: samsung/Links.txt).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output file path (.xlsx or .csv).",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run with visible browser window (headless by default).",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=25,
        help="Wait timeout in seconds (default: 25).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit of links to scrape (0 means all).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    links_path = Path(args.links)
    output_path = Path(args.output)

    urls = read_links(links_path)
    if args.limit and args.limit > 0:
        urls = urls[: args.limit]

    if not urls:
        raise ValueError(f"No URLs found in: {links_path}")

    print(f"Loaded {len(urls)} URLs from {links_path}")
    df = run(
        urls=urls,
        output_path=output_path,
        headless=not args.headful,
        wait_sec=args.wait,
    )

    ok_count = int((df["status"] == "OK").sum()) if "status" in df.columns else 0
    print(f"Done. Rows={len(df)} OK={ok_count} Output={output_path}")


if __name__ == "__main__":
    main()
