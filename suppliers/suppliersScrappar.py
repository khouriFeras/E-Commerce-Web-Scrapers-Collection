# Arabi E-mart direct search: read SKUs from Excel → open
#   https://arabiemart.com/<locale>/products/search?keyword=<sku>
# → take the FIRST product result → save its URL.
# Prints fetched links and an HTML snippet in the terminal.
import argparse
import sys
import os
import time

from urllib.parse import quote_plus

import pandas as pd
from playwright.sync_api import sync_playwright

CHECKPOINT_EVERY = 25
DEFAULT_TIMEOUT_MS = 15000

BASE = "https://arabiemart.com"
DEFAULT_LOCALE = "jo-en"

# Product detail pages look like /jo-en/product/<slug>-<id>/<vendor>.
# The singular "/product/" segment excludes "/products/search" and "/category/..".
PRODUCT_LINK_SEL = "a[href*='/product/']"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


def speed_up(page):
    def handler(route):
        if route.request.resource_type in {"image", "media", "font", "stylesheet"}:
            return route.abort()
        return route.continue_()
    page.route("**/*", handler)


def print_page_debug(page):
    """Print og:url (if any) and the first 500 chars of HTML. Always flush."""
    try:
        og = page.locator("meta[property='og:url']").first.get_attribute("content")
    except Exception:
        og = None
    try:
        html = page.content()
    except Exception as e:
        html = f"<html read error: {e}>"
    print(f"    [OG:url] {og or '-'}", flush=True)
    print(f"    [HTML snippet] {(html or '')[:500].replace('\n', ' ')}...", flush=True)


def search_first_product(page, sku, locale=DEFAULT_LOCALE):
    """Search arabiemart.com for `sku` and return the first product result.

    Returns (title, result_url, final_url); all None when there is no result.
    """
    url = f"{BASE}/{locale}/products/search?keyword={quote_plus(sku)}"
    print(f"  [Search] {url}", flush=True)
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        print(f"  [Search Error] {e}", flush=True)
        return (None, None, None)

    # Results render client-side; wait for the first product card to appear.
    try:
        page.wait_for_selector(PRODUCT_LINK_SEL, timeout=DEFAULT_TIMEOUT_MS)
    except Exception:
        print("  [No result]", flush=True)
        return (None, None, None)

    a = page.locator(PRODUCT_LINK_SEL).first
    href = (a.get_attribute("href") or "").strip()
    if not href:
        print("  [No result]", flush=True)
        return (None, None, None)
    if href.startswith("/"):
        href = BASE + href
    title = (a.inner_text() or "").strip()

    # Open the product page to resolve the final URL and dump a debug snippet.
    final_url = href
    try:
        tab = page.context.new_page()
        speed_up(tab)
        tab.set_default_timeout(DEFAULT_TIMEOUT_MS)
        tab.goto(href, wait_until="domcontentloaded")
        final_url = tab.url
        print(f"  [ArabiEmart] {final_url}", flush=True)
        print_page_debug(tab)
        tab.close()
    except Exception as e:
        print(f"  [ArabiEmart Error] {e}", flush=True)

    return (title, href, final_url)


def main():
    ap = argparse.ArgumentParser(description="Arabi E-mart first-product link scraper (direct site search)")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", required=True, help="Output Excel file")
    ap.add_argument("--sku-col", dest="sku_col", default="SKU", help="Column with SKUs (default: SKU)")
    ap.add_argument("--locale", default=DEFAULT_LOCALE, help=f"Site locale segment (default: {DEFAULT_LOCALE})")
    ap.add_argument("--headful", action="store_true", help="Run a visible browser")
    args = ap.parse_args()

    if not os.path.exists(args.inp):
        raise FileNotFoundError(f"Input Excel not found: {args.inp}")
    df = pd.read_excel(args.inp)
    if args.sku_col not in df.columns:
        raise ValueError(f"Column '{args.sku_col}' not found. Available: {list(df.columns)}")
    skus = [str(x).strip() for x in df[args.sku_col].fillna("")]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        speed_up(page)
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        rows = []
        for i, sku in enumerate(skus, 1):
            if not sku or sku.lower() in ("nan", "none"):
                rows.append({
                    "sku": sku,
                    "result_title": "",
                    "result_url": "",
                    "final_url": "",
                    "error": "empty_sku",
                })
                continue
            print(f"[{i}/{len(skus)}] {sku}", flush=True)

            title, result_url, final_url = search_first_product(page, sku, args.locale)

            rows.append({
                "sku": sku,
                "result_title": title or "",
                "result_url": result_url or "",
                "final_url": final_url or "",
                "error": "" if result_url else "no_result",
            })

            if i % CHECKPOINT_EVERY == 0:
                pd.DataFrame(rows).to_excel(args.out, index=False)
                print(f"[checkpoint] saved {len(rows)} rows -> {args.out}", flush=True)

        pd.DataFrame(rows).to_excel(args.out, index=False)
        print(f"Saved {len(rows)} rows -> {args.out}", flush=True)
        browser.close()


# ------------------------- optional self-tests -------------------------

def _format_snippet(html: str) -> str:
    """Helper used by tests to mirror snippet logic."""
    return (html or "")[:500].replace("\n", " ")


def _test_formatting():
    """Ensure snippet formatting is stable and bounded."""
    s = _format_snippet("<div>line1\nline2</div>")
    assert "line1" in s and "line2" in s, "snippet should include content across newlines"
    assert "\n" not in s, "newlines should be replaced with spaces"
    long = _format_snippet("x" * 600)
    assert len(long) == 500, "snippet should be truncated to 500 chars"


def _run_smoke_tests():
    _test_formatting()
    print("Self-tests passed.")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _run_smoke_tests()
    else:
        main()
