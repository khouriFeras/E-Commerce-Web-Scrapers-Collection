# Search-Based Product Scraper Guide

## Overview

This n8n workflow implements the search-based scraping approach you described:

1. **Input**: Site URL + SKU
2. **Process**: Search for SKU on the site
3. **Navigate**: Click first search result
4. **Extract**: Get product images and description
5. **Output**: Save to Excel

## Workflow Flow

```
Manual Trigger → Read Search Data → Process One by One → Load Homepage
                                                                    ↓
Save Results ← Extract Product Data ← Get Product Page ← Find First Product ← Search for SKU
```

## Input Format

Create an Excel file with two columns:

| URL | SKU |
|-----|-----|
| https://doublem-jo.com | ABC123 |
| https://arabiemart.com | XYZ789 |
| https://example-shop.com | TEST456 |

## How It Works

### 1. **Load Homepage**
- Fetches the main site URL
- Analyzes the page to find search functionality

### 2. **Prepare Search URL**
- Tries multiple common search URL patterns:
  - `/search?q=SKU`
  - `/search?type=product&q=SKU`
  - `/products?search=SKU`
  - `/shop?search=SKU`
  - `/catalogsearch/result/?q=SKU`
- Analyzes HTML forms to find the correct search endpoint

### 3. **Search for SKU**
- Executes the search request
- Gets search results page

### 4. **Find First Product**
- Looks for product links in search results
- Common patterns:
  - `/products/...`
  - `/product/...`
  - `/items/...`
  - `/shop/...`
  - `/catalog/...`
- Scores results based on SKU match in URL or surrounding text
- Selects the best match (usually first result)

### 5. **Get Product Page**
- Fetches the selected product page
- Gets full HTML content

### 6. **Extract Product Data**
- **Description extraction** using multiple selectors:
  - `[itemprop='description']`
  - `.product-single__description`
  - `.product__description`
  - `.ProductMeta__Description`
  - `.product-description`
  - `.woocommerce-product-details__short-description`
  - And many more...

- **Image extraction** using gallery selectors:
  - `.product__media-list img`
  - `.product-gallery__main img`
  - `.product-media img`
  - `[data-product-media] img`
  - `.woocommerce-product-gallery__wrapper img`
  - And many more...

- **Smart filtering**:
  - Removes logos, icons, payment badges
  - Skips SVG and GIF files
  - Deduplicates images
  - Shopify size bumping (`_300x.` → `_1024x.`)

## Output Format

| Column | Description | Example |
|--------|-------------|---------|
| URL | Original site URL | https://doublem-jo.com |
| SKU | Search term used | ABC123 |
| Body (HTML) | Product description | `<p>Product description...</p>` |
| Image Src | Semicolon-separated image URLs | `img1.jpg;img2.jpg;img3.jpg` |
| Image Count | Number of images found | 3 |
| Product URL | Final product page URL | https://doublem-jo.com/products/item123 |
| Status | Processing status | SUCCESS |

## Setup Instructions

