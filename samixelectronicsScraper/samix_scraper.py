"""
Samix Electronics Scraper
Reads SNK Ref from Excel, navigates directly to product pages,
and scrapes product images, title, and description.
"""

import time
import argparse
import os
from urllib.parse import urljoin
from typing import List, Dict, Any
import re

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup


def build_driver(headful: bool = False) -> webdriver.Chrome:
    """Build and return Chrome WebDriver."""
    options = webdriver.ChromeOptions()
    if not headful:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


def navigate_to_product_page(
    driver, wait: WebDriverWait, snk_ref: str, pause: float = 2.0
) -> str:
    """
    Navigate directly to product page using SNK Ref.
    Returns product URL.
    """
    product_url = f"https://samixelectronics.com/index.php/{snk_ref}.html"
    print(f"   → Navigating to: {product_url}")
    driver.get(product_url)
    time.sleep(pause)
    return product_url


def extract_product_title(driver) -> str:
    """Extract product title from page."""
    title_selectors = [
        "h1.product-title",
        "h1.product-name",
        "h1.product-name h1",
        ".product-title",
        ".product-name",
        "h1",
        ".product-info h1",
        ".product-header h1",
        "[itemprop='name']",
        ".title",
        "h1.page-title",
    ]
    
    for selector in title_selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            title = element.text.strip()
            if title:
                print(f"   → Title: {title[:80]}...")
                return title
        except Exception:
            continue
    
    print("   → No title found")
    return ""


def extract_product_price(driver) -> str:
    """Extract product price from page."""
    # Get page source and parse with BeautifulSoup
    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")
    
    # Remove script and style tags
    for script in soup(["script", "style", "noscript"]):
        script.decompose()
    
    price_selectors = [
        ".product-description",
        ".product-description-full",
        ".product-details",
        ".product-info",
        ".product-content",
        ".description",
        ".item-description",
        ".product-specifications",
        ".specifications",
        "#description",
        "[itemprop='description']",
        ".product-summary",
        ".woocommerce-product-details__short-description",
        ".woocommerce-Tabs-panel--description",
        "#tab-description",
        ".product__description",
    ]
    
    # Try CSS selectors first
    for selector in price_selectors:
        try:
            element = soup.select_one(selector)
            if element:
                price = element.get_text(" ", strip=True)
                if price and len(price) > 5:  # Price can be shorter
                    print(f"   → Price: {price[:100]}...")
                    return price
        except Exception:
            continue
    
    # Try to find price via Selenium as fallback
    for selector in price_selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            price = element.text.strip()
            if price and len(price) > 5:
                print(f"   → Price: {price[:100]}...")
                return price
        except Exception:
            continue
    
    print("   → No price found")
    return ""


def extract_product_description(driver) -> str:
    """Extract product description from .short-description class."""
    # Try Selenium first
    try:
        desc_element = driver.find_element(By.CSS_SELECTOR, ".short-description")
        desc = desc_element.text.strip()
        if desc:
            print(f"   → Description: {len(desc)} characters")
            return desc
    except Exception:
        pass
    
    # Try BeautifulSoup
    try:
        html = driver.page_source
        soup = BeautifulSoup(html, "lxml")
        desc_element = soup.select_one(".short-description")
        if desc_element:
            desc = desc_element.get_text(" ", strip=True)
            if desc:
                print(f"   → Description: {len(desc)} characters")
                return desc
    except Exception:
        pass
    
    print("   → No description found")
    return ""


