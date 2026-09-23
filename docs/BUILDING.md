# 源码运行与 Windows 构建

环境：Windows x64、Python 3.11 或更高。依赖版本固定在 `pyproject.toml`，发布包的依赖清单在 `dependencies.json`。

## 开发运行

```powershell
git clone https://github.com/kkray983681062-bit/douyin-spark-mate.git
cd douyin-spark-mate
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.browsers'
.\.venv\Scripts\python.exe -m playwright install chromium --no-shell
.\.venv\Scripts\python.exe -m spark_mate
```

`--demo` 使用独立的模拟数据，禁用真实发送。`--self-test outputs\smoke.json` 离线检查窗口、登录文件加密、内置浏览器和接口脚本加载，不登录、不发消息。

## 检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests tools
.\.venv\Scripts\python.exe -m spark_mate --self-test outputs\smoke.json
```

测试使用虚构身份、临时数据库和本地网页。离线测试验证程序逻辑；真实平台行为应由使用者在自己的账号上另行确认。

## 打包

```powershell
.\.venv\Scripts\python.exe tools\build.py
```

输出位于 `dist/<版本号>/`，包括程序目录、便携 ZIP、源码 ZIP 和 SHA256 清单。构建脚本使用隔离的 PATH，避免混入其他软件的 Qt / ICU DLL。打包不会上传到 GitHub。

安装器使用 [Inno Setup](https://jrsoftware.org/isdl.php)，当前构建版本见第三方说明。将编译器安装到 `tools\vendor\inno`，再运行：

```powershell
.\.venv\Scripts\python.exe tools\build.py --installer
```

已经构建的程序可用 `--archives-only --installer` 刷新说明、源码包及安装器。发行前应检查最终包内文件，不能仅依赖 `.gitignore`。

## 代码位置

| 目录 / 文件 | 作用 |
| --- | --- |
| `src/spark_mate/ui.py`、`composer.py` | 桌面界面与消息编辑 |
| `browser.py`、`page_scripts.js` | 登录、会话同步和网页方式发送 |
| `direct.py`、`im_bridge.js` | 文字 / emoji 接口适配与回执确认 |
| `service.py`、`worker.py` | 发送队列、取消和后台执行 |
| `accounts.py`、`storage.py`、`secrets.py` | 多账号目录、本地设置、历史和独立加密登录态 |
| `tests/` | 虚构数据及离线回归测试 |
| `tools/` | 图标准备、打包和安装测试 |

网页结构和 IM 模块可能变化，适配失败时应停止并明确提示，不自动降级重发不确定的消息。