### 1. Import Workflow
1. Open n8n (http://localhost:5678)
2. Click "Import from File" (Ctrl+O)
3. Select `n8n_search_scraper_workflow.json`

### 2. Prepare Input Data
1. Create Excel file: `Data/search_products.xlsx`
2. Add columns: `URL` and `SKU`
3. Add your site URLs and SKUs to search

### 3. Configure File Paths
- **Read Search Data node**: Update to your input file path
- **Save Results node**: Update to your desired output path

### 4. Run Workflow
1. Click "Execute Workflow"
2. Check the output file for results

## Customization

### Adding New Search Patterns

Edit the "Prepare Search URL" node to add new search URL patterns:

```javascript
const searchPatterns = [
  `${siteUrl}/search?q=${encodeURIComponent(sku)}`,
  `${siteUrl}/search?type=product&q=${encodeURIComponent(sku)}`,
  `${siteUrl}/products?search=${encodeURIComponent(sku)}`,
  // Add your custom patterns here
  `${siteUrl}/your-custom-search?query=${encodeURIComponent(sku)}`,
];
```

### Adding New Product Link Patterns

Edit the "Find First Product" node to add new product link patterns:

```javascript
const productLinkPatterns = [
  /href="([^"]*\/products\/[^"]*)"/gi,
  /href="([^"]*\/product\/[^"]*)"/gi,
  // Add your custom patterns here
  /href="([^"]*\/your-pattern\/[^"]*)"/gi,
];
```

### Customizing Selectors

Edit the "Extract Product Data" node to add new selectors:

```javascript
// Description selectors
const descSelectors = [
  '[itemprop="description"]',
  '.product-single__description',
  // Add your custom selectors here
  '.your-custom-description',
];

// Image selectors
const imgSelectors = [
  '.product__media-list img',
  '.product-gallery__main img',
  // Add your custom selectors here
  '.your-custom-gallery img',
];
```

## Troubleshooting

### Common Issues

#### 1. **No Search Results Found**
- **Cause**: Search URL pattern doesn't match the site
- **Solution**: Add custom search pattern for your site
- **Debug**: Check the site's search functionality manually

#### 2. **No Product Found in Search Results**
- **Cause**: Product link pattern doesn't match the site
- **Solution**: Add custom product link pattern
- **Debug**: Inspect search results page HTML

#### 3. **No Images Found**
- **Cause**: Image selectors don't match site structure
- **Solution**: Update image selectors in "Extract Product Data" node
- **Debug**: Use browser dev tools to find correct selectors

#### 4. **Empty Descriptions**
- **Cause**: Description selectors don't match site structure
- **Solution**: Update description selectors
- **Debug**: Inspect product page HTML

### Debug Mode

1. Enable "Save execution progress" in workflow settings
2. Check execution logs for detailed information
3. Use browser dev tools to verify selectors

## Advanced Features

### Site-Specific Configuration

Create different workflows for different sites:

1. **Shopify stores**: Use Shopify-specific selectors
2. **WooCommerce stores**: Use WooCommerce-specific selectors
3. **Custom platforms**: Create custom selectors

### Browser Automation

For JavaScript-heavy sites, replace HTTP Request nodes with Playwright:

```json
{
  "url": "={{ $json.searchUrl }}",
  "waitFor": ".search-results",
  "timeout": 30000
}
```

### Rate Limiting

Adjust batch size in "Process One by One" node:
- **Conservative**: 1 (1 request per second)
- **Moderate**: 2-3 (if site allows)
- **Aggressive**: 5+ (use with caution)

## Performance Tips

### Optimization
- **Batch size**: Keep at 1 for stability
- **Timeout**: Increase for slow sites (30-60 seconds)
- **Memory**: Process large datasets in smaller chunks

### Scaling
- **Large datasets**: Use batch processing
- **Multiple sites**: Create separate workflows
- **Cloud deployment**: Deploy n8n on cloud for better performance

## Security & Ethics

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

## Examples

### Example 1: Shopify Store
```
URL: https://mystore.myshopify.com
SKU: ABC123
Result: Finds product at https://mystore.myshopify.com/products/abc123
```

### Example 2: WooCommerce Store
```
URL: https://mystore.com
SKU: XYZ789
Result: Finds product at https://mystore.com/product/xyz789
```

### Example 3: Custom E-commerce
```
URL: https://customstore.com
SKU: TEST456
Result: Finds product at https://customstore.com/items/test456
```

## Support

### Getting Help
1. Check the execution logs in n8n
2. Use browser dev tools to debug selectors
3. Test with a single URL/SKU first
4. Check the site's search functionality manually

### Common Solutions
1. **Add custom search patterns** for your site
2. **Update product link patterns** for your site
3. **Customize selectors** for your site's structure
4. **Use browser automation** for JavaScript-heavy sites

---

**Ready to start?** Import the workflow and add your site URLs and SKUs! 🚀

