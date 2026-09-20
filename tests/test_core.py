from pathlib import Path

import pytest


def api():
    import importlib.util
    assert importlib.util.find_spec('spark_mate.storage'), '本地数据层尚未实现'
    from spark_mate.models import Friend, Message
    from spark_mate.storage import Store
    return Store, Friend, Message


def test_refresh_preserves_selection_override_and_missing_contacts(tmp_path):
    Store, Friend, Message = api()
    with Store(tmp_path) as db:
        db.save_friends('a', [Friend('c1', '小雨'), Friend('c2', '阿远')])
        db.select('a', ['c1'])
        db.set_override('a', 'c1', Message('text', '晚安 🌙'))
        db.save_friends('a', [Friend('c1', '小雨的新名字')])
        friends = {f.key: f for f in db.friends('a')}
        assert friends['c1'].selected
        assert friends['c1'].override.value == '晚安 🌙'
        assert friends['c1'].name == '小雨的新名字'
        assert 'c2' in friends


def test_streak_friends_come_first_by_days_and_keep_saved_choices(tmp_path):
    Store, Friend, Message = api()
    with Store(tmp_path) as db:
        db.save_friends('a', [
            Friend('none', 'A 普通好友'), Friend('nine', 'B 九天', streak='9'),
            Friend('hundred', 'Z 一百天', streak='100'),
            Friend('longest', 'Y 最久', streak='128 天'),
            Friend('spaces', 'C 普通好友', streak='  '),
            Friend('rekindle', 'D 重燃', streak='重燃中'),
            Friend('twenty', 'E 二十八天', streak=' 28 '),
        ])
        db.select('a', ['none', 'nine'])
        db.set_override('a', 'nine', Message('text', '专属问候'))
        friends = db.friends('a')
        assert [f.key for f in friends] == [
            'longest', 'hundred', 'twenty', 'nine', 'rekindle', 'none', 'spaces']
        assert {f.key for f in friends if f.selected} == {'none', 'nine'}
        assert next(f for f in friends if f.key == 'nine').override.value == '专属问候'
        db.save_friends('a', [Friend('nine', 'B 九天', streak='129')])
        assert db.friends('a')[0].key == 'nine'
        assert {f.key for f in db.friends('a') if f.selected} == {'none', 'nine'}


def test_accounts_never_share_recipients_or_daily_history(tmp_path):
    Store, Friend, Message = api()
    with Store(tmp_path) as db:
        db.save_friends('a', [Friend('c1', '同名')])
        db.save_friends('b', [Friend('c1', '同名')])
        db.select('a', ['c1'])
        assert not db.friends('b')[0].selected
        token = db.reserve('a', 'c1', '2026-09-20', Message('text', 'hi'))
        db.finish(token, 'sent', '已确认')
        assert db.reserve('b', 'c1', '2026-09-20', Message('text', 'hi'))
        assert db.reserve('a', 'c1', '2026-09-20', Message('text', 'another')) is None


def test_concurrent_connections_cannot_reserve_same_person_twice(tmp_path):
    Store, Friend, Message = api()
    with Store(tmp_path) as a, Store(tmp_path) as b:
        a.save_friends('owner', [Friend('c1', '好友')])
        token = a.reserve('owner', 'c1', '2026-09-20', Message('text', 'hi'))
        assert token
        assert b.reserve('owner', 'c1', '2026-09-20', Message('text', 'hi')) is None


def test_crash_after_trigger_blocks_resend_but_pretrigger_can_retry(tmp_path):
    Store, Friend, Message = api()
    with Store(tmp_path) as db:
        db.save_friends('a', [Friend('c1', '一'), Friend('c2', '二')])
        first = db.reserve('a', 'c1', '2026-09-20', Message('text', 'hi'))
        second = db.reserve('a', 'c2', '2026-09-20', Message('text', 'hi'))
        db.mark_triggered(first)
        db.recover_inflight()
        assert db.reserve('a', 'c1', '2026-09-20', Message('text', 'hi')) is None
        assert db.reserve('a', 'c2', '2026-09-20', Message('text', 'hi'))
        history = {row['id']: row for row in db.history('a')}
        assert history[first]['status'] == 'unknown'
        assert history[second]['status'] == 'cancelled'


def test_template_and_override_survive_reopen(tmp_path):
    Store, _, Message = api()
    with Store(tmp_path) as db:
        db.save_template('a', '每日', Message('text', '今天也要开心呀 🧡'))
        db.set_setting('a', 'message', Message('sticker', '比心', resource='/obj/im-resource/x').as_dict())
    with Store(tmp_path) as db:
        assert db.templates('a')[0]['message']['value'] == '今天也要开心呀 🧡'
        assert db.setting('a', 'message')['resource'] == '/obj/im-resource/x'
        assert db.templates('b') == []


def test_message_validation_rejects_blank_and_missing_image(tmp_path):
    _, _, Message = api()
    for message in [Message('text', ' \n'), Message('image', str(tmp_path/'gone.png')),
                    Message('sticker', ''), Message('bad', 'value')]:
        with pytest.raises(ValueError):
            message.validate()


def test_imported_gif_survives_original_removal(tmp_path):
    Store, _, _ = api()
    original = tmp_path/'原始表情.gif'
    original.write_bytes(bytes.fromhex('47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b'))
    with Store(tmp_path/'data') as db:
        media = db.import_media(original)
        original.unlink()
        assert Path(media).read_bytes().startswith(b'GIF89a')
        bad = tmp_path/'伪装.png'
        bad.write_text('not an image')
        with pytest.raises(ValueError):
            db.import_media(bad)


def test_dpapi_roundtrip_and_corruption_never_become_blank_login(tmp_path):
    import importlib.util
    assert importlib.util.find_spec('spark_mate.secrets'), '加密凭据层尚未实现'
    from spark_mate.secrets import Vault
    vault = Vault(tmp_path/'login.dpapi')
    state = {'cookies': [{'name': 'sessionid', 'value': 'secret-test-value'}], 'origins': []}
    vault.save(state)
    assert b'secret-test-value' not in vault.path.read_bytes()
    assert vault.load() == state
    vault.path.write_bytes(b'broken')
    with pytest.raises(ValueError):
        vault.load()
