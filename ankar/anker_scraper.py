"""
Scraper for Anker Singapore Shopify storefront.

Reads SKUs from `ankar/New products Anker (2).xlsx`, searches the store,
grabs the first product result, and extracts its title, description, and
image URLs (joined by ';'). Results are saved to
`ankar/anker_products_scraped.xlsx`.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests import Response
import json
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs

BASE_URL = "https://anker.com.sg"
SEARCH_PATH = "/search"
INPUT_PATH = r"D:\JafarShop\Scrapers\ankar\Anker.xlsx"
OUTPUT_PATH = "anker_products_scraped.xlsx"
REQUEST_TIMEOUT = 20
SLEEP_SECONDS = 1.0

UK_SEARCH_API = "https://www.anker.com/api/multipass/rainbowbridge/search"
UK_PRODUCT_BASE = "https://www.anker.com/uk/products/"
UK_SHOPIFY_DOMAIN = "ankeruk.myshopify.com"
UK_LOCALE = "en-gb"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


@dataclass
class ProductResult:
    sku: str
    product_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    images: Optional[List[str]] = None
    status: str = "ok"
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "SKU": self.sku,
            "Product URL": self.product_url or "",
            "Title": self.title or "",
            "Description": self.description or "",
            "Images": ";".join(self.images or []),
            "Status": self.status,
            "Error": self.error or "",
        }


class AnkerScraper:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch(self, url: str, **kwargs) -> Response:
        response = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response

    def search_product_url(self, sku: str) -> Optional[str]:
        search_variants = [
            {"q": sku, "type": "product"},
            {"q": f"sku:{sku}", "type": "product"},
            {"q": sku},
        ]
        for params in search_variants:
            query = urlencode(params, doseq=True)
            url = f"{BASE_URL}{SEARCH_PATH}?{query}"
            try:
                response = self.fetch(url)
            except requests.RequestException as exc:
                logging.warning("Search request failed for %s: %s", sku, exc)
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            card = soup.select_one(".card.card--product")
            if not card:
                continue
            link = card.select_one("a.full-unstyled-link")
            if link and link.has_attr("href"):
                href = link["href"].split("?")[0]
                return urljoin(BASE_URL, href)
        return self.search_product_url_uk(sku)

    def search_product_url_uk(self, sku: str) -> Optional[str]:
        params = {
            "q": sku,
            "query_type": "product",
            "page_size": 5,
            "current_page": 1,
            "shopify_domain": UK_SHOPIFY_DOMAIN,
        }
        headers = {**self.session.headers, "current-language": UK_LOCALE}
        try:
            response = self.session.get(
                UK_SEARCH_API,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logging.warning("UK search request failed for %s: %s", sku, exc)
            return None

        try:
            payload = response.json()
        except ValueError as exc:
            logging.warning("Invalid UK search JSON for %s: %s", sku, exc)
            return None

        data = (
            payload.get("data", {})
            .get("data", {})
            .get("items", [])
        )
        if not isinstance(data, list):
            logging.debug("Unexpected UK search schema for %s: %s", sku, data)
            return None
        for item in data:
            if isinstance(item, str) and item:
                return urljoin(UK_PRODUCT_BASE, item)
        return None

    def scrape_product_page(self, url: str) -> ProductResult:
        try:
            response = self.fetch(url)
        except requests.RequestException as exc:
            return ProductResult(
                sku="",
                product_url=url,
                status="error",
                error=f"Product page request failed: {exc}",
            )

        soup = BeautifulSoup(response.text, "html.parser")

        title = self._extract_title(soup)
        description = self._extract_description(soup)
        images = self._extract_images(soup)

        return ProductResult(
            sku="",
            product_url=url,
            title=title,
            description=description,
            images=images,
        )

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> Optional[str]:
        title_el = soup.select_one("h1.product__title")
        if title_el:
            return title_el.get_text(" ", strip=True)
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()
        return None

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> Optional[str]:
        desc_el = soup.select_one(".product__description")
        if desc_el:
            # Preserve basic HTML structure for downstream formatting.
            return desc_el.decode_contents().strip()
        jsonld = soup.find_all("script", type="application/ld+json")
        for script in jsonld:
            raw = script.string
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, dict) and parsed.get("@type") == "Product":
                description = parsed.get("description")
                if description:
                    return description.strip()
        return None

    @staticmethod
    def _extract_images(soup: BeautifulSoup) -> List[str]:
        images_map: "OrderedDict[str, Tuple[str, int]]" = OrderedDict()
        order: List[str] = []
        selectors = [
            "media-gallery img",
            ".product__media-item img",
            ".slider-component img",
            "img.product__media-item",
        ]
        for selector in selectors:
            for img in soup.select(selector):
                src = AnkerScraper._first_present_attr(
                    img, ("data-src", "data-srcset", "src", "data-original", "data-lazy")
                )
                if not src:
                    continue
                if " " in src:
                    src = src.split()[0]
                if "," in src and "http" not in src:
                    src = src.split(",")[0]
                src = src.strip()
                if not src:
                    continue
                src = AnkerScraper._normalize_image_url(src)
                AnkerScraper._register_image(images_map, order, src)
        if not images_map:
            # Fallback to JSON-LD images
            for script in soup.find_all("script", type="application/ld+json"):
                raw = script.string
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except Exception:
                    continue
                if isinstance(parsed, dict) and parsed.get("@type") == "Product":
                    imgs = parsed.get("image")
                    if isinstance(imgs, str):
                        imgs = [imgs]
                    if isinstance(imgs, Iterable):
                        for item in imgs:
                            if not isinstance(item, str):
                                continue
                            normalized = AnkerScraper._normalize_image_url(item)
                            AnkerScraper._register_image(images_map, order, normalized)
        return [images_map[key][0] for key in order if key in images_map]

    @staticmethod
    def _first_present_attr(element, attrs: Iterable[str]) -> Optional[str]:
        for attr in attrs:
            if element.has_attr(attr):
                return element[attr]
        return None

    @staticmethod
    def _normalize_image_url(url: str) -> Optional[str]:
        url = url.strip()
        if not url:
            return None
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = urljoin(BASE_URL, url)
        return url

    @staticmethod
    def _register_image(
        images_map: "OrderedDict[str, Tuple[str, int]]",
        order: List[str],
        url: Optional[str],
    ) -> None:
        if not url:
            return
        key, size = AnkerScraper._image_key_and_size(url)
        if not key:
            return
        if key not in images_map:
            images_map[key] = (url, size)
            order.append(key)
            return
        _, existing_size = images_map[key]
        if size > existing_size:
            images_map[key] = (url, size)

    @staticmethod
    def _image_key_and_size(url: str) -> Tuple[str, int]:
        split = urlsplit(url)
        key = f"{split.netloc}{split.path}"
        if not key:
            key = url
        query_params = parse_qs(split.query)
        width = AnkerScraper._extract_int_from_query(query_params, ("width", "w", "size"))
        if width == 0:
            width = AnkerScraper._extract_int_from_query(query_params, ("height", "h"))
        return key, width

    @staticmethod
    def _extract_int_from_query(
        params: Dict[str, List[str]], keys: Iterable[str]
    ) -> int:
        for key in keys:
            values = params.get(key)
            if not values:
                continue
            value = values[0]
            try:
                return int(float(value))
            except (TypeError, ValueError):
                continue
        return 0


def run() -> None:
    scraper = AnkerScraper()
    try:
        source_df = pd.read_excel(INPUT_PATH)
    except Exception as exc:
        raise SystemExit(f"Failed to read input Excel file: {exc}") from exc

    # Normalize column names and find SKU column
    source_df.columns = [str(col).replace("\n", " ").strip() for col in source_df.columns]
    sku_col = None
    for col in source_df.columns:
        if "SKU" in col.upper() or "MODEL" in col.upper():
            sku_col = col
            break
    
    if not sku_col:
        raise SystemExit("Input Excel must have a 'SKU' or 'Model' column.")

    results: List[dict] = []
    for sku_value in source_df[sku_col]:
        sku = str(sku_value).strip()
        if not sku or sku.lower() == "nan":
            continue
        logging.info("Processing SKU %s", sku)
        result = ProductResult(sku=sku)
        product_url = scraper.search_product_url(sku)
        if not product_url:
            result.status = "not_found"
            result.error = "No product results found"
            results.append(result.as_dict())
            time.sleep(SLEEP_SECONDS)
            continue

        page_data = scraper.scrape_product_page(product_url)
        result.product_url = product_url
        if page_data.status == "error":
            result.status = "error"
            result.error = page_data.error
        else:
            result.title = page_data.title
            result.description = page_data.description
            result.images = page_data.images
        results.append(result.as_dict())
        time.sleep(SLEEP_SECONDS)

    output_df = pd.DataFrame(results)
    output_df.to_excel(OUTPUT_PATH, index=False)
    logging.info("Saved results to %s", OUTPUT_PATH)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()

