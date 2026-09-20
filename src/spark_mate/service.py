from __future__ import annotations

from threading import Event

from .models import (
    Cancelled,
    Friend,
    IdentityMismatch,
    LoginRequired,
    Message,
    SendFailed,
    SendUnknown,
    VerificationRequired,
    today,
)
from .storage import Store


def build_plan(friends: list[Friend], default: Message) -> list[tuple[Friend, Message]]:
    plan, seen = [], set()
    for friend in friends:
        if friend.selected and friend.key not in seen:
            message = friend.override or default
            message.validate()
            plan.append((friend, message))
            seen.add(friend.key)
    if not plan:
        raise ValueError('请先勾选要续火花的好友')
    return plan


def run_batch(store: Store, account: str, plan: list[tuple[Friend, Message]], transport,
              cancel: Event, progress, *, verify=lambda: None, interval=4) -> list[dict]:
    if not account:
        raise LoginRequired('请先登录抖音')
    for _, message in plan:
        message.validate()
    date = today()
    results = []
    batch = store.begin_batch(account, len(plan)) if not cancel.is_set() else None
    for index, (friend, message) in enumerate(plan):
        if cancel.is_set():
            break
        token = store.reserve(account, friend.key, date, message)
        row = {'key': friend.key, 'name': friend.name, 'done': index+1, 'total': len(plan), 'batch_id': batch}
        detail = store.record_batch_item(batch, index, friend, message, date, token)
        if not token:
            row.update(status='skipped', detail=detail)
            results.append(row)
            progress(row)
            continue
        triggered = False
        fatal = False
        def trigger(attempt=token):
            nonlocal triggered
            if cancel.is_set():
                raise Cancelled('已停止')
            if today() != date:
                raise Cancelled('已跨过北京时间零点，请重新确认发送任务')
            verify()
            store.mark_triggered(attempt)
            triggered = True
        try:
            verify()
            progress({**row, 'status': 'working', 'detail': '正在核对好友并发送'})
            transport.open_target(friend)
            transport.send(friend, message, trigger)
            if not triggered:
                raise SendUnknown('未取得本次发送操作记录')
            row.update(status='sent', detail='已取得发送确认')
        except Cancelled as exc:
            row.update(status='unknown' if triggered else 'cancelled', detail=str(exc))
            fatal = True
        except (LoginRequired, VerificationRequired, IdentityMismatch) as exc:
            row.update(status='unknown' if triggered else 'failed', detail=str(exc))
            fatal = True
        except SendFailed as exc:
            row.update(status='failed', detail=str(exc))
        except SendUnknown as exc:
            row.update(status='unknown', detail=str(exc))
        except Exception as exc:  # noqa: BLE001 -- persist uncertainty at the external-send boundary
            row.update(status='unknown' if triggered else 'failed', detail=str(exc)[:600])
        store.finish(token, row['status'], row['detail'])
        results.append(row)
        progress(row)
        if fatal:
            break
        if index < len(plan)-1 and cancel.wait(interval):
            break
    return results
