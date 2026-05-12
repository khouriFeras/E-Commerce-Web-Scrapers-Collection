#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, os, re, time, math, html
from typing import List, Optional, Tuple, Iterable
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException, WebDriverException
)

BASE = "https://ronixtools.com"
SEARCH_URL = BASE + "/en/search/?term={term}"
# Alternative search URL if the above doesn't work
SEARCH_URL_ALT = BASE + "/en/search?term={term}"

ACC_SEL = ".accordion.px-0"       # description source (TEXT)
SLIDER_COL_SEL = ".slider__col"   # gallery source for images

# ---------------- utils ----------------

def build_driver(headful: bool, profile: Optional[str] = None) -> webdriver.Chrome:
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    
    # Anti-detection measures
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    if profile:
        opts.add_argument(f"--user-data-dir={os.path.abspath(profile)}")
    
    driver = webdriver.Chrome(options=opts)
    
    # Execute script to remove webdriver property
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    driver.set_page_load_timeout(45)
    return driver

def wait_css(driver, sel, timeout=15):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))

def normalize(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())

def parse_srcset(srcset: str) -> Optional[str]:
    if not srcset:
        return None
    best_url, best_w = None, -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except:
                w = 0
        if w > best_w:
            best_w, best_url = w, url
    return best_url or None

def absolutize(url: str) -> str:
    if not url: return url
    if url.startswith("//"): return "https:" + url
    if url.startswith("/"):  return BASE + url
    return url

def clean_image_url(u: str) -> str:
    if not u: return u
    # strip sizing/format junk so we keep canonical URL
    u = re.sub(r"([?&])(w|h|width|height|fit|format|auto|quality|q)=[^&]+", r"\1", u, flags=re.I)
    u = re.sub(r"[?&]+$", "", u)
    return u

def scroll_page(driver, steps: int = 6, pause: float = 0.35):
    """Slow scroll to trigger lazy-loading."""
    try:
        height = driver.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
    except WebDriverException:
        height = 3000
    step = max(300, math.ceil(height / max(1, steps)))
    y = 0
    for _ in range(steps):
        y += step
        driver.execute_script("window.scrollTo(0, arguments[0]);", y)
        time.sleep(pause)
    driver.execute_script("window.scrollTo(0, 0);")

def text_to_html_paragraphs(txt: str) -> str:
    # collapse excessive blanks, split on blank lines, escape to safe HTML, wrap in <p>
    if not txt: return ""
    txt = re.sub(r"\r\n?", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    paras = [p.strip() for p in re.split(r"\n\s*\n+", txt) if p.strip()]
    return "\n".join(f"<p>{html.escape(p).replace('\n', '<br/>')}</p>" for p in paras)

def html_to_plain_text(html_content: str) -> str:
    """Convert HTML content to plain text by removing all HTML tags."""
    if not html_content:
        return ""
    
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', html_content)
    
    # Decode HTML entities
    text = html.unescape(text)
    
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)  # Replace multiple whitespace with single space
    text = re.sub(r'\n\s*\n', '\n\n', text)  # Clean up multiple newlines
    text = text.strip()
    
    return text

def save_progress(df, output_path, processed_count):
    """Save current progress to Excel file"""
    try:
        # Normalize file extension for pandas compatibility
        if output_path.lower().endswith(".xls"):
            output_path = output_path[:-4] + ".xlsx"
        
        df.to_excel(output_path, index=False)
        print(f"   💾 Progress saved: {processed_count} products processed → {output_path}")
    except Exception as e:
        print(f"   ⚠️  Warning: Could not save progress: {e}")

# ---------------- description (from .accordion.px-0 TEXT) ----------------

def expand_all_accordion_panels(driver):
    """Try to expand any collapsed panels inside .accordion.px-0 to ensure text is visible."""
    try:
        accs = driver.find_elements(By.CSS_SELECTOR, ACC_SEL)
    except Exception:
        return
    for acc in accs:
        toggles = []
        for css in (".accordion-button", ".accordion-header button", "[data-bs-toggle='collapse']", "summary"):
            try:
                toggles.extend(acc.find_elements(By.CSS_SELECTOR, css))
            except Exception:
                pass
        for t in toggles:
            try:
                expanded = (t.get_attribute("aria-expanded") or "").lower()
                if expanded in ("", "false"):
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
                    time.sleep(0.1)
                    try:
                        t.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", t)
                    time.sleep(0.2)
            except Exception:
                continue

def get_description_html(driver) -> str:
    """Gather ALL visible text inside .accordion.px-0 and convert to simple HTML paragraphs."""
    try:
        wait_css(driver, ACC_SEL, timeout=10)
    except TimeoutException:
        # fallback: continue, maybe page uses a different layout
        pass

    expand_all_accordion_panels(driver)

    texts = []
    try:
        for acc in driver.find_elements(By.CSS_SELECTOR, ACC_SEL):
            t = (acc.text or "").strip()
            if t:
                texts.append(t)
    except Exception:
        pass

    joined = "\n\n".join([t for t in texts if t])
    return text_to_html_paragraphs(joined)

