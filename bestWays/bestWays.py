#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
import time
import pandas as pd
from urllib.parse import quote_plus

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# -------------------- CONFIG --------------------
BASE_URL = "https://bestways.com"
WAIT_TIMEOUT = 25
PAUSE_BETWEEN_MODELS = 0.6


# -------------------- DRIVER --------------------
def build_driver(headless=False):
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


# -------------------- HELPERS --------------------
def clean_model(x):
    s = str(x).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def normalize_url(url):
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL + url
    return url


def get_highest_from_srcset(srcset):
    """
    Parse srcset and return the URL with the highest width descriptor.
    Example: "image-300w.jpg 300w, image-800w.jpg 800w" -> "image-800w.jpg"
    """
    if not srcset:
        return None
    best_url = None
    best_width = -1
    
    parts = [p.strip() for p in srcset.split(",") if p.strip()]
    for part in parts:
        # Match pattern like "url 800w"
        match = re.match(r'(.+?)\s+(\d+)w', part)
        if match:
            url = match.group(1).strip()
            width = int(match.group(2))
            if width > best_width:
                best_width = width
                best_url = url
        else:
            # If no width descriptor, use as fallback
            if best_url is None:
                best_url = part.split()[0].strip()
    
    return best_url


def remove_size_params_from_url(url):
    """
    Remove size parameters from Shopify image URLs to get the original/full size.
    Shopify URLs often have ?v= or size parameters.
    """
    if not url:
        return url
    
    # Remove common size parameters but keep the URL structure
    # Remove size parameters like ?v=1234567890, ?width=800, etc.
    url = re.sub(r'[?&](v|width|height|size|w|h)=\d+[^&]*', '', url)
    url = re.sub(r'[?&]+$', '', url)
    
    # For Shopify CDN URLs, try to get original by removing size indicators
    # Example: image_800x800.jpg -> image.jpg or image_1024x1024.jpg
    url = re.sub(r'_(\d+x\d+|\d+w)', '', url, flags=re.I)
    
    return url


# -------------------- SCRAPERS --------------------
def open_first_product(driver):
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, "ul#collection li.product-card")
        )
    )

    cards = driver.find_elements(By.CSS_SELECTOR, "ul#collection li.product-card")
    if not cards:
        return None

    href = cards[0].find_element(By.CSS_SELECTOR, "a").get_attribute("href")
    return normalize_url(href)


def verify_sku_on_page(driver, sku):
    """
    Check if the SKU is mentioned anywhere on the product page.
    Returns True if found, False otherwise.
    """
    if not sku:
        return True
    
    try:
        # Get the entire page source or text content
        page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
        sku_lower = sku.lower()
        
        if sku_lower in page_text:
            print(f"  ✓ SKU '{sku}' verified on page")
            return True
        else:
            print(f"  ⚠ WARNING: SKU '{sku}' NOT found on this product page!")
            return False
    except Exception as e:
        print(f"  → Error verifying SKU: {e}")
        return False


def scrape_title(driver):
    try:
        el = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "header.mobile-hide h2.m5")
            )
        )
        return el.text.strip()
    except:
        return None


def scrape_description(driver):
    try:
        el = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.visibility_of_element_located((By.CLASS_NAME, "tabs-inner"))
        )
        return el.text.strip()
    except:
        return None


def is_product_image(img):
    """
    Check if an image is a product image by examining its context and URL.
    Returns True if it's likely a product image, False otherwise.
    """
    try:
        # Get image URL
        src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        src_lower = src.lower()
        
        # Exclude common non-product images
        exclude_patterns = [
            "logo", "icon", "sprite", "placeholder", "loading", "spinner",
            "header", "footer", "nav", "menu", "banner", "ad", "advertisement",
            "cart", "wishlist", "search", "close", "arrow", "chevron",
            "social", "facebook", "twitter", "instagram", "youtube",
            "payment", "visa", "mastercard", "paypal", "trust", "badge"
        ]
        
        if any(pattern in src_lower for pattern in exclude_patterns):
            return False
        
        # Check parent elements to see if we're in a product gallery
        try:
            parent = img.find_element(By.XPATH, "./ancestor::*[contains(@class, 'swiper') or contains(@class, 'gallery') or contains(@class, 'product') or contains(@class, 'media')]")
            # If we found a product-related parent, it's likely a product image
            return True
        except:
            # No product-related parent found, check if URL suggests it's a product image
            # Product images often have patterns like: product, image, photo, media
            product_patterns = ["product", "image", "photo", "media", "cdn", "shopify"]
            if any(pattern in src_lower for pattern in product_patterns):
                # But exclude if it's clearly not a product image
                if not any(exclude in src_lower for exclude in ["logo", "icon", "sprite"]):
                    return True
        
        return False
    except:
        return False


