# ADR-0007. Private flight API for Personal Hub

Status: **Proposed**, implementation prepared locally on 2026-10-29; not deployed.

## Context

flight-bot runs on Riga and owns the airport adapters, schedule lookup, and
flight-change comparison. Its Telegram Bot API client cannot be called by
Kuzmich as a user, and a second bot cannot exchange messages with it.

## Decision

- Add an authenticated JSON API to the existing flight-bot application code.
- Bind the API to 127.0.0.1:18766 only. Personal Hub on the Mac reaches it
  through an outbound SSH local forward bound to 127.0.0.1:18765.
- Reuse the flight-bot search and status adapters instead of copying airport
  scraping or API credentials to the Mac.
- Store Kuzmich's tracks and change events in separate SQLite tables. Existing
  flight-bot subscriptions and Telegram delivery stay independent.
- Queue change events on Riga. The Mac poller asks Hermes to phrase each update
  as Kuzmich, sends it through the existing Kuzmich Telegram bot, then
  acknowledges it. Delivery is at least once if acknowledgement fails after a
  successful Telegram send.
- Do not expose a public listener, put flight-service credentials in Git, or
  enable a second Telegram poller.

## API

- POST /v1/search — resolve a flight and available dates.
- GET /v1/status — read one selected flight/date/direction.
- POST /v1/tracks requires a candidate_id returned by a recent search; those
  candidate records expire after one hour. GET /v1/tracks and DELETE
  /v1/tracks/{id} manage Kuzmich's subscriptions.
- GET /v1/events, POST /v1/events/{id}/ack — deliver changes to the Mac poller.
- GET /health — local process health only; all other routes require a bearer
  key of at least 32 characters.

## Consequences

Kuzmich can search, ask which date to follow when the result is ambiguous,
create or cancel tracks, and send live changes in his own voice. If the SSH
tunnel or either service is down, new lookups report that flight tracking is
temporarily unavailable; queued change events remain on Riga.

## Activation

Production activation requires a reviewed GitHub PR, a dedicated API token
stored only in the Mac Keychain and Riga's protected .env, a local SSH tunnel
LaunchAgent, and explicit user confirmation before deployment.
