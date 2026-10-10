// Health > Workspace Health: which workspaces need attention (latest build failed, or agents unhealthy / not
// connected). Shared by the icon beside "Workspace Health" in the Health panel (icon) and its page
// (workspace-health-view.js). Reads Coder's own API with the admin's session; nothing is stored.

export async function unhealthy() {
  const res = await fetch('/api/v2/workspaces?limit=1000', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`Coder workspaces API: HTTP ${res.status}`);
  const { workspaces = [] } = await res.json();
  const out = [];
  for (const w of workspaces) {
    const build = w.latest_build || {};
    const agents = (build.resources || []).flatMap((r) => r.agents || []);
    const problems = [];
    if (build.status === 'failed' || build.job?.status === 'failed') problems.push({ kind: 'failed', text: `${build.transition || 'build'} failed` });
    if (build.status === 'running') {
      for (const a of agents) {
        if (a.health && a.health.healthy === false) problems.push({ kind: 'unhealthy', text: `agent ${a.name}: ${a.health.reason || 'unhealthy'}` });
        else if (a.status && a.status !== 'connected' && a.status !== 'connecting') problems.push({ kind: 'unhealthy', text: `agent ${a.name}: ${a.status}` });
        if (['start_error', 'start_timeout', 'shutdown_error'].includes(a.lifecycle_state)) problems.push({ kind: 'unhealthy', text: `agent ${a.name}: ${a.lifecycle_state.replace('_', ' ')}` });
      }
      if (w.health && w.health.healthy === false && !problems.length) problems.push({ kind: 'unhealthy', text: 'unhealthy' });
    }
    if (problems.length) {
      out.push({
        id: w.id, owner: w.owner_name, name: w.name, template: w.template_display_name || w.template_name,
        status: build.status, updatedAt: build.updated_at, problems,
        severity: problems.some((p) => p.kind === 'failed') ? 'error' : 'warning',
      });
    }
  }
  return out.sort((a, b) => (a.severity === b.severity ? a.owner.localeCompare(b.owner) : a.severity === 'error' ? -1 : 1));
}

const ICONS = {
  ok: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5 8.2l2 2 4-4.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  alert: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 1.6l6.6 12H1.4z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6.2v3.6M8 11.6v.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
};

// The icon beside "Workspace Health": a blue check when nothing needs attention, otherwise an alert in the Health
// page's warning / error colour. Re-checked every 30 seconds while the panel is on screen.
export function icon(el) {
  el.style.cssText = 'display:inline-flex;align-items:center;width:16px;height:16px;flex-shrink:0';
  const paint = async () => {
    if (!el.isConnected) return;
    try {
      const list = await unhealthy();
      const error = list.some((w) => w.severity === 'error');
      el.innerHTML = list.length ? ICONS.alert : ICONS.ok; // constant markup
      el.style.color = list.length ? `hsl(var(--content-${error ? 'destructive' : 'warning'}))` : '#3b82f6';
      el.title = list.length ? `${list.length} workspace${list.length === 1 ? ' needs' : 's need'} attention` : 'All workspaces healthy';
      el.setAttribute('role', 'img');
      el.setAttribute('aria-label', el.title);
    } catch {
      el.innerHTML = '';
    }
    setTimeout(paint, 30000);
  };
  paint();
}
