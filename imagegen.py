"""NSFW image and video generation through a managed provider.

Google will not produce NSFW at any safety level, so the Imagen path in app.py
cannot serve the one thing a persona most needs to send. This module talks to a
provider that will, behind an interface thin enough that a second one exists
from day one — a single provider's terms changing should cost an env var, not
the feature.

Two providers, same shape:

    Runware    primary. One endpoint, a task array, the CivitAI catalogue
               (the NSFW-tuned SDXL checkpoints this needs) and video on the
               same key. Images answer synchronously; video is submitted and
               polled.
    ModelsLab  fallback. An explicitly uncensored endpoint plus faceswap and
               image-to-video. Everything is submit-then-poll.

Identity is never left to the prompt. A still is conditioned on an approved
reference photo and then faceswapped from the same photo, and a clip is only
ever generated from an already-approved still — see `video_spec_from_media`.
There is no text-to-video path here on purpose: a clip whose first frame we did
not approve is a clip of someone else.

The Runware payload field names are gathered in `_RW` so a smoke test against a
live key can correct them in one place.
"""
import base64
import json
import logging
import os
import re
import uuid

logger = logging.getLogger(__name__)

RUNWARE_ENDPOINT = 'https://api.runware.ai/v1'
MODELSLAB_ENDPOINT = 'https://modelslab.com/api/v6'

TIMEOUT = 60

# A video submit is not an image call: the provider fetches and validates the
# source clip before it acknowledges the task, which a 60s read timeout cuts
# off mid-ingest.
VIDEO_TIMEOUT = int(os.getenv('RW_VIDEO_TIMEOUT', '180'))

# Credit model keys (credits.IMAGE_MODELS) to each provider's model id.
#
# Seedream replaced the Flux/SDXL family here. It is a closed API model, which
# decides most of what follows: no LoRA exists for its architecture, `strength`
# and `checkNSFW` are rejected outright, and moderation is ByteDance's rather
# than ours. What it does carry is native multi-reference conditioning, which
# is the identity mechanism Flux needed three models and two passes to fake.
RUNWARE_MODELS = {
    'seedream-4-5': os.getenv('RW_MODEL_SEEDREAM_45', 'bytedance:seedream@4.5'),
    'seedream-5-pro': os.getenv('RW_MODEL_SEEDREAM_5PRO', 'bytedance:seedream@5.0-pro'),
    'nano-banana-pro': os.getenv('RW_MODEL_NANO_BANANA_PRO', 'google:4@2'),
    'nano-banana-2': os.getenv('RW_MODEL_NANO_BANANA_2', 'google:4@3'),
}

# Google's image models, whatever Runware calls them, refuse explicit content
# at any safety level. They are safe-work rungs only, and an explicit shot is
# moved off them before it is priced rather than after it is refused.
SFW_ONLY_MODELS = ('nano-banana-pro', 'nano-banana-2')
RUNWARE_VIDEO_MODEL = os.getenv('RW_MODEL_VIDEO', 'runware:201@1')

# Video models, keyed the same way the image ones are.
RUNWARE_VIDEO_MODELS = {
    'wan-2-5': os.getenv('RW_MODEL_WAN_25', 'runware:201@1'),
    'wan-2-7': os.getenv('RW_MODEL_WAN_27', 'alibaba:wan@2.7'),
    'seedance-2-5': os.getenv('RW_MODEL_SEEDANCE_25', 'bytedance:seedance@2.5'),
    # Character replacement rather than generation: it keeps the source clip's
    # motion, timing, camera, lighting and background and changes only who is
    # on camera, which is what a swap has always meant here.
    'p-video-replace': os.getenv('RW_MODEL_VIDEO_REPLACE',
                                 'prunaai:p-video@replace'),
}
DEFAULT_VIDEO_MODEL = 'wan-2-5'

# Swapping someone into an uploaded clip is video-to-video, which only Wan 2.7
# carries. It is not a separate "video edit" model, which is why searching for
# one finds nothing.
VIDEO_EDIT_MODEL = 'wan-2-7'

# Both take an input clip, and they do opposite things with it: replace keeps
# the video and changes the person, Wan 2.7 regenerates the video from her
# references. The picker offers both because only the operator can say which
# one this clip wants.
SWAP_MODELS = ('p-video-replace', 'wan-2-7')
DEFAULT_SWAP_MODEL = 'p-video-replace'

# Only 4.5 serves explicit work. 5.0 Pro returns `invalidProviderContent` —
# ByteDance's own moderation, not a setting — so an explicit shot is pinned to
# 4.5 rather than left to the picker.
EXPLICIT_MODEL = 'seedream-4-5'

# Identity is one reference-conditioned call. `referenceImages` is the field
# Seedream accepts; `seedImage` with a strength is refused by the architecture.
REFERENCE_FIELD = 'referenceImages'

