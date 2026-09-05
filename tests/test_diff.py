"""Диффер: структурные изменения рейса → строки для пуша."""
from flight_bot.diff import diff_snapshots
from flight_bot.models import FlightSnapshot, Leg


def _snap(**kw) -> FlightSnapshot:
    base = dict(flight="SU2128", date="2026-09-06", direction="departure",
                origin_iata="SVO", dest_iata="AYT", status="")
    base.update(kw)
    return FlightSnapshot(**base)


def test_no_change_empty():
    s = _snap(gate="129", departure=Leg(plan="2026-09-06T00:25:00+03:00"))
    assert diff_snapshots(s, s) == []


def test_gate_change():
    a = _snap(gate="129")
    b = _snap(gate="141")
    assert diff_snapshots(a, b) == ["🚪 Выход: 129 → 141"]


def test_terminal_change():
    out = diff_snapshots(_snap(terminal="B"), _snap(terminal="C"))
    assert out == ["🏢 Терминал: B → C"]


def test_departure_delay_by_estimate():
    a = _snap(departure=Leg(plan="2026-09-06T00:25:00+03:00"))
    b = _snap(departure=Leg(plan="2026-09-06T00:25:00+03:00",
                            est="2026-09-06T01:10:00+03:00"))
    assert diff_snapshots(a, b) == ["⏱ Вылет: 00:25 → 01:10"]


def test_departed_event_beats_estimate():
    a = _snap(departure=Leg(plan="2026-09-06T00:25:00+03:00"))
    b = _snap(departure=Leg(plan="2026-09-06T00:25:00+03:00",
                            est="2026-09-06T00:40:00+03:00",
                            fact="2026-09-06T00:38:00+03:00"))
    assert diff_snapshots(a, b) == ["✈️ Вылет состоялся в 00:38 (план 00:25)"]


def test_arrived_event():
    a = _snap(arrival=Leg(plan="2026-09-06T05:35:00+03:00"))
    b = _snap(arrival=Leg(plan="2026-09-06T05:35:00+03:00",
                          fact="2026-09-06T05:34:00+03:00"))
    assert diff_snapshots(a, b) == ["✈️ Прилёт состоялся в 05:34 (план 05:35)"]


def test_multiple_changes_combined():
    a = _snap(gate="129", departure=Leg(plan="2026-09-06T00:25:00+03:00"))
    b = _snap(gate="130", departure=Leg(plan="2026-09-06T00:25:00+03:00",
                                        est="2026-09-06T00:55:00+03:00"))
    assert diff_snapshots(a, b) == ["🚪 Выход: 129 → 130", "⏱ Вылет: 00:25 → 00:55"]
