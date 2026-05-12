"""
Scraper for Anker Singapore Shopify storefront (anker.com.sg).

Reads SKUs from `ankar/New products Anker (2).xlsx`, searches the store,
verifies the SKU matches on the product page, then extracts title, description,
and image URLs (joined by ';'). Results are saved to
`ankar/anker_products_scraped_sg.xlsx`.
"""
from __future__ import annotations

import logging
import time
import re
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
OUTPUT_PATH = "anker_products_scraped_sg.xlsx"
REQUEST_TIMEOUT = 20
SLEEP_SECONDS = 1.0

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
    found_sku: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "SKU": self.sku,
            "Product URL": self.product_url or "",
            "Title": self.title or "",
            "Description": self.description or "",
            "Images": ";".join(self.images or []),
            "Status": self.status,
            "Error": self.error or "",
            "Found SKU": self.found_sku or "",
        }


class AnkerScraperSG:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch(self, url: str, **kwargs) -> Response:
        response = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response

    def search_product_url(self, sku: str) -> Optional[str]:
        """Search for product URL on Singapore site."""
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
        return None

    def extract_sku_from_page(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract SKU from product page using multiple methods."""
        # Method 1: JSON-LD structured data
        jsonld_scripts = soup.find_all("script", type="application/ld+json")
        for script in jsonld_scripts:
            raw = script.string
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    # Check for Product schema
                    if parsed.get("@type") == "Product":
                        sku = parsed.get("sku")
                        if sku:
                            return str(sku).strip()
                    # Check for ItemList with products
                    if parsed.get("@type") == "ItemList":
                        items = parsed.get("itemListElement", [])
                        for item in items:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                sku = item.get("sku")
                                if sku:
                                    return str(sku).strip()
            except (json.JSONDecodeError, Exception):
                continue

        # Method 2: Meta tags
        meta_sku = soup.find("meta", {"property": "product:sku"})
        if meta_sku and meta_sku.get("content"):
            return meta_sku["content"].strip()
        
        meta_sku2 = soup.find("meta", {"name": "sku"})
        if meta_sku2 and meta_sku2.get("content"):
            return meta_sku2["content"].strip()

        # Method 3: HTML elements with SKU
        sku_selectors = [
            ".product__sku",
            ".sku",
            ".product-sku",
            "[data-sku]",
            ".variant-sku",
        ]
        for selector in sku_selectors:
            element = soup.select_one(selector)
            if element:
                # Try text content first
                text = element.get_text(strip=True)
                if text:
                    return text
                # Try data attribute
                if element.has_attr("data-sku"):
                    return element["data-sku"].strip()

        # Method 4: Search in script tags for SKU patterns
        scripts = soup.find_all("script")
        for script in scripts:
            if not script.string:
                continue
            # Look for SKU patterns in JavaScript variables
            patterns = [
                r'sku["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'"sku"\s*:\s*"([^"]+)"',
                r"'sku'\s*:\s*'([^']+)'",
                r'productSku["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, script.string, re.IGNORECASE)
                if match:
                    sku = match.group(1).strip()
                    if sku:
                        return sku

        # Method 5: Search in page text for SKU-like patterns
        page_text = soup.get_text()
        # Look for "SKU:" or "Model:" followed by alphanumeric code
        sku_patterns = [
            r'(?:SKU|Model|Item\s*#?)[\s:]+([A-Z0-9\-]+)',
            r'([A-Z]\d{4}[A-Z]\d{2}[A-Z]\d{2})',  # Pattern like A1638H21
        ]
        for pattern in sku_patterns:
            matches = re.findall(pattern, page_text, re.IGNORECASE)
            if matches:
                return matches[0].strip()

        return None

    def normalize_sku(self, sku: str) -> str:
        """Normalize SKU for comparison (remove spaces, convert to uppercase)."""
        return re.sub(r'[\s\-_]', '', sku.upper())

    def verify_sku_match(self, target_sku: str, found_sku: Optional[str]) -> bool:
        """Verify if found SKU matches target SKU."""
        if not found_sku:
            return False
        return self.normalize_sku(target_sku) == self.normalize_sku(found_sku)

    def scrape_product_page(self, url: str, target_sku: str) -> ProductResult:
        """Scrape product page and verify SKU matches."""
        try:
            response = self.fetch(url)
        except requests.RequestException as exc:
            return ProductResult(
                sku=target_sku,
                product_url=url,
                status="error",
                error=f"Product page request failed: {exc}",
            )

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract and verify SKU
        found_sku = self.extract_sku_from_page(soup)
        if not self.verify_sku_match(target_sku, found_sku):
            return ProductResult(
                sku=target_sku,
                product_url=url,
                status="sku_mismatch",
                error=f"SKU mismatch: expected {target_sku}, found {found_sku or 'none'}",
                found_sku=found_sku,
            )

        # Extract product data
        title = self._extract_title(soup)
        description = self._extract_description(soup)
        images = self._extract_images(soup)

        return ProductResult(
            sku=target_sku,
            product_url=url,
            title=title,
            description=description,
            images=images,
            found_sku=found_sku,
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
        # Try truncate-text__content rte first (current anker.com.sg product description container)
        for selector in (".truncate-text__content.rte", ".truncate-text__content", ".product__description"):
            desc_el = soup.select_one(selector)
            if desc_el:
                raw = desc_el.decode_contents().strip()
                if raw and len(raw) > 20:
                    return raw
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
                src = AnkerScraperSG._first_present_attr(
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
                src = AnkerScraperSG._normalize_image_url(src)
                AnkerScraperSG._register_image(images_map, order, src)
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
                            normalized = AnkerScraperSG._normalize_image_url(item)
                            AnkerScraperSG._register_image(images_map, order, normalized)
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
        key, size = AnkerScraperSG._image_key_and_size(url)
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
        width = AnkerScraperSG._extract_int_from_query(query_params, ("width", "w", "size"))
        if width == 0:
            width = AnkerScraperSG._extract_int_from_query(query_params, ("height", "h"))
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
    scraper = AnkerScraperSG()
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

        page_data = scraper.scrape_product_page(product_url, sku)
        result.product_url = product_url
        result.status = page_data.status
        result.error = page_data.error
        result.found_sku = page_data.found_sku
        
        if page_data.status == "error" or page_data.status == "sku_mismatch":
            # Don't extract data if SKU doesn't match or there's an error
            pass
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






