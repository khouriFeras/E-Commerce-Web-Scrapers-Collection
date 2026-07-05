# How to Add a New Scraper to the Desktop App

The desktop app is a Flask launcher. It runs scrapers as subprocesses and streams their output live to the browser. Adding a new scraper is always two steps: **write the scraper script**, then **register it**.

---

## Step 1 — Create the scraper folder and file

Create a new folder in the project root (alongside the other scrapers):

```
d:\JafarShop\Scrapers\
└── your_store\
    └── your_store_scraper.py   ← your new file
```

Name the folder and file consistently using lowercase with underscores.

---

## Step 2 — Write the scraper script

The app calls your scraper as a subprocess and passes arguments via CLI flags. Your script must:

1. Accept CLI flags (at minimum `--in` and `--out`)
2. Read an Excel/CSV file
3. Scrape each row
4. Write results to an output Excel file
5. Print progress to stdout (this appears live in the app's log window)

### Minimal template

```python
import argparse
import pandas as pd

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in",      dest="input",    required=True)
    parser.add_argument("--out",     dest="output",   required=True)
    parser.add_argument("--sku-col", dest="sku_col",  default=None)
    args = parser.parse_args()

    df = pd.read_excel(args.input)

    # Auto-detect SKU column if not provided
    sku_col = args.sku_col
    if not sku_col:
        for col in df.columns:
            if "sku" in col.lower() or "model" in col.lower():
                sku_col = col
                break
    if not sku_col:
        raise ValueError("Could not find a SKU column. Pass --sku-col explicitly.")

    results = []
    for i, row in df.iterrows():
        sku = str(row[sku_col]).strip()
        print(f"[{i+1}/{len(df)}] Scraping {sku} ...")

        # --- your scraping logic here ---
        description = "..."
        images = "url1;url2"
        # --------------------------------

        results.append({"SKU": sku, "Description": description, "Images": images})

    out_df = pd.DataFrame(results)
    merged = df.merge(out_df, on="SKU", how="left")
    merged.to_excel(args.output, index=False)
    print(f"Done. Saved to {args.output}")

if __name__ == "__main__":
    main()
```

### If you need a browser (Selenium)

Add a `--headful` flag (or `--headless`, see the headful_mode section below):

```python
parser.add_argument("--headful", action="store_true", default=False)
```

Then build the driver accordingly:

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def build_driver(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)
```

---

## Step 3 — Register the scraper

Open [desktop_app/registry.py](registry.py) and add a new entry to the `SCRAPERS` list. Pick the appropriate section (Pet, Electronics, etc.) or add a new category comment.

```python
{
    "id":          "your_store",                          # unique, no spaces
    "name":        "Your Store Name",                     # shown in the UI
    "script":      "your_store/your_store_scraper.py",    # path from project root
    "in_flag":     "--in",                                # flag for the input file
    "sku_flag":    "--sku-col",                           # flag for the SKU column (or None)
    "out_flag":    "--out",                               # flag for the output file (or None)
    "headful_mode": "flag",                               # see table below
    "description": "Scrapes images & description from yourstore.com by SKU",
},
```

### headful_mode values

| Value | When to use | What the launcher does |
|---|---|---|
| `"flag"` | Script is headless by default; `--headful` enables headed mode | Passes `--headful` when user toggles headed mode |
| `"inverted"` | Script is headless by default via `--headless` flag; omitting it = headed | Passes nothing (omits `--headless`) for headed mode |
| `"default"` | Script is always headed (e.g. `HEADLESS = False` constant in code) | No flag passed either way |
| `"none"` | Script uses requests, no browser at all | No flag ever passed |

### Omitting optional keys

- If your scraper does **not** take a SKU column flag, set `"sku_flag": None`
- If your scraper writes its own output file (no `--out` flag), set `"out_flag": None` — the launcher will try to find the output using common name patterns

---

## Step 4 — Restart the app

The registry is imported once at startup. Restart the Flask server to pick up the new entry:

```
python desktop_app/app.py
```

Your scraper will now appear in the scraper picker in the UI.

---

## Step 5 — Test it

1. Open `http://localhost:5000` in your browser
2. Upload a test Excel file with a SKU column
3. Select your column
4. Pick your new scraper from the grid
5. Click Run and watch the live log
6. Download the output file and verify the results

---

## Quick reference: how the launcher calls your script

The launcher builds a command like this and runs it as a subprocess:

```
python your_store/your_store_scraper.py \
  --in  "C:\path\to\uploads\file.xlsx" \
  --sku-col "SKU" \
  --out "C:\path\to\uploads\file_your_store_output.xlsx"
```

Everything your script prints to stdout (or stderr merged into stdout) appears live in the app's log panel.

The job is marked **done** when the script exits with code `0`, and **error** if it exits with a non-zero code.

---

## Checklist

- [ ] Created `your_store/your_store_scraper.py`
- [ ] Script accepts `--in`, `--out`, and optionally `--sku-col` via argparse
- [ ] Script exits with code `0` on success
- [ ] Script prints progress lines to stdout
- [ ] Added entry to `SCRAPERS` list in `desktop_app/registry.py`
- [ ] Restarted the app
- [ ] Ran a test end-to-end through the UI
