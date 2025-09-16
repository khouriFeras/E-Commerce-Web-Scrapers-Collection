

import argparse, time, json, re
from urllib.parse import urljoin
from typing import List, Dict, Any, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE = "https://birdslandjo.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; birdsland-scraper/1.4; +https://example.local)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

def find_sku_column(df: pd.DataFrame, user_col: Optional[str]) -> str:
    if user_col:
        if user_col in df.columns:
            return user_col
        raise ValueError(f"--sku-col '{user_col}' not found. Available: {list(df.columns)}")
    for c in df.columns:
        if "sku" in str(c).lower():
            return c
    for candidate in ["رقم الباركود", "Item", "Item No", "ItemNo", "Code", "Product Code", "Variant SKU"]:
        if candidate in df.columns:
            return candidate
    return df.columns[0]

def _abs_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    if ("," in u and " " in u) or u.endswith(("w", "x")):
        u = u.split(",")[0].strip().split(" ")[0].strip()
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("/"):
        u = urljoin(BASE, u)
    return u

def _push(urls: List[str], u: str):
    u = _abs_url(u)
    if not u.startswith("http"):
        return
    low = u.lower()
    junk_terms = ["placeholder", "sprite", "data:image", "base64,"]
    if any(t in low for t in junk_terms):
        return
    if u not in urls:
        urls.append(u)

def extract_description(soup: BeautifulSoup) -> str:
    selectors = [
        "div.product.attribute.description div.value",
        "div#description",
        "div[itemprop='description']",
        "div.product-info-main .value",
        "div.product.attribute.overview div.value",
        "div.product.attribute.description",
    ]
    for sel in selectors:
        node = soup.select_one(sel)
        if node:
            for t in node.select("script, style, noscript"):
                t.decompose()
            txt = node.get_text(" ", strip=True)
            if txt:
                return txt
    return ""

def extract_images_from_jsonld(soup: BeautifulSoup, take_largest=False) -> List[str]:
    out: List[str] = []

    def pick_largest(cands: List[str]) -> str:
        cands = [_abs_url(c) for c in cands if c]
        cands = [c for c in cands if c.startswith("http")]
        if not cands:
            return ""
        def score(u: str) -> int:
            m = re.search(r"/(\d{2,4})x(\d{2,4})/", u)
            if m:
                w, h = map(int, m.groups())
                return w * h
            return len(u)
        cands.sort(key=score, reverse=True)
        return cands[0]

    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text() or "{}")
        except Exception:
            continue

        def visit(obj):
            if isinstance(obj, dict):
                if "image" in obj:
                    v = obj["image"]
                    if isinstance(v, str):
                        _push(out, v)
                    elif isinstance(v, list):
                        if take_largest:
                            best = pick_largest([x for x in v if isinstance(x, str)])
                            if best: _push(out, best)
                        else:
                            for x in v:
                                if isinstance(x, str):
                                    _push(out, x)
                for v in obj.values():
                    visit(v)
            elif isinstance(obj, list):
                for it in obj:
                    visit(it)
        visit(data)
    return out

def extract_images_from_magento_gallery(soup: BeautifulSoup) -> List[str]:
    out: List[str] = []
    scripts = soup.find_all("script", attrs={"type": "text/x-magento-init"}) or []
    scripts += [s for s in soup.find_all("script") if "mage/gallery/gallery" in (s.string or s.get_text() or "")]
    for s in scripts:
        txt = s.string or s.get_text() or ""
        if "gallery" not in txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:
            m = re.search(r'(\{.*"mage/gallery/gallery".*\})', txt, flags=re.S)
            if not m:
                continue
            try:
                data = json.loads(m.group(1))
            except Exception:
                continue

        def visit(obj):
            if isinstance(obj, dict):
                full = obj.get("full")
                if isinstance(full, str):
                    _push(out, full)
                for k in ("img", "thumb"):
                    v = obj.get(k)
                    if isinstance(v, str):
                        _push(out, v)
                for v in obj.values():
                    visit(v)
            elif isinstance(obj, list):
                for it in obj:
                    visit(it)
        visit(data)
    return out

def _split_srcset(val: str) -> List[str]:
    out = []
    for part in (val or "").split(","):
        part = part.strip()
        if not part:
            continue
        url_only = part.split(" ")[0].strip()
        out.append(url_only)
    return out

