// General > Change Logo: shows the logo in use and uploads a replacement (stored in Coder's database by the
// coder-ui-updates service; boot.js hands it to every page so the dashboard's logo component shows it).
const API = '/__coder-ui/api/logo';
const $ = (id) => document.getElementById(id);
const MAX = 1024 * 1024;
const TYPES = ['image/png', 'image/jpeg', 'image/svg+xml', 'image/webp', 'image/gif'];
// Coder's own mark (the dashboard's logo component), drawn in the current text colour.
const CODER_MARK = '<svg viewBox="0 0 120 60" fill="currentColor" aria-label="Coder logo"><path d="M34.5381 0C54.5335 6.29882e-05 65.7432 10.1355 66.122 25.0544L48.853 25.6216C48.3984 17.3514 41.544 11.9189 34.5381 12.0809C24.919 12.2836 17.7989 19.1351 17.7988 29.9999C17.7988 40.8648 24.919 47.5951 34.5381 47.5951C41.544 47.5945 48.2468 42.4055 49.0043 34.1352L66.2733 34.5408C65.8189 49.7027 53.9276 60 34.5381 60C15.1484 60 0 48.2433 0 29.9999C7.1014e-05 11.6757 14.5426 0 34.5381 0ZM120 1.7728V58.5299H74.5559V1.7728H120Z"/></svg>';

let pending = null; // {dataUrl, type, size, name}
const kb = (n) => (n < 1024 ? `${n} bytes` : `${Math.round(n / 1024)} KB`);

function toast(text, isError) {
  const t = $('toast');
  t.textContent = text;
  t.classList.toggle('error', !!isError);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 5000);
}

function show(slot, src) {
  const node = $(slot);
  node.replaceChildren();
  if (src) {
    const img = document.createElement('img');
    img.src = src;
    img.alt = 'Logo';
    node.append(img);
  } else {
    node.innerHTML = CODER_MARK; // constant markup above, no user data
  }
}

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function load() {
  const res = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  if (!data.available) {
    $('state').textContent = 'Not available';
    $('meta').textContent = "The service is not connected to Coder's database, so a logo cannot be stored.";
    $('open').disabled = true;
    return;
  }
  $('state').textContent = data.set ? 'Custom logo' : "Coder's logo";
  $('state').className = `pill ${data.set ? 'ok' : 'info'}`;
  show('p-dark', data.url); show('p-light', data.url);
  $('reset').hidden = !data.set;
  $('meta').textContent = data.set
    ? `${data.type.replace('image/', '').toUpperCase()}, ${kb(data.bytes)} · uploaded by ${data.updatedBy} on ${new Date(data.updatedAt).toLocaleString()}`
    : 'No custom logo: Coder shows its own.';
}

function pick(file) {
  pending = null;
  $('save').disabled = true;
  $('new-previews').hidden = true;
  if (!file) return;
  if (!TYPES.includes(file.type)) { $('file-info').textContent = `${file.name}: not a PNG, JPEG, SVG, WebP or GIF image.`; return; }
  if (file.size > MAX) { $('file-info').textContent = `${file.name} is ${(file.size / 1048576).toFixed(1)} MB; the limit is 1 MB.`; return; }
  const reader = new FileReader();
  reader.onload = () => {
    pending = { dataUrl: reader.result, type: file.type, size: file.size, name: file.name };
    for (const slot of ['n-dark', 'n-light', 'n-signin']) show(slot, reader.result);
    $('new-previews').hidden = false;
    $('drop-text').innerHTML = '';
    $('drop-text').append(Object.assign(document.createElement('b'), { textContent: file.name }), ' · choose another or drop it here');
    $('file-info').textContent = `${kb(file.size)}. This is how it will look.`;
    $('save').disabled = false;
  };
  reader.readAsDataURL(file);
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
  pick(null);
  $('file').value = '';
  $('file-info').textContent = '';
  $('drop-text').innerHTML = '<b>Choose an image</b> or drop it here<br><small>PNG, JPEG, SVG, WebP or GIF · up to 1 MB · a wide logo about 3–4× as wide as it is tall works best</small>';
  $('dialog').showModal();
  revealDialog();
}

$('open').addEventListener('click', openDialog);
$('cancel').addEventListener('click', () => $('dialog').close());
$('file').addEventListener('change', () => pick($('file').files[0]));
const drop = $('drop');
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); pick(e.dataTransfer.files[0]); });
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); } });

$('save').addEventListener('click', async () => {
  if (!pending) return;
  $('save').disabled = true;
  try {
    await post(API, { dataUrl: pending.dataUrl });
    $('dialog').close();
    toast('Logo saved. Reloading the dashboard to show it…');
    await load();
    setTimeout(() => { try { window.top.location.reload(); } catch { /* not inside Coder */ } }, 1200);
  } catch (e) {
    toast(e.message, true);
    $('save').disabled = false;
  }
});

$('reset').addEventListener('click', async () => {
  if (!window.confirm("Remove the custom logo and show Coder's logo again?")) return;
  try {
    await post(API + '/reset', {});
    toast("Coder's logo is back. Reloading…");
    await load();
    setTimeout(() => { try { window.top.location.reload(); } catch { /* not inside Coder */ } }, 1200);
  } catch (e) {
    toast(e.message, true);
  }
});

load().catch((e) => { $('state').textContent = 'Error'; $('meta').textContent = e.message; });
