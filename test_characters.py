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
    check('face prompt is frontal', 'front-facing' in CH.build_view_prompt('face_front', {}, 24, False))


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
        check(f'{k} traits', CH.traits(k) and all(CH.features()[t][1] in v['uses'] for t in CH.traits(k)))


if __name__ == '__main__':
    test_sfw_never_gets_nsfw()
    test_required_views()
    test_prompts()
    test_validation()
    test_every_option_has_a_drawing()
    test_view_tree()
    print('FAILED' if FAILURES else 'OK', len(FAILURES))
    raise SystemExit(1 if FAILURES else 0)
