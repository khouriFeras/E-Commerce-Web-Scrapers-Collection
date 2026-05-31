#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bashiti Hardware Scraper
Scrapes product data from https://bashitihardware.com via search:
  https://bashitihardware.com/ar/?s=SKU&post_type=product
Uses WooCommerce search results; handles no-results and error pages.
"""

import argparse
import os
import re
import time
import json
import sys
from urllib.parse import quote_plus, urlparse
from typing import List, Dict, Any, Optional
from pathlib import Path
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup


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
    """
    Find all product page links on a WooCommerce search results page.
    Uses broad selectors so we don't miss results when the theme differs.
    """
    selectors = [
        "ul.products li.product a.woocommerce-loop-product__link",
        "ul.products li.product a[href*='/product/']",
        "ul.products li.product a",
        ".products li.product a",
        ".products .product a",
        "li.product a[href*='/product/']",
        ".product a[href*='/product/']",
        # Fallback: any product link in main content (avoid header/footer)
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


def is_no_search_results(driver) -> bool:
    """
    Check if the current page is a WooCommerce "no products" search result.
    We prioritize finding product links first; only if none found do we say "no results".
    """
    try:
        # First: if we find any product link, we have results (don't trust "no products" text elsewhere)
        first_link = next(_get_search_result_product_links(driver), None)
        if first_link is not None:
            return False
        # No product links found - confirm with "no products" message in page
        page_text = driver.find_element(By.TAG_NAME, "body").text
        if "لا توجد منتجات" in page_text or "no products" in page_text.lower():
            return True
        # Still no links but no explicit message - treat as no results
        return True
    except Exception:
        return False


def _norm_sku_text(value: str) -> str:
    """Normalize SKU text for reliable comparison."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _sku_matches_expected(expected_sku: str, scraped_sku: str) -> bool:
    """
    Compare SKU values leniently:
    - exact match after normalization
    - containment for cases like separators/extra suffixes
    """
    expected = _norm_sku_text(expected_sku)
    scraped = _norm_sku_text(scraped_sku)
    if not expected or not scraped:
        return False
    if expected == scraped:
        return True
    # Keep containment strict enough to avoid tiny accidental matches.
    if len(expected) >= 4 and (expected in scraped or scraped in expected):
        return True
    return False


def get_product_urls_from_search(driver, search_url: str, wait: WebDriverWait, pause: float) -> List[str]:
    """
    Open the search URL and return all product URLs from results.
    """
    try:
        driver.get(search_url)
        time.sleep(pause)
        # Wait for results area or any product link (some themes load via JS)
        try:
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    ".products, .woocommerce-info, .woocommerce-no-products-found, "
                    "li.product, a[href*='/product/']"
                ))
            )
        except TimeoutException:
            pass
        # Extra moment for JS-rendered product links
        time.sleep(1.0)
        if is_no_search_results(driver):
            return []
        return list(_get_search_result_product_links(driver))
    except Exception:
        return []


def get_product_url_from_search(driver, search_url: str, wait: WebDriverWait, pause: float) -> Optional[str]:
    """Backward-compatible helper returning first product URL from search."""
    try:
        urls = get_product_urls_from_search(driver, search_url, wait, pause)
        return urls[0] if urls else None
    except Exception:
        return None


