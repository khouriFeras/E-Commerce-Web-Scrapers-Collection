import requests
from bs4 import BeautifulSoup

url = "https://toolmart.me/en/products/total-impact-socket-adapter-1-2-x-3-4-thia1234"
try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')

    print(f"Checking URL: {url}")
    
    # Check data-fancybox
    fancybox = soup.find(attrs={"data-fancybox": True})
    print(f"data-fancybox found: {fancybox is not None}")
    if fancybox:
        print(f"fancybox tag: {fancybox.name}, href: {fancybox.get('href')}")

    # Check title class="m5 text-uppercase"
    # Note: BeautifulSoup doesn't match class exactly like string, but checks if class list contains these.
    # Instruction says 'class="m5 text-uppercase"'. It might mean class="m5" AND class="text-uppercase"
    title_element = soup.select_one(".m5.text-uppercase")
    print(f"Title (.m5.text-uppercase) found: {title_element is not None}")
    if title_element:
        print(f"Title text: {title_element.get_text(strip=True)}")

    # Check class="has-link-more"
    link_more = soup.select_one(".has-link-more")
    print(f"has-link-more found: {link_more is not None}")
    
    # Check class="product-container"
    prod_container = soup.select_one(".product-container")
    print(f"product-container found: {prod_container is not None}")
    if prod_container:
        print("product-container content preview:", prod_container.get_text(strip=True)[:100])

except Exception as e:
    print(f"Error: {e}")
