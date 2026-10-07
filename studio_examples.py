"""Example photos for the studio's tiles: one fixed demo model, shot once per
option and per engine, so a creator sees what a pick does before paying for it.

Every example is the same woman. An anchor portrait is made first on the
example model; every example after it sends that anchor as its reference with
the same look text and the same style suffix, whichever engine runs it.

The examples are not media. They live under storage's `examples/` prefix and
in one app setting (the manifest), never in PersonaMedia, CharacterImage or the
vault, so no send, post, reference or prompt path can reach one.
"""
import io
import logging
import os
import time

import credits as CR
import imagegen
import storage

logger = logging.getLogger('studio_examples')

# The default example engine. EXAMPLES_MODEL is the Runware AIR it runs on, so
# a newer Nano Banana can be set on the host without a deploy.
EXAMPLE_ENGINE = 'nano-banana-2'
FALLBACK_ENGINE = 'seedream-4-5'
# The image engines the studio offers. Her LoRA has no demo LoRA to run.
ENGINES = ('nano-banana-2', 'nano-banana-2-1', 'nano-banana-pro', 'seedream-4-5')
RESOLUTION = '1k'
ASPECT = '3:2'
ANCHOR_ASPECT = '2:3'
TILE_SIDE = 768
POLL_SECONDS = 180


def example_air():
    return (os.getenv('EXAMPLES_MODEL') or '').strip() or imagegen.RUNWARE_MODELS[EXAMPLE_ENGINE]


LOOK = ('She is one specific fictional adult woman, 25 years old: a soft oval face with high '
        'cheekbones, almond-shaped hazel-green eyes, straight dark-brown brows, a light dusting '
        'of freckles across her nose and cheeks, full natural lips; shoulder-length wavy '
        'chestnut-brown hair with a side part; warm light-olive skin; a slim athletic build, '
        'about 170 cm, natural medium bust, toned legs; minimal makeup with a nude-rose lip and '
        'small gold hoop earrings. Her clothes and surroundings keep one palette of cream, sage '
        'green and warm caramel.')
REFERENCE = ('She is the woman in the reference image: the same face, eyes, freckles, hair colour '
             'and cut, skin tone and build. ' + imagegen.CONSISTENCY)
STYLE = imagegen.QUALITY[imagegen.DEFAULT_QUALITY][1] + ' No text, no watermark, nobody else in the frame.'

ANCHOR_PROMPT = ('A waist-up portrait of her standing against a plain cream wall in soft window '
                 'daylight, facing the lens with a relaxed closed-mouth smile, in a cream ribbed '
                 'tank top, her face, hair and build clearly visible.')

_SWEATER = 'in a cream knit sweater'
_LIVING = 'in her sunlit living room with a linen sofa and plants'

