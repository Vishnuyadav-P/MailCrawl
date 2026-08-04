/* ==========================================================================
   Scan history: resume an interrupted scan, or reload a finished one.
   ========================================================================== */

const History = (() => {

  const STATUS_TONE = {
    completed: 'good',
    running: 'neutral',
    interrupted: 'warn',
    cancelled: 'warn',
    failed: 'bad',
  };

  function render(payload) {
    const body = $('history-rows');
    const records = payload.records || [];

    body.replaceChildren();
    show($('history-empty'), records.length === 0);

    const note = $('history-note');
    if (payload.resumable_count) {
      note.textContent =
        `${payload.resumable_count} scan(s) stopped early and can resume where they left off`;
    } else {
      note.textContent = records.length ? `${records.length} scan(s) logged` : '';
    }

    for (const record of records) {
      const tr = document.createElement('tr');
      tr.dataset.resumable = String(Boolean(record.resumable));

      tr.appendChild(cell((record.started_at || '').replace('T', ' '), 'cell-mono'));
      tr.appendChild(cell(record.search_name));
      tr.appendChild(cell(record.target_domain, 'cell-mono'));
      tr.appendChild(pillCell(
        record.active ? 'running' : record.status,
        STATUS_TONE[record.status] || 'neutral'
      ));

      [record.sites_discovered, record.pages_scanned, record.unique_emails].forEach((value) => {
        const td = cell(value);
        td.className = 'col-num';
        tr.appendChild(td);
      });

      const duration = cell(formatDuration(record.duration_seconds));
      duration.className = 'col-num';
      tr.appendChild(duration);

      tr.appendChild(actionsCell(record));
      body.appendChild(tr);
    }
  }

  function actionsCell(record) {
    const td = document.createElement('td');
    const wrap = document.createElement('div');
    wrap.className = 'history__actions';

    if (record.resumable && !record.active) {
      const resume = document.createElement('button');
      resume.type = 'button';
      resume.className = 'btn btn--ghost btn--tiny';
      resume.textContent = 'Resume';
      resume.addEventListener('click', () => resumeScan(record));
      wrap.appendChild(resume);
    }

    if (record.has_results) {
      const load = document.createElement('button');
      load.type = 'button';
      load.className = 'btn btn--ghost btn--tiny';
      load.textContent = 'Load results';
      load.addEventListener('click', () => loadResults(record));
      wrap.appendChild(load);
    }

    if (!wrap.children.length) {
      td.textContent = '—';
      td.classList.add('cell-empty');
      return td;
    }

    td.appendChild(wrap);
    return td;
  }

  async function resumeScan(record) {
    try {
      const revealEvery = Number($('reveal-every').value) || 10;
      const job = await API.resumeScan(record.scan_id, revealEvery);

      const checkpoint = job.checkpoint || {};
      toast(
        `Resuming ${record.search_name} — ${checkpoint.pages_scanned || 0} page(s) already done, ` +
        `${checkpoint.queued_urls || 0} still queued.`
      );

      // A resumed scan runs under its checkpointed settings, not the current
      // settings panel values, so the panel is left alone here on purpose.
      Main.attachToScan(job.scan_id, { resetRail: true });

    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function loadResults(record) {
    State.scanId = record.scan_id;
    State.page.offset = 0;
    await Ledger.load();
    toast(`Loaded results from ${record.search_name}.`);
    $('ledger').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function refresh() {
    try {
      render(await API.history());
    } catch (err) {
      toast(`Could not read the scan history: ${err.message}`, 'error');
    }
  }

  return { refresh, render };
})();
