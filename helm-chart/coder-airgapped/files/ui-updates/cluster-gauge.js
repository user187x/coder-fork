// Admin menu, last entry: the cluster's CPU and memory at a glance, two horizontal bars side by side, refreshed every
// 3 seconds while the menu is open (/__coder-ui/api/cluster/usage, admins only; for anyone else the entry stays
// empty). Each bar fills with what is in use (live from metrics-server; without it, what pods have requested), the
// lighter band behind it shows what running pods have reserved, and "free" is what new workspaces can still be given
// (allocatable - requested). Visual only: no link, nothing to click.

const API = '/__coder-ui/api/cluster/usage';
const EVERY_MS = 3000;
const GiB = 1024 ** 3;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

const num = (v) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
const amount = { cpu: (v) => num(v), memory: (v) => num(v / GiB) };
const unit = { cpu: (v) => (Math.abs(v - 1) < 0.005 ? 'core' : 'cores'), memory: () => 'GiB' };
const fmt = { cpu: (v) => `${amount.cpu(v)} ${unit.cpu(v)}`, memory: (v) => `${amount.memory(v)} GiB` };

function bar(kind, label) {
  const track = el('div', 'cui-cl-track');
  track.setAttribute('role', 'meter');
  track.setAttribute('aria-label', `${label} in use`);
  track.setAttribute('aria-valuemin', '0');
  track.append(el('div', 'cui-cl-req'), el('div', 'cui-cl-fill'));
  const box = el('div', 'cui-cl-bar');
  box.dataset.kind = kind;
  const head = el('div', 'cui-cl-label');
  head.append(el('span', '', label), el('span', 'cui-cl-pct', '–'));
  box.append(head, track, el('div', 'cui-cl-val', '…'), el('div', 'cui-cl-free', ''));
  return box;
}

function paint(box, kind, r) {
  const pct = (v) => (r.total > 0 ? Math.min(100, (v / r.total) * 100) : 0);
  const used = pct(r.used);
  const track = box.querySelector('.cui-cl-track');
  track.querySelector('.cui-cl-fill').style.width = `${used}%`;
  track.querySelector('.cui-cl-req').style.width = `${pct(r.requested)}%`;
  track.setAttribute('aria-valuemax', String(r.total));
  track.setAttribute('aria-valuenow', String(r.used));
  track.setAttribute('aria-valuetext', `${fmt[kind](r.used)} of ${fmt[kind](r.total)}`);
  box.dataset.level = used >= 90 ? 'high' : used >= 70 ? 'mid' : 'low';
  box.querySelector('.cui-cl-pct').textContent = `${Math.round(used)}%`;
  box.querySelector('.cui-cl-val').textContent = `${amount[kind](r.used)} / ${amount[kind](r.total)} ${unit[kind](r.total)}`;
  box.querySelector('.cui-cl-free').textContent = `${fmt[kind](r.free)} free`;
  box.title = `In use ${fmt[kind](r.used)} · reserved by pods ${fmt[kind](r.requested)} · allocatable ${fmt[kind](r.total)}`;
}

export function mount(root) {
  if (!root || root.dataset.cuiGauge) return;
  root.dataset.cuiGauge = '1';
  const status = el('span', 'cui-cl-status', '');
  const head = el('div', 'cui-cl-head');
  head.append(el('span', '', 'Cluster'), status);
  const cpu = bar('cpu', 'CPU');
  const memory = bar('memory', 'Memory');
  const bars = el('div', 'cui-cl-bars');
  bars.append(cpu, memory);
  root.append(head, bars);
  root.hidden = true;   // until the first answer: nothing for non-admins

  let timer = null;
  const tick = async () => {
    if (!root.isConnected) return;   // the menu closed (Radix unmounts it): stop
    try {
      const res = await fetch(API, { credentials: 'same-origin', cache: 'no-store' });
      if (res.status === 401 || res.status === 403) { root.remove(); return; }
      const u = await res.json();
      if (!res.ok) throw new Error(u.error || `HTTP ${res.status}`);
      paint(cpu, 'cpu', u.cpu);
      paint(memory, 'memory', u.memory);
      status.textContent = `${u.live ? 'live' : 'requests'} · ${u.nodes} node${u.nodes === 1 ? '' : 's'}`;
      status.title = u.live ? 'In use = live usage (metrics-server)' : 'metrics-server is not installed: in use = what pods requested';
      status.dataset.live = u.live ? '1' : '';
      root.hidden = false;
    } catch {
      status.textContent = 'unavailable';
    }
    timer = setTimeout(tick, EVERY_MS);
  };
  tick();
  return () => clearTimeout(timer);
}
