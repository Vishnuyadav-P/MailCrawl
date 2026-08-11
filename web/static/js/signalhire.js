/* ==========================================================================
   SignalHire Employee Crawler UI (Full-Width Stacked Layout matching Domain Scanner)
   ========================================================================== */

const SignalHire = (() => {
  let lastResults = [];
  let railCount = 0;
  let currentSearchQuery = '';

  function getEl(id) {
    return document.getElementById(id);
  }

  function setText(el, str) {
    if (el) el.textContent = str;
  }

  function show(el, visible) {
    if (el) el.hidden = !visible;
  }

  async function downloadExport(format) {
    if (!lastResults.length) {
      const errorEl = getEl('signalhire-error');
      if (errorEl) {
        setText(errorEl, 'No employee results to export. Please run a search first.');
        show(errorEl, true);
      }
      return;
    }

    try {
      const response = await fetch('/api/signalhire/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: lastResults, format })
      });

      if (!response.ok) {
        throw new Error('Failed to export: ' + response.status);
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `signalhire_employees.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('[SignalHire] Export error:', err);
      const errorEl = getEl('signalhire-error');
      if (errorEl) {
        setText(errorEl, err.message || 'Export failed');
        show(errorEl, true);
      }
    }
  }

  function setExportEnabled(enabled) {
    const btns = [
      getEl('signalhire-export-csv'),
      getEl('signalhire-export-xlsx'),
      getEl('signalhire-live-csv'),
      getEl('signalhire-live-xlsx'),
    ];
    btns.forEach(btn => {
      if (btn) btn.disabled = !enabled;
    });
  }

  function renderRail(results) {
    const railSection = getEl('signalhire-rail-section');
    const railOl = getEl('signalhire-rail');
    const railEmpty = getEl('signalhire-rail-empty');
    if (!railSection || !railOl) return;

    if (results.length === 0) {
      show(railSection, false);
      railOl.innerHTML = '';
      railCount = 0;
      return;
    }

    show(railSection, true);
    if (railEmpty) show(railEmpty, false);

    for (let i = railCount; i < results.length; i++) {
      const item = results[i];
      const li = document.createElement('li');
      li.className = 'rail__row row-animate';
      li.style.setProperty('--prov', 'var(--p-web)');
      li.style.setProperty('--prov-wash', 'var(--p-web-wash)');

      const sigil = document.createElement('span');
      sigil.className = 'rail__sigil';
      sigil.textContent = 'EMP';

      const nameSpan = document.createElement('span');
      nameSpan.className = 'rail__email';
      nameSpan.style.fontWeight = '600';
      nameSpan.textContent = item.name;

      const titleSpan = document.createElement('span');
      titleSpan.className = 'rail__where';
      titleSpan.textContent = item.title || 'Employee';

      li.appendChild(sigil);
      li.appendChild(nameSpan);
      li.appendChild(titleSpan);

      railOl.prepend(li);
    }
    railCount = results.length;
  }

  function renderLedger() {
    const ledgerSection = getEl('signalhire-ledger-section');
    const tbody = getEl('signalhire-rows');
    const countEl = getEl('signalhire-ledger-count');
    const totalEl = getEl('signalhire-ledger-total');
    const emptyEl = getEl('signalhire-empty');
    if (!tbody || !ledgerSection) return;

    if (lastResults.length === 0) {
      show(ledgerSection, false);
      tbody.innerHTML = '';
      return;
    }

    show(ledgerSection, true);
    if (totalEl) setText(totalEl, String(lastResults.length));

    const query = currentSearchQuery.toLowerCase().trim();
    const filtered = lastResults.filter(item => {
      if (!query) return true;
      const nameMatch = item.name && item.name.toLowerCase().includes(query);
      const titleMatch = item.title && item.title.toLowerCase().includes(query);
      return nameMatch || titleMatch;
    });

    if (countEl) setText(countEl, String(filtered.length));
    tbody.innerHTML = '';

    if (filtered.length === 0) {
      if (emptyEl) show(emptyEl, true);
      return;
    }

    if (emptyEl) show(emptyEl, false);

    filtered.forEach((res, i) => {
      const tr = document.createElement('tr');
      tr.className = 'row-animate';

      // 1. #
      const tdIdx = document.createElement('td');
      tdIdx.className = 'col-num cell-mono';
      tdIdx.textContent = String(i + 1);
      tr.appendChild(tdIdx);

      // 2. Name
      const tdName = document.createElement('td');
      tdName.style.fontWeight = '600';
      tdName.textContent = res.name;
      tr.appendChild(tdName);

      // 3. Job Title
      const tdTitle = document.createElement('td');
      tdTitle.style.color = 'var(--muted)';
      tdTitle.textContent = res.title || 'Employee';
      tr.appendChild(tdTitle);

      // 4. Source
      const tdSrc = document.createElement('td');
      tdSrc.innerHTML = '<span class="legend__chip" style="--prov: var(--p-web); --prov-wash: var(--p-web-wash);">SignalHire</span>';
      tr.appendChild(tdSrc);

      tbody.appendChild(tr);
    });
  }

  async function loadHistoricalResults(item) {
    try {
      const response = await fetch(`/api/signalhire/history/${encodeURIComponent(item.id)}/results`);
      if (!response.ok) throw new Error('Failed to load historical SignalHire results');
      const data = await response.json();
      const results = data.results || [];
      lastResults = results;

      const railOl = getEl('signalhire-rail');
      if (railOl) railOl.innerHTML = '';
      railCount = 0;

      renderRail(results);
      renderLedger();

      const ledgerSection = getEl('signalhire-ledger-section');
      if (ledgerSection) {
        show(ledgerSection, true);
        ledgerSection.scrollIntoView({ behavior: 'smooth' });
      }
      setExportEnabled(results.length > 0);
    } catch (err) {
      console.error('[SignalHire] Load history error:', err);
      const errorEl = getEl('signalhire-error');
      if (errorEl) {
        setText(errorEl, err.message || 'Failed to load historical results');
        show(errorEl, true);
      }
    }
  }

  async function submitCrawl(event) {
    event.preventDefault();

    const inputEl = getEl('signalhire-company');
    const errorEl = getEl('signalhire-error');
    const btn = getEl('signalhire-btn');
    const btnSpan = btn ? btn.querySelector('span') : null;
    const railOl = getEl('signalhire-rail');
    const railSection = getEl('signalhire-rail-section');
    const ledgerSection = getEl('signalhire-ledger-section');

    show(errorEl, false);

    const companyVal = inputEl ? inputEl.value.trim() : '';
    if (!companyVal) {
      setText(errorEl, 'Please enter a company name, domain, or SignalHire URL.');
      show(errorEl, true);
      return;
    }

    try {
      railCount = 0;
      lastResults = [];
      setExportEnabled(false);
      if (btn) btn.disabled = true;
      if (btnSpan) setText(btnSpan, 'Searching...');
      show(ledgerSection, false);
      show(railSection, false);
      if (railOl) railOl.innerHTML = '';

      const progressSection = getEl('signalhire-progress');
      const countFoundSpan = getEl('signalhire-count-found');
      const fillBar = getEl('signalhire-fill');
      const phaseSpan = getEl('signalhire-phase');

      if (progressSection) {
        show(progressSection, true);
        setText(countFoundSpan, '0');
        setText(phaseSpan, 'Searching');
        if (fillBar) fillBar.style.width = '100%';
      }

      const onProgress = (accumulatedResults, isDone, totalCount) => {
        lastResults = accumulatedResults;

        if (countFoundSpan) {
          setText(countFoundSpan, String(accumulatedResults.length));
        }

        renderRail(accumulatedResults);
        renderLedger();

        if (isDone && progressSection) {
          setText(phaseSpan, 'Completed');
        }
      };

      const response = await API.crawlSignalHire(companyVal, onProgress);
      const results = response.results || lastResults;
      lastResults = results;

      if (results.length === 0) {
        setText(errorEl, `No employees found for "${companyVal}".`);
        show(errorEl, true);
        if (progressSection) show(progressSection, false);
      } else {
        renderRail(results);
        renderLedger();
        setExportEnabled(true);
      }
    } catch (err) {
      setText(errorEl, err.message || 'SignalHire search failed');
      show(errorEl, true);
      const progressSection = getEl('signalhire-progress');
      if (progressSection) show(progressSection, false);
    } finally {
      if (btn) btn.disabled = false;
      if (btnSpan) setText(btnSpan, 'Find Employees');
      await loadSignalHireHistory();
    }
  }

  let historyPollTimer = null;

  async function loadSignalHireHistory() {
    const tablewrap = getEl('signalhire-history-tablewrap');
    const rowsEl = getEl('signalhire-history-rows');
    const emptyEl = getEl('signalhire-history-empty');
    const noteEl = getEl('signalhire-history-note');
    if (!rowsEl) return;

    try {
      const response = await fetch('/api/signalhire/history');
      if (!response.ok) return;
      const data = await response.json();

      rowsEl.innerHTML = '';
      if (!data || data.length === 0) {
        if (tablewrap) show(tablewrap, false);
        show(emptyEl, true);
        if (noteEl) setText(noteEl, '');
        return;
      }

      if (tablewrap) show(tablewrap, true);
      show(emptyEl, false);
      if (noteEl) setText(noteEl, `${data.length} search(es) logged`);

      let hasRunning = false;

      data.forEach(item => {
        const tr = document.createElement('tr');
        const timestamp = new Date(item.created_at).toLocaleString();

        // 1. Started
        const tdDate = document.createElement('td');
        tdDate.className = 'cell-mono';
        setText(tdDate, timestamp);
        tr.appendChild(tdDate);

        // 2. Company / Input
        const tdComp = document.createElement('td');
        tdComp.style.fontWeight = '500';
        setText(tdComp, item.company_input);
        tr.appendChild(tdComp);

        // 3. Status
        const tdStatus = document.createElement('td');
        const st = (item.status || 'completed').toLowerCase();
        if (st === 'running') {
          hasRunning = true;
          tdStatus.innerHTML = '<span class="statusdot" data-state="running"></span> Running...';
        } else if (st === 'failed') {
          tdStatus.innerHTML = '<span class="statusdot" data-state="error"></span> Failed';
        } else {
          tdStatus.innerHTML = '<span class="statusdot" data-state="done"></span> Completed';
        }
        tr.appendChild(tdStatus);

        // 4. Employees Found
        const tdCount = document.createElement('td');
        tdCount.className = 'col-num cell-mono';
        setText(tdCount, String(item.total_employees));
        tr.appendChild(tdCount);

        // 5. Actions
        const tdActions = document.createElement('td');
        tdActions.style.textAlign = 'right';

        const wrap = document.createElement('div');
        wrap.className = 'history__actions';
        wrap.style.display = 'inline-flex';
        wrap.style.gap = '0.4rem';
        wrap.style.justifyContent = 'flex-end';

        if (item.has_results || item.total_employees > 0 || st === 'running') {
          const loadBtn = document.createElement('button');
          loadBtn.type = 'button';
          loadBtn.className = 'btn btn--ghost btn--tiny';
          loadBtn.textContent = 'View';
          loadBtn.title = 'View results in table';
          loadBtn.addEventListener('click', () => loadHistoricalResults(item));
          wrap.appendChild(loadBtn);

          const csvLink = document.createElement('a');
          csvLink.href = `/api/signalhire/history/${encodeURIComponent(item.id)}/export.csv`;
          csvLink.className = 'btn btn--ghost btn--tiny';
          csvLink.textContent = 'CSV';
          csvLink.download = `signalhire_${item.company_input}.csv`;
          wrap.appendChild(csvLink);

          const xlsxLink = document.createElement('a');
          xlsxLink.href = `/api/signalhire/history/${encodeURIComponent(item.id)}/export.xlsx`;
          xlsxLink.className = 'btn btn--ghost btn--tiny';
          xlsxLink.textContent = 'Excel';
          xlsxLink.download = `signalhire_${item.company_input}.xlsx`;
          wrap.appendChild(xlsxLink);
        }

        tdActions.appendChild(wrap);
        tr.appendChild(tdActions);

        rowsEl.appendChild(tr);
      });

      if (hasRunning) {
        if (historyPollTimer) clearTimeout(historyPollTimer);
        historyPollTimer = setTimeout(loadSignalHireHistory, 3000);
      }
    } catch (err) {
      console.error('Failed to load SignalHire history', err);
    }
  }

  function init() {
    const form = getEl('signalhire-form');
    if (form) {
      form.addEventListener('submit', submitCrawl);
    }

    const searchInput = getEl('signalhire-search');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        currentSearchQuery = e.target.value;
        renderLedger();
      });
    }

    const exportCsv = getEl('signalhire-export-csv');
    if (exportCsv) exportCsv.addEventListener('click', () => downloadExport('csv'));

    const exportXlsx = getEl('signalhire-export-xlsx');
    if (exportXlsx) exportXlsx.addEventListener('click', () => downloadExport('xlsx'));

    const liveCsv = getEl('signalhire-live-csv');
    if (liveCsv) liveCsv.addEventListener('click', () => downloadExport('csv'));

    const liveXlsx = getEl('signalhire-live-xlsx');
    if (liveXlsx) liveXlsx.addEventListener('click', () => downloadExport('xlsx'));

    loadSignalHireHistory();
  }

  return { init };
})();

document.addEventListener('DOMContentLoaded', SignalHire.init);
