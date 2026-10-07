"""Studio option table tests. Run with `python test_studio_options.py`."""
import os
import tempfile

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())

import imagegen as IG  # noqa: E402
import studio_options as SO  # noqa: E402
import studio_examples as EX  # noqa: E402

FAILURES = []


def check(name, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + name + ('' if cond else ' ' + str(detail)))
    if not cond:
        FAILURES.append(name)


def keys(rows):
    return [r['key'] for r in rows]


def flat(groups):
    return [r['key'] for g in groups for r in g['rows']]


v = SO.vocab('sfw')
check('safe shots are the four framings', keys(v['shots']) == ['closeup', 'portrait', 'half', 'full'])
check('safe has no moments or distance', not v['moments'] and not v['distance'])
check('candid/mirror are not shots', not set(SO.RETIRED_SHOTS) & set(keys(v['shots'])))
check('retired shots still build', all(k in IG.SHOT_FRAMING for k in SO.RETIRED_SHOTS))
check('safe skin has no oiled/wet', not {'oiled', 'wet'} & set(keys(v['skins'])))
x = SO.vocab('explicit')
check('explicit offers on-show shots', 'explicit' in keys(x['shots']) and 'closeup' not in keys(x['shots']))
check('explicit offers every moment', set(flat(x['moments'])) == {k for k, (l, _) in IG.SCENES.items() if l != 'sfw'})
check('places cover every safe scene', set(flat(v['places'])) == {k for k, (l, _) in IG.SCENES.items() if l == 'sfw'})
check('tease never sees explicit moments', 'solo-touch' not in flat(SO.vocab('suggestive')['moments']))
for name, table in (('cameras', IG.CAMERAS), ('quality', IG.QUALITY)):
    got = set(flat(v[name])) if name == 'cameras' else set(keys(v[name]))
    check(f'{name} labelled', got == set(table), got ^ set(table))
check('lights labelled', set(flat(v['lights'])) == set(IG.LIGHTING))
check('expressions labelled', set(keys(v['expressions'])) == set(IG.EXPRESSIONS))
check('styles labelled', set(keys(v['styles'])) == set(IG.STYLES))
check('POV is written as POV', any(r['label'] == 'POV selfie' for r in v['styles']))

youth = ('pigtail', 'school', 'teen', 'girl', 'child', 'innocent', 'young')
for t in (IG.ANGLES, IG.POSES, IG.GAZES, IG.HAIR_STYLES, IG.MAKEUPS, IG.SKINS, IG.LENSES, IG.GRADES):
    for k, row in t.items():
        check(f'adult wording {k}', not any(w in (row[0] + row[1]).lower() for w in youth))

check('unknown key reads as auto', IG.pick_option(IG.POSES, 'nope', 'explicit') == 'auto')
check('wet skin refused when safe', IG.pick_option(IG.SKINS, 'wet', 'sfw') == 'auto')
check('wet skin allowed above safe', IG.pick_option(IG.SKINS, 'wet', 'explicit') == 'wet')
p = IG.build_prompt('a woman', 'half', scene='exposed', pose='sitting', angle='above')
check('pose dropped under a scene that poses her', 'She is sitting' not in p)
check('angle reaches the prompt', 'from slightly above' in p)
c = IG.build_prompt('a woman', 'closeup', has_reference=True, makeup='glam')
check('chosen makeup replaces the reference copy', IG.MAKEUP_FROM_REFERENCE not in c and 'glam makeup' in c)
check('baseline negative keeps underage terms', 'underage' in IG.merge_negative(IG.keep_out_text(['text'])))
check('compose_part joins action, camera, pace, end',
      SO.compose_part({'motion': 'she turns', 'cam': 'push', 'pace': 'slow', 'end': 'on the bed'})
      == 'she turns, the camera pushes slowly in, slow, unhurried movement, ending on the bed')
check('every new option has an example prompt', all(
    f'{p}:{k}' in EX.PROMPTS for p, t in (('angle', IG.ANGLES), ('pose', IG.POSES), ('hair', IG.HAIR_STYLES))
    for k, r in t.items() if r[1]))
check('recommended engines', SO.recommended_image_engine('explicit') == 'seedream-4-5'
      and SO.recommended_video_engine('animate', 'explicit') == 'wan-2-2-gv')

print('FAILED: ' + ', '.join(FAILURES) if FAILURES else 'all ok')
raise SystemExit(1 if FAILURES else 0)
