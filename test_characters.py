"""Character catalogue rules. Run: python test_characters.py"""
import characters as CH
from imagegen import SHOT_LEVEL, SCENES

FAILURES = []


def check(name, cond):
    if not cond:
        FAILURES.append(name)
        print('FAIL', name)


NSFW_GROUPS = ('breasts', 'nipples', 'pubic', 'vulva', 'anus')
FULL_SHEET = {k: opts[0][0] for k, (_, _, opts) in CH.features().items()}
# A full sheet that stays under the youth-combination limit.
for k in ('height', 'build', 'shoulders', 'hips', 'bust', 'face_shape', 'nose', 'perkiness'):
    FULL_SHEET[k] = CH.features()[k][2][1][0]


def test_sfw_never_gets_nsfw():
    intimate = CH._fragments(FULL_SHEET, NSFW_GROUPS)
    check('sheet has intimate words to leak', bool(intimate))
    for shot, lvl in SHOT_LEVEL.items():
        for scene in [''] + list(SCENES):
            level = CH.job_level(shot, scene)
            keys = CH.views_for_job(shot, scene, level)
            for k in keys:
                check(f'{shot}/{scene} view {k} within {level}',
                      CH._rank(CH.view(k)['rating']) <= CH._rank(level))
            if level == 'sfw':
                text = CH.content_clause(FULL_SHEET, level)
                check(f'{shot}/{scene} sfw text has no intimate words',
                      not any(w in text for w in intimate))
    check('sfw forced even if scene asks', all(
        CH.view(k)['rating'] == 'sfw' for k in CH.views_for_job('portrait', 'bent-over', 'sfw')))


def test_required_views():
    check('sfw requires face and body',
          CH.required_views('sfw') == ['face_front', 'body_front'])
    top = CH.required_views('moderate')
    check('topless adds nude full body', 'nude_front' in top)
    check('topless adds breasts and nipples', {'breasts', 'nipples'} <= set(top))
    check('topless has nothing explicit', not {'pubic', 'vulva_closed', 'anus_closed'} & set(top))
    exp = set(CH.required_views('explicit'))
    check('explicit requires pubic, vulva, anus closed',
          {'pubic', 'vulva_closed', 'anus_closed'} <= exp)
    check('open views optional', not {'vulva_open', 'anus_open'} & exp)
    check('missing views', CH.missing_views('sfw', ['face_front']) == ['body_front'])
    check('catalogue cut to level', all(
        CH._rank(v['rating']) <= CH._rank('moderate') for v in CH.catalogue('moderate')['views']))
    check('catalogue features cut', not any(
        f['group'] in ('pubic', 'vulva', 'anus') for f in CH.catalogue('moderate')['features']))


