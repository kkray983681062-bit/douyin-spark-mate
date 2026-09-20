# 第三方组件

Spark Mate 自有代码使用 MIT 许可。下列独立组件保留各自许可；MIT 不替代其许可。具体构建版本见 `dependencies.json`，许可证副本见 `licenses/`。

| 组件 | 本次版本 | 许可与来源 |
| --- | --- | --- |
| Python | 构建清单中的 Python 3.11 | [PSF License](https://docs.python.org/3/license.html) |
| PySide6 Essentials / Shiboken / Qt | 6.11.2 | LGPL v3，本程序以独立 DLL 动态链接；[Qt licensing](https://doc.qt.io/qt-6/licensing.html) |
| Playwright Python / driver | 1.63.0 | Apache 2.0；[源码](https://github.com/microsoft/playwright-python) |
| pyee / greenlet / typing_extensions | 构建清单 | 上游的 MIT / PSF 兼容许可，副本随包附带 |
| 内置 Chrome for Testing | 153.0.8010.12，Playwright 1243 | Google Chrome 及其 Chromium/第三方组件，见浏览器 `ABOUT`、`chrome://terms`、`chrome://credits` |
| Playwright FFmpeg / Windows dependency helper | 1011 / 1007 | 浏览器配套组件；FFmpeg 许可副本在 `_internal/browsers/ffmpeg-1011/COPYING.LGPLv2.1`，[构建源码](https://github.com/microsoft/playwright/tree/main/browser_patches/ffmpeg) |
| PyInstaller 构建引导器 | 6.22.3 | GPL 2.0-or-later with bootloader exception；[许可](https://pyinstaller.org/en/stable/license.html) |
| Inno Setup 安装器 | 6.7.3 | 上游许可；[官方](https://jrsoftware.org/isinfo.php) |

## Qt / PySide 源码和替换

软件使用的 Qt 动态库、PySide6 绑定没有修改。许可证全文见 `licenses/LGPL-3.0.txt` 和 `licenses/GPL-3.0.txt`；本程序不限制为调试库修改而进行的逆向工程。

- [PySide / Shiboken 6.11.2 完整源代码](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/)
- [Qt 6.11.2 源代码模块](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/)
- [Qt 构建说明](https://doc.qt.io/qt-6/build-sources.html)
- [PySide 构建说明](https://doc.qt.io/qtforpython-6/building_from_source/index.html)

本程序采用可展开的目录包。关闭程序、备份安装目录后，可以用 ABI 兼容的构建替换 `_internal/PySide6/` 的 Qt DLL、插件和 PySide 绑定，以及 `_internal/shiboken6/`，再运行 `克克咪 火花搭子.exe`。也可以直接用本项目源码在自己的 Python 环境安装修改后的 PySide/Qt 并运行 `python -m spark_mate`。没有库签名锁定或自动还原限制。

用户提供的图标原图保存为 `src/spark_mate/assets/icon-source.jpg`。该图片及其图标格式副本不纳入项目代码的 MIT 授权。

发行包保留组件许可文件，并随附对应版本的上游源码访问地址及动态链接库替换说明。上游源码由各自项目维护，本仓库的 MIT 许可不替代这些组件的许可。
