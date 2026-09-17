// The Discord setup wizard: what it says, step by step. The frame it says it
// in — the stage tabs, the step pills, the tour, the borrowing of the console's
// own controls — is js/console-guide.js, shared with Fanvue and X.
//
// Discord is the one platform here driven as a real user account rather than an
// app, so the wizard's job is not only "fill this in": it has to say plainly
// what that costs and where the account can be lost. The risk step is not
// skippable filler.
(function () {

  var STAGES = [
    { key: 'connect', label: 'Connect',    icon: '🔌' },
    { key: 'reply',   label: 'Replying',   icon: '💬' },
    { key: 'public',  label: 'In public',  icon: '📣' },
    { key: 'live',    label: 'Go live',    icon: '◎' }
  ];

  function dl(rows) {
    return '<dl class="fg-dl">' + rows.map(function (r) {
      return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>';
    }).join('') + '</dl>';
  }

  function on(id) {
    var e = document.getElementById(id);
    return !!(e && e.checked);
  }

  var STEPS = [
    {
      stage: 'connect', nav: 'How it works',
      title: 'What you are about to set up',
      sub: '{stages} stages. You do each one right here — this is the console, not a copy of it.',
      fields: [],
      do: [
        'Read the risk step. Discord is the one platform here that can cost you the account.',
        'Sign her in, as herself, on Discord\'s own login page.',
        'Set how she answers DMs and mentions, and how human that looks.',
        'Choose the channels she may speak in — and whether she ever speaks first.',
        'Set the offer, switch auto-reply on, and watch a round in the log.'
      ],
      check: 'Nothing to fill in on this step — <b>Continue</b> starts the first one.',
      body:
        '<p>This wizard links one of your models to one <b>real Discord account</b>. She then ' +
        'answers her DMs in her voice, answers when somebody @mentions her in a channel you ' +
        'have allowed, and — if you let her — joins a conversation now and then without being ' +
        'spoken to.</p>' +
        '<p>Two things make Discord different from every other platform in this app, and they ' +
        'shape everything below:</p>' +
        dl([
          ['She is an account, not a bot',
           'There is no "add to server" button and no bot badge. She is in a server because ' +
           'somebody invited her, exactly like a person. That is also why Discord can take ' +
           'the account away — see the next step.'],
          ['A channel is not a DM',
           'A room full of people never runs the funnel. She does not warm anyone up there, ' +
           'does not nudge, and never posts a paid link. The offer only ever goes out in a ' +
           'one-to-one DM, and that rule is in the code, not in a setting you can flip.']
        ]) +
        '<p class="fg-note">You can leave at any point with <b>Skip guide</b> and finish by ' +
        'hand, or wipe this model\'s Discord setup and begin again with <b>Start over</b>.</p>'
    },
    {
      stage: 'connect', nav: 'The risk',
      title: 'Read this before you connect anything',
      sub: 'Discord\'s terms do not allow an automated user account. This is the honest version.',
      fields: ['dc-warn'],
      do: [
        'Use an account you could lose tomorrow without losing anything else.',
        'Keep her only in servers she was genuinely invited to.',
        'Keep the chime-in cap low — that is the setting that makes an account look like a bot.',
        'Never point her at a server you moderate from your own main account.'
      ],
      check: 'Nothing to save here. Continue once you have decided the account is expendable.',
      body:
        '<p>Automating a user account is called <b>self-botting</b> and Discord bans for it. ' +
        'Enforcement is not only account-level: a termination can sweep other accounts seen ' +
        'on the same device or payment method. That is why the account she runs on should be ' +
        'hers alone and nothing else.</p>' +
        '<p>What this app does to keep her from looking automated is fixed, not optional:</p>' +
        dl([
          ['One socket per token',
           'She holds a single gateway connection, the way a real client does. Two would be ' +
           'the first thing a detection pass looks for.'],
          ['One stable fingerprint',
           'The build number and capabilities she identifies with are the ones her own ' +
           'sign-in really used — captured, never guessed. A fingerprint that does not match ' +
           'any real client is a giveaway.'],
          ['She never joins a server',
           'There is no join button in this console. Somebody invites her, as a person.'],
          ['She never opens a DM first',
           'Answering an unsolicited DM is normal behaviour. Starting thousands of them is ' +
           'the definition of spam, and it is not something this console can do.'],
          ['She never reconnects on a refused token',
           'Once Discord rejects the session it stays rejected until you sign in again. ' +
           'Hammering a dead token is what turns a lockout into a ban.']
        ]) +
        '<p class="fg-note">None of that makes it allowed. It makes it quieter. Treat the ' +
        'account as something you are prepared to lose.</p>'
    },
    {
      stage: 'connect', nav: 'Sign her in',
      title: 'Pick the model and sign in to Discord',
      sub: 'Each model holds one Discord account. Connecting one never touches the others.',
      fields: ['dc-account-row', 'dc-signin-row', 'dc-signin-hint', 'dc-token-details',
               'account-status'],
      do: [
        'Pick the model this Discord account belongs to.',
        'Press <b>Sign in to Discord</b> — a real Discord login page opens in a window.',
        'Sign in as her: password, the human check, 2FA and any emailed device code all ' +
        'happen on Discord\'s own page.',
        'Wait for the pill next to the dropdown to turn green.'
      ],
      check: 'The pill shows her username in green, and the line underneath names the account.',
      done: function () {
        var p = document.getElementById('conn-pill');
        return !!(p && p.classList.contains('ok'));
      },
      body:
        '<p>The sign-in runs in a browser we host, on Discord\'s own page. Nothing you type ' +
        'reaches this app — what comes back is the session her browser was already using, ' +
        'plus the build and capabilities that account really identified with. Those two are ' +
        'what her gateway then claims to be, which is the whole point of signing in this way ' +
        'rather than pasting something.</p>' +
        dl([
          ['Choose a model (persona)',
           'Which of your models this account belongs to. Her persona decides how every ' +
           'reply sounds; this console only decides where and when she sends them.'],
          ['Sign in to Discord',
           'Opens the hosted window. If it closes before the pill turns green, the sign-in ' +
           'was interrupted — just press it again, nothing is half-saved.'],
          ['Disconnect',
           'Forgets the stored session. Her settings, channels and history stay; only the ' +
           'account link goes.'],
          ['Paste a token instead',
           'The fallback for a deployment with no sign-in browser. It works, but a pasted ' +
           'token brings no build or capabilities with it, so she identifies with defaults ' +
           'rather than with what her own client really sent. Prefer the window.']
        ]) +
        '<p>Nothing else on this page does anything until that pill is green: every round ' +
        'reads and writes as the connected account.</p>'
    },

    {
      stage: 'reply', nav: 'The master switch',
      title: 'Switch her replies on',
      sub: 'One switch decides whether anything on this page happens at all.',
      needs: 'connected',
      fields: ['dc-auto-row'],
      do: [
        'Tick <b>Auto-reply is on</b>.',
        'Leave the rest of this stage at its defaults for now — the next steps explain each one.'
      ],
      check: 'The switch is on. Nothing sends yet — the settings below still need a <b>Save</b>.',
      done: function () { return on('auto-enabled'); },
      body:
        '<p>With this off she is simply a signed-in account that sits there. With it on she ' +
        'does exactly two things: she answers <b>DMs</b>, and she answers when somebody ' +
        '<b>@mentions her</b> in a channel you have allowed. She does not read channels she ' +
        'has not been allowed into, and she does not speak first anywhere — that is a ' +
        'separate switch, two steps along.</p>' +
        '<p class="fg-note">Off is the safe state. If anything looks wrong later, this is the ' +
        'one control to reach for.</p>'
    },
    {
      stage: 'reply', nav: 'Pace and volume',
      title: 'How much she answers, and how fast',
      sub: 'These three numbers are what stands between "a busy person" and "a script".',
      needs: 'connected',
      fields: ['dc-pace-row'],
      do: [
        'Leave <b>Most replies per round</b> low while you are testing — 3 to 5.',
        'Set <b>Typing speed</b> to something a person could plausibly type.',
        'Set <b>Reaction rate</b>, or 0 if you would rather she never reacts.'
      ],
      check: 'The three numbers read the way you want them. Press <b>Save</b> on the next step.',
      body:
        dl([
          ['Most replies per round',
           'A round is one pass over everything waiting for her. This caps how many messages ' +
           'she sends in that pass, so a backlog of forty DMs does not go out as forty ' +
           'messages in one burst. The rest wait for the next round.'],
          ['Typing speed (chars/sec)',
           'How fast her typing indicator advances, and therefore how long a reply takes to ' +
           'arrive. 14 is an ordinary phone-typing pace. Push it to 40 and a paragraph lands ' +
           'in two seconds, which nobody does.'],
          ['Reaction rate (%)',
           'How often she reacts with an emoji instead of, or before, replying. Unlike most ' +
           'platforms Discord has a real reaction button, so this is a genuine reaction and ' +
           'not a message. 0 switches it off.']
        ]) +
        '<p>If you are unsure, the shipped defaults are deliberately unremarkable. The number ' +
        'worth lowering first is the reply cap, not the typing speed.</p>'
    },
    {
      stage: 'reply', nav: 'Sounding human',
      title: 'Human pacing, and who may write to her',
      sub: 'Three switches: how a reply is delivered, and whose messages she will take.',
      needs: 'connected',
      fields: ['dc-human-row'],
      do: [
        'Leave <b>Human pacing</b> on.',
        'Decide whether she answers people who are not friends yet (<b>Accept DM requests</b>).',
        'Decide whether she accepts <b>friend requests</b> at all.'
      ],
      check: 'The three switches match how you want her to behave.',
      body:
        dl([
          ['Human pacing',
           'She pauses to "read", shows as typing, and splits a long answer across a message ' +
           'or two — the way somebody on a phone actually writes. Off, replies land instantly ' +
           'and whole, which is the single most obvious tell there is. There is no good ' +
           'reason to turn this off.'],
          ['Accept DM requests',
           'Discord puts a message from a non-friend into a request tray. On, she answers ' +
           'those, which is where most new fans arrive. Off, she only talks to people already ' +
           'connected to her. Either way <b>she never opens a DM first</b> — that is not a ' +
           'setting, it is a rule, because unsolicited DMs are exactly what gets an account ' +
           'terminated.'],
          ['Accept friend requests',
           'Auto-accepts incoming friend requests. Convenient, and it also makes her look ' +
           'indiscriminate if the volume is high. Off is the conservative choice; she can ' +
           'still answer a DM request without being friends.']
        ])
    },
    {
      stage: 'reply', nav: 'Save and test',
      title: 'Save the reply settings, then run one round by hand',
      sub: 'A round on demand is the fastest way to find out something is wrong.',
      needs: 'connected',
      fields: ['dc-auto-save', 'auto-status'],
      do: [
        'Press <b>Save</b>.',
        'Press <b>Run a round now</b>.',
        'Read the line underneath: it says what she did, or why she did nothing.'
      ],
      check: 'The status line reports a completed round rather than an error.',
      body:
        '<p><b>Save</b> writes everything in this stage at once — the master switch, the three ' +
        'numbers and the three toggles. Nothing above takes effect until you press it.</p>' +
        '<p><b>Run a round now</b> does immediately what the background loop would do on its ' +
        'own schedule: read what is waiting, answer what qualifies, stop at the reply cap. It ' +
        'is the honest test, because it uses the real account and the real settings.</p>' +
        '<p class="fg-note">The always-on loop needs a host that stays awake, so it runs on ' +
        'Cloud Run and is off on Vercel. On Vercel, this button is how a round happens at all.</p>'
    },

    {
      stage: 'public', nav: 'Channels',
      title: 'The channels she may speak in',
      sub: 'Being in a server is not permission to talk in it. This list is the permission.',
      needs: 'connected',
      fields: ['sec-channels'],
      do: [
        'Press <b>Add a channel</b> and paste a channel ID.',
        'Tick <b>nsfw</b> only where the channel really is marked NSFW.',
        'Tick <b>scheduled</b> only in channels where an unprompted post is welcome.',
        'Press <b>Save channels</b>.'
      ],
      check: 'The channels you want appear in the list and survive a refresh.',
      body:
        '<p>An empty list means she says nothing in public anywhere — she will still answer ' +
        'DMs. That is a perfectly good way to run her, and it is the lowest-risk one.</p>' +
        '<p>To get a channel ID: in Discord, <b>Settings → Advanced → Developer Mode</b>, then ' +
        'right-click the channel and <b>Copy Channel ID</b>. It is a long number.</p>' +
        dl([
          ['nsfw',
           'Tells her the channel is age-gated, so she may match its register. Tick it only ' +
           'where Discord itself marks the channel NSFW — being explicit in a normal channel ' +
           'is a report waiting to happen.'],
          ['scheduled',
           'Lets the scheduled-posting step, two along, post into this channel. A channel ' +
           'without this tick is answer-only.'],
          ['Remove',
           'Takes the channel off the list. She goes quiet there immediately; nothing else ' +
           'is lost.']
        ]) +
        '<p class="fg-note">Whatever you put here, a channel never runs the funnel: no warming ' +
        'up, no nudging, no offer, no link. A room full of people is the fastest place to lose ' +
        'an account over one.</p>'
    },
    {
      stage: 'public', nav: 'Joining in',
      title: 'Whether she ever speaks without being spoken to',
      sub: 'The only thing on this page she does unprompted — and the one that costs money.',
      needs: 'connected',
      fields: ['sec-chime'],
      do: [
        'Decide whether to tick <b>Let her join in</b> at all.',
        'Set a long <b>Wait between</b> — 45 minutes or more.',
        'Set a low <b>Most per channel per day</b>. Six is already chatty.',
        'Fill in <b>keywords</b> so she only joins conversations she has something to say about.',
        'Press <b>Save</b>.'
      ],
      check: 'The switch and the two caps read the way you want, and the status line confirms the save.',
      body:
        '<p>Answering a mention is free: somebody asked for her. This is the opposite — she ' +
        'reads an allowed channel and decides to say something. It is what makes her feel ' +
        'present in a server, and it is also the behaviour that most looks automated if the ' +
        'rate is high.</p>' +
        dl([
          ['Let her join in',
           'The master switch for unprompted messages. Off, she only ever answers.'],
          ['Wait between (minutes)',
           'The minimum gap between two unprompted messages anywhere. This is the setting ' +
           'that keeps her from looking like she is watching the channel.'],
          ['Most per channel per day',
           'A hard daily ceiling per channel. It is also a cost ceiling: every chime-in is a ' +
           'model call on a channel that may be quiet. Zero silences chime-ins entirely — and ' +
           'silences scheduled posting with it, since they share the same daily budget.'],
          ['Only when someone says one of these',
           'Comma-separated keywords. She only joins a conversation containing one of them, ' +
           'which is how you keep her on the topics her persona actually has something to say ' +
           'about. Leave it empty and she may join anything in an allowed channel, within the ' +
           'caps above.']
        ]) +
        '<p class="fg-note">If you only change one number on this page, make it the daily cap. ' +
        'A low cap costs you nothing and is the difference between a member and a bot.</p>'
    },
    {
      stage: 'public', nav: 'Scheduled posts',
      title: 'Posting on a clock',
      sub: 'Messages into the channels you ticked <em>scheduled</em>, on a timer rather than in answer to anyone.',
      needs: 'connected',
      fields: ['sec-posting'],
      do: [
        'Tick <b>Post on a schedule</b> if you want it at all.',
        'Set <b>How often</b> — 240 minutes or more reads as a person with a life.',
        'Optionally write a <b>brief</b> for what she posts about.',
        'Press <b>Save</b>, then <b>Post once now</b> to see one for real.'
      ],
      check: 'A post appears in one of your scheduled channels, and the status line says so.',
      body:
        '<p>This posts into the channels you ticked <b>scheduled</b> on the previous step — ' +
        'nowhere else. If no channel is ticked, nothing happens however this is set.</p>' +
        dl([
          ['Post on a schedule',
           'The switch. Off, she only ever answers and chimes in.'],
          ['How often (minutes)',
           'The interval between posts. It draws on the same daily budget as joining in, so a ' +
           'chime-in cap of zero silences this too, no matter what is set here.'],
          ['What she should post about',
           'An optional brief — "whatever she is up to that day", "the game she is playing". ' +
           'Leave it empty and she draws from her own persona, which is usually the more ' +
           'natural result.'],
          ['Post once now',
           'Fires a single post immediately, into a scheduled channel. The right way to see ' +
           'what her posts actually read like before you leave it running.']
        ]) +
        '<p class="fg-note">A scheduled post never carries a link. Same rule as everything ' +
        'else in a channel.</p>'
    },

    {
      stage: 'live', nav: 'The offer',
      title: 'When she mentions the paid link',
      sub: 'In a DM only, after a real conversation — never in a channel.',
      needs: 'connected',
      fields: ['sec-conversion'],
      do: [
        'Set <b>First offer after this many messages</b> — how long she talks before mentioning it.',
        'Set <b>And not again for this many</b> — how long before she may mention it again.',
        'Press <b>Save</b>.'
      ],
      check: 'Both numbers read the way you want. They apply to DMs only.',
      body:
        '<p>Discord has no paid-message feature, so there is nothing to unlock in the chat ' +
        'itself. Her "offer" is her paid link, sent in a DM once the conversation has earned ' +
        'it, and counted when somebody clicks it.</p>' +
        dl([
          ['First offer after this many messages',
           'How many messages into a DM before she may mention the link at all. Low turns her ' +
           'into an advert; too high and she never gets to the point. Six is a reasonable ' +
           'starting place.'],
          ['And not again for this many',
           'The cooling-off gap. After an offer she goes back to talking for this many ' +
           'messages before the link is allowed again — which is what stops a DM turning into ' +
           'a sales loop.']
        ]) +
        '<p><b>links sent</b> and <b>links opened</b> on the Overview tab count these. A click ' +
        'is a click: it is not a sale, and the difference between the two numbers is the most ' +
        'useful thing on that tab.</p>' +
        '<p class="fg-note">Neither number can put a link in a channel. <code>_dc_channel_round</code> ' +
        'sits outside the shared reply round precisely so that it cannot.</p>'
    },
    {
      stage: 'live', nav: 'Watch her work',
      title: 'The log, and what the Overview tab is telling you',
      sub: 'Read one full conversation here before you leave her running.',
      fields: ['sec-log'],
      do: [
        'Press <b>Refresh</b> and read the last few lines.',
        'Find one DM that went received → sent, and read what she actually said.',
        'Check <b>Overview</b> for anything red before you walk away.'
      ],
      check: 'You can see her answering, and nothing in the log is an error you do not understand.',
      body:
        '<p>Every line is one thing she did, newest last. The icons are the stage: ' +
        '<code>←</code> received, <code>→</code> sent, <code>🛡</code> a guardrail stopped ' +
        'something, <code>⏭</code> skipped, <code>🎯</code> the funnel moved, <code>⚠</code> an ' +
        'error. <b>Clear</b> empties it; it is a log, not a record of anything she needs.</p>' +
        '<p>The other three tabs of this console are:</p>' +
        dl([
          ['Overview',
           'Whether she is working, in one sentence, plus the single next thing to fix if she ' +
           'is not. The counts — replies, links sent, links opened — live here.'],
          ['Inbox',
           'The conversations themselves, so you can read a DM as the fan sees it and send ' +
           'one by hand if you want to take over.'],
          ['Advanced',
           'This log. Nothing here needs setting; it is where you look when something is off.']
        ]) +
        '<p class="fg-note">A gateway connection does not survive a redeploy. If the Overview ' +
        'tab says "signed in, but not connected" after a quiet spell, that is usually all it ' +
        'is — the account is still linked.</p>'
    }
  ];

  var TOUR = [
    { anchor: '#fg-step', placement: 'top',
      title: 'One thing at a time',
      body: 'Setup is split into a handful of short steps. Each one covers a single part of the Discord console and hands you the real controls for it — what you change here is changed for real.' },

    { anchor: '.fg-do', placement: 'bottom',
      title: 'What to do here',
      body: 'Every step opens with the moves for it, in the order you meet them — work down the list. The line under the box below tells you how to know it worked.' },

    { anchor: '#fg-step-body', placement: 'bottom',
      title: 'This is the actual setting',
      body: 'Whatever appears in this box is the console\'s own field, moved here for this step. Fill it in and save it with the button in the step — the same button you would press on the full page.' },

    { anchor: '.cn-tabs', placement: 'bottom',
      title: 'Where you are',
      body: '{stages} stages, in the same tab bar the console itself uses. Each tab counts the steps you have done in it, and clicking one jumps straight there — the pills underneath are the steps inside the stage you are on.' },

    { anchor: '.fg-foot', placement: 'top',
      title: 'Continue, or skip ahead',
      body: 'Continue moves on to the next step. If you would rather see every setting at once, "Skip guide" hands you the full console — and you can come back to this guide any time.' }
  ];

  window.ConsoleGuide.create({
    name: 'DiscordGuide',
    railTitle: 'Discord setup',
    storageKey: 'dcGuide',
    scroll: '.fv-scroll',
    stages: STAGES,
    steps: STEPS,
    tour: TOUR,
    gate: 'Sign her in to Discord first — this step works on her real account, ' +
          'so there is nothing to show until then.',
    done: {
      sub: 'Everything you filled in is saved on the console behind this screen. ' +
           'Watch one full DM in the activity log before you leave her running.',
      logId: 'trace',
      logDesc: 'See her replies, chime-ins and errors as they happen.',
      resetDesc: 'Erase this model\'s Discord setup — channels, limits, settings — and begin again.'
    },
    reset: {
      what: 'Discord setup',
      erased: [
        'Every channel on her allowed list, with its nsfw and scheduled ticks',
        'All reply, chime-in and posting settings, back to their defaults',
        'The offer pacing',
        'The activity log'
      ],
      kept: [
        'Her persona, voice and photos — those live in the persona builder',
        'Her Discord account itself, and every conversation in it',
        'Her friends and the servers she is in'
      ],
      dropLabel: 'Also disconnect her Discord account',
      dropNote: '— untick to keep the session and only clear the setup',
      jobs: function (slug, drop, http) {
        // Order matters: replies stop before anything they read is deleted, so
        // a run that dies half way leaves her off rather than live on a
        // half-erased setup.
        var jobs = [
          ['Stopping her replies', function () {
            return http.post('/api/discord/auto', {
              persona: slug, enabled: false, reply_limit: 10, typing_speed: 14,
              react_rate: 25, humanize: true, ppv_first_after: 6, ppv_gap: 8 });
          }],
          ['Silencing chime-ins and scheduled posts', function () {
            return http.post('/api/discord/chime', {
              persona: slug, enabled: false, cooldown_min: 45, daily_cap: 6,
              keywords: [], auto_accept: true, auto_accept_friends: false,
              post_enabled: false, post_interval_min: 240, post_brief: '' });
          }],
          ['Clearing her channels', function () {
            return http.post('/api/discord/guilds', { persona: slug, allow: [] });
          }],
          ['Clearing the activity log', function () {
            return http.del('/api/discord/trace?persona=' + encodeURIComponent(slug));
          }]
        ];
        if (drop) {
          jobs.push(['Disconnecting her Discord account', function () {
            return http.del('/api/discord/connect?persona=' + encodeURIComponent(slug));
          }]);
        }
        return jobs;
      }
    }
  });
})();
