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
