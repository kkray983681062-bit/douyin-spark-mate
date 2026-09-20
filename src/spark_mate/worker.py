from pathlib import Path
from queue import Empty, Queue
from threading import Event

from PySide6.QtCore import QThread, Signal

from .browser import DouyinSession, account_key
from .direct import InterfaceTransport, bind_account_identity, confirm_current_user
from .models import Cancelled, LoginRequired
from .secrets import Vault
from .service import run_batch
from .storage import Store


class Worker(QThread):
    result = Signal(str, object)
    failure = Signal(str)
    status = Signal(str)
    step = Signal(dict)
    operation_finished = Signal()

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.cancel_event = Event()
        self.shutdown_event = Event()
        self.jobs = Queue()

    def submit(self, action: str, account: str, payload=None):
        self.cancel_event.clear()
        self.jobs.put((action, account, payload))

    def shutdown(self):
        self.cancel_event.set()
        self.shutdown_event.set()
        self.jobs.put(None)

    def run(self):
        # All operations share one Playwright owner thread and one visible browser.
        session = DouyinSession(Vault(self.root/'login.dpapi'), self.cancel_event, self.status.emit)
        try:
            while not self.shutdown_event.is_set():
                try:
                    command = self.jobs.get(timeout=0.1)
                except Empty:
                    session.pump_events()
                    continue
                if command is None:
                    break
                action, account, payload = command
                try:
                    value = self.execute(session, action, account, payload)
                    self.result.emit(action, value)
                except Cancelled:
                    self.status.emit('操作已停止，抖音窗口已保留；可以等待网页加载后再次操作。')
                except Exception as exc:  # noqa: BLE001 -- report action failure without closing its page
                    self.failure.emit(str(exc)[:700])
                finally:
                    self.operation_finished.emit()
        except Exception as exc:  # noqa: BLE001 -- Qt worker boundary must report errors to the UI
            self.failure.emit(str(exc)[:700])
        finally:
            session.close()

    def execute(self, session, action, account, payload):
        if action == 'login':
            return session.login()
        if action == 'open':
            with session.open():
                return None
        if action == 'logout':
            session.close()
            Vault(self.root/'login.dpapi').clear()
            return None
        if action == 'sync':
            return session.sync_friends(account)
        if action == 'stickers':
            return session.stickers(account, payload)
        if action == 'send':
            with Store(self.root) as store, session.open() as (context, chat):
                transport = chat
                cookie_identity = account
                if any(message.kind == 'text' for _, message in payload):
                    transport = InterfaceTransport(chat)
                    state = transport.wait_ready()
                    cookie_identity = account_key(context.cookies())
                    if cookie_identity != account:
                        confirm_current_user(context, state['user_id'])
                        if account_key(context.cookies()) != cookie_identity:
                            raise LoginRequired('核对期间登录状态发生变化，请重试')
                    bind_account_identity(store, account, state['user_id'], context.cookies())
                    transport.user_id = state['user_id']
                else:
                    session.checked(context, chat, account)
                def verify():
                    if account_key(context.cookies()) != cookie_identity:
                        raise LoginRequired('账号已改变，发送已停止')
                    if isinstance(transport, InterfaceTransport):
                        transport.verify()
                    else:
                        chat.check_access()
                return run_batch(store, account, payload, transport, self.cancel_event,
                                 self.step.emit, verify=verify)
        raise ValueError('未知操作')
