import importlib.util
from threading import Event

import pytest

from spark_mate.models import Friend, Message, SendUnknown
from spark_mate.storage import Store


def service():
    assert importlib.util.find_spec('spark_mate.service'), '发送队列尚未实现'
    from spark_mate.service import build_plan, run_batch
    return build_plan, run_batch


class Transport:
    def __init__(self, outcome=None, cancel=None):
        self.outcome, self.cancel = outcome, cancel
        self.payloads = []

    def open_target(self, friend):
        self.target = friend.key

    def send(self, friend, message, on_trigger):
        on_trigger()
        self.payloads.append((friend.key, message.value))
        if self.cancel:
            self.cancel.set()
        if self.outcome:
            raise self.outcome


def test_only_selected_get_their_own_message_and_empty_selection_rejected():
    build_plan, _ = service()
    plan = build_plan([Friend('a', '一', selected=True, override=Message('text', '专属')),
                       Friend('b', '二'), Friend('c', '三', selected=True)], Message('text', '统一'))
    assert [(f.key, m.value) for f, m in plan] == [('a', '专属'), ('c', '统一')]
    with pytest.raises(ValueError):
        build_plan([Friend('b', '二')], Message('text', '统一'))


def test_unknown_send_survives_restart_and_is_not_repeated(tmp_path):
    _, run_batch = service()
    plan = [(Friend('a', '一'), Message('text', 'hi'))]
    with Store(tmp_path) as db:
        db.save_friends('owner', [plan[0][0]])
        run_batch(db, 'owner', plan, Transport(SendUnknown('未确认')), Event(), lambda _: None, interval=0)
    transport = Transport()
    with Store(tmp_path) as db:
        db.recover_inflight()
        run_batch(db, 'owner', plan, transport, Event(), lambda _: None, interval=0)
        assert db.history('owner')[0]['status'] == 'skipped'
        assert len(db.history('owner', status='unknown')) == 1
        assert len(db.history('owner')) == 2
        assert transport.payloads == []


def test_cancel_finishes_current_receipt_and_leaves_remaining_untouched(tmp_path):
    _, run_batch = service()
    cancel = Event()
    transport = Transport(cancel=cancel)
    plan = [(Friend('a', '一'), Message('text', 'hi')), (Friend('b', '二'), Message('text', 'hi'))]
    with Store(tmp_path) as db:
        db.save_friends('owner', [f for f, _ in plan])
        run_batch(db, 'owner', plan, transport, cancel, lambda _: None, interval=0)
        assert [(r['target'], r['status']) for r in db.history('owner')] == [('a', 'sent')]
    assert transport.payloads == [('a', 'hi')]


def test_account_change_before_trigger_prevents_any_message(tmp_path):
    _, run_batch = service()
    from spark_mate.models import LoginRequired
    plan = [(Friend('a', '一'), Message('text', 'hi'))]
    transport = Transport()
    def changed():
        raise LoginRequired('账号改变')
    with Store(tmp_path) as db:
        db.save_friends('owner', [plan[0][0]])
        run_batch(db, 'owner', plan, transport, Event(), lambda _: None, verify=changed, interval=0)
        assert not any(r['status'] == 'sent' for r in db.history('owner'))
        assert transport.payloads == []
