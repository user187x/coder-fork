// Chat between admins and users, Gmail style: small windows docked at the bottom right of every Coder page, above the
// "Chats" panel, which rests on whatever sits at the bottom of the window (Coder's deployment stats footer, the
// classification marking). Windows are as tall as their user dragged them (the grip on the top edge), at most what fits
// between the navbar and the panel.
//
// An admin starts a conversation with "Chat" in Admin > Accounts (the rewritten users table calls openWith(user)); the
// user gets a window as soon as the message arrives, on whatever Coder page they have open, and answers there. Every
// window can be minimized to its title bar (click the title or "–") or closed ("×"); a closed conversation comes back
// when a new message arrives. The "Chats" panel at the far right slides up from its handle with the recent
// conversations, to reopen one; for everyone who is not an admin it is also their line to support, listing the admins
// to write to (even before any conversation). Every admin has an on/off switch next to the handle: developers write
// only to admins with chat on; when none is, their panel is greyed out and their message box to an admin who is off is
// disabled. "<name> is typing…" shows while the other side types. The ⋮ menu of a
// window takes a screenshot of the page (everything but the chat itself, classification marking included) and puts it
// in the message being written, to send or discard; and deletes the conversation, for both people (the service tells
// the other side's open window). Screenshots are drawn from the page itself (html2canvas, served with these files), so
// the browser asks nothing; content it cannot draw (e.g. a WebGL canvas) comes out blank.
//
// Unread messages are counted on the window's title bar, the panel's handle and, for everyone, on the navbar's inbox
// bell (the rewritten bell has an empty .coder-ui-chat-inbox; chat.css draws the count from <html>). A message counts
// as read once its window is used: clicked, typed in, or expanded; one that only popped up is still unread.
// Admins' messages are on the left in their own colour, developers' on the right (from the sender's role).
//
// The coder-ui-updates service keeps the messages (30 days) and decides who may write: admins (ADMIN_ROLES) to anyone,
// everyone else only to someone who wrote to them. This module keeps one long poll open (/api/chat/poll): the service
// answers it at once when a message or a typing change concerns this user. Open windows are remembered per tab
// (sessionStorage), so they survive reloads. dashboard.js loads this on every top-level Coder page.

const API = '/__coder-ui/api/chat/';
const OPEN_KEY = 'coder-ui-chat-windows';
const TYPING_EVERY_MS = 2500;   // renews the service's 6-second typing signal while keys are pressed
const RECENT_DAYS = 14;         // the panel lists conversations active this recently
const HEIGHT_KEY = 'coder-ui-chat-height';   // the message area's height the user last dragged to (this browser)
const DEFAULT_H = 380;
const MIN_H = 160;
const state = { me: null, cursor: 0, clearVersion: 0, typing: new Set(), convs: new Map(), wins: new Map(), admins: null,
  available: new Set(), off: new Set(), availVersion: 0 };   // admins with chat on / off (their switch)
let started = null;
let dock = null;
let wins = null;   // the row of windows, above the panel
let drawer = null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ICONS = {   // 24 x 24, stroked with currentColor (lucide-like, as Coder's own icons)
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  minimize: '<path d="M5 12h14"/>',
  expand: '<path d="m18 15-6-6-6 6"/>',
  close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  more: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
};

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

function icon(name, cls = '') {
  const span = el('span', { class: `cui-chat-svg ${cls}`.trim(), 'aria-hidden': 'true' });
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
  return span;
}

function css() {
  if (document.getElementById('coder-ui-chat-css')) return;
  document.head.append(el('link', { id: 'coder-ui-chat-css', rel: 'stylesheet', href: new URL('chat.css', import.meta.url).pathname }));
}

async function api(path, body) {
  const res = await fetch(API + path, body
    ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' }, body: JSON.stringify(body) }
    : { credentials: 'same-origin', cache: 'no-store' });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = new Error(out.error || `HTTP ${res.status}`);
    e.status = res.status;
    throw e;
  }
  return out;
}

const displayName = (peer) => peer.name || peer.username;

// The picture Coder would show for this user (uploaded avatar or the default avatar), else their initials.
function avatar(peer) {
  const map = window.__cuiAvatars || {};
  const url = Object.hasOwn(map, peer.username) ? map[peer.username]
    : window.__cuiAvatarDefault && window.__cuiAvatarUsers?.has(peer.username) ? window.__cuiAvatarDefault : null;
  const box = el('span', { class: 'cui-chat-avatar', 'aria-hidden': 'true' });
  if (url) box.append(el('img', { src: url, alt: '' }));
  else box.textContent = displayName(peer).split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
  return box;
}

