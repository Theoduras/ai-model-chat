// The Fanvue setup wizard: what it says, step by step. The frame it says it
// in — the stages, the rail, the tour, the borrowing of the console's own
// controls — is js/console-guide.js, shared with the X console's guide.
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
      do: [
        'Connect her Fanvue account, so she can read her inbox and send as herself.',
        'Group her locked media into sets and price each tier.',
        'Set who she answers, how human she sounds and how fast she sells.',
        'Press <b>Save &amp; Run</b>, then watch the log before you open her up.'
      ],
      check: 'Nothing to fill in on this step — <b>Continue</b> starts the first one.',
      body:
        '<p>This wizard links one of your models to one Fanvue account, then lets her answer ' +
        'her own DMs — in her voice, at a human pace, selling your locked content along the way.</p>' +
        '<p>The four things above are the whole job, and they are what the steps walk you ' +
        'through. Every step hands you the console\'s own controls, so what you change here is ' +
        'changed for real — and each step saves with its own button, exactly as it does on the ' +
        'full page.</p>' +
        '<p class="fg-note">You can leave at any point with <b>Skip guide</b> and finish by hand, ' +
        'or wipe this model\'s Fanvue setup and begin again with <b>Start over</b>.</p>'
    },
    {
      stage: 'connect', nav: 'Connect her account',
      title: 'Pick the model and connect Fanvue',
      sub: 'Each model holds one Fanvue account. Connecting one never touches the others.',
      fields: ['persona-row', 'creator-row', 'connect-panel'],
      do: [
        'Pick the model this Fanvue account belongs to.',
        'Press <b>Connect Fanvue</b> and sign in as her in the window that opens.',
        'Approve the request — there are no keys or developer settings to fill in.',
        'If an <b>Acting as</b> dropdown appears, pick the profile whose DMs she answers.'
      ],
      check: 'The pill next to the dropdown reads <b>connected</b>. If Fanvue refuses one of the ' +
        'permissions the box says so and asks again without it — you only need to act if it ' +
        'turns red and stops.',
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
            'with <code>?code=…&amp;state=…</code> — into the box and press Finish connection. ' +
            'A URL carrying <code>error=invalid_scope</code> works here too: it is retried ' +
            'without the permission Fanvue named.']
        ])
    },
    {
      stage: 'content', nav: 'Build a set',
      title: 'Group her content into sets',
      sub: 'A set is one theme — one outfit, one location, one mood.',
      needs: 'connected',
      do: [
        'Press <b>＋ set</b> on the tab row to start one — or click a tab to edit a set you have.',
        'Name it after a single theme: "In bed", "Gym fit", "Shower".',
        'Describe what she\'s doing in it, the way a fan would say it.',
        'Add trigger words, plus an hour window if the set only suits part of the day.'
      ],
      check: 'The set has a name, a scene and at least one trigger word. It is held in the form ' +
        'until <b>Save all sets</b> on the next step — that button stores every set at once.',
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
      do: [
        'With tier 1 selected, click thumbnails in the grid to put media in it.',
        'Set that tier\'s unlock price ($3 minimum), and a caption if you want one.',
        'Press <b>＋ tier</b> and repeat — each rung goes further and costs more. <b>Ladder ＋$5</b> prices them all for you.',
        'Press <b>Save all sets</b>. Nothing above is stored until you do.'
      ],
      check: 'Every tier shows media and a price, and no warnings are left under the buttons.',
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
      do: [
        'Type something a fan would realistically send.',
        'Set an hour if you are testing an "hours it suits" window.',
        'Press <b>Simulate</b> and read which set won, and the score behind it.'
      ],
      check: 'The set you expected wins. If not, fix the trigger words on the previous step — ' +
        'deleting the set is almost never the answer.',
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
      do: [
        'Tick <b>Auto-reply enabled</b>.',
        'Leave <b>Exclude other creators</b> on — creators message each other constantly.',
        'Tick <b>Require payment before next PPV tier</b> for real fans; leave it off while testing.',
        'Only tick <b>Only reply to fans who are online</b> if she should skip everyone else this round.'
      ],
      check: 'The switches are set — but nothing runs yet. They all save on the <b>Go live</b> step.',
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
      do: [
        'Leave <b>Type like a human</b> on.',
        'Pick the typing speed that matches how she would text.',
        'Set what share of replies open with an emoji, or 0 to switch that off.'
      ],
      check: 'She now pauses to read and types at a believable speed, instead of answering the ' +
        'instant a message lands.',
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
      do: [
        'Put your own test handle in <b>Only reply to these fans</b> for the first run.',
        'Set any list of refunders or expired subs to <b>exclude</b>.',
        'Use <b>include</b> only when she should talk to those lists and nobody else.'
      ],
      check: 'While a handle sits in that box she replies to nobody else — clear it when you are ' +
        'ready to open her up.',
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
      do: [
        'Set <b>First PPV after</b> — 6 to 10 messages is a normal start.',
        'Set the gap between drops, and whether an unbought one is offered again.',
        'Set a <b>PPV test phrase</b> if you want to walk the ladder without paying.'
      ],
      check: 'The pacing reads like a person, not a vending machine — and the test phrase is ' +
        'blank before real fans see her.',
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
      do: [
        'Set the minutes of silence before she nudges — 30 to 120 in normal use.',
        'Set it to 5 while testing so you can watch it happen, then put it back.'
      ],
      check: 'A number is in the box — or it is blank on purpose, which switches nudges off.',
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
      do: [
        'Press <b>Save &amp; Run</b> — it stores everything from the last four steps and starts her worker.',
        'Watch the pill next to it turn to <b>running</b>.',
        'If the box names an error, fix what it says and press the button again.'
      ],
      check: 'The pill reads <b>running</b>. The worker polls her inbox on our server, so closing ' +
        'this tab changes nothing.',
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
      do: [
        'Paste something a fan actually said.',
        'Press <b>Draft reply</b> and read it back in her voice.',
        'Copy it into Fanvue if this is a fan you would rather answer yourself.'
      ],
      check: 'The reply sounds like her. If it does not, her voice is set in the persona builder, ' +
        'not on this page.',
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
      do: [
        'Leave <b>live</b> ticked and watch the rows arrive by themselves.',
        'Follow one fan through a whole ladder before you open her up to everyone.',
        'Check <b>Connected accounts</b> to see every model running right now.'
      ],
      check: 'You can see a fan message come in, and her reply go out, in the log.',
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

    { anchor: '.fg-do', placement: 'right',
      title: 'What to do here',
      body: 'Every step opens with the moves for it, in the order you meet them — work down the list. The line under the box below tells you how to know it worked.' },

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

  window.ConsoleGuide.create({
    name: 'FanvueGuide',
    railTitle: 'Fanvue setup',
    storageKey: 'fvGuide',
    scroll: '.fv-scroll',
    stages: STAGES,
    steps: STEPS,
    tour: TOUR,
    gate: 'Connect her Fanvue account first — this step works on her real vault, ' +
          'so there is nothing to show until then.',
    done: {
      sub: 'Everything you filled in is saved on the console behind this screen. ' +
           'Watch one full ladder in the activity log before you open her up to everyone.',
      logId: 'fv-trace-rows',
      logDesc: 'See her replies, drops and errors as they happen.',
      resetDesc: 'Erase this model\'s Fanvue setup — sets, prices, settings — and begin again.'
    },
    reset: {
      what: 'Fanvue setup',
      erased: ['Every PPV set and tier, with its prices and captions',
               'All auto-reply settings, back to their defaults',
               'What she has already sent each fan',
               'The activity log'],
      kept: ['Her persona, voice and photos — those live in the persona builder',
             'What fans have already bought',
             'Everything in her Fanvue vault'],
      dropLabel: 'Also disconnect her Fanvue account',
      dropNote: '— untick to keep the connection and only clear the setup',
      jobs: function (slug, drop, http) {
        var jobs = [
          ['Stopping auto-reply', function () {
            return http.post('/api/fanvue/auto', {
              persona: slug, enabled: false, exclude_creators: true, only_handles: '',
              online_only: false, online_grace: 5, humanize: true, typing_speed: 14,
              react_rate: 25, followup_min: 30, ppv_require_payment: false,
              ppv_test_phrase: '', ppv_first_after: 6, ppv_gap: 8, ppv_retry_after: 0,
              ppv_retry_max: 1, ppv_stale_days: 14, ppv_retry_discount: 0,
              include_lists: [], exclude_lists: [] });
          }],
          ['Clearing what she has already sent', function () {
            return http.post('/api/fanvue/ppv-reset', { persona: slug, confirm: true });
          }],
          ['Deleting her PPV sets', function () {
            return http.post('/api/fanvue/ppv', { persona: slug, sets: [] });
          }],
          ['Clearing the activity log', function () {
            return http.del('/api/fanvue/trace?persona=' + encodeURIComponent(slug));
          }]
        ];
        if (drop) jobs.push(['Disconnecting Fanvue', function () {
          return http.post('/api/fanvue/disconnect', { persona: slug });
        }]);
        return jobs;
      }
    },
    // The console's own loaders repaint the controls the wizard is holding —
    // they rewrite their contents, they do not move them.
    afterReset: function () { if (typeof window.loadConfig === 'function') window.loadConfig(); }
  });
})();
