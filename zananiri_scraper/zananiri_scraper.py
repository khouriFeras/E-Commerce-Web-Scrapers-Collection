#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zananiri_scraper.py

يبحث عن كل SKU من عمود محدد بملف الإكسل بموقع zananirijo.com، ويجيب الوصف
(Description) فقط، ويضيفه كعمود جديد بدون ما يلمس أي عمود أو بيانات قديمة
موجودة بالملف.

هاد الإصدار معدّل ليصير على نفس نمط RCCScraper.py:
  - يقرأ كل الشيتات بالملف (مش شيت واحد بس)
  - checkpoint: يحفظ التقدم كل N صف، حتى لو وقع الكود بالنص ما تخسر شغل
  - resume: لو شغّلت الكود مرة ثانية على نفس --out، بيتخطى الصفوف يلي
    خلصت MATCHED مسبقاً وبيكمل بس الباقي
  - بعد ما يخلص، بيقسم النتيجة لملفين: <out>-found.xlsx و <out>-notfound.xlsx
  - أسماء الأعمدة الجديدة (Description/Status/Product URL) آمنة: إذا كانت
    موجودة بالملف الأصلي أصلاً، ما بيعمل لها overwrite - بيضيف عمود جديد
    بإسم مختلف (Description_2 إلخ)
  - اختصار مهم: إذا الملف عنده عمود فيه رابط صفحة المنتج جاهز (مثل عمود
    "images" بملف Tefal)، بيستخدمه مباشرة بدل ما يعمل بحث بالموقع، وهاد
    أسرع وأدق. إذا مافي رابط جاهز أو الرابط فشل، بيرجع يعمل بحث عادي.

الاستخدام:
    python zananiri_scraper.py --in "input.xlsx" --out "output.xlsx" --sku-col "Part No."

    # تحديد شيت معين بس
    python zananiri_scraper.py --in "input.xlsx" --out "output.xlsx" --sheet Sheet1

    # تجربة على أول 5 صفوف بس
    python zananiri_scraper.py --in "input.xlsx" --out "output.xlsx" --limit 5

    # تحديد اسم عمود الرابط الجاهز يدوياً (بدل ما يخمنه الكود)
    python zananiri_scraper.py --in "input.xlsx" --out "output.xlsx" --link-col "images"

    # تعطيل استخدام الرابط الجاهز والاعتماد على البحث فقط
    python zananiri_scraper.py --in "input.xlsx" --out "output.xlsx" --no-direct-link

المتطلبات:
    pip install playwright pandas openpyxl
    playwright install chromium
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
except ImportError:  # allow --dry-run / structural tests without playwright installed
    sync_playwright = None
    Page = object  # type: ignore
    class PlaywrightTimeoutError(Exception):  # type: ignore
        pass


# =========================
# CONFIG
# =========================

DESIRED_ADDED_COLUMNS = ["title", "description", "image_urls", "status", "product_url_used"]

BASE_URL = "https://www.zananirijo.com"
SEARCH_URL_TEMPLATE = BASE_URL + "/search?q={sku}&options%5Bprefix%5D=last"

PRODUCT_GRID_SELECTOR = "#items-grid"
PRODUCT_LINK_SELECTOR = '#items-grid a[href*="/products/"]'
DESCRIPTION_SELECTOR = "div.x-block-description .rte"

# Title: try common Shopify theme selectors first, og:title meta as fallback.
TITLE_SELECTORS = [
    "h1.product-title",
    "h1.product__title",
    "h1.product-single__title",
    ".product-meta h1",
    "h1.x-product-title",
    "h1",
]

# Image gallery: confirmed from actual zananirijo.com product page markup.
# Main image sits inside a button labelled "Image zoom"; thumbnails sit
# inside buttons labelled "image-thumbnai" (typo on the live site - we match
# both the typo and the correct spelling for safety).
MAIN_IMAGE_SELECTOR = 'button[aria-label="Image zoom"] img, img.image-detail'
THUMB_IMAGE_SELECTOR = (
    'button[aria-label="image-thumbnai"] img, '
    'button[aria-label="image-thumbnail"] img'
)
# Last-resort fallback if the selectors above ever find nothing (theme change etc.):
# any <img> pointing at Shopify's product file CDN, anywhere on the page.
CDN_FILE_IMG_SELECTOR = (
    'img[src*="/cdn/shop/files/"], img[data-src*="/cdn/shop/files/"], '
    'img[srcset*="/cdn/shop/files/"], img[data-srcset*="/cdn/shop/files/"]'
)

