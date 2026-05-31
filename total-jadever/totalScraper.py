
import sys
import io
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

import argparse
import re
import time
from pathlib import Path
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, WebDriverException

SLEEP = 0.25
POST_SEARCH_DELAY = 1
TIMEOUT = 3
PAGE_LOAD_WAIT = 0.5
BETWEEN_PRODUCTS_SLEEP = 0.1
TEST_MODE = False
TEST_LIMIT = 5
# ============================

HOME_URL = "https://www.total-jo.com/"


def make_driver(headful: bool = False):
    """Create and configure Chrome driver"""
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1360,2200")
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--log-level=3")
    d = webdriver.Chrome(options=opts)
    d.set_page_load_timeout(60)
    return d


def clean_text(s: str) -> str:
    """Clean and normalize text"""
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s).strip()


def click_search_element(driver, wait):
    """Open/focus the site search UI and focus the input."""
    try:
        # Prefer the known input id (site-specific)
        input_selectors = [
            "#input_search-box-input-comp-mm1dvrrc",
            "input.snize-input-style",
            ".snize-instant-search input",
            "input[type='search']",
        ]

        # If a separate search "launcher" exists, click it (older snize UI)
        try:
            search_element = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".snize-instant-search"))
            )
            try:
                search_element.click()
            except Exception:
                driver.execute_script("arguments[0].click();", search_element)
        except TimeoutException:
            # No launcher found; that's OK as long as input exists
            pass

        search_input = None
        for selector in input_selectors:
            try:
                search_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector)))
                if search_input and search_input is not False and search_input.is_displayed():
                    break
            except TimeoutException:
                continue

        if search_input and search_input is not False:
            try:
                search_input.click()
            except Exception:
                driver.execute_script("arguments[0].focus();", search_input)

        time.sleep(0.2)
        return bool(search_input and search_input is not False)
    except TimeoutException:
        print("Warning: Could not find search UI/input")
        return False


def search_and_click_product(driver, wait, barcode: str):
    """Step 3: Enter barcode in search, wait 1 second, then click product"""
    try:
        # Find the search input field (usually inside the search element)
        # Try multiple selectors for the search input
        search_input = None
        selectors = [
            "#input_search-box-input-comp-mm1dvrrc",
            "input.snize-input-style",
            ".snize-instant-search input",
            "input[type='search']",
            "input[type='text']",
        ]
        
        for selector in selectors:
            try:
                search_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector)))
                if search_input and search_input is not False and search_input.is_displayed():
                    try:
                        search_input.click()
                    except Exception:
                        driver.execute_script("arguments[0].focus();", search_input)
                    break
            except Exception:
                continue
        
        if not search_input or search_input is False:
            print(f"Could not find search input for barcode: {barcode}")
            return False
        
        # Clear and type barcode
        try:
            search_input.send_keys(Keys.CONTROL, "a")
            search_input.send_keys(Keys.DELETE)
        except Exception:
            try:
                search_input.clear()
            except Exception:
                pass
        search_input.send_keys(barcode)
        # Many Wix search inputs require Enter to trigger results
        try:
            search_input.send_keys(Keys.ENTER)
        except Exception:
            pass
        time.sleep(SLEEP)  # Wait for suggestions to appear
        time.sleep(POST_SEARCH_DELAY)  # Let results stabilize before clicking
        
        # Wait for search results and click the first product
        # (site may show Wix search dropdown or Snize results grid)
        result_click_selectors = [
            # Snize instant-search / collection style
            ".snize-product a",
            ".snize-product",
            ".snize-ac-results a",
            ".snize-ac-results li",
            # Wix site search suggestions / results
            "[data-hook*='search-results'] a",
            "[data-hook*='search'] a",
            "a[href*='/product-page/']",
            "a[href*='/product-page']",
            "a[href*='/products/']",
        ]

        def _find_first_clickable_result(drv):
            for css in result_click_selectors:
                try:
                    nodes = drv.find_elements(By.CSS_SELECTOR, css)
                except Exception:
                    continue
                for n in nodes:
                    try:
                        if n and n.is_displayed() and n.is_enabled():
                            return n
                    except Exception:
                        continue
            return None

        try:
            product = WebDriverWait(driver, TIMEOUT).until(lambda d: _find_first_clickable_result(d))
        except TimeoutException:
            product = None

        if not product:
            print(f"Product not found for barcode: {barcode}")
            return False

        try:
            product.click()
        except Exception:
            driver.execute_script("arguments[0].click();", product)
        time.sleep(PAGE_LOAD_WAIT)
        return True
            
    except Exception as e:
        print(f"Error searching for barcode {barcode}: {e}")
        return False


