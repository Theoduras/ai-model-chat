// The X setup wizard: what it says, step by step. The frame it says it in —
// the stages, the rail, the tour, the borrowing of the console's own controls
// — is js/console-guide.js, shared with the Fanvue guide.
(function () {

  var STAGES = [
    { key: 'connect', label: 'Connect', icon: '🔌' },
    { key: 'voice',   label: 'Her voice', icon: '🗣' },
    { key: 'reach',   label: 'Who she talks to', icon: '👥' },
    { key: 'hand',    label: 'By hand',   icon: '🔧' },
    { key: 'live',    label: 'Go live', icon: '◎' }
  ];

  function dl(rows) {
    return '<dl class="fg-dl">' + rows.map(function (r) {
      return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>';
    }).join('') + '</dl>';
  }

  var STEPS = [
    {
      stage: 'connect', nav: 'How it works',
      title: 'What you are about to set up',
      sub: '{stages} stages. You do each one right here — this is the console, not a copy of it.',
      fields: [],
      do: [
        'Connect her X account, so she can read her DMs and post as herself.',
        'Set how she writes and how fast she warms a fan up.',
        'Choose who she talks to: the DMs already open, the feed, and new people.',
        'Switch <b>Always on</b> so it keeps running with this tab closed.'
      ],
      check: 'Nothing to fill in on this step — <b>Continue</b> starts the first one.',
      body:
        '<p>This wizard links one of your models to one X account, then lets her work it on ' +
        'her own: answering DMs in her voice, commenting on the feed, opening chats with new ' +
        'people, and sending everyone she warms up to your funnel link.</p>' +
        '<p>Every step hands you the console\'s own controls, so what you change here is ' +
        'changed for real — and each step saves the way it does on the full page.</p>' +
        '<p class="fg-note">You can leave at any point with <b>Skip guide</b> and finish by ' +
        'hand, or wipe this model\'s X setup and begin again with <b>Start over</b>.</p>'
    },
    {
      stage: 'connect', nav: 'Connect her account',
      title: 'Pick the model and connect X',
      sub: 'Each model holds one X account. Connecting one never touches the others.',
      fields: ['x-account-row', 'connect-panel'],
      do: [
        'Pick the model this X account belongs to.',
        'Press <b>Connect X</b> and fill in the app\'s Client ID and Redirect URI.',
        'Sign in as her in the window that opens and approve the request.',
        'Paste the URL you land on back into the box, if the console asks for it.'
      ],
      check: 'The pill next to the dropdown turns green and shows her @handle.',
      done: function () {
        var p = document.getElementById('conn-pill');
        return !!(p && p.classList.contains('ok'));
      },
      body:
        '<p>X has no one-click connect for this: you need an app of your own on the X ' +
        'developer portal, and its <b>Client ID</b> and <b>Redirect URI</b> go in the two ' +
        'fields here. The secret is optional and is only stored if your app is confidential.</p>' +
        '<p>Nothing else on this page does anything until that pill is green — every round ' +
        'reads and writes as the connected account.</p>'
    },
    {
      stage: 'voice', nav: 'How she replies',
      title: 'How she sounds, and how fast she follows up',
      sub: 'The same voice rules every round uses — DMs, comments and openers alike.',
      fields: ['sec-replies'],
      needs: 'connected',
      do: [
        'Leave <b>Bot active</b> on, or nothing will answer a DM.',
        'Keep <b>Type like a human</b> on unless you want instant replies.',
        'Pick a typing speed that matches how she writes.',
        'Decide how long a fan may go quiet before she nudges, then <b>Save</b>.'
      ],
      check: 'The line under the buttons reads back what you just set.',
      body:
        '<p><b>Bot active</b> is the master switch for replies. <b>Type like a human</b> adds a ' +
        'pause to read and a delay scaled to the length of the reply — X has no typing ' +
        'indicator, so that delay is the only signal a person is there.</p>' +
        '<p><b>Follow up on quiet fans</b> nudges a chat that went cold, at most twice, after ' +
        'the number of minutes you set.</p>'
    },
    {
      stage: 'reach', nav: 'Keep chats going',
      title: 'The audience she already has',
      sub: 'DMs, comments on posts, and the feed she works every round.',
      fields: ['sec-chat'],
      needs: 'connected',
      do: [
        'Leave <b>Reply to incoming DMs</b> ticked — that is the funnel.',
        'Tick <b>Work the feed</b> to comment on fresh posts and answer the people replying.',
        'Set how many comments and answers one round may write.',
        'Tick <b>Post new content</b> if she should post on her own too.'
      ],
      check: 'Nothing saves with a button here — the settings are stored as you change them.',
      body:
        '<p>These are the sub-rounds. Each one is a switch plus a per-round limit, and the ' +
        'limits are deliberately small: a round that does four things looks like a person, a ' +
        'round that does forty looks like a script and gets the account suspended.</p>' +
        '<p>The daily ceilings on the next stage sit on top of these, so a busy day cannot add ' +
        'up to a ban however often the round runs.</p>'
    },
    {
      stage: 'reach', nav: 'Find new people',
      title: 'Cold outreach',
      sub: 'New chats with people who have never messaged her.',
      fields: ['sec-gather'],
      needs: 'connected',
      do: [
        'Set how many new chats one round may open — two is a safe start.',
        'Narrow the age windows so she only reaches people who just posted.',
        'Tick <b>Gather new chats when running unattended</b> to include this in the loop.',
        'Tick <b>Follow new people first</b> if she should follow before writing.'
      ],
      check: 'Anyone she already has a conversation with is skipped automatically.',
      body:
        '<p>She looks through her own audience — recent followers and people who replied to ' +
        'her posts — and writes a first message that reacts to what they just posted.</p>' +
        '<p>The age windows are the quality control: somebody who posted four minutes ago is ' +
        'at their phone, somebody who posted last Tuesday is not.</p>'
    },
    {
      stage: 'live', nav: 'Always on',
      title: 'Run it with the tab closed',
      sub: 'The server runs the whole round on a schedule, with a ceiling on each day.',
      fields: ['sec-always'],
      needs: 'connected',
      do: [
        'Tick <b>Run unattended</b>.',
        'Set how many minutes there are between server rounds.',
        'Set the daily ceilings — the defaults are deliberately conservative.',
        'Close the tab. It keeps running.'
      ],
      check: 'The manual Start buttons grey out, which is how you know the server has it.',
      done: function () {
        var c = document.getElementById('auto-always');
        return !!(c && c.checked);
      },
      body:
        '<p>Without this, every round comes from this browser tab and the funnel stops when ' +
        'you close it. With it, the server runs the same round on its own.</p>' +
        '<p>The daily caps are the reason it is safe to leave alone: once a day\'s DMs, ' +
        'follows, comments, likes or posts are spent, the next round is trimmed rather than ' +
        'skipped, and says so in the log. They reset at midnight UTC.</p>'
    },
    {
      stage: 'hand', nav: 'Post something',
      title: 'Writing a post by hand',
      sub: 'Everything so far was her answering. This is her starting the conversation.',
      needs: 'connected',
      fields: ['sec-post'],
      do: [
        'Optionally write a <b>topic</b> — leave it blank and she picks one herself.',
        'Press <b>Preview draft</b> and read it.',
        'Press <b>Post now</b> only once you are happy with the words.'
      ],
      check: 'The result box shows the posted tweet, and it is on her timeline.',
      body:
        '<p>Writes an original post in her voice and puts it on her account. Nothing on this ' +
        'page posts on a schedule \u2014 for that, use the planner, which writes one row per slot ' +
        'and fires them server-side.</p>' +
        dl([
          ['Topic / vibe',
           'A one-line brief \u2014 "lazy Sunday", "new photoset teaser", "gym day". Left blank, ' +
           'she picks something from her persona, which is fine for filler and rarely the ' +
           'best post of the week.'],
          ['Preview draft',
           'Writes it and shows it to you without posting. Free, and worth pressing every ' +
           'time \u2014 the difference between her voice and something almost her voice is ' +
           'easiest to catch here.'],
          ['Post now',
           'Posts it as her, immediately. There is no undo in this console; delete it on X if ' +
           'it was wrong.']
        ])
    },
    {
      stage: 'hand', nav: 'Work a thread',
      title: 'Replying to the comments under a post',
      sub: 'Hers or anyone\'s \u2014 this is where a post turns into conversations.',
      needs: 'connected',
      fields: ['sec-comments'],
      do: [
        'Paste a <b>post URL or tweet ID</b>, or skip the field entirely.',
        'Pick <b>How many</b> \u2014 start at 3.',
        'Press <b>Preview drafts</b> and read all of them.',
        'Then <b>Reply for real</b>, or <b>Reply on my recent posts</b> to work her own timeline.'
      ],
      check: 'The result box lists the replies she sent, and they read like her.',
      body:
        '<p>A post with comments under it is the cheapest reach there is: the people commenting ' +
        'are already engaged, and a reply from her puts her in front of everyone else reading ' +
        'the thread.</p>' +
        dl([
          ['Post URL or tweet ID',
           'Any post \u2014 hers, or somebody else\'s whose comments are worth answering. Note ' +
           'that X may block replying where she is neither the author nor mentioned; if the ' +
           'Overview says feed commenting is paused, that is the app\'s access level, not a ' +
           'bug, and it is raised in the X developer portal.'],
          ['How many',
           'How many comments she answers in one pass. Three is a sensible first go: enough to ' +
           'see the pattern, small enough to undo by hand.'],
          ['Preview drafts',
           'Writes every reply and shows them without sending. Always do this the first time ' +
           'on an unfamiliar thread.'],
          ['Reply on my recent posts',
           'Ignores the URL field and answers new comments across her own latest posts. This ' +
           'is the one to press regularly \u2014 it is the maintenance that keeps her threads ' +
           'looking alive.']
        ])
    },
    {
      stage: 'hand', nav: 'Open a DM',
      title: 'Starting one conversation by hand',
      sub: 'The same thing cold outreach does, one person at a time and with your eyes on it.',
      needs: 'connected',
      fields: ['sec-dm'],
      do: [
        'Put a <b>@handle</b> or profile URL in the target field.',
        'Add a <b>note</b> \u2014 why her, what she liked \u2014 so the opener is not generic.',
        'Press <b>Preview opener</b> and read it.',
        'Press <b>Send DM</b> if it is right.'
      ],
      check: 'The result box shows the message that went out.',
      body:
        '<p>This is the manual version of the cold-outreach stage: one person, one opener, ' +
        'checked before it goes. Worth using on somebody who matters rather than leaving it ' +
        'to the automatic pass.</p>' +
        dl([
          ['Target @handle or profile URL',
           'Either form works. X only lets a DM through if that account accepts DMs from ' +
           'people it does not follow \u2014 a failure here is usually their setting, not ours.'],
          ['Context / note',
           'What she should seem to already know: "liked 3 of my posts", "into gaming". This ' +
           'is the difference between an opener that reads as noticed and one that reads as ' +
           'a mail-merge.'],
          ['Preview opener',
           'Writes it without sending. Always press it before sending to somebody you care ' +
           'about reaching.']
        ]) +
        '<p class="fg-note">Once she has sent the first message, the ordinary reply loop takes ' +
        'the conversation over \u2014 you do not have to keep coming back here.</p>'
    },
    {
      stage: 'hand', nav: 'Follow and unfollow',
      title: 'The follow button, and the log underneath everything',
      sub: 'A follow is often the cheapest way to get noticed.',
      needs: 'connected',
      fields: ['sec-follow', 'sec-log'],
      do: [
        'Paste a <b>@handle</b> and press <b>Follow</b>.',
        'Use <b>Unfollow</b> to tidy up afterwards.',
        'Read the activity log underneath to see what the automatic passes have been doing.'
      ],
      check: 'The result box confirms it, and the log shows her recent rounds.',
      body:
        dl([
          ['Follow / Unfollow',
           'Follows or unfollows as her, one account at a time. A follow often puts her in ' +
           'somebody\'s notifications where a DM would not arrive at all. Follow-churn \u2014 ' +
           'following hundreds and unfollowing them a day later \u2014 is a well-known spam ' +
           'signal, so use it deliberately.'],
          ['Activity log',
           'Everything auto-chat and gathering have done, newest last. If a round did less ' +
           'than you asked for, the reason is here rather than in the Overview tab.']
        ]) +
        '<p>The rest of the <b>Advanced</b> tab is exactly these hand-tools. Nothing there ' +
        'needs setting for her to run \u2014 it is where you go to do one thing yourself.</p>'
    },
    {
      stage: 'live', nav: 'Watch the first round',
      title: 'Before you leave her to it',
      sub: 'One round with your eyes on it is worth a day of guessing.',
      fields: [],
      do: [
        'Close the guide and open the <b>Overview</b> tab.',
        'Read what she sent under <b>Inbox</b> — the funnel phase is next to each fan.',
        'If a reply is wrong, send one yourself from the inbox; she picks the chat up after.',
        'Check the <b>Advanced</b> log if a round does less than you asked.'
      ],
      check: 'The Overview hero reads <b>She is live and running on her own</b>.',
      body:
        '<p>The Overview tab says in one line whether she is working and what the single next ' +
        'thing to fix is. The Inbox is every conversation she has, and you can take any of ' +
        'them over by typing in the box under the thread.</p>' +
        '<p class="fg-note">The one thing worth checking by hand is the funnel link. If she has ' +
        'nothing to send fans to, every warmed-up chat dead-ends.</p>'
    }
  ];

  var TOUR = [
    { anchor: '#fg-step', placement: 'top',
      title: 'One thing at a time',
      body: 'Setup is split into a handful of short steps. Each one covers a single part of the X console and hands you the real controls for it — what you change here is changed for real.' },

    { anchor: '.fg-do', placement: 'bottom',
      title: 'What to do here',
      body: 'Every step opens with the moves for it, in the order you meet them — work down the list. The line under the box below tells you how to know it worked.' },

    { anchor: '#fg-step-body', placement: 'bottom',
      title: 'This is the actual setting',
      body: 'Whatever appears in this box is the console\'s own field, moved here for this step. Change it here and it is changed on the page behind.' },

    { anchor: '.cn-tabs', placement: 'bottom',
      title: 'Where you are',
      body: '{stages} stages, in the same tab bar the console itself uses. Each tab counts the steps you have done in it, and clicking one jumps straight there — the pills underneath are the steps inside the stage you are on.' },

    { anchor: '.fg-foot', placement: 'top',
      title: 'Continue, or skip ahead',
      body: 'Continue moves on to the next step. If you would rather see every setting at once, "Skip guide" hands you the full console — and you can come back to this guide any time.' }
  ];

  window.ConsoleGuide.create({
    name: 'XGuide',
    railTitle: 'X setup',
    storageKey: 'xGuide',
    scroll: '.xbot-scroll',
    stages: STAGES,
    steps: STEPS,
    tour: TOUR,
    gate: 'Connect her X account first — every round here reads and writes as that ' +
          'account, so there is nothing to set until then.',
    done: {
      sub: 'Everything you filled in is saved on the console behind this screen. ' +
           'Watch one full round in the activity log before you leave her to it.',
      logId: 'sec-log',
      logDesc: 'See her DMs, comments and posts as they happen.',
      resetDesc: 'Switch this model\'s X bot off and clear its round settings.'
    },
    reset: {
      what: 'X setup',
      erased: ['The bot, switched off, and unattended rounds stopped',
               'Every round setting, back to its default',
               'The daily ceilings, back to their defaults'],
      kept: ['Her persona, voice and photos — those live in the persona builder',
             'Everything she has already posted or sent on X',
             'The conversation log and the inbox'],
      dropLabel: 'Also disconnect her X account',
      dropNote: '— untick to keep the connection and only clear the setup',
      jobs: function (slug, drop, http) {
        var jobs = [
          ['Switching the bot off', function () {
            return http.post('/api/x/settings', { persona: slug, enabled: false, auto: false });
          }],
          ['Clearing the round settings', function () {
            return http.post('/api/x/settings', {
              persona: slug, humanize: true, followups: true, followup_min: 45,
              typing_speed: 14, interval_min: 15, query: '', post: '', post_topic: '',
              dm_replies: true, new_chats: false, comments: false, respond_own: false,
              post_content: false, follow: false, feed_engage: true,
              feed_comment_posts: true, feed_answer_replies: true, feed_like_replies: true,
              new_chat_limit: 2, comment_limit: 0, feed_post_limit: 4, feed_reply_limit: 8,
              post_age_min: 30, reply_age_min: 15,
              daily_caps: { dms: 40, follows: 40, comments: 30, likes: 100, posts: 4 } });
          }]
        ];
        if (drop) jobs.push(['Disconnecting X', function () {
          return http.post('/api/x/disconnect', { persona: slug });
        }]);
        return jobs;
      }
    },
    afterReset: function () { if (typeof window.loadBehaviour === 'function') window.loadBehaviour(); }
  });
})();
