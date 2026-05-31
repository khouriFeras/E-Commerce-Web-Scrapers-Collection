#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
New Vision All Categories Scraper
Scrapes all product categories from New Vision and combines into one Excel file
"""

import os
import sys
import time
from datetime import datetime
import pandas as pd

# Import the existing scraper functions
from LGScraper import scrape_all_products

def scrape_all_newvision_categories():
    """Scrape all New Vision product categories and combine into one Excel file."""
    
    # List of all category URLs to scrape
    categories = [
        {
            'name': 'Air Conditioners',
            'url': 'https://newvision.jo/product-category/air-conditioners-ar/',
            'output': 'Data/LG/air_conditioners.xlsx'
        },
        {
            'name': 'Dryers',
            'url': 'https://newvision.jo/product-category/dryers-ar/',
            'output': 'Data/LG/dryers.xlsx'
        },
        {
            'name': 'Washing Machines',
            'url': 'https://newvision.jo/product-category/washing-machines-ar/page/2/',
            'output': 'Data/LG/washing_machines.xlsx'
        },
        {
            'name': 'Dishwashers',
            'url': 'https://newvision.jo/product-category/dishwashers-ar/',
            'output': 'Data/LG/dishwashers.xlsx'
        },
        {
            'name': 'Vacuum Cleaners',
            'url': 'https://newvision.jo/product-category/vacuum-cleaner-ar/',
            'output': 'Data/LG/vacuum_cleaners.xlsx'
        },
        {
            'name': 'Microwaves',
            'url': 'https://newvision.jo/product-category/microwaves-ar/',
            'output': 'Data/LG/microwaves.xlsx'
        },
        {
            'name': 'Audio Video Systems',
            'url': 'https://newvision.jo/product-category/audio-video-and-home-theater-systems-ar/',
            'output': 'Data/LG/audio_video.xlsx'
        },
        {
            'name': 'Refrigerators',
            'url': 'https://newvision.jo/product-category/refrigerators-ar/',
            'output': 'Data/LG/refrigerators.xlsx'
        }
    ]
    
    print("🚀 Starting New Vision All Categories Scraper")
    print("=" * 60)
    
    all_results = []
    successful_categories = 0
    failed_categories = 0
    
    for i, category in enumerate(categories, 1):
        print(f"\n[{i}/{len(categories)}] Scraping {category['name']}...")
        print(f"   URL: {category['url']}")
        
        try:
            # Create output directory if it doesn't exist
            os.makedirs(os.path.dirname(category['output']), exist_ok=True)
            
            # Scrape the category
            scrape_all_products(
                base_url=category['url'],
                output_file=category['output'],
                headful=False,  # Set to False for faster execution
                delay=2.0
            )
            
            # Read the scraped data and add category information
            try:
                df = pd.read_excel(category['output'])
                df['category'] = category['name']
                df['category_url'] = category['url']
                all_results.append(df)
                successful_categories += 1
                print(f"   ✅ Successfully scraped {len(df)} products from {category['name']}")
            except Exception as e:
                print(f"   ⚠️  Scraped but couldn't read results: {e}")
                failed_categories += 1
                
        except Exception as e:
            print(f"   ❌ Failed to scrape {category['name']}: {e}")
            failed_categories += 1
        
        # Add delay between categories to be respectful
        if i < len(categories):
            print(f"   ⏳ Waiting 5 seconds before next category...")
            time.sleep(5)
    
    # Combine all results
    if all_results:
        print(f"\n📊 Combining results from {len(all_results)} categories...")
        
        # Concatenate all DataFrames
        combined_df = pd.concat(all_results, ignore_index=True)
        
        # Add metadata
        combined_df['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        combined_df['scraper_version'] = '1.0'
        
        # Reorder columns
        columns = ['category', 'category_url', 'url', 'title', 'old_price', 'new_price', 'description', 'sku', 'images', 'status', 'scraped_at', 'scraper_version']
        if 'error' in combined_df.columns:
            columns.append('error')
        
        combined_df = combined_df[columns]
        
        # Save combined results
        output_file = f"Data/LG/all_newvision_products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        combined_df.to_excel(output_file, index=False)
        
        # Print final summary
        print("\n" + "=" * 60)
        print(" 🎉 SCRAPING COMPLETE!")
        print("=" * 60)
        print(f"Total categories processed: {len(categories)}")
        print(f"Successful categories: {successful_categories}")
        print(f"Failed categories: {failed_categories}")
        print(f"Total products scraped: {len(combined_df)}")
        print(f"Combined file saved to: {output_file}")
        
        # Print category breakdown
        print(f"\n📈 Category Breakdown:")
        category_counts = combined_df['category'].value_counts()
        for category, count in category_counts.items():
            print(f"   {category}: {count} products")
        
        return output_file
    else:
        print("\n❌ No data was successfully scraped from any category.")
        return None

if __name__ == "__main__":
    try:
        output_file = scrape_all_newvision_categories()
        if output_file:
            print(f"\n✅ All done! Check the results in: {output_file}")
        else:
            print("\n❌ Scraping failed. Please check the error messages above.")
    except KeyboardInterrupt:
        print("\n\n⏹️  Scraping interrupted by user.")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")


