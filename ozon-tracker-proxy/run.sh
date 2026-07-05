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

  ENGINE_OPT="$(opt engine camoufox)"
  [ -n "${ENGINE_OPT}" ] && export OZON_ENGINE="${ENGINE_OPT}"
fi

# Make Python log lines appear immediately in the add-on log.
export PYTHONUNBUFFERED=1

ENGINE="${OZON_ENGINE:-camoufox}"

# The Camoufox browser is fetched at runtime (not at build) so the image builds
# even when GitHub's API rate limit blocks the download (common behind CGNAT).
# Cache it in persistent /data so it is downloaded once and reused after that.
if [ "${ENGINE}" = "camoufox" ]; then
  if [ -d /data ] && [ -w /data ]; then
    export XDG_CACHE_HOME=/data/.cache
  fi
  mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"
  CAMDIR="${XDG_CACHE_HOME:-$HOME/.cache}/camoufox"
  if [ ! -d "${CAMDIR}" ] || [ -z "$(ls -A "${CAMDIR}" 2>/dev/null)" ]; then
    echo "[ozon-tracker-proxy] Camoufox browser not cached; fetching (first run)..."
    for i in 1 2 3 4 5; do
      if python3 -m camoufox fetch; then break; fi
      echo "[ozon-tracker-proxy] camoufox fetch failed (attempt $i, GitHub rate limit?); retry in 45s"
      sleep 45
    done || true
  else
    echo "[ozon-tracker-proxy] Camoufox browser found in cache"
  fi
fi

# The camoufox engine manages its own virtual display (headless="virtual").
# For the Chromium fallback we start our own Xvfb so it can run *headed*
# (far less detectable than headless); app.py auto-detects DISPLAY.
if [ "${ENGINE}" != "camoufox" ] && [ "${OZON_HEADLESS:-0}" != "1" ] \
    && command -v Xvfb >/dev/null 2>&1; then
  echo "[ozon-tracker-proxy] starting virtual display :99"
  Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
  export DISPLAY=:99
  sleep 1
fi

echo "[ozon-tracker-proxy] launching app (engine=${ENGINE}, DISPLAY=${DISPLAY:-none}, log level: ${LOG_LEVEL:-INFO})"
exec python3 -u app.py
