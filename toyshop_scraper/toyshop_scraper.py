"""
toyshop_scraper.py

Scrapes product Title, Description, and Image URLs from
https://toyshop.alekha.com/products/{ITEM#}

Matches the CLI conventions used by the other scrapers in this
collection (e.g. fairuzy_scraper.py) so it can be launched from the
desktop_app registry:

    python toyshop_scraper.py --in input.xlsx --out output.xlsx --sku-col ITEM# [--headful]

Login:
    This site requires an authenticated session. Set these environment
    variables before running the launcher app (or before running this
    script directly):
        TOYSHOP_EMAIL
        TOYSHOP_PASSWORD
    The session is cached in auth_state.json next to this script, so
    login only needs to happen once (until the session expires).
"""

import argparse
import json
import os
import time
from urllib.parse import urljoin, urlparse, urlunparse

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# =========================================================
# CONFIG
# =========================================================
BASE_URL = "https://toyshop.alekha.com/products/"
LOGIN_URL = "https://toyshop.alekha.com/"

TITLE_SELECTOR = ".product-card-title.details-product-card-title"
DESCRIPTION_SELECTOR = ".product-description.isMobile"
GALLERY_SELECTOR = (
    ".swiper.swiper-initialized.swiper-horizontal.swiper-rtl.mySwiper2"
)

NAV_TIMEOUT_MS = 30000
SMALL_SLEEP_SECONDS = 1.0
AUTOSAVE_EVERY = 25  # Save progress to disk every N rows (safety net for long runs)

# Session cache and credentials file live next to this script (not the
# current working directory), so it works no matter where the launcher
# app runs it from.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

TOYSHOP_EMAIL = os.environ.get("TOYSHOP_EMAIL", "")
TOYSHOP_PASSWORD = os.environ.get("TOYSHOP_PASSWORD", "")

# If no environment variables were set (e.g. running from a packaged .exe
# with no terminal), fall back to a local credentials file next to this
# script. This file is NOT committed to Git (see .gitignore) and never
# contains any hardcoded secrets in the source code itself.
if not TOYSHOP_EMAIL or not TOYSHOP_PASSWORD:
    _creds_path = os.path.join(SCRIPT_DIR, "credentials.json")
    if os.path.exists(_creds_path):
        try:
            with open(_creds_path, "r", encoding="utf-8") as _f:
                _creds = json.load(_f)
            TOYSHOP_EMAIL = TOYSHOP_EMAIL or _creds.get("TOYSHOP_EMAIL", "")
            TOYSHOP_PASSWORD = TOYSHOP_PASSWORD or _creds.get("TOYSHOP_PASSWORD", "")
        except Exception as _e:
            print(f"Warning: could not read credentials.json: {_e}")

STORAGE_STATE_FILE = os.path.join(SCRIPT_DIR, "auth_state.json")


# =========================================================
# HELPERS
# =========================================================
def build_product_url(item_id):
    """Build the full product URL from an ITEM# value."""
    item_id = str(item_id).strip()
    return urljoin(BASE_URL, item_id)


def clean_image_url(src):
    """Remove query parameters from an image URL and return the clean URL."""
    if not src:
        return None
    parsed = urlparse(src)
    cleaned = parsed._replace(query="", fragment="")
    return urlunparse(cleaned)


def get_title(page):
    """Extract the product title from the page. Returns '' if not found."""
    try:
        el = page.query_selector(TITLE_SELECTOR)
        if el:
            return el.inner_text().strip()
    except Exception:
        pass
    return ""


def get_description(page):
    """Extract the product description from the page. Returns '' if not found."""
    try:
        el = page.query_selector(DESCRIPTION_SELECTOR)
        if el:
            return el.inner_text().strip()
    except Exception:
        pass
    return ""


def extract_images(page):
    """
    Extract all product image URLs from the swiper gallery.
    - collects ALL images
    - removes duplicates (preserving order)
    - ignores empty links
    - strips query parameters
    """
    images = []
    seen = set()

    try:
        gallery = page.query_selector(GALLERY_SELECTOR)
        if not gallery:
            return images

        for img in gallery.query_selector_all("img"):
            try:
                src = img.get_attribute("src")
            except Exception:
                src = None

            if not src:
                continue

            cleaned = clean_image_url(src)
            if not cleaned or cleaned in seen:
                continue

            seen.add(cleaned)
            images.append(cleaned)
    except Exception:
        pass

    return images


def is_login_page(page):
    """Return True if the current page looks like the login form."""
    try:
        return page.query_selector("input[type='password']") is not None
    except Exception:
        return False


