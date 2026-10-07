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
    ModelsLab  fallback, and also where the explicit swap runs: Runware has no
               Wan animate/replace variant in its catalogue, so the one true
               (not regenerated) explicit-capable replace goes here instead of
               the configured default. An explicitly uncensored endpoint plus
               faceswap and image-to-video. Everything is submit-then-poll.

Identity is never left to the prompt. A still is conditioned on an approved
reference photo and then faceswapped from the same photo, and a clip that
claims to be her is only ever generated from an already-approved still. The one
exception is a safe-for-work reel, which may run from a prompt alone and
therefore carries no identity claim at all — it still lands unapproved in
staging like everything else.

`VIDEO_JOBS` is the table that says what each video job is: which models serve
it, what it needs as input, and which prompt clauses are forced on. The model
is a consequence of the job rather than something a creator is asked to pick.

The Runware payload field names are gathered in `_RW` so a smoke test against a
live key can correct them in one place.
"""
import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import shutil
import random
import re
import uuid

logger = logging.getLogger(__name__)

RUNWARE_ENDPOINT = 'https://api.runware.ai/v1'
MODELSLAB_ENDPOINT = 'https://modelslab.com/api/v6'

TIMEOUT = 60
# A still request waits for every image in the batch, so its window grows with it.
IMAGE_TIMEOUT = int(os.getenv('RW_IMAGE_TIMEOUT', '180'))

# A video submit is not an image call: the provider fetches and validates the
# source clip before it acknowledges the task, which a 60s read timeout cuts
# off mid-ingest.
VIDEO_TIMEOUT = int(os.getenv('RW_VIDEO_TIMEOUT', '180'))


def submit_window(spec):
    """The longest a submit for this spec can block before it has an answer.
    A still is answered inline, so until then there is no task id to record,
    and a job without one is not dead until this has passed."""
    if (spec or {}).get('model') in VAST_MODELS:
        # A Vast submit waits for a worker to wake before it has a job id.
        return VAST_ROUTE_WAIT + VIDEO_TIMEOUT
    if (spec or {}).get('kind') == 'image':
        return IMAGE_TIMEOUT + 30 * int(spec.get('batch') or 1)
    return VIDEO_TIMEOUT

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
    # Read off Runware's public model pages, not its live catalogue -- confirm
    # with search_models() before trusting one in production.
    'seedance-2-0': os.getenv('RW_MODEL_SEEDANCE_20', 'bytedance:seedance@2.0'),
    'seedance-2-0-fast': os.getenv('RW_MODEL_SEEDANCE_20_FAST',
                                   'bytedance:seedance@2.0-fast'),
    'minimax-h3': os.getenv('RW_MODEL_MINIMAX_H3', 'minimax:h3@0'),
    'minimax-h3-fast': os.getenv('RW_MODEL_MINIMAX_H3_FAST', 'minimax:h3@fast'),
    'wan-3-0': os.getenv('RW_MODEL_WAN_30', 'alibaba:wan@3.0'),
    # Kling motion control: her one photo performs the uploaded clip.
    'kling-2-6-mc': os.getenv('RW_MODEL_KLING_26_MC', 'klingai:kling-video@2.6-pro'),
    # Not in Runware's catalogue (modelSearch lists no Wan 2.2 at all): the id
    # is refused, so it is priced for past jobs but no longer offered.
    'wan-2-2-animate': os.getenv('RW_MODEL_WAN22_ANIMATE', 'runware:200@8'),
    # Her photo performs the uploaded clip. Explicit clips go here because
    # Alibaba's hosted Wan 2.7 filters the input clip (DataInspectionFailed).
    # It was dropped once for crashing the provider worker in its safety check;
    # a crash fails the job and refunds it, so it is back for this one job.
    'p-video-animate': os.getenv('RW_MODEL_VIDEO_ANIMATE', 'prunaai:p-video@animate'),
    'kling-3-0-mc': os.getenv('RW_MODEL_KLING_30_MC', 'klingai:kling-video@3-pro'),
    # Video edit: the clip and up to four photos, addressed in the prompt as
    # @Image1.. Id and fields are from public docs, not the live catalogue.
    'kling-3-0-omni': os.getenv('RW_MODEL_KLING_30_OMNI', 'klingai:kling-video@o3-pro'),
}
KLING_MOTION_MODELS = ('kling-2-6-mc', 'kling-3-0-mc')
EXPLICIT_MOTION_MODEL = 'p-video-animate'
# The safe-work models that take her photos as `inputs.referenceImages` beside
# a prompt, so a reel or an animate can carry her character on them.
REFERENCE_VIDEO_MODELS = ('wan-2-7', 'seedance-2-0', 'seedance-2-0-fast',
                          'minimax-h3', 'minimax-h3-fast', 'wan-3-0')
# Models whose allow-list has no `negativePrompt`. Sending it costs a refused
# round trip carrying every reference photo before `_send` strips it.
NO_NEGATIVE_MODELS = ('wan-3-0', 'seedance-2-0', 'seedance-2-0-fast')
# Models that only work on a clip the creator uploaded.
# p-video-replace stays priced for past jobs but is no longer offered.
CLIP_ONLY_MODELS = ('p-video-replace', 'kling-3-0-omni')
DEFAULT_REPLACE_MODEL = 'kling-3-0-omni'
DEFAULT_VIDEO_MODEL = 'seedance-2-0-fast'

# Wan 2.2 is open weights, so it runs on RunPod's public endpoint with its
# safety checker off -- the explicit still-to-clip model. Billed per video.
# Wan 2.6 is Alibaba-hosted behind RunPod's switch, so whether its output is
# really unfiltered is what running it explicit is testing.
RUNPOD_API_KEY = (os.getenv('RUNPOD_API_KEY') or '').strip()
RUNPOD_ENDPOINTS = {
    'wan-2-2': os.getenv('RUNPOD_ENDPOINT', 'https://api.runpod.ai/v2/wan-2-2-i2v-720'),
    'wan-2-6-rp': os.getenv('RUNPOD_WAN26_ENDPOINT', 'https://api.runpod.ai/v2/wan-2-6-i2v'),
    'wan-2-2-lora': os.getenv('RUNPOD_WAN22_LORA_ENDPOINT',
                              'https://api.runpod.ai/v2/wan-2-2-t2v-720-lora'),
}
# Our own serverless endpoint from the hub's wlsdml1114/generate_video (Wan 2.2
# I2V on ComfyUI, no safety checker at all). Billed per GPU-second, not per clip.
# The id is the default so no host needs config; set the variable empty to drop it.
_GV = os.getenv('RUNPOD_GV_ENDPOINT', 'ys8km1d7sayxtz').strip().rstrip('/')
if _GV:
    RUNPOD_ENDPOINTS['wan-2-2-gv'] = _GV if '/' in _GV else f'https://api.runpod.ai/v2/{_GV}'
# Our MiniMax H3 endpoint (infra/runpod-h3): image to video with its own audio.
# Its weights are Blackwell-only (nvfp4, w6a8), so the endpoint runs RTX PRO 6000s.
# The id is the default so no host needs config; set the variable empty to drop it.
_H3 = os.getenv('RUNPOD_H3_ENDPOINT', 'szk0bfj0wywyyv').strip().rstrip('/')
if _H3:
    RUNPOD_ENDPOINTS['h3-gv'] = _H3 if '/' in _H3 else f'https://api.runpod.ai/v2/{_H3}'
# Our character-LoRA endpoint (infra/runpod-lora): trains a Wan 2.2 LoRA on a
# character's approved photos, and makes stills from the same base with it.
CHAR_LORA_IMAGE_MODEL = 'wan-2-2-char'
_LORA_EP = os.getenv('RUNPOD_LORA_ENDPOINT', '').strip().rstrip('/')
if _LORA_EP:
    RUNPOD_ENDPOINTS[CHAR_LORA_IMAGE_MODEL] = (
        _LORA_EP if '/' in _LORA_EP else f'https://api.runpod.ai/v2/{_LORA_EP}')
# Civitai serves most NSFW files only to a signed-in caller; RunPod fetches the
# LoRA itself, so the token rides on the link it is handed.
CIVITAI_TOKEN = (os.getenv('CIVITAI_TOKEN') or '').strip()
# Our own models on Vast Serverless (infra/vast), by endpoint name. With
# VAST_API_KEY set they run there instead of on RunPod, same payloads.
VAST_API_KEY = (os.getenv('VAST_API_KEY') or '').strip()
VAST_ENDPOINTS = {m: os.getenv(f'VAST_{m.upper().replace("-", "_")}_ENDPOINT', m)
                  for m in ('wan-2-2-gv', 'h3-gv', CHAR_LORA_IMAGE_MODEL)}
VAST_MODELS = tuple(VAST_ENDPOINTS) if VAST_API_KEY else ()
for _m in VAST_MODELS:
    RUNPOD_ENDPOINTS.setdefault(_m, 'vast')
RUNPOD_MODELS = tuple(RUNPOD_ENDPOINTS)
# Vast has no queue: a submit waits here while a cold worker boots.
VAST_ROUTE_WAIT = int(os.getenv('VAST_ROUTE_WAIT', '1200'))
# RunPod's Wan workers return silent clips and nothing follows up on them, so
# they take an uploaded track or none, never a generated one.
SILENT_MODELS = tuple(m for m in RUNPOD_MODELS if m != 'h3-gv')
# Pixel sizes for a character-LoRA still, by frame shape; multiples of 16.
IMAGE_LORA_PX = {'1:1': (1216, 1216), '2:3': (1024, 1536), '3:2': (1536, 1024),
                 '4:5': (1152, 1440), '5:4': (1440, 1152), '21:9': (1792, 768),
                 '3:4': (1088, 1440), '4:3': (1440, 1088), '9:16': (896, 1600),
                 '16:9': (1600, 896)}
# Models that run the studio's LoRA library (`spec['loras']`).
LORA_VIDEO_MODELS = ('wan-2-2-lora', 'wan-2-2-gv', 'h3-gv')


def lora_family(model_key):
    """Which LoRA library a model loads: H3's files are one file each and do not
    fit Wan's high/low pairs, nor the other way round."""
    return 'h3' if model_key == 'h3-gv' else 'wan'
# The prompt side of the pipeline, after RunPod's text-to-video tutorial: Qwen
# reads what the still shows and suggests what it could do, then writes the
# clip's prompt. Qwen reads text only, so the still arrives as its description.
RUNPOD_QWEN_ENDPOINT = os.getenv('RUNPOD_QWEN_ENDPOINT', 'https://api.runpod.ai/v2/qwen3-32b-awq')

# Swapping someone into an uploaded clip is video-to-video, which only Wan 2.7
# carries. It is not a separate "video edit" model, which is why searching for
# one finds nothing.
VIDEO_EDIT_MODEL = 'wan-2-7'

# ── The job table ─────────────────────────────────────────────────────────────
# What a creator actually wants, and the one place each of those wants is
# described. The surface used to ask "which model?" and then hide most of what
# each model could do behind that answer; here the job is the question and the
# model is a consequence of it, which is why nothing below needs a second list.
#
# `models`   the models that serve this job, safe-work first and explicit
#            second, so a rating picks within the list rather than off it.
# `needs`    what the job cannot run without: a source clip, her references, a
#            first frame.
# `kind`     which priced shape the job settles into. Only a swap is billed for
#            the clip it was handed; everything else is a generated clip.
# `ratings`  which ratings may ask for it at all. A reel is safe work by
#            construction -- it may run from a prompt alone, so it carries no
#            claim to be her and must not be used to make one.
# `clause`   a prompt clause forced on regardless of what the creator typed.
# `modes`    the sub-choices a job offers, where it has any.
#
# Runware has no Wan animate/replace variant -- its catalogue carries only base
# Wan generation models (3.0, 2.7, 2.6, ...), confirmed by querying its own
# model list. A true, explicit-capable replace instead goes to ModelsLab, whose
# face-swap endpoint is uncensored and swaps rather than regenerates.
VIDEO_JOBS = {
    'reel': {'models': ('seedance-2-0-fast', 'minimax-h3-fast', 'wan-3-0',
                        'kling-3-0-mc', 'kling-3-0-omni'), 'needs': (),
             'kind': 'video', 'ratings': ('sfw',), 'open': ('sfw',),
             'label': 'Reel',
             'note': 'A prompt, a photo, or both, as a short clip.'},
    # Swap, Extend and Multi-reference are hidden from explicit work, not
    # removed: explicit video is Photo to Video alone for now.
    'swap': {'models': ('kling-3-0-mc', 'ml-face-swap', 'kling-3-0-omni'),
             'needs': ('source', 'refs'), 'kind': 'swap', 'ratings': ('sfw',),
             'open': ('sfw',), 'clause': 'preserve',
             'label': 'Swap',
             'note': 'Her into a clip you upload. Everything else untouched.'},
    # Wan 2.7 stays as the explicit still-to-clip model until RunPod's key is set.
    'animate': {'models': ('seedance-2-0-fast', 'minimax-h3-fast', 'wan-3-0')
                          + (RUNPOD_MODELS if RUNPOD_API_KEY or VAST_API_KEY else ('wan-2-7',))
                          + ('p-video-animate',),
                'needs': ('first_frame',),
                'kind': 'video',
                # Every plan may run it at these ratings; Extend and
                # Multi-reference stay admin-only while they are in testing.
                'open': ('sfw', 'nsfw'),
                'label': 'Photo to Video',
                'note': 'One of her approved photos, brought to life as a clip.'},
    'extend': {'models': ('wan-2-7',), 'needs': ('first_frame',),
               'kind': 'video', 'ratings': ('sfw',), 'modes': ('continue', 'longer', 'loop'),
               'label': 'Extend',
               'note': 'Carry on from a clip you already have.'},
    'multiref': {'models': ('wan-2-7',), 'needs': ('refs',), 'kind': 'video', 'ratings': ('sfw',),
                 'label': 'Multi-reference',
                 'note': 'Her identity refs plus a separate scene or outfit.'},
}

# Every job's own longest clip, for the Extend job's `longer` mode: it asks for
# the model's ceiling rather than a number of its own, so a model whose range
# widens needs no second edit here.
EXTEND_MODES = VIDEO_JOBS['extend']['modes']

# Derived aliases, so the swap path, the price table and the studio keep
# working off the same table rather than a second copy of it.
SWAP_MODELS = VIDEO_JOBS['swap']['models']
DEFAULT_SWAP_MODEL = SWAP_MODELS[0]
# The one that serves explicit work and still replaces rather than regenerates.
EXPLICIT_SWAP_MODEL = 'ml-face-swap'


def job_models(job):
    """The models that serve a job, in the table's own order."""
    return list((VIDEO_JOBS.get(job) or {}).get('models') or ())


def job_kind(job):
    """Which priced shape a job settles into: `swap` is billed for the clip it
    was handed, everything else is a generated clip."""
    return (VIDEO_JOBS.get(job) or {}).get('kind') or 'video'


def job_ratings(job):
    return list((VIDEO_JOBS.get(job) or {}).get('ratings') or ('sfw', 'nsfw'))


def job_open(job, rating):
    """Whether every plan may run this job at this rating, not only an admin."""
    want = 'nsfw' if rating in ('nsfw', 'explicit') else 'sfw'
    return want in ((VIDEO_JOBS.get(job) or {}).get('open') or ())


def jobs_for_rating(rating):
    want = 'nsfw' if rating == 'nsfw' else 'sfw'
    return [j for j in VIDEO_JOBS if want in job_ratings(j)]


def job_needs(job, what):
    return what in ((VIDEO_JOBS.get(job) or {}).get('needs') or ())


# The clause a swap cannot be talked out of. A regenerating model is being
# asked to reproduce a clip it did not make, and the one thing that must change
# is the only thing a creator's own wording tends to leave vague -- so this
# leads the prompt and the creator's words follow it.
PRESERVE_CLAUSE = (
    'Keep the source video identical in every respect — same setting, same '
    'background, same motion and timing, same camera movement and framing, '
    'same lighting, same colour grade, same style and quality. Change only '
    'the person: her face, her body, her hair. Nothing else in the frame may '
    'change.')