# ---------------- images (from .slider__col + alt preference) ----------------

def collect_product_images(driver, product_title: str = "") -> List[str]:
    """
    Collect slider images + main product image from specific class 'col-12 px-sm-0 imageOnly-parent'.
    Keep the largest version per unique base URL.
    """
    print("   → Searching for slider images + main product image from specific class...")

    # ensure lazy-loaded slides are in DOM
    scroll_page(driver, steps=6, pause=0.3)

    containers = []
    
    # Get slider images (keep existing behavior)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, SLIDER_COL_SEL))
        )
        containers.extend(driver.find_elements(By.CSS_SELECTOR, SLIDER_COL_SEL))
        print("   → Found .slider__col containers")
    except TimeoutException:
        print("   → .slider__col not found")
    
    # Get main product image from specific class
    try:
        main_image_containers = driver.find_elements(By.CSS_SELECTOR, ".col-12.px-sm-0.imageOnly-parent")
        if main_image_containers:
            containers.extend(main_image_containers)
            print("   → Found main product image containers with class 'col-12 px-sm-0 imageOnly-parent'")
        else:
            print("   → No containers found with class 'col-12 px-sm-0 imageOnly-parent'")
    except Exception as e:
        print(f"   → Error finding main product image containers: {e}")
    
    if not containers:
        print("   → No image containers found; falling back to page-wide images")
        containers = [driver.find_element(By.TAG_NAME, "body")]

    raw_pairs = set()  # (url, alt)

    def harvest(scope):
        imgs = []
        for css in ("img", "picture img", "source"):
            try:
                imgs.extend(scope.find_elements(By.CSS_SELECTOR, css))
            except Exception:
                continue
        for el in imgs:
            try:
                srcset = el.get_attribute("srcset") or ""
                src = el.get_attribute("src") or ""
                alt = (el.get_attribute("alt") or "").strip()
                url = parse_srcset(srcset) if srcset else src
                url = absolutize(url)
                if not url or url.startswith("data:"):
                    continue
                raw_pairs.add((url, alt))
            except StaleElementReferenceException:
                continue

    for c in containers:
        harvest(c)

    # filter + rank - keep slider images + main product image from specific class
    by_base = {}  # base_url -> (chosen_url, alt, score)
    for url, alt in raw_pairs:
        lo_url, lo_alt = url.lower(), (alt or "").lower()

        # Skip obviously non-product images
        if any(t in lo_url for t in ("sprite", "icon", "arrow", "thumb", "placeholder", "loading", "spinner")):
            continue

        # Keep all images from both slider and main product image containers
        # No alt text filtering - just collect all images from the specified containers

        # size score from URL (bigger -> better)
        size_score = 0
        m = re.search(r"[?&](w|width)=(\d+)", url, re.I)
        if m:
            try:
                size_score = int(m.group(2))
            except:
                pass
        if any(tag in lo_url for tag in ("_original", "_full", "_big", "2048", "1600", "1200")):
            size_score = max(size_score, 1200)
        elif any(tag in lo_url for tag in ("_medium", "800", "768", "640")):
            size_score = max(size_score, 800)
        elif any(tag in lo_url for tag in ("_small", "thumb", "320", "256")):
            size_score = max(size_score, 320)

        # dedup by base url
        cleaned = clean_image_url(url)
        base = re.sub(r"[?&](w|h|width|height|size|resize|fit|format|auto|quality|q)=[^&]*", "", cleaned, flags=re.I)
        base = re.sub(r"[?&]+$", "", base)

        # Keep the highest quality version of each unique image
        if base not in by_base or size_score > by_base[base][2]:
            by_base[base] = (cleaned, alt, size_score)

    final_urls = [v[0] for v in by_base.values()]
    # Sort by quality (highest first)
    final_urls.sort(key=lambda u: -len(u))
    print(f"   → Found {len(final_urls)} unique product images (slider + main product image)")
    return final_urls

# ---------------- step 1+2: search then click first product ----------------