function when(iso) {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// Admins on the left in their colour, developers on the right. Between two admins (or messages from before the
// service recorded roles): yours on the right.
function side(m) {
  if (m.fromAdmin !== m.toAdmin) return m.fromAdmin ? 'admin' : 'dev';
  return m.from === state.me.id ? 'dev' : 'admin';
}

// -------------------------------------------------------------------------------------------------- persistence
function saveOpen() {
  const list = [...state.wins.values()].map((w) => ({ id: w.peer.id, username: w.peer.username, name: w.peer.name, min: w.min }));
  try { sessionStorage.setItem(OPEN_KEY, JSON.stringify(list)); } catch { /* not remembered */ }
}
function loadOpen() {
  try { return JSON.parse(sessionStorage.getItem(OPEN_KEY) || '[]').filter((p) => p && p.id && p.username); } catch { return []; }
}

// -------------------------------------------------------------------------------------------------- dock and position
// The dock rests on whatever is fixed or sticky at the bottom of the window: the classification marking's bottom bar
// and Coder's deployment stats footer (which the marking pushes up). Both come and go (settings, pages, permissions),
// so their top edge is measured, not assumed: on scroll and resize, and twice a second for everything else. The same
// for the top (navbar, classification top bar): windows get at most the height between that and the panel.
const BOTTOM_BARS = '#coder-classification-bottom, div.sticky.bottom-0, div.fixed.bottom-0';
const TOP_BARS = '#coder-classification-top, div.sticky.top-0';
let inset = -1;
let room = -1;
function measureRoom() {
  if (!dock || !drawer) return;
  let top = 0;
  for (const b of document.querySelectorAll(TOP_BARS)) {
    if (dock.contains(b)) continue;
    const r = b.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.top < 160 && r.height < window.innerHeight / 3) top = Math.max(top, r.bottom);
  }
  const panelTop = drawer.root.hidden ? window.innerHeight - Math.max(inset, 0) : drawer.root.getBoundingClientRect().top;
  const avail = Math.max(0, Math.round(panelTop - top - 16));   // 8 px gap above the panel, 8 below the navbar
  if (avail !== room) {
    room = avail;
    dock.style.setProperty('--cui-chat-room', `${avail}px`);
  }
}
function measure() {
  if (!dock) return;
  const h = window.innerHeight;
  const bars = [...document.querySelectorAll(BOTTOM_BARS)]
    .filter((b) => !dock.contains(b))
    .map((b) => b.getBoundingClientRect())
    .filter((r) => r.width > 0 && r.height > 0 && r.top < h && r.height < h / 3)
    .sort((a, b) => b.bottom - a.bottom);
  let top = 0;   // occupied height, from the bottom edge up
  for (const r of bars) if (h - r.bottom <= top + 2) top = Math.max(top, h - r.top);   // stacked on the edge or on a bar
  top = Math.round(top);
  if (top !== inset) {
    inset = top;
    dock.style.setProperty('--cui-chat-bottom', `${top}px`);
  }
  measureRoom();
}
let measureQueued = false;
const queueMeasure = () => { if (!measureQueued) { measureQueued = true; requestAnimationFrame(() => { measureQueued = false; measure(); }); } };
window.addEventListener('resize', queueMeasure);
window.addEventListener('scroll', queueMeasure, { capture: true, passive: true });
setInterval(measure, 500);

function ensureDock() {
  if (dock?.isConnected) return dock;
  dock = el('div', { class: 'cui-chat-dock', 'aria-label': 'Chats' });
  inset = -1;
  room = -1;
  drawer = buildDrawer();
  wins = el('div', { class: 'cui-chat-wins' });
  dock.append(wins, drawer.root);
  document.body.append(dock);
  measure();
  return dock;
}

const maxOpen = () => Math.max(1, Math.min(3, Math.floor((window.innerWidth - 32) / 340)));

// -------------------------------------------------------------------------------------------------- the Chats panel
// A rectangular panel at the far right whose handle (chat icon, "Chats", unread count) stays on the footer; it slides
// up with the recent conversations.
function buildDrawer() {
  const count = el('span', { class: 'cui-chat-count', hidden: true });
  const handle = el('button', { type: 'button', class: 'cui-chat-handle', 'aria-expanded': 'false', 'aria-controls': 'cui-chat-list' },
    icon('chat', 'cui-chat-handle-icon'), el('span', { class: 'cui-chat-handle-text', text: 'Chats' }), count, icon('expand', 'cui-chat-chevron'));
  const list = el('div', { class: 'cui-chat-list', id: 'cui-chat-list', role: 'menu', 'aria-label': 'Recent chats' });
  // admins: their chat on / off, a switch beside the handle (outside the button, so it is a control of its own)
  const toggle = el('button', { type: 'button', class: 'cui-chat-switch', role: 'switch', 'aria-checked': 'true', hidden: true },
    el('span', { class: 'cui-chat-switch-track', 'aria-hidden': 'true' }, el('span', { class: 'cui-chat-switch-thumb' })));
  toggle.addEventListener('click', (e) => { e.stopPropagation(); setChatOn(!state.me.chatOn); });
  const root = el('div', { class: 'cui-chat-drawer', hidden: true },
    el('div', { class: 'cui-chat-drawer-body' }, el('div', { class: 'cui-chat-drawer-inner' }, list)),
    el('div', { class: 'cui-chat-handlebar' }, handle, toggle));
  handle.addEventListener('click', (e) => {
    e.stopPropagation();
    if (handle.getAttribute('aria-disabled') === 'true') return;   // no admin is taking chats
    setDrawer(!root.classList.contains('cui-chat-open'));
  });
  root.addEventListener('click', (e) => e.stopPropagation());
  // the panel's height changes as it slides: the windows above it get the room that is left
  root.addEventListener('transitionend', measureRoom);
  return { root, handle, list, count, toggle };
}

