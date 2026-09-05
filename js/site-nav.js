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

  var SIGNED_OUT = [
    { href: '/#pricing', label: 'Pricing' },
    { href: '/blog', label: 'Blog', keep: true },
    { href: '/login', label: 'Log in' },
    { href: '/register', label: 'Register', cta: true },
  ];
  var SIGNED_IN = [
    { href: '/#pricing', label: 'Pricing' },
    { href: '/blog', label: 'Blog', keep: true },
    { href: '/dashboard', label: 'Dashboard', cta: true },
    { href: '/logout', label: 'Log out' },
  ];

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
        '>' + i.label + '</a>';
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

  function paint(items) {
    hosts().forEach(function (host) {
      host.classList.add('sn-links');
      if (host.matches('[data-site-nav="inline"]')) host.classList.add('sn-inline');
      // Only ever replace our own links: a page's theme toggle and its own
      // entries (comingsoon.html's Pricing) share this container.
      [].slice.call(host.querySelectorAll('a[data-sn]')).forEach(function (el) {
        el.remove();
      });
      host.insertAdjacentHTML('beforeend', linksHtml(items));
    });
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
    paint(SIGNED_OUT);

    fetch('/api/me', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (me) { if (me && me.signed_in) paint(SIGNED_IN); })
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
