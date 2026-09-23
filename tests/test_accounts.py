import importlib.util
from pathlib import Path
from threading import Event

import pytest

from spark_mate import browser as browser_module
from spark_mate.browser import DouyinSession, account_key
from spark_mate.models import Friend, LoginRequired, Message, today
from spark_mate.secrets import Vault
from spark_mate.storage import Store


def api(store):
    assert importlib.util.find_spec('spark_mate.accounts'), 'Multi-account storage has not been implemented'
    from spark_mate.accounts import Accounts
    return Accounts(store)


def state(token):
    return {'cookies': [{'name': 'sessionid', 'value': token, 'domain': '.douyin.com', 'path': '/'}],
            'origins': [{'origin': 'https://www.douyin.com',
                         'localStorage': [{'name': 'offline-account', 'value': token}]}]}


def test_accounts_survive_restart_without_sharing_credentials_or_history(tmp_path):
    with Store(tmp_path) as store:
        accounts = api(store)
        a = accounts.remember('100', state('SYNTHETIC-ACCOUNT-A'), label='大号')
        b = accounts.remember('200', state('SYNTHETIC-ACCOUNT-B'), label='小号')
        assert a != b
        for account in (a, b):
            store.save_friends(account, [Friend('same-group', '模拟共同群', conversation_type=2)])
        store.select(a, ['same-group'])
        store.save_template(a, '问候', Message('text', '只属于 A'))
        token = store.reserve(a, 'same-group', today(), Message('text', 'offline'))
        store.finish(token, 'sent', 'simulated receipt')
        accounts.activate(b)
        assert b'SYNTHETIC-ACCOUNT-A' not in accounts.vault(a).path.read_bytes()
    with Store(tmp_path) as store:
        accounts = api(store)
        assert store.setting('', 'current_account') == b
        assert accounts.vault(a).load() == state('SYNTHETIC-ACCOUNT-A')
        assert accounts.vault(b).load() == state('SYNTHETIC-ACCOUNT-B')
        assert store.friends(a)[0].selected and not store.friends(b)[0].selected
        assert not store.templates(b)
        assert store.reserve(a, 'same-group', today(), Message('text', 'offline')) is None
        assert store.reserve(b, 'same-group', today(), Message('text', 'offline')) is not None


def test_same_user_relogin_updates_vault_without_duplicate_account(tmp_path):
    with Store(tmp_path) as store:
        accounts = api(store)
        first = accounts.remember('100', state('first'), label='我的账号')
        accounts.activate(first)
        again = accounts.remember('100', state('rotated'), label='不应覆盖备注')
        assert again == first
        assert len(accounts.all()) == 1
        assert accounts.get(first)['label'] == '我的账号'
        assert accounts.vault(first).load() == state('rotated')


def test_reauth_cannot_replace_selected_account_with_another_user(tmp_path):
    with Store(tmp_path) as store:
        accounts = api(store)
        a = accounts.remember('100', state('original'))
        accounts.activate(a)
        original = accounts.vault(a).path.read_bytes()
        with pytest.raises(LoginRequired):
            accounts.remember('999', state('wrong-user'), preferred=a)
        assert accounts.vault(a).path.read_bytes() == original
        assert store.setting('', 'current_account') == a
        assert len(accounts.all()) == 1


def test_legacy_login_upgrade_preserves_namespace_and_receipts(tmp_path):
    old_state = state('old-cookie')
    old_account = account_key(old_state['cookies'])
    Vault(tmp_path/'login.dpapi').save(old_state)
    with Store(tmp_path) as store:
        store.set_setting('', 'current_account', old_account)
        store.set_setting(old_account, 'im_user_id', '100')
        store.save_friends(old_account, [Friend('old-friend', '模拟旧好友')])
        store.select(old_account, ['old-friend'])
        token = store.reserve(old_account, 'old-friend', today(), Message('text', 'offline'))
        store.finish(token, 'sent', 'old receipt')
        accounts = api(store)
        accounts.import_legacy()
        accounts.import_legacy()
        assert len(accounts.all()) == 1
        assert accounts.vault(old_account).load() == old_state
        assert not (tmp_path/'login.dpapi').exists()
        assert accounts.remember('100', state('updated-cookie'), preferred=old_account) == old_account
        assert store.friends(old_account)[0].selected
        assert store.reserve(old_account, 'old-friend', today(), Message('text', 'offline')) is None


def test_forget_only_removes_selected_login_and_relogin_reuses_history(tmp_path):
    with Store(tmp_path) as store:
        accounts = api(store)
        accounts.import_legacy()
        a = accounts.remember('100', state('a'))
        b = accounts.remember('200', state('b'))
        store.save_friends(a, [Friend('friend-a', '模拟好友')])
        accounts.activate(b)
        accounts.forget(a)
        assert store.setting('', 'current_account') == b
        assert not accounts.vault(a).path.exists()
        assert accounts.vault(b).load() == state('b')
        assert store.friends(a)[0].key == 'friend-a'
        assert accounts.get(a)['status'] == 'forgotten'
        assert accounts.remember('100', state('a-again')) == a
        assert len(accounts.all()) == 2


def test_switch_uses_isolated_contexts_and_reuses_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_module, 'HOME', (Path(__file__).with_name('fixtures')/'chat.html').as_uri())
    a, b = Vault(tmp_path/'a.dpapi'), Vault(tmp_path/'b.dpapi')
    a.save(state('a'))
    b.save(state('b'))
    session = DouyinSession(a, Event(), lambda _: None)
    try:
        assert hasattr(session, 'select_account'), 'Account context switching has not been implemented'
        session.select_account('a', a)
        with session.open(visible=False) as (context_a, chat_a):
            browser = context_a.browser
            chat_a.page.evaluate("window.accountMarker='A'")
        session.select_account('b', b)
        with session.open(visible=False) as (context_b, chat_b):
            assert context_b is not context_a
            assert context_b.browser is browser
            assert chat_b.page.evaluate('window.accountMarker') is None
            assert next(c['value'] for c in context_b.cookies() if c['name'] == 'sessionid') == 'b'
        session.select_account('b', b)
        with session.open(visible=False) as (_, again_b):
            assert again_b.page is chat_b.page
        session.select_account('a', a)
        with session.open(visible=False) as (restored_a, _):
            assert restored_a.browser is browser
            assert next(c['value'] for c in restored_a.cookies() if c['name'] == 'sessionid') == 'a'
        assert chat_a.page.is_closed() and chat_b.page.is_closed()
    finally:
        session.close()


def test_switch_cannot_be_queued_while_another_operation_is_pending(tmp_path):
    from spark_mate.worker import Worker
    worker = Worker(tmp_path)
    assert worker.submit('send', 'a', []) is True
    worker.cancel_event.set()
    assert worker.submit('switch', 'a', 'b') is False
    assert worker.cancel_event.is_set()
    assert worker.jobs.qsize() == 1


def test_readding_unbound_legacy_account_preserves_its_verified_contact_namespace(tmp_path):
    old_state = state('legacy-before-first-send')
    old_account = account_key(old_state['cookies'])
    Vault(tmp_path/'login.dpapi').save(old_state)
    with Store(tmp_path) as store:
        store.set_setting('', 'current_account', old_account)
        store.save_friends(old_account, [Friend('0:1:100:201', '模拟甲'), Friend('0:1:100:202', '模拟乙')])
        accounts = api(store)
        accounts.import_legacy()
        assert accounts.remember('100', state('new-cookie')) == old_account
        assert len(accounts.all()) == 1