def preserves_source(job, model_key):
    """Whether this job's prompt carries the locked preservation clause.

    Only where the model regenerates: one that replaces the person in the clip
    it was given keeps the setting and the camera by construction, so telling
    it to is spending prompt on something already true.
    """
    spec = VIDEO_JOBS.get(job) or {}
    return (spec.get('clause') == 'preserve'
            and _video_fields(model_key).get('shape') != 'replace')

# Only 4.5 serves explicit work. 5.0 Pro returns `invalidProviderContent` —
# ByteDance's own moderation, not a setting — so an explicit shot is pinned to
# 4.5 rather than left to the picker.
EXPLICIT_MODEL = 'seedream-4-5'
# Every image model that may run an explicit shot as asked. credits imports
# this module, so this is a copy of its MODEL_RATINGS; test_tokens pins the two.
EXPLICIT_MODELS = (EXPLICIT_MODEL, CHAR_LORA_IMAGE_MODEL)

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
    'p-video-animate': {'shape': 'replace', 'in_source': 'referenceVideos',
                        'in_refs': 'referenceImages'},
    'p-video-replace': {'shape': os.getenv('RW_REPLACE_SHAPE', 'replace'),
                        'in_source': os.getenv('RW_REPLACE_VIDEO_KEY', 'video'),
                        'in_refs': os.getenv('RW_REPLACE_REF_KEY',
                                             'referenceImages')},
    'wan-2-7': {'shape': os.getenv('RW_VIDEO_SHAPE', 'inputs'),
                'source': os.getenv('RW_VIDEO_SOURCE_FIELD', 'inputVideo'),
                'refs': os.getenv('RW_VIDEO_REF_FIELD', 'referenceImages')},
    **{m: {'shape': 'motion', 'in_source': 'referenceVideos',
           'in_refs': 'referenceImages'}
       for m in ('kling-2-6-mc', 'kling-3-0-mc', 'wan-2-2-animate')},
    **{m: {'shape': 'inputs', 'source': 'inputVideo', 'refs': 'referenceImages'}
       for m in ('seedance-2-0', 'seedance-2-0-fast', 'minimax-h3',
                 'minimax-h3-fast', 'wan-3-0', 'kling-3-0-omni')},
}

# referenceImages takes up to 30 and referenceVideos up to 10, nested.
MAX_VIDEO_REFERENCES = 30

# For the models that take a rung by name, in ascending order. A provider that
# spells them differently, or serves fewer of them, is an entry here rather
# than a branch in the request builder. This model has no 480p at all, and a
# phone clip's short side is often below 720: such a source runs at the lowest
# rung the model does serve, and is billed at that rung because that is what
# the provider charges for it.
MODEL_RESOLUTION_VALUES = {
    'p-video-replace': (('720p', '720p'), ('1080p', '1080p')),
    'p-video-animate': (('720p', '720p'), ('1080p', '1080p')),
    # 1080p at 81 frames was never run on the endpoint.
    'wan-2-2-gv': (('480p', '480p'), ('720p', '720p')),
    'h3-gv': (('480p', '480p'), ('720p', '720p')),
}


def model_rungs(model_key):
    """The rungs a model serves, lowest first, or None when it takes pixels
    and any rung is as good as another."""
    rows = MODEL_RESOLUTION_VALUES.get(model_key)
    return [key for key, _ in rows] if rows else None


def rung_for(model_key, resolution):
    """The rung this model will actually run, given the one asked for: its
    own lowest when the source sits below everything it serves."""
    rungs = model_rungs(model_key)
    if not rungs:
        return resolution
    return resolution if resolution in rungs else rungs[0]


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

# The explicit-capable replace: swaps the face into an existing clip rather
# than regenerating it, unlike everything Runware serves. Path, model id and
# field names are all env-settable for the same reason the Runware swap
# fields are -- a wrong one is the one failure that looks like success.
MODELSLAB_FACESWAP_PATH = os.getenv('ML_FACESWAP_PATH', 'video/face_swap')
MODELSLAB_FACESWAP_MODEL = os.getenv('ML_MODEL_FACESWAP', 'video-face-swap')
MODELSLAB_FACESWAP_VIDEO_FIELD = os.getenv('ML_FACESWAP_VIDEO_FIELD', 'init_video')
MODELSLAB_FACESWAP_FACE_FIELD = os.getenv('ML_FACESWAP_FACE_FIELD', 'target_image')

# Dimensions are per model, not global: Seedream refuses anything under
# 3,686,400 pixels, and the Google models take only sizes from their own list.
# A tier is therefore a name for "about this big on whichever model is running",
# and RESOLUTION_PX is the fallback for anything not listed.
MODEL_PX = {
    'seedream-4-5':    {'1k': (1664, 2432), '2k': (1664, 2432), '4k': (3072, 4096)},
    'seedream-5-pro':  {'1k': (1664, 2432), '2k': (1664, 2432), '4k': (3072, 4096)},
    'nano-banana-pro': {'1k': (848, 1264), '2k': (1696, 2528), '4k': (3392, 5096)},
    'nano-banana-2':   {'1k': (848, 1264), '2k': (1696, 2528), '4k': (3392, 5096)},
}
RESOLUTION_PX = {
    '1k': (1664, 2432),
    '2k': (1664, 2432),
    '4k': (3072, 4096),
}


# Frame shapes for a still. 2:3 is closest to the frame every still had before
# there was a choice; a request that names no shape still gets that exact frame.
IMAGE_ASPECTS = ('1:1', '4:5', '5:4', '3:4', '4:3', '2:3', '3:2', '9:16', '16:9', '21:9')
DEFAULT_IMAGE_ASPECT = '2:3'

# The Google models take sizes from their own list only, one per shape.
GOOGLE_PX_2K = {
    '1:1': (2048, 2048), '2:3': (1696, 2528), '3:2': (2528, 1696),
    '3:4': (1792, 2400), '4:3': (2400, 1792), '4:5': (1856, 2304),
    '5:4': (2304, 1856), '9:16': (1536, 2752), '16:9': (2752, 1536),
    '21:9': (3168, 1344),
}
SEEDREAM_MIN_PX = 3686400
MAX_IMAGE_SIDE = 4096


def image_aspects_for(model_key):
    """The frame shapes a model serves, in the picker's own order."""
    return list(IMAGE_ASPECTS)


