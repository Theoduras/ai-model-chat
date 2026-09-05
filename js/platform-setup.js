// Guided platform connection for creators — the counterpart to onboarding.js,
// which walks them through building the persona itself.
//
// Deliberate difference from Onboarding: that wizard *moves* the builder's real
// inputs into a one-step frame, because collectConfig() has to keep finding
// them. There is no pre-existing platform form to borrow (the operator consoles
// are separate documents loaded in an iframe), so this wizard renders its own
// markup and posts to the platform APIs step by step. It reuses Onboarding's
// CSS shell — .ob-split, .ob-rail, .ob-stepwrap, .ob-card, .ob-foot, .ob-done.
//
// Both wizards own #form-area, so open() always tears the other one down first.
(function () {

  var PLATFORMS = {
    telegram: { label: 'Telegram', icon: '💬', ready: true },
    x:        { label: '𝕏',        icon: '𝕏',  ready: false },
    fanvue:   { label: 'Fanvue',   icon: '💎', ready: false },
    threads:  { label: 'Threads',  icon: '@',  ready: false },
  };

  var STAGES = [
    { key: 'choose',  label: 'Choose' },
    { key: 'connect', label: 'Connect' },
    { key: 'live',    label: 'Go live' },
  ];

  var TELEGRAM_STEPS = [
    { stage: 'choose', key: 'persona', nav: 'Which model',
      title: 'Which model is this for?',
      sub: 'Each model gets her own Telegram account. Connecting one never touches the others.',
      render: renderPersonaStep,
      done: function (c) { return !!c.slug; } },

    { stage: 'choose', key: 'mode', nav: 'Account type',
      title: 'How should she appear on Telegram?',
      sub: 'A bot is labelled as a bot and can only answer people who tapped Start first. A personal account has neither limit — she looks like a person and can message fans first.',
      render: renderModeStep,
      done: function () { return visited('mode'); } },

    { stage: 'connect', key: 'connect', nav: 'Connect',
      title: 'Connect her account',
      sub: 'One model holds one Telegram account at a time — connecting again replaces the current one.',
      render: renderConnectStep,
      done: function (c) { return !!(c.status && c.status.connected); } },

    { stage: 'live', key: 'share', nav: 'Go live',
      title: function () {
        return state.mode === 'user' ? 'She\'s live' : 'Put this link in her bio';
      },
      sub: function () {
        return state.mode === 'user'
          ? 'Her account is signed in and answering. Nothing else to set up.'
          : 'Anyone who opens it lands in a chat with her. You can also set the button she shows fans when the conversation is warm.';
      },
      render: renderShareStep,
      // Seeing this step is not enough: a stored visit from an earlier attempt
      // must not show a disconnected model as finished.
      done: function (c) { return visited('share') && !!(c.status && c.status.connected); } },
  ];

  var state = { platform: 'telegram', slug: null, mode: 'hosted',
                idx: 0, on: false, status: null, personas: [], overview: {},
                platformReady: false, userReady: false, busy: false, err: '',
                userStage: 'phone', userPhone: '' };

  function byId(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function steps() { return TELEGRAM_STEPS; }
  // Step copy is a string, or a function of the current state where the wording
  // depends on the mode chosen earlier.
  function text(v) { return typeof v === 'function' ? v() : v; }
  function ctx() { return { slug: state.slug, status: state.status, mode: state.mode }; }

  // Progress is namespaced per platform *and* persona, so "tg:lilith" sits
  // beside the persona wizard's own "lilith" key in the same setup blob.
  function setupKey() { return state.platform + ':' + (state.slug || '-'); }
  function entry() {
    if (!window.USER_SETUP) window.USER_SETUP = {};
    var m = window.USER_SETUP;
    if (!m[setupKey()]) m[setupKey()] = {};
    return m[setupKey()];
  }
  function seen() { return entry().seen || []; }
  function visited(key) { return seen().indexOf(key) !== -1; }
  function markVisited(key) {
    if (!state.slug || visited(key)) return Promise.resolve();
    var e = entry();
    e.seen = seen().concat([key]);
    return fetch('/api/me/setup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slug: setupKey(), seen: e.seen })
    }).catch(function () {});
  }

  function safeDone(step) {
    try { return !!step.done(ctx()); } catch (e) { return false; }
  }
  function progress() {
    var all = steps(), n = 0;
    all.forEach(function (s) { if (safeDone(s)) n++; });
    return Math.round(n / all.length * 100);
  }
  function stageState(key) {
    var inStage = steps().filter(function (s) { return s.stage === key; });
    if (!inStage.length) return 'todo';
    if (inStage.every(safeDone)) return 'done';
    var cur = steps()[state.idx];
    return (cur && cur.stage === key) ? 'now' : 'todo';
  }

  function railHtml() {
    var all = steps();
    return STAGES.map(function (st, i) {
      var stt = stageState(st.key);
      var inStage = all.filter(function (s) { return s.stage === st.key; });
      var open = inStage.some(function (s) { return s === all[state.idx]; });
      var num = stt === 'done' ? '✓' : (i + 1);
      return '<div class="ob-rstage">' +
        '<div class="ob-rstage-t' + (open ? ' on' : '') + '">' +
          '<span class="ob-rnum ' + stt + '">' + num + '</span>' + esc(st.label) + '</div>' +
        (open ? '<div class="ob-rsteps">' + inStage.map(function (s) {
            var gi = all.indexOf(s);
            var cls = gi === state.idx ? 'on' : (safeDone(s) ? 'done' : '');
            return '<div class="ob-rstep ' + cls + '" onclick="PlatformSetup.goto(' + gi + ')">' +
              (safeDone(s) && gi !== state.idx ? '<span class="ob-rcheck">✓</span>'
                                               : '<span class="ob-rdot"></span>') +
              esc(s.nav) + '</div>';
          }).join('') + '</div>' : '') +
        '</div>';
    }).join('');
  }

  // ---- steps -------------------------------------------------------------

  function renderPersonaStep() {
    if (!state.personas.length) {
      return '<p class="hint">You have no models yet. Build one first, then come back to connect her.</p>';
    }
    return '<div class="ps-picks">' + state.personas.map(function (p) {
      var conn = ((state.overview[p.slug] || {})[state.platform] || {}).connected;
      return '<label class="ps-pick' + (p.slug === state.slug ? ' on' : '') + '">' +
        '<input type="radio" name="ps-persona" value="' + esc(p.slug) + '"' +
          (p.slug === state.slug ? ' checked' : '') +
          ' onchange="PlatformSetup.setPersona(this.value)">' +
        '<span class="ps-pick-t">' + esc(p.name || p.slug) + '</span>' +
        '<span class="ps-pick-d">' + (conn ? 'Already connected — reconnecting replaces it' : 'Not connected yet') + '</span>' +
      '</label>';
    }).join('') + '</div>';
  }

  function renderModeStep() {
    var opts = [
      ['hosted', 'Our bot', 'Zero setup. She answers on our shared Telegram bot under her own link.'],
      ['own', 'My own bot', 'Your bot name and avatar. You need a token from @BotFather on Telegram.'],
      ['user', 'Her own personal account',
       'A real Telegram account, not a bot. No "bot" label, and she can open the '
       + 'conversation herself. Sign in with her phone number and the code Telegram texts.'],
    ];
    return '<div class="ps-picks">' + opts.map(function (o) {
      // The personal account runs on the operator's registered Telegram app, so
      // it is offered but not selectable until they have set one up. Showing it
      // greyed out beats hiding a mode the creator may be looking for.
      var off = o[0] === 'user' && !state.userReady;
      return '<label class="ps-pick' + (state.mode === o[0] ? ' on' : '') +
          (off ? ' off' : '') + '">' +
        '<input type="radio" name="ps-mode" value="' + o[0] + '"' +
          (state.mode === o[0] ? ' checked' : '') + (off ? ' disabled' : '') +
          ' onchange="PlatformSetup.setMode(this.value)">' +
        '<span class="ps-pick-t">' + esc(o[1]) + '</span>' +
        '<span class="ps-pick-d">' + esc(o[2]) +
          (off ? ' — not switched on for this workspace yet; ask your operator.' : '') +
        '</span>' +
      '</label>';
    }).join('') + '</div>';
  }

  function renderConnectStep() {
    var s = state.status || {};
    var head = s.connected
      ? '<p class="ps-ok">✓ Connected' + (s.username ? ' as @' + esc(s.username) : '') + '.</p>'
      : '';
    if (state.mode === 'hosted') {
      if (!state.platformReady) {
        return head + '<p class="hint">Telegram is not switched on for this workspace yet. ' +
          'Ask your operator to set up the shared bot, then come back — nothing is needed from you.</p>';
      }
      return head +
        '<p class="hint">One click. We mint her own code on the shared bot and hand back her link.</p>' +
        '<div class="ob-foot" style="border:0;padding:0;margin-top:12px;">' +
          '<button class="btn btn-primary" onclick="PlatformSetup.connectHosted()"' +
            (state.busy ? ' disabled' : '') + '>' +
            (state.busy ? 'Connecting…' : (s.connected ? 'Reconnect' : 'Connect her now')) + '</button>' +
          (s.connected ? '<button class="btn btn-ghost" onclick="PlatformSetup.disconnect()">Disconnect</button>' : '') +
        '</div>' + errHtml();
    }
    if (state.mode === 'user') return head + renderUserConnect();
    return head +
      '<div class="field"><label>Bot token</label>' +
        '<input type="password" id="ps-token" placeholder="123456:ABC-DEF…" autocomplete="off">' +
        '<p class="hint">Open @BotFather in Telegram, send /newbot, and paste the token it gives you.</p></div>' +
      '<div class="ob-foot" style="border:0;padding:0;margin-top:12px;">' +
        '<button class="btn btn-primary" onclick="PlatformSetup.connectOwn()"' +
          (state.busy ? ' disabled' : '') + '>' +
          (state.busy ? 'Connecting…' : 'Connect this bot') + '</button>' +
        (s.connected ? '<button class="btn btn-ghost" onclick="PlatformSetup.disconnect()">Disconnect</button>' : '') +
      '</div>' + errHtml();
  }

  // Personal-account sign-in is a three-beat flow inside one step: phone, then
  // the code Telegram texts, then the 2FA password only if the account has one.
  function renderUserConnect() {
    var foot = function (label, fn) {
      return '<div class="ob-foot" style="border:0;padding:0;margin-top:12px;">' +
        '<button class="btn btn-primary" onclick="PlatformSetup.' + fn + '()"' +
          (state.busy ? ' disabled' : '') + '>' +
          (state.busy ? 'Working…' : label) + '</button>' +
        (state.userStage !== 'phone'
          ? '<button class="btn btn-ghost" onclick="PlatformSetup.userRestart()">Start over</button>'
          : '') +
        '</div>' + errHtml();
    };
    if (state.userStage === 'code') {
      return '<p class="hint">Telegram just texted ' + esc(state.userPhone) +
          '. Enter the code — it arrives in the Telegram app itself if she is already signed in there.</p>' +
        '<div class="field"><label>Login code</label>' +
          '<input type="text" id="ps-code" inputmode="numeric" autocomplete="one-time-code" placeholder="12345"></div>' +
        foot('Sign in', 'userSignIn');
    }
    if (state.userStage === 'password') {
      return '<p class="hint">This account has two-step verification. Enter its password to finish.</p>' +
        '<div class="field"><label>Two-step password</label>' +
          '<input type="password" id="ps-2fa" autocomplete="current-password"></div>' +
        foot('Finish sign-in', 'userSignIn');
    }
    return '<p class="hint">Use the number the account is registered to, in full international ' +
        'format. She stays signed in afterwards — you will not be asked again.</p>' +
      '<div class="field"><label>Phone number</label>' +
        '<input type="tel" id="ps-phone" placeholder="+31 6 12345678" value="' + esc(state.userPhone) + '"></div>' +
      foot('Text her a code', 'userSendCode');
  }

  function renderShareStep() {
    var s = state.status || {};
    if (!s.connected) {
      return '<p class="hint">Connect her account first and her link appears here.</p>';
    }
    if (state.mode === 'user') {
      return '<p class="ps-ok">✓ Signed in as ' +
          esc(s.username ? '@' + s.username : (s.first_name || 'her account')) + '.</p>' +
        '<p class="hint">She is live on her own account — fans message her like any other ' +
          'person, and she can open a conversation first. Share ' +
          (s.username ? '@' + esc(s.username) : 'her username') + ' wherever you want fans to find her.</p>' +
        '<div class="ob-foot" style="border:0;padding:0;margin-top:12px;">' +
          '<button class="btn btn-ghost" onclick="PlatformSetup.disconnect()">Disconnect</button>' +
        '</div>' + errHtml();
    }
    var link = s.share_link || '';
    return '<div class="field"><label>Her Telegram link</label>' +
        '<input type="text" id="ps-link" readonly value="' + esc(link) + '">' +
        '<p class="hint">Put this in her bio, her posts, anywhere fans can tap it.</p></div>' +
      '<div class="ob-foot" style="border:0;padding:0;margin:8px 0 16px;">' +
        '<button class="btn btn-ghost" onclick="PlatformSetup.copyLink()">Copy link</button>' +
        '<span class="ob-saved" id="ps-copied"></span></div>' +
      '<div class="field"><label>Button link (optional)</label>' +
        '<input type="text" id="ps-cta-url" value="' + esc(s.cta_url || '') + '" placeholder="https://…"></div>' +
      '<div class="field"><label>Button label</label>' +
        '<input type="text" id="ps-cta-label" value="' + esc(s.cta_label || '') + '" placeholder="See more of me"></div>' +
      '<div class="ob-foot" style="border:0;padding:0;margin-top:12px;">' +
        '<button class="btn btn-primary" onclick="PlatformSetup.saveCta()">Save button</button>' +
        '<span class="ob-saved" id="ps-cta-saved"></span></div>' + errHtml();
  }

  function errHtml() {
    return state.err ? '<p class="ps-err">' + esc(state.err) + '</p>' : '';
  }

  // ---- shell -------------------------------------------------------------

  function render() {
    var all = steps();
    var step = all[state.idx];
    var stage = STAGES.filter(function (s) { return s.key === step.stage; })[0];
    var inStage = all.filter(function (s) { return s.stage === step.stage; });
    var pct = progress();
    var meta = PLATFORMS[state.platform];

    byId('form-area').innerHTML =
      '<div class="ob-split">' +
        '<div class="ob-rail">' +
          '<div class="ob-rail-h">' + esc(meta.label) + ' setup</div>' +
          '<div class="ob-rail-p">' + pct + '% done</div>' +
          '<div class="ob-mini"><i style="width:' + pct + '%"></i></div>' +
          railHtml() +
        '</div>' +
        '<div class="ob-stepwrap">' +
          '<div class="ob-crumb">Stage ' + (STAGES.indexOf(stage) + 1) +
            ' · Step ' + (inStage.indexOf(step) + 1) + ' of ' + inStage.length + '</div>' +
          '<h2 class="ob-step-h">' + esc(text(step.title)) + '</h2>' +
          '<p class="ob-step-s">' + esc(text(step.sub)) + '</p>' +
          '<div class="ob-card" id="ps-step-body">' + step.render(ctx()) + '</div>' +
          '<div class="ob-foot">' +
            (state.idx > 0 ? '<button class="btn btn-ghost" onclick="PlatformSetup.back()">← Back</button>' : '') +
            '<button class="btn btn-primary" onclick="PlatformSetup.next()">' +
              (state.idx === all.length - 1 ? 'Done ✓' : 'Continue →') + '</button>' +
            '<button class="ob-skip" onclick="PlatformSetup.hub()">All platforms</button>' +
          '</div>' +
        '</div>' +
      '</div>';
  }

  function takeOver() {
    if (window.Onboarding && typeof Onboarding.exit === 'function') Onboarding.exit(true);
    var stash = byId('ob-stash');
    // A stranded stash would silently swallow the builder's form on the next
    // render, so never leave one behind.
    if (stash) stash.remove();
    var bar = byId('action-bar');
    if (bar) bar.style.display = 'none';
    document.body.classList.add('ob-running');
    document.body.classList.add('ps-running');
    state.on = true;
  }

  async function loadOverview() {
    try {
      var r = await fetch('/api/platforms/overview');
      var d = await r.json();
      state.overview = d.personas || {};
      state.platformReady = !!d.telegram_platform_ready;
      state.userReady = !!d.telegram_user_ready;
      state.personas = Object.keys(state.overview).map(function (slug) {
        return { slug: slug, name: state.overview[slug].name || slug };
      });
    } catch (e) {
      state.overview = {}; state.personas = [];
    }
  }

  // A model is either on a bot or on a personal account. The personal account
  // wins when both exist, matching /api/platforms/overview.
  async function loadStatus() {
    state.status = null;
    if (!state.slug) return;
    var slug = state.slug;
    try {
      var ur = await fetch('/api/tguser/status');
      var ud = await ur.json();
      var acct = ud[slug];
      if (acct && acct.connected) {
        state.status = {
          connected: true, mode: 'user', username: acct.username,
          first_name: acct.first_name, running: acct.running,
          cta_url: acct.cta_url, cta_label: acct.cta_label, share_link: '',
        };
        state.mode = 'user';
        return;
      }
    } catch (e) {}
    try {
      var r = await fetch('/api/telegram/status?persona=' + encodeURIComponent(slug));
      var d = await r.json();
      state.status = d[slug] || null;
      if (state.status && state.status.mode) state.mode = state.status.mode;
    } catch (e) {}
  }

  async function refresh() {
    await loadStatus();
    render();
  }

  var PlatformSetup = {

    open: async function (platform, slug) {
      state.platform = platform || 'telegram';
      state.err = '';
      // Every step and its copy ("How should she appear on Telegram?", etc.) is
      // Telegram-specific — the only platform with a real connect flow so far.
      // Anything else falls back to the same "Coming soon" state the hub shows.
      if (!(PLATFORMS[state.platform] || {}).ready) return this.comingSoon();
      takeOver();
      byId('form-area').innerHTML = '<p class="hint" style="padding:24px;">Loading…</p>';
      await loadOverview();
      state.slug = slug || state.slug ||
        (state.personas[0] ? state.personas[0].slug : null);
      await loadStatus();
      // Unlike the persona wizard, a finished flow still opens — "done" here
      // means connected, and a creator reopens it to change or disconnect.
      state.idx = this.firstUnfinished();
      render();
    },

    comingSoon: function () {
      takeOver();
      var meta = PLATFORMS[state.platform] || { label: state.platform, icon: '' };
      byId('form-area').innerHTML =
        '<div class="ps-hub">' +
          '<h2 class="ob-step-h">' + esc(meta.icon) + ' ' + esc(meta.label) + '</h2>' +
          '<p class="ob-step-s">This platform is not connected yet — coming soon.</p>' +
          '<button type="button" class="btn btn-primary" onclick="PlatformSetup.hub()">See all platforms</button>' +
        '</div>';
    },

    firstUnfinished: function () {
      var all = steps();
      for (var i = 0; i < all.length; i++) if (!safeDone(all[i])) return i;
      return all.length - 1;
    },

    goto: function (i) {
      var all = steps();
      state.err = '';
      state.idx = Math.max(0, Math.min(i, all.length - 1));
      render();
    },

    back: function () { this.goto(state.idx - 1); },

    next: async function () {
      var all = steps();
      await markVisited(all[state.idx].key);
      if (state.idx === all.length - 1) return this.hub();
      this.goto(state.idx + 1);
    },

    setPersona: async function (slug) {
      state.slug = slug;
      state.err = '';
      await refresh();
    },

    setMode: function (mode) {
      state.mode = mode;
      state.err = '';
      render();
    },

    connectHosted: async function () {
      if (!state.slug) return;
      state.busy = true; state.err = ''; render();
      try {
        var r = await fetch('/api/telegram/hosted', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ persona: state.slug })
        });
        var d = await r.json();
        if (!r.ok || !d.ok) state.err = d.error || 'Could not connect.';
      } catch (e) { state.err = 'Network error: ' + e.message; }
      state.busy = false;
      await refresh();
      if (!state.err) this.goto(state.idx + 1);
    },

    connectOwn: async function () {
      var el = byId('ps-token');
      var token = el ? el.value.trim() : '';
      if (!token) { state.err = 'Paste the token BotFather gave you.'; return render(); }
      state.busy = true; state.err = ''; render();
      try {
        var r = await fetch('/api/telegram/connect', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ persona: state.slug, bot_token: token })
        });
        var d = await r.json();
        if (!r.ok || !d.ok) state.err = d.error || 'Could not connect that bot.';
      } catch (e) { state.err = 'Network error: ' + e.message; }
      state.busy = false;
      await refresh();
      if (!state.err) this.goto(state.idx + 1);
    },

    disconnect: async function () {
      if (!confirm('Disconnect her Telegram account?')) return;
      state.err = '';
      var user = (state.status || {}).mode === 'user';
      var url = user ? '/api/tguser/control' : '/api/telegram/disconnect';
      var body = user ? { persona: state.slug, action: 'disconnect' }
                      : { persona: state.slug };
      try {
        await fetch(url, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
      } catch (e) { state.err = 'Network error: ' + e.message; }
      state.userStage = 'phone';
      await refresh();
    },

    userSendCode: async function () {
      var el = byId('ps-phone');
      var phone = el ? el.value.trim() : '';
      if (!phone) { state.err = 'Enter her phone number first.'; return render(); }
      state.userPhone = phone;
      state.busy = true; state.err = ''; render();
      try {
        var r = await fetch('/api/tguser/send-code', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ persona: state.slug, phone: phone })
        });
        var d = await r.json();
        if (r.ok && d.ok) state.userStage = 'code';
        else state.err = d.error || 'Telegram would not send a code to that number.';
      } catch (e) { state.err = 'Network error: ' + e.message; }
      state.busy = false; render();
    },

    userSignIn: async function () {
      var code = (byId('ps-code') || {}).value || state.userCode || '';
      var pw = (byId('ps-2fa') || {}).value || '';
      if (state.userStage === 'code' && !code.trim()) {
        state.err = 'Enter the code Telegram sent.'; return render();
      }
      state.userCode = code;
      state.busy = true; state.err = ''; render();
      try {
        var r = await fetch('/api/tguser/sign-in', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ persona: state.slug, code: state.userCode, password: pw })
        });
        var d = await r.json();
        if (!r.ok || !d.ok) {
          state.err = d.error || 'Could not sign in.';
        } else if (d.needs_password) {
          state.userStage = 'password';
        } else {
          state.userStage = 'phone';
          state.userCode = '';
          state.busy = false;
          await refresh();
          return this.goto(state.idx + 1);
        }
      } catch (e) { state.err = 'Network error: ' + e.message; }
      state.busy = false; render();
    },

    userRestart: function () {
      state.userStage = 'phone'; state.userCode = ''; state.err = '';
      render();
    },

    saveCta: async function () {
      var note = byId('ps-cta-saved');
      try {
        await fetch('/api/telegram/settings', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ persona: state.slug,
            cta_url: (byId('ps-cta-url') || {}).value || '',
            cta_label: (byId('ps-cta-label') || {}).value || '' })
        });
        if (note) note.textContent = 'Saved';
      } catch (e) {
        if (note) note.textContent = 'Not saved — check your connection';
      }
    },

    copyLink: function () {
      var el = byId('ps-link');
      if (!el) return;
      el.select();
      try { document.execCommand('copy'); } catch (e) {}
      var note = byId('ps-copied');
      if (note) note.textContent = 'Copied';
    },

    // The persona × platform matrix: every model down the side, every platform
    // across the top. One model can hold one account per platform.
    hub: async function () {
      state.err = '';
      takeOver();
      byId('form-area').innerHTML = '<p class="hint" style="padding:24px;">Loading…</p>';
      await loadOverview();
      var keys = Object.keys(PLATFORMS);
      var rows = state.personas.map(function (p) {
        return '<tr><th>' + esc(p.name) + '</th>' + keys.map(function (k) {
          var meta = PLATFORMS[k];
          var st = (state.overview[p.slug] || {})[k] || {};
          if (!meta.ready) return '<td><span class="ps-chip soon">Coming soon</span></td>';
          var cls = st.connected ? 'on' : '';
          var label = st.connected
            ? 'Connected' + (st.username ? ' · @' + esc(st.username) : '')
            : 'Not connected';
          return '<td><button type="button" class="ps-chip ' + cls + '" ' +
            'onclick="PlatformSetup.open(\'' + k + '\',\'' + esc(p.slug) + '\')">' +
            label + '</button></td>';
        }).join('') + '</tr>';
      }).join('');

      byId('form-area').innerHTML =
        '<div class="ps-hub">' +
          '<h2 class="ob-step-h">Where she talks to fans</h2>' +
          '<p class="ob-step-s">Connect each model to the platforms she works on. ' +
            'One account per model per platform — connecting again replaces it.</p>' +
          (state.personas.length
            ? '<div class="ps-tablewrap"><table class="ps-table"><thead><tr><th></th>' +
                keys.map(function (k) { return '<th>' + esc(PLATFORMS[k].label) + '</th>'; }).join('') +
              '</tr></thead><tbody>' + rows + '</tbody></table></div>'
            : '<p class="hint">You have no models yet. Build one first.</p>') +
        '</div>';
    },

    close: function () {
      document.body.classList.remove('ps-running');
      document.body.classList.remove('ob-running');
      var bar = byId('action-bar');
      if (bar) bar.style.display = 'flex';
      state.on = false;
    },

    running: function () { return state.on; },
  };

  window.PlatformSetup = PlatformSetup;
})();
