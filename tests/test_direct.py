from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from spark_mate.browser import ChatPage, configure_browser
from spark_mate.models import (
    Cancelled,
    Friend,
    IdentityMismatch,
    LoginRequired,
    Message,
    SendFailed,
    SendUnknown,
)
from spark_mate.service import run_batch
from spark_mate.storage import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page():
    configure_browser()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel='chromium')
        page = browser.new_page()
        page.goto((ROOT/'tests/fixtures/im_sdk.html').as_uri())
        yield page
        browser.close()


def transport(page):
    from spark_mate.direct import InterfaceTransport
    chat = ChatPage(page, Event(), lambda _: None)
    chat.open_target = lambda _: pytest.fail('Text must not open the chat page')
    chat.ensure_chat = lambda **_: pytest.fail('Text must not load the chat list')
    return InterfaceTransport(chat, '100', confirm_timeout=0.15)


def test_direct_text_never_opens_chat_and_requires_server_ack(page):
    direct = transport(page)
    assert direct.wait_ready()['single_count'] == 2
    friend = Friend('0:1:100:201', '甲')
    direct.open_target(friend)
    calls = []
    direct.send(friend, Message('text', '你好 🔥'), lambda: calls.append('durable-trigger'))
    assert calls == ['durable-trigger']
    actions = page.evaluate('calls')
    assert [a['action'] for a in actions] == ['create', 'send']
    assert actions[0]['insert'] is False
    assert actions[1]['target'] == friend.key


@pytest.mark.parametrize('mode', ['no_server', 'no_body', 'wrong_target', 'wrong_client',
                                  'wrong_sender', 'wrong_content', 'wrong_echo', 'network', 'pending'])
def test_uncertain_or_unrelated_ack_never_counts_as_sent(page, mode):
    direct = transport(page)
    page.evaluate('(value)=>window.mode=value', mode)
    with pytest.raises(SendUnknown):
        direct.send(Friend('0:1:100:201', '甲'), Message('text', 'hello'), lambda: None)
    assert len(page.evaluate("calls.filter(x=>x.action==='send')")) == 1


def test_explicit_server_rejection_is_failed(page):
    page.evaluate("mode='rejected'")
    with pytest.raises(SendFailed):
        transport(page).send(Friend('0:1:100:201', '甲'), Message('text', 'hello'), lambda: None)


def test_account_switch_or_group_stops_before_transmission(page):
    direct = transport(page)
    with pytest.raises(IdentityMismatch):
        direct.open_target(Friend('0:2:100:203', '群'))
    page.evaluate("uid='201'")
    with pytest.raises(LoginRequired):
        direct.send(Friend('0:1:100:201', '甲'), Message('text', 'hello'), lambda: None)
    assert page.evaluate('calls') == []


def test_trigger_failure_and_cancellation_do_not_send(page):
    direct = transport(page)
    def stop():
        raise Cancelled('stopped')
    with pytest.raises(Cancelled):
        direct.send(Friend('0:1:100:201', '甲'), Message('text', 'hello'), stop)
    direct.chat.cancel.set()
    with pytest.raises(Cancelled):
        direct.open_target(Friend('0:1:100:201', '甲'))
    assert not page.evaluate("calls.some(x=>x.action==='send')")


def test_pending_direct_send_is_durable_and_not_resent(page, tmp_path):
    direct = transport(page)
    page.evaluate("mode='pending'")
    plan = [(Friend('0:1:100:201', '甲'), Message('text', 'hello'))]
    with Store(tmp_path) as store:
        first = run_batch(store, 'account', plan, direct, Event(), lambda _: None, interval=0)
        second = run_batch(store, 'account', plan, direct, Event(), lambda _: None, interval=0)
    assert first[0]['status'] == 'unknown'
    assert second[0]['status'] == 'skipped'
    assert len(page.evaluate("calls.filter(x=>x.action==='send')")) == 1


def test_legacy_cookie_rotation_binds_only_the_common_conversation_owner(tmp_path):
    from spark_mate.direct import bind_account_identity
    cookies = [{'name':'sessionid', 'value':'rotated', 'domain':'.douyin.com'}]
    with Store(tmp_path) as store:
        store.save_friends('old', [Friend('0:1:100:201', '甲'), Friend('0:1:100:202', '乙')])
        bind_account_identity(store, 'old', '100', cookies)
        assert store.setting('old', 'im_user_id') == '100'
        with pytest.raises(LoginRequired):
            bind_account_identity(store, 'old', '201', cookies)
        store.save_friends('ambiguous', [Friend('0:1:100:201', '甲')])
        with pytest.raises(LoginRequired):
            bind_account_identity(store, 'ambiguous', '201', cookies)


def test_worker_routes_text_without_dom_and_keeps_existing_account_history(page, tmp_path):
    from spark_mate.worker import Worker
    friends = [Friend('0:1:100:201', '甲'), Friend('0:1:100:202', '乙')]
    with Store(tmp_path) as store:
        store.save_friends('old-account', friends)
    chat = ChatPage(page, Event(), lambda _: None)
    chat.ensure_chat = lambda **_: pytest.fail('Text route must not wait for the DOM inbox')
    context = SimpleNamespace(cookies=lambda: [{'name':'sessionid', 'value':'rotated', 'domain':'.douyin.com'}])
    response = SimpleNamespace(status=200, json=lambda: {'status_code': 0, 'user_uid': '100'}, dispose=lambda: None)
    context.request = SimpleNamespace(get=lambda *_, **__: response)
    @contextmanager
    def opened():
        yield context, chat
    session = SimpleNamespace(open=opened, checked=lambda *_: pytest.fail('Unexpected legacy DOM path'))
    worker = Worker(tmp_path)
    results = worker.execute(session, 'send', 'old-account', [(friends[0], Message('text', 'hi'))])
    assert results[0]['status'] == 'sent'
    with Store(tmp_path) as store:
        assert store.history('old-account')[0]['status'] == 'sent'
        assert store.setting('old-account', 'im_user_id') == '100'


def test_initial_login_render_can_finish_before_sdk_ready(page):
    page.evaluate('''() => {
        window.initResult=1;
        const login=document.createElement('button'); login.dataset.e2e='login-button';
        login.textContent='登录'; document.body.append(login);
        setTimeout(()=>{login.remove();window.initResult=3},350);
    }''')
    direct = transport(page)
    direct.chat.load_timeout = 2
    assert direct.wait_ready()['user_id'] == '100'
    assert page.evaluate('calls') == []


def test_send_receipt_cannot_continue_batch_after_account_change(page, tmp_path):
    page.evaluate('''() => {const send=sdk.sendMessage;sdk.sendMessage=async args=>{
        const result=await send(args);window.uid='999';return result;
    }}''')
    plan = [(Friend(f'0:1:100:{other}', other), Message('text', 'hi')) for other in ('201', '202')]
    with Store(tmp_path) as store:
        results = run_batch(store, 'account', plan, transport(page), Event(), lambda _: None, interval=0)
    assert len(results) == 1 and results[0]['status'] == 'unknown'
    assert len(page.evaluate("calls.filter(x=>x.action==='send')")) == 1


@pytest.mark.parametrize('payload', [{'status_code': 8}, {'status_code': 0, 'user_uid': '201'}])
def test_rotated_cookie_cannot_use_another_accounts_stale_sdk(payload):
    from spark_mate.direct import confirm_current_user
    response = SimpleNamespace(status=200, json=lambda: payload, dispose=lambda: None)
    context = SimpleNamespace(request=SimpleNamespace(get=lambda *_, **__: response))
    with pytest.raises(LoginRequired):
        confirm_current_user(context, '100')
