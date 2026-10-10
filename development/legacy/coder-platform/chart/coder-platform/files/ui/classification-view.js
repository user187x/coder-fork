// Deployment > Classification: edits the top/bottom classification marking (shown by classification.js on every page).
const API = '/__coder-ui/api/classification';
const $ = (id) => document.getElementById(id);

// Standard banner colours for US classification markings (background), as quick picks.
const BACKGROUNDS = [
  ['UNCLASSIFIED', '#007a33'], ['CUI', '#502b85'], ['CONFIDENTIAL', '#0033a0'],
  ['SECRET', '#c8102e'], ['TOP SECRET', '#ff8c00'], ['TOP SECRET//SCI', '#fce83a'],
];
const FOREGROUNDS = [['White', '#ffffff'], ['Black', '#000000']];

let saved = null;
let form = null;

function toast(text, isError) {
  const t = $('toast');
  t.textContent = text;
  t.classList.toggle('error', !!isError);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 4000);
}

// WCAG contrast ratio, to warn about unreadable combinations.
function luminance(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

function swatches(container, list, key) {
  for (const [name, hex] of list) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'swatch';
    b.style.background = hex;
    b.title = `${name} (${hex})`;
    b.setAttribute('aria-label', `${name} ${hex}`);
    b.dataset.hex = hex;
    b.addEventListener('click', () => {
      form[key] = hex;
      // A preset background also gets the text colour that marking normally uses.
      if (key === 'background') form.color = hex === '#fce83a' || hex === '#ff8c00' ? '#000000' : '#ffffff';
      render();
    });
    container.append(b);
  }
}

function same(a, b) {
  return ['enabled', 'text', 'height', 'background', 'color'].every((k) => a[k] === b[k]);
}

function render() {
  $('enabled').checked = form.enabled;
  $('enabled-state').textContent = form.enabled ? 'On' : 'Off';
  if (document.activeElement !== $('text')) $('text').value = form.text;
  $('height').value = form.height;
  $('height-out').textContent = `${form.height} px`;
  for (const [id, key] of [['bg', 'background'], ['fg', 'color']]) {
    $(id).value = form[key];
    if (document.activeElement !== $(`${id}-hex`)) $(`${id}-hex`).value = form[key];
  }
  for (const b of document.querySelectorAll('#bg-swatches .swatch')) b.setAttribute('aria-pressed', String(b.dataset.hex === form.background));
  for (const b of document.querySelectorAll('#fg-swatches .swatch')) b.setAttribute('aria-pressed', String(b.dataset.hex === form.color));
  const font = Math.max(10, Math.min(20, Math.round(form.height * 0.55)));
  for (const bar of [$('bar-top'), $('bar-bottom')]) {
    bar.textContent = form.text || 'CLASSIFICATION';
    Object.assign(bar.style, { height: `${form.height}px`, lineHeight: `${form.height}px`, fontSize: `${font}px`, background: form.background, color: form.color });
  }
  document.querySelector('.mock').classList.toggle('off', !form.enabled);
  const ratio = contrast(form.background, form.color);
  $('contrast').textContent = ratio < 4.5 ? `Low contrast (${ratio.toFixed(1)}:1): the text may be hard to read. 4.5:1 or more is recommended.` : '';
  const dirty = !same(form, saved);
  $('save').disabled = !dirty;
  $('revert').disabled = !dirty;
  $('dirty').textContent = dirty ? 'Unsaved changes' : '';
  if (form.enabled && !form.text.trim()) $('dirty').textContent = 'Enter the classification text to turn the banners on.';
}

async function load() {
  const res = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
  saved = await res.json();
  form = { ...saved };
  $('meta').textContent = saved.updatedBy ? `Last changed by ${saved.updatedBy} on ${new Date(saved.updatedAt).toLocaleString()}.` : 'Not set up yet.';
  render();
}

async function save() {
  $('save').disabled = true;
  try {
    const res = await fetch(API, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' },
      body: JSON.stringify({ enabled: form.enabled, text: form.text.trim(), height: form.height, background: form.background, color: form.color }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    saved = data;
    form = { ...data };
    $('meta').textContent = `Last changed by ${data.updatedBy} on ${new Date(data.updatedAt).toLocaleString()}.`;
    toast(data.enabled ? 'Saved. Every Coder page shows the banners within 30 seconds.' : 'Saved. The banners are off.');
    try { window.parent.__coderUiClassification?.refresh(); } catch { /* not inside Coder */ }
  } catch (e) {
    toast(e.message, true);
  }
  render();
}

function bind() {
  swatches($('bg-swatches'), BACKGROUNDS, 'background');
  swatches($('fg-swatches'), FOREGROUNDS, 'color');
  $('enabled').addEventListener('change', () => { form.enabled = $('enabled').checked; render(); });
  $('text').addEventListener('input', () => { form.text = $('text').value; render(); });
  $('height').addEventListener('input', () => { form.height = Number($('height').value); render(); });
  for (const [id, key] of [['bg', 'background'], ['fg', 'color']]) {
    $(id).addEventListener('input', () => { form[key] = $(id).value.toLowerCase(); render(); });
    $(`${id}-hex`).addEventListener('input', () => {
      const v = $(`${id}-hex`).value.trim();
      if (/^#[0-9a-fA-F]{6}$/.test(v)) { form[key] = v.toLowerCase(); render(); }
    });
  }
  const dropper = 'EyeDropper' in window;
  for (const b of document.querySelectorAll('.eyedropper')) {
    if (!dropper) { b.hidden = true; continue; }
    b.addEventListener('click', () => {
      new window.EyeDropper().open().then((r) => {
        form[b.dataset.for === 'bg' ? 'background' : 'color'] = r.sRGBHex.slice(0, 7).toLowerCase();
        render();
      }).catch(() => {});
    });
  }
  $('save').addEventListener('click', save);
  $('revert').addEventListener('click', () => { form = { ...saved }; $('text').value = form.text; render(); });
}

bind();
load().catch((e) => toast(`Could not load the settings: ${e.message}`, true));
