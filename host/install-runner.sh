#!/bin/bash
# Battle Bridge: install or update the host runner (jobs, drives, updates,
# the reboot light).  Optional: the dashboard works without it.
#
#   sudo bash install-runner.sh            install, or update after a new release
#   sudo bash install-runner.sh --remove   take it all out again
#
# It says what it will do and asks once.  Safe to run again: it replaces its
# own files and nothing else.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$here/runner" ]; then src="$here/runner"; else src="$(dirname "$here")/runner"; fi
GID=10220
LIB=/usr/local/lib/battle-bridge
B=$'\e[1m'; G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; N=$'\e[0m'

die() { echo "${R}$*${N}" >&2; exit 1; }
ask() { read -r -p "$1 [y/N] " ok </dev/tty || ok=n; [[ "$ok" =~ ^[Yy]$ ]]; }

[ "$(id -u)" -eq 0 ] || die "Run it with sudo:  sudo bash $0
It installs a system service and files in /usr/local, /etc and /var, which need root."

if [ "${1:-}" = "--remove" ]; then
  cat <<TXT

${B}This will remove:${N}
  - bb-runner.service, $LIB and /etc/sudoers.d/battle-bridge
  - /var/lib/battle-bridge (the runner's facts, not your dashboard's settings)
It keeps /etc/battle-bridge (your drop-in files), /var/log/battle-bridge and
the 'battlebridge' group.  Delete those yourself if you want them gone.

TXT
  ask "Remove the runner?" || { echo "Nothing changed."; exit 0; }
  systemctl disable --now bb-runner.service 2>/dev/null || true
  rm -f /etc/systemd/system/bb-runner.service /etc/sudoers.d/battle-bridge
  rm -rf "$LIB" /var/lib/battle-bridge
  systemctl daemon-reload
  echo "${G}Removed.${N}"
  exit 0
fi

# Whose account the runner uses: the person running sudo.  It needs to read
# your compose files, and AUR helpers refuse to run as root.
USER_NAME="${BB_USER:-${SUDO_USER:-}}"
[ -n "$USER_NAME" ] && [ "$USER_NAME" != root ] || die "Run it with sudo from your own account, or set BB_USER=you."
id "$USER_NAME" >/dev/null 2>&1 || die "No user $USER_NAME."
GROUP_NAME="$(id -gn "$USER_NAME")"
getent group docker >/dev/null || die "No docker group.  Is Docker installed?"
command -v python3 >/dev/null || die "python3 not found."
[ -f "$src/bb-runner.py" ] || die "Can't find the runner's files next to this script ($src)."
existing=$(getent group "$GID" | cut -d: -f1 || true)
if [ -n "$existing" ] && [ "$existing" != battlebridge ]; then
  die "Group id $GID is already used by '$existing'.  Stopping before changing anything."
fi
pyok=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))')

cat <<TXT

${B}This will:${N}
  - make a group 'battlebridge' (id $GID) and add $USER_NAME to it
    (the dashboard's container runs with the same group id, so only it can
    use the runner's socket)
  - install bb-runner to $LIB, owned by root so neither $USER_NAME
    nor the container can edit it
  - add one sudoers line, /etc/sudoers.d/battle-bridge, for the read-only
    root-facts script only (checked with visudo first)
  - make /etc/battle-bridge (for your own drop-in jobs, checks and boxes),
    /var/lib/battle-bridge and /var/log/battle-bridge
  - install and start bb-runner.service, running as $USER_NAME

It doesn't touch any other service, compose file or sudoers rule.

Why sudo: everything above is a system file or service, so it needs root.
The runner itself then runs as $USER_NAME, not root.  The sudoers line lets
it read drive health (SMART data), which only root can read, without a
password.  It allows one read-only script, with no arguments, and nothing
else.  To take it all out later:  sudo bash $0 --remove
TXT
command -v sudo >/dev/null || echo "${Y}Note: sudo isn't installed.  Drive health and the PC update job need it.${N}"
command -v smartctl >/dev/null || echo "${Y}Note: smartctl isn't installed, so there's no drive health.  Install smartmontools.${N}"
if [ -f /etc/arch-release ] && ! command -v checkupdates >/dev/null; then
  echo "${Y}Note: checkupdates isn't installed, so pending updates can't be counted.  Install pacman-contrib.${N}"
fi
[ "$pyok" = 1 ] || echo "${Y}Note: drop-in files need Python 3.11 or newer.  Everything else works.${N}"
echo
ask "Carry on?" || { echo "Nothing changed."; exit 0; }

echo "${B}Group${N}"
getent group battlebridge >/dev/null || groupadd -g "$GID" battlebridge
id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx battlebridge || usermod -aG battlebridge "$USER_NAME"

echo "${B}Folders${N}"
install -d -o "$USER_NAME" -g battlebridge -m 750 /var/lib/battle-bridge
install -d -o "$USER_NAME" -g "$GROUP_NAME" -m 755 /var/log/battle-bridge /var/log/battle-bridge/jobs \
  /var/log/battle-bridge/pc-jobs
install -d -o root -g root -m 755 /etc/battle-bridge /etc/battle-bridge/jobs.d \
  /etc/battle-bridge/checks.d /etc/battle-bridge/panels.d
if [ ! -f /etc/battle-bridge/runner.toml ] && [ -f "$src/runner.toml" ]; then
  install -o root -g root -m 644 "$src/runner.toml" /etc/battle-bridge/runner.toml
fi

echo "${B}Runner files${N}"
install -d -o root -g root -m 755 "$LIB" "$LIB/bbrunner" "$LIB/pc-jobs"
install -o root -g root -m 755 "$src/bb-runner.py" "$LIB/bb-runner.py"
install -o root -g root -m 644 "$src"/bbrunner/*.py "$LIB/bbrunner/"
install -o root -g root -m 755 "$src/root-facts" "$LIB/root-facts"
install -o root -g root -m 755 "$src/pc-jobs/update-system" "$src/pc-jobs/reboot" "$LIB/pc-jobs/"
rm -rf "$LIB/bbrunner/__pycache__"

echo "${B}Sudoers${N}"
tmp=$(mktemp)
sed "s/@USER@/$USER_NAME/" "$src/battle-bridge-sudoers" > "$tmp"
chmod 440 "$tmp"
command -v visudo >/dev/null || { rm -f "$tmp"; die "visudo not found.  Install sudo first."; }
visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; die "The sudoers line didn't pass visudo.  Not installed."; }
install -o root -g root -m 440 "$tmp" /etc/sudoers.d/battle-bridge
rm -f "$tmp"
visudo -c >/dev/null || die "visudo reports a problem with the sudoers files."

echo "${B}Service${N}"
sed -e "s/@USER@/$USER_NAME/" -e "s/@GROUP@/$GROUP_NAME/" "$src/bb-runner.service" > /etc/systemd/system/bb-runner.service
chmod 644 /etc/systemd/system/bb-runner.service
systemctl daemon-reload
systemctl enable bb-runner.service >/dev/null 2>&1
systemctl restart bb-runner.service
sleep 3

echo
if systemctl is-active --quiet bb-runner.service; then
  echo "${G}bb-runner is running.${N}  Reload the dashboard: jobs, drives and updates fill in within a minute."
else
  echo "${R}bb-runner didn't start.  See:  journalctl -u bb-runner -n 30 --no-pager${N}"
  exit 1
fi
