from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .models import Friend, Message, validate_image


def data_root() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SparkMate'


def now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'spark-mate.db', timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS friends (
                account TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL,
                avatar TEXT NOT NULL, streak TEXT NOT NULL, identity TEXT NOT NULL,
                selected INTEGER NOT NULL DEFAULT 0, override TEXT,
                conversation_type INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(account,key));
            CREATE TABLE IF NOT EXISTS settings (
                account TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(account,key));
            CREATE TABLE IF NOT EXISTS accounts (
                account TEXT PRIMARY KEY, user_id TEXT UNIQUE, label TEXT NOT NULL,
                status TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS templates (
                account TEXT NOT NULL, name TEXT NOT NULL, message TEXT NOT NULL,
                PRIMARY KEY(account,name));
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, target TEXT NOT NULL,
                day TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL,
                detail TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS prevent_duplicate ON attempts(account,target,day)
                WHERE status IN ('queued','sending','sent','unknown');
            CREATE TABLE IF NOT EXISTS send_batches (
                id TEXT PRIMARY KEY, account TEXT NOT NULL,
                created TEXT NOT NULL, total INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS batch_items (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, position INTEGER NOT NULL,
                account TEXT NOT NULL, target TEXT NOT NULL, name TEXT NOT NULL,
                day TEXT NOT NULL, message TEXT NOT NULL, attempt_id TEXT UNIQUE,
                blocked_attempt_id TEXT, detail TEXT NOT NULL, created TEXT NOT NULL,
                UNIQUE(batch_id,position));
        ''')
        # 0.1.5 stored single chats only. Add their default type without replacing
        # any selections, account settings, message templates, or send receipts.
        with self.db:
            columns = {r['name'] for r in self.db.execute('PRAGMA table_info(friends)')}
            if 'conversation_type' not in columns:
                self.db.execute('ALTER TABLE friends ADD COLUMN conversation_type INTEGER NOT NULL DEFAULT 1')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def save_friends(self, account: str, friends: list[Friend]) -> None:
        with self.db:
            for f in friends:
                self.db.execute('''INSERT INTO friends(account,key,name,avatar,streak,identity,conversation_type)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,key) DO UPDATE SET
                    name=excluded.name,avatar=excluded.avatar,streak=excluded.streak,
                    identity=excluded.identity,conversation_type=excluded.conversation_type''',
                    (account, f.key, f.name, f.avatar, f.streak, f.identity, f.conversation_type))

    def friends(self, account: str) -> list[Friend]:
        friends = [Friend(r['key'], r['name'], r['avatar'], r['streak'], r['identity'],
                          bool(r['selected']), Message.from_dict(json.loads(r['override'])) if r['override'] else None,
                          r['conversation_type'])
                   for r in self.db.execute('SELECT * FROM friends WHERE account=?', (account,))]

        def order(friend):
            streak = friend.streak.strip()
            days = re.fullmatch(r'(\d+)\s*(?:天)?', streak)
            return (not bool(streak), -int(days[1]) if days else 0, friend.name.casefold(), friend.key)

        return sorted(friends, key=order)

    def select(self, account: str, keys: list[str]) -> None:
        with self.db:
            self.db.execute('UPDATE friends SET selected=0 WHERE account=?', (account,))
            self.db.executemany('UPDATE friends SET selected=1 WHERE account=? AND key=?',
                                [(account, key) for key in keys])

    def set_override(self, account: str, key: str, message: Message | None) -> None:
        if message:
            message.validate()
        with self.db:
            self.db.execute('UPDATE friends SET override=? WHERE account=? AND key=?',
                            (json.dumps(message.as_dict(), ensure_ascii=False) if message else None, account, key))

    def set_setting(self, account: str, key: str, value) -> None:
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?,?)',
                            (account, key, json.dumps(value, ensure_ascii=False)))

    def setting(self, account: str, key: str, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE account=? AND key=?', (account, key)).fetchone()
        return json.loads(row[0]) if row else default

    def save_template(self, account: str, name: str, message: Message) -> None:
        if not name.strip():
            raise ValueError('请输入模板名称')
        message.validate()
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO templates VALUES (?,?,?)',
                            (account, name.strip(), json.dumps(message.as_dict(), ensure_ascii=False)))

    def templates(self, account: str) -> list[dict]:
        return [{'name': r['name'], 'message': json.loads(r['message'])} for r in self.db.execute(
            'SELECT * FROM templates WHERE account=? ORDER BY name', (account,))]

    def import_media(self, source: Path) -> str:
        source = Path(source)
        fmt = validate_image(source)
        data = source.read_bytes()
        folder = self.root/'media'
        folder.mkdir(exist_ok=True)
        target = folder/f'{hashlib.sha256(data).hexdigest()[:24]}.{fmt}'
        if not target.exists():
            temp = target.with_suffix('.tmp')
            temp.write_bytes(data)
            os.replace(temp, target)
        return str(target)

    def reserve(self, account: str, target: str, day: str, message: Message) -> str | None:
        message.validate()
        ident = uuid.uuid4().hex
        try:
            with self.db:
                self.db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?)',
                                (ident, account, target, day, json.dumps(message.as_dict(), ensure_ascii=False),
                                 'queued', '等待执行', now(), now()))
        except sqlite3.IntegrityError:
            return None
        return ident

    def mark_triggered(self, token: str) -> None:
        with self.db:
            cursor = self.db.execute("UPDATE attempts SET status='sending',updated=? WHERE id=? AND status='queued'",
                                     (now(), token))
            if cursor.rowcount != 1:
                raise ValueError('本次发送任务状态已改变，已停止')

    def finish(self, token: str, status: str, detail: str) -> None:
        if status not in {'sent', 'unknown', 'failed', 'cancelled'}:
            raise ValueError('无效的完成状态')
        with self.db:
            self.db.execute('UPDATE attempts SET status=?,detail=?,updated=? WHERE id=?',
                            (status, detail, now(), token))

    def recover_inflight(self) -> None:
        with self.db:
            self.db.execute("UPDATE attempts SET status='unknown',detail='上次发送中断，请核对聊天记录' WHERE status='sending'")
            self.db.execute("UPDATE attempts SET status='cancelled',detail='上次任务在发送前中断' WHERE status='queued'")

    def begin_batch(self, account: str, total: int) -> str:
        ident = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO send_batches VALUES (?,?,?,?)', (ident, account, now(), total))
        return ident

    def record_batch_item(self, batch: str, position: int, friend: Friend, message: Message,
                          day: str, attempt: str | None) -> str:
        owner = self.db.execute('SELECT account FROM send_batches WHERE id=?', (batch,)).fetchone()
        if not owner:
            raise ValueError('本轮发送记录不存在')
        account = owner['account']
        detail = ''
        blocked = None
        if attempt:
            item = self.db.execute('SELECT account,target FROM attempts WHERE id=?', (attempt,)).fetchone()
            if not item or item['account'] != account or item['target'] != friend.key:
                raise ValueError('发送记录的账号或好友不一致')
        else:
            prior = self.db.execute('''SELECT id,status,updated FROM attempts
                WHERE account=? AND target=? AND day=? AND status IN ('queued','sending','sent','unknown')
                ORDER BY created DESC,rowid DESC LIMIT 1''', (account, friend.key, day)).fetchone()
            if not prior:
                raise ValueError('未找到可核对的跳过原因，请刷新发送记录')
            blocked = prior['id']
            if prior['status'] == 'sent':
                stamp = datetime.fromisoformat(prior['updated']).astimezone().strftime('%H:%M:%S')
                detail = f'今天已发送（{stamp}），本轮跳过'
            elif prior['status'] == 'unknown':
                detail = '上次发送结果待确认，为避免重复，本轮跳过'
            else:
                detail = '已有发送任务等待或处理中，本轮跳过'
        with self.db:
            self.db.execute('INSERT INTO batch_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (uuid.uuid4().hex, batch, position, account, friend.key, friend.name, day,
                 json.dumps(message.as_dict(), ensure_ascii=False), attempt, blocked, detail, now()))
        return detail

    def batches(self, account: str, limit=50) -> list[dict]:
        return [dict(r) for r in self.db.execute('''SELECT * FROM send_batches
            WHERE account=? ORDER BY created DESC,rowid DESC LIMIT ?''', (account, limit))]

    def delivery_statuses(self, account: str, day: str) -> dict[str, str]:
        result = {}
        for row in self.db.execute('''SELECT target,status FROM attempts
                WHERE account=? AND day=? ORDER BY created DESC,rowid DESC''', (account, day)):
            result.setdefault(row['target'], row['status'])
        return result

    def history(self, account: str, limit=200, *, batch_id=None, status=None) -> list[dict]:
        # Batch events preserve display names and skips without altering the
        # authoritative attempts table or its same-day duplicate protection.
        sql = '''SELECT * FROM (
            SELECT COALESCE(a.id,i.id) AS id,i.account,i.target,i.day,i.message,
                COALESCE(a.status,'skipped') AS status,
                CASE WHEN i.attempt_id IS NULL THEN i.detail ELSE a.detail END AS detail,
                i.created,COALESCE(a.updated,i.created) AS updated,i.name,
                i.batch_id,b.rowid AS batch_order,i.position AS position
            FROM batch_items i JOIN send_batches b ON b.id=i.batch_id
            LEFT JOIN attempts a ON a.id=i.attempt_id AND a.account=i.account
            UNION ALL
            SELECT a.id,a.account,a.target,a.day,a.message,a.status,a.detail,a.created,a.updated,
                COALESCE(f.name,a.target) AS name,NULL AS batch_id,0 AS batch_order,a.rowid AS position
            FROM attempts a LEFT JOIN friends f ON a.account=f.account AND a.target=f.key
            WHERE NOT EXISTS (SELECT 1 FROM batch_items i WHERE i.attempt_id=a.id)
            ) WHERE account=?'''
        params: list = [account]
        if batch_id:
            sql += ' AND batch_id=?'
            params.append(batch_id)
        if status:
            sql += ' AND status=?'
            params.append(status)
        sql += ' ORDER BY created DESC,batch_order DESC,position DESC'
        if not batch_id:
            sql += ' LIMIT ?'
            params.append(limit)
        return [dict(r) for r in self.db.execute(sql, params)]
