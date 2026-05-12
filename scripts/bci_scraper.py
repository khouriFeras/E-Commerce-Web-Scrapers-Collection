import asyncio
import csv
import os
import json
from typing import Dict, List, Optional, Set

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright, Page

LISTING_URL = "https://jo.bcimobilestore.com/en/xiaomi/smartphones.html?product_list_limit=48"
OUTPUT_CSV = "output/bci2_xiaomi_smartphones.csv"

# Ensure output dir exists
os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)


def get_text_or_none(page: Page, selector: str) -> Optional[str]:
	try:
		el = page.query_selector(selector)
		if not el:
			return None
			
		text = el.inner_text().strip()
		return text if text else None
	except PlaywrightTimeoutError:
		return None


def get_attr_or_none(page: Page, selector: str, attr: str) -> Optional[str]:
	try:
		el = page.query_selector(selector)
		if not el:
			return None
		val = el.get_attribute(attr)
		return val
	except PlaywrightTimeoutError:
		return None


def absolute_url(page: Page, url: str) -> str:
	if not url:
		return url
	if url.startswith("http://") or url.startswith("https://"):
		return url
	base = page.url.rstrip("/")
	if url.startswith("/"):
		return base.split("/en")[0] + url
	return f"{base}/{url}"


def _text_in_el(el) -> Optional[str]:
	if not el:
		return None
	try:
		text = el.inner_text().strip()
		return text if text else None
	except Exception:
		return None


