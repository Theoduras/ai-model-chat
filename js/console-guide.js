// The engine behind the console setup wizards (/fanvue, /xbot).
//
// It is the frame, not the words: the stages, the step rail, the coach-mark
// tour, and the trick the wizards are built on — the console's real controls
// are *moved* into the step and put back when it closes, so what a creator
// changes inside the guide is changed for real and every handler on the page
// keeps working.
//
// A console supplies its own stages, steps, tour and start-over list through
// create(); nothing platform-specific lives here, so a third guide is a
// content file rather than another copy of this one.
(function () {

  var injected = false;

  function create(cfg) {
    var NS = cfg.name;
    var STAGES = cfg.stages;
    var STEPS = cfg.steps;
    var TOUR = cfg.tour || [];
    var SCROLL = cfg.scroll || '.fv-scroll';

  var state = { idx: 0, on: false, seen: {}, finished: false, toured: false, confirming: false };
  var tour = { on: false, items: [], i: 0, el: null };

  function byId(id) { return document.getElementById(id); }

  function jpost(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) }).then(function (r) { return r.json(); });
  }
  function jdel(url) {
    return fetch(url, { method: 'DELETE' }).then(function (r) { return r.json(); });
  }

  function embedded() {
    try { return window.self !== window.top; } catch (e) { return true; }
  }
  // A coach-mark inside an iframe can only dim the iframe. Ask the dashboard to
  // dim its own chrome for the duration so the whole window goes dark, the way
  // it does when the persona wizard runs.
  function tellParent(on) {
    if (!embedded()) return;
    try { window.parent.postMessage({ type: 'fv-tour', on: !!on }, location.origin); }
    catch (e) {}
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function load() {
    try {
      var raw = JSON.parse(localStorage.getItem(cfg.storageKey) || '{}') || {};
      state.seen = raw.seen || {};
      state.finished = !!raw.finished;
      state.toured = !!raw.toured;
    } catch (e) { state.seen = {}; }
  }
  function save() {
    try {
      localStorage.setItem(cfg.storageKey, JSON.stringify(
        { seen: state.seen, finished: state.finished, toured: state.toured }));
    } catch (e) {}
  }
  function markSeen(i) { state.seen[STEPS[i].nav] = 1; save(); }

  // A step counts as done when its own check says so — a real connection, a
  // running worker — and otherwise when it has been read.
  function isDone(i) {
    var s = STEPS[i];
    if (s.done) { try { if (s.done()) return true; } catch (e) {} }
    return !!state.seen[s.nav];
  }

  // Most consoles say "connected" with a green pill next to the account
  // dropdown; one that says it some other way passes its own reader in.
  function connected() {
    if (cfg.connected) { try { return !!cfg.connected(); } catch (e) { return false; } }
    var p = byId('conn-pill');
    return !!(p && p.classList.contains('ok'));
  }

  // ---- moving the console's own controls ---------------------------------

  function stash() {
    var h = byId('fg-stash');
    if (!h) {
      h = document.createElement('div');
      h.id = 'fg-stash';
      h.style.display = 'none';
      document.body.appendChild(h);
    }
    return h;
  }

  function resolve(ref) {
    var m = /^(\.[\w-]+):(.+)$/.exec(ref);
    if (m) {
      var inner = byId(m[2]);
      return inner ? inner.closest(m[1]) : null;
    }
    return byId(ref);
  }

  // Leave a marker where the node lived, so putting it back is exact.
  function take(node) {
    if (!node || node.dataset.fgSlot) return node;
    var slot = document.createElement('div');
    slot.className = 'fg-slot';
    slot.style.display = 'none';
    slot.dataset.fgFor = node.dataset.fgSlot = String(++take.n);
    node.parentNode.insertBefore(slot, node);
    return node;
  }
  take.n = 0;

  function putBack(node) {
    var id = node.dataset ? node.dataset.fgSlot : null;
    if (!id) return;
    var slot = document.querySelector('.fg-slot[data-fg-for="' + id + '"]');
    delete node.dataset.fgSlot;
    if (slot && slot.parentNode) {
      slot.parentNode.insertBefore(node, slot);
      slot.remove();
    } else {
      stash().appendChild(node);
    }
  }

  // Park whatever the current step borrowed, before rendering the next one.
  function unmount() {
    var card = byId('fg-step-body');
    if (!card) return;
    Array.prototype.slice.call(card.children).forEach(function (n) {
      if (n.dataset && n.dataset.fgSlot) putBack(n);
    });
  }

  function mountFields(step) {
    var card = byId('fg-step-body');
    if (!card) return;
    if (step.needs === 'connected' && !connected()) {
      card.innerHTML = '<p class="hint">' + cfg.gate +
        ' <a href="#" onclick="' + NS + '.goto(1);return false;">Back to Connect</a></p>';
      return;
    }
    (step.fields || []).forEach(function (ref) {
      var node = resolve(ref);
      if (!node) return;
      take(node);
      card.appendChild(node);
    });
    if (!card.children.length) card.style.display = 'none';
    else card.style.display = '';
  }

  // ---- rendering ---------------------------------------------------------

  // What to do on this step, what the console looks like when it is done, and
  // the field-by-field detail folded away underneath.
  function doHtml(step) {
    if (!step.do || !step.do.length) return '';
    return '<ol class="fg-do">' + step.do.map(function (d) {
      return '<li>' + d + '</li>';
    }).join('') + '</ol>';
  }
  function checkHtml(step) {
    if (!step.check) return '';
    var ok = false;
    if (step.done) { try { ok = !!step.done(); } catch (e) {} }
    return '<p class="fg-check' + (ok ? ' ok' : '') + '">' + step.check + '</p>';
  }
  // A step that borrows no controls is explanation only — hiding all of it
  // behind a disclosure would leave the step empty.
  function explainHtml(step) {
    if (!(step.fields || []).length) return '<div class="fg-explain">' + step.body + '</div>';
    return '<details class="fg-more"><summary>What every field here does</summary>' +
      '<div class="fg-explain">' + step.body + '</div></details>';
  }

  function progress() {
    var n = 0;
    STEPS.forEach(function (s, i) { if (isDone(i)) n++; });
    return Math.round(n / STEPS.length * 100);
  }
  function stageSteps(key) {
    var mine = [];
    STEPS.forEach(function (s, i) { if (s.stage === key) mine.push(i); });
    return mine;
  }

  // The wizard is drawn in the console's own language: the segmented tab bar
  // js/console-shell.js uses for Overview/Inbox/Settings/Advanced, the same
  // workflow pill chain under it, and the page's .form-section cards. One tab
  // per stage — clicking a stage opens the first step in it you have not done.
  function stageTabs() {
    return '<div class="cn-tabs" role="tablist">' + STAGES.map(function (st, n) {
      var mine = stageSteps(st.key);
      var got = mine.filter(isDone).length;
      var all = got === mine.length;
      var on = STEPS[state.idx] && STEPS[state.idx].stage === st.key;
      return '<button class="cn-tab' + (on ? ' on' : '') + '" role="tab" type="button" ' +
        'onclick="' + NS + '.gotoStage(\'' + st.key + '\')">' +
        '<span class="cn-tab-ic">' + (st.icon || (n + 1)) + '</span>' + esc(st.label) +
        '<span class="cn-tab-badge' + (all ? ' ok' : got ? '' : ' none') + '">' +
          (all ? '\u2713' : got + '/' + mine.length) + '</span></button>';
    }).join('') + '</div>';
  }

  function stepPills() {
    var mine = stageSteps(STEPS[state.idx].stage);
    if (mine.length < 2) return '';
    return '<div class="cn-steps">' + mine.map(function (i, n) {
      var cls = i === state.idx ? ' now' : (isDone(i) ? ' done' : '');
      return '<button class="cn-step' + cls + '" type="button" onclick="' + NS +
        '.goto(' + i + ')"><span class="cn-step-n">' +
        (isDone(i) && i !== state.idx ? '\u2713' : (n + 1)) + '</span>' +
        esc(STEPS[i].nav) + '</button>';
    }).join('') + '</div>';
  }

  // The same read-out the Overview tab opens with: one line on where setup
  // stands, and the dot that colours it.
  function heroHtml() {
    var pct = progress();
    var live = pct === 100;
    var step = STEPS[Math.min(state.idx, STEPS.length - 1)];
    return '<div class="cn-hero ' + (live ? 'live' : connected() ? 'warn' : 'off') + '">' +
      '<span class="cn-hero-dot"></span>' +
      '<div class="cn-hero-txt">' +
        '<div class="cn-hero-title">' + esc(cfg.railTitle) + ' \u00b7 ' + pct + '% done</div>' +
        '<div class="cn-hero-sub">' + (live
          ? 'Every step is done. Anything here can still be changed.'
          : 'Step ' + (state.idx + 1) + ' of ' + STEPS.length + ' \u00b7 ' + esc(step.nav)) +
        '</div></div>' +
      '<div class="fg-bar"><i style="width:' + pct + '%"></i></div></div>';
  }

  function footHtml() {
    return '<div class="fg-foot">' +
      (state.idx > 0 ? '<button class="btn btn-ghost" onclick="' + NS + '.back()">\u2190 Back</button>' : '') +
      '<button class="btn btn-primary" onclick="' + NS + '.next()">' +
        (state.idx === STEPS.length - 1 ? 'Finish \u2713' : 'Continue \u2192') + '</button>' +
      '<span class="fg-count">Step ' + (state.idx + 1) + ' of ' + STEPS.length + '</span>' +
      '<div class="fg-alt">' +
        '<button class="fg-skip" type="button" onclick="' + NS + '.tourReplay()">' +
          '<span aria-hidden="true">\u25ce</span>Show me around</button>' +
        '<button class="fg-skip" type="button" onclick="' + NS + '.close()">' +
          '<span aria-hidden="true">\u2699</span>Skip guide</button>' +
        '<button class="fg-skip fg-danger-btn" type="button" onclick="' + NS + '.resetAsk()">' +
          '<span aria-hidden="true">\u21ba</span>Start over</button>' +
      '</div></div>';
  }

  function doneHtml() {
    var d = cfg.done || {};
    var card = function (icon, title, desc, call) {
      return '<a class="fg-nxt-c" href="#" onclick="' + NS + '.' + call + ';return false;">' +
        '<div class="fg-nxt-i">' + icon + '</div><div class="fg-nxt-t">' + esc(title) + '</div>' +
        '<div class="fg-nxt-d">' + esc(desc) + '</div></a>';
    };
    return '<div class="fg-page">' +
      '<div class="cn-hero live"><span class="cn-hero-dot"></span><div class="cn-hero-txt">' +
        '<div class="cn-hero-title">She\'s set up.</div>' +
        '<div class="cn-hero-sub">' + esc(d.sub || '') + '</div></div></div>' +
      '<div class="form-section">' +
        '<div class="section-title">Where to next</div>' +
        '<div class="fg-nxt">' +
          card('\u2699\ufe0f', 'Open the full console',
               'Every setting on one page, to fine-tune what you just set up.', 'close()') +
          card('\ud83e\ude7a', 'Watch the log',
               d.logDesc || 'See what she does, as it happens.',
               'jump(\'' + esc(d.logId || '') + '\')') +
          card('\ud83d\udcd8', 'Read it again',
               'Walk the steps from the top. Everything you set up stays as it is.', 'replay()') +
          card('\u21ba', 'Start over', d.resetDesc || '', 'resetAsk()') +
        '</div></div></div>';
  }

  function personaName() {
    var sel = byId('persona');
    if (!sel) return 'this model';
    var o = sel.options[sel.selectedIndex];
    return (o && o.textContent) || sel.value || 'this model';
  }

  // Everything the console holds for one model, in one place — because doing it
  // by hand means five separate controls and it is easy to half-finish.
  function resetHtml() {
    var r = cfg.reset || {};
    var li = function (rows) {
      return (rows || []).map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('');
    };
    return '<div class="fg-page">' +
      '<div class="cn-hero off"><span class="cn-hero-dot"></span><div class="cn-hero-txt">' +
        '<div class="cn-hero-title">Erase ' + esc(personaName()) + '\'s ' +
          esc(r.what || 'setup') + '?</div>' +
        '<div class="cn-hero-sub">This cannot be undone. It touches this model only \u2014 ' +
          'your other models keep everything they have.</div></div></div>' +
      '<div class="form-section">' +
        '<div class="section-title">What goes, what stays</div>' +
        '<div class="fg-two">' +
          '<div><b class="fg-bad">Erased</b><ul class="fg-list">' + li(r.erased) + '</ul></div>' +
          '<div><b class="fg-ok">Kept</b><ul class="fg-list">' + li(r.kept) + '</ul></div>' +
        '</div>' +
        (r.dropLabel
          ? '<label class="fg-drop"><input type="checkbox" id="fg-drop-conn" checked> ' +
            esc(r.dropLabel) + ' <span>' + esc(r.dropNote || '') + '</span></label>'
          : '') +
        '<div id="fg-reset-log" class="fg-check fg-plain"></div>' +
      '</div>' +
      '<div class="fg-foot">' +
        '<button class="btn btn-ghost" onclick="' + NS + '.resetCancel()">Cancel</button>' +
        '<button class="btn btn-primary fg-erase" id="fg-reset-go" onclick="' + NS + '.resetRun()">' +
          'Erase everything</button>' +
      '</div></div>';
  }

  function render() {
    var shell = byId('fg-shell');
    if (!shell) return;
    unmount();

    if (state.confirming) { shell.innerHTML = resetHtml(); return; }
    if (state.finished && state.idx >= STEPS.length) { shell.innerHTML = doneHtml(); return; }

    var step = STEPS[state.idx];
    // Nothing to do and nothing to confirm until the account is connected.
    var gated = step.needs === 'connected' && !connected();

    shell.innerHTML =
      '<div class="fg-page">' +
        heroHtml() +
        stageTabs() +
        stepPills() +
        '<div class="form-section" id="fg-step">' +
          '<div class="section-title">' + esc(step.title) + '</div>' +
          (step.sub ? '<span class="hint fg-sub">' + esc(step.sub) + '</span>' : '') +
          (gated ? '' : doHtml(step)) +
          '<div id="fg-step-body"></div>' +
          (gated ? '' : checkHtml(step)) +
          explainHtml(step) +
        '</div>' +
        footHtml() +
      '</div>';

    mountFields(step);
    shell.scrollTop = 0;
  }

  // ---- intro coach-marks -------------------------------------------------

  function tourEls() {
    var r = byId('fg-tour');
    return { root: r,
      t: r.querySelector('.obt-t'), rr: r.querySelector('.obt-r'),
      b: r.querySelector('.obt-b'), l: r.querySelector('.obt-l'),
      ring: r.querySelector('.obt-ring'), dlg: r.querySelector('.obt-dialog') };
  }

  function place() {
    var item = tour.items[tour.i];
    var e = tourEls();
    var el = tour.el;
    var pad = 8, vw = window.innerWidth, vh = window.innerHeight;
    var r = el ? el.getBoundingClientRect() : null;
    if (!r || !r.width) {
      e.root.classList.add('obt-nospot');
      e.t.style.cssText = 'position:fixed;inset:0;';
    } else {
      e.root.classList.remove('obt-nospot');
      var top = Math.max(0, r.top - pad), left = Math.max(0, r.left - pad);
      var right = Math.min(vw, r.right + pad), bot = Math.min(vh, r.bottom + pad);
      e.t.style.cssText = 'position:fixed;top:0;left:0;right:0;height:' + top + 'px;';
      e.b.style.cssText = 'position:fixed;top:' + bot + 'px;left:0;right:0;bottom:0;';
      e.l.style.cssText = 'position:fixed;top:' + top + 'px;left:0;width:' + left +
        'px;height:' + (bot - top) + 'px;';
      e.rr.style.cssText = 'position:fixed;top:' + top + 'px;left:' + right + 'px;width:' +
        (vw - right) + 'px;height:' + (bot - top) + 'px;';
      e.ring.style.cssText = 'position:fixed;top:' + top + 'px;left:' + left + 'px;width:' +
        (right - left) + 'px;height:' + (bot - top) + 'px;';
    }

    e.dlg.innerHTML =
      '<button class="obt-close" type="button" onclick="' + NS + '.tourEnd()" aria-label="Close">✕</button>' +
      '<div class="obt-title">' + esc(item.title) + '</div>' +
      '<div class="obt-body">' + item.body + '</div>' +
      '<div class="obt-foot">' +
        '<span class="obt-count">' + (tour.i + 1) + ' of ' + tour.items.length + '</span>' +
        (tour.i > 0 ? '<button class="btn btn-ghost" type="button" onclick="' + NS + '.tourGo(' +
          (tour.i - 1) + ')">Back</button>' : '') +
        '<button class="btn btn-primary" type="button" onclick="' + NS + '.' +
          (tour.i < tour.items.length - 1 ? 'tourGo(' + (tour.i + 1) + ')">Next' : 'tourEnd()">Got it') +
        '</button>' +
      '</div>';

    // Measure before placing: the copy varies enough that a fixed height would
    // push the dialog off-screen on the short marks.
    var dw = e.dlg.offsetWidth || Math.min(380, vw - 24);
    var dh = e.dlg.offsetHeight || 200;
    var gap = 14, pl = item.placement || 'bottom', top2, left2;
    if (!r || !r.width) pl = 'center';
    else {
      if (pl === 'right' && r.right + gap + dw > vw) pl = 'left';
      if (pl === 'top' && r.top - gap - dh < 0) pl = 'bottom';
      if (pl === 'bottom' && r.bottom + gap + dh > vh) pl = r.top - gap - dh > 0 ? 'top' : 'bottom';
    }
    if (pl === 'center') { top2 = (vh - dh) / 2; left2 = (vw - dw) / 2; }
    else if (pl === 'right') { top2 = r.top; left2 = r.right + gap; }
    else if (pl === 'left') { top2 = r.top; left2 = r.left - gap - dw; }
    else if (pl === 'top') { top2 = r.top - gap - dh; left2 = r.left; }
    else { top2 = r.bottom + gap; left2 = r.left; }
    e.dlg.style.top = Math.max(12, Math.min(top2, vh - dh - 12)) + 'px';
    e.dlg.style.left = Math.max(12, Math.min(left2, vw - dw - 12)) + 'px';
  }

  var api = {
    open: function () {
      if (state.on) return;
      state.on = true;
      if (state.idx >= STEPS.length && !state.finished) state.idx = 0;
      document.body.classList.add('fg-open');
      // Inside the dashboard the page's own header is a second copy of chrome
      // the dashboard already draws, so the wizard runs without it.
      if (embedded()) document.body.classList.add('fg-embedded');
      byId('fg-shell').classList.add('on');
      state.idx = state.finished ? state.idx : api.firstUnfinished();
      markSeen(Math.min(state.idx, STEPS.length - 1));
      render();
      if (!state.toured) {
        state.toured = true;
        save();
        setTimeout(function () { api.tourReplay(); }, 280);
      }
    },
    firstUnfinished: function () {
      for (var i = 0; i < STEPS.length; i++) if (!isDone(i)) return i;
      return 0;
    },
    // Closing hands the console back with every borrowed control returned to
    // its own place, so the page is exactly as it would have been.
    close: function () {
      api.tourEnd(true);
      unmount();
      byId('fg-shell').classList.remove('on');
      byId('fg-shell').innerHTML = '';
      document.body.classList.remove('fg-open', 'fg-embedded');
      state.on = false;
      try { localStorage.setItem(cfg.storageKey + 'Opened', '1'); } catch (e) {}
    },
    replay: function () { state.idx = 0; state.finished = false; save(); render(); },
    // A stage tab opens the first step in it still to do, or its last step
    // once the whole stage is done.
    gotoStage: function (key) {
      var mine = stageSteps(key);
      if (!mine.length) return;
      var next = mine.filter(function (i) { return !isDone(i); })[0];
      api.goto(next === undefined ? mine[mine.length - 1] : next);
    },
    goto: function (i) {
      state.idx = Math.max(0, Math.min(STEPS.length - 1, i));
      markSeen(state.idx);
      render();
    },
    next: function () {
      markSeen(state.idx);
      if (state.idx === STEPS.length - 1) {
        state.finished = true;
        state.idx = STEPS.length;
        save();
        render();
        return;
      }
      api.goto(state.idx + 1);
    },
    back: function () { api.goto(state.idx - 1); },
    // Leave the wizard and land on a control in the console behind it.
    jump: function (id) {
      api.close();
      var el = byId(id);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },
    resetAsk: function () {
      api.tourEnd();
      state.confirming = true;
      render();
    },
    resetCancel: function () {
      state.confirming = false;
      if (state.finished) state.idx = STEPS.length;
      render();
    },
    resetRun: function () {
      var sel = byId('persona');
      var slug = sel ? sel.value : '';
      var log = byId('fg-reset-log');
      var go = byId('fg-reset-go');
      var drop = byId('fg-drop-conn');
      if (!slug) { log.className = 'fg-check fg-plain fg-bad'; log.textContent = 'Pick a model first.'; return; }

      // Order matters: the console's own reset list stops the worker before
      // anything it reads is deleted, so a run that fails half way leaves her
      // off, never live on a half-erased setup.
      var jobs = (cfg.reset.jobs || function () { return []; })(
        slug, !drop || drop.checked, { post: jpost, del: jdel });

      go.disabled = true;
      if (drop) drop.disabled = true;
      var i = 0;
      function run() {
        if (i >= jobs.length) return done();
        log.className = 'fg-check';
        log.textContent = jobs[i][0] + '…';
        jobs[i][1]().then(function (d) {
          if (d && d.ok === false) throw new Error(d.error || 'Failed');
          i++; run();
        }).catch(function (e) {
          log.className = 'fg-check fg-plain fg-bad';
          log.innerHTML = '✗ ' + esc(jobs[i][0]) + ' failed: ' + esc(e.message || String(e)) +
            '. Auto-reply is off, so nothing is running — press Erase everything to try again.';
          go.disabled = false;
          if (drop) drop.disabled = false;
        });
      }
      function done() {
        try {
          localStorage.removeItem(cfg.storageKey);
          localStorage.removeItem(cfg.storageKey + 'Opened');
        } catch (e) {}
        state.seen = {};
        state.finished = false;
        state.idx = 0;
        state.confirming = false;
        save();
        // The console's own loaders repaint the controls the wizard is holding —
        // they rewrite their contents, they do not move them, so the borrowed
        // nodes stay where the step put them.
        if (typeof cfg.afterReset === 'function') { try { cfg.afterReset(); } catch (e) {} }
        render();
      }
      run();
    },
    tourReplay: function () {
      // A mark with nothing to point at dims the screen and explains a hole
      // that isn't there — so skip anchors that are missing or unrendered (the
      // overview step borrows no controls, so its field box is empty).
      var items = TOUR.filter(function (t) {
        var el = document.querySelector(t.anchor);
        return !!(el && el.getBoundingClientRect().width);
      });
      if (!items.length) return;
      tour.items = items;
      tour.on = true;
      tellParent(true);
      byId('fg-tour').classList.add('on');
      api.tourGo(0);
    },
    tourGo: function (i) {
      tour.i = Math.max(0, Math.min(tour.items.length - 1, i));
      tour.el = document.querySelector(tour.items[tour.i].anchor);
      place();
    },
    tourEnd: function () {
      if (!tour.on) return;
      tour.on = false;
      tellParent(false);
      byId('fg-tour').classList.remove('on');
    }
  };

  function mount() {
    load();

    if (injected) return mountNodes();
    injected = true;
    var css = document.createElement('style');
    css.textContent = [
      // The wizard takes the console's slot under the page header, not the
      // whole viewport — same as the persona wizard inside the dashboard.
      '#fg-shell{flex:1;min-height:0;display:none;background:var(--bg);}',
      '#fg-shell.on{display:block;overflow-y:auto;}',
      'body.fg-open ' + SCROLL + '{display:none;}',
      'body.fg-embedded > header{display:none;}',
      // Same column the console itself is set in, so leaving the guide does not
      // move anything sideways.
      '.fg-page{max-width:760px;margin:0 auto;padding:28px 24px 80px;',
      'display:flex;flex-direction:column;gap:20px;}',
      '@media (max-width:640px){.fg-page{padding:18px 14px 70px;gap:16px;}',
      // Four stage labels do not fit a phone. Let them size to their words and
      // let the bar scroll, rather than squeeze until they overlap.
      '.fg-page .cn-tab{flex:0 0 auto;min-width:0;}}',
      // The hero carries the progress bar the left rail used to.
      '.fg-bar{margin-left:auto;flex-shrink:0;width:104px;height:5px;border-radius:9999px;',
      'background:var(--surface-2);overflow:hidden;}',
      '.fg-bar i{display:block;height:100%;background:var(--grad);background-size:300% 100%;}',
      '@media (max-width:640px){.fg-bar{display:none;}}',
      '.cn-tab-badge.ok{background:var(--ok);color:#04240f;}',
      '.cn-tab-badge.none{background:var(--surface-2);color:var(--text-muted);}',
      '.cn-tab.on .cn-tab-badge.none{background:var(--surface);color:var(--text-3);}',
      // A step title is a sentence, not a label, so it keeps the section header's
      // accent bar and colour without the console's uppercase tracking.
      '#fg-step > .section-title{text-transform:none;letter-spacing:0;font-size:1rem;}',
      '.fg-sub{display:block;margin:-4px 0 4px;max-width:64ch;}',
      // Borrowed console nodes carry their own margins from the page; inside a
      // step they are the only thing in the card.
      '#fg-step-body{display:flex;flex-direction:column;gap:16px;}',
      '#fg-step-body > *{margin-top:0;}',
      '#fg-step-body > .form-section{background:transparent;border:0;padding:0;box-shadow:none;}',
      '#fg-step-body:empty{display:none;}',
      '.fg-foot{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}',
      '.fg-count{font-size:.74rem;color:var(--text-muted);}',
      '.fg-alt{margin-left:auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap;}',
      '.fg-skip{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;min-height:36px;',
      'background:var(--panel);border:1px solid var(--border);border-radius:var(--r);',
      'cursor:pointer;font-family:var(--font);font-size:.78rem;font-weight:500;',
      'color:var(--text-2);white-space:nowrap;}',
      '.fg-skip:hover{background:var(--surface);border-color:var(--accent-line);color:var(--text);}',
      '.fg-do{list-style:none;counter-reset:fgdo;margin:0;padding:0;max-width:64ch;}',
      '.fg-do li{counter-increment:fgdo;position:relative;padding-left:30px;margin-bottom:7px;',
      'font-size:.86rem;line-height:1.5;color:var(--text-2);}',
      '.fg-do li::before{content:counter(fgdo);position:absolute;left:0;top:0;width:20px;height:20px;',
      'border-radius:50%;background:var(--accent-soft);color:var(--accent);font-size:.7rem;',
      'font-weight:700;display:flex;align-items:center;justify-content:center;}',
      '.fg-check{font-size:.8rem;line-height:1.5;color:var(--text-muted);margin:0;max-width:64ch;}',
      '.fg-check:not(:empty)::before{content:"\u2713 ";color:var(--text-3);}',
      '.fg-check.ok{color:var(--ok);}',
      '.fg-check.ok::before{color:var(--ok);}',
      '.fg-check.fg-plain::before{content:"";}',
      '.fg-check.fg-bad{color:#fb7185;}',
      '.fg-check.fg-bad::before{content:"";}',
      '.fg-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px;}',
      '.fg-bad{color:#fb7185;}',
      '.fg-ok{color:var(--ok);}',
      '.fg-list{margin:8px 0 0;padding-left:18px;font-size:.82rem;line-height:1.6;color:var(--text-2);}',
      '.fg-drop{display:block;font-size:.82rem;font-weight:400;color:var(--text-2);cursor:pointer;}',
      '.fg-drop input{width:auto;margin-right:7px;}',
      '.fg-drop span{color:var(--text-muted);font-weight:400;}',
      // Continue and "erase everything" must never look like the same button.
      '.fg-erase{background:#dc2626;border-color:#dc2626;}',
      '.fg-erase:hover{background:#b91c1c;border-color:#b91c1c;}',
      '.fg-erase[disabled]{opacity:.6;cursor:default;}',
      '.fg-danger-btn:hover{border-color:#fb7185;color:#fb7185;}',
      '.fg-more{max-width:64ch;}',
      '.fg-more > summary{cursor:pointer;font-size:.78rem;color:var(--text-muted);list-style:none;',
      'display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:var(--r);',
      'border:1px solid var(--border);background:var(--panel);}',
      '.fg-more > summary::-webkit-details-marker{display:none;}',
      '.fg-more > summary::before{content:"\u25b8";font-size:.7rem;}',
      '.fg-more[open] > summary::before{content:"\u25be";}',
      '.fg-more > summary:hover{color:var(--text-2);border-color:var(--accent-line);}',
      '.fg-explain{font-size:.86rem;line-height:1.62;color:var(--text-2);max-width:64ch;margin-top:14px;}',
      '.fg-explain p{margin:0 0 10px;}',
      '.fg-explain code{background:var(--surface);padding:1px 5px;border-radius:var(--r-sm);font-size:.8rem;}',
      '.fg-note{border-left:2px solid var(--accent);padding-left:10px;margin-top:14px;color:var(--text-muted);}',
      '.fg-dl{margin:0 0 4px;}',
      '.fg-dl dt{font-weight:600;color:var(--text);margin-top:12px;}',
      '.fg-dl dt:first-child{margin-top:0;}',
      '.fg-dl dd{margin:2px 0 0;color:var(--text-2);}',
      // The done card's onward links, in the same shape as the console's
      // health cards.
      '.fg-nxt{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:12px;}',
      '.fg-nxt-c{background:var(--panel);border:1px solid var(--border);border-radius:var(--r);',
      'padding:17px;text-decoration:none;color:inherit;}',
      '.fg-nxt-c:hover{border-color:var(--accent-line);}',
      '.fg-nxt-i{width:31px;height:31px;border-radius:var(--r-sm);background:var(--accent-soft);',
      'border:1px solid var(--accent-line);display:flex;align-items:center;justify-content:center;',
      'margin-bottom:10px;font-size:.9rem;}',
      '.fg-nxt-t{font-size:.84rem;font-weight:600;margin-bottom:4px;}',
      '.fg-nxt-d{font-size:.73rem;color:var(--text-muted);line-height:1.5;}',
      '.obt-body code{background:var(--surface);padding:1px 5px;border-radius:var(--r-sm);}'
    ].join('');
    document.head.appendChild(css);
    mountNodes();
  }

  function mountNodes() {
    var shell = document.createElement('div');
    shell.id = 'fg-shell';
    var scroll = document.querySelector(SCROLL);
    if (scroll && scroll.parentNode) scroll.parentNode.insertBefore(shell, scroll.nextSibling);
    else document.body.appendChild(shell);

    var t = document.createElement('div');
    t.id = 'fg-tour';
    t.className = 'obt-root';
    t.innerHTML = '<div class="obt-mask obt-t"></div><div class="obt-mask obt-r"></div>' +
      '<div class="obt-mask obt-b"></div><div class="obt-mask obt-l"></div>' +
      '<div class="obt-ring"></div><div class="obt-dialog"></div>';
    document.body.appendChild(t);

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      if (tour.on) api.tourEnd();
      else if (state.on) api.close();
    });
    window.addEventListener('resize', function () { if (tour.on) place(); });

    var first = false;
    try { first = !localStorage.getItem(cfg.storageKey + 'Opened'); } catch (e) {}
    if (first) api.open();
  }

    window[NS] = api;
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', mount);
    } else {
      mount();
    }
    return api;
  }

  window.ConsoleGuide = { create: create };
})();
