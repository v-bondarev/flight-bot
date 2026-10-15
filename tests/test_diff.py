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
    assert diff_snapshots(_snap(gate="129"), _snap(gate="141")) == ["🚪 Новый выход на посадку в SVO: 141 (был 129)"]


def test_gate_change_after_departure_is_history():
    a = _snap(gate="129", departure=Leg(plan=DEP, fact="2026-09-06T00:30:00+03:00"))
    b = _snap(gate="141", departure=Leg(plan=DEP, fact="2026-09-06T00:30:00+03:00"))
    assert diff_snapshots(a, b) == []


def test_terminal_change():
    assert diff_snapshots(_snap(terminal="B"), _snap(terminal="C")) == ["🏢 Сменился терминал: теперь C, был B"]


def test_small_shift_is_ignored():
    b = _snap(departure=Leg(plan=DEP, est="2026-09-06T00:28:00+03:00"))
    assert diff_snapshots(_snap(), b) == []


def test_departure_delay_by_estimate():
    b = _snap(departure=Leg(plan=DEP, est="2026-09-06T01:10:00+03:00"))
    assert diff_snapshots(_snap(), b) == ["⏱ Вылет позже на 45 мин: теперь 01:10, было 00:25"]


def test_departed_event_beats_estimate():
    b = _snap(departure=Leg(plan=DEP, est="2026-09-06T00:40:00+03:00",
                            fact="2026-09-06T00:38:00+03:00"))
    assert diff_snapshots(_snap(), b) == ["✈️ Самолёт взлетел в 00:38 (по расписанию 00:25)"]


def test_arrived_event():
    b = _snap(arrival=Leg(plan=ARR, fact="2026-09-06T05:34:00+03:00"))
    assert diff_snapshots(_snap(), b) == ["🛬 Самолёт приземлился в 05:34 (по расписанию 05:35). С прибытием!"]


def test_checkin_opened_with_desks_and_terminal():
    b = _snap(terminal="B",
              checkin=Checkin(desks="312,314-316", start_fact="2026-09-05T17:26:00+03:00"))
    assert diff_snapshots(_snap(terminal="B"), b) == [
        "🛎 Началась регистрация: терминал B, стойки 312, 314-316"]


def test_boarding_started_with_gate():
    b = _snap(gate="101", boarding=Boarding(start_fact="2026-09-05T23:45:00+03:00"))
    assert diff_snapshots(_snap(gate="101"), b) == ["🚶 Началась посадка, выход 101. Пора к выходу!"]


def test_cancellation_is_the_only_message():
    b = _snap(status="Рейс отменен", gate="141")
    assert diff_snapshots(_snap(gate="129"), b) == [
        "❌ Рейс отменён. Уточни варианты у авиакомпании, а я больше за ним не слежу."]


def test_status_phase_change_only_when_nothing_else_and_no_digits():
    assert diff_snapshots(_snap(status="Идёт регистрация"), _snap(status="Идёт посадка")) == ["ℹ️ Идёт посадка"]
    # статус с временем (SVO) — молчим
    assert diff_snapshots(_snap(status="Регистрация в 18:25"), _snap(status="Регистрация в 18:30")) == []
    # есть структурное изменение — статус не дублируем
    assert diff_snapshots(_snap(status="Ожидается", gate="1"),
                          _snap(status="Идёт посадка", gate="2")) == ["🚪 Новый выход на посадку в SVO: 2 (был 1)"]


def test_chronology_before_tiles():
    a = _snap(gate="129")
    b = _snap(gate="130", departure=Leg(plan=DEP, est="2026-09-06T00:55:00+03:00"))
    assert diff_snapshots(a, b) == ["⏱ Вылет позже на 30 мин: теперь 00:55, было 00:25",
                                  "🚪 Новый выход на посадку в SVO: 130 (был 129)"]