// An admin's switch: off = developers can't write to them (the service refuses; their name leaves developers' lists).
async function setChatOn(on) {
  const before = state.me.chatOn;
  state.me.chatOn = on;
  renderAvailability();
  try {
    applyAvailability(await api('availability', { enabled: on }));
  } catch {
    state.me.chatOn = before;
    renderAvailability();
  }
}

function applyAvailability(r) {
  if (!r || !Array.isArray(r.available)) return;
  if ((r.availVersion || 0) < state.availVersion) return;
  const changed = r.available.join() !== [...state.available].join() || r.off.join() !== [...state.off].join();
  state.availVersion = r.availVersion || state.availVersion;
  state.available = new Set(r.available);
  state.off = new Set(r.off);
  if (state.me?.admin && r.available.includes(state.me.id)) state.me.chatOn = true;
  if (state.me?.admin && r.off.includes(state.me.id)) state.me.chatOn = false;
  if (changed) { adminsAt = 0; state.admins = null; }   // the developers' list of admins is out of date
  renderAvailability();
}

// Everything that depends on who takes chats: the admin's switch, the developer's panel, each window's message box.
function renderAvailability() {
  if (!state.me) return;
  if (drawer) {
    const t = drawer.toggle;
    t.hidden = !state.me.admin;
    if (state.me.admin) {
      const on = state.me.chatOn;
      t.setAttribute('aria-checked', String(on));
      const say = on ? 'Chat is on: developers can message you. Click to turn it off.' : 'Chat is off: developers can\'t message you. Click to turn it on.';
      t.title = say;
      t.setAttribute('aria-label', say);
      drawer.root.classList.toggle('cui-chat-self-off', !on);
    }
    // developers: greyed out while no admin takes chats
    const none = !state.me.admin && state.available.size === 0;
    drawer.root.classList.toggle('cui-chat-unavailable', none);
    drawer.handle.setAttribute('aria-disabled', String(none));
    drawer.handle.title = none ? 'No admin is taking chats right now' : '';
    if (none && drawer.root.classList.contains('cui-chat-open')) setDrawer(false);
  }
  for (const w of state.wins.values()) renderWinAvailability(w);
  renderCounts();
}

function renderWinAvailability(w) {
  const blocked = !state.me.admin && state.off.has(w.peer.id);   // a developer writing to an admin who is off
  const selfOff = state.me.admin && !state.me.chatOn;
  w.el.classList.toggle('cui-chat-blocked', blocked);
  w.input.disabled = blocked;
  w.input.placeholder = blocked ? `${displayName(w.peer)} has turned chat off` : 'Message';
  w.el.querySelector('.cui-chat-send').disabled = blocked;
  w.notice.textContent = blocked ? `${displayName(w.peer)} isn't taking chats right now. Try again later or message another admin.`
    : selfOff ? "Your chat is off: developers can't write to you. Turn it on in the Chats panel." : '';
  w.notice.hidden = !w.notice.textContent;
}

function setDrawer(open) {
  if (!drawer) return;
  drawer.root.classList.toggle('cui-chat-open', open);
  drawer.handle.setAttribute('aria-expanded', String(open));
  requestAnimationFrame(measureRoom);
  if (open) {
    if (!state.me?.admin) loadAdmins();
    renderList();
    drawer.list.querySelector('button')?.focus({ preventScroll: true });
  }
}

document.addEventListener('click', () => { setDrawer(false); closeMenus(); });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (closeMenus()) return;
  if (drawer?.root.classList.contains('cui-chat-open')) { setDrawer(false); drawer.handle.focus(); }
});

function renderList() {
  if (!drawer) return;
  const convs = [...state.convs.values()].sort((a, b) => b.last.id - a.last.id).slice(0, 15);
  drawer.list.replaceChildren();
  for (const c of convs) {
    const mine = c.last.from === state.me.id;
    const item = el('button', { type: 'button', class: 'cui-chat-list-item', role: 'menuitem' },
      avatar(c.peer),
      el('span', { class: 'cui-chat-list-text' },
        el('span', { class: 'cui-chat-list-name', text: displayName(c.peer) }),
        el('span', { class: 'cui-chat-list-last', text: `${mine ? 'You: ' : ''}${c.last.text.replace(/\s+/g, ' ') || (c.last.image ? 'Screenshot' : '')}` })),
      c.unread ? el('span', { class: 'cui-chat-count', text: String(c.unread) }) : null);
    item.addEventListener('click', () => { setDrawer(false); markRead(openWin(c.peer, { min: false, focus: true }), true); });
    drawer.list.append(item);
  }
  if (state.me?.admin) {
    if (!convs.length) drawer.list.append(el('p', { class: 'cui-chat-list-empty', text: 'No conversations yet. Start one with "Chat" in Admin › Accounts.' }));
    return;
  }
  // everyone else: the admins they can write to, for help
  drawer.list.append(el('div', { class: 'cui-chat-list-head', text: 'Need help? Message an admin' }));
  const admins = state.admins;
  if (!admins) { drawer.list.append(el('p', { class: 'cui-chat-list-empty', text: 'Loading…' })); loadAdmins(); return; }
  if (!admins.length) drawer.list.append(el('p', { class: 'cui-chat-list-empty', text: 'No admin can be reached right now.' }));
  for (const a of admins) {
    const item = el('button', { type: 'button', class: 'cui-chat-list-item', role: 'menuitem' },
      avatar(a), el('span', { class: 'cui-chat-list-text' },
        el('span', { class: 'cui-chat-list-name', text: displayName(a) }), el('span', { class: 'cui-chat-list-last', text: `@${a.username} · admin` })));
    item.addEventListener('click', () => { setDrawer(false); markRead(openWin({ id: a.id, username: a.username, name: a.name }, { min: false, focus: true }), true); });
    drawer.list.append(item);
  }
}