def scrape_search_results_page(
    driver,
    search_url: str,
    wait: WebDriverWait,
    pause: float,
    searched_sku: str,
    max_images: int = 10,
) -> Dict[str, Any]:
    """
    Scrape data directly from WooCommerce search results page (no product-page navigation).
    """
    result = {
        "ProductURL": search_url,
        "SKU": searched_sku,
        "Title": "",
        "Price": "",
        "Description": "",
        "Images": [],
        "Found": False,
        "Status": "NOT_FOUND",
        "Note": "No products in search results",
    }

    try:
        driver.get(search_url)
        time.sleep(pause)
        current_url = (driver.current_url or "").strip()

        # If the search URL redirects directly to a product page, scrape it as product.
        # This still follows the "no click" flow because navigation happened by URL redirect.
        if "/product/" in current_url or "/single-product/" in current_url:
            direct = scrape_product(driver, current_url, wait, 0, max_images)
            if not direct.get("SKU"):
                direct["SKU"] = searched_sku
            note = direct.get("Note", "")
            redirect_note = "Direct redirect from search URL to product page"
            direct["Note"] = f"{note} | {redirect_note}" if note else redirect_note
            return direct

        try:
            wait.until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        ".products, .woocommerce-info, .woocommerce-no-products-found, "
                        "li.product, a[href*='/product/']",
                    )
                )
            )
        except TimeoutException:
            pass
        time.sleep(1.0)

        if is_no_search_results(driver):
            return result

        soup = BeautifulSoup(driver.page_source or "", "html.parser")
        cards = soup.select("ul.products li.product, .products li.product, li.product")
        if not cards:
            cards = soup.select(".products .product")
        if not cards:
            result["Status"] = "ERROR"
            result["Note"] = "Search page loaded but no product cards were parsed"
            return result

        # Prefer the card containing the searched SKU text; fallback to first card.
        chosen = cards[0]
        for card in cards:
            card_text = card.get_text(" ", strip=True)
            if _sku_matches_expected(searched_sku, card_text):
                chosen = card
                break

        # URL
        link = chosen.select_one(
            "a.woocommerce-loop-product__link[href], a[href*='/product/'][href], a[href]"
        )
        if link and link.get("href"):
            href = str(link.get("href")).strip()
            result["ProductURL"] = _normalize_image_url(href)

        # Title
        title_el = chosen.select_one(
            ".woocommerce-loop-product__title, .product-title, h2, h3, h4, .wd-entities-title"
        )
        if title_el:
            result["Title"] = " ".join(title_el.get_text(" ", strip=True).split())

        # Price
        price_el = chosen.select_one(".new-price, .price, .woocommerce-Price-amount, bdi")
        if price_el:
            result["Price"] = " ".join(price_el.get_text(" ", strip=True).split())

        # Images
        image_candidates: List[str] = []
        for img in chosen.select("img"):
            for attr in ("data-large_image", "data-zoom_image", "data-src", "src"):
                v = img.get(attr)
                if v:
                    image_candidates.append(v)
            image_candidates.extend(_urls_from_srcset(img.get("srcset") or ""))
        result["Images"] = pick_largest_image_urls(image_candidates)[:max_images]

        result["Found"] = True
        result["Status"] = "FOUND"
        result["Note"] = "Scraped from search results page (no product-page click)"
        return result
    except Exception as e:
        result["Status"] = "ERROR"
        result["Note"] = f"Failed scraping search results page: {e}"
        return result


def is_laravel_error_page(driver) -> bool:
    """
    Check if the current page is a Laravel error/debug page.
    Based on error page analysis: detects ErrorException with item_name property.
    """
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text
        
        # Check for ErrorException
        if "ErrorException" in page_text:
            return True
        
        # Check for specific error message pattern
        if "Attempt to read property" in page_text and "item_name" in page_text and "on null" in page_text:
            return True
        
        # Check for Laravel debug UI elements
        if any(tab in page_text for tab in ["STACK", "CONTEXT", "DEBUG", "FLARE"]):
            return True
        
        # Check page title
        page_title = driver.title.lower()
        if any(keyword in page_title for keyword in ["laravel", "exception", "error", "whoops"]):
            return True
        
        return False
    except Exception:
        return False


def extract_laravel_debug_info(driver) -> Dict[str, Any]:
    """
    Extract useful information from Laravel error pages.
    This helps understand the application structure and database schema.
    """
    debug_info = {
        "error_type": "ErrorException",
        "error_message": "Attempt to read property 'item_name' on null",
        "controller": "ShopController@single_product",
        "route": "single_product",
        "tables": [],
        "queries": []
    }
    
    try:
        page_source = driver.page_source
        
        # Extract table names from SQL queries
        tables = re.findall(r"from\s+`(\w+)`", page_source, re.IGNORECASE)
        debug_info["tables"] = list(set(tables))
        
        # Extract SQL queries (simplified)
        sql_matches = re.findall(r"select.*?from\s+`(\w+)`", page_source, re.IGNORECASE | re.DOTALL)
        if sql_matches:
            debug_info["queries"] = sql_matches[:5]
        
    except Exception as e:
        debug_info["extraction_error"] = str(e)
    
    return debug_info


