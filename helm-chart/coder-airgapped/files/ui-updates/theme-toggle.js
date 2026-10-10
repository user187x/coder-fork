// The "Dark Mode" switch in the user menu (it replaces the "Documentation" link). The rewritten entry chunk renders the
// item and calls bind() for its switch and toggle() when it is chosen. It flips the user's own Coder appearance
// setting between "Dark default" and "Light default" (single-theme mode), keeping their terminal font and the themes
// they picked for "Sync with system", then has Coder re-read the setting (window.__cuiAppearance is the dashboard's
// appearance query, exposed by the rewrite), so the theme changes at once, here and in embedded Coder frames.
const API = '/api/v2/users/me/appearance';
const bound = new Set();
let busy = false;

const isDark = () => ![...document.documentElement.classList].some((c) => c === 'light' || c.startsWith('light-'));

function csrf() {
  return document.querySelector('meta[property="csrf-token"]')?.getAttribute('content') || '';
}

function paint(on) {
  for (const sw of bound) {
    if (!sw.isConnected) { bound.delete(sw); continue; }
    sw.dataset.on = on ? '1' : '0';
    sw.closest('[role]')?.setAttribute('aria-checked', String(on));
  }
}

const watch = new MutationObserver(() => paint(isDark()));
watch.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

export function bind(sw) {
  bound.add(sw);
  paint(isDark());
}

export async function toggle() {
  if (busy) return;
  busy = true;
  const dark = !isDark();
  paint(dark);
  try {
    const res = await fetch(API, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    const current = res.ok ? await res.json() : {};
    // terminal_font is required; "" (never chosen) means Coder's default, which its own page sends as geist-mono.
    const body = { ...current, theme_preference: dark ? 'dark' : 'light', terminal_font: current.terminal_font || 'geist-mono' };
    // Coder v2.37+: single theme instead of following the system; the light / dark picks for "Sync with system" must
    // be real themes (a user who never saved any gets "" back, which the API refuses), as Coder's own page sends them.
    if ('theme_mode' in current) {
      body.theme_mode = 'single';
      body.theme_light = current.theme_light || 'light';
      body.theme_dark = current.theme_dark || 'dark';
    }
    const put = await fetch(API, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRF-TOKEN': csrf() },
      body: JSON.stringify(body),
    });
    if (!put.ok) throw new Error(`HTTP ${put.status}`);
    const query = window.__cuiAppearance;
    if (query && typeof query.refetch === 'function') await query.refetch();
    else window.location.reload();
    for (const frame of document.querySelectorAll('iframe[data-cui-embed]')) {
      try { frame.contentWindow.__cuiAppearance?.refetch?.(); } catch { /* not loaded yet */ }
    }
  } catch (e) {
    console.warn('coder-ui-updates: the theme could not be changed', e);
    paint(isDark());
  } finally {
    busy = false;
  }
}
