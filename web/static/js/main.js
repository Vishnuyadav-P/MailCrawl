/* ==========================================================================
   Wiring: settings panel, scan lifecycle, stream handlers.
   ========================================================================== */

const Main = (() => {

  /* ---------- Settings panel ---------- */

  function readConfig() {
    return {
      // 0 means "follow links as deep as the site goes"; the checkbox replaces
      // the slider rather than disabling it.
      max_depth: $('unlimited-depth').checked ? 0 : Number($('max-depth').value),
      timeout: Number($('timeout').value),
      concurrent_requests: Number($('concurrency').value),
      include_pdfs: $('include-pdfs').checked,
      enable_pdf_ocr: $('enable-pdf-ocr').checked,
      js_rendering: $('js-rendering').value,
      only_target_domain: $('only-target-domain').checked,
      include_subdomains: $('include-subdomains').checked,
      include_external_sources: $('include-external').checked,
      include_wellknown_sources: $('include-wellknown').checked,
      extra_source_urls: $('extra-urls').value
        .split('\n').map((line) => line.trim()).filter(Boolean),
      // The control asks to IGNORE robots; the API asks whether to RESPECT it.
      respect_robots: !$('ignore-robots').checked,
    };
  }

  function bindSettings() {
    $('unlimited-depth').addEventListener('change', (e) => {
      show($('depth-field'), !e.target.checked);
    });

    $('ignore-robots').addEventListener('change', (e) => {
      show($('robots-warn'), e.target.checked);
    });

    const ranges = [
      ['max-depth', 'max-depth-out', (v) => v],
      ['timeout', 'timeout-out', (v) => `${v}s`],
      ['concurrency', 'concurrency-out', (v) => v],
      ['reveal-every', 'reveal-out', (v) => v],
    ];
    for (const [input, output, format] of ranges) {
      const el = $(input);
      el.addEventListener('input', () => setText($(output), format(el.value)));
    }
  }

  /* ---------- Status ---------- */

  const STATUS_LABEL = {
    idle: 'Idle',
    queued: 'Queued',
    running: 'Scanning',
    cancelling: 'Stopping',
    completed: 'Complete',
    interrupted: 'Interrupted',
    cancelled: 'Stopped',
    failed: 'Failed',
  };

  function setStatus(status) {
    State.status = status;

    const dotState =
      status === 'completed' ? 'done'
      : ['failed', 'interrupted', 'cancelled'].includes(status) ? 'error'
      : ['running', 'queued', 'cancelling'].includes(status) ? 'running'
      : 'idle';

    $('status-dot').dataset.state = dotState;
    setText($('status-label'), STATUS_LABEL[status] || status);

    const running = State.isRunning;
    $('start-btn').disabled = running;
    show($('cancel-btn'), running);
  }

  /* ---------- Progress ---------- */

  const PHASE_LABEL = {
    queued: 'Queued', crawl: 'Crawling', pdfs: 'Reading PDFs',
    external: 'External sources', dedup: 'Deduplicating',
    persist: 'Saving results',
  };

  function applyProgress(progress) {
    if (!progress) return;

    setText($('pages-scanned'), progress.pages_scanned ?? 0);
    setText($('pages-known'), progress.pages_discovered ?? 0);

    const percent = progress.percent ?? 0;
    $('frontier-fill').style.width = `${percent}%`;
    $('frontier-bar').setAttribute('aria-valuenow', String(percent));

    const url = progress.current_url || '';
    setText($('frontier-url'), url ? `${progress.status_message || ''} ${url}`.trim() : (progress.status_message || ''));
  }

  function applyPhase(phase) {
    State.phase = phase;
    setText($('frontier-phase'), PHASE_LABEL[phase] || phase);
  }

  const TERMINAL_PHASE = {
    completed: 'Complete', cancelled: 'Stopped',
    interrupted: 'Interrupted', failed: 'Failed',
  };

  /* Leaves a final reading instead of whatever phase the scan stopped in.
     Called from both onTerminal and onSnapshot, because a client connecting to
     an already-finished scan receives only a snapshot — there is no terminal
     event left to replay. */
  function applyTerminalPresentation(status) {
    $('frontier-fill').style.width = '100%';
    setText($('frontier-phase'), TERMINAL_PHASE[status] || status);

    const stats = State.stats;
    setText($('frontier-url'), stats
      ? `${stats.pages_scanned} page(s) scanned across ${stats.sites_discovered} site(s).`
      : '');
  }

  const TERMINAL_STATUSES = ['completed', 'cancelled', 'interrupted', 'failed'];

  /* ---------- Stream handlers ---------- */

  function updateLiveExportLinks() {
    if (!State.scanId) return;
    const csvBtn = $('live-export-csv');
    const xlsxBtn = $('live-export-xlsx');
    if (csvBtn) csvBtn.href = API.exportUrl(State.scanId, 'csv');
    if (xlsxBtn) xlsxBtn.href = API.exportUrl(State.scanId, 'xlsx');
  }

  const handlers = {
    onSnapshot(snap) {
      // A snapshot is authoritative: it replaces client state wholesale, which
      // is what makes reconnecting after missed events safe.
      State.liveEmails.clear();
      const rows = snap.emails_live || [];
      rows.forEach((row) => State.liveEmails.set(row.email, row));
      State.liveTruncated = snap.live_truncated;
      State.stats = snap.stats;
      State.config = snap.config;

      Rail.replaceAll(rows);
      applyProgress(snap.progress);
      applyPhase(snap.phase);
      setStatus(snap.status);
      updateLiveExportLinks();

      if (snap.stats) Ledger.renderTiles(snap.stats);

      if (TERMINAL_STATUSES.includes(snap.status)) {
        applyTerminalPresentation(snap.status);
        Ledger.load();
      }
    },

    onProgress(progress) {
      applyProgress(progress);
      if (progress.phase) applyPhase(progress.phase);
    },

    onEmails(payload) {
      const added = State.addEmails(payload.batch || []);
      Rail.append(added);
      updateLiveExportLinks();
    },

    onPhase(payload) {
      applyPhase(payload.phase);
    },

    onStats(stats) {
      State.stats = stats;
      Ledger.renderTiles(stats);
    },

    async onTerminal(status, payload) {
      setStatus(status);
      applyTerminalPresentation(status);

      try { localStorage.removeItem(ACTIVE_SCAN_KEY); } catch { /* ignore */ }

      if (status === 'completed') {
        toast(`Scan complete — ${payload.result_count} address(es) in ${formatDuration(payload.duration_seconds)}.`);
        await Ledger.load();
      } else {
        const reason = (payload.error && payload.error.message) || 'Scan stopped.';
        toast(`${STATUS_LABEL[status] || status}: ${reason} Resume it from the history below.`, 'error', 8000);
        await Ledger.load();
      }

      History.refresh();
    },

    onDisconnect() {
      // The server closes the stream once a scan is terminal; only warn if it
      // dropped while we still believed the scan was live.
      if (State.isRunning) {
        toast('Live stream disconnected. The scan is still running — reload to reattach.', 'error');
      }
    },
  };

  /* ---------- Lifecycle ---------- */

  // Lets a reopened tab find the scan it was watching.
  const ACTIVE_SCAN_KEY = 'dei.activeScan';

  function attachToScan(scanId, { resetRail = true, arm = true } = {}) {
    State.scanId = scanId;
    if (resetRail) {
      State.reset();
      Rail.clear();
    }

    show($('frontier'), true);
    show($('rail-section'), true);
    updateLiveExportLinks();

    if (arm) {
      const frontier = $('frontier');
      frontier.classList.add('is-arming');
      setTimeout(() => frontier.classList.remove('is-arming'), 900);
    }

    setStatus('running');
    try {
      localStorage.setItem(ACTIVE_SCAN_KEY, scanId);
    } catch { /* private mode; reattach is a convenience, not a requirement */ }

    Stream.connect(scanId, handlers);
  }

  /**
   * Reattaches to a scan started before this page load.
   *
   * The snapshot is fetched first so a finished or evicted scan is presented
   * correctly instead of sitting on an optimistic "Scanning". Only a scan that
   * is genuinely still active gets a stream.
   */
  async function reattach(scanId) {
    let snapshot;
    try {
      snapshot = await API.snapshot(scanId);
    } catch {
      // Unknown to both the registry and the log; nothing to go back to.
      try { localStorage.removeItem(ACTIVE_SCAN_KEY); } catch { /* ignore */ }
      return;
    }

    State.scanId = scanId;
    show($('frontier'), true);

    const stillRunning = !TERMINAL_STATUSES.includes(snapshot.status);

    if (stillRunning && !snapshot.archived) {
      show($('rail-section'), true);
      attachToScan(scanId, { resetRail: true, arm: false });
      toast(`Reattached to the scan of ${snapshot.target_domain}.`);
      return;
    }

    // Finished while the tab was away: present the outcome without a stream.
    try { localStorage.removeItem(ACTIVE_SCAN_KEY); } catch { /* ignore */ }

    if (snapshot.emails_live && snapshot.emails_live.length) {
      show($('rail-section'), true);
      snapshot.emails_live.forEach((row) => State.liveEmails.set(row.email, row));
      Rail.replaceAll(snapshot.emails_live);
    }

    State.stats = snapshot.stats;
    if (snapshot.stats) Ledger.renderTiles(snapshot.stats);
    setStatus(snapshot.status);
    applyTerminalPresentation(snapshot.status);
    if (snapshot.result_count != null) Ledger.load();
  }

  async function startScan(event) {
    event.preventDefault();
    show($('form-error'), false);

    const domain = $('domain').value.trim();
    if (!domain) {
      setText($('form-error'), 'Enter a domain, for example example.com.');
      show($('form-error'), true);
      return;
    }

    try {
      const job = await API.startScan({
        domain,
        search_name: $('search-name').value.trim() || null,
        reveal_every: Number($('reveal-every').value) || 10,
        config: readConfig(),
      });

      show($('ledger'), false);
      show($('summary'), false);
      attachToScan(job.scan_id);
      History.refresh();

    } catch (err) {
      setText($('form-error'), err.message);
      show($('form-error'), true);
      setStatus('idle');
    }
  }

  async function cancelScan() {
    if (!State.scanId) return;
    try {
      await API.cancelScan(State.scanId);
      setStatus('cancelling');
      toast('Stopping — progress will be checkpointed so you can resume.');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  /* ---------- Init ---------- */

  async function init() {
    bindSettings();
    Ledger.bind();

    $('scan-form').addEventListener('submit', startScan);
    $('cancel-btn').addEventListener('click', cancelScan);

    setStatus('idle');
    History.refresh();

    // A scan outlives the tab that started it — the crawl runs on its own
    // thread server-side — so pick it back up on reload.
    let previous = null;
    try { previous = localStorage.getItem(ACTIVE_SCAN_KEY); } catch { /* ignore */ }
    if (previous) reattach(previous);

    try {
      await API.health();
    } catch {
      /* health is advisory; the app works without it */
    }
  }

  return { init, attachToScan, reattach };
})();

document.addEventListener('DOMContentLoaded', Main.init);
