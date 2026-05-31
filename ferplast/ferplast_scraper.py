#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, sys, time, re, logging
from pathlib import Path
from typing import List, Optional, Tuple, Set
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, StaleElementReferenceException

# ------------- logging -------------
log = logging.getLogger("ferplast")
log.setLevel(logging.INFO)
h = logging.StreamHandler(sys.stdout)
h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
log.addHandler(h)

HOME = "https://int.ferplast.com/"

# --------- utilities ---------
def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def parse_srcset(srcset: str) -> List[str]:
    """
    Given a srcset string, return list of URLs ordered as provided.
    We'll return the list; callers usually take the last (largest).
    """
    urls = []
    if not srcset:
        return urls
    for part in srcset.split(","):
        u = part.strip().split(" ")[0].strip()
        if u:
            urls.append(u)
    return urls

def pick_best_img_url(img_el) -> Optional[str]:
    """
    Choose the highest-res URL from srcset/data-srcset/src/data-src.
    """
    attrs = ["srcset", "data-srcset", "data-src", "src"]
    for a in attrs:
        val = img_el.get_attribute(a)
        if val:
            if "srcset" in a:
                candidates = parse_srcset(val)
                if candidates:
                    return candidates[-1]  # usually largest size at the end
            else:
                return val
    return None

def chrome_driver(headful: bool, profile: Optional[str], profile_dir: Optional[str]) -> webdriver.Chrome:
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,1000")
    # Make Selenium look more like a real browser
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if profile:
        opts.add_argument(f'--user-data-dir={profile}')
    if profile_dir:
        opts.add_argument(f'--profile-directory={profile_dir}')
    try:
        driver = webdriver.Chrome(options=opts)
    except WebDriverException as e:
        log.error("Failed to start Chrome. Ensure Chrome/driver are installed and compatible.")
        raise
    driver.set_page_load_timeout(45)
    return driver

def safe_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    el.click()