def test_naive_time_takes_offset_of_its_pair():
    # Время без офсета (старый снимок в БД, табло без пояса) — местное время
    # того же аэропорта, что и у пары: смесь не роняет дифф TypeError'ом.
    a = _snap(departure=Leg(plan="2026-09-06T00:25:00"))
    b = _snap(departure=Leg(plan=DEP, est="2026-09-06T01:10:00+03:00"))
    assert diff_snapshots(a, b) == ["⏱ Вылет позже на 45 мин: теперь 01:10, было 00:25"]


def test_naive_time_outside_moscow_is_local():
    # Вылет из Новосибирска (+07:00): без офсета — местное, а не МСК.
    a = _snap(departure=Leg(plan="2026-09-06T10:00:00"))
    b = _snap(departure=Leg(plan="2026-09-06T10:00:00+07:00", est="2026-09-06T10:50:00+07:00"))
    assert diff_snapshots(a, b) == ["⏱ Вылет позже на 50 мин: теперь 10:50 (06:50 МСК), было 10:00"]


def test_shift_push_for_foreign_airport_shows_msk():
    from flight_bot.diff import diff_snapshots
    from flight_bot.models import FlightSnapshot, Leg
    base = dict(flight="EK770", date="2026-10-16", direction="departure", origin_iata="DXB",
                dest_iata="CPT", status="")
    a = FlightSnapshot(**base, departure=Leg(plan="2026-10-16T09:10:00+04:00"))
    b = FlightSnapshot(**base, departure=Leg(plan="2026-10-16T09:10:00+04:00", est="2026-10-16T09:40:00+04:00"))
    assert any("позже на 30 мин: теперь 09:40 (08:40 МСК), было 09:10" in line for line in diff_snapshots(a, b))


def test_earlier_departure_says_earlier():
    b = _snap(departure=Leg(plan=DEP, est="2026-09-06T00:10:00+03:00"))
    assert diff_snapshots(_snap(), b) == ["⏱ Вылет раньше на 15 мин: теперь 00:10, было 00:25"]


def test_first_gate_is_assigned_not_changed():
    assert diff_snapshots(_snap(), _snap(gate="141")) == ["🚪 Назначен выход на посадку в SVO: 141"]


def test_gate_change_names_airport_of_dxb_departure():
    # Пуш EK770 15.10.2026 «🚪 Выход: B24 → A17» не говорил, что за выход и где.
    a = _snap(flight="EK770", origin_iata="DXB", dest_iata="CPT", gate="B24")
    b = _snap(flight="EK770", origin_iata="DXB", dest_iata="CPT", gate="A17")
    assert diff_snapshots(a, b) == ["🚪 Новый выход на посадку в DXB: A17 (был B24)"]


def test_arrival_gate_is_named_arrival_gate_at_destination():
    a = _snap(direction="arrival", gate="3")
    b = _snap(direction="arrival", gate="5")
    assert diff_snapshots(a, b) == ["🚪 Новый гейт прилёта в AYT: 5 (был 3)"]


def test_gate_from_other_source_is_not_a_change():
    # Табло не ответило, снимок взят из резервного источника: выходы разных
    # источников несравнимы, «смена» была бы ложной.
    a = _snap(gate="B24", source="dubaiairports.ae")
    b = _snap(gate="A17", source="airlabs.co")
    assert diff_snapshots(a, b) == []


def test_boarding_finished_and_checkin_closed():
    a = _snap(checkin=Checkin(start_fact="2026-09-05T17:26:00+03:00"),
              boarding=Boarding(start_fact="2026-09-05T23:45:00+03:00"))
    b = _snap(checkin=Checkin(start_fact="2026-09-05T17:26:00+03:00", finish_fact="2026-09-05T23:40:00+03:00"),
              boarding=Boarding(start_fact="2026-09-05T23:45:00+03:00", finish_fact="2026-09-06T00:10:00+03:00"))
    assert diff_snapshots(a, b) == ["🔒 Регистрация закончилась в 23:40", "🔒 Посадка закончилась в 00:10"]


def test_belt_and_first_terminal():
    assert diff_snapshots(_snap(), _snap(baggage_belt="5", terminal="C")) == [
        "🏢 Назначен терминал C", "🧳 Багаж выдадут на ленте 5"]
