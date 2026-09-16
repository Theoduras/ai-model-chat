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

  var PAGE_LINKS = [
    { href: '/#pricing', label: 'Pricing' },
    { href: '/blog', label: 'Blog', keep: true },
  ];
  var ACCOUNT_OUT = [
    { href: '/login', label: 'Log in', icon: 'login' },
    { href: '/register', label: 'Register', icon: 'register', cta: true },
  ];
  var ACCOUNT_IN = [
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

  function linksHtml(items) {
    return items.map(function (i) {
      return '<a data-sn href="' + i.href + '"' +
        (i.cta ? ' class="sn-cta"' : '') +
        (i.keep ? ' data-keep' : '') +
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
    [].slice.call(host.querySelectorAll('a[data-sn]')).forEach(function (el) { el.remove(); });
    host.insertAdjacentHTML('beforeend', linksHtml(items));
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
      .then(function (me) { if (me && me.signed_in) paint(ACCOUNT_IN); })
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
