#!/bin/bash
# Run Battle Bridge locally against the fakes, on http://127.0.0.1:8220
#   bash tests/run_dev.sh [--trouble] [--seed] [--fresh] [--nokeys] [--norunner]
#     --trouble   things going wrong: stopped containers, failing checks
#     --seed      24 hours of made-up history for the graphs
#     --fresh     no password yet: the first-run page
#     --nokeys    services found, but no keys set
#     --norunner  as if the host runner isn't installed
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="${BB_TMP:-$(mktemp -d)}"
has() { [[ " $* " == *" $1 "* ]]; }
args=()
for a in "$@"; do [[ "$a" == "--trouble" ]] && args+=(--trouble); done
mkdir -p "$TMP/www" && echo "hello" > "$TMP/www/index.html"
python3 -m http.server 18080 --bind 127.0.0.1 --directory "$TMP/www" >/dev/null 2>&1 &
WWW=$!
python3 "$ROOT/tests/fakes/fake_docker.py" 23750 --probe-port 18080 "${args[@]}" &
FAKE=$!
python3 "$ROOT/tests/fakes/fake_services.py" 23760 &
FAKE2=$!
PIDS="$WWW $FAKE $FAKE2"
trap 'kill $PIDS 2>/dev/null' EXIT
rm -f "$TMP/bb.db"
if [[ " $* " == *" --seed "* ]]; then
  python3 "$ROOT/tests/seed_history.py" "$TMP/bb.db" "$ROOT/app"
fi
mkdir -p "$TMP/r"
if [[ " $* " != *" --norunner "* ]]; then
  python3 "$ROOT/tests/fakes/make_facts.py" "$TMP/r" "${args[@]}"
  python3 "$ROOT/tests/fakes/dev_runner.py" "$TMP/r" &
  PIDS="$PIDS $!"
else
  rm -f "$TMP/r/facts.json" "$TMP/r/runner.sock"
fi
trap 'kill $PIDS 2>/dev/null' EXIT
cd "$ROOT/app"
BB_DB="$TMP/bb.db" python3 - "$@" <<'PY'
import sys
from battle_bridge import config, db, auth, store
c = config.load()
d = db.DB(c.db_path)
s = store.Store(d)
if "--fresh" not in sys.argv:
    auth.Auth(d, c).set_password("dev password 1")
    s.save({"pc": {"terminal": "konsole", "ssh_target": "me@192.168.1.10", "ssh_port": 22,
                   "folders": ["~", "/srv/compose/media"]},
            "unifi": {"url": "https://192.168.1.1", "fingerprint": ""}}, 0)
if "--nokeys" not in sys.argv and "--fresh" not in sys.argv:
    for k in ("plex_token", "jellyfin_key", "sonarr_key", "radarr_key", "prowlarr_key", "seerr_key",
              "pihole_password", "unifi_key", "transmission_pass"):
        s.set_secret(k, "test-key")
    s.set_secret("transmission_user", "user")
PY
F=http://127.0.0.1:23760
export BB_TEST_URL_PLEX=$F/plex BB_TEST_URL_JELLYFIN=$F/jellyfin BB_TEST_URL_SONARR=$F/sonarr \
  BB_TEST_URL_RADARR=$F/radarr BB_TEST_URL_PROWLARR=$F/prowlarr BB_TEST_URL_TRANSMISSION=$F/transmission \
  BB_TEST_URL_SEERR=$F/seerr BB_TEST_URL_PIHOLE=$F/pihole BB_TEST_URL_UNIFI=$F/unifi
BB_LISTEN_HOST=127.0.0.1 BB_LISTEN_PORT=8220 BB_HOST_GATEWAY=127.0.0.1 \
BB_DB="$TMP/bb.db" BB_HOST_PROC=/proc BB_HOST_SYS="$ROOT/tests/fakes/sys" \
BB_DOCKER_URL=http://127.0.0.1:23750 BB_RUNNER_DIR="$TMP/r" BB_NVIDIA_SMI="$ROOT/tests/fakes/nvidia-smi" \
  python3 -m battle_bridge
