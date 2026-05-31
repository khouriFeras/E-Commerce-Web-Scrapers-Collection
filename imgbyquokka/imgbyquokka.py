
import argparse, sys
from typing import List, Dict
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import pandas as pd

BASE = "https://www.byquokka.com"
SEARCH_URL_TMPL = (
    "https://www.byquokka.com/en/buscar"
    "?controller=search&orderby=position&orderway=desc"
    "&search_query={sku}&submit_search="
)

# -------------------- HTTP helpers --------------------
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/123.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.7",
        "Referer": BASE,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return s

def fetch_html(s: requests.Session, url: str) -> str:
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return r.text

# -------------------- URL utils --------------------
def _abs(u: str) -> str:
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if urlparse(u).netloc:
        return u
    return urljoin(BASE, u)

def _is_placeholder(u: str) -> bool:
    low = u.lower()
    return any(t in low for t in ["placeholder", "noimage", "no-image", "question", "default", "dummy", "svg"])

# -------------------- Search page parsing --------------------
def extract_product_links_from_search(html: str) -> List[str]:
    """
    From the search results page, return product page links in order.
    HTML structure (example):
      ul#product_list.product_list.grid.row
        li.ajax_block_product ...
          a.product_img_link[href]  <-- product page
    """
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.select_one("ul#product_list.product_list.grid.row") \
           or soup.select_one("#product_list, .product_list, .products, .product-list") \
           or soup
    links = []
    # Prefer product image links; fallback to product-name anchors
    for a in grid.select("li.ajax_block_product a.product_img_link[href], li.ajax_block_product a.product-name[href]"):
        href = a.get("href")
        if href:
            links.append(_abs(href.strip()))
    # de-dup while preserving order
    seen, out = set(), []
    for u in links:
        if u not in seen:
            out.append(u); seen.add(u)
    return out

# -------------------- Product page parsing --------------------
TARGET_CONTAINERS = [
    "div.col-xs-12.col-md-3.col-sm-3.foto-principal",
    "div.pb-center-column.col-xs-12.col-md-4.col-sm-4",
]

def _collect_imgs_from_container(root: BeautifulSoup) -> List[str]:
    urls = []
    # any <img> inside the given container(s)
    for img in root.select("img"):
        for attr in ("data-zoom-image", "data-large", "data-image-large-src", "data-src", "srcset", "src"):
            v = img.get(attr)
            if not v:
                continue
            if attr == "srcset":
                v = v.split(",")[0].strip().split(" ")[0]
            u = _abs(v.strip())
            if u and not _is_placeholder(u):
                urls.append(u)
                break
    return urls

