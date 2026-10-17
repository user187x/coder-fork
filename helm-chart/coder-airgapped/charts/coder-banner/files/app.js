/* Coder banner admin page. All text from the server is inserted with textContent. */
(function () {
  'use strict';

  var API = '/__banner/api/';
  var PRESETS = {
    maintenance: {
      level: 'warning', title: 'Scheduled maintenance:', dismissible: true,
      message: 'Coder will be unavailable on [date] from [start] to [end] UTC. Please save your work and stop your workspaces beforehand.',
    },
    emergency: {
      level: 'critical', title: 'Emergency security patch:', dismissible: false,
      message: 'Coder will restart at [time] UTC to apply a critical security fix. Save your work and push any code you need to keep now.',
    },
    clear: {
      level: 'success', title: 'All clear:', dismissible: true,
      message: 'Maintenance is complete and Coder is back to normal. Thanks for your patience.',
    },
  };
  var TEXT_FIELDS = ['message', 'title', 'linkText', 'linkUrl', 'picture'];
  var BOOL_FIELDS = ['dismissible', 'showOnLoginPage', 'repeat', 'outline'];
  var SELECT_FIELDS = ['effect', 'speed', 'pictureFit'];
  // The effects whose pace the speed sets: the crawl and the scanner.
  var PACED_EFFECTS = ['starwars', 'kitt'];
  var PLAY_DELAY_MS = 600; // let typing settle before (re)playing the effect in the preview

  var $ = function (id) { return document.getElementById(id); };
  var server = null; // last state from the service
  var busy = false;
  var toastTimer = null;
  var previewEl = null;
  var playTimer = null;

  function api(path, body) {
    var options = { credentials: 'same-origin', headers: {} };
    if (body !== undefined) {
      options.method = 'POST';
      options.headers = { 'Content-Type': 'application/json', 'X-Requested-With': 'coder-banner-admin' };
      options.body = JSON.stringify(body);
    }
    return fetch(API + path, options).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (res.status === 401) {
          window.location.href = '/login?redirect=' + encodeURIComponent('/__banner/admin');
          return new Promise(function () {});
        }
        if (!res.ok) throw new Error(data.error || 'Request failed (HTTP ' + res.status + ')');
        return data;
      });
    });
  }

  // ---- form <-> banner
  // Custom colours, as the dashboard's editor keeps them: a token in the (otherwise unused) lead-in.
  var COLORS_RE = /^\{\{colors bg=(#[0-9a-fA-F]{6}) fg=(#[0-9a-fA-F]{6})\}\}$/;
  function colorsOf(title) {
    var m = COLORS_RE.exec(String(title || '').trim());
    return m ? { bg: m[1].toLowerCase(), fg: m[2].toLowerCase() } : null;
  }
  // The lead-in as compared: a colours token whatever the case of its digits.
  function leadIn(title) {
    var c = colorsOf(title);
    return c ? '{{colors bg=' + c.bg + ' fg=' + c.fg + '}}' : title || '';
  }
  // What is published, with this page's fields over it: what only the dashboard's editor sets (the font, the
  // alignment, the countdown, receipts, the open and close transitions, Markdown) is kept as it is.
  function readForm() {
    var out = Object.assign({}, server ? server.banner : { enabled: true });
    delete out.id;
    TEXT_FIELDS.forEach(function (k) { out[k] = $(k).value.trim(); });
    BOOL_FIELDS.forEach(function (k) { out[k] = $(k).checked; });
    SELECT_FIELDS.forEach(function (k) { out[k] = $(k).value; });
    if ($('customColors').checked) out.title = '{{colors bg=' + $('colorBg').value + ' fg=' + $('colorFg').value + '}}';
    var level = document.querySelector('input[name=level]:checked');
    out.level = level ? level.value : 'info';
    out.refreshSeconds = parseInt($('refreshSeconds').value, 10) || 60;
    return out;
  }

  // A banner saved before a field existed does not have it: that means its default, not "changed".
  var FIELD_DEFAULTS = { effect: 'none', repeat: false, speed: 'normal', picture: '', pictureFit: 'span', outline: false };

  function value(b, k) {
    return b[k] === undefined && k in FIELD_DEFAULTS ? FIELD_DEFAULTS[k] : b[k];
  }

  function writeForm(b) {
    TEXT_FIELDS.forEach(function (k) { $(k).value = value(b, k) || ''; });
    BOOL_FIELDS.forEach(function (k) { $(k).checked = !!value(b, k); });
    SELECT_FIELDS.forEach(function (k) {
      $(k).value = value(b, k);
      if (!$(k).value) $(k).value = FIELD_DEFAULTS[k]; // a value this page does not know about
    });
    var colors = colorsOf(b.title);
    $('customColors').checked = !!colors;
    if (colors) {
      $('colorBg').value = colors.bg;
      $('colorFg').value = colors.fg;
      $('title').value = '';
    }
    var radio = document.querySelector('input[name=level][value=' + (b.level || 'info') + ']');
    if (radio) radio.checked = true;
    $('refreshSeconds').value = b.refreshSeconds || 60;
  }

  function sameAsServer(form) {
    var s = server.banner;
    return TEXT_FIELDS.concat(BOOL_FIELDS, SELECT_FIELDS, ['level', 'refreshSeconds']).every(function (k) {
      return k === 'title' ? leadIn(form.title) === leadIn(s.title) : form[k] === value(s, k);
    });
  }

  // A message, a background picture or a countdown: an announcement may be only a picture.
  function hasContent(b) {
    return !!(b.message || b.picture || b.countdown);
  }

  // ---- rendering
  // The preview text is redrawn on every keystroke; the effect is (re)played once typing settles.
  function playPreview() {
    window.clearTimeout(playTimer);
    if (previewEl && previewEl.isConnected) window.__coderBannerPreview.play(previewEl, readForm());
  }

  function renderEffectHint() {
    var option = $('effect').selectedOptions[0];
    var hint = option ? option.getAttribute('data-hint') : '';
    if (window.__coderBannerPreview.reducedMotion()) hint += ' This device asks for reduced motion, so effects are not played here, or for anyone with that setting.';
    $('effect-hint').textContent = hint;
    $('repeat').disabled = $('effect').value === 'none';
    $('replay').disabled = $('effect').value === 'none' || !previewEl;
    $('speed-field').hidden = PACED_EFFECTS.indexOf($('effect').value) < 0;
    var custom = $('customColors').checked;
    $('title').disabled = custom;
    $('emoji-btn').disabled = custom;
    $('colorBg').disabled = $('colorFg').disabled = !custom;
  }

  function renderPreview() {
    var form = readForm();
    var box = $('preview');
    window.clearTimeout(playTimer);
    window.__coderBannerPreview.stop();
    previewEl = null;
    box.textContent = '';
    if (!hasContent(form)) {
      var empty = document.createElement('div');
      empty.className = 'preview-empty';
      empty.textContent = 'Type a message (or give a background picture) to see a preview.';
      box.appendChild(empty);
    } else {
      form.enabled = true;
      previewEl = window.__coderBannerPreview.build(form);
      box.appendChild(previewEl);
      if (form.effect !== 'none') playTimer = window.setTimeout(playPreview, PLAY_DELAY_MS);
    }
    renderEffectHint();
  }

  // "3 open tabs" - how many browsers are holding the live connection right now.
  function tabsText(prefix, suffix) {
    if (!server || typeof server.subscribers !== 'number') return '';
    return prefix + server.subscribers + ' open tab' + (server.subscribers === 1 ? '' : 's') + suffix;
  }

  function renderStatus() {
    var pill = $('status');
    var on = server.banner.enabled && hasContent(server.banner);
    pill.textContent = on ? '● Live for all developers' + tabsText(' · ', ' connected') : '○ Hidden';
    pill.className = 'pill ' + (on ? 'live' : 'off');
    $('toggle').textContent = on ? 'Hide banner' : 'Show banner';
    $('reappear').disabled = !hasContent(server.banner);
  }

  function renderState() {
    var form = readForm();
    var dirty = !sameAsServer(form);
    $('count').textContent = $('message').value.length + ' / 4000';
    $('publish').disabled = busy || !dirty || !hasContent(form);
    $('revert').disabled = busy || !dirty;
    $('toggle').disabled = busy;
    $('reappear').disabled = busy || !hasContent(server.banner);
    $('reset').hidden = !server.overrideActive;
    var note = '';
    if (dirty) note = 'You have unpublished changes.';
    else if (/\[[^\]]+\]/.test(form.message)) note = 'Replace the [bracketed] parts of the message before publishing.';
    $('dirty').textContent = note;
    renderPreview();
  }

  function renderMeta() {
    var meta = $('meta');
    if (server.overrideActive && server.updatedBy) {
      meta.textContent = 'Last published by ' + server.updatedBy + ' on ' + new Date(server.updatedAt).toLocaleString() + '.';
    } else if (server.overrideActive) {
      meta.textContent = 'Published from this page.';
    } else {
      meta.textContent = 'Nothing has been published from this page yet, so the chart defaults are showing.';
    }
    var secs = server.banner.refreshSeconds;
    $('reappear-note').textContent = 'Tabs with a live connection' + tabsText(' (', ' right now)') + ' update instantly; any other open tab within about ' + secs +
      ' seconds; anyone who loads Coder afterwards sees it immediately.';
  }

  function apply(data, resetForm) {
    server = data;
    $('who').textContent = 'Signed in as ' + data.user.username;
    if (resetForm) writeForm(data.banner);
    renderStatus();
    renderMeta();
    renderState();
  }

  function toast(text, isError) {
    var el = $('toast');
    el.textContent = text;
    el.className = 'toast' + (isError ? ' error' : '');
    el.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { el.hidden = true; }, isError ? 8000 : 4000);
  }

  function showProblem(text) {
    var el = $('problem');
    el.textContent = text || '';
    el.hidden = !text;
  }

  // ---- actions
  function run(promise, success) {
    busy = true;
    showProblem('');
    renderState();
    return promise.then(function (data) {
      busy = false;
      apply(data, true);
      if (success) toast(success + (typeof data.delivered === 'number' ? ' Sent instantly to ' + data.delivered + ' open tab' + (data.delivered === 1 ? '' : 's') + '.' : ''));
    }).catch(function (e) {
      busy = false;
      renderState();
      showProblem(e.message);
      toast(e.message, true);
    });
  }

  function publish() {
    var form = readForm();
    if (/\[[^\]]+\]/.test(form.message) && !window.confirm('The message still contains [bracketed] placeholders. Publish it anyway?')) return;
    form.enabled = true;
    run(api('banner', form), 'Published. Developers will see the new message.');
  }

  function toggle() {
    var current = Object.assign({}, server.banner);
    current.enabled = !server.banner.enabled;
    if (current.enabled && !hasContent(current)) { showProblem('Write and publish a message first.'); return; }
    run(api('banner', current), current.enabled ? 'Banner is showing again.' : 'Banner hidden.');
  }

  function reappear() {
    var ok = window.confirm('Show the banner again to EVERYONE, including people who dismissed it?\n\nTabs with a live connection' + tabsText(' (', ' right now)') + ' will show it instantly; any other open tab within about ' + server.banner.refreshSeconds + ' seconds.');
    if (!ok) return;
    run(api('reappear', {}), 'Done. The banner will reappear for everyone, including people who dismissed it.');
  }

  function reset() {
    if (!window.confirm('Discard what was published from this page and go back to the chart defaults?')) return;
    run(api('reset', {}), 'Back to the chart defaults.');
  }

  function preset(name) {
    var p = PRESETS[name];
    var f = readForm();
    f.level = p.level; f.title = p.title; f.message = p.message; f.dismissible = p.dismissible;
    writeForm(f);
    renderState();
    $('message').focus();
  }

  function init() {
    ['input', 'change'].forEach(function (evt) {
      document.querySelector('main').addEventListener(evt, function (e) {
        if (e.target.closest('#preview') || !server) return;
        renderState();
      });
    });
    // The picker is a separate script; if it did not load, everything else still works (the button just does nothing).
    if (window.CoderBannerEmojiPicker) {
      window.CoderBannerEmojiPicker.init({ button: $('emoji-btn'), panel: $('emoji-pop'), fields: { message: $('message'), title: $('title') } });
    }
    $('replay').addEventListener('click', function () { renderPreview(); playPreview(); });
    $('publish').addEventListener('click', publish);
    $('revert').addEventListener('click', function () { writeForm(server.banner); showProblem(''); renderState(); });
    $('toggle').addEventListener('click', toggle);
    $('reappear').addEventListener('click', reappear);
    $('reset').addEventListener('click', reset);
    Array.prototype.forEach.call(document.querySelectorAll('[data-preset]'), function (b) {
      b.addEventListener('click', function () { preset(b.getAttribute('data-preset')); });
    });
    window.addEventListener('beforeunload', function (e) {
      if (server && !sameAsServer(readForm())) { e.preventDefault(); e.returnValue = ''; }
    });
    window.setInterval(function () {
      if (!server || document.hidden) return;
      api('state').then(function (data) {
        server.subscribers = data.subscribers;
        renderStatus();
        renderMeta();
      }).catch(function () { /* the next tick will try again */ });
    }, 15000);
    api('state').then(function (data) { apply(data, true); }).catch(function (e) {
      $('status').textContent = 'Error';
      showProblem(e.message);
    });
  }

  init();
})();
