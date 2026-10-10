// Settings > Certificates: the user's PKCS#12 certificate, installed into their workspaces as ~/.cert/<username>.p12.
//
// The browser talks to Coder's own user-secrets API with the user's session; nothing passes through
// coder-ui-updates. The certificate is one Coder secret: value = the file, base64-encoded (secret values are text),
// file_path = ~/.cert/.<username>.p12.b64. Coder's agent writes it into the owner's workspaces when they start and the
// template's installer (workspace-certificates.tf) decodes it to ~/.cert/<username>.p12 with mode 0600.
// One certificate per user: a certificate saved under another name (earlier versions of this page let users pick
// one, and users can be renamed) is moved to the username's path when the page loads; any further ones are listed
// as not installed so they can be deleted.
// Coder never returns secret values, so this page shows what it can see: description and dates.
import { certificates, checkPassword, inspect, Pkcs12Error, sha256 } from './pkcs12.js';

const API = '/api/v2/users/me/secrets';
const MAX_BYTES = 18 * 1024; // base64 of this fits Coder's 24 KiB limit for one secret value
const PATH_RE = /^~\/\.cert\/\.([A-Za-z0-9_@+-][A-Za-z0-9._@+-]*)\.p12\.b64$/;
const secretName = (name) => `certificate-${name}`;
const pathFor = (name) => `~/.cert/.${name}.p12.b64`;
const $ = (id) => document.getElementById(id);

let username = '';
let current = null; // the certificate installed in the workspaces
let extras = []; // certificates under other names, not installed
let pending = null; // {bytes, info, sha, base64}

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

// Coder's API expects the dashboard's CSRF token with cookie-authenticated changes (the dashboard sends it too).
function csrf() {
  try {
    return window.parent.document.querySelector('meta[property="csrf-token"]')?.getAttribute('content') || '';
  } catch { return ''; }
}

async function api(method, path = '', body) {
  const res = await fetch(API + path, {
    method, credentials: 'same-origin', cache: 'no-store',
    headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}), 'X-CSRF-TOKEN': csrf() },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.validations?.map((v) => v.detail).join(' ') || data?.detail || '';
    throw new Error([data?.message || `HTTP ${res.status}`, detail].filter(Boolean).join(': '));
  }
  return data;
}

const fmtDate = (d) => (d ? new Date(d).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '—');
const daysLeft = (d) => Math.floor((new Date(d) - Date.now()) / 86400000);

// What the description carries (no secrets): subject, expiry and fingerprint, so the list can show them.
function describe(name, facts) {
  const parts = [`PKCS#12 certificate, installed as ~/.cert/${name}.p12`];
  if (facts.subject) parts.push(`subject ${facts.subject}`);
  if (facts.notAfter) parts.push(`expires ${facts.notAfter.toISOString().slice(0, 10)}`);
  parts.push(`sha256 ${facts.sha.slice(0, 16)}`);
  return parts.join(' · ');
}

function parseDescription(d) {
  return {
    subject: /subject ([^·]+?)(?: ·|$)/.exec(d || '')?.[1],
    expires: /expires (\d{4}-\d\d-\d\d)/.exec(d || '')?.[1],
    sha: /sha256 ([0-9a-f]{16})/.exec(d || '')?.[1],
  };
}

// ------------------------------------------------------------------ list
function row(c, installed) {
  const d = parseDescription(c.description);
  const left = d.expires ? daysLeft(d.expires) : null;
  const expiry = d.expires
    ? el('span', {}, fmtDate(d.expires), left < 0 ? el('span', { class: 'badge error', text: 'expired' }) : left <= 30 ? el('span', { class: 'badge warn', text: `${left} days left` }) : null)
    : el('span', { class: 'sub', text: 'unknown' });
  const remove = el('button', { type: 'button', class: 'btn btn-small btn-danger-quiet', text: 'Delete' });
  remove.addEventListener('click', () => removeCert(c, installed));
  let replace = null;
  if (installed) {
    replace = el('button', { type: 'button', class: 'btn btn-small', text: 'Replace…' });
    replace.addEventListener('click', openDialog);
  }
  return el('tr', {},
    el('td', {}, installed ? el('code', { text: `~/.cert/${username}.p12` }) : el('span', {}, el('code', { text: `${c.cert}.p12` }), el('span', { class: 'badge muted', text: 'not installed' })),
      installed && !c.enabled ? el('span', { class: 'badge muted', text: 'disabled' }) : null,
      el('span', { class: 'sub', text: d.sha ? `SHA-256 ${d.sha}…` : '' })),
    el('td', { text: d.subject || '—' }),
    el('td', {}, expiry),
    el('td', { text: fmtDate(c.updated_at) }),
    el('td', { class: 'actions' }, replace, remove));
}

