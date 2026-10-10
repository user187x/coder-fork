/*
 * Coder announcement banner loader.
 *
 * Injected into Coder's HTML by a Traefik rewrite-body middleware. The dashboard is a client-rendered
 * SPA, so the header does not exist in the server HTML: this script places the banner as the sibling
 * right after the sticky header once React has rendered it, re-placing it whenever React re-renders.
 *
 * Changes arrive instantly over a WebSocket to the banner service (/__banner/live): the tab is sent the
 * current banner on connect and every change the moment an admin publishes it, so nobody has to refresh.
 * If the socket cannot be used, it falls back to polling /__banner/banner.json.
 *
 * Text effects (typewriter, wave, ...) use Anime.js, vendored and served from the same service
 * (vendor/anime.umd.min.js). It is fetched only when the banner has an effect, and the banner is fully
 * readable without it: if it cannot load, or the user asked their system for reduced motion, the text is
 * simply shown as it is.
 *
 * Served same-origin because Coder's CSP is `script-src 'self'`. All text is set with textContent.
 */
(function () {
  'use strict';

  var thisScript = document.currentScript;
  var BASE = '/__banner/';
  var BANNER_ID = 'coder-system-banner';
  var STORAGE_KEY = 'coder-banner-dismissed';
  var HEADER_SELECTOR = 'div.sticky.top-0.bg-surface-primary';

  var LEVELS = {
    info: { bg: '#1e40af', fg: '#ffffff' },
    success: { bg: '#166534', fg: '#ffffff' },
    warning: { bg: '#b45309', fg: '#ffffff' },
    critical: { bg: '#b91c1c', fg: '#ffffff' },
  };

  // The version in the URL is what lets the browser cache the library for good; keep it in step with the
  // file in the chart (files/vendor/anime.umd.min.js).
  var LIB_URL = BASE + 'vendor/anime.umd.min.js?v=4.5.0';
  var LIB_TIMEOUT_MS = 4000; // show the plain text if the library has not arrived by then

  var LIVE_PATH = BASE + 'live';
  var SLOW_POLL_SECONDS = 300; // safety net while the live socket is up
  var SILENCE_MS = 60000; // the server sends a heartbeat every 20 s; this long without a word = dead connection

  var cfg = null;
  var pending = false;
  var announce = false; // the next placement comes from a live push: draw attention to it
  var socket = null;
  var liveUp = false;
  var retries = 0;
  var retryTimer = null;
  var lastMessage = 0;

  function safeUrl(url) {
    return typeof url === 'string' && /^(https?:\/\/|\/(?!\/))/i.test(url) ? url : '';
  }

  // What is drawn, as opposed to which announcement it is: `id` decides whether it was dismissed, this decides
  // whether the element on screen is out of date (e.g. an admin changed only "let people dismiss it").
  function signature(c) {
    return JSON.stringify([c.level, c.title, c.message, c.linkText, c.linkUrl, !!c.dismissible, c.effect || 'none', !!c.repeat]);
  }

  function isDismissed() {
    if (!cfg.dismissible) return false; // an undismissable banner is always shown, whatever was dismissed before
    try {
      return !!cfg.id && window.localStorage.getItem(STORAGE_KEY) === cfg.id;
    } catch (e) {
      return false;
    }
  }

  function dismiss() {
    try {
      window.localStorage.setItem(STORAGE_KEY, cfg.id);
    } catch (e) {
      /* storage unavailable: the banner just returns on the next load */
    }
    schedule();
  }

  function build(cfg, onDismiss) {
    var level = LEVELS[cfg.level] || LEVELS.info;
    var el = document.createElement('div');
    el.id = BANNER_ID;
    el.setAttribute('role', 'status');
    el.setAttribute('data-banner-id', cfg.id || '');
    el.setAttribute('data-banner-sig', signature(cfg));
    el.style.cssText =
      'display:flex;align-items:center;justify-content:center;gap:12px;flex-shrink:0;' +
      'box-sizing:border-box;width:100%;padding:10px 24px;font-size:14px;line-height:20px;' +
      'text-align:center;background:' + level.bg + ';color:' + level.fg + ';';

    var text = document.createElement('span');
    text.setAttribute('data-banner-text', '');
    if (cfg.title) {
      var title = document.createElement('strong');
      title.textContent = cfg.title + ' ';
      text.appendChild(title);
    }
    text.appendChild(document.createTextNode(cfg.message || ''));
    var href = safeUrl(cfg.linkUrl);
    if (href) {
      text.appendChild(document.createTextNode(' '));
      var link = document.createElement('a');
      link.href = href;
      link.textContent = cfg.linkText || href;
      link.style.cssText = 'color:inherit;text-decoration:underline;font-weight:600;';
      if (/^https?:/i.test(href)) {
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
      text.appendChild(link);
    }
    el.appendChild(text);

    if (cfg.dismissible) {
      var close = document.createElement('button');
      close.type = 'button';
      close.setAttribute('aria-label', 'Dismiss announcement');
      close.textContent = '×';
      close.style.cssText =
        'background:transparent;border:0;color:inherit;cursor:pointer;font-size:20px;line-height:1;padding:0 4px;';
      close.addEventListener('click', onDismiss);
      el.appendChild(close);
    }
    return el;
  }

  // ---- text effects -------------------------------------------------------------------------------
  // Each effect gets the Anime.js API, the split text (split.chars / split.words) and `run`, which adds the
  // repeat settings. Names must match server.py EFFECTS and the drop-down in admin.html.
  var PAUSE_MS = 2500; // rest between repeats of an effect that ends with the text in place
  var effects = {
    typewriter: function (a, sp, text, run) {
      sp.chars.forEach(function (c) { c.style.opacity = '0'; });
      return run(sp.chars, { opacity: [0, 1], duration: 1, ease: 'linear', delay: a.stagger(each(sp.chars.length, 45, 3000)) }, PAUSE_MS);
    },
    fade: function (a, sp, text, run) {
      return run(sp.words, { opacity: [0, 1], duration: 700, ease: 'inOutQuad', delay: a.stagger(each(sp.words.length, 140, 2000)) }, PAUSE_MS);
    },
    rise: function (a, sp, text, run) {
      return run(sp.chars, { translateY: ['1em', '0em'], opacity: [0, 1], duration: 700, ease: 'outExpo', delay: a.stagger(each(sp.chars.length, 25, 1500)) }, PAUSE_MS);
    },
    wave: function (a, sp, text, run) {
      return run(sp.chars, {
        translateY: [{ to: '-0.5em', duration: 300, ease: 'outSine' }, { to: '0em', duration: 300, ease: 'inSine' }],
        delay: a.stagger(each(sp.chars.length, 50, 1500)),
      }, 400);
    },
    bounce: function (a, sp, text, run) {
      return run(sp.chars, { translateY: ['-1em', '0em'], opacity: [0, 1], duration: 900, ease: 'outBounce', delay: a.stagger(each(sp.chars.length, 30, 1500)) }, PAUSE_MS);
    },
    flip: function (a, sp, text, run) {
      return run(sp.chars, { rotateX: [-90, 0], opacity: [0, 1], duration: 600, ease: 'outBack', delay: a.stagger(each(sp.chars.length, 30, 1500)) }, PAUSE_MS);
    },
    shake: function (a, sp, text, run) {
      return run(text, {
        translateX: [{ to: -7, duration: 70 }, { to: 7, duration: 110 }, { to: -5, duration: 110 }, { to: 5, duration: 110 }, { to: 0, duration: 70 }],
      }, 1500);
    },
    pulse: function (a, sp, text, run) {
      return run(text, { opacity: [{ to: 0.4, duration: 600, ease: 'inOutSine' }, { to: 1, duration: 600, ease: 'inOutSine' }], scale: [{ to: 1.03, duration: 600 }, { to: 1, duration: 600 }] }, 300, 3);
    },
    rainbow: function (a, sp, text, run) {
      var colors = [];
      for (var h = 0; h <= 360; h += 60) colors.push('hsl(' + h + ', 95%, 78%)');
      return run(sp.chars, { color: colors, duration: 2600, ease: 'linear', delay: a.stagger(each(sp.chars.length, 60, 1200)) }, 0);
    },
  };

  // Delay between letters: `per` ms, but never more than `total` ms for the whole text (a long message
  // must not take half a minute to type).
  function each(count, per, total) {
    return Math.max(4, Math.min(per, total / Math.max(1, count)));
  }

  function reducedMotion() {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) {
      return false;
    }
  }

  var libPromise = null;
  function loadLib() {
    if (window.anime && window.anime.animate) return Promise.resolve(window.anime);
    if (!libPromise) {
      libPromise = new Promise(function (resolve, reject) {
        var tag = document.createElement('script');
        tag.src = LIB_URL;
        tag.async = true;
        tag.onload = function () {
          if (window.anime && window.anime.animate) resolve(window.anime);
          else reject(new Error('effects library missing'));
        };
        tag.onerror = function () {
          reject(new Error('effects library failed to load'));
        };
        (document.head || document.documentElement).appendChild(tag);
      });
      libPromise.catch(function () {
        libPromise = null; // try again the next time an effect starts
      });
    }
    return libPromise;
  }

  var activeFx = null; // one banner (or one preview) animates at a time

  // Stops the effect and puts the plain text back. Safe to call any time, any number of times.
  function stopEffect() {
    var fx = activeFx;
    activeFx = null;
    if (!fx || fx.stopped) return;
    fx.stopped = true;
    window.clearTimeout(fx.timer);
    try {
      if (fx.split) fx.split.revert(); // also reverts the animations created inside it
    } catch (e) {
      /* the text node may already be gone */
    }
    fx.text.style.visibility = '';
    fx.text.removeAttribute('style');
  }

  // Plays `cfg.effect` on the banner's text. `hide`: keep the text invisible until the effect is ready to
  // start, so a typewriter does not flash the whole sentence first (if the library is slow, a timer gives up and shows the plain text).
  function startEffect(el, c, hide) {
    stopEffect();
    var name = c.effect || 'none';
    var text = el.querySelector('[data-banner-text]');
    if (!text || !effects.hasOwnProperty(name) || reducedMotion()) return;
    var fx = { stopped: false, split: null, timer: 0, text: text };
    activeFx = fx;
    // Clip letters that travel outside the line (a rising letter should slide in, not float over the page),
    // with room to spare so nothing is cut at rest.
    text.style.cssText = 'overflow:hidden;padding:10px 4px;margin:-10px -4px;';
    if (hide) {
      text.style.visibility = 'hidden';
      fx.timer = window.setTimeout(function () {
        if (activeFx === fx) stopEffect(); // too slow: this banner stays plain rather than start on visible text
      }, LIB_TIMEOUT_MS);
    }
    loadLib().then(
      function (a) {
        if (fx.stopped || !text.isConnected) return;
        window.clearTimeout(fx.timer);
        var split = a.splitText(text, { words: true, chars: true });
        fx.split = split;
        // An effect (not a bare animation) so that it is rebuilt if the split is redone, e.g. on resize.
        split.addEffect(function () {
          // Underlines do not reach letters that are inline-blocks, so underline a link letter by letter
          // (not the spaces between its words: those would show while the letters are still hidden).
          [].forEach.call(text.querySelectorAll('a'), function (link) {
            link.style.textDecoration = 'none';
            [].forEach.call(link.querySelectorAll('[data-char]'), function (ch) {
              ch.style.textDecoration = 'underline';
            });
          });
          var run = function (targets, props, pause, times) {
            props.loop = c.repeat ? true : times || false;
            if (props.loop && pause) props.loopDelay = pause;
            if (!c.repeat) props.onComplete = function () { if (activeFx === fx) stopEffect(); };
            return a.animate(targets, props);
          };
          return effects[name](a, split, text, run);
        });
        text.style.visibility = '';
      },
      function () {
        if (activeFx === fx) stopEffect(); // no library: the plain text is already right, just undo our styling
      }
    );
  }

  // The header is the sticky *top* bar; prefer the one directly followed by <main id="main-content">.
  function findHeader() {
    var headers = document.querySelectorAll(HEADER_SELECTOR);
    for (var i = 0; i < headers.length; i++) {
      var next = headers[i].nextElementSibling;
      if (next && next.id === 'main-content') return headers[i];
    }
    return headers[0] || null;
  }

  function emphasise(el) {
    if (!el.animate) return;
    try {
      el.animate([{ opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' }], { duration: 300, easing: 'ease-out' });
    } catch (e) {
      /* purely cosmetic */
    }
  }

  function discard(el) {
    stopEffect();
    el.remove();
  }

  function place() {
    pending = false;
    var animate = announce;
    announce = false;
    var existing = document.getElementById(BANNER_ID);
    var wanted = cfg && cfg.enabled && cfg.message && !isDismissed();
    if (!wanted) {
      if (existing) discard(existing);
      return;
    }
    if (existing && (existing.getAttribute('data-banner-id') !== (cfg.id || '') || existing.getAttribute('data-banner-sig') !== signature(cfg))) {
      discard(existing);
      existing = null;
    }

    var header = findHeader();
    var root = document.getElementById('root');
    if (!header && !(cfg.showOnLoginPage && root)) {
      if (existing) discard(existing);
      return;
    }
    var el = existing || build(cfg, dismiss);
    if (header) {
      if (header.nextElementSibling !== el) header.insertAdjacentElement('afterend', el);
    } else if (root.previousElementSibling !== el) {
      root.parentNode.insertBefore(el, root);
    }
    if (!existing && animate) emphasise(el);
    if (!existing && cfg.effect && cfg.effect !== 'none') startEffect(el, cfg, true);
  }

  // Coalesce bursts of DOM mutations (React renders) into one placement per frame. A hidden tab never gets
  // animation frames, so place immediately there: the banner is already in the page when the user returns.
  function schedule() {
    if (pending || !cfg) return;
    pending = true;
    if (document.hidden) window.setTimeout(place, 0);
    else window.requestAnimationFrame(place);
  }

  function apply(next, live) {
    cfg = next;
    announce = !!live;
    schedule();
  }

  function load() {
    return window
      .fetch(BASE + 'banner.json?t=' + Date.now(), { cache: 'no-store', credentials: 'omit' })
      .then(function (res) {
        return res.ok ? res.json() : Promise.reject(new Error('HTTP ' + res.status));
      })
      .then(function (next) {
        apply(next, false);
        return next;
      })
      .catch(function () {
        /* keep showing the last good config; try again on the next tick */
      });
  }

  // ---- live channel -------------------------------------------------------------------------------
  function liveUrl() {
    return (window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + LIVE_PATH;
  }

  // Exponential backoff with jitter, so a restart of the service does not have every tab reconnect at once.
  function reconnectSoon() {
    if (retryTimer) return;
    var delay = Math.min(30000, 1000 * Math.pow(2, retries)) * (0.5 + Math.random() / 2);
    retries++;
    retryTimer = window.setTimeout(function () {
      retryTimer = null;
      connect();
    }, delay);
  }

  function lost(ws) {
    if (socket !== ws) return;
    socket = null;
    liveUp = false;
    reconnectSoon();
  }

  function connect() {
    if (socket || !window.WebSocket) return;
    var ws;
    try {
      ws = new window.WebSocket(liveUrl());
    } catch (e) {
      reconnectSoon();
      return;
    }
    socket = ws;
    lastMessage = Date.now();
    ws.onmessage = function (event) {
      lastMessage = Date.now();
      retries = 0;
      liveUp = true;
      var msg;
      try {
        msg = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      if (msg && msg.type === 'banner' && msg.banner) apply(msg.banner, true); // heartbeats ({"type":"hb"}) only prove life
    };
    ws.onclose = ws.onerror = function () {
      lost(ws);
    };
  }

  // A connection can die silently (laptop sleep, NAT timeout): no close event ever arrives.
  function watchdog() {
    if (socket && Date.now() - lastMessage > SILENCE_MS) {
      var ws = socket;
      lost(ws);
      try {
        ws.close();
      } catch (e) {
        /* already gone */
      }
    }
  }

  // ---- polling (safety net) -------------------------------------------------------------------------
  // Re-read the interval every cycle so an admin can change it. While the live socket is up polling only
  // guards against a missed push; without it, polling is the whole mechanism.
  function poll() {
    var seconds = liveUp ? SLOW_POLL_SECONDS : Math.max(15, Number((cfg && cfg.refreshSeconds) || 60));
    window.setTimeout(function () {
      load().then(poll);
    }, seconds * 1000);
  }

  function start() {
    load().then(poll);
    connect();
    window.setInterval(watchdog, 10000);
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) {
        load();
        connect();
      }
    });
    window.addEventListener('online', function () {
      retries = 0;
      connect();
    });
  }

  // The admin page loads this file (with data-preview) only to reuse the exact rendering for its
  // live preview; it must not start polling or touch the page.
  if (thisScript && thisScript.hasAttribute('data-preview')) {
    window.__coderBannerPreview = {
      build: function (c) {
        return build(c, function () {});
      },
      // Effects on the admin page's preview: play `c.effect` on a built preview, or stop whatever is playing.
      play: function (el, c) {
        startEffect(el, c, false);
      },
      stop: stopEffect,
      reducedMotion: reducedMotion,
    };
    return;
  }

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
