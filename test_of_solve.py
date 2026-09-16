"""The recipe solver, against recipes it is not told."""
import random
import sys
import time
import unittest

import of_solve

PARAM = 'UJXshlDJ2krwaP7pFk6OQeuXZ0OewwPR'
REVISION, SUFFIX = '65232', '6a452ef4'
# A recipe of the published shape, and deliberately not one of the six the app
# already carries: the point is recovering one nobody has.
INDEXES = [3, 3, 7, 9, 9, 9, 11, 12, 14, 15, 16, 18, 19, 21, 22, 22,
           24, 25, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 0, 1]
CONSTANT = -287


def sign_like_onlyfans(path, user_id, stamp, param=PARAM,
                       indexes=INDEXES, constant=CONSTANT):
    digest = of_solve.digest_of(param, {'path': path, 'user_id': user_id,
                                        'time': stamp})
    checksum = abs(sum(digest[i] for i in indexes) + constant)
    return '%s:%s:%x:%s' % (REVISION, digest.decode(), checksum, SUFFIX)


def captures(n, **kw):
    rng = random.Random(20260916)
    out = []
    for i in range(n):
        path = '/api2/v2/chats?limit=%d&offset=%d' % (rng.randrange(10, 60), i)
        stamp = str(1789500000000 + rng.randrange(10 ** 7))
        user_id = str(rng.randrange(1000, 99999))
        out.append({'path': path, 'user_id': user_id, 'time': stamp,
                    'sign': sign_like_onlyfans(path, user_id, stamp, **kw)})
    return out


class ParamTest(unittest.TestCase):
    """One signature settles the param: it carries the digest."""

    def test_the_right_param_fits_one_signature(self):
        self.assertTrue(of_solve.param_fits(PARAM, captures(1)[0]))

    def test_a_wrong_param_does_not(self):
        self.assertFalse(of_solve.param_fits('x' * 32, captures(1)[0]))


class RecipeTest(unittest.TestCase):
    def test_recovers_a_recipe_it_was_never_given(self):
        got = of_solve.solve_recipe(PARAM, captures(60))
        self.assertEqual(sorted(got['checksum_indexes']), sorted(INDEXES))
        self.assertEqual(got['checksum_constant'], CONSTANT)

    def test_the_recovered_recipe_signs_unseen_requests(self):
        rules = of_solve.rules_from(PARAM, captures(60))
        fresh = captures(5)[0]
        digest = of_solve.digest_of(PARAM, fresh)
        total = sum(digest[i] for i in rules['checksum_indexes'])
        self.assertEqual(rules['format'].format(
            digest.decode(), abs(total + rules['checksum_constant'])),
            fresh['sign'])

    def test_the_format_comes_from_the_signatures(self):
        rules = of_solve.rules_from(PARAM, captures(60))
        self.assertEqual(rules['format'], '65232:{}:{:x}:6a452ef4')
        self.assertEqual(rules['revision'], '65232')

    def test_too_few_signatures_solve_nothing(self):
        self.assertEqual(of_solve.solve_recipe(PARAM, captures(20)), {})

    def test_a_wrong_param_solves_nothing(self):
        self.assertEqual(of_solve.solve_recipe('x' * 32, captures(60)), {})

    def test_signatures_from_two_recipes_do_not_average_into_a_third(self):
        """A capture taken either side of a rotation is the realistic mess: the
        wrong answer here is a recipe that reproduces neither."""
        mixed = captures(40) + captures(40, indexes=INDEXES[::-1], constant=11)
        got = of_solve.solve_recipe(PARAM, mixed)
        self.assertEqual(got, {})

    def test_a_changed_shape_is_reported_rather_than_forced(self):
        """A rotation that counts a different number of positions is not this
        recipe with a bad constant, and must not be returned as one."""
        odd = captures(60, indexes=INDEXES[:20], constant=CONSTANT)
        self.assertEqual(of_solve.solve_recipe(PARAM, odd), {})
        self.assertTrue(of_solve.solve_recipe(PARAM, odd, size=20))


class CostTest(unittest.TestCase):
    def test_solving_is_quick_enough_to_run_inside_a_repair(self):
        started = time.time()
        of_solve.solve_recipe(PARAM, captures(60))
        self.assertLess(time.time() - started, 20)


if __name__ == '__main__':
    unittest.main()
