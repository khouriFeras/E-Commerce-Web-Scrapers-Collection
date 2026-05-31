"""
scraperGoat.py — product-only images (strict) with single 'Images' column

Outputs (CSV):
- URL
- Product Type
- Cost
- Description
- Seller
- Images   (semicolon-separated list of full-size product image URLs)

Usage:
  python scraperGoat.py --in urls.txt --out results.csv
  python scraperGoat.py --in ANKAR_translated_SAMPLE3 - Copy.xlsx --out results.csv
  python scraperGoat.py --url "https://www.anker.com/products/..."
"""

from __future__ import annotations
import argparse, csv, json, re, sys, os, random, time
from dataclasses import dataclass
from typing import List
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag
import tldextract
try:
    import pandas as pd
except Exception:
    pd = None
USER_AGENTS = [
    # A few modern desktop UAs to try if blocked
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
]

def build_headers(url: str, ua: str | None = None) -> dict:
    host = ensure_hostname(url)
    ua = ua or random.choice(USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Referer": f"https://{host}/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

def fetch(url: str, timeout: int = 30) -> str:
    # Try primary headers, then rotate UA on 403/429
    headers = build_headers(url)
    r = requests.get(url, headers=headers, timeout=timeout)
    if r.status_code in (403, 429):
        # brief backoff + retry with a different UA
        time.sleep(1.0)
        alt_headers = build_headers(url, ua=random.choice(USER_AGENTS))
        r = requests.get(url, headers=alt_headers, timeout=timeout)
    r.raise_for_status()
    # Fix mis-detected encodings (avoids mojibake for Arabic, etc.)
    enc = r.encoding or ""
    if not enc or enc.lower() in ("iso-8859-1", "latin-1", "ascii"):
        try:
            enc = r.apparent_encoding or "utf-8"
        except Exception:
            enc = "utf-8"
    try:
        r.encoding = enc
        return r.text
    except Exception:
        # Fallback to manual decode
        try:
            return r.content.decode(enc, errors="replace")
        except Exception:
            return r.content.decode("utf-8", errors="replace")
@dataclass
class ProductRecord:
    url: str
    product_type: str = ""
    cost: str = ""
    description: str = ""
    seller: str = ""
    images: str = ""   # image1;image2;image3

    def set_images(self, images: List[str]):
        uniq, seen = [], set()
        for u in images:
            u = (u or "").strip()
            if not u or u in seen:
                continue
            uniq.append(u); seen.add(u)
        self.images = ";".join(uniq)

IMG_URL_RE = re.compile(r"https?://[^\s\"']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"']*)?", re.I)
AMZ_SIZE_SUFFIX = re.compile(r"\._[A-Z0-9_,\-]+_\.", re.I)

NON_PRODUCT_KEYWORDS = re.compile(
    r"(logo|icon|sprite|badge|placeholder|swatch|thumb|thumbnail|avatar|spinner|tracking|social|share|banner|promo|gift|cart)",
    re.I,
)

GALLERY_ANCESTOR = re.compile(r"(product|gallery|media|carousel|slider|image|photos?)", re.I)

def clean_img_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    # Remove Amazon size codes like ..._SX466_.jpg
    url = AMZ_SIZE_SUFFIX.sub(".", url)
    # Drop query params (often size/quality)
    if "?" in url:
        url = url.split("?", 1)[0]
    return url

def ensure_hostname(url: str) -> str:
    ext = tldextract.extract(url)
    if ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return urlparse(url).hostname or ""

def tag_has_min_dimensions(img: Tag, min_px: int = 300) -> bool:
    w = img.get("width") or img.get("data-width")
    h = img.get("height") or img.get("data-height")
    try:
        w = int(str(w)) if w is not None else None
        h = int(str(h)) if h is not None else None
    except Exception:
        return True  # unknown => don't filter by size
    if w is not None and h is not None:
        return (w >= min_px and h >= min_px)
    return True

def is_product_container(el: Tag) -> bool:
    # Walk up a few ancestors looking for product/gallery/media hints
    steps = 0
    node = el
    while node is not None and steps < 4:
        cls = " ".join(node.get("class", []))
        idv = node.get("id", "")
        attrs = f"{cls} {idv}".strip()
        if GALLERY_ANCESTOR.search(attrs or ""):
            return True
        node = node.parent
        steps += 1
    return False

def is_probably_product_image(url: str, hostname: str, img_tag: Tag | None) -> bool:
    if not url:
        return False
    if not re.search(r"\.(jpg|jpeg|png|webp)$", url, re.I):
        return False
    if NON_PRODUCT_KEYWORDS.search(url):
        return False
    # Drop tiny assets if dimensions known
    if img_tag and not tag_has_min_dimensions(img_tag):
        return False
    # Amazon: keep only gallery path
    if hostname.startswith("amazon."):
        if "/images/I/" not in url:
            return False
    # Shopify CDN: require product/files path
    if "cdn.shopify.com" in url:
        if "/products/" not in url and "/files/" not in url:
            return False
    return True

def dedup_preserve_order(urls: List[str]) -> List[str]:
    out, seen = [], set()
    for u in urls:
        if u and u not in seen:
            out.append(u); seen.add(u)
    return out

def images_from_jsonld(soup: BeautifulSoup) -> List[str]:
    imgs: List[str] = []
    for tag in soup.find_all("script", type=lambda v: v and "ld+json" in v):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            t = (node.get("@type") or node.get("type") or "").lower()
            if "product" in t:
                im = node.get("image")
                if isinstance(im, list):
                    imgs += [clean_img_url(x) for x in im]
                elif isinstance(im, str):
                    imgs.append(clean_img_url(im))
    imgs = [u for u in imgs if not NON_PRODUCT_KEYWORDS.search(u)]
    return dedup_preserve_order(imgs)

# ---- Product-scoped gallery collectors ----
GALLERY_SELECTORS = [
    ".product-gallery img",
    ".product__media img",
    ".product-media img",
    ".gallery__image img",
    ".product-images img",
    ".product-photos img",
    "[class*='product'] [class*='gallery'] img",
    "[class*='product'] [class*='image'] img",
    "[id*='product'] img",
    "[id*='gallery'] img",
    "[id*='media'] img",
]

def collect_scoped_gallery_images(soup: BeautifulSoup, hostname: str) -> List[str]:
    cand: List[str] = []
    for sel in GALLERY_SELECTORS:
        for img in soup.select(sel):
            if not isinstance(img, Tag):
                continue
            if not is_product_container(img):
                continue
            # Prefer zoom/large attrs
            src = img.get("data-zoom-image") or img.get("data-large_image") or img.get("src") or img.get("data-src")
            if src:
                src = clean_img_url(src)
                if is_probably_product_image(src, hostname, img):
                    cand.append(src)
            # srcset variants
            srcset = img.get("srcset")
            if srcset:
                for part in [p.strip() for p in srcset.split(",") if p.strip()]:
                    u = clean_img_url(part.split()[0])
                    if is_probably_product_image(u, hostname, img):
                        cand.append(u)
    return dedup_preserve_order(cand)

# ---- Amazon-specific gallery ----
def amazon_gallery_images(soup: BeautifulSoup) -> List[str]:
    imgs: List[str] = []
    # data-a-dynamic-image (gallery map)
    for img in soup.select("img[data-a-dynamic-image]"):
        try:
            data = img.get("data-a-dynamic-image")
            if not data:
                continue
            d = json.loads(data)  # {url: [w,h], ...}
            for u in d.keys():
                u = clean_img_url(u)
                if "/images/I/" in u and not NON_PRODUCT_KEYWORDS.search(u):
                    imgs.append(u)
        except Exception:
            continue
    # ImageBlockATF scripts
    for s in soup.find_all("script"):
        if not s.string or "ImageBlockATF" not in s.string:
            continue
        for m in IMG_URL_RE.findall(s.string):
            m = clean_img_url(m)
            if "/images/I/" in m and not NON_PRODUCT_KEYWORDS.search(m):
                imgs.append(m)
    return dedup_preserve_order(imgs)

# ------------- Extractors -------------
class BaseExtractor:
    domains: List[str] = []
    def matches(self, hostname: str) -> bool:
        return any(hostname.endswith(d) for d in self.domains)
    def extract(self, url: str, html: str, soup: BeautifulSoup) -> ProductRecord:
        raise NotImplementedError

class GenericExtractor(BaseExtractor):
    domains = [""]  # fallback

    def extract(self, url: str, html: str, soup: BeautifulSoup) -> ProductRecord:
        host = ensure_hostname(url)
        rec = ProductRecord(url=url)

        # JSON-LD images (usually clean)
        jl_imgs = images_from_jsonld(soup)
        # Description (OG fallback)
        og = soup.find("meta", property="og:description")
        if og and og.get("content"):
            rec.description = og["content"].strip()
        # Price
        price_el = soup.select_one('[itemprop="price"], meta[property="product:price:amount"]')
        if price_el:
            rec.cost = price_el.get("content") or price_el.get_text(strip=True) or rec.cost

        # Strict product-scoped gallery collection
        scoped = collect_scoped_gallery_images(soup, host)
        imgs = jl_imgs + [u for u in scoped if u not in jl_imgs]
        rec.set_images(imgs)
        return rec

class AmazonExtractor(BaseExtractor):
    domains = ["amazon.com","amazon.ae","amazon.eg","amazon.co.uk","amazon.sa","amazon.de","amazon.ie","amazon.in"]
    def extract(self, url: str, html: str, soup: BeautifulSoup) -> ProductRecord:
        rec = ProductRecord(url=url)

        # Description (bullets)
        bullets = soup.select("#feature-bullets li span")
        if bullets:
            rec.description = "; ".join([b.get_text(strip=True) for b in bullets if b.get_text(strip=True)])

        # Price
        price_el = soup.select_one(
            "#corePriceDisplay_desktop_feature_div .a-offscreen, "
            "#corePrice_feature_div .a-offscreen, "
            "#priceblock_ourprice, #priceblock_dealprice"
        )
        if price_el:
            rec.cost = price_el.get_text(strip=True)

        # Brand
        brand_el = soup.select_one("#bylineInfo")
        if brand_el:
            rec.seller = brand_el.get_text(strip=True)

        # Product type via breadcrumbs
        crumb = soup.select("#wayfinding-breadcrumbs_container ul.a-unordered-list a")
        if crumb:
            rec.product_type = " > ".join([c.get_text(strip=True) for c in crumb if c.get_text(strip=True)])

        # Images — strictly from gallery
        imgs = amazon_gallery_images(soup)
        if not imgs:
            imgs = collect_scoped_gallery_images(soup, "amazon.com")
        rec.set_images(imgs)
        return rec

class AnkerFamilyExtractor(BaseExtractor):
    domains = ["anker.com","anker.com.sg","myanker.com.au","ankersolix.com","soundcore.com","eufy.com","ankermake.com","ankerkw.com"]
    def extract(self, url: str, html: str, soup: BeautifulSoup) -> ProductRecord:
        host = ensure_hostname(url)
        rec = ProductRecord(url=url)

        # JSON-LD first
        jl = images_from_jsonld(soup)
        # Description
        if "soundcore" in host:
            # Prefer bullet features for Soundcore
            bullets: List[str] = []
            for sel in [
                "ul.a-unordered-list li",
                "ul[class*='a-unordered-list'] li",
                "ul[class*='a-spacing-mini'] li",
                "ul[class*='unordered'] li",
                "ul[class*='list'] li",
            ]:
                for li in soup.select(sel):
                    if not isinstance(li, Tag):
                        continue
                    txt = li.get_text(" ", strip=True)
                    if not txt:
                        continue
                    # Heuristics to avoid navigation and tiny items
                    if 20 <= len(txt) <= 300 and not NON_PRODUCT_KEYWORDS.search(txt):
                        bullets.append(txt)
            bullets = dedup_preserve_order(bullets)
            if bullets:
                rec.description = "; ".join(bullets)
        # Fallback to OG description if still empty
        if not rec.description:
            og = soup.find("meta", property="og:description")
            if og and og.get("content"):
                rec.description = og["content"].strip()
        # Price
        price_el = soup.select_one('[itemprop="price"], meta[property="product:price:amount"]')
        if price_el:
            rec.cost = price_el.get("content") or price_el.get_text(strip=True) or rec.cost
        # Seller
        if not rec.seller:
            if "soundcore" in host:
                rec.seller = "Soundcore (by Anker)"
            elif "eufy" in host:
                rec.seller = "eufy (by Anker)"
            elif "ankermake" in host:
                rec.seller = "AnkerMake"
            else:
                rec.seller = "Anker"

        scoped = collect_scoped_gallery_images(soup, host)
        imgs = jl + [u for u in scoped if u not in jl]
        rec.set_images(imgs)
        return rec

class ShopifyLikeExtractor(BaseExtractor):
    domains = ["goldjo.com","tv-it.com","ankeromanstore.com","jumbo.ae","jarir.com"]
    def extract(self, url: str, html: str, soup: BeautifulSoup) -> ProductRecord:
        host = ensure_hostname(url)
        rec = ProductRecord(url=url)

        # Prefer Shopify product JSON: <script id="ProductJson-...">
        prod_json = None
        for s in soup.find_all("script", id=re.compile(r"ProductJson-", re.I)):
            try:
                prod_json = json.loads(s.string or "{}")
                break
            except Exception:
                continue

        imgs = []
        if isinstance(prod_json, dict):
            # Description
            body_html = prod_json.get("body_html") or ""
            if body_html:
                rec.description = BeautifulSoup(body_html, "html.parser").get_text(" ", strip=True)
            # Product type
            if prod_json.get("product_type"):
                rec.product_type = prod_json["product_type"]
            # Price
            variants = prod_json.get("variants") or []
            if variants:
                price = variants[0].get("price")
                if price:
                    rec.cost = str(price)
            # Images (Shopify CDN only)
            for im in prod_json.get("images", []):
                src = im.get("src")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                src = clean_img_url(src)
                if "cdn.shopify.com" in src and ("/products/" in src or "/files/" in src):
                    imgs.append(src)

        # If JSON had no images, collect from strict gallery containers
        if not imgs:
            imgs = collect_scoped_gallery_images(soup, host)

        rec.set_images(imgs)
        return rec

EXTRACTORS = [
    AmazonExtractor(),
    AnkerFamilyExtractor(),
    ShopifyLikeExtractor(),
    GenericExtractor(),
]

def pick_extractor(hostname: str):
    for ex in EXTRACTORS:
        if ex.matches(hostname):
            return ex
    return GenericExtractor()

COLUMNS = [
    "URL","Product Type","Cost","Description","Seller","Images"
]

def scrape_one(url: str) -> ProductRecord:
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    hostname = ensure_hostname(url)
    extractor = pick_extractor(hostname)
    rec = extractor.extract(url, html, soup)
    return rec

def write_rows(path: str, rows: List[ProductRecord]):
    file_exists = os.path.exists(path)
    write_header = (not file_exists) or (os.path.getsize(path) == 0)
    mode = "a" if file_exists else "w"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r.url, r.product_type, r.cost, r.description, r.seller, r.images])

