/* ==========================================================================
   Summary tiles, filters, results table and exports.

   Filtering is a server round-trip. That is deliberate: the same predicate then
   serves the table and the CSV/XLSX exports, so what is on screen and what gets
   downloaded cannot drift apart.
   ========================================================================== */

const Ledger = (() => {

  let debounceTimer = null;

  /* ---------- Summary tiles ---------- */

  const TILES = [
    { label: 'Sites on domain',  get: (s) => s.sites_discovered,        accent: 'violet' },
    { label: 'Pages scanned',    get: (s) => s.pages_scanned, sub: (s) => `/ ${s.pages_discovered}` },
    { label: 'Raw occurrences',  get: (s) => s.raw_email_occurrences },
    { label: 'Unique addresses', get: (s) => s.unique_emails,           accent: 'violet' },
    { label: 'Target domain',    get: (s) => s.target_domain_emails,    accent: 'teal' },
    { label: 'Duration',         get: (s) => formatDuration(s.scan_duration_seconds) },
    { label: 'PDFs parsed',      get: (s) => s.pdf_files },
    { label: 'External sources', get: (s) => s.external_sources_scanned },
    { label: 'Declared records', get: (s) => s.wellknown_emails_found,  accent: 'teal' },
    { label: 'HTML pages',       get: (s) => s.html_pages },
    // Only tinted when there is something to look at; amber on a zero would
    // read as a warning where none exists.
    { label: 'Errors logged',    get: (s) => s.error_count,
      accent: (s) => (s.error_count > 0 ? 'amber' : null) },
  ];

  function renderTiles(stats) {
    const host = $('tiles');
    if (!host || !stats) return;

    host.replaceChildren();

    for (const spec of TILES) {
      const tile = document.createElement('div');
      tile.className = 'tile';
      const accent = typeof spec.accent === 'function' ? spec.accent(stats) : spec.accent;
      if (accent) tile.dataset.accent = accent;

      const label = document.createElement('span');
      label.className = 'tile__label';
      label.textContent = spec.label;

      const value = document.createElement('span');
      value.className = 'tile__value';
      value.textContent = spec.get(stats) ?? 0;

      if (spec.sub) {
        const sub = document.createElement('small');
        sub.textContent = ` ${spec.sub(stats)}`;
        value.appendChild(sub);
      }

      tile.append(label, value);
      host.appendChild(tile);
    }

    show($('summary'), true);

    // Only shown when rules were actually bypassed, so it stays meaningful.
    const note = $('robots-ignored-note');
    if (stats.robots_rules_ignored > 0) {
      note.textContent =
        `${stats.robots_rules_ignored} URL(s) were crawled despite a robots.txt Disallow rule, ` +
        `because robots.txt enforcement was off for this scan.`;
      show(note, true);
    } else {
      show(note, false);
    }
  }

  /* ---------- Filters ---------- */

  function fillFacets(facets) {
    const map = {
      'f-type': { values: facets.email_type || [], any: 'Any type' },
      'f-validation': { values: facets.validation_status || [], any: 'Any validation' },
      'f-domain': { values: facets.domain_match || [], any: 'Any domain match' },
    };

    for (const [id, spec] of Object.entries(map)) {
      const select = $(id);
      if (!select) continue;
      const current = select.value;

      select.replaceChildren();
      const anyOption = document.createElement('option');
      anyOption.value = '';
      anyOption.textContent = spec.any;
      select.appendChild(anyOption);

      for (const value of spec.values) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }

      // Facets come from the unfiltered set, so a live selection stays valid.
      if (current && spec.values.includes(current)) select.value = current;
    }
  }

  function readFilters() {
    return {
      q: $('f-q').value.trim(),
      email_type: $('f-type').value,
      validation_status: $('f-validation').value,
      domain_match: $('f-domain').value,
      min_confidence: Number($('f-conf').value) || 0,
    };
  }

  /* ---------- Results table ---------- */

  function renderRows(rows) {
    const body = $('ledger-rows');
    body.replaceChildren();

    for (const row of rows) {
      const tr = document.createElement('tr');

      tr.appendChild(sigilCell(row.source_type));

      const emailCell = cell(row.email, 'cell-mono');
      tr.appendChild(emailCell);

      tr.appendChild(cell(row.name));
      tr.appendChild(cell(row.role));
      tr.appendChild(cell(row.department));
      tr.appendChild(cell(row.email_type));
      tr.appendChild(cell(row.purpose));
      tr.appendChild(cell(row.domain, 'cell-mono'));

      const seen = cell(row.occurrences);
      seen.className = 'col-num';
      tr.appendChild(seen);

      tr.appendChild(pillCell(row.validation_status, validationTone(row.validation_status)));
      tr.appendChild(pillCell(row.domain_match, domainMatchTone(row.domain_match)));
      tr.appendChild(confidenceCell(row.confidence));
      tr.appendChild(linkCell(row.source_url));

      body.appendChild(tr);
    }

    show($('ledger-empty'), rows.length === 0);
  }

  function renderErrors(errors) {
    const block = $('errors-block');
    if (!errors || !errors.length) {
      show(block, false);
      return;
    }

    setText($('errors-count'), errors.length);
    const body = $('errors-rows');
    body.replaceChildren();

    for (const err of errors) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(err.error_type));
      tr.appendChild(cell(err.status_code));
      tr.appendChild(linkCell(err.url));
      tr.appendChild(cell(err.message));
      body.appendChild(tr);
    }

    show(block, true);
  }

  function updateExportLinks() {
    if (!State.scanId) return;
    $('export-csv').href = API.exportUrl(State.scanId, 'csv', State.filters);
    $('export-xlsx').href = API.exportUrl(State.scanId, 'xlsx', State.filters);
  }

  function updatePager() {
    const { offset, limit, filteredTotal } = State.page;
    const pager = $('pager');

    if (filteredTotal <= limit) {
      show(pager, false);
      return;
    }

    const from = filteredTotal === 0 ? 0 : offset + 1;
    const to = Math.min(offset + limit, filteredTotal);
    setText($('pager-at'), `${from}–${to} of ${filteredTotal}`);

    $('page-prev').disabled = offset <= 0;
    $('page-next').disabled = offset + limit >= filteredTotal;
    show(pager, true);
  }

  /* ---------- Loading ---------- */

  async function load() {
    if (!State.scanId) return;

    try {
      const data = await API.results(
        State.scanId, State.filters, State.page.offset, State.page.limit
      );

      State.page.total = data.total;
      State.page.filteredTotal = data.filtered_total;
      State.facets = data.facets;
      if (data.stats) State.stats = data.stats;
      if (data.config) State.config = data.config;

      fillFacets(data.facets);
      renderRows(data.results);
      renderErrors(data.errors);
      if (data.stats) renderTiles(data.stats);

      setText($('ledger-count'), data.filtered_total);
      setText($('ledger-total'), data.total);
      updatePager();
      updateExportLinks();
      show($('ledger'), true);

    } catch (err) {
      if (err.status === 409) return;      // scan still running; results not ready
      toast(err.message, 'error');
    }
  }

  function onFilterChange() {
    State.filters = readFilters();
    State.page.offset = 0;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(load, 200);
  }

  function bind() {
    ['f-type', 'f-validation', 'f-domain'].forEach((id) => {
      $(id).addEventListener('change', onFilterChange);
    });

    $('f-q').addEventListener('input', onFilterChange);

    $('f-conf').addEventListener('input', (e) => {
      setText($('f-conf-out'), e.target.value);
      onFilterChange();
    });

    $('page-prev').addEventListener('click', () => {
      State.page.offset = Math.max(0, State.page.offset - State.page.limit);
      load();
    });

    $('page-next').addEventListener('click', () => {
      State.page.offset += State.page.limit;
      load();
    });
  }

  return { bind, load, renderTiles };
})();
