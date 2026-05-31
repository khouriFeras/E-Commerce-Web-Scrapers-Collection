#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, sys, time, re, logging, random
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

# ---------------- logging ----------------
log = logging.getLogger("woolapet")
log.setLevel(logging.INFO)
h = logging.StreamHandler(sys.stdout)
h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
log.addHandler(h)

HOME = "https://woolapet.es/"

# --------------- utils -------------------
def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def parse_srcset(srcset: str) -> List[str]:
    if not srcset:
        return []
    urls = []
    for part in srcset.split(","):
        u = part.strip().split(" ")[0].strip()
        if u:
            urls.append(u)
    return urls

def pick_best_img_url(img_el) -> Optional[str]:
    for a in ["data-image-large-src", "data-zoom-image", "data-large_image",
              "data-srcset", "srcset", "data-src", "src"]:
        val = img_el.get_attribute(a)
        if not val:
            continue
        if "srcset" in a:
            cand = parse_srcset(val)
            if cand:
                return cand[-1]
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
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if profile:
        opts.add_argument(f'--user-data-dir={profile}')
    if profile_dir:
        opts.add_argument(f'--profile-directory={profile_dir}')
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver

def safe_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    driver.execute_script("arguments[0].click();", el)

# --------------- site flows ---------------
def open_home(driver):
    driver.get(HOME)
    # Try to accept cookies if present
    try:
        WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        for sel in [
            "button[aria-label*='Aceptar']",
            "button[aria-label*='aceptar']",
            "button[aria-label*='Accept']",
            "button#onetrust-accept-btn-handler",
            "a#cn-accept-cookie",
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

def search_sku(driver, sku: str):
    """
    Go straight to Woolapet's search page for the SKU.
    If results container doesn't load, treat as not found.
    """
    url = f"https://woolapet.es/buscar?controller=search&s={sku}"
    driver.get(url)

    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#js-product-list"))
        )
    except TimeoutException:
        # No results page loaded
        raise RuntimeError(f"No results page for {sku}")

def page_is_404_or_empty(driver) -> bool:
    """
    True if a 404 (Página no encontrada) OR search has no products.
    Selenium can't read HTTP status directly, so we check DOM.
    """
    try:
        body_class = driver.find_element(By.TAG_NAME, "body").get_attribute("class") or ""
        if "error404" in body_class or "not-found" in body_class:
            return True
    except Exception:
        pass

    # Common 404 markers
    try:
        if driver.find_elements(By.XPATH, "//*[contains(translate(., 'Página NO ENCONTRADA', 'página no encontrada'), 'página no encontrada')]"):
            return True
    except Exception:
        pass
    try:
        h1txt = (driver.find_element(By.TAG_NAME, "h1").text or "").strip()
        if re.search(r"\b404\b", h1txt):
            return True
    except Exception:
        pass

    # Empty search results: no product cards and a notice
    if not driver.find_elements(By.CSS_SELECTOR, "article.product-miniature, ul.products li.product"):
        # Look for any 'no products found' text
        page_txt = norm_ws(driver.find_element(By.TAG_NAME, "body").text)
        if re.search(r"(no se han encontrado|no hay productos|no products)", page_txt, re.I):
            return True

    return False

def open_first_result(driver) -> bool:
    """
    Click the first product link in the search results.
    Skip if no products are listed.
    """
    try:
        results = WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#js-product-list"))
        )
    except TimeoutException:
        return False

    # Check if there are product cards
    cards = results.find_elements(By.CSS_SELECTOR, "article.product-miniature a.thumbnail")
    if not cards:
        return False

    try:
        href = cards[0].get_attribute("href")
        if not href:
            return False

        driver.execute_script("window.location = arguments[0];", href)

        # Wait until product page <h1> is visible
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1, #product h1, .product-detail h1"))
        )

        # Extra wait to let tabs/description load
        time.sleep(1)

        return True
    except Exception as e:
        log.warning(f"Failed to open product: {e}")
        return False

