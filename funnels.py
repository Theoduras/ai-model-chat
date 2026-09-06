"""PPV sales funnels, fan scoring and adaptive testing.

Pure logic: no Flask, no database, no network. Everything here takes plain
values and returns plain values so the whole system can be exercised in tests
without a live Fanvue account. app.py owns the I/O and calls in here to decide.

The vocabulary follows the spec: a *fan type* is what the classifier decided, a
*funnel* is the sales approach assigned to that fan, a *variant* is the small
sub-arm being tested inside the funnel, and a *posterior* is what has been
learned about a (type × funnel × variant) cell so far.
"""
import json
import math
import random
import re

# ── 1. Fan taxonomy ───────────────────────────────────────────────────────────
FAN_TYPES = {
    'GF': ('Girlfriend-seeker',
           'asks about her day, uses her name, long messages, emotional openers',
           'connection, being seen'),
    'CO': ('Collector',
           '"what do you have", "any vids", "bundle?", talks price early',
           'completeness, value'),
    'DO': ('Dominant / director',
           'tells her what to do, requests customs, specific instructions',
           'control, exclusivity'),
    'SU': ('Submissive / worshipper',
           'compliments, "anything for you", asks permission, tips unprompted',
           'approval, serving'),
    'FL': ('Flirt / banterer',
           'jokes, teases, sarcasm, one-liners, matches her deadpan',
           'play, the chase'),
    'LU': ('Lurker / low-signal',
           'one-word replies, slow replies, opens PPV but never buys',
           'passive consumption'),
    'WH': ('Whale / high-intent',
           'fast tips, buys early, asks "what\'s the most"',
           'status, being the favorite'),
    'SK': ('Skeptic',
           '"are you real?", "is this AI?", "do you actually reply?"',
           'proof, authenticity'),
}

# Until a cell has volume, types are pooled into four super-groups (§4.2) so the
# bandit learns from 4 × 10 cells instead of 80.
SUPER_GROUPS = {'GF': 'GF+SK', 'SK': 'GF+SK', 'CO': 'CO+WH', 'WH': 'CO+WH',
                'DO': 'DO+SU', 'SU': 'DO+SU', 'FL': 'FL+LU', 'LU': 'FL+LU'}

RECLASSIFY_EVERY = 10          # messages
MIN_CONFIDENCE = 40            # below this the type is treated as unknown


def classification_prompt(transcript, current_type=''):
    """The fixed rubric. Returns strict JSON so a chatty model still parses."""
    rows = '\n'.join(f'- {code}: {name} — signals: {sig}; wants: {want}'
                     for code, (name, sig, want) in FAN_TYPES.items())
    now = (f'\nThe fan is currently classified {current_type}. Only change it if '
           'the transcript clearly contradicts it.' if current_type else '')
    return (
        'Classify this fan of an adult content creator into exactly one type, '
        'from how they write and what they ask for.\n\n' + rows + now +
        '\n\nTranscript (oldest first, "fan:" is them):\n' + transcript +
        '\n\nAnswer with JSON only, no prose: '
        '{"type": "GF", "confidence": 0-100, "why": "one short clause"}')


def parse_classification(text):
    """Pull {type, confidence} out of a model reply. Anything unrecognised comes
    back as no classification rather than a guess — a wrong type sends a fan
    down the wrong funnel for days."""
    if not text:
        return None
    raw = str(text).strip()
    m = re.search(r'\{.*\}', raw, re.S)
    data = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:
            data = {}
    code = str(data.get('type') or '').strip().upper()[:2]
    if code not in FAN_TYPES:
        # A bare "GF" answer, or one wrapped in prose.
        hits = [c for c in FAN_TYPES if re.search(rf'\b{c}\b', raw.upper())]
        if len(hits) != 1:
            return None
        code = hits[0]
    try:
        conf = int(float(data.get('confidence', 60)))
    except Exception:
        conf = 60
    return {'type': code, 'confidence': max(0, min(conf, 100)),
            'why': str(data.get('why') or '')[:200]}


