"""Diagnose a signing rotation from a dump, with no network and no sign-in.

Everything that decides whether signing works is arithmetic over data the app
already stores: a signature OnlyFans produced, the rule sets on offer, and the
signatures we have collected. So it can all be answered here, in a second, from
a paste — rather than in a deploy, a sign-in and a wait.

    curl -s 'https://…/api/diag?key=…&export=1' > dump.json
    python3 of_replay.py dump.json

What it reports:

  * which rule set, if any, reproduces the oracle;
  * whether the captured param is the one OnlyFans hashes;
  * what the solver makes of the signatures held;
  * and the split that a logged-out capture cannot show on its own -- whether
    the rules that work for a visitor also work for a signed-in request.
"""
import json
import sys

import of_rules
import of_solve


def _load(path):
    with open(path) as fh:
        dump = json.load(fh)
    # The export is nested under the diagnostic's own envelope; a hand-made
    # file with the same keys at the top is just as good.
    return dump.get('export') or dump


def _signed_out(s):
    return str(s.get('user_id') or '0') in ('', '0')


def _report_rules(dump, sample):
    print('== rule sets vs the signature OnlyFans produced')
    sets = dict(dump.get('sources') or {})
    if dump.get('override'):
        sets['pasted in the console'] = dump['override']
    if dump.get('rules'):
        sets.setdefault('in use', dump['rules'])
    if not sets:
        print('   (none in the dump)')
        return
    for name, candidate in sets.items():
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except ValueError:
                print(f'   {name}: not JSON')
                continue
        verdict = of_rules.verify(sample, candidate)
        print('   %-24s rev %-8s %s'
              % (name, of_rules._revision_of(candidate) or '?',
                 'matches' if verdict is True else
                 'stale' if verdict is False else 'unknown'))


def _report_signatures(signatures, sample):
    print('\n== signatures held')
    revisions = {}
    for s in signatures:
        revisions.setdefault(str(s.get('sign') or '').split(':')[0], []).append(s)
    for revision, held in sorted(revisions.items()):
        out = [s for s in held if _signed_out(s)]
        print('   revision %-8s %3d total, %d logged out, %d signed in'
              % (revision or '?', len(held), len(out), len(held) - len(out)))
    current = str(sample.get('sign') or '').split(':')[0] if sample else ''
    if current:
        print('   OnlyFans is signing with revision %s' % current)
    return revisions


def _report_solve(param, signatures, label):
    if not param:
        return {}
    fits = [s for s in signatures if of_solve.param_fits(param, s)]
    print('   %-12s %d of %d signatures are hashed with this param'
          % (label, len(fits), len(signatures)))
    need = of_solve.DIGEST_LEN + 2
    if len(fits) < need:
        print('   %-12s not enough to solve the recipe (%d of %d)'
              % ('', len(fits), need))
        return {}
    solved = of_solve.rules_from(param, fits)
    if not solved:
        print('   %-12s no recipe reproduces them — the signature is not built '
              'the way we think' % '')
        return {}
    print('   %-12s solved revision %s' % ('', solved.get('revision')))
    return solved


def main(path):
    dump = _load(path)
    sample = dump.get('sample') or {}
    signatures = [s for s in (dump.get('signatures') or []) if s.get('sign')]
    if not (sample or signatures):
        print('nothing to replay: the dump has no sample and no signatures')
        return 2

    _report_rules(dump, sample)
    _report_signatures(signatures, sample)

    param = (dump.get('param')
             or (dump.get('override') or {}).get('static_param')
             or (dump.get('rules') or {}).get('static_param') or '')
    print('\n== solving from the signatures held')
    if not param:
        print('   no param in the dump to try')
        return 0

    out = [s for s in signatures if _signed_out(s)]
    inn = [s for s in signatures if not _signed_out(s)]
    visitor = _report_solve(param, out, 'logged out:')
    hers = _report_solve(param, inn, 'signed in:') if inn else {}

    print('\n== verdict')
    if not inn:
        print('   no signature captured while signed in, so nothing here can '
              'say whether the rules that work for a visitor work for her.\n'
              '   Connect the account once with the window open and export '
              'again.')
    elif visitor and hers and visitor.get('checksum_indexes') == hers.get('checksum_indexes') \
            and visitor.get('checksum_constant') == hers.get('checksum_constant'):
        print('   the same recipe signs both, so signing is not what OnlyFans '
              'is refusing on her account.')
    elif hers:
        print('   a signed-in request is signed with a different recipe than a '
              'visitor request. Adopt the signed-in one — that is the bug.')
    else:
        # The rules already verify against a logged-out oracle at this point,
        # so the two cannot both be describing the same arithmetic.
        print('   the rules reproduce a visitor signature and fail her own, and '
              'no recipe fits hers. Collect more signed-in signatures.')
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