def extract_prices(page: Page) -> Dict[str, Optional[str]]:
	# Ensure price box is present/rendered
	try:
		page.wait_for_selector(".product-info-main .price-box", timeout=3000)
	except Exception:
		pass

	container = page.query_selector(".product-info-main") or page

	def read_amount_or_text_in(el) -> Optional[str]:
		if not el:
			return None
		amt = el.get_attribute("data-price-amount")
		if amt and any(ch.isdigit() for ch in amt):
			return amt
		# meta[itemprop=price] under this element
		meta = el.query_selector("meta[itemprop='price']")
		if meta:
			content = meta.get_attribute("content") or ""
			if content and any(ch.isdigit() for ch in content):
				return content
		# visible text fallback
		text_el = el.query_selector(".price") or el
		text = _text_in_el(text_el)
		if text and any(ch.isdigit() for ch in text):
			return text
		return None

	def wait_and_read(selectors: List[str], timeout_ms: int = 5000, step_ms: int = 200) -> Optional[str]:
		elapsed = 0
		while elapsed <= timeout_ms:
			for sel in selectors:
				el = container.query_selector(sel)
				val = read_amount_or_text_in(el)
				if val:
					return val
				# Also check meta directly under the selector scope
				if el is None:
					# Try finding meta price within the same scope even if wrapper missing
					for meta_sel in [f"{sel} meta[itemprop='price']", "meta[itemprop='price']"]:
						m = container.query_selector(meta_sel)
						if m:
							content = m.get_attribute("content") or ""
							if content and any(ch.isdigit() for ch in content):
								return content
			page.wait_for_timeout(step_ms)
			elapsed += step_ms
		return None

	# New price selectors (most specific first)
	new_selectors = [
		".price-box.price-final_price .normal-price .price-container.price-final_price .price-wrapper[data-price-type='finalPrice']",
		".price-box.price-final_price .price-wrapper[data-price-type='finalPrice']",
		".price-box .price-wrapper[data-price-type='finalPrice']",
		".price-box [id^='product-price-'][data-price-amount]",
		".price-box .special-price",
		".price-box.price-final_price .price-from .price-wrapper[data-price-type='finalPrice']",
		".price-box.price-final_price .price-from",
		".price-container.price-final_price",
		".price-box",
	]
	old_selectors = [
		".price-box .old-price .price-wrapper[data-price-type='oldPrice']",
		".price-box [id^='old-price-'][data-price-amount]",
		".price-box .old-price",
		".old-price.sly-old-price",
	]

	new_price_text = wait_and_read(new_selectors)
	old_price_text = wait_and_read(old_selectors)

	# JSON-LD as last resort for new price
	if not new_price_text:
		html = page.content()
		soup = BeautifulSoup(html, "lxml")
		for script in soup.find_all("script", {"type": "application/ld+json"}):
			try:
				data = json.loads(script.string or script.get_text() or "{}")
			except Exception:
				continue
			if isinstance(data, dict):
				offers = data.get("offers")
				if isinstance(offers, dict) and offers.get("price"):
					new_price_text = str(offers.get("price"))
			elif isinstance(data, list):
				for d in data:
					if isinstance(d, dict) and isinstance(d.get("offers"), dict) and d["offers"].get("price"):
						new_price_text = str(d["offers"].get("price"))
						break

	# Magento init (price-box) fallback if still missing
	if not new_price_text or (not old_price_text):
		html = page.content()
		parsed = _parse_prices_from_magento_init(html)
		if not new_price_text and parsed.get("new_price"):
			new_price_text = parsed.get("new_price")
		if not old_price_text and parsed.get("old_price"):
			old_price_text = parsed.get("old_price")

	# Ultra-broad DOM fallbacks inside product price box
	if not new_price_text:
		# Any finalPrice wrapper anywhere in price box
		el = container.query_selector(".price-box [data-price-type='finalPrice']") or \
			container.query_selector(".price-box span.price-wrapper[data-price-type='finalPrice']")
		new_price_text = read_amount_or_text_in(el)
		if not new_price_text:
			# XPath fallback for finalPrice
			try:
				el = container.query_selector("xpath=.//*[contains(@class,'price-box')]//*[contains(@data-price-type,'finalPrice')]")
				new_price_text = read_amount_or_text_in(el)
			except Exception:
				pass
	# If still no new price, try direct BeautifulSoup parsing as last resort
	if not new_price_text:
		html = page.content()
		soup = BeautifulSoup(html, "lxml")
		# Try to find price-wrapper with data-price-type=finalPrice
		price_wrapper = soup.select_one('.price-wrapper[data-price-type="finalPrice"]')
		if price_wrapper:
			amt = price_wrapper.get('data-price-amount')
			if amt and any(ch.isdigit() for ch in amt):
				new_price_text = amt
			else:
				# Try meta tag
				meta_price = soup.select_one('meta[itemprop="price"]')
				if meta_price:
					content = meta_price.get('content', '')
					if content and any(ch.isdigit() for ch in content):
						new_price_text = content
	if not old_price_text:
		el = container.query_selector(".price-box [data-price-type='oldPrice']") or \
			container.query_selector(".price-box span.price-wrapper[data-price-type='oldPrice']") or \
			container.query_selector(".price-box .old-price .price")
		old_price_text = read_amount_or_text_in(el)
		if not old_price_text:
			try:
				el = container.query_selector("xpath=.//*[contains(@class,'price-box')]//*[contains(@data-price-type,'oldPrice')]|.//*[contains(@class,'price-box')]//*[contains(@class,'old-price')]//*[contains(@class,'price')]")
				old_price_text = read_amount_or_text_in(el)
			except Exception:
				pass
	# If still no old price, try direct BeautifulSoup parsing as last resort
	if not old_price_text:
		html = page.content()
		soup = BeautifulSoup(html, "lxml")
		# Try to find old-price with price-wrapper
		old_price_wrapper = soup.select_one('.old-price .price-wrapper[data-price-type="oldPrice"]')
		if old_price_wrapper:
			amt = old_price_wrapper.get('data-price-amount')
			if amt and any(ch.isdigit() for ch in amt):
				old_price_text = amt
		else:
			# Try just old-price class
			old_price_el = soup.select_one('.old-price .price')
			if old_price_el:
				text = old_price_el.get_text(strip=True)
				if text and any(ch.isdigit() for ch in text):
					old_price_text = text

	# Clean invalid old price
	if old_price_text and new_price_text and old_price_text.strip() == new_price_text.strip():
		old_price_text = None
	if old_price_text and not any(ch.isdigit() for ch in old_price_text):
		old_price_text = None

	return {"old_price": old_price_text, "new_price": new_price_text}


