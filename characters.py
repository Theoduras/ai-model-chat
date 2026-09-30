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


# Intimate colour is said relative to her skin, so it follows whatever skin
# tone she has rather than naming a colour that may not fit it.
def _skin_relative(noun):
    return [('Same as skin', f'{noun} the same tone as her skin'),
            ('Slightly darker', f'{noun} a shade slightly darker than her skin tone'),
            ('Darker', f'{noun} noticeably darker than her skin tone, natural pigmentation'),
            ('Rosy', f'{noun} in her own skin tone with a natural rosy flush')]


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
        # Described in words only: the picker's example photos never reach a
        # prompt, so she takes the makeup and never those women's faces.
        'makeup': ('Makeup', 'face', [
            ('Natural glam', 'wearing natural glam makeup: soft brown eyeshadow, defined lashes, groomed brows, soft blush and a glossy nude-pink lip'),
            ('Smoky red', 'wearing smoky makeup: smoky brown-burgundy eyes with smudged liner and a deep berry-red lip'),
            ('Gold winged', 'wearing gold winged makeup: gold and white shimmer on the lids, a sharp black winged liner and a glossy berry-pink lip'),
            ('Bronze glam', 'wearing bronze glam makeup: copper-bronze shimmer eyeshadow, winged liner, warm blush and a matte terracotta lip'),
            ('Emerald glitter', 'wearing emerald glitter makeup: emerald glitter smoky eyes, full lashes, glowing highlighted cheekbones and a matte mauve lip'),
            ('Bare', 'no makeup, bare natural skin')]),
        'cheekbones': ('Cheekbones', 'face', _opts('cheekbones', 'High', 'Medium', 'Soft')),
        'jaw': ('Jaw and chin', 'face', [('Soft, rounded chin', 'a soft jaw and rounded chin'), ('Defined jaw', 'a defined jawline'),
                                          ('Pointed chin', 'a pointed chin'), ('Square jaw', 'a square jaw')]),
        'ears': ('Ears', 'face', _opts('ears', 'Small, close-set', 'Medium', 'Prominent')),
        'hair_colour': ('Hair colour', 'face', _opts('hair', 'Black', 'Dark brown', 'Light brown', 'Auburn', 'Red', 'Strawberry blonde', 'Blonde', 'Platinum')
                        + [('Custom', '')]),
        'hair_texture': ('Hair style', 'face', _opts('hair', 'Long, straight', 'Long, loose waves', 'Long, curly', 'Shoulder-length', 'Bob', 'Pixie')
                         + [('Afro', 'a full natural afro'), ('Box braids', 'long box braids'),
                            ('Cornrows', 'neat cornrow braids close to the scalp'), ('Locs', 'long locs'),
                            ('Bantu knots', 'bantu knots'), ('Twist-out curls', 'defined twist-out curls'),
                            ('High ponytail', 'hair pulled back into a high ponytail'),
                            ('Low ponytail', 'hair pulled back into a sleek low ponytail'),
                            ('Side braid', 'a long braid over one shoulder'), ('Crown braid', 'a braided crown'),
                            ('Messy bun', 'hair up in a messy bun')]),
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
        # Explicit
        'pubic_style': ('Pubic hair style', 'pubic', [
            ('Trimmed', 'short, neatly trimmed pubic hair covering the mound'),
            ('Landing strip', 'a thin vertical landing strip: a narrow, neat band of short pubic hair about 1.5 cm wide '
                              'and 6 cm tall, centred on the pubic mound, starting just above the top of the vulva and '
                              'running straight up to the top of the mound; clearly much taller than wide, like a thin '
                              'vertical line of hair, not a patch or a triangle; the vulva and all skin around the strip '
                              'cleanly shaved; the stomach above the mound smooth and bare'),
            ('Triangle', 'a neat trimmed triangle of pubic hair on the mound; the rest shaved'),
            ('Natural', 'natural full pubic hair'),
                                                     ('Shaved', 'shaved pubic area')]),
        'pubic_colour': ('Pubic hair colour', 'pubic', _opts('pubic hair', 'Matches hair', 'Dark', 'Light')),
        'pubic_density': ('Density', 'pubic', _opts('', 'Sparse', 'Medium', 'Dense')),
        # Her shape comes from the example she picks (LOOKS), so only the colour
        # is described in words.
        'vulva_colour': ('Vulva colour', 'vulva', _skin_relative('vulva')),
        'anus_colour': ('Anus colour', 'anus', _skin_relative('anus')),
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
RENAMED = {**{(k, old): new for k in ('vulva_colour', 'anus_colour') for old, new in (
               ('Pink', 'Rosy'), ('Tan', 'Slightly darker'), ('Brown', 'Darker'), ('Dark', 'Darker'))},
           ('face_shape', 'Long'): 'Rectangle', ('makeup', 'Soft natural'): 'Natural glam',
           ('makeup', 'Polished'): 'Natural glam', ('makeup', 'Soft glam'): 'Bronze glam',
           ('makeup', 'Classic red lip'): 'Smoky red', ('makeup', 'Old Hollywood'): 'Smoky red',
           ('makeup', 'Wine lip'): 'Smoky red', ('makeup', 'Fresh glow'): 'Natural glam',
           ('makeup', 'Cat-eye gloss'): 'Gold winged', ('makeup', 'Bronze smoky'): 'Bronze glam'}

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

