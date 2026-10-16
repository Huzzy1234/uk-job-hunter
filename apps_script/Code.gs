/**
 * UK Job Hunter — Google Sheet backend.
 *
 * Paste this whole file into the Sheet: Extensions → Apps Script (replace everything),
 * then Deploy → New deployment → Web app → Execute as: Me, Who has access: Anyone.
 * The /exec URL it gives you is APPS_SCRIPT_URL.
 *
 * The Python pipeline (GitHub Actions) and the portal both talk to this script:
 *   GET  ?action=ping|settings|ids|jobs|dropped|log|stats|links&key=SECRET
 *   POST JSON {key, action: setup|upsert_jobs|replace_dropped|set_status|set_settings|append_log|run_now, ...}
 */

const SECRET = '__SHEET_SECRET__';
const GITHUB_REPO = '__GITHUB_REPO__';   // owner/repo — used by the Run now button
const TZ = 'Europe/London';
const VERSION = '0.2.0';

const TAB = { jobs: 'Jobs', dropped: 'Dropped', settings: 'Settings', log: 'Log', profile: 'Profile', links: 'Links' };

const JOB_HEADERS = ['Score', 'Title', 'Company', 'Location', 'Work mode', 'Hours / contract', 'Pay',
  'Why it fits', 'Red flags', 'Apply', 'Posted', 'Source', 'First seen', 'Last seen', 'Status', 'Notes', 'job_id'];
const JOB_UPDATE_COLS = ['Score', 'Title', 'Company', 'Location', 'Work mode', 'Hours / contract', 'Pay',
  'Why it fits', 'Red flags', 'Apply', 'Posted', 'Source', 'Last seen'];
const DROPPED_HEADERS = ['Title', 'Company', 'Location', 'Source', 'Reason', 'Link', 'Posted', 'Seen'];
const LOG_HEADERS = ['Run time', 'Source', 'Fetched', 'Kept', 'New', 'Dropped', 'Errors', 'Duration s', 'Notes'];
const STATUS_OPTIONS = ['New', 'Applied', 'Interview', 'Rejected', 'Offer', 'Skip'];

const DEFAULT_SETTINGS = [
  ['mode', 'part_time', 'part_time (Student visa, ≤20 h/week) or full_time (after graduation)'],
  ['keywords', 'finance assistant, accounts assistant, assistant accountant, bookkeeper, payroll, treasury, payments, reconciliation, credit control, finance analyst, accountant', 'Search terms sent to each job source (comma separated)'],
  ['title_include', 'account, finance, treasury, payment, settlement, reconcil, bookkeep, payroll, ledger, credit control, regulatory report, safeguarding, billing, audit, fp&a', 'A job title must contain one of these'],
  ['title_exclude', 'director, head of, chief, sales, mortgage adviser, financial adviser, financial advisor, teacher, lecturer, solicitor, nurse, driver', 'Titles containing these are dropped'],
  ['radius_miles', '15', 'Distance from Bristol centre'],
  ['pay_floor_hourly', '12.71', 'Drop anything paying less per hour (National Living Wage Apr 2026)'],
  ['min_fte_salary', '24000', 'Drop annual salaries below this (full-time equivalent)'],
  ['good_pay_hourly', '15', 'Tag as good pay at or above this per hour'],
  ['max_days_old', '21', 'Ignore adverts older than this many days'],
  ['sources_enabled', 'reed, adzuna', 'Sources to query'],
  ['keep_unverified_hours', 'true', 'Keep temp/interim roles whose hours are not stated (flagged "hours: confirm")'],
  ['max_london_office_days', '0', 'London hybrid roles allowed if office days ≤ this (0 = never)'],
  ['include_remote', 'true', 'Include fully remote UK roles'],
];

const DEFAULT_PROFILE = [
  ['headline', 'Payments treasury & settlement analyst · ICAN chartered accountant · FCA regulatory reporting'],
  ['role_families_ranked', '1 Treasury / liquidity / FX ops; 2 Settlement, reconciliation, safeguarding; 3 Regulatory reporting (payments, e-money); 4 Finance / accounts assistant, assistant accountant; 5 Payroll, bookkeeping'],
  ['skills', 'treasury management, FX pricing & execution, liquidity routing, settlement, bank & wallet reconciliation, FCA returns, SEC compliance, month-end close, management accounts, tax filings (CIT, VAT, PAYE), payroll, cost control'],
  ['systems', 'Xero, Sage 50, SAP S/4HANA, advanced Excel'],
  ['experience_summary', '4+ years: senior treasury analyst at an African-corridor FX fintech ($12M+ monthly GHS volume); finance, treasury & settlement analyst at a UK FCA-authorised payment institution (remote); financial control analyst; audit trainee'],
  ['constraints', 'Student visa: max 20 hours/week in term (hard weekly cap), full-time only 21 Dec 2026–3 Jan 2027 and 22 Mar–4 Apr 2027; no self-employment; Bristol / Bath / remote UK only'],
  ['availability', 'Up to 20 h/week now; full-time from Oct 2027 on Graduate visa (18 months, no sponsorship needed)'],
  ['pay_expectation', '£15–20/hour part-time; £33k+ full-time from 2027'],
];