def dimensions(model_key, resolution, aspect=None):
    """(width, height) for a still. The rung is a pixel budget and the aspect
    only reshapes it, so a 16:9 still costs what a 2:3 one does."""
    sizes = MODEL_PX.get(model_key) or RESOLUTION_PX
    base = sizes.get(resolution) or sizes.get('2k') or RESOLUTION_PX['2k']
    if aspect not in IMAGE_ASPECTS:
        return base
    if model_key in ('nano-banana-pro', 'nano-banana-2'):
        w, h = GOOGLE_PX_2K[aspect]
        # Google's 1K list is its 2K list halved.
        return (w // 2, h // 2) if resolution == '1k' else (w, h)
    rw, rh = (int(x) for x in aspect.split(':'))
    budget = max(base[0] * base[1], SEEDREAM_MIN_PX)
    # The pair on the 64 grid closest to the shape that neither drops under
    # Seedream's floor nor overshoots the rung it was priced at.
    best = None
    for width in range(64, MAX_IMAGE_SIDE + 1, 64):
        height = max(64, round(width * rh / rw / 64) * 64)
        if height > MAX_IMAGE_SIDE or width * height < SEEDREAM_MIN_PX:
            continue
        if width * height > budget * 1.15:
            break
        err = (abs(width / height - rw / rh) / (rw / rh), abs(width * height - budget))
        if best is None or err < best[0]:
            best = (err, (width, height))
    return best[1] if best else base
# Frame shape and pixel count are separate axes: a rung says how much detail a
# clip is billed for, an aspect says what shape it is. A reel and a 16:9 cut of
# the same scene cost the same, so the rung alone could never express the one
# thing a creator most wants to choose.
ASPECTS = ('9:16', '4:5', '1:1', '16:9')
DEFAULT_ASPECT = '9:16'

VIDEO_PX = {
    '9:16': {'480p': (480, 854), '720p': (720, 1280), '1080p': (1080, 1920)},
    '4:5':  {'480p': (480, 600), '720p': (720, 900), '1080p': (1080, 1350)},
    '1:1':  {'480p': (480, 480), '720p': (720, 720), '1080p': (1080, 1080)},
    '16:9': {'480p': (854, 480), '720p': (1280, 720), '1080p': (1920, 1080)},
}


def video_px(aspect, resolution):
    """The pixel size of one (aspect, rung) pair. Both fall back rather than
    raise: a picker that gains an option before the server does should make a
    plainer clip, not a failed generation the creator already paid for."""
    row = VIDEO_PX.get(aspect) or VIDEO_PX[DEFAULT_ASPECT]
    return row.get(resolution) or row['720p']


def takes_aspect(model_key):
    """Whether a model can be asked for a frame shape at all.

    One that replaces the person in an existing clip inherits the source's
    frame, so an aspect picker in front of it is a control that changes
    nothing -- which is worse than no control, because it reads as a promise.
    """
    if model_key in _NO_DURATION_MODELS or model_key in RUNG_SCALED_MODELS:
        return False
    return _video_fields(model_key).get('shape') != 'replace'

# Takes any size, so the clip's own shape is kept and scaled to the rung the
# creator picked and paid for, rather than run at whatever the phone filmed.
RUNG_SCALED_MODELS = ('wan-2-2-animate', 'p-video-animate')
_RUNG_SHORT_SIDE = {'480p': 480, '720p': 720, '1080p': 1080}

# Wan 2.7 takes a fixed set of sizes and refuses anything else outright, so a
# phone clip's own 480p dimensions are not a size it can be asked for. Snapping
# is the only option a swap has: the source is whatever the creator filmed.
MODEL_VIDEO_SIZES = {
    'wan-2-6-rp': ((1280, 720), (720, 1280), (1920, 1080), (1080, 1920)),
    'wan-2-7': ((1280, 720), (720, 1280), (960, 960), (1088, 832), (832, 1088),
                (1920, 1080), (1080, 1920), (1440, 1440), (1632, 1248),
                (1248, 1632)),
    'wan-3-0': ((832, 480), (480, 832), (624, 624), (720, 544), (544, 720),
                (1280, 720), (720, 1280), (960, 960), (1104, 832), (832, 1104),
                (1920, 1080), (1080, 1920), (1440, 1440), (1648, 1248),
                (1248, 1648)),
    **{m: ((864, 496), (752, 560), (640, 640), (560, 752), (496, 864),
           (1280, 720), (1112, 834), (960, 960), (834, 1112), (720, 1280))
       for m in ('seedance-2-0', 'seedance-2-0-fast')},
    'minimax-h3': ((1344, 768), (1024, 768), (768, 768), (768, 1024), (768, 1344),
                   (2560, 1440), (1920, 1440), (1440, 1440), (1440, 1920),
                   (1440, 2560)),
    **{m: ((1920, 1080), (1080, 1920), (1440, 1440))
       for m in ('kling-2-6-mc', 'kling-3-0-mc', 'kling-3-0-omni')},
    'minimax-h3-fast': ((864, 480), (640, 480), (480, 480), (480, 640), (480, 864)),
}


# Seconds a model will accept. Wan 2.7 refuses anything outside 2-15 outright,
# and a swap's length comes from the file rather than a picker, so the ask is
# clamped to the model's own range instead of failing at the provider.
# The durations a model serves. The two that price by preset take those
# presets and nothing between them: offering 7s to one of them is offering a
# length the provider refuses.
MODEL_VIDEO_DURATIONS = {
    'wan-2-2': (5, 8, 10, 15),
    'wan-2-6-rp': (5, 10, 15),
    'wan-2-2-lora': (5, 8),
    'wan-2-2-gv': (5, 8),
    'h3-gv': (5, 10, 15),
    'wan-2-5': (3, 5, 10),
    'seedance-2-5': (3, 5, 10),
}


# Longer than one clip: parts chained end frame to start frame and joined.
# Only lengths that split into whole parts the model serves, so what is
# generated is exactly what is priced. Needs ffmpeg for the frame and the join.
CHAIN_DURATIONS = {'wan-2-2': (20, 30), 'wan-2-6-rp': (20, 30),
                   'wan-2-2-lora': (16, 24, 32), 'wan-2-2-gv': (10, 16, 24, 32),
                   'h3-gv': (20, 30, 45, 60, 90, 120)}


def chain_plan(model_key, seconds):
    """The part lengths a chained clip runs as, or None for a single clip."""
    if not HAS_FFMPEG or seconds not in CHAIN_DURATIONS.get(model_key, ()):
        return None
    for part in sorted(MODEL_VIDEO_DURATIONS.get(model_key) or (), reverse=True):
        if seconds % part == 0:
            return [part] * (seconds // part)
    return None


def model_durations(model_key):
    """The discrete lengths a model serves, or None when it takes any whole
    number in its range."""
    single = list(MODEL_VIDEO_DURATIONS.get(model_key) or ())
    if single and HAS_FFMPEG:
        single += list(CHAIN_DURATIONS.get(model_key, ()))
    return sorted(set(single)) or None


# ModelsLab's face swap is not a Runware model, so it carries no entry in
# MODEL_VIDEO_FIELDS -- it is never told a duration either, for the same
# reason p-video-replace is not: the output runs as long as the source clip.
_NO_DURATION_MODELS = frozenset({'ml-face-swap'})

MODEL_VIDEO_SECONDS = {
    'wan-2-2': (5, 15),
    'wan-2-6-rp': (5, 15),
    'wan-2-2-lora': (5, 8),
    'wan-2-2-gv': (5, 8),
    'h3-gv': (5, 15),
    'wan-2-5': (3, 10),
    'seedance-2-5': (3, 10),
    'ml-face-swap': (1, 60),
    'wan-2-7': (2, 15),
    # The source clip's own length, whatever it is: this model is never told a
    # duration, so nothing here may shorten what it will be billed for.
    'p-video-replace': (1, 60),
    'seedance-2-0': (4, 15),
    'seedance-2-0-fast': (4, 15),
    'minimax-h3': (4, 15),
    'minimax-h3-fast': (4, 15),
    'wan-3-0': (2, 15),
    'kling-2-6-mc': (1, 60),
    'p-video-animate': (1, 60),
    'kling-3-0-mc': (1, 60),
    'kling-3-0-omni': (3, 15),
}


def takes_duration(model_key):
    """Whether a model can be told how long to run. One that cannot runs the
    length of the clip it is given, so nothing may quote it anything else."""
    if model_key in _NO_DURATION_MODELS:
        return False
    return _video_fields(model_key).get('shape') not in ('replace', 'motion')


def wants_face_only(model_key):
    """Whether a model should be sent her face references and nothing else.

    Only the face swap endpoint: it moves a face and nothing else. A model that
    replaces the person takes her body from the references too now -- a face on
    a borrowed body is not her -- and is told which image is which, so a body
    photo is named as her build rather than left to read as a second identity.
    """
    return model_key == 'ml-face-swap'


def wants_body_only(model_key):
    """Kling motion control takes one photo of her, and it has to carry her
    build as well as her face, so it is the full body."""
    return model_key in KLING_MOTION_MODELS


# A model asking for a clean portrait is not helped by thirty of them, and each
# extra one is another chance to pull her face towards an average.
MODEL_REF_CAP = {'kling-2-6-mc': 1, 'wan-2-2-animate': 1, 'p-video-animate': 1, 'kling-3-0-mc': 1, 'p-video-replace': 4, 'wan-2-7': 3, 'minimax-h3': 5, 'minimax-h3-fast': 5,
                 'kling-3-0-omni': 4}

# The studio offers a handful of her views, not thirty, so a model that takes
# that many is still asked for the few a creator would actually tick.
PICK_REF_MAX = 6


def ref_cap(model_key):
    """How many of her photos a model is sent, and so how many the picker lets
    a creator tick for it."""
    return min(MODEL_REF_CAP.get(model_key, MAX_VIDEO_REFERENCES), PICK_REF_MAX)


def video_seconds(model_key, seconds):
    lo, hi = MODEL_VIDEO_SECONDS.get(model_key, (1, 30))
    return max(lo, min(hi, int(seconds or 0) or lo))


RUNWARE_SEARCH_CATEGORIES = ('checkpoint', 'lora', 'lycoris', 'controlnet',
                             'vae', 'embeddings')


def search_models(query, category=None, limit=20):
    """Ask the provider which models it has, by name.

    Every model id in this file was read off documentation; three of them were
    wrong, and a wrong one fails as `Invalid value for 'model'` after the job
    has been priced. The provider knows its own catalogue, so ask it.
    """
    provider = get_provider('runware')
    task = {'taskType': 'modelSearch',
            'taskUUID': str(uuid.uuid4()),
            'search': str(query or '')[:80],
            'limit': max(1, min(int(limit or 20), 50))}
    # Runware's categories are model kinds, not media: 'video' is not one, and
    # sending it filtered every answer away. Anything else searches everything.
    if category in RUNWARE_SEARCH_CATEGORIES:
        task['category'] = category
    rows = provider._send([task])
    out = []
    for row in rows:
        for hit in (row.get('results') or [row]):
            air = hit.get('air') or hit.get('model') or hit.get('id')
            if air:
                out.append({'air': air,
                            'name': hit.get('name') or hit.get('title') or '',
                            'type': hit.get('type') or hit.get('category') or '',
                            'version': str(hit.get('version') or '')})
    return out


def video_size(model_key, width=0, height=0, resolution=None, aspect=None):
    """The size to ask a model for, given the source's own.

    Aspect ratio first and pixel count second: a portrait clip sent at a
    landscape size comes back letterboxed or cropped through her face, which is
    worse than a rung either side of what was asked for.

    With no source, the asked-for aspect is the shape: that is the whole point
    of the picker, and a job with nothing to inherit from has nothing else to
    go on.
    """
    import math
    fallback = video_px(aspect or DEFAULT_ASPECT, resolution)
    w, h = int(width or 0), int(height or 0)
    sizes = MODEL_VIDEO_SIZES.get(model_key)
    if not sizes:
        # No fixed list to snap to, so the source's own shape is the right
        # answer: a clip whose person is replaced should come out the shape it
        # went in, and the rung default would letterbox a landscape one. Held
        # to a multiple of 16, because a phone crop is any width it likes --
        # 406 is a real one -- and an encoder takes macroblocks or nothing.
        if w > 0 and h > 0:
            rung = rung_for(model_key, resolution)
            if model_key in RUNG_SCALED_MODELS and rung in _RUNG_SHORT_SIDE:
                k = _RUNG_SHORT_SIDE[rung] / float(min(w, h))
                w, h = w * k, h * k
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
    'portrait': 'a head-and-shoulders selfie, looking into the lens',
    'closeup': ('a tight full-face beauty close-up, her whole face filling the frame '
                'from hairline to chin, facing the lens, her makeup crisp in every detail'),
    'half': 'a waist-up photo',
    'full': 'a full-body photo',
    'candid': 'a candid photo in the middle of an everyday moment',
    'mirror': 'a mirror selfie, phone in hand',
    'lingerie': 'a boudoir photo in lingerie',
    'implied': ('an implied-nude photo — bare shoulders and back, the camera '
                'angle and framing suggesting more than it shows'),
    'sheer': 'a moody photo in a sheer, partially see-through robe',
    'bedroom': 'a relaxed, sultry bedroom photo',
    'topless': 'a topless boudoir photo',
    'nude': 'a full nude boudoir photo',
    'explicit': 'an explicit intimate photo, candid and unposed',
}

# How far the camera is from her, picked on its own. Where a zoom is chosen the
# shot's own distance words step aside, so the two never argue.
ZOOM = {
    'auto': ('Auto', ''),
    'close': ('Close', 'framed close, her face and shoulders filling the frame'),
    'medium': ('Medium', 'framed at a medium distance, from the waist up'),
    'wide': ('Wide', 'framed wide, her whole body from head to toe with the room around her'),
    'far': ('Far', 'shot from far away, she is small in the frame and the surroundings dominate'),
}
SHOT_FRAMING_NEUTRAL = {
    'portrait': 'a selfie, looking into the lens',
    'closeup': ('a photo of her face, facing the lens, her makeup crisp in every detail'),
    'half': 'a photo',
    'full': 'a photo',
}

def zoom_text(key):
    return (ZOOM.get(key) or ('', ''))[1]

# The one thing a close-up takes from the reference beyond who she is: the
# face photo is what the creator uploaded to show her makeup, and a face this
# close with different makeup reads as a different woman.
MAKEUP_FROM_REFERENCE = ('Her makeup copies the face reference photo exactly: the same '
                         'eye makeup, lashes, brows, blush, contour and lip colour and finish, '
                         'no heavier and no lighter.')

# The same shots with what she wears taken out, for when the creator has typed
# the clothing herself: her words win, so the framing must not argue with them.
SHOT_FRAMING_BARE = {
    'lingerie': 'a boudoir photo',
    'sheer': 'a moody, intimate photo',
    'topless': 'an intimate boudoir photo',
    'nude': 'an intimate full-body boudoir photo',
}

SHOT_LEVEL = {
    'portrait': 'sfw', 'closeup': 'sfw', 'half': 'sfw', 'full': 'sfw', 'candid': 'sfw',
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

# A style the shot already says, so it is not said twice.
STYLE_IN_SHOT = {'mirror': ('mirror-selfie',), 'portrait': ('pov-selfie',),
                 'candid': ('candid',), 'explicit': ('candid',)}

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

# Scenes that name a place of their own, without it, for when the creator
# picked where she is: the location always wins.
SCENES_ACTION = {
    'shower': 'wet, steamy skin',
    'bath': 'wet skin, bubbles',
    'vanity': 'doing her makeup',
    'walk-in-closet': 'choosing an outfit',
}

# Scenes that name clothing, without it, for when the creator typed her own.
SCENES_BARE = {
    'lingerie-tease': 'teasing the camera',
    'activewear': 'stretching on a yoga mat',
}

# The phone the photo was taken on, which is most of how a creator's photo
# looks: each one is written to a visibly different result, so picking a
# budget phone over a flagship is a choice the output actually shows.
CAMERAS = {
    'auto': ('Auto', ''),
    'iphone-16-pro': ('iPhone 16 Pro', 'shot on an iPhone 16 Pro main camera: crisp 48MP detail, '
                      'natural HDR, true colour, gentle natural depth of field'),
    'iphone-14': ('iPhone 14', 'shot on an iPhone 14: clean detail, slightly warm Apple colour, '
                  'mild HDR, moderate sharpening'),
    'iphone-11': ('iPhone 11', 'shot on an iPhone 11: softer detail, visible noise in the shadows, '
                  'older HDR with slightly washed-out highlights'),
    'iphone-front': ('iPhone front camera', 'shot on an iPhone front camera: wide selfie lens with '
                     'slight edge stretch, softer detail, a little smoothing'),
    'galaxy-s24-ultra': ('Samsung S24 Ultra', 'shot on a Samsung Galaxy S24 Ultra: very sharp detail, '
                         'punchy saturated colour, bright exposure'),
    'galaxy-s22': ('Samsung S22', 'shot on a Samsung Galaxy S22: saturated Samsung colour, strong '
                   'sharpening, lifted shadows'),
    'pixel-8': ('Google Pixel 8', 'shot on a Google Pixel 8: contrasty HDR, cool neutral colour, '
                'deep shadows'),
    'budget-android': ('Budget Android', 'shot on a cheap Android phone: low detail, smeared noise '
                       'reduction, heavy JPEG compression, blown highlights'),
    'disposable': ('Disposable camera', 'shot on a disposable film camera: direct flash, coarse grain, '
                   'faded colour, slightly soft focus'),
}

# The cameras that are phones: in a selfie, the one she is holding.
PHONE_CAMERAS = tuple(k for k in CAMERAS if k not in ('auto', 'disposable'))

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
    'phone-flash': 'harsh direct phone flash',
    'night': 'night-time, lit only by warm lamps and city light, dark windows',
    'dark': 'dark, moody low-key light, deep shadows, most of the frame in shade',
    # No fixed words: the sentence depends on whether a place photo is sent.
    'match-location': '',
}

EXPRESSIONS = {
    'auto': ('Auto', ''),
    'soft-smile': ('Soft smile', 'a soft, relaxed closed-mouth smile'),
    'big-smile': ('Big smile', 'a big genuine smile, teeth showing, eyes creased'),
    'laughing': ('Laughing', 'laughing mid-moment, eyes squinting'),
    'smirk': ('Smirk', 'a playful half smirk, one corner of her mouth raised'),
    'wink': ('Wink', 'winking at the camera with a small smile'),
    'kissy-pout': ('Kissy pout', 'puckered lips blowing a kiss at the camera'),
    'scrunched': ('Scrunched face', 'a goofy scrunched-nose face, lips pouted, eyes squeezed'),
    'tongue-out': ('Tongue out', 'playfully sticking her tongue out'),
    'deadpan': ('Deadpan', 'a neutral deadpan look straight into the lens'),
    'sultry': ('Sultry', 'a sultry heavy-lidded gaze, lips slightly parted'),
    'biting-lip': ('Biting lip', 'biting her lower lip, eyes on the lens'),
    'surprised': ('Surprised', 'eyebrows raised, mouth open in mock surprise'),
    'shy': ('Shy', 'a shy smile, glancing down and away'),
}

# Real phone selfies are rarely taken through clean glass. Which glass depends
# on the photo: the phone's own lens, or the mirror she is holding it up to.
SMUDGES = {
    'pov-selfie': ('faint fingerprint smudges and dust specks on the phone lens, '
                   'a soft hazy glow over one corner'),
    'mirror-selfie': ('fingerprint smudges, dust spots and faint streaks on the '
                      'mirror glass, catching a little glare'),
}


def expression_text(key):
    return (EXPRESSIONS.get(key) or ('', ''))[1]


# How real the photo looks. The first is the default and the target: a real
# creator's own phone photo, skin, light and all.
QUALITY = {
    'real-phone': ('Real phone photo', (
        'Real unretouched smartphone photo: true-to-life skin with visible pores, peach fuzz, fine texture '
        'and small imperfections; light behaves like a real room, with soft falloff and real shadows; '
        'slight sensor noise and phone sharpening; accurate colour — not airbrushed, not glossy, not a '
        'studio render, not CGI.')),
    'flagship-clean': ('Clean flagship phone', (
        'Clean, bright flagship-phone photo: crisp detail, gentle HDR, even exposure, real skin texture '
        'with pores still visible — not airbrushed, not CGI.')),
    'pro-shoot': ('Professional shoot', (
        'Professional full-frame camera photo: controlled light, shallow depth of field, a light retouch '
        'that keeps real skin texture — photographic, not CGI.')),
}
DEFAULT_QUALITY = 'real-phone'

# Said only when she is conditioned on photos: the details that make a
# returning fan recognise her are the first ones a model lets drift.
CONSISTENCY = ('Every identifying detail — face shape, eyes, freckles, moles, tattoos, piercings, '
               'hair colour and cut — exactly as in the reference images.')

# Directions for jobs that never go to Gemini: anything above safe work. A few
# per scene, then per shot, in a creator's own shorthand for her next post.
DIRECTIONS = {
    'lingerie-tease': ['kneeling on the bed, one strap slipping off her shoulder, biting her lip',
                       'hand on her hip, looking back over her shoulder with a half smile'],
    'shower': ['head tilted back under the water, eyes closed, hands in her wet hair',
               'looking down with a shy smile, water running down her shoulders'],
    'bath': ['leaning back in the bubbles, one leg raised, smirking at the lens',
             'chin resting on the edge of the tub, wet hair, playful look'],
    'undressing': ['pulling her top over her head, stomach showing, laughing',
                   'hooking her thumbs into her waistband, glancing at the lens'],
    'activewear': ['mid-stretch on the mat, looking up at the phone', 'sitting cross-legged, towel round her neck, flushed cheeks'],
    'just-woke-up': ['lying on her side in the sheets, sleepy half smile', 'sitting up in bed, messy hair, stretching one arm'],
    'towel-drop': ['holding the towel loosely at her chest, looking back', 'towel sliding off one hip, surprised grin'],
    'vanity': ['leaning into the mirror doing her lashes, eyes on the lens', 'sitting sideways on the stool, legs crossed, glancing back'],
    'walk-in-closet': ['holding two outfits up, pouting at the lens', 'leaning on the shelves, one hand in her hair'],
    'exposed': ['lying back on the pillows, arms above her head, heavy-lidded look',
                'propped on her elbows, knees up, looking straight at the lens'],
    'solo-touch': ['lying back, eyes half closed, lips parted', 'hand trailing down her stomach, biting her lip'],
    'bent-over': ['bent over the edge of the bed, looking back over her shoulder', 'hands on the dresser, arched back, glancing back with a smirk'],
    'nipple-play': ['cupping her chest, looking down at the lens', 'fingertips at her chest, playful wink'],
    'aftermath': ['sprawled across the sheets, flushed cheeks, messy hair', 'lying on her stomach, chin on her hands, satisfied smile'],
}
DIRECTIONS_BY_SHOT = {
    'closeup': ['eyes on the lens, lips softly closed, relaxed brows',
                'chin tilted slightly down, soft half-smile, looking up into the lens'],
    'portrait': ['chin resting on her hand, soft smile, looking into the lens',
                 'hair tucked behind one ear, laughing at something off camera'],
    'half': ['leaning on the counter, coffee in hand, relaxed smile',
             'arms folded loosely, head tilted, playful look'],
    'full': ['walking towards the lens mid-step, hair moving',
             'leaning against the wall, one foot up, looking off to the side'],
    'candid': ['laughing mid-sentence, glancing away from the lens',
               'reaching for something on a shelf, looking back over her shoulder'],
    'mirror': ['hip popped, phone covering half her face, peace sign',
               'turned side-on to the mirror, checking her outfit'],
    'lingerie': ['kneeling on the bed, hands on her thighs, looking up at the lens',
                 'standing by the window, one hand in her hair, looking back'],
    'implied': ['sitting with her back to the lens, looking over her shoulder',
                'lying on her stomach, sheets at her waist, chin on her arms'],
    'sheer': ['standing in the doorway, robe falling open, leaning on the frame',
              'sitting on the edge of the bed, robe slipping off one shoulder'],
    'bedroom': ['lying across the bed, phone held above her, lazy smile',
                'sitting against the headboard, knees up, teasing look'],
    'topless': ['arm across her chest, soft smile, looking at the lens',
                'sitting on the bed, hair over one shoulder, glancing down'],
    'nude': ['lying on her side along the bed, head propped on one hand',
             'standing by the window, looking back over her shoulder'],
    'explicit': ['lying back on the pillows, knees apart, eyes on the lens',
                 'on her knees on the bed, looking back with a smirk'],
}


_DOING_STYLE = {'pov-selfie': 'taking a pov selfie', 'mirror-selfie': 'taking a mirror selfie',
                'candid': 'caught mid-moment', 'photoshoot': 'posing for a photoshoot'}
_DOING_SHOT = {'portrait': 'looking into the camera', 'closeup': 'looking into the lens',
               'half': 'posing', 'full': 'standing', 'candid': 'caught mid-moment',
               'mirror': 'taking a mirror selfie'}


def doing_from_choices(shot, scene='', location='', style='', expression='', rng=None,
                       location_text=''):
    """A short line for what she is doing that says only what the dropdowns
    already say ("taking a pov selfie in the kitchen, kissy pout"). Anything
    above safe-for-work keeps the built-in pose text, which is scene-specific."""
    if SHOT_LEVEL.get(shot, 'sfw') != 'sfw' or SCENES.get(scene, ('sfw',))[0] != 'sfw':
        return pick_direction(shot, scene, rng)
    verb = _DOING_STYLE.get(style) or _DOING_SHOT.get(shot) or 'posing'
    place = SCENES.get(location or scene, ('', ''))[1] or (
        f'in {location_text}' if location_text else '')
    mood = (EXPRESSIONS.get(expression) or ('', ''))[0].lower() if expression != 'auto' else ''
    return ', '.join(b for b in (' '.join(b for b in (verb, place) if b), mood) if b)


def camera_text(key):
    return (CAMERAS.get(key) or ('', ''))[1]


def quality_text(key):
    return (QUALITY.get(key) or QUALITY[DEFAULT_QUALITY])[1]


def pick_direction(shot, scene, rng=None):
    """A built-in direction for this shot and scene."""
    pool = (DIRECTIONS.get(scene or '') or DIRECTIONS_BY_SHOT.get(shot or '')
            or DIRECTIONS_BY_SHOT['portrait'])
    return (rng or random).choice(pool)


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
    'illustration, 3d render, doll, plastic skin, child, teen, underage, '
    'airbrushed, glossy skin, CGI, over-smoothed skin, studio glamour retouching'
)

