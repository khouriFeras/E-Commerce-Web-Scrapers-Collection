#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://en.titizplastik.com/"
DEFAULT_INPUT = "Titiz.XLSX"
DEFAULT_OUTPUT = "Titiz_scraped.xlsx"

CODE_COL_CANDIDATES = ["Item CodeS", "Item Code", "Code", "SKU", "sku"]
MAX_IMAGES_DEFAULT = 100
WAIT_SEC = 20
PAUSE_SEC = 1.0


def build_driver(headless: bool) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1500,1200")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL.rstrip("/") + url
    return url


def pick_code_column(df: pd.DataFrame) -> str:
    normalized = {str(c).strip().lower(): c for c in df.columns}
    for candidate in CODE_COL_CANDIDATES:
        hit = normalized.get(candidate.strip().lower())
        if hit is not None:
            return hit
    return df.columns[0]


def wait_visible_search_input(driver: webdriver.Chrome, wait: WebDriverWait):
    def _find(_):
        for element in driver.find_elements(By.CSS_SELECTOR, "input#q"):
            if element.is_displayed() and element.is_enabled():
                return element
        return False

    return wait.until(_find)


def maybe_open_search_overlay(driver: webdriver.Chrome) -> None:
    openers = [
        ".search-btn",
        ".search",
        "a[title*='Search']",
        "a[aria-label*='Search']",
        "button[aria-label*='Search']",
        ".fa-search",
        ".icon-search",
    ]
    for selector in openers:
        for elem in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if elem.is_displayed() and elem.is_enabled():
                    driver.execute_script("arguments[0].click();", elem)
                    time.sleep(0.3)
                    return
            except Exception:
                continue


def first_visible(elements: List) -> Optional[object]:
    for elem in elements:
        try:
            if elem.is_displayed() and elem.is_enabled():
                return elem
        except Exception:
            continue
    return None


def get_first_product_link(driver: webdriver.Chrome, wait: WebDriverWait):
    selectors = [
        "a[href*='/urun/']",
        "a[href*='/product/']",
        "a[href*='ProductDetail']",
        ".product-item a[href]",
        ".products a[href]",
        ".urun a[href]",
    ]

    def _pick(_):
        for sel in selectors:
            elems = driver.find_elements(By.CSS_SELECTOR, sel)
            elem = first_visible(elems)
            if elem is not None:
                return elem
        return False

    return wait.until(_pick)


def find_best_result_anchor(driver: webdriver.Chrome, item_code: str):
    code_upper = str(item_code or "").strip().upper()
    excluded_parts = (
        "/products",
        "/personal-data",
        "/Home#/",
        "/news",
        "/about",
        "/contact",
    )
    candidates: List[tuple[int, object]] = []
    for anchor in driver.find_elements(By.XPATH, "//a[@href]"):
        try:
            href = (anchor.get_attribute("href") or "").strip()
            text = " ".join((anchor.text or "").split())
            if not href.startswith("http"):
                continue
            if any(part.lower() in href.lower() for part in excluded_parts):
                continue
            if "titizplastik.com" not in href.lower():
                continue
            score = 0
            if text:
                score += 5
            if code_upper and (code_upper in text.upper() or code_upper in href.upper()):
                score += 100
            if text.lower() == "read more":
                score += 15
            if "/arama/" in href.lower():
                score -= 80
            if "/en-us/" in href.lower():
                score += 10
            candidates.append((score, anchor))
        except Exception:
            continue

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def submit_hidden_search_form(driver: webdriver.Chrome, item_code: str) -> None:
    input_elements = driver.find_elements(By.ID, "q")
    if not input_elements:
        raise TimeoutException("Input #q not found on page")
    form = driver.find_element(By.ID, "searchform")
    driver.execute_script("arguments[0].value = arguments[1];", input_elements[0], item_code)
    driver.execute_script("arguments[0].submit();", form)


def open_first_search_result(
    driver: webdriver.Chrome,
    wait: WebDriverWait,
    item_code: str,
) -> Dict[str, str]:
    result = {"status": "not_found", "note": "No search result clicked"}
    driver.get(BASE_URL)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(0.5)

    try:
        search_input = wait_visible_search_input(driver, wait)
    except TimeoutException:
        maybe_open_search_overlay(driver)
        try:
            search_input = wait_visible_search_input(driver, wait)
        except TimeoutException:
            submit_hidden_search_form(driver, str(item_code).strip())
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(PAUSE_SEC)
            best = find_best_result_anchor(driver, item_code)
            if best is None:
                _ = get_first_product_link(driver, wait)
                best = find_best_result_anchor(driver, item_code)
            if best is None:
                raise TimeoutException("No product link found after hidden-form submit")
            driver.execute_script("arguments[0].click();", best)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            result["status"] = "ok"
            result["note"] = "Submitted hidden #q form and clicked top result"
            return result

    search_input.click()
    search_input.send_keys(Keys.CONTROL, "a")
    search_input.send_keys(Keys.DELETE)
    search_input.send_keys(str(item_code).strip())
    time.sleep(0.8)

    suggestion_selectors = [
        ".search_result a[href]",
        ".search-results a[href]",
        ".autocomplete a[href]",
        "ul[role='listbox'] a[href]",
    ]
    for selector in suggestion_selectors:
        elems = driver.find_elements(By.CSS_SELECTOR, selector)
        first = first_visible(elems)
        if first is not None:
            driver.execute_script("arguments[0].click();", first)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            result["status"] = "ok"
            result["note"] = f"Clicked first suggestion using {selector}"
            return result

    search_input.send_keys(Keys.ENTER)
    time.sleep(PAUSE_SEC)
    best = find_best_result_anchor(driver, item_code)
    first_product = best if best is not None else get_first_product_link(driver, wait)
    driver.execute_script("arguments[0].click();", first_product)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    result["status"] = "ok"
    result["note"] = "Clicked first product result after Enter search"
    return result


