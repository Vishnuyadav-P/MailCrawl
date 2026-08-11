/* ==========================================================================
   SignalHire Employee Crawler.
   ========================================================================== */

const SignalHire = (() => {
  let lastResults = [];
  let renderedCount = 0;

  async function downloadExport(format) {
    if (!lastResults.length) {
      const errorEl = $('signalhire-error');
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
      const errorEl = $('signalhire-error');
      if (errorEl) {
        setText(errorEl, err.message || 'Export failed');
        show(errorEl, true);
      }
    }
  }

  function setExportEnabled(enabled) {
    const btnCsv = $('signalhire-export-csv');
    const btnXlsx = $('signalhire-export-xlsx');
    if (btnCsv) btnCsv.disabled = !enabled;
    if (btnXlsx) btnXlsx.disabled = !enabled;
  }

  function updateLiveFeed(results) {
    const liveSection = $('signalhire-live-feed-section');
    const feedList = $('signalhire-live-feed');
    const countSpan = $('signalhire-live-count');
    if (!feedList || !liveSection) return;

    if (results.length === 0) {
      show(liveSection, false);
      feedList.innerHTML = '';
      return;
    }

    show(liveSection, true);
    if (countSpan) setText(countSpan, `${results.length} extracted so far`);

    // Get recent 10 items (newest first)
    const recent10 = results.slice(-10).reverse();
    feedList.innerHTML = '';

    recent10.forEach((item, idx) => {
      const li = document.createElement('li');
      li.className = 'sh-live-item row-animate';
      
      // Left side: badge + name
      const leftDiv = document.createElement('div');
      leftDiv.className = 'sh-live-left';

      const badge = document.createElement('span');
      badge.className = idx === 0 ? 'sh-live-badge sh-live-badge--new' : 'sh-live-badge';
      badge.textContent = idx === 0 ? 'NEW' : `REC-${String(idx + 1).padStart(2, '0')}`;
      leftDiv.appendChild(badge);

      const nameSpan = document.createElement('span');
      nameSpan.className = 'sh-live-name';
      setText(nameSpan, item.name);
      leftDiv.appendChild(nameSpan);

      li.appendChild(leftDiv);

      // Right side: job title pill badge
      const titleSpan = document.createElement('span');
      titleSpan.className = 'sh-live-title';
      titleSpan.title = item.title || 'Employee';
      setText(titleSpan, item.title || 'Employee');
      li.appendChild(titleSpan);

      feedList.appendChild(li);
    });
  }

  function renderResults(results) {
    const tbody = $('signalhire-rows');
    const countEl = $('signalhire-results-count');
    if (countEl) setText(countEl, `${results.length} total profiles extracted`);
    if (!tbody) return;
    
    for (let i = renderedCount; i < results.length; i++) {
      const res = results[i];
      const tr = document.createElement('tr');
      tr.className = 'row-animate';
      tr.style.animationDelay = `${(i % 10) * 40}ms`;
      
      // 1. #
      const tdIdx = document.createElement('td');
      tdIdx.className = 'col-num cell-mono';
      setText(tdIdx, String(i + 1));
      tr.appendChild(tdIdx);
      
      // 2. Name
      const tdName = document.createElement('td');
      tdName.style.fontWeight = '600';
      setText(tdName, res.name);
      tr.appendChild(tdName);
      
      // 3. Job Title
      const tdTitle = document.createElement('td');
      tdTitle.style.color = 'var(--muted)';
      setText(tdTitle, res.title || 'Employee');
      tr.appendChild(tdTitle);
      
      tbody.appendChild(tr);
    }
    renderedCount = results.length;
  }

  async function loadHistoricalResults(item) {
    try {
      const response = await fetch(`/api/signalhire/history/${encodeURIComponent(item.id)}/results`);
      if (!response.ok) throw new Error('Failed to load historical SignalHire results');
      const data = await response.json();
      const results = data.results || [];
      lastResults = results;
      
      const resultsContainer = $('signalhire-results');
      const tbody = $('signalhire-rows');
      if (tbody) tbody.innerHTML = '';
      renderedCount = 0;
      
      renderResults(results);
      updateLiveFeed(results);
      if (resultsContainer) {
        show(resultsContainer, true);
        resultsContainer.scrollIntoView({ behavior: 'smooth' });
      }
      setExportEnabled(true);
    } catch (err) {
      console.error('[SignalHire] Load history error:', err);
      const errorEl = $('signalhire-error');
      if (errorEl) {
        setText(errorEl, err.message || 'Failed to load historical results');
        show(errorEl, true);
      }
    }
  }

  async function submitCrawl(event) {
    event.preventDefault();
    
    const inputEl = $('signalhire-company');
    const errorEl = $('signalhire-error');
    const btn = $('signalhire-btn');
    const btnSpan = btn.querySelector('span');
    const resultsContainer = $('signalhire-results');
    const tbody = $('signalhire-rows');
    const liveSection = $('signalhire-live-feed-section');
    
    show(errorEl, false);
    
    const companyVal = inputEl ? inputEl.value.trim() : '';
    if (!companyVal) {
      setText(errorEl, 'Please enter a company name, domain, or SignalHire URL.');
      show(errorEl, true);
      return;
    }

    try {
      renderedCount = 0;
      lastResults = [];
      setExportEnabled(false);
      btn.disabled = true;
      setText(btnSpan, 'Searching...');
      show(resultsContainer, false);
      if (liveSection) show(liveSection, false);
      if (tbody) tbody.innerHTML = '';
      
      const progressSection = $('signalhire-progress');
      const validatedSpan = $('signalhire-count-found');
      const totalSpan = $('signalhire-count-total');
      const fillBar = $('signalhire-fill');
      const phaseSpan = $('signalhire-phase');
      
      if (progressSection) {
        show(progressSection, true);
        setText(validatedSpan, '0');
        setText(totalSpan, '0');
        setText(phaseSpan, 'Searching');
        fillBar.style.width = '0%';
      }
      
      const onProgress = (accumulatedResults, isDone, totalCount) => {
        lastResults = accumulatedResults;
        
        if (totalCount !== undefined && progressSection) {
           setText(totalSpan, String(totalCount));
           const val = accumulatedResults.length;
           setText(validatedSpan, String(val));
           if (totalCount > 0) {
              const pct = Math.min(100, Math.round((val / totalCount) * 100));
              fillBar.style.width = pct + '%';
           }
        }
        
        if (accumulatedResults.length > 0) {
          show(resultsContainer, true);
        }
        renderResults(accumulatedResults);
        updateLiveFeed(accumulatedResults);
        
        if (isDone && progressSection) {
           setText(phaseSpan, 'Done');
           fillBar.style.width = '100%';
        }
      };
      
      const response = await API.crawlSignalHire(companyVal, onProgress);
      const results = response.results || [];
      lastResults = results;
      
      if (results.length === 0) {
        setText(errorEl, `No employees found for "${companyVal}".`);
        show(errorEl, true);
        if (progressSection) show(progressSection, false);
        if (liveSection) show(liveSection, false);
      } else {
        renderResults(results);
        updateLiveFeed(results);
        show(resultsContainer, true);
        setExportEnabled(true);
      }
    } catch (err) {
      setText(errorEl, err.message || 'SignalHire search failed');
      show(errorEl, true);
      const progressSection = $('signalhire-progress');
      if (progressSection) show(progressSection, false);
      if (liveSection) show(liveSection, false);
    } finally {
      btn.disabled = false;
      setText(btnSpan, 'Find Employees');
    }
  }

  async function loadSignalHireHistory() {
    const tablewrap = $('signalhire-history-tablewrap');
    const rowsEl = $('signalhire-history-rows');
    const emptyEl = $('signalhire-history-empty');
    const noteEl = $('signalhire-history-note');
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
      
      data.forEach(item => {
        const tr = document.createElement('tr');
        
        const timestamp = new Date(item.created_at).toLocaleString();
        
        // 1. Date & Time
        const tdDate = document.createElement('td');
        tdDate.className = 'cell-mono';
        setText(tdDate, timestamp);
        tr.appendChild(tdDate);
        
        // 2. Company Query
        const tdComp = document.createElement('td');
        tdComp.style.fontWeight = '500';
        setText(tdComp, item.company_input);
        tr.appendChild(tdComp);
        
        // 3. Employees Found
        const tdCount = document.createElement('td');
        tdCount.className = 'col-num cell-mono';
        setText(tdCount, String(item.total_employees));
        tr.appendChild(tdCount);
        
        // 4. Actions
        const tdActions = document.createElement('td');
        tdActions.style.textAlign = 'right';
        
        const wrap = document.createElement('div');
        wrap.className = 'history__actions';
        wrap.style.display = 'inline-flex';
        wrap.style.gap = '0.4rem';
        wrap.style.justifyContent = 'flex-end';
        
        if (item.has_results) {
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
    } catch (err) {
      console.error('Failed to load SignalHire history', err);
    }
  }

  function init() {
    const form = $('signalhire-form');
    if (form) {
      form.addEventListener('submit', async (e) => {
        await submitCrawl(e);
        await loadSignalHireHistory();
      });
    }
    
    const btnCsv = $('signalhire-export-csv');
    if (btnCsv) {
      btnCsv.disabled = true;
      btnCsv.addEventListener('click', () => downloadExport('csv'));
    }
    
    const btnXlsx = $('signalhire-export-xlsx');
    if (btnXlsx) {
      btnXlsx.disabled = true;
      btnXlsx.addEventListener('click', () => downloadExport('xlsx'));
    }
    
    loadSignalHireHistory();
  }

  return { init };
})();

document.addEventListener('DOMContentLoaded', SignalHire.init);
