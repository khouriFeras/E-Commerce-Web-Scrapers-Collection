from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


BASE_URL = "https://shop.cosmoplast.com"
SEARCH_URL = BASE_URL + "/search?type=product&q={q}&options%5Bprefix%5D=last"
MAX_IMAGES = 5
REQUEST_TIMEOUT = 30
SLEEP_SECONDS = 0.35


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin(BASE_URL, url)
    return url


def dedupe_keep_order(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def split_search_terms(raw_value: object) -> List[str]:
    text = str(raw_value or "").strip()
    if not text or text.lower() == "nan":
        return []
    parts = [p.strip() for p in re.split(r"[;\n\r]+", text) if p.strip()]
    return dedupe_keep_order(parts or [text])


def has_existing_imgs(raw_value: object) -> bool:
    text = str(raw_value or "").strip()
    return bool(text and text.lower() != "nan")


def is_valid_product_image(url: str) -> bool:
    low = url.lower()
    if re.search(r"(placeholder|blank|spacer|logo)", low):
        return False
    if "microsoftteams-image" in low:
        return False

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    try:
        width = int((qs.get("width") or ["0"])[0])
    except Exception:
        width = 0
    try:
        height = int((qs.get("height") or ["0"])[0])
    except Exception:
        height = 0

    if width and width < 200:
        return False
    if height and height < 200:
        return False
    return True


def extract_first_product_url(search_html: str) -> Optional[str]:
    soup = BeautifulSoup(search_html, "html.parser")
    links: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/products/" not in href:
            continue
        full = normalize_url(href.split("?")[0])
        if full:
            links.append(full)
    links = dedupe_keep_order(links)
    return links[0] if links else None


def extract_images_from_ld_json(product_html: str) -> List[str]:
    soup = BeautifulSoup(product_html, "html.parser")
    image_urls: List[str] = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") != "Product":
                continue
            images = node.get("image")
            if isinstance(images, list):
                image_urls.extend(str(x) for x in images if x)
            elif isinstance(images, str):
                image_urls.append(images)

    normalized = [normalize_url(u) for u in image_urls if normalize_url(u)]
    filtered = [u for u in normalized if is_valid_product_image(u)]
    return dedupe_keep_order(filtered)


def extract_images_fallback(product_html: str) -> List[str]:
    soup = BeautifulSoup(product_html, "html.parser")
    urls: List[str] = []

    for img in soup.find_all("img"):
        for attr in ("src", "data-src"):
            value = img.get(attr)
            if not value:
                continue
            full = normalize_url(value)
            if "cdn.shopify.com" not in full and "/files/" not in full:
                continue
            if not is_valid_product_image(full):
                continue
            urls.append(full)

    return dedupe_keep_order(urls)


def fetch_images_for_product_name(session: requests.Session, product_name: str) -> List[str]:
    q = quote_plus(str(product_name).strip())
    search_url = SEARCH_URL.format(q=q)
    search_resp = session.get(search_url, timeout=REQUEST_TIMEOUT)
    search_resp.raise_for_status()

    product_url = extract_first_product_url(search_resp.text)
    if not product_url:
        return []

    product_resp = session.get(product_url, timeout=REQUEST_TIMEOUT)
    product_resp.raise_for_status()

    images = extract_images_from_ld_json(product_resp.text)
    if not images:
        images = extract_images_fallback(product_resp.text)
    return images[:MAX_IMAGES]


def main() -> None:
    ap = argparse.ArgumentParser(description="Cosmoplast image scraper by product name")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", required=True, help="Output Excel file")
    ap.add_argument("--sku-col", dest="sku_col", default="Product Name", help="Product name column (default: Product Name)")
    args = ap.parse_args()

    excel_path = Path(args.inp)
    if not excel_path.exists():
        raise FileNotFoundError(f"File not found: {excel_path}")

    df = pd.read_excel(excel_path)
    if args.sku_col not in df.columns:
        raise ValueError(f"Column `{args.sku_col}` not found. Available: {list(df.columns)}")

    image_cols = [col for col in df.columns if str(col).strip().lower().startswith("image src")]
    if "imgs" not in df.columns:
        df["imgs"] = ""

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )

    updated = 0
    skipped_existing = 0
    for idx, row in df.iterrows():
        if has_existing_imgs(row.get("imgs", "")):
            skipped_existing += 1
            print(f"[SKIP] Row {idx + 2}: imgs already has value")
            continue

        product_names = split_search_terms(row.get(args.sku_col, ""))
        if not product_names:
            continue

        images: List[str] = []
        selected_name = product_names[0]
        for name in product_names:
            selected_name = name
            try:
                images = fetch_images_for_product_name(session, name)
            except Exception as exc:
                print(f"[WARN] Row {idx + 2} product_name={name}: {exc}")
                images = []
            if images:
                break

        if not images:
            print(f"[MISS] Row {idx + 2} product_names={product_names}: no product/images found")
            continue

        df.at[idx, "imgs"] = ";".join(images)

        updated += 1
        print(f"[OK] Row {idx + 2} product_name={selected_name}: {len(images)} image(s)")
        time.sleep(SLEEP_SECONDS)

    if image_cols:
        df = df.drop(columns=image_cols)

    df.to_excel(args.out, index=False)
    print(f"Done. Updated rows: {updated} | Skipped existing imgs: {skipped_existing}")


if __name__ == "__main__":
    main()