def extract_title(driver) -> str:
    """Extract product title from product page. Title is in .product-title with trailing space."""
    try:
        # First try to find element with class="product-title " (with trailing space) - this is the title
        try:
            # Find all elements with product-title class
            elems = driver.find_elements(By.XPATH, "//*[contains(@class, 'product-title')]")
            for elem in elems:
                class_attr = elem.get_attribute("class") or ""
                text = elem.text.strip()
                
                # Check if class has trailing space "product-title " - this is the title
                if "product-title " in class_attr or class_attr.endswith("product-title "):
                    if text:
                        return text
        except Exception:
            pass
        
        # Fallback selectors
        title_selectors = [
            "h1.product-title",
            "h1",
            ".product-name",
            ".entry-title",
            "h1.page-title"
        ]
        
        for selector in title_selectors:
            try:
                title_elem = driver.find_element(By.CSS_SELECTOR, selector)
                title_text = title_elem.text.strip()
                if title_text:
                    return title_text
            except NoSuchElementException:
                continue
        
        return ""
    except Exception as e:
        return ""


def extract_price(driver) -> str:
    """Extract product price from product page."""
    try:
        price_selectors = [
            ".new-price",  # Exact class from user
            ".price",
            ".product-price",
            ".price .amount",
            "span.price",
            "span.new-price",
            "bdi",
            ".woocommerce-Price-amount",
            ".product-summary .price"
        ]
        
        for selector in price_selectors:
            try:
                price_elems = driver.find_elements(By.CSS_SELECTOR, selector)
                for price_elem in price_elems:
                    price_text = price_elem.text.strip()
                    if price_text and any(ch.isdigit() for ch in price_text):
                        return price_text
            except Exception:
                continue
        
        return ""
    except Exception as e:
        return ""


def extract_description(driver) -> str:
    """
    Product description only — avoid `.product-summary` / broad `[class*='content']`
    which pulls breadcrumbs, price, stock, and buttons.
    """
    heading_tags = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

    def _clean_desc(text: str) -> str:
        if not text:
            return ""
        junk_line_markers = [
            "free delivery",
            "secure payment",
            "support 24/7",
            "الرئيسية",
            "إضافة إلى السلة",
            "أضف إلى المفضلة",
            "مقارنة",
            "share:",
            "buy now",
            "click to enlarge",
            "back to products",
        ]
        lines = [ln.strip() for ln in str(text).splitlines()]
        kept: List[str] = []
        for ln in lines:
            if not ln:
                continue
            if any(m in ln for m in junk_line_markers):
                continue
            low = ln.lower()
            if any(m in low for m in ("share:", "buy now", "click to enlarge", "back to products")):
                continue
            kept.append(ln)
        return " ".join(" ".join(kept).split()).strip()

    def _norm_heading_text(tag) -> str:
        return " ".join((tag.get_text(" ", strip=True) or "").split())

    def _is_description_heading(tag) -> bool:
        if getattr(tag, "name", None) not in heading_tags:
            return False
        t = _norm_heading_text(tag)
        tl = t.lower()
        return tl == "description" or t == "الوصف"

    def _is_recently_viewed_heading(tag) -> bool:
        if getattr(tag, "name", None) not in heading_tags:
            return False
        t = _norm_heading_text(tag)
        tl = t.lower()
        return tl == "recently viewed" or "شوهد مؤخر" in t

    try:
        soup = BeautifulSoup(driver.page_source or "", "html.parser")

        # 1) WoodMart / Elementor product body (same as bashitihardware_scraper.py)
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
                items = []
                for li in ul.find_all("li"):
                    t = _clean_desc(li.get_text("\n", strip=True))
                    if t:
                        items.append(t)
                if items:
                    parts.append("\n".join(f"- {x}" for x in items))
            final = "\n".join(p for p in parts if p).strip()
            if final:
                return final

        # 2) WooCommerce description tab (safe, narrow)
        tab = soup.select_one("#tab-description, .woocommerce-Tabs-panel--description")
        if tab:
            text = _clean_desc(tab.get_text("\n", strip=True))
            if text and len(text) > 20:
                return text

        # 3) Between "Description" / "الوصف" and "Recently Viewed"
        desc_header = soup.find(_is_description_heading)
        if desc_header:
            collected: List[str] = []
            seen = set()
            for el in desc_header.find_all_next():
                if getattr(el, "name", None) in heading_tags and _is_recently_viewed_heading(el):
                    break
                name = getattr(el, "name", None)
                if name not in {"p", "li", "h3", "h4"}:
                    continue
                text = _clean_desc(el.get_text("\n", strip=True))
                if not text or len(text) <= 3:
                    continue
                line = f"- {text}" if name == "li" else text
                if line not in seen:
                    seen.add(line)
                    collected.append(line)
            out = "\n".join(collected).strip()
            if out and len(out) > 20:
                return out

        return ""
    except Exception:
        return ""


