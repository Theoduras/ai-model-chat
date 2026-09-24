"""The character catalogue: which photos make up a character, which of them a
level requires, and the words each one puts in a prompt.

Pure data and pure functions, no Flask, so the rules that keep safe-work and
explicit apart can be tested without a server. Everything is keyed by body
type, so a second one is a new table entry rather than a rewrite.
"""
import os
import re

from imagegen import LEVEL_ORDER, PHOTO_LOOK, SCENES, SHOT_LEVEL

MIN_AGE = 18

# The builder offers three levels; they sit on the studio's own ladder so the
# same comparison gates both.
LEVELS = (('sfw', 'SFW'), ('moderate', 'Topless'), ('explicit', 'Explicit'))
LEVEL_KEYS = tuple(k for k, _ in LEVELS)
BATCH_CHOICES = (1, 2, 3, 4, 6)
DEFAULT_BATCH = 4
MAX_BATCH = 8


class CharacterError(ValueError):
    pass


def _rank(level):
    try:
        return LEVEL_ORDER.index(level or 'sfw')
    except ValueError:
        return 0


# ── Features ──────────────────────────────────────────────────────────────────
# Every option is ours, so nothing typed can reach an explicit prompt. An option
# is (label, prompt fragment); the fragment is what the model reads.

def _opts(noun, *values):
    return [(v, f'{v.lower()} {noun}'.strip()) for v in values]


