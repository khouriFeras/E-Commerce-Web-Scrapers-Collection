#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup


BASE = "https://www.bashiticentral.com"
SEARCH_URL_TEMPLATE = BASE + "/en/item-by-search?c=1&s={sku}"
PRODUCT_PATH_FRAGMENT = "/en/single-product/"


@dataclass(frozen=True)
class ScrapeResult:
    SKU: str
    title: str
    Description: str
    imgs: str
    url: str
    Status: str


def _clean_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _strip_description_prefix(text: str) -> str:
    text = _clean_ws(text)
    if not text:
        return ""
    # Common observed formats:
    # - "Description Voltage: ... "
    # - "Description: Voltage: ..."
    return re.sub(r"^Description\s*:?\s*", "", text, flags=re.IGNORECASE).strip()


def _is_probably_product_image(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    if any(x in u for x in ["/assets/logo", "/image/brand/", "logo"]):
        return False
    if "/photos/" in u:
        return True
    return bool(re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", u))


def _dedupe_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for v in values:
        v = (v or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _get(session: requests.Session, url: str, timeout: float = 30.0) -> str:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def discover_product_url_from_search_html(search_url: str, html: str, sku: Optional[str] = None) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href")
        if not href:
            continue
        if PRODUCT_PATH_FRAGMENT in href:
            links.append(href)
    if not links:
        return None

    abs_links = [urljoin(search_url, h) for h in links]
    if sku:
        sku = sku.strip()
        exact = f"{BASE}{PRODUCT_PATH_FRAGMENT}{sku}"
        for u in abs_links:
            if u.rstrip("/") == exact.rstrip("/"):
                return u

    return abs_links[0]


def parse_product_page(product_url: str, html: str) -> Tuple[str, str, List[str]]:
    soup = BeautifulSoup(html, "lxml")

    # Title: there are two `h1.product-title`, one is "Item Number: <SKU>", the other is product name.
    title = ""
    for h in soup.select("div.single-product-content h1.product-title"):
        t = _clean_ws(h.get_text(" ", strip=True))
        if not t:
            continue
        if t.lower().startswith("item number"):
            continue
        title = t
        break

    # Description: site sometimes renders it as:
    # - <h3>Description Voltage: ...</h3>
    # - <h3>Voltage: ...</h3>
    # - <h3>Description</h3> then <h1 class="product-title">Voltage: ... <br> ...</h1>
    # - <h3>The length of temple: ...</h3>
    description = ""
    content = soup.select_one("div.single-product-content")
    if content:
        # First: explicit "Description" label then take the next meaningful text node.
        label = content.find(string=lambda s: isinstance(s, str) and s.strip().lower() == "description")
        if label and getattr(label, "parent", None):
            for nxt in label.parent.find_all_next(["h1", "h3", "h4", "p", "div"], limit=12):
                txt = _clean_ws(nxt.get_text(" ", strip=True))
                if not txt:
                    continue
                low = txt.lower()
                if low == "description":
                    continue
                if low.startswith("all features") or low.startswith("related products"):
                    break
                # Avoid grabbing the product title again or the item number header.
                if title and txt == title:
                    continue
                if low.startswith("item number"):
                    continue
                description = _strip_description_prefix(txt)
                if len(description) >= 20:
                    break

        # Preferred: find the first heading after the price block.
        price_div = content.select_one("div.single-product-price")
        if price_div:
            for sib in price_div.find_all_next(["h1", "h3", "h4", "p", "div"], limit=12):
                txt = _clean_ws(sib.get_text(" ", strip=True))
                if not txt:
                    continue
                if txt.strip().lower() == "description":
                    continue
                if txt.lower().startswith("all features") or txt.lower().startswith("related products"):
                    break
                description = _strip_description_prefix(txt)
                if len(description) >= 20:
                    break

        # Fallback: first non-trivial h3/h4 inside content (excluding "All features")
        if not description:
            for node in content.select("h1.product-title, h3, h4, p, div"):
                txt = _clean_ws(node.get_text(" ", strip=True))
                if not txt:
                    continue
                if txt.lower().startswith("all features") or txt.lower().startswith("related products"):
                    break
                if txt.strip().lower() == "description":
                    continue
                txt = _strip_description_prefix(txt)
                if len(txt) >= 20 and (not title or txt != title):
                    description = txt
                    break

    # Images: prefer active tab pane gallery, then fallback to all images in tab content.
    img_candidates: List[str] = []
    active_imgs = soup.select("div.single-product-tab-content .tab-pane.active img")
    if active_imgs:
        for img in active_imgs:
            src = img.get("src") or img.get("data-src")
            if src:
                img_candidates.append(urljoin(product_url, src))
    else:
        for img in soup.select("div.single-product-tab-content img"):
            src = img.get("src") or img.get("data-src")
            if src:
                img_candidates.append(urljoin(product_url, src))

    img_candidates = [u for u in img_candidates if _is_probably_product_image(u)]
    img_urls = _dedupe_keep_order(img_candidates)

    # Prefer /Photos/ images first (main product photos)
    photos = [u for u in img_urls if "/Photos/" in u or "/photos/" in u]
    non_photos = [u for u in img_urls if u not in photos]
    img_urls = photos + non_photos

    return title, description, img_urls


def scrape_sku(session: requests.Session, sku: str, pause: float = 0.5) -> ScrapeResult:
    sku = (sku or "").strip()
    search_url = SEARCH_URL_TEMPLATE.format(sku=quote_plus(sku))

    try:
        search_html = _get(session, search_url)
    except Exception:
        return ScrapeResult(SKU=sku, title="", Description="", imgs="", url=search_url, Status="SEARCH_ERROR")

    product_url = discover_product_url_from_search_html(search_url, search_html, sku=sku)
    if not product_url:
        return ScrapeResult(SKU=sku, title="", Description="", imgs="", url=search_url, Status="NOT_FOUND")

    time.sleep(pause)
    try:
        product_html = _get(session, product_url)
    except Exception:
        return ScrapeResult(SKU=sku, title="", Description="", imgs="", url=product_url, Status="PRODUCT_ERROR")

    title, desc, images = parse_product_page(product_url, product_html)
    imgs = ";".join(images) if images else ""
    status = "FOUND" if (title or desc or imgs) else "EMPTY"

    return ScrapeResult(SKU=sku, title=title, Description=desc, imgs=imgs, url=product_url, Status=status)

def _build_driver(headless: bool) -> Any:
    # Local import so requests-mode doesn't require selenium at runtime.
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    if headless:
        # Keep compatibility with newer Chromes
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver


def scrape_sku_browser(driver: Any, sku: str, pause: float = 0.8) -> ScrapeResult:
    """
    Visible-browser mode (for debugging):
    - Opens search URL so user can see results.
    - If a product link exists, opens product page and parses from page_source.
    """
    sku = (sku or "").strip()
    search_url = SEARCH_URL_TEMPLATE.format(sku=quote_plus(sku))

    try:
        driver.get(search_url)
        time.sleep(pause)
        search_html = driver.page_source or ""
    except Exception:
        return ScrapeResult(SKU=sku, title="", Description="", imgs="", url=search_url, Status="SEARCH_ERROR")

    product_url = discover_product_url_from_search_html(search_url, search_html, sku=sku)
    if not product_url:
        return ScrapeResult(SKU=sku, title="", Description="", imgs="", url=search_url, Status="NOT_FOUND")

    try:
        driver.get(product_url)
        time.sleep(pause)
        product_html = driver.page_source or ""
    except Exception:
        return ScrapeResult(SKU=sku, title="", Description="", imgs="", url=product_url, Status="PRODUCT_ERROR")

    title, desc, images = parse_product_page(product_url, product_html)
    imgs = ";".join(images) if images else ""
    status = "FOUND" if (title or desc or imgs) else "EMPTY"
    return ScrapeResult(SKU=sku, title=title, Description=desc, imgs=imgs, url=product_url, Status=status)


def _sheet_name_or_index(value: str) -> Any:
    """Argparse type: 0-based sheet index or sheet name."""
    try:
        return int(value)
    except ValueError:
        return value


def read_skus_from_excel(path: Path, sheet: Any, sku_col: str) -> List[str]:
    df = pd.read_excel(path, sheet_name=sheet)
    if sku_col not in df.columns:
        raise ValueError(f"Column '{sku_col}' not found. Available columns: {list(df.columns)}")
    skus = (
        df[sku_col]
        .dropna()
        .astype(str)
        .map(lambda s: s.strip())
        .loc[lambda s: (s != "") & (s.str.lower() != "nan")]
        .tolist()
    )
    return skus


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bashiti Central scraper (reads SKUs from Excel and outputs SKU;title;Description;imgs;url)."
    )
    parser.add_argument(
        "--in",
        dest="input_file",
        default=str(Path("bashiticentral") / "injco.xls"),
        help="Input Excel file path (default: bashiticentral/injco.xls)",
    )
    parser.add_argument(
        "--append-to-input",
        action="store_true",
        help=(
            "Merge scraped columns back into the original input sheet (by SKU). "
            "Keeps all original columns and adds: scraped_title, scraped_description, scraped_imgs, scraped_url, scraped_status."
        ),
    )
    parser.add_argument(
        "--inplace",
        action="store_true",
        help=(
            "When used with --append-to-input and the input is .xlsx, overwrite the input file. "
            "Ignored for .xls inputs (will write a new *_updated.xlsx instead)."
        ),
    )
    parser.add_argument(
        "--out",
        dest="output_file",
        default=str(Path("bashiticentral") / "bashiticentral_scraped.xlsx"),
        help="Output .xlsx file path (default: bashiticentral/bashiticentral_scraped.xlsx)",
    )
    parser.add_argument(
        "--sheet",
        default=0,
        type=_sheet_name_or_index,
        help="Excel sheet: 0-based index (e.g. 0) or name (default: 0 = first sheet)",
    )
    parser.add_argument(
        "--sku-col",
        default="SKU",
        help="Excel column name containing SKUs (default: SKU)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.5,
        help="Pause between requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Open a visible Chrome window while scraping (slower, useful for debugging).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Use Selenium but keep Chrome headless (ignored unless --headful/--headless is used).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of SKUs processed (0 = all)",
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    in_df = pd.read_excel(input_path, sheet_name=args.sheet)
    if args.sku_col not in in_df.columns:
        raise ValueError(f"Column '{args.sku_col}' not found. Available columns: {list(in_df.columns)}")

    skus = (
        in_df[args.sku_col]
        .dropna()
        .astype(str)
        .map(lambda s: s.strip())
        .loc[lambda s: (s != "") & (s.str.lower() != "nan")]
        .tolist()
    )
    if args.limit and args.limit > 0:
        skus = skus[: args.limit]

    use_browser = bool(args.headful or args.headless)
    driver = None
    session = None

    if use_browser:
        driver = _build_driver(headless=(not args.headful))
    else:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                )
            }
        )

    results: List[Dict[str, str]] = []
    total = len(skus)
    try:
        for i, sku in enumerate(skus, 1):
            print(f"[{i}/{total}] {sku}")
            if use_browser:
                r = scrape_sku_browser(driver, sku, pause=max(0.6, args.pause))
            else:
                r = scrape_sku(session, sku, pause=args.pause)

            results.append(
                {
                    "SKU": r.SKU,
                    "title": r.title,
                    "Description": r.Description,
                    "imgs": r.imgs,
                    "url": r.url,
                    "Status": r.Status,
                }
            )
            if i < total:
                time.sleep(args.pause)
    finally:
        if driver is not None:
            driver.quit()

    out_df = pd.DataFrame(results, columns=["SKU", "title", "Description", "imgs", "url", "Status"])

    if args.append_to_input:
        scraped_df = out_df.rename(
            columns={
                "title": "scraped_title",
                "Description": "scraped_description",
                "imgs": "scraped_imgs",
                "url": "scraped_url",
                "Status": "scraped_status",
            }
        )

        # Normalize join keys as strings (avoids 123 vs "123" mismatches).
        in_df_keyed = in_df.copy()
        in_df_keyed[args.sku_col] = in_df_keyed[args.sku_col].astype(str).map(lambda s: s.strip())
        scraped_df["SKU"] = scraped_df["SKU"].astype(str).map(lambda s: s.strip())

        merged = in_df_keyed.merge(
            scraped_df,
            how="left",
            left_on=args.sku_col,
            right_on="SKU",
        )
        if "SKU" in merged.columns and args.sku_col != "SKU":
            merged = merged.drop(columns=["SKU"])

        # Decide output path for append mode.
        if args.inplace and input_path.suffix.lower() == ".xlsx":
            out_path = input_path
        else:
            out_path = input_path.with_suffix("").with_name(input_path.stem + "_updated").with_suffix(".xlsx")

        merged.to_excel(out_path, index=False)
    else:
        out_path = Path(args.output_file)
        if not out_path.is_absolute():
            out_path = Path.cwd() / out_path
        out_df.to_excel(out_path, index=False)

    found = (out_df["Status"] == "FOUND").sum()
    not_found = (out_df["Status"] == "NOT_FOUND").sum()
    print()
    print("Done")
    print(f"Total: {len(out_df)} | Found: {found} | NotFound: {not_found}")
    print(f"Saved: {out_path.resolve()}")


if __name__ == "__main__":
    main()

