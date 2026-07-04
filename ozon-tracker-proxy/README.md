# ozon-tracker-proxy

A tiny companion service for the
[Ozon Package Tracker](../README.md) Home Assistant integration.

`tracking.ozon.ru` is protected by a JavaScript proof-of-work anti-bot
challenge (`fab_ichlg`). The access token it produces can only be obtained by
running the challenge JavaScript in a real browser, so any plain HTTP client
(including one that fakes headers or the TLS fingerprint) is answered with
HTTP 403.

This service runs a real headless Chromium via Playwright, lets the page solve
the challenge, keeps the resulting cookie session alive, and exposes the
tracking data over a simple local HTTP API. The Home Assistant integration
points at it and never has to deal with the anti-bot itself.

## API

| Method | Path | Response |
|---|---|---|
| `GET` | `/track/{tracking_number}` | Raw Ozon BFF JSON (200); `{"error": …}` on 404/502 |
| `GET` | `/healthz` | `{"status": "ok"}` |

## Run with Docker Compose

```bash
docker compose up -d --build
curl http://localhost:8080/healthz          # {"status": "ok"}
curl http://localhost:8080/track/33310100-0168-1
```

Then set the integration's **Headless-browser proxy URL** option to
`http://<docker-host>:8080` (e.g. `http://homeassistant.local:8080`).

## Run without Docker

Outside the Playwright base image you must install Playwright yourself and
download its Chromium (inside Docker both are already provided by the base
image, so `requirements.txt` deliberately omits Playwright):

```bash
pip install -r requirements.txt          # aiohttp
pip install playwright
playwright install --with-deps chromium
python app.py                            # listens on :8080
```

## Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8080` | HTTP port to listen on |
| `OZON_APP_VERSION` | `release/TPLAPI-5269` | `x-o3-app-version` header value |
| `OZON_USER_AGENT` | Chrome 149 UA | Browser User-Agent |
| `OZON_NAV_TIMEOUT_MS` | `60000` | Navigation / request timeout |
| `LOG_LEVEL` | `INFO` | Python log level |

## Notes

- Chromium needs roughly 350–500 MB RAM; the compose file sets `shm_size: 512m`.
- Requests are serialized (one browser context), which is plenty for a home
  setup polling a handful of packages every 30 minutes.
- The host must reach `tracking.ozon.ru` from a Russian IP; a foreign
  VPN/VPS may be blocked regardless of the browser.
- Keep this service on your local network only — it has no authentication.
