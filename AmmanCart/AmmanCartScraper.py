import time
import argparse
import os
import re
from urllib.parse import quote_plus, urljoin, urlparse
from typing import List, Dict, Any, Optional

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
def search_and_click_first_result(
    driver, wait: WebDriverWait, sku: str, pause: float = 2.0) -> Optional[str]:
    """
    Search AmmanCart with SKU and click first result.
    Returns product URL or None.
    """
    search_url = f"https://www.ammancart.com/a/search?q={quote_plus(sku)}&options%5Bprefix%5D=last"
    
    print(f"   → Searching: {sku}")
    print(f"   → URL: {search_url}")
    
    try:
        driver.get(search_url)
        time.sleep(pause)
        
        # Wait for search results to load
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-item, .product-card, a[href*='/products/'], a[href*='/product/']")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
            )
        )
        
        # Additional wait for dynamic content
        time.sleep(1)
        
    except TimeoutException:
        print(f"   → Timeout waiting for search results")
        return None
    except Exception as e:
        print(f"   → Error loading search page: {e}")
        return None
    
    # Look for first product link
    product_selectors = [
        "a[href*='/products/']",
        "a[href*='/product/']",
        ".product-item a",
        ".product-card a",
        "a.product-link",
        "h3 a",
        ".search-result a",
        "[data-product-url]",
    ]
    
    product_url = None
    for selector in product_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements[:5]:  # Check first 5 matches
                try:
                    href = element.get_attribute("href")
                    if href and ("/products/" in href or "/product/" in href):
                        # Make absolute URL if relative
                        if href.startswith("/"):
                            href = urljoin("https://www.ammancart.com", href)
                        elif not href.startswith("http"):
                            href = urljoin("https://www.ammancart.com", href)
                        
                        if href.startswith("http") and "ammancart.com" in href:
                            product_url = href
                            print(f"   → Found product URL: {product_url}")
                            
                            # Navigate to product page
                            driver.get(product_url)
                            time.sleep(pause)
                            return product_url
                except Exception:
                    continue
        except Exception:
            continue
    
    # Alternative: try to find product link by clicking on product card/image
    if not product_url:
        try:
            # Look for clickable product elements
            product_elements = driver.find_elements(
                By.CSS_SELECTOR, 
                ".product-item, .product-card, [class*='product']"
            )
            
            if product_elements:
                # Try clicking the first product element
                first_product = product_elements[0]
                first_product.click()
                time.sleep(pause)
                
                # Get current URL after click
                product_url = driver.current_url
                if "ammancart.com" in product_url and ("/products/" in product_url or "/product/" in product_url):
                    print(f"   → Navigated to product URL: {product_url}")
                    return product_url
        except Exception as e:
            print(f"   → Error clicking product element: {e}")
    
    if not product_url:
        print(f"   → No product found for SKU: {sku}")
    
    return product_url


