// Deployment > Keycloak: discovers how Keycloak and Coder are set up for sign-in, shows every check with what is there
// now and what it should be, and applies the suggested fixes on approval (the coder-ui-updates service does the work:
// Keycloak's admin API, the two Secrets Coder reads, and Coder's Argo CD values).
const API = '/__coder-ui/api/keycloak';
const $ = (id) => document.getElementById(id);
const FIX_LABELS = {
  'kc.client': 'Create / repair the Keycloak client',
  'kc.scopes': 'Add the profile and e-mail scopes',
  'coder.secret': "Copy the client secret into Coder's Secret",
  'coder.ca': "Give Coder Keycloak's CA",
  'coder.values': "Update Coder's OIDC settings (Coder restarts)",
};
// Settings field <- Coder env var, to show what Coder has now under each field.
const ENV_FOR = {
  clientId: 'CODER_OIDC_CLIENT_ID', scopes: 'CODER_OIDC_SCOPES', usernameField: 'CODER_OIDC_USERNAME_FIELD',
  emailField: 'CODER_OIDC_EMAIL_FIELD', signInText: 'CODER_OIDC_SIGN_IN_TEXT', iconUrl: 'CODER_OIDC_ICON_URL',
};

let report = null;
let polls = 0;
let pollTimer = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

function toast(text, isError) {
  const t = $('toast');
  t.textContent = text;
  t.classList.toggle('error', !!isError);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 6000);
}

const STATUS = { ok: 'OK', warn: 'Needs fix', error: 'Problem', info: 'Note', unknown: 'Unknown' };

function checkRow(c) {
  const values = [];
  if (c.current) values.push(el('span', { class: 'now', text: c.current }));
  if (c.expected) values.push(el('span', { class: 'arrow', text: '→' }), el('span', { class: 'want', text: c.expected }));
  const row = el('div', { class: 'check', 'data-id': c.id },
    el('span', { class: `pill ${c.status}`, text: STATUS[c.status] || c.status }),
    el('div', {}, el('div', { class: 'check-title', text: c.title }), values.length ? el('div', { class: 'check-values' }, ...values) : null),
    c.fix === 'kc.connect' ? el('button', { type: 'button', class: 'btn btn-small fix', 'data-connect': '1', text: 'Connect…' })
      : c.fix ? el('button', { type: 'button', class: 'btn btn-small fix', 'data-fix': c.fix, text: 'Fix' }) : el('span'),
    c.detail ? el('p', { class: 'check-detail', text: c.detail }) : null);
  if (c.id === 'coder.env' && report.facts.envDiffs?.length) {
    row.append(el('table', { class: 'diffs' },
      el('thead', {}, el('tr', {}, el('th', { text: 'Coder setting' }), el('th', { text: 'Now' }), el('th', { text: 'Suggested' }))),
      el('tbody', {}, ...report.facts.envDiffs.map((d) => el('tr', {},
        el('td', {}, el('code', { text: d.name })),
        el('td', {}, d.current == null ? el('span', { class: 'none', text: 'not set' }) : el('code', { text: d.current })),
        el('td', {}, el('code', { text: d.expected })))))));
  }
  return row;
}

function worst(checks) {
  if (checks.some((c) => c.status === 'error')) return 'error';
  if (checks.some((c) => c.status === 'warn')) return 'warn';
  return 'ok';
}

// "kc.connect" is not applied like the others: it needs an admin to sign in once (the Connect card).
function fixesNeeded() {
  return [...new Set(report.checks.filter((c) => c.fix && c.fix !== 'kc.connect').map((c) => c.fix))];
}