let adminsAt = 0;
async function loadAdmins() {
  if (Date.now() - adminsAt < 60000 && state.admins) return;
  adminsAt = Date.now();
  try { state.admins = (await api('admins')).admins; } catch { state.admins = state.admins || []; }
  if (drawer?.root.classList.contains('cui-chat-open')) renderList();
}

// Unread counts: the panel's handle, the inbox bell (<html> attributes, drawn by chat.css).
function renderCounts() {
  const total = [...state.convs.values()].reduce((n, c) => n + c.unread, 0);
  const root = document.documentElement;
  if (total) {
    root.dataset.cuiChatUnread = String(total);
    root.style.setProperty('--cui-chat-unread', String(Math.min(total, 99)));
  } else {
    delete root.dataset.cuiChatUnread;
    root.style.removeProperty('--cui-chat-unread');
  }
  if (!drawer) return;
  const recent = [...state.convs.values()].some((c) => Date.now() - new Date(c.last.at).getTime() < RECENT_DAYS * 86400000);
  const hidden = !state.me;   // always there once signed in: admins start chats, everyone else reaches the admins
  if (drawer.root.hidden !== hidden) { drawer.root.hidden = hidden; requestAnimationFrame(measureRoom); }
  drawer.count.textContent = total > 99 ? '99+' : String(total);
  drawer.count.hidden = total === 0;
  drawer.handle.setAttribute('aria-label', total ? `Chats, ${total} unread` : 'Chats');
  const none = !state.me?.admin && state.available.size === 0;
  drawer.root.querySelector('.cui-chat-handle-text').textContent = none ? 'Chat unavailable' : state.me?.admin || recent ? 'Chats' : 'Chat with an admin';
  if (drawer.root.classList.contains('cui-chat-open')) renderList();
}

// -------------------------------------------------------------------------------------------------- windows
function openWin(peer, { min = false, focus = false } = {}) {
  ensureDock();
  let w = state.wins.get(peer.id);
  if (!w) {
    w = createWin(peer);
    state.wins.set(peer.id, w);
    wins.prepend(w.el);   // above the panel, rightmost; older windows move left
    const open = [...state.wins.values()];
    while (open.length > maxOpen()) closeWin(open.shift());   // the oldest gives way, like Gmail
    loadHistory(w);
  }
  setMin(w, min);
  if (focus && !min) w.input.focus();
  saveOpen();
  return w;
}

function closeWin(w) {
  w.el.remove();
  state.wins.delete(w.peer.id);
  saveOpen();
  renderCounts();
}

function setMin(w, min) {
  w.min = min;
  w.el.classList.toggle('cui-chat-min', min);
  w.title.setAttribute('aria-expanded', String(!min));
  w.minBtn.setAttribute('aria-label', min ? 'Expand' : 'Minimize');
  w.minBtn.title = min ? 'Expand' : 'Minimize';
  w.minBtn.replaceChildren(icon(min ? 'expand' : 'minimize'));
  if (!min) scrollDown(w, true);
  saveOpen();
}