def search_and_open_first_product(driver, sku: str, pause: float) -> Optional[str]:
    # First, go to homepage to look more natural
    print(f"   → Going to homepage first...")
    driver.get("https://ronixtools.com/en/")
    time.sleep(2)  # Wait a bit like a human would
    
    # Try the primary search URL
    url = SEARCH_URL.format(term=sku)
    print(f"   → Searching: {url}")
    driver.get(url)
    
    # Add human-like delay
    time.sleep(1)
    
    # Check if we got redirected to homepage
    current_url = driver.current_url
    print(f"   → Current URL after search: {current_url}")
    
    if current_url == "https://ronixtools.com/en/" or current_url == "https://ronixtools.com/en":
        print("   → Redirected to homepage, trying alternative search URL...")
        time.sleep(2)  # Wait before retry
        url = SEARCH_URL_ALT.format(term=sku)
        print(f"   → Trying alternative: {url}")
        driver.get(url)
        time.sleep(1)
        current_url = driver.current_url
        print(f"   → Alternative URL result: {current_url}")

    # Wait for results or an empty-state
    print("   → Waiting for search results...")
    try:
        WebDriverWait(driver, 20).until(
            EC.any_of(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='/en/product/']")),
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='/product/']")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-empty], .no-result, .empty"))
            )
        )
    except TimeoutException:
        print("   → Timeout waiting for search results")
        return None

    # prefer product links
    selectors = [
        "a[href*='/en/product/']",
        "a[href*='/product/']"
    ]
    links = []
    for sel in selectors:
        try:
            for a in driver.find_elements(By.CSS_SELECTOR, sel):
                href = a.get_attribute("href") or ""
                if not href: 
                    continue
                if any(x in href for x in ("/account", "/login", "/orders", "/cart", "/search", "/blog/")):
                    continue
                links.append(a)
            if links:
                break
        except Exception:
            continue

    if not links:
        print("   × No product links found")
        return None

    first = links[0]
    href = first.get_attribute("href")
    print(f"   → Opening product: {href}")

    old_url = driver.current_url
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", first)
        time.sleep(0.2)
        try:
            first.click()
        except Exception:
            driver.execute_script("arguments[0].click();", first)

        try:
            WebDriverWait(driver, 8).until(EC.url_changes(old_url))
        except TimeoutException:
            driver.get(href)
    except Exception:
        driver.get(href)

    time.sleep(pause)
    return driver.current_url

# ---------------- step 3: scrape ----------------

def scrape_product(driver) -> Tuple[str, str]:
    # Title not required for your filters, but keeping for future tweaks
    try:
        title = driver.find_element(By.CSS_SELECTOR, "h1").text.strip()
    except Exception:
        title = ""
    body_html = get_description_html(driver)  # from .accordion.px-0 TEXT
    body_text = html_to_plain_text(body_html)  # Convert HTML to plain text
    images = collect_product_images(driver, product_title=title)  # from .slider__col + main product areas
    return body_text, ";".join(images)

# ---------------- per-SKU orchestrator (1..5) ----------------

def run_for_sku(driver, sku: str, pause: float) -> Tuple[str, str, str]:
    url = search_and_open_first_product(driver, sku, pause)
    if not url:
        return "", "", ""

    # wait for either description accordion or gallery columns to appear
    try:
        WebDriverWait(driver, 15).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, ACC_SEL)),
                EC.presence_of_element_located((By.CSS_SELECTOR, SLIDER_COL_SEL)),
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
            )
        )
    except TimeoutException:
        pass

    body_html, image_src = scrape_product(driver)
    return body_html, image_src, url

# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="RonixTools scraper: search by SKU -> open first result -> scrape Description (.accordion.px-0 text) & Images (.slider__col).")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", dest="out", required=True, help="Output Excel file")
    ap.add_argument("--sheet", dest="sheet", default=None, help="Worksheet name (default: first)")
    ap.add_argument("--sku-col", dest="sku_col", required=True, help="Column containing SKUs")
    ap.add_argument("--pause", type=float, default=3.0, help="Pause between steps (sec) - increased for rate limiting")
    ap.add_argument("--headful", action="store_true", help="Headed Chrome")
    ap.add_argument("--profile", default=None, help="Chrome user-data-dir (optional)")
    ap.add_argument("--delay", type=float, default=3.0, help="Delay between SKU processing (sec) - for rate limiting")
    ap.add_argument("--max-retries", type=int, default=3, help="Max retries per SKU if rate limited")
    ap.add_argument("--max-skus", type=int, default=5, help="Max SKUs to process per session (safety limit)")
    ap.add_argument("--session-break", type=int, default=300, help="Break time between sessions (sec) - 5 min default")
    ap.add_argument("--save-interval", type=int, default=50, help="Save progress every N products")
    args = ap.parse_args()

    # read Excel - try to read from output file first (if it exists and has data)
    output_path = str(args.out)
    if output_path.lower().endswith(".xls"):
        output_path = output_path[:-4] + ".xlsx"
    
    try:
        # Try to read from output file first (to continue from where we left off)
        if os.path.exists(output_path):
            df = pd.read_excel(output_path, sheet_name=args.sheet)
            print(f"📁 Reading from existing output file: {output_path}")
        else:
            # Read from input file if output doesn't exist
            df = pd.read_excel(args.inp, sheet_name=args.sheet)
            print(f"📁 Reading from input file: {args.inp}")
    except Exception as e:
        # Fallback to input file if output file is corrupted
        print(f"⚠️  Could not read output file, using input file: {e}")
        df = pd.read_excel(args.inp, sheet_name=args.sheet)
    
    if isinstance(df, dict):  # multiple sheets
        if args.sheet is None:
            first_sheet = list(df.keys())[0]
            print(f"Multiple sheets detected. Using first sheet: '{first_sheet}'")
            df = df[first_sheet]
        else:
            df = df[args.sheet]

    # ensure output columns exist
    for col in ["Body (Text)", "Image Src", "Source_URL"]:
        if col not in df.columns:
            df[col] = ""

    driver = build_driver(args.headful, args.profile)

    print(f"\n⚠️  ANTI-BAN MODE ENABLED ⚠️")
    print(f"   → Processing max {args.max_skus} SKUs per session")
    print(f"   → {args.delay}s delay between SKUs")
    print(f"   → {args.pause}s delay between actions")
    print(f"   → Auto-save every {args.save_interval} products")
    print(f"   → Additional random delays for safety")
    print(f"   → If you get banned, wait {args.session_break//60} minutes before next session\n")

    try:
        processed_count = 0
        for i, row in df.iterrows():
            # Safety limit: stop after max SKUs per session
            if processed_count >= args.max_skus:
                print(f"\n⚠️  SAFETY LIMIT REACHED: Processed {args.max_skus} SKUs in this session.")
                print(f"   → To continue, wait {args.session_break//60} minutes and run again.")
                print(f"   → Or increase --max-skus parameter if you want to process more.")
                break
                
            sku = str(row[args.sku_col]).strip()
            if not sku or sku.lower() in ("nan", "none", "null"):
                continue

            # Skip if this SKU already has data (already processed)
            body_text = str(row.get("Body (Text)", "")).strip() if pd.notna(row.get("Body (Text)", "")) else ""
            image_src = str(row.get("Image Src", "")).strip() if pd.notna(row.get("Image Src", "")) else ""
            source_url = str(row.get("Source_URL", "")).strip() if pd.notna(row.get("Source_URL", "")) else ""
            
            if body_text or image_src or source_url:
                print(f"[{i+1}/{len(df)}] SKU={sku} → Already processed, skipping...")
                print(f"   → Body: {len(body_text)} chars, Images: {len(image_src)} chars, URL: {source_url[:50]}...")
                continue

            print(f"[{i+1}/{len(df)}] SKU={sku} → search/click/scrape (Session: {processed_count+1}/{args.max_skus})")
            
            # Add delay between SKU processing to respect rate limits
            if i > 0:  # Don't delay before first SKU
                print(f"   → Waiting {args.delay}s to respect rate limits...")
                time.sleep(args.delay)
            
            retry_count = 0
            body_html, image_src, url = "", "", ""
            
            while retry_count < args.max_retries:
                try:
                    body_html, image_src, url = run_for_sku(driver, sku, args.pause)
                    break  # Success, exit retry loop
                except (TimeoutException, WebDriverException) as e:
                    print(f"   × Error: {str(e)}")
                    break  # Don't retry for these errors
                except Exception as e:
                    error_msg = str(e).lower()
                    if "rate limit" in error_msg or "too many requests" in error_msg or "blocked" in error_msg:
                        retry_count += 1
                        if retry_count < args.max_retries:
                            wait_time = 30 * retry_count  # Exponential backoff
                            print(f"   × Rate limit hit! Waiting {wait_time}s before retry {retry_count}/{args.max_retries}...")
                            time.sleep(wait_time)
                        else:
                            print(f"   × Max retries ({args.max_retries}) exceeded for SKU {sku}")
                            break
                    else:
                        print(f"   × Unexpected error: {str(e)}")
                        break

            if url:
                df.at[i, "Body (Text)"] = body_html
                df.at[i, "Image Src"]   = image_src
                df.at[i, "Source_URL"]  = url
                print(f"   ✓ OK: {url} | images={len(image_src.split(';')) if image_src else 0}")
            else:
                print(f"   × Not found")
            
            processed_count += 1
            
            # Auto-save progress every N products
            if processed_count % args.save_interval == 0:
                save_progress(df, args.out, processed_count)
            
            if processed_count < args.max_skus:
                random_delay = min(7, 3 + (2 * (processed_count % 3)))  # 5, 7, 9, 5 (max 7s)
                print(f"   → Additional safety delay: {random_delay}s")
                time.sleep(random_delay)

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    # Final save
    save_progress(df, args.out, processed_count)
    print(f"\n✅ Scraping completed! Final results saved.")

if __name__ == "__main__":
        main()  