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
 * (vendor/anime.umd.min.js); the crawl, Pew-Pew, Split-Flap, Ghostwrite and Knight Rider use the service's
 * stylesheet (fx.css), which also draws the background picture, the outline and the countdown clock. Each is
 * fetched only when the banner needs it, and the banner is fully readable without them: if they cannot load,
 * or the user asked their system for reduced motion, the text is simply shown as it is.
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
  var FX_CSS_URL = BASE + 'fx.css';

  // The banner's fonts: the dashboard's (its bannerFonts.ts) - generic stacks, nothing is fetched.
  var FONTS = {
    sans: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    serif: "Georgia, 'Times New Roman', 'Liberation Serif', serif",
    condensed: "'Franklin Gothic Medium', 'Arial Narrow', 'Liberation Sans Narrow', 'Roboto Condensed', sans-serif",
    'geist-mono': "'Geist Mono Variable', monospace",
    'ibm-plex-mono': "'IBM Plex Mono', monospace",
    'jetbrains-mono': "'JetBrains Mono', monospace",
    'fira-code': "'Fira Code', monospace",
    'source-code-pro': "'Source Code Pro', monospace",
  };
  var PICTURE_FITS = ['center', 'span', 'tile', 'fill', 'stretch'];
  var ROLL_PX_PER_SECOND = 24; // a spanning picture's roll through the banner, as on the dashboard

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
    return JSON.stringify([c.level, c.title, c.message, c.linkText, c.linkUrl, !!c.dismissible, c.effect || 'none', !!c.repeat,
      c.speed || 'normal', c.font || 'default', c.align || 'center', c.picture || '', c.pictureFit || 'span', !!c.outline, c.countdown || '']);
  }

  // What needs the stylesheet: a picture, the outline, the countdown, or an effect it draws.
  function needsFxCss(c) {
    return !!(safeUrl(c.picture) || c.outline || c.countdown || CSS_EFFECTS.hasOwnProperty(c.effect || 'none'));
  }

  // The stylesheet, added to the page once; resolves when it has loaded (or failed: the banner then stays plain).
  var cssPromise = null;
  function loadFxCss() {
    if (!cssPromise) {
      cssPromise = new Promise(function (resolve) {
        var existing = document.querySelector('link[data-coder-banner-fx]');
        if (existing && existing.sheet) return resolve(true);
        var link = existing || document.createElement('link');
        link.rel = 'stylesheet';
        link.href = FX_CSS_URL;
        link.setAttribute('data-coder-banner-fx', '');
        link.addEventListener('load', function () { resolve(true); });
        link.addEventListener('error', function () { resolve(false); });
        if (!existing) (document.head || document.documentElement).appendChild(link);
      });
    }
    return cssPromise;
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

  // ---- the dashboard's tokens (its bannerTokens.ts) ------------------------------------------------------
  // The dashboard's editor keeps custom colours in the (otherwise unused) lead-in, {{colors bg=#rrggbb fg=#rrggbb}};
  // a Markdown message (markdown true) may hold the inline Markdown subset and live countdowns,
  // {{countdown to=2026-10-04T02:00:00Z format=dhms|clock|words done="now"}}. Never HTML.
  var COLORS_RE = /^\{\{colors bg=(#[0-9a-fA-F]{6}) fg=(#[0-9a-fA-F]{6})\}\}$/;
  var COUNTDOWN_RE = /^\{\{countdown\s+to=([0-9T:.\-+Z]+)(?:\s+format=(dhms|clock|words))?(?:\s+done="([^"]{0,80})")?\s*\}\}/;

  function parseColors(title) {
    var m = COLORS_RE.exec(String(title || '').trim());
    return m ? { bg: m[1], fg: m[2] } : null;
  }

  // Relative luminance (WCAG) of #rrggbb: the outline is the opposite of the text's colour.
  function luminance(hex) {
    var m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
    if (!m) return 1;
    var c = [m[1], m[2], m[3]].map(function (x) {
      var v = parseInt(x, 16) / 255;
      return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  function remaining(ms, format) {
    var s = Math.max(0, Math.floor(ms / 1000));
    var d = Math.floor(s / 86400); s -= d * 86400;
    var h = Math.floor(s / 3600); s -= h * 3600;
    var m = Math.floor(s / 60); s -= m * 60;
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    if (format === 'clock') return pad(d * 24 + h) + ':' + pad(m) + ':' + pad(s);
    if (format === 'words') {
      var parts = [[d, 'day'], [h, 'hour'], [m, 'minute'], [s, 'second']].filter(function (p) { return p[0] > 0; }).slice(0, 2);
      return parts.length ? parts.map(function (p) { return p[0] + ' ' + p[1] + (p[0] === 1 ? '' : 's'); }).join(', ') : '0 seconds';
    }
    return (d ? d + 'd ' : '') + pad(h) + 'h ' + pad(m) + 'm ' + pad(s) + 's';
  }

  // A countdown in the text: its time left, kept up to date while it is on the page; then its "done" text.
  function countdownSpan(to, format, done) {
    var target = Date.parse(to);
    var span = document.createElement('span');
    span.setAttribute('data-countdown', '');
    if (isNaN(target)) { span.textContent = '(invalid date)'; return span; }
    var tick = function () {
      var left = target - Date.now();
      span.textContent = left > 0 ? remaining(left, format || 'dhms') : done;
      return left > 0;
    };
    tick();
    var timer = window.setInterval(function () {
      if (span.isConnected) span.wasConnected = true;
      else if (span.wasConnected) return window.clearInterval(timer);
      if (!tick()) window.clearInterval(timer);
    }, 1000);
    return span;
  }

  // The inline Markdown subset, as the dashboard reads it: pictures, countdowns, `code`, **bold**, __bold__,
  // ~~strike~~, *italic*, _italic_, [links](https://... or /path), and backslash escapes. Appends to `parent`.
  var INLINE = [
    [/^!\[([^\]]*)\]\(([^)\s]+)\)/, function (m) {
      var src = safeUrl(m[2]);
      if (!src) return document.createTextNode(m[0]);
      var img = document.createElement('img');
      img.src = src; img.alt = m[1];
      img.style.cssText = 'height:1.4em;width:auto;vertical-align:middle;';
      return img;
    }],
    [/^\{\{countdown\s[^}]*\}\}/, function (m) {
      var c = COUNTDOWN_RE.exec(m[0]);
      return c ? countdownSpan(c[1], c[2], c[3] === undefined ? 'now' : c[3]) : document.createTextNode(m[0]);
    }],
    [/^`([^`]+)`/, function (m) { var code = document.createElement('code'); code.textContent = m[1]; return code; }],
    [/^\*\*(.+?)\*\*/, function (m) { return wrap('strong', m[1]); }],
    [/^__(.+?)__/, function (m) { return wrap('strong', m[1]); }],
    [/^~~(.+?)~~/, function (m) { return wrap('s', m[1]); }],
    [/^\*([^*\s](?:[^*]*[^*\s])?)\*/, function (m) { return wrap('em', m[1]); }, true],
    [/^_([^_\s](?:[^_]*[^_\s])?)_(?![A-Za-z0-9])/, function (m) { return wrap('em', m[1]); }, true],
    [/^\[([^\]]+)\]\(([^)\s]+)\)/, function (m) {
      var href = safeUrl(m[2]);
      if (!href) return document.createTextNode(m[0]);
      return linkTo(href, function (a) { inline(a, m[1]); });
    }],
  ];

  function wrap(tag, text) {
    var el = document.createElement(tag);
    inline(el, text);
    return el;
  }

  function inline(parent, text) {
    var plain = '';
    var flush = function () { if (plain) { parent.appendChild(document.createTextNode(plain)); plain = ''; } };
    for (var i = 0; i < text.length;) {
      var rest = text.slice(i), hit = null;
      for (var r = 0; r < INLINE.length && !hit; r++) {
        var m = INLINE[r][0].exec(rest);
        // A word character right before * or _ makes it part of a word (snake_case, 2*3), not emphasis.
        if (m && !(INLINE[r][2] && /[A-Za-z0-9]/.test(text[i - 1] || ''))) hit = [m, INLINE[r][1]];
      }
      if (hit) {
        flush();
        parent.appendChild(hit[1](hit[0]));
        i += hit[0][0].length;
      } else if (text[i] === '\\' && /[\\`*_~[\]{}()]/.test(text[i + 1] || '')) {
        plain += text[i + 1];
        i += 2;
      } else {
        plain += text[i];
        i += 1;
      }
    }
    flush();
  }

  function linkTo(href, fill) {
    var link = document.createElement('a');
    link.href = href;
    fill(link);
    link.style.cssText = 'color:inherit;text-decoration:underline;font-weight:600;';
    if (/^https?:/i.test(href)) {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    }
    return link;
  }

  function build(cfg, onDismiss) {
    var colors = parseColors(cfg.title) || LEVELS[cfg.level] || LEVELS.info;
    var level = colors;
    var el = document.createElement('div');
    el.id = BANNER_ID;
    el.setAttribute('role', 'status');
    el.setAttribute('data-banner-id', cfg.id || '');
    el.setAttribute('data-banner-sig', signature(cfg));
    el.style.cssText =
      'display:flex;align-items:center;justify-content:center;gap:12px;flex-shrink:0;' +
      'box-sizing:border-box;width:100%;padding:10px 24px;font-size:14px;line-height:20px;' +
      'text-align:center;background:' + level.bg + ';color:' + level.fg + ';';

    if (needsFxCss(cfg)) loadFxCss();
    var picture = safeUrl(cfg.picture);
    if (picture) {
      el.classList.add('cb-screen');
      var fit = PICTURE_FITS.indexOf(cfg.pictureFit) >= 0 ? cfg.pictureFit : 'span';
      var screen = document.createElement('span');
      screen.className = 'cb-picture';
      screen.setAttribute('data-fit', fit);
      screen.setAttribute('role', 'img');
      screen.setAttribute('aria-label', 'Picture');
      screen.style.backgroundImage = 'url("' + picture.replace(/["\\\n\r]/g, '') + '")';
      if (fit === 'span') rollPicture(el, screen, picture);
      el.appendChild(screen);
    }
    if (cfg.countdown) el.appendChild(countdownClock(cfg.countdown));

    var text = document.createElement('span');
    text.setAttribute('data-banner-text', '');
    if (picture) text.setAttribute('data-over-picture', '');
    if (cfg.outline) text.setAttribute('data-outline', '');
    // Its alignment (the text takes the banner's free width for it) and font: kept when an effect ends.
    var base = '';
    if (cfg.align === 'left' || cfg.align === 'right') base += 'flex:1 1 auto;min-width:0;text-align:' + cfg.align + ';';
    if (FONTS.hasOwnProperty(cfg.font)) base += 'font-family:' + FONTS[cfg.font] + ';';
    text.style.cssText = base;
    // The outline: the opposite of the text's colour (a light text gets a dark ring, a dark one a light ring).
    text.style.setProperty('--cb-outline', luminance(level.fg) >= 0.3 ? 'rgb(0 0 0 / 0.92)' : 'rgb(255 255 255 / 0.92)');
    base = text.style.cssText;
    // A lead-in that carries custom colours is not shown: the banner is drawn in them instead.
    if (cfg.title && !parseColors(cfg.title)) {
      var title = document.createElement('strong');
      title.textContent = cfg.title + ' ';
      text.appendChild(title);
    }
    if (cfg.markdown === false) text.appendChild(document.createTextNode(cfg.message || ''));
    else inline(text, cfg.message || '');
    var href = safeUrl(cfg.linkUrl);
    if (href) {
      text.appendChild(document.createTextNode(' '));
      text.appendChild(linkTo(href, function (a) { a.textContent = cfg.linkText || href; }));
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

  // A spanning picture rolls through the banner at a steady pace: its height at the banner's width is one round.
  // Measured once the picture has loaded, and again when the banner's width changes.
  function rollPicture(el, screen, src) {
    var img = new Image();
    var size = function () {
      if (!img.naturalWidth || !screen.isConnected) return;
      var px = (el.clientWidth * img.naturalHeight) / img.naturalWidth;
      screen.style.setProperty('--cb-roll-px', px + 'px');
      screen.style.animationDuration = Math.max(4, px / ROLL_PX_PER_SECOND) + 's';
    };
    img.onload = function () {
      size();
      if (window.ResizeObserver) new window.ResizeObserver(size).observe(el);
    };
    img.src = src;
  }

  // The countdown: a clock at the banner's left, [days D] HH:MM:SS, each character on a plate; at zero it blinks.
  function countdownClock(to) {
    var target = Date.parse(to);
    var clock = document.createElement('span');
    clock.className = 'cb-clock';
    clock.setAttribute('role', 'timer');
    if (isNaN(target)) return clock;
    clock.title = 'Counts down to ' + new Date(target).toLocaleString();
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    var tick = function () {
      var left = Math.max(0, Math.floor((target - Date.now()) / 1000));
      var d = Math.floor(left / 86400);
      var parts = (d > 0 ? d + 'D ' : '') + pad(Math.floor((left % 86400) / 3600)) + ':' + pad(Math.floor((left % 3600) / 60)) + ':' + pad(left % 60);
      clock.textContent = '';
      parts.split('').forEach(function (ch) {
        if (ch === ' ') return;
        var plate = document.createElement(ch === ':' ? 'i' : 'b');
        plate.textContent = ch;
        clock.appendChild(plate);
      });
      clock.setAttribute('aria-label', left ? 'Countdown: ' + parts + ' left' : 'Countdown finished');
      if (left) clock.removeAttribute('data-done');
      else clock.setAttribute('data-done', '');
      return left;
    };
    tick();
    var timer = window.setInterval(function () {
      // Built but not placed yet counts as connected later; once placed and then removed, the clock stops.
      if (clock.isConnected) clock.wasConnected = true;
      else if (clock.wasConnected) return window.clearInterval(timer);
      if (!tick()) window.clearInterval(timer);
    }, 1000);
    return clock;
  }

  // ---- text effects -------------------------------------------------------------------------------
  // Each effect gets the Anime.js API, the split text (split.chars / split.words) and `run`, which adds the
  // repeat settings. These and CSS_EFFECTS are server.py's EFFECTS, as the drop-down in admin.html offers them.
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

  // The effects drawn by the stylesheet (fx.css), as the dashboard draws them: letters in <b>s with their index
  // (--i; closer together past maxIndex letters), a class on the whole text, KITT's scanner behind it, the crawl.
  var CSS_EFFECTS = {
    pewpew: { cls: 'cb-fx-pewpew', letters: true, maxIndex: 12 },
    flap: { cls: 'cb-fx-flap', letters: true, maxIndex: 14 },
    shimmer: { cls: 'cb-fx-shimmer' },
    kitt: { scanner: true },
    starwars: { crawl: true },
  };
  // Knight Rider: one sweep there and back by the banner's speed; a lamp for about every 44 px; without repeat, two
  // sweeps and the lamps go dark. The crawl's pace in pixels per second.
  var KITT_CYCLE_MS = { slow: 2400, normal: 1600, fast: 1000 };
  var KITT_LAMP_PX = 44;
  var KITT_MIN_LAMPS = 8;
  var KITT_MAX_LAMPS = 40;
  var KITT_ONCE_CYCLES = 2;
  var CRAWL_PX_PER_SECOND = { slow: 9, normal: 15, fast: 26 };

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
      if (fx.undo) fx.undo();
    } catch (e) {
      /* the text node may already be gone */
    }
    fx.text.style.cssText = fx.base; // its alignment and font, as built
  }

  // Pew-Pew, Split-Flap: each letter in a <b> with its index, a word's letters kept together on a line; Ghostwrite:
  // the class on the whole text. Without repeat, the plain text comes back when the last letter (or the sweep) is
  // done. Returns what undoes it.
  function letterEffect(text, c, plan, done) {
    var original = text.cloneNode(true);
    var letters = [];
    var walk = function (node) {
      [].slice.call(node.childNodes).forEach(function (child) {
        if (child.nodeType === 1) return walk(child);
        if (child.nodeType !== 3) return;
        var frag = document.createDocumentFragment();
        child.nodeValue.split(/(\s+)/).forEach(function (part) {
          if (!part) return;
          if (/^\s+$/.test(part)) return frag.appendChild(document.createTextNode(part));
          var word = document.createElement('span');
          word.style.whiteSpace = 'nowrap';
          Array.from(part).forEach(function (ch) {
            var b = document.createElement('b');
            b.textContent = ch;
            letters.push(b);
            word.appendChild(b);
          });
          frag.appendChild(word);
        });
        node.replaceChild(frag, child);
      });
    };
    if (plan.letters) walk(text);
    var scale = plan.maxIndex ? Math.min(1, plan.maxIndex / Math.max(1, letters.length)) : 1;
    letters.forEach(function (b, i) {
      b.style.setProperty('--i', String(Math.round(i * scale * 1000) / 1000));
    });
    text.classList.add(plan.cls);
    var last = plan.letters ? letters[letters.length - 1] : text;
    var onEnd = function (e) {
      if (e.target === last && !e.pseudoElement) done();
    };
    if (!c.repeat) {
      text.setAttribute('data-fx-once', '');
      text.addEventListener('animationend', onEnd);
    }
    return function () {
      text.removeEventListener('animationend', onEnd);
      text.classList.remove(plan.cls);
      text.removeAttribute('data-fx-once');
      if (!plan.letters) return;
      text.textContent = '';
      while (original.firstChild) text.appendChild(original.firstChild);
    };
  }

  // Knight Rider: a row of red lamps across the banner, behind the message, the light sweeping back and forth.
  function kittScanner(el, text, c, done) {
    var cycle = KITT_CYCLE_MS[c.speed] || KITT_CYCLE_MS.normal;
    var lamps = Math.min(KITT_MAX_LAMPS, Math.max(KITT_MIN_LAMPS, Math.round(el.clientWidth / KITT_LAMP_PX)));
    var row = document.createElement('span');
    row.className = 'cb-kitt';
    row.setAttribute('aria-hidden', 'true');
    row.style.setProperty('--kitt-cycle', cycle + 'ms');
    row.style.setProperty('--kitt-n', String(lamps));
    for (var i = 0; i < lamps; i++) {
      var lamp = document.createElement('i');
      lamp.style.setProperty('--i', String(i));
      row.appendChild(lamp);
    }
    if (!c.repeat) {
      row.setAttribute('data-once', '');
      row.style.animationDuration = cycle * KITT_ONCE_CYCLES + 'ms';
      row.addEventListener('animationend', function (e) {
        if (e.target === row) done();
      });
    }
    var hadScreen = el.classList.contains('cb-screen');
    el.classList.add('cb-screen');
    el.insertBefore(row, text);
    return function () {
      row.remove();
      if (!hadScreen) el.classList.remove('cb-screen');
    };
  }

  // The opening crawl: the text tilted away, rising from below the banner and receding, at a steady pace (its
  // duration from its height); without repeat once, then the plain text.
  function crawl(text, c, done) {
    var view = document.createElement('span');
    view.className = 'cb-crawl';
    var inner = document.createElement('span');
    inner.className = 'cb-crawl-text';
    while (text.firstChild) inner.appendChild(text.firstChild);
    view.appendChild(inner);
    text.appendChild(view);
    text.classList.add('cb-crawl-host');
    var travel = inner.offsetHeight + view.offsetHeight;
    inner.style.animationDuration = Math.max(4, travel / (CRAWL_PX_PER_SECOND[c.speed] || CRAWL_PX_PER_SECOND.normal)) + 's';
    inner.style.animationIterationCount = c.repeat ? 'infinite' : '1';
    if (!c.repeat) {
      inner.addEventListener('animationend', function (e) {
        if (e.target === inner) done();
      });
    }
    return function () {
      text.classList.remove('cb-crawl-host');
      while (inner.firstChild) text.appendChild(inner.firstChild);
      view.remove();
    };
  }

  // A stylesheet effect: once fx.css is there (hidden until then when asked, as the Anime.js ones).
  function startCssEffect(el, text, c, plan, hide) {
    var fx = { stopped: false, split: null, undo: null, timer: 0, text: text, base: text.style.cssText };
    activeFx = fx;
    if (hide) {
      text.style.visibility = 'hidden';
      fx.timer = window.setTimeout(function () {
        if (activeFx === fx) stopEffect(); // too slow: this banner stays plain rather than start on visible text
      }, LIB_TIMEOUT_MS);
    }
    loadFxCss().then(function (ok) {
      if (fx.stopped || !text.isConnected) return;
      window.clearTimeout(fx.timer);
      text.style.visibility = '';
      if (!ok) return stopEffect();
      var done = function () {
        if (activeFx === fx) stopEffect();
      };
      if (plan.scanner) fx.undo = kittScanner(el, text, c, done);
      else if (plan.crawl) fx.undo = crawl(text, c, done);
      else fx.undo = letterEffect(text, c, plan, done);
    });
  }

  // Plays `cfg.effect` on the banner's text. `hide`: keep the text invisible until the effect is ready to
  // start, so a typewriter does not flash the whole sentence first (if the library is slow, a timer gives up and shows the plain text).
  function startEffect(el, c, hide) {
    stopEffect();
    var name = c.effect || 'none';
    var text = el.querySelector('[data-banner-text]');
    if (!text || reducedMotion()) return;
    if (CSS_EFFECTS.hasOwnProperty(name)) return startCssEffect(el, text, c, CSS_EFFECTS[name], hide);
    if (!effects.hasOwnProperty(name)) return;
    var fx = { stopped: false, split: null, timer: 0, text: text, base: text.style.cssText };
    activeFx = fx;
    // Clip letters that travel outside the line (a rising letter should slide in, not float over the page),
    // with room to spare so nothing is cut at rest.
    text.style.cssText = fx.base + 'overflow:hidden;padding:10px 4px;margin:-10px -4px;';
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
    // A message, a background picture or a countdown: an announcement may be only a picture (as on the dashboard).
    var wanted = cfg && cfg.enabled && (cfg.message || safeUrl(cfg.picture) || cfg.countdown) && !isDismissed();
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

  // A workspace app (marimo, File Browser, code-server, ... at /@<user>/<workspace>[.<agent>]/apps/<slug>/) is the
  // workspace's own page, not Coder's: no banner in it. The HTTPRoute sends those straight to Coder, unrewritten;
  // this covers a gateway that ranks the routes otherwise.
  if (/^\/@[^/]+\/[^/]+\/apps\//.test(window.location.pathname)) return;

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
