"""Offline group regression coverage; never connects to a real Douyin account."""
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from spark_mate.browser import ChatPage, account_key, configure_browser
from spark_mate.direct import InterfaceTransport, bind_account_identity
from spark_mate.models import Friend, IdentityMismatch, LoginRequired, Message, today
from spark_mate.service import run_batch
from spark_mate.storage import Store

ROOT = Path(__file__).resolve().parents[1]
GROUP_ID = 'offline-group-300'


def group():
    # Duck typing also exercises the old release without requiring its new field.
    return SimpleNamespace(**{**asdict(Friend(GROUP_ID, '测试群', streak='12')),
                              'conversation_type': 2})


@pytest.fixture(scope='module')
def browser():
    configure_browser()
    with sync_playwright() as pw:
        instance = pw.chromium.launch(headless=True, channel='chromium')
        yield instance
        instance.close()


@pytest.fixture
def sdk_page(browser):
    page = browser.new_page()
    page.goto((ROOT/'tests/fixtures/im_sdk.html').as_uri())
    page.evaluate('(id) => {conversations[2].id=id}', GROUP_ID)
    yield page
    page.close()


@pytest.fixture
def chat_page(browser):
    page = browser.new_page()
    page.goto((ROOT/'tests/fixtures/chat.html').as_uri())
    page.evaluate('''id => {
        conversations[id]={id,type:2,lastMessageIndexV2:'100'};
        const row=document.createElement('div'); row.dataset.e2e='conversation-item';
        row.innerHTML='<div class="ConversationItemtitle">测试群</div>';
        bind(row,{conversation:conversations[id]}); row.onclick=()=>openChat(id);
        document.querySelector('.contacts').append(row);
    }''', GROUP_ID)
    yield page
    page.close()


def direct(page):
    chat = ChatPage(page, Event(), lambda _: None)
    chat.open_target = lambda _: pytest.fail('Group text must not open a chat page')
    return InterfaceTransport(chat, '100', confirm_timeout=0.2)


def test_group_sync_keeps_stable_identity_and_default_unselected(chat_page):
    chat = ChatPage(chat_page, Event(), lambda _: None)
    contacts = chat.sync_friends()
    assert {f.key for f in contacts} == {'c1', 'c2', GROUP_ID}
    item = next(f for f in contacts if f.key == GROUP_ID)
    assert item.conversation_type == 2
    assert not item.selected
    chat.open_target(item)
    assert chat.read('snapshot')['conversation']['type'] == 2
    assert chat_page.evaluate('sent') == []


def test_group_only_inbox_is_ready_and_unknown_types_stay_excluded(chat_page):
    chat_page.locator('#one, #two').evaluate_all('nodes=>nodes.forEach(node=>node.remove())')
    chat = ChatPage(chat_page, Event(), lambda _: None, load_timeout=0.4)
    chat.ensure_chat()
    assert [f.key for f in chat.visible_friends()] == [GROUP_ID]
    chat_page.evaluate('id=>conversations[id].type=3', GROUP_ID)
    assert chat.visible_friends() == []


@pytest.mark.parametrize('kind', ['image', 'sticker'])
def test_group_media_opens_exact_group_and_confirms_one_send(chat_page, tmp_path, kind):
    chat = ChatPage(chat_page, Event(), lambda _: None, confirm_timeout=1)
    # Obtain the saved type from synchronization, as the application does.
    items = [f for f in chat.visible_friends() if f.key == GROUP_ID]
    assert len(items) == 1
    chat.open_target(items[0])
    if kind == 'image':
        path = tmp_path/'offline.gif'
        path.write_bytes(bytes.fromhex('47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b'))
        message = Message('image', str(path))
    else:
        sticker = chat.list_stickers()[0]
        message = Message('sticker', sticker['name'], resource=sticker['resource'])
    triggered = []
    chat.send(items[0], message, lambda: triggered.append(True))
    assert triggered == [True]
    assert chat_page.evaluate('sent.map(x=>x.target)') == [GROUP_ID]


def test_dom_rejects_same_id_with_changed_type_before_send(chat_page):
    chat_page.evaluate('id=>{conversations[id].type=1;openChat(id)}', GROUP_ID)
    chat = ChatPage(chat_page, Event(), lambda _: None)
    with pytest.raises(IdentityMismatch):
        chat.send(group(), Message('text', 'offline'), lambda: pytest.fail('Must not trigger'))
    assert chat_page.evaluate('sent') == []


def test_group_text_uses_sdk_and_deduplicates_with_named_history(sdk_page, tmp_path):
    with Store(tmp_path) as store:
        plan = [(group(), Message('text', '离线测试 🔥'))]
        first = run_batch(store, 'account', plan, direct(sdk_page), Event(), lambda _: None, interval=0)
        second = run_batch(store, 'account', plan, direct(sdk_page), Event(), lambda _: None, interval=0)
        assert first[0]['status'] == 'sent'
        assert second[0]['status'] == 'skipped'
        assert store.history('account')[0]['name'] == '测试群'
    assert sdk_page.evaluate("calls.filter(x=>x.action==='send').map(x=>x.target)") == [GROUP_ID]


