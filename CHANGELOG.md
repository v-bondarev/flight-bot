# Changelog

## Unreleased

- Каркас: модели `FlightSnapshot`/`Leg`/`FlightRoute`, контракты `FlightSource`
  и `AirportResolver`.
- Источник `SvoSource` — табло Шереметьево (Bitrix-JSON), чистый парсер + тесты
  на боевой фикстуре (SU2128).
- Диффер `diff_snapshots` — изменения по структурным полям (выход, терминал,
  время вылета/прилёта, факт вылета/прилёта), тесты.
- Хранилище подписок и last-снимка в SQLite (`storage`), тесты.
- Реестр источников и проба «номер → аэропорт» опросом табло (`registry`,
  `BoardProbeResolver`).
- Поллер: снапшот → diff → пуш, снятие подписки после прилёта; тесты без сети.
- aiogram-бот: номер рейса → даты кнопками → подписка, `/list` с отпиской.
- Entrypoint `flight_bot.main`, загрузчик `.env`, systemd-юнит в `deploy/`.