def clean_image_url(url: str) -> str:
    """Remove size parameters from URL to get base URL for deduplication."""
    # Extract domain and path
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path
    
    # Handle Magento cache URLs: remove /cache/1/thumbnail/SIZE/ and hash
    # Example: /cache/1/thumbnail/600x/17f82f742ffe127f42dca9de82fb58b1/0/0/007b.jpg
    # Should normalize to just the filename or base path for comparison
    path = re.sub(r'/cache/\d+/thumbnail/\d+x\d+/[^/]+/', '/', path)
    path = re.sub(r'/cache/\d+/thumbnail/\d+x\d+x\d+/[^/]+/', '/', path)
    path = re.sub(r'/cache/\d+/thumbnail/[^/]+/', '/', path)
    
    # Extract just the filename (last part after /)
    filename_match = re.search(r'/([^/]+\.(jpg|jpeg|png|gif|webp))$', path, re.I)
    if filename_match:
        # For Magento, sometimes same image has different paths but same filename
        # e.g., /0/0/007b.jpg vs /s/n/snk-007b.jpg - but both end with 007b.jpg
        # So use just the filename for deduplication
        filename = filename_match.group(1).lower()
        return f"{parsed.netloc}:{filename}"
    
    # Remove common size parameters from query string
    query = parsed.query
    query = re.sub(r'[?&](w|h|width|height|size|resize|fit|format|auto|quality|q|scale|s)=[^&]*', '', query)
    query = re.sub(r'[?&]+$', '', query)
    
    # Remove common size indicators in path (after cache removal)
    path = re.sub(r'/(\d+)x(\d+)/', '/', path)
    path = re.sub(r'/(thumb|small|medium|large|big|full|original|zoom)[_-]', '/', path, flags=re.I)
    
    # Return domain + cleaned path for comparison
    return f"{parsed.netloc}:{path}"


