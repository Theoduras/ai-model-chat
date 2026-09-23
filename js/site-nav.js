// Token spend watcher. Spending happens on half a dozen pages (studio, the
// character builder, the dashboard and the consoles' image buttons), so it is
// caught here from fetch instead of every page remembering to report it. It
// runs in every window, framed ones included: the studio and the character
// builder live inside the dashboard's iframe, while the counter lives in the
// top window's header, so a framed page posts the news up to it. A job poll is
// where a refund for a failed generation surfaces; those run every few
// seconds, so they only report on a throttle.
(function () {
  if (!window.fetch) return;
  var framed = window.self !== window.top;
  var SPEND = /^\/api\/(generate\/|characters\/[^/]+\/generate)/;
  var POLL = /^\/api\/generate\/jobs?(\/|$|\?)/;
  var lastPoll = 0;
  // A refused write on a plan that cannot go live, or a spend the balance
  // cannot cover, is the moment to show the upgrade offer. The offer lives in
  // the top window, so a framed page hands it up the same way.
  function blocked(d) {
    var msg = { snUpgrade: (d && d.capability) || 'tokens' };
    if (framed) {
      try { window.top.postMessage(msg, location.origin); } catch (e) {}
    } else {
      document.dispatchEvent(new CustomEvent('sn-upgrade', { detail: msg }));
    }
  }
  function changed() {
    if (framed) {
      try { window.top.postMessage({ snTokens: 'changed' }, location.origin); } catch (e) {}
    } else {
      document.dispatchEvent(new Event('sn-tokens-changed'));
    }
  }
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var p = nativeFetch.apply(null, arguments);
    try {
      var url = new URL(typeof input === 'string' ? input : input.url, location.href);
      var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      if (url.origin === location.origin && url.pathname !== '/api/generate/tick') {
        var spend = method === 'POST' && SPEND.test(url.pathname);
        var poll = method === 'GET' && POLL.test(url.pathname) && Date.now() - lastPoll > 10000;
        if (spend || poll) {
          if (poll) lastPoll = Date.now();
          p.then(changed, function () {});
        }
        if (method !== 'GET') {
          p.then(function (r) {
            if (r.status !== 402) return;
            r.clone().json().then(function (d) {
              if (d && (d.free || d.capability === 'platform' || 'need' in d)) blocked(d);
            }, function () {});
          }, function () {});
        }
      }
    } catch (e) { /* never let the counter break a request */ }
    return p;
  };
})();

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
    return fetch('/api/tokens/balance', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && 'balance' in d) paintTokens(d.balance); })
      .catch(function () {});
  }

  // The watcher below reports spends; bursts (Generate remaining fires one
  // POST per view) collapse into one balance read.
  var refreshTimer = 0;
  function tokensChanged() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refreshTokens, 400);
  }
  document.addEventListener('sn-tokens-changed', tokensChanged);
  addEventListener('message', function (e) {
    if (e.origin === location.origin && e.data && e.data.snTokens) tokensChanged();
  });
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
        document.dispatchEvent(new CustomEvent('sn-me', { detail: me }));
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

