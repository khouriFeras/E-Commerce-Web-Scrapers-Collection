"""
zananiri_scraper.py

Scrapes product descriptions from zananirijo.com by searching for each
Variant SKU found in Tefal_cookware.xlsx, then writes the results into
a new file: zananiri_test_20.xlsx

Usage:
    python zananiri_scraper.py

Requirements:
    pip install playwright pandas openpyxl
    playwright install chromium
"""

import sys
import traceback

import pandas as pd
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

# =========================
# CONFIG
# =========================

INPUT_FILE = "Tefal_electrical.xlsx"
OUTPUT_FILE = "Tefal_electrical_full.xlsx"

SKU_COLUMN = "Part No."
DESCRIPTION_COLUMN = "Description"
STATUS_COLUMN = "Status"

BASE_URL = "https://www.zananirijo.com"
SEARCH_URL_TEMPLATE = (
    BASE_URL + "/search?q={sku}&options%5Bprefix%5D=last"
)

PRODUCT_GRID_SELECTOR = "#items-grid"
PRODUCT_LINK_SELECTOR = '#items-grid a[href*="/products/"]'
DESCRIPTION_SELECTOR = "div.x-block-description .rte"

# Easy to change/remove for a full run: set to None to process all rows.
ROW_LIMIT = None

# Timeouts (ms)
NAV_TIMEOUT = 30000
ELEMENT_TIMEOUT = 10000


# =========================
# CORE FUNCTIONS
# =========================

def get_first_product_link(page: Page, sku: str) -> str | None:
    """
    Navigates to the search results page for the given SKU and returns
    the absolute URL of the first product found in #items-grid.
    Returns None if no product link is found.
    """
    search_url = SEARCH_URL_TEMPLATE.format(sku=sku)
    page.goto(search_url, wait_until="networkidle", timeout=NAV_TIMEOUT)

    # Make sure the grid itself shows up (or times out quickly if absent).
    try:
        page.wait_for_selector(PRODUCT_GRID_SELECTOR, timeout=ELEMENT_TIMEOUT)
    except PlaywrightTimeoutError:
        return None

    link_locator = page.locator(PRODUCT_LINK_SELECTOR).first

    if link_locator.count() == 0:
        return None

    href = link_locator.get_attribute("href")
    if not href:
        return None

    href = href.strip()
    if href.startswith("/"):
        href = BASE_URL + href

    return href


def extract_description(page: Page, product_url: str) -> str:
    """
    Opens the given product page and extracts the plain-text description
    from div.x-block-description .rte. Returns an empty string if the
    description element is not present or is empty.
    """
    page.goto(product_url, wait_until="networkidle", timeout=NAV_TIMEOUT)

    try:
        page.wait_for_selector(DESCRIPTION_SELECTOR, timeout=ELEMENT_TIMEOUT)
    except PlaywrightTimeoutError:
        return ""

    description_locator = page.locator(DESCRIPTION_SELECTOR).first

    if description_locator.count() == 0:
        return ""

    text = description_locator.inner_text()
    return text if text else ""


def process_product(page: Page, sku: str) -> tuple[str, str]:
    """
    Runs the full pipeline for a single SKU:
      1. Find the first product link on the search page.
      2. Open the product page.
      3. Extract the description.

    Returns a (description, status) tuple.
    """
    try:
        product_link = get_first_product_link(page, sku)

        if not product_link:
            return "", "NO_RESULT"

        description = extract_description(page, product_link)
        return description, "MATCHED"

    except Exception as exc:  # noqa: BLE001 - we want to catch and log everything
        error_message = f"ERROR: {exc}"
        # Print full traceback to console for debugging, but keep the
        # Excel cell short and readable.
        traceback.print_exc()
        return "", error_message


# =========================
# MAIN
# =========================

def load_input_dataframe(path: str) -> pd.DataFrame:
    """
    Loads the input Excel file.

    Some Shopify-exported files split the header across two physical rows
    (e.g. most column names on row 1, but a couple like "Variant SKU" and
    "Variant Inventory Qty" pushed onto row 2). This function detects that
    case and merges the two header rows into one, then returns the data
    starting from row 3. If the file has a normal single-row header, it is
    read normally.
    """
    raw = pd.read_excel(path, header=None, dtype=str)

    row0 = raw.iloc[0]
    row1 = raw.iloc[1]

    # Heuristic: if row1 contains any of our expected columns (or looks like
    # header text rather than data), treat rows 0+1 as a combined header.
    row1_values = {str(v).strip() for v in row1 if pd.notna(v)}

    looks_like_split_header = (
        SKU_COLUMN in row1_values
        or "Variant SKU" in row1_values
        or "Variant Inventory Qty" in row1_values
        or "Part No." in row1_values
    )

    if looks_like_split_header:
        columns = []
        for col_idx in range(raw.shape[1]):
            val0 = row0[col_idx]
            val1 = row1[col_idx]
            val0 = str(val0).strip() if pd.notna(val0) else ""
            val1 = str(val1).strip() if pd.notna(val1) else ""

            # إذا الصف الثاني يحتوي عنوان، استخدمه، وإلا استخدم الأول
            if val1:
                columns.append(val1)
            elif val0:
                columns.append(val0)
            else:
                columns.append(f"Unnamed: {col_idx}")

        df = raw.iloc[2:].reset_index(drop=True)
        df.columns = columns
        return df

    # Normal case: single header row.
    return pd.read_excel(path, dtype={SKU_COLUMN: str})


def main() -> None:
    try:
        df = load_input_dataframe(INPUT_FILE)
    except FileNotFoundError:
        print(f"Input file not found: {INPUT_FILE}")
        sys.exit(1)

    if SKU_COLUMN not in df.columns:
        print(f"Column '{SKU_COLUMN}' not found in {INPUT_FILE}.")
        print(f"Available columns: {list(df.columns)}")
        sys.exit(1)

    # Ensure the new columns exist without disturbing existing ones.
    if DESCRIPTION_COLUMN not in df.columns:
        df[DESCRIPTION_COLUMN] = ""
    if STATUS_COLUMN not in df.columns:
        df[STATUS_COLUMN] = ""

    # Determine which rows to process.
    if ROW_LIMIT is not None:
        row_indices = df.index[:ROW_LIMIT]
    else:
        row_indices = df.index

    total = len(row_indices)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(NAV_TIMEOUT)

        for progress_num, idx in enumerate(row_indices, start=1):
            raw_sku = df.at[idx, SKU_COLUMN]

            # Skip empty/missing SKUs but keep the row untouched otherwise.
            if pd.isna(raw_sku) or str(raw_sku).strip() == "":
                print(f"[{progress_num}/{total}] <empty SKU> - SKIPPED")
                continue

            sku = str(raw_sku).strip()
            print(f"[{progress_num}/{total}] {sku}")

            description, status = process_product(page, sku)

            df.at[idx, DESCRIPTION_COLUMN] = description
            df.at[idx, STATUS_COLUMN] = status

            print(f"    -> {status}")

        browser.close()

    df.to_excel(OUTPUT_FILE, index=False, engine="openpyxl")
    print(f"\nDone. Results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()