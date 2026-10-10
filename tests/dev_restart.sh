#!/bin/bash
# Restart the dev server in the background.  bash tests/dev_restart.sh [run_dev.sh options]
# Uses $BB_TMP for its files (default .dev in the repo: Unix socket paths
# must stay under 108 bytes, so keep it short).
pkill -f '^python3 -m battle_bridge$'
pkill -f '^python3 .*(fake_docker|fake_services|dev_runner)\.py'
pkill -f 'http.server 18080'
sleep 1
export BB_TMP="${BB_TMP:-$(cd "$(dirname "$0")/.." && pwd)/.dev}"
mkdir -p "$BB_TMP"
setsid bash "$(dirname "$0")/run_dev.sh" "$@" > "$BB_TMP/log.txt" 2>&1 < /dev/null &
for i in $(seq 1 30); do
  curl -s -o /dev/null http://127.0.0.1:8220/api/session && break
  sleep 0.5
done
sleep 12   # let the slow loop record a couple of samples
echo "dev server up"