# One prompt per option key, each written for what that option changes on her.
PROMPTS = {
    # What the shot is (Content type).
    'shot:portrait': f'A head-and-shoulders selfie of her {_SWEATER} {_LIVING}, looking into the lens with a soft smile.',
    'shot:closeup': ('A tight full-face beauty close-up, her whole face filling the frame from hairline '
                     'to chin, facing the lens, freckles and makeup crisp in every detail.'),
    'shot:half': ('A waist-up photo of her leaning on a kitchen counter in a cream knit sweater and '
                  'jeans, coffee mug in hand, relaxed smile.'),
    'shot:full': ('A full-body photo of her, head to toe, standing in her living room in a sage-green '
                  'slip dress and white sneakers, one hand in her hair.'),
    'shot:candid': ('A candid photo of her laughing mid-sentence at a cafe terrace table, glancing '
                    'away from the lens, caught in an everyday moment.'),
    'shot:mirror': ('A mirror selfie in a hallway mirror, phone in hand covering part of her face, '
                    'in a caramel ribbed top and jeans.'),
    'shot:lingerie': ('A boudoir photo of her in a cream lace bra and matching briefs, kneeling on a '
                      'bed with white linen, hands on her thighs, looking up at the lens.'),
    'shot:implied': ('An implied-nude photo: she sits on a bed with her bare back to the camera, '
                     'looking over her shoulder, a white sheet gathered at her waist, nothing explicit shown.'),
    'shot:sheer': ('A moody photo of her in a sheer, partly see-through cream robe, standing in a '
                   'bedroom doorway, leaning on the frame.'),
    'shot:bedroom': ('A relaxed, sultry bedroom photo: she lies across the bed in a sage silk camisole '
                     'and shorts, phone held above her, lazy smile.'),
    'shot:topless': ('A topless boudoir photo of her sitting on the edge of a bed in cream lace briefs, '
                     'hair over one shoulder, soft smile at the lens.'),
    'shot:nude': ('A full nude boudoir photo of her lying on her side along a bed with white linen, '
                  'head propped on one hand.'),
    'shot:explicit': ('An explicit intimate photo of her, nude, lying back on the pillows with her knees '
                      'apart, eyes on the lens, candid and unposed.'),

    # Zoom: one scene, four distances.
    **{f'zoom:{k}': (f'A photo of her in a sage-green slip dress {_LIVING}, '
                     f'{imagegen.ZOOM[k][1]}.') for k in ('close', 'medium', 'wide', 'far')},

    # Style.
    'style:pov-selfie': f'A POV selfie of her in her bedroom {_SWEATER}, arm visible, phone held close, soft smile.',
    'style:mirror-selfie': ('A mirror selfie in her bedroom mirror, phone visible in the reflection, in a '
                            'cream knit sweater and jeans.'),
    'style:candid': (f'A candid, unposed photo of her in her bedroom {_SWEATER}, caught mid-moment folding '
                     'a blanket, not looking at the camera.'),
    'style:photoshoot': (f'A styled photoshoot frame of her in her bedroom {_SWEATER}, a deliberate pose, '
                         'one hand on her hip, chin lifted.'),

    # Where she is.
    'place:bedroom': f'A waist-up photo of her sitting on her bed with white linen and a cream headboard, {_SWEATER}.',
    'place:bathroom': 'A waist-up photo of her in a bright tiled bathroom, leaning on the sink by the mirror, in a white tank top.',
    'place:hotel': ('A waist-up photo of her in a hotel room, sitting on a crisp made bed, a city view in '
                    'the window, in a caramel blazer.'),
    'place:living-room': f'A waist-up photo of her on the linen sofa {_LIVING}, in a sage sweater.',
    'place:kitchen': 'A waist-up photo of her at the kitchen counter slicing fruit, in a cream t-shirt.',
    'place:poolside': ('A photo of her poolside on a sun lounger, a turquoise pool behind her, in a sage-green '
                       'swimsuit, sunglasses pushed up in her hair.'),
    'place:beach': "A photo of her on a sandy beach at the water's edge, in a cream linen shirt over a bikini.",
    'place:cafe': 'A waist-up photo of her at a cafe window table, latte in hand.',
    'place:gym': ('A photo of her at the gym between sets, in a sage sports bra and black leggings, '
                  'dumbbells racked behind her.'),
    'place:car': "A selfie of her in the driver's seat of her parked car, seatbelt on, in a caramel jacket.",
    'place:fitting-room': 'A mirror photo of her in a fitting room trying on a caramel dress, curtain behind her.',
    'place:street': 'A photo of her walking on a city street past shop fronts, in a caramel trench coat.',
    'place:rooftop-bar': ('A photo of her at a rooftop bar at dusk, cocktail in hand, the city skyline behind '
                          'her, in a cream satin dress.'),

    # What is happening (18+ section).
    'scene:lingerie-tease': ('In cream lace lingerie, teasing the camera: kneeling on the bed, one strap '
                             'slipping off her shoulder, biting her lip.'),
    'scene:shower': ('In a steamy glass shower, head tilted back under the water, eyes closed, hands in '
                     'her wet hair, framed from the shoulders up.'),
    'scene:bath': 'In a bubble bath, leaning back in the bubbles, wet hair, smirking at the lens.',
    'scene:undressing': ('Undressing in her bedroom, caught part-way: pulling her sweater over her head, '
                         'stomach and cream bra showing, laughing.'),
    'scene:activewear': 'In sage yoga activewear, mid-stretch on a mat in her living room, looking up at the phone.',
    'scene:just-woke-up': ('Just woken up, lying on her side in tangled white sheets, messy hair, sleepy '
                           'half smile, soft morning light.'),
    'scene:towel-drop': ('In her bathroom, a white towel slipping off one hip, holding it loosely at her '
                         'chest, surprised grin.'),
    'scene:vanity': 'At her vanity in a silk robe, leaning into the mirror doing her lashes, eyes on the lens.',
    'scene:walk-in-closet': ('In her walk-in closet in a cream bra and jeans, holding up two outfits, '
                             'pouting at the lens.'),
    'scene:exposed': 'Nude, lying back on the pillows of her bed, arms above her head, heavy-lidded look.',
    'scene:solo-touch': 'Nude on her bed, touching herself, eyes half closed, lips parted.',
    'scene:bent-over': 'Nude, bent over the edge of her bed, looking back over her shoulder.',
    'scene:nipple-play': 'Topless on her bed, hands at her chest, a playful wink at the lens.',
    'scene:aftermath': ('Afterwards: nude, sprawled across rumpled white sheets, flushed cheeks, messy hair, '
                        'satisfied smile.'),

    # Expression: the same selfie, her face changing.
    **{f'expression:{k}': (f'A head-and-shoulders selfie of her {_SWEATER} {_LIVING}, '
                           f'{imagegen.EXPRESSIONS[k][1]}.')
       for k in imagegen.EXPRESSIONS if k != 'auto'},

    # Phone camera: the same selfie, a different phone.
    **{f'camera:{k}': (f'A waist-up photo of her {_SWEATER} on the sofa {_LIVING}, '
                       f'{imagegen.CAMERAS[k][1]}.')
       for k in imagegen.CAMERAS if k != 'auto'},

    # Light: the same moment, a different light.
    'light:warm-low': f'A waist-up photo of her {_SWEATER} on her sofa in the evening, warm low lamp light, soft shadows.',
    'light:daylight': f'A waist-up photo of her {_SWEATER} on her sofa, flat even daylight from an overcast window.',
    'light:morning-sun': f'A waist-up photo of her {_SWEATER} by the window, morning sun streaming in, bright patches on the wall.',
    'light:golden-hour': (f'A waist-up photo of her {_SWEATER} on her balcony, golden hour light, low warm sun from the side, '
                          'glowing rim light in her hair, long soft shadows.'),
    'light:dusk-neon': f'A waist-up photo of her {_SWEATER} by a window at dusk, pink and blue neon spill from a sign outside.',
    'light:candlelight': f'A waist-up photo of her {_SWEATER} at a table, lit only by candlelight, warm flicker on her face.',
    'light:studio': f'A waist-up photo of her {_SWEATER} against a plain backdrop, clean softbox studio lighting.',
    'light:shower-light': 'A waist-up photo of her in a white towel in her bathroom, diffused light through steam, a soft haze.',
    'light:phone-flash': f'A waist-up photo of her {_SWEATER} at night indoors, harsh direct phone flash, dark background.',
    'light:night': (f'A waist-up photo of her {_SWEATER} on her sofa at night, lit only by warm lamps and city light, '
                    'dark windows behind her.'),
    'light:dark': f'A waist-up photo of her {_SWEATER}, dark moody low-key light, deep shadows, most of the frame in shade.',

    # Photo quality: the style suffix itself changes (see prompt_for).
    **{f'quality:{k}': f'A waist-up photo of her {_SWEATER} on the sofa {_LIVING}, looking at the lens.'
       for k in imagegen.QUALITY},

    # Phone look: one plain selfie; the other strengths are this one filtered.
    'phone-look:off': f'A head-and-shoulders selfie of her {_SWEATER} by a window, soft smile.',

    'smudges:on': (f'A POV selfie of her in her bedroom {_SWEATER}, arm visible, phone held close, '
                   f'{imagegen.SMUDGES["pov-selfie"]}.'),
}