# The look every still is asked for, content and character builder alike:
# the creator's reference is casual phone photos, not studio renders. No light
# in it: that belongs to the lighting pick, and saying it here said it twice.
PHOTO_LOOK = QUALITY[DEFAULT_QUALITY][1]


def shots_for_level(level):
    """The shots a persona at this NSFW level may ask for."""
    try:
        ceiling = LEVEL_ORDER.index(level or 'sfw')
    except ValueError:
        ceiling = 0
    return [s for s, l in SHOT_LEVEL.items() if LEVEL_ORDER.index(l) <= ceiling]


def shot_allowed(shot, level):
    return shot in shots_for_level(level)


# What goes wrong in a clip rather than a still: her drifting into someone
# else, and a camera that moves when the shot should hold.
VIDEO_NEGATIVE = (
    'different person, identity change, face change, morphing face, '
    'face distortion, warped face, melted face, changing hairstyle, '
    'changing clothes, changing background, extra arms, extra legs, '
    'deformed hands, malformed limbs, unnatural movement, jerky motion, '
    'jitter, flickering, camera movement, zoom, zoom in, zoom out, pan, tilt, '
    'dolly, orbit, camera shake, scene change, subtitles, changing tattoos, '
    'extra tattoos, new tattoos, missing tattoos, changing piercings, '
    'extra piercings, new piercings, missing piercings'
)


# Never dropped, whatever the prompt says.
_KEEP_NEGATIVE = frozenset({'child', 'teen', 'underage'})


def negative_for(spec):
    """The negative prompt minus anything the prompt itself asks for: a clip
    told to zoom in must not also be told not to."""
    terms = [t.strip() for t in (spec.get('negative') or NEGATIVE_PROMPT).split(',')]
    prompt = (spec.get('prompt') or '').lower()

    def asked(term):
        return re.search(r'\b' + re.escape(term.lower()) + r'(?:s|es|ed|ing|ning|ned)?\b',
                         prompt) is not None

    return ', '.join(t for t in terms if t and (t.lower() in _KEEP_NEGATIVE or not asked(t)))


def merge_negative(extra='', video=False):
    """The creator's additions are added to the baseline, never swapped for it:
    the baseline is what keeps a generation off anything underage, and a text
    box is not somewhere that should be editable."""
    base = NEGATIVE_PROMPT + (', ' + VIDEO_NEGATIVE if video else '')
    extra = (extra or '').strip().strip(',')
    return (base + ', ' + extra) if extra else base


def _norm(text):
    return re.sub(r'[^a-z0-9 ]+', '', text.lower()).strip()


def _dedupe_clauses(text):
    """Drop every clause that says again what an earlier one already said.

    Shot, scene, style and the creator's direction are written separately and
    overlap ("a mirror selfie" twice, "candid and unposed" twice); a prompt
    that repeats itself weights the repeat, not the picture.
    """
    seen, out = [], []
    for sentence in re.split(r'(?<=[.!?])\s+', text.strip()):
        end = sentence[-1] if sentence[-1:] in '.!?' else ''
        body = sentence[:-1] if end else sentence
        kept = []
        for clause in body.split(', '):
            n = _norm(clause)
            if not n or any(n == o or f' {n} ' in f' {o} ' for o in seen):
                continue
            seen.append(n)
            kept.append(clause.strip())
        if kept:
            out.append(', '.join(kept) + (end or '.'))
    return ' '.join(out)


def _sentence(text):
    text = (text or '').strip().rstrip(' .')
    return (text[0].upper() + text[1:] + '.') if text else ''


def build_prompt(appearance, shot, outfit=None, has_reference=False, extra='',
                 style='', scene='', camera='', lighting='', direction='',
                 banned=(), age=None, quality='', clothing='', features='',
                 expression='', smudges=False, location='', zoom='',
                 location_ref='', location_text='', scene_text=''):
    """The positive prompt for one generation, written the way a creator
    would brief her own post: what it is for, who, the shot, what she wears,
    what she is doing, the phone and the light, how real it looks.

    With a reference photo the prompt describes what changes, not who she is —
    leading with a fresh description invites the model to draw a new person and
    ignore the reference. The reference fixes who she is and nothing else: what
    she wears comes from the creator's clothing, the shot or the scene.
    """
    outfit = outfit or {}
    clothing = (clothing or outfit.get('clothing') or '').strip().rstrip('.')
    level = SHOT_LEVEL.get(shot, 'sfw')
    scene_row = ('explicit', scene_text) if scene_text else SCENES.get(scene, ('sfw', ''))
    intimate = level != 'sfw' or scene_row[0] != 'sfw'

    zoomed = zoom_text(zoom)
    framing = ((clothing and SHOT_FRAMING_BARE.get(shot))
               or (zoomed and SHOT_FRAMING_NEUTRAL.get(shot))
               or SHOT_FRAMING.get(shot, SHOT_FRAMING['portrait']))
    where = (clothing and SCENES_BARE.get(scene)) or scene_row[1]
    place = SCENES.get(location, ('', ''))[1] if SCENES.get(location, ('',))[0] == 'sfw' else ''
    if location_text and not place:
        place = (location_text if re.match(r'(in|at|on|by|inside|outside|near|under)\b',
                                           location_text, re.I) else 'in ' + location_text)
    if location_ref:
        # The creator's own photo of the place wins over every dropdown.
        place = ''
        where = ('' if scene_row[0] == 'sfw'
                 else SCENES_ACTION.get(scene) or SCENES_BARE.get(scene) or where)
    if place:
        act = scene_text or ('' if scene in ('', location) else (SCENES_ACTION.get(scene) or where))
        where = ', '.join(b for b in (place, act) if b)
    if not where and outfit.get('location'):
        where = f"in {outfit['location']}"
    styled = '' if style in STYLE_IN_SHOT.get(shot, ()) else STYLES.get(style, '')
    glass = (style if style in SMUDGES
             else next(iter(STYLE_IN_SHOT.get(shot, ())), '')) if smudges else ''

    purpose = '' if intimate else 'A real photo for her social media feed'
    who = 'the exact same woman as the reference images' if has_reference else appearance
    light = LIGHTING.get(lighting, '') or (
        f"{outfit['lighting']} lighting" if outfit.get('lighting') else '')
    # A model told only "the same room" relights her flat and frontal; the
    # room's own light has to be spelled out onto her.
    matched = ''
    if lighting == 'match-location' or (location_ref and lighting in ('', 'auto')):
        light = ''
        matched = (f'Light her exactly as the {location_ref} reference image is lit: the same '
                   'light sources, direction, colour temperature, hardness and contrast fall on '
                   'her as on the room — if it is backlit by windows she is backlit with a '
                   'bright rim and her front in soft shadow; sun patches and shadows land on her '
                   'body the same way; her exposure matches the room, not a separate flash or '
                   'studio light' if location_ref else
                   'She is lit by the same light as the setting around her, from the same '
                   'direction and colour, with no separate studio light')

    body = ' '.join(filter(None, [
        _sentence(((purpose + ': ') if purpose else '')
                  + ', '.join(b for b in (who, framing, zoomed, where, styled) if b)),
        (('The reference images set who she is — not what she wears'
          + ('.' if location_ref else ' or where she is.')) if has_reference else ''),
        _sentence(f'The photo is taken {place}; the setting must clearly be that place' if place else ''),
        _sentence(f'The photo is taken in the exact place shown in the {location_ref} '
                  'reference image — the same room, layout, furniture and light; the '
                  'setting must clearly match it, with nobody else in it'
                  if location_ref else ''),
        _sentence(matched),
        (MAKEUP_FROM_REFERENCE if has_reference and shot == 'closeup' else ''),
        _sentence(f'She is wearing {clothing}' if clothing else ''),
        _sentence(direction),
        _sentence(expression_text(expression)),
        _sentence(features),
        _sentence(', '.join(b for b in (camera_text(camera), light,
                                        SMUDGES.get(glass, '')) if b)),
    ]))
    look = quality_text(quality) + (' ' + CONSISTENCY if has_reference else '')

    # The creator's own words go last, where a diffusion prompt weights them
    # least — they refine the shot, they do not get to replace who she is.
    extra = (' ' + extra.strip()) if (extra or '').strip() else ''
    prompt = (_dedupe_clauses(body) + ' ' + look + ' Fictional adult woman, '
              f'{max(18, int(age or 25))} years old.' + extra)
    return finish_prompt(prompt, banned, age, quality)


def finish_prompt(prompt, banned=(), age=None, quality=''):
    """What every still's prompt passes through last, whoever wrote it.

    A character's banned terms are struck from the finished prompt rather than
    trusted to the negative: a word the creator has forbidden should not reach
    the model at all, whichever field it was typed into. And a prompt the
    creator edited by hand still says she is an adult, because the edit is
    exactly where that sentence would go missing.
    """
    for term in (banned or ()):
        term = (term or '').strip()
        if term:
            prompt = re.sub(re.escape(term), '', prompt, flags=re.I)
    prompt = re.sub(r'\s{2,}', ' ', prompt).strip()
    if not any(text in prompt for _, text in QUALITY.values()):
        prompt = (prompt.rstrip(' .') + '. ' if prompt else '') + quality_text(quality)
    if 'fictional adult woman' not in prompt.lower():
        prompt = (prompt.rstrip(' .') + '. ' if prompt else '') + (
            f'Fictional adult woman, {max(18, int(age or 25))} years old.')
    return prompt


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


# The second clause a swap cannot be talked out of: who she is, in the detail a
# model otherwise averages away. Ours, like PRESERVE_CLAUSE, not the creator's.
IDENTITY_CLAUSE = (
    'The woman in the reference images is the only person who may appear. '
    'Reproduce her exactly, not approximately: the same face — bone structure, '
    'eyes, eyebrows, nose, lips, jaw, skin tone and texture, every beauty mark, '
    'freckle, scar and piercing — the same hair colour, length and style, and '
    'the same body — height, proportions, shoulders, waist, hips and legs, with '
    'every tattoo exactly where and as it appears in the references. Do not '
    'beautify, slim, smooth, age or restyle her, and do not add, remove or '
    'alter any feature or body part. Keep the clothing from the video.')

_ROLE_SAID = {'face': 'shows her face', 'body': 'shows her full body',
              'outfit': 'shows her outfit', 'location': 'shows the location'}


def locks_identity(model_key):
    """Whether a swap on this model carries IDENTITY_CLAUSE. Kling takes who she
    is from its one photo and uses the prompt only to steer the scene; the face
    swap endpoint has no prompt to put it in."""
    return model_key not in KLING_MOTION_MODELS and model_key != EXPLICIT_SWAP_MODEL


def uses_at_images(model_key):
    return model_key == 'kling-3-0-omni'


def _roles_sentence(roles, at_images=False):
    """Which reference image is which, by position: the payload has no other way
    to tell a model that one photograph is her face and another her body."""
    if not roles:
        return ''
    name = '@Image{n}' if at_images else 'reference image {n}'
    parts = [name.format(n=n) + ' ' + _ROLE_SAID.get(role, 'is another view of her')
             for n, role in enumerate(roles, 1)]
    text = '; '.join(parts)
    return text[0].upper() + text[1:] + '. All the images of her are the same woman.'


_OMNI_ROLE = {'face': 'shows her face (identity)',
              'body': 'shows her full body (identity)',
              'outfit': 'is for the outfit only',
              'location': 'is for the location only'}


def _omni_roles(roles):
    return ' '.join(f'@Image{n} {_OMNI_ROLE.get(r, "shows her (identity)")}.'
                    for n, r in enumerate(roles, 1))


def _omni_swap_prompt(motion, roles, place):
    """Omni links its inputs only by tag: untagged, it follows the place line
    and leaves the person in the clip as she was. Each photo is named for what
    it is, so an outfit or a room is never read as more of her."""
    tag = {r: f'@Image{n}' for n, r in reversed(list(enumerate(roles, 1)))}
    her = ' and '.join(f'@Image{n}' for n, r in enumerate(roles, 1)
                       if r not in ('outfit', 'location')) or '@Image1'
    place = place.strip().rstrip('.')
    if 'location' in tag or place:
        where = (f"the place shown in {tag['location']}" if 'location' in tag
                 else 'a different place')
        scene = ('Keep the original motion, timing and camera movement exactly as '
                 f'they are in @Video1, but set the scene in {where}'
                 + (f': {place[0].lower() + place[1:]}.' if place else '.'))
    else:
        scene = ('Keep the original motion, framing, pacing, lighting and '
                 'background exactly as they are in @Video1.')
    if 'location' in tag:
        scene += (f" Take only the room and background from {tag['location']}; "
                  f"ignore any person in it — nobody from {tag['location']} "
                  'appears in the clip.')
    if 'outfit' in tag:
        outfit = (f"She wears exactly the outfit shown in {tag['outfit']}, with the "
                  'same garments, colours, fabric, fit, length and every detail, '
                  'instead of the clothing in @Video1. Do not add, remove or '
                  f"restyle any piece of it. Take only the clothing from {tag['outfit']}"
                  ' — not the person wearing it, her face, hair, body or the '
                  'background.')
    else:
        outfit = 'Keep the clothing from @Video1.'
    identity = (IDENTITY_CLAUSE
                .replace('The woman in the reference images', f'The woman in {her}')
                .replace(' as it appears in the references.', ' as it appears.')
                .replace(' Keep the clothing from the video.', ''))
    motion = motion.strip().rstrip('.')
    if motion and motion[0].islower():
        motion = 'She ' + motion
    return ' '.join(part for part in (
        f'Edit @Video1: replace the woman in @Video1 with the woman shown in {her}, '
        'keeping her face and body exactly as in those images.',
        scene, outfit, identity, _omni_roles(roles),
        motion + '.' if motion else '') if part)


def build_swap_prompt(motion='', preserve=False, roles=None, place='',
                      at_images=False):
    """Instruction text for an edit, not for a still coming to life: the model
    is being told whose face to carry over, and what to leave alone.

    `preserve` adds the locked clause, which is ours and not the creator's:
    her own words are appended after it, where they can refine the swap but
    cannot talk the model out of keeping the clip. `roles` adds the identity
    clause: one entry per reference image, in the order they are sent, each
    `face`, `body` or anything else for another view. An empty list adds the
    clause without naming the images; None leaves it out.
    """
    base = ('Replace the woman in the reference video with the woman in the '
            'reference images, keeping her face and body consistent with them. '
            'Keep the original motion, framing, pacing and lighting exactly as '
            'they are in the video.')
    if at_images:
        return _omni_swap_prompt(motion, roles or [], place)
    new_place = bool(place.strip()) or 'location' in (roles or [])
    if new_place:
        clip = '@Video1' if at_images else 'the video'
        base = base.replace(
            'Keep the original motion, framing, pacing and lighting exactly as '
            f'they are in {clip}.',
            'Keep the original motion, timing and camera movement exactly as '
            f'they are in {"@Video1" if at_images else "the video"}, but set the '
            'scene in a different place.')
        if place.strip():
            base += f' Set in: {place.strip().rstrip(".")}.'
    if roles is not None:
        base = ' '.join(part for part in (base, IDENTITY_CLAUSE,
                                          _roles_sentence(roles, at_images)) if part)
    if preserve and not new_place:
        base = base + ' ' + PRESERVE_CLAUSE
    return (base + ' ' + motion.strip()) if motion.strip() else base


def build_animate_prompt(motion=''):
    """An explicit Animate with a motion clip: the photo is her, her outfit
    and the place, and the clip lends only its movement. The swap wording said the
    opposite -- keep the clip's clothes and background -- which this mode
    does not do."""
    base = ('The woman in the reference image performs the movement of the '
            'reference video: follow its motion, timing, pose and expressions '
            'exactly. Keep her face, hair, body, outfit and the setting exactly '
            'as they are in the reference image; take nothing from the video '
            'but the movement.')
    motion = motion.strip()
    return f'{base} {motion[0].upper()}{motion[1:]}' if motion else base


