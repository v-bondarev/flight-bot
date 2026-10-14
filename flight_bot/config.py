"""Настройки из окружения (.env рядом на проде). Секреты — не в git."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, FrozenSet


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: str
    poll_interval_sec: int
    grace_sec: int              # держать подписку после прилёта, потом снять
    scrapedo_api_key: str       # для источников с антиботом (DME/Pulkovo); пусто — не используем
    yandex_rasp_api_key: str    # реестр «номер → аэропорт» вне Москвы; пусто — не используем
    airlabs_api_key: str        # статус/маршрут по номеру вне наших табло; пусто — не используем
    aerodatabox_api_key: str    # расписание по номеру на 5 дней вперёд (RapidAPI); пусто — не используем
    schedule_proxy_url: str     # выход для AeroDataBox (socks5://127.0.0.1:1081, туннель до DK); пусто — напрямую
    aerodatabox_budget: int     # платных запросов расписания за скользящие 31 день (квота 400/6 = 66)
    ru_proxy_url: str           # свой RU-выход для табло (socks5://127.0.0.1:1080 SSH-туннель до RU-хоста); пусто — сразу scrape.do
    scrapedo_concurrency: int   # одновременных запросов к scrape.do (кредиты платные)
    render_url: str             # резервный рендер VKO через туннель; основной путь — HTTP API
    render_cache_sec: int       # TTL кэша VKO HTTP-поиска и резервного HTML-рендера
    owner_ids: FrozenSet[int]   # Telegram user id, чьи команды и кнопки бот принимает в группах; пусто — все
    greet_names: Dict[str, str]  # username (без @, нижний регистр) → обращение на «вы» в приветствии


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


def _owner_ids(raw: str) -> FrozenSet[int]:
    try:
        return frozenset(int(x) for x in raw.split(",") if x.strip())
    except ValueError:
        raise SystemExit(f"OWNER_IDS: ожидаются числовые Telegram id через запятую, а не {raw!r}")


def _greet_names(raw: str) -> Dict[str, str]:
    """«@user=Имя Отчество, other=…» → {"user": "Имя Отчество", …}."""
    out = {}
    for item in filter(str.strip, raw.split(",")):
        user, sep, name = item.partition("=")
        if not sep or not user.strip() or not name.strip():
            raise SystemExit(f"GREET_NAMES: ожидается username=Обращение через запятую, а не {item.strip()!r}")
        out[user.strip().lstrip("@").lower()] = name.strip()
    return out


def load() -> Settings:
    return Settings(
        bot_token=os.getenv("BOT_TOKEN", ""),
        db_path=os.getenv("DB_PATH", "flight-bot.sqlite"),
        poll_interval_sec=int(os.getenv("POLL_INTERVAL_SEC", "300")),
        grace_sec=int(os.getenv("GRACE_SEC", "3600")),
        scrapedo_api_key=os.getenv("SCRAPEDO_API_KEY", ""),
        yandex_rasp_api_key=os.getenv("YANDEX_RASP_API_KEY", ""),
        airlabs_api_key=os.getenv("AIRLABS_API_KEY", ""),
        aerodatabox_api_key=os.getenv("AERODATABOX_API_KEY", ""),
        schedule_proxy_url=os.getenv("SCHEDULE_PROXY_URL", ""),
        aerodatabox_budget=int(os.getenv("AERODATABOX_BUDGET", "60")),
        ru_proxy_url=os.getenv("RU_PROXY_URL", ""),
        scrapedo_concurrency=int(os.getenv("SCRAPEDO_CONCURRENCY", "2")),
        render_url=os.getenv("RENDER_URL", ""),
        render_cache_sec=int(os.getenv("RENDER_CACHE_SEC", "75")),
        owner_ids=_owner_ids(os.getenv("OWNER_IDS", "")),
        greet_names=_greet_names(os.getenv("GREET_NAMES", "")),
    )