def extract_images_generic(soup: BeautifulSoup) -> List[str]:
    urls: List[str] = []

    # 1) Product/visible imgs incl. lazy attributes
    for tag in soup.select(
        "div.product.media img, "
        "div.fotorama__stage__shaft.fotorama__grab img, "
        "div.fotorama__nav__frame--thumb img, "
        "img.fotorama__img, "
        "img.gallery-placeholder__image"
    ):
        cands = []
        for attr in ("data-full", "data-zoom-image", "data-image", "data-src", "src"):
            v = tag.get(attr)
            if v:
                cands.append(v)
        for attr in ("srcset", "data-srcset"):
            v = tag.get(attr)
            if v:
                cands.extend(_split_srcset(v))
        for c in cands:
            _push(urls, c)

    # 2) Inline background-image
    for d in soup.select("[style*='background-image']"):
        m = re.search(r'url\((["\']?)(.+?)\1\)', d.get("style") or "", flags=re.I)
        if m:
            _push(urls, m.group(2))

    # 3) og:image / twitter:image
    for sel, attr in [("meta[property='og:image']", "content"),
                      ("meta[name='twitter:image']", "content")]:
        for m in soup.select(sel):
            _push(urls, m.get(attr) or "")

    return urls

from urllib.parse import urlparse, parse_qs

_SIZE_PATTERNS = [
    re.compile(r"/(\d{2,5})x(\d{2,5})/"),               # /600x600/
    re.compile(r"[_-](\d{2,5})x(\d{2,5})(?=\.)"),        # _600x600.jpg or -800x450.png
    re.compile(r"[?&](?:w|width)=(\d{2,5}).*?[&?](?:h|height)=(\d{2,5})", re.I),  # ?w=800&h=800
]

def _img_size_hint(u: str) -> tuple[int, int]:
    """Try to read WxH hints from URL path/query."""
    try:
        for pat in _SIZE_PATTERNS:
            m = pat.search(u)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                return w, h
        # single param cases like ?width=800 (assume square as fallback)
        q = parse_qs(urlparse(u).query)
        w = q.get("w", q.get("width", [None]))[0]
        h = q.get("h", q.get("height", [None]))[0]
        if w and h:
            return int(w), int(h)
        if w and not h:
            return int(w), int(w)
        if h and not w:
            return int(h), int(h)
    except Exception:
        pass
    return (0, 0)

def _img_key(u: str) -> str:
    """
    Stable key to identify the SAME photo across size variants.
    We use filename (without _600x600) + domain as the key.
    """
    try:
        p = urlparse(u)
        fname = p.path.split("/")[-1]
        # strip common size suffixes like _600x600
        fname = re.sub(r"[_-]\d{2,5}x\d{2,5}(?=\.)", "", fname)
        # drop query
        return f"{p.netloc}/{fname}".lower()
    except Exception:
        return u.lower()

def _score_url(u: str) -> int:
    """Prefer larger images by WxH; fallback to URL length as tiebreaker."""
    w, h = _img_size_hint(u)
    if w and h:
        return w * h
    return len(u)

def extract_images(soup: BeautifulSoup, max_img: int) -> List[str]:
    """
    Collect all candidates, group by photo key, keep ONLY the largest variant per photo.
    Return up to max_img distinct photos (each at largest size).
    """
    candidates: List[str] = []

    # JSON-LD (often full-size)
    candidates.extend(extract_images_from_jsonld(soup, take_largest=False))
    # Magento gallery blobs
    for u in extract_images_from_magento_gallery(soup):
        if u not in candidates:
            candidates.append(u)
    # Generic fallbacks (img/srcset/data-*)
    for u in extract_images_generic(soup):
        if u not in candidates:
            candidates.append(u)

    # Group by image key (same photo across sizes), pick the largest per group
    best_by_key: Dict[str, str] = {}
    for u in candidates:
        key = _img_key(u)
        cur_best = best_by_key.get(key)
        if not cur_best or _score_url(u) > _score_url(cur_best):
            best_by_key[key] = u

    # Now we have one URL per distinct photo (largest variant only)
    unique_fullsize = list(best_by_key.values())

    # Sort photos by their size (largest-first) and cap to max_img
    unique_fullsize.sort(key=_score_url, reverse=True)
    return unique_fullsize[:max_img]



