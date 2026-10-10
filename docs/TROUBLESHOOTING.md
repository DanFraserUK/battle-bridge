# Troubleshooting

Start with the check, which tries every data source once and changes
nothing:

```bash
docker exec battle-bridge python3 -m battle_bridge check
```

## Locked out

- **Forgotten password:**
  `docker exec -it battle-bridge python3 -m battle_bridge set-password`
- **Set the allowed networks wrong:**
  `docker exec battle-bridge python3 -m battle_bridge reset-access`
- **"Use the address, not a hostname":** you reached it by name.  Use the
  address, or add the name on the setup page under Access.

## The terminal box draws wrongly, or text is double size

Firefox and LibreWolf with `privacy.resistFingerprinting` on scramble the
canvas the terminal draws on, and fake the screen's pixel ratio.  Exempt
just Battle Bridge: in `about:config`, add its address to
`privacy.resistFingerprinting.exemptedDomains` (comma separated), then
reload.

## PC job buttons do nothing

- The browser asks once whether to open `battlebridge://` links.  If you
  said no, Firefox keeps that under Settings, Applications.
- Check the handler is registered:
  `xdg-mime query default x-scheme-handler/battlebridge` should say
  `battlebridge-handler.desktop`.
- Firefox-based browsers may need `network.protocol-handler.external.battlebridge`
  set to `true` in `about:config`.
- Changed the folders or SSH login?  Download the handler again.

## A service says "no answer"

Battle Bridge reaches services at the address it finds in Docker.  If that
doesn't work from inside its container (macvlan without a shim, an unusual
port), type the address on the setup page under Services and keys.

## Sonarr, Radarr or Prowlarr refuse the key

Recent versions warn when Allowed Hosts is empty, and some setups then
refuse requests by address.  In the app, Settings, General, include the
app's own address in Allowed Hosts, not just its hostname.

## No logs for Cloudflare DDNS

The socket proxy refuses container logs unless `ALLOW_LOGS: 1` is set.  It
is in the published `compose.yml`.  Still read-only.

## The host runner

The runner is a system service, so restarting it needs sudo.  Reading its
status doesn't.  Reading its log may, depending on your distro: if
`journalctl` shows nothing, put `sudo` in front.

- Status: `systemctl status bb-runner --no-pager`
- Its log: `journalctl -u bb-runner -n 50 --no-pager`
- Drive health missing: install `smartmontools`.  It shows a note saying so.
- Arch, no updates counted: install `pacman-contrib` for `checkupdates`.
- Debian and Ubuntu: the count comes from the package lists apt already
  has.  They refresh daily through `apt-daily.timer`.
- "sudo refused root-facts": the one sudoers line that lets the runner
  read drive health is missing or out of date.  Run the install line again.
  It rewrites it.
- The runner redoes its quick checks every time it starts, so a restart
  never shows stale figures: `sudo systemctl restart bb-runner`.
- Don't add `ProtectKernelModules=yes` to the unit.  It hides
  `/usr/lib/modules`, which the reboot check reads.

## After-reboot checks cross the drives

They check every drive `/etc/fstab` mounts at boot.  If one is meant to be
absent sometimes, list the ones to check in `/etc/battle-bridge/runner.toml`
(`after_reboot_mounts`).
