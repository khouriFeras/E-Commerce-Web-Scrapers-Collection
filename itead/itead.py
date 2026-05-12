import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import os

def run_scraper():
    # File paths
    input_file = r'd:\JafarShop\Scrapers\itead\TMAIN.xlsx'
    output_file = r'd:\JafarShop\Scrapers\itead\TMAIN_updated.xlsx'
    
    print(f"Reading input file: {input_file}")
    
    # Read Excel - Sheet SONOFF
    try:
        df = pd.read_excel(input_file, sheet_name='SONOFF')
        print(f"Columns: {df.columns.tolist()}")
        if 'SKU' not in df.columns:
            print("Error: 'SKU' column not found in 'SONOFF' sheet.")
            # Fallback to column 0 if needed or handle error
            # return
    except Exception as e:
        print(f"Error reading Excel: {e}")
        return

    # Process ALL items
    sku_list = df['SKU'].tolist()
    print(f"Processing {len(sku_list)} SKUs")

    results = []

    # Setup Selenium
    options = webdriver.ChromeOptions()
    options.add_argument('--start-maximized')
    options.add_argument('--headless') # Keep visible for debugging as requested
    driver = webdriver.Chrome(options=options)

    for sku in sku_list:
        if pd.isna(sku):
            continue
            
        print(f"Processing SKU: {sku}")
        search_url = f"https://itead.cc/?s={sku}&post_type=product&type_aws=true"
        
        item_data = {
            'SKU': sku,
            'Title': '',
            'Images': '',
            'Description': '',
            'Product_URL': ''
        }

        try:
            driver.get(search_url)
            # time.sleep(2) # Wait for load
            
            # Click first result
            # Assuming standard WooCommerce structure for search results. 
            # The URL usually redirects directly if one match, or shows list.
            # We'll try to find a product link if we are not already on a product page.
            
            # Check if we are on a search result page or product page
            # If search result page, click first product.
            # Look for common product link selectors in standard themes or specific to itead.
            # Generic search result item selector
            try:
                # Wait for either a product title on a product page OR a search result link
                WebDriverWait(driver, 10).until(
                    lambda d: d.find_elements(By.CLASS_NAME, "et_pb_wc_title") or d.find_elements(By.CSS_SELECTOR, ".product-title a") or d.find_elements(By.CSS_SELECTOR, "h2.entry-title a")
                )
                
                # If we see search results list
                search_results = driver.find_elements(By.CSS_SELECTOR, ".product a") # generic WC
                if not search_results:
                     search_results = driver.find_elements(By.CSS_SELECTOR, "h2.entry-title a") # another common one

                # If current URL is still a search query (?) and we have results, click first
                if "?s=" in driver.current_url and search_results:
                    print("Clicking first search result...")
                    search_results[0].click()
                    time.sleep(3) # Wait for navigation
                
                item_data['Product_URL'] = driver.current_url
                
                # Extract Data
                
                # Title
                try:
                    title_el = driver.find_element(By.CLASS_NAME, "et_pb_wc_title")
                    item_data['Title'] = title_el.text.strip()
                except:
                    print(f"Title not found for {sku}")

                # Images (class="slick-track")
                try:
                    import re
                    # Find slick-track container first
                    slick_track = driver.find_element(By.CLASS_NAME, "slick-track")
                    images = slick_track.find_elements(By.TAG_NAME, "img")
                    img_urls = []
                    
                    for img in images:
                        # Try to get the largest image directly
                        src = img.get_attribute('data-large_image')
                        if not src:
                            src = img.get_attribute('data-src')
                        if not src:
                            src = img.get_attribute('src')
                            
                        if src:
                            # Clean URL - remove dimensions like -100x100.jpg
                            # Regex matches -digitsxdigits.extension at the end
                            clean_src = re.sub(r'-\d+x\d+(\.[a-zA-Z]+)$', r'\1', src)
                            
                            if clean_src not in img_urls:
                                img_urls.append(clean_src)
                                
                    item_data['Images'] = " ; ".join(img_urls)
                except:
                    print(f"Images not found for {sku}")

                # Description Parts
                full_description = []
                
                # 1. class="et_pb_module et_pb_wc_description..."
                try:
                    # Specific selector requested by user
                    desc_selector = ".et_pb_module.et_pb_wc_description.et_pb_wc_description_0_tb_body.et_pb_bg_layout_light.et_pb_text_align_left"
                    modules = driver.find_elements(By.CSS_SELECTOR, desc_selector)
                    for m in modules:
                        txt = m.text.strip()
                        if txt: 
                            full_description.append(txt)
                except:
                    pass

                # 2. class="et_pb_tab_content" (Initial)
                try:
                    tabs = driver.find_elements(By.CLASS_NAME, "et_pb_tab_content")
                    for t in tabs:
                        txt = t.text.strip()
                        if txt: 
                            full_description.append(txt)
                except:
                    pass
                
                # 3. Click href="#tab-additional_information", wait, extract class="et_pb_tab_content"
                try:
                    tab_link = driver.find_element(By.CSS_SELECTOR, 'a[href="#tab-additional_information"]')
                    # Ensure clickable
                    driver.execute_script("arguments[0].scrollIntoView(true);", tab_link)
                    driver.execute_script("arguments[0].click();", tab_link)
                    
                    time.sleep(0.5) # Wait half a second as requested
                    
                    tabs_after = driver.find_elements(By.CLASS_NAME, "et_pb_tab_content")
                    for t in tabs_after:
                        txt = t.text.strip()
                        if txt: 
                            full_description.append(txt)
                except Exception as e:
                    print(f"Could not click Additional Info tab: {e}")

                item_data['Description'] = "\n\n".join([d for d in full_description if d])

            except Exception as e:
                print(f"Failed to find product or elements for {sku}: {e}")
                
        except Exception as e:
            print(f"Error processing {sku}: {e}")

        results.append(item_data)
        time.sleep(1) # polite delay

    driver.quit()

    # Save - Merge with original
    print("Merging results with original data...")
    results_df = pd.DataFrame(results)
    
    # Merge on SKU
    # Note: 'item_data' has the same columns as keys.
    # We want to add Title, Images, Description, Product_URL to original df
    # If original df has these columns, we update them? Or just suffix?
    # Assuming original doesn't have them or we want the new ones.
    # We will use suffix for collisions if any, or just overwrite/add.
    # Simple merge:
    final_df = pd.merge(df, results_df, on='SKU', how='left')
    
    final_df.to_excel(output_file, index=False)
    print(f"Saved merged results to {output_file}")

if __name__ == "__main__":
    run_scraper()
