// Step-by-step setup guide for the Fanvue console.
//
// Same wizard as "New Persona" (js/onboarding.js): the .ob-* shell from
// style.css — rail, stage numbers, crumb, card, footer, finish screen — and
// the same .obt-* spotlight for "show me on the page". The difference is what
// it holds: onboarding *moves the builder's real fields* into the frame, while
// this one teaches a console that already exists, so each step is copy plus a
// spotlight that points at the live control.
//
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
      spots: [['persona', 'the model picker', 'Everything else on this page belongs to the persona selected here. Switching it reloads the connection, the PPV sets and every auto-reply setting.'],
                ['conn-pill', 'the status pill', 'Green and reading <b>connected</b> means we hold a working Fanvue authorization. Anything else and nothing will send.']],
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
      spots: [['connect-btn', 'the Connect button', 'Opens Fanvue in a new window. Sign in as the creator whose inbox this model should answer, and approve.'],
                ['fv-callback', 'the callback box', 'Only needed if the popup was blocked. Paste the whole URL Fanvue landed on — the one containing <code>?code=…&amp;state=…</code> — then press Finish connection.']],
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
      spots: [['ppv-folder-filter', 'the filters', 'Folder, search and type narrow the grid below. They only filter — nothing here changes what is in your vault.'],
                ['ppv-media-grid', 'the media grid', 'Your live Fanvue vault. Click a thumbnail to put it in the tier you are editing, click again to take it out.']],
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
      spots: [['ppv-set-bar', 'the set tabs', 'One button per set, plus ＋ to add one. The highlighted tab is the set every field below edits.'],
                ['ppv-set-fields', 'the set fields', 'Name, scene, trigger words and hours — together these decide when this set is the one she reaches for.']],
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
      spots: [['ppv-tier-bar', 'the tier tabs', 'The ladder inside the current set. Tier 1 sends first; each one after it goes further and costs more.'],
                ['ppv-price', 'the price field', 'What the fan pays to unlock this tier. Fanvue will not accept less than $3.'],
                ['ppv-caption', 'the caption', 'The message that rides along with the locked drop. Keep it in her voice — low pressure sells better than a sales line.']],
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
      spots: [['ppv-sim-text', 'the simulator', 'Type what a fan might say, optionally set an hour, and Simulate shows which set and tier would go out and why. Nothing is sent.']],
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
      spots: [['fv-auto-enabled', 'the switches', 'The master switch and the three guards next to it: exclude other creators, require payment before the next tier, and only reply to fans who are online.']],
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
      spots: [['fv-humanize', 'the humanize switch', 'Adds a pause to read, then types at a believable speed instead of answering the instant a message lands.'],
                ['fv-typing-speed', 'typing speed', 'How fast she types on Fanvue. The Reply Speed in the persona builder is a different setting — that one drives the chat on your own website.'],
                ['fv-react-rate', 'reaction rate', 'Percentage of replies she opens with a single emoji sent as its own short message, because Fanvue has no reaction button.']],
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
      spots: [['fv-only-handles', 'the handle allowlist', 'The safest switch on the page. Put your own test handle here for the first run and she talks to nobody else.'],
                ['fv-lists', 'the Fanvue lists', 'Her smart segments and custom lists. Any list set to include means she talks only to fans in those lists; exclude is for refunders and expired subs.']],
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
      spots: [['fv-ppv-first-after', 'the pacing fields', 'When the first drop lands, how much chat sits between drops, and how a drop that went unbought is re-offered, discounted or finally dropped.'],
                ['fv-ppv-test-phrase', 'the test phrase', 'Testing only — it marks the last PPV as paid so you can walk the whole ladder for free. Clear it before going live.']],
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
      spots: [['fv-followup-min', 'the follow-up field', 'Minutes of silence before she nudges. The second nudge lands at double this, and there is never a third.']],
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
      spots: [['fv-auto-state', 'the run state', 'Reads <b>off</b>, or <b>running</b> with a pulse once the worker is polling her inbox on our server.']],
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
      spots: [['fan-msg', 'the drafter', 'Paste a fan message, get one in-persona reply. Works with no connection at all.'],
                ['fv-trace-rows', 'the activity log', 'Every message in and every reply, drop and error out, refreshed by itself while <b>live</b> is ticked.'],
                ['fv-accounts', 'connected accounts', 'Every persona with a Fanvue connection. All the ones with auto-reply on run at the same time, one worker each.']],
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

  // The intro tour explains the frame, exactly as Onboarding's does for the
  // persona wizard: what the step panel is, where progress lives, and what the
  // footer does. It runs once per creator, not once per visit.
  var TOUR = [
    { anchor: '.ob-stepwrap', placement: 'left',
      title: 'One thing at a time',
      body: 'Setup is split into a handful of short steps. Each one explains a single part of the Fanvue console — read it and continue. Nothing here changes your settings; the guide only teaches the page underneath it.' },

    { anchor: '.ob-card', placement: 'left',
      title: 'What this box covers',
      body: 'Every field, button and pill in that part of the console, in the order you meet them — including the ones that quietly break a setup if you leave them wrong.' },

    { anchor: '.ob-rail', placement: 'right',
      title: 'Where you are',
      body: 'Four stages: connect her account, load the content she sells, set how she replies, then go live. The bar shows how much you have read, and you can jump back to any step by clicking it.' },

    { anchor: '.fg-spots', placement: 'top',
      title: 'See it on the real page',
      body: 'These point at the live control. The guide steps aside, the console lights up the exact field being explained, and Next walks you through the rest of that step\'s controls.' },

    { anchor: '.ob-foot', placement: 'top',
      title: 'Continue, or skip ahead',
      body: 'Continue moves on and ticks the step off. "Skip guide" hands you the console — and the Setup guide button in the header brings this back whenever you want it.' },
  ];

  var state = { idx: 0, on: false, seen: {}, done: false, toured: false };
  var tour = { on: false, items: [], i: 0, el: null, intro: false };

  function byId(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Progress lives in localStorage, not /api/me/setup: the console is one page
  // per creator and the guide teaches it rather than writing anything.
  function load() {
    try {
      var raw = JSON.parse(localStorage.getItem('fvGuide') || '{}') || {};
      state.seen = raw.seen || {};
      state.done = !!raw.done;
      state.toured = !!raw.toured;
    } catch (e) { state.seen = {}; }
  }
  function save() {
    try {
      localStorage.setItem('fvGuide', JSON.stringify(
        { seen: state.seen, done: state.done, toured: state.toured }));
    } catch (e) {}
  }
  function markSeen(i) { state.seen[STEPS[i].nav] = 1; save(); }
  function isDone(i) { return !!state.seen[STEPS[i].nav]; }

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

  function spotsHtml(step) {
    if (!step.spots || !step.spots.length) return '';
    return '<div class="fg-spots">' + step.spots.map(function (sp, i) {
      return '<button type="button" class="ob-skip" onclick="FanvueGuide.spot(' + i + ')">' +
        '<span class="ob-skip-icon" aria-hidden="true">◎</span>Show me ' + esc(sp[1]) + '</button>';
    }).join('') + '</div>';
  }

  function doneHtml() {
    return '<div class="ob-done">' +
      '<div class="ob-seal">✓</div>' +
      '<h2 class="ob-done-h">That\'s the whole console.</h2>' +
      '<p class="ob-done-s">Connect her account, fill one PPV set, allowlist your own handle and ' +
      'press Save &amp; Run. Watch a single ladder end to end in the activity log before you open ' +
      'her up to everyone.</p>' +
      '<div class="ob-nxt">' +
        '<a class="ob-nxt-c" href="#" onclick="FanvueGuide.jump(\'connect-btn\');return false;">' +
          '<div class="ob-nxt-i">💎</div><div class="ob-nxt-t">Connect Fanvue</div>' +
          '<div class="ob-nxt-d">Authorize her account so she can read the inbox and send.</div></a>' +
        '<a class="ob-nxt-c" href="#" onclick="FanvueGuide.jump(\'fv-auto-enabled\');return false;">' +
          '<div class="ob-nxt-i">🤖</div><div class="ob-nxt-t">Set up auto-reply</div>' +
          '<div class="ob-nxt-d">Pick who she answers, how human she sounds and how fast she sells.</div></a>' +
        '<a class="ob-nxt-c" href="#" onclick="FanvueGuide.replay();return false;">' +
          '<div class="ob-nxt-i">📘</div><div class="ob-nxt-t">Read it again</div>' +
          '<div class="ob-nxt-d">Start the guide from the top — it is always in the header.</div></a>' +
      '</div></div>';
  }

  function render() {
    var shell = byId('fg-shell');
    if (!shell) return;
    if (state.done && state.idx >= STEPS.length) {
      shell.innerHTML = '<div class="ob-split">' + doneHtml() + '</div>';
      return;
    }
    var step = STEPS[state.idx];
    var stage = STAGES.filter(function (s) { return s.key === step.stage; })[0];
    var inStage = STEPS.filter(function (s) { return s.stage === step.stage; });
    var pos = inStage.indexOf(step) + 1;
    var pct = progress();

    shell.innerHTML =
      '<div class="ob-split">' +
        '<div class="ob-rail">' +
          '<div class="ob-rail-h">Fanvue setup</div>' +
          '<div class="ob-rail-p">' + pct + '% read</div>' +
          '<div class="ob-mini"><i style="width:' + pct + '%"></i></div>' +
          railHtml() +
        '</div>' +
        '<div class="ob-stepwrap">' +
          '<div class="ob-crumb">Stage ' + (STAGES.indexOf(stage) + 1) + ' · Step ' + pos +
            ' of ' + inStage.length + '</div>' +
          '<h2 class="ob-step-h">' + esc(step.title) + '</h2>' +
          (step.sub ? '<p class="ob-step-s">' + esc(step.sub) + '</p>' : '') +
          '<div class="ob-card"><div class="fg-text">' + step.body + '</div>' +
            spotsHtml(step) + '</div>' +
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
    var w = shell.querySelector('.ob-stepwrap');
    if (w) w.scrollTop = 0;
  }

  // ---- coach-marks -------------------------------------------------------
  // Same overlay as Onboarding's tour: four masks fitted around the anchor
  // leave a real hole, so what is being explained stays readable and clickable.
  // Two flavours share it — the intro tour, which points at the guide's own
  // frame, and the per-step spotlights, which point at controls on the console
  // behind it (so the guide hides for the duration and comes back after).

  function tourEls() {
    var r = byId('fg-tour');
    return { root: r,
      t: r.querySelector('.obt-t'), rr: r.querySelector('.obt-r'),
      b: r.querySelector('.obt-b'), l: r.querySelector('.obt-l'),
      ring: r.querySelector('.obt-ring'), dlg: r.querySelector('.obt-dialog') };
  }

  function anchorEl(item) {
    if (item.anchor) return document.querySelector(item.anchor);
    var t = byId(item.id);
    if (!t) return null;
    return t.closest('.field, .row-inline, .auto-toggles, #ppv-set-fields, #ppv-media-grid') || t;
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

    // Measure the dialog before placing it: the copy varies enough that a
    // fixed height would push it off-screen on the short steps.
    var dw = e.dlg.offsetWidth || Math.min(380, vw - 24);
    var dh = e.dlg.offsetHeight || 200;
    var gap = 14, top2, left2;
    var pl = item.placement || 'bottom';
    if (!r || !r.width) { pl = 'center'; }
    if (pl === 'right' && r.right + gap + dw > vw) pl = r.left - gap - dw > 0 ? 'left' : 'bottom';
    if (pl === 'top' && r.top - gap - dh < 0) pl = 'bottom';
    if (pl === 'bottom' && r && r.bottom + gap + dh > vh) pl = r.top - gap - dh > 0 ? 'top' : 'bottom';

    if (pl === 'center') { top2 = (vh - dh) / 2; left2 = (vw - dw) / 2; }
    else if (pl === 'right') { top2 = r.top; left2 = r.right + gap; }
    else if (pl === 'left') { top2 = r.top; left2 = r.left - gap - dw; }
    else if (pl === 'top') { top2 = r.top - gap - dh; left2 = r.left; }
    else { top2 = r.bottom + gap; left2 = r.left; }
    e.dlg.style.top = Math.max(12, Math.min(top2, vh - dh - 12)) + 'px';
    e.dlg.style.left = Math.max(12, Math.min(left2, vw - dw - 12)) + 'px';
  }

  function startTour(items, intro) {
    // A coach-mark with no anchor on screen would dim the whole page and point
    // at nothing — the spots line, for one, only exists on steps that have one.
    items = items.filter(function (it) {
      return it.anchor ? !!document.querySelector(it.anchor) : !!byId(it.id);
    });
    if (!items.length) return;
    tour.items = items;
    tour.intro = !!intro;
    tour.on = true;
    // A spotlight points at the console, which body.fg-open hides — so step
    // the whole guide aside, frame included, for the duration.
    if (!intro) {
      byId('fg-shell').classList.remove('on');
      document.body.classList.remove('fg-open');
    }
    byId('fg-tour').classList.add('on');
    api.tourGo(0);
  }

  var api = {
    open: function () {
      state.on = true;
      if (state.idx >= STEPS.length) state.idx = STEPS.length - 1;
      document.body.classList.add('fg-open');
      byId('fg-shell').classList.add('on');
      markSeen(state.idx);
      render();
      if (!state.toured) {
        state.toured = true;
        save();
        setTimeout(function () { startTour(TOUR, true); }, 260);
      }
    },
    close: function () {
      state.on = false;
      api.tourEnd(true);
      byId('fg-shell').classList.remove('on');
      document.body.classList.remove('fg-open');
      try { localStorage.setItem('fvGuideOpened', '1'); } catch (e) {}
    },
    replay: function () { state.idx = 0; state.done = false; save(); render(); },
    goto: function (i) {
      state.idx = Math.max(0, Math.min(STEPS.length - 1, i));
      markSeen(state.idx);
      render();
    },
    next: function () {
      markSeen(state.idx);
      if (state.idx === STEPS.length - 1) {
        state.done = true;
        state.idx = STEPS.length;
        save();
        render();
        return;
      }
      api.goto(state.idx + 1);
    },
    back: function () { api.goto(state.idx - 1); },
    // Close the wizard and land on a control — used by the finish screen.
    jump: function (id) {
      api.close();
      var el = anchorEl({ id: id });
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },
    tourReplay: function () { startTour(TOUR, true); },
    spot: function (i) {
      var step = STEPS[state.idx];
      if (!step || !step.spots || !step.spots.length) return;
      startTour(step.spots.map(function (sp) {
        return { id: sp[0], title: sp[1].charAt(0).toUpperCase() + sp[1].slice(1),
                 body: sp[2] || '', placement: 'bottom' };
      }), false);
      api.tourGo(i || 0);
    },
    tourGo: function (i) {
      tour.i = Math.max(0, Math.min(tour.items.length - 1, i));
      tour.el = anchorEl(tour.items[tour.i]);
      if (tour.el && !tour.intro) {
        tour.el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // Let the smooth scroll land before the hole is cut, or the mask sits
        // where the element used to be.
        setTimeout(place, 320);
      }
      place();
    },
    tourEnd: function (silent) {
      if (!tour.on) return;
      tour.on = false;
      byId('fg-tour').classList.remove('on');
      if (!silent && state.on) {
        document.body.classList.add('fg-open');
        byId('fg-shell').classList.add('on');
      }
    }
  };

  function mount() {
    load();

    var css = document.createElement('style');
    css.textContent = [
      // The guide takes the console's slot under the page header, not the whole
      // viewport — same as the persona wizard, which leaves the dashboard chrome
      // standing around it.
      '#fg-shell{flex:1;min-height:0;display:none;background:var(--bg);}',
      '#fg-shell.on{display:flex;flex-direction:column;}',
      'body.fg-open .fv-scroll{display:none;}',
      '#fg-shell .ob-split{flex:1;min-height:0;}',
      '.fg-text{font-size:.86rem;line-height:1.62;color:var(--text-2);}',
      '.fg-text p{margin:0 0 10px;}',
      '.fg-text p:last-child{margin-bottom:0;}',
      '.fg-text code{background:var(--surface);padding:1px 5px;border-radius:var(--r-sm);font-size:.8rem;}',
      '.fg-note{border-left:2px solid var(--accent);padding-left:10px;margin-top:14px;color:var(--text-muted);}',
      '.fg-dl{margin:0 0 4px;}',
      '.fg-dl dt{font-weight:600;color:var(--text);margin-top:12px;}',
      '.fg-dl dt:first-child{margin-top:0;}',
      '.fg-dl dd{margin:2px 0 0;color:var(--text-2);}',
      '.fg-spots{display:flex;flex-wrap:wrap;gap:8px;}',
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
