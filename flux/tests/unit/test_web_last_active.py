"""D942: Admin › Users can sort by each user's last activity -- their latest login, audited action,
or run start or end."""

from __future__ import annotations

import time

from flux_web.store import Store


def test_last_activity_is_the_latest_of_login_action_and_run(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    store.add_user("cy", "a third long secret")
    with store._db() as db:
        db.execute("INSERT INTO sessions VALUES ('s', (SELECT id FROM users WHERE name='ada'), 100, 9e9)")
        db.execute("INSERT INTO audit(t, user, action) VALUES (200, 'ada', 'edit')")
    store.add_run(store.user(name="bob"), "x", "db", "log", ["x"], {})
    got = store.last_activity()
    assert got["ada"] == 200 and abs(got["bob"] - time.time()) < 60 and "cy" not in got