FEATURES = {
    'female': {
        # Face
        'ethnicity': ('Ethnicity', 'face', [('White / European', 'of European descent'), ('Black / African', 'of African descent'),
                                             ('East Asian', 'of East Asian descent'), ('South Asian', 'of South Asian descent'),
                                             ('Southeast Asian', 'of Southeast Asian descent'),
                                             ('Hispanic / Latina', 'of Latin American descent'),
                                             ('Middle Eastern', 'of Middle Eastern descent'), ('Mixed', 'of mixed heritage')]),
        # Never younger than 18, and never a "teen" look: the lowest option is
        # still a grown woman, and it weighs double toward the youth limit.
        'apparent_age': ('Looks', 'face', [('18–21', 'looks 18 to 21, clearly a grown adult woman'),
                                           ('Early 20s', 'looks in her early twenties'),
                                           ('Late 20s', 'looks in her late twenties'), ('30s', 'looks in her thirties'),
                                           ('40s', 'looks in her forties'), ('50s+', 'looks fifty or older')]),
        'face_shape': ('Face shape', 'face', [('Oval', 'oval face'), ('Round', 'round face'), ('Square', 'square face'),
                                              ('Heart-shaped', 'heart-shaped face'), ('Diamond', 'diamond face'),
                                              ('Rectangle', 'long rectangular face'),
                                              ('Triangle', 'triangle-shaped face, wide forehead and narrow pointed chin'),
                                              ('Base-down triangle', 'base-down triangle face, narrow forehead and wide jaw')]),
        'skin_tone': ('Skin tone', 'face', _opts('skin', 'Fair', 'Light', 'Light olive', 'Olive', 'Tan', 'Brown', 'Deep brown', 'Dark')),
        'marks': ('Freckles or moles', 'face', [('None', 'clear skin'), ('Light freckles, nose', 'light freckles across the nose'),
                                                 ('Heavy freckles', 'heavy freckles'), ('Beauty mark, cheek', 'a small beauty mark on the cheek'),
                                                 ('Beauty mark, lip', 'a small beauty mark above the lip')]),
        'eye_shape': ('Eye shape', 'face', _opts('eyes', 'Almond', 'Round', 'Hooded', 'Monolid', 'Upturned', 'Downturned')),
        'eye_colour': ('Eye colour', 'face', _opts('eyes', 'Brown', 'Dark brown', 'Hazel', 'Green', 'Blue', 'Grey')),
        'brows': ('Eyebrows', 'face', _opts('eyebrows', 'Soft arch, medium', 'High arch, thin', 'Straight, full', 'Bold, thick')),
        'nose': ('Nose', 'face', _opts('nose', 'Straight, narrow', 'Button', 'Slightly upturned', 'Roman', 'Wide')),
        'lips': ('Lips', 'face', _opts('lips', 'Full, defined bow', 'Medium', 'Thin', 'Very full')),
        'cheekbones': ('Cheekbones', 'face', _opts('cheekbones', 'High', 'Medium', 'Soft')),
        'jaw': ('Jaw and chin', 'face', [('Soft, rounded chin', 'a soft jaw and rounded chin'), ('Defined jaw', 'a defined jawline'),
                                          ('Pointed chin', 'a pointed chin'), ('Square jaw', 'a square jaw')]),
        'ears': ('Ears', 'face', _opts('ears', 'Small, close-set', 'Medium', 'Prominent')),
        'hair_colour': ('Hair colour', 'face', _opts('hair', 'Black', 'Dark brown', 'Light brown', 'Auburn', 'Red', 'Strawberry blonde', 'Blonde', 'Platinum')
                        + [('Custom', '')]),
        'hair_texture': ('Hair length and texture', 'face', _opts('hair', 'Long, straight', 'Long, loose waves', 'Long, curly', 'Shoulder-length', 'Bob', 'Pixie')),
        'hairline': ('Hairline', 'face', [('Rounded, middle part', 'a rounded hairline with a middle part'), ('Side part', 'a side part'),
                                          ('Straight, fringe', 'a straight fringe'), ('Widow\'s peak', 'a widow\'s peak')]),
        # Body
        'height': ('Height', 'body', [('Under 155 cm', 'short, under 155 cm'), ('155–165 cm', 'average height'), ('165–175 cm', 'tall'), ('Over 175 cm', 'very tall')]),
        'build': ('Build', 'body', _opts('build', 'Slim', 'Athletic', 'Average', 'Curvy', 'Voluptuous', 'Muscular')),
        'body_shape': ('Body shape', 'body', [('Apple', 'an apple-shaped figure, fuller through the middle'),
                                              ('Pear', 'a pear-shaped figure, hips wider than shoulders'),
                                              ('Hourglass', 'an hourglass figure'),
                                              ('Rectangle', 'a straight rectangle figure'),
                                              ('Oval', 'an oval figure, fuller through the torso'),
                                              ('Inverted triangle', 'an inverted-triangle figure, shoulders wider than hips')]),
        'shoulders': ('Shoulders', 'body', _opts('shoulders', 'Narrow', 'Medium', 'Broad')),
        'waist': ('Waist', 'body', _opts('waist', 'Defined', 'Straight', 'Soft')),
        'hips': ('Hips', 'body', _opts('hips', 'Narrow', 'Medium', 'Wide')),
        'bust': ('Bust', 'body', _opts('bust', 'Small', 'Medium', 'Large', 'Very large')),
        'glute_shape': ('Bum shape, from behind', 'body', [('Round', 'a round bum'), ('Heart-shaped', 'a heart-shaped bum'),
                                                           ('A-shaped', 'an A-shaped bum, fuller at the bottom'),
                                                           ('Pear', 'a pear-shaped bum, widest at the base'),
                                                           ('Square', 'a square-shaped bum'),
                                                           ('Bubble', 'a high, rounded bubble bum'),
                                                           ('Wide', 'a wide, full bum set low on the hips'),
                                                           ('V-shaped', 'a V-shaped bum')]),
        'thighs': ('Thighs', 'body', _opts('thighs', 'Slim', 'Toned', 'Full')),
        'tattoos': ('Tattoos', 'body', [('None', 'no tattoos'), ('Small, wrist', 'a small wrist tattoo'), ('Small, ankle', 'a small ankle tattoo'),
                                        ('Hip', 'a hip tattoo'), ('Sleeve', 'a full arm sleeve tattoo'), ('Back piece', 'a large back tattoo')]),
        'piercings': ('Piercings', 'body', [('None', 'no piercings'), ('Ears', 'pierced ears'), ('Nose', 'a nose piercing'), ('Navel', 'a navel piercing')]),
        'birthmarks': ('Birthmarks', 'body', [('None', ''), ('Shoulder', 'a small birthmark on the shoulder'), ('Hip', 'a small birthmark on the hip')]),
        'nails': ('Nails', 'body', _opts('nails', 'Short, nude', 'Medium, painted', 'Long, painted', 'French tips')),
        # Topless
        'cup': ('Cup size', 'breasts', [('B', 'B-cup breasts'), ('C', 'C-cup breasts'), ('D', 'D-cup breasts'), ('DD', 'DD-cup breasts'),
                                        ('E+', 'very large breasts'), ('A', 'A-cup breasts')]),
        'breast_shape': ('Shape', 'breasts', _opts('breasts', 'Round', 'Teardrop', 'Athletic', 'Relaxed')),
        'spacing': ('Spacing', 'breasts', _opts('set breasts', 'Close', 'Average', 'Wide')),
        'augmented': ('Natural or augmented', 'breasts', [('Natural', 'natural breasts'), ('Augmented', 'augmented breasts')]),
        'perkiness': ('Perkiness', 'breasts', _opts('breasts', 'Perky', 'Natural', 'Soft')),
        'areola_size': ('Areola size', 'nipples', _opts('areolae', 'Small', 'Medium', 'Large')),
        'areola_colour': ('Areola colour', 'nipples', _opts('areolae', 'Light pink', 'Pink', 'Rosy brown', 'Brown', 'Dark brown')),
        'nipple_size': ('Nipple size', 'nipples', _opts('nipples', 'Small', 'Medium', 'Large')),
        'nipple_shape': ('Nipple shape', 'nipples', _opts('nipples', 'Flat', 'Puffy', 'Protruding', 'Inverted')),
        'nipple_piercing': ('Nipple piercing', 'nipples', [('None', ''), ('One', 'one pierced nipple'), ('Both', 'both nipples pierced')]),
        # Explicit
        'pubic_style': ('Pubic hair style', 'pubic', [('Trimmed', 'trimmed pubic hair'), ('Landing strip', 'a landing strip'),
                                                     ('Triangle', 'a neat triangle of pubic hair'), ('Natural', 'natural full pubic hair'),
                                                     ('Shaved', 'shaved pubic area')]),
        'pubic_colour': ('Pubic hair colour', 'pubic', _opts('pubic hair', 'Matches hair', 'Dark', 'Light')),
        'pubic_density': ('Density', 'pubic', _opts('', 'Sparse', 'Medium', 'Dense')),
        'labia': ('Labia', 'vulva', [('Tucked', 'tucked inner labia'), ('Slightly visible', 'slightly visible inner labia'), ('Protruding', 'protruding inner labia')]),
        'labia_fullness': ('Fullness', 'vulva', _opts('outer labia', 'Slim', 'Medium', 'Full')),
        'vulva_colour': ('Colour', 'vulva', _opts('vulva', 'Pink', 'Rosy', 'Tan', 'Brown', 'Dark')),
        'anus_colour': ('Colour', 'anus', _opts('anus', 'Pink', 'Rosy', 'Tan', 'Brown', 'Dark')),
    },
}

# Which feature groups each level may put into words. A safe-work prompt never
# carries an intimate word, whatever the character has filled in.
GROUP_LEVEL = {'face': 'sfw', 'body': 'sfw', 'breasts': 'moderate',
               'nipples': 'moderate', 'pubic': 'explicit', 'vulva': 'explicit',
               'anus': 'explicit'}