NAV_TIMEOUT = 30000
ELEMENT_TIMEOUT = 10000

# Column names commonly used to hold a ready-made product link in the
# original sheet (checked in this order, case-insensitive).
LIKELY_LINK_COLUMNS = ["images", "image", "product_url", "product url", "url", "link"]


# ---------------------------------------------------------------------------
# Collision-safe column naming (same idea as RCCScraper._safe_new_col)
# ---------------------------------------------------------------------------


def _safe_new_col(desired: str, existing_cols) -> str:
    existing = {str(c).strip().lower() for c in existing_cols}
    if desired.lower() not in existing:
        return desired
    i = 2
    candidate = f"{desired}_{i}"
    while candidate.lower() in existing:
        i += 1
        candidate = f"{desired}_{i}"
    return candidate


class _ScrapeCols:
    """Collision-safe column names for one sheet, computed once from that
    sheet's ORIGINAL columns (before we add anything). Re-running the script
    always yields the same names because we compute from the ORIGINAL cols,
    not from a previous run's output."""

    def __init__(self, original_cols):
        self.names: Dict[str, str] = {}
        used: List[str] = list(original_cols)
        for desired in DESIRED_ADDED_COLUMNS:
            # Preserve backward-compatible fixed names when possible so the
            # older "Description"/"Status" columns keep working.
            pretty = {
                "title": "Title",
                "description": "Description",
                "image_urls": "Image URLs",
                "status": "Status",
                "product_url_used": "Product URL Used",
            }[desired]
            name = _safe_new_col(pretty, used)
            self.names[desired] = name
            used.append(name)

    def __getitem__(self, desired: str) -> str:
        return self.names[desired]

    def all_cols(self) -> List[str]:
        return list(self.names.values())


# ---------------------------------------------------------------------------
# Pacer (base sleep + jitter), same idea as RCCScraper.Pacer
# ---------------------------------------------------------------------------


class Pacer:
    def __init__(self, base: float = 1.0, jitter: float = 0.3):
        self.base = max(0.0, float(base))
        self.jitter = max(0.0, float(jitter))

    def wait(self) -> None:
        lo = self.base * (1 - self.jitter)
        hi = self.base * (1 + self.jitter)
        time.sleep(random.uniform(max(0.0, lo), max(0.0, hi)))


# ---------------------------------------------------------------------------
# Core scraping
# ---------------------------------------------------------------------------


@dataclass
class ScrapeResult:
    title: str = ""
    description: str = ""
    images: List[str] = None  # type: ignore
    status: str = "NO_RESULT"
    product_url: str = ""

    def __post_init__(self):
        if self.images is None:
            self.images = []


# ---------------------------------------------------------------------------
# Image URL normalization/dedupe (Shopify CDN size variants)
# ---------------------------------------------------------------------------

import re as _re

# Shopify CDN size suffix right before the extension, e.g. "...product_600x600.jpg"
# or "...product_600x.jpg" (width-only). Also handles "?width=1024" query params.
_SHOPIFY_SIZE_SUFFIX_RE = _re.compile(r"_(\d{2,5})x(\d{0,5})(?=\.[a-zA-Z0-9]{2,5}(?:$|\?))")


def _abs_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return BASE_URL.rstrip("/") + u
    return u


def _normalize_image_key(u: str) -> str:
    u = u.split("#", 1)[0]
    u = _re.sub(r"[?&](?:width|height|w|h|v)=[^&]+", "", u)
    u = _re.sub(r"[?&]+$", "", u)
    u = _SHOPIFY_SIZE_SUFFIX_RE.sub("", u)
    return u.lower()


def _size_score_from_url(u: str) -> int:
    ul = u.lower()
    score = 0
    m = _SHOPIFY_SIZE_SUFFIX_RE.search(ul)
    if m:
        w = int(m.group(1))
        h = int(m.group(2)) if m.group(2) else w
        score = max(score, w * h)
    m = _re.search(r"[?&]width=(\d{2,5})", ul)
    if m:
        w = int(m.group(1))
        score = max(score, w * w)
    # No size suffix at all -> likely the original upload, treat as largest.
    if not _SHOPIFY_SIZE_SUFFIX_RE.search(ul):
        score = max(score, 3_000_000)
    return score