function render() {
  const { checks, facts } = report;
  const level = worst(checks);
  const fixes = fixesNeeded();
  const problems = checks.filter((c) => c.status === 'error' || c.status === 'warn').length;
  $('status-dot').className = `big-dot ${level}`;
  $('status-h').textContent = level === 'ok' ? 'Keycloak sign-in for Coder is set up correctly'
    : `${problems} ${problems === 1 ? 'thing needs' : 'things need'} attention`;
  $('status-sub').textContent = level === 'ok' ? 'Everything below was checked just now.'
    : facts.keycloakConnected === false ? `${facts.connect?.reason === 'stale' ? "The admin login kept in the cluster no longer works. " : ''}Connect to Keycloak below (once) so Keycloak's side can be checked and repaired too.`
      : fixes.length ? 'Review the suggestions below, then approve them. Nothing changes until you do.'
        : 'These cannot be fixed from here; the details below say what to do.';
  const s = facts.settings || {};
  const factRows = [
    ['Keycloak', s.keycloakUrl || '—'], ['Realm', s.realm], ['OIDC client', s.clientId], ['Coder', facts.coderUrl],
    ["Coder's settings", facts.argoApp ? `Argo CD Application ${facts.argoApp}` : 'not editable from here'],
  ];
  $('facts').replaceChildren(...factRows.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v || '—' })]));
  $('fix-all').disabled = !fixes.length;
  $('fix-all').textContent = fixes.length ? `Approve suggested fixes (${fixes.length})` : 'Nothing to fix';
  $('fix-all').title = fixes.map((f) => FIX_LABELS[f]).join('\n');
  $('connect').hidden = facts.keycloakConnected !== false;
  if (facts.keycloakConnected === false) fillConnect(facts.connect || {});
  $('undo').hidden = !facts.undo;
  if (facts.undo) $('undo').title = `Saved ${new Date(facts.undo.savedAt).toLocaleString()} by ${facts.undo.savedBy}`;

  const groups = ['Connection', 'Keycloak', 'Coder'];
  $('checks').replaceChildren(...groups.map((g) => {
    const items = checks.filter((c) => c.group === g);
    return items.length ? el('div', { class: 'group' }, el('h3', { text: g }), ...items.map(checkRow)) : null;
  }).filter(Boolean));
  $('checked-at').textContent = `Checked ${new Date().toLocaleTimeString()}`;
}

// The Connect card, filled from what the service found in the cluster: why it cannot sign in by itself, where the admin
// signs in, the account named in the cluster (pre-filled), and where to check or recover it. Only the password is left.
let connectFilled = false;
function fillConnect(c) {
  const why = $('connect-why');
  why.textContent = c.why || '';
  why.hidden = !c.why;
  const rows = [['Keycloak', c.keycloakUrl], ['Realm', c.realm], ['Looked in', c.secret ? `Secret ${c.secret}` : null]].filter(([, v]) => v);
  $('connect-facts').replaceChildren(...rows.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })]));
  $('connect-facts').hidden = !rows.length;
  const links = [];
  if (c.console) links.push(el('a', { href: c.console, target: '_blank', rel: 'noopener', text: "Keycloak's admin console" }));
  if (c.recovery) links.push(el('a', { href: c.recovery, target: '_blank', rel: 'noopener', text: 'lost the admin account?' }));
  $('connect-links').replaceChildren(...links.flatMap((a, i) => (i ? [' · ', a] : [a])));
  const form = $('connect-form');
  if (!connectFilled) {   // once: never overwrite what the admin typed
    connectFilled = true;
    if (c.username && !form.elements.username.value) form.elements.username.value = c.username;
  }
  $('connect-user-note').textContent = c.username
    ? `From the cluster (${c.secret}). Keep it if only its password changed; otherwise use your admin account.` : '';
}

function focusConnect() {
  $('connect').scrollIntoView({ behavior: 'smooth' });
  const f = $('connect-form').elements;
  (f.username.value ? f.password : f.username).focus();
}

function fillSettings() {
  const s = report.facts.settings || {};
  const form = $('settings');
  const diffs = Object.fromEntries((report.facts.envDiffs || []).map((d) => [d.name, d]));
  for (const input of form.querySelectorAll('input')) {
    const name = input.name;
    const suggest = input.parentElement.querySelector('.suggest');
    if (input.type === 'checkbox') input.checked = !!s[name];
    else if (name === 'tokenHours') input.value = Math.round(((s.tokenLifespan || 36000) / 3600) * 10) / 10;
    else input.value = s[name] ?? '';
    if (!suggest) continue;
    suggest.replaceChildren();
    input.parentElement.classList.remove('differs');
    const env = diffs[ENV_FOR[name]];
    if (env) {
      input.parentElement.classList.add('differs');
      suggest.append(`Suggested. Coder has now: ${env.current ?? 'not set'}`);
    } else if (name === 'keycloakUrl' && report.facts.discoveredKeycloakUrl && report.facts.discoveredKeycloakUrl !== s.keycloakUrl) {
      const use = el('button', { type: 'button', text: `use ${report.facts.discoveredKeycloakUrl}` });
      use.addEventListener('click', () => { input.value = report.facts.discoveredKeycloakUrl; });
      suggest.append('Discovered in the cluster: ', use);
    } else if (name === 'keycloakUrl' && report.facts.discoveredKeycloakUrl) {
      suggest.append('Discovered from the Keycloak resource in the cluster.');
    }
  }
}

