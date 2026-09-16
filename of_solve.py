"""Recovering a signing recipe OnlyFans rotated to and nobody published.

A rotation usually moves `static_param` alone, and `of_rules.solve` recovers
that from the page. 65232 moved the checksum recipe too, which is not written
anywhere in the bundle and cannot be read off a single signature -- so it is
solved for instead.

The arithmetic is linear, which is what makes this tractable. A signature is

    rev : sha1(param\\n time \\n path \\n user_id) : checksum : suffix
    checksum = abs(sum(digest[i] for i in indexes) + constant)

`indexes` is 32 positions into the 40-character hex digest, repeats allowed, so
only how many times each position is counted matters. That is 40 unknown counts
plus the constant: 41 unknowns, and every captured signature is one equation in
them. Capture more signatures than unknowns and the recipe falls out of exact
Gaussian elimination -- no search, no guessing.

The digest in the signature is the same sha1 we can compute, so one signature is
enough to say whether a candidate param is the right one. Only the checksum
needs solving.
"""
import hashlib
import logging
from fractions import Fraction

logger = logging.getLogger(__name__)

DIGEST_LEN = 40
# Every published set counts 32 positions. It is an argument rather than a
# constant because a rotation that changes it would otherwise look like an
# unsolvable system rather than the one thing to try next.
DEFAULT_SIZE = 32


def digest_of(param, sample):
    """The sha1 OnlyFans hashes for this request, as the hex bytes it sums."""
    msg = '\n'.join([param, str(sample.get('time') or ''),
                     sample.get('path') or '', str(sample.get('user_id') or '0')])
    return hashlib.sha1(msg.encode('utf-8')).hexdigest().encode('ascii')


def parts_of(sample):
    """(digest, checksum) as the signature carries them, or (None, None)."""
    fields = str(sample.get('sign') or '').split(':')
    if len(fields) < 4:
        return None, None
    try:
        return fields[1].encode('ascii'), int(fields[2], 16)
    except (ValueError, UnicodeEncodeError):
        return None, None


def param_fits(param, sample):
    """Is this the param OnlyFans hashed? The signature carries the digest, so
    this is a fact about one signature rather than a guess needing many."""
    theirs, _ = parts_of(sample)
    return bool(theirs) and digest_of(param, sample) == theirs


def _solve_exact(rows, targets):
    """Least-unknowns solution of an exact integer system, or None.

    Fractions rather than floats: the answer is a vector of small integers, and
    a float pivot turns one of them into 31.999999999999996.
    """
    width = len(rows[0])
    matrix = [[Fraction(v) for v in row] + [Fraction(t)]
              for row, t in zip(rows, targets)]
    where = []
    pivot_row = 0
    for col in range(width):
        pick = next((r for r in range(pivot_row, len(matrix)) if matrix[r][col]), None)
        if pick is None:
            continue
        matrix[pivot_row], matrix[pick] = matrix[pick], matrix[pivot_row]
        lead = matrix[pivot_row][col]
        matrix[pivot_row] = [v / lead for v in matrix[pivot_row]]
        for r in range(len(matrix)):
            if r != pivot_row and matrix[r][col]:
                factor = matrix[r][col]
                matrix[r] = [a - factor * b
                             for a, b in zip(matrix[r], matrix[pivot_row])]
        where.append(col)
        pivot_row += 1
        if pivot_row == len(matrix):
            break
    # An inconsistent system -- a row of zeroes against a non-zero target -- is
    # the honest answer for a wrong param or a changed shape, not a failure to
    # try hard enough.
    for r in range(pivot_row, len(matrix)):
        if matrix[r][-1]:
            return None
    if len(where) < width:
        return None
    out = [Fraction(0)] * width
    for r, col in enumerate(where):
        out[col] = matrix[r][-1]
    return out


def solve_recipe(param, samples, size=DEFAULT_SIZE):
    """The checksum recipe behind these signatures, or {}.

    `samples` are captured signatures: {path, time, user_id, sign}. Returns
    {'checksum_indexes', 'checksum_constant'} only when the recovered recipe
    reproduces every signature given, including the ones elimination did not
    need -- an exactly-determined system always satisfies its own equations,
    so the spare ones are the whole test.
    """
    usable = []
    for s in samples:
        theirs, checksum = parts_of(s)
        if theirs is None or checksum is None:
            continue
        if digest_of(param, s) != theirs:
            # Wrong param, or a sample from before the rotation. Either way its
            # equation describes different arithmetic and would poison the rest.
            continue
        usable.append((theirs, checksum))
    if len(usable) < DIGEST_LEN + 2:
        logger.info('not enough signatures to solve the recipe: %d of %d',
                    len(usable), DIGEST_LEN + 2)
        return {}

    rows = [[float(0)] * (DIGEST_LEN + 1) for _ in usable]
    targets = []
    for n, (digest, checksum) in enumerate(usable):
        for i in range(DIGEST_LEN):
            rows[n][i] = digest[i]
        rows[n][DIGEST_LEN] = 1          # the constant's own column
        targets.append(checksum)

    answer = _solve_exact(rows, targets)
    if answer is None:
        return {}
    counts, constant = answer[:DIGEST_LEN], answer[DIGEST_LEN]
    if any(c.denominator != 1 or c < 0 for c in counts) or constant.denominator != 1:
        return {}
    counts = [int(c) for c in counts]
    constant = int(constant)
    if size and sum(counts) != size:
        logger.info('solved recipe counts %d positions, expected %d',
                    sum(counts), size)
        return {}
    indexes = [i for i, c in enumerate(counts) for _ in range(c)]
    recipe = {'checksum_indexes': indexes, 'checksum_constant': constant}
    if not all(_reproduces(recipe, digest, checksum)
               for digest, checksum in usable):
        return {}
    return recipe


def _reproduces(recipe, digest, checksum):
    total = sum(digest[i] for i in recipe['checksum_indexes'])
    return abs(total + recipe['checksum_constant']) == checksum


def rules_from(param, samples, base=None, size=DEFAULT_SIZE):
    """A whole rule set built from a recovered recipe, or {}.

    The format comes from the signatures themselves -- they carry the revision
    and the suffix in plain view -- so nothing here depends on a source having
    published anything about this rotation.
    """
    recipe = solve_recipe(param, samples, size=size)
    if not recipe:
        return {}
    fmt = ''
    for s in samples:
        fields = str(s.get('sign') or '').split(':')
        if len(fields) >= 4:
            fmt = '%s:{}:{:x}:%s' % (fields[0], fields[3])
            break
    if not fmt:
        return {}
    out = dict(base or {})
    out.pop('checksum_constants', None)
    out.update(recipe)
    out['static_param'] = param
    out['format'] = fmt
    out['revision'] = fmt.split(':')[0]
    return out
