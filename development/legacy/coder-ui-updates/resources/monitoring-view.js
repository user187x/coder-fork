// General > Monitoring: every user, their running workspaces, and a live log for any of them. The service streams the
// workspace pod's log into the temporary log database (5 MB kept per workspace); this page only fetches the new lines
// every second and keeps at most MAX_DOM_LINES on screen, so the browser never carries the whole log.
const API = '/__coder-ui/api/monitoring';
const $ = (id) => document.getElementById(id);
const REFRESH_MS = 20000;
const POLL_MS = 1000;
const MAX_DOM_LINES = 3000;

let data = null;
const openUsers = new Set();
const viewers = new Map(); // workspaceId -> viewer

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
  toast.timer = setTimeout(() => { t.hidden = true; }, 5000);
}

const ago = (iso) => {
  if (!iso) return '—';
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  return s < 90 ? 'just now' : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
const size = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`);

// ------------------------------------------------------------------ live log viewer (one per open workspace)
function viewer(inst) {
  const pre = el('pre', { class: 'log', tabindex: '0', 'aria-label': `Log of ${inst.owner}/${inst.name}`, 'aria-live': 'off' });
  const state = el('span', { class: 'state', text: 'connecting…' });
  const where = el('code', { text: inst.pod || 'pod' });
  const stored = el('span');
  const follow = el('input', { type: 'checkbox', checked: true });
  const pause = el('button', { type: 'button', class: 'btn btn-small', text: 'Pause' });
  const clear = el('button', { type: 'button', class: 'btn btn-small', text: 'Clear view' });
  const bar = el('div', { class: 'logbar' }, state, where, stored, el('span', { class: 'spacer' }),
    el('label', { class: 'row' }, follow, 'Follow'), pause, clear);
  const note = el('div', { class: 'log-note', text: `Streamed from the pod into the temporary log database; the newest ${size(data.capBytes)} are kept there. This page shows up to ${MAX_DOM_LINES.toLocaleString()} lines.` });
  const cell = el('td', { colspan: '5' }, bar, pre, note);
  const v = { row: el('tr', { class: 'logs' }, cell), after: 0, paused: false, timer: null, lines: 0, stopped: false, top: 0, restore: () => {} };

  // Follow = keep the newest line in view. Scrolling up to read pauses it (the box unticks); scrolling back to the
  // bottom, or ticking the box, resumes it.
  const nearBottom = () => pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 12;
  const toBottom = () => { pre.scrollTop = pre.scrollHeight; v.top = pre.scrollTop; };
  let lastTop = 0;
  pre.addEventListener('scroll', () => {
    if (nearBottom()) follow.checked = true;
    else if (pre.scrollTop < lastTop - 2) follow.checked = false; // the user scrolled up
    lastTop = pre.scrollTop;
    v.top = pre.scrollTop;
  });
  follow.addEventListener('change', () => { if (follow.checked) toBottom(); });
  // Moving the row (the users table is rebuilt every few seconds) resets its scroll position: put it back.
  v.restore = () => { if (follow.checked) toBottom(); else pre.scrollTop = v.top || 0; lastTop = pre.scrollTop; };

  pause.addEventListener('click', () => {
    v.paused = !v.paused;
    pause.textContent = v.paused ? 'Resume' : 'Pause';
    if (!v.paused) tick();
  });
  clear.addEventListener('click', () => { pre.replaceChildren(); v.lines = 0; });

  const append = (lines) => {
    const frag = document.createDocumentFragment();
    for (const [, ts, text] of lines) {
      const cls = /\b(error|fatal|panic|fail(ed)?)\b/i.test(text) ? 'err' : /\bwarn(ing)?\b/i.test(text) ? 'warn' : '';
      const line = el('div', cls ? { class: cls } : {}, el('span', { class: 't', text: `${(ts || '').slice(11, 19)} ` }), text);
      frag.append(line);
    }
    pre.append(frag);
    v.lines += lines.length;
    while (v.lines > MAX_DOM_LINES && pre.firstChild) { pre.firstChild.remove(); v.lines -= 1; }
    if (follow.checked) toBottom();
  };

  const tick = async () => {
    clearTimeout(v.timer);
    if (v.stopped || v.paused) return;
    try {
      const res = await fetch(`${API}/logs?workspace=${encodeURIComponent(inst.workspaceId)}&after=${v.after}`, { credentials: 'same-origin', cache: 'no-store' });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
      where.textContent = `${d.pod} / ${d.container}`;
      state.className = d.state === 'streaming' ? 'live' : 'state';
      state.textContent = d.state === 'streaming' ? 'live' : d.state + (d.error ? `: ${d.error}` : '');
      stored.textContent = `${d.stored.lines.toLocaleString()} lines · ${size(d.stored.bytes)} of ${size(d.stored.capBytes)} stored`;
      if (d.lines.length) {
        append(d.lines);
        v.after = d.lines[d.lines.length - 1][0];
      }
      v.timer = setTimeout(tick, d.lines.length >= 2000 ? 50 : POLL_MS);
    } catch (e) {
      state.className = 'state';
      state.textContent = e.message;
      v.timer = setTimeout(tick, 5000);
    }
  };
  v.start = () => { v.stopped = false; tick(); };
  v.stop = () => { v.stopped = true; clearTimeout(v.timer); };
  return v;
}

function toggleInstance(tr, inst) {
  const open = viewers.get(inst.workspaceId);
  if (open) {
    open.stop();
    open.row.remove();
    viewers.delete(inst.workspaceId);
    tr.classList.remove('open');
    tr.setAttribute('aria-expanded', 'false');
    tr.querySelector('.chev').textContent = 'Show logs ▸';
    return;
  }
  const v = viewer(inst);
  viewers.set(inst.workspaceId, v);
  tr.after(v.row);
  tr.classList.add('open');
  tr.setAttribute('aria-expanded', 'true');
  tr.querySelector('.chev').textContent = 'Hide logs ▾';
  v.start();
}

// ------------------------------------------------------------------ users table
function instanceRow(inst) {
  const agent = inst.agents[0];
  const bad = !inst.healthy || (agent && agent.status !== 'connected');
  const tr = el('tr', { class: 'instance', tabindex: '0', role: 'button', 'aria-expanded': 'false', 'data-id': inst.workspaceId },
    el('td', {}, el('span', { class: `dot ${bad ? 'warn' : ''}` }), el('b', { text: inst.name })),
    el('td', { text: inst.template || '—' }),
    el('td', { text: inst.agents.map((a) => `${a.name}: ${a.status}${a.lifecycle && a.lifecycle !== 'ready' ? ` (${a.lifecycle})` : ''}`).join(', ') || '—' }),
    el('td', { text: ago(inst.startedAt), title: inst.startedAt || '' }),
    el('td', {}, el('span', { class: 'chev', text: inst.pod ? 'Show logs ▸' : 'no pod found' })));
  if (inst.pod) {
    tr.addEventListener('click', () => toggleInstance(tr, inst));
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleInstance(tr, inst); } });
  }
  return tr;
}

function userBlock({ user, instances }) {
  const initials = (user.name || user.username).split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  const details = el('details', { class: `user ${instances.length ? '' : 'idle'}`, 'data-user': user.id });
  if (openUsers.has(user.id)) details.open = true;
  details.addEventListener('toggle', () => (details.open ? openUsers.add(user.id) : openUsers.delete(user.id)));
  details.append(el('summary', {},
    el('span', { class: 'avatar', 'aria-hidden': 'true', text: initials }),
    el('span', { class: 'who' }, el('b', { text: user.name || user.username }), el('small', { text: `${user.username}${user.email ? ' · ' + user.email : ''}` })),
    el('span', { class: `pill count ${instances.length ? 'ok' : 'info'}`, text: instances.length ? `${instances.length} running` : 'none running' })));
  const body = instances.length
    ? el('table', {}, el('thead', {}, el('tr', {}, ...['Workspace', 'Template', 'Agent', 'Started', ''].map((h) => el('th', { scope: 'col', text: h })))),
      el('tbody', {}, ...instances.map(instanceRow)))
    : el('p', { class: 'empty', text: 'No running workspaces.' });
  details.append(body);
  return details;
}

function render() {
  if (!data) return;
  const q = $('filter').value.trim().toLowerCase();
  const onlyRunning = $('only-running').checked;
  const rows = data.users.filter(({ user, instances }) => (!onlyRunning || instances.length)
    && (!q || [user.username, user.name, user.email, ...instances.map((i) => i.name)].join(' ').toLowerCase().includes(q)));
  const running = data.users.reduce((n, u) => n + u.instances.length, 0);
  $('summary').textContent = `${data.users.length} users · ${running} running workspace${running === 1 ? '' : 's'} (namespace ${data.namespace})`;
  // Keep open log viewers across refreshes: re-attach their rows under the rebuilt instance rows.
  const blocks = rows.map(userBlock);
  $('users').replaceChildren(...(blocks.length ? blocks : [el('p', { class: 'empty', text: onlyRunning ? 'Nobody has a running workspace right now.' : 'No users match.' })]));
  for (const [id, v] of viewers) {
    const tr = $('users').querySelector(`tr.instance[data-id="${CSS.escape(id)}"]`);
    if (tr) { tr.after(v.row); v.restore(); tr.classList.add('open'); tr.setAttribute('aria-expanded', 'true'); tr.querySelector('.chev').textContent = 'Hide logs ▾'; } else { v.stop(); viewers.delete(id); }
  }
}

async function load() {
  try {
    const res = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
    data = d;
    render();
  } catch (e) {
    $('summary').textContent = `Could not load: ${e.message}`;
  }
  setTimeout(load, REFRESH_MS);
}

$('filter').addEventListener('input', render);
$('only-running').addEventListener('change', render);
load();
