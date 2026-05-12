#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Add Images to Clean Descriptions Script
Adds image data directly to the clean descriptions file.
"""

import pandas as pd
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def add_images_to_clean():
    """Add image data to the clean descriptions file."""
    try:
        # Read the clean descriptions file
        logger.info("Reading clean descriptions file...")
        df_clean = pd.read_excel('Data/Product Details_CLEAN.xlsx')
        
        # Read the images file
        logger.info("Reading images file...")
        df_images = pd.read_excel('Data/Product Details_FULL_scraped.xlsx')
        
        logger.info(f"Clean file shape: {df_clean.shape}")
        logger.info(f"Images file shape: {df_images.shape}")
        
        # Create a mapping of product names to image data
        image_mapping = {}
        for _, row in df_images.iterrows():
            product_name = row['اسم المنتج']
            if product_name not in image_mapping:
                image_mapping[product_name] = {
                    'imgs src': row['imgs src'],
                    'Found': row['Found'],
                    'Status': row['Status']
                }
        
        # Add image data to clean file
        logger.info("Adding image data...")
        df_clean['imgs src'] = df_clean['اسم المنتج'].map(lambda x: image_mapping.get(x, {}).get('imgs src', ''))
        df_clean['Found'] = df_clean['اسم المنتج'].map(lambda x: image_mapping.get(x, {}).get('Found', 'NO'))
        df_clean['Status'] = df_clean['اسم المنتج'].map(lambda x: image_mapping.get(x, {}).get('Status', 'No images found'))
        
        # Fill missing values
        df_clean['imgs src'] = df_clean['imgs src'].fillna('')
        df_clean['Found'] = df_clean['Found'].fillna('NO')
        df_clean['Status'] = df_clean['Status'].fillna('No images found')
        
        logger.info(f"Final file shape: {df_clean.shape}")
        
        # Save the final file
        output_file = 'Data/Product Details_COMPLETE.xlsx'
        df_clean.to_excel(output_file, index=False)
        logger.info(f"Complete file saved to: {output_file}")
        
        # Print summary
        products_with_images = len(df_clean[df_clean['Found'] == 'YES'])
        total_images = sum(len(str(row['imgs src']).split(';')) for _, row in df_clean.iterrows() if row['imgs src'])
        
        logger.info("=" * 50)
        logger.info("FINAL RESULTS")
        logger.info("=" * 50)
        logger.info(f"Total products: {len(df_clean)}")
        logger.info(f"Products with images: {products_with_images}")
        logger.info(f"Total images: {total_images}")
        logger.info(f"Final columns: {list(df_clean.columns)}")
        
        return df_clean
        
    except Exception as e:
        logger.error(f"Error: {e}")
        raise

if __name__ == "__main__":
    add_images_to_clean()

