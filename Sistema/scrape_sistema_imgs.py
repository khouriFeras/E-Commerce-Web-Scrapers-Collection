import argparse
import re
import time
from typing import List, Optional, Tuple
from urllib.parse import quote, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.sistemaplastics.com"
SEARCH_URL = BASE_URL + "/catalogsearch/result/?q={query}"
IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp)(?:\?.*)?$", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
NUM_RE = re.compile(r"\d+")


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    n = name.strip().lower()
    n = n.replace("sistema", " ")
    n = n.replace("tm", " ")
    n = n.replace("®", " ").replace("™", " ")
    n = n.replace("&", " and ")
    n = WHITESPACE_RE.sub(" ", n)
    n = NON_ALNUM_RE.sub("", n)
    return n


def name_tokens(name: str) -> List[str]:
    text = str(name or "").lower()
    text = text.replace("sistema", " ")
    text = text.replace("tm", " ")
    text = text.replace("®", " ").replace("™", " ")
    text = text.replace("&", " and ")
    text = NON_ALNUM_RE.sub(" ", text)
    tokens = [t for t in WHITESPACE_RE.sub(" ", text).strip().split(" ") if t]
    stop = {
        "with",
        "and",
        "the",
        "to",
        "go",
        "lunch",
        "bag",
        "bottle",
        "plus",
        "square",
        "rectangle",
        "ml",
        "l",
    }
    return [t for t in tokens if t not in stop and len(t) > 1]


def likely_same_name(left: str, right: str) -> bool:
    nl = normalize_name(left)
    nr = normalize_name(right)
    if nl == nr:
        return True
    if nl and nr and (nl in nr or nr in nl):
        return True

    lt = set(name_tokens(left))
    rt = set(name_tokens(right))
    if not lt or not rt:
        return False
    overlap = len(lt & rt)
    return overlap >= max(1, min(len(lt), len(rt)) - 1)


def item_code_digits(item_code: str) -> str:
    digs = "".join(NUM_RE.findall(str(item_code or "")))
    return digs


def looks_like_image_url(url: str) -> bool:
    return bool(url and IMAGE_EXT_RE.search(url))


def absolutize(url: str) -> str:
    return urljoin(BASE_URL, url)


def strip_query(url: str) -> str:
    return url.split("?", 1)[0]


def is_valid_product_image(url: str) -> bool:
    low = (url or "").lower()
    if "placeholder" in low:
        return False
    return True


def extract_image_urls(product_html: str) -> List[str]:
    soup = BeautifulSoup(product_html, "html.parser")
    urls: List[str] = []
    seen = set()

    # The PDP gallery uses "simple-item" thumbnails; removing query params yields
    # the full-resolution source hosted on the same Sirv path.
    gallery_candidates = soup.select("img.simple-item[data-image], img.simple-item")
    for img in gallery_candidates:
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src:
            continue
        abs_url = absolutize(src)
        if "/catalog/product/" not in abs_url or not looks_like_image_url(abs_url):
            continue
        hi_res = strip_query(abs_url)
        if not is_valid_product_image(hi_res):
            continue
        if hi_res not in seen:
            seen.add(hi_res)
            urls.append(hi_res)

    # Fallback: also check og:image.
    og = soup.select_one("meta[property='og:image']")
    if og:
        og_url = (og.get("content") or "").strip()
        if og_url:
            og_abs = strip_query(absolutize(og_url))
            if "/catalog/product/" in og_abs and looks_like_image_url(og_abs):
                if is_valid_product_image(og_abs) and og_abs not in seen:
                    seen.add(og_abs)
                    urls.append(og_abs)

    # Stable ordering with highest-confidence gallery URLs first.
    urls.sort(key=lambda u: ("/catalog/product/" not in u, len(u)))
    return urls