def _parse_prices_from_magento_init(html: str) -> Dict[str, Optional[str]]:
	old_price = None
	new_price = None
	soup = BeautifulSoup(html, "lxml")
	for script in soup.find_all("script", {"type": "text/x-magento-init"}):
		text = script.string or script.get_text() or ""
		if not text.strip():
			continue
		try:
			data = json.loads(text)
		except Exception:
			continue
		# Walk to find price-box configs
		def walk(obj):
			nonlocal old_price, new_price
			if isinstance(obj, dict):
				# Direct module config
				if any(isinstance(v, dict) for v in obj.values()):
					for k, v in obj.items():
						# Look for Magento_Catalog/js/price-box object
						if isinstance(v, dict) and ("Magento_Catalog/js/price-box" in str(v) or "price-box" in str(v)):
							# Try common locations for prices
							for subk, subv in v.items():
								if not isinstance(subv, dict):
									continue
								prices = subv.get("prices") or subv.get("priceConfig", {}).get("prices")
								if isinstance(prices, dict):
									fp = prices.get("finalPrice") or {}
									op = prices.get("oldPrice") or {}
									if not new_price and isinstance(fp, dict):
										amt = fp.get("amount") or fp.get("value")
										if amt is not None:
											new_price = str(amt)
									if not old_price and isinstance(op, dict):
										amt = op.get("amount") or op.get("value")
										if amt is not None:
											old_price = str(amt)
						walk(v)
			elif isinstance(obj, list):
				for x in obj:
					walk(x)
		walk(data)
	return {"old_price": old_price, "new_price": new_price}


def extract_sku(page: Page) -> Optional[str]:
	candidates = [
		".product.attribute.sku .value",
		".product.info.detailed .value.sku",
		".product-info-main .sku .value",
		".product-info-main .sku",
		"span[itemprop=sku]",
	]
	for sel in candidates:
		text = get_text_or_none(page, sel)
		if text:
			return text

	# Fallback: parse More Information table for SKU
	html = page.content()
	soup = BeautifulSoup(html, "lxml")
	table = soup.select_one(".additional-attributes-wrapper.table-wrapper table")
	if table:
		for row in table.select("tr"):
			th = row.select_one("th")
			td = row.select_one("td")
			if th and td and th.get_text(strip=True).lower() in {"sku", "model", "part number"}:
				return td.get_text(strip=True)
	return None


def extract_description_kv(page: Page) -> str:
	# Collect key-value rows from the More Information table
	html = page.content()
	soup = BeautifulSoup(html, "lxml")
	pairs: List[str] = []
	table = soup.select_one(".additional-attributes-wrapper.table-wrapper table")
	if table:
		for row in table.select("tr"):
			th = row.select_one("th")
			td = row.select_one("td")
			if th and td:
				k = th.get_text(strip=True)
				v = td.get_text(" ", strip=True)
				if k and v:
					pairs.append(f"{k}: {v}")
	return "; ".join(pairs)


def get_enabled_color_swatches(page: Page) -> List[Dict[str, str]]:
	# Magento swatch options typically under .swatch-attribute.color .swatch-option
	swatches: List[Dict[str, str]] = []
	containers = page.query_selector_all(".swatch-attribute.color, .swatch-attribute[data-attribute-code='color']")
	for container in containers:
		options = container.query_selector_all(".swatch-option")
		for opt in options:
			classes = (opt.get_attribute("class") or "").lower()
			aria_disabled = opt.get_attribute("aria-disabled") or "false"
			is_disabled = "disabled" in classes or aria_disabled == "true"
			if is_disabled:
				continue
			label = opt.get_attribute("option-label") or opt.get_attribute("aria-label") or opt.inner_text().strip()
			if not label:
				continue
			swatches.append({
				"label": label.strip(),
				"locator": opt
			})
	return swatches


