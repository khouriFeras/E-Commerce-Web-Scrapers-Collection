#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Merge Description Columns Script
Merges الوصف الكامل and صفات/ميزات/خصائص columns into a single column.
"""

import argparse
import pandas as pd
import logging
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('merge_descriptions.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def clean_text(text: str) -> str:
    """
    Clean and normalize text content.
    
    Args:
        text: Input text to clean
        
    Returns:
        Cleaned text
    """
    if pd.isna(text) or not text:
        return ""
    
    text = str(text).strip()
    
    # Remove extra whitespace and normalize line breaks
    text = ' '.join(text.split())
    
    # Replace multiple newlines with single newline
    import re
    text = re.sub(r'\n+', '\n', text)
    
    return text


def merge_descriptions(desc1: str, desc2: str, merge_strategy: str = "combine") -> str:
    """
    Merge two description columns based on the specified strategy.
    
    Args:
        desc1: First description (الوصف الكامل)
        desc2: Second description (صفات/ميزات/خصائص)
        merge_strategy: Strategy to use for merging
        
    Returns:
        Merged description
    """
    desc1_clean = clean_text(desc1)
    desc2_clean = clean_text(desc2)
    
    if not desc1_clean and not desc2_clean:
        return ""
    
    if not desc1_clean:
        return desc2_clean
    
    if not desc2_clean:
        return desc1_clean
    
    if desc1_clean == desc2_clean:
        return desc1_clean
    
    if merge_strategy == "combine":
        # Combine both descriptions with a separator
        return f"{desc1_clean}\n\n{desc2_clean}"
    
    elif merge_strategy == "longest":
        # Use the longer description
        return desc1_clean if len(desc1_clean) > len(desc2_clean) else desc2_clean
    
    elif merge_strategy == "first":
        # Use the first description
        return desc1_clean
    
    elif merge_strategy == "second":
        # Use the second description
        return desc2_clean
    
    else:
        # Default to combine
        return f"{desc1_clean}\n\n{desc2_clean}"


def merge_excel_columns(input_file: str, output_file: str = None, 
                       merge_strategy: str = "combine", 
                       new_column_name: str = "الوصف المدمج",
                       keep_original: bool = False) -> pd.DataFrame:
    """
    Merge description columns in an Excel file.
    
    Args:
        input_file: Path to input Excel file
        output_file: Path to output Excel file
        merge_strategy: Strategy for merging (combine, longest, first, second)
        new_column_name: Name for the merged column
        keep_original: Whether to keep original columns
        
    Returns:
        Updated DataFrame
    """
    try:
        logger.info(f"Reading Excel file: {input_file}")
        df = pd.read_excel(input_file)
        
        # Check if required columns exist
        required_columns = ['الوصف الكامل', 'صفات/ميزات/خصائص']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            raise ValueError(f"Missing required columns: {missing_columns}")
        
        logger.info(f"Original DataFrame shape: {df.shape}")
        logger.info(f"Using merge strategy: {merge_strategy}")
        
        # Merge the columns
        df[new_column_name] = df.apply(
            lambda row: merge_descriptions(
                row['الوصف الكامل'], 
                row['صفات/ميزات/خصائص'], 
                merge_strategy
            ), 
            axis=1
        )
        
        # Remove original columns if not keeping them
        if not keep_original:
            df = df.drop(columns=required_columns)
            logger.info("Removed original description columns")
        
        logger.info(f"Updated DataFrame shape: {df.shape}")
        
        # Save to output file
        if output_file:
            df.to_excel(output_file, index=False)
            logger.info(f"Results saved to: {output_file}")
        
        return df
        
    except Exception as e:
        logger.error(f"Error processing Excel file: {e}")
        raise


def main():
    """Main function to run the column merger."""
    parser = argparse.ArgumentParser(description="Merge Description Columns in Excel File")
    parser.add_argument("--input", "-i", required=True, help="Input Excel file path")
    parser.add_argument("--output", "-o", help="Output Excel file path (default: adds '_merged' to input name)")
    parser.add_argument("--strategy", "-s", choices=["combine", "longest", "first", "second"], 
                       default="combine", help="Merge strategy (default: combine)")
    parser.add_argument("--column-name", "-c", default="الوصف المدمج", 
                       help="Name for the merged column (default: الوصف المدمج)")
    parser.add_argument("--keep-original", action="store_true", 
                       help="Keep original columns in addition to merged column")
    parser.add_argument("--preview", action="store_true", 
                       help="Show preview of merged data without saving")
    
    args = parser.parse_args()
    
    # Determine output file
    if args.output:
        output_file = args.output
    else:
        input_path = args.input
        if input_path.endswith('.xlsx'):
            output_file = input_path.replace('.xlsx', '_merged.xlsx')
        else:
            output_file = input_path + '_merged.xlsx'
    
    try:
        # Run merging
        logger.info("Starting column merging process...")
        logger.info(f"Input file: {args.input}")
        logger.info(f"Output file: {output_file}")
        logger.info(f"Merge strategy: {args.strategy}")
        logger.info(f"New column name: {args.column_name}")
        logger.info(f"Keep original columns: {args.keep_original}")
        
        df = merge_excel_columns(
            input_file=args.input,
            output_file=output_file if not args.preview else None,
            merge_strategy=args.strategy,
            new_column_name=args.column_name,
            keep_original=args.keep_original
        )
        
        # Print summary
        logger.info("=" * 50)
        logger.info("MERGING COMPLETED")
        logger.info("=" * 50)
        logger.info(f"Total rows processed: {len(df)}")
        logger.info(f"New column '{args.column_name}' created")
        
        if args.preview:
            logger.info("Preview mode - file not saved")
            print("\nPreview of merged data:")
            print(df[[args.column_name]].head(3).to_string())
        else:
            logger.info(f"Results saved to: {output_file}")
        
        # Show sample of merged content
        print(f"\nSample merged content:")
        sample_text = df[args.column_name].iloc[0] if len(df) > 0 else "No data"
        print(f"Length: {len(str(sample_text))} characters")
        print(f"Preview: {str(sample_text)[:200]}...")
        
    except Exception as e:
        logger.error(f"Merging failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
