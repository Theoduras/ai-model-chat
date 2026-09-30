"""python test_wishes.py"""
import wishes as W

assert W.looks_like_wish('I wish I saw you naked')
assert W.looks_like_wish('send me a pic in that red dress')
assert not W.looks_like_wish('how was your day?')

ok = W.parse('```json\n{"wish": true, "scene": "red dress on a beach", '
             '"explicit": false, "caption": "made this for you"}\n```')
assert ok == {'scene': 'red dress on a beach', 'explicit': False, 'outfit': '',
              'caption': 'made this for you'}, ok
assert W.parse('{"wish": false, "scene": "x"}') is None
assert W.parse('{"wish": true, "scene": ""}') is None
assert W.parse('not json') is None
assert W.parse('{"wish": "yes", "scene": "x"}') is None

assert W.blocked('dress up as a schoolgirl') == 'schoolgirl'
assert W.blocked('you and your sister') == 'sister'
assert W.blocked('in a red dress', 'on a beach') == ''
assert W.blocked('kidding, show me the bikini') == ''

assert W.price({'price_sfw': 500, 'price_nsfw': 1500}, False, 300) == 500
assert W.price({'price_sfw': 500, 'price_nsfw': 1500}, True, 300) == 1500
assert W.price({}, True, 300) == 300

print('test_wishes: ok')