function render() {
  $('add').textContent = current ? 'Replace certificate…' : 'Upload certificate…';
  $('target').textContent = `~/.cert/${username}.p12`;
  if (!current && !extras.length) {
    $('list').replaceChildren(el('p', { class: 'empty', text: `No certificate yet. Upload your .p12 and every workspace you start has it as ~/.cert/${username}.p12.` }));
    return;
  }
  const rows = [current && row(current, true), ...extras.map((c) => row(c, false))].filter(Boolean);
  $('list').replaceChildren(el('table', {},
    el('thead', {}, el('tr', {}, ...['In your workspaces', 'Certificate', 'Expires', 'Updated', ''].map((h) => el('th', { scope: 'col', text: h })))),
    el('tbody', {}, ...rows)),
    extras.length ? el('p', { class: 'hint', text: `Only ~/.cert/${username}.p12 is installed. Certificates saved under other names are not; delete them.` }) : '');
}

async function load() {
  try {
    if (!username) username = (await (await fetch('/api/v2/users/me', { credentials: 'same-origin', cache: 'no-store' })).json()).username;
    const all = await api('GET');
    const certs = (all || []).map((s) => ({ ...s, cert: PATH_RE.exec(s.file_path || '')?.[1] })).filter((s) => s.cert)
      .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
    current = certs.find((c) => c.cert === username) || null;
    if (!current && certs.length) {
      // Saved under another name: install it as ~/.cert/<username>.p12 from now on (the newest, if several).
      const c = certs[0];
      const d = parseDescription(c.description);
      const facts = { sha: d.sha || '', subject: d.subject, notAfter: d.expires ? new Date(d.expires) : null };
      current = await api('PATCH', '/' + encodeURIComponent(c.name), { file_path: pathFor(username), description: describe(username, facts) });
      current = { ...c, ...current, cert: username };
      toast(`${c.cert}.p12 is now installed as ~/.cert/${username}.p12. Restart your workspaces to apply.`);
    }
    extras = certs.filter((c) => current && c.name !== current.name);
    render();
  } catch (e) {
    $('list').replaceChildren(el('p', { class: 'empty', text: `Could not load your certificate: ${e.message}` }));
  }
}

