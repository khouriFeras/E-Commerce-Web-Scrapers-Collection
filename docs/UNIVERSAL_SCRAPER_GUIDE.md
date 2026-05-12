# Universal Search-Based Product Scraper Guide

## 🌐 Overview

The **Universal Search-Based Product Scraper** is designed to work with **any e-commerce website** without prior knowledge of the site's structure. It automatically adapts to different platforms, search patterns, and product layouts.

## 🎯 Key Features

### ✅ **Universal Compatibility**
- Works with **any e-commerce website**
- No need to know the site's structure beforehand
- Automatically detects search patterns and product layouts
- Adapts to different platforms (Shopify, WooCommerce, custom sites, etc.)

### ✅ **Smart Search Detection**
- Tries **25+ different search URL patterns**
- Analyzes HTML forms to find correct search endpoints
- Detects search parameter names automatically
- Falls back to common patterns if analysis fails

### ✅ **Intelligent Product Detection**
- Uses **15+ product link patterns** to find products
- Scores results based on SKU match and URL structure
- Filters out non-product links (search, category, login, etc.)
- Prioritizes most relevant results

### ✅ **Comprehensive Data Extraction**
- **50+ description selectors** for maximum compatibility
- **30+ image selectors** covering all major platforms
- Smart filtering to remove UI assets and logos
- Preserves HTML formatting for Shopify compatibility

## 🔧 How It Works

### 1. **Universal Search URL Generation**
The scraper tries multiple search patterns automatically:

```javascript
// Standard search patterns
/search?q=SKU
/search?keyword=SKU
/search?query=SKU
/search?term=SKU
/search?type=product&q=SKU

// Product-specific patterns
/products?search=SKU
/products?q=SKU
/shop?search=SKU
/shop?q=SKU

// Platform-specific patterns
/catalogsearch/result/?q=SKU  // Magento
/search/?q=SKU                // Generic
/all?search=SKU              // Category-based
/catalog?search=SKU          // Catalog-based

// API-style patterns
/api/search?q=SKU
/api/products?search=SKU
/api/shop?q=SKU
```

### 2. **Smart Form Analysis**
If the homepage contains search forms, the scraper:
- Analyzes form action URLs
- Detects search parameter names
- Constructs the correct search URL
- Falls back to common patterns if needed

### 3. **Universal Product Detection**
The scraper looks for product links using multiple patterns:

```javascript
// Standard e-commerce patterns
/products/...
/product/...
/items/...
/item/...
/shop/...
/catalog/...
/p/...

// Platform-specific patterns
/collections/...        // Shopify
/collections/.../products/...  // Shopify
/store/...              // Generic
/buy/...                // Generic
/pdp/...                // Generic PDP

// ID-based patterns
/id/...
/pid/...
/sku/...

// Generic patterns
/[a-zA-Z0-9-]+/[a-zA-Z0-9-]+
/[a-zA-Z0-9-]+/[a-zA-Z0-9-]+/[a-zA-Z0-9-]+
```

### 4. **Intelligent Scoring System**
Products are scored based on:
- **SKU in URL** (20 points)
- **SKU in surrounding text** (10 points)
- **Product-like URL structure** (5 points)
- **URL depth** (2 points)
- **Fallback score** (1 point)

### 5. **Comprehensive Data Extraction**
The scraper uses extensive selector lists:

#### Description Selectors (50+ patterns)
```javascript
// Standard e-commerce
[itemprop="description"]
.product-description
.product-info
.product-details
.product-summary
.product-content
.product-text
.description
.item-description
.item-info
.item-details

// Platform-specific
.product-single__description    // Shopify
.ProductMeta__Description      // Shopify
.woocommerce-product-details__short-description  // WooCommerce
.woocommerce-Tabs-panel--description  // WooCommerce
#tab-description               // WooCommerce
#product-description           // Generic

// Generic patterns
.desc
.detail
.info
.content
.text
.summary

// Structural patterns
h1 + p
h1 + div
.product-title + p
.product-title + div
.item-title + p
.item-title + div

// Class-based patterns
[class*="description"]
[class*="info"]
[class*="details"]
[class*="content"]
[class*="text"]
[class*="summary"]

// ID-based patterns
#description
#info
#details
#content
#text
#summary
```