const LINKS = [
  ['Reed – part-time finance, Bristol', 'https://www.reed.co.uk/jobs/part-time-finance-jobs-in-bristol', 'Same data the tool pulls; useful to browse'],
  ['LinkedIn – part-time finance, Bristol (last 7 days)', 'https://www.linkedin.com/jobs/search?keywords=finance&location=Bristol%2C%20England%2C%20United%20Kingdom&f_JT=P&f_TPR=r604800', 'Cannot be automated; set an email alert here'],
  ['Indeed – part-time finance assistant, Bristol', 'https://uk.indeed.com/jobs?q=finance+assistant&l=Bristol&sc=0kf%3Ajt%28parttime%29%3B&fromage=7', 'Set an email alert'],
  ['Totaljobs – part-time finance, Bristol', 'https://www.totaljobs.com/jobs/part-time/finance/in-bristol?radius=15', 'Set an email alert'],
  ['GOV.UK Find a job – finance, Bristol', 'https://findajob.dwp.gov.uk/search?q=finance&w=Bristol', 'Set an email alert'],
  ['ACCA Careers – Bristol', 'https://jobs.accaglobal.com/jobs/bristol/', 'Qualified-accountant roles'],
  ['UWE Students\' Union JobShop', 'https://www.thestudentsunion.co.uk/jobs/browse/', 'Student-friendly part-time roles'],
  ['UWE InfoHub jobs', 'https://infohub.uwe.ac.uk/students/jobs', 'Login with UWE account'],
  ['Robert Half Bristol', 'https://www.roberthalf.com/gb/en/locations/bristol', 'Register for temp finance work'],
  ['Core3 (Bristol finance recruiters)', 'https://www.core3.co.uk/', 'Register for interim roles'],
  ['Ashley Rees Associates', 'https://www.ashleyreesassociates.co.uk/', 'Temp & perm accountancy, Bristol'],
  ['Element Recruitment', 'https://www.element-recruitment.co.uk/', 'Part-qualified finance, Bristol'],
];

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

function doGet(e) { return handle_(e, 'GET'); }
function doPost(e) { return handle_(e, 'POST'); }

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Job Hunter')
    .addItem('Set up / repair tabs', 'menuSetup_')
    .addItem('Run the job search now', 'menuRunNow_')
    .addToUi();
}

function menuSetup_() { ensureAll_(); SpreadsheetApp.getUi().alert('Tabs are ready.'); }
function menuRunNow_() {
  const r = runNow_();
  SpreadsheetApp.getUi().alert(r.ok ? 'Started. New jobs appear in a few minutes.' : 'Could not start: ' + r.error);
}

