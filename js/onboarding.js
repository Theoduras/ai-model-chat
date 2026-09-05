// Guided persona setup for non-admin creators.
//
// The wizard does not duplicate the builder's fields — it moves the real ones,
// which renderForm() already built, into a one-step-at-a-time frame. Every input
// keeps its id, so collectConfig() and the save endpoint work untouched and the
// admin's full-form view stays the single source of truth.
(function () {

  var STAGES = [
    { key: 'who',   label: 'Who she is' },
    { key: 'talks', label: 'How she talks' },
    { key: 'earns', label: 'How she earns' },
    { key: 'live',  label: 'Go live' },
  ];

  // `pick` returns the real form nodes for the step. `done` decides whether the
  // step counts as finished, which drives both the progress bar and the rail.
  var STEPS = [
    { stage: 'who', key: 'basics', nav: 'Name & age',
      title: 'Who is she?',
      sub: 'The basics every fan sees first. Leave the location blank and she\'ll claim to be from wherever the fan is.',
      pick: function () { return fields(['f-name', 'f-age', 'f-gender', 'f-location']); },
      done: function (c) { return !!(c.name && c.age); } },

    { stage: 'who', key: 'personality', nav: 'Personality',
      title: 'What\'s she like?',
      sub: 'This shapes every message she sends. Pick the closest fit — you can fine-tune the details later.',
      pick: function () { return fields(['f-archetype']); },
      done: function (c) { return !!c.archetype; } },

    { stage: 'who', key: 'backstory', nav: 'Backstory',
      title: 'What\'s her story?',
      sub: 'Two or three sentences — her job, her city, her vibe. Let AI interview you if it\'s easier.',
      pick: function () { return fields(['f-backstory']); },
      done: function (c) { return (c.backstory || '').trim().length > 20; } },

    { stage: 'who', key: 'photos', nav: 'Photos',
      title: 'What does she look like?',
      sub: 'Generate a fictional face, then add more shots of the same person. Fans see these on her profile.',
      pick: function () { return section('photo-grid'); },
      done: function () { return (document.querySelectorAll('#photo-grid .photo-item, #photo-grid img') || []).length > 0; } },

    { stage: 'talks', key: 'speech', nav: 'Speech style',
      title: 'How does she write?',
      sub: 'Sentence length, punctuation, slang and emoji. Generate it from her personality, or write your own rules.',
      pick: function () { return fields(['f-speech', 'f-reply-length', 'f-emoji-use', 'f-lowercase']); },
      done: function (c) { return (c.speech_style || '').trim().length > 10; } },

    { stage: 'talks', key: 'warmth', nav: 'Warmth',
      title: 'How warm is she?',
      sub: 'Cold and hard to read, or affectionate from the first message — and how often she asks questions back.',
      pick: function () { return fields(['f-warmth', 'f-qfreq']); },
      done: function (c) { return !!c.warmth; } },

    { stage: 'talks', key: 'interests', nav: 'Interests',
      title: 'What does she love talking about?',
      sub: 'Topics she steers back to naturally, so conversations never run dry.',
      pick: function () { return fields(['f-interests']); },
      done: function (c) { return (c.interests || '').trim().length > 2; } },

    { stage: 'talks', key: 'pacing', nav: 'Reply speed',
      title: 'How fast does she reply?',
      sub: 'The pause before she starts typing, and how long the typing bubble runs. Slow feels like a real person with a life — instant feels like a bot.',
      pick: function () { return fields(['f-reply-speed', 'f-typing-speed']); },
      done: function (c) { return !!c.reply_speed; } },

    { stage: 'earns', key: 'flirt', nav: 'Flirting pace',
      title: 'How fast does she flirt?',
      sub: 'A slow burn keeps fans chatting for weeks. Instant converts faster but burns out sooner.',
      pick: function () { return fields(['f-flirt']); },
      done: function (c) { return !!c.flirt_pace; } },

    { stage: 'earns', key: 'nsfw', nav: 'Content level',
      title: 'How far does she go?',
      sub: 'Sets the ceiling on what she\'ll say. She works up to it gradually — she never opens at the top end.',
      pick: function () { return fields(['f-nsfw']).concat(byId('nsfw-level-field') ? [byId('nsfw-level-field')] : []); },
      // Off is a legitimate answer, so there is no value that proves a choice —
      // seeing the step is what counts as answering it.
      done: function () { return visited('nsfw'); } },

    { stage: 'earns', key: 'convert', nav: 'Your link',
      title: 'Where do you want fans to end up?',
      sub: 'Your subscription or PPV link, and the moments she uses to bring it up in character.',
      pick: function () { return fields(['f-triggers', 'f-cta-url', 'f-cta-label', 'f-spicy-cta']); },
      done: function () { return !!(val('f-cta-url') || '').trim(); } },
  ];

  // The tour explains the frame the wizard puts people inside — what the step
  // panel is, where progress lives, and that the rail on the right is the
  // persona talking. It runs once per creator, not once per persona.
  var TOUR = [
    { anchor: '.ob-stepwrap', placement: 'right',
      title: 'One thing at a time',
      body: 'Setup is split into a handful of short steps. Each one asks a single question about your persona — answer it and continue. Nothing here is permanent; you can change any of it later.' },

    { anchor: '#ob-step-body', placement: 'right',
      title: 'This is the actual setting',
      body: 'Whatever appears in this box is a real field on your persona. Fill it in and it saves automatically when you hit Continue.' },

    { anchor: '.ob-rail', placement: 'right',
      title: 'Where you are',
      body: 'Four stages. The bar shows how far along you are, and you can jump back to any step you have already passed by clicking it.' },

    { anchor: '.preview-panel', placement: 'left',
      title: 'She updates as you type',
      body: 'This is your persona speaking, rebuilt from your answers as you make them. Watch it change when you adjust her personality or warmth — it is the fastest way to tell whether she sounds right.' },

    { anchor: '.ob-foot', placement: 'top',
      title: 'Continue, or skip ahead',
      body: 'Continue saves the step and moves on. If you would rather see every setting at once, "Skip setup" hands you the full builder — and you can come back to this guide any time.' },
  ];

  var state = { slug: null, idx: 0, cfg: {}, on: false };
  var tour  = { idx: 0, on: false, nodes: null, raf: 0 };

  function byId(id) { return document.getElementById(id); }
  function val(id) { var e = byId(id); return e ? e.value : ''; }

  // Progress lives on the user row, served by /api/me and mirrored here so the
  // rail can re-render without a round trip. Writes go straight to the server —
  // a creator who switches browsers picks up where they left off.
  function setupMap() {
    if (!window.USER_SETUP) window.USER_SETUP = {};
    return window.USER_SETUP;
  }
  function entry(slug) {
    var m = setupMap();
    if (!m[slug]) m[slug] = {};
    return m[slug];
  }
  function seen() { return entry(state.slug).seen || []; }
  function visited(key) { return seen().indexOf(key) !== -1; }

  function pushSetup(patch) {
    if (!state.slug) return Promise.resolve();
    return fetch('/api/me/setup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ slug: state.slug }, patch))
    }).catch(function () {});
  }

  function markVisited(key) {
    if (visited(key)) return Promise.resolve();
    var e = entry(state.slug);
    e.seen = seen().concat([key]);
    return pushSetup({ seen: e.seen });
  }

  function markDone(done) {
    entry(state.slug).done = done;
    return pushSetup({ done: done });
  }

  // The label, input and hint all live in one .field wrapper — move that, not
  // the bare input, so the step keeps its explanatory copy.
  function fields(ids) {
    var out = [];
    ids.forEach(function (id) {
      var el = byId(id);
      var wrap = el && (el.closest('.field') || el.closest('.toggle-row'));
      if (wrap && out.indexOf(wrap) === -1) out.push(wrap);
    });
    return out;
  }
  function section(innerId) {
    var el = byId(innerId);
    var s = el && el.closest('.form-section');
    return s ? [s] : [];
  }

  function stash() { return byId('ob-stash'); }

  function progress() {
    var done = 0;
    STEPS.forEach(function (s) { if (safeDone(s)) done++; });
    return Math.round(done / STEPS.length * 100);
  }
  function safeDone(step) {
    try { return !!step.done(state.cfg); } catch (e) { return false; }
  }

  // Only the stage being worked on reads as active — otherwise every stage with
  // one defaulted field lights up and the rail stops telling you where you are.
  function stageState(key) {
    var steps = STEPS.filter(function (s) { return s.stage === key; });
    if (!steps.length) return 'todo';
    if (steps.every(safeDone)) return 'done';
    var cur = STEPS[state.idx];
    return (cur && cur.stage === key) ? 'now' : 'todo';
  }

  function railHtml() {
    return STAGES.map(function (st, i) {
      var stt = st.key === 'live' ? (progress() === 100 ? 'now' : 'todo') : stageState(st.key);
      var steps = STEPS.filter(function (s) { return s.stage === st.key; });
      var open = steps.some(function (s) { return s === STEPS[state.idx]; });
      var num = stt === 'done' ? '✓' : (i + 1);
      return '<div class="ob-rstage">' +
        '<div class="ob-rstage-t' + (open ? ' on' : '') + '">' +
          '<span class="ob-rnum ' + stt + '">' + num + '</span>' + esc(st.label) + '</div>' +
        (open ? '<div class="ob-rsteps">' + steps.map(function (s) {
            var gi = STEPS.indexOf(s);
            var cls = gi === state.idx ? 'on' : (safeDone(s) ? 'done' : '');
            return '<div class="ob-rstep ' + cls + '" onclick="Onboarding.goto(' + gi + ')">' +
              (safeDone(s) && gi !== state.idx ? '<span class="ob-rcheck">✓</span>'
                                               : '<span class="ob-rdot"></span>') +
              esc(s.nav) + '</div>';
          }).join('') + '</div>' : '') +
        '</div>';
    }).join('');
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function render() {
    document.body.classList.remove('browsing', 'embedding');
    var step = STEPS[state.idx];
    var stage = STAGES.filter(function (s) { return s.key === step.stage; })[0];
    var inStage = STEPS.filter(function (s) { return s.stage === step.stage; });
    var pos = inStage.indexOf(step) + 1;
    var pct = progress();

    var fa = byId('form-area');
    fa.innerHTML =
      '<div class="ob-split">' +
        '<div class="ob-rail">' +
          '<div class="ob-rail-h">Your setup</div>' +
          '<div class="ob-rail-p">' + pct + '% done</div>' +
          '<div class="ob-mini"><i style="width:' + pct + '%"></i></div>' +
          railHtml() +
        '</div>' +
        '<div class="ob-stepwrap">' +
          '<div class="ob-crumb">Stage ' + (STAGES.indexOf(stage) + 1) + ' · Step ' + pos + ' of ' + inStage.length + '</div>' +
          '<h2 class="ob-step-h">' + esc(step.title) + '</h2>' +
          '<p class="ob-step-s">' + esc(step.sub) + '</p>' +
          '<div class="ob-card" id="ob-step-body"></div>' +
          '<div class="ob-foot" id="ob-foot">' +
            (state.idx > 0 ? '<button class="btn btn-ghost" onclick="Onboarding.back()">← Back</button>' : '') +
            '<button class="btn btn-primary" onclick="Onboarding.next()">' +
              (state.idx === STEPS.length - 1 ? 'Finish ✓' : 'Continue →') + '</button>' +
            '<span class="ob-saved" id="ob-saved"></span>' +
            '<div class="ob-alt">' +
              '<button class="ob-skip" type="button" onclick="Onboarding.tourReplay()">' +
                '<span class="ob-skip-icon" aria-hidden="true">◎</span>Show me around</button>' +
              '<button class="ob-skip" type="button" onclick="Onboarding.exit()">' +
                '<span class="ob-skip-icon" aria-hidden="true">⚙</span>Skip setup</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';

    var body = byId('ob-step-body');
    step.pick().forEach(function (n) { body.appendChild(n); });
    if (!body.children.length) {
      body.innerHTML = '<p class="hint">Nothing to fill in here — continue.</p>';
    }

    // The persistent live-chat panel on the right (renderChatPreview, in
    // dashboard.html) already shows how these fields sound — no separate
    // inline preview needed here. Just make sure it reflects this step's
    // fields as they were moved into the wizard frame.
    if (typeof renderChatPreview === 'function') renderChatPreview();
  }

  // Abandon the wizard without touching progress or the form it is holding —
  // used when the creator moves on to another persona mid-flow.
  function teardown() {
    Onboarding.tourEnd(true);
    unmount();
    var hold = stash();
    if (hold) hold.remove();
    document.body.classList.remove('ob-running');
    state.on = false;
  }

  // Park the current step's nodes back in the stash so they survive navigation.
  function unmount() {
    var body = byId('ob-step-body');
    if (!body) return;
    while (body.firstChild) stash().appendChild(body.firstChild);
  }

  async function saveQuiet() {
    if (!state.slug || typeof collectConfig !== 'function') return;
    state.cfg = collectConfig();
    var note = byId('ob-saved');
    try {
      // Photos live outside the config blob and save through their own endpoint,
      // so posting collectConfig() alone silently dropped everything the photo
      // step generated.
      if (typeof saveGallery === 'function') await saveGallery(state.slug);
      await fetch('/api/personas/' + state.slug, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state.cfg)
      });
      refreshSidebarEntry();
      if (note) note.textContent = 'Saved';
    } catch (e) {
      if (note) note.textContent = 'Not saved — check your connection';
    }
  }

  // The sidebar renders from the `personas` map, not from the form, so its name
  // and thumbnail stay stale until that map is told what was just saved.
  function refreshSidebarEntry() {
    if (typeof personas !== 'object' || !personas || !personas[state.slug]) return;
    personas[state.slug].name = state.cfg.name || state.slug;
    personas[state.slug].avatar = state.cfg.avatar || '';
    if (typeof renderSidebar === 'function') renderSidebar();
  }

  // ── Guided tour ───────────────────────────────────────────────────────────
  // The spotlight is four solid rectangles around the anchor rather than one
  // element with a huge spread shadow: the gap is a real hole, so the
  // highlighted control stays clickable and nothing breaks inside a scroll
  // container.
  function tourBuild() {
    if (tour.nodes) return tour.nodes;
    var root = document.createElement('div');
    root.className = 'obt-root';
    root.innerHTML =
      '<div class="obt-mask obt-t"></div><div class="obt-mask obt-r"></div>' +
      '<div class="obt-mask obt-b"></div><div class="obt-mask obt-l"></div>' +
      '<div class="obt-ring"></div>' +
      '<div class="obt-dialog" role="dialog" aria-modal="true" aria-labelledby="obt-title">' +
        '<button class="obt-close" type="button" aria-label="Close guide">&times;</button>' +
        '<h3 class="obt-title" id="obt-title"></h3>' +
        '<p class="obt-body"></p>' +
        '<div class="obt-foot">' +
          '<span class="obt-count"></span>' +
          '<button class="btn btn-ghost obt-back" type="button">Back</button>' +
          '<button class="btn btn-primary obt-next" type="button">Next</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(root);

    root.querySelector('.obt-close').onclick = function () { Onboarding.tourEnd(true); };
    root.querySelector('.obt-back').onclick  = function () { tourGo(tour.idx - 1); };
    root.querySelector('.obt-next').onclick  = function () { tourGo(tour.idx + 1); };
    // Clicking the dimmed area is the usual way out of an overlay.
    ['obt-t', 'obt-r', 'obt-b', 'obt-l'].forEach(function (c) {
      root.querySelector('.' + c).onclick = function () { Onboarding.tourEnd(true); };
    });

    tour.nodes = {
      root: root,
      t: root.querySelector('.obt-t'), r: root.querySelector('.obt-r'),
      b: root.querySelector('.obt-b'), l: root.querySelector('.obt-l'),
      ring: root.querySelector('.obt-ring'),
      dialog: root.querySelector('.obt-dialog'),
      title: root.querySelector('.obt-title'),
      bodyEl: root.querySelector('.obt-body'),
      count: root.querySelector('.obt-count'),
      back: root.querySelector('.obt-back'),
      next: root.querySelector('.obt-next'),
    };
    return tour.nodes;
  }

  function tourPosition() {
    if (!tour.on) return;
    var n = tour.nodes, step = TOUR[tour.idx];
    var el = document.querySelector(step.anchor);
    var vw = window.innerWidth, vh = window.innerHeight;
    var pad = 8;

    // No usable anchor — either it is missing (a narrow viewport hides the rail)
    // or the viewport is too small for a coach-mark to point at anything without
    // covering it. Both dim the whole screen; the dialog docks as a sheet via CSS.
    if (!el || !el.getBoundingClientRect().width || vw <= 700) {
      n.root.classList.add('obt-nospot');
      n.t.style.cssText = 'top:0;left:0;width:100%;height:100%';
      n.r.style.cssText = n.b.style.cssText = n.l.style.cssText = 'display:none';
      if (vw <= 700) {
        // The bottom-sheet rule owns placement here (it uses !important, so any
        // inline position would be ignored anyway).
        n.dialog.style.cssText = '';
      } else {
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

    // Place the dialog on the requested side, then clamp it into the viewport.
    n.dialog.style.transform = '';
    var dw = n.dialog.offsetWidth, dh = n.dialog.offsetHeight, gap = 14;
    var dx, dy;
    if (step.placement === 'left')       { dx = x - dw - gap;  dy = y; }
    else if (step.placement === 'top')   { dx = x;             dy = y - dh - gap; }
    else if (step.placement === 'bottom'){ dx = x;             dy = y + h + gap; }
    else                                 { dx = x + w + gap;   dy = y; }

    // If the preferred side has no room, flip to the opposite one.
    if (dx + dw > vw - 12) dx = x - dw - gap;
    if (dx < 12)           dx = Math.min(x + w + gap, vw - dw - 12);
    if (dy + dh > vh - 12) dy = y - dh - gap;

    n.dialog.style.left = Math.max(12, Math.min(dx, vw - dw - 12)) + 'px';
    n.dialog.style.top  = Math.max(12, Math.min(dy, vh - dh - 12)) + 'px';
  }

  function tourSchedule() {
    if (tour.raf) return;
    tour.raf = requestAnimationFrame(function () { tour.raf = 0; tourPosition(); });
  }

  function tourGo(i) {
    if (i < 0) return;
    if (i >= TOUR.length) return Onboarding.tourEnd(false);
    tour.idx = i;
    var n = tourBuild(), step = TOUR[i];
    n.title.textContent = step.title;
    n.bodyEl.textContent = step.body;
    n.count.textContent = (i + 1) + ' of ' + TOUR.length;
    n.back.style.visibility = i === 0 ? 'hidden' : 'visible';
    n.next.textContent = i === TOUR.length - 1 ? 'Got it' : 'Next';
    tourPosition();
    n.next.focus();
  }

  function tourKey(e) {
    if (!tour.on) return;
    if (e.key === 'Escape')     { e.preventDefault(); Onboarding.tourEnd(true); }
    else if (e.key === 'ArrowRight') tourGo(tour.idx + 1);
    else if (e.key === 'ArrowLeft')  tourGo(tour.idx - 1);
  }

  var Onboarding = {

    // Non-admins get the wizard; admins keep the full form.
    active: function () {
      return !document.body.classList.contains('is-admin');
    },

    tourSeen: function () {
      var m = (window.USER_SETUP || {})[state.slug];
      return !!(m && m.tour);
    },

    tourStart: function (force) {
      if (tour.on || !state.on) return;
      if (!force && this.tourSeen()) return;
      tour.on = true;
      tour.idx = 0;
      tourBuild().root.classList.add('on');
      document.body.classList.add('obt-open');
      window.addEventListener('resize', tourSchedule);
      window.addEventListener('scroll', tourSchedule, true);
      document.addEventListener('keydown', tourKey);
      tourGo(0);
    },

    // Skipped and finished both mean "do not show me this again" — the flag
    // records which so the wizard footer can offer the right wording.
    tourEnd: function (skipped) {
      if (!tour.on) return;
      tour.on = false;
      if (tour.nodes) tour.nodes.root.classList.remove('on');
      document.body.classList.remove('obt-open');
      window.removeEventListener('resize', tourSchedule);
      window.removeEventListener('scroll', tourSchedule, true);
      document.removeEventListener('keydown', tourKey);
      entry(state.slug).tour = skipped ? 'skipped' : 'done';
      pushSetup({ tour: entry(state.slug).tour });
    },

    tourReplay: function () { this.tourStart(true); },

    isComplete: function (slug) {
      var m = (window.USER_SETUP || {})[slug];
      return !!(m && m.done);
    },

    // Called at the end of renderForm(). Everything the wizard shows is already
    // in the DOM at this point.
    onFormRendered: function (slug, cfg) {
      if (state.on) {
        if (slug === state.slug) { state.cfg = cfg || state.cfg; return; }
        // A different persona is being opened while the previous wizard (or its
        // done card) is still mounted — drop it so the new one gets the guide.
        teardown();
      }
      if (!this.active() || this.isComplete(slug)) return;
      if (document.body.classList.contains('ps-running')) return;
      state.slug = slug;
      state.cfg = cfg || {};
      state.on = true;

      var fa = byId('form-area');
      var stale = byId('ob-stash');
      if (stale) stale.remove();
      var hold = document.createElement('div');
      hold.id = 'ob-stash';
      hold.style.display = 'none';
      while (fa.firstChild) hold.appendChild(fa.firstChild);
      document.body.appendChild(hold);

      var bar = byId('action-bar');
      if (bar) bar.style.display = 'none';
      document.body.classList.add('ob-running');

      state.idx = this.firstUnfinished();
      render();
      var self = this;
      requestAnimationFrame(function () { self.tourStart(false); });
    },

    firstUnfinished: function () {
      for (var i = 0; i < STEPS.length; i++) if (!safeDone(STEPS[i])) return i;
      return 0;
    },

    goto: function (i) {
      if (i === state.idx) return;
      unmount();
      state.idx = Math.max(0, Math.min(i, STEPS.length - 1));
      render();
    },

    back: function () { this.goto(state.idx - 1); },

    next: async function () {
      await markVisited(STEPS[state.idx].key);
      await saveQuiet();
      if (state.idx === STEPS.length - 1) return this.finish();
      unmount();
      state.idx++;
      render();
    },

    finish: async function () {
      await saveQuiet();
      await markDone(true);
      var name = (state.cfg.name || state.slug);
      unmount();
      // The done card is an ordinary page, not a wizard frame: leaving
      // ob-running on the body kept .form-area at overflow:hidden and killed
      // scrolling here and on the full builder the creator lands on next.
      document.body.classList.remove('ob-running');
      byId('form-area').innerHTML =
        '<div class="ob-done">' +
          '<div class="ob-seal">✓</div>' +
          '<h2 class="ob-done-h">' + esc(name) + ' is ready.</h2>' +
          '<p class="ob-done-s">Her personality, voice and funnel are set. Connect a platform and she\'ll start answering fans on her own.</p>' +
          '<div class="ob-nxt">' +
            '<a class="ob-nxt-c" href="#" onclick="PlatformSetup.open(\'telegram\',\'' +
                esc(state.slug) + '\');return false;"><div class="ob-nxt-i">💬</div>' +
              '<div class="ob-nxt-t">Connect Telegram</div>' +
              '<div class="ob-nxt-d">Point a Telegram account at her so she replies to real fans.</div></a>' +
            '<a class="ob-nxt-c" href="#" onclick="PlatformSetup.hub();return false;"><div class="ob-nxt-i">💎</div>' +
              '<div class="ob-nxt-t">See all platforms</div>' +
              '<div class="ob-nxt-d">Every model against every platform she can work on.</div></a>' +
            '<a class="ob-nxt-c" href="#" onclick="Onboarding.exit();return false;"><div class="ob-nxt-i">⚙️</div>' +
              '<div class="ob-nxt-t">Fine-tune her</div>' +
              '<div class="ob-nxt-d">Open the full builder to adjust anything you set up here.</div></a>' +
          '</div>' +
        '</div>';
    },

    // Drop the wizard and hand back the ordinary builder.
    exit: function (keepProgress) {
      if (!state.on) return;
      this.tourEnd(true);
      unmount();
      var hold = stash();
      var fa = byId('form-area');
      fa.innerHTML = '';
      if (hold) {
        while (hold.firstChild) fa.appendChild(hold.firstChild);
        hold.remove();
      }
      var bar = byId('action-bar');
      if (bar) bar.style.display = 'flex';
      document.body.classList.remove('ob-running');
      state.on = false;
      // The platform wizard takes over #form-area the same way; when it is the
      // one calling, backing out of the builder must not count as finishing it.
      if (!keepProgress) markDone(true);
    },

    // Let a creator run the guided flow again from the full builder.
    restart: function (slug) {
      state.slug = slug;
      markDone(false).then(function () {
        if (typeof loadPersona === 'function') loadPersona(slug);
      });
    },
  };

  window.Onboarding = Onboarding;
})();