#### Image Selectors (30+ patterns)
```javascript
// Standard e-commerce
.product-images img
.product-gallery img
.product-photos img
.gallery img
.images img
.photos img
.thumbnails img
.preview img
.main-image img
.featured-image img

// Platform-specific
.product__media-list img        // Shopify
.product-gallery__main img      // Shopify
.product-media img              // Shopify
[data-product-media] img        // Shopify
.woocommerce-product-gallery__wrapper img  // WooCommerce
.woocommerce-product-gallery img  // WooCommerce
#product-gallery img            // Generic
#gallery img                    // Generic

// Generic patterns
.item-images img
.item-gallery img
.item-photos img
.product-slider img
.carousel img
.swiper img
.slider img

// Attribute-based patterns
img[src*="product"]
img[src*="item"]
img[src*="gallery"]
img[src*="photo"]
img[src*="image"]
img[data-src*="product"]
img[data-src*="item"]

// Class-based patterns
[class*="gallery"] img
[class*="image"] img
[class*="photo"] img
[class*="thumbnail"] img
[class*="preview"] img
[class*="main"] img
[class*="featured"] img
```

## 📁 File Structure

```
├── n8n_universal_search_scraper.json    # Universal workflow
├── test_universal_scraper.py            # Test script
├── Data/
│   ├── universal_test_data.xlsx         # Test data
│   └── universal_results.xlsx           # Output results
└── docs/
    └── UNIVERSAL_SCRAPER_GUIDE.md       # This guide
```

## 🚀 Quick Start

### 1. **Setup**
```bash
# Install n8n
npm install -g n8n

# Install Python dependencies
pip install pandas openpyxl requests beautifulsoup4

# Start n8n
n8n start
```

