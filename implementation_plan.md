# Communities in the X Bot tab — implementation plan

## What the X API actually exposes (verified 2 Aug 2026, docs v2.166)

| Capability | Endpoint | Status |
|---|---|---|
| Look up a community by id | `GET /2/communities/:id` | live |
| Search communities by keyword | `GET /2/communities/search?query=` | live |
| Post **into** a community | `POST /2/tweets` + `community_id` | live |
| **List communities a user joined** | — | **does not exist** |
| **List posts inside a community** | — | **does not exist** (no `community_id` / `is:community` search operator) |

Communities were announced for shutdown in April 2026, then kept. The endpoints
above are documented with no deprecation notice, so the earlier "it's gone"
conclusion was wrong.

## Consequences for the request

1. **"Gather the communities the account is signed up to"** — no API returns
   memberships. `x.com/<user>/communities` renders from X's private GraphQL
   layer, which needs the creator's logged-in session cookies and is off-limits
   to a server integration.
   → The creator picks their communities instead: paste the id/URL from that
   page, or search by name. Same end state (a stored, selectable list), one
   manual step to populate it.

2. **"…and those selected are where the new chat function is used"** — there is
   no way to read posts inside a community, so there is nothing to scan for
   repliers. This half cannot be built on the public API at all.
   → What a selected community *can* drive today is **posting into it**
   (`community_id` on the tweet payload), which puts her in front of that
   audience; people who then reply to her own post are already picked up by the
   existing reply-answering round.

## Proposed build

### [NEW] `x_communities_{persona}` setting
`[{id, name, selected}]`.

### [MODIFY] `app.py`
- `_x_community_lookup(persona, community_id)` → `GET /2/communities/:id`,
  validates a pasted id/URL and returns its name.
- `_x_community_search(persona, query)` → `GET /2/communities/search`.
- `GET/POST /api/x/communities` — list, add (by id/URL or search result),
  toggle selected, remove.
- `_x_post_round` / `api_x_post`: when a community is selected, attach
  `community_id` so posts land in it. Rotate across selected communities.

### [MODIFY] `xbot.html`
- "Communities" panel: search box, paste-a-link box, list with checkboxes,
  remove button.
- Post section: "post into selected communities" toggle.

## Open question for the user

Posting into communities is the only real use the API supports. Worth building,
or leave communities out and keep the feed-based sourcing that already works?
