# Battle Bridge.  Prebuilt on GitHub's registry; this is how it's made.
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# A fixed user and group, so the host runner's socket can be shared by
# group id (10220) and nothing else.
RUN groupadd -g 10220 bb && useradd -u 10220 -g 10220 -M -d /nonexistent -s /usr/sbin/nologin bb \
 && mkdir -p /data && chown 10220:10220 /data

WORKDIR /app
COPY app/ /app/
# The host runner travels in the image, so its version always matches.
# Copy it out with:  docker cp battle-bridge:/opt/battle-bridge/host ./battle-bridge-host
COPY host/install-runner.sh /opt/battle-bridge/host/install-runner.sh
COPY runner/ /opt/battle-bridge/host/runner/

USER 10220:10220
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python3", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('BB_LISTEN_PORT', '8080'), timeout=4)"]
CMD ["python3", "-m", "battle_bridge"]
