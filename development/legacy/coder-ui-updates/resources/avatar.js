// User menu > "Avatar": pick any picture as your own avatar. The browser crops it to its centre square and scales it
// to 256 px (WebP, else PNG), so every format the browser can show works and the stored file stays small. A GIF is
// uploaded as it is (up to 2 MB) so it stays animated; the circle crop is done by CSS (object-fit: cover). The
// coder-ui-updates service keeps it in Coder's own database (coder_ui_updates.avatar). Coder's avatar component
// (rewritten Avatar chunk) shows it wherever Coder shows that user's avatar by username (user menu, workspace
// lists, ...) as a circle inside Coder's rounded square.
const API = '/__coder-ui/api/avatar';
const SIZE = 256;
const MAX_INPUT = 25 * 1024 * 1024;
const MAX_GIF = 2 * 1024 * 1024;
let dialog = null;
let state = { username: '', current: null, next: null };

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
  if (document.getElementById('coder-ui-avatar-css')) return;
  const link = el('link', { id: 'coder-ui-avatar-css', rel: 'stylesheet', href: new URL('avatar.css', import.meta.url).pathname });
  document.head.append(link);
}

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' },
    body: JSON.stringify(body),
  });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
  return out;
}

const asDataUrl = (file) => new Promise((ok, bad) => {
  const r = new FileReader();
  r.onload = () => ok(r.result);
  r.onerror = () => bad(r.error);
  r.readAsDataURL(file);
});

// An animated GIF as it is (checked that the browser can show it), any other picture -> centre square, SIZE x SIZE.
async function prepare(file) {
  if (file.type === 'image/gif') {
    if (file.size > MAX_GIF) throw new RangeError('GIFs are kept as they are, so they must be under 2 MB.');
    const url = await asDataUrl(file);
    const img = new Image();
    img.src = url;
    await img.decode();
    return url;
  }
  return square(file);
}

async function square(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.decoding = 'async';
    img.src = url;
    await img.decode();
    const w = img.naturalWidth || SIZE;
    const h = img.naturalHeight || SIZE;
    const side = Math.min(w, h);
    const canvas = document.createElement('canvas');
    canvas.width = SIZE;
    canvas.height = SIZE;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, (w - side) / 2, (h - side) / 2, side, side, 0, 0, SIZE, SIZE);
    const webp = canvas.toDataURL('image/webp', 0.9);
    return webp.startsWith('data:image/webp') ? webp : canvas.toDataURL('image/png');
  } finally {
    URL.revokeObjectURL(url);
  }
}

function preview(src, initials) {
  const box = dialog.querySelector('.cui-av-preview');
  box.replaceChildren(src ? el('img', { src, alt: '' }) : el('span', { text: initials }));
  box.classList.toggle('coder-ui-avatar', !!src);
}

function build() {
  css();
  const file = el('input', { type: 'file', accept: 'image/*', id: 'cui-av-file', class: 'cui-av-file' });
  dialog = el('dialog', { class: 'cui-av-dialog', 'aria-labelledby': 'cui-av-h' },
    el('form', { method: 'dialog' },
      el('h2', { id: 'cui-av-h', text: 'Your avatar' }),
      el('p', { class: 'cui-av-hint', text: 'Any picture works, animated GIFs too: it is shown as a circle wherever Coder shows you.' }),
      el('div', { class: 'cui-av-row' },
        el('span', { class: 'cui-av-preview', 'aria-hidden': 'true' }),
        el('div', { class: 'cui-av-pick' },
          el('label', { for: 'cui-av-file', class: 'cui-av-btn', text: 'Choose a picture…' }),
          file,
          el('span', { class: 'cui-av-name', 'aria-live': 'polite' }))),
      el('p', { class: 'cui-av-error', role: 'alert', hidden: true }),
      el('div', { class: 'cui-av-actions' },
        el('button', { type: 'button', class: 'cui-av-btn cui-av-remove', text: 'Remove avatar', hidden: true }),
        el('span', { class: 'cui-av-spacer' }),
        el('button', { type: 'button', class: 'cui-av-btn cui-av-cancel', text: 'Cancel' }),
        el('button', { type: 'button', class: 'cui-av-btn cui-av-primary cui-av-save', text: 'Save', disabled: true }))));
  document.body.append(dialog);

  const error = (text) => { const e = dialog.querySelector('.cui-av-error'); e.textContent = text || ''; e.hidden = !text; };
  const busy = (on) => dialog.querySelectorAll('button').forEach((b) => { b.disabled = on || (b.classList.contains('cui-av-save') && !state.next); });

  file.addEventListener('change', async () => {
    error('');
    const f = file.files && file.files[0];
    if (!f) return;
    dialog.querySelector('.cui-av-name').textContent = f.name;
    if (f.size > MAX_INPUT) { error('That file is larger than 25 MB.'); return; }
    try {
      state.next = await prepare(f);
      preview(state.next, '');
      busy(false);
    } catch (e) {
      state.next = null;
      busy(false);
      error(e instanceof RangeError ? e.message : 'This browser cannot read that file as a picture. Try a PNG, JPEG, WebP, GIF or SVG.');
    }
  });
  dialog.querySelector('.cui-av-cancel').addEventListener('click', () => dialog.close());
  const done = (url) => {
    window.__cuiAvatars = { ...(window.__cuiAvatars || {}) };
    if (url) window.__cuiAvatars[state.username] = url; else delete window.__cuiAvatars[state.username];
    dialog.querySelector('.cui-av-name').textContent = 'Saved. Updating the page…';
    setTimeout(() => window.location.reload(), 400);   // Coder's avatars re-render with the new picture
  };
  dialog.querySelector('.cui-av-save').addEventListener('click', async () => {
    if (!state.next) return;
    busy(true); error('');
    try { done((await post(API, { dataUrl: state.next })).url); } catch (e) { error(`Not saved: ${e.message}`); busy(false); }
  });
  dialog.querySelector('.cui-av-remove').addEventListener('click', async () => {
    busy(true); error('');
    try { await post(`${API}/reset`, {}); done(null); } catch (e) { error(`Not removed: ${e.message}`); busy(false); }
  });
}

export async function open() {
  if (!dialog) build();
  state = { username: '', current: null, next: null };
  dialog.querySelector('.cui-av-file').value = '';
  dialog.querySelector('.cui-av-name').textContent = '';
  dialog.querySelector('.cui-av-error').hidden = true;
  dialog.querySelector('.cui-av-save').disabled = true;
  preview(null, '…');
  if (!dialog.open) dialog.showModal();
  try {
    const res = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
    const me = await res.json();
    if (!res.ok) throw new Error(me.error || `HTTP ${res.status}`);
    state.username = me.username;
    state.current = me.url;
    // no picture of their own: the default avatar (if one is installed) is what everyone sees
    preview(me.url || me.defaultUrl, (me.username || '?').slice(0, 2).toUpperCase());
    dialog.querySelector('.cui-av-remove').hidden = !me.set;
    if (!me.available) {
      dialog.querySelector('.cui-av-error').textContent = 'Avatars are not available: the service is not connected to Coder\'s database.';
      dialog.querySelector('.cui-av-error').hidden = false;
      dialog.querySelector('.cui-av-pick label').setAttribute('aria-disabled', 'true');
    }
  } catch (e) {
    dialog.querySelector('.cui-av-error').textContent = `Could not read your avatar: ${e.message}`;
    dialog.querySelector('.cui-av-error').hidden = false;
  }
}
