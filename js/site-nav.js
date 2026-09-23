// The site menu, in one place. Blog is always offered; Dashboard only once the
// visitor is signed in. Two modes so a page never grows a second bar:
//
//   bar     — injects the floating pill nav (marketing pages with no header)
//   inline  — fills an existing <nav data-site-nav="inline"> in the page's own
//             header (dashboard and the platform consoles)
//
// A page that already ships .site-nav markup (comingsoon.html) keeps it: only
// the links inside are rendered, so its entry animation and layout are untouched.
(function () {
  // The landing page embeds the fan chat in an iframe; the menu belongs to the
  // outer page only. Same guard as js/devnav.js.
  if (window.self !== window.top) return;

  // One "Features" dropdown over every public platform and tool page. The
  // pages are otherwise only reachable from the homepage footer, and a menu
  // is the link a visitor actually follows.
  var FEATURES = [
    { group: 'Platforms', items: [
      { href: '/telegram-ai-chatbot', label: 'Telegram AI chatbot' },
      { href: '/discord-ai-chatbot', label: 'Discord AI chatbot' },
      { href: '/x-ai-bot', label: 'X (Twitter) AI bot' },
      { href: '/instagram-posting-automation', label: 'Instagram posting' },
      { href: '/threads-auto-reply', label: 'Threads auto-reply' },
      { href: '/reddit-posting-bot', label: 'Reddit posting bot', soon: true },
      { href: '/tiktok-posting-automation', label: 'TikTok posting', soon: true },
    ] },
    { group: 'Paid pages', items: [
      { href: '/fanvue-ai-chatter', label: 'Fanvue AI chatter' },
      { href: '/onlyfans-ai-chatbot', label: 'OnlyFans AI chatbot', soon: true },
      { href: '/fansly-ai-chatbot', label: 'Fansly AI chatbot', soon: true },
      { href: '/velvetchat-share-link', label: 'Your own chat page' },
    ] },
    { group: 'Tools', items: [
      { href: '/ai-content-planner', label: 'AI content planner' },
      { href: '/ai-image-generator', label: 'AI image generator', soon: true },
    ] },
  ];

  var PAGE_LINKS = [
    { menu: 'Features', groups: FEATURES },
    { href: '/#pricing', label: 'Pricing' },
    { href: '/blog', label: 'Blog', keep: true },
  ];
  var ACCOUNT_OUT = [
    { href: '/login', label: 'Log in', icon: 'login' },
    { href: '/register', label: 'Register', icon: 'register', cta: true },
  ];
  var ACCOUNT_IN = [
    { href: '/tokens', label: 'Tokens', icon: 'tokens', cta: true, keep: true, tokens: true },
    { href: '/billing', label: 'Upgrade', icon: 'upgrade', cta: true, keep: true },
    { href: '/dashboard', label: 'Dashboard', icon: 'dashboard', cta: true },
    { href: '/logout', label: 'Log out', icon: 'logout' },
  ];

  function svg(paths) {
    return '<span class="sn-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
      ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      paths + '</svg></span>';
  }
  var ICONS = {
    login: svg('<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="M10 17l5-5-5-5"/><path d="M15 12H3"/>'),
    register: svg('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M22 11h-6"/>'),
    dashboard: svg('<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>'),
    logout: svg('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>'),
    upgrade: svg('<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>'),
    tokens: svg('<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>'),
    account: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>'),
  };

  var BRAND_HTML = '<a href="/" class="brand">' +
    '<span data-sn-brand>Velvetfunnel</span><i data-sn-suffix>.app</i></a>';

  // The homepage's brand wording is operator-editable and stored under the
  // "home" page content; every page reads it so the name never diverges.
  function paintBrand() {
    var name = document.querySelector('[data-sn-brand]');
    var suffix = document.querySelector('[data-sn-suffix]');
    if (!name && !suffix) return;
    fetch('/api/site-content/home', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : { content: {} }; })
      .then(function (d) {
        var c = (d && d.content) || {};
        if (name && c['brand-name']) name.textContent = c['brand-name'];
        if (suffix && c['brand-suffix']) suffix.textContent = c['brand-suffix'];
      })
      .catch(function () {});
  }

  function isCurrent(href) {
    if (href.indexOf('#') !== -1) return false;
    var path = location.pathname;
    return path === href || (href !== '/' && path.indexOf(href) === 0);
  }

  function menuHtml(i) {
    var open = i.groups.some(function (g) {
      return g.items.some(function (it) { return isCurrent(it.href); });
    });
    return '<div class="sn-menu' + (open ? ' sn-menu-here' : '') + '" data-sn>' +
      '<button type="button" class="sn-menu-btn" aria-expanded="false">' +
      '<span class="sn-label">' + i.menu + '</span>' +
      '<svg class="sn-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="m6 9 6 6 6-6"/></svg></button>' +
      '<div class="sn-menu-panel">' + i.groups.map(function (g) {
        return '<div class="sn-menu-group"><span class="sn-menu-label">' + g.group + '</span>' +
          g.items.map(function (it) {
            return '<a href="' + it.href + '"' +
              (isCurrent(it.href) ? ' aria-current="page"' : '') + '>' + it.label +
              (it.soon ? '<i class="sn-soon">Soon</i>' : '') + '</a>';
          }).join('') + '</div>';
      }).join('') + '</div></div>';
  }

  function linksHtml(items) {
    return items.map(function (i) {
      if (i.menu) return menuHtml(i);
      return '<a data-sn href="' + i.href + '"' +
        (i.cta ? ' class="sn-cta"' : '') +
        (i.keep ? ' data-keep' : '') +
        (i.tokens ? ' data-sn-tokens title="Buy tokens"' : '') +
        (isCurrent(i.href) ? ' aria-current="page"' : '') +
        '>' + (i.icon ? ICONS[i.icon] : '') +
        '<span class="sn-label">' + i.label + '</span></a>';
    }).join('');
  }

  // Every host for the links on this page: the inline containers a page declared,
  // plus the .auth/.sn-links list inside a .site-nav it already had.
  function hosts() {
    var out = [].slice.call(document.querySelectorAll('[data-site-nav="inline"]'));
    var bar = document.querySelector('.site-nav');
    if (bar) {
      var list = bar.querySelector('.sn-links') || bar.querySelector('.auth');
      if (list) out.push(list);
    }
    return out;
  }

  // Marketing pages get a second pill on the right for the account actions;
  // inline hosts (dashboard, consoles) keep one row and just gain the icons.
  function accountHost() {
    if (!document.querySelector('.site-nav')) return null;
    var pill = document.querySelector('.sn-account');
    if (pill) return pill.querySelector('.sn-acct-links');
    pill = document.createElement('nav');
    pill.className = 'sn-account';
    pill.innerHTML = '<button class="sn-acct-btn" type="button" aria-expanded="false" ' +
      'aria-label="Account menu">' + ICONS.account + '</button>' +
      '<div class="sn-acct-links sn-links"></div>';
    document.body.appendChild(pill);

    var toggle = document.querySelector('.site-nav .theme-toggle');
    if (toggle) pill.insertBefore(toggle, pill.firstChild);

    var btn = pill.querySelector('.sn-acct-btn');
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = pill.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
      if (!pill.contains(e.target)) close();
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    function close() {
      pill.classList.remove('open');
      btn.setAttribute('aria-expanded', 'false');
    }
    addEventListener('scroll', function () {
      pill.classList.toggle('scrolled', scrollY > 10);
    }, { passive: true });
    return pill.querySelector('.sn-acct-links');
  }

  function fill(host, items) {
    [].slice.call(host.querySelectorAll('[data-sn]')).forEach(function (el) { el.remove(); });
    host.insertAdjacentHTML('beforeend', linksHtml(items));
    [].slice.call(host.querySelectorAll('.sn-menu')).forEach(wireMenu);
  }

  // Click to open, because the bar is reachable on touch as well as hover.
  function wireMenu(menu) {
    var btn = menu.querySelector('.sn-menu-btn');
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = menu.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
      if (!menu.contains(e.target)) shut();
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') shut(); });
    function shut() {
      menu.classList.remove('open');
      btn.setAttribute('aria-expanded', 'false');
    }
  }

  function paint(account) {
    var pill = accountHost();
    hosts().forEach(function (host) {
      host.classList.add('sn-links');
      // Inline hosts are the app's own chrome (dashboard, consoles): the
      // marketing links belong to the marketing pages only.
      var inline = host.matches('[data-site-nav="inline"]');
      if (inline) host.classList.add('sn-inline');
      var links = inline ? [] : PAGE_LINKS;
      // Only ever replace our own links: a page's theme toggle and its own
      // entries (comingsoon.html's Pricing) share this container.
      fill(host, pill ? links : links.concat(account));
    });
    if (pill) fill(pill, account);
  }

  // The header's token count. Painted from /api/me on load, then refreshed
  // whenever this page does something that spends or refunds tokens.
  var tokenBalance;
  function paintTokens(balance) {
    tokenBalance = balance;
    [].slice.call(document.querySelectorAll('[data-sn-tokens] .sn-label')).forEach(function (el) {
      el.innerHTML = balance === null ? 'Unlimited'
        : Number(balance).toLocaleString() +
          '<span class="sn-tok-unit">' + (balance === 1 ? ' token' : ' tokens') + '</span>';
    });
  }

  function refreshTokens() {
    if (tokenBalance === undefined) return;
    return nativeFetch('/api/tokens/balance', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && 'balance' in d) paintTokens(d.balance); })
      .catch(function () {});
  }

  // Spending happens on half a dozen pages (studio, dashboard, the consoles'
  // image buttons, the character builder), so the count listens to fetch here
  // instead of every page remembering to report a spend. A job poll is where a
  // refund for a failed generation surfaces; those run every few seconds, so
  // they only refresh on a throttle.
  var nativeFetch = window.fetch.bind(window);
  var SPEND = /^\/api\/(generate\/|characters\/[^/]+\/generate)/;
  var POLL = /^\/api\/generate\/jobs?(\/|$|\?)/;
  var lastPollRefresh = 0;
  window.fetch = function (input, init) {
    var p = nativeFetch.apply(null, arguments);
    try {
      var url = new URL(typeof input === 'string' ? input : input.url, location.href);
      var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      if (url.origin === location.origin && url.pathname !== '/api/generate/tick') {
        var spend = method === 'POST' && SPEND.test(url.pathname);
        var poll = method === 'GET' && POLL.test(url.pathname) && Date.now() - lastPollRefresh > 10000;
        if (spend || poll) {
          if (poll) lastPollRefresh = Date.now();
          p.then(refreshTokens, function () {});
        }
      }
    } catch (e) { /* never let the counter break a request */ }
    return p;
  };
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') refreshTokens();
  });

  function mount() {
    // No host and no header of its own: this page wants the whole bar.
    if (!hosts().length && !document.querySelector('header')) {
      var bar = document.createElement('header');
      bar.className = 'site-nav';
      bar.id = bar.id || 'site-nav';
      bar.innerHTML = BRAND_HTML + '<nav class="sn-links"></nav>';
      document.body.insertBefore(bar, document.body.firstChild);
    }

    paintBrand();

    // Signed-out links go up straight away; a slow /api/me would otherwise leave
    // the bar empty on first paint.
    paint(ACCOUNT_OUT);

    fetch('/api/me', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (me) {
        if (!me || !me.signed_in) return;
        paint(ACCOUNT_IN);
        var t = me.usage && me.usage.tokens;
        paintTokens(t && 'balance' in t ? t.balance : 0);
      })
      .catch(function () { /* keep the signed-out menu */ });

    var bar2 = document.querySelector('.site-nav');
    if (bar2) {
      addEventListener('scroll', function () {
        bar2.classList.toggle('scrolled', scrollY > 10);
      }, { passive: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
