// Workspace page > every agent > "Terminal": the agent's terminal in a drop-down right under its "Logs", inside the
// page instead of a new window. The rewritten WorkspacePage chunk adds the drop-down (Coder's own Collapsible, button
// and chevron, built like "Logs") and calls attach(section, agent) once; this module fills it while it is open.
//
// The terminal is Coder's own terminal page (/@owner/workspace.agent/terminal?reconnect=<id>) in a same-origin frame:
// the same renderer, font, copy / paste and reconnection as the window the "Terminal" button opens. Inside the frame,
// dashboard.js sees data-cui-embed and leaves out the dashboard around it (classification marking, announcement).
//
// Sessions. Coder's agent keeps a terminal session (a "reconnecting PTY", keyed by its reconnect id) running for a few
// minutes after its last connection closes, and its protocol has no "end session" message. So this module starts the
// session itself before the frame attaches to it: the first connection runs the user's login shell through a small
// wrapper that records the shell's process id (/tmp/coder-ui-terminal-<id>.pid) and marks its environment
// (CODER_UI_TERMINAL=<id>). Ending the session runs a one-off command in the workspace that sends that shell SIGHUP
// (what closing a terminal window does) and SIGKILL a second later if it is still there; on Linux the mark is checked
// first, so a reused process id is never hit. Windows agents get Coder's plain session: closing only disconnects, and
// the agent ends the session after its idle timeout.
//
//   "End session when closed" (on by default, remembered per browser): collapsing the drop-down, or leaving the
//   workspace page inside the dashboard, ends the session. Off: it keeps running and is there again when the drop-down
//   is reopened in the same tab; meanwhile the header shows "Session running" with an "End session" button.
//   Closing the browser tab cannot end it; the agent does, after its idle timeout.
//   A shell that exits (`exit`, Ctrl-D) ends the session; the drop-down then offers a new one.

const END_KEY = 'coder-ui-terminal-end-on-close';
const START_WAIT_MS = 4000;   // the new session's first output (the prompt); the frame is shown after it at the latest
const END_WAIT_MS = 10000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const rows = new Map();   // agent id -> row state (one terminal drop-down per agent)

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...children.filter(Boolean));
  return node;
}

function css() {
  if (document.getElementById('coder-ui-agent-terminal-css')) return;
  document.head.append(el('link', { id: 'coder-ui-agent-terminal-css', rel: 'stylesheet', href: new URL('agent-terminal.css', import.meta.url).pathname }));
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function endOnClose() {
  try { return localStorage.getItem(END_KEY) !== '0'; } catch { return true; }
}

function setEndOnClose(on) {
  try { localStorage.setItem(END_KEY, on ? '1' : '0'); } catch { /* not remembered */ }
}

// The session survives reloads of this tab (sessionStorage), not other tabs.
const sessionKey = (agentId) => `coder-ui-terminal:${agentId}`;
function loadSession(agentId) {
  try {
    const s = JSON.parse(sessionStorage.getItem(sessionKey(agentId)) || 'null');
    return s && UUID.test(s.id) ? s : null;
  } catch { return null; }
}
function saveSession(agentId, s) {
  try {
    if (s) sessionStorage.setItem(sessionKey(agentId), JSON.stringify(s));
    else sessionStorage.removeItem(sessionKey(agentId));
  } catch { /* the session is still ended by the agent's idle timeout */ }
}

// Shell snippets for the agent (run with the user's login shell -c, so: one `exec`, the rest single-quoted for /bin/sh;
// no backslashes or single quotes inside, which fish would treat differently).
const pidFile = (id) => `/tmp/coder-ui-terminal-${id}.pid`;
const startCommand = (id) => "exec /bin/sh -c '"
  + `echo $$ >${pidFile(id)}; s=$SHELL; [ -n "$s" ] || s=$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7); `
  + `exec env CODER_UI_TERMINAL=${id} "\${s:-/bin/sh}" -l'`;
const endCommand = (id) => "exec /bin/sh -c '"
  + `f=${pidFile(id)}; p=$(cat "$f" 2>/dev/null); rm -f "$f"; case "$p" in ""|*[!0-9]*) exit 0;; esac; `
  + `if [ -r /proc/self/environ ]; then case "$(tr "\\000" " " <"/proc/$p/environ" 2>/dev/null)" in *"CODER_UI_TERMINAL=${id} "*) ;; *) exit 0;; esac; fi; `
  + `kill -HUP "$p" 2>/dev/null || exit 0; sleep 1; kill -0 "$p" 2>/dev/null && kill -KILL "$p"; exit 0'`;

function ptyUrl(agentId, reconnect, command) {
  const q = new URLSearchParams({ reconnect, width: '80', height: '24' });
  if (command) q.set('command', command);
  return `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/v2/workspaceagents/${encodeURIComponent(agentId)}/pty?${q}`;
}

// One connection to a session: until its first output (start) or until the agent closes it after the command (end).
// true = that happened; false = no connection, or no answer in time.
function connect(url, untilClose, timeoutMs) {
  return new Promise((resolve) => {
    let ws = null;
    let opened = false;
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { ws?.close(1000); } catch { /* already closed */ }
      resolve(ok);
    };
    const timer = setTimeout(() => done(false), timeoutMs);
    try { ws = new WebSocket(url); } catch { done(false); return; }
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => { opened = true; };
    ws.onmessage = () => { if (!untilClose) done(true); };
    ws.onclose = () => done(untilClose && opened);
  });
}

