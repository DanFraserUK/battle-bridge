"""The PC link handler, as the setup page makes it: three kinds of link,
everything else refused, and nothing from a link ever reaching a command."""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from battle_bridge import pc, store  # noqa: E402

PC = {"terminal": "konsole", "ssh_target": "me@192.168.1.5", "ssh_port": 2222,
      "folders": ["~", "~/compose", "/srv/my media"]}


class TestHandler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for name in [*pc.TERMINAL_LINES, "notify-send"]:
            p = Path(self.tmp.name) / name
            p.write_text(f'#!/bin/sh\nprintf "{name.upper()}:"; printf " [%s]" "$@"; echo\n')
            p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        self.tmp.cleanup()

    def run_link(self, link, conf=PC):
        script = Path(self.tmp.name) / "handler"
        script.write_text(pc.handler(conf))
        env = dict(os.environ, PATH=self.tmp.name + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["bash", str(script), link], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout

    def test_accepted(self):
        for link, want in [
            ("battlebridge://update-system", "[bash /usr/local/lib/battle-bridge/pc-jobs/update-system]"),
            ("battlebridge://reboot/", "[bash /usr/local/lib/battle-bridge/pc-jobs/reboot]"),
            ("battlebridge://terminal/0", '[cd && exec "$SHELL" -l]'),
            ("battlebridge://terminal/1", """[cd -- "$HOME"/'compose' && exec "$SHELL" -l]"""),
            ("battlebridge://terminal/2", """[cd -- '/srv/my media' && exec "$SHELL" -l]"""),
        ]:
            code, out = self.run_link(link)
            self.assertEqual(code, 0, link)
            self.assertTrue(out.startswith("KONSOLE: [--separate]"), out)
            self.assertIn("[ssh] [-t] [-p] [2222] [me@192.168.1.5]", out)
            self.assertTrue(out.rstrip().endswith(want), out)

    def test_refused(self):
        for link in ["battlebridge://rm -rf ~", "battlebridge://update-system;reboot", "battlebridge://../x",
                     "http://evil/update-system", "battlebridge://", "battlebridge://UPDATE-SYSTEM",
                     "battlebridge://update-system$(id)", "", "battlebridge://reboot`id`",
                     "battlebridge://terminal/3", "battlebridge://terminal/01", "battlebridge://terminal/-1",
                     "battlebridge://terminal/1;id", "battlebridge://terminal/", "battlebridge://terminal/999"]:
            code, out = self.run_link(link)
            self.assertEqual(code, 1, link)
            self.assertNotIn("KONSOLE", out, link)
            self.assertNotIn("uid=", out, link)

    def test_every_terminal(self):
        for term in pc.TERMINAL_LINES:
            code, out = self.run_link("battlebridge://reboot", dict(PC, terminal=term))
            self.assertEqual(code, 0, term)
            self.assertTrue(out.startswith(term.upper() + ":"), out)
            self.assertIn("[me@192.168.1.5]", out)

    def test_every_terminal_is_offered(self):
        self.assertEqual(set(pc.TERMINAL_LINES), set(store.TERMINALS))

    def test_installer(self):
        text = pc.installer(PC, "192.168.1.5")
        r = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), PATH=self.tmp.name + os.pathsep + os.environ["PATH"])
        for name in ("xdg-mime", "update-desktop-database"):
            p = Path(self.tmp.name) / name
            p.write_text("#!/bin/sh\nexit 0\n")
            p.chmod(0o755)
        if os.getuid() == 0:
            self.skipTest("the installer refuses root, as it should")
        r = subprocess.run(["bash"], input=text, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        handler = home / ".local/bin/battlebridge-handler"
        # Line 2 holds the time it was made.
        self.assertEqual(handler.read_text().splitlines()[2:], pc.handler(PC).splitlines()[2:])
        desktop = (home / ".local/share/applications/battlebridge-handler.desktop").read_text()
        self.assertIn(f"Exec={home}/.local/bin/battlebridge-handler %u", desktop)


if __name__ == "__main__":
    unittest.main()