# The two fields a swap lives or dies by, per model and settable from the
# environment: a model that names them differently is then a service env var
# rather than a deploy, and a wrong name is the one failure that looks like
# success -- the task still runs, just without her or the clip in it.
#
# `shape` is which of the two payloads a model speaks. The newer models take
# their media nested under one `inputs` object and refuse the flat fields by
# name; the older ones have never refused the flat shape, so they keep it until
# one does.
MODEL_VIDEO_FIELDS = {
    # Its own shape, not Wan's: no duration (the source decides the length --
    # which is the whole point of a replace), no width/height (a `resolution`
    # string instead), and no negative prompt. It also names the clip it edits
    # `inputs.video`, where Wan calls the same thing a reference video: one is
    # the subject of the edit, the other is something to take guidance from,
    # and the models are right to spell them differently.
    'p-video-replace': {'shape': os.getenv('RW_REPLACE_SHAPE', 'replace'),
                        'in_source': os.getenv('RW_REPLACE_VIDEO_KEY', 'video'),
                        'in_refs': os.getenv('RW_REPLACE_REF_KEY',
                                             'referenceImages')},
    'wan-2-7': {'shape': os.getenv('RW_VIDEO_SHAPE', 'inputs'),
                'source': os.getenv('RW_VIDEO_SOURCE_FIELD', 'inputVideo'),
                'refs': os.getenv('RW_VIDEO_REF_FIELD', 'referenceImages')},
}

# referenceImages takes up to 30 and referenceVideos up to 10, nested.
MAX_VIDEO_REFERENCES = 30

# For the models that take a rung by name. A provider that spells them
# differently is an entry here rather than a branch in the request builder.
MODEL_RESOLUTION_VALUES = {
    'p-video-replace': {'480p': '480p', '720p': '720p', '1080p': '1080p'},
}


def _video_fields(model_key):
    return MODEL_VIDEO_FIELDS.get(model_key,
                                  {'shape': 'flat', 'source': 'inputVideo',
                                   'refs': REFERENCE_FIELD})
MAX_REFERENCES = 14

MODELSLAB_MODELS = {
    'sdxl': os.getenv('ML_MODEL_SDXL', 'uncensored-flux-lora'),
    'flux-schnell': os.getenv('ML_MODEL_FLUX_SCHNELL', 'flux-schnell'),
    'flux-dev': os.getenv('ML_MODEL_FLUX_DEV', 'flux-dev'),
    'qwen': os.getenv('ML_MODEL_QWEN', 'qwen-image'),
}
MODELSLAB_VIDEO_MODEL = os.getenv('ML_MODEL_VIDEO', 'wan2.2')

# Dimensions are per model, not global: Seedream refuses anything under
# 3,686,400 pixels, and the Google models take only sizes from their own list.
# A tier is therefore a name for "about this big on whichever model is running",
# and RESOLUTION_PX is the fallback for anything not listed.
MODEL_PX = {
    'seedream-4-5':    {'2k': (1664, 2432), '4k': (3072, 4096)},
    'seedream-5-pro':  {'2k': (1664, 2432), '4k': (3072, 4096)},
    'nano-banana-pro': {'2k': (1696, 2528), '4k': (3392, 5096)},
    'nano-banana-2':   {'2k': (1696, 2528), '4k': (3392, 5096)},
}
RESOLUTION_PX = {
    '2k': (1664, 2432),
    '4k': (3072, 4096),
}


def dimensions(model_key, resolution):
    sizes = MODEL_PX.get(model_key) or RESOLUTION_PX
    return sizes.get(resolution) or sizes.get('2k') or RESOLUTION_PX['2k']
VIDEO_PX = {
    '480p': (480, 854),
    '720p': (720, 1280),
    '1080p': (1080, 1920),
}

# Wan 2.7 takes a fixed set of sizes and refuses anything else outright, so a
# phone clip's own 480p dimensions are not a size it can be asked for. Snapping
# is the only option a swap has: the source is whatever the creator filmed.
MODEL_VIDEO_SIZES = {
    'wan-2-7': ((1280, 720), (720, 1280), (960, 960), (1088, 832), (832, 1088),
                (1920, 1080), (1080, 1920), (1440, 1440), (1632, 1248),
                (1248, 1632)),
}


# Seconds a model will accept. Wan 2.7 refuses anything outside 2-15 outright,
# and a swap's length comes from the file rather than a picker, so the ask is
# clamped to the model's own range instead of failing at the provider.
MODEL_VIDEO_SECONDS = {
    'wan-2-7': (2, 15),
    # The source clip's own length, whatever it is: this model is never told a
    # duration, so nothing here may shorten what it will be billed for.
    'p-video-replace': (1, 60),
}


def takes_duration(model_key):
    """Whether a model can be told how long to run. One that cannot runs the
    length of the clip it is given, so nothing may quote it anything else."""
    return _video_fields(model_key).get('shape') != 'replace'


def wants_face_only(model_key):
    """Whether a model should be sent her face references and nothing else.

    One that replaces the person in a clip takes wardrobe, build and setting
    from the source, so a body reference adds no information it can use -- and
    a body shot cropped below the neck, or of anyone else, is a second identity
    for it to average her face towards. One that regenerates the whole clip
    needs both.
    """
    return _video_fields(model_key).get('shape') == 'replace'


