// Runs inside the Banner admin page when it is embedded as Coder's Deployment > Announcement view (views.js adds it
// after the page's own scripts). The admin page itself is unchanged and keeps working stand-alone; this rearranges it
// and adds to it, and it drives the page's own form fields and buttons, so publishing, confirmations, errors and the
// live status keep working exactly as before:
//
//   top of "What developers see"   a visibility switch and an orange "Show again to everyone" button
//   Colors                         the four styles as swatches, plus custom background / text colours
//   Message                        Markdown with a Write / Preview toggle, the emoji picker and a live countdown
//                                  underneath; the lead-in and link fields are gone (Markdown covers both)
//   Acknowledgements               every announcement with who saw / confirmed it; each can be deleted
import {
  COLORS_RE, colorsToken, countdownToken, decorate, parseCountdown, render,
} from './banner-enhance.js';

const $ = (id) => document.getElementById(id);
const API = '/__coder-ui/api/';
const REFRESH_MS = 15000;
const LEVEL_COLOURS = { info: '#1e40af', success: '#166534', warning: '#b45309', critical: '#b91c1c' };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (v === true) node.setAttribute(k, '');
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

// Change one of the page's own fields the way typing would, so it recomputes "unpublished changes" and the preview.
function setField(id, value) {
  const field = $(id);
  if (!field || field.value === value) return;
  field.value = value;
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

function post(path, body) {
  return fetch(API + path, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' },
    body: JSON.stringify(body),
  }).then(async (res) => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  });
}

function toast(text, isError) {
  const t = $('toast');
  if (!t) return;
  t.textContent = text;
  t.classList.toggle('error', !!isError);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 4000);
}

const eyedropper = 'EyeDropper' in window;
function pickWithEyedropper(input) {
  new window.EyeDropper().open().then((r) => {
    input.value = r.sRGBHex.slice(0, 7);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }).catch(() => { /* cancelled */ });
}

