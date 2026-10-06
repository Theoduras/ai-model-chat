"""Retention and lapsed-account tests. Run with `python test_account_lifecycle.py`."""
import os
import sys
import tempfile
import zipfile
from datetime import timedelta

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
os.environ['LAPSE_PURGE'] = '1'

import account_lifecycle as AL  # noqa: E402
import db  # noqa: E402

FAILURES = []


def check(name, cond):
    print(('  ok   ' if cond else '  FAIL ') + name)
    if not cond:
        FAILURES.append(name)


def make_user(s, email, lapsed_days):
    u = db.User(email=email, password_hash='x', role='user', status='expired',
                expires_at=AL._now() - timedelta(days=lapsed_days))
    s.add(u)
    s.flush()
    s.add(db.Workspace(id=u.id, owner_id=u.id, name=email))
    slug = 'p-' + email.split('@')[0]
    s.add(db.SavedPersona(slug=slug, name=slug, config_json='{}', prompt='hi', owner_id=u.id))
    s.add(db.PersonaMedia(slug=slug, image_data='data:image/png;base64,aGk=', mime='image/png'))
    c = db.Conversation(persona=slug, client_id='fan')
    c.messages.append(db.Message(role='user', content='hello'))
    s.add(c)
    s.commit()
    return u, slug


def test_sweep_and_export():
    print('lapse sweep')
    db.init_db()
    s = db.SessionLocal()
    _, keep = make_user(s, 'day13@x.com', 13)
    _, gone = make_user(s, 'day15@x.com', 15)
    sent = []
    AL.sweep(s, notify=lambda u, left: sent.append((u.email, left)) or True)
    count = lambda m, slug: s.query(m).filter(m.slug == slug).count()  # noqa: E731
    check('day 13 kept', count(db.PersonaMedia, keep) == 1)
    check('day 15 media deleted', count(db.PersonaMedia, gone) == 0)
    check('day 15 history deleted', s.query(db.Conversation).filter_by(persona=gone).count() == 0)
    check('persona config and account kept', s.get(db.SavedPersona, gone) is not None
          and s.query(db.User).filter_by(email='day15@x.com').count() == 1)
    check('day 13 warned once, with days left', ('day13@x.com', 1) in sent)
    u13 = s.query(db.User).filter_by(email='day13@x.com').one()
    u13.expires_at = AL._now() + timedelta(days=30)
    s.commit()
    AL.sweep(s)
    s.refresh(u13)
    check('resubscribing clears the warning flag', u13.lapse_notified_at is None)
    zf = zipfile.ZipFile(AL.build_export(s, u13.id))
    names = zf.namelist()
    check('export has chats and the photo', f'{keep}/conversations.json' in names
          and any(n.endswith('.png') for n in names))
    s.close()


def test_prune_logs():
    print('log retention')
    s = db.SessionLocal()
    old = AL._now() - timedelta(days=31)
    s.add_all([db.Visit(path='/', created_at=old), db.Visit(path='/new')])
    s.commit()
    db.prune_logs(s)
    s.commit()
    check('old visit gone, new kept', s.query(db.Visit).filter_by(path='/').count() == 0
          and s.query(db.Visit).filter_by(path='/new').count() == 1)
    s.close()


def test_delete_account():
    print('delete account')
    s = db.SessionLocal()
    u, slug = make_user(s, 'gone@x.com', 0)
    seat, other_slug = make_user(s, 'seat@x.com', 0)
    s.add(db.Membership(workspace_id=u.id, user_id=seat.id, role='chatter'))
    s.add(db.Payment(user_id=u.id, tier='starter', provider='stripe', amount='19'))
    db.token_post(s, u.id, 50, 'adjust', source='test')
    t = db.SupportThread(user_id=u.id)
    s.add(t)
    s.flush()
    s.add(db.SupportMessage(thread_id=t.id, role='user', content='help'))
    s.add(db.BioPage(slug='acct-' + u.id, handle='gonehandle'))
    s.add(db.PushSubscription(user_id=u.id, endpoint='https://push/1'))
    s.commit()
    uid = u.id

    AL.delete_account(s, u)
    u = s.get(db.User, uid)
    check('account marked deleted', u.status == 'deleted')
    check('email and password gone', u.email.endswith('@deleted.invalid')
          and u.password_hash == '!' and not u.name)
    check('persona and its media gone', s.get(db.SavedPersona, slug) is None
          and s.query(db.PersonaMedia).filter_by(slug=slug).count() == 0)
    check('chats gone', s.query(db.Conversation).filter_by(persona=slug).count() == 0)
    check('workspace and seats gone', s.get(db.Workspace, uid) is None
          and s.query(db.Membership).filter_by(workspace_id=uid).count() == 0)
    check('support thread gone', s.query(db.SupportThread).filter_by(user_id=uid).count() == 0
          and s.query(db.SupportMessage).filter_by(thread_id=t.id).count() == 0)
    check('account page and push gone', s.get(db.BioPage, 'acct-' + uid) is None
          and s.query(db.PushSubscription).filter_by(user_id=uid).count() == 0)
    check('payment and ledger kept', s.query(db.Payment).filter_by(user_id=uid).count() == 1
          and db.token_balance(s, uid) == 50)
    check('the seat keeps its own account', s.get(db.User, seat.id).status != 'deleted'
          and s.get(db.SavedPersona, other_slug) is not None)
    check('running it again is harmless', AL.delete_account(s, u) == 0)
    s.close()


if __name__ == '__main__':
    test_sweep_and_export()
    test_prune_logs()
    test_delete_account()
    sys.exit(1 if FAILURES else 0)
