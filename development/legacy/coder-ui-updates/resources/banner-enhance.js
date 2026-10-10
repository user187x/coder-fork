// Everything coder-ui-updates adds to the coder-banner announcement, applied to the banner's DOM after coder-banner has
// drawn it (by ack.js on every Coder page, by admin-embed.js on the admin page's previews, so both look the same):
//
//   Confirm      the "×" becomes a "Confirm" button (undismissable banners get one that leaves the banner in place)
//   Markdown     **bold**  *italic*  ~~strike~~  `code`  [text](https://… or /path)
//   Countdown    {{countdown to=2026-10-04T02:00:00Z format=dhms done="now"}} anywhere in the message ticks live
//   Colours      custom background / text colours, carried in the banner's (otherwise unused) lead-in field as
//                {{colors bg=#rrggbb fg=#rrggbb}}
//
// coder-banner itself stores and delivers the text unchanged (so its live push, ids and dismissals keep working); it
// is only drawn differently here. All text is inserted with textContent / createTextNode, never as HTML.

const BUTTON_STYLE = [
  'flex-shrink:0', 'box-sizing:border-box', 'padding:3px 14px', 'border-radius:6px', 'cursor:pointer',
  'border:1px solid rgba(255,255,255,.6)', 'background:rgba(255,255,255,.16)', 'color:inherit',
  'font:inherit', 'font-size:13px', 'font-weight:600', 'line-height:18px', 'white-space:nowrap',
].join(';');