// A colour control: swatch-style native picker (it has its own eyedropper in Chromium), an explicit eyedropper button
// where the browser supports one, and the hex value.
function colourControl(label, value, onChange) {
  const picker = el('input', { type: 'color', value, class: 'cui-color', 'aria-label': `${label} colour` });
  const hex = el('input', { type: 'text', value, class: 'cui-hex', maxlength: '7', spellcheck: 'false', 'aria-label': `${label} colour (hex)` });
  const drop = eyedropper ? el('button', { type: 'button', class: 'btn cui-drop', title: 'Pick a colour from the screen', 'aria-label': `Pick the ${label.toLowerCase()} colour from the screen`, text: '💧' }) : null;
  const set = (v, from) => {
    if (!/^#[0-9a-fA-F]{6}$/.test(v)) return;
    if (from !== picker) picker.value = v.toLowerCase();
    if (from !== hex) hex.value = v.toLowerCase();
    onChange(v.toLowerCase());
  };
  picker.addEventListener('input', () => set(picker.value, picker));
  hex.addEventListener('input', () => set(hex.value.trim(), hex));
  drop?.addEventListener('click', () => pickWithEyedropper(picker));
  const wrap = el('div', { class: 'cui-colour-control' }, el('span', { class: 'label', text: label }), picker, drop, hex);
  wrap.setValue = (v) => { picker.value = v; hex.value = v; };
  return wrap;
}

// ------------------------------------------------------------------ controls: visibility switch + "show again"
function controls() {
  const toggle = $('toggle');
  const reappear = $('reappear');
  const card = $('preview-h') && $('preview-h').closest('.card');
  if (!toggle || !reappear || !card) return;

  const sw = el('input', { type: 'checkbox', role: 'switch', id: 'cui-visible', 'aria-describedby': 'cui-visible-state' });
  const state = el('span', { class: 'cui-switch-state', id: 'cui-visible-state' });
  const switchLabel = el('label', { class: 'cui-switch', for: 'cui-visible' }, sw,
    el('span', { class: 'cui-track', 'aria-hidden': 'true' }, el('span', { class: 'cui-thumb' })),
    el('span', { class: 'cui-switch-text', text: 'Show banner' }), state);
  const force = el('button', { type: 'button', class: 'btn cui-force', id: 'cui-force', title: 'Force Banner Display',
    text: 'Show again to everyone' });

  const row = el('div', { class: 'cui-controls', role: 'group', 'aria-label': 'Banner visibility' }, switchLabel, force);
  card.prepend(row);
  const note = $('reappear-note');
  if (note) { note.classList.add('cui-note'); row.after(note); }

  const sync = () => {
    const on = toggle.textContent.trim() === 'Hide banner';
    sw.checked = on;
    sw.disabled = toggle.disabled;
    state.textContent = on ? 'On: developers see it' : 'Off: hidden from everyone';
    force.disabled = reappear.disabled;
  };
  new MutationObserver(sync).observe(toggle, { childList: true, characterData: true, subtree: true, attributes: true });
  new MutationObserver(sync).observe(reappear, { attributes: true });
  sync();
  sw.addEventListener('change', () => { sync(); toggle.click(); }); // the switch moves once the change is published
  force.addEventListener('click', () => reappear.click()); // the page asks for confirmation itself
}

// ------------------------------------------------------------------ Colors: swatches + custom colours
let colours = null;
function colorsSection() {
  const levels = $('levels');
  const fieldset = levels && levels.closest('fieldset');
  if (!fieldset) return;
  const legend = fieldset.querySelector('legend');
  if (legend) legend.textContent = 'Colors';
  levels.classList.add('cui-swatches');
  for (const label of levels.querySelectorAll('.level')) {
    const value = label.querySelector('input').value;
    label.title = `${label.textContent.trim()} (${LEVEL_COLOURS[value]})`;
  }
  const custom = el('input', { type: 'radio', name: 'cui-colour-mode', value: 'custom', id: 'cui-custom-swatch' });
  const customLabel = el('label', { class: 'level level-custom', title: 'Custom colours' }, custom, el('span', { text: 'Custom' }));
  levels.append(customLabel);

  let bg = '#1e40af';
  let fg = '#ffffff';
  const write = () => setField('title', colorsToken(bg, fg));
  const bgCtl = colourControl('Background', bg, (v) => { bg = v; write(); });
  const fgCtl = colourControl('Text', fg, (v) => { fg = v; write(); });
  const panel = el('div', { class: 'cui-custom-colours', hidden: true }, bgCtl, fgCtl);
  fieldset.append(panel);

  custom.addEventListener('change', () => {
    const level = document.querySelector('input[name=level]:checked');
    if (!COLORS_RE.test($('title').value)) bg = LEVEL_COLOURS[level ? level.value : 'info'];
    bgCtl.setValue(bg); fgCtl.setValue(fg);
    write();
  });
  // Picking one of the four styles again drops the custom colours.
  levels.addEventListener('change', (e) => {
    if (e.target.name === 'level' && COLORS_RE.test($('title').value)) setField('title', '');
  });
  // Follow the form (it is rewritten when the page loads or publishes).
  colours = () => {
    const m = COLORS_RE.exec($('title').value);
    custom.checked = !!m;
    levels.classList.toggle('cui-custom-on', !!m);
    panel.hidden = !m;
    if (m) { bg = m[1].toLowerCase(); fg = m[2].toLowerCase(); bgCtl.setValue(bg); fgCtl.setValue(fg); }
  };
  colours();
}

// ------------------------------------------------------------------ Message: toolbar (emoji, countdown, preview)
let refreshPreviewPane = () => {};
let syncCountdown = () => {};
function messageEditor() {
  const message = $('message');
  if (!message) return;
  const field = message.closest('.field');
  let caret = null;
  const remember = () => { caret = [message.selectionStart, message.selectionEnd]; };
  message.addEventListener('keyup', remember);
  message.addEventListener('click', remember);
  message.addEventListener('blur', remember);

  // Write / Preview
  const writeTab = el('button', { type: 'button', class: 'cui-tab', 'aria-pressed': 'true', text: 'Write' });
  const previewTab = el('button', { type: 'button', class: 'cui-tab', 'aria-pressed': 'false', text: 'Preview' });
  const pane = el('div', { class: 'cui-md-preview preview-frame', hidden: true, 'aria-live': 'polite' });
  const showPreview = (on) => {
    writeTab.setAttribute('aria-pressed', String(!on));
    previewTab.setAttribute('aria-pressed', String(on));
    message.hidden = on;
    pane.hidden = !on;
    if (on) refreshPreviewPane();
  };
  writeTab.addEventListener('click', () => { showPreview(false); message.focus(); });
  previewTab.addEventListener('click', () => showPreview(true));
  refreshPreviewPane = () => {
    if (pane.hidden || !window.__coderBannerPreview) return;
    const level = document.querySelector('input[name=level]:checked');
    const banner = window.__coderBannerPreview.build({
      enabled: true, level: level ? level.value : 'info', title: $('title').value, message: message.value,
      linkText: '', linkUrl: '', dismissible: $('dismissible').checked, effect: 'none',
    });
    pane.replaceChildren(banner);
    render(banner);
    decorate(banner);
  };

  // Emoji: the page's own picker, moved here from the (removed) lead-in field. It inserts into the message.
  const emojiBtn = $('emoji-btn');
  const emojiPop = $('emoji-pop');
  const countdownBtn = el('button', { type: 'button', class: 'btn cui-tool', 'aria-expanded': 'false', 'aria-controls': 'cui-countdown', text: '⏱ Countdown' });
  const tools = el('div', { class: 'cui-toolbar' },
    el('div', { class: 'cui-tools' }, emojiBtn, countdownBtn),
    el('span', { class: 'cui-md-hint', text: 'Markdown: **bold** *italic* ~~strike~~ `code` [link](https://…)' }),
    el('div', { class: 'cui-tabs', role: 'group', 'aria-label': 'Message view' }, writeTab, previewTab));
  if (emojiBtn) { emojiBtn.textContent = '😀 Emoji'; emojiBtn.classList.add('cui-tool'); }
  const holder = el('div', { class: 'cui-emoji-holder' }, tools);
  if (emojiPop) holder.append(emojiPop);
  message.after(pane);
  field.after(holder);

  // Countdown panel
  const when = el('input', { type: 'datetime-local', step: '60', id: 'cui-cd-when' });
  const format = el('select', { id: 'cui-cd-format' },
    el('option', { value: 'dhms', text: '2d 04h 12m 09s' }), el('option', { value: 'clock', text: '52:12:09' }),
    el('option', { value: 'words', text: '2 days, 4 hours' }));
  const done = el('input', { type: 'text', id: 'cui-cd-done', maxlength: '60', value: 'now' });
  const insert = el('button', { type: 'button', class: 'btn btn-primary', text: 'Insert at cursor' });
  const remove = el('button', { type: 'button', class: 'btn', text: 'Remove countdown' });
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const nudges = el('div', { class: 'cui-nudges', role: 'group', 'aria-label': 'Adjust the time' },
    ...[['−1h', -60], ['−15m', -15], ['+15m', 15], ['+1h', 60], ['+1d', 1440]].map(([t, min]) =>
      el('button', { type: 'button', class: 'chip', 'data-min': String(min), text: t })));
  const panel = el('div', { class: 'cui-countdown', id: 'cui-countdown', hidden: true },
    el('div', { class: 'cui-cd-grid' },
      el('label', { class: 'field' }, el('span', { class: 'label', text: `Counts down to (${zone})` }), when),
      el('label', { class: 'field' }, el('span', { class: 'label', text: 'Shown as' }), format),
      el('label', { class: 'field' }, el('span', { class: 'label', text: 'At zero, show' }), done)),
    nudges,
    el('div', { class: 'actions' }, insert, remove),
    el('p', { class: 'hint', text: 'The timer ticks live in the banner. Adjusting it here updates the countdown in the message; publish to send it out.' }));
  holder.append(panel);
  countdownBtn.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    countdownBtn.setAttribute('aria-expanded', String(!panel.hidden));
    if (!panel.hidden) syncCountdown();
  });

  const toLocalInput = (iso) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const toIso = (local) => {
    const d = new Date(local);
    return Number.isNaN(d.getTime()) ? null : d.toISOString().replace(/\.\d{3}Z$/, 'Z');
  };
  // The time input shows minutes; keep the exact target (seconds included) while the input still shows it.
  let exactTo = null;
  const target = () => (exactTo && toLocalInput(exactTo) === when.value ? exactTo : toIso(when.value));
  const token = () => {
    const to = target();
    return to && countdownToken({ to, format: format.value, done: done.value.trim() || 'now' });
  };
  syncCountdown = () => {
    const cd = parseCountdown(message.value);
    insert.textContent = cd ? 'Update countdown' : 'Insert at cursor';
    remove.hidden = !cd;
    if (cd && document.activeElement !== when && document.activeElement !== done) {
      exactTo = cd.to;
      when.value = toLocalInput(cd.to); format.value = cd.format; done.value = cd.done;
    } else if (!cd && !when.value) {
      const soon = new Date(Date.now() + 3600e3); soon.setMinutes(0, 0, 0);
      when.value = toLocalInput(soon.toISOString());
    }
  };
  const replaceToken = () => {
    const cd = parseCountdown(message.value);
    const t = token();
    if (!cd || !t) return;
    setField('message', message.value.slice(0, cd.index) + t + message.value.slice(cd.index + cd.length));
  };
  for (const input of [when, format, done]) input.addEventListener('input', replaceToken);
  nudges.addEventListener('click', (e) => {
    const min = Number(e.target.dataset.min);
    if (!min) return;
    const d = new Date(target() || Date.now());
    d.setMinutes(d.getMinutes() + min);
    exactTo = d.toISOString().replace(/\.\d{3}Z$/, 'Z');
    when.value = toLocalInput(exactTo);
    replaceToken();
  });
  insert.addEventListener('click', () => {
    const t = token();
    if (!t) { toast('Pick the date and time the countdown runs to.', true); return; }
    if (parseCountdown(message.value)) { replaceToken(); return; }
    const [a, b] = caret || [message.value.length, message.value.length];
    const before = message.value.slice(0, a);
    const after = message.value.slice(b);
    const spaced = (before && !/\s$/.test(before) ? ' ' : '') + t + (after && !/^\s/.test(after) ? ' ' : '');
    if ((before + spaced + after).length > Number(message.maxLength || 400)) { toast('The message would be too long for a countdown.', true); return; }
    setField('message', before + spaced + after);
    syncCountdown();
  });
  remove.addEventListener('click', () => {
    const cd = parseCountdown(message.value);
    if (!cd) return;
    setField('message', (message.value.slice(0, cd.index) + message.value.slice(cd.index + cd.length)).replace(/\s{2,}/g, ' ').trim());
    syncCountdown();
  });
}