def needs_classification(profile_type, type_msgs_at, msg_count, purchased=False):
    """Re-classify every 10 messages, after any purchase, and whenever a fan has
    no type yet but has said enough to place them."""
    if not profile_type:
        return msg_count >= 3
    if purchased:
        return True
    return (msg_count - int(type_msgs_at or 0)) >= RECLASSIFY_EVERY


# ── 2. The funnels ────────────────────────────────────────────────────────────
# `steer` is what gets injected into the reply prompt: strategy, never copy. The
# spec is explicit that pitch copy stays hand-written (§4.3), so `openers` holds
# the reviewed variants and the model is told to work with them, not invent.

FUNNELS = {
    'F1': {
        'name': 'Slow Burn',
        'trigger': 'GF type, or any fan with 5+ exchanges and no purchase',
        'steer': ('Pure rapport for now. Build the relationship, reference what they '
                  'have told you before, and do not pitch anything paid yet. The '
                  'unlock comes later and should feel like a continuation of what '
                  'you were already talking about.'),
        'ladder': ['T1', 'T2', 'T3'],
        'min_exchanges': 5,
        'first_pitch_delay_h': 24,
        'best': ['GF', 'SK', 'LU'], 'worst': ['CO', 'WH'],
        'exit': 'no engagement with the free teaser → F9',
        'exit_to': 'F9',
    },
    'F2': {
        'name': 'Fast Open',
        'trigger': 'new subscriber, first 48 hours',
        'steer': ('This fan just subscribed. Be quick, warm and a bit irreverent. A '
                  'cheap welcome unlock is appropriate early — framed as a favour, '
                  'not a sale.'),
        'ladder': ['T1', 'T2', 'T3'],
        'min_exchanges': 2,
        'first_pitch_delay_h': 0,
        'window_h': 48,
        'best': ['WH', 'CO', 'FL'], 'worst': ['SK', 'GF'],
        'exit': 'no purchase by hour 48 → reclassify → F1 or F6',
        'exit_to': 'F1',
    },
    'F3': {
        'name': 'Storyline Arc',
        'trigger': 'GF/FL, or any fan reacting to lore and backstory',
        'steer': ('Tell this as a serial. Refer back to the last part, hint at the '
                  'next one, and keep the story running between drops. Each part '
                  'should feel like an episode, not a product.'),
        'ladder': ['free', 'T1', 'T1', 'T2', 'T3'],
        'min_exchanges': 4,
        'first_pitch_delay_h': 12,
        'best': ['GF', 'FL', 'CO'], 'worst': [],
        'exit': 'skips two consecutive parts → offer the bundle once → F9',
        'exit_to': 'F9',
    },
    'F4': {
        'name': 'Custom Pull',
        'trigger': 'DO type, or any fan giving instructions',
        'steer': ('They are directing. Acknowledge it with attitude, get the specifics '
                  '(what they ask for is data), and treat a custom as expensive and '
                  'on your terms. Offer an existing close match as the cheaper option.'),
        'ladder': ['T2', 'T4', 'T4'],
        'min_exchanges': 3,
        'first_pitch_delay_h': 0,
        'best': ['DO', 'WH'], 'worst': ['LU', 'SK'],
        'exit': 'haggles below the floor twice → catalog (F7)',
        'exit_to': 'F7',
    },
    'F5': {
        'name': 'Tribute / Ranking',
        'trigger': 'SU type, unprompted tips',
        'steer': ('There is a hierarchy and they are in it. Content is a reward for '
                  'generosity, never a shop. Keep it dry — the moment it reads as '
                  'pressure it stops working.'),
        'ladder': ['T1', 'T2', 'T3', 'T4'],
        'min_exchanges': 3,
        'first_pitch_delay_h': 6,
        'best': ['SU', 'WH'], 'worst': ['SK', 'FL', 'GF'],
        'exit': 'any pushback on the ranking → F1 immediately',
        'exit_to': 'F1',
    },
    'F6': {
        'name': 'Reactivation Loop',
        'trigger': 'no message in 5+ days, or renewal within 7 days',
        'steer': ('This fan went quiet. Say something human and specific with nothing '
                  'to buy in it. No offer in this message, at all — the sale only '
                  'ever happens after they reply.'),
        'ladder': ['T1'],
        'min_exchanges': 0,
        'first_pitch_delay_h': 24,
        'best': ['LU', 'GF', 'CO'], 'worst': [],
        'exit': '3 unanswered reactivations over 6 weeks → stop',
        'exit_to': '',
    },
    'F7': {
        'name': 'Menu / Bundle',
        'trigger': 'CO type, price questions in the first few messages',
        'steer': ('They want the list, so give them the list — a few items, the bundle '
                  'anchored at the top, singles under it. Be matter-of-fact about it '
                  'and let them choose.'),
        'ladder': ['T2', 'T3', 'T4'],
        'min_exchanges': 2,
        'first_pitch_delay_h': 0,
        'best': ['CO', 'WH'], 'worst': ['GF', 'SK'],
        'exit': 'opens the menu, buys nothing in 24h → one T1 nudge → F6',
        'exit_to': 'F6',
    },
    'F8': {
        'name': 'Banter Escalation',
        'trigger': 'FL type, fan matches her humour',
        'steer': ('Keep the bit going. The unlock is the payoff of a running joke — a '
                  'bet lost or earned — not an offer dropped into the conversation. '
                  'Call back to it days later.'),
        'ladder': ['T1', 'T2', 'T3'],
        'min_exchanges': 4,
        'first_pitch_delay_h': 6,
        'best': ['FL', 'GF'], 'worst': ['DO', 'SU', 'LU'],
        'exit': 'fan stops matching energy → F1',
        'exit_to': 'F1',
    },
    'F9': {
        'name': 'Low-Ask Ladder',
        'trigger': 'LU type, opens previews but never buys',
        'steer': ('Micro-commitments only. Short questions, this-or-that choices, and '
                  'the cheapest thing on the account when something paid finally '
                  'goes out. Never reach for a big ask from here.'),
        'ladder': ['T1', 'T1', 'T2'],
        'min_exchanges': 2,
        'first_pitch_delay_h': 12,
        'max_tier': 'T2',
        'best': ['LU', 'SK'], 'worst': ['WH'],
        'exit': '3 unopened T1s → F6',
        'exit_to': 'F6',
    },
    'F10': {
        'name': 'Proof-First',
        'trigger': 'SK type, asks whether she is real',
        'steer': ('They think they are talking to a bot. Answer them straight, in '
                  'character, and sell nothing this session. Prove it by referring to '
                  'something specific they said and to what time it is.'),
        'ladder': ['T1', 'T2'],
        'min_exchanges': 4,
        'first_pitch_delay_h': 24,
        'no_pitch_first_session': True,
        'best': ['SK'], 'worst': ['GF', 'CO', 'DO', 'SU', 'FL', 'LU', 'WH'],
        'exit': 'hostile after disclosure → polite stop, no further pitches',
        'exit_to': '',
    },
}

