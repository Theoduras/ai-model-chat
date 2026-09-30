// Renders a link-in-bio page from its config. Used by /link-<handle> and by the
// editor's live preview, so what the creator sees is what fans get.
(function () {
  var ICONS = {
    instagram: '<rect x="3" y="3" width="18" height="18" rx="5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="17.5" cy="6.5" r="1.3" fill="currentColor"/>',
    x: '<path d="M4 4l16 16M20 4L4 20" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/>',
    tiktok: '<path d="M14 3v11.5a3.5 3.5 0 1 1-3-3.46M14 3c.5 2.6 2.3 4.3 5 4.6" stroke="currentColor" stroke-width="2.2" fill="none" stroke-linecap="round"/>',
    telegram: '<path d="M3 11.5L20.5 4l-3 16-5.5-4.5-3 3v-5l8-7-10 6z" fill="currentColor"/>',
    fanvue: '<path d="M7 4h11M7 4v16M7 11h9" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" fill="none"/>',
    onlyfans: '<circle cx="9" cy="13" r="6" fill="none" stroke="currentColor" stroke-width="2.4"/><path d="M13 8h8l-4 6" stroke="currentColor" stroke-width="2.2" fill="none" stroke-linejoin="round"/>',
    fansly: '<path d="M12 21s-8-5-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 10c0 6-8 11-8 11z" fill="none" stroke="currentColor" stroke-width="2.2"/>',
    reddit: '<circle cx="12" cy="14" r="7" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="9.5" cy="13.5" r="1.2" fill="currentColor"/><circle cx="14.5" cy="13.5" r="1.2" fill="currentColor"/><path d="M12 7l1.5-4 4 1" stroke="currentColor" stroke-width="1.8" fill="none"/>',
    discord: '<path d="M6 6.5c4-2 8-2 12 0l2 10c-2 1.8-4 2.5-5 2.5l-1-2c-1.4.3-2.6.3-4 0l-1 2c-1 0-3-.7-5-2.5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="9.5" cy="12.5" r="1.3" fill="currentColor"/><circle cx="14.5" cy="12.5" r="1.3" fill="currentColor"/>',
    youtube: '<rect x="2.5" y="5.5" width="19" height="13" rx="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M10 9v6l5-3z" fill="currentColor"/>',
    twitch: '<path d="M5 3h15v10l-4 4h-4l-3 3v-3H5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M11 7v4M15 7v4" stroke="currentColor" stroke-width="2"/>',
    snapchat: '<path d="M12 3c3 0 5 2 5 5v3l2 1-2 1c.5 2 2 3 3 3.5-1 .8-2.5.5-3 1.5s-2 1-5 1-4.5 0-5-1-2-.7-3-1.5c1-.5 2.5-1.5 3-3.5l-2-1 2-1V8c0-3 2-5 5-5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    threads: '<path d="M16.5 11c-.5-3-2.5-4.5-5-4.5-3 0-5 2.5-5 5.5s2 5.5 5.5 5.5c3 0 5-2 5-4.5 0-2-1.5-3-4-3-2 0-3 1-3 2.2s1 2 2.3 2c2 0 3.2-1.6 3.2-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    spotify: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7.5 9.5c3-1 6.5-.7 9 .8M8 12.5c2.5-.7 5-.5 7 .7M8.5 15.3c2-.5 3.8-.3 5.3.5" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round"/>',
    amazon: '<path d="M4 15c4 3 11 3.5 16 0M17 13.5l3 1.5-1 3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M9 11.5c0-2 2-3 5-3V7c0-1.5-2.5-2-4-.5" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>',
    email: '<rect x="3" y="5" width="18" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4 7l8 6 8-6" stroke="currentColor" stroke-width="2" fill="none"/>',
    website: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" stroke="currentColor" stroke-width="1.8" fill="none"/>'
  };
  var CSS = '' +
    '.bio{min-height:100%;box-sizing:border-box;display:flex;flex-direction:column;align-items:center;padding:48px 20px 28px;position:relative;background-size:cover;background-position:center}' +
    '.bio *{box-sizing:border-box}' +
    '.bio-in{width:100%;max-width:560px;display:flex;flex-direction:column;align-items:center;gap:12px;flex:1}' +
    '.bio-av{width:96px;height:96px;object-fit:cover;flex:none;background:rgba(0,0,0,.08)}' +
    '.bio-av.circle{border-radius:50%}.bio-av.rounded{border-radius:28px}.bio-av.cover{width:100%;height:220px;border-radius:var(--bio-r)}' +
    '.bio-name{margin:4px 0 0;font-size:30px;line-height:1.1;text-align:center;word-break:break-word}' +
    '.bio-text{margin:0;text-align:center;font-size:15px;max-width:40ch;opacity:.85;white-space:pre-line}' +
    '.bio-soc{display:flex;flex-wrap:wrap;gap:16px;justify-content:center;margin:2px 0 6px}' +
    '.bio-soc a{color:inherit;display:flex;opacity:.9}.bio-soc a:hover{opacity:1;transform:translateY(-1px)}.bio-soc svg{width:24px;height:24px}' +
    '.bio-btn{width:100%;min-height:56px;display:flex;align-items:center;justify-content:center;gap:10px;padding:8px 52px;position:relative;text-decoration:none;font-weight:600;font-size:15.5px;text-align:center;border-radius:var(--bio-r);cursor:pointer;transition:transform .15s;border:0;font-family:inherit;background:var(--bio-bb);color:var(--bio-bt)}' +
    '.bio-btn:hover{transform:scale(1.015)}.bio-btn:focus-visible{outline:3px solid var(--bio-ac);outline-offset:3px}' +
    '.bio-btn img{position:absolute;left:8px;top:50%;transform:translateY(-50%);width:40px;height:40px;object-fit:cover;border-radius:calc(var(--bio-r) * .7)}' +
    '.bio-btn .tag{position:absolute;right:16px;font-size:11px;font-weight:700;opacity:.7}' +
    '.st-glass .bio-btn{background:color-mix(in srgb,var(--bio-bb) 18%,transparent);border:1px solid color-mix(in srgb,var(--bio-bt) 22%,transparent);backdrop-filter:blur(8px);color:var(--bio-bt)}' +
    '.st-outline .bio-btn{background:transparent;border:2px solid var(--bio-bt)}' +
    '.st-shadow .bio-btn{border:2.5px solid var(--bio-tx);box-shadow:5px 5px 0 var(--bio-tx)}' +
    '.st-fill .bio-btn{box-shadow:0 6px 16px -10px rgba(0,0,0,.45)}' +
    '.bio-btn.spot{background:var(--bio-ac);color:#fff;border-color:transparent;animation:bioPulse 2.4s ease-in-out infinite}' +
    '@keyframes bioPulse{50%{transform:scale(1.025)}}' +
    '.bio-h{margin:10px 0 -2px;font-size:13px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;opacity:.8;text-align:center}' +
    '.bio-note{margin:0;text-align:center;font-size:14px;opacity:.85;white-space:pre-line;max-width:44ch}' +
    '.bio-gate{position:absolute;inset:0;z-index:10;display:flex;align-items:center;justify-content:center;padding:28px;background:rgba(10,6,9,.6);backdrop-filter:blur(18px)}' +
    '.bio-gate>div{max-width:320px;width:100%;text-align:center;color:#fff;display:flex;flex-direction:column;gap:12px;font-family:system-ui,sans-serif}' +
    '.bio-gate b{font-size:44px;line-height:1}.bio-gate p{margin:0;font-size:14px;opacity:.9}' +
    '.bio-gate .go{background:var(--bio-ac);color:#fff;border:0;border-radius:999px;padding:14px;font-weight:700;font-size:15px;cursor:pointer}' +
    '.bio-gate .no{background:none;border:0;color:#fff;opacity:.7;cursor:pointer;font-size:13px}' +
    '@media (prefers-reduced-motion:reduce){.bio-btn.spot{animation:none}.bio-btn:hover{transform:none}}';

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function loadFonts(t) {
    var fams = [t.font_title, t.font_body].filter(Boolean);
    var href = 'https://fonts.googleapis.com/css2?' + fams.map(function (f) {
      return 'family=' + f.replace(/ /g, '+') + ':wght@400;600;700';
    }).join('&') + '&display=swap';
    if (document.querySelector('link[data-bio-font="' + href + '"]')) return;
    var l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = href; l.setAttribute('data-bio-font', href);
    document.head.appendChild(l);
  }
  function adultOk() { try { return localStorage.getItem('bio_18') === '1'; } catch (e) { return false; } }
  function setAdult() { try { localStorage.setItem('bio_18', '1'); } catch (e) {} }

  function gate(root, onYes) {
    var g = el('div', 'bio-gate'), box = el('div');
    box.appendChild(el('b', '', '18+'));
    box.appendChild(el('p', '', 'This contains content for adults only. Confirm you are 18 or older to continue.'));
    var yes = el('button', 'go', "I'm 18 or older"), no = el('button', 'no', 'Take me back');
    yes.type = no.type = 'button';
    yes.onclick = function () { setAdult(); g.remove(); if (onYes) onYes(); };
    no.onclick = function () { g.remove(); if (!onYes) history.length > 1 ? history.back() : (location.href = 'about:blank'); };
    box.appendChild(yes); box.appendChild(no); g.appendChild(box); root.appendChild(g);
  }

  window.renderBio = function (mount, cfg, opts) {
    opts = opts || {};
    var t = cfg.theme || {};
    if (!document.getElementById('bio-css')) {
      var st = el('style'); st.id = 'bio-css'; st.textContent = CSS; document.head.appendChild(st);
    }
    loadFonts(t);
    mount.innerHTML = '';
    var root = el('div', 'bio st-' + (t.btn_style || 'fill'));
    var r = { square: '0px', round: '16px', pill: '999px' }[t.radius] || '16px';
    root.style.cssText = '--bio-r:' + r + ';--bio-bb:' + t.btn_bg + ';--bio-bt:' + t.btn_text +
      ';--bio-ac:' + t.accent + ';--bio-tx:' + t.text + ';color:' + t.text +
      ';font-family:"' + t.font_body + '",system-ui,sans-serif;';
    if (t.bg_type === 'image' && t.bg_image_url) {
      root.style.backgroundImage = 'linear-gradient(rgba(0,0,0,.25),rgba(0,0,0,.25)),url("' + t.bg_image_url + '")';
      root.style.backgroundColor = t.bg1;
    } else if (t.bg_type === 'gradient') {
      root.style.background = 'linear-gradient(180deg,' + t.bg1 + ',' + t.bg2 + ')';
    } else {
      root.style.background = t.bg1;
    }
    var inner = el('div', 'bio-in');
    if (cfg.avatar_url) {
      var av = el('img', 'bio-av ' + (t.avatar_shape || 'circle'));
      av.src = cfg.avatar_url; av.alt = '';
      av.onerror = function () { av.remove(); };
      inner.appendChild(av);
    }
    var nm = el('h1', 'bio-name', cfg.name || '');
    nm.style.fontFamily = '"' + t.font_title + '",' + '"' + t.font_body + '",sans-serif';
    inner.appendChild(nm);
    if (cfg.bio) inner.appendChild(el('p', 'bio-text', cfg.bio));
    var soc = el('div', 'bio-soc');
    (cfg.socials || []).forEach(function (s) {
      if (!ICONS[s.net] || !s.url) return;
      var a = el('a'); a.href = s.url; a.target = '_blank'; a.rel = 'noopener nofollow';
      a.setAttribute('aria-label', s.net);
      a.innerHTML = '<svg viewBox="0 0 24 24">' + ICONS[s.net] + '</svg>';
      soc.appendChild(a);
    });
    var socTop = t.socials_pos !== 'bottom';
    if (soc.children.length && socTop) inner.appendChild(soc);

    (cfg.blocks || []).forEach(function (b) {
      if (b.hidden && !opts.preview) return;
      if (b.type === 'header') { inner.appendChild(el('p', 'bio-h', b.title)); return; }
      if (b.type === 'text') { inner.appendChild(el('p', 'bio-note', b.text)); return; }
      var a = el('a', 'bio-btn' + (b.spotlight ? ' spot' : ''));
      if (b.hidden) a.style.opacity = '.4';
      a.href = b.href || '#';
      if (b.type === 'link') { a.target = '_blank'; a.rel = 'noopener nofollow'; }
      if (b.thumb_url) { var im = el('img'); im.src = b.thumb_url; im.alt = ''; a.appendChild(im); }
      a.appendChild(el('span', '', b.title || (b.type === 'chat' ? 'Chat with me' : 'Link')));
      if (b.adult) a.appendChild(el('span', 'tag', '18+'));
      if (opts.preview) a.onclick = function (e) { e.preventDefault(); };
      else if (b.adult && cfg.gate === 'button') {
        a.onclick = function (e) {
          if (adultOk()) return;
          e.preventDefault();
          gate(root, function () { a.target === '_blank' ? window.open(a.href, '_blank', 'noopener') : (location.href = a.href); });
        };
      }
      inner.appendChild(a);
    });
    if (soc.children.length && !socTop) inner.appendChild(soc);
    root.appendChild(inner);
    mount.appendChild(root);
    if (!opts.preview && cfg.gate === 'page' && !adultOk()) gate(root);
  };
})();
