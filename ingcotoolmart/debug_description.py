from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

def build_driver():
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1440,1200")
    driver = webdriver.Chrome(options=opts)
    return driver

def debug_description():
    driver = build_driver()
    try:
        # Use a product URL that we know exists
        url = "https://toolmart.me/en/products/total-impact-socket-adapter-1-2-x-3-4-thia1234" 
        print(f"Navigating to {url}")
        driver.get(url)

        # 1. Check .product-container
        try:
            desc = driver.find_element(By.CSS_SELECTOR, ".product-container")
            print(f"Found .product-container. Text length: {len(desc.text)}")
            print(f"Text preview: {desc.text[:100]}...")
            print(f"Is displayed: {desc.is_displayed()}")
        except Exception as e:
            print(f"Could not find .product-container: {e}")

        # 2. Check for 'has-link-more'
        try:
            read_more = driver.find_elements(By.CSS_SELECTOR, ".has-link-more")
            if read_more:
                print(f"Found {len(read_more)} 'has-link-more' elements.")
                for rm in read_more:
                    print(f" - Tag: {rm.tag_name}, Text: {rm.text}, Visible: {rm.is_displayed()}")
            else:
                print("No 'has-link-more' elements found.")
        except Exception as e:
             print(f"Error checking has-link-more: {e}")

        # 3. Dump page source if needed (or just list generic content divs)
        # generic_divs = driver.find_elements(By.CSS_SELECTOR, "div[class*='description'], div[class*='content']")
        # for div in generic_divs:
        #     print(f"Potential container: {div.get_attribute('class')}")

    finally:
        driver.quit()

if __name__ == "__main__":
    debug_description()