def test_prompts():
    for v in CH.views():
        p = CH.build_view_prompt(v['key'], FULL_SHEET, 31, True)
        check(f"{v['key']} adult clause", 'Fictional adult woman, 31 years old' in p)
        if v['rating'] != 'explicit':
            check(f"{v['key']} no explicit words",
                  not any(w in p for w in CH._fragments(FULL_SHEET, ('pubic', 'vulva', 'anus'))))
    shapes = {'body_shape': 'Pear', 'glute_shape': 'Heart-shaped', 'face_shape': 'Triangle',
              'ethnicity': 'East Asian', 'apparent_age': '30s'}
    text = CH.describe(shapes, 'sfw')
    check('shape fragments reach a safe-work prompt',
          all(dict(CH.features()[k][2])[v] in text for k, v in shapes.items()))
    for key in ('body_front', 'body_side', 'body_back'):
        check(f'{key} defaults to a nude-coloured bodysuit', 'nude-coloured bodysuit' in CH.build_view_prompt(key, {}, 24, True))
        for o in CH.OUTFITS:
            p = CH.build_view_prompt(key, {'view_outfits': [o]}, 24, True)
            check(f'{key} wears {o}', CH.outfit_text({}, o) in p and '{' not in p)
    two = {'view_outfits': ['Casual', 'Fitted dress'], 'outfit_colours': {'Casual': 'Black', 'Fitted dress': 'Red'}}
    check('named outfit picked', 'red knee-length dress' in CH.build_view_prompt('body_front', two, 24, True, outfit='Fitted dress'))
    check('first outfit by default', 'black T-shirt' in CH.build_view_prompt('body_front', two, 24, True))
    check('old single outfit still reads', CH.outfits({'view_outfit': 'Activewear'}) == ['Activewear'])
    check('outfit never reaches a content prompt',
          all(CH.describe(two, lvl) == '' for lvl in ('sfw', 'explicit')))
    blend = CH.build_view_prompt('face_front', {}, 24, True, blend=True)
    check('blend asks for a new face', 'new, distinct face' in blend and 'exact same woman' not in blend)
    check('blend only on the face', 'exact same woman' in CH.build_view_prompt('body_front', {}, 24, True, blend=True))
    check('body stands perfectly straight', 'perfectly straight' in CH.build_view_prompt('body_front', {}, 24, True))
    custom = {'hair_colour': 'Custom', 'hair_colour_hex': '#e8a0bf'}
    check('custom hair colour reaches the prompt as words', 'pastel pink hair' in CH.describe(custom, 'sfw'))
    check('custom hair colour reaches the face view', 'pastel pink hair' in CH.build_view_prompt('face_front', custom, 24, True))
    check('hair words', [CH.hair_words(h) for h in ('#000000', '#c0c0c0', '#a8391c')] == ['jet black', 'silver grey', 'dark auburn'])
    matched = CH.build_view_prompt('body_front', {}, 24, True, match=True)
    check('body match asks for a similar body', 'similar to them, not a copy' in matched and 'ignore their faces' in matched)
    check('body match only on the front body', 'not a copy' not in CH.build_view_prompt('body_side', {}, 24, True, match=True))
    up = 'upload:' + 'a' * 32
    check('uploaded outfit points at the last reference', 'last reference image' in CH.build_view_prompt('body_front', {'view_outfits': [up]}, 24, True, outfit=up))
    check('face prompt is frontal', 'front-facing' in CH.build_view_prompt('face_front', {}, 24, False))
    close = CH.build_view_prompt('vulva_open', FULL_SHEET, 31, True)
    far = ('height', 'bust', 'shoulders', 'body_shape', 'nails')
    check('close-up drops body features it cannot show',
          not any(w in close for w in CH._fragments({k: FULL_SHEET[k] for k in far if k in FULL_SHEET}, ('body',))))
    check('close-up keeps its own features',
          all(w in close for w in CH._fragments(FULL_SHEET, ('vulva',))))
    check('full body keeps every body feature',
          all(w in CH.build_view_prompt('body_front', FULL_SHEET, 31, True)
              for w in CH._fragments(FULL_SHEET, ('body',))))
    check('nipples carry no body features',
          not any(CH.features()[k][1] == 'body' for k in CH.traits('nipples')))
    check('hands keep nails', 'nails' in CH.traits('hands'))
    for k in ('anus_closed', 'anus_open'):
        p = CH.build_view_prompt(k, FULL_SHEET, 31, True)
        check(f'{k} shows the vulva', 'vulva just below' in p
              and all(w in p for w in CH._fragments(FULL_SHEET, ('vulva', 'anus'))))
        check(f'{k} drops far body features',
              not any(w in p for w in CH._fragments({x: FULL_SHEET[x] for x in far}, ('body',))))


