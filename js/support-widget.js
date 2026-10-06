// Support bubble, bottom right. An AI assistant answers until the visitor asks
// for a person or an admin writes in; an admin can also open the thread first,
// so it polls even while closed and shows a badge when the team has written.
// Loaded by js/site-nav.js; the server decides who is who from the session or
// the visitor cookie, so this file holds no identity of its own.
(function () {
  if (window.__supportWidget || window.self !== window.top) return;
  if (/^\/(chat|t\/|admin\/support)/.test(location.pathname)
      || /[?&]embed=/.test(location.search)) return;
  window.__supportWidget = true;

  var OPEN_POLL = 4000, CLOSED_POLL = 30000;
  var state = { open: false, messages: [], mode: 'ai', unread: 0, isAdmin: false,
                sending: false, sig: '' };
  var timer = null, root, panel, list, input, badge, modeLine, humanBtn;
  var SEEN_KEY = 'support-seen-admin';

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  var CSS = ''
    + '.spw{--spw-bg:#ffffff;--spw-surface:#f3f1ef;--spw-border:#d9d5d0;--spw-text:#1a1614;'
    + '--spw-muted:#6e6762;--spw-accent:#c53c20;--spw-grad:linear-gradient(135deg,#ff5c38,#ff2d78 55%,#7c3aed);'
    + 'font-family:Inter,system-ui,sans-serif;}'
    + ':root[data-theme="dark"] .spw{--spw-bg:#111113;--spw-surface:#1c1c1c;--spw-border:#2e2e2e;'
    + '--spw-text:#f0ece9;--spw-muted:#97918d;--spw-accent:#ff5c38;}'
    + '.spw-fab{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;border:none;'
    + 'background:var(--spw-grad);color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;'
    + 'box-shadow:0 8px 24px rgba(0,0,0,.28);z-index:2147482000;transition:transform .2s;padding:0;}'
    + '.spw-fab:hover{transform:scale(1.06);}'
    + '.spw-fab svg{width:26px;height:26px;}'
    + '.spw-badge{position:absolute;top:-3px;right:-3px;min-width:20px;height:20px;padding:0 5px;border-radius:999px;'
    + 'background:#e11d48;color:#fff;font-size:.7rem;font-weight:700;display:none;align-items:center;justify-content:center;'
    + 'border:2px solid var(--spw-bg);}'
    + '.spw-badge.on{display:flex;}'
    + '.spw-watch{position:fixed;right:20px;bottom:84px;max-width:240px;padding:8px 12px;border-radius:12px;'
    + 'background:var(--spw-grad);color:#fff;font:600 .76rem Inter,system-ui,sans-serif;line-height:1.35;'
    + 'box-shadow:0 8px 24px rgba(0,0,0,.28);z-index:2147481999;display:none;align-items:center;gap:7px;}'
    + '.spw-watch.on{display:flex;}'
    + '.spw-watch .d{width:9px;height:9px;border-radius:50%;background:#fff;flex:none;animation:spwp 1.4s infinite;}'
    + '@keyframes spwp{0%,100%{opacity:1;}50%{opacity:.3;}}'
    + '.spw-panel{position:fixed;right:20px;bottom:88px;width:370px;height:540px;max-height:calc(100vh - 110px);'
    + 'background:var(--spw-bg);color:var(--spw-text);border:1px solid var(--spw-border);border-radius:18px;'
    + 'box-shadow:0 18px 48px rgba(0,0,0,.3);z-index:2147482001;display:none;flex-direction:column;overflow:hidden;}'
    + '.spw-panel.on{display:flex;}'
    + '.spw-head{padding:14px 16px;background:var(--spw-grad);color:#fff;display:flex;align-items:center;gap:10px;}'
    + '.spw-head .t{font-weight:700;font-size:.95rem;}'
    + '.spw-head .s{font-size:.75rem;opacity:.9;margin-top:1px;}'
    + '.spw-head .grow{flex:1;min-width:0;}'
    + '.spw-x{background:rgba(255,255,255,.18);border:none;color:#fff;width:30px;height:30px;border-radius:50%;'
    + 'cursor:pointer;font-size:1rem;line-height:1;padding:0;}'
    + '.spw-list{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px;}'
    + '.spw-m{max-width:84%;padding:9px 12px;border-radius:14px;font-size:.87rem;line-height:1.45;'
    + 'white-space:pre-wrap;word-wrap:break-word;}'
    + '.spw-m.user{align-self:flex-end;background:var(--spw-accent);color:#fff;border-bottom-right-radius:4px;}'
    + '.spw-m.ai,.spw-m.admin{align-self:flex-start;background:var(--spw-surface);border-bottom-left-radius:4px;}'
    + '.spw-m.admin{border:1px solid var(--spw-accent);}'
    + '.spw-by{display:block;font-size:.66rem;color:var(--spw-muted);margin-bottom:2px;font-weight:600;}'
    + '.spw-typing{align-self:flex-start;color:var(--spw-muted);font-size:.8rem;padding:4px 2px;}'
    + '.spw-bar{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 14px;'
    + 'border-top:1px solid var(--spw-border);font-size:.74rem;color:var(--spw-muted);}'
    + '.spw-bar button,.spw-bar a{background:none;border:none;color:var(--spw-accent);cursor:pointer;'
    + 'font:inherit;font-weight:600;padding:0;text-decoration:none;}'
    + '.spw-form{display:flex;gap:8px;padding:10px 12px 12px;}'
    + '.spw-form textarea{flex:1;resize:none;height:42px;max-height:120px;border:1px solid var(--spw-border);'
    + 'background:var(--spw-surface);color:var(--spw-text);border-radius:12px;padding:10px 12px;font:inherit;'
    + 'font-size:.88rem;outline:none;}'
    + '.spw-form textarea:focus{border-color:var(--spw-accent);}'
    + '.spw-send{width:42px;height:42px;border-radius:12px;border:none;background:var(--spw-grad);color:#fff;'
    + 'cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center;padding:0;}'
    + '.spw-send:disabled{opacity:.5;cursor:default;}'
    + '.spw-send svg{width:18px;height:18px;}'
    + '@media(max-width:520px){.spw-panel{right:0;left:0;bottom:0;top:0;width:auto;height:auto;max-height:none;'
    + 'border-radius:0;border:none;}.spw-fab{display:none;}.spw-open .spw-fab{display:none;}}';

  var CHAT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5'
    + 'a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  var SEND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2 11 13"/>'
    + '<path d="M22 2 15 22l-4-9-9-4z"/></svg>';

  function build() {
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    root = document.createElement('div');
    root.className = 'spw';
    root.innerHTML = ''
      + '<div class="spw-panel" role="dialog" aria-label="Support chat">'
      + '  <div class="spw-head"><div class="grow"><div class="t">Support</div>'
      + '    <div class="s" data-mode></div></div>'
      + '    <button type="button" class="spw-x" aria-label="Close">&times;</button></div>'
      + '  <div class="spw-list" aria-live="polite"></div>'
      + '  <div class="spw-bar"><span data-hint></span><span>'
      + '    <button type="button" data-human>Talk to a human</button></span></div>'
      + '  <form class="spw-form"><textarea rows="1" placeholder="Ask anything…" maxlength="2000"></textarea>'
      + '    <button type="submit" class="spw-send" aria-label="Send">' + SEND_ICON + '</button></form>'
      + '</div>'
      + '<button type="button" class="spw-fab" aria-label="Open support chat">' + CHAT_ICON
      + '<span class="spw-badge"></span></button>';
    document.body.appendChild(root);

    panel = root.querySelector('.spw-panel');
    list = root.querySelector('.spw-list');
    input = root.querySelector('textarea');
    badge = root.querySelector('.spw-badge');
    modeLine = root.querySelector('[data-mode]');
    humanBtn = root.querySelector('[data-human]');

    root.querySelector('.spw-fab').onclick = function () { setOpen(!state.open); };
    root.querySelector('.spw-x').onclick = function () { setOpen(false); };
    humanBtn.onclick = askHuman;
    root.querySelector('form').onsubmit = function (e) { e.preventDefault(); send(); };
    input.onkeydown = function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    };
    input.oninput = function () {
      input.style.height = '42px';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    };
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll();
    });
  }

  function setOpen(open) {
    state.open = open;
    panel.classList.toggle('on', open);
    root.classList.toggle('spw-open', open);
    if (open) {
      render(true);
      markRead();
      setTimeout(function () { input.focus(); }, 50);
    }
    schedule();
  }

  function markRead() {
    if (!state.unread) return;
    state.unread = 0;
    paintBadge();
    fetch('/api/support/read', { method: 'POST', credentials: 'same-origin' });
  }

  function paintBadge() {
    badge.textContent = state.unread > 9 ? '9+' : String(state.unread || '');
    badge.classList.toggle('on', !state.open && state.unread > 0);
  }

  function render(force) {
    var last = state.messages[state.messages.length - 1];
    var sig = state.messages.length + '|' + (last ? last.id : '') + '|' + state.mode + '|' + state.sending;
    if (!force && sig === state.sig) return;
    state.sig = sig;

    modeLine.textContent = state.mode === 'human'
      ? 'You’re chatting with the team'
      : 'AI assistant · a person can join any time';
    humanBtn.style.display = state.mode === 'human' ? 'none' : '';
    var hint = root.querySelector('[data-hint]');
    hint.innerHTML = state.isAdmin
      ? '<a href="/admin/support">Open support inbox</a>'
      : esc(state.mode === 'human' ? 'We’ll reply here' : 'Replies in seconds');

    var h = '';
    if (!state.messages.length) {
      h += '<div class="spw-m ai"><span class="spw-by">AI assistant</span>'
        + 'Hi! I can help with the persona builder, platforms, tokens, plans and billing. '
        + 'What can I help you with?</div>';
    }
    state.messages.forEach(function (m) {
      var by = m.role === 'admin' ? 'Team' : m.role === 'ai' ? 'AI assistant' : '';
      h += '<div class="spw-m ' + m.role + '">'
        + (by ? '<span class="spw-by">' + by + '</span>' : '') + esc(m.content) + '</div>';
    });
    if (state.sending && state.mode === 'ai') h += '<div class="spw-typing">Typing…</div>';
    list.innerHTML = h;
    list.scrollTop = list.scrollHeight;
    paintBadge();
  }

  var mirroring = false, lastShot = '', watchBanner = null;

  function setWatchBanner(on) {
    if (!watchBanner) {
      watchBanner = document.createElement('div');
      watchBanner.className = 'spw-watch';
      watchBanner.innerHTML = '<span class="d"></span><span>A support agent is viewing this page to help you.</span>';
      (root || document.body).appendChild(watchBanner);
    }
    watchBanner.classList.toggle('on', !!on);
  }

  function shot() {
    var doc = document.documentElement.cloneNode(true);
    var live = document.querySelectorAll('input,textarea,select');
    var copy = doc.querySelectorAll('input,textarea,select');
    for (var i = 0; i < live.length && i < copy.length; i++) {
      var el = live[i], c = copy[i], v = el.type === 'password' ? '••••' : el.value;
      if (el.tagName === 'TEXTAREA') c.textContent = v;
      else if (el.tagName === 'SELECT') { var o = c.options[el.selectedIndex]; if (o) o.setAttribute('selected', ''); }
      else if (el.type === 'checkbox' || el.type === 'radio') { if (el.checked) c.setAttribute('checked', ''); }
      else if (el.type !== 'file') c.setAttribute('value', v);
    }
    doc.querySelectorAll('script,noscript,iframe').forEach(function (n) { n.remove(); });
    var head = doc.querySelector('head');
    if (head) { var b = document.createElement('base'); b.href = location.origin + '/'; head.insertBefore(b, head.firstChild); }
    return '<!DOCTYPE html>' + doc.outerHTML;
  }

  function mirror() {
    if (document.hidden) { setTimeout(mirror, 2000); return; }
    var html = shot(), body = { url: page(), w: innerWidth, h: innerHeight, x: scrollX, y: scrollY };
    if (html !== lastShot && html.length < 1500000) body.html = html;
    post('/api/support/screen', body).then(function (d) {
      if (body.html) lastShot = html;
      if (d && d.watch) { setWatchBanner(true); setTimeout(mirror, 2000); }
      else { mirroring = false; lastShot = ''; setWatchBanner(false); }
    }, function () { mirroring = false; setWatchBanner(false); });
  }

  function apply(d) {
    if (d && d.watch && !mirroring) { mirroring = true; mirror(); }
    if (!d || !d.ok) return;
    var before = state.messages.length;
    state.messages = d.messages || [];
    state.mode = d.mode || 'ai';
    state.isAdmin = !!d.is_admin;
    state.unread = d.unread || 0;
    // A team message nobody has seen yet opens the chat once, so an admin who
    // reaches out first is not left waiting on a badge nobody notices.
    var lastAdmin = null;
    state.messages.forEach(function (m) { if (m.role === 'admin') lastAdmin = m; });
    if (!state.open && lastAdmin && state.unread > 0 && state.messages.length > before) {
      var seen = null;
      try { seen = localStorage.getItem(SEEN_KEY); } catch (e) {}
      if (seen !== lastAdmin.id) {
        try { localStorage.setItem(SEEN_KEY, lastAdmin.id); } catch (e) {}
        if (before > 0 || !document.hidden) { setOpen(true); return; }
      }
    }
    if (state.open) markRead();
    render(false);
  }

  function page() { return location.pathname + location.search; }

  function poll() {
    fetch('/api/support?page=' + encodeURIComponent(page()), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(apply, function () {})
      .then(schedule);
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      if (document.hidden) { schedule(); return; }
      poll();
    }, state.open ? OPEN_POLL : CLOSED_POLL);
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(function (r) { return r.json(); });
  }

  function send() {
    var text = (input.value || '').trim();
    if (!text || state.sending) return;
    input.value = '';
    input.style.height = '42px';
    state.messages.push({ id: 'local' + Date.now(), role: 'user', content: text });
    state.sending = true;
    render(true);
    post('/api/support', { message: text, page: page() })
      .then(function (d) {
        state.sending = false;
        if (d && d.ok) { apply(d); render(true); return; }
        throw new Error((d && d.error) || 'failed');
      })
      .catch(function () {
        state.sending = false;
        state.messages.push({ id: 'err' + Date.now(), role: 'ai',
                              content: 'That didn’t send. Check your connection and try again.' });
        render(true);
      });
  }

  function askHuman() {
    post('/api/support/human', {}).then(function (d) { apply(d); render(true); },
                                        function () {});
  }

  function start() { build(); poll(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
