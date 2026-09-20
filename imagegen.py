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
import uuid

logger = logging.getLogger(__name__)

RUNWARE_ENDPOINT = 'https://api.runware.ai/v1'
MODELSLAB_ENDPOINT = 'https://modelslab.com/api/v6'

TIMEOUT = 60

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
}
RUNWARE_VIDEO_MODEL = os.getenv('RW_MODEL_VIDEO', 'runware:201@1')

# Only 4.5 serves explicit work. 5.0 Pro returns `invalidProviderContent` —
# ByteDance's own moderation, not a setting — so an explicit shot is pinned to
# 4.5 rather than left to the picker.
EXPLICIT_MODEL = 'seedream-4-5'

# Identity is one reference-conditioned call. `referenceImages` is the field
# Seedream accepts; `seedImage` with a strength is refused by the architecture.
REFERENCE_FIELD = 'referenceImages'
MAX_REFERENCES = 14

MODELSLAB_MODELS = {
    'sdxl': os.getenv('ML_MODEL_SDXL', 'uncensored-flux-lora'),
    'flux-schnell': os.getenv('ML_MODEL_FLUX_SCHNELL', 'flux-schnell'),
    'flux-dev': os.getenv('ML_MODEL_FLUX_DEV', 'flux-dev'),
    'qwen': os.getenv('ML_MODEL_QWEN', 'qwen-image'),
}
MODELSLAB_VIDEO_MODEL = os.getenv('ML_MODEL_VIDEO', 'wan2.2')

# Seedream refuses anything under 3,686,400 pixels, so the old 768x1024 rungs
# are gone rather than rounded up: every tier here is a size it will serve.
RESOLUTION_PX = {
    '2k': (1664, 2432),
    '4k': (3072, 4096),
}
VIDEO_PX = {
    '480p': (480, 854),
    '720p': (720, 1280),
    '1080p': (1080, 1920),
}


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


def build_prompt(appearance, shot, outfit=None, has_reference=False, extra=''):
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

    # The creator's own words go last, where a diffusion prompt weights them
    # least — they refine the shot, they do not get to replace who she is.
    extra = (' ' + extra.strip()) if (extra or '').strip() else ''
    return (lead + lock + tail +
            ' Shot on a phone camera, natural skin texture and lighting, '
            'sharp focus, realistic. Fictional adult woman, 25 years old.' + extra)


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
        raise GenerationError(f'{url} unreachable: {e}', fatal=False)
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
    if not isinstance(body, dict):
        return ''
    errs = body.get('errors') or body.get('error') or body.get('message')
    if isinstance(errs, list) and errs:
        first = errs[0]
        if isinstance(first, dict):
            return str(first.get('message') or first.get('error') or first)[:300]
        return str(first)[:300]
    return str(errs)[:300] if errs else ''


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


class RunwareProvider(Provider):
    name = 'runware'

    def __init__(self, key=None):
        self.key = (key or os.getenv('RUNWARE_API_KEY') or '').strip()
        if not self.key:
            raise GenerationError('RUNWARE_API_KEY is not set')

    def _headers(self):
        return {'Content-Type': 'application/json'}

    def _send(self, tasks):
        # Runware authenticates with a task at the head of the body. A bearer
        # header comes back 401 invalidApiKey however good the key is.
        body = _post(RUNWARE_ENDPOINT,
                     [{'taskType': 'authentication', 'apiKey': self.key}] + tasks,
                     self._headers())
        err = _error_text(body)
        if err:
            raise GenerationError(err)
        return body.get('data') or []

    def _base_task(self, spec, model, width, height):
        return {
            'taskType': _RW['image_task'],
            'taskUUID': str(uuid.uuid4()),
            'model': model,
            _RW['prompt']: spec.get('prompt') or '',
            _RW['negative']: spec.get('negative') or NEGATIVE_PROMPT,
            'width': width,
            'height': height,
            _RW['output']: 'URL',
            _RW['format']: 'JPEG',
            'includeCost': True,
        }

    def submit_image(self, spec):
        width, height = RESOLUTION_PX.get(spec.get('resolution'),
                                          RESOLUTION_PX['2k'])
        model_key = spec.get('model') or 'seedream-4-5'
        if spec.get('explicit'):
            model_key = EXPLICIT_MODEL
        model = RUNWARE_MODELS.get(model_key) or RUNWARE_MODELS['seedream-4-5']

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
        width, height = VIDEO_PX.get(spec.get('resolution'), VIDEO_PX['720p'])
        task_uuid = str(uuid.uuid4())
        task = {
            'taskType': _RW['video_task'],
            'taskUUID': task_uuid,
            'model': RUNWARE_VIDEO_MODEL,
            _RW['prompt']: spec.get('prompt') or build_video_prompt(),
            _RW['negative']: spec.get('negative') or NEGATIVE_PROMPT,
            'width': width,
            'height': height,
            'duration': int(spec.get('seconds') or 5),
            _RW['output']: 'URL',
            'includeCost': True,
            'checkNSFW': False,
            'deliveryMethod': 'async',
        }
        frame = spec.get('reference_b64')
        if not frame:
            # Enforced here as well as in the UI: identity comes from the first
            # frame, so a clip without one is not this feature.
            raise GenerationError('a video needs an approved still as its first frame')
        task[_RW['frame_images']] = [{'inputImage':
                                      _data_uri(frame, spec.get('reference_mime'))}]

        data = self._send([task])
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