def build_video_prompt(motion=''):
    """A still becoming a clip. With an action, the action is the clip: a
    generic idle line after it only pulls the model back towards standing still."""
    keep = ('The same woman from the photo, with the same face, hair, tattoos, '
            'piercings, jewelry, clothes and body.')
    hold = 'The camera stays fixed: no zoom, no pan. Same lighting, same background.'
    motion = motion.strip().rstrip('.')
    if not motion:
        return (f'{keep} She moves naturally and subtly — a slow breath, a small '
                f'shift of weight, hair settling. {hold}')
    return (f'{keep} {motion[0].upper()}{motion[1:]}, a steady motion repeated '
            f'through the whole clip, her expression matching it. {hold}')


def build_chain_prompt(motion=''):
    """A later part of a chained clip. It starts on the previous part's last
    frame, but its action is its own: telling it to continue would make it
    repeat the part before."""
    return 'From the first frame, she changes to a new action. ' + build_video_prompt(motion)


def build_reel_prompt(prompt='', has_photo=False, character=False):
    """A safe-work reel. Without a character it may run from a prompt alone and
    makes no claim to be anybody -- so nothing in this wording describes a
    person, and a photo, when there is one, is what does. With a character her
    views are the reference photographs, and a photo is a person to replace."""
    text = (prompt or '').strip()
    if character and has_photo:
        lead = ('The woman in the first reference photographs — identical face, '
                'hair and features — in place of the person in the last '
                'photograph, with the same setting, pose, outfit and framing, '
                'filmed in a short vertical clip.')
    elif character:
        lead = ('The woman in the reference photographs — identical face, hair '
                'and features — filmed in a short vertical clip.')
    elif has_photo:
        lead = 'The woman in the reference photograph, filmed in a short vertical clip.'
    else:
        lead = 'A short, natural-looking clip.'
    tail = ('Shot on a phone, natural light, handheld and unstyled, realistic '
            'motion.')
    return ' '.join(part for part in (lead, text, tail) if part)


def build_multiref_prompt(prompt='', scenes=0):
    """Her identity references and a separate scene or outfit reference, named
    by position: the model is told which photographs say who she is and which
    say where she is, because nothing else in the payload distinguishes them."""
    text = (prompt or '').strip()
    which = ('the last photograph' if scenes == 1
             else f'the last {scenes} photographs')
    lead = ('The woman in the first photographs — identical face, hair and '
            f'features — in the setting, outfit and styling of {which}.'
            if scenes else
            'The woman in the reference photographs — identical face, hair and '
            'features.')
    tail = ('She moves naturally. Photorealistic, natural skin texture and '
            'lighting.')
    return ' '.join(part for part in (lead, text, tail) if part)


def build_extend_prompt(mode='continue', motion=''):
    """Continue a clip from its own last frame. The model is being asked to
    carry on rather than to start, which is a different instruction from an
    animate -- and a loop additionally has to arrive somewhere exact."""
    lead = {
        'continue': ('Continue this shot from the frame given. The same woman, '
                     'the same setting and the same camera — the motion simply '
                     'carries on.'),
        'longer': ('Continue this shot from the frame given, at length. The '
                   'same woman, the same setting and the same camera '
                   'throughout.'),
        'loop': ('Continue this shot from the first frame given and arrive '
                 'back at the closing frame given, so the clip loops '
                 'seamlessly. The same woman, setting and camera throughout.'),
    }.get(mode, 'Continue this shot from the frame given.')
    motion = (motion or '').strip()
    return (lead + ' ' + motion) if motion else lead


# ── Audio ─────────────────────────────────────────────────────────────────────
# Generated sound comes out of the generation or out of a second provider task
# that returns a clip already carrying it, both named from the environment
# because none of this is verified against the live catalogue. Keeping,
# dropping or replacing a clip's own track is ffmpeg on our side (the
# Dockerfile installs it); without ffmpeg those return the clip unchanged.
AUDIO_MODES = ('ambience', 'moaning', 'speech', 'custom', 'music', 'lipsync')

# Sound a clip gets without a provider pass, so no add-on is charged.
TRACK_MODES = ('original', 'none', 'upload')

AUDIO_PROMPTS = {
    'ambience': ('natural room tone for this scene — the quiet of the room, '
                 'fabric and movement, nothing musical and no speech'),
    'moaning': ('her breathing and soft moaning, in time with what is on '
                'screen, no words and no music'),
    'music': 'background music that fits the mood of the scene, no speech',
}

# Which route a clip gets its sound by. `native` asks the generation task for
# it; `task` runs a second video-to-audio task over the finished clip and takes
# back a muxed file. Native is the default because it is one call and one bill.
AUDIO_ROUTE = os.getenv('RW_AUDIO_ROUTE', 'native').strip().lower()

# The generation task's own audio parameters. Neither is in `_NEVER_STRIP`, so
# a model that does not know them has them dropped by the refused-parameter
# retry and still returns the clip -- silent, which is the right failure.
RW_VIDEO_AUDIO_FLAG = os.getenv('RW_VIDEO_AUDIO_FLAG', 'generateAudio')
RW_VIDEO_AUDIO_FIELD = os.getenv('RW_VIDEO_AUDIO_FIELD', 'audioPrompt')

# The follow-on route: a task type and a model id, both guesses until
# `search_models('audio')` is run against a live key.
RW_AUDIO_TASK = os.getenv('RW_AUDIO_TASK', 'videoToAudio')
RW_MODEL_AUDIO = os.getenv('RW_MODEL_AUDIO', 'runware:400@1')

# Lip-sync: a follow-on task that makes her mouth say the line. Unverified
# like the two above -- check with `search_models('lipsync')` on a live key.
RW_LIPSYNC_TASK = os.getenv('RW_LIPSYNC_TASK', 'videoInference')
RW_MODEL_LIPSYNC = os.getenv('RW_MODEL_LIPSYNC', 'sync:lipsync-2@1')

HAS_FFMPEG = bool(shutil.which('ffmpeg'))


def last_frame(video):
    """The clip's final frame as a JPEG, the next part's first frame; b'' when
    it cannot be read."""
    if not HAS_FFMPEG:
        return b''
    with tempfile.TemporaryDirectory(dir='/tmp') as d:
        src, out = os.path.join(d, 'in.mp4'), os.path.join(d, 'last.jpg')
        with open(src, 'wb') as f:
            f.write(video)
        try:
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-sseof', '-0.5', '-i', src,
                            '-update', '1', '-q:v', '2', out],
                           check=True, timeout=60, capture_output=True)
            with open(out, 'rb') as f:
                return f.read()
        except Exception as e:
            logging.getLogger(__name__).warning('last frame failed: %s', str(e)[:200])
            return b''


def join_clips(videos):
    """Parts back to back as one clip. Each later part starts on the frame
    the one before ended on, so that frame is dropped once to avoid a stutter.
    b'' when the join fails."""
    if not HAS_FFMPEG or not videos:
        return b''
    with tempfile.TemporaryDirectory(dir='/tmp') as d:
        cmd = ['ffmpeg', '-y', '-loglevel', 'error']
        for i, v in enumerate(videos):
            p = os.path.join(d, f'{i}.mp4')
            with open(p, 'wb') as f:
                f.write(v)
            cmd += ['-i', p]
        n = len(videos)
        chains = [f'[{i}:v]' + ('trim=start_frame=1,setpts=PTS-STARTPTS,' if i else '')
                  + f'scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[v{i}]'
                  for i in range(n)]
        # Parts with sound (H3) keep it, trimmed by the same dropped frame;
        # silent parts have no audio stream, so that graph fails and the
        # video-only one runs.
        sound = [f'[{i}:a]' + (f'atrim=start={1 / 24:.4f},asetpts=PTS-STARTPTS,' if i else '')
                 + f'aresample=48000[a{i}]' for i in range(n)]
        tries = (
            (';'.join(chains + sound) + ';' + ''.join(f'[v{i}][a{i}]' for i in range(n))
             + f'concat=n={n}:v=1:a=1[out][aout]', ['-map', '[out]', '-map', '[aout]', '-c:a', 'aac']),
            (';'.join(chains) + ';' + ''.join(f'[v{i}]' for i in range(n))
             + f'concat=n={n}:v=1:a=0[out]', ['-map', '[out]']))
        out = os.path.join(d, 'out.mp4')
        for graph, maps in tries:
            try:
                subprocess.run(cmd + ['-filter_complex', graph] + maps + [
                                   '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                                   '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out],
                               check=True, timeout=600, capture_output=True)
                with open(out, 'rb') as f:
                    return f.read()
            except Exception as e:
                last = e
        logging.getLogger(__name__).warning('join failed: %s', str(last)[:300])
        return b''


def _ffmpeg(video, args, audio=None):
    """Run ffmpeg over a clip held in memory; the clip unchanged on failure,
    because it is already paid for and already good."""
    if not HAS_FFMPEG:
        return video
    with tempfile.TemporaryDirectory(dir='/tmp') as d:
        src, out = os.path.join(d, 'in.mp4'), os.path.join(d, 'out.mp4')
        with open(src, 'wb') as f:
            f.write(video)
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', src]
        if audio is not None:
            snd = os.path.join(d, 'track')
            with open(snd, 'wb') as f:
                f.write(audio)
            cmd += ['-i', snd]
        try:
            subprocess.run(cmd + args + [out], check=True, timeout=120,
                           capture_output=True)
            with open(out, 'rb') as f:
                return f.read()
        except Exception as e:
            logging.getLogger(__name__).warning('ffmpeg failed: %s', str(e)[:200])
            return video


def strip_audio(video):
    return _ffmpeg(video, ['-an', '-c:v', 'copy', '-movflags', '+faststart'])


def copy_audio(video, source):
    """The source clip's own track on a clip regenerated from it."""
    return _ffmpeg(video, ['-map', '0:v:0', '-map', '1:a:0?', '-c:v', 'copy',
                           '-c:a', 'aac', '-shortest', '-movflags', '+faststart'],
                   audio=source)


def mux_audio(video, audio):
    """Her uploaded track in place of the clip's own, cut to the clip."""
    return _ffmpeg(video, ['-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
                           '-c:a', 'aac', '-shortest', '-movflags', '+faststart'],
                   audio=audio)


def audio_prompt(audio, voice=''):
    """What to ask for, from the preset the creator picked.

    `speech` is the only one that carries her: the line is the creator's and
    the voice is the persona's own builder field, so a spoken clip does not
    invent a way for her to sound.
    """
    audio = audio or {}
    mode = (audio.get('mode') or '').strip().lower()
    if mode not in AUDIO_MODES:
        return ''
    text = (audio.get('prompt') or '').strip()
    if mode == 'custom':
        return text
    if mode == 'music':
        return AUDIO_PROMPTS['music'] + (f'. Mood: {text}' if text else '')
    if mode == 'lipsync':
        mode = 'speech'
    if mode == 'speech':
        if not text:
            return ''
        voice = (voice or audio.get('voice') or '').strip()
        said = f'She says, clearly and in sync: "{text}"'
        return f'{said} Her voice: {voice}.' if voice else said
    return AUDIO_PROMPTS.get(mode, '')


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

    def poll(self, job_id, expect=1):
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


# A provider whose own worker crashes answers with its stack rather than a
# sentence. This one is worth translating: the module it fails to import is the
# model's content check, so the crash is how a refusal reaches us.
_PROVIDER_CRASH_HINTS = (
    ("'safety' module is not available",
     'the model refused this clip at its own safety check'),
)


def _error_text(body):
    # 800, not 300: a provider that refuses a parameter answers with the list of
    # the ones it does take, and that list is the only way to learn the right
    # field name. Cutting it mid-word threw away the answer.
    if not isinstance(body, dict):
        return ''
    # Some answers nest the whole thing one level down, and an error there is
    # still an error: reading only the top level reported nothing at all.
    if 'errors' not in body and isinstance(body.get('response'), dict):
        body = body['response']
    errs = body.get('errors') or body.get('error') or body.get('message')
    if isinstance(errs, list) and errs:
        errs = errs[0]
    if isinstance(errs, dict):
        text = (errs.get('message') or errs.get('errorMessage')
                or errs.get('error') or '')
        if not text:
            detail = errs.get('additionalDetails') or {}
            inner = (detail.get('responseContent') if isinstance(detail, dict)
                     else '') or ''
            text = ' '.join(filter(None, (errs.get('errorCode') or '', inner)))
        errs = text or errs
    text = str(errs)[:800] if errs else ''
    for needle, plain in _PROVIDER_CRASH_HINTS:
        if needle in text:
            return f'{plain} ({text[:200]})'
    return text


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
_BAD_MODEL = "Invalid value for 'model'"


# What to search the provider's catalogue for when it refuses one of our ids.
MODEL_SEARCH_NAMES = {'wan-2-2-animate': ('wan animate', 'animate', 'wan2.2'),
                      'p-video-animate': ('p-video', 'animate')}


def _bad_model_error(tasks, err):
    """Runware's refusal of a model id names neither the id nor the model, and
    most ids here were read off documentation, so the refusal also asks the
    catalogue what the model is really called: the failed card then carries
    the id to set, instead of a second guess."""
    names = {**{v: k for k, v in RUNWARE_MODELS.items()},
             **{v: k for k, v in RUNWARE_VIDEO_MODELS.items()}}
    sent = [t['model'] for t in tasks if isinstance(t.get('model'), str)]
    said = ', '.join(f"{a!r} ({names[a]})" if a in names else repr(a) for a in sent) or 'none'
    found = ''
    keys = [names[a] for a in sent if a in names]
    hits = []
    if keys:
        for query in MODEL_SEARCH_NAMES.get(keys[0]) or (keys[0].replace('-', ' '),):
            try:
                hits = search_models(query, limit=5)
            except Exception:
                hits = []
            if hits:
                break
        if hits:
            found = (' Runware lists: ' + '; '.join(
                f"{h['air']} ({h['name']})" if h['name'] else h['air'] for h in hits) + '.')
    return GenerationError(
        f'Runware does not know the model id {said}.{found} Set the right one in '
        f'that model\'s RW_MODEL_* variable (or look it up at '
        f'/api/generate/models?q=<model name>). The provider said: {err}', fatal=True)

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


