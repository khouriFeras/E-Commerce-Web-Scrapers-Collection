# Internal Development Guide

This document provides guidelines for internal development and maintenance of the scrapers collection.

## 🏗️ Development Environment

### Setup for New Team Members

1. **Clone the private repository**
2. **Set up Python environment**:
   ```bash
   python -m venv scraper_env
   scraper_env\Scripts\activate  # Windows
   # or source scraper_env/bin/activate  # macOS/Linux
   pip install -r requirements.txt
   playwright install
   ```

3. **Verify installation**:
   ```bash
   python -c "import arabiMart; print('Setup complete!')"
   ```

## 📋 Scraper Development Standards

### Code Structure

Each scraper should follow this pattern:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import pandas as pd
from selenium import webdriver
# ... other imports

def main():
    parser = argparse.ArgumentParser(description="Scraper description")
    # ... argument definitions
    args = parser.parse_args()
    
    # Main scraper logic
    pass

if __name__ == "__main__":
    main()
```

### Required Features

- **Error Handling**: Comprehensive try-catch blocks
- **Logging**: Use Python logging module
- **Progress Tracking**: Show progress with tqdm
- **Checkpoint Saving**: Save progress periodically
- **Rate Limiting**: Respectful delays between requests
- **Headless/Headful Modes**: Support both modes

### Output Format

All scrapers must output data in this format:

| Column | Type | Description |
|--------|------|-------------|
| SKU | String | Original product identifier |
| Description | String | Product description (HTML or text) |
| Image Src | String | Semicolon-separated image URLs |
| Source_URL | String | Product page URL |
| Found | String | YES/NO indicating success |
| Status | String | Additional status information |

## 🧪 Testing Guidelines

### Before Deployment

1. **Test with sample data** (5-10 items)
2. **Test error scenarios**:
   - Invalid SKUs
   - Network timeouts
   - Missing products
3. **Test both modes**:
   - Headless mode
   - Headful mode
4. **Verify output format** consistency

### Sample Test Data

Use the files in `examples/` folder:
- `sample_skus.xlsx` - Sample SKU data
- `sample_urls.txt` - Sample URLs for testing

## 🔧 Maintenance

### Regular Tasks

1. **Update dependencies** monthly
2. **Test all scrapers** after dependency updates
3. **Monitor for site changes** that break scrapers
4. **Update documentation** when adding features

### Monitoring

- Check scraper success rates
- Monitor for new anti-bot measures
- Update selectors when sites change
- Test with different data sets

## 📊 Performance Guidelines

### Optimization

- Use appropriate delays (1-2 seconds between requests)
- Implement retry mechanisms for failed requests
- Use headless mode for production runs
- Monitor memory usage for large datasets

### Resource Management

- Close browser instances properly
- Handle timeouts gracefully
- Implement checkpoint saving for long runs
- Use appropriate timeouts (15-30 seconds)

## 🚨 Troubleshooting

### Common Issues

1. **Chrome Driver Issues**:
   - Update Chrome browser
   - Check Selenium version compatibility
   - Use webdriver-manager for automatic driver management

2. **Site Changes**:
   - Update CSS selectors
   - Check for new anti-bot measures
   - Test with sample data

3. **Memory Issues**:
   - Process data in smaller batches
   - Close browser instances between runs
   - Use checkpoint saving

### Debug Mode

Run scrapers with `--headful` flag to see browser behavior:

```bash
python scraper_name.py --in data.xlsx --out results.xlsx --headful
```

## 📝 Documentation Standards

### Code Comments

- Explain complex logic
- Document function purposes
- Include parameter descriptions
- Add usage examples

### README Updates

When adding new scrapers:
1. Update the main README.md
2. Add to the supported websites table
3. Include usage examples
4. Update the project structure

## 🔒 Security Considerations

### Data Handling

- Never commit sensitive data
- Use environment variables for credentials
- Implement proper error handling
- Log activities appropriately

### Legal Compliance

- Respect robots.txt files
- Implement appropriate delays
- Monitor for rate limiting
- Follow website terms of service

## 📞 Support Contacts

- **Technical Issues**: [Team Lead Email]
- **Urgent Problems**: [Emergency Contact]
- **Feature Requests**: [Product Manager Email]

## 🔄 Version Control

### Branch Strategy

- `main` - Production-ready code
- `develop` - Integration branch
- `feature/name` - New features
- `hotfix/name` - Critical fixes

### Commit Messages

Use clear, descriptive commit messages:
```
feat(arabiMart): add support for product variants
fix(ferplast): handle timeout errors gracefully
docs: update installation instructions
```

## 📈 Metrics and Monitoring

### Key Metrics

- Success rate per scraper
- Average processing time
- Error frequency and types
- Data quality metrics

### Reporting

- Weekly status reports
- Monthly performance reviews
- Quarterly feature planning
- Annual maintenance planning