function createWin(peer) {
  const title = el('button', { type: 'button', class: 'cui-chat-title', 'aria-expanded': 'true' },
    avatar(peer), el('span', { class: 'cui-chat-name', text: displayName(peer), title: `${displayName(peer)} (@${peer.username})` }),
    el('span', { class: 'cui-chat-count', hidden: true }));
  const moreBtn = el('button', { type: 'button', class: 'cui-chat-icon', 'aria-label': 'More', title: 'More', 'aria-haspopup': 'menu', 'aria-expanded': 'false' }, icon('more'));
  const minBtn = el('button', { type: 'button', class: 'cui-chat-icon', 'aria-label': 'Minimize', title: 'Minimize' }, icon('minimize'));
  const closeBtn = el('button', { type: 'button', class: 'cui-chat-icon', 'aria-label': 'Close', title: 'Close' }, icon('close'));
  const log = el('div', { class: 'cui-chat-log', role: 'log', 'aria-live': 'polite', 'aria-label': `Messages with ${displayName(peer)}` });
  const typing = el('div', { class: 'cui-chat-typing', hidden: true },
    el('span', { text: `${displayName(peer)} is typing` }), el('span', { class: 'cui-chat-dots', 'aria-hidden': 'true' }, el('i'), el('i'), el('i')));
  const input = el('textarea', { class: 'cui-chat-input', rows: '1', placeholder: 'Message', 'aria-label': `Message ${displayName(peer)}`, maxlength: '4000' });
  const send = el('button', { type: 'submit', class: 'cui-chat-send', 'aria-label': 'Send', title: 'Send (Enter)' }, icon('send'));
  const error = el('p', { class: 'cui-chat-error', role: 'alert', hidden: true });
  const attach = el('div', { class: 'cui-chat-attach', hidden: true });
  const notice = el('p', { class: 'cui-chat-notice', role: 'status', hidden: true });
  const form = el('form', { class: 'cui-chat-form' }, input, send);
  const grip = el('div', { class: 'cui-chat-grip', role: 'separator', 'aria-orientation': 'horizontal', tabindex: '0',
    'aria-label': 'Resize the chat (drag, or arrow keys; double-click resets)', title: 'Drag to resize' });
  const menu = el('div', { class: 'cui-chat-menu', role: 'menu', hidden: true });
  // a small gold lock floating in the top-right corner of the message area (it stays there while messages scroll, and
  // goes with the message area when the window is minimized): the chat travels over HTTPS (TLS) between the browser and
  // Coder. Only shown when the page itself was loaded over HTTPS.
  const TLS = 'Encrypted in transit: this chat travels over HTTPS (TLS) to Coder, where messages are kept for 30 days.';
  const lock = location.protocol === 'https:' ? el('span', { class: 'cui-chat-lock', role: 'img', 'aria-label': TLS, title: TLS }, icon('lock')) : null;
  const body = el('div', { class: 'cui-chat-body' }, el('div', { class: 'cui-chat-logwrap' }, log, lock), typing, notice, error, attach, form);
  const root = el('section', { class: 'cui-chat-win', role: 'dialog', 'aria-label': `Chat with ${displayName(peer)}` },
    grip, el('header', { class: 'cui-chat-head' }, title, moreBtn, minBtn, closeBtn), menu, body);
  const w = { peer, el: root, title, minBtn, moreBtn, menu, body, log, typingEl: typing, input, error, attach, notice, shot: null, min: false, ids: new Set(),
    loading: true, pending: [], lastIn: 0, readSent: 0, typedAt: 0, lastShown: null };
  setHeight(w, preferredHeight());
  if (state.me) queueMicrotask(() => renderWinAvailability(w));
  resizable(w, grip);
  moreBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleMenu(w); });
  menu.addEventListener('click', (e) => e.stopPropagation());

  // using the window reads it: expanding it, clicking in it, typing in it
  const toggle = () => { setMin(w, !w.min); if (!w.min) markRead(w, true); };
  title.addEventListener('click', toggle);
  minBtn.addEventListener('click', toggle);
  closeBtn.addEventListener('click', () => closeWin(w));
  root.addEventListener('pointerdown', () => { if (!w.min) markRead(w, true); });
  input.addEventListener('focus', () => markRead(w, true));
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
    if (input.value.trim() && Date.now() - w.typedAt > TYPING_EVERY_MS) {
      w.typedAt = Date.now();
      api('typing', { to: peer.id }).catch(() => {});
    }
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    if (e.key === 'Escape') { e.preventDefault(); setMin(w, true); }
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text && !w.shot) return;
    send.disabled = true;
    showError(w, '');
    try {
      const { message } = await api('send', { to: peer.id, text, ...(w.shot ? { image: w.shot.dataUrl, page: w.shot.page } : {}) });
      setShot(w, null);
      input.value = '';
      input.style.height = 'auto';
      w.typedAt = 0;
      receive(message);
    } catch (err) {
      showError(w, `Not sent: ${err.message}`);
    } finally {
      send.disabled = false;
      input.focus();
    }
  });
  return w;
}

// -------------------------------------------------------------------------------------------------- height
function preferredHeight() {
  try { return Number(localStorage.getItem(HEIGHT_KEY)) || DEFAULT_H; } catch { return DEFAULT_H; }
}
// The wanted height; CSS keeps it between MIN_H and the room above the panel (--cui-chat-room), so it adapts when the
// window, the footer, the classification marking or the open panel change what is free.
function setHeight(w, h) {
  w.wanted = Math.max(MIN_H, Math.round(h));
  w.body.style.setProperty('--cui-chat-h', `${w.wanted}px`);
}
function resizable(w, grip) {
  let startY = 0;
  let startH = 0;
  const done = () => { try { localStorage.setItem(HEIGHT_KEY, String(w.body.getBoundingClientRect().height)); } catch { /* not kept */ } };
  grip.addEventListener('pointerdown', (e) => {
    if (w.min || e.button !== 0) return;
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    startY = e.clientY;
    startH = w.body.getBoundingClientRect().height;   // what is shown, even if more was wanted
    w.el.classList.add('cui-chat-resizing');
  });
  grip.addEventListener('pointermove', (e) => {
    if (!grip.hasPointerCapture(e.pointerId)) return;
    setHeight(w, Math.min(startH + startY - e.clientY, Math.max(room - 44, MIN_H)));   // up = taller
    scrollDown(w, false);
  });
  const end = (e) => {
    if (!grip.hasPointerCapture(e.pointerId)) return;
    grip.releasePointerCapture(e.pointerId);
    w.el.classList.remove('cui-chat-resizing');
    done();
  };
  grip.addEventListener('pointerup', end);
  grip.addEventListener('pointercancel', end);
  grip.addEventListener('dblclick', () => { setHeight(w, DEFAULT_H); try { localStorage.removeItem(HEIGHT_KEY); } catch { /* fine */ } });
  grip.addEventListener('keydown', (e) => {
    const step = e.key === 'ArrowUp' ? 20 : e.key === 'ArrowDown' ? -20 : 0;
    if (!step || w.min) return;
    e.preventDefault();
    setHeight(w, Math.min(w.body.getBoundingClientRect().height + step, Math.max(room - 44, MIN_H)));
    done();
  });
}