### 2. **Import Workflow**
1. Open n8n (http://localhost:5678)
2. Click "Import from File" (Ctrl+O)
3. Select `n8n_universal_search_scraper.json`

### 3. **Prepare Data**
Create Excel file with `URL` and `SKU` columns:

| URL | SKU |
|-----|-----|
| https://arabiemart.com | A3035031 |
| https://shop.example.com | PROD123 |
| https://store.example.com | ITEM456 |
| https://marketplace.example.com | GOODS789 |

### 4. **Configure Workflow**
- Update file paths in workflow nodes
- Set input file: `Data/universal_test_data.xlsx`
- Set output file: `Data/universal_results.xlsx`

### 5. **Run Workflow**
1. Click "Execute Workflow"
2. Check the output file for results

## 📊 Input/Output Format

### Input (Excel/CSV)
| URL | SKU |
|-----|-----|
| https://any-shop.com | ANY123 |
| https://another-store.com | PROD456 |

### Output (Excel)
| Column | Description | Example |
|--------|-------------|---------|
| URL | Original site URL | https://any-shop.com |
| SKU | Search term used | ANY123 |
| Body (HTML) | Product description | `<p>Product description...</p>` |
| Image Src | Semicolon-separated image URLs | `img1.jpg;img2.jpg;img3.jpg` |
| Image Count | Number of images found | 3 |
| Product URL | Final product page URL | https://any-shop.com/products/item123 |
| Status | Processing status | SUCCESS |

## 🔍 Supported Platforms

### ✅ **E-commerce Platforms**
- **Shopify** stores
- **WooCommerce** stores
- **Magento** stores
- **BigCommerce** stores
- **PrestaShop** stores
- **OpenCart** stores
- **Custom** e-commerce sites

### ✅ **Search Patterns**
- Standard search (`/search?q=`)
- Keyword search (`/search?keyword=`)
- Query search (`/search?query=`)
- Term search (`/search?term=`)
- Product search (`/products?search=`)
- Shop search (`/shop?search=`)
- Catalog search (`/catalog?search=`)
- API search (`/api/search?`)

### ✅ **Product Link Patterns**
- Product pages (`/products/...`)
- Item pages (`/items/...`)
- Shop pages (`/shop/...`)
- Catalog pages (`/catalog/...`)
- Collection pages (`/collections/...`)
- Store pages (`/store/...`)
- ID-based pages (`/id/...`, `/pid/...`)

## 🧪 Testing

### Run Tests
```bash
# Test universal scraper
python test_universal_scraper.py

# Test specific website
python test_arabiemart_real.py
```

### Test Results
The test script will:
1. ✅ Test multiple websites
2. ✅ Validate workflow JSON
3. ✅ Test search URL generation
4. ✅ Test product detection
5. ✅ Test data extraction
6. ✅ Generate test data

## 🔧 Customization

### Adding New Search Patterns
Edit the "Prepare Universal Search" node:

```javascript
const searchPatterns = [
  // Add your custom patterns here
  `${siteUrl}/your-custom-search?param=${encodeURIComponent(sku)}`,
  `${siteUrl}/api/v1/search?query=${encodeURIComponent(sku)}`,
  `${siteUrl}/find?term=${encodeURIComponent(sku)}`
];
```

### Adding New Product Patterns
Edit the "Find First Product" node:

```javascript
const productLinkPatterns = [
  // Add your custom patterns here
  /href="([^"]*\/your-pattern\/[^"]*)"/gi,
  /href="([^"]*\/custom\/[^"]*)"/gi
];
```

### Adding New Selectors
Edit the "Extract Universal Product Data" node:

```javascript
// Add description selectors
const descSelectors = [
  // Add your custom selectors here
  '.your-custom-description',
  '.your-custom-info',
  '#your-custom-id'
];

// Add image selectors
const imgSelectors = [
  // Add your custom selectors here
  '.your-custom-gallery img',
  '.your-custom-images img',
  'img[src*="your-pattern"]'
];
```

## 🚨 Troubleshooting

### Common Issues

#### 1. **No Search Results Found**
- **Cause**: Search URL pattern doesn't match the site
- **Solution**: Add custom search pattern for your site
- **Debug**: Check the site's search functionality manually

#### 2. **No Products Found**
- **Cause**: Product link pattern doesn't match the site
- **Solution**: Add custom product link pattern
- **Debug**: Inspect search results page HTML

#### 3. **No Images Found**
- **Cause**: Image selectors don't match site structure
- **Solution**: Add custom image selectors
- **Debug**: Use browser dev tools to find correct selectors

#### 4. **Empty Descriptions**
- **Cause**: Description selectors don't match site structure
- **Solution**: Add custom description selectors
- **Debug**: Inspect product page HTML

### Debug Mode
1. Enable "Save execution progress" in workflow settings
2. Check execution logs for detailed information
3. Use browser dev tools to verify selectors

## 📈 Performance Tips

### Optimization
- **Batch size**: Keep at 1 for stability
- **Timeout**: Increase for slow sites (30-60 seconds)
- **Memory**: Process large datasets in smaller chunks
- **Rate limiting**: Be conservative (1-2 req/s)

### Scaling
- **Large datasets**: Use batch processing
- **Multiple sites**: Create separate workflows
- **Cloud deployment**: Deploy n8n on cloud for better performance

## 🔒 Security & Ethics

### Best Practices
- **Respect robots.txt**: Check before scraping
- **Rate limiting**: Don't overwhelm servers
- **User-Agent**: Use realistic browser strings
- **Terms of Service**: Review and comply with site policies

### Legal Considerations
- **Public data only**: Scrape only publicly available information
- **No authentication**: Don't bypass login requirements
- **Attribution**: Credit sources when required
- **Commercial use**: Check licensing requirements

## 🎯 Use Cases

### 1. **Multi-Platform Scraping**
- Scrape products from multiple different e-commerce sites
- Compare prices across different platforms
- Aggregate product data from various sources

### 2. **Unknown Site Exploration**
- Test new e-commerce sites without prior knowledge
- Explore competitor websites
- Research new marketplaces

### 3. **Bulk Data Collection**
- Collect product data from hundreds of different sites
- Build comprehensive product databases
- Monitor product availability across platforms

### 4. **Market Research**
- Analyze product offerings across different platforms
- Track product trends and availability
- Research competitor strategies

## 🎉 Conclusion

The Universal Search-Based Product Scraper is designed to work with **any e-commerce website** without prior knowledge. It automatically adapts to different platforms, search patterns, and product layouts, making it perfect for:

- ✅ **Multi-platform scraping**
- ✅ **Unknown site exploration**
- ✅ **Bulk data collection**
- ✅ **Market research**

The scraper uses extensive pattern matching, intelligent scoring, and comprehensive selector lists to maximize compatibility with any e-commerce platform.

---

**Ready to scrape any website?** Import the universal workflow and add your site URLs and SKUs! 🚀





