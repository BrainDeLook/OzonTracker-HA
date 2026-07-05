# Changelog

## 0.7.2

- The tracking URL serves the anti-bot challenge page directly, so change the
  solve flow: warm up on the site root, wait for the network to settle so the
  challenge JS can set its cookie, then re-navigate to the tracking page
  through the challenge (retry loop) and capture the app's BFF response.
- Richer diagnostics: log total responses, challenge-asset count and attempts;
  dump more of the challenge page HTML (3000 chars) in `debug` mode.

## 0.7.1

- Fix the add-on hanging at start with only `starting under xvfb` in the log
  and no HTTP server: replace the flaky `xvfb-run` wrapper with an explicit
  `Xvfb :99` launch, and run Python unbuffered so logs appear immediately.
- Browser launch failures no longer take down the HTTP server: `/healthz`
  stays up and `/track` returns HTTP 503 with a clear message.
- Headed launch now falls back to headless automatically if it fails.

## 0.7.0

- Solve the anti-bot challenge far more reliably: run a **headed** Chromium
  under a virtual display (xvfb) instead of easily-detected headless, and
  apply stealth patches (`navigator.webdriver`, languages, plugins, chrome
  runtime) before site scripts run.
- After navigation, poll the BFF endpoint with the browser's own cookies so
  the data is fetched as soon as the challenge clears.
- Log the final URL and the BFF status codes seen while solving.
- New `debug` option: dumps the challenge page HTML when solving fails.
- `OZON_HEADLESS=1` forces headless if no display is available.

## 0.6.2

- Fix "No module named 'playwright'" on start: pin `playwright==1.48.0` to
  match the base image tag so pip installs the exact version whose Chromium
  build ships in the image (0.6.1 removed it entirely, but the build's
  `python3` only sees Playwright when pip installs it).

## 0.6.1

- Fix add-on start crash ("Executable doesn't exist … chromium_headless_shell"):
  stop reinstalling/upgrading Playwright over the base image, which pulled a
  version whose Chromium build was missing. Playwright now comes solely from
  the pinned base image.

## 0.6.0

- Initial release of the Ozon Tracker Proxy add-on.
- Headless Chromium (Playwright) solves the tracking.ozon.ru anti-bot
  challenge and serves the tracking JSON at `GET /track/{number}`.
- Options: `log_level`, `app_version`, `nav_timeout_ms`.
