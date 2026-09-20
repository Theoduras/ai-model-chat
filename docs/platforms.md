# Platform Notes

Why each platform is built the way it is, and why the parked ones are parked.
`CLAUDE.md` carries the short operative rules; this file carries the reasoning
behind them. Read the section for the platform you are touching — you do not
need the rest.

---

## Discord

- Discord is driven as a real user account, not a bot application. That is
  against Discord's terms of service and the account can be terminated for it,
  so anything that makes an account look automated is a bug: one socket per
  token, one stable client fingerprint, never join a server, never open a DM
  first, and never reconnect at a token Discord has already refused.
- Her Discord account is connected through the same hosted sign-in browser
  OnlyFans uses (`of_connect.SITES`, `/discord/connect`), not by pasting a
  token. The operator signs in on Discord's own page, so the captcha, 2FA and
  the new-device code are Discord's to handle; what comes back is the token
  plus the build and capabilities that account really identified with. Those
  two are what the gateway must then claim to be — captured, never guessed.

- Discord server channels are the one exception, in `_dc_channel_round`: a room
  full of people is not a fan being worked towards something, so it never runs
  the funnel, never nudges, and never carries an offer. A paid link only ever
  goes out in a DM.

---

## OnlyFans

- **OnlyFans is parked in the UI.** The sidebar says Coming soon and the
  onboarding's done card no longer offers it, so nobody is pointed at a
  connection they are not meant to make yet. Nothing else changed: the console,
  `onlyfans.py`, the hosted sign-in and the reply loop all stay in the tree and
  `/onlyfans` still serves, the same way Reddit's does. Unpark it by restoring
  the sidebar item (`data-platform="onlyfans"` with its `openPlatform` handler)
  and the `onlyfans` entry in `PLATFORM_CARDS` in `js/onboarding.js`.

---

## Reddit

- **Reddit is parked.** The sidebar says Coming soon, `growth.PUBLISHABLE` no
  longer carries it, and a planned Reddit post goes back to the creator as a
  `manual` row. Everything else is built and tested and stays in the tree —
  only the way in is missing, and it is missing at Reddit's end, not ours.
  Reddit shut down self-serve app creation (the create button on
  `/prefs/apps` silently refreshes), Data API access is now a manual approval
  at <https://developers.reddit.com/app-registration>, and Devvit cannot
  stand in: its apps install only into communities you fully moderate, and
  `runAs: 'USER'` states plainly that it needs "an explicit manual action,
  e.g. from a button" and forbids automated actions. Unpark it by getting an
  approved `client_id` and putting Reddit back in `PUBLISHABLE` and the
  sidebar.

- Reddit is the one platform here that is **not** a driven browser, and it got
  there the hard way. It started out like Discord — hosted sign-in
  (`of_connect.SITES['reddit']`), cookie, bearer, a Sendbird socket for chat —
  because Reddit Chat has never been reachable from the API. Reddit refused that
  browser on every auth path: correct credentials came back "invalid username or
  password", the one-time email link came back `UPEl3D`, on a clean residential
  IP with patchright, with the page loading fine. Reddit was rejecting the
  client, not the account. Chat was then dropped from scope, which removed the
  only reason to drive a browser at all, so her account is now connected as a
  **registered Reddit app** (`reddit_oauth.py`, `/reddit/oauth/start` →
  `/reddit/oauth/callback`): the operator approves once, `duration=permanent`
  brings back a refresh token that does not expire, and `reddit_rest.Rest`
  speaks to `oauth.reddit.com` with a bearer and no cookie. That is the only
  path worth extending. It also fixes the ToS posture — a declared app under
  Reddit's developer terms, rather than a client its terms forbid.