// The Free plan's upgrade lightbox. The server owns the clock: it says when
// the offer may first show, stamps the 30-minute window on the account when
// it does, and applies the discount at checkout only while that window runs.
// This file only shows it. Top window only, like the menu.
(function () {
  if (window.self !== window.top) return;
  var SEEN = 'snOfferSeen';
  var offer = null, box = null, chip = null, tickTimer = 0, endsAt = 0;

  function seen(mark) {
    try {
      if (mark) sessionStorage.setItem(SEEN, '1');
      return sessionStorage.getItem(SEEN) === '1';
    } catch (e) { return false; }
  }
  function money(n) {
    return '€' + (Math.round(n) === n ? n : Number(n).toFixed(2));
  }
  function clock(s) {
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function esc(t) {
    return String(t).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function left() { return Math.max(0, Math.round((endsAt - Date.now()) / 1000)); }
  function live() { return !!offer && offer.state === 'active' && left() > 0; }

  function css() {
    if (document.getElementById('upoffer-css')) return;
    var st = document.createElement('style');
    st.id = 'upoffer-css';
    st.textContent = [
      '.upoffer-back{position:fixed;inset:0;z-index:2147483000;background:rgba(0,0,0,.62);display:flex;align-items:center;justify-content:center;padding:16px;overflow-y:auto}',
      '.upoffer{position:relative;width:100%;max-width:880px;background:var(--panel,#111113);color:var(--text,#f0ece9);border:1px solid var(--border,#2e2e2e);border-radius:var(--r-lg,16px);padding:28px 24px 20px;box-shadow:0 30px 80px rgba(0,0,0,.5);font-family:var(--font,system-ui,sans-serif);margin:auto;box-sizing:border-box}',
      '.upoffer h2{margin:0 0 6px;padding-right:28px;font-family:var(--display,inherit);font-size:1.5rem;line-height:1.2}',
      '.upoffer p{margin:0 0 4px;color:var(--text-2,#cdc8c4)}',
      '.upoffer-x{position:absolute;top:10px;right:12px;background:none;border:0;color:var(--text-3,#ada7a3);font-size:1.6rem;line-height:1;cursor:pointer;padding:6px}',
      '.upoffer-timer{display:inline-block;margin:10px 0 16px;padding:6px 12px;border-radius:999px;background:var(--accent-soft,#ff5c3826);border:1px solid var(--accent-line,#ff5c3866);font-weight:600;font-variant-numeric:tabular-nums}',
      '.upoffer-gap{height:14px}',
      '.upoffer-plans{display:grid;gap:12px;grid-template-columns:1fr}',
      '@media(min-width:680px){.upoffer-plans{grid-template-columns:repeat(3,1fr)}}',
      '.upoffer-plan{border:1px solid var(--border,#2e2e2e);border-radius:var(--r,12px);padding:16px;display:flex;flex-direction:column;gap:6px;background:var(--panel-2,#151517)}',
      '.upoffer-plan.pro{border-color:var(--accent-line,#ff5c3866)}',
      '.upoffer-plan h3{margin:0;font-size:1.05rem}',
      '.upoffer-price{font-size:1.5rem;font-weight:700}',
      '.upoffer-price s{opacity:.5;font-size:1rem;font-weight:400;margin-right:6px}',
      '.upoffer-price small{font-size:.8rem;font-weight:400;color:var(--text-3,#ada7a3)}',
      '.upoffer-plan .blurb{font-size:.85rem;color:var(--text-2,#cdc8c4);flex:1}',
      '.upoffer-plan button{margin-top:6px;padding:10px 12px;border:0;border-radius:var(--r-sm,8px);background:var(--accent,#ff5c38);color:var(--accent-ink,#fff);font-weight:600;cursor:pointer}',
      '.upoffer-plan button:disabled{opacity:.6;cursor:default}',
      '.upoffer-foot{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:14px;font-size:.8rem;color:var(--text-3,#ada7a3)}',
      '.upoffer-foot a{color:inherit}',
      '.upoffer-err{color:var(--err,#ef4444);font-size:.85rem;margin-top:8px}',
      '.upoffer-err:empty{display:none}',
      '.upoffer-chip{position:fixed;right:16px;bottom:16px;z-index:2147482000;padding:9px 14px;border-radius:999px;border:1px solid var(--accent-line,#ff5c3866);background:var(--panel,#111113);color:var(--text,#f0ece9);font:600 .85rem var(--font,system-ui,sans-serif);cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.35);font-variant-numeric:tabular-nums}'
    ].join('\n');
    document.head.appendChild(st);
  }

  function checkout(btn, tier) {
    var err = box && box.querySelector('.upoffer-err');
    btn.disabled = true;
    var old = btn.textContent;
    btn.textContent = 'Redirecting…';
    fetch('/api/billing/checkout', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tier: tier, provider: 'stripe' })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.payment_url) { location.href = d.payment_url; return; }
      if (err) err.textContent = d.error || 'Could not start checkout.';
      btn.disabled = false; btn.textContent = old;
    }).catch(function () {
      // Card payments may be off here; the billing page offers what is on.
      location.href = '/billing';
    });
  }

  function render() {
    var pct = offer.applied_pct || 0;
    var discounted = pct > 0 && (live() || offer.referral_better);
    var head, sub;
    if (live() && offer.referral_better) {
      head = pct + '% off your first month';
      sub = 'Your referral discount beats the ' + offer.pct +
        '% welcome offer, so that is the one applied at checkout.';
    } else if (live()) {
      head = offer.pct + '% off your first month';
      sub = 'A welcome offer for going live: your persona on every platform, and a monthly token allowance.';
    } else {
      head = 'Going live needs a paid plan';
      sub = 'Free lets you build and test. A plan puts your persona in front of fans.';
    }
    var plans = (offer.plans || []).map(function (p) {
      var price = discounted && p.offer_price !== p.price
        ? '<s>' + money(p.price) + '</s>' + money(p.offer_price)
        : money(p.price);
      return '<div class="upoffer-plan' + (p.key === 'pro' ? ' pro' : '') + '">' +
        '<h3>' + esc(p.name) + '</h3>' +
        '<div class="upoffer-price">' + price + ' <small>/month</small></div>' +
        '<div class="blurb">' + esc(p.blurb) + '</div>' +
        '<button type="button" data-upoffer-tier="' + esc(p.key) + '">Choose ' + esc(p.name) + '</button></div>';
    }).join('');
    box.querySelector('.upoffer').innerHTML =
      '<button type="button" class="upoffer-x" aria-label="Close">×</button>' +
      '<h2 id="upoffer-title">' + head + '</h2><p>' + sub + '</p>' +
      (live() ? '<div class="upoffer-timer">Ends in <span data-upoffer-left>' +
        clock(left()) + '</span></div>' : '<div class="upoffer-gap"></div>') +
      '<div class="upoffer-plans">' + plans + '</div>' +
      '<div class="upoffer-err" role="alert"></div>' +
      '<div class="upoffer-foot"><span>' +
      (discounted ? 'The discount covers the first month of a monthly plan paid by card. ' : '') +
      'Prices exclude VAT.</span><a href="#" data-upoffer-close>Keep exploring on Free</a></div>';
    [].slice.call(box.querySelectorAll('[data-upoffer-tier]')).forEach(function (b) {
      b.addEventListener('click', function () { checkout(b, b.dataset.upofferTier); });
    });
    [].slice.call(box.querySelectorAll('.upoffer-x,[data-upoffer-close]')).forEach(function (b) {
      b.addEventListener('click', function (e) { e.preventDefault(); close(); });
    });
  }

  var lastFocus = null;
  function onKey(e) {
    if (!box) return;
    if (e.key === 'Escape') { close(); return; }
    if (e.key !== 'Tab') return;
    var f = [].slice.call(box.querySelectorAll('button,a[href]'))
      .filter(function (el) { return !el.disabled; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function open() {
    if (!offer || offer.state === 'ineligible' || box) return;
    css();
    lastFocus = document.activeElement;
    box = document.createElement('div');
    box.className = 'upoffer-back';
    box.innerHTML = '<div class="upoffer" role="dialog" aria-modal="true" aria-labelledby="upoffer-title"></div>';
    box.addEventListener('click', function (e) { if (e.target === box) close(); });
    document.body.appendChild(box);
    render();
    document.addEventListener('keydown', onKey);
    var btn = box.querySelector('.upoffer-plan.pro button') || box.querySelector('button');
    if (btn) btn.focus();
    seen(true);
  }

  function close() {
    if (!box) return;
    box.remove();
    box = null;
    document.removeEventListener('keydown', onKey);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function paintChip() {
    if (!live()) {
      if (chip) { chip.remove(); chip = null; }
      return;
    }
    css();
    if (!chip) {
      chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'upoffer-chip';
      chip.addEventListener('click', open);
      document.body.appendChild(chip);
    }
    chip.textContent = (offer.applied_pct || offer.pct) + '% off · ' + clock(left());
  }

  function tick() {
    clearTimeout(tickTimer);
    if (offer.state === 'active' && !left()) {
      // Window over: prices go back to full, here as they do at checkout.
      offer.state = 'expired';
      if (!offer.referral_better) offer.applied_pct = 0;
      paintChip();
      if (box) render();
      return;
    }
    paintChip();
    var el = box && box.querySelector('[data-upoffer-left]');
    if (el) el.textContent = clock(left());
    if (live()) tickTimer = setTimeout(tick, 1000);
  }

  function activate(state) {
    offer = state;
    endsAt = Date.now() + (state.seconds_left || 0) * 1000;
    tick();
  }

  var starting = false;
  function start() {
    if (starting) return;
    starting = true;
    fetch('/api/offer/start', { method: 'POST', credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        starting = false;
        if (!d || !d.state || d.state === 'ineligible') return;
        if (d.state === 'pending') {
          // The browser timer beat the server's clock: try again when it is due.
          setTimeout(start, Math.max(1, d.show_in_seconds || 1) * 1000);
          offer = d;
          return;
        }
        activate(d);
        if (box) render(); else open();
      })
      .catch(function () { starting = false; });
  }

  document.addEventListener('sn-me', function (e) {
    var o = e.detail && e.detail.offer;
    if (!o || o.state === 'ineligible') return;
    offer = o;
    if (o.state === 'pending') {
      setTimeout(start, (o.show_in_seconds || 0) * 1000);
    } else if (o.state === 'active') {
      activate(o);
      // A tab that has already seen it keeps only the chip on later pages.
      if (!seen(false)) open();
    } else {
      activate(o);
    }
  });

  // Reaching for a live feature. Before the timer this shows the plans at
  // full price; the discount still opens on its own clock.
  function upgrade() { open(); }
  window.openUpgradeOffer = upgrade;
  document.addEventListener('sn-upgrade', upgrade);
  addEventListener('message', function (e) {
    if (e.origin === location.origin && e.data && e.data.snUpgrade) upgrade();
  });
})();
