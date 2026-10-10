// General > Network (top of the page): a live map of the Kubernetes cluster Coder runs on. Every node with its
// addresses, pod CIDR, CPU / memory (live usage when metrics-server runs, else the pods' requests) and the Coder pods and
// workspaces placed on it; lines show the way in (access URL -> Coder) and each workspace agent's connection to Coder.
// Tables below list the same workspaces and nodes in full. Re-read every 5 seconds while the tab is visible.
const API = '/__coder-ui/api/network';
const REFRESH_MS = 5000;
const $ = (id) => document.getElementById(id);

let data = null;
let paused = false;
let timer = null;
let lastOk = 0;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

const ago = (iso) => {
  if (!iso) return '—';
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  return s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : s < 129600 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;
};
const cores = (c) => (c == null ? '—' : c < 1 ? `${Math.round(c * 1000)}m` : `${+c.toFixed(c < 10 ? 2 : 1)}`);
const bytes = (b) => {
  if (b == null) return '—';
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i += 1; }
  return `${b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1)} ${u[i]}`;
};
const pct = (a, b) => (a != null && b ? Math.min(100, (a / b) * 100) : null);
const first = (list) => (list && list.length ? list[0] : null);
const agentOk = (w) => w.agents.length > 0 && w.agents.every((a) => a.status === 'connected');
const agentLabel = (w) => {
  if (w.orphan) return 'pod only: not running in Coder';
  if (!w.agents.length) return 'no agent';
  const bad = w.agents.filter((a) => a.status !== 'connected');
  return bad.length ? bad.map((a) => `${a.name}: ${a.status}`).join(', ') : w.agents.length === 1 ? 'connected' : `${w.agents.length} connected`;
};

// ------------------------------------------------------------------ summary tiles
function tile(k, v, s) {
  return el('div', { class: 'tile' }, el('div', { class: 'k', text: k }), el('div', { class: 'v', text: v, title: v }), el('div', { class: 's', text: s || ' ', title: s || '' }));
}

function tiles(d) {
  const ready = d.nodes.filter((n) => n.ready).length;
  const pods = d.nodes.reduce((a, n) => a + n.podCount, 0);
  const podCap = d.nodes.reduce((a, n) => a + (n.allocatable.pods || 0), 0);
  const used = d.nodes.reduce((a, n) => a + ((n.usage && n.usage.cpu) || 0), 0);
  const cpu = d.nodes.reduce((a, n) => a + (n.allocatable.cpu || 0), 0);
  const mem = d.nodes.reduce((a, n) => a + ((n.usage && n.usage.memory) || 0), 0);
  const memCap = d.nodes.reduce((a, n) => a + (n.allocatable.memory || 0), 0);
  const onNodes = new Set(d.workspaces.map((w) => w.pod.node)).size;
  const coder = d.coder.filter((p) => p.role === 'coder');
  $('tiles').replaceChildren(
    tile('Nodes', `${ready} / ${d.nodes.length} ready`, d.nodes.some((n) => n.unschedulable) ? `${d.nodes.filter((n) => n.unschedulable).length} cordoned` : d.kubernetesVersion ? `Kubernetes ${d.kubernetesVersion}` : ''),
    tile('Running workspaces', String(d.workspaces.filter((w) => !w.orphan).length + d.unplaced.length), d.workspaces.length ? `on ${onNodes} node${onNodes === 1 ? '' : 's'}` : 'none on this cluster'),
    tile('Coder servers', `${coder.filter((p) => p.ready).length} / ${coder.length} ready`, coder.map((p) => p.podIP).filter(Boolean).join(', ')),
    tile('Pods', `${pods}${podCap ? ` / ${podCap}` : ''}`, 'all namespaces'),
    d.metrics
      ? tile('CPU in use (cores)', `${cores(used)} / ${cores(cpu)}`, `memory ${bytes(mem)} / ${bytes(memCap)}`)
      : tile('CPU allocatable (cores)', cores(cpu), `memory ${bytes(memCap)} · no metrics-server`),
  );
}

