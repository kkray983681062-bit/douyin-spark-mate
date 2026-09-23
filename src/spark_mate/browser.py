from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from threading import Event

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeout

from .models import (
    Cancelled,
    Friend,
    IdentityMismatch,
    LoginRequired,
    Message,
    SendFailed,
    SendUnknown,
    VerificationRequired,
)
from .secrets import Vault

SCRIPT = Path(__file__).with_name('page_scripts.js').read_text(encoding='utf-8')
HOME = 'https://www.douyin.com/'


def configure_browser() -> None:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    bundled = root / 'browsers' if getattr(sys, 'frozen', False) else root / '.browsers'
    if bundled.is_dir():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(bundled)


def account_key(cookies: list[dict]) -> str:
    values = {c['name']: c['value'] for c in cookies
              if c.get('domain', '').lstrip('.') in {'douyin.com', 'www.douyin.com'} and c.get('value')}
    if not (values.get('sessionid') or values.get('sessionid_ss')):
        raise LoginRequired('请先扫码登录抖音')
    identity = next((values[n] for n in ('uid_tt', 'uid_tt_ss', 'sessionid', 'sessionid_ss') if values.get(n)), '')
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def css_attr(name: str, value: str) -> str:
    return '[' + name + '=' + json.dumps(value, ensure_ascii=False) + ']'


