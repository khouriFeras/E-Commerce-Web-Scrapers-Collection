#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Merge Descriptions with Images Script
Combines the clean descriptions with the scraped image data.
"""

import argparse
import pandas as pd
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('merge_with_images.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def merge_descriptions_with_images(descriptions_file: str, images_file: str, output_file: str = None):
    """
    Merge clean descriptions with image data.
    
    Args:
        descriptions_file: Path to file with merged descriptions
        images_file: Path to file with scraped images
        output_file: Path to output file
        
    Returns:
        Merged DataFrame
    """
    try:
        logger.info(f"Reading descriptions file: {descriptions_file}")
        df_descriptions = pd.read_excel(descriptions_file)
        
        logger.info(f"Reading images file: {images_file}")
        df_images = pd.read_excel(images_file)
        
        logger.info(f"Descriptions file shape: {df_descriptions.shape}")
        logger.info(f"Images file shape: {df_images.shape}")
        
        # Merge on product number (رقم المنتج) to avoid duplicates
        merged_df = pd.merge(
            df_descriptions, 
            df_images[['رقم المنتج', 'imgs src', 'Found', 'Status']], 
            on='رقم المنتج', 
            how='left'
        )
        
        logger.info(f"Merged file shape: {merged_df.shape}")
        
        # Check for missing images
        missing_images = merged_df['imgs src'].isna().sum()
        if missing_images > 0:
            logger.warning(f"Found {missing_images} products without images")
        
        # Fill missing image data
        merged_df['imgs src'] = merged_df['imgs src'].fillna('')
        merged_df['Found'] = merged_df['Found'].fillna('NO')
        merged_df['Status'] = merged_df['Status'].fillna('No images found')
        
        # Save merged file
        if output_file:
            merged_df.to_excel(output_file, index=False)
            logger.info(f"Merged file saved to: {output_file}")
        
        return merged_df
        
    except Exception as e:
        logger.error(f"Error merging files: {e}")
        raise


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Merge Descriptions with Images")
    parser.add_argument("--descriptions", "-d", default="Data/Product Details_CLEAN.xlsx", 
                       help="File with merged descriptions")
    parser.add_argument("--images", "-i", default="Data/Product Details_FULL_scraped.xlsx", 
                       help="File with scraped images")
    parser.add_argument("--output", "-o", default="Data/Product Details_FINAL.xlsx", 
                       help="Output file path")
    parser.add_argument("--preview", action="store_true", 
                       help="Show preview without saving")
    
    args = parser.parse_args()
    
    try:
        logger.info("Starting merge process...")
        logger.info(f"Descriptions file: {args.descriptions}")
        logger.info(f"Images file: {args.images}")
        logger.info(f"Output file: {args.output}")
        
        merged_df = merge_descriptions_with_images(
            descriptions_file=args.descriptions,
            images_file=args.images,
            output_file=args.output if not args.preview else None
        )
        
        # Print summary
        logger.info("=" * 50)
        logger.info("MERGE COMPLETED")
        logger.info("=" * 50)
        logger.info(f"Total products: {len(merged_df)}")
        logger.info(f"Products with images: {len(merged_df[merged_df['Found'] == 'YES'])}")
        logger.info(f"Products without images: {len(merged_df[merged_df['Found'] == 'NO'])}")
        
        total_images = sum(len(str(row['imgs src']).split(';')) for _, row in merged_df.iterrows() if pd.notna(row['imgs src']) and row['imgs src'])
        logger.info(f"Total images: {total_images}")
        
        if args.preview:
            logger.info("Preview mode - file not saved")
            print("\nPreview of merged data:")
            print(merged_df[['اسم المنتج', 'الوصف المدمج', 'imgs src', 'Found']].head(2).to_string())
        
        # Show sample
        print(f"\nSample merged content:")
        sample_product = merged_df.iloc[0]
        print(f"Product: {sample_product['اسم المنتج']}")
        print(f"Description length: {len(str(sample_product['الوصف المدمج']))} characters")
        print(f"Images found: {sample_product['Found']}")
        if sample_product['Found'] == 'YES':
            image_count = len(str(sample_product['imgs src']).split(';'))
            print(f"Number of images: {image_count}")
        
    except Exception as e:
        logger.error(f"Merge failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