# Options that are fine alone but, stacked, describe someone who reads young.
# Counted rather than banned, so a petite adult is still possible.
YOUTH_LEANING = {
    ('height', 'Under 155 cm'), ('build', 'Slim'), ('shoulders', 'Narrow'),
    ('hips', 'Narrow'), ('bust', 'Small'), ('cup', 'A'),
    ('pubic_style', 'Shaved'), ('pubic_density', 'Sparse'),
    ('face_shape', 'Round'), ('nose', 'Button'), ('perkiness', 'Perky'),
    ('apparent_age', '18–21'), ('apparent_age', 'Early 20s'),
}
YOUTH_WEIGHT = {('apparent_age', '18–21'): 2}
# Options renamed after sheets were saved with them: the old label still
# validates and is stored as the new one.
RENAMED = {('face_shape', 'Long'): 'Rectangle'}

YOUTH_BLOCK_AT = 3
YOUTH_WARN_AT = 2

# Words that must never reach a prompt from the notes field.
YOUTH_TERMS = ('child', 'kid', 'teen', 'underage', 'minor', 'young girl',
               'schoolgirl', 'school girl', 'loli', 'petite girl', 'little girl',
               'baby face', 'babyface', 'barely legal', 'jailbait', 'preteen',
               'adolescent', 'juvenile', 'girlish', 'youthful')


# ── Views ─────────────────────────────────────────────────────────────────────

STUDIO = 'Soft natural daylight, plain light-grey wall behind her. ' + PHOTO_LOOK

VARIATIONS = BATCH_CHOICES

# A preset sets every body feature, replacing earlier picks, so clicking one
# visibly changes the sheet. Face and intimate features are left alone.
def _preset(height, build, shape, shoulders, waist, hips, bust, glutes, thighs, tattoos, piercings, birthmarks, nails):
    return {'height': height, 'build': build, 'body_shape': shape, 'shoulders': shoulders, 'waist': waist,
            'hips': hips, 'bust': bust, 'glute_shape': glutes, 'thighs': thighs, 'tattoos': tattoos,
            'piercings': piercings, 'birthmarks': birthmarks, 'nails': nails}


BODY_PRESETS = {
    'petite_athletic': ('Petite athletic', 'Short, toned, compact',
                        _preset('155–165 cm', 'Athletic', 'Rectangle', 'Narrow', 'Defined', 'Medium', 'Medium',
                                'Bubble', 'Toned', 'Small, ankle', 'Ears', 'None', 'Short, nude')),
    'slim_tall': ('Slim tall', 'Tall, lean, long lines',
                  _preset('Over 175 cm', 'Slim', 'Rectangle', 'Medium', 'Straight', 'Medium', 'Medium',
                          'Round', 'Slim', 'None', 'Ears', 'None', 'Medium, painted')),
    'curvy': ('Curvy', 'Full hips and thighs',
              _preset('155–165 cm', 'Curvy', 'Pear', 'Medium', 'Defined', 'Wide', 'Large',
                      'Wide', 'Full', 'Hip', 'Navel', 'None', 'Long, painted')),
    'hourglass': ('Hourglass', 'Defined waist, balanced curves',
                  _preset('165–175 cm', 'Curvy', 'Hourglass', 'Medium', 'Defined', 'Wide', 'Large',
                          'Heart-shaped', 'Full', 'None', 'Ears', 'None', 'French tips')),
    'athletic_tall': ('Athletic tall', 'Strong frame, sporty',
                      _preset('Over 175 cm', 'Athletic', 'Inverted triangle', 'Broad', 'Defined', 'Medium', 'Medium',
                              'Bubble', 'Toned', 'Small, wrist', 'Ears', 'None', 'Short, nude')),
    'slim_petite': ('Slim petite', 'Small frame, lean',
                    _preset('155–165 cm', 'Slim', 'Rectangle', 'Medium', 'Defined', 'Medium', 'Medium',
                            'Round', 'Slim', 'None', 'Ears', 'None', 'Medium, painted')),
    'voluptuous': ('Voluptuous', 'Very full bust, hips and bum',
                   _preset('165–175 cm', 'Voluptuous', 'Hourglass', 'Medium', 'Defined', 'Wide', 'Very large',
                           'Bubble', 'Full', 'Hip', 'Navel', 'None', 'Long, painted')),
    'fitness': ('Fitness', 'Muscular, strong, sculpted',
                _preset('165–175 cm', 'Muscular', 'Inverted triangle', 'Broad', 'Defined', 'Medium', 'Medium',
                        'Bubble', 'Toned', 'Sleeve', 'Navel', 'None', 'Short, nude')),
    'pear': ('Pear / thick bottom', 'Slimmer top, big hips and bum',
             _preset('155–165 cm', 'Curvy', 'Pear', 'Narrow', 'Defined', 'Wide', 'Medium',
                     'Wide', 'Full', 'Small, ankle', 'Ears', 'None', 'French tips')),
    'scratch': ('Start from scratch', 'Clear every body pick', {}),
}


def apply_preset(sheet, preset, body_type='female'):
    if preset not in BODY_PRESETS:
        raise CharacterError('Unknown body preset.')
    values = BODY_PRESETS[preset][2]
    out = dict(sheet or {})
    for k, (_, group, _) in features(body_type).items():
        if group != 'body':
            continue
        if k in values:
            out[k] = values[k]
        else:
            out.pop(k, None)
    return out

