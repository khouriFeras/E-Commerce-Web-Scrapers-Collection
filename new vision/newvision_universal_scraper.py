#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
New Vision Universal Scraper
Scrapes all products from any New Vision category page
Supports multiple product categories and enhanced data extraction
"""

import argparse
import os
import re
import time
from typing import List, Dict, Any, Optional
import pandas as pd
from urllib.parse import urljoin, urlparse, quote_plus

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException


NEWVISION_SEARCH_URL = "https://newvision.jo/en/?post_type=product&s={query}"


def build_driver(headful: bool = False) -> webdriver.Chrome:
    """Build Chrome driver with proper options."""
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


def clean_image_url(url: str) -> str:
    """Clean image URL by removing size parameters to get the original image."""
    if not url:
        return url
    
    # Remove common size parameters
    url = re.sub(r'-\d+x\d+\.', '.', url)  # Remove -700x465.jpg -> .jpg
    url = re.sub(r'[?&](w|h|width|height|size|resize|fit|format|auto)=[^&]*', '', url)
    url = re.sub(r'[?&]+$', '', url)
    
    return url


def extract_category_from_url(url: str) -> str:
    """Extract category name from URL."""
    if 'refrigerators' in url:
        return 'Refrigerators'
    elif 'tvs' in url:
        return 'TVs'
    elif 'washing' in url or 'washer' in url:
        return 'Washing Machines'
    elif 'air-conditioner' in url or 'ac' in url:
        return 'Air Conditioners'
    elif 'microwave' in url:
        return 'Microwaves'
    elif 'vacuum' in url:
        return 'Vacuum Cleaners'
    elif 'dishwasher' in url:
        return 'Dishwashers'
    else:
        return 'Electronics'


def _normalize_sku(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip()).upper()


def _get_page_sku(driver: webdriver.Chrome) -> str:
    """Return SKU from current page (best-effort)."""
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".sku")
        return (el.text or "").strip()
    except Exception:
        return ""


def extract_product_details_from_loaded_page(
    driver: webdriver.Chrome, page_url: str, category: str = "Electronics"
) -> Dict[str, Any]:
    """Extract detailed information from the currently loaded page (no navigation)."""
    # Scroll to load all content (best-effort)
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.5);")
        time.sleep(0.8)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.8)
    except Exception:
        pass

    result = {
        'url': page_url,
        'title': '',
        'price': '',
        'original_price': '',
        'discount': '',
        'description': '',
        'images': [],
        'specifications': '',
        'features': '',
        'brand': 'LG',
        'category': category,
        'sku': '',
        'availability': '',
        'status': 'SUCCESS'
    }

    # Extract title
    try:
        title_selectors = [
            'h1.product-title',
            '.product_title',
            'h1',
            '.product-name',
            '.entry-title'
        ]
        for selector in title_selectors:
            try:
                title_el = driver.find_element(By.CSS_SELECTOR, selector)
                if title_el.text.strip():
                    result['title'] = title_el.text.strip()
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"     Error extracting title: {e}")

    # Extract price information
    try:
        price_selectors = [
            '.price .amount',
            '.woocommerce-Price-amount',
            '.price-current',
            '.current-price',
            '.price'
        ]
        for selector in price_selectors:
            try:
                price_el = driver.find_element(By.CSS_SELECTOR, selector)
                if price_el.text.strip():
                    result['price'] = price_el.text.strip()
                    break
            except Exception:
                continue

        original_price_selectors = [
            '.price del .amount',
            '.price-original',
            '.old-price',
            '.regular-price'
        ]
        for selector in original_price_selectors:
            try:
                orig_el = driver.find_element(By.CSS_SELECTOR, selector)
                if orig_el.text.strip():
                    result['original_price'] = orig_el.text.strip()
                    break
            except Exception:
                continue

        discount_selectors = [
            '.discount-percentage',
            '.sale-badge',
            '.offer-badge'
        ]
        for selector in discount_selectors:
            try:
                disc_el = driver.find_element(By.CSS_SELECTOR, selector)
                if disc_el.text.strip():
                    result['discount'] = disc_el.text.strip()
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"     Error extracting price: {e}")

    # Extract SKU
    try:
        sku_selectors = [
            '.sku',
            '.product-sku',
            '.item-sku',
            '[data-sku]'
        ]
        for selector in sku_selectors:
            try:
                sku_el = driver.find_element(By.CSS_SELECTOR, selector)
                if sku_el.text.strip():
                    result['sku'] = sku_el.text.strip()
                    break
                elif sku_el.get_attribute('data-sku'):
                    result['sku'] = sku_el.get_attribute('data-sku')
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"     Error extracting SKU: {e}")

    # Extract availability
    try:
        availability_selectors = [
            '.stock',
            '.availability',
            '.in-stock',
            '.out-of-stock'
        ]
        for selector in availability_selectors:
            try:
                avail_el = driver.find_element(By.CSS_SELECTOR, selector)
                if avail_el.text.strip():
                    result['availability'] = avail_el.text.strip()
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"     Error extracting availability: {e}")

    # Extract description
    try:
        desc_selectors = [
            '.product-description',
            '.woocommerce-product-details__short-description',
            '.product-summary',
            '.product-info',
            '.description',
            '.product-content',
            '.product-details',
            '.entry-content'
        ]
        for selector in desc_selectors:
            try:
                desc_el = driver.find_element(By.CSS_SELECTOR, selector)
                if desc_el.text.strip():
                    result['description'] = desc_el.text.strip()
                    break
            except Exception:
                continue

        # Append specs table text into description (requested)
        try:
            specs_el = driver.find_element(By.CSS_SELECTOR, "#specsTable")
            specs_text = specs_el.text.strip()
            if specs_text:
                if result['description']:
                    result['description'] = f"{result['description']}\n\n{specs_text}"
                else:
                    result['description'] = specs_text
        except Exception:
            pass
    except Exception as e:
        print(f"     Error extracting description: {e}")

    # Extract specifications
    try:
        spec_selectors = [
            '.product-attributes',
            '.woocommerce-product-attributes',
            '.specifications',
            '.product-specs',
            '.attributes',
            '.product-attributes-table'
        ]
        for selector in spec_selectors:
            try:
                spec_el = driver.find_element(By.CSS_SELECTOR, selector)
                if spec_el.text.strip():
                    result['specifications'] = spec_el.text.strip()
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"     Error extracting specifications: {e}")

    # Extract features
    try:
        features_selectors = [
            '.product-features',
            '.features',
            '.key-features',
            '.product-highlights'
        ]
        for selector in features_selectors:
            try:
                feat_el = driver.find_element(By.CSS_SELECTOR, selector)
                if feat_el.text.strip():
                    result['features'] = feat_el.text.strip()
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"     Error extracting features: {e}")

    # Extract images
    try:
        image_selectors = [
            'img[src*="product"]',
            '.product-images img',
            '.woocommerce-product-gallery img',
            '.product-gallery img',
            '.product-photos img',
            '.gallery img',
            '.fotorama__img',
            '.product-image img'
        ]

        image_urls = set()
        for selector in image_selectors:
            try:
                imgs = driver.find_elements(By.CSS_SELECTOR, selector)
                for img in imgs:
                    src = img.get_attribute("src")
                    if src and not src.startswith("data:") and "placeholder" not in src.lower():
                        image_urls.add(clean_image_url(src))
            except Exception:
                continue

        result['images'] = list(image_urls)
    except Exception as e:
        print(f"     Error extracting images: {e}")

    # Validate results
    if not result['title'] and not result['description'] and not result['images']:
        result['status'] = 'FAILED'
        result['error'] = 'No product data found'
    elif not result['title'] or not result['images']:
        result['status'] = 'PARTIAL'

    return result


def find_product_url_by_sku(driver: webdriver.Chrome, sku: str, timeout_s: int = 15) -> Optional[str]:
    """Search by SKU, then return the first product page URL found."""
    sku = (sku or "").strip()
    if not sku:
        return None

    search_url = NEWVISION_SEARCH_URL.format(query=quote_plus(sku))
    print(f"Searching SKU: {sku}")
    print(f"   {search_url}")

    driver.get(search_url)

    try:
        WebDriverWait(driver, timeout_s).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
        )
    except TimeoutException:
        return None

    time.sleep(1.2)

    # The search page can show products in different templates; grab the first real product link.
    candidates: List[str] = []
    selectors = [
        'a[href*="/product/"]',
        ".product-item a[href]",
        ".product a[href]",
        ".woocommerce-loop-product__link",
        ".woodmart-product-link",
    ]

    for selector in selectors:
        try:
            links = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue

        for link in links:
            href = link.get_attribute("href")
            if not href:
                continue
            if "/product/" not in href:
                continue
            if "/product-category/" in href:
                continue
            candidates.append(href)

        if candidates:
            break

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    return unique[0] if unique else None


def scrape_products_by_skus(
    skus: List[str],
    output_file: str,
    headful: bool = False,
    delay: float = 1.5,
) -> None:
    """Scrape products by SKU using the site's search page."""

    # Resume logic: load existing output and skip already-done URLs
    existing_df = None
    already_done_urls: set = set()
    if os.path.exists(output_file):
        try:
            existing_df = pd.read_excel(output_file)
            if "url" in existing_df.columns and "status" in existing_df.columns:
                already_done_urls = set(
                    existing_df.loc[existing_df["status"] == "SUCCESS", "url"].astype(str).str.strip()
                )
            print(f"Resuming: {len(already_done_urls)} URLs already done, will skip them")
        except Exception as e:
            print(f"Warning: could not read existing output ({e}), starting fresh")
            existing_df = None

    driver = build_driver(headful)
    try:
        print("Starting New Vision SKU Search Scraper")
        print(f"   SKUs: {len(skus)}")
        print(f"   Output: {output_file}")
        print(f"   Delay: {delay}s between requests")
        print()

        results: List[Dict[str, Any]] = []
        for i, sku in enumerate(skus, 1):
            sku_clean = (sku or "").strip()
            if not sku_clean:
                continue

            print(f"\n[{i}/{len(skus)}] SKU={sku_clean}")
            search_url = NEWVISION_SEARCH_URL.format(query=quote_plus(sku_clean))
            print(f"   Loading: {search_url}")
            driver.get(search_url)
            try:
                WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
            except TimeoutException:
                pass
            time.sleep(1.0)

            # Skip if this URL was already successfully scraped
            if driver.current_url in already_done_urls:
                print(f"   Skipping already-done URL: {driver.current_url[:60]}")
                if i < len(skus):
                    time.sleep(delay)
                continue

            # Step 1: validate SKU on the page. If it doesn't validate, skip scraping.
            page_sku = _get_page_sku(driver)
            if not page_sku:
                results.append(
                    {
                        "url": driver.current_url,
                        "title": "",
                        "price": "",
                        "original_price": "",
                        "discount": "",
                        "description": "",
                        "images": [],
                        "specifications": "",
                        "features": "",
                        "brand": "LG",
                        "category": "Electronics",
                        "sku": sku_clean,
                        "availability": "",
                        "status": "FAILED",
                        "error": "SKU element (.sku) not found on search page",
                    }
                )
            elif _normalize_sku(page_sku) != _normalize_sku(sku_clean):
                results.append(
                    {
                        "url": driver.current_url,
                        "title": "",
                        "price": "",
                        "original_price": "",
                        "discount": "",
                        "description": "",
                        "images": [],
                        "specifications": "",
                        "features": "",
                        "brand": "LG",
                        "category": "Electronics",
                        "sku": sku_clean,
                        "availability": "",
                        "status": "FAILED",
                        "error": f"SKU mismatch (page='{page_sku}' excel='{sku_clean}')",
                    }
                )
            else:
                result = extract_product_details_from_loaded_page(
                    driver, page_url=driver.current_url, category="Electronics"
                )
                # Keep the requested SKU as the output SKU
                result["sku"] = sku_clean
                results.append(result)

            if i < len(skus):
                time.sleep(delay)

        new_df = pd.DataFrame(results)
        if "images" in new_df.columns and len(new_df) > 0:
            new_df["images"] = new_df["images"].apply(lambda x: ";".join(x) if isinstance(x, list) else "")

        if len(new_df) > 0:
            columns = [
                "url",
                "title",
                "price",
                "original_price",
                "discount",
                "description",
                "images",
                "specifications",
                "features",
                "brand",
                "category",
                "sku",
                "availability",
                "status",
            ]
            if "error" in new_df.columns:
                columns.append("error")
            existing_cols = [c for c in columns if c in new_df.columns]
            new_df = new_df[existing_cols]

        if existing_df is not None and len(new_df) > 0:
            final_df = pd.concat([existing_df, new_df], ignore_index=True)
        elif existing_df is not None:
            final_df = existing_df
        else:
            final_df = new_df
        final_df.to_excel(output_file, index=False)

        print("\n" + "=" * 60)
        print("SCRAPING SUMMARY")
        print("=" * 60)
        print(f"Total SKUs: {len(skus)}")
        print(f"Rows written: {len(final_df)}")
        print(f"Successful: {len([r for r in results if r.get('status') == 'SUCCESS'])}")
        print(f"Partial: {len([r for r in results if r.get('status') == 'PARTIAL'])}")
        print(f"Failed: {len([r for r in results if r.get('status') == 'FAILED'])}")
        print(f"Saved to: {output_file}")
    finally:
        driver.quit()


