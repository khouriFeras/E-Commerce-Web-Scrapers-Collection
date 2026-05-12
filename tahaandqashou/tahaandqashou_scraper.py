#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scrape Taha & Qashou WooCommerce category listings: skip OOS cards, paginate, PDP title/price/description/images."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    from markdownify import markdownify as to_markdown  # type: ignore
except Exception:  # pragma: no cover
    to_markdown = None  # type: ignore[assignment]

BASE_URL = "https://tahaandqashou.net"
DEFAULT_CATEGORY = "https://tahaandqashou.net/ar/product-category/sprayers-ar/"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR / "tahaandqashou_sprayers-ar.xlsx"
REQUEST_TIMEOUT = 45
OOS_PHRASE = "نفد من المخزون"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

LISTING_ROOT_SELECTOR = "div.listing-products.container.pt-3.pb-3"

logger = logging.getLogger(__name__)


def normalize_category_base(url: str) -> str:
    u = url.strip()
    if not u.endswith("/"):
        u += "/"
    return u


def category_page_url(category_base: str, page_num: int) -> str:
    if page_num <= 1:
        return category_base
    return f"{category_base}page/{page_num}/"


def extract_text(el: Optional[BeautifulSoup]) -> str:
    if not el:
        return ""
    return el.get_text(" ", strip=True)


def extract_description(soup: BeautifulSoup) -> str:
    description_el = soup.select_one(".woocommerce-Tabs-panel--description")
    if not description_el:
        description_el = soup.select_one(".woocommerce-product-details__short-description")
    if not description_el:
        return ""

    html = description_el.decode_contents().strip()
    if not html:
        return ""
    if to_markdown is not None:
        markdown = to_markdown(html, heading_style="ATX")
        return markdown.strip()
    fallback = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    return fallback.strip()


def extract_images(product: BeautifulSoup, base: str) -> List[str]:
    image_urls: List[str] = []
    for anchor in product.select(".woocommerce-product-gallery__image a[href]"):
        href = anchor.get("href")
        if not href:
            continue
        full_url = urljoin(base, href.strip())
        if full_url not in image_urls:
            image_urls.append(full_url)

    if image_urls:
        return image_urls

    for img in product.select("img"):
        src = img.get("data-large_image") or img.get("data-src") or img.get("src")
        if not src or src.startswith("data:"):
            continue
        full_url = urljoin(base, src.strip())
        if full_url not in image_urls:
            image_urls.append(full_url)
    return image_urls


def card_is_oos_skip(card: BeautifulSoup) -> bool:
    label = card.select_one(".out-of-stock-label")
    if not label:
        return False
    text = label.get_text(" ", strip=True)
    return OOS_PHRASE in text


def product_link_from_card(card: BeautifulSoup) -> Optional[str]:
    link = card.select_one("a.woocommerce-LoopProduct-link")
    if not link or not link.get("href"):
        return None
    href = link["href"].strip()
    if "add-to-cart" in href:
        return None
    path = urlparse(href).path
    if "/product/" not in path:
        return None
    return href


def fetch(session: requests.Session, url: str) -> Tuple[int, str]:
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    return r.status_code, r.text


def parse_listing_cards(html: str) -> List[BeautifulSoup]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(LISTING_ROOT_SELECTOR)
    if not root:
        return []
    return root.select("li.product")


def scrape_product_page(session: requests.Session, product_url: str) -> Tuple[str, str, str, List[str], str]:
    status_code, html = fetch(session, product_url)
    if status_code != 200:
        return "", "", "", [], f"HTTP_{status_code}"

    soup = BeautifulSoup(html, "html.parser")
    product = soup.select_one("div.product")
    if not product:
        return "", "", "", [], "no_product_container"

    title = extract_text(product.select_one("h1.product_title") or soup.select_one("h1"))
    price = extract_text(product.select_one("p.price") or product.select_one(".price"))
    description = extract_description(soup)
    images = extract_images(product, BASE_URL)

    return title, price, description, images, "ok"


def run_scraper(
    category_url: str,
    out_path: Path,
    pause: float,
    max_pages: int,
    log_skipped: bool,
) -> None:
    category_base = normalize_category_base(category_url)
    session = requests.Session()
    session.headers.update(HEADERS)

    seen_urls: Set[str] = set()
    rows: List[Dict[str, object]] = []

    page_num = 1
    while True:
        if max_pages and page_num > max_pages:
            logger.info("Stopping: reached --max-pages=%s", max_pages)
            break

        url = category_page_url(category_base, page_num)
        logger.info("Listing page %s: %s", page_num, url)

        try:
            status_code, html = fetch(session, url)
        except requests.RequestException as e:
            logger.error("Request failed for %s: %s", url, e)
            break

        if status_code == 404:
            logger.info("Stopping: 404 for %s", url)
            break

        if status_code != 200:
            logger.warning("Skipping page %s (status %s)", page_num, status_code)
            break

        cards = parse_listing_cards(html)
        if not cards:
            logger.info("Stopping: no product cards in listing container (page %s)", page_num)
            break

        for card in cards:
            if card_is_oos_skip(card):
                if log_skipped:
                    pu = product_link_from_card(card) or ""
                    rows.append(
                        {
                            "Product URL": pu,
                            "Title": "",
                            "Price": "",
                            "Description": "",
                            "Images": "",
                            "Listing page": page_num,
                            "Status": "skipped_oos",
                        }
                    )
                continue

            product_url = product_link_from_card(card)
            if not product_url:
                continue
            if product_url in seen_urls:
                continue
            seen_urls.add(product_url)

            time.sleep(pause)
            title, price, description, images, st = scrape_product_page(session, product_url)
            rows.append(
                {
                    "Product URL": product_url,
                    "Title": title,
                    "Price": price,
                    "Description": description,
                    "Images": ";".join(images),
                    "Listing page": page_num,
                    "Status": st,
                }
            )
            logger.info("Scraped %s -> %s", product_url, st)

        page_num += 1

    df = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out_path, index=False)
    logger.info("Wrote %s rows to %s", len(df), out_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Scrape Taha & Qashou category (pagination + PDP).")
    parser.add_argument(
        "--category-url",
        default=DEFAULT_CATEGORY,
        help="Category URL (page 1); pagination uses …/page/N/",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output Excel path",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.75,
        help="Seconds between PDP requests",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Max category pages (0 = unlimited)",
    )
    parser.add_argument(
        "--log-skipped",
        action="store_true",
        help="Append rows for OOS listing cards (skipped_oos, empty title/price)",
    )
    args = parser.parse_args()

    run_scraper(
        category_url=args.category_url,
        out_path=args.out,
        pause=args.pause,
        max_pages=args.max_pages,
        log_skipped=args.log_skipped,
    )


if __name__ == "__main__":
    main()