# F6 is a state, not an arm (§4.1): a dormant fan is reactivated rather than
# assigned, so it is excluded from sampling.
ARMS = [f for f in FUNNELS if f != 'F6']

# §3 starting priors. 0 = never assign.
PRIORS = {
    'F1':  {'GF': 1.0, 'CO': 0.2, 'DO': 0.3, 'SU': 0.5, 'FL': 0.5, 'LU': 0.6, 'WH': 0.1, 'SK': 0.6},
    'F2':  {'GF': 0.3, 'CO': 0.7, 'DO': 0.6, 'SU': 0.5, 'FL': 0.7, 'LU': 0.3, 'WH': 1.0, 'SK': 0.1},
    'F3':  {'GF': 0.9, 'CO': 0.7, 'DO': 0.3, 'SU': 0.5, 'FL': 0.8, 'LU': 0.4, 'WH': 0.6, 'SK': 0.4},
    'F4':  {'GF': 0.3, 'CO': 0.4, 'DO': 1.0, 'SU': 0.4, 'FL': 0.3, 'LU': 0.0, 'WH': 0.9, 'SK': 0.1},
    'F5':  {'GF': 0.3, 'CO': 0.3, 'DO': 0.2, 'SU': 1.0, 'FL': 0.2, 'LU': 0.1, 'WH': 0.8, 'SK': 0.0},
    'F7':  {'GF': 0.2, 'CO': 1.0, 'DO': 0.5, 'SU': 0.3, 'FL': 0.3, 'LU': 0.4, 'WH': 0.9, 'SK': 0.2},
    'F8':  {'GF': 0.7, 'CO': 0.3, 'DO': 0.2, 'SU': 0.2, 'FL': 1.0, 'LU': 0.3, 'WH': 0.5, 'SK': 0.4},
    'F9':  {'GF': 0.4, 'CO': 0.4, 'DO': 0.1, 'SU': 0.3, 'FL': 0.3, 'LU': 1.0, 'WH': 0.0, 'SK': 0.6},
    'F10': {'GF': 0.2, 'CO': 0.1, 'DO': 0.1, 'SU': 0.1, 'FL': 0.2, 'LU': 0.2, 'WH': 0.1, 'SK': 1.0},
}