def write_with_preserved_excel(out_path: str, source_df, url_col: str, records: List[ProductRecord]):
    if pd is None:
        raise RuntimeError("pandas required to write Excel/CSV with preserved columns")
    # Build map from URL to (description, images)
    url_to_desc = {r.url: r.description for r in records}
    url_to_imgs = {r.url: r.images for r in records}
    # Create/replace only Description and Images columns
    source_df = source_df.copy()
    # Map new values
    mapped_desc = source_df[url_col].map(url_to_desc)
    mapped_imgs = source_df[url_col].map(url_to_imgs)
    # Merge with existing columns if present; otherwise just create new columns
    if "Description" in source_df.columns:
        source_df["Description"] = mapped_desc.combine_first(source_df["Description"])
    else:
        source_df["Description"] = mapped_desc
    if "Images" in source_df.columns:
        source_df["Images"] = mapped_imgs.combine_first(source_df["Images"])
    else:
        source_df["Images"] = mapped_imgs
    # Save to desired format
    if out_path.lower().endswith(('.xlsx', '.xls')):
        source_df.to_excel(out_path, index=False)
    else:
        source_df.to_csv(out_path, index=False, encoding="utf-8", lineterminator="\n")

def read_input(arg_in: str) -> List[str]:
    lower = arg_in.lower()
    if lower.endswith((".xlsx",".xls")):
        if pd is None:
            raise RuntimeError("pandas required for Excel. pip install pandas openpyxl")
        df = pd.read_excel(arg_in)
        for col in ["Links","Link","URL","Urls","Product Link","Product URL"]:
            if col in df.columns:
                s = df[col].dropna().astype(str)
                return [u for u in s if u.strip()]
        # auto-detect first URL-like column
        for c in df.columns:
            s = df[c].dropna().astype(str)
            hit = s[s.str.contains(r"^https?://", case=False, regex=True)]
            if not hit.empty:
                return hit.tolist()
        raise ValueError("No URL column found in Excel (looked for Links/URL).")
    else:
        for enc in ("utf-8","utf-8-sig","cp1256","cp1252"):
            try:
                with open(arg_in, "r", encoding=enc) as f:
                    return [ln.strip() for ln in f if ln.strip()]
            except UnicodeDecodeError:
                continue
        raise