# --------- site-specific actions ---------
def open_home(driver):
    driver.get(HOME)
    # Cookie banner varies; try to accept if present
    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        # common accept buttons
        for sel in [
            "button#onetrust-accept-btn-handler",
            "button[aria-label='Accept']",
            "button[aria-label*='accept']",
            "button.cookie-accept",
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_displayed():
                    btn.click()
                    break
            except NoSuchElementException:
                continue
    except TimeoutException:
        pass

def search_sku(driver, sku: str) -> None:
    """
    Type SKU in the site search and submit.
    Works with the header search on Ferplast.
    """
    # Many Shopify-based themes toggle a search drawer. Try common selectors.
    # If there's a quick search icon, click it first.
    try:
        # Try to open search if a button exists
        for sel in ["button[aria-controls*='search']", "button.header__icon--search", "summary[aria-controls*='Search']"]:
            try:
                b = driver.find_element(By.CSS_SELECTOR, sel)
                if b.is_displayed():
                    b.click()
                    time.sleep(0.2)
                    break
            except NoSuchElementException:
                continue
    except Exception:
        pass

    # Locate the input
    input_el = None
    for sel in [
        "input[type='search']",
        "input[name='q']",
        "form[action*='search'] input",
        "input[placeholder*='Search']",
    ]:
        try:
            input_el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            if input_el.is_displayed():
                break
        except TimeoutException:
            continue

    if not input_el:
        raise RuntimeError("Search input not found on homepage.")

    input_el.clear()
    input_el.send_keys(sku)
    input_el.send_keys(Keys.ENTER)

def open_first_result(driver) -> bool:
    """
    On results page, open the first product card/link.
    Returns True if navigated, False if nothing found.
    """
    try:
        # Wait for search results or empty state
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except TimeoutException:
        return False

    # Try common product link selectors
    selectors = [
        "a[href*='/products/']",
        "a.product-card__link",
        "a.card__link",
        "a.full-unstyled-link"
    ]
    for sel in selectors:
        try:
            links = driver.find_elements(By.CSS_SELECTOR, sel)
            links = [l for l in links if l.is_displayed()]
            if links:
                safe_click(driver, links[0])
                return True
        except Exception:
            continue
    return False

def collect_image_urls(driver) -> List[str]:
    """
    Collect product image URLs only from the gallery thumbnails on Ferplast.
    Primary target: elements under `.product__thumb-item.slick-slide`.
    Falls back to a broader search if nothing is found.
    """
    urls: List[str] = []
    seen: Set[str] = set()

    # 1) Strict: only product thumbnails (requested)
    thumb_links = []
    try:
        # Anchors inside each thumbnail slide often hold the full-size URL in href
        thumb_links = driver.find_elements(
            By.CSS_SELECTOR,
            ".product__thumb-item.slick-slide a[href]",
        )
    except Exception:
        thumb_links = []

    if thumb_links:
        for a in thumb_links:
            try:
                href = (a.get_attribute("href") or "").strip()
                if not href:
                    # Sometimes the anchor has no href and the image is inside
                    imgs = a.find_elements(By.TAG_NAME, "img")
                    if imgs:
                        href = pick_best_img_url(imgs[0]) or ""
                if not href:
                    continue
                # Normalize protocol-relative URLs
                if href.startswith("//"):
                    href = "https:" + href
                # Filter to Ferplast CDN media
                if not re.search(r"int\\.ferplast\\.com/.+\\.(?:jpg|jpeg|png|webp)(?:\\?|$)|/cdn/(shop|shopify)/files/|/cdn/shop/files/", href):
                    continue
                if href not in seen:
                    urls.append(href)
                    seen.add(href)
            except StaleElementReferenceException:
                continue

    # 2) Fallback: previous heuristic if strict selection found nothing
    if not urls:
        containers = driver.find_elements(
            By.CSS_SELECTOR,
            ".product__media, .product-media, .product__media-list, .media, .image-wrap, .product-gallery, .slider, .product__thumbnail",
        ) or [driver.find_element(By.TAG_NAME, "body")]

        imgs = []
        for c in containers:
            try:
                imgs.extend(c.find_elements(By.TAG_NAME, "img"))
            except StaleElementReferenceException:
                continue
        if not imgs:
            imgs = driver.find_elements(By.TAG_NAME, "img")

        for img in imgs:
            try:
                u = pick_best_img_url(img)
                if not u:
                    continue
                if not re.search(r"/cdn/(shop|shopify)/files/|/cdn/shop/files/", u):
                    if not re.search(r"int\\.ferplast\\.com/.+\\.(?:jpg|jpeg|png|webp)(?:\\?|$)", u):
                        continue
                u = u.strip()
                if u not in seen:
                    urls.append(u)
                    seen.add(u)
            except StaleElementReferenceException:
                continue

    return urls

def text_under_section(driver, title_en: str) -> str:
    """
    Find a section by heading text (exact match in English UI, e.g., 'CHARACTERISTICS', 'DESCRIPTION'),
    then return concatenated clean text (li/paragraphs).
    """
    # Case-insensitive match on headings (h1/h2/h3) or accordion headers
    up = title_en.upper()
    ci = (
        "translate(normalize-space(), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')"
    )
    xpaths = [
        f"//h1[{ci}='{up}']/following-sibling::*",
        f"//h2[{ci}='{up}']/following-sibling::*",
        f"//h3[{ci}='{up}']/following-sibling::*",
        f"//section[.//*[self::h1 or self::h2 or self::h3][{ci}='{up}']]",
        f"//*[contains(@class,'product__accordion') or contains(@class,'accordion')][.//*[{ci}='{up}']]",
    ]
    container = None
    for xp in xpaths:
        try:
            el = driver.find_element(By.XPATH, xp)
            container = el
            break
        except NoSuchElementException:
            continue

    if not container:
        # Fallback: entire page text (not ideal)
        return ""

    parts = []
    # Collect list items and paragraphs under the container
    for tag in ["li", "p", "div"]:
        try:
            for el in container.find_elements(By.TAG_NAME, tag):
                txt = norm_ws(el.text)
                if txt:
                    parts.append(txt)
        except StaleElementReferenceException:
            continue

    # Post-process: try to keep only the block up to the next big section
    joined = " ".join(parts)
    # Deduplicate bullet dots etc.
    joined = re.sub(r"•\s*", "• ", joined)
    return joined.strip()

def accordion_list_items(driver, title_en: str) -> List[str]:
    """
    For Ferplast accordion blocks:
      Find the button/label whose inner .tab-title equals title_en, then read
      list items under its associated collapsible content's
      .collapsible-content__inner.rte ul li
    """
    up = title_en.upper()
    ci = (
        "translate(normalize-space(), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')"
    )
    # Locate the label/button that toggles the section
    x_btn = (
        f"//button[contains(@class,'collapsible-trigger')][.//div[contains(@class,'tab-title')][{ci}='{up}']]"
    )
    try:
        btn = driver.find_element(By.XPATH, x_btn)
    except Exception:
        return []

    # Its following sibling div is the content wrapper
    try:
        content = btn.find_element(
            By.XPATH,
            "following-sibling::div[contains(@class,'collapsible-content')][1]//div[contains(@class,'collapsible-content__inner') and contains(@class,'rte')]",
        )
    except Exception:
        return []

    items: List[str] = []
    try:
        for li in content.find_elements(By.CSS_SELECTOR, "ul li"):
            t = norm_ws(li.text)
            if t:
                items.append(t)
    except Exception:
        pass
    return items

def accordion_text(driver, title_en: str) -> str:
    """
    Like accordion_list_items but returns concatenated plain text from the
    .collapsible-content__inner.rte node (paragraphs and lists joined).
    """
    up = title_en.upper()
    ci = (
        "translate(normalize-space(), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')"
    )
    x_btn = (
        f"//button[contains(@class,'collapsible-trigger')][.//div[contains(@class,'tab-title')][{ci}='{up}']]"
    )
    try:
        btn = driver.find_element(By.XPATH, x_btn)
    except Exception:
        return ""

    try:
        content = btn.find_element(
            By.XPATH,
            "following-sibling::div[contains(@class,'collapsible-content')][1]//div[contains(@class,'collapsible-content__inner') and contains(@class,'rte')]",
        )
    except Exception:
        return ""

    parts: List[str] = []
    try:
        for tag in ["p", "li", "div", "span"]:
            for el in content.find_elements(By.TAG_NAME, tag):
                txt = norm_ws(el.text)
                if txt:
                    parts.append(txt)
    except Exception:
        pass
    joined = " ".join(parts)
    joined = re.sub(r"•\s*", "• ", joined)
    return joined.strip()

def scrape_product(driver) -> Tuple[List[str], str, str, str]:
    """
    Returns: (images, characteristics, description, url)
    """
    # Wait for product title to ensure page loaded
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1, h1.product__title, .product__title"))
        )
    except TimeoutException:
        pass

    url = driver.current_url
    imgs = collect_image_urls(driver)

    # Try broader set of section titles for characteristics/specs
    characteristics = ""
    for t in [
        "CHARACTERISTICS",
        "FEATURES",
        "TECHNICAL FEATURES",
        "SPECIFICATIONS",
        "TECHNICAL SPECIFICATIONS",
        "PRODUCT FEATURES",
        "مميزات المنتج",
        "المواصفات",
    ]:
        # Prefer explicit accordion list items if present
        items = accordion_list_items(driver, t)
        if items:
            characteristics = "\n".join(f"- {x}" for x in items)
            break
        characteristics = text_under_section(driver, t)
        if characteristics:
            break

    # Try broader set for description
    description = ""
    for t in [
        "DESCRIPTION",
        "PRODUCT DESCRIPTION",
        "DETAILS",
        "OVERVIEW",
        "About this item",
        "الوصف",
    ]:
        # Prefer accordion text if present
        desc_acc = accordion_text(driver, t)
        if desc_acc:
            description = desc_acc
            break
        description = text_under_section(driver, t)
        if description:
            break

    return imgs, characteristics, description, url