# A preset sets every body feature and her intimate shapes, replacing earlier
# picks, so clicking one visibly changes the sheet. Face picks and colours are
# left alone. No preset uses a youth-leaning option, so none can push a face
# pick over the limit.
def _preset(height, build, shape, shoulders, waist, hips, bust, glutes, thighs, birthmarks, nails,
            cup, breast_shape, spacing, augmented, perkiness, areola, nipple, nipple_shape, pubic, density):
    return {'height': height, 'build': build, 'body_shape': shape, 'shoulders': shoulders, 'waist': waist,
            'hips': hips, 'bust': bust, 'glute_shape': glutes, 'thighs': thighs,
            'birthmarks': birthmarks, 'nails': nails,
            'cup': cup, 'breast_shape': breast_shape, 'spacing': spacing, 'augmented': augmented,
            'perkiness': perkiness, 'areola_size': areola, 'nipple_size': nipple, 'nipple_shape': nipple_shape,
            'pubic_style': pubic, 'pubic_density': density}


PRESET_GROUPS = ('body', 'breasts', 'nipples', 'pubic')

BODY_PRESETS = {
    'natural': ('Natural', 'Average height and build',
                _preset('165–175 cm', 'Average', 'Rectangle', 'Medium', 'Straight', 'Medium', 'Medium',
                        'Round', 'Toned', 'None', 'Short, nude',
                        'B', 'Round', 'Average', 'Natural', 'Natural', 'Medium', 'Medium', 'Protruding', 'Trimmed', 'Medium')),
    'petite_athletic': ('Petite athletic', 'Compact, toned',
                        _preset('155–165 cm', 'Athletic', 'Rectangle', 'Medium', 'Defined', 'Medium', 'Medium',
                                'Bubble', 'Toned', 'None', 'Short, nude',
                                'B', 'Athletic', 'Average', 'Natural', 'Natural', 'Small', 'Medium', 'Protruding', 'Landing strip', 'Medium')),
    'curvy': ('Curvy', 'Full hips and thighs',
              _preset('155–165 cm', 'Curvy', 'Pear', 'Medium', 'Defined', 'Wide', 'Large',
                      'Wide', 'Full', 'None', 'Long, painted',
                      'D', 'Round', 'Average', 'Natural', 'Natural', 'Medium', 'Medium', 'Protruding', 'Trimmed', 'Medium')),
    'hourglass': ('Hourglass', 'Defined waist, balanced curves',
                  _preset('165–175 cm', 'Curvy', 'Hourglass', 'Medium', 'Defined', 'Wide', 'Large',
                          'Heart-shaped', 'Full', 'None', 'French tips',
                          'D', 'Teardrop', 'Average', 'Natural', 'Natural', 'Medium', 'Medium', 'Protruding', 'Triangle', 'Medium')),
    'athletic_tall': ('Athletic tall', 'Strong frame, sporty',
                      _preset('Over 175 cm', 'Athletic', 'Inverted triangle', 'Broad', 'Defined', 'Medium', 'Medium',
                              'Bubble', 'Toned', 'None', 'Short, nude',
                              'B', 'Athletic', 'Wide', 'Natural', 'Natural', 'Small', 'Medium', 'Flat', 'Landing strip', 'Medium')),
    'voluptuous': ('Voluptuous', 'Very full bust, hips and bum',
                   _preset('165–175 cm', 'Voluptuous', 'Hourglass', 'Medium', 'Defined', 'Wide', 'Very large',
                           'Bubble', 'Full', 'None', 'Long, painted',
                           'E+', 'Round', 'Close', 'Natural', 'Soft', 'Large', 'Large', 'Puffy', 'Trimmed', 'Medium')),
    'fitness': ('Fitness', 'Muscular, strong, sculpted',
                _preset('165–175 cm', 'Muscular', 'Inverted triangle', 'Broad', 'Defined', 'Medium', 'Medium',
                        'Bubble', 'Toned', 'None', 'Short, nude',
                        'C', 'Athletic', 'Average', 'Natural', 'Natural', 'Small', 'Medium', 'Protruding', 'Landing strip', 'Medium')),
    'pear': ('Pear / thick bottom', 'Slimmer top, big hips and bum',
             _preset('155–165 cm', 'Curvy', 'Pear', 'Medium', 'Defined', 'Wide', 'Medium',
                     'Wide', 'Full', 'None', 'French tips',
                     'C', 'Teardrop', 'Average', 'Natural', 'Natural', 'Medium', 'Medium', 'Protruding', 'Natural', 'Medium')),
    'glamour': ('Glamour', 'Tall, enhanced bust, defined waist',
                _preset('165–175 cm', 'Curvy', 'Hourglass', 'Medium', 'Defined', 'Medium', 'Very large',
                        'Round', 'Toned', 'None', 'Long, painted',
                        'DD', 'Round', 'Close', 'Augmented', 'Natural', 'Medium', 'Medium', 'Protruding', 'Landing strip', 'Medium')),
    'scratch': ('Start from scratch', 'Clear every body pick', {}),
}


