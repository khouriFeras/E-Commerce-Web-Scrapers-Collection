"""
Desktop launcher app for JafarShop scrapers.
Run with: python desktop_app/app.py
Then open http://localhost:5000 in your browser.
"""

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request, send_file

BASE_DIR = Path(__file__).resolve().parent
SCRAPERS_DIR = BASE_DIR.parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
from registry import SCRAPERS, SCRAPERS_BY_ID  # noqa: E402

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))

# In-memory job store  {job_id: {"status": ..., "log": [...], "output": ...}}
jobs: dict = {}
jobs_lock = threading.Lock()


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", scrapers=SCRAPERS)


@app.route("/upload", methods=["POST"])
def upload():
    """Receive an Excel/CSV file, return its column names."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file provided"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return jsonify({"error": "Only .xlsx, .xls or .csv files are supported"}), 400

    job_id = uuid.uuid4().hex[:10]
    save_path = UPLOADS_DIR / f"{job_id}{ext}"
    f.save(save_path)

    try:
        if ext == ".csv":
            df = pd.read_csv(save_path, nrows=0)
        else:
            df = pd.read_excel(save_path, nrows=0)
        columns = list(df.columns)
    except Exception as exc:
        save_path.unlink(missing_ok=True)
        return jsonify({"error": f"Could not read file: {exc}"}), 400

    return jsonify({"job_id": job_id, "columns": columns, "path": str(save_path)})


@app.route("/run", methods=["POST"])
def run_scraper():
    """Start a scraping job in a background thread."""
    data = request.get_json()
    scraper_id = data.get("scraper")
    sku_col = data.get("sku_col", "")
    input_path = data.get("input_path")

    if not scraper_id or not input_path:
        return jsonify({"error": "scraper and input_path are required"}), 400

    scraper = SCRAPERS_BY_ID.get(scraper_id)
    if not scraper:
        return jsonify({"error": f"Unknown scraper: {scraper_id}"}), 400

    input_p = Path(input_path)
    if not input_p.exists():
        return jsonify({"error": "Uploaded file not found — please re-upload"}), 400

    # Derive output path alongside the input file
    out_ext = ".xlsx"
    output_p = UPLOADS_DIR / f"{input_p.stem}_{scraper_id}_output{out_ext}"

    job_id = uuid.uuid4().hex[:10]
    with jobs_lock:
        jobs[job_id] = {"status": "running", "log": [], "output": None}

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, scraper, input_path, str(output_p), sku_col),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/stream/<job_id>")
def stream(job_id):
    """Server-Sent Events stream for live log output."""
    def generate():
        sent = 0
        import time
        while True:
            with jobs_lock:
                job = jobs.get(job_id)
            if job is None:
                yield "data: [job not found]\n\n"
                break
            log = job["log"]
            while sent < len(log):
                line = log[sent].rstrip("\n")
                yield f"data: {line}\n\n"
                sent += 1
            if job["status"] != "running":
                status = job["status"]
                output = job.get("output") or ""
                yield f"event: done\ndata: {json.dumps({'status': status, 'output': output})}\n\n"
                break
            time.sleep(0.3)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download")
def download():
    """Download the output file produced by a job."""
    path = request.args.get("path", "")
    p = Path(path)
    if not p.exists() or not p.is_file():
        return "File not found", 404
    # Safety: only allow files inside our uploads dir
    try:
        p.relative_to(UPLOADS_DIR)
    except ValueError:
        return "Forbidden", 403
    download_name = f"{p.stem}_scraped{p.suffix}"
    return send_file(str(p), as_attachment=True, download_name=download_name)


@app.route("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": job["status"], "output": job.get("output")})


# ──────────────────────────────────────────────────────────────────────────────
# Background job runner
# ──────────────────────────────────────────────────────────────────────────────

def _run_job(job_id: str, scraper: dict, input_path: str, output_path: str, sku_col: str):
    cmd = [sys.executable, str(SCRAPERS_DIR / scraper["script"])]

    # --in / --input / --file
    cmd += [scraper["in_flag"], input_path]

    # --sku-col / --sku-column  (only if scraper has a flag and user provided a column)
    if scraper.get("sku_flag") and sku_col:
        cmd += [scraper["sku_flag"], sku_col]

    # --out / --output  (only if scraper supports it)
    if scraper.get("out_flag"):
        cmd += [scraper["out_flag"], output_path]

    # headful mode
    hm = scraper.get("headful_mode", "none")
    if hm == "flag":
        cmd.append("--headful")
    # "inverted" means don't pass --headless → browser shows by default
    # "default" / "none" → no action needed

    def _log(msg: str):
        with jobs_lock:
            jobs[job_id]["log"].append(msg)

    _log(f"[launcher] Running: {' '.join(cmd)}\n")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(SCRAPERS_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        for line in proc.stdout:
            _log(line)
        proc.wait()

        if proc.returncode != 0:
            with jobs_lock:
                jobs[job_id]["status"] = "error"
            return

        # Find output file — use specified path if it exists, else try common patterns
        out_p = Path(output_path)
        if not out_p.exists():
            out_p = _guess_output(scraper, input_path)

        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["output"] = str(out_p) if (out_p and out_p.exists()) else None

    except Exception as exc:
        _log(f"[launcher error] {exc}\n")
        with jobs_lock:
            jobs[job_id]["status"] = "error"


def _guess_output(scraper: dict, input_path: str) -> Path | None:
    """Try to find the output file when the scraper doesn't have --out."""
    inp = Path(input_path)
    # Common patterns scrapers use
    candidates = [
        inp.parent / f"{inp.stem}_scraped.xlsx",
        inp.parent / f"{inp.stem}_output.xlsx",
        SCRAPERS_DIR / scraper["script"].replace(".py", "_results.xlsx"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import webbrowser
    port = 5000
    print(f"\n  JafarShop Scraper Launcher")
    print(f"  Open: http://localhost:{port}\n")
    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
