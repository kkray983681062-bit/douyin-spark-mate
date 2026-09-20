import importlib.util
import os
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def window(tmp_path, app):
    assert importlib.util.find_spec('spark_mate.ui'), '桌面界面尚未实现'
    from spark_mate.ui import MainWindow
    return MainWindow(tmp_path, demo=True)


def test_filter_does_not_discard_hidden_selection(tmp_path, app):
    ui = window(tmp_path, app)
    ui.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    first = ui.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    ui.search.setText('找不到这个人')
    app.processEvents()
    assert first in [f.key for f in ui.store.friends(ui.account) if f.selected]
    ui.close()


def test_history_can_show_only_skipped_while_today_stays_sent(tmp_path, app):
    from threading import Event

    from spark_mate.models import Message, today
    from spark_mate.service import run_batch
    ui = window(tmp_path, app)
    try:
        friend = ui.friends[0]
        token = ui.store.reserve(ui.account, friend.key, today(), Message('text', 'hi'))
        ui.store.finish(token, 'sent', 'earlier success')
        result = run_batch(ui.store, ui.account, [(friend, Message('text', 'hi'))], None,
                           Event(), lambda _: None, interval=0)
        ui.on_result('send', result)
        ui.history_status.setCurrentIndex(ui.history_status.findData('skipped'))
        app.processEvents()
        assert ui.history_table.rowCount() == 1
        assert ui.history_table.item(0, 1).text() == friend.name
        assert ui.history_table.item(0, 2).text() == '已跳过'
        assert '今天已发送' in ui.history_table.item(0, 3).text()
        assert ui.table.item(0, 2).text() == '已发送'
    finally:
        ui.close()


@pytest.mark.parametrize('area', ['avatar', 'name', 'detail', 'gap', 'status', 'checkbox', 'check_padding'])
def test_clicking_any_part_of_a_friend_row_toggles_once_and_persists(tmp_path, app, area):
    ui = window(tmp_path, app)
    try:
        ui.select_visible(False, all_rows=True)
        ui.show()
        app.processEvents()
        key = ui.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
        changes = []
        ui.table.itemChanged.connect(lambda item: changes.append(item.checkState()))
        cell = ui.table.cellWidget(0, 1)
        labels = cell.findChildren(QLabel)
        if area in {'avatar', 'name', 'detail'}:
            target = next(x for x in labels if
                          (area == 'avatar' and x.objectName() == 'avatar') or
                          (area == 'name' and x.text() == ui.friends[0].name) or
                          (area == 'detail' and x.objectName() == 'muted'))
            point = target.mapToGlobal(target.rect().center())
        elif area == 'gap':
            point = cell.mapToGlobal(QPoint(cell.width() - 3, cell.height() - 3))
        else:
            col = 2 if area == 'status' else 0
            rect = ui.table.visualItemRect(ui.table.item(0, col))
            pos = QPoint(rect.right() - 2, rect.center().y()) if area == 'check_padding' else rect.center()
            point = ui.table.viewport().mapToGlobal(pos)
        for checked in (True, False):
            # Hit the widget under the pointer, including the avatar/name widgets.
            target = app.widgetAt(point)
            assert target is not None
            QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target.mapFromGlobal(point))
            app.processEvents()
            assert {f.key for f in ui.store.friends(ui.account) if f.selected} == ({key} if checked else set())
            assert len(changes) == (1 if checked else 2)
    finally:
        ui.close()


