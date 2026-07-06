#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RRC (https://rrc.jo/en/) scraper.

Reads MODEL values from every sheet of RCC/RCC.xlsx, submits each one to the
site's WooCommerce product search, opens the first matching product page,
validates that the exact model string appears on the page, and scrapes the
product title, full description, and every unique gallery image URL (kept at
the highest resolution available).

Usage:
    python RCC/RCCScraper.py
    python RCC/RCCScraper.py --sheet NUTRICOOK --limit 5
    python RCC/RCCScraper.py --sleep 2.0 --jitter 0.5 --proxy http://127.0.0.1:8888
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, unquote, urljoin, urlparse
from urllib.parse import parse_qsl, urlencode, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


# The input sheets can contain Arabic "Name" values. On Windows the default
# console encoding is cp1252, which raises UnicodeEncodeError when we log those
# rows. Force UTF-8 with replacement so a stray Arabic character never aborts
# the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


BASE = "https://rrc.jo/"
SEARCH_TMPL = "https://rrc.jo/?s={q}&post_type=product"

DEFAULT_INPUT = os.path.join(os.path.dirname(__file__), "RRC-not-in-store.xlsx")
DEFAULT_OUTPUT = os.path.join(os.path.dirname(__file__), "RCC_scraped.xlsx")

ADDED_COLUMNS = [
    "validate",
    "scraped_title",
    "description",
    "image_urls",
    "image_count",
    "product_url",
    "status",
]


# ---------------------------------------------------------------------------
# HTTP session: realistic browser headers + retries + rate limiting
# ---------------------------------------------------------------------------

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "DNT": "1",
}


def make_session(proxy: Optional[str] = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    return s


def warmup(session: requests.Session) -> None:
    """Prime cookies by visiting the homepage like a real visitor."""
    try:
        session.get(BASE, timeout=30)
    except Exception:
        pass


def polite_get(
    session: requests.Session,
    url: str,
    *,
    referer: Optional[str] = None,
    max_attempts: int = 3,
) -> requests.Response:
    """GET with retries and exponential backoff on transient/block errors."""
    headers: Dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    backoff = 1.0
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            r = session.get(url, headers=headers, timeout=30, allow_redirects=True)
        except Exception as e:
            last_exc = e
            if attempt == max_attempts:
                raise
            time.sleep(backoff)
            backoff *= 2.5
            continue

        if r.status_code in (429, 403, 500, 502, 503, 504):
            if attempt == max_attempts:
                return r
            time.sleep(backoff)
            backoff *= 2.5
            continue
        return r

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"polite_get exhausted attempts for {url}")


class Pacer:
    """Rate limiter: base sleep with jitter; a longer pause every N requests."""

    def __init__(self, base: float = 1.5, jitter: float = 0.4, long_every: int = 25):
        self.base = max(0.0, float(base))
        self.jitter = max(0.0, float(jitter))
        self.long_every = max(1, int(long_every))
        self._n = 0

    def wait(self) -> None:
        self._n += 1
        lo = self.base * (1 - self.jitter)
        hi = self.base * (1 + self.jitter)
        time.sleep(random.uniform(max(0.0, lo), max(0.0, hi)))
        if self._n % self.long_every == 0:
            time.sleep(random.uniform(3.0, 6.0))


# ---------------------------------------------------------------------------
# URL helpers + image dedupe (preserve order, keep the largest variant)
# ---------------------------------------------------------------------------


def _abs_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return urljoin(BASE, u)
    return u


def _clean_product_url(u: str) -> str:
    """Normalize a product URL and drop cart/action query params."""
    au = _abs_url(u)
    if not au:
        return ""
    p = urlparse(au)
    if "/product/" not in (p.path or ""):
        return au
    keep = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in {"add-to-cart", "quantity"}]
    q = urlencode(keep, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, q, p.fragment))


# Strip WordPress size suffixes like `-600x600`, `-64x65`, `-scaled` right before
# the file extension, plus common resize query params. The result collapses all
# thumbnails of one image to the same normalization key.
_WP_SIZE_SUFFIX_RE = re.compile(r"-\d{2,5}x\d{2,5}(?=\.[a-zA-Z0-9]{2,5}(?:$|\?))")
_WP_SCALED_SUFFIX_RE = re.compile(r"-scaled(?=\.[a-zA-Z0-9]{2,5}(?:$|\?))")


