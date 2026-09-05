"""Источник: онлайн-табло Внуково (VKO). Только через рендер-сервис.

Табло отдаёт контент лишь браузеру (JS-челлендж → куки → reload), поэтому
HTML берём у deploy/render: страница исполняется как у посетителя, ничего не
эмулируем. Без RENDER_URL источник молчит (VKO остаётся на AirLabs).

Строка табло (Vue): <a class="timetable__row" href=".../online-tablo/<id>">
  <time>HH:MM</time> + «5 сентября» · Город + IATA (fl-airport-code) ·
  номера (fl-number; первый _strong — основной, остальные кодшеры; коды бывают
  кириллицей: «ЮТ 571», «В2 2271») · авиакомпания · терминал · выход ·
  статус («Регистрация с 22:00», «Идёт регистрация», «Посадка с 20:55»,
  «Идёт посадка», «Посадка закончена», «Вылетел в HH:MM», «Задержан до HH:MM»,
  «Отменён»). Отдельных план/факт-колонок нет — времена читаем из статуса.
Направление — по заголовку колонки времени («Время вылета» / «Время прилета»).
"""
from __future__ import annotations

import html as _html
import re
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from flight_bot.http import Renderer
from flight_bot.models import Boarding, Checkin, FlightSnapshot, Leg
from flight_bot.sources.base import FlightSource

BOARD_URL = "https://www.vnukovo.ru/ru/for-passengers/reysi/online-tablo/"
# URL-параметров у табло нет: «Прилёт» — кнопка-переключатель, её нажимает
# рендер-сервис. Табло показывает сегодня и завтра одной страницей.
ARRIVAL_CLICK = 'button:has-text("Прилёт")'
MSK = timezone(timedelta(hours=3))
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7,
          "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
# Кириллические коды авиакомпаний на табло → IATA: сперва явная транслитерация
# (СУ→SU, а не CY по похожести), потом — замена похожих букв (В2→B2, ТК→TK).
CODES = {"СУ": "SU", "ФВ": "FV", "ДР": "DP", "ДП": "DP", "ЮТ": "UT", "С7": "S7",
         "У6": "U6", "Н4": "N4", "ЕО": "EO", "ВИ": "VI", "ЯК": "R3", "ИЖ": "I8", "ЮВ": "UW"}
CYR = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")

# Строки вылета — class="timetable__row", прилёта — "timetable__row _arrival".
_ROW_RE = re.compile(r'<a class="timetable__row(?: [^"]*)?"[^>]*href="[^"]*?(\d+)"[^>]*>(.*?)</a>', re.S)
_HHMM = re.compile(r"(\d{1,2}):(\d{2})")


def _text(fragment: str) -> str:
    t = _html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def canon(no: str) -> str:
    """«ЮТ 571» → «UT571», «В2 2271» → «B22271», «su 25» → «SU25»."""
    s = re.sub(r"[\s-]", "", no or "").upper()
    m = re.match(r"^([A-ZА-Я0-9]{2})0*(\d{1,4})$", s)
    if not m:
        return s
    code = CODES.get(m.group(1), m.group(1).translate(CYR))
    return f"{code}{int(m.group(2))}"


def _date(text: str, today: date) -> Optional[str]:
    """«5 сентября» → YYYY-MM-DD; год от today, декабрь→январь через границу."""
    m = re.search(r"(\d{1,2})\s+([а-яё]+)", text.lower())
    if not m:
        return None
    mon = next((v for k, v in MONTHS.items() if m.group(2).startswith(k)), None)
    if not mon:
        return None
    year = today.year + (1 if mon == 1 and today.month == 12 else 0)
    return date(year, mon, int(m.group(1))).isoformat()


def _at(day: str, hhmm: str, not_before: Optional[str] = None) -> Optional[str]:
    m = _HHMM.search(hhmm or "")
    if not day or not m:
        return None
    d = datetime.fromisoformat(day).replace(hour=int(m.group(1)), minute=int(m.group(2)), tzinfo=MSK)
    if not_before and d < datetime.fromisoformat(not_before):
        d += timedelta(days=1)
    return d.isoformat()


