import pandas as pd
import time
import urllib.parse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

def build_driver(headful: bool = True):
    opts = Options()
    if not headful:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-extensions")
    # opts.add_argument("--start-maximized") # Optional
    driver = webdriver.Chrome(options=opts)
    # Set page load timeout
    driver.set_page_load_timeout(120)  # 120 seconds timeout
    return driver

def scrape_toolmart(max_products: int = None, input_file: str = 'data.xlsx', output_file: str = 'data_updated.xlsx', skip_completed: bool = True):
    # Load the Excel file
    excel_path = input_file
    try:
        df = pd.read_excel(excel_path)
    except FileNotFoundError:
        print(f"Error: Could not find {excel_path}")
        return

    # Create new columns if they don't exist
    for col in ['Scraped_Title', 'Scraped_Image_URL', 'Scraped_Description', 'Product_Link', 'Scrape_Status']:
        if col not in df.columns:
            df[col] = None
    
    # Count how many are already completed
    if skip_completed:
        completed_mask = df['Scrape_Status'].notna() & (df['Scrape_Status'] == 'Success')
        completed_count = completed_mask.sum()
        print(f"Found {completed_count} already completed products. Skipping them.")
        remaining = len(df) - completed_count
        print(f"Will process {remaining} remaining products.")

    # Base URL
    base_url = "https://toolmart.me"

    # Initialize Driver (headful mode - Chrome visible)
    driver = build_driver(headful=True)

    # Counter for processed products
    processed_count = 0
    
    try:
        # Iterate over SKUs
        for index in df.index:
            # Limit to max_products if specified
            if max_products is not None and processed_count >= max_products:
                print(f"\nReached limit of {max_products} products. Stopping.")
                break
            
            # Skip if already successfully scraped
            if skip_completed:
                status = df.at[index, 'Scrape_Status']
                if pd.notna(status) and status == 'Success':
                    continue
            
            sku_raw = df.at[index, 'SKU']
            if pd.isna(sku_raw):
                continue
            
            sku = str(sku_raw).strip()
            if not sku:
                continue

            processed_count += 1
            print(f"\n[{processed_count}] Processing SKU: {sku}...")

            # 1. Search for the SKU
            # Direct search URL or typing in search box? URL is faster.
            search_url = f"{base_url}/en/search?q={urllib.parse.quote(sku)}"
            try:
                driver.get(search_url)
            except TimeoutException:
                print(f"  Timeout loading search page for {sku}. Skipping.")
                df.at[index, 'Scrape_Status'] = "Timeout: Search Page"
                continue

            # Wait for results or no results
            # Similar to arabiMart logic, wait for potential product links
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/products/']"))
                )
            except TimeoutException:
                print(f"  No results found for {sku}")
                df.at[index, 'Scrape_Status'] = "No Search Results"
                continue

            # 2. Find and click the first product
            try:
                # Find all links containing '/products/'
                product_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/products/']")
                
                # Filter out irrelevant links if any (though /products/ is usually specific)
                target_link = None
                for link in product_links:
                    href = link.get_attribute("href")
                    if href:
                        target_link = link
                        break # Take the first one
                
                if not target_link:
                    print("  No product link found in results.")
                    df.at[index, 'Scrape_Status'] = "No Product Link"
                    continue

                product_url = target_link.get_attribute("href")
                print(f"  Found product: {product_url}")
                df.at[index, 'Product_Link'] = product_url
                
                # Navigate to product page
                # Clicking might be safer if there's tracking or dynamic loading, 
                # but direct get on the href is usually fine and more robust against click interception.
                try:
                    driver.get(product_url)
                except TimeoutException:
                    print(f"  Timeout loading product page. Skipping.")
                    df.at[index, 'Scrape_Status'] = "Timeout: Product Page"
                    continue
                
            except Exception as e:
                print(f"  Error navigating to product: {e}")
                df.at[index, 'Scrape_Status'] = f"Navigation Error: {e}"
                continue

            # 3. VERIFY SKU (class="f8pr-codes")
            try:
                # Wait for SKU element
                sku_element = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".f8pr-codes"))
                )
                page_sku_text = sku_element.text.strip() # e.g. "SKU: THIA1234"
                
                # Clean up "SKU:" prefix
                cleaned_page_sku = page_sku_text.replace("SKU:", "").strip()
                
                print(f"  Page SKU: '{cleaned_page_sku}' vs Expected: '{sku}'")
                
                # Comparison (Case insensitive?)
                if cleaned_page_sku.lower() != sku.lower():
                    print(f"  SKU MISMATCH! Skipping.")
                    df.at[index, 'Scrape_Status'] = f"Mismatch: Found {cleaned_page_sku}"
                    continue # Skip scraping
                
            except TimeoutException:
                print("  SKU element (.f8pr-codes) not found on product page.")
                df.at[index, 'Scrape_Status'] = "SKU Element Not Found"
                continue

            # 4. Scrape Data
            try:
                # Image (data-fancybox href)
                try:
                    img_element = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-fancybox]"))
                    )
                    df.at[index, 'Scraped_Image_URL'] = img_element.get_attribute("href")
                except TimeoutException:
                    print("  Image not found.")

                # Title (.m5.text-uppercase)
                try:
                    title_element = driver.find_element(By.CSS_SELECTOR, ".m5.text-uppercase")
                    df.at[index, 'Scraped_Title'] = title_element.text.strip()
                except NoSuchElementException:
                    print("  Title not found.")

                # Description (.product-container)
                # First, try to click 'has-link-more' to expand description
                try:
                    # Wait for the page to fully load first (reduced wait time)
                    time.sleep(0.5)
                    
                    # Try to find and click read more buttons using class selector
                    read_more_elements = []
                    try:
                        read_more_elements = WebDriverWait(driver, 3).until(
                            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".has-link-more"))
                        )
                    except TimeoutException:
                        # Alternative: try finding by text content (read more, show more, etc.)
                        print("  No '.has-link-more' elements found, trying alternative selectors...")
                        try:
                            # Try XPath to find buttons/links with "read more" or "show more" text
                            read_more_xpath = "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'read more') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'show more')]"
                            read_more_elements = driver.find_elements(By.XPATH, read_more_xpath)
                            if read_more_elements:
                                print(f"  Found {len(read_more_elements)} 'read more' elements by text")
                        except Exception:
                            pass
                    
                    if read_more_elements:
                        print(f"  Found {len(read_more_elements)} 'read more' button(s)")
                        # Click all visible buttons, refreshing the list after each click
                        max_clicks = len(read_more_elements)
                        for click_attempt in range(max_clicks):
                            try:
                                # Refresh the list of buttons each time (elements can become stale)
                                current_buttons = driver.find_elements(By.CSS_SELECTOR, ".has-link-more")
                                if not current_buttons:
                                    # Also try XPath as fallback
                                    try:
                                        read_more_xpath = "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'read more') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'show more')]"
                                        current_buttons = driver.find_elements(By.XPATH, read_more_xpath)
                                    except:
                                        pass
                                
                                if not current_buttons:
                                    print("  No more 'read more' buttons found")
                                    break
                                
                                # Find the first visible button
                                button_to_click = None
                                for btn in current_buttons:
                                    try:
                                        if btn.is_displayed():
                                            button_to_click = btn
                                            break
                                    except:
                                        continue
                                
                                if not button_to_click:
                                    print("  No visible 'read more' buttons remaining")
                                    break
                                
                                # Scroll element into view (instant scroll for headless)
                                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button_to_click)
                                time.sleep(0.2)
                                
                                # Try regular click first
                                try:
                                    button_to_click.click()
                                    print(f"  Clicked 'read more' button {click_attempt + 1} (regular click)")
                                except Exception as click_error:
                                    # Fallback to JavaScript click
                                    print(f"  Regular click failed, trying JavaScript click...")
                                    driver.execute_script("arguments[0].click();", button_to_click)
                                    print(f"  Clicked 'read more' button {click_attempt + 1} (JavaScript click)")
                                
                                # Wait for content to expand (reduced wait time)
                                time.sleep(1)
                                
                            except Exception as e:
                                print(f"  Error clicking read more button: {e}")
                                break
                        
                        # Final wait after all clicks (reduced)
                        time.sleep(0.5)
                        print("  Finished expanding description sections")
                    else:
                        print("  No 'read more' buttons found (description may already be expanded)")
                except TimeoutException:
                    print("  No 'read more' buttons found on page")
                except Exception as e:
                    print(f"  Error finding/clicking read more: {e}")

                try:
                    # Wait for content to be fully loaded after expansion (reduced wait)
                    time.sleep(0.5)
                    
                    found_desc = False
                    description_text = ""
                    
                    # Strategy 1: Try the specific m6lm class (user-specified selector)
                    try:
                        m6lm_element = driver.find_element(By.CSS_SELECTOR, ".m6lm.m6lm-initialized.high")
                        # Get text using JavaScript to capture all content including hidden
                        text_content = driver.execute_script("""
                            var el = arguments[0];
                            var text = el.innerText || el.textContent || '';
                            return text.trim();
                        """, m6lm_element)
                        
                        if text_content and len(text_content) > 20:
                            description_text = text_content
                            found_desc = True
                            print(f"  Found description in .m6lm.m6lm-initialized.high ({len(description_text)} characters)")
                    except NoSuchElementException:
                        # Try just .m6lm as fallback
                        try:
                            m6lm_element = driver.find_element(By.CSS_SELECTOR, ".m6lm")
                            text_content = driver.execute_script("""
                                var el = arguments[0];
                                var text = el.innerText || el.textContent || '';
                                return text.trim();
                            """, m6lm_element)
                            
                            if text_content and len(text_content) > 20:
                                description_text = text_content
                                found_desc = True
                                print(f"  Found description in .m6lm ({len(text_content)} characters)")
                        except:
                            pass
                    except Exception as e:
                        print(f"  .m6lm approach failed: {e}")
                    
                    # Strategy 2: Try .product-container and get all text including hidden content
                    try:
                        container = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".product-container"))
                        )
                        # Get innerHTML to capture all content including hidden/collapsed sections
                        html_content = container.get_attribute("innerHTML")
                        if html_content:
                            # Extract text from HTML using JavaScript (more reliable)
                            text_from_html = driver.execute_script("""
                                var container = arguments[0];
                                var text = container.innerText || container.textContent || '';
                                return text.trim();
                            """, container)
                            
                            if text_from_html and len(text_from_html) > 20:
                                description_text = text_from_html
                                found_desc = True
                                print(f"  Found description in .product-container ({len(description_text)} characters)")
                        else:
                            # Fallback to .text property
                            text_content = container.text.strip()
                            if text_content and len(text_content) > 20:
                                description_text = text_content
                                found_desc = True
                                print(f"  Found description in .product-container (text property) ({len(description_text)} characters)")
                    except Exception as e:
                        print(f"  .product-container approach failed: {e}")
                    
                    # Strategy 3: Try nested selectors within .product-container
                    if not found_desc:
                        nested_selectors = [
                            ".product-container .rte",
                            ".product-container .rte-content",
                            ".product-container [class*='content']",
                            ".product-container [class*='description']",
                            ".product-container div.rte",
                            ".product-container > div",
                            ".product-container p",
                        ]
                        
                        for sel in nested_selectors:
                            try:
                                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                                for el in elements:
                                    try:
                                        # Get text using JavaScript to capture all content
                                        text_content = driver.execute_script("""
                                            var el = arguments[0];
                                            var text = el.innerText || el.textContent || '';
                                            return text.trim();
                                        """, el)
                                        
                                        if text_content and len(text_content) > 20:
                                            description_text = text_content
                                            found_desc = True
                                            print(f"  Found description with nested selector: {sel} ({len(text_content)} characters)")
                                            break
                                    except:
                                        continue
                                if found_desc:
                                    break
                            except:
                                continue
                    
                    # Strategy 4: Try other common description selectors
                    if not found_desc:
                        fallback_selectors = [
                            ".m6lm",  # Also try just .m6lm in case classes vary
                            "#description", 
                            ".description", 
                            "[itemprop='description']", 
                            "div.rte", 
                            ".product-description",
                            ".product-details",
                            ".product-info",
                            ".product-content",
                            "[class*='product-description']",
                            "[class*='product-content']"
                        ]
                        
                        for sel in fallback_selectors:
                            try:
                                el = driver.find_element(By.CSS_SELECTOR, sel)
                                # Use JavaScript to get all text including hidden
                                text_content = driver.execute_script("""
                                    var el = arguments[0];
                                    var text = el.innerText || el.textContent || '';
                                    return text.trim();
                                """, el)
                                
                                if text_content and len(text_content) > 20:
                                    description_text = text_content
                                    found_desc = True
                                    print(f"  Found description with fallback: {sel} ({len(text_content)} characters)")
                                    break
                            except NoSuchElementException:
                                continue
                            except Exception as e:
                                continue
                    
                    # Strategy 5: Try to find all paragraphs/text blocks in the product area
                    if not found_desc:
                        try:
                            # Look for main product section
                            product_sections = driver.find_elements(By.CSS_SELECTOR, "main, [class*='product'], [id*='product']")
                            all_text_parts = []
                            
                            for section in product_sections[:3]:  # Check first 3 sections
                                try:
                                    paragraphs = section.find_elements(By.TAG_NAME, "p")
                                    for p in paragraphs:
                                        p_text = p.text.strip()
                                        if len(p_text) > 30 and "read more" not in p_text.lower() and "show more" not in p_text.lower():
                                            all_text_parts.append(p_text)
                                except:
                                    continue
                            
                            if all_text_parts:
                                description_text = "\n\n".join(all_text_parts[:5])  # Take first 5 meaningful paragraphs
                                found_desc = True
                                print(f"  Found description from paragraphs ({len(description_text)} characters)")
                        except Exception as e:
                            pass
                    
                    # Save the description if found
                    if found_desc and description_text:
                        df.at[index, 'Scraped_Description'] = description_text
                    else:
                        print("  Description not found with any method.")
                        
                except Exception as desc_error:
                    print(f"  Error extracting description: {desc_error}")
                    import traceback
                    traceback.print_exc()

                print("  Scraped successfully.")
                df.at[index, 'Scrape_Status'] = "Success"

            except Exception as e:
                print(f"  Error scraping data: {e}")
                df.at[index, 'Scrape_Status'] = f"Scraping Error: {e}"

            # Save periodically
            if index % 5 == 0:
                try:
                    df.to_excel(output_file, index=False)
                except PermissionError:
                    print("  Warning: Could not save (file may be open). Will retry at end.")
                except Exception as e:
                    print(f"  Warning: Save error: {e}")

    except KeyboardInterrupt:
        print("\nScraping interrupted by user.")
    finally:
        # Final save
        try:
            df.to_excel(output_file, index=False)
            print(f"Saved to '{output_file}'.")
        except PermissionError:
            print(f"Error: Could not save '{output_file}' - file may be open in Excel. Please close it and run again.")
        except Exception as e:
            print(f"Error saving file: {e}")
        # Keep browser open for a moment or close it? usually close.
        driver.quit()

if __name__ == "__main__":
    # Resume from copy file, skip completed products, save to new file
    scrape_toolmart(
        input_file='data_updated - Copy.xlsx',
        output_file='data_updatedV2.xlsx',
        skip_completed=True
    )
