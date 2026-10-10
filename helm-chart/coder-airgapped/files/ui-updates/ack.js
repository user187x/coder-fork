// Watches for the coder-banner announcement on every Coder page: draws it with Markdown, the live countdown and custom
// colours (banner-enhance.js), replaces its "×" with "Confirm", and tells the coder-ui-updates service who saw and who
// confirmed which announcement, for the admins' Announcement view.
//
// Signed-out pages (the login page) get no button: a confirmation has to be attributable to someone, and they will
// see the announcement again with its Confirm button once signed in.
import { decorate, markConfirmed, render } from './banner-enhance.js';

const API = '/__coder-ui/api/';
const BANNER_ID = 'coder-system-banner';
const DISMISSED_KEY = 'coder-banner-dismissed'; // coder-banner's own localStorage key for a dismissed announcement
const viewed = new Set();
const status = new Map(); // announcement id -> Promise<{signedIn, username, acked} | null>

// Signed-in answers are remembered for the page's life; "signed out" is not (Coder signs in without a reload).
function me(id) {
  if (!status.has(id)) {
    const answer = fetch(API + 'me?id=' + encodeURIComponent(id), { credentials: 'same-origin', cache: 'no-store' })
      .then((res) => (res.ok ? res.json() : null))
      .then((who) => (who && who.signedIn ? who : null))
      .catch(() => null);
    status.set(id, answer);
    answer.then((who) => { if (!who) status.delete(id); });
  }
  return status.get(id);
}

// coder-banner puts the announcement above #root on pages without Coder's navbar (the login page), and moves the
// same element under the navbar once the dashboard renders.
const onLoginPage = (banner) => banner.nextElementSibling && banner.nextElementSibling.id === 'root';

function post(kind, id) {
  return fetch(API + kind, {
    method: 'POST',
    credentials: 'same-origin',
    keepalive: true, // the banner disappears on confirm; the request must still go out
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-ui' },
    body: JSON.stringify({ id }),
  }).catch(() => {});
}

async function handle(banner) {
  banner.dataset.cui = '1';
  render(banner);
  const id = banner.dataset.bannerId;
  if (!id) return;
  const { button, dismissible } = decorate(banner, {
    onConfirm: () => {
      status.set(id, Promise.resolve({ signedIn: true, acked: true }));
      post('ack', id);
    },
  });
  button.style.visibility = 'hidden'; // until we know who is looking
  const who = await me(id);
  if (!who) { // signed out: no button; look again once the banner has moved into the signed-in dashboard
    button.style.display = 'none';
    banner.dataset.cui = 'signed-out';
    return;
  }
  button.style.display = '';
  if (!banner.isConnected) return;
  if (who.acked) {
    // Confirmed already (maybe in another browser): a dismissible announcement goes away here too.
    if (dismissible) {
      try { window.localStorage.setItem(DISMISSED_KEY, id); } catch { /* the banner just stays */ }
      banner.remove();
      return;
    }
    markConfirmed(button);
  }
  button.style.visibility = '';
  if (!viewed.has(id)) {
    viewed.add(id);
    post('view', id);
  }
}

function scan() {
  const banner = document.getElementById(BANNER_ID);
  if (!banner) return;
  if (!banner.dataset.cui || (banner.dataset.cui === 'signed-out' && !onLoginPage(banner))) handle(banner);
}

new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
scan();
