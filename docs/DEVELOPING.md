# Developing

Python standard library only, in both the container and the runner.  No
build step for the page: plain HTML, CSS and JavaScript, with xterm.js
vendored in `app/static/vendor`.

```
app/battle_bridge/   the web app: server, collectors, discovery, service clients
app/static/          the page and the setup page
runner/              bb-runner (host side), root-facts, PC job scripts, systemd unit
host/                install-runner.sh
examples/            drop-in jobs, checks and panels
tests/               unit tests, fakes, dev server, screenshots
```

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests run against fakes: a fake Docker API (`tests/fakes/fake_docker.py`),
a fake docker CLI (`tests/fakes/bin/docker`), and one fake server answering
as every service (`tests/fakes/fake_services.py`).

## The dev server

```bash
bash tests/dev_restart.sh --seed
```

Then open http://127.0.0.1:8220 (password `dev password 1`).  Options:
`--trouble` (things going wrong), `--fresh` (the first-run page),
`--nokeys`, `--norunner`.  Its files go in `.dev/` in the repo, because Unix
socket paths must stay under 108 bytes.

## Screenshots

With the dev server running, and Playwright installed:

```bash
mkdir -p screens && node tests/screens.mjs screens bridge jobs setup narrow
```

Set `PLAYWRIGHT_REQUIRE` to the folder holding Playwright's `node_modules`
if it isn't the default.

## A release

Bump `VERSION` in both `app/battle_bridge/__init__.py` and
`runner/bbrunner/__init__.py`, then publish a GitHub release tagged with the
same version.  The release workflow builds the image for amd64 and arm64 and
pushes it to `ghcr.io/<owner>/battle-bridge`.

## Rules of the house

- No inline styles or scripts: the CSP blocks them.  Use classes.
- Don't redraw a box on `change` or `blur`, or a click can land on a box
  that's just been replaced.  Boxes rebuild only when their data changes,
  and never while a button is held.
- Enter never submits a form, except the login forms.
- Make xterm.js only once its box is visible and settled, or it measures
  characters wrongly.
- Runner commands are argument lists.  Never a shell string.