def extract_storage_options(page: Page) -> str:
	# Prefer dropdown: select.swatch-select.storage
	labels: List[str] = []
	select = page.query_selector("select.swatch-select.storage")
	if select:
		for opt in select.query_selector_all("option"):
			# Skip placeholders and disabled
			if opt.is_disabled():
				continue
			text = (opt.inner_text() or "").strip()
			value = (opt.get_attribute("value") or "").strip()
			if not value:
				continue
			if text and text.lower() not in {"choose an option...", "choose an option", "select"}:
				labels.append(text)
	# Fallback: swatch buttons with storage code
	if not labels:
		for container in page.query_selector_all(".swatch-attribute[data-attribute-code='storage'], .swatch-attribute.storage"):
			for opt in container.query_selector_all(".swatch-option"):
				classes = (opt.get_attribute("class") or "").lower()
				aria_disabled = opt.get_attribute("aria-disabled") or "false"
				is_disabled = "disabled" in classes or aria_disabled == "true"
				if is_disabled:
					continue
				label = opt.get_attribute("option-label") or opt.get_attribute("aria-label") or opt.inner_text().strip()
				if label:
					labels.append(label.strip())
	# Deduplicate preserving order
	seen: Set[str] = set()
	result: List[str] = []
	for l in labels:
		if l and l not in seen:
			seen.add(l)
			result.append(l)
	return ",".join(result)


def parse_fullsize_from_magento_init(html: str) -> List[str]:
	urls: List[str] = []
	soup = BeautifulSoup(html, "lxml")
	for script in soup.find_all("script", {"type": "text/x-magento-init"}):
		text = script.string or script.get_text() or ""
		if "mage/gallery/gallery" not in text:
			continue
		try:
			data = json.loads(text)
		except Exception:
			continue
		# Traverse nested dicts to find gallery config with 'data'
		def walk(obj):
			if isinstance(obj, dict):
				for k, v in obj.items():
					if isinstance(v, dict) and "data" in v and isinstance(v["data"], list):
						for item in v["data"]:
							if isinstance(item, dict):
								full = item.get("full") or item.get("img") or item.get("thumb")
								if full:
									urls.append(full)
					walk(v)
			elif isinstance(obj, list):
				for x in obj:
					walk(x)
		walk(data)
	return urls


def collect_gallery_image_urls_full(page: Page) -> List[str]:
	urls: List[str] = []
	allowed_ext = (".jpg", ".jpeg", ".png", ".webp")
	# 1) Parse Magento init JSON for full-size URLs
	html = page.content()
	init_urls = parse_fullsize_from_magento_init(html)
	for u in init_urls:
		if u and u not in urls and u.lower().split("?")[0].endswith(allowed_ext):
			urls.append(u)
	# 2) DOM-based fallbacks with zoom/full attributes
	selectors = [
		".fotorama__stage__frame img",
		".product.media img",
		".gallery-placeholder img",
		"img.fotorama__img",
	]
	for sel in selectors:
		for img in page.query_selector_all(sel):
			candidates = [
				img.get_attribute("data-zoom-image"),
				img.get_attribute("data-image-zoom"),
				img.get_attribute("data-full"),
				img.get_attribute("data-src-zoom"),
				img.get_attribute("srcset"),
				img.get_attribute("data-src"),
				img.get_attribute("src"),
			]
			# If srcset, pick the last (largest) URL
			srcset = candidates[4] or ""
			best = None
			if srcset:
				parts = [p.strip() for p in srcset.split(",") if p.strip()]
				if parts:
					last_url = parts[-1].split(" ")[0]
					best = last_url
			# Fallback to other attributes in order
			for val in candidates[:4] + candidates[5:]:
				if not best and val and any(ch.isalnum() for ch in val):
					best = val
			if best:
				b = best.strip()
				b_noq = b.split("?")[0].lower()
				if b_noq.endswith(allowed_ext) and b not in urls:
					urls.append(b)
	# Note: do NOT collect anchors (to avoid Gallery Next/Previous)
	# Make absolute and unique while preserving order
	seen: Set[str] = set()
	resolved: List[str] = []
	for src in urls:
		absu = absolute_url(page, src)
		if absu not in seen:
			seen.add(absu)
			resolved.append(absu)
	return resolved