def _clean_breadcrumb(parts) -> str:
    seen = set()
    out = []
    for p in parts:
        p = (p or "").strip()
        if not p: continue
        if p.lower() == "home":  # skip "Home"
            continue
        if p not in seen:
            seen.add(p)
            out.append(p)
    return " > ".join(out)

def extract_categories(soup: BeautifulSoup) -> str:
    try:
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(s.string or s.get_text() or "{}")
            except Exception:
                continue
            def visit(obj):
                if isinstance(obj, dict):
                    if obj.get("@type") == "BreadcrumbList" and isinstance(obj.get("itemListElement"), list):
                        items = obj["itemListElement"]
                        norm = []
                        for it in items:
                            if isinstance(it, dict):
                                norm.append(it)
                            elif isinstance(it, list):
                                norm.extend([x for x in it if isinstance(x, dict)])
                        try:
                            norm.sort(key=lambda d: int(d.get("position", 1e9)))
                        except Exception:
                            pass
                        names = []
                        for it in norm:
                            item = it.get("item") if isinstance(it.get("item"), dict) else it.get("item")
                            name = ""
                            if isinstance(item, dict):
                                name = item.get("name", "")
                            elif isinstance(item, str):
                                name = item
                            if not name and "name" in it:
                                name = it.get("name", "")
                            if name:
                                names.append(name)
                        if names:
                            return _clean_breadcrumb(names)
                    for v in obj.values():
                        res = visit(v)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for v in obj:
                        res = visit(v)
                        if res:
                            return res
                return ""
            res = visit(data)
            if res:
                return res
    except Exception:
        pass

    crumbs = []
    for a in soup.select("div.breadcrumbs a, ol.breadcrumbs a, ul.items li a"):
        t = a.get_text(" ", strip=True)
        if t:
            crumbs.append(t)
    if crumbs:
        return _clean_breadcrumb(crumbs)
    return ""

def extract_brand(soup: BeautifulSoup) -> str:
    label_patterns = [
        r"^\s*Brand\s*Product\s*:?\s*$",
        r"^\s*Brand\s*:?\s*$",
        r"^\s*الماركة\s*:?\s*$",
    ]
    def label_value_text(container):
        if not container:
            return ""
        links = [a.get_text(" ", strip=True) for a in container.find_all("a")]
        if links:
            return ", ".join(dict.fromkeys([l for l in links if l]))
        return container.get_text(" ", strip=True)

    for lab_pat in label_patterns:
        for th in soup.select("table.additional-attributes th, table.data th, th"):
            if re.match(lab_pat, th.get_text(" ", strip=True), flags=re.I):
                td = th.find_next_sibling("td")
                return label_value_text(td)
        for dt in soup.select("dt"):
            if re.match(lab_pat, dt.get_text(" ", strip=True), flags=re.I):
                dd = dt.find_next_sibling("dd")
                return label_value_text(dd)
        for strong in soup.select("strong, span, div"):
            if re.match(lab_pat, strong.get_text(" ", strip=True), flags=re.I):
                parent = strong.parent
                if parent:
                    candidates = parent.find_all(["a","span","div"], recursive=False)
                    txt = ", ".join(
                        dict.fromkeys([c.get_text(" ", strip=True) for c in candidates if c is not strong])
                    )
                    if txt:
                        return txt
                sib = strong.find_next_sibling()
                if sib:
                    return label_value_text(sib)

    block = soup.find(string=re.compile(r"Brand\s*Product", re.I))
    if block and block.parent:
        links = block.parent.find_all("a")
        if links:
            return ", ".join(dict.fromkeys([a.get_text(" ", strip=True) for a in links]))
    return ""

# =========================
# Selenium core (UI you can see)
# =========================
def open_browser(headless: bool) -> webdriver.Chrome:
    """
    Uses Selenium Manager (no webdriver-manager). Requirements: pip install -U selenium
    """
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=options)  # Selenium Manager picks correct driver
    return driver

def _visible(el) -> bool:
    try:
        return el.is_displayed() and el.is_enabled()
    except Exception:
        return False