# A model asking for a clean portrait is not helped by thirty of them, and each
# extra one is another chance to pull her face towards an average.
MODEL_REF_CAP = {'p-video-replace': 4}


def video_seconds(model_key, seconds):
    lo, hi = MODEL_VIDEO_SECONDS.get(model_key, (1, 30))
    return max(lo, min(hi, int(seconds or 0) or lo))


def video_size(model_key, width=0, height=0, resolution=None):
    """The size to ask a model for, given the source's own.

    Aspect ratio first and pixel count second: a portrait clip sent at a
    landscape size comes back letterboxed or cropped through her face, which is
    worse than a rung either side of what was asked for.
    """
    import math
    fallback = VIDEO_PX.get(resolution) or VIDEO_PX['720p']
    w, h = int(width or 0), int(height or 0)
    sizes = MODEL_VIDEO_SIZES.get(model_key)
    if not sizes:
        # No fixed list to snap to, so the source's own shape is the right
        # answer: a clip whose person is replaced should come out the shape it
        # went in, and the rung default would letterbox a landscape one. Held
        # to a multiple of 16, because a phone crop is any width it likes --
        # 406 is a real one -- and an encoder takes macroblocks or nothing.
        if w > 0 and h > 0:
            return _macroblock(w), _macroblock(h)
        return fallback
    if w <= 0 or h <= 0:
        w, h = fallback
    # The rung is what was asked for and paid for, so it filters rather than
    # merely nudges; the source's shape then picks within it.
    rung = [s for s in sizes if size_rung(s[1], s[0]) == resolution]
    if not rung:
        # The rung asked for does not exist on this model, so take the cheapest
        # one it does have rather than the nearest to the source: an absent
        # rung must never resolve upwards into a dearer clip.
        for r in ('480p', '720p', '1080p'):
            rung = [s for s in sizes if size_rung(s[1], s[0]) == r]
            if rung:
                break
    ratio = w / float(h)
    return min(rung, key=lambda s: (round(abs(math.log(s[0] / float(s[1])
                                                       / ratio)), 3),
                                    abs(s[0] * s[1] - w * h)))


def _macroblock(px, block=16):
    return max(block, int(round(px / float(block))) * block)


def size_rung(height, width=0):
    """The priced rung of a size, off its short side when both are known."""
    h, w = int(height or 0), int(width or 0)
    short = min(h, w) if w else h
    if short >= 1080:
        return '1080p'
    if short >= 720:
        return '720p'
    return '480p'


# ── Prompts ───────────────────────────────────────────────────────────────────
# The framing vocabulary the SFW path already uses, plus the explicit rungs it
# could never serve. `nsfw_level` gates which of these a persona may request:
# the creator's own setting decides, and nothing above her level is offered.

SHOT_FRAMING = {
    'portrait': 'a head-and-shoulders portrait selfie, looking at the camera',
    'half': 'a waist-up casual photo',
    'full': 'a full-body photo in a casual outfit',
    'candid': 'a candid lifestyle photo doing an everyday activity',
    'mirror': 'a mirror selfie holding a phone',
    'lingerie': 'a boudoir photo in matching lingerie, soft window light',
    'implied': ('an implied-nude photo — bare shoulders and back, the camera '
                'angle and framing suggesting more than it shows'),
    'sheer': 'a photo in a sheer, partially see-through robe, artistic and moody',
    'bedroom': 'a sultry, relaxed bedroom photo, intimate mood',
    'topless': 'a topless boudoir photo, warm natural light, tasteful and artistic',
    'nude': 'an artistic full nude photo, soft natural light, classic boudoir framing',
    'explicit': 'an explicit intimate photo, natural light, candid and unposed',
}

SHOT_LEVEL = {
    'portrait': 'sfw', 'half': 'sfw', 'full': 'sfw', 'candid': 'sfw',
    'mirror': 'sfw',
    'lingerie': 'suggestive', 'implied': 'suggestive', 'sheer': 'suggestive',
    'bedroom': 'suggestive',
    'topless': 'moderate', 'nude': 'moderate',
    'explicit': 'explicit',
}
LEVEL_ORDER = ('sfw', 'suggestive', 'moderate', 'explicit')

# The scene builder. `shot` says how close the camera is and how much is on
# show; everything below says what the picture is of. They are separate axes
# because a creator changing the scene should not have to re-pick the framing.
#
# Scenes carry their own level and are gated by it exactly as shots are: the
# persona's NSFW setting is the ceiling for both, so nothing here can be used
# to ask for something the shot list would have refused.

STYLES = {
    'any': '',
    'pov-selfie': 'shot as a POV selfie, arm visible, phone held close',
    'mirror-selfie': 'a mirror selfie, phone visible in the reflection',
    'candid': 'candid and unposed, as if caught mid-moment',
    'photoshoot': 'a styled photoshoot frame, deliberate posing',
}

