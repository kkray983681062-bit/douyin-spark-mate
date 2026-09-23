from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMovie, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .models import Message
from .storage import Store


class Composer(QWidget):
    read_stickers = Signal()

    def __init__(self, store: Store):
        super().__init__()
        self.store, self.image_path, self.movie = store, '', None
        self.tabs = QTabWidget()
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText('写一句想说的话，也可以加上喜欢的 emoji…')
        self.text_edit.setMinimumHeight(145)
        self.text_edit.setMaximumHeight(180)
        text = QWidget()
        layout = QVBoxLayout(text)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.text_edit)
        row = QHBoxLayout()
        for emoji in ('🔥', '🧡', '🥰', '✨', '☀️', '🌙'):
            btn = QPushButton(emoji)
            btn.setFixedSize(42, 34)
            btn.clicked.connect(lambda checked=False, e=emoji: self.text_edit.insertPlainText(e))
            row.addWidget(btn)
        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()

        picture = QWidget()
        pl = QVBoxLayout(picture)
        pl.setContentsMargins(0, 0, 0, 0)
        self.image_preview = QLabel('添加一张喜欢的表情包')
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setFixedHeight(145)
        self.image_preview.setStyleSheet('background:#faf7f4;border:1px dashed #e8cabb;border-radius:10px;color:#ae8b79')
        self.file_name = QLabel('PNG / JPG / WebP / GIF · 20 MB 以内')
        self.file_name.setObjectName('muted')
        pl.addWidget(self.image_preview)
        pl.addWidget(self.file_name)
        choose = QPushButton('选择本地图片 / GIF')
        choose.clicked.connect(self.choose_image)
        pl.addWidget(choose)
        pl.addStretch()

        sticker = QWidget()
        sl = QVBoxLayout(sticker)
        sl.setContentsMargins(0, 0, 0, 0)
        hint = QLabel('从所选好友或群聊中读取可用表情，\n选择后再点击底部按钮发送。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        self.stickers = QComboBox()
        self.stickers.addItem('请先读取抖音表情', None)
        read = QPushButton('读取抖音原生表情')
        read.clicked.connect(self.read_stickers.emit)
        sl.addWidget(hint)
        sl.addSpacing(12)
        sl.addWidget(self.stickers)
        sl.addWidget(read)
        sl.addWidget(QLabel('读取和选择表情不会发送消息。', objectName='muted'))
        sl.addStretch()
        self.tabs.addTab(text, '文字 / emoji')
        self.tabs.addTab(picture, '图片表情')
        self.tabs.addTab(sticker, '抖音表情')
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.tabs)

    def choose_image(self):
        name, _ = QFileDialog.getOpenFileName(self, '选择表情包', '', '图片 (*.png *.jpg *.jpeg *.gif *.webp)')
        if name:
            try:
                self.set_image(self.store.import_media(Path(name)))
            except ValueError as exc:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self, '图片无法添加', str(exc))

    def set_image(self, path: str):
        self.image_path = path
        if self.movie:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.image_preview.clear()
            self.image_preview.setText('图片已丢失，请重新选择')
        else:
            self.image_preview.setPixmap(pixmap.scaled(280, 130, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
            if Path(path).suffix.lower() == '.gif':
                self.movie = QMovie(path)
                self.movie.setScaledSize(pixmap.size().scaled(280, 130, Qt.AspectRatioMode.KeepAspectRatio))
                self.image_preview.setMovie(self.movie)
                self.movie.start()
        self.file_name.setText(Path(path).name)

    def set_stickers(self, rows: list[dict]):
        previous = self.stickers.currentData()
        self.stickers.clear()
        for row in rows:
            self.stickers.addItem(row['name'], row)
        if not rows:
            self.stickers.addItem('请先读取抖音表情', None)
        if previous:
            for i in range(self.stickers.count()):
                if self.stickers.itemData(i) == previous:
                    self.stickers.setCurrentIndex(i)

    def message(self, *, validate=True) -> Message:
        tab = self.tabs.currentIndex()
        if tab == 0:
            msg = Message('text', self.text_edit.toPlainText())
        elif tab == 1:
            msg = Message('image', self.image_path)
        else:
            row = self.stickers.currentData()
            if not row and validate:
                raise ValueError('请先读取并选择一个抖音原生表情')
            row = row or {'name': '', 'resource': ''}
            msg = Message('sticker', row['name'], row['resource'], row.get('category', ''))
        if validate:
            msg.validate()
        return msg

    def load(self, message: Message):
        if message.kind == 'text':
            self.tabs.setCurrentIndex(0)
            self.text_edit.setPlainText(message.value)
        elif message.kind == 'image':
            self.tabs.setCurrentIndex(1)
            self.set_image(message.value)
        else:
            self.tabs.setCurrentIndex(2)
            if not message.value:
                self.stickers.setCurrentIndex(0)
                return
            row = {'name': message.value, 'resource': message.resource, 'category': message.category}
            index = next((i for i in range(self.stickers.count()) if self.stickers.itemData(i)
                          and self.stickers.itemData(i).get('resource') == message.resource
                          and self.stickers.itemData(i).get('name') == message.value), -1)
            if index < 0:
                self.stickers.addItem(message.value, row)
                index = self.stickers.count()-1
            self.stickers.setCurrentIndex(index)