// Starts the session (or attaches to it if it is still running: the agent then ignores the command). Best effort: if
// this fails, the frame starts Coder's plain session under the same id, and shows why it cannot connect if it cannot.
async function startSession(agent, session) {
  if (!session.wrapped) return;
  await connect(ptyUrl(agent.id, session.id, startCommand(session.id)), false, START_WAIT_MS);
}

// true: ended; false: not confirmed; null: nothing to end from here (Windows: the agent ends it when idle).
async function endSession(agent, session) {
  if (!session.wrapped) return null;
  return connect(ptyUrl(agent.id, uuid(), endCommand(session.id)), true, END_WAIT_MS);
}

function terminalPath(agent, id) {
  const m = location.pathname.match(/^\/(@[^/]+)\/([^/.]+)/);
  if (!m) return null;
  return `/${m[1]}/${m[2]}.${encodeURIComponent(agent.name)}/terminal?reconnect=${id}`;
}

// -------------------------------------------------------------------------------------------------- one agent's row
function rowState(agent) {
  let row = rows.get(agent.id);
  if (!row) {
    const stored = loadSession(agent.id);
    row = { agent, section: null, body: null, frame: null, session: stored, busy: false, watch: null };
    rows.set(agent.id, row);
  }
  row.agent = agent;
  return row;
}

function setStatus(row, text, kind = '') {
  const status = row.body?.querySelector('.cui-term-message');
  if (!status) return;
  status.textContent = text || '';
  status.dataset.kind = kind;
  status.hidden = !text;
}

// The header next to "Terminal": a kept session (drop-down closed, "End session when closed" off) and its End button.
function renderHeader(row) {
  const slot = row.section?.querySelector('.coder-ui-term-status');
  if (!slot) return;
  const kept = !!(row.session && !row.body);
  // Only on a change: the section's observer calls this on every mutation, and a mutation here would call it again.
  if (slot.dataset.kept === String(kept)) return;
  slot.dataset.kept = String(kept);
  if (!kept) { slot.replaceChildren(); return; }
  const end = el('button', { type: 'button', class: 'cui-term-btn cui-term-danger', text: 'End session' });
  end.addEventListener('click', (e) => { e.stopPropagation(); finish(row, 'header'); });
  slot.replaceChildren(el('span', { class: 'cui-term-badge', text: 'Session running' }), end);
}

function removeFrame(row) {
  if (row.watch) { row.watch.disconnect(); row.watch = null; }
  row.frame?.remove();
  row.frame = null;
}