// ------------------------------------------------------------------ previews + keeping our controls in step
let migrated = false;
function migrateLegacy() {
  // Banners published before this view had a lead-in and a link: fold them into the Markdown message once.
  if (migrated || !$('status') || /Loading/.test($('status').textContent)) return;
  migrated = true;
  const title = $('title').value.trim();
  const linkText = $('linkText').value.trim();
  const linkUrl = $('linkUrl').value.trim();
  if ((!title || COLORS_RE.test(title)) && !linkUrl && !linkText) return;
  let md = $('message').value.trim();
  if (title && !COLORS_RE.test(title)) { md = `**${title}** ${md}`; setField('title', ''); }
  if (linkUrl) md = `${md} [${linkText || linkUrl}](${linkUrl})`;
  setField('linkText', ''); setField('linkUrl', '');
  setField('message', md.slice(0, 400));
}

function previews() {
  const box = $('preview');
  if (!box) return;
  const apply = () => {
    const banner = box.querySelector('#coder-system-banner');
    if (banner && !banner.dataset.cui) { banner.dataset.cui = 'preview'; render(banner); decorate(banner); }
    migrateLegacy();
    colours?.();
    syncCountdown();
    refreshPreviewPane();
  };
  new MutationObserver(apply).observe(box, { childList: true, subtree: true });
  apply();
}

