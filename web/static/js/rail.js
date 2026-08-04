/* ==========================================================================
   The provenance rail.

   Rows are appended, never re-rendered, so a long-running scan does not rebuild
   the list on every batch. Rows within one batch are given a staggered index so
   they cascade rather than appearing all at once.
   ========================================================================== */

const Rail = (() => {

  const MAX_RENDERED = 400;   // newest-first; the full set lives in the ledger

  function whereLabel(row) {
    const prov = provenanceOf(row.source_type);

    // Records have no meaningful page title, so name the record instead.
    if (prov.cls === 'declared') return row.source_title || prov.label;

    if (row.source_url) {
      try {
        const url = new URL(row.source_url);
        const path = url.pathname === '/' ? url.hostname : url.pathname;
        return path.length > 48 ? path.slice(0, 47) + '…' : path;
      } catch {
        return row.source_url;
      }
    }
    return prov.label;
  }

  function buildRow(row, index) {
    const prov = provenanceOf(row.source_type);

    const li = document.createElement('li');
    li.className = 'rail__row is-new';
    li.dataset.prov = prov.cls;
    li.style.setProperty('--i', String(index));

    const sigil = document.createElement('span');
    sigil.className = 'rail__sigil';
    sigil.textContent = prov.sigil;
    sigil.title = prov.label;

    const email = document.createElement('span');
    email.className = 'rail__email';
    email.textContent = row.email;

    const where = document.createElement('span');
    where.className = 'rail__where';
    if (row.source_url && prov.cls !== 'declared') {
      const a = document.createElement('a');
      a.href = row.source_url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = whereLabel(row);
      a.title = row.source_url;
      where.appendChild(a);
    } else {
      where.textContent = whereLabel(row);
      where.title = row.source_title || prov.label;
    }

    li.append(sigil, email, where);

    // Drop the marker once the arrival animation has run so a later reflow
    // does not replay it.
    li.addEventListener('animationend', () => li.classList.remove('is-new'), { once: true });

    return li;
  }

  function append(rows) {
    if (!rows.length) return;

    const rail = $('rail');
    const fragment = document.createDocumentFragment();

    rows.forEach((row, i) => fragment.appendChild(buildRow(row, i)));
    rail.appendChild(fragment);

    while (rail.children.length > MAX_RENDERED) {
      rail.removeChild(rail.firstElementChild);
    }

    show($('rail-empty'), false);
    updateNote();
  }

  function updateNote() {
    const count = State.liveEmails.size;
    const note = $('rail-note');
    if (!note) return;

    if (!count) {
      note.textContent = 'Addresses appear as they are found.';
      return;
    }

    const classes = { declared: 0, page: 0, elsewhere: 0, unknown: 0 };
    for (const row of State.liveEmails.values()) {
      classes[provenanceOf(row.source_type).cls] += 1;
    }

    const parts = [];
    if (classes.declared)  parts.push(`${classes.declared} declared`);
    if (classes.page)      parts.push(`${classes.page} from pages`);
    if (classes.elsewhere) parts.push(`${classes.elsewhere} elsewhere`);

    note.textContent = `${count} unique — ${parts.join(', ')}`;
  }

  function clear() {
    const rail = $('rail');
    if (rail) rail.replaceChildren();
    show($('rail-empty'), true);
    updateNote();
  }

  /* A snapshot replaces the rail wholesale — used on reconnect, where the
     client may have missed events. */
  function replaceAll(rows) {
    clear();
    if (rows.length) {
      const rail = $('rail');
      const fragment = document.createDocumentFragment();
      rows.slice(-MAX_RENDERED).forEach((row) => {
        const li = buildRow(row, 0);
        li.classList.remove('is-new');    // historical rows must not animate
        fragment.appendChild(li);
      });
      rail.appendChild(fragment);
      show($('rail-empty'), false);
    }
    updateNote();
  }

  return { append, clear, replaceAll, updateNote };
})();