async function removeCert(c, installed) {
  const what = installed ? `~/.cert/${username}.p12` : `${c.cert}.p12 (not installed)`;
  if (!window.confirm(`Delete ${what}?${installed ? '\n\nIt is removed from your workspaces the next time each one starts.' : ''}`)) return;
  try {
    await api('DELETE', '/' + encodeURIComponent(c.name));
    toast(installed ? `Deleted ~/.cert/${username}.p12. Restart your workspaces to remove it from them.` : `Deleted ${c.cert}.p12.`);
    load();
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------------------------ add / replace
function fact(k, v, kind = '') { return el('div', { class: `fact ${kind}` }, el('span', { class: 'k', text: k }), el('span', { class: 'v', text: v })); }

async function analyse() {
  const facts = $('facts');
  if (!pending) { facts.hidden = true; return; }
  const { info, bytes } = pending;
  const out = [fact('File', `PKCS#12, ${(bytes.length / 1024).toFixed(1)} KB, SHA-256 ${pending.sha.slice(0, 16)}…`, 'ok')];
  out.push(fact('Private key', info.hasKey ? 'included' : 'not visible (encrypted, or certificate only)'));
  const legacy = info.encryption.filter((e) => /legacy/.test(e));
  out.push(fact('Protection', `${info.encryption.join(', ') || 'none'}${info.mac ? `; integrity ${info.mac}` : ''}`, legacy.length ? 'warn' : 'ok'));
  if (legacy.length) out.push(fact('', 'Legacy encryption: consider re-exporting with AES (e.g. openssl pkcs12 -export without -legacy).', 'warn'));
  pending.details = null;
  const pw = $('password').value;
  if (pw || info.mac) {
    try {
      const ok = await checkPassword(bytes, pw);
      if (pw || ok) out.push(fact('Password', ok === null ? 'cannot be checked here (PBMAC1)' : ok ? 'correct' : 'does not match this file', ok === false ? 'error' : ok ? 'ok' : ''));
      if (ok) {
        const list = await certificates(bytes, pw).catch(() => null);
        if (Array.isArray(list) && list.length) {
          const leaf = list[0];
          pending.details = leaf;
          const left = daysLeft(leaf.notAfter);
          out.push(fact('Subject', leaf.subject));
          out.push(fact('Issued by', leaf.issuer));
          out.push(fact('Valid until', `${leaf.notAfter.toLocaleDateString(undefined, { dateStyle: 'medium' })}${left < 0 ? ' (expired)' : left <= 30 ? ` (${left} days left)` : ''}`,
            left < 0 ? 'error' : left <= 30 ? 'warn' : 'ok'));
        } else if (list && list.legacy) {
          out.push(fact('Details', `not readable here (${list.legacy})`));
        }
      }
    } catch { /* details are optional */ }
  }
  if (!pw) out.push(fact('', 'Enter the password to check it and see who the certificate is for (optional).'));
  facts.replaceChildren(...out);
  facts.hidden = false;
}

async function pick(file) {
  pending = null;
  $('save').disabled = true;
  if (!file) { analyse(); return; }
  const bytes = new Uint8Array(await file.arrayBuffer());
  $('drop-text').replaceChildren(el('b', { text: file.name }), ' · choose another or drop it here');
  try {
    if (bytes.length > MAX_BYTES) throw new Pkcs12Error(`The file is ${(bytes.length / 1024).toFixed(1)} KB; the limit is 18 KB.`);
    const info = inspect(bytes);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    pending = { bytes, info, sha: await sha256(bytes), base64: btoa(bin) };
  } catch (e) {
    $('facts').replaceChildren(fact('File', e instanceof Pkcs12Error ? e.message : 'This file cannot be read.', 'error'));
    $('facts').hidden = false;
    return;
  }
  await analyse();
  $('save').disabled = !pending;
}

// The frame is as tall as its page, so a centred dialog could sit below the fold or under Coder's bottom bars. Keep it
// inside the part of the frame that is on screen: top of the visible area, at most its height (the dialog's content
// scrolls, its buttons stay at the bottom), re-fitted while the dashboard scrolls or resizes.
function fitDialog(dialog) {
  try {
    const r = window.frameElement.getBoundingClientRect();
    const vh = window.parent.innerHeight;
    const bottomBars = [...window.parent.document.querySelectorAll('.sticky.bottom-0, #coder-classification-bottom')]
      .reduce((h, e) => Math.max(h, vh - e.getBoundingClientRect().top), 0);
    const top = Math.max(0, -r.top);
    const visible = Math.min(vh - bottomBars, r.bottom) - Math.max(0, r.top);
    dialog.style.marginTop = `${top + 12}px`;
    dialog.style.maxHeight = `${Math.max(280, visible - 24)}px`;
  } catch { /* not inside Coder */ }
}
function revealDialog() {
  const dialog = document.querySelector('dialog');
  fitDialog(dialog);
  const refit = () => (dialog.open ? fitDialog(dialog) : window.parent.removeEventListener('scroll', refit));
  try {
    window.parent.addEventListener('scroll', refit, { passive: true });
    window.parent.addEventListener('resize', refit, { passive: true });
  } catch { /* not inside Coder */ }
}

function openDialog() {
  pending = null;
  $('file').value = '';
  $('password').value = '';
  $('dialog-h').textContent = `${current ? 'Replace' : 'Upload'} ~/.cert/${username}.p12`;
  $('save').textContent = current ? 'Replace' : 'Upload';
  $('drop-text').innerHTML = '<b>Choose a .p12 / .pfx file</b> or drop it here<br><small>PKCS#12, up to 18 KB</small>';
  $('facts').hidden = true;
  $('save').disabled = true;
  $('dialog').showModal();
  revealDialog();
}

async function save() {
  if (!pending || !username) return;
  const facts = { sha: pending.sha, subject: pending.details?.subject, notAfter: pending.details?.notAfter };
  const body = { value: pending.base64, description: describe(username, facts), file_path: pathFor(username) };
  const replaced = !!current;
  $('save').disabled = true;
  try {
    if (current && current.name === secretName(username)) {
      await api('PATCH', '/' + encodeURIComponent(current.name), { ...body, enabled: true });
    } else {
      // New, or replacing one whose secret still carries an older name: store it as certificate-<username>.
      if (current) await api('PATCH', '/' + encodeURIComponent(current.name), { file_path: pathFor(`${current.cert}.replaced`) });
      try {
        await api('POST', '', { name: secretName(username), ...body });
      } catch (e) {
        if (current) await api('PATCH', '/' + encodeURIComponent(current.name), { file_path: pathFor(username) }).catch(() => {});
        throw e;
      }
      if (current) await api('DELETE', '/' + encodeURIComponent(current.name));
    }
    pending = null; // drop the file from memory
    $('password').value = '';
    $('dialog').close();
    toast(`${replaced ? 'Replaced' : 'Uploaded'} ~/.cert/${username}.p12. Workspaces you start from now on have it; restart running ones.`);
    load();
  } catch (e) {
    toast(e.message, true);
    $('save').disabled = false;
  }
}

$('add').addEventListener('click', openDialog);
$('cancel').addEventListener('click', () => { pending = null; $('password').value = ''; $('dialog').close(); });
$('dialog').addEventListener('close', () => { pending = null; $('password').value = ''; });
$('file').addEventListener('change', () => pick($('file').files[0]));
let timer = null;
$('password').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(analyse, 350); });
$('save').addEventListener('click', save);
const drop = $('drop');
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); pick(e.dataTransfer.files[0]); });
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); } });

load();
