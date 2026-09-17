// The Instagram setup wizard: what it says, step by step. The frame it says it
// in is js/console-guide.js, shared with Fanvue, X and Discord.
//
// Instagram is the odd one in this app: it posts and nothing else. No DMs, no
// funnel, no scheduler, no adapter. Half this wizard's job is saying so
// plainly, because every other console here answers people.
(function () {

  var STAGES = [
    { key: 'connect', label: 'Connect',       icon: '🔌' },
    { key: 'media',   label: 'What she posts', icon: '📷' },
    { key: 'words',   label: 'The words',     icon: '📝' },
    { key: 'after',   label: 'Post it',       icon: '◎' }
  ];

  function dl(rows) {
    return '<dl class="fg-dl">' + rows.map(function (r) {
      return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>';
    }).join('') + '</dl>';
  }

  var STEPS = [
    {
      stage: 'connect', nav: 'How it works',
      title: 'What this console does — and what it does not',
      sub: 'Four stages. You do each one right here — this is the console, not a copy of it.',
      fields: [],
      do: [
        'Read the risk step. This is a real account signed in as herself.',
        'Sign her in on Instagram\'s own page.',
        'Pick a format — a feed post, a Story or a Reel.',
        'Give it a photo or video, and either a caption or a brief.',
        'Press <b>Post now</b> and check it landed.'
      ],
      check: 'Nothing to fill in on this step — <b>Continue</b> starts the first one.',
      body:
        '<p>This console posts to one Instagram account as one of your models: ' +
        '<b>Stories, feed posts and Reels</b>. That is the whole feature.</p>' +
        '<p>What it deliberately does not do, so you are not waiting for it:</p>' +
        dl([
          ['No DMs',
           'She does not read or answer Instagram messages. Every other console in this app ' +
           'runs a reply loop; this one has none, which is why there is no Inbox worth ' +
           'opening here.'],
          ['No funnel',
           'There is no warming up, no offer pacing and no paid link. A caption is a caption.'],
          ['No scheduler',
           'Posting happens when you press the button. There is no queue and no timer — the ' +
           'planner does not fire at this channel.']
        ]) +
        '<p>Why it is built this way: Meta\'s official Graph API cannot post Stories <i>at ' +
        'all</i>, and for feed posts and Reels it needs a Business or Creator account plus ' +
        'app review. So this posts through a real sign-in instead, the same hosted window ' +
        'Discord uses — which brings the risk on the next step.</p>' +
        '<p class="fg-note">You can leave at any point with <b>Skip guide</b> and finish by ' +
        'hand, or clear this model\'s Instagram setup with <b>Start over</b>.</p>'
    },
    {
      stage: 'connect', nav: 'The risk',
      title: 'Read this before you connect anything',
      sub: 'A real account, signed in as herself, doing something Instagram\'s terms do not allow.',
      fields: ['ig-warn'],
      do: [
        'Use an account you could lose tomorrow without losing anything else.',
        'Do not use the creator\'s only account, and do not use your own.',
        'Post at a human rate. Three posts in ten minutes is not one.'
      ],
      check: 'Nothing to save here. Continue once you have decided the account is expendable.',
      body:
        '<p>There is no supported way to do this. The official route — the Graph API — cannot ' +
        'post Stories, and gates posts and Reels behind a Business account and Meta app ' +
        'review. So this signs in as her, through a browser we host on Instagram\'s own page, ' +
        'and posts the way her phone would.</p>' +
        '<p>Instagram\'s terms do not permit an automated client. Accounts caught doing it get ' +
        'actioned: posting privileges pulled, shadow-limited reach, or the account gone. ' +
        'Enforcement can also look at the device and the network behind it, which is why an ' +
        'account you care about should not be signed in here.</p>' +
        '<p class="fg-note">The honest summary: this works, and it is against the rules, and ' +
        'the account is the thing at stake. Decide that before you sign in, not after.</p>'
    },
    {
      stage: 'connect', nav: 'Sign her in',
      title: 'Pick the model and sign in to Instagram',
      sub: 'Each model holds one Instagram account. Connecting one never touches the others.',
      fields: ['ig-account-row', 'ig-signin-row', 'ig-signin-hint', 'ig-cookie-details',
               'account-status'],
      do: [
        'Pick the model this Instagram account belongs to.',
        'Press <b>Sign in to Instagram</b> — a real login page opens in a window.',
        'Sign in as her: password, the human check, 2FA and any emailed code all happen on ' +
        'Instagram\'s own page.',
        'Wait for the pill next to the dropdown to turn green.'
      ],
      check: 'The pill reads "connected as @her" in green.',
      done: function () {
        var p = document.getElementById('conn-pill');
        return !!(p && p.classList.contains('ok'));
      },
      body:
        '<p>The window is a browser we host, pointed at Instagram\'s own login. Nothing you ' +
        'type passes through this app — what comes back is the session her browser was ' +
        'already using. That matters: the human check reads the window it runs in, which is ' +
        'why it is a real popup and not a panel inside this page.</p>' +
        dl([
          ['Choose a model (persona)',
           'Which of your models this account belongs to. Her persona is what writes a ' +
           'caption when you leave the caption box empty.'],
          ['Sign in to Instagram',
           'Opens the hosted window. If it closes before the pill turns green the sign-in was ' +
           'interrupted — press it again, nothing is half-saved.'],
          ['Disconnect',
           'Forgets the stored session. Her posts stay up; only the link to the account goes.'],
          ['Paste a cookie instead',
           'The fallback for a deployment with no sign-in browser: the full Cookie header ' +
           'from a signed-in tab. Stored encrypted and never shown again. It works, but a ' +
           'cookie expires and brings none of the sign-in\'s other signals with it, so prefer ' +
           'the window.']
        ]) +
        '<p>Nothing else on this page does anything until that pill is green.</p>'
    },

    {
      stage: 'media', nav: 'Pick a format',
      title: 'Feed post, Story or Reel',
      sub: 'The three formats behave differently, and one of them constrains the file.',
      needs: 'connected',
      fields: ['ig-kind-row'],
      do: [
        'Pick <b>What to post</b>.',
        'Note what it accepts before you pick the file on the next step.'
      ],
      check: 'The dropdown reads the format you want.',
      body:
        dl([
          ['Feed post',
           'A permanent post on her grid. Photo or video. This is the one people find later, ' +
           'so it is the one worth a real caption.'],
          ['Story',
           'Gone in 24 hours, shown full-screen to people who already follow her. Photo or ' +
           'video. The lowest-stakes thing to post, and the one the official API cannot touch ' +
           'at all — it is the reason this console signs in rather than using the Graph API.'],
          ['Reel',
           '<b>Video only.</b> Pick a photo with Reel selected and the post will fail. Reels ' +
           'are the format Instagram pushes to people who do not follow her yet, so it is the ' +
           'one that actually reaches new fans.']
        ])
    },
    {
      stage: 'media', nav: 'The photo or video',
      title: 'Give it something to post',
      sub: 'Upload a file, or have her generate a photo.',
      needs: 'connected',
      fields: ['ig-media-row', 'ig-preview'],
      do: [
        'Either pick a file with <b>Photo or video</b>…',
        '…or press <b>Generate a photo with AI</b> to make one in her likeness.',
        'Check the preview underneath is the right image the right way up.'
      ],
      check: 'A preview thumbnail appears below the file picker.',
      body:
        dl([
          ['Photo or video',
           'Reads the file in your browser and holds it here until you post. The size, shape ' +
           'and, for video, the length and cover frame are read from the file itself — so a ' +
           'clip that Instagram would reject for being too long is rejected at post time, not ' +
           'silently cropped.'],
          ['Generate a photo with AI',
           'Makes a photo from this model\'s persona and her reference photos, so it looks ' +
           'like her rather than like a stock image. Useful for a Story; think harder before ' +
           'putting one on the grid, where it sits next to everything else she has posted.'],
          ['The preview',
           'What will actually go up. If it looks wrong here it will look wrong on Instagram — ' +
           'this is the last place to catch it.']
        ]) +
        '<p class="fg-note">Nothing is uploaded until you press <b>Post now</b>. Changing the ' +
        'model in the dropdown clears the file, on purpose — a photo of one model must not go ' +
        'out on another one\'s account.</p>'
    },

    {
      stage: 'words', nav: 'Caption',
      title: 'The caption — yours, or hers',
      sub: 'Write it, or leave it empty and let her write it in her own voice.',
      needs: 'connected',
      fields: ['ig-caption-field', 'ig-brief-field'],
      do: [
        'Either type the <b>Caption</b> you want, word for word…',
        '…or leave it empty and write a <b>brief</b> instead.',
        'Leave both empty and she posts from her persona alone.'
      ],
      check: 'The caption box says what you want said, or is empty on purpose.',
      body:
        dl([
          ['Caption',
           'Used exactly as typed. Nothing is added and nothing is rewritten, so this is the ' +
           'right box when the words matter — a launch, a date, anything you would not want ' +
           'paraphrased.'],
          ['What she should post about',
           'Only read when the caption is empty. A one-line brief — "gym session", "the game ' +
           'she is playing" — that she turns into a caption in her own voice, with her own ' +
           'punctuation and emoji habits from the persona builder.'],
          ['Both empty',
           'She picks something from her persona. Fine for a Story; on the grid you usually ' +
           'want at least a brief.']
        ]) +
        '<p class="fg-note">There is no offer pacing here and no paid link. This console has ' +
        'no funnel at all, so a caption that sells is a caption you wrote yourself.</p>'
    },

    {
      stage: 'after', nav: 'Post it',
      title: 'Post now, and read what comes back',
      sub: 'One press, one post. There is no queue and nothing to undo.',
      needs: 'connected',
      fields: ['ig-post-row', 'post-status'],
      do: [
        'Press <b>Post now</b>.',
        'Wait — a video takes longer, because it uploads and then Instagram processes it.',
        'Read the status line: it either confirms the post or says why it failed.',
        'Open Instagram and look at it.'
      ],
      check: 'The status line confirms the post, and it is really there on her account.',
      body:
        '<p>Posting happens in three moves: the file is uploaded, the post is configured ' +
        '(format, caption, cover frame), and Instagram publishes it. A Reel can sit in ' +
        'processing for a while after the upload finishes — that is Instagram, not this page ' +
        'hanging.</p>' +
        '<p>Common failures and what they mean:</p>' +
        dl([
          ['"not connected"',
           'The session expired. Sign her in again on the Connect stage; nothing else is lost.'],
          ['A Reel rejecting a photo',
           'Reels are video only. Change the format or the file.'],
          ['A video refused on length or shape',
           'Instagram\'s own limits. Trim or re-crop the clip and try again.']
        ]) +
        '<p class="fg-note">There is no unpost button here. Delete it in the Instagram app if ' +
        'it was wrong.</p>'
    },
    {
      stage: 'after', nav: 'What she has posted',
      title: 'The log, and the rest of this console',
      sub: 'Where to look when a post did not appear.',
      fields: ['sec-log'],
      do: [
        'Press <b>Refresh</b> and read the last few lines.',
        'Check <b>Overview</b> for anything red before you walk away.'
      ],
      check: 'Your post appears in the log.',
      body:
        '<p>Every line is one post attempt, newest last, with what happened to it. It is the ' +
        'first place to look when something you pressed did not appear on her account.</p>' +
        '<p>The other tabs of this console:</p>' +
        dl([
          ['Overview',
           'Whether the account is connected, in one sentence, and the next thing to fix if ' +
           'it is not. There are no counts here — this console posts, it does not converse, ' +
           'so there is nothing to count.'],
          ['Inbox',
           'Empty by design. She does not read Instagram DMs; nothing in this app answers ' +
           'them.'],
          ['Advanced',
           'This log. Nothing here needs setting.']
        ]) +
        '<p class="fg-note">If you want her posting on a schedule, that is Threads and Discord ' +
        'today, not Instagram — though Threads posts off this very Instagram session, so ' +
        'connecting her here is what makes that possible.</p>'
    }
  ];

  var TOUR = [
    { anchor: '#fg-step', placement: 'top',
      title: 'One thing at a time',
      body: 'Setup is split into a handful of short steps. Each one covers a single part of the Instagram console and hands you the real controls for it — what you change here is changed for real.' },

    { anchor: '.fg-do', placement: 'bottom',
      title: 'What to do here',
      body: 'Every step opens with the moves for it, in the order you meet them — work down the list. The line under the box below tells you how to know it worked.' },

    { anchor: '#fg-step-body', placement: 'bottom',
      title: 'This is the actual setting',
      body: 'Whatever appears in this box is the console\'s own field, moved here for this step. Fill it in and press the step\'s own button — the same one you would press on the full page.' },

    { anchor: '.cn-tabs', placement: 'bottom',
      title: 'Where you are',
      body: 'Four stages, in the same tab bar the console itself uses. Each tab counts the steps you have done in it, and clicking one jumps straight there — the pills underneath are the steps inside the stage you are on.' },

    { anchor: '.fg-foot', placement: 'top',
      title: 'Continue, or skip ahead',
      body: 'Continue moves on to the next step. If you would rather see every setting at once, "Skip guide" hands you the full console — and you can come back to this guide any time.' }
  ];

  window.ConsoleGuide.create({
    name: 'InstagramGuide',
    railTitle: 'Instagram setup',
    storageKey: 'igGuide',
    scroll: '.fv-scroll',
    stages: STAGES,
    steps: STEPS,
    tour: TOUR,
    gate: 'Sign her in to Instagram first — this step posts to her real account, ' +
          'so there is nothing to show until then.',
    done: {
      sub: 'Her account is connected and you have posted once. Everything here is a ' +
           'button you press; there is nothing running in the background.',
      logId: 'trace',
      logDesc: 'Every post attempt, and what happened to it.',
      resetDesc: 'Disconnect this model\'s Instagram account and begin again.'
    },
    reset: {
      what: 'Instagram connection',
      erased: [
        'The stored Instagram session for this model'
      ],
      kept: [
        'Her persona, voice and photos — those live in the persona builder',
        'Everything already posted to her account',
        'Her followers, her grid and her Stories'
      ],
      dropLabel: 'Also disconnect her Instagram account',
      dropNote: '— untick and there is nothing left to erase',
      jobs: function (slug, drop, http) {
        if (!drop) return [];
        return [['Disconnecting her Instagram account', function () {
          return http.del('/api/instagram/connect?persona=' + encodeURIComponent(slug));
        }]];
      }
    }
  });
})();
