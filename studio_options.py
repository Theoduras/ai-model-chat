"""The studio's option table: the words a creator reads, their groups and order.

Prompt wording stays in imagegen.py. This file only labels, groups and orders
the keys imagegen already knows, so a stored key (and every past job, price and
example) is untouched by a label change. `vocab(level)` is what /studio renders.
"""
import imagegen as IG

LEVELS = [
    ('sfw', 'Safe for work', 'Clothed. Feeds and socials.'),
    ('suggestive', 'Tease', 'Lingerie, sheer, implied.'),
    ('moderate', 'Spicy', 'Topless and nude.'),
    ('explicit', 'Explicit', 'Fully explicit. PPV only.'),
]

FRAMING = [('closeup', 'Face close-up', 'Makeup in detail'), ('portrait', 'Portrait', 'Head and shoulders'),
           ('half', 'Waist up', 'Down to the waist'), ('full', 'Full body', 'Head to toe')]
ON_SHOW = [('lingerie', 'Lingerie', 'Boudoir'), ('implied', 'Implied nude', 'Bare back, shoulders'),
           ('sheer', 'Sheer robe', 'See-through, moody'), ('bedroom', 'In bed', 'Relaxed, sultry'),
           ('topless', 'Topless', 'Boudoir'), ('nude', 'Nude', 'Full nude boudoir'),
           ('explicit', 'Explicit', 'Candid, unposed')]
# Still valid and still read; the picker offers them as a photo style instead.
RETIRED_SHOTS = ('candid', 'mirror')

DISTANCE = [('auto', 'Let the shot decide'), ('close', 'Close'), ('medium', 'Waist up'),
            ('wide', 'Full body'), ('far', 'Far away')]
STYLE = [('any', 'Let the shot decide'), ('pov-selfie', 'POV selfie'), ('mirror-selfie', 'Mirror selfie'),
         ('candid', 'Candid'), ('photoshoot', 'Photoshoot')]

PLACES = [
    ('At home', [('bedroom', 'Bedroom'), ('bathroom', 'Bathroom'), ('living-room', 'Living room'),
                 ('kitchen', 'Kitchen')]),
    ('Out and about', [('cafe', 'Cafe'), ('street', 'City street'), ('rooftop-bar', 'Rooftop bar'),
                       ('gym', 'Gym'), ('car', 'In her car'), ('fitting-room', 'Fitting room')]),
    ('Getaway', [('hotel', 'Hotel room'), ('poolside', 'Poolside'), ('beach', 'Beach')]),
]
MOMENTS = [
    ('Tease', [('lingerie-tease', 'Lingerie tease'), ('shower', 'In the shower'), ('bath', 'Bubble bath'),
               ('undressing', 'Undressing'), ('activewear', 'Stretching in activewear'),
               ('just-woke-up', 'Just woke up'), ('towel-drop', 'Towel slipping'),
               ('vanity', 'At her vanity'), ('walk-in-closet', 'In her closet')]),
    ('Explicit', [('exposed', 'Lying back'), ('solo-touch', 'Touching herself'), ('bent-over', 'Bent over'),
                  ('nipple-play', 'Hands at her chest'), ('aftermath', 'Afterwards')]),
]

EXPRESSIONS = [('auto', 'Let the shot decide'), ('soft-smile', 'Soft smile'), ('big-smile', 'Big smile'),
               ('laughing', 'Laughing'), ('smirk', 'Smirk'), ('wink', 'Wink'), ('kissy-pout', 'Kissy pout'),
               ('scrunched', 'Scrunched face'), ('tongue-out', 'Tongue out'), ('deadpan', 'Deadpan'),
               ('sultry', 'Sultry'), ('biting-lip', 'Biting lip'), ('surprised', 'Surprised'),
               ('shy', 'Shy')]

QUALITY = [('real-phone', 'Real phone photo'), ('flagship-clean', 'Clean flagship phone'),
           ('pro-shoot', 'Professional shoot')]
