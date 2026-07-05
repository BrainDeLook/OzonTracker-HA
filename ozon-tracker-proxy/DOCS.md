# Ozon Tracker Proxy

Home Assistant add-on that lets the **Ozon Package Tracker** integration read
`tracking.ozon.ru` despite its JavaScript anti-bot challenge.

Ozon guards the tracking endpoint with a proof-of-work challenge (`fab_ichlg`):
its access token is produced by JavaScript and stored in a short-lived cookie,
so a plain HTTP client — even one that fakes headers or the TLS fingerprint —
gets HTTP 403. This add-on runs a real headless Chromium (Playwright) that
solves the challenge, keeps the cookie session alive and refreshes it
automatically, then serves the tracking data to the integration.

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**.
2. Open the **⋮** menu → **Repositories**, add:
   `https://github.com/BrainDeLook/OzonTracker-HA`
3. Install **Ozon Tracker Proxy** and start it. Enable **Start on boot** and
   **Watchdog**.
4. In the **Ozon Package Tracker** integration options set the
   **Headless-browser proxy URL** to `http://<HA-host>:8080`
   (e.g. `http://homeassistant.local:8080` or your Home Assistant IP).

> Architecture: only **amd64** and **aarch64** are supported — Playwright's
> Chromium is not available for armv7/armhf (older Raspberry Pi).

## Options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Log verbosity (`trace`…`fatal`). |
| `app_version` | `release/TPLAPI-5269` | Value of the `x-o3-app-version` header the browser sends. Update if Ozon starts rejecting it. |
| `nav_timeout_ms` | `60000` | Page navigation / challenge-solving timeout in ms. Raise it (e.g. `120000`) if the challenge needs longer. |
| `debug` | `false` | Log the challenge page HTML when solving fails (troubleshooting). |

## How it gets past the anti-bot

Ozon's challenge detects ordinary automation (e.g. Playwright's CDP
`Runtime.enable` leak), so the add-on drives Chromium with **patchright** — an
undetected fork of Playwright — running **headed** under a virtual display
(xvfb). It loads the tracking page, lets the challenge JavaScript run and set
its cookie, then reads the tracking JSON. The browser profile is kept in
`/data/ozon-profile`, so a solved session survives add-on restarts.

## Verifying it works

From any machine on your network:

```bash
curl http://<HA-host>:8080/healthz            # {"status": "ok"}
curl http://<HA-host>:8080/track/33310100-0168-1
```

The second call should return JSON with an `items` array. If it returns an
error or times out:

- Check the add-on log. A line like
  `Solve …: final_url=… bff_statuses=[403] solved=False` means the challenge
  did not clear.
- Raise `nav_timeout_ms` to `120000` and enable `debug` to log the page HTML.
- Make sure your Home Assistant host reaches Ozon from a **Russian** IP — a
  foreign VPN/VPS is blocked regardless of the browser.

## Notes

- Chromium needs roughly 350–500 MB RAM.
- The API has no authentication; keep the add-on on your local network only.
