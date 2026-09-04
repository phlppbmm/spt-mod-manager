"""Process entry of the watcher that waits for a running game to close.

Started as ``python -m sptmod.watch <game directory>`` by
:func:`sptmod.core.launch.launch_profile` and lives as long as the game does.
"""

import sys
from collections.abc import Sequence
from pathlib import Path

from sptmod.core.launch import watch_game

USAGE = "usage: python -m sptmod.watch <game directory>"


def main(argv: Sequence[str]) -> int:
    """Watch the game directory named in ``argv``."""
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    watch_game(Path(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
