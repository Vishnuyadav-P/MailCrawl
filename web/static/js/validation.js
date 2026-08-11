/* ==========================================================================
   Batch Validation.
   ========================================================================== */

const Validation = (() => {
  let lastResults = [];
  let renderedCount = 0;

  async function downloadExport(format) {
    console.log('[Validation] downloadExport called, format:', format, 'results:', lastResults.length);
    
    if (!lastResults.length) {
      const errorEl = $('validate-error');
      if (errorEl) {
        setText(errorEl, 'No validation results to export. Please run a validation first.');
        show(errorEl, true);
      }
      return;
    }
    
    try {
      const response = await fetch('/api/validate_export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: lastResults, format })
      });
      
      if (!response.ok) {
        const errBody = await response.text();
        console.error('[Validation] Export failed:', response.status, errBody);
        throw new Error('Failed to export: ' + response.status);
      }
      
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `validation_results.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('[Validation] Export error:', err);
      const errorEl = $('validate-error');
      if (errorEl) {
        setText(errorEl, err.message || 'Export failed');
        show(errorEl, true);
      }
    }
  }

  function setExportEnabled(enabled) {
    const btnCsv = $('validate-export-csv');
    const btnXlsx = $('validate-export-xlsx');
    if (btnCsv) btnCsv.disabled = !enabled;
    if (btnXlsx) btnXlsx.disabled = !enabled;
  }

  function renderResults(results) {
    const tbody = $('validate-rows');
    if (!tbody) return;
    
    for (let i = renderedCount; i < results.length; i++) {
      const res = results[i];
      const tr = document.createElement('tr');
      tr.className = 'row-animate';
      tr.style.animationDelay = `${(i % 10) * 40}ms`;
      
      const tdIdx = document.createElement('td');
      tdIdx.className = 'col-num';
      setText(tdIdx, String(i + 1));
      tr.appendChild(tdIdx);
      
      const tdOrig = document.createElement('td');
      setText(tdOrig, res.original_email);
      tr.appendChild(tdOrig);
      
      const tdNorm = document.createElement('td');
      setText(tdNorm, res.normalized_email);
      tr.appendChild(tdNorm);
      
      const tdSyntax = document.createElement('td');
      tdSyntax.innerHTML = res.is_valid_syntax 
        ? '<span class="statusdot" data-state="done"></span> Yes' 
        : '<span class="statusdot" data-state="error"></span> No';
      tr.appendChild(tdSyntax);
      
      const tdMx = document.createElement('td');
      tdMx.innerHTML = res.mx_status.toLowerCase() === 'valid'
        ? '<span class="statusdot" data-state="done"></span> Valid'
        : (['invalid', 'absent'].includes(res.mx_status.toLowerCase()) ? '<span class="statusdot" data-state="error"></span> ' + res.mx_status : '<span class="statusdot" data-state="idle"></span> ' + res.mx_status);
      tr.appendChild(tdMx);
      
      const tdMb = document.createElement('td');
      tdMb.innerHTML = res.mailbox_status.toLowerCase() === 'valid'
        ? '<span class="statusdot" data-state="done"></span> Valid'
        : (res.mailbox_status.toLowerCase() === 'invalid' ? '<span class="statusdot" data-state="error"></span> Invalid' : '<span class="statusdot" data-state="idle"></span> ' + res.mailbox_status);
      tr.appendChild(tdMb);
      
      const tdReason = document.createElement('td');
      if (res.mx_status === 'unknown' || res.mailbox_status === 'unknown') {
        setText(tdReason, res.reason || 'Unknown error occurred.');
      } else {
        setText(tdReason, res.reason || '-');
      }
      tr.appendChild(tdReason);
      
      tbody.appendChild(tr);
    }
    renderedCount = results.length;
  }

  async function loadHistoricalResults(item) {
    try {
      const response = await fetch(`/api/validate_history/${encodeURIComponent(item.id)}/results`);
      if (!response.ok) throw new Error('Failed to load historical validation results');
      const data = await response.json();
      const results = data.results || [];
      lastResults = results;
      
      const resultsContainer = $('validate-results');
      const tbody = $('validate-rows');
      if (tbody) tbody.innerHTML = '';
      renderedCount = 0;
      
      renderResults(results);
      if (resultsContainer) {
        show(resultsContainer, true);
        resultsContainer.scrollIntoView({ behavior: 'smooth' });
      }
      setExportEnabled(true);
    } catch (err) {
      console.error('[Validation] Load history error:', err);
      const errorEl = $('validate-error');
      if (errorEl) {
        setText(errorEl, err.message || 'Failed to load historical results');
        show(errorEl, true);
      }
    }
  }

  async function submitValidation(event) {
    event.preventDefault();
    
    const fileInput = $('validation-file');
    const errorEl = $('validate-error');
    const btn = $('validate-btn');
    const btnSpan = btn.querySelector('span');
    const resultsContainer = $('validate-results');
    const tbody = $('validate-rows');
    
    show(errorEl, false);
    
    if (!fileInput.files.length) {
      setText(errorEl, 'Please select a file to validate.');
      show(errorEl, true);
      return;
    }
    
    const file = fileInput.files[0];
    const allowed = ['text/csv', 'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'];
    if (!allowed.includes(file.type) && !file.name.endsWith('.csv') && !file.name.endsWith('.xlsx')) {
      setText(errorEl, 'Only .csv and .xlsx files are supported.');
      show(errorEl, true);
      return;
    }

    try {
      renderedCount = 0;
      lastResults = [];
      setExportEnabled(false);
      btn.disabled = true;
      setText(btnSpan, 'Validating...');
      show(resultsContainer, false);
      if (tbody) tbody.innerHTML = '';
      
      const progressSection = $('validation-progress');
      const validatedSpan = $('emails-validated');
      const totalSpan = $('emails-total');
      const fillBar = $('validation-fill');
      const phaseSpan = $('validation-phase');
      
      if (progressSection) {
        show(progressSection, true);
        setText(validatedSpan, '0');
        setText(totalSpan, '0');
        setText(phaseSpan, 'Validating');
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
        
        if (isDone && progressSection) {
           setText(phaseSpan, 'Done');
           fillBar.style.width = '100%';
        }
      };
      
      const response = await API.validateFile(file, onProgress);
      const results = response.results || [];
      lastResults = results;
      console.log('[Validation] Final results count:', lastResults.length);
      
      if (results.length === 0) {
        setText(errorEl, 'No emails found in the file.');
        show(errorEl, true);
        if (progressSection) show(progressSection, false);
      } else {
        renderResults(results);
        show(resultsContainer, true);
        setExportEnabled(true);
      }
    } catch (err) {
      setText(errorEl, err.message || 'Validation failed');
      show(errorEl, true);
      const progressSection = $('validation-progress');
      if (progressSection) show(progressSection, false);
    } finally {
      btn.disabled = false;
      setText(btnSpan, 'Validate');
    }
  }

  let historyPollTimer = null;

  async function loadValidationHistory() {
    const tablewrap = $('validation-history-tablewrap');
    const rowsEl = $('validation-history-rows');
    const emptyEl = $('validation-history-empty');
    const noteEl = $('validation-history-note');
    if (!rowsEl) return;
    
    try {
      const response = await fetch('/api/validate_history');
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
      if (noteEl) setText(noteEl, `${data.length} validation run(s) logged`);
      
      let hasRunning = false;

      data.forEach(item => {
        const tr = document.createElement('tr');
        const timestamp = new Date(item.created_at).toLocaleString();
        
        // 1. Started
        const tdDate = document.createElement('td');
        tdDate.className = 'cell-mono';
        setText(tdDate, timestamp);
        tr.appendChild(tdDate);
        
        // 2. File Name
        const tdFile = document.createElement('td');
        tdFile.style.fontWeight = '500';
        setText(tdFile, item.filename);
        tr.appendChild(tdFile);
        
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

        // 4. Total Emails
        const tdCount = document.createElement('td');
        tdCount.className = 'col-num cell-mono';
        setText(tdCount, String(item.total_emails));
        tr.appendChild(tdCount);
        
        // 5. Actions
        const tdActions = document.createElement('td');
        tdActions.style.textAlign = 'right';
        
        const wrap = document.createElement('div');
        wrap.className = 'history__actions';
        wrap.style.display = 'inline-flex';
        wrap.style.gap = '0.4rem';
        wrap.style.justifyContent = 'flex-end';
        
        if (item.has_results || item.total_emails > 0 || st === 'running') {
          const loadBtn = document.createElement('button');
          loadBtn.type = 'button';
          loadBtn.className = 'btn btn--ghost btn--tiny';
          loadBtn.textContent = 'View';
          loadBtn.title = 'View results in table';
          loadBtn.addEventListener('click', () => loadHistoricalResults(item));
          wrap.appendChild(loadBtn);

          const csvLink = document.createElement('a');
          csvLink.href = `/api/validate_history/${encodeURIComponent(item.id)}/export.csv`;
          csvLink.className = 'btn btn--ghost btn--tiny';
          csvLink.textContent = 'CSV';
          csvLink.download = `validation_${item.filename}.csv`;
          wrap.appendChild(csvLink);

          const xlsxLink = document.createElement('a');
          xlsxLink.href = `/api/validate_history/${encodeURIComponent(item.id)}/export.xlsx`;
          xlsxLink.className = 'btn btn--ghost btn--tiny';
          xlsxLink.textContent = 'Excel';
          xlsxLink.download = `validation_${item.filename}.xlsx`;
          wrap.appendChild(xlsxLink);
        }
        
        tdActions.appendChild(wrap);
        tr.appendChild(tdActions);
        
        rowsEl.appendChild(tr);
      });

      if (hasRunning) {
        if (historyPollTimer) clearTimeout(historyPollTimer);
        historyPollTimer = setTimeout(loadValidationHistory, 3000);
      }
    } catch (err) {
      console.error('Failed to load validation history', err);
    }
  }

  function init() {
    console.log('[Validation] init() called');
    
    const form = $('validate-form');
    if (form) {
      form.addEventListener('submit', async (e) => {
        await submitValidation(e);
        await loadValidationHistory();
      });
    }
    
    const btnCsv = $('validate-export-csv');
    console.log('[Validation] validate-export-csv button found:', !!btnCsv);
    if (btnCsv) {
      btnCsv.disabled = true;
      btnCsv.addEventListener('click', () => downloadExport('csv'));
    }
    
    const btnXlsx = $('validate-export-xlsx');
    console.log('[Validation] validate-export-xlsx button found:', !!btnXlsx);
    if (btnXlsx) {
      btnXlsx.disabled = true;
      btnXlsx.addEventListener('click', () => downloadExport('xlsx'));
    }
    
    loadValidationHistory();
  }

  return { init };
})();

document.addEventListener('DOMContentLoaded', Validation.init);

