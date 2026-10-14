import pytest

from archive_ferry.progress import Meter, fmt_eta, fmt_speed


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_meter_speed_and_eta():
    clock = Clock()
    meter = Meter(total=1000, clock=clock)
    assert meter.speed is None and meter.eta is None
    for _ in range(4):
        clock.now += 1
        meter.update(meter.done + 100)
    assert meter.speed == pytest.approx(100)
    assert meter.eta == pytest.approx(6)
    assert meter.average == pytest.approx(100)


def test_meter_uses_recent_window():
    clock = Clock()
    meter = Meter(total=10_000, clock=clock, window=10)
    for _ in range(20):
        clock.now += 1
        meter.update(meter.done + 10)
    for _ in range(20):
        clock.now += 1
        meter.update(meter.done + 100)
    assert meter.speed == pytest.approx(100)


def test_formats():
    assert fmt_speed(1_250_000) == "1.2 MB/s (10.0 Mbit/s)"
    assert fmt_eta(30) == "less than 1 min left"
    assert fmt_eta(600) == "about 10 min left"
    assert fmt_eta(5400) == "about 1 h 30 min left"
    assert fmt_eta(None) == "time left unknown"