SCENES = {
    # Safe for work.
    'bedroom': ('sfw', 'in her bedroom'),
    'bathroom': ('sfw', 'in the bathroom'),
    'hotel': ('sfw', 'in a hotel room'),
    'living-room': ('sfw', 'in her living room'),
    'kitchen': ('sfw', 'in the kitchen'),
    'poolside': ('sfw', 'poolside'),
    'beach': ('sfw', 'on a beach'),
    'cafe': ('sfw', 'in a cafe'),
    'gym': ('sfw', 'at the gym'),
    'car': ('sfw', 'in her car'),
    'fitting-room': ('sfw', 'in a fitting room'),
    'street': ('sfw', 'on a city street'),
    'rooftop-bar': ('sfw', 'at a rooftop bar'),
    # Tease and setup.
    'lingerie-tease': ('suggestive', 'in lingerie, teasing the camera'),
    'shower': ('suggestive', 'in the steamy shower'),
    'bath': ('suggestive', 'in a bubble bath'),
    'undressing': ('suggestive', 'undressing, caught part-way'),
    'activewear': ('suggestive', 'in yoga activewear, stretching'),
    'just-woke-up': ('suggestive', 'just woken up, sheets tangled'),
    'towel-drop': ('suggestive', 'a towel slipping'),
    'vanity': ('suggestive', 'at her vanity'),
    'walk-in-closet': ('suggestive', 'in her walk-in closet'),
    # Explicit.
    'exposed': ('explicit', 'lying back, exposed'),
    'solo-touch': ('explicit', 'touching herself'),
    'bent-over': ('explicit', 'bent over, looking back'),
    'nipple-play': ('explicit', 'hands at her chest'),
    'aftermath': ('explicit', 'afterwards, flushed and tousled'),
}

CAMERAS = {
    'auto': '',
    'flash': 'harsh direct flash',
    'night-mode': 'phone night mode, slight grain',
    '35mm': 'shot on 35mm film',
    'film-grain': 'visible film grain',
}

LIGHTING = {
    'auto': '',
    'warm-low': 'warm low light',
    'daylight': 'flat daylight',
    'morning-sun': 'morning sun through a window',
    'golden-hour': 'golden hour light',
    'dusk-neon': 'dusk, neon spill',
    'candlelight': 'candlelight',
    'studio': 'studio lighting',
    'shower-light': 'diffused light through steam',
}


def scenes_for_level(level):
    """The scenes a persona at this NSFW level may ask for."""
    try:
        ceiling = LEVEL_ORDER.index(level or 'sfw')
    except ValueError:
        ceiling = 0
    return [k for k, (lvl, _) in SCENES.items()
            if LEVEL_ORDER.index(lvl) <= ceiling]


def scene_allowed(scene, level):
    return not scene or scene in scenes_for_level(level)

NEGATIVE_PROMPT = (
    'deformed, disfigured, extra limbs, extra fingers, fused fingers, '
    'mutated hands, bad anatomy, bad proportions, watermark, text, logo, '
    'signature, blurry, low quality, jpeg artifacts, cartoon, anime, '
    'illustration, 3d render, doll, plastic skin, child, teen, underage'
)


def shots_for_level(level):
    """The shots a persona at this NSFW level may ask for."""
    try:
        ceiling = LEVEL_ORDER.index(level or 'sfw')
    except ValueError:
        ceiling = 0
    return [s for s, l in SHOT_LEVEL.items() if LEVEL_ORDER.index(l) <= ceiling]


def shot_allowed(shot, level):
    return shot in shots_for_level(level)


def merge_negative(extra=''):
    """The creator's additions are added to the baseline, never swapped for it:
    the baseline is what keeps a generation off anything underage, and a text
    box is not somewhere that should be editable."""
    extra = (extra or '').strip().strip(',')
    return (NEGATIVE_PROMPT + ', ' + extra) if extra else NEGATIVE_PROMPT