def scrape_product(page: Page, url: str) -> Dict:
	page.goto(url, wait_until="networkidle")
	# Wait for price box to initialize for accurate prices
	try:
		page.wait_for_selector(".product-info-main .price-box", timeout=2000)
	except Exception:
		pass
	# Title
	title = get_text_or_none(page, ".page-title span") or get_text_or_none(page, "h1.page-title")
	# SKU
	sku = extract_sku(page)
	# Prices
	prices = extract_prices(page)
	# Description (More Information)
	description = extract_description_kv(page)
	# Storage options (comma-separated)
	storage = extract_storage_options(page)

	# Colors and per-color images
	color_to_images: Dict[str, List[str]] = {}
	swatches = get_enabled_color_swatches(page)
	if swatches:
		for sw in swatches:
			label = sw["label"].strip().lower()
			# Click swatch and wait for gallery update
			try:
				sw["locator"].click()
				page.wait_for_timeout(600)
			except PlaywrightTimeoutError:
				pass
			imgs = collect_gallery_image_urls_full(page)
			color_to_images[label] = imgs
	else:
		# No color options: collect default gallery images under a generic key
		imgs = collect_gallery_image_urls_full(page)
		color_to_images["default"] = imgs

	return {
		"url": url,
		"title": title,
		"sku": sku,
		"old_price": prices.get("old_price"),
		"new_price": prices.get("new_price"),
		"description": description,
		"storage": storage,
		"color_to_images": color_to_images,
	}


def collect_product_links(page: Page, listing_url: str) -> List[str]:
	page.goto(listing_url, wait_until="networkidle")
	links: Set[str] = set()
	# Magento product listing links
	for a in page.query_selector_all("a.product-item-link"):
		href = a.get_attribute("href")
		if href:
			links.add(href)
	return list(links)


def write_csv(rows: List[Dict], out_path: str) -> None:
	# Determine dynamic color columns across all rows
	color_columns: Set[str] = set()
	for r in rows:
		for color in r.get("color_to_images", {}).keys():
			col = f"{color.replace(' ', '').replace('-', '').lower()}imgs"
			color_columns.add(col)
	# Ensure requested columns for yellow/green exist even if absent
	for forced in ["yellowimgs", "greenimgs"]:
		color_columns.add(forced)

	headers = [
		"sku",
		"product_title",
		"storage",
		"price",
		"old_price",
		"new_price",
		"description",
	] + sorted(color_columns)

	with open(out_path, "w", newline="", encoding="utf-8") as f:
		writer = csv.DictWriter(f, fieldnames=headers)
		writer.writeheader()
		for r in rows:
			# Determine the main price (prefer new_price, fall back to old_price)
			new_price_str = (r.get("new_price") or "").strip()
			old_price_str = (r.get("old_price") or "").strip()
			main_price = new_price_str if new_price_str else old_price_str
			
			row: Dict[str, str] = {
				"sku": r.get("sku") or "",
				"product_title": r.get("title") or "",
				"storage": r.get("storage") or "",
				"price": main_price,
				"old_price": old_price_str,
				"new_price": new_price_str,
				"description": r.get("description") or "",
			}
			# Fill color columns
			color_to_images = r.get("color_to_images", {})
			for color, imgs in color_to_images.items():
				col = f"{color.replace(' ', '').replace('-', '').lower()}imgs"
				row[col] = ";".join(imgs)
			# Ensure all color columns present
			for col in color_columns:
				row.setdefault(col, "")
			writer.writerow(row)


def main() -> None:
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		context = browser.new_context()
		page = context.new_page()

		# 1) Collect product links
		product_links = collect_product_links(page, LISTING_URL)
		product_links = sorted(set(product_links))

		# 2) Scrape each product
		results: List[Dict] = []
		for i, link in enumerate(product_links, start=1):
			print(f"[{i}/{len(product_links)}] Scraping: {link}")
			try:
				data = scrape_product(page, link)
				results.append(data)
			except Exception as e:
				print(f"Error scraping {link}: {e}")

		# 3) Write CSV
		write_csv(results, OUTPUT_CSV)
		print(f"CSV written to {OUTPUT_CSV}")

		context.close()
		browser.close()


if __name__ == "__main__":
	main()