// -------------------------------------------------------------------------------------------------- ⋮ menu
function closeMenus() {
  let closed = false;
  for (const w of state.wins.values()) {
    if (!w.menu.hidden) { w.menu.hidden = true; w.moreBtn.setAttribute('aria-expanded', 'false'); closed = true; }
  }
  return closed;
}

function toggleMenu(w) {
  const open = w.menu.hidden;
  closeMenus();
  if (!open) return;
  if (w.min) setMin(w, false);
  const shot = el('button', { type: 'button', class: 'cui-chat-menu-item', role: 'menuitem' }, icon('camera'), el('span', { text: 'Screenshot' }));
  shot.addEventListener('click', () => { closeMenus(); screenshot(w); });
  if (w.input.disabled) shot.disabled = true;
  const del = el('button', { type: 'button', class: 'cui-chat-delete', role: 'menuitem' }, icon('trash'), el('span', { text: 'Delete conversation' }));
  del.addEventListener('click', () => confirmDelete(w));
  w.menu.replaceChildren(shot, del);
  w.menu.hidden = false;
  w.moreBtn.setAttribute('aria-expanded', 'true');
  shot.focus();
}

// Permanent, for both people: asked once more inside the menu.
function confirmDelete(w) {
  const yes = el('button', { type: 'button', class: 'cui-chat-delete cui-chat-delete-confirm', text: 'Delete' });
  const no = el('button', { type: 'button', class: 'cui-chat-menu-cancel', text: 'Cancel' });
  const note = el('p', { class: 'cui-chat-menu-note', text: `Delete every message with ${displayName(w.peer)}, for both of you? This can't be undone.` });
  no.addEventListener('click', () => { closeMenus(); w.input.focus(); });
  yes.addEventListener('click', async () => {
    yes.disabled = no.disabled = true;
    try {
      await api('delete', { to: w.peer.id });
      closeMenus();
      cleared(w.peer.id, true);
    } catch (err) {
      note.textContent = `Not deleted: ${err.message}`;
      yes.disabled = no.disabled = false;
    }
  });
  w.menu.replaceChildren(note, el('div', { class: 'cui-chat-menu-actions' }, no, yes));
  no.focus();
}

// A conversation was deleted: by this user (its window closes) or by the other side (its window says so).
function cleared(peerId, byMe) {
  state.convs.delete(peerId);
  const w = state.wins.get(peerId);
  if (w) {
    if (byMe) closeWin(w);
    else {
      w.log.replaceChildren(el('p', { class: 'cui-chat-empty', text: `${displayName(w.peer)} deleted this conversation.` }));
      w.ids.clear();
      w.pending = [];
      w.lastIn = w.readSent = 0;
      w.lastShown = null;
      updateBadge(w);
    }
  }
  renderCounts();
}

// -------------------------------------------------------------------------------------------------- screenshot
// What the user sees, minus the chat dock: drawn from the page (html2canvas), so no browser prompt. WebP (else JPEG),
// made smaller until it fits the service's 4 MB.
const MAX_SHOT = 4 * 1024 * 1024;
let h2c = null;
function html2canvas() {
  h2c ??= window.html2canvas ? Promise.resolve(window.html2canvas) : new Promise((ok, bad) => {
    const s = el('script', { src: new URL('html2canvas.min.js', import.meta.url).pathname });
    s.onload = () => (window.html2canvas ? ok(window.html2canvas) : bad(new Error('html2canvas did not load')));
    s.onerror = () => { h2c = null; bad(new Error('html2canvas could not be loaded')); };
    document.head.append(s);
  });
  return h2c;
}

function encode(canvas) {
  for (let scale = 1; scale > 0.2; scale *= 0.75) {
    let c = canvas;
    if (scale < 1) {
      c = document.createElement('canvas');
      c.width = Math.round(canvas.width * scale);
      c.height = Math.round(canvas.height * scale);
      c.getContext('2d').drawImage(canvas, 0, 0, c.width, c.height);
    }
    let url = c.toDataURL('image/webp', 0.9);
    if (!url.startsWith('data:image/webp')) url = c.toDataURL('image/jpeg', 0.88);
    if ((url.length - url.indexOf(',') - 1) * 0.75 <= MAX_SHOT) return url;
  }
  throw new Error('the page is too large to send as a screenshot');
}

async function screenshot(w) {
  showError(w, '');
  w.attach.hidden = false;
  w.attach.replaceChildren(el('span', { class: 'cui-chat-attach-note', text: 'Taking a screenshot…' }));
  try {
    const render = await html2canvas();
    const canvas = await render(document.documentElement, {
      x: window.scrollX, y: window.scrollY, width: window.innerWidth, height: window.innerHeight,
      windowWidth: window.innerWidth, windowHeight: window.innerHeight,
      scale: Math.min(window.devicePixelRatio || 1, 1.5), useCORS: true, logging: false,
      backgroundColor: getComputedStyle(document.body).backgroundColor || null,
      ignoreElements: (node) => node.classList?.contains('cui-chat-dock'),
    });
    setShot(w, { dataUrl: encode(canvas), page: location.pathname + location.search });
    w.input.focus();
  } catch (err) {
    setShot(w, null);
    showError(w, `No screenshot: ${err.message}`);
  }
}