@pytest.mark.parametrize('change', ["message.conversationType=1", "message.conversationShortId='999'",
                                   "message.conversationId='another-group'", "message.sender='999'"])
def test_group_ack_mismatch_is_unknown_and_never_retried(sdk_page, tmp_path, change):
    sdk_page.evaluate('''change => {
        const send=sdk.sendMessage;
        sdk.sendMessage=async ({message})=>{const ack=await send({message});
            new Function('message', change)(message);return ack;};
    }''', change)
    with Store(tmp_path) as store:
        plan = [(group(), Message('text', 'offline'))]
        first = run_batch(store, 'account', plan, direct(sdk_page), Event(), lambda _: None, interval=0)
        second = run_batch(store, 'account', plan, direct(sdk_page), Event(), lambda _: None, interval=0)
    assert first[0]['status'] == 'unknown'
    assert second[0]['status'] == 'skipped'
    assert len(sdk_page.evaluate("calls.filter(x=>x.action==='send')")) == 1


@pytest.mark.parametrize('mutation', ["conversations[2].type=1", "conversations[2].shortId=''",
                                     'conversations.pop()'])
def test_missing_or_changed_group_never_sends(sdk_page, mutation):
    sdk_page.evaluate(mutation)
    with pytest.raises(IdentityMismatch):
        direct(sdk_page).send(group(), Message('text', 'offline'), lambda: pytest.fail('Must not trigger'))
    assert sdk_page.evaluate('calls') == []


def test_upgrade_preserves_old_selections_messages_and_dedupe(tmp_path):
    # Simulate the exact 0.1.5 friends schema without touching the real database.
    with sqlite3.connect(tmp_path/'spark-mate.db') as db:
        db.execute('''CREATE TABLE friends (account TEXT NOT NULL,key TEXT NOT NULL,name TEXT NOT NULL,
            avatar TEXT NOT NULL,streak TEXT NOT NULL,identity TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0,override TEXT,PRIMARY KEY(account,key))''')
        db.execute("INSERT INTO friends VALUES ('a','0:1:100:201','旧联系人','','8','',1,NULL)")
    with Store(tmp_path) as store:
        original = store.friends('a')[0]
        assert getattr(original, 'conversation_type', None) == 1
        store.set_override('a', original.key, Message('text', '原来的专属内容'))
        token = store.reserve('a', original.key, today(), Message('text', '已发内容'))
        store.finish(token, 'sent', 'original receipt')
        store.save_friends('a', [group(), Friend(original.key, '更新后的名称')])
        contacts = {f.key: f for f in store.friends('a')}
        assert contacts[original.key].selected
        assert contacts[original.key].override.value == '原来的专属内容'
        assert contacts[GROUP_ID].conversation_type == 2
        assert not contacts[GROUP_ID].selected
        assert store.reserve('a', original.key, today(), Message('text', 'duplicate')) is None
        store.select('a', [original.key, GROUP_ID])
        store.set_override('a', GROUP_ID, Message('text', '群专属内容'))
        store.save_friends('a', [group()])
    with Store(tmp_path) as store:
        contacts = {f.key: f for f in store.friends('a')}
        assert contacts[GROUP_ID].selected
        assert contacts[GROUP_ID].override.value == '群专属内容'
        assert store.delivery_statuses('a', today())[original.key] == 'sent'
        assert store.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('server_user', ['100', '999'])
def test_group_only_account_requires_server_verified_identity(sdk_page, tmp_path, server_user):
    from spark_mate.worker import Worker
    cookies = [{'name': 'sessionid', 'value': 'offline-only', 'domain': '.douyin.com'}]
    account = account_key(cookies)
    with Store(tmp_path) as store:
        store.save_friends(account, [group()])
    checks = []
    response = SimpleNamespace(status=200, json=lambda: {'status_code': 0, 'user_uid': server_user},
                               dispose=lambda: None)
    def query(*_, **__):
        checks.append(True)
        return response
    context = SimpleNamespace(cookies=lambda: cookies, request=SimpleNamespace(get=query))
    chat = ChatPage(sdk_page, Event(), lambda _: None)
    @contextmanager
    def opened():
        yield context, chat
    session = SimpleNamespace(open=opened, checked=lambda *_: pytest.fail('Unexpected DOM path'))
    action = lambda: Worker(tmp_path).execute(session, 'send', account, [(group(), Message('text', 'offline'))])
    if server_user == '100':
        assert action()[0]['status'] == 'sent'
        with Store(tmp_path) as store:
            assert store.setting(account, 'im_user_id') == '100'
    else:
        with pytest.raises(LoginRequired):
            action()
        assert sdk_page.evaluate('calls') == []
    assert checks == [True]


def test_group_id_cannot_be_used_to_infer_account_owner(tmp_path):
    with Store(tmp_path) as store:
        store.save_friends('unverified-account', [group()])
        with pytest.raises(LoginRequired):
            bind_account_identity(store, 'unverified-account', '100', [])
        assert store.setting('unverified-account', 'im_user_id') is None