def test_validation():
    def refused(data):
        try:
            CH.validate(data)
        except CH.CharacterError:
            return True
        return False
    check('under 18 refused', refused({'age': 17}))
    check('18 accepted', not refused({'age': 18}))
    check('unknown level refused', refused({'age': 25, 'nsfw_level': 'extreme'}))
    check('unknown option refused', refused({'age': 25, 'sheet': {'cup': 'AA'}}))
    check('youth term in notes refused', refused({'age': 25, 'notes': 'looks like a teen'}))
    check('youth combo blocked', refused({'age': 25, 'sheet': {
        'height': 'Under 155 cm', 'cup': 'A', 'pubic_style': 'Shaved'}}))
    clean, warn = CH.validate({'age': 25, 'sheet': {'height': 'Under 155 cm', 'cup': 'A'}})
    check('two leaning choices warn', bool(warn))
    clean, _ = CH.validate({'age': 25, 'sheet': {'face_shape': 'Long', 'glutes': 'Very full'}})
    check('renamed options still validate, retired features drop', clean['sheet'] == {'face_shape': 'Rectangle'})
    clean, warn = CH.validate({'age': 25, 'sheet': {'apparent_age': '18–21'}})
    check('youngest look warns on its own', bool(warn))
    check('youngest look plus one lean is blocked', refused({'age': 25, 'sheet': {'apparent_age': '18–21', 'bust': 'Small'}}))
    check('no teen look offered', not any('teen' in o.lower() or 'teen' in fr for o, fr in CH.features()['apparent_age'][2]))
    clean, _ = CH.validate({'age': 25, 'sheet': {'view_outfits': ['Casual', 'Casual', 'Bodysuit'],
                                                 'outfit_colours': {'Casual': 'Navy', 'Activewear': 'Red'}}})
    check('outfits deduped, colours cut to picked outfits',
          clean['sheet'] == {'view_outfits': ['Casual', 'Bodysuit'], 'outfit_colours': {'Casual': 'Navy'}})
    check('unknown outfit refused', refused({'age': 25, 'sheet': {'view_outfits': ['Ballgown']}}))
    check('unknown colour refused', refused({'age': 25, 'sheet': {'view_outfits': ['Casual'], 'outfit_colours': {'Casual': 'Plaid'}}}))
    clean, _ = CH.validate({'age': 25, 'sheet': {'view_outfit': 'Activewear'}})
    check('old single outfit validates', clean['sheet'] == {'view_outfits': ['Activewear']})
    check('custom hair needs a colour', refused({'age': 25, 'sheet': {'hair_colour': 'Custom', 'hair_colour_hex': 'pink'}}))
    clean, _ = CH.validate({'age': 25, 'sheet': {'hair_colour': 'Custom', 'hair_colour_hex': '#AABBCC'}})
    check('custom hair stored', clean['sheet'] == {'hair_colour': 'Custom', 'hair_colour_hex': '#aabbcc'})
    clean, _ = CH.validate({'age': 25, 'sheet': {'face_mode': 'blend', 'nose': 'Roman', 'apparent_age': '30s', 'build': 'Athletic'}})
    check('blend drops drawn face features', clean['sheet'] == {'face_mode': 'blend', 'apparent_age': '30s', 'build': 'Athletic'})
    check('face mode never reaches a prompt', CH.describe(clean['sheet'], 'sfw') == CH.describe({'apparent_age': '30s', 'build': 'Athletic'}, 'sfw'))
    check('unknown face mode refused', refused({'age': 25, 'sheet': {'face_mode': 'morph'}}))
    clean, _ = CH.validate({'age': 25, 'sheet': {'body_mode': 'match', 'build': 'Curvy', 'hips': 'Wide', 'height': 'Over 175 cm',
                                                 'nails': 'French tips', 'nose': 'Roman'}})
    check('body match keeps height, marks and face', clean['sheet'] == {'body_mode': 'match', 'height': 'Over 175 cm',
                                                                        'nails': 'French tips', 'nose': 'Roman'})
    check('body mode never reaches a prompt', 'match' not in CH.describe(clean['sheet'], 'sfw'))
    check('unknown body mode refused', refused({'age': 25, 'sheet': {'body_mode': 'morph'}}))
    clean, _ = CH.validate({'age': 25, 'sheet': {'view_outfits': ['Casual', 'upload:' + 'b' * 32]}})
    check('uploaded outfit validates', clean['sheet']['view_outfits'] == ['Casual', 'upload:' + 'b' * 32])
    check('malformed uploaded outfit refused', refused({'age': 25, 'sheet': {'view_outfits': ['upload:../x']}}))
    clean, warn = CH.validate({'age': 25, 'notes': 'likes red', 'banned': ['red']})
    check('banned terms struck', 'red' not in clean['notes'])


def test_every_option_has_a_drawing():
    import json, os, shutil, subprocess
    node = shutil.which('node')
    if not node:
        print('skip drawings: node not installed')
        return
    here = os.path.dirname(os.path.abspath(__file__))
    feats = {k: [o for o, _ in v[2]] for k, v in CH.features().items()}
    script = ("const V=require(process.argv[1]),F=JSON.parse(process.argv[2]);"
              "const m=[];for(const[k,os]of Object.entries(F)){if(V.TEXT_ONLY.includes(k))continue;"
              "for(const o of os)if(!V.render(k,o,{}).startsWith('<svg'))m.push(k+'/'+o)}"
              "console.log(JSON.stringify(m))")
    out = subprocess.run([node, '-e', script, os.path.join(here, 'js', 'character-visuals.js'),
                          json.dumps(feats)], capture_output=True, text=True)
    missing = json.loads(out.stdout or '["node failed: ' + out.stderr[:80].replace('"', "'") + '"]')
    check('every option has a drawing: ' + ', '.join(missing), not missing)


