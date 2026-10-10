// Health > Workspace Health page: the table of failed / unhealthy workspaces; a row opens that workspace.
import { unhealthy } from './workspace-health.js';

const $ = (id) => document.getElementById(id);
const ICON_OK = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5 8.2l2 2 4-4.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ICON_ALERT = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.6l6.6 12H1.4z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6.2v3.6M8 11.6v.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v);
  }
  node.append(...children.filter(Boolean));
  return node;
}

function open(w) {
  const url = `/@${encodeURIComponent(w.owner)}/${encodeURIComponent(w.name)}`;
  try { window.parent.__cuiNavigate(url); } catch { window.top.location.href = url; }
}

async function refresh() {
  try {
    const list = await unhealthy();
    const worst = list.some((w) => w.severity === 'error') ? 'error' : list.length ? 'warning' : 'ok';
    $('state').className = `state ${worst}`;
    $('state').innerHTML = worst === 'ok' ? ICON_OK : ICON_ALERT; // constant markup
    $('count').className = `pill ${worst === 'ok' ? 'info' : worst === 'error' ? 'error' : 'warn'}`;
    $('count').textContent = list.length ? `${list.length} need${list.length === 1 ? 's' : ''} attention` : 'All healthy';
    const rows = list.map((w) => {
      const tr = el('tr', { class: 'ws', tabindex: '0', role: 'link', title: `Open ${w.owner}/${w.name}` },
        el('td', {}, el('b', {}, el('span', { class: `sev ${w.severity}` }), `${w.owner} / ${w.name}`), el('small', { text: `status: ${w.status}` })),
        el('td', { text: w.problems.map((p) => p.text).join('; ') }),
        el('td', { text: w.template || '—' }),
        el('td', { text: w.updatedAt ? new Date(w.updatedAt).toLocaleString() : '—' }));
      tr.addEventListener('click', () => open(w));
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(w); });
      return tr;
    });
    $('rows').replaceChildren(...(rows.length ? rows : [el('tr', {}, el('td', { class: 'empty', colspan: '4', text: 'Nothing to triage: no workspace has failed or become unhealthy.' }))]));
    $('checked').textContent = `checked ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    $('count').textContent = 'Error';
    $('checked').textContent = e.message;
  }
  setTimeout(refresh, 30000);
}

refresh();