// ------------------------------------------------------------------ acknowledgements accordion
const openEvents = new Set();
let list, updated, timer = null;

function when(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function person(u) {
  const main = el('span', { class: 'cui-user', text: u.name || u.username });
  const sub = el('span', { class: 'cui-sub', text: u.name ? `${u.username}${u.email ? ' · ' + u.email : ''}` : (u.email || '') });
  return el('td', { class: 'cui-person' }, main, sub.textContent ? sub : null);
}

function badge(kind) {
  const text = { acked: 'Confirmed', viewed: 'Seen, not confirmed', pending: 'Not seen yet' }[kind];
  return el('span', { class: `cui-badge cui-${kind}`, text });
}

// The message as a one-line label: tokens shown as what they are.
function label(ev) {
  const m = ev.title && COLORS_RE.exec(ev.title);
  const text = (ev.message || '(no message)').replace(/\{\{countdown[^}]*\}\}/g, '⏱')
    .replace(/\[([^\]]+)\]\([^)\s]+\)/g, '$1').replace(/[*_~`]/g, '');
  return { text: (ev.title && !m ? ev.title + ' ' : '') + text, colour: m ? m[1] : LEVEL_COLOURS[ev.level] || LEVEL_COLOURS.info };
}

function eventRow(ev, totalUsers, again) {
  const details = el('details', { class: 'cui-event', 'data-id': ev.id });
  if (openEvents.has(ev.id) || (openEvents.size === 0 && ev.current)) details.open = true;
  details.addEventListener('toggle', () => (details.open ? openEvents.add(ev.id) : openEvents.delete(ev.id)));

  const total = totalUsers ?? ev.viewed;
  const pct = total ? Math.round((ev.acked / total) * 100) : 0;
  const { text, colour } = label(ev);
  const del = el('button', { type: 'button', class: 'cui-del', title: 'Delete this announcement and its confirmations',
    'aria-label': 'Delete this announcement and its confirmations', text: '✕' });
  del.addEventListener('click', async (e) => {
    e.preventDefault(); // inside <summary>: do not open / close the row
    e.stopPropagation();
    const what = ev.current ? 'This announcement is live: its confirmations start again from nobody.' : 'Its confirmations are deleted.';
    if (!window.confirm(`Delete the record of "${text.slice(0, 80)}"?\n\n${what}`)) return;
    try {
      await post('events/delete', { id: ev.id });
      openEvents.delete(ev.id);
      toast('Deleted.');
      refresh();
    } catch (err) {
      toast(err.message, true);
    }
  });
  const summary = el('summary', {},
    el('span', { class: 'cui-dot', style: `background:${colour}` }),
    el('span', { class: 'cui-msg', text }),
    again ? el('span', { class: 'pill cui-again', text: 'Shown again', title: 'The same announcement, re-shown to everyone' }) : null,
    ev.current ? el('span', { class: 'pill live cui-current', text: 'Live now' }) : null,
    el('span', { class: 'cui-counts', title: `${ev.acked} confirmed, ${ev.viewed} seen${totalUsers != null ? `, ${totalUsers} active users` : ''}` },
      el('span', { class: 'cui-meter', 'aria-hidden': 'true' }, el('span', { style: `width:${pct}%` })),
      document.createTextNode(`${ev.acked}${totalUsers != null ? ' / ' + totalUsers : ''} confirmed · ${ev.viewed} seen`)),
    el('span', { class: 'cui-date', text: when(ev.firstSeen) }),
    del);
  details.append(summary);

  const rows = [
    ...ev.receipts.map((r) => el('tr', {}, person(r), el('td', {}, badge(r.ackedAt ? 'acked' : 'viewed')),
      el('td', { text: when(r.viewedAt) }), el('td', { text: when(r.ackedAt) }))),
    ...(ev.pending || []).map((u) => el('tr', { class: 'cui-pending' }, person(u), el('td', {}, badge('pending')),
      el('td', { text: '—' }), el('td', { text: '—' }))),
  ];
  const table = el('table', { class: 'cui-table' },
    el('thead', {}, el('tr', {}, ...['Person', 'Status', 'Seen', 'Confirmed'].map((h) => el('th', { scope: 'col', text: h })))),
    el('tbody', {}, ...(rows.length ? rows : [el('tr', {}, el('td', { colspan: '4', class: 'cui-empty', text: 'Nobody has seen this announcement yet.' }))])));
  details.append(el('div', { class: 'cui-table-wrap' }, table));
  return details;
}

async function refresh() {
  clearTimeout(timer);
  try {
    const res = await fetch(API + 'acks', { credentials: 'same-origin', cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    list.replaceChildren(...(data.events.length
      ? data.events.map((ev, i) => eventRow(ev, data.totalUsers, data.events.slice(i + 1).some((o) =>
        o.level === ev.level && o.title === ev.title && o.message === ev.message)))
      : [el('p', { class: 'hint', text: 'No announcement has been shown yet. Publish one and confirmations appear here.' })]));
    updated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    updated.textContent = `Could not load confirmations: ${e.message}`;
  }
  timer = setTimeout(() => (document.hidden ? (timer = setTimeout(refresh, REFRESH_MS)) : refresh()), REFRESH_MS);
}

function refreshSoon() { setTimeout(refresh, 800); }

function acknowledgements() {
  const anchor = document.querySelector('section.meta') || document.querySelector('main');
  if (!anchor) return;
  updated = el('span', { class: 'meta cui-updated', 'aria-live': 'polite' });
  list = el('div', { class: 'cui-events' }, el('p', { class: 'hint', text: 'Loading…' }));
  const section = el('section', { class: 'card cui-acks', 'aria-labelledby': 'cui-acks-h' },
    el('div', { class: 'card-head' }, el('h2', { id: 'cui-acks-h', text: 'Acknowledgements' }), updated),
    el('p', { class: 'hint cui-intro', text: 'Each announcement you publish (or show again) is listed with everyone who has seen it and who pressed Confirm. Open one to see the people.' }),
    list);
  if (anchor.tagName === 'MAIN') anchor.append(section); else anchor.before(section);
  const t = $('toast');
  if (t) new MutationObserver(() => { if (!t.hidden) refreshSoon(); }).observe(t, { attributes: true, attributeFilter: ['hidden'] });
  refresh();
}

controls();
colorsSection();
messageEditor();
previews();
acknowledgements();
