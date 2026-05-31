#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bashiti Hardware Scraper
Scrapes product data from https://bashitihardware.com
Extracts SKU, Description, and Images from product pages.

SKU mode: searches
  https://bashitihardware.com/?s=<SKU>&post_type=product
then opens the first product in the results and scrapes that product page.
"""

import argparse
import os
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, quote_plus
import pandas as pd
import sys

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
import requests

# WooCommerce product search (site root; matches browser search, e.g. ?s=sky&post_type=product).
SKU_SEARCH_URL_TEMPLATE = "https://bashitihardware.com/?s={}&post_type=product"


def build_driver(headless: bool = True) -> webdriver.Chrome:
    """Build Chrome driver with proper options."""
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver


def _get_search_result_product_links(driver):
    """Product page links on a WooCommerce search results page."""
    selectors = [
        "ul.products li.product a.woocommerce-loop-product__link",
        "ul.products li.product a[href*='/product/']",
        "ul.products li.product a",
        ".products li.product a",
        ".products .product a",
        "li.product a[href*='/product/']",
        ".product a[href*='/product/']",
        "main a[href*='/product/']",
        "#main a[href*='/product/']",
        ".content a[href*='/product/']",
        "#content a[href*='/product/']",
        "a[href*='/product/'][href*='bashitihardware']",
    ]
    seen = set()
    for selector in selectors:
        for elem in driver.find_elements(By.CSS_SELECTOR, selector):
            href = elem.get_attribute("href")
            if href and "/product/" in href and "bashitihardware" in href and href not in seen:
                seen.add(href)
                yield href


def _is_no_search_results(driver) -> bool:
    try:
        if next(_get_search_result_product_links(driver), None) is not None:
            return False
        page_text = driver.find_element(By.TAG_NAME, "body").text
        if "لا توجد منتجات" in page_text or "no products" in page_text.lower():
            return True
        return True
    except Exception:
        return False


def get_product_url_from_search(driver, search_url: str, wait: WebDriverWait, pause: float) -> Optional[str]:
    """Load search URL and return first product page URL, or None."""
    try:
        driver.get(search_url)
        time.sleep(pause)
        try:
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    ".products, .woocommerce-info, .woocommerce-no-products-found, "
                    "li.product, a[href*='/product/']",
                ))
            )
        except TimeoutException:
            pass
        time.sleep(1.0)
        if _is_no_search_results(driver):
            return None
        return next(_get_search_result_product_links(driver), None)
    except Exception:
        return None


def resolve_product_url_for_query_sku(
    driver,
    search_url: str,
    query_sku: str,
    wait: WebDriverWait,
    pause: float,
    max_candidates: int = 8,
) -> Optional[str]:
    """
    WooCommerce search often returns unrelated items.
    We therefore try multiple product links and pick the one whose product-page SKU matches query_sku.
    """
    query_sku = (query_sku or "").strip()
    if not query_sku:
        return get_product_url_from_search(driver, search_url, wait, pause)

    try:
        driver.get(search_url)
        time.sleep(pause)

        # If WooCommerce (or a plugin) redirects directly to a product page,
        # do NOT click anything else — just scrape the landed page.
        try:
            landed = (driver.current_url or "").strip()
            if "/product/" in landed:
                return landed
        except Exception:
            pass

        try:
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    ".products, .woocommerce-info, .woocommerce-no-products-found, "
                    "li.product, a[href*='/product/']",
                ))
            )
        except TimeoutException:
            pass

        if _is_no_search_results(driver):
            return None

        # Check again in case the page navigated after the wait.
        try:
            landed = (driver.current_url or "").strip()
            if "/product/" in landed:
                return landed
        except Exception:
            pass

        candidates = list(_get_search_result_product_links(driver))
        if not candidates:
            return None

        # Limit candidate attempts to keep runs bounded.
        candidates = candidates[: max_candidates]

        # Try to find exact SKU match by opening each candidate product page.
        for url in candidates:
            try:
                driver.get(url)
                # Wait for product page base container; keep it broad.
                try:
                    wait.until(
                        EC.presence_of_element_located((
                            By.CSS_SELECTOR,
                            ".product, .product-container, .product-main, .summary, .entry-summary",
                        ))
                    )
                except TimeoutException:
                    pass
                time.sleep(max(0.5, pause))

                page_sku = (extract_sku(driver) or "").strip()
                if page_sku and page_sku == query_sku:
                    return url
            except Exception:
                continue

        # No exact SKU match: fall back to first result.
        return candidates[0]
    except Exception:
        return get_product_url_from_search(driver, search_url, wait, pause)


def extract_all_product_links(driver, search_url: str, wait: WebDriverWait) -> List[str]:
    """Extract all product links from a search results or category page."""
    product_links = []
    
    try:
        driver.get(search_url)
        print(f"   → Loaded search URL: {search_url}")
        
        # Wait for products to load
        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".product-item, .product, article.product"))
        )
        time.sleep(2)
        
        # Get all product links from the page
        # Common selectors for WooCommerce product links
        selectors = [
            "a.woocommerce-LoopProduct-link",
            "a[href*='/product/']",
            ".product-item a",
            ".product a",
            "article.product a",
            ".product-item-link",
            "a.woocommerce-loop-product__link",
            "h3 a",
            "h2 a",
        ]
        
        for selector in selectors:
            try:
                links = driver.find_elements(By.CSS_SELECTOR, selector)
                for link in links:
                    href = link.get_attribute("href")
                    if href and "product" in href.lower() and href not in product_links:
                        # Filter out non-product links
                        if not any(x in href for x in ["/category/", "/tag/", "/account/", "/cart/", "/checkout/"]):
                            product_links.append(href)
                if product_links:
                    break
            except Exception as e:
                continue
        
        # Try to handle pagination - get more products if "Next" exists
        try:
            next_button = driver.find_element(By.CSS_SELECTOR, "a.next.page-numbers, a.next, .pagination-next")
            while next_button:
                try:
                    driver.execute_script("arguments[0].click();", next_button)
                    time.sleep(2)
                    
                    # Get links from new page
                    for selector in selectors:
                        links = driver.find_elements(By.CSS_SELECTOR, selector)
                        for link in links:
                            href = link.get_attribute("href")
                            if href and "product" in href.lower() and href not in product_links:
                                if not any(x in href for x in ["/category/", "/tag/", "/account/", "/cart/", "/checkout/"]):
                                    product_links.append(href)
                    
                    # Check for next button again
                    next_button = driver.find_element(By.CSS_SELECTOR, "a.next.page-numbers, a.next, .pagination-next")
                except NoSuchElementException:
                    break
        except NoSuchElementException:
            pass  # No pagination
        
        print(f"   → Found {len(product_links)} product links")
        return product_links
        
    except Exception as e:
        print(f"   → Error extracting product links: {e}")
        return []


def extract_title(driver) -> str:
    """Extract product title from product page."""
    try:
        # Try various selectors for title
        title_selectors = [
            ".product_title.entry-title.wd-entities-title",
            "h1.product_title",
            ".entry-title",
            "h1",
            ".product-title",
            ".product-name",
            "h1.product-title",
        ]
        
        for selector in title_selectors:
            try:
                title_element = driver.find_element(By.CSS_SELECTOR, selector)
                title_text = title_element.text.strip()
                
                if title_text:
                    return title_text
            except NoSuchElementException:
                continue
        
        return ""
        
    except Exception as e:
        print(f"   → Error extracting title: {e}")
        return ""


def extract_sku(driver) -> str:
    """Extract SKU from product page."""
    try:
        # Try various selectors for SKU
        sku_selectors = [
            ".sku",
            ".product_meta .sku",
            ".woocommerce-product-attributes-item--sku .woocommerce-product-attributes-item__value",
            "span[class*='sku']",
            "meta[property='product:retailer_part_no']",
        ]
        
        for selector in sku_selectors:
            try:
                sku_element = driver.find_element(By.CSS_SELECTOR, selector)
                sku_text = sku_element.text.strip()
                
                # Try content attribute for meta tags
                if not sku_text:
                    sku_text = sku_element.get_attribute("content") or ""
                
                # Clean up SKU text (remove "SKU:" prefix if present)
                sku_text = sku_text.replace("SKU:", "").replace("SKU", "").strip()
                
                if sku_text:
                    return sku_text
            except NoSuchElementException:
                continue
        
        # Fallback: Look for SKU in text
        try:
            page_text = driver.page_source
            soup = BeautifulSoup(page_text, "html.parser")
            
            # Look for "SKU: xxxxxx" pattern
            sku_patterns = soup.find_all(text=lambda t: t and "SKU:" in t)
            for text in sku_patterns:
                parts = text.split("SKU:")
                if len(parts) > 1:
                    potential_sku = parts[1].strip().split()[0]
                    if potential_sku:
                        return potential_sku
        except Exception:
            pass
        
        return ""
        
    except Exception as e:
        print(f"   → Error extracting SKU: {e}")
        return ""


def extract_description(driver) -> str:
    """Extract product description from product page."""
    try:
        def _clean_desc(text: str) -> str:
            if not text:
                return ""
            # Remove common site-wide boilerplate that often appears inside broad containers
            drop_phrases = [
                "free delivery",
                "secure payment",
                "refund allowed",
                "support 24/7",
                "subscribe",
                "all categories",
                "my account",
                "wishlist",
                "compare",
                "shopping cart",
                "terms & conditions",
                "privacy policy",
                "refund and returns",
                "contact us",
                "developed by",
            ]
            lines = [ln.strip() for ln in str(text).splitlines()]
            kept: List[str] = []
            for ln in lines:
                if not ln:
                    continue
                low = ln.lower()
                if any(p in low for p in drop_phrases):
                    continue
                kept.append(ln)
            cleaned = " ".join(kept).strip()
            # Collapse whitespace
            cleaned = " ".join(cleaned.split())
            return cleaned

        # STRICT MODE (requested): only scrape the Elementor single-product-content widget body.
        # This matches blocks like:
        # `.wd-single-content.elementor-widget-wd_single_product_content .elementor-widget-container`
        try:
            soup = BeautifulSoup(driver.page_source or "", "html.parser")
            widget = soup.select_one(
                ".wd-single-content.elementor-widget-wd_single_product_content .elementor-widget-container"
            )
            if widget:
                parts: List[str] = []
                h3 = widget.find("h3")
                if h3:
                    t = _clean_desc(h3.get_text("\n", strip=True))
                    if t:
                        parts.append(t)

                ul = widget.find("ul")
                if ul:
                    items: List[str] = []
                    for li in ul.find_all("li"):
                        t = _clean_desc(li.get_text("\n", strip=True))
                        if t:
                            items.append(t)
                    if items:
                        parts.append("\n".join(f"- {x}" for x in items))

                final = "\n".join(p for p in parts if p).strip()
                if final:
                    return final
        except Exception:
            pass

        # STRICT FALLBACK: slice the page content from "Description" until "Recently Viewed".
        # This avoids scraping footer widgets, info messages, and recently-viewed blocks.
        try:
            soup = BeautifulSoup(driver.page_source or "", "html.parser")

            def _norm_heading(s: str) -> str:
                return " ".join((s or "").strip().lower().split())

            desc_header = soup.find(
                lambda t: getattr(t, "name", None) in {"h1", "h2", "h3", "h4", "h5", "h6"}
                and _norm_heading(t.get_text(" ", strip=True)) == "description"
            )
            if desc_header:
                collected_lines: List[str] = []
                seen_lines = set()
                for el in desc_header.find_all_next():
                    if getattr(el, "name", None) in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                        if _norm_heading(el.get_text(" ", strip=True)) == "recently viewed":
                            break
                    name = getattr(el, "name", None)
                    if name not in {"p", "li", "h3", "h4"}:
                        continue

                    text = _clean_desc(el.get_text("\n", strip=True))
                    if not text or len(text) <= 3:
                        continue

                    line = f"- {text}" if name == "li" else text
                    if line not in seen_lines:
                        seen_lines.add(line)
                        collected_lines.append(line)

                final = "\n".join(collected_lines).strip()
                if final and len(final) > 20:
                    return final
        except Exception:
            pass

        # Preferred: bashitihardware.com renders the real description body inside:
        # class="markdown prose w-full break-words dark:prose-invert dark"
        # The previous logic was too broad and could pull unrelated page text.
        try:
            soup = BeautifulSoup(driver.page_source or "", "html.parser")
            required = {"markdown", "prose", "w-full", "break-words", "dark:prose-invert", "dark"}

            def _has_required_classes(tag) -> bool:
                if not getattr(tag, "name", None):
                    return False
                classes = tag.get("class") or []
                if not classes:
                    return False
                return required.issubset(set(classes))

            container = soup.find(_has_required_classes)
            if container:
                text = _clean_desc(container.get_text("\n", strip=True))
                if text and len(text) > 20:
                    return text
        except Exception:
            pass

        # Try targeted selectors first (avoid broad Elementor containers).
        priority_selectors = [
            ".woocommerce-Tabs-panel--description",
            "#tab-description",
            ".woocommerce-product-details__short-description",
            ".product_description",
        ]
        for selector in priority_selectors:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, selector)
                candidates: List[str] = []
                for elem in elems:
                    t = _clean_desc(elem.text or "")
                    if t and len(t) > 20:
                        candidates.append(t)
                if candidates:
                    # pick the longest candidate (usually the real description)
                    return max(candidates, key=len)
            except Exception:
                continue

        # Elementor fallback: keep it constrained and filtered.
        # Many pages wrap everything in `.elementor-widget-container`; pick the best text-like block.
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, ".elementor-widget-container, .elementor-text-editor")
            candidates = []
            for elem in elems:
                t = _clean_desc(elem.text or "")
                if not t or len(t) <= 20:
                    continue
                # Heuristic: avoid huge containers that still slipped boilerplate in
                if len(t) > 12000:
                    continue
                candidates.append(t)
            if candidates:
                return max(candidates, key=len)
        except Exception:
            pass
        
        # Fallback: Try to expand tabs and get description
        try:
            # Look for tabs
            tabs = driver.find_elements(By.CSS_SELECTOR, ".tabs li, .woocommerce-tabs li a")
            for tab in tabs:
                try:
                    if "description" in tab.text.lower():
                        driver.execute_script("arguments[0].click();", tab)
                        time.sleep(1)
                        
                        # Try to get description again from targeted selectors
                        for selector in priority_selectors:
                            try:
                                desc_element = driver.find_element(By.CSS_SELECTOR, selector)
                                desc_text = _clean_desc(desc_element.text or "")
                                if desc_text and len(desc_text) > 20:
                                    return desc_text
                            except NoSuchElementException:
                                continue
                except Exception:
                                    continue
        except Exception:
            pass
        
        return ""
        
    except Exception as e:
        print(f"   → Error extracting description: {e}")
        return ""


def extract_price(driver) -> str:
    """Extract product price from product page."""
    try:
        # Common WooCommerce price selectors
        price_selectors = [
            ".summary .price",
            "p.price",
            "span.price",
            ".woocommerce-Price-amount",
            ".price .amount",
            "bdi",
            # Provided container class; try finding a price within it
            ".elementor-element.elementor-element-308ab41.e-con-full.e-flex.e-con.e-child .price",
            ".elementor-element.elementor-element-308ab41.e-con-full.e-flex.e-con.e-child bdi",
        ]

        # Try selenium first
        for selector in price_selectors:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elems:
                    text = (elem.text or "").strip()
                    if text and any(ch.isdigit() for ch in text):
                        return text
            except Exception:
                continue

        # Fallback to BeautifulSoup if selenium selectors fail
        try:
            soup = BeautifulSoup(driver.page_source, "html.parser")
            for selector in price_selectors:
                for elem in soup.select(selector):
                    text = (elem.get_text(strip=True) or "").strip()
                    if text and any(ch.isdigit() for ch in text):
                        return text
        except Exception:
            pass

        return ""
    except Exception as e:
        print(f"   → Error extracting price: {e}")
        return ""


def extract_images(driver, max_images: int = 10) -> List[str]:
    """Extract product images from product page."""
    images = []
    
    try:
        # Try various selectors for product images
        img_selectors = [
            ".woocommerce-product-gallery__image img",
            ".product-gallery img",
            ".product-images img",
            ".wp-post-image",
            ".product-thumbnails img",
            ".woocommerce-product-gallery img",
            "figure.product-gallery__image img",
            "img.attachment-woocommerce_single",
        ]
        
        for selector in img_selectors:
            try:
                img_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                
                for img in img_elements:
                    # Try different attributes for image source
                    src = (img.get_attribute("src") or 
                           img.get_attribute("data-src") or 
                           img.get_attribute("data-large_image") or
                           img.get_attribute("data-zoom_image") or
                           img.get_attribute("srcset"))
                    
                    if src and src.startswith("http"):
                        # Handle srcset (contains multiple URLs)
                        if "," in src:
                            src = src.split(",")[0].split()[0]
                        
                        if src not in images and "placeholder" not in src.lower():
                            images.append(src)
                            
                            if len(images) >= max_images:
                                break
                
                if images:
                    break
                    
            except Exception as e:
                continue
        
        # Fallback: Use BeautifulSoup to parse images
        if not images:
            try:
                soup = BeautifulSoup(driver.page_source, "html.parser")
                
                # Look for all img tags
                for img in soup.find_all("img"):
                    src = (img.get("src") or 
                           img.get("data-src") or 
                           img.get("data-large_image") or
                           img.get("data-zoom_image"))
                    
                    if src:
                        if src.startswith("//"):
                            src = "https:" + src
                        elif src.startswith("/"):
                            src = "https://bashitihardware.com" + src
                        
                        if src.startswith("http") and "placeholder" not in src.lower():
                            if src not in images:
                                images.append(src)
                                
                                if len(images) >= max_images:
                                    break
            except Exception as e:
                print(f"   → Error parsing images with BeautifulSoup: {e}")
        
        # Remove duplicates while preserving order
        seen = set()
        unique_images = []
        for img in images:
            # Normalize URL by removing query parameters for comparison
            img_normalized = img.split('?')[0]
            if img_normalized not in seen:
                seen.add(img_normalized)
                unique_images.append(img)
        
        return unique_images[:max_images]
        
    except Exception as e:
        print(f"   → Error extracting images: {e}")
        return []


def scrape_product(driver, product_url: str, wait: WebDriverWait, pause: float, max_images: int) -> Dict[str, Any]:
    """Scrape data for a single product."""
    result = {
        "ProductURL": product_url,
        "Title": "",
        "SKU": "",
        "Price": "",
        "Description": "",
        "Images": [],
        "Found": False
    }
    
    try:
        print(f"   → Scraping: {product_url}")
        driver.get(product_url)
        
        # Wait for product to load
        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".product, .product-container, .product-main"))
        )
        time.sleep(pause)
        
        # Extract Title
        title = extract_title(driver)
        result["Title"] = title
        if title:
            print(f"   → Found title: {title[:60]}...")
        
        # Extract SKU
        sku = extract_sku(driver)
        result["SKU"] = sku
        if sku:
            print(f"   → Found SKU: {sku}")

        # Extract Price
        price = extract_price(driver)
        result["Price"] = price
        if price:
            print(f"   → Found price: {price}")
        
        # Extract Description
        description = extract_description(driver)
        result["Description"] = description
        if description:
            print(f"   → Found description: {len(description)} chars")
        
        # Extract Images
        images = extract_images(driver, max_images)
        result["Images"] = images
        if images:
            print(f"   → Found {len(images)} images")
        
        result["Found"] = bool(title or sku or price or description or images)
        
        return result
        
    except TimeoutException:
        print(f"   → Timeout loading product page")
        result["Note"] = "Timeout loading page"
        return result
    except Exception as e:
        print(f"   → Error scraping product: {e}")
        result["Note"] = f"Error: {e}"
        return result


def scrape_from_sku_search(
    driver,
    query_sku: str,
    wait: WebDriverWait,
    pause: float,
    max_images: int,
) -> Dict[str, Any]:
    """Search by SKU on site root, then scrape the first product page."""
    search_url = SKU_SEARCH_URL_TEMPLATE.format(quote_plus(query_sku.strip()))
    print(f"   → Search: {search_url}")
    product_url = resolve_product_url_for_query_sku(driver, search_url, query_sku, wait, pause)
    if not product_url:
        return {
            "QuerySKU": query_sku,
            "SearchURL": search_url,
            "ProductURL": "",
            "Title": "",
            "SKU": "",
            "Price": "",
            "Description": "",
            "Images": [],
            "Found": False,
            "Note": "No products in search results",
        }
    result = scrape_product(driver, product_url, wait, pause, max_images)
    result["QuerySKU"] = query_sku
    result["SearchURL"] = search_url
    return result


def _load_skus_from_excel(path: str, sku_col: str) -> List[str]:
    df = pd.read_excel(path)
    if sku_col not in df.columns:
        raise ValueError(f"Column '{sku_col}' not found. Available: {list(df.columns)}")
    raw = (
        df[sku_col]
        .dropna()
        .astype(str)
        .map(lambda s: s.strip())
        .loc[lambda s: (s != "") & (s.str.lower() != "nan")]
        .tolist()
    )
    seen = set()
    out: List[str] = []
    for s in raw:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main():
    """Main function for CLI usage."""
    # Windows consoles may use legacy encodings; force UTF-8 to avoid crashes on non-ASCII output.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Bashiti Hardware scraper")
    ap.add_argument("--url", dest="url", help="Single product URL to scrape")
    ap.add_argument("--search-url", dest="search_url", help="Search results or category URL to scrape all products")
    ap.add_argument("--in", dest="input_file", help="Input Excel (.xlsx): URLs unless --sku-col is set")
    ap.add_argument("--sku", dest="sku", default=None, help="Single SKU: search ?s=SKU&post_type=product then scrape first hit")
    ap.add_argument(
        "--sku-col",
        dest="sku_col",
        default=None,
        help="With --in: column name for SKUs (search then scrape; uses site search URL)",
    )
    ap.add_argument("--out", dest="output_file", required=True, help="Output Excel file path")
    ap.add_argument("--url-col", dest="url_col", default=None, help="URL column name when using --in without --sku-col (auto-detect if omitted)")
    ap.add_argument("--start", type=int, default=0, help="Start index (0-based) for a test run (default: 0)")
    ap.add_argument("--limit", type=int, default=0, help="Max items to process (0 = all) for a test run")
    ap.add_argument("--pause", type=float, default=2.0, help="Pause between requests (seconds)")
    ap.add_argument("--headless", action="store_true", help="Run in headless mode")
    ap.add_argument("--max-img", type=int, default=10, help="Maximum images to collect per product")
    args = ap.parse_args()
    
    if args.sku_col and not args.input_file:
        print("❌ Error: --sku-col requires --in")
        return
    if args.sku and args.input_file:
        print("❌ Error: use either --sku or --in, not both")
        return
    if not args.url and not args.search_url and not args.input_file and not args.sku:
        print("❌ Error: Must provide one of --url, --search-url, --in, or --sku")
        return
    
    # Create output directory if needed
    output_dir = os.path.dirname(args.output_file)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # Build driver
    driver = build_driver(headless=args.headless)
    wait = WebDriverWait(driver, 20)
    
    try:
        print("Bashiti Hardware Scraper")
        print(f"Output: {args.output_file}")
        print(f"Pause: {args.pause}s")
        print()
        
        sku_mode = bool(args.sku or args.sku_col)
        product_urls: List[str] = []
        sku_list: List[str] = []

        if sku_mode:
            if args.sku:
                sku_list = [args.sku.strip()]
            else:
                try:
                    sku_list = _load_skus_from_excel(args.input_file, args.sku_col)
                except ValueError as e:
                    print(f"❌ {e}")
                    return
                except Exception as e:
                    print(f"❌ Error reading Excel: {e}")
                    return
            print("SKU search template: https://bashitihardware.com/?s=<SKU>&post_type=product")
            # Apply test slicing
            start = max(0, int(args.start or 0))
            if start:
                sku_list = sku_list[start:]
            if args.limit and int(args.limit) > 0:
                sku_list = sku_list[: int(args.limit)]
            print(f"Loaded {len(sku_list)} SKU(s) to resolve")
        elif args.url:
            # Single URL
            product_urls = [args.url]
            print(f"Scraping single URL: {args.url}")
            
        elif args.search_url:
            # Search/category URL - extract all product links
            print(f"Extracting products from: {args.search_url}")
            product_urls = extract_all_product_links(driver, args.search_url, wait)
            print(f"Found {len(product_urls)} products to scrape")
            
        elif args.input_file:
            # Excel file with URLs
            try:
                df = pd.read_excel(args.input_file)
                print(f"Loaded {len(df)} rows from {args.input_file}")
                
                # Find URL column
                if args.url_col:
                    if args.url_col in df.columns:
                        url_col = args.url_col
                    else:
                        print(f"❌ Column '{args.url_col}' not found. Available: {list(df.columns)}")
                        return
                else:
                    # Auto-detect
                    for c in df.columns:
                        if "url" in str(c).lower():
                            url_col = c
                            break
                    else:
                        url_col = df.columns[0]
                
                print(f"Using URL column: '{url_col}'")
                product_urls = [str(url).strip() for url in df[url_col] if pd.notna(url) and str(url).strip().startswith("http")]
                
            except Exception as e:
                print(f"❌ Error reading input file: {e}")
                return
        
        if sku_mode:
            if not sku_list:
                print("❌ No SKUs to process")
                return
        elif not product_urls:
            print("❌ No product URLs to scrape")
            if args.input_file and not args.sku_col:
                print(
                    "   Hint: URL mode only keeps rows whose cell starts with http:// or https:// . "
                    'If your sheet has SKUs (e.g. column "Item No."), run with '
                    '--sku-col "Item No." (or your exact header) to search then scrape.'
                )
            return
        
        if not sku_mode:
            # Deduplicate product URLs before scraping
            seen_urls = set()
            unique_product_urls = []
            for url in product_urls:
                normalized_url = url.split("?")[0]
                if normalized_url not in seen_urls:
                    seen_urls.add(normalized_url)
                    unique_product_urls.append(url)
            if len(product_urls) != len(unique_product_urls):
                print(f"Deduplicated: {len(product_urls)} -> {len(unique_product_urls)} unique products")
            product_urls = unique_product_urls
            # Apply test slicing
            start = max(0, int(args.start or 0))
            if start:
                product_urls = product_urls[start:]
            if args.limit and int(args.limit) > 0:
                product_urls = product_urls[: int(args.limit)]
        
        results: List[Dict[str, Any]] = []
        total = len(sku_list) if sku_mode else len(product_urls)

        if sku_mode:
            for idx, qsku in enumerate(sku_list, 1):
                print(f"\n[{idx}/{total}] SKU: {qsku}")
                try:
                    result = scrape_from_sku_search(driver, qsku, wait, args.pause, args.max_img)
                    results.append(result)
                    status = "FOUND" if result.get("Found") else "NOT FOUND"
                    title = result.get("Title", "")
                    psku = result.get("SKU", "")
                    desc_len = len(result.get("Description", ""))
                    img_count = len(result.get("Images", []))
                    price = result.get("Price", "")
                    print(
                        f"    → {status} | Title: {title[:40]}... | SKU: {psku} | "
                        f"Price: {price} | Desc: {desc_len} chars | Images: {img_count}"
                    )
                except Exception as e:
                    print(f"    → ERROR: {e}")
                    results.append(
                        {
                            "QuerySKU": qsku,
                            "SearchURL": SKU_SEARCH_URL_TEMPLATE.format(quote_plus(qsku.strip())),
                            "ProductURL": "",
                            "Title": "",
                            "SKU": "",
                            "Price": "",
                            "Description": "",
                            "Images": [],
                            "Found": False,
                            "Note": f"Error: {e}",
                        }
                    )
                time.sleep(args.pause)
        else:
            for idx, product_url in enumerate(product_urls, 1):
                print(f"\n[{idx}/{total}] Processing: {product_url}")
                try:
                    result = scrape_product(driver, product_url, wait, args.pause, args.max_img)
                    result["QuerySKU"] = ""
                    result["SearchURL"] = ""
                    results.append(result)
                    status = "FOUND" if result.get("Found") else "NOT FOUND"
                    title = result.get("Title", "")
                    sku = result.get("SKU", "")
                    desc_len = len(result.get("Description", ""))
                    img_count = len(result.get("Images", []))
                    price = result.get("Price", "")
                    print(
                        f"    → {status} | Title: {title[:40]}... | SKU: {sku} | "
                        f"Price: {price} | Desc: {desc_len} chars | Images: {img_count}"
                    )
                except Exception as e:
                    print(f"    → ERROR: {e}")
                    results.append(
                        {
                            "QuerySKU": "",
                            "SearchURL": "",
                            "ProductURL": product_url,
                            "Title": "",
                            "SKU": "",
                            "Price": "",
                            "Description": "",
                            "Images": [],
                            "Found": False,
                            "Note": f"Error: {e}",
                        }
                    )
                time.sleep(args.pause)
        
        # Create results DataFrame
        results_df = pd.DataFrame(results)
        
        # Convert Images list to semicolon-separated string
        results_df["Images"] = results_df["Images"].apply(
            lambda lst: ";".join(lst) if isinstance(lst, list) and lst else ""
        )
        
        # Save results
        results_df.to_excel(args.output_file, index=False)
        
        # Print summary
        found_count = sum(1 for result in results if result.get("Found", False))
        print("\nScraping Complete!")
        print(f"   Total products: {total}")
        print(f"   Found: {found_count}")
        print(f"   Success Rate: {found_count/total*100:.1f}%")
        print(f"   Results saved to: {args.output_file}")
    
    finally:
        driver.quit()


if __name__ == "__main__":
    main()