# The per-shot options added with the redesigned studio: one demo sentence per
# key, built from the same words the prompt uses, so the tile shows the pick.
for _prefix, _table in (('angle', imagegen.ANGLES), ('pose', imagegen.POSES), ('gaze', imagegen.GAZES),
                        ('hair', imagegen.HAIR_STYLES), ('makeup', imagegen.MAKEUPS),
                        ('skin', imagegen.SKINS), ('lens', imagegen.LENSES), ('grade', imagegen.GRADES)):
    for _k, _row in _table.items():
        if _row[1] and f'{_prefix}:{_k}' not in PROMPTS:
            PROMPTS[f'{_prefix}:{_k}'] = (f'A waist-up photo of her {_SWEATER} {_LIVING}, '
                                          f'{_row[1]}.')

# Admin edits: key -> sentence, set by the app from its settings before a run.
OVERRIDES = {}

# Free to make: the phone look is a filter, so these are the plain selfie run
# through it, never a provider call.
DERIVED = {'phone-look:light': 'light', 'phone-look:medium': 'medium', 'phone-look:heavy': 'heavy'}
DERIVED_SOURCE = 'phone-look:off'

# The 18+ tiles: the explicit shots and the whole "What is happening" section.
ADULT = frozenset(k for k in PROMPTS
                  if k.startswith('scene:')
                  or (k.startswith('shot:') and imagegen.SHOT_LEVEL.get(k[5:]) != 'sfw'))

KEYS = tuple(PROMPTS) + tuple(DERIVED)


def sfw_only(engine):
    return 'nsfw' not in CR.MODEL_RATINGS.get(engine, ('sfw',))


