#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys
import time
from typing import Dict, List, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import os
import pandas as pd


def build_driver(headful: bool = False) -> webdriver.Chrome:
	"""Build and return a configured Chrome WebDriver."""
	opts = Options()
	if not headful:
		opts.add_argument("--headless=new")
	opts.add_argument("--no-sandbox")
	opts.add_argument("--disable-gpu")
	opts.add_argument("--disable-dev-shm-usage")
	opts.add_argument("--window-size=1440,1200")
	opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
	driver = webdriver.Chrome(options=opts)
	driver.set_page_load_timeout(45)
	return driver


def safe_get_text(el) -> str:
	"""Return trimmed text content from an element, or empty string."""
	try:
		return (el.text or "").strip()
	except Exception:
		return ""


def wait_any(driver: webdriver.Chrome, conditions: List, timeout: int = 20):
	"""Wait until any of the provided expected_conditions is met."""
	return WebDriverWait(driver, timeout).until(EC.any_of(*conditions))


def open_search_page(driver: webdriver.Chrome, sku: str) -> None:
	"""Open PHC search page for the given SKU."""
	search_url = f"https://phc.com.jo/en/ecommerce/result?search={sku}"
	driver.get(search_url)


def find_first_product_link(driver: webdriver.Chrome) -> Optional[Tuple[str, object]]:
	"""
	Find the first product link in search results.
	Returns (href, element) or None if not found.
	"""
	# Wait for either products or "no results" message
	try:
		wait_any(
			driver,
			[
				EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='/product']")),
				EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='/products']")),
				EC.presence_of_all_elements_located((By.XPATH, "//*[contains(., 'No Result Found')]")),
			],
			timeout=20,
		)
	except TimeoutException:
		return None

	# Try several likely product link selectors (generic)
	candidate_selectors = [
		".product-card a[href]",
		".product a[href]",
		".item a[href]",
		".result a[href]",
		"main a[href*='/product']",
		"a[href*='/product']",
		"a[href*='/products']",
	]

	for sel in candidate_selectors:
		try:
			links = driver.find_elements(By.CSS_SELECTOR, sel)
			for link in links:
				href = link.get_attribute("href") or ""
				if not href:
					continue
				if any(x in href for x in ["/account", "/login", "/orders", "/cart", "/search", "/category", "/filter"]):
					continue
				return href, link
		except Exception:
			continue

	return None


def navigate_to_product(driver: webdriver.Chrome, link_el, href: str) -> str:
	"""Navigate to the product page using a click fallback to direct navigation."""
	old_url = driver.current_url
	try:
		driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link_el)
		time.sleep(0.3)
		link_el.click()
		try:
			WebDriverWait(driver, 10).until(EC.url_changes(old_url))
		except TimeoutException:
			driver.get(href)
	except Exception:
		try:
			driver.execute_script("arguments[0].click();", link_el)
			try:
				WebDriverWait(driver, 10).until(EC.url_changes(old_url))
			except TimeoutException:
				driver.get(href)
		except Exception:
			driver.get(href)

	time.sleep(0.5)
	return driver.current_url


def collect_images(driver: webdriver.Chrome) -> List[str]:
	"""Collect product image URLs from common gallery selectors."""
	# PHC-specific container first (class="relative mb-6")
	selectors = [
		".relative.mb-6 img",
		".product-gallery img",
		".product-images img",
		".product-photos img",
		".gallery img",
		".images img",
		".photos img",
		".thumbnails img",
		".main-image img",
		".featured-image img",
		".product-image img",
		".product-media img",
		"img[data-zoom-image]",
		"img[data-image]",
		"img[src*='product']",
	]
	seen = []
	seen_set = set()
	for sel in selectors:
		try:
			els = driver.find_elements(By.CSS_SELECTOR, sel)
		except Exception:
			continue
		for img in els:
			src = (img.get_attribute("src") or "").strip()
			if not src or src.startswith("data:"):
				srcset = (img.get_attribute("srcset") or "").strip()
				if srcset:
					# pick first candidate
					src = srcset.split(",")[0].strip().split()[0]
			if src and src not in seen_set:
				seen.append(src)
				seen_set.add(src)
	return seen


def extract_title(driver: webdriver.Chrome) -> str:
	"""Extract product title using common selectors."""
	selectors = [
		"h1.product-title",
		"h1.product-name",
		"h1[itemprop='name']",
		"h1",
		".product-title",
		".product-name",
	]
	for sel in selectors:
		try:
			el = driver.find_element(By.CSS_SELECTOR, sel)
			text = safe_get_text(el)
			if text:
				return text
		except NoSuchElementException:
			continue
	return ""


def extract_description_html(driver: webdriver.Chrome) -> str:
	"""Extract product description HTML using common selectors."""
	# PHC-specific: class="body2 text-gray-700"
	selectors = [
		".body2.text-gray-700",
		".product-description",
		".product-details",
		".description",
		".product-content",
		"#description",
		"[itemprop='description']",
		".woocommerce-product-details__short-description",
		"#tab-description",
	]
	for sel in selectors:
		try:
			el = driver.find_element(By.CSS_SELECTOR, sel)
			html = (el.get_attribute("innerHTML") or "").strip()
			if html and len(html) > 20:
				return html
		except NoSuchElementException:
			continue
	return ""