def get_full_size_image_url(url):
    """Convert a thumbnail/small image URL to full-size (Wix, CDNs, query params)."""
    if not url:
        return url
    
    original_url = url
    
    # Wix static: /v1/fill/w_300,h_300,al_c/... -> request large size (e.g. 2400)
    if "/v1/fill/" in url:
        url = re.sub(r'/v1/fill/w_\d+,h_\d+', '/v1/fill/w_2400,h_2400', url, flags=re.IGNORECASE)
        # Some Wix URLs use w_XXX,h_YYY,al_c - keep al_c
        if url != original_url:
            return url
    
    # Remove query parameters that specify size
    url = re.sub(r'[?&](w|width|h|height|size|resize|scale|fit|quality)=\d+', '', url)
    
    # Remove common size suffixes from filename (e.g., image_300x300.jpg -> image.jpg)
    url = re.sub(r'_(\d+)x(\d+)\.(jpg|jpeg|png|webp|gif)', r'.\3', url, flags=re.IGNORECASE)
    
    # Remove common thumbnail indicators
    url = re.sub(r'[-_]thumb(nail)?[-_]', '', url, flags=re.IGNORECASE)
    url = re.sub(r'[-_]small[-_]', '', url, flags=re.IGNORECASE)
    url = re.sub(r'[-_]medium[-_]', '', url, flags=re.IGNORECASE)
    url = re.sub(r'[-_]large[-_]', '', url, flags=re.IGNORECASE)
    
    # Size folders (e.g. /300x300/ -> /)
    url = re.sub(r'/\d+x\d+/', '/', url)
    url = re.sub(r'/thumb(nail)?s?/', '/', url, flags=re.IGNORECASE)
    url = re.sub(r'/small/', '/', url, flags=re.IGNORECASE)
    url = re.sub(r'/medium/', '/', url, flags=re.IGNORECASE)
    url = re.sub(r'/resize/\d+x\d+/', '/', url, flags=re.IGNORECASE)
    url = re.sub(r'/crop/\d+x\d+/', '/', url, flags=re.IGNORECASE)
    url = re.sub(r'/fit/\d+x\d+/', '/', url, flags=re.IGNORECASE)
    
    if url != original_url:
        return url
    url = re.sub(r'/(thumb|small|medium|large)/', '/', url, flags=re.IGNORECASE)
    return url


def get_base_image_url(url):
    """Get base URL without size parameters for comparison"""
    if not url:
        return ""
    
    if '/v1/fill/' in url:
        base = url.split('/v1/fill/')[0]
        return base.lower()
    
    base = url.split('?')[0]
    base = re.sub(r'_\d+x\d+\.(jpg|jpeg|png|webp|gif)', r'.\1', base, flags=re.IGNORECASE)
    base = re.sub(r'/\d+x\d+/', '/', base)
    return base.lower()


def get_image_size_from_url(url):
    """Extract size from URL (width x height) to determine largest"""
    if not url:
        return 0
    
    wix_match = re.search(r'/v1/fill/w_(\d+),h_(\d+)', url)
    if wix_match:
        width = int(wix_match.group(1))
        height = int(wix_match.group(2))
        return width * height  
    
    size_match = re.search(r'[_\?/](\d+)x(\d+)', url)
    if size_match:
        width = int(size_match.group(1))
        height = int(size_match.group(2))
        return width * height  
    
    width_match = re.search(r'[?&]w=(\d+)', url)
    height_match = re.search(r'[?&]h=(\d+)', url)
    if width_match and height_match:
        return int(width_match.group(1)) * int(height_match.group(2))
    if not re.search(r'[_\?/]\d+x\d+', url) and '/v1/fill/' not in url:
        return 999999 
    return 0


def _get_image_from_media_wrapper(driver, seen_urls, image_map):
    """Scrape image from element with class 'v4kqzh media-wrapper-hook uok6tq' (Wix media wrapper)."""
    try:
        # Use stable part of class: media-wrapper-hook (v4kqzh/uok6tq may be hashed)
        wrappers = driver.find_elements(By.CSS_SELECTOR, "[class*='media-wrapper-hook']")
        for node in wrappers:
            img_url = None
            if node.tag_name == "img":
                img_url = (node.get_attribute("src") or node.get_attribute("data-src") or
                           node.get_attribute("data-original") or node.get_attribute("data-full") or "")
            else:
                try:
                    img = node.find_element(By.TAG_NAME, "img")
                    img_url = (img.get_attribute("src") or img.get_attribute("data-src") or
                               img.get_attribute("data-original") or img.get_attribute("data-full") or "")
                except Exception:
                    style = node.get_attribute("style") or ""
                    bg = re.search(r'url\(["\']?([^"\']+)["\']?\)', style)
                    if bg:
                        img_url = bg.group(1)
            if img_url:
                img_url = img_url.split("#")[0].strip()
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                elif img_url.startswith("/"):
                    img_url = "https://www.total-jo.com" + img_url
                if img_url and img_url not in seen_urls:
                    seen_urls.add(img_url)
                    base_url = get_base_image_url(img_url)
                    size = get_image_size_from_url(img_url)
                    if base_url and (base_url not in image_map or size > image_map[base_url][1]):
                        image_map[base_url] = (img_url, size)
    except Exception:
        pass