def login(page):
    """
    Log in to toyshop.alekha.com using TOYSHOP_EMAIL / TOYSHOP_PASSWORD.
    Raises an exception if login fields/button cannot be found or if
    login does not succeed.
    """
    if not TOYSHOP_EMAIL or not TOYSHOP_PASSWORD:
        raise RuntimeError(
            "Missing credentials. Set TOYSHOP_EMAIL and TOYSHOP_PASSWORD "
            "environment variables before running this scraper."
        )

    print("Logging in to toyshop.alekha.com ...")
    page.goto(LOGIN_URL, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)

    if not is_login_page(page):
        print("  -> Already logged in (session reused).")
        return

    username_input = page.query_selector(
        "input[placeholder*='المستخدم'], input[placeholder*='البريد']"
    )
    if username_input is None:
        username_input = page.query_selector("input[type='text'], input[type='email']")

    password_input = page.query_selector("input[type='password']")

    if username_input is None or password_input is None:
        raise RuntimeError("Could not find login form fields on the page.")

    username_input.click()
    username_input.fill(TOYSHOP_EMAIL)
    password_input.click()
    password_input.fill(TOYSHOP_PASSWORD)

    submit_button = page.query_selector("button:has-text('تسجيل الدخول')")
    if submit_button is None:
        submit_button = page.query_selector("button[type='submit']")
    if submit_button is None:
        raise RuntimeError("Could not find the login submit button.")

    submit_button.click()

    logged_in = False
    for _ in range(15):
        time.sleep(1)
        if not is_login_page(page):
            logged_in = True
            break

    if not logged_in:
        try:
            page.screenshot(path=os.path.join(SCRIPT_DIR, "login_debug.png"))
        except Exception:
            pass
        raise RuntimeError(
            "Login appears to have failed (still on login page after 15s). "
            "Check login_debug.png and verify TOYSHOP_EMAIL/TOYSHOP_PASSWORD."
        )

    print("Login successful.")


def process_product(page, item_id):
    """
    Navigate to the product page for item_id and extract Title, Description,
    Images, and Status. Returns a dict with keys: Title, Description, Images, Status
    """
    result = {"Title": "", "Description": "", "Images": "", "Status": "NO_RESULT"}
    url = build_product_url(item_id)

    try:
        response = page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)

        if response is not None and response.status == 404:
            result["Status"] = "NO_RESULT"
            return result

        time.sleep(SMALL_SLEEP_SECONDS)

        # Session expired mid-run -> log back in and retry this product
        if is_login_page(page):
            login(page)
            page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
            time.sleep(SMALL_SLEEP_SECONDS)

        title = get_title(page)
        description = get_description(page)
        images = extract_images(page)

        if not title and not description and not images:
            result["Status"] = "NO_RESULT"
            return result

        result["Title"] = title
        result["Description"] = description
        result["Images"] = ";".join(images)
        result["Status"] = "MATCHED"
        return result

    except PlaywrightTimeoutError as e:
        result["Status"] = f"ERROR: Timeout - {str(e)}"
        return result
    except Exception as e:
        result["Status"] = f"ERROR: {str(e)}"
        return result


# =========================================================
# MAIN
# =========================================================
def main():
    parser = argparse.ArgumentParser(description="Toyshop (Alekha) scraper")
    parser.add_argument("--in", dest="input", required=True, help="Input Excel path")
    parser.add_argument("--out", dest="output", required=True, help="Output Excel path")
    parser.add_argument(
        "--sku-col", dest="sku_col", default="ITEM#",
        help="Column containing the item number (default: ITEM#)",
    )
    parser.add_argument("--headful", action="store_true", help="Show browser window")
    args = parser.parse_args()

    df = pd.read_excel(args.input)

    sku_col = args.sku_col if args.sku_col in df.columns else df.columns[0]
    print(f"Using column: {sku_col}")

    for col in ["Title", "Description", "Images", "Status"]:
        if col not in df.columns:
            df[col] = ""

    total = len(df)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)

        if os.path.exists(STORAGE_STATE_FILE):
            context = browser.new_context(storage_state=STORAGE_STATE_FILE)
        else:
            context = browser.new_context()

        page = context.new_page()

        login(page)
        context.storage_state(path=STORAGE_STATE_FILE)

        for idx, row in df.iterrows():
            item_id = row[sku_col]

            # Resume support: skip rows already successfully scraped
            existing_status = str(df.at[idx, "Status"]).strip()
            if existing_status == "MATCHED":
                print(f"[{idx + 1}/{total}] {item_id} -> SKIP (already done)")
                continue

            if pd.isna(item_id) or str(item_id).strip() == "":
                df.at[idx, "Status"] = "NO_RESULT"
                print(f"[{idx + 1}/{total}] <empty {sku_col}>")
                print("NO_RESULT")
                continue

            item_id_str = str(item_id).strip()
            print(f"[{idx + 1}/{total}] {item_id_str}")

            try:
                result = process_product(page, item_id_str)
            except Exception as e:
                result = {"Title": "", "Description": "", "Images": "", "Status": f"ERROR: {str(e)}"}

            df.at[idx, "Title"] = result["Title"]
            df.at[idx, "Description"] = result["Description"]
            df.at[idx, "Images"] = result["Images"]
            df.at[idx, "Status"] = result["Status"]

            print(result["Status"])

            if (idx + 1) % AUTOSAVE_EVERY == 0:
                df.to_excel(args.output, index=False)
                print(f"  -> Autosaved progress ({idx + 1}/{total}) to {args.output}")

        context.close()
        browser.close()

    df.to_excel(args.output, index=False)
    print("\nDONE")
    print(f"Saved -> {args.output}")


if __name__ == "__main__":
    main()