def _strip_param(tasks, key):
    """Drop one refused parameter from every task, wherever it sits.

    A nested one is refused by name exactly as a top-level one is, and reading
    only the top level turned a model that simply does not know an optional
    key -- an audio field, a frame position -- into a hard failure. Nothing
    that carries the clip or her face can be reached this way: `_NEVER_STRIP`
    is checked before this is called.
    """
    gone = False
    for task in tasks:
        if task.pop(key, None) is not None:
            gone = True
        for provider in (task.get('providerSettings') or {}).values():
            if isinstance(provider, dict) and provider.pop(key, None) is not None:
                gone = True
        inputs = task.get('inputs')
        if not isinstance(inputs, dict):
            continue
        if inputs.pop(key, None) is not None:
            gone = True
        for value in inputs.values():
            for row in (value if isinstance(value, list) else ()):
                if isinstance(row, dict) and row.pop(key, None) is not None:
                    gone = True
    return gone


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
                    return body.get('data') or (body.get('response') or {}).get('data') or []
                failure = GenerationError(err)
            if _BAD_MODEL in err:
                raise _bad_model_error(tasks, err)
            hit = _UNSUPPORTED_PARAM.search(err)
            key = hit.group(1) if hit else None
            if key in _NEVER_STRIP:
                raise GenerationError(
                    f'This model refused {key!r}, which carries the clip or her '
                    f'face — running without it would generate someone else. '
                    f'The provider said: {err}', fatal=True)
            if not key:
                break
            if not _strip_param(tasks, key):
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
        if spec.get('explicit') and model_key not in EXPLICIT_MODELS:
            model_key = EXPLICIT_MODEL
        model = RUNWARE_MODELS.get(model_key) or RUNWARE_MODELS['seedream-4-5']
        width, height = dimensions(model_key, spec.get('resolution'), spec.get('aspect'))

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

        if spec.get('async_delivery'):
            # Answered with an ack instead of the images, so the task id is on
            # the job within seconds. Held open for the whole batch, a submit
            # that died mid-wait -- a lambda cut off, a restarted worker --
            # left a job with no id, written off as never started while the
            # provider went on to make and bill every image.
            task['deliveryMethod'] = 'async'
            try:
                # The full window still: a model that refuses the field has it
                # stripped and answers the old, blocking way.
                data = self._send([task], timeout=submit_window(spec))
            except ProviderUnreachable as e:
                logger.warning('runware image submit unanswered, adopting %s: %s',
                               task['taskUUID'], e)
                return task['taskUUID'], Result('running')
        else:
            data = self._send([task], timeout=submit_window(spec))
        urls = [d.get('imageURL') for d in data if d.get('imageURL')]
        cost = sum(float(d.get('cost') or 0) for d in data)
        if not urls:
            return task['taskUUID'], Result('running')
        return task['taskUUID'], Result('done', urls, cost=cost or None)

    def _refs(self, spec):
        """Her reference photographs, inlined frame first when one was sent."""
        refs = list(spec.get('reference_urls') or [])
        if spec.get('reference_b64'):
            refs = [_data_uri(spec['reference_b64'],
                              spec.get('reference_mime'))] + refs
        return refs

    def submit_video(self, spec):
        task_uuid = str(uuid.uuid4())
        job = spec.get('job') or ('swap' if spec.get('kind') == 'swap'
                                  else 'animate')
        model_key = spec.get('model') or DEFAULT_VIDEO_MODEL
        if job == 'swap' and model_key not in SWAP_MODELS:
            model_key = DEFAULT_SWAP_MODEL
        width, height = video_size(model_key, spec.get('source_width'),
                                   spec.get('source_height'),
                                   spec.get('resolution'),
                                   spec.get('aspect'))
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
        if shape == 'motion':
            # The clip sets the length, size and motion; the prompt only steers
            # the scene. A duration, a size or a negative prompt is refused.
            if spec.get('fps'):
                task['fps'] = int(spec['fps'])
            if model_key in RUNG_SCALED_MODELS:
                task['width'], task['height'] = width, height
            # Wan 2.2 Animate shares this shape but runs on Runware's own GPUs,
            # and a Kling provider block on it is refused as an invalid model.
            if model_key in KLING_MOTION_MODELS:
                kling = {'characterOrientation': spec.get('orientation') or 'video',
                         'keepOriginalSound': spec.get('keep_sound', True) is not False}
                # Kling takes no background parameter; a new place rides in the
                # prompt, which build_swap_prompt writes from `place`.
                task['providerSettings'] = {'klingai': kling}
        elif shape == 'replace':
            # It takes a rung by name and no length at all: the output runs as
            # long as the clip it was given. Sending either of the others is
            # refused outright, which is the model saying what it is.
            wanted = rung_for(model_key, spec.get('resolution'))
            spelling = dict(MODEL_RESOLUTION_VALUES.get(model_key) or ())
            if spelling.get(wanted):
                task['resolution'] = spelling[wanted]
            if spec.get('fps'):
                task['fps'] = int(spec['fps'])
        else:
            if model_key not in NO_NEGATIVE_MODELS:
                task[_RW['negative']] = negative_for(spec)
            task['width'] = width
            task['height'] = height
            task['duration'] = video_seconds(model_key, spec.get('seconds') or 5)
        # Only the older flat payload takes it; every newer model refuses it by
        # name. The retry in _send would strip it anyway, at the price of a
        # wasted round trip on every single clip.
        if shape == 'flat':
            task['checkNSFW'] = False

        cap = MODEL_REF_CAP.get(model_key, MAX_VIDEO_REFERENCES)

        if job == 'swap' or (shape in ('replace', 'motion') and spec.get('source_url')):
            source = spec.get('source_url')
            if not source:
                raise GenerationError('a swap needs the clip it is swapping into')
            fields = _video_fields(model_key)
            refs = self._refs(spec)
            if not refs:
                raise GenerationError(
                    'a swap needs at least one approved photo of her to swap in')
            if fields.get('shape') in ('inputs', 'replace', 'motion'):
                # A model that edits the clip takes it as a single `video`; one
                # that takes guidance from it takes a list of reference videos.
                src_key = fields.get('in_source') or 'referenceVideos'
                task['inputs'] = {
                    src_key: source if src_key == 'video' else [source],
                    (fields.get('in_refs') or 'referenceImages'):
                        list(refs)[:cap]}
                if model_key == 'wan-2-2-animate':
                    task['advancedFeatures'] = {'wanAnimate': {
                        'mode': 'replace' if job == 'swap' else 'animate'}}
            else:
                task[fields['source']] = source
                task[fields['refs']] = list(refs)[:MAX_REFERENCES]
        elif (job == 'reel' and shape == 'inputs' and spec.get('reference_urls')
              and not spec.get('source_url')):
            # A reel with her character: her views lead and the creator's
            # still, when there is one, goes last -- the prompt names it by
            # position as the person to replace.
            scenes = list(spec.get('scene_urls') or [])
            keep = max(1, cap - len(scenes))
            task['inputs'] = {'referenceImages':
                              (list(spec['reference_urls'])[:keep] + scenes)[:cap]}
        elif job == 'multiref':
            # Her identity references first and the scene references after,
            # because the prompt names them by position -- the payload has no
            # other way to say which photograph is which.
            refs = self._refs(spec)
            scenes = list(spec.get('scene_urls') or [])
            if not refs:
                raise GenerationError(
                    'a multi-reference clip needs at least one photo of her')
            if not scenes:
                raise GenerationError(
                    'a multi-reference clip needs a scene or outfit reference')
            # Her own photographs keep their places when the cap bites: losing
            # a scene reference makes a plainer clip, losing an identity one
            # makes a stranger.
            keep = max(1, cap - len(scenes))
            merged = list(refs)[:keep] + scenes
            task['inputs'] = {'referenceImages': merged[:cap]}
        elif job == 'extend':
            frame = spec.get('reference_b64')
            if not frame:
                raise GenerationError(
                    'an extension needs the last frame of the clip it continues')
            frames = [{'inputImage': _data_uri(frame, spec.get('reference_mime')),
                       'frame': 'first'}]
            if spec.get('closing_b64'):
                # A loop has to arrive somewhere exact, so the source's own
                # opening frame is pinned as the closing one.
                frames.append({
                    'inputImage': _data_uri(spec['closing_b64'],
                                            spec.get('closing_mime')),
                    'frame': 'last'})
            if shape in ('inputs', 'replace'):
                task['inputs'] = {'frameImages': frames}
            else:
                task[_RW['frame_images']] = frames
        elif spec.get('source_url') and shape == 'inputs':
            # A clip that carries a source clip is motion transfer: her photo
            # is the subject, the clip is only where the movement comes from.
            refs = self._refs(spec)
            if not refs:
                raise GenerationError(
                    'a clip driven by a video still needs a photo of her')
            task['inputs'] = {
                'referenceVideos': [spec['source_url']],
                'referenceImages': list(refs)[:cap]}
        else:
            frame = spec.get('reference_b64')
            if not frame:
                if job != 'reel':
                    # Enforced here as well as in the UI: identity comes from
                    # the first frame, so a clip without one is not this
                    # feature. A reel is the exception -- it claims to be
                    # nobody, so it may run from its prompt alone.
                    raise GenerationError(
                        'a video needs an approved still as its first frame')
            else:
                first = {'inputImage': _data_uri(frame, spec.get('reference_mime'))}
                # Mutually exclusive with the reference inputs above, which is
                # why this is the whole of `inputs` rather than another key.
                if shape in ('inputs', 'replace'):
                    task['inputs'] = {'frameImages': [first]}
                else:
                    task[_RW['frame_images']] = [first]

        # Sound on the generation itself, where the model emits it in one pass.
        # A model that does not know these drops them through the refused-
        # parameter retry and still returns the clip, silent.
        want_audio = audio_prompt(spec.get('audio'), spec.get('voice'))
        if want_audio and AUDIO_ROUTE == 'native' and shape != 'replace':
            task[RW_VIDEO_AUDIO_FLAG] = True
            task[RW_VIDEO_AUDIO_FIELD] = want_audio[:600]

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
            logger.warning('runware video refused job=%s model=%s fields=%s',
                           job, model_key, sorted(task))
            raise
        urls = [d.get('videoURL') for d in data if d.get('videoURL')]
        if urls:
            return task_uuid, Result('done', urls)
        return task_uuid, Result('running')

    def submit_audio(self, video_url, spec):
        """The follow-on route: a second task over a finished clip that hands
        back a muxed file. Used only when `RW_AUDIO_ROUTE` is set to `task` —
        the task type and the model id are both guesses until they are checked
        against a live catalogue, which is why neither is a literal here."""
        prompt = audio_prompt(spec.get('audio'), spec.get('voice'))
        if not prompt:
            raise GenerationError('no audio was asked for', fatal=True)
        task_uuid = str(uuid.uuid4())
        lipsync = (spec.get('audio') or {}).get('mode') == 'lipsync'
        task = {'taskType': RW_LIPSYNC_TASK if lipsync else RW_AUDIO_TASK,
                'taskUUID': task_uuid,
                'model': RW_MODEL_LIPSYNC if lipsync else RW_MODEL_AUDIO,
                _RW['prompt']: prompt[:600],
                'inputs': {'video': video_url},
                _RW['output']: 'URL',
                'includeCost': True,
                'deliveryMethod': 'async'}
        data = self._send([task], timeout=VIDEO_TIMEOUT)
        urls = [d.get('videoURL') or d.get('audioURL') for d in data
                if d.get('videoURL') or d.get('audioURL')]
        if urls:
            return task_uuid, Result('done', urls)
        return task_uuid, Result('running')

    def poll(self, job_id, expect=1):
        data = self._send([{'taskType': _RW['get'], 'taskUUID': job_id}])
        if not data:
            return Result('running')
        urls, status = [], 'running'
        for row in data:
            url = row.get('videoURL') or row.get('imageURL')
            if url:
                urls.append(url)
            state = (row.get('status') or '').lower()
            # A row carrying an error is finished whether or not it also
            # carries a status: waiting on one that has already failed spends
            # the job's whole timeout to report that nothing ever happened.
            if state in ('error', 'failed') or row.get('errors') or row.get('errorCode'):
                return Result('failed', error=_error_text(row) or 'generation failed')
            if state in ('success', 'done', 'completed'):
                status = 'done'
        # A batch comes back an image at a time; the first is not the job.
        if len(urls) >= expect:
            return Result('done', urls)
        return Result('running', urls)


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
        width, height = dimensions(spec.get('model'), spec.get('resolution'),
                                   spec.get('aspect'))
        payload = {
            'model_id': MODELSLAB_MODELS.get(spec.get('model'),
                                             MODELSLAB_MODELS['sdxl']),
            'prompt': spec.get('prompt') or '',
            'negative_prompt': negative_for(spec),
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
        if spec.get('job') == 'swap' or spec.get('kind') == 'swap':
            return self._submit_face_swap(spec)
        width, height = video_px(spec.get('aspect'), spec.get('resolution'))
        if not spec.get('reference_url'):
            raise GenerationError('a video needs an approved still as its first frame')
        body = self._send('video/img2video', {
            'model_id': MODELSLAB_VIDEO_MODEL,
            'init_image': spec['reference_url'],
            'prompt': spec.get('prompt') or build_video_prompt(),
            'negative_prompt': negative_for(spec),
            'width': width, 'height': height,
            'num_frames': int(spec.get('seconds') or 5) * 16,
            'safety_checker': 'no',
        })
        return str(body.get('id') or ''), self._read(body)

    def _submit_face_swap(self, spec):
        source = spec.get('source_url')
        if not source:
            raise GenerationError('a swap needs the clip it is swapping into')
        refs = spec.get('reference_urls') or []
        if spec.get('reference_b64'):
            refs = [_data_uri(spec['reference_b64'],
                              spec.get('reference_mime'))] + list(refs)
        if not refs:
            raise GenerationError(
                'a swap needs at least one approved photo of her to swap in')
        body = self._send(MODELSLAB_FACESWAP_PATH, {
            'model_id': MODELSLAB_FACESWAP_MODEL,
            MODELSLAB_FACESWAP_VIDEO_FIELD: source,
            # Only the first face: a swap conditions on one identity, and more
            # references here would only give it something to average towards.
            MODELSLAB_FACESWAP_FACE_FIELD: refs[0],
            'watermark': 'no',
        })
        return str(body.get('id') or ''), self._read(body)

    def poll(self, job_id, expect=1):
        body = _post(f'{MODELSLAB_ENDPOINT}/images/fetch/{job_id}',
                     {'key': self.key}, {'Content-Type': 'application/json'})
        return self._read(body)


# ── RunPod ────────────────────────────────────────────────────────────────────

def _first_url(out):
    """The video URL in a RunPod output, whatever shape the worker returns."""
    if isinstance(out, str):
        return out if out.startswith('http') else ''
    items = out.values() if isinstance(out, dict) else out if isinstance(out, list) else ()
    for v in items:
        url = _first_url(v)
        if url:
            return url
    return ''


class RunPodProvider(Provider):
    name = 'runpod'

    def __init__(self, key=None):
        self.key = (key or RUNPOD_API_KEY or os.getenv('RUNPOD_API_KEY') or '').strip()
        if not self.key:
            raise GenerationError('RUNPOD_API_KEY is not set')

    def _headers(self):
        return {'Authorization': f'Bearer {self.key}',
                'Content-Type': 'application/json'}

    @staticmethod
    def _read(body):
        status = (body.get('status') or '').upper()
        if status == 'COMPLETED':
            out = body.get('output')
            url = _first_url(out)
            if not url and isinstance(out, dict) and isinstance(out.get('image'), str):
                url = 'data:image/png;base64,' + out['image']
            if not url and isinstance(out, dict) and out.get('trained'):
                return Result('done', ['trained'])
            if not url and isinstance(out, dict) and out.get('error'):
                return Result('failed', error=str(out['error'])[:500])
            if not url and isinstance(out, dict) and isinstance(out.get('video'), str):
                # generate_video hands the clip back inline as base64.
                url = 'data:video/mp4;base64,' + out['video']
            if not url and isinstance(out, dict):
                # worker-comfyui: [{'type': 'base64' | 's3_url', 'data': ...}].
                for f in out.get('images') or ():
                    if f.get('type') == 's3_url':
                        url = f.get('data')
                    elif f.get('type') == 'base64' and f.get('data'):
                        url = 'data:video/mp4;base64,' + f['data']
            if url:
                return Result('done', [url])
            return Result('failed', error='RunPod finished without a video')
        if status in ('FAILED', 'CANCELLED', 'TIMED_OUT'):
            return Result('failed', error=_error_text(body) or f'RunPod {status.lower()}')
        return Result('running')

    def payload(self, spec):
        image = spec.get('reference_url') or (
            _data_uri(spec['reference_b64'], spec.get('reference_mime'))
            if spec.get('reference_b64') else '')
        if not image:
            raise GenerationError('a video needs an approved still as its first frame')
        model = spec.get('model') or 'wan-2-2'
        if model == 'h3-gv':
            # worker-comfyui decodes every input image as base64 and fetches no
            # links, so a URL arrives as an unreadable still.png.
            if spec.get('reference_b64'):
                image = _data_uri(spec['reference_b64'], spec.get('reference_mime'))
            else:
                data, mime = fetch_result(image)
                image = _data_uri(base64.b64encode(data).decode(), mime)
            ref = (_data_uri(spec['identity_b64'], spec.get('identity_mime'))
                   if spec.get('identity_b64') else None)
            return h3_payload(spec, image, ref)
        if model == 'wan-2-2-gv':
            width, height = video_px(spec.get('aspect'), spec.get('resolution') or '720p')
            body = {
                'prompt': spec.get('prompt') or build_video_prompt(),
                'negative_prompt': negative_for(spec),
                'width': width, 'height': height,
                # 16 fps, and Wan wants 4n+1 frames.
                'length': video_seconds(model, spec.get('seconds')) * 16 + 1,
                # The stock image ignores these; infra/runpod-gv-patch.py applies them.
                'steps': 8,
                'seed': int(spec['seed']) if spec.get('seed') is not None else random.randint(0, 2**31 - 1),
                # Downloaded by the worker on first use, by the same links
                # wan-2-2-lora hands its endpoint.
                'lora_pairs': [{'high': lora_url(l['high']), 'low': lora_url(l['low']),
                                'high_weight': float(l['scale']), 'low_weight': float(l['scale'])}
                               for l in (spec.get('loras') or [])[:4] if l.get('high') or l.get('low')],
            }
            if spec.get('reference_url'):
                body['image_url'] = spec['reference_url']
            else:
                body['image_base64'] = spec['reference_b64']
            return {'input': body}
        if model == 'wan-2-6-rp':
            return {'input': {
                'prompt': spec.get('prompt') or build_video_prompt(),
                'image': image,
                'negative_prompt': negative_for(spec),
                # Despite the docs, this endpoint takes the rung, not pixels.
                'size': '1080p' if (spec.get('resolution') or '720p') == '1080p' else '720p',
                'duration': video_seconds(model, spec.get('seconds')),
                'shot_type': 'single',
                'seed': int(spec['seed']) if spec.get('seed') is not None else -1,
                'enable_prompt_expansion': False,
                'enable_safety_checker': not spec.get('explicit'),
            }}
        width, height = video_px(spec.get('aspect'), '720p')
        body = {
            'prompt': spec.get('prompt') or build_video_prompt(),
            'image': image,
            'negative_prompt': negative_for(spec),
            'size': f'{width}*{height}',
            # The live endpoint requires these, though its docs call them optional.
            'num_inference_steps': 30,
            'guidance': 5,
            'flow_shift': 5,
            'duration': video_seconds(model if model == 'wan-2-2-lora' else 'wan-2-2',
                                      spec.get('seconds')),
            'seed': int(spec['seed']) if spec.get('seed') is not None else -1,
            'enable_prompt_optimization': False,
            'enable_safety_checker': not spec.get('explicit'),
        }
        if model == 'wan-2-2-lora':
            loras = spec.get('loras') or []
            body['high_noise_loras'] = [{'path': lora_url(l['high']), 'scale': float(l['scale'])}
                                        for l in loras if l.get('high')]
            body['low_noise_loras'] = [{'path': lora_url(l['low']), 'scale': float(l['scale'])}
                                       for l in loras if l.get('low')]
        return {'input': body}

    def submit_image(self, spec):
        if spec.get('model') != CHAR_LORA_IMAGE_MODEL or CHAR_LORA_IMAGE_MODEL not in RUNPOD_ENDPOINTS:
            raise GenerationError('RunPod only runs Wan 2.2 video here', fatal=True)
        w, h = IMAGE_LORA_PX.get(spec.get('aspect') or '2:3', IMAGE_LORA_PX['2:3'])
        args = {'prompt': spec.get('prompt') or '', 'negative': spec.get('negative') or '',
                'width': w, 'height': h, 'seed': spec.get('seed'),
                'loras': [{'high': lora_url(l['high']), 'low': lora_url(l['low']),
                           'scale': float(l['scale'])}
                          for l in (spec.get('loras') or [])[:3] if l.get('high') and l.get('low')]}
        return self._run(CHAR_LORA_IMAGE_MODEL, {'input': {'image': args}})

    def submit_lora_training(self, images, trigger, high_put, low_put, steps, h3_put=None):
        """Start a character LoRA training run: `images` is [{url, caption}],
        the PUT links are where the worker uploads the finished files (Wan
        high/low, then the H3 one)."""
        if CHAR_LORA_IMAGE_MODEL not in RUNPOD_ENDPOINTS:
            raise GenerationError('RUNPOD_LORA_ENDPOINT is not set', fatal=True)
        return self._run(CHAR_LORA_IMAGE_MODEL, {'input': {'train': {
            'images': images, 'trigger': trigger, 'steps': steps,
            'high_put': high_put, 'low_put': low_put, 'h3_put': h3_put}}})

    def _run(self, model, payload):
        body = _post(f'{RUNPOD_ENDPOINTS[model]}/run', payload, self._headers(), timeout=VIDEO_TIMEOUT)
        job = str(body.get('id') or '')
        return (f'{model}|{job}' if job else ''), self._read(body)

    def submit_video(self, spec):
        model = spec.get('model') if spec.get('model') in RUNPOD_ENDPOINTS else 'wan-2-2'
        body = _post(f'{RUNPOD_ENDPOINTS[model]}/run', self.payload(spec), self._headers(),
                     timeout=VIDEO_TIMEOUT)
        job = str(body.get('id') or '')
        return (f'{model}|{job}' if job else ''), self._read(body)

    @staticmethod
    def _endpoint(job_id):
        # Jobs submitted before Wan 2.6 carry a bare id, and were all Wan 2.2.
        model, _, job = job_id.rpartition('|')
        return RUNPOD_ENDPOINTS.get(model or 'wan-2-2', RUNPOD_ENDPOINTS['wan-2-2']), job

    def poll(self, job_id, expect=1):
        import requests
        try:
            endpoint, job = self._endpoint(job_id)
            resp = requests.get(f'{endpoint}/status/{job}',
                                headers=self._headers(), timeout=TIMEOUT)
        except Exception as e:
            raise ProviderUnreachable(f'RunPod unreachable: {e}', fatal=False)
        if resp.status_code == 404:
            return Result('failed', error='RunPod no longer knows this job')
        if resp.status_code >= 400:
            raise GenerationError(f'RunPod status {resp.status_code}',
                                  fatal=resp.status_code in (401, 403))
        return self._read(resp.json())


class VastProvider(RunPodProvider):
    """Our RunPod workers on Vast Serverless. A job id is a JSON handle on
    the one worker it runs on: its URL, the session pinning it, the job."""
    name = 'vast'

    def __init__(self, key=None):
        self.key = (key or VAST_API_KEY).strip()
        if not self.key:
            raise GenerationError('VAST_API_KEY is not set')

    def _post(self, url, body, timeout=TIMEOUT, key=None):
        # The router and its workers take the endpoint's own key, not the account's.
        import requests
        try:
            resp = requests.post(url, json=body, timeout=timeout, headers={
                'Authorization': f'Bearer {key or self.key}', 'Content-Type': 'application/json'})
        except Exception as e:
            raise ProviderUnreachable(f'Vast unreachable: {e}', fatal=False)
        if resp.status_code == 410:
            return {'status': 'FAILED', 'error': 'the Vast worker running this job is gone'}
        if resp.status_code >= 400:
            raise GenerationError(f'Vast {resp.status_code}: {resp.text[:300]}',
                                  fatal=resp.status_code in (401, 403))
        return resp.json()

    def _endpoint_key(self, name):
        # Lets a box without the account key (infra/vast/deploy.py test) reach an endpoint.
        keys = json.loads(os.getenv('VAST_ENDPOINT_KEYS') or '{}')
        if keys.get(name):
            return keys[name]
        import requests
        resp = requests.get('https://console.vast.ai/api/v0/endptjobs/', params={'client_id': 'me'},
                            headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        for e in resp.json().get('results') or ():
            if e.get('endpoint_name') == name:
                return e.get('api_key') or self.key
        raise GenerationError(f'no Vast endpoint named {name}', fatal=True)

    def _drop_unavailable(self, name, key):
        """A stopped worker whose host was rented away reports unavailable,
        and Vast keeps counting it, so it never hires a replacement."""
        import requests
        try:
            ep = next(e for e in requests.get(
                'https://console.vast.ai/api/v0/endptjobs/', headers=self._headers(),
                timeout=TIMEOUT).json().get('results') or () if e.get('endpoint_name') == name)
            workers = requests.post('https://run.vast.ai/get_endpoint_workers/',
                                    json={'id': ep['id'], 'api_key': key}, timeout=TIMEOUT).json()
            for w in workers if isinstance(workers, list) else ():
                if w.get('status') == 'unavail':
                    logger.warning('vast %s: deleting unavailable worker %s', name, w['id'])
                    requests.delete(f'https://console.vast.ai/api/v0/instances/{w["id"]}/',
                                    headers=self._headers(), timeout=TIMEOUT)
        except Exception as e:
            logger.warning('vast %s: worker check failed: %s', name, e)

    def _route(self, name):
        key = self._endpoint_key(name)
        body, idx, deadline, first = {}, None, time.time() + VAST_ROUTE_WAIT, True
        checked = time.time()
        while not body.get('url'):
            if time.time() > deadline:
                raise GenerationError(f'no Vast worker for {name} came up', fatal=True)
            if not first:
                time.sleep(5)
                if time.time() - checked > 60:
                    checked = time.time()
                    self._drop_unavailable(name, key)
            first = False
            try:
                body = self._post('https://run.vast.ai/route/', {
                    'endpoint': name, 'api_key': key, 'cost': 100, 'request_idx': idx,
                    'replay_timeout': 60}, key=key)
            except ProviderUnreachable:
                # One dropped connection must not end a wait that can run 20 minutes.
                continue
            if body.get('error_msg'):
                raise GenerationError(f'Vast refused the route: {body["error_msg"]}', fatal=True)
            idx = body.get('request_idx', idx)
        return dict(body, _key=key)

    def _run(self, model, payload):
        auth = self._route(VAST_ENDPOINTS[model])
        key = auth.pop('_key')
        url = auth['url'].rstrip('/')
        sess = self._post(f'{url}/session/create', {'auth_data': auth, 'payload': {
            # Every poll extends it; an orphaned job frees the worker this long after.
            'lifetime': 900}}, key=key)
        body = self._post(f'{url}/run', {'auth_data': auth, 'session_id': sess['session_id'],
                                         'payload': payload}, timeout=VIDEO_TIMEOUT, key=key)
        job = json.dumps({'url': url, 'auth': auth, 'session': sess['session_id'],
                          'job': body.get('id'), 'key': key})
        return job, self._read(body)

    def submit_video(self, spec):
        return self._run(spec.get('model'), self.payload(spec))


    def poll(self, job_id, expect=1):
        h = json.loads(job_id)
        res = self._read(self._post(f'{h["url"]}/status', {
            'auth_data': h['auth'], 'session_id': h['session'], 'payload': {'id': h['job']}},
            key=h.get('key')))
        if res.status != 'running':
            try:
                self._post(f'{h["url"]}/session/end', {'session_id': h['session'],
                                                       'session_auth': h['auth']}, key=h.get('key'))
            except Exception:
                pass
        return res


# H3 at 24 fps wants 17n+5 frames; turbo LoRA at 8 steps, as Comfy's template.
H3_STEPS = 8


# H3 prompts are fixed locks around one written action, so nothing but the
# action varies from clip to clip and her identity never rides on wording.
# Never names tattoos or piercings: H3 has no negative input, so any word in
# the prompt is something to draw, and "no new tattoos" adds tattoos.
H3_LOCKS = 'her exact face, hair, body, skin, clothes and background'
H3_START = ('Video starts exactly on <Picture 2> (frame0): same position, pose, framing, '
            'and background, and continues from there.')
H3_CAMERA = ('Camera locked to reference: same framing, same angle, same distance, no zoom, '
             'no pan, no tilt, no dolly, no orbit, no shake.')
H3_CLOSE = ('She stays exactly as she looks in the reference image, with nothing added to '
            'or removed from her skin or body. Camera stays fixed.')


# Words that never go into the positive, even as "avoid": naming them there
# invites them.
_H3_NO_AVOID = {'child', 'teen', 'underage'}
_H3_NO_AVOID_WORDS = ('tattoo', 'piercing')


def h3_avoid(negative=None):
    """The negative prompt as a sentence: H3 has no negative input, so what
    it must not do is said in the prompt itself."""
    terms = [t.strip() for t in (negative or merge_negative('', video=True)).split(',')]
    terms = [t for t in terms if t and t.lower() not in _H3_NO_AVOID
             and not any(w in t.lower() for w in _H3_NO_AVOID_WORDS)]
    return ('Avoid: ' + ', '.join(terms) + '.') if terms else ''


def h3_prompt(action, explicit=False, chained=False, triggers=(), negative=None):
    who = 'The woman in <Picture 1> (ref)' if chained else 'The woman in the reference image'
    head = 'Photorealistic, explicit' if explicit else 'Photorealistic'
    parts = [f'{head}. {who} keeps {H3_LOCKS} for the entire clip.']
    if chained:
        parts.append(H3_START)
    parts.append(H3_CAMERA)
    if triggers:
        parts.append(', '.join(triggers) + '.')
    parts += [(action or '').strip(), H3_CLOSE, h3_avoid(negative)]
    return ' '.join(p for p in parts if p)


def h3_payload(spec, image, ref=None):
    """An API-format copy of Comfy's video_minimax_h3_i2v template, sent whole
    so the worker is stock worker-comfyui plus a LoRA fetcher. With `ref` (her
    original photo) the clip still starts on `image`, pinned at frame 0, and
    the photo is <Picture 1>, holding her identity across a chain."""
    # Height follows the rounded width, so the clip keeps the asked-for shape.
    w, h = video_px(spec.get('aspect'), spec.get('resolution') or '720p')
    width = max(32, round(w / 32) * 32)
    height = max(32, round(width * h / w / 32) * 32)
    frames = max(5, video_seconds('h3-gv', spec.get('seconds')) * 24)
    frames += (5 - frames % 17) % 17
    seed = int(spec['seed']) if spec.get('seed') is not None else random.randint(0, 2**31 - 1)
    model = ['unet', 0]
    wf = {
        'unet': {'class_type': 'UNETLoader', 'inputs': {
            'unet_name': 'minimax_h3_fl2va_pruned_int8_convrot.safetensors', 'weight_dtype': 'default'}},
        'turbo': {'class_type': 'LoraLoaderModelOnly', 'inputs': {
            'model': model, 'lora_name': 'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors',
            'strength_model': 1.0}},
    }
    model = ['turbo', 0]
    for i, l in enumerate((spec.get('loras') or [])[:4]):
        url = l.get('high') or l.get('low')
        if url:
            wf[f'lora{i}'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {
                'model': model, 'lora_name': lora_url(url), 'strength_model': float(l['scale'])}}
            model = [f'lora{i}', 0]
    wf.update({
        'clip': {'class_type': 'CLIPLoader', 'inputs': {
            'clip_name': 'qwen3vl_32b_minimax_h3_int8_convrot.safetensors',
            'type': 'minimax', 'device': 'default'}},
        'vae': {'class_type': 'VAELoader', 'inputs': {
            'vae_name': 'minimax_h3_video_vae_int8_convrot.safetensors'}},
        'avae': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'minimax_h3_audio_vae_fp32.safetensors'}},
        'still': {'class_type': 'LoadImage', 'inputs': {'image': 'still.png'}},
        # Cropped, not stretched: a 2:3 still squeezed into 9:16 distorts her.
        'fit': {'class_type': 'ImageScale', 'inputs': {
            'image': ['still', 0], 'upscale_method': 'lanczos',
            'width': width, 'height': height, 'crop': 'center'}},
        'cond': {'class_type': 'MiniMaxH3ImageToVideo', 'inputs': {
            'clip': ['clip', 0], 'vae': ['vae', 0], 'first_frame': ['fit', 0],
            'prompt': spec.get('prompt') or build_video_prompt(),
            'width': width, 'height': height, 'length': frames}},
        'noise': {'class_type': 'RandomNoise', 'inputs': {'noise_seed': seed}},
        'guider': {'class_type': 'BasicGuider', 'inputs': {'model': model, 'conditioning': ['cond', 0]}},
        'sampler': {'class_type': 'KSamplerSelect', 'inputs': {'sampler_name': 'res_multistep'}},
        'sigmas': {'class_type': 'BasicScheduler', 'inputs': {
            'model': model, 'scheduler': 'simple', 'steps': H3_STEPS, 'denoise': 1.0}},
        'sample': {'class_type': 'SamplerCustomAdvanced', 'inputs': {
            'noise': ['noise', 0], 'guider': ['guider', 0], 'sampler': ['sampler', 0],
            'sigmas': ['sigmas', 0], 'latent_image': ['cond', 1]}},
        'frames': {'class_type': 'VAEDecode', 'inputs': {'samples': ['sample', 0], 'vae': ['vae', 0]}},
        'audio': {'class_type': 'VAEDecodeAudio', 'inputs': {'samples': ['sample', 0], 'vae': ['avae', 0]}},
        'video': {'class_type': 'CreateVideo', 'inputs': {
            'images': ['frames', 0], 'audio': ['audio', 0], 'fps': 24}},
        'save': {'class_type': 'SaveVideo', 'inputs': {
            'video': ['video', 0], 'filename_prefix': 'h3', 'format': 'auto', 'codec': 'auto'}},
    })
    images = [{'name': 'still.png', 'image': image}]
    prompt = spec.get('prompt') or build_video_prompt()
    if ref:
        wf['ref'] = {'class_type': 'LoadImage', 'inputs': {'image': 'ref.png'}}
        wf['cond'] = {'class_type': 'MiniMaxH3ReferenceToVideo', 'inputs': {
            'clip': ['clip', 0], 'vae': ['vae', 0], 'audio_vae': ['avae', 0],
            'prompt': prompt if '<Picture 1>' in prompt else (
                f'The woman in <Picture 1> (ref) keeps {H3_LOCKS}. {H3_START} ' + prompt),
            'width': width, 'height': height, 'length': frames,
            'ref_image_size': 'match', 'ref_images.ref_image_0': ['ref', 0],
            'ref_images.ref_image_1': ['fit', 0]}}
        wf['guide'] = {'class_type': 'MiniMaxH3AddGuide', 'inputs': {
            'positive': ['cond', 0], 'latent': ['cond', 1], 'vae': ['vae', 0],
            'image': ['fit', 0], 'frame_idx': 0}}
        wf['guider']['inputs']['conditioning'] = ['guide', 0]
        images.append({'name': 'ref.png', 'image': ref})
    return {'input': {'workflow': wf, 'images': images}}


