"""Start Battle Bridge, or look after it.

    python3 -m battle_bridge                  run the server
    python3 -m battle_bridge check            try every data source once
    python3 -m battle_bridge setup-code       show the first-run setup code
    python3 -m battle_bridge set-password     set or change the login password
    python3 -m battle_bridge reset-access     back to the default networks, if a
                                              setting has locked you out
"""

import getpass
import logging
import signal
import sys

from . import VERSION, config
from .auth import Auth
from .db import DB
from .poller import Poller
from .sampler import Sampler
from .server import MIN_PASSWORD, App, make_server
from .store import Store


def set_password(db, auth):
    first = getpass.getpass("New password: ")
    if len(first) < MIN_PASSWORD:
        print(f"Use at least {MIN_PASSWORD} characters.  Nothing changed.")
        return 1
    if getpass.getpass("Same again: ") != first:
        print("They don't match.  Nothing changed.")
        return 1
    auth.set_password(first)
    print("Password set.  Any open logins have been logged out.")
    return 0


def _stop_on_term(*_):
    raise KeyboardInterrupt


def serve(cfg, db, auth, store):
    sampler = Sampler(cfg, db, store)
    sampler.start()
    poller = Poller(cfg, sampler, store)
    poller.start()
    srv = make_server(App(cfg, db, auth, store, sampler, poller=poller))
    signal.signal(signal.SIGTERM, _stop_on_term)
    logging.info("Battle Bridge %s listening on port %s", VERSION, cfg.listen_port)
    if not auth.has_password():
        logging.warning("First run.  Open the page in a browser and enter this setup code: %s",
                        auth.setup_code())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sampler.stop()
        poller.stop()
        srv.server_close()
    return 0


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    cfg = config.load()
    db = DB(cfg.db_path)
    auth = Auth(db, cfg)
    store = Store(db)
    cmd = argv[:1]
    if cmd == ["set-password"]:
        return set_password(db, auth)
    if cmd == ["setup-code"]:
        if auth.has_password():
            print("A password is already set, so there's no setup code.  Use set-password to change it.")
            return 1
        print(auth.setup_code())
        return 0
    if cmd == ["reset-access"]:
        store.reset_access()
        print("Allowed networks are back to the private ranges, and no hostnames are allowed.")
        return 0
    if cmd == ["check"]:
        from . import check
        logging.getLogger().setLevel(logging.ERROR)
        return check.run(cfg, db, auth, store)
    if argv:
        print(__doc__)
        return 2
    return serve(cfg, db, auth, store)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