IMAGE_BASE = "https://bashitihardware.com"


def _normalize_image_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return IMAGE_BASE + u
    return u


def _urls_from_srcset(srcset: str) -> List[str]:
    urls: List[str] = []
    for part in (srcset or "").split(","):
        part = part.strip()
        if not part:
            continue
        urls.append(part.split()[0])
    return urls


def _image_identity_key(u: str) -> str:
    """Group same image across WP/WooCommerce size variants."""
    path = urlparse(u).path.lower()
    name = path.split("/")[-1]
    name = re.sub(r"-\d+x\d+(?=\.[a-z0-9]+$)", "", name)
    path = re.sub(r"/cache/[^/]+/", "/", path)
    return path or name


def _image_size_score(u: str) -> int:
    u_l = u.lower()
    score = 0

    for m in re.finditer(r"(\d{2,4})x(\d{2,4})", u_l):
        w, h = int(m.group(1)), int(m.group(2))
        score = max(score, w * h)

    m = re.search(r"/cache/w(\d+)(?:/|$)", u_l)
    if m:
        w = int(m.group(1))
        score = max(score, w * w)

    if "zoom" in u_l or "large" in u_l or "full" in u_l:
        score += 500
    if "thumbnail" in u_l or "thumb" in u_l:
        score -= 10_000
    if "small" in u_l or "swatch" in u_l:
        score -= 5_000
    if not re.search(r"-\d+x\d+\.", u_l) and "/cache/w" not in u_l:
        score += 1_000

    return score


def pick_largest_image_urls(candidates: List[str]) -> List[str]:
    """One URL per image — keep the largest variant."""
    best: Dict[str, tuple[int, str]] = {}
    order: List[str] = []

    for raw in candidates:
        u = _normalize_image_url(raw)
        if not u.startswith("http") or "placeholder" in u.lower():
            continue
        key = _image_identity_key(u)
        if key not in order:
            order.append(key)
        s = _image_size_score(u)
        if key not in best or s > best[key][0]:
            best[key] = (s, u)

    return [best[k][1] for k in order if k in best]


def _collect_img_candidates(img) -> List[str]:
    urls: List[str] = []
    for attr in ("data-large_image", "data-zoom_image", "data-src", "src"):
        v = img.get_attribute(attr)
        if v:
            urls.append(v)
    srcset = img.get_attribute("srcset")
    if srcset:
        urls.extend(_urls_from_srcset(srcset))
    return urls


def _collect_element_candidates(elem) -> List[str]:
    urls: List[str] = []
    href = elem.get_attribute("href")
    if href:
        urls.append(href)
    try:
        for img in elem.find_elements(By.TAG_NAME, "img"):
            urls.extend(_collect_img_candidates(img))
    except Exception:
        pass
    return urls