function handle_(e, method) {
  try {
    const params = (e && e.parameter) || {};
    let body = {};
    if (method === 'POST' && e.postData && e.postData.contents) {
      body = JSON.parse(e.postData.contents);
    }
    const key = params.key || body.key;
    if (key !== SECRET) return json_({ ok: false, error: 'unauthorised' });
    const action = params.action || body.action || 'ping';
    const fn = ACTIONS[action];
    if (!fn) return json_({ ok: false, error: 'unknown action: ' + action });

    const lock = LockService.getScriptLock();
    lock.waitLock(30000);
    try {
      const result = fn(body, params) || {};
      result.ok = result.ok !== false;
      return json_(result);
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return json_({ ok: false, error: String(err && err.message || err) });
  }
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

const ACTIONS = {
  ping: () => ({ version: VERSION, sheet: ss_().getName(), tabs: ss_().getSheets().map(s => s.getName()), time: now_() }),
  setup: () => { ensureAll_(); return { tabs: ss_().getSheets().map(s => s.getName()) }; },

  settings: () => ({ settings: readSettings_() }),
  set_settings: (body) => { writeSettings_(body.settings || {}); return { settings: readSettings_() }; },

  ids: () => ({ rows: readJobs_().map(j => ({
    job_id: j.job_id, status: j.Status, title: j.Title, company: j.Company, location: j.Location,
    last_seen: j['Last seen'], score: j.Score, why: j['Why it fits'], red_flags: j['Red flags'],
  })) }),
  jobs: (body, p) => {
    const q = Object.assign({}, p, body);
    let rows = readJobs_();
    if (q.status) rows = rows.filter(j => String(j.Status) === q.status);
    if (q.since) rows = rows.filter(j => String(j['First seen']) >= q.since);
    const limit = Number(q.limit || 500);
    return { rows: rows.slice(0, limit), total: rows.length };
  },
  upsert_jobs: (body) => upsertJobs_(body.jobs || []),
  set_status: (body) => setStatus_(body.job_id, body.status, body.notes),

  replace_dropped: (body) => replaceDropped_(body.rows || []),
  dropped: () => ({ rows: readTable_(sheet_(TAB.dropped, DROPPED_HEADERS)) }),

  append_log: (body) => { appendRow_(sheet_(TAB.log, LOG_HEADERS), LOG_HEADERS, body.row || {}); return {}; },
  log: (body, p) => {
    const rows = readTable_(sheet_(TAB.log, LOG_HEADERS));
    const n = Number((p && p.limit) || body.limit || 30);
    return { rows: rows.slice(-n).reverse() };
  },
  stats: () => stats_(),
  links: () => ({ rows: readTable_(sheet_(TAB.links, ['Name', 'URL', 'What for'])) }),
  profile: () => ({ profile: readKeyValue_(sheet_(TAB.profile, ['Field', 'Value'])) }),
  run_now: () => runNow_(),
};

// ---------------------------------------------------------------------------
// Sheet plumbing
// ---------------------------------------------------------------------------

function ss_() { return SpreadsheetApp.getActiveSpreadsheet(); }
function now_() { return Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd HH:mm'); }
function today_() { return Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd'); }

function sheet_(name, headers) {
  const ss = ss_();
  let sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  const firstRow = sh.getLastRow() ? sh.getRange(1, 1, 1, headers.length).getValues()[0] : [];
  if (String(firstRow[0] || '') !== headers[0]) {
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  } else {
    // add any new header columns at the end without touching data
    headers.forEach((h, i) => { if (String(firstRow[i] || '') !== h) sh.getRange(1, i + 1).setValue(h); });
  }
  const hdr = sh.getRange(1, 1, 1, headers.length);
  hdr.setFontWeight('bold').setBackground('#1f2937').setFontColor('#ffffff');
  sh.setFrozenRows(1);
  textColumns_(sh, headers);
  return sh;
}

const TEXT_COLUMNS = ['First seen', 'Last seen', 'Posted', 'Run time', 'Seen', 'Apply', 'Link', 'job_id'];

function cell_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, TZ, 'yyyy-MM-dd HH:mm').replace(' 00:00', '');
  return v;
}

function readTable_(sh) {
  const last = sh.getLastRow();
  if (last < 2) return [];
  const values = sh.getRange(1, 1, last, sh.getLastColumn()).getValues();
  const headers = values[0].map(String);
  return values.slice(1).filter(r => r.some(c => c !== '')).map(r => {
    const o = {};
    headers.forEach((h, i) => { if (h) o[h] = cell_(r[i]); });
    return o;
  });
}

// Timestamps, URLs and ids are stored as plain text so Sheets never re-types them.
function textColumns_(sh, headers) {
  headers.forEach((h, i) => {
    if (TEXT_COLUMNS.indexOf(h) >= 0) sh.getRange(1, i + 1, Math.max(sh.getMaxRows(), 2), 1).setNumberFormat('@');
  });
}

function readKeyValue_(sh) {
  const o = {};
  readTable_(sh).forEach(r => { const k = Object.values(r)[0]; if (k) o[String(k)] = Object.values(r)[1]; });
  return o;
}

function appendRow_(sh, headers, obj) {
  sh.appendRow(headers.map(h => obj[h] === undefined || obj[h] === null ? '' : obj[h]));
}

// ---- Jobs ------------------------------------------------------------------

function jobsSheet_() { return sheet_(TAB.jobs, JOB_HEADERS); }

function readJobs_() {
  return readTable_(jobsSheet_()).filter(j => j.job_id);
}

function upsertJobs_(incoming) {
  const sh = jobsSheet_();
  const col = {}; JOB_HEADERS.forEach((h, i) => col[h] = i);
  const last = sh.getLastRow();
  const existing = last > 1 ? sh.getRange(2, 1, last - 1, JOB_HEADERS.length).getValues() : [];
  const index = {};
  existing.forEach((r, i) => { if (r[col.job_id]) index[String(r[col.job_id])] = i; });

  const stamp = now_();
  let updated = 0, added = 0;
  const newRows = [];
  incoming.forEach(job => {
    const id = String(job.job_id || '');
    if (!id) return;
    if (index[id] !== undefined) {
      const r = existing[index[id]];
      JOB_UPDATE_COLS.forEach(h => { if (job[h] !== undefined) r[col[h]] = job[h]; });
      r[col['Last seen']] = stamp;
      updated++;
    } else {
      const r = JOB_HEADERS.map(h => job[h] === undefined || job[h] === null ? '' : job[h]);
      r[col['First seen']] = stamp;
      r[col['Last seen']] = stamp;
      r[col.Status] = 'New';
      r[col.Notes] = '';
      newRows.push(r);
      added++;
    }
  });

  if (existing.length) sh.getRange(2, 1, existing.length, JOB_HEADERS.length).setValues(existing);
  if (newRows.length) sh.getRange(existing.length + 2, 1, newRows.length, JOB_HEADERS.length).setValues(newRows);

  const total = existing.length + newRows.length;
  if (total) {
    sh.getRange(2, 1, total, JOB_HEADERS.length)
      .sort([{ column: col['First seen'] + 1, ascending: false }, { column: col.Score + 1, ascending: false }]);
  }
  decorateJobs_(sh, total);
  return { new: added, updated: updated, total: total };
}

function setStatus_(jobId, status, notes) {
  if (!jobId) throw new Error('job_id required');
  const sh = jobsSheet_();
  const last = sh.getLastRow();
  if (last < 2) throw new Error('no jobs');
  const idCol = JOB_HEADERS.indexOf('job_id') + 1;
  const ids = sh.getRange(2, idCol, last - 1, 1).getValues().map(r => String(r[0]));
  const i = ids.indexOf(String(jobId));
  if (i < 0) throw new Error('job not found: ' + jobId);
  if (status) sh.getRange(i + 2, JOB_HEADERS.indexOf('Status') + 1).setValue(status);
  if (notes !== undefined) sh.getRange(i + 2, JOB_HEADERS.indexOf('Notes') + 1).setValue(notes);
  return { job_id: jobId, status: status, row: i + 2 };
}

function decorateJobs_(sh, total) {
  const rows = Math.max(total, 1);
  const statusCol = JOB_HEADERS.indexOf('Status') + 1;
  const scoreCol = JOB_HEADERS.indexOf('Score') + 1;
  const statusRange = sh.getRange(2, statusCol, rows, 1);
  statusRange.setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(STATUS_OPTIONS, true).setAllowInvalid(false).build());

  const all = sh.getRange(2, 1, rows, JOB_HEADERS.length);
  const scoreRange = sh.getRange(2, scoreCol, rows, 1);
  const rules = [
    SpreadsheetApp.newConditionalFormatRule().whenNumberGreaterThanOrEqualTo(80).setBackground('#d9ead3').setRanges([scoreRange]).build(),
    SpreadsheetApp.newConditionalFormatRule().whenNumberBetween(60, 79).setBackground('#fff2cc').setRanges([scoreRange]).build(),
    SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=$' + colLetter_(statusCol) + '2="Applied"').setBackground('#cfe2f3').setRanges([all]).build(),
    SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=$' + colLetter_(statusCol) + '2="Interview"').setBackground('#d9d2e9').setRanges([all]).build(),
    SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=OR($' + colLetter_(statusCol) + '2="Rejected",$' + colLetter_(statusCol) + '2="Skip")').setFontColor('#9ca3af').setRanges([all]).build(),
  ];
  sh.setConditionalFormatRules(rules);

  const widths = { 'Score': 55, 'Title': 240, 'Company': 170, 'Location': 130, 'Work mode': 95, 'Hours / contract': 160,
    'Pay': 150, 'Why it fits': 300, 'Red flags': 220, 'Apply': 90, 'Posted': 90, 'Source': 110, 'First seen': 120,
    'Last seen': 120, 'Status': 95, 'Notes': 220 };
  JOB_HEADERS.forEach((h, i) => { if (widths[h]) sh.setColumnWidth(i + 1, widths[h]); });
  sh.hideColumns(JOB_HEADERS.indexOf('job_id') + 1);
  ['Why it fits', 'Red flags', 'Notes'].forEach(h => sh.getRange(2, JOB_HEADERS.indexOf(h) + 1, rows, 1).setWrap(true));
  sh.getRange(2, JOB_HEADERS.indexOf('Apply') + 1, rows, 1).setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP);
}