def _dedupe_images(urls) -> List[str]:
    order: List[str] = []
    key_to_idx: Dict[str, int] = {}
    key_to_score: Dict[str, int] = {}
    for raw in urls:
        u = _abs_url(raw)
        if not u or u.startswith("data:"):
            continue
        ul = u.lower()
        if any(tag in ul for tag in ("logo", "favicon", "sprite", "loading", "spinner", "placeholder", "icon-")):
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


def get_first_product_link(page: "Page", sku: str, max_attempts: int = 2) -> Optional[str]:
    search_url = SEARCH_URL_TEMPLATE.format(sku=sku)
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(search_url, wait_until="networkidle", timeout=NAV_TIMEOUT)
            try:
                page.wait_for_selector(PRODUCT_GRID_SELECTOR, timeout=ELEMENT_TIMEOUT)
            except PlaywrightTimeoutError:
                return None

            link_locator = page.locator(PRODUCT_LINK_SELECTOR).first
            if link_locator.count() == 0:
                return None

            href = link_locator.get_attribute("href")
            if not href:
                return None

            href = href.strip()
            if href.startswith("/"):
                href = BASE_URL + href
            return href
        except Exception as e:  # transient nav errors -> retry once
            last_exc = e
            if attempt < max_attempts:
                time.sleep(1.5)
                continue
    if last_exc:
        raise last_exc
    return None


def _extract_title_from_dom(page: "Page") -> str:
    for sel in TITLE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                t = loc.inner_text().strip()
                if t:
                    return t
        except Exception:
            continue
    # og:title meta fallback - present on almost every Shopify product page.
    try:
        meta = page.locator('meta[property="og:title"]').first
        if meta.count() > 0:
            content = meta.get_attribute("content")
            if content:
                return content.strip()
    except Exception:
        pass
    return ""


def _collect_img_attrs(locator, limit: int) -> List[str]:
    out: List[str] = []
    try:
        n = locator.count()
    except Exception:
        return out
    for i in range(min(n, limit)):
        img = locator.nth(i)
        for attr in ("src", "data-src"):
            try:
                v = img.get_attribute(attr)
            except Exception:
                v = None
            if v:
                out.append(v)
        for attr in ("srcset", "data-srcset"):
            try:
                v = img.get_attribute(attr)
            except Exception:
                v = None
            if v:
                for piece in v.split(","):
                    piece = piece.strip().split(" ")[0]
                    if piece:
                        out.append(piece)
    return out


def _extract_images_from_dom(page: "Page") -> List[str]:
    candidates: List[str] = []

    # 1) Precise, site-specific selectors (main zoom image first, then thumbnails).
    try:
        candidates.extend(_collect_img_attrs(page.locator(MAIN_IMAGE_SELECTOR), limit=10))
    except Exception:
        pass
    try:
        candidates.extend(_collect_img_attrs(page.locator(THUMB_IMAGE_SELECTOR), limit=40))
    except Exception:
        pass

    # 2) Fallback: any <img> pointing at Shopify's product file CDN, anywhere on the page.
    if not candidates:
        try:
            candidates.extend(_collect_img_attrs(page.locator(CDN_FILE_IMG_SELECTOR), limit=40))
        except Exception:
            pass

    # 3) og:image meta as a last resort / to make sure the main image is present.
    try:
        meta = page.locator('meta[property="og:image"]').first
        if meta.count() > 0:
            content = meta.get_attribute("content")
            if content:
                candidates.insert(0, content)
    except Exception:
        pass

    return _dedupe_images(candidates)


