"""讓 `python -m harness` 與 `pyharness` 指令有相同的效果。"""

import sys

from harness import cli

sys.exit(cli.main())