def get_image_size_score(url: str) -> int:
    """Score image URL based on size indicators. Higher score = larger image."""
    url_lower = url.lower()
    score = 0
    
    # Check for Magento cache size patterns: /cache/1/thumbnail/600x/ or /cache/1/thumbnail/100x100/
    magento_cache_match = re.search(r'/cache/\d+/thumbnail/(\d+)x(\d+)', url_lower)
    if magento_cache_match:
        w, h = int(magento_cache_match.group(1)), int(magento_cache_match.group(2))
        score = w * h  # Use pixel area as score
        return score
    
    # Check for size parameters in query string
    size_match = re.search(r'[?&](?:w|width)=(\d+)', url_lower)
    if size_match:
        score = int(size_match.group(1))
    
    # Check for size keywords in URL
    if any(x in url_lower for x in ['original', 'full', 'large', 'big', 'zoom']):
        score = max(score, 1000)
    elif any(x in url_lower for x in ['medium', 'med']):
        score = max(score, 500)
    elif any(x in url_lower for x in ['small', 'thumb', 'mini']):
        score = max(score, 100)
    
    # Check for dimensions in path (generic pattern)
    dim_match = re.search(r'/(\d{3,4})x(\d{3,4})/', url_lower)
    if dim_match:
        w, h = int(dim_match.group(1)), int(dim_match.group(2))
        score = max(score, w * h // 100)
    
    return score


def extract_product_images(driver, base_url: str = "https://samixelectronics.com") -> List[str]:
    """Extract product images from page, prioritizing largest size and removing duplicates."""
    all_image_urls = []  # Store all found URLs with their sources
    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")
    
    # Scroll to trigger lazy loading
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
    time.sleep(1)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)
    
    # FIRST: Try to find etalage container (specific to samixelectronics)
    etalage_selectors = [
        ".etalage img",
        "[class*='etalage'] img",
        "[id*='etalage'] img",
        "div.etalage img",
        "div[id^='etalage_'] img",
    ]
    
    # Try Selenium first for etalage (might be dynamic)
    try:
        # Look for etalage container by class or id
        etalage_containers = driver.find_elements(By.CSS_SELECTOR, ".etalage, [id*='etalage'], [class*='etalage']")
        
        for container in etalage_containers:
            # Get all img tags inside etalage container
            imgs = container.find_elements(By.TAG_NAME, "img")
            for img_elem in imgs:
                # Prioritize zoom/original over thumbnail
                src = (img_elem.get_attribute("data-zoom-image") or 
                       img_elem.get_attribute("data-original") or
                       img_elem.get_attribute("data-image") or
                       img_elem.get_attribute("src") or 
                       img_elem.get_attribute("data-src") or 
                       img_elem.get_attribute("data-lazy-src"))
                
                if src:
                    # Make absolute URL
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = urljoin(base_url, src)
                    elif not src.startswith("http"):
                        src = urljoin(base_url, src)
                    
                    # Filter out non-product images
                    low_src = src.lower()
                    skip_terms = ["logo", "icon", "favicon", "banner", "placeholder", "sprite", "data:image", "base64"]
                    if any(term in low_src for term in skip_terms):
                        continue
                    
                    if src.startswith("http"):
                        all_image_urls.append(src)
                        print(f"   → Found etalage image: {src[:80]}...")
            
            # Also check for li items or anchor tags that might contain images
            items = container.find_elements(By.CSS_SELECTOR, "li, a")
            for item in items:
                # Prioritize zoom-image over regular image
                data_src = (item.get_attribute("data-zoom-image") or
                           item.get_attribute("data-image") or
                           item.get_attribute("href"))
                
                if data_src and ("jpg" in data_src.lower() or "png" in data_src.lower() or "jpeg" in data_src.lower() or "webp" in data_src.lower()):
                    if data_src.startswith("//"):
                        data_src = "https:" + data_src
                    elif data_src.startswith("/"):
                        data_src = urljoin(base_url, data_src)
                    elif not data_src.startswith("http"):
                        data_src = urljoin(base_url, data_src)
                    
                    if data_src.startswith("http"):
                        all_image_urls.append(data_src)
                        print(f"   → Found etalage item image: {data_src[:80]}...")
                        
    except Exception as e:
        print(f"   → Error extracting from etalage: {e}")
    
    # Also try BeautifulSoup for etalage
    for selector in etalage_selectors:
        try:
            imgs = soup.select(selector)
            for img in imgs:
                src = (img.get("data-zoom-image") or
                       img.get("data-original") or
                       img.get("data-image") or
                       img.get("src") or 
                       img.get("data-src") or 
                       img.get("data-lazy-src"))
                if src:
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = urljoin(base_url, src)
                    elif not src.startswith("http"):
                        src = urljoin(base_url, src)
                    if src.startswith("http"):
                        all_image_urls.append(src)
                        print(f"   → Found etalage image (BS): {src[:80]}...")
        except Exception:
            continue
    
    # Process and deduplicate images
    if all_image_urls:
        # Group by base URL (without size parameters)
        base_url_to_images = {}  # base_url -> list of (full_url, score)
        
        for url in all_image_urls:
            base = clean_image_url(url)
            score = get_image_size_score(url)
            
            if base not in base_url_to_images:
                base_url_to_images[base] = []
            base_url_to_images[base].append((url, score))
        
        # For each base URL, keep only the largest version
        final_images = []
        for base, variants in base_url_to_images.items():
            # Sort by score (largest first) and take the best one
            variants.sort(key=lambda x: x[1], reverse=True)
            best_url = variants[0][0]
            final_images.append((best_url, variants[0][1]))
        
        # Sort final images by score (largest first)
        final_images.sort(key=lambda x: x[1], reverse=True)
        images = [url for url, score in final_images]
        
        print(f"   → Found {len(images)} unique images from etalage (deduplicated from {len(all_image_urls)} candidates)")
        return images
    
    # Fallback: Generic image selectors for product pages
    image_selectors = [
        ".product-images img",
        ".product-gallery img",
        ".product-photos img",
        ".product-images img",
        ".woocommerce-product-gallery img",
        ".product-single__photos img",
        ".product-media img",
        "img[itemprop='image']",
        ".main-product-image img",
        ".product-thumbnails img",
        "figure.product-image img",
    ]
    
    candidates = set()
    
    # Try CSS selectors
    for selector in image_selectors:
        try:
            imgs = soup.select(selector)
            for img in imgs:
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if not src:
                    continue
                
                # Make absolute URL
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = urljoin(base_url, src)
                elif not src.startswith("http"):
                    src = urljoin(base_url, src)
                
                # Filter out non-product images
                low_src = src.lower()
                skip_terms = [
                    "logo", "icon", "favicon", "banner", "header", "footer",
                    "placeholder", "sprite", "data:image", "base64"
                ]
                if any(term in low_src for term in skip_terms):
                    continue
                
                candidates.add(src)
        except Exception:
            continue
    
    # Also try Selenium approach
    for selector in image_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for elem in elements:
                src = elem.get_attribute("src") or elem.get_attribute("data-src") or elem.get_attribute("data-lazy-src")
                if not src:
                    continue
                
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = urljoin(base_url, src)
                elif not src.startswith("http"):
                    src = urljoin(base_url, src)
                
                low_src = src.lower()
                skip_terms = [
                    "logo", "icon", "favicon", "banner", "header", "footer",
                    "placeholder", "sprite", "data:image", "base64"
                ]
                if any(term in low_src for term in skip_terms):
                    continue
                
                candidates.add(src)
        except Exception:
            continue
    
    # Clean and deduplicate URLs
    images = []
    seen_base = set()
    for url in candidates:
        # Remove size parameters to deduplicate
        base_url_clean = re.sub(r'[?&](w|h|width|height|size|resize|fit|format|auto)=[^&]*', '', url)
        base_url_clean = re.sub(r'[?&]+$', '', base_url_clean)
        
        if base_url_clean not in seen_base and url.startswith("http"):
            seen_base.add(base_url_clean)
            images.append(url)
    
    print(f"   → Found {len(images)} product images")
    return images