def test_friend_row_keeps_keyboard_checkbox_control_and_is_locked_while_busy(tmp_path, app):
    ui = window(tmp_path, app)
    try:
        ui.select_visible(False, all_rows=True)
        ui.show()
        app.processEvents()
        ui.table.setCurrentCell(0, 0)
        QTest.keyClick(ui.table, Qt.Key.Key_Space)
        key = ui.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
        assert {f.key for f in ui.store.friends(ui.account) if f.selected} == {key}
        ui.set_busy(True)
        pos = ui.table.visualItemRect(ui.table.item(0, 0)).center()
        QTest.mouseClick(ui.table.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        assert {f.key for f in ui.store.friends(ui.account) if f.selected} == {key}
    finally:
        ui.close()


def test_personal_message_does_not_replace_global_template(tmp_path, app):
    ui = window(tmp_path, app)
    ui.text_edit.setPlainText('给大家的问候 🔥')
    ui.save_current()
    ui.scope.setCurrentIndex(1)
    ui.text_edit.setPlainText('只给你的问候 🧡')
    ui.save_current()
    assert ui.store.setting(ui.account, 'message')['value'] == '给大家的问候 🔥'
    friend_key = ui.scope.currentData()
    assert next(f for f in ui.store.friends(ui.account) if f.key == friend_key).override.value == '只给你的问候 🧡'
    ui.close()


def test_blank_account_cannot_start_sending(tmp_path, app):
    ui = window(tmp_path, app)
    ui.account = ''
    ui.refresh()
    assert not ui.send_btn.isEnabled()
    ui.close()


def test_visiting_and_resetting_personal_scope_keeps_global_inheritance(tmp_path, app):
    ui = window(tmp_path, app)
    ui.scope.setCurrentIndex(1)
    key = ui.scope.currentData()
    ui.scope.setCurrentIndex(0)
    assert next(f for f in ui.store.friends(ui.account) if f.key == key).override is None
    ui.scope.setCurrentIndex(1)
    ui.text_edit.setPlainText('曾经的专属内容')
    ui.save_current()
    ui.reset_override()
    ui.scope.setCurrentIndex(0)
    ui.text_edit.setPlainText('新的统一消息')
    ui.save_current()
    ui.scope.setCurrentIndex(1)
    assert ui.text_edit.toPlainText() == '新的统一消息'
    assert next(f for f in ui.store.friends(ui.account) if f.key == key).override is None
    ui.close()


@pytest.mark.parametrize('draft', ['正在编辑，还没有保存 🧡', ''])
def test_friend_sync_preserves_unsaved_message(tmp_path, app, draft):
    ui = window(tmp_path, app)
    ui.text_edit.setPlainText(draft)
    ui.on_result('sync', ui.store.friends(ui.account))
    assert ui.text_edit.toPlainText() == draft
    ui.close()


def test_account_switch_does_not_carry_old_draft(tmp_path, app):
    ui = window(tmp_path, app)
    ui.text_edit.setPlainText('只属于原账号')
    ui.account = 'a-different-account'
    ui.refresh()
    assert ui.text_edit.toPlainText() != '只属于原账号'
    ui.close()


@pytest.mark.parametrize('stop_first', [False, True])
def test_repeated_sync_reuses_browser_and_application_close_releases_it(
        tmp_path, app, monkeypatch, chat_server, stop_first):
    from spark_mate import browser as browser_module
    from spark_mate.browser import account_key
    from spark_mate.secrets import Vault
    from spark_mate.storage import Store
    from spark_mate.ui import MainWindow

    monkeypatch.setattr(browser_module, 'HOME', chat_server.url)
    cookies = [{'name': 'sessionid', 'value': 'offline-fixture-only', 'domain': '.douyin.com', 'path': '/'}]
    Vault(tmp_path/'login.dpapi').save({'cookies': cookies, 'origins': []})
    with Store(tmp_path) as store:
        store.set_setting('', 'current_account', account_key(cookies))
    failures = []
    monkeypatch.setattr(MainWindow, 'on_failure', lambda _self, text: failures.append(text))
    ui = MainWindow(tmp_path)

    def wait_until(predicate):
        deadline = time.monotonic() + 15
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert predicate(), '桌面操作没有按时完成'

    try:
        if stop_first:
            from threading import Event
            chat_server.gate = Event()
            ui.start('sync')
            wait_until(lambda: chat_server.requests == 1)
            ui.stop()
            wait_until(lambda: ui.worker is None)
            chat_server.gate.set()
        for _ in range(2):
            ui.start('sync')
            wait_until(lambda: ui.worker is None)
            assert not failures
            assert ui.table.rowCount() == 2
            assert ui.sync_btn.isEnabled()
        assert chat_server.requests == 1
        ui.logout()
        wait_until(lambda: ui.worker is None)
        assert not failures
        assert not ui.account
        assert not (tmp_path/'login.dpapi').exists()
    finally:
        ui.close()
        wait_until(lambda: ui.closed)
