import pandas as pd

PRODUCTS_PATH = r"D:\JafarShop\Scrapers\ankar\Anker Q1-2026 - Jafar Shop - Part 2 (1).xlsx.xlsx"
TEMPLATE_PATH = r"D:\JafarShop\Scrapers\ankar\anker_products_scraped_sg.xlsx"
OUTPUT_PATH = r"D:\JafarShop\Scrapers\ankar\concatAnker.xlsx"

# Edit these lists to control which columns are kept from each file.
# Include "SKU" in both lists so the merge key stays available.
cols_from_products = [
    "SKU / Model Number",
    "Retail",
    "Jaafar Shop Price W/O Sales TAX",
]

# Set to None (or []) to keep every column from the template after normalization.
# If you only want a subset, replace with a list that includes "SKU".
cols_from_template = None

df_products = pd.read_excel(PRODUCTS_PATH)
df_template = pd.read_excel(TEMPLATE_PATH)


def normalize_columns(df, source_name):
    normalized = [str(col).replace("\n", " ").strip() for col in df.columns]
    duplicates_mask = pd.Index(normalized).duplicated(keep="first")
    if duplicates_mask.any():
        dup_names = sorted({normalized[i] for i, dup in enumerate(duplicates_mask) if dup})
        print(
            f"Warning: duplicate columns {dup_names} in {source_name}. "
            "Keeping first occurrence; later duplicates are dropped."
        )
    df = df.copy()
    df.columns = normalized
    df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="first")]
    return df


def select_columns(df, desired_cols, source_name):
    if not desired_cols:
        return df
    missing = [col for col in desired_cols if col not in df.columns]
    if missing:
        available = ", ".join(str(col) for col in df.columns)
        raise KeyError(
            f"{missing} not in {source_name} columns. "
            f"Available columns: {available}"
        )
    return df[desired_cols]


df_products = normalize_columns(df_products, "products file")
df_template = normalize_columns(df_template, "template file")

df_products = select_columns(df_products, cols_from_products, "products file")
df_products = df_products.rename(columns={"SKU / Model Number": "SKU"})
df_template = select_columns(df_template, cols_from_template, "template file")

df_concat = pd.merge(df_template, df_products, on="SKU", how="left")
print(df_concat.head())

df_concat.to_excel(OUTPUT_PATH, index=False)