def get_product_images(driver, wait):
    image_map = {}  
    seen_urls = set()  
    
    try:
        # First: scrape image from media-wrapper-hook (v4kqzh media-wrapper-hook uok6tq)
        _get_image_from_media_wrapper(driver, seen_urls, image_map)
        
        try:
            active_slide = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, '.slick-slide.slick-active.slick-current'))
            )
            active_slide.click()
            time.sleep(0.4)
        except Exception as e:
            print(f"Warning: Could not click active slick slide: {e}")
        
        max_iterations = 20 
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            try:
                modal_gallery_nodes = driver.find_elements(By.CSS_SELECTOR, '[data-hook="modal-gallery-media-node"]')
                
                for node in modal_gallery_nodes:
                    try:
                        img_url = None
                        
                        # Check if it's an img element
                        if node.tag_name == "img":
                            img_url = (node.get_attribute("src") or 
                                      node.get_attribute("data-src") or 
                                      node.get_attribute("data-original") or
                                      node.get_attribute("data-full") or
                                      node.get_attribute("data-full-src") or "")
                        else:
                            # Check for img inside the node
                            try:
                                img = node.find_element(By.TAG_NAME, "img")
                                if img:
                                    img_url = (img.get_attribute("src") or 
                                              img.get_attribute("data-src") or 
                                              img.get_attribute("data-original") or
                                              img.get_attribute("data-full") or
                                              img.get_attribute("data-full-src") or "")
                            except:
                                # Check for background image or data attributes
                                img_url = (node.get_attribute("data-src") or 
                                          node.get_attribute("data-image") or 
                                          node.get_attribute("data-original") or
                                          node.get_attribute("data-full") or "")
                                
                                # Check style for background image
                                style = node.get_attribute("style") or ""
                                bg_match = re.search(r'url\(["\']?([^"\']+)["\']?\)', style)
                                if bg_match:
                                    img_url = bg_match.group(1) or img_url
                        
                        # Check srcset for largest image
                        if node.tag_name == "img":
                            srcset = node.get_attribute("srcset") or ""
                        else:
                            try:
                                img = node.find_element(By.TAG_NAME, "img")
                                srcset = img.get_attribute("srcset") or "" if img else ""
                            except:
                                srcset = ""
                        
                        if srcset:
                            parts = srcset.split(",")
                            largest = ""
                            largest_size = 0
                            for part in parts:
                                part = part.strip()
                                if " " in part:
                                    url_part, size_part = part.rsplit(" ", 1)
                                    try:
                                        size_match = re.search(r'\d+', size_part)
                                        if size_match:
                                            size = int(size_match.group())
                                            if size > largest_size:
                                                largest_size = size
                                                largest = url_part.strip()
                                    except:
                                        pass
                            if largest:
                                img_url = largest
                        
                        if img_url:
                            # Normalize URL
                            img_url = img_url.split('#')[0].strip()
                            if img_url.startswith('//'):
                                img_url = 'https:' + img_url
                            elif img_url.startswith('/'):
                                img_url = 'https://www.total-jo.com' + img_url
                            
                            if img_url and img_url not in seen_urls:
                                seen_urls.add(img_url)
                                
                                base_url = get_base_image_url(img_url)
                                size = get_image_size_from_url(img_url)
                                
                                if base_url:
                                    if base_url not in image_map or size > image_map[base_url][1]:
                                        image_map[base_url] = (img_url, size)
                                        print(f"  Found image (size: {size}): {img_url[:80]}...")
                    except Exception:
                        continue
            except Exception as e:
                print(f"Warning: Could not find modal-gallery-media-node: {e}")
            
            images_before = len(image_map)
            
            try:
                next_arrow_clicked = False
                try:
                    next_arrow = driver.find_element(By.CSS_SELECTOR, '[data-hook="modal-gallery-arrow-next"]')
                    if next_arrow and next_arrow.is_displayed() and next_arrow.is_enabled():
                        next_arrow.click()
                        time.sleep(0.25)
                        next_arrow_clicked = True
                except:
                    pass
                
                next_button_clicked = False
                try:
                    next_button = driver.find_element(By.CSS_SELECTOR, '[aria-label="التالي"]')
                    if next_button and next_button.is_displayed() and next_button.is_enabled():
                        next_button.click()
                        time.sleep(0.35)
                        next_button_clicked = True
                except:
                    pass
                
                if not next_arrow_clicked and not next_button_clicked:
                    break
                if len(image_map) == images_before and iteration > 3:
                    time.sleep(0.4)
                    try:
                        modal_gallery_nodes = driver.find_elements(By.CSS_SELECTOR, '[data-hook="modal-gallery-media-node"]')
                        for node in modal_gallery_nodes:
                            try:
                                if node.tag_name == "img":
                                    img_url = node.get_attribute("src") or node.get_attribute("data-src") or ""
                                else:
                                    img = node.find_element(By.TAG_NAME, "img")
                                    img_url = img.get_attribute("src") or img.get_attribute("data-src") or "" if img else ""
                                
                                if img_url:
                                    img_url = img_url.split('#')[0].strip()
                                    if img_url.startswith('//'):
                                        img_url = 'https:' + img_url
                                    elif img_url.startswith('/'):
                                        img_url = 'https://www.total-jo.com' + img_url
                                    
                                    if img_url and img_url not in seen_urls:
                                        seen_urls.add(img_url)
                                        base_url = get_base_image_url(img_url)
                                        size = get_image_size_from_url(img_url)
                                        if base_url:
                                            if base_url not in image_map or size > image_map[base_url][1]:
                                                image_map[base_url] = (img_url, size)
                                                print(f"  Found image (size: {size}): {img_url[:80]}...")
                            except:
                                continue
                    except:
                        pass
                    if len(image_map) == images_before:
                        break
                    
            except Exception as e:
                break
        try:
            modal_gallery_nodes = driver.find_elements(By.CSS_SELECTOR, '[data-hook="modal-gallery-media-node"]')
            
            for node in modal_gallery_nodes:
                try:
                    if node.tag_name == "img":
                        img_url = (node.get_attribute("src") or 
                                  node.get_attribute("data-src") or 
                                  node.get_attribute("data-original") or
                                  node.get_attribute("data-full") or
                                  node.get_attribute("data-full-src") or "")
                    else:
                        img = node.find_element(By.TAG_NAME, "img")
                        if img:
                            img_url = (img.get_attribute("src") or 
                                      img.get_attribute("data-src") or 
                                      img.get_attribute("data-original") or
                                      img.get_attribute("data-full") or
                                      img.get_attribute("data-full-src") or "")
                        else:
                            img_url = (node.get_attribute("data-src") or 
                                      node.get_attribute("data-image") or 
                                      node.get_attribute("data-original") or
                                      node.get_attribute("data-full") or "")
                            style = node.get_attribute("style") or ""
                            bg_match = re.search(r'url\(["\']?([^"\']+)["\']?\)', style)
                            if bg_match:
                                img_url = bg_match.group(1) or img_url
                    if node.tag_name == "img":
                        srcset = node.get_attribute("srcset") or ""
                    else:
                        try:
                            img = node.find_element(By.TAG_NAME, "img")
                            srcset = img.get_attribute("srcset") or "" if img else ""
                        except:
                            srcset = ""
                    
                    if srcset:
                        parts = srcset.split(",")
                        largest = ""
                        largest_size = 0
                        for part in parts:
                            part = part.strip()
                            if " " in part:
                                url_part, size_part = part.rsplit(" ", 1)
                                try:
                                    size_match = re.search(r'\d+', size_part)
                                    if size_match:
                                        size = int(size_match.group())
                                        if size > largest_size:
                                            largest_size = size
                                            largest = url_part.strip()
                                except:
                                    pass
                        if largest:
                            img_url = largest
                    
                    if img_url:
                        img_url = img_url.split('#')[0].strip()
                        if img_url.startswith('//'):
                            img_url = 'https:' + img_url
                        elif img_url.startswith('/'):
                            img_url = 'https://www.total-jo.com' + img_url
                        
                        if img_url and img_url not in seen_urls:
                            seen_urls.add(img_url)
                            
                            base_url = get_base_image_url(img_url)
                            size = get_image_size_from_url(img_url)
                            
                            if base_url:
                                if base_url not in image_map or size > image_map[base_url][1]:
                                    image_map[base_url] = (img_url, size)
                except Exception:
                    continue
        except Exception as e:
            print(f"Warning: Could not find modal-gallery-media-node elements: {e}")
        
        try:
            slick_slides = driver.find_elements(By.CSS_SELECTOR, '.slick-slide.slick-active.slick-current')
            
            for slide in slick_slides:
                try:
                    if slide.tag_name == "img":
                        img_url = (slide.get_attribute("src") or 
                                  slide.get_attribute("data-src") or 
                                  slide.get_attribute("data-original") or
                                  slide.get_attribute("data-full") or
                                  slide.get_attribute("data-full-src") or "")
                    else:
                        try:
                            img = slide.find_element(By.TAG_NAME, "img")
                            if img:
                                img_url = (img.get_attribute("src") or 
                                          img.get_attribute("data-src") or 
                                          img.get_attribute("data-original") or
                                          img.get_attribute("data-full") or
                                          img.get_attribute("data-full-src") or "")
                            else:
                                img_url = ""
                        except:
                            img_url = (slide.get_attribute("data-src") or 
                                      slide.get_attribute("data-image") or 
                                      slide.get_attribute("data-original") or
                                      slide.get_attribute("data-full") or "")
                            
                            style = slide.get_attribute("style") or ""
                            bg_match = re.search(r'url\(["\']?([^"\']+)["\']?\)', style)
                            if bg_match:
                                img_url = bg_match.group(1) or img_url
                    
                    if slide.tag_name == "img":
                        srcset = slide.get_attribute("srcset") or ""
                    else:
                        try:
                            img = slide.find_element(By.TAG_NAME, "img")
                            srcset = img.get_attribute("srcset") or "" if img else ""
                        except:
                            srcset = ""
                    
                    if srcset:
                        parts = srcset.split(",")
                        largest = ""
                        largest_size = 0
                        for part in parts:
                            part = part.strip()
                            if " " in part:
                                url_part, size_part = part.rsplit(" ", 1)
                                try:
                                    size_match = re.search(r'\d+', size_part)
                                    if size_match:
                                        size = int(size_match.group())
                                        if size > largest_size:
                                            largest_size = size
                                            largest = url_part.strip()
                                except:
                                    pass
                        if largest:
                            img_url = largest
                    
                    if img_url:
                        # Normalize URL
                        img_url = img_url.split('#')[0].strip()
                        if img_url.startswith('//'):
                            img_url = 'https:' + img_url
                        elif img_url.startswith('/'):
                            img_url = 'https://www.total-jo.com' + img_url
                        
                        if img_url and img_url not in seen_urls:
                            seen_urls.add(img_url)
                            
                            base_url = get_base_image_url(img_url)
                            size = get_image_size_from_url(img_url)
                            
                            if base_url:
                                if base_url not in image_map or size > image_map[base_url][1]:
                                    image_map[base_url] = (img_url, size)
                except Exception:
                    continue
        except Exception as e:
            print(f"Warning: Could not find slick-slide elements: {e}")
        
        try:
            js_slick_images = driver.execute_script("""
                var images = [];
                var slides = document.querySelectorAll('.slick-slide');
                slides.forEach(function(slide) {
                    var img = slide.tagName === 'IMG' ? slide : slide.querySelector('img');
                    if (img) {
                        var url = img.getAttribute('src') || 
                                  img.getAttribute('data-src') || 
                                  img.getAttribute('data-original') ||
                                  img.getAttribute('data-full') ||
                                  img.getAttribute('data-full-src');
                        
                        // Check srcset for largest
                        var srcset = img.getAttribute('srcset');
                        if (srcset) {
                            var parts = srcset.split(',');
                            var largest = '';
                            var maxSize = 0;
                            parts.forEach(function(part) {
                                var match = part.trim().match(/(\\S+)\\s+(\\d+)/);
                                if (match) {
                                    var size = parseInt(match[2]);
                                    if (size > maxSize) {
                                        maxSize = size;
                                        largest = match[1];
                                    }
                                }
                            });
                            if (largest) url = largest;
                        }
                        
                        if (url) images.push(url);
                    } else {
                        // Check data attributes on the slide itself
                        var url = slide.getAttribute('data-src') || 
                                  slide.getAttribute('data-image') || 
                                  slide.getAttribute('data-original') ||
                                  slide.getAttribute('data-full');
                        if (url) images.push(url);
                    }
                });
                return images;
            """)
            
            for img_url in js_slick_images:
                if img_url:
                    img_url = img_url.split('#')[0].strip()
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
                    elif img_url.startswith('/'):
                        img_url = 'https://www.total-jo.com' + img_url
                    
                    if img_url and img_url not in seen_urls:
                        seen_urls.add(img_url)
                        
                        base_url = get_base_image_url(img_url)
                        size = get_image_size_from_url(img_url)
                        
                        if base_url:
                            if base_url not in image_map or size > image_map[base_url][1]:
                                image_map[base_url] = (img_url, size)
        except Exception as e:
            pass
        
        try:
            js_images = driver.execute_script("""
                var images = [];
                var nodes = document.querySelectorAll('[data-hook="modal-gallery-media-node"]');
                nodes.forEach(function(node) {
                    var img = node.tagName === 'IMG' ? node : node.querySelector('img');
                    if (img) {
                        var url = img.getAttribute('src') || 
                                  img.getAttribute('data-src') || 
                                  img.getAttribute('data-original') ||
                                  img.getAttribute('data-full') ||
                                  img.getAttribute('data-full-src');
                        
                        // Check srcset for largest
                        var srcset = img.getAttribute('srcset');
                        if (srcset) {
                            var parts = srcset.split(',');
                            var largest = '';
                            var maxSize = 0;
                            parts.forEach(function(part) {
                                var match = part.trim().match(/(\\S+)\\s+(\\d+)/);
                                if (match) {
                                    var size = parseInt(match[2]);
                                    if (size > maxSize) {
                                        maxSize = size;
                                        largest = match[1];
                                    }
                                }
                            });
                            if (largest) url = largest;
                        }
                        
                        if (url) images.push(url);
                    } else {
                        // Check data attributes on the node itself
                        var url = node.getAttribute('data-src') || 
                                  node.getAttribute('data-image') || 
                                  node.getAttribute('data-original') ||
                                  node.getAttribute('data-full');
                        if (url) images.push(url);
                    }
                });
                return images;
            """)
            
            for img_url in js_images:
                if img_url:
                    img_url = img_url.split('#')[0].strip()
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
                    elif img_url.startswith('/'):
                        img_url = 'https://www.total-jo.com' + img_url
                    
                    if img_url and img_url not in seen_urls:
                        seen_urls.add(img_url)
                        
                        # Get base URL and size
                        base_url = get_base_image_url(img_url)
                        size = get_image_size_from_url(img_url)
                        
                        # Keep only the largest version
                        if base_url:
                            if base_url not in image_map or size > image_map[base_url][1]:
                                image_map[base_url] = (img_url, size)
        except Exception as e:
            pass
        swiper = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".swiper.swiper-initialized.swiper-horizontal.swiper-rtl.swiper-watch-progress.AG_dFf.gxDeG2"))
        )
        img_elements = swiper.find_elements(By.TAG_NAME, "img")
        parent_elements = swiper.find_elements(By.CSS_SELECTOR, ".swiper-slide, [class*='slide'], [class*='image']")
        
        all_elements = list(img_elements) + list(parent_elements)
        
        for element in all_elements:
            try:
                img_url = None
                if element.tag_name == "img":
                    data_full = element.get_attribute("data-full") or ""
                    data_full_src = element.get_attribute("data-full-src") or ""
                    data_original_full = element.get_attribute("data-original-full") or ""
                    data_large = element.get_attribute("data-large") or ""
                    data_zoom = element.get_attribute("data-zoom") or ""
                    data_zoom_src = element.get_attribute("data-zoom-src") or ""
                    data_href = element.get_attribute("data-href") or ""
                    src = element.get_attribute("src") or ""
                    data_src = element.get_attribute("data-src") or ""
                    data_original = element.get_attribute("data-original") or ""
                    data_lazy = element.get_attribute("data-lazy-src") or ""
                    srcset = element.get_attribute("srcset") or ""
                    img_url = (data_full or data_full_src or data_original_full or 
                              data_large or data_zoom or data_zoom_src or 
                              data_href or data_src or src or data_original or data_lazy)
                    if srcset:
                        parts = srcset.split(",")
                        largest = ""
                        largest_size = 0
                        for part in parts:
                            part = part.strip()
                            if " " in part:
                                url_part, size_part = part.rsplit(" ", 1)
                                try:
                                    # Parse size (could be "800w", "2x", etc.)
                                    size_match = re.search(r'\d+', size_part)
                                    if size_match:
                                        size = int(size_match.group())
                                        if size > largest_size:
                                            largest_size = size
                                            largest = url_part.strip()
                                except:
                                    pass
                        if largest and (not img_url or largest_size > 1000):
                            img_url = largest
                
                else:
                    data_full = element.get_attribute("data-full") or ""
                    data_full_src = element.get_attribute("data-full-src") or ""
                    data_image = element.get_attribute("data-image") or ""
                    data_original = element.get_attribute("data-original") or ""
                    data_href = element.get_attribute("data-href") or ""
                    style = element.get_attribute("style") or ""
                    bg_match = re.search(r'url\(["\']?([^"\']+)["\']?\)', style)
                    bg_url = bg_match.group(1) if bg_match else ""
                    
                    img_url = (data_full or data_full_src or data_image or 
                              data_original or data_href or bg_url)
                if img_url:
                    full_size_url = get_full_size_image_url(img_url)
                    full_size_url = full_size_url.split('#')[0].strip()
                    if full_size_url.startswith('//'):
                        full_size_url = 'https:' + full_size_url
                    elif full_size_url.startswith('/'):
                        full_size_url = 'https://www.total-jo.com' + full_size_url
                    original_normalized = img_url.split('#')[0].strip()
                    if original_normalized.startswith('//'):
                        original_normalized = 'https:' + original_normalized
                    elif original_normalized.startswith('/'):
                        original_normalized = 'https://www.total-jo.com' + original_normalized
                    candidates = [full_size_url, original_normalized]
                    for candidate in candidates:
                        if candidate and candidate not in seen_urls:
                            if any(ext in candidate.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']) or 'image' in candidate.lower():
                                seen_urls.add(candidate)
                                
                                base_url = get_base_image_url(candidate)
                                size = get_image_size_from_url(candidate)
                                
                                if base_url:
                                    if base_url not in image_map or size > image_map[base_url][1]:
                                        image_map[base_url] = (candidate, size)
                                break 
            except Exception:
                continue
        
        try:
            links = swiper.find_elements(By.CSS_SELECTOR, "a[href*='.jpg'], a[href*='.jpeg'], a[href*='.png'], a[href*='.webp']")
            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    if href and href not in seen_urls:
                        full_href = href
                        if full_href.startswith('//'):
                            full_href = 'https:' + full_href
                        elif full_href.startswith('/'):
                            full_href = 'https://www.total-jo.com' + full_href
                        if full_href not in seen_urls:
                            seen_urls.add(full_href)
                            
                            base_url = get_base_image_url(full_href)
                            size = get_image_size_from_url(full_href)
                            if base_url:
                                if base_url not in image_map or size > image_map[base_url][1]:
                                    image_map[base_url] = (full_href, size)
                except:
                    continue
        except:
            pass
        
    except TimeoutException:
        print("Warning: Could not find swiper container for images")
    except Exception as e:
        print(f"Error getting images: {e}")
    
    # Prefer full-size URL for every image
    final_images = []
    for url, _ in image_map.values():
        full_url = get_full_size_image_url(url)
        full_url = (full_url or url).split("#")[0].strip()
        if full_url.startswith("//"):
            full_url = "https:" + full_url
        elif full_url.startswith("/"):
            full_url = "https://www.total-jo.com" + full_url
        final_images.append(full_url)
    print(f"  Total unique images (full-size): {len(final_images)}")
    return final_images


def get_product_title(driver, wait):
    """Get product title from data-hook='product-title'"""
    try:
        title_element = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-hook='product-title']"))
        )
        return clean_text(title_element.text or title_element.get_attribute("textContent") or "")
    except TimeoutException:
        print("Warning: Could not find product title")
        return ""
    except Exception as e:
        print(f"Error getting title: {e}")
        return ""


