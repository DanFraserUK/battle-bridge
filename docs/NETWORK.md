# Its own address on your LAN (macvlan)

By default Battle Bridge publishes port 8080 on the server.  If your other
containers each have their own LAN address on a macvlan network, Battle
Bridge can have one too.

1. If you don't have a macvlan network yet, make one.  Change the subnet,
   gateway, address range and parent interface to suit your network.  The
   range should be addresses your router doesn't hand out.

   ```bash
   docker network create -d macvlan --subnet=192.168.1.0/24 --gateway=192.168.1.1 \
     --ip-range=192.168.1.192/26 -o parent=eth0 lan
   ```

2. In `compose.yml`, delete the `ports:` and `networks:` lines under
   `battle-bridge`, and uncomment the macvlan block below them.  Set the
   address and the network name.

3. `docker compose up -d`, then open `http://<that address>`.

## The host can't reach its own macvlan containers

That's how macvlan works.  It matters in two places:

- **From the server itself**, you can't open Battle Bridge's address.  Use
  another machine, or add a macvlan "shim" interface on the host.
- **Battle Bridge on the default bridge, other services on macvlan:** it
  can't reach their addresses to read their APIs.  Either put Battle Bridge
  on the macvlan too, or set each service's address on the setup page to a
  port it publishes on the server.

## Tile links when Battle Bridge has its own address

Links to containers on published ports use the server's address.  Battle
Bridge can't tell the server's address from the one you used to reach it,
so set it on the setup page under Access, "Server address for tile links".
