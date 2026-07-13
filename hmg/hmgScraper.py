#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
hmgScraper_no_click.py
- Searches hmg.jo for each SKU
- Locates the first matching product tile in the search results
- Opens the actual PRODUCT PAGE (this is the key change) so that we can
  reach the full-resolution images and full description, which are NOT
  available on the search results page (search tiles only expose 100x100
  thumbnails).
- Extracts description from electro-description clearfix class
- Extracts the ORIGINAL / full-resolution image(s), never thumbnails
- Appends results to the SAME Excel file in columns:
    - "HMG Description" (from electro-description clearfix)
    - "HMG Images"      (pipe-separated URLs, full resolution)

Flow:
    Search SKU
      -> Locate first matching product tile
      -> Extract product URL from the tile
      -> driver.get(product_url)
      -> Extract original image(s) from the product page
      -> Extract description from the product page
      -> Return results

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

def is_thumbnail_url(url: str, el=None) -> bool:
    """
    Detect common WordPress/WooCommerce THUMBNAIL suffixes so we NEVER
    save a thumbnail as if it were the original image.

    Important: WordPress also generates large, perfectly valid image
    sizes with a WxH suffix (e.g. -768x1152.jpg, -1024x1536.jpg,
    -1366x2048.jpg). Those are NOT thumbnails and must be kept.
    Only reject the suffix if BOTH dimensions are small (<= 300px),
    which matches genuine thumbnail sizes like -100x100, -150x150,
    -300x300, -300x450.

    Additional safety net: some themes generate non-square thumbnail
    sizes (e.g. -300x450) where only ONE dimension is <=300px, which
    would slip past the pure width/height check above. If we still
    have a handle on the source <img> element, also reject it when its
    class attribute openly identifies it as a WooCommerce thumbnail
    attachment (e.g. "attachment-woocommerce_thumbnail",
    "attachment-shop_thumbnail", "size-woocommerce_thumbnail").
    """
    if not url:
        return True
    m = re.search(r"-(\d{2,4})x(\d{2,4})\.(jpg|jpeg|png|webp)(\?|$)", url, re.I)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if w <= 300 and h <= 300:
            return True
    if el is not None:
        cls = (safe_get_attr(el, "class") or "").lower()
        if any(tag in cls for tag in (
            "attachment-woocommerce_thumbnail",
            "attachment-shop_thumbnail",
            "size-woocommerce_thumbnail",
            "size-shop_thumbnail",
        )):
            return True
    return False

def choose_largest_from_srcset(srcset: str) -> Optional[str]:
    """
    Parse a srcset attribute and return the URL with the largest declared
    width (the 'w' descriptor), which corresponds to the highest
    resolution version offered on the page.
    """
    try:
        best, best_w = None, -1
        for p in [x.strip() for x in srcset.split(",") if x.strip()]:
            m = re.match(r"(.*?)\s+(\d+)w", p)
            if m:
                url, w = m.group(1).strip(), int(m.group(2))
                if w > best_w:
                    best, best_w = url, w
            else:
                if best is None:
                    best = p.split()[0]
        return best
    except Exception:
        return None

def dedup_preserve_order(items: List[str]) -> List[str]:
    seen = set(); out = []
    for x in items:
        if x not in seen:
            out.append(x); seen.add(x)
    return out


# ---------- step 1: search results page (find the product URL only) ----------