def lora_url(url):
    if CIVITAI_TOKEN and 'civitai.com' in url and 'token=' not in url:
        return url + ('&' if '?' in url else '?') + 'token=' + CIVITAI_TOKEN
    return url


def _qwen_chat_text(messages):
    """Messages in Qwen's chat format, for the native route that takes one
    prompt string. The empty think block makes it answer without reasoning."""
    out = ''
    for m in messages:
        content = m['content'] if isinstance(m['content'], str) else ' '.join(
            c.get('text', '') for c in m['content'] if isinstance(c, dict))
        out += f"<|im_start|>{m['role']}\n{content}<|im_end|>\n"
    return out + '<|im_start|>assistant\n<think>\n\n</think>\n\n'


def _runsync_text(output):
    """The text in a native-route answer, list or dict, choices or tokens."""
    if isinstance(output, list):
        return ''.join(_runsync_text(o) for o in output)
    if isinstance(output, dict):
        for c in output.get('choices') or []:
            if isinstance(c, dict):
                return ''.join(c.get('tokens') or []) or c.get('text', '') or (
                    (c.get('message') or {}).get('content', ''))
        return output.get('text', '') or ''
    return output if isinstance(output, str) else ''


def _runpod_chat(endpoint, model, messages, max_tokens=300, why=None):
    """One chat completion on a RunPod public endpoint, or '' -- a prompt
    helper that fails must never stop the clip it was helping. `why`, when
    given, is told the reason for an empty answer, for the studio to show.

    The endpoint scales to zero and answers 500 now and then, so a timeout or
    a server error is retried once, then the native /runsync route is tried."""
    import requests
    import time as _time
    why = why if why is not None else {}
    key = (RUNPOD_API_KEY or os.getenv('RUNPOD_API_KEY') or '').strip()
    if not key:
        why['reason'] = 'RUNPOD_API_KEY is not set'
        return ''
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    failures = []
    body, text = None, None
    for attempt in (1, 2):
        try:
            resp = requests.post(f'{endpoint}/openai/v1/chat/completions', headers=headers,
                                 # Qwen3 thinks first by default, and the thinking
                                 # alone can fill max_tokens and leave no answer.
                                 json={'model': model, 'messages': messages,
                                       'max_tokens': max_tokens, 'temperature': 0.7,
                                       'chat_template_kwargs': {'enable_thinking': False}},
                                 timeout=(10, 120))
        except requests.exceptions.Timeout:
            failures.append('timed out')
            continue
        except Exception as e:
            failures.append(str(e)[:100])
            break
        if resp.status_code >= 500 or resp.status_code == 429:
            failures.append(f'error {resp.status_code}: {resp.text[:100]}')
            if attempt == 1:
                _time.sleep(2)
            continue
        try:
            body = resp.json()
            text = body['choices'][0]['message']['content'] or ''
        except Exception:
            failures.append(f'error {resp.status_code}: {resp.text[:100]}')
        break
    if text is None and 'Qwen' in model:
        try:
            resp = requests.post(f'{endpoint}/runsync', headers=headers, timeout=(10, 120),
                                 json={'input': {'prompt': _qwen_chat_text(messages),
                                                 'max_tokens': max_tokens, 'temperature': 0.7}})
            body = resp.json()
            text = _runsync_text(body.get('output')) if resp.status_code < 400 else None
            if text is None:
                failures.append(f'runsync {resp.status_code}: {resp.text[:100]}')
        except Exception as e:
            failures.append(f'runsync: {str(e)[:100]}')
    if text is None:
        logger.warning('runpod chat %s failed: %s', model, ' | '.join(failures))
        why['reason'] = ' / '.join(failures) or 'no answer'
        return ''
    body = body if isinstance(body, dict) else {}
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL).strip()
    if not text:
        finish = (body.get('choices') or [{}])[0].get('finish_reason')
        logger.warning('runpod chat %s: empty answer; finish=%s', model, finish)
        why['reason'] = f'empty answer (finish={finish})'
        return ''
    # A refusal is not a prompt; sent to the video model it would be one.
    if re.match(r"(?i)(i('m| am) sorry|i can(no|')t|i won't|sorry,|as an ai)", text):
        logger.warning('runpod chat %s refused: %.200s', model, text)
        why['reason'] = 'refused: ' + text[:120]
        return ''
    return text