// The screenshot waiting in the message being written: shown above it, removable before sending.
function setShot(w, shot) {
  w.shot = shot;
  if (!shot) { w.attach.hidden = true; w.attach.replaceChildren(); return; }
  const remove = el('button', { type: 'button', class: 'cui-chat-attach-remove', 'aria-label': 'Remove the screenshot', title: 'Remove the screenshot' }, icon('close'));
  remove.addEventListener('click', () => { setShot(w, null); w.input.focus(); });
  const kb = Math.round((shot.dataUrl.length - shot.dataUrl.indexOf(',') - 1) * 0.75 / 1024);
  w.attach.replaceChildren(
    el('a', { class: 'cui-chat-attach-thumb', href: shot.dataUrl, target: '_blank', rel: 'noopener', title: 'Open full size' }, el('img', { src: shot.dataUrl, alt: 'Screenshot to send' })),
    el('span', { class: 'cui-chat-attach-note' }, el('strong', { text: 'Screenshot' }), el('span', { text: ` of ${shot.page} · ${kb} KB. Add a note and send, or remove it.` })),
    remove);
  w.attach.hidden = false;
}

function showError(w, text) {
  w.error.textContent = text;
  w.error.hidden = !text;
}

async function loadHistory(w) {
  try {
    const { messages, readUpTo } = await api(`messages?peer=${encodeURIComponent(w.peer.id)}`);
    w.readSent = Math.max(w.readSent, readUpTo);
    flush(w, messages);
    if (!w.ids.size) w.log.append(el('p', { class: 'cui-chat-empty', text: `Say hello to ${displayName(w.peer)}.` }));
  } catch (err) {
    flush(w, []);
    showError(w, `Could not load the conversation: ${err.message}`);
  }
  scrollDown(w, true);
  markRead(w, false);
}

// The history, then what arrived while it loaded, in order.
function flush(w, history) {
  w.loading = false;
  const all = [...history, ...w.pending].sort((a, b) => a.id - b.id);
  w.pending = [];
  for (const m of all) append(w, m);
}

function scrollDown(w, force) {
  const near = w.log.scrollHeight - w.log.scrollTop - w.log.clientHeight < 60;
  if (force || near) w.log.scrollTop = w.log.scrollHeight;
}

function append(w, m) {
  if (w.loading) { w.pending.push(m); return; }
  if (w.ids.has(m.id)) return;
  w.ids.add(m.id);
  w.log.querySelector('.cui-chat-empty')?.remove();
  const mine = m.from === state.me.id;
  const where = side(m);
  const t = new Date(m.at).getTime();
  const gap = !w.lastShown || t - w.lastShown > 10 * 60000;
  if (gap) w.log.append(el('div', { class: 'cui-chat-time', text: when(m.at) }));
  w.lastShown = t;
  const prev = w.log.lastElementChild;
  const grouped = !gap && prev?.classList.contains('cui-chat-msg') && prev.dataset.from === m.from;
  if (!grouped) w.log.append(el('div', { class: `cui-chat-who cui-chat-${where}`, text: mine ? 'You' : (m.fromDisplay || m.fromName) }));
  const bubble = el('div', { class: 'cui-chat-bubble' });
  if (m.image) {
    // the picture arrives after the message: keep the newest in view if the reader was at the bottom
    const img = el('img', { src: m.image, alt: `Screenshot${m.page ? ` of ${m.page}` : ''}` });
    const follow = mine || w.log.scrollHeight - w.log.scrollTop - w.log.clientHeight < 60;
    img.addEventListener('load', () => { if (follow) scrollDown(w, true); }, { once: true });
    bubble.append(el('a', { class: 'cui-chat-shot', href: m.image, target: '_blank', rel: 'noopener', title: 'Open full size' }, img));
    if (m.page) bubble.append(el('div', { class: 'cui-chat-shot-page', text: `Screenshot of ${m.page}` }));
  }
  if (m.text) bubble.append(el('div', { class: 'cui-chat-text', text: m.text }));
  w.log.append(el('div', { class: `cui-chat-msg cui-chat-${where}${mine ? ' cui-chat-mine' : ''}`, 'data-from': m.from, title: when(m.at) }, bubble));
  if (!mine) w.lastIn = Math.max(w.lastIn, m.id);
  scrollDown(w, mine);
}

function updateBadge(w) {
  const n = state.convs.get(w.peer.id)?.unread || 0;
  const badge = w.title.querySelector('.cui-chat-count');
  badge.textContent = String(n);
  badge.hidden = n === 0;
  w.el.classList.toggle('cui-chat-unread', n > 0);
}

// Read = the window is in use (used: clicked, typed in or expanded just now; otherwise it must hold the focus) in a
// visible tab. A window that only popped up leaves its messages unread: its title bar, the panel and the bell say so.
function markRead(w, used) {
  if (!w || w.min || document.visibilityState !== 'visible' || !w.el.isConnected) return;
  if (!used && !w.el.contains(document.activeElement)) return;
  const c = state.convs.get(w.peer.id);
  if (c) c.unread = 0;
  updateBadge(w);
  renderCounts();
  if (w.lastIn > w.readSent) {
    w.readSent = w.lastIn;
    api('read', { to: w.peer.id, last: w.lastIn }).catch(() => {});
  }
}

