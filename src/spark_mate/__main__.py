from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QLockFile, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__
from .storage import data_root
from .ui import MainWindow


def self_test(output: Path, app: QApplication) -> int:
    from threading import Event

    from . import browser as browser_module
    from .browser import DouyinSession
    from .direct import SCRIPT as IM_SCRIPT
    from .models import Friend, Message, today
    from .secrets import Vault
    from .service import run_batch
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {'version': __version__, 'frozen': bool(getattr(sys, 'frozen', False)), 'ok': False}
    try:
        with tempfile.TemporaryDirectory(prefix='SparkMate-smoke-') as folder:
            win = MainWindow(Path(folder), demo=True)
            def mock_state(token):
                return {'cookies': [{'name': 'sessionid', 'value': token,
                                     'domain': '.douyin.com', 'path': '/'}], 'origins': []}
            accounts = win.accounts
            state_a, state_b = mock_state('SYNTHETIC-SMOKE-A'), mock_state('SYNTHETIC-SMOKE-B')
            a = accounts.remember('100001', state_a, label='演示大号')
            b = accounts.remember('200002', state_b, label='演示小号')
            win.store.save_friends(a, win.friends)
            win.store.select(a, [friend.key for friend in win.friends if friend.selected])
            accounts.activate(a)
            win.account = a
            result['multi_account_vaults'] = (accounts.vault(a).load() == state_a
                and accounts.vault(b).load() == state_b
                and b'SYNTHETIC-SMOKE-A' not in accounts.vault(a).path.read_bytes())
            demo_group = Friend('demo-group', '周末小分队', streak='72', conversation_type=2)
            win.store.save_friends(win.account, [demo_group])
            win.refresh()
            result['account_selector'] = (win.account_select.count() == 3
                and win.account_select.currentData() == a and win.account_select.findData(b) > 0)
            stored_group = next(f for f in win.friends if f.key == demo_group.key)
            result['group_storage'] = stored_group.conversation_type == 2 and not stored_group.selected
            # A self-test has no app.exec() loop to flush replaced row widgets.
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            win.show()
            app.processEvents()
            win.grab().save(str(output.with_suffix('.png')))
            result['window_visible'] = win.isVisible()
            result['real_send_disabled'] = not win.send_btn.isEnabled()
            friend = win.friends[0]
            message = Message('text', '演示问候')
            token = win.store.reserve(win.account, friend.key, today(), message)
            win.store.finish(token, 'sent', '模拟成功回执')
            rows = run_batch(win.store, win.account, [(friend, message)], None,
                             Event(), lambda _: None, interval=0)
            win.on_result('send', rows)
            win.history_status.setCurrentIndex(win.history_status.findData('skipped'))
            from PySide6.QtWidgets import QPushButton
            next(b for b in win.findChildren(QPushButton) if '发送记录' in b.text()).click()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            result['skip_history'] = (win.history_table.rowCount() == 1
                and win.history_table.item(0, 1).text() == friend.name
                and '今天已发送' in win.history_table.item(0, 3).text()
                and win.table.item(0, 2).text() == '已发送')
            win.grab().save(str(output.with_name(output.stem+'-history.png')))
            vault = Vault(Path(folder)/'test.dpapi')
            vault.save({'cookies': [], 'origins': []})
            result['vault_roundtrip'] = vault.load() == {'cookies': [], 'origins': []}
            html = Path(folder)/'offline.html'
            html.write_text('''<title>Spark Mate packaged browser</title>
                <div data-e2e="conversation-item"><span class="ConversationItemtitle">Demo group</span></div>
                <div class="RightPanelHeaderconvHeader">Demo group</div>
                <script>
                const group={id:'demo-group',type:2,lastMessageIndexV2:'1'};
                document.querySelector('[data-e2e]').__reactProps$smoke={conversation:group};
                document.querySelector('.RightPanelHeaderconvHeader').__reactProps$smoke={curConversation:group};
                </script>''', encoding='utf-8')
            original_home = browser_module.HOME
            browser_module.HOME = html.as_uri()
            session = DouyinSession(vault, Event(), lambda _: None)
            try:
                with session.open(visible=False) as (_, chat):
                    page = chat.page
                result['browser_kept_open'] = not page.is_closed()
                with session.open(visible=False) as (_, chat):
                    result['browser_reused'] = chat.page is page
                result['browser'] = page.title()
                result['im_bridge_loaded'] = page.evaluate(IM_SCRIPT, {'op': 'status'}) == {'ready': False}
                contacts = chat.visible_friends()
                result['group_adapter'] = (len(contacts) == 1 and contacts[0].key == demo_group.key
                                          and contacts[0].conversation_type == 2
                                          and chat.read('snapshot')['conversation']['type'] == 2)
                browser = session._browser
                isolated = True
                for account, expected in ((a, 'SYNTHETIC-SMOKE-A'), (b, 'SYNTHETIC-SMOKE-B'),
                                          (a, 'SYNTHETIC-SMOKE-A')):
                    previous_page = page
                    session.select_account(account, accounts.vault(account))
                    with session.open(visible=False) as (context, chat):
                        page = chat.page
                        values = [cookie['value'] for cookie in context.cookies()
                                  if cookie['name'] == 'sessionid']
                        isolated &= (values == [expected] and previous_page.is_closed()
                            and page is not previous_page and session._browser is browser
                            and page.evaluate('typeof window.smokeMarker') == 'undefined')
                        page.evaluate('window.smokeMarker = true')
                result['account_context_isolation'] = bool(isolated)
            finally:
                session.close()
                browser_module.HOME = original_home
            result['browser_closed_on_exit'] = page.is_closed()
            win.close()
            result['ok'] = all(result.get(key) for key in (
                'window_visible', 'real_send_disabled', 'vault_roundtrip',
                'browser_kept_open', 'browser_reused', 'browser_closed_on_exit', 'im_bridge_loaded',
                'skip_history', 'group_storage', 'group_adapter', 'multi_account_vaults',
                'account_selector', 'account_context_isolation'))
    except Exception as exc:  # noqa: BLE001 -- packaged smoke emits a failure receipt on any exception
        result['error'] = str(exc)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1


def main() -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description=f'{APP_NAME} · Windows 本地抖音好友消息助手')
    parser.add_argument('--demo', action='store_true', help='独立模拟数据，不登录、不发送')
    parser.add_argument('--capture', type=Path, help='保存窗口截图（开发验证）')
    parser.add_argument('--quit-after', type=float, default=0, help='定时退出（开发验证）')
    parser.add_argument('--self-test', type=Path, help='离线检查界面、加密和内置浏览器，写入 JSON')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('SparkMate')
    app.setOrganizationName('SparkMate')
    app.setWindowIcon(QIcon(str(Path(__file__).with_name('assets')/'icon.ico')))
    app.setFont(QFont('Microsoft YaHei UI', 10))
    if args.self_test:
        return self_test(args.self_test.resolve(), app)
    folder = data_root().with_name('SparkMateDemo') if args.demo else data_root()
    folder.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(folder/'app.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, f'{APP_NAME}已在运行', '请切换到已经打开的窗口，避免重复发送。')
        return 1
    window = MainWindow(folder, demo=args.demo)
    window.show()
    if args.capture:
        args.capture.parent.mkdir(parents=True, exist_ok=True)
        QTimer.singleShot(600, lambda: window.grab().save(str(args.capture.resolve())))
    if args.quit_after:
        QTimer.singleShot(int(args.quit_after*1000), window.close)
    result = app.exec()
    lock.unlock()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
