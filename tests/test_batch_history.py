from threading import Event

from spark_mate.models import Friend, Message, today
from spark_mate.service import run_batch
from spark_mate.storage import Store


class Transport:
    def __init__(self):
        self.sent = []

    def open_target(self, friend):
        pass

    def send(self, friend, message, trigger):
        trigger()
        self.sent.append(friend.key)


def test_skipped_names_reasons_and_batches_survive_restart_without_resending(tmp_path):
    plan = [(Friend('f1', '已经发送的朋友'), Message('text', 'hi')),
            (Friend('f2', '待确认的朋友'), Message('text', 'hi')),
            (Friend('f3', '新选择的朋友'), Message('text', 'hi'))]
    with Store(tmp_path) as store:
        for friend, message in plan[:2]:
            token = store.reserve('a', friend.key, today(), message)
            store.finish(token, 'sent' if friend.key == 'f1' else 'unknown', 'earlier')
        transport = Transport()
        result = run_batch(store, 'a', plan, transport, Event(), lambda _: None, interval=0)
        assert transport.sent == ['f3']
        assert [r['status'] for r in result] == ['skipped', 'skipped', 'sent']
        batch = result[0]['batch_id']
    with Store(tmp_path) as store:
        rows = store.history('a', batch_id=batch, status='skipped')
        assert {r['name'] for r in rows} == {'已经发送的朋友', '待确认的朋友'}
        reasons = {r['target']:r['detail'] for r in rows}
        assert '今天已发送' in reasons['f1']
        assert '待确认' in reasons['f2']
        assert store.delivery_statuses('a', today()) == {'f1':'sent', 'f2':'unknown', 'f3':'sent'}
        assert store.reserve('a', 'f1', today(), Message('text', 'again')) is None
        assert store.reserve('a', 'f2', today(), Message('text', 'again')) is None
        assert store.history('other', batch_id=batch) == []
        assert store.batches('a')[0]['total'] == 3


def test_repeated_all_skipped_batches_do_not_hide_real_delivery_state(tmp_path):
    plan = [(Friend('f', '朋友'), Message('text', 'hi'))]
    with Store(tmp_path) as store:
        run_batch(store, 'a', plan, Transport(), Event(), lambda _: None, interval=0)
        for _ in range(3):
            run_batch(store, 'a', plan, Transport(), Event(), lambda _: None, interval=0)
        assert len(store.history('a', status='skipped')) == 3
        assert store.delivery_statuses('a', today())['f'] == 'sent'
        assert len(store.batches('a')) == 4
        assert store.db.execute('SELECT count(*) FROM attempts').fetchone()[0] == 1


def test_legacy_attempts_remain_visible_and_skip_records_do_not_change_them(tmp_path):
    with Store(tmp_path) as store:
        store.save_friends('a', [Friend('f', '旧记录朋友')])
        token = store.reserve('a', 'f', today(), Message('text', 'old'))
        store.finish(token, 'sent', '原始成功回执')
        before = dict(store.db.execute('SELECT * FROM attempts WHERE id=?', (token,)).fetchone())
        run_batch(store, 'a', [(Friend('f', '旧记录朋友'), Message('text', 'new'))],
                  Transport(), Event(), lambda _: None, interval=0)
        after = dict(store.db.execute('SELECT * FROM attempts WHERE id=?', (token,)).fetchone())
        assert before == after
        assert len(store.history('a')) == 2
        assert store.history('a', status='sent')[0]['detail'] == '原始成功回执'


def test_selected_batch_shows_every_recipient_beyond_recent_history_limit(tmp_path):
    plan = [(Friend(str(i), f'模拟好友 {i}'), Message('text', 'hi')) for i in range(205)]
    with Store(tmp_path) as store:
        first = run_batch(store, 'a', plan, Transport(), Event(), lambda _: None, interval=0)
        second = run_batch(store, 'a', plan, None, Event(), lambda _: None, interval=0)
        assert len(store.history('a')) == 200
        assert len(store.history('a', batch_id=first[0]['batch_id'])) == 205
        skipped = store.history('a', batch_id=second[0]['batch_id'], status='skipped')
        assert {r['target'] for r in skipped} == {str(i) for i in range(205)}