def build_prompt(appearance, shot, outfit=None, has_reference=False, extra='',
                 style='', scene='', camera='', lighting='', direction='',
                 banned=()):
    """The positive prompt for one generation.

    With a reference photo the prompt describes what changes, not who she is —
    leading with a fresh description invites the model to draw a new person and
    ignore the reference.
    """
    framing = SHOT_FRAMING.get(shot, SHOT_FRAMING['portrait'])
    outfit = outfit or {}
    bits = []
    if outfit.get('clothing'):
        bits.append(f"wearing exactly {outfit['clothing']}")
    if outfit.get('location'):
        bits.append(f"in the same place: {outfit['location']}")
    if outfit.get('lighting'):
        bits.append(f"{outfit['lighting']} lighting")

    if has_reference:
        lead = ('photorealistic photo of the exact same woman as the reference '
                'image — identical face, hair and features — now as ' + framing)
        lock = (' She must be ' + ', '.join(bits) + '.') if bits else (
            ' Copy the clothing, hairstyle, location and lighting exactly as '
            'they appear in the reference image.')
        tail = (' Taken in the same session minutes apart — only the pose and '
                'framing change.')
    else:
        lead = f'photorealistic photo of {appearance}, {framing}'
        lock = (' She is ' + ', '.join(bits) + '.') if bits else ''
        tail = ''

    # Scene, style, camera and lighting sit between the subject and the
    # creator's own words: specific enough to steer the shot, general enough
    # that none of them competes with the reference for who she is.
    scene_bits = [SCENES.get(scene, ('', ''))[1], STYLES.get(style, ''),
                  CAMERAS.get(camera, ''), LIGHTING.get(lighting, '')]
    scene_text = ', '.join(b for b in scene_bits if b)
    scene_text = (' ' + scene_text[0].upper() + scene_text[1:] + '.') if scene_text else ''
    direction = (' ' + direction.strip()) if (direction or '').strip() else ''

    # The creator's own words go last, where a diffusion prompt weights them
    # least — they refine the shot, they do not get to replace who she is.
    extra = (' ' + extra.strip()) if (extra or '').strip() else ''
    prompt = (lead + lock + tail + scene_text +
              ' Shot on a phone camera, natural skin texture and lighting, '
              'sharp focus, realistic. Fictional adult woman, 25 years old.' +
              direction + extra)

    # A character's banned terms are struck from the finished prompt rather
    # than trusted to the negative: a word the creator has forbidden should not
    # reach the model at all, whichever field it was typed into.
    for term in (banned or ()):
        term = (term or '').strip()
        if term:
            prompt = re.sub(re.escape(term), '', prompt, flags=re.I)
    return re.sub(r'\s{2,}', ' ', prompt).strip()


def engine_report():
    """Which provider model does what, for the studio to show. A creator picking
    a shot should be able to see the checkpoint and where identity comes from."""
    provider = (os.getenv('IMAGEGEN_PROVIDER') or 'runware').strip().lower()
    if provider != 'runware':
        return {'provider': provider, 'sfw': {}, 'nsfw': {}, 'loras': {},
                'video': MODELSLAB_VIDEO_MODEL}
    return {
        'provider': 'runware',
        # `key` is the credits.IMAGE_MODELS key, so the studio can name the
        # pinned model in a creator's words rather than printing an air id.
        'sfw': {'key': 'seedream-4-5',
                'model': RUNWARE_MODELS['seedream-4-5'],
                'identity': 'reference images', 'passes': 1},
        'nsfw': {'key': EXPLICIT_MODEL,
                 'model': RUNWARE_MODELS[EXPLICIT_MODEL],
                 'identity': 'reference images', 'passes': 1},
        'loras': {},
        'nsfw_shots': [k for k, v in SHOT_LEVEL.items() if v != 'sfw'],
        'video': RUNWARE_VIDEO_MODEL,
    }


def build_swap_prompt(motion=''):
    """Instruction text for an edit, not for a still coming to life: the model
    is being told whose face to carry over, and what to leave alone."""
    base = ('Replace the woman in the reference video with the woman in the '
            'reference images, keeping her face and body consistent with them. '
            'Keep the original motion, framing, pacing and lighting exactly as '
            'they are in the video.')
    return (base + ' ' + motion.strip()) if motion.strip() else base


def build_video_prompt(motion=''):
    base = ('She moves naturally and subtly — a slow breath, a small shift of '
            'weight, hair settling. The camera holds nearly still.')
    return (motion.strip() + ' ' + base) if motion.strip() else base


# ── Provider interface ────────────────────────────────────────────────────────

class GenerationError(RuntimeError):
    """The provider refused or failed. Carries `fatal` so the caller knows
    whether a retry is worth anything — the same distinction the Reddit and
    TikTok token paths draw, and for the same reason."""

    def __init__(self, message, fatal=True):
        super().__init__(message)
        self.fatal = fatal


class ProviderUnreachable(GenerationError):
    """The one ambiguous failure: the request left and no answer came back.
    The task may well have been accepted and be running, so a caller holding a
    taskUUID of its own can adopt it rather than write the job off. A refusal,
    a 429 or a 5xx is not this — there the task certainly never started."""


class Result:
    """What a finished job produced. `urls` are the provider's, short-lived —
    the caller downloads them into our own storage before they expire."""

    def __init__(self, status, urls=None, error='', cost=None):
        self.status = status            # running | done | failed
        self.urls = urls or []
        self.error = error
        self.cost = cost


class Provider:
    name = ''

    def submit_image(self, spec):
        raise NotImplementedError

    def submit_video(self, spec):
        raise NotImplementedError

    def poll(self, job_id):
        raise NotImplementedError


def _post(url, payload, headers, timeout=TIMEOUT):
    import requests
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception as e:
        # A network failure is not a refusal: the job may well still be running.
        raise ProviderUnreachable(f'{url} unreachable: {e}', fatal=False)
    if resp.status_code in (401, 402, 403):
        raise GenerationError(f'provider rejected the key ({resp.status_code})',
                              fatal=True)
    if resp.status_code >= 500 or resp.status_code == 429:
        raise GenerationError(f'provider busy ({resp.status_code})', fatal=False)
    try:
        body = resp.json()
    except ValueError:
        raise GenerationError(f'provider sent non-JSON ({resp.status_code})',
                              fatal=resp.status_code < 500)
    if resp.status_code >= 400:
        raise GenerationError(_error_text(body) or f'HTTP {resp.status_code}',
                              fatal=True)
    return body