def extract_product_details(page: "Page", product_url: str, max_attempts: int = 2) -> Tuple[str, str, List[str]]:
    """Load a product page once and pull title + description + gallery images."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(product_url, wait_until="networkidle", timeout=NAV_TIMEOUT)

            title = _extract_title_from_dom(page)
            images = _extract_images_from_dom(page)

            try:
                page.wait_for_selector(DESCRIPTION_SELECTOR, timeout=ELEMENT_TIMEOUT)
                description_locator = page.locator(DESCRIPTION_SELECTOR).first
                description = description_locator.inner_text() if description_locator.count() > 0 else ""
            except PlaywrightTimeoutError:
                description = ""

            return title, (description or ""), images
        except Exception as e:
            last_exc = e
            if attempt < max_attempts:
                time.sleep(1.5)
                continue
    if last_exc:
        raise last_exc
    return "", "", []


def process_product(page: "Page", sku: str, direct_link: Optional[str] = None) -> ScrapeResult:
    """
    Scrape one SKU. If `direct_link` is given (a product URL already present
    in the sheet), try it first - skips the search step entirely, which is
    faster and avoids picking the wrong search result. Falls back to a
    normal site search if the direct link is missing or fails.
    """
    try:
        if direct_link:
            try:
                title, description, images = extract_product_details(page, direct_link)
                if description or title or images:
                    return ScrapeResult(
                        title=title, description=description, images=images,
                        status="MATCHED_DIRECT", product_url=direct_link,
                    )
                # direct link loaded but nothing found -> fall through to search
            except Exception:
                pass  # direct link failed -> fall back to search below

        product_link = get_first_product_link(page, sku)
        if not product_link:
            return ScrapeResult(status="NO_RESULT")

        title, description, images = extract_product_details(page, product_link)
        if not description and not title and not images:
            return ScrapeResult(status="NO_DESCRIPTION", product_url=product_link)

        return ScrapeResult(
            title=title, description=description, images=images,
            status="MATCHED_SEARCH", product_url=product_link,
        )

    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return ScrapeResult(status=f"ERROR: {exc}")


# ---------------------------------------------------------------------------
# Excel IO / header handling (split-header support kept from original)
# ---------------------------------------------------------------------------


def _find_column(cols, name: str) -> Optional[str]:
    key = str(name).strip().lower()
    for c in cols:
        if str(c).strip().lower() == key:
            return c
    return None


def _detect_link_column(cols, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return _find_column(cols, explicit)
    for candidate in LIKELY_LINK_COLUMNS:
        found = _find_column(cols, candidate)
        if found:
            return found
    return None


def load_workbook_sheets(path: str, sku_col: str) -> Dict[str, pd.DataFrame]:
    """
    Reads every sheet in the workbook. Supports the case where the header is
    split across two rows (common in Shopify exports, and in the Tefal
    file), where row 0 has a placeholder in the first cell and row 1 has the
    real column names.
    """
    xl = pd.ExcelFile(path)
    sheets: Dict[str, pd.DataFrame] = {}
    for name in xl.sheet_names:
        raw = xl.parse(name, header=None, dtype=str)
        if len(raw) < 2:
            sheets[name] = xl.parse(name)
            continue

        row0 = raw.iloc[0]
        row1 = raw.iloc[1]
        row1_values = {str(v).strip() for v in row1 if pd.notna(v)}

        looks_like_split_header = (
            sku_col in row1_values
            or "Variant SKU" in row1_values
            or "Variant Inventory Qty" in row1_values
        )

        if looks_like_split_header:
            columns = []
            for col_idx in range(raw.shape[1]):
                val0 = row0[col_idx]
                val1 = row1[col_idx]
                val0 = str(val0).strip() if pd.notna(val0) else ""
                val1 = str(val1).strip() if pd.notna(val1) else ""
                columns.append(val1 if val1 else (val0 if val0 else f"Unnamed: {col_idx}"))
            df = raw.iloc[2:].reset_index(drop=True)
            df.columns = columns
        else:
            df = xl.parse(name)

        sheets[name] = df
    return sheets


def write_checkpoint(path: str, sheets: Dict[str, pd.DataFrame]) -> None:
    tmp = path + ".part.xlsx"
    with pd.ExcelWriter(tmp, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=str(name)[:31] or "Sheet1", index=False)
    os.replace(tmp, path)


def write_found_notfound(sheets: Dict[str, pd.DataFrame], status_cols: Dict[str, str], output_path: str) -> None:
    base, ext = os.path.splitext(output_path)
    found_path = f"{base}-found{ext}"
    notfound_path = f"{base}-notfound{ext}"

    found_sheets: Dict[str, pd.DataFrame] = {}
    notfound_sheets: Dict[str, pd.DataFrame] = {}

    for name, df in sheets.items():
        status_col = status_cols.get(name)
        if status_col and status_col in df.columns:
            is_found = df[status_col].astype(str).str.startswith("MATCHED")
            found_sheets[name] = df[is_found]
            notfound_sheets[name] = df[~is_found]
        else:
            notfound_sheets[name] = df

    if found_sheets:
        with pd.ExcelWriter(found_path, engine="openpyxl") as w:
            for name, df in found_sheets.items():
                df.to_excel(w, sheet_name=str(name)[:31] or "Sheet1", index=False)
        print(f"Found saved to: {found_path}")

    if notfound_sheets:
        with pd.ExcelWriter(notfound_path, engine="openpyxl") as w:
            for name, df in notfound_sheets.items():
                df.to_excel(w, sheet_name=str(name)[:31] or "Sheet1", index=False)
        print(f"Not found saved to: {notfound_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    input_file: str,
    output_file: str,
    sku_col: str = "Part No.",
    sheet_filter: Optional[List[str]] = None,
    limit: int = 0,
    sleep: float = 1.0,
    jitter: float = 0.3,
    checkpoint_every: int = 10,
    link_col: Optional[str] = None,
    use_direct_link: bool = True,
    dry_run: bool = False,
    _page_factory=None,
) -> int:
    """
    Core run loop, split out from main() so it can be called directly
    (e.g. from a test harness with a fake `_page_factory`) without going
    through argparse/sys.exit.
    """
    if not os.path.isfile(input_file):
        print(f"الملف مش موجود: {input_file}")
        return 1

    sheets = load_workbook_sheets(input_file, sku_col)
    sheet_names = list(sheets.keys())
    if sheet_filter:
        requested = {s.strip().lower() for s in sheet_filter}
        sheet_names = [s for s in sheet_names if s.strip().lower() in requested]
        if not sheet_names:
            print(f"ما في شيتات مطابقة. الشيتات الموجودة: {list(sheets.keys())}")
            return 1

    # Resume support: reuse a previous --out if present.
    prev_sheets: Dict[str, pd.DataFrame] = {}
    if os.path.isfile(output_file):
        try:
            prev_xl = pd.ExcelFile(output_file)
            for name in prev_xl.sheet_names:
                prev_sheets[name] = prev_xl.parse(name)
            print(f"لاقيت ملف نتائج سابق ({output_file}): رح أكمل من وين ما وقفت")
        except Exception as e:
            print(f"تحذير: ما قدرت أقرا الملف السابق ({e})، رح أبلش من جديد")

    pacer = Pacer(base=sleep, jitter=jitter)

    out_sheets: Dict[str, pd.DataFrame] = {}
    status_cols: Dict[str, str] = {}
    total_rows = 0
    total_matched = 0

    playwright_ctx = None
    browser = None
    page = None
    if not dry_run:
        if _page_factory is not None:
            page = _page_factory()
        else:
            if sync_playwright is None:
                print("playwright مش مثبت. شغّل: pip install playwright && playwright install chromium")
                return 1
            playwright_ctx = sync_playwright().start()
            browser = playwright_ctx.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(NAV_TIMEOUT)

    try:
        for sheet_idx, sheet in enumerate(sheet_names, 1):
            df = sheets[sheet]
            found_sku_col = _find_column(df.columns, sku_col)
            if found_sku_col is None:
                print(f"[{sheet_idx}/{len(sheet_names)}] {sheet!r}: عمود {sku_col!r} مش موجود، تم التخطي")
                out_sheets[sheet] = df.copy()
                continue

            link_col_name = _detect_link_column(df.columns, link_col) if use_direct_link else None
            if link_col_name:
                print(f"  رابط جاهز لقيته بعمود: {link_col_name!r} (رح يتستخدم قبل البحث)")

            cols = _ScrapeCols(list(df.columns))
            status_col = cols["status"]
            status_cols[sheet] = status_col

            prev = prev_sheets.get(sheet)
            if prev is not None and len(prev) == len(df) and status_col in prev.columns:
                work_df = prev
                print(f"  (بكمّل شيت {sheet!r} من نتيجة سابقة)")
            else:
                work_df = df.copy()
                for col in cols.all_cols():
                    work_df[col] = ""

            for col in cols.all_cols():
                work_df[col] = work_df[col].astype(object)

            n_rows = len(work_df)
            if limit and limit > 0:
                n_rows = min(n_rows, limit)

            print(f"\n=== شيت {sheet_idx}/{len(sheet_names)}: {sheet} ({n_rows} صف) ===")

            for idx in range(n_rows):
                row_idx = work_df.index[idx]

                prev_status = str(work_df.at[row_idx, status_col] or "")
                if prev_status.startswith("MATCHED") or prev_status == "SKIPPED_HAS_VALUE":
                    print(f"  [{idx+1:>3}/{n_rows}] خلص مسبقاً ({prev_status}) - تم التخطي")
                    total_rows += 1
                    if prev_status.startswith("MATCHED"):
                        total_matched += 1
                    continue

                raw_sku = work_df.iloc[idx][found_sku_col]
                if pd.isna(raw_sku) or str(raw_sku).strip() == "":
                    work_df.at[row_idx, status_col] = "SKIPPED_EMPTY_SKU"
                    print(f"  [{idx+1:>3}/{n_rows}] <SKU فاضي> - تم التخطي")
                    continue

                sku = str(raw_sku).strip()

                direct_link = None
                if link_col_name:
                    raw_link = work_df.iloc[idx][link_col_name]
                    if pd.notna(raw_link) and str(raw_link).strip().startswith("http"):
                        direct_link = str(raw_link).strip()

                if dry_run:
                    result = ScrapeResult(
                        title=f"[DRY_RUN] title for {sku}",
                        description=f"[DRY_RUN] would fetch sku={sku} link={bool(direct_link)}",
                        images=[f"https://cdn.shopify.com/{sku}_main.jpg", f"https://cdn.shopify.com/{sku}_thumb1.jpg"],
                        status="MATCHED_DIRECT" if direct_link else "MATCHED_SEARCH",
                        product_url=direct_link or "",
                    )
                else:
                    try:
                        result = process_product(page, sku, direct_link=direct_link)
                    except Exception as e:
                        result = ScrapeResult(status=f"ERROR: {e}")

                work_df.at[row_idx, cols["title"]] = result.title
                work_df.at[row_idx, cols["description"]] = result.description
                work_df.at[row_idx, cols["image_urls"]] = ";".join(result.images)
                work_df.at[row_idx, status_col] = result.status
                work_df.at[row_idx, cols["product_url_used"]] = result.product_url

                total_rows += 1
                if result.status.startswith("MATCHED"):
                    total_matched += 1

                print(f"  [{idx+1:>3}/{n_rows}] {sku:<16} -> {result.status}")

                if (idx + 1) % checkpoint_every == 0:
                    out_sheets[sheet] = work_df
                    try:
                        write_checkpoint(output_file, out_sheets)
                    except Exception as e:
                        print(f"  تحذير أثناء حفظ checkpoint: {e}")

                if not dry_run:
                    pacer.wait()

            out_sheets[sheet] = work_df
            try:
                write_checkpoint(output_file, out_sheets)
                print(f"  checkpoint اتحفظ: {output_file}")
            except Exception as e:
                print(f"  فشل حفظ checkpoint: {e}")
    finally:
        try:
            if out_sheets:
                write_checkpoint(output_file, out_sheets)
        except Exception as e:
            print(f"فشل الحفظ النهائي: {e}", file=sys.stderr)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright_ctx is not None:
            try:
                playwright_ctx.stop()
            except Exception:
                pass

    try:
        if out_sheets:
            write_found_notfound(out_sheets, status_cols, output_file)
    except Exception as e:
        print(f"فشل تقسيم found/notfound: {e}", file=sys.stderr)

    print(f"\nخلص: {total_matched}/{total_rows} لقيت وصف | الملف: {output_file}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Zananiri JO scraper")
    ap.add_argument("--in", dest="input_file", required=True, help="Input Excel path")
    ap.add_argument("--out", dest="output_file", required=True, help="Output Excel path")
    ap.add_argument("--sku-col", dest="sku_col", default="Part No.", help="Column name to search by")
    ap.add_argument("--sheet", action="append", default=[], help="Restrict to a sheet (repeatable)")
    ap.add_argument("--limit", type=int, default=0, help="Limit rows per sheet (0 = no limit)")
    ap.add_argument("--sleep", type=float, default=1.0, help="Base delay between requests (s)")
    ap.add_argument("--jitter", type=float, default=0.3, help="Jitter fraction for sleep")
    ap.add_argument("--checkpoint-every", type=int, default=10, help="Save progress every N rows")
    ap.add_argument("--link-col", default=None, help="Column that already holds a product URL to try first")
    ap.add_argument("--no-direct-link", action="store_true", help="Ignore any ready-made product link column, always search")
    ap.add_argument("--dry-run", action="store_true", help="Run all the file/checkpoint/resume logic without touching the network (no browser needed)")
    args = ap.parse_args(argv)

    return run(
        input_file=args.input_file,
        output_file=args.output_file,
        sku_col=args.sku_col,
        sheet_filter=args.sheet,
        limit=args.limit,
        sleep=args.sleep,
        jitter=args.jitter,
        checkpoint_every=args.checkpoint_every,
        link_col=args.link_col,
        use_direct_link=not args.no_direct_link,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())