def get_product_links(driver, base_url: str) -> List[Dict[str, str]]:
    """Get all product links from the category page."""
    print(f"Scraping product links from: {base_url}")
    
    driver.get(base_url)
    time.sleep(3)
    
    # Scroll to load all products
    print("   Scrolling to load all products...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_attempts = 0
    max_scrolls = 10
    
    while scroll_attempts < max_scrolls:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
        scroll_attempts += 1
    
    # Find all product links
    products = []
    try:
        # Try multiple selectors for product links
        selectors = [
            'a[href*="/product/"]',
            '.product-item a',
            '.product-card a',
            '.woocommerce-loop-product__link',
            '.woodmart-product-link'
        ]
        
        for selector in selectors:
            try:
                links = driver.find_elements(By.CSS_SELECTOR, selector)
                for link in links:
                    href = link.get_attribute("href")
                    if href and "/product/" in href and "/product-category/" not in href:
                        title = link.text.strip()
                        if title and not title.startswith('%') and len(title) > 10:
                            products.append({
                                'url': href,
                                'title': title
                            })
                if products:
                    break
            except Exception as e:
                print(f"   Selector {selector} failed: {e}")
                continue
        
        # Remove duplicates
        seen_urls = set()
        unique_products = []
        for product in products:
            if product['url'] not in seen_urls:
                seen_urls.add(product['url'])
                unique_products.append(product)
        
        print(f"   Found {len(unique_products)} unique products")
        return unique_products
        
    except Exception as e:
        print(f"   Error getting product links: {e}")
        return []


def extract_product_details(driver, product_url: str, category: str = "Electronics") -> Dict[str, Any]:
    """Extract detailed information from a product page."""
    print(f"   Scraping: {product_url}")
    
    try:
        driver.get(product_url)
        time.sleep(2)
        
        # Scroll to load all content
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.5);")
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
        
        # Extract product information
        result = {
            'url': product_url,
            'title': '',
            'price': '',
            'original_price': '',
            'discount': '',
            'description': '',
            'images': [],
            'specifications': '',
            'features': '',
            'brand': 'LG',
            'category': category,
            'sku': '',
            'availability': '',
            'status': 'SUCCESS'
        }
        
        # Extract title
        try:
            title_selectors = [
                'h1.product-title',
                '.product_title',
                'h1',
                '.product-name',
                '.entry-title'
            ]
            for selector in title_selectors:
                try:
                    title_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if title_el.text.strip():
                        result['title'] = title_el.text.strip()
                        break
                except:
                    continue
        except Exception as e:
            print(f"     Error extracting title: {e}")
        
        # Extract price information
        try:
            # Current price
            price_selectors = [
                '.price .amount',
                '.woocommerce-Price-amount',
                '.price-current',
                '.current-price',
                '.price'
            ]
            for selector in price_selectors:
                try:
                    price_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if price_el.text.strip():
                        result['price'] = price_el.text.strip()
                        break
                except:
                    continue
            
            # Original price (if discounted)
            original_price_selectors = [
                '.price del .amount',
                '.price-original',
                '.old-price',
                '.regular-price'
            ]
            for selector in original_price_selectors:
                try:
                    orig_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if orig_el.text.strip():
                        result['original_price'] = orig_el.text.strip()
                        break
                except:
                    continue
            
            # Discount percentage
            discount_selectors = [
                '.discount-percentage',
                '.sale-badge',
                '.offer-badge'
            ]
            for selector in discount_selectors:
                try:
                    disc_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if disc_el.text.strip():
                        result['discount'] = disc_el.text.strip()
                        break
                except:
                    continue
                    
        except Exception as e:
            print(f"     Error extracting price: {e}")
        
        # Extract SKU
        try:
            sku_selectors = [
                '.sku',
                '.product-sku',
                '.item-sku',
                '[data-sku]'
            ]
            for selector in sku_selectors:
                try:
                    sku_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if sku_el.text.strip():
                        result['sku'] = sku_el.text.strip()
                        break
                    elif sku_el.get_attribute('data-sku'):
                        result['sku'] = sku_el.get_attribute('data-sku')
                        break
                except:
                    continue
        except Exception as e:
            print(f"     Error extracting SKU: {e}")
        
        # Extract availability
        try:
            availability_selectors = [
                '.stock',
                '.availability',
                '.in-stock',
                '.out-of-stock'
            ]
            for selector in availability_selectors:
                try:
                    avail_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if avail_el.text.strip():
                        result['availability'] = avail_el.text.strip()
                        break
                except:
                    continue
        except Exception as e:
            print(f"     Error extracting availability: {e}")
        
        # Extract description
        try:
            desc_selectors = [
                '.product-description',
                '.woocommerce-product-details__short-description',
                '.product-summary',
                '.product-info',
                '.description',
                '.product-content',
                '.product-details',
                '.entry-content'
            ]
            for selector in desc_selectors:
                try:
                    desc_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if desc_el.text.strip():
                        result['description'] = desc_el.text.strip()
                        break
                except:
                    continue

            # Append specs table text into description (requested)
            try:
                specs_el = driver.find_element(By.CSS_SELECTOR, "#specsTable")
                specs_text = specs_el.text.strip()
                if specs_text:
                    if result['description']:
                        result['description'] = f"{result['description']}\n\n{specs_text}"
                    else:
                        result['description'] = specs_text
            except:
                pass
        except Exception as e:
            print(f"     Error extracting description: {e}")
        
        # Extract specifications
        try:
            spec_selectors = [
                '.product-attributes',
                '.woocommerce-product-attributes',
                '.specifications',
                '.product-specs',
                '.attributes',
                '.product-attributes-table'
            ]
            for selector in spec_selectors:
                try:
                    spec_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if spec_el.text.strip():
                        result['specifications'] = spec_el.text.strip()
                        break
                except:
                    continue
        except Exception as e:
            print(f"     Error extracting specifications: {e}")
        
        # Extract features
        try:
            features_selectors = [
                '.product-features',
                '.features',
                '.key-features',
                '.product-highlights'
            ]
            for selector in features_selectors:
                try:
                    feat_el = driver.find_element(By.CSS_SELECTOR, selector)
                    if feat_el.text.strip():
                        result['features'] = feat_el.text.strip()
                        break
                except:
                    continue
        except Exception as e:
            print(f"     Error extracting features: {e}")
        
        # Extract images
        try:
            image_selectors = [
                'img[src*="product"]',
                '.product-images img',
                '.woocommerce-product-gallery img',
                '.product-gallery img',
                '.product-photos img',
                '.gallery img',
                '.fotorama__img',
                '.product-image img'
            ]
            
            image_urls = set()
            for selector in image_selectors:
                try:
                    imgs = driver.find_elements(By.CSS_SELECTOR, selector)
                    for img in imgs:
                        src = img.get_attribute("src")
                        if src and not src.startswith("data:") and "placeholder" not in src.lower():
                            # Clean the URL to get original size
                            clean_url = clean_image_url(src)
                            image_urls.add(clean_url)
                except:
                    continue
            
            result['images'] = list(image_urls)
            
        except Exception as e:
            print(f"     Error extracting images: {e}")
        
        # Validate results
        if not result['title'] and not result['description'] and not result['images']:
            result['status'] = 'FAILED'
            result['error'] = 'No product data found'
        elif not result['title'] or not result['images']:
            result['status'] = 'PARTIAL'
        
        print(f"     Title: {result['title'][:50]}...")
        print(f"     Price: {result['price']} (Original: {result['original_price']})")
        print(f"     Images: {len(result['images'])}")
        print(f"     Description: {len(result['description'])} chars")
        print(f"     SKU: {result['sku']}")
        
        return result
        
    except Exception as e:
        print(f"   Error scraping product: {e}")
        return {
            'url': product_url,
            'title': '',
            'price': '',
            'original_price': '',
            'discount': '',
            'description': '',
            'images': [],
            'specifications': '',
            'features': '',
            'brand': 'LG',
            'category': category,
            'sku': '',
            'availability': '',
            'status': 'FAILED',
            'error': str(e)
        }


