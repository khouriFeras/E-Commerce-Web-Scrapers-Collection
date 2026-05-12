"""
Scraper for Saheb Shwar Al Tamimi storefront.

Reads SKUs from `sahebshawar/New products (Al Saheb and Shawar Al Tamimi) (2).xlsx`,
searches by SKU, follows the matching product page, and extracts product images
and markdown-formatted descriptions. Results are written to
`sahebshawar/sahebshawar_scraped.xlsx`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    from markdownify import markdownify as to_markdown  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    to_markdown = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

BASE_URL = "https://sahebshawar.com"
INPUT_PATH = Path("sahebshawar") / "New products (Al Saheb and Shawar Al Tamimi) (2).xlsx"
OUTPUT_PATH = Path("sahebshawar") / "sahebshawar_scraped.xlsx"
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


@dataclass
class ProductRecord:
    sku: str
    product_url: str = ""
    title: str = ""
    price: str = ""
    description: str = ""
    images: Optional[List[str]] = None
    status: str = "ok"
    error: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "Model": self.sku,
            "Scraped Title": self.title,
            "Scraped Price": self.price,
            "Product URL": self.product_url,
            "Description": self.description,
            "Images": ";".join(self.images or []),
            "Status": self.status,
            "Error": self.error,
        }


class SahebShawarScraper:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response

    def find_product_url(self, sku: str) -> Tuple[Optional[str], str]:
        search_url = f"{BASE_URL}/?s={quote_plus(sku)}"
        logger.debug("Searching for SKU %s at %s", sku, search_url)
        try:
            response = self.fetch(search_url)
        except requests.RequestException as exc:
            raise RuntimeError(f"Search request failed: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        article = self._match_article(soup, sku)
        if not article:
            return None, search_url

        link = article.select_one("h2.entry-title a") or article.select_one(
            "a.entry-featured-image-url"
        )
        if link and link.get("href"):
            return urljoin(BASE_URL, link["href"]), search_url
        return None, search_url

    def _match_article(self, soup: BeautifulSoup, sku: str) -> Optional[BeautifulSoup]:
        candidates = soup.select("div.container article.et_pb_post")
        if not candidates:
            return None

        normalized_sku = sku.strip().lower()
        best: Optional[BeautifulSoup] = None
        for article in candidates:
            title_el = article.select_one("h2.entry-title")
            if not title_el:
                continue
            title_text = title_el.get_text(strip=True)
            if not title_text:
                continue
            if title_text.strip().lower() == normalized_sku:
                return article
            if normalized_sku in title_text.strip().lower() and best is None:
                best = article
        return best

    def scrape_product(self, sku: str, product_url: str) -> ProductRecord:
        logger.debug("Fetching product page for %s at %s", sku, product_url)
        try:
            response = self.fetch(product_url)
        except requests.RequestException as exc:
            raise RuntimeError(f"Product page request failed: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        product = soup.select_one("div.product")
        if not product:
            raise RuntimeError("Could not locate product container on page.")

        title = self._extract_text(product.select_one("h1.product_title"))
        price = self._extract_text(product.select_one("p.price"))
        images = self._extract_images(product)
        description = self._extract_description(soup)

        return ProductRecord(
            sku=sku,
            product_url=product_url,
            title=title,
            price=price,
            description=description,
            images=images,
        )

    def _extract_images(self, product: BeautifulSoup) -> List[str]:
        image_urls: List[str] = []
        # Prefer anchor hrefs for full-size images.
        for anchor in product.select(".woocommerce-product-gallery__image a[href]"):
            href = anchor.get("href")
            if not href:
                continue
            full_url = urljoin(BASE_URL, href.strip())
            if full_url not in image_urls:
                image_urls.append(full_url)

        if image_urls:
            return image_urls

        for img in product.select("img"):
            src = img.get("data-large_image") or img.get("data-src") or img.get("src")
            if not src:
                continue
            full_url = urljoin(BASE_URL, src.strip())
            if full_url not in image_urls:
                image_urls.append(full_url)
        return image_urls

    def _extract_description(self, soup: BeautifulSoup) -> str:
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

    @staticmethod
    def _extract_text(element: Optional[BeautifulSoup]) -> str:
        if not element:
            return ""
        return element.get_text(" ", strip=True)


def iter_rows(df: pd.DataFrame) -> Iterable[Tuple[int, Dict[str, str]]]:
    for index, row in df.iterrows():
        yield index, {column: str(row[column]) if pd.notna(row[column]) else "" for column in df.columns}


def build_result_row(
    row_data: Dict[str, str],
    record: ProductRecord,
) -> Dict[str, str]:
    output = dict(row_data)
    output.update(record.as_dict())
    return output


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_PATH}")

    df = pd.read_excel(INPUT_PATH)
    scraper = SahebShawarScraper()

    results: List[Dict[str, str]] = []

    for _, row_data in iter_rows(df):
        sku = row_data.get("Model", "").strip()
        if not sku:
            results.append(
                build_result_row(
                    row_data,
                    ProductRecord(
                        sku="",
                        status="skipped",
                        error="Missing Model value",
                    ),
                )
            )
            continue

        logger.info("Processing SKU %s", sku)

        try:
            product_url, search_url = scraper.find_product_url(sku)
        except RuntimeError as exc:
            logger.warning("Search failed for %s: %s", sku, exc)
            results.append(
                build_result_row(
                    row_data,
                    ProductRecord(
                        sku=sku,
                        status="error",
                        error=str(exc),
                    ),
                )
            )
            continue

        if not product_url:
            message = f"No product match found. Search URL: {search_url}"
            logger.info("%s: %s", sku, message)
            results.append(
                build_result_row(
                    row_data,
                    ProductRecord(
                        sku=sku,
                        status="not_found",
                        error=message,
                    ),
                )
            )
            continue

        try:
            record = scraper.scrape_product(sku, product_url)
        except RuntimeError as exc:
            logger.warning("Product scrape failed for %s: %s", sku, exc)
            results.append(
                build_result_row(
                    row_data,
                    ProductRecord(
                        sku=sku,
                        product_url=product_url,
                        status="error",
                        error=str(exc),
                    ),
                )
            )
            continue

        results.append(build_result_row(row_data, record))

    output_df = pd.DataFrame(results)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_excel(OUTPUT_PATH, index=False)
    logger.info("Saved %s rows to %s", len(output_df), OUTPUT_PATH)


if __name__ == "__main__":
    main()

