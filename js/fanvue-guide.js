// The Fanvue setup wizard — the same guided flow as "New Persona"
// (js/onboarding.js), pointed at the Fanvue console instead of the builder.
//
// It works the way that one does: the console's real controls are *moved* into
// the step frame, so the creator connects the account, builds the PPV ladder
// and switches auto-reply on from inside the wizard. Nothing here is a copy or
// a mock — the fields are the page's own, so every existing handler in
// fanvue.html (loadConfig, stashSet, savePpv, saveAuto…) keeps working, and the
// settings are saved by the console's own buttons as you go.
//
// What the wizard adds around them is the explanation: each step says what the
// controls in it do, in the order you meet them.
//
// Every moved node leaves a placeholder behind, so closing the wizard puts the
// console back exactly as it was.
(function () {

  var STAGES = [
    { key: 'connect', label: 'Connect' },
    { key: 'content', label: 'PPV content' },
    { key: 'auto',    label: 'Auto-reply' },
    { key: 'live',    label: 'Go live' }
  ];

  function dl(rows) {
    return '<dl class="fg-dl">' + rows.map(function (r) {
      return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>';
    }).join('') + '</dl>';
  }

  // A step's fields are the console nodes it takes over. Ids resolve to the
  // node itself; '.field:id' takes the wrapper that carries the label and hint.
  var STEPS = [
    {
      stage: 'connect', nav: 'How it works',
      title: 'What you are about to set up',
      sub: 'Four stages. You do each one right here — this is the console, not a copy of it.',
      fields: [],
      body:
        '<p>This wizard links one of your models to one Fanvue account, then lets her answer ' +
        'her own DMs — in her voice, at a human pace, selling your locked content along the way.</p>' +
        dl([
          ['1. Connect', 'Authorize Fanvue so she can read her inbox and send as herself.'],
          ['2. PPV content', 'Group the media she may send locked into sets, and price each tier.'],
          ['3. Auto-reply', 'Decide who she answers, how human she sounds, how fast she sells.'],
          ['4. Go live', 'Save &amp; Run, then watch the log until you trust her.']
        ]) +
        '<p class="fg-note">Each step saves with its own button, exactly as it does on the full ' +
        'console. You can leave at any point with <b>Skip guide</b> and finish by hand.</p>'
    },
    {
      stage: 'connect', nav: 'Connect her account',
      title: 'Pick the model and connect Fanvue',
      sub: 'Each model holds one Fanvue account. Connecting one never touches the others.',
      fields: ['persona-row', 'creator-row', 'connect-panel'],
      done: function () {
        var p = document.getElementById('conn-pill');
        return !!(p && p.classList.contains('ok'));
      },
      body:
        dl([
          ['Choose a model (persona)', 'Everything in this wizard belongs to the persona picked ' +
            'here — the connection, the PPV sets, every auto-reply setting. Switching it reloads ' +
            'all of them.'],
          ['The status pill', 'Next to the dropdown. <b>connected</b> means we hold a working ' +
            'authorization. Anything else and nothing will send.'],
          ['Connect Fanvue', 'Opens Fanvue in a new window. Sign in as the creator whose inbox ' +
            'this model should answer and approve the request — no app credentials or developer ' +
            'keys needed. <b>Disconnect</b> drops the authorization and keeps every setting.'],
          ['Acting as', 'Only appears when that login manages more than one profile. Pick the one ' +
            'whose DMs she should answer, or she will reply from the wrong account.'],
          ['Popup blocked?', 'If the window never opened, copy the URL Fanvue landed on — the one ' +
            'with <code>?code=…&amp;state=…</code> — into the box and press Finish connection.']
        ])
    },
    {
      stage: 'content', nav: 'Build a set',
      title: 'Group her content into sets',
      sub: 'A set is one theme — one outfit, one location, one mood.',
      needs: 'connected',
      fields: ['ppv-set-bar', 'ppv-set-fields'],
      body:
        '<p>She stays on whichever set a fan is working through and sends its tiers in order, ' +
        'switching only when another set clearly fits the conversation better or this one is used ' +
        'up. So keep each set coherent.</p>' +
        dl([
          ['The set tabs', 'One button per set, plus <b>＋</b> for a new one. The highlighted tab ' +
            'is the set the fields below are editing.'],
          ['Set name', 'For you, not the fan. "In bed", "Gym fit", "Shower".'],
          ['What she\'s doing in it', 'A plain sentence, written the way a fan would describe the ' +
            'scene — it is matched against what is actually said in the chat.'],
          ['Trigger words', 'Your strongest signal, comma separated. <code>word</code> exact · ' +
            '<code>word*</code> also matches longer forms · <code>two words</code> a phrase · ' +
            '<code>word^5</code> counts five times as much · <code>!word</code> holds back ' +
            '<i>every</i> set while that word is in play — use it for <code>!broke</code>.'],
          ['Hours it suits', 'A 0–23 window this set is allowed in; blank means any hour. A ' +
            'morning-in-bed set at 3pm reads as fake. The window may wrap midnight (22 → 4).']
        ])
    },
    {
      stage: 'content', nav: 'Fill the tiers',
      title: 'Fill and price the ladder',
      sub: 'Tier 1 is the cheap opener; each tier after it goes further and costs more.',
      needs: 'connected',
      fields: ['ppv-tier-bar', 'ppv-tier-contents', 'ppv-filters', 'ppv-media-block', 'ppv-tier-block'],
      body:
        dl([
          ['The tier tabs', 'One per rung of the ladder in the current set, plus <b>＋</b>. The ' +
            'selected tier owns the media, price and caption below.'],
          ['Folder / Search / Type', 'Narrow the grid. They filter only — nothing changes in your ' +
            'Fanvue vault.'],
          ['The media grid', 'Your live vault. Click a thumbnail to put it in this tier, click ' +
            'again to take it out. <b>Load more</b> pulls the next page.'],
          ['Unlock price', 'What the fan pays to open this tier. Fanvue will not accept under $3.'],
          ['Ladder ＋$5', 'Prices the whole set from tier 1 upward in $5 steps — a sane default to ' +
            'hand-tune.'],
          ['Caption', 'Optional line that rides along with the drop. Keep it in her voice; low ' +
            'pressure outsells a sales line.'],
          ['Save all sets', 'Writes every set and tier for this model. Nothing above is stored ' +
            'until you press it — do that before you continue.'],
          ['The warnings underneath', 'They flag what silently breaks a ladder: an empty tier, a ' +
            'price under the minimum, a vault too big to cache.']
        ])
    },
    {
      stage: 'content', nav: 'Test the match',
      title: 'Try a message before a fan does',
      sub: 'Nothing is sent — this only shows what the matcher would pick.',
      needs: 'connected',
      fields: ['ppv-sim-block'],
      body:
        '<p>Type something a fan might realistically say, optionally set an hour to test your ' +
        '"hours it suits" windows, and Simulate shows which set wins, which tier goes out and the ' +
        'score behind it — including any <code>!word</code> that blocked everything.</p>' +
        '<p class="fg-note">If the wrong set keeps winning, fix it in the trigger words on the ' +
        'previous step. Deleting the set is almost never the answer.</p>'
    },
    {
      stage: 'auto', nav: 'Master switches',
      title: 'Turn her on, and pick who counts',
      fields: ['fv-toggles-main', 'fv-online-grace-wrap'],
      body:
        dl([
          ['Auto-reply enabled', 'The master switch. Off, the console still drafts replies for you ' +
            'by hand; on, the server reads her inbox on a loop and answers by itself.'],
          ['Exclude other creators', 'Leave on. Fanvue creators message each other constantly and ' +
            'you do not want your funnel run on them.'],
          ['Require payment before next PPV tier', 'On for live use: tier 2 waits until tier 1 is ' +
            'actually bought. Off, tiers flow on chat volume alone — useful while testing, ' +
            'dangerous with real fans.'],
          ['Only reply to fans who are online', 'Skips everyone not in the app this round. Replies ' +
            'land while they are still reading, but quiet fans then depend on follow-ups.'],
          ['Count as online for', 'Appears with that switch. Presence flickers when someone ' +
            'backgrounds the app, so a few minutes of grace stops her missing an active fan.']
        ])
    },
    {
      stage: 'auto', nav: 'Sounding human',
      title: 'Make the timing believable',
      sub: 'Instant, perfectly typed replies are the fastest way to get spotted.',
      fields: ['fv-toggles-human', 'fv-typing-wrap', 'fv-react-wrap'],
      body:
        dl([
          ['Type like a human', 'Adds a pause to read, then types at a believable speed instead of ' +
            'answering the instant a message lands.'],
          ['Typing speed', 'Roughly characters per second. This is Fanvue only — the Reply Speed ' +
            'and Typing Speed in the persona builder drive the chat on your own website.'],
          ['React with an emoji', 'Share of replies she opens with a single emoji. Fanvue has no ' +
            'reaction button, so it goes out as its own short message first — which is exactly ' +
            'what a real person does. 0 switches it off.']
        ])
    },
    {
      stage: 'auto', nav: 'Who she talks to',
      title: 'Limit the audience',
      sub: 'Set these hard for the first run, then open them up.',
      fields: ['.field:fv-only-handles', '.field:fv-lists'],
      body:
        dl([
          ['Only reply to these fans', 'Comma-separated handles; blank means everyone. Put your own ' +
            'test account here before you go live — it is the safest switch on the page.'],
          ['Fanvue lists', 'Her smart segments and custom lists. As soon as any list is set to ' +
            '<b>include</b>, she talks only to fans in those lists. <b>Exclude</b> is for refunders ' +
            'and expired subs. <b>Refresh lists</b> re-reads them from Fanvue.']
        ])
    },
    {
      stage: 'auto', nav: 'Selling pace',
      title: 'Decide how fast the offers come',
      sub: 'This is what separates a person from a vending machine.',
      fields: ['fv-pacing-block'],
      body:
        dl([
          ['First PPV after', 'Messages in the chat, both directions, before the first locked drop. ' +
            'Low numbers sell fast and burn fans; 6–10 is a normal start.'],
          ['Messages between PPVs', 'How much more conversation before the next tier may go out.'],
          ['Offer it again after / How many times', 'If a drop goes unbought she re-offers it after ' +
            'this much more chat, this many times, then drops it. 0 means never re-offer.'],
          ['Stop waiting after', 'Days before an unbought unlock stops blocking the ladder, so one ' +
            'ignored photo cannot end the funnel for that fan.'],
          ['Discount a re-offer by', 'Applied only when they opened the preview and did not buy — ' +
            'that reads as a price objection rather than disinterest.'],
          ['Reset PPV progress', 'Clears what has been sent and paid, for every fan. Use it after ' +
            'you rebuild the sets.'],
          ['Reconcile purchases', 'Re-reads Fanvue\'s own records and fills in purchases we missed, ' +
            'so a fan who paid is not stuck on the same tier.'],
          ['PPV test phrase', 'Testing only: a fan message containing it marks the last PPV as ' +
            'paid, so you can walk the ladder for free. <b>Clear it before going live.</b>']
        ])
    },
    {
      stage: 'auto', nav: 'Follow-ups',
      title: 'Nudge the fans who go quiet',
      fields: ['.field:fv-followup-min'],
      body:
        '<p>Minutes of silence before she nudges. The first nudge lands at this value, the second ' +
        'at double it, and there is never a third — two unanswered nudges is where charming turns ' +
        'into desperate. Set it to 5 to watch it work, then put it back to 30–120.</p>' +
        '<p class="fg-note">Scheduled follow-ups are a Pro feature. Replies to fans who message ' +
        'her work on every plan.</p>'
    },
    {
      stage: 'live', nav: 'Go live',
      title: 'Save & Run',
      sub: 'One button starts the loop on our server.',
      fields: ['fv-run-block'],
      done: function () {
        var p = document.getElementById('fv-auto-state');
        return !!(p && /run/i.test(p.textContent || ''));
      },
      body:
        dl([
          ['Save &amp; Run', 'Stores everything you set in the last four steps and starts (or ' +
            'restarts) her worker. Nothing in Auto-reply takes effect until you press it.'],
          ['The state pill', '<b>off</b>, or <b>running</b> with a pulse — the worker is polling ' +
            'her inbox on our server, so closing this tab changes nothing.'],
          ['The result box', 'Says what the save did, and names the first error if the run could ' +
            'not start — usually a missing connection or an empty PPV set.']
        ]) +
        '<p class="fg-note">For the first live run: allowlist your test handle, set the test ' +
        'phrase, and watch one full ladder end to end before you open her up.</p>'
    },
    {
      stage: 'live', nav: 'Draft by hand',
      title: 'Draft a reply yourself',
      sub: 'Useful with or without a connection.',
      fields: ['.form-section:fan-msg'],
      body:
        '<p>Paste what a fan said and get one in-persona, funnel-aware reply. It works even when ' +
        'nothing is connected, so it is the fastest way to audition her voice — or to hand-answer ' +
        'a fan you would rather not automate.</p>'
    },
    {
      stage: 'live', nav: 'Watch her work',
      title: 'The log, and everyone connected',
      sub: 'The two panels you will live in after launch.',
      fields: ['.form-section:fv-trace-rows', '.form-section:fv-accounts'],
      body:
        dl([
          ['Activity log', 'Every fan message in, and every reply, PPV drop and error out, ' +
            'refreshing by itself while <b>live</b> is ticked. <b>Refresh</b> forces a read, ' +
            '<b>Clear</b> empties it. Problems are lifted above the rows so a failing connection ' +
            'is not buried.'],
          ['Connected accounts', 'Every persona with a Fanvue connection. All the ones with ' +
            'auto-reply on run at the same time — one worker each — so a busy inbox never holds ' +
            'up another model.']
        ])
    }
  ];

  // The intro tour explains the frame the wizard puts people inside, exactly as
  // Onboarding's does. It runs once per creator, not once per visit.
  var TOUR = [
    { anchor: '.ob-stepwrap', placement: 'right',
      title: 'One thing at a time',
      body: 'Setup is split into a handful of short steps. Each one covers a single part of the Fanvue console and hands you the real controls for it — what you change here is changed for real.' },

    { anchor: '#fg-step-body', placement: 'right',
      title: 'This is the actual setting',
      body: 'Whatever appears in this box is the console\'s own field, moved here for this step. Fill it in and save it with the button in the step — the same button you would press on the full page.' },

    { anchor: '.ob-rail', placement: 'right',
      title: 'Where you are',
      body: 'Four stages. The bar shows how far along you are, and you can jump back to any step you have already passed by clicking it.' },

    { anchor: '.ob-foot', placement: 'top',
      title: 'Continue, or skip ahead',
      body: 'Continue moves on to the next step. If you would rather see every setting at once, "Skip guide" hands you the full console — and you can come back to this guide any time.' }
  ];

  var state = { idx: 0, on: false, seen: {}, finished: false, toured: false };
  var tour = { on: false, items: [], i: 0, el: null };

  function byId(id) { return document.getElementById(id); }

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
      var raw = JSON.parse(localStorage.getItem('fvGuide') || '{}') || {};
      state.seen = raw.seen || {};
      state.finished = !!raw.finished;
      state.toured = !!raw.toured;
    } catch (e) { state.seen = {}; }
  }
  function save() {
    try {
      localStorage.setItem('fvGuide', JSON.stringify(
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
      card.innerHTML = '<p class="hint">Connect her Fanvue account first — this step works on her ' +
        'real vault, so there is nothing to show until then. ' +
        '<a href="#" onclick="FanvueGuide.goto(1);return false;">Back to Connect</a></p>';
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
          return '<div class="ob-rstep ' + cls + '" onclick="FanvueGuide.goto(' + i + ')">' +
            (isDone(i) && i !== state.idx ? '<span class="ob-rcheck">✓</span>'
                                          : '<span class="ob-rdot"></span>') +
            esc(STEPS[i].nav) + '</div>';
        }).join('') + '</div>' : '') +
      '</div>';
    }).join('');
  }

  function doneHtml() {
    return '<div class="ob-done">' +
      '<div class="ob-seal">✓</div>' +
      '<h2 class="ob-done-h">She\'s set up.</h2>' +
      '<p class="ob-done-s">Everything you filled in is saved on the console behind this screen. ' +
      'Watch one full ladder in the activity log before you open her up to everyone.</p>' +
      '<div class="ob-nxt">' +
        '<a class="ob-nxt-c" href="#" onclick="FanvueGuide.close();return false;">' +
          '<div class="ob-nxt-i">⚙️</div><div class="ob-nxt-t">Open the full console</div>' +
          '<div class="ob-nxt-d">Every setting on one page, to fine-tune what you just set up.</div></a>' +
        '<a class="ob-nxt-c" href="#" onclick="FanvueGuide.jump(\'fv-trace-rows\');return false;">' +
          '<div class="ob-nxt-i">🩺</div><div class="ob-nxt-t">Watch the log</div>' +
          '<div class="ob-nxt-d">See her replies, drops and errors as they happen.</div></a>' +
        '<a class="ob-nxt-c" href="#" onclick="FanvueGuide.replay();return false;">' +
          '<div class="ob-nxt-i">📘</div><div class="ob-nxt-t">Run it again</div>' +
          '<div class="ob-nxt-d">Start from the top — for the next model, or to change your mind.</div></a>' +
      '</div></div>';
  }

  function render() {
    var shell = byId('fg-shell');
    if (!shell) return;
    unmount();

    if (state.finished && state.idx >= STEPS.length) {
      shell.innerHTML = '<div class="ob-split">' + doneHtml() + '</div>';
      return;
    }
    var step = STEPS[state.idx];
    var stage = STAGES.filter(function (s) { return s.key === step.stage; })[0];
    var inStage = STEPS.filter(function (s) { return s.stage === step.stage; });
    var pct = progress();

    shell.innerHTML =
      '<div class="ob-split">' +
        '<div class="ob-rail">' +
          '<div class="ob-rail-h">Fanvue setup</div>' +
          '<div class="ob-rail-p">' + pct + '% done</div>' +
          '<div class="ob-mini"><i style="width:' + pct + '%"></i></div>' +
          railHtml() +
        '</div>' +
        '<div class="ob-stepwrap">' +
          '<div class="ob-crumb">Stage ' + (STAGES.indexOf(stage) + 1) + ' · Step ' +
            (inStage.indexOf(step) + 1) + ' of ' + inStage.length + '</div>' +
          '<h2 class="ob-step-h">' + esc(step.title) + '</h2>' +
          (step.sub ? '<p class="ob-step-s">' + esc(step.sub) + '</p>' : '') +
          '<div class="ob-card" id="fg-step-body"></div>' +
          // The per-element explanation is folded away by default: the step has
          // to read like the persona wizard's — heading, the fields, the
          // footer, no scrolling to reach Continue.
          '<details class="fg-more"><summary>What every field here does</summary>' +
            '<div class="fg-explain">' + step.body + '</div></details>' +
          '<div class="ob-foot">' +
            (state.idx > 0 ? '<button class="btn btn-ghost" onclick="FanvueGuide.back()">← Back</button>' : '') +
            '<button class="btn btn-primary" onclick="FanvueGuide.next()">' +
              (state.idx === STEPS.length - 1 ? 'Finish ✓' : 'Continue →') + '</button>' +
            '<span class="ob-saved">Step ' + (state.idx + 1) + ' of ' + STEPS.length + '</span>' +
            '<div class="ob-alt">' +
              '<button class="ob-skip" type="button" onclick="FanvueGuide.tourReplay()">' +
                '<span class="ob-skip-icon" aria-hidden="true">◎</span>Show me around</button>' +
              '<button class="ob-skip" type="button" onclick="FanvueGuide.close()">' +
                '<span class="ob-skip-icon" aria-hidden="true">⚙</span>Skip guide</button>' +
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
      '<button class="obt-close" type="button" onclick="FanvueGuide.tourEnd()" aria-label="Close">✕</button>' +
      '<div class="obt-title">' + esc(item.title) + '</div>' +
      '<div class="obt-body">' + item.body + '</div>' +
      '<div class="obt-foot">' +
        '<span class="obt-count">' + (tour.i + 1) + ' of ' + tour.items.length + '</span>' +
        (tour.i > 0 ? '<button class="btn btn-ghost" type="button" onclick="FanvueGuide.tourGo(' +
          (tour.i - 1) + ')">Back</button>' : '') +
        '<button class="btn btn-primary" type="button" onclick="FanvueGuide.' +
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
      try { localStorage.setItem('fvGuideOpened', '1'); } catch (e) {}
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
    tourReplay: function () {
      var items = TOUR.filter(function (t) { return document.querySelector(t.anchor); });
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

    var css = document.createElement('style');
    css.textContent = [
      // The wizard takes the console's slot under the page header, not the
      // whole viewport — same as the persona wizard inside the dashboard.
      '#fg-shell{flex:1;min-height:0;display:none;background:var(--bg);}',
      '#fg-shell.on{display:flex;flex-direction:column;}',
      'body.fg-open .fv-scroll{display:none;}',
      '#fg-shell .ob-split{flex:1;min-height:0;}',
      // Borrowed console nodes carry their own margins from the page; inside a
      // step they are the only thing in the card.
      '#fg-step-body > *{margin-top:0;}',
      '#fg-step-body:empty{display:none;}',
      'body.fg-embedded > header{display:none;}',
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

    var shell = document.createElement('div');
    shell.id = 'fg-shell';
    var scroll = document.querySelector('.fv-scroll');
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
    try { first = !localStorage.getItem('fvGuideOpened'); } catch (e) {}
    if (first) api.open();
  }

  window.FanvueGuide = api;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
