// Step-by-step setup guide for the Fanvue console — the in-page counterpart to
// the dashboard's PlatformSetup wizard. That one *drives* the connection; this
// one sits on top of the real console and explains every control on it, because
// the Fanvue page is the one creators use directly (see openPlatform() in
// dashboard.html) and it has far more knobs than a wizard can own.
//
// Every step points at live elements by id: "Show me" scrolls the console to
// the real field and flashes it, so the guide never drifts from the page.
(function () {

  var STAGES = [
    { key: 'connect', label: 'Connect' },
    { key: 'content', label: 'PPV content' },
    { key: 'auto',    label: 'Auto-reply' },
    { key: 'watch',   label: 'Go live' }
  ];

  function dl(rows) {
    return '<dl class="fg-dl">' + rows.map(function (r) {
      return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>';
    }).join('') + '</dl>';
  }

  var STEPS = [
    {
      stage: 'connect', nav: 'How it works',
      title: 'What this page does',
      sub: 'Two minutes of reading now saves an hour of guessing later.',
      spots: [],
      body:
        '<p>The Fanvue console links one of your models to one Fanvue account, then lets ' +
        'that model answer her DMs by herself — in her own voice, at a human pace, and ' +
        'selling your locked content along the way.</p>' +
        '<p>There are four things to set up, in this order:</p>' +
        dl([
          ['1. Connect', 'Authorize Fanvue so we can read her inbox and send messages as her.'],
          ['2. PPV content', 'Pick the media she may send locked, group it into sets, and price each tier.'],
          ['3. Auto-reply', 'Decide who she replies to, how human she sounds, and how fast the offers come.'],
          ['4. Go live', 'Save &amp; Run, then watch the activity log until you trust her.']
        ]) +
        '<p class="fg-note">Everything runs on our server, not in this tab. You can close the ' +
        'page and she keeps replying.</p>'
    },
    {
      stage: 'connect', nav: 'Pick the model',
      title: 'Choose which model this is for',
      sub: 'Each persona holds its own Fanvue account, its own PPV sets and its own settings.',
      spots: [['persona', 'the model picker'], ['conn-pill', 'the status pill']],
      body:
        dl([
          ['Choose a model (persona)', 'The dropdown lists every persona you have built. Everything ' +
            'else on this page — connection, PPV sets, auto-reply settings — belongs to the model ' +
            'selected here. Switching the dropdown reloads all of it.'],
          ['Status pill', 'Sits next to the dropdown. <b>connected</b> means we hold a working Fanvue ' +
            'authorization for her; <b>—</b> or an error means we do not, and nothing will send.'],
          ['Connect Fanvue', 'Starts the authorization. You do not need any app credentials or ' +
            'developer keys — just her Fanvue login.'],
          ['Disconnect', 'Drops the stored authorization. Her PPV sets and settings stay; she simply ' +
            'stops being able to read or send until you connect again.']
        ]) +
        '<p class="fg-note">No persona in the list? Build one in the dashboard first — the guide ' +
        'here only connects a persona that already exists.</p>'
    },
    {
      stage: 'connect', nav: 'Authorize',
      title: 'Authorize her Fanvue account',
      sub: 'A normal OAuth sign-in, with a fallback for blocked popups.',
      spots: [['connect-btn', 'the Connect button'], ['fv-callback', 'the callback box']],
      body:
        '<p>Click <b>Connect Fanvue</b>. A Fanvue window opens; sign in as the creator whose ' +
        'inbox this model should answer and approve the access request. When it closes, the ' +
        'status pill flips to <b>connected</b>.</p>' +
        dl([
          ['Popup blocked?', 'If nothing opened, or the window closed without the pill changing, ' +
            'copy the full URL of the page Fanvue landed on (it contains <code>?code=…&amp;state=…</code>) ' +
            'and paste it into the callback box.'],
          ['Finish connection ✓', 'Completes the same handshake from that pasted URL.'],
          ['Acting as', 'Appears only when the login you used manages more than one Fanvue profile. ' +
            'Pick the profile whose DMs this model should answer — get this wrong and she will reply ' +
            'from the wrong account.']
        ])
    },
    {
      stage: 'content', nav: 'Your media',
      title: 'Find the media she can send',
      sub: 'The grid is your real Fanvue vault, read live. Nothing is uploaded or copied.',
      spots: [['ppv-folder-filter', 'the filters'], ['ppv-media-grid', 'the media grid']],
      body:
        dl([
          ['Folder', 'Narrows the grid to one Fanvue folder. "All folders" shows everything.'],
          ['Search', 'Matches the file name and description you gave the media in Fanvue.'],
          ['Type', 'Photos only, videos only, or both.'],
          ['The grid', 'Click a thumbnail to add it to the tier you are editing; click again to ' +
            'remove it. Selected items are outlined.'],
          ['Load more / count', 'The vault is paged. The count tells you how many are loaded, and ' +
            'the button (or scrolling to the bottom) fetches the next page.']
        ]) +
        '<p class="fg-note">Only media that exists in her Fanvue vault can be sent. Add new content ' +
        'in Fanvue first, then hit the folder filter again to pull it in.</p>'
    },
    {
      stage: 'content', nav: 'Build a set',
      title: 'Group the media into sets',
      sub: 'A set is one theme — one outfit, one location, one mood.',
      spots: [['ppv-set-bar', 'the set tabs'], ['ppv-set-fields', 'the set fields']],
      body:
        '<p>She stays on whichever set a fan is working through and sends its tiers in order, ' +
        'only switching to another set when it clearly fits the conversation better or the ' +
        'current one is used up. So make each set coherent.</p>' +
        dl([
          ['Set tabs', 'One button per set, plus <b>＋</b> to add one. The highlighted tab is the ' +
            'set every field below is editing.'],
          ['Set name', 'For you, not the fan. "In bed", "Gym fit", "Shower".'],
          ['What she\'s doing in it', 'A plain sentence describing the scene. This is matched ' +
            'against what is actually being said in the chat, so write it the way a fan would ' +
            'describe it: "lying in bed in the morning".'],
          ['Trigger words', 'Comma separated, and the strongest signal you have. Syntax: ' +
            '<code>word</code> exact match · <code>word*</code> also matches longer forms ' +
            '(<code>stocking*</code> hits "stockings") · <code>two words</code> a phrase · ' +
            '<code>word^5</code> counts five times as much · <code>!word</code> holds back ' +
            '<i>every</i> set while that word is in play (use it for <code>!broke</code>, ' +
            '<code>!refund</code>).'],
          ['Hours it suits', 'A 0–23 window in which this set is allowed. Leave blank for any hour. ' +
            'A morning-in-bed set at 3pm reads as fake; this is how you stop it. The window may ' +
            'wrap midnight (22 → 4).']
        ])
    },
    {
      stage: 'content', nav: 'Tiers & prices',
      title: 'Price the ladder inside each set',
      sub: 'Tier 1 is the cheap opener; every tier after it goes further and costs more.',
      spots: [['ppv-tier-bar', 'the tier tabs'], ['ppv-price', 'the price field'], ['ppv-caption', 'the caption']],
      body:
        dl([
          ['Tier tabs', 'One per step of the ladder inside the current set, plus <b>＋</b> to add ' +
            'one. Selecting a tier makes the grid selection, price and caption below belong to it.'],
          ['Tier contents', 'The media currently in this tier, in the order it will send.'],
          ['This tier\'s unlock price', 'What the fan pays to open it. Fanvue\'s minimum is $3.'],
          ['Ladder ＋$5', 'Fills the whole set from tier 1 upward in $5 steps — a fast, sane default ' +
            'you can then hand-tune.'],
          ['Caption', 'Optional message that rides along with the locked drop. Keep it in her voice ' +
            'and low-pressure — "made something just for you 🙈" outsells "BUY NOW".'],
          ['Save all sets', 'Writes every set and tier for this model. Nothing above is stored until ' +
            'you press it.'],
          ['Hints &amp; warnings', 'The lines under the buttons flag the things that silently break a ' +
            'ladder: a tier with no media, a price below the minimum, or a vault too large to cache.']
        ])
    },
    {
      stage: 'content', nav: 'Test the match',
      title: 'Try a message before any fan sees it',
      sub: 'Nothing is sent — this only shows what the matcher would pick.',
      spots: [['ppv-sim-text', 'the simulator']],
      body:
        dl([
          ['Message box', 'Type something a fan might realistically say.'],
          ['Hour', 'Optional 0–23, so you can check the "hours it suits" windows without waiting ' +
            'for that time of day.'],
          ['Simulate', 'Shows which set wins, which tier would go out, and the score behind the ' +
            'decision — including any <code>!word</code> that blocked everything.']
        ]) +
        '<p class="fg-note">If the wrong set keeps winning, fix it here by editing trigger words and ' +
        'weights, not by deleting the set.</p>'
    },
    {
      stage: 'auto', nav: 'Master switches',
      title: 'Turn her on, and pick who counts',
      sub: 'The four checkboxes at the top of Auto-reply.',
      spots: [['fv-auto-enabled', 'the switches']],
      body:
        dl([
          ['Auto-reply enabled', 'The master switch. Off, this page still drafts replies for you by ' +
            'hand; on, the server reads her inbox on a loop and answers by itself.'],
          ['Exclude other creators', 'Leave on. Fanvue creators message each other constantly and ' +
            'you do not want your funnel run on them.'],
          ['Require payment before next PPV tier', 'On (recommended for live use): she will not send ' +
            'tier 2 until tier 1 is actually bought. Off: tiers flow on chat volume alone — useful ' +
            'while you are testing, dangerous with real fans.'],
          ['Only reply to fans who are online', 'Skips everyone not currently in the app this round. ' +
            'Replies land while the fan is still reading, which converts better, but quiet fans then ' +
            'depend on follow-ups.'],
          ['Count as online for', 'Appears with the switch above. Presence flickers when someone ' +
            'backgrounds the app, so a few minutes of grace stops her from missing an active fan. ' +
            '0 means trust the online flag exactly.']
        ])
    },
    {
      stage: 'auto', nav: 'Sounding human',
      title: 'Make the timing believable',
      sub: 'Instant, perfectly-typed replies are the fastest way to get spotted.',
      spots: [['fv-humanize', 'the humanize switch'], ['fv-typing-speed', 'typing speed'], ['fv-react-rate', 'reaction rate']],
      body:
        dl([
          ['Type like a human', 'Adds a pause to "read" the message, then types at a believable ' +
            'speed instead of answering the instant it arrives.'],
          ['Typing speed', 'Characters per second, roughly: <b>Slow</b> is thumbs on a phone, ' +
            '<b>Natural</b> suits most personas, <b>Very fast</b> only fits a persona written as a ' +
            'fast texter. This applies to Fanvue only — the Reply Speed and Typing Speed in the ' +
            'persona builder drive the chat on your own website.'],
          ['React with an emoji', 'Percentage of replies she prefaces with a single emoji. Fanvue ' +
            'has no reaction button, so it goes out as its own short message just before the reply — ' +
            'which is exactly what a real person does. 0 switches it off.']
        ])
    },
    {
      stage: 'auto', nav: 'Who she talks to',
      title: 'Limit the audience',
      sub: 'Use these hard while testing, then open them up.',
      spots: [['fv-only-handles', 'the handle allowlist'], ['fv-lists', 'the Fanvue lists']],
      body:
        dl([
          ['Only reply to these fans', 'Comma-separated Fanvue handles. Blank means everyone. Put ' +
            'your own test account in here for the first run — it is the safest switch on the page.'],
          ['Fanvue lists', 'Her smart segments and custom lists, pulled from Fanvue. Set each one to ' +
            '<b>include</b> or <b>exclude</b>. As soon as any list is on include, she talks only to ' +
            'fans in those lists. Exclude is for lists like refunders or expired subs.'],
          ['Refresh lists', 'Re-reads the lists from Fanvue after you have changed them there.']
        ])
    },
    {
      stage: 'auto', nav: 'Selling pace',
      title: 'Decide how fast the offers come',
      sub: 'This is the part that decides whether she feels like a person or a vending machine.',
      spots: [['fv-ppv-first-after', 'the pacing fields'], ['fv-ppv-test-phrase', 'the test phrase']],
      body:
        dl([
          ['First PPV after', 'Messages in the chat, counting both directions, before the very first ' +
            'locked drop. Low numbers sell fast and burn fans; 6–10 is a normal starting point.'],
          ['Messages between PPVs', 'How much more conversation has to happen before the next tier ' +
            'may go out.'],
          ['Offer it again after', 'If a drop goes unbought, she re-offers the same one after this ' +
            'many more messages. 0 means never re-offer.'],
          ['How many times', 'How many re-offers before she drops it and moves on.'],
          ['Stop waiting after', 'Days before an unbought unlock stops blocking the ladder, so one ' +
            'ignored photo cannot end the funnel for that fan forever.'],
          ['Discount a re-offer by', 'Percentage off, applied only when the fan opened the preview ' +
            'and did not buy — that reads as a price objection rather than disinterest.'],
          ['Reset PPV progress', 'Clears which tiers have been sent and paid for this model, for ' +
            'every fan. Use it after you rebuild the sets.'],
          ['Reconcile purchases', 'Re-reads Fanvue\'s own records and fills in purchases we missed, ' +
            'so a fan who paid is not stuck at the same tier.'],
          ['PPV test phrase', 'Testing only: when a fan message contains this phrase, the last PPV ' +
            'sent to them counts as paid, so you can walk the whole ladder without spending money. ' +
            '<b>Clear it before going live.</b>']
        ])
    },
    {
      stage: 'auto', nav: 'Follow-ups',
      title: 'Nudge the fans who go quiet',
      spots: [['fv-followup-min', 'the follow-up field']],
      body:
        dl([
          ['Follow-up after silence', 'Minutes of silence before she nudges. The first nudge lands ' +
            'at this value, the second at double it, and there is never a third — two unanswered ' +
            'nudges is where charming turns into desperate. Set it to something small like 5 to ' +
            'watch it work, then put it back to 30–120.'],
          ['Pro note', 'Scheduled follow-ups are a Pro feature. Replies to fans who message her work ' +
            'on every plan.']
        ])
    },
    {
      stage: 'watch', nav: 'Go live',
      title: 'Save & Run',
      sub: 'One button starts the server-side loop.',
      spots: [['fv-auto-state', 'the run state']],
      body:
        dl([
          ['Save &amp; Run', 'Stores every setting in this section and starts (or restarts) her ' +
            'worker. Nothing in Auto-reply takes effect until you press it.'],
          ['State pill', '<b>off</b>, or <b>running</b> with a pulse. Running means the worker is ' +
            'polling her inbox on our server — closing this tab changes nothing.'],
          ['Result box', 'Reports what the save did and surfaces the first error if the run could ' +
            'not start (usually a missing connection or an empty PPV set).']
        ]) +
        '<p class="fg-note">First live run: allowlist your test handle, set the test phrase, and ' +
        'watch one full ladder end to end before you open her up to everyone.</p>'
    },
    {
      stage: 'watch', nav: 'Watch & check',
      title: 'Draft, log and accounts',
      sub: 'The three panels you will live in after launch.',
      spots: [['fan-msg', 'the drafter'], ['fv-trace-rows', 'the activity log'], ['fv-accounts', 'connected accounts']],
      body:
        dl([
          ['Draft a reply', 'Paste a fan message and get one in-persona, funnel-aware reply. Works ' +
            'with no connection at all — the fastest way to audition her voice, or to hand-answer a ' +
            'fan you would rather not automate.'],
          ['Activity log', 'Every fan message in and every reply, PPV drop and error out, refreshed ' +
            'every few seconds while <b>live</b> is ticked. <b>Refresh</b> forces a read, ' +
            '<b>Clear</b> empties the log. Problems are pulled out above the rows so a failing ' +
            'connection is not buried.'],
          ['Connected accounts', 'Every persona with a Fanvue connection. Accounts with auto-reply ' +
            'on all run at the same time — one worker each — so a busy inbox never holds up another ' +
            'model.']
        ]) +
        '<p class="fg-note">You have seen the whole console. Reopen this guide any time from the ' +
        '<b>Setup guide</b> button in the header.</p>'
    }
  ];

  var idx = 0, open = false, seen = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function byId(id) { return document.getElementById(id); }

  function loadSeen() {
    try { seen = JSON.parse(localStorage.getItem('fvGuideSeen') || '{}') || {}; }
    catch (e) { seen = {}; }
  }
  function markSeen(i) {
    seen[STEPS[i].nav] = 1;
    try { localStorage.setItem('fvGuideSeen', JSON.stringify(seen)); } catch (e) {}
  }
  function done(i) { return !!seen[STEPS[i].nav]; }

  function stageState(key) {
    var ids = [];
    STEPS.forEach(function (s, i) { if (s.stage === key) ids.push(i); });
    if (STEPS[idx] && STEPS[idx].stage === key) return 'now';
    return ids.every(done) ? 'done' : 'todo';
  }

  function railHtml() {
    return STAGES.map(function (st, n) {
      var stt = stageState(st.key);
      var mine = [];
      STEPS.forEach(function (s, i) { if (s.stage === st.key) mine.push(i); });
      var openStage = STEPS[idx].stage === st.key;
      return '<div class="fg-stage">' +
        '<div class="fg-stage-t' + (openStage ? ' on' : '') + '">' +
          '<span class="fg-num ' + stt + '">' + (stt === 'done' ? '✓' : (n + 1)) + '</span>' +
          esc(st.label) + '</div>' +
        (openStage ? '<div class="fg-substeps">' + mine.map(function (i) {
          var cls = i === idx ? 'on' : (done(i) ? 'done' : '');
          return '<div class="fg-substep ' + cls + '" onclick="FanvueGuide.goto(' + i + ')">' +
            '<span class="fg-dot">' + (done(i) && i !== idx ? '✓' : '') + '</span>' +
            esc(STEPS[i].nav) + '</div>';
        }).join('') + '</div>' : '') +
      '</div>';
    }).join('');
  }

  function spotsHtml(step) {
    if (!step.spots || !step.spots.length) return '';
    return '<div class="fg-spots">' + step.spots.map(function (sp) {
      return '<button type="button" class="fg-spot-btn" onclick="FanvueGuide.spot(\'' + sp[0] + '\')">' +
        '◎ Show me ' + esc(sp[1]) + '</button>';
    }).join('') + '</div>';
  }

  function render() {
    var el = byId('fg-panel');
    if (!el) return;
    var step = STEPS[idx];
    var pct = Math.round(STEPS.filter(function (s, i) { return done(i); }).length / STEPS.length * 100);
    el.innerHTML =
      '<div class="fg-head">' +
        '<div><div class="fg-title">Fanvue setup guide</div>' +
        '<div class="fg-progress"><span style="width:' + pct + '%"></span></div></div>' +
        '<button type="button" class="fg-x" onclick="FanvueGuide.close()" aria-label="Close guide">✕</button>' +
      '</div>' +
      '<div class="fg-body">' +
        '<div class="fg-rail">' + railHtml() + '</div>' +
        '<div class="fg-card">' +
          '<div class="fg-step-t">' + esc(step.title) + '</div>' +
          (step.sub ? '<div class="fg-step-s">' + esc(step.sub) + '</div>' : '') +
          spotsHtml(step) +
          '<div class="fg-text">' + step.body + '</div>' +
        '</div>' +
      '</div>' +
      '<div class="fg-foot">' +
        '<button type="button" class="fg-btn ghost" onclick="FanvueGuide.prev()"' +
          (idx === 0 ? ' disabled' : '') + '>← Back</button>' +
        '<span class="fg-count">Step ' + (idx + 1) + ' of ' + STEPS.length + '</span>' +
        (idx === STEPS.length - 1
          ? '<button type="button" class="fg-btn" onclick="FanvueGuide.close()">Done ✓</button>'
          : '<button type="button" class="fg-btn" onclick="FanvueGuide.next()">Next →</button>') +
      '</div>';
  }

  var api = {
    open: function () {
      open = true;
      byId('fg-shell').classList.add('on');
      markSeen(idx);
      render();
    },
    close: function () {
      open = false;
      byId('fg-shell').classList.remove('on');
      try { localStorage.setItem('fvGuideOpened', '1'); } catch (e) {}
    },
    toggle: function () { open ? api.close() : api.open(); },
    goto: function (i) {
      idx = Math.max(0, Math.min(STEPS.length - 1, i));
      markSeen(idx);
      render();
      var c = document.querySelector('.fg-card');
      if (c) c.scrollTop = 0;
    },
    next: function () { api.goto(idx + 1); },
    prev: function () { api.goto(idx - 1); },
    // The console scrolls inside .fv-scroll, not the window, so scrollIntoView
    // is the only reliable way to reach a field from the fixed panel.
    spot: function (id) {
      var t = byId(id);
      if (!t) return;
      var box = t.closest('.field, .form-section') || t;
      box.scrollIntoView({ behavior: 'smooth', block: 'center' });
      box.classList.remove('fg-flash');
      void box.offsetWidth;
      box.classList.add('fg-flash');
      setTimeout(function () { box.classList.remove('fg-flash'); }, 2400);
    }
  };

  function mount() {
    loadSeen();
    var css = document.createElement('style');
    css.textContent = [
      '#fg-shell{position:fixed;inset:0;z-index:900;display:none;}',
      '#fg-shell.on{display:block;}',
      '#fg-scrim{position:absolute;inset:0;background:rgba(0,0,0,.5);}',
      '#fg-panel{position:absolute;top:0;right:0;bottom:0;width:min(560px,100%);',
      'background:var(--panel);border-left:1px solid var(--border);display:flex;',
      'flex-direction:column;box-shadow:-18px 0 40px rgba(0,0,0,.35);}',
      '.fg-head{display:flex;align-items:flex-start;gap:12px;padding:16px 18px 12px;',
      'border-bottom:1px solid var(--border-soft);}',
      '.fg-head>div:first-child{flex:1;}',
      '.fg-title{font-size:.95rem;font-weight:600;color:var(--text);}',
      '.fg-progress{margin-top:8px;height:4px;border-radius:9999px;background:var(--border);overflow:hidden;}',
      '.fg-progress span{display:block;height:100%;background:var(--accent);transition:width .25s;}',
      '.fg-x{background:none;border:0;color:var(--text-muted);font-size:1rem;cursor:pointer;padding:2px 4px;}',
      '.fg-body{flex:1;min-height:0;display:flex;}',
      '.fg-rail{width:172px;flex:none;padding:14px 10px;border-right:1px solid var(--border-soft);',
      'overflow-y:auto;font-size:.78rem;}',
      '.fg-stage{margin-bottom:10px;}',
      '.fg-stage-t{display:flex;align-items:center;gap:8px;color:var(--text-3);}',
      '.fg-stage-t.on{color:var(--text);font-weight:600;}',
      '.fg-num{width:19px;height:19px;flex:none;border-radius:50%;display:flex;align-items:center;',
      'justify-content:center;font-size:.66rem;background:var(--border);color:var(--text-2);}',
      '.fg-num.now{background:var(--accent);color:#fff;}',
      '.fg-num.done{background:#14532d;color:#4ade80;}',
      '.fg-substeps{margin:6px 0 0 27px;display:flex;flex-direction:column;gap:2px;}',
      '.fg-substep{display:flex;align-items:center;gap:6px;padding:3px 6px;border-radius:6px;',
      'color:var(--text-muted);cursor:pointer;}',
      '.fg-substep:hover{background:var(--surface);}',
      '.fg-substep.on{background:var(--surface);color:var(--text);}',
      '.fg-substep.done{color:var(--text-2);}',
      '.fg-dot{width:12px;flex:none;font-size:.65rem;color:#4ade80;}',
      '.fg-card{flex:1;min-width:0;overflow-y:auto;padding:18px 20px 24px;}',
      '.fg-step-t{font-size:1.02rem;font-weight:600;color:var(--text);}',
      '.fg-step-s{margin-top:5px;font-size:.8rem;color:var(--text-muted);}',
      '.fg-spots{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;}',
      '.fg-spot-btn{font-size:.72rem;padding:4px 10px;border-radius:9999px;cursor:pointer;',
      'background:var(--surface);border:1px solid var(--border);color:var(--text-2);}',
      '.fg-spot-btn:hover{color:var(--text);border-color:var(--accent);}',
      '.fg-text{margin-top:14px;font-size:.84rem;line-height:1.62;color:var(--text-2);}',
      '.fg-text p{margin:0 0 10px;}',
      '.fg-text code{background:var(--surface);padding:1px 5px;border-radius:5px;font-size:.78rem;}',
      '.fg-note{border-left:2px solid var(--accent);padding-left:10px;color:var(--text-muted);}',
      '.fg-dl{margin:0 0 10px;}',
      '.fg-dl dt{font-weight:600;color:var(--text);margin-top:10px;}',
      '.fg-dl dd{margin:2px 0 0;color:var(--text-2);}',
      '.fg-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;',
      'padding:12px 18px;border-top:1px solid var(--border-soft);}',
      '.fg-count{font-size:.75rem;color:var(--text-muted);}',
      '.fg-btn{padding:7px 16px;border-radius:8px;border:0;cursor:pointer;font-size:.8rem;',
      'background:var(--accent);color:#fff;}',
      '.fg-btn.ghost{background:var(--border);color:var(--text-2);}',
      '.fg-btn[disabled]{opacity:.45;cursor:default;}',
      '.fg-flash{outline:2px solid var(--accent);outline-offset:4px;border-radius:8px;',
      'animation:fg-pulse 1.2s ease-in-out 2;}',
      '@keyframes fg-pulse{0%,100%{outline-color:var(--accent);}50%{outline-color:transparent;}}',
      '@media (max-width:720px){.fg-rail{display:none;}}'
    ].join('');
    document.head.appendChild(css);

    var shell = document.createElement('div');
    shell.id = 'fg-shell';
    shell.innerHTML = '<div id="fg-scrim" onclick="FanvueGuide.close()"></div><div id="fg-panel"></div>';
    document.body.appendChild(shell);

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && open) api.close();
    });

    render();
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
