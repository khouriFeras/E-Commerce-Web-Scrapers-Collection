"""
Ambro-Sol scraper.

For every Code in Ambro-Sol_Price_List.xlsx:
  1. Search  https://www.ambro-sol.it/en/?s=<CODE>
  2. Open the first matching product page.
  3. Extract:
        - Title (h1)
        - Image URL  (from .woocommerce-product-gallery__wrapper)
        - Description = <p> paragraphs inside the product's .uncode_text_column
                        + key: value lines from table[aria-label="Product Details"]

Results are written incrementally to Ambro-Sol_Scraped.xlsx.

Run:
    python ambro_scraper.py
Optional:
    python ambro_scraper.py --limit 10      # only scrape first 10 rows (for testing)
    python ambro_scraper.py --delay 1.5     # delay between requests in seconds
"""
import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent
INPUT_XLSX = SCRIPT_DIR / "Ambro-Sol_Price_List.xlsx"
OUTPUT_XLSX = SCRIPT_DIR / "Ambro-Sol_Scraped.xlsx"

SEARCH_URL = "https://www.ambro-sol.it/en/?s="

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

OUTPUT_COLUMNS = [
    "Code",
    "DESCRIPTION",
    "Price JOD",
    "Page",
    "Product URL",
    "Title",
    "Image URL",
    "Scraped Description",
    "Status",
]


def clean(text) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def find_first_product_url(search_soup: BeautifulSoup) -> str:
    """Return the first unique /product/ (or /prodotto/) link on a search page."""
    for a in search_soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if "/product/" in href or "/prodotto/" in href:
            return href
    return ""


def extract_image_url(product_soup: BeautifulSoup) -> str:
    gallery = product_soup.select_one(".woocommerce-product-gallery__wrapper")
    if not gallery:
        return ""

    a = gallery.select_one("a[href]")
    if a and a.get("href"):
        return a["href"].strip()

    img = gallery.select_one("img")
    if img:
        for attr in ("data-large_image", "data-src", "src"):
            val = img.get(attr)
            if val:
                return val.strip()
    return ""


def extract_description(product_soup: BeautifulSoup) -> str:
    """Combine product <p> paragraphs and the Product Details table."""
    parts: list[str] = []

    product_container = (
        product_soup.select_one("div.post-body")
        or product_soup.select_one("div[id^='product-']")
    )

    if product_container:
        for p in product_container.select(".uncode_text_column p"):
            txt = clean(p.get_text(" ", strip=True))
            if txt:
                parts.append(txt)
    else:
        for p in product_soup.select(".uncode_text_column p"):
            if p.find_all_next("table", attrs={"aria-label": "Product Details"}):
                txt = clean(p.get_text(" ", strip=True))
                if txt:
                    parts.append(txt)

    details_table = product_soup.find("table", attrs={"aria-label": "Product Details"})
    if details_table:
        details: list[str] = []
        for tr in details_table.select("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.select("th,td")]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                details.append(f"{cells[0]}: {cells[-1]}")
            elif len(cells) == 1:
                details.append(cells[0])
        if details:
            parts.append("\n".join(details))

    return "\n\n".join(parts).strip()


def _search_for(session: requests.Session, term: str, timeout: int) -> tuple[str, str | None]:
    """Return (product_url, error). On success error is None; on failure url is ''."""
    try:
        resp = session.get(SEARCH_URL + quote_plus(term), timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return "", f"search_error:{type(e).__name__}"
    soup = BeautifulSoup(resp.text, "html.parser")
    return find_first_product_url(soup), None


def _code_variants(code: str) -> list[str]:
    """Produce fallback search terms for tricky codes."""
    variants = [code]
    # 'S152/ESP' -> also try 'S152'
    if "/" in code:
        base = code.split("/", 1)[0].strip()
        if base and base not in variants:
            variants.append(base)
    # Remove trailing numeric sub-suffix: 'V400DIAM.6' -> 'V400DIAM'
    m = re.match(r"^(.+?)\.[0-9]+$", code)
    if m and m.group(1) not in variants:
        variants.append(m.group(1))
    return variants


def scrape_code(session: requests.Session, code: str, timeout: int = 30) -> dict:
    out = {
        "Product URL": "",
        "Title": "",
        "Image URL": "",
        "Scraped Description": "",
        "Status": "",
    }

    product_url = ""
    last_error = None
    matched_variant = code
    for variant in _code_variants(code):
        url, err = _search_for(session, variant, timeout)
        if err:
            last_error = err
            continue
        if url:
            product_url = url
            matched_variant = variant
            break

    if not product_url:
        out["Status"] = last_error or "not_found"
        return out
    out["Product URL"] = product_url
    if matched_variant != code:
        out["Status"] = f"ok_via:{matched_variant}"

    try:
        resp = session.get(product_url, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        out["Status"] = f"product_error:{type(e).__name__}"
        return out

    psoup = BeautifulSoup(resp.text, "html.parser")
    h1 = psoup.select_one("h1")
    out["Title"] = clean(h1.get_text(" ", strip=True)) if h1 else ""
    out["Image URL"] = extract_image_url(psoup)
    out["Scraped Description"] = extract_description(psoup)
    if not out["Status"]:
        out["Status"] = "ok"
    return out


def main():
    parser = argparse.ArgumentParser(description="Scrape Ambro-Sol by product code")
    parser.add_argument("--limit", type=int, default=0, help="Only scrape first N rows")
    parser.add_argument("--delay", type=float, default=0.8, help="Seconds between requests")
    parser.add_argument("--input", default=str(INPUT_XLSX))
    parser.add_argument("--output", default=str(OUTPUT_XLSX))
    parser.add_argument("--save-every", type=int, default=20)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    df = pd.read_excel(input_path)
    if args.limit > 0:
        df = df.head(args.limit).copy()

    for col in ("Product URL", "Title", "Image URL", "Scraped Description", "Status"):
        if col not in df.columns:
            df[col] = ""

    print(f"Loaded {len(df)} rows from {input_path.name}")
    print(f"Output will be written to {output_path.name}")

    session = requests.Session()
    session.headers.update(HEADERS)

    total = len(df)
    ok = notfound = errors = 0

    for i, (idx, row) in enumerate(df.iterrows(), start=1):
        code = clean(row.get("Code", ""))
        if not code:
            df.at[idx, "Status"] = "empty_code"
            continue

        res = scrape_code(session, code)
        for k, v in res.items():
            df.at[idx, k] = v

        status = res["Status"]
        if status == "ok" or status.startswith("ok_via:"):
            ok += 1
        elif status == "not_found":
            notfound += 1
        else:
            errors += 1

        title_preview = (res["Title"] or "")[:55]
        print(f"[{i}/{total}] {code:<14} {status:<14} {title_preview}")

        if i % args.save_every == 0:
            df[OUTPUT_COLUMNS].to_excel(output_path, index=False)
            print(f"   checkpoint saved ({i} rows)")

        time.sleep(args.delay)

    df[OUTPUT_COLUMNS].to_excel(output_path, index=False)

    print("\n----- Summary -----")
    print(f"  ok        : {ok}")
    print(f"  not_found : {notfound}")
    print(f"  errors    : {errors}")
    print(f"  total     : {total}")
    print(f"  saved to  : {output_path}")


if __name__ == "__main__":
    main()
