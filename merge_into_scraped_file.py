#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Merge Descriptions into Scraped File
Adds the merged description column to the existing scraped file.
"""

import pandas as pd
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def merge_descriptions_into_scraped():
    """Add merged descriptions to the scraped file."""
    try:
        # Read the scraped file (with images)
        scraped_file = "D:\\JafarShop\\Scrapers\\Data\\Product Details_FULL_scraped.xlsx"
        logger.info(f"Reading scraped file: {scraped_file}")
        df_scraped = pd.read_excel(scraped_file)
        
        # Read the clean descriptions file
        clean_file = "Data/Product Details_CLEAN.xlsx"
        logger.info(f"Reading clean descriptions file: {clean_file}")
        df_clean = pd.read_excel(clean_file)
        
        logger.info(f"Scraped file shape: {df_scraped.shape}")
        logger.info(f"Clean file shape: {df_clean.shape}")
        
        # Create a mapping of product names to merged descriptions
        description_mapping = {}
        for _, row in df_clean.iterrows():
            product_name = row['اسم المنتج']
            description_mapping[product_name] = row['الوصف المدمج']
        
        # Add merged description column to scraped file
        logger.info("Adding merged descriptions...")
        df_scraped['الوصف المدمج'] = df_scraped['اسم المنتج'].map(description_mapping)
        
        # Fill missing descriptions
        df_scraped['الوصف المدمج'] = df_scraped['الوصف المدمج'].fillna('')
        
        # Reorder columns to put merged description after original descriptions
        columns = list(df_scraped.columns)
        # Remove the new column from its current position
        columns.remove('الوصف المدمج')
        # Insert it after the original description columns
        insert_index = columns.index('صفات/ميزات/خصائص') + 1
        columns.insert(insert_index, 'الوصف المدمج')
        df_scraped = df_scraped[columns]
        
        logger.info(f"Updated file shape: {df_scraped.shape}")
        logger.info(f"Updated columns: {list(df_scraped.columns)}")
        
        # Save back to the same file
        df_scraped.to_excel(scraped_file, index=False)
        logger.info(f"Updated file saved to: {scraped_file}")
        
        # Print summary
        logger.info("=" * 50)
        logger.info("MERGE COMPLETED")
        logger.info("=" * 50)
        logger.info(f"Total products: {len(df_scraped)}")
        logger.info(f"Products with merged descriptions: {len(df_scraped[df_scraped['الوصف المدمج'] != ''])}")
        logger.info(f"Products with images: {len(df_scraped[df_scraped['Found'] == 'YES'])}")
        
        total_images = sum(len(str(row['imgs src']).split(';')) for _, row in df_scraped.iterrows() if pd.notna(row['imgs src']) and row['imgs src'])
        logger.info(f"Total images: {total_images}")
        
        # Show sample
        print("\n=== SAMPLE DATA ===")
        sample = df_scraped.iloc[0]
        print(f"Product: {sample['اسم المنتج']}")
        print(f"Description length: {len(str(sample['الوصف المدمج']))} characters")
        print(f"Images found: {sample['Found']}")
        if sample['Found'] == 'YES':
            image_count = len(str(sample['imgs src']).split(';'))
            print(f"Number of images: {image_count}")
        
        return df_scraped
        
    except Exception as e:
        logger.error(f"Error: {e}")
        raise

if __name__ == "__main__":
    merge_descriptions_into_scraped()

