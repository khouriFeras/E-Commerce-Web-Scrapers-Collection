"""
Convert Ambro-Sol PDF price list to an Excel file ready for scraping.

Columns extracted: Code | DESCRIPTION | Price JOD
(Picture column is intentionally skipped — images will come from the website.)
"""

import re
from pathlib import Path

import pdfplumber
import pandas as pd

PDF_PATH = Path(__file__).parent / "Ambro-Sol Price JOD 2026-03-09 Adjusted (1).pdf"
OUT_PATH = Path(__file__).parent / "Ambro-Sol_Price_List.xlsx"

HEADER_CELLS = {"Code", "Picture", "DESCRIPTION", "Price JOD"}


def clean_text(value: str | None) -> str:
    """Collapse newlines and extra whitespace."""
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_header_or_title(row: list) -> bool:
    """Skip the title row and the repeating column-header row."""
    cells = {clean_text(c) for c in row if c}
    if HEADER_CELLS.issubset(cells):
        return True
    joined = " ".join(clean_text(c) for c in row if c).lower()
    if "price list" in joined and "ambro" in joined:
        return True
    return False


def parse_price(value: str) -> float | str:
    """Convert '4.000' style price to float; keep original string if not parseable."""
    txt = clean_text(value).replace(",", "")
    if not txt:
        return ""
    try:
        return float(txt)
    except ValueError:
        return txt


def extract_rows(pdf_path: Path) -> list[dict]:
    rows: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables() or []:
                for raw_row in table:
                    if not raw_row or all(c in (None, "") for c in raw_row):
                        continue
                    if is_header_or_title(raw_row):
                        continue

                    # Expected layout: [Code, Picture, DESCRIPTION, Price]
                    padded = list(raw_row) + [""] * (4 - len(raw_row))
                    code, _picture, description, price = padded[:4]

                    code = clean_text(code)
                    description = clean_text(description)
                    price_val = parse_price(price)

                    if not code and not description and price_val == "":
                        continue

                    rows.append(
                        {
                            "Code": code,
                            "DESCRIPTION": description,
                            "Price JOD": price_val,
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

    df = pd.DataFrame(rows, columns=["Code", "DESCRIPTION", "Price JOD", "Page"])

    # Drop rows that have no code (safety net for any stray artifacts)
    df = df[df["Code"].astype(str).str.strip() != ""].reset_index(drop=True)

    df.to_excel(OUT_PATH, index=False)
    print(f"Saved: {OUT_PATH}")
    print(f"Final row count: {len(df)}")
    print("\nFirst 5 codes/prices:")
    for _, row in df.head().iterrows():
        print(f"  {row['Code']:<12} {row['Price JOD']}")


if __name__ == "__main__":
    main()