def image_identity_key(url: str) -> str:
    path = urlparse(url).path.lower()
    name = path.split("/")[-1]
    name = re.sub(r"-\d{2,4}x\d{2,4}(?=\.[a-z0-9]+$)", "", name)
    return name or path


def image_size_score(url: str) -> int:
    u = url.lower()
    score = 0
    for match in re.finditer(r"(\d{2,4})x(\d{2,4})", u):
        w, h = int(match.group(1)), int(match.group(2))
        score = max(score, w * h)
    if "large" in u or "zoom" in u or "original" in u or "full" in u:
        score += 1000
    if "thumb" in u or "thumbnail" in u:
        score -= 8000
    if "small" in u:
        score -= 3000
    if score == 0:
        score = 400
    return score


def pick_largest_images(candidates: List[str], max_images: int = 0) -> List[str]:
    best: Dict[str, tuple[int, str]] = {}
    order: List[str] = []
    for raw in candidates:
        normalized = normalize_url(raw)
        if not normalized.startswith("http"):
            continue
        if "placeholder" in normalized.lower():
            continue
        key = image_identity_key(normalized)
        if key not in order:
            order.append(key)
        score = image_size_score(normalized)
        if key not in best or score > best[key][0]:
            best[key] = (score, normalized)
    output = [best[key][1] for key in order if key in best]
    if max_images and max_images > 0:
        return output[:max_images]
    return output


def extract_images(driver: webdriver.Chrome, max_images: int, item_code: str) -> List[str]:
    selectors = [
        ".product-detail img",
        ".product-images img",
        ".owl-item img",
        ".swiper-slide img",
        ".gallery img",
        "img",
    ]
    candidates: List[str] = []

    for selector in selectors:
        elems = driver.find_elements(By.CSS_SELECTOR, selector)
        for img in elems:
            for attr in (
                "data-zoom-image",
                "data-large-image",
                "data-src",
                "data-original",
                "data-lazy",
                "src",
            ):
                value = img.get_attribute(attr)
                if value:
                    candidates.append(value)
            srcset = img.get_attribute("srcset") or ""
            for part in srcset.split(","):
                part = part.strip()
                if part:
                    candidates.append(part.split()[0])

    ranked = pick_largest_images(candidates, max_images=0)
    filtered: List[str] = []
    for url in ranked:
        low = normalize_url(url).lower()
        if "/upload/dosyalar/" not in low:
            continue
        if "logo" in low or "product-property" in low:
            continue
        filtered.append(normalize_url(url))

    code_sig = re.sub(r"[^a-z0-9]+", "", str(item_code or "").lower())
    if code_sig:
        matched = []
        for url in filtered:
            norm = re.sub(r"[^a-z0-9]+", "", url.lower())
            if code_sig in norm:
                matched.append(url)
        if matched:
            return matched[:max_images] if max_images > 0 else matched

    return filtered[:max_images] if max_images > 0 else filtered


def scrape_one_item(
    driver: webdriver.Chrome,
    wait: WebDriverWait,
    item_code: str,
    max_images: int,
) -> Dict[str, object]:
    out: Dict[str, object] = {
        "searched_item_code": item_code,
        "status": "not_found",
        "note": "",
        "images": [],
    }
    code = str(item_code or "").strip()
    if not code or code.lower() == "nan":
        out["status"] = "empty_code"
        out["note"] = "Item code is empty"
        return out

    try:
        click_state = open_first_search_result(driver, wait, code)
        out["status"] = click_state["status"]
        out["note"] = click_state["note"]
        out["images"] = extract_images(driver, max_images=max_images, item_code=code)
        if out["status"] == "ok" and not out["images"]:
            out["status"] = "no_images"
            out["note"] = "Product opened but no images found"
    except TimeoutException:
        out["status"] = "timeout"
        out["note"] = "Timeout while searching/clicking result"
    except Exception as exc:
        out["status"] = "error"
        out["note"] = f"{type(exc).__name__}: {exc}"
    return out


def parse_bool(s: str) -> bool:
    return str(s).strip().lower() in {"1", "true", "yes", "y"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Titiz scraper (search by Item CodeS).")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Input Excel path.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output Excel path.")
    parser.add_argument("--headless", default="false", help="true/false. Default false.")
    parser.add_argument(
        "--max-images",
        type=int,
        default=MAX_IMAGES_DEFAULT,
        help="Max image count (0 means all).",
    )
    parser.add_argument("--limit", type=int, default=0, help="Only process first N rows (0=all).")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = script_dir / input_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = script_dir / output_path

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    df = pd.read_excel(input_path)
    code_col = pick_code_column(df)
    print(f"Using code column: {code_col}")

    if "imgs" not in df.columns:
        df["imgs"] = ""

    headless = parse_bool(args.headless)
    driver = build_driver(headless=headless)
    wait = WebDriverWait(driver, WAIT_SEC)

    try:
        total = len(df) if args.limit <= 0 else min(len(df), args.limit)
        print(f"Rows to process: {total}")
        for idx in range(total):
            item_code = df.at[idx, code_col]
            print(f"[{idx + 1}/{total}] searching code={item_code}")
            result = scrape_one_item(driver, wait, str(item_code), max_images=args.max_images)

            images: List[str] = result["images"] if isinstance(result["images"], list) else []
            df.at[idx, "imgs"] = ";".join(images)

            print(
                f"   -> status={result['status']} images={len(images)}"
            )
            time.sleep(PAUSE_SEC)
    finally:
        driver.quit()

    df.to_excel(output_path, index=False)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
