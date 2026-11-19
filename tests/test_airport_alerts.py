"""Алерты аэропортов: парсер ленты, окно/ночь и отправка по подпискам."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from flight_bot import airport_alerts as aa
from flight_bot import storage
from flight_bot.icons import ic
from flight_bot.models import FlightSnapshot, Leg

MSK = timezone(timedelta(hours=3))
SETTINGS = SimpleNamespace(airport_alert_window_h=12,
                           airport_alert_quiet_from=23,
                           airport_alert_quiet_to=7,
                           airport_alert_quiet_near_h=6)


def _page(*posts):
    out = []
    for pid, text, at, reply in posts:
        body = text.replace("\n", "<br/>")
        quote = (f'<a class="tgme_widget_message_reply" href="https://t.me/favt_info/{reply}">'
                 f'<div class="tgme_widget_message_text js-message_reply_text">ЦИТАТА</div></a>'
                 if reply else "")
        out.append(
            f'<div class="tgme_widget_message js-widget_message" data-post="favt_info/{pid}">'
            f'{quote}<div class="tgme_widget_message_text js-message_text" dir="auto">{body}</div>'
            f'<time datetime="{at}"></time></div>')
    return "".join(out)


CLOSED = ("⛔️ Аэропорты\n— ВНУКОВО\n— ДОМОДЕДОВО\n"
          "✈️ВВЕДЕНЫ временные ограничения на прием и выпуск воздушных судов.\n"
          "✈️Ограничения необходимы для обеспечения безопасности полетов.")
PARTIAL = ("🟡 Аэропорт ДОМОДЕДОВО\n"
           "принимает и отправляет рейсы по согласованию с соответствующими органами.")
LIFTED = "⬜️Аэропорт ВНУКОВО\n✈️СНЯТЫ ограничения на прием и выпуск воздушных судов."
UPD = ("▫️Аэропорт СОЧИ\n✈️ВВЕДЕНЫ временные ограничения на прием и выпуск воздушных судов.\n"
       "UPD. Ограничения сняты")
SUMMARY = ("Замминистра транспорта провел в Шереметьево оперштаб. "
           "Аэропорты принимают и отправляют рейсы по согласованию.")


def _snap(dep="2026-11-19T03:00:00+03:00", arr="2026-11-19T05:00:00+03:00", **kw):
    base = dict(flight="SU1234", date="2026-11-19", direction="departure",
                origin_iata="VKO", dest_iata="AER", status="",
                departure=Leg(plan=dep), arrival=Leg(plan=arr))
    base.update(kw)
    return FlightSnapshot(**base)


class Sends:
    def __init__(self):
        self.msgs = []
        self.ok = True

    async def __call__(self, chat_id, text, thread_id=None):
        self.msgs.append((chat_id, text))
        return self.ok


def _watcher(conn, page, send, now):
    async def fetch(url):
        return page if "before=" not in url else ""

    return aa.Watcher(conn, SETTINGS, send, fetch=fetch, now=lambda: now)


def test_parse_page_uses_own_text_not_reply_quote():
    page = _page((2, LIFTED, "2026-11-18T23:30:00+00:00", 1))
    posts = aa.parse_page(page)
    assert len(posts) == 1
    assert posts[0].reply_to == 1
    assert posts[0].text.startswith("⬜️Аэропорт ВНУКОВО")
    assert "ЦИТАТА" not in posts[0].text


def test_parse_closed_list_and_partial():
    closed = aa.parse_page(_page((10, CLOSED, "2026-11-18T23:00:00+00:00", None)))
    events = aa.parse_post(closed[0])
    assert {(e.iata, e.kind) for e in events} == {("VKO", "closed"), ("DME", "closed")}

    partial = aa.parse_page(_page((11, PARTIAL, "2026-11-18T23:10:00+00:00", None)))
    events = aa.parse_post(partial[0])
    assert [(e.iata, e.kind) for e in events] == [("DME", "partial")]


def test_parse_lifted_reply_and_upd():
    lifted = aa.parse_page(_page((12, LIFTED, "2026-11-18T23:20:00+00:00", 10)))
    ev = aa.parse_post(lifted[0])[0]
    assert ev.kind == "lifted" and ev.episode == 10

    upd = aa.parse_page(_page((13, UPD, "2026-11-18T23:30:00+00:00", None)))
    ev = aa.parse_post(upd[0])[0]
    assert ev.kind == "lifted" and ev.episode == 13


def test_summary_is_ignored():
    posts = aa.parse_page(_page((14, SUMMARY, "2026-11-18T23:40:00+00:00", None)))
    assert aa.parse_post(posts[0]) == []


def test_window_side_and_quiet():
    snap = _snap()
    at = datetime(2026, 11, 19, 2, 0, tzinfo=MSK)          # за 1 ч до вылета
    assert aa.in_window(snap, "VKO", at)
    assert not aa.in_window(snap, "VKO", at - timedelta(hours=14))  # за 15 ч
    assert not aa.is_quiet(at, snap, "VKO")                # до рейса 1 ч
    far = _snap(dep="2026-11-19T13:00:00+03:00")
    assert aa.is_quiet(at, far, "VKO")                     # 11 ч, ночь
    departed = _snap(departure=Leg(plan="2026-11-19T03:00:00+03:00",
                                   fact="2026-11-19T03:05:00+03:00"))
    assert not aa._side_ok(departed, "VKO")


def test_tick_silent_first_then_sends_and_dedupes():
    conn = storage.connect(":memory:")
    sid = storage.add(conn, 42, "SU1234", "2026-11-19",
                      origin_iata="VKO", dest_iata="AER")
    storage.set_last(conn, sid, _snap())
    send = Sends()
    now = datetime(2026, 11, 18, 23, 5, tzinfo=timezone.utc)

    # первый тик — только курсор, без пуша
    asyncio.run(_watcher(conn, _page((100, CLOSED, "2026-11-18T23:00:00+00:00", None)),
                         send, now).tick(now))
    assert send.msgs == []

    page = _page((100, CLOSED, "2026-11-18T23:00:00+00:00", None),
                 (101, CLOSED, "2026-11-18T23:10:00+00:00", None))
    asyncio.run(_watcher(conn, page, send, now).tick(now))
    assert len(send.msgs) == 1
    assert ic("flight-header") in send.msgs[0][1]
    assert f'{ic("status-cancelled")} Внуково (VKO)' in send.msgs[0][1]
    assert "ограничения с 02:10 МСК" in send.msgs[0][1]

    # повтор того же поста — без дубля
    asyncio.run(_watcher(conn, page, send, now).tick(now))
    assert len(send.msgs) == 1


def test_lift_sent_only_after_open_alert():
    conn = storage.connect(":memory:")
    sid = storage.add(conn, 42, "SU1234", "2026-11-19",
                      origin_iata="VKO", dest_iata="AER")
    storage.set_last(conn, sid, _snap())
    send = Sends()
    now = datetime(2026, 11, 18, 23, 5, tzinfo=timezone.utc)
    asyncio.run(_watcher(conn, _page((100, CLOSED, "2026-11-18T23:00:00+00:00", None)),
                         send, now).tick(now))
    page = _page((100, CLOSED, "2026-11-18T23:00:00+00:00", None),
                 (101, CLOSED, "2026-11-18T23:10:00+00:00", None),
                 (102, LIFTED, "2026-11-18T23:20:00+00:00", 101))
    asyncio.run(_watcher(conn, page, send, now).tick(now))
    assert len(send.msgs) == 2
    assert (f'{ic("status-arrived")} Внуково (VKO): ограничения сняты, '
            "были 10 мин") in send.msgs[1][1]


def test_outside_window_and_quiet_are_silent():
    # рейс через 2 дня — событие вне окна
    conn = storage.connect(":memory:")
    sid = storage.add(conn, 42, "SU1234", "2026-11-21",
                      origin_iata="VKO", dest_iata="AER")
    storage.set_last(conn, sid, _snap(dep="2026-11-21T03:00:00+03:00",
                                      arr="2026-11-21T05:00:00+03:00"))
    send = Sends()
    now = datetime(2026, 11, 18, 23, 5, tzinfo=timezone.utc)
    asyncio.run(_watcher(conn, _page((100, CLOSED, "2026-11-18T23:00:00+00:00", None)),
                         send, now).tick(now))
    page = _page((100, CLOSED, "2026-11-18T23:00:00+00:00", None),
                 (101, CLOSED, "2026-11-18T23:10:00+00:00", None))
    asyncio.run(_watcher(conn, page, send, now).tick(now))
    assert send.msgs == []

    # ночь, до рейса 11 ч — suppressed, без пуша
    conn = storage.connect(":memory:")
    sid = storage.add(conn, 42, "SU1234", "2026-11-19",
                      origin_iata="VKO", dest_iata="AER")
    storage.set_last(conn, sid, _snap(dep="2026-11-19T13:00:00+03:00",
                                      arr="2026-11-19T15:00:00+03:00"))
    send = Sends()
    asyncio.run(_watcher(conn, _page((200, CLOSED, "2026-11-18T23:00:00+00:00", None)),
                         send, now).tick(now))
    asyncio.run(_watcher(conn, _page((200, CLOSED, "2026-11-18T23:00:00+00:00", None),
                                     (201, CLOSED, "2026-11-18T23:10:00+00:00", None)),
                         send, now).tick(now))
    assert send.msgs == []
    assert storage.alert_sent(conn, 42, sid, "VKO", 201, "suppressed")


def test_set_last_fills_route_for_old_subscriptions():
    conn = storage.connect(":memory:")
    sid = storage.add(conn, 42, "SU1234", "2026-11-19")
    storage.set_last(conn, sid, _snap())
    row = storage.active_all(conn)[0]
    assert (row["origin_iata"], row["dest_iata"]) == ("VKO", "AER")


def test_subscriptions_match_by_iata():
    conn = storage.connect(":memory:")
    storage.add(conn, 1, "SU1", "2026-11-19", origin_iata="VKO", dest_iata="AER")
    storage.add(conn, 2, "SU2", "2026-11-19", origin_iata="LED", dest_iata="SVO")
    assert [r["chat_id"] for r in storage.subs_for_airport(conn, "VKO")] == [1]
    assert {r["chat_id"] for r in storage.subs_for_airport(conn, "SVO")} == {2}