export const COLORS_RE = /^\{\{colors bg=(#[0-9a-fA-F]{6}) fg=(#[0-9a-fA-F]{6})\}\}$/;
export const COUNTDOWN_RE = /\{\{countdown\s+to=([0-9T:.\-+Z]+)(?:\s+format=(dhms|clock|words))?(?:\s+done="([^"]{0,80})")?\s*\}\}/;
const COUNTDOWN_ALL = new RegExp(COUNTDOWN_RE.source, 'g');

export function colorsToken(bg, fg) { return `{{colors bg=${bg} fg=${fg}}}`; }

export function countdownToken({ to, format = 'dhms', done = 'now' }) {
  const safeDone = String(done).replace(/["{}]/g, '').slice(0, 80);
  return `{{countdown to=${to} format=${format} done="${safeDone}"}}`;
}

export function parseCountdown(text) {
  const m = COUNTDOWN_RE.exec(text || '');
  return m ? { to: m[1], format: m[2] || 'dhms', done: m[3] ?? 'now', index: m.index, length: m[0].length } : null;
}

// ------------------------------------------------------------------ countdown
const pad = (n) => String(n).padStart(2, '0');

export function formatRemaining(ms, format) {
  let s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  if (format === 'clock') return `${pad(d * 24 + h)}:${pad(m)}:${pad(s)}`;
  if (format === 'words') {
    const parts = [[d, 'day'], [h, 'hour'], [m, 'minute'], [s, 'second']].filter(([n]) => n > 0).slice(0, 2);
    return parts.length ? parts.map(([n, u]) => `${n} ${u}${n === 1 ? '' : 's'}`).join(', ') : '0 seconds';
  }
  return (d ? `${d}d ` : '') + `${pad(h)}h ${pad(m)}m ${pad(s)}s`;
}

function tick(root) {
  for (const el of root.querySelectorAll('[data-cui-countdown]')) {
    const left = Date.parse(el.dataset.cuiCountdown) - Date.now();
    const text = Number.isNaN(left) ? '(invalid date)' : left > 0 ? formatRemaining(left, el.dataset.cuiFormat) : el.dataset.cuiDone;
    if (el.textContent !== text) el.textContent = text;
  }
}

let ticking = false;
function startTicker() {
  if (ticking) return;
  ticking = true;
  setInterval(() => tick(document), 1000);
}

function countdownNode(m) {
  const span = document.createElement('span');
  span.dataset.cuiCountdown = m[1];
  span.dataset.cuiFormat = m[2] || 'dhms';
  span.dataset.cuiDone = m[3] ?? 'now';
  span.style.cssText = 'font-variant-numeric:tabular-nums;font-weight:700;white-space:nowrap';
  span.setAttribute('role', 'timer');
  startTicker();
  return span;
}

// ------------------------------------------------------------------ markdown (inline subset)
function safeHref(url) {
  return /^(https?:\/\/|\/(?!\/))/i.test(url) ? url : '';
}

const INLINE = [
  { re: /^\{\{countdown\s[^}]*\}\}/, node: (m) => { const c = COUNTDOWN_RE.exec(m[0]); return c ? countdownNode(c) : document.createTextNode(m[0]); } },
  { re: /^`([^`]+)`/, node: (m) => el('code', 'font-family:ui-monospace,monospace;background:rgba(255,255,255,.18);padding:0 4px;border-radius:4px', [document.createTextNode(m[1])]) },
  { re: /^\*\*(.+?)\*\*/, tag: 'strong' },
  { re: /^__(.+?)__/, tag: 'strong' },
  { re: /^~~(.+?)~~/, tag: 's' },
  { re: /^\*([^*\s](?:[^*]*[^*\s])?)\*/, tag: 'em' },
  { re: /^_([^_\s](?:[^_]*[^_\s])?)_(?![A-Za-z0-9])/, tag: 'em' },
  { re: /^\[([^\]]+)\]\(([^)\s]+)\)/, node: (m) => {
    const href = safeHref(m[2]);
    if (!href) return document.createTextNode(m[0]);
    const a = el('a', 'color:inherit;text-decoration:underline;font-weight:600', inline(m[1]));
    a.href = href;
    if (/^https?:/i.test(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    return a;
  } },
];

function el(tag, style, children) {
  const node = document.createElement(tag);
  if (style) node.style.cssText = style;
  node.append(...children);
  return node;
}

export function inline(text) {
  const out = [];
  let plain = '';
  let i = 0;
  const flush = () => { if (plain) { out.push(document.createTextNode(plain)); plain = ''; } };
  outer: while (i < text.length) {
    const rest = text.slice(i);
    // a word character right before * or _ means it is part of a word (snake_case, 2*3), not emphasis
    for (const rule of INLINE) {
      const m = rule.re.exec(rest);
      if (!m) continue;
      if ((rest[0] === '_' || rest[0] === '*') && rule.tag === 'em' && /[A-Za-z0-9]/.test(text[i - 1] || '')) continue;
      flush();
      out.push(rule.node ? rule.node(m) : el(rule.tag, '', inline(m[1])));
      i += m[0].length;
      continue outer;
    }
    if (rest[0] === '\\' && /[\\`*_~[\]{}()]/.test(rest[1] || '')) { plain += rest[1]; i += 2; continue; }
    plain += rest[0];
    i += 1;
  }
  flush();
  return out;
}

// ------------------------------------------------------------------ the Confirm button
export function markConfirmed(button) {
  button.dataset.cuiDone = '1';
  button.textContent = 'Confirmed ✓';
  button.disabled = true;
  button.style.cursor = 'default';
  button.style.opacity = '.8';
  button.setAttribute('aria-label', 'You confirmed this announcement');
}

// Returns { button, dismissible }. Safe to call again (finds its own button, keeps one click handler).
// `onConfirm` runs on click, after the banner's own dismiss handler, if any.
export function decorate(banner, { acked = false, onConfirm = null } = {}) {
  let button = banner.querySelector('button[data-cui-confirm]');
  let dismissible = button ? button.dataset.cuiConfirm === 'dismiss' : false;
  if (!button) {
    button = banner.querySelector('button[aria-label="Dismiss announcement"]');
    dismissible = !!button;
  }
  if (!button) {
    button = document.createElement('button');
    button.type = 'button';
    banner.appendChild(button);
  }
  button.dataset.cuiConfirm = dismissible ? 'dismiss' : 'stay';
  button.__cuiOnConfirm = onConfirm;
  if (!button.disabled) {
    button.textContent = 'Confirm';
    button.title = 'Confirm that you have read this announcement';
    button.setAttribute('aria-label', 'Confirm that you have read this announcement');
    button.style.cssText = BUTTON_STYLE;
  }
  if (!dismissible && acked) markConfirmed(button);
  if (!button.dataset.cuiBound) {
    button.dataset.cuiBound = '1';
    button.addEventListener('click', () => {
      if (button.dataset.cuiDone) return;
      if (button.__cuiOnConfirm) button.__cuiOnConfirm();
      if (button.dataset.cuiConfirm === 'stay') markConfirmed(button);
    });
  }
  return { button, dismissible };
}

// ------------------------------------------------------------------ the whole banner
// Colours + Markdown + countdown. Idempotent: the text is rendered once per banner element.
export function render(banner) {
  const text = banner.querySelector('[data-banner-text]');
  if (!text || text.dataset.cuiMd) return;
  text.dataset.cuiMd = '1';
  const title = text.querySelector(':scope > strong');
  const colors = title && COLORS_RE.exec(title.textContent.trim());
  if (colors) {
    banner.style.background = colors[1];
    banner.style.color = colors[2];
  }
  // coder-banner writes: [<strong>lead-in</strong> ]message[ <a>link</a>]. Only the message is Markdown.
  const message = [...text.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent).join('').trim();
  const keep = [...text.childNodes].filter((n) => n.nodeType === Node.ELEMENT_NODE && n !== title);
  const nodes = [];
  if (title && !colors) nodes.push(title, document.createTextNode(' '));
  nodes.push(...inline(message));
  for (const k of keep) nodes.push(document.createTextNode(' '), k);
  text.replaceChildren(...nodes);
  tick(banner);
}

export function hasCountdown(text) { return COUNTDOWN_ALL.test(text || '') && !(COUNTDOWN_ALL.lastIndex = 0); }
