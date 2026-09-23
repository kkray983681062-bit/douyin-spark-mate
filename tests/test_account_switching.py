"""Worker integration with the real JS bridge and synthetic local identities."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from spark_mate.accounts import Accounts
from spark_mate.browser import ChatPage, account_key, configure_browser
from spark_mate.models import Cancelled, Friend, LoginRequired, Message
from spark_mate.storage import Store
from spark_mate.worker import Worker


def state(uid, suffix='original'):
    return {'cookies': [{'name': 'sessionid', 'value': f'fixture-{uid}-{suffix}',
                         'domain': '.douyin.com', 'path': '/'}], 'origins': []}


@pytest.fixture(scope='module')
def browser():
    configure_browser()
    with sync_playwright() as pw:
        instance = pw.chromium.launch(headless=True, channel='chromium')
        yield instance
        instance.close()


class Session:
    def __init__(self, page, cancel):
        self.page, self.cancel = page, cancel
        self.account = self.verified_user = self.verified_cookie = ''
        self._context = self._chat = None
        self.server_user = None
        self.server_status = 200
        self.sdk_user = None
        self.next_login = state('100', 'rescan')
        self.query_hook = lambda: None
        self.login_cancelled = False
        self.delayed_inbox = False

    def prepare(self, saved):
        self.saved = deepcopy(saved)
        uid = saved['cookies'][0]['value'].split('-')[1]
        self.page.goto((Path(__file__).with_name('fixtures')/'im_sdk.html').as_uri())
        self.page.evaluate('uid=>window.uid=uid', self.sdk_user or uid)
        def get(*_, **__):
            self.query_hook()
            return SimpleNamespace(status=self.server_status, dispose=lambda: None,
                json=lambda: {'status_code': 0, 'user_uid': self.server_user or uid})
        self._context = SimpleNamespace(cookies=lambda: deepcopy(self.saved['cookies']),
            storage_state=lambda: deepcopy(self.saved), request=SimpleNamespace(get=get))
        self._chat = ChatPage(self.page, self.cancel, lambda _: None)
        if self.delayed_inbox:
            self._chat.load_timeout = 0.8
            self.page.evaluate('''() => {
                initResult=1; window.privateClicks=0;
                const button=document.createElement('button'); button.textContent='私信';
                button.onclick=()=>{privateClicks++;initResult=3;}; document.body.append(button);
            }''')

    def select_account(self, account, vault):
        if account == self.account:
            return
        self.account, self.vault = account, vault
        self.verified_user = self.verified_cookie = ''
        if not account.startswith('login:'):
            self.prepare(vault.load())

    @contextmanager
    def open(self, **_):
        yield self._context, self._chat

    def login(self, *, persist=True):
        assert not persist, 'Unverified QR state must not overwrite a saved login'
        self.prepare(self.next_login)
        if self.login_cancelled:
            raise Cancelled('simulated cancellation')
        return account_key(self.saved['cookies'])

    def close(self):
        self._context = self._chat = None


@pytest.fixture
def setup(browser, tmp_path):
    with Store(tmp_path) as store:
        accounts = Accounts(store)
        a = accounts.remember('100', state('100'), label='大号')
        b = accounts.remember('200', state('200'), label='小号')
        accounts.activate(a)
    page = browser.new_page()
    worker = Worker(tmp_path)
    session = Session(page, worker.cancel_event)
    yield worker, session, a, b, tmp_path
    page.close()


def test_worker_switch_roundtrip_keeps_each_accounts_dedupe(setup):
    worker, session, a, b, root = setup
    friend = Friend('0:2:100:203', '模拟共同群', conversation_type=2)
    plan = [(friend, Message('text', 'offline greeting'))]
    assert worker.execute(session, 'switch', a, a) == a
    assert worker.execute(session, 'send', a, plan)[0]['status'] == 'sent'
    assert worker.execute(session, 'switch', a, b) == b
    assert worker.execute(session, 'send', b, plan)[0]['status'] == 'sent'
    assert worker.execute(session, 'switch', b, a) == a
    assert worker.execute(session, 'send', a, plan)[0]['status'] == 'skipped'
    assert session.page.evaluate("calls.filter(x=>x.action==='send')") == []
    with Store(root) as store:
        assert store.setting('', 'current_account') == a
        assert store.history(a)[0]['status'] == 'skipped'
        assert store.history(b)[0]['status'] == 'sent'


def test_switch_initializes_private_inbox_without_needing_contact_rows(setup):
    worker, session, a, b, _ = setup
    session.delayed_inbox = True
    assert worker.execute(session, 'switch', a, b) == b
    assert session.page.evaluate('privateClicks') == 1
    assert session.page.evaluate('calls') == []


@pytest.mark.parametrize('mismatch', ['server', 'sdk'])
def test_switch_mismatch_keeps_previous_active_account_and_saved_vault(setup, mismatch):
    worker, session, a, b, root = setup
    setattr(session, f'{mismatch}_user', '999')
    with Store(root) as store:
        original = Accounts(store).vault(b).path.read_bytes()
    with pytest.raises(LoginRequired):
        worker.execute(session, 'switch', a, b)
    with Store(root) as store:
        assert store.setting('', 'current_account') == a
        assert Accounts(store).vault(b).path.read_bytes() == original
        assert Accounts(store).get(b)['status'] == 'needs_login'
    assert not session.page.is_closed()
    assert session.page.evaluate('calls') == []


def test_stopping_during_identity_check_does_not_commit_switch(setup):
    worker, session, a, b, root = setup
    session.query_hook = worker.cancel_event.set
    with pytest.raises(Cancelled):
        worker.execute(session, 'switch', a, b)
    with Store(root) as store:
        assert store.setting('', 'current_account') == a


def test_adding_same_account_updates_in_place_and_wrong_rescan_is_rejected(setup):
    worker, session, a, b, root = setup
    assert worker.execute(session, 'add', a, None) == a
    session.next_login = state('200', 'wrong-scan')
    with pytest.raises(LoginRequired):
        worker.execute(session, 'reauth', a, a)
    with Store(root) as store:
        accounts = Accounts(store)
        assert len(accounts.all()) == 2
        assert accounts.vault(a).load() == state('100', 'rescan')
        assert accounts.vault(b).load() == state('200')
        assert store.setting('', 'current_account') == a


def test_cancelled_add_keeps_original_login_and_remembered_list(setup):
    worker, session, a, _, root = setup
    session.login_cancelled = True
    with pytest.raises(Cancelled):
        worker.execute(session, 'add', a, None)
    with Store(root) as store:
        assert len(Accounts(store).all()) == 2
        assert store.setting('', 'current_account') == a
        assert Accounts(store).vault(a).load() == state('100')


def test_corrupt_target_vault_does_not_become_an_empty_login_or_change_account(setup):
    worker, session, a, b, root = setup
    with Store(root) as store:
        Accounts(store).vault(b).path.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='登录状态'):
        worker.execute(session, 'switch', a, b)
    with Store(root) as store:
        assert store.setting('', 'current_account') == a
        assert Accounts(store).vault(a).load() == state('100')


def test_refreshed_cookie_is_saved_only_after_rechecking_its_owner(setup):
    worker, session, a, b, root = setup
    worker.execute(session, 'switch', a, a)
    session.saved = state('100', 'refreshed')
    worker.checkpoint(session)
    with Store(root) as store:
        assert Accounts(store).vault(a).load() == state('100', 'refreshed')
    # A stale SDK must not authorize storing somebody else's new session cookie.
    session.saved = state('999', 'wrong-owner')
    session.server_user = '999'
    worker.checkpoint(session)
    with Store(root) as store:
        assert Accounts(store).vault(a).load() == state('100', 'refreshed')
        assert Accounts(store).vault(b).load() == state('200')


def test_stop_during_vault_save_keeps_previous_active_account(setup, monkeypatch):
    worker, session, a, b, root = setup
    original = Accounts.remember
    def save_and_stop(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        worker.cancel_event.set()
        return result
    monkeypatch.setattr(Accounts, 'remember', save_and_stop)
    with pytest.raises(Cancelled):
        worker.execute(session, 'switch', a, b)
    with Store(root) as store:
        assert store.setting('', 'current_account') == a


def test_temporary_server_failure_does_not_mark_saved_login_expired(setup):
    worker, session, a, b, root = setup
    session.server_status = 503
    with pytest.raises(ValueError, match='网络|服务'):
        worker.execute(session, 'switch', a, b)
    with Store(root) as store:
        assert Accounts(store).get(b)['status'] == 'saved'
        assert store.setting('', 'current_account') == a
