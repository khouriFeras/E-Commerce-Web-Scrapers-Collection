#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Simple HTTP API for Universal Scraper
n8n can call this with GET/POST requests
"""

from flask import Flask, request, jsonify
import time
import sys
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import re

def clean_html_to_text(html_content):
    """Clean HTML and extract meaningful text."""
    if not html_content:
        return ""
    
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', html_content)
    
    # Decode HTML entities
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    
    # Clean up extra whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    return text

def build_simple_driver(headful=False):
    """Build Chrome driver with error handling."""
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-web-security")
    opts.add_argument("--disable-features=VizDisplayCompositor")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    try:
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        print(f"Chrome driver error: {e}")
        # Try with minimal options
        opts = Options()
        if not headful:
            opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(30)
        return driver

# Import the universal scraper
from universal_selenium_scraper import build_driver, run_for_sku

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "message": "Scraper API is running"})

@app.route('/scrape', methods=['GET', 'POST'])
def scrape():
    """Scrape a single SKU."""
    try:
        # Get parameters from GET or POST
        if request.method == 'GET':
            sku = request.args.get('sku')
            site_url = request.args.get('site_url')
            pause = float(request.args.get('pause', 1.0))
            headful = request.args.get('headful', 'false').lower() == 'true'
        else:  # POST
            data = request.get_json() or {}
            sku = data.get('sku')
            site_url = data.get('site_url')
            pause = float(data.get('pause', 1.0))
            headful = data.get('headful', False)
        
        if not sku or not site_url:
            return jsonify({"error": "sku and site_url are required"}), 400
        
        print(f"🔍 Scraping SKU: {sku} from {site_url}")
        
        # Run the universal scraper
        driver = build_simple_driver(headful)
        
        try:
            body_html, image_src, product_url = run_for_sku(driver, site_url, sku, pause)
            
            result = {
                "sku": sku,
                "site_url": site_url,
                "product_url": product_url,
                "description": body_html,
                "images": image_src,
                "image_count": len(image_src.split(';')) if image_src else 0,
                "status": "SUCCESS" if (body_html and image_src) else "PARTIAL" if (body_html or image_src) else "FAILED",
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
            }
            
            print(f"✅ Result: {result['status']} - {result['image_count']} images")
            return jsonify(result)
            
        finally:
            driver.quit()
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/scrape-url', methods=['POST'])
def scrape_url():
    """Scrape directly from product URL."""
    try:
        data = request.get_json()
        product_url = data.get('product_url')
        headful = data.get('headful', False)
        
        if not product_url:
            return jsonify({"error": "product_url is required"}), 400
        
        print(f"🔍 Scraping product URL: {product_url}")
        
        # Run the universal scraper directly on the product URL
        driver = build_simple_driver(headful)
        
        try:
            # Navigate directly to product page
            driver.get(product_url)
            time.sleep(3)
            
            # Extract data directly from product page
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.common.exceptions import TimeoutException, NoSuchElementException
            import re
            
            # Wait for page to load
            time.sleep(3)
            
            # Get product description - try multiple selectors
            description = ""
            desc_selectors = [
                # Ronix specific selectors
                ".detailed-description",
                ".product-detail-description", 
                ".product-description",
                ".product-details",
                ".product-info",
                ".description",
                ".product-content",
                ".product-text",
                ".product-specifications",
                ".specifications",
                "[itemprop='description']",
                ".woocommerce-product-details__short-description",
                ".product__description",
                ".product-single__description",
                "#product-description",
                ".product-overview",
                ".product-summary",
                # Generic content selectors
                ".content",
                ".main-content",
                "main"
            ]
            
            for selector in desc_selectors:
                try:
                    desc_element = driver.find_element(By.CSS_SELECTOR, selector)
                    # Try to get text content first, then HTML if needed
                    raw_content = desc_element.text or desc_element.get_attribute("innerHTML")
                    if raw_content:
                        # Clean the HTML to get readable text
                        description = clean_html_to_text(raw_content)
                        if description and len(description.strip()) > 50:
                            print(f"Found description with selector: {selector}")
                            break
                except NoSuchElementException:
                    continue
            
            # If no description found, try to get any text content
            if not description or len(description.strip()) < 200:
                try:
                    # Try to get main content area with more specific selectors
                    content_selectors = [
                        ".product-detail",
                        ".product-page", 
                        ".product-content",
                        ".product-info",
                        ".product-description",
                        ".detailed-description",
                        "main",
                        ".main-content", 
                        ".content",
                        ".product-specifications",
                        ".specifications"
                    ]
                    
                    for selector in content_selectors:
                        try:
                            content = driver.find_element(By.CSS_SELECTOR, selector)
                            raw_content = content.text or content.get_attribute("innerHTML")
                            if raw_content:
                                text_content = clean_html_to_text(raw_content)
                                if text_content and len(text_content.strip()) > 200:
                                    description = text_content
                                    print(f"Found longer description with selector: {selector}")
                                    break
                        except NoSuchElementException:
                            continue
                    
                    # If still no good description, try to get all text from the page
                    if not description or len(description.strip()) < 200:
                        try:
                            # Get all text content and filter for product-related content
                            all_text = driver.find_element(By.TAG_NAME, "body").text
                            lines = all_text.split('\n')
                            
                            # Filter lines that look like product descriptions
                            product_lines = []
                            for line in lines:
                                line = line.strip()
                                if (len(line) > 20 and 
                                    not any(skip in line.lower() for skip in ['cookie', 'privacy', 'terms', 'copyright', 'menu', 'search', 'login', 'cart', 'checkout', 'footer', 'header', 'navigation', 'popular', 'categories', 'about', 'contact', 'locations', 'become', 'distributor']) and
                                    not line.startswith('http') and
                                    not line.startswith('www.') and
                                    not line.isdigit() and
                                    not line.startswith('$') and
                                    not line.startswith('€') and
                                    not line.startswith('£') and
                                    not line.startswith('EN') and
                                    not line.startswith('Menu') and
                                    not line.startswith('Product Category')):
                                    product_lines.append(line)
                            
                            if product_lines:
                                description = '\n'.join(product_lines[:50])  # Take first 50 relevant lines
                                print(f"Extracted description from page text: {len(description)} characters")
                        except:
                            pass
                except:
                    pass
            
            # Get product images - be more specific
            images = []
            img_selectors = [
                # Product gallery selectors
                ".product-gallery img",
                ".product-images img",
                ".product-photos img", 
                ".gallery img",
                ".product-media img",
                ".product-image img",
                ".main-image img",
                ".featured-image img",
                ".product-thumbnails img",
                ".product-slider img",
                ".swiper-slide img",
                ".product-carousel img",
                
                # E-commerce specific
                ".woocommerce-product-gallery img",
                ".product__media-list img",
                ".product__gallery img",
                ".product__images img",
                
                # Generic product image selectors
                "img[src*='product']",
                "img[alt*='product']",
                "img[class*='product']",
                "img[data-src*='product']",
                "img[data-image]",
                "img[data-zoom-image]",
                
                # Ronix specific (based on the page structure)
                ".product-detail img",
                ".product-page img", 
                ".product-content img",
                ".product-gallery img",
                ".product-images img",
                ".gallery-item img",
                ".swiper-slide img",
                ".product-slider img",
                ".product-carousel img",
                ".product-thumbnails img",
                ".product-media img",
                ".product-photos img"
            ]
            
            for selector in img_selectors:
                try:
                    img_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for img in img_elements:
                        src = img.get_attribute("src") or img.get_attribute("data-src")
                        alt = img.get_attribute("alt") or ""
                        
                        if src and not src.startswith("data:") and "http" in src:
                            # Filter out non-product images
                            skip_terms = [
                                "logo", "icon", "favicon", "banner", "header", "footer",
                                "nav", "menu", "button", "arrow", "social", "facebook",
                                "instagram", "twitter", "youtube", "payment", "visa",
                                "mastercard", "loading", "spinner", "error", "404",
                                "advertisement", "ad", "promo", "sale", "discount"
                            ]
                            
                            # Check if image should be skipped
                            should_skip = False
                            for term in skip_terms:
                                if term.lower() in src.lower() or term.lower() in alt.lower():
                                    should_skip = True
                                    break
                            
                            # Skip very small images
                            if any(size in src for size in ["16x16", "24x24", "32x32", "48x48", "64x64"]):
                                should_skip = True
                            
                            if not should_skip:
                                images.append(src)
                                print(f"Added image: {src[:80]}...")
                    
                    if images:
                        break
                except Exception as e:
                    print(f"Selector {selector} failed: {e}")
                    continue
            
            # Remove duplicates and limit to reasonable number
            unique_images = []
            seen_urls = set()
            for img in images:
                # Clean URL (remove size parameters)
                clean_url = re.sub(r'[?&](w|h|width|height|size|resize|fit|format|auto)=[^&]*', '', img)
                clean_url = re.sub(r'[?&]+$', '', clean_url)
                
                if clean_url not in seen_urls:
                    seen_urls.add(clean_url)
                    unique_images.append(img)
            
            # Limit to 10 images max
            unique_images = unique_images[:10]
            image_src = ";".join(unique_images)
            
            print(f"Found {len(unique_images)} product images")
            print(f"Description length: {len(description)} characters")
            
            result = {
                "product_url": product_url,
                "description": description,
                "images": image_src,
                "image_count": len(image_src.split(';')) if image_src else 0,
                "status": "SUCCESS" if (description and image_src) else "PARTIAL" if (description or image_src) else "FAILED",
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
            }
            
            print(f"✅ Result: {result['status']} - {result['image_count']} images")
            return jsonify(result)
            
        finally:
            driver.quit()
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/scrape-batch', methods=['POST'])
def scrape_batch():
    """Scrape multiple SKUs."""
    try:
        data = request.get_json()
        skus = data.get('skus', [])
        site_url = data.get('site_url')
        pause = float(data.get('pause', 1.0))
        headful = data.get('headful', False)
        
        if not skus or not site_url:
            return jsonify({"error": "skus and site_url are required"}), 400
        
        print(f"🔍 Scraping {len(skus)} SKUs from {site_url}")
        
        results = []
        driver = build_simple_driver(headful)
        
        try:
            for i, sku in enumerate(skus, 1):
                print(f"📦 [{i}/{len(skus)}] Processing SKU: {sku}")
                
                try:
                    body_html, image_src, product_url = run_for_sku(driver, site_url, sku, pause)
                    
                    result = {
                        "sku": sku,
                        "site_url": site_url,
                        "product_url": product_url,
                        "description": body_html,
                        "images": image_src,
                        "image_count": len(image_src.split(';')) if image_src else 0,
                        "status": "SUCCESS" if (body_html and image_src) else "PARTIAL" if (body_html or image_src) else "FAILED",
                        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                    }
                    
                    results.append(result)
                    print(f"✅ {sku}: {result['status']} - {result['image_count']} images")
                    
                except Exception as e:
                    print(f"❌ Error processing {sku}: {e}")
                    results.append({
                        "sku": sku,
                        "site_url": site_url,
                        "product_url": "",
                        "description": "",
                        "images": "",
                        "image_count": 0,
                        "status": "ERROR",
                        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                    })
                
                # Small delay between requests
                if i < len(skus):
                    time.sleep(0.5)
            
            return jsonify({
                "results": results,
                "summary": {
                    "total": len(results),
                    "success": len([r for r in results if r['status'] == 'SUCCESS']),
                    "partial": len([r for r in results if r['status'] == 'PARTIAL']),
                    "failed": len([r for r in results if r['status'] in ['FAILED', 'ERROR']])
                }
            })
            
        finally:
            driver.quit()
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("🚀 Starting Universal Scraper HTTP API...")
    print("📡 Available endpoints:")
    print("   GET  /health - Health check")
    print("   GET  /scrape?sku=SKU&site_url=URL - Scrape single SKU")
    print("   POST /scrape - Scrape single SKU (JSON body)")
    print("   POST /scrape-url - Scrape directly from product URL")
    print("   POST /scrape-batch - Scrape multiple SKUs")
    print()
    print("🌐 Server will run on: http://localhost:5000")
    print()
    
    app.run(host='0.0.0.0', port=5000, debug=True)