def test_view_tree():
    keys = {v['key'] for v in CH.views()}
    order = CH.topo_order()
    check('topo order covers every view once', sorted(order) == sorted(keys))
    for v in CH.views():
        k = v['key']
        for p in v['parents']:
            check(f'{k} parent {p} exists', p in keys)
            check(f'{k} parent {p} comes first', order.index(p) < order.index(k))
            check(f'{k} parent {p} rated no higher', CH._rank(CH.view(p)['rating']) <= CH._rank(v['rating']))
            if v['required_from']:
                pr = CH.view(p)['required_from']
                check(f'{k} parent {p} required no later',
                      pr and CH._rank(pr) <= CH._rank(v['required_from']))
        ptiers = [CH.view(p)['tier'] for p in v['parents']]
        check(f'{k} tier', v['tier'] in (0, 1, 2) and all(t <= v['tier'] for t in ptiers))
        check(f'{k} mode', v['mode'] in CH.STRENGTH)
        check(f'{k} crop has a region', v['mode'] != 'crop' or bool(v.get('region')))
        # Non-face views also inherit the body profile and skin tone.
        extra = set() if 'face' in v['uses'] else {'body'}
        check(f'{k} traits', CH.traits(k) and all(CH.features()[t][1] in set(v['uses']) | extra or t == 'skin_tone'
                                                  for t in CH.traits(k)))


def _row(k, status='not_started', version=0, pv=None):
    return {'view_key': k, 'status': status, 'version': version, 'parent_versions': pv or {}}


def test_resolver():
    rows = {k: _row(k) for k in CH.topo_order()}
    st = CH.resolve_all(rows)
    check('root starts not_started', st['face_front'] == 'not_started')
    check('child of unapproved is locked', st['body_front'] == 'locked')
    check('lock chain reaches grandchild', st['nipples'] == 'locked' and st['nude_front'] == 'locked')
    rows['face_front'] = _row('face_front', 'approved', 1)
    st = CH.resolve_all(rows)
    check('approval unlocks child', st['body_front'] == 'not_started')
    check('grandchild still locked', st['nude_front'] == 'locked')
    rows['body_front'] = _row('body_front', 'approved', 1, {'face_front': 1})
    rows['face_profile'] = _row('face_profile', 'approved', 1, {'body_front': 1})
    check('approved stays approved', CH.resolve_all(rows)['face_profile'] == 'approved')
    check('rear_nude needs both parents', CH.resolve_all(rows)['rear_nude'] == 'locked')
    rows['body_front']['version'] = 2
    st = CH.resolve_all(rows)
    check('re-approved parent outdates child', st['face_profile'] == 'outdated')
    check('not-yet-generated child is not outdated', st['hands'] == 'not_started')
    branch = CH.outdated_branch(rows)
    check('branch holds outdated', 'face_profile' in branch and 'face_front' not in branch)
def test_snapshot_views():
    every = {v['key']: {'path': v['key'], 'mime': 'image/jpeg'} for v in CH.views()}
    snap = {'body_type': 'female', 'views': every}
    for shot in SHOT_LEVEL:
        for scene in [''] + list(SCENES):
            level = CH.job_level(shot, scene)
            for k in CH.snapshot_views(snap, shot, scene):
                check(f'snapshot {shot}/{scene} {k} within {level}',
                      CH._rank(CH.view(k)['rating']) <= CH._rank(level))
    check('clip gets only safe-work views', all(
        CH.view(k)['rating'] == 'sfw' for k in CH.snapshot_views(snap, None, None)))
    check('face-only is face views', all(
        CH.view(k)['group'] == 'face' for k in CH.snapshot_views(snap, None, None, True)))
    partial = {'body_type': 'female', 'views': {'face_front': every['face_front']}}
    check('reel sends face then full body', CH.reel_views(snap) == ['face_front', 'body_front'])
    check('reel skips an unapproved view',
          CH.reel_views({'views': {'body_front': 1}}) == ['body_front'])
    check('partial sends only approved views',
          CH.snapshot_views(partial, 'portrait', '') == ['face_front'])


if __name__ == '__main__':
    test_sfw_never_gets_nsfw()
    test_required_views()
    test_prompts()
    test_validation()
    test_every_option_has_a_drawing()
    test_view_tree()
    test_resolver()
    test_snapshot_views()
    print('FAILED' if FAILURES else 'OK', len(FAILURES))
    raise SystemExit(1 if FAILURES else 0)