function colLetter_(n) { let s = ''; while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = (n - m - 1) / 26; } return s; }

// ---- Dropped / Settings / Stats --------------------------------------------

function replaceDropped_(rows) {
  const sh = sheet_(TAB.dropped, DROPPED_HEADERS);
  const last = sh.getLastRow();
  if (last > 1) sh.getRange(2, 1, last - 1, sh.getLastColumn()).clearContent();
  const stamp = now_();
  const values = rows.map(r => DROPPED_HEADERS.map(h => h === 'Seen' ? stamp : (r[h] === undefined ? '' : r[h])));
  if (values.length) sh.getRange(2, 1, values.length, DROPPED_HEADERS.length).setValues(values);
  sh.setColumnWidth(1, 240); sh.setColumnWidth(5, 320); sh.setColumnWidth(6, 90);
  return { rows: values.length };
}

function settingsSheet_() {
  const sh = sheet_(TAB.settings, ['Setting', 'Value', 'What it does']);
  const have = readKeyValue_(sh);
  const missing = DEFAULT_SETTINGS.filter(d => have[d[0]] === undefined);
  if (missing.length) sh.getRange(sh.getLastRow() + 1, 1, missing.length, 3).setValues(missing);
  sh.setColumnWidth(1, 180); sh.setColumnWidth(2, 420); sh.setColumnWidth(3, 420);
  return sh;
}

