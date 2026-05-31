"""
Scraper for Anker UK Shopify storefront (anker.com/uk).

Reads SKUs from `ankar/New products Anker (2).xlsx`, searches the UK store,
verifies the SKU matches on the product page, then extracts title, description,
and image URLs (joined by ';'). Results are saved to
`ankar/anker_products_scraped_uk.xlsx`.
"""
from __future__ import annotations

import argparse
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

UK_SEARCH_API = "https://www.anker.com/api/multipass/rainbowbridge/search"
UK_PRODUCT_BASE = "https://www.anker.com/uk/products/"
UK_BASE_URL = "https://www.anker.com"
UK_SHOPIFY_DOMAIN = "ankeruk.myshopify.com"
UK_LOCALE = "en-gb"
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


class AnkerScraperUK:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch(self, url: str, **kwargs) -> Response:
        response = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response

    def search_product_url(self, sku: str) -> Optional[str]:
        """Search for product URL on UK site using API."""
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
                src = AnkerScraperUK._first_present_attr(
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
                src = AnkerScraperUK._normalize_image_url(src)
                AnkerScraperUK._register_image(images_map, order, src)
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
                            normalized = AnkerScraperUK._normalize_image_url(item)
                            AnkerScraperUK._register_image(images_map, order, normalized)
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
            url = urljoin(UK_BASE_URL, url)
        return url

    @staticmethod
    def _register_image(
        images_map: "OrderedDict[str, Tuple[str, int]]",
        order: List[str],
        url: Optional[str],
    ) -> None:
        if not url:
            return
        key, size = AnkerScraperUK._image_key_and_size(url)
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
        width = AnkerScraperUK._extract_int_from_query(query_params, ("width", "w", "size"))
        if width == 0:
            width = AnkerScraperUK._extract_int_from_query(query_params, ("height", "h"))
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


def run(inp: str, out: str, sku_col: Optional[str] = None) -> None:
    scraper = AnkerScraperUK()
    try:
        source_df = pd.read_excel(inp)
    except Exception as exc:
        raise SystemExit(f"Failed to read input Excel file: {exc}") from exc

    source_df.columns = [str(col).replace("\n", " ").strip() for col in source_df.columns]
    if sku_col and sku_col in source_df.columns:
        pass
    else:
        sku_col = None
        for col in source_df.columns:
            if "SKU" in col.upper() or "MODEL" in col.upper():
                sku_col = col
                break
    if not sku_col:
        raise SystemExit("Input Excel must have a 'SKU' or 'Model' column (or pass --sku-col).")

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

        if page_data.status not in ("error", "sku_mismatch"):
            result.title = page_data.title
            result.description = page_data.description
            result.images = page_data.images

        results.append(result.as_dict())
        time.sleep(SLEEP_SECONDS)

    output_df = pd.DataFrame(results)
    output_df.to_excel(out, index=False)
    logging.info("Saved results to %s", out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Anker UK scraper by SKU (with SKU verification)")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", required=True, help="Output Excel file")
    ap.add_argument("--sku-col", dest="sku_col", default=None, help="SKU column name (auto-detect if omitted)")
    args = ap.parse_args()
    run(args.inp, args.out, args.sku_col)