// ------------------------------------------------------------------ topology
function meter(label, value, request, total, fmt) {
  const p = pct(value, total);
  const r = pct(request, total);
  const hot = p != null && p >= 90;
  const bar = el('div', { class: 'bar' });
  if (p != null) bar.append(el('span', { class: 'fill', style: `width:${p.toFixed(1)}%` }));
  if (r != null && value !== request) bar.append(el('span', { class: 'req', style: `left:calc(${r.toFixed(1)}% - 1px)`, title: `requested ${fmt(request)}` }));
  const text = p == null ? `${fmt(request)} requested` : `${fmt(value)} / ${fmt(total)}${hot ? ' · high' : ''}`;
  return el('div', {
    class: `meter${hot ? ' hot' : ''}`, role: 'meter', 'aria-label': label, 'aria-valuemin': '0', 'aria-valuemax': '100',
    'aria-valuenow': p == null ? null : p.toFixed(0), 'aria-valuetext': text,
    title: `${label}: ${fmt(value)} in use, ${fmt(request)} requested by pods, ${fmt(total)} allocatable`,
  }, el('span', { text: label }), bar, el('span', { class: 'val', text }));
}

function chip(kind, name, sub, title, extra = '') {
  return el('div', { class: `chip ${kind}${extra}`, title }, el('b', { text: name }), sub ? el('small', { text: sub }) : null);
}

function nodeCard(n, d) {
  const coder = d.coder.filter((p) => p.node === n.name);
  const ws = d.workspaces.filter((w) => w.pod.node === n.name);
  const status = !n.ready ? ['error', 'Not ready'] : n.unschedulable ? ['warn', 'Cordoned'] : n.pressure.length ? ['warn', n.pressure.join(', ')] : ['ok', 'Ready'];
  const meta = el('dl', { class: 'node-meta' });
  const add = (k, v) => { if (v) meta.append(el('dt', { text: k }), el('dd', { text: v })); };
  add('Internal IP', (n.addresses.InternalIP || []).join(', '));
  add('External IP', (n.addresses.ExternalIP || []).join(', '));
  add('Pod CIDR', n.podCIDRs.join(', '));
  add('Zone', [n.region, n.zone].filter(Boolean).join(' / '));
  add('Kubelet', `${n.kubelet || ''} · ${n.arch || ''}`);
  const card = el('article', { class: `node${n.ready ? '' : ' down'}`, 'data-node': n.name, 'aria-label': `Node ${n.name}` },
    el('div', { class: 'node-head' },
      el('span', { class: 'node-name', text: n.name }),
      el('span', { class: `pill ${status[0] === 'error' ? 'error' : status[0]}`, text: status[1] })),
    el('div', { class: 'hint', text: `${n.roles.join(', ')} · ${n.podCount} pods${n.allocatable.pods ? ` of ${n.allocatable.pods}` : ''} · up ${ago(n.created)}` }),
    meta,
    meter('CPU', n.usage ? n.usage.cpu : null, n.requests.cpu, n.allocatable.cpu, (v) => `${cores(v)}`),
    meter('Memory', n.usage ? n.usage.memory : null, n.requests.memory, n.allocatable.memory, bytes));
  if (coder.length) {
    card.append(el('div', {}, el('p', { class: 'group-h', text: `Coder (${d.coderNamespace})` }),
      el('div', { class: 'chips' }, ...coder.map((p) => {
        const c = chip(p.role, p.name, p.podIP || '', `${p.role} · ${p.name}\nIP ${p.podIP || '—'} · ${p.ready ? 'ready' : 'not ready'} · ${p.restarts} restarts · up ${ago(p.started)}`, p.ready ? '' : ' not-ready');
        c.dataset.pod = p.name;
        if (p.role === 'coder') c.dataset.coder = '1';
        return c;
      }))));
  }
  card.append(el('div', {}, el('p', { class: 'group-h', text: `Workspaces (${ws.length})` }),
    ws.length ? el('div', { class: 'chips' }, ...ws.map((w) => {
      const ok = agentOk(w);
      const a = first(w.agents);
      const c = chip('workspace', `${w.owner}/${w.name}`, w.pod.podIP || '',
        `${w.owner}/${w.name} (${w.template})\npod ${w.pod.name} · IP ${w.pod.podIP || '—'}\nagent: ${agentLabel(w)}${a && a.latencyMs != null ? ` · ${a.latencyMs.toFixed(0)} ms` : ''}`,
        ok ? '' : ' bad');
      c.dataset.ws = w.id;
      c.dataset.ok = ok ? '1' : '0';
      return c;
    })) : el('p', { class: 'empty', text: 'No workspaces on this node.' })));
  return card;
}