def apply_preset(sheet, preset, body_type='female'):
    if preset not in BODY_PRESETS:
        raise CharacterError('Unknown body preset.')
    values = BODY_PRESETS[preset][2]
    out = dict(sheet or {})
    for k, (_, group, _) in features(body_type).items():
        if group not in PRESET_GROUPS or (group != 'body' and k.endswith('_colour')):
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
             parents=('face_front',), tier=1, mode='reference', framing='a side-profile head-and-shoulders portrait, neutral expression', uses=('face',)),
        dict(key='face_smile', label='Face, smiling', group='face', rating='sfw', required_from=None,
             parents=('face_front',), tier=1, mode='reference', framing='a front-facing head-and-shoulders portrait with a natural warm smile', uses=('face',)),
        dict(key='body_front', label='Full body, front', group='body', rating='sfw', required_from='sfw', parents=('face_front',), tier=0, mode='reference',
             framing=('a full-body photo from head to feet, standing perfectly straight and upright, facing the '
                      'camera squarely, head level, shoulders level, feet together, arms relaxed slightly away '
                      'from the body, symmetrical posture, wearing {outfit}'),
             uses=('face', 'body')),
        dict(key='body_back', label='Full body, back', group='body', rating='sfw', required_from='moderate', parents=('body_front',), tier=1, mode='reference',
             framing=('a full-body view from behind, standing perfectly straight and upright, head level, feet '
                      'together, wearing {outfit}'), uses=('body',)),
        dict(key='body_side', label='Full body, side', group='body', rating='sfw', required_from=None, parents=('body_front',), tier=1, mode='reference',
             framing=('a full-body side view, standing perfectly straight and upright, head level, feet together, '
                      'wearing {outfit}'), uses=('body',)),
        dict(key='hands', label='Hands, back of hand', group='body', rating='sfw', required_from=None, parents=('body_front',), tier=1, mode='reference',
             zoom=True, body=('nails',),
             framing=('a tight close-up of only her two hands, resting palms down side by side on a plain surface so '
                      'the backs of the hands, fingers and nails are in sharp focus, wrists at the frame edge'), uses=('body',)),
        dict(key='hands_palms', label='Hands, palms', group='body', rating='sfw', required_from=None, parents=('body_front',), tier=1, mode='reference',
             zoom=True, body=('nails',),
             framing=('a tight close-up of only her two open hands resting palms up side by side on a plain surface so '
                      'the palms, fingers and fingertips are in sharp focus, wrists at the frame edge'), uses=('body',)),
        dict(key='feet', label='Feet, top of foot', group='body', rating='sfw', required_from=None, parents=('body_front',), tier=1, mode='reference',
             zoom=True, body=(),
             framing=('a tight close-up of only her two bare feet standing side by side on a plain floor, seen from '
                      'above so the tops of the feet, toes and nails are in sharp focus, ankles at the top edge of the frame'), uses=('body',)),
        dict(key='feet_soles', label='Feet, soles', group='body', rating='sfw', required_from=None, parents=('body_front',), tier=1, mode='reference',
             zoom=True, body=(),
             framing=('a tight close-up of only the soles of her two bare feet, soles facing the camera side by side '
                      'against a plain surface, soles and toes in sharp focus, ankles at the edge of the frame'), uses=('body',)),
        dict(key='breasts', label='Breasts (topless, front)', group='nsfw', rating='moderate', required_from='moderate',
             parents=('body_front',), tier=1, mode='reference', nocrop=True, zoom=True, body=('build',),
             framing=('a close-up of her bare breasts from the front, framed from the collarbones to just below the '
                      'breasts, arms down at her sides out of frame, both breasts centred and in sharp focus'),
             uses=('breasts', 'nipples')),
        dict(key='nipples', label='Nipples (close-up)', group='nsfw', rating='moderate', required_from='moderate',
             parents=('breasts',), tier=2, mode='crop', region='chest_detail', zoom=True, body=(),
             framing=('a macro close-up of her bare nipples and areolae, breast skin filling the frame, '
                      'nipple texture in sharp focus'),
             uses=('nipples',)),
        dict(key='pubic', label='Pubic area (front, standing)', group='nsfw', rating='explicit', required_from='explicit',
             parents=('body_front',), tier=1, mode='reference', nocrop=True, zoom=True,
             body=('hips', 'thighs', 'birthmarks'),
             framing=('a close-up of her nude pubic area from the front while standing, framed from just below the '
                      'navel to the top of the thighs, pubic mound centred and in sharp focus'),
             uses=('pubic',)),
        dict(key='vulva_closed', label='Vagina, closed', group='nsfw', rating='explicit', required_from='explicit',
             parents=('pubic', 'body_front'), tier=2, mode='reference', zoom=True, body=('thighs',),
             framing=('an explicit macro close-up of her vulva with labia closed, legs apart, the vulva centred and '
                      'filling the frame, inner thighs at the edges'),
             uses=('pubic', 'vulva')),
        dict(key='vulva_open', label='Vagina, open', group='nsfw', rating='explicit', required_from=None,
             parents=('vulva_closed',), tier=2, mode='reference', zoom=True, body=('thighs',),
             framing=('an explicit macro close-up of her vulva with labia spread open by her fingers, the vulva centred '
                      'and filling the frame, only fingertips and inner thighs at the edges'),
             uses=('pubic', 'vulva')),
        dict(key='nude_front', label='Nude full body, front', group='nsfw', rating='moderate', required_from='moderate',
             parents=('body_front', 'breasts', 'nipples', 'pubic', 'vulva_closed'), tier=2, mode='reference',
             framing='a full-body nude photo from head to feet, standing straight facing the camera, arms relaxed at her sides',
             topless=('Topless full body, front', 'a full-body topless photo from head to feet wearing only plain panties, '
                      'standing straight facing the camera, arms relaxed at her sides'),
             uses=('body', 'breasts', 'nipples', 'pubic')),
        dict(key='rear_nude', label='Nude from behind (standing)', group='nsfw', rating='moderate', required_from='explicit',
             parents=('nude_front', 'body_back'), tier=2, mode='reference', framing='a full-body nude photo from behind, standing straight', uses=('body',),
             topless=('Topless from behind (standing)', 'a full-body topless photo from behind wearing only plain panties, standing straight')),
        dict(key='anus_closed', label='Anus, closed (bending forward)', group='nsfw', rating='explicit', required_from=None,
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
    'vulva_look': ('vulva', 'Vagina, closed', ('vulva_closed',),
                   'Her vulva has the same shape as the vulva' + _LOOK_TAIL),
    'vulva_open_look': ('vulva_open', 'Vagina, open', ('vulva_open',),
                        'Her open vulva has the same shape as the open vulva' + _LOOK_TAIL),
    'anus_look': ('anus', 'Anus, closed', ('anus_closed',),
                  'Her anus has the same shape as the anus' + _LOOK_TAIL),
    'anus_open_look': ('anus_open', 'Anus, open', ('anus_open',),
                       'Her open anus has the same shape as the open anus' + _LOOK_TAIL),
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


def for_level(v, level):
    """The view as this level shows it: below Explicit her full-body "nude"
    views are topless in panties."""
    if v and v.get('topless') and _rank(level) < _rank('explicit'):
        return dict(v, label=v['topless'][0], framing=v['topless'][1])
    return v


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


def resolve_reference_images(refs, canon_by_view, image_view):
    """A reference means "that view's approved photo", whatever photo that is
    now. Each stored reference is moved to the current approved photo of its
    view; repeats collapse and a view with no approved photo drops out.
    `canon_by_view` is {view: image id}; `image_view` is {image id: view}."""
    out, seen = [], set()
    for r in refs or []:
        cid = canon_by_view.get(image_view.get(r.get('image_id')))
        if cid and cid not in seen:
            seen.add(cid)
            out.append({'image_id': cid, 'weight': r.get('weight') or 1.0})
    return out


def level_parents(v, level=None):
    """The parents of a view that exist at `level`. The nude is built from the
    pubic and vagina photos only where those are shown; at Topless it is not."""
    ps = tuple(v['parents'])
    if level is None:
        return ps
    return tuple(p for p in ps if (view(p) or {}).get('rating') is None or _rank(view(p)['rating']) <= _rank(level))


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
        'views': [dict(for_level(v, level), parents=list(level_parents(v, level)), uses=list(v['uses']), traits=traits(v['key'], body_type),
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
        if g not in groups:
            continue
        # She wears light makeup unless the creator says otherwise; a bare face
        # is what the unretouched photo look drifts to by itself.
        val = (sheet or {}).get(k) or (DEFAULT_MAKEUP if k == 'makeup' else None)
        if not val:
            continue
        frag = dict(opts).get(val, '')
        if k == 'hair_colour' and val == 'Custom':
            frag = hair_words(sheet.get('hair_colour_hex')) + ' hair' if sheet.get('hair_colour_hex') else ''
        if k == 'pubic_colour' and val == 'Matches hair':
            frag = 'pubic hair matching her hair colour'
        if k == 'pubic_density' and frag:
            # Density describes the style's hair; alone, "sparse pubic hair"
            # outweighs a landing strip and reads as shaved.
            style = (sheet or {}).get('pubic_style')
            if style == 'Shaved':
                continue
            frag = (PUBIC_DENSITY_WORDS.get(val, frag) + (' in the strip' if style == 'Landing strip' else '')
                    if style else f'{frag} pubic hair')
        if frag and frag not in out:
            out.append(frag)
    return out


PUBIC_DENSITY_WORDS = {'Sparse': 'the pubic hair is fine and thin', 'Medium': 'the pubic hair is of medium thickness',
                       'Dense': 'the pubic hair is thick and full'}


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


BLEND_KEEP = ('ethnicity', 'apparent_age', 'hair_colour', 'hair_texture', 'makeup')
DEFAULT_MAKEUP = 'Natural glam'
FACE_MODES = ('build', 'blend')
BODY_MATCH_KEEP = ('height', 'birthmarks', 'nails')
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
                      match=False, look=False, level='explicit', ref_views=()):
    v = for_level(view(key, body_type), level)
    if not v:
        raise CharacterError('Unknown view.')
    if '{outfit}' in v['framing']:
        v = dict(v, framing=v['framing'].replace('{outfit}', outfit_text(sheet, outfit)))
    uses = tuple(v['uses']) + (() if 'face' in v['uses'] else ('body',))
    # What may be said follows the character's level, not the view's rating: the
    # nude is rated Topless but at Explicit it shows her pubic hair too.
    groups = tuple(g for g in uses if _rank(GROUP_LEVEL[g]) <= _rank(level))
    keep = set(traits(key, body_type))
    kept = {k: x for k, x in (sheet or {}).items() if k in keep}
    if 'hair_colour' in kept and (sheet or {}).get('hair_colour_hex'):
        kept['hair_colour_hex'] = sheet['hair_colour_hex']
    frags = _fragments(kept, groups, body_type)
    if ('body' in uses and 'breasts' not in groups and not v.get('zoom') and (sheet or {}).get('cup')
            and _rank(level) >= _rank('moderate')):
        # Cup size is a breasts-group word, but a clothed full-body photo has to
        # show it too or every later nude is generated from the wrong bust.
        frags += _fragments({'cup': sheet['cup']}, ('breasts',), body_type)
    if 'face' not in v['uses'] and (sheet or {}).get('skin_tone'):
        # Skin tone alone: the face group would also add default makeup,
        # which pulls a face into a body close-up.
        tone = dict(features(body_type)['skin_tone'][2]).get(sheet['skin_tone'])
        frags = ([tone] if tone else []) + frags
    detail = ', '.join(frags)
    strength = STRENGTH[mode] if strength is None else strength
    if mode == 'crop':
        # Seedream refuses a denoise strength, so the crop is the first
        # reference and the strength says in words how far it may move.
        touch = ('only sharpen detail and skin texture, change nothing else' if strength < 0.4
                 else 'refine detail, keeping pose, skin tone and lighting' if strength < 0.7
                 else 'redraw the detail freely, keeping the same body and lighting')
        if 'pubic' in groups and (sheet or {}).get('pubic_style'):
            # The crop's parent was often made before the style was picked, so
            # copying it would copy the wrong hair.
            touch = ('keep the pose, skin, lighting and proportions of that crop, but redraw her pubic '
                     'hair exactly as described below')
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
        if v.get('zoom') and 'face' not in v['uses']:
            # "The same woman" makes the model copy the face it sees in the
            # full-body reference into the close-up; only her skin carries over.
            lead = ('photorealistic close-up taking only the skin tone and body of the woman in the '
                    'reference images, ' + hold + ', now as ' + v['framing'] + '.')
        else:
            lead = ('photorealistic photo of the exact same woman as the reference images, '
                    + hold + ', now as ' + v['framing'] + '.')
    else:
        lead = f"photorealistic photo of a woman, {v['framing']}."
    body = f' Her features: {detail}.' if detail else ''
    text = look and next((t for _, _, vs, t in LOOKS.values() if key in vs), '')
    if text:
        body += ' ' + text
    need, at, clause = REF_CLAUSES.get(key, ((), 'explicit', ''))
    if clause and _rank(level) >= _rank(at) and all(k in ref_views for k in need):
        body += ' ' + clause
    zoom = (' Zoomed in: the subject fills the whole frame; no face, no full body, nothing '
            'beyond the subject in shot.' + ('' if 'face' in v['uses'] else
            ' Her face, head and hair are not in the picture at all.')) if v.get('zoom') else ''
    return (lead + zoom + body + ' ' + STUDIO + ' ' + adult_clause(age)).strip()


# What a reference photo is for, said in the prompt only when that photo is one
# of the references actually sent. Keyed by view: (views that must be sent, level, text).
REF_CLAUSES = {
    'nude_front': (('pubic', 'vulva_closed'), 'explicit',
                   'Her pubic area and vulva match the close-up reference images exactly.'),
    'vulva_closed': (('pubic',), 'explicit',
                     'Her pubic hair and skin match the pubic-area close-up reference image exactly.'),
    'vulva_open': (('vulva_closed',), 'explicit',
                   'It is the same vulva as in the closed-vulva reference image, now opened.'),
}


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


def resolve_all(rows, body_type='female', level=None):
    """{key: display status} for rows keyed by view key. A view whose parent
    has no row is locked. Parents above `level` do not count."""
    out = {}
    for k in topo_order(body_type):
        if k not in rows:
            continue
        ps = [rows.get(p) or {'view_key': p, 'status': 'not_started', 'version': 0}
              for p in level_parents(view(k, body_type), level)]
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
    'chest_detail': {'x': 0.03, 'y': 0.50, 'w': 0.94, 'h': 0.32},
}
CROP_PAD = 0.08


def default_crop(region):
    return dict(REGIONS[region]) if region in REGIONS else {'x': .2, 'y': .2, 'w': .6, 'h': .6}
