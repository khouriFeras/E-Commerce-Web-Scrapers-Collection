# n8n Product Scraper - Complete Guide

## Overview

This n8n-based scraper extracts product information from URLs, focusing on gallery images and HTML descriptions. It's designed to work with Shopify, WooCommerce, and other e-commerce platforms.

## Features

- **URL-first approach**: Takes exact product URLs as input
- **Gallery image extraction**: Focuses on product gallery images only
- **HTML description extraction**: Preserves rich HTML formatting
- **Smart filtering**: Removes UI assets, logos, and non-product images
- **Shopify optimization**: Automatically bumps image sizes for better quality
- **JSON-LD support**: Uses structured data as hints for better accuracy
- **Batch processing**: Handles large datasets with rate limiting

## File Structure

```
├── n8n_product_scraper_workflow.json    # Main n8n workflow
├── n8n_image_processor.py              # Python helper for advanced processing
├── Data/
│   ├── product_urls.xlsx               # Sample input file
│   └── products_out.xlsx               # Output file (generated)
└── docs/
    └── N8N_SCRAPER_GUIDE.md            # This documentation
```

## Prerequisites

### n8n Installation
1. **Windows (Recommended)**:
   ```bash
   npm install -g n8n
   n8n start
   ```

2. **Docker**:
   ```bash
   docker run -it --rm --name n8n -p 5678:5678 -v ~/.n8n:/home/node/.n8n n8nio/n8n
   ```

3. **Access**: Open http://localhost:5678 in your browser

### Python Dependencies
```bash
pip install pandas openpyxl requests beautifulsoup4
```

## Setup Instructions

### 1. Import the Workflow

1. Open n8n in your browser
2. Click "Import from File" or use Ctrl+O
3. Select `n8n_product_scraper_workflow.json`
4. The workflow will be imported with all nodes configured

### 2. Configure File Paths

Update the file paths in the workflow nodes:

**Read Product URLs node**:
- Set `filePath` to your input Excel file location
- Default: `Data/product_urls.xlsx`

**Write Results node**:
- Set `filePath` to your desired output location
- Default: `Data/products_out.xlsx`

### 3. Prepare Input Data

Create an Excel file with a `URL` column containing product URLs:

| URL |
|-----|
| https://example.com/products/item1 |
| https://shop.com/products/item2 |
| https://store.com/products/item3 |

## Workflow Overview

### Node Flow

```
Manual Trigger → Read URLs → Split Batches → HTTP Request
                                                      ↓
Write Results ← Filter Images ← Merge Fields ← Extract Data
```

### Node Details

#### 1. **Manual Trigger**
- Starts the workflow execution
- No configuration needed

#### 2. **Read Product URLs**
- Reads Excel/CSV file with URLs
- **File Path**: Path to input file
- **Sheet Name**: Worksheet name (default: Sheet1)
- **Header Row**: Row containing column names (default: 0)

#### 3. **Split In Batches**
- Processes URLs one at a time
- **Batch Size**: 1 (for rate limiting)
- Prevents overwhelming target websites

#### 4. **Fetch Page HTML**
- Downloads page content via HTTP
- **Timeout**: 30 seconds
- **User-Agent**: Realistic browser string
- Handles redirects and errors gracefully

#### 5. **Extract Description HTML**
- Uses CSS selectors to find product descriptions
- **Selectors** (in priority order):
  - `[itemprop='description']`
  - `.product-single__description`
  - `.product__description`
  - `.ProductMeta__Description`
  - `.product-description`
  - `.woocommerce-product-details__short-description`
  - `.woocommerce-Tabs-panel--description`
  - `#tab-description`
  - `#product-description`
  - `.desc`
  - `.description`

#### 6. **Extract Gallery Images**
- Finds product gallery images only
- **Selectors**:
  - `.product__media-list img`
  - `.product-gallery__main img`
  - `.product-media img`
  - `[data-product-media] img`
  - `.woocommerce-product-gallery__wrapper img`
  - `.woocommerce-product-gallery img`
  - `#product-gallery img`
  - `#gallery img`
  - `.product-images img`
  - `.gallery img`
  - `.thumbnails img`

#### 7. **Extract JSON-LD**
- Extracts structured data for better accuracy
- **Selector**: `script[type='application/ld+json']`

#### 8. **Merge Fields**
- Combines all extracted data
- Handles data from multiple extraction nodes

#### 9. **Filter & Process Images**
- **Image Processing**:
  - Prefers `data-zoom-image`, `data-large_image`, `data-src` over `src`
  - Parses `srcset` to get largest image
  - Makes relative URLs absolute
  - Filters out UI assets (logos, icons, sprites)
  - Removes very small images (< 200px)
  - Skips SVG and GIF files
  - Deduplicates images

- **Shopify Optimization**:
  - Bumps `_300x.` to `_1024x.`
  - Bumps `_800x.` to `_1024x.`
  - Bumps `?width=300` to `?width=2048`
  - Bumps `?width=800` to `?width=2048`

- **JSON-LD Integration**:
  - Uses structured data images as hints
  - Filters gallery images to match JSON-LD when available

