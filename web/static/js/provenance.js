/* ==========================================================================
   Provenance taxonomy.

   The crawler's source_type values, grouped into three classes by HOW the
   address was obtained. This grouping is what the rail's colour encodes, so it
   lives in one place and is used by both the rail and the ledger.
   ========================================================================== */

const PROVENANCE = {
  // Declared — the domain owner published it in a machine-readable record
  'dns':          { sigil: 'DNS', cls: 'declared',  label: 'DNS record' },
  'rdap':         { sigil: 'RDP', cls: 'declared',  label: 'RDAP registration' },
  'security.txt': { sigil: 'SEC', cls: 'declared',  label: 'security.txt' },

  // Page — found in rendered web content
  'mailto':       { sigil: 'MTO', cls: 'page',      label: 'mailto: link' },
  'visible_text': { sigil: 'WEB', cls: 'page',      label: 'Page text' },
  'obfuscated':   { sigil: 'OBF', cls: 'page',      label: 'Obfuscated in page' },

  // Elsewhere — documents and off-domain presences
  'pdf':          { sigil: 'PDF', cls: 'elsewhere', label: 'PDF document' },
  'external':     { sigil: 'EXT', cls: 'elsewhere', label: 'External presence' },
};

const UNKNOWN_PROVENANCE = { sigil: '???', cls: 'unknown', label: 'Unknown source' };

function provenanceOf(sourceType) {
  return PROVENANCE[sourceType] || UNKNOWN_PROVENANCE;
}

/* Validation verdicts map to a tone so the pill colour carries the meaning. */
function validationTone(status) {
  if (!status) return 'neutral';
  if (status.startsWith('Valid Domain – Target')) return 'good';
  if (status.startsWith('Valid Domain')) return 'neutral';
  if (status === 'No MX Record') return 'warn';
  if (status === 'Invalid Syntax') return 'bad';
  return 'neutral';
}

function domainMatchTone(match) {
  if (match === 'Target Domain') return 'good';
  if (match === 'Subdomain') return 'good';
  if (match === 'External Domain') return 'warn';
  return 'neutral';
}
