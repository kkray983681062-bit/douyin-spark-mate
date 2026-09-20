from pathlib import Path
from threading import Event

import pytest

from spark_mate import browser as browser_module
from spark_mate.browser import DouyinSession
from spark_mate.secrets import Vault

FIXTURE = Path(__file__).with_name('fixtures')/'chat.html'


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_module, 'HOME', FIXTURE.as_uri())
    instance = DouyinSession(Vault(tmp_path/'login.dpapi'), Event(), lambda _: None)
    yield instance
    if hasattr(instance, 'close'):
        instance.close()


@pytest.mark.parametrize('failed', [False, True])
def test_completed_or_failed_operation_keeps_the_same_browser_page(session, failed):
    try:
        with session.open(saved=False, visible=False) as (_, chat):
            page = chat.page
            page.evaluate("window.userWork = 'still loading here'")
            if failed:
                raise ValueError('模拟本轮读取失败')
    except ValueError:
        assert failed
    assert not page.is_closed()
    with session.open(saved=False, visible=False) as (_, next_chat):
        assert next_chat.page is page
        assert next_chat.page.evaluate('userWork') == 'still loading here'
        assert next_chat.page.evaluate('sent') == []


def test_manually_closed_browser_can_be_opened_again(session):
    with session.open(saved=False, visible=False) as (context, chat):
        first = chat.page
        context.browser.close()
    with session.open(saved=False, visible=False) as (_, chat):
        assert chat.page is not first
        chat.ensure_chat()
        assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']


def test_slow_initial_response_does_not_block_access_to_the_open_window(session, chat_server, monkeypatch):
    chat_server.gate = Event()
    monkeypatch.setattr(browser_module, 'HOME', chat_server.url)
    with session.open(saved=False, visible=False) as (_, chat):
        assert not chat_server.gate_expired
        chat_server.gate.set()
        chat.ensure_chat()
        assert [f.key for f in chat.visible_friends()] == ['c1', 'c2']
