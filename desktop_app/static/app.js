(() => {
  // ── State ──────────────────────────────────────────────────────────────────
  let uploadedPath = null;
  let selectedScraper = null;
  let outputPath = null;
  let eventSource = null;

  // ── Element refs ──────────────────────────────────────────────────────────
  const dropZone    = document.getElementById('dropZone');
  const fileInput   = document.getElementById('fileInput');
  const fileStatus  = document.getElementById('fileStatus');
  const step2       = document.getElementById('step2');
  const step3       = document.getElementById('step3');
  const step4       = document.getElementById('step4');
  const stepProg    = document.getElementById('stepProgress');
  const colSelect   = document.getElementById('colSelect');
  const scraperGrid = document.getElementById('scraperGrid');
  const runSummary  = document.getElementById('runSummary');
  const btnRun      = document.getElementById('btnRun');
  const logBox      = document.getElementById('logBox');
  const progStatus  = document.getElementById('progressStatus');
  const btnDownload = document.getElementById('btnDownload');
  const btnReset    = document.getElementById('btnReset');

  // ── File upload ────────────────────────────────────────────────────────────
  dropZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));

  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  });

  async function handleFile(file) {
    if (!file) return;
    showFileStatus(`Uploading ${file.name}…`, false);

    const fd = new FormData();
    fd.append('file', file);

    try {
      const res = await fetch('/upload', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.error) { showFileStatus(data.error, true); return; }

      uploadedPath = data.path;
      showFileStatus(`✓  ${file.name}  (${data.columns.length} columns detected)`, false);

      // Populate column dropdown
      colSelect.innerHTML = '<option value="">— pick a column —</option>';
      data.columns.forEach(c => {
        const opt = document.createElement('option');
        opt.value = opt.textContent = c;
        // Auto-select likely SKU columns
        if (/sku|barcode|model|code|item/i.test(c)) opt.selected = true;
        colSelect.appendChild(opt);
      });

      show(step2);
      show(step3);
    } catch (e) {
      showFileStatus('Upload failed. Is the server running?', true);
    }
  }

  function showFileStatus(msg, isError) {
    fileStatus.textContent = msg;
    fileStatus.className = 'file-status' + (isError ? ' error' : '');
    fileStatus.classList.remove('hidden');
  }

  // ── Scraper selection ──────────────────────────────────────────────────────
  scraperGrid.addEventListener('click', e => {
    const btn = e.target.closest('.scraper-btn');
    if (!btn) return;
    scraperGrid.querySelectorAll('.scraper-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    selectedScraper = { id: btn.dataset.id, name: btn.querySelector('.scraper-name').textContent };
    updateRunSummary();
    show(step4);
  });

  colSelect.addEventListener('change', updateRunSummary);

  function updateRunSummary() {
    const col = colSelect.value || '(auto-detect)';
    const scraper = selectedScraper ? selectedScraper.name : '—';
    runSummary.innerHTML =
      `<strong>File:</strong> ${uploadedPath ? uploadedPath.split(/[\\/]/).pop() : '—'}<br>` +
      `<strong>SKU column:</strong> ${col}<br>` +
      `<strong>Scraper:</strong> ${scraper}`;
  }

  // ── Run ────────────────────────────────────────────────────────────────────
  btnRun.addEventListener('click', async () => {
    if (!uploadedPath || !selectedScraper) {
      alert('Please upload a file and choose a scraper first.');
      return;
    }

    // Switch to progress view
    hide(step4);
    show(stepProg);
    logBox.textContent = '';
    progStatus.textContent = 'Starting…';
    progStatus.className = 'progress-status running';
    btnDownload.classList.add('hidden');
    outputPath = null;

    const payload = {
      scraper: selectedScraper.id,
      sku_col: colSelect.value,
      input_path: uploadedPath,
    };

    let jobId;
    try {
      const res = await fetch('/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.error) { progStatus.textContent = '✗ ' + data.error; progStatus.className = 'progress-status error'; return; }
      jobId = data.job_id;
    } catch (e) {
      progStatus.textContent = '✗ Could not start job.';
      progStatus.className = 'progress-status error';
      return;
    }

    // Listen to SSE stream
    if (eventSource) eventSource.close();
    eventSource = new EventSource(`/stream/${jobId}`);

    eventSource.onmessage = e => {
      logBox.textContent += e.data + '\n';
      logBox.scrollTop = logBox.scrollHeight;
    };

    eventSource.addEventListener('done', e => {
      const info = JSON.parse(e.data);
      eventSource.close();

      if (info.status === 'done') {
        progStatus.textContent = '✓ Scraping complete!';
        progStatus.className = 'progress-status success';
        if (info.output) {
          outputPath = info.output;
          btnDownload.classList.remove('hidden');
          btnDownload.onclick = () => {
            window.location.href = '/download?path=' + encodeURIComponent(outputPath);
          };
        } else {
          progStatus.textContent += '  (output file not found — check log)';
        }
      } else {
        progStatus.textContent = '✗ Scraper exited with an error. Check the log above.';
        progStatus.className = 'progress-status error';
      }
    });

    eventSource.onerror = () => {
      progStatus.textContent = '✗ Lost connection to server.';
      progStatus.className = 'progress-status error';
      eventSource.close();
    };
  });

  // ── Reset ──────────────────────────────────────────────────────────────────
  btnReset.addEventListener('click', () => {
    if (eventSource) { eventSource.close(); eventSource = null; }
    uploadedPath = null;
    selectedScraper = null;
    outputPath = null;
    fileInput.value = '';
    fileStatus.className = 'file-status hidden';
    colSelect.innerHTML = '<option value="">— pick a column —</option>';
    scraperGrid.querySelectorAll('.scraper-btn').forEach(b => b.classList.remove('selected'));
    logBox.textContent = '';
    progStatus.textContent = '';
    btnDownload.classList.add('hidden');
    [step2, step3, step4, stepProg].forEach(hide);
  });

  // ── Helpers ────────────────────────────────────────────────────────────────
  function show(el) { el.classList.remove('hidden'); }
  function hide(el) { el.classList.add('hidden'); }
})();