#### 10. **Write Results**
- Saves processed data to Excel
- **Output Columns**:
  - `URL`: Original product URL
  - `Body (HTML)`: Rich HTML description
  - `Image Src`: Semicolon-separated image URLs
  - `Image Count`: Number of images found
  - `Status`: Processing status (SUCCESS/NO_IMAGES/ERROR)

## Usage

### Basic Usage

1. **Prepare Input**: Create Excel file with URLs
2. **Configure Paths**: Update file paths in workflow nodes
3. **Run Workflow**: Click "Execute Workflow" button
4. **Check Results**: Output file will be created with processed data

### Advanced Usage

#### Using Python Helper

For more sophisticated image processing:

```bash
# Process existing output file
python n8n_image_processor.py --input Data/products_out.xlsx --output Data/products_enhanced.xlsx

# Process URL string
python n8n_image_processor.py --urls "url1;url2;url3" --base-url "https://example.com"
```

#### Customizing Selectors

Edit the CSS selectors in the HTML Extract nodes to match your target websites:

1. **Description Selectors**: Add site-specific selectors
2. **Gallery Selectors**: Modify to match your site's gallery structure
3. **Test Selectors**: Use browser dev tools to find the right selectors

## Output Format

### Excel Output

| Column | Description | Example |
|--------|-------------|---------|
| URL | Original product URL | https://shop.com/products/item1 |
| Body (HTML) | Rich HTML description | `<p>Product description...</p>` |
| Image Src | Semicolon-separated URLs | `url1;url2;url3` |
| Image Count | Number of images | 5 |
| Status | Processing status | SUCCESS |

### Shopify Import Ready

The output is formatted for direct import into Shopify:
- **Body (HTML)**: Paste into Shopify's "Body (HTML)" field
- **Image Src**: Split by semicolon for multiple image rows

## Troubleshooting

### Common Issues

#### 1. **No Images Found**
- **Cause**: Gallery selectors don't match site structure
- **Solution**: Update gallery selectors in "Extract Gallery Images" node
- **Debug**: Check browser dev tools for actual gallery structure

#### 2. **Empty Descriptions**
- **Cause**: Description selectors don't match site structure
- **Solution**: Update description selectors in "Extract Description HTML" node
- **Debug**: Inspect page source for description containers

#### 3. **HTTP Errors**
- **Cause**: Rate limiting or blocked requests
- **Solution**: Increase delay between requests in "Split In Batches" node
- **Alternative**: Use different User-Agent string

#### 4. **JavaScript-Heavy Sites**
- **Cause**: Content loaded dynamically
- **Solution**: Add Playwright/Browserless node before HTML extraction
- **Implementation**: Replace HTTP Request with Playwright node

### Debug Mode

Enable debug logging:
1. Go to workflow settings
2. Enable "Save execution progress"
3. Check execution logs for detailed error information

## Performance Optimization

### Rate Limiting
- **Current**: 1 request per batch (1 req/s)
- **Safe Range**: 1-2 requests per second
- **Adjust**: Modify batch size in "Split In Batches" node

### Memory Management
- **Large Files**: Process in smaller chunks
- **Batch Size**: Keep at 1 for stability
- **Timeout**: Increase for slow sites (30-60 seconds)

### Error Handling
- **Failed URLs**: Continue processing other URLs
- **Empty Results**: Log and continue
- **Timeouts**: Retry with longer timeout

## Extensions

### Adding New Platforms

1. **Create Platform Profile**:
   ```json
   {
     "platform": "custom-shop",
     "description_selectors": [".custom-desc", ".product-info"],
     "gallery_selectors": [".custom-gallery img", ".product-photos img"]
   }
   ```

2. **Update Workflow**:
   - Add conditional logic based on URL domain
   - Use different selectors per platform

### Browser Automation

For JavaScript-heavy sites:

1. **Add Playwright Node**:
   - Replace HTTP Request with Playwright
   - Configure browser options
   - Add wait conditions for dynamic content

2. **Configuration**:
   ```json
   {
     "url": "={{ $json.URL }}",
     "waitFor": ".product-gallery",
     "timeout": 30000
   }
   ```

### Cloud Storage

Add cloud storage integration:

1. **Google Drive**:
   - Add Google Drive node after "Write Results"
   - Upload output file automatically

2. **AWS S3**:
   - Add S3 node for file storage
   - Configure bucket and permissions

## Security & Ethics

### Best Practices
- **Respect robots.txt**: Check before scraping
- **Rate Limiting**: Don't overwhelm servers
- **User-Agent**: Use realistic browser strings
- **Terms of Service**: Review and comply with site policies

### Legal Considerations
- **Public Data Only**: Scrape only publicly available information
- **No Authentication**: Don't bypass login requirements
- **Attribution**: Credit sources when required
- **Commercial Use**: Check licensing requirements

## Support

### Getting Help
1. **n8n Documentation**: https://docs.n8n.io/
2. **Community Forum**: https://community.n8n.io/
3. **GitHub Issues**: Report bugs and feature requests

### Contributing
1. Fork the repository
2. Create feature branch
3. Submit pull request with improvements

## Changelog

### Version 1.0.0
- Initial release
- Basic URL-based scraping
- Gallery image extraction
- HTML description extraction
- Shopify optimization
- JSON-LD support
- Python helper script

---

**Note**: This scraper is designed for legitimate business purposes. Always respect website terms of service and implement appropriate rate limiting.

