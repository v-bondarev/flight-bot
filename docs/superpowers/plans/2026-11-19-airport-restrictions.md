# Алерты об ограничениях аэропортов — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax.

**Goal:** Для активных подписок присылать отдельное сообщение при введении/снятии
ограничений Росавиации в аэропорту вылета/прилёта.

**Architecture:** Новый модуль `flight_bot/airport_alerts.py` — отдельная корутина
рядом с поллером: читает `t.me/s/favt_info`, парсит шаблонные посты, ведёт
состояние аэропортов в SQLite, матчит подписки по IATA и шлёт сообщения через тот
же `send`. Поллер подписок не меняется, кроме сохранения IATA маршрута.

**Tech Stack:** Python 3.11+, httpx, aiogram (только существующий `send`), SQLite,
pytest, asyncio.

## Global Constraints

- Аэропорты: SVO, DME, VKO, ZIA, LED, AER.
- Окно рейса: `[вылет − 12 ч; прилёт + 2 ч]`.
- Режимы: `closed`, `partial`, `lifted`.
- Тихие часы: 23:00–07:00 МСК, если до значимого времени рейса > 6 ч.
- Сразу, без бэкфилла; события старше 30 минут пуш не порождают.
- Эмодзи только существующие: `🔔`, `🔴`, `🟡`, `🟢`.
- Новых платных зависимостей нет.

---

### Task 1: Storage — маршрут подписки и таблицы алертов

**Files:**
- Modify: `flight_bot/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- `add(conn, chat_id, flight, date, direction="departure", source="", origin_iata="", dest_iata="")`
- `set_route(conn, sub_id, origin_iata, dest_iata)`
- `subs_for_airport(conn, iata) -> list[Row]`
- `meta_get(conn, key)`, `meta_set(conn, key, value)`
- `past_hash(conn, post_id)`, `save_post(conn, post_id, at, text_hash, reply_to, processed_at)`
- `replace_events(conn, post_id, events)`
- `get_state(conn, iata)`, `set_state(conn, iata, kind, since, post_id, updated_at)`
- `alert_sent(conn, chat_id, sub_id, iata, episode, kind)`, `mark_alert(conn, ...)`

- [ ] Добавить в `SCHEMA` таблицы `airport_posts`, `airport_events`,
      `airport_state`, `airport_alerts_sent`, `meta`; в `_MIGRATIONS` —
      `origin_iata`, `dest_iata`.
- [ ] `set_last` обновляет `origin_iata/dest_iata` из снимка (пустые не затирают).
- [ ] Тесты: миграция старых колонок, `subs_for_airport`, дедуп-хелперы.

### Task 2: Parser — посты канала

**Files:**
- Create: `flight_bot/airport_alerts.py`
- Test: `tests/test_airport_alerts.py`

**Interfaces:**
- `Post(id: int, at: str, text: str, reply_to: int | None)`
- `Event(post_id: int, at: str, iata: str, kind: str, episode: int | None)`
- `parse_page(html) -> list[Post]`
- `parse_post(post) -> list[Event]`

- [ ] `parse_page` режет по `js-widget_message" data-post="favt_info/(\d+)"`,
      берёт **только** `js-message_text` (не `js-message_reply_text`), время из
      `<time datetime>`, родителя из `tgme_widget_message_reply`.
- [ ] `parse_post`: шаблон = строка «Аэропорт(ы)» в первых 4 строках;
      `UPD … снят|открыт` → `lifted` (episode = id поста), `СНЯТЫ` → `lifted`
      (episode = reply_to), `по согласованию` → `partial`, `ВВЕДЕНЫ` → `closed`.
- [ ] Аэропорты — по шести regex-вариантам, регистр не важен.
- [ ] Фикстуры: одиночное закрытие, список, partial, reply-снятие, UPD, сводка,
      дубль.

### Task 3: Watcher — окно, ночь, дедуп, отправка

**Files:**
- Modify: `flight_bot/airport_alerts.py`
- Test: `tests/test_airport_alerts.py`

**Interfaces:**
- `Watcher(conn, settings, send, fetch=None, now=None)`
- `async Watcher.tick(now=None)`
- `build_message(snap, iata, action, kind, at, duration) -> str`
- `in_window(snap, iata, at, now) -> bool`
- `is_quiet(at, snap, iata) -> bool`

- [ ] Первый тик: запомнить `max(post_id)` и молчать.
- [ ] Каждый тик: 2 страницы, новые посты + изменённые по хешу; события
      пересобираются; переходы состояния — по 12-часовому правилу повторов.
- [ ] `closed/partial`: пуш, если окно, не ночь и нет отправки по
      `(chat, sub, iata, episode, kind)`.
- [ ] `lifted`: пуш только если был реальный пуш по эпизоду; длительность из
      `post.at − state.since`; для `UPD` — без длительности.
- [ ] Ошибка `send` (False) — в память `_pending`, повтор на следующем тике.
- [ ] Тексты: `🔔 <b>route_line</b>` + `🔴/🟡/🟢` + оговорка.

### Task 4: Интеграция — config, bot, main, env, README

**Files:**
- Modify: `flight_bot/config.py`, `flight_bot/bot.py`, `flight_bot/main.py`,
  `.env.example`, `README.md`, `CHANGELOG.md`
- Test: `tests/test_airport_alerts.py`

- [ ] `Settings`: `airport_alerts`, `airport_alerts_interval_sec`,
      `airport_alert_window_h`, `airport_alert_quiet_from/to`,
      `airport_alert_quiet_near_h`.
- [ ] `on_subscribe` сохраняет IATA из снимка через `set_route`.
- [ ] `main.send` возвращает `bool`; в `on_startup` — задача
      `airport_alerts.run` при включённом флаге.
- [ ] `.env.example` и README — новые переменные и раздел.

### Task 5: Проверка и выкатка

- [ ] `pytest` — всё зелёное.
- [ ] Ручной прогон `Watcher.tick` на живой ленте (локально): парсится, события
      есть, пушей нет при пустой базе.
- [ ] `git push`; на `vps-site`: `git pull`, `systemctl restart flight-bot`,
      `journalctl` — без ошибок.
- [ ] `curl https://t.me/s/favt_info` с сервера — 200.