def _error_text(body):
    # 800, not 300: a provider that refuses a parameter answers with the list of
    # the ones it does take, and that list is the only way to learn the right
    # field name. Cutting it mid-word threw away the answer.
    if not isinstance(body, dict):
        return ''
    errs = body.get('errors') or body.get('error') or body.get('message')
    if isinstance(errs, list) and errs:
        first = errs[0]
        if isinstance(first, dict):
            return str(first.get('message') or first.get('error') or first)[:800]
        return str(first)[:800]
    return str(errs)[:800] if errs else ''


# ── Runware ───────────────────────────────────────────────────────────────────

# Payload field names in one place: correct them here after a live smoke test
# rather than hunting through the request builders.
_RW = {
    'image_task': 'imageInference',
    'video_task': 'videoInference',
    'get': 'getResponse',
    'prompt': 'positivePrompt',
    'negative': 'negativePrompt',
    'seed_image': 'seedImage',
    'adapters': 'ipAdapters',
    'pulid': 'puLID',
    'lora': 'lora',
    'frame_images': 'frameImages',
    'results': 'numberResults',
    'output': 'outputType',
    'format': 'outputFormat',
}


_UNSUPPORTED_PARAM = re.compile(r"Unsupported use of '([A-Za-z0-9_]+)' parameter")

# Dropping one of these and resending does not degrade the generation, it
# replaces it: a swap with no input clip and no reference is a stranger's video
# that bills and reports success. A refusal naming one of them is a wrong field
# name for that model, and the only safe answer is to fail the job.
# Only the keys whose loss changes *who* is in the clip. `duration` was here
# and should not have been: a length is a parameter, not an identity, and
# guarding it turned a model that simply has no duration -- because its output
# runs as long as the source -- into a hard failure.
_NEVER_STRIP = frozenset({'taskType', 'taskUUID', 'model', 'positivePrompt',
                          'inputs', 'inputVideo', 'referenceImages',
                          'referenceVideos', 'frameImages', 'inputImages',
                          'video'})


