/* ==========================================================================
   Client state and small shared helpers.

   Live addresses are keyed by address, which is what makes the SSE contract
   safe: a `snapshot` replaces the map wholesale, deltas add to it, and a
   duplicated event after a reconnect changes nothing.
   ========================================================================== */

const State = {
  scanId: null,
  status: 'idle',
  phase: null,
  liveEmails: new Map(),   // address -> row
  liveTruncated: false,
  stats: null,
  config: null,
  facets: {},
  filters: { q: '', email_type: '', validation_status: '', domain_match: '', min_confidence: 0 },
  page: { offset: 0, limit: 200, total: 0, filteredTotal: 0 },

  reset() {
    this.liveEmails.clear();
    this.liveTruncated = false;
    this.stats = null;
    this.phase = null;
    this.page.offset = 0;
  },

  addEmails(rows) {
    const added = [];
    for (const row of rows) {
      if (!this.liveEmails.has(row.email)) {
        this.liveEmails.set(row.email, row);
        added.push(row);
      }
    }
    return added;
  },

  get isRunning() {
    return this.status === 'running' || this.status === 'queued' || this.status === 'cancelling';
  },
};

/* ---------- DOM helpers ---------- */

const $ = (id) => document.getElementById(id);

function show(el, visible = true) {
  if (el) el.hidden = !visible;
}

function setText(el, value) {
  if (el) el.textContent = value;
}

/* Always build cells with textContent rather than innerHTML: every value here
   is crawled from third-party pages and must never be parsed as markup. */
function cell(text, className) {
  const td = document.createElement('td');
  if (className) td.className = className;
  td.textContent = text == null || text === '' ? '—' : String(text);
  if (text == null || text === '') td.classList.add('cell-empty');
  return td;
}

function linkCell(url, className = 'cell-url') {
  const td = document.createElement('td');
  if (!url) {
    td.textContent = '—';
    td.classList.add('cell-empty');
    return td;
  }
  const a = document.createElement('a');
  a.className = className;
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.textContent = url.replace(/^https?:\/\//, '');
  a.title = url;
  td.appendChild(a);
  return td;
}

function pillCell(text, tone) {
  const td = document.createElement('td');
  if (!text) {
    td.textContent = '—';
    td.classList.add('cell-empty');
    return td;
  }
  const span = document.createElement('span');
  span.className = 'pill';
  span.dataset.tone = tone;
  span.textContent = text;
  td.appendChild(span);
  return td;
}

function sigilCell(sourceType) {
  const prov = provenanceOf(sourceType);
  const td = document.createElement('td');
  td.dataset.prov = prov.cls;
  const span = document.createElement('span');
  span.className = 'sigil';
  span.textContent = prov.sigil;
  span.title = prov.label;
  td.appendChild(span);
  return td;
}

function confidenceCell(value) {
  const td = document.createElement('td');
  td.className = 'col-num';
  const wrap = document.createElement('span');
  wrap.className = 'conf';

  const bar = document.createElement('span');
  bar.className = 'conf__bar';
  const fill = document.createElement('span');
  fill.className = 'conf__fill';
  fill.style.width = `${Math.max(0, Math.min(100, value || 0))}%`;
  bar.appendChild(fill);

  const num = document.createElement('span');
  num.textContent = `${value ?? 0}`;

  wrap.append(bar, num);
  td.appendChild(wrap);
  return td;
}

function formatDuration(seconds) {
  if (seconds == null) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${mins}m ${rest}s`;
}

let toastTimer = null;
function toast(message, tone = 'info', ms = 4200) {
  const el = $('toast');
  if (!el) return;
  el.textContent = message;
  el.dataset.tone = tone;
  el.hidden = false;
  el.style.animation = 'none';
  void el.offsetWidth;          // restart the entry animation
  el.style.animation = '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}
