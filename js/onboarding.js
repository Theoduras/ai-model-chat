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
      pick: function () { return fields(['f-speech']); },
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
      pick: function () { return fields(['f-triggers', 'f-cta-url', 'f-cta-label']); },
      done: function () { return !!(val('f-cta-url') || '').trim(); } },
  ];

  var state = { slug: null, idx: 0, cfg: {}, on: false };

  function byId(id) { return document.getElementById(id); }
  function val(id) { var e = byId(id); return e ? e.value : ''; }

  function visitKey() { return 'ob-seen-' + state.slug; }
  function seen() {
    try { return JSON.parse(localStorage.getItem(visitKey()) || '[]'); } catch (e) { return []; }
  }
  function visited(key) { return seen().indexOf(key) !== -1; }
  function markVisited(key) {
    var all = seen();
    if (all.indexOf(key) !== -1) return;
    all.push(key);
    try { localStorage.setItem(visitKey(), JSON.stringify(all)); } catch (e) {}
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
          '<div class="ob-foot">' +
            (state.idx > 0 ? '<button class="btn btn-ghost" onclick="Onboarding.back()">← Back</button>' : '') +
            '<button class="btn btn-primary" onclick="Onboarding.next()">' +
              (state.idx === STEPS.length - 1 ? 'Finish ✓' : 'Continue →') + '</button>' +
            '<span class="ob-saved" id="ob-saved"></span>' +
            '<button class="ob-skip" onclick="Onboarding.exit()">Skip setup — show me everything</button>' +
          '</div>' +
        '</div>' +
      '</div>';

    var body = byId('ob-step-body');
    step.pick().forEach(function (n) { body.appendChild(n); });
    if (!body.children.length) {
      body.innerHTML = '<p class="hint">Nothing to fill in here — continue.</p>';
    }
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
      await fetch('/api/personas/' + state.slug, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state.cfg)
      });
      if (note) note.textContent = 'Saved';
    } catch (e) {
      if (note) note.textContent = 'Not saved — check your connection';
    }
  }

  var Onboarding = {

    // Non-admins get the wizard; admins keep the full form.
    active: function () {
      return !document.body.classList.contains('is-admin');
    },

    isComplete: function (slug) {
      try { return localStorage.getItem('ob-done-' + slug) === '1'; } catch (e) { return false; }
    },

    // Called at the end of renderForm(). Everything the wizard shows is already
    // in the DOM at this point.
    onFormRendered: function (slug, cfg) {
      if (!this.active() || this.isComplete(slug)) return;
      state.slug = slug;
      state.cfg = cfg || {};
      state.on = true;

      var fa = byId('form-area');
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
      markVisited(STEPS[state.idx].key);
      await saveQuiet();
      if (state.idx === STEPS.length - 1) return this.finish();
      unmount();
      state.idx++;
      render();
    },

    finish: async function () {
      await saveQuiet();
      try { localStorage.setItem('ob-done-' + state.slug, '1'); } catch (e) {}
      var name = (state.cfg.name || state.slug);
      unmount();
      byId('form-area').innerHTML =
        '<div class="ob-done">' +
          '<div class="ob-seal">✓</div>' +
          '<h2 class="ob-done-h">' + esc(name) + ' is ready.</h2>' +
          '<p class="ob-done-s">Her personality, voice and funnel are set. Connect a platform and she\'ll start answering fans on her own.</p>' +
          '<div class="ob-nxt">' +
            '<a class="ob-nxt-c" href="/telegram"><div class="ob-nxt-i">💬</div>' +
              '<div class="ob-nxt-t">Connect Telegram</div>' +
              '<div class="ob-nxt-d">Point a Telegram account at her so she replies to real fans.</div></a>' +
            '<a class="ob-nxt-c" href="/fanvue"><div class="ob-nxt-i">💎</div>' +
              '<div class="ob-nxt-t">Set up Fanvue</div>' +
              '<div class="ob-nxt-d">Load the photo sets she offers and what each costs to unlock.</div></a>' +
            '<a class="ob-nxt-c" href="#" onclick="Onboarding.exit();return false;"><div class="ob-nxt-i">⚙️</div>' +
              '<div class="ob-nxt-t">Fine-tune her</div>' +
              '<div class="ob-nxt-d">Open the full builder to adjust anything you set up here.</div></a>' +
          '</div>' +
        '</div>';
    },

    // Drop the wizard and hand back the ordinary builder.
    exit: function () {
      if (!state.on) return;
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
      try { localStorage.setItem('ob-done-' + state.slug, '1'); } catch (e) {}
    },

    // Let a creator run the guided flow again from the full builder.
    restart: function (slug) {
      try { localStorage.removeItem('ob-done-' + slug); } catch (e) {}
      if (typeof loadPersona === 'function') loadPersona(slug);
    },
  };

  window.Onboarding = Onboarding;
})();