def get_product_description(driver, wait):
    """Get product description from data-hook='info-section-description'"""
    try:
        desc_element = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-hook='info-section-description']"))
        )
        return clean_text(desc_element.text or desc_element.get_attribute("textContent") or "")
    except TimeoutException:
        print("Warning: Could not find product description")
        return ""
    except Exception as e:
        print(f"Error getting description: {e}")
        return ""


def _digits_only(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"\D+", "", s)


def get_page_barcode(driver, wait, expected_barcode: str = "") -> str:
    """
    Read the barcode shown on the product page from the <p> under class 'LvOu6J'.
    Returns cleaned visible text (not digits-normalized).
    """
    try:
        expected_digits = _digits_only(clean_text(str(expected_barcode)))

        # Prefer <p> nodes under LvOu6J, but pick the one that matches the digits
        best_txt = ""
        best_digits_len = -1

        try:
            ps = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".LvOu6J p")))
        except TimeoutException:
            ps = []

        for p in ps:
            try:
                txt = clean_text(p.text or p.get_attribute("textContent") or "")
                if not txt:
                    continue
                digits = _digits_only(txt)
                if expected_digits and digits == expected_digits:
                    return txt
                if len(digits) > best_digits_len:
                    best_txt = txt
                    best_digits_len = len(digits)
            except Exception:
                continue

        # Fallback: sometimes the digits are on the container itself, not the <p>
        if not best_txt:
            try:
                container = driver.find_element(By.CSS_SELECTOR, ".LvOu6J")
                txt = clean_text(container.text or container.get_attribute("textContent") or "")
                if txt:
                    best_txt = txt
            except Exception:
                pass

        return best_txt
    except TimeoutException:
        return ""
    except Exception:
        return ""