def open_home_and_search(driver, sku: str, wait: WebDriverWait) -> None:
    """
    Go to homepage, focus the search input, type SKU, press Enter.
    No clicking of suggestions; Enter only.
    """
    driver.get(f"{BASE}/en/")

    # Wait for input or a toggle that reveals it
    try:
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input#search, input[name='q']")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".action.show-search, button[title='Search'], .search-toggle, .extra-menu-icon[data-item-type='search']"))
            )
        )
    except Exception:
        pass

    # Try input directly
    search_input = None
    for sel in ["input#search", "input[name='q']"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if _visible(el):
                search_input = el
                break
        except Exception:
            pass

    # If not visible, click a toggle then re-find input
    if not search_input:
        toggles = [
            ".action.show-search",
            "button[title='Search']",
            ".search-toggle",
            ".extra-menu-icon[data-item-type='search']",
            "button[data-action='toggle-search']",
        ]
        for sel in toggles:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                if _visible(btn):
                    btn.click()
                    break
            except Exception:
                continue
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input#search, input[name='q']")))
        except Exception:
            pass
        for sel in ["input#search", "input[name='q']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if _visible(el):
                    search_input = el
                    break
            except Exception:
                pass

    if not search_input:
        raise RuntimeError("Search input not found on homepage.")

    # Type SKU and press Enter
    search_input.clear()
    search_input.send_keys(str(sku).strip())
    search_input.send_keys(Keys.ENTER)

    # Wait for product page or results page
    try:
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-info-main, .product.media")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "a.product-item-link, .search.results, .column.main .products.list"))
            )
        )
    except Exception:
        pass

def _get_first_result_url_from_results_page(driver) -> str:
    """
    On a search results page, pick the first product link URL (no clicking).
    """
    hrefs = []
    for sel in ["a.product-item-link", "a.product.photo", "div.product-item-info a"]:
        for a in driver.find_elements(By.CSS_SELECTOR, sel):
            href = (a.get_attribute("href") or "").strip()
            if href and href.startswith("http") and "birdslandjo.com" in href:
                if href not in hrefs:
                    hrefs.append(href)
        if hrefs:
            break
    return hrefs[0] if hrefs else ""

def extract_all_from_current_page(driver: webdriver.Chrome, max_img: int) -> Dict[str, Any]:
    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")
    desc = extract_description(soup)
    imgs = extract_images(soup, max_img=max_img)
    cats = extract_categories(soup)
    brand = extract_brand(soup)
    return {"Description": desc, "Images": imgs, "Categories": cats, "Brand": brand}

def scrape_product_for_sku_selenium(driver, sku: str, max_img: int, sleep_s: float, wait) -> Dict[str, Any]:
    """
    Workflow:
      1) Open homepage
      2) Type SKU in search bar, press Enter (no clicks)
      3) If on results page, navigate to the first product URL (driver.get, no click)
      4) Scroll to trigger lazy-loading, then extract
    """
    result = {"Description": "", "Images": [], "Categories": "", "Brand": "", "ProductURL": "", "Found": False, "Note": ""}

    try:
        # 1–2) Search by typing + Enter
        open_home_and_search(driver, sku, wait)
        time.sleep(max(sleep_s, 0.5))

        # Are we on a product page already?
        html = driver.page_source
        soup = BeautifulSoup(html, "lxml")

        def _is_product_page(sp: BeautifulSoup) -> bool:
            return bool(sp.select_one(".product-info-main, .product.media, div.product.attribute.description"))

        if not _is_product_page(soup):
            # still on results; navigate to first product URL (no click)
            first_url = _get_first_result_url_from_results_page(driver)
            if not first_url:
                result["Note"] = "No product results after search"
                return result
            driver.get(first_url)
            time.sleep(max(sleep_s, 0.5))

            # trigger lazy-loading
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.8)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.6);")
                time.sleep(0.4)
            except Exception:
                pass

            html = driver.page_source
            soup = BeautifulSoup(html, "lxml")
            if not _is_product_page(soup):
                result["Note"] = "Could not reach a product page"
                return result
            chosen_url = first_url
        else:
            # already on product page
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.8)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.6);")
                time.sleep(0.4)
            except Exception:
                pass

            html = driver.page_source
            soup = BeautifulSoup(html, "lxml")
            chosen_url = driver.current_url

        # Extract data
        desc = extract_description(soup)
        imgs = extract_images(soup, max_img=max_img)
        cats = extract_categories(soup)
        brand = extract_brand(soup)

        result.update({
            "Description": desc,
            "Images": imgs,
            "Categories": cats,
            "Brand": brand,
            "ProductURL": chosen_url,
            "Found": True if (desc or imgs or cats or brand) else False,
            "Note": "" if (desc or imgs or cats or brand) else "Page parsed but empty",
        })
        return result

    except Exception as e:
        result["Note"] = f"Error: {e.__class__.__name__}: {e}"
        return result