def get_first_result_tile(driver, timeout: int = 10, sku: Optional[str] = None):
    """
    Returns the first product tile from the search results.
    Prefers a tile whose link/text contains the SKU.
    NOTE: we only use this tile to find the product URL - we never pull
    images or description from it, since search tiles only expose
    low-resolution thumbnails.
    """
    product_list_selectors = [
        "ul.products li.product",
        "div.products .product",
        ".archive .products .product",
        ".products .product",
        ".product",
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
        try:
            page_source = driver.page_source
            print(f"   → Page title: {driver.title}")
            print(f"   → Page source length: {len(page_source)}")
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

def extract_product_url_from_tile(tile) -> Optional[str]:
    """
    Pull the product page URL out of a search-result tile.
    We deliberately do NOT extract images/description here anymore -
    the tile is only used as a pointer to the real product page.
    """
    link_selectors = [
        "a.woocommerce-LoopProduct-link",
        "a.woocommerce-loop-product__link",
        "a",
    ]
    for css in link_selectors:
        try:
            a = tile.find_element(By.CSS_SELECTOR, css)
            href = safe_get_attr(a, "href")
            if href:
                return href
        except NoSuchElementException:
            continue
    return None


# ---------- step 2: product page (real extraction happens here) ----------

def extract_images_from_product_page(driver) -> List[str]:
    """
    Extract ORIGINAL / full-resolution image URLs from a product page.

    STRICTLY scoped to the CURRENT product's own gallery containers. Never
    reaches into Related Products / Upsells / Cross-sells / product sliders
    / widgets, even if the page layout changes in the future.

    Why the previous version could leak images from Related Products:
      - The old gallery selector list included ".product .images". ".product"
        matches every `li.product` tile inside the Related/Upsell/Cross-sell
        loops too, not just the current product's wrapper. If any of those
        loop tiles (or a theme wrapper around them) also carries an
        "images" class, `find_element()` - which only returns the FIRST
        DOM match - could resolve to a related-product container instead
        of the real single-product gallery.
      - Once the wrong container was captured, the generic "img" fallback
        selector then happily pulled images from wherever that container
        actually was.
      - Additionally, the thumbnail filter only rejected size suffixes
        where BOTH dimensions were <=300px. Some WooCommerce thumbnail
        sizes are non-square (e.g. "-300x450"), which slipped through.

    Why ".woocommerce-product-gallery" alone was still incomplete:
      - This theme (Electro + a bolted-on "nickx" swiper) renders the same
        product photos in more than one gallery. ".woocommerce-product-gallery"
        sometimes only carries the FIRST image; any additional images (the
        ones visible in the thumbnail nav strip, class "thumbnail-slider
        ... nickx-slider-nav") only exist inside the paired main slider,
        ".nickx-slider-for". That container is specific to the single
        product's own image slider - it is never reused by Related Products
        (which renders via a separate "wt-related-products"/owl-carousel
        plugin with no "nickx"/"nswiper" classes at all) - so scanning it
        carries the same no-leak guarantee as ".woocommerce-product-gallery".

    Fix:
      - Scan BOTH ".woocommerce-product-gallery" and ".nickx-slider-for".
      - Only search for images with selectors that specifically target
        gallery images (no page-wide bare "img" fallback).
      - Add a class-based safety net to the thumbnail filter to catch
        non-square WooCommerce thumbnail sizes.

    Priority order (per image element found), unchanged from before:
      1. data-zoom-image   (WooCommerce zoom - always full resolution)
      2. data-large_image  (alternate full-resolution attribute)
      3. largest entry inside srcset
      4. src / data-src / data-lazy  (only kept if it does NOT look like a thumbnail)
    """
    # Locate the CURRENT product's gallery containers only. Both selectors
    # below are emitted once per single-product page by this site's own
    # templates and are never reused by Related Products / Upsell /
    # Cross-sell loops, so there is no ambiguity about which containers
    # these are.
    gallery_selectors = [".woocommerce-product-gallery", ".nickx-slider-for"]
    galleries = []
    try:
        for css in gallery_selectors:
            found = driver.find_elements(By.CSS_SELECTOR, css)
            if found:
                print(f"   → [product page] Found gallery container: {css} "
                      f"({len(found)} match(es) on page)")
                galleries.extend(found)
    except Exception as e:
        print(f"   → [product page] Error locating gallery container: {e}")

    if not galleries:
        print("   → [product page] WARNING: no gallery container found, skipping image extraction")
        return []

    imgs: List[str] = []
    seen_urls = set()
    seen_elements = set()

    for gallery in galleries:
        # The "nickx" swiper slider internally clones each real slide's
        # <img> several times for its loop/lazy-load mechanism. Only the
        # FIRST <img> inside each real ".nswiper-slide" carries the correct
        # data-zoom-image; the rest are attribute-stripped clones that fall
        # through to a medium-resolution "src" and get counted as bogus
        # extra images. Where slides exist, take one <img> per slide;
        # otherwise (e.g. ".woocommerce-product-gallery") query directly.
        slides = gallery.find_elements(By.CSS_SELECTOR, ".nswiper-slide")
        if slides:
            found_imgs = []
            for slide in slides:
                slide_imgs = slide.find_elements(By.CSS_SELECTOR, "img")
                if slide_imgs:
                    found_imgs.append(slide_imgs[0])
        else:
            found_imgs = gallery.find_elements(By.CSS_SELECTOR, "img")
        print(f"   → [product page] Found {len(found_imgs)} images in gallery container")

        for el in found_imgs:
            # The same physical image can appear in more than one gallery
            # container (e.g. the main photo is duplicated into both the
            # WooCommerce gallery and the nickx slider) - only process each
            # physical element once. Duplicate URLs across containers are
            # still caught below via seen_urls.
            el_id = el.id
            if el_id in seen_elements:
                continue
            seen_elements.add(el_id)

            # Priority 1: data-zoom-image
            zoom = safe_get_attr(el, "data-zoom-image")
            if zoom:
                if zoom.startswith("//"): zoom = "https:" + zoom
                if not is_thumbnail_url(zoom, el) and zoom not in seen_urls:
                    imgs.append(zoom)
                    seen_urls.add(zoom)
                    print(f"   → Added data-zoom-image (full-res): {zoom}")
                continue

            # Priority 2: data-large_image
            large = safe_get_attr(el, "data-large_image")
            if large:
                if large.startswith("//"): large = "https:" + large
                if not is_thumbnail_url(large, el) and large not in seen_urls:
                    imgs.append(large)
                    seen_urls.add(large)
                    print(f"   → Added data-large_image (full-res): {large}")
                continue

            # Priority 3: largest entry inside srcset
            srcset = safe_get_attr(el, "srcset")
            if srcset:
                best = choose_largest_from_srcset(srcset)
                if best:
                    if best.startswith("//"): best = "https:" + best
                    if not is_thumbnail_url(best, el) and best not in seen_urls:
                        imgs.append(best)
                        seen_urls.add(best)
                        print(f"   → Added largest srcset image: {best}")
                    continue

            # Priority 4: plain src (only if NOT a thumbnail)
            src = safe_get_attr(el, "src", "data-src", "data-lazy")
            if src:
                if src.startswith("//"): src = "https:" + src
                if not is_thumbnail_url(src, el) and src not in seen_urls:
                    imgs.append(src)
                    seen_urls.add(src)
                    print(f"   → Added src image: {src}")
                elif is_thumbnail_url(src, el):
                    print(f"   → Skipped thumbnail-looking src: {src}")

    # Keep only valid image formats, dedup, and drop anything that still
    # looks like a thumbnail (belt-and-suspenders safety check).
    imgs = [u for u in imgs if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", u, re.I)]
    imgs = [u for u in imgs if not is_thumbnail_url(u)]
    imgs = dedup_preserve_order(imgs)

    print(f"   → [product page] Final full-resolution images: {len(imgs)}")
    return imgs

def extract_description_from_product_page(driver) -> str:
    """
    Extract description from electro-description clearfix class (same
    selectors/logic as before, just now run against the product page
    instead of the search tile).
    """
    desc_selectors = [
        ".electro-description.clearfix",
        ".electro-description",
        ".product-description",
        ".woocommerce-product-details__short-description",
        ".woocommerce-Tabs-panel--description",
        ".description",
        "p",
    ]

    print(f"   → [product page] Looking for descriptions with selectors: {desc_selectors}")

    for css in desc_selectors:
        try:
            desc_el = driver.find_element(By.CSS_SELECTOR, css)
            desc_text = desc_el.text.strip()
            print(f"   → Found description with selector {css}: {desc_text[:100]}...")
            if desc_text:
                return desc_text
        except NoSuchElementException:
            print(f"   → No description found with selector: {css}")
            continue

    print(f"   → No description found with any selector")
    return ""


# ---------- orchestration: search -> locate -> open product page -> extract ----------

def scrape_for_sku(driver, sku: str, pause: float, timeout: int) -> Tuple[str, str]:
    """
    Returns (plain_description, images_joined)

    New flow:
      1. Search for the SKU.
      2. Locate the first matching product tile.
      3. Pull the product URL from that tile.
      4. Open the product page (driver.get).
      5. Extract full-resolution images + description from the product page.
    """
    search_url = search_url_for_sku(sku)
    print(f"   → Navigating to search page: {search_url}")

    driver.get(search_url)
    time.sleep(pause)

    current_url = driver.current_url
    print(f"   → Current URL: {current_url}")

    # WordPress/WooCommerce auto-redirects straight to the product page
    # when the search has exactly one match - we are ALREADY on the
    # correct product page in that case. Looking for ".product" tiles
    # here would instead match the "Related Products" carousel on THIS
    # page and hijack us into scraping a completely different product.
    if "/product/" in current_url:
        print(f"   → Search redirected directly to product page (single match)")
        product_url = current_url
    else:
        time.sleep(2)

        tile = get_first_result_tile(driver, timeout=timeout, sku=sku)
        if not tile:
            print(f"   → No product tile found for SKU: {sku}")
            return "", ""

        product_url = extract_product_url_from_tile(tile)
        if not product_url:
            print(f"   → Could not extract product URL from tile for SKU: {sku}")
            return "", ""

        print(f"   → Found product URL: {product_url}")
        print(f"   → Opening product page...")

        driver.get(product_url)
        time.sleep(pause)

    # Wait specifically for the main product image inside the WooCommerce
    # Product Gallery (not "any image on the page"), so we never start
    # extracting before the real gallery has rendered.
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".woocommerce-product-gallery img.wp-post-image")
            )
        )
    except TimeoutException:
        print(f"   → Timed out waiting for product page images to load for SKU: {sku}")

    desc = extract_description_from_product_page(driver)
    imgs = extract_images_from_product_page(driver)

    print(f"   → Description length: {len(desc)}")
    print(f"   → Images found: {len(imgs)}")

    return desc, " | ".join(imgs)


# ---------- main ----------

def main():

    ap = argparse.ArgumentParser(
        description="Search + open product page scraper for hmg.jo: extracts full-resolution product images and descriptions from the actual product page.",
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
        valid_skus = df[df[args.sku_col].notna() & (df[args.sku_col].astype(str).str.strip() != '') & (df[args.sku_col].astype(str).str.lower() != 'nan')]
        if len(valid_skus) == 0:
            print("ERROR: No valid SKUs found in the file", file=sys.stderr)
            sys.exit(2)

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
        base, extn = os.path.splitext(args.input)
        output_file = f"{base}_sample_{args.sample}{extn}"
    else:
        output_file = args.input
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