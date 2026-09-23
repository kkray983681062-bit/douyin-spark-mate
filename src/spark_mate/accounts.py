"""Local account catalogue; each authenticated state has its own DPAPI vault."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid

from .browser import account_key
from .direct import bind_account_identity
from .models import LoginRequired
from .secrets import Vault
from .storage import now


class Accounts:
    def __init__(self, store):
        self.store = store

    def vault(self, account):
        if not account:
            raise ValueError('请选择账号')
        name = hashlib.sha256(account.encode('utf-8')).hexdigest()
        return Vault(self.store.root/'accounts'/f'{name}.dpapi')

    def get(self, account):
        row = self.store.db.execute('SELECT * FROM accounts WHERE account=?', (account,)).fetchone()
        return dict(row) if row else None

    def all(self):
        return [{**dict(row), 'has_login': self.vault(row['account']).path.is_file()}
                for row in self.store.db.execute('SELECT * FROM accounts ORDER BY updated DESC,account')]

    def import_legacy(self):
        """Move only the former active vault; retain its existing database namespace."""
        if self.store.setting('', 'accounts_imported', False):
            return
        legacy = self.store.root/'login.dpapi'
        account = self.store.setting('', 'current_account', '')
        if account and legacy.is_file():
            target = self.vault(account).path
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_suffix('.tmp')
                temp.write_bytes(legacy.read_bytes())  # Already encrypted; never writes plaintext.
                os.replace(temp, target)
            if target.read_bytes() != legacy.read_bytes():
                raise ValueError('新旧登录文件不一致，已保留原文件，请先核对账号')
            uid = self.store.setting(account, 'im_user_id')
            uid = uid if isinstance(uid, str) and re.fullmatch(r'[1-9]\d*', uid) else None
            with self.store.db:
                self.store.db.execute('INSERT OR IGNORE INTO accounts VALUES (?,?,?,?,?)',
                                      (account, uid, '原有账号', 'saved', now()))
            # If interrupted here, the next launch repeats the same byte comparison.
            legacy.unlink()
        self.store.set_setting('', 'accounts_imported', True)

    def remember(self, user_id, state, *, preferred='', label=''):
        """The worker must verify this user against the server and IM SDK first."""
        if not isinstance(user_id, str) or not re.fullmatch(r'[1-9]\d*', user_id):
            raise LoginRequired('无法识别当前账号，请重新扫码')
        cookie_identity = account_key(state.get('cookies', []))
        existing = self.store.db.execute('SELECT * FROM accounts WHERE user_id=?', (user_id,)).fetchone()
        if not preferred and not existing:
            matches = []
            for item in self.all():
                if item['user_id']:
                    continue
                members = [set(f.key.split(':')[2:]) for f in self.store.friends(item['account'])
                           if f.conversation_type == 1 and re.fullmatch(r'0:1:\d+:\d+', f.key)]
                owners = set.intersection(*members) if members else set()
                if cookie_identity == item['account'] or owners == {user_id}:
                    matches.append(item['account'])
            if len(matches) > 1:
                raise LoginRequired('有多个旧档案可对应当前账号，请在“管理账号”中选择原档案重新扫码')
            preferred = matches[0] if matches else ''
        requested = self.get(preferred) if preferred else None
        if preferred and not requested:
            raise LoginRequired('所选账号不存在，请重新添加')
        if requested and requested['user_id'] and requested['user_id'] != user_id:
            raise LoginRequired('扫码账号与所选账号不同，原登录和记录未替换；请用“添加账号”添加其他账号')
        if requested and existing and existing['account'] != preferred:
            raise LoginRequired('该抖音账号已单独保存，请直接切换到对应账号，原记录未合并')
        if requested and not requested['user_id']:
            bind_account_identity(self.store, preferred, user_id, state['cookies'], verified_user_id=user_id)
        account = (existing['account'] if existing else preferred) or uuid.uuid4().hex
        previous = self.get(account)
        display = previous['label'] if previous else (label.strip()[:24] or f'账号 · {user_id[-6:]}')
        self.vault(account).save(state)
        with self.store.db:
            self.store.db.execute('''INSERT INTO accounts VALUES (?,?,?,?,?) ON CONFLICT(account) DO UPDATE SET
                user_id=excluded.user_id,status=excluded.status,updated=excluded.updated''',
                (account, user_id, display, 'saved', now()))
            self.store.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?,?)',
                                  (account, 'im_user_id', json.dumps(user_id)))
        return account

    def activate(self, account):
        if not self.get(account) or not self.vault(account).path.is_file():
            raise LoginRequired('该账号没有保存的登录，请重新扫码')
        self.store.set_setting('', 'current_account', account)

    def needs_login(self, account):
        with self.store.db:
            self.store.db.execute("UPDATE accounts SET status='needs_login' WHERE account=?", (account,))

    def rename(self, account, label):
        label = label.strip()
        if not label or len(label) > 24 or any(ord(char) < 32 for char in label):
            raise ValueError('账号备注请填写 1–24 个字符，不要换行')
        with self.store.db:
            self.store.db.execute('UPDATE accounts SET label=? WHERE account=?', (label, account))

    def forget(self, account):
        if not self.get(account):
            raise ValueError('所选账号不存在')
        self.vault(account).clear()
        with self.store.db:
            self.store.db.execute("UPDATE accounts SET status='forgotten' WHERE account=?", (account,))
            if self.store.setting('', 'current_account') == account:
                self.store.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?,?)',
                                      ('', 'current_account', json.dumps('')))
