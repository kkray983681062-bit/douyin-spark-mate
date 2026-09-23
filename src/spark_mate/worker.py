from pathlib import Path
from queue import Empty, Queue
from threading import Event

from PySide6.QtCore import QThread, Signal

from .accounts import Accounts
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
        self.busy = Event()

    def submit(self, action: str, account: str, payload=None):
        if self.busy.is_set() or self.shutdown_event.is_set():
            return False
        self.busy.set()
        self.cancel_event.clear()
        self.jobs.put((action, account, payload))
        return True

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
                    self.busy.clear()
                    self.operation_finished.emit()
        except Exception as exc:  # noqa: BLE001 -- Qt worker boundary must report errors to the UI
            self.failure.emit(str(exc)[:700])
        finally:
            self.checkpoint(session)
            session.close()

    def execute(self, session, action, account, payload):
        with Store(self.root) as store:
            accounts = Accounts(store)
            accounts.import_legacy()
            if action in {'login', 'add', 'switch', 'reauth', 'forget'} or accounts.get(account):
                return self.managed(session, store, accounts, action, account, payload)
        # Retain the existing route for legacy namespaces without a saved vault.
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
                    verified_user_id = ''
                    if cookie_identity != account or not store.setting(account, 'im_user_id'):
                        confirm_current_user(context, state['user_id'])
                        if account_key(context.cookies()) != cookie_identity:
                            raise LoginRequired('核对期间登录状态发生变化，请重试')
                        verified_user_id = state['user_id']
                    bind_account_identity(store, account, state['user_id'], context.cookies(),
                                          verified_user_id=verified_user_id)
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

    def verify_session(self, store, account, context, chat):
        if self.cancel_event.is_set():
            raise Cancelled('已停止账号核对')
        cookie = account_key(context.cookies())
        saved = Accounts(store).get(account) if account else None
        expected = (saved['user_id'] if saved else '') or store.setting(account, 'im_user_id', '')
        if expected:
            confirm_current_user(context, expected)
        transport = InterfaceTransport(chat, expected)
        state = transport.wait_ready(open_inbox=True)
        uid = state['user_id']
        if not expected:
            confirm_current_user(context, uid)
        transport.user_id = uid
        transport.verify()
        if account_key(context.cookies()) != cookie:
            raise LoginRequired('核对期间登录状态发生变化，请重试；当前账号未切换')
        if self.cancel_event.is_set():
            raise Cancelled('已停止账号核对')
        return uid, cookie

    def checkpoint(self, session):
        """Save only the last verified, unchanged identity; never copy a pending login."""
        uid = getattr(session, 'verified_user', '')
        cookie = getattr(session, 'verified_cookie', '')
        context, chat = getattr(session, '_context', None), getattr(session, '_chat', None)
        if not uid or not cookie or context is None or chat is None:
            return
        try:
            if account_key(context.cookies()) != cookie:
                confirm_current_user(context, uid)
                cookie = account_key(context.cookies())
            status = InterfaceTransport(chat).read('status')
            if not status.get('ready') or status.get('user_id') != uid:
                return
            snapshot = context.storage_state()
            if account_key(snapshot['cookies']) != cookie:
                return
            with Store(self.root) as store:
                accounts = Accounts(store)
                item = accounts.get(session.account)
                if item and item['user_id'] == uid and item['status'] == 'saved':
                    accounts.remember(uid, snapshot, preferred=session.account)
                    session.verified_cookie = cookie
        except Exception:  # noqa: BLE001 -- old encrypted login remains intact if checkpoint fails
            self.status.emit('最新登录状态暂未保存，上次保存的登录和记录仍保留。')

    def managed(self, session, store, accounts, action, account, payload):
        if action in {'forget', 'logout'}:
            target = payload or account
            if getattr(session, 'account', '') in {target, f'login:{target}'}:
                session.close()
                session.account = ''
            accounts.forget(target)
            return store.setting('', 'current_account', '')
        if action in {'login', 'add', 'reauth'}:
            target = (payload or account) if action == 'reauth' else ''
            self.checkpoint(session)
            session.select_account(f'login:{target or "new"}', Vault(self.root/'accounts/pending.dpapi'))
            session.login(persist=False)
            with session.open(saved=False) as (context, chat):
                uid, cookie = self.verify_session(store, target, context, chat)
                snapshot = context.storage_state()
                if self.cancel_event.is_set():
                    raise Cancelled('已停止登录')
                if account_key(snapshot['cookies']) != cookie:
                    raise LoginRequired('保存前登录状态发生变化，请重试')
                selected = accounts.remember(uid, snapshot, preferred=target)
                if self.cancel_event.is_set():
                    raise Cancelled('登录已保存，但切换已停止；当前仍为原账号')
                accounts.activate(selected)
                session.account, session.vault = selected, accounts.vault(selected)
                session.verified_user, session.verified_cookie = uid, cookie
                return selected
        target = payload if action == 'switch' else account
        if not accounts.get(target):
            raise LoginRequired('所选账号不存在，请添加账号')
        if getattr(session, 'account', '') != target:
            try:
                accounts.vault(target).load()
            except ValueError:
                accounts.needs_login(target)
                raise
            self.checkpoint(session)
        session.select_account(target, accounts.vault(target))
        try:
            with session.open() as (context, chat):
                uid, cookie = self.verify_session(store, target, context, chat)
                snapshot = context.storage_state()
                if self.cancel_event.is_set():
                    raise Cancelled('已停止切换')
                if account_key(snapshot['cookies']) != cookie:
                    raise LoginRequired('保存前登录状态发生变化，请重试')
                selected = accounts.remember(uid, snapshot, preferred=target)
                if self.cancel_event.is_set():
                    raise Cancelled('切换已停止，当前仍为原账号')
                session.verified_user, session.verified_cookie = uid, cookie
                if action == 'switch':
                    accounts.activate(selected)
                    return selected
                transport = InterfaceTransport(chat, uid)
                def verify():
                    if account_key(context.cookies()) != cookie:
                        raise LoginRequired('登录账号发生变化，操作已停止')
                    transport.verify()
                if action == 'open':
                    return None
                if action == 'sync':
                    result = chat.sync_friends()
                    verify()
                elif action == 'stickers':
                    transport.open_target(payload)
                    chat.open_target(payload)
                    result = chat.list_stickers()
                    verify()
                elif action == 'send':
                    result = run_batch(store, target, payload, transport, self.cancel_event,
                                       self.step.emit, verify=verify)
                else:
                    raise ValueError('未知操作')
                self.checkpoint(session)
                return result
        except LoginRequired:
            accounts.needs_login(target)
            raise
