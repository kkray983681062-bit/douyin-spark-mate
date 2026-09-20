"""Convert the supplied image into application icon formats and prepare notices."""
from __future__ import annotations

import json
import shutil
import struct
import sys
import urllib.request
from importlib import metadata
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parents[1]


def download(url: str, path: Path) -> None:
    if path.is_file() and path.stat().st_size > 100:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read()
    path.write_bytes(data)


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    assets = ROOT/'src/spark_mate/assets'
    assets.mkdir(parents=True, exist_ok=True)
    original = QImage(str(assets/'icon-source.jpg'))
    if original.isNull():
        raise RuntimeError('Missing or unreadable supplied icon-source.jpg')
    frames = []
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        scaled = original.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        icon = QImage(size, size, QImage.Format.Format_ARGB32)
        icon.fill(0)
        painter = QPainter(icon)
        painter.drawImage((size-scaled.width())//2, (size-scaled.height())//2, scaled)
        painter.end()
        if size == 512:
            if not icon.save(str(assets/'icon.png')):
                raise RuntimeError('Could not create application PNG')
        else:
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not icon.save(buffer, 'PNG'):
                raise RuntimeError('Could not encode ICO frame')
            frames.append((size, bytes(buffer.data())))
    offset = 6 + 16 * len(frames)
    entries, payloads = [], []
    for size, data in frames:
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        payloads.append(data)
        offset += len(data)
    (assets/'icon.ico').write_bytes(struct.pack('<HHH', 0, 1, len(frames)) + b''.join(entries + payloads))
    notices = ROOT/'licenses'
    notices.mkdir(exist_ok=True)
    download('https://raw.githubusercontent.com/qt/qtbase/v6.11.2/LICENSES/LGPL-3.0-only.txt', notices/'LGPL-3.0.txt')
    download('https://raw.githubusercontent.com/qt/qtbase/v6.11.2/LICENSES/GPL-3.0-only.txt', notices/'GPL-3.0.txt')
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():
        shutil.copy2(python_license, notices/'Python-LICENSE.txt')
    packages = {}
    for name in ('PySide6-Essentials', 'shiboken6', 'playwright', 'pyee', 'greenlet', 'typing_extensions', 'pyinstaller'):
        dist = metadata.distribution(name)
        packages[name] = dist.version
        for item in dist.files or []:
            if 'dist-info' in str(item) and ('license' in str(item).lower() or item.name == 'METADATA'):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    target = notices/name/item.name
                    target.parent.mkdir(exist_ok=True)
                    shutil.copy2(source, target)
    import playwright
    driver = Path(playwright.__file__).parent/'driver'
    for relative in ('LICENSE', 'package/LICENSE', 'package/NOTICE'):
        shutil.copy2(driver/relative, notices/('Playwright-'+relative.replace('/', '-')))
    (ROOT/'dependencies.json').write_text(json.dumps({'python': sys.version, 'packages': packages}, indent=2), encoding='utf-8')
    app.processEvents()
    print('Prepared icon, license copies and dependency versions.')


if __name__ == '__main__':
    main()
