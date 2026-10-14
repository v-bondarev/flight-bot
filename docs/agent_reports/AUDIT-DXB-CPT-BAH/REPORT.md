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