def scrape_phc_by_sku(driver: webdriver.Chrome, sku: str) -> Dict[str, str]:
	"""End-to-end: open search, go to first product, extract fields."""
	open_search_page(driver, sku)
	first = find_first_product_link(driver)
	if not first:
		return {"sku": sku, "status": "NO_RESULTS", "title": "", "description": "", "images": ""}
	href, el = first
	product_url = navigate_to_product(driver, el, href)

	# Optional wait for product page readiness
	try:
		wait_any(
			driver,
			[
				EC.presence_of_element_located((By.CSS_SELECTOR, "h1")),
				EC.presence_of_element_located((By.CSS_SELECTOR, ".product-title")),
				EC.presence_of_element_located((By.CSS_SELECTOR, ".product-description")),
			],
			timeout=15,
		)
	except TimeoutException:
		pass

	title = extract_title(driver)
	description_html = extract_description_html(driver)
	images = collect_images(driver)

	status = "SUCCESS" if (title or description_html or images) else "PARTIAL"
	return {
		"sku": sku,
		"status": status,
		"title": title,
		"description": description_html,
		"images": ";".join(images),
		"product_url": product_url,
	}


def read_skus_from_excel(path: str, sku_column: Optional[str] = None) -> List[str]:
	"""Read SKUs from an Excel file, guessing the column if not provided."""
	if not os.path.exists(path):
		raise FileNotFoundError(f"Input file not found: {path}")
	df = pd.read_excel(path)
	if df.empty:
		return []
	# Determine column
	if sku_column and sku_column in df.columns:
		col = sku_column
	else:
		# Try common column names
		candidates = ["SKU", "sku", "Sku", "SKUs", "CODE", "Code", "code", "Item Code", "ItemCode", "Barcode", "barcode"]
		col = None
		for c in candidates:
			if c in df.columns:
				col = c
				break
		if col is None:
			# Fallback: first column
			col = df.columns[0]
	# Extract SKUs as strings, drop NaNs/empties
	skus = []
	for v in df[col].astype(str).tolist():
		v = (v or "").strip()
		if v and v.lower() not in ("nan", "none"):
			skus.append(v)
	return skus


def process_excel_and_append_columns(in_path: str, out_path: Optional[str], sku_column: Optional[str], driver: webdriver.Chrome) -> str:
	"""
	Read the original Excel, keep all columns as-is and append:
	- PHC_Title
	- PHC_Description
	- PHC_Images
	- PHC_ProductURL
	- PHC_Status
	Write to out_path (or <input>_phc_results.xlsx) and return the output path.
	"""
	df = pd.read_excel(in_path)
	if df.empty:
		# Still write out an empty with new columns
		df["PHC_Title"] = ""
		df["PHC_Description"] = ""
		df["PHC_Images"] = ""
		df["PHC_ProductURL"] = ""
		df["PHC_Status"] = "NO_ROWS"
		if not out_path:
			root, ext = os.path.splitext(in_path)
			out_path = f"{root}_phc_results.xlsx"
		df.to_excel(out_path, index=False)
		return out_path

	# Find SKU column
	col = sku_column if (sku_column and sku_column in df.columns) else None
	if col is None:
		for c in ["SKU", "sku", "Sku", "SKUs", "CODE", "Code", "code", "Item Code", "ItemCode", "Barcode", "barcode"]:
			if c in df.columns:
				col = c
				break
	if col is None:
		# Fallback to first column
		col = df.columns[0]

	# Ensure result columns exist (preserve original columns)
	for c in ["PHC_Title", "PHC_Description", "PHC_Images", "PHC_ProductURL", "PHC_Status"]:
		if c not in df.columns:
			df[c] = ""

	# Iterate rows and fill result columns
	for idx, row in df.iterrows():
		raw_sku = row[col]
		sku = "" if pd.isna(raw_sku) else str(raw_sku).strip()
		if not sku:
			df.at[idx, "PHC_Status"] = "EMPTY_SKU"
			continue
		res = scrape_phc_by_sku(driver, sku)
		df.at[idx, "PHC_Title"] = res.get("title", "")
		df.at[idx, "PHC_Description"] = res.get("description", "")
		df.at[idx, "PHC_Images"] = res.get("images", "")
		df.at[idx, "PHC_ProductURL"] = res.get("product_url", "")
		df.at[idx, "PHC_Status"] = res.get("status", "")

	# Decide output path
	if not out_path:
		root, ext = os.path.splitext(in_path)
		out_path = f"{root}_phc_results.xlsx"
	df.to_excel(out_path, index=False)
	return out_path


def main():
	parser = argparse.ArgumentParser(description="PHC.com.jo product scraper by SKU")
	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument("--sku", help="Single SKU to search")
	group.add_argument("--in", dest="in_file", help="Excel file path containing SKUs")
	parser.add_argument("--sku-col", dest="sku_col", help="Column name in Excel that contains SKUs")
	parser.add_argument("--out", dest="out_file", help="Output file path (defaults to <input>_phc_results.xlsx for batch)")
	parser.add_argument("--headful", action="store_true", help="Run with visible browser")
	args = parser.parse_args()

	driver = build_driver(headful=args.headful)
	try:
		# Single SKU mode
		if args.sku:
			result = scrape_phc_by_sku(driver, args.sku)
			print(json.dumps(result, ensure_ascii=False))
			sys.exit(0 if result.get("status") == "SUCCESS" else 1)
		# Batch mode: preserve original Excel columns and append new columns
		out_path = process_excel_and_append_columns(args.in_file, args.out_file, args.sku_col, driver)
		print(json.dumps({"out": out_path}, ensure_ascii=False))
		sys.exit(0)
	finally:
		driver.quit()


if __name__ == "__main__":
	main()


