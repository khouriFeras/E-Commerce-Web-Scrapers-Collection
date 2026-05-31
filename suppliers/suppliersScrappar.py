# Google approach: read product names from Excel → search "<name> arabi emart" → take first result → save
# Prints fetched links and HTML snippet in the terminal
import sys
import os
import re
import time
from urllib.parse import urlparse, parse_qs, quote_plus

import pandas as pd
from playwright.sync_api import sync_playwright

INPUT_EXCEL = "Arabi E-mart - New Suppliers.xlsx"
SHEET_NAME = 0
NAME_COL = "Sellers"
OUTPUT_XLSX = "product_first_links.xlsx"
CHECKPOINT_EVERY = 25
DEFAULT_TIMEOUT_MS = 15000


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


def accept_google_consent(page):
    for sel in [
        "button:has-text('I agree')",
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
        "button:has-text(' موافق ')",
        "button:has-text('أوافق')",
    ]:
        try:
            page.locator(sel).first.click(timeout=1200)
            break
        except Exception:
            pass


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
    print(f"    [OG:url] {og or '—'}", flush=True)
    # FIX: properly terminated f-string on a single line
    print(f"    [HTML snippet] {(html or '')[:500].replace('\n', ' ')}...", flush=True)


def google_first_result(page, query):
    q = f"{query} arabi emart"
    page.goto(f"https://www.google.com/search?hl=en&pws=0&q={quote_plus(q)}", wait_until="domcontentloaded")
    accept_google_consent(page)
    for a in page.locator("#search a:has(h3)").all()[:6]:
        href = (a.get_attribute("href") or "").strip()
        if not href or any(b in href for b in ["google.", "webcache", "translate.google"]):
            continue
        if href.startswith("/url?"):
            href = parse_qs(urlparse(href).query).get("q", [href])[0]
        try:
            tab = page.context.new_page()
            speed_up(tab)
            tab.set_default_timeout(DEFAULT_TIMEOUT_MS)
            tab.goto(href, wait_until="domcontentloaded")
            final_url = tab.url
            print(f"  [Google] {final_url}", flush=True)
            print_page_debug(tab)
            tab.close()
        except Exception as e:
            print(f"  [Google Error] {e}")
            final_url = href
        return (a.inner_text() or "", href, final_url)
    return (None, None, None)


def ddg_first_result(page, query):
    q = f"{query} arabi emart"
    page.goto(f"https://duckduckgo.com/?q={quote_plus(q)}&kl=us-en", wait_until="domcontentloaded")
    candidates = page.locator("a[data-testid='result-title-a']")
    if candidates.count() == 0:
        candidates = page.locator("a.result__a")
    for a in candidates.all()[:6]:
        href = (a.get_attribute("href") or "").strip()
        if not href:
            continue
        try:
            tab = page.context.new_page()
            speed_up(tab)
            tab.set_default_timeout(DEFAULT_TIMEOUT_MS)
            tab.goto(href, wait_until="domcontentloaded")
            final_url = tab.url
            print(f"  [DuckDuckGo] {final_url}", flush=True)
            print_page_debug(tab)
            tab.close()
        except Exception as e:
            print(f"  [DuckDuckGo Error] {e}")
            final_url = href
        return (a.inner_text() or "", href, final_url)
    return (None, None, None)


def main():
    if not os.path.exists(INPUT_EXCEL):
        raise FileNotFoundError(f"Input Excel not found: {INPUT_EXCEL}")
    df = pd.read_excel(INPUT_EXCEL, sheet_name=SHEET_NAME)
    if NAME_COL not in df.columns:
        raise ValueError(f"Column '{NAME_COL}' not found in {INPUT_EXCEL}")
    names = [str(x).strip() for x in df[NAME_COL].fillna("")]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        speed_up(page)
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        rows = []
        for i, name in enumerate(names, 1):
            if not name:
                rows.append({
                    "product_name": name,
                    "engine": "",
                    "result_title": "",
                    "result_url": "",
                    "final_url": "",
                    "error": "empty_name",
                })
                continue
            print(f"[{i}/{len(names)}] {name}", flush=True)

            title, result_url, final_url = google_first_result(page, name)
            engine = "google" if final_url else ""
            if not final_url:
                title, result_url, final_url = ddg_first_result(page, name)
                engine = "duckduckgo" if final_url else ""

            rows.append({
                "product_name": name,
                "engine": engine,
                "result_title": title or "",
                "result_url": result_url or "",
                "final_url": final_url or "",
                "error": "" if final_url else "no_result",
            })

            if i % CHECKPOINT_EVERY == 0:
                pd.DataFrame(rows).to_excel(OUTPUT_XLSX, index=False)
                print(f"[checkpoint] saved {len(rows)} rows → {OUTPUT_XLSX}", flush=True)

        pd.DataFrame(rows).to_excel(OUTPUT_XLSX, index=False)
        print(f"Saved {len(rows)} rows → {OUTPUT_XLSX}", flush=True)
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