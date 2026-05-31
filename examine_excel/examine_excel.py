#!/usr/bin/env python3
import pandas as pd
import sys

def examine_excel(file_path, sheet_name=None):
    try:
        # Read the Excel file
        if sheet_name:
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
        else:
            df = pd.read_excel(file_path, header=None)
        
        print(f"File: {file_path}")
        print(f"Sheet: {sheet_name if sheet_name else 'Default'}")
        print(f"Shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")
        print("\nFirst 10 rows:")
        print(df.head(10).to_string())
        
        # Check for non-empty columns
        print("\nNon-empty columns:")
        for i, col in enumerate(df.columns):
            non_empty = df[col].notna().sum()
            if non_empty > 0:
                print(f"  Column {i} ({col}): {non_empty} non-empty values")
                print(f"    Sample values: {df[col].dropna().head(3).tolist()}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    examine_excel("Data/HA&AV.xlsx", "HA")
