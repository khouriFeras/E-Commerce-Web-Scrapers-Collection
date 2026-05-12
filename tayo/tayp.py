import argparse
import os
import re
import json
import hashlib
import urllib.parse
from typing import Dict, List, Tuple, Optional

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


START_URL = "https://toya24.pl/main-eng.html"

SEARCH_INPUT_SELECTOR = ".menu_search__input"  # provided by you

# Heuristics to detect "Search results" pages
SEARCH_RESULTS_TEXTS = [
    "Search results",
    "Results for",
    "No results",
]

# Product-page heuristics:
# - many IdoSell stores use /product-eng-<id>-<slug>.html
PRODUCT_URL_REGEX = re.compile(r"/product-[a-z]{2,3}-\d+-.+\.html", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """Normalize URLs for deduping (strip fragments, normalize query ordering)."""
    if not url:
        return url
    try:
        parsed = urllib.parse.urlsplit(url)
        # drop fragment
        fragmentless = parsed._replace(fragment="")
        # normalize query order
        q = urllib.parse.parse_qsl(fragmentless.query, keep_blank_values=True)
        q_sorted = urllib.parse.urlencode(sorted(q))
        normalized = fragmentless._replace(query=q_sorted)
        return urllib.parse.urlunsplit(normalized)
    except Exception:
        return url


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def is_product_page(url: str, page_text: str) -> bool:
    """Best-effort check: URL pattern or product-ish content."""
    if PRODUCT_URL_REGEX.search(url or ""):
        return True
    # fallback heuristic: product pages usually contain "Symbol"
    # (seen on a product page: Symbol YT-37200) :contentReference[oaicite:0]{index=0}
    if "Symbol" in (page_text or "") and "Add to cart" in (page_text or ""):
        return True
    return False


def looks_like_search_results(page_text: str) -> bool:
    t = (page_text or "").strip()
    if not t:
        return False
    for needle in SEARCH_RESULTS_TEXTS:
        if needle.lower() in t.lower():
            return True
    return False


def extract_description(page) -> str:
    """Try multiple selectors to find product description reliably."""
    selectors = [
        "#projector_longdescription",
        ".projector_longdescription",
        ".projector_description",
        ".product__description",
        ".product-description",
        "section:has-text('Description')",
        "div:has(h2:has-text('Description'))",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                txt = loc.inner_text(timeout=1500).strip()
                if len(txt) >= 20:
                    return clean_text(txt)
        except Exception:
            pass

    # Fallback: grab a large block near "Symbol" area (common on this site) :contentReference[oaicite:1]{index=1}
    try:
        body_txt = page.locator("body").inner_text(timeout=2000)
        return clean_text(body_txt)
    except Exception:
        return ""


def extract_specs(page) -> Dict[str, str]:
    """
    Extract specification/parameters as key/value.
    Primary source: all .dictionary__param.row.mb-3 on the page (each row = param name + value).
    Fallback: tables, dl/dt/dd, then body text heuristics.
    """
    specs: Dict[str, str] = {}

    # 0) Primary: all dictionary__param row mb-3 on the page
    param_row_selectors = [
        ".dictionary__param.row.mb-3",
        ".dictionary__param.mb-3",
        '[class*="dictionary__param"][class*="row"]',
    ]
    for prows in param_row_selectors:
        try:
            rows = page.locator(prows)
            n = rows.count()
            if n == 0:
                continue
            for i in range(n):
                row = rows.nth(i)
                # Each row usually has two parts: label (key) and value
                children = row.locator("> *")
                if children.count() >= 2:
                    k = clean_text(children.nth(0).inner_text(timeout=800))
                    v = clean_text(children.nth(1).inner_text(timeout=800))
                else:
                    # Single block: try split by first colon or first newline
                    full = clean_text(row.inner_text(timeout=800))
                    if ":" in full:
                        parts = full.split(":", 1)
                        k = parts[0].strip()
                        v = parts[1].strip() if len(parts) > 1 else ""
                    else:
                        k = full
                        v = ""
                if k and v and len(k) <= 80:
                    specs.setdefault(k, v)
            if specs:
                return specs
        except Exception:
            pass

    # 1) Table rows: th/td or td/td (page-wide fallback)
    table_selectors = [
        "table.projector_params",
        "table.projector_specification",
        "table.product-params",
        "table",
    ]
    for tsel in table_selectors:
        try:
            tables = page.locator(tsel)
            for i in range(min(tables.count(), 5)):
                table = tables.nth(i)
                rows = table.locator("tr")
                for r in range(rows.count()):
                    row = rows.nth(r)
                    cells = row.locator("th, td")
                    if cells.count() >= 2:
                        k = clean_text(cells.nth(0).inner_text(timeout=800))
                        v = clean_text(cells.nth(1).inner_text(timeout=800))
                        if k and v and len(k) <= 80:
                            specs.setdefault(k, v)
        except Exception:
            pass
        if len(specs) >= 5:
            break

    # 2) Definition lists dt/dd
    if len(specs) < 3:
        try:
            dts = page.locator("dt")
            for i in range(dts.count()):
                dt = dts.nth(i)
                k = clean_text(dt.inner_text(timeout=800))
                dd = dt.locator("xpath=following-sibling::dd[1]")
                if dd.count() > 0:
                    v = clean_text(dd.first.inner_text(timeout=800))
                    if k and v:
                        specs.setdefault(k, v)
        except Exception:
            pass

    # 3) Fallback: pairs like "Brand ... Yato", "Series ...", "Material ..." (seen on product page) :contentReference[oaicite:2]{index=2}
    if len(specs) < 3:
        try:
            body = page.locator("body").inner_text(timeout=2000)
            # crude parse for known labels
            for label in ["Brand", "Series", "Material", "Symbol", "Application", "Lifting height"]:
                m = re.search(rf"\b{re.escape(label)}\b\s*([\s\S]{{0,200}})", body)
                if m:
                    tail = m.group(1).strip().splitlines()
                    # next non-empty line is often the value in text extraction
                    val = ""
                    for line in tail:
                        line = line.strip()
                        if line:
                            val = line
                            break
                    if val:
                        specs.setdefault(label, clean_text(val))
        except Exception:
            pass

    return specs


def extract_fullsize_images(page) -> List[str]:
    """
    Collect image URLs from the photos slider (.photos__nav), full-size only, no duplicates.
    Primary: links (href) and data-zoom-image / data-large / data-full in the slider.
    Fallback: page-wide if slider not found.
    """
    IMG_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(\?|$)", re.IGNORECASE)

    def is_image_url(u: str) -> bool:
        return bool(u and IMG_EXT_RE.search(u))

    def collect_from_slider() -> List[str]:
        """Get full-size image URLs only from .photos__nav slider; no duplicates."""
        js = r"""
        () => {
          const sel = '.photos__nav.d-none.d-md-flex.flex-md-column, .photos__nav, [class*="photos__nav"]';
          const slider = document.querySelector(sel);
          if (!slider) return [];
          const seen = new Set();
          const order = [];
          function add(u) {
            if (!u || !/\.(jpg|jpeg|png|webp)(\?|$)/i.test(u)) return;
            try {
              const abs = new URL(u, location.href).toString();
              if (abs.indexOf('eng_ps_') !== -1) return;
              if (seen.has(abs)) return;
              seen.add(abs);
              order.push(abs);
            } catch (e) {}
          }
          // 1) Links in slider (often full-size)
          slider.querySelectorAll('a[href]').forEach(a => {
            const h = a.getAttribute('href');
            if (h) add(h);
          });
          // 2) Full-size attrs on img (prefer zoom/large/full)
          ['data-zoom-image','data-large','data-full','data-original','data-src','src'].forEach(k => {
            slider.querySelectorAll('img[' + k + ']').forEach(img => {
              add(img.getAttribute(k));
            });
          });
          // 3) Full-size on wrapper elements
          slider.querySelectorAll('[data-zoom-image],[data-large],[data-full]').forEach(el => {
            ['data-zoom-image','data-large','data-full'].forEach(k => {
              const v = el.getAttribute(k);
              if (v) add(v);
            });
          });
          return order;
        }
        """
        try:
            raw = page.evaluate(js)
            return [u for u in (raw or []) if is_image_url(u)]
        except Exception:
            return []

    def collect_page_wide() -> List[str]:
        """Fallback: collect from whole page, prefer full-size attrs."""
        js = r"""
        () => {
          const seen = new Set();
          const order = [];
          function add(u) {
            if (!u || !/\.(jpg|jpeg|png|webp)(\?|$)/i.test(u)) return;
            try {
              const abs = new URL(u, location.href).toString();
              if (abs.indexOf('eng_ps_') !== -1) return;
              if (seen.has(abs)) return;
              seen.add(abs);
              order.push(abs);
            } catch (e) {}
          }
          document.querySelectorAll('a[href]').forEach(a => {
            const h = a.getAttribute('href');
            if (h) add(h);
          });
          ['data-zoom-image','data-large','data-full','data-original','data-src','src'].forEach(k => {
            document.querySelectorAll('img[' + k + ']').forEach(img => add(img.getAttribute(k)));
          });
          document.querySelectorAll('[data-zoom-image],[data-large],[data-full]').forEach(el => {
            ['data-zoom-image','data-large','data-full'].forEach(k => add(el.getAttribute(k)));
          });
          return order;
        }
        """
        try:
            raw = page.evaluate(js)
            return [u for u in (raw or []) if is_image_url(u)]
        except Exception:
            return []

    urls = collect_from_slider()
    if not urls:
        urls = collect_page_wide()

    # Exclude thumbnail/slider versions (eng_ps_); keep only full-size (eng_pl_)
    urls = [u for u in urls if "eng_ps_" not in u]

    # Final dedupe by normalized URL (in case JS missed edge cases)
    dedup: List[str] = []
    seen: set = set()
    for u in urls:
        nu = normalize_url(u)
        if not nu or not is_image_url(nu):
            continue
        if nu in seen:
            continue
        seen.add(nu)
        dedup.append(u)

    return dedup


def clean_text(s: str) -> str:
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def download_images(page, sku: str, image_urls: List[str], out_dir: str) -> List[str]:
    """
    Download images via the browser context to avoid anti-bot blocks.
    Returns list of local file paths written.
    """
    if not image_urls:
        return []

    sku_dir = os.path.join(out_dir, safe_filename(sku))
    os.makedirs(sku_dir, exist_ok=True)

    saved = []
    for idx, url in enumerate(image_urls, start=1):
        try:
            resp = page.request.get(url, timeout=20000)
            if not resp.ok:
                continue
            ct = (resp.headers.get("content-type") or "").lower()
            ext = guess_ext(url, ct)
            fname = f"{idx:02d}_{sha1(url)[:10]}{ext}"
            path = os.path.join(sku_dir, fname)
            with open(path, "wb") as f:
                f.write(resp.body())
            saved.append(path)
        except Exception:
            continue
    return saved


def guess_ext(url: str, content_type: str) -> str:
    m = re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url, re.IGNORECASE)
    if m:
        return "." + m.group(1).lower().replace("jpeg", "jpg")
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\-\.]+", "_", name, flags=re.UNICODE)
    return name[:120] or "item"