def prior_for(fan_type, funnel_id):
    return PRIORS.get(funnel_id, {}).get(fan_type, 0.0)


def eligible_funnels(fan_type, unlocked=None):
    """Funnels this type may be assigned at all. A 0 prior means never."""
    allow = set(unlocked) if unlocked else set(ARMS)
    return [f for f in ARMS if f in allow and prior_for(fan_type, f) > 0]


# ── 3. Sub-arm variants (§4.3) ────────────────────────────────────────────────
VARIANT_AXES = {
    'price_tier': ['T1', 'T2'],
    'delay': ['same_session', 'next_day'],
    'opener': ['a', 'b', 'c'],
    'preview': ['blurred', 'text'],
}


def variant_key(variant):
    if not variant:
        return ''
    return '|'.join(f'{k}={variant[k]}' for k in sorted(variant) if variant.get(k))


def pick_variant(axis, rng=None):
    """One axis at a time (§4.3), so a win is attributable."""
    rng = rng or random
    if axis not in VARIANT_AXES:
        return {}
    return {axis: rng.choice(VARIANT_AXES[axis])}


# ── 4. Assignment: matrix, then Thompson sampling ─────────────────────────────
def assign_by_matrix(fan_type, unlocked=None, rng=None):
    """Fixed assignment (§6 step 3): the highest prior, ties broken at random."""
    rng = rng or random
    options = eligible_funnels(fan_type, unlocked)
    if not options:
        return ''
    best = max(prior_for(fan_type, f) for f in options)
    return rng.choice([f for f in options if prior_for(fan_type, f) == best])


def _beta_sample(alpha, beta, rng):
    a, b = max(0.001, float(alpha)), max(0.001, float(beta))
    x, y = rng.gammavariate(a, 1.0), rng.gammavariate(b, 1.0)
    return x / (x + y) if (x + y) else 0.0


def assign_by_thompson(fan_type, posteriors, unlocked=None, rng=None):
    """Sample once per assignment (§4.1). `posteriors` maps funnel_id → (a, b).

    The prior scales the sample rather than the alpha so a strong prior still
    loses to evidence: 30 fans through a bad cell will pull it below a funnel
    the matrix liked."""
    rng = rng or random
    options = eligible_funnels(fan_type, unlocked)
    if not options:
        return ''
    best, pick = -1.0, options[0]
    for f in options:
        a, b = posteriors.get(f, (1, 1))
        draw = _beta_sample(a, b, rng) * (0.5 + 0.5 * prior_for(fan_type, f))
        if draw > best:
            best, pick = draw, f
    return pick


def reward_value(revenue_cents, churned, avg_sub_cents, crs_jump=0):
    """§4.1 reward: 7-day revenue minus a churn penalty, so a funnel that burns
    fans cannot win on revenue alone. A CRS jump within 3 days of a pitch counts
    against the arm even before an unsub (§8.4)."""
    value = int(revenue_cents or 0)
    if churned:
        value -= int(avg_sub_cents or 0)
    if crs_jump and crs_jump > 25:
        value -= int((avg_sub_cents or 0) * 0.5)
    return value


