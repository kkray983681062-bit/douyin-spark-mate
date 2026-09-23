from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPersistentModelIndex, Qt, QUrl
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__
from .accounts import Accounts
from .composer import Composer
from .models import Friend, Message, today
from .service import build_plan
from .storage import Store
from .theme import STYLE
from .worker import Worker

LABELS = {'sent': '已发送', 'unknown': '待确认', 'failed': '失败', 'cancelled': '已停止',
          'queued': '等待中', 'sending': '发送中', 'working': '处理中', 'skipped': '已跳过'}


def label(text, kind='', wrap=False):
    result = QLabel(text)
    result.setObjectName(kind)
    result.setWordWrap(wrap)
    return result


def button(text, callback=None, kind=''):
    result = QPushButton(text)
    result.setObjectName(kind)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        result.clicked.connect(callback)
    return result


def card():
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(14)
    return frame, layout


class FriendTable(QTableWidget):
    """Keep native checkbox/keyboard behavior and extend mouse clicks to the row."""

    def mousePressEvent(self, event):
        self._pressed_check = None
        index = self.indexAt(event.position().toPoint())
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton and index.isValid():
            check = self.item(index.row(), 0)
            self._pressed_check = (QPersistentModelIndex(self.model().index(index.row(), 0)),
                                   check.checkState())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        pressed = getattr(self, '_pressed_check', None)
        self._pressed_check = None
        index = self.indexAt(event.position().toPoint())
        super().mouseReleaseEvent(event)
        if (self.isEnabled() and event.button() == Qt.MouseButton.LeftButton and pressed
                and pressed[0].isValid() and index.isValid() and index.row() == pressed[0].row()):
            # The native delegate may already have toggled a checkbox click.
            # Setting the intended final state also covers the other row cells once.
            state = Qt.CheckState.Unchecked if pressed[1] == Qt.CheckState.Checked else Qt.CheckState.Checked
            self.item(index.row(), 0).setCheckState(state)


