// The persona wizard's frame for pages that are not the persona builder.
//
// onboarding.js is welded to the builder's form, and console-guide.js draws a
// different shell (tabs, no rail), so the Character Builder and the Studio get
// this: the same .ob-* markup and .obt-* coach-marks, which style.css already
// carries, with the page supplying the words. Progress rides the persona
// wizard's own store (/api/me/setup) under namespaced keys such as
// "page:studio", so it follows the creator between browsers.
(function () {

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function byId(id) { return document.getElementById(id); }

  function embedded() {
    try { return window.self !== window.top; } catch (e) { return true; }
  }

  // ── Who is looking, and how far they got ─────────────────────────────────
  var mePromise = null;
  var setup = {};
  function me() {
    if (!mePromise) {
      mePromise = fetch('/api/me').then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; })
        .then(function (d) {
          setup = d.setup || {};
          return { isAdmin: !!d.is_admin, setup: setup };
        });
    }
    return mePromise;
  }
  function entry(key) {
    if (!setup[key]) setup[key] = {};
    return setup[key];
  }
  function save(key, patch) {
    var e = entry(key);
    if ('done' in patch) e.done = !!patch.done;
    if (patch.tour) e.tour = patch.tour;
    if (patch.seen) e.seen = patch.seen;
    return fetch('/api/me/setup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ slug: key }, patch))
    }).catch(function () {});
  }
  function seen(key) { return entry(key).seen || []; }
  function markSeen(key, stepKey) {
    if (seen(key).indexOf(stepKey) !== -1) return Promise.resolve();
    return save(key, { seen: seen(key).concat([stepKey]) });
  }

  // ── Markup, identical to onboarding.js so the frames read as one product ──

  // Only the stage being worked on reads as active, as in the persona rail.
  function railHtml(o) {
    var cur = o.steps[o.idx];
    var html = '<div class="ob-rail-h">' + esc(o.title) + '</div>' +
      (o.name ? '<div class="pw-rail-name">' + esc(o.name) + '</div>' : '') +
      '<div class="ob-rail-p">' + esc(o.pctLabel || (o.pct + '% done')) + '</div>' +
      '<div class="ob-mini"><i style="width:' + o.pct + '%"></i></div>';
    html += '<div class="pw-stages">' + o.stages.map(function (st, n) {
      var mine = [];
      o.steps.forEach(function (s, i) { if (s.stage === st.key) mine.push(i); });
      var all = mine.length && mine.every(function (i) { return o.done(i); });
      var open = !!cur && cur.stage === st.key;
      var stt = all ? 'done' : (open ? 'now' : 'todo');
      var steps = open || o.expandAll ? '<div class="ob-rsteps">' + mine.map(function (i) {
        var s = o.steps[i], go = o.reachable(i) && i !== o.idx;
        var cls = i === o.idx ? 'on' : (o.done(i) ? 'done' : '');
        return '<button type="button" class="ob-rstep ' + cls + (go ? '' : ' pw-off') + '"' +
          (i === o.idx ? ' aria-current="step"' : '') + (go ? ' onclick="' + o.call + '(' + i + ')"' : ' tabindex="-1"') + '>' +
          (o.done(i) && i !== o.idx ? '<span class="ob-rcheck">✓</span>' : '<span class="ob-rdot"></span>') +
          esc(s.nav) + '</button>';
      }).join('') + '</div>' : '';
      return '<div class="ob-rstage">' +
        '<div class="ob-rstage-t' + (open ? ' on' : '') + '">' +
          '<span class="ob-rnum ' + stt + '">' + (all ? '✓' : (n + 1)) + '</span>' + esc(st.label) + '</div>' +
        steps + '</div>';
    }).join('') + '</div>';
    return html;
  }

  function crumb(stages, steps, idx) {
    var step = steps[idx];
    var n = 0, inStage = steps.filter(function (s) { return s.stage === step.stage; });
    stages.forEach(function (s, i) { if (s.key === step.stage) n = i + 1; });
    return 'Stage ' + n + ' · Step ' + (inStage.indexOf(step) + 1) + ' of ' + inStage.length;
  }

  function headHtml(o) {
    return '<div class="ob-crumb">' + esc(o.crumb) + '</div>' +
      '<h2 class="ob-step-h">' + esc(o.title) + '</h2>' +
      (o.sub ? '<p class="ob-step-s">' + esc(o.sub) + '</p>' : '');
  }

  // `next.why` says what is missing while Continue is held back, so a greyed
  // button never leaves the creator guessing.
  function footHtml(o) {
    var n = o.next;
    return (o.back ? '<button type="button" class="btn btn-ghost" onclick="' + o.back + '">← Back</button>' : '') +
      (n ? '<button type="button" class="btn btn-primary" onclick="' + n.call + '"' +
        (n.disabled ? ' disabled' : '') + '>' + esc(n.label) + '</button>' : '') +
      (n && n.disabled && n.why ? '<span class="pw-why">' + esc(n.why) + '</span>' : '') +
      '<span class="ob-saved"' + (o.statusId ? ' id="' + o.statusId + '" aria-live="polite"' : '') + '></span>' +
      (o.alt && o.alt.length ? '<div class="ob-alt">' + o.alt.map(function (a) {
        return '<button class="ob-skip" type="button" onclick="' + a.call + '">' +
          '<span class="ob-skip-icon" aria-hidden="true">' + a.icon + '</span>' + esc(a.label) + '</button>';
      }).join('') + '</div>' : '');
  }

  function doneHtml(o) {
    return '<div class="ob-done">' +
      '<div class="ob-seal">✓</div>' +
      '<h2 class="ob-done-h">' + esc(o.title) + '</h2>' +
      '<p class="ob-done-s">' + esc(o.sub) + '</p>' +
      '<div class="ob-nxt">' + o.cards.map(function (c) {
        return '<a class="ob-nxt-c" href="#" onclick="' + c.call + ';return false;">' +
          '<div class="ob-nxt-i">' + c.icon + '</div>' +
          '<div class="ob-nxt-t">' + esc(c.title) + '</div>' +
          '<div class="ob-nxt-d">' + esc(c.desc) + '</div></a>';
      }).join('') + '</div></div>';
  }

  // Inside the dashboard a sidebar page opens through the parent, so the
  // sidebar highlight follows and the page is not framed twice.
  function openPage(url) {
    try {
      var p = window.parent;
      if (embedded() && p && typeof p.openEmbedded === 'function') {
        var item = p.document.querySelector('[data-view="' + url.split('?')[0] + '"]');
        p.openEmbedded(url, item);
        return;
      }
    } catch (e) {}
    location.href = url;
  }

  // ── Coach-marks ──────────────────────────────────────────────────────────
  // The same spotlight as onboarding.js: four solid rectangles leave a real hole,
  // so the highlighted control stays visible and clickable. An iframe can only
  // dim itself, so the dashboard is asked to dim the chrome around it.
  function tour(items, opts) {
    var t = { idx: 0, on: false, nodes: null, raf: 0 };
    opts = opts || {};

    function tellParent(on) {
      if (!embedded()) return;
      try { window.parent.postMessage({ type: 'fv-tour', on: !!on }, location.origin); } catch (e) {}
    }

    function build() {
      if (t.nodes) return t.nodes;
      var root = document.createElement('div');
      root.className = 'obt-root';
      root.innerHTML =
        '<div class="obt-mask obt-t"></div><div class="obt-mask obt-r"></div>' +
        '<div class="obt-mask obt-b"></div><div class="obt-mask obt-l"></div>' +
        '<div class="obt-ring"></div>' +
        '<div class="obt-dialog" role="dialog" aria-modal="true" aria-labelledby="pwt-title">' +
          '<button class="obt-close" type="button" aria-label="Close guide">&times;</button>' +
          '<h3 class="obt-title" id="pwt-title"></h3>' +
          '<p class="obt-body"></p>' +
          '<div class="obt-foot">' +
            '<span class="obt-count"></span>' +
            '<button class="btn btn-ghost obt-back" type="button">Back</button>' +
            '<button class="btn btn-primary obt-next" type="button">Next</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(root);
      root.querySelector('.obt-close').onclick = function () { end(true); };
      root.querySelector('.obt-back').onclick = function () { go(t.idx - 1); };
      root.querySelector('.obt-next').onclick = function () { go(t.idx + 1); };
      ['obt-t', 'obt-r', 'obt-b', 'obt-l'].forEach(function (c) {
        root.querySelector('.' + c).onclick = function () { end(true); };
      });
      t.nodes = {
        root: root,
        t: root.querySelector('.obt-t'), r: root.querySelector('.obt-r'),
        b: root.querySelector('.obt-b'), l: root.querySelector('.obt-l'),
        ring: root.querySelector('.obt-ring'),
        dialog: root.querySelector('.obt-dialog'),
        title: root.querySelector('.obt-title'),
        body: root.querySelector('.obt-body'),
        count: root.querySelector('.obt-count'),
        back: root.querySelector('.obt-back'),
        next: root.querySelector('.obt-next'),
      };
      return t.nodes;
    }

    function anchorOf(step) {
      var a = typeof step.anchor === 'function' ? step.anchor() : document.querySelector(step.anchor);
      return a && a.getBoundingClientRect().width ? a : null;
    }

    function position() {
      if (!t.on) return;
      var n = t.nodes, step = items[t.idx];
      var el = anchorOf(step);
      var vw = window.innerWidth, vh = window.innerHeight, pad = 8;
      if (!el || vw <= 700) {
        n.root.classList.add('obt-nospot');
        n.t.style.cssText = 'top:0;left:0;width:100%;height:100%';
        n.r.style.cssText = n.b.style.cssText = n.l.style.cssText = 'display:none';
        if (vw <= 700) n.dialog.style.cssText = '';
        else {
          n.dialog.style.top = '50%';
          n.dialog.style.left = '50%';
          n.dialog.style.transform = 'translate(-50%, -50%)';
        }
        return;
      }
      n.root.classList.remove('obt-nospot');
      n.r.style.display = n.b.style.display = n.l.style.display = '';
      var b = el.getBoundingClientRect();
      var x = Math.max(0, b.left - pad), y = Math.max(0, b.top - pad);
      var w = Math.min(vw, b.right + pad) - x, h = Math.min(vh, b.bottom + pad) - y;
      n.t.style.cssText = 'top:0;left:0;width:100%;height:' + y + 'px';
      n.b.style.cssText = 'top:' + (y + h) + 'px;left:0;width:100%;height:' + Math.max(0, vh - y - h) + 'px';
      n.l.style.cssText = 'top:' + y + 'px;left:0;width:' + x + 'px;height:' + h + 'px';
      n.r.style.cssText = 'top:' + y + 'px;left:' + (x + w) + 'px;width:' + Math.max(0, vw - x - w) + 'px;height:' + h + 'px';
      n.ring.style.cssText = 'top:' + y + 'px;left:' + x + 'px;width:' + w + 'px;height:' + h + 'px';

      n.dialog.style.transform = '';
      var dw = n.dialog.offsetWidth, dh = n.dialog.offsetHeight, gap = 14, dx, dy;
      if (step.placement === 'left')        { dx = x - dw - gap; dy = y; }
      else if (step.placement === 'top')    { dx = x;            dy = y - dh - gap; }
      else if (step.placement === 'bottom') { dx = x;            dy = y + h + gap; }
      else                                  { dx = x + w + gap;  dy = y; }
      if (dx + dw > vw - 12) dx = x - dw - gap;
      if (dx < 12)           dx = Math.min(x + w + gap, vw - dw - 12);
      if (dy + dh > vh - 12) dy = y - dh - gap;
      n.dialog.style.left = Math.max(12, Math.min(dx, vw - dw - 12)) + 'px';
      n.dialog.style.top  = Math.max(12, Math.min(dy, vh - dh - 12)) + 'px';
    }

    function schedule() {
      if (t.raf) return;
      t.raf = requestAnimationFrame(function () { t.raf = 0; position(); });
    }

    function go(i) {
      if (i < 0) return;
      if (i >= items.length) return end(false);
      t.idx = i;
      var n = build(), step = items[i];
      // A mark about something further down the step scrolls it into view first,
      // or the hole in the dim lands on nothing.
      var el = anchorOf(step);
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
      n.title.textContent = step.title;
      n.body.textContent = step.body;
      n.count.textContent = (i + 1) + ' of ' + items.length;
      n.back.style.visibility = i === 0 ? 'hidden' : 'visible';
      n.next.textContent = i === items.length - 1 ? 'Got it' : 'Next';
      position();
      n.next.focus();
    }

    function key(e) {
      if (!t.on) return;
      if (e.key === 'Escape') { e.preventDefault(); end(true); }
      else if (e.key === 'ArrowRight') go(t.idx + 1);
      else if (e.key === 'ArrowLeft') go(t.idx - 1);
    }

    function start() {
      if (t.on || !items.length) return;
      t.on = true;
      t.idx = 0;
      build().root.classList.add('on');
      document.body.classList.add('obt-open');
      window.addEventListener('resize', schedule);
      window.addEventListener('scroll', schedule, true);
      document.addEventListener('keydown', key);
      tellParent(true);
      go(0);
    }

    function end(skipped) {
      if (!t.on) return;
      t.on = false;
      if (t.nodes) t.nodes.root.classList.remove('on');
      document.body.classList.remove('obt-open');
      window.removeEventListener('resize', schedule);
      window.removeEventListener('scroll', schedule, true);
      document.removeEventListener('keydown', key);
      tellParent(false);
      if (opts.onEnd) opts.onEnd(skipped);
    }

    return { start: start, end: end, on: function () { return t.on; } };
  }

  // ── Borrowing a page's own controls ──────────────────────────────────────
  // A step shows the page's real panels, not copies: every handler and id keeps
  // working, and a marker left behind puts each one back exactly on exit.
  var slotN = 0;
  function take(node) {
    if (!node || node.dataset.pwSlot) return node;
    var slot = document.createElement('div');
    slot.className = 'pw-slot';
    slot.hidden = true;
    slot.dataset.pwFor = node.dataset.pwSlot = String(++slotN);
    node.parentNode.insertBefore(slot, node);
    return node;
  }
  function putBack(node) {
    var id = node.dataset ? node.dataset.pwSlot : null;
    if (!id) return;
    var slot = document.querySelector('.pw-slot[data-pw-for="' + id + '"]');
    delete node.dataset.pwSlot;
    if (slot && slot.parentNode) {
      slot.parentNode.insertBefore(node, slot);
      slot.remove();
    }
  }

  // cfg: name, host, key, tourKey, railTitle, stages, steps, tour, done,
  //      onOpen(), onClose()
  // step: stage, key, nav, title, sub, nodes() -> [Node], done() -> bool,
  //       gate() -> reason Continue is held back (or ''), wide
  function frame(cfg) {
    var NS = cfg.name, STAGES = cfg.stages, STEPS = cfg.steps;
    var state = { on: false, idx: 0, finished: false };
    var guide = tour(cfg.tour || [], {
      onEnd: function (skipped) { save(cfg.tourKey || cfg.key, { tour: skipped ? 'skipped' : 'done' }); }
    });

    function done(i) {
      var s = STEPS[i];
      try { if (s.done && s.done()) return true; } catch (e) {}
      return !s.done && seen(cfg.key).indexOf(s.key) !== -1;
    }
    function pct() {
      var n = 0;
      STEPS.forEach(function (s, i) { if (done(i)) n++; });
      return Math.round(n / STEPS.length * 100);
    }
    // Any step already reached, or the first one still to do, can be jumped to.
    function reachable(i) {
      if (i <= state.idx) return true;
      for (var k = 0; k < i; k++) if (!done(k)) return false;
      return true;
    }
    function gate(i) {
      var s = STEPS[i];
      try { return (s.gate && s.gate()) || ''; } catch (e) { return ''; }
    }

    function unmount() {
      var body = byId('pw-step-body');
      if (!body) return;
      Array.prototype.slice.call(body.children).forEach(putBack);
    }

    function shell() {
      var h = byId('pw-shell');
      if (!h) {
        h = document.createElement('div');
        h.id = 'pw-shell';
        h.className = 'pw-shell';
        (cfg.host || document.body).appendChild(h);
      }
      return h;
    }

    function render() {
      unmount();
      var h = shell();
      if (state.finished) {
        h.innerHTML = doneHtml(cfg.done);
        return;
      }
      var step = STEPS[state.idx], last = state.idx === STEPS.length - 1, why = gate(state.idx);
      h.innerHTML =
        '<div class="ob-split">' +
          '<nav class="ob-rail" aria-label="Setup steps">' + railHtml({
            title: cfg.railTitle || 'Your setup', pct: pct(), stages: STAGES, steps: STEPS,
            idx: state.idx, done: done, reachable: reachable, call: NS + '.goto' }) + '</nav>' +
          '<div class="ob-stepwrap" id="pw-stepwrap">' +
            headHtml({ crumb: crumb(STAGES, STEPS, state.idx), title: step.title, sub: step.sub }) +
            '<div class="ob-card' + (step.wide ? ' pw-wide' : '') + '" id="pw-step-body"></div>' +
            '<div class="ob-foot" id="pw-foot">' + footHtml({
              back: state.idx > 0 ? NS + '.back()' : null,
              next: { label: last ? 'Finish ✓' : 'Continue →', call: NS + '.next()',
                      disabled: !!why, why: why },
              alt: [{ icon: '◎', label: 'Show me around', call: NS + '.tourStart(true)' },
                    { icon: '⚙', label: 'Skip setup', call: NS + '.close(true)' }]
            }) + '</div>' +
          '</div>' +
        '</div>';
      var body = byId('pw-step-body');
      (step.nodes ? step.nodes() : []).forEach(function (n) {
        if (!n) return;
        take(n);
        body.appendChild(n);
      });
      if (step.enter) { try { step.enter(); } catch (e) {} }
    }

    var api = {
      running: function () { return state.on; },
      // Opens on its own for a creator who has neither finished nor skipped it;
      // an admin keeps the ordinary page and reaches it through the Guide button.
      auto: function () {
        return me().then(function (m) {
          if (m.isAdmin || entry(cfg.key).done) return false;
          api.open();
          return true;
        });
      },
      open: function () {
        if (state.on) return;
        state.on = true;
        state.finished = false;
        state.idx = api.firstUnfinished();
        document.body.classList.add('pw-running');
        render();
        if (cfg.onOpen) cfg.onOpen();
        me().then(function () {
          var t = entry(cfg.tourKey || cfg.key).tour;
          if (!t) requestAnimationFrame(function () { api.tourStart(false); });
        });
      },
      firstUnfinished: function () {
        for (var i = 0; i < STEPS.length; i++) if (!done(i)) return i;
        return 0;
      },
      goto: function (i) {
        if (!state.on || i === state.idx || !reachable(i)) return;
        state.idx = Math.max(0, Math.min(i, STEPS.length - 1));
        render();
      },
      back: function () { api.goto(state.idx - 1); },
      next: function () {
        if (gate(state.idx)) return;
        markSeen(cfg.key, STEPS[state.idx].key);
        if (state.idx === STEPS.length - 1) return api.finish();
        state.idx++;
        render();
      },
      finish: function () {
        unmount();
        state.finished = true;
        save(cfg.key, { done: true });
        render();
      },
      // Skipping and finishing both mean "stop opening this on its own".
      close: function (markDone) {
        if (!state.on) return;
        guide.end(true);
        unmount();
        var h = byId('pw-shell');
        if (h) h.remove();
        document.body.classList.remove('pw-running');
        state.on = false;
        if (markDone) save(cfg.key, { done: true });
        if (cfg.onClose) cfg.onClose();
      },
      // The page calls this when its own state moves, so the rail and Continue
      // follow a generation that finished or a persona that changed.
      refresh: function () {
        if (!state.on || state.finished) return;
        var h = byId('pw-shell');
        var rail = h && h.querySelector('.ob-rail');
        if (rail) rail.innerHTML = railHtml({
          title: cfg.railTitle || 'Your setup', pct: pct(), stages: STAGES, steps: STEPS,
          idx: state.idx, done: done, reachable: reachable, call: NS + '.goto' });
        var foot = byId('pw-foot');
        if (foot) {
          var why = gate(state.idx), btn = foot.querySelector('.btn-primary'), w = foot.querySelector('.pw-why');
          if (btn) btn.disabled = !!why;
          if (why && !w && btn) btn.insertAdjacentHTML('afterend', '<span class="pw-why">' + esc(why) + '</span>');
          else if (w) { if (why) w.textContent = why; else w.remove(); }
        }
      },
      tourStart: function (force) {
        if (!state.on || state.finished) return;
        if (!force && entry(cfg.tourKey || cfg.key).tour) return;
        guide.start();
      },
    };
    window[NS] = api;
    return api;
  }

  window.PageWizard = {
    esc: esc, me: me, entry: entry, save: save, seen: seen, markSeen: markSeen,
    railHtml: railHtml, crumb: crumb, headHtml: headHtml, footHtml: footHtml,
    doneHtml: doneHtml, openPage: openPage, tour: tour, frame: frame,
  };
})();