def extract_images(driver, max_images: int = 10) -> List[str]:
    """Extract product images; keep only the largest size per image."""
    candidates: List[str] = []

    try:
        easyzoom_selectors = [
            "a.easyzoom.easyzoom--overlay.is-ready",
            "a.easyzoom",
            ".easyzoom.easyzoom--overlay.is-ready",
            ".easyzoom",
            "[class*='easyzoom']",
        ]
        for selector in easyzoom_selectors:
            try:
                for elem in driver.find_elements(By.CSS_SELECTOR, selector):
                    candidates.extend(_collect_element_candidates(elem))
                if candidates:
                    break
            except Exception:
                continue

        if not candidates:
            img_selectors = [
                ".woocommerce-product-gallery__image a",
                ".woocommerce-product-gallery__image img",
                ".product-gallery img",
                ".product-images img",
                "img.product-image",
                ".wp-post-image",
                ".product-single img",
            ]
            for selector in img_selectors:
                try:
                    for elem in driver.find_elements(By.CSS_SELECTOR, selector):
                        tag = (elem.tag_name or "").lower()
                        if tag == "img":
                            candidates.extend(_collect_img_candidates(elem))
                        else:
                            candidates.extend(_collect_element_candidates(elem))
                    if candidates:
                        break
                except Exception:
                    continue

        if not candidates:
            soup = BeautifulSoup(driver.page_source, "html.parser")
            for elem in soup.find_all(class_=re.compile("easyzoom")):
                href = elem.get("href")
                if href:
                    candidates.append(href)
                for img in elem.find_all("img"):
                    for attr in ("data-large_image", "data-zoom_image", "data-src", "src"):
                        v = img.get(attr)
                        if v:
                            candidates.append(v)
                    candidates.extend(_urls_from_srcset(img.get("srcset") or ""))

            if not candidates:
                for img in soup.select(
                    ".woocommerce-product-gallery__image img, .product-gallery img, .wp-post-image"
                ):
                    for attr in ("data-large_image", "data-zoom_image", "data-src", "src"):
                        v = img.get(attr)
                        if v:
                            candidates.append(v)
                    candidates.extend(_urls_from_srcset(img.get("srcset") or ""))

        return pick_largest_image_urls(candidates)[:max_images]

    except Exception:
        return []


def scrape_product(driver, product_url: str, wait: WebDriverWait, pause: float, max_images: int = 10) -> Dict[str, Any]:
    """
    Scrape data for a single product page (bashitihardware WooCommerce).
    Handles error pages when product doesn't exist.
    """
    result = {
        "ProductURL": product_url,
        "SKU": "",
        "Title": "",
        "Price": "",
        "Description": "",
        "Images": [],
        "Found": False,
        "Status": "PENDING"
    }
    
    try:
        print(f"   → Scraping: {product_url}")
        driver.get(product_url)
        time.sleep(pause)
        
        # Extract SKU - try from .product-title element (without trailing space) first, then from URL
        try:
            # SKU is in .product-title (without trailing space)
            # Find all product-title elements and get the one WITHOUT trailing space
            elems = driver.find_elements(By.XPATH, "//*[contains(@class, 'product-title')]")
            for elem in elems:
                class_attr = elem.get_attribute("class") or ""
                text = elem.text.strip()
                
                # Check if class does NOT have trailing space after "product-title"
                # This means: class contains "product-title" but NOT "product-title " (with space after)
                has_trailing_space = "product-title " in class_attr or class_attr.endswith("product-title ")
                
                if text and not has_trailing_space:
                    # This is the SKU element (without trailing space)
                    result["SKU"] = text
                    break
        except Exception:
            pass
        
        # Fallback to URL if SKU not found (WooCommerce: /product/... or from query)
        if not result["SKU"]:
            sku_match = re.search(r"/single-product/([^/]+)", product_url)
            if sku_match:
                result["SKU"] = sku_match.group(1)
            else:
                # WooCommerce product slug in URL
                slug_match = re.search(r"/product/([^/]+)/?", product_url)
                if slug_match:
                    result["SKU"] = slug_match.group(1)
        
        # Check if it's a Laravel error page
        if is_laravel_error_page(driver):
            print(f"   ⚠️  Product not found (Laravel error page)")
            result["Status"] = "NOT_FOUND"
            result["Note"] = "Product not found - SKU doesn't exist in database"
            
            # Extract debug info for learning (optional)
            debug_info = extract_laravel_debug_info(driver)
            result["DebugInfo"] = json.dumps(debug_info, indent=2)
            
            return result
        
        # Wait for product page to load
        try:
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR, 
                    "h1, .product-title, .product, .product-container, .product-main"
                ))
            )
        except TimeoutException:
            # Double-check if it's an error page
            if is_laravel_error_page(driver):
                result["Status"] = "NOT_FOUND"
                result["Note"] = "Product not found - Laravel error page"
                return result
            raise
        
        # Extract product data
        title = extract_title(driver)
        result["Title"] = title
        if title:
            print(f"   → Found title: {title[:60]}...")
        
        price = extract_price(driver)
        result["Price"] = price
        if price:
            print(f"   → Found price: {price}")
        
        description = extract_description(driver)
        result["Description"] = description
        if description:
            print(f"   → Found description: {len(description)} chars")
        
        images = extract_images(driver, max_images)
        result["Images"] = images
        if images:
            print(f"   → Found {len(images)} images")
        
        # Determine if product was found
        result["Found"] = bool(title or price or description or images)
        result["Status"] = "FOUND" if result["Found"] else "EMPTY"
        
        return result
        
    except TimeoutException:
        print(f"   → Timeout loading product page")
        result["Status"] = "TIMEOUT"
        result["Note"] = "Timeout loading page"
        
        # Check if it's an error page
        if is_laravel_error_page(driver):
            result["Status"] = "NOT_FOUND"
            result["Note"] = "Product not found - Laravel error page"
        
        return result
    except Exception as e:
        print(f"   → Error scraping product: {e}")
        result["Status"] = "ERROR"
        result["Note"] = f"Error: {e}"
        
        # Final check for error page
        if is_laravel_error_page(driver):
            result["Status"] = "NOT_FOUND"
            result["Note"] = "Product not found - Laravel error page"
        
        return result