def extract_images_from_product_page(html: str) -> List[str]:
    """
    On a product page, extract images from:
      - div.col-xs-12.col-md-3.col-sm-3.foto-principal
      - div.pb-center-column.col-xs-12.col-md-4.col-sm-4
    If those are missing, do a safe fallback to any obvious product image block (#image-block, #bigpic).
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: List[str] = []

    # 1) Strict containers you requested
    for sel in TARGET_CONTAINERS:
        cont = soup.select_one(sel)
        if cont:
            urls.extend(_collect_imgs_from_container(cont))

    # 2) Fallbacks commonly used on PrestaShop product pages
    if not urls:
        # #image-block or #views_block
        for sel in ["#image-block", "#views_block", ".pb-center-column", ".primary_block", ".product-container"]:
            cont = soup.select_one(sel)
            if cont:
                urls.extend(_collect_imgs_from_container(cont))

    # 3) Final de-dup and return
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            out.append(u); seen.add(u)
    return out

# -------------------- Workflow: Search -> click each product -> grab images --------------------
def scrape_images_for_sku(session: requests.Session, sku: str, follow_all_products: bool = True, max_products: int = 50) -> List[str]:
    """
    For a SKU:
      1) Open search URL
      2) Collect product links
      3) Visit each product page (up to max_products) and scrape images from the two containers
      4) Return unique URLs
    """
    search_url = SEARCH_URL_TMPL.format(sku=str(sku).strip())
    search_html = fetch_html(session, search_url)

    product_links = extract_product_links_from_search(search_html)
    if not product_links:
        return []

    if not follow_all_products:
        product_links = product_links[:1]
    else:
        product_links = product_links[:max_products]

    all_urls: List[str] = []
    seen = set()
    for link in product_links:
        try:
            prod_html = fetch_html(session, link)
            urls = extract_images_from_product_page(prod_html)
            for u in urls:
                if u not in seen:
                    all_urls.append(u); seen.add(u)
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to fetch product '{link}': {e}\n")
            continue
    return all_urls

# -------------------- Excel helpers --------------------
def ensure_image_columns(df: pd.DataFrame, image_cols: int, single_col: bool, col_name: str) -> List[str]:
    if single_col:
        if col_name not in df.columns:
            df[col_name] = ""
        return [col_name]
    else:
        cols = []
        for i in range(1, image_cols + 1):
            c = f"Image {i}"
            if c not in df.columns:
                df[c] = ""
            cols.append(c)
        return cols

# -------------------- Main --------------------
def main():
    ap = argparse.ArgumentParser(description="Search by SKU, open each product page, scrape images from specified containers, and write back to the SAME Excel.")
    ap.add_argument("--in", dest="infile", required=True, help="Path to Excel to update (overwritten).")
    ap.add_argument("--sheet", default="Sheet1", help="Sheet name (default: Sheet1)")
    ap.add_argument("--sku-col", default="SKU", help="Column containing SKUs (default: SKU)")
    ap.add_argument("--sample", type=int, default=0, help="Process only the first N SKUs (testing).")

    # storage options
    ap.add_argument("--image-cols", type=int, default=8, help="Create columns Image 1..N (default: 8).")
    ap.add_argument("--single-col", action="store_true", help="Store all URLs in one column instead of multiple.")
    ap.add_argument("--single-col-name", default="Product Images", help="Name of the single column (default: 'Product Images').")

    # traversal options
    ap.add_argument("--first-product-only", action="store_true", help="Open only the first product in results (default: open all).")
    ap.add_argument("--max-products", type=int, default=50, help="Max product pages to visit per SKU if not first-only.")

    args = ap.parse_args()

    try:
        df = pd.read_excel(args.infile, sheet_name=args.sheet)
    except Exception as e:
        sys.stderr.write(f"[ERROR] Reading Excel failed: {e}\n")
        sys.exit(1)

    if args.sku_col not in df.columns:
        sys.stderr.write(f"[ERROR] Column '{args.sku_col}' not in sheet. Columns: {list(df.columns)}\n")
        sys.exit(1)

    image_columns = ensure_image_columns(df, args.image_cols, args.single_col, args.single_col_name)

    skus = df[args.sku_col].astype(str).fillna("").str.strip().tolist()
    if args.sample > 0:
        skus = skus[:args.sample]

    session = make_session()

    # SKU -> URLs
    mapping: Dict[str, List[str]] = {}
    for sku in skus:
        if not sku:
            continue
        try:
            urls = scrape_images_for_sku(
                session,
                sku,
                follow_all_products=not args.first_product_only,
                max_products=args.max_products
            )
            mapping[sku] = urls
        except Exception as e:
            sys.stderr.write(f"[WARN] {sku}: {e}\n")
            mapping[sku] = []

    # Write back
    for idx in df.index:
        sku = str(df.at[idx, args.sku_col]).strip()
        urls = mapping.get(sku, [])
        if args.single_col:
            df.at[idx, image_columns[0]] = " | ".join(urls)
        else:
            for i, c in enumerate(image_columns):
                df.at[idx, c] = urls[i] if i < len(urls) else ""

    # Save
    try:
        with pd.ExcelWriter(args.infile, engine="openpyxl", mode="w") as xw:
            df.to_excel(xw, sheet_name=args.sheet, index=False)
        print(f"✓ Updated '{args.infile}' ({args.sheet}) with product-page images.")
    except Exception as e:
        sys.stderr.write(f"[ERROR] Writing Excel failed: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
