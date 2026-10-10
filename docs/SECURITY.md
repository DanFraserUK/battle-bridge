# Security model

Battle Bridge can restart containers and update a server, so it's built
so that a web page can only ever ask for a fixed job by name.

## The pieces

```
browser ──► battle-bridge (container, uid 10220, read-only, no capabilities)
              │  read-only Docker API            │  Unix socket (group 10220)
              ▼                                  ▼
          bb-dockerproxy ──► docker.sock     bb-runner (host, your account)
          (refuses writes)                     fixed job list, no shell
                                               │  one sudoers line
                                               ▼
                                             root-facts (reads only)

PC: battlebridge:// link ──► link handler ──► terminal ──► ssh -t ──► sudo asks there
```

## What each part may do

**The container** reads host stats from `/proc` and `/sys` (read-only),
reads Docker through the proxy, and talks to the runner's socket.  It never
sees the Docker socket, and runs as a non-root user with a read-only
filesystem, no capabilities and `no-new-privileges`.

**The socket proxy** allows container lists, stats, inspect and logs, and
refuses every write (`POST=0`).  It has no address on your network.

**The host runner** runs as the account that installed it, in the docker
group.  It accepts a job name and at most one argument, and the argument
must exactly match a compose project or container Docker reports right
now.  Every command is a fixed argument list, run without a shell.  It runs
one job at a time and logs every run.  Its own files are root-owned, so
neither your account nor the container can change them.  systemd runs it
with `ProtectSystem=strict`.

Docker access is root-equivalent, so the runner's account is too.  The real
protection is the fixed list and the socket: only processes in the
container's group can connect, and the list has no "run this" job.

**root-facts** is the only thing that runs as root, through one sudoers
line.  It takes no arguments and only reads: `smartctl`, `btrfs`, `zpool`
and the dates on Nginx Proxy Manager's certificates.

**Drop-in jobs** are read only from root-owned files that nobody else can
write.  Otherwise the runner's own account could add a job.

**PC jobs** go through a link handler on your desktop that accepts exactly
`update-system`, `reboot` and `terminal/<number>`.  The folders are baked in
when you download it, so a page can only pick one by number.  sudo asks for
your password in the terminal, over SSH.  It never reaches the web page.

## The web side

- Clients must be on an allowed network (private ranges by default).
- Requests must use an address, or a hostname you've allowed.  This stops
  DNS rebinding: a page elsewhere can't point a name at your server and
  reach it through your browser.
- Every change needs the login.  Writes must carry a custom header and come
  from the same origin.  Session cookies are `HttpOnly` and
  `SameSite=Strict`.
- Five wrong passwords (or setup codes) in fifteen minutes lock logins for
  fifteen minutes.
- The first password needs a setup code from the container's log, so
  nobody else on the network can set it first.
- Settings saves quote a revision number, so an old tab can't overwrite
  newer changes.
- Keys for other services are stored in the container's database volume and
  are never sent to the browser.  The setup page only learns whether one is
  set.  The UniFi key is only sent once the gateway's certificate is pinned.
- A strict Content Security Policy: scripts, styles and data from the page's
  own address only, no inline scripts or styles, no framing.

## Outside fetches

The container fetches nothing from the internet.  The runner checks, once a
day, the registries your images came from (a HEAD request for the digest, no
download), Arch news on Arch, and your external IP from api.ipify.org if
you run Cloudflare DDNS.  Panels fetch the addresses you give them.

## Reporting a problem

Please open a private security advisory on GitHub rather than a public
issue.
