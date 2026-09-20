import importlib.util
import os
import time
from pathlib import Path
from threading import Event, Timer

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page():
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT/'.browsers')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel='chromium')
        page = browser.new_page()
        page.goto((ROOT/'tests/fixtures/chat.html').as_uri())
        yield page
        browser.close()


def adapter(page):
    assert importlib.util.find_spec('spark_mate.browser'), '浏览器适配器尚未实现'
    from spark_mate.browser import ChatPage
    return ChatPage(page, Event(), lambda _: None, confirm_timeout=0.8)


def test_same_names_remain_separate_and_open_by_stable_id(page):
    chat = adapter(page)
    friends = chat.visible_friends()
    assert [f.name for f in friends] == ['小雨', '小雨']
    assert [f.key for f in friends] == ['c1', 'c2']
    chat.open_target(friends[1])
    assert page.evaluate('active') == 'c2'


def test_stale_or_wrong_conversation_cannot_send(page):
    chat = adapter(page)
    from spark_mate.models import Friend, IdentityMismatch, Message
    with pytest.raises(IdentityMismatch):
        chat.send(Friend('not-the-current-chat', '小雨'), Message('text', 'hello'), lambda: None)
    assert page.evaluate('sent') == []


def test_text_waits_for_server_state_and_only_sends_once(page):
    chat = adapter(page)
    from spark_mate.models import Message
    friend = chat.visible_friends()[0]
    trigger = []
    chat.send(friend, Message('text', '你好 🔥'), lambda: trigger.append(True))
    assert page.evaluate('sent') == [{'value': '你好 🔥', 'kind': 'text', 'target': 'c1'}]
    assert trigger == [True]


@pytest.mark.parametrize('mode,exception', [('empty', 'SendUnknown'), ('pending', 'SendUnknown'), ('failed', 'SendFailed')])
def test_cleared_composer_is_not_delivery_confirmation(page, mode, exception):
    chat = adapter(page)
    from spark_mate import models
    page.evaluate('(mode)=>window.mode=mode', mode)
    with pytest.raises(getattr(models, exception)):
        chat.send(chat.visible_friends()[0], models.Message('text', 'hello'), lambda: None)
    assert len(page.evaluate('sent')) == 1


def test_listing_native_stickers_never_sends_and_click_sends_once(page):
    chat = adapter(page)
    from spark_mate.models import Message
    stickers = chat.list_stickers()
    assert stickers[0]['name'] == '比心'
    assert page.evaluate('sent') == []
    chat.send(chat.visible_friends()[0], Message('sticker', '比心', resource=stickers[0]['resource']), lambda: None)
    assert len(page.evaluate('sent')) == 1
    assert page.evaluate('sent[0].kind') == 'sticker'


def test_upload_that_autosends_never_triggers_second_message(page, tmp_path):
    chat = adapter(page)
    from spark_mate.models import Message
    image = tmp_path/'图片.gif'
    image.write_bytes(bytes.fromhex('47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b'))
    trigger = []
    chat.send(chat.visible_friends()[0], Message('image', str(image)), lambda: trigger.append(True))
    assert len(page.evaluate('sent')) == 1
    assert trigger == [True]


def test_cancel_before_trigger_leaves_chat_untouched(page):
    chat = adapter(page)
    from spark_mate.models import Cancelled, Message
    chat.cancel.set()
    with pytest.raises(Cancelled):
        chat.send(chat.visible_friends()[0], Message('text', 'hello'), lambda: None)
    assert page.evaluate('sent') == []


def test_captcha_interstitial_without_dialog_stops_before_typing(page):
    chat = adapter(page)
    from spark_mate.models import Message, VerificationRequired
    page.evaluate("document.title = '验证码中间页'")
    with pytest.raises(VerificationRequired):
        chat.send(chat.visible_friends()[0], Message('text', 'hello'), lambda: None)
    assert page.evaluate('sent') == []
    assert page.locator('[contenteditable=true]').inner_text() == ''


def test_missing_message_boundary_cannot_be_treated_as_zero(page):
    chat = adapter(page)
    from spark_mate.models import Message
    page.evaluate("delete conversations.c1.lastMessageIndexV2")
    with pytest.raises(ValueError, match='消息状态'):
        chat.send(chat.visible_friends()[0], Message('text', 'hello'), lambda: None)
    assert page.evaluate('sent') == []
    assert page.locator('[contenteditable=true]').inner_text() == ''


def test_slow_message_list_waits_without_toggling_it_closed(page):
    chat = adapter(page)
    progress = []
    chat.progress = progress.append
    page.evaluate('''() => {
        document.querySelector('.contacts').style.display = 'none';
        window.messageClicks = 0;
        const button = document.createElement('button');
        button.textContent = '消息'; document.body.append(button);
        button.onclick = () => {
            messageClicks++;
            if (messageClicks === 1) setTimeout(() => {
                document.querySelector('.contacts').style.display = 'block';
            }, 3000);
            else document.querySelector('.contacts').style.display = 'none';
        };
    }''')
    chat.ensure_chat()
    assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']
    assert page.evaluate('messageClicks') == 1
    assert progress
    assert page.evaluate('sent') == []


def test_homepage_can_finish_loading_before_message_entry_appears(page):
    chat = adapter(page)
    page.evaluate('''() => {
        document.querySelector('.contacts').style.display = 'none';
        setTimeout(() => {
            const button = document.createElement('button');
            button.textContent = '消息'; document.body.append(button);
            button.onclick = () => document.querySelector('.contacts').style.display = 'block';
        }, 700);
    }''')
    chat.ensure_chat()
    assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']