class ChatPage:
    def __init__(self, page: Page, cancel: Event, progress: Callable[[str], None], confirm_timeout=20,
                 load_timeout=300):
        self.page, self.cancel, self.progress = page, cancel, progress
        self.confirm_timeout = confirm_timeout
        self.load_timeout = load_timeout
        page.set_default_timeout(7000)

    def check(self) -> None:
        if self.cancel.is_set():
            raise Cancelled('已停止')
        self.check_access()

    def check_access(self) -> None:
        if re.search('验证码|安全验证|人机验证', self.page.title()):
            raise VerificationRequired('抖音正在要求安全验证，请重新扫码并在官方窗口由本人完成')
        for locator in [self.page.locator('#captcha_container, .captcha_verify_container, #verify-bar-code'),
                        self.page.get_by_role('dialog').filter(has_text=re.compile('安全验证|短信验证')),
                        self.page.locator('[role="alert"], .semi-toast-content').filter(
                            has_text=re.compile('操作频繁|操作太频繁'))]:
            if any(item.is_visible() for item in locator.all()):
                raise VerificationRequired('抖音需要本人完成验证，请重新扫码并在官方窗口处理')
        if any(el.is_visible() for el in self.page.locator('[data-e2e="login-button"], .web-login').all()):
            raise LoginRequired('登录已失效，请重新扫码')

    def read(self, mode: str, message: Message | None = None) -> dict | list:
        return self.page.evaluate(SCRIPT, {'mode': mode, 'message': message.as_dict() if message else {}})

    def visible_friends(self) -> list[Friend]:
        return [Friend(**row) for row in self.read('friends')]

    def message_entry(self, *, private_only=False):
        gap = r'[\s\u200b-\u200d\ufeff]*'
        for text in (('私信',) if private_only else ('私信', '消息')):
            # Prefer the private inbox over a separate notification entry. Badge
            # counts and icon-only accessible labels must not hide the entrance.
            name = re.compile(rf'^{gap}{text}{gap}(?:\d+\+?|[（(]{gap}\d+\+?{gap}[）)])?{gap}$')
            for candidates in (
                    self.page.get_by_role('button', name=name),
                    self.page.get_by_role('link', name=name),
                    self.page.get_by_label(name),
                    self.page.get_by_title(name),
                    self.page.get_by_text(name)):
                for item in candidates.all():
                    if item.is_visible() and item.is_enabled():
                        return item, text
        return None

    def ensure_chat(self, *, timeout=None) -> None:
        started = time.monotonic()
        deadline = started + (self.load_timeout if timeout is None else timeout)
        clicked = None
        last_progress = None
        access_error = None
        while True:
            if self.cancel.is_set():
                raise Cancelled('已停止等待，抖音窗口已保留')
            if self.page.is_closed():
                raise ValueError('抖音窗口已关闭，请再次打开窗口或同步好友')
            hint = '正在等待私信列表加载；你也可以在抖音窗口手动打开「私信 / 消息」'
            try:
                self.check_access()
                access_error = None
                # A mounted row is not ready until its stable identity has loaded.
                if self.visible_friends():
                    return
                rows = self.page.locator('[data-e2e="conversation-item"]:visible')
                if clicked != '私信' and not rows.count():
                    entry = self.message_entry(private_only=clicked == '消息')
                    if entry is not None:
                        item, entry_name = entry
                        hint = '正在打开抖音私信'
                        try:
                            item.click(timeout=max(1, min(700, (deadline - time.monotonic()) * 1000)),
                                       no_wait_after=True)
                            # Only successful clicks count; loading overlays can block
                            # a click. Retrying those must not toggle an opened drawer.
                            clicked = entry_name
                        except BrowserTimeout:
                            pass
            except (LoginRequired, VerificationRequired) as exc:
                access_error = exc
                hint = ('请在抖音窗口完成安全验证，完成后会继续读取'
                        if isinstance(exc, VerificationRequired)
                        else '正在等待登录状态加载；如出现二维码，请在抖音窗口扫码')
            except BrowserError as exc:
                # The browser can navigate while the user scans or refreshes a slow page.
                if not any(text in str(exc) for text in ('Execution context was destroyed',
                                                         'Cannot find context')):
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                note = '本轮等待已结束，抖音窗口会继续保留。页面加载好后可再次点击「同步好友」。'
                if access_error:
                    raise type(access_error)(str(access_error) + '。' + note)
                raise ValueError('暂未读到可核对的好友或群聊。' + note)
            elapsed = int(time.monotonic() - started)
            progress = (hint, elapsed)
            if progress != last_progress:
                self.progress(f'{hint} · 已等 {elapsed} 秒 / 最多 {int(deadline-started)} 秒，可点停止')
                last_progress = progress
            self.page.wait_for_timeout(min(250, remaining * 1000))

    def scroll(self, *, reset=False) -> dict:
        return self.page.locator('[data-e2e="conversation-item"]:visible').first.evaluate('''(item, reset) => {
            let p=item.parentElement;
            while(p && p!==document.body){
                if(p.scrollHeight>p.clientHeight+8 && /auto|scroll/.test(getComputedStyle(p).overflowY)) {
                    const old=p.scrollTop; p.scrollTop=reset?0:old+Math.max(80,p.clientHeight*0.75);
                    return {old,top:p.scrollTop,end:p.scrollTop+p.clientHeight>=p.scrollHeight-3};
                } p=p.parentElement;
            } return {old:0,top:0,end:true};
        }''', reset)

    def sync_friends(self) -> list[Friend]:
        self.ensure_chat()
        self.scroll(reset=True)
        found: dict[str, Friend] = {}
        stagnant = 0
        for _ in range(100):
            self.check()
            for f in self.visible_friends():
                found[f.key] = f
            self.progress(f'正在读取好友和群聊 · 已找到 {len(found)} 个会话')
            pos = self.scroll()
            stagnant = stagnant + 1 if abs(pos['top'] - pos['old']) < 2 else 0
            if stagnant >= 3:
                break
            self.page.wait_for_timeout(450)
        self.scroll(reset=True)
        if not found:
            raise ValueError('页面未提供可核对的好友会话身份，已停止同步；需要适配当前抖音页面')
        return list(found.values())

    def verify_target(self, friend: Friend) -> None:
        self.check_access()
        state = self.read('snapshot')
        current = state.get('conversation')
        if (not current or current['id'] != friend.key or
                current['type'] != friend.conversation_type or friend.conversation_type not in (1, 2)):
            raise IdentityMismatch('当前聊天对象或会话类型与所选对象不一致，已停止发送')

    def open_target(self, friend: Friend) -> None:
        self.check()
        self.ensure_chat()
        self.scroll(reset=True)
        stagnant = 0
        for _ in range(100):
            self.check()
            matches = [f for f in self.visible_friends() if f.key == friend.key]
            if matches:
                if any(f.conversation_type != friend.conversation_type for f in matches):
                    raise IdentityMismatch('会话类型已改变，请重新同步好友和群聊')
                items = self.page.locator(css_attr('data-spark-key', friend.key) + ':visible')
                if items.count() != 1:
                    raise IdentityMismatch('会话身份不唯一，请刷新好友列表')
                items.click()
                started = time.monotonic()
                deadline = started + self.load_timeout
                last_elapsed = -1
                while time.monotonic() < deadline:
                    self.check()
                    try:
                        self.verify_target(friend)
                        if self.read('snapshot')['conversation']['knownBoundary']:
                            self.editor()
                            return
                    except (IdentityMismatch, ValueError):
                        pass
                    elapsed = int(time.monotonic() - started)
                    if elapsed != last_elapsed:
                        self.progress(f'正在等待「{friend.name}」的聊天内容加载 · 已等 {elapsed} 秒，可点停止')
                        last_elapsed = elapsed
                    self.page.wait_for_timeout(250)
                raise IdentityMismatch('聊天内容尚未加载完成，抖音窗口已保留；加载好后可重试')
            pos = self.scroll()
            stagnant = stagnant + 1 if abs(pos['top']-pos['old']) < 2 else 0
            if stagnant >= 3:
                break
            self.page.wait_for_timeout(350)
        raise IdentityMismatch(f'未在当前聊天列表中找到「{friend.name}」，请先在抖音与对方打开会话后重新同步')

    def editor(self):
        for selector in ('.messageEditorinputArea[contenteditable="true"]',
                         '[data-e2e="msg-input"] [contenteditable="true"]',
                         '[contenteditable="true"]', 'textarea[placeholder*="消息"]'):
            loc = self.page.locator(selector + ':visible')
            if loc.count() == 1:
                return loc
        raise ValueError('未找到唯一的消息输入框，已停止')

    def list_stickers(self) -> list[dict]:
        self.check()
        panel = self.page.locator('.componentsemojiemojiPanel:visible')
        if not panel.count():
            buttons = self.page.get_by_role('button', name='表情', exact=True)
            candidates = [b for b in buttons.all() if b.is_visible()]
            if len(candidates) != 1:
                buttons = self.page.locator('[data-e2e="msg-input"] .messageMsgInputinputAction .messageMsgInputiconAction:visible')
                candidates = buttons.all()
            if len(candidates) != 1:
                raise ValueError('无法确认原生表情入口，请先在抖音聊天窗口打开表情面板')
            candidates[0].click()
            panel.wait_for(state='visible')
        stickers = self.read('stickers')
        if not stickers:
            raise ValueError('当前表情页没有可识别的原生表情，请在抖音选择常用/互动表情后再读取')
        return stickers

    def trigger_text(self, editor) -> None:
        visible = [b for b in self.page.get_by_role('button', name='发送', exact=True).all() if b.is_visible()]
        if len(visible) == 1:
            visible[0].click()
        elif not visible:
            editor.press('Enter')
        else:
            raise ValueError('发送按钮不唯一')

    def send(self, friend: Friend, message: Message, on_trigger: Callable[[], None]) -> None:
        self.check()
        message.validate()
        self.verify_target(friend)
        state = self.read('snapshot', message)
        if not state.get('conversation', {}).get('knownBoundary'):
            raise ValueError('暂时无法核对该会话的消息状态，请等待聊天加载后重试')
        editor = self.editor()
        existing = editor.evaluate("el => 'value' in el ? el.value : el.innerText")
        if str(existing).replace('\u200b', '').strip():
            raise ValueError('该会话存在未发送草稿，请先在抖音处理草稿后再试')
        item = None
        if message.kind == 'text':
            editor.click()
            self.page.keyboard.insert_text(message.value)
            entered = editor.evaluate("el => 'value' in el ? el.value : el.innerText")
            if str(entered).replace('\r\n', '\n').strip() != message.value.strip():
                raise ValueError('文字没有完整写入输入框，未发送')
        elif message.kind == 'sticker':
            matches = [s for s in self.list_stickers() if s['name'] == message.value and
                       (not message.resource or s['resource'] == message.resource)]
            if len(matches) != 1:
                raise ValueError('表情不存在或同名表情不唯一，请重新读取选择')
            item = self.page.locator(css_attr('data-spark-sticker', matches[0]['key']) + ':visible')
            if item.count() != 1:
                raise ValueError('原生表情定位已变化')
        else:
            inputs = self.page.locator('input[type="file"][accept*="image"]')
            if inputs.count() != 1:
                raise ValueError('没有找到唯一的图片上传入口，未发送')
            item = inputs
        self.check()
        self.verify_target(friend)
        before = self.read('snapshot', message)
        if not before.get('conversation', {}).get('knownBoundary'):
            raise ValueError('会话消息状态发生变化，未执行发送')
        on_trigger()  # Durable reservation precedes every operation that can transmit.
        try:
            if message.kind == 'text':
                self.trigger_text(editor)
            elif message.kind == 'sticker':
                item.click()
            else:
                item.set_input_files(message.value)
                self.page.wait_for_timeout(600)
                new = self.read('snapshot', message)
                previous_ids = {m['id'] for m in before['messages']}
                if not any(m['id'] not in previous_ids for m in new['messages']):
                    # Only a visible upload preview can authorize an extra send click.
                    preview = self.page.get_by_role('dialog').filter(has=self.page.locator('img'))
                    button = preview.get_by_role('button', name='发送', exact=True)
                    if button.count() == 1 and button.is_visible():
                        button.click()
            self.confirm(friend, message, before)
        except (SendFailed, SendUnknown, LoginRequired, VerificationRequired):
            raise
        except Exception as exc:
            raise SendUnknown('发送已触发，但无法确认结果；请核对抖音聊天记录') from exc

    def confirm(self, friend: Friend, message: Message, before: dict) -> None:
        old = {m['id'] for m in before['messages']}
        boundary = int(before['conversation']['watermark'])
        deadline = time.monotonic() + self.confirm_timeout
        tracked = None
        while time.monotonic() < deadline:
            self.check_access()
            current = self.read('snapshot', message)
            if (not current.get('conversation') or current['conversation']['id'] != friend.key or
                    current['conversation']['type'] != friend.conversation_type):
                raise SendUnknown('等待发送结果时会话发生改变，请核对聊天记录')
            fresh = [m for m in current['messages'] if m['id'] not in old and int(m['order']) > boundary]
            if len(fresh) > 1:
                raise SendUnknown('出现多条相同的新消息，无法确认本次发送结果')
            if fresh:
                row = fresh[0]
                if tracked and tracked != row['id']:
                    raise SendUnknown('待确认消息发生变化')
                tracked = row['id']
                if row['status'] in (-1, -2):
                    raise SendFailed('抖音返回消息发送失败')
                if (row['status'] in (3, 4) or row['hydrated']) and row['server'].isdigit() and int(row['server']) > 0:
                    return
            self.page.wait_for_timeout(150)
        raise SendUnknown('未取得可靠的发送确认，请核对聊天记录；今天不会自动重发')