def posterior_update(alpha, beta, value):
    """Beta update on a binarised reward. Revenue size is tracked separately in
    revenue_cents; the posterior only answers "did this cell pay off"."""
    if value > 0:
        return int(alpha) + 1, int(beta)
    return int(alpha), int(beta) + 1


MIN_CELL_N = 30                # §4.2: before a winner means anything


def cell_is_trusted(n):
    return int(n or 0) >= MIN_CELL_N


# ── 5. Fan Rank Score (§7) ────────────────────────────────────────────────────
FRS_WEIGHTS = {'spend_velocity': 35, 'lifetime': 15, 'tip_ratio': 10,
               'engagement': 15, 'recency': 15, 'conversion': 10}


def _log_scale(cents, ceiling_cents):
    """Log scale so one €500 custom does not make a fan permanently #1."""
    if cents <= 0:
        return 0.0
    return min(1.0, math.log1p(cents) / math.log1p(max(cents, ceiling_cents)))


def fan_rank_score(spend_30d=0, lifetime_spend=0, tips=0, msgs_7d=0,
                   avg_msg_len=0, days_since_msg=0.0, ppv_sent=0, ppv_bought=0,
                   chargeback=False, velocity_ceiling=20000,
                   lifetime_ceiling=100000):
    """0–100. A chargeback freezes it at 0 (§7.3)."""
    if chargeback:
        return 0
    parts = {
        'spend_velocity': _log_scale(spend_30d, velocity_ceiling),
        'lifetime': _log_scale(lifetime_spend, lifetime_ceiling),
        'tip_ratio': (min(1.0, tips / lifetime_spend) if lifetime_spend > 0 else 0.0),
        # 10 messages of ~120 characters is a fully engaged week.
        'engagement': min(1.0, (msgs_7d * max(0, avg_msg_len)) / 1200.0),
        'recency': 0.5 ** (max(0.0, float(days_since_msg)) / 4.0),
        'conversion': (ppv_bought / ppv_sent) if ppv_sent >= 3 else 0.0,
    }
    return int(round(sum(FRS_WEIGHTS[k] * v for k, v in parts.items())))


TIER_ORDER = ['S', 'A', 'B', 'C', 'D']
TIER_SLA_MINUTES = {'S': 2, 'A': 5, 'B': 15, 'C': 60, 'D': 0}
# F4 is withheld below A: a custom quote from a fan who has never paid is how
# accounts get time-wasted.
TIER_FUNNELS = {
    'S': ARMS,
    'A': ARMS,
    'B': [f for f in ARMS if f != 'F4'],
    'C': ['F9'],
    'D': [],
}
DEMOTE_AFTER_DAYS = 14         # hysteresis (§7.2)


def tier_for(frs, spend_30d, rank_pct, replied_within_7d=True, subscribed=True):
    """§7.2. rank_pct is the fan's percentile by FRS, 0 = top."""
    if not subscribed:
        return 'D'
    if rank_pct <= 3 and spend_30d >= 15000:
        return 'S'
    if rank_pct <= 20:
        return 'A'
    if rank_pct <= 60 and replied_within_7d:
        return 'B'
    return 'C'


def apply_hysteresis(current, proposed, days_below):
    """Promote immediately, demote only after 14 days below (§7.2)."""
    if not current:
        return proposed
    if TIER_ORDER.index(proposed) <= TIER_ORDER.index(current):
        return proposed                      # same or better: take it now
    if days_below >= DEMOTE_AFTER_DAYS:
        return proposed
    return current


# ── 6. Churn risk (§8) ────────────────────────────────────────────────────────
CRS_WEIGHTS = {
    'latency_doubled': 22,      # earliest signal, 10-14 days ahead
    'length_shrunk': 18,        # avg words down >40% vs their own baseline
    'opens_no_buy': 16,         # 3 consecutive
    'tips_stopped': 12,
    'auto_renew_off': 12,
    'rebill_soon_quiet': 10,
    'sentiment_shift': 6,
    'card_failed': 4,
}


