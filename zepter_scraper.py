#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Zepter JO scraper (explicit search-click flow)

Flow per user spec:
1) Open https://shop.zepter.com.jo/en-JO/
2) Click the search button: button.extra-menu-icon[data-item-type="search"]
3) Type the SKU (Item Code) into the search field (handles iframe/contenteditable)
4) Click the first result / suggestion
5) Scrape Overview / QUALITY / TRADITION / Technical data into a single "description" column

Edit the CONFIG section below.
Dependencies: pip install selenium pandas openpyxl
(Use Selenium ≥ 4.10 so Selenium Manager auto-fetches the correct driver.)
"""

import re, time
from pathlib import Path
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, WebDriverException

# ============================
# CONFIG — edit here
# ============================
INPUT_FILE  = r"data\Jafarshop Stock Update - Feb 6.xlsx - Sheet1 (1).xlsx"  # your Excel
OUTPUT_FILE = r"zepter_out.xlsx"
SKU_COL     = "Item Code"      # column that holds the SKU you want to search
HEADLESS    = False             # set True on server; keep False now to watch it work
SLEEP       = 0.8
TIMEOUT     = 25
SAVE_DEBUG  = True              # saves HTML/PNG on failures under ./debug/
# ============================

HOME = "https://shop.zepter.com.jo/en-JO/"

SECTION_HEADINGS = [
    ("Overview", ["overview presentation", "overview", "presentation"]),
    ("QUALITY", ["quality"]),
    ("TRADITION", ["tradition"]),
    ("Technical data", ["technical data", "tech data", "specifications", "specs"]),
]


def make_driver():
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1360,2200")
    # quieter logs
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--log-level=3")
    d = webdriver.Chrome(options=opts)  # Selenium Manager picks the right driver
    d.set_page_load_timeout(60)
    return d


def clean_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s).strip()


def visible_text(el) -> str:
    try:
        txt = el.get_attribute("textContent") or el.text or ""
    except StaleElementReferenceException:
        txt = el.text or ""
    return clean_text(txt)


def ensure_debug_dir():
    if SAVE_DEBUG:
        Path("debug").mkdir(exist_ok=True)


def save_debug(driver, label):
    if not SAVE_DEBUG:
        return
    ensure_debug_dir()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:80]
    try:
        with open(Path("debug") / f"{safe}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass
    try:
        driver.save_screenshot(str(Path("debug") / f"{safe}.png"))
    except Exception:
        pass


# ---------- Search UI helpers ----------

def wait_for_overlay(driver, timeout=10):
    # Common patterns: a backdrop or overlay container shows up
    sel_candidates = [
        ".search", ".search-overlay", ".search-modal", ".overlay--search",
        "div[class*='search'][class*='overlay']", "div[id*='search'][class*='overlay']",
        "div[role='dialog']",
    ]
    end = time.time() + timeout
    while time.time() < end:
        for sel in sel_candidates:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    return True
            except Exception:
                pass
        time.sleep(0.1)
    return False


def click_search_button(driver):
    """Click the search toggle button the user specified."""
    btn = driver.find_element(By.CSS_SELECTOR, "button.extra-menu-icon[data-item-type='search']")
    btn.click()
    time.sleep(0.3)
    wait_for_overlay(driver, timeout=5)


def switch_into_search_iframe_if_any(driver):
    """Some sites put the search field inside an iframe; switch in if found."""
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
    for fr in frames:
        try:
            src = (fr.get_attribute("src") or "").lower()
            name = (fr.get_attribute("name") or "").lower()
            title = (fr.get_attribute("title") or "").lower()
            if any(k in (src + name + title) for k in ["search", "header", "overlay"]):
                driver.switch_to.frame(fr)
                return True
        except Exception:
            continue
    return False


def find_search_input(driver):
    """Find the text input that appears after clicking the search button."""
    trials = [
        "input[type='search']",
        "input[name='q']",
        "input[name='search']",
        "input[name='searchTerm']",
        "input[id*='search']",
        "form[role='search'] input",
        ".search input[type='text']",
        ".search input",
        "input#search",
    ]
    for sel in trials:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed() and el.is_enabled():
                return el
        except Exception:
            continue

    # Fallback: contenteditable search field
    try:
        el = driver.find_element(By.CSS_SELECTOR, "[contenteditable='true']")
        if el.is_displayed():
            return el
    except Exception:
        pass

    # Last resort: active element
    try:
        el = driver.switch_to.active_element
        if el and el.is_displayed():
            return el
    except Exception:
        pass

    return None


def js_type_and_submit(driver, element, text):
    """Use JS to set value & dispatch events (covers React/Vue/etc.), then press Enter."""
    driver.execute_script(
        """
        const el = arguments[0], val = arguments[1];
        if (!el) return;
        if ('value' in el) {
            el.value = '';
            el.value = val;
        } else if (el.isContentEditable) {
            el.textContent = '';
            el.textContent = val;
        }
        const evts = ['input','change','keyup','keydown','keypress'];
        evts.forEach(t => el.dispatchEvent(new Event(t, {bubbles:true})));
        """,
        element,
        text,
    )
    try:
        element.send_keys(Keys.ENTER)
    except Exception:
        # Try triggering form submit via JS
        driver.execute_script(
            """
            let e = arguments[0];
            while (e && e.tagName && e.tagName.toLowerCase() !== 'form') e = e.parentElement;
            if (e && e.submit) e.submit();
            """,
            element,
        )


def looks_like_product_link(href: str) -> bool:
    """Accept category-style deep product URLs (no /p/ needed)."""
    if not href or "javascript:" in href:
        return False
    h = href.lower()
    if "/en-jo/" in h or "/en-jo/" in h.replace("jo", "JO") or "/en-JO/" in href:
        # require some depth to avoid category root
        path = href.split("?")[0]
        depth = len([p for p in path.split("/") if p])
        return depth >= 4
    return False


def click_first_result(driver, wait) -> bool:
    """Click the first product result shown after typing the SKU.
    Supports several common suggestion/results containers.
    """
    # 1) Autocomplete/suggestions list (preferred)
    suggestion_selectors = [
        "ul[role='listbox'] li a, ul[role='listbox'] li",
        ".autocomplete li a, .autocomplete li",
        ".ui-autocomplete li a, .ui-autocomplete li",
        ".search-suggestions li a, .search-suggestions li",
        ".search-results li a, .search-results a",
    ]
    for sel in suggestion_selectors:
        try:
            items = WebDriverWait(driver, 2).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, sel))
            )
            for it in items:
                if it.is_displayed():
                    href = it.get_attribute("href") or ""
                    if not href or looks_like_product_link(href):
                        it.click()
                        return True
        except Exception:
            continue

    # 2) If it navigated to a results page, click first product-like link
    grid_selectors = [
        ".product a[href]", ".product-list a[href]", ".grid a[href]",
        "main a[href]",
    ]
    for sel in grid_selectors:
        try:
            links = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, sel)))
            for a in links:
                href = a.get_attribute("href") or ""
                if looks_like_product_link(href):
                    a.click()
                    return True
        except TimeoutException:
            continue
        except Exception:
            continue

    return False


def open_product_by_clicking_search(driver, wait, sku: str) -> bool:
    """Exact flow requested by user: open → click search → type SKU → choose first result."""
    driver.get(HOME)
    time.sleep(SLEEP)

    # Step 1: click the search icon
    try:
        click_search_button(driver)
    except Exception:
        save_debug(driver, f"no_search_button_{sku}")
        return False

    # Step 2: if the search is in an iframe, switch in
    switched = switch_into_search_iframe_if_any(driver)

    # Step 3: find the search field
    box = find_search_input(driver)
    if not box:
        # If we switched into an iframe and still didn't find it, try root again
        if switched:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            click_search_button(driver)
            box = find_search_input(driver)
        if not box:
            save_debug(driver, f"no_search_input_{sku}")
            return False

    # Step 4: try variants and force-typing via JS
    variants = [sku, sku.replace("-", ""), sku.replace("-", " "), sku.lower()]
    for q in variants:
        try:
            # Focus & clear (using both JS and keys)
            try:
                box.click()
            except Exception:
                pass
            # Type and submit with JS-backed events
            js_type_and_submit(driver, box, q)
            time.sleep(SLEEP)

            # Click first result (suggestion or grid)
            if click_first_result(driver, wait):
                # If we were inside an iframe, switch back to default for the product page
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                return True

            # If an H1 appears (landed directly), accept
            try:
                WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1")))
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                return True
            except TimeoutException:
                # Re-open search box (some UIs close it after submit without results)
                try:
                    if switched:
                        driver.switch_to.default_content()
                        switched = False
                    click_search_button(driver)
                    switched = switch_into_search_iframe_if_any(driver)
                    box = find_search_input(driver)
                except Exception:
                    pass
        except Exception:
            # Try to recover search UI
            try:
                if switched:
                    driver.switch_to.default_content()
                    switched = False
                click_search_button(driver)
                switched = switch_into_search_iframe_if_any(driver)
                box = find_search_input(driver)
            except Exception:
                pass

    save_debug(driver, f"not_found_{sku}")
    # Ensure we leave any iframe
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return False


# ---------- scraping ----------

def extract_table_like(driver):
    tables = driver.find_elements(By.CSS_SELECTOR, "table")
    best, score = None, -1
    for t in tables:
        try:
            rows = t.find_elements(By.CSS_SELECTOR, "tr")
            r = len(rows)
            c = max((len(row.find_elements(By.CSS_SELECTOR, "th,td")) for row in rows), default=0)
            sc = r * c
            if sc > score:
                best, score = t, sc
        except Exception:
            continue
    if not best or score < 2:
        return None
    lines = []
    for tr in best.find_elements(By.CSS_SELECTOR, "tr"):
        cells = tr.find_elements(By.CSS_SELECTOR, "th,td")
        vals = [visible_text(td) for td in cells]
        vals = [v for v in vals if v]
        if not vals:
            continue
        if len(vals) == 1:
            lines.append(vals[0])
        else:
            lines.append(f"{vals[0]}: {' '.join(vals[1:])}")
    out = " ; ".join(lines)
    return out if len(out) > 10 else None


def extract_section(driver, variants):
    XPATHS = [
        ".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6][contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
        ".//*[self::button or self::a][contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
        ".//div[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
    ]
    for needle in variants:
        n = needle.lower()
        for xp in XPATHS:
            q = xp.format(needle=n)
            try:
                heads = driver.find_elements(By.XPATH, q)
            except Exception:
                continue
            for h in heads:
                try:
                    tag = (h.tag_name or "").lower()
                    if tag in {"button", "a"}:
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", h)
                            h.click(); time.sleep(0.25)
                        except Exception:
                            pass
                    container = h
                    for _ in range(3):
                        container = container.find_element(By.XPATH, "..")
                    txt = visible_text(container)
                    if len(txt) < 60:
                        sibs = h.find_elements(By.XPATH, "following-sibling::*")
                        txt2 = " ".join(visible_text(s) for s in sibs[:4])
                        if len(txt2) > len(txt):
                            txt = txt2
                    txt = clean_text(txt)
                    if len(txt) > 60:
                        return txt
                except Exception:
                    continue
    return None


def scrape_description(driver) -> str:
    try:
        WebDriverWait(driver, TIMEOUT).until(EC.presence_of_element_located((By.CSS_SELECTOR, "main, body")))
    except TimeoutException:
        return ""
    parts = []
    for human, variants in SECTION_HEADINGS:
        txt = extract_section(driver, variants)
        if txt:
            parts.append(f"{human}: {txt}")
    if not any(p.lower().startswith(("technical data:", "tech data:")) for p in parts):
        tab = extract_table_like(driver)
        if tab:
            parts.append(f"Technical data: {tab}")
    return "\n".join(parts)


# ---------- main ----------

def main():
    df = pd.read_excel(INPUT_FILE)
    if SKU_COL not in df.columns:
        raise SystemExit(f"Column '{SKU_COL}' not found. Available: {list(df.columns)}")

    driver = make_driver()
    wait = WebDriverWait(driver, TIMEOUT)
    out_desc = []

    try:
        for i, row in df.iterrows():
            sku = str(row.get(SKU_COL, "")).strip()
            if not sku:
                out_desc.append(""); continue

            try:
                opened = open_product_by_clicking_search(driver, wait, sku)
            except WebDriverException:
                # soft reset once
                try: driver.get("about:blank")
                except Exception: pass
                opened = open_product_by_clicking_search(driver, wait, sku)

            if not opened:
                print(f"[{i}] {sku}: NOT FOUND via search click")
                out_desc.append(""); continue

            time.sleep(SLEEP)
            desc = ""
            try:
                desc = scrape_description(driver)
            except Exception as e:
                print(f"[{i}] {sku}: scrape error: {e}")
                desc = ""

            print(f"[{i}] {sku}: {'OK' if desc else 'EMPTY'}")
            out_desc.append(desc)
            time.sleep(SLEEP)
    finally:
        try: driver.quit()
        except Exception: pass

    df = df.copy()
    df["description"] = out_desc
    Path(OUTPUT_FILE).parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(OUTPUT_FILE, index=False)
    print(f"Saved {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
