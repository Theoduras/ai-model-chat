// The Telegram setup wizard: what it says, step by step. The frame it says it
// in is js/console-guide.js, shared with Fanvue, X, Discord, Instagram and
// Threads.
//
// Telegram is the only console offering two genuinely different connections —
// a bot and a real user account — that behave differently for the fan and carry
// very different risk. The connect stage exists mostly to make that choice
// deliberate rather than accidental.
(function () {

  var STAGES = [
    { key: 'connect', label: 'Connect',   icon: '🔌' },
    { key: 'voice',   label: 'Her voice', icon: '💬' },
    { key: 'reach',   label: 'Her fans',  icon: '👥' },
    { key: 'live',    label: 'Go live',   icon: '◎' }
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

  // Which of the two connections this model is being set up on. The two are
  // genuinely alternatives with different risk, so the wizard asks rather than
  // walking everyone past both \u2014 and 'both' is a real answer, because a bot for
  // the fans who arrive by link and an account for the ones she reaches out to
  // is a reasonable way to run her.
  var ROUTE_KEY = 'tgGuideRoute';
  var Route = {
    get: function () {
      try { return localStorage.getItem(ROUTE_KEY) || ''; } catch (e) { return ''; }
    },
    set: function (v) {
      try { localStorage.setItem(ROUTE_KEY, v); } catch (e) {}
      window.TelegramGuide.rerender();
    },
    has: function (v) {
      var r = Route.get();
      return r === v || r === 'both';
    }
  };
  window.TelegramRoute = Route;

  function choiceCards() {
    var r = Route.get();
    var card = function (key, icon, title, desc) {
      return '<button type="button" class="fg-pick' + (r === key ? ' on' : '') +
        '" onclick="TelegramRoute.set(\'' + key + '\')">' +
        '<div class="fg-pick-i">' + icon + '</div>' +
        '<div class="fg-pick-t">' + title + (r === key ? ' \u2713' : '') + '</div>' +
        '<div class="fg-pick-d">' + desc + '</div></button>';
    };
    return '<div class="fg-picks">' +
      card('bot', '\ud83e\udd16', 'A bot',
           'Within Telegram\'s rules. Carries a bot label and a START button, and can never ' +
           'message anyone first.') +
      card('user', '\ud83d\udc64', 'A real account',
           'Looks like a person and can open a conversation. Needs its own phone number, and ' +
           'it is what Telegram bans for.') +
      card('both', '\u267b\ufe0f', 'Both',
           'A bot for fans who arrive by link, an account for the ones she reaches out to. ' +
           'Two connections, one set of settings.') +
      '</div>';
  }

  var STEPS = [
    {
      stage: 'connect', nav: 'How it works',
      title: 'What you are about to set up',
      sub: '{stages} stages. You do each one right here — this is the console, not a copy of it.',
      fields: [],
      do: [
        'Choose how she appears on Telegram: as a bot, as a real account, or both.',
        'Set how she writes and how fast she follows up.',
        'Give her outfits and photos, so she has something to send.',
        'Decide who she talks to, and read the click numbers.',
        'Watch the log before you share her link.'
      ],
      check: 'Nothing to fill in on this step — <b>Continue</b> starts the first one.',
      body:
        '<p>This console runs one of your models on Telegram: fans message her privately and ' +
        'she answers in her voice, follows up when they go quiet, and sends your funnel link ' +
        'once the conversation has earned it. Unlike a public feed, every Telegram ' +
        'conversation here is a private chat, so the funnel runs in all of them.</p>' +
        '<p>The first choice is the big one, and the next step is nothing but that choice: ' +
        'she can run on a <b>bot</b>, on a <b>real account</b>, or on both at once. They are ' +
        'not the same product \u2014 one is inside Telegram\'s rules and cannot message anyone ' +
        'first, the other looks like a person and can.</p>' +
        '<p>Everything after the connect stage is shared: the voice settings, the media and ' +
        'the fan list apply to whichever of the two a conversation is on.</p>' +
        '<p class="fg-note">The reply loop runs on the server, so she keeps answering with ' +
        'this tab closed. That needs a host that stays awake — it runs on Cloud Run and is ' +
        'off on Vercel.</p>'
    },
    {
      stage: 'connect', nav: 'Bot or account?',
      title: 'Choose how she appears on Telegram',
      sub: 'The two routes are not the same product. Pick one \u2014 or both.',
      fields: [],
      do: [
        'Press the one you want. The next step is the one you picked.',
        'Unsure? Open <b>More about this</b> underneath for the two side by side.',
        'You can come back and change it, or add the second route, at any time.'
      ],
      check: 'One of the three is ticked. It decides which connect step comes next.',
      done: function () { return !!Route.get(); },
      render: function (card) {
        var box = document.createElement('div');
        box.innerHTML = choiceCards();
        card.appendChild(box.firstChild);
      },
      body: function () {
        return dl([
          ['Who she looks like',
           '<b>Bot:</b> a bot \u2014 Telegram puts the label on it and there is no hiding it. ' +
           '<b>Account:</b> a person, with her name and photo and nothing marking her out.'],
          ['How a conversation starts',
           '<b>Bot:</b> the fan must press <b>START</b> first. Nothing reaches them before ' +
           'that, ever. <b>Account:</b> no START, and she can write to someone who has never ' +
           'written to her.'],
          ['What it costs to set up',
           '<b>Bot:</b> one click on the shared bot, or a token from @BotFather. ' +
           '<b>Account:</b> its own phone number, api_id and api_hash from my.telegram.org, ' +
           'and a login code every time the session dies.'],
          ['The risk',
           '<b>Bot:</b> none worth naming \u2014 this is what the Bot API is for. ' +
           '<b>Account:</b> automating a user account is against the spirit of Telegram\'s ' +
           'terms, and accounts that message people who have not written first get ' +
           'rate-limited or banned. Use a number you can afford to lose.'],
          ['Reach',
           '<b>Bot:</b> as far as her link travels \u2014 bio, link tree, X, anywhere. ' +
           '<b>Account:</b> the same, plus anyone she can find a handle for. That extra reach ' +
           'is exactly the part that carries the risk.']
        ]) +
        '<p class="fg-note">If you are unsure, take the bot. It is the recommended route, it ' +
        'takes one click, and you can add the account later without redoing anything else ' +
        '\u2014 the voice, media and fan settings are shared by both.</p>';
      }
    },
    {
      stage: 'connect', nav: 'Connect a bot',
      hidden: function () { return !Route.has('bot'); },
      title: 'The bot route — one click, or your own bot',
      sub: 'The safe option, and the one most creators should take.',
      fields: ['sec-bot'],
      do: [
        'Pick the model this Telegram presence belongs to.',
        'Press <b>Connect bot</b>.',
        'Leave <b>Use our bot</b> selected and press <b>Connect with our bot →</b>.',
        'Copy <b>Your link</b> and put it in her bio, link tree, X — anywhere.'
      ],
      check: 'The pill next to the dropdown is green, and you have a share link you can open.',
      done: function () {
        var p = document.getElementById('conn-pill');
        return !!(p && p.classList.contains('ok'));
      },
      body:
        '<p>Each model gets its own Telegram presence. Connecting one never touches another.</p>' +
        dl([
          ['Use our bot <i>(recommended)</i>',
           'One click. You get a personal <b>connect code</b> and a <b>link</b> on the ' +
           'platform\'s shared bot; anyone who opens that link lands straight in a private ' +
           'chat with this model. Nothing to install, no token to keep safe, and the webhook ' +
           'is already wired up.'],
          ['Use my own bot',
           'A bot you create in <a href="https://t.me/BotFather" target="_blank">@BotFather</a> ' +
           'with <code>/newbot</code>. Paste its token and the public HTTPS base URL, and the ' +
           'webhook is registered for you. Worth it when you want her own bot name and avatar ' +
           'rather than the shared one — use <code>/setuserpic</code> and ' +
           '<code>/setdescription</code> in BotFather to brand it.'],
          ['Your connect code',
           'What the link encodes. A fan who already has the bot open can send the code ' +
           'instead of following a link and land in the same place.'],
          ['Disconnect',
           'Unhooks the bot. Her fans, settings and history stay.']
        ]) +
        '<p class="fg-note">A bot cannot message anyone first — that is Telegram\'s rule, not a ' +
        'setting. Everything starts with the fan pressing START, which is exactly why the ' +
        'share link matters more here than on any other platform.</p>'
    },
    {
      stage: 'connect', nav: 'Connect an account',
      hidden: function () { return !Route.has('user'); },
      title: 'The account route — no bot label, no START',
      sub: 'More reach, more risk, and its own phone number.',
      fields: ['sec-account'],
      do: [
        'Read the warning. This is a real account doing something Telegram does not sanction.',
        'Get an <b>api_id</b> and <b>api_hash</b> from my.telegram.org and save them.',
        'Enter her phone number with the country code and press <b>Send login code →</b>.',
        'Type the code Telegram sends, plus the 2FA password if she has one, and sign in.',
        'Press <b>▶ Go online</b>.'
      ],
      check: 'The pill next to the phone number turns green and the Stop button appears.',
      body:
        '<p>This signs in as an actual Telegram user rather than a bot. To a fan she is a ' +
        'person: no "bot" tag, no START button, and she can write to someone who has never ' +
        'written to her.</p>' +
        '<p>That last ability is the whole risk. Telegram rate-limits and bans accounts that ' +
        'message people who have not written first, and there is no appeal worth counting on. ' +
        'Use a number you can afford to lose and keep the volume low.</p>' +
        dl([
          ['api_id / api_hash',
           'Telegram\'s own developer credentials, from <b>my.telegram.org</b>. They identify ' +
           'the client, not the account — one pair is reused across her sessions.'],
          ['Phone number',
           'With the country code. This is the account she will <i>be</i>, so it should not be ' +
           'a number tied to anything else you care about.'],
          ['Login code / 2FA password',
           'Telegram sends the code to that number, in the app. The 2FA password is only asked ' +
           'for if the account has one set.'],
          ['▶ Go online / ■ Stop',
           'Starts and stops her listening. Stop is the control to reach for if anything looks ' +
           'wrong — the session stays signed in.'],
          ['Message someone first',
           'Sends one opener to a @username or phone number, generated in her voice if you ' +
           'leave the message blank. This is the single most dangerous button on the page: ' +
           'every send here is an unsolicited message. Use it sparingly, on people who expect ' +
           'to hear from her.']
        ]) +
        '<p class="fg-note">Running this alongside the bot is fine and is what <b>Both</b> ' +
        'sets up: the two connections are independent, and every setting after this stage ' +
        'applies to whichever one a given conversation is on.</p>'
    },

    {
      stage: 'voice', nav: 'How she replies',
      title: 'How she sounds, and how fast she follows up',
      sub: 'These apply to both the bot and the real account.',
      needs: 'connected',
      fields: ['sec-replies'],
      do: [
        'Leave <b>Replies active</b> on.',
        'Leave <b>Type like a human</b> on.',
        'Decide whether she chases quiet fans, and how long she waits.',
        'Press <b>Save</b>.'
      ],
      check: 'The status line next to Save confirms it.',
      done: function () { return on('f-tg-enabled'); },
      body:
        dl([
          ['Replies active',
           'The master switch. Off, messages arrive and nothing answers them — useful while ' +
           'you are still setting her up.'],
          ['Type like a human',
           'She pauses to read, shows "typing…", and sends a long answer as two or three ' +
           'messages the way a person on a phone does. Off, replies land instantly and whole, ' +
           'which is the most obvious tell there is.'],
          ['Follow up on quiet fans',
           'When a conversation stops, she nudges — at most twice per fan, ever. That cap is ' +
           'fixed: a third nudge is harassment and it is not something this console can do.'],
          ['Typing speed',
           'How fast the typing indicator advances, and therefore how long a reply takes to ' +
           'arrive. "Natural" is an ordinary phone-typing pace.'],
          ['Follow-up delay (minutes)',
           'How long silence has to last before the first nudge. Short reads as desperate; 45 ' +
           'minutes or more reads as somebody who has a life and came back to her phone.']
        ]) +
        '<p class="fg-note">Her actual voice — the words, the warmth, how fast she flirts — is ' +
        'set in the persona builder, not here. This page only decides the delivery.</p>'
    },
    {
      stage: 'voice', nav: 'Outfits and media',
      title: 'What she has to send',
      sub: 'Six outfits, each one consistent look, and the vault behind them.',
      needs: 'connected',
      fields: ['sec-media'],
      do: [
        'Upload photos into the <b>Media Vault</b>, or drop files straight onto it.',
        'Open an outfit and describe it — clothing, place, lighting.',
        'Select photos in the vault and <b>Add to outfit</b>.',
        'Press <b>Save</b> on the section header.'
      ],
      check: 'Each outfit you care about has a description and at least one photo.',
      body:
        '<p>An outfit is one consistent look: same clothing, same place, same lighting. That ' +
        'consistency is what makes a set of photos read as the same person on the same day, ' +
        'rather than a shuffle of unrelated images.</p>' +
        dl([
          ['Media Vault',
           'Everything you upload or generate lands here first. Filter by <b>Unassigned</b> to ' +
           'find what you have not sorted yet. The same photo can sit in more than one outfit.'],
          ['Outfits',
           'Six of them. Open one to write its clothing, place and lighting — those words are ' +
           'what keeps a generated photo matching the ones already in the set.'],
          ['Purpose',
           'What a shot is for: <b>Main</b> (profile), <b>Tease</b> (flirty preview), ' +
           '<b>Behind-the-scenes</b>, <b>Custom</b>. It is how she picks a sensible photo for ' +
           'the moment she is in rather than the newest one.'],
          ['Add a hosted video',
           'A clip over 12MB cannot live here. Host it wherever you like and paste the link — ' +
           'the vault keeps the reference and she sends the link.']
        ]) +
        '<p class="fg-note">With an empty vault she can still talk; she just has nothing to ' +
        'show. On Telegram that is most of the appeal, so this step is worth the time.</p>'
    },

    {
      stage: 'reach', nav: 'Who she talks to',
      title: 'Her fans, and the numbers underneath them',
      sub: 'By default she talks to everyone. This is where you narrow it, or shut somebody out.',
      needs: 'connected',
      fields: ['sec-conversion'],
      do: [
        'Press <b>Load chats from Telegram</b> to pull in who has written to her.',
        'Leave every <b>Talk to</b> box unticked to talk to everyone — that is the default.',
        'Tick <b>Block</b> on anyone she should never answer.',
        'Press <b>Save who she talks to</b>.'
      ],
      check: 'The fan list shows real names, and the state line confirms the save.',
      body:
        '<p>The four counters at the top are the honest read on whether any of this is working:</p>' +
        dl([
          ['Fans',
           'How many people she is in a conversation with.'],
          ['CTAs sent',
           'How many times she has sent your funnel link.'],
          ['Clicks / Click rate',
           'How many of those were opened. A click is a click — it is not a sale — but the gap ' +
           'between sent and clicked tells you whether the offer is landing or whether she is ' +
           'pushing it too early.']
        ]) +
        '<p>The list underneath controls who she answers:</p>' +
        dl([
          ['Talk to',
           'With <b>none</b> ticked she talks to everyone, which is the default and usually ' +
           'the right answer. Tick some and she talks only to those.'],
          ['Block',
           'Shuts a fan out entirely. A block always wins, even over a Talk-to tick.'],
          ['Skipped fans',
           'Get no replies and no follow-ups. Their messages are simply ignored — nothing is ' +
           'queued up waiting for you to change your mind.'],
          ['Load chats from Telegram',
           'Pulls the conversation list off Telegram so the table shows people she has not ' +
           'answered yet, not only ones already in the database.']
        ])
    },

    {
      stage: 'live', nav: 'Watch her work',
      title: 'The log — every message, and what she did with it',
      sub: 'If she has gone quiet, the reason is here.',
      fields: ['sec-log'],
      do: [
        'Read the last few lines.',
        'Find one conversation that went in and back out, and read what she actually said.',
        'Leave <b>live</b> ticked to watch it update as things happen.'
      ],
      check: 'You can see her answering, and nothing in the log is an error you do not understand.',
      body:
        '<p>Every incoming message and what happened to it, newest last, with any problems ' +
        'called out above the rows. This is the first place to look before anything else.</p>' +
        dl([
          ['Refresh / Clear',
           '<b>Clear</b> empties the log. It is a log, not a record of anything she needs — ' +
           'her conversations and her fan list are untouched.'],
          ['live',
           'Keeps the log updating by itself. Unticking it stops the polling, which is worth ' +
           'doing if you are leaving the tab open all day.']
        ]) +
        '<p>The other tabs of this console:</p>' +
        dl([
          ['Overview',
           'Whether she is answering, in one sentence, plus the single next thing to fix if ' +
           'she is not.'],
          ['Inbox',
           'The conversations themselves, so you can read one as the fan sees it and send a ' +
           'message by hand if you want to take over.'],
          ['Advanced',
           'This log, the webhook, and the two settings below. Nothing here needs touching for ' +
           'her to work.']
        ])
    },
    {
      stage: 'live', nav: 'The wiring',
      title: 'The webhook, and why it is already done',
      sub: 'Worth reading once, so you know where to look if messages stop arriving.',
      fields: ['sec-plumbing', 'sec-webhook'],
      do: [
        'Check the <b>Callback URL</b> is filled in.',
        'Nothing to change — this is registered for you when the bot connects.'
      ],
      check: 'The callback URL shows a real https address rather than "connect a bot first".',
      body:
        '<p>Telegram pushes each message to us rather than us polling for them, so replies are ' +
        'immediate. The webhook that receives them is registered automatically the moment a ' +
        'bot connects — there is nothing here to set up.</p>' +
        '<p>Two things are worth knowing:</p>' +
        dl([
          ['The path is unguessable, and checked',
           'Every request is verified against a per-bot secret header, so nobody can send us ' +
           'messages pretending to be Telegram and puppet her.'],
          ['Local development has no webhook',
           'There is no public HTTPS URL on a laptop, so it is skipped and nothing arrives. ' +
           'Deploy, or point the base URL at a tunnel, if you need to receive messages while ' +
           'developing.']
        ]) +
        '<p class="fg-note">If she has stopped receiving anything at all — not replying badly, ' +
        'but silent — this is the second place to look, after the log.</p>'
    },
    {
      stage: 'live', nav: 'The shared bot',
      title: 'The platform bot — operator only, set once',
      sub: 'The bot every creator\'s one-click connect runs on. Skip this if you are not the operator.',
      fields: ['sec-platform'],
      do: [
        'Create one bot in @BotFather with <code>/newbot</code>.',
        'Paste its token and the public base URL here.',
        'Press <b>Save platform bot</b>.'
      ],
      check: 'The pill turns green and names the bot handle.',
      body:
        '<p>This is the shared bot behind <b>Use our bot</b> on the connect step. Set it once ' +
        'for the whole platform and from then on every creator connects with a single click ' +
        'and gets their own link on it — no BotFather, no token to lose.</p>' +
        '<p>It is operator-only on purpose: the token controls every creator\'s Telegram ' +
        'presence at once, so it is not something a creator should be able to see or change.</p>' +
        '<p class="fg-note">If the connect step said the shared bot was missing, this is the ' +
        'step that fixes it.</p>'
    }
  ];

  var TOUR = [
    { anchor: '#fg-step', placement: 'top',
      title: 'One thing at a time',
      body: 'Setup is split into a handful of short steps. Each one covers a single part of the Telegram console and hands you the real controls for it — what you change here is changed for real.' },

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
    name: 'TelegramGuide',
    railTitle: 'Telegram setup',
    storageKey: 'tgGuide',
    scroll: '.xbot-scroll',
    stages: STAGES,
    steps: STEPS,
    tour: TOUR,
    gate: 'Connect her on Telegram first — a bot or a real account — because this step ' +
          'works on the live connection.',
    done: {
      sub: 'The reply loop runs on the server, so she keeps answering with this tab ' +
           'closed. Watch one full conversation in the log before you share her link.',
      logId: 'trace-rows',
      logDesc: 'Every message she receives, and what she did with it.',
      resetDesc: 'Switch this model\'s Telegram replies off and begin again.'
    },
    reset: {
      what: 'Telegram setup',
      erased: [
        'Replies, switched off and back to their defaults',
        'Typing pace and the follow-up delay',
        'Which route you chose \u2014 bot, real account or both',
        'The activity log'
      ],
      kept: [
        'Her persona, voice and photos — those live in the persona builder',
        'Her bot or account connection, and her share link',
        'Her fans, her conversations and her outfits'
      ],
      jobs: function (slug, drop, http) {
        return [
          ['Switching her replies off', function () {
            return http.post('/api/telegram/settings', {
              persona: slug, enabled: false, humanize: true, followups: true,
              typing_speed: 14, followup_min: 45 });
          }],
          ['Clearing the activity log', function () {
            return http.del('/api/telegram/trace?persona=' + encodeURIComponent(slug));
          }],
          ['Forgetting which route you chose', function () {
            try { localStorage.removeItem(ROUTE_KEY); } catch (e) {}
            return Promise.resolve({ ok: true });
          }]
        ];
      }
    }
  });
})();