# ---------- IMAGES ----------
def collect_image_urls(driver) -> List[str]:
    urls: List[str] = []
    seen: Set[str] = set()

    # Exact gallery thumbs container used on Woolapet
    try:
        thumbs = driver.find_elements(
            By.CSS_SELECTOR,
            "div.product-thumb-images .thumb-container a[data-image], "
            "div#thumb-gallery .thumb-container a[data-image]"
        )
        for a in thumbs:
            try:
                href = (a.get_attribute("data-image") or "").strip()
                if not href:
                    href = (a.get_attribute("data-zoom-image") or "").strip()
                if href.startswith("//"):
                    href = "https:" + href
                if href and re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", href, re.I) and href not in seen:
                    urls.append(href)
                    seen.add(href)
            except StaleElementReferenceException:
                continue
    except Exception:
        pass

    # Also collect the main image as backup
    try:
        main_imgs = driver.find_elements(By.CSS_SELECTOR, ".product-cover img, .js-qv-product-cover img, .images-container img")
        for img in main_imgs:
            try:
                u = pick_best_img_url(img)
                if not u:
                    continue
                if u.startswith("//"):
                    u = "https:" + u
                if re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", u, re.I) and u not in seen:
                    urls.append(u)
                    seen.add(u)
            except StaleElementReferenceException:
                continue
    except Exception:
        pass

    return urls

# ---------- TEXT (Descripción + Administración) ----------
def get_tab_following_text(driver, heading_texts: List[str]) -> str:
    for h in heading_texts:
        try:
            up = h.upper()
            ci = "translate(normalize-space(), 'abcdefghijklmnopqrstuvwxyzáéíóúüñ', 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÜÑ')"
            h3 = driver.find_element(By.XPATH, f"//h3[contains(@class,'titulo-tab')][{ci}='{up}']")
            container = h3.find_element(By.XPATH, "following-sibling::*[1]")
            parts: List[str] = []
            for tag in ["p", "li", "div", "span"]:
                for el in container.find_elements(By.TAG_NAME, tag):
                    t = norm_ws(el.text)
                    if t:
                        parts.append(t)
            txt = " ".join(parts).strip()
            if txt:
                return txt
        except NoSuchElementException:
            continue
    return ""

def scrape_product(driver) -> Tuple[List[str], str, str, str]:
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1, h1.product_title, h1.entry-title"))
        )
    except TimeoutException:
        pass

    url = driver.current_url
    imgs = collect_image_urls(driver)

    # --- Description ---
    description = ""

    # Case 1: tabbed layout
    if not description:
        description = get_tab_following_text(driver, ["Descripción", "DESCRIPCIÓN", "Descripcion", "DESCRIPCION"])

    # Case 2: direct .product-description
    if not description:
        try:
            desc_block = driver.find_element(By.CSS_SELECTOR, "div.product-description")
            parts = []
            for tag in ["p", "li", "div", "span"]:
                for el in desc_block.find_elements(By.TAG_NAME, tag):
                    t = norm_ws(el.text)
                    if t:
                        parts.append(t)
            description = " ".join(parts).strip()
        except NoSuchElementException:
            pass

    # --- Administration ---
    administration = get_tab_following_text(driver, ["Administración", "ADMINISTRACIÓN", "Administracion"])
    if not administration:
        try:
            admin_block = driver.find_element(By.CSS_SELECTOR, "div#administracion, .tab-content #administracion")
            parts = []
            for tag in ["p", "li", "div", "span"]:
                for el in admin_block.find_elements(By.TAG_NAME, tag):
                    t = norm_ws(el.text)
                    if t:
                        parts.append(t)
            administration = " ".join(parts).strip()
        except NoSuchElementException:
            pass

    return imgs, description, administration, url