def extract_product_title(driver) -> str:
    """Extract product title from page."""
    title_selectors = [
        "h1.product-title",
        "h1.product-name",
        "h1",
        ".product-title",
        ".product-name",
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


def extract_product_description(driver) -> str:
    """Extract product description from page."""
    # Try Selenium first
    description_selectors = [
        ".product-description",
        ".product-description-full",
        ".product-details",
        ".product-info",
        ".product-content",
        ".description",
        ".product-description-text",
        "[itemprop='description']",
        ".product-summary",
        ".product-specifications",
        ".specifications",
        "#description",
        ".product__description",
    ]
    
    for selector in description_selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            desc = element.text.strip()
            if desc and len(desc) > 10:
                print(f"   → Description: {len(desc)} characters")
                return desc
        except Exception:
            continue
    
    # Try BeautifulSoup for more flexible parsing
    try:
        html = driver.page_source
        soup = BeautifulSoup(html, "lxml")
        
        for selector in description_selectors:
            element = soup.select_one(selector)
            if element:
                desc = element.get_text(" ", strip=True)
                if desc and len(desc) > 10:
                    print(f"   → Description: {len(desc)} characters")
                    return desc
    except Exception:
        pass
    
    print("   → No description found")
    return ""


def extract_price(driver) -> str:
    """
    Extract all prices from price__container element.
    Returns combined price string with all prices found.
    """
    prices = []
    
    try:
        # Try BeautifulSoup first
        html = driver.page_source
        soup = BeautifulSoup(html, "lxml")
        
        # Find the price__container element
        price_container = soup.select_one(".price__container")
        if price_container:
            # Find all price-item elements within the container
            price_elements = price_container.find_all(class_=lambda x: x and 'price-item' in str(x))
            
            for elem in price_elements:
                price_text = elem.get_text(" ", strip=True)
                if price_text:
                    prices.append(price_text)
            
            if prices:
                combined_price = " | ".join(prices)
                print(f"   → Price found: {combined_price}")
                return combined_price
        
    except Exception as e:
        print(f"   → Error extracting prices with BeautifulSoup: {e}")
    
    # Fallback to Selenium
    try:
        # Find price__container element
        container = driver.find_element(By.CSS_SELECTOR, ".price__container")
        
        # Find all price-item elements within the container
        price_elements = container.find_elements(By.CSS_SELECTOR, "[class*='price-item']")
        
        for elem in price_elements:
            price_text = elem.text.strip()
            if price_text:
                prices.append(price_text)
        
        if prices:
            combined_price = " | ".join(prices)
            print(f"   → Price found (Selenium): {combined_price}")
            return combined_price
        
    except Exception as e:
        print(f"   → Error extracting prices with Selenium: {e}")
    
    print("   → No price found")
    return ""


def clean_image_url(url: str) -> str:
    """Remove size parameters from URL to get base URL for deduplication."""
    parsed = urlparse(url)
    path = parsed.path
    
    # Extract filename (last part after /)
    filename_match = re.search(r'/([^/]+\.(jpg|jpeg|png|gif|webp))$', path, re.I)
    if filename_match:
        # Use filename for comparison (same image may have different paths but same filename)
        filename = filename_match.group(1).lower()
        return f"{parsed.netloc}:{filename}"
    
    # Remove common size parameters from query string
    query = parsed.query
    query = re.sub(r'[?&](w|h|width|height|size|resize|fit|format|auto|quality|q|scale|s)=[^&]*', '', query)
    query = re.sub(r'[?&]+$', '', query)
    
    # Remove common size indicators in path
    path = re.sub(r'/(\d+)x(\d+)/', '/', path)
    path = re.sub(r'/(thumb|small|medium|large|big|full|original|zoom)[_-]', '/', path, flags=re.I)
    
    # Return domain + cleaned path for comparison
    return f"{parsed.netloc}:{path}"


def get_image_size_score(url: str) -> int:
    """Score image URL based on size indicators. Higher score = larger image."""
    url_lower = url.lower()
    score = 0
    
    # Check for size parameters in query string
    size_match = re.search(r'[?&](?:w|width)=(\d+)', url_lower)
    if size_match:
        score = int(size_match.group(1))
    
    # Check for height as well
    height_match = re.search(r'[?&](?:h|height)=(\d+)', url_lower)
    if height_match:
        h = int(height_match.group(1))
        if score > 0:
            score = score * h  # Use area if both width and height
        else:
            score = h
    
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
    
    # If still no score, default based on path structure (assume bigger if no size indicators)
    if score == 0:
        # Prefer URLs without size indicators (likely full size)
        if not any(x in url_lower for x in ['thumb', 'small', 'mini', '100x', '200x', '300x']):
            score = 500
    
    return score


def extract_product_images(driver, base_url: str = "https://www.ammancart.com") -> List[str]:
    """Extract product images from page, prioritizing largest size and removing duplicates."""
    all_image_urls = []
    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")
    
    # Scroll to trigger lazy loading
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
    time.sleep(1)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)
    
    # FIRST: Try to find MediaGallery container (specific to AmmanCart)
    media_gallery_selectors = [
        "#MediaGallery-template--21614832943407__main img",
        "[id^='MediaGallery-template'] img",
        "[id*='MediaGallery'] img",
    ]
    
    # Try Selenium first for MediaGallery (might be dynamic)
    try:
        for selector in media_gallery_selectors:
            try:
                gallery_container = driver.find_element(By.CSS_SELECTOR, selector.split(" img")[0])
                imgs = gallery_container.find_elements(By.TAG_NAME, "img")
                
                for img_elem in imgs:
                    src = (img_elem.get_attribute("src") or 
                           img_elem.get_attribute("data-src") or 
                           img_elem.get_attribute("data-lazy-src") or
                           img_elem.get_attribute("data-zoom-image") or
                           img_elem.get_attribute("data-original") or
                           img_elem.get_attribute("data-image"))
                    
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
                            print(f"   → Found MediaGallery image: {src[:80]}...")
                
                if all_image_urls:
                    print(f"   → Found {len(all_image_urls)} images in MediaGallery")
                    # Group by base URL and keep largest version
                    base_url_to_images = {}
                    for url in all_image_urls:
                        base = clean_image_url(url)
                        score = get_image_size_score(url)
                        if base not in base_url_to_images:
                            base_url_to_images[base] = []
                        base_url_to_images[base].append((url, score))
                    
                    # For each base URL, keep only the largest version
                    final_images = []
                    for base, variants in base_url_to_images.items():
                        variants.sort(key=lambda x: x[1], reverse=True)
                        best_url = variants[0][0]
                        final_images.append((best_url, variants[0][1]))
                    
                    # Sort final images by score (largest first)
                    final_images.sort(key=lambda x: x[1], reverse=True)
                    images = [url for url, score in final_images]
                    
                    print(f"   → Returning {len(images)} unique images from MediaGallery (deduplicated from {len(all_image_urls)} candidates)")
                    return images
            except Exception as e:
                continue
    except Exception as e:
        print(f"   → Error extracting from MediaGallery: {e}")
    
    # Also try BeautifulSoup for MediaGallery
    for selector in media_gallery_selectors:
        try:
            gallery_element = soup.select_one(selector.split(" img")[0])
            if gallery_element:
                imgs = gallery_element.find_all("img")
                for img in imgs:
                    src = (img.get("src") or 
                           img.get("data-src") or 
                           img.get("data-lazy-src") or
                           img.get("data-zoom-image") or
                           img.get("data-original") or
                           img.get("data-image"))
                    if src:
                        if src.startswith("//"):
                            src = "https:" + src
                        elif src.startswith("/"):
                            src = urljoin(base_url, src)
                        elif not src.startswith("http"):
                            src = urljoin(base_url, src)
                        
                        low_src = src.lower()
                        skip_terms = ["logo", "icon", "favicon", "banner", "placeholder", "sprite", "data:image", "base64"]
                        if any(term in low_src for term in skip_terms):
                            continue
                        
                        if src.startswith("http"):
                            all_image_urls.append(src)
                            print(f"   → Found MediaGallery image (BS): {src[:80]}...")
                
                if all_image_urls:
                    # Group by base URL and keep largest version
                    base_url_to_images = {}
                    for url in all_image_urls:
                        base = clean_image_url(url)
                        score = get_image_size_score(url)
                        if base not in base_url_to_images:
                            base_url_to_images[base] = []
                        base_url_to_images[base].append((url, score))
                    
                    # For each base URL, keep only the largest version
                    final_images = []
                    for base, variants in base_url_to_images.items():
                        variants.sort(key=lambda x: x[1], reverse=True)
                        best_url = variants[0][0]
                        final_images.append((best_url, variants[0][1]))
                    
                    # Sort final images by score (largest first)
                    final_images.sort(key=lambda x: x[1], reverse=True)
                    images = [url for url, score in final_images]
                    
                    print(f"   → Found {len(images)} unique images from MediaGallery (deduplicated from {len(all_image_urls)} candidates)")
                    return images
        except Exception:
            continue
    
    # Fallback: Generic image selectors for product pages
    image_selectors = [
        ".product-images img",
        ".product-gallery img",
        ".product-photos img",
        ".product-media img",
        ".product-single__photos img",
        ".product-image img",
        "img[itemprop='image']",
        ".main-product-image img",
        ".product-thumbnails img",
        ".gallery img",
        "[class*='product-image'] img",
        "[class*='product-gallery'] img",
    ]
    
    # Try CSS selectors with BeautifulSoup
    for selector in image_selectors:
        try:
            imgs = soup.select(selector)
            for img in imgs:
                src = (img.get("src") or 
                       img.get("data-src") or 
                       img.get("data-lazy-src") or
                       img.get("data-zoom-image") or
                       img.get("data-original"))
                
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
                
                if src.startswith("http"):
                    all_image_urls.append(src)
        except Exception:
            continue
    
    # Also try Selenium approach
    for selector in image_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for elem in elements:
                src = (elem.get_attribute("src") or 
                       elem.get_attribute("data-src") or 
                       elem.get_attribute("data-lazy-src") or
                       elem.get_attribute("data-zoom-image") or
                       elem.get_attribute("data-original"))
                
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
                
                if src.startswith("http"):
                    all_image_urls.append(src)
        except Exception:
            continue
    
    # Group by base URL and keep largest version
    if all_image_urls:
        base_url_to_images = {}
        for url in all_image_urls:
            base = clean_image_url(url)
            score = get_image_size_score(url)
            if base not in base_url_to_images:
                base_url_to_images[base] = []
            base_url_to_images[base].append((url, score))
        
        # For each base URL, keep only the largest version
        final_images = []
        for base, variants in base_url_to_images.items():
            variants.sort(key=lambda x: x[1], reverse=True)
            best_url = variants[0][0]
            final_images.append((best_url, variants[0][1]))
        
        # Sort final images by score (largest first)
        final_images.sort(key=lambda x: x[1], reverse=True)
        images = [url for url, score in final_images]
        
        print(f"   → Found {len(images)} unique product images (deduplicated from {len(all_image_urls)} candidates)")
        return images
    
    print(f"   → No product images found")
    return []