function readSettings_() { return readKeyValue_(settingsSheet_()); }

function writeSettings_(obj) {
  const sh = settingsSheet_();
  const last = sh.getLastRow();
  const keys = last > 1 ? sh.getRange(2, 1, last - 1, 1).getValues().map(r => String(r[0])) : [];
  Object.keys(obj).forEach(k => {
    const i = keys.indexOf(k);
    const v = Array.isArray(obj[k]) ? obj[k].join(', ') : String(obj[k]);
    if (i >= 0) sh.getRange(i + 2, 2).setValue(v); else sh.appendRow([k, v, '']);
  });
}

function stats_() {
  const jobs = readJobs_();
  const byStatus = {};
  STATUS_OPTIONS.forEach(s => byStatus[s] = 0);
  jobs.forEach(j => { byStatus[j.Status] = (byStatus[j.Status] || 0) + 1; });
  const t = today_();
  const logRows = readTable_(sheet_(TAB.log, LOG_HEADERS));
  return {
    total: jobs.length,
    by_status: byStatus,
    new_today: jobs.filter(j => String(j['First seen']).slice(0, 10) === t).length,
    last_run: logRows.length ? logRows[logRows.length - 1] : null,
    runs_logged: logRows.length,
  };
}

function ensureAll_() {
  const sh = jobsSheet_();
  decorateJobs_(sh, Math.max(sh.getLastRow() - 1, 1));
  sheet_(TAB.dropped, DROPPED_HEADERS);
  settingsSheet_();
  sheet_(TAB.log, LOG_HEADERS);
  const prof = sheet_(TAB.profile, ['Field', 'Value']);
  if (prof.getLastRow() < 2) { prof.getRange(2, 1, DEFAULT_PROFILE.length, 2).setValues(DEFAULT_PROFILE); }
  prof.setColumnWidth(1, 180); prof.setColumnWidth(2, 700); prof.getRange(2, 2, DEFAULT_PROFILE.length, 1).setWrap(true);
  const links = sheet_(TAB.links, ['Name', 'URL', 'What for']);
  if (links.getLastRow() < 2) { links.getRange(2, 1, LINKS.length, 3).setValues(LINKS); }
  links.setColumnWidth(1, 300); links.setColumnWidth(2, 420); links.setColumnWidth(3, 300);
  // Tab order: Jobs first.
  ss_().setActiveSheet(sh); ss_().moveActiveSheet(1);
  const extra = ss_().getSheetByName('Sheet1');
  if (extra && extra.getLastRow() === 0 && ss_().getSheets().length > 1) ss_().deleteSheet(extra);
}

// ---- Run now (GitHub workflow_dispatch) --------------------------------------

function runNow_() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) return { ok: false, error: 'GITHUB_TOKEN not set (Apps Script → Project settings → Script properties)' };
  const url = 'https://api.github.com/repos/' + GITHUB_REPO + '/actions/workflows/run.yml/dispatches';
  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true,
  });
  const code = res.getResponseCode();
  return code === 204 ? { ok: true, started: now_() } : { ok: false, error: 'GitHub said ' + code + ': ' + res.getContentText().slice(0, 200) };
}