def scrape_all_products(base_url: str, output_file: str, headful: bool = False, delay: float = 2.0) -> None:
    """Scrape all products from the New Vision category page."""

    # Resume logic: load existing output and skip already-done URLs
    existing_df = None
    already_done_urls: set = set()
    if os.path.exists(output_file):
        try:
            existing_df = pd.read_excel(output_file)
            if "url" in existing_df.columns and "status" in existing_df.columns:
                already_done_urls = set(
                    existing_df.loc[existing_df["status"] == "SUCCESS", "url"].astype(str).str.strip()
                )
            print(f"Resuming: {len(already_done_urls)} URLs already done, will skip them")
        except Exception as e:
            print(f"Warning: could not read existing output ({e}), starting fresh")
            existing_df = None

    driver = build_driver(headful)

    try:
        print("Starting New Vision Universal Scraper")
        print(f"   Base URL: {base_url}")
        print(f"   Output: {output_file}")
        print(f"   Delay: {delay}s between requests")
        print()

        # Extract category from URL
        category = extract_category_from_url(base_url)
        print(f"   Detected Category: {category}")

        # Get all product links
        products = get_product_links(driver, base_url)

        if not products:
            print("No products found!")
            return

        print(f"\nFound {len(products)} products to scrape")
        print("=" * 60)

        # Scrape each product
        all_results = []
        for i, product in enumerate(products, 1):
            if product['url'] in already_done_urls:
                print(f"\n[{i}/{len(products)}] Skipping already-done: {product['url'][:60]}")
                continue
            print(f"\n[{i}/{len(products)}] {product['title'][:50]}...")

            result = extract_product_details(driver, product['url'], category)
            all_results.append(result)

            # Add delay between requests
            if i < len(products):
                time.sleep(delay)

        # Create DataFrame and save
        new_df = pd.DataFrame(all_results)

        if len(new_df) > 0:
            # Convert images list to semicolon-separated string
            new_df['images'] = new_df['images'].apply(lambda x: ';'.join(x) if isinstance(x, list) else '')

            # Reorder columns
            columns = ['url', 'title', 'price', 'original_price', 'discount', 'description', 'images',
                      'specifications', 'features', 'brand', 'category', 'sku', 'availability', 'status']
            if 'error' in new_df.columns:
                columns.append('error')
            existing_cols = [c for c in columns if c in new_df.columns]
            new_df = new_df[existing_cols]

        if existing_df is not None and len(new_df) > 0:
            final_df = pd.concat([existing_df, new_df], ignore_index=True)
        elif existing_df is not None:
            final_df = existing_df
        else:
            final_df = new_df

        # Save to Excel
        final_df.to_excel(output_file, index=False)
        
        # Print summary
        print("\n" + "=" * 60)
        print("SCRAPING SUMMARY")
        print("=" * 60)
        print(f"Total products: {len(all_results)}")
        print(f"Successful: {len([r for r in all_results if r['status'] == 'SUCCESS'])}")
        print(f"Partial: {len([r for r in all_results if r['status'] == 'PARTIAL'])}")
        print(f"Failed: {len([r for r in all_results if r['status'] == 'FAILED'])}")
        print(f"Total images: {sum(len(r['images']) for r in all_results if isinstance(r['images'], list))}")
        print(f"Saved to: {output_file}")
        
    finally:
        driver.quit()