CAMERAS = [
    ('', [('auto', 'Let the shot decide')]),
    ('iPhone', [('iphone-16-pro', 'iPhone 16 Pro'), ('iphone-14', 'iPhone 14'), ('iphone-11', 'iPhone 11'),
                ('iphone-front', 'Front camera')]),
    ('Android', [('galaxy-s24-ultra', 'Samsung S24 Ultra'), ('galaxy-s22', 'Samsung S22'),
                 ('pixel-8', 'Google Pixel 8'), ('budget-android', 'Budget Android')]),
    ('Film', [('disposable', 'Disposable camera')]),
]
LIGHTS = [
    ('', [('auto', 'Let the shot decide'), ('match-location', 'Match the place photo')]),
    ('Daylight', [('daylight', 'Flat daylight'), ('morning-sun', 'Morning sun'), ('golden-hour', 'Golden hour')]),
    ('Indoors', [('warm-low', 'Warm low light'), ('candlelight', 'Candlelight'), ('studio', 'Studio'),
                 ('shower-light', 'Steamy shower')]),
    ('Night', [('dusk-neon', 'Dusk and neon'), ('night', 'Lamps at night'), ('dark', 'Dark and moody'),
               ('phone-flash', 'Phone flash')]),
]


def _flat(groups):
    return [k for _, rows in groups for k, *_ in rows]


def _rows(table):
    return [(k, v[0]) for k, v in table.items()]


def _group(groups):
    return [{'group': g, 'rows': [{'key': r[0], 'label': r[1]} for r in rows]} for g, rows in groups]


def _plain(rows):
    return [{'key': r[0], 'label': r[1], 'sub': r[2] if len(r) > 2 else ''} for r in rows]


def vocab(level='explicit'):
    """Everything the studio offers at this content level, grouped and labelled.
    A level never receives a key imagegen would refuse at that level."""
    shots = set(IG.shots_for_level(level))
    scenes = set(IG.scenes_for_level(level))
    x = level != 'sfw'
    return {
        'levels': [{'key': k, 'label': l, 'sub': s} for k, l, s in LEVELS],
        'shots': _plain([r for r in (ON_SHOW if x else FRAMING) if r[0] in shots]),
        'distance': _plain(DISTANCE) if x else [],
        'styles': _plain(STYLE),
        'places': _group(PLACES),
        'moments': _group([(g, [r for r in rows if r[0] in scenes]) for g, rows in MOMENTS]) if x else [],
        'expressions': _plain(EXPRESSIONS),
        'gazes': _plain(_rows(IG.GAZES)),
        'hair': _plain(_rows(IG.HAIR_STYLES)),
        'makeup': _plain(_rows(IG.MAKEUPS)),
        'skins': _plain([(k, v[0]) for k, v in IG.SKINS.items()
                         if IG.pick_option(IG.SKINS, k, level) == k]),
        'angles': _plain(_rows(IG.ANGLES)),
        'poses': _plain(_rows(IG.POSES)),
        'quality': _plain(QUALITY),
        'cameras': _group(CAMERAS),
        'lights': _group(LIGHTS),
        'lenses': _plain(_rows(IG.LENSES)),
        'grades': _plain(_rows(IG.GRADES)),
        'keep_out': [{'key': k, 'label': v[0]} for k, v in IG.KEEP_OUT.items()],
    }


def recommended_image_engine(level, has_lora=False):
    """Creative Pro for everything 18+; Her LoRA only on request, never by default."""
    return 'seedream-4-5' if level != 'sfw' else 'nano-banana-2'


def recommended_video_engine(job, level):
    if job in ('reel', 'swap'):
        return 'kling-3-0-omni'
    return 'wan-2-2-gv' if level != 'sfw' else 'wan-3-0'


CAMERA_MOVES = {'still': 'the camera holds still', 'push': 'the camera pushes slowly in',
                'pull': 'the camera pulls slowly back', 'pan': 'the camera pans across',
                'orbit': 'the camera circles around her', 'hand': 'a handheld camera, slightly shaky'}
PACES = {'slow': 'slow, unhurried movement', 'natural': '', 'fast': 'quick, energetic movement'}


def compose_part(part):
    """One part of a clip as the motion string the video prompts already take."""
    part = part or {}
    bits = [str(part.get('motion') or '').strip().rstrip('.'),
            CAMERA_MOVES.get(part.get('cam') or '', ''),
            PACES.get(part.get('pace') or '', '')]
    end = str(part.get('end') or '').strip().rstrip('.')
    if end:
        bits.append(f'ending {end}')
    return ', '.join(b for b in bits if b)