document.addEventListener('visibilitychange', () => { for (const w of state.wins.values()) markRead(w, false); });

// -------------------------------------------------------------------------------------------------- messages in
function receive(m) {
  state.cursor = Math.max(state.cursor, m.id);
  const incoming = m.to === state.me.id;
  const peer = incoming ? { id: m.from, username: m.fromName, name: m.fromDisplay } : { id: m.to, username: m.toName, name: m.toDisplay };
  const known = state.convs.get(peer.id);
  if (known?.last.id >= m.id) { const w = state.wins.get(peer.id); if (w) append(w, m); return; }   // already counted
  const conv = known || { peer, unread: 0, last: m };
  conv.peer = peer;
  conv.last = m;
  if (incoming) conv.unread += 1;
  state.convs.set(peer.id, conv);
  let w = state.wins.get(peer.id);
  if (incoming) {
    state.typing.delete(peer.id);
    // a new message brings its conversation up: opened if it was closed, left minimized (with its count) if it was
    if (!w) w = openWin(peer, { min: false });
  }
  if (w) {
    append(w, m);
    showTyping(w);
    updateBadge(w);
    markRead(w, false);
  }
  renderCounts();
}

function showTyping(w) {
  const on = state.typing.has(w.peer.id);
  if (w.typingEl.hidden === !on) return;
  w.typingEl.hidden = !on;
  if (on) scrollDown(w, false);
}

function setTyping(list) {
  state.typing = new Set(list);
  for (const w of state.wins.values()) showTyping(w);
}

// -------------------------------------------------------------------------------------------------- start / poll
async function poll() {
  let backoff = 2000;
  for (;;) {
    try {
      const q = new URLSearchParams({ after: String(state.cursor), typing: [...state.typing].sort().join(','), cleared: String(state.clearVersion), av: String(state.availVersion) });
      const r = await api(`poll?${q}`);
      backoff = 2000;
      state.clearVersion = Math.max(state.clearVersion, r.clearVersion || 0);
      for (const peerId of r.cleared || []) cleared(peerId, false);
      applyAvailability(r);
      for (const m of r.messages) receive(m);
      setTyping(r.typing);
    } catch (e) {
      if (e.status === 401) { signedOut(); return; }
      await sleep(backoff);
      backoff = Math.min(backoff * 2, 30000);
    }
  }
}

function signedOut() {
  dock?.remove();
  dock = drawer = null;
  state.me = null;
  state.convs.clear();
  state.wins.clear();
  renderCounts();
  delete document.documentElement.dataset.cuiChatAdmin;
  try { sessionStorage.removeItem(OPEN_KEY); } catch { /* nothing kept */ }
  started = null;
  setTimeout(start, 30000);   // signing in does not reload the page
}

async function boot() {
  css();
  let s;
  for (;;) {
    try { s = await api('state'); break; } catch (e) { await sleep(e.status === 401 ? 30000 : 10000); }
  }
  state.me = s.me;
  state.cursor = s.cursor;
  state.clearVersion = s.clearVersion || 0;
  state.availVersion = s.availVersion || 0;
  state.available = new Set(s.available || []);
  state.off = new Set(s.off || []);
  state.typing = new Set(s.typing);
  if (s.me.admin) document.documentElement.dataset.cuiChatAdmin = '';
  if (/^[0-9a-f-]{36}$/.test(s.me.id)) {   // no "Chat" button on your own row in Admin > Users
    document.getElementById('coder-ui-chat-self')?.remove();
    document.head.append(el('style', { id: 'coder-ui-chat-self', text: `.coder-ui-chat-user[data-user="${s.me.id}"]{display:none!important}` }));
  }
  for (const c of s.conversations) state.convs.set(c.peer.id, c);
  ensureDock();
  for (const p of loadOpen()) if (p.id !== s.me.id) openWin({ id: p.id, username: p.username, name: p.name || '' }, { min: !!p.min });
  // unread conversations come up minimized, with their count, after a reload or on the first page of the day
  for (const c of s.conversations) if (c.unread && !state.wins.has(c.peer.id)) openWin(c.peer, { min: true });
  for (const w of state.wins.values()) updateBadge(w);
  renderAvailability();   // also the counts
  poll();
}

export function start() {
  started ??= boot();
  return started;
}

// From Admin > Users: open (or bring up) the conversation with this user.
export async function openWith(user) {
  if (!user || !user.id || !user.username) return;
  await start();
  if (user.id === state.me.id) return;
  markRead(openWin({ id: user.id, username: user.username, name: user.name || '' }, { min: false, focus: true }), true);
}

// From the inbox bell's chat indicator: every conversation with unread messages, expanded; else the Chats panel.
export async function openUnread() {
  await start();
  const unread = [...state.convs.values()].filter((c) => c.unread).sort((a, b) => a.last.id - b.last.id);
  if (!unread.length) { ensureDock(); setDrawer(true); return; }
  let w = null;
  for (const c of unread) w = openWin(c.peer, { min: false });
  w.input.focus();
  for (const c of unread) markRead(state.wins.get(c.peer.id), true);
}
