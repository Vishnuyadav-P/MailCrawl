/* ==========================================================================
   API client.
   ========================================================================== */

const API = (() => {

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });

    if (response.status === 204) return null;

    let body = null;
    try {
      body = await response.json();
    } catch {
      // Non-JSON error pages still need to surface something useful.
    }

    if (!response.ok) {
      const detail = body && body.detail
        ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail))
        : `Request failed (HTTP ${response.status})`;
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }

    return body;
  }

  /* Filter inputs -> query string. The same spec drives the table and the
     export links, so what is on screen is what gets downloaded. */
  function filterParams(filters = {}) {
    const params = new URLSearchParams();
    if (filters.q) params.set('q', filters.q);
    if (filters.email_type) params.set('email_type', filters.email_type);
    if (filters.validation_status) params.set('validation_status', filters.validation_status);
    if (filters.domain_match) params.set('domain_match', filters.domain_match);
    if (filters.min_confidence) params.set('min_confidence', String(filters.min_confidence));
    return params;
  }

  return {
    health: () => request('/api/health'),

    startScan: (payload) => request('/api/scans', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

    resumeScan: (scanId, revealEvery) => request(`/api/scans/${encodeURIComponent(scanId)}/resume`, {
      method: 'POST',
      body: JSON.stringify({ reveal_every: revealEvery }),
    }),

    cancelScan: (scanId) => request(`/api/scans/${encodeURIComponent(scanId)}/cancel`, {
      method: 'POST',
    }),

    snapshot: (scanId) => request(`/api/scans/${encodeURIComponent(scanId)}`),

    results: (scanId, filters = {}, offset = 0, limit = 10) => {
      const params = filterParams(filters);
      params.set('offset', String(offset));
      params.set('limit', String(limit));
      return request(`/api/scans/${encodeURIComponent(scanId)}/results?${params}`);
    },

    exportUrl: (scanId, format, filters = {}) => {
      const params = filterParams(filters);
      const query = params.toString();
      return `/api/scans/${encodeURIComponent(scanId)}/export.${format}${query ? '?' + query : ''}`;
    },

    history: () => request('/api/history'),

    deleteScanData: (scanId) => request(`/api/scans/${encodeURIComponent(scanId)}/data`, {
      method: 'DELETE',
    }),

    validateFile: async (file, onProgress) => {
      const formData = new FormData();
      formData.append('file', file);
      const response = await fetch('/api/validate_file', {
        method: 'POST',
        body: formData,
      });
      
      if (!response.ok) {
        let body = null;
        try {
          body = await response.json();
        } catch {}
        const detail = body && body.detail ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)) : `Request failed (HTTP ${response.status})`;
        const error = new Error(detail);
        error.status = response.status;
        throw error;
      }

      if (onProgress) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let allResults = [];
        let totalCount = 0;
        
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          
          let lines = buffer.split('\n');
          buffer = lines.pop(); 
          
          for (let line of lines) {
            if (line.trim()) {
              const data = JSON.parse(line);
              if (data.total !== undefined) {
                totalCount = data.total;
                onProgress(allResults, false, totalCount);
              }
              if (data.results) {
                allResults.push(...data.results);
                onProgress(allResults, false, totalCount); 
              }
            }
          }
        }
        
        if (buffer.trim()) {
          const data = JSON.parse(buffer);
          if (data.results) {
             allResults.push(...data.results);
             onProgress(allResults, false, totalCount);
          }
        }
        
        onProgress(allResults, true, totalCount);
        return { results: allResults };
      } else {
         const body = await response.json();
         return body;
      }
    },

    crawlSignalHire: async (company, onProgress) => {
      const response = await fetch('/api/signalhire/crawl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company }),
      });
      
      if (!response.ok) {
        let body = null;
        try { body = await response.json(); } catch {}
        const detail = body && body.detail ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)) : `Request failed (HTTP ${response.status})`;
        const error = new Error(detail);
        error.status = response.status;
        throw error;
      }

      if (onProgress) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let allResults = [];
        let totalCount = 0;
        
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          
          let lines = buffer.split('\n');
          buffer = lines.pop(); 
          
          for (let line of lines) {
            if (line.trim()) {
              const data = JSON.parse(line);
              if (data.total !== undefined) {
                totalCount = data.total;
                onProgress(allResults, false, totalCount);
              }
              if (data.results) {
                allResults.push(...data.results);
                onProgress(allResults, false, totalCount); 
              }
            }
          }
        }
        
        if (buffer.trim()) {
          const data = JSON.parse(buffer);
          if (data.results) {
             allResults.push(...data.results);
             onProgress(allResults, false, totalCount);
          }
        }
        
        onProgress(allResults, true, totalCount);
        return { results: allResults };
      } else {
         const body = await response.json();
         return body;
      }
    }
  };
})();
