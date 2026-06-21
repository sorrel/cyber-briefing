from db import state


def test_weekly_not_delivered_initially(tmp_path):
    conn = state.get_connection(str(tmp_path / "s.db"))
    assert state.was_weekly_delivered_this_week(conn) is False


def test_mark_weekly_delivered_sets_flag(tmp_path):
    conn = state.get_connection(str(tmp_path / "s.db"))
    state.mark_weekly_delivered(conn)
    assert state.was_weekly_delivered_this_week(conn) is True
