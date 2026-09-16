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

  function connected() {
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
  function stageState(key) {
    var mine = [];
    STEPS.forEach(function (s, i) { if (s.stage === key) mine.push(i); });
    if (STEPS[state.idx] && STEPS[state.idx].stage === key) return 'now';
    return mine.every(isDone) ? 'done' : 'todo';
  }

  function railHtml() {
    return STAGES.map(function (st, n) {
      var stt = stageState(st.key);
      var mine = [];
      STEPS.forEach(function (s, i) { if (s.stage === st.key) mine.push(i); });
      var open = STEPS[state.idx].stage === st.key;
      return '<div class="ob-rstage">' +
        '<div class="ob-rstage-t' + (open ? ' on' : '') + '">' +
          '<span class="ob-rnum ' + stt + '">' + (stt === 'done' ? '✓' : (n + 1)) + '</span>' +
          esc(st.label) + '</div>' +
        (open ? '<div class="ob-rsteps">' + mine.map(function (i) {
          var cls = i === state.idx ? 'on' : (isDone(i) ? 'done' : '');
          return '<div class="ob-rstep ' + cls + '" onclick="' + NS + '.goto(' + i + ')">' +
            (isDone(i) && i !== state.idx ? '<span class="ob-rcheck">✓</span>'
                                          : '<span class="ob-rdot"></span>') +
            esc(STEPS[i].nav) + '</div>';
        }).join('') + '</div>' : '') +
      '</div>';
    }).join('');
  }

  function doneHtml() {
    var d = cfg.done || {};
    return '<div class="ob-done">' +
      '<div class="ob-seal">✓</div>' +
      '<h2 class="ob-done-h">She\'s set up.</h2>' +
      '<p class="ob-done-s">' + esc(d.sub || '') + '</p>' +
      '<div class="ob-nxt">' +
        '<a class="ob-nxt-c" href="#" onclick="' + NS + '.close();return false;">' +
          '<div class="ob-nxt-i">⚙️</div><div class="ob-nxt-t">Open the full console</div>' +
          '<div class="ob-nxt-d">Every setting on one page, to fine-tune what you just set up.</div></a>' +
        '<a class="ob-nxt-c" href="#" onclick="' + NS + '.jump(\'' + esc(d.logId || '') + '\');return false;">' +
          '<div class="ob-nxt-i">🩺</div><div class="ob-nxt-t">Watch the log</div>' +
          '<div class="ob-nxt-d">' + esc(d.logDesc || 'See what she does, as it happens.') + '</div></a>' +
        '<a class="ob-nxt-c" href="#" onclick="' + NS + '.replay();return false;">' +
          '<div class="ob-nxt-i">📘</div><div class="ob-nxt-t">Read it again</div>' +
          '<div class="ob-nxt-d">Walk the steps from the top. Everything you set up stays as it is.</div></a>' +
        '<a class="ob-nxt-c" href="#" onclick="' + NS + '.resetAsk();return false;">' +
          '<div class="ob-nxt-i">↺</div><div class="ob-nxt-t">Start over</div>' +
          '<div class="ob-nxt-d">' + esc(d.resetDesc || '') + '</div></a>' +
      '</div></div>';
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
    return '<div class="ob-rail">' +
        '<div class="ob-rail-h">' + esc(cfg.railTitle) + '</div>' +
        '<div class="ob-rail-p">Starting over</div>' +
      '</div>' +
      '<div class="ob-stepwrap">' +
        '<div class="ob-crumb">Start over</div>' +
        '<h2 class="ob-step-h">Erase ' + esc(personaName()) + '\'s ' + esc(r.what || 'setup') + '?</h2>' +
        '<p class="ob-step-s">This cannot be undone. It touches this model only — your other ' +
          'models keep everything they have.</p>' +
        '<div class="ob-card fg-two">' +
          '<div><b class="fg-bad">Erased</b><ul class="fg-list">' + li(r.erased) + '</ul></div>' +
          '<div><b class="fg-ok">Kept</b><ul class="fg-list">' + li(r.kept) + '</ul></div>' +
        '</div>' +
        (r.dropLabel
          ? '<label class="fg-drop"><input type="checkbox" id="fg-drop-conn" checked> ' +
            esc(r.dropLabel) + ' <span>' + esc(r.dropNote || '') + '</span></label>'
          : '') +
        '<div id="fg-reset-log" class="fg-check fg-plain"></div>' +
        '<div class="ob-foot">' +
          '<button class="btn btn-ghost" onclick="' + NS + '.resetCancel()">Cancel</button>' +
          '<button class="btn btn-primary fg-erase" id="fg-reset-go" onclick="' + NS + '.resetRun()">' +
            'Erase everything</button>' +
        '</div>' +
      '</div>';
  }

  function render() {
    var shell = byId('fg-shell');
    if (!shell) return;
    unmount();

    if (state.confirming) {
      shell.innerHTML = '<div class="ob-split">' + resetHtml() + '</div>';
      return;
    }
    if (state.finished && state.idx >= STEPS.length) {
      shell.innerHTML = '<div class="ob-split">' + doneHtml() + '</div>';
      return;
    }
    var step = STEPS[state.idx];
    var stage = STAGES.filter(function (s) { return s.key === step.stage; })[0];
    var inStage = STEPS.filter(function (s) { return s.stage === step.stage; });
    var pct = progress();
    // Nothing to do and nothing to confirm until the account is connected.
    var gated = step.needs === 'connected' && !connected();

    shell.innerHTML =
      '<div class="ob-split">' +
        '<div class="ob-rail">' +
          '<div class="ob-rail-h">' + esc(cfg.railTitle) + '</div>' +
          '<div class="ob-rail-p">' + pct + '% done</div>' +
          '<div class="ob-mini"><i style="width:' + pct + '%"></i></div>' +
          railHtml() +
        '</div>' +
        '<div class="ob-stepwrap">' +
          '<div class="ob-crumb">Stage ' + (STAGES.indexOf(stage) + 1) + ' · Step ' +
            (inStage.indexOf(step) + 1) + ' of ' + inStage.length + '</div>' +
          '<h2 class="ob-step-h">' + esc(step.title) + '</h2>' +
          (step.sub ? '<p class="ob-step-s">' + esc(step.sub) + '</p>' : '') +
          (gated ? '' : doHtml(step)) +
          '<div class="ob-card" id="fg-step-body"></div>' +
          (gated ? '' : checkHtml(step)) +
          explainHtml(step) +
          '<div class="ob-foot">' +
            (state.idx > 0 ? '<button class="btn btn-ghost" onclick="' + NS + '.back()">← Back</button>' : '') +
            '<button class="btn btn-primary" onclick="' + NS + '.next()">' +
              (state.idx === STEPS.length - 1 ? 'Finish ✓' : 'Continue →') + '</button>' +
            '<span class="ob-saved">Step ' + (state.idx + 1) + ' of ' + STEPS.length + '</span>' +
            '<div class="ob-alt">' +
              '<button class="ob-skip" type="button" onclick="' + NS + '.tourReplay()">' +
                '<span class="ob-skip-icon" aria-hidden="true">◎</span>Show me around</button>' +
              '<button class="ob-skip" type="button" onclick="' + NS + '.close()">' +
                '<span class="ob-skip-icon" aria-hidden="true">⚙</span>Skip guide</button>' +
              '<button class="ob-skip fg-danger-btn" type="button" onclick="' + NS + '.resetAsk()">' +
                '<span class="ob-skip-icon" aria-hidden="true">↺</span>Start over</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';

    mountFields(step);
    var w = shell.querySelector('.ob-stepwrap');
    if (w) w.scrollTop = 0;
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
      '#fg-shell.on{display:flex;flex-direction:column;}',
      'body.fg-open ' + SCROLL + '{display:none;}',
      '#fg-shell .ob-split{flex:1;min-height:0;}',
      // Borrowed console nodes carry their own margins from the page; inside a
      // step they are the only thing in the card.
      '#fg-step-body > *{margin-top:0;}',
      '#fg-step-body:empty{display:none;}',
      'body.fg-embedded > header{display:none;}',
      '.fg-do{list-style:none;counter-reset:fgdo;margin:0 0 18px;padding:0;max-width:64ch;}',
      '.fg-do li{counter-increment:fgdo;position:relative;padding-left:30px;margin-bottom:7px;',
      'font-size:.86rem;line-height:1.5;color:var(--text-2);}',
      '.fg-do li::before{content:counter(fgdo);position:absolute;left:0;top:0;width:20px;height:20px;',
      'border-radius:50%;background:var(--accent-soft);color:var(--accent);font-size:.7rem;',
      'font-weight:700;display:flex;align-items:center;justify-content:center;}',
      '.fg-check{font-size:.8rem;line-height:1.5;color:var(--text-muted);margin:14px 0 0;max-width:64ch;}',
      '.fg-check:not(:empty)::before{content:"✓ ";color:var(--text-3);}',
      '.fg-check.ok{color:var(--ok);}',
      '.fg-check.ok::before{color:var(--ok);}',
      '.fg-check.fg-plain::before{content:"";}',
      '.fg-check.fg-bad{color:#fb7185;}',
      '.fg-check.fg-bad::before{content:"";}',
      '.fg-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px;}',
      '.fg-bad{color:#fb7185;}',
      '.fg-ok{color:var(--ok);}',
      '.fg-list{margin:8px 0 0;padding-left:18px;font-size:.82rem;line-height:1.6;color:var(--text-2);}',
      '.fg-drop{display:block;margin-top:14px;font-size:.82rem;font-weight:400;',
      'color:var(--text-2);cursor:pointer;}',
      '.fg-drop input{width:auto;margin-right:7px;}',
      '.fg-drop span{color:var(--text-muted);font-weight:400;}',
      // Continue and "erase everything" must never look like the same button.
      '.fg-erase{background:#dc2626;border-color:#dc2626;}',
      '.fg-erase:hover{background:#b91c1c;border-color:#b91c1c;}',
      '.fg-erase[disabled]{opacity:.6;cursor:default;}',
      '.fg-danger-btn:hover{border-color:#fb7185;color:#fb7185;}',
      '.fg-more{max-width:64ch;margin-top:18px;}',
      '.fg-more > summary{cursor:pointer;font-size:.78rem;color:var(--text-muted);list-style:none;',
      'display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:var(--r);',
      'border:1px solid var(--border);background:var(--panel);}',
      '.fg-more > summary::-webkit-details-marker{display:none;}',
      '.fg-more > summary::before{content:"▸";font-size:.7rem;}',
      '.fg-more[open] > summary::before{content:"▾";}',
      '.fg-more > summary:hover{color:var(--text-2);border-color:var(--accent-line);}',
      '.fg-explain{font-size:.86rem;line-height:1.62;color:var(--text-2);max-width:64ch;margin-top:14px;}',
      '.fg-explain p{margin:0 0 10px;}',
      '.fg-explain code{background:var(--surface);padding:1px 5px;border-radius:var(--r-sm);font-size:.8rem;}',
      '.fg-note{border-left:2px solid var(--accent);padding-left:10px;margin-top:14px;color:var(--text-muted);}',
      '.fg-dl{margin:0 0 4px;}',
      '.fg-dl dt{font-weight:600;color:var(--text);margin-top:12px;}',
      '.fg-dl dt:first-child{margin-top:0;}',
      '.fg-dl dd{margin:2px 0 0;color:var(--text-2);}',
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
