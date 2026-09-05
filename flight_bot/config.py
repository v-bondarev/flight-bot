"""Настройки из окружения (.env рядом на проде). Секреты — не в git."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: str
    poll_interval_sec: int
    grace_sec: int              # держать подписку после прилёта, потом снять
    scrapedo_api_key: str       # для источников с антиботом (DME/Pulkovo); пусто — не используем
    yandex_rasp_api_key: str    # реестр «номер → аэропорт» вне Москвы; пусто — не используем
    airlabs_api_key: str        # статус/маршрут по номеру вне наших табло; пусто — не используем


def load_env(path: str = ".env") -> None:
    """Простой загрузчик .env: KEY=VALUE → os.environ (не перетирая заданное).

    Хватает для запуска вручную; под systemd тот же файл цепляется через
    EnvironmentFile, и эта функция просто ничего не находит.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = val.strip().strip('"').strip("'")


def load() -> Settings:
    return Settings(
        bot_token=os.getenv("BOT_TOKEN", ""),
        db_path=os.getenv("DB_PATH", "flight-bot.sqlite"),
        poll_interval_sec=int(os.getenv("POLL_INTERVAL_SEC", "300")),
        grace_sec=int(os.getenv("GRACE_SEC", "3600")),
        scrapedo_api_key=os.getenv("SCRAPEDO_API_KEY", ""),
        yandex_rasp_api_key=os.getenv("YANDEX_RASP_API_KEY", ""),
        airlabs_api_key=os.getenv("AIRLABS_API_KEY", ""),
    )
