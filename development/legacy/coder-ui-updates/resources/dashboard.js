// Loaded on every Coder page: the rewritten entry chunk starts with import(`/__coder-ui/dashboard.js`).
// Each add-on is its own module, so one failing does not stop the other.
const css = document.createElement('link');
css.rel = 'stylesheet';
css.href = new URL('dashboard.css', import.meta.url).pathname;
document.head.append(css);

// A Coder page embedded by one of the General views (Agents) or by a workspace's Terminal drop-down
// (agent-terminal.js): the surrounding dashboard already shows the navigation bar, the classification marking and the
// announcement, so this one shows only its own content.
let embedded = false;
try { embedded = !!(window.frameElement && window.frameElement.dataset.cuiEmbed); } catch { /* cross-origin parent */ }
if (embedded) {
  document.documentElement.classList.add('coder-ui-embedded');
  // Links that leave the embedded page (e.g. Agents -> "set up a provider") navigate the dashboard around it, not the
  // frame. Capture phase, before React Router's own handler, which skips clicks that are already handled.
  window.addEventListener('click', (e) => {
    const a = e.target instanceof Element && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.target === '_blank') return;
    const url = new URL(a.href, location.href);
    const home = window.frameElement.dataset.cuiEmbedPath || '/agents';
    if (url.origin !== location.origin || url.pathname === home || url.pathname.startsWith(home + '/')) return;
    e.preventDefault();
    const parent = window.parent;
    if (typeof parent.__cuiNavigate === 'function') parent.__cuiNavigate(url.pathname + url.search + url.hash);
    else parent.location.href = url.href;
  }, true);
} else {
  import('./classification.js').catch(() => {});
  import('./ack.js').catch(() => {});
  import('./banner-dock.js').catch(() => {});
  import('./chat.js').then((m) => m.start()).catch(() => {});
  avatarDefaults();
}

// The default avatar (resources/defaults/avatar.*) is for users without a picture of their own; which users those are
// is only told to signed-in pages. boot.js starts every page with this browser's last list (localStorage, so avatars
// are right from the first paint, embedded frames included); it is refreshed here once per page load, and forgotten
// when signed out.
async function avatarDefaults() {
  const users = window.__cuiAvatarUsers;
  if (!window.__cuiAvatarDefault || !(users instanceof Set)) return;
  try {
    const res = await fetch(new URL('api/avatar/defaults', import.meta.url).pathname, { credentials: 'same-origin', cache: 'no-store' });
    const list = res.ok ? (await res.json()).users : res.status === 401 ? [] : null;
    if (!Array.isArray(list)) return;
    users.clear();
    list.forEach((name) => users.add(name));
    localStorage.setItem('coder-ui-avatar-users', JSON.stringify(list));
  } catch { /* keep the last list */ }
}