def main():
    """Main function for CLI usage."""
    parser = argparse.ArgumentParser(description="New Vision Universal Scraper")
    parser.add_argument(
        "--url",
        required=False,
        help="URL of the category page to scrape (category mode)",
    )
    parser.add_argument(
        "--sku",
        required=False,
        help="Single SKU to search then scrape (SKU search mode)",
    )
    parser.add_argument(
        "--skus-file",
        required=False,
        help="Excel file path containing a SKU column (SKU search mode)",
    )
    parser.add_argument(
        "--sku-column",
        default="SKU",
        help="Column name to read from --skus-file (default: SKU)",
    )
    parser.add_argument("--output", "-o", default="newvision_products.xlsx", 
                       help="Output Excel file path")
    parser.add_argument("--headful", action="store_true", 
                       help="Run browser in headful mode (visible)")
    parser.add_argument("--delay", type=float, default=2.0, 
                       help="Delay between requests in seconds")
    
    args = parser.parse_args()
    
    # Create output directory if it doesn't exist
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    if args.sku or args.skus_file:
        skus: List[str] = []
        if args.sku:
            skus = [args.sku]
        else:
            df_in = pd.read_excel(args.skus_file)
            if args.sku_column not in df_in.columns:
                raise ValueError(
                    f"SKU column '{args.sku_column}' not found. Available: {list(df_in.columns)}"
                )
            skus = [str(x) for x in df_in[args.sku_column].dropna().tolist()]

        scrape_products_by_skus(skus, args.output, args.headful, delay=min(args.delay, 3.0))
        return

    if not args.url:
        raise ValueError("Provide either --url (category mode) or --sku/--skus-file (SKU mode).")

    scrape_all_products(args.url, args.output, args.headful, args.delay)


if __name__ == "__main__":
    main()

