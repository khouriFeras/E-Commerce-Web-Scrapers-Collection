# pip install playwright pandas openpyxl
# python -m playwright install

import re, time, unicodedata, os
from urllib.parse import urlencode, urlparse, parse_qs
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

SEARCH_URL = "https://arabiemart.com/search"
BASE = "https://arabiemart.com"
OUTPUT_XLSX = "sellers_with_links.xlsx"
CHECKPOINT_EVERY = 25
MAX_SCROLL_STEPS = 200
SCROLL_PAUSE = 0.05

def build_products_url(slug: str) -> str:
    params = {
        "keyword": "", "category": "", "wishlist": "",
        "sort_by": "relevance", "price_min": "", "price_max": "",
        "organization_slugs[]": [slug],
    }
    return f"{BASE}/search?{urlencode(params, doseq=True)}"

def normalize_key(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("أ","ا").replace("إ","ا").replace("آ","ا").replace("ى","ي").replace("ئ","ي").replace("ؤ","و").replace("ة","ه")
    return re.sub(r"\s+", " ", s.strip().lower())

def open_dropdown(page):
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    opened = False
    for trig in [
        page.get_by_text("All sellers", exact=True),
        page.get_by_text("Sellers"),
        page.get_by_text("Toggle"),
        page.get_by_text("كل البائعين"),
        page.get_by_role("button", name=re.compile(r"sellers?|البائع", re.I)),
    ]:
        try:
            trig.click()
            opened = True
            break
        except Exception:
            pass
    if not opened:
        raise RuntimeError("Could not open Sellers dropdown.")
    panel = page.locator("[role='listbox'], ul[role='listbox'], div[role='listbox'], [id*='listbox-options']").first
    panel.wait_for(state="visible", timeout=15000)
    return panel

def collect_all_names(context):
    """Scroll the dropdown to bottom and collect all visible names (unique)."""
    page = context.new_page()
    page.set_default_timeout(15000)
    panel = open_dropdown(page)

    # Scroll until bottom or max steps
    last_height = -1
    for _ in range(MAX_SCROLL_STEPS):
        at_bottom = panel.evaluate("el => (el.scrollTop + el.clientHeight) >= (el.scrollHeight - 3)")
        if at_bottom:
            break
        panel.evaluate("el => el.scrollBy(0, el.clientHeight)")
        time.sleep(SCROLL_PAUSE)
        h = panel.evaluate("el => el.scrollHeight")
        if h == last_height:  # no growth
            break
        last_height = h

    # Grab candidates
    cand = panel.locator("[role='option'], li.block.capitalize, li, a, button, div[role='option']")
    names, seen = [], set()
    for el in cand.all():
        if not el.is_visible():
            continue
        txt = (el.inner_text() or "").strip()
        if not txt:
            continue
        low = txt.lower()
        if low in {"all sellers", "toggle"} or "we didn’t found a seller" in low:
            continue
        key = normalize_key(txt)
        if key in seen:
            continue
        seen.add(key)
        names.append(txt)
    page.close()
    return names

def scroll_item_into_view(panel, name):
    """Scroll inside the panel until an element with this text is visible (or give up)."""
    # Try exact role=option first
    target = panel.get_by_role("option", name=name)
    for _ in range(MAX_SCROLL_STEPS):
        if target.count() and target.first.is_visible():
            return target.first
        panel.evaluate("el => el.scrollBy(0, Math.ceil(el.clientHeight/2))")
        time.sleep(SCROLL_PAUSE)

    # Fallback: generic text search
    target = panel.get_by_text(name, exact=True)
    for _ in range(MAX_SCROLL_STEPS):
        if target.count() and target.first.is_visible():
            return target.first
        panel.evaluate("el => el.scrollBy(0, Math.ceil(el.clientHeight/2))")
        time.sleep(SCROLL_PAUSE)

    return None

def resolve_slug_for_name(context, name):
    """Open new page, open dropdown, scroll to item, click, read slug from URL."""
    page = context.new_page()
    page.set_default_timeout(15000)
    try:
        panel = open_dropdown(page)
        el = scroll_item_into_view(panel, name)
        if not el:
            return None, "not_found_in_dropdown"

        before = page.url
        try:
            el.click(timeout=8000)
        except Exception as e:
            # try force click if needed
            try:
                el.click(timeout=8000, force=True)
            except Exception:
                return None, f"click_error:{type(e).__name__}"

        # Wait for URL to include organization_slugs
        try:
            page.wait_for_url(re.compile(r"(organization_slugs%5B%5D=|organization_slugs\[\]=)"), timeout=8000)
        except PwTimeout:
            # poll a bit
            for _ in range(24):
                if "organization_slugs%5B%5D=" in page.url or "organization_slugs[]=" in page.url:
                    break
                time.sleep(0.25)

        after = page.url
        if after == before:
            return None, "url_not_changed"

        qs = parse_qs(urlparse(after).query)
        vals = qs.get("organization_slugs[]") or qs.get("organization_slugs%5B%5D")
        if vals and vals[0]:
            return vals[0], ""
        return None, "slug_missing_in_url"
    finally:
        page.close()

def load_checkpoint():
    if os.path.exists(OUTPUT_XLSX):
        try:
            df = pd.read_excel(OUTPUT_XLSX)
            done = {normalize_key(n): True for n in df["display_text"].astype(str)}
            rows = df.to_dict("records")
            return done, rows
        except Exception:
            pass
    return {}, []

def save_checkpoint(rows):
    pd.DataFrame(rows).to_excel(OUTPUT_XLSX, index=False)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        # 1) Collect all names
        names = collect_all_names(context)
        print(f"Collected {len(names)} seller names from dropdown")

        # 2) Load checkpoint
        done_keys, rows = load_checkpoint()

        # 3) Resolve each name to slug (with retries)
        processed = 0
        for i, name in enumerate(names, 1):
            key = normalize_key(name)
            if key in done_keys:
                continue

            slug, err = resolve_slug_for_name(context, name)
            if not slug and not err:
                err = "unknown_error"

            products_url = build_products_url(slug) if slug else ""
            rows.append({
                "display_text": name,
                "slug": slug or "",
                "products_url": products_url,
                "error": err,
            })
            done_keys[key] = True
            processed += 1

            # checkpoint
            if processed % CHECKPOINT_EVERY == 0:
                save_checkpoint(rows)
                print(f"[checkpoint] saved {len(rows)} rows")

        # final save
        save_checkpoint(rows)
        print(f"Saved {len(rows)} rows to {OUTPUT_XLSX}")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
