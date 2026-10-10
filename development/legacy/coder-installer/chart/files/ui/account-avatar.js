// Settings > Account: your avatar, large, above Email / Username / Name (the rewritten Account page mounts it). It shows
// what Coder shows for you everywhere (your uploaded picture, else Coder's own (OIDC) picture, else the default avatar,
// else your initials); the pencil badge says it can be changed, and a click opens the same dialog as User menu > Avatar.

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...children.filter(Boolean));
  return node;
}

function css() {
  if (document.getElementById('coder-ui-avatar-css')) return;
  document.head.append(el('link', { id: 'coder-ui-avatar-css', rel: 'stylesheet', href: new URL('avatar.css', import.meta.url).pathname }));
}

const PENCIL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/><path d="m15 5 4 4"/></svg>';

export async function mount(root) {
  css();
  const picture = el('span', { class: 'cui-acc-picture' });
  const badge = el('span', { class: 'cui-acc-badge', 'aria-hidden': 'true' });
  badge.innerHTML = PENCIL;
  const button = el('button', { type: 'button', class: 'cui-acc-avatar', 'aria-label': 'Change your avatar', title: 'Change your avatar' }, picture, badge);
  const name = el('div', { class: 'cui-acc-name' });
  const hint = el('div', { class: 'cui-acc-hint', text: 'Click the picture to change it. It shows wherever Coder shows you.' });
  root.replaceChildren(el('div', { class: 'cui-acc-row' }, button, el('div', { class: 'cui-acc-text' }, name, hint)));
  button.addEventListener('click', () => import('./avatar.js').then((m) => m.open()).catch(() => {}));

  try {
    const [mine, me] = await Promise.all([
      fetch('/__coder-ui/api/avatar', { credentials: 'same-origin', cache: 'no-store' }).then((r) => (r.ok ? r.json() : {})),
      fetch('/api/v2/users/me', { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then((r) => (r.ok ? r.json() : {})),
    ]);
    const url = mine.url || me.avatar_url || mine.defaultUrl || null;
    const label = me.name || me.username || mine.username || '';
    name.replaceChildren(el('strong', { text: label }), me.name && me.username ? el('span', { text: ` @${me.username}` }) : null);
    if (url) {
      picture.replaceChildren(el('img', { src: url, alt: '' }));
      root.classList.toggle('cui-acc-uploaded', !!mine.url);
    } else {
      picture.textContent = label.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
    }
  } catch {
    picture.textContent = '?';
  }
}
