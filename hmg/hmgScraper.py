#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
hmgScraper_no_click.py
- Searches hmg.jo for each SKU
- DOES NOT click into results
- Extracts description from electro-description clearfix class
- Extracts image(s) from nswiper-wrapper class and other selectors
- Appends results to the SAME Excel file in columns:
    - "HMG Description" (from electro-description clearfix)
    - "HMG Images"      (pipe-separated URLs from nswiper-wrapper)

Examples:
  python hmgScraper.py --in "Data\\products.xlsx" --sheet "Sheet1" --sku-col "SKU"
  python hmgScraper.py --in "Data\\items.xls" --sku-col "Item" --headful
  python hmgScraper.py --sample 3 --in "Data\\products.xlsx" --sku-col "SKU"
"""

import argparse, sys, time, re, os, shutil
from urllib.parse import quote_plus
from typing import List, Optional, Tuple
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

# Fix Unicode output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ---------- helpers ----------

def start_driver(headful: bool = False) -> webdriver.Chrome:
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,2000")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=ar,en-US;q=0.9")
    return webdriver.Chrome(options=opts)

def read_excel(path: str, sheet: Optional[str]) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet if sheet else 0)
    except Exception as e:
        print(f"ERROR reading Excel: {e}", file=sys.stderr); sys.exit(2)

def ensure_cols(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df

def search_url_for_sku(sku: str) -> str:
    return f"https://hmg.jo/?s={quote_plus(str(sku))}&post_type=product"

def safe_get_attr(el, *attrs) -> Optional[str]:
    for a in attrs:
        try:
            v = el.get_attribute(a)
            if v and v.strip():
                return v.strip()
        except Exception:
            pass
    return None

def choose_largest_from_srcset(srcset: str) -> Optional[str]:
    """
    Choose the largest image from srcset, prioritizing high-resolution images.
    """
    try:
        best, best_w = None, -1
        for p in [x.strip() for x in srcset.split(",") if x.strip()]:
            m = re.match(r"(.*?)\s+(\d+)w", p)
            if m:
                url, w = m.group(1).strip(), int(m.group(2))
                # Prioritize larger images, but also consider very large ones
                if w > best_w:
                    best, best_w = url, w
            else:
                # If no width specified, use as fallback
                if best is None:
                    best = p.split()[0]
        
        # If we found a very small image, try to get a larger one
        if best_w < 300 and best:
            # Look for any image with higher resolution
            for p in [x.strip() for x in srcset.split(",") if x.strip()]:
                m = re.match(r"(.*?)\s+(\d+)w", p)
                if m:
                    url, w = m.group(1).strip(), int(m.group(2))
                    if w > best_w:
                        best, best_w = url, w
        
        return best
    except Exception:
        return None

def dedup_preserve_order(items: List[str]) -> List[str]:
    seen = set(); out = []
    for x in items:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

def prioritize_high_res_images(imgs: List[str]) -> List[str]:
    """
    Prioritize high-resolution images and remove low-quality duplicates.
    """
    if not imgs:
        return imgs
    
    # Group images by base URL (without size parameters)
    base_groups = {}
    for img in imgs:
        # Remove common size parameters to group similar images
        base_url = re.sub(r'[?&](w|h|width|height|size)=\d+', '', img)
        base_url = re.sub(r'-\d+x\d+', '', base_url)  # Remove -300x300 type suffixes
        if base_url not in base_groups:
            base_groups[base_url] = []
        base_groups[base_url].append(img)
    
    # For each group, choose the highest quality image
    prioritized = []
    for base_url, group in base_groups.items():
        if len(group) == 1:
            prioritized.append(group[0])
        else:
            # Choose the image with the highest resolution indicators
            best_img = group[0]
            best_score = 0
            
            for img in group:
                score = 0
                # Score based on URL patterns that indicate high resolution
                if 'large' in img.lower(): score += 3
                if 'full' in img.lower(): score += 3
                if 'original' in img.lower(): score += 3
                if 'zoom' in img.lower(): score += 2
                if 'high' in img.lower(): score += 2
                
                # Score based on size parameters in URL
                size_matches = re.findall(r'(\d+)x(\d+)', img)
                if size_matches:
                    for w, h in size_matches:
                        score += int(w) * int(h) // 10000  # Normalize to reasonable score
                
                # Score based on width parameters
                width_matches = re.findall(r'[?&](?:w|width)=(\d+)', img)
                if width_matches:
                    score += max(int(w) for w in width_matches) // 100
                
                if score > best_score:
                    best_score = score
                    best_img = img
            
            prioritized.append(best_img)
    
    return prioritized

# ---------- search-only extraction ----------

def get_first_result_tile(driver, timeout: int = 10, sku: Optional[str] = None):
    """
    Returns the first product tile from the search results.
    Prefers a tile whose link/text contains the SKU.
    """
    product_list_selectors = [
        "ul.products li.product",
        "div.products .product",
        ".archive .products .product",
        ".products .product",  # More general selector
        ".product",  # Even more general
    ]
    tiles = []
    
    print(f"   → Looking for product tiles with selectors: {product_list_selectors}")
    
    for css in product_list_selectors:
        try:
            print(f"   → Trying selector: {css}")
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, css))
            )
            found_tiles = driver.find_elements(By.CSS_SELECTOR, css)
            tiles.extend(found_tiles)
            print(f"   → Found {len(found_tiles)} tiles with selector: {css}")
        except TimeoutException:
            print(f"   → No tiles found with selector: {css}")
            continue

    print(f"   → Total tiles found: {len(tiles)}")
    
    if not tiles:
        # Let's also try to see what's actually on the page
        try:
            page_source = driver.page_source
            print(f"   → Page title: {driver.title}")
            print(f"   → Page source length: {len(page_source)}")
            # Look for any product-related elements
            all_products = driver.find_elements(By.CSS_SELECTOR, "*[class*='product']")
            print(f"   → Elements with 'product' in class: {len(all_products)}")
        except Exception as e:
            print(f"   → Error checking page content: {e}")
        return None

    if sku:
        sku_l = str(sku).lower()
        def tile_score(tile):
            try:
                try:
                    a = tile.find_element(By.CSS_SELECTOR, "a.woocommerce-LoopProduct-link")
                except NoSuchElementException:
                    a = tile.find_element(By.CSS_SELECTOR, "a")
                href = (safe_get_attr(a, "href") or "").lower()
                txt = ((a.text or "") + " " + (tile.text or "")).lower()
                return 1 if (sku_l in href or sku_l in txt) else 0
            except Exception:
                return 0
        tiles = sorted(tiles, key=tile_score, reverse=True)

    return tiles[0]

def extract_images_from_tile(tile) -> List[str]:
    """
    Extract image URLs from nswiper-wrapper class and other selectors.
    Prioritizes high-resolution images and removes duplicates.
    """
    img_selectors = [
        ".nswiper-wrapper img",  # Primary selector for swiper images
        "a.woocommerce-LoopProduct-link img",
        "a img",
        "img.wp-post-image",
        "img",
    ]
    imgs = []
    seen_urls = set()  # Track seen URLs to avoid duplicates
    
    print(f"   → Looking for images with selectors: {img_selectors}")
    
    for css in img_selectors:
        found_imgs = tile.find_elements(By.CSS_SELECTOR, css)
        print(f"   → Found {len(found_imgs)} images with selector: {css}")
        
        for el in found_imgs:
            # Priority 1: data-large_image or data-zoom-image (highest quality)
            big = safe_get_attr(el, "data-large_image", "data-zoom-image")
            if big:
                if big.startswith("//"): big = "https:" + big
                if big not in seen_urls:
                    imgs.append(big)
                    seen_urls.add(big)
                    print(f"   → Added high-res image: {big}")
                continue

            # Priority 2: srcset with largest size
            srcset = safe_get_attr(el, "srcset")
            if srcset:
                best = choose_largest_from_srcset(srcset)
                if best:
                    if best.startswith("//"): best = "https:" + best
                    if best not in seen_urls:
                        imgs.append(best)
                        seen_urls.add(best)
                        print(f"   → Added srcset image: {best}")
                continue

            # Priority 3: regular src attributes
            src = safe_get_attr(el, "src", "data-src", "data-lazy")
            if src:
                if src.startswith("//"): src = "https:" + src
                if src not in seen_urls:
                    imgs.append(src)
                    seen_urls.add(src)
                    print(f"   → Added src image: {src}")

    # Filter for valid image formats and remove any remaining duplicates
    imgs = [u for u in imgs if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", u, re.I)]
    imgs = dedup_preserve_order(imgs)
    
    # Prioritize high-resolution images and remove low-quality duplicates
    imgs = prioritize_high_res_images(imgs)
    
    print(f"   → Final prioritized images: {len(imgs)}")
    return imgs

def extract_description_from_tile(tile) -> str:
    """
    Extract description from electro-description clearfix class and other selectors.
    """
    desc_selectors = [
        ".electro-description.clearfix",  # Primary selector for description
        ".electro-description",
        ".product-description",
        ".description",
        ".woocommerce-product-details__short-description",
        "p",
    ]
    
    print(f"   → Looking for descriptions with selectors: {desc_selectors}")
    
    for css in desc_selectors:
        try:
            desc_el = tile.find_element(By.CSS_SELECTOR, css)
            desc_text = desc_el.text.strip()
            print(f"   → Found description with selector {css}: {desc_text[:100]}...")
            if desc_text:
                return desc_text
        except NoSuchElementException:
            print(f"   → No description found with selector: {css}")
            continue
    
    print(f"   → No description found with any selector")
    return ""

def scrape_for_sku(driver, sku: str, pause: float, timeout: int) -> Tuple[str, str]:
    """
    Returns (plain_description, images_joined)
    - DOES NOT navigate into product pages.
    - Extracts description from electro-description clearfix class
    - Extracts image(s) from nswiper-wrapper class and other selectors
    """
    search_url = search_url_for_sku(sku)
    print(f"   → Navigating to: {search_url}")
    
    driver.get(search_url)
    time.sleep(pause)
    
    # Check if we're actually on the search page
    current_url = driver.current_url
    print(f"   → Current URL: {current_url}")
    
    # Wait a bit more for the page to fully load
    time.sleep(2)

    tile = get_first_result_tile(driver, timeout=timeout, sku=sku)
    if not tile:
        print(f"   → No product tile found for SKU: {sku}")
        return "", ""

    print(f"   → Found product tile, extracting data...")
    desc = extract_description_from_tile(tile)
    imgs = extract_images_from_tile(tile)
    
    print(f"   → Description length: {len(desc)}")
    print(f"   → Images found: {len(imgs)}")
    
    return desc, " | ".join(imgs)

# ---------- main ----------

def main():

    ap = argparse.ArgumentParser(
        description="Search-only image scraper for hmg.jo: extracts product images from the first search result tile (no clicks).",
        formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("--in", dest="input", required=True, help="Original Excel file (.xlsx/.xls).")
    ap.add_argument("--sheet", help="Sheet name (defaults to first sheet).")
    ap.add_argument("--sku-col", dest="sku_col", required=True, help="Column name containing SKUs.")
    ap.add_argument("--pause", type=float, default=0.8, help="Pause between steps. Default: 0.8")
    ap.add_argument("--timeout", type=int, default=12, help="Wait timeout seconds. Default: 12")
    ap.add_argument("--headful", action="store_true", help="Run with visible Chrome.")
    ap.add_argument("--no-backup", action="store_true", help="Do NOT create a .bak.xlsx backup (not recommended).")
    ap.add_argument("--sample", type=int, help='Process only the first N products and save to a new file (e.g., --sample 3)')

    args, _unknown = ap.parse_known_args()

    # 1) Validate input path
    if not os.path.exists(args.input):
        print(f"ERROR: file not found: {args.input}", file=sys.stderr); sys.exit(2)

    ext = os.path.splitext(args.input)[1].lower()
    if ext not in (".xlsx", ".xls"):
        print("ERROR: Only Excel files are supported for in-place append (.xlsx/.xls).", file=sys.stderr); sys.exit(2)

    # 2) Load Excel
    df = read_excel(args.input, args.sheet)

    if args.sku_col not in df.columns:
        print(f"ERROR: SKU column '{args.sku_col}' not found. Available: {list(df.columns)}", file=sys.stderr)
        sys.exit(2)

    # 3) Apply sample limit if specified
    if args.sample:
        # Filter to only rows with valid SKUs first
        valid_skus = df[df[args.sku_col].notna() & (df[args.sku_col].astype(str).str.strip() != '') & (df[args.sku_col].astype(str).str.lower() != 'nan')]
        if len(valid_skus) == 0:
            print("ERROR: No valid SKUs found in the file", file=sys.stderr)
            sys.exit(2)
        
        # Take only the first N valid SKUs
        sample_indices = valid_skus.head(args.sample).index
        df = df.loc[sample_indices].copy()
        print(f"✓ Processing sample of {len(df)} products (requested: {args.sample})")

    # 4) Prepare output columns
    desc_col = "HMG Description"
    imgs_col = "HMG Images"
    df = ensure_cols(df, [desc_col, imgs_col])

    # 5) Start browser
    try:
        driver = start_driver(headful=args.headful)
    except WebDriverException as e:
        print(f"Failed to start Chrome: {e}", file=sys.stderr); sys.exit(1)

    # 6) Iterate rows and append values
    try:
        for idx, row in df.iterrows():
            sku = str(row[args.sku_col]).strip()
            if not sku or sku.lower() == "nan":
                continue

            print(f"→ {sku}")
            try:
                desc, imgs = scrape_for_sku(driver, sku, pause=args.pause, timeout=args.timeout)
            except Exception as e:
                desc, imgs = "", ""
                print(f"   ! error for {sku}: {type(e).__name__}: {e}", file=sys.stderr)

            df.at[idx, desc_col] = desc
            df.at[idx, imgs_col] = imgs
    finally:
        driver.quit()

    # 7) Determine output file and save
    if args.sample:
        # For sample mode, create a new file in the same directory
        base, extn = os.path.splitext(args.input)
        output_file = f"{base}_sample_{args.sample}{extn}"
    else:
        # For normal mode, use the same file
        output_file = args.input
        # Create backup only for normal mode
        if not args.no_backup:
            base, extn = os.path.splitext(args.input)
            backup = f"{base}.bak.xlsx"
            try:
                shutil.copyfile(args.input, backup)
                print(f"✓ Backup saved: {backup}")
            except Exception as e:
                print(f"! Backup failed ({e}). Proceeding to overwrite...", file=sys.stderr)

    with pd.ExcelWriter(output_file, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, index=False)
    
    if args.sample:
        print(f"✓ Sample file created: {output_file}")
    else:
        print(f"✓ Updated: {output_file}")
    print(f"Added/updated columns: '{desc_col}', '{imgs_col}'")

if __name__ == "__main__":
    main()