- Devvit (Reddit's Developer Platform, developers.reddit.com) is **not** an
  option for this and was checked: a Devvit app can only be installed into
  communities the developer *fully moderates*. It cannot post into a subreddit
  she does not own, which is the entire job. It is worth revisiting only if a
  persona ever runs her own subreddit — there it needs no auth at all, fires on
  a CommentCreate trigger rather than polling, and can act as her via
  `runAs: 'USER'` — but it is TypeScript on Reddit's infrastructure, so it
  would be a second codebase calling back into this one for the reply text.

- The two fallbacks stay because they are the only things that can carry a chat
  token: the hosted window, and a session pasted in by hand. Both expire, both
  need a per-persona residential proxy (`_rd_proxy_for`), and an OAuth session
  deliberately uses no proxy at all — an approved app has no reason to hide, and
  a pool only adds a way for a post to fail. `reddit_chat.py` and the DM half of
  the adapter are kept but dormant, gated on a `bearer` no OAuth session has.

- A refresh token is the whole connection, so it is never thrown away on a
  guess. `_rd_fresh_token` clears the session only when Reddit calls the
  refusal final (400/401/403); a network failure raises and leaves the token
  alone, because losing it means the operator approves again for nothing.

- Reddit's two carve-outs are not optional. A public comment thread never runs
  the funnel and never carries a link, a CTA or a URL — a subreddit is the
  fastest place to lose an account over one, and `_rd_comment_round` is
  deliberately outside the shared round for that reason, the same way
  `_dc_channel_round` is. And a Sendbird group channel is dropped at the
  dispatcher, so a room full of people never reaches the reply round. In a DM
  the funnel runs in full, but the offer is her profile or linktree rather than
  a direct unlock link: Reddit filters known paysite domains.

- One planned Reddit post is several posts. The planner writes one
  `ScheduledPost` row per subreddit, each with its own title, flair and slot,
  staggered by default — the same words in four subreddits at once is what a
  spam filter is built to catch.

---

## Instagram

- Instagram is built the same way Discord is — a real signed-in account
  through the same hosted sign-in browser (`of_connect.SITES['instagram']`,
  `/instagram/connect`), because Meta's Graph API needs a Business/Creator
  account plus app review and still cannot post Stories at all. It is
  posting-only (Stories, Posts, Reels via `instagram_rest.py`), so it is
  deliberately not a `_Platform` adapter: there is no DM, no funnel and no
  scheduler yet, just `_ig_post_now` triggered from the console. Instagram's
  terms do not allow an automated client either, so the same care applies —
  an account that can be lost, not the creator's only one.

---

## TikTok

- **TikTok is parked too.** The sidebar says Coming soon, `growth.PUBLISHABLE`
  no longer carries it, and a planned TikTok post goes back to the creator as a
  `manual` row rather than firing at a channel the console is not offering. The
  app, the OAuth and the Content Posting transport are all built and tested and
  stay in the tree; unpark it by putting `tiktok` back in `PUBLISHABLE`, in
  `PL_PUBLISHABLE` in `planner.html`, and restoring the sidebar item.

- TikTok is a **registered app**, the same answer Reddit arrived at, and for
  the same kind of reason. It started out as a signed-in account through the
  hosted browser; that cannot work, because every tiktok.com web call carries a
  signature its own JavaScript computes over the query string and the user
  agent, which a captured cookie cannot carry — calls come back as empty 200s —
  and the sign-in burned the account's SMS quota before it ever got in. Posting
  is all that is wanted here and posting is documented, so it is
  `tiktok_oauth.py` (Login Kit, `/tiktok/oauth/start` → `/tiktok/oauth/callback`)
  plus `tiktok_rest.py` speaking the Content Posting API with a bearer token.

- **What an unaudited app may do decides the shape of it.** Direct Post — up on
  her profile — needs TikTok to audit the app, and until it does every post is
  SELF_ONLY and only five accounts a day may post at all. Uploading to her inbox
  needs no audit: the video lands in her TikTok drafts and she taps publish,
  picking the privacy herself. So inbox is the default and `TIKTOK_DIRECT_POST=1`
  is the switch, one env var and one re-approval, the day the audit clears. The
  console and the planner both say which of the two is live, because "posted"
  and "in her drafts" are not the same claim.

- TikTok's refresh token **rotates on every refresh**, unlike Reddit's. The one
  that comes back is stored or the connection dies within the day. The
  fatal-vs-blip rule is Reddit's exactly: only a refusal TikTok calls final
  (400/401/403) clears the session, because losing a token to a network blip
  means approving again for nothing.

- It is not a `_Platform` adapter and will not become one: no DMs, no funnel, no
  comment replies — TikTok has no comment API at all. Posting is one video; a
  photo post can only be pulled from a URL on a domain verified with TikTok,
  which is setup nobody has done, so `growth.MEDIA_SUPPORT['tiktok']` offers a
  clip only rather than failing at a slot. TikTok bars pointing anyone at adult
  content, so `growth.SFW_LOCKED` holds this channel safe for work and no paid
  link ever rides on it.
