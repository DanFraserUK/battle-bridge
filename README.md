# Battle Bridge

A full-screen dashboard for a Linux home server running Docker.  Live stats,
a tile for every container, what's waiting to update, drive health, and the
everyday jobs as buttons.  Built to stay on your home network.  Original
work on my own server dashboard was human made with some AI webpage and
design, but I had £100 free claude cloud code credits so I burned them all
this public version for the fun of it.

![The bridge at 2560x1440, with made-up data](docs/screenshot.png)

## What it shows

- **System:** CPU per core, temperature, memory, network, load, and an
  NVIDIA GPU if you have one.  Click any figure for its last 24 hours.
- **Services:** a tile per container, with state, uptime, CPU and memory,
  and a link to its web page.  Links are found for you (see below).
- **Media, downloads and network:** Plex and Jellyfin streams, Sonarr and
  Radarr queues and what's coming, Transmission, Prowlarr, Seerr, Pi-hole,
  a UniFi gateway, Cloudflare DDNS and Nginx Proxy Manager's certificates.
  Each turns on when it's found and given a key.
- **Drives:** space per filesystem, SMART health per disk, btrfs error
  counters and scrubs, ZFS pools.
- **Updates waiting:** system packages (Arch and the AUR, Debian and Ubuntu,
  Fedora), newer container images, a reboot-needed light, and Arch news on
  Arch.
- **Scheduled jobs:** your systemd timers and how they last ended, plus
  outcome checks you write yourself.
- **Your own boxes:** figures from any JSON file or address.

The **Engineering** drawer runs jobs in a live terminal: status, logs,
restart, update one or all (DNS containers last), rebuild, prune, validate
compose files, check for updates, after-reboot checks, and any jobs you add.
Jobs that need sudo (system update, reboot) open a terminal on your Linux
desktop over SSH instead, so your password never goes near the web page.

## Setup

Each step says what it changes.  Only step 3 needs sudo.

1. **Start it.**  On the server, this makes a `battle-bridge` folder,
   downloads `compose.yml` from the
   [latest release](https://github.com/DanFraserUK/battle-bridge/releases/latest)
   into it, and starts two containers: Battle Bridge and its read-only
   Docker proxy.  Nothing outside that folder and Docker changes.

   ```bash
   mkdir battle-bridge && cd battle-bridge && curl -fsSLO https://github.com/DanFraserUK/battle-bridge/releases/latest/download/compose.yml && docker compose up -d
   ```

   No sudo needed if your account is in the `docker` group, which is usual
   on a Docker server.  If Docker says "permission denied", it isn't: put
   `sudo` in front of each `docker` command, or add yourself to the group.

   Want an NVIDIA GPU shown, or its own LAN address?  Uncomment those parts
   of `compose.yml` first.

2. **Set a password.**  Open `http://<your server's address>:8080`.  The
   setup page asks for a setup code, to prove you're the person who started
   it and not someone else on your network.  This reads it from the
   container's log, and changes nothing:

   ```bash
   docker logs battle-bridge 2>&1 | grep "setup code"
   ```

   Then set a password, add keys for the services it found, and you're done.

3. **Optional: the host runner**, for jobs, drives, updates and the reboot
   light.  On the server:

   ```bash
   docker cp battle-bridge:/opt/battle-bridge/host ./battle-bridge-host && sudo bash battle-bridge-host/install-runner.sh
   ```

   The first half copies the installer out of the container, into a
   `battle-bridge-host` folder.  The second half runs it with sudo, so it
   asks for your password.  It needs root because it installs system parts:

   - a systemd service, `bb-runner`, so the runner starts at boot
   - the runner itself in `/usr/local/lib/battle-bridge`, owned by root so
     nothing else can change it
   - one sudoers line, so the runner can read drive health (SMART), which
     only root can read, without asking for a password.  It allows one
     read-only script, with no arguments, and nothing else.
   - a `battlebridge` group, and folders in `/etc`, `/var/lib` and
     `/var/log`

   It lists all of that and asks before changing anything.  The runner then
   runs as you, not as root.  `sudo bash battle-bridge-host/install-runner.sh --remove`
   takes it all out again.

4. **Optional: PC jobs.**  On the setup page, pick your terminal and SSH
   login, download the link handler, and run it on your PC:

   ```bash
   bash ~/Downloads/battlebridge-pc-install.sh
   ```

   No sudo: it only writes two files in your home folder and tells your
   desktop to open `battlebridge://` links with them.  Later, the Update
   system and Reboot buttons open a terminal on your PC, logged in to the
   server over SSH.  Those two ask for your sudo password in that terminal,
   because updating packages and rebooting need root.  The password goes to
   the server over SSH.  Battle Bridge never sees it.

`docker exec battle-bridge python3 -m battle_bridge check` tries every data
source once and says plainly what works.  It changes nothing.

To update: `cd battle-bridge && docker compose pull && docker compose up -d`.
That downloads the new image and restarts the container with it.  Your
settings are kept.  If you use the host runner, run the step 3 line again
to update it too.  To stay on one version, change `latest` in `compose.yml`
to a version such as `1.0.0`.

## How links are found

For each container, in order: a link you type on the setup page, then Docker
labels, then built-in defaults for about fifty common images, then a quick
probe of its ports.  Labels look like this:

```yaml
labels:
  battlebridge.port: "8080"        # the container's own port
  battlebridge.path: "/admin/"
  battlebridge.label: "My app"
  battlebridge.icon: "grafana"     # any icon in app/static/icons
  # or the whole thing:  battlebridge.url: "https://{server}:8443/"
  # or no link at all:   battlebridge.link: "none"
  # updated last by "Update all", on its own:  battlebridge.update: "last"
```

The address comes from Docker: the container's own LAN address on a
macvlan network, or the server's address and the published port.

## Your own jobs, checks and boxes

Small TOML files in root-owned folders on the server:
`/etc/battle-bridge/jobs.d`, `checks.d` and `panels.d`.  See
[examples](examples/).

## Safety

- Home network only by default, and only by address.  Requests by hostname
  are refused unless you allow that hostname, which stops DNS rebinding.
- The container never sees the Docker socket.  It reads Docker through a
  read-only proxy that refuses every write.
- The host runner runs only jobs from a fixed list, over a Unix socket only
  the container's group can use.  No shell, no free commands: a job is a
  name, plus at most one compose project or container that Docker itself
  reports.
- Anything needing sudo runs in a terminal on your PC, never in the page.
- One password for every button that changes anything, with a lockout after
  repeated wrong tries.  Keys for other services stay on the server and are
  never sent to the browser.
- A strict Content Security Policy, and nothing loaded from elsewhere.

More in [docs/SECURITY.md](docs/SECURITY.md).

## More

- [docs/NETWORK.md](docs/NETWORK.md): its own LAN address with macvlan
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): things that have
  caught people out
- [docs/DEVELOPING.md](docs/DEVELOPING.md): tests and the dev server

The layout is made for a 2560x1440 screen, edge to edge.  Smaller screens
get a stacked layout that scrolls.

MIT licence.  xterm.js is included under its own MIT licence.
