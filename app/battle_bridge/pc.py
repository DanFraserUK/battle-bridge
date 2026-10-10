"""Makes the PC link handler and its installer, from the PC jobs settings.

The handler is a short bash script on the user's Linux desktop.  The
browser hands it links like battlebridge://update-system.  It accepts three
kinds of link, each mapped to a fixed command baked in when it was made:

    battlebridge://update-system     the server's update script, over SSH
    battlebridge://reboot            the server's reboot script, over SSH
    battlebridge://terminal/<n>      a shell on the server in folder number n

Anything else is refused, and nothing from the link ever reaches a command.
The folders are fixed when the handler is made, so a web page can only pick
one by number.
"""

import shlex
import time

from . import VERSION

PC_JOBS = "/usr/local/lib/battle-bridge/pc-jobs"

# How each terminal runs a command.  $title and "$@" are filled in by the
# handler's run_in_terminal function.
TERMINAL_LINES = {
    "konsole": 'exec konsole --separate -p tabtitle="$title" -e "$@"',
    "gnome-terminal": 'exec gnome-terminal --title="$title" -- "$@"',
    "ptyxis": 'exec ptyxis --new-window -- "$@"',
    "xfce4-terminal": 'exec xfce4-terminal --title="$title" -x "$@"',
    "kitty": 'exec kitty --title "$title" "$@"',
    "alacritty": 'exec alacritty --title "$title" -e "$@"',
    "foot": 'exec foot --title="$title" "$@"',
    "wezterm": 'exec wezterm start -- "$@"',
    "xterm": 'exec xterm -T "$title" -e "$@"',
}


def remote_command(folder):
    """The command run on the server to open a shell in a folder.  Folders
    are checked when saved (no quotes, $, backslashes or backticks), so
    single quotes are safe around them."""
    shell = 'exec "$SHELL" -l'
    if folder == "~":
        return f"cd && {shell}"
    if folder.startswith("~/"):
        return f"cd -- \"$HOME\"/'{folder[2:]}' && {shell}"
    return f"cd -- '{folder}' && {shell}"


def handler(pc):
    """The handler script, as text."""
    target = pc["ssh_target"]
    folders = pc["folders"]
    lines = "\n".join(f"  {shlex.quote(remote_command(f))}   # {i}: {f}" for i, f in enumerate(folders))
    names = "\n".join(f"  {shlex.quote(f)}" for f in folders)
    return f"""#!/bin/bash
# Battle Bridge link handler {VERSION}, made {time.strftime('%Y-%m-%d %H:%M')}.
#
# The browser hands it links like battlebridge://update-system.  It accepts
# update-system, reboot and terminal/<number>, each mapped to a fixed
# command below.  Everything else is refused, and nothing from the link is
# ever passed on to a command.  Remake it on the PC jobs setup page.

TARGET={shlex.quote(target)}
PORT={int(pc["ssh_port"])}
FOLDERS=(
{names}
)
REMOTE=(
{lines}
)

refuse() {{
  notify-send -a "Battle Bridge" -i dialog-warning "Battle Bridge" \\
    "Refused a link it doesn't know.  Nothing was run." 2>/dev/null
  exit 1
}}

run_in_terminal() {{
  local title="$1"
  shift
  {TERMINAL_LINES[pc["terminal"]]}
}}

link="${{1:-}}"
[[ "$link" == battlebridge://* ]] || refuse
name="${{link#battlebridge://}}"
name="${{name%%\\?*}}"
name="${{name%/}}"

case "$name" in
  update-system)
    run_in_terminal "Battle Bridge: update system" \\
      ssh -t -p "$PORT" "$TARGET" "bash {PC_JOBS}/update-system" ;;
  reboot)
    run_in_terminal "Battle Bridge: reboot" \\
      ssh -t -p "$PORT" "$TARGET" "bash {PC_JOBS}/reboot" ;;
  *)
    [[ "$name" =~ ^terminal/(0|[1-9][0-9]?)$ ]] || refuse
    i="${{BASH_REMATCH[1]}}"
    [ "$i" -lt "${{#REMOTE[@]}}" ] || refuse
    run_in_terminal "Battle Bridge: ${{FOLDERS[$i]}}" \\
      ssh -t -p "$PORT" "$TARGET" "${{REMOTE[$i]}}" ;;
esac
"""


DESKTOP = """[Desktop Entry]
Type=Application
Name=Battle Bridge links
Comment=Opens a terminal for Battle Bridge jobs that need sudo
Exec=@HOME@/.local/bin/battlebridge-handler %u
Icon=utilities-terminal
NoDisplay=true
Terminal=false
MimeType=x-scheme-handler/battlebridge;
"""


def installer(pc, page_address):
    """A script that installs the handler for the user who runs it."""
    body = handler(pc)
    marker = "BATTLE_BRIDGE_HANDLER_END"
    return f"""#!/bin/bash
# Installs the Battle Bridge link handler for you (not root):
#   bash battlebridge-pc-install.sh
# Writes ~/.local/bin/battlebridge-handler and
# ~/.local/share/applications/battlebridge-handler.desktop, and makes it the
# handler for battlebridge:// links.  Nothing else.
set -euo pipefail
if [ "$(id -u)" -eq 0 ]; then echo "Run this as yourself, not root."; exit 1; fi
command -v {pc["terminal"]} >/dev/null || echo "Note: {pc["terminal"]} isn't installed.  Pick another terminal on the setup page."
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
cat > "$HOME/.local/bin/battlebridge-handler" <<'{marker}'
{body}{marker}
chmod 755 "$HOME/.local/bin/battlebridge-handler"
cat > "$HOME/.local/share/applications/battlebridge-handler.desktop" <<{marker}
{DESKTOP.replace("@HOME@", "$HOME")}{marker}
xdg-mime default battlebridge-handler.desktop x-scheme-handler/battlebridge
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installed.  Check:  xdg-mime query default x-scheme-handler/battlebridge"
echo "First use: your browser asks once whether to open battlebridge:// links."
echo "Firefox or LibreWolf with resistFingerprinting on: add {page_address} to"
echo "privacy.resistFingerprinting.exemptedDomains in about:config, or the terminal box draws wrongly."
"""
