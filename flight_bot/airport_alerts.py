"""Алерты об ограничениях аэропортов по активным подпискам.

Источник — канал «Говорит Росавиация» (t.me/s/favt_info, зеркало MAX):
публичная HTML-версия без API. Посты шаблонные: «ВВЕДЕНЫ ограничения»,
«работает по согласованию», «СНЯТЫ ограничения» (обычно reply на пост о
введении). Снятие иногда дописывают в тот же пост строкой «UPD … сняты».

Модуль изолирован от поллера: свой тик, своё состояние в SQLite. Поллер
подписок только хранит IATA маршрута, по нему и матчим событие.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from flight_bot import storage
from flight_bot.config import Settings
from flight_bot.icons import ic
from flight_bot.models import FlightSnapshot, snap_from_dict
from flight_bot.render import human, route_line

log = logging.getLogger("flight_bot.airport_alerts")

CHANNEL_URL = "https://t.me/s/favt_info"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MSK = timezone(timedelta(hours=3))

STALE_EVENT_SEC = 30 * 60          # старше — только состояние, без пуша
REPEAT_EPISODE_SEC = 12 * 3600     # повтор «введены» в том же режиме — не новость
PENDING_TTL_SEC = 6 * 3600         # сколько держим неотправленный пуш

# IATA → (русское название, regex-варианты в постах Росавиации).
AIRPORTS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "VKO": ("Внуково", (r"ВНУКОВО",)),
    "DME": ("Домодедово", (r"ДОМОДЕДОВО",)),
    "SVO": ("Шереметьево", (r"ШЕРЕМЕТЬЕВО",)),
    "ZIA": ("Жуковский", (r"ЖУКОВСКИЙ", r"РАМЕНСКОЕ")),
    "LED": ("Пулково", (r"ПУЛКОВО",)),
    "AER": ("Сочи", (r"СОЧИ", r"АДЛЕР")),
}

Send = Callable[..., Awaitable[Optional[bool]]]
Fetch = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class Post:
    id: int
    at: str
    text: str
    reply_to: Optional[int] = None


@dataclass(frozen=True)
class Event:
    post_id: int
    at: str
    iata: str
    kind: str                  # closed | partial | lifted
    episode: Optional[int] = None


# --- Разбор ленты -----------------------------------------------------------

_WIDGET_RE = re.compile(r'js-widget_message" data-post="favt_info/(\d+)"')
_TEXT_RE = re.compile(r'js-message_text[^>]*>(.*?)</div>', re.S)
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_REPLY_RE = re.compile(
    r'tgme_widget_message_reply[^>]*href="https://t\.me/favt_info/(\d+)"')
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()


def parse_page(page: str) -> List[Post]:
    """Посты страницы t.me/s/<канал> — с своим текстом, а не reply-цитатой."""
    marks = list(_WIDGET_RE.finditer(page))
    out: List[Post] = []
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(page)
        body = page[start:end]
        own = _TEXT_RE.search(body)
        if not own:
            continue
        tm = _TIME_RE.search(body)
        reply = _REPLY_RE.search(body)
        out.append(Post(
            id=int(m.group(1)),
            at=tm.group(1) if tm else "",
            text=_clean(own.group(1)),
            reply_to=int(reply.group(1)) if reply else None,
        ))
    return out


def _template(text: str) -> bool:
    """Пост про ограничения, а не сводка оперштаба с упоминанием аэропорта."""
    lines = [line.strip() for line in text.split("\n") if line.strip()][:4]
    return any(re.match(r"^[^0-9A-Za-zА-Яа-я]*Аэропорт", line) for line in lines)


def _kind(text: str) -> Optional[str]:
    if re.search(r"UPD", text, re.I) and re.search(r"снят|открыт", text, re.I):
        return "lifted"
    if re.search(r"СНЯТЫ", text, re.I):
        return "lifted"
    if re.search(r"по согласованию", text, re.I):
        return "partial"
    if re.search(r"ВВЕДЕНЫ", text, re.I):
        return "closed"
    return None


def parse_post(post: Post) -> List[Event]:
    if not post.at or not _template(post.text):
        return []
    kind = _kind(post.text)
    if not kind:
        return []
    episode: Optional[int] = None
    if kind == "lifted":
        if post.reply_to:
            episode = post.reply_to
        elif re.search(r"UPD", post.text, re.I):
            episode = post.id
    up = post.text.upper()
    return [
        Event(post.id, post.at, iata, kind, episode)
        for iata, (_, pats) in AIRPORTS.items()
        if any(re.search(p, up) for p in pats)
    ]


# --- Время и текст ----------------------------------------------------------

def _dt(iso: Optional[str]) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _dep_arr(snap: FlightSnapshot) -> Tuple[datetime, Optional[datetime]]:
    dep = _dt(snap.departure.est or snap.departure.plan)
    arr = _dt(snap.arrival.est or snap.arrival.plan)
    if dep is None:
        day = datetime.fromisoformat(snap.date).replace(tzinfo=MSK)
        dep = day
    return dep, arr


def in_window(snap: FlightSnapshot, iata: str, at: datetime,
              window_h: int = 12) -> bool:
    """Событие релевантно рейсу: [вылет − window_h ч; прилёт + 2 ч]."""
    dep, arr = _dep_arr(snap)
    start = dep - timedelta(hours=window_h)
    end = (arr or dep) + timedelta(hours=2)
    return start <= at <= end


def _side_ok(snap: FlightSnapshot, iata: str) -> bool:
    if iata not in (snap.origin_iata, snap.dest_iata):
        return False
    if iata == snap.origin_iata and snap.departure.fact:
        return False
    if iata == snap.dest_iata and snap.arrival.fact:
        return False
    return True


def is_quiet(at: datetime, snap: FlightSnapshot, iata: str,
             quiet_from: int = 23, quiet_to: int = 7, near_h: int = 6) -> bool:
    """Тихие часы МСК и до рейса больше near_h ч — не будим."""
    local = at.astimezone(MSK)
    if not (local.hour >= quiet_from or local.hour < quiet_to):
        return False
    dep, arr = _dep_arr(snap)
    target = dep if iata == snap.origin_iata else (arr or dep)
    return target - at > timedelta(hours=near_h)


def _line(snap: FlightSnapshot, iata: str, action: str, kind: str,
          at: datetime, duration: Optional[timedelta]) -> str:
    name = AIRPORTS[iata][0]
    suffix = ", аэропорт прилёта" if (
        iata == snap.dest_iata and snap.dest_iata != snap.origin_iata) else ""
    if action == "lifted":
        line = f'{ic("status-arrived")} {name} ({iata}){suffix}: ограничения сняты'
        if duration and duration >= timedelta(minutes=1):
            line += f", были {human(duration)}"
        return line
    hhmm = at.astimezone(MSK).strftime("%H:%M")
    if kind == "closed":
        return (f'{ic("status-cancelled")} {name} ({iata}){suffix}: '
                f"ограничения с {hhmm} МСК")
    return (f'{ic("status-delay")} {name} ({iata}){suffix}: '
            f"работает по согласованию с {hhmm} МСК")


def build_message(snap: FlightSnapshot, iata: str, action: str, kind: str,
                  at: datetime, duration: Optional[timedelta] = None) -> str:
    return _compose(snap, [(iata, action, kind, at, duration)])


def _compose(snap: FlightSnapshot, items: Sequence[Tuple]) -> str:
    lines = [f'{ic("flight-header")} <b>{route_line(snap)}</b>']
    for iata, action, kind, at, duration in items:
        lines.append(_line(snap, iata, action, kind, at, duration))
    if any(a == "opened" and k == "closed" for _, a, k, _, _ in items):
        lines.append("Возможна задержка — статус уточняем по табло.")
    elif any(a == "opened" for _, a, _, _, _ in items):
        lines.append("Рейсы выполняются с задержками — следим за статусом.")
    return "\n".join(lines)


# --- Наблюдатель ------------------------------------------------------------

class Watcher:
    def __init__(self, conn, settings: Settings, send: Send,
                 fetch: Optional[Fetch] = None,
                 now: Optional[Callable[[], datetime]] = None):
        self.conn = conn
        self.settings = settings
        self.send = send
        self.fetch = fetch or _fetch_html
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pending: List[dict] = []

    async def tick(self, now: Optional[datetime] = None) -> None:
        now = now or self._now()
        await self._flush_pending(now)
        posts = await self._posts()
        if not posts:
            return
        max_id = max(p.id for p in posts)
        last_raw = storage.meta_get(self.conn, "airport_last_post_id")
        if last_raw is None:
            # Первый запуск: старые посты не рассылаем.
            storage.meta_set(self.conn, "airport_last_post_id", str(max_id))
            for p in posts:
                storage.save_post(self.conn, p.id, p.at,
                                  hashlib.sha1(p.text.encode()).hexdigest(),
                                  p.reply_to, now.isoformat())
            return
        last = int(last_raw)
        for p in sorted(posts, key=lambda x: x.id):
            digest = hashlib.sha1(p.text.encode()).hexdigest()
            prev = storage.past_hash(self.conn, p.id)
            fresh = p.id > last
            changed = prev is not None and prev != digest
            if not fresh and not changed:
                continue
            events = parse_post(p)
            storage.save_post(self.conn, p.id, p.at, digest, p.reply_to,
                              now.isoformat())
            storage.replace_events(self.conn, p.id, events)
            if not events:
                continue
            at = _dt(p.at)
            stale = fresh and (at is None or now - at > timedelta(seconds=STALE_EVENT_SEC))
            await self._apply(events, now, allow_push=not stale)
        storage.meta_set(self.conn, "airport_last_post_id", str(max(last, max_id)))

    async def _posts(self) -> List[Post]:
        page = await self.fetch(CHANNEL_URL)
        posts = parse_page(page)
        if not posts:
            log.warning("в ленте не распознано ни одного поста — формат изменился?")
            return []
        before = min(p.id for p in posts)
        try:
            second = parse_page(await self.fetch(f"{CHANNEL_URL}?before={before}"))
        except Exception:  # noqa: BLE001 — вторая страница не критична
            log.warning("не прочиталась вторая страница ленты", exc_info=True)
            second = []
        seen: Dict[int, Post] = {p.id: p for p in posts}
        for p in second:
            seen.setdefault(p.id, p)
        return [seen[k] for k in sorted(seen)]

    async def _apply(self, events: List[Event], now: datetime,
                     allow_push: bool) -> None:
        transitions: List[dict] = []
        for e in events:
            prev = storage.get_state(self.conn, e.iata)
            at = _dt(e.at)
            if at is None:
                continue
            if e.kind in ("closed", "partial"):
                repeat = (prev is not None and prev["kind"] == e.kind
                          and at - _dt(prev["since"]) <= timedelta(seconds=REPEAT_EPISODE_SEC))
                if repeat:
                    continue
                storage.set_state(self.conn, e.iata, e.kind, e.at, e.post_id,
                                  now.isoformat())
                if allow_push:
                    transitions.append({"iata": e.iata, "action": "opened",
                                        "kind": e.kind, "episode": e.post_id,
                                        "at": at})
            else:  # lifted
                if prev is None or prev["kind"] not in ("closed", "partial"):
                    continue
                episode = e.episode or int(prev["post_id"])
                duration = at - _dt(prev["since"])
                storage.set_state(self.conn, e.iata, "open", e.at, e.post_id,
                                  now.isoformat())
                if allow_push:
                    transitions.append({"iata": e.iata, "action": "lifted",
                                        "kind": prev["kind"], "episode": episode,
                                        "at": at, "duration": duration})
        if transitions:
            await self._deliver(transitions, now)

    def _eligible(self, row, tr: dict) -> bool:
        snap = _snap(row)
        if snap is None:
            return False
        if not _side_ok(snap, tr["iata"]):
            return False
        if not in_window(snap, tr["iata"], tr["at"],
                         self.settings.airport_alert_window_h):
            return False
        if tr["action"] == "lifted":
            if not storage.alert_sent_any(self.conn, row["chat_id"], row["id"],
                                          tr["iata"], tr["episode"],
                                          ("closed", "partial")):
                return False
            return not storage.alert_sent(self.conn, row["chat_id"], row["id"],
                                          tr["iata"], tr["episode"], "lifted")
        return not storage.alert_sent(self.conn, row["chat_id"], row["id"],
                                      tr["iata"], tr["episode"], tr["kind"])

    async def _deliver(self, transitions: List[dict], now: datetime) -> None:
        grouped: Dict[Tuple[int, int], Tuple[object, List[dict]]] = {}
        for tr in transitions:
            for row in storage.subs_for_airport(self.conn, tr["iata"]):
                if not self._eligible(row, tr):
                    continue
                snap = _snap(row)
                if snap is not None and is_quiet(
                        tr["at"], snap, tr["iata"],
                        self.settings.airport_alert_quiet_from,
                        self.settings.airport_alert_quiet_to,
                        self.settings.airport_alert_quiet_near_h):
                    storage.mark_alert(self.conn, row["chat_id"], row["id"],
                                       tr["iata"], tr["episode"], "suppressed",
                                       now.isoformat())
                    continue
                key = (row["chat_id"], row["id"])
                grouped.setdefault(key, (row, []))[1].append(tr)
        for (chat_id, _sid), (row, trs) in grouped.items():
            snap = _snap(row)
            if snap is None:
                continue
            items = [(t["iata"], t["action"], t["kind"], t["at"], t.get("duration"))
                     for t in trs]
            text = _compose(snap, items)
            marks = [(t["iata"], t["episode"],
                      t["kind"] if t["action"] == "opened" else "lifted") for t in trs]
            ok = await self.send(chat_id, text, row["thread_id"])
            if ok is False:
                self._pending.append({"chat_id": chat_id, "sub_id": row["id"],
                                      "thread_id": row["thread_id"],
                                      "text": text, "marks": marks, "created": now})
                continue
            for iata, episode, kind in marks:
                storage.mark_alert(self.conn, chat_id, row["id"], iata, episode,
                                   kind, now.isoformat())

    async def _flush_pending(self, now: datetime) -> None:
        keep: List[dict] = []
        for item in self._pending:
            if now - item["created"] > timedelta(seconds=PENDING_TTL_SEC):
                continue
            ok = await self.send(item["chat_id"], item["text"], item["thread_id"])
            if ok is False:
                keep.append(item)
                continue
            for iata, episode, kind in item["marks"]:
                storage.mark_alert(self.conn, item["chat_id"], item["sub_id"],
                                   iata, episode, kind, now.isoformat())
        self._pending = keep


def _snap(row) -> Optional[FlightSnapshot]:
    try:
        raw = row["last_json"]
        return snap_from_dict(json.loads(raw)) if raw else None
    except (ValueError, TypeError, KeyError):
        return None


async def _fetch_html(url: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True,
                                 headers={"User-Agent": UA}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def run(conn, settings: Settings, send: Send) -> None:
    if not settings.airport_alerts:
        log.info("алерты аэропортов выключены")
        return
    watcher = Watcher(conn, settings, send)
    log.info("наблюдатель аэропортов запущен, интервал %ds",
             settings.airport_alerts_interval_sec)
    while True:
        try:
            await watcher.tick()
        except Exception:  # noqa: BLE001 — один сбой не должен ронять цикл
            log.exception("тик алертов аэропортов упал")
        await asyncio.sleep(settings.airport_alerts_interval_sec)
