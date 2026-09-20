"""Build local Windows distributions. Never uploads or publishes anything."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from spark_mate import APP_NAME, __version__

VERSION = __version__


def run(command):
    env = os.environ.copy()
    windows = Path(env.get('SystemRoot', 'C:/Windows'))
    env['PATH'] = os.pathsep.join(str(p) for p in (
        Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix)/'DLLs',
        windows/'System32', windows, windows/'System32/Wbem'))
    env['PYTHONNOUSERSITE'] = '1'
    for key in ('PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
        env.pop(key, None)
    subprocess.run([str(part) for part in command], cwd=ROOT, check=True, env=env)


def source_files():
    for name in ('README.md', 'README.en.md', 'CHANGELOG.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                 'pyproject.toml', '.gitignore', '.gitattributes', 'dependencies.json'):
        yield ROOT/name
    for folder in ('src/spark_mate', 'tools', 'tests', 'docs', 'licenses', '.github'):
        for path in (ROOT/folder).rglob('*'):
            if path.is_file() and not any(x in path.parts for x in ('vendor', '__pycache__')) and path.suffix != '.pyc':
                yield path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archives-only', action='store_true', help='Refresh docs, source and ZIP after an existing build')
    parser.add_argument('--installer', action='store_true', help='Also compile the current-user installer')
    args = parser.parse_args()
    dist = ROOT/'dist'/VERSION
    bundle = dist/APP_NAME
    executable = bundle/f'{APP_NAME}.exe'
    if not args.archives_only:
        run([sys.executable, ROOT/'tools/prepare_assets.py'])
        if not list((ROOT/'.browsers').glob('chromium-*/chrome-win64/chrome.exe')):
            raise RuntimeError('Run playwright install chromium --no-shell with the project browser path first')
        run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
             '--name', APP_NAME, '--icon', ROOT/'src/spark_mate/assets/icon.ico',
             '--distpath', dist, '--workpath', ROOT/'build/pyinstaller-isolated', '--specpath', ROOT/'build',
             '--paths', ROOT/'src', '--add-data', f'{ROOT}/src/spark_mate/page_scripts.js;spark_mate',
             '--add-data', f'{ROOT}/src/spark_mate/im_bridge.js;spark_mate',
             '--add-data', f'{ROOT}/src/spark_mate/assets;spark_mate/assets',
             '--add-data', f'{ROOT}/.browsers;browsers',
             '--exclude-module', 'PySide6.QtQuick', '--exclude-module', 'PySide6.QtQml',
             ROOT/'tools/launch.py'])
    if not executable.is_file():
        raise RuntimeError('Missing compiled executable')
    # Playwright's local installation bookkeeping is not needed at runtime.
    links = bundle/'_internal/browsers/.links'
    if links.is_dir():
        links.resolve().relative_to(bundle.resolve())
        shutil.rmtree(links)
    for name in ('README.md', 'README.en.md', 'CHANGELOG.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'dependencies.json'):
        shutil.copy2(ROOT/name, bundle/name)
    shutil.copytree(ROOT/'licenses', bundle/'licenses', dirs_exist_ok=True)
    shutil.copytree(ROOT/'docs', bundle/'docs', dirs_exist_ok=True)
    receipt = ROOT/'outputs/packaged-smoke.json'
    run([executable, '--self-test', receipt])
    smoke = json.loads(receipt.read_text(encoding='utf-8'))
    if not smoke.get('ok') or not smoke.get('frozen'):
        raise RuntimeError('Packaged smoke test did not pass')
    archives = []
    portable = dist/f'{APP_NAME}-{VERSION}-windows-x64-portable.zip'
    with zipfile.ZipFile(portable, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                z.write(path, path.relative_to(dist).as_posix())
    archives.append(portable)
    source = dist/f'{APP_NAME}-{VERSION}-source.zip'
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(set(source_files())):
            z.write(path, 'spark-mate/'+path.relative_to(ROOT).as_posix())
    archives.append(source)
    if args.installer:
        compiler = ROOT/'tools/vendor/inno/ISCC.exe'
        if not compiler.is_file():
            raise RuntimeError('Install Inno Setup in tools/vendor/inno or run its ISCC.exe on tools/installer.iss')
        run([compiler, f'/DAppVersion={VERSION}', ROOT/'tools/installer.iss'])
        archives.append(dist/f'{APP_NAME}-{VERSION}-windows-x64-setup.exe')
    hashes = []
    files = []
    for path in archives:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        hashes.append(f'{digest}  {path.name}')
        files.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest})
    (dist/'SHA256SUMS.txt').write_text('\n'.join(hashes)+'\n', encoding='utf-8')
    (dist/'build-receipt.json').write_text(json.dumps({
        'built_at': datetime.now(UTC).isoformat(), 'version': VERSION, 'files': files,
        'packaged_smoke': smoke, 'live_douyin_validated': False, 'published': False,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(files, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