def find_sku_or_url_column(df: pd.DataFrame, user_col: Optional[str] = None) -> Optional[str]:
    """
    Find SKU or URL column in DataFrame.
    Auto-detects common column names.
    """
    if user_col and user_col in df.columns:
        return user_col
    
    # Common SKU column names
    sku_patterns = [
        "sku", "item", "itemno", "item_no", "code", "product_code",
        "product_id", "id", "item_code", "itemnumber"
    ]
    
    # Common URL column names
    url_patterns = [
        "url", "link", "product_url", "producturl", "product_link",
        "href", "website", "source_url"
    ]
    
    # Check for exact matches first
    for col in df.columns:
        col_lower = str(col).lower()
        if user_col and col_lower == user_col.lower():
            return col
        if any(pattern in col_lower for pattern in sku_patterns + url_patterns):
            return col
    
    # Check if first column contains URLs
    if len(df.columns) > 0:
        first_col = df.columns[0]
        sample = df[first_col].dropna().astype(str).head(10)
        if sample.str.contains(r"^https?://", case=False, regex=True).any():
            return first_col

    # Fallback: if headers look meaningless (e.g. Unnamed) try a data-driven guess
    # by selecting the first column that has at least one plausible SKU-like value.
    def is_plausible(val: str) -> bool:
        # simple heuristic: alphanumeric with few symbols or starts with http
        val = val.strip()
        if not val or val.lower() in ("nan", "none"):
            return False
        if val.startswith("http"):
            return True
        # allow letters, digits, dashes, underscores, slashes
        if re.match(r"^[A-Za-z0-9_\-/]+$", val):
            return True
        return False

    for col in df.columns:
        sample_vals = df[col].dropna().astype(str).head(20)
        if any(is_plausible(str(v)) for v in sample_vals):
            # give user a heads up if we are guessing
            print(f"   → Auto-detected column '{col}' based on sample values")
            return col

    return None


def resolve_input_path(path_str: str) -> tuple[Optional[Path], List[Path]]:
    """Resolve input file from cwd, script directory, or absolute path."""
    raw = Path(path_str)
    script_dir = Path(__file__).resolve().parent

    bases: List[Path] = []
    if raw.is_absolute():
        bases.append(raw)
    else:
        bases.append(Path.cwd() / raw)
        bases.append(script_dir / raw.name)
        if raw.parts and raw.parts[0].lower() == script_dir.name.lower():
            bases.append(script_dir / Path(*raw.parts[1:]))
        bases.append(script_dir / raw)

    if raw.suffix.lower() == ".xlsx":
        alt_extensions = [".xls", ".XLS"]
    elif raw.suffix.lower() == ".xls":
        alt_extensions = [".xlsx", ".XLSX"]
    else:
        alt_extensions = [".xls", ".xlsx", ".XLS", ".XLSX"]

    tried: List[Path] = []
    seen: set[str] = set()
    for base in bases:
        candidates = [base]
        if base.suffix:
            candidates.extend(base.with_suffix(ext) for ext in alt_extensions)
        else:
            candidates.extend(base.with_suffix(ext) for ext in alt_extensions)

        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            tried.append(candidate)
            if candidate.exists():
                return candidate.resolve(), tried

    return None, tried


