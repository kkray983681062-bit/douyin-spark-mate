"""Send text via the authenticated IM SDK without opening recipient chat pages."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from .browser import ChatPage, account_key
from .models import (
    Cancelled,
    IdentityMismatch,
    LoginRequired,
    Message,
    SendFailed,
    SendUnknown,
    VerificationRequired,
)

SCRIPT = Path(__file__).with_name('im_bridge.js').read_text(encoding='utf-8')


def confirm_current_user(context, user_id: str) -> None:
    """A rotated cookie must still authenticate the same SDK user on the server."""
    try:
        response = context.request.get('https://www.douyin.com/aweme/v1/web/query/user/',
                                       params={'device_platform': 'webapp', 'aid': '6383',
                                               'publish_video_strategy_type': '2'},
                                       timeout=15000, max_redirects=0)
        try:
            data = response.json() if response.status == 200 else {}
        finally:
            response.dispose()
    except Exception as exc:
        raise LoginRequired('暂时无法核对更新后的登录状态，请等待网络恢复后重试') from exc
    if data.get('status_code') != 0 or str(data.get('user_uid', '')) != user_id:
        raise LoginRequired('当前登录账号与私信账号不一致，发送已停止，请重新登录')


def bind_account_identity(store, account: str, user_id: str, cookies: list[dict]) -> None:
    """Keep the old namespace/dedupe history when cookie identity values rotate."""
    cookie_identity = account_key(cookies)
    if not account or not re.fullmatch(r'[1-9]\d*', user_id):
        raise LoginRequired('无法核对私信账号，请重新登录')
    saved = store.setting(account, 'im_user_id')
    if saved:
        if saved != user_id:
            raise LoginRequired('私信账号与已保存的账号不同，发送已停止')
        return
    members = [set(f.key.split(':')[2:]) for f in store.friends(account)
               if re.fullmatch(r'0:1:\d+:\d+', f.key)]
    common = set.intersection(*members) if members else set()
    if common != {user_id} and not (cookie_identity == account and user_id in common):
        raise LoginRequired('无法将当前登录账号与已保存好友对应，请重新扫码并同步好友')
    store.set_setting(account, 'im_user_id', user_id)


class InterfaceTransport:
    def __init__(self, chat: ChatPage, user_id='', *, confirm_timeout=60):
        self.chat = chat
        self.user_id = user_id
        self.confirm_timeout = confirm_timeout

    def read(self, op, **args):
        result = self.chat.page.evaluate(SCRIPT, {'op': op, 'user_id': self.user_id, **args})
        error = result.get('error')
        if error == 'account_changed':
            raise LoginRequired('私信账号已改变，发送已停止')
        if error in {'target_mismatch', 'message_mismatch'}:
            raise IdentityMismatch('接口返回的好友或消息身份不一致，已停止发送')
        if error:
            raise ValueError('抖音私信接口尚未就绪或已变化，请保留窗口后重试；未自动改用聊天页发送')
        return result

    def wait_ready(self):
        started = time.monotonic()
        last_progress = None
        while True:
            if self.chat.cancel.is_set():
                raise Cancelled('已停止连接，抖音窗口已保留')
            hint = '正在连接私信服务'
            try:
                self.chat.check_access()
            except LoginRequired:
                hint = '正在等待登录状态，请在抖音窗口完成登录'
            except VerificationRequired:
                hint = '请在抖音窗口由本人完成验证，完成后继续连接'
            else:
                state = self.read('status')
                if state.get('ready'):
                    if self.user_id and state['user_id'] != self.user_id:
                        raise LoginRequired('私信账号已改变，发送已停止')
                    return state
            elapsed = int(time.monotonic() - started)
            if elapsed >= self.chat.load_timeout:
                raise ValueError('私信服务尚未完成连接，窗口已保留。请加载完成后重试。')
            progress = (hint, elapsed)
            if progress != last_progress:
                self.chat.progress(f'{hint} · 已等 {elapsed} 秒，可点停止')
                last_progress = progress
            self.chat.page.wait_for_timeout(250)

    def verify(self):
        self.chat.check()
        state = self.read('status')
        if not state.get('ready') or state.get('user_id') != self.user_id:
            raise LoginRequired('私信连接或账号状态已改变，发送已停止')

    def open_target(self, friend):
        self.verify()
        self.read('target', target=friend.key)

    def send(self, friend, message: Message, trigger):
        message.validate()
        self.open_target(friend)
        if message.kind != 'text':
            self.chat.progress('图片 / 原生表情正在打开聊天页')
            self.chat.open_target(friend)
            return self.chat.send(friend, message, trigger)
        operation = {'target': friend.key, 'client_id': str(uuid.uuid4())}
        self.read('prepare', **operation, text=message.value)
        self.verify()
        trigger()  # Durable record and account check precede every external send.
        self.chat.progress(f'正在通过接口发送给「{friend.name}」')
        try:
            self.read('start', **operation)
            deadline = time.monotonic() + self.confirm_timeout
            while True:
                self.verify()
                result = self.read('poll', **operation)
                if result.get('state') == 'sent':
                    return
                if result.get('state') == 'rejected':
                    raise SendFailed(f'抖音明确拒绝了本次消息（状态码 {result["code"]}）')
                if result.get('state') == 'unknown' or time.monotonic() >= deadline:
                    raise SendUnknown('接口已调用，但未取得可核对的发送回执；请核对聊天记录，不会自动重发')
                self.chat.page.wait_for_timeout(100)
        except (Cancelled, LoginRequired, VerificationRequired, IdentityMismatch, SendFailed, SendUnknown):
            raise
        except Exception as exc:
            raise SendUnknown('接口发送后连接或操作中断，请核对聊天记录；不会自动重发') from exc