def scrape_product(driver, wait, barcode: str):
    """Main scraping function for a single product"""
    result = {
        "BAR CODE": barcode,
        "Page_Barcode": "",
        "Title": "",
        "Description": "",
        "Images": "",
        "Product_URL": "",
        "Status": "NOT_FOUND"
    }
    
    try:
        driver.get(HOME_URL)
        # Wait for either the legacy search launcher or the current Wix search input
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "#input_search-box-input-comp-mm1dvrrc")
                or d.find_elements(By.CSS_SELECTOR, ".snize-instant-search")
            )
        except TimeoutException:
            time.sleep(0.8)
        if not click_search_element(driver, wait):
            result["Status"] = "SEARCH_ELEMENT_NOT_FOUND"
            return result
        if not search_and_click_product(driver, wait, barcode):
            result["Status"] = "PRODUCT_NOT_FOUND"
            return result

        # Verify the opened product page belongs to the same barcode
        page_barcode = get_page_barcode(driver, wait, barcode)
        result["Page_Barcode"] = page_barcode
        if page_barcode:
            expected_clean = clean_text(str(barcode))
            if page_barcode != expected_clean and _digits_only(page_barcode) != _digits_only(expected_clean):
                result["Product_URL"] = driver.current_url
                result["Status"] = "PRODUCT_NOT_FOUND"
                return result
        else:
            # Barcode not found on product page
            result["Product_URL"] = driver.current_url
            result["Status"] = "PRODUCT_NOT_FOUND"
            return result

        result["Product_URL"] = driver.current_url
        result["Title"] = get_product_title(driver, wait)
        result["Description"] = get_product_description(driver, wait)
        images = get_product_images(driver, wait)
        result["Images"] = ", ".join(images) if images else ""
        
        result["Status"] = "SUCCESS" if result["Title"] else "NO_DATA"
        
    except Exception as e:
        print(f"Error scraping barcode {barcode}: {e}")
        result["Status"] = f"ERROR: {str(e)}"
    
    return result


