# Drop-in examples

Your own jobs, checks and boxes, one small TOML file each.  Copy one into
the matching folder on the server, edit it, and it shows up within a
minute.  No restart needed.

```
/etc/battle-bridge/jobs.d/     a button in the Engineering drawer
/etc/battle-bridge/checks.d/   an outcome check in the Scheduled jobs box
/etc/battle-bridge/panels.d/   a stat box from a JSON file or address
```

For example:

```bash
sudo cp examples/checks.d/backup-fresh.toml /etc/battle-bridge/checks.d/ && sudo nano /etc/battle-bridge/checks.d/backup-fresh.toml
```

The rules, so a web page can never run something you didn't write:

- Files and folders must be owned by root and writable only by root.
  Anything else is ignored, and the page says why.
- The file name is the job's name: lower case letters, digits and dashes.
- `command` is a list of arguments, never a shell string.  The first item is
  a full path.  No pipes, no `&&`, no wildcards.  Put anything fancier in a
  root-owned script and run that.
- Commands run as the runner's user (the account that installed it), not
  root.  Anything needing sudo belongs in a terminal, not a button.
- Drop-ins need Python 3.11 or newer on the host.

## Jobs (`jobs.d`)

| Key | Needed | Meaning |
|-----|--------|---------|
| `label` | yes | The button's text |
| `command` | yes | The argument list to run |
| `group` | no | Which heading it sits under.  Default "Your jobs" |
| `confirm` | no | Asks this before running.  Setting it marks the job as one that changes things |
| `changes` | no | `true` or `false`.  Shown with an orange edge when true |
| `cwd` | no | Folder to run in |
| `interruptible` | no | `true` for jobs that never end by themselves, like following a log.  Starting another job stops it |

## Checks (`checks.d`)

| Key | Needed | Meaning |
|-----|--------|---------|
| `label` | yes | What it checks, as a statement: "Newest backup under a day old" |
| `command` | yes | The argument list to run |
| `expect` | no | `""` for no output, `"not empty"` for any output, `"exit 0"` for an exit code, or the exact output.  Leave it out for "exits 0" |
| `every` | no | `15m`, `1h`, `1d`.  Default `1h` |
| `timeout` | no | Seconds, 600 at most.  Default 60 |
| `note` | no | A short line shown under it when it passes |

A check against a job that runs once a day needs a day's slack.  If a
cleanup keeps 31 days and runs at midnight, files reach 32 days before the
next run, so check for `+32`, not `+31`.

## Panels (`panels.d`)

| Key | Needed | Meaning |
|-----|--------|---------|
| `title` | yes | The box's heading |
| `source` | yes | A full file path, or an `http://` or `https://` address, giving JSON |
| `link` | no | Makes the heading a link |
| `every` | no | How often to read it.  Default `5m` |
| `[[show]]` | yes | One to twelve figures, each with `label`, `key` and optional `unit` |

`key` is a dotted path into the JSON: `today.kwh`, or `items[0].name`.  The
runner reads the source and sends only those figures to the page.