VIEWS = {
    'female': [
        # key, label, group, rating, required_from, parents, tier, mode, region, framing, feature groups.
        # A close-up names the body features it keeps in `body`: height or bust
        # in a zoomed shot only pulls the camera back to show them.
        dict(key='face_front', label='Face, front', group='face', rating='sfw', required_from='sfw', parents=(), tier=0, mode='reference',
             framing=('a front-facing head-and-shoulders portrait, looking straight into the lens, neutral '
                      'relaxed expression, mouth closed, hair tucked behind the shoulders, both ears visible'),
             uses=('face',)),
        dict(key='face_three_quarter', label='Face, three-quarter', group='face', rating='sfw', required_from=None,
             parents=('face_front',), tier=1, mode='reference', framing='a three-quarter view head-and-shoulders portrait, neutral expression', uses=('face',)),
        dict(key='face_profile', label='Face, profile', group='face', rating='sfw', required_from=None,
             parents=('body_front',), tier=1, mode='reference', framing='a side-profile head-and-shoulders portrait, neutral expression', uses=('face',)),
        dict(key='face_smile', label='Face, smiling', group='face', rating='sfw', required_from=None,
             parents=('body_front',), tier=1, mode='reference', framing='a front-facing head-and-shoulders portrait with a natural warm smile', uses=('face',)),
        dict(key='body_front', label='Full body, front', group='body', rating='sfw', required_from='sfw', parents=('face_front',), tier=0, mode='reference',
             framing=('a full-body photo from head to feet, standing perfectly straight and upright, facing the '
                      'camera squarely, head level, shoulders level, feet together, arms relaxed slightly away '
                      'from the body, symmetrical posture, wearing {outfit}'),
             uses=('face', 'body')),
        dict(key='body_side', label='Full body, side', group='body', rating='sfw', required_from=None, parents=('body_front',), tier=1, mode='reference',
             framing=('a full-body side view, standing perfectly straight and upright, head level, feet together, '
                      'wearing {outfit}'), uses=('body',)),
        dict(key='body_back', label='Full body, back', group='body', rating='sfw', required_from='explicit', parents=('body_front',), tier=1, mode='reference',
             framing=('a full-body view from behind, standing perfectly straight and upright, head level, feet '
                      'together, wearing {outfit}'), uses=('body',)),
        dict(key='hands', label='Hands', group='body', rating='sfw', required_from=None, parents=('body_front',), tier=1, mode='reference',
             zoom=True, body=('nails', 'tattoos'),
             framing=('a tight close-up of only her two hands, resting open palms down side by side on a plain surface, '
                      'fingers and nails in sharp focus, wrists at the frame edge'), uses=('body',)),
        dict(key='feet', label='Feet', group='body', rating='sfw', required_from=None, parents=('body_front',), tier=1, mode='reference',
             zoom=True, body=('tattoos',),
             framing=('a tight close-up of only her two bare feet standing side by side on a plain floor, '
                      'toes and nails in sharp focus, ankles at the top edge of the frame'), uses=('body',)),
        dict(key='nude_front', label='Nude full body, front', group='nsfw', rating='moderate', required_from='moderate',
             parents=('body_front',), tier=0, mode='reference',
             framing='a full-body nude photo from head to feet, standing straight facing the camera, arms relaxed at her sides',
             uses=('body', 'breasts', 'nipples', 'pubic')),
        dict(key='breasts', label='Breasts (topless, front)', group='nsfw', rating='moderate', required_from='moderate',
             parents=('nude_front',), tier=1, mode='crop', region='chest', zoom=True, body=('build',),
             framing=('a close-up of her bare breasts from the front, framed from the collarbones to just below the '
                      'breasts, arms down at her sides out of frame, both breasts centred and in sharp focus'),
             uses=('breasts', 'nipples')),
        dict(key='nipples', label='Nipples (close-up)', group='nsfw', rating='moderate', required_from='moderate',
             parents=('breasts',), tier=2, mode='crop', region='chest_detail', zoom=True, body=(),
             framing=('a macro close-up of her bare nipples and areolae, breast skin filling the frame, '
                      'nipple texture in sharp focus'),
             uses=('nipples',)),
        dict(key='rear_nude', label='Nude from behind (standing)', group='nsfw', rating='moderate', required_from='explicit',
             parents=('nude_front', 'body_back'), tier=1, mode='reference', framing='a full-body nude photo from behind, standing straight', uses=('body',)),
        dict(key='pubic', label='Pubic area (front, standing)', group='nsfw', rating='explicit', required_from='explicit',
             parents=('nude_front',), tier=1, mode='crop', region='pelvis', zoom=True,
             body=('hips', 'thighs', 'tattoos', 'birthmarks'),
             framing=('a close-up of her nude pubic area from the front while standing, framed from just below the '
                      'navel to the top of the thighs, pubic mound centred and in sharp focus'),
             uses=('pubic',)),
        dict(key='vulva_closed', label='Vagina, closed', group='nsfw', rating='explicit', required_from='explicit',
             parents=('nude_front',), tier=1, mode='reference', zoom=True, body=('thighs',),
             framing=('an explicit macro close-up of her vulva with labia closed, legs apart, the vulva centred and '
                      'filling the frame, inner thighs at the edges'),
             uses=('pubic', 'vulva')),
        dict(key='vulva_open', label='Vagina, open', group='nsfw', rating='explicit', required_from=None,
             parents=('vulva_closed',), tier=2, mode='reference', zoom=True, body=('thighs',),
             framing=('an explicit macro close-up of her vulva with labia spread open by her fingers, the vulva centred '
                      'and filling the frame, only fingertips and inner thighs at the edges'),
             uses=('pubic', 'vulva')),
        dict(key='anus_closed', label='Anus, closed (bending forward)', group='nsfw', rating='explicit', required_from='explicit',
             parents=('rear_nude',), tier=2, mode='reference', zoom=True, body=('glute_shape',),
             framing=('an explicit macro close-up from behind while she bends forward, buttocks parted, her closed '
                      'anus and her vulva just below it both in frame and in sharp focus, filling the frame, '
                      'buttock skin at the edges'),
             uses=('anus', 'vulva')),
        dict(key='anus_open', label='Anus, open (bending forward)', group='nsfw', rating='explicit', required_from=None,
             parents=('rear_nude',), tier=2, mode='reference', zoom=True, body=('glute_shape',),
             framing=('an explicit macro close-up from behind while she bends forward, buttocks spread, her open '
                      'anus and her vulva just below it both in frame and in sharp focus, filling the frame, '
                      'buttock skin at the edges'),
             uses=('anus', 'vulva')),
    ],
}