# =========================
# Main program
# =========================
def main():
    ap = argparse.ArgumentParser(description="BirdslandJO scraper by SKU (Selenium visible browser)")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel path")
    ap.add_argument("--out", dest="out", required=True, help="Output Excel path")
    ap.add_argument("--sku-col", dest="sku_col", default=None, help="SKU column name (auto-detect if omitted)")
    ap.add_argument("--max-img", dest="max_img", type=int, default=10, help="Max images per product")
    ap.add_argument("--sleep", dest="sleep", type=float, default=1.0, help="Delay between navigations (seconds)")
    ap.add_argument("--headless", dest="headless", action="store_true", help="Run Chrome headless (no UI)")
    ap.add_argument("--keep-open", dest="keep_open", action="store_true", help="Keep browser open after finishing")
    args = ap.parse_args()

    driver = open_browser(headless=args.headless)
    wait = WebDriverWait(driver, 15)

    try:
        df = pd.read_excel(args.inp)
        sku_col = find_sku_column(df, args.sku_col)
        df["_SKU_SCRAPE_KEY"] = df[sku_col].astype(str).str.strip()

        records = []
        total = len(df)
        for idx, sku in enumerate(df["_SKU_SCRAPE_KEY"], 1):
            sku_norm = (sku or "").strip()
            if not sku_norm or sku_norm.lower() in {"nan", "none"}:
                rec = {"_SKU_SCRAPE_KEY": sku, "Found": False, "Description": "", "Categories": "", "Brand": "", "ProductURL": "", "Images": [], "Note": "Empty SKU"}
                records.append(rec)
                print(f"[{idx}/{total}] SKU='{sku}' -> Empty SKU, skipped.")
                continue

            print(f"[{idx}/{total}] Searching: {sku_norm}")
            info = scrape_product_for_sku_selenium(
                driver=driver,
                sku=sku_norm,
                max_img=args.max_img,
                sleep_s=args.sleep,
                wait=wait
            )
            info["_SKU_SCRAPE_KEY"] = sku
            records.append(info)

            status = "FOUND" if info.get("Found") else f"MISS ({info.get('Note','')})"
            img_count = len(info.get("Images") or [])
            first_img = (info.get("Images") or [""])[0]
            print(f"    -> {status}; imgs={img_count}; first_img={first_img}; url={info.get('ProductURL','')}")
            time.sleep(args.sleep)

        out_df = pd.DataFrame(records)

        # Expand Images list into IMGURL1..N (keep at least IMGURL1 column)
        max_imgs = max((len(x or []) for x in out_df["Images"]), default=0)
        max_imgs = min(max_imgs, args.max_img)
        if max_imgs == 0:
            max_imgs = 1

        for i in range(max_imgs):
            out_df[f"IMGURL{i+1}"] = out_df["Images"].apply(lambda lst, i=i: (lst[i] if (isinstance(lst, list) and i < len(lst)) else ""))

        keep_cols = ["_SKU_SCRAPE_KEY", "Found", "ProductURL", "Description", "Categories", "Brand"] + \
                    [f"IMGURL{i+1}" for i in range(max_imgs)] + ["Note"]
        out_df = out_df[keep_cols]

        merged = df.merge(out_df, on="_SKU_SCRAPE_KEY", how="left").drop(columns=["_SKU_SCRAPE_KEY"])
        merged.to_excel(args.out, index=False)
        print(f"\nDone. Saved: {args.out}")

        if args.keep_open and not args.headless:
            input("\nScraping finished. Press Enter to close the browser...")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