function readSettings() {
  const f = $('settings');
  const v = (n) => f.elements[n].value.trim();
  return {
    keycloakUrl: v('keycloakUrl'), realm: v('realm'), clientId: v('clientId'), scopes: v('scopes'),
    usernameField: v('usernameField'), emailField: v('emailField'), signInText: v('signInText'), iconUrl: v('iconUrl'),
    flowAlias: v('flowAlias'), tokenLifespan: Math.round(Number(v('tokenHours') || 10) * 3600),
    allowSignups: f.elements.allowSignups.checked, ignoreEmailVerified: f.elements.ignoreEmailVerified.checked,
  };
}

async function load({ quiet = false } = {}) {
  if (!quiet) $('recheck').disabled = true;
  try {
    const res = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    const first = !report;
    report = data;
    render();
    if (first) fillSettings();
    // While Coder restarts with new settings, look again every few seconds.
    const settling = report.checks.some((c) => c.id === 'coder.rollout' || (c.id === 'coder.live' && c.status !== 'ok'));
    clearTimeout(pollTimer);
    if (settling && polls < 25) { polls += 1; pollTimer = setTimeout(() => load({ quiet: true }), 8000); }
  } catch (e) {
    $('status-dot').className = 'big-dot error';
    $('status-h').textContent = 'Discovery failed';
    $('status-sub').textContent = e.message;
  } finally {
    $('recheck').disabled = false;
  }
}

async function apply(fixes, settings) {
  if (!fixes.length) return;
  const list = fixes.map((f) => `  • ${FIX_LABELS[f] || f}`).join('\n');
  const restart = fixes.includes('coder.values') ? "\n\nCoder restarts to pick up the new settings (about 30 seconds). Signed-in users stay signed in." : '';
  if (!window.confirm(`Apply these changes?\n\n${list}${restart}`)) return;
  for (const b of document.querySelectorAll('button')) b.disabled = true;
  try {
    const res = await fetch(API + '/apply', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' },
      body: JSON.stringify({ fixes, settings }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    toast(data.done.join('. ') + '.');
    polls = 0;
    report = null; // refill the form from the new state
  } catch (e) {
    toast(e.message, true);
  }
  for (const b of document.querySelectorAll('button')) b.disabled = false;
  setTimeout(() => load(), 1500);
}

$('recheck').addEventListener('click', () => { polls = 0; load(); });
$('fix-all').addEventListener('click', () => apply(fixesNeeded(), readSettings()));
$('connect-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  const button = f.querySelector('button');
  button.disabled = true;
  try {
    const res = await fetch(API + '/connect', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' },
      body: JSON.stringify({ username: f.elements.username.value.trim(), password: f.elements.password.value, settings: readSettings() }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    f.reset();
    connectFilled = false;
    toast(data.done.join(' '));
    report = null;
    load();
  } catch (err) {
    toast(err.message, true);
  } finally {
    f.elements.password.value = '';
    button.disabled = false;
  }
});
$('checks').addEventListener('click', (e) => {
  if (e.target.closest('button[data-connect]')) { focusConnect(); return; }
  const fix = e.target.closest('button[data-fix]');
  if (fix) apply([fix.dataset.fix], readSettings());
});
$('settings').addEventListener('submit', (e) => {
  e.preventDefault();
  const fixes = ['kc.client', 'kc.scopes', 'coder.secret', 'coder.ca'];
  if (report?.facts.canEditCoder) fixes.push('coder.values');
  apply(fixes, readSettings());
});
$('reset-settings').addEventListener('click', () => { if (report) fillSettings(); });
$('undo').addEventListener('click', async () => {
  if (!window.confirm("Put Coder's previous sign-in settings back? Coder restarts.")) return;
  const res = await fetch(API + '/undo', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' }, body: '{}' });
  const data = await res.json().catch(() => ({}));
  toast(res.ok ? data.done.join('. ') : data.error || `HTTP ${res.status}`, !res.ok);
  polls = 0;
  report = null;
  setTimeout(() => load(), 1500);
});

load();
