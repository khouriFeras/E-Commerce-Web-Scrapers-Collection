import argparse
import time

import pandas as pd
from playwright.sync_api import sync_playwright


def get_first_product_link(page):
    try:
        links = page.locator(
            ".product-list__inner a[href*='/products/']"
        ).all()
        if not links:
            return None
        href = links[0].get_attribute("href")
        if not href:
            return None
        if href.startswith("/"):
            href = "https://jo.fairuzy.com" + href
        return href
    except Exception:
        return None


def extract_description(page):
    try:
        return page.locator(
            ".product-form__description.rte"
        ).inner_text().strip()
    except Exception:
        return ""


def extract_images(page):
    images = set()
    try:
        imgs = page.locator(
            ".product__thumbnail-list-inner img"
        ).all()
        for img in imgs:
            src = (
                img.get_attribute("src")
                or img.get_attribute("data-src")
                or ""
            )
            if not src:
                continue
            src_lower = src.lower()
            if "thumbnail" in src_lower:
                continue
            if "preview_images" in src_lower:
                continue
            if ".mp4" in src_lower:
                continue
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("/"):
                src = "https://jo.fairuzy.com" + src
            src = src.split("?")[0]
            images.add(src)
    except Exception:
        pass
    return ";".join(sorted(images))


def process_product(page, product_name):
    search_url = "https://jo.fairuzy.com/ar/search?q=" + product_name
    page.goto(search_url, wait_until="networkidle", timeout=60000)
    time.sleep(2)

    product_link = get_first_product_link(page)
    if not product_link:
        return "", "", "NO_RESULT"

    page.goto(product_link, wait_until="networkidle", timeout=60000)
    time.sleep(2)

    description = extract_description(page)
    images = extract_images(page)
    return description, images, "MATCHED"


def main():
    parser = argparse.ArgumentParser(description="Fairuzy scraper")
    parser.add_argument("--in",   dest="input",   required=True,  help="Input Excel path")
    parser.add_argument("--out",  dest="output",  required=True,  help="Output Excel path")
    parser.add_argument("--sku-col", dest="sku_col", default="Product Name", help="Column to search by")
    parser.add_argument("--headful", action="store_true", help="Show browser window")
    args = parser.parse_args()

    df = pd.read_excel(args.input)

    sku_col = args.sku_col if args.sku_col in df.columns else df.columns[0]
    print(f"Using column: {sku_col}")

    descriptions, images_col, status_col = [], [], []
    total = len(df)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        page = browser.new_page()

        for idx, row in df.iterrows():
            product_name = str(row[sku_col]).strip()
            print(f"[{idx+1}/{total}] {product_name}")

            # Skip already-done rows (resume support)
            try:
                existing = str(df.at[idx, "Images"]).strip()
                if existing not in ("", "nan"):
                    print("  -> SKIP (already done)")
                    descriptions.append(df.at[idx, "Description"])
                    images_col.append(df.at[idx, "Images"])
                    status_col.append(df.at[idx, "Status"])
                    continue
            except Exception:
                pass

            try:
                desc, imgs, status = process_product(page, product_name)
            except Exception as e:
                desc, imgs, status = "", "", f"ERROR: {e}"

            descriptions.append(desc)
            images_col.append(imgs)
            status_col.append(status)
            print(f"  -> {status}")

        browser.close()

    result_df = df.copy()
    result_df["Description"] = descriptions
    result_df["Images"] = images_col
    result_df["Status"] = status_col
    result_df.to_excel(args.output, index=False)

    print("\nDONE")
    print(f"Saved -> {args.output}")


if __name__ == "__main__":
    main()