# Which views a content shot sends, beyond the face and body every shot gets.
# Keyed by shot or scene; the rating filter below still has the last word.
_SHOT_VIEWS = {
    'topless': ('nude_front', 'breasts', 'nipples'),
    'nude': ('nude_front', 'breasts', 'nipples', 'rear_nude', 'pubic'),
    'explicit': ('nude_front', 'breasts', 'nipples', 'pubic', 'vulva_closed'),
}
_SCENE_VIEWS = {
    'nipple-play': ('breasts', 'nipples'),
    'exposed': ('nude_front', 'pubic', 'vulva_closed', 'vulva_open'),
    'solo-touch': ('nude_front', 'pubic', 'vulva_closed', 'vulva_open'),
    'bent-over': ('rear_nude', 'anus_closed', 'anus_open', 'vulva_closed'),
}
_BASE_VIEWS = ('face_front', 'face_three_quarter', 'face_profile', 'face_smile',
               'body_front', 'body_side', 'body_back')
FACE_CHECKS = tuple(k for k, v in FEATURES['female'].items() if v[1] == 'face')
AGE_CHECKED_VIEWS = ('face_front', 'body_front')


# AI-generated examples the creator picks her anatomy from, one file each in
# character_looks/{folder}/. The pick goes to its views as the last reference;
# the views built on those inherit it. Kept out of FEATURES so no content
# prompt reads it. A folder with no files has no picker and requires nothing.
LOOK_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'character_looks')
_LOOK_TAIL = (' in the last reference image — its shape only, in her own skin tone; that image is an '
              'anatomy close-up, not her face or body.')
# sheet key: (folder, label, views, prompt text)
LOOKS = {
    'vulva_look': ('vulva', 'Vagina, closed', ('nude_front', 'vulva_closed'),
                   'Her vulva has the same shape as the vulva' + _LOOK_TAIL),
    'vulva_open_look': ('vulva_open', 'Vagina, open', ('vulva_open',),
                        'Her open vulva has the same shape as the open vulva' + _LOOK_TAIL),
}


def _look_files(folder):
    d = os.path.join(LOOK_ROOT, folder)
    return tuple(sorted((f[:-4] for f in (os.listdir(d) if os.path.isdir(d) else ())
                         if f.endswith('.jpg') and f[:-4].isdigit()), key=int))


LOOK_FILES = {k: _look_files(v[0]) for k, v in LOOKS.items()}


def look_for(level, key):
    """The sheet key of the example this view must be generated from, or None."""
    if level != 'explicit':
        return None
    return next((k for k, (_, _, vs, _) in LOOKS.items() if key in vs and LOOK_FILES[k]), None)


def look_path(ref):
    """'folder/n' from a job spec, as a file path, or None if it is not ours."""
    folder, _, n = (ref or '').partition('/')
    k = next((k for k, v in LOOKS.items() if v[0] == folder), None)
    return os.path.join(LOOK_ROOT, folder, n + '.jpg') if k and n in LOOK_FILES[k] else None


def views(body_type='female'):
    return VIEWS.get(body_type) or VIEWS['female']


def view(key, body_type='female'):
    for v in views(body_type):
        if v['key'] == key:
            return v
    return None


# Denoise for a crop's detail pass; reference weight for a generated view.
STRENGTH = {'crop': 0.32, 'reference': 0.65}


def parents(key, body_type='female'):
    v = view(key, body_type)
    return tuple(v['parents']) if v else ()


def topo_order(body_type='female'):
    """View keys with every parent before its children."""
    done, out = set(), []
    def visit(k):
        if k not in done:
            done.add(k)
            for p in parents(k, body_type):
                visit(p)
            out.append(k)
    for v in views(body_type):
        visit(v['key'])
    return out


def traits(key, body_type='female'):
    """Profile traits a view inherits: the sheet keys of the groups it uses,
    cut to the body features a close-up keeps."""
    v = view(key, body_type)
    uses = set(v['uses']) if v else set()
    if v and 'face' not in uses:
        uses |= {'body'}
    keep = v.get('body') if v else None
    return [k for k, (_, g, _) in features(body_type).items()
            if (g in uses and (g != 'body' or keep is None or k in keep))
            or (k == 'skin_tone' and v and 'face' not in v['uses'])]


def features(body_type='female'):
    return FEATURES.get(body_type) or FEATURES['female']


def views_for_level(level, body_type='female'):
    return [v for v in views(body_type) if _rank(v['rating']) <= _rank(level)]


def required_views(level, body_type='female'):
    return [v['key'] for v in views(body_type)
            if v['required_from'] and _rank(v['required_from']) <= _rank(level)]


def missing_views(level, approved, body_type='female'):
    have = set(approved or ())
    return [k for k in required_views(level, body_type) if k not in have]