def scrape_sku(
    driver, wait: WebDriverWait, sku: str, pause: float = 2.0
) -> Dict[str, Any]:
    """
    Complete scraping workflow for one SKU:
    1. Search AmmanCart
    2. Click first result
    3. Scrape title, description, images
    """
    result = {
        "SKU": sku,
        "Product URL": "",
        "Title": "",
        "Description": "",
        "Price": "",
        "Images": "",
        "Image Count": 0,
        "Status": "FAILED",
        "Note": "",
    }
    
    try:
        # Step 1: Search and click first result
        product_url = search_and_click_first_result(driver, wait, sku, pause)
        
        if not product_url:
            result["Note"] = "No search results found"
            return result
        
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
        
        # Step 3: Extract description
        description = extract_product_description(driver)
        result["Description"] = description
        
        # Step 4: Extract price (contains both old and new prices)
        price = extract_price(driver)
        result["Price"] = price
        
        # Step 6: Extract images
        images = extract_product_images(driver, product_url)
        result["Images"] = " | ".join(images)
        result["Image Count"] = len(images)
        
        # Determine status
        if title or description or images:
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
        description="AmmanCart scraper - searches and scrapes product data"
    )
    parser.add_argument(
        "--input",
        default="bci.xlsx",
        help="Input Excel file (default: bci.xlsx)",
    )
    parser.add_argument(
        "--output",
        default="ammancart_scraped_results.xlsx",
        help="Output Excel file (default: ammancart_scraped_results.xlsx)",
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
    parser.add_argument(
        "--sku-col",
        type=str,
        default="sku",
        help="Column name containing SKUs (default: sku, auto-detects if not found)",
    )
    
    args = parser.parse_args()
    
    # Read Excel file
    input_path = os.path.join(os.path.dirname(__file__), args.input)
    if not os.path.exists(input_path):
        print(f"Error: Input file not found: {input_path}")
        return
    
    print(f" Reading Excel file: {input_path}")
    df = pd.read_excel(input_path)
    
    # Try to find SKU column
    sku_col = args.sku_col
    if sku_col not in df.columns:
        # Try common variations
        possible_cols = [col for col in df.columns if "sku" in str(col).lower()]
        if possible_cols:
            sku_col = possible_cols[0]
            print(f" Using column '{sku_col}' for SKUs")
        else:
            print("Error: SKU column not found in Excel file")
            print(f"Available columns: {list(df.columns)}")
            print(f"Please specify column name with --sku-col")
            return
    
    skus = df[sku_col].fillna("").astype(str).tolist()
    skus = [s.strip() for s in skus if s.strip()]

    # Apply limit if specified
    if args.limit:
        skus = skus[:args.limit]
        print(f" TEST MODE: Limited to {args.limit} products")

    print(f" Found {len(skus)} SKU entries")
    print(f" Starting from row {args.start_row}")

    # Resume logic: load existing output and skip already-done SKUs
    output_path = os.path.join(os.path.dirname(__file__), args.output)
    existing_df = None
    already_done_skus = set()
    if os.path.exists(output_path):
        try:
            existing_df = pd.read_excel(output_path)
            if "SKU" in existing_df.columns and "Status" in existing_df.columns:
                already_done_skus = set(
                    existing_df.loc[existing_df["Status"] != "FAILED", "SKU"].astype(str).str.strip()
                )
            print(f" Resuming: {len(already_done_skus)} SKUs already done, will skip them")
        except Exception as e:
            print(f" Warning: could not read existing output ({e}), starting fresh")
            existing_df = None

    # Setup driver
    print(" Starting browser...")
    driver = build_driver(headful=args.headful)
    wait = WebDriverWait(driver, 20)
    
    results = []
    
    try:
        # Slice skus based on start_row and limit
        start_idx = args.start_row
        end_idx = start_idx + args.limit if args.limit else len(skus)
        skus_to_process = skus[start_idx:end_idx]
        total_to_process = len(skus_to_process)
        
        for idx, sku in enumerate(skus_to_process, start=start_idx):
            if sku in already_done_skus:
                print(f"\n[{idx + 1 - start_idx}/{total_to_process}] Skipping already-done SKU: {sku}")
                continue
            print(f"\n[{idx + 1 - start_idx}/{total_to_process}] Processing SKU: {sku}")

            result = scrape_sku(driver, wait, sku, args.pause)
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
        print(f"\n Saving results to: {output_path}")
        new_output_df = pd.DataFrame(results)
        if existing_df is not None and len(new_output_df) > 0:
            final_df = pd.concat([existing_df, new_output_df], ignore_index=True)
        elif existing_df is not None:
            final_df = existing_df
        else:
            final_df = new_output_df
        final_df.to_excel(output_path, index=False)
        
        # Print summary
        success = sum(1 for r in results if r["Status"] == "SUCCESS")
        partial = sum(1 for r in results if r["Status"] == "PARTIAL")
        failed = sum(1 for r in results if r["Status"] in ["FAILED", "ERROR"])
        
        print(f"\n Summary:")
        print(f"    Success: {success}")
        print(f"     Partial: {partial}")
        print(f"    Failed: {failed}")
        print(f"    Results saved to: {output_path}")
        
    finally:
        driver.quit()
        print("\n Browser closed")


if __name__ == "__main__":
    main()