function entryCard(d) {
  let host = d.accessUrl;
  try { host = new URL(d.accessUrl).host; } catch { /* keep as is */ }
  return [
    el('div', { class: 'group-h', text: 'Way in' }),
    el('div', { class: 'host', text: host || 'Access URL' }),
    el('div', { class: 'hint', text: 'Browsers and CLIs reach Coder here (Gateway → Service). Workspace agents dial back to Coder the same way.' }),
  ];
}

// Curves from the entry card to each Coder server, and from a Coder server to every workspace (its agent's connection).
function drawLinks() {
  const svg = $('links');
  const box = $('topology').getBoundingClientRect();
  svg.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
  const at = (node, side) => {
    const r = node.getBoundingClientRect();
    return side === 'right' ? [r.right - box.left, r.top - box.top + r.height / 2]
      : side === 'left' ? [r.left - box.left, r.top - box.top + r.height / 2]
        : [r.left - box.left + r.width / 2, r.top - box.top];
  };
  const curve = ([x1, y1], [x2, y2]) => {
    const dx = Math.max(30, Math.abs(x2 - x1) / 2);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
  };
  const paths = [];
  const servers = [...document.querySelectorAll('.chip[data-coder]')];
  const entry = $('entry');
  for (const s of servers) paths.push(['entry', curve(at(entry, 'right'), at(s, 'left'))]);
  const hub = servers[0];
  if (hub) {
    for (const w of document.querySelectorAll('.chip[data-ws]')) {
      const [hx, hy] = at(hub, 'top');
      const [wx, wy] = at(w, 'top');
      const lift = Math.min(hy, wy) - 18;
      paths.push([w.dataset.ok === '1' ? 'ok' : 'bad', `M${hx},${hy} C${hx},${lift} ${wx},${lift} ${wx},${wy}`]);
    }
  }
  svg.replaceChildren(...paths.map(([cls, dAttr]) => {
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('class', cls);
    p.setAttribute('d', dAttr);
    return p;
  }));
}

// ------------------------------------------------------------------ tables
function wsTable(d) {
  const rows = d.workspaces.map((w) => {
    const a = first(w.agents);
    const ok = agentOk(w);
    return el('tr', {},
      el('td', { text: w.name }), el('td', { text: w.owner }), el('td', { text: w.template || '—' }),
      el('td', { class: 'mono', text: w.pod.node || '—' }), el('td', { class: 'mono', text: w.pod.podIP || '—' }),
      el('td', {}, el('span', { class: `status ${ok ? 'ok' : 'warn'}`, text: agentLabel(w) })),
      el('td', { class: 'num', text: a && a.latencyMs != null ? `${a.latencyMs.toFixed(0)} ms` : '—' }),
      el('td', { class: 'num', text: String(w.pod.restarts) }), el('td', { class: 'num', text: ago(w.pod.started) }));
  });
  $('ws-table').tBodies[0].replaceChildren(...(rows.length ? rows : [el('tr', {}, el('td', { colspan: '9', class: 'empty', text: 'No running workspaces have a pod in this cluster.' }))]));
  $('ws-count').textContent = `${d.workspaces.length} running`;
  const un = $('unplaced');
  un.hidden = !d.unplaced.length;
  un.textContent = d.unplaced.length ? `Running outside this cluster (no pod found): ${d.unplaced.map((w) => `${w.owner}/${w.name}`).join(', ')}` : '';
}

