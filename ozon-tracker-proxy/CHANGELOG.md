# Changelog

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