def read_input_excel(path: str, sku_col: Optional[str]) -> Tuple[pd.DataFrame, str]:
    df = pd.read_excel(path, dtype=str)
    if df.empty:
        raise ValueError("Excel is empty.")

    if sku_col:
        if sku_col not in df.columns:
            raise ValueError(f"SKU column '{sku_col}' not found. Columns: {list(df.columns)}")
        col = sku_col
    else:
        col = df.columns[0]  # first column by default

    df[col] = df[col].astype(str).str.strip()
    return df, col


def ensure_output_cols(df: pd.DataFrame) -> pd.DataFrame:
    for c in [
        "status",
        "product_url",
        "title",
        "description",
        "specs_json",
        "image_urls",
        "image_count",
        "downloaded_files",
        "error",
    ]:
        if c not in df.columns:
            df[c] = ""
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Input Excel file (xlsx).")
    ap.add_argument("--output", required=True, help="Output Excel file (xlsx).")
    ap.add_argument("--sku-col", default=None, help="Column name containing SKU. Defaults to first column.")
    ap.add_argument("--limit", type=int, default=None, help="Process only first N products (for test run). e.g. --limit 5")
    ap.add_argument("--checkpoint-every", type=int, default=50, help="Save progress every N products (default: 50).")
    ap.add_argument("--headless", action="store_true", help="Run headless (default: headed for debugging).")
    ap.add_argument("--download-images", action="store_true", help="Download full-size images to ./toya24_images/<SKU>/")
    ap.add_argument("--images-dir", default="toya24_images", help="Where to save images if --download-images is set.")
    ap.add_argument("--timeout-ms", type=int, default=20000, help="Default timeout (ms).")
    args = ap.parse_args()

    df, sku_col = read_input_excel(args.input, args.sku_col)
    df = ensure_output_cols(df)

    processed_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        # Go to start page
        page.goto(START_URL, wait_until="domcontentloaded")

        # Make sure search input exists
        try:
            page.wait_for_selector(SEARCH_INPUT_SELECTOR, timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            browser.close()
            raise RuntimeError(f"Search input not found with selector: {SEARCH_INPUT_SELECTOR}")

        for idx, row in df.iterrows():
            sku = (row.get(sku_col) or "").strip()
            if not sku:
                df.at[idx, "status"] = "skipped_empty_sku"
                continue

            if args.limit is not None and processed_count >= args.limit:
                print(f"Reached limit of {args.limit} products. Stopping.")
                break

            processed_count += 1
            if args.limit:
                print(f"Processing product {processed_count}/{args.limit}: {sku}")
            else:
                print(f"Processing product {processed_count}: {sku}")

            try:
                # Always start fresh from home for consistent behavior
                page.goto(START_URL, wait_until="domcontentloaded")
                page.wait_for_selector(SEARCH_INPUT_SELECTOR)

                # Type SKU + Enter
                search = page.locator(SEARCH_INPUT_SELECTOR).first
                search.click()
                search.fill("")  # clear
                search.type(sku, delay=20)
                search.press("Enter")

                # Wait for navigation/content to settle
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=args.timeout_ms)
                except PlaywrightTimeoutError:
                    pass

                # Capture evidence
                url = page.url
                body_text = ""
                try:
                    body_text = page.locator("body").inner_text(timeout=3000)
                except Exception:
                    body_text = ""

                # Decide: product vs not found
                if (not is_product_page(url, body_text)) and looks_like_search_results(body_text):
                    df.at[idx, "status"] = "not_found"
                    df.at[idx, "product_url"] = url
                    df.at[idx, "error"] = ""
                    continue

                # If not clearly product page, still attempt to detect by URL pattern
                if not is_product_page(url, body_text):
                    # Many stores still show search results without the exact phrase.
                    # If there is no meaningful product content, treat as not found.
                    if len(body_text) < 2000:
                        df.at[idx, "status"] = "not_found"
                        df.at[idx, "product_url"] = url
                        df.at[idx, "error"] = "Ambiguous page; treated as not_found."
                        continue

                # Product extraction
                df.at[idx, "status"] = "found"
                df.at[idx, "product_url"] = url

                # Title
                try:
                    title = clean_text(page.locator("h1").first.inner_text(timeout=2000))
                except Exception:
                    title = ""
                df.at[idx, "title"] = title

                # Description
                desc = extract_description(page)
                df.at[idx, "description"] = desc

                # Specs
                specs = extract_specs(page)
                df.at[idx, "specs_json"] = json.dumps(specs, ensure_ascii=False)

                # Images
                imgs = extract_fullsize_images(page)
                df.at[idx, "image_urls"] = "\n".join(imgs)
                df.at[idx, "image_count"] = str(len(imgs))

                # Optional downloads
                if args.download_images and imgs:
                    saved = download_images(page, sku, imgs, args.images_dir)
                    df.at[idx, "downloaded_files"] = "\n".join(saved)

                df.at[idx, "error"] = ""

            except Exception as e:
                df.at[idx, "status"] = "error"
                df.at[idx, "error"] = str(e)

            # Checkpoint: save every N products
            if args.checkpoint_every and processed_count % args.checkpoint_every == 0:
                df.to_excel(args.output, index=False)
                print(f"Checkpoint: saved progress after {processed_count} products -> {args.output}")

        browser.close()

    df.to_excel(args.output, index=False)
    print(f"Done. Wrote: {args.output} ({processed_count} products processed)")


if __name__ == "__main__":
    main()