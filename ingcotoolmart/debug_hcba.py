from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

def debug_hcba():
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    driver = webdriver.Chrome(options=opts)
    try:
        url = "https://toolmart.me/en/products/ingco-hcba32203"
        print(f"Checking {url}")
        driver.get(url)
        time.sleep(3)

        # 1. Check for .product-container
        try:
            desc = driver.find_element(By.CSS_SELECTOR, ".product-container")
            print(f".product-container found. Visible: {desc.is_displayed()}")
            print(f"Content length: {len(desc.text)}")
            print(f"Content: {desc.text[:50]}...")
        except:
            print(".product-container NOT found/visible initially.")

        # 2. Check for read more
        try:
            read_more = driver.find_elements(By.CSS_SELECTOR, ".has-link-more")
            print(f"Found {len(read_more)} .has-link-more elements")
            for rm in read_more:
                print(f" - Visible: {rm.is_displayed()}")
        except:
            print("Error checking read more")

    finally:
        driver.quit()

if __name__ == "__main__":
    debug_hcba()