def _minutes_before(plan: Optional[str], boarding_at: Optional[str]) -> str:
    if not plan or not boarding_at:
        return ""
    m = int((datetime.fromisoformat(plan) - datetime.fromisoformat(boarding_at)).total_seconds() // 60)
    return str(m) if m > 0 else ""


def direction_of(page: str) -> str:
    """Направление страницы: заголовок колонки, класс строк или подпись ячейки времени."""
    if (re.search(r'_time"><span class="visible-lg">\s*Время прил', page)
            or 'class="timetable__row _arrival"' in page
            or 'title="Время прилета"' in page):
        return "arrival"
    return "departure"


def parse_board(page: str, today: date) -> List[Dict]:
    out: List[Dict] = []
    for rid, body in _ROW_RE.findall(page):
        tm = re.search(r"<time>(\d{1,2}:\d{2})</time><span class=\"screen-reader-only\">([^<]*)", body)
        ap = re.search(r'class="fl-airport"[^>]*>.*?</span>(.*?)<span class="fl-airport-code">([^<]*)', body, re.S)
        nums = [_text(n) for n in re.findall(r'class="fl-number[^"]*">(.*?)</span>', body, re.S)]
        st = re.search(r'fl-status__content">(.*?)</span>', body, re.S)
        gate = re.search(r'fl-gate">(.*?)</span>', body, re.S)
        term = re.search(r'_terminal"[^>]*>.*?</span>(.*?)</span>', body, re.S)
        airline = re.search(r'fl-airline__text">(.*?)</span>', body, re.S)
        if not tm or not nums:
            continue
        out.append({
            "id": rid, "time": tm.group(1), "date": _date(tm.group(2), today),
            "city": _text(ap.group(1)) if ap else "", "iata": _text(ap.group(2)) if ap else "",
            "numbers": [canon(n) for n in nums], "raw_number": nums[0],
            "airline": _text(airline.group(1)) if airline else "",
            "terminal": _text(term.group(1)) if term else "",
            "gate": _text(gate.group(1)) if gate else "",
            "status": _text(st.group(1)) if st else "",
        })
    return out


_DESKS_RE = re.compile(r"\s*стойк\w*\s*([\d][\d,\-–\s]*)", re.I)


def build(row: Dict, direction: str, flight: str) -> FlightSnapshot:
    day = row["date"] or ""
    plan = _at(day, row["time"])
    # «Идёт регистрация Стойка 129» — стойки лежат внутри статуса.
    m_desks = _DESKS_RE.search(row["status"])
    desks = m_desks.group(1).strip() if m_desks else ""
    st = _DESKS_RE.sub("", row["status"]).strip()
    low = st.lower()
    est = fact = None
    if any(k in low for k in ("вылетел", "прилетел", "прибыл", "приземлил")):
        fact = _at(day, st) or plan
    elif "задерж" in low or "ожида" in low:
        est = _at(day, st)
    own = Leg(plan=plan, est=est, fact=fact)
    checkin = boarding = None
    if direction == "departure":
        # Фактических времён фаз табло не даёт — ничего не выдумываем: «Идёт
        # регистрация»/«Идёт посадка» живут в статусе, диффер шлёт его смену.
        checkin = Checkin(desks=desks,
                          start_plan=_at(day, st) if "регистрация с" in low else None)
        boarding = Boarding(start_min=_minutes_before(plan, _at(day, st)) if "посадка с" in low else "")
    common = dict(flight=flight, date=day, direction=direction, status=st,
                  airline=row["airline"], terminal=row["terminal"], gate=row["gate"],
                  source="vnukovo.ru", key=row["id"])
    if direction == "departure":
        return FlightSnapshot(origin_iata="VKO", origin_city="Москва",
                              dest_iata=row["iata"], dest_city=row["city"],
                              departure=own, checkin=checkin, boarding=boarding, **common)
    return FlightSnapshot(origin_iata=row["iata"], origin_city=row["city"],
                          dest_iata="VKO", dest_city="Москва", arrival=own, **common)


def parse(page: str, flight_no: str, today: date) -> List[FlightSnapshot]:
    """Чистый парсер отрендеренной страницы — без сети."""
    wanted = canon(flight_no)
    direction = direction_of(page)
    return [build(r, direction, wanted) for r in parse_board(page, today) if wanted in r["numbers"]]


class VkoSource(FlightSource):
    name = "vnukovo.ru"

    def __init__(self, renderer: Optional[Renderer] = None):
        self.renderer = renderer or Renderer("")

    async def fetch(
        self,
        flight_no: str,
        date: Optional[str] = None,
        direction: str = "departure",
    ) -> List[FlightSnapshot]:
        if not self.renderer.enabled:
            return []
        today = datetime.now(MSK).date()
        page = await self.renderer.render(BOARD_URL, wait_ms=12000, selector="a.timetable__row",
                                          click=ARRIVAL_CLICK if direction == "arrival" else None)
        if direction_of(page) != direction:
            return []          # переключатель не сработал — не выдавать вылеты за прилёты
        return [s for s in parse(page, flight_no, today) if not date or s.date == date]
