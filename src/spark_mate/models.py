from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


@dataclass(frozen=True)
class Message:
    kind: str = 'text'
    value: str = '今天也要开心呀 🔥'
    resource: str = ''
    category: str = ''

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> Message:
        return cls(**{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__})

    def validate(self) -> None:
        if self.kind not in {'text', 'image', 'sticker'}:
            raise ValueError('不支持的消息类型')
        if not self.value.strip():
            raise ValueError('请先填写消息或选择表情')
        if self.kind == 'text' and len(self.value) > 1000:
            raise ValueError('本软件的单条文字请控制在 1000 字以内')
        if self.kind == 'image':
            validate_image(Path(self.value))

    def preview(self) -> str:
        if self.kind == 'image':
            return f'图片 · {Path(self.value).name}'
        if self.kind == 'sticker':
            return f'抖音表情 · {self.value}'
        return self.value


def validate_image(path: Path) -> str:
    from PySide6.QtGui import QImageReader
    if not path.is_file():
        raise ValueError('图片文件不存在，请重新选择')
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('请选择 20 MB 以内的图片')
    reader = QImageReader(str(path))
    fmt = bytes(reader.format()).decode('ascii', errors='ignore').lower()
    if not reader.canRead() or fmt not in {'png', 'jpg', 'jpeg', 'gif', 'webp'}:
        raise ValueError('请选择有效的 PNG、JPG、WebP 或 GIF 图片')
    if reader.size().width() * reader.size().height() > 40_000_000:
        raise ValueError('图片尺寸过大，请缩小后再添加')
    return 'jpg' if fmt == 'jpeg' else fmt


@dataclass(frozen=True)
class Friend:
    key: str
    name: str
    avatar: str = ''
    streak: str = ''
    identity: str = ''
    selected: bool = False
    override: Message | None = None
    conversation_type: int = 1


class Cancelled(Exception):
    pass


class LoginRequired(Exception):
    pass


class VerificationRequired(Exception):
    pass


class IdentityMismatch(Exception):
    pass


class SendUnknown(Exception):
    pass


class SendFailed(Exception):
    pass
