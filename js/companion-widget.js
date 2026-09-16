// Embeddable AI-companion chat widget.
//
// Drop this on any page the creator controls (their own site, a landing
// page builder, a blog) to add a floating chat bubble. It opens the same
// persona chat this app already serves at /chat, inside an iframe, so the
// persona and funnel logic live in exactly one place — this file only
// builds the bubble/drawer chrome around it, and labels it as AI so
// visitors know who they're talking to before they open it.
//
// Configure through the script tag's own attributes:
//   <script src="https://<app-host>/js/companion-widget.js"
//           data-persona="lilith"
//           data-theme="dark"
//           data-position="right"
//           data-label="Chat with my AI companion"
//           defer></script>
(function () {
  if (window.__aiCompanionWidget) return;
  window.__aiCompanionWidget = true;

  var script = document.currentScript;
  if (!script) return;

  var HOST = (function () {
    try { return new URL(script.src, location.href).origin; }
    catch (e) { return location.origin; }
  })();

  var persona = script.getAttribute('data-persona') || '';
  var theme = script.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  var side = script.getAttribute('data-position') === 'left' ? 'left' : 'right';
  var label = script.getAttribute('data-label') || 'Chat with my AI companion';

  var COLORS = theme === 'light'
    ? { bg: '#faf9f8', accent: '#c53c20', text: '#1a1614', err: '#c81e1e' }
    : { bg: '#0e0e0e', accent: '#ff5c38', text: '#f0ece9', err: '#ef4444' };

  function chatUrl() {
    var u = HOST + '/chat?embed=1&only=1&theme=' + theme;
    if (persona) u += '&persona=' + encodeURIComponent(persona);
    return u;
  }

  function build() {
    var opp = side === 'left' ? 'right' : 'left';
    var css = ''
      + '.aicw-fab{position:fixed;bottom:24px;' + side + ':24px;width:58px;height:58px;'
      + 'border-radius:50%;background:' + COLORS.accent + ';color:#fff;border:none;'
      + 'cursor:pointer;font-size:1.3rem;display:flex;align-items:center;justify-content:center;'
      + 'box-shadow:0 8px 24px rgba(0,0,0,.35);z-index:2147483000;transition:transform .2s;padding:0;}'
      + '.aicw-fab:hover{transform:scale(1.07);}'
      + '.aicw-fab.aicw-hidden{display:none;}'
      + '.aicw-badge{position:absolute;top:-4px;' + opp + ':-4px;min-width:18px;height:18px;padding:0 4px;'
      + 'border-radius:9999px;background:' + COLORS.err + ';color:#fff;font-size:.62rem;font-weight:700;'
      + 'display:flex;align-items:center;justify-content:center;border:2px solid ' + COLORS.bg + ';'
      + 'opacity:0;transform:scale(.5);transition:opacity .2s,transform .2s;line-height:1;}'
      + '.aicw-badge.aicw-visible{opacity:1;transform:scale(1);}'
      + '.aicw-tag{position:absolute;bottom:-3px;' + opp + ':-3px;background:' + COLORS.bg + ';color:' + COLORS.text + ';'
      + 'font-size:.5rem;font-weight:700;letter-spacing:.04em;padding:1px 4px;border-radius:5px;'
      + 'border:1px solid rgba(255,255,255,.2);pointer-events:none;line-height:1.2;}'
      + '.aicw-overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:2147482998;'
      + 'opacity:0;pointer-events:none;transition:opacity .25s ease;}'
      + '.aicw-overlay.aicw-open{opacity:1;pointer-events:all;}'
      + '.aicw-drawer{position:fixed;top:0;' + side + ':0;bottom:0;width:min(420px,100vw);'
      + 'background:' + COLORS.bg + ';z-index:2147482999;display:flex;flex-direction:column;'
      + 'transform:translateX(' + (side === 'left' ? '-100%' : '100%') + ');'
      + 'transition:transform .35s cubic-bezier(.4,0,.2,1);box-shadow:0 20px 60px rgba(0,0,0,.5);}'
      + '.aicw-drawer.aicw-open{transform:translateX(0);}'
      + '.aicw-drawer iframe{flex:1;border:0;width:100%;height:100%;display:block;}'
      + '.aicw-close{position:absolute;top:12px;' + opp + ':-48px;'
      + 'width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.12);'
      + 'border:1px solid rgba(255,255,255,.2);color:#fff;font-size:1.1rem;line-height:1;cursor:pointer;'
      + 'display:flex;align-items:center;justify-content:center;backdrop-filter:blur(8px);}'
      + '.aicw-label{position:fixed;bottom:30px;' + side + ':92px;background:' + COLORS.bg + ';'
      + 'color:' + COLORS.text + ';font-size:.78rem;padding:7px 12px;border-radius:9999px;'
      + 'box-shadow:0 4px 14px rgba(0,0,0,.3);white-space:nowrap;z-index:2147483000;'
      + 'opacity:0;transform:translateY(4px);transition:opacity .2s,transform .2s;pointer-events:none;}'
      + '.aicw-label.aicw-visible{opacity:1;transform:translateY(0);}'
      + '@media (max-width:520px){.aicw-drawer{width:100vw;}'
      + '.aicw-close{' + opp + ':auto;' + side + ':12px;top:12px;}}';

    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    var overlay = document.createElement('div');
    overlay.className = 'aicw-overlay';

    var drawer = document.createElement('div');
    drawer.className = 'aicw-drawer';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'aicw-close';
    closeBtn.setAttribute('aria-label', 'Close chat');
    closeBtn.innerHTML = '&times;';
    drawer.appendChild(closeBtn);

    var fab = document.createElement('button');
    fab.className = 'aicw-fab';
    fab.setAttribute('aria-label', label);
    fab.title = label;
    fab.innerHTML =
      '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
      + '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8A2.5 2.5 0 0 1 17.5 16H10l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5v-8Z" stroke-linejoin="round"/></svg>'
      + '<span class="aicw-badge"></span>'
      + '<span class="aicw-tag">AI</span>';

    var tip = document.createElement('div');
    tip.className = 'aicw-label';
    tip.textContent = label;

    document.body.appendChild(overlay);
    document.body.appendChild(drawer);
    document.body.appendChild(fab);
    document.body.appendChild(tip);

    var open = false, unread = 0, loaded = false;
    var badge = fab.querySelector('.aicw-badge');

    function clearBadge() {
      unread = 0;
      badge.textContent = '';
      badge.classList.remove('aicw-visible');
    }

    function showBadge() {
      if (open) return;
      unread++;
      badge.textContent = unread > 9 ? '9+' : String(unread);
      badge.classList.add('aicw-visible');
    }

    function openWidget() {
      if (!loaded) {
        var frame = document.createElement('iframe');
        frame.title = label;
        frame.setAttribute('allow', 'autoplay');
        frame.src = chatUrl();
        drawer.appendChild(frame);
        loaded = true;
      }
      open = true;
      overlay.classList.add('aicw-open');
      drawer.classList.add('aicw-open');
      fab.classList.add('aicw-hidden');
      tip.classList.remove('aicw-visible');
      clearBadge();
    }

    function closeWidget() {
      open = false;
      overlay.classList.remove('aicw-open');
      drawer.classList.remove('aicw-open');
      fab.classList.remove('aicw-hidden');
    }

    fab.addEventListener('click', openWidget);
    overlay.addEventListener('click', closeWidget);
    closeBtn.addEventListener('click', closeWidget);
    fab.addEventListener('mouseenter', function () { if (!open) tip.classList.add('aicw-visible'); });
    fab.addEventListener('mouseleave', function () { tip.classList.remove('aicw-visible'); });

    // Scoped to our own host so another frame on the same host page can't
    // spoof an unread badge.
    window.addEventListener('message', function (e) {
      if (e.origin !== HOST || !e.data || e.data.type !== 'new_message') return;
      showBadge();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