class RunwareProvider(Provider):
    name = 'runware'

    def __init__(self, key=None):
        self.key = (key or os.getenv('RUNWARE_API_KEY') or '').strip()
        if not self.key:
            raise GenerationError('RUNWARE_API_KEY is not set')

    def _headers(self):
        return {'Content-Type': 'application/json'}

    def _send(self, tasks, timeout=TIMEOUT):
        # Runware authenticates with a task at the head of the body. A bearer
        # header comes back 401 invalidApiKey however good the key is.
        #
        # Every model carries its own parameter allow-list and rejects the
        # whole task for one key it does not know -- Wan 2.7 refuses
        # `checkNSFW`, Seedance refuses `negativePrompt`. Hard-coding those
        # lists here means a new model id is a new refusal, so the refusal
        # itself is read: drop the key it names and send the task again.
        for _ in range(4):
            # A rejected parameter comes back as HTTP 400, which _post raises
            # rather than returns, so the refusal has to be read off both.
            try:
                body = _post(RUNWARE_ENDPOINT,
                             [{'taskType': 'authentication', 'apiKey': self.key}] + tasks,
                             self._headers(), timeout=timeout)
            except GenerationError as e:
                # Kept whole: a network failure is fatal=False, and rebuilding
                # it here would turn a job that should be retried into one that
                # is written off.
                failure, err = e, str(e)
            else:
                err = _error_text(body)
                if not err:
                    return body.get('data') or []
                failure = GenerationError(err)
            hit = _UNSUPPORTED_PARAM.search(err)
            key = hit.group(1) if hit else None
            if key in _NEVER_STRIP:
                raise GenerationError(
                    f'This model refused {key!r}, which carries the clip or her '
                    f'face — running without it would generate someone else. '
                    f'The provider said: {err}', fatal=True)
            if not key:
                break
            if not any(t.pop(key, None) is not None for t in tasks):
                break
        raise failure

    def _base_task(self, spec, model, width, height):
        return {
            'taskType': _RW['image_task'],
            'taskUUID': str(uuid.uuid4()),
            'model': model,
            _RW['prompt']: spec.get('prompt') or '',
            'width': width,
            'height': height,
            _RW['output']: 'URL',
            _RW['format']: 'JPEG',
            'includeCost': True,
        }

    def submit_image(self, spec):
        model_key = spec.get('model') or 'seedream-4-5'
        if spec.get('explicit'):
            model_key = EXPLICIT_MODEL
        model = RUNWARE_MODELS.get(model_key) or RUNWARE_MODELS['seedream-4-5']
        width, height = dimensions(model_key, spec.get('resolution'))

        task = self._base_task(spec, model, width, height)
        task[_RW['results']] = int(spec.get('batch') or 1)
        if spec.get('seed') is not None:
            task['seed'] = int(spec['seed'])

        # Identity is native here: the reference goes in as a reference, and
        # the same one call comes back as the same woman. No LoRA, no adapter
        # and no second pass — none of which this architecture accepts anyway.
        refs = spec.get('reference_urls') or []
        if spec.get('reference_b64'):
            refs = [_data_uri(spec['reference_b64'], spec.get('reference_mime'))] + list(refs)
        if refs:
            task[REFERENCE_FIELD] = list(refs)[:MAX_REFERENCES]

        data = self._send([task])
        urls = [d.get('imageURL') for d in data if d.get('imageURL')]
        cost = sum(float(d.get('cost') or 0) for d in data)
        if not urls:
            return task['taskUUID'], Result('running')
        return task['taskUUID'], Result('done', urls, cost=cost or None)

    def submit_video(self, spec):
        task_uuid = str(uuid.uuid4())
        model_key = spec.get('model') or DEFAULT_VIDEO_MODEL
        if spec.get('kind') == 'swap' and model_key not in SWAP_MODELS:
            model_key = DEFAULT_SWAP_MODEL
        width, height = video_size(model_key, spec.get('source_width'),
                                   spec.get('source_height'),
                                   spec.get('resolution'))
        shape = _video_fields(model_key).get('shape')
        task = {
            'taskType': _RW['video_task'],
            'taskUUID': task_uuid,
            'model': (RUNWARE_VIDEO_MODELS.get(model_key)
                      or RUNWARE_VIDEO_MODELS[DEFAULT_VIDEO_MODEL]),
            _RW['prompt']: spec.get('prompt') or build_video_prompt(),
            _RW['output']: 'URL',
            'includeCost': True,
            'deliveryMethod': 'async',
        }
        if shape == 'replace':
            # It takes a rung by name and no length at all: the output runs as
            # long as the clip it was given. Sending either of the others is
            # refused outright, which is the model saying what it is.
            rungs = MODEL_RESOLUTION_VALUES.get(model_key) or {}
            rung = rungs.get(spec.get('resolution'))
            if rung:
                task['resolution'] = rung
            if spec.get('fps'):
                task['fps'] = int(spec['fps'])
        else:
            task[_RW['negative']] = spec.get('negative') or NEGATIVE_PROMPT
            task['width'] = width
            task['height'] = height
            task['duration'] = video_seconds(model_key, spec.get('seconds') or 5)
        # Only the older flat payload takes it; every newer model refuses it by
        # name. The retry in _send would strip it anyway, at the price of a
        # wasted round trip on every single clip.
        if shape == 'flat':
            task['checkNSFW'] = False
        if spec.get('kind') == 'swap':
            source = spec.get('source_url')
            if not source:
                raise GenerationError('a swap needs the clip it is swapping into')
            fields = _video_fields(model_key)
            refs = spec.get('reference_urls') or []
            if spec.get('reference_b64'):
                refs = [_data_uri(spec['reference_b64'],
                                  spec.get('reference_mime'))] + list(refs)
            if not refs:
                raise GenerationError(
                    'a swap needs at least one approved photo of her to swap in')
            if fields.get('shape') in ('inputs', 'replace'):
                # A model that edits the clip takes it as a single `video`; one
                # that takes guidance from it takes a list of reference videos.
                src_key = fields.get('in_source') or 'referenceVideos'
                cap = MODEL_REF_CAP.get(model_key, MAX_VIDEO_REFERENCES)
                task['inputs'] = {
                    src_key: source if src_key == 'video' else [source],
                    (fields.get('in_refs') or 'referenceImages'):
                        list(refs)[:cap]}
            else:
                task[fields['source']] = source
                task[fields['refs']] = list(refs)[:MAX_REFERENCES]
        else:
            frame = spec.get('reference_b64')
            if not frame:
                # Enforced here as well as in the UI: identity comes from the
                # first frame, so a clip without one is not this feature.
                raise GenerationError(
                    'a video needs an approved still as its first frame')
            first = {'inputImage': _data_uri(frame, spec.get('reference_mime'))}
            # Mutually exclusive with the reference inputs above, which is why
            # this is the whole of `inputs` rather than another key in it.
            if shape in ('inputs', 'replace'):
                task['inputs'] = {'frameImages': [first]}
            else:
                task[_RW['frame_images']] = [first]

        try:
            data = self._send([task], timeout=VIDEO_TIMEOUT)
        except ProviderUnreachable as e:
            # The taskUUID is ours and went out with the request, so the poller
            # can ask after it: either the provider took the task and will hand
            # back the clip, or it never registered and the first poll fails it.
            # Refunding here would drop a clip we may already be paying for.
            logger.warning('runware video submit unanswered, adopting %s: %s',
                           task_uuid, e)
            return task_uuid, Result('running')
        except GenerationError:
            # Keys only: the values are her photographs and a signed clip URL.
            # Which fields went out is the whole question when a model refuses
            # one of them, and the payload is gone by the time anyone looks.
            logger.warning('runware video refused model=%s fields=%s',
                           model_key, sorted(task))
            raise
        urls = [d.get('videoURL') for d in data if d.get('videoURL')]
        if urls:
            return task_uuid, Result('done', urls)
        return task_uuid, Result('running')

    def poll(self, job_id):
        data = self._send([{'taskType': _RW['get'], 'taskUUID': job_id}])
        if not data:
            return Result('running')
        urls, status = [], 'running'
        for row in data:
            url = row.get('videoURL') or row.get('imageURL')
            if url:
                urls.append(url)
            state = (row.get('status') or '').lower()
            if state in ('error', 'failed'):
                return Result('failed', error=_error_text(row) or 'generation failed')
            if state in ('success', 'done', 'completed'):
                status = 'done'
        if urls:
            return Result('done', urls)
        return Result(status if status == 'done' else 'running')