def keys_for(engine):
    """The option keys an engine's set holds. A safe-work engine skips the 18+
    tiles, except the default one, whose fallback covers them."""
    if engine not in ENGINES:
        return ()
    if sfw_only(engine) and engine != EXAMPLE_ENGINE:
        return tuple(k for k in KEYS if k not in ADULT)
    return KEYS


def attempts(engine, key):
    """(engine, AIR or None) in the order to try. The default engine runs on
    EXAMPLES_MODEL and falls back to Seedream for an 18+ tile Google refuses."""
    first = (engine, example_air() if engine == EXAMPLE_ENGINE else None)
    if key in ADULT and engine == EXAMPLE_ENGINE:
        return [first, (FALLBACK_ENGINE, None)]
    return [first]


def prompt_for(key):
    style = STYLE
    if key.startswith('quality:'):
        style = imagegen.QUALITY[key[8:]][1] + ' No text, no watermark, nobody else in the frame.'
    return ' '.join((OVERRIDES.get(key) or PROMPTS[key], LOOK, REFERENCE, style))


def anchor_prompt():
    return ' '.join((ANCHOR_PROMPT, LOOK, STYLE))


def unit_cost(engine):
    return CR.PROVIDER_COST_USD[engine][RESOLUTION]


def estimate(engine):
    """USD for a full set on this engine, anchor excluded: the floor and, with
    every 18+ tile falling back, the ceiling."""
    calls = [k for k in keys_for(engine) if k not in DERIVED]
    low = len(calls) * unit_cost(engine)
    fallbacks = sum(1 for k in calls if len(attempts(engine, k)) > 1)
    return round(low, 2), round(low + fallbacks * unit_cost(FALLBACK_ENGINE), 2), len(calls)


def generate(prompt, engine, air=None, reference=None, aspect=ASPECT):
    """One still, start to finish: submit, wait, download. Returns (bytes,
    mime, cost in USD). Raises GenerationError on a refusal or a timeout."""
    call = {'kind': 'image', 'model': engine, 'resolution': RESOLUTION, 'aspect': aspect,
            'batch': 1, 'prompt': prompt}
    if air:
        call['model_air'] = air
    if reference:
        call['reference_urls'] = [reference]
    provider = imagegen.provider_for(call)
    job, result = provider.submit_image(call)
    deadline = time.time() + POLL_SECONDS
    while result is None or result.status == 'running':
        if time.time() > deadline:
            raise imagegen.GenerationError('timed out waiting for the example')
        time.sleep(3)
        result = provider.poll(job)
    if result.status != 'done' or not result.urls:
        raise imagegen.GenerationError(result.error or 'generation failed')
    data, mime = imagegen.fetch_result(result.urls[0])
    cost = result.cost if result.cost is not None else unit_cost(engine)
    return data, mime, float(cost)


def make(engine, key, reference):
    """An example for one option key, falling back where attempts() allows.
    Returns (bytes, mime, cost, engine that made it)."""
    errors = []
    for eng, air in attempts(engine, key):
        try:
            data, mime, cost = generate(prompt_for(key), eng, air, reference)
            return data, mime, cost, eng
        except Exception as e:
            errors.append(f'{eng}: {str(e)[:160]}')
            logger.info('example %s/%s refused by %s: %s', engine, key, eng, str(e)[:160])
    raise imagegen.GenerationError('; '.join(errors))


def tile(data, crop=None):
    """A tile-sized JPEG: the tiles are a few hundred pixels wide, and a full
    still per tile would make the studio download tens of megabytes."""
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert('RGB')
    if crop:
        w, h = img.size
        cw, ch = int(w * crop), int(h * crop)
        left, top = (w - cw) // 2, max(0, int(h * 0.12))
        img = img.crop((left, top, left + cw, min(h, top + ch)))
    img.thumbnail((TILE_SIDE, TILE_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=84, optimize=True, progressive=True)
    return out.getvalue()


# The phone look is grain and glow at full size; a whole frame shrunk to a tile
# hides it, so these tiles are a face crop of the full-size still.
PHONE_CROP = 0.5


def phone_tile(raw, strength):
    data, _ = imagegen.phone_look(raw, 'image/jpeg', strength) if strength else (raw, 'image/jpeg')
    return tile(data, crop=PHONE_CROP)


def path_for(name):
    import uuid
    safe = ''.join(c if c.isalnum() or c in '-_' else '-' for c in name)
    return f'{storage.EXAMPLES_PREFIX}/{safe}-{uuid.uuid4().hex[:10]}.jpg'
