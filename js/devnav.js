// Dev-only navigation bar. Shows links to Dashboard / Chat / Landing on every
// page, but ONLY in development (dev Cloud Run service, localhost, or ?dev=1).
// Never appears on the live site or inside the embedded landing-page chat.
(function () {
  var h = location.hostname;
  var forced = new URLSearchParams(location.search).get('dev');
  var isDev =
    forced === '1' ||
    h === 'localhost' || h === '127.0.0.1' ||
    h.indexOf('-dev-') !== -1 || /(^|[.-])dev([.-]|$)/.test(h);
  if (forced === '0') isDev = false;
  if (!isDev) return;
  if (window.self !== window.top) return; // skip inside iframes (landing chat)

  var path = location.pathname;
  function btn(href, label) {
    var a = document.createElement('a');
    a.href = href;
    a.textContent = label;
    var active = path === href || (href !== '/' && path.indexOf(href) === 0);
    a.className = 'devnav-btn' + (active ? ' active' : '');
    return a;
  }

  function mount() {
    if (document.getElementById('devnav')) return;
    var bar = document.createElement('div');
    bar.className = 'devnav';
    bar.id = 'devnav';
    var tag = document.createElement('span');
    tag.className = 'devnav-tag';
    tag.textContent = 'DEV';
    bar.appendChild(tag);
    bar.appendChild(btn('/dashboard', 'Dashboard'));
    bar.appendChild(btn('/chat', 'Chat'));
    bar.appendChild(btn('/landing', 'Landing'));
    bar.appendChild(btn('/xbot', '𝕏 Bot'));
    document.body.appendChild(bar);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