def catalogue(level, body_type='female'):
    """What the builder may show at this level. Cut server-side: a view or a
    feature above the level is not in the payload at all."""
    feats = features(body_type)
    groups = {g for g, lvl in GROUP_LEVEL.items() if _rank(lvl) <= _rank(level)}
    return {
        'level': level, 'levels': [{'key': k, 'label': l} for k, l in LEVELS],
        'views': [dict(v, parents=list(v['parents']), uses=list(v['uses']), traits=traits(v['key'], body_type),
                       required=bool(v['required_from'] and _rank(v['required_from']) <= _rank(level)))
                  for v in views_for_level(level, body_type)],
        'features': [{'key': k, 'label': lab, 'group': g, 'options': [o for o, _ in opts]}
                     for k, (lab, g, opts) in feats.items() if g in groups],
        'batch': list(BATCH_CHOICES), 'default_batch': DEFAULT_BATCH,
        'variations': list(VARIATIONS),
        'presets': [{'key': k, 'label': l, 'hint': h, 'values': v} for k, (l, h, v) in BODY_PRESETS.items()],
        'min_age': MIN_AGE,
        'looks': [{'key': k, 'folder': f, 'label': lab, 'views': list(vs), 'options': list(LOOK_FILES[k])}
                  for k, (f, lab, vs, _) in LOOKS.items() if level == 'explicit' and LOOK_FILES[k]],
        'outfits': list(OUTFITS), 'outfit_colours': OUTFIT_COLOURS,
    }


# ── Validation ────────────────────────────────────────────────────────────────

def youth_score(sheet):
    return sum(YOUTH_WEIGHT.get((k, v), 1) for k, v in (sheet or {}).items() if (k, v) in YOUTH_LEANING)


def clean_notes(text, banned=()):
    text = re.sub(r'\s+', ' ', (text or '')).strip()[:400]
    low = text.lower()
    for term in YOUTH_TERMS:
        if re.search(r'\b' + re.escape(term) + r'\b', low):
            raise CharacterError(f'"{term}" cannot be used to describe a character.')
    for term in banned or ():
        term = (term or '').strip()
        if term:
            text = re.sub(re.escape(term), '', text, flags=re.I)
    return text.strip()


def validate(data, body_type='female'):
    """(clean, warnings) for a character save, or CharacterError."""
    try:
        age = int(data.get('age') or 0)
    except (TypeError, ValueError):
        age = 0
    if age < MIN_AGE:
        raise CharacterError(f'A character must be {MIN_AGE} or over.')
    if age > 80:
        raise CharacterError('Age must be 80 or under.')
    level = data.get('nsfw_level') or 'sfw'
    if level not in LEVEL_KEYS:
        raise CharacterError('Unknown level.')
    feats = features(body_type)
    sheet = {}
    for k, v in (data.get('sheet') or {}).items():
        if k not in feats:
            continue
        v = RENAMED.get((k, v), v)
        if v in ('', None):
            continue
        if v not in [o for o, _ in feats[k][2]]:
            raise CharacterError(f'"{v}" is not an option for {feats[k][0]}.')
        sheet[k] = v
    score = youth_score(sheet)
    if score >= YOUTH_BLOCK_AT:
        raise CharacterError('Together these choices describe someone who could '
                             'read as underage. Change at least one of: ' +
                             ', '.join(feats[k][0] for k, v in sheet.items()
                                       if (k, v) in YOUTH_LEANING) + '.')
    warnings = []
    if score >= YOUTH_WARN_AT:
        warnings.append('Several choices lean young. The adult-appearance check '
                        'will be strict on this character.')
    raw = data.get('sheet') or {}
    picked = raw.get('view_outfits')
    if picked is None and raw.get('view_outfit'):
        picked = [raw['view_outfit']]
    if picked:
        if not isinstance(picked, list) or not all(is_outfit(o) for o in picked):
            raise CharacterError('Unknown outfit.')
        sheet['view_outfits'] = list(dict.fromkeys(picked))
        colours = raw.get('outfit_colours') or {}
        if not isinstance(colours, dict) or any(c not in OUTFIT_COLOURS for c in colours.values() if c):
            raise CharacterError('Unknown outfit colour.')
        colours = {o: c for o, c in colours.items() if c and o in sheet['view_outfits']}
        if colours:
            sheet['outfit_colours'] = colours
    for k, (_, label, _, _) in LOOKS.items():
        if raw.get(k):
            if raw[k] not in LOOK_FILES[k]:
                raise CharacterError(f'Unknown {label.lower()} example.')
            sheet[k] = raw[k]
    if sheet.get('hair_colour') == 'Custom':
        hexcode = str(raw.get('hair_colour_hex') or '').lower()
        if not re.fullmatch(r'#[0-9a-f]{6}', hexcode):
            raise CharacterError('Pick a custom hair colour.')
        sheet['hair_colour_hex'] = hexcode
    mode = raw.get('face_mode') or 'build'
    if mode not in FACE_MODES:
        raise CharacterError('Unknown face mode.')
    if mode == 'blend':
        # A blended face comes from the photos; drawn face features would
        # fight them, so only the steers that still make sense stay.
        sheet = {k: x for k, x in sheet.items()
                 if feats.get(k, ('', ''))[1] != 'face' or k in BLEND_KEEP}
        sheet['face_mode'] = 'blend'
    body_mode = raw.get('body_mode') or 'build'
    if body_mode not in BODY_MODES:
        raise CharacterError('Unknown body mode.')
    if body_mode == 'match':
        # Her shape comes from the photos; drawn shape choices would fight them.
        sheet = {k: x for k, x in sheet.items()
                 if feats.get(k, ('', ''))[1] != 'body' or k in BODY_MATCH_KEEP}
        sheet['body_mode'] = 'match'
    name = re.sub(r'\s+', ' ', str(data.get('name') or '')).strip()[:80] or 'Untitled draft'
    return {'name': name, 'age': age, 'nsfw_level': level, 'sheet': sheet,
            'notes': clean_notes(data.get('notes'), data.get('banned') or ())}, warnings


# ── Prompts ───────────────────────────────────────────────────────────────────

