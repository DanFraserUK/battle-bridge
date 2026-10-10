#!/usr/bin/python3
"""Start bb-runner.  Installed to /usr/local/lib/battle-bridge/, run by systemd."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bbrunner.main import main  # noqa: E402

sys.exit(main())
