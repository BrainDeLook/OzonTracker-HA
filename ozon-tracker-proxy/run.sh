#!/usr/bin/env bash
# Entry point for both the Home Assistant add-on and standalone Docker.
#
# As a HA add-on, Supervisor writes the user's options to /data/options.json.
# We map them to the environment variables app.py understands. The Playwright
# base image has no bashio, so options are parsed with Python (always present).
# In plain Docker there is no options.json and the container environment
# (e.g. from docker-compose) is used as-is.
set -euo pipefail

OPTIONS=/data/options.json

opt() {
  # opt <json-key> <default>
  python3 -c "import json,sys;print(json.load(open('${OPTIONS}')).get('$1', '$2'))"
}

if [ -f "${OPTIONS}" ]; then
  LEVEL="$(opt log_level info)"
  # Python logging expects upper-case level names.
  export LOG_LEVEL="$(printf '%s' "${LEVEL}" | tr '[:lower:]' '[:upper:]')"

  APP_VERSION="$(opt app_version '')"
  [ -n "${APP_VERSION}" ] && export OZON_APP_VERSION="${APP_VERSION}"

  NAV_TIMEOUT="$(opt nav_timeout_ms '')"
  [ -n "${NAV_TIMEOUT}" ] && export OZON_NAV_TIMEOUT_MS="${NAV_TIMEOUT}"

  DEBUG="$(opt debug false)"
  [ "${DEBUG}" = "True" ] || [ "${DEBUG}" = "true" ] && export OZON_DEBUG=1
fi

# A headed browser evades anti-bot detection far better than headless, but it
# needs an X display. Run under a virtual framebuffer when available so app.py
# can launch Chromium headed; otherwise fall back to headless.
if [ "${OZON_HEADLESS:-0}" != "1" ] && command -v xvfb-run >/dev/null 2>&1; then
  echo "[ozon-tracker-proxy] starting under xvfb (log level: ${LOG_LEVEL:-INFO})"
  exec xvfb-run -a --server-args="-screen 0 1280x800x24 -nolisten tcp" python3 app.py
fi

echo "[ozon-tracker-proxy] starting headless (log level: ${LOG_LEVEL:-INFO})"
exec python3 app.py
