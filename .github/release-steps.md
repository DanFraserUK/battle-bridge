<!-- standard steps: added to every release by .github/workflows/release.yml -->

## Updating

From 1.2.0, an **Update available** button shows in the top bar within a day of a new release.  Click it, and it pulls the new image and restarts Battle Bridge.  It needs the host runner, and the `latest` tag in `compose.yml`, which is the default.

Without the host runner, or coming from 1.0 or 1.1, update by hand on the server:

1. Pull the new image and restart Battle Bridge.  Your settings are kept.

   ```bash
   cd ~/battle-bridge && docker compose pull && docker compose up -d
   ```

2. If you use the host runner, update it too.  It asks before changing anything.

   ```bash
   cd ~/battle-bridge && docker cp battle-bridge:/opt/battle-bridge/host ./battle-bridge-host && sudo bash battle-bridge-host/install-runner.sh
   ```

3. Refresh the page with Ctrl+Shift+R.

## First time installing

On a Linux server running Docker.  Full details are in the [README](https://github.com/DanFraserUK/battle-bridge#setup).

1. **Start it.**  This makes a `battle-bridge` folder, downloads `compose.yml` into it, and starts Battle Bridge and its read-only Docker proxy.

   ```bash
   mkdir ~/battle-bridge && cd ~/battle-bridge && curl -fsSLO https://github.com/DanFraserUK/battle-bridge/releases/latest/download/compose.yml && docker compose up -d
   ```

   Want an NVIDIA GPU shown, or its own LAN address?  Uncomment those parts of `compose.yml` first, then run `docker compose up -d` again.

2. **Set a password.**  Open `http://<your server's address>:8080`.  It asks for a setup code, which this reads from the log:

   ```bash
   docker logs battle-bridge 2>&1 | grep "setup code"
   ```

3. **Optional, but needed for updates, jobs and drive health: the host runner.**  It lists what it installs and asks before changing anything.

   ```bash
   cd ~/battle-bridge && docker cp battle-bridge:/opt/battle-bridge/host ./battle-bridge-host && sudo bash battle-bridge-host/install-runner.sh
   ```
