# Changelog

## 0.6.0

- Initial release of the Ozon Tracker Proxy add-on.
- Headless Chromium (Playwright) solves the tracking.ozon.ru anti-bot
  challenge and serves the tracking JSON at `GET /track/{number}`.
- Options: `log_level`, `app_version`, `nav_timeout_ms`.
