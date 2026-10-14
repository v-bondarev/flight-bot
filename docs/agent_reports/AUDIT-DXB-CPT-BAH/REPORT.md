# Аудит: табло Дубая (DXB), Кейптауна (CPT), Бахрейна (BAH)

Статус: аудит (только чтение), 14.10.2026 ~10:45 МСК. Правок кода нет.

## DXB — Dubai International: ✅ есть публичный источник
- `GET https://dubaiairports.ae/docs/passengerslibraries/flights-library/flights-data.json`
  — статический JSON всего аэропорта, без ключа и браузера. С сервера riga: 200, 3,7 МБ, 0,7 с.
- `Flights`: 2898 записей, вылеты и прилёты, вчера/сегодня/завтра; `LastFetchedAt` обновляется
  (07:35Z = 10:35 МСК при проверке).
- Поля: `flightNumber` («EK 770»), `arrivalDepartureFlag` D/A, `aircraftTerminal`, `gateNumber`,
  `baggageBeltNumber`, `flightStatus` (Estimated/Departed/Arrived…), `scheduledoffblockTime`,
  `estimatedOffBlockTime`, `actualOffBlockTime`, `actualTakeOffTime`, на прилёт —
  `scheduledOnblockTime`, `estimatedOnBlockTime`, `actualLandingTime`, `actualOnBlockTime`,
  `jointFlightNumber` (кодшеры), `aircraftParkingPosition`.
- **Ловушка:** времена рейсов с суффиксом `Z`, но это местное время Дубая (+04:00):
  EK770 `scheduledoffblockTime` 09:10Z при реальном вылете 09:10 местного (AeroDataBox).
  `LastFetchedAt` — настоящий UTC. Парсер обязан ставить +04:00, а не UTC.
- Нет: стоек и времени регистрации, времени посадки.
- Сырые данные EK770 14.10: Departed, терминал 3, выход A6b, план 09:10, оценка 09:30,
  off-block 09:21, взлёт 09:40 (местное).

## BAH — Bahrain International: ✅ есть публичное API
- `POST https://www.bahrainairport.bh/api/FlightProxy/departures` и `/arrivals`,
  тело `{"date": "дд/мм/гггг"}`, без токена. С riga: 200, 0,15–0,4 с.
- Даты: сегодня и завтра (на +3 дня — пусто).
- Поля: `flight` («GF 013»), `scheduledDate/Time`, `estimatedDate/Time`, `actualDate/Time`
  (местное, +03:00), `remarks` (On Time/Departed…), `gate`, `checkinCounter` (зона стоек, «E»),
  `baggage` (лента, «A04»), `originAirportCode`/`destinationAirportCode`, `terminal` (0).
- GF013 SVO→BAH 14.10: прибытие 19:20, On Time, выход 18A, лента A04.

## CPT — Cape Town International (ACSA): ❌ пригодного официального источника нет
- airports.co.za — SharePoint-форма с постбэком (ViewState) поиска по номеру; на EK770 сайт
  сам ответил «Network Error: The operation has timed out».
- Публичного JSON не нашёл. Сторонние табло (avionio-виджет на неофициальных сайтах) —
  не первоисточник.
- Покрытие CPT остаётся за AirLabs (статус, терминал, выход, лента; только ближайший рейс по номеру)
  и AeroDataBox (даты при поиске).

## Вывод
- DXB и BAH — подключать отдельными источниками (`FlightSource`), как LED: JSON, без браузера,
  доступны с сервера.
- CPT — без своего источника, AirLabs.

## Реализация и выкат — 14.10.2026 10:55 МСК (riga)
Статус: **РЕАЛИЗОВАНО, НО НЕ ПРИНЯТО**.
- Код: a049104 (DxbSource, BahSource), 2610736 (по ревью: SharedFetch — одна загрузка на всех; DXB
  без scrape.do; опрос закрепляется за записью по `key`; статусы BAH по смыслу), a3b981e.
- Ревью дважды: блокеры B1 (N параллельных загрузок файла 3,7 МБ, уход в scrape.do) и B2 (две
  записи номера на дату в DXB) закрыты. Две записи EK132 на 15.10 (…096 03:55, …097 07:05 местного) —
  разные рейсы, не дубль.
- Сервер: бэкап `~/flight-bot-backup/db-20261014-1055.sqlite`; restart 10:55:33 МСК, NRestarts=0.
- Замер `registry.search` на сервере: EK770 7,4 с (14–15.10 — табло DXB, 16–19.10 — AeroDataBox);
  GF13 1,2 с (14–15.10 — табло BAH); EK132 1,2 с (14–15.10 — DME). pytest: 170 passed.
- Хвосты: статусы BAH кроме cancel/land/arriv/depart уходят как есть (англ.); у BAH дубли номера на
  дату не различаются (ключ = номер+дата) — пока не встречались.
