"""Mirrors test_gantt.js's scenarios so the Python CPM port (used for Excel export) and the
JS engine (used for the live Gantt view) agree on the same inputs."""

from schedule import compute_schedule


def t(**overrides):
    base = dict(id=1, blocked_by_id=None, dependency_type=None, lag_days=0,
                pinned=0, is_milestone=0, start_date=None, due="2026-08-10")
    base.update(overrides)
    return base


def test_simple_fs_chain():
    tasks = [
        t(id=1, start_date="2026-08-01", due="2026-08-04"),
        t(id=2, blocked_by_id=1, start_date="2026-08-04", due="2026-08-06"),
        t(id=3, blocked_by_id=2, start_date="2026-08-06", due="2026-08-07"),
    ]
    sched = compute_schedule(tasks)
    assert sched[1]["start"].isoformat() == "2026-08-01"
    assert sched[1]["finish"].isoformat() == "2026-08-04"
    assert sched[2]["start"].isoformat() == "2026-08-04"
    assert sched[3]["start"].isoformat() == "2026-08-06"
    assert all(sched[i]["critical"] for i in (1, 2, 3))


def test_fan_out_slack():
    tasks = [
        t(id=1, start_date="2026-08-01", due="2026-08-02"),
        t(id=2, blocked_by_id=1, start_date="2026-08-02", due="2026-08-05"),  # long leg
        t(id=3, blocked_by_id=1, start_date="2026-08-02", due="2026-08-03"),  # short leg
    ]
    sched = compute_schedule(tasks)
    assert sched[2]["critical"] is True
    assert sched[3]["critical"] is False
    assert sched[3]["slack_days"] > 0


def test_fs_with_lag():
    tasks = [
        t(id=1, start_date="2026-08-01", due="2026-08-02"),
        t(id=2, blocked_by_id=1, dependency_type="FS", lag_days=2, start_date="2026-08-04", due="2026-08-05"),
    ]
    sched = compute_schedule(tasks)
    assert sched[2]["start"].isoformat() == "2026-08-04"


def test_ss_dependency_type():
    tasks = [
        t(id=1, start_date="2026-08-01", due="2026-08-05"),
        t(id=2, blocked_by_id=1, dependency_type="SS", lag_days=1, start_date="2026-08-02", due="2026-08-03"),
    ]
    sched = compute_schedule(tasks)
    assert sched[2]["start"].isoformat() == "2026-08-02"


def test_pinned_task_keeps_own_dates():
    tasks = [
        t(id=1, start_date="2026-08-01", due="2026-08-10"),
        t(id=2, blocked_by_id=1, pinned=1, start_date="2026-08-02", due="2026-08-03"),
    ]
    sched = compute_schedule(tasks)
    assert sched[2]["start"].isoformat() == "2026-08-02"


def test_milestone_zero_duration():
    tasks = [t(id=1, is_milestone=1, start_date="2026-08-10", due="2026-08-10")]
    sched = compute_schedule(tasks)
    assert sched[1]["duration_days"] == 0