class DouyinSession:
    def __init__(self, vault: Vault, cancel: Event, progress: Callable[[str], None]):
        self.vault, self.cancel, self.progress = vault, cancel, progress
        self._pw = self._browser = self._context = self._chat = None
        self.account = ''
        self.verified_user = self.verified_cookie = ''

    def select_account(self, account: str, vault: Vault) -> None:
        if self.account == account and self.vault.path == vault.path:
            return
        if self._context:
            try:
                self._context.close()
            except BrowserError:
                pass
        self._context = self._chat = None
        self.account, self.vault = account, vault
        self.verified_user = self.verified_cookie = ''

    def close(self) -> None:
        """Release the browser only on logout, application exit, or a lost connection."""
        browser, pw = self._browser, self._pw
        self._pw = self._browser = self._context = self._chat = None
        self.verified_user = self.verified_cookie = ''
        try:
            if browser and browser.is_connected():
                browser.close()
        finally:
            if pw:
                pw.stop()

    def pump_events(self) -> None:
        # Playwright's sync API must keep dispatching events while the Qt UI is idle.
        if self._chat and not self._chat.page.is_closed():
            try:
                self._chat.page.wait_for_timeout(100)
            except BrowserError:
                if self._browser.is_connected() and not self._chat.page.is_closed():
                    raise

    @contextmanager
    def open(self, *, saved=True, visible=True):
        if self.cancel.is_set():
            raise Cancelled('已停止')
        if self._browser and not self._browser.is_connected():
            self.close()
        if not self._browser:
            configure_browser()
            self.close()
            self._pw = sync_playwright().start()
            try:
                self._browser = self._pw.chromium.launch(headless=not visible, channel='chromium')
            except BrowserError:
                self.close()
                raise
        if not self._context:
            state = self.vault.load() if saved else None
            try:
                self._context = self._browser.new_context(storage_state=state, locale='zh-CN',
                    viewport={'width': 1280, 'height': 860}, accept_downloads=False)
            except BrowserError:
                self.close()
                raise
        if not self._chat or self._chat.page.is_closed():
            existing = self._context.pages
            page = existing[0] if existing else self._context.new_page()
            self._chat = ChatPage(page, self.cancel, self.progress)
            self.progress('抖音窗口已打开，正在加载网页；完成或停止操作后窗口都会保留。')
            try:
                # Start navigation, then use cancellable DOM readiness polling instead of
                # blocking on a fixed page-load delay. A navigation timeout leaves it running.
                page.goto(HOME, wait_until='commit', timeout=1000)
            except BrowserTimeout:
                self.progress('网页响应较慢，正在继续加载；抖音窗口会一直保留。')
        self._chat.page.bring_to_front()
        yield self._context, self._chat

    def login(self, *, persist=True) -> str:
        self.progress('请在打开的抖音官方窗口扫码；若有短信或滑块验证，请本人完成')
        with self.open(saved=False, visible=True) as (context, chat):
            deadline = time.monotonic() + 300
            clicked_login = False
            last_hint = ''
            while time.monotonic() < deadline:
                if self.cancel.is_set():
                    raise Cancelled('登录已取消')
                try:
                    chat.check_access()
                    key = account_key(context.cookies())
                    if persist:
                        # Legacy callers persist only after a known chat surface.
                        # Account onboarding instead verifies the server and SDK in
                        # the worker, including accounts whose contact list is empty.
                        chat.ensure_chat(timeout=max(0, deadline-time.monotonic()))
                        self.vault.save(context.storage_state())
                    return key
                except (LoginRequired, VerificationRequired, ValueError) as exc:
                    if isinstance(exc, LoginRequired) and not clicked_login:
                        candidates = [el for el in chat.page.get_by_text('登录', exact=True).all() if el.is_visible()]
                        if len(candidates) == 1:
                            candidates[0].click()
                            clicked_login = True
                    if isinstance(exc, VerificationRequired):
                        hint = '请在抖音官方窗口完成安全验证，软件正在等待；也可以点击停止。'
                    elif isinstance(exc, LoginRequired):
                        hint = '请在官方窗口点击登录，用手机抖音扫码；软件不会读取你的密码。'
                    else:
                        hint = '正在等待消息列表，请在抖音窗口打开「消息」；新账号需先建立好友会话。'
                    if hint != last_hint:
                        self.progress(hint)
                        last_hint = hint
                chat.page.wait_for_timeout(1000)
            raise LoginRequired('本轮登录等待已结束，抖音窗口已保留；请在网页完成登录后再次点击扫码登录')

    def checked(self, context, chat: ChatPage, expected: str) -> None:
        if account_key(context.cookies()) != expected:
            raise LoginRequired('当前账号已改变，请重新登录并同步该账号的好友')
        chat.ensure_chat()
        if account_key(context.cookies()) != expected:
            raise LoginRequired('等待期间账号发生变化，请退出当前账号后重新登录')

    def sync_friends(self, expected: str) -> list[Friend]:
        with self.open() as (context, chat):
            self.checked(context, chat, expected)
            return chat.sync_friends()

    def stickers(self, expected: str, friend: Friend) -> list[dict]:
        with self.open() as (context, chat):
            self.checked(context, chat, expected)
            chat.open_target(friend)
            return chat.list_stickers()
