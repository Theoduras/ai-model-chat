// The Threads setup wizard: what it says, step by step. The frame it says it in
// is js/console-guide.js, shared with Fanvue, X, Discord and Instagram.
//
// Threads is the one console whose connection is usually already done
// somewhere else — a Threads account *is* an Instagram account — so the
// connect stage's job is mostly to explain which of the two modes she is in
// and what each one can and cannot do.
(function () {

  var STAGES = [
    { key: 'connect', label: 'Connect',  icon: '🔌' },
    { key: 'post',    label: 'Posting',  icon: '📝' },
    { key: 'reply',   label: 'Replying', icon: '💬' },
    { key: 'live',    label: 'Go live',  icon: '◎' }
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
        'Connect her — usually by having already connected her on Instagram.',
        'Write a post, or have her write it, and give it photos from her library.',
        'Put a week of posts on the calendar so the server sends them without you.',
        'Switch auto-reply on so she answers comments, mentions and DMs.',
        'Add the webhook if you want replies to be instant rather than polled.'
      ],
      check: 'Nothing to fill in on this step — <b>Continue</b> starts the first one.',
      body:
        '<p>This console runs one of your models on Threads: she posts, she answers the ' +
        'replies underneath her own posts, she answers when somebody mentions her, and she ' +
        'answers DMs.</p>' +
        '<p>Two things shape everything below:</p>' +
        dl([
          ['It keeps running with this tab closed',
           'Posting on a schedule and auto-reply both run on the server, not in your browser. ' +
           'Close the tab and she carries on. That needs a host that stays awake, which is why ' +
           'these loops run on Cloud Run and are off on Vercel.'],
          ['Public is not private',
           'A reply under one of her posts never carries a link, a CTA or an offer — it is a ' +
           'public thread and the fastest way to get an account limited. The funnel runs in ' +
           'DMs only, and even there the offer is her profile or linktree rather than a direct ' +
           'unlock link, because Meta filters known paysite domains.']
        ]) +
        '<p class="fg-note">You can leave at any point with <b>Skip guide</b> and finish by ' +
        'hand, or wipe this model\'s Threads setup and begin again with <b>Start over</b>.</p>'
    },
    {
      stage: 'connect', nav: 'Connect her account',
      title: 'Two ways in, and the first one is probably already done',
      sub: 'A Threads account is an Instagram account, so the Instagram sign-in carries it.',
      fields: ['sec-account'],
      do: [
        'Pick the model this Threads account belongs to.',
        'If the pill is already green and says <i>riding her Instagram session</i>, you are done.',
        'If not, connect her on the Instagram console — Threads comes with it.',
        'Only if you need media URLs or a webhook: open <b>Connect Threads</b> and set up a Meta app.'
      ],
      check: 'The pill next to the dropdown is green and names her @handle.',
      done: function () {
        var p = document.getElementById('conn-pill');
        return !!(p && p.classList.contains('ok'));
      },
      body:
        '<p>There are two connection modes, and they are not equivalent:</p>' +
        dl([
          ['Riding her Instagram session <i>(the usual one)</i>',
           'A Threads account <b>is</b> an Instagram account, so signing her in on the ' +
           'Instagram console connects her here too, with nothing to fill in. Everything on ' +
           'this page works — posting, scheduling, replies, DMs — with one exception, below.'],
          ['A registered Threads app',
           'A Meta app with the <code>threads_basic</code>, <code>threads_content_publish</code>, ' +
           '<code>threads_read_replies</code> and <code>threads_manage_replies</code> scopes, ' +
           'approved once through the popup. More setup, and it is the only mode that can post ' +
           'a <b>media URL</b> or receive a <b>webhook</b>, because both need Threads to fetch ' +
           'from us rather than us pushing to Threads.']
        ]) +
        '<p>If you are an operator the app fields are visible here; a creator sees only ' +
        '<b>Authorize on Threads</b>, because the app credentials are not theirs to hold. ' +
        'Either way each model connects its own account — connecting one never touches ' +
        'another.</p>' +
        '<p class="fg-note">Threads has no separate password. If the Instagram session dies, ' +
        'this dies with it — fix it on the Instagram console, not here.</p>'
    },

    {
      stage: 'post', nav: 'Write a post',
      title: 'The words — yours, or hers',
      sub: 'Type it, give her a topic, or let her write from her persona alone.',
      needs: 'connected',
      fields: ['th-topic-field', 'th-text-field'],
      do: [
        'Either type the post in <b>Text</b>, word for word…',
        '…or write a <b>topic</b> and leave the text empty.',
        'Or press <b>✨ Write it for me</b> to see what she would say, then edit it.',
        'Watch the counter — Threads cuts off at 500 characters.'
      ],
      check: 'The text box says what you want posted, or is empty on purpose with a topic filled in.',
      body:
        dl([
          ['Topic / vibe',
           'A one-line brief — "lazy Sunday", "gym day", "new photoset teaser". Only used when ' +
           'the text box is empty. She turns it into a post in her own voice, with the ' +
           'punctuation and emoji habits set in the persona builder.'],
          ['Text',
           'Used exactly as typed, up to 500 characters. The right box when the words matter.'],
          ['✨ Write it for me',
           'Fills the text box with what she would have written from the topic, so you can ' +
           'read it and change it before anything goes out. Nothing is posted by pressing it.']
        ]) +
        '<p class="fg-note">Leave both empty and she writes from her persona alone. That is ' +
        'fine for filler, but a topic almost always reads better.</p>'
    },
    {
      stage: 'post', nav: 'Photos and videos',
      title: 'What goes with it',
      sub: 'Pick from her library — or, on an app connection only, paste a URL.',
      needs: 'connected',
      fields: ['th-media-field', 'url-panel'],
      do: [
        'Click the photos you want from her library strip.',
        'One posts on its own; several post as a carousel, up to 20.',
        'Add alt text if you are posting a single image.'
      ],
      check: 'The thumbnails you picked are highlighted in the strip.',
      body:
        dl([
          ['Photos & videos',
           'Her library — the same photos the persona builder holds. Click to pick, click ' +
           'again to drop. One file posts alone, several post as a carousel (Threads\' limit ' +
           'is 20).'],
          ['Media URLs',
           'One https URL per line. This only works on an <b>app connection</b>: Threads ' +
           'fetches the file itself, so there is nothing for it to fetch when she is riding ' +
           'the Instagram session. If the panel is hidden, that is why.'],
          ['Alt text',
           'Describes the image for screen readers. Single media only — a carousel takes no ' +
           'alt text.']
        ])
    },
    {
      stage: 'post', nav: 'Send or schedule',
      title: 'Who can reply, and when it goes out',
      sub: 'Post it now, put it on the calendar, or read a draft first.',
      needs: 'connected',
      fields: ['th-when-row', 'th-post-btns', 'publish-result'],
      do: [
        'Set <b>Who can reply</b> before it goes out — it cannot be loosened afterwards in this console.',
        'Leave <b>Schedule for</b> empty to post immediately.',
        'Press <b>Preview draft</b> first if she wrote the text.',
        'Then <b>Post now</b>, or set a time and press <b>📅 Schedule</b>.'
      ],
      check: 'The result box confirms the post, or the queue count on the next step goes up.',
      body:
        dl([
          ['Who can reply',
           '<b>Anyone</b> is the default and gets the most engagement. <b>Accounts she ' +
           'follows</b> or <b>Only mentioned</b> lock the thread down, which is worth doing on ' +
           'a post you expect to attract trouble.'],
          ['Schedule for',
           'A date and time. Filled in, <b>📅 Schedule</b> queues the post; empty, <b>Post ' +
           'now</b> sends it immediately.'],
          ['Preview draft',
           'Renders what would be posted without posting it. Always worth one press when she ' +
           'wrote the words.'],
          ['Post now',
           'Goes out immediately, as her. There is no undo here — delete it in the Threads app ' +
           'if it was wrong.']
        ])
    },
    {
      stage: 'post', nav: 'The content plan',
      title: 'A week of posts the server sends without you',
      sub: 'Everything queued for Threads, and the button that fills it.',
      needs: 'connected',
      fields: ['sec-plan'],
      do: [
        'Press <b>✨ Plan the week</b> to have her draft a week of posts.',
        'Read what it queued — every row is a real post with a real slot.',
        'Use <b>Full planner →</b> to edit, move or delete them across every platform.'
      ],
      check: 'The count pill shows how many posts are waiting.',
      body:
        '<p>Each row is one queued post with its own time. The <b>server</b> sends them at ' +
        'their slot, so this keeps working with the tab closed and with your laptop shut. ' +
        'That is the point of scheduling rather than posting by hand.</p>' +
        dl([
          ['✨ Plan the week',
           'Drafts a week of posts from her persona and spaces them out. It queues them — it ' +
           'does not send anything, and every row can be edited or deleted before its slot.'],
          ['↻ Refresh',
           'Re-reads the queue. Useful after the planner has changed something on another tab.'],
          ['Full planner →',
           'The cross-platform view: every model, every channel, one calendar. The place to ' +
           'move things around rather than one post at a time.']
        ]) +
        '<p class="fg-note">A queued post fires once. If a slot passes while the host is down ' +
        'it does not stack up and fire twice.</p>'
    },

    {
      stage: 'reply', nav: 'Auto-reply',
      title: 'Answering comments, mentions and DMs',
      sub: 'Three switches, and the one difference that matters between them.',
      needs: 'connected',
      fields: ['sec-auto'],
      do: [
        'Decide which of the three she answers.',
        'Pick how often a round runs — 3 minutes is a sensible default.',
        'Press <b>Save server-side</b> so it survives this tab closing.',
        'Press <b>▶ Start auto-reply</b>.'
      ],
      check: 'The state pill goes from <i>idle</i> to running, and lines appear in the log.',
      done: function () {
        return on('auto-comments') || on('auto-mentions') || on('auto-dms');
      },
      body:
        dl([
          ['Reply to comments on my posts',
           'Answers the replies under her own posts. Public, so no link, no CTA and no offer — ' +
           'ever. This is what makes her look present rather than broadcast-only.'],
          ['Reply to mentions',
           'Answers when somebody @mentions her anywhere. Also public, same rule.'],
          ['Answer DMs <i>(runs the funnel)</i>',
           'The only one of the three that warms a fan up and eventually makes an offer. ' +
           'Because DMs ride her Instagram session and there is no paywall to verify a sale, ' +
           'the offer is her <b>profile or linktree</b>, never a direct unlock link. Meta ' +
           'filters known paysite domains, so a direct link costs the message and sometimes ' +
           'the account.'],
          ['Seconds between rounds',
           'How often she checks. Shorter feels more alive and costs more model calls; 3 ' +
           'minutes is the shipped default for a reason.'],
          ['Start / Stop vs Save server-side',
           '<b>Start</b> runs it now. <b>Save server-side</b> is what makes it survive this ' +
           'tab closing — press both, in that order, or she stops when you leave.']
        ])
    },

    {
      stage: 'live', nav: 'Instant replies',
      title: 'The webhook, if you want her replying the moment something lands',
      sub: 'Optional, and only possible on an app connection.',
      fields: ['sec-webhook'],
      do: [
        'Copy the <b>Callback URL</b>.',
        'In your Meta app\'s Threads webhook settings, add it with the topics <code>replies</code> and <code>mentions</code>.',
        'Paste the <b>Verify token</b> shown here when Meta asks for it.'
      ],
      check: 'Meta accepts the callback URL and the subscription shows as active on its side.',
      body:
        '<p>Without this, auto-reply polls on the interval you set — she answers within a round. ' +
        'With it, Meta pushes each reply and mention to us the instant it happens and she ' +
        'answers straight away.</p>' +
        '<p>It needs the <b>app connection</b>, not the Instagram one: Meta has to have ' +
        'somewhere to push to, and that is the app. If she is riding the Instagram session, ' +
        'polling is the only mode available and it is perfectly fine.</p>' +
        '<p class="fg-note">The verify token defaults to <code>threads-verify</code>. Change it ' +
        'by setting the <code>threads_webhook_verify_token</code> environment variable on the ' +
        'service, not here.</p>'
    },
    {
      stage: 'live', nav: 'Watch her work',
      title: 'The log, and what the other tabs are telling you',
      sub: 'Read one real exchange before you leave her running.',
      fields: ['sec-log'],
      do: [
        'Read the last few lines of the activity log.',
        'Find one reply she sent and read what she actually said.',
        'Check <b>Overview</b> for anything red before you walk away.'
      ],
      check: 'You can see her answering, and nothing in the log is an error you do not understand.',
      body:
        '<p>Every line is one thing auto-reply did, so it is the first place to look when she ' +
        'is quiet or saying something you did not expect.</p>' +
        '<p>The other tabs of this console:</p>' +
        dl([
          ['Overview',
           'Whether she is connected and which of the two modes she is in, in one sentence, ' +
           'plus the next thing to fix if something is wrong.'],
          ['Inbox',
           'Her Threads DMs, so you can read a conversation as the fan sees it and take over ' +
           'by hand if you want to.'],
          ['Advanced',
           'This log and the webhook. Nothing here needs setting for her to work.']
        ]) +
        '<p class="fg-note">If posts stop going out but replies keep working, check the ' +
        'content plan rather than the connection — an empty queue looks exactly like a broken ' +
        'scheduler.</p>'
    }
  ];

  var TOUR = [
    { anchor: '#fg-step', placement: 'top',
      title: 'One thing at a time',
      body: 'Setup is split into a handful of short steps. Each one covers a single part of the Threads console and hands you the real controls for it — what you change here is changed for real.' },

    { anchor: '.fg-do', placement: 'bottom',
      title: 'What to do here',
      body: 'Every step opens with the moves for it, in the order you meet them — work down the list. The line under the box below tells you how to know it worked.' },

    { anchor: '#fg-step-body', placement: 'bottom',
      title: 'This is the actual setting',
      body: 'Whatever appears in this box is the console\'s own field, moved here for this step. Fill it in and press the step\'s own button — the same one you would press on the full page.' },

    { anchor: '.cn-tabs', placement: 'bottom',
      title: 'Where you are',
      body: '{stages} stages, in the same tab bar the console itself uses. Each tab counts the steps you have done in it, and clicking one jumps straight there — the pills underneath are the steps inside the stage you are on.' },

    { anchor: '.fg-foot', placement: 'top',
      title: 'Continue, or skip ahead',
      body: 'Continue moves on to the next step. If you would rather see every setting at once, "Skip guide" hands you the full console — and you can come back to this guide any time.' }
  ];

  window.ConsoleGuide.create({
    name: 'ThreadsGuide',
    railTitle: 'Threads setup',
    storageKey: 'thGuide',
    scroll: '.xbot-scroll',
    stages: STAGES,
    steps: STEPS,
    tour: TOUR,
    gate: 'Connect her Threads account first — this step works on her real account, ' +
          'so there is nothing to show until then.',
    done: {
      sub: 'Posting and auto-reply both run on the server, so she keeps going with this ' +
           'tab closed. Watch one real reply in the log before you walk away.',
      logId: 'auto-log',
      logDesc: 'Every reply she sends, as it happens.',
      resetDesc: 'Switch this model\'s Threads auto-reply off and begin again.'
    },
    reset: {
      what: 'Threads setup',
      erased: [
        'Auto-reply, switched off and back to its defaults',
        'What she answers: comments, mentions and DMs'
      ],
      kept: [
        'Her persona, voice and photos — those live in the persona builder',
        'Her Threads connection, and her Instagram session behind it',
        'Everything already posted, and everything queued in the planner'
      ],
      jobs: function (slug, drop, http) {
        return [['Switching auto-reply off', function () {
          return http.post('/api/threads/auto', {
            persona: slug, reply_comments: true, reply_mentions: true,
            reply_dms: true, enabled: false });
        }]];
      }
    }
  });
})();