def _level_words(explicit):
    return ('This is an adult (18+) explicit clip; sexual acts are allowed.'
            if explicit else 'Keep it safe for work: no nudity or sexual acts.')


def suggest_motions(description, explicit=False):
    """Short actions this still could plausibly show as a clip, from what it
    shows -- her pose, her hands, what she is holding, where she is."""
    if not (description or '').strip():
        return []
    text = _runpod_chat(RUNPOD_QWEN_ENDPOINT, 'Qwen/Qwen3-32B-AWQ', [
        {'role': 'system', 'content':
            'You suggest motions for an image-to-video model that animates an existing photo. '
            'From the description of the photo, suggest 5 actions it can continue into without '
            'changing who she is, her clothes or the place. Each is one action, at most 12 '
            'words, camera fixed. ' + _level_words(explicit) +
            ' Answer with a JSON array of strings only.'},
        {'role': 'user', 'content': f'The photo: {description.strip()[:1500]} /no_think'},
    ], max_tokens=600)
    match = re.search(r'\[.*\]', text, flags=re.DOTALL)
    try:
        ideas = json.loads(match.group(0)) if match else []
    except ValueError:
        ideas = []
    ideas = [str(i).strip()[:120] for i in ideas if isinstance(i, str) and i.strip()][:6]
    if not ideas:
        logger.warning('motion ideas: nothing usable in %r', text[:300])
    return ideas


def lora_style(spec):
    """The trigger words and example prompts of the LoRAs a job runs with:
    a LoRA follows prompts shaped like the ones it was trained on."""
    loras = spec.get('loras') or []
    triggers = [t.strip() for l in loras for t in (l.get('trigger') or '').split(',') if t.strip()]
    examples = '\n\n'.join((l.get('examples') or '').strip() for l in loras
                            if (l.get('examples') or '').strip())
    return {'triggers': triggers, 'examples': examples[:3000]} if (triggers or examples) else None


def _styled_prompt(idea, base, explicit, style, why=None):
    examples = style.get('examples') or ''
    triggers = style.get('triggers') or []
    text = _runpod_chat(RUNPOD_QWEN_ENDPOINT, 'Qwen/Qwen3-32B-AWQ', [
        {'role': 'system', 'content':
            'You write prompts for an image-to-video model running a LoRA that responds best to '
            'prompts shaped like its training prompts. Match the structure and density of the '
            'examples: a short quality opener, then the trigger word(s) '
            + (', '.join(triggers) or '(none)') + ', then the requested action written out in '
            'comma-separated detail: what she does and with which hand, the pace and how it '
            'changes, her body movement, her expression and breathing. The clip animates a real '
            'photo: keep it photorealistic, never a drawing, animation or anime. Do not copy '
            'objects, creatures or effects that belong only to the examples. End with a short '
            'clause: same woman, same face, hair, tattoos and piercings, camera fixed, same '
            'background. ' + _level_words(explicit) + ' Under 120 words. Output only the prompt.'},
        {'role': 'user', 'content': (f'Examples:\n{examples}\n\n' if examples else '')
            + f'Action: {idea.strip()}\n\nBase prompt, for the fixed details: {base} /no_think'},
    ], max_tokens=600, why=why)
    missing = [t for t in triggers if t.lower() not in text.lower()]
    return (', '.join(missing) + ', ' + text) if text and missing else text


def write_motion_prompt(idea, base='', explicit=False, style=None, why=None):
    """The clip's prompt with the chosen action written out: the template's
    fixed clauses kept, its generic movement replaced by what she does. With
    a LoRA's style, written the way that LoRA's prompts are written."""
    if not (idea or '').strip():
        if why is not None:
            why['reason'] = 'no motion typed'
        return ''
    if style:
        return _styled_prompt(idea, base, explicit, style, why)
    return _runpod_chat(RUNPOD_QWEN_ENDPOINT, 'Qwen/Qwen3-32B-AWQ', [
        {'role': 'system', 'content':
            'You rewrite prompts for an image-to-video model that animates an existing photo. '
            'Keep every fixed clause of the base prompt: the same woman with the same face, hair, '
            'tattoos, piercings, jewelry and clothes, the camera fixed, the same lighting and '
            'background. Replace any generic movement in it with the requested action, written '
            'out: the main action first, which hand or body part does it, its pace, that it '
            'repeats through the clip, then her expression. Do not add new details about how she '
            'looks. ' + _level_words(explicit) + ' Under 120 words. Output only the prompt.'},
        {'role': 'user', 'content': f'Action: {idea.strip()}\n\nBase prompt: {base} /no_think'},
    ], max_tokens=600, why=why)


def write_h3_action(idea, explicit=False, why=None):
    """Only the action of an H3 clip, written out; h3_prompt adds the locks."""
    if not (idea or '').strip():
        return ''
    return _runpod_chat(RUNPOD_QWEN_ENDPOINT, 'Qwen/Qwen3-32B-AWQ', [
        {'role': 'system', 'content':
            'You write the action part of a prompt for an image-to-video model that animates an '
            'existing photo. Write only what she does: the main action first, which hand or body '
            'part does it, its pace and how it changes, her body movement, breathing, sounds and '
            'expression, in short plain sentences. Never describe her face, hair, body, tattoos, '
            'piercings, clothes, the camera or the background: those are fixed elsewhere. '
            + _level_words(explicit) + ' Under 80 words. Output only the action.'},
        {'role': 'user', 'content': f'Action: {idea.strip()} /no_think'},
    ], max_tokens=400, why=why) or idea.strip()


def write_h3_prompt(idea, spec, chained=False, why=None):
    style = lora_style(spec) or {}
    return h3_prompt(write_h3_action(idea, spec.get('explicit'), why), spec.get('explicit'),
                     chained, style.get('triggers') or (),
                     merge_negative(spec.get('negative_extra'), video=True))


# ── Selection ─────────────────────────────────────────────────────────────────

PROVIDERS = {'runware': RunwareProvider, 'modelslab': ModelsLabProvider,
             'runpod': RunPodProvider, 'vast': VastProvider}


def provider_name():
    return (os.getenv('IMAGEGEN_PROVIDER') or 'runware').strip().lower()


def get_provider(name=None):
    name = (name or provider_name()).lower()
    cls = PROVIDERS.get(name)
    if cls is None:
        raise GenerationError(f'unknown generation provider {name!r}')
    return cls()


def provider_name_for(spec):
    """Which provider a job actually runs on. Almost always the configured
    default -- except the explicit replace, which Runware has no model for at
    all, so that one job always goes to ModelsLab regardless of the setting."""
    if ((spec.get('job') == 'swap' or spec.get('kind') == 'swap')
            and (spec.get('model') or DEFAULT_SWAP_MODEL) == EXPLICIT_SWAP_MODEL):
        return 'modelslab'
    if spec.get('model') in VAST_MODELS:
        return 'vast'
    if spec.get('model') in RUNPOD_MODELS:
        return 'runpod'
    return provider_name()


def provider_for(spec):
    return get_provider(provider_name_for(spec))


def configured():
    try:
        get_provider()
        return True
    except GenerationError:
        return False


def _data_uri(b64, mime):
    mime = mime or 'image/jpeg'
    return f'data:{mime};base64,{b64}'


# Kling refuses a reference whose sides fall outside this range; a 4k still is
# over it and a thumbnail under it.
KLING_REF_SIDE = (340, 3850)


def fit_reference(url, lo=KLING_REF_SIDE[0], hi=KLING_REF_SIDE[1]):
    """The reference rescaled so both sides sit inside [lo, hi], as a JPEG data
    URI. Returned untouched when it already fits or cannot be read."""
    import base64
    import io
    from PIL import Image
    try:
        if url.startswith('data:'):
            data = base64.b64decode(url.split(',', 1)[1])
        else:
            data, _ = fetch_result(url)
        img = Image.open(io.BytesIO(data))
        w, h = img.size
    except Exception:
        return url
    if lo <= min(w, h) and max(w, h) <= hi:
        return url
    scale = min(hi / max(w, h), max(lo / min(w, h), 1.0))
    img = img.convert('RGB').resize((max(lo, round(w * scale)), max(lo, round(h * scale))),
                                    Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=92)
    return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()


def fetch_result(url):
    """Pull a finished generation off the provider's CDN. Their URLs are short
    lived, so nothing downstream may hold one — the bytes go to our storage."""
    import requests
    if url.startswith('data:'):
        mime, b64 = url[5:].split(';base64,', 1)
        return base64.b64decode(b64), mime
    try:
        resp = requests.get(url, timeout=TIMEOUT * 3)
        resp.raise_for_status()
    except Exception as e:
        raise GenerationError(f'could not download the result: {e}', fatal=False)
    mime = (resp.headers.get('Content-Type') or '').split(';')[0].strip()
    return resp.content, mime or 'application/octet-stream'


# (blur px, glow opacity, grain amount, black lift). Done here, not by a second
# model, because it costs nothing: Seedream's stills are too clean to pass for
# a phone photo, and grain, halation and a soft lens are what a phone adds.
PHONE_LOOKS = {
    'light':  (0.3, 0.08, 0.02, 4),
    'medium': (0.5, 0.12, 0.035, 7),
    'heavy':  (0.8, 0.18, 0.055, 10),
}


def phone_look(data, mime, strength):
    """Make a generated still read like a phone photo. Any failure hands back
    the original, so the filter can never cost the creator her generation."""
    look = PHONE_LOOKS.get(strength)
    if not look or not (mime or '').startswith('image/'):
        return data, mime
    import io
    from PIL import Image, ImageChops, ImageFilter
    blur, glow, grain, lift = look
    try:
        img = Image.open(io.BytesIO(data)).convert('RGB')
        img.thumbnail((2016, 2016), Image.LANCZOS)
        img = img.filter(ImageFilter.GaussianBlur(blur))
        halo = img.filter(ImageFilter.GaussianBlur(max(img.size) / 60))
        img = Image.blend(img, ImageChops.screen(img, halo), glow)
        img = img.point(lambda v: lift + v * (255 - lift * 1.6) / 255)
        r, g, b = img.split()
        img = Image.merge('RGB', (r.point(lambda v: min(255, v + 3)), g,
                                  b.point(lambda v: max(0, v - 3))))
        noise = Image.effect_noise(img.size, 64).convert('RGB')
        noise = noise.filter(ImageFilter.GaussianBlur(0.6))
        img = Image.blend(img, ImageChops.overlay(img, noise), grain * 4)
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=82)
        return out.getvalue(), 'image/jpeg'
    except Exception:
        logger.exception('phone look failed')
        return data, mime
