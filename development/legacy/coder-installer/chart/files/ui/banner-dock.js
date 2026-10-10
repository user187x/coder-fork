// Docks the coder-banner announcement in Coder's navigation bar: first in its right-hand group (the
// "flex items-center gap-3 ml-auto" div, before the menus and notifications). The navbar is sticky, so the
// announcement stays in view wherever the page is scrolled. dashboard.css draws it there as a rounded bar that fills
// the navbar from the Workspaces drop-down to the menus.
//
// coder-banner's loader (banner.js) owns the element: it builds, replaces and removes it, and after every DOM change it
// puts it back right after the navbar (`header.insertAdjacentElement('afterend', banner)` whenever the banner is not
// the navbar's next sibling). Moving it with a MutationObserver of our own would make the two fight forever, so that
// one call is redirected into the navbar instead; once the banner is docked the call changes nothing, so no further
// mutation follows. Everything else banner.js does works on the element wherever it is.
//
// Below Tailwind's md breakpoint Coder's navbar keeps only the notifications and the menu button: there the banner stays
// full width under the navbar (sticky too, see dashboard.css), as coder-banner places it.
const BANNER_ID = 'coder-system-banner';
const NAVBAR = 'div.sticky.top-0.min-h-\\[72px\\]';
const SLOT = ':scope > div.ml-auto';
const wide = window.matchMedia('(min-width: 768px)');

function slotOf(header) {
  return header instanceof Element && header.matches(NAVBAR) ? header.querySelector(SLOT) : null;
}

// Full text on hover, taken when the pointer arrives (a countdown changes it every second), only when it is cut short.
function bindTitle(banner) {
  if (banner.dataset.cuiDock) return;
  banner.dataset.cuiDock = '1';
  banner.addEventListener('mouseenter', () => {
    const text = banner.querySelector('[data-banner-text]');
    if (!text) return;
    if (text.scrollHeight > text.clientHeight + 1) text.title = text.textContent.replace(/\s+/g, ' ').trim();
    else text.removeAttribute('title');
  });
}

function dock(banner, header) {
  const slot = wide.matches && slotOf(header);
  if (!slot) return false;
  if (slot.firstElementChild !== banner) slot.prepend(banner);
  bindTitle(banner);
  return true;
}

const insertAdjacentElement = Element.prototype.insertAdjacentElement;
Element.prototype.insertAdjacentElement = function (where, element) {
  if (where === 'afterend' && element && element.id === BANNER_ID && dock(element, this)) return element;
  return insertAdjacentElement.call(this, where, element);
};

// Now (banner.js may have placed it already) and whenever the window crosses the breakpoint.
function relocate() {
  const banner = document.getElementById(BANNER_ID);
  const header = banner && document.querySelector(NAVBAR);
  if (!header || dock(banner, header)) return;
  if (banner.parentElement && banner.parentElement.parentElement === header) header.after(banner);
}
wide.addEventListener('change', relocate);
relocate();