# ── ModelsLab ─────────────────────────────────────────────────────────────────

class ModelsLabProvider(Provider):
    name = 'modelslab'

    def __init__(self, key=None):
        self.key = (key or os.getenv('MODELSLAB_API_KEY') or '').strip()
        if not self.key:
            raise GenerationError('MODELSLAB_API_KEY is not set')

    def _send(self, path, payload):
        payload = dict(payload, key=self.key)
        body = _post(f'{MODELSLAB_ENDPOINT}/{path}', payload,
                     {'Content-Type': 'application/json'})
        status = (body.get('status') or '').lower()
        if status == 'error':
            raise GenerationError(_error_text(body) or 'generation failed')
        return body

    @staticmethod
    def _read(body):
        status = (body.get('status') or '').lower()
        urls = [u for u in (body.get('output') or []) if u]
        if status == 'success' and urls:
            return Result('done', urls)
        if status in ('error', 'failed'):
            return Result('failed', error=_error_text(body) or 'generation failed')
        return Result('running')

    def submit_image(self, spec):
        width, height = RESOLUTION_PX.get(spec.get('resolution'),
                                          RESOLUTION_PX['1024x1536'])
        payload = {
            'model_id': MODELSLAB_MODELS.get(spec.get('model'),
                                             MODELSLAB_MODELS['sdxl']),
            'prompt': spec.get('prompt') or '',
            'negative_prompt': spec.get('negative') or NEGATIVE_PROMPT,
            'width': width, 'height': height,
            'samples': int(spec.get('batch') or 1),
            'num_inference_steps': int(spec.get('steps') or 30),
            'guidance_scale': float(spec.get('cfg') or 6.0),
            'safety_checker': 'no',
            'enhance_prompt': 'no',
        }
        if spec.get('seed') is not None:
            payload['seed'] = int(spec['seed'])
        path = 'images/text2img'
        if spec.get('reference_url'):
            path = 'images/img2img'
            payload['init_image'] = spec['reference_url']
            payload['strength'] = float(spec.get('strength') or 0.72)
        body = self._send(path, payload)
        return str(body.get('id') or ''), self._read(body)

    def submit_video(self, spec):
        width, height = VIDEO_PX.get(spec.get('resolution'), VIDEO_PX['720p'])
        if not spec.get('reference_url'):
            raise GenerationError('a video needs an approved still as its first frame')
        body = self._send('video/img2video', {
            'model_id': MODELSLAB_VIDEO_MODEL,
            'init_image': spec['reference_url'],
            'prompt': spec.get('prompt') or build_video_prompt(),
            'negative_prompt': spec.get('negative') or NEGATIVE_PROMPT,
            'width': width, 'height': height,
            'num_frames': int(spec.get('seconds') or 5) * 16,
            'safety_checker': 'no',
        })
        return str(body.get('id') or ''), self._read(body)

    def poll(self, job_id):
        body = _post(f'{MODELSLAB_ENDPOINT}/images/fetch/{job_id}',
                     {'key': self.key}, {'Content-Type': 'application/json'})
        return self._read(body)


# ── Selection ─────────────────────────────────────────────────────────────────

PROVIDERS = {'runware': RunwareProvider, 'modelslab': ModelsLabProvider}


def provider_name():
    return (os.getenv('IMAGEGEN_PROVIDER') or 'runware').strip().lower()


def get_provider(name=None):
    name = (name or provider_name()).lower()
    cls = PROVIDERS.get(name)
    if cls is None:
        raise GenerationError(f'unknown generation provider {name!r}')
    return cls()


def configured():
    try:
        get_provider()
        return True
    except GenerationError:
        return False


def _data_uri(b64, mime):
    mime = mime or 'image/jpeg'
    return f'data:{mime};base64,{b64}'


def fetch_result(url):
    """Pull a finished generation off the provider's CDN. Their URLs are short
    lived, so nothing downstream may hold one — the bytes go to our storage."""
    import requests
    try:
        resp = requests.get(url, timeout=TIMEOUT * 3)
        resp.raise_for_status()
    except Exception as e:
        raise GenerationError(f'could not download the result: {e}', fatal=False)
    mime = (resp.headers.get('Content-Type') or '').split(';')[0].strip()
    return resp.content, mime or 'application/octet-stream'