def churn_risk_score(signals):
    """Weighted sum of whichever signals are active. `signals` is a dict of
    name → truthy."""
    return min(100, sum(w for k, w in CRS_WEIGHTS.items() if signals.get(k)))


def churn_signals(latency_now_h=0.0, latency_prev_h=0.0, len_now=0.0,
                  len_baseline=0.0, consecutive_opens_no_buy=0,
                  tipped_before=False, tips_recent=0, auto_renew=True,
                  renews_in_days=None, days_since_msg=0.0, negative_sentiment=False,
                  card_failed=False):
    return {
        'latency_doubled': bool(latency_prev_h > 0 and latency_now_h >= 2 * latency_prev_h),
        'length_shrunk': bool(len_baseline > 0 and len_now < 0.6 * len_baseline),
        'opens_no_buy': consecutive_opens_no_buy >= 3,
        'tips_stopped': bool(tipped_before and tips_recent == 0),
        'auto_renew_off': not auto_renew,
        'rebill_soon_quiet': bool(renews_in_days is not None
                                  and renews_in_days <= 7 and days_since_msg >= 5),
        'sentiment_shift': bool(negative_sentiment),
        'card_failed': bool(card_failed),
    }


CRS_SAVE = 60
CRS_WATCH = 30


def churn_mode(crs):
    if crs >= CRS_SAVE:
        return 'save'
    if crs >= CRS_WATCH:
        return 'watch'
    return 'normal'


WINBACK_DAYS = [1, 4, 12, 30]   # then quarterly (§8.3)
WINBACK_MAX_TOUCHES = 5


def winback_due(days_since_lapse, touches_sent):
    if touches_sent >= WINBACK_MAX_TOUCHES:
        return False
    if touches_sent < len(WINBACK_DAYS):
        return days_since_lapse >= WINBACK_DAYS[touches_sent]
    quarter = 30 + 90 * (touches_sent - len(WINBACK_DAYS) + 1)
    return days_since_lapse >= quarter


def winback_is_offer(touches_sent):
    """Message, not offer, on days 1 and 4 (§8.3)."""
    return touches_sent >= 2


# ── 7. Guardrails (§5) ────────────────────────────────────────────────────────
DEFAULT_DAILY_CAP_CENTS = 15000
IGNORED_PITCH_LIMIT = 2         # global, not per funnel

# Deliberately narrow. A false positive costs one held pitch; a false negative
# means pitching at someone in trouble, which is the one thing the spec calls
# out as most important for account health.
_DISTRESS_PATTERNS = (
    r"\bcan'?t afford\b", r'\bcannot afford\b', r'\bno money\b', r'\bbroke\b',
    r'\bskint\b', r'\blost my job\b', r'\bgot fired\b', r'\bmade redundant\b',
    r'\bin debt\b', r'\bbills? (are )?piling\b', r'\brent (is )?due\b',
    r'\bevicted\b', r'\bhomeless\b', r'\bfood bank\b', r'\bbenefits?\b(?= ran out)',
    r'\bkill myself\b', r'\bend it all\b', r'\bsuicid', r'\bself.?harm\b',
    r'\bwant to die\b', r'\bno reason to (live|go on)\b',
    r'\bso lonely\b', r'\bnobody (loves|cares about) me\b', r'\bno one to talk to\b',
    r'\bdepress(ed|ion)\b', r'\bpanic attack\b', r'\bin hospital\b',
    r'\bgambling problem\b', r'\baddict(ed|ion)\b', r'\brelapse[d]?\b',
    r'\bblackout drunk\b', r"\bi'?m wasted\b", r'\bso drunk\b', r'\boff my face\b',
    r'\bcoked?\b(?= up)', r'\bhigh as\b',
)
_DISTRESS_RE = re.compile('|'.join(_DISTRESS_PATTERNS), re.I)
DISTRESS_PAUSE_HOURS = 24


def detect_distress(text):
    """True when a fan says something that must pause all pitching for 24h."""
    if not text:
        return False
    return bool(_DISTRESS_RE.search(str(text)))


