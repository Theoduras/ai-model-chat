// Applies the saved theme before first paint, so a light-mode user never sees
// a flash of the dark palette. Must be loaded in <head> without defer.
(function () {
  var KEY = 'ui-theme';
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}

  var theme = saved || 'light';
  document.documentElement.setAttribute('data-theme', theme);

  window.getTheme = function () {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  };

  window.setTheme = function (next) {
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
    document.querySelectorAll('iframe').forEach(function (f) {
      try { f.contentDocument.documentElement.setAttribute('data-theme', next); } catch (e) {}
    });
    document.querySelectorAll('.theme-toggle').forEach(function (btn) {
      btn.setAttribute('aria-label', next === 'light' ? 'Switch to dark mode'
                                                      : 'Switch to light mode');
      btn.title = next === 'light' ? 'Dark mode' : 'Light mode';
    });
  };

  window.toggleTheme = function () {
    window.setTheme(window.getTheme() === 'light' ? 'dark' : 'light');
  };

  document.addEventListener('DOMContentLoaded', function () { window.setTheme(window.getTheme()); });
})();

// Covers the page until its first data has landed, so nothing pops in after it
// is shown: gone once the page has loaded and no fetch has been in flight for a
// moment, and after 4s whatever happens, because some pages poll forever.
(function () {
  var root = document.documentElement;
  var css = document.createElement('style');
  css.textContent = '#page-loader{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;'
    + 'background:var(--bg,#0e0e0e);transition:opacity .2s ease}'
    + '#page-loader.out{opacity:0;pointer-events:none}'
    + '#page-loader i{width:34px;height:34px;border-radius:50%;border:3px solid rgba(255,92,56,.18);border-top-color:#ff5c38;animation:pl-spin .8s linear infinite}'
    + '@keyframes pl-spin{to{transform:rotate(360deg)}}'
    + '@media (prefers-reduced-motion: reduce){#page-loader i{animation:none;border-color:#ff5c38}}';
  document.head.appendChild(css);
  var cover = document.createElement('div');
  cover.id = 'page-loader';
  cover.setAttribute('aria-hidden', 'true');
  cover.innerHTML = '<i></i>';
  root.appendChild(cover);

  var inFlight = 0, loaded = false, done = false, timer = null;
  function finish() {
    if (done) return;
    done = true;
    cover.classList.add('out');
    setTimeout(function () { cover.remove(); }, 250);
  }
  function settle() {
    clearTimeout(timer);
    if (loaded && !inFlight) timer = setTimeout(finish, 250);
  }
  var realFetch = window.fetch;
  if (realFetch) {
    window.fetch = function () {
      var p = realFetch.apply(this, arguments);
      if (done) return p;
      inFlight++;
      clearTimeout(timer);
      var end = function () { inFlight--; settle(); };
      p.then(end, end);
      return p;
    };
  }
  window.addEventListener('load', function () { loaded = true; settle(); });
  setTimeout(finish, 4000);
})();
