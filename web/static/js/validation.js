/* ==========================================================================
   Batch Validation.
   ========================================================================== */

const Validation = (() => {
  let lastResults = [];

  async function downloadExport(format) {
    if (!lastResults.length) return;
    
    try {
      const response = await fetch('/api/validate_export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: lastResults, format })
      });
      
      if (!response.ok) {
        throw new Error('Failed to export');
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
      const errorEl = $('validate-error');
      setText(errorEl, err.message || 'Export failed');
      show(errorEl, true);
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
      btn.disabled = true;
      setText(btnSpan, 'Validating...');
      show(resultsContainer, false);
      
      const response = await API.validateFile(file);
      const results = response.results || [];
      lastResults = results;
      
      tbody.innerHTML = '';
      if (results.length === 0) {
        setText(errorEl, 'No emails found in the file.');
        show(errorEl, true);
      } else {
        results.forEach((res, index) => {
          const tr = document.createElement('tr');
          
          const tdIdx = document.createElement('td');
          tdIdx.className = 'col-num';
          setText(tdIdx, String(index + 1));
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
          
          const tdDisp = document.createElement('td');
          setText(tdDisp, res.is_disposable ? 'Yes' : 'No');
          tr.appendChild(tdDisp);
          
          const tdRole = document.createElement('td');
          setText(tdRole, res.is_role_account ? 'Yes' : 'No');
          tr.appendChild(tdRole);
          
          tbody.appendChild(tr);
        });
        
        show(resultsContainer, true);
      }
    } catch (err) {
      setText(errorEl, err.message || 'Validation failed');
      show(errorEl, true);
    } finally {
      btn.disabled = false;
      setText(btnSpan, 'Validate');
    }
  }

  function init() {
    const form = $('validate-form');
    if (form) {
      form.addEventListener('submit', submitValidation);
    }
    
    const btnCsv = $('export-csv');
    if (btnCsv) {
      btnCsv.addEventListener('click', () => downloadExport('csv'));
    }
    
    const btnXlsx = $('export-xlsx');
    if (btnXlsx) {
      btnXlsx.addEventListener('click', () => downloadExport('xlsx'));
    }
  }

  return { init };
})();

document.addEventListener('DOMContentLoaded', Validation.init);