// Coder's terminal page marks its state on its root element (data-status: initializing | connected | disconnected).
// Once connected, "disconnected" means the shell exited (or the connection dropped): the frame goes before Coder's
// own reconnect (1 s later) would start a fresh, unwrapped shell under the same id, and the drop-down offers to go on.
function watchFrame(row, frame) {
  let connected = false;
  const check = () => {
    const doc = frame.contentDocument;
    const status = doc && doc.querySelector('[data-status]')?.getAttribute('data-status');
    if (status === 'connected') connected = true;
    else if (status === 'disconnected' && connected && row.frame === frame) {
      removeFrame(row);
      showEnded(row, 'The terminal disconnected: the shell exited or the connection dropped.', true);
    }
  };
  frame.addEventListener('load', () => {
    const doc = frame.contentDocument;
    if (!doc) return;
    row.watch?.disconnect();
    row.watch = new doc.defaultView.MutationObserver(check);
    row.watch.observe(doc, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-status'] });
    check();
  });
}

// Starts (or re-attaches to) the session, then shows the frame in whatever drop-down is open by then: the drop-down
// can be closed and reopened, and the session ended, while the session is starting.
async function show(row) {
  if (!row.body) return;
  if (row.busy) { setStatus(row, 'Starting the session…'); return; }
  if (!terminalPath(row.agent, 'x')) { setStatus(row, 'The terminal is only available on the workspace page.', 'error'); return; }
  row.body.querySelector('.cui-term-actions')?.remove();
  row.busy = true;
  try {
    while (row.body && !row.frame) {
      if (!row.session) {
        row.session = { id: uuid(), wrapped: row.agent.operating_system !== 'windows' };
        saveSession(row.agent.id, row.session);
      }
      const session = row.session;
      setStatus(row, 'Starting the session…');
      await startSession(row.agent, session);
      if (!row.body || row.session !== session) continue;   // closed, or ended (then a new one) meanwhile
      const path = terminalPath(row.agent, session.id);
      const frame = el('iframe', {
        class: 'cui-term-frame', name: 'coder-ui-terminal', title: `Terminal: ${row.agent.name}`, src: path,
        allow: 'clipboard-read; clipboard-write', 'data-cui-embed': '1', 'data-cui-embed-path': path.split('?')[0],
      });
      watchFrame(row, frame);
      row.frame = frame;
      setStatus(row, '');
      row.body.querySelector('.cui-term-frame-box').append(frame);
      row.body.querySelector('.cui-term-end').disabled = false;
    }
  } finally {
    row.busy = false;
  }
}

function showEnded(row, text, canResume) {
  const body = row.body;
  if (!body) return;
  body.querySelector('.cui-term-end').disabled = true;
  setStatus(row, text);
  body.querySelector('.cui-term-actions')?.remove();
  const again = el('button', { type: 'button', class: 'cui-term-btn', text: canResume ? 'Reconnect' : 'Start a new session' });
  again.addEventListener('click', () => show(row));
  const actions = el('div', { class: 'cui-term-actions' }, again);
  if (canResume) {
    const fresh = el('button', { type: 'button', class: 'cui-term-btn', text: 'New session' });
    fresh.addEventListener('click', async () => { await finish(row, 'quiet'); show(row); });
    actions.append(fresh);
  }
  body.querySelector('.cui-term-frame-box').append(actions);
}

// Ends the session: the frame first (so it does not reconnect and start another shell), then the shell itself.
async function finish(row, from) {
  const session = row.session;
  removeFrame(row);
  row.session = null;
  saveSession(row.agent.id, null);
  renderHeader(row);
  if (!session) return;
  if (from === 'button') { row.body.querySelector('.cui-term-end').disabled = true; setStatus(row, 'Ending the session…'); }
  const ended = await endSession(row.agent, session);
  if (from === 'button' && row.body) {
    showEnded(row, ended ? 'Session ended.' : ended === null ? 'Disconnected. The agent ends the session after a few idle minutes.'
      : 'The workspace did not confirm the end; the agent ends the session after a few idle minutes.', false);
  }
}

function open(row, body) {
  row.body = body;
  body.dataset.cuiMounted = '1';
  const end = el('input', { type: 'checkbox', class: 'cui-term-check' });
  end.checked = endOnClose();
  end.addEventListener('change', () => setEndOnClose(end.checked));
  const endNow = el('button', { type: 'button', class: 'cui-term-btn cui-term-danger cui-term-end', text: 'End session', disabled: true });
  endNow.addEventListener('click', () => finish(row, 'button'));
  body.replaceChildren(
    el('div', { class: 'cui-term-bar' },
      el('p', { class: 'cui-term-message', role: 'status', 'aria-live': 'polite', hidden: true }),
      el('span', { class: 'cui-term-spacer' }),
      el('label', { class: 'cui-term-option', title: 'Collapsing this drop-down (or leaving the page) ends the shell and everything running in it' },
        end, el('span', { text: 'End session when closed' })),
      endNow),
    el('div', { class: 'cui-term-frame-box' }));
  renderHeader(row);
  show(row);
}

function close(row) {
  row.body = null;
  removeFrame(row);
  if (row.session && endOnClose()) finish(row, 'close');
  renderHeader(row);
}

// Watches the drop-down: the trigger's data-state and the content element Coder mounts while it is open.
export function attach(section, agent) {
  if (!agent || !agent.id || !section) return;
  css();
  const row = rowState(agent);
  row.section = section;
  const sync = () => {
    // Only the agent's current row, while it is on the page: a row Coder re-rendered or unmounted keeps its DOM (and
    // this observer) for a while, and must not take the session back.
    if (row.section !== section) { observer.disconnect(); return; }
    if (!section.isConnected) return;
    const trigger = section.querySelector('[data-state]');
    const isOpen = trigger?.getAttribute('data-state') === 'open';
    const body = section.querySelector('.coder-ui-term-body');
    if (isOpen && body && row.body !== body) open(row, body);
    else if (!isOpen && row.body) close(row);
    else if (!isOpen) renderHeader(row);
  };
  const observer = new MutationObserver(sync);
  observer.observe(section, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-state'] });
  sync();
  // Leaving the workspace page inside the dashboard unmounts the row without closing the drop-down.
  const gone = setInterval(() => {
    if (section.isConnected) return;
    clearInterval(gone);
    observer.disconnect();
    if (row.section !== section) return;
    const hadBody = !!row.body;
    row.section = null;
    row.body = null;
    removeFrame(row);
    if (hadBody && row.session && endOnClose()) finish(row, 'close');
  }, 1500);
}