def scrape_snk_ref(
    driver, wait: WebDriverWait, snk_ref: str, pause: float = 2.0
) -> Dict[str, Any]:
    """
    Complete scraping workflow for one SNK Ref:
    1. Navigate directly to product page
    2. Scrape title, description, images
    """
    result = {
        "SNK Ref": snk_ref,
        "Product URL": "",
        "Title": "",
        "Price": "",
        "Description": "",
        "Images": "",
        "Image Count": 0,
        "Status": "FAILED",
        "Note": "",
    }
    
    try:
        # Step 1: Navigate directly to product page
        product_url = navigate_to_product_page(driver, wait, snk_ref, pause)
        result["Product URL"] = product_url
        
        # Wait for product page to load
        try:
            wait.until(
                EC.any_of(
                    EC.presence_of_element_located((By.TAG_NAME, "h1")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
                )
            )
        except TimeoutException:
            result["Note"] = "Page load timeout"
            return result
        
        time.sleep(pause)
        
        # Step 2: Extract title
        title = extract_product_title(driver)
        result["Title"] = title
        
        # Step 3: Extract price (from old description location)
        price = extract_product_price(driver)
        result["Price"] = price
        
        # Step 4: Extract description (from .short-description)
        description = extract_product_description(driver)
        result["Description"] = description
        
        # Step 5: Extract images
        images = extract_product_images(driver, product_url)
        result["Images"] = " | ".join(images)
        result["Image Count"] = len(images)
        
        # Determine status
        if title or price or description or images:
            if title and images:
                result["Status"] = "SUCCESS"
            else:
                result["Status"] = "PARTIAL"
        else:
            result["Status"] = "FAILED"
            result["Note"] = "No data extracted"
        
        print(f"   → Status: {result['Status']}")
        
    except Exception as e:
        result["Status"] = "ERROR"
        result["Note"] = str(e)[:200]
        print(f"   → Error: {e}")
    
    return result


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Samix Electronics scraper - navigates directly to product pages and scrapes product data"
    )
    parser.add_argument(
        "--input",
        default="samix.xlsx",
        help="Input Excel file (default: samix.xlsx)",
    )
    parser.add_argument(
        "--output",
        default="samix_scraped_results.xlsx",
        help="Output Excel file (default: samix_scraped_results.xlsx)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=2.0,
        help="Pause between actions in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run in headful mode (show browser)",
    )
    parser.add_argument(
        "--start-row",
        type=int,
        default=0,
        help="Start from row index (0-based, default: 0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of products to process (for testing)",
    )
    
    args = parser.parse_args()
    
    # Read Excel file
    input_path = os.path.join(os.path.dirname(__file__), args.input)
    if not os.path.exists(input_path):
        print(f"Error: Input file not found: {input_path}")
        return
    
    print(f" Reading Excel file: {input_path}")
    df = pd.read_excel(input_path)
    
    if "SNK Ref" not in df.columns:
        print("Error: Column 'SNK Ref' not found in Excel file")
        print(f"Available columns: {list(df.columns)}")
        return
    
    snk_refs = df["SNK Ref"].fillna("").astype(str).tolist()
    snk_refs = [sr.strip() for sr in snk_refs if sr.strip()]
    
    # Apply limit if specified
    if args.limit:
        snk_refs = snk_refs[:args.limit]
        print(f" TEST MODE: Limited to {args.limit} products")
    
    print(f" Found {len(snk_refs)} SNK Ref entries")
    print(f" Starting from row {args.start_row}")

    # Resume logic: load existing output and skip already-done SNK Refs
    existing_df = None
    already_done_refs: set = set()
    if os.path.exists(args.output):
        try:
            existing_df = pd.read_excel(args.output)
            if "SNK Ref" in existing_df.columns and "Status" in existing_df.columns:
                already_done_refs = set(
                    existing_df.loc[existing_df["Status"] == "SUCCESS", "SNK Ref"].astype(str).str.strip()
                )
            print(f" Resuming: {len(already_done_refs)} SNK Refs already done, will skip them")
        except Exception as e:
            print(f" Warning: could not read existing output ({e}), starting fresh")
            existing_df = None

    # Setup driver
    print(" Starting browser...")
    driver = build_driver(headful=args.headful)
    wait = WebDriverWait(driver, 20)

    results = []
    
    try:
        # Slice snk_refs based on start_row and limit
        start_idx = args.start_row
        end_idx = start_idx + args.limit if args.limit else len(snk_refs)
        snk_refs_to_process = snk_refs[start_idx:end_idx]
        total_to_process = len(snk_refs_to_process)
        
        for idx, snk_ref in enumerate(snk_refs_to_process, start=start_idx):
            if snk_ref in already_done_refs:
                print(f"\n[{idx + 1 - start_idx}/{total_to_process}] Skipping already-done: {snk_ref}")
                continue
            print(f"\n[{idx + 1 - start_idx}/{total_to_process}] Processing: {snk_ref}")
            
            result = scrape_snk_ref(driver, wait, snk_ref, args.pause)
            results.append(result)
            
            # Save checkpoint every 10 items
            if (idx + 1) % 10 == 0:
                checkpoint_df = pd.DataFrame(results)
                checkpoint_path = args.output.replace(".xlsx", f"_checkpoint_row_{idx + 1}.xlsx")
                checkpoint_df.to_excel(checkpoint_path, index=False)
                print(f"\n💾 Checkpoint saved: {checkpoint_path}")
            
            # Pause between items
            time.sleep(args.pause)
        
        # Save final results
        print(f"\n Saving results to: {args.output}")
        new_output_df = pd.DataFrame(results)
        if existing_df is not None and len(new_output_df) > 0:
            final_df = pd.concat([existing_df, new_output_df], ignore_index=True)
        elif existing_df is not None:
            final_df = existing_df
        else:
            final_df = new_output_df
        final_df.to_excel(args.output, index=False)
        
        # Print summary
        success = sum(1 for r in results if r["Status"] == "SUCCESS")
        partial = sum(1 for r in results if r["Status"] == "PARTIAL")
        failed = sum(1 for r in results if r["Status"] in ["FAILED", "ERROR"])
        
        print(f"\n Summary:")
        print(f"    Success: {success}")
        print(f"     Partial: {partial}")
        print(f"    Failed: {failed}")
        print(f"    Results saved to: {args.output}")
        
    finally:
        driver.quit()
        print("\n Browser closed")


if __name__ == "__main__":
    main()