_HOSTILE_RE = re.compile(
    r'\b(fuck off|piss off|scam(mer)?|rip.?off|report(ing)? you|you.?re a bot\b|'
    r'stop messaging|leave me alone|refund|chargeback|fraud)\b', re.I)


def detect_hostile(text):
    return bool(_HOSTILE_RE.search(str(text or '')))


def pitch_gate(spent_today_cents=0, daily_cap_cents=DEFAULT_DAILY_CAP_CENTS,
               ignored_pitches=0, distress_until_ts=0, now_ts=0, crs=0,
               tier='B', funnel_id='', hostile_stop=False, chargeback=False,
               first_session=True):
    """The single place that decides whether a paid ask may go out at all.

    Returns (allowed, reason, max_tier). Everything that says no here says no
    for the whole system — funnels do not get to override a guardrail."""
    if hostile_stop:
        return False, 'fan turned hostile — no further pitches', None
    if distress_until_ts and now_ts < distress_until_ts:
        return False, 'distress signal — pitching paused for 24h', None
    if ignored_pitches >= IGNORED_PITCH_LIMIT:
        return False, f'{ignored_pitches} pitches ignored — rapport only', None
    if daily_cap_cents and spent_today_cents >= daily_cap_cents:
        return False, 'daily spend cap reached — rapport only', None
    if chargeback:
        return True, 'chargeback on file — capped at T1', 'T1'
    if crs >= CRS_SAVE:
        return False, f'churn risk {crs} — save mode, no pitching', None
    if tier == 'D':
        return False, 'lapsed — win-back cadence only', None

    f = FUNNELS.get(funnel_id) or {}
    if f.get('no_pitch_first_session') and first_session:
        return False, f'{funnel_id} sells nothing in the first session', None
    max_tier = f.get('max_tier')
    if crs >= CRS_WATCH:
        return True, f'churn risk {crs} — watch mode, capped at T1', 'T1'
    if tier == 'C' and funnel_id not in TIER_FUNNELS['C']:
        return False, 'passive fan — low-ask funnel only', None
    return True, '', max_tier


TIER_ORDER_PRICE = ['free', 'T1', 'T2', 'T3', 'T4']


def cap_tier(tier_name, max_tier):
    """Clamp a ladder rung to whatever the guardrails allow."""
    if not max_tier or tier_name not in TIER_ORDER_PRICE:
        return tier_name
    if TIER_ORDER_PRICE.index(tier_name) <= TIER_ORDER_PRICE.index(max_tier):
        return tier_name
    return max_tier


def review_reason(fan_type='', crossed_to_whale=False, custom_over_t4=False,
                  hostile_skeptic=False):
    """§5 human review queue."""
    if crossed_to_whale:
        return 'crossed to whale'
    if custom_over_t4:
        return 'custom request over T4'
    if hostile_skeptic and fan_type == 'SK':
        return 'skeptic turned hostile'
    return ''


# ── 8. Loyalty (§10) ──────────────────────────────────────────────────────────
TENURE_MILESTONES = [1, 3, 6, 12]                       # months
SPEND_MILESTONES = [10000, 25000, 50000, 100000]        # cents
REWARD_DELAY_DAYS = (1, 3)      # delivered 1-3 days later, never the same hour


def due_rewards(months_subscribed=0, lifetime_spend=0, replies_this_week=0,
                first_tip=False, already=()):
    """Milestones reached but not yet delivered. `already` is a set of
    "track:milestone" keys."""
    out = []
    for m in TENURE_MILESTONES:
        if months_subscribed >= m and f'tenure:{m}' not in already:
            out.append(('tenure', str(m)))
    for c in SPEND_MILESTONES:
        if lifetime_spend >= c and f'spend:{c}' not in already:
            out.append(('spend', str(c)))
    if replies_this_week >= 10 and 'engagement:replies10' not in already:
        out.append(('engagement', 'replies10'))
    if first_tip and 'engagement:first_tip' not in already:
        out.append(('engagement', 'first_tip'))
    return out
