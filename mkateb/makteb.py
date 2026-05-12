import os
import time
import pandas as pd
import openpyxl

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


BASE_URL = "https://mkateb.com"


def make_driver():
    opts = webdriver.ChromeOptions()
    opts.add_argument("--start-maximized")
    # opts.add_argument("--headless=new")  # optional
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=opts)
    return driver


def normalize_url(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BASE_URL + href
    return href


def get_first_product_url(driver, wait) -> str:
    """
    Mkateb search results are typically OpenCart-style cards under #content.
    Product links are SEO slugs (NOT /product/...), so we find by structure.
    """
    # Wait for results area (either products exist OR "no products" message exists)
    wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "#content")
        )
    )

    # Sometimes there is a "no products" message; detect it early
    no_results_text_candidates = driver.find_elements(By.CSS_SELECTOR, "#content p")
    for p in no_results_text_candidates[:5]:
        t = (p.text or "").strip().lower()
        if "no products" in t or "there is no product" in t or "no results" in t:
            return ""

    # Best selectors for first product title link
    selectors = [
        "#content .product-thumb h4 a",
        "#content .product-layout h4 a",
        "#content h4 a",  # fallback (still scoped to #content)
    ]

    for sel in selectors:
        links = driver.find_elements(By.CSS_SELECTOR, sel)
        # Filter only real product links (exclude empty href and obvious non-product anchors)
        cleaned = []
        for a in links:
            href = a.get_attribute("href")
            href = normalize_url(href)
            if not href:
                continue
            # Exclude pagination / compare / account, keep item pages
            bad_keywords = ["route=account", "compare", "wishlist", "checkout", "page="]
            if any(k in href.lower() for k in bad_keywords):
                # NOTE: some product links include page= & search= params; don't exclude too aggressively
                pass
            cleaned.append(href)

        if cleaned:
            return cleaned[0]

    return ""


def extract_product_data(driver, wait):
    # Wait for product title
    title = ""
    desc = ""
    images = []

    try:
        h1 = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#content h1")))
        title = (h1.text or "").strip()
    except TimeoutException:
        title = ""

    # Description: OpenCart usually has #tab-description
    desc_selectors = [
        "#tab-description",
        "#content .tab-content",
        "#content .panel-body.tb_product_description",
        "#content .tb_product_description",
    ]
    for sel in desc_selectors:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            text = (els[0].text or "").strip()
            if text:
                desc = text
                break

    # Images: prefer large image anchors inside thumbnails
    # Common OpenCart structure: ul.thumbnails a (href=large image)
    thumb_anchor_selectors = [
        "#content ul.thumbnails a",
        "#content .thumbnails a",
        "#content .tb_thumbs_wrap a",
        "#content .tb-thumbs-wrap a",
    ]
    seen = set()

    for sel in thumb_anchor_selectors:
        anchors = driver.find_elements(By.CSS_SELECTOR, sel)
        for a in anchors:
            href = a.get_attribute("href")
            href = normalize_url(href)
            if not href:
                continue
            # keep image-like
            low = href.lower()
            if any(ext in low for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                href = href.split("?")[0]
                if href not in seen:
                    seen.add(href)
                    images.append(href)
        if images:
            break

    # Fallback: main image
    if not images:
        img_selectors = [
            "#content .image a",
            "#content .image img",
            "#content .tb-product-image img",
            "#content img",
        ]
        for sel in img_selectors:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els[:5]:
                href = el.get_attribute("href") or el.get_attribute("src") or el.get_attribute("data-src")
                href = normalize_url(href)
                if href and any(ext in href.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    href = href.split("?")[0]
                    if href not in seen:
                        seen.add(href)
                        images.append(href)
            if images:
                break

    return title, desc, images


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    excel_file = os.path.join(script_dir, "Jafar shop Products list (1)futher home.xlsx")

    wb = openpyxl.load_workbook(excel_file)
    actual_sheet_names = wb.sheetnames

    all_results = {}

    driver = make_driver()
    wait = WebDriverWait(driver, 20)

    try:
        for actual_sheet_name in actual_sheet_names:
            sheet_display_name = actual_sheet_name.strip()
            print(f"\n{'='*60}")
            print(f"Processing Sheet: {sheet_display_name}")
            print(f"{'='*60}\n")

            try:
                df = pd.read_excel(excel_file, sheet_name=actual_sheet_name)

                # Find Product Code column robustly
                product_code_col = None
                for col in df.columns:
                    if str(col).strip().lower() == "product code":
                        product_code_col = col
                        break

                if product_code_col is None:
                    print(f"Warning: 'Product Code' column not found in sheet {sheet_display_name}")
                    print(f"Available columns: {list(df.columns)}")
                    df.columns = df.columns.str.strip()
                    all_results[sheet_display_name] = df
                    continue

                df.columns = df.columns.str.strip()
                product_code_col_stripped = str(product_code_col).strip()

                # Initialize output columns
                for c in ["scraped_url", "scraped_title", "scraped_description", "scraped_images", "scraping_error"]:
                    if c not in df.columns:
                        df[c] = ""

                for index, row in df.iterrows():
                    raw_code = row.get(product_code_col_stripped, "")
                    product_code = str(raw_code).strip()

                    if pd.isna(raw_code) or not product_code or product_code.lower() == "nan":
                        print(f"  Skipping row {index + 1}: Empty Product Code")
                        continue

                    search_url = f"{BASE_URL}/product/search?search={product_code}"
                    print(f"  Processing [{sheet_display_name}] Row {index + 1}/{len(df)}: {product_code}")

                    try:
                        driver.get(search_url)

                        # find first product link properly (NOT /product/)
                        product_url = get_first_product_url(driver, wait)

                        if not product_url:
                            print("    ✗ No products found in search results")
                            df.at[index, "scraped_url"] = search_url
                            df.at[index, "scraping_error"] = "No products found in search results"
                            continue

                        print(f"    → Navigating to: {product_url}")
                        driver.get(product_url)

                        title, desc, images = extract_product_data(driver, wait)

                        df.at[index, "scraped_url"] = driver.current_url
                        df.at[index, "scraped_title"] = title
                        df.at[index, "scraped_description"] = desc
                        df.at[index, "scraped_images"] = "; ".join(images)
                        df.at[index, "scraping_error"] = ""

                        print(f"    ✓ Successfully scraped: {title[:60]}")

                    except Exception as e:
                        err = str(e)
                        print(f"    ✗ Error: {err}")
                        df.at[index, "scraped_url"] = search_url
                        df.at[index, "scraping_error"] = err

                    time.sleep(0.4)

                all_results[sheet_display_name] = df
                print(f"\n✓ Completed sheet: {sheet_display_name}")

            except Exception as e:
                print(f"Error processing sheet {sheet_display_name}: {e}")
                all_results[sheet_display_name] = pd.DataFrame()

    finally:
        driver.quit()

    if all_results:
        output_file = os.path.join(script_dir, "makteb_scraped.xlsx")
        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            for sheet_name, out_df in all_results.items():
                if not out_df.empty:
                    # Excel sheet name limit safety
                    safe_name = sheet_name[:31]
                    out_df.to_excel(writer, sheet_name=safe_name, index=False)

        print(f"\n{'='*60}")
        print("✓ Scraping completed!")
        print(f"✓ Results saved to: {output_file}")
        print(f"✓ Total sheets processed: {len(all_results)}")
        print(f"{'='*60}")
    else:
        print("\n✗ No results to save.")


if __name__ == "__main__":
    main()
