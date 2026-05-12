"""
Convert the 'Total 4N' PDF price list to an Excel file.

PDF table layout (10 columns):
    Item No. | Picture | Arabic Desc | Product Name | Description & Features |
    Unit | Qty | جملة (Wholesale) | مفرق (Retail) | BAR CODE
"""

import re
from pathlib import Path

import pdfplumber
import pandas as pd

SCRIPT_DIR = Path(__file__).parent
PDF_PATH = SCRIPT_DIR / "Total 4N (1).pdf"
OUT_PATH = SCRIPT_DIR / "Total_4N_Price_List.xlsx"

OUTPUT_COLUMNS = [
    "Item No.",
    "Arabic Description",
    "Product Name",
    "Description & Features",
    "Unit",
    "Qty",
    "Wholesale (JOD)",
    "Retail (JOD)",
    "BAR CODE",
    "Page",
]


def clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"[ \t]+", " ", str(value).replace("\r", " ")).strip()


def clean_multiline(value) -> str:
    """Preserve line breaks but normalise spaces."""
    if value is None:
        return ""
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in str(value).splitlines()]
    return "\n".join(ln for ln in lines if ln)


def parse_number(value):
    txt = clean(value).replace(",", "")
    if not txt:
        return ""
    try:
        return float(txt)
    except ValueError:
        return txt


def parse_int(value):
    txt = clean(value).replace(",", "")
    if not txt:
        return ""
    try:
        return int(float(txt))
    except ValueError:
        return txt


def is_skip_row(row: list) -> bool:
    """Skip title/header rows."""
    first = clean(row[0]) if row else ""
    if not first:
        return True
    lower = first.lower()
    if lower.startswith("list of to4n") or lower.startswith("item no"):
        return True
    return False


def extract_rows(pdf_path: Path) -> list[dict]:
    rows: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables() or []:
                for raw_row in table:
                    if not raw_row or all(c in (None, "") for c in raw_row):
                        continue
                    if is_skip_row(raw_row):
                        continue

                    padded = list(raw_row) + [""] * (10 - len(raw_row))
                    (
                        item_no,
                        _picture,
                        arabic_desc,
                        product_name,
                        desc_features,
                        unit,
                        qty,
                        wholesale,
                        retail,
                        barcode,
                    ) = padded[:10]

                    item_no_c = clean(item_no)
                    if not item_no_c:
                        continue

                    rows.append(
                        {
                            "Item No.": item_no_c,
                            "Arabic Description": clean_multiline(arabic_desc),
                            "Product Name": clean_multiline(product_name),
                            "Description & Features": clean_multiline(desc_features),
                            "Unit": clean(unit),
                            "Qty": parse_int(qty),
                            "Wholesale (JOD)": parse_number(wholesale),
                            "Retail (JOD)": parse_number(retail),
                            "BAR CODE": clean(barcode),
                            "Page": page_num,
                        }
                    )
    return rows


def main() -> None:
    if not PDF_PATH.exists():
        raise FileNotFoundError(f"PDF not found: {PDF_PATH}")

    print(f"Reading: {PDF_PATH.name}")
    rows = extract_rows(PDF_PATH)
    print(f"Extracted {len(rows)} rows")

    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    df.to_excel(OUT_PATH, index=False)
    print(f"Saved: {OUT_PATH}")
    print(f"Final row count: {len(df)}")
    print(f"Unique Item No.: {df['Item No.'].nunique()}")

    print("\nFirst 8 items:")
    for _, r in df.head(8).iterrows():
        print(f"  {r['Item No.']:<14} qty={r['Qty']:<5} "
              f"W={r['Wholesale (JOD)']:<7} R={r['Retail (JOD)']:<8} "
              f"barcode={r['BAR CODE']}")


if __name__ == "__main__":
    main()
