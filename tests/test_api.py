from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from flight_bot import api, storage
from flight_bot.models import FlightSnapshot


def run(coro):
    return asyncio.run(coro)


def test_api_rejects_missing_and_invalid_keys():
    async def check():
        client = TestClient(TestServer(api.create_app("x" * 40, storage.connect(":memory:"))))
        await client.start_server()
        try:
            denied = await client.post("/v1/search", json={"flight": "SU2128"})
            invalid = await client.get("/v1/tracks", headers={"Authorization": "Bearer wrong"})
            health = await client.get("/health")
            assert denied.status == 401
            assert invalid.status == 401
            assert (await health.json()) == {"status": "ok"}
        finally:
            await client.close()
    run(check())


def test_api_track_lifecycle_and_event_ack(monkeypatch):
    class SearchResults(list):
        down = set()

    snap = FlightSnapshot(
        flight="SU2128", date="2026-10-30", direction="departure",
        origin_iata="SVO", dest_iata="AYT", status="По расписанию",
    )

    async def search(_flight):
        return SearchResults([snap])

    monkeypatch.setattr(api.registry, "search", search)

    async def check():
        db = storage.connect(":memory:")
        client = TestClient(TestServer(api.create_app("x" * 40, db)))
        await client.start_server()
        headers = {"Authorization": f"Bearer {'x' * 40}"}
        try:
            found = await client.post("/v1/search", json={"flight": "su 2128"}, headers=headers)
            candidate = (await found.json())["results"][0]["candidate_id"]
            created = await client.post("/v1/tracks", json={"candidate_id": candidate}, headers=headers)
            assert created.status == 201
            track = await created.json()
            assert track["flight"] == "SU2128"
            assert track["status"] == "started"

            invalid = await client.post("/v1/tracks", json={"candidate_id": "invented"}, headers=headers)
            assert invalid.status == 400

            listed = await client.get("/v1/tracks", headers=headers)
            assert len((await listed.json())["tracks"]) == 1
            event_id = storage.add_external_event(db, track["id"], "Статус обновился.")
            events = await client.get("/v1/events", headers=headers)
            assert (await events.json())["events"][0]["id"] == event_id

            ack = await client.post(f"/v1/events/{event_id}/ack", headers=headers)
            assert ack.status == 200
            assert (await (await client.get("/v1/events", headers=headers)).json())["events"] == []
            storage.add_external_event(db, track["id"], "Обновление, пришедшее перед отменой.")
            removed = await client.delete(f"/v1/tracks/{track['id']}", headers=headers)
            assert removed.status == 200
            assert (await (await client.get("/v1/tracks", headers=headers)).json())["tracks"] == []
            assert (await (await client.get("/v1/events", headers=headers)).json())["events"] == []
        finally:
            await client.close()
            db.close()
    run(check())


def test_api_rejects_invalid_flight_numbers():
    async def check():
        client = TestClient(TestServer(api.create_app("x" * 40)))
        await client.start_server()
        try:
            response = await client.post(
                "/v1/search", json={"flight": "not a flight"},
                headers={"Authorization": f"Bearer {'x' * 40}"},
            )
            assert response.status == 400
        finally:
            await client.close()
    run(check())
