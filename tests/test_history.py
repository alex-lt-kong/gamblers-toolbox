import os
import tempfile
from datetime import date, timedelta

from modules.pe_monitor import storage
from modules.pe_monitor.views import _history_rows, _interpolate_series


def _mkdb(rows):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    storage.init_db(path)
    for r in rows:
        storage.append_snapshot(path, "T", r)
    return path


def test_history_fills_window_start_gap():
    # Two forward_pe anchors 40 days apart over a daily price series. A window
    # starting inside the gap must still get an interpolated value at its first
    # row — the read reaches back to the prior anchor, then clips to the window.
    base = date(2020, 1, 1)
    rows = []
    for i in range(41):
        r = {"date": (base + timedelta(days=i)).isoformat(), "price": 100.0 + i}
        if i == 0:
            r["forward_pe"] = 10.0
        if i == 40:
            r["forward_pe"] = 20.0
        rows.append(r)
    db = _mkdb(rows)
    try:
        start = (base + timedelta(days=20)).isoformat()
        out = _history_rows(db, "T", start, None)
        assert out[0]["date"] == start
        assert out[0]["forward_pe"] is not None
        assert out[0]["forward_pe_interpolated"] is True
        assert all(r["date"] >= start for r in out)  # pre-window rows clipped off
    finally:
        os.unlink(db)


def test_interpolate_breaks_across_forward_loss():
    # June: profit; Aug & Sep: forecast loss (negative stored P/E); Oct: profit.
    # The line must break across the loss — never bridge it or plot a negative.
    rows = [
        {"date": "2023-06-15", "price": 68.0, "forward_pe": 115.0},  # eps ~ +0.59
        {"date": "2023-07-15", "price": 60.0},                       # gap day
        {"date": "2023-08-15", "price": 62.0, "forward_pe": -60.0},  # loss anchor
        {"date": "2023-09-15", "price": 64.0, "forward_pe": -58.0},  # loss anchor
        {"date": "2023-10-15", "price": 67.0, "forward_pe": 13.0},   # eps ~ +5.1
    ]
    by = {r["date"]: r.get("forward_pe")
          for r in _interpolate_series([dict(r) for r in rows], "forward_pe", "f_i")}
    assert by["2023-06-15"] == 115.0   # positive anchor kept
    assert by["2023-07-15"] is None    # span bounded by a loss -> gap
    assert by["2023-08-15"] is None    # loss anchor served as gap
    assert by["2023-09-15"] is None    # loss anchor served as gap
    assert by["2023-10-15"] == 13.0    # positive anchor kept
    assert all(v is None or v > 0 for v in by.values())  # never negative


def test_history_fills_custom_window_between_anchors():
    # A custom [start, end] window strictly between two sparse anchors must still
    # interpolate — the read reaches forward to the right anchor, not just back.
    base = date(2020, 1, 1)
    rows = []
    for i in range(41):
        r = {"date": (base + timedelta(days=i)).isoformat(), "price": 100.0 + i}
        if i == 0:
            r["forward_pe"] = 10.0
        if i == 40:
            r["forward_pe"] = 20.0
        rows.append(r)
    db = _mkdb(rows)
    try:
        s = (base + timedelta(days=15)).isoformat()
        e = (base + timedelta(days=20)).isoformat()
        out = _history_rows(db, "T", s, e)
        assert out and out[0]["date"] == s and out[-1]["date"] == e
        assert all(r["forward_pe"] is not None for r in out)   # interpolated, not blank
        assert all(s <= r["date"] <= e for r in out)           # clipped to the window
    finally:
        os.unlink(db)


def test_interpolate_nulls_zero_pe():
    # A zero P/E is a degenerate placeholder, not a value -> served as a gap, not y:0.
    rows = [
        {"date": "2026-01-01", "price": 100.0, "forward_pe": 0.0},   # edge zero
        {"date": "2026-02-01", "price": 110.0, "forward_pe": 10.0},
        {"date": "2026-03-01", "price": 120.0, "forward_pe": 12.0},
    ]
    by = {r["date"]: r.get("forward_pe")
          for r in _interpolate_series([dict(r) for r in rows], "forward_pe", "f_i")}
    assert by["2026-01-01"] is None     # zero nulled, not plotted at y:0
    assert by["2026-02-01"] == 10.0