def _normalize_image_key(u: str) -> str:
    u = u.split("#", 1)[0]
    u = re.sub(r"[?&](?:w|h|width|height|fit|resize|quality|format|auto|ssl)=[^&]+", "", u)
    u = re.sub(r"[?&]+$", "", u)
    u = _WP_SIZE_SUFFIX_RE.sub("", u)
    u = _WP_SCALED_SUFFIX_RE.sub("", u)
    return u.lower()


def _size_score_from_url(u: str) -> int:
    if not u:
        return 0
    ul = u.lower()
    score = 0
    m = re.search(r"-(\d{2,5})x(\d{2,5})(?=\.[a-z0-9]{2,5}(?:$|\?))", ul)
    if m:
        score = max(score, int(m.group(1)) * int(m.group(2)))
    else:
        m = re.search(r"(\d{2,5})x(\d{2,5})", ul)
        if m:
            score = max(score, int(m.group(1)) * int(m.group(2)))
    m = re.search(r"[?&](?:w|width)=(\d{2,5})", ul)
    if m:
        w = int(m.group(1))
        score = max(score, w * w)
    # URLs without a size suffix are usually the original -> boost them.
    if not re.search(r"-\d{2,5}x\d{2,5}\.[a-z0-9]{2,5}(?:$|\?)", ul):
        score = max(score, 3_000_000)
    return score


def _original_variant(u: str) -> str:
    """Try to produce the original (unsized) version of a WP image URL."""
    base = u.split("?", 1)[0]
    base = _WP_SIZE_SUFFIX_RE.sub("", base)
    return base


def _dedupe_images(urls: Iterable[str]) -> List[str]:
    order: List[str] = []
    key_to_idx: Dict[str, int] = {}
    key_to_score: Dict[str, int] = {}

    for raw in urls:
        u = _abs_url(raw)
        if not u or u.startswith("data:"):
            continue
        ul = u.lower()
        if any(tag in ul for tag in ("logo", "favicon", "sprite", "loading", "spinner", "placeholder")):
            continue
        key = _normalize_image_key(u)
        score = _size_score_from_url(u)
        if key not in key_to_idx:
            key_to_idx[key] = len(order)
            key_to_score[key] = score
            order.append(u)
        elif score > key_to_score[key]:
            order[key_to_idx[key]] = u
            key_to_score[key] = score
    return order


# ---------------------------------------------------------------------------
# Page parsers
# ---------------------------------------------------------------------------




_MODEL_TOKEN_RE = re.compile(r"[A-Za-z0-9]{3,}")


def _slug_of(url: str) -> str:
    try:
        path = urlparse(url).path
    except Exception:
        return ""
    decoded = unquote(path)
    return decoded.rstrip("/").split("/")[-1]


def _score_product_link(url: str, model: str) -> int:
    """
    Rank candidate product URLs so we prefer the English storefront version
    whose slug actually references the MODEL, instead of the Arabic-language
    duplicate that WooCommerce sometimes lists first.
    """
    if not url:
        return -1
    try:
        path = urlparse(url).path
    except Exception:
        return 0
    score = 0
    if path.startswith("/en/product/"):
        score += 100
    elif path.startswith("/product/"):
        score += 10

    slug = _slug_of(url)
    if slug:
        non_ascii = sum(1 for c in slug if ord(c) > 127)
        ratio = non_ascii / len(slug)
        if ratio == 0:
            score += 50
        elif ratio < 0.2:
            score += 25

    tokens = _MODEL_TOKEN_RE.findall(model or "")
    slug_l = slug.lower()
    for tok in tokens:
        if tok.lower() in slug_l:
            score += 40
    return score