# --------------- IO & main ----------------
def _load_dataframe(inp_path: str, sku_col: str, sheet: str) -> pd.DataFrame:
    if inp_path.lower().endswith(".csv"):
        return pd.read_csv(inp_path)

    # Try the given sheet. If header not found, attempt header detection.
    try:
        df = pd.read_excel(inp_path, sheet_name=sheet)
    except Exception:
        df = pd.DataFrame()

    def try_header_detection(_df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if not _df.empty and sku_col in _df.columns:
            return _df
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

    # Search other sheets
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

    return df

def _save(df: pd.DataFrame, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if out_path.lower().endswith((".xlsx", ".xls")):
        df.to_excel(out_path, index=False)
    else:
        df.to_csv(out_path, index=False, encoding="utf-8-sig")

def run(args):
    df = _load_dataframe(args.inp, args.sku_col, args.sheet)
    if args.sku_col not in df.columns:
        raise SystemExit(f"SKU column '{args.sku_col}' not found. Available: {list(df.columns)}")

    # ---------- sampling & limiting ----------
    # Drop rows with empty SKU before sampling
    df = df[df[args.sku_col].astype(str).str.strip().str.lower() != "nan"]
    df = df[df[args.sku_col].astype(str).str.strip() != ""]
    if args.sample and args.sample > 0:
        df = df.sample(n=min(args.sample, len(df)), random_state=42).reset_index(drop=True)
        log.info(f"Sampling enabled: running on {len(df)} random rows")
    if args.limit and args.limit > 0:
        df = df.head(args.limit).reset_index(drop=True)
        log.info(f"Limit enabled: running on first {len(df)} rows")

    out_cols = ["Image Src", "Description", "Administration", "Source_URL"]
    for c in out_cols:
        if c not in df.columns:
            df[c] = ""

    driver = chrome_driver(args.headful, args.profile, args.profile_dir)

    try:
        for idx, row in df.iterrows():
            sku = str(row[args.sku_col]).strip()
            log.info(f"[{idx}] Searching for SKU: {sku}")

            # Skip if already scraped and not forcing
            if not args.redo and isinstance(row.get("Source_URL", ""), str) and row.get("Source_URL", "").startswith("http"):
                log.info(f"[{idx}] {sku} → already scraped; skipping")
                continue

            try:
                open_home(driver)
                if args.pause: time.sleep(0.4)
                search_sku(driver, sku)

                # 404 / empty results check
                if page_is_404_or_empty(driver):
                    log.info(f"[{idx}] No search results for {sku} (404/empty). Skipping.")
                    continue

                if not open_first_result(driver):
                    log.info(f"[{idx}] No clickable product for {sku}. Skipping.")
                    continue

                if args.pause: time.sleep(0.5)

                imgs, desc, admin, url = scrape_product(driver)

                img_str = ";".join(imgs)

                def _norm_multiline(s: str) -> str:
                    try:
                        lines = (s or "").splitlines()
                        lines = [norm_ws(l) for l in lines]
                        out_lines, prev_blank = [], False
                        for l in lines:
                            is_blank = (l.strip() == "")
                            if is_blank and prev_blank:
                                continue
                            out_lines.append(l)
                            prev_blank = is_blank
                        return "\n".join(out_lines).strip()
                    except Exception:
                        return norm_ws(s or "")

                desc_clean  = _norm_multiline(desc)
                admin_clean = _norm_multiline(admin)

                df.at[idx, "Image Src"]      = img_str
                df.at[idx, "Description"]    = desc_clean
                df.at[idx, "Administration"] = admin_clean
                df.at[idx, "Source_URL"]     = url

                log.info(f"[{idx}] images={len(imgs)} | desc_len={len(desc_clean)} | admin_len={len(admin_clean)}")
            except Exception as e:
                log.warning(f"[{idx}] Error on {sku}: {e}")
                continue

            if args.checkpoint_every and (idx + 1) % args.checkpoint_every == 0:
                _save(df, args.out)
                log.info(f"Checkpoint saved @ row {idx+1}")

        _save(df, args.out)
        log.info(f"Done. Wrote {len(df)} rows → {args.out}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

def build_argparser():
    p = argparse.ArgumentParser(description="Scrape woolapet.es by SKU: Images, Descripción, Administración.")
    p.add_argument("--in", dest="inp", required=True, help="Input Excel/CSV")
    p.add_argument("--out", required=True, help="Output Excel/CSV")
    p.add_argument("--sheet", default="Sheet1", help="Worksheet name if input is Excel")
    p.add_argument("--sku-col", required=True, help="Column containing SKU/barcode")

    p.add_argument("--headful", action="store_true", help="Show browser window")
    p.add_argument("--profile", default=None, help="Chrome user-data-dir (optional)")
    p.add_argument("--profile-dir", default=None, help="Chrome --profile-directory (e.g., 'Default')")
    p.add_argument("--pause", action="store_true", help="Small sleeps between steps")

    p.add_argument("--checkpoint-every", type=int, default=25, help="Save every N rows (0=only at end)")
    p.add_argument("--redo", action="store_true", help="Re-scrape even if Source_URL already present")
    p.add_argument("--limit", type=int, default=0, help="Process only first N rows (0=all)")
    p.add_argument("--sample", type=int, default=0, help="Randomly sample N rows before processing (0=off)")
    return p

if __name__ == "__main__":
    args = build_argparser().parse_args()
    run(args)