def _fragments(sheet, groups, body_type='female'):
    feats = features(body_type)
    out = []
    for k, (_, g, opts) in feats.items():
        if g not in groups or k not in (sheet or {}):
            continue
        frag = dict(opts).get(sheet[k], '')
        if k == 'hair_colour' and sheet[k] == 'Custom':
            frag = hair_words(sheet.get('hair_colour_hex')) + ' hair' if sheet.get('hair_colour_hex') else ''
        if k == 'pubic_colour' and sheet[k] == 'Matches hair':
            frag = 'pubic hair matching her hair colour'
        if k == 'pubic_density' and frag:
            frag = f'{frag} pubic hair'
        if frag:
            out.append(frag)
    return out


def allowed_groups(level):
    return tuple(g for g, lvl in GROUP_LEVEL.items() if _rank(lvl) <= _rank(level))


def describe(sheet, level, body_type='female'):
    """Her features as prompt text, never past `level`."""
    return ', '.join(_fragments(sheet, allowed_groups(level), body_type))


def adult_clause(age):
    age = max(MIN_AGE, int(age or MIN_AGE))
    return (f'Fictional adult woman, {age} years old, with a clearly adult face '
            'and fully adult body proportions.')


# Image models follow colour words, not hex codes, so a picked colour is
# described by its hue, saturation and lightness.
def hair_words(hexcode):
    import colorsys
    try:
        r, g, b = (int(hexcode[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (TypeError, ValueError, IndexError):
        return ''
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    h *= 360
    if l < 0.12:
        return 'jet black'
    if sat < 0.12:
        return 'platinum white' if l > 0.85 else 'silver grey' if l > 0.55 else 'ash grey' if l > 0.3 else 'charcoal'
    names = [(12, 'red'), (35, 'copper'), (50, 'golden blonde'), (70, 'honey blonde'), (160, 'green'), (200, 'teal'),
             (250, 'blue'), (290, 'purple'), (320, 'magenta'), (345, 'pink'), (361, 'red')]
    hue = next(n for top, n in names if h < top)
    warm = hue in ('copper', 'golden blonde', 'honey blonde')
    if l > 0.85:
        return 'platinum blonde' if warm else f'pastel {hue}'
    if warm and l < 0.42:
        hue = 'auburn' if h < 25 else 'brown'
    if hue == 'red' and l < 0.35:
        hue = 'burgundy red'
    tone = 'pastel' if l > 0.72 else 'light' if l > 0.58 else 'deep' if l < 0.28 else 'dark' if l < 0.42 else ''
    if sat < 0.3 and tone != 'pastel':
        tone = (tone + ' ash').strip()
    return f'{tone} {hue}'.strip()


BLEND_KEEP = ('ethnicity', 'apparent_age', 'hair_colour', 'hair_texture')
FACE_MODES = ('build', 'blend')
BODY_MATCH_KEEP = ('height', 'tattoos', 'piercings', 'birthmarks', 'nails')
BODY_MODES = ('build', 'match')


# What the full-body reference photos are dressed in. Kept out of FEATURES so
# no content prompt inherits it: a sheet holds `view_outfits` (a list, one set
# of options generated per outfit) and `outfit_colours` (outfit -> colour).
OUTFITS = {'Bodysuit': ('a plain fitted {c} bodysuit', 'nude-coloured'),
           'Casual': ('a fitted plain {c} T-shirt and slim jeans', ''),
           'Activewear': ('a fitted {c} tank top and leggings', ''),
           'Fitted dress': ('a simple fitted {c} knee-length dress', '')}
OUTFIT_COLOURS = {'Black': '#1d1d1f', 'White': '#f7f7f5', 'Grey': '#8e8e93', 'Beige': '#d8c3a5', 'Navy': '#23305a',
                  'Red': '#b3261e', 'Pink': '#e8a0bf', 'Green': '#3f6b45', 'Blue': '#3a6ea5', 'Brown': '#6b4a33'}


def outfits(sheet):
    sheet = sheet or {}
    picked = sheet.get('view_outfits')
    if picked is None and sheet.get('view_outfit'):
        picked = [sheet['view_outfit']]
    return [o for o in (picked or []) if is_outfit(o)] or ['Bodysuit']


UPLOADED_OUTFIT = re.compile(r'upload:[0-9a-f]{32}')


def is_outfit(o):
    return o in OUTFITS or bool(isinstance(o, str) and UPLOADED_OUTFIT.fullmatch(o))


OUTFIT_REF_TEXT = ('the exact outfit shown in the last reference image — the clothing only, '
                   'not the person wearing it')


def outfit_text(sheet, outfit=None):
    outfit = outfit if is_outfit(outfit) else outfits(sheet)[0]
    if outfit.startswith('upload:'):
        return OUTFIT_REF_TEXT
    template, default = OUTFITS[outfit]
    colour = ((sheet or {}).get('outfit_colours') or {}).get(outfit, '').lower() or default
    return re.sub(r'\s+', ' ', template.replace('{c}', colour))



def build_view_prompt(key, sheet, age, has_reference, body_type='female',
                      mode='reference', strength=None, outfit=None, blend=False,
                      match=False, look=False):
    v = view(key, body_type)
    if not v:
        raise CharacterError('Unknown view.')
    if '{outfit}' in v['framing']:
        v = dict(v, framing=v['framing'].replace('{outfit}', outfit_text(sheet, outfit)))
    uses = tuple(v['uses']) + (() if 'face' in v['uses'] else ('body',))
    groups = tuple(g for g in uses if _rank(GROUP_LEVEL[g]) <= _rank(v['rating']))
    keep = set(traits(key, body_type))
    kept = {k: x for k, x in (sheet or {}).items() if k in keep}
    if 'hair_colour' in kept and (sheet or {}).get('hair_colour_hex'):
        kept['hair_colour_hex'] = sheet['hair_colour_hex']
    frags = _fragments(kept, groups, body_type)
    if 'face' not in v['uses'] and (sheet or {}).get('skin_tone'):
        frags = _fragments({'skin_tone': sheet['skin_tone']}, ('face',), body_type) + frags
    detail = ', '.join(frags)
    strength = STRENGTH[mode] if strength is None else strength
    if mode == 'crop':
        # Seedream refuses a denoise strength, so the crop is the first
        # reference and the strength says in words how far it may move.
        touch = ('only sharpen detail and skin texture, change nothing else' if strength < 0.4
                 else 'refine detail, keeping pose, skin tone and lighting' if strength < 0.7
                 else 'redraw the detail freely, keeping the same body and lighting')
        lead = ('photorealistic high-resolution close-up recreated from the first reference '
                f'image, which is a crop of the same woman: {v["framing"]}. Same skin, lighting '
                f'and proportions as that crop; {touch}.')
    elif match and key == 'body_front':
        lead = ('photorealistic photo of the exact same woman as the first reference image — identical face — '
                'with a body matching the build, proportions and figure in the other reference photos: similar '
                'to them, not a copy of any one person; ignore their faces, now as ' + v['framing'] + '.')
    elif blend and key == 'face_front':
        lead = ('photorealistic photo of a new woman whose face blends facial features of the women in the '
                'reference images into one new, distinct face — she is not any one of them, now as '
                + v['framing'] + '.')
    elif has_reference:
        # Naming her face in a close-up pulls the camera back to include it.
        hold = ('identical skin and body' if v.get('zoom') and strength >= 0.5
                else 'the same skin and build, loosely' if v.get('zoom')
                else 'identical face and body' if strength >= 0.5
                else 'the same face and build, loosely')
        lead = ('photorealistic photo of the exact same woman as the reference images, '
                + hold + ', now as ' + v['framing'] + '.')
    else:
        lead = f"photorealistic photo of a woman, {v['framing']}."
    body = f' Her features: {detail}.' if detail else ''
    text = look and next((t for _, _, vs, t in LOOKS.values() if key in vs), '')
    if text:
        body += ' ' + text
    zoom = (' Zoomed in: the subject fills the whole frame; no face, no full body, nothing '
            'beyond the subject in shot.') if v.get('zoom') else ''
    return (lead + zoom + body + ' ' + STUDIO + ' ' + adult_clause(age)).strip()


def job_level(shot, scene):
    lvl = SHOT_LEVEL.get(shot or '', 'sfw')
    s = SCENES.get(scene or '')
    if s and _rank(s[0]) > _rank(lvl):
        lvl = s[0]
    return lvl


def views_for_job(shot, scene, level=None, body_type='female'):
    """The views a content generation sends, in the order they should go.
    A view rated above the job is never returned, whatever asked for it."""
    level = level or job_level(shot, scene)
    keys = list(_BASE_VIEWS)
    for k in _SHOT_VIEWS.get(shot or '', ()) + _SCENE_VIEWS.get(scene or '', ()):
        if k not in keys:
            keys.append(k)
    return [k for k in keys
            if view(k, body_type) and _rank(view(k, body_type)['rating']) <= _rank(level)]


# A reel's model takes at most three references, so it gets the two that
# carry identity; a still, when there is one, is the third.
REEL_VIEWS = ('face_front', 'body_front')


def reel_views(snap):
    return [k for k in REEL_VIEWS if k in snap['views']]


def snapshot_views(snap, shot, scene, face_only=False):
    """The approved views in a job's character snapshot that this shot may
    send. A clip passes no shot, so it gets only the safe-work base views."""
    keys = [k for k in views_for_job(shot, scene, None, snap['body_type'])
            if k in snap['views']]
    if face_only:
        keys = [k for k in keys if view(k, snap['body_type'])['group'] == 'face']
    return keys


def content_clause(sheet, level, body_type='female'):
    """What a content prompt adds for a linked character: her fixed features,
    cut to the job's level."""
    text = describe(sheet, level, body_type)
    return f'Her fixed features: {text}.' if text else ''


# ── View tree status ──────────────────────────────────────────────────────────

def resolve_status(view, parents):
    """Display status of one view row. `view` and each parent are dicts with
    view_key, status, version and (view only) parent_versions."""
    if any(p['status'] != 'approved' for p in parents):
        return 'locked'
    seen = view.get('parent_versions') or {}
    if any(p['view_key'] in seen and p['version'] > seen[p['view_key']] for p in parents):
        return 'outdated'
    return view['status']


def resolve_all(rows, body_type='female'):
    """{key: display status} for rows keyed by view key. A view whose parent
    has no row is locked."""
    out = {}
    for k in topo_order(body_type):
        if k not in rows:
            continue
        ps = [rows.get(p) or {'view_key': p, 'status': 'not_started', 'version': 0}
              for p in parents(k, body_type)]
        out[k] = resolve_status(rows[k], ps)
    return out


def outdated_branch(rows, body_type='female'):
    """Keys to regenerate, parents first: every outdated view plus whatever
    sits below it in the tree."""
    status = resolve_all(rows, body_type)
    stale = set()
    for k in topo_order(body_type):
        if status.get(k) == 'outdated' or any(p in stale for p in parents(k, body_type)):
            stale.add(k)
    return [k for k in topo_order(body_type) if k in stale]


# Where a crop view sits in its parent, normalised 0-1, until the creator
# drags it. The anchors are standing A-pose shots, so the body lands in a
# predictable place.
REGIONS = {
    'chest': {'x': 0.28, 'y': 0.20, 'w': 0.44, 'h': 0.20},
    'pelvis': {'x': 0.30, 'y': 0.42, 'w': 0.40, 'h': 0.18},
    'chest_detail': {'x': 0.15, 'y': 0.15, 'w': 0.70, 'h': 0.60},
}
CROP_PAD = 0.08


def default_crop(region):
    return dict(REGIONS[region]) if region in REGIONS else {'x': .2, 'y': .2, 'w': .6, 'h': .6}