class MainWindow(QMainWindow):
    def __init__(self, root: Path, *, demo=False):
        super().__init__()
        self.root, self.demo = Path(root), demo
        self.store = Store(root)
        self.store.recover_inflight()
        self.accounts = Accounts(self.store)
        if not demo:
            self.accounts.import_legacy()
        self.worker = None
        self.browser_worker = None
        self.closing = False
        self.closed = False
        self.loading = True
        self.loaded_scope = ''
        self.avatar_cache = {}
        self.network = QNetworkAccessManager(self)
        self.account = 'demo-account' if demo else self.store.setting('', 'current_account', '')
        if not demo and not self.accounts.get(self.account):
            self.account = ''
        if demo:
            self.store.save_friends(self.account, [Friend('demo-01', '小雨', streak='128'),
                Friend('demo-02', '阿远', streak='36'), Friend('demo-03', '周末去看海', streak='72'),
                Friend('demo-04', '橘子汽水', streak='15'), Friend('demo-05', '林同学', streak='9')])
        self.setWindowTitle(f'{APP_NAME} · {__version__}')
        available = self.screen().availableGeometry()
        self.resize(min(1210, max(780, available.width()-40)), min(820, max(560, available.height()-70)))
        self.setMinimumSize(780, 560)
        self.setStyleSheet(STYLE)
        root_widget = QWidget(objectName='root')
        root_widget.setMinimumSize(1050, 740)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(root_widget)
        self.setCentralWidget(scroll)
        root_layout = QHBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame(objectName='sidebar')
        sidebar.setFixedWidth(206)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 32, 20, 24)
        side.setSpacing(9)
        self.brand_logo = label('', 'brand-logo')
        self.brand_logo.setFixedSize(72, 72)
        self.brand_logo.setPixmap(QPixmap(str(Path(__file__).with_name('assets')/'icon.png')).scaled(
            72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(self.brand_logo)
        side.addSpacing(7)
        self.brand_name = label(APP_NAME, 'brand')
        side.addWidget(self.brand_name)
        side.addWidget(label('KEKEMI · SPARK MATE', 'muted'))
        side.addSpacing(35)
        self.pages = QStackedWidget()
        nav_group = QButtonGroup(self)
        for index, title in enumerate(('♡    续火花', '◷    发送记录', 'ⓘ    使用说明')):
            nav = button(title, kind='nav')
            nav.setCheckable(True)
            nav_group.addButton(nav, index)
            nav.clicked.connect(lambda checked=False, i=index: self.pages.setCurrentIndex(i))
            side.addWidget(nav)
            if index == 0:
                nav.setChecked(True)
        side.addStretch()
        self.account_label = label('', 'title')
        self.account_label.setWordWrap(True)
        side.addWidget(self.account_label)
        self.account_note = label('', 'muted', True)
        side.addWidget(self.account_note)
        self.account_select = QComboBox()
        self.account_select.setMinimumContentsLength(8)
        self.account_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.account_select.setToolTip('选择已保存的账号，核对成功后切换')
        self.account_select.activated.connect(
            lambda index: self.switch_account(self.account_select.itemData(index)))
        side.addWidget(self.account_select)
        account_actions = QHBoxLayout()
        self.add_account_btn = button('添加账号', lambda: self.account_action('add'), 'link')
        self.manage_accounts_btn = button('管理账号', self.manage_accounts, 'link')
        account_actions.addWidget(self.add_account_btn)
        account_actions.addWidget(self.manage_accounts_btn)
        side.addLayout(account_actions)
        self.login_btn = button('扫码登录抖音', self.login, 'primary')
        side.addWidget(self.login_btn)
        self.logout_btn = button('忘记当前登录', self.logout, 'link')
        self.logout_btn.setToolTip('删除当前账号的登录凭据，保留好友、模板和发送记录')
        side.addWidget(self.logout_btn)
        side.addSpacing(16)
        side.addWidget(label(f'v{__version__}  ·  免费使用', 'muted'))
        side.addWidget(label('登录状态仅保存在本机', 'muted'))
        root_layout.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 27, 28, 22)
        content_layout.setSpacing(19)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(label('今天，也别忘了彼此。', 'headline'))
        heading.addWidget(label('选好朋友，留一句问候，让惦记再多一天。', 'muted'))
        header.addLayout(heading)
        header.addStretch()
        self.chip = label('', 'chip')
        header.addWidget(self.chip)
        content_layout.addLayout(header)
        content_layout.addWidget(self.pages, 1)
        root_layout.addWidget(content, 1)

        task = QWidget()
        task_layout = QVBoxLayout(task)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(16)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        friends_card, friends_layout = card()
        friends_card.setMinimumWidth(340)
        title_row = QHBoxLayout()
        title_row.addWidget(label('选择好友 / 群聊', 'title'))
        title_row.addStretch()
        self.sync_btn = button('↻  同步好友', lambda: self.start('sync'), 'link')
        title_row.addWidget(self.sync_btn)
        friends_layout.addLayout(title_row)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索好友昵称或群名…')
        self.search.textChanged.connect(self.filter_friends)
        friends_layout.addWidget(self.search)
        select_row = QHBoxLayout()
        self.select_all = button('全选当前列表', lambda: self.select_visible(True), 'link')
        self.select_none = button('取消全部', lambda: self.select_visible(False, all_rows=True), 'link')
        select_row.addWidget(self.select_all)
        select_row.addWidget(self.select_none)
        select_row.addStretch()
        self.count_label = label('', 'muted')
        select_row.addWidget(self.count_label)
        friends_layout.addLayout(select_row)
        self.table = FriendTable(0, 3)
        self.table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.setHorizontalHeaderLabels(['选择', '好友 / 群聊', '今日状态'])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 43)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 80)
        self.table.itemChanged.connect(self.selection_changed)
        friends_layout.addWidget(self.table, 1)
        self.empty_label = label('扫码登录后，点击「同步好友」\n你的聊天联系人会显示在这里。', 'muted', True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        friends_layout.addWidget(self.empty_label)
        friends_layout.addWidget(label('火花会话优先 · 点击整行勾选或取消', 'muted'))
        columns.addWidget(friends_card, 1)

        self.message_card, ml = card()
        self.message_card.setMinimumWidth(370)
        ml.addWidget(label('准备一句问候', 'title'))
        self.scope = QComboBox()
        self.scope.currentIndexChanged.connect(self.scope_changed)
        ml.addWidget(self.scope)
        self.composer = Composer(self.store)
        self.composer.read_stickers.connect(self.read_stickers)
        self.text_edit = self.composer.text_edit
        ml.addWidget(self.composer)
        template_row = QHBoxLayout()
        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(125)
        self.template_combo.activated.connect(self.apply_template)
        template_row.addWidget(self.template_combo, 1)
        template_row.addWidget(button('存为模板', self.save_template, 'link'))
        ml.addLayout(template_row)
        save_row = QHBoxLayout()
        save_row.addWidget(button('保存内容', lambda: self.safely(self.save_current)))
        save_row.addWidget(button('恢复统一内容', self.reset_override, 'link'))
        save_row.addStretch()
        ml.addLayout(save_row)
        self.save_note = label('可为不同好友或群聊设置专属内容。', 'muted', True)
        ml.addWidget(self.save_note)
        ml.addStretch()
        ml.addWidget(label('文字 / emoji 无需逐个打开聊天页。\n当天已发送或待确认的会话会自动跳过。', 'muted', True))
        columns.addWidget(self.message_card, 1)
        task_layout.addLayout(columns, 1)

        bottom = QFrame(objectName='bar')
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(18, 14, 18, 12)
        action_row = QHBoxLayout()
        status_column = QVBoxLayout()
        self.summary = label('', 'title')
        self.status_label = label('先扫码登录，再选好要联系的人。', 'muted', True)
        status_column.addWidget(self.summary)
        status_column.addWidget(self.status_label)
        action_row.addLayout(status_column, 1)
        self.stop_btn = button('停止', self.stop)
        self.stop_btn.setEnabled(False)
        self.send_btn = button('🔥  一键续火花', self.send, 'primary')
        self.send_btn.setMinimumWidth(160)
        action_row.addWidget(self.stop_btn)
        action_row.addWidget(self.send_btn)
        bl.addLayout(action_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        bl.addWidget(self.progress)
        task_layout.addWidget(bottom)
        self.pages.addWidget(task)

        history_card, hl = card()
        hl.addWidget(label('发送记录', 'title'))
        hl.addWidget(label('每轮都会记录已发送、已跳过及原因。“待确认”请先核对聊天记录。', 'muted', True))
        history_filters = QHBoxLayout()
        self.history_batch = QComboBox()
        self.history_batch.addItem('全部轮次', None)
        self.history_batch.currentIndexChanged.connect(lambda _: self.refresh_history())
        history_filters.addWidget(self.history_batch, 1)
        self.history_status = QComboBox()
        self.history_status.addItem('全部结果', None)
        for key in ('skipped', 'sent', 'unknown', 'failed', 'cancelled'):
            self.history_status.addItem('只看' + LABELS[key], key)
        self.history_status.currentIndexChanged.connect(lambda _: self.refresh_history())
        history_filters.addWidget(self.history_status)
        hl.addLayout(history_filters)
        hl.addWidget(label('全部轮次显示最近 200 条；选择某一轮可查看该轮完整明细。', 'muted', True))
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(['时间', '好友 / 群聊', '结果', '说明'])
        self.history_table.verticalHeader().hide()
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.history_table.setWordWrap(True)
        hl.addWidget(self.history_table, 1)
        self.pages.addWidget(history_card)
        help_card, help_layout = card()
        help_layout.addWidget(label('把一句问候，留给在意的人。', 'title'))
        help_text = QTextBrowser()
        help_text.setStyleSheet('border:none;background:white;padding:12px;')
        help_text.setMarkdown('''### 开始使用
1. 点击左下角 **扫码登录抖音**，在抖音官方窗口使用手机扫码。验证码和短信验证需要本人完成。
2. 点击 **同步好友**，勾选想续火花的好友或群聊。群聊会单独标识，首次同步默认不勾选。
3. 编辑统一内容，也可以在下拉框中选某个好友或群聊设置专属内容。可以保存多个模板。
4. 点击 **一键续火花** 就会向勾选的会话发送。发送中可以停止，当前消息会先完成结果确认。

### 多账号切换
点击左侧 **添加账号**，为每个账号分别扫码；之后从账号下拉框选择，核对成功后切换。
**管理账号**可修改备注、重新扫码或忘记登录。好友、群聊、勾选、草稿、模板、记录和当天去重按账号保存。
发送、同步或登录期间不能切换；停止后需等待当前结果记录完成。切换本身不会发送消息。
登录失效时只需为对应账号重新扫码；网络故障可稍后重试。切换需要联网核对，不保证瞬间完成或登录永久有效。

### 三种内容
- **文字 / emoji**：输入文字，或点选常用 emoji。首次连接私信服务后，通过接口发送，无需逐个打开好友聊天页；登录窗口继续保留。
- **图片表情**：选择 PNG、JPG、WebP 或 GIF。当前仍通过聊天页发送。软件保存一份副本，原文件移动不影响已保存的素材。GIF 的动图效果以平台实际呈现为准。
- **抖音表情**：先选一个好友或群聊，再读取该会话里可用的原生表情。读取本身不会发送。

### 发送结果
**已发送**表示获得消息发送确认，不等于对方已读，也不保证火花已经点亮。火花状态以抖音显示为准。
**待确认**表示已执行发送但没拿到可靠回执，今天会自动跳过，避免重复。
**已跳过**会单独记录好友昵称或群名和原因。在“发送记录”里选择本轮，再选“只看已跳过”即可查看。
旧版没有保存的跳过明细不会自动补造，新版开始逐轮保存。
抖音需要验证或账号发生变化时，会停止本轮操作。网页改版也可能需要更新本软件。

### 本地数据
登录状态使用 Windows 当前用户加密，好友、模板和记录保存在本机。
忘记登录仅删除所选账号的本机登录凭据，保留好友、模板和历史；其他账号不受影响。源码包不包含你的账号数据。
本软件为个人好友互动工具，与抖音官方无隶属关系。
''')
        help_layout.addWidget(help_text)
        self.pages.addWidget(help_card)
        self.refresh()
        self.loading = False

    def safely(self, fn):
        try:
            return fn()
        except ValueError as exc:
            QMessageBox.information(self, '请检查一下', str(exc))
            return None

    def refresh(self):
        same_account = getattr(self, 'loaded_account', None) == self.account
        draft = self.composer.message(validate=False) if same_account else None
        saved_draft = self.store.setting(self.account, 'draft') if not same_account and self.account else None
        self.loading = True
        self.friends = self.store.friends(self.account) if self.account else []
        previous_scope = self.scope.currentData() if same_account else (saved_draft or {}).get('scope', '')
        self.scope.blockSignals(True)
        self.scope.clear()
        self.scope.addItem('统一消息 · 用于没有专属内容的会话', '')
        for f in self.friends:
            name = ('[群聊] ' if f.conversation_type == 2 else '') + f.name
            self.scope.addItem(f'给 {name} 的专属消息  · {f.key[-6:]}', f.key)
        pos = self.scope.findData(previous_scope)
        self.scope.setCurrentIndex(max(0, pos))
        self.scope.blockSignals(False)
        self.composer.set_stickers(self.store.setting(self.account, 'stickers', []))
        self.load_current()
        if draft is not None and self.loaded_scope == (previous_scope or ''):
            self.composer.load(draft)
        elif saved_draft and self.loaded_scope == saved_draft.get('scope', ''):
            self.composer.load(Message.from_dict(saved_draft.get('message')))
        self.refresh_templates()
        self.refresh_table()
        self.refresh_accounts()
        self.loading = False
        self.set_busy(self.worker is not None)

    def refresh_accounts(self):
        current = self.accounts.get(self.account)
        self.account_select.blockSignals(True)
        self.account_select.clear()
        self.account_select.addItem('选择已保存账号…', '')
        for item in self.accounts.all():
            status = '已保存' if item['has_login'] and item['status'] == 'saved' else '需扫码'
            suffix = (item['user_id'] or item['account'])[-6:]
            name = item['label']
            brief = self.account_select.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 65)
            display = name if name == f'账号 · {suffix}' else f'{brief} · {suffix}'
            self.account_select.addItem(display, item['account'])
            self.account_select.setItemData(self.account_select.count()-1,
                f'{name}\n账号标识末六位：{suffix}\n{status}', Qt.ItemDataRole.ToolTipRole)
        self.account_select.setCurrentIndex(max(0, self.account_select.findData(self.account)))
        self.account_select.blockSignals(False)
        title = current['label'] if current else ''
        needs_login = bool(current and current['status'] != 'saved')
        self.account_label.setText('演示账号' if self.demo else (f'当前：{title}' if current else '还没有登录'))
        self.account_note.setText('模拟联系人 · 不连接抖音' if self.demo else
            ('此账号需重新扫码，记录已保留' if needs_login else
             '已保存登录 · 操作前核对账号' if current else '添加账号后可记住登录并切换'))
        suffix = (current['user_id'] or current['account'])[-6:] if current else ''
        self.chip.setText('演示模式 · 模拟数据' if self.demo else
                         (f'●  {title[:12]} · {suffix}' if current else '○  等待扫码'))
        self.login_btn.setText('重新扫码登录' if needs_login else '打开抖音窗口' if self.account else '扫码登录抖音')

    def stash_draft(self):
        if self.account and not self.loading and not self.closed:
            self.store.set_setting(self.account, 'draft',
                {'scope': self.loaded_scope, 'message': self.composer.message(validate=False).as_dict()})

    def account_action(self, action, target=None):
        if self.worker:
            return
        self.stash_draft()
        self.start(action, target)

    def switch_account(self, target):
        if self.worker or not target:
            return
        # A selection is a request, not an active identity, until the worker verifies it.
        self.refresh_accounts()
        self.account_action('switch', target)

    def manage_accounts(self):
        if self.worker or self.demo:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('管理已保存账号')
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        picker = QComboBox()
        for item in self.accounts.all():
            suffix = (item['user_id'] or item['account'])[-6:]
            status = '已保存' if item['has_login'] and item['status'] == 'saved' else '需扫码'
            picker.addItem(f'{item["label"]} · {suffix} · {status}', item['account'])
        picker.setCurrentIndex(max(0, picker.findData(self.account)))
        layout.addWidget(picker)
        layout.addWidget(label('重新扫码必须是所选账号。\n忘记登录会保留该账号的好友、模板和记录。', 'muted', True))
        def launch(action):
            target = picker.currentData()
            dialog.accept()
            self.account_action(action, target)
        def rename():
            item = self.accounts.get(picker.currentData())
            name, ok = QInputDialog.getText(dialog, '账号备注', '备注（最多 24 字）：', text=item['label'])
            if ok:
                try:
                    self.accounts.rename(item['account'], name)
                except ValueError as exc:
                    QMessageBox.information(dialog, '请检查一下', str(exc))
                    return
                dialog.accept()
                self.refresh_accounts()
        layout.addWidget(button('切换到这个账号', lambda: launch('switch'), 'primary'))
        layout.addWidget(button('重新扫码登录这个账号', lambda: launch('reauth')))
        layout.addWidget(button('修改账号备注', rename))
        layout.addWidget(button('忘记这个账号的登录', lambda: launch('forget'), 'link'))
        dialog.exec()

    def refresh_templates(self):
        self.template_combo.clear()
        self.template_combo.addItem('选择已保存模板', None)
        for t in self.store.templates(self.account):
            self.template_combo.addItem(t['name'], t['message'])

    def refresh_table(self):
        self.friends = self.store.friends(self.account) if self.account else []
        statuses = self.store.delivery_statuses(self.account, today()) if self.account else {}
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.friends))
        for i, f in enumerate(self.friends):
            self.table.setRowHeight(i, 67)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            check.setData(Qt.ItemDataRole.UserRole, f.key)
            check.setCheckState(Qt.CheckState.Checked if f.selected else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, check)
            widget = QWidget()
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row = QHBoxLayout(widget)
            row.setContentsMargins(4, 7, 2, 7)
            row.setSpacing(10)
            avatar = label(f.name[:1], 'avatar')
            avatar.setFixedSize(36, 36)
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(avatar)
            texts = QVBoxLayout()
            texts.setSpacing(3)
            name = label(f.name)
            name.setStyleSheet('font-weight:600;')
            name.setToolTip(f.name + '\n会话标识：' + f.key)
            texts.addWidget(name)
            streak = f.streak.strip()
            detail = f'🔥 {streak}{" 天" if streak.isdecimal() else ""}' if streak else '抖音单聊'
            if f.conversation_type == 2:
                detail = f'群聊 · {detail}' if streak else '抖音群聊'
            if f.override:
                detail += '  ·  专属内容'
            texts.addWidget(label(detail, 'muted'))
            row.addLayout(texts, 1)
            self.table.setCellWidget(i, 1, widget)
            self.load_avatar(f.avatar, avatar)
            status = statuses.get(f.key)
            state = QTableWidgetItem(LABELS.get(status, '未发送'))
            state.setForeground(QColor('#45a48a' if status == 'sent' else '#b58235' if status == 'unknown' else '#8f96a3'))
            self.table.setItem(i, 2, state)
        self.table.blockSignals(False)
        self.empty_label.setVisible(not self.friends)
        self.table.setVisible(bool(self.friends))
        self.filter_friends()
        self.update_count()
        self.refresh_history()

    def refresh_history(self, preferred_batch=None):
        selected = preferred_batch or self.history_batch.currentData()
        self.history_batch.blockSignals(True)
        self.history_batch.clear()
        self.history_batch.addItem('全部轮次', None)
        for batch in self.store.batches(self.account) if self.account else []:
            stamp = datetime.fromisoformat(batch['created']).astimezone().strftime('%m-%d %H:%M:%S')
            self.history_batch.addItem(f'{stamp} · {batch["total"]} 个会话', batch['id'])
        self.history_batch.setCurrentIndex(max(0, self.history_batch.findData(selected)))
        self.history_batch.blockSignals(False)
        history = self.store.history(self.account, batch_id=self.history_batch.currentData(),
                                     status=self.history_status.currentData()) if self.account else []
        self.history_table.setRowCount(len(history))
        for i, r in enumerate(history):
            timestamp = datetime.fromisoformat(r['created']).astimezone().strftime('%m-%d %H:%M:%S')
            for col, value in enumerate((timestamp, r['name'], LABELS[r['status']], r['detail'])):
                self.history_table.setItem(i, col, QTableWidgetItem(value))
        self.history_table.resizeRowsToContents()

    def load_avatar(self, url, target):
        if not url:
            return
        if url in self.avatar_cache:
            target.setPixmap(self.avatar_cache[url])
            return
        parsed = QUrl(url)
        if parsed.scheme() != 'https' or not any(parsed.host().endswith(x) for x in
                ('.byteimg.com', '.douyinpic.com', '.pstatp.com', '.ibytedtos.com', '.bytegoofy.com')):
            return
        request = QNetworkRequest(parsed)
        request.setTransferTimeout(8000)
        reply = self.network.get(request)
        def done():
            if reply.error() == QNetworkReply.NetworkError.NoError:
                pix = QPixmap()
                if pix.loadFromData(reply.readAll()):
                    pix = pix.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
                    self.avatar_cache[url] = pix
                    try:
                        target.setPixmap(pix)
                    except RuntimeError:
                        pass
            reply.deleteLater()
        reply.finished.connect(done)

    def filter_friends(self):
        needle = self.search.text().strip().casefold()
        for index, friend in enumerate(self.friends):
            self.table.setRowHidden(index, needle not in friend.name.casefold())

    def selection_changed(self, item):
        if self.loading or item.column() != 0:
            return
        keys = [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.CheckState.Checked]
        self.store.select(self.account, keys)
        self.friends = self.store.friends(self.account)
        self.update_count()

    def select_visible(self, checked: bool, all_rows=False):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            if all_rows or not self.table.isRowHidden(r):
                self.table.item(r, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        if self.table.rowCount():
            self.selection_changed(self.table.item(0, 0))

    def update_count(self):
        count = sum(f.selected for f in self.friends)
        self.count_label.setText(f'{count} / {len(self.friends)}')
        groups = sum(f.selected and f.conversation_type == 2 for f in self.friends)
        self.summary.setText(f'已选 {count - groups} 位好友' + (f' · {groups} 个群聊' if groups else ''))
        saved = self.accounts.get(self.account)
        valid = not saved or saved['status'] == 'saved'
        self.send_btn.setEnabled(bool(self.account and count and valid and not self.worker and not self.demo))

    def persist_message(self, key, message):
        if key:
            self.store.set_override(self.account, key, message)
        else:
            self.store.set_setting(self.account, 'message', message.as_dict())

    def save_current(self):
        message = self.composer.message()
        self.persist_message(self.scope.currentData() or '', message)
        self.loaded_message = message
        self.stash_draft()
        self.save_note.setText('已保存到本机 ✓')
        self.refresh_table()
        return message

    def load_current(self):
        key = self.scope.currentData() or ''
        default = Message.from_dict(self.store.setting(self.account, 'message'))
        friend = next((f for f in self.friends if f.key == key), None)
        self.loaded_message = (friend.override if friend else None) or default
        self.composer.load(self.loaded_message)
        self.loaded_scope = key
        self.loaded_account = self.account

    def scope_changed(self):
        if self.loading:
            return
        try:
            message = self.composer.message()
            if message != self.loaded_message:
                self.persist_message(self.loaded_scope, message)
        except ValueError:
            pass
        self.friends = self.store.friends(self.account)
        self.load_current()
        self.save_note.setText('这里的内容仅用于该会话。' if self.loaded_scope else '没有专属内容的会话将使用统一消息。')

    def reset_override(self):
        key = self.scope.currentData()
        if key:
            self.store.set_override(self.account, key, None)
            self.friends = self.store.friends(self.account)
            self.load_current()
            self.refresh_table()
            self.save_note.setText('已恢复使用统一内容')

    def apply_template(self, index):
        data = self.template_combo.itemData(index)
        if data:
            self.composer.load(Message.from_dict(data))
            self.safely(self.save_current)

    def save_template(self):
        def save():
            message = self.composer.message()
            name, ok = QInputDialog.getText(self, '保存模板', '给这条问候起个名字：')
            if ok and name.strip():
                self.store.save_template(self.account, name, message)
                self.refresh_templates()
                self.save_note.setText('模板已保存 ✓')
        self.safely(save)

    def login(self):
        current = self.accounts.get(self.account)
        if current and current['status'] != 'saved':
            self.account_action('reauth', self.account)
        else:
            self.start('open' if self.account else 'login')

    def logout(self):
        self.account_action('logout')

    def read_stickers(self):
        key = self.scope.currentData()
        target = next((f for f in self.friends if f.key == key), None)
        target = target or next((f for f in self.friends if f.selected), None)
        if target:
            self.start('stickers', target)
        else:
            QMessageBox.information(self, '先选择一个会话', '请勾选一个好友或群聊，用于读取该聊天中的可用表情。')

    def send(self):
        if self.worker or self.demo or not self.account:
            return
        def begin():
            self.save_current()
            default = Message.from_dict(self.store.setting(self.account, 'message'))
            plan = build_plan(self.store.friends(self.account), default)
            self.start('send', plan)
        self.safely(begin)

    def start(self, action, payload=None):
        if self.worker:
            return
        if self.demo:
            self.status_label.setText('这是界面演示，未连接真实抖音账号。')
            return
        if action not in {'login', 'add', 'switch', 'reauth', 'forget'} and not self.account:
            QMessageBox.information(self, '先登录抖音', '请点击左下角扫码登录。')
            return
        if self.browser_worker is None:
            self.browser_worker = Worker(self.root)
            self.browser_worker.status.connect(self.status_label.setText)
            self.browser_worker.step.connect(self.on_step)
            self.browser_worker.failure.connect(self.on_failure)
            self.browser_worker.result.connect(self.on_result)
            self.browser_worker.operation_finished.connect(self.on_finished)
            self.browser_worker.finished.connect(self.on_browser_closed)
            self.browser_worker.start()
        self.worker = self.browser_worker
        self.set_busy(True)
        self.progress.setRange(0, 0)
        self.status_label.setText({'login': '正在打开抖音官方登录窗口…', 'sync': '正在同步好友和群聊…',
                                  'stickers': '正在读取可用原生表情…', 'send': '正在准备发送…',
                                  'open': '正在打开已有抖音窗口…', 'logout': '正在忘记当前登录…',
                                  'add': '正在添加账号，请在独立窗口扫码…',
                                  'reauth': '正在重新扫码，请登录所选账号…',
                                  'switch': '正在恢复登录并核对目标账号，完成前仍保留原账号…',
                                  'forget': '正在忘记所选账号登录，保留记录…'}[action])
        if not self.worker.submit(action, self.account, payload):
            self.status_label.setText('上一项操作尚未结束，请等待或先点击停止。')

    def set_busy(self, busy):
        for widget in (self.table, self.search, self.select_all, self.select_none):
            widget.setEnabled(not busy)
        self.message_card.setEnabled(not busy and bool(self.account))
        self.login_btn.setEnabled(not busy and not self.demo)
        self.logout_btn.setEnabled(not busy and bool(self.account) and not self.demo)
        self.account_select.setEnabled(not busy and not self.demo)
        self.add_account_btn.setEnabled(not busy and not self.demo)
        self.manage_accounts_btn.setEnabled(not busy and bool(self.accounts.all()) and not self.demo)
        self.sync_btn.setEnabled(not busy and bool(self.account) and not self.demo)
        self.stop_btn.setEnabled(busy)
        self.update_count()

    def on_step(self, row):
        self.progress.setRange(0, row['total'])
        self.progress.setValue(row['done']-1 if row['status'] == 'working' else row['done'])
        self.status_label.setText(f'{row["name"]} · {row["detail"]}')
        if row['status'] != 'working':
            self.refresh_table()

    def on_result(self, action, value):
        if action in {'logout', 'forget'}:
            self.account = value or ''
            self.store.set_setting('', 'current_account', self.account)
            self.refresh()
            self.status_label.setText('已忘记所选账号的登录，好友、模板和记录仍保留；其他账号不受影响。')
        elif action == 'open':
            self.status_label.setText('抖音窗口已打开。可以慢慢等待网页加载，再点击「同步好友」。')
        elif action in {'login', 'add', 'switch', 'reauth'}:
            self.account = value
            self.store.set_setting('', 'current_account', value)
            self.refresh()
            self.status_label.setText('账号核对完成，登录已独立加密保存。可同步好友；切换后不会自动发送。')
        elif action == 'sync':
            self.store.save_friends(self.account, value)
            self.refresh()
            groups = sum(f.conversation_type == 2 for f in value)
            self.status_label.setText(f'本次同步 {len(value) - groups} 位好友、{groups} 个群聊，请勾选要联系的对象。')
        elif action == 'stickers':
            self.store.set_setting(self.account, 'stickers', value)
            self.composer.set_stickers(value)
            self.status_label.setText(f'已读取 {len(value)} 个原生表情，没有发送消息。')
        elif action == 'send':
            counts = Counter(row['status'] for row in value)
            summary = ' · '.join(f'{LABELS[key]} {count}' for key, count in counts.items()) or '本轮未发送'
            self.status_label.setText(summary + '。在「发送记录」可按本轮、已跳过筛选。')
            self.refresh_table()
            if value and value[0].get('batch_id'):
                self.refresh_history(value[0]['batch_id'])

    def on_failure(self, message):
        self.refresh_accounts()
        self.status_label.setText(message)
        if not self.closing:
            QMessageBox.information(self, '本次操作未完成', message)

    def on_finished(self):
        self.worker = None
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.refresh_accounts()
        self.set_busy(False)
        if self.closing:
            self.close()

    def on_browser_closed(self):
        worker = self.browser_worker
        self.browser_worker = None
        self.worker = None
        if worker:
            worker.deleteLater()
        if self.closing:
            self.close()
        else:
            self.set_busy(False)

    def stop(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.status_label.setText('正在停止；已触发的消息会先完成结果确认。')
            self.stop_btn.setEnabled(False)

    def closeEvent(self, event):
        self.stash_draft()
        if self.worker:
            self.closing = True
            self.stop()
            event.ignore()
            return
        if self.browser_worker:
            self.closing = True
            self.browser_worker.shutdown()
            event.ignore()
            return
        if not self.closed:
            self.closed = True
            self.store.db.close()
        event.accept()