def main():
    """Main execution function"""
    ap = argparse.ArgumentParser(description="Total JO scraper by barcode")
    ap.add_argument("--in", dest="inp", required=True, help="Input Excel file")
    ap.add_argument("--out", required=True, help="Output Excel file")
    ap.add_argument("--sku-col", dest="sku_col", default=None, help="Barcode column name (auto-detect if omitted)")
    ap.add_argument("--headful", action="store_true", help="Show browser window")
    args = ap.parse_args()

    input_path = Path(args.inp)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.inp}")
        return

    try:
        df = pd.read_excel(input_path)
    except Exception as e:
        print(f"Error reading Excel file: {e}")
        return
    barcode_col = None
    possible_names = [args.sku_col or "BAR CODE", "BAR CODE", "Barcode", "BARCODE", "رقم الباركود", "باركود", "Barcode"]
    for col in df.columns:
        col_str = str(col).strip()
        if col_str in possible_names or "باركود" in col_str or "barcode" in col_str.lower() or "bar code" in col_str.lower():
            barcode_col = col
            break
    
    if not barcode_col:
        print(f"Error: Could not find barcode column. Available columns: {list(df.columns)}")
        print("Please update BARCODE_COL in the script or rename your column.")
        return
    
    print(f"Using barcode column: '{barcode_col}'")
    if TEST_MODE:
        df = df.head(TEST_LIMIT).copy()
        print(f"TEST MODE: Processing only first {TEST_LIMIT} items")
    driver = make_driver(headful=args.headful)
    wait = WebDriverWait(driver, TIMEOUT)

    results = []
    
    try:
        barcodes = df[barcode_col].astype(str).fillna("").tolist()
        total = len(barcodes)
        
        for idx, barcode in enumerate(barcodes, 1):
            barcode = str(barcode).strip()
            if not barcode or barcode.lower() in ["nan", "none", ""]:
                print(f"[{idx}/{total}] Skipping empty barcode")
                results.append({
                    "BAR CODE": barcode,
                    "Page_Barcode": "",
                    "Title": "",
                    "Description": "",
                    "Images": "",
                    "Product_URL": "",
                    "Status": "EMPTY_BARCODE"
                })
                continue
            
            print(f"[{idx}/{total}] Processing barcode: {barcode}")
            result = scrape_product(driver, wait, barcode)
            results.append(result)
            
            status_msg = result["Status"]
            title_preview = result["Title"][:50] if result["Title"] else "N/A"
            img_count = len(result["Images"].split(", ")) if result["Images"] else 0
            print(f"    -> {status_msg} | Title: {title_preview} | Images: {img_count}")
            
            time.sleep(BETWEEN_PRODUCTS_SLEEP)
    
    finally:
        driver.quit()
    
    results_df = pd.DataFrame(results)
    try:
        output_df = df.merge(results_df, left_on=barcode_col, right_on="BAR CODE", how="left", suffixes=("", "_scraped"))
    except Exception:
        output_df = pd.concat([df, results_df], axis=1)
    if len(output_df) == 0:
        output_df = results_df
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_excel(output_path, index=False)
    print(f"\nDone! Results saved to: {args.out}")


if __name__ == "__main__":
    main()

