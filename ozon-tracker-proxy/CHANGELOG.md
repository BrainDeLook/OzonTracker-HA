# Changelog

## 0.10.1

- Fix the add-on build failing at `camoufox fetch` with GitHub API
  "rate limit exceeded" (hits unauthenticated 60 req/h per IP, common behind
  CGNAT). The Camoufox browser is no longer downloaded at build time.
- Instead it is fetched at first run into the persistent `/data` cache
  (`XDG_CACHE_HOME`), retried across restarts until it succeeds, then reused.
- While Camoufox isn't available yet, the service falls back to the `chromium`
  engine so it still builds, starts and answers.

## 0.10.0

- Add a **camoufox** (anti-detect Firefox) engine and make it the default,
  after Chromium — even patched with patchright — kept failing Ozon's
  abt-challenge (fingerprint-level detection under a GPU-less display).
  camoufox spoofs the whole fingerprint (WebGL, canvas, fonts, navigator).
- Runs headful under camoufox's built-in virtual display
  (`headless="virtual"`), with `os=windows`, `locale=ru-RU`, `humanize` and a
  persistent profile so a solved session survives restarts.
- New `engine` option: `camoufox` (default) or `chromium` (the previous
  patchright/Playwright path, kept as a fallback).
- Dockerfile fetches the Camoufox browser (`python -m camoufox fetch`).

## 0.9.0

- Fix the solver hanging with no `Solve` log line: cap each navigation at
  45 s (separate from the overall budget) and log every step (warm-up,
  each attempt with URL/challenge/BFF state) so progress is always visible.
- Behave more like a real visit: warm up on the site root, move the mouse,
  scroll and dwell before navigating to the tracking page.
- Move to the latest patchright (1.61.1) via `patchright install --with-deps
  chromium` for the strongest anti-detection patches.
- Enable software WebGL (`--enable-unsafe-swiftshader`) — a browser with
  WebGL disabled is itself a bot signal under a GPU-less virtual display.

## 0.8.0

- Switch the browser engine to **patchright**, an undetected fork of
  Playwright, because Ozon's abt-challenge detected vanilla Playwright's CDP
  `Runtime.enable` leak (the challenge assets loaded and JS ran, but never
  cleared, even though a normal browser on the same network passes).
- Use a **persistent browser profile** (`/data/ozon-profile` in the add-on)
  so a solved anti-bot session and its cookies survive restarts and are
  reused across lookups.
- Drop the aggressive launch flags and manual stealth script when patchright
  is active (it handles those itself; a double patch is itself detectable).
- The base image only provides Python/xvfb/system libs now — the browser
  comes from `patchright install chromium` in the Dockerfile.

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
