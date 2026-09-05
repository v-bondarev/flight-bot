"""Диффер: структурные изменения рейса → строки для пуша."""
from flight_bot.diff import diff_snapshots
from flight_bot.models import Boarding, Checkin, FlightSnapshot, Leg

DEP = "2026-09-06T00:25:00+03:00"
ARR = "2026-09-06T05:35:00+03:00"


def _snap(**kw) -> FlightSnapshot:
    base = dict(flight="SU2128", date="2026-09-06", direction="departure",
                origin_iata="SVO", dest_iata="AYT", status="",
                departure=Leg(plan=DEP), arrival=Leg(plan=ARR))
    base.update(kw)
    return FlightSnapshot(**base)


def test_no_change_empty():
    s = _snap(gate="129")
    assert diff_snapshots(s, s) == []


def test_gate_change_before_departure():
    assert diff_snapshots(_snap(gate="129"), _snap(gate="141")) == ["🚪 Выход: 129 → 141"]


def test_gate_change_after_departure_is_history():
    a = _snap(gate="129", departure=Leg(plan=DEP, fact="2026-09-06T00:30:00+03:00"))
    b = _snap(gate="141", departure=Leg(plan=DEP, fact="2026-09-06T00:30:00+03:00"))
    assert diff_snapshots(a, b) == []


def test_terminal_change():
    assert diff_snapshots(_snap(terminal="B"), _snap(terminal="C")) == ["🏢 Терминал: B → C"]


def test_small_shift_is_ignored():
    b = _snap(departure=Leg(plan=DEP, est="2026-09-06T00:28:00+03:00"))
    assert diff_snapshots(_snap(), b) == []


def test_departure_delay_by_estimate():
    b = _snap(departure=Leg(plan=DEP, est="2026-09-06T01:10:00+03:00"))
    assert diff_snapshots(_snap(), b) == ["⏱ Вылет: 00:25 → 01:10 (+45 мин)"]


def test_departed_event_beats_estimate():
    b = _snap(departure=Leg(plan=DEP, est="2026-09-06T00:40:00+03:00",
                            fact="2026-09-06T00:38:00+03:00"))
    assert diff_snapshots(_snap(), b) == ["✈️ Вылетел в 00:38 (план 00:25)"]


def test_arrived_event():
    b = _snap(arrival=Leg(plan=ARR, fact="2026-09-06T05:34:00+03:00"))
    assert diff_snapshots(_snap(), b) == ["🛬 Прилетел в 05:34 (план 05:35)"]


def test_checkin_opened_with_desks_and_terminal():
    b = _snap(terminal="B",
              checkin=Checkin(desks="312,314-316", start_fact="2026-09-05T17:26:00+03:00"))
    assert diff_snapshots(_snap(terminal="B"), b) == [
        "🛎 Регистрация открыта, стойки 312,314-316, терминал B"]


def test_boarding_started_with_gate():
    b = _snap(gate="101", boarding=Boarding(start_fact="2026-09-05T23:45:00+03:00"))
    assert diff_snapshots(_snap(gate="101"), b) == ["🚶 Посадка началась, выход 101"]


def test_cancellation_is_the_only_message():
    b = _snap(status="Рейс отменен", gate="141")
    assert diff_snapshots(_snap(gate="129"), b) == ["❌ Рейс отменён"]


def test_chronology_before_tiles():
    a = _snap(gate="129")
    b = _snap(gate="130", departure=Leg(plan=DEP, est="2026-09-06T00:55:00+03:00"))
    assert diff_snapshots(a, b) == ["⏱ Вылет: 00:25 → 00:55 (+30 мин)", "🚪 Выход: 129 → 130"]