def read_input_with_df(arg_in: str):
    lower = arg_in.lower()
    if not lower.endswith((".xlsx",".xls")):
        raise ValueError("read_input_with_df expects an Excel path")
    if pd is None:
        raise RuntimeError("pandas required for Excel. pip install pandas openpyxl")
    df = pd.read_excel(arg_in)
    url_col = None
    for col in ["Links","Link","URL","Urls","Product Link","Product URL"]:
        if col in df.columns:
            url_col = col
            break
    if url_col is None:
        # auto-detect first URL-like column
        for c in df.columns:
            s = df[c].dropna().astype(str)
            hit = s[s.str.contains(r"^https?://", case=False, regex=True)]
            if not hit.empty:
                url_col = c
                break
    if url_col is None:
        raise ValueError("No URL column found in Excel (looked for Links/URL).")
    urls = df[url_col].dropna().astype(str).tolist()
    return urls, df, url_col

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="Single product URL to scrape")
    ap.add_argument("--in", dest="inp", help="Input .txt (one URL per line) OR .xlsx with 'Links' column")
    ap.add_argument("--out", default="results.csv", help="Output CSV path")
    args = ap.parse_args()

    urls: List[str] = []
    if args.url:
        urls = [args.url.strip()]
        src_df = None; url_col = None
    elif args.inp:
        if args.inp.lower().endswith((".xlsx",".xls")):
            urls, src_df, url_col = read_input_with_df(args.inp)
        else:
            urls = read_input(args.inp)
            src_df = None; url_col = None
    else:
        ap.error("Provide --url or --in")

    rows: List[ProductRecord] = []
    total = len(urls)
    for i, u in enumerate(urls, 1):
        try:
            rec = scrape_one(u)
        except Exception as e:
            rec = ProductRecord(url=u, description=f"ERROR: {e}")
        rows.append(rec)
        if i % 10 == 0 or i == total:
            print(f"[{i}/{total}] processed", file=sys.stderr)

    if 'src_df' in locals() and src_df is not None:
        write_with_preserved_excel(args.out, src_df, url_col, rows)
        print(f"Saved -> {args.out} (preserved original columns + appended Description/Images)")
    else:
        write_rows(args.out, rows)
        print(f"Saved -> {args.out}")

if __name__ == "__main__":
    main()
