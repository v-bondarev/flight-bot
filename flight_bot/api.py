"""Loopback-only, authenticated HTTP API for the Personal Hub flight bridge."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from datetime import date

from aiohttp import web

from flight_bot import config, registry, storage
from flight_bot.models import snap_from_dict, snap_to_dict
from flight_bot.render import status_card

log = logging.getLogger("flight_bot.api")
API_TOKEN = web.AppKey("api_token", str)
DATABASE = web.AppKey("db", sqlite3.Connection)
FLIGHT_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9]{2}\s?\d{1,4}$")


def _flight(value: str) -> str | None:
    value = value.strip().upper()
    return value.replace(" ", "") if FLIGHT_RE.fullmatch(value) else None


@web.middleware
async def require_api_key(request: web.Request, handler):
    if request.path == "/health":
        return await handler(request)
    expected = request.app[API_TOKEN]
    supplied = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(supplied, f"Bearer {expected}"):
        raise web.HTTPUnauthorized(text="unauthorized")
    return await handler(request)


async def search(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text="invalid JSON") from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="JSON object required")
    flight = _flight(str(body.get("flight") or ""))
    if flight is None:
        raise web.HTTPBadRequest(text="invalid flight number")
    results = await registry.search(flight)
    candidates = storage.save_flight_candidates(
        request.app[DATABASE], flight, list(results), int(time.time()) + 3600,
    )
    return web.json_response({
        "flight": flight,
        "results": [
            {"candidate_id": item["candidate_id"],
             "snapshot": snap_to_dict(item["snapshot"]),
             "card": status_card(item["snapshot"])}
            for item in candidates
        ],
        "unavailable_sources": sorted(results.down),
    })


async def status(request: web.Request) -> web.Response:
    flight = _flight(request.query.get("flight", ""))
    raw_date = request.query.get("date", "")
    direction = request.query.get("direction", "")
    if flight is None or direction not in {"departure", "arrival"}:
        raise web.HTTPBadRequest(text="invalid flight or direction")
    try:
        flight_day = date.fromisoformat(raw_date)
    except ValueError:
        raise web.HTTPBadRequest(text="date must be YYYY-MM-DD") from None
    if flight_day.isoformat() != raw_date:
        raise web.HTTPBadRequest(text="date must be YYYY-MM-DD")
    result = await registry.fetch_for(flight, raw_date, direction)
    if result is None:
        raise web.HTTPNotFound(text="flight status not found")
    return web.json_response({"snapshot": snap_to_dict(result), "card": status_card(result)})


async def add_track(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text="invalid JSON") from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="JSON object required")
    candidate_id = str(body.get("candidate_id") or "")
    candidate = storage.get_flight_candidate(request.app[DATABASE], candidate_id)
    if candidate is None:
        raise web.HTTPBadRequest(text="choose a recent search result before tracking")
    row = storage.add_external_track(
        request.app[DATABASE], candidate["flight"], candidate["date"],
        candidate["direction"], candidate["source"], None,
    )
    return web.json_response({
        "id": row["id"], "flight": candidate["flight"], "date": candidate["date"],
        "direction": candidate["direction"],
        "status": "started",
    }, status=201)


async def list_tracks(request: web.Request) -> web.Response:
    tracks = []
    for row in storage.list_external_tracks(request.app[DATABASE]):
        snap = snap_from_dict(json.loads(row["last_json"])) if row["last_json"] else None
        tracks.append({
            "id": row["id"], "flight": row["flight"], "date": row["date"],
            "direction": row["direction"], "snapshot": snap_to_dict(snap) if snap else None,
            "card": status_card(snap) if snap else None,
        })
    return web.json_response({"tracks": tracks})


async def remove_track(request: web.Request) -> web.Response:
    try:
        track_id = int(request.match_info["track_id"])
    except ValueError:
        raise web.HTTPBadRequest(text="invalid track id") from None
    if not storage.remove_external_track(request.app[DATABASE], track_id):
        raise web.HTTPNotFound(text="track not found")
    return web.json_response({"id": track_id, "active": False})


async def list_events(request: web.Request) -> web.Response:
    return web.json_response({
        "events": [dict(row) for row in storage.pending_external_events(request.app[DATABASE])]
    })


async def acknowledge_event(request: web.Request) -> web.Response:
    try:
        event_id = int(request.match_info["event_id"])
    except ValueError:
        raise web.HTTPBadRequest(text="invalid event id") from None
    if not storage.acknowledge_external_event(request.app[DATABASE], event_id):
        raise web.HTTPNotFound(text="event not found")
    return web.json_response({"id": event_id, "acknowledged": True})


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_app(api_token: str, db: sqlite3.Connection | None = None) -> web.Application:
    if not api_token or len(api_token) < 32:
        raise ValueError("FLIGHT_API_TOKEN must contain at least 32 characters")
    app = web.Application(middlewares=[require_api_key], client_max_size=1024 * 1024)
    app[API_TOKEN] = api_token
    app[DATABASE] = db if db is not None else storage.connect(":memory:")
    app.add_routes([
        web.get("/health", health),
        web.post("/v1/search", search),
        web.get("/v1/status", status),
        web.post("/v1/tracks", add_track),
        web.get("/v1/tracks", list_tracks),
        web.delete("/v1/tracks/{track_id}", remove_track),
        web.get("/v1/events", list_events),
        web.post("/v1/events/{event_id}/ack", acknowledge_event),
    ])
    return app


async def serve() -> None:
    config.load_env(os.getenv("FLIGHT_BOT_ENV", ".env"))
    settings = config.load()
    conn = storage.connect(settings.db_path)
    registry.configure(settings, conn)
    host = os.getenv("FLIGHT_API_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "::1", "localhost"}:
        conn.close()
        raise SystemExit("Flight API must bind to loopback only")
    port = int(os.getenv("FLIGHT_API_PORT", "18766"))
    if not 1 <= port <= 65535:
        conn.close()
        raise SystemExit("FLIGHT_API_PORT must be between 1 and 65535")
    runner = web.AppRunner(create_app(os.getenv("FLIGHT_API_TOKEN", ""), conn), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Flight API listening on loopback port %d", port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        conn.close()


if __name__ == "__main__":
    from flight_bot.main import setup_logging
    setup_logging()
    asyncio.run(serve())
