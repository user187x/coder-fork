// Admin > Users: a click on a user's row opens their workspace (the rewritten users table calls open(event, user)).
// One workspace: that workspace's page; none or several: their workspaces list, as the row menu's "View workspaces".
// Clicks meant for something inside the row (Chat, the menu, the groups / roles pop-overs, their dialogs, which React
// bubbles through portals) and text selections are left alone; Ctrl / Cmd-click opens a new tab.

const IGNORE = 'a, button, input, select, textarea, label, [role=menuitem], [role=menu], [role=dialog], [role=checkbox]';

function go(url, newTab) {
  if (newTab) { window.open(url, '_blank', 'noopener'); return; }
  // react-router follows history changes it is told about: no page reload
  window.history.pushState({}, '', url);
  window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
}

// click: {row, target, newTab}, taken from the event while it was dispatched (React clears currentTarget afterwards)
export async function open(click, user) {
  const { row, target } = click || {};
  if (!user || !user.username || !(row instanceof Element) || !(target instanceof Element)) return;
  if (!row.contains(target) || (target !== row && target.closest(IGNORE))) return;   // inside the row, not a control
  if (String(window.getSelection?.() || '').trim()) return;
  const newTab = !!click.newTab;
  const list = `/workspaces?filter=${encodeURIComponent(`owner:${user.username}`)}`;
  const root = document.documentElement;
  root.classList.add('coder-ui-busy');
  try {
    const q = new URLSearchParams({ q: `owner:${user.username}`, limit: '2' });
    const res = await fetch(`/api/v2/workspaces?${q}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    const data = res.ok ? await res.json() : null;
    const one = data && data.count === 1 && data.workspaces?.[0];
    go(one ? `/@${encodeURIComponent(one.owner_name || user.username)}/${encodeURIComponent(one.name)}` : list, newTab);
  } catch {
    go(list, newTab);
  } finally {
    root.classList.remove('coder-ui-busy');
  }
}