# --------- main workflow ---------
def _load_dataframe(inp_path: str, sku_col: str, sheet: str) -> pd.DataFrame:
    """
    Load Excel/CSV. If the expected column isn't immediately available,
    try to auto-detect the header row by scanning the first 30 rows.
    Also tries other sheets if the provided sheet name doesn't contain the header.
    """
    path_lower = inp_path.lower()
    # CSV fast path
    if path_lower.endswith((".csv",)):
        df = pd.read_csv(inp_path)
        return df

    # Excel: first try the requested sheet as-is
    try:
        df = pd.read_excel(inp_path, sheet_name=sheet)
    except Exception:
        df = pd.DataFrame()

    def try_header_detection(_df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if not _df.empty and isinstance(_df.columns, pd.Index) and sku_col in _df.columns:
            return _df
        # Read without header and scan first 30 rows for the header cell
        try:
            raw = pd.read_excel(inp_path, sheet_name=sheet, header=None)
        except Exception:
            return None
        max_scan = min(30, len(raw))
        for r in range(max_scan):
            row_vals = [str(v).strip() for v in list(raw.iloc[r].values)]
            if sku_col in row_vals:
                header = [str(v).strip() for v in raw.iloc[r].values]
                body = raw.iloc[r + 1 :].reset_index(drop=True)
                body.columns = header
                return body
        return None

    detected = try_header_detection(df)
    if detected is not None:
        return detected

    # If still not found, iterate sheets to find one that contains the header row
    try:
        xls = pd.ExcelFile(inp_path)
        for sh in xls.sheet_names:
            try:
                raw = pd.read_excel(inp_path, sheet_name=sh, header=None)
            except Exception:
                continue
            max_scan = min(30, len(raw))
            for r in range(max_scan):
                row_vals = [str(v).strip() for v in list(raw.iloc[r].values)]
                if sku_col in row_vals:
                    header = [str(v).strip() for v in raw.iloc[r].values]
                    body = raw.iloc[r + 1 :].reset_index(drop=True)
                    body.columns = header
                    log.info(f"Auto-detected header on sheet '{sh}' at row {r}")
                    return body
    except Exception:
        pass

    # Last fallback: return whatever we could read (maybe empty)
    return df

def run(args):
    # Load input
    df = _load_dataframe(args.inp, args.sku_col, args.sheet)
    if args.sku_col not in df.columns:
        raise SystemExit(f"SKU column '{args.sku_col}' not found. Available: {list(df.columns)}")

    # Prepare output columns
    out_cols = ["Image Src", "Characteristics", "Description", "Source_URL"]
    for c in out_cols:
        if c not in df.columns:
            df[c] = ""

    driver = chrome_driver(args.headful, args.profile, args.profile_dir)
    try:
        # Apply hard row cap early if requested
        iterable = df.head(args.limit) if args.limit and args.limit > 0 else df
        for idx, row in iterable.iterrows():
            sku = str(row[args.sku_col]).strip()
            if not sku or sku.lower() == "nan":
                log.info(f"[{idx}] Empty SKU; skipping")
                continue

            # Skip if already scraped and not forcing
            if not args.redo and isinstance(row.get("Source_URL", ""), str) and row.get("Source_URL", "").startswith("http"):
                log.info(f"[{idx}] {sku} → already scraped; skipping")
                continue

            log.info(f"[{idx}] Searching for SKU: {sku}")
            try:
                open_home(driver)
                if args.pause: time.sleep(0.5)
                search_sku(driver, sku)

                if not open_first_result(driver):
                    log.info(f"[{idx}] No search results for {sku}")
                    continue

                if args.pause: time.sleep(0.6)
                imgs, feats, desc, url = scrape_product(driver)

                # Clean images and preserve multiline text formatting
                img_str = ";".join(imgs)

                def _norm_lines(s: str) -> str:
                    try:
                        lines = (s or "").splitlines()
                        lines = [norm_ws(l) for l in lines]
                        # collapse excessive blank lines
                        out_lines = []
                        prev_blank = False
                        for l in lines:
                            is_blank = (l.strip() == "")
                            if is_blank and prev_blank:
                                continue
                            out_lines.append(l)
                            prev_blank = is_blank
                        return "\n".join(out_lines).strip()
                    except Exception:
                        return norm_ws(s or "")

                feats_clean = _norm_lines(feats)
                desc_clean  = _norm_lines(desc)

                # Merge into single Description column: description, blank line, then characteristics
                if feats_clean:
                    combined = f"{desc_clean}\n\n{feats_clean}" if desc_clean else feats_clean
                else:
                    combined = desc_clean

                df.at[idx, "Image Src"] = img_str
                # Clear old Characteristics as requested; keep the column present
                df.at[idx, "Characteristics"] = ""
                df.at[idx, "Description"] = combined
                df.at[idx, "Source_URL"] = url

                log.info(f"[{idx}] Found {len(imgs)} images | chars={len(feats)} | desc={len(desc)}")
            except Exception as e:
                log.warning(f"[{idx}] Error on {sku}: {e}")
                continue

            # checkpointing
            if args.checkpoint_every and (idx + 1) % args.checkpoint_every == 0:
                _save(df, args.out)
                log.info(f"Checkpoint saved @ row {idx+1}")

            # no per-row processed counting needed; df is pre-sliced when --limit used

        _save(df, args.out)
        log.info(f"Done. Wrote {len(df)} rows → {args.out}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

def _save(df: pd.DataFrame, out_path: str):
    out_path = str(out_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if out_path.lower().endswith((".xlsx", ".xls")):
        df.to_excel(out_path, index=False)
    else:
        df.to_csv(out_path, index=False, encoding="utf-8-sig")

def build_argparser():
    p = argparse.ArgumentParser(description="Scrape Ferplast by SKU: images, characteristics, description.")
    p.add_argument("--in", dest="inp", required=True, help="Input Excel/CSV")
    p.add_argument("--out", required=True, help="Output Excel/CSV")
    p.add_argument("--sheet", default="Sheet1", help="Worksheet name if input is Excel")
    p.add_argument("--sku-col", required=True, help="Column name containing SKU/barcode")

    p.add_argument("--headful", action="store_true", help="Run with a visible browser window")
    p.add_argument("--profile", default=None, help="Chrome user-data-dir (optional)")
    p.add_argument("--profile-dir", default=None, help="Chrome --profile-directory (e.g., 'Default')")
    p.add_argument("--pause", action="store_true", help="Small sleeps between steps (safer)")

    p.add_argument("--checkpoint-every", type=int, default=25, help="Save every N rows (0=only at end)")
    p.add_argument("--redo", action="store_true", help="Re-scrape even if Source_URL already present")
    p.add_argument("--limit", type=int, default=0, help="Process only first N rows (0=all)")
    return p

if __name__ == "__main__":
    args = build_argparser().parse_args()
    run(args)