function nodeTable(d) {
  $('node-table').tBodies[0].replaceChildren(...d.nodes.map((n) => el('tr', {},
    el('td', { text: n.name }),
    el('td', {}, el('span', { class: `status ${!n.ready ? 'error' : n.unschedulable || n.pressure.length ? 'warn' : 'ok'}`, text: !n.ready ? 'Not ready' : n.unschedulable ? 'Cordoned' : n.pressure.length ? n.pressure.join(', ') : 'Ready' })),
    el('td', { text: n.roles.join(', ') }),
    el('td', { class: 'mono', text: (n.addresses.InternalIP || []).join(', ') || '—' }),
    el('td', { class: 'mono', text: (n.addresses.ExternalIP || []).join(', ') || '—' }),
    el('td', { class: 'mono', text: n.podCIDRs.join(', ') || '—' }),
    el('td', { class: 'num', text: `${n.usage ? `${cores(n.usage.cpu)} / ` : ''}${cores(n.allocatable.cpu)}` }),
    el('td', { class: 'num', text: `${n.usage ? `${bytes(n.usage.memory)} / ` : ''}${bytes(n.allocatable.memory)}` }),
    el('td', { class: 'num', text: `${n.podCount} / ${n.allocatable.pods ?? '—'}` }),
    el('td', { class: 'mono', text: n.kubelet || '—' }),
    el('td', { text: `${n.os || '—'} · ${n.kernel || ''}` }),
    el('td', { class: 'mono', text: n.runtime || '—' }),
    el('td', { class: 'mono', text: n.taints.join(', ') || '—' }),
    el('td', { class: 'num', text: ago(n.created) }))));
  $('nodes-count').textContent = `${d.nodes.length} node${d.nodes.length === 1 ? '' : 's'}${d.metrics ? '' : ' · usage needs metrics-server (showing requests)'}`;
}

// ------------------------------------------------------------------ refresh loop
function render(d) {
  tiles(d);
  $('entry').replaceChildren(...entryCard(d));
  $('nodes').replaceChildren(...d.nodes.map((n) => nodeCard(n, d)));
  wsTable(d);
  nodeTable(d);
  requestAnimationFrame(drawLinks);
}

function stamp() {
  const live = $('live');
  const age = lastOk ? Math.round((Date.now() - lastOk) / 1000) : null;
  live.classList.toggle('paused', paused);
  live.classList.toggle('stale', !paused && age != null && age > REFRESH_MS / 1000 * 3);
  $('updated').textContent = paused ? 'Paused' : age == null ? 'Loading…' : `Live · updated ${age} s ago`;
}

async function load() {
  clearTimeout(timer);
  if (paused || document.hidden) return;
  try {
    const res = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    data = body;
    lastOk = Date.now();
    $('error').hidden = true;
    render(data);
  } catch (e) {
    $('error').hidden = false;
    $('error').textContent = `The cluster could not be read: ${e.message}`;
  } finally {
    stamp();
    if (!paused) timer = setTimeout(load, REFRESH_MS);
  }
}

$('pause').addEventListener('click', () => {
  paused = !paused;
  $('pause').textContent = paused ? 'Resume' : 'Pause';
  $('pause').setAttribute('aria-pressed', String(paused));
  if (paused) clearTimeout(timer); else load();
  stamp();
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
setInterval(stamp, 1000);
new ResizeObserver(() => { if (data) requestAnimationFrame(drawLinks); }).observe($('topology'));
load();