def _find_first_product_url(
    soup: BeautifulSoup, final_url: str, model: str = ""
) -> Optional[str]:
    """Return the first product URL in visible listing order."""
    candidates: List[str] = []
    final_is_product = "/product/" in urlparse(final_url).path
    if final_is_product:
        candidates.append(_clean_product_url(final_url))

    # Only treat listing-page hits as candidates; on a product page those same
    # selectors match "Related products" which we do not want to override
    # the redirect target with.
    if not final_is_product:
        for sel in (
            "ul.products li.product a.woocommerce-LoopProduct-link",
            "a.woocommerce-LoopProduct-link",
            "ul.products li.product a[href*='/product/']",
            ".site-main a[href*='/product/']",
        ):
            for a in soup.select(sel):
                href = (a.get("href") or "").strip()
                if href and "/product/" in href:
                    cleaned = _clean_product_url(href)
                    if cleaned:
                        candidates.append(cleaned)
            if candidates:
                break

    if not candidates:
        return None

    seen: set = set()
    unique: List[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    # Prefer English /en/product/ URLs whose slug references the model over
    # Arabic-language duplicates that WooCommerce sometimes lists first.
    unique.sort(key=lambda u: _score_product_link(u, model), reverse=True)
    return unique[0]


def _looks_like_empty_product_page(soup: BeautifulSoup) -> bool:
    """Detect responses that loaded but do not contain product content."""
    has_title = bool(_extract_title(soup))
    has_desc = bool(
        soup.select_one(".woocommerce-product-details__short-description")
        or soup.select_one("#tab-description")
        or soup.select_one(".woocommerce-Tabs-panel--description")
    )
    has_gallery = bool(soup.select_one(".woocommerce-product-gallery img"))
    return not (has_title or has_desc or has_gallery)


def _extract_title(soup: BeautifulSoup) -> str:
    for sel in ("h1.product_title", "h1.entry-title", "h1"):
        el = soup.select_one(sel)
        if el:
            t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
            if t:
                return t
    meta = soup.select_one("meta[property='og:title']")
    if meta and meta.get("content"):
        return meta["content"].strip()
    return ""


def _container_to_text(container) -> str:
    """Render a BeautifulSoup element to newline-preserved plain text."""
    node = BeautifulSoup(str(container), "html.parser")

    for tag in node.find_all(["script", "style", "noscript"]):
        tag.decompose()

    for br in node.find_all("br"):
        br.replace_with("\n")

    for block in node.find_all(["p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]):
        block.append("\n")

    text = node.get_text(" ", strip=False)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def _extract_description(soup: BeautifulSoup) -> str:
    """
    Build the description as: <short summary>\\n\\n<full tab description>.

    WooCommerce renders the headline specs inside
    `.woocommerce-product-details__short-description` (above the gallery) and
    the long copy inside `#tab-description`. We keep both, short first, and
    fall back to whichever one is available if the other is missing.
    """
    parts: List[str] = []

    short_container = soup.select_one(".woocommerce-product-details__short-description")
    short_text = _container_to_text(short_container) if short_container is not None else ""
    if short_text:
        parts.append(short_text)

    long_container = (
        soup.select_one("#tab-description")
        or soup.select_one(".woocommerce-Tabs-panel--description")
    )
    long_text = _container_to_text(long_container) if long_container is not None else ""
    if long_text:
        long_lines = long_text.split("\n")
        if long_lines and long_lines[0].lower() in ("description", "descriptions"):
            long_text = "\n".join(long_lines[1:]).strip()

        # Skip the long block if it already contains the short verbatim, so we
        # do not emit the same sentence twice.
        if long_text and (not short_text or short_text not in long_text):
            parts.append(long_text)

    if parts:
        return "\n\n".join(parts).strip()

    meta = soup.select_one("meta[property='og:description']")
    return (meta.get("content") if meta else "") or ""


def _extract_images(soup: BeautifulSoup) -> List[str]:
    gallery = soup.select_one(".woocommerce-product-gallery") or soup.select_one(
        ".product.type-product"
    )
    candidates: List[str] = []

    if gallery is not None:
        # Anchors wrapping the main image point to the full-size file.
        for a in gallery.select("a[href]"):
            href = a.get("href") or ""
            if re.search(r"\.(jpe?g|png|webp|avif|gif)(?:$|\?)", href, re.I):
                candidates.append(href)

        for img in gallery.select("img"):
            for attr in ("data-large_image", "data-src", "data-lazy-src", "data-lazy", "data-original", "src"):
                v = img.get(attr)
                if v:
                    candidates.append(v)
            srcset = img.get("srcset") or img.get("data-srcset") or ""
            for piece in srcset.split(","):
                piece = piece.strip().split(" ")[0]
                if piece:
                    candidates.append(piece)

        # Attempt to recover the original WP upload URL for each thumbnail.
        originals: List[str] = []
        for u in candidates:
            au = _abs_url(u)
            if au:
                originals.append(_original_variant(au))
        candidates = originals + candidates

    # og:image is almost always the main product shot at full size.
    meta = soup.select_one("meta[property='og:image']")
    if meta and meta.get("content"):
        candidates.insert(0, meta["content"])

    return _dedupe_images(candidates)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


@dataclass
class ProductResult:
    product_url: str = ""
    title: str = ""
    description: str = ""
    images: List[str] = field(default_factory=list)
    validate: str = ""
    status: str = "NOT_FOUND"


def _is_block_response(r: requests.Response) -> bool:
    if r.status_code in (403, 429, 503):
        return True
    body = r.text or ""
    if len(body) < 1000:
        return True
    low = body.lower()
    if any(x in low for x in ("cf-error-details", "attention required", "cf-browser-verification", "just a moment")):
        return True
    return False


def _fetch_search(
    session: requests.Session,
    search_url: str,
    *,
    fallback: Optional["SeleniumFallback"],
) -> Tuple[str, str, bool]:
    """Return (html, final_url, used_fallback) or raise on hard block."""
    r = polite_get(session, search_url, referer=BASE)
    if _is_block_response(r):
        if fallback is not None:
            html = fallback.fetch(search_url)
            return html, fallback.current_url() or search_url, True
        raise RuntimeError(f"blocked_{r.status_code}")
    return r.text, r.url, False


def _ascii_query(q: str) -> str:
    """Strip non-ASCII and extract printable tokens.

    The `Name` column often mixes Arabic copy with the Latin model code
    (e.g. 'ثلاجة هيتاشي R-V990PJ2-1U'). rrc.jo's English WooCommerce search
    doesn't match Arabic terms well, so when the raw query fails we fall back
    to this ASCII-only version which preserves the model code.
    """
    if not q:
        return ""
    ascii_only = "".join(ch if ord(ch) < 128 else " " for ch in q)
    return re.sub(r"\s+", " ", ascii_only).strip()


def search_and_scrape(
    session: requests.Session,
    model: str,
    *,
    fallback: Optional["SeleniumFallback"] = None,
) -> ProductResult:
    res = ProductResult()
    q = quote_plus(model)
    search_url = SEARCH_TMPL.format(q=q)

    try:
        html, final_url, used_fallback = _fetch_search(session, search_url, fallback=fallback)
    except RuntimeError as e:
        res.status = f"ERROR:{e}"
        return res

    soup = BeautifulSoup(html, "html.parser")
    product_url = _find_first_product_url(soup, final_url, model=model)

    # If the raw query has non-ASCII characters (Arabic) and the search came
    # back empty, retry with the extracted ASCII tokens. This recovers rows
    # like 'ثلاجة هيتاشي R-V990PJ2-1U' -> 'R-V990PJ2-1U'.
    if not product_url and any(ord(c) > 127 for c in model):
        ascii_model = _ascii_query(model)
        if ascii_model and ascii_model != model:
            time.sleep(1.0)
            alt_search_url = SEARCH_TMPL.format(q=quote_plus(ascii_model))
            try:
                html, final_url, _ = _fetch_search(
                    session, alt_search_url, fallback=fallback
                )
                soup = BeautifulSoup(html, "html.parser")
                product_url = _find_first_product_url(soup, final_url, model=ascii_model)
                if product_url:
                    search_url = alt_search_url
            except RuntimeError:
                pass

    # If the listing looked empty, it could be a transient glitch rather than a
    # real "no results" answer. Give the site one more chance with a short
    # cooldown before giving up, and consult the Selenium fallback as a last
    # resort if available.
    if not product_url:
        info = soup.select_one(".woocommerce-info")
        info_txt = info.get_text(" ", strip=True) if info else ""
        no_results_markers = (
            "no products were found",
            "nothing found",
            "lm yatim alathwrr",
        )
        looks_like_no_results = any(x in info_txt.lower() for x in no_results_markers)

        if not looks_like_no_results:
            time.sleep(3.0)
            try:
                html, final_url, used_fallback2 = _fetch_search(
                    session, search_url, fallback=fallback
                )
            except RuntimeError as e:
                res.status = f"ERROR:{e}"
                return res
            used_fallback = used_fallback or used_fallback2
            soup = BeautifulSoup(html, "html.parser")
            product_url = _find_first_product_url(soup, final_url, model=model)

        if not product_url and fallback is not None and not used_fallback:
            html = fallback.fetch(search_url)
            final_url = fallback.current_url() or search_url
            used_fallback = True
            soup = BeautifulSoup(html, "html.parser")
            product_url = _find_first_product_url(soup, final_url, model=model)

        if not product_url:
            info = soup.select_one(".woocommerce-info")
            msg = (info.get_text(" ", strip=True) if info else "") or "no results"
            res.status = f"NOT_FOUND:{msg[:60]}"
            return res

    if "/product/" in urlparse(final_url).path and final_url.rstrip("/") == product_url.rstrip("/"):
        product_soup = soup
    else:
        if used_fallback and fallback is not None:
            html = fallback.fetch(product_url)
        else:
            r2 = polite_get(session, product_url, referer=search_url)
            if _is_block_response(r2):
                if fallback is not None:
                    html = fallback.fetch(product_url)
                    used_fallback = True
                else:
                    res.product_url = product_url
                    res.status = f"ERROR:product_blocked_{r2.status_code}"
                    return res
            else:
                html = r2.text
        product_soup = BeautifulSoup(html, "html.parser")

    # Some transient responses are valid HTML shells but contain no product
    # content. Retry once with a direct non-fallback request before marking
    # the row as missing title/description/images.
    if _looks_like_empty_product_page(product_soup):
        try:
            clean_url = _clean_product_url(product_url)
            r3 = polite_get(session, clean_url, referer=search_url)
            if not _is_block_response(r3):
                retry_soup = BeautifulSoup(r3.text, "html.parser")
                if not _looks_like_empty_product_page(retry_soup):
                    product_soup = retry_soup
                    product_url = clean_url
        except Exception:
            pass

    res.product_url = product_url
    res.title = _extract_title(product_soup)
    res.description = _extract_description(product_soup)
    res.images = _extract_images(product_soup)

    missing = []
    if not res.title:
        missing.append("title")
    if not res.description:
        missing.append("description")
    if not res.images:
        missing.append("images")
    res.status = "SUCCESS" if not missing else f"PARTIAL:missing_{'-'.join(missing)}"
    return res


# ---------------------------------------------------------------------------
# Selenium stealth fallback (lazily created, only used if requests is blocked)
# ---------------------------------------------------------------------------


class SeleniumFallback:
    def __init__(self, headful: bool = False):
        self._headful = headful
        self._driver = None

    def _ensure(self):
        if self._driver is not None:
            return self._driver
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        if not self._headful:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--window-size=1440,1200")
        opts.add_argument(f"--user-agent={DEFAULT_HEADERS['User-Agent']}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(60)
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"},
            )
        except Exception:
            pass
        self._driver = driver
        return driver

    def fetch(self, url: str) -> str:
        d = self._ensure()
        d.get(url)
        time.sleep(1.5)
        return d.page_source

    def current_url(self) -> Optional[str]:
        if self._driver is None:
            return None
        try:
            return self._driver.current_url
        except Exception:
            return None

    def quit(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None


# ---------------------------------------------------------------------------
# Excel IO
# ---------------------------------------------------------------------------


def _find_query_column(df: pd.DataFrame, name: str = "model") -> Optional[str]:
    key = (name or "model").strip().lower()
    for c in df.columns:
        if str(c).strip().lower() == key:
            return c
    for c in df.columns:
        if key and key in str(c).strip().lower():
            return c
    return None


def _find_model_column(df: pd.DataFrame) -> Optional[str]:
    return _find_query_column(df, "model")


def write_checkpoint(path: str, sheets: Dict[str, pd.DataFrame]) -> None:
    tmp = path + ".part.xlsx"
    with pd.ExcelWriter(tmp, engine="openpyxl") as w:
        for name, df in sheets.items():
            safe_name = str(name)[:31] or "Sheet1"
            df.to_excel(w, sheet_name=safe_name, index=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "..."


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="RRC (rrc.jo) scraper")
    ap.add_argument("--input", default=DEFAULT_INPUT, help="Input XLSX path")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="Output XLSX path")
    ap.add_argument("--sheet", action="append", default=[], help="Restrict to a sheet (repeatable)")
    ap.add_argument("--limit", type=int, default=0, help="Limit rows per sheet (0 = no limit)")
    ap.add_argument("--sleep", type=float, default=1.5, help="Base delay between requests (s)")
    ap.add_argument("--jitter", type=float, default=0.4, help="Jitter fraction for sleep")
    ap.add_argument("--long-every", type=int, default=25, help="Insert a longer pause every N requests")
    ap.add_argument("--proxy", default=None, help="HTTP proxy, e.g. http://127.0.0.1:8888")
    ap.add_argument("--no-fallback", action="store_true", help="Disable the Selenium fallback")
    ap.add_argument("--headful", action="store_true", help="Run the Selenium fallback with a visible window")
    ap.add_argument(
        "--query-col",
        default="SKU",
        help='Column in the input sheet to use as the search query (default: "SKU")',
    )
    args = ap.parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    xl = pd.ExcelFile(args.input)
    sheet_names = list(xl.sheet_names)
    if args.sheet:
        requested = set(s.strip().lower() for s in args.sheet)
        sheet_names = [s for s in sheet_names if s.strip().lower() in requested]
        if not sheet_names:
            print(f"ERROR: no matching sheets. Available: {xl.sheet_names}", file=sys.stderr)
            return 2

    session = make_session(proxy=args.proxy)
    warmup(session)
    pacer = Pacer(base=args.sleep, jitter=args.jitter, long_every=args.long_every)
    fallback = None if args.no_fallback else SeleniumFallback(headful=True)

    out_sheets: Dict[str, pd.DataFrame] = {}
    total_rows = 0
    total_success = 0

    try:
        for sheet_idx, sheet in enumerate(sheet_names, 1):
            df = xl.parse(sheet)
            model_col = _find_query_column(df, args.query_col)
            if model_col is None:
                print(
                    f"[{sheet_idx}/{len(sheet_names)}] {sheet!r}: "
                    f"no column matching {args.query_col!r}, skipping"
                )
                out_sheets[sheet] = df.copy()
                continue

            work_df = df.copy()
            for col in ADDED_COLUMNS:
                if col not in work_df.columns:
                    work_df[col] = ""
                # pandas 3.x infers new string dtype for "" columns, which
                # rejects int assignment (image_count). Keep them object dtype.
                work_df[col] = work_df[col].astype(object)

            n_rows = len(work_df)
            if args.limit and args.limit > 0:
                n_rows = min(n_rows, args.limit)

            print(f"\n=== Sheet {sheet_idx}/{len(sheet_names)}: {sheet} ({n_rows} rows) ===")

            for idx in range(n_rows):
                raw_model = work_df.iloc[idx][model_col]
                if pd.isna(raw_model):
                    work_df.at[work_df.index[idx], "status"] = "SKIPPED:empty_model"
                    continue
                model = str(raw_model).strip()
                if not model:
                    work_df.at[work_df.index[idx], "status"] = "SKIPPED:empty_model"
                    continue

                try:
                    result = search_and_scrape(session, model, fallback=fallback)
                except Exception as e:
                    result = ProductResult(status=f"ERROR:{e.__class__.__name__}")

                row_idx = work_df.index[idx]
                work_df.at[row_idx, "validate"] = result.validate
                work_df.at[row_idx, "scraped_title"] = result.title
                work_df.at[row_idx, "description"] = result.description
                work_df.at[row_idx, "image_urls"] = ";".join(result.images)
                work_df.at[row_idx, "image_count"] = len(result.images)
                work_df.at[row_idx, "product_url"] = result.product_url
                work_df.at[row_idx, "status"] = result.status

                total_rows += 1
                if result.status.startswith("SUCCESS"):
                    total_success += 1

                print(
                    f"  [{idx+1:>3}/{n_rows}] {model!s:<24} -> {result.status:<28} "
                    f"img={len(result.images):<2} | {_truncate(result.title, 70)}"
                )

                if (idx + 1) % 10 == 0:
                    out_sheets[sheet] = work_df
                    try:
                        write_checkpoint(args.output, out_sheets)
                    except Exception as e:
                        print(f"  checkpoint warning: {e}")

                pacer.wait()

            out_sheets[sheet] = work_df
            try:
                write_checkpoint(args.output, out_sheets)
                print(f"  checkpoint written: {args.output}")
            except Exception as e:
                print(f"  checkpoint failed: {e}")
    finally:
        try:
            if out_sheets:
                write_checkpoint(args.output, out_sheets)
        except Exception as e:
            print(f"final write failed: {e}", file=sys.stderr)
        if fallback is not None:
            fallback.quit()

    print(
        f"\nDone: {total_success}/{total_rows} successful | output: {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
