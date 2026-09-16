// The shell the operator consoles (/fanvue, /telegram) are drawn inside.
//
// Those pages used to be one long scroll in which "connect the account" and
// "the operator-only platform bot" sat at the same visual level, so there was
// no way to tell what you still had to do from what you would never touch.
// This module splits them into four tabs and puts one plain-English answer on
// top: is she working, and if not, what is the single next thing to fix.
//
// A page keeps its own markup. It tags each existing section with
// data-tab="settings|advanced" and registers what its status endpoint means;
// this file does the rest, so a third platform is a register() call rather
// than another copy of a console.
(function () {

  var TABS = [
    { key: 'overview', label: 'Overview', icon: '◎' },
    { key: 'inbox',    label: 'Inbox',    icon: '💬' },
    { key: 'settings', label: 'Settings', icon: '⚙' },
    { key: 'advanced', label: 'Advanced', icon: '🔧' }
  ];

  // Icons for the short activity feed. Anything unlisted falls back to a dot,
  // which is the honest answer for a stage this file has not been taught.
  var STAGE_ICON = {
    received: '←', sent: '→', ppv: '💎', error: '⚠',
    guardrail: '🛡', skipped: '⏭', 'follow-up': '↩',
    funnel: '🎯', typing: '✏', idle: '💤',
    delayed: '⏳', webhook: '🔗', routed: '→',
    connected: '🔌', review: '👤', restarted: '↻',
    history: '📜', chats: '👥'
  };

  var cfg = null, view = null, timer = null;
  var state = { tab: 'overview', showAll: false };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function ago(ts) {
    if (!ts) return '';
    var s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    if (s < 60) return 'now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function el(html) {
    var d = document.createElement('div');
    d.innerHTML = html;
    return d.firstElementChild;
  }

  // ---- layout ------------------------------------------------------------

  function build(wrap) {
    var bar = el('<div class="cn-tabs" role="tablist"></div>');
    var panes = {};
    TABS.forEach(function (t) {
      var b = el('<button class="cn-tab" role="tab" data-tab="' + t.key + '">' +
        '<span class="cn-tab-ic">' + t.icon + '</span>' + t.label +
        '<span class="cn-tab-badge" hidden></span></button>');
      b.onclick = function () { api.show(t.key); };
      bar.appendChild(b);
      panes[t.key] = el('<div class="cn-pane" data-pane="' + t.key + '"></div>');
    });

    // The page's own sections keep their markup, their ids and their handlers —
    // they are only moved into the pane they belong to. Anything the page did
    // not tag lands on Settings, which is the safe default: visible, not buried.
    var owned = Array.prototype.slice.call(wrap.children);
    owned.forEach(function (n) {
      var t = n.dataset ? n.dataset.tab : '';
      panes[panes[t] ? t : 'settings'].appendChild(n);
    });

    panes.overview.innerHTML =
      '<div class="cn-hero"><span class="cn-hero-dot"></span>' +
        '<div class="cn-hero-txt"><div class="cn-hero-title">Checking…</div>' +
        '<div class="cn-hero-sub"></div></div></div>' +
      '<div class="cn-steps"></div>' +
      '<div class="cn-next"></div>' +
      '<div class="cn-health"></div>' +
      '<div class="cn-stats"></div>' +
      '<div class="form-section"><div class="section-title">Recent activity</div>' +
        '<span class="hint" style="display:block;margin-bottom:10px;">The last few things that ' +
        'happened. The whole log is under <b>Advanced</b>, and the conversations themselves are ' +
        'under <b>Inbox</b>.</span><div class="cn-feed"></div></div>';

    panes.inbox.innerHTML =
      '<div class="form-section" style="padding:0;overflow:hidden;">' +
      '<div class="cn-chat"></div></div>';

    wrap.appendChild(bar);
    TABS.forEach(function (t) { wrap.appendChild(panes[t.key]); });
    return panes;
  }

  // ---- overview rendering -------------------------------------------------

  function renderHero(d) {
    var read = cfg.read(d);
    var hero = document.querySelector('.cn-hero');
    hero.className = 'cn-hero ' + (read.live ? 'live' : read.broken ? 'off' : 'warn');
    hero.querySelector('.cn-hero-title').textContent = read.title;
    hero.querySelector('.cn-hero-sub').textContent = read.sub || '';
    return read;
  }

  function renderSteps(read) {
    var box = document.querySelector('.cn-steps');
    var steps = read.steps || [];
    var now = -1;
    for (var i = 0; i < steps.length; i++) { if (!steps[i].done) { now = i; break; } }
    box.innerHTML = steps.map(function (s, i) {
      var cls = s.done ? 'done' : (i === now ? 'now' : '');
      return '<button class="cn-step ' + cls + '" data-go="' + esc(s.tab || 'settings') +
        '" data-focus="' + esc(s.focus || '') + '">' +
        '<span class="cn-step-n">' + (s.done ? '✓' : (i + 1)) + '</span>' +
        esc(s.label) + '</button>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.cn-step'), function (b) {
      b.onclick = function () { api.show(b.dataset.go, b.dataset.focus); };
    });
  }

  function renderHealth(read) {
    document.querySelector('.cn-health').innerHTML = (read.health || []).map(function (h) {
      return '<div class="cn-hcard"><span class="cn-dot ' + esc(h.state) + '"></span>' +
        '<span><span class="cn-hcard-l">' + esc(h.label) + '</span>' +
        (h.hint ? '<span class="cn-hcard-h">' + esc(h.hint) + '</span>' : '') +
        '</span></div>';
    }).join('');
  }

  // One next step, never a wall of them. The rest stay one click away, because
  // a list of six warnings is the thing that made the old page unreadable.
  function renderNext(d) {
    var box = document.querySelector('.cn-next');
    var problems = (d.problems || []).filter(Boolean);
    if (!problems.length) {
      box.className = 'cn-next ok';
      box.innerHTML = '<div class="cn-next-txt"><div class="cn-next-eyebrow">All set</div>' +
        '<div class="cn-next-body">Nothing needs your attention. She is answering on her own.</div></div>';
      return;
    }
    var first = problems[0];
    var go = cfg.fixTab ? (cfg.fixTab(first) || {}) : {};
    box.className = 'cn-next';
    box.innerHTML = '<div class="cn-next-txt">' +
      '<div class="cn-next-eyebrow">Next step</div>' +
      '<div class="cn-next-body">' + esc(first) + '</div>' +
      (problems.length > 1
        ? '<button class="cn-more">' + (state.showAll ? 'Hide' : 'Show') + ' the other ' +
          (problems.length - 1) + '</button>' +
          (state.showAll ? '<ul class="cn-more-list">' + problems.slice(1).map(function (p) {
            return '<li>' + esc(p) + '</li>'; }).join('') + '</ul>' : '')
        : '') +
      '</div>' +
      (go.tab ? '<button class="btn-ai cn-next-btn">' + esc(go.label || 'Take me there') +
        ' →</button>' : '');
    var more = box.querySelector('.cn-more');
    if (more) more.onclick = function () { state.showAll = !state.showAll; renderNext(d); };
    var btn = box.querySelector('.cn-next-btn');
    if (btn) btn.onclick = function () { api.show(go.tab, go.focus); };
  }

  function renderStats(rows) {
    document.querySelector('.cn-stats').innerHTML = (rows || []).map(function (s) {
      return '<div class="cn-stat"><div class="cn-stat-v">' + esc(s.value) +
        '</div><div class="cn-stat-l">' + esc(s.label) + '</div></div>';
    }).join('');
  }

  function renderFeed(d) {
    var rows = (d.rows || []).slice(-14).reverse();
    var box = document.querySelector('.cn-feed');
    if (!rows.length) {
      box.innerHTML = '<div class="cn-empty">Nothing has happened yet.</div>';
      return;
    }
    box.innerHTML = rows.map(function (r) {
      return '<div class="cn-feed-row">' +
        '<span class="cn-feed-ic">' + (STAGE_ICON[r.stage] || '•') + '</span>' +
        '<span class="cn-feed-txt">' + esc(r.detail || r.stage) +
        (r.fan ? '<button class="cn-feed-go" data-fan="' + esc(r.fan) + '">open chat</button>' : '') +
        '</span><span class="cn-feed-at">' + esc(ago(r.at)) + '</span></div>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.cn-feed-go'), function (b) {
      b.onclick = function () {
        api.show('inbox');
        if (view) view.openThread(b.dataset.fan);
      };
    });
  }

  // ---- polling ------------------------------------------------------------

  function poll() {
    var slug = cfg.persona();
    if (!slug) return Promise.resolve();
    return fetch(cfg.statusUrl(slug)).then(function (r) { return r.json(); })
      .then(function (d) {
        var read = renderHero(d);
        renderSteps(read);
        renderNext(d);
        renderHealth(read);
        renderFeed(d);
        markTabs(d);
        // The page still owns its full log and whatever else it draws from the
        // same payload — one fetch, not two.
        if (cfg.onStatus) cfg.onStatus(d);
        if (cfg.stats) Promise.resolve(cfg.stats(slug)).then(renderStats).catch(function () {});
      })
      .catch(function (e) {
        var hero = document.querySelector('.cn-hero');
        hero.className = 'cn-hero off';
        hero.querySelector('.cn-hero-title').textContent = 'Could not reach the server';
        hero.querySelector('.cn-hero-sub').textContent = String(e.message || e);
      });
  }

  function markTabs(d) {
    var bad = (d.problems || []).length;
    var b = document.querySelector('.cn-tab[data-tab="overview"] .cn-tab-badge');
    b.hidden = !bad;
    b.className = 'cn-tab-badge warn';
    b.textContent = bad || '';
  }

  // How many fans wrote last and have had no answer — the one number on this
  // page that means "go and look now".
  function waitingBadge(n) {
    var ib = document.querySelector('.cn-tab[data-tab="inbox"] .cn-tab-badge');
    if (!ib) return;
    ib.hidden = !n;
    ib.className = 'cn-tab-badge';
    ib.textContent = n || '';
  }

  // ---- api ----------------------------------------------------------------

  var api = {
    register: function (options) { cfg = options; },

    mount: function (wrapSelector) {
      var wrap = document.querySelector(wrapSelector);
      if (!wrap || !cfg) return;
      build(wrap);
      view = window.ChatView.create(wrap.querySelector('.cn-chat'), {
        platform: cfg.platform,
        persona: cfg.persona,
        emptyHint: cfg.emptyHint || '',
        sendUrl: cfg.sendUrl || ''
      });
      view.onWaiting = waitingBadge;
      api.show((location.hash || '').replace('#', '') || 'overview');
      window.addEventListener('hashchange', function () {
        api.show((location.hash || '').replace('#', '') || 'overview');
      });
      api.refresh();
      timer = setInterval(function () {
        if (document.hidden) return;
        if (cfg.paused && cfg.paused()) return;
        api.refresh();
      }, 6000);
    },

    show: function (tab, focusId) {
      if (!TABS.some(function (t) { return t.key === tab; })) tab = 'overview';
      state.tab = tab;
      Array.prototype.forEach.call(document.querySelectorAll('.cn-tab'), function (b) {
        b.classList.toggle('on', b.dataset.tab === tab);
      });
      Array.prototype.forEach.call(document.querySelectorAll('.cn-pane'), function (p) {
        p.classList.toggle('on', p.dataset.pane === tab);
      });
      if (location.hash.replace('#', '') !== tab) {
        try { history.replaceState(null, '', '#' + tab); } catch (e) { location.hash = tab; }
      }
      // The console is a 760px column of forms; a conversation is not, so the
      // page is allowed to breathe for the one tab that needs the width.
      var wrap = document.querySelector('.fv-wrap, .xbot-wrap');
      if (wrap) wrap.style.maxWidth = tab === 'inbox' ? '1120px' : '';
      if (tab === 'inbox' && view) view.refresh();
      if (focusId) {
        var node = document.getElementById(focusId);
        if (node) {
          node.scrollIntoView({ behavior: 'smooth', block: 'center' });
          node.classList.add('cn-flash');
          setTimeout(function () { node.classList.remove('cn-flash'); }, 1600);
        }
      } else {
        var scroll = document.querySelector('.fv-scroll, .xbot-scroll');
        if (scroll) scroll.scrollTop = 0;
      }
    },

    refresh: function () {
      poll();
      // The fan list is refreshed even when the Inbox is closed: it is what the
      // tab's "someone is waiting" badge counts, and that has to be visible
      // from the Overview or nobody knows to look.
      if (view) view.refresh().then(function () { view.markWaiting(); });
    },

    // The pages call this after switching persona, so nothing from the previous
    // model is left on screen while the new one loads.
    reset: function () {
      if (view) { view.fan = ''; view.fans = []; }
      state.showAll = false;
      api.refresh();
    },

    inbox: function () { return view; }
  };

  // Named in full rather than `Console`: one typo away from the browser's own
  // console object is not a global worth having.
  window.PlatformConsole = api;
})();
