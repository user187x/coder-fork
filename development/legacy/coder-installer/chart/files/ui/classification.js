// The classification marking on every Coder page (sign-in page included): two identical bars, one glued to the very
// top of the window and one to the very bottom, above everything else. The page is inset by the bars' height so
// nothing of Coder's hides underneath them (its sticky navbar sits below the top bar, full-height screens shrink).
// Settings come from the coder-ui-updates service (/__coder-ui/api/classification), re-read every 30 seconds and
// at once when an admin saves them in Deployment > Classification.

const API = '/__coder-ui/api/classification';
const REFRESH_MS = 30000;
const Z = 2147483000; // above Coder's dialogs and toasts
let current = null;
let bars = null;
let style = null;

function create() {
  const make = (edge) => {
    const bar = document.createElement('div');
    bar.id = `coder-classification-${edge}`;
    bar.setAttribute('role', 'note');
    bar.setAttribute('aria-label', 'Classification');
    // 100vw, not right:0: Coder reserves a scrollbar gutter (scrollbar-gutter: stable) that right:0 would leave uncovered.
    bar.style.cssText = `position:fixed;left:0;width:100vw;${edge}:0;z-index:${Z};display:flex;align-items:center;justify-content:center;`
      + 'box-sizing:border-box;padding:0 12px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-weight:700;'
      + 'letter-spacing:.06em;font-family:inherit;pointer-events:none;user-select:none';
    return bar;
  };
  bars = [make('top'), make('bottom')];
  style = document.createElement('style');
  style.id = 'coder-classification-style';
}

function apply(c) {
  current = c;
  const on = c && c.enabled && c.text;
  if (!on) {
    bars?.forEach((b) => b.remove());
    style?.remove();
    return;
  }
  if (!bars) create();
  const h = Math.max(12, Math.min(64, Number(c.height) || 24));
  const font = Math.max(10, Math.min(20, Math.round(h * 0.55)));
  for (const bar of bars) {
    bar.textContent = c.text;
    bar.style.height = `${h}px`;
    bar.style.lineHeight = `${h}px`;
    bar.style.fontSize = `${font}px`;
    bar.style.background = c.background;
    bar.style.color = c.color;
    if (!bar.isConnected) document.body.appendChild(bar);
  }
  // Coder reserves a scrollbar gutter on <html> (scrollbar-gutter: stable). A fixed bar cannot paint into it (current
  // browsers also leave it out of 100vw), so without a scrollbar the bars stopped short of the right edge. The page
  // background shows there: paint it, fixed to the window, in the bars' colour at the top and bottom edges.
  const bg = String(c.background).replace(/[;{}<>]/g, '');
  style.textContent = `
    html { --coder-classification-h: ${h}px;
      background: linear-gradient(to bottom, ${bg} 0 ${h}px, hsl(var(--surface-primary)) ${h}px calc(100% - ${h}px), ${bg} calc(100% - ${h}px)) fixed !important; }
    body { padding-top: ${h}px !important; padding-bottom: ${h}px !important; box-sizing: border-box; }
    div.sticky.top-0 { top: ${h}px !important; }
    .sticky.bottom-0, .fixed.bottom-0 { bottom: ${h}px !important; } /* e.g. the deployment stats bar: above our bar, not under it */
    [data-sonner-toaster][data-y-position=bottom] { bottom: calc(${h}px + 24px) !important; } /* Coder's toasts too */
    .h-screen { height: calc(100vh - ${2 * h}px) !important; }
    .min-h-screen { min-height: calc(100vh - ${2 * h}px) !important; }
    .max-h-screen { max-height: calc(100vh - ${2 * h}px) !important; }`;
  if (!style.isConnected) document.head.appendChild(style);
}

async function load() {
  try {
    const res = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
    if (res.ok) apply(await res.json());
  } catch { /* keep what is shown */ }
}

// Keep the bars last in <body> (React re-renders never remove them, but other scripts may append after them).
function start() {
  if (window.__cuiClassification) apply(window.__cuiClassification); // from boot.js: no flash before the first fetch
  load();
  setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  new MutationObserver(() => { if (current && bars && current.enabled && !bars[0].isConnected) apply(current); })
    .observe(document.body, { childList: true });
}

window.__coderUiClassification = { refresh: load, preview: apply };
if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