def main():
    """Main function for CLI usage."""
    # Windows consoles often default to cp1252/cp1256 which can crash printing Arabic headers.
    # Force UTF-8 with replacement to keep the scraper running.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Bashiti Hardware Scraper - Scrapes products from bashitihardware.com via SKU search"
    )
    parser.add_argument(
        "--in", 
        dest="input_file", 
        # default refers to file located alongside this script; avoid backslashes to
        # prevent escape-sequence warnings and duplicate path segments when
        # resolved relative to script dir.
        default="injco_updated.xlsx",
        help="Input Excel file with SKUs or URLs (default: injco_updated.xlsx)"
    )
    parser.add_argument(
        "--out", 
        dest="output_file", 
        # Use a simple file name; the previous path introduced an escape-sequence
        # (\b) which erased characters in the printed output.
        default="jadeverscraped.xlsx",
        help="Output Excel file path (default: injco_updated_scraped.xlsx)"
    )
    parser.add_argument(
        "--sku-col", 
        dest="sku_col", 
        default=None,
        help="SKU or URL column name (auto-detect if omitted)"
    )
    parser.add_argument(
        "--url", 
        help="Single product URL to scrape"
    )
    parser.add_argument(
        "--sku", 
        help="Single SKU to scrape"
    )
    parser.add_argument(
        "--pause", 
        type=float, 
        default=2.0, 
        help="Pause between requests in seconds (default: 2.0)"
    )
    parser.add_argument(
        "--headless", 
        action="store_true", 
        help="Run in headless mode"
    )
    parser.add_argument(
        "--max-img", 
        type=int, 
        default=10, 
        help="Maximum images to collect per product (default: 10)"
    )
    parser.add_argument(
        "--sheet", 
        default=0,
        help="Excel sheet name or index (default: 0)"
    )
    
    args = parser.parse_args()
    
    base_url = "https://bashitihardware.com/ar"
    search_url_template = base_url + "/?s={}&post_type=product"
    
    # Create output directory if needed
    output_dir = Path(args.output_file).parent
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    
    # Build driver
    driver = build_driver(headless=args.headless)
    wait = WebDriverWait(driver, 20)
    
    try:
        print(f"🔍 Bashiti Hardware Scraper (search: bashitihardware.com/ar/?s=SKU&post_type=product)")
        print(f"Output: {args.output_file}")
        print(f"Pause: {args.pause}s")
        print()
        
        results = []
        
        def process_sku(sku: str):
            """Search by SKU and scrape directly from search results page."""
            search_url = search_url_template.format(quote_plus(sku))
            print(f"   → Search: {search_url}")
            return scrape_search_results_page(
                driver=driver,
                search_url=search_url,
                wait=wait,
                pause=args.pause,
                searched_sku=sku,
                max_images=args.max_img,
            )
        
        # Handle single URL (direct product page)
        if args.url:
            product_url = args.url
            if not product_url.startswith("http"):
                product_url = search_url_template.format(quote_plus(args.url))
                product_url = get_product_url_from_search(driver, product_url, wait, args.pause)
                if product_url is None:
                    result = {
                        "ProductURL": search_url_template.format(quote_plus(args.url)),
                        "SKU": args.url,
                        "Title": "", "Price": "", "Description": "", "Images": [],
                        "Found": False, "Status": "NOT_FOUND", "Note": "No search results"
                    }
                else:
                    result = scrape_product(driver, product_url, wait, args.pause, args.max_img)
            else:
                result = scrape_product(driver, product_url, wait, args.pause, args.max_img)
            results.append(result)
        
        # Handle single SKU (search then scrape)
        elif args.sku:
            result = process_sku(args.sku)
            results.append(result)
        
        # Handle Excel file
        elif args.input_file:
            input_path, tried_paths = resolve_input_path(args.input_file)
            if input_path is None:
                print(f" Error: Input file not found: {args.input_file}")
                for tried in tried_paths:
                    print(f"   Tried: {tried}")
                return
            
            print(f" Loading Excel file: {input_path}")
            try:
                df = pd.read_excel(input_path, sheet_name=args.sheet)
                print(f" Loaded {len(df)} rows")
            except Exception as e:
                print(f" Error reading Excel file: {e}")
                return
            
            # Find SKU or URL column
            col_name = find_sku_or_url_column(df, args.sku_col)
            if not col_name:
                print(f" Error: Could not find SKU or URL column in Excel file")
                print(f"   Available columns: {list(df.columns)}")
                # show the first few rows to help user identify the right column
                preview = df.head(3).to_dict(orient="list")
                print(f"   Sample data (first 3 rows): {preview}")
                print(f"   Use --sku-col to specify column name or check the input file format")
                return
            
            print(f" Using column: '{col_name}'")

            # Scrape row-by-row (preserve original columns), caching by SKU/URL to avoid re-scraping duplicates.
            # This is important for sheets like injco_updated.xlsx which contain pricing columns per SKU.
            cache: Dict[str, Dict[str, Any]] = {}
            total_rows = len(df)

            # Ensure output columns exist (we keep any existing columns untouched)
            out_cols = ["ProductURL", "Title", "Price", "Description", "Images", "Found", "Status", "Note"]
            for c in out_cols:
                if c not in df.columns:
                    df[c] = ""

            for row_idx in range(total_rows):
                raw_value = df.at[row_idx, col_name]
                if pd.isna(raw_value):
                    continue
                value = str(raw_value).strip()
                if not value or value.lower() in ("nan", "none", ""):
                    continue

                print(f"\n[{row_idx+1}/{total_rows}] Processing: {value}")

                if value in cache:
                    result = cache[value]
                else:
                    if value.startswith("http"):
                        result = scrape_product(driver, value, wait, args.pause, args.max_img)
                    else:
                        result = process_sku(value)
                        if result.get("SKU") == "":
                            result["SKU"] = value
                    cache[value] = result

                results.append(result)

                # Write scraped data back into the same row
                df.at[row_idx, "ProductURL"] = result.get("ProductURL", "")
                df.at[row_idx, "Title"] = result.get("Title", "")
                df.at[row_idx, "Price"] = result.get("Price", "")
                df.at[row_idx, "Description"] = result.get("Description", "")
                imgs = result.get("Images", [])
                df.at[row_idx, "Images"] = ";".join(imgs) if isinstance(imgs, list) else (imgs or "")
                df.at[row_idx, "Found"] = bool(result.get("Found", False))
                df.at[row_idx, "Status"] = result.get("Status", "")
                df.at[row_idx, "Note"] = result.get("Note", "")

                # Status summary
                status = result.get("Status", "UNKNOWN")
                found = result.get("Found", False)
                title = (result.get("Title") or "")[:40]
                print(f"    → Status: {status} | Found: {found} | Title: {title}...")

                time.sleep(args.pause)
        
        else:
            print(" Error: Must provide --url, --sku, or --in")
            return
        
        # Create results DataFrame
        if not results:
            print(" No results to save")
            return
        
        # Save results.
        # If we processed an Excel sheet, df contains original columns + scraped columns.
        # Otherwise, save the standalone results dataframe.
        if args.input_file and "df" in locals():
            df.to_excel(args.output_file, index=False)
        else:
            results_df = pd.DataFrame(results)
            results_df["Images"] = results_df["Images"].apply(
                lambda lst: ";".join(lst) if isinstance(lst, list) and lst else ""
            )
            results_df.to_excel(args.output_file, index=False)
        
        # Print summary
        found_count = sum(1 for r in results if r.get("Found", False))
        not_found_count = sum(1 for r in results if r.get("Status") == "NOT_FOUND")
        error_count = sum(1 for r in results if r.get("Status") == "ERROR")
        
        print(f"\n Scraping Complete!")
        print(f"   Total products: {len(results)}")
        print(f"   Found: {found_count}")
        print(f"   Not Found: {not_found_count}")
        print(f"   Errors: {error_count}")
        print(f"   Success Rate: {found_count/len(results)*100:.1f}%")
        print(f"   Results saved to: {args.output_file}")
    finally:
        driver.quit()
if __name__ == "__main__":
    main()
