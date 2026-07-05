# Works both as a Home Assistant add-on (Supervisor passes BUILD_FROM from
# build.yaml) and as a plain Docker image (the default below is used).
ARG BUILD_FROM=mcr.microsoft.com/playwright/python:v1.48.0-jammy
FROM ${BUILD_FROM}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# camoufox needs PyYAML with the libyaml C extension (CLoader).
RUN pip install --no-cache-dir --force-reinstall pyyaml

# NOTE: the Camoufox browser is NOT fetched at build time on purpose.
# `camoufox fetch` queries the GitHub API (rate-limited to 60 req/h per IP for
# unauthenticated callers — a hard blocker behind CGNAT). Instead run.sh fetches
# it at runtime into the persistent /data cache, retrying across restarts until
# it succeeds, then reusing it forever. The base image already ships Chromium +
# Playwright for the `chromium` fallback engine, so the add-on works meanwhile.

COPY app.py run.sh ./
RUN chmod a+x run.sh

ENV PORT=8080
ENV PYTHONUNBUFFERED=1
EXPOSE 8080

# Healthcheck for standalone Docker (Supervisor uses its own watchdog).
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["./run.sh"]