def test_message_rows_wait_for_identity_data_not_just_empty_shells(page):
    chat = adapter(page)
    page.evaluate('''() => {
        const rows = ['one', 'two'].map(id => document.getElementById(id));
        rows.forEach(el => delete el.__reactFiber$fixture);
        setTimeout(() => rows.forEach((el, i) =>
            bind(el, {conversation: conversations['c' + (i + 1)]})), 700);
    }''')
    chat.ensure_chat()
    assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']


def test_stop_interrupts_slow_loading_and_leaves_the_page_open(page):
    from spark_mate.models import Cancelled
    chat = adapter(page)
    page.locator('.contacts').evaluate("el => el.style.display = 'none'")
    timer = Timer(0.3, chat.cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            chat.ensure_chat()
    finally:
        timer.cancel()
    assert time.monotonic() - started < 2
    assert not page.is_closed()
    assert page.evaluate('sent') == []


def test_wait_expiry_is_bounded_and_the_page_is_still_available(page):
    chat = adapter(page)
    chat.load_timeout = 0.5
    page.locator('.contacts').evaluate("el => el.style.display = 'none'")
    started = time.monotonic()
    with pytest.raises(ValueError):
        chat.ensure_chat()
    assert 0.4 <= time.monotonic() - started < 2
    assert not page.is_closed()


def test_opening_friend_waits_for_message_history_and_editor_before_returning(page):
    chat = adapter(page)
    target = chat.visible_friends()[1]
    page.evaluate('''() => {
        document.getElementById('two').onclick = () => {
            openChat('c2');
            delete conversations.c2.lastMessageIndexV2;
            document.querySelector('[contenteditable]').style.display = 'none';
            setTimeout(() => {
                conversations.c2.lastMessageIndexV2 = '100';
                document.querySelector('[contenteditable]').style.display = 'block';
            }, 900);
        };
    }''')
    chat.open_target(target)
    assert chat.read('snapshot')['conversation']['knownBoundary']
    assert page.locator('[contenteditable]').is_visible()
    assert page.evaluate('sent') == []


def test_sync_opens_private_messages_instead_of_notification_messages(page):
    chat = adapter(page)
    chat.load_timeout = 2
    page.evaluate('''() => {
        document.querySelector('.contacts').style.display = 'none';
        window.notificationClicks = 0; window.privateClicks = 0;
        const news = document.createElement('button');
        news.textContent = '消息'; news.onclick = () => notificationClicks++;
        document.body.prepend(news);
        const inbox = document.createElement('button');
        inbox.textContent = '私信'; inbox.onclick = () => {
            privateClicks++;
            document.querySelector('.contacts').style.display = 'block';
        };
        document.body.append(inbox);
    }''')
    chat.ensure_chat()
    assert page.evaluate('notificationClicks') == 0
    assert page.evaluate('privateClicks') == 1
    assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']
    assert page.evaluate('sent') == []


def test_late_private_entry_is_not_skipped_after_notification_entry_loads_first(page):
    chat = adapter(page)
    chat.load_timeout = 2
    page.evaluate('''() => {
        document.querySelector('.contacts').style.display = 'none';
        window.privateClicks = 0;
        const news = document.createElement('button');
        news.textContent = '消息'; document.body.prepend(news);
        setTimeout(() => {
            const inbox = document.createElement('button');
            inbox.textContent = '私信'; document.body.prepend(inbox);
            inbox.onclick = () => {
                privateClicks++;
                document.querySelector('.contacts').style.display = 'block';
            };
        }, 700);
    }''')
    chat.ensure_chat()
    assert page.evaluate('privateClicks') == 1
    assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']
    assert page.evaluate('sent') == []


@pytest.mark.parametrize('entry_html', [
    '<button aria-label="私信"><svg width="20" height="20"></svg></button>',
    '<button title="私信"><svg width="20" height="20"></svg></button>',
    '<button>私信 <span>99+</span></button>',
    '<button aria-label="私信（3）"><svg width="20" height="20"></svg></button>',
    '<button>\\u200b私信\\u200b</button>',
])
def test_private_entry_can_be_an_icon_or_have_an_unread_badge(page, entry_html):
    chat = adapter(page)
    chat.load_timeout = 2
    page.evaluate('''html => {
        document.querySelector('.contacts').style.display = 'none';
        const host = document.createElement('header');
        host.innerHTML = html; document.body.prepend(host);
        host.querySelector('button').onclick = () =>
            document.querySelector('.contacts').style.display = 'block';
    }''', entry_html.replace('\\u200b', '\u200b'))
    chat.ensure_chat()
    assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']
    assert page.evaluate('sent') == []


def test_private_entry_click_blocked_during_loading_is_retried(page):
    chat = adapter(page)
    chat.load_timeout = 4
    page.evaluate('''() => {
        document.querySelector('.contacts').style.display = 'none';
        window.privateClicks = 0;
        const inbox = document.createElement('button');
        inbox.textContent = '私信'; document.body.prepend(inbox);
        inbox.onclick = () => {
            privateClicks++;
            document.querySelector('.contacts').style.display = 'block';
        };
        const cover = document.createElement('div');
        cover.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.1);z-index:100';
        document.body.append(cover);
        setTimeout(() => cover.remove(), 1300);
    }''')
    chat.ensure_chat()
    assert page.evaluate('privateClicks') == 1
    assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']
    assert page.evaluate('sent') == []