def get_first_search_result(session: requests.Session, product_name: str) -> Optional[Tuple[str, str]]:
    search_url = SEARCH_URL.format(query=quote(product_name))
    resp = session.get(search_url, timeout=45)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    first = soup.select_one("li.product-item a.product-item-link")
    if not first:
        return None
    result_name = WHITESPACE_RE.sub(" ", first.get_text(" ", strip=True))
    href = first.get("href")
    if not href:
        return None
    return result_name, absolutize(href)


def product_identity_from_page(product_html: str, product_url: str) -> Tuple[str, str]:
    soup = BeautifulSoup(product_html, "html.parser")
    title_tag = soup.select_one("h1.page-title span.base, span.base")
    title = WHITESPACE_RE.sub(" ", title_tag.get_text(" ", strip=True)) if title_tag else ""
    sku = ""
    form = soup.select_one("form[data-product-sku]")
    if form:
        sku = (form.get("data-product-sku") or "").strip()
    if not sku:
        sku_input = soup.select_one("form input[name='product']")
        if sku_input:
            sku = (sku_input.get("value") or "").strip()
    if not title:
        title = product_url.rstrip("/").split("/")[-1].replace("-", " ")
    return title, sku


def scrape_from_url(
    session: requests.Session, url: str
) -> Tuple[str, str, str]:
    resp = session.get(url, timeout=45)
    resp.raise_for_status()
    title, sku = product_identity_from_page(resp.text, url)
    imgs = extract_image_urls(resp.text)
    if not imgs:
        return "", title, sku
    return ";".join(imgs), title, sku


def scrape_row_images(
    session: requests.Session, product_name: str, item_code: str
) -> Tuple[str, str]:
    first = get_first_search_result(session, product_name)
    if not first:
        return "", "no-result"

    first_name, first_url = first
    code_digits = item_code_digits(item_code)
    imgs, page_title, sku = scrape_from_url(session, first_url)

    # Accept first result if name matches robustly OR item code appears in page SKU.
    if likely_same_name(first_name, product_name) or likely_same_name(page_title, product_name):
        return imgs, "ok" if imgs else "no-images"
    if code_digits and code_digits in item_code_digits(sku):
        return imgs, "ok" if imgs else "no-images"

    # Fallback: search by item code and take first result.
    if code_digits:
        code_hit = get_first_search_result(session, code_digits)
        if code_hit:
            _, code_url = code_hit
            code_imgs, code_title, code_sku = scrape_from_url(session, code_url)
            if code_digits in item_code_digits(code_sku) or likely_same_name(code_title, product_name):
                return code_imgs, "ok" if code_imgs else "no-images"

    return "", "name-mismatch"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        default=r"D:/JafarShop/Scrapers/Sistema/Upload Products to Jaafar Shop - Sistema.xlsx",
        help="Path to Sistema xlsx file",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Delay in seconds between rows",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output xlsx path (defaults to overwrite --file)",
    )
    args = parser.parse_args()

    df = pd.read_excel(args.file)
    if "Product Name" not in df.columns:
        raise ValueError("Missing 'Product Name' column")
    if "imgs" not in df.columns:
        df["imgs"] = ""
    # Keep strings to avoid pandas dtype warnings when writing URLs.
    df["imgs"] = df["imgs"].astype("object")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )

    stats = {"ok": 0, "no-result": 0, "name-mismatch": 0, "no-images": 0, "error": 0}
    for idx, name in df["Product Name"].items():
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            item_code = str(df.at[idx, "Item Code"]) if "Item Code" in df.columns else ""
            imgs, status = scrape_row_images(session, name.strip(), item_code)
        except Exception:
            imgs, status = "", "error"
        stats[status] = stats.get(status, 0) + 1
        df.at[idx, "imgs"] = imgs
        print(f"[{idx + 1}/{len(df)}] {status}: {name}")
        time.sleep(args.delay)

    output_path = args.out.strip() or args.file
    df.to_excel(output_path, index=False)
    print("\nDone.")
    for k, v in stats.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