def scrape_images_highres(driver):
    """
    Extract all product images from Shopify product page, getting the highest resolution.
    Only extracts images from product gallery areas, excluding navigation, logos, etc.
    """
    try:
        # Wait for page to load
        time.sleep(1)
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, "img"))
        )

        # First, try to find the product gallery container
        gallery_containers = [
            ".swiper-container",
            ".swiper-wrapper",
            ".product-gallery",
            ".product-images",
            "[data-product-image]",
            ".product__media",
            ".product__media-wrapper",
        ]
        
        gallery_container = None
        for container_selector in gallery_containers:
            try:
                containers = driver.find_elements(By.CSS_SELECTOR, container_selector)
                if containers:
                    gallery_container = containers[0]
                    break
            except:
                continue
        
        all_imgs = []
        seen_elements = set()
        
        if gallery_container:
            # If we found a gallery container, only look for images within it
            print("  → Found product gallery container")
            selectors = [
                ".swiper-slide img",
                ".swiper-slide-active img",
                "img[srcset]",
                "img[data-srcset]",
                "img",
            ]
            
            for selector in selectors:
                try:
                    imgs = gallery_container.find_elements(By.CSS_SELECTOR, selector)
                    for img in imgs:
                        try:
                            elem_id = img.id or str(img.location)
                            if elem_id not in seen_elements:
                                seen_elements.add(elem_id)
                                if is_product_image(img):
                                    all_imgs.append(img)
                        except:
                            pass
                except:
                    continue
        else:
            # Fallback: search with more specific selectors
            print("  → Gallery container not found, using specific selectors")
            selectors = [
                ".swiper-slide img",  # Swiper gallery images
                ".swiper-slide-active img",  # Active slide
                ".product-gallery img",  # Product gallery
                ".product-images img",  # Product images container
                "[data-product-image] img",  # Shopify product image data attribute
                ".product__media img",  # Shopify product media
            ]
            
            for selector in selectors:
                try:
                    imgs = driver.find_elements(By.CSS_SELECTOR, selector)
                    for img in imgs:
                        try:
                            elem_id = img.id or str(img.location)
                            if elem_id not in seen_elements:
                                seen_elements.add(elem_id)
                                if is_product_image(img):
                                    all_imgs.append(img)
                        except:
                            pass
                except:
                    continue

        if not all_imgs:
            print("  → No product images found")
            return None

        urls = []
        seen_urls = set()
        
        for img in all_imgs:
            try:
                # Priority 1: Check for Shopify high-res data attributes
                high_res_attrs = [
                    "data-zoom",
                    "data-large",
                    "data-large_image",
                    "data-zoom-image",
                    "data-full-image",
                    "data-original",
                    "data-src-large",
                    "data-hires",
                ]
                
                high_res_url = None
                for attr in high_res_attrs:
                    high_res_url = img.get_attribute(attr)
                    if high_res_url:
                        break
                
                if high_res_url:
                    high_res_url = normalize_url(high_res_url)
                    # Remove size parameters to get original
                    high_res_url = remove_size_params_from_url(high_res_url)
                    if high_res_url and high_res_url not in seen_urls:
                        urls.append(high_res_url)
                        seen_urls.add(high_res_url)
                    continue
                
                # Priority 2: Parse srcset to get highest resolution
                srcset = img.get_attribute("srcset") or img.get_attribute("data-srcset")
                if srcset:
                    best_url = get_highest_from_srcset(srcset)
                    if best_url:
                        best_url = normalize_url(best_url)
                        # Remove size parameters to get original
                        best_url = remove_size_params_from_url(best_url)
                        if best_url and best_url not in seen_urls:
                            urls.append(best_url)
                            seen_urls.add(best_url)
                        continue
                
                # Priority 3: Get current src
                src = img.get_attribute("src") or img.get_attribute("data-src")
                if src:
                    src = normalize_url(src)
                    # Skip thumbnails and icons
                    src_lower = src.lower()
                    if not any(thumb in src_lower for thumb in ["thumb", "thumbnail", "icon", "sprite", "placeholder", "loading"]):
                        # Remove size parameters to get original
                        src = remove_size_params_from_url(src)
                        if src and src not in seen_urls:
                            urls.append(src)
                            seen_urls.add(src)
            except:
                continue

        if urls:
            result = "; ".join(urls)
            print(f"  → Found {len(urls)} product images")
            return result
        else:
            print("  → No valid image URLs extracted")
            return None
            
    except Exception as e:
        print(f"  → Error scraping images: {e}")
        return None


# -------------------- MAIN --------------------
def main():
    ap = argparse.ArgumentParser(description="BestWays scraper by model/SKU")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", required=True, help="Output Excel file")
    ap.add_argument("--sku-col", dest="sku_col", default="Model", help="Column containing model/SKU (default: Model)")
    ap.add_argument("--headful", action="store_true", help="Show browser window")
    args = ap.parse_args()

    df = pd.read_excel(args.inp)

    if args.sku_col not in df.columns:
        raise ValueError(f'Excel must contain column "{args.sku_col}". Available: {list(df.columns)}')

    for col in ["product_url", "title", "description", "images_highres", "scrape_status", "scrape_error"]:
        if col not in df.columns:
            df[col] = ""

    driver = build_driver(headless=not args.headful)

    try:
        for idx, row in df.iterrows():
            model = clean_model(row[args.sku_col])

            if not model:
                df.at[idx, "scrape_status"] = "SKIPPED"
                continue

            if str(row["scrape_status"]).strip() == "OK":
                continue

            search_url = f"{BASE_URL}/search?q={quote_plus(model)}"
            print(f"[{idx+1}/{len(df)}] {model}")

            try:
                driver.get(search_url)
                WebDriverWait(driver, WAIT_TIMEOUT).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )

                product_url = open_first_product(driver)
                if not product_url:
                    df.at[idx, "scrape_status"] = "NO_RESULT"
                    continue

                driver.get(product_url)
                sku_found = verify_sku_on_page(driver, model)

                df.at[idx, "product_url"] = product_url
                df.at[idx, "title"] = scrape_title(driver)
                df.at[idx, "description"] = scrape_description(driver)
                df.at[idx, "images_highres"] = scrape_images_highres(driver)
                df.at[idx, "scrape_status"] = "OK"

                if not sku_found:
                    df.at[idx, "scrape_error"] = f"WARNING: SKU '{model}' NOT found on product page"

            except Exception as e:
                df.at[idx, "scrape_status"] = "ERROR"
                df.at[idx, "scrape_error"] = str(e)

            time.sleep(PAUSE_BETWEEN_MODELS)

    finally:
        driver.quit()

    df.to_excel(args.out, index=False)
    print(f"\nSaved results to: {args.out}")


if __name__ == "__main__":
    main()
